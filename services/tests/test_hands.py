"""Hands (voice/hands.py): the partner's actions in Hooke typed, cued, re-checked, verified by the app and yielding to
the user. The broad journey of 29 October: 7 of 13 failed steps were actions in Hooke unconfirmed or on the wrong
target, 3 more said "done" without the app's confirmation."""
import asyncio
import json

from orchestrator.voice import hands as hands_mod
from orchestrator.voice.hands import Hands, intent_of
from tests.test_voice_partner import done, partner_session, press, release, settle  # noqa: F401
from tests.test_voice_partner import no_tail  # noqa: F401  (autouse)
from tests.waiting import until_async


def report(*, front="main", tabs=("main", "Measurements"), drawn=None, windows=(), selected=(), seq=7, magnifier=None):
    items = [{"id": "workspace-tabs", "open": True, "view": {"legacy": {"workspace": {
        "project": "p", "tab": front, "tabTitle": front.title(),
        "tabs": [{"id": t, "title": t.title(), "front": t == front} for t in tabs]}}}}]
    if drawn is not None:
        items.append({"id": "outlines", "open": True, "view": {"legacy": {"drawn": [{"id": d, "kind": "highlight"}
                                                                                   for d in drawn]}}})
    if magnifier is not None:
        items.append({"id": "magnifier", "open": True, "view": {"legacy": {"shows": magnifier, "level": 2}}})
    items += [{"id": w, "open": True, "view": {"legacy": {}}} for w in windows]
    return {"seq": seq, "items": items, "interaction": {"selected": list(selected)}}


class Studio:
    """The studio's half: answers each cue, records what it was sent."""

    def __init__(self, hands_ref, *, answer=True):
        self.sent, self.answer, self.hands = [], answer, hands_ref

    async def send(self, message):
        self.sent.append(message)
        if message["type"] == "drive" and self.answer:
            asyncio.get_running_loop().call_soon(self.hands[0].shown, {"type": "drive-shown", "id": message["id"],
                                                                     "ok": True, "rect": [1, 2, 3, 4]})

    def types(self):
        return [m["type"] for m in self.sent]


def make(screen, **kw):
    ref = []
    studio = Studio(ref, **kw)
    logged = []
    hands = Hands(send=studio.send, observe=lambda: screen["now"], log=logged.append)
    ref.append(hands)
    return hands, studio, logged


def test_an_action_is_cued_under_the_partners_control_then_verified_from_the_report():
    async def go():
        screen = {"now": report(front="main")}
        hands, studio, logged = make(screen)
        ran = []

        async def effect():
            ran.append(studio.types()[:])
            screen["now"] = report(front="Measurements", seq=8)
            return {"ok": True, "summary": "switched to Measurements"}
        out = await hands.act("tabs", {"action": "switch", "tab": "Measurements"}, effect)
        assert out["result"] == "verified"
        assert ran[0][:1] == ["control"], "the partner's control comes before the effect"
        assert "drive" in studio.types(), "the cue runs alongside the action, never delaying it"
        control, drive = studio.sent[0], studio.sent[1]
        assert control == {"type": "control", "version": 1, "owner": "partner"}
        assert drive["version"] == 1 and drive["intent"] == {"kind": "focus", "target": "tab:Measurements",
                                                             "caption": "Switching Tabs"}
        assert studio.sent[2] == {"type": "drive-end", "id": drive["id"], "outcome": "verified"}
        assert studio.sent[3] == {"type": "control", "version": 2, "owner": "user"}
        record = next(r for r in logged if r["kind"] == "partner-hands")
        assert record["result"] == "verified" and record["revision"] == 7 and record["intent"] == "focus"
    asyncio.run(go())


def test_the_tools_own_success_is_not_done_when_the_report_does_not_show_it():
    """Zero false "done" by construction: the report decides, not the tool's words or the model's."""
    async def go():
        screen = {"now": report(front="main")}
        hands, studio, _ = make(screen)

        async def effect():
            return {"ok": True, "summary": "switched"}  # the studio still shows main in front
        out = await hands.act("tabs", {"action": "switch", "tab": "Measurements"}, effect)
        assert out["result"] == "failed" and "does not show it" in out["error"]
        assert studio.sent[-2]["outcome"] == "failed"
        failed = await hands.act("highlight", {"things": ["o3"]}, lambda: asyncio.sleep(0, {"ok": False,
                                                                                          "summary": "no o3"}))
        assert failed["result"] == "failed"
        screen["now"] = report(drawn=["o3"])
        shown = await hands.act("highlight", {"things": ["o3"]}, lambda: asyncio.sleep(0, {"ok": True}))
        assert shown["result"] == "verified"
        screen["now"] = report(drawn=[])
        unseen = await hands.act("highlight", {"things": ["o3"]}, lambda: asyncio.sleep(0, {"ok": True}))
        assert unseen["result"] == "failed"
    asyncio.run(go())


def test_a_report_that_cannot_show_it_leaves_it_uncertain_unless_the_tool_checks_the_report_itself():
    async def go():
        screen = {"now": report()}  # no outlines item: an older studio
        hands, _, _ = make(screen)
        out = await hands.act("highlight", {"things": ["o3"]}, lambda: asyncio.sleep(0, {"ok": True}))
        assert out["result"] == "uncertain"
        confirmed = await hands.act("highlight", {"things": ["o3"]},
                                    lambda: asyncio.sleep(0, {"ok": True, "_log": {"confirmed": True}}))
        assert confirmed["result"] == "uncertain", "a report newer than the change is not the state asked for"
        opened = await hands.act("show_measurements", {"of": "e2"}, lambda: asyncio.sleep(0, {"ok": True}))
        assert opened["result"] == "uncertain", "no shortcut: the tool's ok is not the table on screen"
        screen["now"] = report(front="main")
        screen["now"]["items"].append({"id": "w-table", "open": True, "view": {"legacy": {"document": {
            "project": "p", "tab": "main", "placement": "p-1", "resource": {"id": "e2"}}}}})
        shown = await hands.act("show_measurements", {"of": "e2"}, lambda: asyncio.sleep(0, {"ok": True}))
        assert shown["result"] == "verified", "the table exists, on the Tab in front, showing e2"
        elsewhere = await hands.act("show_measurements", {"of": "e2", "tab": "Measurements"},
                                    lambda: asyncio.sleep(0, {"ok": True}))
        assert elsewhere["result"] == "failed", "asked for in another Tab, which is not in front"
        running = await hands.act("show_measurements", {"of": "e2"},
                                  lambda: asyncio.sleep(0, {"ok": None, "still_working": True}))
        assert running["result"] == "running"
    asyncio.run(go())


def test_takeover_mid_effect_is_uncertain_and_nothing_queued_runs_after():
    """Mouse, Escape or the talk key: the version rises, the one cancel signal stops the running intent within the
    drain, and an intent queued behind it never runs (HeyClicky's Windows fix: a drive that kept typing)."""
    async def go():
        hands_mod.RECONCILE_S = 1.0
        screen = {"now": report()}
        hands, studio, logged = make(screen)
        gate, ran = asyncio.Event(), []

        async def slow():
            ran.append("first")
            await gate.wait()
            return {"ok": True}

        async def second():
            ran.append("second")
            return {"ok": True}
        lock = asyncio.Lock()

        async def locked(effect):
            async with lock:
                return await effect()
        first = asyncio.ensure_future(hands.act("present", {"do": "close", "what": "w-1"}, lambda: locked(slow)))
        await until_async(lambda: ran)
        queued = asyncio.ensure_future(hands.act("tabs", {"action": "switch", "tab": "main"}, lambda: locked(second)))
        await asyncio.sleep(0.05)
        await hands.takeover({"type": "takeover", "version": hands.version, "why": "mouse", "at": 1})
        out = await asyncio.wait_for(first, 2)
        assert out["result"] == "uncertain"
        gate.set()
        later = await asyncio.wait_for(queued, 2)
        assert later["result"] == "failed" and "took over" in later["error"]
        assert ran == ["first"], "nothing queued ran after the takeover"
        ack = next(m for m in studio.sent if m.get("ack"))
        assert ack["owner"] == "user" and ack["version"] >= 2 and isinstance(ack["at"], float)
        assert any(r["kind"] == "partner-takeover" and r["why"] == "mouse" for r in logged)
        hands_mod.RECONCILE_S = 30.0
    asyncio.run(go())


def test_think_does_not_act_again_after_a_takeover_until_a_fresh_look():
    async def go():
        screen = {"now": report()}
        hands, _, _ = make(screen)
        hands.owner = "partner"
        await hands.takeover({"type": "takeover", "version": hands.version, "why": "escape", "at": 1})
        out = await hands.act("tabs", {"action": "switch", "tab": "main"}, lambda: asyncio.sleep(0, {"ok": True}),
                              from_think=True)
        assert out["result"] == "failed" and "look at the screen again" in out["error"]
        hands.fresh_look()
        again = await hands.act("tabs", {"action": "switch", "tab": "main"}, lambda: asyncio.sleep(0, {"ok": True}),
                                from_think=True)
        assert again["result"] == "verified"
    asyncio.run(go())


def test_a_target_nothing_knows_is_filled_from_one_candidate_and_asked_about_with_two():
    """ "Close that": the window it pointed at last, when that is the one candidate; two left, one short question."""
    async def go():
        screen = {"now": report(windows=["w-photo"])}
        hands, _, _ = make(screen)
        hands.point(["window:w-photo"])
        closed = []

        async def effect():
            closed.append(True)
            screen["now"] = report()
            return {"ok": True}
        out = await hands.act("present", {"do": "close", "what": "w-gone"}, effect)
        assert out["result"] == "verified" and out["_hands"]["target"] == "window:w-photo" and closed
        screen["now"] = report(windows=["w-a", "w-b"])
        ask = await hands.act("present", {"do": "close", "what": "w-gone"}, lambda: asyncio.sleep(0, {"ok": True}))
        assert ask["result"] == "ask" and set(ask["candidates"]) == {"w-a", "w-b"}
    asyncio.run(go())


def test_spending_asks_once_and_runs_on_the_approval():
    async def go():
        screen = {"now": report()}
        hands, _, _ = make(screen)
        hands.spends = lambda intent: "SAM 3.1 runs in the cloud and is paid per use"
        ran = []

        async def effect():
            ran.append(True)
            screen["now"]["items"].append({"id": "layer:e5", "open": True,
                                           "view": {"legacy": {"members": {"count": 12, "drawn": 12}}}})
            return {"ok": True}
        asked = await hands.act("outline_cells", {}, effect)
        assert asked["result"] == "permission" and "paid per use" in asked["ask"] and not ran
        out = await hands.act("outline_cells", {}, effect, approved=asked["approval"])
        assert out["result"] == "verified" and ran == [True] and out["said"] == "Done: 12 outlined."
        again = await hands.act("outline_cells", {}, effect, approved=asked["approval"])
        assert again["result"] == "permission", "an approval is used once"
    asyncio.run(go())


def test_a_cue_the_studio_does_not_answer_never_blocks_the_action():
    async def go():
        hands_mod.CUE_WAIT_S = 0.05
        screen = {"now": report(selected=["o3"])}
        hands, studio, logged = make(screen, answer=False)
        out = await hands.act("select", {"object": "o3"}, lambda: asyncio.sleep(0, {"ok": True}))
        assert out["result"] == "verified"
        drive = await until_async(lambda: next((r for r in logged if r["kind"] == "partner-drive"), None))
        assert drive["shownT"] is None
        hands_mod.CUE_WAIT_S = 0.5
    asyncio.run(go())


def test_intents_are_typed_from_the_tools_the_voice_already_picks():
    assert intent_of("present", {"do": "open", "what": "e2"}).kind == "open"
    assert intent_of("present", {"do": "close", "what": {"instanceId": "w-1"}}).target == "window:w-1"
    assert intent_of("tabs", {"action": "switch", "tab": "main"}).kind == "focus"
    assert intent_of("tabs", {"action": "list"}) is None
    assert intent_of("show_measurements", {"of": "e2", "tab": "Sizes"}).target == "tab:Sizes"
    assert intent_of("zoom", {"object": "o4"}).ids == ["o4"]
    assert intent_of("outline_cells", {}).kind == "outline"
    researcher = intent_of("start_researcher", {})
    assert (researcher.kind, researcher.ui, researcher.check) == ("research", False, "record")
    assert intent_of("count", {}, kind="query") is None, "a look has no receipt: its numbers are its answer"
    assert intent_of("make_table", {}, produces="dataset-table").check == "record"
    assert intent_of("new_project", {}).ui and intent_of("new_project", {}).check == "record"
    assert intent_of("add_asset", {"asset": "a7"}).kind == "open"


def test_the_partner_routes_its_screen_changes_through_hands_and_the_studio_answers_on_the_voice_socket(monkeypatch):
    async def go():
        t = await partner_session(monkeypatch, tools={"tabs": "switched to Measurements"})
        assert t.partner.tools.hands is not None
        spec = next(s for s in t.session.config.tools if s.name == "tabs")
        assert "approved" in spec.parameters["properties"]
        call = asyncio.ensure_future(spec.handler({"action": "switch", "tab": "Measurements"}))
        drive = await until_async(lambda: next((m for m in t.studio if m["type"] == "drive"), None))
        await t.partner.command({"type": "drive-shown", "id": drive["id"], "ok": True})
        out = await asyncio.wait_for(call, 20)  # its look after waits for a picture nobody sends here
        assert out["result"] in ("verified", "uncertain") and t.ran and t.ran[0][0] == "tabs"
        assert [m["type"] for m in t.studio if m["type"] in ("control", "drive", "drive-end")][:3] == \
            ["control", "drive", "drive-end"]
        t.partner.tools.hands.owner = "partner"
        await t.partner.command({"type": "takeover", "version": t.partner.tools.hands.version, "why": "talk", "at": 5})
        assert any(m.get("ack") for m in t.studio if m["type"] == "control")
    asyncio.run(go())


def test_a_retried_action_gets_its_receipt_not_a_second_effect_and_a_cut_off_one_is_reconciled_later():
    """The sharpened spec (hooke-web #78): an action id makes retries safe; a takeover gives control at once and the
    cut-off action is reconciled in the background, its real receipt in the log when it finishes."""
    async def go():
        screen = {"now": report(front="main")}
        hands, _, logged = make(screen)
        ran = []

        async def effect():
            ran.append(True)
            screen["now"] = report(front="Measurements")
            return {"ok": True}
        first = await hands.act("tabs", {"action": "switch", "tab": "Measurements"}, effect, action_id="call_1")
        again = await hands.act("tabs", {"action": "switch", "tab": "Measurements"}, effect, action_id="call_1")
        assert first["result"] == again["result"] == "verified" and ran == [True]
        assert first["said"] == "Done: Measurements is in front (it was Main, for \"go back\")."
        gate = asyncio.Event()

        async def slow():
            await gate.wait()
            screen["now"] = report(front="main")
            return {"ok": True}
        cut = asyncio.ensure_future(hands.act("tabs", {"action": "switch", "tab": "main"}, slow))
        await asyncio.sleep(0.05)
        await hands.takeover({"type": "takeover", "version": hands.version, "why": "escape", "at": 1})
        out = await asyncio.wait_for(cut, 0.5)
        assert out["result"] == "uncertain", "control is the user's at once"
        gate.set()
        reconciled = await until_async(lambda: next((r for r in logged if r["kind"] == "partner-hands"
                                                     and r.get("reconciled")), None))
        assert reconciled["result"] == "verified"
    asyncio.run(go())


# --- every acting tool, talk and takeover kept apart, Hooke's own records (spec 4.2) -----------------------------------
from types import SimpleNamespace  # noqa: E402

from orchestrator.voice.records import Records  # noqa: E402


def researcher(rid="r3", n=3, status="running", **more):
    return SimpleNamespace(rid=rid, n=n, name=f"Researcher {n}", status=status, notes=[], pending=[], history=[],
                           run=None, presentation_jobs=[], origin={"channel": "voice"}, sitting="p", **more)


class Manager:
    def __init__(self, *sessions):
        self.sessions = {s.rid: s for s in sessions}

    def get(self, rid):
        return self.sessions.get(rid)


class Desk:
    def __init__(self, manager):
        self.manager = manager

    def find(self, ref):
        return next(s for s in self.manager.sessions.values() if str(ref) in (s.rid, str(s.n)))


def with_records(screen, *, manager=None, settings=None, project=lambda: "p", **kw):
    hands, studio, logged = make(screen, **kw)
    services = SimpleNamespace(research=manager, desk=Desk(manager) if manager else None)
    hub = SimpleNamespace(settings=settings, resources=None)
    hands.records = Records(hub, services=services)
    hands.records._project = project
    hands.project = project
    return hands, studio, logged


def test_talking_over_it_is_a_pause_what_is_being_done_finishes_and_what_was_queued_is_not_run():
    """The talk key: nothing queued runs, its receipt says the user spoke (so the voice plans again from the new words),
    and the action already being done finishes and its receipt is said."""
    async def go():
        screen = {"now": report(front="main")}
        hands, studio, _ = make(screen)
        gate, ran = asyncio.Event(), []

        async def slow():
            ran.append("first")
            await gate.wait()
            screen["now"] = report(front="Measurements")
            return {"ok": True}

        async def second():
            ran.append("second")
            return {"ok": True}
        first = asyncio.ensure_future(hands.act("tabs", {"action": "switch", "tab": "Measurements"}, slow))
        await until_async(lambda: ran)
        queued = asyncio.ensure_future(hands.act("tabs", {"action": "switch", "tab": "main"}, second))
        await asyncio.sleep(0.02)
        await hands.takeover({"type": "takeover", "version": hands.version, "why": "talk", "at": 1})
        assert not hands.cancel.is_set(), "a pause sends no cancel"
        gate.set()
        finished = await asyncio.wait_for(first, 2)
        assert finished["result"] == "verified", "what was being done finished, and its receipt stands"
        later = await asyncio.wait_for(queued, 2)
        assert later["result"] == "failed" and later["paused"] and "started speaking" in later["error"]
        assert ran == ["first"]
        hands.fresh_look()  # the next turn's key-down pictures
        again = await hands.act("tabs", {"action": "switch", "tab": "main"},
                                lambda: asyncio.sleep(0, {"ok": True}))
        assert again["result"] in ("failed", "verified") and again.get("error") != hands_mod.LOOK_AGAIN
    asyncio.run(go())


def test_the_mouse_stops_what_is_being_done_where_its_tool_can_stop_and_its_real_receipt_comes_after():
    async def go():
        hands_mod.RECONCILE_S = 1.0
        screen = {"now": report(front="main")}
        hands, _, logged = make(screen)
        told, stop = [], asyncio.Event()

        async def effect():
            await stop.wait()  # the tool run's Stop (user_stop.Stop.stop) ends it
            return {"ok": False, "stopped": True, "summary": "stopped: you pressed Stop"}

        async def reconciled(intent, receipt):
            told.append(receipt)
        hands.reconciled = reconciled
        cut = asyncio.ensure_future(hands.act("present", {"do": "open", "what": "w-1"}, effect, stop=stop.set))
        await asyncio.sleep(0.02)
        await hands.takeover({"type": "takeover", "version": hands.version, "why": "mouse", "at": 1})
        out = await asyncio.wait_for(cut, 0.5)
        assert out["result"] == "failed" and "stopped when you took over" in out["error"], \
            "stopped within the drain: its receipt is that it was stopped, never done"
        # A tool that cannot stop: uncertain at once, and its real receipt when it ends (reconciled in the background).
        hands.fresh_look()
        gate = asyncio.Event()

        async def unstoppable():
            await gate.wait()
            screen["now"] = report(front="main", windows=["w-1"])
            return {"ok": True}
        cut = asyncio.ensure_future(hands.act("present", {"do": "open", "what": "w-1"}, unstoppable))
        await asyncio.sleep(0.02)
        await hands.takeover({"type": "takeover", "version": hands.version, "why": "escape", "at": 2})
        out = await asyncio.wait_for(cut, 0.5)
        assert out["result"] == "uncertain", "control is the user's at once"
        gate.set()
        await until_async(lambda: told)
        assert told[0]["result"] == "verified", "what really happened is told after"
        refused = await hands.act("present", {"do": "open", "what": "w-1"}, lambda: asyncio.sleep(0, {"ok": True}))
        assert refused["result"] == "failed" and refused["error"] == hands_mod.LOOK_AGAIN, \
            "the voice too acts on the UI again only after a fresh look"
        assert any(r["kind"] == "partner-takeover" and r["stopped"] for r in logged)
        hands_mod.RECONCILE_S = 30.0
    asyncio.run(go())


def test_the_precondition_is_what_the_action_was_planned_against_not_the_screen_at_call_time():
    async def go():
        screen = {"now": report(front="main")}
        where = {"p": "A"}
        hands, _, _ = make(screen)
        hands.project = lambda: where["p"]
        hands.turn = lambda: 4
        hands.fresh_look()  # turn 4's key-down: Project A
        plan = hands.plan(4)
        where["p"] = "B"  # the user opened another Project by hand while the key was held
        out = await hands.act("tabs", {"action": "switch", "tab": "main"}, lambda: asyncio.sleep(0, {"ok": True}),
                              plan=plan)
        assert out["result"] == "failed" and "another Project" in out["error"]
        hands.fresh_look()
        old = hands.plan(3)  # planned in turn 3; the user has spoken since
        spoke = await hands.act("tabs", {"action": "switch", "tab": "main"}, lambda: asyncio.sleep(0, {"ok": True}),
                                plan=old)
        assert spoke["result"] == "failed" and spoke["paused"]
        # The partner's own new Project is what it planned on next ("a new Project, then a Tab in it").
        before = hands.plan(4)
        hands.moved_to("C")
        where["p"] = "C"
        moved = await hands.act("tabs", {"action": "switch", "tab": "main"}, lambda: asyncio.sleep(0, {"ok": True}),
                                plan=before)
        assert moved["result"] == "verified"
    asyncio.run(go())


def test_a_researcher_is_started_without_the_ui_lease_and_its_receipt_is_its_record_running():
    async def go():
        screen = {"now": report()}
        manager = Manager(researcher(status="running"))
        hands, studio, _ = with_records(screen, manager=manager)

        async def effect():
            return {"ok": True, "summary": "Researcher 3 is making a research note", "researcher": "r3"}
        out = await hands.act("start_researcher", {"brief": "look"}, effect, action_id="c1")
        assert out["result"] == "running" and out["said"] == "Researcher 3 is working on it; its finding comes when it is done."
        assert out["researcher"] == "r3" and studio.sent == [], "no lease and no cue for work behind the UI"
        again = await hands.act("start_researcher", {"brief": "look"}, effect, action_id="c1")
        assert again["result"] == "running" and "not done again" in again["note"]
        manager.sessions["r3"].status = "failed"
        failed = await hands.act("start_researcher", {"brief": "look"}, effect)
        assert failed["result"] == "failed" and "Researcher 3" in failed["said"]
        gone = await hands.act("start_researcher", {"brief": "look"}, lambda: asyncio.sleep(0, {"ok": True}))
        assert gone["result"] == "uncertain", "no record of a researcher: never said started"
    asyncio.run(go())


def test_a_cancel_is_said_only_when_the_record_says_cancelled():
    async def go():
        manager = Manager(researcher(status="running"))
        hands, _, _ = with_records({"now": report()}, manager=manager)
        out = await hands.act("cancel_researcher", {"researcher": "3"},
                              lambda: asyncio.sleep(0, {"ok": True, "summary": "stopped Researcher 3"}))
        assert out["result"] == "running" and out["said"].startswith("Cancelling Researcher 3")
        manager.sessions["r3"].status = "cancelled"
        out = await hands.act("cancel_researcher", {"researcher": "3"},
                              lambda: asyncio.sleep(0, {"ok": True, "summary": "stopped Researcher 3"}))
        assert out["result"] == "verified" and out["said"] == "Researcher 3 is stopped."
    asyncio.run(go())


def test_a_note_is_recorded_on_that_researcher():
    async def go():
        s = researcher(status="running")
        hands, _, _ = with_records({"now": report()}, manager=Manager(s))

        async def note():
            s.notes.append({"at": 1, "text": "focus on the middle"})
            return {"ok": True, "summary": "passed the note to Researcher 3"}
        out = await hands.act("researcher_note", {"researcher": "3", "text": "focus on the middle"}, note)
        assert out["result"] == "verified" and "has the note" in out["said"]
        silent = await hands.act("researcher_note", {"researcher": "3", "text": "x"},
                                 lambda: asyncio.sleep(0, {"ok": True}))
        assert silent["result"] == "uncertain"
    asyncio.run(go())


def test_a_setting_reads_back_changed_and_a_project_is_open_and_shown():
    async def go():
        settings = SimpleNamespace(cells="cellpose", to_wire=lambda: {"cells": settings.cells})
        where = {"p": "A"}
        hands, _, _ = with_records({"now": report()}, settings=settings, project=lambda: where["p"])

        async def change():
            settings.cells = "micro-sam"
            return {"ok": True}
        out = await hands.act("set_default", {"operation": "cells", "implementation": "micro-sam"}, change)
        assert out["result"] == "verified" and out["said"].startswith("Done:")
        same = await hands.act("set_default", {"operation": "cells", "implementation": "micro-sam"},
                               lambda: asyncio.sleep(0, {"ok": True}))
        assert same["result"] == "failed" and same["said"].startswith("Nothing changed")
        already = await hands.act("switch_session", {"session": "A"},
                                  lambda: asyncio.sleep(0, {"ok": True, "summary": 'already in the session "A"'}))
        assert already["result"] == "failed" and already["said"] == "Nothing opened: you are already in it."
        several = await hands.act("switch_session", {"session": "leaf"}, lambda: asyncio.sleep(
            0, {"ok": True, "summary": "nothing was opened: these sessions may be meant, ask the user which"}))
        assert several["result"] == "failed" and "ask which" in several["said"]

        async def switch():
            where["p"] = "B"
            return {"ok": True, "summary": 'opened the session "Leaf" (today)'}
        hands.records.shown = lambda: True
        opened = await hands.act("switch_session", {"session": "leaf"}, switch)
        assert opened["result"] == "verified" and opened["said"] == "Done: Leaf is open."
        where["p"] = "A"
        hands.records.shown = lambda: False

        async def landed():
            return False
        hands.records.landed = landed
        hands.fresh_look()
        unshown = await hands.act("switch_session", {"session": "leaf"}, switch)
        assert unshown["result"] == "uncertain" and "not showing it" in unshown["said"]
    asyncio.run(go())


def test_a_use_says_it_applies_to_this_request_only_and_a_capability_job_is_running():
    async def go():
        hands, _, _ = with_records({"now": report()})
        use = await hands.act("use", {"operation": "cells", "implementation": "sam-3.1"},
                              lambda: asyncio.sleep(0, {"ok": True, "summary": "for this request, ..."}))
        assert use["result"] == "verified" and "this request only" in use["said"] and "nothing has been done" in use["said"]
        job = SimpleNamespace(values={"backgroundJob": {"id": "j4", "session": "p"}}, resource=None, outputs=[])
        started = await hands.act("run_capability", {"capability": "denoise"},
                                  lambda: asyncio.sleep(0, {"ok": True, "_run": job}))
        assert started["result"] == "running" and "j4" in started["said"] and "_run" not in started
    asyncio.run(go())


def test_what_an_analysis_saves_resolves_in_hookes_stores_with_its_numbers():
    async def go():
        hands, _, _ = with_records({"now": report()})
        tables = SimpleNamespace(version=lambda tid, v: {"id": tid, "version": v, "title": "Nuclei sizes",
                                                         "rows": [1, 2, 3]})
        hands.records.hub.datatables = tables
        run = SimpleNamespace(resource={"id": "d4", "day": "p", "version": 2, "operation": "make_table"}, values={},
                              outputs=[])
        out = await hands.act("make_table", {}, lambda: asyncio.sleep(0, {"ok": True, "_run": run}),
                              intent=intent_of("make_table", {}, produces="dataset-table"))
        assert out["result"] == "verified" and out["said"] == "Saved: Nuclei sizes (d4, version 2, 3 rows)."
        nothing = await hands.act("make_table", {}, lambda: asyncio.sleep(0, {"ok": True}),
                                  intent=intent_of("make_table", {}, produces="dataset-table"))
        assert nothing["result"] == "uncertain"
    asyncio.run(go())


def test_a_reference_photo_is_checked_in_the_report_when_it_lands():
    async def go():
        screen = {"now": report(windows=["w-ab12"])}
        hands, _, _ = with_records(screen)
        found = {"ok": True, "summary": "found a photo of a stoma, shown in window w-ab12"}
        out = await hands.act("reference_photo", {"search_terms": "stoma"}, lambda: asyncio.sleep(0, dict(found)))
        assert out["result"] == "verified" and out["pointAt"] == ["window:w-ab12"]
        screen["now"] = report()
        missing = await hands.act("reference_photo", {"search_terms": "stoma"}, lambda: asyncio.sleep(0, dict(found)))
        assert missing["result"] == "uncertain" and "not showing it" in missing["said"]
    asyncio.run(go())


def test_no_verifier_shortcuts_every_member_asked_for_the_layer_counted_and_find_is_never_done():
    async def go():
        screen = {"now": report(drawn=["o3"])}
        hands, _, _ = make(screen)
        some = await hands.act("highlight", {"things": ["o3", "o4"]}, lambda: asyncio.sleep(0, {"ok": True}))
        assert some["result"] == "failed", "a highlight needs all the members asked for"
        screen["now"]["items"].append({"id": "layer:e5", "open": True, "view": {"legacy": {"members": None}}})
        layer = {"ok": True, "outputs": [{"kind": "population", "id": "e5"}]}
        uncounted = await hands.act("outline_cells", {}, lambda: asyncio.sleep(0, dict(layer)))
        assert uncounted["result"] == "uncertain", "a layer on screen without its member counts shows nothing of them"
        screen["now"]["items"][-1]["view"]["legacy"]["members"] = {"count": 41, "drawn": 41}
        counted = await hands.act("outline_cells", {}, lambda: asyncio.sleep(0, dict(layer)))
        assert counted["result"] == "verified" and counted["said"] == "Done: 41 outlined."
        found = await hands.act("find", {"like": "o3"}, lambda: asyncio.sleep(
            0, {"ok": True, "outputs": [{"kind": "named-outline", "id": "o3"}]}))
        assert found["result"] == "verified" and found["said"].startswith("Found") and "Done" not in found["said"]
        missed = await hands.act("find", {"like": "o3"}, lambda: asyncio.sleep(0, {"ok": False, "summary": "none like it"}))
        assert missed["said"] == "Not found: none like it"
        confirmed = await hands.act("present", {"do": "open", "what": "w-9"},
                                    lambda: asyncio.sleep(0, {"ok": True, "_run": SimpleNamespace(outcome={
                                        "status": "confirmed"})}))
        assert confirmed["result"] == "failed", "present's own receipt is not enough when the report lacks its window"
        screen["now"]["items"].append({"id": "w-9", "open": True, "view": {"legacy": {}}})
        shown = await hands.act("present", {"do": "open", "what": "w-9"},
                                lambda: asyncio.sleep(0, {"ok": True, "_run": SimpleNamespace(outcome={
                                    "status": "confirmed"})}))
        assert shown["result"] == "verified"
        timed = await hands.act("present", {"do": "open", "what": "w-9"},
                                lambda: asyncio.sleep(0, {"ok": True, "_run": SimpleNamespace(outcome={
                                    "status": "timed_out"})}))
        assert timed["result"] == "failed"
    asyncio.run(go())


def test_asks_permissions_and_uncertain_receipts_are_lines_in_activity_and_never_done():
    from orchestrator.activity import Activity

    async def go():
        screen = {"now": report(windows=["w-a", "w-b"])}
        dialogue = SimpleNamespace(here=lambda: None, show=lambda line, **k: None, day=SimpleNamespace(sid="p"))
        hub = SimpleNamespace(settings=None)
        hub.activities = Activity(hub, dialogue)
        hands, _, _ = make(screen)
        hands.hub = hub
        await hands.act("present", {"do": "close", "what": "w-gone"}, lambda: asyncio.sleep(0, {"ok": True}))
        hands.spends = lambda intent: "SAM 3.1 runs in the cloud and is paid per use"
        await hands.act("outline_cells", {}, lambda: asyncio.sleep(0, {"ok": True}))
        hands.spends = lambda intent: None
        await hands.act("select", {"object": "o9"}, lambda: asyncio.sleep(0, {"ok": True}))  # no selection reported
        lines = list(hub.activities.recent.values())
        said = [line["text"] for line in lines]
        assert any("asked which one you meant" in t for t in said)
        assert any("asked your permission (SAM 3.1 runs in the cloud" in t for t in said)
        unsure = next(line for line in lines if "not known whether it happened" in line["text"])
        assert unsure["state"] != "done" and unsure["details"]["ids"]["receipt"] == "uncertain"
        assert all(hub.activities.project_of(line["id"]) == "p" for line in lines)
    asyncio.run(go())


def test_every_acting_tool_of_the_partner_goes_through_hands_researchers_without_the_ui_lease(monkeypatch):
    async def go():
        t = await partner_session(monkeypatch, tools={"start_researcher": "Researcher 4 is making a research note"})
        spec = next(s for s in t.session.config.tools if s.name == "start_researcher")
        assert "approved" in spec.parameters["properties"], "a long or paid run asks first, and takes the yes back"
        out = await spec.handler({"brief": "look at the table", "_call_id": "c9"})
        assert out["_hands"]["intent"] == "research" and out["result"] in ("running", "uncertain")
        assert "_run" not in out, "the run itself never goes to the model"
        assert not [m for m in t.studio if m["type"] in ("control", "drive")], "no UI lease for work behind the UI"
        again = await spec.handler({"brief": "look at the table", "_call_id": "c9"})
        assert [r[0] for r in t.ran] == ["start_researcher"] and "not done again" in again["note"]
    asyncio.run(go())


def test_thinks_steps_have_action_ids_so_a_retry_returns_the_earlier_receipt(monkeypatch):
    async def go():
        monkeypatch.setattr("orchestrator.voice.partner_tools.REPORT_WAIT_S", 0.01)
        t = await partner_session(monkeypatch, tools={"tabs": "switched to Sizes"})
        args = {"action": "switch", "tab": "Sizes"}
        first = await t.partner.tools.run("tabs", dict(args), patient=True, action_id="t1:toolu_1")
        again = await t.partner.tools.run("tabs", dict(args), patient=True, action_id="t1:toolu_1")
        assert len(t.ran) == 1 and again["result"] == first["result"] and "not done again" in again["note"]
        await t.partner.tools.run("tabs", dict(args), patient=True, action_id="t1:toolu_2")
        assert len(t.ran) == 2, "another step of the task is its own action"
    asyncio.run(go())


def test_a_voice_use_holds_for_the_later_steps_of_its_turn(monkeypatch):
    from orchestrator import ticket as tickets

    seen = []

    async def use(ctx, args):
        tickets.current().choices["cells"] = {"implementation": "sam-3.1", "policy": "raw", "origin": "user-chosen",
                                              "used": False, "label": "SAM 3.1"}
        return "for this request, segmenting every cell uses SAM 3.1"

    async def outline(ctx, args):
        chosen = tickets.current().choices.get("cells")
        seen.append(dict(chosen) if chosen else None)
        if chosen:
            chosen["used"] = True
        return "outlined 12 cells"

    async def go():
        t = await partner_session(monkeypatch, tools={"use": use, "outline_cells": outline})
        await press(t, 1)
        await release(t, 1)
        chose = await t.partner.tools.run("use", {"operation": "cells", "implementation": "sam-3.1"})
        assert chose["result"] == "verified" and "this request only" in chose["said"]
        await t.partner.tools.run("outline_cells", {})
        assert seen and seen[0]["implementation"] == "sam-3.1", "the choice reached the turn's next step"
    asyncio.run(go())


def test_outlining_rings_the_whole_picture_and_hookes_record_decides_when_the_report_cannot(monkeypatch):
    """Smoke run, 29 Oct: the cue's "frame" target had no region (the studio refused it, bad-target), and Cellpose's
    outlines were said "not sure" because the report carried no counted layer. The population the run saved, in Hooke's
    evidence store, is its record."""
    from types import SimpleNamespace as NS

    from orchestrator import evidence

    store = {"e5": NS(live=lambda: [object()] * 12)}
    monkeypatch.setattr(evidence, "evidence_of", lambda hub: NS(get=lambda eid: store.get(eid)))

    async def go():
        screen = {"now": report()}
        hands, studio, _ = make(screen)
        hands.hub = NS()
        out = await hands.act("outline_cells", {}, lambda: asyncio.sleep(
            0, {"ok": True, "outputs": [{"kind": "population", "id": "e5"}]}))
        drive = next(m for m in studio.sent if m["type"] == "drive")
        assert drive["intent"]["target"] == "frame" and drive["intent"]["region"] == list(hands_mod.WHOLE_PICTURE)
        assert out["result"] == "verified" and out["said"] == "Done: 12 outlined."
        # The studio names the layer by its presentation, not the population, and carries no counts: no failure.
        screen["now"]["items"].append({"id": "layer:pr-1", "open": True, "view": {"legacy": {}}})
        shown = await hands.act("outline_cells", {}, lambda: asyncio.sleep(
            0, {"ok": True, "outputs": [{"kind": "population", "id": "e5"}]}))
        assert shown["result"] == "verified" and shown["said"] == "Done: 12 outlined."
        store.clear()
        none = await hands.act("outline_cells", {}, lambda: asyncio.sleep(
            0, {"ok": True, "outputs": [{"kind": "population", "id": "e5"}]}))
        assert none["result"] == "uncertain", "no record and no report: never done"
    asyncio.run(go())
