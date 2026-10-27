"""Native thick-slice presentation, separate from scientific projection outputs, and native dimension navigation
(napari's own viewer actions, a world point and the focused slider), each validated before a native setter runs.

Actions run the pinned upstream functions themselves (`napari.components._viewer_key_bindings`), so Hooke's keyboard
and buttons have exactly napari's effect, including its rollable and singleton rules. A world point is napari's
`Dims.set_point`, which keeps a coordinate between slices and clips it to the range as upstream does.
"""
import copy
import math

PROJECTIONS = ('none', 'sum', 'mean', 'max', 'min')


def margins(value, ndim):
    def finite(n):
        try:
            return type(n) in (int, float) and math.isfinite(n) and n >= 0
        except OverflowError:
            return False
    if (type(value) is not dict or set(value) != {'left', 'right'}
            or any(type(value[key]) is not list or len(value[key]) != ndim
                   or not all(finite(n) for n in value[key]) for key in ('left', 'right'))):
        raise ValueError('Enter a finite, nonnegative slice margin on each side of every axis, in world coordinates')
    return copy.deepcopy(value)


def projection(value):
    if type(value) is not str or value not in PROJECTIONS:
        raise ValueError('Choose current slice, sum, mean, maximum or minimum for this Image layer')
    return value


def snapshot(dims):
    return margins({'left': list(dims.margin_left), 'right': list(dims.margin_right)}, dims.ndim)


def apply(dims, value):
    dims.margin_left = tuple(value['left'])
    dims.margin_right = tuple(value['right'])


# napari action name -> what the Studio offers. Only dimension actions; nothing here changes data.
ACTIONS = ('increment_dims_left', 'increment_dims_right', 'focus_axes_up', 'focus_axes_down',
           'roll_axes', 'transpose_axes', 'toggle_ndisplay')
NAVIGATION = ('viewer-action', 'dims-point', 'dims-focus')


def navigation(dims):
    """Fields the frame adds to its dimensions: the focused slider, rollable flags and axis units."""
    units = None if dims.units is None else [str(unit) for unit in dims.units]
    return {'lastUsed': int(dims.last_used), 'rollable': [bool(flag) for flag in dims.rollable], 'units': units}


def _sliders(dims):
    nsteps = dims.nsteps
    return [axis for axis in dims.not_displayed if nsteps[axis] > 1]


def prepare_navigation(viewer, operation, value):
    dims = viewer.dims
    if operation == 'viewer-action':
        if type(value) is not str or value not in ACTIONS:
            raise ValueError('Choose a supported dimension action')
        if value == 'toggle_ndisplay' and dims.ndisplay == 2 and dims.ndim < 3:
            raise ValueError('3D display needs at least three dimensions')
        if value in ('increment_dims_left', 'increment_dims_right', 'focus_axes_up', 'focus_axes_down') \
                and not _sliders(dims):
            raise ValueError('No sliced axis has more than one slice to move')
        from napari.components import _viewer_key_bindings as actions
        action = getattr(actions, value)
        return lambda: action(viewer)
    if operation == 'dims-point':
        if type(value) is not dict or set(value) != {'axis', 'value'} or type(value['axis']) is not int \
                or not 0 <= value['axis'] < dims.ndim or value['axis'] in dims.displayed:
            raise ValueError('Choose a sliced axis for this coordinate')
        coordinate = value['value']
        if type(coordinate) not in (int, float) or not math.isfinite(coordinate) or abs(coordinate) > 2 ** 53:
            raise ValueError('Enter a finite world coordinate')
        axis = value['axis']
        return lambda: dims.set_point(axis, float(coordinate))
    if operation == 'dims-focus':
        if type(value) is not int or value not in _sliders(dims):
            raise ValueError('Choose a sliced axis with more than one slice')
        def apply():
            dims.last_used = value
        return apply
    raise ValueError('This dimension operation is not supported')
