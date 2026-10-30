"""Real App/journal/decoder on synthetic retained pixels; bridge transport is a stand-in."""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image
import pytest

from orchestrator import commands, source_images
from orchestrator.library import Asset
from orchestrator.paths import Paths
from orchestrator.project_media import ProjectMedia
from orchestrator.project_stage_media import StageMedia
from tests.test_project_app_routing import build
from tests.test_project_media_decoder import image_project  # noqa: F401 - shared real-file fixture
from tests.test_sources import Client


@pytest.fixture
def app_media(tmp_path, monkeypatch):
    app = build(Paths.under(tmp_path / 'host'), tmp_path / 'Projects')
    sid = app.day.new('Retained microscopy')
    original = tmp_path / 'original.png'
    Image.new('RGB', (13, 17), (4, 83, 212)).save(original)
    asset = Asset('a1', 'added:fixture', 'still', original, 'added', 'Retained cells',
                  optics={'umPerPx': 0.4}, user={'specimen': 'sample'})
    media = ProjectMedia(app.hub.resources.projects)
    ref = media.retain(sid, asset, request='stage-source-0001', max_bytes=4096, producer={'actor': 'user'})
    original.unlink()
    observed = []

    async def bridge(_app, method, path):
        if method == 'GET':
            return {}
        fields = parse_qs(urlsplit(path).query)
        if fields['source'] == ['asset']:
            held = Path(fields['path'][0])
            assert held.suffix == '.png'
            with Image.open(held) as pixels:
                assert pixels.size == (13, 17)
                assert pixels.tobytes() == bytes((4, 83, 212)) * 13 * 17
            observed.append(held)
        return {'ok': True}

    monkeypatch.setattr(commands, '_bridge', bridge)
    def forbidden(*args, **kwargs):
        pytest.fail('Retained source touched host Library')
    for method in ('get', 'set_meta', 'trash', 'restore', 'edits'):
        monkeypatch.setattr(app.hub.library, method, forbidden)
    try:
        yield app, sid, ref, observed
    finally:
        manager = getattr(app, '_project_stage_media', None)
        if manager is not None:
            asyncio.run(manager.replaced(None))  # stand-in bridge has no reader
        app.day.locations.close()  # fixture has no admitted background jobs
        asyncio.run(app.stop())


def test_source_reopens_exact_revision_and_analysis_survives_cache_removal(app_media):
    app, sid, ref, observed = app_media
    async def run():
        result = await commands.source(app, Client(), {'open': 'a1', 'session': sid, 'retained': asdict(ref)})
        assert result['ok']
        assert app.day.source_references == {'a1': asdict(ref)}
        assert app.day.catalogue.get(sid).source_references == app.day.source_references
        listed = await app.session_sources(sid)
        assert listed['sources'][0]['retained'] == asdict(ref)
        assert '/api/scientific-files/' in listed['sources'][0]['thumb']
        frozen = json.loads(app.hub.stage_image_json)
        assert 'path' not in frozen and frozen['retained'] == asdict(ref)
        with pytest.raises(ValueError, match='immutable optics'):
            app.hub.scale.set_objective(40)
        assert app.hub.scale.asset == {'id': 'a1', 'umPerPx': 0.4}
        app.subjects.name('new observation')  # journal observation, not a Library edit
        for command in ('asset-meta', 'asset-trash', 'asset-restore'):
            client = Client()
            await commands.handle(app, client, {'type': command, 'asset': 'a1', 'set': {'title': 'changed'}})
            assert not client.replies[-1]['ok']
            assert 'explicit revision' in client.replies[-1]['error']
        await commands.source(app, Client(), {'open': 'live'})
        assert not observed[0].exists()
        # Frozen analysis reads immutable Project bytes, not the bridge cache.
        app.hub.stage_image_json = json.dumps(frozen)
        capture = await source_images.for_analysis(app.hub)
        assert capture.source_image['retained'] == asdict(ref)
        assert capture.source_image['width'] == 13 and capture.source_image['height'] == 17
        assert capture.source_image['umPerPx'] == 0.4
        await commands.source(app, Client(), {'open': 'a1'})  # saved exact binding
        removed = await commands.source_remove(app, Client(), {'asset': 'a1'})
        assert removed['title'] == 'Retained cells'
        restored = await commands.source_restore(app, Client(), {'asset': 'a1'})
        assert restored['title'] == 'Retained cells'
    asyncio.run(run())


@pytest.mark.parametrize('change', ['foreign', 'digest', 'identity', 'missing'])
def test_invalid_selection_never_opens_bridge_or_global_fallback(app_media, change):
    app, sid, ref, observed = app_media
    selected = asdict(ref)
    if change == 'foreign': selected['origin'] = '0' * 32
    if change == 'digest': selected['digest'] = '0' * 64
    target = 'a2' if change == 'identity' else 'a1'
    async def run():
        client = Client()
        await commands.handle(app, client, {'type': 'source', 'open': target,
            **({'retained': selected} if change != 'missing' else {})})
        assert not client.replies[-1]['ok']
        assert observed == [] and app.day.source_references == {}
    asyncio.run(run())


def test_uncertain_bridge_input_survives_until_observed_replacement(app_media, monkeypatch):
    app, sid, ref, observed = app_media
    original_bridge = commands._bridge
    async def uncertain(_app, method, path):
        await original_bridge(_app, method, path)
        if method == 'POST' and 'source=asset' in path:
            raise asyncio.CancelledError()
        return {}
    monkeypatch.setattr(commands, '_bridge', uncertain)
    async def run():
        with pytest.raises(asyncio.CancelledError):
            await commands.source(app, Client(), {'open': 'a1', 'retained': asdict(ref)})
        assert observed[0].exists() and app.day.source_references == {}
        await commands.source(app, Client(), {'open': 'live'})
        assert not observed[0].exists()
    asyncio.run(run())


def test_retired_media_host_refuses_new_preparation(image_project, tmp_path):
    _, _, sid, stores, _, _, ref = image_project
    manager = StageMedia(stores, tmp_path / 'cache', max_bytes=4096)
    manager.retire()
    with pytest.raises(ValueError, match='stopped'):
        asyncio.run(manager.prepare(asdict(ref), sid, 'a1'))


def test_population_overlay_requires_the_exact_retained_revision(app_media):
    from orchestrator.population import on_stage
    app, sid, ref, _ = app_media
    spec = {'asset': 'a1', 'session': sid, 'retained': asdict(ref)}
    assert on_stage(json.dumps(spec), spec)
    assert not on_stage(json.dumps(spec), {**spec, 'retained': {**asdict(ref), 'revision': ref.revision + 1}})
    assert not on_stage(json.dumps(spec), {**spec, 'retained': {**asdict(ref), 'origin': '0' * 32}})
    assert not on_stage(json.dumps(spec), {'asset': 'a1', 'session': sid})
    assert not on_stage(json.dumps({'asset': 'a1', 'path': '/other.png'}), spec)


def test_repeated_cancellation_keeps_real_copy_owned_until_cleanup(image_project, tmp_path, monkeypatch):
    from contextlib import contextmanager
    import threading
    from orchestrator import project_stage_media
    from tests.waiting import HANG_S, until_async
    _, _, sid, stores, _, _, ref = image_project
    manager = StageMedia(stores, tmp_path / 'cache', max_bytes=4096)
    original = project_stage_media.retained_path
    entered, release = threading.Event(), threading.Event()
    paths = []
    @contextmanager
    def delayed(*args, **kwargs):
        with original(*args, **kwargs) as source:
            paths.append(source.path)
            entered.set()
            assert release.wait(HANG_S)
            yield source
    monkeypatch.setattr(project_stage_media, 'retained_path', delayed)
    async def run():
        task = asyncio.create_task(manager.prepare(asdict(ref), sid, 'a1'))
        await until_async(entered.is_set)
        task.cancel()
        await until_async(lambda: task.cancelling() == 1)
        # Let the cancellation handler install ownership before cancelling it again.
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await until_async(lambda: bool(manager.held))
        assert paths[0].exists()
        await manager.replaced(None)
        assert not paths[0].exists() and manager.held == []
    try:
        asyncio.run(run())
    finally:
        release.set()


def test_application_reopens_saved_source_after_project_folder_move(app_media, tmp_path):
    from orchestrator.project_roots import ProjectRoot
    app, sid, ref, observed = app_media
    asyncio.run(commands.source(app, Client(), {'open': 'a1', 'retained': asdict(ref)}))
    asyncio.run(app._project_stage_media.replaced(None))  # fixture bridge is quiescent
    root = app.day.locations.project(sid)
    app.day.locations.close()  # no fixture writers; real shutdown retains leases until process exit
    asyncio.run(app.stop())
    moved = tmp_path / 'Moved project'
    root.path.rename(moved)
    app.day.locations.registry.register(ProjectRoot.open(moved), replace_inactive=True)
    reopened = build(app.paths, tmp_path / 'Projects')
    try:
        reopened.day.open(sid)
        async def run():
            result = await commands.source(reopened, Client(), {'open': 'a1', 'session': sid})
            assert result['ok']
            capture = await source_images.for_analysis(reopened.hub)
            assert capture.source_image['retained'] == asdict(ref)
            assert (capture.source_image['width'], capture.source_image['height']) == (13, 17)
            listed = await reopened.session_sources(sid)
            assert listed['sources'][0]['title'] == 'Retained cells'
            await reopened._project_stage_media.replaced(None)
        asyncio.run(run())
    finally:
        reopened.day.locations.close()
        asyncio.run(reopened.stop())


def test_multi_chunk_original_is_analyzed_from_all_retained_bytes(app_media, tmp_path):
    import hashlib
    import numpy as np
    app, sid, _, _ = app_media
    original = tmp_path / 'large.png'
    pixels = np.random.default_rng(412).integers(0, 256, (700, 700, 3), dtype=np.uint8)
    Image.fromarray(pixels).save(original)
    assert original.stat().st_size > 1 << 20
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    asset = Asset('a2', 'added:large', 'still', original, 'added', 'Large image')
    ref = ProjectMedia(app.hub.resources.projects).retain(sid, asset,
        request='large-source-0001', max_bytes=2 << 20, producer={'actor': 'user'})
    original.unlink()
    app.hub.stage_image_json = json.dumps({'asset': 'a2', 'session': sid, 'retained': asdict(ref),
                                          'optics': {'umPerPx': 999}})
    result = asyncio.run(source_images.for_analysis(app.hub))
    assert result.source_image['sha256'] == digest
    assert result.source_image['width'] == result.source_image['height'] == 700
    assert 'umPerPx' not in result.source_image  # immutable source facts win


def test_slow_preparation_cannot_replace_newer_live_selection(app_media, monkeypatch):
    from orchestrator.project_stage_media import manager
    app, sid, ref, observed = app_media
    media = manager(app)
    original = media.prepare
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args):
            entered.set()
            await release.wait()
            return await original(*args)
        monkeypatch.setattr(media, 'prepare', delayed)
        earlier = asyncio.create_task(commands.source(app, Client(), {'open': 'a1', 'retained': asdict(ref)}))
        await entered.wait()
        assert (await commands.source(app, Client(), {'open': 'live'}))['ok']
        release.set()
        with pytest.raises(Exception, match='superseded'):
            await earlier
        assert observed == [] and app.day.on_stage == 'live'
        assert not list(media.cache.glob('hooke-media-*'))
    asyncio.run(run())


def test_failed_retained_history_cannot_leave_previous_scientific_source(app_media, monkeypatch):
    app, sid, ref, observed = app_media
    def fail(*args, **kwargs):
        raise OSError('fixture disk full')
    monkeypatch.setattr(app.day, 'open_source', fail)
    async def run():
        app.hub.stage_meta = {'id': 'a999', 'title': 'Previous specimen'}
        app.hub.stage_image_json = json.dumps({'asset': 'a999', 'path': '/previous.png', 'session': sid})
        with pytest.raises(OSError, match='disk full'):
            await commands.source(app, Client(), {'open': 'a1', 'retained': asdict(ref)})
        assert observed[0].exists()  # bridge owns new pixels, even though publication failed
        assert app.hub.stage_meta is None
        with pytest.raises(Exception, match='has not been retained'):
            await source_images.for_analysis(app.hub)
        assert app.day.source_references == {}
    asyncio.run(run())
