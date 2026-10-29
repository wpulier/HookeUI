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
        assert opened["result"] == "verified", "the table window's own check is the studio's report"
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
    assert intent_of("start_researcher", {}) is None


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
