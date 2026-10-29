import asyncio
import base64

import numpy as np

from orchestrator.voice.base import ToolSpec, VoiceConfig
from orchestrator.voice.openai_live import OpenAILiveSession, findings_text, resample_16k_to_24k
from tests.waiting import HANG_S


class FakeConn:
    def __init__(self):
        self.sent = []
        self.inbox: asyncio.Queue = asyncio.Queue()
        conn = self

        class Part:
            def __init__(self, kind):
                self.kind = kind

            async def append(self, **kw):
                conn.sent.append((self.kind, kw))

            async def mute(self, **kw):
                conn.sent.append(("mute", kw))

            async def unmute(self, **kw):
                conn.sent.append(("unmute", kw))

        class Session:
            input_audio = Part("audio")
            commentary = Part("commentary")
            thinking = Part("thinking")
            instructions = Part("instructions")

            async def start(self, session):
                conn.sent.append(("start", session))

            async def close(self):
                conn.sent.append(("close", {}))
                await conn.inbox.put({"type": "session.closed", "reason": "client"})

        self.session = Session()

    def __aiter__(self):
        return self

    async def __anext__(self):
        ev = await self.inbox.get()
        if ev is None:
            raise StopAsyncIteration
        return ev


class FakeCM:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return None


def make(handler=None, history=None):
    conn = FakeConn()
    calls = []

    async def look(args):
        calls.append({k: v for k, v in args.items() if k != "say"})
        return await handler(args) if handler else {"answer": "Likely a stoma.", "outlined": ["stoma"]}

    config = VoiceConfig(model="gpt-live-1", system_prompt="hi", tools=[ToolSpec("look", "", {}, look)],
                         history=history or [])
    return OpenAILiveSession(config, connect=lambda: FakeCM(conn)), conn, calls


async def drain(session, n_wait=0.05):
    await asyncio.sleep(n_wait)
    events = []
    while not session._queue.empty():
        events.append(session._queue.get_nowait())
    return events


def test_starts_with_client_delegation_and_24k_audio():
    async def go():
        session, conn, _ = make()
        await session.start()
        kind, cfg = conn.sent[0]
        assert kind == "start"
        assert cfg["model"] == "gpt-live-1" and cfg["delegation"] == {"type": "client"}
        assert cfg["audio"]["format"] == {"type": "audio/pcm", "rate": 24000} and cfg["instructions"] == "hi"
        await session.send_audio(np.zeros(640, np.int16).tobytes())  # 40 ms at 16 kHz
        _, audio = conn.sent[1]
        assert len(base64.b64decode(audio["audio"])) == 960 * 2  # 40 ms at 24 kHz
        await session.close()

    asyncio.run(go())


def test_delegation_uses_the_transcript_and_answers_with_commentary():
    async def go():
        session, conn, calls = make()
        await session.start()
        for piece, (start, end) in zip(["What's the ", "oval in the middle?"], [(200, 400), (400, 800)]):
            await conn.inbox.put({"type": "session.input_transcript.delta", "delta": piece, "start_ms": start, "end_ms": end})
        await conn.inbox.put({"type": "session.delegation.created", "offset_ms": 900,
                              "delegation": {"id": "d1", "type": "delegation", "target": "client"}})
        events = await drain(session, 0.1)
        kinds = [e.kind for e in events]
        finals = [e for e in events if e.kind == "input_transcript" and not e.data.get("interim")]
        assert [e.text for e in finals] == ["What's the oval in the middle?"]
        # The final transcript carries its span on the voice's clock, and the delegation its place on it: the
        # app matches the two (brain/speech.py); the joined words are shown, never what is done.
        assert finals[0].data == {"startMs": 200, "endMs": 800}
        assert calls == [{"question": "What's the oval in the middle?", "delegation_id": "d1", "offset_ms": 900}]
        assert "tool_call" in kinds and "tool_result" in kinds
        kind, kw = [s for s in conn.sent if s[0] == "commentary"][0]
        assert kw["delegation_id"] == "d1" and "stoma" in kw["content"]
        await session.close()

    asyncio.run(go())


def test_user_speech_during_model_audio_is_an_interruption():
    async def go():
        session, conn, _ = make()
        await session.start()
        silence = base64.b64encode(np.zeros(240, np.int16).tobytes()).decode()
        speech = base64.b64encode((np.sin(np.arange(240)) * 8000).astype(np.int16).tobytes()).decode()
        # Idle silence streams continuously and must not count as the model speaking.
        await conn.inbox.put({"type": "session.output_audio.delta", "delta": silence, "start_ms": 0, "end_ms": 10})
        await conn.inbox.put({"type": "session.input_transcript.delta", "delta": "hello"})
        assert "interrupted" not in [e.kind for e in await drain(session)]
        await conn.inbox.put({"type": "session.output_audio.delta", "delta": speech, "start_ms": 10, "end_ms": 20})
        await conn.inbox.put({"type": "session.input_transcript.delta", "delta": "wait"})
        kinds = [e.kind for e in await drain(session)]
        assert kinds.index("audio") < kinds.index("interrupted") < kinds.index("turn_complete")
        await session.close()

    asyncio.run(go())


def test_findings_text():
    assert findings_text({"answer": "A stoma.", "outlined": ["stoma", "stoma"]}) == "A stoma. Outlined on screen: stoma, stoma."
    assert "could not look" in findings_text({"error": "no fresh camera frame"})


def test_a_reworded_answer_is_not_said_again_only_one_the_receipts_contradict():
    """Live 2026-10-10: 6 of 7 voice replies were followed by "Correction to what you said" for a rewording."""
    zoomed = {"tool": "zoom", "ok": True, "summary": "zoomed in on the eye"}
    missed = {"tool": "zoom", "ok": False, "summary": "this answer outlined nothing, so there is no 'target 1' yet"}
    reworded = findings_text({"answer": "I zoomed in on your left eye.", "actions": [zoomed]},
                             said="Zooming in on the eye.")
    assert "Correction" not in reworded and "Done on screen: zoomed in on the eye" in reworded
    failed = findings_text({"answer": "I could not zoom: nothing was outlined yet.", "actions": [missed]},
                           said="Zooming in on the eye.")
    assert failed.startswith("Correction to what you said: I could not zoom") and "Could not:" in failed
    assert "no answer" in findings_text({"answer": "", "actions": []})


def test_resample_length_and_shape():
    tone = (np.sin(np.linspace(0, 20, 1600)) * 10000).astype(np.int16)
    out = np.frombuffer(resample_16k_to_24k(tone.tobytes()), np.int16)
    assert len(out) == 2400 and abs(int(out.max()) - int(tone.max())) < 200


def test_the_servers_word_times_are_recorded_with_their_lag():
    # W0-5: start_ms/end_ms/offset_ms are kept with how far they lag our clock, so the voice
    # report can say whether they could replace the fixed waits (they are not used yet).
    async def go():
        session, conn, _ = make()
        await session.start()
        await conn.inbox.put({"type": "session.started", "session": {"id": "s1"}})
        await asyncio.sleep(0.05)
        await conn.inbox.put({"type": "session.input_transcript.delta", "delta": "what is", "start_ms": 0, "end_ms": 20})
        await conn.inbox.put({"type": "session.delegation.created", "offset_ms": 30, "delegation": {"id": "d1"}})
        events = await drain(session, 0.1)
        piece = next(e for e in events if e.kind == "input_transcript" and e.data.get("interim"))
        assert piece.data["startMs"] == 0 and piece.data["endMs"] == 20
        assert isinstance(piece.data["endLagMs"], int) and piece.data["endLagMs"] >= 0
        call = next(e for e in events if e.kind == "tool_call")
        assert call.data["offsetMs"] == 30 and "offsetLagMs" in call.data
        await session.close()

    asyncio.run(go())


def test_no_server_times_means_nothing_recorded():
    async def go():
        session, conn, _ = make()
        await session.start()
        await conn.inbox.put({"type": "session.input_transcript.delta", "delta": "hello"})
        piece = next(e for e in await drain(session) if e.kind == "input_transcript")
        assert piece.data == {"interim": True, "delta": "hello", "speechSession": None}
        assert "startMs" not in piece.data and "at" not in piece.data
        await session.close()

    asyncio.run(go())


def test_the_session_starts_knowing_the_recap_as_its_history():
    async def go():
        session, conn, _ = make(history=[("developer", "Earlier: a stem cross-section."), ("user", " ")])
        await session.start()
        _, cfg = conn.sent[0]
        assert cfg["input"] == [{"type": "message", "role": "developer",
                                 "content": [{"type": "input_text", "text": "Earlier: a stem cross-section."}]}]
        await session.close()

    asyncio.run(go())


def test_the_answer_is_said_as_soon_as_it_is_written_then_only_what_happened():
    async def go():
        async def brain(args):
            await args["say"]("Outlining the stomata now.")
            return {"answer": "Outlining the stomata now.", "outlined": ["stoma", "stoma"]}

        session, conn, _ = make(brain)
        await session.start()
        await conn.inbox.put({"type": "session.input_transcript.delta", "delta": "highlight the stomata"})
        await conn.inbox.put({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        await drain(session, 0.1)
        said = [kw["content"] for kind, kw in conn.sent if kind == "commentary"]
        assert said == ["Outlining the stomata now.", "Outlined on screen: stoma, stoma."]
        await session.close()

    asyncio.run(go())


def test_a_rewritten_answer_is_said_again_as_a_correction():
    async def go():
        async def brain(args):
            await args["say"]("Outlining the stomata now.")
            return {"answer": "I couldn't outline them: the slide moved.", "outlined": []}

        session, conn, _ = make(brain)
        await session.start()
        await conn.inbox.put({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        await drain(session, 0.1)
        said = [kw["content"] for kind, kw in conn.sent if kind == "commentary"]
        assert said[1] == "Correction to what you said: I couldn't outline them: the slide moved."
        await session.close()

    asyncio.run(go())


def test_closing_the_session_does_not_cancel_delegated_work():
    async def go():
        finished = asyncio.Event()

        async def brain(args):
            await asyncio.sleep(0.05)
            finished.set()
            return {"answer": "A stoma."}

        session, conn, _ = make(brain)
        await session.start()
        await conn.inbox.put({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        await asyncio.sleep(0.01)
        await session.close()  # the user stopped talking right after asking
        await asyncio.wait_for(finished.wait(), HANG_S)
        assert not [s for s in conn.sent if s[0] == "commentary"]  # nobody left to say it to

    asyncio.run(go())


def test_mute_tells_the_session_and_notes_carry_an_event_id():
    async def go():
        session, conn, _ = make()
        await session.start()
        await session.mute(True)
        await session.mute(False)
        await session.inject("The view: a stem.", respond=False)
        kinds = [k for k, _ in conn.sent]
        assert "mute" in kinds and "unmute" in kinds
        _, kw = [s for s in conn.sent if s[0] == "thinking"][0]
        assert kw["event_id"] == "note-1"
        await conn.inbox.put({"type": "session.thinking.appended", "client_event_id": "note-1"})
        events = await drain(session)
        assert [e.data["eventId"] for e in events if e.kind == "note_ack"] == ["note-1"]
        await session.close()

    asyncio.run(go())


def test_24k_microphone_audio_passes_through_unchanged():
    async def go():
        conn = FakeConn()
        session = OpenAILiveSession(VoiceConfig(model="gpt-live-1", input_rate=24000), connect=lambda: FakeCM(conn))
        await session.start()
        pcm = (np.arange(960) % 100).astype(np.int16).tobytes()
        await session.send_audio(pcm)
        assert base64.b64decode([kw for k, kw in conn.sent if k == "audio"][0]["audio"]) == pcm
        await session.close()

    asyncio.run(go())


def test_webrtc_makes_the_session_from_the_offer_and_listens_on_a_sideband():
    async def go():
        conn = FakeConn()
        made, attached = [], []

        async def create(**kw):
            made.append(kw)
            return {"session": {"id": "live_s1"}, "transport": {"type": "webrtc", "sdp": "v=0 answer"}}

        def sideband(session_id):
            attached.append(session_id)
            return FakeCM(conn)

        calls = []

        async def look(args):
            calls.append(args["question"])
            return {"answer": "A stoma.", "outlined": ["stoma"]}

        config = VoiceConfig(model="gpt-live-1", system_prompt="hi", tools=[ToolSpec("look", "", {}, look)],
                             history=[("developer", "Earlier: a leaf.")])
        session = OpenAILiveSession(config, link="webrtc", create=create, sideband=sideband)
        await session.start(offer_sdp="v=0 offer")
        cfg = made[0]["session"]
        assert made[0]["transport"] == {"type": "webrtc", "sdp": "v=0 offer"}
        assert "format" not in cfg.get("audio", {}) and cfg["delegation"] == {"type": "client"}
        assert cfg["client"] == {"data_channel": {"allowed_client_events": [], "allowed_server_events": []}}
        assert cfg["input"][0]["content"][0]["text"] == "Earlier: a leaf."
        assert session.answer_sdp == "v=0 answer" and attached == ["live_s1"]
        assert not [k for k, _ in conn.sent if k == "start"]  # a sideband never starts the session
        await session.send_audio(np.zeros(960, np.int16).tobytes())
        speech = base64.b64encode((np.sin(np.arange(240)) * 8000).astype(np.int16).tobytes()).decode()
        await conn.inbox.put({"type": "session.output_audio.delta", "delta": speech})
        await conn.inbox.put({"type": "session.input_transcript.delta", "delta": "what is that?"})
        await conn.inbox.put({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        events = await drain(session, 0.1)
        assert not [k for k, _ in conn.sent if k == "audio"]  # the browser sends its microphone itself
        assert "audio" not in [e.kind for e in events]  # ...and plays the model's speech itself
        assert calls == ["what is that?"]
        assert [kw["content"] for k, kw in conn.sent if k == "commentary"] == ["A stoma. Outlined on screen: stoma."]
        await session.close()

    asyncio.run(go())


def test_a_barge_in_is_said_once_and_the_models_turn_ends_only_when_its_audio_stops(monkeypatch):
    """The review of #200 (SF1): after the user spoke over the voice, its turn never ended until it spoke again,
    so a reply cut off and the next one were one line ("...longSure..."). The barge-in is said once, and the turn
    ends when the model's audio stops; a model that talks on through the user's words is still one turn."""
    import orchestrator.voice.openai_live as live

    monkeypatch.setattr(live, "QUIET_TURN_END_S", 0.1)

    async def go():
        session, conn, _ = make()
        await session.start()
        speech = base64.b64encode((np.sin(np.arange(240)) * 8000).astype(np.int16).tobytes()).decode()
        await conn.inbox.put({"type": "session.output_audio.delta", "delta": speech, "start_ms": 0, "end_ms": 10})
        await conn.inbox.put({"type": "session.input_transcript.delta", "delta": "wait"})
        await conn.inbox.put({"type": "session.input_transcript.delta", "delta": " a moment"})
        first = [(e.kind, (e.data or {}).get("reason")) for e in await drain(session, 0.02)]
        assert first.count(("interrupted", None)) == 1 and first.count(("turn_complete", "user_spoke")) == 1
        # it talks on through the user's words: no turn end while its audio goes on
        for i in range(3):
            await conn.inbox.put({"type": "session.output_audio.delta", "delta": speech, "start_ms": 10 + i, "end_ms": 11 + i})
            await asyncio.sleep(0.05)
        talking = [(e.kind, (e.data or {}).get("reason")) for e in await drain(session, 0.0)]
        assert ("turn_complete", "quiet") not in talking
        # then it stops: its turn ends
        ended = [(e.kind, (e.data or {}).get("reason")) for e in await drain(session, 0.5)]
        assert ("turn_complete", "quiet") in ended
        await session.close()

    asyncio.run(go())


def test_a_voice_cut_off_by_the_user_ends_its_turn_when_its_audio_stops(monkeypatch):
    """The other case of the review of #200 (SF1): the voice stops as the user speaks over it; its turn (its line)
    ends then, so its next reply is a new line, never glued to the cut-off one ("...longSure...")."""
    import orchestrator.voice.openai_live as live

    monkeypatch.setattr(live, "QUIET_TURN_END_S", 0.1)

    async def go():
        session, conn, _ = make()
        await session.start()
        speech = base64.b64encode((np.sin(np.arange(240)) * 8000).astype(np.int16).tobytes()).decode()
        await conn.inbox.put({"type": "session.output_audio.delta", "delta": speech, "start_ms": 0, "end_ms": 10})
        await conn.inbox.put({"type": "session.input_transcript.delta", "delta": "stop"})  # no more model audio
        events = [(e.kind, (e.data or {}).get("reason")) for e in await drain(session, 0.5)]
        assert ("turn_complete", "user_spoke") in events and ("turn_complete", "quiet") in events
        await session.close()

    asyncio.run(go())


def test_a_step_still_running_after_the_answer_was_said_is_not_said_again():
    """Live 2026-10-11 (second stack, by voice): "Can you pull up an image of a stoma" was answered aloud as it was
    written ("Retrieving a reference photo"), then the hand-off's findings, which held nothing new but the step still
    running, were spoken again: "Still pulling that photo. Still at it." Findings with only that go to the voice as
    silent context on its delegation; the outcome is spoken when it lands (announcer.late_tool)."""
    async def go():
        async def brain(args):
            await args["say"]("Getting a photo of a stoma now.")
            return {"answer": "I'm still retrieving a reference photo of a stoma.",
                    "actions": [{"tool": "reference_photo", "ok": None, "summary": "still working"}]}

        session, conn, _ = make(brain)
        await session.start()
        await conn.inbox.put({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        await drain(session, 0.1)
        said = [kw["content"] for kind, kw in conn.sent if kind == "commentary"]
        assert said == ["Getting a photo of a stoma now."]  # once
        silent = [kw for kind, kw in conn.sent if kind == "thinking"]
        assert silent and silent[-1]["delegation_id"] == "d1" and "reference_photo" in silent[-1]["content"]
        await session.close()

    asyncio.run(go())


def test_a_step_still_running_is_never_said_even_when_nothing_was_said_yet():
    """The user's 2026-10-11 decision: a spoken request is the voice's "Sure", its card in the chat, and the outcome
    when it lands; "It's still being done. I'll put it up as soon as it comes through." was noise. Findings that hold
    only steps still running go to the voice silently, said or not before."""
    async def go():
        async def brain(args):  # no early answer: still nothing to say until the outcome
            return {"answer": "Getting a photo of a stoma.",
                    "actions": [{"tool": "reference_photo", "ok": None, "summary": "still working"}]}

        session, conn, _ = make(brain)
        await session.start()
        await conn.inbox.put({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        await drain(session, 0.1)
        assert [kw for kind, kw in conn.sent if kind == "commentary"] == []
        silent = [kw for kind, kw in conn.sent if kind == "thinking"]
        assert silent and silent[-1]["delegation_id"] == "d1" and "reference_photo" in silent[-1]["content"]
        await session.close()

    asyncio.run(go())


def test_a_voice_requests_late_outcome_is_said_even_without_a_model_answer():
    """Live 2026-10-11 12:21: the photo search failed after the hand-off, its late explanation timed out, and the
    failure reached the voice only as silent context; it improvised "I found the photo, it's on your screen". The
    outcome of what was asked by voice is said, in the voice's own words from the facts (narrator: notify)."""
    from types import SimpleNamespace

    from orchestrator.announcer import Announcer
    from orchestrator.brain.registry import ToolContext, ToolRun

    offered = []
    announcer = Announcer(conversation=None, dialogue=SimpleNamespace(show=lambda *a, **k: None),
                          narrator=SimpleNamespace(offer=offered.append), presenter=None, research=None, scene=None,
                          voice=lambda: None, note_to_voice=lambda *a, **k: None, log=lambda record: None)
    voice = SimpleNamespace(closed=False)
    ctx = ToolContext("voice", 7, None, "Can you pull up an image of a stoma")
    ctx.voice_owner, ctx.delegation_id = voice, "d1"
    run = ToolRun("reference_photo", {"subject": "plant stoma"}, ok=False,
                  summary="no reference photo of plant stoma returned by this lookup: web search returned no displayable photo")
    asyncio.run(announcer.late_tool(ctx, run))
    assert offered and offered[0].notify is True and offered[0].delegation_id == "d1"
    assert offered[0].provenance == "data" and offered[0].delivery == "interrupt"


def _announcer(offered, shown):
    from types import SimpleNamespace

    from orchestrator.announcer import Announcer

    return Announcer(conversation=None, dialogue=SimpleNamespace(show=lambda line, asked=None: shown.append(line)),
                     narrator=SimpleNamespace(offer=offered.append), presenter=None, research=None, scene=None,
                     voice=lambda: None, note_to_voice=lambda *a, **k: None, log=lambda record: None)


def test_a_late_step_that_starts_waiting_is_shown_on_its_row_and_told_not_said():
    """One request: one acknowledgement, its card, one outcome (the user's 2026-10-11 decision). A wait (a photo up
    in a studio out of view, a model loading) is on its row in the chat and told to the voice silently, so it can
    answer "what's taking so long?" without improvising (live 2026-10-11: "It's not on your screen yet...")."""
    from types import SimpleNamespace

    from orchestrator.brain.registry import Registry, ToolContext, ToolRun

    offered, shown = [], []
    announcer = _announcer(offered, shown)
    registry = Registry()
    registry.progressed.append(announcer.progressed)
    ctx = ToolContext("voice", 7, None, "Can you pull up an image of a stoma")
    ctx.voice_owner, ctx.delegation_id = SimpleNamespace(closed=False), "d1"
    run = ToolRun("reference_photo", {})
    run._on_progress = lambda changed: [listener(changed, ctx) for listener in registry.progressed]
    run.report_progress("loading micro-sam")  # before its answer: the answer carries it, nothing more
    assert not offered and not shown
    run.late = True
    run.report_progress("waiting for the studio to be in view")
    run.report_progress("waiting for the studio to be in view")  # no change, nothing again
    assert len(offered) == 1 and offered[0].delivery == "silent" and not offered[0].notify
    assert "waiting for the studio to be in view" in offered[0].text
    assert len(shown) == 1 and shown[0]["type"] == "chat-action" and shown[0]["progress"].startswith("waiting")


def test_a_typed_requests_late_wait_is_on_its_row_and_never_spoken():
    from orchestrator.brain.registry import ToolContext, ToolRun

    offered, shown = [], []
    announcer = _announcer(offered, shown)
    ctx = ToolContext("chat", 9, None, "pull up a photo of a stoma")
    run = ToolRun("reference_photo", {}, late=True)
    run._on_progress = lambda changed: announcer.progressed(changed, ctx)
    run.report_progress("waiting for the studio to be in view")
    assert not offered and len(shown) == 1 and shown[0]["turnId"] == 9


def test_findings_name_what_a_running_step_waits_on_but_a_wait_is_never_said():
    from orchestrator.voice.openai_live import only_in_progress, split_findings

    result = {"answer": "Getting a photo.", "actions": [{"tool": "reference_photo", "ok": None,
                                                        "progress": "waiting for the studio to be in view"}]}
    found = split_findings(result, said="Getting a photo.")
    # A fact for the model (Part 2.5: what only the model should know is never in what the voice is asked to say).
    assert "reference_photo (waiting for the studio to be in view)" in found.facts and "waiting" not in found.spoken
    assert only_in_progress(result)  # told to the voice silently; the row in the chat shows the wait


def test_every_silent_note_is_logged_whole_with_where_it_came_from_and_the_session():
    """The demo review of 2026-10-11: what the voice held when it answered could not be read back. Every piece of
    silent context is logged as it is sent (voice-note): its whole text, its source, its event id (the note_ack's) and
    the session's id; what the voice is asked to say (commentary) is not a note."""
    async def go():
        async def brain(args):
            await args["say"]("Getting a photo of a stoma now.")
            return {"answer": "Still getting it.", "actions": [{"tool": "reference_photo", "ok": None, "summary": "working"}]}

        session, conn, _ = make(brain)
        logged = []
        session.noted = logged.append
        await session.start()
        await conn.inbox.put({"type": "session.started", "session": {"id": "sess-1"}})
        await drain(session)
        await session.inject("View notes: a stem.", respond=False, source="view", facts={"why": ["session start"]})
        await session.inject("Say this.", respond=True)
        await conn.inbox.put({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        await drain(session, 0.1)
        sent = [kw["content"] for kind, kw in conn.sent if kind == "thinking"]
        assert [r["source"] for r in logged] == ["view", "in-progress"]
        view, running = logged
        assert view["kind"] == "voice-note" and view["eventId"] == "note-1" and view["sessionId"] == "sess-1"
        assert view["text"] == sent[0] and view["text"].endswith("View notes: a stem.") and view["chars"] == len(sent[0])
        assert view["why"] == ["session start"] and "delegationId" not in view
        assert running["eventId"] == "note-2" and running["delegationId"] == "d1" and running["text"] == sent[1]
        await session.close()

    asyncio.run(go())


def test_a_steps_outcome_said_as_its_late_result_is_not_said_again_by_the_hand_off():
    """Voice journeys run 4 (2026-10-11): the photo's outcome was said as its late result and again in the hand-off's
    findings. A step whose outcome the voice was asked to say is never asked twice (session.spoken_actions)."""
    async def go():
        async def brain(args):
            return {"answer": "I've pulled up a photo of a stoma.",
                    "actions": [{"tool": "reference_photo", "ok": True, "summary": "shown", "actionId": "a1"}]}

        session, conn, _ = make(brain)
        await session.start()
        session.spoken_actions.add("a1")  # the narrator said it as the step's late result
        await conn.inbox.put({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        await drain(session, 0.1)
        assert [kw for kind, kw in conn.sent if kind == "commentary"] == []
        assert any("already said" in kw["content"] for kind, kw in conn.sent if kind == "thinking")
        await session.close()

    asyncio.run(go())


def test_shutdown_retains_final_usage_and_requires_provider_close_ack(monkeypatch):
    async def run():
        session, conn, _ = make()
        await session.start()
        async def closing():
            await conn.inbox.put({"type": "session.usage.updated", "usage": {"seconds": 3.5}})
            await conn.inbox.put({"type": "session.closed", "reason": "close_requested", "usage": {"seconds": 4.0}})
        conn.session.close = closing
        await session.close()
        assert session.termination == 'confirmed'
        assert [event.data['seconds'] for event in session._queue._queue if event.kind == 'usage'] == [3.5, 4.0]
        missing, conn, _ = make()
        await missing.start()
        async def no_ack():
            pass
        conn.session.close = no_ack
        monkeypatch.setattr('orchestrator.voice.openai_live.CLOSE_STEP_S', 0.01)
        await missing.close()
        assert 'unconfirmed' in missing.termination
    asyncio.run(run())


# --- Part 2.5, PR 1: what the voice says, said once (the live voice session of 2026-10-28) ---------------------------

def commentary(conn):
    return [kw["content"] for kind, kw in conn.sent if kind == "commentary"]


def thinking(conn):
    return [kw["content"] for kind, kw in conn.sent if kind == "thinking"]


OUTLINED_341 = {"tool": "outline_cells", "ok": True, "summary": "outlined 341 nuclei with Cellpose-SAM",
                "requestId": "r1", "actionId": "a1"}


def test_one_outcome_is_said_once_per_request_whichever_delegation_or_session_reports_it():
    """Live 2026-10-28 (log lines 14-17): one Cellpose-SAM outline, "341 nuclei, outlines on the picture", was said three
    times. Every delegation, replay or new Talk session that reports the same request's outcome carries the request's
    identity (requestId), and the voice is asked to say it once: by identity, never by comparing words."""
    from orchestrator.brain.narrator import Said

    async def go():
        async def brain(args):
            return {"requestId": "r1", "answer": "Cellpose-SAM found 341 nuclei; the outlines are on the picture.",
                    "outlined": ["nucleus"] * 3, "outlinedIds": ["o1", "o2", "o3"], "actions": [dict(OUTLINED_341)]}

        said = Said()  # the brain's: it outlives one Talk session
        session, conn, _ = make(brain)
        session.adopt_said(said)
        await session.start()
        await conn.inbox.put({"type": "session.delegation.created", "event_id": "e1", "delegation": {"id": "d1"}})
        await drain(session, 0.1)
        # the voice hands the same request over again (after a cough), and the provider replays d1
        await conn.inbox.put({"type": "session.delegation.created", "event_id": "e2", "delegation": {"id": "d2"}})
        await conn.inbox.put({"type": "session.delegation.created", "event_id": "e3", "delegation": {"id": "d1"}})
        await drain(session, 0.1)
        assert len(commentary(conn)) == 1 and "341" in commentary(conn)[0]
        assert any("already said" in t for t in thinking(conn))
        await session.close()
        # Talk is pressed again: a new session, the same brain
        again, conn2, _ = make(brain)
        again.adopt_said(said)
        await again.start()
        await conn2.inbox.put({"type": "session.delegation.created", "event_id": "e4", "delegation": {"id": "d9"}})
        await drain(again, 0.1)
        assert commentary(conn2) == []
        await again.close()

    asyncio.run(go())


def test_a_late_raw_result_already_said_is_told_but_the_models_own_late_answer_is_said():
    """The findings said the step's outcome; its raw late result is then told, not said again. The model's own late
    answer about it has an identity of its own and is said (review defect 2: never silenced by a raw receipt)."""
    from types import SimpleNamespace

    from orchestrator.announcer import Announcer
    from orchestrator.brain import narrator as N
    from orchestrator.brain.registry import ActionOccurrence, ToolContext, ToolRun

    async def go():
        async def brain(args):
            return {"requestId": "r1", "answer": "Cellpose-SAM found 341 nuclei.", "actions": [dict(OUTLINED_341)]}

        session, conn, _ = make(brain)
        await session.start()
        await conn.inbox.put({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        await drain(session, 0.1)
        assert len(commentary(conn)) == 1
        session.last_speech_at = lambda: time.monotonic()  # the voice is heard saying what it is asked to
        narrator = N.Narrator(lambda: session, chat=lambda *a, **k: None)
        announcer = Announcer(conversation=None, dialogue=SimpleNamespace(show=lambda *a, **k: None), narrator=narrator,
                              presenter=None, research=None, scene=None, voice=lambda: session,
                              note_to_voice=lambda *a, **k: None, log=lambda record: None)
        ctx = ToolContext("voice", 7, None, "outline the nuclei", "d1")
        ctx.voice_owner = session

        async def late(answer):
            run = ToolRun("outline_cells", {}, ok=True, summary=OUTLINED_341["summary"], answer=answer)
            run.occurrence = ActionOccurrence("r1", "a1")
            await announcer.late_tool(ctx, run)
            for _ in range(40):
                await asyncio.sleep(0.05)
                if not narrator.waiting and (narrator._task is None or narrator._task.done()):
                    return

        await late(None)
        assert len(commentary(conn)) == 1  # the raw result: said already by the findings, told
        await late("I have outlined 341 cells in the picture using Cellpose-SAM.")
        assert len(commentary(conn)) == 2 and "341 cells" in commentary(conn)[1]  # the model's own answer: said
        await session.close()

    import time
    asyncio.run(go())


def test_an_outcome_told_silently_is_still_said_by_the_hand_off():
    """Review defect 1: a request the app did without a hand-off offers its outcome to the narrator as context (it
    was never asked to be said; a failure too). That claims nothing: the hand-off's findings for the same request say
    it, the failure included."""
    from orchestrator.brain import narrator as N
    from orchestrator.brain.speech import outcome_line

    failed = {"requestId": "r1", "answer": "I couldn't select it.", "outlined": [], "actions": [
        {"tool": "select", "ok": False, "summary": "no plant cell is outlined", "actionId": "a1"}]}

    async def go():
        async def brain(args):
            return dict(failed)

        session, conn, _ = make(brain)
        await session.start()
        narrator = N.Narrator(lambda: session, chat=lambda *a, **k: None)
        narrator.offer(outcome_line(dict(failed), session, delivery="interrupt"))
        for _ in range(20):
            await asyncio.sleep(0.05)
            if not narrator.waiting and (narrator._task is None or narrator._task.done()):
                break
        assert commentary(conn) == [] and any("no plant cell" in t for t in thinking(conn))
        await conn.inbox.put({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        await drain(session, 0.1)
        assert len(commentary(conn)) == 1 and "Could not: no plant cell is outlined" in commentary(conn)[0]
        await session.close()

    asyncio.run(go())


def test_what_a_closed_session_did_not_say_a_new_one_says():
    """Review defect 5: the record of what was said is the brain's, shared by Talk sessions; an outcome is marked
    said only once it was sent. One whose session closed while it waited for the user, or whose send failed, is said
    by the next session or hand-off."""
    import orchestrator.voice.openai_live as live
    from orchestrator.brain.narrator import Said

    async def go():
        heard_more = asyncio.Event()

        async def brain(args):
            if args.get("delegation_id") == "d1":
                await heard_more.wait()  # the answer comes while the user is still talking
            return {"requestId": "r1", "answer": "Researcher 3 is still running.", "outlined": [], "actions": []}

        said = Said()
        first, conn, _ = make(brain)
        first.adopt_said(said)
        await first.start()
        await conn.inbox.put({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        await asyncio.sleep(0.05)
        await conn.inbox.put({"type": "session.input_transcript.delta", "delta": "and also the other one"})
        await asyncio.sleep(0.05)
        heard_more.set()
        await asyncio.sleep(0.1)
        await first.close()  # Talk ends while the findings wait for the user
        await asyncio.sleep(live.QUIET_TURN_END_S + 0.3)
        assert commentary(conn) == [] and "answer:r1" not in said
        failing, conn2, _ = make(brain)
        failing.adopt_said(said)
        await failing.start()
        sends = conn2.session.commentary.append

        async def broken(**kw):
            raise ConnectionError("socket closed")

        conn2.session.commentary.append = broken
        await conn2.inbox.put({"type": "session.delegation.created", "delegation": {"id": "d2"}})
        await drain(failing, 0.1)
        assert "answer:r1" not in said  # the send failed: not said
        conn2.session.commentary.append = sends
        await failing.close()
        again, conn3, _ = make(brain)
        again.adopt_said(said)
        await again.start()
        await conn3.inbox.put({"type": "session.delegation.created", "delegation": {"id": "d3"}})
        await drain(again, 0.1)
        assert len(commentary(conn3)) == 1 and "answer:r1" in said
        await again.close()

    asyncio.run(go())


def test_findings_keep_model_facts_apart_from_what_is_said():
    """Live 2026-10-28: "Nothing's outlined" was said after a table, a researcher, a status and a tab request. The
    findings say apart what the voice should say (spoken) and what only the model should know (facts)."""
    from orchestrator.voice.openai_live import split_findings

    for tool, summary, answer in (
            ("show_measurements", "opened the measurements table", "I've opened the measurements table."),
            ("start_researcher", "Researcher 2 is making a research note", "I'm starting a researcher on that table."),
            ("research_status", "Researcher 3 is running", "Researcher 3 is still working."),
            ("tabs", "made a tab called Measurements", "There's a new tab called Measurements.")):
        result = {"requestId": "r1", "answer": answer, "outlined": [],
                  "actions": [{"tool": tool, "ok": True, "summary": summary, "actionId": "a1"}]}
        found = split_findings(result)
        assert "outlined" not in found.spoken.lower() and "outlined" not in found.facts.lower(), tool
        assert answer in found.spoken and summary in found.spoken
        assert findings_text(result) == found.spoken
    # An outline was asked for and nothing came of it: the model knows, and the voice says it once, with what next.
    asked = {"requestId": "r2", "answer": "Here are the stomata.", "outlined": [], "targets": 2, "actions": []}
    found = split_findings(asked)
    assert "Nothing was outlined" in found.facts
    assert "Nothing could be outlined" in found.spoken and "next" in found.spoken
    assert "nothing-outlined:r2" in found.outcomes
    failed = {"requestId": "r3", "answer": "", "outlined": [], "actions": [
        {"tool": "outline_cells", "ok": False, "summary": "Cellpose-SAM could not load", "next": "try again in a minute",
         "actionId": "a3"}]}
    found = split_findings(failed)
    assert "Could not: Cellpose-SAM could not load" in found.spoken and "try again in a minute" in found.spoken
    assert found.spoken.count("outline") <= 1  # the failure says it; no second "nothing outlined"


def test_nothing_outlined_is_not_said_after_a_table_request():
    """Log lines 26-28: "I've brought up the measurements table ... And nothing's outlined right now"."""
    async def go():
        async def brain(args):
            return {"requestId": "r1", "answer": "The measurements table is open.", "outlined": [],
                    "actions": [{"tool": "show_measurements", "ok": True, "summary": "opened the measurements table",
                                 "actionId": "a1"}]}

        session, conn, _ = make(brain)
        await session.start()
        await conn.inbox.put({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        await drain(session, 0.1)
        assert commentary(conn) and not any("utlined" in c for c in commentary(conn) + thinking(conn))
        await session.close()

    asyncio.run(go())


def test_a_draft_is_said_with_its_pending_steps_marked_not_done():
    """Log line 28: "I've brought up the measurements table" was said before the table had been brought up. A draft
    that waits on steps (Brain.turn: claims.Draft) is said with those steps named as not done yet, so only what it
    says of them waits for their outcome (the model judges which words those are); its answer is never held back
    (review defect 3: "What is that? Get me a reference photo" must still say "That's a diatom")."""
    from orchestrator.brain.claims import Draft

    async def go():
        async def brain(args):
            await args["say"](Draft("That's a diatom. I'm getting you a reference photo.", pending=("reference_photo",)))
            await asyncio.sleep(0.05)
            return {"requestId": "r1", "answer": "That's a diatom. I'm getting you a reference photo.", "outlined": [],
                    "actions": [{"tool": "reference_photo", "ok": None, "summary": "", "actionId": "a1"}]}

        session, conn, _ = make(brain)
        await session.start()
        await conn.inbox.put({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        await drain(session, 0.2)
        said = commentary(conn)
        assert len(said) == 1 and "That's a diatom" in said[0]  # the answer is said, once
        assert "reference_photo" in said[0] and "never that it is done" in said[0]  # the pending step: not done yet
        await session.close()

    asyncio.run(go())


def test_findings_wait_while_the_user_is_talking(monkeypatch):
    """Log lines 102-106: the voice answered "That one was cancelled ... What I see now are ..." while the user was
    saying "No, it wasn't cancelled. The researcher, researcher number three": "why are you talking over me?". What
    the app asks the voice to say waits while the user's words are still coming (bounded); when they asked something
    newer meanwhile, the earlier outcome is told, not said over them."""
    import orchestrator.voice.openai_live as live

    monkeypatch.setattr(live, "HOLD_FOR_USER_S", 2.0)

    async def go():
        release = asyncio.Event()

        async def brain(args):
            await release.wait()
            return {"requestId": "r1", "answer": "Researcher 3 is still running.", "outlined": [], "actions": []}

        session, conn, _ = make(brain)
        from types import SimpleNamespace

        # Wired as the route wires it: the session knows which request a delegation is; what counts as newer is the
        # spoken requests' own business, and the words heard meanwhile are only words.
        session.requests = session.spoken = SimpleNamespace(request_for=lambda did: "r1", newest=lambda rid: False)
        await session.start()
        await conn.inbox.put({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        await drain(session, 0.05)
        for piece in ("No, it wasn't ", "cancelled. The researcher, ", "researcher number three"):
            await conn.inbox.put({"type": "session.input_transcript.delta", "delta": piece})
            await asyncio.sleep(0.15)
            release.set()
        assert commentary(conn) == []  # not over the user's words
        await asyncio.sleep(live.QUIET_TURN_END_S + 0.5)
        # Said once their words stopped coming, whatever they were (a cough, "thanks", talk to someone in the room):
        # noise never turns an answer into silent context (review defect 4).
        assert len(commentary(conn)) == 1
        await session.close()

    asyncio.run(go())


def test_gpt_live_turn_taking_is_the_models_own_and_the_session_asks_for_none():
    """OpenAI's GPT-Live documents no turn detection or VAD settings (checked 2026-10-28, see the module's notes):
    the model decides when to speak and yield. The session sends only documented fields."""
    async def go():
        session, conn, _ = make()
        await session.start()
        _, cfg = conn.sent[0]
        assert set(cfg) <= {"model", "instructions", "audio", "delegation", "input", "client", "store", "triggers"}
        assert "turn_detection" not in json.dumps(cfg)
        await session.close()

    import json
    asyncio.run(go())
