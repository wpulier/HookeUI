"""Helping the user get a good view (orchestrator/coach.py, roadmap W4-2)."""
from types import SimpleNamespace

from orchestrator import coach as C


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def frame(focus, steady=True, quality=(), disc=0.0):
    return SimpleNamespace(focus=focus, steady=steady, quality=quality, disc=disc)


def test_a_focus_sweep_says_sharper_then_past_the_sharpest():
    sent, clock = [], Clock()
    coach = C.Coach(sent.append, clock=clock)
    for f in [100, 112, 95, 104]:  # still, the scope's own breathing (under 30%): nothing sent
        coach.frame(frame(f))
        clock.t += 0.1
    assert sent == []
    for f in [60, 120, 200, 250, 262, 255, 210, 170, 140]:  # turning the knob through focus
        coach.frame(frame(f))
        clock.t += 0.3
    cues = [m["cue"] for m in sent if m["focus"] is not None]
    assert "sharper" in cues and cues[-1] == "passed"
    assert sent[-1]["focus"] < C.PASSED_SHARE
    clock.t += C.SWEEP_IDLE_S + 0.5
    for _ in range(4):
        coach.frame(frame(180))
        clock.t += 0.1
    assert sent[-1]["focus"] is None  # the sweep ended: the meter goes


def test_a_view_that_stays_dark_gets_one_word_spoken_once():
    sent, spoken, clock = [], [], Clock()
    coach = C.Coach(sent.append, speak=spoken.append, clock=clock)
    for _ in range(10):
        coach.frame(frame(120, quality=("dark",)))
        clock.t += 0.5
    assert spoken == [C.ADVICE["dark"]] and sent[-1]["advice"] == C.ADVICE["dark"]
    for _ in range(3):  # light fixed: the advice goes
        coach.frame(frame(120))
        clock.t += 0.5
    assert sent[-1]["advice"] is None
    for _ in range(10):  # dark again within a minute: shown, not spoken again
        coach.frame(frame(120, quality=("dark",)))
        clock.t += 0.5
    assert spoken == [C.ADVICE["dark"]] and sent[-1]["advice"] == C.ADVICE["dark"]


def test_quiet_modes_never_speak():
    spoken, clock = [], Clock()
    coach = C.Coach(lambda m: None, speak=spoken.append, quiet=lambda: True, clock=clock)
    for _ in range(12):
        coach.frame(frame(120, quality=("blurry",)))
        clock.t += 0.5
    assert spoken == []


# The phone's own autofocus hunting, as on the 2026-10-06 demo recording (84-88 s, a blank field):
# sharpness rose and fell three times in three seconds while the disc's radius went 384 -> 378 -> 384
# in step with it (focus breathing), then settled. The knob through focus (1917-1919 s): sharpness
# 166 -> 53 -> 155 with the radius steady to 0.02 px.
HUNT = [(23, 382.0), (27, 381.4), (37, 379.3), (43, 378.0), (40, 378.5), (32, 380.1), (18, 382.8), (16, 383.2),
        (24, 381.8), (34, 379.7), (31, 380.4), (17, 383.1), (13, 384.2), (20, 382.6), (21, 382.5)]
KNOB = [(160, 383.59), (150, 383.60), (140, 383.58), (124, 383.59), (114, 383.59), (78, 383.59), (57, 383.60),
        (53, 383.59), (62, 383.58), (87, 383.59), (111, 383.59), (131, 383.60), (146, 383.58), (155, 383.59)]
THROUGH = KNOB[7:] + KNOB[:4]  # up through the sharpest (160) and on past it


def play(coach, clock, frames, step=0.2):
    cues = []
    for focus, disc in frames:
        coach.frame(frame(focus, disc=disc))
        cues.append(coach.cue)
        clock.t += step
    return cues


def test_the_phone_hunting_is_not_taken_for_the_knob():
    sent, logs, clock = [], [], Clock()
    coach = C.Coach(sent.append, clock=clock, advise=lambda: False, log=logs.append)
    cues = play(coach, clock, HUNT)
    assert "phone" in cues and "passed" not in cues
    assert sent[-1]["cue"] == "phone" and sent[-1]["advice"] is None  # no written advice without the gate
    clock.t += C.SWEEP_IDLE_S + 0.5
    play(coach, clock, [(21, 382.5)] * 4)
    assert logs[-1]["kind"] == "coach-sweep" and logs[-1]["by"] == "phone" and logs[-1]["discChange"] > 1.0


def test_the_knob_through_focus_still_says_turn_back():
    sent, logs, clock = [], [], Clock()
    coach = C.Coach(sent.append, clock=clock, log=logs.append)
    cues = play(coach, clock, THROUGH)
    assert cues[-1] == "passed" and "phone" not in cues
    clock.t += C.SWEEP_IDLE_S + 0.5
    play(coach, clock, [(155, 383.59)] * 4)
    assert logs[-1]["by"] == "knob" and logs[-1]["discChange"] < 100 * C.LENS_SHARE


def test_with_the_option_off_every_sweep_is_the_knob():
    clock = Clock()
    coach = C.Coach(lambda m: None, clock=clock, lens=lambda: False)
    assert "phone" not in play(coach, clock, HUNT)


def test_an_unmeasured_disc_changes_nothing():
    """No disc measured (no eyepiece, too little rim in the frame): the meter works as before."""
    clock = Clock()
    coach = C.Coach(lambda m: None, clock=clock)
    cues = play(coach, clock, [(f, 0.0) for f, _ in THROUGH])
    assert cues[-1] == "passed"


def test_the_phones_advice_follows_the_advice_rules():
    sent, spoken, clock = [], [], Clock()
    coach = C.Coach(sent.append, speak=spoken.append, clock=clock)  # the QUALITY gate: code writes advice
    play(coach, clock, HUNT)
    assert spoken == [C.PHONE_ADVICE] and sent[-1]["advice"] == C.PHONE_ADVICE
    clock.t += C.SWEEP_IDLE_S + 0.5
    play(coach, clock, [(21, 382.5)] * 4)
    assert sent[-1]["advice"] is None  # the sweep is over: the advice goes
    play(coach, clock, HUNT)  # hunting again within the minute: shown, not spoken again
    assert spoken == [C.PHONE_ADVICE] and sent[-1]["advice"] == C.PHONE_ADVICE
    quiet_spoken = []
    quiet = C.Coach(lambda m: None, speak=quiet_spoken.append, quiet=lambda: True, clock=clock)
    play(quiet, clock, HUNT)
    assert quiet_spoken == []


def test_a_blank_field_is_never_a_sweep():
    """On a blank field sharpness is noise of about 1 that halves and doubles from frame to frame:
    no meter, no "turn back" (2026-10-06 demo: cues at sharpness 1.2-1.5 on an empty view)."""
    sent, clock = [], Clock()
    coach = C.Coach(sent.append, clock=clock)
    for f in [1.2, 0.7, 1.5, 0.6, 1.4, 0.7, 1.3, 0.6, 1.5, 0.7] * 3:
        coach.frame(frame(f, quality=("empty",)))
        clock.t += 0.1
    assert sent == [] and coach.sweeping_since is None
