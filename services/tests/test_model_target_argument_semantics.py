"""TEST-only: installed capability argument meaning through App/COMMANDS/Brain.

Synthetic camera ingress, canned external vision and worker RPC masks; all writes
use temporary local Paths. App, Sight, Registry, ActionRunner, operation Runner,
Tickets, captures, tool handlers and receipts remain real. No server, worker,
browser or actual model is started. Chat prose is deliberately not an oracle.
Authored without execution; Root must distinguish setup failures from feature RED.
"""
import asyncio
import copy
import hashlib
import inspect
import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator import tools
from orchestrator.app import App
from orchestrator.brain.admission import RequestAdmission
from orchestrator.brain.dispatch import in_answer_phase
from orchestrator.capture import captures
from orchestrator.commands import COMMANDS
from orchestrator.hub import Client
from orchestrator.paths import Paths
from orchestrator.ticket import current, issue, using


BOOT = 'synthetic-target-argument-camera'
REFERENCE = 'Synthetic retained reference whose literal title token is s2.'


def reply(actions=()):
    return {'answer': 'Synthetic provider reply.', 'targets': [],
            'actions': list(actions), 'intent': 'request', 'needs_look': False,
            'observation_scope': 'none', 'input_refs': []}


def action(tool, **args):
    return {'tool': tool, 'args': args}


def sight_reply(*, swapped=False):
    things = [
        {'name': 'synthetic left structure', 'where': 'upper left',
         'box_2d': [220, 220, 340, 340], 'confidence': 0.95},
        {'name': 'synthetic right structure', 'where': 'lower right',
         'box_2d': [620, 620, 740, 740], 'confidence': 0.95},
    ]
    return {'summary': 'Two synthetic structures.', 'specimen': '',
            'focus': 'clear', 'things': list(reversed(things)) if swapped else things}


class ArgumentRig:
    def __init__(self, tmp_path, monkeypatch):
        self.app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=True)
        settings = self.app.hub.settings
        settings.fast = settings.vision = 'gemini-3.5-flash'
        settings.compare = ''
        settings.pointing = 'box'
        settings.outline = 'edgetam'
        settings.closeup = 'single'
        settings.outcome_lines = settings.speculate = 'off'
        settings.outline_check = settings.taught = settings.seen = 'off'
        settings.naming = 'per-path'  # naming drawn outlines is a background model call of its own (option NAMING)
        self.app.hub.execution.set_enabled(False)
        self.client = Client(None)
        self.app.hub.clients.add(self.client)
        self.initial = reply()
        self.sight_data = None
        self.later_sight = False
        self.sends, self.started, self.masks = [], [], []
        self.entries, self.memory_prompts = [], []
        self.seq = 0
        pixels = np.full((640, 640), 5, np.uint8)
        disc = np.zeros_like(pixels)
        cv2.circle(disc, (320, 320), 280, 1, -1)
        texture = np.random.default_rng(641).integers(95, 210, pixels.shape, dtype=np.uint8)
        pixels[disc.astype(bool)] = texture[disc.astype(bool)]
        ok, encoded = cv2.imencode('.jpg', pixels, [cv2.IMWRITE_JPEG_QUALITY, 96])
        assert ok, 'SETUP: synthetic camera input must be an actual JPEG'
        self.jpeg = encoded.tobytes()
        self.record = self.app.hub.resources.results.add('compare', {}, REFERENCE,
            values={'reference': REFERENCE}, request='Keep the synthetic s2 reference.',
            thread=self.app.day.active_id)

        actual_facts_prompt = self.app.memory.facts_prompt

        def trace_facts_prompt():
            prompt = actual_facts_prompt()
            self.memory_prompts.append((self.app.memory, prompt))
            return prompt

        monkeypatch.setattr(self.app.memory, 'facts_prompt', trace_facts_prompt)

        def provider_entry(images, prompt, kwargs):
            ticket, task = current(), asyncio.current_task()
            coro = task.get_coro() if task is not None else None
            entry = SimpleNamespace(ticket=ticket, admission=getattr(ticket, 'admission', None),
                task=task, task_code=getattr(coro, 'cr_code', None),
                images=tuple(images), prompt=prompt, arguments=copy.deepcopy(kwargs),
                active_admission=None, attempt=None, answer_scope=in_answer_phase(),
                sight_producer=None, memory_producer=None, memory_prompt=None,
                memory_agent=None, memory_hub=None, look_producer=None, model_producer=None,
                look_source=None, model_source=None, camera_held=None, camera_crop=None,
                camera_jpeg=None, camera_shape=None, stack=[], completion='entered', error=None)
            # Read actual executing frames, rather than borrowing the admission's
            # newest historical Attempt or the inherited Ticket. No frame is kept.
            frame = inspect.currentframe().f_back
            try:
                while frame is not None:
                    code = frame.f_code
                    entry.stack.append(code)
                    if code is self.app.sight.look.__func__.__code__ and entry.attempt is None:
                        entry.sight_producer = self.app.sight
                    if code is self.app.memory.ask.__code__:
                        entry.memory_producer = self.app.memory
                        entry.memory_prompt = frame.f_locals.get('prompt')
                        entry.memory_agent = frame.f_locals.get('agent')
                        entry.memory_hub = frame.f_locals.get('hub')
                    if code is inspect.unwrap(self.app.agent.look).__code__:
                        entry.look_producer = frame.f_locals.get('self')
                        entry.look_source = frame.f_locals['view'][0]
                    if code is self.app.agent._ask_model.__func__.__code__:
                        entry.model_producer = frame.f_locals.get('self')
                        entry.model_source = frame.f_locals.get('info')
                        entry.camera_held = ticket.held(entry.model_source.id) if ticket is not None else None
                        entry.camera_crop = frame.f_locals.get('crop')
                        entry.camera_jpeg = frame.f_locals.get('jpeg')
                        entry.camera_shape = tuple(frame.f_locals['image'].shape)
                    if code is RequestAdmission.dispatch.__code__ and entry.attempt is None:
                        entry.active_admission = frame.f_locals.get('self')
                        entry.attempt = frame.f_locals.get('attempt')
                    frame = frame.f_back
            finally:
                del frame
            self.entries.append(entry)
            return entry

        async def external_provider(images, prompt, **kwargs):
            entry = provider_entry(images, prompt, kwargs)
            try:
                if entry.memory_producer is self.app.memory:
                    # This actual independent producer can run after a thread
                    # change, outside any answer Ticket. Give it its own shape.
                    data = {'facts': []}
                elif self.sight_data is not None:
                    data, self.sight_data = self.sight_data, None
                else:
                    ticket = current()
                    self.sends.append(SimpleNamespace(ticket=ticket, admission=ticket.admission,
                                                      images=tuple(images), prompt=prompt))
                    first = len(self.sends) == 1
                    if first and self.later_sight:
                        # Publish through the actual Sight parser while the original
                        # answer is awaiting its external provider. IDs are renumbered.
                        await self.publish_sight(swapped=True)
                    data = self.initial if first else reply()
                result = SimpleNamespace(data=copy.deepcopy(data), latency_ms=0.0,
                                         model='canned-target-argument-provider', error=None)
            except BaseException as exc:
                entry.completion, entry.error = 'raised', type(exc).__name__
                raise
            entry.completion = 'returned'
            return result

        async def external_worker(op, timeout=30.0, priority=None, **payload):
            if op != 'segment':
                raise RuntimeError('synthetic worker supplies outline masks only')
            x0, y0, x1, y1 = payload['box']
            self.masks.append(SimpleNamespace(ticket=current(), payload=copy.deepcopy(payload)))
            return {'polygons': [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]],
                    'score': 0.99, 'ms': 0.0}

        monkeypatch.setattr(self.app.agent, 'vision', lambda _model: SimpleNamespace(ask=external_provider))
        # Real GpuClient objects; only external readiness/RPC input is synthetic.
        for worker in (self.app.hub.gpu, self.app.hub.gpu_heavy,
                       self.app.hub.gpu_follow, self.app.hub.gpu_molmo):
            monkeypatch.setattr(worker, 'ready', True)
            monkeypatch.setattr(worker, 'call', external_worker)
        actual_start = self.app.brain.registry.start

        def trace_start(name, args, ctx, run=None):
            ticket = current()
            made = actual_start(name, args, ctx, run=run)
            selected = ctx.camera_selection
            self.started.append(SimpleNamespace(run=made, ctx=ctx, ticket=ticket,
                occurrence=made.occurrence, held=ticket.held(selected.capture.id)
                if selected is not None and selected.capture is not None else None))
            return made

        monkeypatch.setattr(self.app.brain.registry, 'start', trace_start)

    async def prepare(self):
        for _ in range(4):
            self.seq += 1
            await self.app.hub.source._accept(self.jpeg, seq=self.seq, boot=BOOT)
        info = self.app.hub.latest_info
        assert info is not None and info.fov is not None, 'SETUP: actual intake must detect the eyepiece'
        self.capture = captures(self.app.hub).take(info.id)
        assert self.capture.jpeg == self.jpeg and self.capture.boot == BOOT
        assert self.capture.seq == self.seq

    async def publish_sight(self, *, swapped=False):
        self.sight_data = sight_reply(swapped=swapped)
        view = await self.app.sight.look('gemini-3.5-flash')
        assert view is self.app.sight.view and len(view.things) == 2
        return view

    async def seed(self, thing):
        x0, y0, x1, y1 = thing.box
        ticket = issue(self.app.hub, 'turn')
        try:
            with using(ticket):
                obj = await tools.add_outline(self.app.hub, self.capture,
                    [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]],
                    source='click', label=thing.name, status='outline')
        finally:
            ticket.close()
        assert obj.track == 'tracking', 'SETUP: actual producer must verify the seeded outline'
        return obj

    async def ask(self, question, planned):
        self.initial = reply([planned])
        return await COMMANDS['ask'](self.app, self.client,
            {'type': 'ask', 'text': question, 'frameId': self.capture.id})

    def original_run(self, result, tool, original_view):
        starts = [s for s in self.started if s.run.tool == tool]
        assert len(starts) == 1, 'BEHAVIOR: the actual selected operation executes once'
        start = starts[0]
        assert start.ctx.seen_view == original_view
        if original_view is not None:
            assert start.ctx.seen_view is not original_view
            assert start.ctx.seen_view.things is not original_view.things
            assert all(frozen is not live for frozen, live in
                       zip(start.ctx.seen_view.things, original_view.things, strict=True))
            # The operation owns the admitted facts, not a mutable later Sight view.
            name = original_view.things[0].name
            try:
                original_view.things[0].name = 'A later unrelated synthetic Sight label'
                assert start.ctx.seen_view.things[0].name == name
            finally:
                original_view.things[0].name = name
        assert start.ticket is self.sends[0].ticket
        assert start.held is self.capture, 'BEHAVIOR: the original request owns its actual source'
        assert start.ctx.admission is start.ticket.admission is self.sends[0].admission
        assert all(s.ticket is start.ticket and s.admission is start.ctx.admission for s in self.sends)
        self.assert_provider_accounting(start.ticket)
        assert start.run.occurrence is start.occurrence
        assert start.occurrence.request_id == start.ctx.admission.request_id
        number, steps = start.run.step
        assert steps.runs[number] is start.run and steps.occurrences[number] is start.occurrence
        receipts = [r for r in result['actions'] if r['actionId'] == start.occurrence.action_id]
        assert len(receipts) == 1 and receipts[0]['requestId'] == start.occurrence.request_id
        return start.run

    def assert_provider_accounting(self, ticket):
        admission = ticket.admission
        attempts = {a.attempt_id: a for a in admission.attempts}
        charged, sight, memory, unknown = [], [], [], []
        manifest = []
        for entry in self.entries:
            # A nested synthetic Sight publication can execute inside an outer
            # answer await. Its actual Sight producer precedes that dispatch
            # frame; it is retained separately, not an additional charged send.
            if entry.sight_producer is not None:
                owner = 'actual Sight input'
                sight.append(entry)
            elif entry.answer_scope:
                owner = 'original charged answer'
                charged.append(entry)
            elif entry.memory_producer is not None:
                owner = 'actual independent Memory input'
                memory.append(entry)
            else:
                owner = 'unrecognized uncharged provider input'
                unknown.append(entry)
            attempt = entry.attempt
            actual = attempts.get(attempt.attempt_id) if attempt is not None else None
            manifest.append({'owner': owner, 'ticketId': getattr(entry.ticket, 'id', None),
                'requestId': getattr(entry.admission, 'request_id', None),
                'activeRequestId': getattr(entry.active_admission, 'request_id', None),
                'attemptId': getattr(attempt, 'attempt_id', None),
                'phase': getattr(attempt, 'phase', None),
                'status': getattr(actual, 'status', None), 'answerScope': entry.answer_scope,
                'task': getattr(entry.task_code, 'co_qualname', None),
                'producerFile': getattr(entry.task_code, 'co_filename', None),
                'stack': [code.co_qualname for code in entry.stack],
                'images': [{'bytes': len(image), 'sha256': hashlib.sha256(image).hexdigest()}
                           for image in entry.images],
                'cameraProducer': {'look': entry.look_producer is self.app.agent,
                    'model': entry.model_producer is self.app.agent,
                    'frameId': getattr(entry.model_source, 'id', None),
                    'boot': getattr(entry.model_source, 'boot', None),
                    'seq': getattr(entry.model_source, 'seq', None),
                    'rawSha256': hashlib.sha256(entry.model_source.jpeg).hexdigest()
                        if entry.model_source is not None else None,
                    'originalHeld': entry.camera_held is self.capture,
                    'shape': entry.camera_shape,
                    'crop': [entry.camera_crop.x, entry.camera_crop.y,
                             entry.camera_crop.width, entry.camera_crop.height]
                        if entry.camera_crop is not None else None},
                'arguments': entry.arguments, 'completion': entry.completion,
                'error': entry.error, 'prompt': entry.prompt})
        self.provider_accounting = manifest
        print('PROVIDER_ACCOUNTING ' + json.dumps(manifest))
        assert len(charged) + len(sight) + len(memory) + len(unknown) == len(self.entries), manifest
        assert not unknown, manifest
        assert len(self.sends) == len(charged), manifest
        assert charged and admission.used == len(charged) <= 3, manifest
        assert all(e.ticket is ticket and e.admission is admission and
                   e.active_admission is admission and e.attempt is not None and
                   e.answer_scope and e.memory_producer is None for e in charged), manifest
        assert {e.attempt.attempt_id for e in charged} == set(attempts), manifest
        assert len({e.attempt.attempt_id for e in charged}) == len(charged), manifest
        assert all(e.attempt.request_id == admission.request_id and
                   e.attempt.status == 'running' and
                   attempts[e.attempt.attempt_id].status == 'done' for e in charged), manifest
        for entry in charged:
            if entry.attempt.phase == 'look':
                # A failed real operation may legitimately use the original
                # camera for recovery. Attribute it to its actual Agent producer,
                # never to the newer Sight numbering or a free extra dispatch.
                assert entry.look_producer is entry.model_producer is self.app.agent, manifest
                assert entry.look_source is entry.model_source is entry.camera_held is self.capture, manifest
                assert entry.model_source.boot == BOOT and entry.model_source.seq == self.seq, manifest
                assert entry.model_source.jpeg == self.jpeg and entry.camera_shape == (640, 640, 3), manifest
                assert len(entry.images) == 1 and entry.images[0] == entry.camera_jpeg, manifest
                decoded = cv2.imdecode(np.frombuffer(entry.images[0], np.uint8), cv2.IMREAD_COLOR)
                assert decoded is not None and 0 < min(decoded.shape[:2]) <= max(decoded.shape[:2]) <= 1024, manifest
                assert decoded.shape[:2] == (entry.camera_crop.height, entry.camera_crop.width), manifest
                assert 0 <= entry.camera_crop.x and 0 <= entry.camera_crop.y, manifest
                assert entry.camera_crop.x + entry.camera_crop.width <= 640, manifest
                assert entry.camera_crop.y + entry.camera_crop.height <= 640, manifest
            else:
                assert entry.look_producer is entry.model_producer is None, manifest
                if entry.images:
                    assert entry.attempt.phase == 'final' and len(entry.images) == 1, manifest
                    assert 'original request camera frame' in entry.prompt and self.capture.boot in entry.prompt, manifest
                    assert f'"seq": {self.seq}' in entry.prompt, manifest
        for entry in sight:
            assert entry.sight_producer is self.app.sight and entry.memory_producer is None, manifest
            assert entry.images and entry.completion == 'returned', manifest
            # An outer charged Attempt is allowed only for this deliberate
            # nested publication; it is never miscounted as another attempt.
            if entry.attempt is not None:
                assert entry.active_admission is admission and entry.ticket is ticket, manifest
                assert entry.attempt.attempt_id in attempts, manifest
        for entry in memory:
            assert entry.memory_producer is self.app.memory, manifest
            assert entry.task_code is type(self.app.memory).refresh_facts.__code__, manifest
            assert entry.memory_agent is self.app.agent and entry.memory_hub is self.app.hub, manifest
            assert entry.memory_prompt == entry.prompt, manifest
            assert any(owner is self.app.memory and prompt == entry.prompt
                       for owner, prompt in self.memory_prompts), manifest
            assert entry.attempt is None and entry.active_admission is None and not entry.answer_scope, manifest
            assert ((entry.ticket is None and entry.admission is None) or
                    (entry.ticket is ticket and entry.admission is admission)), manifest
            assert not entry.images and entry.arguments == {'max_output_tokens': 800}, manifest
            assert entry.completion == 'returned' and entry.error is None, manifest

    async def close(self):
        tickets = {s.ticket for s in self.sends}
        tasks = {t for ticket in tickets for t in ticket.tasks}
        tasks.update(s.run.task for s in self.started if s.run.task is not None)
        tasks.update(getattr(self.app.brain, '_query_answers', ()))
        tasks.update(getattr(self.app.brain, '_drafts', ()))
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for ticket in tickets:
            ticket.close()
        self.app.hub._analysis.shutdown(wait=True)
        self.app.hub._background.shutdown(wait=True)


@pytest.mark.parametrize('tool,field', [
    ('resources', 'query'), ('name_outline', 'name'), ('name_specimen', 'name'),
])
def test_literal_sight_shaped_text_keeps_capability_meaning(tmp_path, monkeypatch, tool, field):
    async def scenario():
        rig = ArgumentRig(tmp_path, monkeypatch)
        try:
            await rig.prepare()
            original = await rig.publish_sight()
            named = await rig.seed(original.things[0])
            before = set(rig.app.hub.scene.objects)
            args = {field: 's2'}
            if tool == 'name_outline':
                args['object'] = named.id
            question = {'resources': 'Find saved resources with the literal text s2.',
                        'name_outline': 'Call this outline s2.',
                        'name_specimen': 'The specimen is named s2.'}[tool]
            result = await rig.ask(question, action(tool, **args))
            run = rig.original_run(result, tool, original)
            assert run.args[field] == 's2', 'MISSING_FEATURE: literal capability text must not become an outline ID'
            assert run.ok is True
            assert set(rig.app.hub.scene.objects) == before and not rig.masks, (
                'MISSING_FEATURE: a literal text argument must not produce an unrelated outline')
            if tool == 'resources':
                assert rig.record['id'] in run.summary and REFERENCE in run.summary
            elif tool == 'name_outline':
                assert named.label == 's2' and named.status == 'confirmed'
            else:
                assert rig.app.day.active.subject == 's2'
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_declared_object_reference_uses_original_sight_target(tmp_path, monkeypatch):
    async def scenario():
        rig = ArgumentRig(tmp_path, monkeypatch)
        try:
            await rig.prepare()
            original = await rig.publish_sight()
            left = await rig.seed(original.things[0])
            right = await rig.seed(original.things[1])
            rig.later_sight = True
            result = await rig.ask('Select the second structure from this look.', action('select', object='s2'))
            run = rig.original_run(result, 'select', original)
            assert rig.app.sight.view is not original
            assert rig.app.sight.view.things[1].box == original.things[0].box
            assert run.ok is True and run.args['object'] == right.id
            assert rig.app.hub.scene.selected == right.id != left.id
            assert set(rig.app.hub.scene.objects) == {left.id, right.id} and not rig.masks
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_explicit_highlight_still_produces_original_target_outline(tmp_path, monkeypatch):
    async def scenario():
        rig = ArgumentRig(tmp_path, monkeypatch)
        try:
            await rig.prepare()
            original = await rig.publish_sight()
            rig.later_sight = True
            result = await rig.ask('Highlight the second structure from this look.', action('highlight', things=['s2']))
            run = rig.original_run(result, 'highlight', original)
            assert run.ok is True and run.args['things'] == ['s2']
            assert len(rig.app.hub.scene.objects) == len(rig.masks) == 1
            obj = next(iter(rig.app.hub.scene.objects.values()))
            assert obj.source == 'ai:sight' and obj.label == original.things[1].name
            assert obj.origin == rig.capture.id and obj.track == 'tracking'
            assert obj.origin_box == original.things[1].box
            mask = rig.masks[0]
            assert mask.ticket is rig.sends[0].ticket
            assert mask.payload['frame_id'] == rig.capture.id and mask.payload['jpeg'] == rig.capture.jpeg
            assert mask.payload['box'] == list(original.things[1].box)
            assert obj.made_by['execution'], 'BEHAVIOR: the real operation Runner records what produced the outline'
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_absent_original_sight_does_not_rebind_to_later_s2(tmp_path, monkeypatch):
    async def scenario():
        rig = ArgumentRig(tmp_path, monkeypatch)
        try:
            await rig.prepare()
            assert rig.app.sight.view is None
            rig.later_sight = True
            result = await rig.ask('Select s2 from the look attached to this request.', action('select', object='s2'))
            run = rig.original_run(result, 'select', None)
            assert rig.app.sight.view is not None, 'SETUP: a later actual Sight publication must exist'
            assert not rig.app.hub.scene.objects and not rig.masks, (
                'MISSING_FEATURE: missing original Sight must not borrow a later look or produce its target')
            assert run.ok is False and rig.app.hub.scene.selected is None
        finally:
            await rig.close()
    asyncio.run(scenario())
