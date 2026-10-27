"""Bench hygiene shared by the decision benches (fast_bench, tool_choice; roadmap W0-7):
- prompt variants: the same prompt with its formatting changed (line wraps reflowed, or framed
  by one neutral line). A model whose calls change with these is fragile to wording (AILA, 2025:
  instruction-following broke with prompt structure). They are not paraphrases of meaning.
- repeats: the same case several times; consistency is how often every repeat agreed.
- unasked actions: acting when the case expected no action (a remark, a plain question, a
  case that needs a look), counted on its own (AILA's "sleepwalking").
- tags: what each case's right answer rests on: act (a tool now), notes (answer from notes),
  vision (needs a look), remark (nothing to do).
"""
from __future__ import annotations

import re
from collections import defaultdict


def _reflow(prompt: str) -> str:
    """Join wrapped lines inside paragraphs (keeps blank lines, list items and the JSON)."""
    out = []
    for block in prompt.split("\n\n"):
        lines = block.split("\n")
        if any(line.lstrip().startswith(("{", "-", "*", "<")) for line in lines):
            out.append(block)
        else:
            out.append(" ".join(line.strip() for line in lines))
    return "\n\n".join(out)


VARIANTS = {
    "as-is": lambda prompt: prompt,
    "reflowed": _reflow,
    "framed": lambda prompt: "The request below comes from a person at a microscope.\n\n" + prompt,
}


class Variant:
    """A vision client whose prompts are rewritten by one variant before they are sent."""

    def __init__(self, vision, name: str) -> None:
        self.vision, self.name, self.transform = vision, name, VARIANTS[name]

    async def ask(self, images, prompt, *args, **kwargs):
        return await self.vision.ask(images, self.transform(prompt), *args, **kwargs)


def tag(expected) -> str:
    if expected == "none" or expected is None:
        return "remark"
    if expected == "answer":
        return "notes"
    if expected == "look":
        return "vision"
    return "act"


def case_key(expected) -> str:
    return "/".join(expected) if isinstance(expected, tuple) else str(expected)


def summarise(rows: list[dict]) -> dict:
    """Rows: {variant, text, expected, used (tool names), right, unasked (it acted when the case
    asked for no action; each bench decides, see there)}. Per variant: right, unasked actions,
    consistency across repeats, accuracy per tag; and the spread between variants."""
    out: dict = {}
    by_variant = defaultdict(list)
    for r in rows:
        by_variant[r.get("variant", "as-is")].append(r)
    for variant, vrows in by_variant.items():
        no_action = [r for r in vrows if tag(r["expected"]) in ("remark", "notes", "vision")]
        unasked = [r for r in no_action if r.get("unasked")]
        per_case = defaultdict(list)
        per_tag = defaultdict(lambda: [0, 0])
        for r in vrows:
            per_case[(r["text"], r.get("scene"))].append(r["right"])  # the same words on another board are another case
            per_tag[tag(r["expected"])][0] += bool(r["right"])
            per_tag[tag(r["expected"])][1] += 1
        repeated = [v for v in per_case.values() if len(v) > 1]
        out[variant] = {
            "right": round(sum(bool(r["right"]) for r in vrows) / len(vrows), 3),
            "n": len(vrows),
            "unasked": round(len(unasked) / len(no_action), 3) if no_action else None,
            "unaskedCases": sorted({r["text"] for r in unasked}),
            "consistency": round(sum(1 for v in repeated if len(set(v)) == 1) / len(repeated), 3) if repeated else None,
            "byTag": {k: f"{a}/{b}" for k, (a, b) in sorted(per_tag.items())},
        }
    rights = [v["right"] for v in out.values()]
    if len(rights) > 1:
        out["spreadBetweenVariants"] = round(max(rights) - min(rights), 3)
    return out


def variant_names(arg: str) -> list[str]:
    names = [n for n in re.split(r"[,\s]+", arg or "") if n]
    unknown = [n for n in names if n not in VARIANTS]
    if unknown:
        raise SystemExit(f"unknown prompt variants: {', '.join(unknown)} (one of {', '.join(VARIANTS)})")
    return names or ["as-is"]
