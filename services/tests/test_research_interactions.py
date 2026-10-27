"""Request admission survives actual session writes/reloads; external work is scripted."""
import asyncio
import json

import pytest

from orchestrator.research import ResearchManager, ResearchRefused
from tests.test_research import FakeHub
from tests.test_research_versions import lesson_session


def test_duplicate_send_and_restart_return_one_persisted_dispatch(tmp_path):
    async def scenario():
        manager, session = lesson_session(tmp_path)
        session.status = 'done'
        dispatched = []
        async def dispatch():
            dispatched.append('provider')
            await asyncio.sleep(0)
            return {'ok': True, 'answer': 'Existing retained evidence'}
        replies = await asyncio.gather(*(manager.interact(session.rid, 'request-1', {'text': 'Why?'}, dispatch) for _ in range(2)))
        assert replies[0] == replies[1]
        assert dispatched == ['provider']
        restored = ResearchManager(FakeHub(), root=tmp_path)
        assert await restored.interact(session.rid, 'request-1', {'text': 'Why?'}, dispatch) == replies[0]
        assert dispatched == ['provider']
        with pytest.raises(ResearchRefused, match='different'):
            await restored.interact(session.rid, 'request-1', {'text': 'Changed words'}, dispatch)
    asyncio.run(scenario())


def test_failure_before_dispatch_does_not_call_provider(tmp_path, monkeypatch):
    async def scenario():
        manager, session = lesson_session(tmp_path)
        dispatched = []
        async def dispatch():
            dispatched.append(True)
            return {'ok': True}
        def fail(_):
            raise OSError('disk full')
        monkeypatch.setattr(manager, '_save', fail)
        with pytest.raises(OSError, match='disk full'):
            await manager.interact(session.rid, 'request-2', {'text': 'Continue'}, dispatch)
        assert not dispatched
    asyncio.run(scenario())


def test_interruption_retains_unknown_outcome_and_never_redispatches(tmp_path):
    async def scenario():
        manager, session = lesson_session(tmp_path)
        session.status = 'done'
        async def interrupted():
            raise asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await manager.interact(session.rid, 'request-3', {'text': 'Why?'}, interrupted)
        restored = ResearchManager(FakeHub(), root=tmp_path)
        async def forbidden():
            pytest.fail('unknown provider outcome must not redispatch')
        reply = await restored.interact(session.rid, 'request-3', {'text': 'Why?'}, forbidden)
        assert reply['ok'] is False and reply['code'] == 'OUTCOME_UNKNOWN'
        record = json.loads((tmp_path / session.day / session.rid / 'session.json').read_text())
        assert record['interactionJobs'][0]['state'] == 'interrupted'
    asyncio.run(scenario())


def test_expected_base_is_checked_before_dispatch(tmp_path):
    async def scenario():
        manager, session = lesson_session(tmp_path)
        session.result = {'findingRevision': {'version': 3, 'digest': 'three', 'complete': True}}
        async def forbidden():
            pytest.fail('stale edit must not dispatch')
        with pytest.raises(ResearchRefused, match='changed'):
            await manager.interact(session.rid, 'request-4', {'text': 'Revise', 'expectedBase': 2}, forbidden)
    asyncio.run(scenario())


def test_failed_finding_save_cannot_release_successful_completion(tmp_path, monkeypatch):
    async def scenario():
        manager, session = lesson_session(tmp_path)
        attempt = manager._new_attempt(session)
        attempt.record['dispatchedAt'] = 1
        def disk_failure(*_):
            raise OSError('disk full')
        monkeypatch.setattr('orchestrator.research.manager.os.replace', disk_failure)
        with pytest.raises(OSError, match='disk full'):
            manager._finish(session, 'done', {'summary': 'Not saved'})
        assert attempt.completion.done()
        assert attempt.completion.result()['status'] == 'failed'
        assert attempt.completion.result()['cause'] == 'persistence_failed'
        assert 'save' in session.step.lower()
    asyncio.run(scenario())


def test_late_research_usage_is_once_only_and_cannot_replace_new_work(tmp_path):
    async def scenario():
        manager, session = lesson_session(tmp_path)
        first = manager._new_attempt(session)
        first.record['dispatchedAt'] = 1
        message = {'event':'cost', 'rid':session.rid, 'runId':first.id, 'receiptId':'bill-1', 'spent':.03, 'estimated':0,
                   'charge':{'model':'gpt-6.1-sol', 'usd':.03}}
        manager._event(message)
        manager._finish(session, 'done', {'summary':'Original result'})
        session.status = 'running'
        newer = manager._new_attempt(session)
        newer.record['dispatchedAt'] = 2
        before = session.spent
        manager._event(message)
        assert session.spent == before
        late = {**message, 'receiptId':'bill-2', 'spent':.05, 'charge':{'model':'gpt-6.1-sol', 'usd':.02}}
        manager._event(late)
        manager._event({**late, 'event':'done', 'status':'done', 'result':{'summary':'Wrong old completion'}})
        assert session.result['summary'] == 'Original result' and session.status == 'running'
        assert abs(session.spent - before - .02) < 1e-9
        restored = ResearchManager(FakeHub(), root=tmp_path)
        recovered = restored.get(session.rid)
        restored._event(late)
        assert recovered.spent == session.spent
        assert recovered.result['summary'] == 'Original result'
    asyncio.run(scenario())


def test_restart_before_first_receipt_keeps_original_job_and_unknown_cost(tmp_path):
    async def scenario():
        manager, session = lesson_session(tmp_path)
        attempt = manager._new_attempt(session)
        attempt.record.update(status='dispatched', dispatchedAt=1)
        session.status = 'running'
        manager._save(session)
        restored = ResearchManager(FakeHub(), root=tmp_path)
        recovered = restored.get(session.rid)
        usage = recovered.usage_runs[attempt.id]
        assert usage['job']['status'] == 'interrupted'
        assert usage['outcomeUnknown'] and recovered.spent_estimated > 0
        assert not restored.queue
        before = recovered.spent
        message = {'event':'done', 'rid':session.rid, 'runId':attempt.id, 'eventSequence':5,
                   'spent':.03, 'spent_estimated':0, 'status':'done', 'result':{'summary':'Late result'}}
        restored._event(message)
        restored._event(message)
        assert recovered.spent == pytest.approx(before + .03)
        assert recovered.spent_estimated == 0
        assert (recovered.result or {}).get('summary') != 'Late result'
        reloaded = ResearchManager(FakeHub(), root=tmp_path).get(session.rid)
        assert reloaded.spent == recovered.spent and reloaded.spent_estimated == 0
    asyncio.run(scenario())


def test_failed_start_admission_releases_slot_and_never_dispatches(tmp_path, monkeypatch):
    async def scenario():
        manager = ResearchManager(FakeHub(), root=tmp_path)
        def fail(_):
            raise OSError('disk full')
        monkeypatch.setattr(manager, '_save', fail)
        with pytest.raises(OSError, match='disk full'):
            await manager.start('Read retained work', fake={'result': {'summary': 'unused'}})
        assert not manager.sessions and not manager.queue and not manager._snapshots
        assert not manager.spending._held
    asyncio.run(scenario())


def test_delayed_intent_keeps_admitted_basis_and_refuses_newer_science(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from orchestrator import commands
    from orchestrator.research.selection import view

    async def scenario():
        manager, session = lesson_session(tmp_path)
        manager._finish(session, 'done', {'summary':'Original findings', 'report':{'observations':'Original findings'}})
        base = session.result['findingRevision']['version']
        entered, release = asyncio.Event(), asyncio.Event()
        captured = []
        async def paused_brain(app, client, message, *, research_selection, said):
            assert said == 'Continue' and message['text'].endswith('Continue')  # the user's words, apart from context
            captured.append(research_selection)
            entered.set()
            await release.wait()
            assert view(session, research_selection).result['summary'] == 'Original findings'
            await manager.followup(session.rid, 'Continue', mode='continue', selection=research_selection)
            pytest.fail('stale continuation must be refused before dispatch')
        monkeypatch.setattr(commands, 'ask', paused_brain)
        request = {'rid':session.rid, 'text':'Continue', 'requestId':'slow-intent', 'expectedBase':base}
        task = asyncio.create_task(commands.research_followup(SimpleNamespace(research=manager), None, request))
        await entered.wait()
        session.status = 'running'
        manager._finish(session, 'done', {'summary':'New findings', 'report':{'observations':'New findings'}})
        release.set()
        response = await task
        assert not response['ok'] and 'older' in response['error']
        assert captured[0]['findingVersion'] == base
        restored = ResearchManager(FakeHub(), root=tmp_path)
        kept = restored.get(session.rid)
        assert kept.result['summary'] == 'New findings'
        assert kept.interaction_jobs[0]['request']['selection']['findingVersion'] == base
        assert kept.interaction_jobs[0]['state'] == 'failed'
        replay = await commands.research_followup(SimpleNamespace(research=restored), None, request)
        assert not replay['ok'] and 'older' in replay['error']
        assert len(captured) == 1 and not restored.queue
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['answer', 'continue'])
def test_followup_storage_failure_restores_prior_work_without_reservation(tmp_path, monkeypatch, mode):
    import copy
    async def scenario():
        manager, session = lesson_session(tmp_path)
        manager._finish(session, 'done', {'summary':'Kept result'})
        session.deliverable = 'research'
        before = copy.deepcopy(session.wire())
        saved = (tmp_path / session.day / session.rid / 'session.json').read_bytes()
        def fail(_):
            raise OSError('disk full')
        monkeypatch.setattr(manager, '_save', fail)
        with pytest.raises(OSError, match='disk full'):
            await manager.followup(session.rid, 'Explain the findings', mode=mode)
        assert session.wire() == before and not manager.queue and not manager._snapshots
        assert not manager.spending._held
        assert (tmp_path / session.day / session.rid / 'session.json').read_bytes() == saved
    asyncio.run(scenario())


def test_evidence_button_uses_existing_tool_once_through_persistence(tmp_path, monkeypatch):
    from orchestrator.commands import research_evidence
    from types import SimpleNamespace
    import orchestrator.commands as commands
    async def scenario():
        manager, session = lesson_session(tmp_path)
        session.status = 'running'
        session.requests = [{'id':'q1', 'run':0, 'status':'asked', 'seconds':15, 'what':'hold a still field', 'why':'compare motion'}]
        calls = []
        async def tool(app, name, args, **kwargs):
            calls.append((name, args, kwargs))
            manager.note(session.rid, args['text'], answers=args['answers'])
            return {'ok':True, 'summary':'Holding requested frames'}
        monkeypatch.setattr(commands, 'run_tool', tool)
        app = SimpleNamespace(research=manager)
        message = {'rid':session.rid, 'day':session.day, 'questionId':'q1', 'run':0, 'choice':'hold', 'requestId':'evidence-1'}
        first = await research_evidence(app, None, message)
        assert first['ok'] and len(calls) == 1
        assert calls[0][0] == 'researcher_note' and calls[0][1]['hold_seconds'] == 15
        assert calls[0][2]['question'] == calls[0][1]['asked_with']
        app.research = ResearchManager(FakeHub(), root=tmp_path)
        assert await research_evidence(app, None, message) == first and len(calls) == 1
        assert app.research.get(session.rid).requests[0]['status'] == 'answered'
        assert not (await research_evidence(app, None, {**message, 'day':'2020-01-01'}))['ok']
    asyncio.run(scenario())


def test_explicit_hold_button_runs_production_registry_without_model(tmp_path):
    from orchestrator.commands import research_evidence
    from tests.test_workspace_actions_app import built, research_fixture
    app, _ = built(tmp_path)
    async def scenario():
        manager, session = research_fixture(app)
        session.status = 'running'
        session.requests = [{'id':'q1', 'run':0, 'status':'asked', 'seconds':15, 'what':'hold the view', 'why':'inspect motion'}]
        message = {'rid':session.rid, 'day':session.day, 'questionId':'q1', 'run':0, 'choice':'hold', 'requestId':'real-tool-evidence'}
        response = await research_evidence(app, None, message)
        assert response['ok'], response
        assert session.requests[0]['status'] == 'answered'
        assert any(note.get('holding') for note in session.notes)
        assert await research_evidence(app, None, message) == response
        assert len([note for note in session.notes if note.get('holding')]) == 1
    asyncio.run(scenario())


def test_retained_followup_without_camera_reaches_brain_and_persists_once(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from orchestrator.commands import research_followup
    from tests.test_workspace_actions_app import built, research_fixture
    from orchestrator.ticket import current
    app, _ = built(tmp_path)
    manager, session = research_fixture(app)
    app.hub.settings.vision = app.hub.settings.fast = 'gpt-6.1-sol'
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-external-provider')
    sends = []
    class Provider:
        async def ask(self, images, prompt, **kwargs):
            assert current().admission is not None
            sends.append((images, prompt))
            return SimpleNamespace(data={'answer':'The retained work remains available.', 'actions':[],
                'intent':'question', 'needs_look':False, 'observation_scope':'none', 'input_refs':[]},
                model='synthetic-external-provider', latency_ms=0, error=None)
    monkeypatch.setattr(app.agent, 'vision', lambda _: Provider())
    async def scenario():
        assert app.hub.source.latest() is None
        message = {'rid':session.rid, 'text':'Explain the retained work.', 'requestId':'no-camera-answer'}
        result = await research_followup(app, None, message)
        assert result['ok'] and result['answer'] == 'The retained work remains available.'
        count = len(sends)
        assert count > 0 and all(not images for images, _ in sends)
        # The model is told which researcher and version; the chat, the conversation and Activity show only what was typed.
        assert any(f'researcher {session.rid}' in str(prompt) for _, prompt in sends)
        lines = app.day.active.lines
        assert [l['text'] for l in lines if l.get('type') == 'chat' and l.get('role') == 'user'] == ['Explain the retained work.']
        assert [t.text for t in app.day.active.turns if t.role == 'user'] == ['Explain the retained work.']
        requests = [l for l in lines if l.get('type') == 'activity' and l.get('kind') == 'request']
        assert requests and all(l['text'] == 'Explain the retained work.' for l in requests)
        assert not any('Selected retained artifact' in json.dumps(l) for l in lines)
        assert await research_followup(app, None, message) == result
        assert len(sends) == count
        restored = ResearchManager(FakeHub(), root=manager.root, today=lambda:session.day)
        job = restored.get(session.rid).interaction_jobs[-1]
        assert job['outcome']['answer'] == result['answer'] and job['state'] == 'completed'
        await app.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize('quoted', [True, False])
def test_continue_keeps_user_authorization_separate_from_the_models_work_brief(tmp_path, monkeypatch, quoted):
    from tests.test_research_presentations import setup
    from tests.fakes import research_tools
    from orchestrator.brain.registry import ToolContext
    async def scenario():
        manager, session, _, _ = setup(tmp_path, monkeypatch)
        # A lesson continue needs the user's own words (a "report" no longer does: it runs as research).
        session.deliverable, session.effort = 'lesson', 'standard'
        manager._save(session)
        monkeypatch.setattr(manager, '_pump', lambda: None)  # admission/persistence only; no external worker
        registry = research_tools(manager.hub, manager)
        words = 'Continue this research with a short source-backed explanation of one practical check.'
        ctx = ToolContext('chat', 1, None, words, 'continue-authorization')
        run = registry.start('researcher_followup', {'researcher':session.rid, 'mode':'continue',
            'question':'Investigate the retained report further and explain a practical check.',
            'asked_with':words if quoted else 'Produce a long video about fish migration.'}, ctx)
        await run.task
        assert run.ok is quoted, run.summary
        assert bool(manager.queue) is quoted
        if quoted:
            restored = ResearchManager(FakeHub(), root=tmp_path).get(session.rid)
            assert restored.history and session.result is None
        manager.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize('fields', [['prompt'], ['answer'], [], ['prompt', 'prompt'], ['unknown'], [['prompt']]])
def test_model_selected_lesson_fields_are_validated_and_persisted(tmp_path, monkeypatch, fields):
    from tests.test_research_presentations import setup
    from tests.fakes import research_tools
    from orchestrator.brain.registry import ToolContext
    async def scenario():
        manager, session, _, _ = setup(tmp_path, monkeypatch)
        monkeypatch.setattr(manager, '_pump', lambda: None)
        registry = research_tools(manager.hub, manager)
        ctx = ToolContext('chat', 1, None, 'Simplify the selected question only.', 'field-scope')
        output = manager.presentations.current(session, 'lesson')
        ctx.research_selection = {'day':session.day, 'rid':session.rid, 'outputId':output['id'],
            'outputVersion':output['version'], 'anchor':'step:q2'}
        run = registry.start('revise_work', {'researcher':session.rid, 'target':output['id'],
            'change':'Simplify question 2 only.', 'edit_fields':fields}, ctx)
        await run.task
        valid = fields in (['prompt'], ['answer'])
        assert run.ok is valid, run.summary
        assert bool(manager.queue) is valid
        if valid:
            saved = json.loads((tmp_path / session.day / session.rid / 'session.json').read_text())
            assert saved['revising']['edit_fields'] == fields
            assert saved['revising']['anchor'] == 'step:q2'
            restored = ResearchManager(FakeHub(), root=tmp_path).get(session.rid)
            assert restored.history[-1]['edit_fields'] == fields
            assert restored.history[-1]['status'] == 'interrupted'
        manager.stop()
    asyncio.run(scenario())
