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


class AnthropicVision(_Client):
    """Claude over the Messages API (the app calls Anthropic with httpx, as research_worker/presentation.py does).
    Opus 5.5 always thinks; `effort` is how much (low is its fastest). `fast`: Opus fast mode (beta, Claude API only,
    twice the price, its own rate limit). Server-side fallbacks are on, so a refusal is retried on another Claude
    model in the same call; one still refused is said as that, never read as an empty answer."""

    BETAS = "server-side-fallback-2026-07-01"
    FAST_BETA = "fast-mode-2026-02-01"

    def __init__(self, model: str, effort: str | None = "low", fast: bool = False) -> None:
        self.model = model
        self.effort = effort
        self.fast = fast
        self._key = os.environ["ANTHROPIC_API_KEY"]
        self._make_client()

    def _new_client(self):
        import httpx

        betas = self.BETAS + ("," + self.FAST_BETA if self.fast else "")
        return httpx.AsyncClient(base_url="https://api.anthropic.com", timeout=60.0, headers={
            "x-api-key": self._key, "anthropic-version": "2023-06-01", "anthropic-beta": betas})

    @execution.traced("vision")
    async def ask(self, images: list[bytes], prompt: str, max_output_tokens: int = 1500) -> VisionResult:
        execution.note(selected=self.model, adapter="orchestrator.vision.AnthropicVision", provider="anthropic",
                       model=self.model, identity="request-model; hosted revision unconfirmed",
                       optionId=self.model + ("-fast" if self.fast else ""))
        _diagnostic_entry(prompt, max_output_tokens)
        content = [{"type": "image", "source": {"type": "base64", "media_type": image_mime(img),
                                                "data": base64.b64encode(img).decode()}} for img in images]
        content.append({"type": "text", "text": prompt + "\n\nReply with the JSON object only."})
        # Thinking shares max_tokens with the answer, so the cap leaves room for it beyond the answer's own.
        body: dict = {"model": self.model, "max_tokens": max_output_tokens + 2000, "fallbacks": "default",
                      "messages": [{"role": "user", "content": content}]}
        if self.effort:
            body["output_config"] = {"effort": self.effort}
        if self.fast:
            body["speed"] = "fast"
        t0 = time.perf_counter()
        try:
            client = await self._ready()
            t0 = time.perf_counter()  # the model's time, never the client's first making
            async with api_usage.attempt(self.model, provider="Anthropic", area="Live",
                                         in_tokens=api_usage.tokens_in(prompt, len(images)),
                                         out_tokens=body["max_tokens"]):
                response = await client.post("/v1/messages", json=body)
                if response.is_error:  # say Anthropic's own reason ("credit balance is too low"), not a bare status
                    try:
                        reason = response.json()["error"]["message"]
                    except Exception:
                        reason = response.text[:200]
                    raise RuntimeError(f"Anthropic {response.status_code}: {reason}")
                reply = response.json()
                usage = reply.get("usage") or {}
                price = api_usage.PRICES.get(self.model)
                tokens = (usage.get("input_tokens"), usage.get("output_tokens"))
                usd = ((tokens[0] * price["in"] + tokens[1] * price["out"]) / 1e6 * (2 if self.fast else 1)
                       if isinstance(price, dict) and all(isinstance(n, int) for n in tokens) else None)
                api_usage.record(self.model, usd, provider="Anthropic", area="Live",
                                 basis=("Reported tokens · list price before discounts" + (" · fast mode" if self.fast else ""))
                                 if usd is not None else "Usage or price unavailable")
        except Exception as exc:  # surfaced in results, never fatal for a benchmark run
            execution.finish("failed", errorType=type(exc).__name__)
            return VisionResult(None, (time.perf_counter() - t0) * 1000, self.model, error=f"{type(exc).__name__}: {exc}"[:400])
        latency = (time.perf_counter() - t0) * 1000
        if reply.get("model"):
            execution.note(reportedModel=reply["model"], identity="provider-reported")
        if reply.get("stop_reason") == "refusal":
            category = (reply.get("stop_details") or {}).get("category")
            execution.finish("refused", category=category)
            return VisionResult(None, latency, self.model, error=f"refused by the model's safeguards ({category or 'no category'})")
        text = "".join(part.get("text", "") for part in reply.get("content", []) if part.get("type") == "text")
        data = _parse_json(text)
        _diagnostic_note(inputTokens=tokens[0], outputTokens=tokens[1], finishReason=reply.get("stop_reason"),
                         parseOk=isinstance(data, dict), **_plan_diagnostics(data))
        if data is None:
            execution.finish("unreadable")
        return VisionResult(data, latency, self.model, raw_text=text,
                            usage={"input": tokens[0], "output": tokens[1]})


def make_vision(spec: str, temperature: float | None = 0.2):
    """'gemini:gemini-3.8-flash' or 'openai:gpt-6-luna', optionally '@level' at the end
    for thinking level / reasoning effort, e.g. 'gemini:gemini-3.8-flash@low' or
    'openai:gpt-6-luna@none' (use '@default' to leave it to the provider). Gemini also takes
    '+low|medium|high' after the level for the image resolution it reads at
    ('gemini:gemini-3.8-flash@low+high'); OpenAI takes the service tier there
    ('openai:gpt-5.6-luna@none+priority'). `temperature`: Gemini's, None for the model's own
    default (option SAMPLING)."""
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
        # Effort low unless named; '+fast' after it for Opus fast mode ('anthropic:claude-opus-5-5@low+fast').
        return AnthropicVision(model, effort=None if level == "default" else (level or "low"), fast=resolution == "fast")
    raise ValueError(f"unknown vision provider: {spec}")
