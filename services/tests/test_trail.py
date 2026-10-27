"""A followed outline's trail (orchestrator/trail.py; the partner §2.1, UX §1 "Motion the user can
trust"): its own places from the follower's verified steps, in slide coordinates; only the
follower's own moves count as motion (the hub's re-anchor nudges do not); broken at every gap,
loss, epoch and stretch nothing observed, never joined; how far and over how long, in µm with the
scale's source and px without."""
import asyncio
from types import SimpleNamespace

from orchestrator.objtrack import MISSES_TO_DROP
from orchestrator.scene import SceneObject
from orchestrator.scale import Reading
from orchestrator.trail import REACH_PX, SHOWN_PX, STALE_S, WINDOW_S, Trails, centre
from tests.test_objtrack import make, square


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


ESTIMATED = Reading(um_per_px=0.5, field_um=600.0, objective=40, measured=False)
MEASURED = Reading(um_per_px=0.5, field_um=600.0, objective=40, measured=True)


def outline(x, y, offset=(0.0, 0.0), epoch=0, track="tracking"):
    return SceneObject(id="o1", kind="highlight", polygons=square(x, y), anchor_frame=1, anchor_epoch=epoch,
                       anchor_offset=offset, source="click", track=track)


def test_a_place_is_kept_in_slide_coordinates_so_the_slides_own_motion_is_taken_out():
    clock = Clock()
    trails = Trails(clock)
    # The slide carries the outline 30 px right; the outline is re-anchored there each time.
    for k in range(4):
        clock.t += 0.6
        trails.observe(outline(100 + 10 * k, 100, offset=(10.0 * k, 0.0)), "seen", 0)
    trail = trails.trails["o1"]
    assert {tuple(p[:2]) for p in trail.segments[0].points} == {(100.0, 100.0)}  # it stayed put on the slide
    assert trail.path_px() == 0 and trails.words("o1", ESTIMATED) is None
    assert centre(outline(130, 100, offset=(30.0, 0.0))) == (100.0, 100.0)


def test_gaps_losses_and_epochs_break_a_trail_and_nothing_joins_them():
    clock = Clock()
    trails = Trails(clock)

    def step(x, state="seen", epoch=0, obj_epoch=0):
        clock.t += 0.2
        trails.observe(outline(x, 100, epoch=obj_epoch), state, epoch, wrote=True)

    step(100); step(110); step(120)
    step(125, "missed")  # EdgeTAM did not find it: its place is not known
    step(140); step(150)
    step(150, "unsure")  # a smeared view
    step(160, "lost")  # dropped, or being re-found
    step(170, "missed")
    step(200); step(210)
    step(300, epoch=1, obj_epoch=1)  # another epoch: not comparable
    segs = trails.trails["o1"].segments
    assert [(s.start, s.end, [p[0] for p in s.points]) for s in segs] == [
        ("first", "gap", [100, 110, 120]),
        ("after-gap", "lost", [140, 150]),  # an unsure step closed it; the loss that followed says so
        ("reacquired", "gap", [200, 210]),  # found again after a loss, the miss in between remembered
        ("after-gap", "open", [300]),
    ]
    # The path runs along the drawn segments only: 20 + 10 + 10, never across a break.
    assert trails.trails["o1"].path_px() == 40
    assert trails.trails["o1"].breaks() == (2, 1)
    wire = trails.wire(None)
    assert wire["type"] == "trails" and wire["trails"][0]["id"] == "o1" and wire["windowS"] == WINDOW_S


def test_its_words_say_how_far_and_how_long_in_um_with_a_scale_and_px_without():
    clock = Clock()
    trails = Trails(clock)
    for k in range(11):
        clock.t += 0.5
        trails.observe(outline(100 + 3 * k, 100), "seen", 0, wrote=True)
    assert trails.trails["o1"].path_px() == 30 >= SHOWN_PX
    assert trails.words("o1", MEASURED) == "15 µm over the slide in 5 s"
    assert trails.words("o1", ESTIMATED) == "about 15 µm (scale estimated from the objective) over the slide in 5 s"
    assert trails.words("o1", None) == "30 px (no scale yet) over the slide in 5 s"
    clock.t += 0.5
    trails.observe(outline(140, 100), "lost", 0)
    clock.t += 5.0
    trails.observe(outline(150, 100), "seen", 0, wrote=True)
    clock.t += 1.0
    trails.observe(outline(160, 100), "seen", 0, wrote=True)
    # Only where its motion was measured: 30 + 10 px, in 5 + 1 of the 11.5 s, never across the loss.
    assert trails.words("o1", None) == "40 px (no scale yet) over the slide in the 6 s of the last 12 s its motion was measured (lost 1x)"


def test_old_places_age_out_and_a_removed_outlines_trail_goes_with_it():
    clock = Clock()
    trails = Trails(clock)
    trails.observe(outline(100, 100), "seen", 0)
    clock.t += WINDOW_S + 1
    trails.observe(outline(120, 100), "seen", 0)
    assert [p[0] for s in trails.trails["o1"].segments for p in s.points] == [120]
    trails.keep_only({"o2": object()})
    assert not trails.trails


def follow(steps, **infos):
    follower, hub = make()
    hub.trails = Trails(Clock())
    for where in steps:
        hub.trails.clock.t += 0.1
        hub.gpu_heavy.where["o1"] = where
        asyncio.run(follower.step())
    return follower, hub


def test_the_follower_records_only_places_it_verified_and_breaks_where_it_did_not():
    # It swims right 20 px a step; then EdgeTAM misses it once; then it is seen again.
    follower, hub = follow([(140, 100), (160, 100), None, (180, 100)])
    segs = hub.trails.trails["o1"].segments
    # Slide offset 5 px: its slide place is the outline's centre minus 5.
    assert [(s.start, s.end, [p[0] for p in s.points]) for s in segs] == [
        ("first", "gap", [135.0, 155.0]), ("after-gap", "open", [175.0])]


def test_an_object_edgetam_drops_is_lost_on_its_trail():
    follower, hub = follow([(140, 100)] + [None] * MISSES_TO_DROP)
    seg = hub.trails.trails["o1"].segments[-1]
    assert seg.end == "lost"  # the first miss closed it; the drop after the misses says it was lost (a cross)
    assert hub.trails.trails["o1"].after == "reacquired"


def test_the_hubs_re_anchor_nudges_are_not_the_objects_own_motion():
    """Review of #155, T1: a still cell followed through four gentle pans, each followed by the hub's
    ~3 px drift correction, is not said to have moved."""
    clock = Clock()
    trails = Trails(clock)
    x = 100.0
    for _ in range(4):
        for _ in range(5):  # the follower sees it where the slide puts it (no write of its own)
            clock.t += 0.1
            trails.observe(outline(x, 100), "seen", 0)
        x += 3.0  # the hub's re-anchor nudges the outline: not the follower's move
    clock.t += 0.1
    trails.observe(outline(x, 100), "seen", 0)
    trail = trails.trails["o1"]
    assert trail.path_px() == 0 and trails.words("o1", None) is None
    assert trail.segments[-1].points[-1][0] == x  # the trail moved with its outline: still on it
    # A back-and-forth the follower did write, never far from where it started, is not "went somewhere".
    trails2 = Trails(clock)
    for k in range(8):
        clock.t += 0.6
        trails2.observe(outline(100 + (4 if k % 2 else 0), 100), "seen", 0, wrote=True)
    assert trails2.trails["o1"].path_px() >= SHOWN_PX and trails2.trails["o1"].reach_px() < REACH_PX
    assert trails2.words("o1", None) is None


def test_nothing_observed_for_a_while_breaks_the_trail_and_a_reset_or_switch_off_ends_it():
    """Review of #155, T2 and should-fix 1: never joined across time nothing observed."""
    clock = Clock()
    trails = Trails(clock)
    trails.observe(outline(100, 100), "seen", 0, wrote=True)
    clock.t += STALE_S + 11.0  # a step timed out; following was reset and slept
    trails.observe(outline(160, 100), "seen", 0, wrote=True)
    segs = trails.trails["o1"].segments
    assert [(s.end, len(s.points)) for s in segs] == [("gap", 1), ("open", 1)] and trails.trails["o1"].path_px() == 0
    follower, hub = follow([(140, 100), (160, 100)])
    follower.resync("a step timed out")
    assert hub.trails.trails["o1"].segments[-1].end == "gap"
    follower, hub = follow([(140, 100), (160, 100)])
    hub.trails.end("o1", "stopped")
    clock2 = hub.trails.clock
    clock2.t += 0.1
    obj = hub.scene.objects["o1"]
    hub.trails.observe(obj, "seen", 0, wrote=True)
    # A stop is a break: the words count only the known time.
    assert hub.trails.trails["o1"].breaks() == (1, 0)


def test_the_window_is_held_in_the_words_and_places_carry_their_frames_time():
    clock = Clock()
    clock.t = 905.0  # the step finished after its frame: the place keeps the frame's own time
    trails = Trails(clock)
    for k in range(6):
        trails.observe(outline(100 + 5 * k, 100), "seen", 0, wrote=True, at=900.0 + k * 0.6, frame=10 + k)
    trail = trails.trails["o1"]
    assert [p[2] for p in trail.segments[0].points][:2] == [900.0, 900.6] and trail.segments[0].points[0][3] == 10
    assert trails.words("o1", None) is not None
    clock.t = 900.0 + WINDOW_S + 10  # a swimmer that left the view: its words go with the window
    assert trails.words("o1", None) is None


def test_a_blurred_view_gives_no_place():
    follower, hub = make()
    hub.trails = Trails(Clock())
    hub.infos = {50 - k: SimpleNamespace(id=50 - k, dx=5.0, dy=0.0, epoch=0, quality=("blurry",) if k == 3 else (),
                                         disc=399.3) for k in range(30)}
    hub.latest_info = hub.infos[50]
    hub.gpu_heavy.where["o1"] = (140, 120)
    asyncio.run(follower.step())
    assert not any(s.points for s in hub.trails.trails.get("o1", SimpleNamespace(segments=[])).segments)


def test_the_brain_is_told_how_far_a_followed_outline_went_only_when_it_went_somewhere():
    from orchestrator.board import Item
    from orchestrator.brain.context import _outlined

    info = SimpleNamespace(width=1000, height=1000)

    def rows(moved):
        item = Item("o1", "highlight", "sperm cell", (100, 100, 120, 120), 0.0004, "likely", "click", 0.8, True, None, 0.0,
                    moved=moved)
        state = SimpleNamespace(outlines=[item], selected=None, magnifier={"object": None}, evidence=())
        return _outlined(state, info, True)

    assert "moved on its own: 15 µm over the slide in 10 s" in rows("15 µm over the slide in 10 s")[0]
    assert "moved on its own" not in rows(None)[0]


def test_a_small_correction_while_the_slide_travels_is_not_motion_but_a_slow_swimmer_on_a_still_slide_is():
    """Review of #155, re-check: the follower correcting the tracker's drift (9 px, during a slow pan) is not
    the object moving; on a still slide the same small moves are a slow swimmer's own."""
    follower, hub = make()
    hub.trails = Trails(Clock())
    for dx, where in ((30.0, (139, 100)), (60.0, (178, 100))):  # the slide travels 30 px a step; 9 px off each time
        hub.trails.clock.t += 0.1
        hub.latest_info.dx = dx
        hub.gpu_heavy.where["o1"] = where
        asyncio.run(follower.step())
    trail = hub.trails.trails["o1"]
    assert hub.scene.objects["o1"].anchor_offset == (60.0, 0.0)  # the follower did place it
    assert trail.path_px() == 0 and hub.trails.words("o1", None) is None
    follower, hub = make()  # a still slide (5 px from its anchor): 9 px a step is its own motion
    hub.trails = Trails(Clock())
    for where in ((114, 100), (123, 100)):
        hub.trails.clock.t += 0.1
        hub.gpu_heavy.where["o1"] = where
        asyncio.run(follower.step())
    assert hub.trails.trails["o1"].path_px() == 9


def test_following_switched_off_ends_the_trails_and_the_reach_is_the_hubs_largest_correction():
    from orchestrator import hub as hub_module, objtrack
    from orchestrator.trail import REACH_PX

    assert REACH_PX == hub_module.MAX_DRIFT_CORRECTION_PX == objtrack.MAX_DRIFT_CORRECTION_PX
    follower, hub = follow([(140, 100), (160, 100)])
    hub.settings.track = "slide"
    follower.suspend()  # what _loop does when TRACK is no longer "objects"
    assert hub.trails.trails["o1"].segments[-1].end == "stopped" and not follower.followed


def test_a_correction_never_moves_the_places_measured_before_it():
    """Review of #155, second re-check: a swimmer's real path (100 to 140 on a still slide), then the
    follower's small corrections during a pan. The path before stays where it was measured, and no
    path is counted across a correction."""
    follower, hub = make()
    hub.trails = Trails(Clock())
    hub.latest_info.dx = 0.0
    for where in ((110, 100), (120, 100), (130, 100), (140, 100)):  # it swims 10 px a step on a still slide
        hub.trails.clock.t += 0.1
        hub.gpu_heavy.where["o1"] = where
        asyncio.run(follower.step())
    first = [p[0] for p in hub.trails.trails["o1"].segments[0].points]
    x = 140.0
    for k in range(1, 6):  # the slide pans 30 px a step; the follower moves it 9 px each time
        hub.trails.clock.t += 0.1
        hub.latest_info.dx = 30.0 * k
        x += 9.0
        hub.gpu_heavy.where["o1"] = (x + 30.0 * k, 100)
        asyncio.run(follower.step())
    trail = hub.trails.trails["o1"]
    assert [p[0] for p in trail.segments[0].points] == first == [110.0, 120.0, 130.0, 140.0]  # never moved
    assert trail.segments[0].end == "corrected" and trail.path_px() == 30
    assert trail.breaks() == (0, 0)  # a correction is not a gap


def test_a_move_just_past_the_largest_correction_is_motion_even_while_the_slide_travels():
    follower, hub = make()
    hub.trails = Trails(Clock())
    for dx, where in ((30.0, (141, 100)), (60.0, (182, 100))):  # 11 px off each time: more than a correction
        hub.trails.clock.t += 0.1
        hub.latest_info.dx = dx
        hub.gpu_heavy.where["o1"] = where
        asyncio.run(follower.step())
    assert hub.trails.trails["o1"].path_px() == 11


def test_the_loop_suspends_following_when_it_is_switched_off_and_stale_breaks_are_counted():
    follower, hub = follow([(140, 100), (160, 100)])
    hub.settings.track = "slide"

    async def run_once():
        task = asyncio.create_task(follower._loop())
        await asyncio.sleep(0.25)
        task.cancel()

    asyncio.run(run_once())
    assert hub.trails.trails["o1"].segments[-1].end == "stopped" and not follower.followed
    clock = Clock()
    trails = Trails(clock)
    trails.observe(outline(100, 100), "seen", 0, wrote=True)
    clock.t += STALE_S + 1
    trails.observe(outline(120, 100), "seen", 0, wrote=True)
    assert trails.stale_breaks == 1


def test_a_long_slow_pan_of_corrections_never_pushes_out_what_was_measured():
    """Review of #155, third re-check: 45 corrections (a slow pan) after a real swim. The measured segment
    and its words stay; the corrections are one moving place, said as left out."""
    clock = Clock()
    trails = Trails(clock)
    for x in (100, 110, 120, 130, 140):  # its own motion, written
        clock.t += 0.2
        trails.observe(outline(x, 100), "seen", 0, wrote=True)
    x = 140.0
    for _ in range(45):  # the slide pans; each step the outline is corrected 5 px (not its motion)
        clock.t += 0.2
        x += 5.0
        trails.observe(outline(x, 100), "seen", 0)
    trail = trails.trails["o1"]
    assert [p[0] for p in trail.segments[0].points] == [100, 110, 120, 130, 140] and len(trail.segments) == 2
    assert trail.segments[1].points[0][0] == x and trail.path_px() == 40
    assert trails.words("o1", None) == "40 px (no scale yet) over the slide in 10 s (45 re-registrations of its outline left out)"


def test_a_place_that_did_not_move_is_kept_in_the_same_segment():
    """The 1 px step: a still outline seen again where it was is not a correction (no new segment)."""
    clock = Clock()
    trails = Trails(clock)
    for _ in range(10):
        clock.t += 0.6
        trails.observe(outline(100, 100), "seen", 0)
    assert len(trails.trails["o1"].segments) == 1 and trails.trails["o1"].corrections == []


def test_a_corrections_jump_is_not_reach():
    """Reach takes out what corrections moved, as path does: a back-and-forth within 4 px plus corrections
    of 15 px says nothing."""
    clock = Clock()
    trails = Trails(clock)
    x = 100.0
    for k in range(8):
        clock.t += 0.6
        trails.observe(outline(x + (4 if k % 2 else 0), 100), "seen", 0, wrote=True)
        if k in (2, 5):
            clock.t += 0.1
            x += 15.0
            trails.observe(outline(x + (4 if k % 2 else 0), 100), "seen", 0)  # corrected 15 px
    trail = trails.trails["o1"]
    assert trail.path_px() >= SHOWN_PX and trail.reach_px() < REACH_PX and trails.words("o1", None) is None



def test_corrections_far_apart_with_the_same_place_kept_between_never_pile_up_segments():
    """Review of #155, fourth re-check: corrections 0.5-1.5 s apart, the same place kept again between them
    (after KEEP_EVERY_S): still one moving place, so the measured segment and its words stay."""
    clock = Clock()
    trails = Trails(clock)
    for x in (100, 110, 120, 130):
        clock.t += 0.2
        trails.observe(outline(x, 100), "seen", 0, wrote=True)
    x = 130.0
    for _ in range(60):  # a correction every 1.2 s, the same place seen again in between (kept after 0.5 s)
        clock.t += 0.6
        trails.observe(outline(x, 100), "seen", 0)
        clock.t += 0.6
        x += 3.0
        trails.observe(outline(x, 100), "seen", 0)
    trail = trails.trails["o1"]
    # 72 s later the measured places have aged out of the window; until then they were never pushed out.
    assert len(trail.segments) <= 2


def test_the_measured_segment_survives_corrections_far_apart_within_the_window():
    clock = Clock()
    trails = Trails(clock)
    for x in (100, 110, 120, 130):
        clock.t += 0.2
        trails.observe(outline(x, 100), "seen", 0, wrote=True)
    x = 130.0
    for _ in range(40):  # 40 corrections over 48 s, each 1.2 s apart
        clock.t += 0.6
        trails.observe(outline(x, 100), "seen", 0)
        clock.t += 0.6
        x += 3.0
        trails.observe(outline(x, 100), "seen", 0)
    trail = trails.trails["o1"]
    assert [p[0] for p in trail.segments[0].points][:4] == [100, 110, 120, 130] and trail.path_px() == 30
    assert len(trail.segments) == 2 and trails.words("o1", None).endswith("(40 re-registrations of its outline left out)")


def test_corrections_older_than_the_window_are_not_counted_and_rounding_says_known_time():
    clock = Clock()
    trails = Trails(clock)
    trails.observe(outline(100, 100), "seen", 0, wrote=True)
    clock.t += 0.2
    trails.observe(outline(105, 100), "seen", 0)  # a correction
    clock.t += WINDOW_S + 1
    trails.words("o1", None)  # read: the window is held
    assert trails.trails["o1"].corrections == []
    # Known time rounding to the whole span: "its place known about N s", never "the 1 s of the last 1 s".
    trails2 = Trails(clock)
    for x in (100, 110, 120):
        clock.t += 0.3
        trails2.observe(outline(x, 100), "seen", 0, wrote=True)
    clock.t += 0.1
    trails2.observe(outline(120, 100), "missed", 0)
    assert "its motion measured over about 1 s (1 gap)" in trails2.words("o1", None)


def test_the_hub_says_how_many_trail_segments_ended_unobserved():
    from orchestrator.hub import Hub

    hub = Hub("http://127.0.0.1:9", use_gpu=False)
    hub.follower = SimpleNamespace(steps=3, last_step_at=0.0, followed={})
    hub.trails.stale_breaks = 2
    assert hub._follow_wire()["trailStaleBreaks"] == 2



def test_its_own_motion_after_a_correction_is_never_taken_for_a_correction():
    """Review of #155, fourth re-check SF3: after a correction, the object moves on its own (out 5 px and back);
    the next correction must end that segment, never collapse it (judged on every point, from 1 px)."""
    clock = Clock()
    trails = Trails(clock)
    trails.observe(outline(100, 100), "seen", 0, wrote=True)
    clock.t += 0.2
    trails.observe(outline(110, 100), "seen", 0)  # a correction: a new place at 110
    for x in (115, 110):  # its own motion: out 5 px and back
        clock.t += 0.2
        trails.observe(outline(x, 100), "seen", 0, wrote=True)
    clock.t += 0.2
    trails.observe(outline(125, 100), "seen", 0)  # another correction
    trail = trails.trails["o1"]
    assert trail.path_px() == 10  # the 5 px out and back are kept: never collapsed away
    assert [s.end for s in trail.segments] == ["corrected", "corrected", "open"]


def test_corrections_before_what_its_words_cover_are_not_counted():
    """Review of #155, fourth re-check SF1: corrections collapsed into one place (its time moving on), then its
    own motion; the words count only the corrections from its first kept place on."""
    clock = Clock()
    trails = Trails(clock)
    trails.observe(outline(100, 100), "seen", 0, wrote=True)
    clock.t += 0.2
    trails.observe(outline(103, 100), "seen", 0)  # the first correction: a new one-place segment
    for k in range(20):  # 20 more, each collapsing that place (its time moves on)
        clock.t += 0.2
        trails.observe(outline(106 + 3 * k, 100), "seen", 0)
    trail = trails.trails["o1"]
    trail.segments = trail.segments[1:]  # as when the earlier segment aged out of the window
    for x in (170, 180, 190, 200):
        clock.t += 0.2
        trails.observe(outline(x, 100), "seen", 0, wrote=True)
    assert trails.words("o1", None).endswith("(1 re-registration of its outline left out)")
