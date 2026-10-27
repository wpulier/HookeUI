"""Does the fast pass (orchestrator/brain/fast.py) know when it can act without a new look?

Requests against fixed notes (a running look with two stomata and a dark speck, one
stoma outlined and selected, a finished researcher), each with the right call: act now with
a given tool, answer now from notes, wait for the vision call (needs_look), or do nothing
(remarks, fillers and pieces of speech never act). Measured per model: right calls and time.

  cd services && .venv/bin/python -m bench.fast_bench [--models gemini-3.5-flash-lite,gemini-3.8-flash] [--level simple]

The notes carry the user's level line (learner.py) as the app's do; --level none leaves it out.

Writes results/fast-bench.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from bench.hygiene import Variant, summarise as hygiene, variant_names
from bench.tool_choice import level_line, registry
from orchestrator.brain.context import Moment
from orchestrator.brain.fast import decide
from orchestrator.keys import load_keys
from orchestrator.options import VISION
from orchestrator.vision import make_vision

BENCH = Path(__file__).resolve().parent
SEEN = ("Leaf epidermis: a sheet of green cells with two oval stomata. Specimen: leaf epidermis, ~400x. Things in view: "
        "s1 stoma (centre); s2 stoma (upper right); s3 dark speck (lower left); s4 epidermal cells (all around). Focus: sharp.")
SCREEN = "o1 'stoma' is selected"
SCREEN_MAG = "the magnifier is open on o1 'stoma' (live zoom); o1 'stoma' is selected"
SCREEN_VIEWER = "showing Researcher 1's lesson (Interactive lesson: stomata) beside the live view; o1 'stoma' is selected"
SCREEN_VIEWER_Q1 = ("showing Researcher 1's lesson (Interactive lesson: stomata) beside the live view, on step 1 of 3 "
                    "(the researcher's question, information only: \"What opens the pore?\"); o1 'stoma' is selected")
RESEARCH = "Researcher 1 (guard cells): done: Guard cells open the pore by taking up water."
SCREEN_CELLS = ("the cell outlines e1 (Cell outlines · 18:04: 6 instances segmented by cellpose-sam) are shown; "
                "the user clicked cell c3 of e1")
# (request, screen, expected): expected is a tool name (act now), "answer" (answer now), "look",
# "none" (no action), the model's intent "unfinished" / "cancel", or "look+<tool>" (needs a look for
# part of it, and does <tool> at once); a tuple of them accepts any. An optional fourth item forbids
# markers: "find:concept" (find by SAM 3.1's words, only when asked for).
CASES = [
    ("What are we looking at?", SCREEN, "answer"),
    ("Magnify that.", SCREEN, "zoom"),
    ("Highlight the other stoma.", SCREEN, "highlight"),
    ("Close the magnifier.", SCREEN_MAG, "close_magnifier"),
    ("Can you magnify it more, even if it blurs?", SCREEN_MAG, "zoom"),
    ("Show me what the researcher found.", SCREEN, "show_result"),
    ("Can you teach me more about that?", SCREEN, "start_researcher"),
    ("Does the dark speck have a ring around it?", SCREEN, "look"),
    ("Is the stoma on the right bigger than the one in the middle?", SCREEN, "look"),
    ("What's that thing right at the top edge?", SCREEN, "look"),
    ("Clear everything.", SCREEN, "clear"),
    ("Highlight the stomata.", SCREEN, "highlight"),
    ("What is it made of?", SCREEN, "dissect"),
    ("Close that.", SCREEN_VIEWER, "close_result"),
    ("Bring the lesson back up.", SCREEN, "show_result"),
    # Researchers' work: go through it, never invent it (live session 2026-10-04, 15:10-15:12).
    ("Can you walk me through the quiz the researcher made?", SCREEN_VIEWER, ("result_step", "read_result")),
    ("What's the next question?", SCREEN_VIEWER_Q1, "result_step"),
    ("What does the lesson say about guard cells?", SCREEN_VIEWER, "read_result"),
    ("Let me find things myself, stop pointing them out.", SCREEN, "set_lead"),
    # How to explain (learner.py, W6-1): the model judges that the user asked.
    ("Can you explain things more simply? I'm new to this.", SCREEN, "set_level"),
    ("I'm a biology student, so be technical with me.", SCREEN, "set_level"),
    ("Give me hints first instead of telling me the answer.", SCREEN, "set_level"),
    # Changing a researcher's work vs asking about it vs making something new.
    ("Make question 2 of the quiz easier.", SCREEN_VIEWER, "revise_work"),
    ("Rename the label on the picture to guard cell.", SCREEN_VIEWER, "revise_work"),
    ("Ask the researcher why the pore closes at night.", SCREEN_VIEWER, "researcher_followup"),
    ("Make me another picture, of a closed stoma this time.", SCREEN, "start_researcher"),
    # The session's conversations and memory: start, go back, look back.
    # Corrections of segmented cells (M2.3): only when the user says one is wrong, never a remark about it.
    ("That's not a cell, take it off.", SCREEN_CELLS, "correct_cells"),
    ("These two are one cell.", SCREEN_CELLS, "correct_cells"),
    ("Undo that correction.", SCREEN_CELLS, "correct_cells"),
    ("Huh, that cell looks odd.", SCREEN_CELLS, ("none", "answer", "look")),
    ("Is that really a cell?", SCREEN_CELLS, ("answer", "look")),
    ("Let's start a new topic.", SCREEN, "new_thread"),
    ("Can we go back to the blood smear conversation?", SCREEN, "switch_thread"),
    ("What did we say about the platelet earlier today?", SCREEN, "recall"),
    # Sessions (ADR 0017): a new session only when asked for in so many words; a new topic or starting over is a
    # conversation in this one, and a question about another session looks it up without going there.
    ("Start a new session.", SCREEN, "new_session"),
    ("New topic, let's look at the onion.", SCREEN, "new_thread"),
    ("Let's start over.", SCREEN, ("new_thread", "none")),
    ("Go back to yesterday's leaf session.", SCREEN, "switch_session"),
    ("What did we find in Tuesday's blood smear session?", SCREEN, "recall"),
    ("Back to the leaf session: what did we conclude?", SCREEN, "switch_session"),
    # Remarks, thinking aloud and pieces of speech never act (same session: "Okay" outlined a
    # bristle, "and" a nucleus, "I wanna become more observant" two things).
    ("Okay", SCREEN, "none"),
    ("interesting", SCREEN, "none"),
    ("Uh this is really", SCREEN, "none"),
    ("So I wanna become more observant myself", SCREEN, "none"),
    ("So the magnifying of the stoma is cool", SCREEN_MAG, "none"),
    ("I'm going to change the slide now, but this is really interesting", SCREEN, "none"),
    ("To me, looking at this, it seems the cells are elongated", SCREEN, "none"),
    ("Yeah", SCREEN, "none"),
    # The model judges what used to be word lists (2026-10-06): cut off mid-sentence, called off,
    # and "wait" that is not a cancel.
    ("Can you close the", SCREEN_MAG, "unfinished"),
    ("What is", SCREEN, "unfinished"),
    ("Oh, wait, never mind", SCREEN_MAG, "cancel"),
    ("Actually, forget it", SCREEN, "cancel"),
    ("Wait for it to settle, then zoom in on the stoma", SCREEN, "zoom"),
    ("Stop highlighting the stoma", SCREEN, ("clear", "highlight")),
    # Part needs a look, part does not (2026-10-06): the steps that need none are given and done at
    # once; the close does not wait 3-5 s for the vision call.
    ("Close the magnifier and highlight the hairs along the edge.", SCREEN_MAG, "look+close_magnifier"),
    ("Clear the outlines and find the guard cell nuclei.", SCREEN, "look+clear"),
    # The live journey of 2026-10-11: words alone (SAM 3.1) were chosen for "guard cells", the find schema's
    # own example for "concept", and found nothing. A look, or find by look inside the stoma, finds them.
    ("Close the magnifier then highlight the guard cells.", SCREEN_MAG, ("look+close_magnifier", "close_magnifier+find"),
     ("find:concept",)),
    ("Highlight the guard cells in that stoma.", SCREEN, ("look", "find"), ("find:concept",)),
]


def judged(d, expected) -> bool:
    """One expected call (see CASES) against the fast pass's decision; "a+b" acts with both at once."""
    used = [n for n, _ in d.actions]
    if expected.startswith("look+"):
        return d.needs_look and expected[5:] in used
    if expected == "look":
        return d.needs_look
    if expected == "answer":
        return not d.needs_look and bool(d.answer) and "start_researcher" not in used
    if expected == "none":
        return not used  # no action, whatever it answers
    if expected in ("unfinished", "cancel"):
        return d.intent == expected and not used and not d.needs_look
    return not d.needs_look and all(part in used for part in expected.split("+"))


def markers(d) -> list[str]:
    return ["find:concept" for n, a in d.actions if n == "find" and (a or {}).get("by") == "concept"]


def notes(screen: str, level: str = "simple") -> Moment:
    """What the fast pass is told in each case (brain/context.Moment)."""
    return Moment(outlined=('o1 "stoma" (box x 0.47-0.53, y 0.47-0.57, 0.6% of the view; likely; yours; selected)',), outlined_plain=(), selected="o1", seen=SEEN, view=None, screen=screen,
                  screen_voice=screen, researchers=RESEARCH, roster="", history="(none)", recap_lines=(),
                  title="", summary="", thread_tag="", digest="", user_leads=False, learner=level_line(level))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="gemini-3.5-flash-lite,gemini-3.8-flash")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--only", default="", help="run only the requests containing this text (case-insensitive)")
    parser.add_argument("--menu", default="flat", help="how the tool menu is laid out (options.MENU)")
    parser.add_argument("--variants", default="as-is", help="prompt variants (bench/hygiene.py): as-is,reflowed,framed")
    parser.add_argument("--level", default="simple",
                        help="how to explain, as the app tells it (learner.py): none (no line), simple, technical, "
                             "technical+hints")
    args = parser.parse_args()
    load_keys()
    summary, rows = {}, []
    for model in args.models.split(","):
        for variant in variant_names(args.variants):
            right, ms = 0, []
            vision, tools = Variant(make_vision(VISION[model].spec), variant), registry().prompt_section(args.menu)
            for _ in range(args.repeats):
                for text, screen, expected, *forbid in CASES:
                    if args.only.lower() not in text.lower():
                        continue
                    d = await decide(notes(screen, args.level), text, vision, tools, model)
                    used = [n for n, _ in d.actions]
                    accepted = expected if isinstance(expected, tuple) else (expected,)
                    ok = any(judged(d, e) for e in accepted) and not set(markers(d)) & set(forbid[0] if forbid else ())
                    right += ok
                    ms.append(d.ms)
                    # Acting when the case asked for none: a remark or a plain answer that acted, or a
                    # case that needs a look acted on without one.
                    acted = [] if d.needs_look else used
                    unasked = expected in ("none", "answer", "look", "unfinished", "cancel") and bool(acted)
                    rows.append({"model": model, "variant": variant, "text": text, "expected": expected, "needs_look": d.needs_look,
                                 "used": used + markers(d), "answer": d.answer, "ms": round(d.ms), "right": ok, "unasked": unasked})
                    print(f"{'ok ' if ok else 'BAD'} {model[:16]:16} {text[:50]:50} -> {'LOOK' if d.needs_look else used + markers(d)} {round(d.ms)} ms  {d.answer[:60]}", flush=True)
            summary[f"{model}/{variant}"] = {"level": args.level, "right": f"{right}/{len(ms)}", "p50Ms": round(float(np.median(ms))),
                                             "p90Ms": round(float(np.percentile(ms, 90)))}
    for model in args.models.split(","):
        summary[f"{model} hygiene"] = hygiene([r for r in rows if r["model"] == model])
    print(json.dumps(summary, indent=2))
    if args.only:
        return  # a subset never replaces the whole bench's results
    (BENCH / "results" / "fast-bench.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
