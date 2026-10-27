"""Actual local storage and ExecutionLog; no models, camera or browser claims."""
import json

from orchestrator.execution import ExecutionLog, span
from orchestrator.execution_archive import ExecutionArchive


def test_execution_survives_restart_and_memory_eviction(tmp_path):
    archive = ExecutionArchive(tmp_path)
    log = ExecutionLog(enabled=True, max_roots=1, archive=archive)
    settings = {'vision': 'first-model'}
    log.context_provider = lambda: {'build': {'revision': 'original-build'}, 'settings': dict(settings)}
    with log.request('ask', day='2026-10-10', thread='conversation') as original:
        with span('fetch', provider='Google'):
            pass
    with log.request('click'):
        pass
    settings['vision'] = 'replacement-model'
    archive.flush()
    assert log.inspect(original.id)['steps'][0]['operation'] == 'fetch'
    archive.close()
    restored = ExecutionArchive(tmp_path)
    try:
        other = ExecutionLog(enabled=False, archive=restored)
        detail = other.inspect(original.id)
        assert detail['durable'] and detail['root']['thread'] == 'conversation'
        assert detail['root']['outcome'] == 'completed'
        assert detail['context']['build']['revision'] == 'original-build'
        assert detail['context']['settings']['vision'] == 'first-model'
        assert restored.references('conversation')[0]['id'] == original.id
        events = [json.loads(line) for line in (tmp_path/'events'/f'{original.id}.jsonl').read_text().splitlines()]
        assert events[0]['snapshot']['root']['outcome'] == 'running'
        assert events[-1]['snapshot']['closed']
        assert all(e['schema'] == 1 and e['at'].endswith('+00:00') and e['monoNs'] > 0 for e in events)
        assert len({e['clockDomain'] for e in events}) == 1
    finally:
        restored.close()


def test_torn_tail_and_lost_index_rebuild_preserve_complete_records(tmp_path):
    archive = ExecutionArchive(tmp_path)
    log = ExecutionLog(enabled=True, archive=archive)
    with log.request('ask') as original:
        pass
    archive.flush(); archive.close()
    with (tmp_path/'events'/f'{original.id}.jsonl').open('ab') as stream:
        stream.write(b'{"schema":')
    (tmp_path/'index.sqlite3').unlink()
    restored = ExecutionArchive(tmp_path)
    try:
        assert restored.inspect(original.id)['root']['outcome'] == 'completed'
        assert restored.status()['recoveryGaps'] == 1
    finally:
        restored.close()


def test_logging_switch_keeps_durable_history_and_blind_identity_hidden(tmp_path):
    from orchestrator import execution
    archive = ExecutionArchive(tmp_path)
    log = ExecutionLog(enabled=True, archive=archive)
    try:
        with log.request('ask') as original:
            execution.bind(trial_id='blind')
            with span('name', model='hidden-model'):
                pass
        archive.flush()
        log.set_enabled(False)
        detail = log.inspect(original.id)
        assert detail['status'] == 'blind' and detail['steps'] == []
        assert 'hidden-model' not in json.dumps(detail)
    finally:
        archive.close()


def test_storage_failure_cannot_change_primary_work(tmp_path):
    blocked = tmp_path/'file'; blocked.write_text('not a directory')
    archive = ExecutionArchive(blocked)
    log = ExecutionLog(enabled=True, archive=archive)
    try:
        with log.request('ask') as original:
            with span('fetch'):
                pass
        archive.flush()
        assert log.inspect(original.id)['root']['outcome'] == 'completed'
        assert archive.status()['writeErrors'] > 0
        assert archive.status()['lastError'] in ('NotADirectoryError', 'FileExistsError')
    finally:
        archive.close()


def test_marker_is_idempotent_and_retention_preserves_marked_and_active(tmp_path):
    import pytest
    archive = ExecutionArchive(tmp_path)
    log = ExecutionLog(enabled=True, archive=archive)
    try:
        with log.request('ask') as marked:
            pass
        archive.flush()
        first = archive.mark(marked.id, 'problem-1', 'The image did not appear')
        assert archive.mark(marked.id, 'problem-1', 'The image did not appear') == first
        assert first['recordingStarted'] is False and first['recordings'] == []
        with pytest.raises(ValueError):
            archive.mark(marked.id, 'problem-1', 'changed note')
        archive.max_bytes = 0
        active = log.request('ask')
        archive.flush()
        assert archive.inspect(marked.id) is not None
        assert archive.inspect(active.id) is not None
        assert len(list((tmp_path/'markers').glob('*.json'))) == 1
    finally:
        archive.close()


def test_retention_only_removes_eligible_completed_execution_files(tmp_path):
    archive = ExecutionArchive(tmp_path, retention_days=0)
    log = ExecutionLog(enabled=True, archive=archive)
    unrelated = tmp_path/'conversation.jsonl'; unrelated.write_text('preserve journal')
    try:
        active = log.request('ask')
        with log.request('click') as closed:
            pass
        archive.flush()
        assert archive.inspect(closed.id) is None
        assert archive.inspect(active.id) is not None
        assert unrelated.read_text() == 'preserve journal'
        assert archive.status()['prunedTraces'] == 1
        assert closed.id in (tmp_path/'retention.jsonl').read_text()
    finally:
        archive.close()


def test_live_references_never_read_storage():
    class SlowArchive:
        def offer(self, snapshot):
            return True
        def references(self, *args):
            raise AssertionError('live snapshots must not read disk')
    log = ExecutionLog(enabled=True, archive=SlowArchive())
    with log.request('ask') as request:
        pass
    assert log.references()[0]['id'] == request.id


def test_evicted_inflight_request_keeps_late_outcome(tmp_path):
    archive = ExecutionArchive(tmp_path)
    log = ExecutionLog(enabled=True, max_roots=1, archive=archive)
    try:
        with log.request('ask') as original:
            with span('fetch'):
                with log.request('click'):
                    pass
            original.finish('failed')
        archive.flush()
        detail = archive.inspect(original.id)
        assert detail['closed'] and detail['root']['outcome'] == 'failed'
        assert detail['steps'][0]['outcome'] == 'completed'
        assert log._steps == 0
    finally:
        archive.close()


def test_restart_interruption_is_honest_and_retention_eligible(tmp_path):
    archive = ExecutionArchive(tmp_path)
    log = ExecutionLog(enabled=True, archive=archive)
    request = log.request('ask')
    archive.flush()
    assert not archive.inspect(request.id).get('interrupted')
    archive.close()
    restored = ExecutionArchive(tmp_path)
    try:
        assert restored.inspect(request.id)['interrupted']
        assert restored.inspect(request.id)['root']['outcome'] == 'running'
    finally:
        restored.close()
    pruned = ExecutionArchive(tmp_path, retention_days=0)
    try:
        assert pruned.inspect(request.id) is None
        assert pruned.status()['prunedTraces'] == 1
    finally:
        pruned.close()


def test_disabling_mid_request_makes_stopped_trace_eligible(tmp_path):
    archive = ExecutionArchive(tmp_path)
    log = ExecutionLog(enabled=True, archive=archive)
    try:
        with log.request('ask') as request:
            log.set_enabled(False)
            archive.max_bytes = 0
            archive.flush()
            assert archive.inspect(request.id) is not None
        archive.flush()
        assert archive.inspect(request.id) is None
        log.set_enabled(True)
        with log.request('click'):
            pass
        archive.flush()
        assert archive.inspect(request.id) is None
    finally:
        archive.close()


def test_blind_vote_reveals_evicted_trace_after_restart(tmp_path):
    from orchestrator import execution
    archive = ExecutionArchive(tmp_path)
    log = ExecutionLog(enabled=True, max_roots=1, archive=archive)
    with log.request('ask') as original:
        execution.bind(trial_id='trial-1')
        with span('name', model='revealed-after-vote'):
            pass
    with log.request('click'):
        pass
    log.reveal('trial-1')
    archive.flush(); archive.close()
    restored = ExecutionArchive(tmp_path)
    try:
        detail = ExecutionLog(archive=restored).inspect(original.id)
        assert detail['status'] == 'available'
        assert detail['steps'][0]['model'] == 'revealed-after-vote'
    finally:
        restored.close()


def test_history_pages_pin_membership_and_reject_cursor_after_restart(tmp_path):
    import pytest
    from orchestrator import execution
    archive = ExecutionArchive(tmp_path)
    log = ExecutionLog(enabled=True, archive=archive)
    originals = []
    for i in range(5):
        with log.request('ask', thread='t1') as request:
            execution.bind(words=f'photo request {i}', owner={'workspaceId': 'browser-a'})
            originals.append(request.id)
    archive.flush()
    first = archive.search('photo', limit=2)
    assert len(first['entries']) == 2 and first['nextCursor']
    with log.request('ask'):
        execution.bind(words='new photo request')
    archive.flush()
    entries = first['entries']; cursor = first['nextCursor']
    while cursor:
        page = archive.search('photo', cursor=cursor, limit=2)
        entries += page['entries']; cursor = page['nextCursor']
    assert {e['id'] for e in entries} == set(originals)
    assert all(e['owner']['workspaceId'] == 'browser-a' for e in entries)
    with pytest.raises(ValueError):
        archive.search('changed query', cursor=first['nextCursor'])
    archive.close()
    restored = ExecutionArchive(tmp_path)
    try:
        with pytest.raises(ValueError, match='restart'):
            restored.search('photo', cursor=first['nextCursor'])
        assert len(restored.search('photo')['entries']) == 6
    finally:
        restored.close()


def test_reused_trial_id_cannot_inherit_an_earlier_process_vote(tmp_path):
    from orchestrator import execution
    archive = ExecutionArchive(tmp_path)
    log = ExecutionLog(enabled=True, archive=archive)
    with log.request('ask') as old:
        execution.bind(trial_id='t1')
    log.reveal('t1')
    archive.flush(); archive.close()
    restored = ExecutionArchive(tmp_path)
    log = ExecutionLog(enabled=True, archive=restored)
    try:
        with log.request('ask') as new:
            execution.bind(trial_id='t1')
            with span('name', model='still-blind'):
                pass
        restored.flush()
        assert restored.inspect(old.id)['status'] == 'available'
        assert restored.inspect(new.id)['status'] != 'available'
        assert 'still-blind' not in str(log.inspect(new.id))
    finally:
        restored.close()


def test_archive_is_private_under_a_permissive_umask(tmp_path):
    import os
    import stat
    prior = os.umask(0o022)
    archive = None
    try:
        folder = tmp_path / 'private'
        archive = ExecutionArchive(folder)
        log = ExecutionLog(enabled=True, archive=archive)
        with log.request('ask'):
            pass
        archive.flush()
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
        assert stat.S_IMODE(archive.index.stat().st_mode) == 0o600
        assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in archive.events.iterdir())
    finally:
        if archive:
            archive.close()
        os.umask(prior)


def test_problem_marker_is_in_inspection_after_restart(tmp_path):
    archive = ExecutionArchive(tmp_path)
    log = ExecutionLog(enabled=True, archive=archive)
    with log.request('ask') as request:
        pass
    marker = archive.mark(request.id, 'persistent-marker', 'The photo never arrived')
    assert log.inspect(request.id)['markers'] == [marker]
    archive.close()
    restored = ExecutionArchive(tmp_path)
    try:
        detail = ExecutionLog(enabled=False, archive=restored).inspect(request.id)
        assert detail['markers'] == [marker]
    finally:
        restored.close()


def test_details_off_retains_new_request_ownership_and_outcome_without_spans(tmp_path):
    from orchestrator import execution
    archive = ExecutionArchive(tmp_path)
    log = ExecutionLog(enabled=False, archive=archive)
    try:
        with log.request('ask') as request:
            execution.bind(words='Show that photo', owner={'workspaceId': 'requesting-browser', 'secretGrant': 'never'})
            with execution.span('vision', model='not-recorded'):
                pass
            execution.finish('failed')
        archive.flush()
        detail = log.inspect(request.id)
        assert detail['root']['outcome'] == 'failed' and detail['closed']
        assert detail['steps'] == [] and detail['trackingStopped']
        assert detail['context']['owner'] == {'workspaceId': 'requesting-browser'}
        assert detail['context']['words'] == 'Show that photo'
    finally:
        archive.close()


def test_details_off_production_wrappers_retain_requests_and_isolate_child_outcomes(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from orchestrator import execution, commands
    archive = ExecutionArchive(tmp_path)
    log = ExecutionLog(enabled=False, archive=archive)
    hub = SimpleNamespace(execution=log)
    seen = []
    async def handle(app, client, message):
        seen.append(execution.root_id())
        with execution.span('failed child'):
            execution.finish('failed')
        with execution.span('successful child'):
            execution.finish('completed')
        execution.finish('failed')  # the request boundary decides its actual outcome
    monkeypatch.setattr(commands, '_handle', handle)
    class Worker:
        def __init__(self): self.hub = hub
        @execution.traced('delegate', request='ask')
        async def run(self):
            seen.append(execution.root_id())
            execution.bind(words='Original voice words')
    async def scenario():
        await commands.handle(SimpleNamespace(hub=hub), None, {'type': 'ask', 'text': 'Original typed words'})
        await Worker().run()
    try:
        asyncio.run(scenario())
        archive.flush()
        assert all(seen) and seen[0] != seen[1]
        assert log.inspect(seen[0])['root']['outcome'] == 'failed'
        assert log.inspect(seen[1])['context']['words'] == 'Original voice words'
        assert log.inspect(seen[0])['steps'] == []
    finally:
        archive.close()


def test_usage_counts_occurrences_not_snapshot_updates_and_keeps_history_unknown(tmp_path):
    archive = ExecutionArchive(tmp_path)
    log = ExecutionLog(enabled=True, archive=archive)
    try:
        with log.request('ask'):
            with span('tool:reference_photo'):
                pass
            with span('tool:reference_photo') as failed:
                failed.finish('failed')
        with log.request('ask'):
            from orchestrator import execution
            execution.bind(trial_id='unrevealed')
            with span('tool:private-alternative'):
                pass
        archive.flush()
        report = archive.usage()
        assert report['tools']['reference_photo']['count'] == 2
        assert report['tools']['reference_photo']['failed'] == 1
        assert 'private-alternative' not in report['tools']
        assert report['complete'] is False and report['from'] is not None
    finally:
        archive.close()
