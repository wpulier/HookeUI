import os
from pathlib import Path
import subprocess
import shutil

import numpy as np
import pytest

from env_worker.scientific_host import ReviewSession, number, vector
from env_worker.server import read_message, write_message
from orchestrator import owned_process, sandbox, worker_resources
from orchestrator.native_regions import NativeRegionInputs
from tests.test_native_regions import source
from tests.waiting import HANG_S


@pytest.mark.parametrize('value', [True, None, '1', float('nan'), float('inf'), 2**53, 10**1000])
def test_host_coordinates_are_finite_exact_numbers(value):
    with pytest.raises(ValueError):
        number(value)


def test_host_vectors_have_exact_dimensions():
    assert vector([1, 2.5, 3], 3) == [1, 2.5, 3]
    for value in ([1, 2], [True, 2, 3], {}, None):
        with pytest.raises(ValueError):
            vector(value, 3)


def test_python_consumes_frame_even_when_the_cell_fails():
    import threading
    from types import SimpleNamespace
    from env_worker.scientific_console import Console
    host = object.__new__(ReviewSession)
    host._closed, host._thread, host._frame = False, threading.get_ident(), 'current'
    viewer, invalidations = SimpleNamespace(value=0), []
    host.console = Console(viewer)
    host.panel = SimpleNamespace(invalidate=lambda: invalidations.append(True))
    failed = host.python('current', 'viewer.value += 1\nraise ValueError("fix this")')
    assert failed['error'].startswith('ValueError: fix this\n') and host.dirty
    with pytest.raises(ValueError):
        host.python('current', 'viewer.value += 1')
    assert viewer.value == 1 and invalidations == [True]


def test_disconnect_failure_does_not_skip_scientific_resources():
    from types import SimpleNamespace
    closed = []
    def broken_disconnect(callback):
        raise ValueError('controlled disconnect failure')
    host = object.__new__(ReviewSession)
    host._closed = False
    host._frame = 'old'
    host._connections = [SimpleNamespace(disconnect=broken_disconnect)]
    host._layers = [object()]
    host._files = {'source': SimpleNamespace(close=lambda: closed.append('source-file'))}
    host.editable = host.view_bindings = None
    for name in ('console', 'panel', 'container', 'viewer', 'backend'):
        setattr(host, name, SimpleNamespace(close=lambda name=name: closed.append(name)))
    with pytest.raises(RuntimeError, match='could not close'):
        host.close()
    assert closed == ['console', 'panel', 'container', 'viewer', 'backend', 'source-file']
    assert host._frame is None and not host._connections and not host._layers
    host.close()


def test_actual_confined_scientific_view_and_controls(tmp_path, monkeypatch):
    location = os.environ.get('NAPARI_REFERENCE_PYTHON')
    if not location:
        pytest.skip('requires the prepared reference runtime; not browser or package qualification')
    assert sandbox.available()
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    executable = Path(location)
    probe = Path(__file__).with_name('scientific_host_probe.py')
    services = probe.parents[1]
    work = tmp_path / 'work'
    work.mkdir()
    bootstrap = tmp_path / 'bootstrap'
    from orchestrator.scientific_host import BOOTSTRAP
    for package, names in BOOTSTRAP.items():
        folder = bootstrap / package
        folder.mkdir(parents=True)
        (folder / '__init__.py').touch()
        for name in names:
            shutil.copy2(services / package / name, folder / name)
    values = np.arange(6 * 24 * 28, dtype='<u2').reshape(6, 24, 28)
    original = values.tobytes()
    reads = []
    from orchestrator.scientific_projects import ProjectStore
    from orchestrator.scientific_edits import ViewEdits
    from orchestrator.scientific_host import LayerStaging
    from orchestrator.native_regions import Source
    from tests.test_scientific_arrays import fill, accept, project
    store = ProjectStore(tmp_path / 'projects')
    document = project(store)
    draft = store.array_draft(document.id, 's1', component='array.volume', shape=values.shape,
        dtype=values.dtype.str, axes=['z', 'y', 'x'], chunks=[1, 12, 14],
        scientific={'semantic': 'intensity'}, max_written_bytes=values.nbytes)
    reference = fill(draft, values)
    accepted = accept(store, document, reference)
    native = store.array_source(document.id, 's1', accepted.revision, reference)
    def read(slices):
        reads.append(slices)
        return native.read(slices)
    grants = NativeRegionInputs({'volume': Source(native.shape, native.dtype, native.axes, native.descriptor, read)})
    edits = ViewEdits(store, document.id, 's1', accepted.revision, reference)
    displayed = edits.present(view='test-view', layer='volume', context={'evidence': 'synthetic parent frame'})
    edits.rendered(displayed)
    edit = edits.begin(displayed, max_written_bytes=values.nbytes)
    staging = LayerStaging(edit)

    command = sandbox.wrap([str(executable), '-I', '-u', str(probe), str(bootstrap)],
                           [executable.parent.parent, sandbox.python_home(executable), probe, bootstrap],
                           [work], gpu=True, children=False)
    environment = sandbox.confine({'PATH': '/usr/bin:/bin', 'QT_QPA_PLATFORM': 'offscreen', 'QT_API': 'pyside6',
                                    'NAPARI_ASYNC': '0', 'NAPARI_CONFIG': str(work / 'napari.yaml'),
                                    'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
                                    'VECLIB_MAXIMUM_THREADS': '1', 'NUMBA_NUM_THREADS': '1'}, work)
    process = guard = None
    try:
        with (work / 'stderr.log').open('wb') as errors:
            process = subprocess.Popen(command, cwd=work, env=environment, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=errors, start_new_session=True, close_fds=True)
            guard = worker_resources.Guard(process, work, worker_resources.Limits(memory_bytes=1 << 30,
                                                                                 work_entries=512)).start()
            with owned_process.watching(process, HANG_S, 'scientific host view'):
                write_message(process.stdin, {'inputs': grants.specifications(), 'edit': edit.specification()})
                while True:
                    try:
                        def admit(header, size):
                            if header.get('op') == 'write-native-chunk': staging.admit(header, size)
                            elif size: raise ValueError('Unexpected scientific probe payload')
                        header, body = read_message(process.stdout, max_header=128 << 10, max_payload=1 << 20, admit=admit)
                    except EOFError:
                        guard.check()
                        pytest.fail(f'worker exited {process.poll()}: ' + (work / 'stderr.log').read_text()[-8000:])
                    if header.get('op') == 'read-native-region':
                        reply, payload = grants.answer(header)
                        write_message(process.stdin, reply, payload)
                    elif header.get('op') == 'write-native-chunk':
                        reply, payload = staging.answer(header, body)
                        write_message(process.stdin, reply, payload)
                    else:
                        assert header == {'ok': True, 'frames': 6, 'nativeValuesUnchanged': True}
                        break
                process.wait(timeout=HANG_S)
            guard.close()
            guard.check()
        assert process.returncode == 0, (work / 'stderr.log').read_text()
        assert len(reads) == 1 and values.tobytes() == original
        staging.finish({'ok': True, 'staged': edit.identity}, b'')
        assert store.read(document.id, 's1') == accepted
        saved = edit.commit()
        result = store.array_source(document.id, 's1', saved.revision, saved.document['resources'][0])
        expected = values.copy(); expected[0, 0, 0] = 65535
        bounds = tuple(slice(0, n) for n in values.shape)
        assert np.array_equal(result.read(bounds), expected)
        assert np.array_equal(native.read(bounds), values)
    finally:
        grants.close()
        if guard is not None:
            guard.close()
        elif process is not None:
            owned_process.terminate(process)
        if process is not None:
            process.stdin.close()
            process.stdout.close()


@pytest.mark.parametrize('change', [{'layer':'missing','visible':False,'opacity':.4},
    {'layer':'one','visible':1,'opacity':.4}, {'layer':'one','visible':False,'opacity':True},
    {'layer':'one','visible':False,'opacity':1.1}, {'layer':'one','visible':False,'opacity':float('nan')}])
def test_invalid_layer_presentation_never_partially_mutates(change):
    import threading
    from types import SimpleNamespace
    first, second = SimpleNamespace(name='same',visible=True,opacity=1), SimpleNamespace(name='same',visible=True,opacity=1)
    host = object.__new__(ReviewSession)
    host._closed, host._thread, host._frame = False, threading.get_ident(), 'current'
    host._layers = [('one',first),('two',second)]
    host.viewer = SimpleNamespace(grid=SimpleNamespace(enabled=False))
    host.panel = SimpleNamespace(invalidate=lambda: None)
    with pytest.raises(ValueError): host.change({'frame':'current','operation':'layer-presentation','value':change})
    assert first.visible is second.visible is True and first.opacity == second.opacity == 1
    host.change({'frame':'current','operation':'layer-presentation','value':{'layer':'two','visible':False,'opacity':.4}})
    assert first.visible is True and first.opacity == 1
    assert second.visible is False and second.opacity == .4

@pytest.mark.parametrize('order', [None, {}, [], ['one'], ['one','one'], ['one','unknown'], [True,'two'], [['one'],'two']])
def test_layer_order_requires_exact_current_members_without_mutation(order):
    import threading
    from types import SimpleNamespace
    first, second = object(), object()
    class Layers(list):
        def move(self, source, target):
            if target in (source, source + 1):
                return False
            item = self.pop(source)
            self.insert(target if target < source else target-1, item)
    layers = Layers([first, second])
    host = object.__new__(ReviewSession)
    host._closed, host._thread, host._frame = False, threading.get_ident(), 'current'
    host._layers = [('one', first), ('two', second)]
    host.viewer = SimpleNamespace(layers=layers)
    host.panel = SimpleNamespace(invalidate=lambda: None)
    with pytest.raises(ValueError):
        host.change({'frame':'current','operation':'layer-order','value':order})
    assert layers == [first, second] and host._frame == 'current'
    host.change({'frame':'current','operation':'layer-order','value':['two','one']})
    assert layers == [second, first] and host._frame is None
    with pytest.raises(ValueError):
        host.change({'frame':'current','operation':'layer-order','value':['one','two']})
    assert layers == [second, first]


def test_multi_staging_rejects_cross_layer_authority_and_missing_chunks():
    from types import SimpleNamespace
    from orchestrator.scientific_host import LayersStaging, Failed, CaptureRefused
    class Edit:
        def __init__(self, token): self.token=token
        def specification(self):
            return {'format':'native-edit-v1','token':self.token,'shape':[1,1],'dtype':'|u1','axes':['y','x'],'chunks':[1,1]}
        def admit_message(self, header, size):
            if header.get('token')!=self.token: raise Failed('wrong token')
        def answer(self, header, payload): return {},b''
    staging=LayersStaging([Edit('first'),Edit('second')])
    header={'op':'write-native-chunk','index':[0,0],'token':'second'}
    with pytest.raises(Failed,match='token'): staging.admit(header,1)
    with pytest.raises(CaptureRefused): staging.refuse({'ok':False,'code':'NATIVE_CAPTURE_NOT_STARTED','error':'correct input'},b'')
    staging.answer({**header,'token':'first'},b'0')
    with pytest.raises(Failed): staging.refuse({'ok':False,'code':'NATIVE_CAPTURE_NOT_STARTED','error':'late refusal'},b'')
    with pytest.raises(Failed,match='incomplete'): staging.finish({'ok':True,'staged':['first','second']},b'')
    staging.answer(header,b'0')
    staging.finish({'ok':True,'staged':['first','second']},b'')
    with pytest.raises(Failed): staging.admit(header,1)
