"""The brain's function-calling mode (options.BRAIN "fc"): one model call with the tools as
real functions, normalised to the same shape as json-actions replies
({answer, targets, actions}) so the turn loop handles both the same way.

Measured in spike S1 (docs/bench/brain.md): as accurate as JSON on Flash at the same latency
when the prompt asks for the answer first.
"""
from __future__ import annotations

from research_worker import api_usage

import asyncio
import base64
import json
import os
import time

from ..prompts import OUTLINE_SCHEMA
from .. import execution
from ..vision import _diagnostic_entry, _diagnostic_note, _diagnostic_result, _plan_diagnostics, image_mime
from .dispatch import PLANNER_OUTPUT_TOKENS

MAX_OUTPUT_TOKENS = PLANNER_OUTPUT_TOKENS
OUTLINE_DESCRIPTION = "Outline things in the current microscope view so the user sees what you mean."


class FunctionCallingBrain:
    """Calls the selected model ('gemini:...', 'openai:...' or 'anthropic:...' spec) with the registry's tools."""

    def __init__(self) -> None:
        self._clients: dict[tuple[str, str], object] = {}  # (provider, key) -> its client (`_client`)

    async def _client(self, which: tuple[str, str], make):
        """One provider client per key, built once and in a thread (L1b part 2: `setdefault` built a new client on the
        event loop every function-calling turn, its CA bundle load included, and kept the first even after the key was
        replaced)."""
        client = self._clients.get(which)
        if client is None:
            client = self._clients.setdefault(which, await asyncio.to_thread(make))
        return client

    @execution.traced("function-calling")
    async def ask(self, spec: str, jpeg: bytes, prompt: str, registry) -> tuple[dict, dict]:
        provider, _, rest = spec.partition(":")
        model, _, level = rest.partition("@")
        execution.note(selected=spec, adapter="brain.llm.FunctionCallingBrain", provider=provider,
                       model=model, identity="request-model; hosted revision unconfirmed")
        from ..agent import key_needed
        from ..options import VISION
        from .dispatch import dispatch_answer

        failed = key_needed(next((o.id for o in VISION.values() if o.spec == spec), None))
        if failed is not None:
            raise failed  # its key is missing: said in plain words, never the client's KeyError

        if provider == "gemini":
            return await dispatch_answer(lambda: self._gemini(model, level or "low", jpeg, prompt, registry),
                                         model=model, effort=level or "low")
        if provider == "openai":
            # gpt-6.1-sol rejects reasoning effort "none" (spike S1); "low" works for all.
            effort = "low" if level in ("", "none") else level
            return await dispatch_answer(lambda: self._openai(model, effort, jpeg, prompt, registry),
                                         model=model, effort=effort)
        if provider == "anthropic":
            # As the look reads the spec (vision.make_vision): effort low unless named, '+fast' for Opus fast mode.
            level, _, mode = level.partition("+")
            effort = None if level == "default" else (level or "low")
            return await dispatch_answer(lambda: self._anthropic(model, effort, mode == "fast", jpeg, prompt, registry),
                                         model=model, effort=effort or "")
        raise ValueError(f"unknown provider in {spec}")

    async def _gemini(self, model: str, level: str, jpeg: bytes, prompt: str, registry) -> tuple[dict, dict]:
        from google import genai
        from google.genai import types

        key = os.environ["GEMINI_API_KEY"]
        client = await self._client(("gemini", key), lambda: genai.Client(api_key=key, http_options={"retry_options": {"attempts": 1}}))
        declarations = [types.FunctionDeclaration(name="outline", description=OUTLINE_DESCRIPTION,
                                                  parameters_json_schema=OUTLINE_SCHEMA)] + registry.gemini_declarations()
        config = types.GenerateContentConfig(
            max_output_tokens=MAX_OUTPUT_TOKENS, temperature=0.2,
            thinking_config=types.ThinkingConfig(thinking_level=level.upper()) if level != "default" else None,
            tools=[types.Tool(function_declarations=declarations)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        t0 = time.perf_counter()
        _diagnostic_entry(prompt, MAX_OUTPUT_TOKENS)
        async with api_usage.attempt(model, provider="Google", area="Chat", out_tokens=MAX_OUTPUT_TOKENS,
                                     in_tokens=api_usage.tokens_in(prompt, 1, extra=repr(declarations))):  # thinking included
            response = await client.aio.models.generate_content(
                model=model, contents=[types.Part.from_bytes(data=jpeg, mime_type=image_mime(jpeg)), prompt], config=config)
        api_usage.response(model, response, provider="Google", area="Chat", tier=None, search=False)
        ms = (time.perf_counter() - t0) * 1000
        execution.note(reportedModel=getattr(response, "model_version", None))
        cand = (response.candidates or [None])[0]
        text = "".join(p.text for p in (cand.content.parts if cand and cand.content and cand.content.parts else [])
                       if p.text and not p.thought)
        calls = [(c.name, dict(c.args or {})) for c in response.function_calls or []]
        data = _normalise(text, calls)
        _diagnostic_result(response, "gemini", data)
        return data, {"model": model, "latency_ms": ms, "marks": None}

    async def _openai(self, model: str, effort: str, jpeg: bytes, prompt: str, registry) -> tuple[dict, dict]:
        from openai import AsyncOpenAI

        key = os.environ["OPENAI_API_KEY"]
        client = await self._client(("openai", key), lambda: AsyncOpenAI(api_key=key, max_retries=0))
        tools = [{"type": "function", "name": "outline", "description": OUTLINE_DESCRIPTION,
                  "parameters": OUTLINE_SCHEMA, "strict": False}] + registry.responses_tools()
        content = [{"type": "input_image", "image_url": f"data:{image_mime(jpeg)};base64," + base64.b64encode(jpeg).decode()},
                   {"type": "input_text", "text": prompt}]
        t0 = time.perf_counter()
        _diagnostic_entry(prompt, MAX_OUTPUT_TOKENS)
        async with api_usage.attempt(model, provider="OpenAI", area="Chat", out_tokens=MAX_OUTPUT_TOKENS,  # reasoning included
                                     in_tokens=api_usage.tokens_in(prompt, 1, extra=json.dumps(tools, default=str))):
            response = await client.responses.create(model=model, input=[{"role": "user", "content": content}],
                                                     max_output_tokens=MAX_OUTPUT_TOKENS, tools=tools,
                                                     reasoning={"effort": effort})
        api_usage.response(model, response, provider="OpenAI", area="Chat", tier=None, search=False)
        ms = (time.perf_counter() - t0) * 1000
        execution.note(reportedModel=getattr(response, "model", None))
        calls = []
        for item in response.output or []:
            if getattr(item, "type", None) == "function_call":
                try:
                    calls.append((item.name, json.loads(item.arguments or "{}")))
                except json.JSONDecodeError:
                    calls.append(None)  # missing original call, never a shifted later success
        data = _normalise(response.output_text or "", calls)
        _diagnostic_result(response, "openai", data)
        return data, {"model": model, "latency_ms": ms, "marks": None}

    async def _anthropic(self, model: str, effort: str | None, fast: bool, jpeg: bytes, prompt: str,
                         registry) -> tuple[dict, dict]:
        """Claude with the tools, over the look's own Messages API client (vision.AnthropicVision.http_client): the
        same server-side fallbacks and fast mode, Anthropic's own reason for an error, a refusal said as one."""
        from ..vision import AnthropicVision

        key = os.environ["ANTHROPIC_API_KEY"]
        client = await self._client(("anthropic-fast" if fast else "anthropic", key),
                                    lambda: AnthropicVision.http_client(key, fast))
        tools = [{"name": "outline", "description": OUTLINE_DESCRIPTION, "input_schema": OUTLINE_SCHEMA}] + registry.anthropic_tools()
        content = [{"type": "image", "source": {"type": "base64", "media_type": image_mime(jpeg),
                                                "data": base64.b64encode(jpeg).decode()}},
                   {"type": "text", "text": prompt}]
        # Thinking shares max_tokens with the answer, so the cap leaves it room beyond the answer's own (as the look's).
        body: dict = {"model": model, "max_tokens": MAX_OUTPUT_TOKENS + 2000, "fallbacks": "default", "tools": tools,
                      "messages": [{"role": "user", "content": content}]}
        if effort:
            body["output_config"] = {"effort": effort}
        if fast:
            body["speed"] = "fast"
        t0 = time.perf_counter()
        _diagnostic_entry(prompt, body["max_tokens"])
        async with api_usage.attempt(model, provider="Anthropic", area="Chat", out_tokens=body["max_tokens"],
                                     in_tokens=api_usage.tokens_in(prompt, 1, extra=json.dumps(tools, default=str))):
            response = await client.post("/v1/messages", json=body)
            if response.is_error:  # Anthropic's own reason ("credit balance is too low"), not a bare status
                try:
                    reason = response.json()["error"]["message"]
                except Exception:
                    reason = response.text[:200]
                raise RuntimeError(f"Anthropic {response.status_code}: {reason}")
            reply = response.json()
            usage = reply.get("usage") or {}
            tokens = (usage.get("input_tokens"), usage.get("output_tokens"))  # output includes thinking
            price = api_usage.PRICES.get(model)
            usd = ((tokens[0] * price["in"] + tokens[1] * price["out"]) / 1e6 * (2 if fast else 1)
                   if isinstance(price, dict) and all(isinstance(n, int) for n in tokens) else None)
            api_usage.record(model, usd, provider="Anthropic", area="Chat",
                             basis=("Reported tokens · list price before discounts" + (" · fast mode" if fast else ""))
                             if usd is not None else "Usage or price unavailable")
        ms = (time.perf_counter() - t0) * 1000
        execution.note(reportedModel=reply.get("model"))  # a fallback model, when one answered instead
        if reply.get("stop_reason") == "refusal":
            category = (reply.get("stop_details") or {}).get("category")
            _diagnostic_note(inputTokens=tokens[0], outputTokens=tokens[1], finishReason="refusal", parseOk=False)
            raise RuntimeError(f"refused by the model's safeguards ({category or 'no category'})")
        blocks = reply.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        # Tool calls in the order made; one whose input is not an object keeps its slot as the missing call.
        calls = [(b.get("name"), b.get("input")) for b in blocks if b.get("type") == "tool_use"]
        data = _normalise(text, calls)
        _diagnostic_note(inputTokens=tokens[0], outputTokens=tokens[1], finishReason=reply.get("stop_reason"),
                         parseOk=True, **_plan_diagnostics(data))
        return data, {"model": model, "latency_ms": ms, "marks": None}


def _normalise(text: str, calls: list[tuple[str, dict] | None]) -> dict:
    targets, actions = [], []
    for call in calls:
        if call is None or not isinstance(call[1], dict):
            actions.append(None)
            continue
        name, args = call
        if name == "outline":
            targets += [t for t in args.get("targets") or [] if isinstance(t, dict)]
        else:
            values = dict(args)
            action = {"tool": name, "args": values}
            if "after_steps" in values:
                action["after_steps"] = values.pop("after_steps")
            actions.append(action)
    data = {"answer": text.strip(), "targets": targets, "actions": actions}
    # Scoped FC text supplies the answer's basis; tool outputs remain the only
    # source of actions/targets, in the provider's original function-call order.
    try:
        declared = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        declared = None
    if (isinstance(declared, dict) and isinstance(declared.get("answer"), str)
            and ("observation_scope" in declared or "input_refs" in declared
                 or "observation_basis" in declared or "goal" in declared)):
        data["answer"] = declared["answer"].strip()
        for key in ("observation_scope", "input_refs", "observation_basis"):
            if key in declared:
                data[key] = declared[key]  # invalid/unknown declarations reach the existing scope checker
        if "goal" in declared:
            from .goals import parse

            data["goal"] = parse(declared["goal"], actions)  # text never supplies executable actions
    return data
