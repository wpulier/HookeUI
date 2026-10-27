"""Native display interpolation; saved pixels and calibration are never rewritten."""
import copy
import math

MODES = ('bessel','cubic','linear','blackman','catrom','gaussian','hamming',
         'hanning','hermite','kaiser','lanczos','mitchell','nearest','spline16','spline36')
MAX_KERNEL_SIDE = 32


def settings(value):
    if (type(value) is not dict or set(value) != {'interpolation2d','interpolation3d','kernel2d'}
            or type(value['interpolation2d']) is not str or value['interpolation2d'] not in (*MODES,'custom')
            or type(value['interpolation3d']) is not str or value['interpolation3d'] not in MODES):
        raise ValueError('Choose a supported 2D and 3D interpolation. Native napari does not support custom 3D interpolation.')
    kernel=value['kernel2d']
    if (type(kernel) is not list or not 1 <= len(kernel) <= MAX_KERNEL_SIDE
            or type(kernel[0]) is not list or not 1 <= len(kernel[0]) <= MAX_KERNEL_SIDE
            or any(type(row) is not list or len(row)!=len(kernel[0]) for row in kernel)
            or any(type(n) not in (int,float) or abs(n)>3.4028234663852886e38 or not math.isfinite(n) for row in kernel for n in row)):
        raise ValueError('The 2D interpolation kernel needs a rectangular finite matrix of at most 32 × 32 float32 values.')
    return copy.deepcopy(value)


def snapshot(layer):
    return settings({'interpolation2d':str(layer.interpolation2d),
                     'interpolation3d':str(layer.interpolation3d),
                     'kernel2d':layer.custom_interpolation_kernel_2d.tolist()})


def apply(layer,value):
    import numpy as np
    layer.custom_interpolation_kernel_2d=np.asarray(value['kernel2d'],dtype=np.float32)
    layer.interpolation2d=value['interpolation2d']
    layer.interpolation3d=value['interpolation3d']
