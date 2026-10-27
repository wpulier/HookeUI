"""Versioned bounded directory-reader data checks, separate from workspace qualification."""
import copy
import re
from pathlib import PurePosixPath

FORMAT = 'reader-data-contract-v1'
TYPED_FORMAT = 'reader-data-contract-v2'
MAX_CASES = 8
MAX_FILES = 32
MAX_INPUT_BYTES = 8 << 20
MAX_DATA_BYTES = 64 << 20
MAX_LAYERS = 16
MAX_LEVELS = 16
SHA = re.compile('[0-9a-f]{64}')


def filename(value, prefix):
    return (type(value) is str and 0 < len(value) <= 240 and PurePosixPath(value).name == value
            and '\\' not in value and '\x00' not in value and value.startswith(prefix))


def declaration(value, member):
    if (member.bridge != 'scientific-host-v1' or member.reader.get('format') != 3
            or member.reader.get('kind') != 'directory' or type(value) is not dict
            or set(value) != {'format','cases'} or type(value['format']) is not int or value['format'] != 1
            or type(value['cases']) is not list or not 1 <= len(value['cases']) <= MAX_CASES):
        raise ValueError('Reader checks require version 1 bounded directory cases on scientific-host-v1')
    identifiers=set()
    for case in value['cases']:
        if (type(case) is not dict or set(case) != {'id','files','reference','source'}
                or type(case['id']) is not str or re.fullmatch('[a-z][a-z0-9-]{0,39}',case['id']) is None
                or case['id'] in identifiers or type(case['source']) is not str or not 0 < len(case['source']) <= 1000
                or not filename(case['reference'],'reference') or not case['reference'].endswith('.json')
                or type(case['files']) is not dict or not 1 <= len(case['files']) <= MAX_FILES):
            raise ValueError('Each reader check needs a unique id, bounded example files, reference JSON and source attribution')
        identifiers.add(case['id'])
        for name, path in case['files'].items():
            if (type(name) is not str or not 0 < len(name) <= 240 or '\\' in name or '\x00' in name
                    or PurePosixPath(name).is_absolute() or any(p in ('','.','..') for p in name.split('/'))
                    or not filename(path,'example')):
                raise ValueError('Reader check files must stay inside their declared folder and prepared examples')
        names=set(case['files'])
        if any('/'.join(name.split('/')[:n]) in names for name in names for n in range(1,len(name.split('/')))):
            raise ValueError('Reader check file names cannot alias parent directories')
    return copy.deepcopy(value)


def observation(value):
    """Validate the finite exact observation shared by parent and confined observer."""
    import math
    import numpy as np
    if type(value) is not dict or set(value)!={'format','layers'} or value['format'] not in (FORMAT,TYPED_FORMAT) \
            or type(value['layers']) is not list or not 1<=len(value['layers'])<=MAX_LAYERS:
        raise ValueError('Invalid reader data observation')
    seen=set();total=0
    for layer in value['layers']:
        fields={'key','axes','units','basis','levels'} | ({'type'} if value['format']==TYPED_FORMAT else set())
        if type(layer) is not dict or set(layer)!=fields:
            raise ValueError('Reader layers require exact identity, geometry and levels')
        kind=layer.get('type','image')
        if kind not in ('image','labels'):raise ValueError('Reader layer type must be image or labels')
        if kind=='labels' and (type(layer['levels']) is not list or len(layer['levels'])!=1):
            raise ValueError('Reader Labels require one native level')
        key=layer['key'];axes=layer['axes'];units=layer['units'];basis=layer['basis'];levels=layer['levels']
        if type(key) is not str or re.fullmatch('[A-Za-z][A-Za-z0-9_.-]{0,95}',key) is None or key in seen:
            raise ValueError('Reader layer keys must be unique bounded identities')
        seen.add(key)
        if type(axes) is not list or not 2<=len(axes)<=5 or any(type(a) is not str or re.fullmatch('[a-z][a-z0-9_]{0,15}',a) is None for a in axes) or len(set(axes))!=len(axes):
            raise ValueError('Reader axes must be ordered distinct names')
        if type(units) is not list or len(units)!=len(axes) or any(type(u) is not str or len(u)>100 for u in units):
            raise ValueError('Reader units must correspond to every axis')
        if type(basis) is not list or len(basis)!=len(axes)+1 or any(type(row) is not list or len(row)!=len(axes) or any(type(n) not in (int,float) or not math.isfinite(n) for n in row) for row in basis):
            raise ValueError('Reader calibration must be a finite affine basis')
        if type(levels) is not list or not 1<=len(levels)<=MAX_LEVELS:raise ValueError('Reader levels exceed their allowance')
        for level in levels:
            if type(level) is not dict or set(level)!={'shape','dtype','sha256'}:raise ValueError('Invalid reader level')
            shape=level['shape'];dtype=level['dtype'];digest=level['sha256']
            if type(shape) is not list or len(shape)!=len(axes) or any(type(n) is not int or not 0<n<=2**31-1 for n in shape):raise ValueError('Invalid reader shape')
            if type(dtype) is not str or len(dtype)>10:raise ValueError('Invalid reader dtype')
            parsed=np.dtype(dtype)
            if parsed.str!=dtype or parsed.kind not in 'buif' or parsed.itemsize>8:raise ValueError('Unsupported reader dtype')
            if kind=='labels' and parsed.kind not in 'ui':raise ValueError('Reader Labels require integer dtype')
            if type(digest) is not str or SHA.fullmatch(digest) is None:raise ValueError('Invalid reader value digest')
            total+=math.prod(shape)*parsed.itemsize
            if total>MAX_DATA_BYTES:raise ValueError('Reader data exceeds the aggregate 64 MiB allowance')
    return value
