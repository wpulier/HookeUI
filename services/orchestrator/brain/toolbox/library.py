"""Tools on the library (All assets; docs/design/2026-10-12-workspace-and-assets.md): find one of the user's recordings
or pictures, and put one on stage in this session as the user's own Add does ("show me last week's rotifer recording",
"add the leaf picture to this session"). Adding associates (ADR 0022's amendment): what is put on stage is the
session's from then on. Searching touches no session."""
from __future__ import annotations

import asyncio
import time

from ...untrusted import inline
from ..registry import Registry, Tool, ToolContext, ToolFailed, schema

LISTED = 12  # at most this many assets said; how many more is said too
CATEGORY_WORDS = ("Plant tissue", "Blood", "Human & animal tissue", "Invertebrates", "Protists & algae", "Bacteria & fungi",
                  "Other", "Not sure yet")


def _line(asset, here: set[str]) -> str:
    """One asset as the agent reads it: its id, title, kind and when, its category, and whether this session has it."""
    meta = asset.metadata()
    kind = "a picture" if asset.kind == "still" else "a recording"
    when = meta.get("captured") or ""
    length = f", {int(asset.seconds // 60)}:{int(asset.seconds % 60):02d}" if asset.kind == "recording" and asset.seconds else ""
    category = meta.get("category") or {}
    filed = (f"{category['group']} › {category['name']}" if category.get("name") else category.get("group")) if category else "not looked at yet"
    whose = "an example" if asset.group == "example" else "yours"
    return (f'{asset.id}: "{inline(asset.title_now(), 80)}", {kind}{f", {when}" if when else ""}{length}; {filed}; {whose}'
            + ("; in this session" if asset.id in here else ""))


def add_library_tools(registry: Registry, services) -> None:
    library = getattr(services.hub, "library", None)
    if library is None or services.put_on_stage is None:
        return
    day = services.day

    def in_session() -> set[str]:
        from ...day.catalogue import opened_sources

        return set(opened_sources(day)) if day is not None else set()

    async def assets(ctx: ToolContext, args: dict) -> str:
        words = str(args.get("words") or "")[:200]
        kind = args.get("kind") if args.get("kind") in ("recording", "still") else None
        whose = "example" if args.get("whose") == "examples" else "mine"
        category = str(args.get("category") or "")
        if category and category not in CATEGORY_WORDS:
            raise ToolFailed(f"a category is one of {', '.join(CATEGORY_WORDS)}", next="pick one of these and try again")
        found = await asyncio.to_thread(library.search, words, kind, False, whose)
        if category:
            found = [a for a in found if ((a.category() or {}).get("group") or "Not sure yet") == category]
        days = args.get("within_days")
        if isinstance(days, (int, float)) and days > 0:
            since = time.time() - float(days) * 86400
            found = [a for a in found if (a.at or 0.0) >= since]
        here = in_session()
        where = "your assets" if whose == "mine" else "the examples"
        if not found:
            return f"nothing in {where} matches" + (f' "{inline(words, 60)}"' if words else "") + "; nothing was put on stage"
        lines = [_line(a, here) for a in found[:LISTED]]
        more = f"; and {len(found) - LISTED} more (narrower words find them)" if len(found) > LISTED else ""
        return f"{len(found)} in {where}, newest first (nothing was put on stage): " + " / ".join(lines) + more

    registry.add(Tool(
        "assets", "Look in the user's library (All assets: their own recordings and pictures, and the examples) when they "
        "ask about one or want to see one (\"show me last week's rotifer recording\", \"which pictures of blood do I "
        "have?\"). By words (title, specimen, what it shows), kind, category, whose and how recent. Lists ids (a7) newest "
        "first, with whether this session has each; puts nothing on stage (add_asset does, when the user wants to look at "
        "one).",
        schema({"words": {"type": "string", "description": "what to look for: its title, specimen or what it shows"},
                "kind": {"type": "string", "enum": ["recording", "still"], "description": "a recording or a picture (still)"},
                "category": {"type": "string", "enum": list(CATEGORY_WORDS)},
                "whose": {"type": "string", "enum": ["yours", "examples"], "description": "yours (the default) or the public examples"},
                "within_days": {"type": "number", "description": "only those captured in the last this many days (last week: 7)"}}),
        assets, kind="query", inline_ms=1500, family="library"))

    async def add_asset(ctx: ToolContext, args: dict) -> str:
        from ...library import is_asset_id

        aid = str(args.get("asset") or "").strip()
        if not is_asset_id(aid):
            raise ToolFailed("asset is an asset's id (a7) from assets", next="find it with assets first")
        reply = await services.put_on_stage(aid)
        title = reply.get("title") or aid
        return (f'put "{inline(title, 80)}" ({aid}) on stage, held at its start; it is one of this session\'s assets now '
                "(the live camera comes back with Live camera in the header, or by asking)")

    registry.add(Tool(
        "add_asset", "Put one of the user's recordings or pictures on stage in this session, as their own Add does, when "
        "they ask to look at it (\"show me that rotifer recording\", \"put the leaf picture up\"): by its id from assets. "
        "It becomes one of this session's assets and plays held at its start; the live camera leaves the stage. Never on "
        "your own.",
        schema({"asset": {"type": "string", "description": "its id (a7), from assets"}}, ["asset"]),
        add_asset, inline_ms=4000, family="library"))
