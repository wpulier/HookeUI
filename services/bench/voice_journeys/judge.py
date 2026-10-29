"""What code can't time in a partner step, judged by a model (standard.py reads its verdicts): which spoken lines claim
that something is now on screen or done (checked there against the studio's reports, so a false "done" is the judge's
reading of the words and the app's record of the screen together), how many lines state the request's outcome, whether
the request was answered, and whether the answer meets the step's expectation. The judge sees the camera picture the
plan names (the bundled still), the user's words, the voice's lines and the tools with their confirmations.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from .standard import card_calls, load_bench, partner_calls, released, step_lines, turn_number, unprompted_spans, windows

JUDGE = "openai:gpt-6.1-sol@low"  # as score.py: another model than the voices' own
COST_PER_STEP_USD = 0.02  # a bounded judge call (one image, about 2,000 tokens in, 600 out), for the worst case

PROMPT = """You are scoring a live voice research partner for a microscope app against a strict standard. Image 1 (if
given) is the specimen the camera showed.

The user said: "{say}"
What a good answer does here: {expect}
What the voice said afterwards, in order (seconds after the user finished):
{lines}
What the app did (seconds after the user finished; "confirmed" = the studio reported the change on screen):
{actions}

Judge from these facts only:
- claims_on_screen: each claim that something is now on screen, open, shown, highlighted, started or done, as
  {{"line": its line's number (1-based), "words": the few words of the line that make the claim, exactly as said}}
  (a description of the specimen is not a claim; "opening it now" is a promise, not a claim).
- outcome_mentions: how many separate lines state the request's outcome (its result, or its failure). A preamble
  ("pulling that up") is not an outcome; a line that says the same outcome again counts again.
- answered: true if, by the end, the user got what they asked for (or was told plainly why not and what to do next).
- meets_expectation: true if the answer does what "a good answer does here" says; null if that says nothing.
- why: one sentence.

Reply with JSON only:
{{"claims_on_screen": [{{"line": 2, "words": "the table is open"}}], "outcome_mentions": 1, "answered": true,
 "meets_expectation": true, "why": ""}}"""


def facts(bench: dict, conv: list[dict], inter: list[dict]) -> list[dict]:
    """Each scored step's words, lines and actions, relative to its release (what PROMPT is given)."""
    bench = load_bench(bench)
    steps = [s for s in bench["steps"] if not s.get("setup")]
    out, unprompted = [], unprompted_spans(inter)
    for step, (start, end) in zip(steps, windows(steps)):
        if step.get("kind") in ("idle", "restart"):
            continue
        release = released(step)
        n = turn_number(bench["events"], start, end)
        lines = step_lines(conv, inter, start, end, unprompted)
        calls = partner_calls(inter, n, start, end) or card_calls(conv, start, end)
        out.append({"id": step["id"], "say": step.get("say", ""), "release": release,
                    "lines": [f"{i + 1}. (+{l['first'] - release:.1f} s) {l['text']}" for i, l in enumerate(lines)],
                    "actions": [f"- (+{c.start - release:.1f} s) {c.name}: "
                                f"{'ok' if c.ok else 'failed' if c.ok is False else 'running'}"
                                + (f", confirmed +{c.confirmed_at - release:.1f} s" if c.confirmed_at is not None else
                                   ", not confirmed" if c.screen_change else "")
                                + (f" ({c.error[:120]})" if c.error else "") for c in calls]})
    return out


async def verdicts(bench: dict, conv: list[dict], inter: list[dict], plan: dict, picture: bytes | None,
                   model=None) -> dict:
    """{step id: verdict} for every scored step (claims_on_screen as 0-based line indices, as standard.py reads)."""
    if model is None:
        from orchestrator.keys import load_keys
        from orchestrator.vision import make_vision

        load_keys()
        model = make_vision(JUDGE)
    expects = {s["id"]: s.get("expect") or {} for s in plan["steps"]}
    out = {}
    for step in facts(bench, conv, inter):
        expect = expects.get(step["id"], {})
        prompt = PROMPT.format(say=step["say"].replace("[[slnc 1500]] ", ""),
                               expect=expect.get("judge") or (f"shows {expect['visible']}" if expect.get("visible") else
                                                              "answers what was asked"),
                               lines="\n".join(step["lines"]) or "(nothing)",
                               actions="\n".join(step["actions"]) or "(nothing)")
        result = await model.ask([picture] if picture else [], prompt, max_output_tokens=600)
        data = result.data if isinstance(getattr(result, "data", None), dict) else None
        if data is None:
            out[step["id"]] = {"error": (getattr(result, "error", None) or getattr(result, "raw_text", ""))[:200]}
            continue
        claims = []
        for claim in data.get("claims_on_screen") or []:  # 1-based from the judge, 0-based for standard.py
            if isinstance(claim, int) and claim >= 1:
                claims.append(claim - 1)
            elif isinstance(claim, dict) and isinstance(claim.get("line"), int) and claim["line"] >= 1:
                claims.append({"line": claim["line"] - 1, "words": str(claim.get("words") or "")})
        out[step["id"]] = {**data, "claims_on_screen": claims}
    return out


def judge_run(bench: dict, conv: list[dict], inter: list[dict], plan: dict, checkout: Path) -> dict:
    still = (plan.get("camera") or {}).get("still")
    picture = (checkout / still).read_bytes() if still and (checkout / still).exists() else None
    return asyncio.run(verdicts(bench, conv, inter, plan, picture))

