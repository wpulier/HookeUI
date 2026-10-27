"""Synthetic admission checks; these do not qualify model latency."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from sim import harness


def test_noncloud_stack_cannot_inherit_provider_file_keychain_or_extension(monkeypatch, tmp_path):
    from orchestrator.keys import KNOWN_KEYS, APPLIED, resolve
    from orchestrator.paths import Paths
    file = tmp_path / 'private-keys.txt'
    file.write_text('OPENAI_API_KEY=synthetic-file-secret\n')
    inherited = {name: 'synthetic-environment-secret' for name in KNOWN_KEYS}
    inherited.update(MICROMIND_KEYS_FILE=str(file), MICROMIND_KEYCHAIN='on',
                     MICROMIND_APPLIED_KEYS='OPENAI_API_KEY', HOOKEUI_RESEARCH_EXTENSION='synthetic_extension',
                     MICROMIND_CLICK_DIAGNOSTICS='1', MICROMIND_HOME='/private-user-profile')
    env = harness.simulation_environment(inherited, Paths.under(tmp_path / 'run'), cloud=False)
    assert not any(name in env for name in (*KNOWN_KEYS, APPLIED, 'HOOKEUI_RESEARCH_EXTENSION'))
    assert env['MICROMIND_KEYCHAIN'] == 'off'
    assert env['MICROMIND_KEYS_FILE'] != str(file)
    assert not Path(env['MICROMIND_KEYS_FILE']).exists()
    assert env['MICROMIND_CLICK_DIAGNOSTICS'] == '1'
    assert env['MICROMIND_HOME'] == str(tmp_path / 'run')
    monkeypatch.setenv('MICROMIND_KEYCHAIN', env['MICROMIND_KEYCHAIN'])
    assert not any(key.present for key in resolve(env['MICROMIND_KEYS_FILE'], env).values())
    assert file.read_text() == 'OPENAI_API_KEY=synthetic-file-secret\n'


def test_explicit_cloud_opt_in_keeps_credentials_but_not_user_saved_data(tmp_path):
    from orchestrator.paths import Paths
    env = harness.simulation_environment({'OPENAI_API_KEY': 'synthetic', 'MICROMIND_HOME': '/user',
                                         'MICROMIND_KEYS_FILE': '/explicit/test-keys'}, Paths.under(tmp_path), cloud=True)
    assert env['OPENAI_API_KEY'] == 'synthetic'
    assert env['MICROMIND_KEYS_FILE'] == '/explicit/test-keys'
    assert env['MICROMIND_HOME'] == str(tmp_path)


def health():
    return {'workers': [{'name': name, 'ready': True, 'hung': '', 'loading': {}, 'resident': resident}
                       for name, resident in [('micromind-gpu', ['edgetam']),
                                              ('micromind-gpu-follow', ['edgetam-video']),
                                              ('micromind-gpu-heavy', ['dinov2-small', 'cellpose-sam'])]],
            'follow': {'steps': 3, 'lastStepAt': 101.0}}


@pytest.mark.parametrize('problem', ['loading', 'missing', 'hung', 'not-ready', 'no-step', 'old-step'])
def test_timing_preparation_requires_actual_residency_and_fresh_follower(monkeypatch, problem):
    monkeypatch.setattr(harness.licences, 'edition', lambda: 'research')
    not_ready = health()
    worker = not_ready['workers'][2]
    if problem == 'loading': worker['loading'] = {'cellpose-sam': 1}
    if problem == 'missing': worker['resident'].remove('cellpose-sam')
    if problem == 'hung': worker['hung'] = 'worker timed out'
    if problem == 'not-ready': worker['ready'] = False
    if problem == 'no-step': not_ready['follow']['steps'] = 0
    if problem == 'old-step': not_ready['follow']['lastStepAt'] = 99.0
    observed = []
    async def wait(predicate, timeout, what):
        assert timeout == 120
        observed.extend([predicate(not_ready), predicate(health())])
        return observed[-1], 4.5
    out = harness.Outcome('synthetic')
    assert asyncio.run(harness.prepare_timing(SimpleNamespace(wait=wait), out, following_since=100.0))
    assert observed == [False, True]
    assert out.checks[0].ok
    assert out.observations[0]['kind'] == 'timing-preparation'
    assert out.observations[0]['elapsedS'] == 4.5


def test_failed_preparation_remains_a_failed_check(monkeypatch):
    async def wait(*args, **kwargs): return False, 120.0
    out = harness.Outcome('synthetic')
    assert not asyncio.run(harness.prepare_timing(SimpleNamespace(wait=wait), out))
    assert not out.checks[0].ok


def test_owned_stack_passes_isolation_to_both_children(monkeypatch, tmp_path):
    from orchestrator.keys import KNOWN_KEYS
    for name in KNOWN_KEYS:
        monkeypatch.setenv(name, "synthetic-secret")
    monkeypatch.setenv("MICROMIND_KEYS_FILE", "/synthetic/private-keys")
    monkeypatch.setattr(harness, "MEDIA_COPIES", 0)
    monkeypatch.setattr(harness, "INTERACTIONS", tmp_path / "unused")
    children = []
    def start(**kwargs):
        children.append(kwargs)
        kwargs['stdout'].close()
        return SimpleNamespace()
    monkeypatch.setattr(harness.subprocess, "Popen", start)
    async def run():
        stack = harness.Stack(tmp_path / "video.mp4", 1, 2, 0, False)
        stack.log_dir = tmp_path / "logs"
        async def reachable(_): return False
        async def ready(*args, **kwargs): return True, 0.0
        stack._reachable, stack.wait = reachable, ready
        try:
            await stack.__aenter__()
        finally:
            await stack.http.aclose()
        assert len(children) == 2
        for child in children:
            assert not any(key in child['env'] for key in KNOWN_KEYS)
            assert child['env']['MICROMIND_KEYCHAIN'] == 'off'
            assert child['env']['MICROMIND_HOME'] == stack.home
            assert child['env']['MICROMIND_KEYS_FILE'] == str(Path(stack.home) / 'no-provider-keys')
    asyncio.run(run())


def test_cloud_scenario_requires_opt_in_before_starting_children(monkeypatch, tmp_path, capsys):
    video = tmp_path / 'synthetic.mp4'
    video.touch()
    monkeypatch.setattr(harness.sys, 'argv', ['sim.harness', '--video', str(video), '--only', 'ask'])
    def start(*args, **kwargs):
        pytest.fail('must reject before starting children')
    monkeypatch.setattr(harness, 'Stack', start)
    with pytest.raises(SystemExit, match='2'):
        asyncio.run(harness.main())
    assert 'require explicit --cloud' in capsys.readouterr().err


def test_follow_clicks_retains_slow_samples_and_diagnostics_after_measurement(monkeypatch):
    from sim import click_diagnostics
    clock = [100.0]
    requests = []
    monkeypatch.setattr(harness, 'time', SimpleNamespace(time=lambda: clock[0], perf_counter=lambda: clock[0]))
    async def sleep(seconds): clock[0] += seconds
    monkeypatch.setattr(harness.asyncio, 'sleep', sleep)
    admissions = []
    async def prepare(*args, **kwargs):
        admissions.append(len(requests))
        if kwargs:
            assert len(requests) == 4
            assert kwargs['following_since'] == clock[0]
        else:
            assert not requests
        return True
    monkeypatch.setattr(harness, 'prepare_timing', prepare)
    monkeypatch.setattr(harness, '_interactions', lambda *args, **kwargs: [{'error': None}])
    monkeypatch.setenv('MICROMIND_CLICK_DIAGNOSTICS', '1')
    async def collect(stack, rows, start, end):
        assert len(requests) == 14  # Only after all seed and measured clicks.
        assert start <= rows[0]['sentAt'] < rows[-1]['receivedAt'] <= end
        return {'clicks': rows, 'receipts': [{'id': 'synthetic'}]}
    monkeypatch.setattr(click_diagnostics, 'collect', collect)
    async def wait(*args, **kwargs): return True, 0.0
    class Viewer:
        candidates = [{'x': n, 'y': n} for n in range(4)]
        results = []
        trails = {'trails': [{'id': 'synthetic', 'segments': [{'points': [[0, 0]]}]}]}
        def latest(self): return clock[0], {'id': len(requests), 'fov': None}, b''
        async def send(self, request): requests.append(request)
        async def result_for(self, *args):
            clock[0] += .4 if len(requests) == 5 else .1
            return {'ok': True}
    out = asyncio.run(harness.scenario_follow_clicks(SimpleNamespace(wait=wait), Viewer()))
    assert admissions == [0, 4]
    timing = next(c for c in out.checks if c.what.startswith('click p95'))
    assert not timing.ok  # A slow sample stays a failure, regardless of preparation.
    observed = next(o for o in out.observations if o.get('kind') == 'follow-clicks')
    assert [c['roundTripMs'] for c in observed['clicks']] == [400.0] + [100.0] * 9
    assert observed['receipts'] == [{'id': 'synthetic'}]
