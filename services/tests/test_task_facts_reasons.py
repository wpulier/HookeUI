"""Readiness facts retain their structured causes; synthetic resolver-output fixtures."""
import pytest

from orchestrator import readiness, task_facts


@pytest.mark.parametrize('status,kind,reason', [
    ('unavailable', 'edition', 'edition-restriction'),
    ('unavailable', 'platform', 'unavailable-hardware'),
    ('unavailable', 'route', 'missing-execution-route'),
    ('broken', 'check', 'broken-installation'),
    ('unavailable', '', 'unavailable'),
])
def test_authoritative_readiness_cause_is_preserved(status, kind, reason):
    steps = (readiness.Step(kind, 'the authoritative explanation'),) if kind else ()
    row = {'key': 'test', **readiness.Readiness(status, 'Unavailable', steps=steps).wire()}
    result = task_facts.blockers(have_input=True, calibrated=True, needs_calibration=False, camera=False, candidates=[row])
    assert result['blockers'][0]['reason'] == reason
    kept = result['blockers'][0]['capabilities'][0]
    assert kept['status'] == status and kept['steps'] == row['steps']
    assert kept['next'] == row['next']
