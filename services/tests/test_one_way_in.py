"""One way in (the live partner spec, 4.8 horizontal 1; the finalisation plan's W1): every actor's request gets its
current studio report from Hooke itself, through one helper (screen.fresh_report -> Screen.current_report), and a
voice hand-off is a first-class job (voice/jobs.py HandoffJob): listed and numbered with the others, an Activity line by
its label, the chat's `thinking` message with its id, and stoppable by the voice's stop_work, which says "stopped" only
once its work has.

Live 2026-10-29 17:25: a researcher's own studio action (showing its result, `research-not-shown`) failed
"unavailable or stale" while the studio kept reporting: researchers' and connected agents' studio actions never asked
for a report. The App, Registry, ActionRunner, Screen and Announcer are real; the studio is test_workspace_actions_app's
synthetic renderer (AppStudio), not browser evidence; the voice is test_voice_partner's fake sideband."""
import asyncio
import json

from orchestrator import screen as screen_module
from orchestrator.brain.actions import ActionRunner
from orchestrator.brain.registry import CURRENT_RUN, Tool, ToolContext, schema
from orchestrator.screen import Screen
from orchestrator.voice import partner_tools
from orchestrator.voice.jobs import HandoffJob, Job, jobs_of
from tests.test_voice_jobs import job_lines
from tests.test_voice_partner import partner_session
from tests.test_voice_partner import no_tail  # noqa: F401  (autouse: no release tail, no progress lines)
from tests.test_workspace_actions_app import _ObservationClock, built, open_window, research_fixture, saved_picture
from tests.waiting import until_async


def asks(studio) -> int:
    return sum(m.get("type") == "screen-ask" for m in studio.messages)


def probe(app, seen: list) -> None:
    """A tool that records whether the studio's report it reads is current when it runs (what each tool would read)."""
    async def handler(ctx, args):
        screen = app.hub.screen
        client = screen.current_accepted()
        observed = screen.observation(client) if client is not None else None
        seen.append(bool(observed is not None and screen._current_observation(observed)))
        return "read the screen"
    app.brain.registry.add(Tool("probe_screen", "reads the screen", schema({}), handler, kind="effect", inline_ms=2000))


def delivered(app, studio, session):
    """A researcher's delivery as a request made by this studio leaves it (its accepted socket and Send pin), with the
    viewer open on that researcher, as a finished researcher's work is shown."""
    from orchestrator.research.run import Delivery

    pin = app.hub.screen.pin(studio, request_id="research-original")
    session._delivery = Delivery(asked=app.day.here(), day=session.day, client=studio, request_snapshot=pin,
                                 request_id="research-original")
    app.presenter.for_client(studio).show(session.rid)


# --- one way in: the report ------------------------------------------------------------------------------------------
def test_every_actors_request_asks_through_the_one_helper(tmp_path, monkeypatch):
    """Typed Send steps, a voice hand-off, a researcher showing its work and a connected agent's run each go through
    Screen.current_report: the voice for the report now (it carries none of its own), the others when it is stale."""
    from orchestrator import commands
    from orchestrator.door.actor import ACTOR
    from orchestrator.ticket import issue, using

    app, studio = built(tmp_path)
    picture, _ = saved_picture(app)
    _, session = research_fixture(app)
    probe(app, [])
    monkeypatch.setattr(screen_module, "CONFIRM_S", 0.2)
    calls = []
    original = Screen.current_report

    async def spy(self, client=None, *, now=False, timeout=None):
        calls.append((client is studio, now))
        return await original(self, client, now=now, timeout=timeout)
    monkeypatch.setattr(Screen, "current_report", spy)

    async def go():
        await open_window(app, picture)
        found = {}
        # Typed: a Send's steps (ActionRunner), pinned to its own report.
        ticket = issue(app.hub, "turn", asked=app.day.here(), channel="chat")
        ctx = ToolContext("chat", 0, None, question="read the screen", asked=ticket.asked, admission=ticket.admission,
                          request_id=ticket.admission.request_id)
        ctx.client = studio
        ctx.request_snapshot = app.hub.screen.pin(studio, request_id=ticket.admission.request_id)
        runner = ActionRunner(app.brain.registry, app.hub, lambda: None, lambda run, c: asyncio.sleep(0))
        with using(ticket):
            await runner.run([("probe_screen", {})], ctx)
        ticket.seal()
        found["typed"] = list(calls)
        calls.clear()
        # The voice: a hand-off to Hooke's brain.
        tools = partner_tools.PartnerTools(_Brain(app), app.hub, _Partner(), client=studio)
        await tools._do_in_hooke({"request": "Read the screen."})
        found["voice"] = list(calls)
        calls.clear()
        # A researcher's own studio action: showing its result beside the view.
        delivered(app, studio, session)
        await app.announcer._open_beside(session)
        found["research"] = list(calls)
        calls.clear()
        # A connected agent's run through the door (door/work.run_capability runs its tool by commands.run_tool).
        ACTOR.set("agent:ac_test")
        await commands.run_tool(app, "probe_screen", {})
        found["door"] = list(calls)
        return found

    found = asyncio.run(go())
    assert found["typed"][:1] == [(True, False)], "a Send's steps: its studio, only when stale"
    assert found["voice"][:1] == [(True, True)], "a hand-off: the bound studio's report now"
    assert found["research"][:1] == [(True, False)], "a researcher's show: its original studio, when stale"
    assert found["door"][:1] == [(False, False)], "an agent's run names no studio: the one whose report counts"


def test_a_researchers_show_on_a_stale_report_asks_for_one_and_is_never_refused_as_stale(tmp_path, monkeypatch):
    """17:25:31: Researcher 2's result was not shown, "research-not-shown ... unavailable or stale", because the last
    report taken was over 30 s old. Showing a researcher's work now asks its studio for a report first."""
    app, studio = built(tmp_path)
    picture, _ = saved_picture(app)
    _, session = research_fixture(app)
    monkeypatch.setattr(screen_module, "CONFIRM_S", 0.3)
    logged = []
    original_log = app.announcer.log
    app.announcer.log = lambda record: (logged.append(record), original_log(record))

    async def go():
        await open_window(app, picture)
        delivered(app, studio, session)
        clock = _ObservationClock(monkeypatch, app)
        clock.offset = 45.0  # the last report taken is 45 s old, the studio in view
        before = asks(studio)
        said = await app.announcer._open_beside(session)
        assert "ownership expired" not in said, said
        assert asks(studio) > before, "the studio was asked for its report"
        assert app.hub.screen._current_observation(app.hub.screen.observation(studio)), "a report taken now"
        assert not any("stale" in str(r.get("error") or "") for r in logged if r.get("kind") == "research-not-shown")

    asyncio.run(go())


def test_a_connected_agents_run_on_a_stale_report_asks_for_one_before_its_tool_reads_the_screen(tmp_path, monkeypatch):
    """The door's runs (door/work.run_capability -> commands.run_tool, as the agent) carry no studio of their own: the
    studio whose report counts is asked when its report is stale, before the tool runs, as every request is."""
    from orchestrator import commands
    from orchestrator.door.actor import ACTOR

    app, studio = built(tmp_path)
    picture, _ = saved_picture(app)
    seen = []
    probe(app, seen)

    async def go():
        await open_window(app, picture)
        clock = _ObservationClock(monkeypatch, app)
        clock.offset = 45.0
        before = asks(studio)
        ACTOR.set("agent:ac_test")
        done = await commands.run_tool(app, "probe_screen", {})
        assert done["ok"], done
        assert asks(studio) > before and seen == [True], "the tool read a report asked for at the run"

    asyncio.run(go())


class _Brain:
    """Hooke's brain as the hand-off meets it: a turn that runs the probe through the real registry."""

    def __init__(self, app):
        self.app, self.registry, self.conversation = app, app.brain.registry, app.brain.conversation

    async def turn(self, text, channel, **kwargs):
        if kwargs.get("request_ticket") is not None:
            kwargs["request_ticket"].seal()
        return {"answer": "Read it.", "actions": []}


class _Partner:
    session = None

    def turn(self):
        return 1

    async def words(self, n, wait):
        return "read the screen"

    def when_asked(self, n):
        return None

    async def snapshot(self):
        return None

    def screen_now(self):
        return ""

    def spawn(self, coro):
        return asyncio.ensure_future(coro)

    def log(self, record):
        pass


# --- a hand-off is a job ---------------------------------------------------------------------------------------------
class Shown:
    def __init__(self):
        self.lines, self.flying = [], set()

    def show(self, line, *, asked=None, rid=None):
        self.lines.append(dict(line))
        return line


def rows(shown):
    return [line for line in shown.lines if line["type"] == "handoff"]


async def handing_off(monkeypatch):
    t = await partner_session(monkeypatch, acts="brain")
    t.shown = Shown()
    t.partner.tools.handoffs.dialogue = t.shown
    t.sent = []
    t.hub.broadcast = t.sent.append
    return t


def thinking(t):
    return [m for m in t.sent if m.get("type") == "thinking"]


def test_a_hand_off_is_a_job_listed_and_numbered_with_its_activity_line_and_thinking_message(monkeypatch):
    """Will, 29 Oct: no sign that Hooke's brain was working while the voice talked. A hand-off is now Hooke's own work in
    the one list of jobs (numbered with think jobs and researchers), its Activity line by its label, and the chat's
    working signal (`thinking`, with its id) from the call until the brain's turn returns."""
    from orchestrator import project_state
    from orchestrator.voice.think import ThinkTask

    lines = job_lines(monkeypatch)

    async def go():
        t = await handing_off(monkeypatch)
        gate = asyncio.Event()

        async def turn(text, channel, **kw):
            await gate.wait()
            return {"answer": "There are three Tabs.", "actions": []}
        t.brain.turn = turn
        think = jobs_of(t.hub).add(Job(ThinkTask("compare three outline models")))
        call = asyncio.ensure_future(t.partner.tools.do_in_hooke({"request": "how many tabs are open?",
                                                                  "label": "Count the Tabs"}))
        row = (await until_async(lambda: rows(t.shown)))[0]
        job = jobs_of(t.hub)[row["id"]]
        assert isinstance(job, HandoffJob) and job.kind == "hooke" and job.state == "running"
        think.project = job.project  # the same Project (the stand-in hub's)
        listed = project_state.jobs(t.hub, job.project)
        assert [(r["n"], r["kind"], r["id"]) for r in listed] == [(1, "think", think.id), (2, "hooke", job.id)]
        assert listed[1]["label"] == "Count the Tabs" and listed[1]["group"] == "running"
        on = await until_async(lambda: thinking(t))
        assert on[0] == {"type": "thinking", "on": True, "id": row["id"], "channel": "voice", "job": job.id,
                         "label": "Count the Tabs"}
        assert (f"command:job-{job.id}", "running", "running", "Assistant: Count the Tabs: working") in lines
        gate.set()
        out = await call
        assert out["ok"] is True and job.state == "done" and job.verified is True
        off = thinking(t)[-1]
        assert off["on"] is False and off["id"] == row["id"] and off["state"] == "done"
        assert lines[-1] == (f"command:job-{job.id}", "done", "done", "Assistant: Count the Tabs: done")
        assert len(thinking(t)) == 2, "one on, one off"
    asyncio.run(go())


def test_an_unconfirmed_change_makes_the_job_uncertain_and_a_failed_one_failed(monkeypatch):
    lines = job_lines(monkeypatch)
    unseen = {"tool": "tabs", "ok": True, "summary": "sent, but not confirmed on screen",
              "outcome": {"status": "unobserved", "failure": {"code": "STUDIO_UNREPORTED"}}}
    failed = {"tool": "show_measurements", "ok": False, "summary": "no measurements yet"}

    async def go():
        t = await handing_off(monkeypatch)
        for actions, state, activity, words in (([unseen], "uncertain", "failed", "sent, but not confirmed on screen"),
                                                ([failed], "failed", "failed", "did not work")):
            t.brain.turn = lambda text, channel, actions=actions, **kw: asyncio.sleep(0, {"answer": "", "actions": actions})
            await t.partner.tools.do_in_hooke({"request": "open the table in a new tab", "label": "The table"})
            job = jobs_of(t.hub)[rows(t.shown)[-1]["id"]]
            assert job.state == state and job.verified is False
            assert lines[-1][1:] == (activity, state, f"Assistant: The table: {words}"), "never done"
            assert thinking(t)[-1]["state"] == state
    asyncio.run(go())


def slow_tool(t, started: list, *, holds_s: float = 0.0):
    """A step that runs until it is stopped (a web search the user calls off); `holds_s`: it takes that long to stop."""
    async def handler(ctx, args):
        started.append(CURRENT_RUN.get())
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if holds_s:
                await asyncio.sleep(holds_s)
            raise
    t.brain.registry.add(Tool("retrieve_page", "finds a page", schema({}), handler, kind="effect", inline_ms=50))


def answering_with_a_late_step(t):
    """Hooke's brain answers at once while its step still runs (a late step), as a real turn does: through the real
    ActionRunner, so the step is the request's, under the hand-off's stop holder."""
    async def turn(text, channel, **kw):
        async def late(run, ctx):
            pass
        runner = ActionRunner(t.brain.registry, t.hub, lambda: None, late)
        runs = await runner.run([("retrieve_page", {})], ToolContext("voice", 0, None, question=text))
        return {"answer": "Looking for one now.", "actions": [r.wire() for r in runs]}
    return turn


def test_stop_work_stops_a_running_hand_off_and_says_stopped_only_once_it_has(monkeypatch):
    """"Find a YouTube video about stomata. Actually, stop that.": the brain answered and its step still runs; stop_work
    cancels the step (stopped as the user's stop, never a failure), and answers "Stopped" once it has ended."""
    lines = job_lines(monkeypatch)
    started = []

    async def go():
        t = await handing_off(monkeypatch)
        slow_tool(t, started)
        t.brain.turn = answering_with_a_late_step(t)
        out = await t.partner.tools.do_in_hooke({"request": "find a YouTube video about stomata",
                                                 "label": "A video about stomata"})
        assert out["ok"] is None, "still running"
        job = jobs_of(t.hub)[rows(t.shown)[-1]["id"]]
        assert job.state == "running" and job.doing == "retrieve_page" and len(job.pending) == 1
        assert rows(t.shown)[-1]["settling"] is True
        stop = next(s for s in t.partner.tools.specs() if s.name == "stop_work")
        receipt = await stop.handler({})
        assert receipt["result"] == "verified" and receipt["said"] == "Stopped." and receipt["job"] == job.id
        run = started[0]
        assert run.task.done() and run.stopped is True and run.ok is False, "the step itself stopped, as a stop"
        assert job.state == "cancelled"
        last = rows(t.shown)[-1]
        assert last["state"] == "unknown" and last["stopped"] is True and "Stopped when you asked" in last["reason"]
        assert [l[2] for l in lines if l[0] == f"command:job-{job.id}"][-2:] == ["cancelling", "cancelled"]
        assert lines[-1][3] == "Assistant: A video about stomata: stopped"
        again = await stop.handler({"id": job.id})
        assert again["result"] == "failed" and "already ended (stopped)" in again["said"]
        nothing = await stop.handler({})
        assert nothing["result"] == "failed" and "nothing to stop" in nothing["said"]
    asyncio.run(go())


def test_a_stop_that_takes_a_while_is_said_when_it_has_stopped_never_before(monkeypatch):
    """The brain's turn is still working when the user says stop; its step takes longer than stop_work waits to end:
    stop_work says it is being stopped, and "stopped" comes once, from the job's own end."""
    monkeypatch.setattr(partner_tools, "STOP_WAIT_S", 0.05)
    monkeypatch.setattr(partner_tools, "TOOL_WAIT_S", 0.05)
    started = []

    async def go():
        t = await handing_off(monkeypatch)
        slow_tool(t, started, holds_s=0.4)
        gate = asyncio.Event()
        late = answering_with_a_late_step(t)

        async def turn(text, channel, **kw):
            await gate.wait()
            return await late(text, channel, **kw)
        t.brain.turn = turn
        out = await t.partner.tools.do_in_hooke({"request": "find a video", "label": "A video"})
        assert out.get("still_working")
        job = jobs_of(t.hub)[rows(t.shown)[-1]["id"]]
        gate.set()
        await until_async(lambda: started)
        receipt = await t.partner.tools.stop_work({"id": job.id})
        assert receipt["result"] == "running" and "has not stopped yet" in receipt["said"]
        assert job.state == "cancelling" and not t.session.bus.result_for(job.ref()), "nothing said yet"
        event = await until_async(lambda: t.session.bus.result_for(job.ref()))
        assert event.kind == "job-cancelled" and json.loads(event.text)["stopped"] is True
        assert job.state == "cancelled" and started[0].stopped is True
        await asyncio.sleep(0.1)
        assert sum(1 for e in [t.session.bus.result_for(job.ref())] if e) == 1, "said once"
    asyncio.run(go())


def test_stopping_the_brains_turn_itself_cancels_the_hand_off_and_its_call_says_nothing_more(monkeypatch):
    """Stopped while Hooke's brain is still deciding (no step yet): the turn is cancelled, its row says it was stopped,
    and the voice's own pending call for it comes back stopped, with nothing more to say."""
    async def go():
        t = await handing_off(monkeypatch)
        entered = asyncio.Event()

        async def turn(text, channel, **kw):
            entered.set()
            await asyncio.Event().wait()
        t.brain.turn = turn
        call = asyncio.ensure_future(t.partner.tools.do_in_hooke({"request": "find a video", "label": "A video"}))
        await entered.wait()
        receipt = await t.partner.tools.stop_work({})
        assert receipt["result"] == "verified"
        out = await call
        assert out["stopped"] is True and out["result"] == "cancelled" and "say nothing more" in out["note"]
        job = jobs_of(t.hub)[rows(t.shown)[-1]["id"]]
        assert job.state == "cancelled" and rows(t.shown)[-1]["stopped"] is True
        assert thinking(t)[-1]["on"] is False and thinking(t)[-1]["state"] == "cancelled"
    asyncio.run(go())


def test_a_hand_off_whose_late_step_lands_ends_from_that_steps_receipt(monkeypatch):
    """The brain answered while a step still ran: the job stays running until that step ends, then takes its receipt
    (done here), and the row's line in the journal ends with it."""
    async def go():
        t = await handing_off(monkeypatch)
        gate = asyncio.Event()

        async def handler(ctx, args):
            await gate.wait()
            CURRENT_RUN.get().outcome = {"status": "confirmed"}
            return "outlined 12 cells"
        t.brain.registry.add(Tool("outline_cells", "outlines", schema({}), handler, kind="effect", inline_ms=50))

        async def turn(text, channel, **kw):
            runner = ActionRunner(t.brain.registry, t.hub, lambda: None, lambda run, ctx: asyncio.sleep(0))
            runs = await runner.run([("outline_cells", {})], ToolContext("voice", 0, None, question=text))
            return {"answer": "Outlining them now.", "actions": [r.wire() for r in runs]}
        t.brain.turn = turn
        await t.partner.tools.do_in_hooke({"request": "outline every cell", "label": "Outline every cell"})
        job = jobs_of(t.hub)[rows(t.shown)[-1]["id"]]
        assert job.state == "running" and job.doing == "outline_cells"
        gate.set()
        await until_async(lambda: job.state == "done")
        assert job.verified is True
        last = rows(t.shown)[-1]
        assert last["state"] == "done" and not last.get("settling") and last["steps"][0]["ok"] is True
    asyncio.run(go())
