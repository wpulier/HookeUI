"""Points and Shapes styling: markers, widths, face/border/edge colors by value or by feature, text content, z-order.

Every change goes through napari's public layer setters in the order napari's own controls use, so a color by
feature is napari's cycle or colormap over that feature column (`face_color = feature`, then its cycle or colormap
and contrast limits, then the mode). Coordinates and feature values are never written. A per-element value that
differs between elements (sizes, symbols, widths or direct colors set in Python) is shown as mixed and kept unless
a new value for every element is given. Settings are validated completely in the parent and against the layer
before anything changes (`prepare`), so a refused change leaves the layer as it was.
"""
import copy
import math

SYMBOLS = ('arrow', 'clobber', 'cross', 'diamond', 'disc', 'hbar', 'ring', 'square', 'star', 'tailed_arrow',
           'triangle_down', 'triangle_up', 'vbar', 'x')
MODES = ('direct', 'cycle', 'colormap')
TEXT_KINDS = ('constant', 'feature', 'format', 'other')
MAX_CYCLE = 64  # every frame carries each cycle; keep a layer's style within the view's metadata allowance
MAX_TEXT = 1000
MAX_FORMAT_ROWS = 10000
FORMAT_BATCH = 64
MAX_FORMAT_CHARS = 1 << 20
MAX_CHOICES = 64
MAX_FEATURE_NAME = 128
SIDES = {'points': ('face', 'border'), 'shapes': ('face', 'edge')}
POINT_FIELDS = {'symbol', 'size', 'borderWidth', 'borderWidthIsRelative', 'face', 'border'}
SHAPE_FIELDS = {'edgeWidth', 'face', 'edge'}


def _finite(n):
    try:
        return type(n) in (int, float) and math.isfinite(n)
    except OverflowError:
        return False


def _rgba(value):
    return type(value) is list and len(value) == 4 and all(_finite(n) and 0 <= n <= 1 for n in value)


def _name(value):
    return type(value) is str and 0 < len(value) <= MAX_TEXT


def color(value):
    """One side's colors: a direct color (None keeps each element's), or napari's cycle or colormap over a feature."""
    if type(value) is not dict or value.get('mode') not in MODES:
        raise ValueError('Colors need a mode: direct, cycle by feature or colormap by feature')
    mode = value['mode']
    if mode == 'direct':
        if set(value) != {'mode', 'color'} or not (value['color'] is None or _rgba(value['color'])):
            raise ValueError('A direct color is four values from 0 to 1 (red, green, blue, alpha)')
    elif mode == 'cycle':
        cycle = value.get('cycle')
        if (set(value) != {'mode', 'feature', 'cycle'} or not _name(value['feature']) or type(cycle) is not list
                or not 1 <= len(cycle) <= MAX_CYCLE or not all(_rgba(c) for c in cycle)):
            raise ValueError(f'A color cycle needs a feature and 1 to {MAX_CYCLE} colors, each four values from 0 to 1')
    else:
        limits = value.get('contrastLimits')
        if (set(value) != {'mode', 'feature', 'colormap', 'contrastLimits'} or not _name(value['feature'])
                or not _name(value['colormap'])
                or not (limits is None or type(limits) is list and len(limits) == 2 and all(_finite(n) for n in limits)
                        and limits[0] <= limits[1])):
            raise ValueError('A colormap needs a numeric feature, a colormap name and ordered contrast limits (or none for the data range)')
    return copy.deepcopy(value)


def _width(value, what):
    if not (value is None or _finite(value) and value >= 0):
        raise ValueError(f'{what} must be zero or more (or kept per element)')


def point_settings(value):
    if type(value) is not dict or set(value) != POINT_FIELDS:
        raise ValueError('Marker style needs symbol, size, border width, relative border and face and border colors')
    if not (value['symbol'] is None or value['symbol'] in SYMBOLS):
        raise ValueError('Choose one of napari\'s point symbols')
    if not (value['size'] is None or _finite(value['size']) and value['size'] >= 0):
        raise ValueError('Point size must be zero or more')
    _width(value['borderWidth'], 'Border width')
    if type(value['borderWidthIsRelative']) is not bool:
        raise ValueError('Relative border width is on or off')
    if value['borderWidthIsRelative'] and value['borderWidth'] is not None and value['borderWidth'] > 1:
        raise ValueError('A relative border width is a fraction of the point size, from 0 to 1')
    return {**copy.deepcopy(value), 'face': color(value['face']), 'border': color(value['border'])}


def shape_settings(value):
    if type(value) is not dict or set(value) != SHAPE_FIELDS:
        raise ValueError('Shape style needs edge width and face and edge colors')
    _width(value['edgeWidth'], 'Edge width')
    return {**copy.deepcopy(value), 'face': color(value['face']), 'edge': color(value['edge'])}


def text_settings(value):
    if type(value) is not dict or set(value) != {'string', 'color'}:
        raise ValueError('Text content needs its text and color')
    string = value['string']
    if (type(string) is not dict or set(string) != {'kind', 'value'} or string['kind'] not in TEXT_KINDS[:3]
            or type(string['value']) is not str or len(string['value']) > MAX_TEXT
            or string['kind'] != 'constant' and not string['value']):
        raise ValueError('Text is a constant, a feature name or a format string such as {area:.1f}')
    if not (value['color'] is None or _rgba(value['color'])):
        raise ValueError('Text color is four values from 0 to 1, or kept')
    return copy.deepcopy(value)


def order(value):
    if value not in ('front', 'back'):
        raise ValueError('Move the selected shapes to the front or to the back')
    return value


def choices(value):
    if (type(value) is not dict or set(value) != {'features', 'colormaps'} or type(value['features']) is not list
            or type(value['colormaps']) is not list or len(value['features']) > MAX_CHOICES or len(value['colormaps']) > 256
            or not all(type(f) is list and len(f) == 2 and type(f[0]) is str and 0 < len(f[0]) <= MAX_FEATURE_NAME and f[1] in ('numeric', 'categorical') for f in value['features'])
            or not all(type(n) is str and 0 < len(n) <= MAX_FEATURE_NAME for n in value['colormaps'])):
        raise ValueError('Style choices are feature names with their kinds and colormap names')
    return value


def described(row):
    """The parent's check of a frame row's style fields."""
    kind = row.get('type')
    if ('pointStyle' in row or 'shapeStyle' in row) and 'styleChoices' not in row:
        raise ValueError('A layer style comes with its feature and colormap choices')
    if 'pointStyle' in row:
        if kind != 'points':
            raise ValueError('Marker style belongs to Points layers')
        _snapshot_check(point_settings, row['pointStyle'], ('face', 'border'))
    if 'shapeStyle' in row:
        if kind != 'shapes':
            raise ValueError('Shape style belongs to Shapes layers')
        _snapshot_check(shape_settings, row['shapeStyle'], ('face', 'edge'))
    if 'textContent' in row:
        content = row['textContent']
        if kind not in ('points', 'shapes') or type(content) is not dict or set(content) != {'string', 'color'}:
            raise ValueError('Text content belongs to Points and Shapes layers')
        if content['string'] != {'kind': 'other', 'value': ''}:
            text_settings(content)
        elif not (content['color'] is None or _rgba(content['color'])):
            raise ValueError('Text color is four values from 0 to 1')
    if 'styleChoices' in row:
        choices(row['styleChoices'])


def _snapshot_check(check, value, sides):
    # A cycle too long to edit here is described with no colors; everything else is valid settings.
    if type(value) is dict and all(type(value.get(side)) is dict for side in sides):
        value = {**value, **{side: {**value[side], 'cycle': [[0, 0, 0, 1]]} for side in sides
                             if value[side].get('mode') == 'cycle' and value[side].get('cycle') is None}}
    check(value)


# --- in the worker, with napari -------------------------------------------------------------------------------

def _common(values):
    import numpy as np
    values = np.asarray(values)
    if not len(values) or values.ndim != 1 and values.ndim != 2:
        return None
    first = values[0]
    return first if (values == first).all() else None


def _color_snapshot(layer, side):
    """One side's colors through napari's public properties (Points keep them in a ColorManager, Shapes on the layer)."""
    import numpy as np
    mode = str(getattr(layer, f'{side}_color_mode'))
    if mode == 'direct':
        colors = np.asarray(getattr(layer, f'{side}_color'), dtype=float)
        same = len(colors) and (colors == colors[0]).all()
        return {'mode': 'direct', 'color': [float(n) for n in colors[0]] if same else None}
    feature = (layer._face if side == 'face' else layer._border).color_properties.name if layer._type_string == 'points' \
        else getattr(layer, f'_{side}_color_property')
    if mode == 'cycle':
        cycle = np.asarray(getattr(layer, f'{side}_color_cycle'), dtype=float)
        return {'mode': 'cycle', 'feature': str(feature),
                'cycle': [[float(n) for n in row] for row in cycle] if len(cycle) <= MAX_CYCLE else None}
    limits = getattr(layer, f'{side}_contrast_limits')
    return {'mode': 'colormap', 'feature': str(feature), 'colormap': str(getattr(layer, f'{side}_colormap').name),
            'contrastLimits': None if limits is None else [float(limits[0]), float(limits[1])]}


def point_snapshot(layer):
    symbol = _common([str(s.value) for s in layer.symbol])
    size, width = _common(layer.size), _common(layer.border_width)
    return {'symbol': None if symbol is None else str(symbol), 'size': None if size is None else float(size),
            'borderWidth': None if width is None else float(width), 'borderWidthIsRelative': bool(layer.border_width_is_relative),
            'face': _color_snapshot(layer, 'face'), 'border': _color_snapshot(layer, 'border')}


def shape_snapshot(layer):
    width = _common(layer.edge_width)
    return {'edgeWidth': None if width is None else float(width),
            'face': _color_snapshot(layer, 'face'), 'edge': _color_snapshot(layer, 'edge')}


def text_snapshot(layer):
    text = layer.text
    kind = type(text.string).__name__
    string = ({'kind': 'constant', 'value': str(text.string.constant)} if kind == 'ConstantStringEncoding'
              else {'kind': 'feature', 'value': str(text.string.feature)} if kind == 'DirectStringEncoding'
              else {'kind': 'format', 'value': str(text.string.format)} if kind == 'FormatStringEncoding'
              else {'kind': 'other', 'value': ''})
    if string['kind'] != 'other' and len(string['value']) > MAX_TEXT:
        string = {'kind': 'other', 'value': ''}
    constant = getattr(text.color, 'constant', None) if type(text.color).__name__ == 'ConstantColorEncoding' else None
    return {'string': string, 'color': None if constant is None else [float(n) for n in constant]}


def style_choices(layer):
    import numpy as np
    from napari.utils.colormaps import AVAILABLE_COLORMAPS
    # The first features with short names are offered as choices; every frame carries them.
    named = [name for name in layer.features.columns if type(name) is str and 0 < len(name) <= MAX_FEATURE_NAME][:MAX_CHOICES]
    features = [[name, 'numeric' if issubclass(layer.features[name].dtype.type, np.number) else 'categorical'] for name in named]
    return {'features': features, 'colormaps': sorted(name for name in map(str, AVAILABLE_COLORMAPS) if len(name) <= MAX_FEATURE_NAME)[:256]}


def _data_range(layer, feature):
    import numpy as np
    values = np.asarray(layer.features[feature], dtype=float)
    return (float(np.nanmin(values)), float(np.nanmax(values))) if np.isfinite(values).any() else (0.0, 0.0)


def _admit_color(layer, value):
    import numpy as np
    if value['mode'] == 'direct':
        return
    feature = value['feature']
    if feature not in layer.features.columns:
        raise ValueError(f'This layer has no feature named {feature!r}')
    if value['mode'] == 'colormap':
        if not issubclass(layer.features[feature].dtype.type, np.number):
            raise ValueError('A colormap colors a numeric feature; choose a cycle for categories')
        # Pinned napari preserves (v, v) for constant features; it is valid native state and must remain editable.
        from napari.utils.colormaps import ensure_colormap
        try:
            ensure_colormap(value['colormap'])
        except (KeyError, ValueError, TypeError) as error:
            raise ValueError(f'napari has no colormap named {value["colormap"]!r}') from error


def _apply_color(layer, side, value):
    mode = value['mode']
    if mode == 'direct':
        if value['color'] is None:
            setattr(layer, f'{side}_color_mode', 'direct')
        else:
            setattr(layer, f'{side}_color', value['color'])
        return
    setattr(layer, f'{side}_color', value['feature'])
    if mode == 'cycle':
        setattr(layer, f'{side}_color_cycle', value['cycle'])
    else:
        setattr(layer, f'{side}_colormap', value['colormap'])
        # No limits means the feature's data range, as napari takes it when a feature is first chosen; napari keeps
        # earlier limits when the same feature is chosen again, so the range is set explicitly.
        limits = value['contrastLimits'] or _data_range(layer, value['feature'])
        setattr(layer, f'{side}_contrast_limits', tuple(limits))
    setattr(layer, f'{side}_color_mode', mode)


def _changed_sides(layer, settings, sides, current):
    """Only the sides whose colors change, colormaps last: napari's Shapes refresh both sides when a cycle is set (with
    update_color_mapping), which would replace limits set just before; an unchanged side is never set again."""
    changed = [side for side in sides if settings[side] != current[side]]
    return sorted(changed, key=lambda side: settings[side]['mode'] == 'colormap')


def prepare_points(layer, value):
    settings = point_settings(value)
    for side in SIDES['points']:
        _admit_color(layer, settings[side])
    import numpy as np
    width = layer.border_width if settings['borderWidth'] is None else np.full(len(layer.data), settings['borderWidth'])
    if settings['borderWidthIsRelative'] and np.any(width > 1):
        raise ValueError('A relative border width is a fraction of the point size, from 0 to 1')
    current = point_snapshot(layer)
    sides = _changed_sides(layer, settings, SIDES['points'], current)

    def apply():
        if settings['symbol'] is not None and settings['symbol'] != current['symbol']:
            layer.symbol = settings['symbol']
        if settings['size'] is not None and settings['size'] != current['size']:
            layer.size = settings['size']
        # napari refuses a relative flag while a width is above 1: lower the width first, or clear the flag first.
        width_changes = settings['borderWidth'] is not None and settings['borderWidth'] != current['borderWidth']
        if settings['borderWidthIsRelative']:
            if width_changes:
                layer.border_width = settings['borderWidth']
            if not current['borderWidthIsRelative']:
                layer.border_width_is_relative = True
        else:
            if current['borderWidthIsRelative']:
                layer.border_width_is_relative = False
            if width_changes:
                layer.border_width = settings['borderWidth']
        for side in sides:
            _apply_color(layer, side, settings[side])
    return apply


def prepare_shapes(layer, value):
    settings = shape_settings(value)
    for side in SIDES['shapes']:
        _admit_color(layer, settings[side])
    current = shape_snapshot(layer)
    sides = _changed_sides(layer, settings, SIDES['shapes'], current)

    def apply():
        if settings['edgeWidth'] is not None and settings['edgeWidth'] != current['edgeWidth']:
            layer.edge_width = settings['edgeWidth']
            # Pinned napari's edge_width setter updates the widths but emits nothing, so the canvas would keep the old
            # width until another change; napari's own width control (current_edge_width) emits this after updating.
            layer.events.edge_width()
        for side in sides:
            _apply_color(layer, side, settings[side])
    return apply


def _format_length(parts, row):
    """Bound each substitution before upstream can allocate a whole formatted batch.

    Only direct feature names and known scalar formatters have a bounded cost.
    Attribute/index access and arbitrary object formatting need a different contract.
    """
    import numpy as np
    length = 0
    for literal, field, spec, conversion in parts:
        length += len(literal)
        if field is not None:
            if not field or any(c in field for c in '.[]') or field not in row:
                raise ValueError('use a direct existing feature name in each text substitution')
            value = row[field]
            if type(value) is np.str_ and len(value) > MAX_TEXT:
                raise ValueError('feature substitution exceeds the text allowance')
            scalar_types = (np.str_, np.bool_, np.int8, np.int16, np.int32, np.int64,
                            np.uint8, np.uint16, np.uint32, np.uint64, np.float16, np.float32, np.float64)
            if type(value) in scalar_types:
                value = value.item()
            if type(value) not in (str, int, float, bool, type(None)):
                raise ValueError('text substitutions support only string, numeric, boolean or empty feature scalars')
            if (type(value) is str and len(value) > MAX_TEXT
                    or type(value) is int and value.bit_length() > MAX_TEXT * 3):
                raise ValueError('feature substitution exceeds the text allowance')
            if conversion not in (None, 's', 'r', 'a'):
                raise ValueError('unsupported text conversion')
            if conversion is not None:
                value = {'s': str, 'r': repr, 'a': ascii}[conversion](value)
            # Fixed widths/precision and bounded builtin scalars make this one allocation small.
            length += len(format(value, spec))
        if length > MAX_TEXT:
            raise ValueError('formatted label exceeds the text allowance')
    return length


def prepare_text(layer, value):
    settings = text_settings(value)
    string = settings['string']
    from napari.layers.utils.string_encoding import (ConstantStringEncoding, DirectStringEncoding,
                                                    FormatStringEncoding)
    encoding = (ConstantStringEncoding(constant=string['value']) if string['kind'] == 'constant'
                else DirectStringEncoding(feature=string['value']) if string['kind'] == 'feature'
                else FormatStringEncoding(format=string['value']))
    if string['kind'] == 'feature' and string['value'] not in layer.features.columns:
        raise ValueError(f'This layer has no feature named {string["value"]!r}')
    if string['kind'] == 'format':
        try:  # napari evaluates the format at every refresh: try it on the features now, before anything changes
            import re
            from string import Formatter
            if len(layer.features) > MAX_FORMAT_ROWS:
                raise ValueError(f'format validation supports at most {MAX_FORMAT_ROWS} rows; choose a smaller layer')
            parts = list(Formatter().parse(string['value']))
            for _, field, spec, _ in parts:
                if field is None:
                    continue
                if '{' in spec or '}' in spec or any(int(n) > MAX_TEXT for n in re.findall(r'\d+', spec)):
                    raise ValueError(f'format widths and precision must be fixed and at most {MAX_TEXT}')
            count = 0
            for start in range(0, len(layer.features), FORMAT_BATCH):
                batch = layer.features.iloc[start:start + FORMAT_BATCH]
                count += sum(_format_length(parts, row) for row in batch.to_dict('records'))
                if count > MAX_FORMAT_CHARS:
                    raise ValueError('formatted labels exceed the text allowance; use shorter text or a smaller layer')
                rendered = encoding(batch)
                lengths = [len(str(text)) for text in rendered]
                if any(n > MAX_TEXT for n in lengths):
                    raise ValueError('formatted labels exceed the text allowance; use shorter text or a smaller layer')
        except (KeyError, ValueError, IndexError, TypeError, AttributeError) as error:
            raise ValueError(f'This format string does not fit the layer\'s features: {error}') from error
    def apply():
        layer.text.string = encoding
        if settings['color'] is not None:
            layer.text.color = settings['color']
    return apply


def prepare_order(layer, value):
    action = order(value)
    if not layer.selected_data:
        raise ValueError('Select one or more shapes first (Inspect › Select this shape)')
    return layer.move_to_front if action == 'front' else layer.move_to_back


def reserve(frame, layer_id, key, value):
    """Color cycles appear in every frame; budget the widest description before changing native state."""
    from . import scientific_view_wire as wire
    projected = copy.deepcopy(frame)
    row = next(row for row in projected['layers'] if row['id'] == layer_id)

    def expanded(item):
        if type(item) in (int, float): return 'x' * 24
        if type(item) is list: return [expanded(n) for n in item]
        if type(item) is dict: return {name: expanded(n) for name, n in item.items()}
        return item
    row[key] = expanded(value)
    wire.bounded(projected, wire.MAX_BYTES - 256)


def element_style(value, kind):
    """One inspected element's own style: what napari holds for that point or shape, never a layer summary."""
    names = {'points': {'symbol', 'size', 'borderWidth', 'faceColor', 'borderColor'},
             'shapes': {'edgeWidth', 'faceColor', 'edgeColor', 'z'}}[kind]
    if type(value) is not dict or set(value) != names:
        raise ValueError('An inspected element\'s style is incomplete')
    if kind == 'points':
        if value['symbol'] not in SYMBOLS or not _finite(value['size']) or not _finite(value['borderWidth']) \
                or not _rgba(value['faceColor']) or not _rgba(value['borderColor']):
            raise ValueError('An inspected point\'s style is invalid')
    elif not _finite(value['edgeWidth']) or not _rgba(value['faceColor']) or not _rgba(value['edgeColor']) \
            or type(value['z']) is not int or abs(value['z']) > 2**53 - 1:
        raise ValueError('An inspected shape\'s style is invalid')
    return value


def element_snapshot(layer, index):
    color = lambda values: [float(n) for n in values[index]]
    if layer._type_string == 'points':
        return element_style({'symbol': str(layer.symbol[index].value), 'size': float(layer.size[index]),
                              'borderWidth': float(layer.border_width[index]), 'faceColor': color(layer.face_color),
                              'borderColor': color(layer.border_color)}, 'points')
    return element_style({'edgeWidth': float(layer.edge_width[index]), 'faceColor': color(layer.face_color),
                          'edgeColor': color(layer.edge_color), 'z': int(layer.z_index[index])}, 'shapes')
