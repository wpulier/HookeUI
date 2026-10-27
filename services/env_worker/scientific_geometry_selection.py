"""Frame-admitted native geometry selection; geometry is never reconstructed."""
from .scientific_view_wire import fields, require, token, integer


def settings(value):
    fields(value, 'layer action index')
    require(token(value['layer']) and value['action'] in ('replace', 'toggle', 'clear'))
    require(value['index'] is None if value['action']=='clear' else type(value['index']) is int and integer(value['index'],0,2**53-1))
    return value


def prepare(session, value, kind):
    require(kind in ('points','shapes'))
    settings(value)
    layer=next((layer for identity,layer in session._layers if identity==value['layer']),None)
    if (layer is None or layer._type_string!=kind or not layer.visible
            or session.viewer.dims.ndisplay!=2 or session.viewer.grid.enabled or not layer.loaded):
        raise ValueError('Choose the visible geometry layer in a completed single 2D preview')
    index=value['index']
    if index is not None:
        visible=(index<len(layer.data) and index in layer._view_indices
                 and (kind!='points' or layer.shown[index]))
        if not visible:
            raise ValueError('This geometry is outside the current visible slice. Inspect a visible row before selecting it.')
    selection=set() if value['action']=='clear' else {index} if value['action']=='replace' else set(layer.selected_data) ^ {index}
    def apply():
        layer.selected_data=selection
    return apply
