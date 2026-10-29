"""The live partner's event bus and its delivery gate (voice/events.py and RealtimeSession, its one consumer; the part 2.5
spec, section 4.5), against the fake sideband of test_voice_partner."""
import asyncio
import json
import time
from types import SimpleNamespace

from orchestrator.brain.narrator import Line, Narrator, Said
from orchestrator.voice import events, openai_realtime
from orchestrator.voice.base import VoiceConfig
from orchestrator.voice.events import Bus
from orchestrator.voice.openai_realtime import RealtimeSession
from tests.test_voice_partner import FakeSideband, done, settle
from tests.test_voice_partner import no_tail  # noqa: F401  (autouse: no release tail, no progress lines)
from tests.waiting import until_async


async def session_on(bus=None):
    """A RealtimeSession on a fake sideband, with `bus` handed to it (a new one when None)."""
    sidebands, logged = [], []

    async def create_call(offer, session):
        return "answer", f"rtc_{len(sidebands) + 1}"

    async def open_sideband(call_id):
        sb = FakeSideband()
        sidebands.append(sb)
        return sb

    async def hangup(call_id):
        pass

    session = RealtimeSession(VoiceConfig(model="gpt-realtime-2.1", system_prompt="be brief"), create_call=create_call,
                              open_sideband=open_sideband, hangup=hangup, bus=bus)
    session.noted = logged.append
    await session.start("v=0 offer")
    session._last_user_at = 0.0  # nobody has spoken yet
    return session, sidebands[-1], logged


def creates(sb):
    return [e["response"] for e in sb.sent if e["type"] == "response.create"]


def given(response) -> str:
    """What a bus response was given to say (its own context)."""
    return response["input"][0]["content"][0]["text"]


def system_texts(sb) -> list[str]:
    return [c["text"] for i in sb.items() if i.get("role") == "system" for c in i["content"]]


async def audio(sb, rid, how):
    sb.emit({"type": f"output_audio_buffer.{how}", "response_id": rid})
    await settle()


async def hold(session, n):
    await session.talk_down(n)
    await asyncio.sleep(openai_realtime.MIN_HOLD_S + 0.01)


# --- the delivery gate ---------------------------------------------------------------------------------------------
def test_nothing_is_said_while_the_key_is_held_a_turn_is_pending_or_audio_plays(monkeypatch):
    monkeypatch.setattr(openai_realtime, "IDLE_S", 0.2)

    async def go():
        session, sb, logged = await session_on()
        await session.talk_down(1)
        await session.proactive('{"researcher": "Researcher 3", "status": "done"}', event="researcher-done", ref="R3")
        await asyncio.sleep(0.4)
        assert not creates(sb), "never while the key is held"
        await asyncio.sleep(openai_realtime.MIN_HOLD_S)
        await session.talk_up(1)
        await until_async(lambda: sb.responses == 1)
        await audio(sb, "resp_1", "started")
        await asyncio.sleep(0.4)
        assert sb.responses == 1, "never while the user's turn is answered"
        await done(sb, "resp_1")
        await asyncio.sleep(0.4)
        assert sb.responses == 1, "never while its audio still plays"
        await audio(sb, "resp_1", "stopped")
        await until_async(lambda: sb.responses == 2)
        line = creates(sb)[-1]
        assert line["metadata"]["cause"] == "proactive" and line["tool_choice"] == "none"
        assert "Researcher 3" in given(line) and 'untrusted="true"' in given(line), "facts, marked as not the user's"
        assert "Researcher 3" not in line["instructions"]
    asyncio.run(go())


def test_a_result_is_said_within_two_seconds_of_the_pause(wall_clock):
    async def go():
        session, sb, logged = await session_on()
        await hold(session, 1)
        await session.proactive('{"tool": "count", "status": "done"}', event="tool-done", ref="a1")
        await session.talk_up(1)
        await until_async(lambda: sb.responses == 1)
        await done(sb, "resp_1")  # the turn answered: nothing more pending
        quiet = time.monotonic()
        await until_async(lambda: sb.responses == 2)
        wall_clock.within(time.monotonic() - quiet, 2.0, "a result said once the user's turn was over")
    asyncio.run(go())


def test_results_due_within_120_ms_are_said_together_the_result_first():
    async def go():
        session, sb, logged = await session_on()
        await session.proactive('{"event": "focus-drifted", "sharpness": 0.4}', event="focus-drifted", ref="v1")
        await asyncio.sleep(0.05)
        await session.proactive('{"tool": "count", "status": "done", "summary": "341 cells"}', event="tool-done",
                                ref="a1")
        await until_async(lambda: sb.responses == 1)
        await asyncio.sleep(0.4)
        assert sb.responses == 1, "one response"
        line = creates(sb)[0]
        assert len(line["metadata"]["events"].split(",")) == 2
        assert given(line).index("341 cells") < given(line).index("focus-drifted"), "the result first"
        said = [r for r in logged if r["kind"] == "partner-proactive" and r.get("responseCreatedT")]
        assert sorted(r["ref"] for r in said) == ["a1", "v1"]
    asyncio.run(go())


def test_an_interrupting_failure_stops_the_voices_own_words_but_never_the_users_turn(monkeypatch):
    monkeypatch.setattr(openai_realtime, "IDLE_S", 5.0)  # an interrupting event never waits for a pause

    async def go():
        session, sb, logged = await session_on()
        await session.proactive('{"tool": "count", "status": "done"}', event="tool-done", ref="a1")
        await until_async(lambda: sb.responses == 1)
        await audio(sb, "resp_1", "started")  # the voice is saying it
        await session.proactive('{"job": "j1", "status": "failed", "next": "turn the lamp on"}', event="job-failed",
                                ref="j1", urgent=True)
        await until_async(lambda: "response.cancel" in sb.types())
        await done(sb, "resp_1", status="cancelled")  # the API ends the response it was told to cancel
        await until_async(lambda: sb.responses == 2)
        assert "turn the lamp on" in given(creates(sb)[1])
        assert session.bus.result_for("a1").state == "interrupted", "the result it stopped stays owed"
        await done(sb, "resp_2")
        # While the user holds the key or their turn is being answered, it waits; then it is said at once.
        await hold(session, 2)
        await session.proactive('{"job": "j2", "status": "failed"}', event="job-failed", ref="j2", urgent=True)
        await asyncio.sleep(0.3)
        assert sb.responses == 2
        await session.talk_up(2)
        await until_async(lambda: sb.responses == 3)
        assert creates(sb)[2]["metadata"]["cause"] == "turn"
        await asyncio.sleep(0.3)
        assert sb.responses == 3 and "response.cancel" not in sb.types()[-6:], "the user's own answer is never cut"
        await done(sb, "resp_3")
        await until_async(lambda: sb.responses == 4)
        assert '"j2"' in given(creates(sb)[3])
    asyncio.run(go())


# --- heard, interrupted, owed --------------------------------------------------------------------------------------
def test_heard_only_when_its_audio_stops_and_the_narrators_claim_follows():
    async def go():
        session, sb, logged = await session_on()
        session.said = said = Said()
        narrator = Narrator(lambda: session, lambda *a, **k: None)
        narrator.offer(Line('{"tool": "outline", "status": "done"}', notify=True, action_id="a1",
                            outcomes=frozenset({"action:a1"})))
        await until_async(lambda: sb.responses == 1)
        event = next(iter(session.bus.events.values()))
        await audio(sb, "resp_1", "started")
        await done(sb, "resp_1", usage={"output_token_details": {"audio_tokens": 20}})
        await asyncio.sleep(0.3)
        assert event.state == "playing", "started is not heard"
        assert "action:a1" not in said and "a1" not in session.spoken_actions
        await audio(sb, "resp_1", "stopped")
        assert event.state == "heard"
        assert "action:a1" in said and "a1" in session.spoken_actions
    asyncio.run(go())


def test_a_result_cut_off_comes_back_once_with_what_was_heard_then_is_retired(monkeypatch):
    monkeypatch.setattr(openai_realtime, "IDLE_S", 0.2)

    async def go():
        session, sb, logged = await session_on()
        retired = []
        session.on_retired = retired.append
        await session.proactive('{"researcher": "Researcher 3", "status": "done", "found": "two size groups"}',
                                event="researcher-done", ref="R3")
        await until_async(lambda: sb.responses == 1)
        await audio(sb, "resp_1", "started")
        sb.emit({"type": "response.output_audio_transcript.delta", "response_id": "resp_1",
                 "delta": "Researcher three found two"})
        await done(sb, "resp_1", usage={"output_token_details": {"audio_tokens": 100}})  # 5 s of words
        await hold(session, 2)  # the user cuts it off
        await session.talk_cancel(2)
        event = session.bus.result_for("R3")
        assert event.state == "interrupted" and event.interrupted == 1
        await until_async(lambda: sb.responses == 2)
        again = creates(sb)[1]
        assert "as I started to say" in again["instructions"]
        assert "cut off about" in given(again) and "Researcher three found two" in given(again)
        await audio(sb, "resp_2", "started")
        await done(sb, "resp_2", status="cancelled")  # cut off again
        assert event.state == "retired" and retired == [event]
        await asyncio.sleep(0.5)
        assert sb.responses == 2, "never a third time"
        assert any(t.startswith("Cut off twice") and "two size groups" in t for t in system_texts(sb))
    asyncio.run(go())


def test_progress_is_spaced_dropped_when_cut_and_never_owed(monkeypatch):
    monkeypatch.setattr(openai_realtime, "IDLE_S", 0.2)
    monkeypatch.setattr(openai_realtime, "THINK_PROGRESS_AFTER_S", 0.1)
    monkeypatch.setattr(openai_realtime, "THINK_PROGRESS_EVERY_S", 0.1)

    async def go():
        session, sb, logged = await session_on()
        finished = asyncio.Event()
        telling = asyncio.ensure_future(session.tell_progress("t1", lambda: not finished.is_set(), lines=2,
                                                              facts=lambda: "counting, 3 s so far"))
        await until_async(lambda: sb.responses == 1)
        line = creates(sb)[0]
        assert line["metadata"]["cause"] == "progress" and "counting, 3 s so far" in line["instructions"]
        first = next(e for e in session.bus.events.values() if e.progress)
        await audio(sb, "resp_1", "started")
        await audio(sb, "resp_1", "cleared")
        await done(sb, "resp_1")
        assert first.state == "dropped" and not first.owed and first not in session.bus.due()
        await asyncio.sleep(0.8)
        assert sb.responses == 1, "not replayed, and the next waits PROGRESS_SPACING_S"
        monkeypatch.setattr(events, "PROGRESS_SPACING_S", 0.0)
        await until_async(lambda: sb.responses == 2)
        assert "different words" in creates(sb)[1]["instructions"]
        finished.set()
        await telling
    asyncio.run(go())


def test_owed_results_survive_handing_the_bus_to_a_new_session():
    async def go():
        first, sb1, _ = await session_on()
        await first.proactive('{"researcher": "Researcher 3", "status": "done"}', event="researcher-done", ref="R3")
        await until_async(lambda: sb1.responses == 1)
        await audio(sb1, "resp_1", "started")
        await hold(first, 1)  # the user cuts it off and holds the key; a result lands meanwhile
        await first.proactive('{"tool": "count", "status": "done", "summary": "341"}', event="tool-done", ref="a1")
        await first.close()
        bus = first.bus
        assert bus.result_for("R3").state == "interrupted" and bus.result_for("a1").state == "pending"
        second, sb2, _ = await session_on(bus)
        assert bus.owner is second
        await until_async(lambda: sb2.responses == 1)
        line = creates(sb2)[0]
        assert "Researcher 3" in given(line) and "341" in given(line), "both owed results, in the new session"
        assert sb1.responses == 1, "the session that ended says nothing more"
        await second.close()
    asyncio.run(go())


# --- context and coalescing ----------------------------------------------------------------------------------------
def test_context_stays_silent_and_is_coalesced():
    async def go():
        session, sb, logged = await session_on()
        await session.inject("The user opened Tab Sizes", respond=False, source="user-action")
        await session.inject("The user moved the table", respond=False, source="user-action")
        await asyncio.sleep(0.2)
        assert not [t for t in system_texts(sb) if "opened Tab Sizes" in t], "a burst is told as one, when it ends"
        told = await until_async(lambda: [t for t in system_texts(sb) if "opened Tab Sizes" in t])
        assert len(told) == 1 and "moved the table" in told[0] and told[0].startswith("Context only")
        for _ in range(2):
            await session.proactive("Cellpose is on", event="capability-on", ref="cellpose")
        assert len([t for t in system_texts(sb) if "Cellpose is on" in t]) == 1
        await asyncio.sleep(0.3)
        assert sb.responses == 0, "context is never answered"
    asyncio.run(go())


def test_a_burst_of_watcher_events_is_one_line_with_the_newest_facts():
    async def go():
        session, sb, logged = await session_on()
        for i in range(4):
            await session.proactive(json.dumps({"event": "population-changed", "gone": i}), event="population-changed",
                                    ref=f"v{i}", mark="frame")
        await session.proactive('{"event": "focus-drifted"}', event="focus-drifted", ref="v9")
        await until_async(lambda: sb.responses == 1)
        await asyncio.sleep(0.4)
        assert sb.responses == 1
        text = given(creates(sb)[0])
        assert '"gone": 3' in text and '"gone": 0' not in text and "focus-drifted" in text
        record = next(r for r in logged if r["kind"] == "partner-proactive" and r["ref"] == "v3")
        assert record["merged"] == ["v0", "v1", "v2"] and record["markTarget"] == "frame"
    asyncio.run(go())


# --- the bus's own rules -------------------------------------------------------------------------------------------
def test_the_bus_routes_kinds_folds_the_same_thing_and_forgets_what_is_stale():
    now = [100.0]
    bus = Bus(clock=lambda: now[0], wall=lambda: now[0])
    result, how = bus.publish("researcher-done", '{"status": "done"}', ref="R3")
    assert (result.route, result.owed, how) == ("idle", True, "new")
    assert bus.publish("job-failed", "{}", ref="j1", urgent=True)[0].route == "interrupt"
    assert bus.publish("tool-done", "{}", ref="t1", urgent=True)[0].route == "idle", "only a failure interrupts"
    assert bus.publish("capability-on", "BioCLIP is on", ref="b")[0].route == "context"
    assert bus.publish("slide-moved", "{}", ref="v1", route="context")[0].route == "context", "the watcher's rule"
    assert bus.publish("cell-divided", "{}", ref="v2")[0].route == "interrupt"
    # The same thing again: folded in while it waits; said lately, a duplicate; later on, new.
    again, how = bus.publish("researcher-done", '{"shown": "beside the view"}', ref="R3")
    assert again is result and how == "merged" and "beside the view" in result.text
    bus.sent([result])
    bus.heard(result)
    assert bus.publish("researcher-done", "{}", ref="R3")[1] == "duplicate"
    now[0] += events.SAME_EVENT_S + 1
    assert bus.publish("researcher-done", "{}", ref="R3")[1] == "new"
    # Progress: its own channel, spaced per job, superseded by the job's result.
    p1, _ = bus.publish("progress", "counting", ref="job")
    assert not p1.owed and p1 not in bus.due() and bus.progress_due() is p1
    bus.sent([p1])
    p2, how = bus.publish("progress", "still counting", ref="job")
    assert how == "new" and bus.progress_due() is None
    now[0] += events.PROGRESS_SPACING_S
    assert bus.progress_due() is p2
    bus.publish("job-result", "{}", ref="job")
    assert p2.state == "dropped"
    # A waiting event that is not owed goes stale; a result is kept until it is heard.
    view, _ = bus.publish("focus-drifted", "{}", ref="v3")
    now[0] += events.STALE_S + 1
    dropped = [e for e, _ in bus.prune()]
    assert view in dropped and view.state == "dropped" and bus.result_for("j1").state == "pending"


# --- the brain's one bus, across Talk sessions (step 4b) -----------------------------------------------------------
def test_a_result_that_lands_after_talk_ended_is_said_by_the_next_session_on_the_brains_bus():
    """A think job keeps working when Talk ends; its result lands on the session that started it, closed now. On the
    brain's one bus (events.bus_of) it is still owed, and the next Talk session words and says it, once."""
    from orchestrator.voice.events import bus_of

    async def go():
        brain = SimpleNamespace()
        first, sb1, _ = await session_on(bus_of(brain))
        await first.close()
        await first.deliver("t1", facts={"done": True, "say": "Two size groups."}, say="Two size groups.", points=[],
                            receipt="verified")
        event = bus_of(brain).result_for("t1")
        assert event is not None and event.state == "pending", "owed on the brain's bus, not lost with the session"
        second, sb2, _ = await session_on(bus_of(brain))
        prep = await until_async(lambda: next((r for r in creates(sb2) if r.get("conversation") == "none"), None))
        assert "Two size groups." in given(prep) and "receipt is verified" in given(prep)
        sb2.emit({"type": "response.output_text.delta", "response_id": "resp_1", "delta": "It found two size groups."})
        await done(sb2, "resp_1")
        said = await until_async(lambda: next((r for r in creates(sb2) if r["metadata"]["cause"] == "think"), None))
        assert "It found two size groups." in given(said)
        assert not creates(sb1), "the session that ended says nothing"
        await second.close()
    asyncio.run(go())


def test_a_results_words_follow_its_receipt_and_never_say_done_unless_verified(monkeypatch):
    """deliver: a background result's receipt (hands.py) goes with its facts; its wording is told how to say it, and
    if the voice's own wording does not come back, the receipt's words are said, never "done"."""
    monkeypatch.setattr(openai_realtime, "PREP_WAIT_S", 0.2)

    async def go():
        session, sb, _ = await session_on()
        await session.deliver("photo-1", facts={"tool": "reference_photo", "result": "uncertain"},
                              say="The picture was found, but the studio is not showing it.", points=[],
                              what="reference_photo", receipt="uncertain")
        prep = await until_async(lambda: next((r for r in creates(sb) if r.get("conversation") == "none"), None))
        assert openai_realtime.RECEIPT_RULES["uncertain"] in prep["instructions"]
        assert "never say it is done" in prep["instructions"]
        await done(sb, "resp_1")  # no words came back in time
        said = await until_async(lambda: next((r for r in creates(sb) if r["metadata"]["cause"] == "think"), None))
        assert "the studio is not showing it" in given(said) and "done" not in given(said).split("in your own voice:")[1]
        await session.close()
    asyncio.run(go())


def test_a_result_retired_after_two_cuts_is_one_activity_line(monkeypatch):
    """session.on_retired (Partner.retired): the result is not said a third time, and one Activity line says so, so the
    user can find it and ask."""
    from orchestrator.voice.partner import Partner
    from tests.test_voice_partner import partner_session

    async def go():
        t = await partner_session(monkeypatch)
        lines = []
        monkeypatch.setattr("orchestrator.activity.activity", lambda hub, kind, text, **kw: lines.append((kind, text, kw)))
        assert t.session.on_retired == t.partner.retired
        event, _ = t.session.bus.publish("job-result", '{"done": true}', ref="t9")
        Partner.retired(t.partner, event)
        assert len(lines) == 1 and lines[0][0] == "command" and lines[0][1].startswith("Assistant: not said again")
        assert "a task's result" in lines[0][1] and lines[0][2]["details"]["ids"]["ref"] == "t9"
        assert any(r.get("kind") == "partner-retired" for r in t.logged)
    asyncio.run(go())
