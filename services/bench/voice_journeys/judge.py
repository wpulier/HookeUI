"""What code can't time in a partner step, judged by a model (standard.py reads its verdicts): which spoken lines claim
that something is now on screen or done (checked there against the studio's reports, so a false "done" is the judge's
reading of the words and the app's record of the screen together), how many lines state the request's outcome, whether
the request was answered, whether the answer meets the step's expectation, and how much it felt like a sharp
colleague (`partner_quality`, 0-3: reported on the scoreboard's Overall line, never a gate). The judge sees the camera picture the
plan names (the bundled still), the user's words, the voice's lines and the tools with their confirmations.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from .standard import load_bench, released, step_view, thinks, unprompted_spans, windows

JUDGE = "openai:gpt-6.1-sol@low"  # as score.py: another model than the voices' own
COST_PER_STEP_USD = 0.02  # a bounded judge call (one image, about 2,000 tokens in, 600 out), for the worst case

PROMPT = """You are scoring a live voice research partner for a microscope app against a strict standard. Image 1 (if
given) is the specimen the camera showed.

{earlier}The user said: "{say}"
What a good answer does here: {expect}
What the voice said afterwards, in order (seconds after the user finished):
{lines}
What the app did (seconds after the user finished; "confirmed" = the studio reported the change on screen):
{actions}

Judge from these facts only:
- claims_on_screen: each claim that a change is now on screen or complete (open, shown, highlighted, outlined, done),
  as {{"line": its line's number (1-based), "words": the few words of the line that make the claim, exactly as said}}.
  Not claims: a description of the specimen; a promise or a preamble ("opening it now", "outlining the nuclei");
  a statement that work is still under way ("it's still outlining"); and a researcher's state ("Researcher 2 is
  still running", "I started a researcher"), which the bench checks against the app's own list of researchers.
- outcome_mentions: how many separate lines state the request's outcome (its result, or its failure). A preamble
  ("pulling that up") is not an outcome; a line that says the same outcome again counts again.
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
{{"claims_on_screen": [{{"line": 2, "words": "the table is open"}}], "outcome_mentions": 1, "answered": true,
 "meets_expectation": true, "partner_quality": 2, "why": ""}}"""


def facts(bench: dict, conv: list[dict], inter: list[dict]) -> list[dict]:
    """Each scored step's words, lines and actions, relative to its release (what PROMPT is given)."""
    bench = load_bench(bench)
    steps = [s for s in bench["steps"] if not s.get("setup")]
    out, unprompted, tasks = [], unprompted_spans(inter), thinks(inter)
    for step, (start, end) in zip(steps, windows(steps)):
        if step.get("kind") in ("idle", "restart"):
            continue
        release = released(step)
        _, calls, _, lines = step_view(bench, conv, inter, (start, end), unprompted, tasks)
        out.append({"id": step["id"], "say": step.get("say", ""), "release": release, "said": [l["text"] for l in lines],
                    "lines": [f"{i + 1}. (+{l['first'] - release:.1f} s) {l['text']}" for i, l in enumerate(lines)],
                    "actions": [f"- (+{c.start - release:.1f} s) {c.name}: "
                                f"{'ok' if c.ok else 'failed' if c.ok is False else 'running'}"
                                + (f", confirmed +{c.confirmed_at - release:.1f} s" if c.confirmed_at is not None else
                                   ", not confirmed" if c.screen_change else "")
                                + (f" ({c.error[:120]})" if c.error else "")
                                + (f"; behind it: {', '.join(str(t.get('name')) for t in c.inner)}" if c.inner else "")
                                for c in calls]})
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
    out, history = {}, []
    for step in facts(bench, conv, inter):
        expect = expects.get(step["id"], {})
        # The two turns before, so "that one" and "the second researcher" can be judged as references.
        earlier = "".join(f'Earlier, the user said "{say}"; the voice said: {" ".join(said)[:300] or "(nothing)"}\n'
                          for say, said in history[-2:])
        history.append((re.sub(r"\[\[slnc \d+\]\] ?", "", step["say"]), step["said"]))
        prompt = PROMPT.format(earlier=earlier, say=re.sub(r"\[\[slnc \d+\]\] ?", "", step["say"]),
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

