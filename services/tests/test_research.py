import asyncio
import json
import os
import time
from types import SimpleNamespace

import httpx
import pytest

from orchestrator.brain.registry import Registry, ToolContext
from orchestrator.research.policy import explicit_refusal
from tests.fakes import research_tools
from orchestrator.research import ResearchManager
from orchestrator.research.files import research_file
from research_worker import safe_fetch
from research_worker.budget import Budget, Effort, OverBudget
from research_worker.engines import FakeEngine
from research_worker.researcher import Researcher
from tests.fakes import Hub as BaseHub
from tests.waiting import until_async


def FakeHub():
    """A hub for researchers: no frame, scripted researchers' engine (tests/fakes.Hub)."""
    return BaseHub(frame=False, specimen="leaf", research="fake")


@pytest.fixture
def fake_engine(monkeypatch):
    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "fake")


async def until(predicate):
    return await until_async(predicate, every=0.05)


def test_researcher_spawns_finishes_and_saves(tmp_path, fake_engine):
    async def go():
        manager = ResearchManager(FakeHub(), root=tmp_path)
        done = []
        manager.on_done.append(done.append)
        session = await manager.start("what is a stoma", effort="quick", fake={"seconds": 0.4, "finding": "stomata let gas in"})
        assert session.status in ("queued", "starting") and session.n == 1
        assert await until(lambda: done)
        manager.stop()
        return session

    session = asyncio.run(go())
    assert session.status == "done" and session.result["summary"].startswith("Simulated research")
    saved = json.loads((tmp_path / session.day / session.rid / "session.json").read_text())
    assert saved["status"] == "done" and saved["spent"] > 0
    ledger = [json.loads(line) for line in (tmp_path / "findings.jsonl").read_text().splitlines()]
    assert {e["kind"] for e in ledger} == {"fact", "result"}
    assert (tmp_path / session.day / session.rid / "steps.jsonl").exists()


def test_finished_researchers_come_back_after_a_restart(tmp_path, fake_engine):
    async def go():
        manager = ResearchManager(FakeHub(), root=tmp_path)
        done = []
        manager.on_done.append(done.append)
        session = await manager.start("teach me about stomata", effort="quick", fake={"seconds": 0.3})
        assert await until(lambda: done)
        manager.stop()
        spent = manager.spent_today()
        from tests.test_subject import as_real

        as_real(tmp_path)
        again = ResearchManager(FakeHub(), root=tmp_path)  # the orchestrator restarted
        return session, spent, again

    session, spent, again = asyncio.run(go())
    back = again.get(session.rid)
    assert back is not None and back.status == "done" and back.result == session.result and back.restored
    assert back.wire()["outputs"] == session.wire()["outputs"]
    assert abs(again.spent_today() - spent) < 1e-9  # counted once
    assert again.lines() and "Researcher 1" in again.lines()
    assert again._next_number(again.session()) == 2  # the next researcher is Researcher 2


def test_pools_queue_beyond_three_big_researchers(tmp_path, fake_engine):
    async def go():
        manager = ResearchManager(FakeHub(), root=tmp_path)
        sessions = [await manager.start(f"job {i}", effort="quick", fake={"seconds": 1.5}) for i in range(5)]
        await until(lambda: sum(s.status == "running" for s in sessions) == 3)
        running = [s.status for s in sessions]
        await until(lambda: all(s.status == "done" for s in sessions))
        final = [s.status for s in sessions]
        manager.stop()
        return running, final

    running, final = asyncio.run(go())
    assert running.count("running") == 3 and running.count("queued") == 2
    assert final == ["done"] * 5


def test_cancel_and_crash(tmp_path, fake_engine):
    async def go():
        manager = ResearchManager(FakeHub(), root=tmp_path)
        a = await manager.start("long one", effort="quick", fake={"seconds": 30})
        b = await manager.start("another", effort="quick", fake={"seconds": 30})
        await until(lambda: a.status == "running" and b.status == "running")
        await manager.cancel(a.rid)
        await until(lambda: a.status == "cancelled")
        manager._worker._process.kill()  # the worker dies
        await until(lambda: b.status == "interrupted")
        manager.stop()
        return a.status, b.status

    assert asyncio.run(go()) == ("cancelled", "interrupted")


def test_daily_cap_refuses_new_researchers(tmp_path):
    hub = FakeHub()
    hub.settings.research_cap = 0.0
    manager = ResearchManager(hub, root=tmp_path)
    from orchestrator.research import ResearchRefused

    with pytest.raises(ResearchRefused):
        asyncio.run(manager.start("anything", effort="instant"))


def test_starting_a_slow_researcher_returns_at_once(tmp_path, fake_engine, wall_clock):
    """Live never waits on deep work: a 20 s researcher starts in well under a second."""
    async def go():
        hub = FakeHub()
        manager = ResearchManager(hub, root=tmp_path)
        registry = research_tools(hub, manager)
        ctx = ToolContext("voice", 1, None, "look into how stomata open", "deleg-1")
        t0 = time.perf_counter()
        run = registry.start("start_researcher", {"brief": "how stomata open", "effort": "quick",
                                                  "asked_with": "look into how stomata open"}, ctx)
        await run.task
        took = time.perf_counter() - t0
        session = next(iter(manager.sessions.values()))
        manager.stop()
        return took, run, session

    took, run, session = asyncio.run(go())
    assert run.ok and "Researcher 1" in run.summary
    wall_clock.within(took, 0.5, "starting it")
    assert session.origin == {"channel": "voice", "delegation_id": "deleg-1", "turn_id": 1, "asked": True, "thread": None,
                              'request_id': run.occurrence.request_id, 'action_id': run.occurrence.action_id}


def test_a_researchers_wire_names_the_turn_and_request_that_started_it(tmp_path, fake_engine):
    """The chat matches a researcher's card to the turn that asked for it: its wire carries the turn and
    request of the start_researcher call that started it. A voice start (turn 0) carries a null turn and keeps its
    request; one started with no origin carries null for both."""
    async def go():
        hub = FakeHub()
        manager = ResearchManager(hub, root=tmp_path)
        registry = research_tools(hub, manager)
        ctx = ToolContext("chat", 7, None, "look into how stomata open", "deleg-7")
        run = registry.start("start_researcher", {"brief": "how stomata open", "effort": "quick",
                                                  "asked_with": "look into how stomata open"}, ctx)
        await run.task
        started = next(iter(manager.sessions.values()))
        # A voice (or manual) start has no user turn: its context's turn is 0, and only its request names it.
        spoken = registry.start("start_researcher", {"brief": "how guard cells swell", "effort": "quick",
                                                     "asked_with": "look into how guard cells swell"},
                                ToolContext("voice", 0, None, "look into how guard cells swell", "deleg-0"))
        await spoken.task
        voiced = next(s for s in manager.sessions.values() if s is not started)
        bare = await manager.start("a look nobody asked for", effort="quick", fake={"seconds": 0.2})
        wires = started.wire(), voiced.wire(), bare.wire()
        manager.stop()
        return run, spoken, wires

    run, spoken, (started, voiced, bare) = asyncio.run(go())
    assert run.ok and run.occurrence is not None and run.occurrence.request_id
    assert (started["turnId"], started["requestId"]) == (7, run.occurrence.request_id)
    assert spoken.ok and spoken.occurrence is not None and spoken.occurrence.request_id
    assert (voiced["turnId"], voiced["requestId"]) == (None, spoken.occurrence.request_id)
    assert (bare["turnId"], bare["requestId"]) == (None, None)


def test_big_deliverables_need_an_explicit_request():
    assert explicit_refusal("what's this?", "deep", None)
    assert explicit_refusal("do a thorough deep dive on stomata", "deep", None, "a thorough deep dive on stomata") is None
    assert explicit_refusal("tell me about it", "quick", "video", "tell me about it as a video")
    assert explicit_refusal("make me a short video of how it opens", "quick", "video", "make me a short video") is None
    assert explicit_refusal("look into it", "quick", "research") is None
    # The keyword rule (option ADMIT "keywords"), as before.
    assert explicit_refusal("tell me about it", "quick", "video", mode="keywords")
    assert explicit_refusal("make me a short video of how it opens", "quick", "video", mode="keywords") is None


def run_in_process(tmp_path, effort: Effort, script: dict):
    events = []
    spec = {"rid": "r1", "dir": str(tmp_path / "r1"), "effort": "quick", "brief": "x", "fake": script}
    researcher = Researcher(spec, events.append, None)
    researcher.effort = effort
    researcher.budget = Budget(effort)
    asyncio.run(researcher.run(FakeEngine()))
    return events


def test_the_clock_never_cuts_off_a_run(tmp_path):
    """An effort's seconds are where a researcher is told to write up, never a cut-off (the researcher repair,
    2026-10-11: a quick run's write-up was killed at 75 s with five measurements in hand)."""
    events = run_in_process(tmp_path, Effort("t", 0.3, 1.0, "big", frozenset({"finish"}), 3, "low"), {"seconds": 1})
    assert events[-1]["event"] == "done" and events[-1]["status"] == "done"


def test_budget_is_enforced():
    budget = Budget(Effort("t", 10, 0.05, "big", frozenset(), 3, "low"))
    budget.add("call", 0.04)
    budget.check(0.005)
    with pytest.raises(OverBudget):
        budget.check(0.02)


def test_uncited_reference_pictures_are_deleted(tmp_path):
    researcher = Researcher({"rid": "r1", "dir": str(tmp_path / "r1"), "effort": "quick", "brief": "x"}, lambda e: None, None)
    for name in ("a", "b"):
        (tmp_path / "r1" / "sources" / f"{name}.jpg").write_bytes(b"x")
        researcher.refs[f"http://x/{name}"] = {"path": f"sources/{name}.jpg"}
    asyncio.run(researcher.call_tool("finish", {"title": "t", "summary": "s", "one_liner": "o", "significance": "low",
                                                "refs": ["http://x/b"]}))
    researcher.tidy()
    assert not (tmp_path / "r1/sources/a.jpg").exists() and (tmp_path / "r1/sources/b.jpg").exists()


# --- safe_fetch --------------------------------------------------------------------------

def test_safe_fetch_refuses_local_and_odd_urls():
    for url in ("http://127.0.0.1/x.jpg", "http://localhost/x.jpg", "file:///etc/passwd", "http://10.0.0.1/a.png",
                "http://169.254.169.254/latest", "ftp://example.com/a.jpg", "http://user:pw@example.com/a.jpg"):
        with pytest.raises(safe_fetch.FetchRefused):
            safe_fetch.check_url(url)


def _png() -> bytes:
    import cv2
    import numpy as np

    return cv2.imencode(".png", np.full((20, 30, 3), 128, np.uint8))[1].tobytes()


def fetch_with(handler, url, monkeypatch):
    monkeypatch.setattr(safe_fetch, "_public", lambda host: None if host != "localhost" else (_ for _ in ()).throw(
        safe_fetch.FetchRefused("local")))
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            return await safe_fetch.fetch_image(url, client)
    return asyncio.run(go())


def test_safe_fetch_rechecks_redirects(monkeypatch):
    def handler(request):
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://localhost/secret.png"})
        return httpx.Response(200, content=_png(), headers={"content-type": "image/png"})

    with pytest.raises(safe_fetch.FetchRefused):
        fetch_with(handler, "http://example.com/a.png", monkeypatch)


def test_safe_fetch_reencodes_images_and_refuses_the_rest(monkeypatch):
    image = fetch_with(lambda r: httpx.Response(200, content=_png(), headers={"content-type": "image/png"}),
                       "http://example.com/a.png", monkeypatch)
    assert image.jpeg[:2] == b"\xff\xd8" and (image.width, image.height) == (30, 20)
    with pytest.raises(safe_fetch.FetchRefused):
        fetch_with(lambda r: httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}),
                   "http://example.com/a.png", monkeypatch)
    big = b"\0" * (safe_fetch.MAX_BYTES + 10)
    with pytest.raises(safe_fetch.FetchRefused):
        fetch_with(lambda r: httpx.Response(200, content=big, headers={"content-type": "image/png"}),
                   "http://example.com/a.png", monkeypatch)


# --- serving -----------------------------------------------------------------------------

def test_research_files_are_served_defensively(tmp_path):
    out = tmp_path / "2026-10-03" / "r1" / "outputs"
    out.mkdir(parents=True)
    (out / "01-a.png").write_bytes(b"png")
    (out / "notes.txt").write_text("x")
    (out / "lesson.html").write_text("<p>hi</p>")
    assert research_file(tmp_path, "2026-10-03", "r1", "outputs", "01-a.png")[1] == "image/png"
    assert research_file(tmp_path, "2026-10-03", "r1", "outputs", "notes.txt") is None
    assert research_file(tmp_path, "2026-10-03", "r1", "..", "01-a.png") is None
    assert research_file(tmp_path, "../etc", "r1", "outputs", "01-a.png") is None
    assert research_file(tmp_path, "2026-10-03", "r1", "outputs", "..%2F01-a.png") is None

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from orchestrator.research.files import add_research_routes

    app = FastAPI()
    manager = ResearchManager(FakeHub(), root=tmp_path)
    add_research_routes(app, manager)
    client = TestClient(app)
    page = client.get("/research/2026-10-03/r1/outputs/lesson.html")
    assert page.status_code == 200 and "sandbox" in page.headers["content-security-policy"]
    assert "img-src data:" in page.headers["content-security-policy"] and "connect-src" not in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"
    assert 'micromind-artifact-keyboard-init' in page.text
    assert 'event.isTrusted' in page.text
    assert (out / "lesson.html").read_text() == "<p>hi</p>"  # response-only accessibility script
    assert page.headers['cache-control'] == 'no-store'
    assert client.get("/research/2026-10-03/r1/outputs/notes.txt").status_code == 404
    assert client.get("/api/research").json()["sessions"] == []


# --- annotation fidelity -----------------------------------------------------------------

def test_photo_is_padded_not_stretched_and_redrawing_is_caught():
    import cv2
    import numpy as np

    from research_worker.fidelity import ALTERED_ABOVE, alteration, square_pad

    rng = np.random.default_rng(1)
    photo = cv2.GaussianBlur((rng.random((300, 400, 3)) * 120 + 60).astype(np.uint8), (0, 0), 3)
    padded, (x, y, w, h) = square_pad(cv2.imencode(".jpg", photo)[1].tobytes(), side=512)
    assert (w, h) == (512, 384) and (x, y) == (0, 64)  # 4:3 kept, centred
    base = cv2.imdecode(np.frombuffer(padded, np.uint8), cv2.IMREAD_COLOR)

    labelled = base.copy()
    cv2.arrowedLine(labelled, (40, 100), (200, 250), (255, 255, 255), 2)
    cv2.putText(labelled, "guard cell", (30, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    assert alteration(padded, cv2.imencode(".png", labelled)[1].tobytes(), (x, y, w, h)) <= ALTERED_ABOVE

    redrawn = base.copy()
    cv2.ellipse(redrawn, (256, 256), (60, 90), 0, 0, 360, (40, 90, 40), -1)  # an invented filled shape
    assert alteration(padded, cv2.imencode(".png", redrawn)[1].tobytes(), (x, y, w, h)) > ALTERED_ABOVE


# --- corrections and follow-ups ----------------------------------------------------------

def test_correction_is_only_applied_when_accepted(tmp_path):
    from orchestrator.scene import Scene, SceneObject
    from tests.fakes import announcer

    hub = FakeHub()
    hub.scene = Scene()
    for oid in ("o1", "o2"):
        hub.scene.add(SceneObject(id=oid, kind="highlight", polygons=[[[0, 0], [5, 0], [5, 5]]], anchor_frame=1, anchor_epoch=0,
                                  anchor_offset=(0.0, 0.0), source="ai", label="stoma", status="likely", confidence=0.6))
    manager = ResearchManager(hub, root=tmp_path)
    heard = []
    manager.on_correction.append(lambda s, c: heard.append(c["to_label"]))
    session = manager.brain_session()
    c1 = manager.propose(session, "o1", "trichome base", "no pore visible", 0.7)
    c2 = manager.propose(session, None, "air bubble", "perfectly round", 0.5)
    assert heard == ["trichome base", "air bubble"] and hub.scene.objects["o1"].label == "stoma"  # nothing changed yet
    decide = announcer(tmp_path, manager, scene=hub.scene).announcer.decided
    assert decide(c1["id"], True)["relabelled"] == ["o1"]
    assert hub.scene.objects["o1"].label == "trichome base" and hub.scene.objects["o1"].revised_by == session.name
    assert hub.scene.objects["o1"].status == "confirmed"  # the user agreed (W1-3)
    assert hub.scene.objects["o2"].label == "stoma"
    assert decide(c1["id"], True)["ok"] is False  # decided once
    assert decide(c2["id"], False)["ok"] and hub.scene.objects["o2"].label == "stoma"
    assert [c["status"] for _, c in manager.corrections(None)] == ["accepted", "dismissed"]


def test_followup_continues_the_same_researcher(tmp_path, fake_engine):
    async def go():
        manager = ResearchManager(FakeHub(), root=tmp_path)
        session = await manager.start("first question", effort="quick", fake={"seconds": 0.2})
        await until(lambda: session.status == "done")
        await manager.followup(session.rid, "and a second one?", mode="continue")
        assert session.status in ("queued", "starting")
        await until(lambda: session.status == "done")
        manager.stop()
        return session

    session = asyncio.run(go())
    assert len(session.history) == 1 and session.history[0]["brief"] == "first question"
    # It is still named by what it was asked first; the continue's question is its newest.
    assert session.brief == "first question" and "second one" in session.asking and session.result is not None


def test_followup_outputs_do_not_overwrite_earlier_ones(tmp_path):
    (tmp_path / "r1" / "outputs").mkdir(parents=True)
    for name in ("01-annotated.png", "02-video.mp4"):
        (tmp_path / "r1" / "outputs" / name).write_bytes(b"x")
    researcher = Researcher({"rid": "r1", "dir": str(tmp_path / "r1"), "effort": "quick", "brief": "x"}, lambda e: None, None)
    assert researcher._save_output("annotated", b"png") == "outputs/03-annotated.png"


def test_a_new_session_starts_at_researcher_1_while_the_last_one_works_on(tmp_path, fake_engine):
    """Nothing is archived any more (ADR 0017): a new session is never refused while a researcher works. The one
    working finishes in its own session, numbers start at 1 in the new one, and what both spent counts against
    today's cap, also after a restart."""
    async def go():
        open_ = {"sid": time.strftime("%Y-%m-%d")}
        manager = ResearchManager(FakeHub(), root=tmp_path, session=lambda: open_["sid"])
        done = []
        manager.on_done.append(done.append)
        await manager.start("first look", effort="quick", fake={"seconds": 0.2})
        assert await until(lambda: done)
        spent = manager.spent_today()
        slow = await manager.start("still working", effort="quick", fake={"seconds": 1.0})
        open_["sid"] += ".2"  # a new session, with it still working
        manager.session_event({"type": "session-opened", "session": open_["sid"]})
        fresh = await manager.start("after the switch", effort="quick", fake={"seconds": 0.2})
        working = manager.list_wire()["sessions"]
        assert await until(lambda: len(done) == 3)
        manager.stop()
        return manager, spent, slow, fresh, working

    manager, spent, slow, fresh, working = asyncio.run(go())
    assert slow.n == 2 and slow.status == "done" and fresh.n == 1 and fresh.sitting != slow.sitting
    assert slow.rid in {s["rid"] for s in working}  # still listed while it works ("Still working in other sessions")
    assert [s.rid for s in manager.researchers()] == [fresh.rid]
    assert any((tmp_path / slow.day).glob("*/session.json"))  # kept where it was, never moved
    assert manager.spent_today() >= spent + slow.spent  # what was spent still counts against today's cap
    assert spent > 0 and ResearchManager(FakeHub(), root=tmp_path).spent_today() >= spent  # and after a restart


def test_a_note_during_a_revise_is_refused_not_dropped(tmp_path, fake_engine):
    from orchestrator.research import ResearchRefused

    async def go():
        manager = ResearchManager(FakeHub(), root=tmp_path)
        session = await manager.start("teach me about stomata", effort="quick", fake={"seconds": 5})
        from orchestrator.research.run import Run

        session.run = Run("revise", "done", "revise", "make question 2 easier", kind_name="lesson", target={"id": "o1"})
        try:
            with pytest.raises(ResearchRefused, match="new version"):
                manager.note(session.rid, "keep it short")
        finally:
            await manager.cancel(session.rid)
            manager.stop()

    asyncio.run(go())


def test_research_on_that_is_about_what_was_selected_when_the_user_asked(tmp_path, fake_engine):
    """The executor's re-check of #179, should-fix 1: "research that" with no object named is about what was
    selected when the user asked (C2), not an outline clicked while it is answered."""
    from orchestrator.board import WhenAsked
    from orchestrator.scene import SceneObject

    async def go():
        hub = FakeHub()
        stoma = hub.scene.add(SceneObject(hub.scene.new_id(), "highlight", [[[10, 10], [40, 10], [40, 40]]], 1, 0, (0.0, 0.0),
                                          "ai:test", label="stoma"))
        pore = hub.scene.add(SceneObject(hub.scene.new_id(), "highlight", [[[60, 60], [90, 60], [90, 90]]], 1, 0, (0.0, 0.0),
                                         "ai:test", label="pore"))
        hub.scene.select(pore.id)  # clicked while it is answered
        manager = ResearchManager(hub, root=tmp_path)
        registry = research_tools(hub, manager)
        ctx = ToolContext("chat", 1, None, "research that", when_asked=WhenAsked(1.0, stoma.id, 1.0))
        run = registry.start("start_researcher", {"brief": "what it does", "effort": "quick", "asked_with": "research that"}, ctx)
        await run.task
        session = next(iter(manager.sessions.values()))
        manager.stop()
        return session, stoma

    session, stoma = asyncio.run(go())
    assert session.object_id == stoma.id



def _research_object(tmp_path, setup):
    """What start_researcher with no object named takes, for a scene `setup(hub)` makes; it returns (when_asked,
    pointed, objects)."""
    from orchestrator.scene import SceneObject  # noqa: F401

    async def go():
        hub = FakeHub()
        asked, pointed, objects = setup(hub)
        manager = ResearchManager(hub, root=tmp_path)
        registry = research_tools(hub, manager)
        ctx = ToolContext("chat", 1, None, "research that", objects=list(objects), when_asked=asked)
        ctx.pointed = pointed
        run = registry.start("start_researcher", {"brief": "what it does", "effort": "quick", "asked_with": "research that"}, ctx)
        await run.task
        session = next(iter(manager.sessions.values()))
        manager.stop()
        return session.object_id
    return asyncio.run(go())


def _two(hub, second_source="ai:test"):
    from orchestrator.scene import SceneObject

    stoma = hub.scene.add(SceneObject(hub.scene.new_id(), "highlight", [[[10, 10], [40, 10], [40, 40]]], 1, 0, (0.0, 0.0),
                                      "ai:test", label="stoma", created_at=900.0))
    pore = hub.scene.add(SceneObject(hub.scene.new_id(), "highlight", [[[60, 60], [90, 60], [90, 90]]], 1, 0, (0.0, 0.0),
                                     second_source, label="pore", created_at=998.0))
    return stoma, pore


def test_research_that_is_about_a_click_newer_than_the_old_selection(tmp_path):
    """The #179 re-check's SF2 (PR1): the stoma selected half a minute before, the pore outlined by a click just before
    "research that" (a click outlines without selecting): the researcher is about the pore, as "that" reads it."""
    from orchestrator.board import WhenAsked

    made = {}

    def setup(hub):
        made["stoma"], made["pore"] = _two(hub, "click")
        return WhenAsked(1000.0, made["stoma"].id, 970.0), None, ()

    assert _research_object(tmp_path, setup) == made["pore"].id


def test_research_takes_the_requests_own_steps_before_what_was_pointed_at_when_asked(tmp_path):
    """The #179 re-check's R1 and R3: "select the stoma and research it" is the stoma its own step selected, not the
    pore selected when the user asked; and what the request itself outlined comes first too."""
    from orchestrator.board import WhenAsked

    made = {}

    def own_step(hub):
        made["stoma"], made["pore"] = _two(hub)
        return WhenAsked(1000.0, made["pore"].id, 999.0), made["stoma"].id, ()

    assert _research_object(tmp_path / "r1", own_step) == made["stoma"].id

    def outlined(hub):
        made["stoma"], made["pore"] = _two(hub)
        return WhenAsked(1000.0, made["pore"].id, 999.0), None, (made["stoma"].id,)

    assert _research_object(tmp_path / "r3", outlined) == made["stoma"].id


def test_one_request_starts_one_researcher_however_often_it_is_answered(tmp_path, fake_engine):
    """Live 2026-10-28: "create a researcher that looks at the data on the table ... creates an animation of it" made two
    researchers. One request (its admission's identity) admits one researcher: answered again (its hand-off after hands
    started it, a completion round, a retry or a replay), the same researcher is the answer and no other starts."""
    from orchestrator.brain.registry import Steps

    async def go():
        hub = FakeHub()
        manager = ResearchManager(hub, root=tmp_path)
        registry = research_tools(hub, manager)
        words = "create a researcher that looks at the data on the table"
        ctx = ToolContext("voice", 0, None, words, "d1")
        ctx.step = 1
        first = registry.start("start_researcher", {"brief": words, "effort": "quick", "asked_with": words}, ctx)
        await first.task
        ctx.steps, ctx.step = Steps(), 1  # another answer of the same request
        again = registry.start("start_researcher", {"brief": words + " and creates an animation of it",
                                                    "effort": "quick", "asked_with": words}, ctx)
        await again.task
        sessions = list(manager.sessions.values())
        manager.stop()
        return first, again, sessions

    first, again, sessions = asyncio.run(go())
    assert first.ok and again.ok and len(sessions) == 1
    assert "Researcher 1" in again.summary and "no other" in again.summary
    assert again.outputs and again.outputs[0]["resource"]["researcherId"] == sessions[0].rid


def test_a_request_that_asks_for_two_researchers_gets_two(tmp_path, fake_engine):
    """"Make two researchers, one on the sizes, the other on the shapes": one answer lists two starts, and both start."""
    async def go():
        hub = FakeHub()
        manager = ResearchManager(hub, root=tmp_path)
        registry = research_tools(hub, manager)
        words = "make two researchers, one on the sizes, the other on the shapes"
        ctx = ToolContext("chat", 3, None, words)
        runs = []
        for step, brief in ((1, "the nuclei's sizes"), (2, "the nuclei's shapes")):
            ctx.step = step
            runs.append(registry.start("start_researcher", {"brief": brief, "effort": "quick", "asked_with": words}, ctx))
        for run in runs:
            await run.task
        # a new request is new work: a third starts
        later = ToolContext("chat", 4, None, "and another on their brightness")
        later.step = 1
        third = registry.start("start_researcher", {"brief": "their brightness", "effort": "quick",
                                                    "asked_with": "another on their brightness"}, later)
        await third.task
        sessions = list(manager.sessions.values())
        manager.stop()
        return runs, third, sessions

    runs, third, sessions = asyncio.run(go())
    assert all(r.ok for r in runs) and third.ok and len(sessions) == 3
    assert {s.brief for s in sessions} == {"the nuclei's sizes", "the nuclei's shapes", "their brightness"}


def test_start_researcher_says_a_follow_up_steers_the_one_just_started(tmp_path):
    """The model judges whether the user's next words add to the researcher it just started (Researchers lists it,
    starting or running) or ask for another; the tool no longer tells it to always start a new one."""
    hub = FakeHub()
    words = research_tools(hub, ResearchManager(hub, root=tmp_path)).tools["start_researcher"].description
    assert "always start a new one" not in words
    assert "researcher_note" in words and "just started" in words and "same answer" in words
