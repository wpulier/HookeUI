"""Closed, bounded manual Labels commands and their non-publication receipts."""
import copy
import re
from .scientific_view_wire import require, token, number
from .scientific_label_history import polygon_region

ACTIONS = ('paint','erase','fill','polygon','pick','undo','redo','reset')


def command(value):
    require(type(value) is dict)
    action = value.get('action')
    names = {'op','request','frame','layer','action','points','width','height'}
    if action in ('paint','erase','fill'):
        names |= {'label','brush','preserve','contiguous'}
        if 'dimensions' in value:
            names.add('dimensions')
            require(type(value['dimensions']) is int and 2 <= value['dimensions'] <= 8)
    if action == 'polygon':
        names |= {'label', 'preserve'}
    require(set(value) == names and value['op'] == 'labels' and action in ACTIONS)
    require(type(value['request']) is str and re.fullmatch(r'[A-Za-z0-9_-]{16,128}',value['request']) is not None)
    require(token(value['frame'],64) and token(value['layer']))
    require(all(type(value[key]) is int and 1 <= value[key] <= 1024 for key in ('width','height')))
    points = value['points']
    require(type(points) is list and len(points) <= 256)
    require(all(type(point) is list and len(point) == 2 and all(number(n) for n in point)
                and 0 <= point[0] < value['height'] and 0 <= point[1] < value['width'] for point in points))
    require(len(points) == 0 if action in ('undo','redo','reset') else len(points) == 1 if action in ('fill','pick') else bool(points))
    if action == 'polygon':
        polygon_region(points)
    if action in ('paint','erase','fill','polygon'):
        require(type(value['preserve']) is bool)
        require(type(value['label']) is str and re.fullmatch(r'0|[1-9][0-9]{0,19}',value['label']) is not None)
    if action in ('paint','erase','fill'):
        require(type(value['brush']) is int and 1 <= value['brush'] <= 256)
        require(type(value['preserve']) is bool and type(value['contiguous']) is bool)
    return copy.deepcopy(value)


def receipt(value, identity):
    require(type(value) is dict and set(value) == {'request','state','changed','label','message'})
    require(value['request'] == identity and value['state'] in ('applied','refused'))
    require(type(value['changed']) is bool and (value['state'] != 'refused' or value['changed'] is False))
    require(value['label'] is None or type(value['label']) is str and re.fullmatch(r'0|[1-9][0-9]{0,19}',value['label']) is not None)
    require(type(value['message']) is str and len(value['message']) <= 2000)
    return copy.deepcopy(value)
