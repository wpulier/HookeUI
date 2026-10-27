"""Real native staging and publication separation; synthetic worker messages."""
import itertools
import numpy as np
import pytest

from orchestrator.scientific_host import Failed, LayerStaging
from tests.test_scientific_edits import scene, view, display, values


def header(staging, index):
    return {'op': 'write-native-chunk', 'token': staging.specification['token'],
            'index': index, 'shape': [2, 3], 'dtype': '>u2'}


def test_complete_export_never_publishes_until_parent_accepts(scene):
    store, original, reference, data = scene
    owner = view(scene)
    edit = owner.begin(display(owner), max_written_bytes=data.nbytes)
    staging = LayerStaging(edit)
    changed = data.copy(); changed[1, 4] = 65535
    for row, col in itertools.product(range(2), repeat=2):
        chunk = changed[row*2:row*2+2, col*3:col*3+3]
        staging.answer(header(staging, [row, col]), chunk.tobytes())
    staging.finish({'ok': True, 'staged': edit.identity}, b'')
    assert store.read(original.id, 's1') == original
    saved = edit.commit()
    assert np.array_equal(values(store, saved, saved.document['resources'][0]), changed)
    assert np.array_equal(values(store, original, reference), data)


@pytest.mark.parametrize('bad', ['commit', 'order', 'oversize', 'token', 'dtype', 'duplicate'])
def test_bad_stage_messages_are_refused_before_payload_or_publication(scene, bad):
    store, original, _, data = scene
    owner = view(scene)
    edit = owner.begin(display(owner), max_written_bytes=data.nbytes)
    staging = LayerStaging(edit)
    message, size = header(staging, [0, 0]), 12
    if bad == 'commit': message = {'op': 'commit-native-edit', 'token': edit.identity}; size = 0
    if bad == 'order': message['index'] = [1, 1]
    if bad == 'oversize': size = 1 << 30
    if bad == 'token': message['token'] = 'wrong'
    if bad == 'dtype': message['dtype'] = '<u2'
    if bad == 'duplicate': staging.answer(message, data[:2, :3].tobytes())
    with pytest.raises(ValueError): staging.admit(message, size)
    assert store.read(original.id, 's1') == original
    edit.cancel()


def test_missing_chunks_wrong_completion_and_late_chunks_are_refused(scene):
    store, original, _, data = scene
    owner = view(scene)
    edit = owner.begin(display(owner), max_written_bytes=data.nbytes)
    staging = LayerStaging(edit)
    with pytest.raises(Failed): staging.finish({'ok': True, 'staged': edit.identity}, b'')
    for row, col in itertools.product(range(2), repeat=2):
        staging.answer(header(staging, [row, col]), data[row*2:row*2+2, col*3:col*3+3].tobytes())
    with pytest.raises(Failed): staging.admit(header(staging, [0, 0]), 12)
    with pytest.raises(Failed): staging.finish({'ok': True, 'staged': 'other'}, b'')
    assert store.read(original.id, 's1') == original
    edit.cancel()


@pytest.mark.parametrize('remaining', ['enough','bytes','entries','file'])
def test_snapshot_storage_preserves_existing_limits_and_low_disk_workflows(tmp_path, remaining):
    from orchestrator.scientific_host import snapshot_storage
    from orchestrator.worker_resources import Limits
    (tmp_path/'runtime-cache').write_bytes(b'x'*16)
    limits = Limits(work_bytes=80 if remaining != 'bytes' else 63,
                    work_entries=4 if remaining != 'entries' else 2,
                    file_bytes=64 if remaining != 'file' else 32)
    before = vars(limits).copy()
    mode = snapshot_storage(tmp_path, {'shape':[4,6],'dtype':'>u2'}, limits)
    assert mode == ('files' if remaining == 'enough' else 'memory')
    assert vars(limits) == before
    assert (tmp_path/'runtime-cache').read_bytes() == b'x'*16


def test_unsafe_existing_worker_files_never_select_a_fallback(tmp_path):
    from orchestrator.scientific_host import snapshot_storage
    from orchestrator.worker_resources import Limits
    from orchestrator.fiji_sandbox import Failed
    (tmp_path/'link').symlink_to(tmp_path.parent)
    with pytest.raises(Failed,match='link'):
        snapshot_storage(tmp_path, {'shape':[4,6],'dtype':'>u2'}, Limits())


@pytest.mark.parametrize('free', [0, 47, 48, None])
def test_snapshot_checks_the_worker_volume_not_the_retained_volume(tmp_path, monkeypatch, free):
    from types import SimpleNamespace
    from orchestrator import scientific_host as host
    from orchestrator.downloads import SPARE
    from orchestrator.worker_resources import Limits
    worker, retained = tmp_path/'worker', tmp_path/'retained'
    worker.mkdir(); retained.mkdir()
    checked = []
    def disk(path):
        checked.append(path)
        if path == retained: return SimpleNamespace(free=10 << 30)
        assert path == worker
        if free is None: raise OSError('Controlled inaccessible volume')
        return SimpleNamespace(free=SPARE+free)
    monkeypatch.setattr(host.shutil, 'disk_usage', disk)
    assert host.snapshot_storage(worker, {'shape':[4,6],'dtype':'>u2'}, Limits()) == ('files' if free == 48 else 'memory')
    assert checked == [worker]


def test_only_pre_transport_refusals_preserve_private_worker(scene):
    from orchestrator.scientific_host import CaptureRefused
    owner=view(scene); edit=owner.begin(display(owner),max_written_bytes=48)
    staging=LayerStaging(edit)
    refused={'ok':False,'code':'NATIVE_CAPTURE_NOT_STARTED','error':'Layer binding changed'}
    with pytest.raises(CaptureRefused,match='Layer binding changed'): staging.refuse(refused,b'')
    staging.answer(header(staging,[0,0]),scene[3][:2,:3].tobytes())
    with pytest.raises(Failed) as failure: staging.refuse(refused,b'')
    assert not isinstance(failure.value,CaptureRefused)
    edit.cancel()

@pytest.mark.parametrize('sizes',[(0,0),(0,1),(1,0),(0,1,0),(1,0,1)])
def test_empty_components_need_no_chunks_but_all_nonempty_chunks_and_exact_receipt(sizes):
    from orchestrator.scientific_host import LayersStaging
    class Edit:
        def __init__(self,index,size):self.token=str(index);self.size=size
        def specification(self):
            return {'format':'native-output-v1','token':self.token,'shape':[self.size,1],
                    'dtype':'|u1','axes':['row','column'],'chunks':[1,1]}
        def admit_message(self,header,size):
            if header.get('token')!=self.token or size!=self.size:raise Failed('wrong grant')
        def answer(self,header,payload):return {},b''
    edits=[Edit(i,n) for i,n in enumerate(sizes)];staging=LayersStaging(edits)
    receipt={'ok':True,'staged':[edit.token for edit in edits]}
    if any(sizes):
        with pytest.raises(Failed,match='incomplete'):staging.finish(receipt,b'')
    for edit in edits:
        if edit.size:
            staging.answer({'op':'write-native-chunk','index':[0,0],'token':edit.token},b'x')
    with pytest.raises(Failed):staging.finish({'ok':True,'staged':[]},b'')
    staging.finish(receipt,b'')
    with pytest.raises(Failed):staging.answer({'op':'write-native-chunk','index':[0,0],'token':'0'},b'')
