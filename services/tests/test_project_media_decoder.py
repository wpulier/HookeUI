"""Real Pillow decode of synthetic retained pixels; no live playback or camera claim."""
from dataclasses import replace
from types import SimpleNamespace

from PIL import Image
import pytest

from orchestrator.project_media import ProjectMedia
from orchestrator.project_media_decoder import retained_path
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_roots import ProjectLocationError, ProjectRoot
from orchestrator.project_stores import ProjectStores
from orchestrator.scientific_projects import InvalidProject
from tests.test_project_media import setup, retain


@pytest.fixture
def image_project(tmp_path):
    locations, day, sid, stores, asset = setup(tmp_path)
    image = Image.new('RGB', (13, 17), (4, 83, 212))
    image.save(asset.path)
    media = ProjectMedia(stores)
    ref = retain(media, sid, asset)
    try:
        yield locations, day, sid, stores, asset, media, ref
    finally:
        locations.close()


def test_decode_exact_source_after_deleting_original_and_moving_project(image_project, tmp_path):
    locations, day, sid, stores, asset, media, ref = image_project
    root = locations.project(sid)
    asset.path.unlink()
    locations.close()
    moved = tmp_path / 'Moved'
    root.path.rename(moved)
    fresh = ProjectLocations(locations.legacy, locations.registry, locations.parent)
    try:
        fresh.registry.register(ProjectRoot.open(moved), replace_inactive=True)
        media = ProjectMedia(ProjectStores(stores.legacy, fresh))
        assert media.candidates(sid, asset.id) == [ref]
        with retained_path(media, ref, host_cache=tmp_path / 'cache', max_bytes=4096) as source:
            assert source.reference == ref
            assert source.metadata['asset']['optics'] == {'umPerPx': 0.4}
            assert source.path.stat().st_mode & 0o222 == 0
            with Image.open(source.path) as decoded:
                assert decoded.size == (13, 17)
                assert decoded.tobytes() == bytes((4, 83, 212)) * (13 * 17)
            held = source.path
        assert not held.exists()
        assert media.candidates(sid, asset.id) == [ref]
    finally:
        fresh.close()


def test_same_library_id_in_different_projects_never_selects_other_bytes(image_project, tmp_path):
    locations, day, sid, stores, asset, media, ref = image_project
    other = day.new('Different origin')
    Image.new('RGB', (2, 3), (55, 22, 11)).save(asset.path)
    other_ref = retain(media, other, asset)
    assert media.candidates(sid, asset.id) == [ref]
    assert media.candidates(other, asset.id) == [other_ref]
    with retained_path(media, ref, host_cache=tmp_path / 'cache', max_bytes=4096) as source:
        with Image.open(source.path) as decoded:
            assert decoded.size == (13, 17)
    with pytest.raises(ProjectLocationError, match='origin'):
        with retained_path(media, replace(ref, session=other), host_cache=tmp_path / 'cache', max_bytes=4096):
            pytest.fail('Foreign origin admitted')


def test_multiple_retained_sources_are_choices_not_latest_wins(image_project):
    locations, day, sid, stores, asset, media, ref = image_project
    Image.new('RGB', (2, 3), (55, 22, 11)).save(asset.path)
    second = media.retain(sid, asset, request='second-source-0001', max_bytes=4096, producer={'actor': 'user'})
    assert set(media.candidates(sid, asset.id)) == {ref, second}
    assert media.candidates(sid, 'a999') == []


@pytest.mark.parametrize('fault', ['disk', 'bound', 'stopped', 'project-cache', 'linked-cache', 'changed-chunk', 'closed'])
def test_refused_open_never_changes_accepted_source(image_project, tmp_path, monkeypatch, fault):
    locations, day, sid, stores, asset, media, ref = image_project
    saved = stores.read(ref.document, sid)
    cache = tmp_path / 'cache'
    if fault == 'disk':
        monkeypatch.setattr('orchestrator.project_media_decoder.shutil.disk_usage', lambda _: SimpleNamespace(free=0))
    if fault == 'project-cache':
        cache = locations.project(sid).path / 'workspace' / 'cache'
    if fault == 'linked-cache':
        real = tmp_path / 'real-cache'; real.mkdir()
        cache.symlink_to(real)
    if fault == 'changed-chunk':
        source = media.read(ref)[1]
        chunk = stores.for_session(sid)._folder(ref.document) / 'files' / (source.chunks[0] + '.bin')
        chunk.chmod(0o600); chunk.write_bytes(b'changed source bytes')
    if fault == 'closed':
        locations.close()
    with pytest.raises((ProjectLocationError, InvalidProject, OSError)):
        with retained_path(media, ref, host_cache=cache, max_bytes=0 if fault == 'bound' else 4096,
                           stopping=lambda: fault == 'stopped'):
            pytest.fail('Unavailable source opened')
    if fault != 'closed':
        assert stores.read(ref.document, sid) == saved
    if cache.exists() and not cache.is_symlink():
        assert not list(cache.glob('hooke-media-*'))


def test_decoder_failure_removes_only_disposable_copy(image_project, tmp_path):
    locations, day, sid, stores, asset, media, ref = image_project
    with pytest.raises(RuntimeError, match='decoder failed'):
        with retained_path(media, ref, host_cache=tmp_path / 'cache', max_bytes=4096) as source:
            path = source.path
            raise RuntimeError('decoder failed')
    assert not path.exists()
    assert media.candidates(sid, asset.id) == [ref]
