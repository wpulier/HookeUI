"""Independent example consumer: discrete native stack slices, no animation package.

The same public sequence binding exports a stack in its original slice order.
This local authored consumer is not a held-out integration qualification.
"""
import math
from itertools import product
import numpy as np


def open_host(context):
    from magicgui.widgets import Container
    if context.restored_layers is None:
        if len(context.inputs) != 1:
            raise ValueError('Select exactly one three-dimensional intensity stack')
        key, source = next(iter(context.inputs.items()))
        if (len(source.shape) != 3 or source.descriptor.get('semantic') != 'intensity'
                or math.prod(source.shape) * np.dtype(source.dtype).itemsize > 64 << 20):
            raise ValueError('Use a three-dimensional intensity stack within 64 MiB')
        kwargs = {'axis_labels': source.axes}
        calibration = source.descriptor.get('calibration')
        if calibration is not None:
            if calibration['axes'] != list(source.axes):
                raise ValueError('Stack calibration must cover its ordered input axes')
            affine = np.eye(4)
            affine[:3] = calibration['affine']
            kwargs.update(affine=affine, units=calibration['units'])
        data = np.empty(source.shape, dtype=source.dtype)
        chunks = list(source.shape)
        while math.prod(chunks) * data.itemsize > 1 << 20:
            axis = max(range(3), key=chunks.__getitem__)
            chunks[axis] = max(1, chunks[axis] // 2)
        for start in product(*(range(0, n, c) for n, c in zip(source.shape, chunks))):
            bounds = [[s, min(s+c, n)] for s, c, n in zip(start, chunks, source.shape)]
            data[tuple(slice(*pair) for pair in bounds)] = source.read(bounds)
        layer = context.viewer.add_image(data, name=key, **kwargs)
        context.bind_view(layer=layer, key=key)
        context.bind_editable(layer=layer, input=key)
    layers = list(context.viewer.layers)
    if len(layers) != 1 or layers[0].ndim != 3 or layers[0]._type_string != 'image':
        raise ValueError('This stack sequence needs one three-dimensional image')
    def restore(state):
        if state != {'format': 1}: raise ValueError('Unsupported stack sequence state')
    context.bind_document(schema='hooke.native-stack', version=1, capture=lambda: {'format': 1}, restore=restore)
    def select(index):
        context.viewer.dims.ndisplay = 2
        context.viewer.dims.order = (0, 1, 2)
        context.viewer.dims.set_current_step(0, index)
    context.bind_sequence(version=1, name='native-stack', count=lambda: int(layers[0].data.shape[0]), select=select)
    return Container()
