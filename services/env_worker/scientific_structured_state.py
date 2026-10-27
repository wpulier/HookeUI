"""Bounded, data-only layer state. No pickle, object hooks or importable class names.

This small-state encoding preserves exact numeric bytes and typed feature columns.
Bulk structured resources still need the regional component transport; exceeding
this allowance is a refusal, never truncation or a scientific type conversion.
"""
import base64
import json
import math
import re
import sys

FORMAT = 'scientific-layer-state-v1'
MAX_BYTES = 96 << 10
MAX_ARRAY_BYTES = 48 << 10
MAX_NODES = 4096
MAX_DEPTH = 24
NULLABLE = frozenset(['boolean', 'string', 'Int8', 'Int16', 'Int32', 'Int64',
                      'UInt8', 'UInt16', 'UInt32', 'UInt64', 'Float32', 'Float64'])


def _fail():
    raise ValueError('This layer state needs a supported data-only writer within its bounded allowance; nothing was discarded')


def _dtype(value):
    import numpy as np
    if type(value) is not str or len(value) > 32:
        _fail()
    try: dtype = np.dtype(value)
    except (TypeError, ValueError): _fail()
    if dtype.str != value or dtype.fields or dtype.subdtype or dtype.kind not in 'biufcSU':
        _fail()
    if not 0 < dtype.itemsize <= MAX_ARRAY_BYTES:
        _fail()
    return dtype


class _Budget:
    def __init__(self):
        self.nodes = self.bytes = self.characters = 0

    def visit(self, depth):
        self.nodes += 1
        if depth > MAX_DEPTH or self.nodes > MAX_NODES:
            _fail()

    def text(self, value):
        self.characters += len(value)
        if self.characters > MAX_BYTES: _fail()

    def array(self, count):
        self.bytes += count
        if self.bytes > MAX_ARRAY_BYTES:
            _fail()


def pack(value):
    """Called only in the owning scientific worker; do not execute custom serializers."""
    import numpy as np
    pd = sys.modules.get('pandas')
    budget = _Budget()

    def encode(item, depth=0):
        budget.visit(depth)
        def child(value): return encode(value, depth+1)
        if item is None or type(item) in (str, bool):
            if type(item) is str: budget.text(item)
            return {'type': 'scalar', 'value': item}
        if type(item) is int:
            return {'type': 'integer', 'value': str(item)} if abs(item) >= 2**53 else {'type': 'scalar', 'value': item}
        if type(item) is float and math.isfinite(item):
            return {'type': 'scalar', 'value': item}
        if type(item) is float:
            return {'type': 'float', 'value': item.hex()}
        if type(item) in (list, tuple):
            if len(item) > MAX_NODES: _fail()
            return {'type': 'tuple' if type(item) is tuple else 'list', 'value': [child(v) for v in item]}
        if type(item) is dict:
            if len(item) > MAX_NODES or any(type(k) is not str for k in item): _fail()
            for key in item: budget.text(key)
            return {'type': 'dict', 'value': {k: child(v) for k,v in item.items()}}
        if type(item) is np.ndarray:
            if item.dtype.kind == 'O':
                if item.size > MAX_NODES: _fail()
                return {'type': 'objects', 'shape': list(item.shape), 'value': [child(v) for v in item.flat]}
            dtype = _dtype(item.dtype.str)
            budget.array(item.nbytes)
            return {'type': 'array', 'shape': list(item.shape), 'dtype': dtype.str,
                    'value': base64.b64encode(item.tobytes(order='C')).decode('ascii')}
        if type(item).__module__ == 'numpy' and issubclass(type(item),np.generic):
            return {'type': 'numpy-scalar', 'value': child(np.asarray(item))}
        if pd is not None:
            if type(item) is pd.RangeIndex:
                return {'type':'range','value':[item.start,item.stop,item.step],'name':child(item.name)}
            if type(item) is pd.Index:
                if len(item)>MAX_NODES: _fail()
                data=item.to_numpy() if isinstance(item.dtype,np.dtype) else item.array
                return {'type':'index','value':child(data),'name':child(item.name)}
            if type(item) is pd.Categorical:
                if len(item)>MAX_NODES or len(item.categories)>MAX_NODES: _fail()
                return {'type':'category','codes':child(item.codes),'categories':child(item.categories),'ordered':item.ordered}
            extension_types=[getattr(pd.arrays,name,None) for name in ('StringArray','ArrowStringArray','IntegerArray','FloatingArray','BooleanArray')]
            if any(type(item) is native for native in extension_types):
                if len(item)+budget.nodes>MAX_NODES: _fail()
                if type(item.dtype) is pd.StringDtype:
                    if item.dtype.storage not in ('python','pyarrow'): _fail()
                    return {'type':'pandas-string','storage':item.dtype.storage,
                            'missing':'NA' if item.dtype.na_value is pd.NA else 'nan',
                            'value':child([None if pd.isna(v) else v for v in item])}
                if str(item.dtype) not in NULLABLE: _fail()
                return {'type':'nullable','dtype':str(item.dtype),
                        'data':child(item._data),'mask':child(item._mask)}
            if type(item) is pd.DataFrame:
                if len(item)>MAX_NODES or len(item.columns)>256: _fail()
                if not item.columns.is_unique or any(type(k) is not str for k in item.columns): _fail()
                columns=[]
                for name in item.columns:
                    series=item[name]
                    data=series.to_numpy() if isinstance(series.dtype,np.dtype) else series.array
                    columns.append({'name':name,'values':child(data)})
                return {'type':'table','columns':columns,'index':child(item.index),'columnIndex':child(item.columns)}
        _fail()
    wire={'format':FORMAT,'state':encode(value)}
    validate(wire)
    return wire


def validate(value):
    """Parent/worker admission. Validate types and byte budgets without constructing tables."""
    if type(value) is not dict or set(value) != {'format','state'} or value['format'] != FORMAT: _fail()
    budget=_Budget()

    def check(node, depth=0):
        budget.visit(depth)
        if type(node) is not dict or type(node.get('type')) is not str: _fail()
        kind=node['type']
        for k,v in node.items():
            budget.text(k)
            if type(v) is str: budget.text(v)
        def child(v): return check(v,depth+1)
        def fields(*keys):
            if set(node) != {'type',*keys}: _fail()
        if kind in ('scalar','integer','float'):
            fields('value'); item=node['value']
            if kind=='scalar':
                if item is not None and type(item) not in (str,bool,int,float): _fail()
                if type(item) is int and abs(item)>=2**53: _fail()
                if type(item) is float and not math.isfinite(item): _fail()
            elif kind=='integer':
                if type(item) is not str or len(item)>128 or re.fullmatch(r'-?(0|[1-9][0-9]*)',item) is None: _fail()
            elif item not in ('nan','inf','-inf'): _fail()
        elif kind in ('list','tuple','dict'):
            fields('value'); item=node['value']
            if type(item) is not (dict if kind=='dict' else list) or len(item)>MAX_NODES: _fail()
            if kind=='dict':
                if any(type(k) is not str for k in item): _fail()
                for key in item: budget.text(key)
            for value in item.values() if kind=='dict' else item: child(value)
        elif kind in ('array','objects'):
            fields('shape','value',*(['dtype'] if kind=='array' else []))
            shape=node['shape']
            if type(shape) is not list or len(shape)>8 or any(type(n) is not int or not 0<=n<2**53 for n in shape): _fail()
            count=math.prod(shape)
            if kind=='array':
                dtype=_dtype(node['dtype']); size=count*dtype.itemsize; budget.array(size)
                body=node['value']
                if type(body) is not str or len(body)!=4*((size+2)//3): _fail()
                try: decoded=base64.b64decode(body,validate=True)
                except (ValueError,TypeError): _fail()
                if len(decoded)!=size or base64.b64encode(decoded).decode('ascii')!=body: _fail()
            else:
                if type(node['value']) is not list or len(node['value'])!=count or count>MAX_NODES: _fail()
                for item in node['value']: child(item)
        elif kind=='numpy-scalar':
            fields('value'); child(node['value'])
            if node['value']['type']!='array' or node['value']['shape']!=[]: _fail()
        elif kind in ('range','index'):
            fields('value','name'); child(node['name'])
            if kind=='range':
                v=node['value']
                if type(v) is not list or len(v)!=3 or any(type(n) is not int or abs(n)>=2**53 for n in v) or not v[2]: _fail()
                if len(range(*v))>MAX_NODES: _fail()
            else:
                child(node['value'])
                if node['value']['type'] not in ('array','objects','nullable','pandas-string'): _fail()
                length(node['value'])
        elif kind=='category':
            fields('codes','categories','ordered');child(node['codes']);child(node['categories'])
            if type(node['ordered']) is not bool or node['codes']['type']!='array' or len(node['codes']['shape'])!=1: _fail()
            if _dtype(node['codes']['dtype']).kind!='i': _fail()
            if node['categories']['type']!='index': _fail()
            import numpy as np
            codes=np.frombuffer(base64.b64decode(node['codes']['value']),dtype=node['codes']['dtype'])
            if np.any(codes < -1) or np.any(codes >= length(node['categories'])): _fail()
        elif kind=='nullable':
            fields('dtype','data','mask')
            if type(node['dtype']) is not str or node['dtype'] not in NULLABLE-{'string'}: _fail()
            child(node['data']);child(node['mask'])
            data,mask=node['data'],node['mask']
            if data['type']!='array' or mask['type']!='array' or len(data['shape'])!=1 or data['shape']!=mask['shape'] or mask['dtype']!='|b1': _fail()
            expected='bool' if node['dtype']=='boolean' else node['dtype'].lower()
            if _dtype(data['dtype']).name!=expected: _fail()
        elif kind=='pandas-string':
            fields('storage','missing','value')
            if node['storage'] not in ('python','pyarrow') or node['missing'] not in ('NA','nan'): _fail()
            child(node['value'])
            if node['value']['type']!='list' or any(v['type']!='scalar' or v['value'] is not None and type(v['value']) is not str for v in node['value']['value']): _fail()
        elif kind=='table':
            fields('columns','index','columnIndex');child(node['index']);child(node['columnIndex'])
            if node['index']['type'] not in ('range','index'): _fail()
            cols=node['columns']
            if type(cols) is not list or len(cols)>256: _fail()
            if node['columnIndex']['type'] not in ('range','index') or length(node['columnIndex'])!=len(cols): _fail()
            names=set()
            for row in cols:
                if type(row) is not dict or set(row)!={'name','values'} or type(row['name']) is not str or row['name'] in names: _fail()
                budget.text(row['name']);names.add(row['name']);child(row['values'])
                if row['values']['type'] not in ('array','objects','category','nullable','pandas-string'): _fail()
                if length(row['values']) != length(node['index']): _fail()
        else: _fail()
    check(value['state'])
    try: body=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
    except (TypeError,ValueError,UnicodeError,RecursionError): _fail()
    if len(body)>MAX_BYTES: _fail()
    return value


def length(node):
    kind=node['type']
    if kind=='range': return len(range(*node['value']))
    if kind=='index': return length(node['value'])
    if kind=='category': return length(node['codes'])
    if kind=='nullable': return length(node['data'])
    if kind=='pandas-string': return len(node['value']['value'])
    if kind in ('array','objects') and len(node['shape'])==1: return node['shape'][0]
    _fail()


def unpack(value):
    """Construct only explicitly supported scientific data types in the owning worker."""
    validate(value)
    import numpy as np
    def decode(node):
        kind=node['type']; item=node.get('value')
        if kind=='scalar': return item
        if kind=='integer': return int(item)
        if kind=='float': return float.fromhex(item)
        if kind=='list': return [decode(v) for v in item]
        if kind=='tuple': return tuple(decode(v) for v in item)
        if kind=='dict': return {k:decode(v) for k,v in item.items()}
        if kind=='array': return np.frombuffer(base64.b64decode(item),dtype=node['dtype']).reshape(node['shape']).copy()
        if kind=='objects':
            result=np.empty(math.prod(node['shape']),dtype=object)
            for i,v in enumerate(item): result[i]=decode(v)
            return result.reshape(node['shape'])
        if kind=='numpy-scalar': return decode(item)[()]
        import pandas as pd
        if kind=='range': return pd.RangeIndex(*item,name=decode(node['name']))
        if kind=='index':
            data=decode(item)
            return pd.Index(data,dtype=data.dtype,name=decode(node['name']))
        if kind=='category': return pd.Categorical.from_codes(decode(node['codes']),decode(node['categories']),ordered=node['ordered'])
        if kind=='nullable':
            data,mask=decode(node['data']),decode(node['mask'])
            constructor=pd.arrays.BooleanArray if node['dtype']=='boolean' else pd.arrays.FloatingArray if node['dtype'].startswith('Float') else pd.arrays.IntegerArray
            return constructor(data,mask)
        if kind=='pandas-string': return pd.array(decode(item),dtype=pd.StringDtype(storage=node['storage'],na_value=pd.NA if node['missing']=='NA' else np.nan))
        if kind=='table':
            index=decode(node['index']);columns=decode(node['columnIndex'])
            if list(columns)!=[row['name'] for row in node['columns']]: _fail()
            values={}
            for row in node['columns']:
                data=decode(row['values'])
                values[row['name']]=pd.Series(data,index=index,dtype=data.dtype)
            table=pd.DataFrame(values,index=index)
            table.columns=columns;return table
        _fail()
    return decode(value['state'])
