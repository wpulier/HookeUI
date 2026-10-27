"""TEST-only installed tab adapter wire through real server/App/Screen/Registry.

AppStudio is the unchanged EXTERNAL structural renderer. Its native windows are
opened by the real present tool/PreparedSources owner using real retained JPEGs.
Client, transport hello, Screen pin, Ticket, shared memory, acquisition, decode,
provider admission and input manifests are production actors. Track acquisition
is tested separately in workspace-capture.test.ts; these packets are explicitly
synthetic tab JPEG/adapter outputs, not browser pixels or atomic root paint proof.
No capture-authority callback, successful ToolRun or ACK is manufactured here.
"""
import asyncio
import base64
import copy
import gc
import hashlib
import json
import time
import weakref
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator.app import App
from orchestrator.brain.registry import ToolContext
from orchestrator.capture import RUN, captures
from orchestrator.commands import COMMANDS
from orchestrator.hub import Client
from orchestrator.paths import Paths
from orchestrator.server import create_app
from orchestrator.ticket import current, issue, using
from orchestrator.workspace_observation import ClientRef
from orchestrator.workspace_transport import WorkspaceTransport
from tests.test_workspace_actions_app import AppStudio, STUDIOS, open_window, saved_picture
from tests.test_mixed_camera_retained_context import intake_camera
from tests.waiting import HANG_S

ORIGIN = 'http://127.0.0.1:5173'
MODEL_TEXT = 'I interpreted only the supplied dated tab image; individual root paint is unknown.'


class InstalledStudio(AppStudio, Client):
    """External renderer/socket headers, with the actual Client queue and offer."""
    # AppStudio's lightweight JSON delivery is for synthetic rendering only.
    # This installed adapter exercises the real control queue and binary frame slot.
    offer = Client.offer

    def __init__(self, app, origin=ORIGIN):
        AppStudio.__init__(self, app)
        Client.__init__(self, SimpleNamespace(headers={} if origin is None else {'origin': origin}))


class Installed:
    def __init__(self, tmp_path, *, configured=True, origin=ORIGIN):
        if configured:
            self.web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
            self.app = self.web.state.app
        else:
            self.web = None
            self.app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
            self.app.workspace_transport = WorkspaceTransport(self.app)
        self.studio = InstalledStudio(self.app, origin)
        self.app.hub.clients.add(self.studio)
        self.requests, self.tasks, self.runs = [], [], []
        original = self.app.hub.broadcast

        def external_renderer(message):
            original(message)
            self.studio.hear(message)

        self.app.hub.broadcast = self.app.hub.screen.broadcast = external_renderer
        STUDIOS[id(self.app)] = weakref.ref(self.studio)
        self.app.hub.settings.fast = 'gemini-3.5-flash'
        self.app.hub.settings.compare = ''
        self.app.hub.settings.pointing = 'box'
        self.app.hub.settings.closeup = 'single'
        self.app.hub.settings.outcome_lines = 'off'

    async def initialize(self):
        ready = await COMMANDS['workspace-hello'](self.app, self.studio, {
            'type': 'workspace-hello', 'schema': 'workspace-origin-v2', 'requestId': 'external-hello',
            'workspaceId': 'synthetic-workspace', 'clientId': 'external-installed-client', 'pageEpoch': 'external-installed-page'})
        assert ready['ok'] is True and ready['appRunId'] == RUN
        self.ref = ClientRef(**{ {'workspaceId':'workspace_id', 'clientId':'client_id', 'pageEpoch':'page_epoch', 'connectionEpoch':'connection_epoch'}[k]:v
                                for k,v in ready['client'].items()})
        self.studio.client_ref = ready['client']
        for field, attr in [('workspaceId','workspace_id'),('clientId','client_id'),('pageEpoch','page_epoch'),('connectionEpoch','connection_epoch')]:
            setattr(self.studio, attr, ready['client'][field])
        await self.studio.send_report()
        self.entry, self.source_jpeg = saved_picture(self.app, value=73)
        self.window, made, _ctx = await open_window(self.app, self.entry)
        assert made.ok is True
        observed = self.screen.observation(self.studio).wire()
        item = next(row for row in observed['items'] if row['id'] == self.window)
        assert item['open'] and item['visible'] and item['load']['state'] == 'drawn'
        source = next(row for row in observed['sources'] if row['key'] == item['sourceKey'])
        assert source['identity']['resource']['id'] == self.entry.id
        assert self.app.hub.resources.picture(self.entry.id, 0, self.entry.day)[0] == self.source_jpeg
        assert not self.screen._looks  # useful owner/source preconditions, not feature absence
        self.analysis_baseline = captures(self.app.hub).memory.reserved['analysis']
        return self

    @property
    def screen(self):
        return self.app.hub.screen

    async def next_look(self):
        while True:
            wire = await asyncio.wait_for(self.studio.control.get(), HANG_S)
            message = json.loads(wire) if isinstance(wire, str) else wire
            if isinstance(message, dict) and message.get('type') == 'screen-look':
                return message

    async def start(self):
        request = issue(self.app.hub, 'turn', asked=self.app.day.here(), channel='chat')
        self.requests.append(request)
        pinned = self.screen.pin(self.studio, request_id=request.admission.request_id)
        ctx = ToolContext('chat', 1, None, 'Inspect this originating tab', asked=request.asked,
            admission=request.admission, client=self.studio, workspace_id=self.ref.workspace_id,
            request_snapshot=pinned, request_id=request.admission.request_id)
        with using(request):
            run = self.app.brain.registry.start('look_at_screen', {}, ctx)
        self.runs.append(run)
        look = await self.next_look()
        assert look['client'] == self.ref.wire()
        assert look['requestId'] == request.admission.request_id and look['originSnapshotId'] == pinned.snapshot_id
        assert captures(self.app.hub).memory.reserved['analysis'] > self.analysis_baseline
        assert request.admission.used == 0
        return request, pinned, ctx, run, look

    async def paired_report(self):
        # A new externally observed sequence with identical layout. Do not forge
        # source/root facts: take the actual renderer report already accepted above.
        report = copy.deepcopy(self.screen.observation(self.studio).wire())
        report.update(type='screen', seq=report['seq'] + 1, capturedMonoMs=time.monotonic()*1000)
        self.studio.seq = report['seq']
        assert (await COMMANDS['screen'](self.app, self.studio, report))['ok']
        return self.screen.observation(self.studio).wire()

    def packet(self, look, report=None):
        report = report or self.screen.observation(self.studio).wire()
        # Independently encoded synthetic tab pixels (different from saved source).
        width, height = 1440, 1000
        pixels = np.full((height, width, 3), 31, np.uint8)
        pixels[100:300, 600:1000] = (11, 103, 219)
        pixels[700:900, 80:300] = (222, 41, 17)
        ok, encoded = cv2.imencode('.jpg', pixels)
        assert ok
        raw = encoded.tobytes()
        sampled = time.monotonic()*1000
        basis = dict(schema='installed-studio-tab-v1', captureSessionId='external-returned-track',
            surfaceOrigin=ORIGIN, pageHandle=self.ref.page_epoch, structuralReportSeq=report['seq'],
            viewport=copy.deepcopy(report['viewport']), sampledBeforeMonoMs=sampled-1,
            sampledAfterMonoMs=sampled+1, mediaTimeSeconds=12.0,
            rootPaintInclusion='unknown', rootPaintReason='tab-track-has-no-atomic-root-paint-manifest')
        packet = {key:copy.deepcopy(look[key]) for key in ('id','lookId','requestId','appRunId','client','originSnapshotId')}
        packet.update(type='screen-image', schema='workspace-capture-v1', status='captured',
            capturedObservationId=look['lookId'], layoutRevisionBefore=look['layoutRevision'],
            layoutRevisionAfter=look['layoutRevision'], originalSize=[width,height], inputSize=[width,height],
            workspaceToImage=[1,0,0,1,0,0], captureTimeMonoMs=sampled, uncertaintyMs=1,
            captureMethod='authorized-tab-track', surfaceProof='same-origin-page-handle', fidelity='actual-tab-capture',
            coverage='complete', redactedRects=[], missing=[], sha256=hashlib.sha256(raw).hexdigest(),
            jpeg=base64.b64encode(raw).decode(), acquisitionBasis=basis)
        return packet, raw

    async def finish(self):
        for run in self.runs:
            if not run.task.done(): run.task.cancel()
            await asyncio.gather(run.task, return_exceptions=True)
            run.images.clear()
        for task in self.tasks:
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for request in self.requests: request.seal()
        self.screen.gone(self.studio)
        gc.collect()

    def close(self):
        STUDIOS.pop(id(self.app), None)
        self.app.hub._analysis.shutdown(wait=True)
        self.app.hub._background.shutdown(wait=True)


def scenario(tmp_path, work, **options):
    installed = Installed(tmp_path, **options)
    async def go():
        try:
            await installed.initialize()
            await work(installed)
        finally:
            await installed.finish()
    try: asyncio.run(go())
    finally: installed.close()


def failure(look, code='share-required'):
    return {**{key:copy.deepcopy(look[key]) for key in ('id','lookId','requestId','appRunId','client','originSnapshotId')},
            'type':'screen-image', 'schema':'workspace-capture-v1', 'status':'unavailable', 'code':code}


async def wake_continuation():
    # Event-loop delivery only, not time manipulation or a delayed Ticket guard.
    for _ in range(8): await asyncio.sleep(0)


def test_installed_capture_request_is_versioned_and_browser_timeout_is_relative(tmp_path):
    async def work(i):
        _request, _pin, _ctx, _run, look = await i.start()
        assert look.get('schema') == 'workspace-capture-v1', 'MISSING_FEATURE: installed canonical look envelope'
        assert 0 < look['timeoutMs'] <= 30000
        assert look['maxBytes'] <= 6*1024*1024 and look['maxPixels'] <= 4_000_000 and look['maxInputEdge'] <= 1920
    scenario(tmp_path, work)


@pytest.mark.parametrize('changed_layout', [False, True])
def test_original_correlated_unavailable_settles_immediately_even_after_layout_change(tmp_path, changed_layout):
    async def work(i):
        request, pinned, ctx, run, look = await i.start()
        if changed_layout:
            i.studio.items[i.window]['rect'][0] += 19
            await i.studio.send_report()
            assert i.screen.observation(i.studio).wire()['layoutRevision'] != pinned.observation.wire()['layoutRevision']
        response = await COMMANDS['screen-image'](i.app, i.studio, failure(look, 'layout-changed' if changed_layout else 'share-required'))
        await wake_continuation()
        assert response['ok'] is True, 'MISSING_FEATURE: valid refusal is accepted data, not malformed JPEG or success-layout rejection'
        assert run.task.done(), 'MISSING_FEATURE: original continuation must retire immediately, not wait for 5s timeout'
        assert run.ok is False and not run.images
        assert run.occurrence.request_id == request.admission.request_id and ctx.request_snapshot is pinned
        assert request.admission.used == 0 and not ctx.inspection_slots
        assert captures(i.app.hub).memory.reserved['analysis'] == i.analysis_baseline
    scenario(tmp_path, work)


def test_foreign_refusal_cannot_settle_original_pending_capture(tmp_path):
    async def work(i):
        _request, _pin, _ctx, run, look = await i.start()
        foreign = failure(look)
        foreign['requestId'] = 'unrelated-request'
        reply = await COMMANDS['screen-image'](i.app, i.studio, foreign)
        assert reply['ok'] is False and not run.task.done()
        assert captures(i.app.hub).memory.reserved['analysis'] > i.analysis_baseline
        # Teardown cancels original; a foreign reply cannot release its lease.
    scenario(tmp_path, work)


def test_configured_server_admits_original_origin_tab_jpeg_and_retains_unknown_paint(tmp_path):
    async def work(i):
        request, pinned, ctx, run, look = await i.start()
        paired = await i.paired_report()
        packet, raw = i.packet(look, paired)
        expected_basis = copy.deepcopy(packet['acquisitionBasis'])
        reply = await COMMANDS['screen-image'](i.app, i.studio, packet)
        assert reply['ok'] is True, ('MISSING_FEATURE: local installed server policy must authorize exact originating tab adapter', reply)
        await asyncio.wait_for(run.task, HANG_S)
        assert run.ok is True and len(run.images) == 1
        image = run.images[0]
        assert image.facts['channel'] == 'workspace' and image.facts['fidelity'] == 'actual-tab-capture'
        assert image.facts['requestId'] == request.admission.request_id and image.facts['originSnapshotId'] == pinned.snapshot_id
        assert image.facts['acquisitionBasis'] == expected_basis
        packet['acquisitionBasis']['viewport']['dpr'] = 999
        assert image.facts['acquisitionBasis'] == expected_basis, 'actual admitted facts must be detached from raw adapter JSON'
        decoded = cv2.imdecode(np.frombuffer(image.jpeg, np.uint8), cv2.IMREAD_COLOR)
        actual = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        assert decoded.shape == actual.shape
        error = np.abs(decoded.astype(np.int16) - actual.astype(np.int16))
        assert float(error.mean()) < 2 and float(np.percentile(error, 95)) < 8
        assert image.jpeg != i.source_jpeg and image.facts['sha256'] == hashlib.sha256(raw).hexdigest()
        assert len(ctx.inspection_slots) == 1 and request.admission.used == 0
        reused = await i.screen.look(snapshot=pinned, client=i.studio, request_id=request.admission.request_id,
                                      targets=(), memory=captures(i.app.hub).memory)
        assert reused is image
    scenario(tmp_path, work)


@pytest.mark.parametrize('configured,origin,alter', [(False,ORIGIN,None),(True,None,None),(True,ORIGIN,'origin'),(True,ORIGIN,'handle')])
def test_no_raw_reply_can_grant_default_absent_origin_or_wrong_surface_authority(tmp_path, configured, origin, alter):
    async def work(i):
        _request, _pin, _ctx, run, look = await i.start()
        packet, _raw = i.packet(look)
        if alter == 'origin': packet['acquisitionBasis']['surfaceOrigin'] = 'http://localhost:9999'
        if alter == 'handle': packet['acquisitionBasis']['pageHandle'] = 'other-page'
        reply = await COMMANDS['screen-image'](i.app, i.studio, packet)
        assert reply['ok'] is False and not run.images
        assert reply['code'] == 'CAPTURE_WRONG_SURFACE'
    scenario(tmp_path, work, configured=configured, origin=origin)


def test_real_typed_default_camera_never_requests_workspace_pixels(tmp_path, monkeypatch):
    i = Installed(tmp_path)
    sends, originals = [], []
    async def provider(images, prompt, **kwargs):
        ticket = current()
        attempt = ticket.admission.attempts[-1]
        sends.append(SimpleNamespace(ticket=ticket, admission=ticket.admission, attempt=attempt, images=tuple(images), prompt=prompt,
                                     held=ticket.held(originals[0].id)))
        return SimpleNamespace(data={'answer':'I interpreted the supplied camera view.', 'targets':[], 'actions':[],
            'intent':'question', 'needs_look':False, 'observation_scope':'specimen-camera', 'input_refs':['camera']},
            model='explicitly-canned-camera-provider', latency_ms=0.0, error=None)
    monkeypatch.setattr(i.app.agent, 'vision', lambda _option:SimpleNamespace(ask=provider))
    async def go():
        try:
            await i.initialize()
            i.app.hub.settings.fast = 'off'
            original = await intake_camera(SimpleNamespace(app=i.app))
            originals.append(original)
            report = await i.paired_report()
            result = await COMMANDS['ask'](i.app, i.studio, {'type':'ask', 'text':'Describe this view.', 'frameId':original.id, 'screen':report})
            assert sends and any(sent.images for sent in sends)
            first_pixels = next(sent for sent in sends if sent.images)
            assert first_pixels.held is original, 'actual default look must own the original producing camera Capture'
            shown = cv2.imdecode(np.frombuffer(first_pixels.images[0], np.uint8), cv2.IMREAD_COLOR)
            assert shown is not None
            saved = cv2.imdecode(np.frombuffer(i.source_jpeg, np.uint8), cv2.IMREAD_COLOR)
            raw = cv2.imdecode(np.frombuffer(original.jpeg, np.uint8), cv2.IMREAD_COLOR)
            assert raw is not None and saved is not None and raw.shape[:2] == (1200, 1600)
            # Independent coordinates/colours from spatial_camera_jpeg's three
            # unequal external landmarks, not the producer's encoding helper.
            fov = original.fov
            assert fov is not None
            radius = fov.radius * 1.02
            x0, y0 = max(0, int(fov.cx - radius)), max(0, int(fov.cy - radius))
            x1, y1 = min(1600, int(fov.cx + radius)), min(1200, int(fov.cy + radius))
            assert x0 > 0 and y0 > 0 and x1 - x0 < 1600 and y1 - y0 < 1200
            scale = min(1.0, 1024 / max(x1 - x0, y1 - y0))
            assert shown.shape[:2] == (round((y1 - y0) * scale), round((x1 - x0) * scale))
            for x, y, colour in [(615, 385, (80, 220, 110)), (1165, 485, (220, 110, 190)),
                                 (850, 925, (140, 150, 245))]:
                assert float(np.abs(np.median(raw[y-2:y+3, x-2:x+3], axis=(0, 1)) - colour).max()) < 8
                px, py = round((x - x0) * scale), round((y - y0) * scale)
                patch = shown[py-2:py+3, px-2:px+3]
                assert patch.shape[:2] == (5, 5)
                assert float(np.abs(np.median(patch, axis=(0, 1)) - colour).max()) < 8
                assert float(np.abs(np.median(saved, axis=(0, 1)) - colour).max()) > 40
            assert first_pixels.images[0] != i.source_jpeg and original.jpeg != i.source_jpeg
            assert result['calls'] == len(sends) <= 3
            assert all(sent.ticket is sends[0].ticket and sent.admission is sends[0].admission for sent in sends)
            assert [attempt.attempt_id for attempt in sends[0].admission.attempts] == [sent.attempt.attempt_id for sent in sends]
            assert all(len(sent.images) <= 2 and sent.attempt.status == 'running' for sent in sends)
            settled_status = {attempt.attempt_id: attempt.status for attempt in sends[0].admission.attempts}
            assert all(settled_status[sent.attempt.attempt_id] == 'done' for sent in sends)
            assert not result['actions'] and not i.screen._looks
            queued = []
            while not i.studio.control.empty():
                raw = i.studio.control.get_nowait()
                queued.append(json.loads(raw) if isinstance(raw,str) else raw)
            assert not any(isinstance(row,dict) and row.get('type')=='screen-look' for row in queued)
            assert result['answer'] == 'I interpreted the supplied camera view.'
        finally: await i.finish()
    try: asyncio.run(go())
    finally: i.close()


def test_actual_app_query_interprets_workspace_manifest_with_original_pin_and_two_slot_quota(tmp_path, monkeypatch):
    i = Installed(tmp_path)
    sends, starts = [], []
    actual_start = i.app.brain.registry.start
    def observe_start(name, args, ctx, run=None):
        actual = actual_start(name,args,ctx,run=run)
        starts.append((actual,ctx,current()))
        return actual
    monkeypatch.setattr(i.app.brain.registry,'start',observe_start)  # passive delegate, no executor replacement
    async def provider(images, prompt, **kwargs):
        ticket=current(); attempt=ticket.admission.attempts[-1]
        sends.append(SimpleNamespace(ticket=ticket,admission=ticket.admission,attempt=attempt,images=tuple(images),prompt=prompt))
        data = ({'answer':'I will inspect the tab image.', 'intent':'request', 'needs_look':False,
                 'actions':[{'tool':'look_at_screen','args':{}},{'tool':'resources','args':{'id':i.entry.id,'inspect':True}}],
                 'observation_scope':'none','input_refs':[]} if attempt.phase=='fast' else
                {'answer':MODEL_TEXT,'actions':[{'tool':'resources','args':{'id':i.entry.id,'inspect':True}}],
                 'observation_scope':'workspace-pixels','input_refs':[1]})
        return SimpleNamespace(data=data,model='explicitly-canned-tab-provider',latency_ms=0.0,error=None)
    monkeypatch.setattr(i.app.agent,'vision',lambda _option:SimpleNamespace(ask=provider))
    async def go():
        try:
            await i.initialize()
            original = await intake_camera(SimpleNamespace(app=i.app))
            report = await i.paired_report()
            starts_baseline = len(starts)  # retain initialization history separately from this request
            assert any(run.tool == 'present' and run.ok for run, _ctx, _owner in starts[:starts_baseline])
            asking=asyncio.create_task(COMMANDS['ask'](i.app,i.studio,{'type':'ask','text':'Inspect the supplied tab and retained image.', 'frameId':original.id,'screen':report}))
            i.tasks.append(asking)
            look=await i.next_look()
            assert sends[0].attempt.phase=='fast' and not sends[0].images
            request_starts = starts[starts_baseline:]
            assert request_starts[0][0].tool=='look_at_screen'
            ctx=request_starts[0][1]; snapshot=ctx.request_snapshot
            assert snapshot.observation.wire()==report and snapshot.snapshot_id==look['originSnapshotId']
            paired=await i.paired_report(); packet,_raw=i.packet(look,paired)
            reply=await COMMANDS['screen-image'](i.app,i.studio,packet)
            assert reply['ok'] is True, ('MISSING_FEATURE: actual App query needs optional installed capture admission',reply)
            result=await asyncio.wait_for(asking,HANG_S)
            request_starts = starts[starts_baseline:]  # include every later acquisition/refusal of this request
            budget=sends[0].admission
            assert result['calls']==len(sends)==budget.used<=3
            assert all(sent.ticket is sends[0].ticket and sent.admission is budget for sent in sends)
            assert all(sent.attempt.status=='running' and len(sent.images)<=2 for sent in sends)
            assert [attempt.attempt_id for attempt in budget.attempts] == [sent.attempt.attempt_id for sent in sends]
            settled_status = {attempt.attempt_id: attempt.status for attempt in budget.attempts}
            assert all(settled_status[sent.attempt.attempt_id] == 'done' for sent in sends)
            assert all(owner is sends[0].ticket and context.admission is budget and context.request_snapshot is snapshot
                       for _run,context,owner in request_starts)
            acquired=[image for run,_ctx,_owner in request_starts if run.ok for image in run.images]
            assert len(acquired)==2 and len(ctx.inspection_slots)==2
            assert [image.facts['channel'] for image in acquired]==['workspace','retained-source']
            query=next(sent for sent in sends if sent.attempt.phase=='query')
            assert query.images==tuple(image.jpeg for image in acquired)
            for n,image in enumerate(acquired,1):
                assert json.dumps({'image':n,**image.facts},default=str) in query.prompt
            assert acquired[0].facts['acquisitionBasis']['rootPaintInclusion']=='unknown'
            assert acquired[0].facts['originSnapshotId']==snapshot.snapshot_id
            assert any(not run.ok and 'two' in run.summary for run,_ctx,_owner in request_starts), 'existing two-image quota must refuse third acquisition'
            assert result['answer']==MODEL_TEXT
        finally:
            for run,_ctx,_owner in starts:
                if run not in i.runs:i.runs.append(run)
            await i.finish()
    try: asyncio.run(go())
    finally: i.close()
