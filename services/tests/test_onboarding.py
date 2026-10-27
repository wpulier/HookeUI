"""First experience through ordinary session/command and provider admission seams."""
import asyncio

import pytest

from orchestrator.day.store import DayStore
from tests.waiting import HANG_S


def test_example_session_keeps_local_policy_when_reopened_without_changing_preferences(tmp_path):
    day = DayStore.resume(root=tmp_path)
    original = day.sid
    prefs = dict(day.prefs)
    sid = day.new('Example: Human cells', local_only=True)
    assert day.wire()['localOnly'] is True
    day.switch_session(original)
    assert day.wire()['localOnly'] is False
    day.switch_session(sid)
    reopened = DayStore.resume(root=tmp_path)
    assert reopened.wire()['localOnly'] is True
    assert reopened.prefs == prefs


@pytest.fixture
def example_app(tmp_path, monkeypatch):
    """Real app and local method; only the camera bridge and screen are simulated."""
    import json
    from orchestrator import commands
    from orchestrator.app import App
    from orchestrator.paths import Paths
    from tests.test_screen import Studio
    from research_worker import api_usage

    app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
    class Viewer(Studio):
        def offer(self, message):
            if isinstance(message, str):
                self.hear(json.loads(message))
    viewer = Viewer(app.hub.screen)
    app.hub.clients.add(viewer)
    viewer.report()
    async def bridge(app, method, path):
        return {'ok': True}
    monkeypatch.setattr(commands, '_bridge', bridge)
    # Credential sentinels exercise policy despite credential availability; never real keys.
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-onboarding-sentinel')
    monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-onboarding-sentinel')
    api_usage.configure(None, provider_allowed=lambda: not app.day.local_only)
    yield app, viewer
    api_usage.configure(None)
    app.hub.clients.clear()


def test_real_example_action_table_and_reopen_are_idempotent(example_app):
    from orchestrator import commands
    from orchestrator.tables import read_table
    from orchestrator.app import App
    from orchestrator.onboarding import Onboarding
    from orchestrator.population import capture_of
    from orchestrator.capture import captures
    from orchestrator.examples import human_mitosis
    import hashlib

    app, viewer = example_app
    defaults = app.hub.settings.to_wire()
    async def go():
        replies = await asyncio.gather(*(commands.onboarding(app, viewer, {'action': 'start'}) for _ in range(3)))
        guide = replies[0]['onboarding']
        assert all(r['onboarding'] == guide for r in replies)
        assert app.day.label == 'Example: Human cells'
        sid, aid = guide['session'], guide['asset']['id']
        assert app.day.on_stage == aid and aid in app.day.sources
        assert guide['asset']['optics'] == {} and app.day.local_only
        count = len(app.day.catalogue.list())
        request = {'action': 'analyze', 'session': sid}
        replies = await asyncio.gather(*(commands.onboarding(app, viewer, request) for _ in range(3)))
        guide = replies[0]['onboarding']
        assert guide['step'] == 'measurements' and guide['result']
        assert replies[0]['rendered'] == {'session': sid, **guide['result']}
        assert viewer.items['magnifier']['pictures']
        assert all(r['onboarding']['result'] == guide['result'] for r in replies)
        result = guide['result']
        assert len(app.hub.evidence.items) == 1
        assert (await commands.onboarding(app, viewer, request))['onboarding']['result'] == result
        table = await read_table(app, result['id'], sid, result['revision'])
        assert table['count'] > 0 and table['memberLabel'] == 'bright region'
        assert table['basis'].get('umPerPx') is None
        assert next(c for c in table['columns'] if c['key'] == 'areaPx')['unit'] == 'px²'
        p = app.hub.evidence.get(result['id'])
        capture = capture_of(app.hub, p)
        with captures(app.hub).hold(capture, 'analysis', 'hold', 'checking retained onboarding input'):
            assert hashlib.sha256(capture.native).hexdigest() == hashlib.sha256(human_mitosis(preview=True).read_bytes()).hexdigest()
        progressed = await commands.onboarding(app, viewer, {'action': 'measurements', 'session': sid, 'result': result})
        assert progressed['onboarding']['step'] == 'return'
        finished = await commands.onboarding(app, viewer, {'action': 'finish', 'session': sid, 'result': result})
        assert finished['onboarding']['status'] == 'complete'
        assert app.day.local_only and app.hub.settings.to_wire() == defaults
        resumed = await commands.onboarding(app, viewer, {'action': 'start'})
        assert resumed['onboarding']['session'] == sid and len(app.day.catalogue.list()) == count
        restarted = await commands.onboarding(app, viewer, {'action': 'restart'})
        assert restarted['onboarding']['session'] != sid and len(app.day.catalogue.list()) == count + 1
        assert result['id'] in app.day.elsewhere(sid).evidence
        app.switch_session(sid)
        table_after = await read_table(app, result['id'], sid, result['revision'])
        assert table_after['rows'] == table['rows']
        # A new App reads ordinary journals/evidence, not the controller's memory.
        rebuilt = App.build('http://127.0.0.1:1', app.paths, use_gpu=False)
        assert rebuilt.day.sid == sid and rebuilt.day.local_only
        assert (await read_table(rebuilt, result['id'], sid, result['revision']))['rows'] == table['rows']
        assert Onboarding(rebuilt).state['session'] == restarted['onboarding']['session']
    asyncio.run(go())


def test_skip_and_switch_do_not_enable_cloud_or_change_progress(example_app):
    from orchestrator import commands, observed, key_tests
    from orchestrator.operations import availability, OPERATIONS
    from research_worker import api_usage
    app, viewer = example_app
    async def go():
        guide = (await commands.onboarding(app, viewer, {'action': 'start'}))['onboarding']
        sid = guide['session']
        await commands.onboarding(app, viewer, {'action': 'dismiss', 'session': sid})
        assert app.day.local_only
        entered = []
        with pytest.raises(api_usage.ProviderRefused):
            async with api_usage.attempt('gpt-synthetic'):
                entered.append(True)
        with pytest.raises(api_usage.ProviderRefused):
            api_usage.VoiceUsage('gpt-synthetic')
        with pytest.raises(api_usage.ProviderRefused):
            await asyncio.to_thread(observed.probe_key, 'OPENAI_API_KEY')
        with pytest.raises(api_usage.ProviderRefused):
            await asyncio.to_thread(observed.probe_access, 'dinov3-small')
        with pytest.raises(api_usage.ProviderRefused):
            key_tests.check('openai', 'synthetic-sentinel')
        assert await app.observer.sweep(force=True) == 0
        assert not await app.hub.library_looks.step()
        assert not app.agent.available()
        assert not entered
        cloud = next(i for op in OPERATIONS.values() for i in op.implementations if i.worker == 'cloud')
        assert 'locally' in availability(app.hub, cloud)
        with pytest.raises(Exception, match='locally'):
            await app.research.start('Describe this image', confirmed=True)
        with pytest.raises(api_usage.ProviderRefused):
            await app.peek_catalog()
        current = app.new_session('My own specimen')['session']
        assert not app.day.local_only
        with pytest.raises(Exception, match='no longer open'):
            await commands.onboarding(app, viewer, {'action': 'analyze', 'session': sid})
        assert app.day.sid == current
        restored = (await commands.onboarding(app, viewer, {'action': 'start'}))['onboarding']
        assert restored['session'] == sid and restored['status'] == 'active' and restored['step'] == 'analyze'
        assert app.day.local_only
        assert not any(a.key.startswith('onboarding:') for a in app.hub.library.unlooked())
    asyncio.run(go())


def test_local_request_policy_survives_delayed_child_after_session_switch():
    from research_worker import api_usage
    async def go():
        entered = []
        ready = asyncio.Event()
        async def delayed():
            await ready.wait()
            async with api_usage.attempt('gpt-synthetic'):
                entered.append(True)
        with api_usage.local_only(True):
            child = asyncio.create_task(delayed())
        ready.set()
        with pytest.raises(api_usage.ProviderRefused):
            await child
        assert not entered
        # Outside that request, the same provider-admission seam retains its default.
        async with api_usage.attempt('gpt-synthetic'):
            entered.append(True)
        assert entered == [True]
    asyncio.run(go())


def test_missing_or_corrupt_bundle_preserves_current_session(example_app, tmp_path, monkeypatch):
    from orchestrator import commands, examples
    import shutil
    app, viewer = example_app
    root = tmp_path / 'broken-example'
    shutil.copytree(examples.ROOT, root)
    monkeypatch.setattr(examples, 'ROOT', root)
    sid = app.day.sid
    async def go():
        for content in (b'bad pixels', None):
            p = root / 'human_mitosis.png'
            if content is None:
                p.unlink()
            else:
                p.write_bytes(content)
            with pytest.raises(Exception, match='missing or damaged'):
                await commands.onboarding(app, viewer, {'action': 'start'})
            assert app.day.sid == sid
    asyncio.run(go())


def test_start_does_not_override_session_changed_during_bundle_read(example_app, monkeypatch):
    from orchestrator import commands, examples
    import threading
    app, viewer = example_app
    entered, release = threading.Event(), threading.Event()
    original = examples.human_mitosis
    def slow(**kwargs):
        entered.set()
        assert release.wait(HANG_S)
        return original(**kwargs)
    monkeypatch.setattr(examples, 'human_mitosis', slow)
    async def go():
        task = asyncio.create_task(commands.onboarding(app, viewer, {'action': 'start'}))
        await asyncio.to_thread(entered.wait, 5)
        latest = app.new_session('Newer choice')['session']
        release.set()
        with pytest.raises(Exception, match='session changed'):
            await task
        assert app.day.sid == latest and not app.day.local_only
    asyncio.run(go())


def test_disconnect_keeps_owned_work_and_source_failure_can_retry(example_app, monkeypatch):
    from orchestrator import commands
    app, viewer = example_app
    entered, release = asyncio.Event(), asyncio.Event()
    async def bridge(app, method, path):
        entered.set()
        await release.wait()
        return {'ok': True}
    monkeypatch.setattr(commands, '_bridge', bridge)
    async def go():
        client_task = asyncio.create_task(commands.onboarding(app, viewer, {'action': 'start'}))
        # Bounded by the command itself: one that fails before it reaches the bridge raises its error here, never a hang.
        reaching = asyncio.create_task(entered.wait())
        done, _ = await asyncio.wait({reaching, client_task}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        if client_task in done:
            reaching.cancel()
            client_task.result()  # its own failure, said
            raise AssertionError('the start finished without reaching the bridge')
        assert entered.is_set(), 'the start reached neither the bridge nor an end within 10 s'
        client_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await client_task
        release.set()
        reply = await commands.onboarding(app, viewer, {'action': 'start'})
        sid = reply['onboarding']['session']
        assert app.day.sid == sid and app.day.on_stage == reply['onboarding']['asset']['id']
        assert len([r for r in app.day.catalogue.list() if r.name == 'Example: Human cells']) == 1
    asyncio.run(go())


def test_failed_progress_write_recovers_saved_result_without_second_analysis(example_app, monkeypatch):
    from orchestrator import commands, onboarding
    app, viewer = example_app
    async def go():
        state = (await commands.onboarding(app, viewer, {'action': 'start'}))['onboarding']
        sid = state['session']
        write = onboarding._write
        def no_space(path, value):
            if value.get('result'):
                raise OSError('synthetic disk full')
            write(path, value)
        monkeypatch.setattr(onboarding, '_write', no_space)
        with pytest.raises(OSError, match='disk full'):
            await commands.onboarding(app, viewer, {'action': 'analyze', 'session': sid})
        assert len(app.hub.evidence.items) == 1
        saved = next(iter(app.hub.evidence.items))
        monkeypatch.setattr(onboarding, '_write', write)
        # Simulate losing controller memory, preserving ordinary persisted state.
        app._onboarding = None
        reply = await commands.onboarding(app, viewer, {'action': 'analyze', 'session': sid})
        assert reply['onboarding']['result']['id'] == saved
        assert len(app.hub.evidence.items) == 1
    asyncio.run(go())


def test_result_acknowledgement_cannot_advance_another_session_or_revision(example_app):
    from orchestrator import commands
    app, viewer = example_app
    async def go():
        state = (await commands.onboarding(app, viewer, {'action': 'start'}))['onboarding']
        sid = state['session']
        await commands.onboarding(app, viewer, {'action': 'analyze', 'session': sid})
        with pytest.raises(Exception, match="not the walkthrough"):
            await commands.onboarding(app, viewer, {'action': 'measurements', 'session': sid, 'result': {'id': 'e999', 'revision': 0}})
        assert (await commands.onboarding(app, viewer, {'action': 'status'}))['onboarding']['step'] == 'measurements'
    asyncio.run(go())


@pytest.mark.parametrize('change', ['session', 'source'])
def test_analysis_rechecks_original_owner_after_input_read(example_app, monkeypatch, tmp_path, change):
    from orchestrator import commands
    from pathlib import Path
    import threading
    app, viewer = example_app
    entered, release = threading.Event(), threading.Event()
    read = Path.read_bytes
    async def go():
        state = (await commands.onboarding(app, viewer, {'action': 'start'}))['onboarding']
        sid, aid = state['session'], state['asset']['id']
        image = app.hub.library.get(aid).path
        def delayed(path):
            if path == image:
                entered.set()
                assert release.wait(HANG_S)
            return read(path)
        monkeypatch.setattr(Path, 'read_bytes', delayed)
        task = asyncio.create_task(commands.onboarding(app, viewer, {'action': 'analyze', 'session': sid}))
        assert await asyncio.to_thread(entered.wait, HANG_S)
        if change == 'session':
            latest = app.new_session('My newer session')['session']
        else:
            other = tmp_path / 'other.png'
            other.write_bytes(read(image))
            latest = sid
            other_id = app.hub.library.register('other', other, title='Other specimen', at=0, origin=None)
            await commands.source(app, viewer, {'open': other_id, 'session': sid})
        release.set()
        with pytest.raises(Exception, match='no longer open|example image'):
            await task
        assert app.day.sid == latest
        assert not app.hub.evidence.items
        assert not app.day.elsewhere(sid).evidence
    asyncio.run(go())


def test_cannot_finish_before_measurements_or_after_skip(example_app):
    from orchestrator import commands
    app, viewer = example_app
    async def go():
        state = (await commands.onboarding(app, viewer, {'action': 'start'}))['onboarding']
        sid = state['session']
        state = (await commands.onboarding(app, viewer, {'action': 'analyze', 'session': sid}))['onboarding']
        ack = {'session': sid, 'result': state['result']}
        with pytest.raises(Exception, match='measurements'):
            await commands.onboarding(app, viewer, {'action': 'finish', **ack})
        await commands.onboarding(app, viewer, {'action': 'measurements', **ack})
        await commands.onboarding(app, viewer, {'action': 'dismiss', 'session': sid})
        with pytest.raises(Exception, match='active'):
            await commands.onboarding(app, viewer, {'action': 'finish', **ack})
    asyncio.run(go())


def test_progress_fsync_does_not_block_the_live_loop(example_app, monkeypatch):
    from orchestrator import commands, onboarding
    import threading
    app, viewer = example_app
    entered, release = threading.Event(), threading.Event()
    write = onboarding._write
    loop_thread = threading.get_ident()
    def slow(path, value):
        assert threading.get_ident() != loop_thread, 'durable progress write ran on live loop'
        entered.set()
        assert release.wait(HANG_S)
        write(path, value)
    monkeypatch.setattr(onboarding, '_write', slow)
    async def go():
        task = asyncio.create_task(commands.onboarding(app, viewer, {'action': 'start'}))
        assert await asyncio.to_thread(entered.wait, HANG_S)
        release.set()
        assert (await task)['ok']
    asyncio.run(go())



@pytest.mark.parametrize('hidden', [False, True])
def test_native_result_requires_loaded_visible_picture_and_retry_reuses_result(example_app, monkeypatch, hidden):
    from orchestrator import commands, screen
    app, viewer = example_app
    monkeypatch.setattr(screen, 'CONFIRM_S', .05)
    hear = viewer.hear
    def unloaded(message):
        hear(message)
        if message.get('type') == 'magnifier':
            # The normal screen seam reports the requested URL, but no decoded pixels.
            if hidden:
                viewer.items['magnifier']['visible'] = False
            else:
                viewer.items['magnifier']['pictures'] = []
    viewer.hear = unloaded
    async def go():
        state = (await commands.onboarding(app, viewer, {'action': 'start'}))['onboarding']
        sid = state['session']
        with pytest.raises(Exception, match='retained picture|not shown'):
            await commands.onboarding(app, viewer, {'action': 'analyze', 'session': sid})
        status = (await commands.onboarding(app, viewer, {'action': 'status'}))
        assert status['rendered'] is None
        result = status['onboarding']['result']
        assert result and len(app.hub.evidence.items) == 1
        viewer.hear = hear
        viewer.items['magnifier']['visible'] = True
        retried = await commands.onboarding(app, viewer, {'action': 'analyze', 'session': sid})
        assert retried['rendered'] == {'session': sid, **result}
        assert len(app.hub.evidence.items) == 1
    asyncio.run(go())


@pytest.mark.parametrize('legacy', [False, True, 'missing'])
def test_example_source_is_playable_by_actual_bridge_roots_and_old_pointer_recovers(example_app, monkeypatch, legacy):
    from orchestrator import commands, onboarding, examples
    from types import SimpleNamespace
    from pathlib import Path
    from urllib.parse import parse_qs, urlsplit
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'experiments/iphone-camera-feed/scripts'))
    from bridge_feed import asset_file
    app, viewer = example_app
    roots = SimpleNamespace(media_root=[str(app.paths.snapshots), str(app.paths.results)],
                            datasets_dir=str(app.paths.datasets), footage_dir=None)
    played = []
    async def bridge(app, method, path):
        query = parse_qs(urlsplit(path).query)
        if query.get('source') == ['asset']:
            played.append(asset_file(roots, query['path'][0])[0])
        return {'ok': True}
    monkeypatch.setattr(commands, '_bridge', bridge)
    async def go():
        old_sid = old_aid = None
        old = app.paths.home / 'onboarding/human_mitosis.png'
        if legacy:
            old.parent.mkdir(parents=True, exist_ok=True)
            old.write_bytes(examples.human_mitosis(preview=True).read_bytes())
            old_aid = app.hub.library.register('onboarding:' + onboarding.GUIDE, old, title='Human cells', at=0,
                                               origin=None, group='example')
            old_sid = app.new_session(onboarding.NAME, local_only=True, guide=onboarding.GUIDE)['session']
            await onboarding.of(app).save(session=old_sid, assetId=old_aid, status='active')
            if legacy == 'missing':
                old.unlink()
        state = (await commands.onboarding(app, viewer, {'action': 'start'}))['onboarding']
        assert played and app.paths.snapshots.resolve() in played[-1].parents
        assert played[-1].read_bytes() == examples.human_mitosis(preview=True).read_bytes()
        if legacy:
            assert state['session'] == old_sid and state['asset']['id'] == old_aid
            assert old.is_file() == (legacy != 'missing')  # Never delete an existing old file.
        resumed = (await commands.onboarding(app, viewer, {'action': 'start'}))['onboarding']
        assert resumed['session'] == state['session'] and resumed['asset']['id'] == state['asset']['id']
        from orchestrator.app import App
        rebuilt = App.build('http://127.0.0.1:1', app.paths, use_gpu=False)
        assert rebuilt.day.sid == state['session']
        assert rebuilt.hub.library.get(state['asset']['id']).path == played[-1]
    asyncio.run(go())


def test_missing_legacy_file_recovery_keeps_existing_result_and_trash_intent(example_app):
    from orchestrator import commands
    app, viewer = example_app
    async def go():
        state = (await commands.onboarding(app, viewer, {'action': 'start'}))['onboarding']
        sid, aid = state['session'], state['asset']['id']
        state = (await commands.onboarding(app, viewer, {'action': 'analyze', 'session': sid}))['onboarding']
        result = state['result']
        old = app.paths.home / 'onboarding/human_mitosis.png'
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(app.hub.library.get(aid).path.read_bytes())
        app.hub.library.relocate_registered(aid, old)
        old.unlink()
        state = (await commands.onboarding(app, viewer, {'action': 'start'}))['onboarding']
        assert state['session'] == sid and state['asset']['id'] == aid and state['result'] == result
        assert len(app.hub.evidence.items) == 1
        app.hub.library.trash(aid)
        with pytest.raises(Exception):
            await commands.onboarding(app, viewer, {'action': 'start'})
        assert aid in app.hub.library.trashed()
        assert app.hub.evidence.get(result['id']) is not None
    asyncio.run(go())
