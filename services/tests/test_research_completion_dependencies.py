"""Original research completion dependencies over real manager and public tools.

WorkerMessages are synthetic external process replies; Studio is a structural
legacy renderer, not a native browser paint proof. Manager dispatch, accounting,
attempt futures, retained files, Presenter, Screen and Runner are real owners.
"""
import asyncio
import hashlib
import json

import pytest

from orchestrator.brain.actions import ActionRunner
from orchestrator.brain.registry import ToolContext, parse_actions
from orchestrator.brain.toolbox import Services, build
from orchestrator.day import DayStore
from orchestrator.presenter import Presenter
from orchestrator.research import ResearchManager
from orchestrator.research.desk import Desk
from orchestrator.screen import Screen
from orchestrator.ticket import issue, using
from tests.test_mission_manager_interleavings import ControlledWorker, dispatched
from tests.test_mission_manager_lifecycle import cleanup
from tests.test_population import world  # noqa: F401
from tests.test_screen import Studio
from tests.waiting import HANG_S


@pytest.fixture(autouse=True)
def _research_key(monkeypatch):
    """A set-up app: the researchers' model (GPT-6.1 Sol by default) has its key, so a start is not refused for want of
    it (research/manager.engine_key_refusal; the refusal itself: test_research_engine_key.py)."""
    monkeypatch.setenv('OPENAI_API_KEY', 'test-openai-key')


class ResultStudio(Studio):
    """Only the renderer transport is controlled; actual Screen confirmation runs."""

    def hear(self, message):
        super().hear(message)
        if message.get('type') == 'presenter':
            self.items['viewer'] = {'id': 'viewer', 'kind': 'viewer',
                                    'open': message['open'], 'shows': message['rid'], 'z': 11}
            asyncio.get_running_loop().call_soon(self.report)


class ResearchRig:
    def __init__(self, hub, root):
        self.day = DayStore(root=root / 'days')
        self.manager = ResearchManager(hub, root=root / 'research', today=lambda: self.day.day)
        self.worker = ControlledWorker(self.manager)
        self.manager._worker = self.worker  # only the external process channel
        self.screen = Screen()
        self.studio = ResultStudio(self.screen)
        original_broadcast = hub.broadcast

        def broadcast(message):
            original_broadcast(message)
            self.studio.hear(message)

        hub.broadcast, hub.screen = broadcast, self.screen
        self.screen.broadcast = broadcast
        self.presenter = Presenter(hub, self.manager)
        self.desk = Desk(self.manager, shown=lambda: self.presenter.rid if self.presenter.open else None)
        self.registry = build(Services(hub=hub, research=self.manager, desk=self.desk,
                                       presenter=self.presenter, day=self.day))
        self.studio.report()
        self.ctx = ToolContext('chat', 1, None, 'Research this field, then show its work.', asked=self.day.here())
        self.ticket = issue(hub, 'turn', asked=self.ctx.asked)
        self.ctx.admission = self.ticket.admission
        self.late, self.runs = [], []

        async def late(run, ctx):
            self.late.append((run.wire(), ctx))

        self.runner = ActionRunner(self.registry, hub, lambda: None, late)

    async def start(self):
        with using(self.ticket):
            runs = await self.runner.run(parse_actions([{'tool': 'start_researcher',
                'args': {'brief': 'Research this field', 'effort': 'quick', 'deliverable': 'research'}}]), self.ctx)
        self.runs.extend(runs)
        await asyncio.wait_for(asyncio.shield(runs[0].sequence), HANG_S)
        assert runs[0].ok is True, runs[0].wire(full=True)
        job = await dispatched(self.manager, self.worker, 0)
        self.session = self.manager.get(job['rid'])
        assert self.session.status == 'running' and self.session._attempt.record['dispatchedAt']
        self.attempt = self.session._attempt
        return runs[0], job

    async def plan(self, raw, numbers):
        with using(self.ticket):
            runs = await self.runner.run(parse_actions(raw), self.ctx, numbers)
        self.runs.extend(runs)
        return runs

    async def finish_sequence(self, runs):
        await asyncio.wait_for(asyncio.shield(runs[0].sequence), HANG_S)
        for _ in range(20):
            await asyncio.sleep(0)

    async def close(self):
        sequences = {r.sequence for r in self.runs if r.sequence is not None}
        tasks = {r.task for r in self.runs if r.task is not None}
        for task in sequences | tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*sequences, *tasks, return_exceptions=True)
        self.ticket.close()
        await cleanup(self.manager, self.worker)


def resource(run):
    assert run.outputs, 'The real operation must publish its original attempt, not only a mutable researcher name.'
    wire = run.outputs[0].wire()
    assert wire['kind'] == 'resource' and isinstance(wire['resource'], dict)
    return wire['resource']


def digest(result):
    return hashlib.sha256(json.dumps(result, ensure_ascii=False, allow_nan=False,
                                    sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def test_start_tool_returns_the_actual_dispatched_attempt_and_original_owner(world, tmp_path):
    async def scenario():
        rig = ResearchRig(world, tmp_path)
        try:
            run, job = await rig.start()
            ref = resource(run)
            assert ref == {'kind': 'research-run', 'researcherId': job['rid'], 'day': rig.session.day,
                           'runId': rig.attempt.id, 'run': rig.attempt.number}
            assert rig.attempt.origin['request_id'] == run.occurrence.request_id
            assert rig.attempt.origin['action_id'] == run.occurrence.action_id
            assert rig.attempt.origin['thread'] == rig.ctx.asked.thread
            assert rig.attempt.delivery.asked is rig.ctx.asked
            assert not rig.attempt.completion.done() and rig.ticket.admission.used == 0
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_wait_keeps_the_live_caller_free_and_then_shows_only_the_original_completed_result(world, tmp_path):
    async def scenario():
        rig = ResearchRig(world, tmp_path)
        try:
            _, job = await rig.start()
            runs = await rig.plan([
                {'tool': 'research_status', 'args': {'researcher': job['rid'], 'wait': True}},
                {'tool': 'show_result', 'args': {'researcher': {'step': 2, 'output': 0}}, 'after_steps': [2]},
            ], [2, 3])
            assert runs[0].ok is None and runs[1].ok is None
            assert not runs[0].sequence.done() and not rig.presenter.open
            # A genuinely separate status call can still run while this plan waits.
            with using(issue(world, 'turn')) as other:
                status = rig.registry.start('research_status', {'researcher': job['rid']},
                    ToolContext('chat', 2, None, 'How is it doing?', asked=rig.day.here()))
                await status.task
                assert status.ok is True and rig.session.name in status.summary
                other.close()
            original_ids = [r.occurrence for r in runs]
            rig.worker.finish(job, 'ORIGINAL worker finding')
            await rig.finish_sequence(runs)
            terminal = await asyncio.shield(rig.attempt.completion)
            assert terminal['status'] == 'done' and terminal['result']['summary'] == 'ORIGINAL worker finding'
            ref = resource(runs[0])
            assert ref == {'kind': 'research-result', 'researcherId': job['rid'], 'day': rig.session.day,
                           'runId': rig.attempt.id, 'run': rig.attempt.number, 'status': 'done',
                           'resultSha256': digest(terminal['result']), 'outputs': []}
            assert [r.ok for r in runs] == [True, True]
            assert rig.presenter.rid == job['rid'] and rig.presenter.open
            assert rig.studio.items['viewer']['shows'] == job['rid']
            assert [r.occurrence for r in runs] == original_ids
            assert rig.ticket.admission.used == 0 and rig.ctx.inspection_slots == []
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('terminal', ['failed', 'timeout', 'cancelled', 'partial'])
def test_unfulfilled_original_attempt_cannot_trigger_the_downstream_screen_change(world, tmp_path, terminal):
    async def scenario():
        rig = ResearchRig(world, tmp_path)
        try:
            _, job = await rig.start()
            runs = await rig.plan([
                {'tool': 'research_status', 'args': {'researcher': job['rid'], 'wait': True}},
                {'tool': 'present', 'args': {'what': 'magnifier', 'do': 'close'}, 'after_steps': [2]},
            ], [2, 3])
            assert runs[0].ok is None and rig.studio.items['magnifier']['open']
            pixels = rig.worker.artifact(job) if terminal == 'partial' else None
            rig.worker.finish(job, 'Not fulfilled', 'timeout' if terminal == 'partial' else terminal)
            await rig.finish_sequence(runs)
            outcome = await asyncio.shield(rig.attempt.completion)
            assert outcome['status'] == terminal
            assert [r.ok for r in runs] == [False, False]
            assert 'depend' in runs[1].summary.lower()
            assert not runs[0].outputs and not runs[1].outputs
            assert rig.studio.items['magnifier']['open'] and not rig.studio.applied
            assert all(r.occurrence.request_id == rig.ticket.admission.request_id for r in runs)
            if pixels is not None:
                assert (rig.session.root / rig.session.day / rig.session.rid / 'held-partial.jpg').read_bytes() == pixels
            assert rig.ticket.admission.used == 0
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('retire', ['cancel', 'day'])
def test_waiter_retirement_does_not_cancel_the_independent_producer_or_release_its_future(world, tmp_path, retire):
    async def scenario():
        rig = ResearchRig(world, tmp_path)
        try:
            _, job = await rig.start()
            runs = await rig.plan([
                {'tool': 'research_status', 'args': {'researcher': job['rid'], 'wait': True}},
                {'tool': 'present', 'args': {'what': 'magnifier', 'do': 'close'}, 'after_steps': [2]},
            ], [2, 3])
            assert runs[0].ok is None and not rig.attempt.completion.done()
            if retire == 'cancel':
                rig.ticket.cancel('original user cancelled the dependent plan')
            else:
                rig.ticket.generations.bump('session', why='a new day began')
            await asyncio.wait_for(asyncio.shield(runs[0].sequence), HANG_S)
            assert [r.ok for r in runs] == [False, False]
            assert not rig.attempt.completion.done() and not rig.attempt.completion.cancelled()
            assert rig.session.status == 'running' and rig.worker.cancelled == []
            assert rig.studio.items['magnifier']['open'] and not rig.studio.applied
            rig.worker.finish(job, 'Useful independent research survives')
            outcome = await asyncio.shield(rig.attempt.completion)
            assert outcome['status'] == 'done' and outcome['result']['summary'] == 'Useful independent research survives'
            assert rig.ticket.admission.used == 0
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_wait_resolves_the_exact_successful_start_step_without_a_latest_researcher_fallback(world, tmp_path):
    async def scenario():
        rig = ResearchRig(world, tmp_path)
        try:
            start, job = await rig.start()
            await rig.manager.start('An unrelated newer researcher', effort='quick')
            other = await dispatched(rig.manager, rig.worker, 1)
            rig.worker.finish(other, 'UNRELATED newer result')
            runs = await rig.plan([{'tool': 'research_status',
                'args': {'researcher': {'step': 1, 'output': 0}, 'wait': True}, 'after_steps': [1]}], [2])
            assert runs[0].ok is None
            rig.worker.finish(job, 'ORIGINAL bound result')
            await rig.finish_sequence(runs)
            assert runs[0].ok is True
            ref = resource(runs[0])
            assert ref['researcherId'] == job['rid'] and ref['runId'] == rig.attempt.id
            assert 'ORIGINAL bound result' in runs[0].summary and 'UNRELATED newer result' not in runs[0].summary
            assert resource(start)['researcherId'] == job['rid']
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_a_replacement_attempt_cannot_satisfy_an_old_completion_or_show_its_new_version(world, tmp_path):
    async def scenario():
        rig = ResearchRig(world, tmp_path)
        try:
            _, job = await rig.start()
            runs = await rig.plan([{'tool': 'research_status',
                'args': {'researcher': job['rid'], 'wait': True}}], [2])
            assert runs[0].ok is None
            rig.worker.finish(job, 'ORIGINAL complete result')
            await rig.finish_sequence(runs)
            original_ref = resource(runs[0])
            await rig.manager.followup(job['rid'], 'A separately requested continuation', mode='continue',
                                       origin={'channel': 'chat', 'thread': rig.day.active_id})
            replacement = await dispatched(rig.manager, rig.worker, 1)
            assert rig.session._attempt is not rig.attempt
            rig.worker.finish(replacement, 'REPLACEMENT result must not stand in')
            showing = await rig.plan([{'tool': 'show_result',
                'args': {'researcher': {'step': 2, 'output': 0}}, 'after_steps': [2]}], [3])
            await rig.finish_sequence(showing)
            assert showing[0].ok is False and not rig.presenter.open
            assert not showing[0].outputs
            assert resource(runs[0]) == original_ref
            assert original_ref['runId'] == rig.attempt.id
            original = await asyncio.shield(rig.attempt.completion)
            assert original['result']['summary'] == 'ORIGINAL complete result'
            assert rig.session.result['summary'] == 'REPLACEMENT result must not stand in'
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_legacy_nonwaiting_status_and_plain_result_show_keep_their_existing_usefulness(world, tmp_path):
    async def scenario():
        rig = ResearchRig(world, tmp_path)
        try:
            _, job = await rig.start()
            status = await rig.plan([{'tool': 'research_status', 'args': {'researcher': job['rid']}}], [2])
            await rig.finish_sequence(status)
            assert status[0].ok is True and rig.session.status == 'running'
            assert not rig.attempt.completion.done()
            rig.worker.finish(job, 'Actual ordinary result')
            showing = await rig.plan([{'tool': 'show_result', 'args': {'researcher': job['rid']}}], [3])
            await rig.finish_sequence(showing)
            assert showing[0].ok is True and rig.presenter.rid == job['rid'] and rig.presenter.open
            assert rig.studio.items['viewer']['shows'] == job['rid']
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_successful_answer_followup_waits_and_reads_its_own_reply_without_showing_old_main_work(world, tmp_path):
    async def scenario():
        rig = ResearchRig(world, tmp_path)
        try:
            _, job = await rig.start()
            rig.worker.finish(job, 'PRIOR main work remains retained')
            main = json.loads(json.dumps(rig.session.result))
            await rig.manager.followup(job['rid'], 'What did the later observation add?', mode='answer',
                                       origin={'channel': 'chat', 'thread': rig.ctx.asked.thread})
            followup = await dispatched(rig.manager, rig.worker, 1)
            attempt = rig.session._attempt
            runs = await rig.plan([{'tool': 'research_status',
                'args': {'researcher': job['rid'], 'wait': True}}], [2])
            assert runs[0].ok is None and not attempt.completion.done()
            rig.worker.finish(followup, 'EXACT followup reply about the later observation')
            await rig.finish_sequence(runs)
            outcome = await asyncio.shield(attempt.completion)
            assert outcome['status'] == 'done' and outcome['result']['summary'].startswith('EXACT followup')
            assert rig.session.result == main and rig.session.last['mode'] == 'answer'
            assert rig.session.last['runId'] == attempt.id
            assert runs[0].ok is True, runs[0].wire(full=True)
            reference = resource(runs[0])
            assert reference['runId'] == attempt.id and reference['resultSha256'] == digest(outcome['result'])
            reading = await rig.plan([{'tool': 'read_result',
                'args': {'researcher': {'step': 2, 'output': 0}, 'part': 'summary'}, 'after_steps': [2]}], [3])
            await rig.finish_sequence(reading)
            assert reading[0].ok is True
            assert 'EXACT followup reply' in reading[0].summary and 'PRIOR main work' not in reading[0].summary
            # The existing viewer has only the main result; absence of a reply renderer
            # must be a real refusal rather than confirming the old main panel as this reply.
            showing = await rig.plan([{'tool': 'show_result',
                'args': {'researcher': {'step': 2, 'output': 0}}, 'after_steps': [2]}], [4])
            await rig.finish_sequence(showing)
            assert showing[0].ok is False and not rig.presenter.open
            assert not showing[0].outputs and resource(runs[0]) == reference
            assert rig.session.result == main and rig.ticket.admission.used == 0
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('produced', [True, False], ids=['actual-new-version', 'no-new-output'])
def test_output_only_revision_completion_binds_only_the_actual_new_version(world, tmp_path, produced):
    async def scenario():
        rig = ResearchRig(world, tmp_path)
        try:
            _, job = await rig.start()
            # Both files are actual retained synthetic camera JPEGs. Labels are
            # controlled worker output, not a claim of model or browser quality.
            pixels = world.source.get(world.latest_info.id).jpeg
            (rig.session.root / rig.session.day / rig.session.rid / 'labels-v1.jpg').write_bytes(pixels)
            rig.manager._event({'rid': job['rid'], 'event': 'output', 'kind': 'image',
                'path': 'labels-v1.jpg', 'caption': 'Prior labels', 'generated': False,
                'content': {'kind': 'labels', 'items': [{'name': 'PRIOR label', 'where': 'centre', 'box': None}]}})
            old = json.loads(json.dumps(rig.session.outputs[-1]))
            rig.worker.finish(job, 'PRIOR main written work')
            main = json.loads(json.dumps(rig.session.result))
            await rig.manager.followup(job['rid'], 'Correct the label', mode='revise', target=old['id'],
                                       origin={'channel': 'chat', 'thread': rig.ctx.asked.thread})
            followup = await dispatched(rig.manager, rig.worker, 1)
            attempt = rig.session._attempt
            runs = await rig.plan([{'tool': 'research_status',
                'args': {'researcher': job['rid'], 'wait': True}}], [2])
            assert runs[0].ok is None
            if produced:
                (rig.session.root / rig.session.day / rig.session.rid / 'labels-v2.jpg').write_bytes(pixels)
                rig.manager._event({'rid': job['rid'], 'event': 'output', 'kind': 'image',
                    'path': 'labels-v2.jpg', 'caption': 'Actual corrected labels', 'generated': False,
                    'replaces': old['id'], 'change': 'Correct the label',
                    'content': {'kind': 'labels', 'items': [{'name': 'EXACT new label', 'where': 'centre', 'box': None}]}})
            # Revision completion intentionally has no replacement main result.
            rig.manager._event({'rid': followup['rid'], 'event': 'done', 'status': 'done', 'result': None, 'spent': 0.01})
            await rig.finish_sequence(runs)
            outcome = await asyncio.shield(attempt.completion)
            assert outcome['result'] is None and rig.session.result == main
            assert rig.session.last['mode'] == 'revise' and rig.session.last['runId'] == attempt.id
            assert outcome['status'] == ('done' if produced else 'failed')
            assert runs[0].ok is produced, runs[0].wire(full=True)
            if produced:
                new = rig.session.outputs[-1]
                assert new['replaces'] == old['id'] and new['version'] == old['version'] + 1
                ref = resource(runs[0])
                assert ref['runId'] == attempt.id and ref['resultSha256'] == digest(None)
                assert ref['outputs'] == [{'id': new['id'], 'version': new['version']}]
                assert old == rig.session.outputs[0]
                downstream = await rig.plan([
                    {'tool': 'read_result', 'args': {'researcher': {'step': 2, 'output': 0}, 'part': 'labels'}, 'after_steps': [2]},
                    {'tool': 'show_result', 'args': {'researcher': {'step': 2, 'output': 0}}, 'after_steps': [2]},
                ], [3, 4])
                await rig.finish_sequence(downstream)
                assert [run.ok for run in downstream] == [True, True]
                assert 'EXACT new label' in downstream[0].summary and 'PRIOR label' not in downstream[0].summary
                wire = rig.session.wire()
                shown = wire['items'][rig.presenter.index]
                assert rig.presenter.open and shown['id'] == new['id']
                selected = next(output for output in wire['outputs'] if output['id'] == shown['id'])
                assert selected['version'] == ref['outputs'][0]['version'] == new['version']
                assert selected['replaces'] == old['id'] and selected['current'] is True
                assert selected['path'] == 'labels-v2.jpg'
            else:
                assert not runs[0].outputs and rig.session.outputs == [old]
                assert not rig.presenter.open
            assert rig.ticket.admission.used == 0
        finally:
            await rig.close()
    asyncio.run(scenario())
