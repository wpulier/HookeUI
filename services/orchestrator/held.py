"""Footage a researcher asked for, held when the user said yes (the partner §2.2, evidence requests).

A researcher asks the user for evidence it lacks (`request_evidence`: "15 more seconds of this field").
When the user agrees, the brain passes the answer on with how many seconds to hold
(`researcher_note(answers=q1, hold_seconds=15)`), and from then the app keeps a frame of the live view
every EVERY_S for that long: real frames (the camera's own JPEGs, by reference), with what the hub
measured about each (its slide offset, epoch, whether it was steady). Nothing is invented and nothing
is written to disk: this is memory under the "unsaved" allowance (capture.py, contract 1), not a
recording; Record and Save stay the user's own (ADR 0008). A hold that would go over the allowance
stops there and says so.

A hold needs the user's own yes, quoted and found in what they said (`policy.quoted`), to a question that
asked for footage; it is capped at the seconds asked for. It is released when its researcher's run ends (or
the user stops it: `hold_seconds: 0` without an answer, in their own words), and a new day clears the rest.
A hold that stops early (the memory allowance, no frames from the camera, or its researcher's run ending
first) is told to the user where they said yes (the announcer: shown, and spoken when the voice is on), not
only to the researcher (ADR 0006).

A held clip is an entry of the session's index (kind "clip"; ids new for the session it was held in and for the
day's folder, day/ids.py, so unique across restarts; journaled as a product of that session), so the researcher
reads it with its `session` tool like anything else the session made, and the brain and the studio see it there.
Each frame keeps the bridge's own number for it (`boot`, `seq`).
"""
from __future__ import annotations

import asyncio
import copy
import itertools
import time
from dataclasses import dataclass, field

from research_worker.limits import HOLD_MAX_S

EVERY_S = 0.5
MAX_SECONDS = float(HOLD_MAX_S)
WATCH_EVERY_S = 1.0  # how often a hold with no frames coming is checked (tick)


@dataclass
class HeldFrame:
    frame: int
    jpeg: bytes
    at: float
    dx: float
    dy: float
    epoch: int
    steady: bool
    seq: int | None = None  # the bridge's number for it (frames.Frame.seq): essential provenance
    boot: str = ""  # the bridge run that numbered it
    info: object = None  # its frame's facts as they were (a copy): size, eyepiece; a researcher's run uses them
    scale: dict | None = None  # the scale in force when it was held (capture.scale_at): what its sizes are measured in

    def facts(self, n: int) -> dict:
        return {"n": n, "frame": self.frame, "seq": self.seq, "boot": self.boot, "at": round(self.at, 3),
                "dx": round(self.dx, 1), "dy": round(self.dy, 1), "epoch": self.epoch, "steady": self.steady}


@dataclass
class HeldClip:
    id: str
    rid: str
    researcher: str  # its name ("Researcher 2")
    request: str  # the question it answers ("q1")
    seconds: float
    day: str  # the calendar date it was held on
    started_at: float = field(default_factory=time.time)
    frames: list[HeldFrame] = field(default_factory=list)
    status: str = "holding"  # holding | held | stopped | released
    why: str = ""  # why it stopped early
    reservation: object = None
    before_release: str = ""  # what it held, in words, kept when its frames went
    said: dict = field(default_factory=dict)  # where the user said yes: channel, delegation_id, asked (day/model.Asked)
    cut_short: bool = False  # still holding when its researcher's run ended (or a new day started)
    by_user: bool = False  # the user stopped it (hold_seconds 0): the tool's answer says so; nothing more to tell
    stopped_why: str = ""  # why it stopped early, kept when it is released afterwards
    kept: tuple[int, float] = (0, 0.0)  # frames and seconds it had when it stopped or was released
    session: str = ""  # the session it was held in (ADR 0017); "" with no session store (a test's)

    def span(self) -> float:
        return self.frames[-1].at - self.frames[0].at if len(self.frames) > 1 else 0.0

    def words(self) -> str:
        """What is held, in the app's words only (never the researcher's)."""
        base = f"{len(self.frames)} frames over {self.span():.0f} s, a frame every {EVERY_S:g} s, held for {self.researcher}'s question {self.request}"
        if self.status == "holding":
            return base + f"; still holding (until {self.seconds:g} s)"
        if self.status == "stopped":
            return base + f"; stopped early: {self.why}"
        if self.status == "released":
            return f"{self.before_release}; released: {self.why}"
        return base

    def stop_words(self) -> str:
        """What the user is told when a hold they agreed to ended early (the app's words only)."""
        n, span = self.kept if self.status == "released" else (len(self.frames), self.span())
        held = f"{n} frame{'s' if n != 1 else ''} over {_seconds(span)} s" if n else "no frames"
        if self.status == "released" and not self.cut_short and self.stopped_why:  # stopped early, then released
            return (f"Holding footage for {self.researcher} stopped early ({self.stopped_why}): {held}; then {self.why}, "
                    f"so {'they were' if n else 'nothing was'} let go.")
        if self.status == "released":
            return (f"Holding footage for {self.researcher} stopped early: {self.why}, after {held} of the "
                    f"{self.seconds:g} s agreed" + ("; they were let go with it." if n else "."))
        return (f"Holding footage for {self.researcher} stopped early ({self.why}): {held}"
                + (f" are kept for it as {self.id}." if n else "."))


class Held:
    def __init__(self, hub, memory=None, clock=time.time, log=None) -> None:
        self.hub = hub
        self.memory = memory
        self.clock = clock
        self.log = log
        self.clips: dict[str, HeldClip] = {}
        self._ids = itertools.count(1)
        self._given = 0  # the highest number given in this run (day/ids.py)
        self.store = None  # the session store (app.py): ids, and each clip journaled as a product of its session
        self.on_held: list = []  # callbacks(clip) when a clip is held or stopped
        self._watch: asyncio.Task | None = None

    def start(self, rid: str, researcher: str, request: str, seconds: float, day: str, said: dict | None = None,
              session: str | None = None) -> HeldClip:
        """Hold footage for researcher `rid`'s question, in `session` (the researcher's; default the one open)."""
        seconds = max(1.0, min(MAX_SECONDS, float(seconds)))
        store = self.store
        session = session or getattr(store, "sid", "") or ""
        if store is not None:
            from .day.ids import next_number, owner

            # New for its session (on any of its days) and for today's folder: a restart's k1 is never another clip a
            # finished researcher's evidence names (its "made" line keeps the number taken).
            self._given = next_number(owner(store, session), "k", self._given, getattr(store, "date", None) or day)
            cid = f"k{self._given}"
        else:  # no session store (a test's): unique across restarts from the time it started
            cid = f"k{int(self.clock() * 1000) % 10**10}{next(self._ids)}"
        clip = HeldClip(cid, rid, researcher, request, seconds, getattr(store, "date", None) or day,
                        said=dict(said or {}), session=session)
        if store is not None:
            try:
                store.made("clip", clip.id, day=clip.day, session=session, kept=False)  # memory only: its id kept
            except Exception:
                pass  # the footage is held whatever happens to its line in the journal
        memory = self.memory if self.memory is not None else self._memory()
        if memory is not None:
            clip.reservation = memory.reserve("unsaved", 0, f"footage held for {researcher}")
        self.clips[clip.id] = clip
        self._watching()
        return clip

    def _watching(self) -> None:
        """While anything is holding, a hold whose frames stopped coming is ended every WATCH_EVERY_S (tick):
        started with the first hold, on the app's loop, and done when nothing holds."""
        if self._watch is not None and not self._watch.done():
            return
        try:
            self._watch = asyncio.get_running_loop().create_task(self._watch_loop())
        except RuntimeError:  # no loop (a test): tick is called by hand
            self._watch = None

    async def _watch_loop(self) -> None:
        while any(c.status == "holding" for c in self.clips.values()):
            await asyncio.sleep(WATCH_EVERY_S)
            self.tick()

    def _memory(self):
        from .capture import captures

        try:
            return captures(self.hub).memory
        except Exception:
            return None

    def frame(self, info) -> None:
        """Every published frame (Hub.on_frame): a frame every EVERY_S into each clip still holding."""
        holding = [c for c in self.clips.values() if c.status == "holding"]
        if not holding:
            return
        now = float(getattr(info, "wall_at", None) or self.clock())
        source = getattr(self.hub, "source", None)
        for clip in holding:
            if clip.frames and now - clip.frames[-1].at < EVERY_S:
                continue
            if now - clip.started_at > clip.seconds + EVERY_S / 2:
                self._finish(clip, "held")
                continue
            got = source.get(info.id) if source is not None else None
            jpeg = getattr(got, "jpeg", None) if got is not None else None
            if not jpeg:
                continue
            from .capture import scale_at

            if clip.reservation is not None:
                from .capture import Refused

                try:
                    clip.reservation.grow(len(jpeg), f"footage held for {clip.researcher}")
                except Refused:
                    self._finish(clip, "stopped", "the memory for unsaved evidence is full")
                    continue
            clip.frames.append(HeldFrame(int(info.id), jpeg, now, float(getattr(info, "dx", 0.0)), float(getattr(info, "dy", 0.0)),
                                         int(getattr(info, "epoch", 0)), bool(getattr(info, "steady", True)),
                                         getattr(info, "seq", None), str(getattr(info, "boot", "") or ""),
                                         copy.copy(info), scale_at(self.hub, info)))

    def _finish(self, clip: HeldClip, status: str, why: str = "") -> None:
        clip.status, clip.why = status, why
        if status == "stopped":
            clip.stopped_why = why
        self._notify(clip)

    def _notify(self, clip: HeldClip) -> None:
        for callback in list(self.on_held):
            try:
                callback(clip)
            except Exception as exc:  # a broken listener never stops the hold; it is logged, never swallowed unsaid
                if self.log is not None:
                    self.log({"kind": "held-error", "clip": clip.id, "error": f"{type(exc).__name__}: {exc}"[:200]})

    def tick(self, now: float | None = None) -> None:
        """Ends holds whose time is up although no frame came to end them (the camera stopped)."""
        now = self.clock() if now is None else now
        for clip in [c for c in self.clips.values() if c.status == "holding"]:
            if now - clip.started_at > clip.seconds + 2 * EVERY_S:
                last = clip.frames[-1].at if clip.frames else clip.started_at
                if now - last > 2 * EVERY_S and (not clip.frames or clip.span() < clip.seconds - 2 * EVERY_S):
                    self._finish(clip, "stopped", "no frames came from the camera")
                else:
                    self._finish(clip, "held")

    def release(self, rid: str, why: str) -> list[HeldClip]:
        """Its researcher's run ended, or the user cancelled: the frames go and their memory is freed."""
        released = []
        for clip in [c for c in self.clips.values() if c.rid == rid and c.status != "released"]:
            if clip.status == "holding":  # the run ended first: what the user agreed to was cut short, and is said
                clip.status, clip.why, clip.cut_short = "stopped", why, True
            clip.kept = (len(clip.frames), clip.span())
            clip.before_release = clip.words()
            clip.frames = []
            if clip.reservation is not None:
                clip.reservation.release()
            clip.status, clip.why = "released", why
            released.append(clip)
            if clip.cut_short:
                self._notify(clip)
        return released

    def cancel(self, rid: str) -> list[HeldClip]:
        """The user cancelled holding footage for this researcher."""
        cancelled = []
        for clip in [c for c in self.clips.values() if c.rid == rid and c.status == "holding"]:
            clip.by_user = True  # the tool's answer tells the user; the researcher is still told
            self._finish(clip, "stopped", "cancelled by you")
            cancelled.append(clip)
        return cancelled

    def picture(self, clip_id: str, n: int = 0) -> bytes | None:
        clip = self.clips.get(clip_id)
        if clip is None or not 0 <= n < len(clip.frames):
            return None
        return clip.frames[n].jpeg

    def clear(self) -> None:
        """Another session was opened: what was held for the researchers of the one left goes (it is memory, never
        written); one still holding is told as cut short."""
        for rid in {c.rid for c in self.clips.values()}:
            # Released, not just dropped: one still holding is told as cut short, and one that stopped a moment
            # ago is told as let go, never "kept for it" (its telling may still be queued).
            self.release(rid, "another session was opened")
        self.clips.clear()


def telling(clip: HeldClip) -> dict:
    """Whom a hold's end tells, decided when it ends (a run ending in the same loop step changes the clip before
    the telling runs): the researcher's note, and whether the user is told (it stopped early and they did not
    stop it). What the user is told is the clip as it is when the telling runs (stop_words)."""
    early = clip.status == "stopped" or clip.cut_short
    note = (f"{clip.id} was let go before it was held in full ({clip.why}): it is not in the session any more."
            if clip.cut_short else f"{clip.id} is held: {clip.words()}. Read it with the session tool, id {clip.id}.")
    return {"note": note, "user": early and not clip.by_user}


def tell(clip: HeldClip, research, announcer, log=None, told: dict | None = None) -> None:
    """When a hold ends: the researcher is told it is held (or why it stopped); the user is told when it stopped
    early or was cut short (its run ended, a new day), where they said yes (ADR 0006); not when the user stopped
    it themselves (the tool's answer said so). Called off the frame path (a note and a chat line write to disk).
    The user's line never waits on the researcher's note. `told`: telling(clip), as decided when it ended."""
    from .research.policy import ResearchRefused

    told = told or telling(clip)
    if told["note"]:
        try:
            research.note(clip.rid, told["note"], about={"held": clip.id})  # a finish waiting on it goes on
        except ResearchRefused:
            pass  # it has finished: the footage stays in the session's index for a follow-up
        except Exception as exc:  # the note failed (its inbox): logged; the user is still told below
            if log is not None:
                log({"kind": "held-error", "clip": clip.id, "error": f"{type(exc).__name__}: {exc}"[:200]})
    if told["user"]:
        announcer.held_stopped(clip, clip.stop_words())


def wire(held: Held, research, announcer, log=None) -> None:
    """The live wiring (App.build): a hold's end is told off the frame path (tell), and its frames go when its
    researcher's run ends (release; one still holding then is told as cut short)."""

    def said(clip, told) -> None:
        try:
            tell(clip, research, announcer, log, told)
        except Exception as exc:  # never lost unsaid: logged
            if log is not None:
                log({"kind": "held-error", "clip": clip.id, "error": f"{type(exc).__name__}: {exc}"[:200]})

    def done(clip) -> None:
        told = telling(clip)  # decided now: the telling runs after this loop step
        try:
            asyncio.get_running_loop().call_soon(said, clip, told)
        except RuntimeError:  # no loop (a test): at once
            said(clip, told)

    def release_with_run(session) -> None:
        # Held for its work: released when a run of its work ends (the one it was held for, or the carry-on it was held
        # into), never by a follow-up answer or a new version in between.
        if getattr(session, "answered", None) is not None or getattr(session, "revised", None) is not None:
            return
        held.release(session.rid, "its run ended")

    held.on_held.append(done)
    research.on_done.append(release_with_run)


def _seconds(x: float) -> str:
    """0.5, 3, 12.5: a span in seconds without a rounding that hides it (2 frames are "over 0.5 s", not "0 s")."""
    return f"{x:.1f}".rstrip("0").rstrip(".") or "0"


def held_of(hub) -> Held:
    found = getattr(hub, "held", None)
    if not isinstance(found, Held):
        found = Held(hub)
        try:
            hub.held = found
        except AttributeError:
            pass
    return found
