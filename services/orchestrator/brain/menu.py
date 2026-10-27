"""The menu: every tool, grouped into families, shown to the model the way the MENU option says.

The model always sees every tool and picks one itself; no word list picks a family for it
(the user's direction, 2026-10-06). What the options change is only the layout:
- "flat": every tool in full, in the order they were added (as before the menu);
- "families": the same, under a one-line heading per family;
- "compact": grouped, each tool with its arguments and only the first sentence of its
  description (a smaller prompt; the tool-choice bench says whether the model still picks right);
- "brief": as "flat", with the longest descriptions said briefly (BRIEFS); every other tool in full.

A family is where a tool sits for the model and for the voice ("what can you do?"), declared
by the tool (`Tool.family`). Design: docs/design/2026-10-06-capability-menu.md.
"""
from __future__ import annotations

import json
import re

# Families in the order the model sees them, each with what it is for, in a few words.
FAMILIES: dict[str, str] = {
    "scene": "outline, select, name and clear things in the view",
    "finding": "find things by name or look, inside an outline, every one like this one; break one into parts",
    "magnifier": "magnify, hold, compare side by side, check the focus",
    "measuring": "count, measure sizes, set the objective and the scale",
    "motion": "watch what moves, how fast and how straight",
    "map": "where things outlined earlier are from the view now; the slide map",
    "seeing": "a clearer picture from real frames: averaged, the sharpest, focus-stacked",
    "snapshots": "a picture of the view with its outlines and labels, saved",
    "time": "what happened in the last minute; watch for something to happen and say when",
    "memory": "have we seen this before today",
    "display": "how the app draws things",
    "conversation": "name the specimen, conversations, recall earlier, let the user lead, explain simply or technically (hints first), suggest what next",
    "researchers": "background researchers: pictures, videos, lessons, research; follow-ups, notes, new versions",
    "results": "show, read, step through and close a researcher's work",
    "library": "your recordings and pictures (All assets): find one, put one on stage in this session",
    "volumes": "whole-brain volumes the user added: register one to the Allen mouse atlas (brainreg)",
}
OTHER = "other"
MENUS = ("flat", "families", "compact", "brief")

# The "brief" menu (Phase 2 (a), 2026-10-11): the seven longest tool descriptions (1.4-2.9k characters each, grown
# by a sentence per failure since 2026-10-07) said in a few lines, keeping what the tool does and the contracts its
# arguments need; the arguments themselves are unchanged. Compared with "flat" on the acting bench's live cases.
BRIEFS: dict[str, str] = {
    "zoom": ('Open a live magnifier window on an object (each object its own window; others stay), or change how close an '
             'open one looks. level: "fit" (the whole object: "up close", "in HD"), a number (x closer than fit), "more", '
             '"less" or "max". It never refuses: past the camera\'s detail it is pixel-enlarged. It outlines nothing.'),
    "find": ('Find and outline things by name or look, in the view or inside an outlined or seen thing (inside=: "the '
             'nucleus in that cell", "the pore in the stoma"); use it to point out a part (zoom outlines nothing). all: '
             'every one or one; like=: every thing like this outline; by="cells" for many cells of a smear.'),
    "present": ('Show, hide, fade, colour, emphasise, open, close, move or resize what is on screen: the cells and tracks '
                'layers, the magnifier, and saved work by its exact resource ID (opened as it was, never made again). '
                'Use an operation the target takes; what="@N" is what step N made.'),
    "reference_photo": ('Fetch a real photo from the web into its own window, with its source and licence: a reference, '
                        'not this slide ("show me a real picture of a stoma"). Keep the user\'s subject and imaging method '
                        'in subject and search_terms. You have not seen the photo: say nothing of its details. n: another one.'),
    "start_researcher": ('Start a background researcher for longer work while you keep talking: research, comparison, '
                         'confirmation, or a report, illustration, video or lesson the user asked for (asked_with: their '
                         'words). effort: instant 15 s, quick 75 s, standard 6 min, deep 18 min (deep only when asked). '
                         'For a real photo use reference_photo.'),
    "query_population": ('Facts about a population of segmented things (cells, matches): the count, medians, members by '
                         'measured ranges (where), sorted (by, order, top), outliers, those worth a second look (review). '
                         'It never segments; of="@N" is what step N made. Largest or smallest: by with top and order.'),
    "capabilities": ('What can be done here: the app\'s tools and models, what is available and what is known to fail, as '
                     'the user\'s Capabilities page shows them. For "what can we do?", ideas or "can you...": a few concrete '
                     'things for this specimen. It runs nothing itself.'),
}

_SENTENCE = re.compile(r"(?<!e\.g\.)(?<!i\.e\.)(?<=[.!?])\s+")


def first_sentence(text: str) -> str:
    return _SENTENCE.split(text.strip(), maxsplit=1)[0]


def line(tool, short: bool = False, brief: str | None = None, listed: str = "") -> str:
    """One tool as the model reads it. `listed`: the menu's lists of choices with their names ("id = name"): an
    argument whose choices are all there is not given them again (the models are listed once, on `use`)."""
    props = tool.params.get("properties", {})
    required = set(tool.params.get("required", []))

    listed = (tool.params.get('description') or '') + listed

    def argument(k, value):
        detail = value.get('description') or value.get('type', '')
        # A list's own items may be a fixed list too (compare_models' model ids, revise_work's fields).
        choices = value.get('enum', (value.get('items') or {}).get('enum') if value.get('type') == 'array' else None)
        if choices is not None and not all(f"{c} = " in listed for c in choices):  # listed with their names below: once
            detail += (' choices=' if 'enum' in value else ' each of=') + json.dumps(choices, ensure_ascii=False, separators=(',', ':'))
        return f"{k}{'' if k in required else '?'}: {detail}"

    args = ", ".join(argument(k, v) for k, v in props.items())
    text = f'- "{tool.name}"({args}): {brief or (first_sentence(tool.description) if short else tool.description)}'
    # Schema annotations describe legal syntax in both FC and JSON-actions.
    # Compact layout may shorten prose, never drop the target/argument contract.
    if tool.params.get('description'):
        text += '\n  ' + tool.params['description']
    for example in tool.params.get('examples', []):
        text += '\n  args example: ' + json.dumps(example, ensure_ascii=False, separators=(',', ':'))
    return text


def grouped(tools: list) -> list[tuple[str, list]]:
    """(family, its tools) in the menu's order; tools of no known family come last."""
    out = []
    for family in [*FAMILIES, OTHER]:
        members = [t for t in tools if (t.family if t.family in FAMILIES else OTHER) == family]
        if members:
            out.append((family, members))
    return out


def heading(family: str) -> str:
    return f"{family.capitalize()}: {FAMILIES[family]}" if family in FAMILIES else "Other"


def render(tools: list, menu: str = "flat") -> str:
    listed = " ".join(t.params.get("description") or "" for t in tools)  # every list of choices given with names
    if menu == "brief":
        return "\n".join(line(t, brief=BRIEFS.get(t.name), listed=listed) for t in tools)
    if menu not in ("families", "compact"):
        return "\n".join(line(t, listed=listed) for t in tools)
    blocks = []
    for family, members in grouped(tools):
        blocks.append("\n".join([heading(family), *(line(t, short=menu == "compact", listed=listed) for t in members)]))
    return "\n\n".join(blocks)


def voice_abilities(tools: list) -> str:
    """What the voice is told the app can do: per family, the first sentence of each tool."""
    out = []
    for family, members in grouped(tools):
        out.append(heading(family))
        for t in members:
            first = first_sentence(t.description).rstrip(".")
            out.append(f"- {first[:1].lower() + first[1:]}")
    return "\n".join(out)
