"""Tools that find things in the view: by name or look, anywhere or inside an outlined object,
one or every one (orchestrator/finder)."""
from __future__ import annotations

from ... import tools
from ..registry import Registry, Tool, ToolContext, ToolFailed, schema
from .common import OBJECT

BY = ("look", "cells", "concept")  # how `find` finds things (orchestrator/finder): a fixed list


def add_finding_tools(registry: Registry, services) -> None:
    hub, agent = services.hub, services.agent
    if agent is None or not hasattr(agent, "look_in"):
        return

    async def find(ctx: ToolContext, args: dict) -> str:
        from ...finder import Finder, FindRef, describe_found

        what = str(args.get("what") or "").strip()
        if not what:
            raise ToolFailed("find what?", next=None)  # no next: the model's own call; it corrects it
        like = str(args["like"]).strip() if args.get("like") else None
        by = str(args.get("by") or "look").strip().lower()
        if by not in BY:  # never quietly "look" in its place (live 2026-10-11: a way that is not one was found by looking)
            raise ToolFailed(f"there is no way to find things called {args.get('by')!r}; by is one of {', '.join(BY)}",
                             next="pick one of these and try again")
        ref = FindRef(what, inside=str(args["inside"]).strip() if args.get("inside") else None,
                      quantity=("all" if like or by == "concept" else
                                "all" if args.get("all") is True else "one" if args.get("all") is False else "requested"),
                      like=like, by=by)
        try:
            found = await Finder(hub, agent, services.board, services.segment_all).find(ref, ctx)
        except tools.ToolError as exc:
            raise ToolFailed(str(exc), next=exc.next) from exc
        summary = describe_found(found, ref)
        keep = {o.id for o in found.objects} | set(ctx.objects)
        if found.container is not None:
            keep.add(found.container.id)  # what was searched in stays
        replace_container = (args.get("keep_container") is False and bool(found.objects)
                             and found.container is not None
                             and all(o is not found.container for o in found.objects))
        if not args.get("keep") or replace_container:
            # Like highlight: the app's earlier highlights go, unless the user says "also". Requests
            # still running from before this one may no longer add theirs (ticket.py); this one may.
            from ...ticket import current

            request = current()
            if request is None or not request.superseded("outline:app"):
                hub.scene.generations.bump("outline:app", why="a newer request replaced the app's outlines", by=request)
                for o in list(hub.scene.objects.values()):
                    remove = (not args.get("keep") and o.id not in keep) or (replace_container and o is found.container)
                    if o.kind == "highlight" and o.trial is None and o.source.startswith("ai") and remove:
                        hub.scene.remove(o.id)
                        if o is found.container:
                            summary += f"; removed the prior app outline {o.id}"
        from ..registry import CURRENT_RUN, current_step, record_made
        from .common import record_outline
        number, steps = current_step(ctx)
        # A cell-finder already publishes its population. Ordinary finding
        # publishes its actual outlines so later tools never need to guess IDs.
        owns_population = bool(steps.produced.get(number))
        for o in found.objects:
            if o.id not in ctx.objects:
                ctx.objects.append(o.id)
            if CURRENT_RUN.get() is not None and o.trial is None:
                record_outline(ctx, o)  # the six facts the day's journal keeps, as highlight files them
                if not owns_population:
                    record_made(ctx, o.id)
        from .present import reshow_outlines

        # Never "outlined" while the outlines are hidden; nothing found leaves them as they are (re-check note).
        return summary + (await reshow_outlines(services, ctx) if found.objects else "")

    registry.add(Tool("find", "Find and outline things by name or by how they look, in the view or inside an "
                      "outlined object (\"the nucleus inside that cell\", \"every red cell\", \"point out the pore in "
                      "the stoma\"). Use it whenever the user asks to point out, find or show a part inside an outlined "
                      "object (zoom only magnifies; it outlines nothing). Inside an object it looks at a close-up of it "
                      "and keeps that object by default. Set keep_container=false when the user wants only the found part, "
                      "replacing its prior app-drawn containing outline after finding it; user-drawn outlines always stay. "
                      "An inside=sN search uses that seen region without outlining its container. "
                      "all=true outlines every one; all=false selects one; omit all to follow the quantity in the user's request. "
                      "like=<an outline> outlines every thing that looks like it; "
                      "keep=true keeps your earlier highlights. by=\"cells\": a cell finder outlines every cell and you pick which "
                      "are the ones asked for; best for many cells of a smear or tissue (red or white blood cells, cheek "
                      "cells), not for tiny things like platelets or bacteria (blood smear bench: every red cell 0.90 found this way against 0.60 by looking). "
                      "It sees the camera's picture only, never the outlines the app draws (by their colour: highlight where).",
                      schema({"what": {"type": "string", "description": "what to find (for \"concept\": the thing's name alone)"},
                              "inside": OBJECT,
                              "like": {"type": "string", "description": 'every thing like this outlined one ("find all like '
                                                                        'this", "the others like it"): object id or "that"'},
                              "all": {"type": "boolean", "description": "true: every match; false: one; omitted: the vision model follows the quantity in the original request"},
                              "keep": {"type": "boolean", "description": "keep earlier highlights too"},
                              "keep_container": {"type": "boolean", "description": "default true; false replaces only the app-drawn inside outline after a successful find, preserving user outlines"},
                              "by": {"type": "string", "enum": list(BY),
                                     "description": '"look" (default: the app\'s eyes find the thing meant and its segmenter outlines it, as a click does; '
                                                    'what "segment that" or "use a segmentation model on it" means for one thing), "cells", '
                                                    'or "concept" (SAM 3.1 finds every match of a kind named by its name; on these slides '
                                                    'words often find nothing and an example works better)'}}, ["what"]),
                      find, inline_ms=3500, family="finding", outline_refs=("like",)))
