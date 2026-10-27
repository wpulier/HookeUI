"""Image histograms and contrast ranges from the pinned napari HistogramModel; display only.

Counts come from napari's own model (the displayed slice in canvas mode, the whole array
sampled to max_samples in full mode), so they match napari's histogram exactly. Nothing here
changes native values; the contrast range is the display slider's range.
"""
import copy
import math

MODES = ('canvas', 'full')
MAX_BINS = 512  # every bin travels in the frame; the frame budget is checked before applying
MAX_SAMPLES = 1 << 31
SETTINGS = {'enabled', 'bins', 'mode', 'logScale', 'maxSamples'}
ACTIONS = ('auto-once', 'reset-range', 'autoscale-range', 'set-range', 'reset-histogram')
# 'integer' says whether napari's contrast popup offers "full range" (integer images only).
FIELDS = SETTINGS | {'range', 'binEdges', 'counts', 'integer'}


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def settings(value):
    if (type(value) is not dict or set(value) != SETTINGS or type(value['enabled']) is not bool
            or type(value['bins']) is not int or not 1 <= value['bins'] <= MAX_BINS
            or type(value['mode']) is not str or value['mode'] not in MODES or type(value['logScale']) is not bool
            or type(value['maxSamples']) is not int or not 1 <= value['maxSamples'] <= MAX_SAMPLES):
        raise ValueError(f'Choose 1 to {MAX_BINS} histogram bins, the displayed slice or the whole array, '
                         'a linear or logarithmic scale and a positive sample limit.')
    return copy.deepcopy(value)


def described(value):
    """A frame's histogram: its settings, the contrast range and, when enabled, napari's counts."""
    if type(value) is not dict or set(value) != FIELDS:
        raise ValueError('The image histogram returned unsupported metadata')
    settings({key: value[key] for key in SETTINGS})
    if type(value['integer']) is not bool:
        raise ValueError('The image histogram returned an unsupported data type flag')
    low, high = value['range'] if type(value['range']) is list and len(value['range']) == 2 else (None, None)
    if not (_finite(low) and _finite(high) and low <= high):
        raise ValueError('The image histogram returned an unsupported contrast range')
    edges, counts = value['binEdges'], value['counts']
    if not value['enabled']:
        if edges is not None or counts is not None:
            raise ValueError('A disabled histogram has no counts')
    elif (type(edges) is not list or type(counts) is not list or len(edges) != len(counts) + 1
          or not 1 <= len(counts) <= MAX_BINS or not all(_finite(n) for n in edges)
          or any(b < a for a, b in zip(edges, edges[1:])) or not all(_finite(n) and n >= 0 for n in counts)):
        raise ValueError('The image histogram returned unsupported counts')
    return copy.deepcopy(value)


def snapshot(layer):
    model = layer.histogram
    value = {'enabled': bool(model.enabled), 'bins': int(model.bins), 'mode': str(model.mode),
             'logScale': bool(model.log_scale), 'maxSamples': int(model.max_samples),
             'range': [float(n) for n in layer.contrast_limits_range], 'binEdges': None, 'counts': None,
             'integer': integer(layer)}
    if model.enabled:
        counts = model.counts  # computes when dirty, exactly as napari's histogram widget reads it
        value['counts'] = [float(n) for n in counts]
        value['binEdges'] = [float(n) for n in model._bin_edges]
    return described(value)


def apply(layer, value):
    """Set only what changed, one field at a time as napari's histogram control does, enabling last.

    napari transforms counts in place when only the scale changes and recomputes otherwise, so writing
    unchanged fields again would take a different path than its own control and could differ by a unit
    in the last place.
    """
    model = layer.histogram
    if not value['enabled'] and model.enabled:
        model.enabled = False
    for field, key in (('bins', 'bins'), ('max_samples', 'maxSamples'), ('mode', 'mode'), ('log_scale', 'logScale')):
        if getattr(model, field) != value[key]:
            setattr(model, field, value[key])
    if value['enabled'] and not model.enabled:
        model.enabled = True


def integer(layer):
    """napari's popup test for its full-range button: an integer data type (signed or not)."""
    import numpy as np
    from napari.utils._dtype import normalize_dtype
    return bool(np.issubdtype(normalize_dtype(layer.dtype), np.integer))


def contrast(value):
    """One contrast action: napari's Apply auto-contrast, its contrast popup's full range and reset, or a range."""
    if type(value) is not dict or value.get('action') not in ACTIONS:
        raise ValueError('Choose auto-contrast once, the full range, autoscale the range, or set a contrast range')
    if value['action'] == 'set-range':
        limits = value.get('range')
        if (set(value) != {'action', 'range'} or type(limits) is not list or len(limits) != 2
                or not all(_finite(n) for n in limits) or limits[0] >= limits[1]):
            raise ValueError('Enter an increasing finite contrast range')
    elif set(value) != {'action'}:
        raise ValueError('This contrast action takes no other settings')
    return copy.deepcopy(value)


def apply_contrast(layer, value):
    if value['action'] == 'auto-once':
        layer.reset_contrast_limits()  # napari's auto_contrast_once action (shortcut C)
    elif value['action'] == 'reset-range':
        layer.reset_contrast_limits_range()  # the contrast popup's "full range" (integer images)
    elif value['action'] == 'reset-histogram':
        layer.histogram.reset()  # default settings, disabled, cached counts dropped
    elif value['action'] == 'autoscale-range':
        # The contrast popup's "reset" (napari/_qt/layer_controls/widgets/qt_contrast_limits.py, reset()).
        layer.reset_contrast_limits()
        layer.contrast_limits_range = layer.contrast_limits
    else:
        layer.contrast_limits_range = value['range']


def admit_contrast(layer, value):
    if value['action'] == 'reset-range' and not integer(layer):
        raise ValueError('napari offers Full range only for integer images, where it means the whole type. '
                         'Use Autoscale range to data or set the range for this image.')


def reserve(frame, layer_id, bins):
    from . import scientific_view_wire as wire
    projected = copy.deepcopy(frame)
    row = next(row for row in projected['layers'] if row['id'] == layer_id)
    row['imageHistogram'] = {'enabled': True, 'bins': bins, 'mode': 'canvas', 'logScale': False, 'maxSamples': 'x' * 24,
                             'range': ['x' * 24] * 2, 'binEdges': ['x' * 24] * (bins + 1), 'counts': ['x' * 24] * bins,
                             'integer': False}
    wire.bounded(projected, wire.MAX_BYTES - 256)
