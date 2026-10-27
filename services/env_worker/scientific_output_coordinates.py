"""Explicit same-grid declarations; equality alone never establishes a relationship."""
import copy
import hashlib
from itertools import product
import math
import re

from .native_regions import DTYPES, MAX_REGION_BYTES

FORMAT = 'same-input-grid-v1'
MAX_SOURCE_BYTES = 64 << 20


def declaration(value):
    from gpu_worker.semantic_resources import parse_reference
    if (type(value) is not dict or set(value) != {'format', 'input', 'reference', 'sha256'}
            or value['format'] != FORMAT or type(value['input']) is not str
            or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', value['input'])
            or type(value['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', value['sha256'])):
        raise ValueError('Output coordinates need an exact versioned bound-input declaration')
    reference = parse_reference(value['reference'])
    if type(reference.get('revision')) is not str or not reference.get('component', '').startswith('array.'):
        raise ValueError('Output coordinates require an exact accepted native array revision')
    return copy.deepcopy(value)


def fingerprint(shape, dtype, read):
    """Same bounded tiles for accepted storage and the worker's frozen input values."""
    import numpy as np
    shape = tuple(shape)
    if (not 2 <= len(shape) <= 8 or any(type(n) is not int or n <= 0 for n in shape)
            or math.prod(shape)*DTYPES[dtype] > MAX_SOURCE_BYTES):
        raise ValueError('Same-grid coordinates support bounded scalar inputs up to 64 MiB')
    chunks = list(shape)
    while math.prod(chunks)*DTYPES[dtype] > MAX_REGION_BYTES:
        axis = max(range(len(chunks)), key=chunks.__getitem__)
        chunks[axis] = max(1, chunks[axis]//2)
    digest = hashlib.sha256(repr((dtype, shape)).encode())
    for index in product(*(range(0, n, c) for n, c in zip(shape, chunks))):
        region = tuple(slice(i, min(i+c, n)) for i, c, n in zip(index, chunks, shape))
        data = read(region)
        if (type(data) is not np.ndarray or data.dtype.str != dtype
                or data.shape != tuple(s.stop-s.start for s in region)):
            raise ValueError('The coordinate input reader changed shape or dtype')
        digest.update(data.tobytes(order='C'))
    return digest.hexdigest()


def plain(info):
    return {key:value for key, value in info.items() if key != 'coordinatesFrom'}


def grid(info, meaning, source):
    """Validate geometry independently of the output's dtype and scientific meaning."""
    from . import scientific_output_wire as wire
    calibration = source.descriptor.get('calibration')
    shape = info['shape'][:-1] if info['format'] == wire.RGB_FORMAT else info['shape']
    if (info['format'] not in (wire.FORMAT, wire.RGB_FORMAT)
            or list(source.shape) != shape or list(source.axes) != meaning['axes']
            or type(calibration) is not dict or calibration.get('axes') != list(source.axes)
            or not wire.same_calibration(wire.calibration(info, meaning['axes'], calibration['space']), calibration)):
        raise ValueError('Declared output coordinates must retain the exact input grid, axes, affine and units')
    return copy.deepcopy(calibration)


class AcceptedSource:
    """Cache only the immutable admitted resource's value proof, never a live layer baseline."""
    def __init__(self, source):
        self.source = source
        self.reference = copy.deepcopy(source.reference)
        self.sha256 = None

    def fingerprint(self):
        if self.source.reference != self.reference:
            raise ValueError('The accepted input reference changed; reopen its exact revision')
        if self.sha256 is None:
            self.sha256 = fingerprint(self.source.shape, self.source.dtype,
                lambda region: self.source.read([[part.start, part.stop] for part in region], view=True))
        return self.sha256


class Binding:
    """A declaration grants identity, not completion; describe/save requires a stable proof."""
    def __init__(self, editable, source_layer, output, meaning, sources):
        from .scientific_layer_exports import geometry
        self.editable, self.layer, self.output = editable, source_layer, output
        self.input = editable.input_for(source_layer)
        if self.input is None:
            raise ValueError('Output coordinates require an actual bound input layer')
        source = editable.inputs[self.input]
        if math.prod(source.shape)*DTYPES[source.dtype] > MAX_SOURCE_BYTES:
            raise ValueError('Same-grid coordinates support bounded scalar inputs up to 64 MiB')
        accepted = sources.get(self.input)
        if accepted is None:
            accepted = sources[self.input] = AcceptedSource(source)
        if accepted.source is not source or accepted.reference != source.reference:
            raise ValueError('The accepted input changed; reopen its exact revision')
        self.accepted = accepted
        self.reference = copy.deepcopy(accepted.reference)
        self.axes = list(source.axes)
        from .scientific_output_wire import declaration as output_meaning
        self.meaning = output_meaning(meaning)
        self.geometry = geometry(source_layer)

    def _observe(self):
        if self.editable.input_for(self.layer) != self.input:
            raise ValueError('The coordinate source is no longer an admitted bound input')
        data = self.editable.capture(self.layer, self.input, self.reference)
        return fingerprint(data.shape, data.dtype.str, lambda region:data[region])

    def check(self):
        from .scientific_layer_exports import geometry
        from .scientific_outputs import describe
        from .scientific_output_wire import FORMAT as SCALAR, RGB_FORMAT
        info = describe(self.output)
        source = self.editable.inputs[self.input]
        shape = info['shape'][:-1] if info['format'] == RGB_FORMAT else info['shape']
        observed = self._observe()  # unchanged quiescence guard, before any accepted-source read
        accepted = self.accepted.fingerprint()
        if (info['format'] not in (SCALAR, RGB_FORMAT) or list(source.shape) != shape
                or self.meaning is None or self.meaning['axes'] != self.axes
                or geometry(self.output) != self.geometry or observed != accepted):
            raise ValueError('The declared coordinate input or output grid changed; accept the input and rerun first')
        return declaration({'format':FORMAT, 'input':self.input, 'reference':self.reference, 'sha256':accepted})
