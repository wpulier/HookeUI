"""Bounded native-panel messages; validation never grants interaction authority."""
from __future__ import annotations

import copy
import json
import re

FORMAT = 'scientific-native-panel-v1'
MAX_SIDE = 1024
MAX_BYTES = 64 << 10
MAX_NODES = 512
KEYS = ('Tab', 'Backtab', 'Backspace', 'Delete', 'Return', 'Enter', 'Escape',
        'Space', 'Left', 'Right', 'Up', 'Down', 'Home', 'End', 'PageUp', 'PageDown')


def require(value):
    if not value:
        raise ValueError('The native panel returned unsupported or inconsistent metadata')


def token(value, size):
    return type(value) is str and re.fullmatch('[a-f0-9]{' + str(size) + '}', value) is not None


def text(value, maximum=1000):
    if type(value) is not str:
        return False
    try:
        return len(value.encode('utf-16-le')) // 2 <= maximum
    except UnicodeError:
        return False


def dimensions(width, height):
    require(all(type(n) is int and 1 <= n <= MAX_SIDE for n in (width, height)))
    require(width * height * 32 <= 32 << 20)


def bounded(value):
    try:
        require(len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()) <= MAX_BYTES)
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ValueError('Native panel messages must be bounded finite JSON') from error


def frame(value, pixels=None):
    require(type(value) is dict and set(value) == {
        'format', 'panel', 'frame', 'width', 'height', 'pixelRatio', 'accessibility', 'focus'})
    require(value['format'] == FORMAT and token(value['panel'], 32) and token(value['frame'], 64))
    dimensions(value['width'], value['height'])
    require(type(value['pixelRatio']) is int and value['pixelRatio'] == 1)
    require(value['focus'] is None or text(value['focus']))
    rows = value['accessibility']
    require(type(rows) is list and 1 <= len(rows) <= MAX_NODES)
    bounded(value)
    parents, depths = {}, {}
    booleans = {'disabled', 'focused', 'focusable', 'invisible', 'checked', 'checkable', 'readOnly'}
    for index, row in enumerate(rows):
        require(type(row) is dict and set(row) == booleans | {'id', 'parent', 'role', 'name', 'description', 'value', 'rect'})
        identity = row['id']
        require(type(identity) is str and re.fullmatch('[0-9]{1,20}', identity) is not None and identity not in parents)
        parent = row['parent']
        require(parent is None if index == 0 else type(parent) is str and parent in parents)
        depth = 0 if parent is None else depths[parent] + 1
        require(depth <= 16)
        parents[identity], depths[identity] = parent, depth
        require(type(row['role']) is str and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,79}', row['role']) is not None)
        require(all(text(row[key]) for key in ('name', 'description', 'value')))
        require(all(type(row[key]) is bool for key in booleans))
        rect = row['rect']
        require(type(rect) is list and len(rect) == 4 and all(type(n) is int and abs(n) <= 32768 for n in rect))
        require(rect[2] >= 0 and rect[3] >= 0)
    # Native composite widgets may expose focus on a parent and its text child.
    # Preserve the native tree instead of inventing a single focused element.
    if pixels is not None:
        require(type(pixels) is bytes and len(pixels) == value['width'] * value['height'] * 4)
    return copy.deepcopy(value)


def command(value, current):
    frame(current)
    require(type(value) is dict and set(value) == {'panel', 'frame', 'op', 'value'})
    require(value['panel'] == current['panel'] and value['frame'] == current['frame'])
    bounded(value)
    operation, argument = value['op'], value['value']
    def point(position):
        require(type(position) is list and len(position) == 2 and all(type(n) is int for n in position))
        require(0 <= position[0] < current['width'] and 0 <= position[1] < current['height'])

    if operation == 'click':
        point(argument)
    elif operation == 'drag':
        require(type(argument) is dict and set(argument) == {'points', 'button', 'cancel'})
        require(type(argument['points']) is list and 2 <= len(argument['points']) <= 256)
        require(type(argument['button']) is str and argument['button'] in ('left', 'right'))
        require(type(argument['cancel']) is bool)
        for position in argument['points']:
            point(position)
    elif operation == 'wheel':
        require(type(argument) is dict and set(argument) == {'point', 'angleDelta'})
        point(argument['point'])
        delta = argument['angleDelta']
        require(type(delta) is list and len(delta) == 2
                and all(type(n) is int and abs(n) <= 1200 for n in delta) and any(delta))
    elif operation == 'key':
        require(type(argument) is str and argument in KEYS)
    elif operation == 'text':
        require(text(argument, 4000) and bool(argument)
                and all(ord(c) >= 32 or c in '\n\t' for c in argument))
    else:
        raise ValueError('Choose a declared native panel interaction')
    return copy.deepcopy(value)
