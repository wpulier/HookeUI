"""Data-only typed state regressions; no completed Points journey claim."""
import copy
import numpy as np
import pytest
from env_worker import scientific_structured_state as state


def features_round_trip():
    import json
    import pandas as pd
    table=pd.DataFrame({'object':np.array([2**63+7,2**63+8],dtype='uint64'),
        'score':np.array([np.nan,1.25],dtype='float32'),
        'class':pd.Categorical(['B',None],categories=['B','A'],ordered=True),
        'accepted':pd.array([True,None],dtype='boolean'),
        'optional':pd.array([None,2**62],dtype='Int64'),
        'name':pd.array(['alpha',None],dtype=pd.StringDtype(storage='python'))},index=pd.Index([41,39],name='object-row'))
    nullable_data=np.array([np.nan,123],dtype='float32')
    nullable_data.view('uint32')[0]=0x7fc00007
    table['masked']=pd.arrays.FloatingArray(nullable_data,np.array([False,True]))
    table['literal']=pd.Series(['A','B'],index=table.index,dtype=object)
    table.index=pd.Index([41,39],dtype=object,name='object-row')
    table.columns=pd.Index(list(table.columns),dtype=object,name='feature')
    actual=state.unpack(json.loads(json.dumps(state.pack(table))))
    pd.testing.assert_frame_equal(actual,table)
    assert actual['object'].to_numpy().tobytes()==table['object'].to_numpy().tobytes()
    assert actual['score'].to_numpy().tobytes()==table['score'].to_numpy().tobytes()
    assert actual['masked'].array._data.tobytes()==table['masked'].array._data.tobytes()
    assert actual['masked'].array._mask.tobytes()==table['masked'].array._mask.tobytes()


@pytest.mark.parametrize('dtype',['>f8','<u8','>c16','<U8','|S8','|b1'])
def test_arrays_keep_byte_order_bits_shape_and_type(dtype):
    values=np.array([1,0],dtype=dtype)
    if np.dtype(dtype).kind=='u': values[0]=2**63+17
    if np.dtype(dtype).kind=='f': values.view('>u8')[0]=0x7ff8000000000007
    returned=state.unpack(state.pack({'data':values,'shape':(1,2)}))
    assert returned['data'].dtype==values.dtype and returned['data'].tobytes()==values.tobytes()
    assert returned['shape']==(1,2)


def empty_tables():
    import pandas as pd
    for frame in [pd.DataFrame(),pd.DataFrame({'name':np.array([],dtype=object)}),
                  pd.DataFrame({'name':['A',None],'value':[float('inf'),float('nan')]})]:
        pd.testing.assert_frame_equal(state.unpack(state.pack(frame)),frame)


def test_objects_are_explicit_data():
    objects=np.empty(3,dtype=object);objects[:]=['A',None,2**64+8]
    actual=state.unpack(state.pack(objects))
    assert actual.dtype==object and list(actual)==list(objects)


def test_unsupported_objects_cannot_execute_serialization_or_reduce_hooks():
    class Executable:
        @property
        def __class__(self): pytest.fail('No class hooks')
        def __iter__(self): pytest.fail('No iterator hooks')
        @property
        def dtype(self): pytest.fail('No dtype hooks')
        def __reduce__(self): pytest.fail('No pickle hooks')
        def __repr__(self): pytest.fail('No coercion to text')
        def to_dict(self): pytest.fail('No arbitrary serializer')
    for value in [Executable(),{'metadata':Executable()}]:
        with pytest.raises(ValueError,match='nothing was discarded'): state.pack(value)


@pytest.mark.parametrize('value',[{'type':'pickle','value':'...'},
    {'type':'array','shape':[2**52,2**52],'dtype':'<f8','value':''},
    {'type':'array','shape':[True],'dtype':'<f8','value':''},
    {'type':'array','shape':[1],'dtype':'|O','value':'AAAAAAAAAAA='},
    {'type':'scalar','value':float('nan')}, {'type':'scalar','value':2**64},
    {'type':'integer','value':'1e100'}, {'type':'float','value':'0x1.0p+0'},
    {'type':'numpy-scalar','value':{'type':'list','value':[]}},
    {'type':'range','value':[0,2**40,1],'name':{'type':'scalar','value':None}},
    {'type':'nullable','dtype':'executable.Class','value':{'type':'list','value':[]}},
])
def test_untrusted_state_refused_before_reconstruction(value):
    with pytest.raises(ValueError):state.validate({'format':state.FORMAT,'state':value})


def test_text_nodes_and_encoded_buffers_are_bounded_before_copying():
    for value in ['x'*(state.MAX_BYTES+1),np.zeros(state.MAX_ARRAY_BYTES+1,dtype='u1')]:
        with pytest.raises(ValueError):state.pack(value)
    cyclic=[];cyclic.append(cyclic)
    with pytest.raises(ValueError):state.pack(cyclic)
    with pytest.raises(ValueError):state.validate({'format':state.FORMAT,'state':{'type':'scalar','value':'x'*(state.MAX_BYTES+1)}})


from tests.test_scientific_host_worker import actual_host  # noqa: F401


def test_actual_host_feature_tables(actual_host):
    import inspect
    from pathlib import Path
    from orchestrator import scientific_host as parent
    from tests.test_native_regions import source
    prepared=actual_host
    code='import numpy as np\nfrom env_worker import scientific_structured_state as state\n'
    code+=inspect.getsource(features_round_trip)+'\n'+inspect.getsource(empty_tables)+'\nfeatures_round_trip()\nempty_tables()'
    with parent.review(prepared['key'],Path(prepared['folder']).name,{'volume':source(np.zeros((3,4,5),dtype='<u2'),[])}) as worker:
        result,_=worker.request({'op':'python-resources','source':code})
        assert result['ok'] and not result['console']['error'],result['console']['error']



def points_round_trip():
    import pandas as pd
    from napari.layers import Points
    from env_worker import scientific_points
    data=np.array([[1.25,2.5,4],[7,8.5,9]],dtype=np.float64)
    points=Points(data,features=pd.DataFrame({'id':np.array([2**63+1,2**63+2],dtype='uint64'),
        'class':pd.Categorical(['B','A'],categories=['B','A'],ordered=True)}),
        size=[3,7],symbol=['o','s'],face_color=['red','blue'],border_width=[.1,.2],
        scale=[2,.5,.25],translate=[10,20,30],axis_labels=['z','y','x'],units=['um']*3,
        text={'string':'{class}','size':12},name='Measurements')
    saved=scientific_points.snapshot(points)
    actual=scientific_points.restored(data.copy(),saved)
    assert actual.data.tobytes()==points.data.tobytes()
    assert actual.data_to_world((2,3,4))==points.data_to_world((2,3,4))
    pd.testing.assert_frame_equal(actual.features,points.features)
    assert scientific_points.snapshot(actual)==saved


def points_palette_admission():
    from unittest.mock import patch
    from napari.layers import Points
    from env_worker import scientific_points, scientific_structured_state as state
    points=Points(np.array([[1.,2.]]))
    palette=points._face.categorical_colormap
    palette.colormap.update({str(i):np.ones(4,dtype='f4') for i in range(state.MAX_NODES+1)})
    with patch.object(Points,'as_layer_data_tuple',side_effect=AssertionError('copied before admission')):
        try: scientific_points.snapshot(points)
        except ValueError: pass
        else: raise AssertionError('oversized category palette was accepted')
    palette.colormap.clear()
    palette.fallback_color.values=np.ones((state.MAX_ARRAY_BYTES,4),dtype='f4')
    with patch.object(Points,'as_layer_data_tuple',side_effect=AssertionError('copied before admission')):
        try: scientific_points.snapshot(points)
        except ValueError: pass
        else: raise AssertionError('oversized fallback cycle was accepted')


def test_actual_native_points_state(actual_host):
    import inspect
    from pathlib import Path
    from orchestrator import scientific_host as parent
    from tests.test_native_regions import source
    prepared=actual_host
    code='import numpy as np\n'+inspect.getsource(points_round_trip)+'\n'+inspect.getsource(points_palette_admission)+'\npoints_round_trip()\npoints_palette_admission()'
    with parent.review(prepared['key'],Path(prepared['folder']).name,{'volume':source(np.zeros((3,4,5),dtype='<u2'),[])}) as worker:
        result,_=worker.request({'op':'python-resources','source':code})
        assert result['ok'] and not result['console']['error'],result['console']['error']
