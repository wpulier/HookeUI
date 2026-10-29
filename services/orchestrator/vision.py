"""Cloud vision adapters with one interface: send images + a prompt, get JSON back.

Each call returns the parsed JSON, wall-clock latency and token usage so benchmarks and
the live system measure the same thing.
"""
from __future__ import annotations

from research_worker import api_usage

import asyncio
import base64
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from . import execution


def image_mime(data: bytes) -> str:
    """Transport already admitted PNG losslessly; existing camera/JPEG callers stay unchanged."""
    return "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"


def claude_image(data: bytes) -> dict:
    """A picture as Anthropic's Messages API takes it: its bytes in base64, with their own media type."""
    return {"type": "image", "source": {"type": "base64", "media_type": image_mime(data),
                                        "data": base64.b64encode(data).decode()}}


# The most image tokens one picture can take on Claude's high-resolution models (up to ~4,784 at 2,576 px on the long
# edge, Anthropic's model notes), so a monthly cap admits a call at its true most (api_usage.tokens_in's per_picture).
CLAUDE_PICTURE_TOKENS = 4_800


def claude_text(response) -> str:
    """The answer's text blocks, in order. Thinking blocks (empty unless asked for) and tool calls are not the answer."""
    return "".join(getattr(b, "text", "") or "" for b in getattr(response, "content", None) or []
                   if getattr(b, "type", None) == "text")


def claude_refusal(response) -> str | None:
    """Why Claude declined, in words, when it did (stop_reason "refusal"; its category when given), else None."""
    if getattr(response, "stop_reason", None) != "refusal":
        return None
    category = getattr(getattr(response, "stop_details", None), "category", None)
    return f"Refusal: {getattr(response, 'model', None) or 'Claude'} declined to answer" + (
        f" ({category})" if category else "")


@dataclass
class VisionResult:
    data: dict | None
    latency_ms: float
    model: str
    raw_text: str = ""
    error: str = ""
    usage: dict = field(default_factory=dict)


def _diagnostic_note(**facts):
    """Optional primitive inspection cannot replace an actual provider outcome."""
    try:
        execution.note(**facts)
    except Exception:
        pass


def _diagnostic_entry(prompt, output_cap):
    try:
        from .brain.dispatch import admitted_facts

        encoded = prompt.encode("utf-8")
        _diagnostic_note(**admitted_facts(), outputCap=output_cap,
                         promptDigest=hashlib.sha256(encoded).hexdigest(), promptBytes=len(encoded),
                         inputTokens=None, outputTokens=None, thinkingTokens=None,
                         finishReason=None, responseStatus=None, parseOk=None,
                         rawActionCount=None, actionCount=None, targetCount=None, planDigest=None)
    except Exception:
        pass  # diagnostics neither validate nor modify the actual prompt


def _plan_diagnostics(data, *, actions=None):
    """Hash a small plan without retaining its text or changing original slots.

    Oversized/unsupported values have no digest, never a digest of a partial plan.
    """
    try:
        if not isinstance(data, dict):
            return {"rawActionCount": None, "targetCount": None, "planDigest": None}
        raw = data.get("actions")
        targets = data.get("targets")
        plan = {"actions": raw if isinstance(raw, list) else [],
                "targets": targets if isinstance(targets, list) else []}
        facts = {"rawActionCount": len(raw) if isinstance(raw, list) else 0,
                 "targetCount": len(targets) if isinstance(targets, list) else 0,
                 "planDigest": None, "planOmitted": True}
        if actions is not None:
            from .brain.registry import action_number, actions_wire

            facts["actionCount"] = len(actions)
            if len(actions) > 32 or any(not 1 <= action_number(action, i) <= 32
                                        for i, action in enumerate(actions, 1)):
                return facts  # do not allocate unbounded missing ordinal slots for inspection
            plan["actions"] = actions_wire(actions)
        nodes, characters = 0, 0

        def small(value, depth=0):
            nonlocal nodes, characters
            nodes += 1
            if nodes > 512 or depth > 8:
                return False
            if type(value) is str:
                characters += len(value)
                return len(value) <= 4096 and characters <= 16384
            if value is None or type(value) in (bool, int, float):
                return True
            if type(value) is dict:
                return len(value) <= 32 and all(type(k) is str and len(k) <= 256 and small(v, depth + 1)
                                               for k, v in value.items())
            if type(value) is list:
                return len(value) <= 32 and all(small(v, depth + 1) for v in value)
            return False

        if small(plan):
            encoded = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                 allow_nan=False).encode("utf-8")
            if len(encoded) <= 65536:
                facts.update(planDigest=hashlib.sha256(encoded).hexdigest(), planOmitted=False)
        return facts
    except Exception:
        return {"planDigest": None, "planOmitted": True}


def _diagnostic_result(response, provider, data):
    try:
        def count(value):
            return value if type(value) is int and value >= 0 else None

        def label(value):
            value = getattr(value, "value", value)
            return value[:128] if isinstance(value, str) else None

        if provider == "gemini":
            usage = getattr(response, "usage_metadata", None)
            candidates = getattr(response, "candidates", None) or []
            reason = getattr(candidates[0], "finish_reason", None) if candidates else None
            tokens = (getattr(usage, "prompt_token_count", None), getattr(usage, "candidates_token_count", None),
                      getattr(usage, "thoughts_token_count", None))
            status = None
        elif provider == "anthropic":
            usage = getattr(response, "usage", None)  # output_tokens includes thinking; thinking_tokens is its part
            tokens = (getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None),
                      getattr(getattr(usage, "output_tokens_details", None), "thinking_tokens", None))
            reason, status = getattr(response, "stop_reason", None), None
        else:
            usage = getattr(response, "usage", None)
            tokens = (getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None),
                      getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", None))
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            status = getattr(response, "status", None)
        _diagnostic_note(inputTokens=count(tokens[0]), outputTokens=count(tokens[1]), thinkingTokens=count(tokens[2]),
                         finishReason=label(reason), responseStatus=label(status), parseOk=isinstance(data, dict),
                         **_plan_diagnostics(data))
    except Exception:
        pass  # SDK metadata is optional, never a second response validator


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


class _Client:
    """A provider's client made once. Made on the event loop it would import the SDK and load the certificate bundle
    there (`GeminiVision.__init__`: 1050 ms in the loaded sim of 2026-10-19, 1439 ms in L1a's run 1), so there it is made
    on a thread at the first call instead, and every later call reuses it; anywhere else it is made at once, as before.
    The key is read when the adapter is made either way, so a missing key fails where it always did."""

    def _make_client(self) -> None:
        from .looplag import off_loop, on_loop

        self._building = None
        if on_loop() and off_loop():
            return  # made by `_ready` at the first call
        self.client = self._new_client()

    async def _ready(self):
        client = self.__dict__.get("client")
        if client is not None:
            return client
        building = getattr(self, "_building", None)
        if building is None:
            building = self._building = asyncio.ensure_future(asyncio.to_thread(self._new_client))
        try:
            client = await asyncio.shield(building)
        except BaseException:
            if building.done():
                self._building = None  # a failed build is tried again at the next call
            raise
        self.client = client
        return client

    async def warm(self) -> None:
        """Made now, off the loop (App.start: the selected vision option's), so the first look does not wait for it."""
        try:
            await self._ready()
        except Exception:
            pass  # said where it is used, in its own words


class GeminiVision(_Client):
    def __init__(self, model: str, thinking_level: str | None = "minimal", media_resolution: str | None = None,
                 temperature: float | None = 0.2) -> None:
        self.model = model
        self.thinking_level = thinking_level
        self.media_resolution = media_resolution
        self.temperature = temperature  # None: the model's own default (option SAMPLING)
        self._key = os.environ["GEMINI_API_KEY"]
        self._make_client()

    def _new_client(self):
        from google import genai

        return genai.Client(api_key=self._key, http_options={"retry_options": {"attempts": 1}})

    @execution.traced("vision")
    async def ask(self, images: list[bytes], prompt: str, max_output_tokens: int = 1500) -> VisionResult:
        execution.note(selected=self.model, adapter="orchestrator.vision.GeminiVision", provider="gemini",
                       model=self.model, identity="request-model; hosted revision unconfirmed",
                       optionId=self.model + ("-hires" if self.media_resolution == "high" else ""))
        _diagnostic_entry(prompt, max_output_tokens)
        from google.genai import types

        parts = [types.Part.from_bytes(data=img, mime_type=image_mime(img)) for img in images] + [prompt]
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            max_output_tokens=max_output_tokens,
            **({"temperature": t} if (t := getattr(self, "temperature", 0.2)) is not None else {}),
        )
        if self.thinking_level:
            config.thinking_config = types.ThinkingConfig(thinking_level=self.thinking_level.upper())
        if self.media_resolution:
            config.media_resolution = getattr(types.MediaResolution, f"MEDIA_RESOLUTION_{self.media_resolution.upper()}")
        t0 = time.perf_counter()
        try:
            client = await self._ready()
            t0 = time.perf_counter()  # the model's time, never the client's first making
            async with api_usage.attempt(self.model, provider="Google", area="Live", out_tokens=max_output_tokens,
                                         in_tokens=api_usage.tokens_in(prompt, len(images))):  # thinking included
                response = await client.aio.models.generate_content(model=self.model, contents=parts, config=config)
            api_usage.response(self.model, response, provider="Google", area="Live", tier=None, search=False)
        except Exception as exc:  # surfaced in results, never fatal for a benchmark run
            execution.finish("failed", errorType=type(exc).__name__)
            return VisionResult(None, (time.perf_counter() - t0) * 1000, self.model, error=f"{type(exc).__name__}: {exc}"[:400])
        reported = getattr(response, "model_version", None)
        if reported:
            execution.note(reportedModel=reported, identity="provider-reported")
        latency = (time.perf_counter() - t0) * 1000
        text = response.text or ""
        usage = {}
        if response.usage_metadata:
            usage = {
                "input": response.usage_metadata.prompt_token_count,
                "output": response.usage_metadata.candidates_token_count,
                "thinking": getattr(response.usage_metadata, "thoughts_token_count", None),
            }
        data = _parse_json(text)
        _diagnostic_result(response, "gemini", data)
        if data is None:
            execution.finish("unreadable")
        return VisionResult(data, latency, self.model, raw_text=text, usage=usage)


class OpenAIVision(_Client):
    def __init__(self, model: str, reasoning_effort: str | None = "none", service_tier: str | None = None) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.service_tier = service_tier  # "priority": faster, steadier latency at a higher price
        self._key = os.environ["OPENAI_API_KEY"]
        self._make_client()

    def _new_client(self):
        from openai import AsyncOpenAI

        return AsyncOpenAI(api_key=self._key, max_retries=0)

    @execution.traced("vision")
    async def ask(self, images: list[bytes], prompt: str, max_output_tokens: int = 1500) -> VisionResult:
        execution.note(selected=self.model, adapter="orchestrator.vision.OpenAIVision", provider="openai",
                       model=self.model, identity="request-model; hosted revision unconfirmed",
                       optionId=self.model + ("-priority" if self.service_tier == "priority" else ""))
        _diagnostic_entry(prompt, max_output_tokens)
        content = [
            {"type": "input_image", "image_url": f"data:{image_mime(img)};base64," + base64.b64encode(img).decode()}
            for img in images
        ] + [{"type": "input_text", "text": prompt}]
        kwargs: dict = {
            "model": self.model,
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": max_output_tokens,
            "text": {"format": {"type": "json_object"}},
        }
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        if self.service_tier:
            kwargs["service_tier"] = self.service_tier
        t0 = time.perf_counter()
        try:
            client = await self._ready()
            t0 = time.perf_counter()  # the model's time, never the client's first making
            most = api_usage.bound(self.model, api_usage.tokens_in(prompt, len(images)), max_output_tokens)  # reasoning included
            async with api_usage.attempt(self.model, provider="OpenAI", area="Live",
                                         most=None if most is None else most * (2 if self.service_tier == "priority" else 1)):
                response = await client.responses.create(**kwargs)
            api_usage.response(self.model, response, provider="OpenAI", area="Live", tier=self.service_tier, search=False)
        except Exception as exc:
            execution.finish("failed", errorType=type(exc).__name__)
            return VisionResult(None, (time.perf_counter() - t0) * 1000, self.model, error=f"{type(exc).__name__}: {exc}"[:400])
        reported = getattr(response, "model", None)
        if reported:
            execution.note(reportedModel=reported, identity="provider-reported")
        latency = (time.perf_counter() - t0) * 1000
        text = response.output_text or ""
        usage = {}
        if response.usage:
            usage = {"input": response.usage.input_tokens, "output": response.usage.output_tokens}
        data = _parse_json(text)
        _diagnostic_result(response, "openai", data)
        if data is None:
            execution.finish("unreadable")
        return VisionResult(data, latency, self.model, raw_text=text, usage=usage)


# Claude has no JSON mode without a schema, and these asks each give their own JSON shape in the prompt: this one line is
# the same constraint the other two providers' JSON modes set (response_mime_type, json_object), never new context.
JSON_ONLY = "Reply with one JSON object only, with nothing before or after it and no code fence."


class AnthropicVision(_Client):
    """Claude through Anthropic's Messages API. Thinking stays adaptive (the current models cannot turn it off, or only
    on some); `effort` is how much it may think and say (low...max), None for the model's own default."""

    def __init__(self, model: str, effort: str | None = "low") -> None:
        self.model = model
        self.effort = effort
        self._key = os.environ["ANTHROPIC_API_KEY"]
        self._make_client()

    def _new_client(self):
        from anthropic import AsyncAnthropic

        return AsyncAnthropic(api_key=self._key, max_retries=0)

    @execution.traced("vision")
    async def ask(self, images: list[bytes], prompt: str, max_output_tokens: int = 1500) -> VisionResult:
        execution.note(selected=self.model, adapter="orchestrator.vision.AnthropicVision", provider="anthropic",
                       model=self.model, identity="request-model; hosted revision unconfirmed", optionId=self.model)
        _diagnostic_entry(prompt, max_output_tokens)
        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_output_tokens,  # thinking included, as OpenAI's and Gemini's limits include theirs
            "system": JSON_ONLY,
            "messages": [{"role": "user", "content": [claude_image(img) for img in images]
                          + [{"type": "text", "text": prompt}]}],
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        t0 = time.perf_counter()
        try:
            client = await self._ready()
            t0 = time.perf_counter()  # the model's time, never the client's first making
            async with api_usage.attempt(self.model, provider="Anthropic", area="Live", out_tokens=max_output_tokens,
                                         in_tokens=api_usage.tokens_in(prompt, len(images), per_picture=CLAUDE_PICTURE_TOKENS,
                                                                       extra=JSON_ONLY)):
                response = await client.messages.create(**kwargs)
            api_usage.response(self.model, response, provider="Anthropic", area="Live", tier=None, search=False)
        except Exception as exc:
            execution.finish("failed", errorType=type(exc).__name__)
            return VisionResult(None, (time.perf_counter() - t0) * 1000, self.model, error=f"{type(exc).__name__}: {exc}"[:400])
        reported = getattr(response, "model", None)
        if reported:
            execution.note(reportedModel=reported, identity="provider-reported")
        latency = (time.perf_counter() - t0) * 1000
        text = claude_text(response)
        usage = {}
        if getattr(response, "usage", None):  # output includes thinking (no separate "thinking": never counted twice)
            usage = {"input": response.usage.input_tokens, "output": response.usage.output_tokens}
        refused = claude_refusal(response)
        data = None if refused else _parse_json(text)
        _diagnostic_result(response, "anthropic", data)
        if refused:
            execution.finish("refused")
            return VisionResult(None, latency, self.model, raw_text=text, error=refused, usage=usage)
        if data is None:
            execution.finish("unreadable")
        return VisionResult(data, latency, self.model, raw_text=text, usage=usage)


def make_vision(spec: str, temperature: float | None = 0.2):
    """'gemini:gemini-3.8-flash', 'openai:gpt-6-luna' or 'anthropic:claude-sonnet-5-5', optionally
    '@level' at the end for thinking level / reasoning effort / effort, e.g. 'gemini:gemini-3.8-flash@low',
    'openai:gpt-6-luna@none' or 'anthropic:claude-sonnet-5-5@low' (use '@default' to leave it to the
    provider). Gemini also takes '+low|medium|high' after the level for the image resolution it reads at
    ('gemini:gemini-3.8-flash@low+high'); OpenAI takes the service tier there
    ('openai:gpt-5.6-luna@none+priority'). `temperature`: Gemini's, None for the model's own
    default (option SAMPLING); Claude takes none (its API refuses sampling settings)."""
    provider, _, rest = spec.partition(":")
    model, _, level = rest.partition("@")
    level, _, resolution = level.partition("+")
    if provider == "gemini":
        # No '@' means our fast default; '@default' leaves it to the provider.
        return GeminiVision(model, thinking_level=None if level == "default" else (level or "minimal"),
                            media_resolution=resolution or None, temperature=temperature)
    if provider == "openai":
        # GPT-6 models accept none/low/medium/high/xhigh/max (not "minimal").
        return OpenAIVision(model, reasoning_effort=None if level == "default" else (level or "none"),
                            service_tier=resolution or None)
    if provider == "anthropic":
        # Claude's efforts: low/medium/high/xhigh/max; no '@' is "low", the quickest.
        return AnthropicVision(model, effort=None if level == "default" else (level or "low"))
    raise ValueError(f"unknown vision provider: {spec}")
