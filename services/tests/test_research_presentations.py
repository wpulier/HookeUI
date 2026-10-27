"""Real session/output persistence with a scripted external process boundary; no paid models."""
import asyncio
import copy
import pytest

from orchestrator.research import ResearchManager
from tests.test_research import FakeHub
from tests.test_research_versions import lesson_session


class Process:
    alive = True
    def __init__(self):
        self.calls = []
    async def call(self, op, **payload):
        self.calls.append((op, payload))
        return {'ok': True}
    def stop(self):
        self.alive = False


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-process-boundary')
    manager, session = lesson_session(tmp_path)
    manager._finish(session, 'done', {'title': 'Retained finding', 'summary': 'One failed field',
        'report': {'methods': 'Measured', 'observations': 'One field retained', 'measurements': '3 cells', 'limitations': 'One failed'}})
    process = Process()
    manager.presentations.worker = process
    record = session.finding_revisions[-1]
    args = {'version': record['version'], 'digest': record['digest'], 'model': 'gpt-6.1-sol'}
    return manager, session, process, args


def done(session, job, usd=.01):
    return {'event': 'presentation-done', 'rid': session.rid, 'job': job['id'],
            'plan': {'layout': 'report', 'blocks': [{'ref': ref} for ref in ('summary', 'report:measurements', 'report:limitations')]},
            'receipt': {'id': job['id'] + '-receipt', 'model': 'gpt-6.1-sol', 'usd': usd, 'usage': {'input_tokens': 100}}}


def test_a_new_session_keeps_independent_jobs_and_unknown_receipts_at_their_saved_owner(tmp_path, monkeypatch):
    """Nothing is archived any more (ADR 0017): a new session is opened while a presentation job works, and its late
    receipt still settles on its own researcher, counted in today's spend. A change to it from the new session is
    refused, naming its session."""
    from orchestrator.research import ResearchRefused
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        job = manager.presentations.begin(session.rid, 'clear-while-producing', **args)
        job = session.presentation_jobs[-1]
        for _ in range(5):  # dispatched by this process: its receipt may still come
            await asyncio.sleep(0)
        assert job.get('dispatchedAt') and manager.presentations.receipt_due(session, job)
        sitting = session.sitting
        manager.session = lambda: sitting + '.2'  # the user opened a new session the same day
        manager.session_event({'type': 'session-opened', 'session': sitting + '.2'})
        assert manager.get(session.rid) is session
        assert (tmp_path / session.day / session.rid / 'session.json').exists()
        with pytest.raises(ResearchRefused, match='belongs to'):
            manager.presentations.begin(session.rid, 'from-the-new-session', **args)
        manager.presentations.event(done(session, job))
        assert job['cost'] == .01 and not job['outcomeUnknown'] and job['day'] == session.day
        assert manager.spent_today() >= .01
        manager.presentations.stop()
    asyncio.run(scenario())


def test_a_job_cut_off_by_a_restart_keeps_its_estimated_cost_counted(tmp_path, monkeypatch):
    """Live 2026-10-11 (before the demo): "start today afresh" was refused for a presentation job a restart had cut
    off; its receipt could never come (the worker that dispatched it was gone). Nothing is archived now (ADR 0017);
    its estimated cost stays counted against the day it ran."""
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        manager.presentations.begin(session.rid, 'cut-off-by-a-restart', **args)
        job = session.presentation_jobs[-1]
        for _ in range(5):
            await asyncio.sleep(0)
        manager.presentations.stop()
        restarted = ResearchManager(FakeHub(), root=tmp_path)  # the app again, reading the session back
        again = restarted.get(session.rid)
        cut = next(j for j in again.presentation_jobs if j['id'] == job['id'])
        assert cut['state'] == 'interrupted' and cut['outcomeUnknown'] and cut['estimatedCost'] > 0
        assert restarted.spent_today() >= cut['estimatedCost']
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['answer', 'revise', 'continue'])
def test_independent_completion_is_not_an_output_of_overlapping_science(tmp_path, monkeypatch, mode):
    from orchestrator.research.run import Run
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        job = manager.presentations.begin(session.rid, 'overlapping-presentation', **args)
        await asyncio.sleep(0)
        session._before = {'outputs':len(session.outputs)}
        session.status = 'running'
        if mode != 'continue':
            session.run = Run(mode, 'done', 'quick' if mode == 'answer' else 'revise', 'Follow-up', target={'id':'o3'}, kind_name='lesson')
        attempt = manager._new_attempt(session)
        manager.presentations.event(done(session, job))
        produced = copy.deepcopy(session.outputs[-1])
        manager._finish(session, 'done', {'summary':'Follow-up answer'})
        outcome = attempt.completion.result()
        assert outcome['producedOutputs'] == outcome['outputs'] == []
        if mode == 'revise':
            assert session.last['status'] == 'failed' and session.last['output'] is None
        restored = ResearchManager(FakeHub(), root=tmp_path).get(session.rid)
        assert restored.outputs[-1] == produced and restored.presentation_jobs[-1]['state'] == 'ready'
        manager.presentations.stop()
    asyncio.run(scenario())


def test_independent_lesson_retains_findings_steps_and_persisted_versions(tmp_path, monkeypatch):
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        old = manager.presentations.current(session, 'lesson')
        lesson = session.content_of(old)
        science = copy.deepcopy(session.finding_revisions)
        job = manager.presentations.begin(session.rid, 'lesson-restyle', **args, kind='lesson', expected_base=old['id'])
        await asyncio.sleep(0)
        assert session.status == 'done' and session.run is None
        assert process.calls[0][1]['lesson'] == lesson
        html = '<!doctype html><html><body>' + ''.join(
            f'<section id="{step["id"]}">{step["prompt"]} {step["answer"]} {step["hint"]}</section>'
            for step in lesson['steps']) + '</body></html>'
        message = done(session, job)
        message['plan'] = {'html':html, 'evidenceRefs':['summary', 'report:limitations']}
        manager.presentations.event(message)
        assert session.presentation_jobs[-1]['state'] == 'ready'
        output = session.outputs[-1]
        assert output['kind'] == 'html' and output['format'] == 'html' and output['contentKind'] == 'lesson'
        assert output['content'] == lesson and output['replaces'] == old['id']
        assert output['findingRevision']['digest'] == args['digest']
        assert session.finding_revisions == science
        assert (tmp_path / session.day / session.rid / output['path']).read_text() == html
        recovered = ResearchManager(FakeHub(), root=tmp_path).get(session.rid)
        assert recovered.steps() == session.steps() and recovered.outputs[-1] == output
        manager.presentations.stop()
    asyncio.run(scenario())


def test_lesson_admission_follows_durable_research_completion_and_keeps_original_notice(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-process-boundary')
        manager, session = lesson_session(tmp_path)
        manager.hub.settings.presentation = 'auto'
        lesson = copy.deepcopy(session.content_of(manager.presentations.current(session, 'lesson')))
        process = Process()
        manager.presentations.worker = process
        session.origin = {'thread':'original-thread'}
        attempt = manager._new_attempt(session)
        attempt.record['dispatchedAt'] = 1
        observed = []
        manager.on_done.append(lambda completed: observed.append((attempt.completion.done(), completed.presentation_jobs[-1]['state'])))
        notices = []
        manager.on_presentation.append(lambda original, job: notices.append((original.origin, job['output'])))
        session.origin = {'thread':'original-thread'}
        manager._finish(session, 'done', {'summary':'Original retained lesson', 'lesson':lesson})
        assert observed == [(True, 'queued')]
        assert session.status == 'done' and session.finding_revisions[-1]['complete'] is False
        assert ResearchManager(FakeHub(), root=tmp_path).get(session.rid).result['lesson'] == lesson
        await asyncio.sleep(0)
        job = session.presentation_jobs[-1]
        session.origin = {'thread':'later-followup'}
        html = '<!doctype html><html>' + ''.join(f'<section id="{s["id"]}">{s["prompt"]} {s["answer"]}</section>' for s in lesson['steps']) + '</html>'
        message = done(session, job)
        message['plan'] = {'html':html, 'evidenceRefs':['summary']}
        manager.presentations.event(message)
        assert notices == [({'thread':'original-thread'}, session.outputs[-1]['id'])]
        manager.presentations.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize('field', ['prompt', 'answer'])
def test_targeted_lesson_content_revision_precedes_independent_html(tmp_path, monkeypatch, field):
    from types import SimpleNamespace
    from research_worker.researcher import Researcher
    from research_worker import researcher as worker_module
    from orchestrator.research.run import Run
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        manager.hub.settings.presentation = 'auto'
        old = manager.presentations.current(session, 'lesson')
        content = copy.deepcopy(session.content_of(old))
        async def edit(*_):
            return {**content, 'steps':[{**step, 'prompt':'Changed ' + step['prompt'],
                'answer':'Unauthorized answer rewrite'} for step in content['steps']]}
        async def forbidden(*_):
            pytest.fail('HTML must not consume the research worker budget or rounds')
        monkeypatch.setattr(worker_module, 'edit_json', edit)
        monkeypatch.setattr(worker_module, 'write_lesson', forbidden)
        events = []
        target = {**old, 'content':content, 'anchor':'step:q2', 'edit_fields':[field]}
        spec = {'rid':session.rid, 'dir':str(tmp_path / session.day / session.rid), 'effort':'revise',
                'deliverable':'revise', 'independent_presentation':True, 'brief':'Make this clearer',
                'revise':{'kind':'lesson', 'change':'Make this clearer', 'target':target}}
        await Researcher(spec, events.append, None).run(SimpleNamespace(offline=False))
        assert not any(event['event'] == 'output' for event in events)
        revised = events[-1]['result']['lesson']
        assert revised['steps'][0] == content['steps'][0]
        assert (revised['steps'][1]['prompt'] != content['steps'][1]['prompt']) is (field == 'prompt')
        assert (revised['steps'][1]['answer'] != content['steps'][1]['answer']) is (field == 'answer')
        assert revised['steps'][1].get('hint') == content['steps'][1].get('hint')
        science = copy.deepcopy(session.finding_revisions)
        session.run = Run('revise', 'done', 'revise', 'Make this clearer', target=target, kind_name='lesson')
        session.status = 'running'
        manager._new_attempt(session)
        manager._finish(session, 'done', events[-1]['result'])
        assert len(session.finding_revisions) == len(science) + 1
        assert session.finding_revisions[:-1] == science
        assert session.finding_revisions[-1]['result']['lesson'] == revised
        assert session.history[-1]['presentationPending'] is True
        assert session.status == 'done' and session.run is None
        await asyncio.sleep(0)
        assert process.calls[-1][1]['lesson'] == revised
        restored = ResearchManager(FakeHub(), root=tmp_path).get(session.rid)
        assert restored.result['lesson'] == revised and restored.outputs[-1]['id'] == old['id']
        manager.presentations.stop()
    asyncio.run(scenario())


def test_presentation_is_separate_deduplicated_and_reopens_without_dispatch(tmp_path, monkeypatch):
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        science = copy.deepcopy(session.finding_revisions)
        job = manager.presentations.begin(session.rid, 'p1', **args)
        assert manager.presentations.begin(session.rid, 'p1', **args)['id'] == job['id']
        await asyncio.sleep(0)
        assert len(process.calls) == 1
        assert session.status == 'done' and session.run is None
        manager.presentations.event(done(session, job))
        assert session.presentation_jobs[0]['state'] == 'ready'
        assert session.finding_revisions == science
        restored = ResearchManager(FakeHub(), root=tmp_path)
        recovered = restored.get(session.rid)
        assert recovered.finding_revisions == science
        assert recovered.outputs[-1]['findingRevision']['digest'] == args['digest']
        assert restored.presentations.begin(session.rid, 'p1', **args)['state'] == 'ready'
        assert restored.presentations.worker is None
        manager.presentations.stop()
    asyncio.run(scenario())


def test_competing_presentations_keep_loser_noncurrent_and_receipts_count_once(tmp_path, monkeypatch):
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        before = manager.spending.spent()
        jobs = [manager.presentations.begin(session.rid, key, **args) for key in ('p1', 'p2')]
        await asyncio.sleep(0)
        for job in jobs:
            manager.presentations.event(done(session, job))
            manager.presentations.event(done(session, job))
        assert [j['state'] for j in session.presentation_jobs] == ['ready', 'conflict']
        outputs = [o for o in session.current_outputs() if o['kind'] == 'presentation']
        assert len(outputs) == 1 and outputs[0]['job'] == 'p1'
        assert len([o for o in session.outputs if o['kind'] == 'presentation']) == 2
        assert abs(manager.spending.spent() - before - .02) < 1e-9
        recovered = ResearchManager(FakeHub(), root=tmp_path).get(session.rid)
        assert [j['state'] for j in recovered.presentation_jobs] == ['ready', 'conflict']
        manager.presentations.stop()
    asyncio.run(scenario())


def test_restart_and_late_receipt_do_not_regenerate_or_revive_output(tmp_path, monkeypatch):
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        job = manager.presentations.begin(session.rid, 'p1', **args)
        await asyncio.sleep(0)
        restored = ResearchManager(FakeHub(), root=tmp_path)
        recovered = restored.get(session.rid)
        assert recovered.presentation_jobs[0]['state'] == 'interrupted'
        assert recovered.presentation_jobs[0]['estimatedCost'] == .4
        restored.presentations.event(done(recovered, job))
        assert recovered.presentation_jobs[0]['state'] == 'interrupted'
        assert recovered.presentation_jobs[0]['estimatedCost'] == 0
        assert recovered.presentation_jobs[0]['cost'] == .01
        assert not any(o['kind'] == 'presentation' for o in recovered.outputs)
        assert restored.presentations.worker is None
        manager.presentations.stop()
    asyncio.run(scenario())


def test_invalid_presentation_keeps_findings_and_billed_failure(tmp_path, monkeypatch):
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        before = copy.deepcopy(session.result)
        job = manager.presentations.begin(session.rid, 'p1', **args)
        await asyncio.sleep(0)
        event = done(session, job)
        event['plan']['blocks'].append({'ref': 'invented-data'})
        manager.presentations.event(event)
        assert session.presentation_jobs[0]['state'] == 'failed'
        assert session.presentation_jobs[0]['cost'] == .01
        assert session.result == before
        assert ResearchManager(FakeHub(), root=tmp_path).get(session.rid).result == before
        manager.presentations.stop()
    asyncio.run(scenario())


def test_failed_output_save_never_claims_ready_or_exposes_unsaved_output(tmp_path, monkeypatch):
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        job = manager.presentations.begin(session.rid, 'p1', **args)
        await asyncio.sleep(0)
        saved = manager._save
        def broken(_session):
            raise OSError('disk full')
        manager._save = broken
        manager.presentations.event(done(session, job))
        assert not any(o['kind'] == 'presentation' for o in session.outputs)
        assert session.presentation_jobs[0]['state'] == 'failed'
        assert session.presentation_jobs[0]['persistenceError'] == 'disk full'
        recovered = ResearchManager(FakeHub(), root=tmp_path).get(session.rid)
        assert recovered.presentation_jobs[0]['state'] == 'interrupted'
        assert not any(o['kind'] == 'presentation' for o in recovered.outputs)
        manager._save = saved
        manager.presentations.recover(session.rid, job['id'])
        assert len(process.calls) == 1
        assert session.presentation_jobs[0]['state'] == 'ready'
        assert len([o for o in session.outputs if o['kind'] == 'presentation']) == 1
        manager.presentations.recover(session.rid, job['id'])
        assert len([o for o in session.outputs if o['kind'] == 'presentation']) == 1
        restored = ResearchManager(FakeHub(), root=tmp_path).get(session.rid)
        assert restored.presentation_jobs[0]['state'] == 'ready'
        assert not restored.presentation_jobs[0].get('pendingOutput')
        manager.presentations.stop()
    asyncio.run(scenario())


def test_missing_usage_keeps_estimate_and_cancelled_job_cannot_revive(tmp_path, monkeypatch):
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        job = manager.presentations.begin(session.rid, 'p1', **args)
        await asyncio.sleep(0)
        await manager.presentations.cancel(session.rid, job['id'])
        event = done(session, job)
        event['receipt']['usd'] = None
        manager.presentations.event(event)
        stored = session.presentation_jobs[0]
        assert stored['state'] == 'cancelled' and stored['estimatedCost'] == .4
        assert stored['outcomeUnknown'] and stored['cost'] == 0
        assert not any(o['kind'] == 'presentation' for o in session.outputs)
        assert process.calls[-1][1]['rid'] == session.rid
        manager.presentations.stop()
    asyncio.run(scenario())


def test_admission_failure_does_not_dispatch_or_retain_job(tmp_path, monkeypatch):
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        def broken(_session):
            raise OSError('disk full')
        manager._save = broken
        with pytest.raises(OSError):
            manager.presentations.begin(session.rid, 'p1', **args)
        await asyncio.sleep(0)
        assert not session.presentation_jobs and not process.calls
        assert not ResearchManager(FakeHub(), root=tmp_path).get(session.rid).presentation_jobs
    asyncio.run(scenario())


def test_lesson_validation_binds_retained_text_to_its_step():
    from research_worker.presentation_lesson import validate
    lesson = {'steps':[{'id':'q1', 'prompt':'First question', 'answer':'First answer'},
                       {'id':'q2', 'prompt':'Second question', 'answer':'Second answer'}]}
    def plan(body):
        return {'html':'<!doctype html><html><body>' + body + '</body></html>', 'evidenceRefs':['summary']}
    valid = '<section id="q1">First question First answer</section><section id="q2">Second question Second answer</section>'
    validate(plan(valid), {'summary':'Retained summary'}, lesson)
    swapped = valid.replace('id="q1"', 'id="temp"').replace('id="q2"', 'id="q1"').replace('id="temp"', 'id="q2"')
    for body in (swapped, '<section id="q1"><script>First question First answer</script></section><section id="q2">Second question Second answer</section>'):
        with pytest.raises(ValueError, match='retained prompt'):
            validate(plan(body), {'summary':'Retained summary'}, lesson)


def test_final_lesson_draft_survives_prior_full_findings_save(tmp_path, monkeypatch):
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        manager.hub.settings.presentation = 'auto'
        report = copy.deepcopy(session.result['report'])
        lesson = copy.deepcopy(session.content_of(manager.presentations.current(session, 'lesson')))
        session.status = 'running'
        manager._new_attempt(session)
        manager._finish(session, 'done', {'summary':'Compact recap', 'lesson':lesson})
        restored = ResearchManager(FakeHub(), root=tmp_path).get(session.rid)
        assert restored.result['report'] == report and restored.result['lesson'] == lesson
        await asyncio.sleep(0)
        assert process.calls[-1][1]['lesson'] == lesson
        manager.presentations.stop()
    asyncio.run(scenario())


def test_auto_uses_only_qualified_available_providers_and_explicit_choices_never_fallback(monkeypatch):
    from research_worker.presentation import choose
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'synthetic')
    result = {'report':{'limitations':'One field'}}
    assert choose('auto', result) == 'gpt-6.1-sol'
    assert choose('auto', {}) is None
    monkeypatch.delenv('ANTHROPIC_API_KEY')
    with pytest.raises(ValueError, match='unavailable'):
        choose('auto', result, 'claude-opus-5-5')
    assert choose('off', result) is None


def test_html_report_restyles_through_report_producer_and_does_not_replace_lesson(tmp_path, monkeypatch):
    from tests.fakes import research_tools
    from orchestrator.brain.registry import ToolContext
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        lesson = manager.presentations.current(session, 'lesson')
        basis = session.result['findingRevision']
        report = {'id':'html-report', 'kind':'html', 'contentKind':'report', 'version':1,
                  'path':'outputs/report.html', 'caption':'Report', 'findingRevision':basis}
        session.outputs.append(report)
        assert manager.presentations.current(session, 'lesson') is lesson
        registry = research_tools(manager.hub, manager)
        ctx = ToolContext('chat', 1, None, 'Restyle this report', 'restyle-report')
        ctx.research_selection = {'day':session.day, 'rid':session.rid, 'outputId':report['id'],
                                 'outputVersion':1, 'findingVersion':basis['version'],
                                 'findingDigest':basis['digest'], 'presentationBase':report['id']}
        run = registry.start('revise_work', {'researcher':session.rid, 'scope':'presentation',
                                           'change':'Larger labels', 'model':'gpt-6.1-sol'}, ctx)
        await run.task
        assert run.ok, run.summary
        job = session.presentation_jobs[-1]
        assert job['contentKind'] == 'report' and job['expectedBase'] == report['id']
        await asyncio.sleep(0)
        manager.presentations.event(done(session, job))
        assert manager.presentations.current(session, 'lesson') is lesson
        assert session.outputs[-1]['format'] == 'blocks'
        assert session.outputs[-1]['version'] == 2 and session.outputs[-1]['replaces'] == report['id']
        assert report not in session.current_outputs()
        manager.presentations.stop()
    asyncio.run(scenario())


def test_make_the_presentation_prettier_restyles_the_researchers_lesson(tmp_path, monkeypatch):
    """2026-10-11, live: "make theor prez prettoer" with the lesson on screen went to revise_work target "presentation"
    and made a report presentation beside it (content_kind(None) was "report"); the viewer kept the lesson and the
    brain said the lesson was updated. A restyle is of the researcher's own lesson, as its next version."""
    from tests.fakes import research_tools
    from orchestrator.brain.registry import ToolContext
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        monkeypatch.setattr(manager.hub.settings, 'presentation', 'auto', raising=False)  # the app's default
        lesson = manager.presentations.current(session, 'lesson')
        registry = research_tools(manager.hub, manager)
        ctx = ToolContext('chat', 1, None, 'make theor prez prettoer', 'prettier')
        run = registry.start('revise_work', {'researcher':session.rid, 'target':'presentation',
                                           'change':'make the presentation prettier'}, ctx)
        await run.task
        assert run.ok, run.summary
        assert f"restyling its lesson (v{lesson.get('version') or 1})" in run.summary
        job = session.presentation_jobs[-1]
        assert job['contentKind'] == 'lesson' and job['expectedBase'] == lesson['id']
        assert not [o for o in session.outputs if o['kind'] == 'presentation']  # no report beside it
        await asyncio.sleep(0)
        steps = session.content_of(lesson)['steps']
        html = '<!doctype html><html><body>' + ''.join(
            f'<section id="{step["id"]}">{step["prompt"]} {step["answer"]} {step["hint"]}</section>' for step in steps) + '</body></html>'
        message = done(session, job)
        message['plan'] = {'html':html, 'evidenceRefs':['summary', 'report:limitations']}
        manager.presentations.event(message)
        new = session.outputs[-1]
        assert new['contentKind'] == 'lesson' and new['replaces'] == lesson['id'] and new['version'] == (lesson.get('version') or 1) + 1
        assert manager.presentations.current(session, 'lesson')['id'] == new['id']
        manager.presentations.stop()
    asyncio.run(scenario())


def test_a_credit_refusal_is_said_in_words_and_an_http_429_is_never_counted_as_spent(tmp_path, monkeypatch):
    """S9: the provider's refusal for want of credit becomes fixed words (its raw text kept apart for Activity), and a
    refusal with HTTP 429 counts nothing: the provider refused it before any work, as engines.py's rule says."""
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        job = manager.presentations.begin(session.rid, 'p1', **args)
        await asyncio.sleep(0)
        raw = "Error code: 429 - {'error': {'message': 'You exceeded your current quota', 'code': 'insufficient_quota'}}"
        manager.presentations.event({'event': 'presentation-failed', 'rid': session.rid, 'job': job['id'], 'error': raw,
                                     'status': 429, 'credit': 'OpenAI'})
        stored = session.presentation_jobs[0]
        assert stored['state'] == 'failed' and stored.get('dispatchedAt')  # dispatched, with no receipt
        assert stored['error'] == 'OpenAI has no credits left.'  # the studio says "Presentation not made: " before it
        assert stored['errorDetail'] == raw and 'insufficient_quota' not in stored['error']
        assert stored.get('estimatedCost', 0) == 0 and not stored.get('outcomeUnknown')
        # Another failure after dispatch, with no receipt, may have been billed: counted at its reservation, said as is.
        job2 = manager.presentations.begin(session.rid, 'p2', **args)
        await asyncio.sleep(0)
        manager.presentations.event({'event': 'presentation-failed', 'rid': session.rid, 'job': job2['id'], 'error': 'Connection reset', 'status': 503})
        second = next(j for j in session.presentation_jobs if j['id'] == job2['id'])
        if second.get('dispatchedAt'):
            assert second['estimatedCost'] == second.get('reservation', 0.0) and second['outcomeUnknown']
        assert second['error'] == 'Connection reset' and 'errorDetail' not in second
        manager.presentations.stop()
    asyncio.run(scenario())


def test_after_a_credit_refusal_an_automatic_presentation_keeps_the_standard_layout_and_a_requested_one_still_asks(tmp_path, monkeypatch):
    """Live 2026-10-25: Researching on Gemini, the automatic presentation (setting "auto") asked OpenAI, which had no
    credits, and every researcher ended with a red failure. After one refusal for want of credit an automatic one is
    not dispatched to that provider (unavailable, the standard layout stays, said in its own words, no notice); one
    the user asks for still asks it, and an answer clears the hold."""
    from orchestrator.research import presentation as jobs
    async def scenario():
        manager, session, process, args = setup(tmp_path, monkeypatch)
        monkeypatch.setattr(manager.hub.settings, 'presentation', 'auto', raising=False)
        notified = []
        manager.on_presentation.append(lambda view, job: notified.append(job['id']))
        first = manager.presentations.begin(session.rid, 'auto-1', version=args['version'], digest=args['digest'],
                                            notify=True, automatic=True)
        assert first['state'] == 'queued' and first['model'] == 'gpt-6.1-sol'
        await asyncio.sleep(0)
        manager.presentations.event({'event': 'presentation-failed', 'rid': session.rid, 'job': 'auto-1',
                                     'error': 'insufficient_quota', 'status': 429, 'credit': 'OpenAI'})
        assert notified == ['auto-1']  # the refusal that happened is said, once
        second = manager.presentations.begin(session.rid, 'auto-2', version=args['version'], digest=args['digest'],
                                             notify=True, automatic=True)
        assert second['state'] == 'unavailable' and second['error'] == 'OpenAI has no credits left.'
        assert 'model' not in second and manager.presentations.key(session, second) not in manager.presentations.tasks
        assert notified == ['auto-1']  # nothing new failed: no second notice
        asked = manager.presentations.begin(session.rid, 'asked', **args)  # a restyle the user asked for
        assert asked['state'] == 'queued'
        await asyncio.sleep(0)
        manager.presentations.event(done(session, session.presentation_jobs[-1]))
        assert 'gpt-6.1-sol' not in manager.presentations.refused  # it answered: the hold is cleared
        monkeypatch.setattr(jobs, 'CREDIT_HOLD_S', 0.0)
        manager.presentations.refused['gpt-6.1-sol'] = (0.0, 'OpenAI')  # a refusal longer ago than the hold
        current = manager.presentations.current(session)
        later = manager.presentations.begin(session.rid, 'auto-3', version=args['version'], digest=args['digest'],
                                            expected_base=(current or {}).get('id'), automatic=True)
        assert later['state'] == 'queued'
        manager.presentations.stop()
    asyncio.run(scenario())


def test_the_worker_says_a_refusal_for_want_of_credit_and_its_status(monkeypatch):
    import httpx
    import openai
    from research_worker import engines
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    exc = openai.RateLimitError("Error code: 429", response=httpx.Response(429, request=request),
                                body={"message": "quota", "type": "insufficient_quota", "code": "insufficient_quota"})
    assert engines._credit_refusal(exc) == 'OpenAI' and exc.status_code == 429
