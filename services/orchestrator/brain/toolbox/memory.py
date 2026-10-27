"""Memory of the view: "have we seen this before today?" (family "memory").

About an outlined thing, while option SEEN is on and outlines were named earlier today
(orchestrator/seen.py): seen_before lists the earlier outlines that look most like it (picture
similarity of option EMBED's model, from its list floor, seen.list_floor), with when, in which conversation and what they were named, in
the past tense, and the numbers' measured meaning; no model call. The brain judges what they mean.

About the whole view, or when no named outline is alike: the app's running look saves a small
picture of each view it describes (services/logs/crops/<day>/, its log record carries the picture
and what it saw). seen_before shows the vision model the thing or view now beside a numbered sheet
of today's earlier views (one per stretch of the same specimen, at most MOST spread over the day),
and the model says which show the same kind of thing. The model judges the likeness; the times and
what was seen then come from the log.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from ..registry import Registry, Tool, ToolContext, ToolFailed, schema
from .common import OBJECT, describe, resolve_object

MOST = 20
THUMB = 200
SKIP_RECENT_S = 60.0

SEEN = """Image 1 is {now}, from a live microscope view now. Image 2 is a sheet of {n} numbered pictures
of the view earlier today, each labelled with its time.
The user asks: "{question}"
Which numbered pictures show the same kind of thing as image 1 (the same kind of cell, organism or
structure, not just the same colour or background)? Say none if none do.
Reply with JSON only: {{"same": [numbers], "answer": "1-2 plain sentences"}}"""


def earlier_views(log_path: Path, day: str, now: datetime, most: int = MOST) -> list[dict]:
    """Today's views the running look saved, one per stretch of the same specimen, oldest first;
    at most `most`, spread evenly over the day (the first and the latest always among them)."""
    runs: list[dict] = []
    try:
        lines = log_path.read_text().splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        image = r.get("image")
        if r.get("kind") != "sight" or not isinstance(image, str) or not image.startswith(f"crops/{day}/"):
            continue
        try:
            at = datetime.fromisoformat(r["at"])
        except (KeyError, ValueError):
            continue
        if (now - at).total_seconds() < SKIP_RECENT_S:
            continue
        specimen = str(r.get("specimen") or "")
        if runs and runs[-1]["specimen"] == specimen:
            continue  # the same stretch: its first picture stands for it
        runs.append({"at": at, "specimen": specimen, "things": r.get("things") or [], "path": log_path.parent / image})
    runs = [v for v in runs if v["path"].exists()]
    if len(runs) <= most:
        return runs
    picks = sorted({round(i * (len(runs) - 1) / (most - 1)) for i in range(most)})
    return [runs[i] for i in picks]


def sheet(views: list[dict]) -> bytes:
    """The views as a numbered grid, 5 across, each with its time."""
    tiles = []
    for i, v in enumerate(views, 1):
        img = cv2.imread(str(v["path"]))
        if img is None:
            img = np.zeros((THUMB, THUMB, 3), np.uint8)
        h, w = img.shape[:2]
        k = THUMB / max(h, w)
        img = cv2.resize(img, (max(1, int(w * k)), max(1, int(h * k))))
        tile = np.zeros((THUMB + 24, THUMB, 3), np.uint8)
        tile[24:24 + img.shape[0], :img.shape[1]] = img
        cv2.putText(tile, f"{i}  {v['at']:%H:%M}", (6, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(tile)
    while len(tiles) % 5:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + 5]) for i in range(0, len(tiles), 5)]
    ok, jpeg = cv2.imencode(".jpg", np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])
    return jpeg.tobytes()


async def outlines_alike(seen, obj) -> tuple[str, str]:
    """(what the seen_before tool says from the outlines named earlier today, "" when it cannot
    say anything from them; a sentence to put before the views comparison when none is alike)."""
    from ...seen import list_floor, when
    from ...untrusted import inline

    if seen is None or not seen.on() or not seen.earlier():
        return "", ""
    try:
        found, closest = await asyncio.wait_for(seen.alike(obj.id), 10.0)
    except Exception:
        return "", ""  # its picture is gone, or no worker: the views comparison answers
    if found:
        return seen.listing(found, describe(obj)), ""  # each with its session when another (ADR 0017)
    if closest is None:
        return "", ""
    floor = list_floor(closest.kept.model)
    from ...options import EMBED

    named = getattr(EMBED.get(closest.kept.model), "label", None) or closest.kept.model  # its name, not its id (the review)
    bar = (f"below {floor:.2f}" if floor != float("inf") else
           f"and {named} has no measured threshold here yet, so nothing is called alike by it")
    return "", (f'No outline named earlier today looks much like {describe(obj)} (the closest, "{inline(closest.kept.label, 60)}" '
                f"at {when(closest.kept.at)}, scored {closest.similarity:.2f}, {bar}). ")


def add_memory_tools(registry: Registry, services) -> None:
    hub, agent, log_path, day = services.hub, services.agent, services.history_log, services.day
    if agent is None or log_path is None:
        return

    async def seen_before(ctx: ToolContext, args: dict) -> str:
        from ...magnifier import crop_jpeg
        from ...marks import encode_crop

        obj = resolve_object(hub, args.get("object"), ctx) if args.get("object") else None
        before = ""
        if obj is not None:
            # The outlines named earlier today, by their pictures' likeness (seen.py): facts, no model call.
            said, before = await outlines_alike(services.seen, obj)
            if said:
                return said
        today = getattr(day, "date", None) or datetime.now().strftime("%Y-%m-%d")
        views = await asyncio.to_thread(earlier_views, Path(log_path), today, datetime.now())
        if not views:
            if before:
                return before + "There are no earlier views from today to compare the view with."
            raise ToolFailed("there are no earlier views from today to compare with yet",
                             next="ask again after looking at a few more views")
        if obj is not None:
            got = await asyncio.to_thread(crop_jpeg, hub, obj, 0.25, 90, None)
            if got is None:
                raise ToolFailed("it has no recent clear frame", next="hold the slide still for a second, then ask again")
            now_jpeg, now_words = got[0], f"a close-up of {describe(obj)}"
        else:
            info, image, crop = agent.snapshot(None)
            now_jpeg, _ = await asyncio.to_thread(encode_crop, image, crop, 768, 85)
            now_words = "the whole view"
        grid = await asyncio.to_thread(sheet, views)
        prompt = SEEN.format(now=now_words, n=len(views), question=ctx.question or "Have we seen this before today?")
        from ..dispatch import query_answer

        option = hub.settings.vision
        result = await query_answer(agent.vision(option), option, [now_jpeg, grid], prompt, max_output_tokens=300)
        data = result.data or {}
        same = [int(n) for n in data.get("same") or [] if str(n).isdigit() and 1 <= int(n) <= len(views)]
        if not data.get("answer"):
            raise ToolFailed(f"the vision model gave no answer ({result.error or 'empty'})", next="ask again in a moment")
        when = "; ".join(f"{views[n - 1]['at']:%H:%M} ({str(views[n - 1]['specimen'])[:60] or 'no description'})"
                         for n in same[:6]) or "none of today's earlier views"
        return (f"{before}Compared with {len(views)} earlier views from today, the vision model judged alike: {when}. "
                f"It said: {str(data['answer'])[:300]}")

    registry.add(Tool("seen_before", "Have we seen this before today? With an outlined object: the outlines named earlier "
                      "today that look most like it, with when, in which conversation and their names. Without one, or "
                      "when none is alike: the vision model compares the view now with today's earlier views and says "
                      "which show the same kind of thing, with their times (\"have we seen this before?\", \"where did I "
                      "see one like this?\", \"is this like what we looked at earlier?\").",
                      schema({"object": OBJECT}), seen_before, kind="query", inline_ms=8000, family="memory", outline_refs=("object",)))
