"""Seeing better and snapshots: a clearer picture made from real frames (`enhance`), and a picture
of the view with its outlines to keep (`snapshot`). Both show their picture in the magnifier,
beside the live close-up, labelled with how it was made; neither invents a pixel.
Design: docs/design/2026-10-06-capability-menu.md ("Seeing better", "Snapshots")."""
from __future__ import annotations

import asyncio
import json
import time

import cv2

from ... import enhance as methods
from ...day.sid import session_of
from ...enhance import HOWS, decode as _decode  # which frames, and how, are enhance.py's (a researcher's hands too)
from ...magnifier import MAGNIFIER_ASPECT, object_box_in, padded_rect
from ...snapshot import draw
from ..registry import Registry, Tool, ToolContext, ToolFailed, schema
from .common import OBJECT, describe, resolve_object


def choose_window(observation: dict, mine: set, pointed: str | None, named: bool = False):
    """The window the user was looking at, from the request's own pin (its observation wire): the one
    showing what was pointed at (or named), else the browser's last focused or pointed content window,
    else the only one open. (row, "") or (None, why) for the view itself."""
    sources = {s["key"]: s for s in observation.get("sources", [])}
    windows = [row for row in observation.get("items", [])
               if row["id"] in mine and row.get("open") and row.get("visible")]
    if not windows:
        return None, ""

    def shows(row):
        return ((sources.get(row.get("sourceKey")) or {}).get("identity") or {}).get("object")

    matching = [row for row in windows if pointed is not None and shows(row) == pointed]
    if named and not matching:
        return None, ""  # the object named has no window open: the view, cropped to it
    interaction = observation.get("interaction") or {}
    focused = next((value.get("presentationId") for value in (interaction.get("lastContentPointer"),
                                                               interaction.get("lastContentFocus"))
                    if isinstance(value, dict) and value.get("presentationId")), None)
    choices = matching or [row for row in windows if row["id"] == focused] or windows
    if len(choices) != 1:
        return None, f"{len(choices)} windows were open and none was pointed at, so it is of the whole view"
    return choices[0], ""


def shown_region(row: dict, crop) -> tuple[int, int, int, int]:
    """The part of a live object window's crop that was visible, by its reported zoom and pan: the inverse of
    the window's source-to-content map over its content box, in camera-frame pixels."""
    x0, y0, x1, y1 = crop
    view, content = row.get("viewTransform") or {}, row.get("contentRect")
    affine, size = view.get("sourceToContent"), view.get("originalSize")
    if not (affine and size and content) or len(affine) != 6 or not all(size):
        return tuple(crop)
    a, b, c, d, e, f = affine  # x' = a x + c y + e; y' = b x + d y + f (a DOMMatrix)
    det = a * d - b * c
    if abs(det) < 1e-9:
        return tuple(crop)

    def back(px, py):
        px, py = px - e, py - f
        return (d * px - c * py) / det, (-b * px + a * py) / det

    corners = [back(px, py) for px, py in ((0, 0), (content[2], 0), (0, content[3]), (content[2], content[3]))]
    u0, u1 = max(0.0, min(u for u, _ in corners)), min(float(size[0]), max(u for u, _ in corners))
    v0, v1 = max(0.0, min(v for _, v in corners)), min(float(size[1]), max(v for _, v in corners))
    if u1 - u0 < 2 or v1 - v0 < 2:
        return tuple(crop)
    sx, sy = (x1 - x0) / size[0], (y1 - y0) / size[1]
    return int(x0 + u0 * sx), int(y0 + v0 * sy), int(round(x0 + u1 * sx)), int(round(y0 + v1 * sy))


def add_seeing_tools(registry: Registry, services) -> None:
    hub, magnifier = services.hub, services.magnifier
    if magnifier is None:
        return

    def region(result: methods.Enhanced, shape, newest, fov, obj) -> tuple[tuple[int, int, int, int], str]:
        """Where to cut the result (its own coordinates): the object with room around it, else the
        eyepiece disc. And a note when the object could not be found in these frames."""
        oh, ow = result.image.shape[:2]
        ox, oy = result.origin
        note = ""
        box = None
        if obj is not None:
            if obj.anchor_epoch == newest[2]:
                x0, y0, x1, y1 = obj.bbox
                sx, sy = newest[0] - obj.anchor_offset[0], newest[1] - obj.anchor_offset[1]
                box = padded_rect((x0 + sx, y0 + sy, x1 + sx, y1 + sy), 0.6, shape[1], shape[0], min_side=96,
                                  aspect=MAGNIFIER_ASPECT)
            else:
                note = f"not zoomed to {describe(obj)}: the slide moved since these frames"
        if box is None and fov is not None:
            pad = fov.radius * 1.02
            box = (int(fov.cx - pad), int(fov.cy - pad), int(fov.cx + pad), int(fov.cy + pad))
        if box is None:
            return (0, 0, ow, oh), note
        x0, y0 = max(0, box[0] - ox), max(0, box[1] - oy)
        x1, y1 = min(ow, box[2] - ox), min(oh, box[3] - oy)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return (0, 0, ow, oh), note
        return (x0, y0, x1, y1), note

    async def enhance(ctx: ToolContext, args: dict) -> str:
        how = str(args.get("how") or "average").strip().lower()
        how = {"stack": "stack", "focus stack": "stack", "focus_stack": "stack", "sharp": "sharpest",
               "best": "sharpest", "avg": "average", "mean": "average"}.get(how, how)
        if how not in HOWS:
            raise ToolFailed(f"enhance can {', '.join(HOWS)}, not {how!r}",
                             next=None)  # no next: the model's own call; it corrects it
        obj = resolve_object(hub, args.get("object"), ctx) if args.get("object") else None
        plan = methods.live_plan(hub, how, services.sweep)
        latest = hub.latest_info

        def make():
            # Only its words and the JPEG leave the thread: the picture's pixels are let go here, inside the
            # reservation, never kept while the magnifier is waited on (contract 1).
            result = plan.work()
            cut, note = region(result, (latest.height, latest.width), plan.newest, plan.fov, obj)
            return result.used, result.words, methods.jpeg_of(result, cut), note

        from ...capture import captures

        # Its own measured peak, of the whole frame, in the user's analysis allowance (invariant 3; enhance.Plan.need).
        with captures(hub).memory.reserve("analysis", plan.need, f"an enhanced picture ({how})"):
            used, words, jpeg, note = await asyncio.to_thread(make)
        from ...screen import picture_shown

        await picture_shown(magnifier, jpeg, plan.label.format(n=used), object_id=obj.id if obj is not None else None)
        return f"{words}; shown in the magnifier" + (f" ({note})" if note else "")

    registry.add(Tool("enhance", "Make a clearer picture of the view from real camera frames and show it in the magnifier. "
                      "how: \"average\" (the default) averages the still frames of the last second or so, aligned, "
                      "for less noise and grain; \"sharpest\" picks the sharpest recent frame; \"stack\" focus-stacks "
                      "the frames from the last turn of the focus knob, each place taken from the frame where it was "
                      "sharpest, so more of a thick specimen is in focus at once. Use it when the user asks to clean up, "
                      "sharpen or enhance the picture, reduce the noise, or stack the focus. Nothing is invented: every "
                      "pixel comes from the camera. object: zoom the result to that outline.",
                      schema({"how": {"type": "string", "description": "average | sharpest | stack"},
                              "object": OBJECT}),
                      enhance, inline_ms=4000, family="seeing", outline_refs=("object",)))

    async def snapshot(ctx: ToolContext, args: dict) -> str:
        from ..registry import actor_of

        actor = actor_of(ctx)
        from ...paths import Paths
        from uuid import uuid4

        def enabled(name):
            value = args.get(name, True)
            return not (value is False or str(value).strip().lower() in ("false", "no", "0", "off"))

        overlays = enabled("overlays")
        labels = overlays and enabled("labels")
        # What the user was looking at when they asked (live 2026-10-10 20:31: "a snapshot of the zoomed in eye" took
        # the whole view and showed it where they were not looking): the window of theirs showing it, from the
        # request's own pin, or the object they named; else the view as it is now.
        named = resolve_object(hub, args["object"], ctx) if args.get("object") else None
        pin, row, view_why = getattr(ctx, "request_snapshot", None), None, ""
        if pin is not None and getattr(services, "presentations", None) is not None:
            asked = getattr(ctx, "when_asked", None)
            pointed = (named.id if named is not None else getattr(ctx, "pointed", None)
                       or ((asked.selected or asked.magnified) if asked is not None else None))
            mine = {w.id for w in services.presentations.list(pin.observation.client.workspace_id)}
            row, view_why = choose_window(pin.observation.wire(), mine, pointed, named=named is not None)
        elif named is None and getattr(services, "presentations", None) is None:
            # No windows, only the one magnifier: what it showed when the user asked is what they were looking at.
            asked = getattr(ctx, "when_asked", None)
            shown = hub.scene.objects.get(asked.magnified) if asked is not None and asked.magnified else None
            named = shown if shown is not None and magnifier is not None and magnifier.open else None
        shows = {}
        if row is not None:
            shows = next((s.get("identity") or {} for s in pin.observation.wire().get("sources", [])
                          if s.get("key") == row.get("sourceKey")), {})
            if not shows.get("object"):
                kept = (shows.get("resource") or {}).get("id")
                return (f"that window ({row['id']}) shows a saved picture{f' ({kept})' if kept else ''}, already kept "
                        "with its source: nothing new was taken")
        target = hub.scene.objects.get(shows["object"]) if row is not None else named
        if row is not None and target is None:
            raise ToolFailed("the outline that window showed is gone", next="ask for a snapshot of the view")
        selection = getattr(ctx, "camera_selection", None)
        if target is not None and selection is not None and (selection.capture is not None
                                                             or selection.requested_frame is not None):
            # The frame held for this request, as the user saw it when they asked: never a newer one.
            info = selection.capture
            frame = info if getattr(info, "jpeg", None) else (hub.source.get(info.id) if info is not None else None)
            if frame is None:
                raise ToolFailed("the picture you were looking at when you asked is no longer kept",
                                 next="ask again to take it of the view now")
        else:
            info = hub.best_frame(window_s=1.0)
            frame = hub.source.get(info.id) if info is not None else None
        if frame is None:
            from ...frames import no_picture
            why, next_step = no_picture(hub, "there is no live picture to take",
                                         "check the camera is connected and the view is live, then ask again")
            raise ToolFailed(why, next=next_step)
        crop = None
        if target is not None:
            # What its window shows: the object with room around it (live_sources), as far as the window's
            # zoom and pan let it be seen.
            crop = padded_rect(object_box_in(target, info), .15, info.width, info.height)
            if row is not None:
                crop = shown_region(row, crop)
        objects = [o for o in hub.scene.objects.values()]
        reading = hub.scale_reading() if hasattr(hub, "scale_reading") else None
        size_of = getattr(hub, "size_of", None)
        colour = getattr(hub.settings, "outline_color", "pink")
        day = getattr(services.day, "date", None) or time.strftime("%Y-%m-%d", time.localtime(info.wall_at))
        root = services.snapshots if services.snapshots is not None else Paths.from_env().snapshots
        folder = root / day  # today's folder; the session it was asked in is in its facts (ADR 0017)
        session = getattr(getattr(ctx, "asked", None), "session", None) or getattr(services.day, "sid", None)
        name = f"snapshot-{time.strftime('%H%M%S', time.localtime(info.wall_at))}-{uuid4().hex[:12]}-f{info.id}"

        from ...capture import reserving

        room = reserving(hub, info.width, info.height, "outline", "a snapshot")  # its decode and drawing (invariant 3)
        layer = getattr(hub, "layer", None)
        members = layer.drawable(info) if layer is not None and hasattr(layer, "drawable") else []

        def make():
            drawn = draw(_decode(frame.jpeg), info, objects, reading=reading, colour=colour, labels=labels, members=members,
                         overlays=overlays, region=crop,
                         size_of=size_of, frame_time=info.wall_at)
            ok_png, png = cv2.imencode(".png", drawn.image)
            ok_jpg, jpeg = cv2.imencode(".jpg", drawn.image, [cv2.IMWRITE_JPEG_QUALITY, 94])
            if not (ok_png and ok_jpg):
                raise ToolFailed("the picture could not be encoded", next="ask again in a moment")
            # Made with (made_with.py): the catalog entries behind the outlines it shows, carried inside the PNG, so
            # whoever opens it elsewhere is offered what made them. Never a blind comparison's unvoted model.
            from types import SimpleNamespace

            from ...made_with import of_objects

            shown_ids = {d.get("id") for d in drawn.outlines if d.get("id")}
            shown = [o for o in objects if o.id in shown_ids]
            if members and layer is not None and hasattr(layer, "population"):
                shown.append(SimpleNamespace(made_by=getattr(layer.population(), "made_by", None), trial=None))
            made_with = of_objects(shown, hub) if overlays else []
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{name}.png").write_bytes(png.tobytes())  # native evidence; explicit Share makes a portable copy
            if overlays:
                # Its clean picture too (the same region, nothing drawn): a snapshot can be opened again as a picture to
                # analyse (library.py), and outlines drawn on it would be taken for the specimen.
                clean = draw(_decode(frame.jpeg), info, [], reading=None, colour=colour, labels=False, members=[],
                             overlays=False, region=crop, size_of=size_of, frame_time=info.wall_at)
                ok_clean, clean_png = cv2.imencode(".png", clean.image)
                if ok_clean:  # beside it, in its own folder: the snapshot's picture is still the one PNG of its name
                    (folder / "clean").mkdir(exist_ok=True)
                    (folder / "clean" / f"{name}.png").write_bytes(clean_png.tobytes())
            meta = {"frameId": info.id, "wallAt": info.wall_at,
                    "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(info.wall_at)),
                    "made": ("a real camera frame; outlines, labels and scale bar drawn by the app" if overlays else
                             "a real camera frame, cropped to the eyepiece, without overlays"),
                    "crop": list(drawn.crop), "overlays": overlays, "labels": labels, "outlines": drawn.outlines,
                    "leftOut": drawn.left_out, "scale": drawn.scale, **({"session": session} if session else {}),
                    **({"madeWith": made_with} if made_with else {}),
                    **({"actor": actor} if actor else {})}  # who took it (ticket 66, S2): Data's "Made by"
            (folder / f"{name}.json").write_text(json.dumps(meta, indent=1, default=float))
            if services.keep is not None:  # a dataset being collected keeps its own copy
                for suffix in (".png", ".json"):
                    services.keep(folder / f"{name}{suffix}", "snapshots")
            return drawn, jpeg.tobytes()

        with room:
            drawn, jpeg = await asyncio.to_thread(make)
        made = getattr(services.day, "made", None)
        if made is not None:  # the session's own (its journal claims it): one of its sources, not a product (ADR 0022)
            try:
                made("snapshot", name, day=day, session=session)
            except Exception:
                pass  # the snapshot is kept whatever happens to its line in the journal
        n = len(drawn.outlines)
        what = [f"{n} outline{'s' if n != 1 else ''}" if n else "no outlines"]
        if n and labels:
            what.append("their labels")
        what.append(f"a {drawn.scale['um']} µm scale bar" if drawn.scale else "no scale bar (the scale is not set)")
        if not overlays:
            what = ["camera pixels only, without overlays"]
        from ...screen import picture_shown
        from ..registry import CURRENT_RUN, attach
        from . import present

        label = f"Snapshot: real frame, {n} outline{'s' if n != 1 else ''}" if overlays else "Snapshot: real frame without overlays"
        if target is not None:
            label = f"Snapshot of {describe(target)}: real frame" + ("" if overlays else " without overlays")
        if getattr(services, "presentations", None) is not None and ctx.client is not None and CURRENT_RUN.get() is not None:
            # Kept as a result by its own id, and opened beside the window it was taken from (a window, not the
            # one magnifier the user may not be looking at). "I don't see it" opens this id again.
            attach(picture_bytes=jpeg)
            entry = hub.resources.results.from_run(CURRENT_RUN.get(), ctx, hub, produced=True)
            if entry is None:
                on_screen_words = "; it could not be kept for display"
            else:
                opening = {"do": "open", "source": {"day": session_of(entry, entry["day"]), "id": entry["id"], "version": None, "picture": 0},
                           "title": label[:120]}
                if row is not None:
                    opening["relation"] = {"kind": "beside", "reference": row["id"]}
                try:
                    shown = await present._native_present(services, ctx, opening)
                    on_screen_words = f"; kept as {entry['id']}; {shown}"
                except ToolFailed as exc:  # kept all the same: its id opens it again
                    on_screen_words = f"; kept as {entry['id']}; not shown: {exc.said()}"
        else:
            try:
                await picture_shown(magnifier, jpeg, label)
                on_screen_words = " and shown in the magnifier"
            except ToolFailed as exc:  # saved all the same: the file is the snapshot
                on_screen_words = f"; {exc.said()}"
        try:
            where = (folder / f"{name}.png").relative_to(root.parent)
        except ValueError:
            where = folder / f"{name}.png"
        of = (f" of {describe(target)} as its window {row['id']} showed it" if row is not None
              else f" of {describe(target)}" if target is not None else "")
        out = (f"took a snapshot{of} (a real frame) with {', '.join(what[:-1])} and {what[-1]}" if len(what) > 1 else
               f"took a snapshot{of} (a real frame) with {what[0]}")
        if view_why:
            out += f" ({view_why})"
        # Saved with today's snapshots; its picture joins this session's Assets on its own (ADR 0022), never Made. Its file is
        # one step away there, not in the words (M1.5c S4).
        attach(values={"file": str(where)})
        out += "; saved with today's snapshots" + on_screen_words
        if drawn.left_out:
            out += " (not drawn: " + ", ".join(f"{k} {why}" for why, k in drawn.left_out.items()) + ")"
        return out

    registry.add(Tool("snapshot", "Take a picture of what the user is looking at, with the outlines, their labels and the "
                      "scale bar drawn on it, save it and show it. Use it when the user asks to take a picture, save or "
                      "keep the view, or a snapshot. It is of the window the user was looking at when they asked (a "
                      "live close-up: what it showed, on the frame of that moment), or of the object named, else of "
                      "the view; it opens beside that window and is kept by its own id (open that id again when they "
                      "cannot see it, never take another). For a clean, unannotated camera picture use "
                      "overlays=false: no masks, labels, scale bar or footer; provenance stays with the saved file. "
                      "labels=false alone leaves only the labels off.",
                      schema({"labels": {"type": "boolean", "description": "draw the labels (default true)"},
                              "overlays": {"type": "boolean", "description": "draw app overlays (default true); false keeps camera pixels only"},
                              "object": OBJECT}),
                      snapshot, inline_ms=2500, family="snapshots", outline_refs=("object",)))
