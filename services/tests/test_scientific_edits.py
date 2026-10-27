"""Synthetic frame receipts and real native storage; no viewer/gesture qualification."""
import itertools

import numpy as np
import pytest

from env_worker import native_edits as sdk
from orchestrator.scientific_edits import ViewEdits
from orchestrator.scientific_projects import Conflict, InvalidProject, ProjectStore


@pytest.fixture
def scene(tmp_path):
    store = ProjectStore(tmp_path)
    original = store.create('s1', {'kind': 'scene', 'title': 'Editable labels', 'state': {'note': 'keep'},
                                  'resources': [], 'runtimes': []})
    data = np.arange(4 * 6, dtype='>u2').reshape(4, 6)
    writer = store.array_draft(original.id, 's1', component='array.labels', shape=data.shape,
                               dtype=data.dtype.str, axes=['y', 'x'], chunks=[2, 3],
                               scientific={'semantic': 'instance-labels'}, max_written_bytes=data.nbytes)
    for row, col in itertools.product(range(2), repeat=2):
        writer.write([row, col], data[row * 2:row * 2 + 2, col * 3:col * 3 + 3])
    reference = writer.finish()
    accepted = store.save(original.id, 's1', {**original.document, 'resources': [reference]}, expected_revision=1)
    return store, accepted, reference, data


def view(scene):
    store, owner, reference, _ = scene
    return ViewEdits(store, owner.id, owner.session, owner.revision, reference)


def display(owner):
    frame = owner.present(view='view-one', layer='labels', context={'axes': ['y', 'x'], 'camera': [0, 0, 1]})
    owner.rendered(frame)
    return frame


def values(store, owner, reference):
    source = store.array_source(owner.id, owner.session, owner.revision, reference)
    return source.read(tuple(slice(0, n) for n in source.shape))


def test_one_gesture_publishes_once_and_old_data_remains_exact(scene):
    store, original, reference, data = scene
    host = view(scene)
    frame = display(host)
    edit = host.begin(frame, max_written_bytes=12)
    patch = np.full((2, 3), 65000, dtype='>u2')
    edit.write([0, 1], patch)
    assert store.read(original.id, 's1') == original
    saved = edit.commit()
    assert saved.revision == 3 and saved.transaction == edit.identity
    assert saved.document['state']['note'] == 'keep'
    assert set(saved.document['state']) == {'note', 'hookeEdit'}
    assert saved.document['state']['hookeEdit']['input'] == reference
    assert saved.document['state']['hookeEdit']['output'] == saved.document['resources'][0]
    assert saved.document['runtimes'] == []
    expected = data.copy()
    expected[:2, 3:] = patch
    assert np.array_equal(values(store, saved, saved.document['resources'][0]), expected)
    assert np.array_equal(values(store, original, reference), data)
    assert edit.commit() == saved
    assert store.read(original.id, 's1') == saved
    with pytest.raises(Conflict, match='stale'):
        host.begin(frame, max_written_bytes=12)
    with pytest.raises(InvalidProject, match='ended'):
        edit.write([0, 1], patch)
    assert display(host).revision == saved.revision


def test_unrendered_replaced_and_other_view_frames_cannot_authorize_edits(scene):
    host = view(scene)
    first = host.present(view='view-one', layer='labels', context={})
    with pytest.raises(Conflict, match='displaying'):
        host.begin(first, max_written_bytes=12)
    second = display(host)
    with pytest.raises(Conflict, match='stale'):
        host.rendered(first)
    with pytest.raises(Conflict, match='stale'):
        host.begin(first, max_written_bytes=12)
    other = view(scene)
    with pytest.raises(Conflict, match='stale'):
        other.begin(second, max_written_bytes=12)


def test_frame_context_is_detached_and_bounded_before_admission(scene):
    host = view(scene)
    context = {'camera': [1, 2, 3]}
    frame = host.present(view='v1', layer='l1', context=context)
    context['camera'].clear()
    assert frame._context == b'{"camera":[1,2,3]}'
    with pytest.raises(InvalidProject, match='allowance'):
        host.present(view='v1', layer='l1', context={'large': 'x' * 17000})
    host.rendered(frame)
    edit = host.begin(frame, max_written_bytes=0)
    with pytest.raises(Conflict, match='current gesture'):
        display(host)
    with pytest.raises(Conflict, match='current gesture'):
        host.begin(frame, max_written_bytes=0)
    edit.cancel()


@pytest.mark.parametrize('event', ['invalidate', 'close'])
def test_focus_loss_or_disconnect_discards_pending_authority_not_saved_data(scene, event):
    store, original, reference, data = scene
    host = view(scene)
    frame = display(host)
    edit = host.begin(frame, max_written_bytes=12)
    edit.write([1, 0], np.full((2, 3), 7, dtype='>u2'))
    getattr(host, event)()
    with pytest.raises(InvalidProject, match='ended'):
        edit.commit()
    with pytest.raises((Conflict, InvalidProject)):
        host.begin(frame, max_written_bytes=12)
    assert store.read(original.id, 's1') == original
    assert np.array_equal(values(store, original, reference), data)
    independent = view(scene)
    assert independent.begin(display(independent), max_written_bytes=0).commit().revision == 3


def test_concurrent_views_keep_the_winning_edit_and_refuse_stale_chunks(scene):
    store, original, reference, data = scene
    one, two = view(scene), view(scene)
    first = one.begin(display(one), max_written_bytes=12)
    stale = two.begin(display(two), max_written_bytes=12)
    first.write([0, 0], np.full((2, 3), 111, dtype='>u2'))
    stale.write([1, 1], np.full((2, 3), 222, dtype='>u2'))
    saved = first.commit()
    with pytest.raises(Conflict, match='changed'):
        stale.commit()
    with pytest.raises(InvalidProject, match='ended'):
        stale.commit()
    assert store.read(original.id, 's1') == saved
    expected = data.copy()
    expected[:2, :3] = 111
    assert np.array_equal(values(store, saved, saved.document['resources'][0]), expected)
    with pytest.raises(Conflict, match='changed'):
        two.begin(display(two), max_written_bytes=12)


@pytest.mark.parametrize('after_publish', [False, True])
def test_failed_acknowledgement_retry_preserves_the_exact_edit(scene, monkeypatch, after_publish):
    store, original, reference, _ = scene
    host = view(scene)
    edit = host.begin(display(host), max_written_bytes=12)
    edit.write([0, 0], np.full((2, 3), 77, dtype='>u2'))
    save = store.save
    def failure(*args, **kw):
        if after_publish:
            save(*args, **kw)
        raise OSError('controlled acknowledgement failure')
    monkeypatch.setattr(store, 'save', failure)
    with pytest.raises(OSError, match='acknowledgement'):
        edit.commit()
    assert store.read(original.id, 's1').revision == (3 if after_publish else 2)
    with pytest.raises(InvalidProject, match='prepared'):
        edit.write([0, 0], np.full((2, 3), 88, dtype='>u2'))
    monkeypatch.setattr(store, 'save', save)
    saved = edit.commit()
    assert saved.revision == 3 and saved.transaction == edit.identity
    assert store.read(original.id, 's1') == saved
    assert values(store, saved, saved.document['resources'][0])[0, 0] == 77
    reopened = ProjectStore(store.root)
    assert reopened.save(original.id, 's1', saved.document, expected_revision=2,
                         transaction=edit.identity) == saved


def test_cancel_after_acceptance_cannot_undo_saved_work(scene):
    store, original, _, _ = scene
    host = view(scene)
    edit = host.begin(display(host), max_written_bytes=0)
    saved = edit.commit()
    edit.cancel()
    host.close()
    assert store.read(original.id, 's1') == saved
    assert edit.commit() == saved


def test_temporary_base_read_failure_can_retry_without_losing_staged_edit(scene, monkeypatch):
    store, original, _, _ = scene
    host = view(scene)
    edit = host.begin(display(host), max_written_bytes=12)
    edit.write([0, 0], np.full((2, 3), 91, dtype='>u2'))
    read = store.read
    def failure(*args, **kw):
        raise OSError('controlled base read failure')
    monkeypatch.setattr(store, 'read', failure)
    with pytest.raises(OSError, match='base read failure'):
        edit.commit()
    monkeypatch.setattr(store, 'read', read)
    assert store.read(original.id, 's1') == original
    saved = edit.commit()
    assert saved.revision == 3
    assert values(store, saved, saved.document['resources'][0])[0, 0] == 91


def test_failed_manifest_write_preserves_the_same_gesture_for_retry(scene, monkeypatch):
    from orchestrator import scientific_arrays
    store, original, _, _ = scene
    host = view(scene)
    edit = host.begin(display(host), max_written_bytes=0)
    put = scientific_arrays._put
    def failure(*args, **kw):
        raise OSError('controlled manifest write failure')
    monkeypatch.setattr(scientific_arrays, '_put', failure)
    with pytest.raises(OSError, match='manifest write failure'):
        edit.commit()
    with pytest.raises(OSError, match='manifest write failure'):
        edit.commit()
    monkeypatch.setattr(scientific_arrays, '_put', put)
    assert edit.retryable and store.read(original.id, 's1') == original
    assert edit.commit().revision == 3


def test_bad_or_oversized_chunk_ends_only_its_edit_and_correction_can_retry(scene):
    store, original, _, _ = scene
    host = view(scene)
    edit = host.begin(display(host), max_written_bytes=1)
    with pytest.raises(InvalidProject, match='allowance'):
        edit.write([0, 0], np.zeros((2, 3), dtype='>u2'))
    with pytest.raises(InvalidProject, match='ended'):
        edit.commit()
    assert store.read(original.id, 's1') == original
    corrected = host.begin(display(host), max_written_bytes=12)
    corrected.write([0, 0], np.zeros((2, 3), dtype='>u2'))
    assert corrected.commit().revision == 3


def test_host_cannot_open_another_session_or_unaccepted_component(scene):
    store, original, reference, _ = scene
    with pytest.raises(InvalidProject, match='session'):
        ViewEdits(store, original.id, 'other', original.revision, reference)
    with pytest.raises(InvalidProject, match='not accepted'):
        ViewEdits(store, original.id, 's1', original.revision, {**reference, 'revision': 'a' * 64})


def test_native_edit_protocol_retains_bytes_and_replays_commit(scene):
    store, original, reference, data = scene
    host = view(scene)
    edit = host.begin(display(host), max_written_bytes=24)
    worker = sdk.bind(edit.specification(), edit.answer)
    assert worker.reference == reference
    worker.reference['id'] = 'unrelated'
    assert worker.reference == reference
    worker.write([0, 0], np.full((2, 3), 60001, dtype='>u2'))
    worker.write([1, 1], np.full((2, 3), 65535, dtype='>u2'))
    receipt = worker.commit()
    assert receipt == worker.commit()
    assert receipt['projectRevision'] == original.revision + 1
    saved = store.read(original.id, 's1')
    expected = data.copy()
    expected[:2, :3], expected[2:, 3:] = 60001, 65535
    assert values(store, saved, receipt['reference']).tobytes() == expected.tobytes()


@pytest.mark.parametrize('change,size', [
    ({'token': 'forged'}, 12), ({'shape': [True, 3]}, 12), ({'shape': [200, 300]}, 12),
    ({'index': [0, False]}, 12), ({'index': [2, 0]}, 12), ({'dtype': '<u2'}, 12),
    ({'session': 'other'}, 12), ({}, 1 << 30), ({}, -1), ({}, True),
])
def test_malformed_chunk_refused_before_pipe_payload_is_read(scene, change, size):
    import io
    import json
    import struct
    from env_worker.server import read_message
    store, original, _, _ = scene
    host = view(scene)
    edit = host.begin(display(host), max_written_bytes=12)
    request = {'op': 'write-native-chunk', 'token': edit.identity, 'index': [0, 0],
               'shape': [2, 3], 'dtype': '>u2'} | change
    if type(size) is not int or size < 0:
        with pytest.raises(ValueError):
            edit.admit_message(request, size)
    else:
        header = json.dumps(request).encode()
        prefix = struct.pack('>I', len(header)) + header + struct.pack('>I', size)
        stream = io.BytesIO(prefix + b'payload-must-stay-unread')
        with pytest.raises(ValueError):
            read_message(stream, max_header=4096, max_payload=1 << 20, admit=edit.admit_message)
        assert stream.tell() == len(prefix)
    assert store.read(original.id, 's1') == original


def test_worker_cannot_replace_component_identity_or_commit_cancelled_edit(scene):
    host = view(scene)
    edit = host.begin(display(host), max_written_bytes=0)
    worker = sdk.bind(edit.specification(), edit.answer)
    with pytest.raises(InvalidProject, match='additional authority'):
        edit.answer({'op': 'commit-native-edit', 'token': edit.identity, 'actor': 'user'})
    with pytest.raises(InvalidProject):
        edit.answer({'op': 'commit-native-edit', 'token': edit.identity}, b'unexpected')
    edit.cancel()
    with pytest.raises(InvalidProject, match='ended'):
        worker.commit()


@pytest.mark.parametrize('change', [
    {'dtype': 'object'}, {'axes': ['x', 'x']}, {'chunks': [True, 3]}, {'chunks': [1, 0]},
    {'chunks': [5, 3]}, {'token': ''}, {'path': '/private'}, {'format': 'unknown'},
    {'reference': {'session': 's1', 'id': 'd1', 'revision': 1}},
    {'projectRevision': True}, {'projectRevision': 0}, {'projectRevision': 4096},
])
def test_invalid_worker_grant_cannot_issue_requests(scene, change):
    host = view(scene)
    edit = host.begin(display(host), max_written_bytes=0)
    with pytest.raises(ValueError):
        sdk.bind(edit.specification() | change, lambda *args: pytest.fail('unexpected request'))


@pytest.mark.parametrize('reply', [
    ({'ok': True, 'kind': sdk.FORMAT, 'index': [False, 0], 'bytes': 12}, b''),
    ({'ok': True, 'kind': sdk.FORMAT, 'index': [0, 0], 'bytes': 12}, b'extra'),
    ({'ok': 1, 'kind': sdk.FORMAT, 'index': [0, 0], 'bytes': 12}, b''),
])
def test_worker_rejects_forged_chunk_acknowledgement(scene, reply):
    host = view(scene)
    edit = host.begin(display(host), max_written_bytes=12)
    worker = sdk.bind(edit.specification(), lambda *args: reply)
    with pytest.raises(ValueError, match='acknowledgement'):
        worker.write([0, 0], np.zeros((2, 3), dtype='>u2'))


@pytest.mark.parametrize('revision', [1, 2, 4, True, 3.0])
def test_worker_rejects_completion_for_different_document_revision(scene, revision):
    host = view(scene)
    edit = host.begin(display(host), max_written_bytes=0)
    reply = {'ok': True, 'kind': sdk.FORMAT, 'reference': scene[2], 'projectRevision': revision}
    worker = sdk.bind(edit.specification(), lambda *args: (reply, b''))
    with pytest.raises(ValueError, match='acknowledgement'):
        worker.commit()


def test_worker_serializes_complete_exchanges_from_multiple_callback_threads(scene):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from tests.waiting import HANG_S
    host = view(scene)
    edit = host.begin(display(host), max_written_bytes=12)
    entered, release, contending = threading.Event(), threading.Event(), threading.Event()
    class ObservedLock:
        def __init__(self):
            self.lock = threading.RLock()
            self.entries = 0
        def __enter__(self):
            self.entries += 1
            if self.entries == 2:
                contending.set()
            self.lock.acquire()
        def __exit__(self, *args):
            self.lock.release()
    calls = []
    def request(header, payload):
        calls.append(header['op'])
        if len(calls) == 1:
            entered.set()
            assert release.wait(HANG_S)
        return edit.answer(header, payload)
    worker = sdk.bind(edit.specification(), request)
    object.__setattr__(worker, '_lock', ObservedLock())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker.write, [0, 0], np.full((2, 3), 42, dtype='>u2'))
        try:
            assert entered.wait(HANG_S)
            second = pool.submit(worker.commit)
            assert contending.wait(HANG_S)
            assert calls == ['write-native-chunk']
        finally:
            release.set()
        first.result(timeout=HANG_S)
        saved = second.result(timeout=HANG_S)
    assert saved['projectRevision'] == 3
    assert calls == ['write-native-chunk', 'commit-native-edit']


def test_actual_confined_worker_edits_via_owned_pipe_without_saved_paths(scene, tmp_path, monkeypatch):
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    from env_worker.server import read_message, write_message
    from orchestrator import owned_process, sandbox, worker_resources
    from tests.waiting import HANG_S
    if sys.platform != 'darwin' or not sandbox.available():
        pytest.skip('actual native write confinement requires macOS')
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    bootstrap, work = tmp_path / 'bootstrap', tmp_path / 'worker'
    bootstrap.mkdir()
    work.mkdir()
    services = Path(__file__).parents[1]
    for package, names in {'env_worker': ['server.py', 'native_edits.py', 'native_regions.py', 'region_reader.py'],
                           'gpu_worker': ['semantic_resources.py']}.items():
        folder = bootstrap / package
        folder.mkdir()
        (folder / '__init__.py').touch()
        for name in names:
            shutil.copy2(services / package / name, folder / name)
    child = bootstrap / 'edit.py'
    child.write_text('''import sys
from pathlib import Path
import numpy as np
from env_worker import native_edits
from env_worker.server import read_message, write_message
spec, payload = read_message(sys.stdin.buffer, max_header=8192, max_payload=0)
assert not payload
try:
    Path(sys.argv[1]).read_bytes()
except PermissionError:
    pass
else:
    raise AssertionError('worker could read the accepted Project directly')
def request(header, body):
    write_message(sys.stdout.buffer, header, body)
    return read_message(sys.stdin.buffer, max_header=8192, max_payload=0)
edit = native_edits.bind(spec, request)
edit.write([0, 1], np.full((2, 3), 65001, dtype='>u2'))
first = edit.commit()
assert edit.commit() == first
''')
    store, original, reference, data = scene
    host = view(scene)
    edit = host.begin(display(host), max_written_bytes=12)
    executable = Path(sys.executable).absolute()
    reads = [executable.parent.parent, sandbox.python_home(executable), bootstrap]
    env = sandbox.confine({'PATH': '/usr/bin:/bin', 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
                           'VECLIB_MAXIMUM_THREADS': '1'}, work)
    command = sandbox.wrap([str(executable), '-u', str(child), str(store.root / original.id / 'head.json')],
                           reads, [work], gpu=False, children=False)
    process = guard = None
    try:
        with (work / 'stderr.log').open('wb') as errors:
            process = subprocess.Popen(command, cwd=work, env=env, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=errors, start_new_session=True)
            guard = worker_resources.Guard(process, work).start()
            with owned_process.watching(process, HANG_S, 'native edit test worker'):
                write_message(process.stdin, edit.specification())
                for _ in range(3):
                    header, body = read_message(process.stdout, max_header=8192, max_payload=1 << 20,
                                                admit=edit.admit_message)
                    response, payload = edit.answer(header, body)
                    write_message(process.stdin, response, payload)
                process.stdin.close()
                process.wait(timeout=HANG_S)
            assert process.returncode == 0, (work / 'stderr.log').read_text()
            guard.check()
        saved = ProjectStore(store.root).read(original.id, 's1')
        expected = data.copy()
        expected[:2, 3:] = 65001
        assert values(store, saved, saved.document['resources'][0]).tobytes() == expected.tobytes()
        assert saved.revision == original.revision + 1
        assert values(store, original, reference).tobytes() == data.tobytes()
    finally:
        if guard is not None:
            guard.close()
        elif process is not None:
            owned_process.terminate(process)
        if process is not None:
            for stream in (process.stdin, process.stdout):
                stream.close()
        host.close()
