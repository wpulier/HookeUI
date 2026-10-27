"""Synthetic boundary records: production Activity/execution paths, no provider calls."""
from types import SimpleNamespace
import json
from orchestrator import activity, execution
from orchestrator.day.model import Asked
from orchestrator.execution_export import redacted
from orchestrator.voice.diagnostics import VoiceDiagnostics, CLIENT_STAGES, SERVER_STAGES


def test_voice_observations_are_bounded_owned_and_redacted(monkeypatch):
    rows = []
    monkeypatch.setattr(activity, 'activity', lambda *a, **kw: rows.append((a, kw)))
    log = execution.ExecutionLog()
    hub = SimpleNamespace(execution=log)
    asked = Asked('original-thread', 3, day='original-project', session='original-project')
    diagnostics = VoiceDiagnostics(hub, asked, 'gpt-live-1')
    for _ in range(5):
        for stage in CLIENT_STAGES:
            diagnostics.record(stage, client=True)
        for stage in SERVER_STAGES:
            diagnostics.record(stage)
        diagnostics.record('https://secret.example/?key=private-secret', client=True)
        diagnostics.record({'token': 'private-secret'}, client=True)
        diagnostics.record('ready', client=True)  # browser cannot claim provider acknowledgement
    assert len(rows) == len(CLIENT_STAGES) + len(SERVER_STAGES)
    assert all(kw['asked'] is asked for _, kw in rows)
    assert len({kw['id'] for _, kw in rows}) == 1
    assert all(len(kw['details']['lines']) <= 8 for _, kw in rows)
    assert 'private-secret' not in json.dumps(rows, default=str)
    for root in log._roots.values():
        assert root.session == 'original-project'
        assert root.thread == 'original-thread'
        data = redacted(log.inspect(root.id))
        assert data['steps'][0]['sessionId'] != diagnostics.id
        assert data['steps'][0]['phase'] in CLIENT_STAGES | SERVER_STAGES
        assert 'original-project' not in json.dumps(data)


def test_voice_failure_remains_failed_after_microphone_updates_and_end(monkeypatch):
    rows = []
    monkeypatch.setattr(activity, 'activity', lambda *a, **kw: rows.append((a, kw)))
    diagnostics = VoiceDiagnostics(SimpleNamespace(), None, 'gpt-live-1')
    diagnostics.record('error')
    diagnostics.record('signal', client=True)
    diagnostics.record('ended')
    assert rows[-1][1]['state'] == 'failed'
    assert 'failure' in rows[-1][0][2]
    assert 'Talk ended' in rows[-1][1]['details']['lines'][-1]


def test_credit_refusal_has_specific_retained_recovery_without_provider_text(monkeypatch):
    rows = []
    monkeypatch.setattr(activity, 'activity', lambda *a, **kw: rows.append((a, kw)))
    diagnostics = VoiceDiagnostics(SimpleNamespace(), None, 'gpt-live-1')
    diagnostics.provider_error({'message': 'You have no credits remaining. Add credits at https://private.example/?secret=sentinel-private'})
    diagnostics.record('error')  # a later transport close cannot erase the specific cause
    diagnostics.record('ended')
    assert rows[-1][1]['state'] == 'failed'
    assert 'API credit' in rows[-1][0][2]
    assert 'sentinel-private' not in json.dumps(rows, default=str)
    assert 'private.example' not in json.dumps(rows, default=str)
