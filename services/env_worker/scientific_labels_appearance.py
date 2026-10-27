"""Labels display: contours, the selected label, automatic or direct colors and 3D rendering.

Changing these settings never writes label values, their dtype or calibration. The background
label is not only display, though: as in napari, it is the label Erase writes (and the one preserve
labels and fill treat as empty), so after it changes, erasing writes the new background into the
data, and remembered views (schema 14) restore it. Label identifiers travel as decimal strings so values above 2**53 stay exact in JavaScript.
Colors are built with the pinned napari colormap classes and assigned through the public
``Labels.colormap`` setter, exactly as napari's own controls do.
"""
import copy
import math
import re

RENDERING = ('translucent', 'iso_categorical')
GRADIENT = ('fast', 'smooth')
COLOR_MODES = ('auto', 'direct')
AUTO_SOURCES = ('original', 'seeded', 'other')
FIELDS = {'contour', 'selectedLabel', 'showSelectedLabel', 'colorMode', 'backgroundValue', 'autoSource', 'autoSeed',
          'directColors', 'rendering', 'isoGradientMode'}
MAX_DIRECT = 256
MAX_SEED = 2**32 - 2  # napari's new_colormap draws seeds below 2**32 - 1
# Thick contours dilate and erode each redraw with a diamond footprint of 2t²+2t+1 elements per
# displayed pixel. Admit at most this much work for the array's largest plane, so a redraw stays
# well under a second and never stalls the private worker.
CONTOUR_WORK = 1 << 27
LABEL = re.compile(r'-?(?:0|[1-9][0-9]{0,19})')
# Colormaps this worker built from a seed: id(colormap) -> (colormap, seed, base). The base is the
# layer's original automatic colormap, so a later Python change of colors is never read as seeded.
_SEEDED = {}


def _unit(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def _label(value):
    return type(value) is str and LABEL.fullmatch(value) is not None and value != '-0'


def settings(value):
    """Structural validation; dtype ranges and color availability are checked against a layer in prepare()."""
    def fail(message='Choose a contour width of zero or more, whole selected and background labels, automatic or '
                     'direct colors and a supported 3D rendering.'):
        raise ValueError(message)
    if type(value) is not dict or set(value) != FIELDS:
        fail()
    if type(value['contour']) is not int or value['contour'] < 0 or value['contour'] > 2**53 - 1:
        fail('Contour width must be a whole number of pixels, zero or more.')
    if not _label(value['selectedLabel']) or not _label(value['backgroundValue']):
        fail('Selected and background labels must be whole numbers.')
    if type(value['showSelectedLabel']) is not bool:
        fail()
    if type(value['colorMode']) is not str or value['colorMode'] not in COLOR_MODES:
        fail('Choose automatic or direct label colors.')
    if type(value['autoSource']) is not str or value['autoSource'] not in AUTO_SOURCES:
        fail()
    seed = value['autoSeed']
    if (value['autoSource'] == 'seeded') != (seed is not None):
        fail('New automatic colors need a seed; original colors have none.')
    if seed is not None and (type(seed) is not int or not 0 <= seed <= MAX_SEED):
        fail(f'Automatic color seeds are whole numbers from 0 to {MAX_SEED}.')
    colors = value['directColors']
    if colors is not None:
        if type(colors) is not list or len(colors) > MAX_DIRECT:
            fail(f'Direct colors list at most {MAX_DIRECT} labels here. Use Python for larger color tables.')
        seen = set()
        for row in colors:
            if (type(row) is not dict or set(row) != {'label', 'color'}
                    or not (row['label'] is None or _label(row['label'])) or row['label'] in seen
                    or type(row['color']) is not list or len(row['color']) != 4 or not all(_unit(n) for n in row['color'])):
                fail('Each direct color needs one whole label (or none, for every other label) and four RGBA values from 0 to 1.')
            seen.add(row['label'])
    if value['colorMode'] == 'direct' and colors is None:
        fail('This layer’s direct colors are too many to show here. Use Python to change them.')
    if type(value['rendering']) is not str or value['rendering'] not in RENDERING:
        fail('Choose translucent or isosurface 3D rendering for labels.')
    if type(value['isoGradientMode']) is not str or value['isoGradientMode'] not in GRADIENT:
        fail('Choose fast or smooth isosurface shading.')
    return copy.deepcopy(value)


def _key(label):
    return None if label is None else int(label)


def _direct_rows(color_dict):
    if len(color_dict) > MAX_DIRECT:
        return None
    rows = [{'label': None if key is None else str(int(key)), 'color': [float(n) for n in color]}
            for key, color in color_dict.items()]
    return sorted(rows, key=lambda row: (row['label'] is not None, int(row['label'] or 0)))


def _auto_source(layer):
    current = layer.colormap
    if current is layer._original_random_colormap:
        return 'original', None
    record = _SEEDED.get(id(current))
    if record is not None and record[0] is current and record[2] is layer._original_random_colormap:
        return 'seeded', record[1]
    return 'other', None


def snapshot(layer):
    from napari.utils.colormaps import CyclicLabelColormap
    auto = isinstance(layer.colormap, CyclicLabelColormap)
    source, seed = _auto_source(layer) if auto else ('original', None)
    return settings({'contour': int(layer.contour), 'selectedLabel': str(int(layer.selected_label)),
                     'showSelectedLabel': bool(layer.show_selected_label), 'colorMode': 'auto' if auto else 'direct',
                     'backgroundValue': str(int(layer.colormap.background_value)),
                     'autoSource': source, 'autoSeed': seed,
                     'directColors': _direct_rows(layer._direct_colormap.color_dict),
                     'rendering': str(layer.rendering), 'isoGradientMode': str(layer.iso_gradient_mode)})


def selected_color(layer):
    """napari's Labels.get_color for the selected label (None for the background), as its label control shows it."""
    value = layer.get_color(layer.selected_label)
    return None if value is None else [float(n) for n in value]


def check_selected_color(value):
    if value is not None and (type(value) is not list or len(value) != 4 or not all(_unit(n) for n in value)):
        raise ValueError('The selected label color must be RGBA values from 0 to 1, or none for the background')
    return copy.deepcopy(value)


def _in_range(layer, label, what):
    import numpy as np
    dtype = np.dtype(layer.data.dtype)
    if dtype.kind not in 'iu':
        raise ValueError('This Labels array needs an integer data type')
    limits = np.iinfo(dtype)
    if not limits.min <= int(label) <= limits.max:
        raise ValueError(f'The {what} {label} is outside this {dtype.name} array’s range {limits.min} to {limits.max}.')
    return int(label)


def contour_limit(layer):
    """The widest contour this array's largest displayed plane admits, from CONTOUR_WORK."""
    sizes = sorted(int(n) for n in layer.data.shape)[-2:]
    pixels = max(1, sizes[0] * sizes[1])
    widest = 0
    while 2 * (widest + 1) ** 2 + 2 * (widest + 1) + 1 <= CONTOUR_WORK // pixels:
        widest += 1
    return widest


class Prepared:
    """Validated settings plus the colormap objects to assign; built before anything changes."""
    def __init__(self, layer, value):
        value = settings(value)
        if layer._type_string != 'labels':
            raise ValueError('Choose a current Labels layer')
        if value['autoSource'] == 'other' and (value['colorMode'] != 'auto' or _auto_source(layer)[0] != 'other'):
            raise ValueError('Choose original automatic colors or new colors from a seed.')
        self.selected = _in_range(layer, value['selectedLabel'], 'selected label')
        background = _in_range(layer, value['backgroundValue'], 'background label')
        widest = contour_limit(layer)
        if value['contour'] > widest:
            raise ValueError(f'A contour {value["contour"]} pixels wide is too slow to redraw for this array; '
                             f'choose at most {widest}.')
        from napari.utils.colormaps import CyclicLabelColormap, DirectLabelColormap
        from napari.utils.colormaps.colormap_utils import shuffle_and_extend_colormap
        if value['colorMode'] == 'direct':
            table = {}
            for row in value['directColors']:
                if row['label'] is not None:
                    _in_range(layer, row['label'], 'direct color label')
                table[_key(row['label'])] = row['color']
            # Upstream adds a transparent default (with a warning) when none is given; say it here.
            table.setdefault(None, [0.0, 0.0, 0.0, 0.0])
            target = DirectLabelColormap(color_dict=table, background_value=background)
            # Upstream silently keeps automatic colors for a table of defaults only; refuse it instead.
            if layer._is_default_colors(target.color_dict):
                raise ValueError('Direct colors need at least one label color besides the defaults.')
            # Without numba, pinned napari's numpy colouring raises while drawing a direct table whose labels pass
            # 2**16 (doubled when any is negative). Refuse it here, before the private view is changed.
            from napari.utils.colormaps import _accelerated_cmap
            keys = [key for key in target.color_dict if key is not None]
            largest = max((abs(key) for key in keys), default=0) * (2 if any(key < 0 for key in keys) else 1)
            if largest > 2**16 and _accelerated_cmap.labels_raw_to_texture_direct is _accelerated_cmap._labels_raw_to_texture_direct_numpy:
                raise ValueError(f'This integration\'s napari, without numba, colors direct labels only up to {2**16} '
                                 f'(up to {2**15} when a label is negative). Use automatic colors for these labels, '
                                 'or an integration that includes numba.')
            seed = None
        else:
            base = layer._original_random_colormap
            seed = value['autoSeed']
            if value['autoSource'] == 'other':
                # Keep the automatic colors Python set on this exact frame's layer.
                target = layer.colormap
            else:
                target = base if seed is None else shuffle_and_extend_colormap(base, seed)
            if int(target.background_value) != background:
                target = CyclicLabelColormap(**{**target.model_dump(), 'background_value': background})
        self.layer, self.value, self.target, self.seed, self.kept = layer, value, target, seed, layer.colormap
        if seed is not None:
            target.use_selection = value['showSelectedLabel']
            target.selection = self.selected

    def normalized(self):
        """The settings as the constructed colormap holds them (float32 colors, added defaults)."""
        value = copy.deepcopy(self.value)
        if value['colorMode'] == 'direct':
            value['directColors'] = _direct_rows(self.target.color_dict)
        return settings(value)

    def apply(self):
        layer, value = self.layer, self.value
        base = layer._original_random_colormap
        if self.target is not layer.colormap:
            layer.colormap = self.target
        if value['colorMode'] == 'auto' and value['autoSource'] == 'other':
            if self.target is not self.kept:
                layer._original_random_colormap = base  # a new background on Python's colors; originals stay
        elif value['colorMode'] == 'auto' and self.target is not base:
            if self.seed is None:
                # A new background on the original colors becomes the original automatic colormap.
                base = self.target
            else:
                _SEEDED[id(self.target)] = (self.target, self.seed, base)
                while len(_SEEDED) > 16:
                    del _SEEDED[next(iter(_SEEDED))]
            # Upstream new_colormap keeps the original automatic colors for later shuffles.
            layer._original_random_colormap = base
        layer.selected_label = self.selected
        layer.show_selected_label = value['showSelectedLabel']
        layer.contour = value['contour']
        layer.rendering = value['rendering']
        layer.iso_gradient_mode = value['isoGradientMode']


def prepare(layer, value):
    return Prepared(layer, value)


def reserve(frame, layer_id, value):
    """Direct color tables appear in every frame; budget them before changing native state."""
    from . import scientific_view_wire as wire
    projected = copy.deepcopy(frame)
    row = next(row for row in projected['layers'] if row['id'] == layer_id)

    def expanded(item):
        if type(item) in (int, float): return 'x' * 24
        if type(item) is list: return [expanded(n) for n in item]
        if type(item) is dict: return {key: expanded(n) for key, n in item.items()}
        return item
    row['labelsAppearance'] = expanded(value)
    wire.bounded(projected, wire.MAX_BYTES - 256)
