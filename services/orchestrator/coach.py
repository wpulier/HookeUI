"""Helping the user get a good view (roadmap W4-2): a focus meter while the focus knob turns,
and a word when the view stays too dark, washed out, empty or out of focus.

Gemini Live's Guided Vision tells people to pan or tilt; Cephla's Squid and ImSwitch stop an
autofocus sweep once sharpness falls below 85% of the best seen. Here the person turns the knob,
so the app watches the sweep and says when it went past the sharpest point.

- Focus: while the slide is still and sharpness is changing, it tracks the sweep's best value.
  "sharper" while it rises, "passed" once it falls below PASSED_SHARE of that best after having
  risen. Sent to the studio as a meter, a few times a second, only during a sweep.
- The phone, not the knob (option COACH "lens"): the iPhone's own autofocus makes the same
  sharpness swings when it hunts, and the phone tells the Mac nothing about its lens (Continuity
  Camera exposes no focus mode, lens position or lock to macOS: experiments/iphone-camera-feed,
  probe-camera-controls.sh). What the app can measure is the eyepiece disc: the knob never changes
  its size, the phone's lens does (focus breathing; fov.edge_radius has the numbers). A sweep
  during which the disc changed size by more than LENS_SHARE is the phone's: the meter's cue is
  "phone" ("the phone is refocusing by itself: leave the knob") instead of "turn back a little",
  and, with the QUALITY gate like the other advice, the coach writes what helps (PHONE_ADVICE).
- Advice: a quality problem (orchestrator/quality.py) that lasts ADVICE_AFTER_S becomes one
  plain sentence on what to do. It shows in the studio. It is spoken only when the voice is on,
  through the narrator's rules for unrequested lines (at most one every 20 s, never in quieter
  or "let me find things" mode), and the same advice at most once a minute. By default (QUALITY
  "tell") code writes no advice: the models judge the view. The phone's advice follows the same rules.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Callable

from .quality import MIN_PEAK

SWEEP_WINDOW_S = 1.0  # sharpness that changed by more than SWEEP_CHANGE within this is a sweep
# Above the hand-held scope's own breathing: on the June 14 footage, with nobody refocusing,
# sharpness swung 6% (median), 15% (p90) and at most 29% within a second.
SWEEP_CHANGE = 0.4
SWEEP_IDLE_S = 2.5  # no change for this long: the sweep is over
PASSED_SHARE = 0.85  # below this share of the sweep's best, after rising: went past the sharpest
RISE_SHARE = 1.15  # the best had to be this much above where the sweep started
ADVICE_AFTER_S = 4.0  # a problem that lasts this long gets a word
SAME_ADVICE_S = 60.0  # the same advice is offered at most this often
SEND_EVERY_S = 0.25  # the meter is sent this often during a sweep
# The disc's size changed by more than this share during a sweep: the phone's camera changed, not
# the knob. Measured on the 2026-10-06 demo recording (38 min, iPhone on the eyepiece; the coach run
# over it found 98 sweeps on a textured view): sweeps where the user turned the knob through focus
# changed the radius by at most 0.05% (41 sweeps; 0.006% in steady 1 s windows), the phone hunting
# on a blank field by 1-2.4% (33 sweeps over 1.6%). 0.3% is about 1.1 px on a 380 px disc: six
# times the most a knob turn moved it, under a fifth of a hunt.
LENS_SHARE = 0.003

ADVICE = {
    "dark": "The view is too dark: open the diaphragm or turn the light up.",
    "bright": "The view is washed out: close the diaphragm a little or turn the light down.",
    "empty": "There is nothing on the slide here: move the slide to find the specimen.",
    "blurry": "The view is out of focus: turn the fine focus slowly until it is sharp.",
}
# Continuity Camera has no focus lock (experiments/iphone-camera-feed/LEARNINGS.md), so the help is
# to let it settle; it hunts most where it has little to focus on (the demo's hunts were on a blank
# field), and the Camo app or a custom capture app can lock it.
PHONE_ADVICE = ("That change in focus is the phone refocusing by itself, not the focus knob: leave the knob "
                "until it settles. It hunts most on an empty or faint view, so find some detail first.")


def is_sweep(values: list[float]) -> bool:
    """Sharpness that changed by more than SWEEP_CHANGE within the window, on a view with texture
    enough for sharpness to mean anything (quality.MIN_PEAK, as for the blurry flag): on a blank
    field sharpness is noise of about 1, which changes by half from frame to frame. The coach run
    over the 2026-10-06 demo's camera recording (38 min, per-frame facts from Hub._analyse_batch):
    without it 179 sweeps and 24 "past the sharpest" cues, 15 of them on views whose sharpness never
    reached 20; with it 80 sweeps and 11 cues, 1 such; the knob turned through focus (1927-1968 s)
    keeps its meter. The June 14 recording (textured throughout): 6 sweeps either way."""
    return len(values) > 3 and max(values) >= MIN_PEAK and (max(values) - min(values)) / max(max(values), 1.0) > SWEEP_CHANGE


class Coach:
    def __init__(self, send: Callable[[dict], None], speak: Callable[[str], None] | None = None,
                 quiet: Callable[[], bool] = lambda: False, clock: Callable[[], float] = time.monotonic,
                 advise: Callable[[], bool] = lambda: True, lens: Callable[[], bool] = lambda: True,
                 log: Callable[[dict], None] | None = None) -> None:
        self.send, self.speak, self.quiet, self.clock = send, speak, quiet, clock
        self.advise = advise  # whether code writes advice at all (only with the QUALITY gate; app.py)
        self.lens = lens  # whether a sweep that changed the disc is the phone's (option COACH "lens")
        self.log = log
        self.recent: deque[tuple[float, float]] = deque()  # (time, focus)
        self.discs: deque[tuple[float, float]] = deque()  # (time, disc radius px), same window
        self.sweeping_since: float | None = None
        self.last_change = 0.0
        self.start = self.best = 0.0
        self.cue: str | None = None
        self.disc_lo = self.disc_hi = 0.0  # the disc's smallest and largest radius during the sweep
        self.phone = False  # this sweep changed the disc: the phone's camera, not the knob
        # The view's brightness during the sweep (quality.view_stats mean), logged only: on the
        # 2026-10-06 demo brightness and sharpness moved opposite ways (defocus brightens a
        # brightfield view), not as an exposure gain would, so nothing is decided from it yet.
        self.bright_lo = self.bright_hi = 0.0
        self.passed = False  # the meter said "past the sharpest" during this sweep (logged)
        self.problem: tuple[str, float] | None = None  # (kind, since)
        self.advised: dict[str, float] = {}
        self.view_advice: str | None = None  # a view that stays bad (only with the QUALITY gate)
        self.phone_advice: str | None = None  # during a sweep that is the phone's
        self.last_sent = 0.0

    @property
    def shown(self) -> str | None:
        return self.view_advice or self.phone_advice

    def wire(self) -> dict:
        """The coach's state now, for a studio that (re)connects."""
        sweeping = self.sweeping_since is not None
        return {"type": "coach", "focus": round(self.recent[-1][1] / max(self.best, 1.0), 2) if sweeping and self.recent else None,
                "cue": self.cue if sweeping else None, "advice": self.shown}

    def frame(self, info) -> None:
        """Every published frame (Hub.on_frame)."""
        now = self.clock()
        self._focus(info, now)
        self._advice(info, now)

    # --- the focus knob -----------------------------------------------------------------------
    def _focus(self, info, now: float) -> None:
        if not info.steady or info.focus <= 0:
            self._end_sweep(now)
            return
        self.recent.append((now, info.focus))
        disc = getattr(info, "disc", 0.0) or 0.0
        if disc > 0:
            self.discs.append((now, disc))
        for kept in (self.recent, self.discs):
            while kept and now - kept[0][0] > SWEEP_WINDOW_S:
                kept.popleft()
        changing = is_sweep([f for _, f in self.recent])
        if changing:
            self.last_change = now
            if self.sweeping_since is None:
                values = [f for _, f in self.recent]
                self.sweeping_since, self.start, self.best, self.cue = now, values[0], max(values), None
                # The sweep began with the window that showed it, so the disc's size then counts too.
                sizes = [d for _, d in self.discs]
                self.disc_lo, self.disc_hi = (min(sizes), max(sizes)) if sizes else (0.0, 0.0)
                self.phone = False
        if self.sweeping_since is None:
            return
        if now - self.last_change > SWEEP_IDLE_S:
            self._end_sweep(now)
            return
        if disc > 0:
            self.disc_lo = min(self.disc_lo, disc) if self.disc_lo > 0 else disc
            self.disc_hi = max(self.disc_hi, disc)
        stats = getattr(info, "stats", ()) or ()
        if len(stats) >= 2 and stats[1] > 0:
            self.bright_lo = min(self.bright_lo, stats[1]) if self.bright_lo > 0 else stats[1]
            self.bright_hi = max(self.bright_hi, stats[1])
        if not self.phone and self.lens() and self._disc_change() > LENS_SHARE:
            self.phone = True
            self._phone_refocusing(now)
        self.best = max(self.best, info.focus)
        rose = self.best > RISE_SHARE * max(self.start, 1.0)
        if self.phone:
            self.cue = "phone"
        elif rose and info.focus < PASSED_SHARE * self.best:
            self.cue, self.passed = "passed", True
        elif info.focus >= PASSED_SHARE * self.best:
            self.cue = "sharper" if rose else None
        if now - self.last_sent >= SEND_EVERY_S:
            self.last_sent = now
            self.send({"type": "coach", "focus": round(info.focus / max(self.best, 1.0), 2), "cue": self.cue,
                       "advice": self.shown})

    def _disc_change(self) -> float:
        """How much the disc's radius changed during this sweep, as a share of it (0 unmeasured)."""
        return (self.disc_hi - self.disc_lo) / self.disc_hi if self.disc_lo > 0 else 0.0

    def _phone_refocusing(self, now: float) -> None:
        """This sweep is the phone's own lens (COACH "lens"), and the meter's cue says so. The
        written advice follows the view advice's rules: only when code writes advice (the QUALITY
        gate), shown at once, spoken only through the narrator's rules (voice on, not quiet) and at
        most once a minute."""
        if not self.advise():
            return
        self.phone_advice = PHONE_ADVICE
        if self.speak is not None and not self.quiet() and now - self.advised.get("phone", -1e9) >= SAME_ADVICE_S:
            self.advised["phone"] = now
            self.speak(PHONE_ADVICE)

    def _end_sweep(self, now: float) -> None:
        if self.sweeping_since is not None:
            if self.log is not None:
                # Every sweep, whose it was and what the disc did, so live use can be checked.
                self.log({"kind": "coach-sweep", "by": "phone" if self.phone else "knob", "seconds": round(now - self.sweeping_since, 1),
                          "discChange": round(100 * self._disc_change(), 3), "best": round(self.best, 1), "start": round(self.start, 1),
                          "brightChange": round(100 * (self.bright_hi - self.bright_lo) / self.bright_hi, 1) if self.bright_lo > 0 else None,
                          "passed": self.passed, "lens": self.lens()})
            self.sweeping_since, self.cue, self.phone, self.phone_advice, self.passed = None, None, False, None, False
            self.bright_lo = self.bright_hi = 0.0
            self.send({"type": "coach", "focus": None, "cue": None, "advice": self.shown})
        self.recent.clear()
        self.discs.clear()

    # --- a view that stays bad ----------------------------------------------------------------
    def _advice(self, info, now: float) -> None:
        kinds = [k for k in ("empty", "dark", "bright", "blurry") if k in (getattr(info, "quality", ()) or ())] \
            if self.advise() else []
        kind = kinds[0] if kinds else None
        if kind is None:
            self.problem = None
            if self.view_advice is not None:
                self.view_advice = None
                self.send({"type": "coach", "focus": None, "cue": None, "advice": self.shown})
            return
        if self.problem is None or self.problem[0] != kind:
            self.problem = (kind, now)
            return
        if now - self.problem[1] < ADVICE_AFTER_S or self.view_advice == ADVICE[kind]:
            return
        self.view_advice = ADVICE[kind]
        self.send({"type": "coach", "focus": None, "cue": None, "advice": self.shown})
        if self.speak is not None and not self.quiet() and now - self.advised.get(kind, -1e9) >= SAME_ADVICE_S:
            self.advised[kind] = now
            self.speak(self.view_advice)
