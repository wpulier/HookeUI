"""Probe and benchmark Gemini Live models on spoken "what's that?" questions.

For each model, one Live session per repeat: the microscope view is streamed at 1 fps (a
saved bench frame, i.e. a still slide), the microphone is simulated by streaming PCM in real
time (silence, then a question spoken by macOS `say`, then silence), and the model has one
NON_BLOCKING tool, `locate(description)`. When it calls it, the probe runs the real
grounding call (gemini-3.8-flash, thinking LOW, box strategy, same crop as bench.grounding)
on the view being streamed, or a canned answer after a fixed delay (--grounding canned), and
returns the result with scheduling INTERRUPT.

Measured per question (all from the moment the last voiced sample was streamed, i.e. the
user stopped talking):
  tool call       end of speech -> tool call received
  first audio     end of speech -> first output audio chunk received
  result->audio   tool result sent -> first audio chunk after it
  result->heard   tool result sent -> when the answer would start playing on a client that
                  plays chunks back to back and flushes on `interrupted`
  highlight       end of speech -> grounding box ready (tool call + grounding time)

Everything the model said is kept (output transcription), split into what it said before
the tool result (acknowledgement) and after (answer).

Usage (from services/, keys from ~/Desktop/micromind.txt):
  .venv/bin/python -m bench.live_probe --models gemini-3.8-live,gemini-3.8-live-extended-thinking --repeats 3
  .venv/bin/python -m bench.live_probe --models gemini-3.8-live --silence-ms 300 --tag live-vad300
  .venv/bin/python -m bench.live_probe --soak 900 --models gemini-3.8-live   # session-length test
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from bench.grounding import BENCH, prepare, score
from orchestrator.coords import gemini_box_to_frame
from orchestrator.keys import load_keys
from orchestrator.prompts import LIVE_VOICE_BASE, LIVE_VOICE_SPEAK_FIRST, LOCATE_WITH_BOX
from orchestrator.vision import GeminiVision
from orchestrator.voice.base import ToolSpec, VoiceConfig, VoiceEvent
from orchestrator.voice.gemini_live import GeminiLiveSession

RATE = 16000
CHUNK_MS = 40
CHUNK_BYTES = RATE * 2 * CHUNK_MS // 1000
DEFAULT_QUESTIONS = [0, 2, 6, 9, 10, 13]  # indices into bench/questions.json

INTERIM_RESULT = {
    "status": "in_progress",
    "note": "locate is already running for this request; its result arrives by itself in about 2 seconds. "
            "Do NOT call locate again. Say only a few words now (e.g. 'Let me take a closer look.') and do not guess what it is yet.",
}

LOCATE_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {
            "type": "string",
            "description": "What to find, as seen: position in the view, size, shape, colour. E.g. 'small dark dot near the top'.",
        }
    },
    "required": ["description"],
}


def tts(text: str, workdir: Path, voice: str = "Samantha") -> np.ndarray:
    """Speak `text` with macOS `say` and return 16 kHz mono int16 PCM."""
    aiff = workdir / f"q{abs(hash(text))}.aiff"
    pcm = aiff.with_suffix(".pcm")
    if not pcm.exists():
        subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-ar", str(RATE), "-ac", "1", "-f", "s16le", str(pcm)], check=True)
    return np.frombuffer(pcm.read_bytes(), dtype=np.int16)


def voiced_span(samples: np.ndarray, threshold: int = 400) -> tuple[int, int]:
    """First and last voiced sample (10 ms windows above an amplitude threshold)."""
    win = RATE // 100
    n = len(samples) // win
    loud = np.abs(samples[: n * win].reshape(n, win)).max(axis=1) > threshold
    idx = np.flatnonzero(loud)
    if len(idx) == 0:
        return 0, len(samples)
    return int(idx[0] * win), int((idx[-1] + 1) * win)


class Mic:
    """Streams PCM to the session in real time: silence unless an utterance is queued.
    Records when the first and last voiced samples of each utterance were sent."""

    def __init__(self, session: GeminiLiveSession, end_turn_after_ms: int | None = None) -> None:
        """end_turn_after_ms: client-side turn detection; activity_start before speech and
        activity_end this long after the last voiced sample (None = server VAD)."""
        self.session = session
        self.end_turn_after_ms = end_turn_after_ms
        self.turn_ended_at: float | None = None
        self.pending: bytearray = bytearray()
        self.voiced: tuple[int, int] = (0, 0)
        self.sent_in_utterance = 0
        self.speech_start_at: float | None = None
        self.speech_end_at: float | None = None
        self.done = asyncio.Event()

    def say(self, samples: np.ndarray) -> None:
        start, end = voiced_span(samples)
        self.pending = bytearray(samples.tobytes())
        self.voiced = (start * 2, end * 2)
        self.sent_in_utterance = 0
        self.speech_start_at = self.speech_end_at = self.turn_ended_at = None
        self.done.clear()

    async def _end_turn_later(self) -> None:
        delay = self.speech_end_at + self.end_turn_after_ms / 1000 - time.monotonic()
        await asyncio.sleep(max(0.0, delay))
        await self.session.end_user_turn()
        self.turn_ended_at = time.monotonic()

    async def run(self) -> None:
        silence = bytes(CHUNK_BYTES)
        next_at = time.monotonic()
        while not self.session.closed:
            if self.pending:
                chunk = bytes(self.pending[:CHUNK_BYTES]).ljust(CHUNK_BYTES, b"\0")
                del self.pending[:CHUNK_BYTES]
                lo, hi = self.sent_in_utterance, self.sent_in_utterance + CHUNK_BYTES
                if lo == 0 and self.end_turn_after_ms is not None:
                    await self.session.begin_user_turn()
                await self.session.send_audio(chunk)
                now = time.monotonic()
                # Time the voiced boundary inside this chunk would have been spoken.
                if self.speech_start_at is None and lo <= self.voiced[0] < hi:
                    self.speech_start_at = now - (hi - self.voiced[0]) / (RATE * 2)
                if self.speech_end_at is None and lo < self.voiced[1] <= hi:
                    self.speech_end_at = now - (hi - self.voiced[1]) / (RATE * 2)
                self.sent_in_utterance = hi
                if not self.pending:
                    if self.end_turn_after_ms is not None:
                        if self.speech_end_at is None:
                            self.speech_end_at = now
                        asyncio.create_task(self._end_turn_later())
                    self.done.set()
            else:
                await self.session.send_audio(silence)
            next_at += CHUNK_MS / 1000
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))


@dataclass
class Turn:
    question: dict
    prepared: dict
    asked_at: float = 0.0
    events: list[VoiceEvent] = field(default_factory=list)
    grounding: dict | None = None
    ground_task: asyncio.Task | None = None


class Probe:
    def __init__(self, model: str, args, vision: GeminiVision | None) -> None:
        self.model = model
        self.args = args
        self.vision = vision
        self.turn: Turn | None = None
        self.view_jpeg: bytes | None = None
        self.all_events: list[VoiceEvent] = []
        extra = {}
        if args.thinking_level:
            extra["thinking_level"] = args.thinking_level
        if args.thinking_budget is not None:
            extra["thinking_budget"] = args.thinking_budget
        if args.no_compression:
            extra["compression"] = False
        if args.media_resolution:
            extra["media_resolution"] = args.media_resolution
        self.config = VoiceConfig(
            model=model,
            system_prompt=LIVE_VOICE_BASE + (LIVE_VOICE_SPEAK_FIRST if args.ack == "prompt" else ""),
            voice=args.voice_name,
            tools=[ToolSpec(
                "locate", "Find and outline one thing in the current microscope view.", LOCATE_SCHEMA, self.locate,
                scheduling=args.scheduling,
                interim=INTERIM_RESULT if args.ack == "interim" else None,
            )],
            silence_ms=args.silence_ms,
            client_turns=args.client_turns is not None,
            extra=extra,
        )
        self.session = GeminiLiveSession(self.config)
        self.session.on(self._on_event)

    def _on_event(self, event: VoiceEvent) -> None:
        self.all_events.append(event)
        turn = self.turn
        if turn is None:
            return
        turn.events.append(event)
        # Speculative start: the final transcript of the user's question arrives before the
        # model's tool call, so start locating with the transcript right away; the tool call
        # then joins the job already running.
        if (self.args.speculative and event.kind == "input_transcript" and not event.data.get("interim")
                and turn.ground_task is None):
            turn.ground_task = asyncio.create_task(self.ground(turn, event.text, "transcript"))

    async def locate(self, args: dict) -> dict:
        turn = self.turn
        if turn.ground_task is None:
            description = str(args.get("description") or turn.question["question"])
            turn.ground_task = asyncio.create_task(self.ground(turn, description, "tool"))
        return await asyncio.shield(turn.ground_task)

    async def ground(self, turn: "Turn", description: str, started_by: str) -> dict:
        t0 = time.perf_counter()
        if self.args.grounding == "canned" or self.vision is None:
            await asyncio.sleep(self.args.canned_ms / 1000)
            data = {"box_2d": None, "guess": "dark speck (canned)", "one_line": "It looks like a small dark speck, probably debris on the slide.", "confidence": 0.5}
            error = ""
        else:
            prompt = LOCATE_WITH_BOX.format(question=description, context="")
            result = await self.vision.ask([turn.prepared["clean"]], prompt)
            data, error = result.data or {}, result.error
        ms = (time.perf_counter() - t0) * 1000
        box = None
        if isinstance(data.get("box_2d"), list) and len(data["box_2d"]) == 4:
            box = gemini_box_to_frame(data["box_2d"], turn.prepared["crop"])
        center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) if box else None
        q = turn.question
        turn.grounding = {
            "ms": round(ms), "doneAt": time.monotonic(), "startedBy": started_by,
            "description": description, "guess": data.get("guess"), "box": box,
            "correct": score(center, box, q["targets"], q["tolerance"]) if box else None, "error": error,
        }
        if error or not data:
            return {"found": False, "error": error or "no answer"}
        return {
            "found": box is not None,
            "outlined_on_screen": box is not None,
            "likely": data.get("guess"),
            "one_line": data.get("one_line"),
            "confidence": data.get("confidence"),
        }

    async def frame_loop(self) -> None:
        while not self.session.closed:
            if self.view_jpeg is not None:
                await self.session.send_frame(self.view_jpeg, force=True)
            await asyncio.sleep(1.0)

    async def ask(self, question: dict, prepared: dict, samples: np.ndarray, mic: Mic) -> dict:
        self.view_jpeg = prepared["clean"]
        await asyncio.sleep(self.args.view_seconds)  # let the model see the new view
        self.turn = turn = Turn(question, prepared, asked_at=time.monotonic())
        mic.say(samples)
        await mic.done.wait()
        deadline = time.monotonic() + self.args.turn_timeout
        while time.monotonic() < deadline and not self.session.closed:
            await asyncio.sleep(0.1)
            kinds = ["final_result" if e.kind == "tool_result" and not e.data.get("interim") else e.kind for e in turn.events]
            if "final_result" in kinds:
                # Done when the answer (audio after the result) has finished. A turn_complete
                # right after the result only closes the interrupted acknowledgement.
                after = kinds[kinds.index("final_result"):]
                if "audio" in after and "turn_complete" in after[after.index("audio"):]:
                    break
                result_at = next(e.at for e in turn.events if e.kind == "tool_result" and not e.data.get("interim"))
                if "audio" not in after and time.monotonic() - result_at > 8.0:
                    break
            elif "tool_call" not in kinds and kinds.count("turn_complete") >= 1:
                # Answered without the tool; give it a moment to call it late.
                last_tc = max(e.at for e in turn.events if e.kind == "turn_complete")
                if time.monotonic() - last_tc > 4.0:
                    break
        await asyncio.sleep(0.8)  # trailing transcripts
        self.turn = None
        return summarise(turn, mic)


def playback_start(events: list[VoiceEvent], after: float) -> float | None:
    """When the first audio chunk received after `after` would start playing on a client
    that plays chunks back to back and drops its buffer on `interrupted`."""
    cursor = 0.0
    for e in events:
        if e.kind == "interrupted":
            cursor = e.at
        elif e.kind == "audio":
            start = max(cursor, e.at)
            if e.at >= after:
                return start
            cursor = start + len(e.audio) / 2 / e.rate
    return None


def summarise(turn: Turn, mic: Mic) -> dict:
    ev = turn.events
    end = mic.speech_end_at
    rel = lambda t: None if t is None or end is None else round((t - end) * 1000)  # noqa: E731
    first = lambda kind, after=-1e18: next((e for e in ev if e.kind == kind and e.at >= after), None)  # noqa: E731
    call = first("tool_call")
    result = next((e for e in ev if e.kind == "tool_result" and not e.data.get("interim")), None)
    audio = first("audio", mic.speech_start_at or 0)
    vad_end = next((e for e in ev if e.kind == "vad" and e.data.get("state") == "end"), None)
    in_any = [e for e in ev if e.kind == "input_transcript"]
    in_tr = [e for e in in_any if not e.data.get("interim")]
    out_before = "".join(e.text for e in ev if e.kind == "output_transcript" and (result is None or e.at < result.at) and not e.data.get("thought"))
    out_after = "".join(e.text for e in ev if e.kind == "output_transcript" and result is not None and e.at >= result.at and not e.data.get("thought"))
    audio_after = first("audio", result.at) if result else None
    heard = playback_start(ev, result.at) if result else None
    ack_audio_s = sum(len(e.audio) / 2 / e.rate for e in ev if e.kind == "audio" and (result is None or e.at < result.at))
    errors = [e.data.get("message") for e in ev if e.kind == "error"]
    reasons = [e.data.get("reason") for e in ev if e.kind == "turn_complete" and e.data.get("reason")]
    return {
        "question": turn.question["question"],
        "frame": turn.question["frame"],
        "kind": turn.question["kind"],
        "speechMs": None if end is None or mic.speech_start_at is None else round((end - mic.speech_start_at) * 1000),
        "vadEndMs": rel(vad_end.at) if vad_end else None,
        "inputTranscriptFirstMs": rel(in_any[0].at) if in_any else None,
        "inputTranscriptLastMs": rel(in_tr[-1].at) if in_tr else None,
        "inputTranscript": "".join(e.text for e in in_tr).strip(),
        "toolCallMs": rel(call.at) if call else None,
        "toolArgs": call.args if call else None,
        "toolCalls": sum(e.kind == "tool_call" for e in ev),
        "firstAudioMs": rel(audio.at) if audio else None,
        "audioBeforeToolCall": bool(audio and call and audio.at < call.at),
        "groundingMs": turn.grounding["ms"] if turn.grounding else None,
        "highlightMs": rel(turn.grounding["doneAt"]) if turn.grounding else None,
        "toolResultMs": rel(result.at) if result else None,
        "turnEndSentMs": rel(mic.turn_ended_at),
        "resultToAudioMs": round((audio_after.at - result.at) * 1000) if audio_after else None,
        "resultToHeardMs": round((heard - result.at) * 1000) if heard is not None else None,
        "ackAudioSeconds": round(ack_audio_s, 2),
        "interrupted": any(e.kind == "interrupted" for e in ev),
        "said": out_before.strip(),
        "saidAfterResult": out_after.strip(),
        "grounding": turn.grounding,
        "turnCompleteReasons": reasons,
        "errors": errors,
        "timeline": [
            [rel(e.at), e.kind + (" (interim)" if e.data.get("interim") else ""),
             (e.text or e.name or e.data.get("state") or (len(e.audio) if e.audio else ""))]
            for e in ev if e.kind not in ("usage",)
        ],
    }


def pct(values, q):
    values = [v for v in values if v is not None]
    return int(np.percentile(values, q)) if values else None


async def run_session(model: str, args, questions: list[dict], prepared: dict, speech: dict, vision) -> dict:
    probe = Probe(model, args, vision)
    t0 = time.perf_counter()
    rows, error = [], ""
    try:
        await probe.session.start()
    except Exception as exc:
        return {"model": model, "connectMs": None, "rows": [], "error": f"{type(exc).__name__}: {exc}"[:400]}
    connect_ms = round((time.perf_counter() - t0) * 1000)
    mic = Mic(probe.session, args.client_turns)
    tasks = [asyncio.create_task(mic.run()), asyncio.create_task(probe.frame_loop())]
    try:
        for q in questions:
            if probe.session.closed:
                error = next((e.data.get("reason") for e in probe.all_events if e.kind == "closed"), "closed")
                break
            row = await probe.ask(q, prepared[q["frame"]], speech[q["question"]], mic)
            row["model"] = model
            rows.append(row)
            g = row["grounding"] or {}
            print(f"  {model[:34]:34s} call {row['toolCallMs']}ms audio {row['firstAudioMs']}ms "
                  f"hl {row['highlightMs']}ms res->heard {row['resultToHeardMs']}ms ok={g.get('correct')} "
                  f"| {row['question'][:40]} | ack: {row['said'][:60]!r} | ans: {row['saidAfterResult'][:80]!r} {row['errors'] or ''}")
    finally:
        for t in tasks:
            t.cancel()
        await probe.session.close()
    others = [{"kind": e.kind, "data": e.data} for e in probe.all_events if e.kind in ("go_away", "error", "closed")]
    return {"model": model, "connectMs": connect_ms, "rows": rows, "error": error, "sessionEvents": others}


async def soak(model: str, args, prepared_view: bytes) -> dict:
    """Keep a session open streaming 1 fps video + silent audio; report when/why it ends."""
    probe = Probe(model, args, None)
    await probe.session.start()
    probe.view_jpeg = prepared_view
    mic = Mic(probe.session)
    t0 = time.monotonic()
    tasks = [asyncio.create_task(mic.run()), asyncio.create_task(probe.frame_loop())]
    try:
        while not probe.session.closed and time.monotonic() - t0 < args.soak:
            await asyncio.sleep(1)
            if int(time.monotonic() - t0) % 120 == 0:
                print(f"  soak {model}: {int(time.monotonic() - t0)} s, open")
    finally:
        for t in tasks:
            t.cancel()
        lasted = round(time.monotonic() - t0)
        await probe.session.close()
    events = [{"t": round(e.at - t0, 1), "kind": e.kind, "data": e.data} for e in probe.all_events if e.kind in ("go_away", "error", "closed", "resumption")]
    resumptions = sum(1 for e in events if e["kind"] == "resumption")
    events = [e for e in events if e["kind"] != "resumption"]
    return {"model": model, "compression": not args.no_compression, "lastedSeconds": lasted, "target": args.soak, "resumptionUpdates": resumptions, "events": events}


def summary_table(rows: list[dict]) -> dict:
    table = {}
    for model in dict.fromkeys(r["model"] for r in rows):
        rs = [r for r in rows if r["model"] == model]
        g = [r["grounding"] for r in rs if r["grounding"]]
        table[model] = {
            "n": len(rs),
            "toolCalled": sum(r["toolCallMs"] is not None for r in rs),
            "spokeBeforeToolCall": sum(r["audioBeforeToolCall"] for r in rs),
            "groundingCorrect": sum(bool(x.get("correct")) for x in g),
            "groundingRuns": len(g),
            **{f"{k}P50": pct([r[k] for r in rs], 50) for k in ("vadEndMs", "inputTranscriptLastMs", "toolCallMs", "firstAudioMs", "groundingMs", "highlightMs", "toolResultMs", "resultToAudioMs", "resultToHeardMs")},
            **{f"{k}P95": pct([r[k] for r in rs], 95) for k in ("toolCallMs", "firstAudioMs", "highlightMs", "resultToAudioMs", "resultToHeardMs")},
        }
    return table


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="gemini-3.8-live,gemini-3.8-live-extended-thinking")
    parser.add_argument("--questions", default=",".join(map(str, DEFAULT_QUESTIONS)))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--grounding", choices=["real", "canned"], default="real")
    parser.add_argument("--canned-ms", type=int, default=1800)
    parser.add_argument("--grounding-model", default="gemini-3.8-flash")
    parser.add_argument("--speculative", action="store_true",
                        help="start grounding on the final input transcript; the tool call joins it")
    parser.add_argument("--client-turns", type=int, default=None, metavar="MS",
                        help="disable server VAD; end the user turn MS after the last voiced sample (local VAD / push-to-talk)")
    parser.add_argument("--ack", choices=["none", "prompt", "interim"], default="none",
                        help="how to get a spoken acknowledgement while locate runs: nothing extra; "
                             "system prompt says speak first; or an immediate interim tool result (will_continue)")
    parser.add_argument("--scheduling", choices=["interrupt", "when_idle", "silent"], default="interrupt")
    parser.add_argument("--silence-ms", type=int, default=None, help="VAD silence before end of turn (server default if unset)")
    parser.add_argument("--thinking-level", default=None)
    parser.add_argument("--thinking-budget", type=int, default=None)
    parser.add_argument("--media-resolution", default=None)
    parser.add_argument("--no-compression", action="store_true")
    parser.add_argument("--voice-name", default=None)
    parser.add_argument("--say-voice", default="Samantha")
    parser.add_argument("--view-seconds", type=float, default=2.5)
    parser.add_argument("--turn-timeout", type=float, default=25.0)
    parser.add_argument("--soak", type=int, default=0, help="seconds; run the session-length test instead")
    parser.add_argument("--tag", default="live")
    args = parser.parse_args()

    if not load_keys().get("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY is not set (see ~/Desktop/micromind.txt)")
    all_questions = json.loads((BENCH / "questions.json").read_text())["questions"]
    questions = [all_questions[int(i)] for i in args.questions.split(",")]
    prepared = {q["frame"]: prepare(q) for q in questions}
    models = args.models.split(",")
    out = BENCH / "results" / f"{args.tag}.json"

    if args.soak:
        results = await asyncio.gather(*(soak(m, args, prepared[questions[0]["frame"]]["clean"]) for m in models))
        print(json.dumps(results, indent=2))
        out.write_text(json.dumps({"when": time.strftime("%Y-%m-%d %H:%M"), "soak": results}, indent=2))
        return

    with tempfile.TemporaryDirectory() as tmp:
        speech = {q["question"]: tts(q["question"], Path(tmp), args.say_voice) for q in questions}
    vision = GeminiVision(args.grounding_model, thinking_level="low") if args.grounding == "real" else None

    sessions = []
    for repeat in range(args.repeats):
        for model in models:
            print(f"session {repeat + 1}/{args.repeats} {model}")
            sessions.append(await run_session(model, args, questions, prepared, speech, vision))
            if sessions[-1]["error"]:
                print(f"  error: {sessions[-1]['error']}")
    rows = [r for s in sessions for r in s["rows"]]
    table = summary_table(rows)
    print(json.dumps(table, indent=2))
    settings = {k: v for k, v in vars(args).items() if k not in ("tag",)}
    out.write_text(json.dumps({
        "when": time.strftime("%Y-%m-%d %H:%M"),
        "source": "saved bench frames (still views from the June 14 recordings), speech from macOS say",
        "settings": settings,
        "summary": table,
        "sessions": [{k: v for k, v in s.items() if k != "rows"} for s in sessions],
        "rows": rows,
    }, indent=2, default=str))
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
