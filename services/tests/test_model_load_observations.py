"""Scripted timing transport, not measured performance or model qualification."""
import asyncio
import json
from types import SimpleNamespace

from sim import harness


def test_failed_model_load_retains_each_existing_phase_sample_without_extra_polling(monkeypatch):
    clock = [1000.0]
    real_sleep = asyncio.sleep

    async def advance(seconds):
        clock[0] += seconds
        await real_sleep(0)

    monkeypatch.setattr(harness, 'time', SimpleNamespace(time=lambda: clock[0], monotonic=lambda: clock[0],
                                                       perf_counter=lambda: clock[0]))
    monkeypatch.setattr(harness.asyncio, 'sleep', advance)
    monkeypatch.setattr(harness, '_interactions', lambda *_: [])
    monkeypatch.setattr(harness.licences, 'edition', lambda: 'open')
    monkeypatch.setattr(harness.os, 'cpu_count', lambda: 1)
    monkeypatch.delenv('SIM_MODEL_LOAD_PHASES', raising=False)
    monkeypatch.delenv('SIM_SETTINGS', raising=False)
    monkeypatch.delenv('SIM_BUSY_PER_CORE', raising=False)
    processes = []

    class Process:
        def __init__(self):
            self.killed = self.waited = False
            processes.append(self)

        def kill(self):
            self.killed = True

        def wait(self):
            self.waited = True

    monkeypatch.setattr(harness, 'busy_process', Process)

    class Stack:
        orch_url = 'http://unused'

        def __init__(self):
            self.health = []
            self.bridge = []
            self.http = self

        async def wait(self, *_):
            return True, {}

        async def orch_health(self):
            result = {'latestFrameId': len(self.health), 'skipped': len(self.health) * 2,
                      'connectCount': 3, 'lastError': 'scripted reconnect', 'follow': {'steps': len(self.health)}}
            self.health.append(result)
            return result

        async def bridge_health(self):
            result = {'frameCount': len(self.bridge) * 300}
            self.bridge.append(result)
            return result

        async def get(self, _):
            return SimpleNamespace(json=lambda: {'top': []})

    class Viewer:
        def __init__(self):
            self.results = []
            self.samples = []
            self.commands = []

        def latest(self):
            return clock[0], {'id': 1}, b'unretained pixels'

        async def result_for(self, *_):
            return {'ok': True}

        async def send(self, command):
            self.commands.append(command)
            self.results.append({'requestId': command.get('requestId'), 'ok': True})

        def frames_since(self, start):
            # A >33ms analysis tail, missing source sequence, and receipt delay
            # must survive exactly; summaries cannot reconstruct these samples.
            frames = [(start + .020, {'id': 1, 'seq': 10, 'boot': 'a', 't': start, 'an': start + .016}, b'pixels'),
                      (start + .200, {'id': 2, 'seq': 15, 'boot': 'a', 't': start + .040,
                                      'an': start + .180}, b'pixels')]
            self.samples.append(frames)
            return frames

    stack, viewer = Stack(), Viewer()
    out = asyncio.run(harness._model_load(stack, viewer, [{'x': 1, 'y': 2}, {'x': 3, 'y': 4}]))
    assert not out.passed  # The slow synthetic frames must still fail the existing gate.
    saved = json.loads(json.dumps(out.observations))
    assert [phase['phase'] for phase in saved] == ['baseline', 'outside load', 'test load',
                                                 'sam2.1-tiny loading', 'all at once']
    observed_health = []
    for phase, frames in zip(saved, viewer.samples, strict=True):
        assert phase['frames'] == [{'receivedAt': t, 'header': header} for t, header, _ in frames]
        assert phase['healthBefore']['connectCount'] == phase['healthAfter']['connectCount'] == 3
        assert phase['healthAfter']['skipped'] > phase['healthBefore']['skipped']
        assert phase['healthAfter']['lastError'] == 'scripted reconnect'
        assert len(phase['clickMs']) == 12
        observed_health.extend([phase['healthBefore'], *phase['healthSamples'], phase['healthAfter']])
    assert observed_health == stack.health[:-1]  # Final preload check is the only other health request.
    assert len(stack.bridge) == 10  # Same before/after requests, no observation-only polling.
    assert len([c for c in viewer.commands if c['type'] == 'click']) == 64
    assert all(p.killed and p.waited for p in processes)
