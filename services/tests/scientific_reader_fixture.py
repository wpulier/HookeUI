"""Synthetic integration using actual NumPy and napari; not a qualified catalog tool."""
ADAPTER = '''def open_host(context):
    import numpy as np
    source = context.files["source"]
    if source.size > 64 * 1024 * 1024:
        raise ValueError("This reader supports at most 64 MiB")
    import math
    version = np.lib.format.read_magic(source)
    if version not in ((1, 0), (2, 0)):
        raise ValueError("This reader supports NumPy file versions 1 and 2")
    header = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
    shape, fortran, dtype = header(source, max_header_size=10000)
    if (not 2 <= len(shape) <= 5 or dtype.kind not in "uif" or dtype.hasobject
            or math.prod(shape) * dtype.itemsize > source.size - source.tell()):
        raise ValueError("Choose a complete numeric 2D–5D NumPy array")
    source.seek(0)
    values = np.load(source, allow_pickle=False)
    if not isinstance(values, np.ndarray) or not 2 <= values.ndim <= 5 or values.dtype.kind not in "uif":
        raise ValueError("Choose a numeric 2D–5D NumPy array")
    layer = context.viewer.add_image(values, name=source.name)
    context.bind_view(layer=layer, key="selected-source")
    from magicgui.widgets import Container
    return Container(widgets=[])
'''
