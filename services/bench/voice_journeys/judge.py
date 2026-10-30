"""What only judgement can score in a partner step, the answer's quality (the spec, section 2: "the model judge scores
only what needs judgement"): whether the request was answered, whether the answer meets the step's expectation, and how
much it felt like a sharp colleague (`partner_quality`, 0-3: the scoreboard's Overall line). Everything the app records
(false "done" from receipts, what opened, which researcher, outcomes said) is scored by standard.py, never here. The judge sees the camera picture the
plan names (the bundled still), the user's words, the voice's lines and the tools with their confirmations.

The judge is a choice (`--judge`): GPT-6.1 Sol (the default, so new runs compare with the old ones), Claude Opus 5.5
(the Messages API over plain httpx, as voice/think.py) or Gemini 3.8 Flash, each given the same prompt. With "auto" the
default is used unless its key is missing or a tiny call to it fails (credit run out: 2026-10-29), then Claude, then
Gemini. Judges are not interchangeable: the one used is kept with the verdicts and shown on the scoreboard.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .standard import load_bench, released, step_view, thinks, unprompted_spans, windows

JUDGE = "openai:gpt-6.1-sol@low"  # as score.py: another model than the voices' own
# Each judge: its key, and a judged step's worst cost (one image, about 3,000 tokens in; its output cap, thinking
# included). The order is "auto"'s: the default first.
JUDGES = {
    "gpt-6.1-sol": {"spec": JUDGE, "key": "OPENAI_API_KEY", "usd": 0.03},
    "claude-opus-5-5": {"key": "ANTHROPIC_API_KEY", "usd": 0.10},  # $4 / $20 per MTok, up to 4,000 out
    "gemini-3.8-flash": {"spec": "gemini:gemini-3.8-flash@low", "key": "GEMINI_API_KEY", "usd": 0.01},
}
DEFAULT_JUDGE = "gpt-6.1-sol"
COST_PER_STEP_USD = max(j["usd"] for j in JUDGES.values())  # whichever judge runs: the worst case reserved
CLAUDE_MAX_TOKENS = 4000  # thinking is always on for Opus 5.5 and counts here; the verdict itself is about 300


@dataclass
class Reply:
    data: dict | None
    error: str | None = None
    raw_text: str = ""
    usage: dict = field(default_factory=dict)


def json_of(text: str) -> dict | None:
    """The verdict in a reply's text: the whole text, a fenced block, or the outermost braces."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    for candidate in (text, fenced.group(1) if fenced else "", text[text.find("{"):text.rfind("}") + 1]):
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


class ClaudeJudge:
    """Claude Opus 5.5 over the Messages API, plain httpx (as voice/think.py). Thinking is always on for this model;
    effort low. No prefill on this model: the JSON is read from the reply's text. Server-side fallbacks are on, so a
    safety decline is answered by another model rather than not at all."""

    model = "claude-opus-5-5"

    def __init__(self, client=None) -> None:
        self._client = client

    def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(base_url="https://api.anthropic.com", timeout=120.0, headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                "anthropic-beta": "server-side-fallback-2026-07-01", "content-type": "application/json"})
        return self._client

    async def ask(self, images: list[bytes], prompt: str, max_output_tokens: int = 600) -> Reply:
        content: list[dict] = []
        for picture in images:
            kind = "image/png" if picture[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
            content.append({"type": "image", "source": {"type": "base64", "media_type": kind,
                                                        "data": base64.b64encode(picture).decode()}})
        content.append({"type": "text", "text": prompt})
        body = {"model": self.model, "max_tokens": max(CLAUDE_MAX_TOKENS, max_output_tokens),
                "output_config": {"effort": "low"}, "fallbacks": "default",
                "messages": [{"role": "user", "content": content}]}
        try:
            r = await self._http().post("/v1/messages", json=body)
        except Exception as exc:  # noqa: BLE001 - a judge that can't be reached is said, never fatal for a run
            return Reply(None, f"{type(exc).__name__}: {exc}"[:300])
        if r.status_code != 200:
            return Reply(None, f"HTTP {r.status_code}: {r.text[:200]}")
        reply = r.json()
        usage = reply.get("usage") or {}
        if reply.get("stop_reason") == "refusal":
            return Reply(None, f"refused ({(reply.get('stop_details') or {}).get('category')})", usage=usage)
        text = "".join(b.get("text", "") for b in reply.get("content") or [] if b.get("type") == "text")
        data = json_of(text)
        return Reply(data, None if data is not None else f"no JSON (stop: {reply.get('stop_reason')})", text, usage)


def make_judge(name: str):
    if name == "claude-opus-5-5":
        return ClaudeJudge()
    from orchestrator.vision import make_vision

    return make_vision(JUDGES[name]["spec"])


async def works(name: str, judge) -> str | None:
    """None when this judge answers a tiny call now; else why not (no key, credit, refused key...)."""
    if not os.environ.get(JUDGES[name]["key"]):
        return f"no {JUDGES[name]['key']}"
    try:
        result = await judge.ask([], 'Reply with JSON only: {"ok": true}', max_output_tokens=16)
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"[:200]
    return getattr(result, "error", None) or None


async def achoose(name: str = "auto", tried: list | None = None):
    """(the judge's name, a judge): the one named, or with "auto" the first whose key and credit work now (the default
    first). `tried` collects each one passed over ("gpt-6.1-sol: HTTP 429 ..."). (None, None) when none works."""
    from orchestrator.keys import load_keys

    load_keys()
    for candidate in ([name] if name != "auto" else list(JUDGES)):
        why = await works(candidate, make_judge(candidate)) if name == "auto" else None
        if why is None:
            return candidate, make_judge(candidate)  # a fresh one: its client belongs to the loop that uses it
        if tried is not None:
            tried.append(f"{candidate}: {why}")
    return None, None


def choose(name: str = "auto", tried: list | None = None):
    return asyncio.run(achoose(name, tried))


PROMPT = """You are scoring a live voice research partner for a microscope app against a strict standard. Image 1 (if
given) is the specimen the camera showed.

{earlier}The user said: "{say}"
What a good answer does here: {expect}{interrupted}
What the voice said afterwards, in order (seconds after the user finished):
{lines}
What the app did (seconds after the user finished; "confirmed" = the studio reported the change on screen):
{actions}
{rows}
Judge from these facts only:
- answered: true if, by the end, the user got what they asked for (or was told plainly why not and what to do next).
- meets_expectation: true if the answer does what "a good answer does here" says; null if that says nothing.
- partner_quality: 0-3, how much the reply felt like a sharp, dynamic colleague at the microscope. 3: specific to what
  is on screen and in the Project (names the thing, the count, the table, the researcher), points at what it talks
  about (a mark or a highlight, in the actions), uses the earlier turns ("that one", "the second researcher") without
  asking again, brief and natural, no filler or hedging. 2: right but generic, a little long, or a missed chance to
  point. 1: vague, hedging, filler, robotic, or narrating its own machinery. 0: wrong, unhelpful or off-putting.
  A reply to small talk ("thanks") is a 3 when it is a few warm words and does nothing else.
  Calibration, for "What's that bright one at the top?":
    3: "That's a dividing cell: its chromosomes have condensed, so it glows brighter. I've circled it." (mark: ok)
    0: "I can see the image. It appears to contain various cellular structures. Let me know if you'd like me to
       analyze anything further." (no action)
- why: one sentence.

Reply with JSON only:
{{"answered": true, "meets_expectation": true, "partner_quality": 2, "why": ""}}"""


def receipt(text: str, limit: int = 240) -> str:
    """A receipt's words for the judge: one line, without window or source ids, bounded."""
    text = re.sub(r"\b(?:w-[0-9a-f-]{8,}|[0-9a-f]{8}-[0-9a-f-]{27,})\b", "", " ".join(str(text).split()))
    return text[:limit] + ("…" if len(text) > limit else "")


def facts(bench: dict, conv: list[dict], inter: list[dict]) -> list[dict]:
    """Each scored step's words, lines and actions, relative to its release (what PROMPT is given)."""
    bench = load_bench(bench)
    steps = [s for s in bench["steps"] if not s.get("setup")]
    out, unprompted, tasks = [], unprompted_spans(inter), thinks(inter)
    for i, (step, (start, end)) in enumerate(zip(steps, windows(steps))):
        if step.get("kind") in ("idle", "restart"):
            continue
        release = released(step)
        # The next request interrupted this answer on purpose (the plan's barge-in): judge what was said before it.
        cut = steps[i + 1] if i + 1 < len(steps) and steps[i + 1].get("interrupt") else None
        _, calls, _, lines = step_view(bench, conv, inter, (start, end), unprompted, tasks)
        # The requests Hooke had in its rows in the chat as the user spoke, each in its latest state then (what the user
        # sees): "is the video search done?" is judged against the search's own row, however many steps back it was.
        latest: dict[str, dict] = {}
        for r in conv:
            if r.get("type") == "handoff" and isinstance(r.get("at"), (int, float)) and r["at"] <= release + 0.5:
                latest[str(r.get("id"))] = r
        rows = [f'- "{r.get("label") or r.get("request") or ""}": {r.get("state")}'
                + (f' ({receipt(r.get("answer"), 160)})' if r.get("answer") and r.get("state") != "working" else "")
                for r in latest.values()][-6:]
        out.append({"id": step["id"], "say": step.get("say", ""), "release": release, "said": [l["text"] for l in lines],
                    "rows": rows,
                    "interruptedS": round((cut.get("down") or cut["start"]) - release, 1) if cut else None,
                    "lines": [f"{i + 1}. (+{l['first'] - release:.1f} s) {l['text']}" for i, l in enumerate(lines)],
                    "actions": [f"- (+{c.start - release:.1f} s) {c.name}: "
                                f"{'ok' if c.ok else 'failed' if c.ok is False else 'running'}"
                                + (f", confirmed +{c.confirmed_at - release:.1f} s" if c.confirmed_at is not None else
                                   ", not confirmed" if c.screen_change else "")
                                + (f" ({c.error[:120]})" if c.error else "")
                                + (f"; behind it: {', '.join(str(t.get('name')) for t in c.inner)}" if c.inner else "")
                                # What the app's own receipt says it did, so a claim ("a new Tab called Cell results")
                                # is checked against it (will-display, 29 Oct: the receipt said so; the judge saw
                                # only "ok, confirmed" and called the claim unsupported).
                                + (f'; its receipt: "{receipt(c.summary)}"' if c.summary else "")
                                for c in calls]})
    return out


async def verdicts(bench: dict, conv: list[dict], inter: list[dict], plan: dict, picture: bytes | None,
                   model=None, name: str = DEFAULT_JUDGE, only: set | None = None,
                   pictures: dict[str, bytes] | None = None) -> dict:
    """{step id: verdict} for every scored step (answered, meets_expectation, partner_quality, why), and
    "_judge": the judge's name."""
    if model is None:
        name, model = await achoose(name)
        if model is None:
            raise RuntimeError("no judge answers now (keys or credit)")
    expects = {s["id"]: s.get("expect") or {} for s in plan["steps"]}
    out, history = {"_judge": name}, []
    for step in facts(bench, conv, inter):
        expect = expects.get(step["id"], {})
        # The two turns before, so "that one" and "the second researcher" can be judged as references.
        # ...and what the app did then, so "is it done?" is judged against the earlier work's own record.
        earlier = "".join(f'Earlier, the user said "{say}"; the voice said: {" ".join(said)[:300] or "(nothing)"}'
                          + (f'; the app did: {" ".join(did)[:400]}' if did else "") + "\n"
                          for say, said, did in history[-2:])
        history.append((re.sub(r"\[\[slnc \d+\]\] ?", "", step["say"]), step["said"],
                        [a.lstrip("- ") for a in step["actions"]]))
        if only is not None and step["id"] not in only:
            continue
        interrupted = ("" if step["interruptedS"] is None else
                       f"\nThe user interrupted this answer on purpose about {step['interruptedS']} s in, with their next "
                       "request (a test of stopping at once). Judge only what was said before that: being cut off is not "
                       "a failure to answer, a good start counts as answered, and whether it stopped at once and gave "
                       "what was asked next is judged on that next request.")
        prompt = PROMPT.format(earlier=earlier, interrupted=interrupted, say=re.sub(r"\[\[slnc \d+\]\] ?", "", step["say"]),
                               expect=expect.get("judge") or (f"shows {expect['visible']}" if expect.get("visible") else
                                                              "answers what was asked"),
                               lines="\n".join(step["lines"]) or "(nothing)",
                               actions="\n".join(step["actions"]) or "(nothing)",
                               rows=("Requests in Hooke's chat when the user spoke (their rows, latest state):\n"
                                     + "\n".join(step["rows"]) + "\n") if step.get("rows") else "")
        seen = picture or (pictures or {}).get(step["id"])
        result = await model.ask([seen] if seen else [], prompt, max_output_tokens=600)
        data = result.data if isinstance(getattr(result, "data", None), dict) else None
        if data is None:
            out[step["id"]] = {"error": (getattr(result, "error", None) or getattr(result, "raw_text", ""))[:200]}
            continue
        # Answer quality only: what the app records (false "done", what opened, which researcher, outcomes said) is
        # scored from receipts and app state (standard.py), never from the judge's reading.
        out[step["id"]] = {k: data[k] for k in ("answered", "meets_expectation", "partner_quality", "why") if k in data}
    return out


def step_pictures(bench: dict, inter: list[dict], logs: Path | None) -> dict[str, bytes]:
    """What the camera showed at each step, for a plan played from footage (no still): the newest frame the app kept
    at or just after the user spoke (a brain turn's view, the running look's), from the stack's logs. Without it the
    judge saw no picture and guessed (will-alive, 29 Oct: a stoma on the leaf footage judged "a fluorescent cell")."""
    if logs is None:
        return {}
    kept = sorted((r["t"], logs / r["image"]) for r in inter if r.get("kind") in ("turn", "sight")
                  and isinstance(r.get("t"), (int, float)) and isinstance(r.get("image"), str))
    kept += sorted((p.stat().st_mtime, p) for p in (logs / "crops").rglob("*-sight-*.jpg")) if (logs / "crops").exists() else []
    kept.sort()
    out = {}
    for step in [s for s in load_bench(bench)["steps"] if not s.get("setup")]:
        release = released(step)
        if not isinstance(release, (int, float)):
            continue
        chosen = [p for t, p in kept if t <= release + 8.0 and p.exists()]
        if chosen:
            out[step["id"]] = chosen[-1].read_bytes()
    return out


def judge_run(bench: dict, conv: list[dict], inter: list[dict], plan: dict, checkout: Path, *, name: str = DEFAULT_JUDGE,
              model=None, only: set | None = None, logs: Path | None = None) -> dict:
    still = (plan.get("camera") or {}).get("still")
    picture = (checkout / still).read_bytes() if still and (checkout / still).exists() else None
    pictures = {} if picture else step_pictures(bench, inter, logs)
    return asyncio.run(verdicts(bench, conv, inter, plan, picture, model=model, name=name, only=only,
                                pictures=pictures))


def judged_by(verdicts: dict | None) -> str | None:
    """Which judge a run's kept verdicts came from (those from before the choice: the default, unrecorded)."""
    if not verdicts:
        return None
    return verdicts.get("_judge") or f"{DEFAULT_JUDGE} (unrecorded)"

