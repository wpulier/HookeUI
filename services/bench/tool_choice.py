"""Does the brain pick the right tool when the user asks for one?

Eighty-eight requests, each with the tool it must use (or none required), asked about a June 14
frame with two stomata on screen (o1 selected) and one researcher running, through the brain's
answer prompt with the full tool list (json-actions). A request counts as right when the
expected tool (or one of the expected tools, where two answer it) is among the actions, and no tool it
forbids is ("try the other model" never switches the default); "none" requests count as right when no
researcher is started for a plain question. A request may be asked on another board (SCENES: a finished
researcher, its work open beside the view, the magnifier open).

  cd services && .venv/bin/python -m bench.tool_choice [--repeats 2] [--model gemini-3.8-flash] [--level simple]

The scene table ends with the user's level line (learner.py), as the brain's does; --level none
leaves it out.

Writes results/tool-choice-<model>.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from bench.grounding import prepare
from bench.hygiene import Variant, summarise as hygiene, variant_names
from orchestrator.brain.registry import Registry
from orchestrator.brain.toolbox import catalogue
from orchestrator.keys import load_keys
from orchestrator.learner import line as learner_line
from orchestrator.options import VISION
from orchestrator.prompts import ASK, with_tools
from orchestrator.vision import make_vision

BENCH = Path(__file__).resolve().parent
FRAME = {"frame": "leaf-132922-012s.jpg", "targets": [[853, 387]], "tolerance": 40, "kind": "shape", "question": ""}
SCENE = ('o1 "stoma" (box x 0.63-0.69, y 0.50-0.58, 0.4% of the view; likely; yours)\n'
         'o2 "stoma" (box x 0.49-0.55, y 0.65-0.73, 0.4% of the view; likely; yours)\nResearchers:\n'
         '<researchers source="web" untrusted="true">\nResearcher 1 (guard cells): running, Searched the web\n</researchers>')
# The conversation before a request that refers back to it (as the brain's history is written, brain/state.py).
TWO_NAMED = ("User: was that outline drawn by EdgeTAM or SAM 2.1?\n"
             "You: EdgeTAM drew it, on this Mac, after the look found the stoma.")
CASES = [
    ("Please start a researcher to look into how guard cells open and close.", "start_researcher"),
    ("Can you research what plant this leaf might come from?", "start_researcher"),
    ("Look into why stomata are mostly on the underside of leaves.", "start_researcher"),
    ("Have a researcher double-check whether this is really a stoma.", "start_researcher"),
    # Since snapshot (D3): the app's own labelled picture of the real view answers this too.
    ("Make me an annotated picture of it.", ("start_researcher", "snapshot")),
    ("What is it made of?", "dissect"),
    ("Break it down into its parts.", "dissect"),
    ("What parts does the selected one have?", "dissect"),
    ("Zoom in on it.", "zoom"),
    ("Give me a closer look at the selected stoma.", "zoom"),
    ("Compare the two stomata side by side.", ("compare", "contrast")),
    # Both at once: side by side in one magnifier (going live G4, 2026-10-07), or a magnifier window on each (zoom twice:
    # independent windows since 2026-10-11).
    ("Can you zoom into both of them?", ("compare", "contrast", "zoom×2")),
    ("Make them two separate windows.", ("compare", "contrast", "zoom×2")),
    ("Can you move the magnifier to the top left?", "present"),  # it can be moved and resized (G4 review)
    # The partner's live cases 6 and 7 (2026-10-07): a real photo is the fast reference, never a researcher's
    # illustration; a magnification next to the photo is the zoom beside it, never compare (two outlines).
    ("Pull up a real picture of a stoma, like another one, and then uh have it, just the picture pulled up, zoomed",
     "reference_photo"),
    ("Show me what a normal guard cell looks like, a real photo.", "reference_photo"),
    ("Pull up a magnification so I can look at it next to the photo", "zoom"),
    ("How are these two stomata different from each other?", "contrast"),
    ("How many stomata are there?", "count"),
    ("Is it in focus?", "focus_check"),
    ("How is the researcher getting on?", "research_status"),
    ("What have the researchers found out about it so far?", ("summarize_research", "research_status")),
    # Researcher 1 is still working (the notes above): a note is the way to ask it something.
    ("Ask researcher 1 whether these are common on the underside of leaves.", ("researcher_followup", "researcher_note")),
    # An update for a working researcher (2026-10-11): the user's change of plan reaches it as a note.
    ("Tell researcher 1 to also give the median.", "researcher_note"),
    ("Researcher 1, skip the first test and just do four.", "researcher_note"),
    ("Actually, have the researcher focus on the pore size instead.", "researcher_note"),
    # A part inside an outline: the look's own boxes ("targets") or the find tool both outline it.
    ("Point out the guard cells inside the selected stoma.", ("find", "targets")),
    ("Can you find the pore inside that stoma?", ("find", "targets")),
    # Words alone (SAM 3.1, find by="concept") only when asked for: the live journey of 2026-10-11 chose it for
    # "guard cells", the schema's own example, and found nothing (it finds nothing on our slides).
    ("Close the magnifier then highlight the guard cells.", ("find", "targets"), {"scene": "magnifier", "forbid": ("find:concept",)}),
    ("Highlight the guard cells in that stoma.", ("find", "targets"), {"forbid": ("find:concept",)}),
    ("Have SAM 3.1 find the guard cells just from the words.", "find:concept"),
    # Across the menu (D1, 2026-10-06): the asks of the live session that night, and one per family.
    ("Highlight all the darker cells.", ("find", "targets", "highlight")),
    ("Find every one that looks like the selected stoma.", "find"),
    ("Outline every cell in view.", ("outline_cells", "find")),
    ("Make the outlines yellow.", "set_style"),
    ("Can you make the highlight easier to see?", "set_style"),
    ("How big is it?", "measure"),
    ("I'm on the 40x objective now.", "set_objective"),
    ("This is onion skin.", "name_specimen"),
    ("Let me find things myself from now on.", "set_lead"),
    # How to explain (learner.py, W6-1).
    ("Explain things more simply from now on, I'm new to this.", "set_level"),
    ("I'm a biology student, you can use the technical terms.", "set_level"),
    ("Give me a hint first before you tell me what things are.", "set_level"),
    ("New topic: let's look at blood.", "new_thread"),
    # Sessions (ADR 0017): a new session only when asked for in so many words; a new topic or starting over is a
    # conversation in this one, and a question about another session looks it up without going there.
    ("Start a new session.", "new_session", {"forbid": ("new_thread",)}),
    ("New topic, let's look at the onion.", "new_thread", {"forbid": ("new_session",)}),
    ("Let's start over.", ("new_thread", "none"), {"forbid": ("new_session",)}),
    ("Go back to yesterday's leaf session.", "switch_session"),
    ("What did we find in Tuesday's blood smear session?", ("recall", "resources"), {"forbid": ("switch_session",)}),
    ("Back to the leaf session: what did we conclude?", "switch_session"),
    # Motion (D2): tonight's "are they slowing down?" was answered from general knowledge.
    ("Are they swimming?", "track_motion"),
    ("How fast are they moving?", "track_motion"),
    ("They look like they're slowing down. Are they dying?", "track_motion"),
    ("Why is everything drifting to one side?", "track_motion"),
    # Seeing better and snapshots (D3): a picture from real frames, and a picture of the view to keep.
    ("Can you clean up the picture?", "enhance"),
    ("Stack the focus for me.", "enhance"),
    ("Take a picture of this with the labels.", "snapshot"),
    ("Save this view.", "snapshot"),
    # Time (D4): looking back, and watching for something to happen.
    ("What just happened?", "what_happened"),
    ("Did you see that thing swim past a moment ago?", "what_happened"),
    ("Tell me when they stop moving.", "watch"),
    ("Let me know if anything swims into view.", "watch"),
    ("Stop watching for that.", "stop_watching"),
    ("Have we seen something like this before today?", "seen_before"),
    # The capability list (ADR 0007): asking what it can do, or whether it can do something.
    ("What can you do?", "capabilities"),
    # Side by side is a fair answer; so is a magnifier window on each (independent windows since 2026-10-11).
    ("Can you show two magnifier windows at once?", ("capabilities", "compare", "zoom×2")),
    ("What is that oval?", None),
    ("What am I looking at?", None),
    # Kind (c), the going-live note 3 (2026-10-07, 16:19): "let the researcher run the motion model" started an
    # instant researcher, which has no hands ("My tools don't include running a motion model"). Right: the app's own
    # measurement now, or a researcher at an effort whose hands run it ("start_researcher:hands", quick and up).
    # (Researcher 1 is running in this scene: a note asking it to is a fair reading of "the researcher".)
    ("Let the researcher run the motion model on this and write a short report.",
     ("track_motion", "start_researcher:hands", "researcher_note")),
    ("Have a researcher measure how fast these things swim.", ("track_motion", "start_researcher:hands")),
    ("Get a researcher to segment every cell here and tell me how much they vary in size.",
     ("outline_cells", "start_researcher:hands")),
    # Choosing a model (Slice 0, 2026-10-11; ADR 0011): for this request (`use`) or from now on (`set_default`), the
    # model judging which from the words (Q3); a casual "try the other model" is never a default switched silently.
    ("Use SAM 2.1 for this one.", "use"),
    ("Try outlining it with micro-sam instead.", "use"),
    # After a question naming two models, so "the other model" has one referent (without one, asking which of five is fair).
    ("Hmm, try the other model.", "use", {"forbid": ("set_default",), "history": TWO_NAMED}),
    ("From now on, use micro-sam for outlines.", "set_default"),
    ("Always segment the cells with Cellpose from now on.", "set_default"),
    ("Compare EdgeTAM and SAM 2.1 on this stoma.", "compare_models"),
    ("Which outlines it better, SAM 2.1 or EdgeTAM? Show me both.", "compare_models"),
    # How an outline was made is answered from its own record (Q4: resources id=o1), never guessed.
    ("Which model did you use to outline it?", "resources"),
    ("How do you know it's a stoma?", "resources"),
    # A finished researcher's work (AGENTS.md: "show me what it found" is show_me; opening its work is show_result).
    ("Show me what it found.", "show_me", {"scene": "finished"}),
    ("Open its work.", "show_result", {"scene": "finished"}),
    ("Open what researcher 2 made.", "show_result", {"scene": "finished"}),
    ("What does researcher 2's report say?", "read_result", {"scene": "finished"}),
    ("Read me the lesson.", ("read_result", "result_step"), {"scene": "finished"}),
    ("Close the researcher's work.", ("close_result", "present"), {"scene": "viewer"}),
    # "Close that" closes what is in front: the researcher's work beside the view, or the magnifier.
    ("Close that.", ("close_result", "present"), {"scene": "viewer"}),
    ("Close that.", ("close_magnifier", "present"), {"scene": "magnifier"}),
]
# The scene table for cases that need another board than SCENE: a finished researcher (Researcher 2's lesson), its
# work open beside the view, or the magnifier open on o1. As the brain's scene table writes them (brain/context.py).
FINISHED = ('o1 "stoma" (box x 0.63-0.69, y 0.50-0.58, 0.4% of the view; likely; yours)\n'
            'o2 "stoma" (box x 0.49-0.55, y 0.65-0.73, 0.4% of the view; likely; yours)\nResearchers:\n'
            '<researchers source="web" untrusted="true">\n'
            'Researcher 2 (guard cells): done: A short lesson on how guard cells open and close the pore. [made: o1 lesson]\n'
            '</researchers>')
SCENES = {
    "finished": FINISHED,
    "viewer": FINISHED + "\nOn screen now: showing Researcher 2's lesson (Interactive lesson) beside the live view, on step 1 of 2",
    "magnifier": SCENE + "\nOn screen now: the magnifier is open on o1 'stoma' (live zoom ×3); o1 'stoma' is selected",
}
HANDS = ("quick", "standard", "deep")  # efforts whose researchers can have the app run its operations (budget.EFFORTS)
CALL_TIMEOUT_S = 60.0  # one case's model call; past it the case is an error


def level_line(level: str) -> str:
    """The level line for --level ("none": no line, as before levels)."""
    if level == "none":
        return ""
    name, _, hints = level.partition("+")
    return learner_line(name, hints == "hints")


def registry() -> Registry:
    return catalogue()  # every tool, as the brain is given them


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemini-3.8-flash")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--menu", default="flat", help="how the tool menu is shown (options.MENU): flat, families, compact")
    parser.add_argument("--variants", default="as-is", help="prompt variants (bench/hygiene.py): as-is,reflowed,framed")
    parser.add_argument("--only", default="", help="run only the requests containing this text (case-insensitive)")
    parser.add_argument("--level", default="simple",
                        help="how to explain, as the app tells it (learner.py): none (no line), simple, technical, "
                             "technical+hints")
    args = parser.parse_args()
    load_keys()
    tools = registry().prompt_section(args.menu)
    learner = level_line(args.level)
    prepared = prepare(FRAME)
    rows = []
    for variant, _ in [(v, r) for v in variant_names(args.variants) for r in range(args.repeats)]:
        vision = Variant(make_vision(VISION[args.model].spec), variant)
        for text, expected, *more in [c for c in CASES if args.only.lower() in c[0].lower()]:
            extra = more[0] if more else {}
            # where the brain's scene table has the level line (brain/context.py)
            scene = SCENES.get(extra.get("scene"), SCENE) + (f"\n{learner}" if learner else "")
            prompt = with_tools(ASK, tools, scene, " (selected: o1)").format(context="", history=extra.get("history", "(none)"),
                                                                                   question=text)
            try:  # a call that never returns is that case's error, not the bench's end (2026-10-11: one hung 20 min)
                result = await asyncio.wait_for(vision.ask([prepared["clean"]], prompt), timeout=CALL_TIMEOUT_S)
            except asyncio.TimeoutError:
                result = SimpleNamespace(data={}, latency_ms=CALL_TIMEOUT_S * 1000, error=f"no reply in {CALL_TIMEOUT_S:g} s")
            data = result.data or {}
            used = [a.get("tool") for a in data.get("actions") or [] if isinstance(a, dict)]
            used += ["start_researcher:hands" for a in data.get("actions") or [] if isinstance(a, dict)
                     and a.get("tool") == "start_researcher" and str((a.get("args") or {}).get("effort") or "quick") in HANDS]
            if used.count("zoom") >= 2:
                used.append("zoom×2")  # two magnifier windows, one on each (independent windows since 2026-10-11)
            used += ["find:concept" for a in data.get("actions") or [] if isinstance(a, dict)
                     and a.get("tool") == "find" and (a.get("args") or {}).get("by") == "concept"]
            if data.get("targets"):
                used.append("targets")  # the look pointed with its own boxes
            accepted = expected if isinstance(expected, tuple) else (expected,)
            right = any(e in used for e in accepted) if expected else ("start_researcher" not in used)
            # A tool that must not be used for it ("try the other model" switching the default for good).
            right = right and not any(f in used for f in extra.get("forbid", ()))
            # A plain question that started a researcher nobody asked for (pointing is allowed).
            unasked = expected is None and "start_researcher" in used
            rows.append({"variant": variant, "text": text, "scene": extra.get("scene", "default"),
                         "expected": expected if expected else "none", "used": used,
                         "right": right, "unasked": unasked, "ms": result.latency_ms,
                         "error": result.error, "answer": (data.get("answer") or "")[:120]})
            print(f"{'ok ' if right else 'BAD'} {text[:60]:60} -> {used}", flush=True)
    ms = [r["ms"] for r in rows if not r["error"]]
    by_tool = {}
    for r in rows:
        key = r["expected"] if isinstance(r["expected"], str) else "/".join(r["expected"])
        ok, n = by_tool.get(key, (0, 0))
        by_tool[key] = (ok + r["right"], n + 1)
    summary = {"menu": args.menu, "level": args.level, "toolChars": len(tools), "right": f"{sum(r['right'] for r in rows)}/{len(rows)}", "p50Ms": round(float(np.median(ms))) if ms else None,
               "byTool": {k: f"{a}/{b}" for k, (a, b) in by_tool.items()}, "hygiene": hygiene(rows)}
    print(json.dumps(summary, indent=2))
    if args.only:
        return  # a subset never replaces the whole bench's results
    (BENCH / "results" / f"tool-choice-{args.model}{'' if args.menu == 'flat' else '-' + args.menu}.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
