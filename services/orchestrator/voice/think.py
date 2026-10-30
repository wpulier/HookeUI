"""`think`: the frontier mind behind the one-brain voice (option THINK; part-2.5 architecture, item 2).

The voice in front (gpt-realtime-2.1) hears, sees and answers at once; a hard question, many steps or a deep look goes
behind it, to a frontier model with Hooke's tools: Claude Opus 5.5 by default, GPT-6.1 Sol as an option. On τ-Voice a
fast voice with a frontier backend scored 74.5% where the speech model alone scored 45.7%.

What the backend gets: the voice's `request` and `why`, the user's own words this turn, both pictures of the turn (the
camera frame and Hooke's window as at key-down), the Project brief, the recent conversation and what was seen on
earlier turns. It runs its own tool loop through the same registry path the voice's tools use (partner_tools: one voice
request each, attributed to the Assistant, act then look, every acting call through Hands with its receipt), and ends
by calling `report`: what to say (one or two sentences), what to point at, whether all of it is done, and a question
when it cannot go on without the user. A tool that changed the screen comes back with a fresh picture of the window.
Whether it is done is decided from the receipts of its acting calls (voice/jobs.py `outcome`), never from its report
alone: its `checked` is advisory. It runs as a job (voice/jobs.py): the user's follow-ups reach it at its next step.

A refusal (Anthropic's safeguards can refuse life-science requests, and are billed) is said as a refusal, with the other
backend offered by name; another model never answers silently in its place.

The Claude client is plain httpx (no SDK: uv.lock unchanged), as research_worker/presentation.py and the vision option
call Anthropic; GPT-6.1 Sol goes through the OpenAI SDK's Responses API, as the brain's own calls do.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

MAX_ROUNDS = 8  # tool rounds one task may take before it reports what it has
THINK_TIMEOUT_S = 90.0  # a task still running after this reports what it has, said as unfinished
MAX_TOKENS = 4000  # Opus thinks within max_tokens: room for its thinking beside the answer
TOOL_OUTPUT_CHARS = 6000
CACHE_READ = {"claude-opus-5-5": 0.05}  # a cache read's share of the input price (0.1 for other models)

REPORT = {
    "name": "report",
    "description": "Finish: what the voice says to the user about this task, what to point at while saying it, and "
                   "whether every change you say you made was checked in a fresh picture or a tool's confirmation.",
    "parameters": {
        "type": "object",
        "properties": {
            "say": {"type": "string", "description": "The outcome first, in one or two short spoken sentences in plain "
                                                     "words: what was done or found, or what failed and the next step; "
                                                     "an explanation the user asked for, in full. No ids, no lists."},
            "points": {"type": "array", "items": {"type": "string"}, "maxItems": 4,
                       "description": "Hooke ids to point at while it is said (outline:<id>, population:<id>, "
                                      "window:<id>, tab:<title>, researcher:<number>); only ids a tool gave you."},
            "checked": {"type": "boolean", "description": "true if every change said to be done was confirmed (advisory: "
                                                          "Hooke decides done from its receipts)"},
            "done": {"type": "boolean", "description": "true when the request is fully done; false when it failed or "
                                                       "only part of it was done"},
            "question": {"type": "string", "description": "only when you cannot go on without the user: the one short "
                                                          "question the voice asks them, or a permission's ask (their "
                                                          "answer comes back to you)"},
        },
        "required": ["say", "checked", "done"],
    },
}


@dataclass
class ThinkTask:
    request: str
    why: str = ""
    words: str = ""  # the user's own words this turn (their transcription)
    pictures: list[tuple[bytes, str]] = field(default_factory=list)  # (jpeg, label): camera, window
    brief: str = ""
    history: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"t{uuid.uuid4().hex[:10]}")
    previous: str = ""  # a follow-up's job so far: what it said and found (update_job on a finished job)
    updates: list[str] = field(default_factory=list)  # the user's follow-ups while it works (update_job)
    taken: int = 0

    def new_updates(self) -> list[str]:
        """The follow-ups not yet given to the backend, given once."""
        fresh, self.taken = self.updates[self.taken:], len(self.updates)
        return fresh

    def unread(self) -> bool:
        """Follow-ups that came after the backend's last step: its run goes on with them."""
        return len(self.updates) > self.taken

    def context(self) -> str:
        """What a run starts from, once per run: the follow-ups given so far are taken here (`new_updates`)."""
        parts = [f"The voice handed you this: {self.request}"]
        if self.why:
            parts.append(f"Why it needs you: {self.why}")
        if self.previous:
            parts.append(f"This continues your earlier work on it: {self.previous}")
        added = self.new_updates()
        if added:
            parts.append("The user added: " + " / ".join(added))
        if self.words:
            parts.append(f"The user's own words: {self.words}")
        if self.history:
            parts.append("The conversation so far:\n" + "\n".join(f"{'User' if r == 'user' else 'Hooke'}: {t}"
                                                                  for r, t in self.history[-16:]))
        if self.notes:
            parts.append("What was seen on earlier turns:\n" + "\n".join(self.notes[-6:]))
        if self.brief:
            parts.append("The Project now:\n" + self.brief)
        return "\n\n".join(parts)


@dataclass
class ThinkResult:
    say: str
    ok: bool | None  # True done and verified; False failed or refused; None part done or not all verified (jobs.outcome)
    points: list[str] = field(default_factory=list)
    checked: bool = False  # the backend's own word that it checked (advisory)
    done: bool = False  # the backend's own word that all of the request is done
    refused: str | None = None
    error: str | None = None
    model: str = ""
    rounds: int = 0
    tools: list[dict] = field(default_factory=list)  # each call: {name, ok, confirmed}
    usage: dict = field(default_factory=dict)
    question: str = ""  # asked of the user; their answer goes to the same job (update_job)
    unverified: list[dict] = field(default_factory=list)  # its acting calls not verified: {tool, result, said}

    def facts(self) -> dict:
        return {"done": self.ok is True, "checked": self.checked, "say": self.say, "points": self.points,
                **({"question": self.question} if self.question else {}),
                **({"unverified": self.unverified} if self.unverified else {}),
                "steps": self.tools[-8:], **({"refused": self.refused} if self.refused else {}),
                **({"error": self.error} if self.error else {})}


CallTool = Callable[[str, dict], Awaitable[dict]]
Progress = Callable[[str], None]


def tool_text(result: dict) -> str:
    """A tool's result for the backend, as text: its receipt without the pictures (they go as images)."""
    shown = {k: v for k, v in result.items() if not k.startswith("_")}
    look = result.get("_look") or {}
    if look.get("text"):
        shown["screen_after"] = look["text"]
    return json.dumps(shown, ensure_ascii=False, default=str)[:TOOL_OUTPUT_CHARS]


def tool_pictures(result: dict) -> list[tuple[bytes, str]]:
    look = result.get("_look") or {}
    return [(jpeg, label) for jpeg, label in look.get("images") or []]


def jpeg_b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def summary_of(name: str, result: dict) -> dict:
    """One call as the result keeps it; an acting call's receipt (Hands: result and said) with it."""
    out = {"name": name, "ok": result.get("ok"), "confirmed": result.get("_log", {}).get("confirmed")
           if isinstance(result.get("_log"), dict) else None}
    if result.get("result"):
        out["result"] = str(result["result"])
        if result.get("said"):
            out["said"] = str(result["said"])[:200]
    return out


# --- Claude Opus 5.5 over the Messages API ---------------------------------------------------------------------------
class ClaudeThinker:
    """Opus 5.5 with Hooke's tools over httpx. Opus always thinks; `effort` is how much (low is its fastest). Its
    assistant turns (thinking blocks included, with their signatures) go back whole, as the tool loop requires."""

    def __init__(self, model: str = "claude-opus-5-5", effort: str | None = "low", client=None) -> None:
        self.model, self.effort, self._client = model, effort, client

    def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(base_url="https://api.anthropic.com", timeout=60.0, headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        return self._client

    async def run(self, task: ThinkTask, tools: list[dict], call_tool: CallTool, progress: Progress,
                  system: str) -> ThinkResult:
        declared = [{"name": t["name"], "description": t.get("description", ""), "input_schema": t["parameters"]}
                    for t in [*tools, REPORT]]
        content: list[dict] = []
        for jpeg, label in task.pictures:
            content.append({"type": "text", "text": label})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": jpeg_b64(jpeg)}})
        content.append({"type": "text", "text": task.context()})
        messages = [{"role": "user", "content": content}]
        result = ThinkResult("", None, model=self.model)
        usage = {"in": 0, "out": 0}
        for round_ in range(1, MAX_ROUNDS + 1):
            result.rounds = round_
            # Cached: tools and system (the same for every task) by the marker on the system, which renders after the
            # tools; the round's growing conversation by the automatic marker, so the next round reads it back.
            body: dict[str, Any] = {"model": self.model, "max_tokens": MAX_TOKENS,
                                    "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                                    "messages": messages, "tools": declared, "cache_control": {"type": "ephemeral"}}
            if self.effort:
                body["output_config"] = {"effort": self.effort}
            reply = await self._post(body)
            u = reply.get("usage") or {}
            usage["in"] += (int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
                            + int(u.get("cache_creation_input_tokens") or 0))
            usage["cached"] = usage.get("cached", 0) + int(u.get("cache_read_input_tokens") or 0)
            usage["out"] += int(u.get("output_tokens") or 0)
            result.usage = usage
            if reply.get("stop_reason") == "refusal":
                category = (reply.get("stop_details") or {}).get("category")
                result.refused = category or "safety"
                result.ok = False
                return result
            blocks = reply.get("content") or []
            messages.append({"role": "assistant", "content": blocks})
            uses = [b for b in blocks if b.get("type") == "tool_use"]
            if not uses:
                result.say = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
                return result
            answers, final = [], None
            for use in uses:
                if use.get("name") == "report":
                    final = use.get("input") or {}
                    answers.append({"type": "tool_result", "tool_use_id": use["id"], "content": "reported"})
                    continue
                progress(use.get("name") or "")
                # Its id is the step's: a retry of the same step gets the earlier receipt (partner_tools call_tool).
                out = await call_tool(use["name"], {**(use.get("input") or {}), "_call_id": use.get("id") or ""})
                result.tools.append(summary_of(use["name"], out))
                blocks_out: list[dict] = [{"type": "text", "text": tool_text(out)}]
                for jpeg, label in tool_pictures(out):
                    blocks_out.append({"type": "text", "text": label})
                    blocks_out.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                                   "data": jpeg_b64(jpeg)}})
                answers.append({"type": "tool_result", "tool_use_id": use["id"], "content": blocks_out,
                                **({"is_error": True} if out.get("ok") is False else {})})
            fresh = task.new_updates()
            if fresh:  # the user's follow-ups while it worked, in order (update_job)
                answers.append({"type": "text", "text": "The user added, while you worked: " + " / ".join(fresh)})
            messages.append({"role": "user", "content": answers})
            if final is not None:
                return finish(result, final)
        result.say = result.say or ""
        result.error = "it took more steps than allowed"
        return result

    async def _post(self, body: dict) -> dict:
        from research_worker import api_usage

        async with api_usage.attempt(self.model, provider="Anthropic", area="Live", out_tokens=body["max_tokens"]):
            response = await self._http().post("/v1/messages", json=body)
            response.raise_for_status()
            reply = response.json()
            u = reply.get("usage") or {}
            price = api_usage.PRICES.get(self.model)
            tokens = (u.get("input_tokens"), u.get("output_tokens"))
            # Cache writes at 1.25x the input price, reads at its CACHE_READ share (Opus 5.5: $0.20 of $4 per MTok).
            cached = (int(u.get("cache_creation_input_tokens") or 0) * 1.25
                      + int(u.get("cache_read_input_tokens") or 0) * CACHE_READ.get(self.model, 0.1))
            usd = (((tokens[0] + cached) * price["in"] + tokens[1] * price["out"]) / 1e6
                   if isinstance(price, dict) and all(isinstance(n, int) for n in tokens) else None)
            api_usage.record(self.model, usd, provider="Anthropic", area="Live",
                             basis="Reported tokens · list price before discounts" if usd is not None
                             else "Usage or price unavailable")
        return reply


# --- GPT-6.1 Sol over the Responses API -----------------------------------------------------------------------------
class SolThinker:
    """GPT-6.1 Sol with Hooke's tools, through the OpenAI SDK's Responses API (as the brain calls it)."""

    def __init__(self, model: str = "gpt-6.1-sol", effort: str | None = "low", client=None) -> None:
        self.model, self.effort, self._client = model, effort, client

    def _sdk(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0)
        return self._client

    async def run(self, task: ThinkTask, tools: list[dict], call_tool: CallTool, progress: Progress,
                  system: str) -> ThinkResult:
        from research_worker import api_usage

        declared = [{"type": "function", "name": t["name"], "description": t.get("description", ""),
                     "parameters": t["parameters"], "strict": False} for t in [*tools, REPORT]]
        content: list[dict] = []
        for jpeg, label in task.pictures:
            content.append({"type": "input_text", "text": label})
            content.append({"type": "input_image", "image_url": "data:image/jpeg;base64," + jpeg_b64(jpeg)})
        content.append({"type": "input_text", "text": task.context()})
        items: list[Any] = [{"role": "user", "content": content}]
        result = ThinkResult("", None, model=self.model)
        for round_ in range(1, MAX_ROUNDS + 1):
            result.rounds = round_
            kwargs: dict[str, Any] = {"model": self.model, "instructions": system, "input": items, "tools": declared}
            if self.effort:
                kwargs["reasoning"] = {"effort": self.effort}
            async with api_usage.attempt(self.model, provider="OpenAI", area="Live"):
                reply = await self._sdk().responses.create(**kwargs)
                api_usage.response(self.model, reply, provider="OpenAI", area="Live")
            output = list(getattr(reply, "output", None) or [])
            items.extend(output)
            calls = [o for o in output if getattr(o, "type", None) == "function_call"]
            if not calls:
                result.say = (getattr(reply, "output_text", "") or "").strip()
                if any(getattr(c, "type", None) == "refusal" for o in output for c in getattr(o, "content", None) or []):
                    result.refused, result.ok = "safety", False
                return result
            final, pictures = None, []
            for call in calls:
                try:
                    args = json.loads(call.arguments or "{}")
                except ValueError:
                    args = {}
                if call.name == "report":
                    final = args
                    items.append({"type": "function_call_output", "call_id": call.call_id, "output": "reported"})
                    continue
                progress(call.name)
                out = await call_tool(call.name, {**args, "_call_id": getattr(call, "call_id", "") or ""})
                result.tools.append(summary_of(call.name, out))
                items.append({"type": "function_call_output", "call_id": call.call_id, "output": tool_text(out)})
                pictures.extend(tool_pictures(out))
            if pictures:
                look: list[dict] = []
                for jpeg, label in pictures:
                    look.append({"type": "input_text", "text": label})
                    look.append({"type": "input_image", "image_url": "data:image/jpeg;base64," + jpeg_b64(jpeg)})
                items.append({"role": "user", "content": look})
            fresh = task.new_updates()
            if fresh:
                items.append({"role": "user", "content": [{"type": "input_text", "text": "The user added, while you "
                                                           "worked: " + " / ".join(fresh)}]})
            if final is not None:
                return finish(result, final)
        result.error = "it took more steps than allowed"
        return result


def finish(result: ThinkResult, report: dict) -> ThinkResult:
    """The backend's report as it gave it: `ok` is its own word for now; the job decides it from the receipts of its
    acting calls (jobs.outcome), and `checked` stays advisory."""
    result.say = str(report.get("say") or "").strip()
    result.points = [str(p) for p in (report.get("points") or [])][:4]
    result.checked = bool(report.get("checked"))
    result.question = str(report.get("question") or "").strip()
    result.done = bool(report.get("done"))
    result.ok = True if result.done else False
    return result


def thinker(option: str):
    """The backend of option THINK."""
    if option.startswith("gpt-"):
        return SolThinker(option)
    return ClaudeThinker(option)


async def think(task: ThinkTask, backend, tools: list[dict], call_tool: CallTool, progress: Progress,
                system: str, timeout: float = THINK_TIMEOUT_S) -> ThinkResult:
    """One task to its report, or what it has when the time is up or the backend fails (said plainly, never silent)."""
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(backend.run(task, tools, call_tool, progress, system), timeout)
    except asyncio.TimeoutError:
        result = ThinkResult("", None, error=f"it was still working after {timeout:.0f} s", model=getattr(backend, "model", ""))
    except Exception as exc:  # the voice says it did not work, plainly; the real error is in the log
        result = ThinkResult("", False, error=f"{type(exc).__name__}: {exc}"[:300], model=getattr(backend, "model", ""))
    result.usage = {**result.usage, "ms": round((time.perf_counter() - t0) * 1000)}
    return result
