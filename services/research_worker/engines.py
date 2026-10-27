"""The models a researcher thinks with (options.RESEARCH_ENGINE).

  gpt-6.1-sol       OpenAI Responses with web_search and our functions; sources come back
                    in web_search_call.action.sources (spike S5: searched 3/3, 9-12 s at
                    effort low). The response id chains one run's rounds, never two runs: a
                    continue gets an explicit package (ResearchManager._continuation), as on
                    every engine (a chain ending on finish's unanswered call is refused by the API).
  gemini-3.8-flash  google_search grounding plus functions (needs
                    include_server_side_tool_invocations; spike S5: 3-6 s but it often
                    answers from memory without searching; live, a search round took about
                    13 s: docs/bench/research.md). It thinks as hard as the effort reasons
                    (GEMINI_THINKING). Its pages come as redirect addresses, a new one for the
                    same page in every response: kept for display, counted once per site (searched).
                    An empty or blocked answer ends the run "failed" with Gemini's reason.
  fake              scripted, for tests and the scenario harness (no network); its rounds
                    go through the same rules as the others' (_final)

Each engine loops: ask "keep going?" when due (Researcher.keep_going), check the budget, call the model, run the
function calls it asked for, feed the results back, until `finish` is called. No clock or round count ends it: at three
quarters of its effort's time it is told to plan its write-up, and past its time or rounds told every round to write up
now (_final). Only a capped day's limit, the last budget.STALL_ROUNDS rounds finding nothing new (Researcher.stalled)
or no answer to "keep going?" make one last round with only `finish`. A deep run's first round may only call `plan`
(W6-4d). A call cancelled by the user's stop is billed without reporting usage, so its cost is estimated
(Researcher.cancelled_call). A call that failed for a reason that may
pass is sent again, a little (_model_call); the SDKs' own retries are off so none hide under ours. Each round is kept
in the run's transcript (Researcher.transcribe, transcript.py): what it was sent and offered, and what the model said.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import urllib.parse

from .budget import (CALL_TIMEOUT_S, MIN_OUTPUT_TOKENS, OVERHEAD_TOKENS, PER_PICTURE_TOKENS, STALL_ROUNDS, OverBudget,
                     PRICES, bounded_call, tokens_cost)
from .researcher import Researcher, RunFailed

# A forced write-up asks for the whole finish, not a recap: what it found goes in the report, its sourced
# points in claims, and what it could not find in limitations, or the work is lost when the run ends.
FINISH_ALL = ("title, summary, one_liner and significance, plus everything it found: the report's sections, "
              "tables, claims with the page each came from, session evidence, limitations and next_questions")
WRAP_UP = f"Time is nearly up: plan to call finish soon with {FINISH_ALL}; do not start anything big."
WRITE_UP_NOW = (f"Your time for this work is up. Call finish now with {FINISH_ALL}. Take one more step first only "
                "if the work cannot be written up without it.")
STALLED = (f"Your last {STALL_ROUNDS} rounds found no new source or finding. Call finish now with {FINISH_ALL}; "
           "say plainly in limitations what you could not find.")


# A model call that failed for a reason that may pass (HTTP 408, 429 or 5xx, a timeout, a lost connection, no answer
# in budget.CALL_TIMEOUT_S) is sent again at most RETRIES times, after RETRY_WAIT_S. No run clock limits it: the
# clock never cuts work off (budget.py).
RETRIES = 2
RETRY_WAIT_S = (1.0, 3.0)
# Failures that may pass, by class name (the SDKs' and their HTTP libraries'), so neither SDK is imported to ask.
PASSING_ERRORS = frozenset({"APIConnectionError", "TimeoutException", "NetworkError", "RemoteProtocolError",
                            "ClientConnectionError"})
# Gemini's grounding gives each page as a redirect through this host, a new address in every response.
GROUNDING_REDIRECT = "vertexaisearch.cloud.google.com"
# The effort's reasoning (gpt-6.1-sol's reasoning effort, budget.EFFORTS) as Gemini's thinking level.
GEMINI_THINKING = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH"}


# --- a limited run's calls, each bounded to what is left (a daily cap, or the monthly cap: Budget.unlimited False) -------
# Every attempt (its rounds, its write-up and any retry) is sent only with a most that fits what is left of the run's
# budget, the amount the app held for it: its input bounded by the UTF-8 bytes it sends (no token is shorter than a
# byte), the conversation it continues (the last reported input and output) and a ceiling per picture; its output by
# `max_output_tokens`, which both providers enforce and which counts thinking with the visible answer (OpenAI's reasoning
# tokens, Gemini's thoughts: Google's Generate Content docs, "Token limits"), sized to what is left, never above
# MAX_OUTPUT_TOKENS, its thinking level unchanged. Web search can't be bounded in the request (Gemini's grounding bills per
# query with no query limit; OpenAI's search content is billed as input with no limit the request sets, and
# max_tool_calls bounds only how many: Codex's check of OpenAI's docs, 2026-10-13), so a limited run isn't offered it,
# said once. When not even MIN_OUTPUT_TOKENS fit, no call is made: a round
# becomes the write-up, and a write-up that can't fit ends the run keeping what it found (Researcher.run: partial). A
# failed attempt that may have been billed is counted at its most, so a retry is admitted only if it fits after it.
MAX_OUTPUT_TOKENS = 32_000
NO_SEARCH = ("Web search isn't offered within a budget limit: its searches are billed in ways the app can't limit "
             "in advance")


def _bytes(value) -> tuple[int, int]:
    """UTF-8 bytes of the text in a request (its instructions, words, tool declarations, function calls and results)
    and its pictures, for a limited run's bound."""
    total, pictures = 0, 0

    def walk(v) -> None:
        nonlocal total, pictures
        if isinstance(v, dict):
            if v.get("type") == "input_image" or "inline_data" in v:
                pictures += 1
                return
            for k, x in v.items():
                total += len(str(k).encode())
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            if v.startswith("data:"):
                pictures += 1
            else:
                total += len(v.encode())
        elif isinstance(v, bytes):
            pictures += 1
        elif v is not None:
            total += len(str(v).encode())

    walk(value)
    return total, pictures


def _gemini_bytes(r: Researcher, contents, decls: list) -> tuple[int, int]:
    total = len(r.system_prompt().encode()) + _bytes(decls)[0]
    pictures = 0
    for content in contents:
        for part in content.parts or []:
            if getattr(part, "inline_data", None):
                pictures += 1
            total += len((getattr(part, "text", None) or "").encode())
            call, reply = getattr(part, "function_call", None), getattr(part, "function_response", None)
            if call is not None:
                total += len(json.dumps(call.args or {}, default=str).encode()) + len((call.name or "").encode())
            if reply is not None:
                total += len(json.dumps(reply.response or {}, default=str).encode())
    return total, pictures


def _committed(r: Researcher) -> float:
    """What the run has spent: reported usage, and calls sent without usage (cut off, failed and maybe billed), each
    counted at its most under a limit, so a retry is admitted only beside everything the failed attempt may have cost."""
    return r.committed()


class Limit:
    """A limited run's one attempt: its output limit and its most (None, 0: an unlimited run, nothing bounded)."""

    def __init__(self, out_tokens: int | None, most: float) -> None:
        self.out_tokens, self.most = out_tokens, most


def _limit(r: Researcher, model: str, in_bytes: int, pictures: int, *, carried: int = 0,
           search: str | None = None) -> Limit:
    """This attempt's output limit and most, sized to what is left (see above); OverBudget when not even
    MIN_OUTPUT_TOKENS fit."""
    if r.budget.unlimited:
        return Limit(None, 0.0)
    if search:  # never offered under a limit (above): a request that would carry it isn't bounded
        raise OverBudget(NO_SEARCH)
    out, most = bounded_call(r.budget, _committed(r), model,
                             carried + in_bytes + pictures * PER_PICTURE_TOKENS + OVERHEAD_TOKENS, MAX_OUTPUT_TOKENS)
    return Limit(out, most)


def _passing(exc: BaseException) -> str | None:
    """Why a failed model call may work if sent again (HTTP 408, 429 or 5xx, a timeout, a lost connection), or None
    when it would fail again (any other HTTP 4xx, a bad request, a bug)."""
    status = getattr(exc, "status_code", None)  # openai's APIStatusError
    if not isinstance(status, int):
        status = getattr(exc, "code", None)  # google-genai's APIError
    if isinstance(status, int):
        return f"HTTP {status}" if status in (408, 429) or 500 <= status < 600 else None
    if isinstance(exc, (TimeoutError, ConnectionError)) or any(k.__name__ in PASSING_ERRORS for k in type(exc).__mro__):
        return type(exc).__name__
    return None


# A provider's refusal for want of credit (the codes voice/diagnostics.py knows): never retried, since it fails the same
# way until someone adds credits, and never billed (refused before any work).
CREDIT_CODES = frozenset({"insufficient_quota", "insufficient_credits", "billing_not_active", "credit_balance_exhausted"})


def _credit_refusal(exc: BaseException) -> str | None:
    """Who refused the call for want of credit ("OpenAI"), or None: matched by the error's own code or type, never by
    its words."""
    body = getattr(exc, "body", None)
    codes = {getattr(exc, "code", None), getattr(exc, "type", None)}
    if isinstance(body, dict):
        inner = body.get("error") if isinstance(body.get("error"), dict) else body
        codes |= {inner.get("code"), inner.get("type")}
    if not any(isinstance(code, str) and code in CREDIT_CODES for code in codes):
        return None
    module = type(exc).__module__
    return "OpenAI" if module.startswith("openai") else "Google" if module.startswith("google") else "The model's provider"


async def _model_call(r: Researcher, model: str, call, sent, limit: Limit | None = None):
    """The round's one model call: `call()` sends it, `sent()` says what it carries (characters, pictures, the search
    tool offered) for an estimate. Cut off (the user's stop), it is estimated. Failed for a reason that may pass,
    it is sent again as it was, at most RETRIES times; a failure that may have been
    billed (all but HTTP 429, refused before any work) is estimated too, said to be uncertain. Only this call is
    sent again: the round's tools run once it has answered, so none runs twice. `limit`: a limited run's bound for each
    attempt (_limit): each is admitted only if its most fits what is left, and one that may have been billed is counted at
    its most, so a retry that no longer fits is never sent (OverBudget)."""
    most = limit.most if limit is not None and limit.out_tokens is not None else None
    for attempt in range(RETRIES + 1):
        if most is not None and _committed(r) + most > r.budget.effort.cap_usd + 1e-9:
            # every attempt, the first and each retry, fits what is left
            raise OverBudget(f"spent ${_committed(r):.3f} of ${r.budget.effort.cap_usd:.2f}, and another attempt could "
                             f"cost up to ${most:.3f}")
        try:
            async with asyncio.timeout(CALL_TIMEOUT_S):  # a call that never answers is cut and sent again
                return await call()
        except asyncio.CancelledError:
            r.cancelled_call(model, *sent(), usd=most)  # under a limit, at its most: its usage is unknown
            raise
        except Exception as exc:
            refused = _credit_refusal(exc)
            if refused:  # at once: no retry, nothing counted (researcher.py makes these words the failed step)
                raise RunFailed(f"{refused} says this account has no credits left. Add credits, or choose another "
                                "model for Researching.") from exc
            why = _passing(exc)
            if why is None:
                raise
            billed = why != "HTTP 429"
            if billed:  # under a limit, counted at its most through the run's settlement (Codex's recheck of 73626a0d)
                r.cancelled_call(model, *sent(), usd=most, uncertain=True)
            again = attempt < RETRIES  # no run clock cuts work (budget: "the clock never kills work")
            r.step(f"The model call failed ({why})"
                   + ((f"; it may have been billed, so ${most:.3f}, its most, is counted (usage uncertain)"
                        if most is not None else "; it may have been billed, so an estimate is counted (usage uncertain)")
                       if billed else "")
                   + ("; trying again" if again else ""), "warning")
            if not again:
                if type(exc) is TimeoutError:  # our own CALL_TIMEOUT_S, every time: said as what it is
                    raise RunFailed(f"The model did not answer in {CALL_TIMEOUT_S:.0f} s, {RETRIES + 1} times") from exc
                raise
            await asyncio.sleep(RETRY_WAIT_S[attempt])


def searched(r: Researcher, grounding) -> None:
    """What a Gemini round's Google Search grounding did: each query priced and said; each page kept as given, for
    display (Researcher.search_sources), and when its address is a redirect (GROUNDING_REDIRECT) named by its title
    (the Gemini API gives the site's domain there), so the same site found again is nothing new to the stall rule
    (Researcher.source_names). Another page on that site counts once too. The redirects are never fetched."""
    if grounding is None or not grounding.web_search_queries:
        return
    for query in grounding.web_search_queries:
        r.spend("google_search", PRICES["google_search"])
        r.step(f"Searched the web: {query}", "search")
    for chunk in grounding.grounding_chunks or []:
        web = chunk.web
        if web is None or not web.uri:
            continue
        r.search_sources.append(web.uri)
        if urllib.parse.urlsplit(web.uri).hostname == GROUNDING_REDIRECT:
            r.source_names[web.uri] = web.title or web.domain or web.uri


def _gemini_sent(r: Researcher, contents) -> tuple[int, int]:
    """Characters and pictures in a Gemini request: its instructions, text, the function calls it sends back and their
    results (as JSON), for estimating a call that never reported its usage."""
    chars, pictures = len(r.system_prompt()), 0
    for content in contents:
        for part in content.parts or []:
            pictures += bool(getattr(part, "inline_data", None))
            chars += len(getattr(part, "text", None) or "")
            call, reply = getattr(part, "function_call", None), getattr(part, "function_response", None)
            if call is not None:
                chars += len(json.dumps(call.args or {}, default=str))
            if reply is not None:
                chars += len(json.dumps(reply.response or {}, default=str))
    return chars, pictures


def _gemini_reply(response) -> dict:
    """A Gemini round's reply for its transcript: the model's content (its text, function calls and thoughts' marks), why
    it ended, the searches and pages its grounding gave, and its usage."""
    answer = response.candidates[0] if response.candidates else None
    grounding = getattr(answer, "grounding_metadata", None)
    return {"content": getattr(answer, "content", None),
            "finishReason": _no_answer(response, answer) if answer is None or answer.finish_reason is not None else None,
            "promptFeedback": getattr(response, "prompt_feedback", None),
            "searches": list(getattr(grounding, "web_search_queries", None) or []),
            "pages": [{"uri": c.web.uri, "title": c.web.title} for c in (getattr(grounding, "grounding_chunks", None) or [])
                      if getattr(c, "web", None) is not None],
            "usage": response.usage_metadata}


def _no_answer(response, candidate) -> str:
    """Why Gemini's reply holds nothing to act on: its finish reason, the request's block reason, or that none came
    (each in Gemini's own word, SAFETY, MAX_TOKENS...)."""
    if candidate is not None:
        return str(getattr(candidate.finish_reason, "value", candidate.finish_reason) or "an empty reply")
    blocked = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
    return f"the request was blocked ({getattr(blocked, 'value', blocked)})" if blocked else "no candidates"


def _final(r: Researcher, round_n: int, engine) -> tuple[bool, str | None]:
    """(made to write up now?, what to tell the model first). No clock cuts work off: an effort's time and rounds are
    where it is told to write up (WRAP_UP as it nears, WRITE_UP_NOW once past, every round after); it is made to only
    by the stall rule (STALL_ROUNDS with nothing new; option RESEARCH_PACING "told" only tells it), a daily cap the user
    set (OverBudget), or no answer to "keep going?" (Researcher.keep_going). A write-up in progress always finishes."""
    final = False
    try:
        r.budget.check(0.03 if isinstance(engine, SolEngine) else 0.01)  # only a capped day has a per-run limit
    except OverBudget:
        final, engine.over_budget = True, True
    stalled = r.stalled(round_n)  # every round, so it knows when it last found something
    r.final_round = True  # finish's checks keep what they can on a last chance (Researcher.tool_finish)
    if final:
        return True, None
    if r.forced_write_up:
        return True, r.forced_write_up
    told = r.spec.get("pacing") == "told"
    if stalled and not told:
        r.step(f"Nothing new in {STALL_ROUNDS} rounds: writing up what it has", "stall")
        return True, STALLED
    r.final_round = False
    if r.write_up_due(round_n):
        if not r.told_write_up:
            r.told_write_up = True
            r.step("Its time for this work is up: told to write up now", "warning")
        return False, WRITE_UP_NOW
    if round_n > 1 and r.wrap_up_due() and not r.told_nearly_up:
        r.told_nearly_up = True
        r.step("Its time is nearly up: told to plan its write-up", "step")
        return False, WRAP_UP
    if told:
        # Option RESEARCH_PACING "told": the facts each round; the model decides when to write up.
        left = r.effort.seconds - (time.monotonic() - r.started_at)
        since = round_n - 1 - getattr(r, "_progress_round", 0)
        return False, (f"(The app: about {max(0, left):.0f} s and {max(0, r.effort.max_rounds - round_n + 1)} rounds are "
                       f"left of this run's usual time; your last new source, finding or output was {since} "
                       f"round{'s' if since != 1 else ''} ago. Call finish with everything you found when you are done.)")
    return False, None


def _sent(items) -> tuple[int, int]:
    """Characters of text and number of pictures in a request (for estimating a cancelled call)."""
    chars, images = 0, 0

    def walk(value) -> None:
        nonlocal chars, images
        if isinstance(value, dict):
            if value.get("type") == "input_image" or "inline_data" in value:
                images += 1
                return
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)
        elif isinstance(value, str) and not value.startswith("data:"):
            chars += len(value)
        elif isinstance(value, bytes):
            images += 1

    walk(items)
    return chars, images


def make_engine(name: str | None):
    if name == "fake":  # a scripted researcher stays scripted, whatever the environment says
        return FakeEngine()
    name = os.environ.get("MICROMIND_RESEARCH_ENGINE") or name or "gpt-6.1-sol"
    if name == "fake":
        return FakeEngine()
    if name.startswith("gemini"):
        return GeminiEngine(name)
    return SolEngine(name)


# Pictures of the specimen are looked at closely (the prompt asks it to inspect the pixels); others as the API sees fit.
SPECIMEN_PICTURES = frozenset({"snapshot", "session", "annotate_image"})


def _detail(tool: str) -> str:
    return "high" if tool in SPECIMEN_PICTURES else "auto"


def _data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


class SolEngine:
    def __init__(self, model: str = "gpt-6.1-sol") -> None:
        self.model = model
        self.over_budget = False
        self.carried = 0  # tokens of the response chain the next round continues (previous_response_id)
        self.said_no_search = False

    def _tools(self, r: Researcher, final: bool, only: str | None = None) -> list[dict]:
        names = ["finish"] if final else [only] if only else r.tool_names()
        tools = [{"type": "function", "name": n, "description": r.schema(n)["description"], "parameters": r.schema(n)["parameters"]}
                 for n in names]
        if not final and not only and r.web_search_allowed() and r.budget.unlimited:
            tools.insert(0, {"type": "web_search"})
        return tools

    async def run(self, r: Researcher) -> None:
        import openai

        client = openai.AsyncOpenAI(max_retries=0)  # no hidden retries under ours (_model_call)
        content = [{"type": "input_text", "text": r.brief()}] + [
            {"type": "input_image", "image_url": _data_url(j), "detail": "high"} for j in r.images()]
        # Every run starts from its instructions and what it is given (a continue's package included): no response
        # chain is carried across runs, so every engine continues the same way (the researcher repair, Phase 3).
        r.response_id = None
        items: list = [{"role": "developer", "content": r.system_prompt()}, {"role": "user", "content": content}]
        round_n = 0
        while True:  # no round count or clock ends it: _final says when it writes up
            round_n += 1
            await r.keep_going()  # after an hour or 3x its usual cost: the user's "keep going?" (budget.CHECK_IN_S)
            notes = r.take_notes()  # the user steering it while it works (ResearchManager.note)
            if notes:
                items.append({"role": "user", "content": [{"type": "input_text", "text": r.note_message(notes)}]})
            final, told = _final(r, round_n, self)
            if told:
                items.append({"role": "user", "content": [{"type": "input_text", "text": told}]})
            if not r.budget.unlimited and r.web_search_allowed() and not self.said_no_search:
                self.said_no_search = True
                r.step(NO_SEARCH, "warning")
            while True:  # a limited run: a round that can't fit becomes the write-up; one that can't fit either ends it
                planning = not final and r.plan_due()
                search = not final and not planning and r.web_search_allowed() and r.budget.unlimited
                request = dict(
                    model=self.model, input=items, tools=self._tools(r, final, "plan" if planning else None),
                    tool_choice={"type": "function", "name": "finish"} if final else {"type": "function", "name": "plan"}
                    if planning else "auto",
                    include=["web_search_call.action.sources"], reasoning={"effort": r.effort.reasoning},
                    previous_response_id=r.response_id)
                try:
                    limit = _limit(r, self.model, *_bytes([items, request["tools"]]), carried=self.carried,
                                   search="web_search" if search else None)
                except OverBudget:
                    if final:
                        raise  # nothing fits: the run keeps what it found (Researcher.run)
                    final, self.over_budget = True, True
                    r.final_round = True
                    r.step("Not enough of its budget is left for another round: writing up what it has", "warning")
                    continue
                break
            if limit.out_tokens is not None:
                request["max_output_tokens"] = limit.out_tokens
            asked = {"round": round_n, "engine": "openai-responses", "model": self.model, "final": final,
                     "tools": [t.get("name") or t.get("type") for t in request["tools"]], "toolChoice": request["tool_choice"],
                     "previousResponseId": r.response_id, "sent": [i for i in items if i.get("role") != "developer"]}
            instructions = next((i["content"] for i in items if i.get("role") == "developer"), None)
            try:
                response = await _model_call(r, self.model, lambda: client.responses.create(**request),
                                             # the instructions are in items unless it continues a chain
                                             lambda: (*_sent(items), "web_search" if search else None), limit)
            except Exception as exc:
                r.transcribe(asked, {"error": f"{type(exc).__name__}: {exc}"[:600]}, instructions)
                raise
            r.transcribe(asked, lambda: {"id": response.id, "status": getattr(response, "status", None),
                                         "output": list(response.output or []), "usage": response.usage}, instructions)
            r.response_id = response.id
            usage = response.usage
            # The chain it continues next round: what it was given and what it gave (a bound of the next input).
            self.carried = (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "output_tokens", 0) or 0)
            r.spend(self.model, tokens_cost(self.model, getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0)))
            calls = []
            for item in response.output:
                kind = getattr(item, "type", "")
                if kind == "web_search_call":
                    action = getattr(item, "action", None)
                    query = getattr(action, "query", None) or ""
                    r.spend("web_search", PRICES["web_search"])
                    r.step(f"Searched the web{': ' + query if query else ''}", "search")
                    for source in getattr(action, "sources", None) or []:
                        url = getattr(source, "url", None)
                        if url:
                            r.search_sources.append(url)
                elif kind == "function_call":
                    calls.append(item)
            if not calls:
                await r.finish_from_text(response.output_text or "")
                return
            items = []
            for call in calls:
                try:
                    args = json.loads(call.arguments or "{}")
                except ValueError as exc:
                    # Never an empty call in its place: the model is told, and what it sent is kept.
                    result, picture = r.malformed_call(call.name, call.arguments, exc), None
                else:
                    result, picture = await r.call_tool(call.name, args)
                items.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps(result)})
                if picture:
                    items.append({"role": "user", "content": [
                        {"type": "input_text", "text": f"(picture from {call.name})"},
                        {"type": "input_image", "image_url": _data_url(picture), "detail": _detail(call.name)}]})
            if r.result is not None or final:
                return  # written up, or made to and could not: the run keeps what it found (Researcher.run)


class GeminiEngine:
    def __init__(self, model: str = "gemini-3.8-flash") -> None:
        self.model = model
        self.over_budget = False
        self.said_no_search = False

    async def run(self, r: Researcher) -> None:
        from google import genai
        from google.genai import types

        client = genai.Client(http_options={"retry_options": {"attempts": 1}})  # no hidden retries under ours (_model_call)

        def config(final: bool, planning: bool = False, out_tokens: int | None = None):
            names = ["finish"] if final else ["plan"] if planning else r.tool_names()
            decls = [types.FunctionDeclaration(name=n, description=r.schema(n)["description"],
                                               parameters_json_schema=r.schema(n)["parameters"]) for n in names]
            tools = [types.Tool(function_declarations=decls)]
            search = not final and not planning and r.web_search_allowed() and r.budget.unlimited
            if search:
                tools.insert(0, types.Tool(google_search=types.GoogleSearch()))
            return types.GenerateContentConfig(
                system_instruction=r.system_prompt(), tools=tools,
                thinking_config=types.ThinkingConfig(thinking_level=GEMINI_THINKING.get(r.effort.reasoning, "LOW")),
                max_output_tokens=out_tokens,  # a limited run's: its thoughts and its answer together (Google's docs)
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                tool_config=types.ToolConfig(
                    include_server_side_tool_invocations=True if search else None,
                    function_calling_config=types.FunctionCallingConfig(mode="ANY", allowed_function_names=names)
                    if final or planning else None))

        contents = [types.Content(role="user", parts=[types.Part.from_text(text=r.brief())] + [
            types.Part.from_bytes(data=j, mime_type="image/jpeg") for j in r.images()])]
        round_n, kept = 0, 0  # kept: contents already in its transcript
        while True:  # no round count or clock ends it: _final says when it writes up
            round_n += 1
            await r.keep_going()  # after an hour or 3x its usual cost: the user's "keep going?" (budget.CHECK_IN_S)
            notes = r.take_notes()  # the user steering it while it works (ResearchManager.note)
            if notes:  # the last content is the user's turn (the brief, or the function results)
                contents[-1].parts.append(types.Part.from_text(text=r.note_message(notes)))
            final, told = _final(r, round_n, self)
            if told:
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text=told)]))
            if not r.budget.unlimited and r.web_search_allowed() and not self.said_no_search:
                self.said_no_search = True
                r.step(NO_SEARCH, "warning")
            while True:  # a limited run: a round that can't fit becomes the write-up; one that can't fit either ends it
                planning = not final and r.plan_due()
                search = not final and not planning and r.web_search_allowed() and r.budget.unlimited
                names = ["finish"] if final else ["plan"] if planning else r.tool_names()
                try:
                    limit = _limit(r, self.model, *_gemini_bytes(r, contents, [r.schema(n) for n in names]))
                except OverBudget:
                    if final:
                        raise  # nothing fits: the run keeps what it found (Researcher.run)
                    final, self.over_budget = True, True
                    r.final_round = True
                    r.step("Not enough of its budget is left for another round: writing up what it has", "warning")
                    continue
                break
            settings = config(final, planning, limit.out_tokens)
            offered = ["finish"] if final else ["plan"] if planning else r.tool_names()
            asked = {"round": round_n, "engine": "gemini", "model": self.model, "final": final,
                     "tools": (["google_search"] if search else []) + offered,
                     "toolChoice": f"any of {', '.join(offered)}" if final or planning else "auto",
                     # what this round adds: the brief at first, then the tool results and notes (its own replies are
                     # each round's reply)
                     "sent": [c for c in contents[kept:] if getattr(c, "role", None) != "model"]}
            kept = len(contents)
            try:
                response = await _model_call(
                    r, self.model, lambda: client.aio.models.generate_content(model=self.model, contents=contents, config=settings),
                    lambda: (*_gemini_sent(r, contents), "google_search" if search else None), limit)
            except Exception as exc:
                r.transcribe(asked, {"error": f"{type(exc).__name__}: {exc}"[:600]}, settings.system_instruction)
                raise
            r.transcribe(asked, lambda: _gemini_reply(response), settings.system_instruction)
            usage = response.usage_metadata
            r.spend(self.model, tokens_cost(self.model, getattr(usage, "prompt_token_count", 0) or 0,
                                            (getattr(usage, "candidates_token_count", 0) or 0) + (getattr(usage, "thoughts_token_count", 0) or 0)))
            candidate = response.candidates[0] if response.candidates else None
            searched(r, candidate.grounding_metadata if candidate is not None else None)
            content = candidate.content if candidate is not None else None
            parts = (content.parts if content is not None else None) or []
            if not parts:  # blocked, cut off or empty: nothing to act on, said with Gemini's reason
                raise RunFailed(f"Gemini returned no answer: {_no_answer(response, candidate)}")
            calls = [p.function_call for p in parts if p.function_call]
            if not calls:
                await r.finish_from_text(response.text or "")
                return
            contents.append(content)
            replies = []
            for call in calls:
                result, picture = await r.call_tool(call.name, dict(call.args or {}))
                replies.append(types.Part.from_function_response(name=call.name, response=result))
                if picture:
                    replies.append(types.Part.from_bytes(data=picture, mime_type="image/jpeg"))
            contents.append(types.Content(role="user", parts=replies))
            if r.result is not None or final:
                return  # written up, or made to and could not: the run keeps what it found (Researcher.run)


class FakeEngine:
    """No network. The spec's "fake" dict scripts it: seconds (total, default 1),
    snapshot (ask for one), finding, correction {to_label, reason}, fail, significance;
    things to make at once (locally, no model): annotate [labels], illustrate "prompt",
    lesson {title, key_ideas, questions}; fetch [urls] (real safe_fetch calls); answer
    (finish's summary); no_finish (stop without writing up); revise {seconds, lesson: the
    edited content, labels: the edited list} for a revision (Researcher.revise); plan
    {searches, sources} (a deep run's plan); request [{what, why, seconds}] (evidence asked of the user);
    session [{action, id, ...}] (reads of the session's index,
    each entry read is cited in its summary with its title and when it was made, and in finish.evidence);
    evidence [ids] and session_claims [{point, evidence}] (passed to finish as they are); claims [{point, source}] with pages {url: text}
    (the claims check reads these, nothing is fetched); lesson_check {right: [step ids answered
    without the picture], rewrite: the edited lesson}; idle N (rounds after its work that find nothing). Notes from
    the user (inbox) are read at the start and halfway, and repeated in its summary.

    Its rounds go through _final and the "keep going?" check-in as the others' do: a deep run's scripted plan is a
    round of its own, its work the next, then its idle rounds, then the write-up. Made to write up (nothing new in
    STALL_ROUNDS rounds, a capped day's limit, no answer to "keep going?") or told to (time nearly or wholly up), it
    skips what is left and writes up what it has, as a model that heeds the app would."""

    model = "fake"

    over_budget = False
    offline = True

    async def run(self, r: Researcher) -> None:
        script = r.spec.get("fake") or {}
        seconds = float(script.get("seconds", os.environ.get("MICROMIND_FAKE_RESEARCH_SECONDS", 1.0)))
        r.offline = True
        r.take_notes()
        round_n, cited = 0, []

        async def next_round() -> bool:
            nonlocal round_n
            round_n += 1
            await r.keep_going()
            final, told = _final(r, round_n, self)
            return final or told in (WRAP_UP, WRITE_UP_NOW)

        final = await next_round()
        if not final and script.get("plan") and r.plan_due():  # a deep run's first round only plans
            await r.call_tool("plan", script["plan"])
            final = await next_round()
        if not final:
            cited = await self._work(r, script, seconds)
            final = await next_round()
        for _ in range(int(script.get("idle") or 0)):
            if final:
                break
            r.step("Looked again: nothing new (simulated researcher)", "search")
            final = await next_round()
        if script.get("no_finish"):
            return
        await self._finish(r, script, cited)

    async def _work(self, r: Researcher, script: dict, seconds: float) -> list[tuple[str, str]]:
        """Its one round of work: what the script asks it to look up, make and find. Returns the session's entries it
        read (id, how its summary cites it)."""
        for request in script.get("request") or []:
            await r.call_tool("request_evidence", request)
        r.step("Looking it up (simulated researcher)", "search")
        r.spend("fake", 0.001)
        if script.get("snapshot"):
            await r.call_tool("snapshot", {})
        for url in script.get("fetch") or []:
            await r.call_tool("fetch_reference_image", {"url": url})
        if script.get("annotate"):
            await r.call_tool("annotate_image", {"labels": script["annotate"]})
        if script.get("illustrate"):
            await r.call_tool("generate_image", {"prompt": script["illustrate"]})
        if script.get("lesson"):
            await r.call_tool("make_lesson", script["lesson"])
        cited = []
        for args in script.get("session") or []:
            result, _ = await r.call_tool("session", args)
            entry = _session_entry(result) if args.get("action") == "read" else None
            if entry:
                cited.append((str(entry.get("id")),
                              f"{entry.get('id')} ({entry.get('title')}, made {entry.get('time') or entry.get('at')})"))
        await asyncio.sleep(seconds / 2)
        r.take_notes()
        if script.get("fail"):
            raise RuntimeError("scripted failure")
        if script.get("finding"):
            await r.call_tool("record_finding", {"text": script["finding"], "kind": "fact"})
        if script.get("correction") and r.spec.get("deliverable") != "answer":  # suggested once, not again on follow-ups
            await r.call_tool("propose_correction", {"to_label": script["correction"]["to_label"],
                                                     "reason": script["correction"].get("reason", "scripted"), "confidence": 0.8})
        await asyncio.sleep(seconds / 2)
        return cited

    async def _finish(self, r: Researcher, script: dict, cited: list[tuple[str, str]]) -> None:
        cited_ids = [i for i, _ in cited]
        label = r.spec.get("label") or "it"
        summary = script.get("answer") or f"Simulated research on: {r.brief()[:120]}"
        if cited:
            summary += " From the session: " + "; ".join(said for _, said in cited) + "."
        if r.notes_heard:
            summary += f" (notes from the user: {' / '.join(r.notes_heard)[:300]})"
        await r.call_tool("finish", {
            "title": f"About {label}"[:60], "summary": summary,
            "one_liner": script.get("one_liner") or f"Researcher {r.spec.get('n', 1)} finished looking into {label}.",
            "significance": script.get("significance", "low"), "details": ["simulated detail"],
            "look_for": "simulated", "confidence": 0.5, "sources": [], "ref_candidates": script.get("ref_candidates") or [],
            **({"claims": script["claims"]} if script.get("claims") else {}),
            **({"evidence": script.get("evidence") or cited_ids} if script.get("evidence") or cited_ids else {}),
            **({"session_claims": script["session_claims"]} if script.get("session_claims") else {})})


def _session_entry(result: dict) -> dict | None:
    """The entry a scripted session read returned (inside its untrusted wrapper), or None."""
    text = str(result.get("data") or "")
    body = text.split("\n", 1)[-1].rsplit("\n", 1)[0] if "\n" in text else ""
    try:
        return (json.loads(body) or {}).get("entry")
    except ValueError:
        return None
