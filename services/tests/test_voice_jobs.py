"""The mind as a job, and researchers as jobs too (voice/jobs.py; partner_tools think, jobs, update_job, cancel_job;
the part 2.5 spec, 4.4), against the fake backend and sideband of test_voice_think and test_voice_partner."""
import asyncio
import json
import re
import time
from types import SimpleNamespace

import httpx

from orchestrator.brain.registry import CURRENT_RUN
from orchestrator.voice import think as thinking
from orchestrator.voice.base import VoiceConfig
from orchestrator.voice.jobs import Job, jobs_of
from orchestrator.voice.openai_realtime import RealtimeSession
from tests.test_voice_partner import FakeSideband, done, partner_session, press, release
from tests.test_voice_partner import no_tail  # noqa: F401  (autouse: no release tail, no progress lines)
from tests.test_voice_think import FakeBackend, creates, with_backend
from tests.waiting import until_async


def tool(t, name):
    return next(s for s in t.session.config.tools if s.name == name)


def prep_of(t, after: int = 0):
    """The out-of-band wording of a result (its instructions and what it was given), the `after`-th one."""
    found = [e["response"] for e in creates(t) if e["response"].get("conversation") == "none"]
    return found[after] if len(found) > after else None


def given(response) -> str:
    return response["input"][0]["content"][0]["text"]


def confirmed(reply):
    """A registry tool whose own run says the studio showed it (ToolRun.outcome), so Hands can verify it."""
    async def handler(ctx, args):
        CURRENT_RUN.get().outcome = {"status": "confirmed"}
        return reply
    return handler


def job_lines(monkeypatch):
    """Every Activity line a job files, captured: (id, Activity state, the job's own state, text)."""
    lines = []

    def file(hub, kind, text, **kw):
        ids = ((kw.get("details") or {}).get("ids") or {})
        if str(kw.get("id") or "").startswith("command:job-"):
            lines.append((kw["id"], kw.get("state"), ids.get("state"), text))
    monkeypatch.setattr("orchestrator.activity.activity", file)
    return lines


# --- the lifecycle ---------------------------------------------------------------------------------------------------
def test_a_think_job_is_queued_then_running_then_done_and_its_activity_line_follows_it(monkeypatch):
    lines = job_lines(monkeypatch)
    gate = asyncio.Event()

    async def go():
        t = await with_backend(monkeypatch, FakeBackend(report={"say": "Cellpose found 342.", "checked": True,
                                                                "done": True}, wait=gate))
        started = await tool(t, "think").handler({"request": "compare three outline models"})
        job = t.partner.tools.thinking[started["job"]]
        assert started["task"] == job.id and isinstance(job, Job)
        await until_async(lambda: job.state == "running")
        gate.set()
        await until_async(lambda: job.state == "done")
        mine = [line for line in lines if line[0] == f"command:job-{job.id}"]
        assert [line[2] for line in mine][:2] == ["queued", "running"] and mine[-1][2] == "done"
        assert mine[-1][1] == "done" and mine[-1][3] == "Assistant: thinking it through: compare three outline models: done"
        assert job.verified is True and job.ended is not None
    asyncio.run(go())


def test_talking_over_the_voice_or_taking_over_never_cancels_a_job(monkeypatch):
    """Interrupting speech never cancels a job: the key pressed, a turn answered, Escape and the mouse, all while it
    works; its result comes and is said."""
    gate = asyncio.Event()

    async def go():
        t = await with_backend(monkeypatch, FakeBackend(report={"say": "Two groups.", "checked": True, "done": True},
                                                        wait=gate))
        started = await tool(t, "think").handler({"request": "group them by look"})
        job = t.partner.tools.thinking[started["job"]]
        await press(t, 1)
        await t.partner.command({"type": "takeover", "why": "talk", "version": 0, "at": 0})
        await release(t, 1)
        await done(t.sb, "resp_1")
        await t.partner.command({"type": "takeover", "why": "mouse", "version": 0, "at": 0})
        await t.partner.command({"type": "talk", "cancel": True, "n": 2})
        await asyncio.sleep(0.2)
        assert job.state == "running" and not job.runner.done(), "still working"
        t.session._last_user_at = 0.0
        gate.set()
        prep = await until_async(lambda: prep_of(t))
        assert "Two groups." in given(prep) and job.state == "done"
    asyncio.run(go())


def test_cancel_is_said_only_once_the_job_confirms_it_stopped(monkeypatch):
    lines = job_lines(monkeypatch)
    gate = asyncio.Event()

    async def go():
        t = await with_backend(monkeypatch, FakeBackend(wait=gate))
        started = await tool(t, "think").handler({"request": "compare three outline models"})
        job = t.partner.tools.thinking[started["job"]]
        await until_async(lambda: job.state == "running")
        receipt = await tool(t, "cancel_job").handler({"id": job.id})
        assert receipt["result"] == "running" and "has not stopped yet" in receipt["said"]
        assert job.state == "cancelling" and not t.session.bus.result_for(job.ref()), "nothing said yet"
        await until_async(lambda: job.state == "cancelled")
        event = await until_async(lambda: t.session.bus.result_for(job.ref()))
        assert event.kind == "job-cancelled" and json.loads(event.text)["state"] == "cancelled"
        prep = await until_async(lambda: prep_of(t))
        assert "cancelled as the user asked" in prep["instructions"]
        assert [line[2] for line in lines if line[0] == f"command:job-{job.id}"][-2:] == ["cancelling", "cancelled"]
        again = await tool(t, "cancel_job").handler({"id": job.id})
        assert again["result"] == "failed" and "already ended (cancelled)" in again["said"]
        gate.set()
        await asyncio.sleep(0.2)
        assert not backend_said(t), "a cancelled job never reports"
    asyncio.run(go())


def backend_said(t) -> bool:
    return any(r.get("kind") == "partner-think" and r.get("state") == "done" for r in t.logged)


# --- follow-ups and the numbered list ---------------------------------------------------------------------------------
def test_words_for_a_running_job_reach_its_backend_at_its_next_step(monkeypatch):
    """"Tell it to count only the middle ones": update_job puts the words on the job, and the real Claude loop gives
    them to the backend with its next step's results, once."""
    gate = asyncio.Event()
    replies = [
        {"stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 5},
         "content": [{"type": "tool_use", "id": "tu1", "name": "count", "input": {}}]},
        {"stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 5},
         "content": [{"type": "tool_use", "id": "tu2", "name": "report",
                      "input": {"say": "Forty in the middle.", "checked": True, "done": True}}]},
    ]
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=replies[len(bodies) - 1])

    async def counting(ctx, args):
        await gate.wait()
        return "341 cells"

    async def go():
        t = await partner_session(monkeypatch, tools={"count": counting})
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        client = httpx.AsyncClient(base_url="https://api.anthropic.com", transport=httpx.MockTransport(handler))
        t.partner.tools.backend = lambda option: thinking.ClaudeThinker(client=client)
        monkeypatch.setattr("research_worker.api_usage.record", lambda *a, **k: None)
        started = await tool(t, "think").handler({"request": "count the cells"})
        await until_async(lambda: t.ran)
        receipt = await tool(t, "update_job").handler({"id": started["job"], "words": "only the ones in the middle"})
        assert receipt["result"] == "verified" and "next step" in receipt["said"]
        gate.set()
        await until_async(lambda: len(bodies) == 2)
        texts = [c.get("text", "") for c in bodies[1]["messages"][-1]["content"] if c.get("type") == "text"]
        assert any("only the ones in the middle" in x for x in texts), texts
        job = t.partner.tools.thinking[started["job"]]
        await until_async(lambda: job.state == "done")
        assert len(bodies) == 2, "taken once: no second run for words it already read"
    asyncio.run(go())


def test_jobs_numbers_think_jobs_and_researchers_together_and_the_same_tools_reach_both(monkeypatch):
    """"How are they doing?", "tell the second one..." and "cancel that" work the same for a researcher and a think job:
    one list, numbered by when each started; the model reads the number off it (no ordinal parser here), and the job's
    id goes to update_job or cancel_job. A researcher's go through its own tools and Hands, with their receipts."""
    gate = asyncio.Event()
    now = time.time()
    r3 = SimpleNamespace(rid="r3", n=3, name="Researcher 3", status="running", created=now - 60, label="the sizes",
                         brief="", step="reading the table", requests=[])
    r4 = SimpleNamespace(rid="r4", n=4, name="Researcher 4", status="running", created=now + 60, label="dividing cells",
                         brief="", step="", requests=[])
    index = SimpleNamespace(session=lambda: "p1", researchers_of=lambda sid: [r3, r4] if sid == "p1" else [])
    monkeypatch.setattr("orchestrator.resources.resources_of", lambda hub: index)

    async def go():
        t = await with_backend(monkeypatch, FakeBackend(wait=gate, report={"say": "Done.", "checked": True,
                                                                           "done": True}),
                               tools={"researcher_note": "passed the note to Researcher 4",
                                      "cancel_researcher": "stopping Researcher 4"})
        started = await tool(t, "think").handler({"request": "compare three outline models"})
        await until_async(lambda: t.partner.tools.thinking[started["job"]].state == "running")
        listed = await tool(t, "jobs").handler({})
        rows = [(r["n"], r["kind"], r["id"], r["state"]) for r in listed["jobs"]]
        assert rows == [(1, "researcher", "r3", "running"), (2, "think", started["job"], "running"),
                        (3, "researcher", "r4", "running")]
        assert "numbered together" in listed["note"].lower()
        told = await tool(t, "update_job").handler({"id": started["job"], "words": "focus on the middle"})
        assert told["result"] == "verified" and t.partner.tools.thinking[started["job"]].task.updates == ["focus on the middle"]
        steered = await tool(t, "update_job").handler({"id": "r4", "words": "keep it short"})
        assert ("researcher_note", {"researcher": "r4", "text": "keep it short"}) in [(n, a) for n, a, _ in t.ran]
        assert steered["result"] in ("verified", "running", "uncertain"), "Hands' receipt from the researcher's record"
        stopped = await tool(t, "cancel_job").handler({"id": "r4"})
        assert ("cancel_researcher", {"researcher": "r4"}) in [(n, a) for n, a, _ in t.ran]
        assert stopped["result"] != "verified", "a researcher is cancelled only when its record says so"
        gate.set()
    asyncio.run(go())


# --- questions and permission ----------------------------------------------------------------------------------------
class Asking:
    """A backend that needs the user: its first run meets Hands' permission and asks; its next run, with the user's
    answer, calls the tool again with the approval it was given."""

    model = "fake-opus"

    def __init__(self):
        self.runs = []

    async def run(self, task, tools, call_tool, progress, system):
        context = task.context()
        self.runs.append((context, {t["name"]: t for t in tools}))
        result = thinking.ThinkResult("", None, model=self.model)
        if len(self.runs) == 1:
            out = await call_tool("highlight", {"things": ["o3"]})
            result.tools.append(thinking.summary_of("highlight", out))
            return thinking.finish(result, {"say": "", "question": out["ask"], "done": False, "checked": False})
        approval = re.search(r"approval (ap-\d+)", context).group(1)
        out = await call_tool("highlight", {"things": ["o3"], "approved": approval})
        result.tools.append(thinking.summary_of("highlight", out))
        return thinking.finish(result, {"say": "It's highlighted.", "done": True, "checked": True})


def test_a_jobs_permission_request_is_its_own_question_and_the_users_yes_comes_back_through_update_job(monkeypatch):
    async def go():
        backend = Asking()
        t = await with_backend(monkeypatch, backend, tools={"highlight": confirmed("highlighted o3")})
        t.hub.screen.report(["w-1: live view"])
        hands = t.partner.tools.hands
        hands.spends = lambda intent: "Highlighting with this model costs about $1" if intent.tool == "highlight" else None
        started = await tool(t, "think").handler({"request": "highlight the bright one with the paid model"})
        job = t.partner.tools.thinking[started["job"]]
        event = await until_async(lambda: t.session.bus.result_for(job.ref()))
        assert event.kind == "job-question" and job.state == "running" and job.asking.startswith("Highlighting")
        facts = json.loads(event.text)
        assert facts["job"] == job.id and facts["permission"]["approval"] == job.approval["approval"]
        assert "approved" in backend.runs[0][1]["highlight"]["parameters"]["properties"], "think's tools take it back"
        assert not [r for r in t.ran if r[0] == "highlight"], "nothing done before the yes"
        prep = await until_async(lambda: prep_of(t))
        assert "question you will ask" in prep["instructions"]
        receipt = await tool(t, "update_job").handler({"id": job.id, "words": "yes, go ahead"})
        assert receipt["result"] == "running" and "your answer" in receipt["said"]
        await until_async(lambda: job.state == "done", seconds=20)
        assert "The user added: yes, go ahead" in backend.runs[1][0] and job.approval["approval"] in backend.runs[1][0]
        assert [r[0] for r in t.ran].count("highlight") == 1 and job.verified is True
        assert t.session.bus.result_for(job.ref()).kind == "job-result" and job.ref() == f"{job.id}.2"
    asyncio.run(go())


# --- done from receipts ----------------------------------------------------------------------------------------------
def test_done_comes_from_the_receipts_never_from_the_backends_own_word(monkeypatch):
    """The backend says done and checked, but Hands could not verify its action: the job ended, not verified, and its
    result says what is not verified; the wording is told the receipt is uncertain. And `checked` is advisory: a
    verified action is done though the backend did not say it checked."""
    async def go():
        backend = FakeBackend(steps=[("highlight", {"things": ["o3"]})],
                              report={"say": "Highlighted.", "checked": True, "done": True})
        t = await with_backend(monkeypatch, backend, tools={"highlight": "highlighted o3"})  # no studio receipt
        t.hub.screen.report(["w-1: live view"])
        started = await tool(t, "think").handler({"request": "highlight the bright one"})
        job = t.partner.tools.thinking[started["job"]]
        await until_async(lambda: job.state == "done", seconds=20)
        assert job.verified is False
        facts = json.loads(t.session.bus.result_for(job.ref()).text)
        assert facts["done"] is False and facts["unverified"][0]["tool"] == "highlight"
        assert facts["unverified"][0]["result"] == "uncertain" and facts["checked"] is True
        prep = await until_async(lambda: prep_of(t))
        assert "receipt is uncertain" in given(prep) and "never say it is done" in prep["instructions"]
        record = next(r for r in t.logged if r["kind"] == "partner-think")
        assert record["ok"] is None and record["done"] is True

        sure = FakeBackend(steps=[("highlight", {"things": ["o3"]})],
                           report={"say": "Highlighted.", "checked": False, "done": True})
        t2 = await with_backend(monkeypatch, sure, tools={"highlight": confirmed("highlighted o3")})
        t2.hub.screen.report(["w-1: live view"])
        started = await tool(t2, "think").handler({"request": "highlight the bright one"})
        job = t2.partner.tools.thinking[started["job"]]
        await until_async(lambda: job.state == "done", seconds=20)
        assert job.verified is True and json.loads(t2.session.bus.result_for(job.ref()).text)["done"] is True
    asyncio.run(go())


# --- across Talk sessions --------------------------------------------------------------------------------------------
def test_a_job_outlives_its_talk_session_its_result_is_owed_on_the_bus_and_the_next_session_knows_it(monkeypatch):
    """Talk ended while the job worked: the job goes on (jobs live on the hub), its result lands on the brain's bus and
    the next session says it; a new partner on the same hub lists the job."""
    from orchestrator.voice.partner import Partner

    gate = asyncio.Event()

    async def go():
        t = await with_backend(monkeypatch, FakeBackend(report={"say": "Cellpose found 342.", "checked": True,
                                                                "done": True}, wait=gate))
        started = await tool(t, "think").handler({"request": "compare three outline models"})
        await t.session.close()
        t.partner.close()
        sidebands = []

        async def open_sideband(call_id):
            sidebands.append(FakeSideband())
            return sidebands[-1]

        async def create_call(offer, config):
            return "answer", "rtc_2"

        async def hangup(call_id):
            pass
        second = RealtimeSession(VoiceConfig(model="gpt-realtime-2.1", system_prompt="be brief"), bus=t.session.bus,
                                 create_call=create_call, open_sideband=open_sideband, hangup=hangup)
        again = Partner(t.hub, t.brain, None, send=lambda m: asyncio.sleep(0), log=t.logged.append)
        again.bind(second)
        await second.start("v=0 offer")
        second._last_user_at = 0.0
        assert started["job"] in again.tools.thinking, "the new Talk session's partner knows the job"
        gate.set()
        prep = await until_async(lambda: next((e["response"] for e in sidebands[0].sent if e["type"] == "response.create"
                                               and e["response"].get("conversation") == "none"), None))
        assert "Cellpose found 342." in prep["input"][0]["content"][0]["text"]
        assert not [e for e in t.sb.sent[-3:] if e.get("type") == "response.create"], "the old session says nothing"
        await second.close()
    asyncio.run(go())


def test_jobs_live_on_the_hub_made_once():
    hub = SimpleNamespace()
    assert jobs_of(hub) is jobs_of(hub) is hub.think_jobs
