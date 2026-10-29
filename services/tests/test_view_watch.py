"""The watcher (orchestrator/view_watch.py): events raised from synthetic frame sequences (the hub's per-frame numbers,
as Hub._analyse_batch makes them), the counted population read from a stand-in layer, the delivery rules, and the
partner's side of delivering them (voice/partner.py `happened`)."""
import asyncio
import random
from types import SimpleNamespace

import pytest

from orchestrator import view_watch as W
from orchestrator.fov import FieldOfView

FPS = 30.0
FOV = FieldOfView(640.0, 360.0, 300.0, 1.0)


class Feed:
    """Frames as the hub publishes them: wall time, offset, epoch, steady, sharpness stats and the eyepiece disc."""

    def __init__(self, watch, t=1000.0):
        self.watch, self.t, self.id = watch, t, 0
        self.dx = self.dy = 0.0
        self.epoch, self.focus, self.std, self.disc = 1, 200.0, 40.0, 300.0
        self.latest = None

    def frame(self, *, steady=True, tracked=True):
        self.id += 1
        info = SimpleNamespace(id=self.id, wall_at=self.t, width=1280, height=720, dx=self.dx, dy=self.dy,
                               epoch=self.epoch, steady=steady, tracked=tracked, fov=FOV, disc=self.disc,
                               stats=(self.focus, 120.0, self.std, 0.0))
        self.latest = info
        self.watch.hub.latest_info = info
        self.watch.frame(info)
        self.t += 1 / FPS
        return info

    def still(self, seconds):
        for _ in range(int(seconds * FPS)):
            self.frame()

    def pan(self, ddx, ddy, seconds=1.0):
        n = int(seconds * FPS)
        for _ in range(n):
            self.dx += ddx / n
            self.dy += ddy / n
            self.frame(steady=False)


def watcher(mode="quiet", hub=None, **kw):
    told, logged = [], []

    def deliver(event, rule):
        told.append((event, rule))
        return "voice"

    hub = hub or SimpleNamespace(latest_info=None, layer=None, view_held=False)
    watch = W.ViewWatch(hub, deliver=deliver, mode=lambda: mode, log=logged.append, **kw)
    return watch, told, logged


def kinds(told):
    return [e.kind for e, _ in told]


# --- slide moved ---------------------------------------------------------------------------------------------------
def test_a_still_view_raises_nothing_and_a_move_to_a_new_field_is_one_event_when_it_settles():
    watch, told, logged = watcher("chatty")
    feed = Feed(watch)
    feed.still(5)
    assert told == []  # the first field the watcher sees is where it starts, not a move
    feed.pan(-260, -90)  # most of the eyepiece's radius (300 px)
    assert told == []  # nothing while the slide moves
    stopped = feed.t
    feed.still(W.SETTLE_S - 0.1)
    assert told == []
    feed.still(2)
    assert kinds(told) == ["slide-moved"]
    event, rule = told[0]
    assert rule == "idle"
    assert stopped + W.SETTLE_S - 0.05 <= event.at <= stopped + W.SETTLE_S + 0.1  # eventT: it settled
    assert event.since == pytest.approx(stopped, abs=0.05)
    assert event.facts["moved"]["fieldsOfView"] == pytest.approx(275 / 600, abs=0.02)
    assert event.facts["contentMoved"] == "toward the left"
    assert event.point == ["frame"] and event.marks()[0]["target"] == "frame" and event.frame_ref()["id"] == event.frame
    record = logged[-1]
    assert record["kind"] == "watch-event" and record["eventT"] == round(event.at, 3) and record["to"] == "voice"


def test_a_nudge_is_the_same_field_and_small_nudges_that_add_up_are_a_move():
    watch, told, _ = watcher("chatty")
    feed = Feed(watch)
    feed.still(2)
    feed.pan(40, 0)
    feed.still(2)
    assert told == []
    for _ in range(3):  # 160 px in all from where it settled: past a third of the radius
        feed.pan(40, 0)
        feed.still(2)
    assert kinds(told) == ["slide-moved"]


def test_continuity_lost_is_a_move_once_settled_and_a_rejoin_of_the_same_place_is_not():
    watch, told, _ = watcher("chatty")
    feed = Feed(watch)
    feed.still(2)
    feed.frame(steady=False, tracked=False)
    feed.epoch = 2  # a cut: a new epoch
    feed.still(W.SETTLE_S + 0.2)
    assert told == []  # the tracker is given time to rejoin a place seen before
    feed.epoch = 1  # it rejoined the place it left, still standing there
    feed.still(3)
    assert told == []
    feed.frame(steady=False, tracked=False)
    feed.epoch = 3  # another cut, and no rejoin
    feed.still(W.CUT_SETTLE_S + 0.5)
    assert kinds(told) == ["slide-moved"] and "lost" in told[0][0].facts["continuity"]
    feed.still(W.GAP_S["slide-moved"])
    feed.epoch = 1  # later, while standing still, the tracker names it the old place: not a move
    feed.still(3)
    assert kinds(told) == ["slide-moved"]


# --- focus drifted -------------------------------------------------------------------------------------------------
def test_focus_drifting_on_a_still_field_is_one_event_with_the_numbers_and_said_again_only_after_it_recovered():
    watch, told, _ = watcher("quiet")
    feed = Feed(watch)
    feed.still(3)
    for step in range(30):  # drifting slowly: 200 -> 80
        feed.focus = 200 - 4 * step
        feed.still(0.1)
    began = None
    for _ in range(int((W.DRIFT_HOLD_S + 1) * FPS)):
        feed.frame()
        if began is None and watch._low_since is not None:
            began = watch._low_since
    assert kinds(told) == ["focus-drifted"]
    event, rule = told[0]
    assert rule == "idle"
    assert event.since == began and event.at - event.since == pytest.approx(W.DRIFT_HOLD_S, abs=0.1)
    assert event.facts["sharpness"].startswith("4") and "phoneLens" not in event.facts
    feed.still(W.GAP_S["focus-drifted"] + 5)
    assert kinds(told) == ["focus-drifted"]  # still blurred: not said again
    feed.focus = 200.0
    feed.still(2)
    feed.focus = 70.0
    feed.still(W.DRIFT_HOLD_S + 1.5)
    assert kinds(told) == ["focus-drifted", "focus-drifted"]


def test_light_changing_is_not_focus_and_the_knob_resets_the_fields_sharpest():
    sweeping = {"by": None}
    watch, told, _ = watcher("quiet", sweep=lambda: sweeping["by"])
    feed = Feed(watch)
    feed.still(3)
    feed.focus, feed.std = 200 * 0.4, 40 * 0.4 ** 0.5  # dimmer: sharpness and contrast squared fall together
    feed.still(W.DRIFT_HOLD_S + 2)
    assert told == []
    feed.focus, feed.std = 200.0, 40.0
    feed.still(2)
    sweeping["by"] = "knob"  # the user turns the knob to another plane and leaves it there
    feed.focus = 60.0
    feed.still(2)
    sweeping["by"] = None
    feed.still(W.DRIFT_HOLD_S + 2)
    assert told == []  # the user's own focus is the field's new sharpest


def test_the_phones_lens_changing_focus_is_said_as_the_phones():
    watch, told, _ = watcher("quiet", sweep=lambda: None)
    feed = Feed(watch)
    feed.still(3)
    feed.focus, feed.disc = 70.0, 303.0  # 1% bigger disc: focus breathing of the phone's lens
    feed.still(W.DRIFT_HOLD_S + 1.5)
    assert kinds(told) == ["focus-drifted"] and "phone" in told[0][0].facts["phoneLens"]


def test_a_blank_field_says_nothing_about_focus():
    watch, told, _ = watcher("quiet")
    feed = Feed(watch)
    feed.focus = 8.0
    feed.still(3)
    feed.focus = 2.0
    feed.still(W.DRIFT_HOLD_S + 2)
    assert told == []


def test_a_long_steady_recording_with_the_scopes_own_breathing_raises_nothing():
    """False alarms: an hour of a still view with the hand-held scope's sharpness swings (up to 29% within a second,
    the coach's June 14 numbers), a vibration now and then, light flicker and the tracker's sub-pixel noise."""
    rng = random.Random(7)
    watch, told, logged = watcher("chatty")
    feed = Feed(watch)
    for _ in range(int(3600 * FPS / 3)):  # 10 fps for an hour is enough frames to be sure, at 3x the step
        feed.focus = 200.0 * (1 + rng.uniform(-0.15, 0.15))
        feed.std = 40.0 * (1 + rng.uniform(-0.05, 0.05))
        feed.dx, feed.dy = rng.gauss(0, 0.8), rng.gauss(0, 0.8)
        feed.disc = 300.0 + rng.gauss(0, 0.2)
        feed.frame(steady=rng.random() > 0.01)
        feed.t += 2 / FPS
    assert told == [] and not [r for r in logged if r["kind"] == "watch-event"]


# --- a counted population ------------------------------------------------------------------------------------------
class Layer:
    """population.Layer as the watcher reads it: the shown population, each member's last check and the view."""

    def __init__(self, members=30, at=(640.0, 360.0)):
        self.shown = "e1"
        self.view, self.correction, self.why = (1, 0.0, 0.0), (0.0, 0.0), None
        ms = []
        for i in range(members):
            x = at[0] - 150 + (i % 6) * 60
            y = at[1] - 150 + (i // 6) * 60
            ms.append(SimpleNamespace(id=f"m{i + 1}", box=(x - 10, y - 10, x + 10, y + 10)))
        self.pop = SimpleNamespace(id="e1", revision=0, view={"epoch": 1, "dx": 0.0, "dy": 0.0, "at": 999.0},
                                   made_at=999.0, scale_id={"umPerPx": 0.5}, live=lambda: ms,
                                   value=lambda mid, col: 400.0 if int(mid[1:]) <= 20 else 100.0)
        self.placement = {m.id: {"state": "verified"} for m in ms}

    def population(self):
        return self.pop

    def drawn_frame_reason(self, info):
        return self.why

    def states(self):
        return {m: p["state"] for m, p in self.placement.items()}

    def lose(self, ids, frame=1):
        for mid in ids:
            self.placement[mid] = {"state": "unverified", "frame": frame, "why": "its pixels did not match"}


def test_members_no_longer_where_they_were_counted_on_a_still_view_are_one_event_with_counts_sizes_and_where():
    layer = Layer()
    hub = SimpleNamespace(latest_info=None, layer=layer, view_held=False)
    watch, told, _ = watcher("quiet", hub=hub)
    feed = Feed(watch)
    feed.still(3)
    layer.lose(["m1", "m2"])  # two flicker: under the bar
    feed.still(5)
    assert told == []
    layer.lose([f"m{i}" for i in range(1, 9)])  # 8 of 30 cells gone from where they were counted
    feed.still(W.POP_HOLD * W.POP_EVERY_S + 1)
    assert kinds(told) == ["population-changed"]
    event, rule = told[0]
    assert rule == "idle"
    f = event.facts
    assert f["population"] == "e1" and f["counted"] == 30 and f["inPlaceNow"] == 22
    assert f["noLongerWhereCounted"]["count"] >= 6 and f["noLongerWhereCounted"]["ids"][0] == "m1"
    assert f["medianArea"]["unit"] == "µm²" and f["medianArea"]["inPlaceBefore"] >= f["medianArea"]["inPlaceNow"]
    assert event.point[0] == "population:e1" and event.region is not None
    assert event.marks()[0]["target"] == "frame" and event.marks()[1]["target"] == "population:e1"
    assert event.at - event.since == pytest.approx((W.POP_HOLD - 1) * W.POP_EVERY_S, abs=0.2)
    feed.still(W.GAP_S["population-changed"] + 5)
    assert kinds(told) == ["population-changed"]  # the new baseline: the same state is no news


def test_members_out_of_the_eyepiece_a_moved_slide_or_a_blur_are_no_change_of_the_population():
    layer = Layer()
    hub = SimpleNamespace(latest_info=None, layer=layer, view_held=False)
    watch, told, _ = watcher("quiet", hub=hub)
    feed = Feed(watch)
    feed.still(3)
    feed.pan(30, 0, 0.3)  # a small move: the layer's checks lapse for those that left; a new baseline
    layer.lose([f"m{i}" for i in range(1, 9)])
    feed.still(5)
    assert told == []
    layer.placement = {m: {"state": "verified"} for m in layer.placement}  # checked again: back is no news by itself
    feed.still(5)
    assert told == []
    feed.focus = 50.0  # out of focus: every check fails, and that is the focus, not the cells
    layer.lose(list(layer.placement))
    feed.still(W.DRIFT_HOLD_S + 2)
    assert kinds(told) == ["focus-drifted"]
    layer.why = "not in place on the live view now (their layer is in another motion epoch)"
    feed.still(5)
    assert kinds(told) == ["focus-drifted"]


# --- delivery ------------------------------------------------------------------------------------------------------
def test_each_kind_has_one_rule_per_setting_off_watches_nothing_and_only_what_i_asked_tells_silently():
    assert set(W.DELIVERY) == {"quiet", "chatty"}
    for rules in W.DELIVERY.values():
        assert set(rules) == set(W.KINDS) and set(rules.values()) <= {"context", "idle", "interrupt"}
    watch, told, logged = watcher("off")
    feed = Feed(watch)
    feed.still(2)
    feed.pan(-300, 0)
    feed.still(3)
    assert told == [] and logged == []
    watch, told, _ = watcher("quiet")
    feed = Feed(watch)
    feed.still(2)
    feed.pan(-300, 0)
    feed.still(3)
    assert [(e.kind, r) for e, r in told] == [("slide-moved", "context")]
    watch, told, _ = watcher("chatty", quiet=lambda: True)
    feed = Feed(watch)
    feed.still(2)
    feed.pan(-300, 0)
    feed.still(3)
    assert [r for _, r in told] == ["context"]
    assert watch.status()["kinds"]["slide-moved"]["delivery"] == "context"


def test_a_kind_is_told_at_most_once_in_its_gap_and_every_event_is_logged():
    watch, told, logged = watcher("chatty")
    feed = Feed(watch)
    feed.still(2)
    for _ in range(3):
        feed.pan(-300, 0)
        feed.still(1.5)
    events = [r for r in logged if r["kind"] == "watch-event"]
    assert len(events) == 3 and len(told) == 1
    assert events[1]["to"].startswith("not told: one slide-moved")


def test_a_division_is_not_available_and_its_interface_interrupts():
    watch, told, _ = watcher("quiet")
    status = watch.status()["kinds"]["cell-divided"]
    assert status["available"] is False and "not available" in status["why"]
    assert watch.status()["kinds"]["slide-moved"]["available"] is True
    feed = Feed(watch)
    feed.still(1)
    event = watch.divided("o12", ["o31", "o32"], feed.latest, by="a future detector", confidence=0.8)
    assert told[-1][1] == "interrupt" and event.point == ["outline:o12", "outline:o31", "outline:o32"]
    assert event.marks() == [{"target": "outline:o12", "style": "box"}]


def test_the_frame_listener_costs_microseconds():
    """It runs on every published frame on the event loop: numbers only. Measured here so a slow change shows."""
    import time

    layer = Layer(200)
    hub = SimpleNamespace(latest_info=None, layer=layer, view_held=False)
    watch, _, _ = watcher("chatty", hub=hub)
    feed = Feed(watch)
    start = time.perf_counter()
    feed.still(60)
    per_frame = (time.perf_counter() - start) / (60 * FPS)
    assert per_frame < 0.0005, f"{per_frame * 1e6:.0f} µs a frame"


# --- the partner's side ----------------------------------------------------------------------------------------------
class Session:
    def __init__(self, speaking=False, busy=False):
        self.closed, self._speaking, self._busy = False, speaking, busy
        self.calls = []

    def speaking(self):
        return self._speaking

    def user_busy(self):
        return self._busy

    async def stop_speaking(self):
        self.calls.append(("stop",))
        self._speaking = False

    async def inject(self, text, *, respond=False, source="app", facts=None):
        self.calls.append(("inject", respond, source))

    async def proactive(self, text, *, event, ref=None, before=None, mark=None):
        if before is not None:
            await before()
        self.calls.append(("proactive", event, ref, mark))


def partner_with(session):
    from orchestrator.voice.partner import Partner

    partner = Partner.__new__(Partner)
    partner.session, partner.drawn = session, []

    async def marks(marks, *, frame=None, until="speech-end", timeout=1.0):
        partner.drawn.append((marks, frame))
        return {}
    partner.marks = marks
    return partner


def test_the_partner_tells_context_silently_says_idle_lines_and_an_interrupt_stops_only_its_own_words():
    from orchestrator.voice.partner import Partner

    async def go():
        quiet = Session()
        p = partner_with(quiet)
        await Partner.happened(p, "slide-moved", "{}", ref="v1", delivery="context")
        assert quiet.calls == [("inject", False, "slide-moved")]
        mark = [{"target": "frame", "region": [0.1, 0.1, 0.2, 0.2], "style": "box"}]
        frame = {"id": 9, "crop": [0, 0, 9, 9]}
        await Partner.happened(p, "population-changed", "{}", ref="v2", marks=mark, frame=frame)
        assert quiet.calls[-1] == ("proactive", "population-changed", "v2", "frame") and p.drawn == [(mark, frame)]
        speaking = Session(speaking=True)
        p = partner_with(speaking)
        await Partner.happened(p, "cell-divided", "{}", ref="v3", delivery="interrupt")
        assert speaking.calls[0] == ("stop",) and speaking.calls[1][0] == "proactive"
        over_user = Session(speaking=True, busy=True)
        p = partner_with(over_user)
        await Partner.happened(p, "cell-divided", "{}", ref="v4", delivery="interrupt")
        assert ("stop",) not in over_user.calls  # never over the user: it waits for quiet like any line
    asyncio.run(go())


def test_a_watcher_event_reaches_the_real_session_said_when_quiet_with_its_mark_or_told_silently(monkeypatch):
    """The app's delivery (app.py watch_told) on the one-brain session over a fake sideband: an idle event is one
    proactive response (no tools), its frame region drawn as it is said; a context event is a system item only."""
    from tests.test_voice_partner import partner_session, until_async

    async def go():
        t = await partner_session(monkeypatch)
        t.session._last_user_at -= 5.0  # nobody has spoken for a while

        def deliver(event, rule):
            asyncio.get_running_loop().create_task(t.partner.happened(
                event.kind, event.text(), ref=event.id, delivery=rule, marks=event.marks(), frame=event.frame_ref()))
            return "voice"

        hub = SimpleNamespace(latest_info=None, layer=None, view_held=False)
        watch = W.ViewWatch(hub, deliver=deliver, mode=lambda: "chatty")
        feed = Feed(watch)
        feed.still(2)
        feed.pan(-300, 0)
        feed.still(1)
        marks = await until_async(lambda: next((m for m in t.studio if m["type"] == "marks"), None))
        assert marks["marks"][0]["target"] == "frame" and marks["frame"]["id"] == watch.events[0].frame
        await until_async(lambda: t.sb.responses == 1)
        response = t.sb.sent[-1]["response"]
        assert response["metadata"]["cause"] == "proactive" and response["tool_choice"] == "none"
        facts = [i for i in t.sb.items() if i.get("role") == "system" and "slide-moved" in i["content"][0]["text"]]
        assert facts and '"eventT"' not in facts[0]["content"][0]["text"]  # the wall clock as hh:mm:ss, the log has eventT
        said = await until_async(lambda: next((r for r in t.logged if r["kind"] == "partner-proactive"), None))
        assert said["ref"] == watch.events[0].id and said["markTarget"] == "frame"
        await t.partner.happened("slide-moved", '{"event": "slide-moved"}', ref="v9", delivery="context")
        await asyncio.sleep(0.05)
        assert t.sb.responses == 1
        assert any(i["content"][0]["text"].startswith("Context only") and '"event": "slide-moved"' in i["content"][0]["text"]
                   for i in t.sb.items() if i.get("role") == "system")
    asyncio.run(go())
