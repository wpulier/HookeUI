"""Actual retained stores/manual admission/Registry/Screen; synthetic renderer only."""
import asyncio
import copy
import hashlib
import json
import pytest

from orchestrator.commands import COMMANDS
from orchestrator.research.findings import retain, reference
from tests.test_workspace_actions_app import built, saved_picture, research_fixture, open_window, WORKSPACE
from tests.waiting import HANG_S


def fixture(tmp_path):
    app, studio = built(tmp_path)
    entry, pixels = saved_picture(app)
    manager, session = research_fixture(app)
    document = app.hub.resources.document(entry.id, entry.day)
    digest = hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    result = {'title': 'Original study', 'summary': 'One original frame', 'captures': [{
        'title': 'Original frame', 'kind': 'tracks', 'state': 'ready', 'summary': 'Retained only', 'at': None,
        'source': {'day': entry.day, 'id': entry.id, 'version': entry.version, 'contentRevision': digest},
        'frames': [{'picture': 0}]}]}
    record = retain(session.finding_revisions, result, [], None)
    session.result = {**result, 'findingRevision': reference(record)}
    manager._save(session)
    target = {'type': 'research-open-capture', 'day': session.day, 'rid': session.rid,
        'findingVersion': record['version'], 'findingDigest': record['digest'], 'sequence': 0, 'picture': 0,
        'requestId': 'synthetic/capture-open', 'actionId': 'synthetic/capture-action', 'client': dict(studio.client_ref)}
    return app, studio, entry, session, target, pixels


def test_capture_open_exact_original_and_admitted_replay_after_source_change(tmp_path, monkeypatch):
    app, studio, entry, session, target, pixels = fixture(tmp_path)
    async def go():
        result = await COMMANDS['research-open-capture'](app, studio, copy.deepcopy(target))
        assert result['ok'] and not result['pending'], result
        windows = app.services.presentations.list(WORKSPACE)
        assert len(windows) == 1
        resource = windows[0].source.to_wire()['identity']['resource']
        assert resource == {'day': entry.day, 'id': entry.id, 'version': entry.version, 'picture': 0}
        assert app.hub.resources.picture(entry.id, 0, entry.day)[0] == pixels
        # Lost acknowledgement retries project the admitted original, even when
        # later scientific/evidence metadata no longer resolves the source.
        monkeypatch.setattr(app.hub.resources, 'document', lambda *a, **k: None)
        replay = await COMMANDS['research-open-capture'](app, studio, copy.deepcopy(target))
        assert replay == result and len(app.services.presentations.list(WORKSPACE)) == 1
        changed = await COMMANDS['research-open-capture'](app, studio, {**target, 'picture': 1})
        assert not changed['ok'] and changed['code'] == 'MANUAL_REPLAY_CHANGED'
    asyncio.run(go())


def test_capture_open_refuses_wrong_revision_and_wrong_browser_before_effect(tmp_path):
    app, studio, _, _, target, _ = fixture(tmp_path)
    async def go():
        for packet in ({**target, 'findingDigest': 'wrong'},
                       {**target, 'client': {**target['client'], 'workspaceId': 'another-browser'}}):
            result = await COMMANDS['research-open-capture'](app, studio, packet)
            assert not result['ok'], result
        assert not app.services.presentations.list(WORKSPACE)
    asyncio.run(go())


def test_capture_lookup_refusal_is_retained_without_registry_effect(tmp_path, monkeypatch):
    from orchestrator.research import captures
    from orchestrator.research.policy import ResearchRefused
    from tests.test_native_manual_ingress import Trace
    app, studio, _, _, target, _ = fixture(tmp_path)
    trace = Trace(app, monkeypatch)
    calls = []
    def missing(*args):
        calls.append(args[-1])
        raise ResearchRefused('the original captured frame is no longer retained', next='open another saved capture')
    monkeypatch.setattr(captures, 'resolve', missing)
    async def go():
        result = await COMMANDS['research-open-capture'](app, studio, copy.deepcopy(target))
        assert not result['ok'] and result['error'] == 'the original captured frame is no longer retained'
        replay = await COMMANDS['research-open-capture'](app, studio, copy.deepcopy(target))
        assert replay == result and len(calls) == 1
        assert not trace.calls and not app.manual_commands.pending
        assert not app.services.presentations.list(WORKSPACE)
    asyncio.run(go())


@pytest.mark.parametrize('width', [390, 961, 1440])
@pytest.mark.parametrize('change', [None, 'move', 'resize', 'close', 'close-reopen', 'source', 'invisible', 'area', 'no-room'])
def test_capture_open_groups_only_with_exact_requesting_report(tmp_path, monkeypatch, change, width):
    app, studio, _, session, target, _ = fixture(tmp_path)
    # Extend only the external renderer seam to consume actual saved JSON.
    # Its text rectangle is synthetic, just as AppStudio's image layout is.
    import cv2
    import numpy as np
    from orchestrator.presentation_delivery import written_source
    from orchestrator.presentations import ResourceRef
    payload = studio.retained_payload
    def report_payload(resource):
        if resource['id'] == session.rid and resource.get('picture') is None:
            _, text = written_source(session, ResourceRef.from_wire(resource))
            return text.encode(), 'application/vnd.micromind.findings+json'
        return payload(resource)
    monkeypatch.setattr(studio, 'retained_payload', report_payload)
    decode = cv2.imdecode
    def report_rectangle(raw, flags):
        if raw.tobytes().startswith(b'{'):
            assert json.loads(raw.tobytes())['findingRevision']['digest'] == target['findingDigest']
            return np.zeros((100, 100, 3), dtype=np.uint8)
        return decode(raw, flags)
    monkeypatch.setattr(cv2, 'imdecode', report_rectangle)
    async def go():
        report_entry = app.hub.resources.get(session.rid, session.day)
        parent, _, _ = await open_window(app, report_entry, picture=None)
        studio.presentation_area = [0, 0, width, 1000]
        await studio.send_report()
        later = retain(session.finding_revisions, {**session.result, 'summary': 'Later findings'}, [], None)
        wrong_parent = await COMMANDS['research-open-capture'](app, studio,
            {**target, 'findingVersion': later['version'], 'findingDigest': later['digest'], 'parentInstance': parent})
        assert not wrong_parent['ok'] and 'another findings revision' in wrong_parent['error']
        assert len(app.services.presentations.list(WORKSPACE)) == 1
        parent_rect = list(studio.items[parent]['rect'])
        from orchestrator.brain.toolbox.present import _opening_basis
        wide = _opening_basis({'kind':'beside','reference':parent},
            {parent:app.services.presentations.get(WORKSPACE,parent)},
            {**app.hub.screen.completion_report(studio), 'viewport': {'presentationArea':[0,0,1440,1000]}},(1280,100))
        assert wide[0][2:] == [360,224]  # minimum image area180 plus44px header
        message_offset = len(studio.messages)
        prepared_captures = []
        if change == 'no-room':
            studio.presentation_area = [0,0,width,100]
            await studio.send_report()
        elif change:
            prepare = app.services.prepared_sources.prepare
            async def changed_while_preparing(*args, **kwargs):
                lease = await prepare(*args, **kwargs)
                prepared_captures.append(lease.token)
                if change == 'invisible':
                    studio.items[parent]['visible'] = False
                    await studio.send_report()
                elif change == 'area':
                    studio.presentation_area = [0,0,1300,900]
                    await studio.send_report()
                else:
                    packet = {'type':'present','do':'move','what':{'instanceId':parent},
                        'requestId':'intervening-control','actionId':'intervening-control-action','client':dict(studio.client_ref)}
                    if change == 'move': packet['position'] = {'x':200,'y':100}
                    elif change == 'resize': packet.update(do='resize', size={'width':260,'height':240})
                    elif change in ('close','close-reopen'): packet['do'] = 'close'
                    else:
                        replacement, _ = saved_picture(app,120)
                        packet.update(do='open',source={'day':replacement.day,'id':replacement.id,'version':replacement.version,'picture':0})
                        monkeypatch.setattr(app.services.prepared_sources,'prepare',prepare)
                    changed = await COMMANDS['present'](app,studio,packet)
                    assert changed['ok'], changed
                    if change == 'close-reopen':
                        monkeypatch.setattr(app.services.prepared_sources,'prepare',prepare)
                        reopened = await COMMANDS['present'](app,studio, {**packet,
                            'do':'open','requestId':'reopened-report','actionId':'reopened-report-action',
                            'source':{'day':report_entry.day,'id':report_entry.id,'version':report_entry.version,'picture':None}})
                        assert reopened['ok'], reopened
                return lease
            monkeypatch.setattr(app.services.prepared_sources,'prepare',changed_while_preparing)
        result = await COMMANDS['research-open-capture'](app, studio, {**target, 'parentInstance': parent})
        if change and not (width <= 960 and change == 'no-room'):
            assert not result['ok'] and not result['pending'], result
            assert len(app.services.presentations.list(WORKSPACE)) == 1
            assert all(token not in app.services.prepared_sources._pins for token in prepared_captures)
            return
        assert result['ok'] and not result['pending'], result
        assert len(result['steps']) == 2 and all(step['ok'] is True for step in result['steps'])
        windows = app.services.presentations.list(WORKSPACE)
        assert len(windows) == 2
        groups = {window.view.group for window in windows}
        assert len(groups) == 1 and None not in groups
        assert studio.items[parent]['rect'] == parent_rect
        issued = [m for m in studio.messages[message_offset:] if m.get('schema') == 'presentation-command-v2']
        assert [m['op'] for m in issued] == ['open','group']
        child = issued[0]['instances'][0]
        geometry = child['geometryIntent']
        if width <= 960:
            assert geometry['relation'] is None  # responsive one-result view, never claim beside
        else:
            assert geometry['relation']['reference'] == parent
            assert geometry['position'][0] == parent_rect[0]+parent_rect[2]+12
            assert studio.items[child['id']]['rect'] == [*geometry['position'],*geometry['size']]
    asyncio.run(go())


def test_capture_open_rechecks_changed_source_after_preparation_and_releases_lease(tmp_path, monkeypatch):
    app, studio, _, _, target, _ = fixture(tmp_path)
    store = app.services.prepared_sources
    prepare = store.prepare
    original_document = app.hub.resources.document
    changed = False
    def document(*args, **kwargs):
        return None if changed else original_document(*args, **kwargs)
    monkeypatch.setattr(app.hub.resources, 'document', document)
    async def changed_after_prepare(*args, **kwargs):
        nonlocal changed
        lease = await prepare(*args, **kwargs)
        changed = True
        return lease
    monkeypatch.setattr(store, 'prepare', changed_after_prepare)
    async def go():
        result = await COMMANDS['research-open-capture'](app, studio, target)
        assert not result['ok'] and not result['pending'], result
        assert 'unavailable' in result['run']['summary']
        assert not app.services.presentations.list(WORKSPACE)
        assert not store._pins  # failed admission releases prepared bytes/grants
    asyncio.run(go())


@pytest.mark.parametrize('kind,historical', [('population',False),('clip',False),('tracks',False),('population',True),('tracks',True)])
def test_original_evidence_capture_opens_through_real_producer_store(tmp_path, kind, historical):
    from types import SimpleNamespace
    from tests.test_evidence import population, view, saved
    from orchestrator.evidence import Evidence, evidence_of
    from orchestrator.motiontracks import Tracks, TracksStore, tracks_of
    from orchestrator.day.store import DayStore
    from orchestrator.research.acquisition import save_clip
    from orchestrator.research.missions import plan, Slot
    from tests.test_missions import PIN
    from research_worker.captures import retain as retain_captures
    app, studio = built(tmp_path)
    manager, session = research_fixture(app)
    capture = view()
    pixels = capture.jpeg
    source_day = '2026-10-07' if historical else app.day.day
    old_day = DayStore(root=app.day.root,day=source_day) if historical else None
    if kind == 'population':
        store = evidence_of(app.hub)
        if historical: store = Evidence(SimpleNamespace(),store.root,day=old_day)
        item = population(store, capture)
        item.day = source_day
        store.add(item)
        store.schedule_save(item)
        assert saved(item).status == 'saved'
        eid = item.id
    elif kind == 'tracks':
        store = tracks_of(app.hub)
        if historical: store = TracksStore(SimpleNamespace(),store.root,day=old_day)
        item = Tracks(store.new_id(), 'measured', 'motion', 'spots', {}, capture.id,
            {'width':160,'height':120,'frame':capture.id,'seq':88,'boot':'original'}, None,
            {'from':1000.,'to':1005.,'seconds':5.,'frames':2},
            {'counted':0,'moving':0,'unit':'px/s','threshold':8.}, {}, [], [],
            day=source_day, status='unsaved', picture=pixels)
        store.add(item)
        if historical:
            store._save(item)
            assert item.status == 'saved'
        eid = item.id
    else:
        frames = [(SimpleNamespace(jpeg=pixels), SimpleNamespace(id=70+n,seq=800+n,
            boot='original',wall_at=1000+n*5,width=160,height=120,scale=None)) for n in range(2)]
        mission = plan(session.rid,'capture',30,270,PIN,1000,('capture',),acquisition={'seconds':5})
        record = save_clip(manager.root/'acquisitions', frames, mission=mission,
            slot=Slot(0,1000,'capture'),day=app.day.day)
        eid = record['id']
    document = app.hub.resources.document(eid, source_day)
    assert document is not None
    captures = retain_captures({(eid,'original-read'):document})
    assert len(captures) == 1
    result = {'title':'Retained evidence','summary':'Read original evidence','captures':captures}
    record = retain(session.finding_revisions,result,[],None)
    session.result = {**result,'findingRevision':reference(record)}
    manager._save(session)
    target = {'type':'research-open-capture','day':session.day,'rid':session.rid,
        'findingVersion':record['version'],'findingDigest':record['digest'],'sequence':0,'picture':0,
        'requestId':f'synthetic/{kind}','actionId':f'synthetic/{kind}-open','client':dict(studio.client_ref)}
    async def go():
        result = await COMMANDS['research-open-capture'](app,studio,target)
        assert result['ok'] and not result['pending'], result
        windows = app.services.presentations.list(WORKSPACE)
        assert len(windows) == 1
        source = windows[0].source.to_wire()
        assert source['identity']['resource']['id'] == eid
        assert source['media'] == ('labelled' if kind == 'tracks' else 'capture')
        assert app.hub.resources.picture(eid,0,source_day)[0] == pixels
        if historical:
            live = evidence_of(app.hub) if kind == 'population' else tracks_of(app.hub)
            assert live.get(eid) is None  # read-only history never enters today's scene
    asyncio.run(go())


def test_population_save_completion_during_read_preserves_original_descriptor(tmp_path, monkeypatch):
    from orchestrator.evidence import evidence_of
    from orchestrator.presentations import ResourceRef
    from orchestrator.ticket import issue, using
    from tests.test_evidence import population, view, saved
    from tests.test_presentation_delivery import context
    app, _ = built(tmp_path)
    store = evidence_of(app.hub)
    item = population(store, view())
    item.day = app.day.day
    store.add(item)
    original_picture = app.hub.resources.picture
    changed = False
    def finish_save(*args, **kwargs):
        nonlocal changed
        data = original_picture(*args, **kwargs)
        if not changed:
            changed = True
            store.schedule_save(item)
            assert saved(item).status == 'saved'
        return data
    monkeypatch.setattr(app.hub.resources, 'picture', finish_save)
    async def go():
        ticket = issue(app.hub,'turn',asked=app.day.here())
        leases = []
        try:
            with using(ticket):
                first = await app.services.prepared_sources.prepare(ResourceRef(item.day,item.id,item.revision,0),context(ticket))
                leases.append(first)
                assert item.status == 'saved' and first.source.availability == 'temporary'
                again = await app.services.prepared_sources.prepare(first.source,context(ticket))
                leases.append(again)
                assert again.source == first.source
                assert app.services.prepared_sources.get(again.token,context(ticket)) == original_picture(item.id,0,item.day)[0]
        finally:
            for lease in leases: lease.release()
            ticket.close()
    asyncio.run(go())


@pytest.mark.parametrize('retire', [False, True])
def test_capture_pins_send_before_retained_lookup_and_joins_pending_replay(tmp_path, monkeypatch, retire):
    import threading
    import time
    from orchestrator.research import captures
    from tests.test_native_manual_ingress import Trace, _until
    app, studio, _, _, target, _ = fixture(tmp_path)
    trace = Trace(app, monkeypatch)
    original = captures.resolve
    entered, release = threading.Event(), threading.Event()
    calls = []
    def slow_resolve(*args):
        calls.append(args[-1])
        if len(calls) == 1:
            entered.set()
            assert release.wait(HANG_S), 'test source lookup barrier was not released'
        return original(*args)
    monkeypatch.setattr(captures, 'resolve', slow_resolve)
    async def go():
        await studio.send_report()
        sent = app.hub.screen.observation(studio).wire()
        sent.update(seq=studio.seq+1, capturedMonoMs=time.monotonic()*1000)
        packet = {**target, 'screen':sent}
        first = asyncio.create_task(COMMANDS['research-open-capture'](app,studio,copy.deepcopy(packet)))
        try:
            await _until(entered.is_set, 'retained lookup did not start')
            studio.seq = sent['seq']
            await studio.send_report()  # ordinary newer browser report during disk lookup
            if retire:
                app.manual_commands.gone(studio)
                joined = None
            else:
                joined = asyncio.create_task(COMMANDS['research-open-capture'](app,studio,copy.deepcopy(packet)))
                await asyncio.sleep(0)
        finally:
            release.set()
        result = await first
        if retire:
            assert not result['ok'] and ('retired' in result['error'] or 'connection' in result['error'])
            assert not trace.calls and not app.services.presentations.list(WORKSPACE)
        else:
            assert result['ok'], result
            assert await joined == result
            assert len(trace.issued) == len(trace.calls) == 1
            call = trace.original(packet)
            assert call['pin_wire']['seq'] == sent['seq']
            assert call['pin_wire']['capturedMonoMs'] == sent['capturedMonoMs']
            assert len(calls) == 2  # one initial lookup plus original-source validation
            changed = await COMMANDS['research-open-capture'](app,studio,{**packet,'picture':99})
            assert not changed['ok'] and changed['code'] == 'MANUAL_REPLAY_CHANGED'
        assert not app.manual_commands.pending
    asyncio.run(go())
