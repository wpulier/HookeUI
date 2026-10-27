"""Observe bounded reader data through the private host, never a qualification claim."""
import math

from gpu_worker.reader_proof_contract import FORMAT, TYPED_FORMAT, MAX_DATA_BYTES, MAX_LAYERS, MAX_LEVELS
from .native_regions import layout
from .scientific_layer_exports import geometry, quiescent
from .scientific_output_coordinates import fingerprint


def observe(host):
    import numpy as np
    layers=list(host.viewer.layers)
    if not 1 <= len(layers) <= MAX_LAYERS:
        raise ValueError('A reader data check requires 1 to 16 declared scalar image or Labels layers')
    pending=[]; total=0
    for layer in layers:
        key=host.view_bindings.key_for(layer)
        kind=layer._type_string
        if key is None or kind not in ('image','labels') or getattr(layer,'rgb',False):
            raise ValueError('Reader data checks support explicitly bound scalar image or Labels layers only')
        if kind=='labels' and layer.multiscale:
            raise ValueError('Reader Labels require a single native level')
        data=layer.data
        if layer.multiscale and not 1 <= len(data) <= MAX_LEVELS:
            raise ValueError('Reader data checks support 1 to 16 levels per image')
        levels=list(data) if layer.multiscale else [data]
        if not 1 <= len(levels) <= MAX_LEVELS:
            raise ValueError('Reader data checks support 1 to 16 levels per image')
        checked=[]
        for value in levels:
            shape,dtype,axes=layout(tuple(value.shape),value.dtype.str,tuple(layer.axis_labels))
            if kind=='labels' and value.dtype.kind not in 'iu':
                raise ValueError('Reader Labels require integer object IDs')
            if len(shape)<2 or any(n<=0 for n in shape):
                raise ValueError('Reader data checks require nonempty image levels')
            total+=math.prod(shape)*value.dtype.itemsize
            if total>MAX_DATA_BYTES:
                raise ValueError('Reader data checks exceed the aggregate 64 MiB decoded allowance')
            checked.append((value,shape,dtype))
        pending.append((layer,data,key,kind,geometry(layer),checked))
    typed=any(item[3]=='labels' for item in pending)
    result=[]
    quiescent(np.empty(0,dtype='uint8'))
    for layer,data,key,kind,before,levels in pending:
        rows=[]
        for value,shape,dtype in levels:
            def read(region):
                part=np.asarray(value[region])
                if kind=='labels' and np.any(part<0):
                    raise ValueError('Reader Labels require nonnegative object IDs')
                quiescent(part)
                return part
            rows.append({'shape':list(shape),'dtype':dtype,'sha256':fingerprint(shape,dtype,read)})
        if layer.data is not data or layer._type_string!=kind or geometry(layer)!=before:
            raise ValueError('Reader layers changed during observation')
        basis,axes,units=before
        result.append({'key':key,'axes':list(axes),'units':list(units),
                       'basis':[list(row) for row in basis],'levels':rows,**({'type':kind} if typed else {})})
    quiescent(np.empty(0,dtype='uint8'))
    if len(host.viewer.layers)!=len(layers) or any(a is not b for a,b in zip(host.viewer.layers,layers)):
        raise ValueError('Reader layer ownership changed during observation')
    from gpu_worker.reader_proof_contract import observation
    return observation({'format':TYPED_FORMAT if typed else FORMAT,'layers':result})
