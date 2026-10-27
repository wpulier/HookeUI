"""Explicit regional image decoding over one immutable selected-file grant.

The integration owns format interpretation and synchronous bounded decoding. The
host validates each returned region and owns caching, capture and publication.
Decoders must be deterministic over their captured input and fixed settings; they
are never retained as executable instructions in a saved result.
"""
from dataclasses import dataclass
import secrets

import numpy as np

from .native_regions import layout, region
from .source_files import SourceFile
from .source_directory import SourceDirectory


@dataclass(frozen=True)
class DecodedImage:
    shape: tuple
    dtype: str
    axes: tuple
    _source: object
    _decode: object

    def read(self, bounds, *, view=False):
        slices, shape, _ = region(bounds, self.shape, self.dtype)
        if self._source.closed:
            raise ValueError('The selected source is closed; reopen the input before decoding')
        value = self._decode(slices)
        if (type(value) is not np.ndarray or value.shape != shape or value.dtype.str != self.dtype):
            raise ValueError('The decoder changed the declared region shape, dtype or byte order')
        if self._source.closed:
            raise ValueError('The selected source closed during decoding; no result was accepted')
        # Detach the bounded region from decoder-owned mutable buffers, including
        # buffers reused by a later call. The retained source itself is immutable.
        return np.frombuffer(value.tobytes(order='C'), dtype=self.dtype).reshape(shape)


def image(files, *, input, shape, dtype, axes, decode, cache):
    from .lazy_arrays import LazyNative
    source = files[input]
    if type(source) not in (SourceFile, SourceDirectory):
        raise ValueError('Regional decoding requires an admitted selected-file input')
    if source.closed or not source.regional:
        raise ValueError('This reader must declare regional access before decoding image regions')
    shape, dtype, axes = layout(shape, dtype, axes)
    if not 2 <= len(shape) <= 5 or any(n == 0 for n in shape) or not callable(decode):
        raise ValueError('Regional images require a decoder and two to five nonempty dimensions')
    return LazyNative(DecodedImage(shape, dtype, axes, source, decode), cache, ident=secrets.token_hex(16))
