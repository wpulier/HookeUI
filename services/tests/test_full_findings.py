"""Scientific retention through presentation edits and process restart; no paid models."""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from orchestrator.research import ResearchManager
from orchestrator.research.run import Run
from research_worker.results import full_report
from research_worker.researcher import Researcher
from tests.test_research import FakeHub
from tests.test_research_versions import lesson_session


def test_complete_report_refuses_truncation():
    report = {key: 'Available evidence\n' * 400 for key in ('methods', 'observations', 'measurements', 'limitations')}
    assert full_report(report) == report
    with pytest.raises(ValueError, match='16000'):
        full_report({**report, 'observations': 'x' * 16001})


def test_findings_survive_failed_presentation_and_restart(tmp_path):
    async def scenario():
        manager, session = lesson_session(tmp_path)
        result = {'title': 'Stoma comparison', 'summary': 'Preview',
                  'report': {key: key + '\n' + 'Retained sentence. ' * 300
                             for key in ('methods', 'observations', 'measurements', 'limitations')}}
        session.working = [{'id': 'e1', 'version': 2, 'kind': 'population', 'run': 0}]
        manager._finish(session, 'done', result)
        original = copy.deepcopy(session.finding_revisions)
        assert len(original) == 1 and original[0]['evidence'][0]['version'] == 2
        assert original[0]['complete'] is True
        assert session.outputs[0]['findingRevision']['digest'] == original[0]['digest']
        # Whole, its later lines indented so none can pose as one of the app's own lines.
        assert result['report']['observations'].strip().replace('\n', '\n  ') in session.readable(limit=None)
        session._before['outputs'] = len(session.outputs)
        session.run = Run('revise', 'done', 'quick', 'Make it blue', target=session.outputs[0], kind_name='lesson')
        session.status = 'running'
        manager._finish(session, 'failed', {'summary': 'Presentation failed'})
        assert session.finding_revisions == original
        assert session.result['report'] == result['report']
        back = ResearchManager(FakeHub(), root=tmp_path).get(session.rid)
        assert back.finding_revisions == original
        assert back.result['report'] == result['report']
        # New scientific content creates a new record; its prior record is immutable.
        session.status = 'running'
        manager._finish(session, 'done', {**result, 'report': {**result['report'], 'observations': 'New observation'}})
        assert len(session.finding_revisions) == 2
        assert session.finding_revisions[0] == original[0]
    asyncio.run(scenario())


def test_saved_findings_are_durable_before_presentation_failure(tmp_path):
    async def scenario():
        manager, session = lesson_session(tmp_path)
        result = {'title': 'Stomata', 'summary': 'Preview', 'one_liner': 'Preview', 'significance': 'low',
                  'report': {key: 'No ' + key + ' beyond the retained evidence.'
                             for key in ('methods', 'observations', 'measurements', 'limitations')}}
        # Exercise the actual worker boundary without models or network calls.
        worker = Researcher.__new__(Researcher)
        worker.result, worker.refs, worker.saved_finding_refs = None, {}, set()
        worker.emit = lambda event, **message: manager._event({'rid': session.rid, 'event': event, **message})
        reply, _ = await worker.tool_save_findings(result)
        assert reply['ok'] and worker.result is None  # presentation may continue
        assert len(session.finding_revisions) == 1
        recovered = ResearchManager(FakeHub(), root=tmp_path).get(session.rid)
        assert recovered.result['report'] == result['report']
        # A worker crash with no final result cannot erase the independently saved record.
        manager._finish(session, 'failed', None)
        assert session.status == 'failed' and session.result['report'] == result['report']
        assert len(session.finding_revisions) == 1
    asyncio.run(scenario())


def test_worker_waits_for_actual_persistence_acknowledgement(tmp_path, monkeypatch):
    async def scenario():
        manager, session = lesson_session(tmp_path)
        attempt = manager._new_attempt(session)
        attempt.record['dispatchedAt'] = 1
        async def request(op, **message):
            return await manager._request({'op': op, **message})
        worker = Researcher({'rid': session.rid, 'run_id': attempt.id, 'dir': str(tmp_path), 'effort': 'quick'},
                            manager._event, request)
        payload = {'summary': 'Retained', 'report': {key: 'Evidence' for key in ('methods','observations','measurements','limitations')}}
        saved = manager._save
        def broken(_):
            raise OSError('disk full')
        monkeypatch.setattr(manager, '_save', broken)
        reply, _ = await worker.tool_save_findings(payload)
        assert 'not saved' in reply['error'] and not session.finding_revisions
        assert worker.result is None
        monkeypatch.setattr(manager, '_save', saved)
        reply, _ = await worker.tool_save_findings(payload)
        assert reply['ok']
        assert ResearchManager(FakeHub(), root=tmp_path).get(session.rid).result['report'] == payload['report']
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['failed', 'timeout-after-output'])
def test_worker_failure_keeps_saved_science_and_reference_files(tmp_path, failure):
    async def scenario():
        manager, session = lesson_session(tmp_path)
        directory = tmp_path / session.day / session.rid
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'sources').mkdir(exist_ok=True)
        photo = directory / 'sources' / 'reference.jpg'
        photo.write_bytes(b'original retained image bytes')
        worker = Researcher({'rid': session.rid, 'dir': str(directory), 'effort': 'quick'}, manager._event, None)
        worker.refs['https://example.org/photo'] = {'path': 'sources/reference.jpg', 'page': 'https://example.org/photo'}
        report = {key: 'Retained ' + key for key in ('methods', 'observations', 'measurements', 'limitations')}
        async def run(_):
            reply, _image = await worker.tool_save_findings({'title': 'Study', 'summary': 'Preview', 'report': report,
                                                           'refs': ['https://example.org/photo']})
            assert reply['ok']
            if failure == 'timeout-after-output':
                worker.made.append({'kind': 'html', 'path': 'outputs/report.html', 'caption': 'Presentation'})
                raise TimeoutError('presentation deadline')
            raise RuntimeError('presentation failed')
        await worker.run(SimpleNamespace(run=run))
        assert photo.read_bytes() == b'original retained image bytes'
        assert session.result['report'] == report
        assert len(session.finding_revisions) == 1
        assert session.status in ('failed', 'partial')
        assert ResearchManager(FakeHub(), root=tmp_path).get(session.rid).result['report'] == report
    asyncio.run(scenario())


def test_paginated_worker_read_is_bound_to_saved_findings(tmp_path):
    async def scenario():
        manager, session = lesson_session(tmp_path)
        directory = tmp_path / session.day / session.rid
        directory.mkdir(parents=True, exist_ok=True)
        worker = Researcher({'rid': session.rid, 'dir': str(directory), 'effort': 'quick'}, manager._event, None)
        entry = {'id':'e1', 'day':session.day, 'version':7, 'kind':'population', 'title':'Original measured cells'}
        document = {'entry':entry, 'detail':{'text':'Measured original rows', 'values':{'count':7, 'unit':'cells'}}}
        text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        digest = hashlib.sha256(text.encode()).hexdigest()
        worker.session_index = SimpleNamespace(read_page=lambda *args: {
            'entry':entry, 'text':text,
            'page': {'revision':digest, 'offset':0, 'totalChars':len(text), 'nextOffset':None}})
        reply, _ = await worker.tool_session({'action': 'read', 'id': 'e1'})
        assert reply['ok'] and session.working[0]['version'] == 7
        await worker.tool_save_findings({'title': 'Study', 'summary': 'Preview',
            'charts':[{'title':'Retained count', 'points':[{'id':'e1', 'revision':digest, 'label':'Original capture',
                       'valuePath':'/detail/values/count', 'unitPath':'/detail/values/unit'}]}],
            'report': {key: 'Retained ' + key for key in ('methods', 'observations', 'measurements', 'limitations')}})
        assert session.finding_revisions[0]['evidence'][0]['version'] == 7
        document['detail']['values']['count'] = 100  # later source mutation cannot change retained values
        restored = ResearchManager(FakeHub(), root=tmp_path).get(session.rid)
        point = restored.finding_revisions[0]['result']['charts'][0]['rows'][0]
        assert point['value'] == 7 and point['source']['contentRevision'] == digest
    asyncio.run(scenario())


def test_followup_answer_does_not_revise_science(tmp_path):
    async def scenario():
        manager, session = lesson_session(tmp_path)
        result = {'title': 'Study', 'summary': 'Preview',
                  'report': {key: 'Retained ' + key for key in ('methods', 'observations', 'measurements', 'limitations')}}
        manager._finish(session, 'done', result)
        original = copy.deepcopy(session.finding_revisions)
        session._before['outputs'] = len(session.outputs)
        session.run = Run('answer', 'done', 'quick', 'Explain that table')
        session.status = 'running'
        manager._finish(session, 'done', {'summary': 'Here is how to read the table'})
        assert session.finding_revisions == original
        assert session.result['report'] == result['report']
        assert session.history[-1]['result']['summary'] == 'Here is how to read the table'
    asyncio.run(scenario())


def test_findings_keep_evidence_after_card_eviction_and_restart(tmp_path):
    from orchestrator.research.manager import WORKING_MAX
    async def scenario():
        manager, session = lesson_session(tmp_path)
        for n in range(WORKING_MAX + 3):
            manager._event({'event': 'looked', 'rid': session.rid, 'id': f'e{n + 1}',
                            'how': 'read page', 'kind': 'population', 'title': 'Measured cells',
                            'version': n, 'contentRevision': f'revision-{n}'})
        assert len(session.working) == WORKING_MAX
        assert session.working_dropped == 3
        manager = ResearchManager(FakeHub(), root=tmp_path)
        session = manager.get(session.rid)
        assert len(session.evidence_reads) == WORKING_MAX + 3
        session.status = 'running'
        manager._finish(session, 'done', {'summary': 'Complete',
            'report': {key: 'Retained ' + key for key in ('methods', 'observations', 'measurements', 'limitations')}})
        record = session.finding_revisions[-1]
        assert len(record['evidence']) == WORKING_MAX + 3
        assert record['evidence'][0]['contentRevision'] == 'revision-0'
        assert record['missingEvidenceReads'] == 0
    asyncio.run(scenario())


def test_picture_reads_retain_picture_number_and_original_bytes_identity(tmp_path):
    import hashlib
    from tests.test_resources import jpeg
    async def scenario():
        manager, session = lesson_session(tmp_path)
        directory = tmp_path / session.day / session.rid
        directory.mkdir(parents=True, exist_ok=True)
        worker = Researcher({'rid': session.rid, 'dir': str(directory), 'effort': 'quick'}, manager._event, None)
        pictures = [jpeg(40), jpeg(80)]
        worker.session_index = SimpleNamespace(picture=lambda entry, n: (pictures[n], 'image/jpeg'))
        for n in (0, 1):
            reply, image = await worker.tool_session({'action': 'picture', 'id': 'e1', 'n': n})
            assert reply['ok'] and image
        await worker.tool_save_findings({'summary': 'Compared both pictures',
            'report': {key: 'Retained ' + key for key in ('methods', 'observations', 'measurements', 'limitations')}})
        evidence = session.finding_revisions[-1]['evidence']
        assert [e['how'] for e in evidence] == ['picture 0', 'picture 1']
        assert [e['contentRevision'] for e in evidence] == ['sha256:' + hashlib.sha256(picture).hexdigest() for picture in pictures]
    asyncio.run(scenario())
