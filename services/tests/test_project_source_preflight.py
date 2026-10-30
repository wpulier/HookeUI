"""Historical source inventory over real stores; synthetic media, not complete collection."""
import pytest

from orchestrator.project_media import ProjectMedia
from orchestrator.project_repository import session_root
from orchestrator.project_source_preflight import review_sources
from orchestrator.project_roots import ProjectLocationError
from tests.test_project_media import setup, retain


def test_removed_sources_stay_dependencies_and_host_ids_are_not_guessed(tmp_path):
    locations, day, sid, stores, asset = setup(tmp_path)
    try:
        day.open_source('a1')
        day.remove_source('a1')
        day.open_source('live')
        day.add_turn('user', 'text', 'source a2 and /private/example.png')
        before = review_sources(locations.project(sid))
        assert before['sources'] == [{'asset': 'a1', 'status': 'inclusion-required', 'choices': []}]
        ref = retain(ProjectMedia(stores), sid, asset)
        asset.path.unlink()
        after = review_sources(locations.project(sid))
        assert after['completeProject'] is False
        assert after['journal'] == before['journal']
        assert after['sources'][0]['status'] == 'retained-choices'
        assert after['sources'][0]['choices'] == [{'status': 'retained-binding', 'reference': {
            'id': ref.document, 'session': sid, 'revision': ref.revision, 'digest': ref.digest}}]
    finally:
        locations.close()


def test_old_bindings_remain_choices_and_wrong_origins_are_visible(tmp_path):
    locations, day, sid, stores, asset = setup(tmp_path)
    try:
        day.open_source('a1')
        ref = retain(ProjectMedia(stores), sid, asset)
        saved = stores.read(ref.document, sid)
        doc = saved.document
        doc['state']['librarySource']['projectOrigin'] = 'wrong-origin'
        stores.save(saved.id, sid, doc, expected_revision=saved.revision)
        report = review_sources(locations.project(sid))
        choices = report['sources'][0]['choices']
        assert [item['status'] for item in choices] == ['retained-binding', 'invalid-retained-binding']
        assert [item['reference']['revision'] for item in choices] == [1, 2]
    finally:
        locations.close()


@pytest.mark.parametrize('extra', [b'{"type":"source"', b'not json\n', b'[]\n'])
def test_incomplete_or_damaged_journal_refuses_collection_preflight(tmp_path, extra):
    locations, day, sid, stores, asset = setup(tmp_path)
    try:
        root = locations.project(sid)
        path = session_root(root) / sid / 'journal.jsonl'
        with path.open('ab') as out: out.write(extra)
        with pytest.raises((ValueError, ProjectLocationError)):
            review_sources(root)
        assert path.read_bytes().endswith(extra)
    finally:
        locations.close()


def test_changed_journal_and_cancelled_review_refuse(tmp_path):
    locations, day, sid, stores, asset = setup(tmp_path)
    try:
        day.open_source('a1')
        root = locations.project(sid)
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            if calls == 3: raise RuntimeError('stopped')
        with pytest.raises(RuntimeError, match='stopped'):
            review_sources(root, check=cancel)
        path = session_root(root) / sid / 'journal.jsonl'
        original = path.stat().st_size
        callbacks = 0
        def change():
            nonlocal callbacks
            callbacks += 1
            if callbacks == 2 and path.stat().st_size == original:
                with path.open('ab') as out: out.write(b'{"type":"note","text":"new"}\n')
        with pytest.raises(ProjectLocationError, match='changed'):
            review_sources(root, check=change)
    finally:
        locations.close()


def test_journal_resource_limit_and_empty_source_review(tmp_path, monkeypatch):
    from orchestrator import project_source_preflight as module
    locations, day, sid, stores, asset = setup(tmp_path)
    try:
        root = locations.project(sid)
        assert review_sources(root)['sources'] == []
        monkeypatch.setattr(module, 'MAX_JOURNAL_BYTES', 1)
        with pytest.raises(ProjectLocationError, match='allowance'):
            review_sources(root)
    finally:
        locations.close()


def test_owner_scan_honors_inspection_limits_and_cancellation(tmp_path):
    from orchestrator.day.ownership import retained_owner
    locations, day, sid, stores, asset = setup(tmp_path)
    try:
        root = session_root(locations.project(sid))
        owner = retained_owner(root, sid)
        assert retained_owner(root, sid, allowance=(1 << 20, 65536)) == owner
        for allowance in ((1, 65536), (1 << 20, 1)):
            with pytest.raises(ValueError, match='allowance'):
                retained_owner(root, sid, allowance=allowance)
        def stop():
            raise RuntimeError('stopped')
        with pytest.raises(RuntimeError, match='stopped'):
            retained_owner(root, sid, allowance=(1 << 20, 65536), check=stop)
        assert retained_owner(root, sid) == owner
    finally:
        locations.close()
