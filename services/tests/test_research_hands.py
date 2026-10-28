"""A researcher's hands (orchestrator/research/hands.py; the partner §2.2, "Hands: run"): a researcher asks the
app to run one of its own operations on the live view now, for its reading; what it makes is a session entry,
made for it, never shown; one at a time, within its selected effort's operation limit; refused when it can't."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestrator.population import layer_of
from orchestrator.research import ResearchManager, Session
from orchestrator.research.hands import ELSEWHERE, Hands
from tests.test_population import world  # noqa: F401  (the fixture: a stand-in cell finder)
from tests.test_research import FakeHub
import time


def session(engine="fake", **more) -> Session:
    return Session(rid="r2", day="2026-10-07", n=2, brief="b", deliverable="", effort="quick", engine=engine,
                   purpose="research", label="", object_id=None, origin={"channel": "chat", "thread": "t3"},
                   status="running", **more)


def test_a_researcher_runs_the_cell_finder_for_itself_and_nothing_is_shown(world):
    logged = []
    hands = Hands(world, log=logged.append)
    s = session()
    reply = asyncio.run(hands.run(s, "segment", "how much do they vary in size"))
    assert reply["ok"] and reply["id"].startswith("e")
    assert reply["words"].startswith("segmented the live view: Cell outlines, for Researcher 2 · ")
    assert "6 instances segmented by cellpose-sam" in reply["words"]  # the app's own words: instances, never "cells" recognised
    from orchestrator.evidence import evidence_of

    population = evidence_of(world).get(reply["id"])
    assert population.made_by["for"] == "Researcher 2" and population.made_by.get("origin") == "background"
    assert population.thread == "t3"  # filed with the conversation that started the researcher
    assert layer_of(world).population() is None  # never on screen
    # Which model ran, by the catalog key Tools lists (S12): the researcher's Tab says "Ran Cellpose-SAM" from it.
    assert s.ran == [{"id": reply["id"], "operation": "segment", "why": "how much do they vary in size", "run": 0,
                      "at": s.ran[0]["at"], "count": 6, "capability": "cellpose-sam", "title": population.title, "kind": "population",
                      "words": reply["words"],  # the app's own words: a stopped run's result lists them
                      "version": population.revision}]  # what it had the app make, said on its card (#166)
    assert logged[-1]["kind"] == "research-run" and logged[-1]["ok"] is True


def test_what_it_cannot_run_is_refused_with_the_reason_and_a_next_step(world):
    from research_worker.budget import EFFORTS

    hands = Hands(world)
    s = session()
    assert asyncio.run(hands.run(s, "teleport"))["error"] == "the app does not run 'teleport' for researchers"
    hands.busy = "r9"  # another researcher's operation is running: one at a time, app-wide
    assert asyncio.run(hands.run(s, "segment"))["error"].startswith("another researcher's operation is running")
    hands.busy = None
    world.source.is_fresh = lambda: False
    refused = asyncio.run(hands.run(s, "segment"))
    assert refused["error"] == "no fresh picture from the camera" and refused["next"].startswith("carry on without it")
    world.source.is_fresh = lambda: True
    limit = EFFORTS[s.run_effort].max_rounds
    s.ran = [{"id": f"e{n}", "operation": "segment", "run": 0} for n in range(limit)]
    assert asyncio.run(hands.run(s, "segment"))["error"] == f"at most {limit} operations in one run"
    s.history.append({"q": "a follow-up"})  # a new run of it: its own allowance
    assert asyncio.run(hands.run(s, "segment"))["ok"]


def test_a_cell_finder_that_cannot_run_is_said_with_its_own_next_step(world, monkeypatch):
    from orchestrator.brain.registry import ToolFailed
    import orchestrator.population as population_module

    async def refused(hub, **kwargs):
        raise ToolFailed("the memory for unsaved evidence is full", next="clear earlier analyses")

    monkeypatch.setattr(population_module, "segment", refused)
    reply = asyncio.run(Hands(world).run(session(), "segment"))
    # Its words kept; the next step is one a researcher can take (it can't clear the user's analyses).
    assert reply == {"ok": False, "error": "the memory for unsaved evidence is full",
                     "next": "carry on without it, and say what could not be measured"}


def test_the_manager_answers_a_run_request_keeps_what_it_ran_and_refuses_a_finished_researcher(tmp_path, world):
    manager = ResearchManager(FakeHub(), root=tmp_path)
    manager.hands = Hands(world)
    s = session()
    s.root = tmp_path
    manager.sessions["r2"] = s
    reply = asyncio.run(manager._request({"op": "run", "rid": "r2", "operation": "segment", "why": "sizes"}))
    assert reply["ok"]
    saved = json.loads((tmp_path / "2026-10-07" / "r2" / "session.json").read_text())
    assert saved["ran"][0]["id"] == reply["id"]
    s.status = "done"
    assert asyncio.run(manager._request({"op": "run", "rid": "r2", "operation": "segment"})) == {
        "ok": False, "error": "it has finished"}
    assert asyncio.run(manager._request({"op": "fly", "rid": "r2"}))["error"] == "unknown request fly"


def test_the_researchers_run_tool_asks_the_app_and_reads_its_answer():
    from research_worker.researcher import Researcher

    asked = []

    async def request(op, **payload):
        asked.append((op, payload))
        return {"ok": True, "id": "e7", "title": "Cell outlines · 18:04, for Researcher 2",
                "words": "outlined and measured 46 cells in the live view (Cell outlines · 18:04, e7)"}

    from research_worker.budget import EFFORTS

    researcher = Researcher.__new__(Researcher)
    researcher.rid, researcher.runs, steps, emitted = "r2", 0, [], []
    researcher.effort, researcher.started_at = EFFORTS["standard"], time.monotonic()
    researcher._looked_at = set()
    researcher.emit = lambda event, **fields: emitted.append((event, fields))
    researcher.request = request
    researcher.step = lambda text, kind="step", detail=None: steps.append(text)
    result, picture = asyncio.run(researcher.tool_run({"operation": "segment", "why": "sizes"}))
    # What it made is the app's own record (hands.py `ran`), never the research process's echo (#166 review, note 1).
    assert emitted == []
    assert result["ok"] and result["id"] == "e7" and "session tool" in result["note"] and picture is None
    assert asked == [("run", {"rid": "r2", "operation": "segment", "why": "sizes", "wait": 185.0, "timeout": 185.0})]
    # Plain on its card (Slice 7, 2026-10-11): the researcher's reason and the entry's id are in each step's detail.
    assert steps == ["Asking the app to segment every cell in the picture on the Stage and measure them",
                     "The app outlined and measured 46 cells in the live view (Cell outlines · 18:04)"]

    async def refused(op, **payload):
        return {"ok": False, "error": "no fresh picture from the camera", "next": "carry on without it"}

    researcher.request = refused
    result, _ = asyncio.run(researcher.tool_run({"operation": "segment", "why": "sizes"}))
    assert result == {"error": "no fresh picture from the camera", "next": "carry on without it"}
    researcher.runs = EFFORTS["standard"].max_rounds
    assert "at most" in asyncio.run(researcher.tool_run({"operation": "segment"}))[0]["error"]
    assert "operation is one of" in asyncio.run(researcher.tool_run({"operation": "fly"}))[0]["error"]


@pytest.mark.parametrize("effort,deliverable,purpose,index,offered", [
    ("quick", "", "research", True, True),
    ("standard", "lesson", "research", True, True),
    ("instant", "", "research", True, False),  # no time to spare
    ("quick", "", "research", False, False),  # nothing to read what it makes with
    ("quick", "answer", "research", True, False),  # a follow-up answers from its work
    ("describe", "", "description", True, False),
])
def test_run_is_offered_only_where_its_result_can_be_read_and_there_is_time(effort, deliverable, purpose, index, offered):
    from research_worker.budget import EFFORTS
    from research_worker.researcher import Researcher

    researcher = Researcher.__new__(Researcher)
    researcher.spec = {"deliverable": deliverable, "purpose": purpose}
    researcher.effort = EFFORTS[effort]
    researcher.session_index = object() if index else None
    researcher.crop_jpeg = researcher.frame_jpeg = b"x"
    researcher.plan_due = lambda: False
    assert ("run" in researcher.tool_names()) is offered


def test_what_a_researcher_made_is_never_the_cells_a_users_request_means_unless_shown_or_named(world):
    """The partner's hook in M2.2's rule (made for someone else): a researcher's cells stand in for "the cells"
    only by their id, "@n", or once shown; with nothing else, saying why names them."""
    from orchestrator.population import nothing_here, resolve

    reply = asyncio.run(Hands(world).run(session(), "segment", "sizes"))
    eid = reply["id"]
    from orchestrator.evidence import evidence_of

    population = evidence_of(world).get(eid)
    assert population.made_for == "Researcher 2" and population.title.startswith("Cell outlines, for Researcher 2 · ")
    assert reply["title"] == population.title
    assert resolve(world, None) is None  # "count the cells": not the researcher's
    assert nothing_here(world).startswith(f"only cells segmented for Researcher 2 are here ({eid})")
    assert resolve(world, eid) is population  # named, it stands
    layer_of(world).show(population)
    assert resolve(world, None) is population  # shown, it stands (what is shown stands, whatever made it)



def test_the_researchers_wait_is_the_operations_own_and_an_unfinished_operation_is_said_as_such():
    """Review of #173, SF3, as the clock rule has it now (budget.py: the clock never cuts work): it waits as long as
    the operation may take, however much of its effort's time is used, and "not answered" is said as still possibly
    coming."""
    from research_worker.budget import EFFORTS, RUN_WAIT_MAX_S
    from research_worker.researcher import Researcher

    asked = []

    async def slow(op, **payload):
        asked.append(payload["timeout"])
        return {"ok": False, "error": "the app did not answer"}

    researcher = Researcher.__new__(Researcher)
    researcher.rid, researcher.runs, steps = "r2", 0, []
    researcher.effort, researcher.started_at = EFFORTS["quick"], time.monotonic() - 300  # long past its 75 s
    researcher.request = slow
    researcher.step = lambda text, kind="step", detail=None: steps.append(text)
    result, _ = asyncio.run(researcher.tool_run({"operation": "segment", "why": "sizes"}))
    assert asked[0] == RUN_WAIT_MAX_S  # the cell finder's own limit, never cut to a clock
    assert result["error"] == (f"not answered in {RUN_WAIT_MAX_S:.0f} s: the app lets it go when you stop waiting, "
                               "so nothing is made for you")
    assert researcher.runs == 0  # it made nothing: none of its runs used


def test_two_runs_in_a_row_both_run_and_what_it_ran_comes_back_after_a_restart(tmp_path, world):
    """Review of #173, SF5: the operation in progress is cleared after each run; `ran` is saved and read back."""
    manager = ResearchManager(FakeHub(), root=tmp_path)
    manager.hands = Hands(world)
    s = session(engine="openai")  # a real engine: read_day leaves scripted ones out
    s.root = tmp_path
    manager.sessions["r2"] = s
    first = asyncio.run(manager._request({"op": "run", "rid": "r2", "operation": "segment", "why": "a"}))
    second = asyncio.run(manager._request({"op": "run", "rid": "r2", "operation": "segment", "why": "b"}))
    assert first["ok"] and second["ok"] and manager.hands.busy is None
    again = ResearchManager(FakeHub(), root=tmp_path)
    restored = again.read_day("2026-10-07", kept_only=False)
    kept = next(x for x in restored if x.rid == "r2")
    assert [r["why"] for r in kept.ran] == ["a", "b"]


def test_an_answer_or_a_revision_runs_no_operations_whatever_its_worker_says(tmp_path, world):
    """Review of #173, note 3: the app's own gate, beside the worker's tool list."""
    from orchestrator.research.run import Run

    manager = ResearchManager(FakeHub(), root=tmp_path)
    manager.hands = Hands(world)
    s = session()
    s.run = Run(kind="answer", status_before="done", effort="quick", brief="why?", question="why?")
    manager.sessions["r2"] = s
    reply = asyncio.run(manager._request({"op": "run", "rid": "r2", "operation": "segment"}))
    assert reply == {"ok": False, "error": "this run of it does not run the app's operations"}


def test_another_session_open_is_refused_and_any_failure_is_said_plainly(world, monkeypatch):
    """Review of #173, SF4 and note 4. The session open decides, never the date (ADR 0017): its own session the next
    day runs its operations; another session's live view is never its sample."""
    import orchestrator.population as population_module

    world.execution_day = SimpleNamespace(day="2026-10-07.2", sid="2026-10-07.2", date="2026-10-07", active_id="t1")
    reply = asyncio.run(Hands(world).run(session(), "segment"))
    assert reply["error"] == ELSEWHERE
    world.execution_day = SimpleNamespace(day="2026-10-07", sid="2026-10-07", date="2026-10-08", active_id="t1")
    assert not Hands(world).elsewhere(session())  # its own session after midnight
    world.execution_day = SimpleNamespace(day="2026-10-07", sid="2026-10-07", date="2026-10-07", active_id="t1")
    logged = []

    async def broken(hub, **kwargs):
        raise ValueError("bad JPEG")

    monkeypatch.setattr(population_module, "segment", broken)
    reply = asyncio.run(Hands(world, log=logged.append).run(session(), "segment"))
    assert reply["error"] == "the app could not segment the view this time" and "ValueError" not in reply["error"]
    assert logged[-1]["error"] == "ValueError: bad JPEG"


def test_the_population_is_made_for_the_researcher_from_the_start_and_the_brain_is_not_told_it_is_coming(world, monkeypatch):
    """Review of #173, SF1 and SF5 (M7): attributed when made (its first save and the index see it so), and a
    researcher's running segmentation is never on the brain's board as this conversation's."""
    from orchestrator.evidence import evidence_of
    from orchestrator.population import evidence_lines

    seen = []
    store = evidence_of(world)
    original_add = store.add

    def add(population):
        seen.append(dict(population.made_by))
        return original_add(population)

    monkeypatch.setattr(store, "add", add)
    asyncio.run(Hands(world).run(session(), "segment"))
    assert seen and seen[0]["for"] == "Researcher 2" and seen[0]["origin"] == "background"
    job = SimpleNamespace(execution=SimpleNamespace(operation="cells", origin="background", at=0.0, requested="cellpose-sam"),
                          task=SimpleNamespace(done=lambda: False))
    world.runner = SimpleNamespace(jobs={"k": job})
    assert not any("segmenting every cell now" in line for line in evidence_lines(world))


def test_a_researchers_cell_work_waits_behind_the_users_and_never_takes_or_supersedes_its_slots():
    """Review of #173, BLOCKING: the GPU worker serves a researcher's own work last ("research"), it is bounded
    apart from the app's own background work, and it never drops a call made for the user."""
    from gpu_worker.inbox import Inbox
    from orchestrator.gpu_client import GpuClient

    box = Inbox({"ping", "segment"})
    for i, cls in enumerate(["research", "background", "research", "click", "background"]):
        box.put({"id": i, "op": "cells", "_priority": cls})
    assert [box.get()["id"] for _ in range(5)] == [3, 1, 4, 0, 2]  # the user's first, the researchers' last

    async def go():
        client = GpuClient("micromind-gpu-heavy")
        client._loop = asyncio.get_running_loop()
        sent = []
        client._conn = SimpleNamespace(send=sent.append)
        # Two researchers' cell runs in flight, sent as a researcher's run sends them.
        researchers = [asyncio.ensure_future(client.call("cells", timeout=5.0, priority="research")) for _ in range(2)]
        await asyncio.sleep(0.01)
        user = asyncio.ensure_future(client.call("cells", timeout=5.0, priority="background"))
        await asyncio.sleep(0.01)
        refused = user.done() and user.exception() is not None
        # The user's automatic masks queued, then a researcher's: the user's call is never dropped.
        users_masks = asyncio.ensure_future(client.call("automask", timeout=5.0, priority="background"))
        await asyncio.sleep(0.01)
        users_id = sent[-1]["id"]
        researchers.append(asyncio.ensure_future(client.call("automask", timeout=5.0, priority="research")))
        await asyncio.sleep(0.01)
        before = list(sent)  # what was sent before the test gives everything up (that drops the rest)
        for task in [*researchers, user, users_masks]:
            task.cancel()
        await asyncio.gather(*researchers, user, users_masks, return_exceptions=True)
        return before, refused, users_id

    sent, refused, users_id = asyncio.run(go())
    assert not refused  # not "Busy": the researchers' calls are bounded apart
    assert [m["_priority"] for m in sent if m.get("op") == "cells"] == ["research", "research", "background"]
    assert not any(m.get("op") == "drop" and users_id in (m.get("ids") or []) for m in sent)  # never superseded


def test_a_researchers_job_is_never_shared_with_the_users_and_reaches_the_worker_as_research(world):
    """Through the real runner and adapter: the user's request never joins a researcher's job queued below it,
    and the researcher's call reaches the analysis worker in the "research" class."""
    from orchestrator.capture import captures
    from orchestrator.operations import Prompt, runner
    from tests.test_population import CellFinder

    finder, classes = CellFinder(), []

    class Slow:
        async def ensure_started(self):
            return self

        async def call(self, op, timeout=0, **payload):
            classes.append(payload.get("priority"))
            await asyncio.sleep(0.05)
            return await finder.call(op, timeout, **payload)

    world.gpu_heavy = Slow()

    async def go():
        capture = captures(world).get(world.latest_info.id)
        base = {"crop": None, "disc": None}
        await asyncio.gather(
            runner(world).run("cells", capture, Prompt("field"), origin="background", params={**base, "priority": "research"}),
            runner(world).run("cells", capture, Prompt("field"), origin="brain-default", params=dict(base)))

    asyncio.run(go())
    assert sorted(classes) == ["background", "research"]  # two jobs, each in its own class


def test_a_researchers_run_reaches_the_cell_finder_in_the_research_class(world):
    """End to end from the hands: the cell finder is asked in "research", below the user's own work."""
    from tests.test_population import CellFinder

    finder, classes = CellFinder(), []

    class Recording:
        async def ensure_started(self):
            return self

        async def call(self, op, timeout=0, **payload):
            classes.append(payload.get("priority"))
            return await finder.call(op, timeout, **payload)

    world.gpu_heavy = Recording()
    assert asyncio.run(Hands(world).run(session(), "segment"))["ok"]
    assert classes == ["research"]



# --- the re-check of #173 ------------------------------------------------------------------------------------
class SlowFinder:
    """The analysis worker's cell finder, slow (a researcher's call waits behind the live view's work)."""

    def __init__(self, seconds, op_seen=None):
        from tests.test_population import CellFinder

        self.finder, self.seconds, self.seen = CellFinder(), seconds, op_seen if op_seen is not None else []

    async def ensure_started(self):
        return self

    async def call(self, op, timeout=0, **payload):
        self.seen.append((op, payload.get("priority")))
        await asyncio.sleep(self.seconds)
        return await self.finder.call(op, timeout, **payload)


def test_a_researchers_run_holds_its_own_memory_never_the_users(world):
    """Review of #173 (re-check, SF1): a queued research run reserved its worst case in the user's unsaved
    allowance (20 of 32 MB at 1080p), so the user's own "outline every cell" was refused."""
    from orchestrator.capture import captures

    world.gpu_heavy = SlowFinder(0.2)
    memory = captures(world).memory

    async def go():
        task = asyncio.ensure_future(Hands(world).run(session(), "segment"))
        await asyncio.sleep(0.05)
        during = dict(memory.reserved)
        return during, await task

    during, reply = asyncio.run(go())
    assert reply["ok"] and during["research"] > 0 and during["unsaved"] == 0
    shown = captures(world).wire()["classes"]["research"]  # health() and /api/health show it beside the others
    assert shown["allowanceMb"] > 0 and shown["peakMb"] > 0


def test_a_run_the_researcher_stopped_waiting_for_is_let_go_and_frees_everything(world):
    """Review of #173 (re-check, SF2): its queued call no longer keeps the one-at-a-time slot and its memory until
    the cell finder's 180 s deadline."""
    from orchestrator.capture import captures

    world.gpu_heavy = SlowFinder(30.0)
    hands, logged = Hands(world, log=None), []
    hands.log = logged.append
    s = session()
    reply = asyncio.run(hands.run(s, "segment", wait=7.0))  # let go at 5 s
    assert reply["ok"] is False and reply["error"].startswith("not done in 5 s (the live view's own work comes first)")
    assert "nothing was made" in reply["error"] and hands.busy is None and not s.ran
    assert captures(world).memory.reserved["research"] == 0 and logged[-1]["error"] == "let go after 5 s"


def test_one_researcher_operation_at_a_time_and_the_slot_is_freed_after_a_failure(world, monkeypatch):
    """Re-check mutations: the lock is taken, and released whatever happens."""
    import orchestrator.population as population_module

    world.gpu_heavy = SlowFinder(0.2)
    hands = Hands(world)
    other = session()
    other.rid, other.n = "r3", 3  # Researcher 3

    async def both():
        first = asyncio.ensure_future(hands.run(session(), "segment"))
        await asyncio.sleep(0.05)
        second = await hands.run(other, "segment")
        return await first, second

    first, second = asyncio.run(both())
    assert first["ok"] and second["error"].startswith("another researcher's operation is running")

    async def broken(hub, **kwargs):
        raise ValueError("no")

    monkeypatch.setattr(population_module, "segment", broken)
    asyncio.run(hands.run(session(), "segment"))
    assert hands.busy is None  # freed after a failure too


def test_a_refused_run_uses_none_of_the_researchers_runs():
    from research_worker.budget import EFFORTS
    from research_worker.researcher import Researcher

    async def refused(op, **payload):
        return {"ok": False, "error": "another researcher's operation is running (one at a time, so the live view comes first)",
                "next": "carry on, and ask again later in this run"}

    researcher = Researcher.__new__(Researcher)
    researcher.rid, researcher.runs = "r2", 0
    researcher.effort, researcher.started_at = EFFORTS["standard"], time.monotonic()
    researcher.request = refused
    researcher.step = lambda text, kind="step", detail=None: None
    asyncio.run(researcher.tool_run({"operation": "segment", "why": "sizes"}))
    assert researcher.runs == 0


def test_while_it_waits_the_researchers_status_says_so(tmp_path, world):
    """Review of #173 (re-check, SF4): §1g's "waiting for the live view"."""
    manager = ResearchManager(FakeHub(), root=tmp_path)
    manager.hands = Hands(world)
    world.gpu_heavy = SlowFinder(0.1)
    s = session()
    s.root, s.step = tmp_path, "Reading the session"
    manager.sessions["r2"] = s
    steps = []
    manager.on_change.append(lambda session: steps.append(session.step))
    assert asyncio.run(manager._request({"op": "run", "rid": "r2", "operation": "segment", "wait": 60.0}))["ok"]
    assert steps[0].startswith("waiting for the live view") and s.step == "Reading the session"


def test_with_automatic_masks_a_researchers_call_is_in_the_research_class(world, monkeypatch):
    """Re-check mutation: the automask adapter takes the run's class (else its newest-only rule drops the user's)."""
    from orchestrator.operations import Unavailable

    seen = []

    class Masks:
        async def ensure_started(self):
            return self

        async def call(self, op, timeout=0, **payload):
            seen.append((op, payload.get("priority")))
            return {"regions": [], "count": 0, "ms": 1}

    world.gpu_heavy = Masks()
    world.settings.cells = "edgetam-automask"
    try:
        asyncio.run(Hands(world).run(session(), "segment"))
    except Unavailable:
        pass
    assert ("automask", "research") in seen


def test_the_users_own_running_segmentation_is_still_on_the_board(world):
    """Re-check mutation: only a researcher's running job is left off the brain's board, never the user's."""
    from orchestrator.population import evidence_lines

    mine = SimpleNamespace(execution=SimpleNamespace(operation="cells", origin="brain-default", at=0.0, requested="cellpose-sam"),
                           task=SimpleNamespace(done=lambda: False))
    world.runner = SimpleNamespace(jobs={"k": mine})
    assert any("segmenting every cell now with cellpose-sam" in line for line in evidence_lines(world))


def test_the_execution_says_which_researcher_it_was_for(world):
    """#173's note 9: the run's Execution names who it was for, in its record and on its wire."""
    from orchestrator.evidence import evidence_of
    from orchestrator.operations import runner

    logged = []
    runner(world).log = logged.append
    hands = Hands(world, log=lambda r: None)
    reply = asyncio.run(hands.run(session(), "segment", "how much do they vary in size"))
    population = evidence_of(world).get(reply["id"])
    executions = [r for r in logged if r.get("kind") == "execution" and r.get("id") == population.made_by["execution"]]
    assert executions and executions[-1]["madeFor"] == "Researcher 2"


# --- Slice 5 (2026-10-11, the user's Q24): a researcher names the cell finder ---------------------------------------
def test_a_researcher_names_the_cell_finder_and_it_runs_exactly_that_one(world, monkeypatch):
    """`run` segment takes the cell finder by id or name (names.py), run raw: that one, or it says why not. Motion is
    measured one way, said; the research process's list of ids and names is the app's."""
    from orchestrator import names, operations
    from orchestrator.evidence import evidence_of
    from research_worker.budget import CELL_FINDERS

    monkeypatch.setitem(operations.INSTALLED, "micro-sam", lambda: "")
    hands = Hands(world, log=lambda r: None)
    reply = asyncio.run(hands.run(session(), "segment", "compare the finders", implementation="micro-sam"))
    population = evidence_of(world).get(reply["id"])
    assert reply["ok"] and population.implementation == "micro-sam-lm" and population.made_by["policy"] == "raw"
    assert " · micro-sam (light microscopy) · " in population.title
    wrong = asyncio.run(hands.run(session(), "segment", "x", implementation="yolo"))
    assert not wrong["ok"] and "there is no cell finder 'yolo'; the choices (id = name): cellpose-sam = Cellpose-SAM" in wrong["error"]
    motion = asyncio.run(hands.run(session(), "motion", "x", implementation="trackpy"))
    assert "motion is done one way (spots followed frame to frame)" in motion["error"]
    space = names.index()["cells"]
    assert CELL_FINDERS == {e.id: e.name for e in space.entries.values() if e.choosable}


def test_the_research_process_asks_for_a_named_cell_finder_and_refuses_one_that_is_not():
    from research_worker.work import SCHEMAS

    finder = SCHEMAS["run"]["parameters"]["properties"]["implementation"]
    assert finder["enum"] == ["cellpose-sam", "cellpose-sam-tuned", "micro-sam-lm", "edgetam-automask",
                              "fiji-analyze-particles", "basic-bright-objects"]
    assert "cellpose-sam = Cellpose-SAM" in finder["description"] and "spots followed frame to frame" in finder["description"]


def test_the_researchers_named_cell_finder_reaches_the_hands(tmp_path, world):
    """The research process names the cell finder in its run request; the manager hands it on as it is."""
    manager = ResearchManager(world, root=tmp_path / "research", today=lambda: "2026-10-07")
    got = []

    async def run(session, operation, why="", **kw):
        got.append((operation, kw.get("implementation")))
        return {"ok": False, "error": "x", "next": "y"}

    manager.hands.run = run
    s = session()
    manager.sessions[s.rid] = s
    asyncio.run(manager._request({"op": "run", "rid": s.rid, "operation": "segment", "why": "w",
                                  "implementation": "micro-sam-lm"}))
    asyncio.run(manager._request({"op": "run", "rid": s.rid, "operation": "segment", "why": "w"}))
    assert got == [("segment", "micro-sam-lm"), ("segment", None)]


def test_a_still_on_the_stage_is_said_as_the_picture_not_the_live_view(world, monkeypatch):
    # Ticket 67: a researcher's step said "The app segmented the live view" while the Stage showed a still picture.
    from orchestrator import population as P

    real = P.segment

    async def on_a_still(*args, **kwargs):
        made = await real(*args, **kwargs)
        made.view["sourceImage"] = {"resource": {"id": "a1", "day": "2026-10-27"}}  # found on the Stage's still
        return made

    monkeypatch.setattr(P, "segment", on_a_still)
    reply = asyncio.run(Hands(world, log=lambda r: None).run(session(), "segment", "sizes"))
    assert reply["words"].startswith("segmented the picture on the Stage: ")
    assert "live view" not in reply["words"]
