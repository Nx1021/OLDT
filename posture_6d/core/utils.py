import os
import numpy as np
from _ctypes import PyObj_FromPtr
import json
from json import JSONDecodeError
import re
from typing import Any
import time
import io
import re
import warnings

def get_bbox_connections(bbox_3d_proj:np.ndarray):
    '''
    bbox_3d_proj: [..., B, (x,y)]
    return
    -----
    lines: [..., ((x1,x2), (y1,y2)), 12]
    '''
    b = bbox_3d_proj
    lines = [
    ([b[...,0,0], b[...,1,0]], [b[...,0,1], b[...,1,1]]),
    ([b[...,0,0], b[...,6,0]], [b[...,0,1], b[...,6,1]]),
    ([b[...,6,0], b[...,7,0]], [b[...,6,1], b[...,7,1]]),
    ([b[...,1,0], b[...,7,0]], [b[...,1,1], b[...,7,1]]),

    ([b[...,2,0], b[...,3,0]], [b[...,2,1], b[...,3,1]]),
    ([b[...,2,0], b[...,4,0]], [b[...,2,1], b[...,4,1]]),
    ([b[...,4,0], b[...,5,0]], [b[...,4,1], b[...,5,1]]),
    ([b[...,3,0], b[...,5,0]], [b[...,3,1], b[...,5,1]]),

    ([b[...,0,0], b[...,2,0]], [b[...,0,1], b[...,2,1]]),
    ([b[...,1,0], b[...,3,0]], [b[...,1,1], b[...,3,1]]),
    ([b[...,7,0], b[...,5,0]], [b[...,7,1], b[...,5,1]]),
    ([b[...,6,0], b[...,4,0]], [b[...,6,1], b[...,4,1]]),
    ]
    lines = np.stack(lines)
    return lines #[12, ..., ((x1,x2), (y1,y2))]

def modify_class_id(dict_list:list[dict[int, Any]], modify_class_id_pairs:list[tuple[int]]):
    orig_keys = [x[0] for x in modify_class_id_pairs]
    new_keys  = [x[1] for x in modify_class_id_pairs]
    assert len(orig_keys) == len(set(orig_keys))
    assert len(new_keys)  == len(set(new_keys))
    assert all([len(x) == 2 for x in modify_class_id_pairs])
    for orig_dict in dict_list:
        new_dict = {}
        for pair in modify_class_id_pairs:
            if pair[0] in orig_dict:
                new_dict[pair[1]] = orig_dict[pair[0]]
        orig_dict.clear()
        orig_dict.update(new_dict)    

def get_meta_dict(obj):
    orig_dict_list = []
    for name, orig_dict in vars(obj).items():
        if isinstance(orig_dict, dict) and all([isinstance(x, int) for x in orig_dict.keys()]):
            orig_dict_list.append(orig_dict)
    return orig_dict_list

def extract_doc(doc:str, title:str):
    idx = doc.find(title)
    sub_doc = doc[idx:]
    idx = re.search(r'\n\s*?\n', sub_doc).start()
    sub_doc = sub_doc[:idx]
    return sub_doc


def _ignore_warning(func, category = Warning):
    def warpper(*args, **kwargs):
        warnings.filterwarnings("ignore", category=category) # do not show warning of image size
        rlt = func(*args, **kwargs)
        warnings.filterwarnings("default", category=category) # recover warning of image size
        return rlt
    return warpper


class JsonIO():
    class _NoIndent(object):
        """ Value wrapper. """
        def __init__(self, value):
            self.value = value

    class _MyEncoder(json.JSONEncoder):
        FORMAT_SPEC = '@@{}@@'
        regex = re.compile(FORMAT_SPEC.format(r'(\d+)'))

        def __init__(self, **kwargs):
            # Save copy of any keyword argument values needed for use here.
            self.__sort_keys = kwargs.get('sort_keys', None)
            super(JsonIO._MyEncoder, self).__init__(**kwargs)

        def default(self, obj):
            return (self.FORMAT_SPEC.format(id(obj)) if isinstance(obj, JsonIO._NoIndent)
                    else super(JsonIO._MyEncoder, self).default(obj))

        def encode(self, obj):
            format_spec = self.FORMAT_SPEC  # Local var to expedite access.
            json_repr = super(JsonIO._MyEncoder, self).encode(obj)  # Default JSON.

            # Replace any marked-up object ids in the JSON repr with the
            # value returned from the json.dumps() of the corresponding
            # wrapped Python object.
            for match in self.regex.finditer(json_repr):
                # see https://stackoverflow.com/a/15012814/355230
                id = int(match.group(1))
                no_indent = PyObj_FromPtr(id)
                json_obj_repr = json.dumps(no_indent.value, sort_keys=self.__sort_keys)

                # Replace the matched id string with json formatted representation
                # of the corresponding Python object.
                json_repr = json_repr.replace(
                                '"{}"'.format(format_spec.format(id)), json_obj_repr)

            return json_repr

    class Stream():
        def __init__(self, path, open = False, buffer_length = 100000) -> None:
            self.path = path
            self.buffer = ""
            self.buffer_length = buffer_length
            self._closed = True
            if open:
                self.open()

        @property
        def closed(self):
            return self._closed
        
        @closed.setter
        def closed(self, value):
            value = bool(value)
            if value == True:
                self.close()
            else:
                self.open()
            self._closed = value

        def open(self):
            # 
            if not self.closed:
                return
            print("open JsonIO stream of {}".format(self.path))
            if os.path.exists(self.path):
                try:
                    with open(self.path, 'rb+') as f:
                        f.seek(-3, 2)
                        f.truncate()
                    with open(self.path, 'a') as f:
                        f.write(",")
                except OSError:
                    pass
            else:
                with open(self.path, 'w') as f:
                    f.write("{")   
            self._closed = False         

        def close(self):
            if self.closed:
                return
            print("close JsonIO stream of {}".format(self.path))
            self.save_buffer()
            with open(self.path, 'rb+') as f:
                f.seek(-1, 2)
                f.truncate()
            with open(self.path, 'a') as f:
                f.write('\n}')
            self._closed = True

        def save_buffer(self):
            with open(self.path, 'a') as f:
                f.write(self.buffer)
            self.buffer = ""            

        def write(self, to_dump_dict):
            string = JsonIO._dumps(to_dump_dict)
            self.buffer += string
            if len(self.buffer) > self.buffer_length:
                self.save_buffer()

        def __del__(self):
            self.close()

    @staticmethod
    def create_stream(path):
        stream = JsonIO.Stream(path)
        return stream

    @staticmethod
    def __convert_formatdict_from_json(dictionary):
        def cvt_key(key):
            if isinstance(key, str):
                try:
                    key = int(key)
                except ValueError:
                    pass
            return key

        def cvt_value(value):
            if isinstance(value, list):
                try:
                    array = np.array(value)
                    if np.issubdtype(array.dtype, np.number):
                        new_value = array
                    else:
                        raise ValueError
                except ValueError:
                    new_value = []
                    for item in value:
                        if isinstance(item, dict):
                            item = JsonIO.__convert_formatdict_from_json(item)
                        else:
                            item = cvt_value(item)
                        new_value.append(item)
            elif isinstance(value, dict):
                new_value = JsonIO.__convert_formatdict_from_json(value)
            else:
                new_value = value
            return new_value

        new_dict = {}
        for key, value in dictionary.items():
            new_key = cvt_key(key)
            new_value = cvt_value(value)

            new_dict[new_key] = new_value
        return new_dict

    @staticmethod
    def __convert_dict_to_jsonformat(dictionary):
        def cvt_key(key):
            if isinstance(key, str):
                try:
                    key = int(key)
                except ValueError:
                    pass
            if isinstance(key, np.intc):
                key = int(key)
            return key

        def cvt_value(value):
            if isinstance(value, np.intc):
                new_value = int(value)
            if isinstance(value, np.ndarray):
                new_value = np.around(value, decimals=4).tolist()
                new_value = JsonIO._NoIndent(new_value)
            elif isinstance(value, list):
                new_value = []
                for item in value:
                    if isinstance(item, dict):
                        item = JsonIO.__convert_dict_to_jsonformat(item)
                    else:
                        item = cvt_value(item)
                    new_value.append(item)
            elif isinstance(value, dict):
                new_value = JsonIO.__convert_dict_to_jsonformat(value)
                if not any([isinstance(x, JsonIO._NoIndent) for x in new_value.values()]) and\
                list(new_value.values()) == list(value.values()):
                    new_value = JsonIO._NoIndent(new_value)
            else:
                new_value = value
            return new_value

        new_dict = {}
        for key, value in dictionary.items():
            new_key = cvt_key(key)
            new_value = cvt_value(value)
            new_dict[new_key] = new_value
        return new_dict

    @staticmethod
    def load_json(path, format = True):
        with open(path, 'r') as jf:
            dict_ = json.load(jf)
        if format:
            dict_ = JsonIO.__convert_formatdict_from_json(dict_)
        return dict_

    @staticmethod
    def _dumps(to_dump_dict):
        to_dump_dict = JsonIO.__convert_dict_to_jsonformat(to_dump_dict)
        string = ""            
        for k, v in to_dump_dict.items():
            json_data = json.dumps({k: v}, cls=JsonIO._MyEncoder, ensure_ascii=False, sort_keys=True, indent=2)
            string += json_data[1:-2] + ','
        return string

    @staticmethod
    def dump_json(path, to_dump_dict):
        string = JsonIO._dumps(to_dump_dict)
        string = '{' + string[:-1] + '\n}'
        with open(path, 'w') as fw:
            fw.write(string)

