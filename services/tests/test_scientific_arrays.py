"""Real chunk storage and shared read protocol; synthetic data, no viewer qualification."""
import itertools
import json

import numpy as np
import pytest

from env_worker import native_regions as sdk
from orchestrator.native_regions import NativeRegionInputs
from orchestrator.scientific_projects import Conflict, InvalidProject, ProjectStore


def project(store, session='s1'):
    return store.create(session, {'kind': 'scene', 'title': 'Native labels', 'state': {}, 'resources': [], 'runtimes': []})


def draft(store, owner, data, chunks=(2, 3, 4), **kw):
    return store.array_draft(owner.id, owner.session, component='array.labels', shape=data.shape,
                             dtype=data.dtype.str, axes=tuple('zyx'[-data.ndim:]), chunks=chunks,
                             scientific={'semantic': 'instance-labels'}, max_written_bytes=data.nbytes, **kw)


def fill(writer, data):
    for index in itertools.product(*(range(n) for n in writer.grid)):
        slices = tuple(slice(i * c, min((i + 1) * c, n)) for i, c, n in zip(index, writer.chunks, writer.shape))
        writer.write(index, np.asarray(data[slices]))
    return writer.finish()


def accept(store, owner, ref):
    doc = owner.document
    doc['resources'] = [ref]
    return store.save(owner.id, owner.session, doc, expected_revision=owner.revision)


def bound(store, owner, ref):
    source = store.array_source(owner.id, owner.session, owner.revision, ref)
    grant = NativeRegionInputs({'labels': source})
    return sdk.bind(grant.specifications(), grant.answer)['labels']


@pytest.mark.parametrize('dtype', ['<u2', '>u2', '<u8', '>f4'])
def test_native_values_cross_chunk_boundaries_and_reopen_exactly(tmp_path, dtype):
    store = ProjectStore(tmp_path)
    owner = project(store)
    data = np.arange(3 * 5 * 7).reshape(3, 5, 7).astype(dtype)
    if dtype == '<u8': data += 2**60
    if dtype == '>f4': data[1, 2, 3] = np.nan
    writer = draft(store, owner, data)
    ref = fill(writer, data)
    with pytest.raises(InvalidProject, match='not accepted'):
        bound(store, owner, ref)
    accepted = accept(store, owner, ref)
    store = ProjectStore(tmp_path)
    result = bound(store, accepted, ref).read([[1, 3], [1, 5], [2, 7]])
    assert result.dtype.str == dtype
    assert result.tobytes() == data[1:3, 1:5, 2:7].tobytes()
    assert bound(store, accepted, ref).descriptor['semantic'] == 'instance-labels'
    assert store.read(owner.id, 's1', revision=1).document['resources'] == []
    assert not result.flags.writeable


def test_incremental_edit_reuses_chunks_and_keeps_undo_revision(tmp_path):
    store, data = ProjectStore(tmp_path), np.arange(3 * 5 * 7, dtype='<u2').reshape(3, 5, 7)
    owner = project(store)
    original_ref = fill(draft(store, owner, data), data)
    original = accept(store, owner, original_ref)
    old_files = set((tmp_path / owner.id / 'arrays').glob('*.bin'))
    writer = draft(store, owner, data, base=(original.revision, original_ref))
    changed = data.copy()
    changed[:2, :3, :4] = 65000
    writer.write((0, 0, 0), changed[:2, :3, :4])
    ref = writer.finish()
    updated = accept(store, original, ref)
    assert len(set((tmp_path / owner.id / 'arrays').glob('*.bin')) - old_files) == 1
    bounds = [[0, n] for n in data.shape]
    assert np.array_equal(bound(store, updated, ref).read(bounds), changed)
    assert np.array_equal(bound(store, original, original_ref).read(bounds), data)
    undone = accept(store, updated, original_ref)
    assert np.array_equal(bound(store, undone, original_ref).read(bounds), data)


def test_incomplete_cancelled_and_stale_work_cannot_replace_accepted_data(tmp_path):
    store, data = ProjectStore(tmp_path), np.ones((3, 5, 7), dtype='<u2')
    owner = project(store)
    incomplete = draft(store, owner, data)
    incomplete.write((0, 0, 0), data[:2, :3, :4])
    with pytest.raises(InvalidProject, match='missing'): incomplete.finish()
    incomplete.close()
    with pytest.raises(InvalidProject, match='closed'): incomplete.write((0, 0, 0), data[:2, :3, :4])
    first_ref = fill(draft(store, owner, data), data)
    second_ref = fill(draft(store, owner, data * 2), data * 2)
    accepted = accept(store, owner, first_ref)
    with pytest.raises(Conflict): accept(store, owner, second_ref)
    assert store.read(owner.id, 's1').digest == accepted.digest
    with pytest.raises(InvalidProject, match='not accepted'): bound(store, accepted, second_ref)
    retried = accept(store, accepted, second_ref)
    assert bound(store, retried, second_ref).read([[0, 1]] * 3).item() == 2


def test_another_project_session_or_unaccepted_base_cannot_grant_access(tmp_path):
    store, data = ProjectStore(tmp_path), np.ones((3, 5, 7), dtype='<u2')
    one, two = project(store), project(store)
    ref = fill(draft(store, one, data), data)
    with pytest.raises(InvalidProject, match='not accepted'):
        draft(store, one, data, base=(one.revision, ref))
    accepted = accept(store, one, ref)
    with pytest.raises(InvalidProject, match='session'):
        store.array_source(one.id, 'other', accepted.revision, ref)
    with pytest.raises(InvalidProject, match='belong'): accept(store, two, ref)
    with pytest.raises(InvalidProject, match='not accepted'): bound(store, two, ref)
    assert bound(store, accepted, ref).read([[0, 1]] * 3).item() == 1


@pytest.mark.parametrize('change', [
    {'dtype': 'object'}, {'dtype': '=u2'}, {'chunks': (True, 3, 4)}, {'chunks': (2, 3)},
    {'component': 'array.../secret'}, {'axes': ('z', 'z', 'x')}, {'max_written_bytes': True},
    {'scientific': {'dtype': 'uint8'}}, {'scientific': {'reference': {'session': 's1', 'id': 'd1', 'revision': 0}}},
    # Beyond the chunk index (64**3 chunks), not merely beyond an old whole-array cap.
    {'shape': (4096, 4096, 4096), 'chunks': (64, 64, 64)},
])
def test_invalid_declarations_do_not_write_array_files(tmp_path, change):
    store, owner = ProjectStore(tmp_path), None
    owner = project(store)
    args = dict(component='array.labels', shape=(3, 5, 7), dtype='<u2', axes=('z', 'y', 'x'),
                chunks=(2, 3, 4), scientific={}, max_written_bytes=210)
    with pytest.raises(ValueError): store.array_draft(owner.id, 's1', **(args | change))
    assert not (tmp_path / owner.id / 'arrays').exists()


def test_write_allowance_and_dtype_precede_serialization(tmp_path):
    store, owner = ProjectStore(tmp_path), None
    owner = project(store)
    writer = store.array_draft(owner.id, 's1', component='array.labels', shape=(2, 2), dtype='<u2',
                               axes=('y', 'x'), chunks=(1, 2), scientific={}, max_written_bytes=4)
    class Lazy:
        def __array__(self): pytest.fail('must not materialize arbitrary arrays')
    for data in (Lazy(), np.ones((1, 2), dtype='<u8'), np.ones((2, 1), dtype='<u2')):
        with pytest.raises(InvalidProject): writer.write((0, 0), data)
    writer.write((0, 0), np.ones((1, 2), dtype='<u2'))
    with pytest.raises(InvalidProject, match='allowance'):
        writer.write((1, 0), np.ones((1, 2), dtype='<u2'))
    assert store.read(owner.id, 's1').revision == 1


def test_corrupt_and_symlinked_chunks_fail_without_affecting_unrelated_data(tmp_path):
    store, data = ProjectStore(tmp_path), np.ones((3, 5, 7), dtype='<u2')
    one, two = project(store), project(store)
    ref = fill(draft(store, one, data), data)
    other_ref = fill(draft(store, two, data * 2), data * 2)
    one, two = accept(store, one, ref), accept(store, two, other_ref)
    folder = tmp_path / one.id / 'arrays'
    manifest = json.loads((folder / (ref['revision'] + '.json')).read_text())
    target = folder / (manifest['parts']['0,0,0'] + '.bin')
    target.chmod(0o600)
    target.write_bytes(b'wrong')
    with pytest.raises(InvalidProject, match='chunk changed'): bound(store, one, ref).read([[0, 1]] * 3)
    target.unlink()
    target.symlink_to(tmp_path / two.id / 'arrays' / (json.loads((tmp_path / two.id / 'arrays' / (other_ref['revision'] + '.json')).read_text())['parts']['0,0,0'] + '.bin'))
    with pytest.raises(OSError): bound(store, one, ref).read([[0, 1]] * 3)
    assert bound(store, two, other_ref).read([[0, 1]] * 3).item() == 2


def test_disk_failure_keeps_original_and_retry_works(tmp_path, monkeypatch):
    from orchestrator import scientific_arrays
    store, data = ProjectStore(tmp_path), np.ones((3, 5, 7), dtype='<u2')
    owner = project(store)
    writer = draft(store, owner, data)
    original = scientific_arrays._put
    def fail(*args): raise OSError('controlled full disk')
    monkeypatch.setattr(scientific_arrays, '_put', fail)
    with pytest.raises(OSError): writer.write((0, 0, 0), data[:2, :3, :4])
    assert writer.closed
    assert store.read(owner.id, 's1').digest == owner.digest
    monkeypatch.setattr(scientific_arrays, '_put', original)
    accepted = accept(store, owner, fill(draft(store, owner, data), data))
    assert accepted.revision == 2


def test_chunk_read_amplification_refused_before_file_io(tmp_path, monkeypatch):
    from orchestrator import scientific_arrays
    store, data = ProjectStore(tmp_path), np.ones((3, 5, 7), dtype='<u2')
    owner = project(store)
    ref = fill(draft(store, owner, data, chunks=(1, 1, 1)), data)
    owner = accept(store, owner, ref)
    reader = bound(store, owner, ref)
    monkeypatch.setattr(scientific_arrays, 'MAX_CHUNKS_PER_READ', 2)
    monkeypatch.setattr(scientific_arrays, 'read_at', lambda *args: pytest.fail('chunk I/O before admission'))
    with pytest.raises(InvalidProject, match='too many chunks'):
        reader.read([[0, 3], [0, 5], [0, 7]])


def test_incremental_edit_cannot_reinterpret_scientific_meaning(tmp_path):
    store, data = ProjectStore(tmp_path), np.ones((3, 5, 7), dtype='<u2')
    owner = project(store)
    ref = fill(draft(store, owner, data), data)
    accepted = accept(store, owner, ref)
    with pytest.raises(InvalidProject, match='explicit conversion'):
        store.array_draft(owner.id, 's1', component='array.labels', shape=data.shape, dtype=data.dtype.str,
                          axes=('z', 'y', 'x'), chunks=(2, 3, 4), scientific={'semantic': 'class-labels'},
                          max_written_bytes=data.nbytes, base=(accepted.revision, ref))
    with pytest.raises(InvalidProject, match='exact accepted'):
        store.array_source(owner.id, 's1', None, ref)
    assert bound(store, accepted, ref).descriptor['semantic'] == 'instance-labels'


def test_public_project_save_cannot_accept_missing_or_duplicate_native_components(tmp_path):
    store, data = ProjectStore(tmp_path), np.ones((3, 5, 7), dtype='<u2')
    owner = project(store)
    ref = fill(draft(store, owner, data), data)
    with pytest.raises(OSError): accept(store, owner, ref | {'revision': '0' * 64})
    doc = owner.document
    doc['resources'] = [ref, ref]
    with pytest.raises(InvalidProject, match='one accepted'):
        store.save(owner.id, 's1', doc, expected_revision=1)
    assert store.read(owner.id, 's1').digest == owner.digest


def test_source_byte_amplification_is_bounded_before_chunk_io(tmp_path, monkeypatch):
    from orchestrator import scientific_arrays
    store, data = ProjectStore(tmp_path), np.ones((3, 5, 7), dtype='<u2')
    owner = project(store)
    ref = fill(draft(store, owner, data), data)
    reader = bound(store, accept(store, owner, ref), ref)
    monkeypatch.setattr(scientific_arrays, 'MAX_SOURCE_BYTES_PER_READ', data[:2, :3, :4].nbytes - 1)
    monkeypatch.setattr(scientific_arrays, 'read_at', lambda *args: pytest.fail('chunk I/O before admission'))
    with pytest.raises(InvalidProject, match='source-read budget'):
        reader.read([[0, 1]] * 3)


def test_array_directory_symlink_cannot_write_into_another_project(tmp_path):
    store, data = ProjectStore(tmp_path), np.ones((3, 5, 7), dtype='<u2')
    one, two = project(store), project(store)
    protected = tmp_path / two.id
    (tmp_path / one.id / 'arrays').symlink_to(protected, target_is_directory=True)
    before = set(protected.iterdir())
    with pytest.raises(OSError):
        draft(store, one, data).write((0, 0, 0), data[:2, :3, :4])
    assert set(protected.iterdir()) == before
    assert store.read(two.id, 's1').digest == two.digest


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'symlink'])
def test_damaged_candidate_chunk_cannot_advance_the_accepted_head(tmp_path, damage):
    store, data = ProjectStore(tmp_path), np.ones((3, 5, 7), dtype='<u2')
    owner = project(store)
    ref = fill(draft(store, owner, data), data)
    folder = tmp_path / owner.id / 'arrays'
    record = json.loads((folder / (ref['revision'] + '.json')).read_text())
    path = folder / (record['parts']['0,0,0'] + '.bin')
    original = path.read_bytes()
    path.unlink()
    if damage == 'corrupt': path.write_bytes(b'x' * len(original))
    if damage == 'symlink': path.symlink_to(folder / (ref['revision'] + '.json'))
    with pytest.raises((InvalidProject, OSError)): accept(store, owner, ref)
    assert store.read(owner.id, 's1').digest == owner.digest
    if path.is_symlink() or path.exists(): path.unlink()
    path.write_bytes(original)
    assert accept(store, owner, ref).revision == 2


def test_damaged_untouched_base_chunk_cannot_be_carried_into_an_edit(tmp_path):
    store, data = ProjectStore(tmp_path), np.arange(105, dtype='<u2').reshape(3, 5, 7)
    owner = project(store)
    ref = fill(draft(store, owner, data), data)
    accepted = accept(store, owner, ref)
    writer = draft(store, owner, data, base=(accepted.revision, ref))
    writer.write((0, 0, 0), np.full((2, 3, 4), 65000, dtype='<u2'))
    changed = writer.finish()
    folder = tmp_path / owner.id / 'arrays'
    record = json.loads((folder / (ref['revision'] + '.json')).read_text())
    (folder / (record['parts']['1,1,1'] + '.bin')).unlink()
    with pytest.raises(OSError): accept(store, accepted, changed)
    assert store.read(owner.id, 's1').digest == accepted.digest


@pytest.mark.parametrize('limit', ['MAX_NEW_BYTES', 'MAX_COMBINED_METADATA'])
def test_publication_admission_precedes_chunk_reads(tmp_path, monkeypatch, limit):
    from orchestrator import scientific_arrays
    store, data = ProjectStore(tmp_path), np.ones((3, 5, 7), dtype='<u2')
    owner = project(store)
    ref = fill(draft(store, owner, data), data)
    read = scientific_arrays.read_at
    monkeypatch.setattr(scientific_arrays, limit, 1)
    def metadata_only(folder, name, bound):
        assert name.endswith('.json'), 'chunk read before publication admission'
        return read(folder, name, bound)
    monkeypatch.setattr(scientific_arrays, 'read_at', metadata_only)
    with pytest.raises(InvalidProject, match='save allowance'): accept(store, owner, ref)
    assert store.read(owner.id, 's1').digest == owner.digest



def test_cancel_during_native_verification_stops_before_next_chunk(tmp_path, monkeypatch):
    from orchestrator import scientific_arrays
    store, data = ProjectStore(tmp_path), np.arange(105, dtype='<u2').reshape(3, 5, 7)
    owner = project(store)
    ref = fill(draft(store, owner, data), data)
    document = owner.document
    document['resources'] = [ref]
    read = scientific_arrays.read_at
    chunks_read = []
    def read_then_stop(folder, name, bound):
        body = read(folder, name, bound)
        if name.endswith('.bin'):
            chunks_read.append(name)
        return body
    def check():
        if chunks_read:
            raise InvalidProject('Save stopped by its owner')
    monkeypatch.setattr(scientific_arrays, 'read_at', read_then_stop)
    with pytest.raises(InvalidProject, match='stopped by its owner'):
        store.save(owner.id, owner.session, document, expected_revision=owner.revision, check=check)
    assert len(chunks_read) == 1
    assert store.read(owner.id, owner.session).digest == owner.digest
    # The candidate remains available to an explicit retry; stopping never accepts it.
    retried = store.save(owner.id, owner.session, document, expected_revision=owner.revision)
    assert bound(store, retried, ref).read([[0, 3], [0, 5], [0, 7]]).tobytes() == data.tobytes()

def test_full_supported_chunk_index_uses_manifest_not_document_limit():
    from orchestrator import scientific_arrays
    manifest_fixture = {'format': scientific_arrays.FORMAT, 'session': 's1', 'project': 'p' + 'a' * 32,
                        'component': 'array.labels', 'shape': [65536], 'dtype': '|u1', 'axes': ['x'], 'chunks': [1],
                        'scientific': {}, 'parts': {str(i): 'a' * 64 for i in range(65536)}}
    encoded = scientific_arrays._encode_manifest(manifest_fixture)
    assert (2 << 20) < len(encoded) <= scientific_arrays.MAX_MANIFEST
    assert json.loads(encoded) == manifest_fixture


def test_metadata_cannot_be_accepted_when_transport_would_refuse_it(tmp_path):
    store, owner = ProjectStore(tmp_path), None
    owner = project(store)
    timeline = {'reference': {'session': 's1', 'id': 'd1', 'revision': 0},
                'axis': 't', 'unit': 's', 'samples': list(range(9000))}
    with pytest.raises(ValueError, match='too many values'):
        store.array_draft(owner.id, 's1', component='array.series', shape=(9000,), dtype='|u1', axes=('t',),
                          chunks=(1000,), scientific={'timeline': timeline}, max_written_bytes=9000)
    assert store.read(owner.id, 's1').digest == owner.digest
    assert not (tmp_path / owner.id / 'arrays').exists()
