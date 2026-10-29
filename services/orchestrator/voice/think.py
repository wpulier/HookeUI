"""`think`: the frontier mind behind the one-brain voice (option THINK; part-2.5 architecture, item 2).

The voice in front (gpt-realtime-2.1) hears, sees and answers at once; a hard question, many steps or a deep look goes
behind it, to a frontier model with Hooke's tools: Claude Opus 5.5 by default, GPT-6.1 Sol as an option. On τ-Voice a
fast voice with a frontier backend scored 74.5% where the speech model alone scored 45.7%.

What the backend gets: the voice's `request` and `why`, the user's own words this turn, both pictures of the turn (the
camera frame and Hooke's window as at key-down), the Project brief, the recent conversation and what was seen on
earlier turns. It runs its own tool loop through the same registry path the voice's tools use (partner_tools: one voice
request each, attributed to the Assistant, act then look), and ends by calling `report`: what to say (one or two
sentences), what to point at, and whether everything it says it did was checked on screen. A tool that changed the
screen comes back with a fresh picture of the window, so nothing is reported that it did not look at again.

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

REPORT = {
    "name": "report",
    "description": "Finish: what the voice says to the user about this task, what to point at while saying it, and "
                   "whether every change you say you made was checked in a fresh picture or a tool's confirmation.",
    "parameters": {
        "type": "object",
        "properties": {
            "say": {"type": "string", "description": "One or two short spoken sentences in plain words: what was done or "
                                                     "found, or what failed and the next step. No ids, no lists."},
            "points": {"type": "array", "items": {"type": "string"}, "maxItems": 4,
                       "description": "Hooke ids to point at while it is said (outline:<id>, population:<id>, "
                                      "window:<id>, tab:<title>, researcher:<number>); only ids a tool gave you."},
            "checked": {"type": "boolean", "description": "true only if every change said to be done was confirmed"},
            "done": {"type": "boolean", "description": "true when the request is fully done; false when it failed or "
                                                       "only part of it was done"},
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

    def context(self) -> str:
        parts = [f"The voice handed you this: {self.request}"]
        if self.why:
            parts.append(f"Why it needs you: {self.why}")
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
    ok: bool | None  # True done and checked; False failed or refused; None part done or unchecked
    points: list[str] = field(default_factory=list)
    checked: bool = False
    refused: str | None = None
    error: str | None = None
    model: str = ""
    rounds: int = 0
    tools: list[dict] = field(default_factory=list)  # each call: {name, ok, confirmed}
    usage: dict = field(default_factory=dict)

    def facts(self) -> dict:
        return {"done": self.ok is True, "checked": self.checked, "say": self.say, "points": self.points,
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
    return {"name": name, "ok": result.get("ok"), "confirmed": result.get("_log", {}).get("confirmed")
            if isinstance(result.get("_log"), dict) else None}


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
            body: dict[str, Any] = {"model": self.model, "max_tokens": MAX_TOKENS, "system": system,
                                    "messages": messages, "tools": declared}
            if self.effort:
                body["output_config"] = {"effort": self.effort}
            reply = await self._post(body)
            u = reply.get("usage") or {}
            usage["in"] += int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
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
                out = await call_tool(use["name"], use.get("input") or {})
                result.tools.append(summary_of(use["name"], out))
                blocks_out: list[dict] = [{"type": "text", "text": tool_text(out)}]
                for jpeg, label in tool_pictures(out):
                    blocks_out.append({"type": "text", "text": label})
                    blocks_out.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                                   "data": jpeg_b64(jpeg)}})
                answers.append({"type": "tool_result", "tool_use_id": use["id"], "content": blocks_out,
                                **({"is_error": True} if out.get("ok") is False else {})})
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
            usd = ((tokens[0] * price["in"] + tokens[1] * price["out"]) / 1e6
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
                out = await call_tool(call.name, args)
                result.tools.append(summary_of(call.name, out))
                items.append({"type": "function_call_output", "call_id": call.call_id, "output": tool_text(out)})
                pictures.extend(tool_pictures(out))
            if pictures:
                look: list[dict] = []
                for jpeg, label in pictures:
                    look.append({"type": "input_text", "text": label})
                    look.append({"type": "input_image", "image_url": "data:image/jpeg;base64," + jpeg_b64(jpeg)})
                items.append({"role": "user", "content": look})
            if final is not None:
                return finish(result, final)
        result.error = "it took more steps than allowed"
        return result


def finish(result: ThinkResult, report: dict) -> ThinkResult:
    """The backend's report, checked: "done" only when it also says every change was confirmed."""
    result.say = str(report.get("say") or "").strip()
    result.points = [str(p) for p in (report.get("points") or [])][:4]
    result.checked = bool(report.get("checked"))
    done = bool(report.get("done"))
    result.ok = True if done and result.checked else (False if not done else None)
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
