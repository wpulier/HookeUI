"""Native layer selection, separate from selected geometry within a layer."""
from .scientific_view_wire import fields, require, token


def prepare(session, value):
    fields(value, 'layer action')
    require(value['action'] in ('replace', 'toggle', 'clear'))
    if value['action'] == 'clear':
        require(value['layer'] is None)
        return session.viewer.layers.selection.clear
    require(token(value['layer']))
    layer = next((layer for identity, layer in session._layers if identity == value['layer']), None)
    if layer is None or layer not in session.viewer.layers:
        raise ValueError('This layer is no longer in the scientific view. Refresh its layers before selecting it.')
    selection = session.viewer.layers.selection
    def apply():
        if value['action'] == 'replace':
            selection.select_only(layer)
        else:
            selection.toggle(layer)
    return apply


def snapshot(session):
    selection = session.viewer.layers.selection
    return {'selected': [identity for identity, layer in session._layers if layer in selection],
            'active': next((identity for identity, layer in session._layers if layer is selection.active), None)}
