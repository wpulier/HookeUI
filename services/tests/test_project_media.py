"""Synthetic media through real immutable storage; no decoder or live Library qualification."""
from dataclasses import replace

import pytest

from orchestrator.day.store import SessionStore
from orchestrator.library import Asset
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_media import ProjectMedia
from orchestrator.project_roots import ProjectRegistry, ProjectRoot, ProjectLocationError
from orchestrator.project_stores import ProjectStores
from orchestrator.scientific_projects import ProjectStore, Conflict, InvalidProject


def setup(tmp_path):
    legacy = tmp_path / 'host/sessions'
    SessionStore(legacy, session='2026-10-29')
    locations = ProjectLocations(legacy, ProjectRegistry(tmp_path / 'host'), tmp_path / 'Projects')
    day = SessionStore(legacy, session='2026-10-29', locations=locations)
    sid = day.new('Microscopy')
    stores = ProjectStores(ProjectStore(tmp_path / 'host/scientific'), locations)
    original = tmp_path / 'original.png'
    original.write_bytes(b'synthetic image bytes')
    asset = Asset('a1', 'footage:original', 'still', original, 'added', 'Selected image',
                  optics={'umPerPx': 0.4}, user={'notes': 'Observed note'})
    return locations, day, sid, stores, asset


def retain(media, sid, asset, **kwargs):
    return media.retain(sid, asset, request='media-retention-0001', max_bytes=4096,
                        producer={'actor': 'agent:fixture'}, **kwargs)


def test_retained_media_survives_move_deleted_original_and_another_host_library_id(tmp_path):
    locations, day, sid, stores, asset = setup(tmp_path)
    try:
        media = ProjectMedia(stores)
        ref = retain(media, sid, asset)
        assert retain(media, sid, asset) == ref
        root = locations.project(sid)
        day.new('Different visible Project')
        metadata, source = media.read(ref)
        from orchestrator.resources import Index
        from orchestrator.project_state import _item
        from orchestrator.door.schema import check
        entry = Index.project_entry(stores.read(ref.document, sid))
        assert entry.wire()['material'] == _item(entry)['material']
        material = entry.wire()['material']
        assert material['kind'] == 'image' and material['input']
        check(material, 'project-material')
        assert metadata['asset']['id'] == 'a1'
        assert metadata['asset']['notes'] == 'Observed note'
        assert metadata['asset']['optics'] == {'umPerPx': 0.4}
        assert metadata['history'] == 'metadata-at-retention'
        assert source.read(0, source.size) == b'synthetic image bytes'
        asset.path.unlink()
        locations.close()
        moved = tmp_path / 'Moved Project'
        root.path.rename(moved)
        fresh = ProjectLocations(locations.legacy, locations.registry, locations.parent)
        try:
            fresh.registry.register(ProjectRoot.open(moved), replace_inactive=True)
            reader = ProjectMedia(ProjectStores(stores.legacy, fresh))
            restored_metadata, restored = reader.read(ref)
            assert restored_metadata == metadata
            assert restored.read(0, restored.size) == b'synthetic image bytes'
            assert restored.sha256 == source.sha256
            # A coincident host Library ID cannot redirect an exact Project reference.
            other = tmp_path / 'unrelated.png'; other.write_bytes(b'unrelated a1')
            unrelated = replace(asset, path=other)
            assert unrelated.id == metadata['asset']['id']
            assert reader.read(ref)[1].read(0, restored.size) != unrelated.path.read_bytes()
        finally:
            fresh.close()
    finally:
        locations.close()


def test_changed_original_never_changes_an_accepted_revision(tmp_path):
    locations, day, sid, stores, asset = setup(tmp_path)
    try:
        media = ProjectMedia(stores); ref = retain(media, sid, asset)
        asset.path.write_bytes(b'changed original')
        with pytest.raises(Conflict): retain(media, sid, asset)
        assert media.read(ref)[1].read(0, 21) == b'synthetic image bytes'
    finally:
        locations.close()


def test_generated_image_keeps_origin_when_selected_as_input(tmp_path):
    from orchestrator.project_material import describe
    locations, day, sid, stores, asset = setup(tmp_path)
    try:
        ref = retain(ProjectMedia(stores), sid, replace(asset, group='snapshot'))
        material = describe(stores.read(ref.document, sid).document)
        assert material['origin'] == 'generated' and material['input']
    finally:
        locations.close()


@pytest.mark.parametrize('reason', ['changed', 'replaced', 'stopped', 'closed'])
def test_source_or_owner_change_during_copy_never_publishes(tmp_path, monkeypatch, reason):
    locations, day, sid, stores, asset = setup(tmp_path)
    from orchestrator.project_media import _MediaIntake
    original = _MediaIntake.write
    def changed(self, body):
        original(self, body)
        if reason == 'changed': asset.path.write_bytes(b'changed source')
        if reason == 'replaced':
            saved = asset.path.with_suffix('.old'); asset.path.rename(saved)
            asset.path.write_bytes(body)
        if reason == 'closed': locations.close()
    monkeypatch.setattr(_MediaIntake, 'write', changed)
    try:
        with pytest.raises(ProjectLocationError):
            retain(ProjectMedia(stores), sid, asset, stopping=lambda: reason == 'stopped')
        if reason != 'closed': assert stores.list(sid) == []
    finally:
        locations.close()


def test_second_host_reads_saved_source_but_cannot_write(tmp_path):
    locations, day, sid, stores, asset = setup(tmp_path)
    other = ProjectLocations(locations.legacy, locations.registry, locations.parent)
    try:
        ref = retain(ProjectMedia(stores), sid, asset)
        media = ProjectMedia(ProjectStores(stores.legacy, other))
        assert media.read(ref)[1].read(0, 21) == b'synthetic image bytes'
        with pytest.raises(ProjectLocationError, match='read-only'): retain(media, sid, asset)
        with pytest.raises(ProjectLocationError, match='origin'): media.read(replace(ref, origin='0' * 32))
        with pytest.raises(ProjectLocationError, match='identity'): media.read(replace(ref, digest='0' * 64))
    finally:
        other.close(); locations.close()


@pytest.mark.parametrize('kind', ['record', 'symlink', 'oversize'])
def test_shared_capture_link_and_oversize_refuse_before_acceptance(tmp_path, kind):
    locations, day, sid, stores, asset = setup(tmp_path)
    try:
        if kind == 'record': asset = replace(asset, group='record', key='dataset:ds1/1')
        if kind == 'symlink':
            link = tmp_path / 'link.png'; link.symlink_to(asset.path)
            asset = replace(asset, path=link)
        if kind == 'oversize': asset.path.write_bytes(b'x' * 4097)
        with pytest.raises((ProjectLocationError, InvalidProject, OSError)):
            retain(ProjectMedia(stores), sid, asset)
        assert stores.list(sid) == []
    finally:
        locations.close()


def test_media_copy_refuses_event_loop_work(tmp_path):
    import asyncio
    locations, day, sid, stores, asset = setup(tmp_path)
    try:
        async def on_loop():
            with pytest.raises(ProjectLocationError, match='background'):
                retain(ProjectMedia(stores), sid, asset)
        asyncio.run(on_loop())
    finally:
        locations.close()
