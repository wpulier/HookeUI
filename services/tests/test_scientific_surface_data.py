"""Mesh components keep topology and measured values distinct from coordinates."""
from types import SimpleNamespace

import numpy as np
import pytest

from env_worker.scientific_surface_data import components, declarations


def mesh():
    return SimpleNamespace(_type_string='surface',
        data=(np.array([[0, 0, 0], [0, 1, 0], [1, 0, 0]], dtype='>f8'),
              np.array([[0, 1, 2]], dtype='<i4'), np.array([1, np.nan, 3], dtype='>f4')),
        texture=None, texcoords=None, vertex_colors=None)


def test_components_keep_original_bytes_and_missing_measurements():
    layer = mesh()
    found = components(layer)
    for key, array in zip(('vertices', 'faces', 'values'), layer.data):
        assert found[key] is array
        assert found[key].tobytes() == array.tobytes()
    assert np.isnan(found['values'][1])


@pytest.mark.parametrize('faces', [np.array([[0, 1, 3]]), np.array([[0, -1, 2]]),
    np.array([[0., 1., 2.]]), np.array([[0, 1]])])
def test_invalid_topology_cannot_become_a_saved_mesh(faces):
    layer = mesh()
    layer.data = (layer.data[0], faces, layer.data[2])
    with pytest.raises(ValueError): components(layer)


def test_coordinates_and_texture_correspondence_are_required():
    layer = mesh()
    layer.texcoords = np.zeros((2, 2))
    with pytest.raises(ValueError, match='each vertex'): components(layer)
    layer.texcoords = np.zeros((3, 2))
    components(layer)
    layer.data[0][0, 0] = np.nan
    with pytest.raises(ValueError, match='finite vertices'): components(layer)


def test_value_series_requires_an_explicit_axis_integration_without_flattening():
    layer = mesh()
    values = np.zeros((2, 3))
    layer.data = (*layer.data[:2], values)
    with pytest.raises(ValueError, match='extra axes'): components(layer)
    assert layer.data[2] is values


def test_aggregate_components_are_bounded_before_allocating():
    with pytest.raises(ValueError, match='allowance'):
        declarations({'vertices': {'shape': [2000000, 3], 'dtype': '<f8'},
            'faces': {'shape': [2000000, 3], 'dtype': '<i8'},
            'values': {'shape': [2000000], 'dtype': '<f8'}})


@pytest.mark.parametrize('meaning,data', [
    ('surface-faces', np.array([[0,1,3]],dtype='<i4')),
    ('surface-faces', np.array([[0,-1,2]],dtype='<i4')),
    ('surface-vertices', np.array([[0,np.nan,1]],dtype='<f8')),
])
def test_parent_rejects_malicious_component_bytes_before_writing(meaning,data):
    from orchestrator.scientific_outputs import OutputGrant
    from orchestrator.scientific_projects import InvalidProject
    draft=SimpleNamespace(closed=False,shape=data.shape,dtype=data.dtype.str,
        axes=('row','coordinate'),chunks=data.shape,grid=(1,1),attempted_bytes=0,
        allowance=data.nbytes,scientific={'semantic':meaning},
        write=lambda *args:pytest.fail('Invalid component reached retained storage'))
    grant=OutputGrant(draft,vertex_count=3)
    header={'op':'write-native-chunk','token':grant.identity,'index':[0,0],
        'shape':list(data.shape),'dtype':data.dtype.str}
    with pytest.raises(InvalidProject,match='topology'): grant.answer(header,data.tobytes())


def test_all_components_freeze_before_any_transport_callback(monkeypatch):
    from env_worker import scientific_outputs as module
    from env_worker.native_edits import OUTPUT_FORMAT
    from env_worker.scientific_surface_data import axes
    import sys,threading
    frames=sys._current_frames;harness=set(frames())-{threading.get_ident()}
    monkeypatch.setattr(sys,'_current_frames',lambda:{key:value for key,value in frames().items() if key not in harness})
    layer=mesh();arrays=components(layer)
    expected={name:array.tobytes() for name,array in arrays.items()}
    # Isolate snapshot ordering; actual metadata/native construction is exercised
    # separately by the confined Surface integration tests.
    metadata={'type':'surface'}
    monkeypatch.setattr(module.wire,'compatible',lambda info,meaning:(info,meaning))
    monkeypatch.setattr(module,'describe',lambda layer:metadata)
    specifications=[{'name':name,'edit':{'format':OUTPUT_FORMAT,'token':name+'-owned-grant',
        'shape':list(array.shape),'dtype':array.dtype.str,'axes':axes(name,array.ndim),'chunks':list(array.shape)}}
        for name,array in sorted(arrays.items())]
    received={}
    def exchange(header,payload):
        name=header['token'].split('-')[0];received[name]=payload
        # The first write runs arbitrary transport callbacks. Later components
        # must already have captured the same pre-callback scientific state.
        arrays['values'][:]=999
        return {'ok':True,'kind':OUTPUT_FORMAT,'index':header['index'],'bytes':len(payload)},b''
    with module.capture_components(layer,metadata,{},specifications,exchange,'files') as snapshots:
        for grant,chunks in snapshots:
            for index,array in chunks:grant.write(index,array)
    assert received==expected


def test_every_optional_component_has_a_valid_scientific_role():
    from env_worker.scientific_surface_data import semantic, REQUIRED, OPTIONAL
    from gpu_worker.semantic_resources import parse_descriptor
    roles=[semantic(name) for name in REQUIRED | OPTIONAL]
    assert len(set(roles))==len(roles)
    for role in roles: assert parse_descriptor({'semantic':role}).wire()['semantic']==role
