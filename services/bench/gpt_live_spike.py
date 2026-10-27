"""Spike S2: how GPT-Live (`gpt-live-1`) handles several delegations, late results,
proactive commentary and silent context, through our adapter
(orchestrator/voice/openai_live.py).

One session (under 3 minutes). The microphone is synthesized speech (macOS `say`, 16 kHz,
resampled to 24 kHz by the adapter) streamed in real time in 40 ms chunks, with silence
between utterances. The system prompt is the production one (LIVE_VOICE_DELEGATE); findings
are canned instead of running the vision model.

  A   three questions in a row, each delegation answered after 1.5 s
  B   one question whose delegation is answered only after 6 s
  C1  idle proactive line: commentary.append(delegation_id=None) after 3 s of quiet
  C2  fallback: instructions.append("Tell the user now: ...") after 3 s of quiet
  C3  extra: a second commentary.append on an already-answered delegation id (A3's)
  D   silent context: thinking.append about a chat exchange, then "what did I just ask in chat?"

The WebSocket streams output audio continuously (silence included), so speech is detected by
loudness (SPEECH_PEAK) and timed as a client would play it back to back. Times are from the
end of the user's speech (A, B, D) or from when the command was sent (C).

Usage (from services/; keys from ~/Desktop/micromind.txt):
  .venv/bin/python -m bench.gpt_live_spike [--wav /path/to/output.wav]
  .venv/bin/python -m bench.gpt_live_spike --part rerun --poke-wait 6   # ~1 min re-check
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from bench.live_probe import tts, voiced_span
from orchestrator.keys import load_keys
from orchestrator.prompts import LIVE_VOICE_DELEGATE
from orchestrator.voice.base import ToolSpec, VoiceConfig, VoiceEvent
from orchestrator.voice.openai_live import SPEECH_PEAK, OpenAILiveSession

RATE_IN = 16000
CHUNK_MS = 40
CHUNK_BYTES = RATE_IN * 2 * CHUNK_MS // 1000
OUT_RATE = 24000
HARD_CAP_S = 175
RESULTS = Path(__file__).resolve().parent / "results"


@dataclass
class Step:
    name: str
    say: str
    delay: float  # seconds before the delegation is answered
    answer: str
    outlined: list[str]


STEPS_A = [
    Step("A1", "What's the oval in the middle?", 1.5,
         "It is most likely a stoma: an oval pore between two bean-shaped guard cells.", ["stoma"]),
    Step("A2", "Zoom in on it.", 1.5,
         "The view is now zoomed in two times on the stoma; the two guard cells fill most of the picture.", ["stoma"]),
    Step("A3", "Start a researcher to make a diagram of it.", 1.5,
         "A researcher has started on a labelled diagram of the stoma and its guard cells; it should take about a minute.", []),
]
STEP_B = Step("B", "What's the dark dot near the top?", 6.0,
              "It is most likely a speck of dust or debris on the slide, not part of the leaf.", ["debris speck"])
STEP_D = Step("D", "What did I just ask in chat?", 1.5,
              "The view still shows the stoma outlined earlier; nothing new is visible.", [])

C1_COMMENTARY = "Quick update: the researcher thinks the left one is a trichome base, not a stoma."
C2_INSTRUCTION = ("Tell the user now: the slide has drifted slightly out of focus; "
                  "suggest a small turn of the fine focus knob.")
C3_COMMENTARY = "The researcher finished: the labelled diagram of the stoma and its two guard cells is ready on screen."
# Rerun (--part rerun): the same note framed as context only.
D_THINKING_QUIET = ("Context only; do not say this aloud unless the user asks about it. The user typed in chat: "
                    "what is a guard cell? The chat already answered them in writing: a guard cell is one of the two "
                    "bean-shaped cells around a stoma; they swell or shrink to open and close the pore.")
D_THINKING = ("The user typed in chat: what is a guard cell? You answered in chat: a guard cell is one of the "
              "two bean-shaped cells around a stoma; they swell or shrink to open and close the pore.")


class Recorder:
    """Raw server events, output audio (as a client would play it) and phase labels."""

    def __init__(self) -> None:
        self.t0: float | None = None
        self.phase = "start"
        self.rows: list[dict[str, Any]] = []
        self.audio = bytearray()
        self.cursor = 0.0
        self.spans: list[list[Any]] = []  # [start, end, phase] of loud (spoken) playback
        self.last_loud_end = 0.0
        self.counts: dict[str, int] = {}
        self.usage: Any = None

    def raw(self, ev: Any) -> None:
        now = time.monotonic()
        kind = getattr(ev, "type", None) or (ev.get("type") if isinstance(ev, dict) else None)
        self.counts[kind] = self.counts.get(kind, 0) + 1
        if kind == "session.started" and self.t0 is None:
            self.t0 = now
        if kind == "session.output_audio.delta":
            audio = base64.b64decode(getattr(ev, "delta", "") or "")
            samples = np.frombuffer(audio[: len(audio) // 2 * 2], np.int16)
            dur = len(samples) / OUT_RATE
            start = max(self.cursor, now)
            self.cursor = start + dur
            self.audio += audio
            peak = int(np.abs(samples.astype(np.int32)).max()) if len(samples) else 0
            if peak >= SPEECH_PEAK:
                if self.spans and start - self.spans[-1][1] < 0.6:
                    self.spans[-1][1] = start + dur
                else:
                    self.spans.append([start, start + dur, self.phase])
                self.last_loud_end = start + dur
            return
        row: dict[str, Any] = {"t": now, "phase": self.phase, "type": kind}
        if kind in ("session.output_transcript.delta", "session.input_transcript.delta"):
            row.update(text=ev.delta, start_ms=getattr(ev, "start_ms", None), end_ms=getattr(ev, "end_ms", None))
        elif kind == "session.delegation.created":
            row.update(id=ev.delegation.id, target=ev.delegation.target, offset_ms=ev.offset_ms)
        elif kind in ("session.commentary.appended", "session.instructions.appended", "session.thinking.appended"):
            row.update(start_ms=getattr(ev, "start_ms", None), end_ms=getattr(ev, "end_ms", None),
                       client_event_id=getattr(ev, "client_event_id", None))
        elif kind == "session.usage.updated":
            self.usage = _dump(getattr(ev, "usage", None))
            return
        else:
            row["data"] = _dump(ev)
        self.rows.append(row)

    def first_speech_after(self, t: float) -> list[Any] | None:
        return next((s for s in self.spans if s[0] >= t), None)


def _dump(obj: Any) -> Any:
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        data = obj.model_dump(exclude_none=True)
    elif isinstance(obj, dict):
        data = dict(obj)
    else:
        return str(obj)[:300]
    data.pop("delta", None)
    data.pop("audio", None)
    text = json.dumps(data, default=str)
    return json.loads(text) if len(text) <= 2000 else text[:2000]


class SpikeSession(OpenAILiveSession):
    """The production adapter, plus a raw-event tap."""

    def __init__(self, config: VoiceConfig, recorder: Recorder) -> None:
        super().__init__(config)
        self.recorder = recorder

    def handle(self, ev: Any) -> None:
        self.recorder.raw(ev)
        super().handle(ev)


class Mic:
    """Streams 16 kHz PCM in real time: silence unless an utterance is queued."""

    def __init__(self, session: OpenAILiveSession) -> None:
        self.session = session
        self.pending = bytearray()
        self.voiced = (0, 0)
        self.utt_sent = 0
        self.total_sent = 0  # bytes of input audio sent since the session started
        self.speech_start_at: float | None = None
        self.speech_end_at: float | None = None
        self.speech_end_audio_ms: float | None = None
        self.done = asyncio.Event()

    def say(self, samples: np.ndarray) -> None:
        start, end = voiced_span(samples)
        self.pending = bytearray(samples.tobytes())
        self.voiced = (start * 2, end * 2)
        self.utt_sent = 0
        self.speech_start_at = self.speech_end_at = self.speech_end_audio_ms = None
        self.done.clear()

    async def run(self) -> None:
        silence = bytes(CHUNK_BYTES)
        next_at = time.monotonic()
        while not self.session.closed:
            if self.pending:
                chunk = bytes(self.pending[:CHUNK_BYTES]).ljust(CHUNK_BYTES, b"\0")
                del self.pending[:CHUNK_BYTES]
                lo, hi = self.utt_sent, self.utt_sent + CHUNK_BYTES
                await self.session.send_audio(chunk)
                now = time.monotonic()
                if self.speech_start_at is None and lo <= self.voiced[0] < hi:
                    self.speech_start_at = now - (hi - self.voiced[0]) / (RATE_IN * 2)
                if self.speech_end_at is None and lo < self.voiced[1] <= hi:
                    self.speech_end_at = now - (hi - self.voiced[1]) / (RATE_IN * 2)
                    self.speech_end_audio_ms = (self.total_sent + self.voiced[1] - lo) / (RATE_IN * 2) * 1000
                self.utt_sent = hi
                self.total_sent += CHUNK_BYTES
                if not self.pending:
                    self.done.set()
            else:
                await self.session.send_audio(silence)
                self.total_sent += CHUNK_BYTES
            next_at += CHUNK_MS / 1000
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))


async def wait_until(pred, timeout: float, poll: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(poll)
    return bool(pred())


class Spike:
    def __init__(self, args) -> None:
        self.args = args
        self.rec = Recorder()
        self.current: Step | None = None
        self.calls: list[dict[str, Any]] = []  # delegations as the adapter saw them, with our answers
        tool = ToolSpec("look", "Look at the microscope view and answer.", {"type": "object"}, self.look)
        self.session = SpikeSession(VoiceConfig(model=args.model, system_prompt=LIVE_VOICE_DELEGATE, tools=[tool]), self.rec)
        self.session.on(self._on_event)
        self.mic = Mic(self.session)
        self.steps: list[dict[str, Any]] = []

    def _on_event(self, event: VoiceEvent) -> None:
        if event.kind == "tool_call":
            self.calls.append({"id": event.call_id, "at": event.at, "phase": self.rec.phase,
                               "task": event.args.get("question"), "resultAt": None})
        elif event.kind == "tool_result":
            for c in self.calls:
                if c["id"] == event.call_id:
                    c["resultAt"] = event.at

    async def look(self, args: dict) -> dict:
        step = self.current or STEP_D
        await asyncio.sleep(step.delay)
        return {"answer": step.answer, "outlined": step.outlined}

    def quiet_for(self, seconds: float) -> bool:
        return time.monotonic() - self.rec.last_loud_end >= seconds

    # --- phases ------------------------------------------------------------------------
    async def ask(self, step: Step, speech: np.ndarray) -> dict:
        self.rec.phase = step.name
        self.current = step
        t_start = time.monotonic()
        n_calls = len(self.calls)
        self.mic.say(speech)
        await self.mic.done.wait()
        end = self.mic.speech_end_at
        end_audio_ms = self.mic.speech_end_audio_ms
        await wait_until(lambda: len(self.calls) > n_calls, 8.0)
        mine = lambda: self.calls[n_calls:]  # noqa: E731
        await wait_until(lambda: all(c["resultAt"] for c in mine()) and not self.session._tool_tasks, step.delay + 12)
        last_result = max((c["resultAt"] for c in mine() if c["resultAt"]), default=end)
        await wait_until(lambda: self.rec.first_speech_after(last_result) is not None, 8.0)
        await wait_until(lambda: self.quiet_for(2.0) and not self.session._tool_tasks, 15.0)
        t_end = time.monotonic()
        return self.summarise_ask(step, t_start, t_end, end, end_audio_ms, mine())

    async def poke(self, name: str, kind: str, content: str, delegation_id: str | None) -> dict:
        """Send a command after 3 s of quiet; did the model speak, and what?"""
        self.rec.phase = name
        await wait_until(lambda: self.quiet_for(3.0), 15.0)
        t_start = time.monotonic()
        resource = getattr(self.session._conn.session, kind)
        await resource.append(content=content, delegation_id=delegation_id, event_id=f"spike-{name.lower()}")
        sent = time.monotonic()
        spoke = await wait_until(lambda: self.rec.first_speech_after(sent - 0.05) is not None, self.args.poke_wait)
        if spoke:
            await wait_until(lambda: self.quiet_for(2.5), 15.0)
        t_end = time.monotonic()
        return self.summarise_window(name, t_start, t_end, sent, extra={
            "command": kind, "content": content, "delegationId": delegation_id, "spoke": spoke,
        })

    # --- summaries ---------------------------------------------------------------------
    def _window(self, t_start: float, t_end: float, ref: float) -> dict:
        rel = lambda t: None if t is None or ref is None else round((t - ref) * 1000)  # noqa: E731
        rows = [r for r in self.rec.rows if t_start <= r["t"] <= t_end + 0.5]
        out = [r for r in rows if r["type"] == "session.output_transcript.delta"]
        spans = [s for s in self.rec.spans if t_start <= s[0] <= t_end]
        timeline: list[list[Any]] = []
        line: list[Any] | None = None
        for r in rows:
            if r["type"] == "session.output_transcript.delta":
                if line is not None and line[1] == "model said" and r["t"] - line[3] < 0.8:
                    line[2] += r["text"]
                    line[3] = r["t"]
                    continue
                line = [rel(r["t"]), "model said", r["text"], r["t"]]
                timeline.append(line)
            elif r["type"] == "session.input_transcript.delta":
                if line is not None and line[1] == "heard" and r["t"] - line[3] < 1.5:
                    line[2] += r["text"]
                    line[3] = r["t"]
                    continue
                line = [rel(r["t"]), "heard", r["text"], r["t"]]
                timeline.append(line)
            else:
                line = None
                detail = {k: v for k, v in r.items() if k not in ("t", "phase", "type")}
                timeline.append([rel(r["t"]), r["type"], detail, r["t"]])
        for s in spans:
            timeline.append([rel(s[0]), "speech (loud audio)", f"{s[1] - s[0]:.1f} s", s[0]])
        timeline.sort(key=lambda x: x[3])
        return {
            "outputTranscript": "".join(r["text"] for r in out).strip(),
            "speechSpans": [[rel(s[0]), rel(s[1])] for s in spans],
            "speechSeconds": round(sum(s[1] - s[0] for s in spans), 1),
            "timeline": [x[:3] for x in timeline],
        }

    def summarise_ask(self, step: Step, t_start, t_end, end, end_audio_ms, calls) -> dict:
        rel = lambda t: None if t is None or end is None else round((t - end) * 1000)  # noqa: E731
        win = self._window(t_start, t_end, end)
        first_result = min((c["resultAt"] for c in calls if c["resultAt"]), default=None)
        out_rows = [r for r in self.rec.rows if t_start <= r["t"] <= t_end + 0.5 and r["type"] == "session.output_transcript.delta"]
        before = "".join(r["text"] for r in out_rows if first_result is None or r["t"] < first_result).strip()
        after = "".join(r["text"] for r in out_rows if first_result is not None and r["t"] >= first_result).strip()
        heard = "".join(r["text"] for r in self.rec.rows if t_start <= r["t"] <= t_end and r["type"] == "session.input_transcript.delta").strip()
        raw_deleg = [r for r in self.rec.rows if t_start <= r["t"] <= t_end and r["type"] == "session.delegation.created"]
        first_speech = next((s for s in self.rec.spans if end and s[1] > end - 1.0 and s[0] <= t_end), None)
        speech_after_result = self.rec.first_speech_after(first_result) if first_result else None
        waiting = [s for s in self.rec.spans if calls and first_result and calls[0]["at"] <= s[0] < first_result]
        return {
            "step": step.name, "said": step.say, "resultDelayS": step.delay, "heard": heard,
            "delegations": len(calls),
            "delegationMs": [rel(c["at"]) for c in calls],
            "delegationTimelineMinusSpeechEndMs": [round(r["offset_ms"] - end_audio_ms) for r in raw_deleg] if end_audio_ms else None,
            "delegationTasks": [c["task"] for c in calls],
            "resultSentMs": [rel(c["resultAt"]) for c in calls],
            "firstSpeechMs": rel(first_speech[0]) if first_speech else None,
            "speechWhileWaitingS": round(sum(min(s[1], first_result) - s[0] for s in waiting), 1) if waiting else 0.0,
            "resultToSpeechMs": round((speech_after_result[0] - first_result) * 1000) if speech_after_result else None,
            "saidBeforeResult": before,
            "saidAfterResult": after,
            **win,
        }

    def summarise_window(self, name: str, t_start, t_end, ref, extra: dict) -> dict:
        win = self._window(t_start, t_end, ref)
        first = self.rec.first_speech_after(ref - 0.05)
        acks = [r for r in self.rec.rows if t_start <= r["t"] <= t_end and r["type"].endswith(".appended")]
        errors = [r for r in self.rec.rows if t_start <= r["t"] <= t_end and r["type"] == "error"]
        return {
            "step": name, **extra,
            "speechStartMs": round((first[0] - ref) * 1000) if first and first[0] <= t_end else None,
            "appended": [{k: v for k, v in r.items() if k not in ("t", "phase")} for r in acks],
            "errors": [r.get("data") for r in errors],
            **win,
        }

    # --- run -----------------------------------------------------------------------------
    async def run(self, speech: dict[str, np.ndarray]) -> None:
        await self.session.start()
        drain = asyncio.create_task(self._drain())
        mic_task = asyncio.create_task(self.mic.run())
        try:
            await wait_until(lambda: self.rec.t0 is not None, 10.0)
            await asyncio.sleep(1.5)
            if self.args.part == "rerun":
                # Re-check the two surprises of the full run: "zoom in on it" got no reply,
                # and the thinking note was spoken aloud.
                for step in STEPS_A[:2]:
                    self.steps.append(await self.ask(step, speech[step.say]))
                    self._print(self.steps[-1])
                self.current = None
                note = await self.poke("D-note-quiet", "thinking", D_THINKING_QUIET, None)
                self.steps.append(note)
                self._print(note)
                self.steps.append(await self.ask(STEP_D, speech[STEP_D.say]))
                self._print(self.steps[-1])
                return
            for step in STEPS_A:
                self.steps.append(await self.ask(step, speech[step.say]))
                self._print(self.steps[-1])
            self.steps.append(await self.ask(STEP_B, speech[STEP_B.say]))
            self._print(self.steps[-1])
            self.current = None
            self.steps.append(await self.poke("C1", "commentary", C1_COMMENTARY, None))
            self._print(self.steps[-1])
            self.steps.append(await self.poke("C2", "instructions", C2_INSTRUCTION, None))
            self._print(self.steps[-1])
            a3 = next((c["id"] for c in self.calls if c["phase"] == "A3"), None)
            if a3:
                self.steps.append(await self.poke("C3", "commentary", C3_COMMENTARY, a3))
                self._print(self.steps[-1])
            # D: silent note, check it stays silent, then ask about it by voice.
            note = await self.poke("D-note", "thinking", D_THINKING, None)
            self.steps.append(note)
            self._print(note)
            self.steps.append(await self.ask(STEP_D, speech[STEP_D.say]))
            self._print(self.steps[-1])
        finally:
            mic_task.cancel()
            await self.session.close()
            drain.cancel()

    async def _drain(self) -> None:
        async for _ in self.session.events():
            pass

    @staticmethod
    def _print(row: dict) -> None:
        if "delegations" in row:
            print(f"[{row['step']}] heard {row['heard']!r}: {row['delegations']} delegation(s) at {row['delegationMs']} ms, "
                  f"first speech {row['firstSpeechMs']} ms, result->speech {row['resultToSpeechMs']} ms")
            print(f"     before result: {row['saidBeforeResult']!r}")
            print(f"     after result:  {row['saidAfterResult']!r}")
        else:
            print(f"[{row['step']}] {row['command']}: spoke={row['spoke']} start {row['speechStartMs']} ms "
                  f"errors={row['errors'] or '-'}")
            print(f"     said: {row['outputTranscript']!r}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gpt-live-1")
    parser.add_argument("--say-voice", default="Samantha")
    parser.add_argument("--poke-wait", type=float, default=8.0, help="seconds to wait for speech after a C/D command")
    parser.add_argument("--wav", default=None, help="also save the model's output audio here (24 kHz mono)")
    parser.add_argument("--part", choices=["all", "rerun"], default="all",
                        help="rerun: only A1, A2, a context-only thinking note and the D question")
    parser.add_argument("--tag", default=None)
    args = parser.parse_args()
    args.tag = args.tag or ("spike-s2-gpt-live" if args.part == "all" else "spike-s2-gpt-live-rerun")
    if not load_keys().get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set (see ~/Desktop/micromind.txt)")
    with tempfile.TemporaryDirectory() as tmp:
        speech = {s.say: tts(s.say, Path(tmp), args.say_voice) for s in [*STEPS_A, STEP_B, STEP_D]}
    spike = Spike(args)
    t0 = time.monotonic()
    error = ""
    try:
        await asyncio.wait_for(spike.run(speech), HARD_CAP_S)
    except asyncio.TimeoutError:
        error = f"hit the {HARD_CAP_S} s cap"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:400]
    lasted = round(time.monotonic() - t0, 1)
    others = [{"phase": r["phase"], "type": r["type"], "data": r.get("data")} for r in spike.rec.rows
              if r["type"] in ("error", "info", "session.closed")]
    out = RESULTS / f"{args.tag}.json"
    out.write_text(json.dumps({
        "when": time.strftime("%Y-%m-%d %H:%M"),
        "model": args.model,
        "source": "synthesized speech (macOS say) as the microphone; canned findings; production LIVE_VOICE_DELEGATE prompt",
        "sessionSeconds": lasted,
        "error": error,
        "usage": spike.rec.usage,
        "eventCounts": spike.rec.counts,
        "sessionEvents": others,
        "steps": spike.steps,
    }, indent=2, default=str))
    if args.wav:
        with wave.open(args.wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(OUT_RATE)
            w.writeframes(bytes(spike.rec.audio))
    print(f"session {lasted} s{' error: ' + error if error else ''}; wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
