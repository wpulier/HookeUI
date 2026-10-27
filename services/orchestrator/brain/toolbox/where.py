"""Where things are on the slide (orchestrator/slidemap.py, roadmap W5-4): where an outline is
from the view now ("where was the stoma?"), and the slide map beside the view. Positions come
from the slide's measured movement, never from a model; across a lost epoch they are not told."""
from __future__ import annotations

import time

from ...slidemap import place, place_words
from ..registry import Registry, Tool, ToolContext, ToolFailed, actor_of, schema
from .common import OBJECT, describe, log_measurement, resolve_object

MOST = 4  # outlines listed when a name matches several


def add_where_tools(registry: Registry, services) -> None:
    hub, slidemap = services.hub, services.slidemap
    if getattr(hub, "scene", None) is None:
        return

    def matching(ref) -> list:
        """Every outline a name means ("the stomata"), newest first; [] when `ref` is not a name
        of several (an id, "that", one match: the board resolves it)."""
        key = str(ref or "").strip().lower()
        if not key:
            return []
        found = [o for o in hub.scene.objects.values()
                 if o.trial is None and o.kind in ("highlight", "part") and o.label
                 and (o.label.lower() == key or key in o.label.lower())]
        return sorted(found, key=lambda o: -o.created_at) if len(found) > 1 else []

    def where(obj, info, um_per_px) -> str:
        name = f"{describe(obj)} ({obj.id})"
        p = place(obj, info)
        if p is None:
            reason = ("the current frame's slide movement is not registered"
                      if getattr(info, "tracked", None) is False else
                      "the outlined view and current view have no measured motion relation")
            return f"{name}: can't tell from here: {reason}, so its position is not known against this view"
        return f"{name} is {place_words(p, um_per_px)}"

    async def where_is(ctx: ToolContext, args: dict) -> str:
        info = hub.latest_info
        if info is None or getattr(info, "fov", None) is None:
            raise ToolFailed("I can't see the eyepiece circle right now, so I can't say where things are from here",
                             next="make sure the eyepiece circle is in view and lit, then ask again")
        reading = hub.scale_reading() if hasattr(hub, "scale_reading") else None
        um = reading.um_per_px if reading is not None else None
        ref = args.get("object") or args.get("thing")
        several = matching(ref)
        if several:
            known = [o for o in several if place(o, info) is not None]
            known.sort(key=lambda o: place(o, info).fields)
            unknown = [o for o in several if o not in known]
            shown = (known + unknown)[:MOST]
            lines = [where(o, info, um) for o in shown]
            more = len(several) - len(lines)
            said = "; ".join(lines) + (f"; and {more} more" if more > 0 else "")
            several = shown
        else:
            several = [resolve_object(hub, ref, ctx)]
            said = where(several[0], info, um)
        places = []
        for obj in several[:MOST]:
            p = place(obj, info)
            places.append({"objectId": obj.id, "label": obj.label or None, "known": p is not None,
                           **({} if p is None else {"dxPx": round(p.dx, 1), "dyPx": round(p.dy, 1), "fields": round(p.fields, 3),
                                                    "inView": p.in_view,
                                                    "um": round((p.dx ** 2 + p.dy ** 2) ** 0.5 * um, 2) if um else None})})
        known = any(row["known"] for row in places)
        log_measurement(services, ctx, "place", places=places,
                        how=("from the eyepiece centre now, by how far the slide moved since it was outlined (px in the picture)"
                             if known else "current-relative positions unavailable"))
        return said + (". Available positions are measured from how far the slide has moved since; directions are as seen in the picture."
                       if known else "")

    registry.add(Tool("where_is", "Where something outlined earlier is now, from the view: how far (in fields of view, "
                      "and µm when the scale is known) and which way in the picture, measured by the app from how far "
                      "the slide has moved since it was outlined (\"where was the stoma?\", \"how do I get back to it?\"). "
                      "A name that several outlines share lists each, nearest first. Says so when the slide's movement "
                      "was lost since and the position cannot be told. Never guess a position yourself.",
                      schema({"object": OBJECT}, ["object"]), where_is, kind="query", inline_ms=300, family="map", outline_refs=("object",)))

    bookmarks = getattr(services, "bookmarks", None)
    if bookmarks is not None:
        async def bookmark(ctx: ToolContext, args: dict) -> str:
            import asyncio

            from ...capture import captures

            info = hub.latest_info
            capture = captures(hub).get(info.id) if info is not None else None  # the view now, taken before anything waits
            if capture is None:
                raise ToolFailed("there is no fresh view to keep", next="wait for the view to come back, then ask again")
            subject = (getattr(hub.scene, "context", None) or {}).get("specimen")
            scale = getattr(hub, "scale", None)
            asked = getattr(ctx, "asked", None)  # the conversation it was asked in (review S1), only while it is today's
            current = getattr(getattr(services, "day", None), "current", None)
            thread = (asked.thread if asked is not None and getattr(asked, "thread", None)
                      and (getattr(asked, "session", None) or current is None  # a session's conversations keep their ids
                           or current(getattr(asked, "routing_generation", getattr(asked, "generation", None)))) else None)
            try:  # the picture is decoded and the files written off the loop, inside the memory bound (review B4)
                mark = await asyncio.to_thread(bookmarks.add, capture, note=str(args.get("note") or ""), thread=thread,
                                               subject=subject, objective=getattr(scale, "objective", None), hub=hub,
                                               asked=ctx.question, session=getattr(asked, "session", None),
                                               actor=actor_of(ctx))
            except ValueError as exc:
                raise ToolFailed(str(exc), next="hold the slide still with the eyepiece circle in view, then ask again") from None
            bookmarks.journal(mark)  # in the session it was asked in (ADR 0017), back on the loop
            from ..registry import attach

            attach(values={"bookmark": mark["id"]})
            keep = getattr(services, "keep", None)  # a dataset being collected gets the picture itself (review B2)
            if keep is not None and mark.get("picture"):
                try:
                    keep(bookmarks.folder() / mark["picture"], f"bookmark-{bookmarks.today()}-{mark['picture']}")
                except Exception:
                    pass
            log = getattr(services, "log", None)
            if log is not None:  # the place as kept, nested (its own "at" never overwrites the log's; review B1)
                try:
                    log({"kind": "bookmark", "channel": ctx.channel, "turnId": ctx.turn_id, "bookmark": mark})
                except Exception:
                    pass
            named = f" as \"{mark['note']}\"" if mark.get("note") else ""
            missing = f" ({mark['pictureMissing']})" if mark.get("pictureMissing") else ""  # kept, but said without its picture
            return f"kept this place on the slide{named}{missing}; ask which way it is and I'll say from the view then"

        registry.add(Tool("bookmark", "Keep this place on the slide with the user's words (\"bookmark this\", \"mark this spot\").",
                          schema({"note": {"type": "string", "description": "the user's words for it"}}),
                          bookmark, inline_ms=800, family="map"))

        async def way_to_bookmark(ctx: ToolContext, args: dict) -> str:
            from ...bookmarks import place_of
            from ...slidemap import place_words

            which = str(args.get("which") or "")
            marks = bookmarks.find(which)
            if not marks:
                listed = bookmarks.all()
                if not listed:
                    raise ToolFailed("nothing has been bookmarked today", next="ask to bookmark a place first")
                kept = "; ".join(f"{m['id']} at {time.strftime('%H:%M', time.localtime(m['at']))}"
                                 + (f" \"{m['note']}\"" if m.get("note") else "") for m in listed[-6:])
                raise ToolFailed(f"no bookmark is {which!r}; today's are {kept}", next="name one of those")
            info = hub.latest_info
            if info is None or getattr(info, "fov", None) is None:
                raise ToolFailed("I can't see the eyepiece circle right now, so I can't say the way from here",
                                 next="make sure the eyepiece circle is in view and lit, then ask again")
            reading = hub.scale_reading() if hasattr(hub, "scale_reading") else None
            um = reading.um_per_px if reading is not None else None
            objective = getattr(getattr(hub, "scale", None), "objective", None)
            lines = []
            for mark in marks[:4]:  # every one the words fit, newest first (review S2)
                found, why = place_of(mark, info, objective)
                named = f"the bookmark \"{mark['note']}\"" if mark.get("note") else f"the bookmark {mark['id']}"
                named += f" ({time.strftime('%H:%M', time.localtime(mark['at']))})"
                pictured = bool(mark.get("picture"))  # one kept without its picture is never pointed to it (ADR 0006)
                if found is None:
                    lines.append(f"{named}: can't tell the way from here: {why}" + (
                        "; its picture shows what to look for" if pictured else
                        f"; it was kept without a picture ({mark.get('pictureMissing') or 'none was kept'})"))
                else:
                    where = place_words(found, um)
                    if where.startswith("in view now"):  # said from the slide's movement, never from looking (review S3)
                        where = "by the slide's movement, it should be " + where.replace("in view now", "in view", 1) + \
                                ("; compare with its picture" if pictured else "")
                    lines.append(f"{named} is {where}. Measured from how far the slide has moved since; directions are "
                                 "as seen in the picture")
                log_measurement(services, ctx, "bookmark", bookmark=mark["id"], known=found is not None,
                                **({} if found is None else {"dxPx": round(found.dx, 1), "dyPx": round(found.dy, 1),
                                                            "fields": round(found.fields, 3), "inView": found.in_view}),
                                how="from the eyepiece centre now, by how far the slide moved since it was kept")
            return "\n".join(lines)

        registry.add(Tool("way_to_bookmark", "Which way a bookmarked place is from here (\"where was the stomata I marked?\"), "
                          "measured; never guess a direction.",
                          schema({"which": {"type": "string", "description": "its words or id; empty: the last"}}),
                          way_to_bookmark, kind="query", inline_ms=300, family="map"))

    if slidemap is None:
        return

    async def show_map(ctx: ToolContext, args: dict) -> str:
        from ...screen import on_screen, open_state, screen_of

        hub = services.hub
        before = screen_of(hub).reported_at
        if args.get("open") is False:
            slidemap.set_open(False)
            await on_screen(hub, open_state("slidemap", False), before, "the slide map is not closed", "close it with its ×")
            return "closed the slide map"
        slidemap.set_open(True)
        await on_screen(hub, open_state("slidemap", True), before, "the slide map is not open", "open it with the Slide map button")
        wire = slidemap.wire()
        if wire["url"] is None:
            return ("opened the slide map; it is empty so far: it fills in with every still view as the slide is "
                    "moved and held")
        others = f" ({wire['maps'] - 1} earlier map{'s' if wire['maps'] > 2 else ''} from before the view lost track " \
                 "are kept)" if wire["maps"] > 1 else ""
        return f"opened the slide map: the still views seen so far, with the view now and the outlines marked{others}"

    registry.add(Tool("show_map", "Show the slide map beside the view, or close it (open: false): the still views seen "
                      "so far, pasted where they are on the slide, with the view now and the outlines marked. Use it "
                      "when the user asks for the map, an overview of the slide, or where they have been.",
                      schema({"open": {"type": "boolean", "description": "false closes it (default true)"}}),
                      show_map, inline_ms=200, family="map"))
