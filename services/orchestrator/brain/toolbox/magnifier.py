"""Tools on the magnifier: zoom, hold, compare, focus, count."""
from __future__ import annotations

import time

import asyncio

from ...day.sid import session_of
from ...magnifier import comparison_picture, crop_jpeg, object_box_in, padded_rect, parse_level, region_sharpness, sharpest_frame, side_by_side
from ..registry import Registry, Tool, ToolContext, ToolFailed, schema
from .common import OBJECT, describe, in_view, log_measurement, resolve_object, visible_object


def picture_size(jpeg: bytes) -> tuple[int, int]:
    """A picture's width and height, read from its header (4:3 when it cannot be read: the room made is then only
    approximate, and the fit before its first paint is still the window's own)."""
    import io

    from PIL import Image

    try:
        with Image.open(io.BytesIO(jpeg)) as picture:
            return picture.size
    except Exception:  # an unreadable header leaves only the room's estimate rough (rule 4)
        return 4, 3


def _member_meant(hub, ref, magnifier_open: bool = False, open_obj=None, asked=None, pointed=None):
    """(population, member) when `ref` names a cell of a population: its id ("c12"), or "that cell" /
    nothing while a member is selected on the shown layer. Else None (an outline is meant). With the
    magnifier open, no object or "it" means what it shows (M1.5c review item 1). `asked` (board.WhenAsked): the
    cell selected when the user made the request, not one clicked while it is answered (the partner's Amendment 1,
    C2); an outline selected after that cell, or one this request's own step pointed at (`pointed`), is meant
    instead. With nothing named (not "it" or "that", which go to the board's saved pointing), an open magnifier
    means what it showed when the user asked; the caller passes it only then (#179 re-check SF1)."""
    import re

    from ...evidence import evidence_of
    from ...population import layer_of

    key = " ".join(str(ref or "").lower().split())
    if re.fullmatch(r"c\d+", key):
        population, member = evidence_of(hub).member(key)
        return (population, member) if member is not None else None
    deictic = ("", "that", "this", "it", "that cell", "this cell", "the cell", "the selected cell", "the one i clicked")
    if pointed is not None and pointed in hub.scene.objects and key in deictic:
        return None  # this request's own step selected an outline: that is "it"
    layer = getattr(hub, "layer", None)
    if asked is not None:
        cell = asked.cell if asked.cell and not (asked.selected is not None and asked.selected_at > asked.cell_at) else None
        selected = cell[1] if cell else None
    else:
        cell, selected = None, getattr(layer, "selected", None) if layer is not None else None
    shows = (getattr(open_obj, "made_by", None) or {}) if open_obj is not None else {}
    clicked_since = selected is not None and selected != shows.get("member")  # a cell clicked after it opened
    if magnifier_open and key in ("", "it", "this", "that", "this one", "that one") and not clicked_since:
        return None
    if shows.get("how") == "measured" and key in ("that cell", "this cell", "the cell", "the selected cell") and \
            not clicked_since:
        # The cell already in the magnifier (its promoted outline; re-check N3), unless another cell was
        # clicked since: then that one is meant (re-check R1).
        return None
    if not selected:
        return None
    if key not in deictic:
        return None
    population = evidence_of(hub).get(cell[0]) if cell else layer_of(hub).population()
    member = population.members.get(selected) if population is not None else None
    return (population, member) if member is not None else None


def model_said(model: str) -> str:
    """A model's one name as the user hears it ("Gemini 3.8 Flash"), else its id."""
    from ...options import VISION

    option = VISION.get(model)
    return option.label if option is not None else model


def shown_in_project(hub, ctx) -> list[dict]:
    """The reference photos already kept in the Project the request was asked in, oldest first (at most 12): each
    one's page, title, the digest of its pixels and, when a look at those pixels described it, what it showed. Facts
    from the app for "a different one than you have been"; the web's words cross only as capped lines."""
    from ...untrusted import inline

    results = getattr(getattr(hub, "resources", None), "results", None)
    if results is None:
        return []
    asked = getattr(ctx, "asked", None)
    try:
        entries = results.entries(getattr(asked, "session", None) or None)
    except Exception:
        return []  # an unreadable store leaves the request's own view as the only record of earlier ones
    out = []
    for entry in entries:
        web = entry.get("web") if entry.get("media") == "reference" else None
        if not isinstance(web, dict) or not web.get("page"):
            continue
        source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
        digests = entry.get("digests") or []
        row = {"page": inline(web["page"], 1000), "title": inline(web.get("title"), 200),
               "digest": digests[0] if digests else None}
        if source.get("describedBy") and source.get("description"):  # seen by a look, never a caller's own words
            row["shows"] = inline(source["description"], 300)
        out.append(row)
    return out[-12:]


def on_screen_words(services, ctx) -> str:
    """What the user was looking at when they asked, for a web search to read the request in (live 2026-10-11 16:49:
    "a picture of an iris online", right after zooming on the user's eye, found Iris the plant): the running look's
    description and specimen, the subject named, and the names of the outlines pointed at, selected, magnified or
    shown in the requesting studio's windows. Facts from the app, given as data; the search model decides what they
    mean for the words. "" when there is nothing to say."""
    from ...ticket import current
    from ...untrusted import inline

    hub = services.hub
    parts = []
    request = current()
    selection = getattr(request, "sight_selection", None) if request is not None else None
    sight = getattr(services, "sight", None)
    view = selection.view if selection is not None else getattr(sight, "view", None)
    if view is not None and (view.summary or view.specimen):
        moved = "" if view.current(getattr(hub, "latest_info", None)) else " (before the slide last moved)"
        look = inline(view.summary, 300)
        parts.append(f"the running look{moved}: {look}" + (f" Specimen: {inline(view.specimen, 120)}." if view.specimen else ""))
    context = getattr(hub.scene, "context", None) or {}
    if context.get("specimen"):
        parts.append(f"the subject named: {inline(context['specimen'], 120)}")
    asked = getattr(ctx, "when_asked", None)
    roles = [("pointed at", getattr(ctx, "pointed", None)), ("selected", getattr(asked, "selected", None)),
             ("magnified", getattr(asked, "magnified", None))]
    pin = getattr(ctx, "request_snapshot", None)
    if pin is not None:
        observation = pin.observation.wire()
        sources = {source.get("key"): source for source in observation.get("sources", [])}
        for row in observation.get("items", []):
            identity = (sources.get(row.get("sourceKey")) or {}).get("identity") or {}
            if row.get("open") and row.get("visible"):
                roles.append(("shown in a window", identity.get("object") or identity.get("objectId")))
    named = {}
    for role, oid in roles:
        obj = hub.scene.objects.get(oid) if isinstance(oid, str) else None
        if obj is not None and obj.label:
            named.setdefault(oid, (inline(obj.label, 80), []))[1].append(role)
    for label, how in named.values():
        parts.append(f"the outline {' and '.join(dict.fromkeys(how))}: {label}")
    return inline("; ".join(parts), 900)


def add_magnifier_tools(registry: Registry, services) -> None:
    hub, magnifier = services.hub, services.magnifier
    if magnifier is None:
        return

    def times(level: float) -> str:
        return f"×{level:.1f}".replace(".0", "") if level < 10 else f"×{round(level)}"

    async def show_pair(a, b) -> None:
        """Two objects side by side in the magnifier, each from its sharpest recent frame; done once the
        studio shows it (screen.picture_shown)."""
        loop = asyncio.get_running_loop()
        crops = [await loop.run_in_executor(None, crop_jpeg, hub, o, 0.15, 92, None) for o in (a, b)]
        if None in crops:
            raise ToolFailed("one of them has no recent clear frame", next="hold the slide still for a second, then ask again")
        jpeg = await loop.run_in_executor(None, side_by_side, [c[0] for c in crops])
        from ...screen import picture_shown

        await picture_shown(magnifier, jpeg, f"{a.label or a.id} and {b.label or b.id}", object_id=a.id)

    async def side_by_side_instead(ctx: ToolContext, first, obj, mine: list[str]) -> str:
        """A zoom on another object after this request's own zoom (going live G4, 2026-10-07 16:11:51: "zoom into
        both of them" zoomed o3, then o4, which replaced it, while the answer said both). The magnifier is one
        window showing one thing, so the two are shown side by side in it instead, each a still picture from its
        sharpest recent frame, and that is what is said."""
        from ..intent import log_override

        if len(mine) >= 2:  # the request already shows two side by side: a third would replace them
            shown = " and ".join(describe(hub.scene.objects[i]) for i in mine[:2])
            raise ToolFailed(f"the magnifier is one window: it shows one thing, or two side by side, and it already "
                             f"shows {shown}", next=f"ask for {describe(obj)} on its own next")
        try:
            await show_pair(first, obj)
        except ToolFailed as exc:
            raise ToolFailed(f"the magnifier shows one thing at a time, and showing {describe(first)} and {describe(obj)} "
                             f"side by side did not work: {exc}", next=exc.next) from exc  # next: passed on
        ctx.magnified.append(obj.id)
        log_override(getattr(services.agent, "log", None), "one-magnifier", asked={"zoom": obj.id}, showing=first.id,
                     ran={"compare": [first.id, obj.id]}, question=ctx.question)
        return (f"the magnifier shows one thing at a time, so it shows a still picture of each, {describe(first)} and "
                f"{describe(obj)}, side by side instead")

    async def zoom(ctx: ToolContext, args: dict) -> str:
        ref, level, held = args.get("object"), args.get("level"), bool(args.get("hold"))
        native = getattr(services, 'presentations', None) is not None and getattr(services, 'prepared_sources', None) is not None and bool(ctx.client) and not held
        # The picture the magnifier shows now, else its newest, to keep next to the close-up (live case 7).
        picture = None
        if args.get("picture"):
            key = str(args["picture"])
            picture = next((s for s in magnifier.sources if s.get("kind") == "image" and s.get("url") == key), None)
            if not magnifier.open or picture is None or not magnifier.picture_kept(key):
                raise ToolFailed("that picture is not available in the magnifier", next="open the picture again")
        elif args.get("beside") is True:
            shown = magnifier.pictures_shown()
            now = magnifier.picture_now()
            picture = now if now is not None and now in shown else (shown[-1] if shown else None)
        if level is not None and parse_level(level) is None:
            raise ToolFailed(f"{level!r} is not a zoom level; give a number like 4, or more, less, fit or max",
                             next=None)  # no next: the message says what to do
        displayed = magnifier.displayed_object
        open_on = displayed if magnifier.open and displayed in hub.scene.objects else None
        mine = [i for i in dict.fromkeys(ctx.magnified) if i in hub.scene.objects]  # what this request's zooms showed
        asked = getattr(ctx, "when_asked", None)
        # With nothing named, the magnifier means what it showed when the user asked: selecting another outline since
        # makes an open magnifier follow it, and that is not what the request was about (#179 re-check SF1, PZ4).
        open_then = open_on if asked is None or asked.magnified == open_on else None
        member = _member_meant(hub, ref, magnifier_open=open_then is not None,
                               open_obj=hub.scene.objects.get(open_then) if open_then else None,
                               asked=asked, pointed=getattr(ctx, "pointed", None))
        if member is not None:
            # A cell of a population ("show that cell in HD"): made an outline from its own mask, then
            # magnified live while it is verified in place; otherwise its captured view's real pixels.
            # The outline already made for that cell is used again, never a second one (review item 1).
            from ...evidence import linked_outline
            from ...population import captured_picture, promote

            population, m = member
            again_of = next((link["to"] for link in population.links
                             if link.get("from") == m.id and linked_outline(hub, link) is not None), None)
            if not native and magnifier.open and mine and again_of not in mine:
                # Enforce the one-window limit before promotion can leave an unshown outline on the board.
                shown = " and ".join(describe(hub.scene.objects[i]) for i in mine[:2])
                cell = f"cell {m.id}"
                if len(mine) >= 2:
                    raise ToolFailed(f"the magnifier is one window: it shows one thing, or two side by side, and it "
                                     f"already shows {shown}", next=f"ask for {cell} on its own next")
                if again_of is None:
                    from ...population import capture_of, layer_of
                    from ...apply import place_outline

                    layer = layer_of(hub)
                    if layer.shown == population.id:
                        await layer.check()
                        now = getattr(hub, "latest_info", None)
                        placement = layer.placement.get(m.id) or {}
                        verified = (now is not None and placement.get("frame") == now.id
                                    and placement.get("state") == "verified")
                    else:
                        capture = capture_of(hub, population)
                        verified = capture is not None and (await place_outline(hub, capture, m.polygons)).verified
                    if not verified:
                        raise ToolFailed(f"the magnifier shows one thing at a time and already shows {shown}; {cell} "
                                         "is not in place on the live view, so it can only be shown on its own",
                                         next=f"ask for {cell} on its own next")
            made = [hub.scene.objects[again_of]] if again_of else await promote(
                hub, population, [m.id], why="shown close up", require_live=bool(not native and magnifier.open and mine))
            if not made:
                if m.status in ("deleted", "merged"):  # corrected since the user asked (#179 re-check note PZ6)
                    raise ToolFailed(f"{m.id} was {'removed' if m.status == 'deleted' else 'merged into another'} by a "
                                     "correction since you asked", next="click the cell you mean")
                raise ToolFailed(f"{m.id} could not be shown", next="outline the cells again")
            obj = made[0]
            if not getattr(obj, "shown", True):
                if magnifier.open and mine and obj.id not in mine:
                    # Not in place on the live view, it can only be shown alone, on the view it was made on (G4 review).
                    shown = " and ".join(describe(hub.scene.objects[i]) for i in mine[:2])
                    raise ToolFailed(f"the magnifier shows one thing at a time and already shows {shown}; {describe(obj)} is "
                                     "not in place on the live view, so it can only be shown on its own",
                                     next=f"ask for {describe(obj)} on its own next")
                jpeg = captured_picture(hub, population, [m.id])
                if jpeg is None:
                    raise ToolFailed(f"the view {m.id} was made on is no longer kept", next="outline the cells again")
                when = time.strftime("%H:%M:%S", time.localtime(population.view.get("at") or population.made_at))
                from ...screen import picture_shown

                await picture_shown(magnifier, jpeg, f"{m.id} of {population.id} on the view at {when}: real pixels, not the live view",
                                    object_id=obj.id, **({"beside": picture} if picture is not None else {}))
                from ..registry import attach

                attach(values={"member": m.id, "population": population.id})  # its id one step away (M1.5c re-check note)
                ctx.magnified[:] = [obj.id]
                return ("showing the cell on the view it was made on ({}), real pixels; it is not verified in place on the "
                        "live view now".format(when))
        # No object named while the magnifier is open on what it showed when the user asked: it is the one meant.
        cell_words = ("that cell", "this cell", "the cell", "the selected cell")
        open_obj = hub.scene.objects.get(open_then) if open_then else None
        if member is None and open_obj is not None and str(ref or "").strip().lower() in cell_words:
            ref = None  # "that cell" while its promoted outline is in the magnifier and no other cell was clicked (N3, R1)
        obj = obj if member is not None else (hub.scene.objects[open_then] if not ref and open_then
                                              else visible_object(hub, ref, ctx))
        if native:
            from .present import _native_present
            from ..registry import CURRENT_RUN
            run = CURRENT_RUN.get()
            pctx = services.workspace_context(ctx.client, request_id=run.occurrence.request_id,
                                             owner=run.occurrence.action_id)
            source = services.prepared_sources.live.object_source(obj)
            shown = next((w for w in services.presentations.list(pctx.workspace_id)
                          if w.open and w.source == source), None)
            if shown is None:
                outcome = await _native_present(services, ctx, {'do': 'open', 'source': source.to_wire(),
                    'title': f'{describe(obj)} · live magnification'[:120]})
                if level is not None and parse_level(level) not in ('fit', 1):
                    made = next(output for output in reversed(run.outputs) if output.get('kind') == 'presentation')
                    value = parse_level(level)
                    zoom_value = ({'fit': True} if value == 'fit' else {'factor': .5 if value == 'less' else 2}
                                  if value in ('less', 'more') else {'level': 32 if value == 'max' else value})
                    outcome = await _native_present(services, ctx, {'do': 'zoom',
                        'instance': {'instanceId': made.get('instanceId')}, 'zoom': zoom_value})
            else:
                value = parse_level(level or 'more')
                zoom_value = ({'fit': True} if value == 'fit' else {'factor': .5 if value == 'less' else 2}
                              if value in ('less', 'more') else {'level': 32 if value == 'max' else value})
                outcome = await _native_present(services, ctx, {'do': 'zoom', 'instance': shown.id, 'zoom': zoom_value})
            hub.scene.select(obj.id)
            ctx.pointed = obj.id
            ctx.magnified.append(obj.id)
            return outcome
        if magnifier.open and mine and obj.id not in mine:
            return await side_by_side_instead(ctx, hub.scene.objects[mine[0]], obj, mine)
        again = obj.id == open_on
        if again and level is None and not held and picture is None:
            level = "more"  # "magnify it" on what is already magnified: closer (beside a picture: as it is)
        hub.scene.select(obj.id)
        ctx.pointed = obj.id  # "it" in this request's later steps (board.py)
        before = magnifier.factor if again else 1.0
        from ...screen import on_screen, open_state, screen_of

        reported = screen_of(hub).reported_at
        now = magnifier.zoom(obj.id, hold=held, level=level, beside=picture)
        if not again:  # opened: done only once the studio shows it (Amendment 2, point 4)
            await on_screen(hub, open_state("magnifier", True, shows=obj.id), reported, "the magnifier is not open on it",
                            "click the outline to open it")
        notes = []
        if picture is not None:
            from ...media import WORDS
            from ...screen import magnifier_beside

            await on_screen(hub, magnifier_beside(picture.get("url"), obj.id), reported,
                            "the picture is not shown beside it", "flip to the picture with the magnifier's dots")
            notes.append(f"beside {WORDS.get(picture.get('media') or 'unknown', WORDS['unknown'])}, both in the one window")
        elif args.get("beside") is True and not held:
            notes.append("no picture is in the magnifier to show beside it")
        if not again and level is None:
            text = f"opened the magnifier on {describe(obj)}"
            notes.append("fitted, ×1")
        else:
            from ...screen import magnifier_level

            # On the magnifier already open: done once the report shows that level (M1.5b re-check N5).
            await on_screen(hub, magnifier_level(now, obj.id), reported, "the magnifier is not at that level",
                            "use + and - on the magnifier")
            text = f"magnified {describe(obj)} {times(now)}"
            if now >= magnifier.max_level() - 1e-6 and parse_level(level) not in ("fit", "less"):
                notes.append("the closest it goes: the window is a few camera pixels across")
            elif abs(now - before) < 1e-6 and parse_level(level) in ("more", "less"):
                notes.append("already at that end")
        if magnifier.pixel_enlarged():
            notes.append("beyond the camera's detail: pixel-enlarged")
        if held:
            notes.append("sharpest frame")
        ctx.magnified[:] = [obj.id]
        return text + (f" ({'; '.join(notes)})" if notes else "")

    registry.add(Tool("zoom", "Open an independent live magnifier on an object, retaining other windows, or change "
                      "how close an already open one looks. Each object gets its own window. level: \"fit\" (the whole "
                      "object filling the window: what \"show it up close\", \"in HD\" and \"the whole cell\" mean; a small "
                      "thing is then already enlarged past the camera's detail, and the answer says so), a number (×N closer "
                      "than fit, e.g. 4), \"more\" (twice as close), \"less\" (half), or \"max\" (the closest it goes, only "
                      "when asked for the closest). "
                      "Any level works and it never refuses: past the camera's resolution the picture is "
                      "pixel-enlarged (blocky, no new detail). Without object it acts on the open magnifier. "
                      "This changes camera magnification, never an image source’s size. hold=true shows the sharpest recent "
                      "frame instead of the moving live view. picture names an existing image URL from the view notes "
                      "to show beside the camera crop; use it when a specific reference or snapshot is requested. "
                      "Without picture, beside=true keeps the "
                      "picture in the magnifier (a reference photo, a researcher's picture) and shows the object's live "
                      "close-up next to it, both at once in the one window (\"a magnification next to the photo\").",
                      schema({"object": OBJECT,
                              "level": {"type": "string", "description": 'a number like 4, or "more", "less", "fit", "max"'},
                              "hold": {"type": "boolean", "description": "show the sharpest frame"},
                              "beside": {"type": "boolean", "description": "next to the current picture in the magnifier"},
                              "picture": {"type": "string", "description": "existing image URL to put beside the camera crop"}}),
                      zoom, family="magnifier", outline_refs=("object",)))

    async def magnifier_source(ctx: ToolContext, args: dict) -> str:
        from ...media import WORDS
        from ...screen import on_screen, screen_of
        from ...untrusted import inline

        key = args.get("source")
        choices = magnifier.sources if magnifier.open else []
        index = next((i for i, source in enumerate(choices) if isinstance(key, str) and key
                      if key == (source.get("url") if source.get("kind") == "image" else source.get("kind"))), None)
        if index is None:
            raise ToolFailed("that source is not in the open magnifier",
                             next="choose one of its available sources, or open the picture first")
        source = choices[index]
        if source.get("kind") == "image" and not magnifier.picture_kept(key):
            raise ToolFailed("that picture is no longer kept", next="open the picture again")
        what = WORDS.get(source.get("media") or "unknown", WORDS["unknown"]) if source.get("kind") == "image" \
            else ("the live camera close-up" if source.get("kind") == "live" else "the sharpest camera frame")
        label = inline(source.get("label") or "", 100)
        reported = screen_of(hub).reported_at
        if "beside" in args:
            magnifier.pair_sources(key, args["beside"])
        else:
            magnifier.set_index(index)
        peer = magnifier.shown_beside()
        from ...screen import magnifier_arrangement
        pictures = [s["url"] for s in [source, peer] if s and s.get("kind") == "image"]
        check = magnifier_arrangement(key, magnifier.displayed_object, pictures, (peer or {}).get("url"))
        await on_screen(hub, check, reported,
                        "the selected source is not shown in the magnifier", "use its source dots, or ask again")
        return f"showing {what} ({label})" + (f" beside {inline(peer.get('label') or 'picture', 140)}" if peer else " in one image pane (its image content is unchanged)") + "; its other sources remain available"

    registry.add(Tool("magnifier_source", "Switch the open magnifier to one of its existing sources, without changing "
                      "zoom or losing the other sources. source is a key from the current magnifier sources in your "
                      "view notes: live, held, or an existing picture URL. Selecting a picture shows that entire image, including all subjects already in it; selecting "
                      "live or held restores any camera comparison. Optional beside: another existing picture URL to show "
                      "alongside this source, or an empty string to hide the separate peer pane while retaining it. "
                      "Does not fetch or generate a new picture or change what an existing picture depicts. If the wanted "
                      "subject is a saved member, use present(what=cN, do=open) to open its original crop without a live outline. Returns the actual selected "
                      "source for the answer to use.",
                      schema({"source": {"type": "string", "description": "key of an existing magnifier source from the view notes"},
                              "beside": {"type": "string", "description": "existing picture URL to pair, or empty string to hide the peer"}},
                             ["source"]), magnifier_source, kind="query", family="magnifier"))

    async def magnifier_picture(ctx: ToolContext, args: dict) -> str:
        from ...screen import magnifier_arrangement, on_screen, screen_of

        key = str(args.get("source") or "")
        current = magnifier.sources[magnifier.index] if magnifier.open and magnifier.sources else {}
        selected = current.get("url") if current.get("kind") == "image" else current.get("kind")
        beside = (magnifier.shown_beside() or {}).get("url")
        camera_level = 1.0 if current.get("kind") == "image" else magnifier.factor
        object_id = magnifier.displayed_object
        before = screen_of(hub).reported_at
        scale = magnifier.scale_picture(key, args.get("scale"))

        image_urls = [s["url"] for s in [current, magnifier.shown_beside()] if s and s.get("kind") == "image"]
        arrangement = magnifier_arrangement(selected, object_id, image_urls, beside)
        def displayed(report):
            item = next((i for i in report.get("items", []) if i.get("id") == "magnifier"), {})
            return (arrangement(report) and item.get("shows") == object_id
                    and item.get("source") == selected and item.get("beside") == beside
                    and item.get("level") == camera_level
                    and any(p.get("source") == key and p.get("scale") == scale for p in item.get("pictures", [])))

        await on_screen(hub, displayed, before, "the picture enlargement is not shown with its comparison unchanged",
                        "show the picture and ask again")
        return f"picture displayed at ×{scale:g}; camera zoom and other sources unchanged; enlarged pixels, no new detail; edges may be cropped"

    registry.add(Tool("magnifier_picture", "Enlarge an already displayed image or reference without changing the camera "
                      "zoom, selected source or beside comparison. source is its existing image URL from the view notes. "
                      "scale is more (25% larger), less, fit (whole image) or 1 to 8. Digital enlargement within its pane: "
                      "edges may be cropped, no new detail. Does not generate or fetch an image.",
                      schema({"source": {"type": "string", "description": "URL of the displayed image to enlarge"},
                              "scale": {"type": "string", "description": "more, less, fit or a number from 1 to 8"}},
                             ["source", "scale"]), magnifier_picture, family="magnifier"))

    async def reference_photo(ctx: ToolContext, args: dict) -> str:
        from ... import reference
        from ...media import REFERENCE
        from ...screen import picture_shown
        from ...untrusted import inline
        from ..registry import attach
        from urllib.parse import urlparse

        subject = inline(args.get("subject"), 600)
        if not subject:
            raise ToolFailed("say what to find a reference photo of", next=None)  # no next: the message says what to do
        lookup_args = {}
        prior_references = []
        native_beside = (args.get("beside") is True and args.get("show") is not False
                         and getattr(services, 'presentations', None) is not None and ctx.client is not None)
        beside_anchor, beside_why = None, ''
        if native_beside and ctx.request_snapshot is not None:
            # Boolean compatibility means the source this browser showed when
            # asked, not whichever window happens to be newest after web I/O.
            observation = ctx.request_snapshot.observation.wire()
            source_by_key = {source['key']: source for source in observation.get('sources', [])}
            mine = {window.id for window in services.presentations.list(ctx.request_snapshot.observation.client.workspace_id)}
            candidates = [row for row in observation.get('items', [])
                          if row['id'] in mine and all(row.get(k) is True for k in ('open', 'visible', 'connected'))]
            asked = ctx.when_asked
            pointed = ctx.pointed or (asked.selected or asked.magnified if asked is not None else None)
            matching = [row for row in candidates
                        if source_by_key.get(row.get('sourceKey'), {}).get('identity', {}).get('object') == pointed
                        and pointed is not None]
            choices = matching if matching else candidates
            if len(choices) == 1:
                beside_anchor = choices[0]['id']
            elif choices:
                # Several windows and none pointed at: the one in front, by the z the studio measured, and the
                # receipt says so; never the user's to name (spec 4.8, horizontal 3).
                front = max(choices, key=lambda row: row.get('z') if isinstance(row.get('z'), (int, float)) else -1)
                beside_anchor, beside_why = front['id'], f"beside {inline(str(front.get('title') or front['id']), 80)}, the window in front"
        if not services.find_reference and ctx.question:
            lookup_args["request_context"] = ctx.question
        on_screen = on_screen_words(services, ctx)
        if not services.find_reference and on_screen:
            lookup_args["view_context"] = on_screen
        if not services.find_reference and ctx.request_snapshot is not None:
            # Use the immutable requesting-browser view, never a later or peer
            # workspace. Only public reference metadata goes to web retrieval.
            sources = []
            observation = ctx.request_snapshot.observation.wire()
            by_key = {s['key']: s for s in observation.get('sources', [])}
            for row in observation.get('items', []):
                source = by_key.get(row.get('sourceKey'), {})
                web = source.get('provenance', {}).get('web') or {}
                if row.get('open') and source.get('media') == REFERENCE and web.get('page'):
                    sources.append({'page': inline(web['page'], 1000),
                                    'title': inline(web.get('title'), 200), 'digest': source.get('digest')})
                    resource = source.get('identity', {}).get('resource') or {}
                    prior_references.append({'window': row['id'], 'day': resource.get('day'),
                        'id': resource.get('id'), 'version': resource.get('version'),
                        'picture': resource.get('picture'), 'page': inline(web['page'], 1000)})
            if sources:
                lookup_args['previous_sources'] = sources[:12]
        if not services.find_reference:
            # "A different one than you have been": every reference photo already kept in this Project, not only the
            # ones open in a window when asked (live 2026-10-29 17:16), with what its own look saw in it.
            earlier = shown_in_project(hub, ctx)
            if earlier:
                open_now = {(p.get('digest'), p.get('page')): p for p in lookup_args.get('previous_sources', [])}
                for row in earlier:
                    if (row.get('digest'), row.get('page')) in open_now:  # open in a window too: one row, with its look
                        open_now[(row.get('digest'), row.get('page'))].update({k: v for k, v in row.items() if k == 'shows'})
                merged = [p for p in earlier if (p.get('digest'), p.get('page')) not in open_now]
                lookup_args['previous_sources'] = (lookup_args.get('previous_sources', []) + merged)[-12:]
        if "search_terms" in args:
            try:
                lookup_args["search_terms"] = reference.normalize_search_terms(args["search_terms"])
            except ValueError as exc:
                raise ToolFailed(str(exc), next=None) from None  # no next: the message gives the valid input shape
        n = args.get("n") if isinstance(args.get("n"), int) and not isinstance(args.get("n"), bool) else 1
        settings = getattr(getattr(services, "hub", None), "settings", None)
        sources = getattr(settings, "reference_search", "web") if settings is not None else "web"  # option REFERENCE_SEARCH
        from ..registry import CURRENT_RUN

        searching = CURRENT_RUN.get()
        if searching is not None:  # what it is doing now, in its own words, for a progress line
            searching.report_progress(f"searching the web for a real photo of {inline(subject, 80)} and checking it")
        try:
            found = await asyncio.to_thread(services.find_reference or reference.find_blocking, subject, n, **lookup_args,
                                            sources=sources)
        except reference.NotFound as exc:
            attach(values={"referenceLookup": {"status": "failed"}})
            raise ToolFailed(f"no reference photo of {subject} returned by this lookup: {exc}",
                             next="try a broader search if useful") from None
        # The web's words (title, author, licence) cross only as a capped line (untrusted.inline).
        host = urlparse(found.page).hostname
        site = reference.SITE if host == 'commons.wikimedia.org' else host or 'the web'
        title, credit = inline(found.title, 80) or f"Photo from {site}", inline(found.credit, 120)
        # What the picture shows is what a look at its pixels saw (reference.retrieve), never the request's words nor
        # the caller's own description written before anyone saw it (live 2026-10-29 17:16: "Microscope reference
        # photograph showing a single stoma." beneath a tissue cross-section). The caller's text is kept as its note.
        shows = inline(getattr(found, "shows", ""), 600)
        looked_by = getattr(found, "looked_by", "")
        differs = inline(getattr(found, "differs", ""), 200)
        note = inline(args.get("description"), 600)
        attach(values={"media": REFERENCE, "subject": subject, "searched": found.searched, "n": found.n,
                       **({"onScreen": on_screen} if on_screen else {}),
                       **({'referencesAtRequest': prior_references[:12]} if prior_references else {}),
                       **({"description": shows, "describedBy": looked_by or "a look at its pixels"} if shows else {}),
                       **({"note": note} if note else {}),
                       **({"differs": differs} if differs else {}),
                       "seconds": found.seconds, "web": {"site": site, "page": found.page, "title": title,
                                                         "licence": inline(found.licence, 60), "author": inline(found.author, 80),
                                                         "credit": credit}})
        seen = (f"what it shows, from a look at its pixels{' by ' + model_said(looked_by) if looked_by else ''}: {shows}"
                if shows else "what it shows was not checked: nothing looked at its pixels")
        if differs:  # none found was all that was asked: this is the nearest, and what it misses is said with it
            # A request only partly met gets a next step (will-workspace, 30 Oct: "shows three stomata, rather than just
            # a single stoma", judged as no next step to get one).
            seen = (f"the nearest found, not exactly what was asked ({differs}): say how it differs and offer to look "
                    f"again for exactly {subject}; {seen}")
        if args.get("show") is False:
            attach(picture_bytes=found.jpeg)
            # Kept now, by its id, as this request's own result: a later step ("put them in the new Tab") places it by
            # that id or by this step, and nothing has to be named again (Will, 30 Oct: two photos kept, never placed,
            # "name what to place").
            from ..registry import CURRENT_RUN, record_output

            kept = ""
            run_now = CURRENT_RUN.get()
            results = getattr(getattr(hub, "resources", None), "results", None)
            # Kept now where this app keeps results (a storage failure fails the step, as the registry's own keeping
            # would); with no store, kept as before once the step ends.
            entry = results.from_run(run_now, ctx, hub, produced=True) if run_now is not None and results is not None else None
            if entry is not None:
                record_output(ctx, {"kind": "resource", "resource": {"day": session_of(entry, entry["day"]),
                                                                     "id": entry["id"], "version": None}})
                kept = f" in Data as {entry['id']}"
            return (f"retained a reference photo{kept}: a real photo from {site}, not this slide ({title}; {credit}); "
                    f"{seen}; asked for: {subject}; the existing magnifier is unchanged")
        if getattr(services, 'presentations', None) is not None and ctx.client:
            from ..registry import CURRENT_RUN
            from .present import _native_present
            attach(picture_bytes=found.jpeg)
            run = CURRENT_RUN.get()
            entry = hub.resources.results.from_run(run, ctx, hub, produced=True)
            if entry is None:
                raise ToolFailed('the photo was fetched but could not be retained for display', next=None)  # no next: the concrete limitation is already stated; no automatic substitute action
            opening = {'do': 'open', 'source': {
                'day': session_of(entry, entry['day']), 'id': entry['id'], 'version': None, 'picture': 0}, 'title': title}
            if native_beside:
                if beside_anchor is None:
                    raise ToolFailed(f"the reference photo is kept in Data as {entry['id']}, but no window of this "
                                     "workspace was open in view to put it beside when it was asked for",
                                     next=f"present open {entry['id']} shows it on its own")
                opening['relation'] = {'kind': 'beside', 'reference': beside_anchor}
            room = ''
            try:
                shown = await _native_present(services, ctx, opening)
            except ToolFailed as exc:
                if not native_beside:
                    raise
                # No room beside it is Hooke's to make, as a person would: it moves that window over (and narrows it
                # only when moving is not enough), then places the photo beside it (will-workspace, 30 Oct: "make room
                # or select the original window" was handed to the user). Only a visible area too small for both is
                # said, plainly.
                failure = ((run.outcome or {}).get('failure') or {}) if run is not None else {}
                made = None
                if failure.get('code') == 'CONSTRAINT_CONFLICT':
                    from ...placement import make_room_beside
                    made = await make_room_beside(services, ctx, beside_anchor, picture_size(found.jpeg))
                if made is None:
                    why = ('the visible area is too small to hold both side by side'
                           if failure.get('code') == 'CONSTRAINT_CONFLICT' else str(exc))
                    raise ToolFailed(f"the reference photo is kept in Data as {entry['id']}, but not shown beside the "
                                     f"window it was asked next to: {why}",
                                     next=None) from exc  # no next: what was kept and why it is not beside is said
                try:
                    shown = await _native_present(services, ctx, opening)
                except ToolFailed as again:
                    raise ToolFailed(f"the reference photo is kept in Data as {entry['id']}; {made}, but it is still "
                                     f"not shown beside it: {again}", next=None) from again  # no next: said in full
                room = f'; {made}' if made else ''
            if beside_why:
                room = f'; {beside_why}{room}'
            shown += f'{room}; {seen}; asked for: {subject}'
            if prior_references:
                import json
                shown += '; references already open when requested: ' + json.dumps(prior_references[:12], separators=(',', ':'))
            return shown
        await picture_shown(magnifier, found.jpeg, f"Reference photo: {title} ({site})", kind_of=REFERENCE,
                            credit=credit, **({"beside": True} if args.get("beside") is True else {}))
        return (f"showing a reference photo in the magnifier: a real photo from {site}, not this slide "
                f"({title}; {credit}); {seen}; asked for: {subject}")

    registry.add(Tool("reference_photo", "Retrieve a real photo directly into its own window, usually within seconds: a "
                      "reference photo with its source and available attribution, not this slide (\"show me a real "
                      "picture of a stoma\", \"what does a normal one look like?\"). Not an AI illustration, and no "
                      "research report. Uses Gemini 3.8 Flash with Google Search across the public web, without domain restrictions. A failed lookup does not require a "
                      "researcher. Preserve the current request's imaging method, magnification and subject in subject and search_terms; "
                      "do not inherit old university, collection or domain restrictions from earlier requests unless the user retains them. "
                      "omit search_terms when the full request is already concise. Never drop a required detail just to get a match. "
                      "show=false fetches and retains without changing the current "
                      "magnifier or motion view; its source output 0 lets a later step use present do=open "
                      "source={step:N,output:0} for this step N, without predicting an ID. Alternatively use the returned resource's exact day/id, "
                      "version=null and picture=0 to open an independent window. Every photo found is looked at, its pixels against "
                      "the whole request (subject, how many, kind of view, imaging, a different one from those already in this Project); "
                      "one that fails is never returned, and the receipt says what the returned photo shows from that look: describe the "
                      "photo only in those words, never as what was asked. description is an optional note on the topic, kept as a note, "
                      "never as what the photo shows. Default/"
                      "show=true opens its independent window, retaining the magnification and other work. In a native workspace, with default "
                      "show=true, beside=true fits the new photo beside the original requesting browser's pointed window "
                      "(or its sole visible window, else the one in front) before its first paint; with no room beside it, "
                      "Hooke first moves that window over (narrowing it only when moving is not enough) and says so. "
                      "For an explicit anchor, use show=false then present(do=open,source={step:N,output:0}, "
                      "relation={kind:beside,reference:{instanceId:the exact observed window ID}}) for initial fitting. "
                      "Arranging an already-open window preserves its current size and may not fit. "
                      "n: another match (2, 3...) when asked for another or a different one; photos already kept in this Project are then never returned.",
                      schema({"subject": {"type": "string", "description": "the full requested photo, including imaging method or magnification constraints"},
                              "search_terms": {"anyOf": [{"type": "string", "minLength": 1, "maxLength": 600},
                                               {"type": "array", "minItems": 1, "maxItems": 2,
                                                "items": {"type": "string", "minLength": 1, "maxLength": 600}}],
                                               "description": "optional web search terms that retain the requested subject and imaging constraints"},
                              "n": {"type": "integer", "description": "which match, 1-based (default 1)"},
                              "show": {"type": "boolean", "description": "default true; false retains the photo for a separate native window and preserves the current view"},
                              "description": {"type": "string", "maxLength": 600, "description": "optional note on the topic, kept as a note; the photo's description comes from a look at its pixels"},
                              "beside": {"type": "boolean", "description": "with default show=true, initial fit beside the original requesting browser's pointed, sole visible or front window, before first paint; that window is moved over first when there is no room beside it. show=false only retains; choose an exact anchor with present open+relation"}}, ["subject"]),
                      reference_photo, inline_ms=1500, family="magnifier", alongside=True))

    async def hold(ctx: ToolContext, args: dict) -> str:
        from ...screen import magnifier_source, on_screen, screen_of

        before = screen_of(hub).reported_at
        if not magnifier.open or magnifier.object_id is None:
            obj = resolve_object(hub, args.get("object"), ctx)
            magnifier.zoom(obj.id, hold=True)
        else:
            magnifier.hold()
        await on_screen(hub, magnifier_source("held", magnifier.object_id), before, "the sharpest frame is not shown",
                        "look at the magnifier, or ask again")
        return "holding the sharpest frame in the magnifier"

    registry.add(Tool("hold", "Freeze the magnifier on the sharpest recent frame of its object.",
                      schema({"object": OBJECT}), hold, family="magnifier", outline_refs=("object",)))

    async def comparison(ctx, args):
        """Resolve each subject against the same board/evidence, retaining its original capture."""
        from ...population import captured_picture
        from ...capture import captures
        from ...resources import RetainedComponent, COMPARISON_SAVE_S
        from ...ticket import CURRENT
        from ..registry import attach, CURRENT_RUN
        import copy

        ticket, run = CURRENT.get(), CURRENT_RUN.get()
        index = getattr(hub, "resources", None)
        results = getattr(index, "results", None)
        day_store = getattr(results, "day", None)
        day, generation = getattr(day_store, "sid", None), getattr(day_store, "generation", None)
        checks, workers = [], []
        reservation = captures(hub).memory.reserve("unsaved", 0, "comparison originals")

        def current():
            why = ticket.superseded("magnifier") if ticket is not None else None
            asked = getattr(ctx, "asked", None)
            if ticket is not None and (ticket.closed or ticket.expired()):
                why = why or "the request is no longer open"
            if day_store is not None and (day_store.sid != day or not day_store.current(generation) or
                                         not day_store.current(getattr(asked, "routing_generation", getattr(asked, "generation", None)))):
                why = "another session was opened since the comparison was requested"
            if why:
                raise ToolFailed(why, next="ask again in the current view")
            for population, revision in checks:
                if population.revision != revision:
                    raise ToolFailed("the saved population changed while its comparison was being made",
                                     next="ask again using its corrected members")

        async def working(function, *values, **kwargs):
            task = asyncio.create_task(asyncio.to_thread(function, *values, **kwargs))
            workers.append(task)
            try:
                return await asyncio.wait_for(asyncio.shield(task), COMPARISON_SAVE_S)
            except TimeoutError:
                raise ToolFailed("reading the comparison source took too long", next="check local storage and try again") from None

        try:
            current()
            crops, labels, identities, sizes, sources = [], [], [], [], []
            for ref in (args.get("a"), args.get("b") or "target 2"):
                member = _member_meant(hub, ref, asked=getattr(ctx, "when_asked", None),
                                       pointed=getattr(ctx, "pointed", None))
                obj = None if member is not None else resolve_object(hub, ref, ctx)
                if obj is not None and (getattr(obj, "made_by", None) or {}).get("member"):
                    member = _member_meant(hub, obj.made_by["member"])
                if member is not None:
                    population, m = member
                    if m.status in ("deleted", "merged"):
                        raise ToolFailed(f"{m.id} was removed or merged by a correction", next="choose a current member")
                    revision = population.revision
                    checks.append((population, revision))
                    crop = await working(captured_picture, hub, population, [m.id], outlines=False,
                                         with_facts=True, retained=reservation)
                    current()
                    if crop is None:
                        raise ToolFailed(f"the source picture for {ref} is no longer available", next="choose a retained source")
                    jpeg, source = crop
                    source.update(member=m.id)
                    when = time.strftime("%H:%M:%S", time.localtime(source["at"] or population.made_at))
                    labels.append(f"{m.id} of {population.id} revision {revision}, captured at {when}")
                    identities.append(m.id)
                    sizes.append(None)
                else:
                    # Tracking may mutate an outline while a worker is choosing its view. The
                    # crop uses this request's frozen geometry and the chosen Capture's facts.
                    frozen = copy.copy(obj)
                    frozen.polygons, frozen.anchor_offset = copy.deepcopy(obj.polygons), tuple(obj.anchor_offset)
                    crop = await working(crop_jpeg, hub, frozen, 0.15, 92, None,
                                         with_facts=True, retained=reservation)
                    current()
                    if crop is None:
                        raise ToolFailed(f"the source picture for {ref} is no longer available", next="choose a retained source")
                    jpeg, source = crop
                    labels.append(f"{describe(obj)}, sharpest recent camera frame")
                    identities.append(obj.id)
                    sizes.append(hub.size_of(obj) if hasattr(hub, "size_of") else None)
                crops.append(jpeg)
                sources.append(source)
                reservation.settle(sum(len(c) for c in crops))
            if identities[0] == identities[1]:
                raise ToolFailed("pick two different objects to compare", next=None)  # no next: the refusal already asks for two distinct original objects
            tallest = max(s["naturalSize"][1] for s in sources)
            width = sum(max(1, int(s["naturalSize"][0] * 360 / tallest)) for s in sources) + 8
            reservation.grow(width * 360 * 8 + 4096, "retain the comparison composite")
            jpeg = await working(comparison_picture, hub, crops)
            current()
            if jpeg is None:
                raise ToolFailed("the comparison pictures could not be read", next="choose another source")
            reservation.settle(sum(len(c) for c in crops) + len(jpeg))
            attach(values={"captureSources": copy.deepcopy(sources), "displayScale": "same camera-pixel scale"})
            if results is not None and run is not None:
                reservation.grow(128 * 1024, "comparison source metadata")
                components = tuple(RetainedComponent.of(crop, source) for crop, source in zip(crops, sources))
                owned, reservation = reservation, None  # Results owns it through saving/display/finish
                await results.stage_comparison(run, ctx, hub, jpeg, components, owned, current,
                                               " and ".join(labels))
            return jpeg, labels, sizes, identities
        finally:
            if reservation is not None:
                pending = next((task for task in workers if not task.done()), None)
                if pending is None:
                    reservation.release()
                else:
                    # A canceled await does not cancel a thread using this reservation.
                    held = reservation
                    def finished(task):
                        if not task.cancelled():
                            task.exception()
                        held.release()
                    pending.add_done_callback(finished)

    async def compare(ctx: ToolContext, args: dict) -> str:
        jpeg, labels, _, identities = await comparison(ctx, args)
        from ...screen import picture_shown

        await picture_shown(magnifier, jpeg, " and ".join(labels))
        ctx.magnified[:] = [i for i in identities if i in hub.scene.objects]
        return (f"comparing {' and '.join(labels)} side by side at the same camera-pixel scale; "
                "physical sizes need each capture's scale; these are source pictures, not live tracking. "
                "Created a new comparison artifact; this did not retrieve an earlier comparison.")

    registry.add(Tool("compare", "Create a NEW comparison artifact of two objects or saved population members side by side in the one magnifier window from their original "
                      "captures (saved members) or sharpest recent frame (live outlines). One camera-pixel enlargement "
                      "for both; physical size comparisons require their capture scales. Accepts saved member ids. "
                      "To reopen an earlier comparison, resolve its saved result ID with resources and use present(open), not compare.",
                      schema({"a": OBJECT, "b": OBJECT}, ["a"]), compare, family="magnifier", outline_refs=("a", "b")))

    agent = services.agent
    if agent is not None:
        async def contrast(ctx: ToolContext, args: dict) -> str:
            jpeg, labels, sizes, identities = await comparison(ctx, args)
            from ...screen import picture_shown

            await picture_shown(magnifier, jpeg, " and ".join(labels))
            question = ctx.question or "How do they differ?"
            prompt = (f'The picture shows two source crops side by side: on the left {labels[0]}, on the right {labels[1]}. '
                      f'Both have the same camera-pixel enlargement; physical sizes need their capture scales. '
                      f'The user asks: "{question}"\n'
                      'Compare them by what the picture shows: shape, outline, texture, colour, inner parts. Say what '
                      'you cannot tell. Reply with JSON only: {"answer": "2-4 plain sentences"}')
            option = hub.settings.vision
            from ..dispatch import query_answer

            result = await query_answer(agent.vision(option), option, [jpeg], prompt, max_output_tokens=400)
            answer = str((result.data or {}).get("answer") or "").strip()
            if not answer:
                raise ToolFailed(f"the vision model gave no comparison ({result.error or 'empty'})", next="ask again in a moment")
            from ...scale import size_words

            measured = "" if None in sizes else f" Sizes measured by the app: {size_words(sizes[0])} and {size_words(sizes[1])}."
            return f"What the vision model saw comparing them (shown side by side in the magnifier): {answer[:600]}{measured}"

        registry.add(Tool("contrast", "Compare two objects by looking at them: shows them side by side in the magnifier "
                          "and the vision model says how they differ (\"how are these two different?\", \"is this one "
                          "the same kind as that one?\"). Use compare to only show them.",
                          schema({"a": OBJECT, "b": OBJECT}, ["a"]), contrast, kind="query", inline_ms=6000,
                          family="magnifier", outline_refs=("a", "b")))

    async def focus_check(ctx: ToolContext, args: dict) -> str:
        from ...capture import captures

        obj = resolve_object(hub, args.get("object"), ctx)
        latest = hub.latest_info
        frame = captures(hub).get(latest.id) if latest else None  # the view now, taken before waiting
        best = await asyncio.get_running_loop().run_in_executor(None, sharpest_frame, hub, obj, 4.0)
        if best is None or frame is None or latest.epoch != obj.anchor_epoch:
            raise ToolFailed("I can't see it steadily enough to judge focus",
                             next="hold the slide still for a second, then ask again")
        from ...capture import decoding

        with decoding(hub, frame, "hold", owner="the focus check") as held:  # its decode is counted (invariant 3)
            gray = held.gray()
            r = padded_rect(object_box_in(obj, latest), 0.0, gray.shape[1], gray.shape[0], min_side=16)
            now = region_sharpness(gray[r[1]:r[3], r[0]:r[2]])
        ratio = now / best[2] if best[2] else 1.0
        if ratio >= 0.8:
            return f"{describe(obj)} is about as sharp as it has been in the last few seconds"
        return (f"{describe(obj)} is softer than a moment ago ({ratio:.0%} of its best); "
                "turn the fine focus slowly until its edges are crisp")

    registry.add(Tool("focus_check", "Judge whether an object is in focus now, compared with the last few seconds.",
                      schema({"object": OBJECT}), focus_check, family="magnifier", outline_refs=("object",)))

    async def count(ctx: ToolContext, args: dict) -> str:
        """What is outlined with that name (cells the cell finder outlined count as "cells"); when
        nothing is, every one is found and outlined first, then counted."""
        from ...labels import label_key

        label = str(args.get("label") or "").strip().lower()
        if not label:
            raise ToolFailed("count what?", next=None)  # no next: the model's own call; it corrects it
        key = label_key(label)
        if key in ("cell", "cells") or args.get("of"):
            # Cells: the population the cell finder made, counted as it is (contract 9): instances
            # segmented in a stated region, never a segmentation started here.
            from ...population import first_segmented, layer_of, resolve

            population = resolve(hub, args.get("of"), ctx)
            if population is not None:
                n = len(population.live())
                verified, total = layer_of(hub).verified_count() if layer_of(hub).shown == population.id else (0, n)
                placed = f"; {verified} verified in place now" if layer_of(hub).shown == population.id else ""
                log_measurement(services, ctx, "count", label=label, n=n, population=population, revision=population.revision,
                                segmented=first_segmented(population),
                                how=("the members of a population (the specialist's instances)" if not population.revision else
                                     f"the members of a population as the user corrected it (revision {population.revision}): "
                                     "the specialist's instances, less those removed, merged ones as one"))
                return f"{n} ({population.title}, {population.id}: {population.count_words()}{placed})"
            # Nothing segmented yet: count never starts a segmentation (contract 9); it says what could.
            from ...operations import OPERATIONS
            from ...ticket import chosen

            choice = chosen("cells")
            if choice:  # the request chose a cell finder: only what it made counts (ADR 0011)
                raise ToolFailed(f"no cells have been segmented with {choice.get('label') or choice['implementation']} here yet",
                                 next="outline the cells with it first, then count them")
            from ...population import nothing_here

            raise ToolFailed(nothing_here(hub), next="outline the cells first, then count them")

        def matching():
            info = hub.latest_info
            return [o for o in hub.scene.objects.values() if o.trial is None and in_view(o, info)
                    and o.kind in ("highlight", "part") and o.label and (label in o.label.lower() or label_key(o.label) == key)]

        objects = matching()
        found_now = ""
        just_outlined = False
        if not objects and services.agent is not None and hasattr(services.agent, "look_in"):
            from ...finder import Finder, FindRef

            found = await Finder(hub, services.agent, services.board, services.segment_all).find(FindRef(label, quantity="all"), ctx)
            ctx.objects.extend(o.id for o in found.objects if o.id not in ctx.objects)
            objects, found_now = matching() or found.objects, " (just outlined)"
            just_outlined = True
        n = len(objects)
        log_measurement(services, ctx, "count", label=label, n=n, objectIds=[o.id for o in objects], justOutlined=just_outlined,
                        how="the outlines with that name in view now")
        shown_again = ""
        if just_outlined and found.objects:  # outlined now while the outlines are hidden: shown again, and said
            from .present import reshow_outlines

            shown_again = await reshow_outlines(services, ctx)
        return f"{n} {label}{'s' if n != 1 and not label.endswith('s') else ''} in view{found_now if n else ''}{shown_again}"

    registry.add(Tool("count", "Count the outlined objects with a label (if none are outlined yet, it outlines them first "
                      "by looking). Cells are counted from the population the cell finder made (it never segments: with "
                      "none, use outline_cells first).",
                      schema({"label": {"type": "string", "description": "what to count"},
                              "of": {"type": "string", "description": "a population id (e3) or \"@n\", to count its members"}},
                             ["label"]), count,
                      kind="query", inline_ms=4000, family="measuring"))
