"""Owned upstream worker evidence; synthetic data, no browser or clean-machine qualification."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import env_runner, generation_leases, scientific_host
from orchestrator.native_regions import NativeRegionInputs, Source
from tests.test_native_regions import source
from tests.waiting import HANG_S, until


def frame():
    return {'ok': True, 'frame': {'format': 'scientific-host-v1', 'frame': 'a' * 64,
            'width': 2, 'height': 3, 'pixelRatio': 1, 'derivedDisplay': True, 'privatePreview': True,
            'dirty': False, 'layers': [],
            'camera': {'center': [0, 0, 0], 'zoom': 1, 'angles': [0, 0, 0], 'perspective': 0,
                       'orientation': ['towards', 'down', 'right']},
            'dimensions': {'order': [0, 1], 'displayed': [0, 1], 'ndisplay': 2,
                           'steps': [0, 0], 'counts': [3, 2], 'labels': ['y', 'x'],
                           'point': [0, 0], 'ranges': [[0, 2, 1], [0, 1, 1]]},
            'controls': {'format': 'scientific-controls-v1', 'panel': 'b' * 32, 'revision': 1, 'controls': []}}}


@pytest.mark.parametrize('change', [
    {'frame': 'unknown'}, {'privatePreview': False}, {'derivedDisplay': False}, {'width': 3},
    {'pixelRatio': 2}, {'dirty': 0}, {'layers': [{'id': 'a' * 32}, {'id': 'a' * 32}]},
    {'camera': {'zoom': float('nan')}}, {'controls': {'format': 'scientific-controls-v1'}}
])
def test_malformed_worker_frame_cannot_be_presented(change):
    reply = frame()
    reply['frame'].update(change)
    with pytest.raises(scientific_host.Failed):
        scientific_host.checked_reply({'op': 'render', 'width': 2, 'height': 3}, reply, b'0' * 24)


def test_wrong_payload_and_pick_cannot_be_treated_as_scientific_frame():
    request = {'op': 'render', 'width': 2, 'height': 3}
    with pytest.raises(scientific_host.Failed):
        scientific_host.checked_reply(request, frame(), b'0' * 20)
    for header, body in [({'ok': True, 'position': [True, 1]}, b''),
                         ({'ok': True, 'position': [1, 2]}, b'unexpected'),
                         ({'ok': True, 'position': [1]}, b'')]:
        with pytest.raises(ValueError):
            scientific_host.checked_reply({'op': 'pick'}, header, body)


def fake_worker(grants, *, live=False, late_failure=False):
    worker = object.__new__(scientific_host.ReviewWorker)
    worker._lock, worker._stopped = threading.Lock(), threading.Event()
    worker._interrupt_lock = threading.Lock()
    worker._interrupt_fd = worker._python_token = None
    worker._closed, worker._errors, worker.grants = False, io.BytesIO(), grants
    process = SimpleNamespace(live=live, stdin=io.BytesIO(), stdout=io.BytesIO())
    process.poll = lambda: None if process.live else 0
    def check():
        if late_failure:
            raise RuntimeError('late resource guard failure')
    worker.process = process
    worker.guard = SimpleNamespace(close=lambda: None, check=check)
    return worker


def test_late_guard_failure_is_reported_after_terminal_resources_close():
    grants = NativeRegionInputs({'volume': source(np.ones((2, 3), dtype='<u2'), [])})
    worker = fake_worker(grants, late_failure=True)
    released, stack = [], ExitStack()
    stack.callback(lambda: released.append('reaped'))
    with pytest.raises(scientific_host.Failed, match='cleanup reported'):
        scientific_host.release_worker(worker, stack)
    assert released == ['reaped'] and worker.process.stdin.closed and worker.process.stdout.closed
    with pytest.raises(ValueError, match='closed'):
        grants.specifications()


def test_unreaped_worker_keeps_lease_and_work_until_cleanup_retry(monkeypatch):
    grants = NativeRegionInputs({'volume': source(np.ones((2, 3), dtype='<u2'), [])})
    worker = fake_worker(grants, live=True)
    monkeypatch.setattr(scientific_host.owned_process, 'terminate', lambda process: None)
    released, stack = [], ExitStack()
    stack.callback(lambda: released.append('runtime-and-private-files'))
    try:
        with pytest.raises(scientific_host.Failed, match='remain reserved'):
            scientific_host.release_worker(worker, stack)
        assert not released and scientific_host._retained[worker] is stack
        assert not worker.process.stdin.closed
        worker.process.live = False
        assert scientific_host.retry_cleanup() == []
        assert released == ['runtime-and-private-files'] and worker.process.stdin.closed
        assert worker not in scientific_host._retained
    finally:
        worker.process.live = False
        scientific_host.retry_cleanup()


def test_stop_revokes_grants_before_waiting_for_delayed_source_read():
    started, finish = threading.Event(), threading.Event()
    values = np.ones((2, 3), dtype='<u2')
    original = source(values, [])
    def delayed(slices):
        started.set()
        assert finish.wait(HANG_S)
        return values[slices]
    grants = NativeRegionInputs({'volume': Source(original.shape, original.dtype, original.axes,
                                                  original.descriptor, delayed)})
    token = grants.specifications()['volume']['token']
    worker = fake_worker(grants)
    def read():
        with worker._lock:
            return grants.answer({'op': 'read-native-region', 'token': token, 'region': [[0, 1], [0, 1]]})
    with ThreadPoolExecutor(max_workers=2) as threads:
        reading = threads.submit(read)
        assert started.wait(HANG_S)
        stopping = threads.submit(worker.close)
        try:
            until(worker._stopped.is_set)
            with pytest.raises(ValueError, match='closed'):
                grants.specifications()
            assert not stopping.done()
        finally:
            finish.set()
        with pytest.raises(ValueError, match='closed before'):
            reading.result(timeout=HANG_S)
        stopping.result(timeout=HANG_S)


def test_factory_import_cannot_select_an_installed_module(tmp_path):
    from env_worker.scientific_host_server import load_factory
    (tmp_path / 'json.py').write_text('raise AssertionError("must not run")')
    with pytest.raises(ValueError, match='conflicts'):
        load_factory(tmp_path, 'json:loads')


def test_factory_uses_exact_nested_entry(tmp_path, monkeypatch):
    from env_worker.scientific_host_server import load_factory
    import sys
    folder = tmp_path / 'private_host_fixture'
    folder.mkdir()
    (folder / '__init__.py').touch()
    (folder / 'tools.py').write_text('def open_host(context):\n    return context\n')
    try:
        factory = load_factory(tmp_path, 'private_host_fixture.tools:open_host')
        assert factory('test') == 'test'
        assert Path(sys.modules['private_host_fixture.tools'].__file__) == folder / 'tools.py'
    finally:
        sys.modules.pop('private_host_fixture.tools', None)
        sys.modules.pop('private_host_fixture', None)


@pytest.fixture
def actual_host(monkeypatch):
    location = os.environ.get('NAPARI_HOST_PREPARATION')
    if not location:
        pytest.skip('requires independently prepared private scientific bundle; not browser qualification')
    prepared = json.loads(Path(location).read_text())
    for name, value in prepared['environment'].items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    catalog.manifests.cache_clear()
    try:
        yield prepared
    finally:
        catalog.manifests.cache_clear()


def test_actual_prepared_host_reads_controls_reopens_and_reaps(actual_host):
    prepared = actual_host
    folder = Path(prepared['folder'])
    values = np.arange(6 * 24 * 28, dtype='<u2').reshape(6, 24, 28)
    before, reads = values.tobytes(), []
    owner = catalog.get('mine/host-review')
    assert owner is not None and env_runner.in_use(owner) is None
    for attempt in range(2):
        with scientific_host.review(prepared['key'], folder.name, {'volume': source(values, reads)}) as worker:
            with pytest.raises(generation_leases.Busy):
                with generation_leases.withdraw(folder):
                    pass
            reply, pixels = worker.request({'op': 'render', 'width': 256, 'height': 192})
            first = reply['frame']
            assert len(pixels) == 256 * 192 * 4 and len(set(pixels)) > 10
            assert first['layers'][0]['opacity'] == 1 and not first['dirty']
            button = next(c for c in first['controls']['controls'] if c['kind'] == 'button')
            changed, after = worker.request({'op': 'change', 'width': 256, 'height': 192,
                'request': {'frame': first['frame'], 'operation': 'controls', 'value': {
                    'panel': first['controls']['panel'], 'revision': first['controls']['revision'],
                    'control': button['id'], 'op': 'invoke'}}})
            assert changed['frame']['dirty'] and changed['frame']['layers'][0]['opacity'] == .4
            assert pixels != after
            assert env_runner.in_use(owner) is None
        assert worker.process.poll() is not None and not worker.work.exists()
        with generation_leases.withdraw(folder):
            pass
    assert values.tobytes() == before and len(reads) == 2
    assert env_runner.in_use(owner) is None


def control(frame, identity, op, **values):
    return {'op': 'change', 'width': 256, 'height': 192,
            'request': {'frame': frame['frame'], 'operation': 'controls', 'value': {
                'panel': frame['controls']['panel'], 'revision': frame['controls']['revision'],
                'control': identity, 'op': op, **values}}}


def test_actual_python_uses_same_viewer_keeps_variables_and_private_arrays(actual_host):
    prepared = actual_host
    values = np.arange(6 * 24 * 28, dtype='<u2').reshape(6, 24, 28)
    original = values.tobytes()
    def execute(worker, code):
        shown, _ = worker.request({'op': 'render', 'width': 256, 'height': 192})
        header, body = worker.request({'op': 'python', 'frame': shown['frame']['frame'], 'source': code})
        assert body == b''
        return header['console']
    with scientific_host.review(prepared['key'], Path(prepared['folder']).name, {'volume': source(values, [])}) as worker:
        first = execute(worker, 'saved = 7\nviewer.layers[0].opacity = .25\nprint(int(viewer.layers[0].data.sum()))')
        assert not first['error'] and first['stdout'].strip() == str(int(values.sum()))
        shown, _ = worker.request({'op': 'render', 'width': 256, 'height': 192})
        assert shown['frame']['dirty'] and shown['frame']['layers'][0]['opacity'] == .25
        assert execute(worker, 'saved * 3')['result'] == '21'
        error = execute(worker, 'raise ValueError("inspect private work")')['error']
        assert error.startswith('ValueError: inspect private work\n')
        assert '<Hooke Python 3>, line 1' in error
        assert execute(worker, 'saved + 1')['result'] == '8'
        assert not execute(worker, 'viewer.layers[0].data = viewer.layers[0].data.copy()\nviewer.layers[0].data[0,0,0] = 65535\nviewer.layers[0].refresh()')['error']
        assert execute(worker, 'int(viewer.layers[0].data[0,0,0])')['result'] == '65535'
        assert not execute(worker, 'def divide(value):\n    return saved / value')['error']
        error = execute(worker, 'divide(0)')['error']
        assert '<Hooke Python 7>, line 2, in divide' in error
        assert 'return saved / value' in error and 'divide(0)' in error
        syntax = execute(worker, 'first = 3\nsecond =')['error']
        assert 'SyntaxError:' in syntax and '<Hooke Python 9>, line 2' in syntax
        unusual = execute(worker, 'class Unusual(Exception):\n    @property\n    def __class__(self):\n        raise RuntimeError("class lookup must not run")\nraise Unusual("original error")')['error']
        assert unusual.startswith('Unusual: original error')
        assert execute(worker, 'saved + 2')['result'] == '9'
        shown, _ = worker.request({'op':'render', 'width':256, 'height':192})
        frame_token = shown['frame']['frame']
        result, body = worker.request({'op':'python-assist', 'mode':'complete', 'expression':'viewer.add_im'})
        assert not body and 'add_image' in result['assistance']['matches']
        result, _ = worker.request({'op':'python-assist', 'mode':'help', 'expression':'viewer.add_image'})
        assert 'image' in result['assistance']['text'].lower()
        result, _ = worker.request({'op':'python', 'frame':frame_token, 'source':'saved'})
        assert result['console']['result'] == '7', 'lookup must not consume the rendered-frame authority'
    assert values.tobytes() == original
    assert worker.process.poll() is not None and not worker.work.exists()
    with scientific_host.review(prepared['key'], Path(prepared['folder']).name, {'volume': source(values, [])}) as restarted:
        assert execute(restarted, 'saved')['error'].startswith('NameError:')
        assert execute(restarted, 'int(viewer.layers[0].data[0,0,0])')['result'] == '0'


def test_actual_resource_python_runs_before_any_render_and_keeps_owned_input(actual_host):
    prepared = actual_host
    values = np.arange(6 * 24 * 28, dtype='<u2').reshape(6,24,28)
    original = values.tobytes()
    with scientific_host.review(prepared['key'], Path(prepared['folder']).name, {'volume':source(values, [])}) as worker:
        result, body = worker.request({'op':'python-resources','source':'total = int(viewer.layers[0].data.sum())\ntotal'})
        assert body == b'' and result['console']['result'] == str(int(values.sum()))
        result, _ = worker.request({'op':'python-resources','source':'total + 1'})
        assert result['console']['result'] == str(int(values.sum()) + 1)
        result, _ = worker.request({'op':'python-resources',
            'source':"int(inputs['volume'].read([[0,1],[0,1],[0,2]])[0,0,1])"})
        assert result['console']['result'] == '1' and not result['console']['error']
    assert values.tobytes() == original
    assert worker.process.poll() is not None and not worker.work.exists()


def test_actual_python_replayed_frame_cannot_execute_twice(actual_host):
    prepared = actual_host
    values = np.ones((6, 24, 28), dtype='<u2')
    with scientific_host.review(prepared['key'], Path(prepared['folder']).name, {'volume': source(values, [])}) as worker:
        shown, _ = worker.request({'op': 'render', 'width': 256, 'height': 192})
        request = {'op': 'python', 'frame': shown['frame']['frame'],
                   'source': 'from pathlib import Path\np = Path("once.txt")\np.write_text(p.read_text() + "x" if p.exists() else "x")'}
        result, _ = worker.request(request)
        assert not result['console']['error'] and (worker.work / 'once.txt').read_text() == 'x'
        with pytest.raises((RuntimeError, EOFError, ValueError, OSError)):
            worker.request(request)
        assert (worker.work / 'once.txt').read_text() == 'x'
    assert worker.process.poll() is not None and not worker.work.exists()


def test_actual_python_hang_cancels_only_owned_worker(actual_host, monkeypatch):
    prepared = actual_host
    values = np.arange(6 * 24 * 28, dtype='<u2').reshape(6, 24, 28)
    with ExitStack() as stack:
        good = stack.enter_context(scientific_host.review(prepared['key'], Path(prepared['folder']).name,
                                                        {'volume': source(values, [])}))
        stuck = stack.enter_context(scientific_host.review(prepared['key'], Path(prepared['folder']).name,
                                                         {'volume': source(values, [])}))
        before, pixels = good.request({'op': 'render', 'width': 256, 'height': 192})
        frame, _ = stuck.request({'op': 'render', 'width': 256, 'height': 192})
        with monkeypatch.context() as patch:
            patch.setattr(scientific_host, 'CALL_SECONDS', 1)
            with pytest.raises((RuntimeError, EOFError, OSError)):
                stuck.request({'op': 'python', 'frame': frame['frame']['frame'], 'source': 'while True: pass'})
        assert stuck.process.poll() is not None
        after, retained = good.request({'op': 'render', 'width': 256, 'height': 192})
        assert retained == pixels and after['frame']['layers'] == before['frame']['layers']


@pytest.mark.parametrize('mode', ['crash', 'hang', 'oversized'])
def test_actual_failed_worker_preserves_unrelated_view_and_allows_retry(actual_host, mode):
    prepared, values = actual_host, np.arange(6 * 24 * 28, dtype='<u2').reshape(6, 24, 28)
    folder = Path(prepared['folder'])
    def review():
        return scientific_host.review(prepared['key'], folder.name, {'volume': source(values, [])})
    baseline = values.tobytes()
    with review() as good:
        initial, pixels = good.request({'op': 'render', 'width': 256, 'height': 192})
        with review() as bad:
            first, _ = bad.request({'op': 'render', 'width': 256, 'height': 192})
            choice = next(c for c in first['frame']['controls']['controls'] if c['kind'] == 'choice')
            value = next(c['id'] for c in choice['choices'] if c['label'] == mode)
            selected, _ = bad.request(control(first['frame'], choice['id'], 'set', value=value))
            current = selected['frame']
            button = next(c for c in current['controls']['controls'] if c['kind'] == 'button')
            request = control(current, button['id'], 'invoke')
            if mode == 'hang':
                with ThreadPoolExecutor(max_workers=1) as threads:
                    pending = threads.submit(bad.request, request)
                    until(lambda: (bad.work / 'callback-started').exists(), what='owned callback starts')
                    bad.close()
                    with pytest.raises((EOFError, ValueError, RuntimeError, OSError)):
                        pending.result(timeout=HANG_S)
            else:
                with pytest.raises((EOFError, ValueError, RuntimeError)):
                    bad.request(request)
        assert bad.process.poll() is not None and not bad.work.exists()
        retained, same = good.request({'op': 'render', 'width': 256, 'height': 192})
        assert same == pixels and retained['frame']['layers'][0]['id'] == initial['frame']['layers'][0]['id']
        with review() as corrected:
            retry, _ = corrected.request({'op': 'render', 'width': 256, 'height': 192})
            assert not retry['frame']['dirty']
    assert values.tobytes() == baseline
    with generation_leases.withdraw(folder):
        pass


def test_actual_host_denies_private_files_runtime_writes_network_and_parent_signal(actual_host, monkeypatch):
    prepared, values = actual_host, np.ones((6, 24, 28), dtype='<u2')
    folder = Path(prepared['folder'])
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'synthetic-must-not-inherit')
    forbidden = folder / 'forbidden-write'
    assert not forbidden.exists()
    try:
        with scientific_host.review(prepared['key'], folder.name, {'volume': source(values, [])}) as worker:
            secret = worker.work.parent / 'private-canary'
            secret.write_bytes(b'private-test-canary')
            first, _ = worker.request({'op': 'render', 'width': 256, 'height': 192})
            choice = next(c for c in first['frame']['controls']['controls'] if c['kind'] == 'choice')
            value = next(c['id'] for c in choice['choices'] if c['label'] == 'forbidden')
            selected, _ = worker.request(control(first['frame'], choice['id'], 'set', value=value))
            current = selected['frame']
            button = next(c for c in current['controls']['controls'] if c['kind'] == 'button')
            result, _ = worker.request(control(current, button['id'], 'invoke'))
            assert result['frame']['layers'][0]['opacity'] == .4
            assert secret.read_bytes() == b'private-test-canary' and not forbidden.exists()
    finally:
        forbidden.unlink(missing_ok=True)


def test_actual_label_refusal_returns_a_bounded_preview_and_worker_stays_usable(actual_host):
    prepared=actual_host
    values=np.ones((6,24,28),dtype='<u2')
    with scientific_host.review(prepared['key'],Path(prepared['folder']).name,{'volume':source(values,[])}) as worker:
        first,_=worker.request({'op':'render','width':256,'height':192})
        reply,pixels=worker.request({'op':'labels','request':'gesture-1234567890123456',
            'frame':first['frame']['frame'],'layer':first['frame']['layers'][0]['id'],
            'width':256,'height':192,'action':'undo','points':[]})
        assert reply['edit']['state']=='refused' and not reply['edit']['changed']
        assert len(pixels)==256*192*4
        assert worker.request({'op':'render','width':256,'height':192})[0]['frame']['privatePreview']
    assert worker.process.poll() is not None and not worker.work.exists()


def test_actual_resource_save_after_python_does_not_require_rerender(tmp_path,monkeypatch):
    location=os.environ.get('NAPARI_EDITABLE_HOST_PREPARATION')
    if not location:
        pytest.skip('requires independently prepared editable napari integration')
    prepared=json.loads(Path(location).read_text())
    for name,value in prepared['environment'].items(): monkeypatch.setenv(name,value)
    monkeypatch.delenv('MICROMIND_SANDBOX_READ',raising=False)
    catalog.manifests.cache_clear()
    from orchestrator.scientific_projects import ProjectStore
    from orchestrator.scientific_edits import ResourceEdits
    from tests.test_scientific_arrays import fill
    store=ProjectStore(tmp_path/'retained')
    original=np.arange(6*24*28,dtype='<u2').reshape(6,24,28)
    saved=store.create('s1',{'kind':'scene','title':'Resource save','state':{},'resources':[],'runtimes':[]})
    draft=store.array_draft(saved.id,'s1',component='array.labels',shape=original.shape,dtype='<u2',
        axes=['z','y','x'],chunks=[1,12,14],scientific={'semantic':'instance-labels'},max_written_bytes=original.nbytes)
    ref=fill(draft,original)
    saved=store.save(saved.id,'s1',{**saved.document,'resources':[ref]},expected_revision=1)
    owner=ResourceEdits(store,saved.id,'s1',saved.revision,ref,layer='labels')
    try:
        with scientific_host.review(prepared['key'],Path(prepared['folder']).name,
                                    {'labels':store.array_source(saved.id,'s1',saved.revision,ref)}) as worker:
            shown,_=worker.request({'op':'layer-inventory'})
            layer=next(row for row in shown['inventory']['layers'] if row.get('editableInput')=='labels')
            result,_=worker.request({'op':'python-resources','source':
                "viewer.layers[0].data.flat[3] = 65000\nviewer.layers[0].scale = (2,2,2)\nkeep = 'unrelated private work'\nviewer.add_points([[1,2,3]], name='Unsaved points')"})
            assert not result['console']['error']
            edit=owner.begin(max_written_bytes=original.nbytes)
            with pytest.raises(scientific_host.CaptureRefused,match='calibration'):
                worker.stage_resource(layer['id'],'labels',edit)
            assert worker.process.poll() is None and store.read(saved.id,'s1')==saved
            result,_=worker.request({'op':'python-resources','source':
                "print(keep)\nprint(viewer.layers['Unsaved points'].data.tolist())\nviewer.layers[0].scale = (1,1,1)"})
            assert result['console']['stdout'] == 'unrelated private work\n[[1, 2, 3]]\n'
            owner.close()
            owner=ResourceEdits(store,saved.id,'s1',saved.revision,ref,layer='labels')
            edit=owner.begin(max_written_bytes=original.nbytes)
            worker.stage_resource(layer['id'],'labels',edit)
            assert store.read(saved.id,'s1')==saved
            accepted=edit.commit()
            shown,_=worker.request({'op':'render','width':256,'height':192})
            from tests.test_scientific_view_preferences import CASES as VIEW_CASES
            with pytest.raises(scientific_host.RestoreRefused):
                worker.request({'op':'change','width':256,'height':192,'request':{
                    'frame':shown['frame']['frame'],'operation':'restore-view','value':VIEW_CASES['valid']}})
            assert worker.process.poll() is None
            result,_=worker.request({'op':'python-resources','source':'print(keep)'})
            assert result['console']['stdout']=='unrelated private work\n'
        source=store.array_source(saved.id,'s1',accepted.revision,accepted.document['resources'][0])
        actual=source.read(tuple(slice(0,n) for n in original.shape))
        expected=original.copy();expected.flat[3]=65000
        assert actual.tobytes()==expected.tobytes()
        assert accepted.document['state']['hookeEdit']['frame'] is None
        before=store.array_source(saved.id,'s1',saved.revision,ref).read(tuple(slice(0,n) for n in original.shape))
        assert before.tobytes()==original.tobytes()
        assert worker.process.poll() is not None and not worker.work.exists()
    finally:
        owner.close();catalog.manifests.cache_clear()


def test_actual_multi_array_capture_refusal_retry_and_reopen(tmp_path,monkeypatch):
    location=os.environ.get('NAPARI_MULTI_EDITABLE_HOST_PREPARATION')
    if not location:
        pytest.skip('requires independently prepared multi-layer napari integration')
    prepared=json.loads(Path(location).read_text())
    for name,value in prepared['environment'].items():monkeypatch.setenv(name,value)
    monkeypatch.delenv('MICROMIND_SANDBOX_READ',raising=False)
    catalog.manifests.cache_clear()
    from orchestrator.scientific_projects import ProjectStore
    from orchestrator.scientific_group_save import LayersSave
    from orchestrator.scientific_saves import SaveNotStarted
    from tests.test_scientific_arrays import fill
    store=ProjectStore(tmp_path/'retained')
    saved=store.create('s1',{'kind':'scene','title':'Two layers','state':{},'resources':[],'runtimes':[]})
    originals={name:(np.arange(6*24*28,dtype='<u2')+offset).astype('<u2').reshape(6,24,28)
               for name,offset in [('first',0),('second',5000)]}
    refs={}
    for name,data in originals.items():
        writer=store.array_draft(saved.id,'s1',component='array.'+name,shape=data.shape,dtype=data.dtype.str,
            axes=['z','y','x'],chunks=[1,12,14],scientific={'semantic':'instance-labels'},max_written_bytes=data.nbytes)
        refs[name]=fill(writer,data)
    document=saved.document;document['resources']=list(refs.values())
    document['state']['scientificView']={'runtime':{},'inputs':refs}
    saved=store.save(saved.id,'s1',document,expected_revision=1)
    sources={name:store.array_source(saved.id,'s1',saved.revision,ref) for name,ref in refs.items()}
    try:
        with scientific_host.review(prepared['key'],Path(prepared['folder']).name,sources) as worker:
            inventory,_=worker.request({'op':'layer-inventory'})
            layers=[{'layer':row['id'],'input':row['editableInput']} for row in inventory['inventory']['layers']]
            reply,_=worker.request({'op':'python-resources','source':
                "viewer.layers['first'].data.flat[0] = 60001\nviewer.layers['second'].data.flat[0] = 60002\nviewer.layers['second'].scale = (2,2,2)\nkeep = 1234"})
            assert not reply['console']['error']
            rejected=LayersSave(store,saved,'save-1234567890123456',layers)
            with pytest.raises(SaveNotStarted,match='calibration'): rejected.capture(worker)
            assert worker.process.poll() is None and store.read(saved.id,'s1')==saved
            reply,_=worker.request({'op':'python-resources','source':"print(keep)\nviewer.layers['second'].scale=(1,1,1)"})
            assert reply['console']['stdout']=='1234\n'
            save=LayersSave(store,saved,'save-2234567890123456',layers)
            save.capture(worker);save.publish();save.close()
            accepted=store.read(saved.id,'s1')
            assert accepted.revision==saved.revision+1
            for name,ref in accepted.document['state']['scientificView']['inputs'].items():
                actual=store.array_source(saved.id,'s1',accepted.revision,ref).read(tuple(slice(0,n) for n in originals[name].shape))
                expected=originals[name].copy();expected.flat[0]=60001 if name=='first' else 60002
                assert actual.tobytes()==expected.tobytes()
                before=sources[name].read(tuple(slice(0,n) for n in originals[name].shape))
                assert before.tobytes()==originals[name].tobytes()
        assert worker.process.poll() is not None and not worker.work.exists()
        sources={name:store.array_source(saved.id,'s1',accepted.revision,ref)
                 for name,ref in accepted.document['state']['scientificView']['inputs'].items()}
        with scientific_host.review(prepared['key'],Path(prepared['folder']).name,sources) as reopened:
            reply,_=reopened.request({'op':'python-resources','source':"print([int(layer.data.flat[0]) for layer in viewer.layers])"})
            assert reply['console']['stdout']=='[60001, 60002]\n'
        assert reopened.process.poll() is not None
    finally:catalog.manifests.cache_clear()


def test_actual_new_output_save_and_data_only_reopen(tmp_path,monkeypatch):
    location=os.environ.get('NAPARI_MULTI_EDITABLE_HOST_PREPARATION')
    if not location:pytest.skip('requires independently prepared multi-layer napari integration')
    prepared=json.loads(Path(location).read_text())
    for name,value in prepared['environment'].items():monkeypatch.setenv(name,value)
    monkeypatch.delenv('MICROMIND_SANDBOX_READ',raising=False);catalog.manifests.cache_clear()
    from orchestrator.scientific_projects import ProjectStore
    from orchestrator.scientific_outputs import OutputSave
    from orchestrator.scientific_project_review import review
    from tests.test_scientific_arrays import fill
    store=ProjectStore(tmp_path/'retained')
    bundle,operation=prepared['key'].split('#')
    runtime={'bundle':bundle,'generation':Path(prepared['folder']).name,'operation':operation}
    saved=store.create('s1',{'kind':'scene','title':'Source','state':{},'resources':[],'runtimes':[runtime]})
    data=np.arange(3*8*9,dtype='<u2').reshape(3,8,9)
    draft=store.array_draft(saved.id,'s1',component='array.source',shape=data.shape,dtype=data.dtype.str,axes=['z','y','x'],
        chunks=[1,8,9],scientific={'semantic':'instance-labels'},max_written_bytes=data.nbytes)
    reference=fill(draft,data);document=saved.document;document['resources']=[reference]
    document['state']['scientificView']={'runtime':runtime,'inputs':{'source':reference}}
    saved=store.save(saved.id,'s1',document,expected_revision=1)
    try:
        with review(store,saved.id,'s1',saved.revision) as (_,worker):
            result,_=worker.request({'op':'python-resources','source':
                "import numpy as np\nviewer.add_labels(np.arange(216,dtype=np.uint16).reshape(3,8,9)+1000,name='Derived objects',axis_labels=('z','y','x'),scale=(2,3,4),translate=(5,6,7),units=('um','um','um'))\nkeep_output = 314"})
            assert not result['console']['error']
            inventory,_=worker.request({'op':'layer-inventory'})
            layer=next(row for row in inventory['inventory']['layers'] if row['name']=='Derived objects')
            assert 'editableInput' not in layer
            save=OutputSave(store,saved,'new-output-123456789','%s'%layer['id'],{'semantic':'instance-labels','axes':['z','y','x']})
            save.capture(worker);save.publish();output=store.read(save.target,'s1');save.close()
            result,_=worker.request({'op':'python-resources','source':'print(keep_output)'})
            assert result['console']['stdout']=='314\n'
        assert worker.process.poll() is not None
        with review(store,output.id,'s1',1) as (_,reopened):
            result,_=reopened.request({'op':'python-resources','source':
                "print([(x.name, x.data.dtype.str, list(x.data.shape), int(x.data.flat[0]), int(x.data.flat[-1])) for x in viewer.layers])\nprint(tuple(float(v) for v in viewer.layers[0].data_to_world((1,2,3))))"})
            assert not result['console']['error']
            assert "[('Derived objects', '<u2', [3, 8, 9], 1000, 1215)]" in result['console']['stdout']
            assert '(7.0, 12.0, 19.0)' in result['console']['stdout']
            inventory,_=reopened.request({'op':'layer-inventory'})
            assert inventory['inventory']['layers'][0]['editableInput']=='output'
        assert reopened.process.poll() is not None
        assert store.read(saved.id,'s1')==saved
        source=store.array_source(output.id,'s1',1,output.document['resources'][0])
        assert np.array_equal(source.read(tuple(slice(0,n) for n in data.shape)),data+1000)
    finally:catalog.manifests.cache_clear()


def test_actual_python_interrupt_preserves_namespace_and_unrelated_worker(actual_host):
    prepared=actual_host
    values=np.arange(6*24*28,dtype='<u2').reshape(6,24,28); before=values.tobytes()
    with scientific_host.review(prepared['key'],Path(prepared['folder']).name,{'volume':source(values,[])}) as worker:
        with scientific_host.review(prepared['key'],Path(prepared['folder']).name,{'volume':source(values,[])}) as unrelated:
            worker.request({'op':'python-resources','source':'kept = 29'})
            with ThreadPoolExecutor(max_workers=1) as pool:
                running=pool.submit(worker.request,{'op':'python-resources','source':'while True:\n    pass'})
                until(lambda: worker._python_ready)
                assert worker.interrupt_python() is True
                result,pixels=running.result(timeout=HANG_S)
            assert not pixels and result['console']['error'].startswith('KeyboardInterrupt:')
            assert worker.process.poll() is None
            assert worker.request({'op':'python-resources','source':'kept + 1'})[0]['console']['result']=='30'
            assert worker.interrupt_python() is False
            assert unrelated.request({'op':'python-resources','source':'7*6'})[0]['console']['result']=='42'
    assert values.tobytes()==before
    assert worker.process.poll() is not None and unrelated.process.poll() is not None
    assert not worker.work.exists() and not unrelated.work.exists()


def test_actual_python_interrupt_during_native_read_keeps_protocol_synchronized(actual_host):
    from dataclasses import replace
    prepared=actual_host;values=np.arange(6*24*28,dtype='<u2').reshape(6,24,28)
    base=source(values,[]); entered=threading.Event();release=threading.Event();block=threading.Event()
    def read(slices):
        if block.is_set():
            entered.set()
            assert release.wait(HANG_S)
        return base.read(slices)
    with scientific_host.review(prepared['key'],Path(prepared['folder']).name,{'volume':replace(base,read=read)}) as worker:
        block.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            running=pool.submit(worker.request,{'op':'python-resources','source':"kept = 41\ninputs['volume'].read([[0,1],[0,2],[0,3]])\nkept = 99"})
            try:
                assert entered.wait(HANG_S)
                assert worker.interrupt_python()
            finally:
                release.set()
            result,_=running.result(timeout=HANG_S)
        assert result['console']['error'].startswith('KeyboardInterrupt:')
        assert worker.request({'op':'python-resources','source':"print(kept)\ninputs['volume'].read([[0,1],[0,2],[0,3]]).shape"})[0]['console']['stdout']=='41\n'
        assert worker.request({'op':'layer-inventory'})[0]['inventory']['layers']
    assert worker.process.poll() is not None and not worker.work.exists()


def test_sequence_failure_reports_bounded_cause_and_closes_owned_inputs(monkeypatch):
    from contextlib import nullcontext
    from env_worker.server import write_message
    grants = NativeRegionInputs({'volume': source(np.ones((2,3),dtype='<u2'),[])})
    worker = fake_worker(grants)
    worker._allowed = lambda: None
    closed = []
    worker.guard.close = lambda: closed.append(True)
    monkeypatch.setattr(scientific_host.owned_process, 'watching', lambda *a, **kw: nullcontext())
    write_message(worker.process.stdout, {'ok': False, 'code': 'SCIENTIFIC_SEQUENCE_FAILED',
                                         'error': 'ValueError: keyframe dimensions disagree'})
    worker.process.stdout.seek(0)
    with pytest.raises(scientific_host.Failed, match='keyframe dimensions disagree'):
        worker._exchange({'op':'sequence-render','index':1,'width':2,'height':3})
    assert worker._stopped.is_set() and closed
    with pytest.raises(ValueError, match='closed'): grants.specifications()


def test_server_reports_each_startup_step_before_ready(tmp_path, monkeypatch):
    """A slow first open is said step by step (L6): the integration's code, the viewer, its panel, then ready."""
    import sys
    from env_worker import scientific_host_server as server
    from env_worker.server import read_message, write_message
    folder = tmp_path / 'startup_fixture'
    folder.mkdir()
    (folder / '__init__.py').write_text('')
    (folder / 'tools.py').write_text('def open_host(context):\n    return None\n')
    steps = []

    class Session:
        native_panel = tool_document = None

        def __init__(self, factory, inputs, *, progress, **_):
            progress('viewer')
            progress('tool')
            factory(None)
            with pytest.raises(ValueError):
                progress('compiling')  # only declared steps cross the protocol

        def close(self):
            steps.append('closed')

    monkeypatch.setattr(server, 'ReviewSession', Session)
    monkeypatch.setattr(server.native_regions, 'bind', lambda *a, **k: {})
    incoming = io.BytesIO()
    write_message(incoming, {'op': 'open', 'inputs': {}})
    write_message(incoming, {'op': 'close'})
    incoming.seek(0)
    output = io.BytesIO()
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=incoming))
    monkeypatch.setattr(sys, 'stdout', SimpleNamespace(buffer=output))
    try:
        server.main(tmp_path, 'startup_fixture.tools:open_host', read_fd)
    finally:
        os.close(write_fd)
        for name in ('startup_fixture.tools', 'startup_fixture'):
            sys.modules.pop(name, None)
    output.seek(0)
    replies = [read_message(output)[0] for _ in range(5)]
    assert replies == [{'op': 'starting', 'step': 'code'}, {'op': 'starting', 'step': 'viewer'},
                       {'op': 'starting', 'step': 'tool'}, {'ok': True, 'ready': 'startup_fixture.tools:open_host'},
                       {'ok': True, 'closed': True}]
    assert steps == ['closed']


def startup_worker(monkeypatch, *messages):
    from contextlib import nullcontext
    from env_worker.server import write_message
    worker = fake_worker(NativeRegionInputs({'volume': source(np.ones((2, 3), dtype='<u2'), [])}))
    worker._allowed = lambda: None
    worker._step, heard = -1, []
    worker._starting = heard.append
    monkeypatch.setattr(scientific_host.owned_process, 'watching', lambda *a, **kw: nullcontext())
    for message in messages:
        write_message(worker.process.stdout, message)
    worker.process.stdout.seek(0)
    return worker, heard


def test_parent_hears_each_startup_step_in_order_then_ready(monkeypatch):
    worker, heard = startup_worker(monkeypatch, {'op': 'starting', 'step': 'code'}, {'op': 'starting', 'step': 'viewer'},
                                   {'op': 'starting', 'step': 'tool'}, {'ok': True, 'ready': 'a:b'})
    assert worker._exchange({'op': 'open', 'inputs': {}}) == ({'ok': True, 'ready': 'a:b'}, b'')
    assert heard == ['code', 'viewer', 'tool'] and not worker._stopped.is_set()


def test_startup_listener_is_scoped_to_its_block():
    heard = []
    with scientific_host.reporting(heard.append):
        assert scientific_host._STARTUP.get() is not None
    assert scientific_host._STARTUP.get() is None


@pytest.mark.parametrize('request_op, messages', [
    ('open', [{'op': 'starting', 'step': 'compiling'}]),  # not a declared step
    ('open', [{'op': 'starting', 'step': 'tool'}, {'op': 'starting', 'step': 'viewer'}]),  # out of order
    ('open', [{'op': 'starting', 'step': 'viewer'}, {'op': 'starting', 'step': 'viewer'}]),  # repeated
    ('open', [{'op': 'starting', 'step': 'viewer', 'seconds': 3}]),  # more than its name
    ('render', [{'op': 'starting', 'step': 'viewer'}]),  # only while opening
])
def test_parent_refuses_an_invalid_startup_report_and_stops_the_worker(monkeypatch, request_op, messages):
    worker, heard = startup_worker(monkeypatch, *messages, {'ok': True, 'ready': 'a:b'})
    request = {'op': 'open', 'inputs': {}} if request_op == 'open' else {'op': 'render', 'width': 2, 'height': 3}
    with pytest.raises(scientific_host.Failed, match='invalid startup step'):
        worker._exchange(request)
    assert worker._stopped.is_set()
