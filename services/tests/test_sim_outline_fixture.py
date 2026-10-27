"""The load/collection fixture holds labelled real inputs without stopping frame delivery."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sim import harness


@pytest.fixture()
def labelled_recording(tmp_path, monkeypatch):
    # Metadata stand-in for the pause/seek protocol; no real pixels or model result is claimed.
    from orchestrator import assets

    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'backup': {}, 'real': [{
        'id': harness.DEFAULT_VIDEO_ID, 'file': 'footage/real/synthetic-labelled-source.mp4',
    }], 'bench': []}))
    monkeypatch.setattr(assets, 'MANIFEST', manifest)
    return harness.default_video()


def test_labelled_input_is_held_before_seek_and_playback_restored_on_failure(labelled_recording):
    async def check():
        events = []

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {'playback': {'paused': False}, 'folder': str(labelled_recording.parent),
                        'footage': labelled_recording.name}

        class Http:
            async def get(self, url):
                return Response()

            async def post(self, url, params):
                events.append(('pause', params['paused']))
                return Response()

        class Stack:
            video = labelled_recording
            http = Http()
            bridge_url = 'http://bridge'

            async def seek(self, frame):
                events.append(('seek', frame))
                return True

            async def sim_status(self):
                return {'latestBridgeFrame': 40}

        viewer = SimpleNamespace(latest=lambda: (1, {'id': 5, 'seq': 42, 'tracked': True,
                                                       'steady': True, 'w': 1280, 'h': 720}, b'pixels'))
        from sim.outline_fixture import held_outlines

        with pytest.raises(RuntimeError, match='scenario failed'):
            async with held_outlines(Stack(), viewer) as targets:
                # Independent hand labels in bench/questions.json, not candidate detector output.
                assert targets == [{'x': 853, 'y': 387}, {'x': 667, 'y': 496}]
                assert events == [('pause', 1), ('seek', 360)]
                raise RuntimeError('scenario failed')
        assert events[-1] == ('pause', 0)

    asyncio.run(check())


def test_labelled_coordinates_are_not_applied_to_an_unrelated_recording(labelled_recording):
    async def check():
        from sim.outline_fixture import held_outlines

        stack = SimpleNamespace(video=Path('/unrelated.mp4'))
        with pytest.raises(ValueError, match='labelled leaf recording'):
            async with held_outlines(stack, None):
                pytest.fail('unrelated pixels received known coordinates')

    asyncio.run(check())


def test_labelled_coordinates_are_not_applied_while_the_bridge_plays_another_file(labelled_recording, tmp_path):
    """The labels are of the labelled recording's frame 360: the stack started with it is not enough when an earlier
    scenario left another file playing; nothing is paused or seeked then."""
    async def check():
        from sim.outline_fixture import held_outlines

        other = tmp_path / 'other.mp4'
        other.write_bytes(b'not the labelled recording')
        posted = []

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {'playback': {'paused': False}, 'folder': str(tmp_path), 'footage': other.name}

        class Http:
            async def get(self, url):
                return Response()

            async def post(self, url, params):
                posted.append(params)
                return Response()

        stack = SimpleNamespace(video=labelled_recording, http=Http(), bridge_url='http://bridge')
        with pytest.raises(ValueError, match='labelled leaf recording playing'):
            async with held_outlines(stack, None):
                pytest.fail('known coordinates were given to another file')
        assert posted == []

    asyncio.run(check())


def test_model_load_keeps_its_measured_phases_when_putting_the_view_back_fails(monkeypatch):
    """Putting the held view back failing (the bridge gone at cleanup) is one more failed check beside the phases
    already measured, never in their place."""
    from contextlib import asynccontextmanager
    from sim import outline_fixture

    @asynccontextmanager
    async def held(stack, viewer):
        yield [{'x': 1, 'y': 2}]
        raise ConnectionError('bridge gone')

    async def measured(stack, viewer, spots):
        out = harness.Outcome('nothing freezes while a model loads')
        out.check('baseline: click p95 under 300 ms', True, 'p95 200 ms')
        return out

    monkeypatch.setattr(outline_fixture, 'held_outlines', held)
    monkeypatch.setattr(harness, '_model_load', measured)
    out = asyncio.run(harness.scenario_model_load(None, None))
    assert [(c.what, c.ok) for c in out.checks] == [('baseline: click p95 under 300 ms', True),
                                                    ('the held view was put back', False)]


@pytest.mark.parametrize('drift', [0, 1])
def test_footage_switch_says_a_restore_the_bridge_did_not_answer_as_its_own_failed_check(monkeypatch, drift):
    """Putting back the feed it found failing (the bridge gone at the end) is that one check failing, beside the switches
    and seeks already measured, never in their place."""
    import httpx

    async def no_wait(_seconds):
        pass

    played = {'footage': 'leaf.mp4', 'folder': '/footage', 'playback': {'seconds': 3.0, 'paused': False}}

    class Response:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    class Http:
        async def get(self, url, **_):
            if url.endswith('/assets/library'):
                return Response({'items': [{'name': n} for n in ('a.mp4', 'b.mp4', 'c.mp4')]})
            return Response({**played, 'playback': {**played['playback'], 'seconds': played['playback']['seconds'] + drift}})

        async def post(self, url, params=None, timeout=None):
            if url == 'http://bridge/feed':  # the restore, after every switch
                raise httpx.ConnectError('bridge gone')
            if 'footage=' in url:
                return Response({'footage': url.rsplit('=', 1)[1]})
            if '/feed/seek' in url:
                return Response({'playback': {'seconds': 12.0, 'paused': False}})
            paused = (params or {}).get('paused') == 1
            played['playback'] = {'seconds': 12.0, 'paused': paused}
            return Response({'playback': played['playback']})

    async def live(*_args, **_kw):
        return True, 0.0

    async def health():
        return {'state': 'live'}

    stack = SimpleNamespace(bridge_url='http://bridge', http=Http(), wait=live, orch_health=health)
    viewer = SimpleNamespace(frames_since=lambda t: [(t + i * 0.02, {}, b'') for i in range(100)])
    monkeypatch.setattr(harness.asyncio, 'sleep', no_wait)
    out = asyncio.run(harness.scenario_footage_switch(stack, viewer))
    assert [c.what for c in out.checks if not c.ok] == (['paused: the position holds'] if drift else []) + ['and the feed goes back to what it was']
    assert 'bridge gone' in out.checks[-1].detail
    assert len(out.checks) == 11


@pytest.mark.parametrize('resident,preloads,unloads,expected', [
    (True, [{'ok': True, 't': 10}], [], True),
    (True, [], [], False),
    (True, [{'ok': False, 't': 10}], [], False),
    (False, [{'ok': True, 't': 10}], [], False),
    (False, [{'ok': True, 't': 10}], [{'ok': True, 't': 11, 'for': 'pressure'}], True),
    (False, [{'ok': True, 't': 10}], [{'ok': True, 't': 9, 'for': 'pressure'}], False),
    (False, [{'ok': True, 't': 10}], [{'ok': False, 't': 11, 'for': 'pressure'}], False),
    (False, [{'ok': True, 't': 10}], [{'ok': True, 't': 11, 'for': 'test'}], False),
])
def test_preload_requires_success_and_explains_any_later_absence(resident, preloads, unloads, expected):
    stamp = lambda r: {'worker': 'heavy', 'model': 'cells', **r}
    assert harness.preload_accounted_for('heavy', 'cells', {'heavy': {'ready': True, 'resident': ['cells'] if resident else []}},
                                        list(map(stamp, preloads)), list(map(stamp, unloads)), []) is expected


def test_another_models_pressure_eviction_cannot_explain_missing_default():
    preload = {'worker': 'heavy', 'model': 'cells', 'ok': True, 't': 10}
    unload = {'worker': 'heavy', 'model': 'other', 'ok': True, 't': 11, 'for': 'pressure'}
    assert not harness.preload_accounted_for('heavy', 'cells', {'heavy': {'ready': True}}, [preload], [unload], [])


@pytest.mark.parametrize('healthy,reload_at', [(False, 0), (True, 12)])
def test_historical_eviction_does_not_hide_worker_failure_or_loss_after_reload(healthy, reload_at):
    model = {'worker': 'heavy', 'model': 'cells'}
    preloads = [{**model, 'ok': True, 't': 10}]
    unloads = [{**model, 'ok': True, 't': 11, 'for': 'pressure'}]
    loads = [{**model, 'state': 'loaded', 't': reload_at}] if reload_at else []
    assert not harness.preload_accounted_for('heavy', 'cells', {'heavy': {'ready': healthy, 'resident': []}},
                                            preloads, unloads, loads)


def test_follower_evidence_excludes_prior_scenarios(tmp_path, monkeypatch):
    log = tmp_path / 'interactions.jsonl'
    previous = {'kind': 'object-follow', 't': 1, 'error': 'another session was opened since the request was made'}
    measured = {'kind': 'object-follow', 't': 3, 'ms': 20}
    log.write_text('\n'.join(json.dumps(r) for r in [previous, measured]))
    monkeypatch.setattr(harness, 'INTERACTIONS', log)
    assert harness._interactions('object-follow', after=2) == [measured]
    assert harness._interactions('object-follow') == [previous, measured]
