"""Work in flight while the user keeps talking (Will, 29 Oct: "if its a parallel process and you keep talking, we have to
have some hyper clear way to communicate thats whats happening"): each do_in_hooke hand-off is one `handoff` line,
working at once with the voice's short label, then settled from the brain's own answer (done, or did not work with its
reason), filed with its conversation under one lineId so a later state replaces the earlier one (voice/handoffs.py). A
long one says it is still working past the voice's wait, and its outcome updates the same row when it lands."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestrator import prompts
from orchestrator.day import DayStore
from orchestrator.dialogue import Dialogue
from orchestrator.voice import partner_tools
from orchestrator.voice.followups import followups_of
from orchestrator.voice.handoffs import Handoffs
from tests.test_voice_partner import partner_session
from tests.waiting import until_async

PHOTO = "Reference photo of a stoma"
ASKED = "search online and open a reference image of a stoma"
OPENED = "Opened 'Stomata | Microscopy of Nature' in Stoma Reference."


class Shown:
    """A dialogue: each line as filed, with the conversation it was asked in."""

    def __init__(self):
        self.lines, self.flying = [], set()

    def show(self, line, *, asked=None, rid=None):
        self.lines.append((dict(line), asked))
        return line


def rows(shown, hid=None):
    return [line for line, _ in shown.lines if line["type"] == "handoff" and (hid is None or line["id"] == hid)]


def step(tool, ok, summary, n=1):
    return {"tool": tool, "ok": ok, "summary": summary, "ms": 900, "requestId": "RUN/7", "actionId": f"a{n}",
            "goal": {"not": "for the row"}}


async def handing_off(monkeypatch):
    t = await partner_session(monkeypatch, acts="brain")
    shown = Shown()
    t.partner.tools.handoffs.dialogue = shown
    return t, shown


def test_a_hand_off_shows_working_at_once_with_its_label_then_settles_into_the_brains_answer(monkeypatch):
    async def go():
        t, shown = await handing_off(monkeypatch)
        gate = asyncio.Event()
        where = object()
        t.brain.conversation.here = lambda: where

        async def turn(text, channel, **kw):
            await gate.wait()
            return {"answer": OPENED, "turnId": 7,
                    "actions": [step("reference_photo", True, "found 'Stomata | Microscopy of Nature'"),
                                step("present", True, "the studio shows the requested window change", 2)]}
        t.brain.turn = turn
        call = asyncio.ensure_future(t.partner.tools.do_in_hooke({"request": ASKED, "label": PHOTO}))
        first = (await until_async(lambda: rows(shown)))[0]
        assert first["state"] == "working" and first["label"] == PHOTO and first["request"] == ASKED
        assert first["lineId"] == f"handoff:{first['id']}" and "long" not in first and "answer" not in first
        assert shown.lines[0][1] is where, "filed with the conversation it was asked in"
        assert first["id"] in shown.flying
        gate.set()
        out = await call
        assert out["ok"] is True
        last = rows(shown)[-1]
        assert last["id"] == first["id"] and last["lineId"] == first["lineId"]
        assert last["state"] == "done" and last["answer"] == OPENED and last["turnId"] == 7
        assert [s["tool"] for s in last["steps"]] == ["reference_photo", "present"]
        assert last["steps"][0]["actionId"] == "a1" and "goal" not in last["steps"][0], "the row's facts, no more"
        assert last["endedAt"] >= last["startedAt"] and first["id"] not in shown.flying
        assert [r["state"] for r in rows(shown)] == ["working", "done"]
    asyncio.run(go())


def test_it_did_not_work_with_its_reason_from_the_answer_from_the_failed_step_or_plainly(monkeypatch):
    async def go():
        t, shown = await handing_off(monkeypatch)
        t.brain.turn = lambda text, channel, **kw: asyncio.sleep(0, {
            "answer": "The table could not open: there are no measurements yet.",
            "actions": [step("show_measurements", False, "no measurements yet")]})
        out = await t.partner.tools.do_in_hooke({"request": "open the table", "label": "The measurements table"})
        assert out["ok"] is False
        assert rows(shown)[-1]["state"] == "failed"
        assert rows(shown)[-1]["reason"] == "The table could not open: there are no measurements yet."
        # No answer: the failed step's own words, never its tool's name.
        t.brain.turn = lambda text, channel, **kw: asyncio.sleep(0, {
            "answer": "", "actions": [step("show_measurements", False, "no measurements yet")]})
        await t.partner.tools.do_in_hooke({"request": "open the table", "label": "The measurements table"})
        assert rows(shown)[-1]["reason"] == "no measurements yet"

        async def broken(text, channel, **kw):
            raise RuntimeError("the brain fell over")
        t.brain.turn = broken
        with pytest.raises(RuntimeError):  # the session tells the voice "something went wrong in the app"
            await t.partner.tools.do_in_hooke({"request": "open a new tab", "label": "A new Tab"})
        await until_async(lambda: rows(shown)[-1]["state"] == "failed")
        assert rows(shown)[-1]["reason"] == "Something went wrong in Hooke while it worked"
        assert any(r["kind"] == "partner-handoff-failed" and "fell over" in r["error"] for r in t.logged)
        assert not shown.flying
    asyncio.run(go())


def test_a_change_not_confirmed_on_screen_is_uncertain_never_failed_and_starting_a_researcher_is_started(monkeypatch):
    """Its step's own receipt decides (present.py: sent, no report since; the studio in the background; its check timed
    out): it may not have happened, which is neither done nor "did not work". A real failure beside it still is one."""
    unseen = {**step("tabs", True, "sent, but not confirmed on screen"),
              "outcome": {"status": "unobserved", "failure": {"code": "STUDIO_UNREPORTED"}}}
    timed_out = {**step("present", False, "the display could not be verified"), "outcome": {"status": "timed_out"}}

    async def go():
        t, shown = await handing_off(monkeypatch)
        for actions in ([unseen], [timed_out]):
            t.brain.turn = lambda text, channel, actions=actions, **kw: asyncio.sleep(0, {"answer": "", "actions": actions})
            await t.partner.tools.do_in_hooke({"request": "open a new tab", "label": "A new Tab"})
            assert rows(shown)[-1]["state"] == "uncertain" and "reason" not in rows(shown)[-1]
        t.brain.turn = lambda text, channel, **kw: asyncio.sleep(0, {
            "answer": "", "actions": [unseen, step("show_measurements", False, "no measurements yet", 2)]})
        await t.partner.tools.do_in_hooke({"request": "open a new tab and the table", "label": "A new Tab"})
        assert rows(shown)[-1]["state"] == "failed" and rows(shown)[-1]["reason"] == "no measurements yet"
        t.brain.turn = lambda text, channel, **kw: asyncio.sleep(0, {
            "answer": "Researcher 2 is making the graphic.", "actions": [started_researcher("R2")]})
        await t.partner.tools.do_in_hooke({"request": "make a graphic of a stoma", "label": "A stoma graphic"})
        assert rows(shown)[-1]["state"] == "started", "its part is done; the researcher's work is not"
    asyncio.run(go())


def test_a_long_one_says_still_working_and_its_later_outcome_updates_the_same_row_said_once(monkeypatch):
    monkeypatch.setattr(partner_tools, "TOOL_WAIT_S", 0.1)

    async def go():
        t, shown = await handing_off(monkeypatch)
        gate = asyncio.Event()

        async def turn(text, channel, **kw):
            await gate.wait()
            return {"answer": OPENED, "actions": [step("reference_photo", True, "found it")]}
        t.brain.turn = turn
        first = await asyncio.wait_for(t.partner.tools.do_in_hooke({"request": ASKED, "label": PHOTO}), 1.0)
        assert first["ok"] is None and first["still_working"] is True
        assert first["note"] == partner_tools.STILL_HANDED_OFF, "the voice never narrates the wait: the row shows it"
        assert [(r["state"], r.get("long", False)) for r in rows(shown)] == [("working", False), ("working", True)]
        t.session._last_user_at = 0.0
        gate.set()
        await until_async(lambda: rows(shown)[-1]["state"] == "done")
        assert len({r["id"] for r in rows(shown)}) == 1, "one row, updated in place"
        assert rows(shown)[-1]["long"] is True and rows(shown)[-1]["answer"] == OPENED
        await until_async(lambda: t.sb.responses == 1)  # the owed outcome is said once, when it lands
        await asyncio.sleep(0.2)
        assert t.sb.responses == 1
    asyncio.run(go())


def test_two_in_flight_at_once_are_two_rows_each_settling_on_its_own(monkeypatch):
    async def go():
        t, shown = await handing_off(monkeypatch)
        gates = {"photo": asyncio.Event(), "tab": asyncio.Event()}

        async def turn(text, channel, **kw):
            which = "photo" if "stoma" in text else "tab"
            await gates[which].wait()
            return {"answer": OPENED if which == "photo" else "Opened a new Tab, Tab 2.",
                    "actions": [step("present", True, "shown")]}
        t.brain.turn = turn
        photo = asyncio.ensure_future(t.partner.tools.do_in_hooke({"request": ASKED, "label": PHOTO}))
        tab = asyncio.ensure_future(t.partner.tools.do_in_hooke({"request": "can you open a new tab for me?",
                                                                 "label": "A new Tab"}))
        await until_async(lambda: len(rows(shown)) == 2)
        ids = [r["id"] for r in rows(shown)]
        assert len(set(ids)) == 2 and [r["label"] for r in rows(shown)] == [PHOTO, "A new Tab"]
        assert shown.flying == set(ids)
        gates["tab"].set()
        await tab
        assert rows(shown, ids[1])[-1]["state"] == "done" and rows(shown, ids[0])[-1]["state"] == "working"
        assert shown.flying == {ids[0]}
        gates["photo"].set()
        await photo
        assert rows(shown, ids[0])[-1]["answer"] == OPENED and not shown.flying
    asyncio.run(go())


def test_a_step_still_running_after_the_answer_keeps_the_row_working_and_a_label_falls_back_to_the_words(monkeypatch):
    async def go():
        t, shown = await handing_off(monkeypatch)
        t.brain.turn = lambda text, channel, **kw: asyncio.sleep(0, {
            "answer": "Loading the photo now.", "actions": [step("reference_photo", None, "still loading")]})
        await t.partner.tools.do_in_hooke({"request": "find me a photo of a stoma"})
        last = rows(shown)[-1]
        assert last["state"] == "working" and last["settling"] is True and last["answer"] == "Loading the photo now."
        assert last["label"] == "find me a photo of a stoma", "no label from the voice: the request's own words"
    asyncio.run(go())


def test_without_a_dialogue_the_lines_go_to_the_studio_over_the_voice_socket(monkeypatch):
    async def go():
        t = await partner_session(monkeypatch, acts="brain")
        t.brain.turn = lambda text, channel, **kw: asyncio.sleep(0, {"answer": "Done.", "actions": []})
        await t.partner.tools.do_in_hooke({"request": "open a new tab", "label": "A new Tab"})
        sent = await until_async(lambda: [m for m in t.studio if m.get("type") == "handoff"] if len(
            [m for m in t.studio if m.get("type") == "handoff"]) == 2 else None)
        assert [m["state"] for m in sent] == ["working", "done"]
    asyncio.run(go())


def test_a_replay_shows_its_last_state_and_one_this_run_no_longer_runs_as_unrecorded(tmp_path):
    clock = lambda: 1791712000.0  # noqa: E731
    day = DayStore(root=tmp_path / "sessions", clock=clock)
    published = []
    dialogue = Dialogue(day, published.append)
    handoffs = Handoffs(dialogue)
    tab = handoffs.start("can you open a new tab for me?", "A new Tab", day.here())
    photo = handoffs.start(ASKED, PHOTO, day.here())
    handoffs.still(photo)
    handoffs.answered(photo, {"answer": OPENED, "actions": []}, {"ok": True})
    assert [m["state"] for m in published] == ["working", "working", "working", "done"], "every studio is told"
    lines = [line for line in dialogue.history()["lines"] if line["type"] == "handoff"]
    assert [(line["label"], line["state"]) for line in lines] == [("A new Tab", "working"), (PHOTO, "done")]
    # After a restart nothing is running: a hand-off the journal left working had no result recorded, and says so.
    again = Dialogue(DayStore(root=tmp_path / "sessions", clock=clock), lambda _: None).history()["lines"]
    replayed = [line for line in again if line["type"] == "handoff"]
    assert [(line["id"], line["state"]) for line in replayed] == [(tab.id, "unknown"), (photo.id, "done")]
    assert replayed[0]["reason"].startswith("No result was recorded")


def test_the_voice_gives_each_hand_off_a_title_and_says_only_the_new_request_while_one_runs(monkeypatch):
    async def go():
        t = await partner_session(monkeypatch, acts="brain")
        spec = next(s for s in t.partner.tools.specs() if s.name == "do_in_hooke")
        assert spec.parameters["required"] == ["request", "label"]
        assert "never a tool name" in spec.parameters["properties"]["label"]["description"]
        assert "after the work this request starts" in spec.parameters["properties"]["then"]["description"]
    asyncio.run(go())
    written = prompts.partner_prompt("brain")
    assert "a row of its own" in written and "the new one only, never the earlier one again" in written
    assert "say each outcome once, when it lands" in written
    assert "never promise to do\nanything later otherwise" in written


# --- a step for after the work finishes (voice/followups.py) --------------------------------------------------------
GRAPHIC = "make a small educational graphic of a stoma, then put it in the Stoma Reference tab"
PLACE = "put the picture in the Stoma Reference tab"


def started_researcher(rid):
    return {**step("start_researcher", True, "started Researcher 2"), "outputs": [
        {"kind": "resource", "resource": {"kind": "research-run", "researcherId": rid, "day": "d", "runId": "x", "run": 0}}]}


async def with_research(monkeypatch):
    t, shown = await handing_off(monkeypatch)
    research = SimpleNamespace(on_done=[], get=lambda rid: SimpleNamespace(rid=rid, name="Researcher 2"))
    t.brain.services = SimpleNamespace(research=research)
    research.on_done.append(t.partner._research_done)  # as Partner.bind registers it when the app has researchers
    asked = []

    async def turn(text, channel, **kw):
        asked.append((text, kw.get("said")))
        if len(asked) == 1:
            return {"answer": "Researcher 2 is making the graphic.", "turnId": 3, "actions": [started_researcher("R2")]}
        return {"answer": "Placed the stoma graphic in Stoma Reference.", "turnId": 4,
                "actions": [step("present", True, "the studio shows the requested window change", 2)]}
    t.brain.turn = turn
    return t, shown, research, asked


def landed(research, status="done"):
    finished = SimpleNamespace(purpose="research", status=status, rid="R2", name="Researcher 2",
                               result={"one_liner": "A labelled stoma diagram"})
    for hook in list(research.on_done):
        hook(finished)


def said_items(t):
    return [c["text"] for i in t.sb.items() if i.get("role") == "system" for c in i["content"]]


def test_a_later_step_is_kept_with_the_researcher_done_when_it_lands_and_said_once_with_its_result(monkeypatch):
    """Will's session, 29 Oct: "When it's ready, we'll place the picture into the Stoma Reference tab", and nothing did.
    The voice cannot act when a researcher lands (its unasked lines have no tools), so Hooke keeps the step and does it."""
    async def go():
        t, shown, research, asked = await with_research(monkeypatch)
        out = await t.partner.tools.do_in_hooke({"request": GRAPHIC, "label": "A stoma graphic", "then": PLACE})
        assert "do only the first part now" in asked[0][0], "the brain does the first part; Hooke keeps the step"
        assert out["then"]["kept_with"] == "Researcher 2"
        parent = rows(shown)[-1]
        assert parent["then"] == {"words": PLACE, "state": "waiting", "after": "Researcher 2", "rid": "R2"}
        assert followups_of(t.hub).has("R2")
        t.session._last_user_at = 0.0
        landed(research)
        follow = await until_async(lambda: [r for r in rows(shown) if r.get("after") == "Researcher 2"
                                            and r["state"] == "done"])
        assert follow[-1]["id"] != parent["id"] and follow[-1]["label"] == "Put the picture in the Stoma Reference tab"
        assert asked[1][1] == PLACE and "Researcher 2 has finished now" in asked[1][0] and GRAPHIC in asked[1][0]
        assert rows(shown, parent["id"])[-1]["then"]["state"] == "done"
        await until_async(lambda: t.sb.responses == 1)
        result = next(s for s in said_items(t) if '"researcher": "Researcher 2"' in s)
        assert "Placed the stoma graphic in Stoma Reference." in result, "its result and the step's outcome, together"
        await asyncio.sleep(0.3)
        assert t.sb.responses == 1, "said once"
        assert not followups_of(t.hub).has("R2")
    asyncio.run(go())


def test_a_researcher_that_did_not_finish_leaves_the_step_undone_and_says_so(monkeypatch):
    async def go():
        t, shown, research, asked = await with_research(monkeypatch)
        await t.partner.tools.do_in_hooke({"request": GRAPHIC, "label": "A stoma graphic", "then": PLACE})
        parent = rows(shown)[-1]["id"]
        t.session._last_user_at = 0.0
        landed(research, "failed")
        await until_async(lambda: rows(shown, parent)[-1]["then"]["state"] == "dropped")
        assert "Researcher 2 did not finish" in rows(shown, parent)[-1]["then"]["reason"]
        await until_async(lambda: t.sb.responses == 1)
        assert any('"status": "not done"' in s for s in said_items(t))
        assert len(asked) == 1, "nothing was handed over for a step whose work did not finish"
    asyncio.run(go())


def test_a_step_with_nothing_to_wait_on_is_done_right_after_or_never_promised(monkeypatch):
    async def go():
        t, shown = await handing_off(monkeypatch)
        calls = []

        async def turn(text, channel, **kw):
            calls.append(text)
            return {"answer": "Opened a new Tab." if len(calls) == 1 else "Put the photo in it.",
                    "actions": [step("present", True, "shown", len(calls))]}
        t.brain.turn = turn
        t.session._last_user_at = 0.0
        out = await t.partner.tools.do_in_hooke({"request": "open a new tab, then put the photo in it",
                                                 "label": "A new Tab", "then": "put the photo in it"})
        assert out["then"]["running_now"] is True
        await until_async(lambda: len(calls) == 2 and rows(shown)[-1]["state"] == "done")
        await until_async(lambda: t.sb.responses == 1)
        assert any("Put the photo in it." in s for s in said_items(t))
        # The first part did not work: nothing keeps the later step, and the voice is told not to promise it.
        t.brain.turn = lambda text, channel, **kw: asyncio.sleep(0, {
            "answer": "", "actions": [step("present", False, "the studio did not show it")]})
        out = await t.partner.tools.do_in_hooke({"request": "open a new tab, then put the photo in it",
                                                 "label": "A new Tab", "then": "put the photo in it"})
        assert out["then"]["not_kept"] == "the first part did not work" and "do not promise it" in out["then"]["note"]
        assert rows(shown)[-1]["then"]["state"] == "dropped"
    asyncio.run(go())


def test_a_call_the_brain_corrected_in_the_same_turn_is_no_failure_and_never_said(monkeypatch):
    """will-research, 29 Oct: start_researcher was first refused for its own argument (annotated_image, nothing
    outlined), then started as an illustration in the same turn. The row said "did not work" and the voice said "it
    couldn't annotate your stoma because nothing was outlined first". A refusal of the brain's own call (`argument`)
    that a later call of the same tool replaced is the brain correcting itself: neither a failure nor told to the
    voice. A real failure, or an argument refusal never corrected, still is one."""
    corrected = {**step("start_researcher", False, "annotated_image labels a photo of the user's own outlined thing, "
                                                    "and nothing is outlined"), "argument": True}

    async def go():
        t, shown = await handing_off(monkeypatch)
        t.brain.turn = lambda text, channel, **kw: asyncio.sleep(0, {
            "answer": "Researcher 1 is making a labelled picture of a stoma.",
            "actions": [corrected, started_researcher("r271381")]})
        out = await t.partner.tools.do_in_hooke({"request": "make a labeled graphic of a stoma", "label": "A graphic"})
        assert out["ok"] is True and "failed" not in out
        assert "outlined" not in json.dumps(out), "the corrected refusal never reaches the voice"
        assert rows(shown)[-1]["state"] == "started"
        # Not corrected: still a failure, said from the brain's answer.
        t.brain.turn = lambda text, channel, **kw: asyncio.sleep(0, {
            "answer": "I could not start it.", "actions": [corrected]})
        out = await t.partner.tools.do_in_hooke({"request": "make a labeled graphic of a stoma", "label": "A graphic"})
        assert out["ok"] is False and rows(shown)[-1]["state"] == "failed"
        # A real failure beside a later success of the same tool is not hidden.
        t.brain.turn = lambda text, channel, **kw: asyncio.sleep(0, {
            "answer": "", "actions": [step("present", False, "the window did not close"), step("present", True, "opened", 2)]})
        out = await t.partner.tools.do_in_hooke({"request": "close that and open this", "label": "Windows"})
        assert out["ok"] is False and out["failed"] == ["present: the window did not close"]
    asyncio.run(go())
