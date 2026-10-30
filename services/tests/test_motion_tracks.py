"""Motion tracks as evidence (M3.2 of docs/design/2026-10-06-captures-operations-evidence.md;
orchestrator/motiontracks.py): track_motion runs as an operation with an Execution and keeps what it
measured as a tracks item (slide coordinates, times, frames, epoch, span, summary, scale, producer and the
captured view), counted in memory, saved atomically with the manifest last, journaled and read back; a
followed outline's trail is kept the same way with its breaks; the "tracks" presentation is drawn live only
while its place is verified by its own pixels, else on its captured view; the verbs act on it and are done
once the studio shows them; a reuse is never said to be a fresh run."""
import asyncio
import hashlib
import json
import threading
import time
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.capture import Captures, Memory
from orchestrator.fov import FieldOfView
from orchestrator.motiontracks import TrackLayer, TracksStore, Tracks, keep_measured, lines, track_layer_of, tracks_of
from orchestrator.options import Settings
from orchestrator.scene import Scene
from tests.test_movement import MOVERS, STILL, clip
from tests.test_resources import OCT_7
from tests.test_trail import ESTIMATED
from tests.waiting import HANG_S, until

W, H = 1280, 720
# The gate's bars, said as a fact only when both a view's place change and its seen path are under them (round 6).
BARS = ("(both under the 10 px of place change and 12 px of path a trail needs before its outline's redraws can be told "
        "from motion)")
FOV = (640.0, 360.0, 340.0)


class MotionHub:
    """A hub holding frames as the live one does: JPEGs in the ring, facts (offset, epoch, disc, the
    bridge's seq and boot) in infos, a capture store with its memory bound."""

    def __init__(self, frames=(), memory: Memory | None = None, reading=None, disc=FOV, base=(0.0, 0.0)) -> None:
        self.scene, self.settings = Scene(), Settings()
        self.ring, self.infos = {}, OrderedDict()
        self.latest_info = None
        self.source = SimpleNamespace(get=lambda fid: SimpleNamespace(id=fid, jpeg=self.ring[fid]) if fid in self.ring else None,
                                      is_fresh=lambda: True)
        self.captures = Captures(self, memory or Memory())
        self.reading = reading
        self.disc = disc
        self.sent = []
        self.n = 0
        for f in frames:  # `base`: where the slide's offset stood when the clip began (it never starts at 0 live)
            self.add(f.image, f.dx + base[0], f.dy + base[1], wall=100.0 + f.t)

    def add(self, image, dx, dy, epoch=0, wall=None, disc=None):
        self.n += 1
        disc = self.disc if disc is None else disc
        self.ring[self.n] = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        fov = FieldOfView(disc[0], disc[1], disc[2], 1.0) if disc else None  # None: no eyepiece found (the whole frame)
        info = SimpleNamespace(id=self.n, width=W, height=H, dx=float(dx), dy=float(dy), epoch=epoch,
                               fov=fov, disc=disc[2] if disc else 0.0, tracked=True,
                               wall_at=wall if wall is not None else 200.0 + self.n, seq=5000 + self.n, boot="b0")
        self.infos[self.n] = info
        self.latest_info = info
        return info

    def scale_reading(self):
        return self.reading

    def broadcast(self, payload):
        self.sent.append(json.loads(json.dumps(payload)))


def run(coro):
    return asyncio.run(coro)


def tool(hub, args=None, magnifier=None, log=None):
    from orchestrator.brain.toolbox import Services, build

    registry = build(Services(hub=hub, magnifier=magnifier, log=log))

    async def go():
        r = registry.start("track_motion", args or {}, ToolContext("chat", 1, 7, "are they swimming?"))
        await r.task
        return r

    return run(go())


def moving_slide(seconds=4.0):
    return clip(MOVERS, STILL, seconds=seconds, slide=(80.0, -20.0))


def wait_saved(item):
    """The background save itself (its thread), not only its status: outside a running loop the thread says "saved"
    once the journal files the item, then mirrors it into a dataset being recorded."""
    for thread in threading.enumerate():
        if thread.name == f"tracks-{item.id}":
            thread.join(HANG_S)
    until(lambda: item.status != "saving")
    return item.status


# --- 1. a motion measurement is evidence ------------------------------------------------------------
def test_track_motion_runs_as_an_operation_and_keeps_its_tracks_as_evidence():
    from orchestrator.operations import runner
    from orchestrator.produces import TOOLS
    from orchestrator.resources import Index

    frames = moving_slide()
    hub = MotionHub(frames, disc=False, base=(500.0, -300.0))  # every dot inside the measured view
    logged = []
    r = tool(hub, {"show": False}, log=logged.append)
    assert r.ok, r.summary
    item = tracks_of(hub).latest()
    assert item is not None and item.id == "m1" and TOOLS["track_motion"] == "tracks"
    # An Execution like any other: the operation, its one implementation (an algorithm), origin, policy, attempts, time.
    execution = runner(hub).get(item.made_by["execution"])
    assert (execution.operation, execution.executed, execution.origin, execution.policy) == ("motion", "spots", "brain-default", "route")
    assert execution.attempts[0].ok and execution.attempts[0].ms > 0 and execution.outputs == ["m1"]
    assert item.made_by["implementation"] == "spots" and item.operation == "motion" and item.how == "measured"
    # The measured summary, the span, the epoch and the scale it used (none: pixels).
    assert item.summary["counted"] == len(MOVERS) + len(STILL) and item.summary["moving"] == len(MOVERS)
    assert item.summary["unit"] == "px/s" and item.scale == {"umPerPx": None, "basis": None, "objective": None}
    assert item.summary["slideSpeed"] > 70  # the slide's own motion, said apart
    assert 3.8 < item.span["seconds"] < 4.1 and item.span["frames"] == len(frames) and item.view["epoch"] == 0
    # Tracks in slide coordinates: a still dot stays put on the slide however the slide moved under it.
    still = [r for r in item.rows if not r["values"]["moving"]]
    # (only those the slide never carried to the frame's edge: frame x is slide x plus the offset, 500 at first)
    for row in [r for r in still if item.points[item.rows.index(r)][0][0, 0] + 500 < 900]:
        arr = item.points[item.rows.index(row)][0]
        assert np.ptp(arr[:, 0]) < 6 and np.ptp(arr[:, 1]) < 6  # a centre jitters a px or two; the slide moved 320
    # ...and where it is on the slide: its frame px minus its frame's offset (the dot drawn at (200, 600) when
    # the offset stood at (500, -300)), as outlines are kept, drawn at p + offset now.
    near = min(still, key=lambda r: np.hypot(*(item.points[item.rows.index(r)][0][0, :2] - (-300, 900))))
    assert np.allclose(item.points[item.rows.index(near)][0][0, :2], (-300, 900), atol=2)
    # Each place with its time and frame; each frame with the bridge's (boot, seq).
    arr = item.points[0][0]
    frames_by_id = {f[0]: f for f in item.frames}
    assert all(int(f) in frames_by_id for f in arr[:, 3]) and frames_by_id[int(arr[0, 3])][1:3] == ["b0", 5000 + int(arr[0, 3])]
    assert np.all(np.diff(arr[:, 2]) > 0)
    # What made it, with its version and configuration; what was done to its input; its captured view.
    assert item.producer["version"] == "orchestrator.movement blobs/1" and item.producer["configuration"]["smooth"] == 5
    assert "slide px" in item.transformations["coordinates"]
    assert item.picture[:2] == b"\xff\xd8" and item.capture_id == len(frames)
    assert item.title.startswith("Motion · ") and item.title.endswith(", 4 s")
    # In the index, by kind and plain title, with what made it.
    [entry] = Index(hub).list(kind="tracks")
    assert entry.title == item.title and "5 of 9 small things moved" in entry.summary
    assert entry.made_by["implementation"] == "spots" and entry.source["seq"] == 5000 + len(frames)
    # The tool's words and its measurement record name it.
    assert "Kept as Motion · " in r.summary and "(m1)" in r.summary
    record = [x for x in logged if x.get("kind") == "measurement"][-1]
    assert record["evidence"] == "m1" and record["execution"] == execution.id and record["reused"] is False


def test_a_reuse_of_the_same_measurement_is_said_and_makes_no_second_item():
    """Invariant 5: two requests over the very same frames share one job, one Execution and one item."""
    from orchestrator.operations import runner

    frames = moving_slide()
    hub = MotionHub(frames)
    clip_ = [(hub.source.get(i), hub.infos[i]) for i in hub.infos]

    async def both():
        return await asyncio.gather(keep_measured(hub, clip_, None, "", 4.0), keep_measured(hub, clip_, None, "", 4.0))

    (a, reused_a, done_a), (b, reused_b, done_b) = run(both())
    assert a is b and len(tracks_of(hub).items) == 1
    assert sorted([reused_a, reused_b]) == [False, True] and done_a.execution is done_b.execution
    assert [e for e in runner(hub).executions.values() if e.operation == "motion"] == [done_a.execution]
    # Unsaved evidence held once: the second request's hold was let go.
    assert hub.captures.memory.reserved["unsaved"] == a.hold.reservation.nbytes


def test_the_reuse_is_said_in_the_words_never_as_a_fresh_run(monkeypatch):
    frames = moving_slide()
    hub = MotionHub(frames)
    from orchestrator import motiontracks

    real = motiontracks.keep_measured

    async def shared(*args, **kw):
        item, _reused, done = await real(*args, **kw)
        return item, True, done

    monkeypatch.setattr(motiontracks, "keep_measured", shared)
    r = tool(hub, {"show": False})
    assert r.ok and r.summary.startswith("(Reused: these same frames were measured at ") and "no new run" in r.summary


def test_its_memory_is_counted_and_a_full_allowance_refuses_it_with_the_reason():
    frames = moving_slide()
    hub = MotionHub(frames)
    r = tool(hub, {"show": False})
    item = tracks_of(hub).latest()
    held = hub.captures.memory.reserved["unsaved"]
    want = len(item.capture.jpeg) + len(item.picture) + sum(a.nbytes for segs in item.points for a in segs)
    assert r.ok and held == want  # its view, its picture and its points, settled to what it keeps (contract 1)
    full = MotionHub(frames, memory=Memory({"interactive": 64, "analysis": 160, "unsaved": 0.01}))
    refused = tool(full, {"show": False})
    # An allowance smaller than the work alone: said as too large for it, never as full or worth waiting for.
    assert refused.ok is False and "more than all 0 MB set aside for analyses not yet saved" in refused.summary
    assert tracks_of(full).items == {}


# --- saving and reading back ----------------------------------------------------------------------
def test_it_is_saved_atomically_with_the_manifest_last_journaled_and_read_back(tmp_path, monkeypatch):
    import os
    import threading
    from orchestrator.day import DayStore

    order = []
    folder = tmp_path / "evidence" / "2026-10-07" / "m1"
    saving, release = threading.Event(), threading.Event()
    real = os.replace

    def replace(src, dst):
        path = Path(dst)
        if path.parent == folder:
            order.append(path.name)
            if path.name == "capture.jpg":
                saving.set()
                assert release.wait(HANG_S), "test did not release the save"
        return real(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    day = DayStore(root=tmp_path / "sessions", day="2026-10-07", clock=lambda: OCT_7)  # used on the date it began
    hub = MotionHub(moving_slide())
    hub.execution_day = day
    hub.tracks_store = TracksStore(hub, tmp_path / "evidence", day=day)
    try:
        assert tool(hub, {"show": False}).ok
        item = hub.tracks_store.latest()
        assert saving.wait(HANG_S), "save did not reach the filesystem"
        # Preserve the independently measured values before saving releases them.
        assert item.points is not None
        points = [[a.copy() for a in segs] for segs in item.points]
    finally:
        release.set()
    assert wait_saved(item) == "saved"
    assert order[-1] == "manifest.json" and set(order) == {"capture.jpg", "tracks.jpg", "tracks.npz", "manifest.json"}
    manifest = json.loads((folder / "manifest.json").read_text())
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest
    # Saved: its pixels and points leave memory, its hold is let go.
    assert item.capture is None and item.picture is None and item.points is None
    assert hub.captures.memory.reserved["unsaved"] == 0
    journal = [json.loads(line)["type"] for line in day.journal.path.read_text().splitlines()]
    assert "tracks-made" in journal and "tracks-saved" in journal

    # A restart, with the optional execution log off: everything interpreting it needs is in its manifest.
    monkeypatch.setenv("MICROMIND_EXECUTION_INSPECT", "0")
    again = DayStore(root=tmp_path / "sessions", day="2026-10-07", clock=lambda: OCT_7)
    back = TracksStore(SimpleNamespace(), tmp_path / "evidence", day=again).get("m1")
    assert back is not None and back.status == "saved" and back.title == item.title
    assert back.made_by["execution"] == item.made_by["execution"] and back.made_by["origin"] == "brain-default"
    assert back.producer == item.producer and back.summary == item.summary and back.frames == item.frames
    assert manifest["source"]["seq"] == item.view["seq"] and manifest["source"]["boot"] == "b0"
    store = TracksStore(SimpleNamespace(), tmp_path / "evidence", day=again)
    loaded = store.points(store.get("m1"))
    assert all(np.array_equal(x, y) for segs_a, segs_b in zip(loaded, points) for x, y in zip(segs_a, segs_b))
    assert store.picture(store.get("m1"))[:2] == b"\xff\xd8"
    assert store.new_id() == "m2"  # an id is never given twice in a day

    # A partial write is reported, not loaded.
    (folder / "tracks.npz").write_bytes(b"torn")
    torn = TracksStore(SimpleNamespace(), tmp_path / "evidence", day=DayStore(root=tmp_path / "sessions", day="2026-10-07"))
    assert torn.get("m1") is None and "m1: incomplete, not loaded" in torn.problems[0]


def test_saved_is_said_once_its_session_journal_files_it(tmp_path, monkeypatch):
    """A restart reads tracks back from their session's journal, so "saved" is said only once the journal files them:
    never while that is still being written (as evidence.py's populations, whose race failed on the macos-15 runner).
    The journal append is held here; its status says "saving" until it is done, and a restart then finds the item."""
    import os
    from orchestrator.day import DayStore

    day = DayStore(root=tmp_path / "sessions", day="2026-10-07", clock=lambda: OCT_7)
    hub = MotionHub(moving_slide())
    hub.execution_day = day
    hub.tracks_store = TracksStore(hub, tmp_path / "evidence", day=day)
    written, filing, release = threading.Event(), threading.Event(), threading.Event()
    real_replace, record = os.replace, day.record_tracks

    def replace(src, dst):
        if Path(dst).name == "manifest.json":  # the last file: filed once the tool's loop is gone (off the loop)
            assert written.wait(HANG_S), "test did not let the manifest be written"
        return real_replace(src, dst)

    def held(*args, **kwargs):
        filing.set()
        assert release.wait(HANG_S), "test did not release the journal"
        return record(*args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(day, "record_tracks", held)
    try:
        assert tool(hub, {"show": False}).ok
        item = hub.tracks_store.latest()
        written.set()
        assert filing.wait(HANG_S), "the save never reached the journal"
        assert item.status == "saving"  # its files are written, its filing is not: not said saved yet
        assert item.points is not None and item.hold is not None  # nor its points let go
    finally:
        written.set()
        release.set()
    until(lambda: item.status != "saving")  # the status alone, as the studio and a restart wait for it
    assert item.status == "saved"
    again = DayStore(root=tmp_path / "sessions", day="2026-10-07", clock=lambda: OCT_7)
    back = TracksStore(SimpleNamespace(), tmp_path / "evidence", day=again).get(item.id)
    assert back is not None and back.status == "saved" and back.title == item.title


def test_a_journal_that_does_not_file_them_is_said_failed_never_saved(tmp_path, monkeypatch):
    """Their files written but the session's journal raising: a restart would not find them, so they are said failed
    (with why), never saved, and their points, pictures and hold stay in memory."""
    from orchestrator.day import DayStore

    day = DayStore(root=tmp_path / "sessions", day="2026-10-07", clock=lambda: OCT_7)
    hub = MotionHub(moving_slide())
    hub.execution_day = day
    logged = []
    hub.tracks_store = TracksStore(hub, tmp_path / "evidence", day=day, log=logged.append)

    append = day.journal.append

    def broken(event):
        if event.get("type") == "tracks-saved":
            raise OSError("journal disk full")
        return append(event)

    # Exercise the real SessionStore recorder: an in-memory fallback is not a saved result.
    monkeypatch.setattr(day.journal, "append", broken)
    assert tool(hub, {"show": False}).ok
    item = hub.tracks_store.latest()
    assert wait_saved(item) == "failed" and "OSError: journal disk full" in item.status_why
    assert item.points is not None and item.picture is not None and item.capture is not None
    assert item.hold is not None and hub.captures.memory.reserved["unsaved"] > 0
    assert logged[-1]["kind"] == "tracks-save-failed" and "journal disk full" in logged[-1]["error"]
    again = DayStore(root=tmp_path / "sessions", day="2026-10-07", clock=lambda: OCT_7)
    assert TracksStore(SimpleNamespace(), tmp_path / "evidence", day=again).get(item.id) is None

    monkeypatch.setattr(day.journal, "append", append)
    hub.tracks_store.schedule_save(item)
    assert wait_saved(item) == "saved"
    reopened = DayStore(root=tmp_path / "sessions", day="2026-10-07", clock=lambda: OCT_7)
    assert TracksStore(SimpleNamespace(), tmp_path / "evidence", day=reopened).get(item.id) is not None


# --- 2. a followed thing's trail can be kept ---------------------------------------------------------
def test_a_followed_outlines_trail_is_kept_with_its_breaks_as_they_were():
    from orchestrator.trail import Trails
    from tests.test_trail import Clock, outline

    hub = MotionHub()
    hub.add(cv2.GaussianBlur(np.random.default_rng(3).integers(0, 255, (H, W), dtype=np.uint8), (0, 0), 2.0), 0, 0)
    clock = Clock()
    hub.trails = Trails(clock)
    obj = outline(100, 100)
    obj.label = "paramecium"
    hub.scene.add(obj)

    def step(x, state="seen"):
        clock.t += 0.2
        hub.trails.observe(outline(x, 100), state, 0, wrote=True, frame=1)

    for x in (100, 110, 120):
        step(x)
    step(125, "missed")  # a gap
    for x in (140, 150):
        step(x)
    step(160, "lost")  # lost, then found again
    for x in (200, 210):
        step(x)
    r = tool(hub, {"of": obj.id, "show": False})
    assert r.ok, r.summary
    item = tracks_of(hub).latest()
    assert item.how == "followed" and item.operation == "track" and item.object_id == obj.id and item.label == "paramecium"
    segments = item.rows[0]["segments"]
    assert [(s["start"], s["end"]) for s in segments] == [("first", "gap"), ("after-gap", "lost"), ("reacquired", "open")]
    assert [len(a) for a in item.points[0]] == [3, 2, 2]  # each segment as kept, nothing joined
    assert item.made_by["origin"] == "background" and item.made_by["how"] == "followed"
    assert item.title.startswith("Trail · ") and "the trail of paramecium" in item.words() and "2 gaps" not in item.words()
    assert "1 gap" in item.words() and "lost 1x" in item.words()
    assert item.picture[:2] == b"\xff\xd8"
    # Nothing to keep: said, with what to do.
    from orchestrator.scene import SceneObject
    from tests.test_objtrack import square

    hub.scene.add(SceneObject(id="o2", kind="highlight", polygons=square(400, 400), anchor_frame=1, anchor_epoch=0,
                              anchor_offset=(0.0, 0.0), source="click"))
    refused = tool(hub, {"of": "o2", "show": False})
    assert refused.ok is False and "has no trail" in refused.summary and "follow it" in refused.summary


# --- 3. the "tracks" presentation: live only while verified, else on its captured view ---------------
def textured(seed=5):
    return cv2.GaussianBlur(np.random.default_rng(seed).integers(0, 255, (H + 400, W + 400), dtype=np.uint8), (0, 0), 2.0)


SLIDE = textured()


def view_of(dx, dy, slide=SLIDE):
    x0, y0 = int(200 - dx), int(200 - dy)
    return slide[y0:y0 + H, x0:x0 + W]


def kept_on(hub) -> Tracks:
    """A tracks item measured on the hub's latest view: two tracks near the eyepiece's middle."""
    from orchestrator.population import view_facts

    store = tracks_of(hub)
    capture = hub.captures.take(hub.latest_info.id)
    dx, dy = capture.dx, capture.dy
    a = np.array([[600 - dx + 2 * k, 350 - dy, 100 + k * 0.1, capture.id] for k in range(20)], float)
    b = np.array([[700 - dx, 380 - dy + k, 100 + k * 0.1, capture.id] for k in range(20)], float)
    rows = [{"id": "t1", "values": {"net": 20.0, "path": 21.0, "straightness": 0.95, "moving": True},
             "segments": [{"epoch": capture.epoch, "start": "first", "end": "end", "n": 20}]},
            {"id": "t2", "values": {"net": 2.0, "path": 9.0, "straightness": 0.2, "moving": False},
             "segments": [{"epoch": capture.epoch, "start": "first", "end": "end", "n": 20}]}]
    item = Tracks(store.new_id(), "measured", "motion", "spots", {"execution": "x-test", "origin": "brain-default"},
                  capture.id, view_facts(capture), capture.scale, {"from": 100.0, "to": 101.9, "seconds": 1.9, "frames": 20},
                  {"counted": 2, "moving": 1, "unit": "px/s", "threshold": 8.0}, {"umPerPx": None, "basis": None}, rows,
                  [[a], [b]], capture=capture, picture=capture.jpeg)
    store.add(item)
    return item


def test_tracks_are_drawn_live_only_while_their_own_view_matches_where_the_slide_puts_it():
    hub = MotionHub()
    hub.add(view_of(0, 0), 0, 0)
    item = kept_on(hub)
    layer = TrackLayer(hub, hub.broadcast)
    layer.show(item)
    kept_run = item.view["run"]
    run(layer.check())
    assert layer.verified() and layer.placement["score"] > 0.9
    hub.add(view_of(37, -21), 37, -21)  # the slide moved: checked again where its move puts them
    run(layer.check())
    assert layer.verified()
    hub.add(view_of(37, -21, textured(9)), 37, -21)  # same offset, other pixels (an occlusion, a mistaken offset)
    run(layer.check())
    assert not layer.verified() and "did not match" in layer.placement["why"]  # unverified, never "moved"
    noisy = np.clip(view_of(37, -21).astype(float) + np.random.default_rng(2).normal(0, 30, (H, W)), 0, 255).astype(np.uint8)
    hub.add(noisy, 37, -21)  # its pixels where they should be, but too poorly matched to count (a poor frame)
    run(layer.check())
    assert not layer.verified() and layer.placement["score"] < 0.8
    hub.add(view_of(37, -21), 37, -21)  # a recheck restores verified
    run(layer.check())
    assert layer.verified()
    item.view["run"] = "an earlier start"  # read back after a restart: its epoch and offsets were that run's
    run(layer.check())
    assert not layer.verified() and "restarted" in layer.placement["why"]
    item.view["run"] = kept_run
    run(layer.check())
    assert layer.verified()
    hub.add(view_of(0, 0), 0, 0, epoch=1)  # continuity lost
    run(layer.check())
    assert not layer.verified() and "lost continuity" in layer.placement["why"]
    hub.add(view_of(37, -21), 37, -21, disc=(640.0, 360.0, 350.0))  # the eyepiece's scale changed (the lens)
    run(layer.check())
    assert not layer.verified() and "scale changed" in layer.placement["why"]
    wire = hub.sent[-1]
    assert wire["type"] == "tracks-layer" and wire["verified"] is False and wire["why"]


def test_the_wire_sends_each_segment_in_its_views_pixels_and_breaks_stay_apart():
    hub = MotionHub()
    hub.add(view_of(10, 0), 10, 0)
    item = kept_on(hub)
    item.rows[0]["segments"] = [{"epoch": 0, "start": "first", "end": "gap", "n": 10},
                                {"epoch": 0, "start": "after-gap", "end": "end", "n": 10},
                                {"epoch": 3, "start": "after-gap", "end": "end", "n": 1}]
    a = item.points[0][0]
    item.points[0] = [a[:10], a[10:], a[:1]]
    wire = tracks_of(hub).wire(item)
    t1 = next(t for t in wire["tracks"] if t["id"] == "t1")
    assert len(t1["segments"]) == 2  # the other epoch's place is not comparable: not sent to be drawn
    assert t1["segments"][0]["points"][0][:2] == [600.0, 350.0]  # slide px plus its view's offset
    assert wire["unit"] == "px/s" and wire["interval"] and wire["title"] == item.title


class Studio:
    """A stand-in studio that draws what it is sent and reports it (the tracks layer included)."""

    def __init__(self, screen):
        from tests.test_screen import Studio as Base

        self.base = Base(screen)

    def hear(self, message):
        if message.get("type") == "tracks-layer":
            for key in [k for k in self.base.items if k.startswith("tracks:")]:
                if key != f"tracks:{message.get('tracks')}":
                    self.base.items[key]["visible"] = False
            if message.get("tracks"):
                key = f"tracks:{message['tracks']}"
                item = self.base.items.setdefault(key, {"id": key, "kind": "tracks", "z": 1})
                style = message.get("style") or {}
                # As main.ts reports it: "verified in place" only while it draws them on the live view.
                item.update(visible=True, opacity=style.get("opacity", 1.0),
                            style={k: v for k, v in style.items() if k not in ("opacity", "values", "legend")},
                            placement="verified in place" if message.get("verified") else
                            f"not drawn live: {message.get('why') or 'not checked yet'}")
        self.base.hear(message)


def studio_for(hub, magnifier=None):
    from orchestrator.brain.toolbox.present import add_present_tools
    from orchestrator.screen import Screen

    screen = Screen()
    studio = Studio(screen)
    sent = hub.broadcast

    def broadcast(message):
        sent(message)
        studio.hear(message)

    hub.broadcast = broadcast
    screen.broadcast = broadcast
    hub.screen = screen
    track_layer_of(hub).broadcast = broadcast
    registry = Registry()
    add_present_tools(registry, SimpleNamespace(hub=hub, magnifier=magnifier, presenter=None, slidemap=None, agent=None,
                                                publish_settings=None, log=None, history_log=None))
    studio.base.report()
    return registry, screen, studio


def present(registry, hub, **args):
    from orchestrator.ticket import issue, using

    async def go():
        with using(issue(hub, "turn")):
            return await registry.tools["present"].handler(ToolContext("chat", 1, None, "show the tracks"), args)

    return run(go())


def test_the_verbs_act_on_the_tracks_and_are_done_once_the_studio_shows_them():
    hub = MotionHub()
    hub.add(view_of(0, 0), 0, 0)
    item = kept_on(hub)
    registry, screen, studio = studio_for(hub)
    said = present(registry, hub, what=item.id, do="show")
    assert said == f"{item.title} shown; drawn over the live view where their place is checked"
    assert studio.base.items[f"tracks:{item.id}"]["visible"] is True
    assert present(registry, hub, what="the tracks", do="opacity", value="40%") == f"{item.title} at 40% opacity"
    assert present(registry, hub, what="the tracks", do="colour", value="red") == f"{item.title} coloured red"
    said = present(registry, hub, what="the motion tracks", do="colour", value="speed")
    assert said == f"{item.title} coloured by net speed, with a legend"
    assert track_layer_of(hub).style["legend"]["unit"] == "px/s"
    said = present(registry, hub, what="the tracks", do="emphasise", by="speed", top=1, rest="hide")
    assert said == f"{item.title} 1 kept (highest net speed, first 1), the rest hidden"
    assert studio.base.items[f"tracks:{item.id}"]["style"]["emphasis"] == {"members": ["t1"], "rest": "hide"}
    assert screen.owner(f"tracks:{item.id}", "emphasis").startswith("request:")
    assert any(f"tracks:{item.id}" in line for line in screen.lines())
    with pytest.raises(ToolFailed) as refused:
        present(registry, hub, what="the tracks", do="fill")
    assert "tracks are lines" in str(refused.value) and refused.value.next
    assert present(registry, hub, what=item.id, do="hide") == f"{item.title} hidden"
    assert track_layer_of(hub).shown is None and studio.base.items[f"tracks:{item.id}"]["visible"] is False
    present(registry, hub, what=item.id, do="open")  # a layer is opened by showing it...
    assert present(registry, hub, what="the tracks", do="close") == f"{item.title} hidden"  # ...and closed by hiding it


def test_track_colour_consumes_the_declared_by_column():
    hub = MotionHub()
    hub.add(view_of(0, 0), 0, 0)
    item = kept_on(hub)
    registry, _, studio = studio_for(hub)
    present(registry, hub, what=item.id, do="show")
    said = present(registry, hub, what=item.id, do="colour", by="straightness")
    assert "coloured by straightness" in said
    layer = track_layer_of(hub)
    assert layer.style["column"] == layer.confirmed_style["column"] == "straightness"
    assert studio.base.items[f"tracks:{item.id}"]["style"]["column"] == "straightness"


@pytest.mark.parametrize("args", [{"do": "emphasise", "outliers": "speed"},
                                 {"do": "opacity", "value": "40%", "by": "speed", "top": 1}])
def test_track_verbs_refuse_selectors_they_do_not_implement_without_changing_style(args):
    hub = MotionHub()
    hub.add(view_of(0, 0), 0, 0)
    item = kept_on(hub)
    registry, _, studio = studio_for(hub)
    present(registry, hub, what=item.id, do="show")
    layer = track_layer_of(hub)
    before = json.loads(json.dumps(layer.style))
    drawn = json.loads(json.dumps(studio.base.items[f"tracks:{item.id}"]))
    with pytest.raises(ToolFailed) as refused:
        present(registry, hub, what=item.id, **args)
    assert refused.value.next
    assert layer.style == before and studio.base.items[f"tracks:{item.id}"] == drawn


def test_show_me_the_tracks_from_earlier_shows_them_on_their_view_when_not_in_place():
    hub = MotionHub()
    hub.add(view_of(0, 0), 0, 0)
    item = kept_on(hub)
    hub.add(view_of(0, 0, textured(11)), 0, 0, epoch=4)  # since then: another part of the slide
    from orchestrator.magnifier import Magnifier
    magnifier = Magnifier(hub)
    registry, screen, studio = studio_for(hub, magnifier)
    said = present(registry, hub, what="the tracks from earlier", do="show")
    assert "shown on the view it was measured on, in the magnifier, not the live view" in said
    assert "lost continuity" in said
    assert "not the live view" in magnifier.picture_now()["label"] and item.title in magnifier.picture_now()["label"]
    # Through the index too (flow 10: "what data do we have?", then "show me the motion one"): the id the
    # index gives opens it as its presentation, as Made today's Open does.
    from orchestrator.resources import Index

    hub.resources = Index(hub)
    track_layer_of(hub).hide()
    [entry] = hub.resources.list(kind="tracks")
    assert f"{entry.title} ({entry.id})" in hub.resources.summary_text() and "open" in entry.actions
    said = present(registry, hub, what=entry.id, do="open")
    assert said.startswith(f"{item.title} shown") and track_layer_of(hub).shown == item.id


def test_the_board_and_the_index_say_what_tracks_there_are_and_where_they_are_shown():
    from orchestrator.population import evidence_lines
    from orchestrator.resources import Index

    hub = MotionHub()
    hub.add(view_of(0, 0), 0, 0)
    item = kept_on(hub)
    lines = evidence_lines(hub)
    assert lines and lines[0].startswith(f"{item.title} ({item.id}): 1 of 2 small things moved") and "not shown" in lines[0]
    layer = track_layer_of(hub)
    layer.show(item)
    run(layer.check())
    assert "shown over the live view, verified in place" in evidence_lines(hub)[0]
    index = Index(hub)
    summary = index.summary_text()
    assert "Motion tracks: 1" in summary and item.title in summary
    read = index.read(item.id)
    assert "values:" in read and '"t1"' in read
    assert index.picture(item.id)[1] == "image/jpeg"


def test_clearing_the_view_takes_the_tracks_off_and_a_late_measurement_shows_nothing():
    """A clear of everything hides the tracks layer (the item stays kept) and supersedes requests made
    before it, so a measurement that lands after it is kept but not shown."""
    from orchestrator.apply import apply
    from orchestrator.brain.toolbox import Services, build
    from orchestrator.ticket import issue, using

    hub = MotionHub()
    hub.add(view_of(0, 0), 0, 0)
    item = kept_on(hub)
    layer = track_layer_of(hub)
    layer.show(item)
    registry = build(Services(hub=hub))

    async def go():
        before = issue(hub, "turn")  # a request made before the clear, whose show lands after it
        with using(issue(hub, "turn")):
            await registry.tools["clear"].handler(ToolContext("chat", 2, None, "clear everything"), {"what": "all"})
        assert layer.shown is None and tracks_of(hub).get(item.id) is item
        with using(before):
            return await apply(hub, lambda _: layer.show(item), scope="motion")

    done = run(go())
    assert not done.ok and "cleared" in done.reason and layer.shown is None


def test_measuring_motion_has_one_way_so_there_is_no_model_to_choose_for_it():
    """`use` and `set_default` choose among an operation's implementations; motion has one (an algorithm),
    so asking to choose for it is refused with the reason, never a crash."""
    from orchestrator.brain.toolbox import Services, build
    from orchestrator.ticket import issue, using

    hub = MotionHub()
    hub.add(view_of(0, 0), 0, 0)
    registry = build(Services(hub=hub))

    async def go():
        with using(issue(hub, "turn")):
            return await registry.tools["use"].handler(ToolContext("chat", 1, None, "use spots"),
                                                       {"operation": "motion", "implementation": "spots"})

    with pytest.raises(ToolFailed) as refused:
        run(go())
    assert "no choice to make for measuring what moves: it is done one way" in str(refused.value)

    async def follow_choice():  # following has one way too (as before M3.2's merge with M3.5's words for every operation)
        with using(issue(hub, "turn")):
            return await registry.tools["use"].handler(ToolContext("chat", 1, None, "use edgetam video"),
                                                       {"operation": "track", "implementation": "edgetam-video"})

    with pytest.raises(ToolFailed) as refused:
        run(follow_choice())
    assert "no choice to make for following outlined things: it is done one way" in str(refused.value)



# --- the partner's review of M3.2 (#182 at c25c395) ---------------------------------------------------------
def follow(hub, steps, label="paramecium", clock=None):
    """A followed outline's trail from (x, y, state, epoch) steps 0.2 s apart; returns the outline."""
    from orchestrator.trail import Trails
    from tests.test_trail import Clock, outline

    clock = clock or Clock()
    hub.trails = Trails(clock)
    obj = outline(steps[0][0], steps[0][1])
    obj.label = label
    hub.scene.add(obj)
    for x, y, state, epoch in steps:
        clock.t += 0.2
        hub.trails.observe(outline(x, y, epoch=epoch), state, epoch, wrote=True, frame=1)
    return obj


GAPPED = ([(100, 100, "seen", 0), (110, 100, "seen", 0), (110, 110, "seen", 0), (110, 110, "lost", 0)]
          + [(110, 110, "missed", 0)] * 50 + [(420, 110, "seen", 0), (430, 110, "seen", 0), (440, 110, "seen", 0)])


@pytest.mark.parametrize("epoch_after", [0, 1])
def test_a_kept_trail_never_measures_speed_or_straightness_across_a_break(epoch_after):
    """B1: lost for 10 s and found 300 px on; its net speed is its own motion within the stretches its place was
    known, never the jump across the gap, so net <= path and straightness is not measured across the break (also
    when it was found again in another view)."""
    hub = MotionHub()
    hub.add(textured(3)[:H, :W], 0, 0, epoch=epoch_after)
    steps = [(x, y, st, epoch_after if x >= 400 else 0) for x, y, st, _e in GAPPED]
    obj = follow(hub, steps)
    r = tool(hub, {"of": obj.id, "show": False})
    assert r.ok, r.summary
    item = tracks_of(hub).latest()
    v = item.rows[0]["values"]
    # Stretch one: 10 right then 10 down (20 px of path, 14.1 px moved); stretch two: 20 right. Both 0.4 s.
    assert v["path"] == pytest.approx(40 / 0.8, rel=1e-3)
    assert v["net"] == pytest.approx(float(np.hypot(30, 10)) / 0.8, rel=1e-3) and v["net"] <= v["path"]
    assert v["straightness"] is None and "never across a break" in v["netBasis"]
    layer = TrackLayer(hub, hub.broadcast)
    layer.show(item)
    if epoch_after == 0:  # its legend is its own measured speed, never one across the gap
        assert layer.style["legend"]["max"] <= v["path"] + 1e-6
    # An unbroken trail: straightness is its own (at most 1).
    hub2 = MotionHub()
    hub2.add(textured(3)[:H, :W], 0, 0)
    obj2 = follow(hub2, [(100, 100, "seen", 0), (110, 100, "seen", 0), (110, 110, "seen", 0)])
    assert tool(hub2, {"of": obj2.id, "show": False}).ok
    v2 = tracks_of(hub2).latest().rows[0]["values"]
    assert v2["straightness"] == pytest.approx(np.hypot(10, 10) / 20, rel=1e-3) and v2["net"] <= v2["path"]


def test_a_kept_trail_that_went_nowhere_says_so_and_is_not_moving():
    """B2: trail.py's own gate. test_trail's back-and-forth (its outline's redraws, within 4 px) and a still cell
    are kept as "stayed within N px of where it started", never as a distance travelled, and not moving."""
    from tests.test_trail import Clock

    hub = MotionHub()
    hub.add(textured(3)[:H, :W], 0, 0)
    clock = Clock()
    obj = follow(hub, [(100, 100, "seen", 0)], label="red cell", clock=clock)
    for k in range(8):  # as test_trail's: written, never far from where it started
        clock.t += 0.6
        from tests.test_trail import outline

        hub.trails.observe(outline(100 + (4 if k % 2 else 0), 100), "seen", 0, wrote=True, frame=1)
    assert hub.trails.words(obj.id) is None
    r = tool(hub, {"of": obj.id, "show": False})
    item = tracks_of(hub).latest()
    assert "it got as far as about 4 px from where it was first seen; 28 px of path was seen" in item.words()
    assert "over the slide" not in item.words()
    assert item.rows[0]["values"]["moving"] is False and item.summary["wentSomewhere"] is False
    assert "28 px of path was seen" in r.summary
    still = MotionHub()
    still.add(textured(3)[:H, :W], 0, 0)
    cell = follow(still, [(640, 360, "seen", 0)] * 20)
    assert tool(still, {"of": cell.id, "show": False}).ok
    kept = tracks_of(still).latest()
    assert kept.rows[0]["values"]["moving"] is False
    assert "its place did not change from where it was first seen " + BARS in kept.words()
    assert tracks_of(still).wire(kept)["tracks"][0]["moving"] is False  # never drawn as a mover
    went = MotionHub()
    went.add(textured(3)[:H, :W], 0, 0)
    mover = follow(went, [(100 + 10 * k, 100, "seen", 0) for k in range(4)])
    assert tool(went, {"of": mover.id, "show": False}).ok
    assert tracks_of(went).latest().rows[0]["values"]["moving"] is True
    assert "30 px over the slide" in tracks_of(went).latest().words()


def test_a_loss_is_said_as_found_again_by_its_look_and_an_open_end_is_explained():
    """Notes: ADR 0012's re-find by look, and what an "open" last segment means."""
    hub = MotionHub()
    hub.add(textured(3)[:H, :W], 0, 0)
    obj = follow(hub, GAPPED)
    assert tool(hub, {"of": obj.id, "show": False}).ok
    item = tracks_of(hub).latest()
    assert "lost 1x, found again by its look" in item.words()
    assert "still being followed when it was kept" in item.transformations["breaks"]
    assert "found again by the outline's look" in item.transformations["breaks"]


def test_save_this_session_keeps_motion_tracks_whole_with_their_points(tmp_path):
    """B3: a saved tracks item is whole in the preview and the dataset, said as motion tracks, with its points
    and picture; one journaled but never saved is said as motion tracks not saved whole."""
    from orchestrator.datasets import snapshot
    from orchestrator.day import DayStore
    from orchestrator.paths import Paths

    paths = Paths.under(tmp_path)
    today = time.strftime("%Y-%m-%d")
    day = DayStore(root=paths.sessions, day=today)
    hub = MotionHub(moving_slide(), disc=False, base=(500.0, -300.0))
    hub.execution_day = day
    hub.tracks_store = TracksStore(hub, paths.evidence, day=day)
    start = time.time() - 60
    assert tool(hub, {"show": False}).ok
    item = hub.tracks_store.latest()
    assert wait_saved(item) == "saved"
    day.record_tracks_made("m7")  # made and never saved
    stop = time.time() + 1
    review = snapshot.preview(paths, start, stop, "today")
    assert review["holds"]["tracks"] == 1 and review["holds"]["tracksCount"] == len(item.rows)
    assert review["holds"]["evidence"] == 0  # no set of outlines is claimed
    assert [e["id"] for e in review["holds"]["evidenceIncomplete"]] == ["m7"]
    assert not any("outlines (m1)" in m for m in review["missing"])
    assert any(m.startswith("Motion tracks (m7) are not saved whole") for m in review["missing"])
    folder = snapshot.save(paths, start, stop, scope="today")
    kept = {e["id"]: e for e in json.loads((folder / "dataset.json").read_text())["evidence"]}
    assert kept["m1"]["status"] == "whole" and kept["m1"]["kind"] == "tracks" and kept["m1"]["tracks"] == len(item.rows)
    names = {Path(f["file"]).name for f in kept["m1"]["files"]}
    assert {f"evidence-{today}-m1-r0-tracks.npz", f"evidence-{today}-m1-r0-tracks.jpg",
            f"evidence-{today}-m1-r0-manifest.json"} <= names
    with np.load(folder / "pictures" / f"evidence-{today}-m1-r0-tracks.npz") as points:
        assert len(points.files) == len(item.rows)


def test_a_dataset_being_recorded_keeps_the_whole_item(tmp_path):
    """S4: the points are the item's core: mirrored with its manifest, picture and view."""
    from orchestrator.day import DayStore

    day = DayStore(root=tmp_path / "sessions", day="2026-10-07")
    hub = MotionHub(moving_slide(), disc=False, base=(500.0, -300.0))
    hub.execution_day = day
    kept = []
    hub.tracks_store = TracksStore(hub, tmp_path / "evidence", day=day, keep=lambda path, name: kept.append(path.name))
    assert tool(hub, {"show": False}).ok
    assert wait_saved(hub.tracks_store.latest()) == "saved"
    assert set(kept) == {"manifest.json", "tracks.npz", "tracks.jpg", "capture.jpg"}


class LaggingStudio:
    """Reports as main.ts does (the layer visible; "verified in place" only while it draws them), but the
    verified wire reaches it late (`frozen`), or never."""

    def __init__(self, screen, frozen=True):
        self.screen, self.frozen, self.state = screen, frozen, {"shown": None, "verified": False, "heard": False}

    def report(self):
        items = [{"id": "live", "kind": "live", "visible": True, "opacity": 1.0, "z": 0}]
        if self.state["shown"]:
            items.append({"id": f"tracks:{self.state['shown']}", "kind": "tracks", "visible": True, "opacity": 1.0, "z": 1,
                          "placement": "verified in place" if self.state["verified"] else "not drawn live: not checked yet"})
        self.screen.report({"items": items, "applied": [], "user": [], "visible": True}, client=self)

    def hear(self, message):
        if message.get("type") == "tracks-layer" and not (self.frozen and self.state["heard"]):
            self.state.update(shown=message.get("tracks"), verified=bool(message.get("verified")), heard=True)
        if message.get("type") == "screen-ask":
            asyncio.get_running_loop().call_soon(self.report)


@pytest.mark.parametrize("lags", [True, False])
def test_drawn_over_the_live_view_is_said_only_once_the_studio_reports_them_drawn(lags, monkeypatch, wall_clock):
    """S1: a layer visible is not tracks drawn: "drawn over the live view" needs the studio's placement."""
    from orchestrator import screen as screen_module
    from orchestrator.motiontracks import show
    from orchestrator.screen import Screen
    from orchestrator.ticket import issue, using

    hub = MotionHub()
    hub.add(view_of(0, 0), 0, 0)
    item = kept_on(hub)
    screen = Screen()
    studio = LaggingStudio(screen, frozen=lags)
    sent = hub.broadcast

    def broadcast(message):
        sent(message)
        studio.hear(message)

    hub.broadcast = screen.broadcast = broadcast
    hub.screen = screen
    track_layer_of(hub).broadcast = broadcast
    studio.report()
    shown = []
    magnifier = SimpleNamespace(show_picture=lambda jpeg, label, object_id=None: shown.append(label) or "/p")

    async def go():
        with using(issue(hub, "turn")):
            return await show(SimpleNamespace(hub=hub, magnifier=magnifier), item)

    t0 = time.monotonic()
    said = run(go())
    took = time.monotonic() - t0
    assert track_layer_of(hub).verified()
    if lags:
        assert not said.startswith("drawn") and "the studio has not drawn them on the live view" in said
        assert shown and "not the live view" in shown[0]  # their own view instead, labelled
        # ...after DRAWN_WAIT_S (1.5 s), not the 4 s a change waits (screen.CONFIRM_S; the re-check's note 2)
        wall_clock.within(took, screen_module.CONFIRM_S - 1.0, "saying it")
    else:
        assert said.startswith("drawn over the live view") and not shown


def test_tracks_near_the_eyepiece_edge_are_checked_inside_the_disc_or_said_to_be_at_its_edge():
    """S3: the region is clipped to the eyepiece disc (masked), not to the square inscribed in it; when nothing of
    it is left inside, the reason is the edge, never "too little detail"."""
    from orchestrator.motiontracks import EDGE

    hub = MotionHub()
    hub.add(view_of(0, 0), 0, 0)  # eyepiece (640, 360) r 340; its inscribed square starts at x 412
    obj = follow(hub, [(320 + 5 * k, 360, "seen", 0) for k in range(6)], label="rotifer")
    assert tool(hub, {"of": obj.id, "show": False}).ok
    item = tracks_of(hub).latest()
    layer = TrackLayer(hub, hub.broadcast)
    layer.show(item)
    run(layer.check())
    assert layer.verified(), layer.placement.get("why")
    hub.add(view_of(30, 0), 30, 0)  # the slide moved it 30 px inward: still checked by what is inside the eyepiece
    run(layer.check())
    assert layer.verified(), layer.placement.get("why")
    edge = MotionHub()
    edge.add(view_of(0, 0), 0, 0)
    obj = follow(edge, [(278 + k, 360, "seen", 0) for k in range(6)], label="rotifer")  # just outside 95% of the disc
    assert tool(edge, {"of": obj.id, "show": False}).ok
    layer = TrackLayer(edge, edge.broadcast)
    layer.show(tracks_of(edge).latest())
    run(layer.check())
    assert not layer.verified() and layer.placement["why"] == EDGE and "too little detail" not in layer.placement["why"]
    assert layer._make_template(tracks_of(edge).latest(), None) == EDGE  # its own region's check says so (N12)


def test_evidence_asked_in_one_session_and_made_after_a_switch_is_filed_in_it_never_listed_in_the_new_one(tmp_path):
    """S2 re-scoped to sessions (ADR 0017; the plan's P4), tracks and populations: a request asked in t1 of one session,
    then a new session while it measures. What it made is filed in the session it was asked in, under its own t1 (a
    session's conversations keep their ids), with an id new for that session; the new session's t1 never lists it."""
    from orchestrator.day import DayStore
    from orchestrator.evidence import Evidence, evidence_of
    from orchestrator.population import _session, _thread, evidence_lines
    from orchestrator.ticket import issue, using
    from tests.test_evidence import population

    day = DayStore(root=tmp_path / "sessions", day="2026-10-07", clock=lambda: OCT_7)
    hub = MotionHub(moving_slide(), disc=False, base=(500.0, -300.0))
    hub.execution_day = day
    hub.tracks_store = TracksStore(hub, tmp_path / "evidence", day=day)
    hub.evidence = Evidence(hub, None, day=day)
    clip_ = [(hub.source.get(i), hub.infos[i]) for i in hub.infos]
    earlier = issue(hub, "turn", asked=day.here())

    async def made_in(ticket):
        with using(ticket):
            return await keep_measured(hub, clip_, None, "", 4.0)

    first, _r, _d = run(made_in(earlier))  # made in t1 of the first session
    with using(earlier):
        cells = population(evidence_of(hub), hub.captures.take(hub.latest_info.id))
        cells.thread, cells.generation, cells.session = _thread(earlier, hub), day.generation, _session(earlier, hub)
    evidence_of(hub).add(cells)
    assert first.thread == "t1" and first.session == "2026-10-07"
    assert lines(hub, "t1") and any(cells.id in x for x in evidence_lines(hub, "t1"))
    late = issue(hub, "turn", asked=day.here())
    assert day.new() == "2026-10-07.2" and day.active_id == "t1"  # a new session: a new t1
    assert hub.tracks_store.items == {} and evidence_of(hub).items == {}  # the one left's are filed with it
    hub.infos = OrderedDict((k + 1000, v) for k, v in hub.infos.items())  # other frames: a new job
    clip2 = [(SimpleNamespace(id=i, jpeg=hub.ring[i - 1000]), SimpleNamespace(**{**vars(info), "id": i}))
             for i, info in hub.infos.items()]
    clip_[:] = clip2
    second, _r, _d = run(made_in(late))
    assert (second.session, second.thread) == ("2026-10-07", "t1")  # its own session's conversation
    assert second.id != first.id  # new for the session it was asked in
    assert wait_saved(second) == "saved"
    left = DayStore(root=tmp_path / "sessions", day="2026-10-07", clock=lambda: OCT_7)
    assert left.tracks[second.id]["thread"] == "t1" and second.id in left.tracks_ids
    assert second.id not in day.tracks and second.id not in day.tracks_ids  # never in the session open now
    board = evidence_lines(hub, "t1")
    assert not any(first.id in x or second.id in x or cells.id in x for x in board)
    assert lines(hub, "t1") == []


def test_tracks_lines_list_only_this_conversations_and_the_shown_one():
    """M20: the Board's tracks are this conversation's and the one shown, never every conversation's."""
    hub = MotionHub()
    hub.add(view_of(0, 0), 0, 0)
    a, b, c = kept_on(hub), kept_on(hub), kept_on(hub)
    a.thread, b.thread, c.thread = "t1", "t2", "t3"
    listed = lambda t: {line.split(" (")[1].split(")")[0] for _at, line in lines(hub, t)}  # noqa: E731
    assert listed("t2") == {b.id}
    track_layer_of(hub).show(c)
    assert listed("t2") == {b.id, c.id}


def test_an_id_made_and_never_saved_is_not_given_again_after_a_restart(tmp_path):
    """M11: ids are a day's own, whatever became of a save (the journal's tracks-made)."""
    from orchestrator.day import DayStore

    day = DayStore(root=tmp_path / "sessions", day="2026-10-07")
    store = TracksStore(SimpleNamespace(), tmp_path / "evidence", day=day)
    assert store.new_id() == "m1"
    day.record_tracks_made("m1")
    again = TracksStore(SimpleNamespace(), tmp_path / "evidence", day=DayStore(root=tmp_path / "sessions", day="2026-10-07"))
    assert again.new_id() == "m2"


@pytest.mark.parametrize("measured,words", [(True, "µm by the measured scale"),
                                            (False, "µm by the scale estimated from the objective")])
def test_the_scale_is_said_as_measured_or_estimated(measured, words):
    """M16: µm only with a scale, said as measured or estimated."""
    from orchestrator.scale import Reading

    reading = Reading(um_per_px=0.5, field_um=600.0, objective=40, measured=measured)
    hub = MotionHub(moving_slide(), disc=False, base=(500.0, -300.0), reading=reading)
    assert tool(hub, {"show": False}).ok
    item = tracks_of(hub).latest()
    assert item.unit == "µm/s" and words in item.words() and item.scale["basis"] == ("measured" if measured else "estimated")
    trail_hub = MotionHub(reading=reading)
    trail_hub.add(textured(3)[:H, :W], 0, 0)
    obj = follow(trail_hub, [(100 + 10 * k, 100, "seen", 0) for k in range(4)])
    assert tool(trail_hub, {"of": obj.id, "show": False}).ok
    kept = tracks_of(trail_hub).latest()
    assert words in kept.words() and ("about " in kept.words()) == (not measured)


def test_segments_of_an_earlier_view_are_never_given_todays_scale_nor_drawn_on_this_view():
    """Note 3 and M18: a segment of another epoch stays in pixels (today's reading is not put on it), and the
    captured view draws only its own epoch's segments."""
    from orchestrator.motiontracks import draw_followed
    from orchestrator.scale import Reading

    reading = Reading(um_per_px=0.5, field_um=600.0, objective=40, measured=True)
    hub = MotionHub(reading=reading)
    hub.add(textured(3)[:H, :W], 0, 0, epoch=1)
    steps = [(100, 100, "seen", 0), (140, 100, "seen", 0), (180, 100, "seen", 0), (180, 100, "lost", 0),
             (400, 400, "seen", 1), (440, 400, "seen", 1), (480, 400, "seen", 1)]
    obj = follow(hub, steps)
    assert tool(hub, {"of": obj.id, "show": False}).ok
    item = tracks_of(hub).latest()
    assert item.unit == "px/s" and item.scale["umPerPx"] is None and "earlier view" in item.words()
    drawn = draw_followed(np.zeros((H, W, 3), np.uint8), item)
    assert drawn[100, 140].sum() == 0  # the earlier epoch's segment: not on this view
    assert drawn[400, 440].sum() > 0


def test_a_view_with_no_run_recorded_is_never_taken_for_this_runs():
    """Note 1: can't tell, so not verified (ADR 0012), as Captures.read_back reads a missing run."""
    hub = MotionHub()
    hub.add(view_of(0, 0), 0, 0)
    item = kept_on(hub)
    item.view.pop("run")
    layer = TrackLayer(hub, hub.broadcast)
    layer.show(item)
    run(layer.check())
    assert not layer.verified() and "restarted" in layer.placement["why"]


def test_the_watchers_check_reserves_what_it_decodes(monkeypatch):
    """Note 5: grey frames only (no colour picture drawn) reserve a hold's room, not an outline's."""
    from orchestrator import capture
    from orchestrator.motiontracks import measure_clip

    kinds = []
    real = capture.reserving

    def spy(hub, width, height, kind="look", owner="", cls="analysis"):
        kinds.append(kind)
        return real(hub, width, height, kind, owner, cls)

    monkeypatch.setattr(capture, "reserving", spy)
    hub = MotionHub(moving_slide(1.5), disc=False)
    clip_ = [(hub.source.get(i), hub.infos[i]) for i in hub.infos]
    measure_clip(hub, clip_, None, "", draw=False)
    measure_clip(hub, clip_, None, "", draw=True)
    assert kinds == ["hold", "outline"]


def test_a_shown_items_wire_is_made_once_and_kept_for_a_studio_that_reconnects(monkeypatch):
    """Note 4: the studio data (its points read from disk once saved) is made off the loop before it is shown,
    and kept: a studio reconnecting is sent it without reading the item again."""
    hub = MotionHub()
    hub.add(view_of(0, 0), 0, 0)
    item = kept_on(hub)
    layer = TrackLayer(hub, hub.broadcast)
    wire = tracks_of(hub).wire(item)
    calls = []
    monkeypatch.setattr(TracksStore, "wire", lambda self, it: calls.append(it.id) or {})
    layer.show(item, wire)
    assert calls == [] and layer.item_wire is wire and hub.sent[-2] == json.loads(json.dumps(wire))


def test_the_screen_and_trails_read_the_clock_when_asked_never_at_import(monkeypatch):
    """test_golden patches `time.time` while it first imports `orchestrator.screen` and `orchestrator.trail`; a
    default `clock=time.time` bound then froze every later Screen's clock for the rest of the run (its reports
    never newer than a change, so every wait on the screen timed out: M3.2's tests failed only after
    test_golden). Each now reads the wall clock when asked."""
    import time as clock_module

    from orchestrator.screen import Screen
    from orchestrator.trail import Trails

    screen, trails = Screen(), Trails()
    monkeypatch.setattr(clock_module, "time", lambda: 5.0)
    assert screen.clock() == 5.0 and trails.clock() == 5.0 and Screen().clock() == 5.0
    monkeypatch.undo()
    assert screen.clock() > 1e9 and trails.clock() > 1e9



# --- the partner's re-check of M3.2 (#182 at ac94afc) ----------------------------------------------------------
LOST_THEN = [(100, 100, "seen", 0), (101, 100, "seen", 0), (102, 100, "seen", 0), (102, 100, "lost", 0)] + \
    [(102, 100, "missed", 0)] * 5


def test_a_trail_that_went_somewhere_only_in_a_later_view_is_never_said_to_have_stayed_put():
    """Re-check B1 (P1c): still (2 px) in one view, continuity lost, then 200 px in the next. Each view is gated on
    its own path and its reach from its own first place there; it went somewhere if any view did. "Stayed within"
    only when every view stayed, said per view."""
    hub = MotionHub()
    hub.add(textured(3)[:H, :W], 0, 0, epoch=1)
    obj = follow(hub, LOST_THEN + [(300 + 20 * k, 100, "seen", 1) for k in range(11)])
    r = tool(hub, {"of": obj.id, "show": False})
    item = tracks_of(hub).latest()
    assert item.rows[0]["values"]["moving"] is True and item.summary["wentSomewhere"] is True
    assert "stayed within" not in item.words() and "202 px over the slide" in item.words() and "stayed within" not in r.summary
    assert [(v["epoch"], round(v["pathPx"]), round(v["reachPx"])) for v in item.summary["views"]] == [(0, 2, 2), (1, 200, 200)]
    assert item.summary["reachPx"] == 200  # the farthest from its first place in any one view (not the first view's 2)
    still = MotionHub()
    still.add(textured(3)[:H, :W], 0, 0, epoch=1)
    obj = follow(still, LOST_THEN + [(300 + (3 if k % 2 else 0), 100, "seen", 1) for k in range(11)])
    assert tool(still, {"of": obj.id, "show": False}).ok
    kept = tracks_of(still).latest()
    assert kept.rows[0]["values"]["moving"] is False
    assert ("in each of its 2 views: in its first view it got as far as about 2 px from where it was first seen there; "
            "2 px of path was seen " + BARS + "; in its second view it got as far as about 3 px from where it was first "
            "seen there; 30 px of path was seen") in kept.words()


def test_made_before_a_switch_and_saved_after_it_is_read_back_in_its_own_session_only(tmp_path, monkeypatch):
    """Re-check S1 (P18) re-scoped to sessions (ADR 0017; the plan's P4), tracks and populations: made in t1, a new
    session opened while its save runs, then a restart. Each is filed in its own session's journal under its own t1
    and read back there; the new session never lists it, whatever it reads."""
    import threading

    from orchestrator.day import DayStore
    from orchestrator.evidence import Evidence
    from orchestrator.population import evidence_lines
    from tests.test_evidence import population

    day = DayStore(root=tmp_path / "sessions", day="2026-10-07", clock=lambda: OCT_7)
    hub = MotionHub(moving_slide(), disc=False, base=(500.0, -300.0))
    hub.execution_day = day
    hub.tracks_store = TracksStore(hub, tmp_path / "evidence", day=day)
    hub.evidence = Evidence(hub, tmp_path / "evidence", day=day)
    gate = threading.Event()
    for store_class in (TracksStore, Evidence):
        real = store_class._save

        def held(self, item, loop=None, real=real):
            gate.wait(5)
            return real(self, item, loop)

        monkeypatch.setattr(store_class, "_save", held)
    assert tool(hub, {"show": False}).ok
    item = hub.tracks_store.latest()
    cells = population(hub.evidence, hub.captures.take(hub.latest_info.id))
    cells.generation, cells.session = day.generation, day.sid
    hub.evidence.add(cells)
    hub.evidence.schedule_save(cells)
    assert item.thread == "t1" and cells.thread == "t1"
    day.new()  # while both saves wait
    assert hub.tracks_store.get(item.id) is None and hub.evidence.get(cells.id) is None  # not the new session's
    day.switch_session("2026-10-07")  # back, before they are filed: still its own, listed again
    assert hub.tracks_store.get(item.id) is item and hub.evidence.get(cells.id) is cells
    day.switch_session(f"2026-10-07.2")
    gate.set()
    assert wait_saved(item) == "saved" and wait_saved(cells) == "saved"
    assert hub.tracks_store.get(item.id) is None and hub.evidence.get(cells.id) is None
    for sid, found in (("2026-10-07", True), ("2026-10-07.2", False)):
        again = DayStore(root=tmp_path / "sessions", day=sid, clock=lambda: OCT_7)
        restarted = MotionHub()
        restarted.execution_day = again
        restarted.tracks_store = TracksStore(restarted, tmp_path / "evidence", day=again)
        restarted.evidence = Evidence(restarted, tmp_path / "evidence", day=again)
        if found:
            assert restarted.tracks_store.get(item.id).thread == "t1" and restarted.evidence.get(cells.id).thread == "t1"
            assert restarted.tracks_store.get(item.id).session == restarted.evidence.get(cells.id).session == sid
        else:
            assert restarted.tracks_store.get(item.id) is None and restarted.evidence.get(cells.id) is None
            assert not any(item.id in x or cells.id in x for x in evidence_lines(restarted, again.active_id))
    manifest = json.loads((tmp_path / "evidence" / "2026-10-07" / item.id / "manifest.json").read_text())
    assert (manifest["thread"], manifest["session"]) == ("t1", "2026-10-07")


def test_a_shared_jobs_other_request_keeps_the_measurement_when_the_first_could_not(monkeypatch):
    """Re-check note 1 (P17): the request keeping a shared job's item is cancelled; the other one, which held its
    room while it waited, keeps the measurement itself (never "could not be kept" with nothing)."""
    from orchestrator import motiontracks

    hub = MotionHub(moving_slide())
    clip_ = [(hub.source.get(i), hub.infos[i]) for i in hub.infos]
    real = motiontracks.from_movement

    def slow(*a, **k):
        time.sleep(0.5)
        return real(*a, **k)

    monkeypatch.setattr(motiontracks, "from_movement", slow)

    async def both():
        first = asyncio.ensure_future(keep_measured(hub, clip_, None, "", 4.0))
        second = asyncio.ensure_future(keep_measured(hub, clip_, None, "", 4.0))
        while not tracks_of(hub).making:
            await asyncio.sleep(0.01)
        first.cancel()
        out = await asyncio.gather(first, second, return_exceptions=True)
        await asyncio.sleep(0.8)  # the cancelled one's thread finishes
        return out

    first, second = run(both())
    assert isinstance(first, asyncio.CancelledError)
    item, reused, done = second
    assert list(tracks_of(hub).items) == [item.id] and reused is True  # shared, and said so
    assert hub.captures.memory.reserved["unsaved"] == item.hold.reservation.nbytes  # the cancelled one let its room go


def eyepiece(image, disc=FOV):
    """A camera frame as the phone sends it: black outside the eyepiece."""
    out = image.copy()
    yy, xx = np.mgrid[0:H, 0:W]
    out[np.hypot(xx - disc[0], yy - disc[1]) > disc[2]] = 0
    return out


def test_tracks_the_slide_carries_toward_the_edge_are_checked_by_what_is_still_inside_the_eyepiece():
    """Re-check N11: the region is matched by its pixels inside the eyepiece where they are now too, so the part
    the slide carried into the dark surround is left out, never compared."""
    hub = MotionHub()
    hub.add(eyepiece(view_of(0, 0)), 0, 0)
    obj = follow(hub, [(340 + 4 * k, 360, "seen", 0) for k in range(6)], label="rotifer")
    assert tool(hub, {"of": obj.id, "show": False}).ok
    layer = TrackLayer(hub, hub.broadcast)
    layer.show(tracks_of(hub).latest())
    run(layer.check())
    assert layer.verified(), layer.placement.get("why")
    hub.add(eyepiece(view_of(-50, 0)), -50, 0)  # carried 50 px toward the left edge: part of the region is dark now
    run(layer.check())
    assert layer.verified(), layer.placement.get("why")



# --- the partner's second re-check of M3.2 (#182 at e8fbd1b) ----------------------------------------------------
def keep_steps(steps, reading=None, now_epoch=None):
    """A followed outline's trail from (x, state, epoch) steps 0.2 s apart at y 100, kept: (item, what the tool said)."""
    hub = MotionHub(reading=reading)
    hub.add(textured(3)[:H, :W], 0, 0, epoch=steps[-1][2] if now_epoch is None else now_epoch)
    obj = follow(hub, [(x, 100, st, ep) for x, st, ep in steps])
    r = tool(hub, {"of": obj.id, "show": False})
    assert r.ok, r.summary
    return tracks_of(hub).latest(), r.summary


def lost_at(x, epoch, n=5):
    return [(x, "lost", epoch)] + [(x, "missed", epoch)] * n


@pytest.mark.parametrize("views", [1, 2])
def test_a_thing_found_far_off_after_a_break_is_never_said_to_have_stayed(views):
    """Second re-check B1 (P1j2): still at x 100, lost, found again 300 px on and still there, in one view (and then
    a second view where it stayed). It went nowhere it was seen going (not moving, by trail.py's gate), but it was
    found 300 px away after the break: said as that, never as its outline's redraws; the jump is kept per view."""
    steps = [(100, "seen", 0)] * 5 + lost_at(100, 0) + [(400, "seen", 0)] * 5
    if views == 2:
        steps += lost_at(400, 0) + [(600, "seen", 1)] * 5
    item, said = keep_steps(steps)
    first = item.summary["views"][0]
    assert item.rows[0]["values"]["moving"] is False
    assert (first["pathPx"], first["reachPx"], first["acrossBreakPx"]) == (0, 300, 300)
    jumped = "it got as far as about 300 px from where it was first seen"
    assert jumped in item.words() and "its place shifted 300 px across 1 break in following it, unobserved" in item.words()
    assert jumped in said and "none of it was seen as path" in item.words()
    if views == 1:
        assert "redraws" not in item.words()
    else:
        assert ("in each of its 2 views: in its first view it got as far as about 300 px from where it was first seen there; "
                "none of it was seen as path; its place shifted 300 px across 1 break in following it, unobserved") in item.words()
        assert "in its second view its place did not change from where it was first seen there " + BARS in item.words()
    scaled, _ = keep_steps([(100, "seen", 0)] * 5 + lost_at(100, 0) + [(400, "seen", 0)] * 5, reading=ESTIMATED)
    assert "it got as far as about 300 px (about 150 µm) from where it was first seen" in scaled.words()


def test_one_place_seen_is_never_said_to_have_stayed():
    """Second re-check SF1 (P1e, P1f): a view of one place is "seen once there"; a trail of one place says whether
    it moved can't be told, never that it stayed."""
    two, _ = keep_steps([(100 + (2 if k % 2 else 0), "seen", 0) for k in range(5)] + lost_at(100, 0) + [(300, "seen", 1)])
    assert "in its second view it was seen once there" in two.words() and "then 0 px" not in two.words()
    assert two.rows[0]["values"]["moving"] is False and two.summary["views"][1]["points"] == 1
    one, said = keep_steps([(100, "seen", 0)])
    assert "seen once; whether it moved can't be told" in one.words() and "stayed" not in one.words()
    assert one.rows[0]["values"]["moving"] is False and "whether it moved can't be told" in said


def test_a_kept_trails_corrections_are_not_its_reach():
    """Second re-check note (P19): test_trail's back-and-forth with two 15 px re-registrations of its outline (the
    hub's corrections, not its own moves), kept: still "stayed within 4 px", not moving."""
    from orchestrator.trail import Trails
    from tests.test_trail import Clock, outline

    hub = MotionHub()
    hub.add(textured(3)[:H, :W], 0, 0)
    clock = Clock()
    hub.trails = Trails(clock)
    obj = outline(100, 100)
    hub.scene.add(obj)
    x = 100.0
    for k in range(8):
        clock.t += 0.6
        hub.trails.observe(outline(x + (4 if k % 2 else 0), 100), "seen", 0, wrote=True, frame=1)
        if k in (2, 5):  # re-registered 15 px on (not written by the follower): a correction
            clock.t += 0.1
            x += 15.0
            hub.trails.observe(outline(x + (4 if k % 2 else 0), 100), "seen", 0, frame=1)
    assert hub.trails.words(obj.id) is None
    assert tool(hub, {"of": obj.id, "show": False}).ok
    item = tracks_of(hub).latest()
    assert item.rows[0]["values"]["moving"] is False and "it got as far as about 4 px from where it was first seen" in item.words()
    assert "unobserved" not in item.words() and "of path was seen" in item.words()  # corrections are no break
    assert item.summary["views"][0]["reachPx"] == 4 and item.summary["views"][0]["acrossBreakPx"] == 0



# --- the partner's third re-check of M3.2 (#182 at 4361712) -----------------------------------------------------
def test_a_short_move_seen_with_no_break_is_said_as_seen_never_as_a_gap_nor_as_staying():
    """Third re-check B1 (P20): one unbroken segment, 100 -> 105.5 -> 111 (path 11, under SHOWN_PX; reach 11, over
    REACH_PX; no jump). It was seen moving, so never "after a gap; not observed", and never "stayed"; too short to tell
    from its outline's redraws, so not moving by trail.py's gate."""
    item, said = keep_steps([(100, "seen", 0), (105.5, "seen", 0), (111, "seen", 0)])
    v = item.summary["views"][0]
    assert len(item.rows[0]["segments"]) == 1 and v["acrossBreakPx"] == 0 and v["pathPx"] == 11 and v["reachPx"] == 11
    assert "after a gap" not in item.words() and "not observed" not in item.words() and "stayed" not in item.words()
    assert "it got as far as about 11 px from where it was first seen; 11 px of path was seen" in item.words()
    assert "as far as about 11 px" in said and "unobserved" not in item.words() and "redraws" not in item.words()
    assert item.rows[0]["values"]["moving"] is False


def test_single_places_far_apart_are_a_jump_and_close_together_are_seen_several_times():
    """Third re-check B1 (P21): two single places 300 px apart across a loss (points 2, no known time) are not "seen
    once": its place changed by 300 px after a gap. Two single places 4 px apart are "seen 2 times, within 4 px"."""
    far, said = keep_steps([(100, "seen", 0)] + lost_at(100, 0) + [(400, "seen", 0)])
    v = far.summary["views"][0]
    assert (v["points"], v["knownS"], v["acrossBreakPx"]) == (2, 0, 300)
    assert "seen once" not in far.words() and "can't be told" not in far.words()
    assert ("it got as far as about 300 px from where it was first seen; none of it was seen as path; its place shifted "
            "300 px across 1 break in following it, unobserved") in far.words()
    assert far.rows[0]["values"]["moving"] is False
    near, _ = keep_steps([(100, "seen", 0)] + lost_at(100, 0) + [(104, "seen", 0)])
    assert ("it got as far as about 4 px from where it was first seen; none of it was seen as path; its place shifted 4 px "
            "across 1 break in following it, unobserved " + BARS) in near.words() and "seen once" not in near.words()



# --- the partner's fourth re-check of M3.2 (#182 at 3920b3c) ----------------------------------------------------
def test_a_still_thing_re_found_a_few_px_off_after_each_of_several_gaps_stayed():
    """Fourth re-check B1 (P27): a still cell whose follow breaks five times, each re-find 3 px to one side or the
    other: its re-finds' offsets add up to 15 px, but its place is never more than 3 px from where it was first seen.
    It stayed (its outline's redraws), never "found 3 px from where it was first seen, after a gap"."""
    steps = []
    for k in range(6):
        steps += [(100 + (3 if k % 2 else 0), "seen", 0)] * 4 + ([(100, "missed", 0)] if k < 5 else [])
    item, said = keep_steps(steps)
    v = item.summary["views"][0]
    assert (v["reachPx"], v["acrossBreakPx"]) == (3, 15) and item.rows[0]["values"]["moving"] is False
    assert ("it got as far as about 3 px from where it was first seen; none of it was seen as path; its place shifted 15 px "
            "in all across 5 breaks in following it, unobserved " + BARS) in item.words()

    places, _ = keep_steps([(100, "seen", 0)] + lost_at(100, 0) + [(106, "seen", 0)] + lost_at(106, 0) + [(100, "seen", 0)])
    assert ("it got as far as about 6 px from where it was first seen; none of it was seen as path; its place shifted 12 px "
            "in all across 2 breaks in following it, unobserved " + BARS) in places.words()


def test_the_10_px_edges_and_a_distance_just_under_the_12_px_gate_are_said_as_they_are():
    """Fourth re-check, V4 and V9 (P28's boundaries) and the rounding note: exactly 10 px across a break is a jump,
    9 px stayed; exactly 10 px of path and reach with no break is a change of place too short to tell from its
    outline's redraws, never "stayed"; 11.9 px is said as 11.9, never rounded up to the 12 px gate."""
    ten, _ = keep_steps([(100, "seen", 0)] * 4 + lost_at(100, 0) + [(110, "seen", 0)] * 4)
    assert ("it got as far as about 10 px from where it was first seen; none of it was seen as path; its place shifted 10 px "
            "across 1 break in following it, unobserved") in ten.words() and "redraws" not in ten.words()
    nine, _ = keep_steps([(100, "seen", 0)] * 4 + lost_at(100, 0) + [(109, "seen", 0)] * 4)
    assert "it got as far as about 9 px from where it was first seen" in nine.words() and BARS[1:-1] in nine.words()
    seen_ten, _ = keep_steps([(100, "seen", 0), (105, "seen", 0), (110, "seen", 0)])
    assert "it got as far as about 10 px from where it was first seen; 10 px of path was seen" in seen_ten.words()
    assert "redraws" not in seen_ten.words()
    under, _ = keep_steps([(100, "seen", 0), (105.95, "seen", 0), (111.9, "seen", 0)])
    assert "it got as far as about 11.9 px from where it was first seen; 11.9 px of path was seen" in under.words()
    assert under.rows[0]["values"]["moving"] is False


# --- round 6 (the partner's fifth re-check, at a3627a6): the measured facts, never a category ----------------------
def _still(x, e, n=5):
    return [(x, "seen", e)] * n


def _gapped(x, e, n=8):  # unobserved past STALE_S: a gap, not a loss
    return [(x, "missed", e)] * n


def _single(xs, e=0):
    steps = []
    for i, x in enumerate(xs):
        steps.append((x, "seen", e))
        if i < len(xs) - 1:
            steps += lost_at(x, e)
    return steps


def _jitter(n):  # a still stretch re-found n times, 3 px to one side or the other
    steps = []
    for k in range(n):
        steps += _still(100 + (3 if k % 2 else 0), 0, 4) + _gapped(100, 0, 2)
    return steps


SHIFT = "its place shifted {} across {} in following it, unobserved"
FACTS = [  # (probe, steps, moving, what the words say: each number as measured)
    ("P1c", [(100, "seen", 0), (101, "seen", 0), (102, "seen", 0)] + lost_at(102, 0) + [(300 + 20 * k, "seen", 1) for k in range(11)],
     True, "202 px over the slide while its place was known (about 2 of 4 s; lost 1x, found again by its look)"),
    ("P1d", [(100 + (2 if k % 2 else 0), "seen", 0) for k in range(5)] + lost_at(100, 0)
     + [(300 + (3 if k % 2 else 0), "seen", 1) for k in range(5)] + lost_at(300, 1) + [(500 + (1 if k % 2 else 0), "seen", 2) for k in range(5)],
     False, "in each of its 3 views: in its first view it got as far as about 2 px from where it was first seen there; 8 px of "
            "path was seen " + BARS + "; in its second view it got as far as about 3 px from where it was first seen there; 12 px "
            "of path was seen; in its third view it got as far as about 1 px from where it was first seen there; 4 px of path was "
            "seen " + BARS),
    ("P1e", [(100 + (2 if k % 2 else 0), "seen", 0) for k in range(5)] + lost_at(100, 0) + [(300, "seen", 1)],
     False, "in its second view it was seen once there; whether it moved can't be told"),
    ("P1f", [(100, "seen", 0)], False, "seen once; whether it moved can't be told"),
    ("P1j2", _still(100, 0) + lost_at(100, 0) + _still(400, 0),
     False, "it got as far as about 300 px from where it was first seen; none of it was seen as path; "
            + SHIFT.format("300 px", "1 break")),
    ("P13", [(100, "seen", 0)] + [(100 + (4 if k % 2 else 0), "seen", 0) for k in range(8)],
     False, "it got as far as about 4 px from where it was first seen; 28 px of path was seen"),
    ("P10", [(640, "seen", 0)] * 20, False, "its place did not change from where it was first seen " + BARS),
    ("P20", [(100, "seen", 0), (105.5, "seen", 0), (111, "seen", 0)],
     False, "it got as far as about 11 px from where it was first seen; 11 px of path was seen"),
    ("P21", _single([100, 400]),
     False, "it got as far as about 300 px from where it was first seen; none of it was seen as path; "
            + SHIFT.format("300 px", "1 break")),
    ("P24", _still(100, 0) + _gapped(100, 0) + _still(160, 0) + lost_at(160, 0) + _still(300, 1) + _gapped(300, 1) + _still(500, 1)
     + lost_at(500, 1) + _still(700, 2) + _gapped(700, 2) + _still(705, 2),
     False, "in its third view it got as far as about 5 px from where it was first seen there; none of it was seen as path; "
            + SHIFT.format("5 px", "1 break") + " " + BARS),
    ("P25", [(100 + 10 * k, "seen", 0) for k in range(6)] + lost_at(150, 0) + [(450 + 10 * k, "seen", 0) for k in range(3)],
     True, "70 px over the slide while its place was known"),
    ("P27", sum(([*_still(100 + (3 if k % 2 else 0), 0, 4), *(_gapped(100, 0, 2) if k < 5 else [])] for k in range(6)), []),
     False, "it got as far as about 3 px from where it was first seen; none of it was seen as path; "
            + SHIFT.format("15 px in all", "5 breaks") + " " + BARS),
    ("P28 10 px across a break", _still(100, 0) + _gapped(100, 0) + _still(110, 0),
     False, "it got as far as about 10 px from where it was first seen; none of it was seen as path; "
            + SHIFT.format("10 px", "1 break")),
    ("P28 9 px across a break", _still(100, 0) + _gapped(100, 0) + _still(109, 0),
     False, "it got as far as about 9 px from where it was first seen; none of it was seen as path; "
            + SHIFT.format("9 px", "1 break") + " " + BARS),
    ("P28 12 px of path", [(100, "seen", 0), (106, "seen", 0), (112, "seen", 0)], True, "12 px over the slide in 1 s"),
    ("P28 11.9 px of path", [(100, "seen", 0), (106, "seen", 0), (111.9, "seen", 0)],
     False, "it got as far as about 11.9 px from where it was first seen; 11.9 px of path was seen"),
    ("P28 single places 5 px apart", _single([100, 105]),
     False, "it got as far as about 5 px from where it was first seen; none of it was seen as path; "
            + SHIFT.format("5 px", "1 break") + " " + BARS),
    ("P28 single places, the far one between", _single([100, 400, 104]),
     False, "it got as far as about 300 px from where it was first seen, and ended about 4 px from it; none of it was "
            "seen as path; " + SHIFT.format("596 px in all", "2 breaks")),
    ("P29", _jitter(4) + [(100, "seen", 0), (105.5, "seen", 0), (111, "seen", 0)],
     False, "it got as far as about 11 px from where it was first seen; 11 px of path was seen; "
            + SHIFT.format("12 px in all", "4 breaks")),
    ("P30", [(100, "seen", 0), (103, "seen", 0), (106, "seen", 0)] + _gapped(106, 0) + _still(111, 0),
     False, "it got as far as about 11 px from where it was first seen; 6 px of path was seen; " + SHIFT.format("5 px", "1 break")),
    ("P31", [(100, "seen", 0), (105.5, "seen", 0), (111, "seen", 0)] + _gapped(111, 0) + _still(130, 0),
     False, "it got as far as about 30 px from where it was first seen; 11 px of path was seen; " + SHIFT.format("19 px", "1 break")),
]


@pytest.mark.parametrize("probe,steps,moving,facts", FACTS, ids=[f[0] for f in FACTS])
def test_a_kept_trail_says_its_measured_facts(probe, steps, moving, facts):
    """Round 6 (the partner's P1c-P31): each view of a trail that went nowhere is said in its measured numbers (its place
    change, the path it was seen making, its shift across breaks, unobserved) and the gate's bars only when both are under
    them; never a category. P29: an 11 px change seen as path is never "not observed" for the jitter of its re-finds;
    P31: of a 30 px change, the 11 px seen as path are said as seen and the 19 px across the break as unobserved."""
    item, said = keep_steps(steps)
    assert item.rows[0]["values"]["moving"] is moving
    assert facts in item.words(), item.words()
    for word in ("stayed within", "after a gap", "found", "seen 2 times", "it moved about"):
        assert word not in item.words() or (word == "found" and "found again by its look" in item.words())


def test_the_views_record_keeps_the_numbers_the_words_say():
    """The `views` record keeps each view's numbers (reach, path, shift across breaks, the breaks), as the words say them."""
    item, _ = keep_steps([(100, "seen", 0), (105.5, "seen", 0), (111, "seen", 0)] + _gapped(111, 0) + _still(130, 0))
    v = item.summary["views"][0]
    assert (v["reachPx"], v["pathPx"], v["acrossBreakPx"], v["gaps"]) == (30, 11, 19, 1)


@pytest.mark.parametrize("span", [{}, {"from": None, "to": None}, {"from": 100, "to": 99},
                                  {"from": float("nan"), "to": 101.9}])
def test_motion_context_does_not_invent_missing_or_invalid_historical_timing(monkeypatch, span):
    hub = MotionHub()
    hub.add(view_of(0, 0), 0, 0)
    item = kept_on(hub)
    item.span = span
    monkeypatch.setattr("orchestrator.motiontracks.time.time", lambda: 200.0)
    first = lines(hub)[0][1]
    monkeypatch.setattr("orchestrator.motiontracks.time.time", lambda: 900.0)
    second = lines(hub)[0][1]
    assert "measurement interval unknown; age unknown" in first
    assert first == second, "missing historical time must not become the time of the later question"


def test_motion_context_distinguishes_saved_measurement_age_from_current_trails(monkeypatch):
    import copy

    hub = MotionHub()
    hub.add(view_of(0, 0), 0, 0)
    item = kept_on(hub)
    original = copy.deepcopy((item.span, item.summary, item.rows))
    layer = track_layer_of(hub)
    layer.shown = item.id
    # Context projection of an active producer; actual intake/CPU producer is
    # separately exercised by test_current_camera_motion.py.
    layer._overlay_live = SimpleNamespace(work=SimpleNamespace(valid=lambda: True))
    monkeypatch.setattr("orchestrator.motiontracks.time.time", lambda: 111.9)
    first = lines(hub, "new-conversation")[0][1]
    assert "measurement ended 10.0 s ago" in first
    assert "saved measurement unchanged" in first
    assert "current trails do not update these counts or speeds" in first
    assert "spot tracks do not establish organism identity" in first
    monkeypatch.setattr("orchestrator.motiontracks.time.time", lambda: 171.9)
    second = lines(hub, "new-conversation")[0][1]
    assert "measurement ended 70.0 s ago" in second
    assert "1 of 2 small things moved" in second
    assert (item.span, item.summary, item.rows) == original
    monkeypatch.setattr("orchestrator.motiontracks.time.time", lambda: 90.0)
    assert "age unknown (measurement end is ahead of this clock)" in lines(hub)[0][1]
