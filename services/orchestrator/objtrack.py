"""Outlines that follow their own object (option TRACK "objects"; gpu_worker/objects.py).

Slide motion moves every outline by the slide's move. Things that move on their own (sperm,
protists, a cell in a current) leave their outlines behind, and a slide the motion tracker loses
leaves outlines waiting to be re-found. EdgeTAM's video model, on the heavy GPU worker, follows
each outlined object a few times a second and says, per object, whether it is in the frame:

- in the frame (its object score above 0) and moved on its own (more than MOVED_PX from where the
  slide's motion puts it, or in another epoch): the outline is moved to where EdgeTAM sees it,
  anchored to that frame, so it is drawn there (verified by EdgeTAM, also across a lost epoch);
  in the frame where the slide's motion already puts it: left as it is;
- not in the frame: nothing changes here; slide motion and template re-finding carry on as before,
  and after MISSES_TO_DROP checks in a row the object is no longer followed.

An object is only started from a verified position (its outline "tracking" in the current
epoch), so EdgeTAM never learns a misplaced outline. The newest highlights are followed (at most
MAX_FOLLOWED); an object's dissected parts move with it by the same shift. Decisions are logged
when something changes (an object started, placed or dropped), and a step every LOG_EVERY_S.

The shape the user chose stays: an outline another model drew (micro-sam, SAM 2.1, SAM 3.1, a promoted
cell's own mask...) is moved by the displacement EdgeTAM measured, never redrawn with EdgeTAM's mask (live
2026-10-11: "outline it with micro-sam" showed EdgeTAM's shape about 0.1 s later). Only an outline EdgeTAM drew
takes EdgeTAM video's new shape. Each followed outline carries `followed_by` (the model, whether its shape is
kept, from and until when), so how it was made can say "drawn by micro-sam, followed by EdgeTAM".

Nothing of a step's work is done on the event loop but its bookkeeping: the view's decode and the placing
(template matches) run in a thread (live 2026-10-11: the loop stalled for 46.8 s under load).
"""
from __future__ import annotations

import asyncio
import os
import time

import cv2
import numpy as np
from . import execution

# One EdgeTAM step this often (~100 ms each on an M-series GPU). bench/object_tracking.py: a
# swimming cell on target 80-89% of frames at 10 a second against 62-73% at 5 (a fast swimmer gets
# ~20 px ahead between steps); sim `follow-clicks` p95 104 ms either way.
STEP_S = 0.1
MAX_FOLLOWED = 4  # the newest highlights followed at once
MISSES_TO_DROP = 15  # checks in a row without the object (3 s): stop following it
GROWTH_MAX = 4.0  # an outline that grows (or shrinks) more than this many times is not taken
# EdgeTAM moves an outline only when its object has moved on its own by more than this from where
# the slide's motion puts it: on still tissue its outline's centre moves 1-7 px from step to step as
# its shape is redrawn, and the slide's (consensus-checked) motion is the more precise there (sim
# `tracking`: at 4 px, still outlines were nudged 6-7 px off); a swimming cell moves ~15 px a step.
MOVED_PX = 8.0
# ...and only while the slide moves slower than the studio's fade threshold (contract/overlay.json
# fadeStartPx) over the last few frames: in a fast pan the frames are smeared, EdgeTAM can jump to
# a look-alike (sim `tracking`: 72 px), and the slide's own (consensus-checked) motion carries it.
CALM_FRAMES = 6
# ...and only when the object's own appearance (the template captured when it was outlined) agrees:
# it must match better at the new place than where the slide's motion puts it. A swimming cell
# carries its looks along; an EdgeTAM outline that spread into the neighbouring tissue does not.
LOOKS_MARGIN = 0.05
LOOKS_SEARCH_PX = 6  # ...searched for this far around where EdgeTAM puts it (as re-finding across epochs did)
CLEAR_FRAMES = 30  # ...and only on a view that has been sharp, at a steady scale, for about a second
# ...and only where the motion tracker placed each of the last CALM_FRAMES frames: a frame it could
# not register carries the last offset over, so it looks still while the view may already be
# somewhere else (sim `refind`: in the first frames of a cut, EdgeTAM latched on to the new view and
# the outline, kept in the old epoch, came back 72 px off when the slide returned).
# Highlights only: cells (and, from M1.3, population members) are many and have their own checks;
# following them pushed the user's own outlines out of the MAX_FOLLOWED newest (2026-10-07).
KINDS = ("highlight",)
LOG_EVERY_S = 5.0
# The hub's largest drift correction (hub.MAX_DRIFT_CORRECTION_PX): a same-epoch move this small, made while
# the slide has travelled at least SLIDE_TRAVEL_PX since the outline was anchored, may be the motion tracker's
# accumulated drift corrected rather than the object moving; its trail takes it as a correction (trail.py).
# On a still slide nothing accumulates, so a slow swimmer's small moves still count.
MAX_DRIFT_CORRECTION_PX = 10.0
SLIDE_TRAVEL_PX = 20.0
# The follower's own model, and the outline models whose shapes it may redraw: only EdgeTAM's (gpu_worker/objects.py
# follows with EdgeTAM's video model). Any other model's outline keeps its shape and is moved.
FOLLOWER = {"implementation": "edgetam-video", "model": "EdgeTAM"}
OWN_SHAPES = {"edgetam"}
# When the follower's own model loads (MICROMIND_FOLLOW_READY). "first-use" (as before): the wanted set's preload
# (loads.py, PRELOAD_AFTER_S of frames after the click worker is ready) or else the follower's first step, whichever
# comes first. The L1a sims (2026-10-20) found the first step first in three stacks of five: the user's first outlines
# went unfollowed while EdgeTAM's video model loaded, 2.7 s on a quiet Mac and up to 18.3 s on a loaded one. "frames":
# the follow worker starts and loads it as soon as the click worker is ready and a frame has come, before anything is
# outlined; the preload then finds it held. Its memory is what the preload holds anyway, some seconds sooner (the
# follow worker and EdgeTAM video, docs/bench/click-lane.md), and none while no frame comes (No camera).
# Measured (docs/bench/click-lane.md): in the sims of 2026-10-20 the follower's first step loaded it in 9 of 15 stacks
# with "first-use" (3.2-6.9 s after the click worker was ready), in none of 11 with "frames" (quiet: 2.6-3.4 s).
# The default stays "first-use" until a quiet A/B of the scenarios' click margins (the coordinator's) shows "frames"
# makes none of them worse; then it is switched.
READY_WHEN = ("first-use", "frames")  # the default first
READY_POLL_S = 0.5


def ready_when(environ=os.environ) -> str:
    value = (environ.get("MICROMIND_FOLLOW_READY") or "").strip().lower()
    return value if value in READY_WHEN else READY_WHEN[0]


def keeps_shape(obj) -> bool:
    """An outline the follower moves without redrawing: drawn by another model than EdgeTAM. One that does not say
    what drew it (made before outlines said so) is taken as EdgeTAM's, as they all were then."""
    implementation = (getattr(obj, "made_by", None) or {}).get("implementation")
    return implementation is not None and implementation not in OWN_SHAPES


class ObjectFollower:
    def __init__(self, hub, log=lambda record: None) -> None:
        self.hub = hub
        self.log = log
        self.followed: dict[str, dict] = {}  # object id -> {"misses", "area"}
        # The worker's session as this follower knows it: a failed or timed-out step means the two
        # may disagree about what is followed, so the next step resets the worker's session and
        # starts again from verified positions (`resync`). Calls carry the session and a sequence
        # number; a reply from an older session changes nothing.
        self.session = 0
        self.seq = 0
        self.reset_next = False
        self.ticket = None  # the follower's own ticket (ticket.py): its writes are superseded by a reset
        self.steps = 0  # steps the worker answered (health: following goes on beside other work)
        self.last_step_at = 0.0
        self.updated_at: dict[str, float] = {}  # object id -> when EdgeTAM last placed it (hub skips its template re-anchor)
        self.dropped: dict[str, int] = {}  # object id -> its anchor frame when EdgeTAM lost it: not started again from there
        self._task: asyncio.Task | None = None
        self._readying: asyncio.Task | None = None
        self._logged_at = 0.0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
        if ready_when() == "frames" and os.environ.get("MICROMIND_PRELOAD", "1") != "0" and (
                self._readying is None or self._readying.done()):
            self._readying = asyncio.create_task(self.ready())

    def stop(self) -> None:
        for task in (self._task, self._readying):
            if task is not None:
                task.cancel()

    async def ready(self) -> bool:
        """The follower's model loaded on its worker once the click worker is ready and a frame has come, while
        following is on (READY_WHEN "frames"): quietly, in the background, logged as the preload is (`model-preload`,
        loads.py), so the first outline is followed from its first step. True when it was loaded here."""
        hub = self.hub
        if getattr(hub, "gpu_follow", None) is None or getattr(hub, "loads", None) is None:
            return False  # no follow worker here (no GPU, a test's hub): nothing to load ahead
        while True:
            main = getattr(hub, "gpu", None)
            if (getattr(hub, "latest_info", None) is not None and getattr(main, "ready", False)
                    and getattr(hub.settings, "track", "slide") == "objects"):
                break
            await asyncio.sleep(READY_POLL_S)
        return await hub.loads._ready(hub.gpu_follow, FOLLOWER["implementation"])

    def placed_recently(self, object_id: str, within_s: float = 1.0) -> bool:
        return time.monotonic() - self.updated_at.get(object_id, -1e9) < within_s

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(STEP_S)
            if getattr(self.hub.settings, "track", "slide") != "objects":
                self.suspend()
                continue
            try:
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the follower never stops the app; the error is logged
                self.resync(f"{type(exc).__name__}: {exc}"[:300])
                await asyncio.sleep(2.0)

    def suspend(self) -> None:
        """Following switched off (TRACK not "objects"): nothing is followed, and the trails end here."""
        if not self.followed:
            return
        with execution.request(self.hub, "tracking"):
            with execution.span("tracking-suspended", provider="app", adapter="orchestrator.objtrack.ObjectFollower",
                                policy="setting-changed; no-tracking-dispatch"):
                for oid in self.followed:
                    execution.bind(object_id=oid)
        self._end_trails("stopped")
        for oid in self.followed:
            self._stamp(self.hub.scene.objects.get(oid), started=False)
        self.followed.clear()

    def resync(self, why: str, bump: bool = True) -> None:
        """A step failed or timed out: the worker may hold objects this follower thinks it is not
        following, or not hold ones it thinks it is. Forget every followed object, reset the
        worker's session on the next step, and supersede writes of the old session (they change
        nothing on screen); objects are started again from verified positions. Also when the
        follower's ticket was superseded (a new day, a tracking reset; `bump` False: already bumped),
        so the next step follows again under a new ticket."""
        dropped = list(self.followed)
        self._end_trails("gap")  # nothing observed them meanwhile: a later place is never joined on
        for oid in dropped:
            self._stamp(self.hub.scene.objects.get(oid), started=False)
        self.followed.clear()
        self.reset_next = True
        self.session += 1
        if bump:
            self.hub.scene.generations.bump("track", why="following was reset after a failed step")
        if self.ticket is not None:
            self.ticket.seal()
        self.ticket = None
        self.log({"kind": "object-follow", "error": why, "resync": True, "session": self.session, "forgot": dropped})

    def _found(self, obj, gray_of, sx: float, sy: float, moved: tuple[float, float],
               same_epoch: bool) -> tuple[tuple[float, float], bool, float | None] | None:
        """The object's move on its own, whether its looks measured it exactly, and that match's score (None
        when they did not), or None (not moved there). EdgeTAM says roughly where the object went, but its
        mask's centre moves with the mask's shape (sim `refind`: masks that grew lopsidedly put outlines 11-18 px off where
        the object's looks matched at 0.87-0.93). So the object's own template is searched within
        LOOKS_SEARCH_PX of EdgeTAM's place, and an unambiguous best match there is where it is.
        - Another epoch (being re-found, or followed across a loss): only where its template is
          found at REFIND_ACROSS_EPOCHS_SCORE, as re-finding across epochs requires (EdgeTAM can
          latch on to a look-alike: sim `refind`, 66 px off).
        - The same epoch: the found place (else EdgeTAM's) must match the template better than where
          the slide's motion puts it, by LOOKS_MARGIN; no absolute score, as a swimmer's surroundings
          change while it swims (bench/object_tracking.py: requiring 0.8 left swimmers behind, on
          target 80% -> 40%).
        No template (a cell from the cell finder): EdgeTAM's move as it is, in its own epoch only.
        `gray_of()`: the step's view, decoded (held) on first use."""
        gray = gray_of() if obj.template is not None else None
        if gray is None:
            return (moved, False, None) if same_epoch else None
        from .hub import REFIND_ACROSS_EPOCHS_SCORE
        from .scene import refind

        looks = obj.template
        ox, oy = obj.template_origin[0] + sx, obj.template_origin[1] + sy  # where the slide puts it
        found = refind(gray, looks, (ox + moved[0], oy + moved[1]), search_px=LOOKS_SEARCH_PX,
                       min_score=REFIND_ACROSS_EPOCHS_SCORE if not same_epoch else 0.0)
        if not same_epoch:
            return None if found is None else ((moved[0] + found[0], moved[1] + found[1]), True, float(found[2]))
        th, tw = looks.shape[:2]

        def score(x: float, y: float) -> float:
            x, y = int(round(x)), int(round(y))
            if x < 0 or y < 0 or x + tw > gray.shape[1] or y + th > gray.shape[0]:
                return -1.0
            return float(cv2.matchTemplate(gray[y:y + th, x:x + tw], looks, cv2.TM_CCOEFF_NORMED)[0, 0])

        place = (moved[0] + found[0], moved[1] + found[1]) if found is not None else moved
        if score(ox + place[0], oy + place[1]) <= score(ox, oy) + LOOKS_MARGIN:
            return None
        return place, found is not None, (float(found[2]) if found is not None else None)

    def _clear(self, info) -> bool:
        """The view of the last CLEAR_FRAMES frames is sharp and its scale steady: never flagged
        blurry (quality.py) and the eyepiece disc's size within coach.LENS_SHARE (the phone's own
        lens refocusing changes it 1-2.4%, the knob never more than 0.05%). EdgeTAM's masks on a
        refocusing view are unreliable and the object's looks match nowhere well, so the follower
        waits (sim `refind`: the phone hunting at footage frames 6340-6460, disc 399 -> 395 px,
        sharpness 206 -> 39, and outlines moved there were 10-16 px off once it was sharp again)."""
        from .coach import LENS_SHARE

        recent = [self.hub.infos.get(info.id - k) for k in range(CLEAR_FRAMES)]
        recent = [i for i in recent if i is not None and i.epoch == info.epoch]
        if any("blurry" in (getattr(i, "quality", ()) or ()) for i in recent):
            return False
        discs = [d for d in (getattr(i, "disc", 0.0) or 0.0 for i in recent) if d > 0]
        return not discs or (max(discs) - min(discs)) / max(discs) <= LENS_SHARE

    def _registered(self, info) -> bool:
        """The motion tracker registered this frame and the CALM_FRAMES before it, so where the slide
        is now is known (an unregistered frame only carries the last offset over)."""
        recent = [self.hub.infos.get(info.id - k) for k in range(CALM_FRAMES + 1)]
        return all(getattr(i, "tracked", True) for i in recent if i is not None)

    def _calm(self, info) -> bool:
        """The slide has moved slower than the studio's fade threshold over the last frames."""
        from .contract import OVERLAY

        limit = float(OVERLAY["fadeStartPx"])
        recent = [self.hub.infos.get(info.id - k) for k in range(CALM_FRAMES + 1)]
        recent = [i for i in recent if i is not None]
        return all(a.epoch == b.epoch and np.hypot(a.dx - b.dx, a.dy - b.dy) < limit for a, b in zip(recent, recent[1:]))

    def wanted(self, info) -> list:
        """The outlines the user asked to keep an eye on (identity.py, "follow_cells") first, then the newest
        highlights (never parts or arena arms), at most MAX_FOLLOWED: later clicks never push a followed
        swimmer out (review SF2 of K2)."""
        objects = [o for o in self.hub.scene.objects.values()
                   if o.kind in KINDS and o.parent is None and o.trial is None]
        objects.sort(key=lambda o: o.created_at, reverse=True)
        identity = getattr(self.hub, "identity", None)
        kept = set(identity.followed_outlines()) if hasattr(identity, "followed_outlines") else set()
        objects.sort(key=lambda o: o.id not in kept)  # stable: the followed first, each part newest first
        return objects[:MAX_FOLLOWED]

    async def step(self) -> None:
        from .capture import Refused, captures

        hub = self.hub
        info = hub.latest_info
        store = captures(hub)
        # The view this step is about, taken before anything is awaited (ADR 0010): its bytes go to
        # EdgeTAM and its pixels check the object's looks afterwards.
        frame = store.get(info.id) if info is not None else None
        heavy = getattr(hub, "gpu_follow", None) or hub.gpu_heavy  # the follow worker (contract 5)
        if frame is None or heavy is None:
            return
        superseded = self.ticket.superseded("track") if self.ticket is not None else None
        if superseded:
            # A new day or a tracking reset since this ticket: its writes would change nothing, for
            # good. Start again under a new one (scaffolding review, M1.1 item 1).
            self.resync(superseded, bump=False)
        wanted = self.wanted(info)
        ids = {o.id for o in wanted}
        stop = [oid for oid in self.followed if oid not in ids]
        start = []
        for obj in wanted:
            if obj.id in self.followed:
                continue
            # Only from a verified position: tracked in this epoch, so its place now is known; and
            # not again from the place EdgeTAM already lost it at (only once something re-placed it).
            if obj.track != "tracking" or obj.anchor_epoch != info.epoch or self.dropped.get(obj.id) == obj.anchor_frame:
                continue
            x0, y0, x1, y1 = obj.bbox
            sx, sy = info.dx - obj.anchor_offset[0], info.dy - obj.anchor_offset[1]
            start.append({"id": obj.id, "box": [x0 + sx, y0 + sy, x1 + sx, y1 + sy]})
            self.followed[obj.id] = {"misses": 0, "area": None}
        for oid in stop:
            self.followed.pop(oid, None)
            self._stamp(hub.scene.objects.get(oid), started=False)
        if not self.followed and not stop:
            return
        # What the frames up to this one say (the facts are read now, before waiting for EdgeTAM).
        calm = self._calm(info)
        clear = self._clear(info)
        placed_by_slide = self._registered(info)
        from . import made_with

        # Said only by a step that starts an outline or writes its log line: the catalog's scan is ~3.5 ms on the
        # loop, too much ten times a second.
        producer = made_with.capture_implementation(FOLLOWER["implementation"]) if start else None
        heavy = await heavy.ensure_started()
        from .ticket import issue

        if self.ticket is None:
            self.ticket = issue(hub, "follower")
        request = self.ticket
        self.seq += 1
        call = {"frame_id": info.id, "jpeg": frame.jpeg, "start": start, "stop": stop, "session": self.session,
                "seq": self.seq, "reset": self.reset_next}
        self.reset_next = False
        if start or stop:
            # One receipt at a session transition, never one per tracked frame.
            with execution.request(hub, "tracking"):
                with execution.span("tracking-transition", policy="start-stop"):
                    for oid in [s["id"] for s in start] + stop:
                        execution.bind(object_id=oid)
                    result = await heavy.call("track", timeout=10.0, priority="interactive", **call)
        else:
            result = await heavy.call("track", timeout=10.0, priority="interactive", **call)
        if result.get("session", self.session) != self.session or result.get("seq", self.seq) != self.seq:
            return  # a reply from before a reset, or to an older call: what it says no longer holds
        started_ids = {one["id"] for one in start}
        for obj in wanted:
            if obj.id in started_ids and hub.scene.objects.get(obj.id) is obj:
                self._stamp(obj, started=True, producer=producer)
        self.steps += 1
        self.last_step_at = time.time()
        changes, dropped = [], []
        found: dict[str, str] = {}  # what this step found per followed outline, for its trail (trail.py)
        corrected: set = set()  # moves this step that may be drift corrected, not the object's own motion
        carried: dict = {}  # outlines this step brings back after a gap or a jump, with their looks' match (hub.carried)
        decoded: dict = {}
        holds = []

        def gray_of():
            # The step's view decoded once, under a hold, so the decode is counted (invariant 3).
            if "gray" not in decoded:
                try:
                    holds.append(store.hold(frame, "analysis", "hold", "following an object"))
                    decoded["gray"] = frame.gray()
                except (Refused, ValueError):
                    decoded["gray"] = None
            return decoded["gray"]

        try:
            # The view's decode and the template matches, off the event loop (only this task touches what it changes).
            await asyncio.to_thread(self._place, result, info, calm, clear, placed_by_slide, gray_of, changes, dropped,
                                    found, corrected, carried)
        finally:
            for hold in holds:
                hold.release()
        for oid in dropped:
            self._stamp(hub.scene.objects.get(oid), started=False)
        await self._write(request, changes, dropped, start, stop, info, result, found, corrected, carried, producer=producer)

    def _stamp(self, obj, started: bool, producer=None) -> None:
        """Following starts or stops for an outline, said on it (`followed_by`: the model, whether its own shape is
        kept, since and until when), so how it was made can say "drawn by micro-sam, followed by EdgeTAM"."""
        if obj is None:
            return
        now = round(time.time(), 1)
        if started:
            obj.followed_by = {**FOLLOWER, "shape": "kept" if keeps_shape(obj) else "redrawn", "since": now, "until": None,
                               **({"producer": {"catalog": producer}} if producer else {})}
        elif getattr(obj, "followed_by", None) and obj.followed_by.get("until") is None:
            obj.followed_by = {**obj.followed_by, "until": now}

    def _place(self, result, info, calm, clear, placed_by_slide, gray_of, changes, dropped, found=None, corrected=None,
               carried=None) -> None:
        """What EdgeTAM saw, decided against the slide's move and the object's looks: `changes` to
        write, objects `dropped`, what was `found` of each for its trail: "seen" only where its
        place is known (trail.py), and those `carried` back after a gap or a jump (with their looks' match)."""
        found = {} if found is None else found
        corrected = set() if corrected is None else corrected
        carried = {} if carried is None else carried
        hub = self.hub
        for oid, seen in (result.get("objects") or {}).items():
            state = self.followed.get(oid)
            obj = hub.scene.objects.get(oid)
            if state is None or obj is None:
                continue
            area = float(seen.get("area") or 0)
            if state["area"] is None and seen.get("present"):
                state["area"] = area
            grew = state["area"] and area and not (1 / GROWTH_MAX <= area / state["area"] <= GROWTH_MAX)
            if not seen.get("present") or not seen.get("polygons") or grew:
                state["misses"] += 1
                found[oid] = "missed"
                if state["misses"] >= MISSES_TO_DROP:
                    self.followed.pop(oid, None)
                    self.dropped[oid] = obj.anchor_frame
                    dropped.append(oid)
                    found[oid] = "lost"
                continue
            state["misses"] = 0
            polygons = seen["polygons"]
            # Keep the template's place consistent with the new outline (hub._reanchor matches it).
            old = np.array([p for poly in obj.polygons for p in poly], float).mean(axis=0)
            new = np.array([p for poly in polygons for p in poly], float).mean(axis=0)
            sx, sy = info.dx - obj.anchor_offset[0], info.dy - obj.anchor_offset[1]
            moved = (float(new[0] - (old[0] + sx)), float(new[1] - (old[1] + sy)))
            same_epoch = obj.anchor_epoch == info.epoch and obj.track == "tracking"
            kept = keeps_shape(obj)
            if kept and same_epoch:
                # Another model's shape: EdgeTAM's mask is not that shape, so its centre is not the outline's. The
                # object's own move is how far EdgeTAM's mask moved on the slide since the outline was last placed
                # (where it sat when following began, or after the follower's last move of it).
                here = (info.epoch, float(new[0] - info.dx), float(new[1] - info.dy))  # its centre on the slide
                ref = state.get("ref")
                if ref is None or ref[0] != info.epoch:
                    ref = state["ref"] = here
                moved = (here[1] - ref[1], here[2] - ref[2])
                state["next_ref"] = here
            # Its place is known only on a calm, clear view the motion tracker registered.
            known = calm and clear and placed_by_slide
            if same_epoch and (np.hypot(*moved) <= MOVED_PX or not calm):
                found[oid] = "seen" if known else "unsure"
                continue  # where the slide puts it, or a fast pan: left to the slide
            if not clear or not placed_by_slide:
                found[oid] = "unsure"
                continue  # a blurred, refocusing or unregistered view: left to the slide and re-finding
            # Not placed by the slide (another epoch, or being re-found), EdgeTAM may have latched on
            # to a look-alike (sim `refind`: 66 px off after coming back); so its place must show the
            # object's own looks, which also say exactly where it is.
            placed = self._found(obj, gray_of, sx, sy, moved, same_epoch)
            if placed is None or (same_epoch and np.hypot(*placed[0]) <= MOVED_PX):
                found[oid] = "unsure" if placed is None else ("seen" if known else "unsure")
                continue
            moved, exact, looks_score = placed
            if not same_epoch and looks_score is not None and (obj.anchor_epoch != info.epoch or obj.track in ("lost", "refinding")):
                carried[oid] = looks_score  # back after its pixels had not matched, or across a jump (review B3 of K2)
            if exact or kept:
                # Measured by its looks, or another model's shape: the outline keeps its shape and moves by that much.
                polygons = [[[x + sx + moved[0], y + sy + moved[1]] for x, y in poly] for poly in obj.polygons]
            change = {"polygons": polygons, "anchor_frame": info.id, "anchor_epoch": info.epoch,
                      "anchor_offset": (info.dx, info.dy), "track": "tracking", "lost_since": None}
            if obj.template is not None:
                tx, ty = obj.template_origin
                change["template_origin"] = (int(round(tx + sx + moved[0])), int(round(ty + sy + moved[1])))
            changes.append((oid, change))
            found[oid] = "seen"
            # A small move while the slide travelled may be the tracker's residual drift corrected, not the
            # object's own motion (review of #155, after T1): it is a correction for the trail, never motion.
            if same_epoch and np.hypot(*moved) <= MAX_DRIFT_CORRECTION_PX and np.hypot(sx, sy) >= SLIDE_TRAVEL_PX:
                corrected.add(oid)
            for part in [o for o in list(hub.scene.objects.values()) if o.parent == oid]:
                # A part moves with its object: the slide's move since it was anchored, plus the
                # object's own move that EdgeTAM saw.
                px = info.dx - part.anchor_offset[0] + moved[0]
                py = info.dy - part.anchor_offset[1] + moved[1]
                changes.append((part.id, {"polygons": [[[x + px, y + py] for x, y in poly] for poly in part.polygons],
                                          "anchor_frame": info.id, "anchor_epoch": info.epoch,
                                          "anchor_offset": (info.dx, info.dy), "track": "tracking", "lost_since": None}))

    def _end_trails(self, state: str) -> None:
        trails = getattr(self.hub, "trails", None)
        if trails is not None:
            for oid in self.followed:
                trails.end(oid, state)

    def _trail(self, found: dict, stop, info, wrote=frozenset()) -> None:
        """What this step found goes to each outline's trail (trail.py), after its writes, so a place
        is the outline's as written. An outline the hub is re-finding is lost there, whatever was found."""
        trails = getattr(self.hub, "trails", None)
        if trails is None:
            return
        for oid, state in list(found.items()) + [(oid, "stopped") for oid in stop]:
            obj = self.hub.scene.objects.get(oid)
            if obj is None:
                continue
            if obj.track != "tracking" and state != "stopped":
                state = "lost"
            trails.observe(obj, state, info.epoch, wrote=oid in wrote, at=getattr(info, "wall_at", None),
                           frame=getattr(info, "id", None))

    async def _write(self, request, changes, dropped, start, stop, info, result, found=None, corrected=frozenset(),
                     carried=None, producer=None) -> None:
        from .apply import apply

        hub = self.hub
        found = dict(found or {})
        carried = dict(carried or {})
        wrote = set()  # the follower's own moves: the only motion a trail counts as the object's
        for object_id, change in changes:
            # Through the one gate (ADR 0010): a reset of following since this step, or the outline
            # removed, means the move changes nothing. EdgeTAM and the object's own looks are this
            # write's placement check (_found), so no further one is made here.
            applied = await apply(hub, lambda _, oid=object_id, ch=change: hub.scene.update(oid, **ch), scope="track",
                                  ticket=request, target=(object_id, None))
            if applied.ok and object_id in carried and callable(getattr(hub, "carried", None)):
                hub.carried(object_id, "follower", carried.pop(object_id))  # said with its basis (identity.py)
            if applied.ok and object_id in self.followed:
                # Only a move that was written marks it placed (the hub's re-anchor then waits for it); another
                # model's shape now sits where EdgeTAM's mask is, so its next move is measured from there.
                self.updated_at[object_id] = time.monotonic()
                state = self.followed[object_id]
                if "next_ref" in state:
                    state["ref"] = state.pop("next_ref")
                if object_id not in corrected:
                    wrote.add(object_id)
            elif not applied.ok and found.get(object_id) == "seen":
                found[object_id] = "unsure"  # the move was not written: its place on screen is not that
        self._trail(found, stop, info, wrote)
        now = time.monotonic()
        if dropped:
            with execution.request(hub, "tracking"):
                with execution.span("tracking-drop", provider="app", adapter="orchestrator.objtrack.ObjectFollower"):
                    for oid in dropped:
                        execution.bind(object_id=oid)
        if start or stop or dropped or now - self._logged_at >= LOG_EVERY_S:
            self._logged_at = now
            if producer is None:
                from . import made_with

                producer = made_with.capture_implementation(FOLLOWER["implementation"])
            self.log({"kind": "object-follow", "implementation": FOLLOWER["implementation"],
                      **({"producer": {"catalog": producer}} if producer else {}), "frameId": info.id, "ms": result.get("ms"), "started": [s["id"] for s in start],
                      "stopped": stop, "dropped": dropped, "placed": [c[0] for c in changes if c[0] in self.followed],
                      "scores": {oid: s.get("score") for oid, s in (result.get("objects") or {}).items()}})
