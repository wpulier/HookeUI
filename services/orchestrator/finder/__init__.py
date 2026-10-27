"""The Finder: what a request means ("the nuclei inside that cell", "all the red cells", "the
stoma") becomes outlines (CONTEXT.md "Finder").

`Finder.find(ref, ctx)` takes a `FindRef`:
- what: a name or appearance ("nucleus", "small dark dots");
- inside: an outline the things must be in (resolved on the Board: an id, "that", a label);
- quantity: "one", "all", or "requested" (the vision model follows the original request);
- and returns `Found`: the outlines (new or reused), how they were found, and why not when
  nothing was.

Ways, tried in order (fixed in the code, no setting chooses among them; the capability list
shows each with its models: contract/capabilities.json find, find-like, find-cells):
- `board`: what is already outlined with that name (inside the scope);
- `sam3.1-all`: every thing like an outlined one (`like`, an exemplar), from SAM 3.1's matches
  (fal; role fixed:find-all, whatever the Outlining setting is); a whole field of look-alikes in one
  call. When SAM 3.1 can't run here (a ToolError: no FAL_KEY, not in this edition) the next way
  takes over; when it finds none, that is said; an error from fal is a failure;
- `cells`: with by="cells", the cell finder (setting Cells) outlines every cell and the vision
  model says which are the ones asked for;
- `vlm-box>edgetam`: the vision model (setting Seeing) boxes them in the view, or, inside an
  outline, in a close-up of it beside the whole view (Agent.look_in), and the outline model
  (setting Outlining) draws each box.

The thing searched inside is never replaced by what is found in it ("point out the nucleus in
the highlight" keeps the highlight). Later PRs add ways behind the same interface: SAM 3.1's
matches, Cellpose's cells, look-alikes, points as clicks, hosted models.
"""
from __future__ import annotations

import re

from dataclasses import dataclass, field

from .. import tools, execution
from ..agent import _box_in, _same_thing
from ..brain.registry import ToolContext, ToolFailed
from ..magnifier import padded_rect
from ..prompts import FIND, FIND_IMAGES_INSIDE

INSIDE_PAD = 0.15  # the close-up of an outline shows this much around it
MAX_FOUND = 15
MAX_CELLS = 60  # cells the model is shown, numbered (the picked ones are all kept)

PICK_CELLS = """You are looking through a microscope together with the user. Image 1 is the current view. Image 2
is the same view with every cell a cell finder outlined, each with a number.
The user asked: "{question}"
Which numbered outlines are {what}? {quantity} List no numbers if none fit. Judge by what each
outlined thing looks like (shape, size against its neighbours, colour, nucleus).
Reply with JSON only: {{"numbers": [numbers], "note": "what you saw, if none fit"}}"""


def numbered(image, polygons, crop, max_side: int = 1024) -> bytes:
    """The view with each polygon outlined thinly and numbered at its centre (1-based)."""
    import cv2
    import numpy as np

    marked = image.copy()
    for i, poly in enumerate(polygons, 1):
        pts = np.array(poly, np.int32)
        cv2.polylines(marked, [pts], True, (255, 220, 60), 1, cv2.LINE_AA)
        cx, cy = int(pts[:, 0].mean()), int(pts[:, 1].mean())
        cv2.putText(marked, str(i), (cx - 6, cy + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(marked, str(i), (cx - 6, cy + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    from ..marks import encode_crop

    return encode_crop(marked, crop, max_side=max_side)[0]


def picked(data: dict, polygons: list) -> list:
    """The polygons the model's numbers name (unknown numbers dropped)."""
    nums = {int(n) for n in (data or {}).get("numbers") or [] if str(n).lstrip("-").isdigit()}
    return [polygons[n - 1] for n in sorted(nums) if 1 <= n <= len(polygons)]


@dataclass(frozen=True)
class FindRef:
    what: str
    inside: str | None = None
    quantity: str = "one"  # "one" | "all" | "requested"; direct callers retain the one-result default
    like: str | None = None  # an outline: every thing like this one (an exemplar)
    by: str = "look"  # "look": the vision model boxes them; "cells": the cell finder outlines every cell, the model picks;
    # "concept": SAM 3.1 finds every match of the words themselves (a text concept, no example)


@dataclass
class Found:
    objects: list = field(default_factory=list)  # scene.SceneObject, in the order found
    how: str = ""
    note: str = ""  # why nothing was found, in the model's words
    container: object = None  # an existing outline searched inside, if any
    errors: list[str] = field(default_factory=list)
    ctx: object = None  # the request's context (its steps: what "@n" names)
    unshown: bool = False  # SAM 3.1 found some, but none could be shown: said as that, never "couldn't find"
    next: str | None = None  # the first failure's own next step, carried to what is said (M3.1's third re-check)
    scope_label: str = ""  # an original Sight region; never a rendered/persisted outline


def _inside(box, outer) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def _on_scope_picture(obj, info) -> bool:
    """A held Sight region cannot borrow a newer scene outline's identity."""
    from ..capture import RUN

    return (obj.anchor_frame == info.id and obj.anchor_epoch == info.epoch
            and getattr(obj.producing_basis, "run", None) in (None, RUN))


class Finder:
    def __init__(self, hub, agent, board=None, segment_all=None) -> None:
        self.hub, self.agent, self.board = hub, agent, board
        self.segment_all = segment_all or tools.segment_all  # (hub, frame_id, box, text) -> [{"polygons", "score"}]

    @execution.traced("find")
    async def find(self, ref: FindRef, ctx: ToolContext) -> Found:
        from ..board import Board

        board = ctx.board or self.board or Board(self.hub, listen=False)
        info = self.hub.latest_info
        container, region, scope_label, view = None, None, "", None
        if ref.inside and re.fullmatch(r"s\d+", ref.inside.strip()):
            # A search region is not a request to paint its container. Resolve
            # only the original look against the exact picture being searched.
            view = self.agent.snapshot(ctx.frame_id)
            info = view[0] if view is not None else None
            seen = ctx.seen_view
            thing = next((t for t in seen.things if t.id == ref.inside.strip()), None) if seen is not None else None
            if thing is None or info is None:
                raise tools.ToolError("the original search region is no longer available", next="ask me to look again or name an existing outline")
            region = seen.box_now(thing, info)
            if region is None:
                raise tools.ToolError("the view moved since that search region was seen", next="ask me to look again")
            scope_label = thing.name
        elif ref.inside:
            container = board.resolve(ref.inside, ctx)
            region = _box_in(container, info)
        found = Found(container=container, scope_label=scope_label, ctx=ctx)

        # Already outlined with that name (inside the scope): reuse it.
        key = ref.what.strip().lower()
        named = [o for o in self.hub.scene.objects.values()
                 if o.kind == "highlight" and o.trial is None and o is not container and o.label and key
                 and (not scope_label or _on_scope_picture(o, info))
                 and (key in o.label.lower() or o.label.lower() in key)
                 and (region is None or _inside(_box_in(o, info), region))]
        # Under this request's choice of model, one another model made is outlined again with it (ticket.reusable;
        # acceptance flow 2 on footage: "outline that cell again with SAM 2.1 tiny" reused EdgeTAM's).
        from ..ticket import reusable

        named = [o for o in named if reusable("outline", o.made_by)]
        if named and ref.quantity == "one" and not ref.like:
            found.objects, found.how = named[-1:], "board"
            execution.bind(object_id=found.objects[0].id)
            execution.finish("reused", cache="board-outline", policy="no-model-call")
            return found

        if ref.like or ref.by == "concept":
            # Every thing like this one: SAM 3.1's matches for the exemplar's box. Or every thing the words
            # name (a text concept, M3): SAM 3.1's matches for the words, no example.
            example = board.resolve(ref.like, ctx) if ref.like else None
            if scope_label and example is not None and not _on_scope_picture(example, info):
                raise tools.ToolError("the example and original search region are on different pictures",
                                      next="choose an example on the same captured view")
            from ..operations import Unavailable, runner

            try:
                await self._like(example, ref, region, found, capture=view[0] if view is not None else None)
                return found
            except Unavailable as exc:
                if ref.by == "concept":
                    # Words to SAM 3.1 are asked for by name (finding 85): an explicit choice, raw (ADR 0011). It
                    # is never replaced by the vision model's boxes; what else can be done is said.
                    raise tools.ToolError(f"{exc}; finding by words needs SAM 3.1",
                                          next="outline one and ask for every one like it, or ask me to find them by looking") from None
                if exc.fallback != "vision-boxes":
                    raise
                # SAM 3.1 can't run here (this edition, no key): the declared fallback
                # (operations.FIND_LIKE), recorded as what ran, and said.
                execution.note(fallback="exemplar-matcher-unavailable; vision-boxes")
                found.errors.append(str(exc))
                runner(self.hub).fell_back("find-like", "sam3.1-all", "vision-boxes", self.hub.latest_info, str(exc))
            except tools.ToolError as exc:
                # It ran and failed (a network error, a refusal): a failure, said as it is, never
                # silently replaced by another way of finding (scaffolding review, M1.2 item 1).
                found.errors.append(f"finding every one like it failed: {exc}")
                return found

        if ref.by == "cells" and region is None:
            # The cell finder outlines every cell; the model says which are the ones asked for.
            return await self._cells(ref, ctx, found)

        # The vision model boxes them; the outline model draws each one.
        view = view if view is not None else self.agent.snapshot(ctx.frame_id)
        frame = view[0]
        look_region = None
        if region is not None:
            look_region = padded_rect(region, INSIDE_PAD, frame.width, frame.height)
        scope_name = scope_label or _name(container)
        images = FIND_IMAGES_INSIDE.format(container=scope_name) if region is not None else \
            " The image is the current view through the eyepiece."
        prompt = FIND.format(images=images, question=ctx.question or ref.what,
                             quantity=("every" if ref.quantity == "all" else "the one" if ref.quantity == "one"
                                       else "the requested number of"), what=ref.what,
                             where=f" inside the {'seen' if scope_label else 'outlined'} {scope_name}" if region is not None else " in the view",
                             image="image 2" if region is not None else "the image")
        data, used = await self.agent.look_in(prompt, view, look_region)
        found.note = str(data.get("note") or "")
        targets = data.get("targets") or []
        if region is not None:
            targets = [t for t in targets if _inside(t["box"], region)]  # what is in the outline, not around it
        if ref.quantity == "one":
            targets = sorted(targets, key=lambda t: -(t.get("confidence") or 0))[:1]
        source = "ai:" + str(used.get("option") or self.hub.settings.vision)
        for t in targets[:MAX_FOUND]:
            box = tuple(t["box"])
            existing = next((o for o in self.hub.scene.objects.values() if o.kind == "highlight" and o.trial is None
                             and (not scope_label or _on_scope_picture(o, info))
                             and o is not container and _same_thing(box, _box_in(o, info))
                             and reusable("outline", o.made_by)), None)
            if existing is not None:
                found.objects.append(existing)
                continue
            # The thing's name, never the words that pointed at it ("that cell" labelled a cell "that cell").
            label = re.sub(r"^(that|this|the|those|these)\s+", "", str(t.get("label") or ref.what).strip(), flags=re.I)[:40]
            conf = t.get("confidence")
            try:
                # On the look's own view (a held Capture), never a frame looked up again by id.
                from ..known import asked_record, look_record

                confidence = float(conf) if isinstance(conf, (int, float)) else None
                # The look's own name, or, when it gave none, the request's word: never credited to the look.
                naming = (look_record(label=label, model=source[3:], confidence=confidence) if t.get("label")
                          else asked_record(label=label, model=source[3:]))
                obj = await tools.outline_at(self.hub, frame, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, box=box,
                                             source=source, label=label, status="likely", confidence=confidence,
                                             naming=naming)
            except tools.ToolError as exc:
                found.errors.append(f"{label}: {exc}")
                continue
            found.objects.append(obj)
        found.how = "vlm-box>edgetam" + (" inside" if region is not None else "")
        return found


    async def _cells(self, ref: FindRef, ctx: ToolContext, found: Found) -> Found:
        import asyncio

        import cv2
        import numpy as np

        from ..marks import encode_crop, fov_crop

        from ..capture import decoding
        from ..brain.registry import record_made
        from ..population import capture_of, promote, segment

        # Every cell, kept as a population (evidence, contract 8): the model picks members of it by
        # number, and the members it picks become outlines from their own masks (promotion).
        population = await segment(self.hub)
        record_made(ctx, population.id)  # "@n" names it to the answer's later steps
        members = [m for m in population.live() if m.polygons][:MAX_CELLS]
        if not members:
            found.how, found.note = "cells", f"{population.id}: the cell finder found no cells here"
            return found
        polygons = [max(m.polygons, key=len) for m in members]  # each member's outer outline, numbered
        capture = capture_of(self.hub, population)
        if capture is None:
            raise tools.ToolError(f"the view {population.id} was made on is not available",
                                  next="ask me to outline them again on the view now")
        with decoding(self.hub, capture, "look", owner="numbering the cells") as held:  # its decode, counted
            image = held.bgr()
            crop = fov_crop(capture.fov, image.shape[1], image.shape[0])
            clean, _ = await asyncio.to_thread(encode_crop, image, crop)
            marked = await asyncio.to_thread(numbered, image, polygons, crop)
        option = self.hub.settings.vision
        result = await self.agent.vision(option).ask(
            [clean, marked], PICK_CELLS.format(question=ctx.question or ref.what, what=ref.what,
                quantity=("List every matching number." if ref.quantity == "all" else
                          "Choose one matching number." if ref.quantity == "one" else
                          "Choose the number of instances requested by the user, using their original words above.")))
        chosen = picked(result.data or {}, list(range(len(members))))
        found.note = str((result.data or {}).get("note") or "") if not chosen else ""
        chosen = chosen[:1] if ref.quantity == "one" else chosen
        try:
            found.objects.extend(await promote(self.hub, population, [members[i].id for i in chosen],
                                               why=f"picked by {option} as {ref.what.strip()[:40]}",
                                               label=ref.what.strip()[:40]))
        except tools.ToolError as exc:
            found.errors.append(str(exc))
        found.how = f"cells ({population.id}: {len(members)} found, {len(chosen)} picked)"
        return found

    async def _like(self, example, ref: FindRef, region, found: Found, *, capture=None) -> None:
        from ..capture import captures
        from ..ticket import current

        latest = self.hub.latest_info
        if capture is None and latest is None:
            from ..frames import no_picture
            why, next_step = no_picture(self.hub, "there is no fresh picture from the camera to look at",
                                         "check the camera is connected and the view is live, then ask again")
            raise tools.ToolError(why, next=next_step)
        # The view now, held by this request while SAM 3.1 finds the matches on it.
        capture = capture if capture is not None else captures(self.hub).get(latest.id) or latest
        if current() is not None:
            current().hold(capture, "analysis", "outline")
        info = capture
        box = _box_in(example, info) if example is not None else None
        words = concept_words(ref.what) if example is None else ""
        matches = await self.segment_all(self.hub, capture, box, words)
        group = self.hub.scene.new_id("a")
        label = (example.label if example is not None else "") or words[:40]
        source = "ai:sam3.1-all" if example is not None else "ai:sam3.1-concept"
        what_for = f"for {example.id}" if example is not None else f'for the words "{words}"'
        recorded = set()
        for m in matches[:32]:
            polys = m["polygons"]
            if not polys:
                continue
            pts = [p for poly in polys for p in poly]
            mbox = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
            if region is not None and not _inside(mbox, region):
                continue
            same = next((o for o in self.hub.scene.objects.values() if o.kind == "highlight" and o.trial is None
                         and (not found.scope_label or _on_scope_picture(o, info))
                         and _same_thing(mbox, _box_in(o, info))), None)
            if m.get("population") and m["population"] not in recorded and ctx_of(found) is not None:
                from ..brain.registry import record_made

                record_made(ctx_of(found), m["population"])  # "@n" names the matches to later steps (review S2)
                recorded.add(m["population"])
            if same is not None:
                if same not in found.objects:
                    found.objects.append(same)  # the example itself, or one already outlined
                continue
            try:
                from ..known import concept_record, match_record

                naming = (match_record(label=label, example=example.id, score=m["score"]) if example is not None
                          else concept_record(label=label, words=words, score=m["score"]))  # the words SAM was given
                # A match is not a name the model is sure of: "Not sure", no confidence (the match score stays in its
                # note and record, never shown as how likely a name is: M3.1 review B3).
                obj = await tools.add_outline(self.hub, capture, polys, source=source, label=label, status="unsure",
                                              note=f"SAM 3.1 match {what_for}, score {m['score']:.2f}", group=group,
                                              confidence=None, naming=naming)
            except tools.ToolError as exc:
                found.errors.append(str(exc))
                found.next = found.next or getattr(exc, "next", None)
                continue
            found.objects.append(obj)
            if m.get("population") and m.get("member"):
                from ..evidence import evidence_of

                store = evidence_of(self.hub)
                population = store.get(m["population"])
                if population is not None:  # the outline is that match: linked (Amendment 1)
                    store.link(population, "same-object", m["member"], obj.id, "outlined from the match's own mask")
        found.how = "sam3.1-all" if example is not None else "sam3.1-concept"
        if not found.objects and matches:  # found, but none could be shown: said as that, never "found nothing" (B1)
            shown = [m for m in matches if m.get("polygons")]
            outside = region is not None and shown and all(not _inside(_poly_box(m["polygons"]), region) for m in shown)
            why = found.errors[0] if found.errors else ""
            why = why[len("not shown: "):] if why.startswith("not shown: ") else why  # said once
            found.note = (f"SAM 3.1 found {len(matches)}, none of them inside the {found.scope_label or _name(found.container)}" if outside else
                          f"SAM 3.1 found {len(matches)}, but none came with an outline to show" if not shown else
                          f"SAM 3.1 found {len(matches)}, but they could not be shown" + (f": {why}" if why else ""))
            found.unshown = True
        elif not found.objects:
            found.note = (f"nothing else like the {label} here" if example is not None else
                          f'SAM 3.1 found nothing it calls "{words}" here (on these slides words often find nothing; '
                          "an example works better: outline one and ask for every one like it)")


# Leading words that say how many or which, not what: "all of the", "every one of the", "the", "those"...
# "every other" (alternate ones), "the most …" (a superlative) and a capital "A" ("A cells") are part of what is meant,
# never taken off (M3.1's third re-check); "a" and "an" only in lower case.
ARTICLES = re.compile(r"^(?:(?i:all|every(?!\s+other\b)|each|some|any|both)(?:\s+(?i:one|single))?(?:\s+of)?"
                      r"|(?i:the)(?!\s+most\b)|(?i:those|these|that|this)|a|an)\s+")


def concept_words(what: str) -> str:
    """The thing's name for SAM 3.1, from the user's words: leading articles and "all the" taken off, at most 6
    words (M3.1 review S4); never more than the request said."""
    said = " ".join(str(what or "").split())
    words = said
    for _ in range(3):
        words = ARTICLES.sub("", words)
    if not words or words.lower() in ("those", "these", "that", "this", "them", "it", "ones"):
        words = said  # nothing left but a pointer: the words as said
    return " ".join(words.split()[:6])


def _poly_box(polys) -> tuple:
    pts = [p for poly in polys for p in poly]
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


def ctx_of(found):
    return getattr(found, "ctx", None)


def _name(obj) -> str:
    return obj.label if obj is not None and obj.label else "outline"


def describe_found(found: Found, ref: FindRef) -> str:
    """The tool's summary, or ToolFailed saying why nothing was found."""
    where = f" inside the {found.scope_label or _name(found.container)}" if found.container is not None or found.scope_label else ""
    if not found.objects and found.unshown:  # found, not shown: never "couldn't find" (M3.1 re-check B1)
        outside = "none of them inside" in found.note
        raise ToolFailed(f"{found.note} ({ref.what})", next="ask for them without the outline" if outside else
                         found.next)  # next: what stopped them says what to do (the failure's own next step), if anything
    if not found.objects:
        why = found.note or (found.errors[0] if found.errors else "")
        raise ToolFailed(f"couldn't find {ref.what}{where}" + (f": {why}" if why else ""),
                         next="click it, or describe it another way")
    names = list(dict.fromkeys(o.label or ref.what for o in found.objects))
    if len(found.objects) == 1:
        return f"outlined the {names[0]}{where}"
    return f"outlined {len(found.objects)} ({', '.join(names)}){where}"
