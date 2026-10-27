"""Checking an outline the app made, and outlining again when it is wrong (C2; option
OUTLINE_CHECK; docs/design/2026-10-05-acting-layer.md "Verify, then escalate").

An outline from a box is a guess twice over: the vision model's box, then the outliner's mask
from it. After the app outlines something (the outline is already on screen; nothing waits for
this), the fast vision model is shown the view with the outline drawn and a close-up (as for
naming a click, agent.identify_images) and the name it is meant to be, and judges: it fits, it
is too big, too small, or the wrong thing; when it does not fit it gives a box for the thing.
With "fix", the app outlines again from that box on the same frame and checks once more; the new
outline is kept only if that check says it fits. The model judges; code only measures and keeps
the outline the model said fits (facts, not verdicts).

Several things of one name (going live G4, 2026-10-07: two stomata, both outlined): the check is
told how many outlines in its picture share the name and where this one was meant to be (the box it
was outlined from, else its own), so being on another one of them is not "wrong" by itself; and a
fix never puts an outline where another outline already is (`covered_by`) unless the outline as
drawn already covered that one as much (a fix that ends a doubling is kept): the outline stays as it
was, which is logged, and nothing on screen changes. A fix that does move an outline changes the
screen after its answer was given, so it is said where the request was made (`tell`; ADR 0006).

Every check is logged ("outline-check": verdict, why, ms, and what was done).
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable
from . import execution

FITS = ("yes", "too_big", "too_small", "wrong", "unclear")
# Two outlines are of one thing when their masks overlap this much: agent._same_thing's bar, which
# outline_seen uses to reuse an outline instead of drawing a second one. Two stomata side by side
# overlap 0; two outlines of one stoma from two prompts overlap far past it.
SAME_IOU = 0.4
# ...or when one's centre lies inside the other and neither is much smaller (a part inside its
# whole, a nucleus in its cell, is not the same thing: _same_thing's 0.25 as well).
PART_SHARE = 0.25
# The check waits at most this long for the tool run that made the outline to finish (one highlight of two
# stomata makes both, 0.4 s apart on 2026-10-07), so it is told of the outlines of the same name made beside
# it. A request's steps are waited for 1.5 s (inline_ms) before its answer; nothing waits for the check.
MAKER_WAIT_S = 2.0

CHECK = """You are shown a microscope view with one thing outlined in magenta, and a close-up of it.
The outline is meant to be: "{label}"{asked}.
Does the magenta outline follow that thing's own edge?
- "yes": it outlines that thing, roughly along its edge.
- "too_big": it covers that thing and much more around it.
- "too_small": it covers only part of that thing.
- "wrong": it outlines something else.
- "unclear": the picture does not let you tell.
When it is not "yes", also give the thing's box in the FIRST picture (the whole view), as
[ymin, xmin, ymax, xmax] from 0 to 1000, or null if you cannot see it.{several}
Reply with JSON only: {{"fits": "...", "why": "a few words", "box_2d": [ymin, xmin, ymax, xmax] or null}}"""
# Only when other outlines share its name (G4): which one of them this is, by its place.
SEVERAL = ('\n{n} outlines in this view are named "{label}"; this one is meant to be the one at {box} in the first '
           'picture, not another of them.')


def prompt(label: str, question: str = "", several: tuple[int, list] | None = None) -> str:
    """`several`: (how many outlines have this name, this one's box [ymin, xmin, ymax, xmax] 0-1000 of
    the first picture), when others share its name."""
    asked = f' (the user asked: "{question[:160]}")' if question else ""
    more = SEVERAL.format(n=several[0], label=label[:60], box=several[1]) if several else ""
    return CHECK.format(label=label[:60] or "the thing the user asked about", asked=asked, several=more)


def verdict(data: dict | None) -> tuple[str, str, list | None]:
    """(fits, why, box) from the model's reply; "unclear" when it said nothing usable."""
    data = data or {}
    fits = str(data.get("fits") or "").strip().lower().replace(" ", "_")
    box = data.get("box_2d")
    if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
        box = None
    return (fits if fits in FITS else "unclear"), str(data.get("why") or "")[:160], box


# The reply is about 60 tokens, but a thinking model counts its thinking in the same cap: at 200 in all, 3 of 7 live
# checks on 2026-10-11 were cut off (MAX_TOKENS) and read "unclear". Its thinking gets a budget of its own in the cap.
CHECK_THINKING_TOKENS = 1000
CHECK_REPLY_TOKENS = 200
CUT_OFF = "the reply was cut off before it said"


async def check(vision, view_jpeg: bytes, close_jpeg: bytes, label: str, question: str = "",
                several: tuple[int, list] | None = None) -> tuple[str, str, list | None, float]:
    t0 = time.perf_counter()
    result = await vision.ask([view_jpeg, close_jpeg], prompt(label, question, several),
                              max_output_tokens=CHECK_THINKING_TOKENS + CHECK_REPLY_TOKENS)
    fits, why, box = verdict(result.data)
    if result.data is None and getattr(result, "raw_text", ""):
        why = CUT_OFF  # said as what happened, never as the model's judgment
    return fits, why, box, (time.perf_counter() - t0) * 1000


def others_on(hub, oid: str, capture) -> list[tuple[str, str, list]]:
    """Every other outline (the app's and the user's) where it is on `capture`: (id, label, polygons
    in the capture's pixels). Same epoch or a bounded known rejoin, with shared capture geometry;
    unrelated epochs and saved views from another run are unknown. Shown or not: one being re-found
    still outlines its thing."""
    from .apply import geometry_reason
    from .capture import RUN
    from .epochs import follow_links

    if getattr(capture, "epoch", None) is None:
        return []  # another run's frame ids and motion offsets cannot locate this run's outlines
    latest = getattr(hub, "latest_info", None)
    now = getattr(latest, "epoch", None)
    links = getattr(hub, "epoch_links", None) if latest is not None else None
    epoch, dx, dy = follow_links(links, capture.epoch, float(capture.dx), float(capture.dy), now=now)
    out = []
    for o in list(hub.scene.objects.values()):
        if o.id == oid or o.kind != "highlight" or o.trial is not None or not o.polygons:
            continue
        if o.anchor_epoch is None:
            continue
        other_epoch, ox, oy = follow_links(links, o.anchor_epoch, *o.anchor_offset, now=now)
        if other_epoch != epoch:
            continue
        geometry = getattr(o, "anchor_geometry", None)
        if geometry is not None and (geometry.run != RUN or geometry_reason(capture, geometry, geometry.objective)):
            continue  # a translation does not relate different camera sizes, eyepiece scales or objectives
        # Unknown geometry on a legacy outline of this known epoch is not evidence that its
        # occupied location is empty. Retain it conservatively; never invent a scale or an epoch link.
        sx, sy = dx - ox, dy - oy
        out.append((o.id, o.label or "", [[[x + sx, y + sy] for x, y in poly] for poly in o.polygons]))
    return out


def overlap(polygons, theirs) -> tuple[float, bool]:
    """(their masks' IoU, whether `polygons` outlines the same thing as `theirs`): they overlap by SAME_IOU
    or more, or one's centre lies inside the other and neither is much smaller (PART_SHARE)."""
    import cv2
    import numpy as np

    if not polygons or not theirs:
        return 0.0, False
    rings = [np.asarray(p, np.float32).reshape(-1, 2) for p in [*polygons, *theirs]]
    x0, y0 = np.floor(np.concatenate(rings).min(0)).astype(int) - 1
    x1, y1 = np.ceil(np.concatenate(rings).max(0)).astype(int) + 2

    def mask(polys):
        m = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.fillPoly(m, [np.round(np.asarray(p, np.float32).reshape(-1, 2) - (x0, y0)).astype(np.int32) for p in polys], 1)
        return m.astype(bool)

    new, old = mask(polygons), mask(theirs)
    a, b = int(new.sum()), int(old.sum())
    if not a or not b:
        return 0.0, False
    iou = float((new & old).sum() / (new | old).sum())
    if iou >= SAME_IOU:
        return iou, True
    if min(a, b) < PART_SHARE * max(a, b):
        return iou, False  # one much smaller inside the other: a part of it, not the same thing

    def centre(m):
        ys, xs = np.nonzero(m)
        return int(round(ys.mean())), int(round(xs.mean()))

    return iou, bool(old[centre(new)] or new[centre(old)])


def covered_by(polygons, others: list[tuple[str, str, list]], drawn=None) -> str | None:
    """The other outline whose thing `polygons` (a fix) would outline too, or None. One the outline as
    `drawn` already outlined, at least as much as the fix would, is not counted: a fix that ends or
    lessens a doubling is not refused for it (the partner's review of G4, SHOULD-FIX 2)."""
    for oid, _label, theirs in others:
        iou, covers = overlap(polygons, theirs)
        if not covers:
            continue
        before, covered = overlap(drawn, theirs) if drawn else (0.0, False)
        if covered and iou <= before:
            continue
        return oid
    return None


def in_picture(polygons, crop) -> bool:
    """`polygons`' centre lies inside `crop` (the first picture the check is shown)."""
    xs = [x for poly in polygons for x, _ in poly]
    ys = [y for poly in polygons for _, y in poly]
    if not xs:
        return False
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    return crop.x <= cx <= crop.x + crop.width and crop.y <= cy <= crop.y + crop.height


def box_1000(polygons, crop) -> list[int]:
    """`polygons`' box as [ymin, xmin, ymax, xmax], 0-1000 of the first picture (the crop)."""
    xs = [x for poly in polygons for x, _ in poly]
    ys = [y for poly in polygons for _, y in poly]
    return frame_box_1000((min(xs), min(ys), max(xs), max(ys)), crop)


def frame_box_1000(box, crop) -> list[int]:
    """A box (x0, y0, x1, y1) in frame pixels as [ymin, xmin, ymax, xmax], 0-1000 of the crop."""
    fx = lambda x: int(round(min(1000.0, max(0.0, (x - crop.x) / max(1, crop.width) * 1000))))
    fy = lambda y: int(round(min(1000.0, max(0.0, (y - crop.y) / max(1, crop.height) * 1000))))
    x0, y0, x1, y1 = box
    return [fy(y0), fx(x0), fy(y1), fx(x1)]


COVERED = object()  # a fix's write declined: another outline is there now

# What a fix that moved an outline is said as (plain words, no ids).
REDRAWN = {"wrong": "the old one was on something else", "too_big": "the old one took in too much around it",
           "too_small": "the old one covered only part of it"}


def place_words(polygons, crop) -> str:
    """Where `polygons` is in the view, in plain words ("at the top left", "in the middle")."""
    xs = [x for poly in polygons for x, _ in poly]
    ys = [y for poly in polygons for _, y in poly]
    fx = ((min(xs) + max(xs)) / 2 - crop.x) / max(1, crop.width)
    fy = ((min(ys) + max(ys)) / 2 - crop.y) / max(1, crop.height)
    col = "left" if fx < 1 / 3 else ("right" if fx > 2 / 3 else "")
    row = "top" if fy < 1 / 3 else ("bottom" if fy > 2 / 3 else "")
    where = " ".join(w for w in (row, col) if w)
    return f"at the {where}" if where else "in the middle"


# A verdict that the outline does not fit, in words, when the check gave no reason of its own.
POOR_FIT = {"too_big": "it covers more than the thing", "too_small": "it covers only part of the thing",
            "wrong": "it may be around something else"}


def redrawn_words(fits: str, label: str, namesakes: int = 0, where: str = "", verified: bool = True) -> str:
    """What is said of a fix that moved an outline: which one (with others of its name, by where it is now),
    why, and, when its new place is not verified yet, that it shows again once it is."""
    named = label and not label.strip().lower().startswith("not sure")  # an unsure name is no name to say
    if named and namesakes:
        what = f"one of the {namesakes + 1} {label} outlines, the one {where or 'in the view'} on the checked view"
    else:
        what = f"the {label} outline" if named else "an outline"
    shows = "" if verified else "; it shows again once its place is checked"
    return f"Redrew {what}: {REDRAWN.get(fits, 'the old one did not fit')}{shows}."


class OutlineCheck:
    """Watches the scene for outlines the app made and checks each once (and fixes, with "fix")."""

    def __init__(self, hub, agent, outline_again: Callable[..., Awaitable[list | None]],
                 log: Callable[[dict], None] = lambda r: None, tell: Callable[[object, str], None] = lambda request, text: None) -> None:
        self.hub = hub
        self.agent = agent
        self.outline_again = outline_again  # (capture, box_px, label, made_by) -> polygons or None
        self.log = log
        self.tell = tell  # (the request that made the outline, words): said where it was made (app.said_late)
        self.checked: set[str] = set()
        self.tasks: set[asyncio.Task] = set()
        hub.scene.on_change.append(self.changed)

    def mode(self) -> str:
        return getattr(self.hub.settings, "outline_check", "off")

    def changed(self, op: str, ids) -> None:
        from .options import unrecorded_reads

        # The check is its own capability (outline-check): what it reads is not the tool's that made the outline.
        with unrecorded_reads():
            self._changed(op, ids)

    def _changed(self, op: str, ids) -> None:
        if op != "add" or self.mode() == "off":
            return
        from .capture import captures
        from . import ticket as tickets

        for oid in ids:
            obj = self.hub.scene.objects.get(oid)
            if obj is None or oid in self.checked or obj.kind != "highlight" or not str(obj.source).startswith("ai"):
                continue
            if str(obj.source).startswith("ai:measured"):
                continue  # placed from a measurement (contract 9): its outline is the specialist's own mask
            self.checked.add(oid)
            # The view it was made on, held now, while the request that made it still holds it
            # (the check runs seconds later; ADR 0010). Part of that request: its ticket tracks it.
            store = captures(self.hub)
            capture = store.get(obj.origin if obj.origin is not None else obj.anchor_frame)
            try:
                hold = store.hold(capture, "analysis", "outline", f"checking {oid}") if capture is not None else None
            except Exception:
                hold = None  # memory full: the check is skipped (logged when it runs)
            request = tickets.current()
            from .brain.registry import CURRENT_RUN

            making = CURRENT_RUN.get()  # the tool run making it (a highlight of two things): it may make more beside it
            try:
                task = asyncio.get_running_loop().create_task(self.run(oid, hold, request, getattr(making, "task", None)))
            except RuntimeError:  # no loop (a test building a scene): nothing to check
                if hold is not None:
                    hold.release()
                return
            if request is not None:
                request.track(task)
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    @execution.traced("outline-check")
    async def run(self, oid: str, hold=None, request=None, maker=None) -> None:
        try:
            await self._run(oid, hold, request, maker)
        finally:
            if hold is not None:
                hold.release()

    async def _run(self, oid: str, hold, request=None, maker=None) -> None:
        execution.bind(object_id=oid)
        if maker is not None and not maker.done() and maker is not asyncio.current_task():
            await asyncio.wait([maker], timeout=MAKER_WAIT_S)  # its other outlines exist by then (several of one name)
        import numpy as np

        from .agent import identify_images
        from .apply import apply, place_outline
        from .marks import fov_crop

        obj = self.hub.scene.objects.get(oid)
        if obj is None:
            return
        if hold is None:
            self.log({"kind": "outline-check", "objectId": oid, "done": "skipped: the view it was made on is no longer held"})
            return
        capture = hold.capture
        revision = obj.revision
        # The outline as it was drawn on that view (re-anchoring and following move `polygons`).
        drawn = obj.origin_polygons if obj.origin_polygons is not None else obj.polygons
        image = await asyncio.to_thread(capture.bgr)
        if image is None:
            return
        h, w = image.shape[:2]
        info = capture
        crop = fov_crop(getattr(info, "fov", None), w, h)
        option = self.hub.settings.fast if self.hub.settings.fast != "off" else self.hub.settings.vision
        execution.note(selected=option, selection="settings")
        vision = self.agent.vision(option)
        label = obj.label or ""

        def images(polygons):
            pts = np.concatenate([np.array(p, np.float32) for p in polygons]) if polygons else np.zeros((0, 2), np.float32)
            if not len(pts):
                return None
            box = (float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max()))
            return identify_images(image, polygons, crop, box)

        shown = await asyncio.to_thread(images, drawn)
        if shown is None:
            return
        from .labels import label_key

        # Other outlines of the same name in the picture the check is shown (two stomata): it is told which one
        # this is meant to be, by the box it was outlined from (the look's), else where it is.
        key = label_key(label)
        namesakes = [o for o in others_on(self.hub, oid, capture) if key and label_key(o[1]) == key and in_picture(o[2], crop)]
        meant = getattr(obj, "origin_box", None) if obj.origin in (None, capture.id) else None
        several = (len(namesakes) + 1, frame_box_1000(meant, crop) if meant else box_1000(drawn, crop)) if namesakes else None
        fits, why, box, ms = await check(vision, *shown, label, several=several)
        record = {"kind": "outline-check", "objectId": oid, "label": label, "fits": fits, "why": why, "ms": round(ms),
                  "mode": self.mode(), "frameId": capture.id, "model": option}
        if several:
            record["sameName"] = [o[0] for o in namesakes]
        # What the check said is part of how the outline was made (known.chain), kept on it whatever is done next.
        checked = {"option": option, "fits": fits, "why": why, "at": time.time()}
        current = self.hub.scene.objects.get(oid)
        if current is not None and current.revision == revision:
            current.checked = checked
        if fits in ("yes", "unclear") or self.mode() != "fix" or box is None:
            self.log({**record, "done": "kept"})
            await self._unresolved(oid, revision, checked, "not outlined again")
            return
        # Outline again from the model's box on the same frame (its coordinates are the outline's).
        x0 = crop.x + box[1] / 1000 * crop.width
        y0 = crop.y + box[0] / 1000 * crop.height
        x1 = crop.x + box[3] / 1000 * crop.width
        y1 = crop.y + box[2] / 1000 * crop.height
        before = getattr(obj, "made_by", None)
        again_made = await self.outline_again(capture, (x0, y0, x1, y1), label, before)
        # What drew it again: its polygons and its own made-by (app.outline_again); a plain list from older callers.
        polygons, redrawn_by = ((again_made.get("polygons"), again_made.get("madeBy")) if isinstance(again_made, dict)
                                else (again_made, None))
        repair_producer = execution.producer()
        if not polygons:
            self.log({**record, "done": "kept: outlining again gave nothing"})
            await self._unresolved(oid, revision, checked, "outlining again gave nothing")
            return
        if self._covers(oid, capture, polygons, record, drawn):
            execution.finish("retained", policy="repair-would-cover")
            await self._unresolved(oid, revision, checked, "the fix would cover another outline")
            return
        shown = await asyncio.to_thread(images, polygons)
        several = (len(namesakes) + 1, [int(round(v)) for v in box]) if namesakes else None  # now meant: the model's box
        again, why2, _, ms2 = await check(vision, *shown, label, several=several) if shown else ("unclear", "", None, 0.0)
        if again == "yes":
            # The repair was made on the capture: it is written with that capture's anchor facts,
            # placed where verified now (contract 3), and only if the outline is still there and
            # unchanged since the check began (apply.py).
            def write(placement):
                # Checked again where nothing awaits before the write: an outline made since may be there now.
                if self._covers(oid, capture, placement.polygons, {**record, "again": again, "againWhy": why2}, drawn):
                    return COVERED
                anchor = placement.anchor
                from .scene import anchor_geometry

                fixed = {"polygons": placement.polygons, "anchor_frame": anchor.id, "anchor_epoch": anchor.epoch,
                         "anchor_offset": (anchor.dx, anchor.dy), "origin": capture.id, "origin_polygons": polygons,
                         "anchor_geometry": anchor_geometry(anchor),
                         "origin_box": (min(x for poly in polygons for x, _ in poly),
                                        min(y for poly in polygons for _, y in poly),
                                        max(x for poly in polygons for x, _ in poly),
                                        max(y for poly in polygons for _, y in poly))}
                if placement.template is not None:  # what re-finds it after the slide moves follows the new outline
                    fixed["template"], fixed["template_origin"] = placement.template, placement.template_origin
                if not placement.verified:
                    fixed["track"], fixed["lost_since"] = "refinding", time.time()
                current = self.hub.scene.objects.get(oid)
                if current is not None:  # said with its name's account (known.py): its outline is not the first one
                    fixed["naming"] = {**(current.naming or {}), "repaired": {"at": time.time()}}
                    # What drew it now is what made it (known.chain: "drawn by"), and the check says what drew it first.
                    fixed["checked"] = {**checked, "redrawn": True, "again": again,
                                        "before": (before or {}).get("implementation")}
                    if redrawn_by:
                        fixed["made_by"] = {**redrawn_by, "repairOf": (before or {}).get("execution")}
                return self.hub.scene.revise(oid, **fixed)

            done = await apply(self.hub, write, scope="outline:app", capture=capture, target=(oid, revision),
                               place=lambda: place_outline(self.hub, capture, polygons))
            if done.ok and done.value is COVERED:
                execution.finish("retained", policy="repair-would-cover")
                await self._unresolved(oid, revision, checked, "the fix would cover another outline")
            elif done.ok:
                execution.finish(producer=repair_producer, policy="accepted-repair")
                self.log({**record, "done": "replaced", "again": again, "againWhy": why2, "againMs": round(ms2),
                          "placed": done.placement.how if done.placement else None,
                          "redrawnWith": (redrawn_by or {}).get("implementation"),
                          "redrawnExecution": (redrawn_by or {}).get("execution")})
                # The screen changed after the request's answer was given: said where it was asked (ADR 0006).
                self.tell(request, redrawn_words(fits, label, len(namesakes), place_words(polygons, crop),
                                                 verified=bool(done.placement is None or done.placement.verified)))
            else:
                execution.finish("retained", policy="repair-not-applied")
                self.log({**record, "done": f"kept: {done.reason}", "again": again, "againWhy": why2})
                await self._unresolved(oid, revision, checked, str(done.reason))
        else:
            execution.finish("retained", policy="repair-not-accepted")
            self.log({**record, "done": "kept: the new outline did not fit either", "again": again, "againWhy": why2})
            await self._unresolved(oid, revision, checked, "the new outline did not fit either")

    async def _unresolved(self, oid: str, revision: int, checked: dict, kept: str) -> None:
        """The check said the outline does not fit and nothing replaced it: that verdict is kept on the outline and shown
        (the studio draws it as uncertain, its label says why) and filed in Activity, so the screen never presents it as
        fitting (live 2026-10-11: 9 of 18 outlines the check called wrong stayed on screen as if they fit). Written
        through apply, as the repair is: only while the outline is the one that was checked (a later revision has its own
        check) and its request stands."""
        from .apply import apply

        current = self.hub.scene.objects.get(oid)
        if current is None or checked.get("fits") not in POOR_FIT:
            return
        def write(_placement):
            return self.hub.scene.update(oid, checked={**checked, "kept": kept})

        done = await apply(self.hub, write, scope="outline:app", target=(oid, revision))
        if not done.ok:
            return
        from .activity import activity

        what = f"the {current.label} outline" if current.label and not current.label.lower().startswith("not sure") else "an outline"
        activity(self.hub, "outline-check", f"The check says {what} may not fit: {checked.get('why') or POOR_FIT[checked['fits']]}",
                 details={"ids": {"object": oid}, "fits": checked["fits"], "kept": kept})

    def _covers(self, oid: str, capture, polygons, record: dict, drawn=None) -> bool:
        """The fix would outline what another outline already does (and the outline as `drawn` did not): the
        outline stays as it was, and that is logged (and as an override: code declines the model's box)."""
        other = covered_by(polygons, others_on(self.hub, oid, capture), drawn)
        if other is None:
            return False
        from .brain.intent import log_override

        self.log({**record, "done": f"kept: the fix would cover {other}", "covers": other})
        log_override(self.log, "check-fix-would-cover", objectId=oid, covers=other, fits=record.get("fits"),
                     why=record.get("why"))
        return True
