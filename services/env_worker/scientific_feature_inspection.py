"""Bounded data-only native feature cells shared by scientific layer inspectors."""
import re
from .scientific_view_wire import fields, require

KINDS = ('string', 'integer', 'float', 'boolean', 'missing', 'datetime', 'timedelta')
NUMBER = r'(?:-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:e[+-]?[0-9]+)?|nan|inf|-inf)'

def text(value, maximum):
    return type(value) is str and len(value.encode('utf-16-le', errors='surrogatepass')) // 2 <= maximum

def validate_features(features):
    require(type(features) is list and len(features) <= 256)
    names = set()
    for feature in features:
        fields(feature, 'name dtype kind value')
        require(text(feature['name'], 256) and feature['name'] not in names)
        names.add(feature['name'])
        require(text(feature['dtype'], 128) and feature['kind'] in KINDS)
        content = feature['value']
        require(content is None if feature['kind'] == 'missing' else text(content, 4096))
        if feature['kind'] == 'integer': require(re.fullmatch(r'-?[0-9]+', content) is not None)
        if feature['kind'] == 'float': require(re.fullmatch(NUMBER, content) is not None)
        if feature['kind'] == 'boolean': require(content in ('True', 'False'))
    return features

def feature_value(value):
    import numpy as np
    import pandas as pd
    if value is None or value is pd.NA or value is pd.NaT:
        return 'missing', None
    if type(value) in (str, np.str_):
        if not text(str(value), 4096): raise ValueError('This feature text exceeds the inspection allowance; use its retained table')
        return 'string', str(value)
    if type(value) in (bool, np.bool_): return 'boolean', str(bool(value))
    if type(value) is int or type(value).__module__ == 'numpy' and isinstance(value, np.integer):
        return 'integer', str(int(value))
    if type(value) is float or type(value).__module__ == 'numpy' and isinstance(value, np.floating):
        # Do not coerce extended-precision scalars through a Python float.
        return 'float', str(value)
    if type(value) is pd.Timestamp: return 'datetime', value.isoformat()
    if type(value) is pd.Timedelta: return 'timedelta', value.isoformat()
    if type(value) in (np.datetime64, np.timedelta64):
        if np.isnat(value): return 'missing', None
        return ('datetime' if type(value) is np.datetime64 else 'timedelta'), str(value)
    raise ValueError('This feature type needs a data-only inspection integration; the source is unchanged')


def dtype_label(dtype):
    import numpy as np
    import pandas as pd
    if isinstance(dtype, np.dtype): return str(dtype)
    for name in ('Int8', 'Int16', 'Int32', 'Int64', 'UInt8', 'UInt16', 'UInt32', 'UInt64', 'Float32', 'Float64', 'Boolean'):
        if type(dtype) is getattr(pd, name+'Dtype'): return 'boolean' if name=='Boolean' else name
    if type(dtype) is pd.CategoricalDtype: return 'category'
    if type(dtype) is pd.StringDtype and dtype.storage in ('python','pyarrow'): return 'string['+dtype.storage+']'
    if type(dtype) is pd.DatetimeTZDtype and dtype.unit in ('s','ms','us','ns'): return 'datetime64['+dtype.unit+', timezone-aware]'
    raise ValueError('This feature dtype needs a data-only inspection integration; the source is unchanged')



def feature_rows(layer, index):
    if not layer.features.columns.is_unique or len(layer.features.columns) > 256 or len(layer.features) != len(layer.data):
        raise ValueError('Feature rows must match their native geometry within the inspection allowance')
    features = []
    for name in layer.features.columns:
        if not text(name, 256): raise ValueError('Features need bounded text column names')
        column = layer.features[name]
        # Read typed columns separately; a mixed row Series can round uint64.
        dtype = dtype_label(column.dtype)
        kind, content = feature_value(column.iloc[index])
        features.append({'name':name, 'dtype':dtype, 'kind':kind, 'value':content})
    return validate_features(features)
