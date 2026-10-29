"""Real immutable intake with synthetic Record bytes; no playback or owner UI proof."""
import json
import os
import shutil

import pytest

from orchestrator.project_record_collection import collect_record, review_record
from orchestrator.project_roots import ProjectLocationError, ProjectRoot
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_stores import ProjectStores
from orchestrator.scientific_projects import InvalidProject
from test_project_media import setup


def dataset(tmp_path):
    folder = tmp_path / 'shared-record'
    (folder / 'video').mkdir(parents=True)
    (folder / 'pictures').mkdir()
    (folder / 'video/part-1.mp4').write_bytes(b'synthetic whole recording, projects A and B')
    (folder / 'video/part-1.frames.jsonl').write_text('{"seq":1}\n')
    (folder / 'pictures/retained.png').write_bytes(b'synthetic captured picture')
    (folder / 'events.jsonl').write_text('{"stream":"journal","record":{"session":"A"}}\n'
                                        '{"stream":"journal","record":{"session":"B"}}\n')
    (folder / 'start-state.jsonl').write_text('{"historical":"state"}\n')
    summary = {'schema': 1, 'kind': 'microscopemind-dataset', 'id': 'shared-record',
        'status': 'complete', 'reconstructed': False,
        'video': {'parts': [{'video': 'video/part-1.mp4', 'sidecar': 'video/part-1.frames.jsonl'}]},
        'endCheck': {'ok': False, 'problems': ['Synthetic capture interruption'], 'notes': []}}
    (folder / 'dataset.json').write_text(json.dumps(summary))
    return folder


def collect(stores, sid, review, **kwargs):
    return collect_record(stores, sid, review, reviewed_digest=review.wire()['reviewDigest'],
        request='record-collection-request-0001', max_bytes=1 << 20,
        producer={'actor': 'agent:fixture'}, **kwargs)


def retained(stores, ref):
    saved = stores.read(ref.document, ref.session, revision=ref.revision)
    source = stores.file_source(saved.id, saved.session, saved.revision,
                               saved.document['state']['sourceFile']['reference'], verify_content=True)
    parts = {row['path']: source.read(row['offset'], row['size'])
             for row in saved.document['state']['sourceDirectory']['entries']}
    return saved, parts


def test_collects_whole_shared_capture_and_warnings_then_relocates_without_original(tmp_path):
    locations, day, sid, stores, _ = setup(tmp_path)
    folder = dataset(tmp_path)
    try:
        review = review_record(folder, max_bytes=1 << 20)
        wire = review.wire()
        assert wire['requiredFreeBytes'] > wire['bytes']
        assert wire['externalReferences'] == 'not-collected'
        assert wire['dataset']['endCheck']['ok'] is False
        ref = collect(stores, sid, review)
        assert collect(stores, sid, review) == ref
        saved, contents = retained(stores, ref)
        assert set(contents) == {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
        assert contents['events.jsonl'] == (folder / 'events.jsonl').read_bytes()
        assert b'projects A and B' in contents['video/part-1.mp4']
        from orchestrator.resources import Index
        from orchestrator.door.schema import check
        material = Index.project_entry(saved).wire()['material']
        assert material['kind'] == 'recorded-dataset' and material['input']
        check(material, 'project-material')
        root = locations.project(sid)
        locations.close()
        moved = tmp_path / 'Moved Research'; root.path.rename(moved)
        shutil.rmtree(folder)  # synthetic original only
        fresh = ProjectLocations(locations.legacy, locations.registry, locations.parent)
        try:
            fresh.registry.register(ProjectRoot.open(moved), replace_inactive=True)
            reopened = ProjectStores(stores.legacy, fresh)
            assert retained(reopened, ref)[1] == contents
        finally:
            fresh.close()
    finally:
        locations.close()


@pytest.mark.parametrize('change', ['bytes', 'member', 'directory', 'project', 'stop'])
def test_changed_source_or_destination_never_publishes(tmp_path, monkeypatch, change):
    from orchestrator.project_record_collection import _RecordIntake
    locations, _, sid, stores, _ = setup(tmp_path)
    folder = dataset(tmp_path)
    review = review_record(folder, max_bytes=1 << 20)
    original = _RecordIntake.write
    def write(self, data):
        original(self, data)
        if change == 'bytes': (folder / 'video/part-1.mp4').write_bytes(b'changed recording')
        if change == 'member': (folder / 'late.txt').write_text('later evidence')
        if change == 'directory' and not (folder / 'video').is_symlink():
            (folder / 'video').rename(folder / 'old-video')
            (folder / 'video').symlink_to(folder / 'old-video', target_is_directory=True)
        if change == 'project': locations.close()
    monkeypatch.setattr(_RecordIntake, 'write', write)
    try:
        with pytest.raises((ProjectLocationError, OSError)):
            collect(stores, sid, review, stopping=lambda: change == 'stop')
        if change != 'project': assert stores.list(sid) == []
    finally:
        locations.close()


@pytest.mark.parametrize('fault', ['live', 'unchecked', 'missing-part', 'symlink-file', 'symlink-folder',
                                  'oversize', 'wrong-review', 'low-disk'])
def test_review_or_admission_refuses_without_retained_record(tmp_path, monkeypatch, fault):
    locations, _, sid, stores, _ = setup(tmp_path)
    folder = dataset(tmp_path)
    try:
        summary = json.loads((folder / 'dataset.json').read_text())
        if fault == 'live': summary['status'] = 'collecting'
        if fault == 'unchecked': summary.pop('endCheck')
        (folder / 'dataset.json').write_text(json.dumps(summary))
        if fault == 'missing-part': (folder / 'video/part-1.mp4').unlink()
        if fault == 'symlink-file': (folder / 'linked').symlink_to(folder / 'events.jsonl')
        if fault == 'symlink-folder': (folder / 'linked').symlink_to(folder / 'video', target_is_directory=True)
        with pytest.raises((ProjectLocationError, InvalidProject, OSError)):
            review = review_record(folder, max_bytes=1 if fault == 'oversize' else 1 << 20)
            if fault == 'wrong-review':
                collect_record(stores, sid, review, reviewed_digest='wrong', request='record-request-0001',
                               max_bytes=1 << 20, producer={'actor': 'agent:fixture'})
            else:
                if fault == 'low-disk':
                    from collections import namedtuple
                    disk = namedtuple('Disk', 'total used free')
                    monkeypatch.setattr(shutil, 'disk_usage', lambda _: disk(100, 99, 1))
                collect(stores, sid, review)
        assert stores.list(sid) == []
    finally:
        locations.close()


def test_mutable_hard_link_is_copied_into_independent_evidence(tmp_path):
    locations, _, sid, stores, _ = setup(tmp_path)
    folder = dataset(tmp_path)
    external = tmp_path / 'bridge-original.mp4'
    os.link(folder / 'video/part-1.mp4', external)
    try:
        ref = collect(stores, sid, review_record(folder, max_bytes=1 << 20))
        external.write_bytes(b'original modified later')
        assert retained(stores, ref)[1]['video/part-1.mp4'] == b'synthetic whole recording, projects A and B'
    finally:
        locations.close()


def test_replaced_source_after_review_requires_new_review(tmp_path):
    locations, _, sid, stores, _ = setup(tmp_path)
    folder = dataset(tmp_path)
    try:
        review = review_record(folder, max_bytes=1 << 20)
        original = folder / 'video/part-1.mp4'
        data = original.read_bytes(); original.unlink(); original.write_bytes(data)
        with pytest.raises(ProjectLocationError): collect(stores, sid, review)
        assert stores.list(sid) == []
    finally:
        locations.close()


def test_collection_refuses_event_loop_work(tmp_path):
    import asyncio
    folder = dataset(tmp_path)
    async def on_loop():
        with pytest.raises(ProjectLocationError, match='background'):
            review_record(folder, max_bytes=1 << 20)
    asyncio.run(on_loop())


@pytest.mark.parametrize('missing', ['file', 'channelsFile'])
def test_missing_declared_workspace_recording_refuses_review(tmp_path, missing):
    folder = dataset(tmp_path)
    summary = json.loads((folder / 'dataset.json').read_text())
    screen = {'file': 'video/screen-1.webm', 'channelsFile': 'video/screen-1.channels.jsonl', 'finished': True}
    for name in ('file', 'channelsFile'):
        (folder / screen[name]).write_bytes(b'synthetic workspace capture')
    summary['screen'] = [screen]
    (folder / 'dataset.json').write_text(json.dumps(summary))
    (folder / screen[missing]).unlink()
    with pytest.raises(ProjectLocationError, match='screen recording'):
        review_record(folder, max_bytes=1 << 20)


def test_growing_source_never_reads_beyond_reviewed_size_plus_probe(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import orchestrator.project_record_collection as collection
    folder = dataset(tmp_path)
    video = folder / 'video/part-1.mp4'
    size = video.stat().st_size
    consumed = []
    original = collection._file
    class Growing:
        def __init__(self, stream): self.stream = stream
        def fileno(self): return self.stream.fileno()
        def read(self, count):
            body = self.stream.read(count)
            consumed.append(len(body))
            with video.open('ab') as output: output.write(b'x' * 100_000)
            return body
    @contextmanager
    def file(root, name):
        with original(root, name) as stream:
            yield Growing(stream) if name == 'video/part-1.mp4' else stream
    monkeypatch.setattr(collection, '_file', file)
    with pytest.raises(ProjectLocationError, match='changed'):
        review_record(folder, max_bytes=1 << 20)
    assert sum(consumed) == size + 1
