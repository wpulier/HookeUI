"""Work the voice hands to Hooke's brain acts through the voice's Hands (brain/acting.py, voice/partner_tools.py
HandOffActing; the live partner spec 4.2), so driving Hooke by voice is shown, checked and truthful:
- each acting step of the brain is carried out under Hands and ends in a receipt from Hooke's state; the next step
  waits for that receipt, and a step that depends on one whose receipt failed is skipped;
- a hand-off's steps are planned against the hand-off, not the voice turn: the user talking again (a new request)
  never drops them (Will's review, 3 Nov), while taking over with the mouse or Escape still does;
- a step Hands ends before it runs (a question back, a takeover) is settled with that reason;
- typed chat sets no Hands and runs as before."""
import asyncio

from orchestrator.brain.acting import acting, current
from orchestrator.brain.registry import parse_actions
from orchestrator.ticket import using
from orchestrator.voice.hands import SPOKE, TOOK_OVER, intent_of
from orchestrator.voice.partner_tools import HandOffActing, PartnerTools
from tests.test_action_success_dependencies import rig, settled
from tests.test_hands import make, report


class Bridge:
    """A stand-in for the voice's Hands: every step but `look` acts; `failing` steps' receipts say Hooke's state does
    not show what the tool said it did."""

    def __init__(self, failing=(), uncertain=()):
        self.events, self.failing, self.uncertain = [], set(failing), set(uncertain)

    def intent(self, tool, args):
        return None if tool == "look" else object()

    async def step(self, run, args, ctx, start, skip):
        self.events.append(f"start:{run.tool}")
        start()
        await asyncio.wait([run.task])
        result = ("failed" if run.tool in self.failing else "uncertain" if run.tool in self.uncertain else "verified")
        if result == "failed":
            run.ok, run.summary = False, "the studio does not show it"
        run.receipt = {"result": result}
        self.events.append(f"receipt:{run.tool}")
        return run.receipt


def _handlers(entered):
    def tool(name):
        async def handler(ctx, args):
            entered.append(name)
            return f"{name} done"
        return handler
    return {name: tool(name) for name in ("make_tab", "rename", "look", "flaky", "after_flaky", "plain")}


def test_each_step_waits_for_the_previous_steps_receipt():
    async def scenario():
        entered = []
        runner, ctx, ticket, _ = rig(_handlers(entered))
        bridge = Bridge()
        try:
            with using(ticket), acting(bridge):
                runs = await runner.run(parse_actions([{"tool": "make_tab", "args": {}}, {"tool": "rename", "args": {}},
                                                       {"tool": "look", "args": {}}]), ctx)
                await settled(runs)
                await asyncio.wait([r.acting for r in runs if r.acting is not None])
            assert bridge.events == ["start:make_tab", "receipt:make_tab", "start:rename", "receipt:rename"]
            assert entered == ["make_tab", "rename", "look"]  # a look runs directly: its result is its answer
            assert runs[0].receipt == {"result": "verified"} and runs[2].acting is None
        finally:
            ticket.close()
    asyncio.run(scenario())


def test_a_step_after_a_failed_step_is_skipped_but_one_after_an_unconfirmed_step_runs():
    """A dependent step needs its prerequisite's work, not its picture: a step that failed skips it; a step whose work
    happened but the screen did not confirm (uncertain) does not (will-drive, 3 Nov: the zoom after a find hidden by
    the Tab in front was skipped, where it would have brought its Tab forward)."""
    async def scenario():
        for kind, expected in (("failing", ["flaky", "plain"]), ("uncertain", ["flaky", "after_flaky", "plain"])):
            entered = []
            runner, ctx, ticket, _ = rig(_handlers(entered))
            bridge = Bridge(**{kind: ["flaky"]})
            try:
                with using(ticket), acting(bridge):
                    runs = await runner.run(parse_actions([
                        {"tool": "flaky", "args": {}},
                        {"tool": "after_flaky", "args": {}, "after_steps": [1]},
                        {"tool": "plain", "args": {}}]), ctx)
                    await settled(runs)
                    await asyncio.wait([r.acting for r in runs if r.acting is not None])
                assert entered == expected, kind
                if kind == "failing":
                    assert runs[1].ok is False and "dependency 1" in runs[1].summary
            finally:
                ticket.close()
    asyncio.run(scenario())


def test_typed_chat_has_no_hands():
    async def scenario():
        assert current() is None
        entered = []
        runner, ctx, ticket, _ = rig(_handlers(entered))
        try:
            with using(ticket):
                runs = await runner.run(parse_actions([{"tool": "make_tab", "args": {}}]), ctx)
                await settled(runs)
            assert entered == ["make_tab"] and runs[0].acting is None and runs[0].receipt is None
        finally:
            ticket.close()
    asyncio.run(scenario())


def test_talking_again_never_drops_a_handoffs_steps_but_taking_over_does():
    async def go():
        screen = {"now": report(front="main", tabs=("main", "Measurements", "Stomata"))}
        hands, studio, _ = make(screen)
        hands.turn = lambda: 5
        release = asyncio.Event()

        async def slow():
            await release.wait()
            screen["now"] = report(front="Measurements", tabs=("main", "Measurements", "Stomata"), seq=8)
            return {"ok": True, "summary": "switched"}

        async def to_stomata():
            screen["now"] = report(front="Stomata", tabs=("main", "Measurements", "Stomata"), seq=9)
            return {"ok": True, "summary": "switched"}

        # The voice's own call, planned at turn 5, and a hand-off's step queued behind it.
        first = asyncio.ensure_future(hands.act("tabs", {"action": "switch", "tab": "Measurements"}, slow,
                                                plan=hands.plan(None, job="handoff:h1")))
        await asyncio.sleep(0.01)
        queued = asyncio.ensure_future(hands.act("tabs", {"action": "switch", "tab": "Stomata"}, to_stomata,
                                                 plan=hands.plan(None, job="handoff:h1")))
        voice_own = hands.plan(5)
        await asyncio.sleep(0.01)
        await hands.takeover({"why": "talk", "version": hands.version})  # the user starts a new request
        hands.turn = lambda: 6
        release.set()
        assert (await first)["result"] == "verified"
        assert (await queued)["result"] == "verified", "a new request beside it never drops the hand-off's step"
        # The voice's own call planned before the talk is still paused, as before.
        mine = await hands.act("tabs", {"action": "switch", "tab": "main"}, to_stomata, plan=voice_own)
        assert mine["result"] == "failed" and mine["error"] in (SPOKE, "not done: the user took over; look at the "
                                                                "screen again (the next turn's pictures) before acting on it")
        # Taking over with the mouse stops a hand-off step planned before it.
        planned = hands.plan(None, job="handoff:h2")
        hold = asyncio.Event()

        async def held():
            await hold.wait()
            return {"ok": True}
        running = asyncio.ensure_future(hands.act("tabs", {"action": "switch", "tab": "Measurements"}, held,
                                                  plan=hands.plan(None, job="handoff:h2")))
        await asyncio.sleep(0.01)
        await hands.takeover({"why": "mouse", "version": hands.version})
        hold.set()
        assert (await running)["result"] in ("uncertain", "failed")
        after = await hands.act("tabs", {"action": "switch", "tab": "Measurements"}, to_stomata, plan=planned)
        assert after["result"] == "failed" and TOOK_OVER in after["error"] or "look at the screen" in after["error"]
    asyncio.run(go())


def test_a_step_hands_ends_before_it_runs_is_settled_with_its_reason():
    """Two Tabs could be meant: Hands asks back instead of guessing, and the brain's step ends with that question."""
    async def scenario():
        screen = {"now": report(front="main", tabs=("main", "Stomata 1", "Stomata 2"))}
        hands, _, _ = make(screen)
        tools = type("Tools", (), {})()
        tools.hands = hands
        tools.intent = lambda name, args: intent_of(name, args)
        tools._result = PartnerTools._result
        entered = []

        async def switch(ctx, args):
            entered.append(args)
            return "switched"
        runner, ctx, ticket, _ = rig({"tabs": switch})
        bridge = HandOffActing(tools, "handoff:h3")
        try:
            with using(ticket), acting(bridge):
                runs = await runner.run(parse_actions([{"tool": "tabs", "args": {"action": "switch",
                                                                                 "tab": "stomata"}}]), ctx)
                await settled(runs)
                await asyncio.wait([runs[0].acting])
            receipt = runs[0].receipt
            assert receipt["result"] == "ask" and entered == []  # asked, never guessed; nothing was run
            assert runs[0].ok is False and "Stomata 1" in runs[0].summary and "Stomata 2" in runs[0].summary
            assert bridge.receipts[0]["name"] == "tabs"
        finally:
            ticket.close()
    asyncio.run(scenario())


def test_a_handoffs_steps_are_logged_with_their_job():
    """The journeys count hand-off steps (bench/voice_journeys/standard.py drive) from this field."""
    async def go():
        screen = {"now": report(front="main")}
        hands, _, logged = make(screen)

        async def effect():
            screen["now"] = report(front="Measurements", seq=8)
            return {"ok": True}
        await hands.act("tabs", {"action": "switch", "tab": "Measurements"}, effect, plan=hands.plan(None, job="handoff:h9"))
        record = next(r for r in logged if r["kind"] == "partner-hands")
        assert record["job"] == "handoff:h9" and record["result"] == "verified"
    asyncio.run(go())


def test_a_mouse_takeover_between_a_handoffs_steps_ends_the_rest():
    """The hand-off holds the lease through its steps, so moving the mouse between two of them is a takeover the server
    hears (will-drive, 3 Nov: nothing reached it), and the steps after it are not done."""
    async def scenario():
        screen = {"now": report(front="main", tabs=("main", "Measurements", "Stomata"))}
        hands, studio, _ = make(screen)
        tools = type("Tools", (), {})()
        tools.hands, tools.intent, tools._result = hands, (lambda name, args: intent_of(name, args)), PartnerTools._result
        tools._reported_at = lambda: 0.0

        async def report_after(before):
            return None
        tools.report_after = report_after
        entered = []

        async def switch(ctx, args):
            entered.append(args["tab"])
            screen["now"] = report(front=args["tab"], tabs=("main", "Measurements", "Stomata"), seq=8 + len(entered))
            return "switched"
        runner, ctx, ticket, _ = rig({"tabs": switch})
        bridge = HandOffActing(tools, "handoff:h5")
        try:
            with using(ticket), acting(bridge):
                first = await runner.run(parse_actions([{"tool": "tabs", "args": {"action": "switch",
                                                                                  "tab": "Measurements"}}]), ctx)
                await settled(first)
                await asyncio.wait([first[0].acting])
                assert hands.owner == "partner", "the lease is held between the hand-off's steps"
                await hands.takeover({"why": "mouse", "version": hands.version})
                second = await runner.run(parse_actions([{"tool": "tabs", "args": {"action": "switch",
                                                                                   "tab": "Stomata"}}]), ctx)
                await settled(second)
                await asyncio.wait([second[0].acting])
            assert entered == ["Measurements"] and second[0].ok is False and TOOK_OVER in second[0].summary
            await bridge.close()
            assert hands.owner == "user"
        finally:
            ticket.close()
    asyncio.run(scenario())
