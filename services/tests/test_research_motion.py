"""A researcher's hands measure motion (the partner's P2b-2; the going-live note 3: "My tools don't include running a
motion model", while the app had it): M3.2's "motion" operation on the live view's last seconds, the slide's own
motion taken out, kept as a tracks item made for the researcher. Never shown, in the "research" allowance, its own
job (never sharing the user's), never "the motion" a request of the user's means, and reached by its id by anyone."""
from __future__ import annotations

import asyncio

from orchestrator.capture import Captures
from orchestrator.motiontracks import lines, track_layer_of, tracks_of
from orchestrator.research.hands import CLIP_FPS_TOO_LOW, Hands
from tests.test_motion_tracks import MotionHub, moving_slide, tool, wait_saved
from tests.test_population import world  # noqa: F401  (the fixture)
from tests.test_research_samples import DAY, clip_of, session


def researching_hub():
    hub = MotionHub(moving_slide(), disc=False, base=(500.0, -300.0))  # every dot inside the measured view
    return hub


def test_a_researcher_measures_what_moves_for_itself_never_shown_never_the_users_motion(monkeypatch):
    from orchestrator import capture as capture_module
    from orchestrator.resources import Index
    from orchestrator.screen import resolve

    held, reserved = [], []
    real_hold, real_reserving = Captures.hold, capture_module.reserving
    monkeypatch.setattr(Captures, "hold", lambda self, c, cls, kind, owner: (held.append(cls), real_hold(self, c, cls, kind, owner))[1])
    monkeypatch.setattr(capture_module, "reserving",
                        lambda hub, w, h, kind="look", owner="", cls="analysis": (reserved.append(cls),
                                                                                 real_reserving(hub, w, h, kind, owner, cls))[1])
    hub = researching_hub()
    s = session(day=DAY)
    s.origin = {"thread": "t2"}
    from orchestrator.operations import runner

    asked, real_run = [], runner(hub).run
    runner(hub).run = lambda *a, **k: (asked.append(k.get("params") or {}), real_run(*a, **k))[1]
    reply = asyncio.run(Hands(hub).run(s, "motion", "how fast they swim", seconds=4))
    assert reply["ok"], reply
    assert [p.get("draw") for p in asked] == [False]  # never shown, so no picture is drawn and held for it
    item = tracks_of(hub).get(reply["id"])
    assert item.made_for == "Researcher 2" and item.title.startswith("Motion, for Researcher 2 · ") and item.thread == "t2"
    assert "the slide's own motion taken out" in reply["words"] and "Followed 9 small things" in reply["words"]
    assert held == ["research"] and reserved == ["research"]  # its own allowance, never the user's work's
    assert s.ran[-1]["kind"] == "tracks" and s.ran[-1]["id"] == item.id and s.ran[-1]["seconds"] == 4.0
    # Never shown, never "the motion" or the user's board; by its id it stands, for the user and the agent (kind (k)).
    assert track_layer_of(hub).shown is None and not [m for m in hub.sent if "tracks" in str(m.get("type", ""))]
    assert tracks_of(hub).latest() is None and not lines(hub) and not lines(hub, "t2")
    try:
        resolve(hub, "the motion")
        raise AssertionError("a researcher's tracks were taken for the user's motion")
    except ValueError as exc:
        assert "no motion has been measured here yet" in str(exc)
    assert resolve(hub, item.id) == (f"tracks:{item.id}", "tracks")
    entry = Index(hub).get(item.id)
    assert entry is not None and entry.made_by.get("for") == "Researcher 2" and entry.title == item.title
    assert wait_saved(item) in ("saved", "unsaved")


def test_a_researchers_motion_is_its_own_job_never_the_users_shared():
    """The scaffolding's condition 2: the research class is in the job's key, so a researcher's measurement never
    joins (or holds back) the user's on the very same frames at the same moment, and the user's is never handed to
    it as its own: two jobs, two Executions, two items."""
    from orchestrator.motiontracks import keep_measured
    from orchestrator.operations import runner

    hub = researching_hub()
    clip_ = [(hub.source.get(i), hub.infos[i]) for i in hub.infos]

    async def both():
        return await asyncio.gather(keep_measured(hub, clip_, None, "", 4.0),
                                    keep_measured(hub, clip_, None, "", 4.0, draw=False, origin="background",
                                                  made_for="Researcher 2", thread="t2"))

    (users, reused_u, done_u), (theirs, reused_r, done_r) = asyncio.run(both())
    assert users is not theirs and not reused_u and not reused_r and done_u.execution is not done_r.execution
    assert sorted(e.origin for e in runner(hub).executions.values() if e.operation == "motion") == ["background", "brain-default"]
    # The execution says whom it was for (#197 review SF1), as a researcher's segment does (population.py).
    assert sorted((e.made_for or "") for e in runner(hub).executions.values() if e.operation == "motion") == ["", "Researcher 2"]
    assert [e.wire().get("madeFor") for e in runner(hub).executions.values() if e.origin == "background"] == ["Researcher 2"]
    assert theirs.made_for == "Researcher 2" and users.made_for is None and tracks_of(hub).latest() is users


def test_motion_is_refused_on_a_held_clip_or_one_view_and_when_the_slide_kept_moving(world):
    """The scaffolding's condition 1: a held clip has a frame every 0.5 s, and the motion operation follows spots frame
    to frame at the live view's rate; at 2 a second a moving spot is lost or taken for its neighbour, so it is not
    measured there (never tracks from 2 fps said as if at 30). One view shows nothing moving."""
    from tests.test_population import cells

    infos = [world.frame(1.0 * i, 0.0) for i in range(5)]
    for info in infos:
        info.steady = True
    clip_of(world, infos)
    hands = Hands(world)
    clip = asyncio.run(hands.run(session(day=DAY), "motion", "x", input="k1"))
    assert not clip["ok"] and CLIP_FPS_TOO_LOW in clip["error"] and "live view now" in clip["next"]
    population, _ = cells(world)
    one = asyncio.run(hands.run(session(day=population.day), "motion", "x", input=population.id))
    assert not one["ok"] and "one frame" in one["error"]
    few = MotionHub(moving_slide(seconds=0.4), disc=False)  # under a second and a half of the slide still
    moved = asyncio.run(Hands(few).run(session(day=DAY), "motion", "x", seconds=1.5))
    assert not moved["ok"] and "so nothing could be followed" in moved["error"] and not session(day=DAY).ran


def test_a_researchers_watch_is_held_to_what_the_tool_allows(monkeypatch):
    """#197 review SF3: the seconds a researcher asks for are the tool's own bounds (1.5 s to LONGEST_S); 500 s is
    never watched (gather runs to seconds + 3), a negative or unreadable number is the bounds or the default."""
    from orchestrator.brain.toolbox import motion
    from tests.test_research_samples import session

    watched = []

    async def gather(hub, seconds):
        watched.append(seconds)
        return []

    monkeypatch.setattr(motion, "gather", gather)
    hands = Hands(researching_hub())
    for asked in (500, -3, "soon", None):
        reply = asyncio.run(hands.run(session(day=DAY), "motion", "x", seconds=asked))
        assert not reply["ok"] and "so nothing could be followed" in reply["error"]
    assert watched == [motion.LONGEST_S, 1.5, motion.DEFAULT_S, motion.DEFAULT_S]
