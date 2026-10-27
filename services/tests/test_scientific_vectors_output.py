"""Vector origins and directions keep distinct scientific coordinate meaning."""
import numpy as np
import pytest
from env_worker.scientific_vectors import check_data

DATA=np.array([[[1,2,3],[2,0,-1]],[[4,5,6],[0,3,1]],[[2,4,6],[-2,1,0]]],dtype='float64')
CREATE="""import numpy as np
import pandas as pd
from env_worker.scientific_vectors import snapshot
viewer.layers.clear()
points=viewer.add_vectors(np.array([[[1,2,3],[2,0,-1]],[[4,5,6],[0,3,1]],[[2,4,6],[-2,1,0]]],dtype='float64'),
    features=pd.DataFrame({'id':np.array([2**63+1,2**63+2,2**63+3],dtype='uint64'),
        'class':pd.Categorical(['B','A','B'],categories=['B','A'],ordered=True)}),edge_color='class',
    scale=[2,.5,.25],translate=[10,20,30],axis_labels=['z','y','x'],units=['um']*3,
    length=2.5,edge_width=3,vector_style='arrow',name='Measured vectors')
np.testing.assert_array_equal(points.data_to_world(points.data[0,0]),[12,21,30.75])
np.testing.assert_array_equal(np.array(points.data_to_world(points.data[0,0]+points.data[0,1]))-np.array(points.data_to_world(points.data[0,0])),[4,0,-.25])
native_events=[]
points.events.edge_color_mode.connect(lambda event: native_events.append(event.type))
points.edge_color_mode='direct'
points.edge_color_mode='cycle'
assert len(native_events)==2,native_events
original_state=snapshot(points)
keep_private=314
viewer.dims.ndisplay=3
viewer.reset_view()
"""

@pytest.mark.parametrize('shape',[(3,3),(3,1,3),(3,3,3),(3,2,1),(3,2,9),(0,2,1)])
def test_vectors_refuse_invalid_origin_direction_structure(shape):
    with pytest.raises(ValueError,match='origin and displacement'):check_data(np.zeros(shape))


def test_vectors_do_not_coerce_missing_or_infinite_directions():
    for bad in (float('nan'),float('inf')):
        data=DATA.copy();data[0,1,0]=bad
        with pytest.raises(ValueError):check_data(data)
    check_data(DATA)


def test_oversized_continuous_palette_refuses_before_serialization():
    from types import SimpleNamespace
    from env_worker.scientific_layer_colors import admit
    from env_worker.scientific_structured_state import MAX_ARRAY_BYTES
    manager=SimpleNamespace(categorical_colormap=SimpleNamespace(colormap={},fallback_color=SimpleNamespace(values=np.zeros((1,4)))),
        continuous_colormap=SimpleNamespace(colors=SimpleNamespace(nbytes=MAX_ARRAY_BYTES),controls=SimpleNamespace(nbytes=8)))
    with pytest.raises(ValueError,match='continuous palette'):admit(manager)


@pytest.mark.parametrize('shape',[[3,2],[3,3,3],[3,2,3,4],[3,2,1],[3,2,9]])
def test_vector_wire_refuses_invalid_shape_before_state_decode(shape,monkeypatch):
    from env_worker import scientific_output_wire,scientific_vectors
    def forbidden(*args):raise AssertionError('Invalid vector shape reached state decoding')
    monkeypatch.setattr(scientific_vectors,'validate',forbidden)
    with pytest.raises(ValueError):
        scientific_output_wire.metadata({'format':scientific_output_wire.VECTORS_FORMAT,
            'shape':shape,'dtype':'<f8','name':'Vectors','type':'vectors','geometry':{},
            'visible':True,'opacity':1.,'state':{}})


def test_empty_vectors_have_a_valid_coordinate_schema():
    check_data(np.empty((0,2,3)))
