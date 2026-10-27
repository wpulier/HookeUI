"""Track coordinates and division edges: no invented instance/time alignment."""
import numpy as np
import pytest
from env_worker.scientific_tracks import check_data

DATA=np.array([[1,0,1,2],[1,1,2,3],[2,2,3,4],[2,3,4,5],[3,2,2,4],[3,3,3,5]],dtype='float64')
GRAPH=[(2,[1]),(3,[1])]

CREATE="""import numpy as np
import pandas as pd
from env_worker.scientific_tracks import snapshot
viewer.layers.clear()
points=viewer.add_tracks(np.array([[1,0,1,2],[1,1,2,3],[2,2,3,4],[2,3,4,5],[3,2,2,4],[3,3,3,5]],dtype='float64'),
    graph={2:[1],3:[1]},features=pd.DataFrame({'score':np.array([.1,.2,.3,.4,.5,.6],dtype='float32'),
    'measurement_id':np.array([2**63+i for i in range(6)],dtype='uint64')}),color_by='score',
    scale=[2,.5,.25],translate=[0,20,30],axis_labels=['t','y','x'],units=['s','um','um'],
    tail_length=12,head_length=4,tail_width=3,name='Dividing tracks')
points.display_id=True
points.display_tail=True
points.display_graph=True
original_state=snapshot(points)
keep_private=314
viewer.dims.ndisplay=3
viewer.reset_view()
"""


def test_division_edges_and_time_scoped_identity_are_explicit():
    assert check_data(DATA,GRAPH)=={2:[1],3:[1]}
    duplicate=np.concatenate([DATA,DATA[:1]])
    with pytest.raises(ValueError,match='ID and time'):check_data(duplicate,GRAPH)
    backwards=DATA.copy();backwards[2:,1]-=2
    with pytest.raises(ValueError,match='end before'):check_data(backwards,GRAPH)
    assert check_data(DATA,GRAPH)=={2:[1],3:[1]}  # correction remains usable


@pytest.mark.parametrize('graph', [[(2,[8])],[(8,[1])],[(2,[2])],[(2,[1,1])],[(2,[1]),(2,[1])],[(2,[1]),(1,[2])]])
def test_dangling_duplicate_and_cyclic_edges_are_refused(graph):
    with pytest.raises(ValueError):check_data(DATA,graph)


@pytest.mark.parametrize('column,value',[(0,-1),(0,.5),(0,2**32),(0,10_000_000),(1,-1),(1,1_000_001),(2,float('nan'))])
def test_invalid_or_unsafe_native_indices_refuse_before_allocation(column,value):
    data=DATA.copy();data[0,column]=value
    with pytest.raises(ValueError):check_data(data,GRAPH)


@pytest.mark.parametrize('width', [3,6,7,8,9])
def test_unsupported_coordinate_width_refuses_before_native_construction(width):
    data=np.zeros((6,width));data[:,:min(width,4)]=DATA[:,:min(width,4)]
    with pytest.raises(ValueError,match='coordinate rows'):check_data(data,GRAPH)


def test_aggregate_palettes_refuse_before_serializing_any_map(monkeypatch):
    import sys
    from types import SimpleNamespace
    from env_worker import scientific_tracks
    palette=SimpleNamespace(colors=np.zeros((256,4)),controls=np.zeros(256))
    layer=SimpleNamespace(_type_string='tracks',data=DATA,features=SimpleNamespace(columns=[]),
        graph={2:[1],3:[1]},colormap='test',colormaps_dict={str(i):palette for i in range(4)})
    monkeypatch.setitem(sys.modules,'napari.utils.colormaps.colormap_utils',
        SimpleNamespace(AVAILABLE_COLORMAPS={'test':palette}))
    def forbidden(*args):
        raise AssertionError('Oversized palettes reached serialization')
    monkeypatch.setattr(scientific_tracks,'_palette',forbidden)
    layer.as_layer_data_tuple=forbidden
    before=layer.data.copy()
    with pytest.raises(ValueError,match='complete bounded state'):scientific_tracks.snapshot(layer)
    np.testing.assert_array_equal(layer.data,before)
    assert layer.graph=={2:[1],3:[1]}


@pytest.mark.parametrize('width',[3,6,7,8,9])
def test_wire_refuses_unsupported_tracks_before_decoding_state(width,monkeypatch):
    from env_worker import scientific_output_wire,scientific_tracks
    def forbidden(*args):raise AssertionError('Invalid native dimensions reached state decoding')
    monkeypatch.setattr(scientific_tracks,'validate',forbidden)
    with pytest.raises(ValueError,match='Structured coordinates'):
        scientific_output_wire.metadata({'format':scientific_output_wire.TRACKS_FORMAT,
            'shape':[6,width],'dtype':'<f8','name':'Tracks','type':'tracks','geometry':{},
            'visible':True,'opacity':1.,'state':{}})


def test_empty_tracks_have_no_invented_lineage():
    assert check_data(np.empty((0,4)),[])=={}
    with pytest.raises(ValueError,match='lineage'):check_data(np.empty((0,4)),[(2,[1])])


def test_empty_track_metadata_refuses_impossible_lineage_before_output_capture(tmp_path):
    from env_worker import scientific_tracks as tracks,scientific_structured_state as state
    # Data-only wire state: validation must reject this before a worker transfers
    # any chunks. Zero rows have no payload on which to defer validation.
    scalar={'type':'scalar','value':None}
    settings={key:dict(scalar) for key in tracks.SETTINGS}
    settings['features']={'type':'table','columnIndex':{'type':'index','value':{'type':'objects','shape':[0],'value':[]},'name':scalar},
        'index':{'type':'range','value':[0,0,1],'name':scalar},'columns':[]}
    value=state.pack({'settings':{},'graph':[(2,[1])],'display':{},'default_colormap':None})
    root=value['state']['value'];root['settings']['value']=settings
    root['display']['value']={key:{'type':'scalar','value':False} for key in tracks.DISPLAY}
    with pytest.raises(ValueError,match='Empty tracks cannot contain lineage'):tracks.validate(value,0,3)

    from types import SimpleNamespace
    from orchestrator.scientific_projects import ProjectStore
    from orchestrator.scientific_outputs import OutputSave
    from orchestrator.scientific_saves import SaveNotStarted
    from tests.test_scientific_surface_output import source
    from env_worker.scientific_output_wire import TRACKS_FORMAT
    store=ProjectStore(tmp_path/'retained')
    original=source(store,{'key':'mine/example#view','folder':'/unused/'+'a'*16})
    info={'format':TRACKS_FORMAT,'shape':[0,4],'dtype':'<f8','name':'Empty tracks',
          'type':'tracks','geometry':{},'visible':True,'opacity':1.,'state':value}
    worker=SimpleNamespace(describe_output=lambda layer:info)  # No transport methods: must refuse first.
    save=OutputSave(store,original,'empty-track-forgery-123456789','b'*32,
                    {'semantic':'track-coordinates','axes':['t','y','x']})
    with pytest.raises(SaveNotStarted,match='Empty tracks cannot contain lineage'):save.capture(worker)
    assert save.stage is None and not store._folder(save.target).exists()
    assert not list(store.root.glob('.scientific-output-*'))
    assert store.read(original.id,'s1')==original
