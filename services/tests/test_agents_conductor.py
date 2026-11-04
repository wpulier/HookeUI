"""The conductor's rules (ADR 0029): what a launch is given, who answers its asks, how it ends.

The adapter here is a stand-in that says what a test tells it to; the real adapters are tested against recorded streams
(test_agents_adapters.py). Hooke's side (the door, the Project, the Study, the allowance) is a stand-in that records
what was asked of it; test_agents_live_hooke.py runs the real one.
"""
import asyncio
import json
import os
import stat

import pytest

from orchestrator.agents.adapter import HOOKE_MARK, Adapter
from orchestrator.agents.conductor import Conductor, Refused


class Side:
    def __init__(self, *, left=None):
        self.calls, self.lines, self.sent, self.held, self.spends, self.said = [], [], [], {}, [], []
        self.left = left
        self.door = "http://127.0.0.1:9"

    def cli(self): return "/opt/hooke"
    def skill(self): return "/opt/SKILL.md"
    def door_url(self): return self.door
    def today(self): return "2026-11-03"
    def project(self, sid): return sid or "s_open", "Leaf stomata"

    def connect(self, label, kind, launch, project):
        self.calls.append(("connect", label, kind, launch, project))
        return "ac_" + "1" * 24, "hk_local_secret"

    def assign(self, project, launch, title, task, budget):
        self.calls.append(("assign", project, title, task, budget))
        return "p" + "a" * 32, "assignment:" + "b" * 32

    def observe(self, project, study, assignment, connection):
        return {"returned": True, "claimed": True, "claims": 1}

    def end(self, project, connection): self.calls.append(("end", project, connection))
    def allowance_left(self): return self.left
    def hold(self, launch, usd): self.held[launch] = usd
    def release(self, launch): self.held.pop(launch, None)
    def spent(self, launch, usd, billing, model, budget): self.spends.append((launch, usd, billing))
    def activity(self, text, **kw): self.lines.append((text, kw.get("state", "done")))
    def say(self, facts, **kw): self.said.append((facts, kw))
    def broadcast(self, payload): self.sent.append(payload)


class Scripted(Adapter):
    """Says what the test emits; records what Hooke asked of it."""

    program, label = "codex", "Codex"
    made: list = []

    def __init__(self):
        super().__init__(None)
        self.spec, self.sent, self.answers, self.stopped = None, [], [], False
        Scripted.made.append(self)

    async def start(self, spec):
        self.spec = spec
        self.emit("session", session="thread-1", model="gpt-6")
        self.emit("turn", state="working", turn="t1")

    async def send(self, text):
        self.sent.append(text)
        return "steered" if self.busy else "started"

    async def interrupt(self):
        return True

    async def answer(self, permission, allow, *, always=False):
        self.answers.append((permission, allow, always))
        self.permissions.pop(permission, None)
        self.emit("resolved", id=permission, outcome="allowed" if allow else "declined")

    async def stop(self):
        self.stopped = True
        self._finish(0, "stopped")


@pytest.fixture
def made(tmp_path):
    Scripted.made = []
    side = Side()
    conductor = Conductor(tmp_path, side, make_adapter=lambda program, path: Scripted(),
                          find=lambda program: f"/opt/bin/{program}")
    return conductor, side, tmp_path


async def settle(conductor, test, seconds=3.0):
    for _ in range(int(seconds / 0.01)):
        if test():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never happened")


def test_a_launch_gets_its_own_connection_project_and_assignment(made):
    conductor, side, home = made

    async def scenario():
        launch = await conductor.start("codex", "write a stomata-tracking capability", by="voice")
        await settle(conductor, lambda: Scripted.made and Scripted.made[0].spec is not None)
        spec = Scripted.made[0].spec
        assert side.calls[0] == ("connect", "Codex", "codex", launch.id, "s_open")
        assert side.calls[1][0] == "assign" and side.calls[1][3] == "write a stomata-tracking capability"
        # The token goes to that agent alone, in a file only this user can read; never in its environment or argv.
        credential = spec.env["HOOKE_CONNECTION_FILE"]
        assert open(credential).read() == "hk_local_secret"
        assert stat.S_IMODE(os.stat(credential).st_mode) == 0o600
        assert "hk_local_secret" not in json.dumps(spec.env) and "hk_local_secret" not in spec.brief
        assert spec.env["HOOKE_AGENT"] == "codex" and spec.cwd == launch.folder
        assert launch.study in spec.brief and launch.assignment in spec.brief and "/opt/hooke" in spec.brief
        # The researcher's request, relayed as Hooke's words.
        assert spec.brief.startswith(HOOKE_MARK) and "never the researcher's consent" in spec.instructions
        assert launch.transcript[0]["role"] == "hooke" and launch.transcript[0]["text"].startswith(HOOKE_MARK)
        assert side.lines[0][1] == "running"
        await settle(conductor, lambda: launch.status == "working")
        await conductor.close()
    asyncio.run(scenario())


def test_asks_wait_for_the_user_and_are_answered_only_by_answer(made):
    conductor, side, _ = made

    async def scenario():
        launch = await conductor.start("codex", "count the stomata")
        await settle(conductor, lambda: Scripted.made and Scripted.made[0].spec)
        adapter = Scripted.made[0]
        adapter.permissions["codex:0"] = object()
        adapter.emit("permission", id="codex:0", title="Run a command", detail="pip install micro-sam", reason="",
                     always=True)
        await settle(conductor, lambda: launch.status == "needs-you")
        assert launch.line() == "Asks to run a command"
        # Said once by the voice, and only while it still waits (the narrator reads `still` as it speaks).
        facts, how = side.said[-1]
        assert facts["agent"] == "Codex" and facts["what"] == "pip install micro-sam" and how["still"]()
        assert any("asks to run a command" in text and state == "running" for text, state in side.lines)
        await asyncio.sleep(0.05)
        assert adapter.answers == []  # nothing answers it on its own
        # A note is Hooke's, never an answer: the ask still waits.
        await conductor.note(launch.id, "yes, go ahead")
        assert adapter.sent == [f"{HOOKE_MARK} yes, go ahead"] and adapter.answers == []
        await conductor.answer(launch.id, "codex:0", True, always=True)
        assert adapter.answers == [("codex:0", True, True)]
        await settle(conductor, lambda: not launch.asks)
        assert not how["still"]()  # answered: the voice is never told to say it
        with pytest.raises(Refused, match="no longer waiting"):
            await conductor.answer(launch.id, "codex:0", True)
        await conductor.close()
    asyncio.run(scenario())


def test_finishing_observes_the_study_and_stop_revokes(made):
    conductor, side, _ = made

    async def scenario():
        launch = await conductor.start("codex", "count the stomata")
        await settle(conductor, lambda: Scripted.made and Scripted.made[0].spec)
        adapter = Scripted.made[0]
        adapter.emit("text", item="m1", text="Done: 42 stomata.", final=True)
        adapter.emit("turn", state="completed", turn="t1")
        await settle(conductor, lambda: launch.status == "done")
        assert launch.report == {"returned": True, "claimed": True, "claims": 1}  # what the Study says, not the agent
        assert launch.line() == "Report returned for review"
        assert ("end", launch.project, launch.connection) not in side.calls  # still answering replies
        stopped = await conductor.stop(launch.id)
        assert stopped.state == "stopped" and adapter.stopped
        assert ("end", launch.project, launch.connection) in side.calls
        assert not os.path.exists(os.path.join(os.path.dirname(launch.folder), "credential"))
        with pytest.raises(Refused, match="has ended"):
            await conductor.note(launch.id, "one more thing")
    asyncio.run(scenario())


def test_a_program_that_dies_fails_and_is_revoked(made):
    conductor, side, _ = made

    async def scenario():
        launch = await conductor.start("codex", "count")
        await settle(conductor, lambda: Scripted.made and Scripted.made[0].spec)
        Scripted.made[0]._finish(1, "the program ended")
        await settle(conductor, lambda: launch.state == "failed")
        assert ("end", launch.project, launch.connection) in side.calls
        assert any(state == "failed" for _, state in side.lines)
    asyncio.run(scenario())


def test_restart_never_says_an_agent_is_still_working(made, tmp_path):
    conductor, side, home = made

    async def scenario():
        launch = await conductor.start("codex", "count")
        await settle(conductor, lambda: launch.status == "working")
        conductor._save()
        return launch
    launch = asyncio.run(scenario())
    later_side = Side()
    later = Conductor(home, later_side, make_adapter=lambda p, path: Scripted(), find=lambda p: None)
    kept = later.launches[launch.id]
    assert kept.state == "stopped" and "Hooke closed" in kept.why and not kept.asks
    assert later_side.calls == []  # nothing until the door is there
    later.recover()
    assert ("end", launch.project, launch.connection) in later_side.calls  # its connection withdrawn at start


def test_refusals_say_what_to_do(made, tmp_path):
    conductor, side, _ = made

    async def scenario():
        with pytest.raises(Refused, match="Say what the agent should do"):
            await conductor.start("codex", "  ")
        with pytest.raises(Refused, match="can't start"):
            await conductor.start("cursor", "count")
        missing = Conductor(tmp_path / "other", Side(), make_adapter=lambda p, path: Scripted(), find=lambda p: None)
        with pytest.raises(Refused, match="isn't installed") as refused:
            await missing.start("grok", "count")
        assert "sign in" in refused.value.next
        assert side.calls == []  # nothing was made for a refused launch
    asyncio.run(scenario())


def test_claude_is_held_to_the_allowance_and_its_spend_counts(tmp_path):
    Scripted.made = []  # not the `made` fixture: an earlier test's adapter would otherwise answer the settle below
    side = Side(left=1.2)
    conductor = Conductor(tmp_path, side, make_adapter=lambda p, path: Scripted(), find=lambda p: "/opt/bin/claude")

    async def scenario():
        launch = await conductor.start("claude-code", "read ten papers")
        assert launch.budget_usd == 1.2 and side.held[launch.id] == 1.2
        await settle(conductor, lambda: Scripted.made and Scripted.made[-1].spec)
        assert Scripted.made[-1].spec.budget_usd == 1.2
        Scripted.made[-1].emit("usage", costUsd=0.31, tokens=900)
        await settle(conductor, lambda: launch.cost_usd == 0.31)
        assert conductor.spent_on("2026-11-03") == 0.31
        await conductor.stop(launch.id)
        assert launch.id not in side.held
        broke = Conductor(tmp_path / "b", Side(left=0.1), make_adapter=lambda p, path: Scripted(),
                          find=lambda p: "/opt/bin/claude")
        with pytest.raises(Refused, match="allowance"):
            await broke.start("claude-code", "read")
    asyncio.run(scenario())


def test_at_most_four_work_at_once(made):
    conductor, side, _ = made

    async def scenario():
        for n in range(4):
            await conductor.start("codex", f"task {n}")
        with pytest.raises(Refused, match="already working"):
            await conductor.start("codex", "a fifth")
        await conductor.close()
        assert all(r.state == "stopped" for r in conductor.launches.values())
    asyncio.run(scenario())


def test_find_one_by_the_users_words(made):
    conductor, side, _ = made

    async def scenario():
        launch = await conductor.start("codex", "count")
        assert conductor.find_one("codex").id == launch.id
        assert conductor.find_one(None).id == launch.id
        with pytest.raises(Refused, match="No agent called Grok"):
            conductor.find_one("Grok")
        await conductor.close()
    asyncio.run(scenario())


def test_list_messages_carry_a_peek_not_the_whole_transcript(made):
    conductor, side, _ = made

    async def scenario():
        launch = await conductor.start("codex", "count")
        await settle(conductor, lambda: Scripted.made and Scripted.made[0].spec)
        for n in range(20):
            Scripted.made[0].emit("text", item=f"m{n}", text=f"step {n}", final=False)
        await settle(conductor, lambda: len(launch.transcript) >= 21)
        await settle(conductor, lambda: side.sent and side.sent[-1]["agents"][0]["transcript"][-1]["text"] == "step 19")
        row = side.sent[-1]["agents"][0]
        assert side.sent[-1]["type"] == "agents" and len(row["transcript"]) == 6 and row["folder"] is None
        assert len(launch.wire(full=True)["transcript"]) == 21
        await conductor.close()
    asyncio.run(scenario())
