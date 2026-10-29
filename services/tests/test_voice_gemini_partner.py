"""The one brain on Gemini 3.8 Live (voice/gemini_partner.py) bound by the same Partner (voice/partner.py), against a
fake Live connection: a stand-in for google-genai's `client.aio.live.connect` session that records everything sent
(realtime input, client content, tool responses) and yields the server's messages, built as the SDK's own types.

Mirrors tests/test_voice_partner.py where the behaviour is the same; the 28 Oct chat log's failures are named on the
tests that hold them, and Gemini's own known flaws (a call repeated when its result is empty, WHEN_IDLE results not
spoken) on theirs."""
import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from google.genai import types

from orchestrator.prompts import PARTNER, PARTNER_GEMINI
from orchestrator.voice import gemini_partner
from orchestrator.voice.base import ToolSpec, VoiceConfig
from orchestrator.voice.gemini_partner import GeminiPartnerSession, build_config, payload, usage_facts
from orchestrator.voice.partner import Partner
from orchestrator.voice.partner_session import PartnerSession
from tests.test_voice_partner import app
from tests.waiting import until_async


class FakeLive:
    """One Live connection: what was sent, in order, and the server's messages (`emit`). `receive()` ends after a
    turn_complete, as the SDK's does; a None ends the connection."""

    def __init__(self, config, model):
        self.config, self.model = config, model
        self.sent: list[tuple] = []
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.closed = False
        self.ended = False  # the connection has closed: every receive() ends at once, as the SDK's does

    async def send_realtime_input(self, **kw):
        if self.closed:
            raise ConnectionError("closed")
        key = next(k for k, v in kw.items() if v is not None)
        self.sent.append(("realtime", key, kw[key]))

    async def send_client_content(self, *, turns=None, turn_complete=True):
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(("content", turns if isinstance(turns, list) else [turns], turn_complete))

    async def send_tool_response(self, *, function_responses):
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(("tool", list(function_responses)))

    async def receive(self):
        while not self.ended:
            msg = await self.inbox.get()
            if msg is None:
                self.ended = True
                return
            yield msg
            if msg.server_content is not None and msg.server_content.turn_complete:
                return

    def emit(self, msg):
        self.inbox.put_nowait(msg)

    def kinds(self) -> list[str]:
        return [s[1] if s[0] == "realtime" else s[0] for s in self.sent]

    def texts(self) -> list[tuple[str, bool]]:
        """Every client content's text parts, with whether it completed the turn."""
        out = []
        for s in self.sent:
            if s[0] == "content":
                for turn in s[1]:
                    for part in turn.parts or []:
                        if part.text:
                            out.append((part.text, s[2]))
        return out

    def pictures(self) -> list[bytes]:
        return [part.inline_data.data for s in self.sent if s[0] == "content" for turn in s[1]
                for part in turn.parts or [] if part.inline_data is not None]

    def responses(self) -> list:
        return [r for s in self.sent if s[0] == "tool" for r in s[1]]


class Connector:
    """`connect(model=, config=)` -> an async context manager yielding a FakeLive; every connection kept."""

    def __init__(self):
        self.lives: list[FakeLive] = []
        self.refuse_handles = False

    def __call__(self, *, model, config):
        connector = self

        class CM:
            async def __aenter__(self):
                if connector.refuse_handles and config.session_resumption and config.session_resumption.handle:
                    raise ConnectionError("1008 the handle is not valid")
                live = FakeLive(config, model)
                connector.lives.append(live)
                return live

            async def __aexit__(self, *exc):
                if connector.lives:
                    for live in connector.lives:
                        if live.config is config and not live.closed:
                            live.closed = True
                            live.inbox.put_nowait(None)
        return CM()


# --- the server's messages ------------------------------------------------------------------------------------------
def audio(seconds=0.2):
    data = b"\x10\x00" * int(24000 * seconds)
    return types.LiveServerMessage(server_content=types.LiveServerContent(model_turn=types.Content(
        role="model", parts=[types.Part(inline_data=types.Blob(data=data, mime_type="audio/pcm;rate=24000"))])))


def said(text):
    return types.LiveServerMessage(server_content=types.LiveServerContent(
        output_transcription=types.Transcription(text=text)))


def heard(text):
    return types.LiveServerMessage(server_content=types.LiveServerContent(
        input_transcription=types.Transcription(text=text)))


def call(call_id, name, args=None):
    return types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[
        types.FunctionCall(id=call_id, name=name, args=args or {})]))


def calls(*items):
    return types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[
        types.FunctionCall(id=i, name=n, args=a) for i, n, a in items]))


def generated():
    return types.LiveServerMessage(server_content=types.LiveServerContent(generation_complete=True))


def interrupted():
    return types.LiveServerMessage(server_content=types.LiveServerContent(interrupted=True))


def turn_done(prompt=(("TEXT", 900), ("AUDIO", 75), ("IMAGE", 140)), out=(("AUDIO", 60),)):
    usage = types.UsageMetadata(
        prompt_token_count=sum(c for _, c in prompt), response_token_count=sum(c for _, c in out),
        prompt_tokens_details=[types.ModalityTokenCount(modality=m, token_count=c) for m, c in prompt],
        response_tokens_details=[types.ModalityTokenCount(modality=m, token_count=c) for m, c in out])
    return types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True), usage_metadata=usage)


def handle(token):
    return types.LiveServerMessage(session_resumption_update=types.LiveServerSessionResumptionUpdate(
        new_handle=token, resumable=True))


def go_away(seconds=50):
    return types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left=f"{seconds}s"))


async def settle():
    for _ in range(6):
        await asyncio.sleep(0.01)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(gemini_partner, "RELEASE_TAIL_S", 0.0)
    monkeypatch.setattr(gemini_partner, "PROGRESS_AFTER_S", 60.0)  # no progress lines unless a test is about them


async def gemini(tools=(), talk="hold", extra=None):
    connector = Connector()
    session = GeminiPartnerSession(VoiceConfig(model="gemini-3.8-live", system_prompt="be brief", tools=list(tools),
                                               input_rate=24000, extra=extra or {"video_fps": 0}),
                                   talk=talk, connect=connector)
    logged = []
    session.noted = logged.append
    await session.start()
    return session, connector, logged


async def partner_session(monkeypatch, tools=None, frames=None, talk="hold", extra=None):
    """A Partner bound to a GeminiPartnerSession on a fake connection; the studio's messages recorded in `studio`."""
    hub, brain, ran, logged = app(tools)
    studio = []
    frames = frames if frames is not None else [(b"CAMERA-AT-KEY-DOWN", 41, [0, 0, 100, 100])]
    grabs = []

    def view_frame(hub_, max_side):
        grabs.append(max_side)
        return frames[min(len(grabs), len(frames)) - 1]

    monkeypatch.setattr("orchestrator.voice.scene_tools.view_frame", view_frame)

    async def send(message):
        studio.append(message)

    partner = Partner(hub, brain, None, send=send, log=logged.append)

    async def brief():
        return "Project Leaf: 341 nuclei measured; Researcher 3 running.", "rev-1"
    partner.brief = brief
    connector = Connector()
    session = GeminiPartnerSession(VoiceConfig(model="gemini-3.8-live", system_prompt=PARTNER_GEMINI, input_rate=24000,
                                               extra=extra or {"video_fps": 0}), talk=talk, connect=connector)
    session.noted = logged.append
    partner.bind(session)
    session.config.tools = partner.tools.specs()
    session.seed_brief(*(await partner.brief()))
    await session.start()
    return SimpleNamespace(partner=partner, session=session, connector=connector, live=connector.lives[0],
                           studio=studio, hub=hub, brain=brain, ran=ran, logged=logged, grabs=grabs)


async def press(t, n, *, snapshot=b"WINDOW-AT-KEY-DOWN", how="tab-share", hold=True):
    await t.partner.command({"type": "talk", "down": True, "n": n})
    if snapshot is not None:
        await t.partner.command({"type": "snapshot", "id": f"talk-{n}", "jpeg": base64.b64encode(snapshot).decode(),
                                 "w": 1280, "h": 800, "at": 0, "how": how})
    if hold:
        await asyncio.sleep(gemini_partner.MIN_HOLD_S + 0.05)
    await settle()


def asked(live, after=1) -> list[str]:
    """The client content that completed a turn (what Gemini answers), after the connection's seed."""
    out, seen = [], 0
    for s in live.sent:
        if s[0] == "content":
            seen += 1
            if s[2] and seen > after:
                out.append(" ".join(p.text for turn in s[1] for p in turn.parts if p.text))
    return out


async def speak(t, chunks=3):
    for _ in range(chunks):
        await t.session.send_audio(b"\x01\x00" * 960)


async def release(t, n):
    await t.partner.command({"type": "talk", "down": False, "n": n})
    live = t.session._live
    await until_async(lambda: "activity_end" in live.kinds())
    await settle()


# --- the interface ------------------------------------------------------------------------------------------------
def test_both_one_brain_sessions_are_partner_sessions():
    from orchestrator.voice.openai_realtime import RealtimeSession

    assert isinstance(RealtimeSession(VoiceConfig(model="gpt-realtime-2.1")), PartnerSession)
    assert isinstance(GeminiPartnerSession(VoiceConfig(model="gemini-3.8-live")), PartnerSession)
    assert GeminiPartnerSession.link == "relay"


def test_the_connection_config_follows_the_live_docs():
    """3.8 Live: tools NON_BLOCKING, no proactivity or affective dialog (removed), no thinking_level (refused: 1007) but
    thinking_budget 0 (measured faster), custom vocabulary on the input transcription, compression and resumption set
    explicitly, video at low resolution; hold to talk turns the server's activity detection off."""
    tool = ToolSpec("count", "Count.", {"type": "object", "properties": {"what": {"type": "string"}}}, None)
    config = build_config(VoiceConfig(model="gemini-3.8-live", system_prompt="p", tools=[tool]), talk="hold")
    assert config.tools[0].function_declarations[0].behavior == types.Behavior.NON_BLOCKING
    assert config.proactivity is None and config.enable_affective_dialog is None
    assert config.thinking_config.thinking_budget == 0 and config.thinking_config.thinking_level is None
    assert "stoma" in config.input_audio_transcription.custom_vocabulary
    assert config.context_window_compression.trigger_tokens == gemini_partner.COMPRESS_AT
    assert config.context_window_compression.sliding_window.target_tokens == gemini_partner.COMPRESS_TO
    assert config.session_resumption is not None and config.session_resumption.handle is None
    assert config.media_resolution == types.MediaResolution.MEDIA_RESOLUTION_LOW
    assert config.realtime_input_config.automatic_activity_detection.disabled is True
    assert config.history_config.initial_history_in_client_content is True
    resumed = build_config(VoiceConfig(model="gemini-3.8-live"), talk="open", handle="h1", fresh=False)
    assert resumed.session_resumption.handle == "h1" and resumed.history_config is None
    assert resumed.realtime_input_config is None, "open mic: Gemini's own activity detection"


def test_a_tools_result_is_never_empty():
    """Gemini 3.8 Live repeats a call 2-5 times when its result says nothing (Google's migration guide): every result
    has a status, retryable and a message, and a failure says not to call it again."""
    assert payload({}) == {"status": "done", "retryable": False, "message": "done; nothing more to report"}
    failed = payload({"ok": False, "error": "no camera frame", "_log": {"x": 1}})
    assert failed["status"] == "failed" and "no camera frame" in failed["message"] and "_log" not in failed
    assert "Do not call it again" in failed["message"]
    assert payload({"ok": None, "still_working": True})["status"] == "still_working"
    assert payload("seven")["result"] == "seven" and payload("seven")["status"] == "done"
    assert payload({"ok": True, "summary": "7 nuclei"})["message"] == "7 nuclei"


def test_usage_is_counted_by_modality_at_gemini_prices():
    usage = types.UsageMetadata(prompt_token_count=1362, response_token_count=82, prompt_tokens_details=[
        types.ModalityTokenCount(modality="TEXT", token_count=604), types.ModalityTokenCount(modality="AUDIO", token_count=298),
        types.ModalityTokenCount(modality="IMAGE", token_count=320)],
        response_tokens_details=[types.ModalityTokenCount(modality="AUDIO", token_count=82)])
    facts = usage_facts(usage)
    assert facts == {"inText": 604, "inAudio": 298, "inImage": 320, "inCached": 0, "outText": 0, "outAudio": 82}
    from orchestrator.voice.openai_realtime import usage_usd

    assert usage_usd(facts, "gemini-3.8-live") == pytest.approx((604 * 0.75 + 298 * 3 + 320 * 1 + 82 * 12) / 1e6)


# --- a turn --------------------------------------------------------------------------------------------------------
def test_a_held_turn_sends_its_pictures_note_and_audio_in_one_activity_and_gemini_answers_its_end(monkeypatch):
    """Perception at key-down (amendment 1) as client content inside the turn's activity: the camera frame and window as
    the key went down, the screen as text; the brief seeded when the connection opened. Audio only while held; release
    closes the activity, nothing fetched after it. Chat log: "I can't see your screen right now"."""
    async def go():
        t = await partner_session(monkeypatch)
        seed = t.live.texts()
        assert any("Project Leaf" in text for text, _ in seed), "the brief is in from the start"
        await t.session.send_audio(b"\x01\x00" * 960)
        assert "audio" not in t.live.kinds(), "no audio while the key is up"
        await t.partner.command({"type": "talk", "down": True, "n": 1})
        await t.session.send_audio(b"\x02\x00" * 960)  # said before the activity opens: held, then sent
        await t.partner.command({"type": "snapshot", "id": "talk-1", "jpeg": base64.b64encode(b"WINDOW").decode(),
                                 "how": "tab-share", "at": 0})
        await asyncio.sleep(gemini_partner.MIN_HOLD_S + 0.05)
        await settle()
        kinds = t.live.kinds()
        start = kinds.index("activity_start")
        assert kinds[start + 1] == "audio", "the audio held since key-down follows the activity's start"
        t.hub.screen._lines = ["w-9: something opened after the key went down"]
        await speak(t)
        await release(t, 1)
        kinds = t.live.kinds()
        assert kinds.count("audio") == 5 and kinds.index("activity_end") > max(i for i, k in enumerate(kinds) if k == "audio")
        assert t.live.pictures() == [b"CAMERA-AT-KEY-DOWN", b"WINDOW"]
        note = next(text for text, _ in t.live.texts() if "Turn 1" in text)
        assert "w-1: Measurements table, in front" in note and note.startswith("(From Hooke, not the user speaking.)")
        assert all(not complete for text, complete in t.live.texts()[len(seed):]), "perception never completes a turn"
        first_picture = next(i for i, s in enumerate(t.live.sent) if s[0] == "content" and s[1][0].parts[-1].inline_data)
        assert first_picture > start, "pictures go in once the key is held past a tap"
        turn = next(r for r in t.logged if r["kind"] == "partner-turn")
        assert turn["camera"] is True and turn["window"]["how"] == "tab-share" and turn["committedT"] >= turn["releasedT"]
        await t.session.send_audio(b"\x03\x00" * 960)
        assert t.live.kinds().count("audio") == 5, "nothing after the activity closed"
    asyncio.run(go())


def test_a_tap_and_a_cancel_ask_for_nothing(monkeypatch):
    async def go():
        t = await partner_session(monkeypatch)
        before = len(t.live.sent)
        await t.partner.command({"type": "talk", "down": True, "n": 1})
        await t.session.send_audio(b"\x01\x00" * 960)
        await t.partner.command({"type": "talk", "down": False, "n": 1})
        await asyncio.sleep(gemini_partner.MIN_HOLD_S + 0.1)
        await t.partner.command({"type": "talk", "down": True, "n": 2})
        await asyncio.sleep(0.05)
        await t.partner.command({"type": "talk", "cancel": True, "n": 2})
        await asyncio.sleep(gemini_partner.EARLY_WAIT_S + 0.1)
        sent = t.live.sent[before:]
        assert not [s for s in sent if s[0] == "realtime"], "no activity, no audio"
        assert not [s for s in sent if s[0] == "content" and any(p.inline_data for turn in s[1] for p in turn.parts)]
    asyncio.run(go())


def test_the_users_words_and_the_voices_are_filed_once_in_the_shared_conversation(monkeypatch):
    """Gemini's input transcription comes in pieces before its answer: one user line for the turn, then the voice's own
    words as its reply line (as the GPT-Live path files them)."""
    async def go():
        t = await partner_session(monkeypatch)
        t.brain.conversation.add("user", "chat", "what is this?")
        await press(t, 1)
        await release(t, 1)
        t.live.emit(heard("outline the "))
        t.live.emit(heard("biggest one"))
        t.live.emit(audio())
        t.live.emit(said("Here it is."))
        t.live.emit(generated())
        t.live.emit(turn_done())
        await settle()
        roles = [(turn.role, turn.channel, turn.text) for turn in t.brain.conversation.turns]
        assert ("user", "voice", "outline the biggest one") in roles and ("assistant", "voice", "Here it is.") in roles
        assert [r for r in roles if r[:2] == ("user", "voice")] == [("user", "voice", "outline the biggest one")]
        response = next(r for r in t.logged if r["kind"] == "partner-response")
        assert response["cause"] == "turn" and response["n"] == 1 and response["transcript"] == "Here it is."
        assert response["firstAudioT"] and response["usage"]["inImage"] == 140 and response["usd"] > 0
    asyncio.run(go())


# --- tools ---------------------------------------------------------------------------------------------------------
def test_a_function_call_runs_the_registry_tool_and_its_result_is_spoken_once_the_words_have_played(monkeypatch):
    """The model's call runs the brain's own tool in a voice request (the Assistant by voice, the user's words its
    question); its result goes back never empty and INTERRUPT, sent once the voice's current words have played: on the
    real API (2026-10-29) a WHEN_IDLE result ended the turn without the answer being said."""
    async def go():
        t = await partner_session(monkeypatch, tools={"resources": "341 nuclei measured: area and shape per cell."})
        await press(t, 1)
        await release(t, 1)
        t.live.emit(heard("what data do we have?"))
        t.live.emit(audio(0.3))  # "Checking the data."
        t.live.emit(call("c1", "resources", {"what": "data"}))
        await until_async(lambda: t.ran)
        await asyncio.sleep(0.1)
        assert not t.live.responses(), "held while its words still play"
        await until_async(lambda: t.live.responses())
        name, args, ctx = t.ran[0]
        assert name == "resources" and args == {"what": "data"}
        assert ctx.channel == "voice" and ctx.voice_owner is t.session and ctx.question == "what data do we have?"
        response = t.live.responses()[0]
        assert response.id == "c1" and response.scheduling == types.FunctionResponseScheduling.INTERRUPT
        assert response.response["status"] == "done" and response.response["message"].startswith("341 nuclei")
        assert response.response["retryable"] is False
        t.live.emit(generated())
        t.live.emit(turn_done())
        t.live.emit(audio())
        t.live.emit(said("We have 341 nuclei measured."))
        t.live.emit(generated())
        t.live.emit(turn_done())
        await settle()
        causes = [(r["cause"], r["transcript"]) for r in t.logged if r["kind"] == "partner-response"]
        assert causes == [("turn", ""), ("tool", "We have 341 nuclei measured.")]
        record = next(r for r in t.logged if r["kind"] == "partner-tool")
        assert record["name"] == "resources" and record["ok"] is True
    asyncio.run(go())


def test_a_repeated_call_while_the_first_runs_is_answered_with_its_result_and_run_once(monkeypatch):
    """Gemini's documented duplicate and sweeping calls: the same call again while it runs is never run twice."""
    gate = None

    async def slow(ctx, args):
        await gate.wait()
        return "grouped 341 cells"

    async def go():
        nonlocal gate
        gate = asyncio.Event()
        t = await partner_session(monkeypatch, tools={"resources": slow})
        await press(t, 1)
        await release(t, 1)
        t.live.emit(call("c1", "resources", {"what": "groups"}))
        await settle()
        t.live.emit(call("c2", "resources", {"what": "groups"}))
        await settle()
        gate.set()
        await until_async(lambda: len(t.live.responses()) == 2)
        assert [r[0] for r in t.ran] == ["resources"]
        second = next(r for r in t.live.responses() if r.id == "c2")
        assert "already running" in second.response["note"] and second.response["message"] == "grouped 341 cells"
        assert any(r["kind"] == "partner-call-repeated" for r in t.logged)
    asyncio.run(go())


def test_act_then_look_puts_the_studios_report_and_a_fresh_window_picture_in_before_the_result(monkeypatch):
    """After a change on screen the model gets the studio's report and a fresh picture before it is told the result.
    Chat log: "I've brought up the measurements table" / "the table is still not open"."""
    async def go():
        t = await partner_session(monkeypatch, tools={"show_measurements": "the table is in front on Tab Measurements"})
        await press(t, 1)
        await release(t, 1)
        t.live.emit(call("c1", "show_measurements", {}))
        await until_async(lambda: t.hub.screen.asked == 1)
        t.hub.screen.report(["w-2: Measurements table, in front"])
        request = await until_async(lambda: next((m for m in t.studio if m["type"] == "snapshot-request"), None))
        await t.partner.command({"type": "snapshot", "id": request["id"], "jpeg": base64.b64encode(b"AFTER").decode(),
                                 "how": "composed", "at": 0})
        await until_async(lambda: t.live.responses())
        order = [s[0] for s in t.live.sent]
        look = next(i for i, s in enumerate(t.live.sent) if s[0] == "content" and any(
            p.inline_data is not None and p.inline_data.data == b"AFTER" for turn in s[1] for p in turn.parts))
        assert look < order.index("tool")
        assert any("After show_measurements" in text and "w-2: Measurements table" in text for text, _ in t.live.texts())
        record = next(r for r in t.logged if r["kind"] == "partner-tool")
        assert record["screenChange"] is True and record["confirmed"] is True
        t.live.emit(turn_done())
        t.live.emit(audio())
        t.live.emit(said("The table is open."))
        t.live.emit(turn_done())
        await settle()
        assert [r["cause"] for r in t.logged if r["kind"] == "partner-response"][-1] == "look"
    asyncio.run(go())


def test_one_request_one_researcher_on_gemini_too(monkeypatch):
    """Chat log: "Why did you create two researchers?" Two calls in one tool_call message start one researcher."""
    async def go():
        t = await partner_session(monkeypatch, tools={"start_researcher": "Researcher 4 started on the table"})
        await press(t, 1)
        await release(t, 1)
        t.live.emit(calls(("c1", "start_researcher", {"what": "the table"}), ("c2", "start_researcher", {"what": "data"})))
        await until_async(lambda: len(t.live.responses()) == 2)
        assert [r[0] for r in t.ran] == ["start_researcher"]
        assert "not done again" in t.live.responses()[1].response["note"]
        assert len([s for s in t.live.sent if s[0] == "tool"]) == 1, "one message's results go back together"
    asyncio.run(go())


def test_a_result_for_an_older_turn_or_a_mark_after_the_answer_is_silent(monkeypatch):
    """The user pressed talk again while a tool ran: its result is context for the newer turn, never spoken over it. A
    mark called after the answer was spoken asks for nothing more."""
    gate = None

    async def slow(ctx, args):
        await gate.wait()
        return "done"

    async def go():
        nonlocal gate
        gate = asyncio.Event()
        t = await partner_session(monkeypatch, tools={"resources": slow})
        await press(t, 1)
        await release(t, 1)
        t.live.emit(call("c1", "resources", {}))
        await settle()
        await press(t, 2, snapshot=None)
        gate.set()
        await until_async(lambda: t.live.responses())
        assert t.live.responses()[0].scheduling == types.FunctionResponseScheduling.SILENT
        await release(t, 2)
        t.live.emit(audio(0.05))
        t.live.emit(said("That one."))
        await asyncio.sleep(0.1)
        t.live.emit(call("m1", "mark", {"targets": [{"target": "outline:o1"}]}))
        sent = await until_async(lambda: next((m for m in t.studio if m["type"] == "marks"), None))
        await t.partner.command({"type": "marks-shown", "id": sent["id"], "resolved": [{"target": "outline:o1", "ok": True}]})
        await until_async(lambda: len(t.live.responses()) == 2)
        mark = t.live.responses()[1]
        assert mark.scheduling == types.FunctionResponseScheduling.SILENT and mark.response["shown"] == ["outline:o1"]
    asyncio.run(go())


def test_a_long_tool_gets_one_progress_line_that_calls_no_tool(monkeypatch):
    monkeypatch.setattr(gemini_partner, "PROGRESS_AFTER_S", 0.1)
    gate = None

    async def slow(ctx, args):
        await gate.wait()
        return "outlined 341 nuclei"

    async def go():
        nonlocal gate
        gate = asyncio.Event()
        t = await partner_session(monkeypatch, tools={"outline_cells": slow})
        await press(t, 1)
        await release(t, 1)
        t.live.emit(heard("outline the nuclei"))
        t.live.emit(call("c1", "outline_cells", {}))
        await until_async(lambda: any(r["kind"] == "partner-progress" for r in t.logged))
        lines = asked(t.live)
        assert len(lines) == 1 and "Still running in Hooke: outline_cells" in lines[0] and "call no tool" in lines[0]
        await asyncio.sleep(0.3)
        assert len([r for r in t.logged if r["kind"] == "partner-progress"]) == 1
        gate.set()
    asyncio.run(go())


# --- talking over it, lines nobody asked for ---------------------------------------------------------------------------
def test_pressing_talk_while_it_speaks_stops_it_and_drops_the_rest_of_its_words(monkeypatch):
    """Barge-in (amendment 2): a key down while it generates opens the activity at once (Gemini stops only then); the
    studio flushes its audio (interrupted); what still comes of that generation is neither sent nor filed. Chat log:
    "Wait, why are you talking over me?"."""
    async def go():
        t = await partner_session(monkeypatch)
        out = []
        t.session.on(lambda e: out.append(e) if e.kind in ("audio", "interrupted") else None)
        await press(t, 1)
        await release(t, 1)
        t.live.emit(audio(2.0))
        t.live.emit(said("A long answer about"))
        await settle()
        assert t.session.speaking()
        before = len(t.live.sent)
        await press(t, 2, snapshot=None, hold=False)
        assert t.live.kinds()[before:][:1] == ["activity_start"], "at once, not after the tap's wait"
        assert [e.kind for e in out][-1] == "interrupted"
        t.live.emit(audio())
        t.live.emit(said(" the nuclei."))
        t.live.emit(interrupted())
        await settle()
        assert [e.kind for e in out].count("audio") == 1
        assistant = [turn.text for turn in t.brain.conversation.turns if turn.role == "assistant"]
        assert assistant == ["A long answer about"]
        assert next(r for r in t.logged if r["kind"] == "partner-barge")["n"] == 2
        # A tap after the barge-in: the activity it opened is closed, and whatever Gemini answers is not heard.
        await t.partner.command({"type": "talk", "down": False, "n": 2})
        await settle()
        assert "activity_end" in t.live.kinds()[before:]
        t.live.emit(audio())
        t.live.emit(said("Yes?"))
        t.live.emit(turn_done())
        await settle()
        assert [e.kind for e in out].count("audio") == 1 and "Yes?" not in json.dumps(
            [turn.text for turn in t.brain.conversation.turns])
    asyncio.run(go())


def test_a_line_nobody_asked_for_waits_for_quiet_and_is_said_once(monkeypatch):
    """Amendment 4: one sentence, only when nobody is talking, at most once per event, pointing at the card."""
    async def go():
        t = await partner_session(monkeypatch)
        await press(t, 1)
        await t.partner.happened("researcher-done", '{"researcher": "Researcher 3", "status": "done"}', ref="R3",
                                 mark="researcher:R3")
        await asyncio.sleep(0.3)
        assert not asked(t.live), "never over the user"
        await release(t, 1)
        t.session._last_user_at -= 2.0
        marks = await until_async(lambda: next((m for m in t.studio if m["type"] == "marks"), None))
        assert marks["marks"] == [{"target": "researcher:R3", "style": "box"}]
        lines = await until_async(lambda: asked(t.live) or None)
        assert "Researcher 3" in lines[0] and "exactly one short spoken sentence" in lines[0]
        await t.partner.happened("researcher-done", '{"researcher": "Researcher 3", "status": "done"}', ref="R3")
        await asyncio.sleep(0.4)
        assert len(asked(t.live)) == 1, "one line per event"
        said_ = [r for r in t.logged if r["kind"] == "partner-proactive"]
        assert said_[0]["markTarget"] == "researcher:R3" and said_[1]["skipped"] == "duplicate"
        t.live.emit(audio())
        t.live.emit(said("Researcher 3 is done."))
        t.live.emit(turn_done())
        await settle()
        assert [r["cause"] for r in t.logged if r["kind"] == "partner-response"] == ["proactive"]
    asyncio.run(go())


def test_two_lines_nobody_asked_for_are_never_asked_before_the_first_ones_answer_begins(monkeypatch):
    """Client content that completes a turn interrupts whatever Gemini is generating: a second line waits until the
    first's answer has begun and ended (the OpenAI voice's one-response gate, here), and an answer that begins while
    the key is held is never heard (the user is talking)."""
    async def go():
        t = await partner_session(monkeypatch)
        t.session._last_user_at = 0.0
        await t.partner.happened("tool-done", '{"tool": "count", "status": "done"}', ref="a1")
        await t.partner.happened("capability-on", '{"capability": "Cellpose"}', ref="cellpose")
        await until_async(lambda: asked(t.live) or None)
        await asyncio.sleep(0.6)
        assert len(asked(t.live)) == 1, "the second waits for the first's answer"
        t.live.emit(audio(0.1))
        t.live.emit(said("Counting is done."))
        t.live.emit(turn_done())
        await until_async(lambda: len(asked(t.live)) == 2)
        await press(t, 1, snapshot=None, hold=False)
        out = []
        t.session.on(lambda e: out.append(e.kind))
        t.live.emit(audio(0.1))
        t.live.emit(said("Cellpose is on."))
        t.live.emit(turn_done())
        await settle()
        assert "audio" not in out and "Cellpose is on." not in [turn.text for turn in t.brain.conversation.turns]
    asyncio.run(go())


def test_typed_chat_and_the_narrators_context_reach_it_without_an_answer(monkeypatch):
    async def go():
        t = await partner_session(monkeypatch)
        t.brain.voice_notes = ["the user typed 'how many?' and the app answered '341'"]
        await until_async(lambda: any("how many?" in text for text, _ in t.live.texts()))
        note = next((text, complete) for text, complete in t.live.texts() if "how many?" in text)
        assert note[1] is False and "Context only" in note[0]
    asyncio.run(go())


# --- session length -------------------------------------------------------------------------------------------------
def test_a_dropped_connection_resumes_with_its_handle_and_seeds_nothing(monkeypatch):
    """A lost connection (the partner asks, as for a dropped sideband) resumes with the newest handle: the same
    conversation on Gemini's side, so nothing is sent again; the same session object goes on."""
    async def go():
        t = await partner_session(monkeypatch)
        t.live.emit(handle("h-1"))
        await settle()
        first = t.live
        first.emit(None)  # the connection closes under it
        await until_async(lambda: len(t.connector.lives) == 2)
        second = t.connector.lives[1]
        assert second.config.session_resumption.handle == "h-1"
        await settle()
        assert not second.sent, "a resumed conversation is not seeded again"
        record = await until_async(lambda: next((r for r in t.logged if r["kind"] == "partner-reconnect"), None))
        assert record["resumed"] is True and record["newCall"] is False
        assert not [m for m in t.studio if m["type"] == "reoffer"], "no browser offer: the audio is relayed"
        assert t.partner.session is t.session and t.session.connected()
    asyncio.run(go())


def test_without_a_handle_a_fresh_connection_is_seeded_with_the_conversation_notes_and_brief(monkeypatch):
    async def go():
        t = await partner_session(monkeypatch)
        t.brain.conversation.add("user", "chat", "how many cells?")
        t.brain.conversation.add("assistant", "voice", "341.")
        await press(t, 1)
        await release(t, 1)
        t.connector.refuse_handles = True
        t.live.emit(handle("h-1"))
        await settle()
        t.live.emit(None)
        await until_async(lambda: len(t.connector.lives) == 2)
        second = t.connector.lives[1]
        await until_async(lambda: second.sent)
        seeded = second.sent[0]
        assert seeded[0] == "content" and seeded[2] is True, "the seed ends complete: Gemini does not answer it"
        texts = [p.text for turn in seeded[1] for p in turn.parts]
        assert texts[:2] == ["how many cells?", "341."] and [turn.role for turn in seeded[1]][:2] == ["user", "model"]
        assert any("Turn 1" in x for x in texts) and any("Project Leaf" in x for x in texts)
        assert any(r["kind"] == "partner-resume-refused" for r in t.logged)
    asyncio.run(go())


def test_a_goaway_or_new_tools_renew_the_connection_when_it_is_quiet(monkeypatch):
    """Gemini sends GoAway about a minute before a connection ends; the renewal waits for quiet (never mid-turn) and
    resumes with the handle. A capability turned on is a new tool list, which Gemini takes only on a new connection."""
    async def go():
        t = await partner_session(monkeypatch)
        t.live.emit(handle("h-2"))
        await press(t, 1)
        t.live.emit(go_away(50))
        await asyncio.sleep(0.6)
        assert len(t.connector.lives) == 1, "not while the key is held"
        await release(t, 1)
        t.live.emit(turn_done())
        await until_async(lambda: len(t.connector.lives) == 2)
        assert t.connector.lives[1].config.session_resumption.handle == "h-2"
        extra = ToolSpec("run_capability", "Run a capability.", {"type": "object"}, None)
        await t.session.update_tools([*t.session.config.tools, extra])
        await until_async(lambda: len(t.connector.lives) == 3)
        names = [d.name for d in t.connector.lives[2].config.tools[0].function_declarations]
        assert "run_capability" in names
        reasons = [r["reason"] for r in t.logged if r["kind"] == "partner-reconnect"]
        assert reasons == ["goaway", "tools"]
    asyncio.run(go())


def test_a_release_whose_audio_went_to_a_replaced_connection_asks_to_say_it_again(monkeypatch):
    async def go():
        t = await partner_session(monkeypatch)
        await press(t, 1)
        await t.session.reconnect(None, why="close")
        await t.partner.command({"type": "talk", "down": False, "n": 1})
        live = t.connector.lives[-1]
        lines = await until_async(lambda: [x for x in asked(live, after=0) if "say that again" in x] or None)
        assert len(lines) == 1
        assert any(r["kind"] == "partner-turn-lost" for r in t.logged)
    asyncio.run(go())


def test_the_camera_streams_as_video_at_most_once_a_second_newest_frame_only(monkeypatch):
    monkeypatch.setattr(gemini_partner, "VIDEO_FPS", 10.0)

    async def go():
        frames = iter([(b"F1", 1, [0, 0, 1, 1]), (b"F1", 1, [0, 0, 1, 1]), (b"F2", 2, [0, 0, 1, 1])] + [None] * 100)

        async def grab():
            return next(frames)
        connector = Connector()
        session = GeminiPartnerSession(VoiceConfig(model="gemini-3.8-live", extra={"video_fps": 10.0}),
                                       connect=connector)
        session.video = grab
        await session.start()
        await asyncio.sleep(0.5)
        await session.close()
        video = [s[2].data for s in connector.lives[0].sent if s[:2] == ("realtime", "video")]
        assert video == [b"F1", b"F2"], "a frame already sent is not sent again"
        assert gemini_partner.VIDEO_FPS <= 10 and float(VoiceConfig(model="x").extra.get("video_fps", 1.0)) == 1.0
    asyncio.run(go())


# --- the route, the prompt and the option --------------------------------------------------------------------------
def test_the_route_opens_gemini_through_the_relay_only(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from orchestrator.voice import partner as partner_module, route

    made = []

    class FakeGemini:
        link = "relay"

        def __init__(self, config, talk="hold", **kw):
            self.config, self.talk, self.closed, self.n = config, talk, False, 0
            self.answer_sdp, self.session_id, self.last_commentary_at, self.usage_seconds = None, "gl_1", 0.0, None
            self.video = None
            made.append(self)

        def on(self, callback):
            pass

        def seed_brief(self, text, revision):
            self.brief = text

        async def start(self, offer_sdp=None):
            assert offer_sdp is None

        async def events(self):
            from orchestrator.voice.base import VoiceEvent
            yield VoiceEvent("ready")
            yield VoiceEvent("closed", data={"reason": "test"})

        async def close(self):
            self.closed = True

    monkeypatch.setattr(route, "GeminiPartnerSession", FakeGemini)

    async def brief(self):
        return "Project Leaf", "r1"
    monkeypatch.setattr(partner_module.Partner, "brief", brief)
    hub, brain, _, _ = app()
    hub.settings.voice, hub.settings.talk = "gemini-3.8-live", "hold"
    web = FastAPI()
    route.add_voice_route(web, hub, brain, dialogue=None)
    client = TestClient(web)
    with client.websocket_connect("/ws/voice?link=webrtc", headers={"origin": "http://127.0.0.1:8770"}) as ws:
        assert "through the app" in ws.receive_json()["data"]["message"]
    with client.websocket_connect("/ws/voice?rate=24000", headers={"origin": "http://127.0.0.1:8770"}) as ws:
        model = ws.receive_json()
        assert model["kind"] == "model" and model["data"]["talk"] == "hold" and model["data"]["model"] == "gemini-3.8-live"
    session = made[0]
    assert session.config.model == "gemini-3.8-live" and session.config.input_rate == 24000
    assert session.config.system_prompt == PARTNER_GEMINI and session.brief == "Project Leaf"
    assert {"mark", "look_hard", "do_in_hooke"} <= {t.name for t in session.config.tools}
    assert callable(session.video), "the eyepiece streams while Talk is open"


def test_the_gemini_prompt_is_the_partners_with_googles_tool_call_cap():
    said = " ".join(PARTNER_GEMINI.split())
    assert PARTNER_GEMINI.startswith(PARTNER)
    assert "Never call more than two tools in a row without saying something back to the user" in said
    assert "Never tell the user to restart, refresh, reload or quit the app" in said


def test_the_option_is_offered_beside_gpt_realtime_and_the_default_is_unchanged():
    from orchestrator.options import VOICE, Settings
    from orchestrator.voice.gemini_partner import MODEL

    assert MODEL == "gemini-3.8-live" and MODEL in VOICE and "gpt-realtime-2.1" in VOICE
    assert "Gemini" in VOICE[MODEL].label and VOICE[MODEL].model == "gemini-3-8-live"
    assert Settings().voice == "gpt-live-1"
    from research_worker.budget import PRICES

    assert PRICES[MODEL]["audio_out"] == 12.00 and PRICES[MODEL]["image_in"] == 1.00


def test_the_probe_runs_one_held_turn_with_a_tool_through_the_session_itself(monkeypatch):
    """The paid smoke test and the app's own check of the option (model_checks) run `probe`: a fake that answers as the
    real API did on 2026-10-29 (a few words, the tool call, then the result said after the INTERRUPT response)."""
    monkeypatch.setattr(gemini_partner, "MIN_HOLD_S", 0.01)

    class Scripted(Connector):
        def __call__(self, *, model, config):
            cm = super().__call__(model=model, config=config)
            connector = self
            enter = cm.__aenter__

            async def scripted():
                live = await enter()
                send_realtime, send_tool = live.send_realtime_input, live.send_tool_response

                async def realtime(**kw):
                    await send_realtime(**kw)
                    if kw.get("activity_end") is not None:
                        for msg in (heard("Outline the nuclei."), audio(0.1), said("Counting the nuclei."),
                                    call("c1", "outline_cells", {"what": "nuclei"})):
                            live.emit(msg)

                async def tool(*, function_responses):
                    await send_tool(function_responses=function_responses)
                    for msg in (generated(), turn_done(), audio(0.1), said("I outlined seven nuclei."), generated(),
                                turn_done()):
                        live.emit(msg)
                live.send_realtime_input, live.send_tool_response = realtime, tool
                connector.last = live
                return live
            cm.__aenter__ = scripted
            return cm

    async def go():
        connector = Scripted()
        found = await gemini_partner.probe(image=b"JPEG", speech=b"\x01\x00" * 4800, connect=connector, timeout=5)
        assert found["heard"] == "Outline the nuclei." and found["calls"][0]["what"] == "nuclei"
        assert found["said"] == [("turn", "Counting the nuclei."), ("tool", "I outlined seven nuclei.")]
        assert found["toolCallMs"] is not None and found["firstAudioMs"] is not None and found["usd"] > 0
        response = connector.last.responses()[0]
        assert response.scheduling == types.FunctionResponseScheduling.INTERRUPT and response.response["count"] == 7
        assert b"JPEG" in connector.last.pictures()
    asyncio.run(go())
