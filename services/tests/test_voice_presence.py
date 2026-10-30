"""The voice's state on screen, sent by the server at each of its moments (voice/presence.py; W3 "alive and available").

Will, live, 29 Oct: after the voice said "the video search is still running", "it just paused. No indication anywhere
the LIVE model was thinking or working". Each test drives the one-brain session on a fake sideband through one moment
and reads the `voice-state` messages the studio was sent: live and idle, the key held, the turn heard, thinking until its
first words, speaking, a tool of its own (thinking), a hand-off in flight (working, with its row's key), a think job,
back to idle, and off."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestrator.voice import openai_realtime, presence
from orchestrator.voice.jobs import Job, jobs_of
from tests.test_voice_partner import done, partner_session, press, release, settle
from tests.waiting import until_async


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(openai_realtime, "RELEASE_TAIL_S", 0.0)
    monkeypatch.setattr(openai_realtime, "PROGRESS_AFTER_S", 60.0)
    monkeypatch.setattr(openai_realtime, "THINK_PROGRESS_AFTER_S", 60.0)
    monkeypatch.setattr(presence, "SETTLE_S", 0.05)
    monkeypatch.setattr(presence, "TICK_S", 0.02)


def states(t) -> list[str]:
    return [m["state"] for m in t.studio if m.get("type") == "voice-state"]


def last(t) -> dict:
    return [m for m in t.studio if m.get("type") == "voice-state"][-1]


async def state_is(t, state: str, **more):
    await until_async(lambda: [m for m in t.studio if m.get("type") == "voice-state"]
                      and last(t)["state"] == state and all(last(t).get(k) == v for k, v in more.items()),
                      what=f"the state {state} {more}")
    return last(t)


def test_live_is_idle_then_listening_heard_thinking_speaking_and_idle_again(monkeypatch):
    async def go():
        t = await partner_session(monkeypatch)
        first = await state_is(t, "idle", n=0)
        assert first["working"] == [] and first["seq"] == 1
        await press(t, 1)
        await state_is(t, "listening")
        await release(t, 1)
        # Released and taken, then its answer asked for: thinking until its first words.
        await state_is(t, "thinking")
        said = states(t)
        assert said.index("listening") < said.index("heard") < said.index("thinking"), said
        t.sb.emit({"type": "output_audio_buffer.started", "response_id": "resp_1"})
        await state_is(t, "speaking")
        await done(t.sb, "resp_1")
        assert last(t)["state"] == "speaking", "generated is not played: still speaking"
        t.sb.emit({"type": "output_audio_buffer.stopped", "response_id": "resp_1"})
        await state_is(t, "idle", n=0)
        logged = [r for r in t.logged if r["kind"] == "partner-state"]
        assert [r["state"] for r in logged] == states(t)
        assert all(isinstance(r["t"], float) for r in logged)
    asyncio.run(go())


def test_a_tool_of_its_own_with_nothing_playing_is_thinking(monkeypatch):
    """The voice waits on its own tool with no words playing: thinking, never a blank."""
    async def go():
        gate = asyncio.Event()

        async def slow(ctx, args):
            await gate.wait()
            return "3 groups"
        t = await partner_session(monkeypatch, tools={"resources": slow})
        await press(t, 1)
        await release(t, 1)
        t.sb.emit({"type": "response.function_call_arguments.done", "response_id": "resp_1", "call_id": "c1",
                   "name": "resources", "arguments": "{}"})
        await done(t.sb, "resp_1")
        await asyncio.sleep(0.15)  # past the settle: the wait on the tool shows
        assert last(t)["state"] == "thinking" and "idle" not in states(t)[1:], states(t)
        gate.set()
        await until_async(lambda: t.sb.responses == 2)
        assert last(t)["state"] == "thinking", "its round's answer asked for: still thinking"
        t.sb.emit({"type": "output_audio_buffer.started", "response_id": "resp_2"})
        await state_is(t, "speaking")
    asyncio.run(go())


def test_a_hand_off_in_flight_is_hooke_working_with_its_rows_key_and_lands_idle(monkeypatch):
    """The video search still running after the voice spoke: Hooke is working · 1, with the hand-off's own key (the
    studio's row and strip use the same), until its result lands."""
    # Past the voice's wait it is answered "still working"; long enough here for the state to settle into working first.
    monkeypatch.setattr("orchestrator.voice.partner_tools.TOOL_WAIT_S", 0.6)

    async def go():
        t = await partner_session(monkeypatch, acts="brain")
        gate = asyncio.Event()

        async def turn(text, channel, **kw):
            await gate.wait()
            return {"answer": "Found a video about stomata.", "actions": [{"tool": "retrieve_page", "ok": True,
                                                                          "summary": "a YouTube video"}]}
        t.brain.turn = turn
        await press(t, 1)
        await release(t, 1)
        t.sb.emit({"type": "response.function_call_arguments.done", "response_id": "resp_1", "call_id": "c1",
                   "name": "do_in_hooke", "arguments": json.dumps({"request": "find a YouTube video about stomata",
                                                                   "label": "A video about stomata"})})
        await done(t.sb, "resp_1")
        working = await state_is(t, "working", n=1)
        handoff = next(iter(t.partner.tools.handoffs.open.values()))
        assert working["working"] == [{"key": f"handoff:{handoff.id}", "label": "A video about stomata",
                                       "since": round(handoff.started, 3)}]
        # Its row's line went to the studio too (no dialogue here): the same id as the state's key.
        assert any(m.get("type") == "handoff" and m["id"] == handoff.id for m in t.studio)
        # Its still-running output is not said (its row shows it: one acknowledgement, the row, one outcome)...
        await until_async(lambda: any(r.get("kind") == "partner-round-quiet" for r in t.logged), seconds=30)
        assert t.sb.responses == 1
        # ...and when the voice speaks over it (another line), speaking, then the hand-off still counted when quiet.
        t.session.respond("proactive", tool_choice="none")
        await until_async(lambda: t.sb.responses >= 2)
        t.sb.emit({"type": "output_audio_buffer.started", "response_id": "resp_2"})
        await state_is(t, "speaking")
        await done(t.sb, "resp_2")
        t.sb.emit({"type": "output_audio_buffer.stopped", "response_id": "resp_2"})
        await state_is(t, "working", n=1)
        gate.set()
        await state_is(t, "idle", n=0)
    asyncio.run(go())


def test_a_think_job_running_counts_as_hooke_working(monkeypatch):
    async def go():
        t = await partner_session(monkeypatch)
        await state_is(t, "idle")
        job = Job(SimpleNamespace(id="j1", request="compare the three models on this field"), project=None,
                  state="running")
        jobs_of(t.hub)[job.id] = job
        working = await state_is(t, "working", n=1)
        assert working["working"][0]["key"] == "job:j1"
        assert working["working"][0]["label"] == "compare the three models on this field"
        job.state = "done"
        await state_is(t, "idle", n=0)
    asyncio.run(go())


def test_leaving_thinking_waits_its_settle_so_one_round_to_the_next_never_flashes_idle(monkeypatch):
    monkeypatch.setattr(presence, "SETTLE_S", 0.3)

    async def go():
        t = await partner_session(monkeypatch)
        await press(t, 1)
        await release(t, 1)
        await state_is(t, "thinking")
        await done(t.sb, "resp_1")  # its words never came: quiet for a moment
        t.session.respond("tool", n=1)  # and the next response asked for within the settle
        await asyncio.sleep(0.4)
        assert states(t)[-1] == "thinking" and states(t).count("thinking") == 1, states(t)
        await until_async(lambda: t.sb.responses == 2)
        await done(t.sb, "resp_2")
        await state_is(t, "idle")
    asyncio.run(go())


def test_a_tap_goes_back_to_idle_and_talk_ending_says_off(monkeypatch):
    async def go():
        t = await partner_session(monkeypatch)
        await state_is(t, "idle")
        await t.partner.command({"type": "talk", "down": True, "n": 1})
        await state_is(t, "listening")
        await t.partner.command({"type": "talk", "down": False, "n": 1})  # shorter than a hold: nothing asked
        await state_is(t, "idle")
        assert "heard" not in states(t)
        await t.session.close()
        await state_is(t, "off", n=0)
        before = len(states(t))
        t.partner.close()
        await settle()
        assert len(states(t)) == before, "off is said once"
    asyncio.run(go())


def test_the_seq_rises_and_an_unchanged_state_is_not_sent_again(monkeypatch):
    async def go():
        t = await partner_session(monkeypatch)
        await state_is(t, "idle")
        await asyncio.sleep(0.1)  # several ticks
        sent = [m for m in t.studio if m.get("type") == "voice-state"]
        assert len(sent) == 1
        await press(t, 1)
        await state_is(t, "listening")
        seqs = [m["seq"] for m in t.studio if m.get("type") == "voice-state"]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    asyncio.run(go())


def test_phase_of_a_bare_session():
    """RealtimeSession.phase with nothing going on, a held key, and closed."""
    s = openai_realtime.RealtimeSession(openai_realtime.VoiceConfig(model="gpt-realtime-2.1", system_prompt=""))
    assert s.phase() == "quiet"
    s._held = 1
    assert s.phase() == "listening"
    s._held = None
    s._speaking = True
    assert s.phase() == "speaking"
    s._speaking = False
    s._heard_n = 2
    assert s.phase() == "heard"
    s._heard_n = None
    s._asks.append(openai_realtime.Ask("proactive", 0, {}, 0))
    assert s.phase() == "quiet", "a line nobody asked for, waiting for quiet, is not the voice thinking"
    s._asks.append(openai_realtime.Ask("turn", 1, {}, 0))
    assert s.phase() == "thinking"
    s.closed = True
    assert s.phase() == "off"


def test_a_hand_off_kept_as_a_job_is_counted_once_by_its_rows_key(monkeypatch):
    """claude/p25-w1 keeps each hand-off as a job too (kind "hooke", the hand-off's id): counted once, under its row's
    key, also while a late step of it runs after the brain answered and its row has left the open hand-offs."""
    async def go():
        t = await partner_session(monkeypatch)
        await state_is(t, "idle")
        handed = SimpleNamespace(id="h9", kind="hooke", label="A video about stomata", request="find a video",
                                 state="running", started=100.0, project=None)
        jobs_of(t.hub)[handed.id] = handed
        working = await state_is(t, "working", n=1)
        assert working["working"] == [{"key": "handoff:h9", "label": "A video about stomata", "since": 100.0}]
        handed.state = "done"
        await state_is(t, "idle", n=0)
    asyncio.run(go())
