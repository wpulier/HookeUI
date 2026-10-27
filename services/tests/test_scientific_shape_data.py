"""Native shape boundaries and types retain ragged scientific identity."""
from types import SimpleNamespace
import numpy as np
import pytest
from env_worker.scientific_shape_data import native, describe, split


def layer():
    return SimpleNamespace(_type_string='shapes',ndim=2,shape_type=['line','polygon'],
        data=[np.array([[1,2],[3,4]],dtype='<f4'),np.array([[4,3],[5,7],[9,8]],dtype='<f4')])


def test_ragged_geometry_reconstructs_each_boundary_without_numeric_conversion():
    original=layer(); metadata=describe(original)
    assert metadata['vertices']=={'shape':[5,2],'dtype':'<f4'}
    rows=split({'vertices':np.concatenate(original.data),'offsets':np.array([0,2,5],dtype='<u8')},original.shape_type)
    for actual,expected in zip(rows,original.data):
        assert actual.dtype.str==expected.dtype.str and actual.tobytes()==expected.tobytes()


@pytest.mark.parametrize('offsets', [[1,2,5],[0,3,4],[0,2,6],[0,0,5],[0,5,2]])
def test_incomplete_overlapping_or_reordered_boundaries_refuse(offsets):
    original=layer()
    with pytest.raises(ValueError): split({'vertices':np.concatenate(original.data),'offsets':np.array(offsets,dtype='<u8')},original.shape_type)


def test_mixed_native_types_and_incomplete_shapes_are_not_silently_converted():
    original=layer(); original.data[1]=original.data[1].astype('>f8')
    with pytest.raises(ValueError,match='Mixed numeric'): native(original)
    original=layer();original.shape_type[0]='rectangle'
    with pytest.raises(ValueError,match='incomplete'):native(original)


@pytest.mark.parametrize('offsets', [[1,2,5],[0,3,5],[0,2,6],[0,0,5],[0,5,2]])
def test_parent_refuses_invalid_boundaries_before_writing(offsets):
    from orchestrator.scientific_outputs import OutputGrant
    from orchestrator.scientific_projects import InvalidProject
    data=np.array(offsets,dtype='<u8')
    draft=SimpleNamespace(closed=False,shape=data.shape,dtype=data.dtype.str,
        axes=('shape-boundary',),chunks=data.shape,grid=(1,),attempted_bytes=0,
        allowance=data.nbytes,scientific={'semantic':'shape-offsets'},
        write=lambda *args:pytest.fail('Invalid geometry boundaries reached storage'))
    grant=OutputGrant(draft,vertex_count=5,shape_types=('line','polygon'))
    header={'op':'write-native-chunk','token':grant.identity,'index':[0],
        'shape':list(data.shape),'dtype':data.dtype.str}
    with pytest.raises(InvalidProject,match='boundaries'):grant.answer(header,data.tobytes())


def test_unknown_component_cannot_borrow_boundary_axes():
    from env_worker.scientific_shape_data import axes
    with pytest.raises(ValueError,match='Unknown'):axes('unrelated',1)

@pytest.mark.parametrize('ndim',[2,3,8])
def test_empty_shapes_have_explicit_dimension_and_single_zero_boundary(ndim):
    from env_worker.scientific_shape_data import components,boundaries
    empty=SimpleNamespace(_type_string='shapes',ndim=ndim,shape_type=[],data=[])
    assert describe(empty)=={'vertices':{'shape':[0,ndim],'dtype':'<f8'},'offsets':{'shape':[1],'dtype':'<u8'}}
    parts=components(empty)
    assert parts['vertices'].shape==(0,ndim) and parts['vertices'].dtype.str=='<f8'
    assert parts['offsets'].tolist()==[0] and parts['offsets'].dtype.str=='<u8'
    boundaries(parts['offsets'],[],0)
    assert split(parts,[])==[]

@pytest.mark.parametrize('vertices,offsets,dtype',[(1,1,'<f8'),(0,2,'<f8'),(0,1,'>f8')])
def test_empty_shape_declarations_cannot_hide_vertices_or_invent_empty_geometry(vertices,offsets,dtype):
    from env_worker.scientific_shape_data import declarations
    with pytest.raises(ValueError):declarations({'vertices':{'shape':[vertices,3],'dtype':dtype},'offsets':{'shape':[offsets],'dtype':'<u8'}})


@pytest.mark.parametrize('offset,types',[(1,()),(0,('line',))])
def test_parent_refuses_impossible_empty_shape_boundary_before_storage(offset,types):
    from orchestrator.scientific_outputs import OutputGrant
    from orchestrator.scientific_projects import InvalidProject
    data=np.array([offset],dtype='<u8')
    draft=SimpleNamespace(closed=False,shape=(1,),dtype='<u8',axes=('shape-boundary',),
        chunks=(1,),grid=(1,),attempted_bytes=0,allowance=8,scientific={'semantic':'shape-offsets'},
        write=lambda *args:pytest.fail('Impossible empty shape boundary reached storage'))
    grant=OutputGrant(draft,vertex_count=0,shape_types=types)
    header={'op':'write-native-chunk','token':grant.identity,'index':[0],'shape':[1],'dtype':'<u8'}
    with pytest.raises(InvalidProject,match='boundaries'):grant.answer(header,data.tobytes())
