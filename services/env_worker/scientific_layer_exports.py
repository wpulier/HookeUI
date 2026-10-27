"""Factory-declared native layer exports. A binding grants no publication authority."""
from dataclasses import dataclass
from contextlib import contextmanager, ExitStack
from itertools import product
import copy
import math
from pathlib import Path
import sys
import tempfile
import threading

# The worker holds an editable layer in its own memory (1 GiB allowance), so the layer may be up to half
# of it. A save sends only the chunks that differ from the accepted base (H01 slice c); the bytes of
# changed chunks frozen in memory are bounded separately, and unchanged chunks cost only a digest.
MAX_BYTES = 512 << 20
MEMORY_SNAPSHOT_BYTES = 64 << 20


class FreezeBudget:
    """Changed-chunk bytes one save may freeze in worker memory, across every layer it stages."""

    def __init__(self, limit=None):
        self.limit = MEMORY_SNAPSHOT_BYTES if limit is None else limit
        self.used = 0

    def take(self, size):
        self.used += size
        if self.used > self.limit:
            raise ValueError(f'More than {self.limit >> 20} MiB of the layers in this save changed, too much to freeze '
                             'in the worker\'s memory. Save fewer layers or fewer changes at once, or free the worker\'s '
                             'disk; nothing was sent and private work is unchanged')


class _SnapshotFiles:
    """A sequential snapshot in bounded visible files, independent of chunk count."""
    FILE_BYTES = 1 << 20

    def __init__(self, directory):
        self.directory = Path(directory)
        self.stream = None
        self.total = self.position = self.number = self.offset = 0
        self.reading = False

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None

    def write(self, body):
        view = memoryview(body)
        while view:
            if self.stream is None:
                self.stream = (self.directory/str(self.number)).open('xb')
            count = min(len(view), self.FILE_BYTES-self.offset)
            if self.stream.write(view[:count]) != count:
                raise OSError('The private layer snapshot could not be written completely')
            self.total += count
            self.offset += count
            view = view[count:]
            if self.offset == self.FILE_BYTES:
                self.close(); self.number += 1; self.offset = 0

    def read(self, size):
        if not self.reading:
            self.close(); self.number = self.offset = 0; self.reading = True
        if self.position+size > self.total:
            raise OSError('The private layer snapshot is incomplete; accepted data was not changed')
        parts, remaining = [], size
        while remaining:
            if self.stream is None:
                path = self.directory/str(self.number)
                expected = min(self.FILE_BYTES, self.total-self.position)
                if path.stat().st_size != expected:
                    raise OSError('The private layer snapshot changed; accepted data was not changed')
                self.stream = path.open('rb')
            count = min(remaining, self.FILE_BYTES-self.offset)
            body = self.stream.read(count)
            if len(body) != count:
                raise OSError('The private layer snapshot is incomplete; accepted data was not changed')
            parts.append(body); remaining -= count; self.position += count; self.offset += count
            if self.offset == self.FILE_BYTES or self.position == self.total:
                self.close(); (self.directory/str(self.number)).unlink()
                self.number += 1; self.offset = 0
        return b''.join(parts)


def single_threaded():
    if set(sys._current_frames()) != {threading.get_ident()}:
        raise ValueError('Stop background Python work before saving this layer; a stable snapshot is required')


def quiescent(data):
    import numpy as np
    single_threaded()
    if type(data) is not np.ndarray:
        from .lazy_labels import LazyLabels
        if type(data) is LazyLabels:return
    base = data
    while type(base) is np.ndarray:
        if base.base is None:
            return
        base = base.base
    if type(base) is not bytes:
        raise ValueError('Saving requires worker-owned native storage; shared memory needs an explicit snapshot adapter')


def geometry(layer):
    ndim = layer.ndim
    points = [(0,) * ndim, *(tuple(int(i == j) for i in range(ndim)) for j in range(ndim))]
    transform = tuple(tuple(float(v) for v in layer.data_to_world(point)) for point in points)
    if any(len(row) != ndim or any(not math.isfinite(v) for v in row) for row in transform):
        raise ValueError('Editable layers need finite scientific coordinates')
    return transform, tuple(layer.axis_labels), tuple(str(unit) for unit in layer.units)


def values(layer, source):
    import numpy as np
    data = layer.data
    meaning = source.descriptor.get('semantic')
    expected = {'intensity': 'image', 'probabilities': 'image',
                'class-labels': 'labels', 'instance-labels': 'labels'}.get(meaning)
    if expected is None or layer._type_string != expected:
        raise ValueError('Declare the native input meaning before binding an editable layer')
    rgb = bool(getattr(layer, 'rgb', False))
    if rgb and (meaning!='intensity' or layer._type_string!='image' or len(source.shape)<3
                or source.shape[-1] not in (3,4) or source.axes[-1]!='color'
                or source.descriptor.get('channels')!={'axis':'color','ids':['red','green','blue','alpha'][:source.shape[-1]]}):
        raise ValueError('RGB layers require their declared spatial axes and exact color components')
    regional=False
    if type(data) is not np.ndarray:
        from .lazy_labels import LazyLabels
        regional=type(data) is LazyLabels and data._source is source and expected=='labels' and data._reference==source.reference
    if ((type(data) is not np.ndarray and not regional) or data.shape != source.shape or data.dtype.str != source.dtype
            or layer.ndim != len(source.axes)-int(rgb) or getattr(layer, 'multiscale', False)):
        raise ValueError('Saving this layer requires the original native shape, dtype and axes; use an explicit conversion for other data')
    if not regional and data.nbytes > MAX_BYTES:
        raise ValueError('An editable layer is held in the worker\'s memory, so it can be at most 512 MiB; '
                         'larger data needs lazy regional editing')
    return data


@dataclass(frozen=True)
class Binding:
    input: str
    layer: object
    source: object
    reference: dict
    geometry: tuple


class EditableLayers:
    def __init__(self, viewer, inputs):
        self.viewer, self.inputs = viewer, dict(inputs)
        self._bindings = []
        self._sealed = False

    def bind(self, *, layer, input):
        if self._sealed:
            raise ValueError('Declare editable layers only while the integration factory opens its view')
        if (type(input) is not str or input not in self.inputs
                or not any(current is layer for current in self.viewer.layers)
                or any(row.layer is layer or row.input == input for row in self._bindings)):
            raise ValueError('An editable binding needs one distinct viewer layer and one declared input')
        source = self.inputs[input]
        values(layer, source)
        self._bindings.append(Binding(input, layer, source, copy.deepcopy(source.reference), geometry(layer)))

    def seal(self):
        self._sealed = True

    def refresh(self):
        self._bindings = [row for row in self._bindings if any(layer is row.layer for layer in self.viewer.layers)]

    def input_for(self, layer):
        self.refresh()
        return next((row.input for row in self._bindings if row.layer is layer), None)

    def values_for(self, input):
        """The current values of the layer bound to an input, or None when no layer is bound to it."""
        self.refresh()
        row = next((row for row in self._bindings if row.input == input), None)
        return None if row is None else values(row.layer, row.source)

    def capture(self, layer, input, reference):
        import numpy as np
        self.refresh()
        binding = next((row for row in self._bindings if row.layer is layer and row.input == input), None)
        if binding is None or binding.reference != reference:
            raise ValueError('This layer has no editable binding to the requested retained input')
        data = values(layer, binding.source)
        if geometry(layer) != binding.geometry:
            raise ValueError('Layer calibration or axes changed; save an explicit converted resource instead')
        quiescent(data)
        if type(data) is not np.ndarray:
            return data.snapshot()
        frozen = data.copy(order='C')
        quiescent(data)
        if geometry(layer) != binding.geometry or layer.data is not data:
            raise ValueError('The layer changed while its array was being captured; inspect it before saving again')
        frozen.flags.writeable = False
        return frozen

    @contextmanager
    def staged_chunks(self, layer, input, grant, *, storage='files', base=None, budget=None):
        """Freeze to owned temporary storage before any transport callback can run.

        Each copy/read is bounded by the existing native chunk grant. This avoids a
        second whole-array allocation while preserving snapshot semantics during
        parent/worker exchanges. The ordinary editor's smaller in-memory snapshot
        path and its existing limits remain separate.
        """
        import numpy as np
        from .native_edits import chunk
        if storage not in ('files', 'memory'):
            raise ValueError('Choose the admitted scientific snapshot storage')
        self.refresh()
        binding = next((row for row in self._bindings if row.layer is layer and row.input == input), None)
        if binding is None or binding.reference != grant.reference:
            raise ValueError('This layer has no editable binding to the requested retained input')
        data = values(layer, binding.source)
        if (data.shape != grant.shape or data.dtype.str != grant.dtype or binding.source.axes != grant.axes):
            raise ValueError('The write grant does not match the declared native input')
        if geometry(layer) != binding.geometry:
            raise ValueError('Layer calibration or axes changed; save an explicit converted resource instead')
        def verify():
            if geometry(layer) != binding.geometry or layer.data is not data:
                raise ValueError('The layer changed during capture; inspect it before saving again')
        if type(data) is not np.ndarray:
            with data.staged_chunks(grant,verify,budget=budget or FreezeBudget()) as chunks:
                yield chunks
            return
        with snapshot_chunks(data,grant,verify,storage=storage,base=base,budget=budget) as chunks:
            yield chunks

    def close(self):
        self._bindings.clear()
        self.inputs.clear()
        self._sealed = True


@contextmanager
def snapshot_chunks(data,grant,verify,*,storage='files',base=None,budget=None):
    """Freeze a bounded array in one pass; callers independently validate its meaning/state.

    With ``base`` (16-byte prefixes of the accepted base's chunk digests, in grid order), a chunk whose
    frozen bytes hash to its base digest is yielded as ``Kept`` and never stored or sent; the parent
    accepts it only for the exact full digest. Every other chunk is frozen and yielded as values.
    Changed chunks frozen in memory count against ``budget``, shared by every layer of one save, so the
    save is refused before any transport as soon as they would exceed it.
    """
    import hashlib
    import numpy as np
    from .native_edits import Kept, chunk
    if storage not in ('files','memory') or type(data) is not np.ndarray:
        raise ValueError('Choose an owned native snapshot storage')
    if data.shape!=grant.shape or data.dtype.str!=grant.dtype or data.nbytes>MAX_BYTES:
        raise ValueError('The output does not match its bounded write grant')
    indices = list(product(*(range(n) for n in grant.grid)))
    if base is not None and (type(base) is not bytes or len(base) != 16*len(indices)):
        raise ValueError('The base chunk digests do not match this write grant')
    def region(index):
        return tuple(slice(i*c, min((i+1)*c, n)) for i,c,n in zip(index,grant.chunks,grant.shape))
    def kept(number, body):
        if base is None:
            return None
        digest = hashlib.sha256(body).digest()
        return Kept(digest.hex()) if digest[:16] == base[16*number:16*(number+1)] else None
    verify()
    quiescent(data)
    shared = budget if budget is not None else FreezeBudget()
    def memory_chunks():
        quiescent(data); verify()
        items = []
        for number, index in enumerate(indices):
            view = data[region(index)]
            if base is not None:
                same = kept(number, memoryview(np.ascontiguousarray(view)).cast('B'))
                if same is not None:
                    items.append((index, same))
                    continue
                shared.take(view.nbytes)  # counted before the copy: the budget bounds what is held
            block = np.array(view, order='C', copy=True)
            block.flags.writeable = False
            items.append((index, block))
        quiescent(data); verify()
        return iter(items)
    if storage == 'memory':
        yield memory_chunks()
        return
    # In production TMPDIR is the confined worker's owned work directory.
    # Named, bounded files remain visible to its disk monitor.
    with ExitStack() as cleanup:
        try:
            directory = cleanup.enter_context(tempfile.TemporaryDirectory(prefix='hooke-layer-snapshot-'))
            stream = _SnapshotFiles(directory)
            cleanup.callback(stream.close)
            claims = {}
            for number, index in enumerate(indices):
                body = data[region(index)].tobytes(order='C')
                same = kept(number, body)
                if same is not None:
                    claims[number] = same
                else:
                    stream.write(body)
            stream.close()
        except OSError:
            # No transport callback has run: safely use the original bounded
            # snapshot when temporary storage disappears after admission.
            cleanup.close()
            yield memory_chunks()
            return
        quiescent(data)
        verify()
        def read_chunks():
            for number, index in enumerate(indices):
                if number in claims:
                    yield index, claims[number]
                    continue
                shape, size = chunk(index, grant.shape, grant.dtype, grant.chunks, grant.grid)
                yield index, np.frombuffer(stream.read(size), dtype=grant.dtype).reshape(shape)
        # Read/transport errors must propagate. Never replay a partial export.
        yield read_chunks()
