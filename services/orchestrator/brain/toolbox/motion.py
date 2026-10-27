"""Watching what moves (orchestrator/movement.py): how many things move, how fast and how
straight, and whether they drift together. A query tool: the numbers go back to the model,
which says what they mean ("Are they slowing down?" is answered from them).

What it measured is kept as a tracks item (orchestrator/motiontracks.py, M3.2): the "motion" operation
runs through the runner, and the tracks are drawn over the live view only where their place is verified,
else on the view they were measured on. `of` keeps a followed outline's trail the same way."""
from __future__ import annotations

import asyncio
import math
import time

from ..registry import Registry, Tool, ToolContext, ToolFailed, record_made, record_output, schema
from .common import OBJECT, log_measurement, resolve_object

DEFAULT_S = 4.0  # what the hub keeps already: measured at once, from the frames just seen
LONGEST_S = 20.0


class InterruptedCameraInterval(ToolFailed):
    """This acquisition lost continuity; no measurement or replacement was made."""

    def __init__(self, message: str = "", next: str | None = None, *, reason: str | None = None,
                 original=None, offending=None) -> None:
        super().__init__(message, next=next)

        def facts(view):
            # Detached, byte-free facts from the exact held/rejected Capture;
            # retaining the exception must not retain camera pixels or an input lease.
            def integer(name):
                value = getattr(view, name, None)
                return value if isinstance(value, int) and not isinstance(value, bool) else None

            at = getattr(view, "wall_at", None)
            boot = getattr(view, "boot", None)
            tracked = getattr(view, "tracked", None)
            return {"frame": integer("id"), "seq": integer("seq"), "epoch": integer("epoch"),
                    "tracked": tracked if isinstance(tracked, bool) else None,
                    "at": at if isinstance(at, (int, float)) and not isinstance(at, bool) and math.isfinite(at) else None,
                    "boot": boot if isinstance(boot, str) and len(boot) <= 128 else None}

        self.interval = ({"reason": reason, "original": facts(original), "offending": facts(offending)}
                         if reason in {"basis_changed", "registration_lost", "nonmonotonic_time"} else None)


def recent(hub, seconds: float) -> list:
    """(frame, info) of the newest epoch over the last `seconds`, oldest first."""
    latest = hub.latest_info
    if latest is None:
        return []
    out = []
    for fid, info in list(hub.infos.items()):
        frame = hub.source.get(fid)
        if frame is not None and info.epoch == latest.epoch and latest.wall_at - info.wall_at <= seconds:
            out.append((frame, info))
    return out


async def gather(hub, seconds: float, *, fresh: bool = False, check=None, clocked: bool = False) -> list:
    """The last `seconds` of frames: from what the hub holds when that is enough, otherwise
    watching on until there are (a new epoch, the slide whipped away, starts again).

    `fresh` instead acquires only new arrivals, taking their bytes and immutable
    facts immediately after choosing each frame, before the next await. Its caller
    releases the temporary input; a CPU reader retains its own lease. A changed
    basis or incomplete interval fails without recovering another view.
    """
    if fresh:
        from ...capture import captures
        from ...motiontracks import MotionFrames

        cap = captures(hub)
        latest = hub.latest_info
        source = hub.source.get(latest.id) if latest is not None else None
        if source is None:
            raise ToolFailed("there is no original camera frame to start the upcoming interval", next=None)  # no next: original acquisition cannot start
        boundary = cap.adopt(latest, source.jpeg)
        frames = MotionFrames(cap)
        frames.received_times = {}
        last = boundary.id
        previous_received = getattr(source, "received_at", None)
        if clocked and (not isinstance(previous_received, (int, float)) or not math.isfinite(previous_received)):
            frames.release()
            raise ToolFailed("camera intake has no monotonic timestamp for this interval", next=None)  # no next: timing cannot be qualified
        boundary_received = previous_received
        deadline = time.monotonic() + seconds + 3.0

        def basis(view):
            optics = getattr(view, "scale", None) or {}
            return (view.epoch, view.boot, view.width, view.height, optics.get("objective"),
                    optics.get("fieldNumber"), optics.get("calibratedAt"), optics.get("optics"))

        original = basis(boundary)
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(1 / 60)
                if check is not None:
                    check()
                for fid, info in list(hub.infos.items()):
                    if fid <= last:
                        continue
                    frame = hub.source.get(fid)
                    if frame is None:
                        continue
                    captured = cap.adopt(info, frame.jpeg)
                    received = getattr(frame, "received_at", None)
                    if clocked and (not isinstance(received, (int, float)) or not math.isfinite(received)
                                    or received <= previous_received
                                    or abs((captured.wall_at - boundary.wall_at) -
                                           (received - boundary_received)) > .25):
                        raise ToolFailed("camera clocks changed during the interval; timing is unqualified",
                                         next=None)  # no next: this interval cannot be repaired with replacement frames
                    reason = ("basis_changed" if basis(captured) != original else
                              "registration_lost" if captured.tracked is False else
                              "nonmonotonic_time" if captured.wall_at <= (
                                  frames[-1][1].wall_at if frames else boundary.wall_at) else None)
                    if reason:
                        raise InterruptedCameraInterval(
                            "the camera interval changed basis or lost registration before it was complete",
                            next="try a new upcoming interval; the failed interval produced no measurement",
                            reason=reason, original=boundary, offending=captured)
                    frames.add(captured)
                    if clocked:
                        frames.received_times[captured.id] = received
                        previous_received = received
                    last = fid
                    elapsed = (received - frames.received_times[frames[0][1].id] if clocked else
                               frames[-1][1].wall_at - frames[0][1].wall_at)
                    if elapsed >= seconds:
                        return frames
            raise ToolFailed("not enough upcoming camera frames arrived for the requested motion interval",
                             next=None)  # no next: original interval was incomplete; no retained-view substitution
        except BaseException:
            frames.release()
            raise
    frames = recent(hub, seconds)
    deadline = time.monotonic() + seconds + 3.0
    while (not frames or frames[-1][1].wall_at - frames[0][1].wall_at < seconds * 0.9) and time.monotonic() < deadline:
        await asyncio.sleep(1 / 60)
        info = hub.latest_info
        frame = hub.source.get(info.id) if info is not None else None
        if frame is None or (frames and info.id == frames[-1][1].id):
            continue
        if frames and info.epoch != frames[-1][1].epoch:
            frames = []
        frames.append((frame, info))
    return frames


async def upcoming(hub, seconds: float, *, check=None) -> list:
    """Compatibility entry for new-arrival acquisition owned by `gather`."""
    return await gather(hub, seconds, fresh=True, check=check)


def add_motion_tools(registry: Registry, services) -> None:
    hub = services.hub
    if getattr(hub, "infos", None) is None or getattr(hub, "source", None) is None:
        return

    def scale_now():
        reading = hub.scale_reading() if hasattr(hub, "scale_reading") else None
        words = ""
        if reading is not None:
            words = (f"{reading.objective:g}x objective, " if reading.objective else "") + \
                ("measured scale" if reading.measured else "scale estimated from the objective")
        return reading, words

    async def keep_trail(ctx: ToolContext, args: dict) -> str:
        """Where a followed outline went (trail.py), kept as a tracks item with its breaks as they were."""
        from ...motiontracks import keep_followed, show

        obj = resolve_object(hub, args.get("of"), ctx)
        reading, _ = scale_now()
        item = await keep_followed(hub, obj, reading)
        record_made(ctx, item.id)  # "@n" to the steps after this one
        record_output(ctx, {"kind": "source", "source": {
            "day": item.day, "id": item.id, "version": None, "picture": 0}})
        said = f"Kept {item.title} ({item.id}): {item.words()}."
        if args.get("show", True) is not False:
            said += f" The trail: {await show(services, item)}."
        return said

    async def track_motion(ctx: ToolContext, args: dict) -> str:
        live = args.get('live', not args.get('of') and args.get('show', True) is not False)
        if not isinstance(live, bool):
            raise ToolFailed('live must be true or false', next=None)  # no next: the message states the required argument type
        if args.get("of"):
            if live:
                raise ToolFailed('a followed outline trail has no current-camera spot continuation', next=None)  # no next: this producer has no live outline-trail continuation
            return await keep_trail(ctx, args)
        try:
            seconds = float(args.get("seconds") or DEFAULT_S)
        except (TypeError, ValueError):
            seconds = DEFAULT_S
        seconds = max(1.5, min(LONGEST_S, seconds))
        frames = await gather(hub, seconds)
        if len(frames) < 10 or frames[-1][1].wall_at - frames[0][1].wall_at < 1.2:
            raise ToolFailed("the slide kept moving, so nothing could be followed: hold it still for a few seconds",
                             next=None)  # no next: the message says what to do
        reading, scale_words = scale_now()
        from ...motiontracks import hhmmss, keep_measured, show, track_layer_of
        from ...apply import apply

        # The "motion" operation, through the runner (an Execution like any other), kept as a tracks item (M3.2).
        item, reused, done = await keep_measured(hub, frames, reading, scale_words, seconds)
        record_made(ctx, item.id)  # "@n" to the steps after this one
        record_output(ctx, {"kind": "source", "source": {
            "day": item.day, "id": item.id, "version": None, "picture": 0}})
        if live:
            layer = track_layer_of(hub)
            issued = []
            admitted = await apply(hub, lambda _: issued.append(layer.live_source(item)), scope='motion')
            if not admitted.ok:
                raise ToolFailed(f'the current-camera continuation was not enabled ({admitted.reason})', next=None)  # no next: the original request's guarded apply was refused
            record_output(ctx, {'kind': 'source', 'source': issued[0]})
        m = done.output["movement"]
        log_measurement(services, ctx, "motion", seconds=round(m.seconds, 2), frames=m.frames, fps=round(m.fps, 2), unit=m.unit,
                        threshold=round(m.threshold, 4), slideSpeed=round(m.slide_speed, 4), drift=m.drift,
                        tracks=len(m.tracks), moving=len(m.moving), evidence=item.id, execution=done.execution.id,
                        reused=reused,
                        counted=[{k: (round(v, 4) if isinstance(v, float) else v) for k, v in c.items()} for c in m.counted[:200]],
                        how="small spots followed frame to frame, placed by the slide's own movement (movement.py)")
        said = str(done.output.get("words") or "")
        if reused:  # never presented as a fresh run (invariant 5)
            said = (f"(Reused: these same frames were measured at {hhmmss(done.execution.at)} for another request; "
                    "no new run was made.) " + said)
        said += f" Kept as {item.title} ({item.id})."
        if args.get("show", True) is not False and (m.counted or live):
            said = f"Display: {await show(services, item, current_camera=live)}. " + said
        if reading is None:
            said += " No scale is set, so speeds are in pixels a second (the user can name the objective)."
        return said

    registry.add(Tool("track_motion", "Watch what moves in the view and measure it: how many small things move, their "
                      "speed (net and along their path), how straight they go, and whether they drift together; keeps "
                      "the tracks and draws them where they still fit the view. Later steps can use present(what='@N') "
                      "for step N's exact motion trajectory layer (not specimen outlines), or present(source={step:N,output:0},do='open') for its "
                      "original captured motion picture in an independent window. Never predict a new mN ID. "
                      "Displayed motion continues on the current camera by default (live=true); use live=false "
                      "for only the retained measurement. Continuation also returns output 1: a current-camera spot/link source for "
                      "present(source={step:N,output:1},do='open'). It draws raw image-coordinate trails only on "
                      "their exact camera frame, including camera/stage motion; these are not stage-compensated "
                      "speeds or a learned motion model. The finite output 0 measurement stays unchanged. "
                      "show=false leaves the continuation dormant until a display owns it; hiding/closing its "
                      "last display stops its work. "
                      "Use it for questions about movement "
                      "(\"are they swimming?\", \"how fast?\", \"are they slowing down?\", \"why is everything drifting?\"). "
                      "It measures the last few seconds at once; more seconds watches on. With `of` (a followed outline: "
                      "\"where did it go?\"), keeps its trail instead. The numbers are measurements; say what they "
                      "suggest as your reading of them.",
                      schema({"seconds": {"type": "number", "description": f"how long to watch (default {DEFAULT_S:g}, "
                                                                           f"at most {LONGEST_S:g})"},
                              "of": OBJECT,
                              "live": {"type": "boolean", "description": "continue current-camera trails while shown or opened (default true when show=true and no of; otherwise false)"},
                              "show": {"type": "boolean", "description": "draw the tracks (default true)"}}),
                      track_motion, kind="query", inline_ms=8000, family="motion", outline_refs=("of",)))
