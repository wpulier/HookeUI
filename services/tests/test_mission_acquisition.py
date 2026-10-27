"""Literal acquisition plans: fixed slots, retained parameters, no model-clock drift."""
import asyncio

import pytest

from orchestrator.research.missions import Mission, plan
from tests.test_missions import Clock, PIN, scheduler
from tests.waiting import HANG_S, until_async


def test_ten_five_second_clips_keep_the_clock_and_only_analyze_after_acquisition():
    async def go():
        clock = Clock()
        checkpoints, endings = [], []

        async def capture(m, slot):
            assert m.acquisition == {"seconds": 5.0}
            await clock.sleep(5)
            return {"ok": True, "id": f"clip-{slot.n}", "capture": {"at": clock() - 5}}

        async def checkpoint(m):
            checkpoints.append(clock())
            await clock.sleep(99)

        async def finish(m):
            endings.append(clock())

        s, _, ran = scheduler(clock, capture, checkpoint=checkpoint, finish=finish)
        m = plan("r4", "capture", 30, 270, PIN, clock(), ("capture",), acquisition={"seconds": 5})
        await s.start(m)
        assert [at for _, at in ran] == [1000 + n * 30 for n in range(10)]
        assert endings == [1275]
        assert checkpoints == []
        assert m.status == "done" and len(m.ledger) == 10
        again = Mission.from_wire("r4", m.wire())
        assert again.acquisition == {"seconds": 5.0} and again.slots == 10
    asyncio.run(go())


@pytest.mark.parametrize("duration", [0, -1, 30, float('nan'), True, "5"])
def test_invalid_clip_duration_is_refused_before_scheduling(duration):
    with pytest.raises(ValueError):
        plan("r4", "capture", 30, 270, PIN, 1000, ("capture",), acquisition={"seconds": duration})


def test_capture_parameters_are_required_and_not_accepted_for_analysis():
    with pytest.raises(ValueError):
        plan("r4", "capture", 30, 270, PIN, 1000, ("capture",))
    with pytest.raises(ValueError):
        plan("r4", "motion", 60, 270, PIN, 1000, ("motion",), acquisition={"seconds": 5})


def test_original_clip_pixels_and_sources_survive_reopening(tmp_path):
    from types import SimpleNamespace
    from orchestrator.research.acquisition import save_clip, read_clip, picture
    from orchestrator.research.missions import Slot
    from PIL import Image
    import io
    frames = []
    for n in range(3):
        buffer = io.BytesIO()
        Image.new('RGB', (8, 8), (n * 90, 0, 0)).save(buffer, format='JPEG')
        frames.append((SimpleNamespace(jpeg=buffer.getvalue()), SimpleNamespace(
            id=n + 70, seq=n + 800, boot='original-bridge', wall_at=1000 + n * 2.5,
            width=8, height=8, scale={'objective': 40})))
    m = plan('r4', 'capture', 30, 270, PIN, 1000, ('capture',), acquisition={'seconds': 5})
    root = tmp_path / 'acquisitions'
    record = save_clip(root, frames, mission=m, slot=Slot(0, 1000, 'capture'), day='2026-10-10')
    reopened = read_clip(root, '2026-10-10', record['id'])
    assert reopened == record
    assert reopened['frames'][1]['seq'] == 801
    assert reopened['channels']['microphone'] == 'unavailable'
    assert picture(root, '2026-10-10', record['id'], 1) == frames[1][0].jpeg
    assert picture(root, '2026-10-10', record['id'], 1, max_bytes=3) is None
    assert picture(root, '2026-10-11', record['id'], 1) is None
    assert picture(root, '2026-10-10', '../' + record['id'], 1) is None
    assert picture(root, '2026-10-10', record['id'], -1) is None
    assert (root / '2026-10-10' / record['id'] / 'frames.zip').stat().st_mode & 0o777 == 0o600

    # The regular resource reader opens it without a researcher process or live camera.
    from orchestrator.resources import Index
    index = Index(SimpleNamespace(), research=SimpleNamespace(root=tmp_path),
                  day=SimpleNamespace(day='2026-10-10', sid='2026-10-10', date='2026-10-10'))
    entries = index._acquisitions('2026-10-10')
    assert [e.id for e in entries] == [record['id']]
    assert entries[0].values['frames'][1]['seq'] == 801
    assert index.picture(record['id'], 1) == (frames[1][0].jpeg, 'image/jpeg')


def test_failed_clip_write_never_publishes_a_partial_manifest(tmp_path):
    from types import SimpleNamespace
    from orchestrator.research.acquisition import save_clip
    from orchestrator.research.missions import Slot
    m = plan('r4', 'capture', 30, 270, PIN, 1000, ('capture',), acquisition={'seconds': 5})
    with pytest.raises(ValueError, match='original pixels'):
        save_clip(tmp_path, [(SimpleNamespace(jpeg=b''), None)], mission=m,
                  slot=Slot(0, 1000, 'capture'), day='2026-10-10')
    assert not list(tmp_path.rglob('manifest.json'))
    assert not list(tmp_path.rglob('frames.zip'))


def test_camera_acquisition_survives_finished_model_run_and_releases_memory(tmp_path):
    from types import SimpleNamespace
    from orchestrator.research.acquisition import acquire, read_clip, picture
    from orchestrator.research.missions import Slot
    from tests.test_research_fresh_motion import motion_hub
    from tests.test_motion_tracks import moving_slide

    async def go():
        hub = motion_hub()
        baseline = dict(hub.captures.memory.reserved)
        hub.source.get = lambda fid: SimpleNamespace(id=fid, jpeg=hub.ring[fid],
            received_at=hub.infos[fid].wall_at - 90) if fid in hub.ring else None
        m = plan('r4', 'capture', 30, 270, PIN, 1000, ('capture',), acquisition={'seconds': 5})
        # Mission ownership deliberately outlives an individual model completion.
        session = SimpleNamespace(status='done', mission=m)
        def check():
            assert session.mission is m and m.status == 'watching'
        task = asyncio.create_task(acquire(hub, tmp_path, m, Slot(0, 1000, 'capture'), '2026-10-10', check))
        try:
            await asyncio.sleep(0)
            samples = moving_slide(seconds=.1)
            expected = []
            for n in range(21):
                frame = hub.add(samples[0].image, 500.0, -300.0, wall=104 + n * .25)
                expected.append(hub.ring[frame.id])
                await asyncio.sleep(.02)  # external synthetic ingress, not a replaced collector/clock
            reply = await asyncio.wait_for(task, HANG_S)
            assert reply['ok'] and reply['capture']['seconds'] == 5
            record = read_clip(tmp_path, '2026-10-10', reply['id'])
            assert len(record['frames']) == 21
            assert picture(tmp_path, '2026-10-10', reply['id'], 0) == expected[0]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert not hub.captures.held() and hub.captures.memory.reserved == baseline
    asyncio.run(go())


def test_forward_wall_clock_jump_does_not_complete_a_clip(tmp_path):
    from types import SimpleNamespace
    from orchestrator.research.acquisition import acquire
    from orchestrator.research.missions import Slot
    from orchestrator.brain.registry import ToolFailed
    from tests.test_research_fresh_motion import motion_hub
    from tests.test_motion_tracks import moving_slide

    async def go():
        hub = motion_hub()
        clocks = {fid: info.wall_at - 90 for fid, info in hub.infos.items()}
        hub.source.get = lambda fid: SimpleNamespace(jpeg=hub.ring[fid], received_at=clocks[fid]) if fid in hub.ring else None
        m = plan('r4', 'capture', 30, 270, PIN, 1000, ('capture',), acquisition={'seconds': 5})
        task = asyncio.create_task(acquire(hub, tmp_path, m, Slot(0, 1000, 'capture'), '2026-10-10', lambda: None))
        await asyncio.sleep(0)
        sample = moving_slide(seconds=.1)[0]
        f = hub.add(sample.image, 500, -300, wall=110)  # wall jumps, camera intake only advances a fraction
        clocks[f.id] = max(clocks.values()) + .03
        with pytest.raises(ToolFailed, match='clocks changed'):
            await asyncio.wait_for(task, HANG_S)
        assert not list(tmp_path.rglob('manifest.json')) and not hub.captures.held()
    asyncio.run(go())


def test_manifest_is_invisible_until_atomic_publication(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import os
    from orchestrator.research.acquisition import save_clip, read_clip
    from orchestrator.research.missions import Slot
    m = plan('r4', 'capture', 30, 270, PIN, 1000, ('capture',), acquisition={'seconds': 5})
    frames = [(SimpleNamespace(jpeg=b'original-jpeg'), SimpleNamespace(id=n, seq=n, boot='b',
        wall_at=1000 + n * 5, width=8, height=8, scale=None)) for n in range(2)]
    original_replace = os.replace
    def publish(source, destination):
        assert source.name == 'manifest.pending'
        assert read_clip(tmp_path, '2026-10-10', source.parent.name) is None
        assert not list(tmp_path.rglob('manifest.json'))
        original_replace(source, destination)
    monkeypatch.setattr(os, 'replace', publish)
    record = save_clip(tmp_path, frames, mission=m, slot=Slot(0, 1000, 'capture'), day='2026-10-10')
    assert read_clip(tmp_path, '2026-10-10', record['id']) == record


from tests.test_population import world  # noqa: F401 — synthetic camera and cell-finder boundary


def test_saved_clip_frame_can_be_segmented_after_restart_without_camera(world, tmp_path):
    import json
    from orchestrator.research.acquisition import save_clip, load_capture
    from orchestrator.research.missions import Slot
    from orchestrator.research.hands import Hands
    from orchestrator.evidence import evidence_of
    from tests.test_mission_manager_lifecycle import setup_manager, kept_researcher, cleanup, DAY

    async def go():
        manager, _, worker = setup_manager(world, tmp_path)
        session = kept_researcher(manager)
        info = world.latest_info
        original_bytes = world.source.get(info.id).jpeg
        m = plan(session.rid, 'capture', 30, 270, PIN, 1000, ('capture',), acquisition={'seconds': 5})
        record = save_clip(manager.root / 'acquisitions', [(world.source.get(info.id), info)] * 2,
                           mission=m, slot=Slot(0, 1000, 'capture'), day=DAY, session=session.sitting)
        assert record['session'] == session.sitting and record['day'] == DAY  # its session, its date folder
        path = manager.root / 'acquisitions' / DAY / record['id'] / 'manifest.json'
        record['run'] = 'earlier-app-run'
        path.write_text(json.dumps(record))
        capture, n, reservation = load_capture(world, manager.root / 'acquisitions', DAY, record['id'], 0)
        assert n == 0 and capture.id < 0 and capture.epoch is None
        assert capture.jpeg == original_bytes
        reservation.release()
        world.source.is_fresh = lambda: False
        try:
            reply = await Hands(world).run(session, 'segment', input=record['id'], frame=0)
            assert reply['ok'], reply
            population = evidence_of(world).get(reply['id'])
            assert len(population.live()) == 6 and population.view['epoch'] is None
            assert session.ran[-1]['input'] == record['id'] and session.ran[-1]['frame'] == 0
        finally:
            await cleanup(manager, worker)
    asyncio.run(go())


def test_cancelled_acquisition_keeps_the_attempted_slot():
    async def go():
        clock = Clock()
        entered = asyncio.Event()
        async def capture(m, slot):
            entered.set()
            await asyncio.Event().wait()
        s, _, _ = scheduler(clock, capture)
        m = plan('r4', 'capture', 30, 270, PIN, clock(), ('capture',), acquisition={'seconds': 5})
        task = s.start(m)
        await entered.wait()
        s.cancel(m.rid)
        await asyncio.gather(task, return_exceptions=True)
        assert m.status == 'cancelled' and len(m.ledger) == 1
        assert m.ledger[0].n == 0 and m.ledger[0].outcome == 'failed'
        assert 'cancellation' in m.ledger[0].why
    asyncio.run(go())


@pytest.mark.parametrize('cancel', [False, True])
def test_completed_clip_keeps_identity_when_slide_moves_or_caller_cancels_during_write(tmp_path, monkeypatch, cancel):
    import threading
    from types import SimpleNamespace
    from orchestrator.research import acquisition
    from tests.test_research_fresh_motion import motion_hub
    from tests.test_motion_tracks import moving_slide

    async def go():
        hub = motion_hub()
        hub.source.get = lambda fid: SimpleNamespace(jpeg=hub.ring[fid],
            received_at=hub.infos[fid].wall_at - 90) if fid in hub.ring else None
        entered, release = threading.Event(), threading.Event()
        field_matches = [True]
        original = acquisition.save_clip
        def delayed(*args, **kwargs):
            field_matches[0] = False  # acquisition ended; the user moves the slide while disk writes
            entered.set()
            assert release.wait(HANG_S)
            return original(*args, **kwargs)
        monkeypatch.setattr(acquisition, 'save_clip', delayed)
        updates = []
        clock = Clock()
        async def capture(m, slot):
            def field():
                assert field_matches[0], 'completed capture must not recheck the later live field'
            def owner():
                assert m.status == 'watching'
            return await acquisition.acquire(hub, tmp_path, m, slot, '2026-10-10', field, owner,
                                             lambda: updates.append(slot.entry))
        s, _, _ = scheduler(clock, capture)
        m = plan('r4', 'capture', 30, 30, PIN, clock(), ('capture',), acquisition={'seconds': 5})
        task = s.start(m)
        try:
            await asyncio.sleep(0)
            sample = moving_slide(seconds=.1)[0]
            for n in range(21):
                hub.add(sample.image, 500, -300, wall=104 + n * .25)
                await asyncio.sleep(.02)
            await until_async(lambda: entered.is_set())
            assert entered.is_set()
            if cancel:
                s.cancel(m.rid)
                await asyncio.gather(task, return_exceptions=True)
                assert m.ledger[0].outcome == 'failed'
                release.set()
                await until_async(lambda: updates)
                assert updates == [m.ledger[0].entry]
                assert m.ledger[0].entry.startswith('acq-') and m.status == 'cancelled'
            else:
                release.set()
                await until_async(lambda: m.ledger)
                assert m.ledger[0].outcome == 'done' and m.ledger[0].entry.startswith('acq-')
        finally:
            release.set()
            s.cancel(m.rid)
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(go())


def test_retained_frame_loading_reserves_memory_until_cancelled_reader_really_finishes(world, tmp_path, monkeypatch):
    import threading
    from orchestrator.research import acquisition
    from orchestrator.research.missions import Slot
    from orchestrator.capture import captures

    async def go():
        m = plan('r4', 'capture', 30, 270, PIN, 1000, ('capture',), acquisition={'seconds': 5})
        info = world.latest_info
        info.seq, info.boot = 800, 'synthetic-camera'
        record = acquisition.save_clip(tmp_path, [(world.source.get(info.id), info)] * 2,
            mission=m, slot=Slot(0, 1000, 'capture'), day='2026-10-10')
        memory = captures(world).memory
        baseline = memory.reserved['research']
        original = acquisition.picture
        called = []
        def unexpected(*args, **kwargs):
            called.append(True)
            return original(*args, **kwargs)
        monkeypatch.setattr(acquisition, 'picture', unexpected)
        occupied = memory.reserve('research', memory.allowances['research'] - baseline, 'occupied allowance')
        try:
            with pytest.raises(Exception, match='memory|room|research'):
                await acquisition.open_capture(world, tmp_path, '2026-10-10', record['id'], 0)
            assert not called
        finally:
            occupied.release()

        entered, release = threading.Event(), threading.Event()
        def delayed(*args, **kwargs):
            entered.set()
            assert release.wait(HANG_S)
            return original(*args, **kwargs)
        monkeypatch.setattr(acquisition, 'picture', delayed)
        task = asyncio.create_task(acquisition.open_capture(world, tmp_path, '2026-10-10', record['id'], 0))
        try:
            await until_async(lambda: entered.is_set())
            assert entered.is_set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert memory.reserved['research'] == baseline + record['frames'][0]['bytes']
            release.set()
            await until_async(lambda: memory.reserved['research'] == baseline)
            assert memory.reserved['research'] == baseline
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(go())


def test_ordinary_schedule_tool_reaches_manager_and_preserves_request_origin(world, tmp_path, monkeypatch):
    from orchestrator.brain.toolbox import build, Services
    from orchestrator.brain.registry import ToolContext
    from orchestrator.research.desk import Desk
    from orchestrator.research import acquisition
    from tests.test_mission_manager_lifecycle import setup_manager, kept_researcher, cleanup

    async def go():
        manager, clock, worker = setup_manager(world, tmp_path)
        world.settings.research_cap = None
        session = kept_researcher(manager)
        attempts, filed_in = [], set()
        async def camera_boundary(hub, root, mission, slot, day, check, owner_check, on_retained, session=None):
            check()
            attempts.append((slot.n, clock()))
            filed_in.add(session)  # each clip's manifest says its researcher's session (ADR 0017)
            await clock.sleep(5)
            return {'ok': True, 'id': f'synthetic-slot-{slot.n}'}
        monkeypatch.setattr(acquisition, 'acquire', camera_boundary)  # external capture completion, no fake model bill
        registry = build(Services(hub=world, research=manager, desk=Desk(manager)))
        words = 'Capture ten five-second clips every 30 seconds for this researcher.'
        ctx = ToolContext('chat', 99, None, words)
        args = {'researcher': session.rid, 'count': 10, 'every_seconds': 30,
                'clip_seconds': 5, 'asked_with': words}
        try:
            action = registry.start('schedule_acquisition', args, ctx)
            await action.task
            assert action.ok, action.summary
            await manager.missions.tasks[session.rid]
            m = session.mission
            assert m.status == 'done' and len(m.ledger) == 10
            assert attempts == [(n, 1000 + 30*n) for n in range(10)]
            assert filed_in == {session.sitting}
            assert m.origin['channel'] == 'chat' and m.origin['turn_id'] == 99
            assert len(worker.jobs) == 1 and worker.jobs[0]['phase'] == 'finishing'
            assert worker.jobs[0]['rows'] == 10
            assert manager.mission_runs(m) == 1
        finally:
            await cleanup(manager, worker)
    asyncio.run(go())


def test_cancel_then_immediate_replacement_persists_the_old_attempt(world, tmp_path, monkeypatch):
    from orchestrator.research import ResearchManager, acquisition
    from tests.test_mission_manager_lifecycle import setup_manager, kept_researcher, cleanup, DAY

    async def go():
        manager, _, worker = setup_manager(world, tmp_path)
        world.settings.research_cap = None
        session = kept_researcher(manager)
        entered = asyncio.Event()

        async def camera_boundary(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(acquisition, 'acquire', camera_boundary)
        old_task = None
        try:
            old = manager.start_mission(session.rid, 'capture', 30, 270, acquisition={'seconds': 5})
            old_task = manager.missions.tasks[session.rid]
            await entered.wait()
            manager.missions.cancel(session.rid)
            replacement = manager.start_mission(session.rid, 'capture', 30, 270, acquisition={'seconds': 5})
            reserved = manager.spending.committed()
            await asyncio.gather(old_task, return_exceptions=True)
            assert session.mission is replacement
            assert manager.spending.committed() == reserved
            restored = ResearchManager(world, root=manager.root, today=lambda: DAY).get(session.rid)
            assert restored.mission.id == replacement.id
            previous = restored.past_missions[0]
            assert previous.id == old.id and previous.status == 'cancelled'
            assert len(previous.ledger) == 1 and previous.ledger[0].outcome == 'failed'
            assert 'cancellation' in previous.ledger[0].why
        finally:
            await cleanup(manager, worker)
    asyncio.run(go())


def test_motion_uses_retained_clip_after_restart_without_camera_acquisition(tmp_path):
    import json
    from orchestrator.research.acquisition import save_clip
    from orchestrator.research.missions import Slot
    from orchestrator.research.hands import Hands
    from orchestrator.motiontracks import MotionFrames, tracks_of
    from tests.test_motion_tracks import MotionHub, moving_slide
    from tests.test_research_samples import session, DAY

    async def go():
        hub = MotionHub(moving_slide(seconds=2), disc=False)
        frames = MotionFrames(hub.captures)
        frames.received_times = {}
        for fid, info in hub.infos.items():
            frames.add(hub.captures.adopt(info, hub.ring[fid]))
            frames.received_times[fid] = info.wall_at - 90
        m = plan('r2', 'capture', 30, 270, PIN, 1000, ('capture',), acquisition={'seconds': 5})
        try:
            record = save_clip(tmp_path / 'acquisitions', frames, mission=m,
                               slot=Slot(0, 1000, 'capture'), day=DAY)
        finally:
            frames.release()
        manifest = tmp_path / 'acquisitions' / DAY / record['id'] / 'manifest.json'
        record['run'] = 'earlier-app-run'
        manifest.write_text(json.dumps(record))
        restarted = MotionHub(moving_slide(seconds=.1), disc=False)  # colliding frame ids; original epoch must remain historical
        from orchestrator.day import DayStore
        from orchestrator.motiontracks import TracksStore
        restarted.tracks_store = TracksStore(restarted, tmp_path / 'evidence',
            day=DayStore(root=tmp_path / 'sessions', day=DAY))
        restarted.source.is_fresh = lambda: False
        restarted.source.get = lambda fid: None
        s = session()
        s.root = tmp_path
        hands = Hands(restarted)
        reply = await hands.run(s, 'motion', 'compare this retained interval', input=record['id'])
        assert reply['ok'], reply
        assert 'retained camera clip' in reply['words']
        result = tracks_of(restarted).get(reply['id'])
        assert result.capture_id < 0 and result.view['epoch'] is None
        assert result.span['seconds'] == pytest.approx(2, abs=.1)
        assert result.summary['moving'] > 0
        assert s.ran[-1]['input'] == record['id']
        assert not restarted.sent  # background analysis never redirects presentation
        await until_async(lambda: result.status != 'saving')
        assert result.status == 'saved'
        assert not restarted.captures.held()
    asyncio.run(go())
