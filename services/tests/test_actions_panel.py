"""The Actions panel, the server's half (docs/design/2026-10-13-actions-panel.md): the studio command `action` runs one of
the agent's capabilities by the user's hand, through the tool the agent would call, with a model chosen for that run
only through the agent's own `use`; `GET /api/actions` gives the tools' argument schemas the panel's forms are built
from."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.commands import handle
from orchestrator.paths import Paths
from orchestrator.server import create_app
from tests.test_activity import Client, app, lines, outline, run
from tests.test_capability_matrix import world  # noqa: F401 - the matrix's world: keys, code and the cloud stood in for

ROOT = Path(__file__).resolve().parents[2]


def reply(client: Client) -> dict:
    return next(m for m in reversed(client.sent) if "ok" in m)


def test_comparison_action_returns_pinned_summaries_without_copying_scientific_geometry(tmp_path, monkeypatch):
    from orchestrator.brain.registry import attach
    from orchestrator.commands import run_tool

    built, _ = app(tmp_path)
    async def compare(ctx, args):
        attach(values={"comparison": {"id":"trial", "operation":"cells", "capture":1, "pairs":[],
            "arms":[{"impl":"a", "label":"A", "population":"e1", "revision":2, "count":12000,
                     "boxes":[[0, 0, 1, 1]] * 12000, "polygons":[[[0, 0], [1, 0], [1, 1]]] * 12000}]}})
        return "Compared"
    monkeypatch.setattr(built.brain.registry.tools["compare_models"], "handler", compare)
    result = run(run_tool(built, "compare_models", {}))
    assert result["ok"]
    assert result["comparison"]["arms"] == [{"impl":"a", "label":"A", "population":"e1", "revision":2, "count":12000}]
    assert len(json.dumps(result)) < 1024


def test_an_action_runs_the_agents_tool_and_is_said_as_the_users(tmp_path):
    built, sent = app(tmp_path)
    obj = outline(built)
    client = Client()
    run(handle(built, client, {"type": "action", "capability": "clear", "args": {}}))
    answered = reply(client)
    assert answered["ok"] and answered["title"] == "Clear outlines"
    assert obj.id not in built.hub.scene.objects  # the tool the agent would call did it
    said = lines(sent)[-1]
    assert said["text"].startswith("You ran “Clear outlines”") and said["state"] != "failed"


def test_an_action_that_is_not_the_agents_or_not_here_is_refused(tmp_path):
    built, sent = app(tmp_path)
    for cid in ("no-such-action", "camera-checklist"):  # unknown; a capability of the user's own controls (no tool)
        client = Client()
        run(handle(built, client, {"type": "action", "capability": cid, "args": {}}))
        answered = reply(client)
        assert answered["ok"] is False and "there is no action" in answered["error"]
    client = Client()
    run(handle(built, client, {"type": "action", "capability": "clear", "args": ["not", "fields"]}))
    assert reply(client)["ok"] is False


def test_run_with_a_model_that_means_nothing_is_refused_and_nothing_runs_in_its_place(tmp_path):
    built, sent = app(tmp_path)
    obj = outline(built)
    client = Client()
    run(handle(built, client, {"type": "action", "capability": "clear", "args": {}, "use": {"outline": "no-such-model"}}))
    answered = reply(client)
    assert answered["ok"] is False and answered["error"]
    assert obj.id in built.hub.scene.objects  # the action itself never ran
    assert lines(sent)[-1]["state"] == "failed"


def test_run_with_a_model_that_can_run_is_chosen_by_the_user_for_that_run_only(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")  # a key is present: the model can run here
    built, sent = app(tmp_path)
    before = built.hub.settings.vision
    client = Client()
    run(handle(built, client, {"type": "action", "capability": "clear", "args": {}, "use": {"vision": "gemini-3.5-flash"}}))
    assert reply(client)["ok"], reply(client)
    assert built.hub.settings.vision == before  # a choice for that run, never a default


@pytest.mark.parametrize('role', ['embed', 'looks'])
def test_local_embedding_choice_uses_canonical_operation_without_a_chat_key(tmp_path, monkeypatch, role):
    from tests.capability_rig import Rig
    for key in ('GEMINI_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    built = Rig(tmp_path).app
    built.hub.settings.update({'embed': 'bioclip-2'})
    obj = outline(built)
    client = Client()
    run(handle(built, client, {'type': 'action', 'capability': 'clear', 'args': {}, 'use': {role: 'dinov2-small'}}))
    assert reply(client)['ok'], json.dumps(reply(client))
    assert obj.id not in built.hub.scene.objects
    assert built.hub.settings.embed == 'bioclip-2'


def test_the_route_lists_every_tool_with_its_arguments_kind_and_family(tmp_path):
    client = TestClient(create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path)))
    with client:
        tools = client.get("/api/actions").json()["tools"]
    assert tools["clear"]["kind"] == "effect" and tools["clear"]["family"] == "scene"
    assert tools["count"]["params"]["type"] == "object" and "properties" in tools["count"]["params"]
    assert {"find", "highlight", "enhance", "snapshot"} <= set(tools)


def test_a_setting_an_action_changes_is_the_users_in_activity(tmp_path):
    built, sent = app(tmp_path)
    client = Client()
    run(handle(built, client, {"type": "action", "capability": "objective", "args": {"objective": 40}}))
    assert reply(client)["ok"], reply(client)
    said = [m["text"] for m in lines(sent)]
    assert any(t.startswith("You chose 40×") and t.endswith("(Actions)") for t in said), said
    assert not any(t.startswith("The app set") for t in said), said
    assert said[-1].startswith("You ran “Set the objective”")


# --- every action the panel can offer reaches its tool --------------------------------------------------------------
# The panel's rows (actions-panel.ts actionRows) are the agent's capabilities with a tool, in contract/actions.json's
# families, less its agent-only ones and those whose tool needs a list or an object only words give. Each is run once
# through the `action` command in the capability matrix's world (tests/capability_rig.py: the real app over stand-in
# workers and models), on a view with an outline selected: it must start the tool its capability names, never be
# refused as no action. Whether the tool then finds what it looks for in the stand-in world is the matrix's to check.
SAMPLES = {"what": "stoma", "label": "stoma", "name": "guard cell", "subject": "stoma", "objective": 40, "micrometres": 100,
           "seconds": 4, "minutes": 1, "groups": 2, "by": "motion", "change": "make the outline yellow", "level": "more"}
CHOICES = re.compile(r"^\s*[\w-]+(?:\s*\|\s*[\w-]+)+\s*$")  # a description that lists its choices: "motion | appears | look"
PLACEHOLDER = re.compile(r"^(?:[a-z]{1,2}\d+|that)$")


def offered(registry) -> list[tuple[dict, dict]]:
    """(capability, args) for every row the panel offers, its required fields filled and its target the outline o1."""
    contract = json.loads((ROOT / "contract" / "actions.json").read_text())
    families = {g["family"] for g in contract["groups"]}
    rows = []
    for cap in json.loads((ROOT / "contract" / "capabilities.json").read_text())["capabilities"]:
        tool = registry.tools.get((cap.get("how") or {}).get("tool") or "")
        if cap["actor"] != "agent" or tool is None or cap["family"] not in families or cap["id"] in contract["agentOnly"]["ids"]:
            continue
        props, required = tool.params.get("properties", {}), set(tool.params.get("required", []))
        presets = (cap.get("how") or {}).get("args") or {}
        if any(k in required and k not in presets and props[k].get("type") in ("array", "object") for k in props):
            continue  # the agent's: a list or an object said in words
        target = next((k for k, v in presets.items() if isinstance(v, str) and PLACEHOLDER.match(v)), None)
        target = target or next((k for k in ("object", "a") if k in props and k not in presets), None)
        args = {k: SAMPLES.get(k, "stoma") for k in required if k not in presets and k != target}
        if target:
            args[target] = "o1"
        rows.append((cap, args))
    return rows


def test_every_action_the_panel_offers_reaches_its_tool(world):
    from tests import capability_rig as setups
    from tests.capability_rig import Rig

    async def one(n: int, cap: dict, args: dict) -> str:
        rig = Rig(world["root"] / f"a{n}")
        await setups.prepare(rig, ("outline", "still"))
        before = len(rig.runs)
        client = Client()
        await asyncio.wait_for(handle(rig.app, client, {"type": "action", "capability": cap["id"], "args": args}), 30)
        answered = reply(client)
        ran = [r.tool for r, _ in rig.runs[before:]]
        if "there is no action" in str(answered.get("error")):
            return f"{cap['id']}: refused as no action"
        if cap["how"]["tool"] not in ran:
            return f"{cap['id']}: its tool {cap['how']['tool']} never started ({answered.get('error') or answered})"
        return ""

    probe = Rig(world["root"] / "probe")
    rows = offered(probe.app.brain.registry)
    assert len(rows) >= 30, [c["id"] for c, _ in rows]  # the panel offers the view's work, not a handful
    problems = [p for n, (cap, args) in enumerate(rows) if (p := asyncio.run(one(n, cap, args)))]
    assert not problems, "\n".join(problems)


# --- Stop (issue 40: capability safety and recovery) ----------------------------------------------------------------
def slow_action(built, monkeypatch, seen: list):
    """A capability of the agent's whose tool works for a while and then outlines something: what comes after the Stop."""
    from orchestrator import capabilities
    from orchestrator.brain.registry import CURRENT_RUN, Tool

    async def handler(ctx, args):
        seen.append(CURRENT_RUN.get())
        await asyncio.sleep(0.4)
        outline(built, "made after the Stop")
        return "outlined it"

    built.brain.registry.add(Tool("slow_test", "works a while, then outlines", {"type": "object", "properties": {}},
                                  handler, family="scene"))
    real = capabilities.load
    monkeypatch.setattr(capabilities, "load", lambda *a, **k: {**real(*a, **k), "capabilities": [
        {"id": "slow-thing", "actor": "agent", "title": "Slow thing", "how": {"tool": "slow_test"}}]})


def test_stop_ends_exactly_that_running_action_and_activity_says_it_was_stopped(tmp_path, monkeypatch):
    built, sent = app(tmp_path)
    seen = []
    slow_action(built, monkeypatch, seen)
    mine, other = Client(), Client()

    async def go():
        running = asyncio.ensure_future(handle(built, mine, {"type": "action", "requestId": "a", "capability": "slow-thing",
                                                             "args": {}, "run": "run-1"}))
        await asyncio.sleep(0.05)
        refusals = []
        for who, run_id in ((other, "run-1"), (mine, "no-such-run")):
            await handle(built, who, {"type": "action-cancel", "requestId": "c", "run": run_id})
            refusals.append(reply(who))
        await handle(built, mine, {"type": "action-cancel", "requestId": "s", "run": "run-1"})
        stopped = next(m for m in mine.sent if m.get("requestId") == "s")
        await asyncio.wait_for(running, 5)
        await asyncio.sleep(0.5)  # past the tool's own work: nothing after the Stop is applied
        return refusals, stopped

    (by_other, unknown), stopped = run(go())
    assert by_other["ok"] is False and "another window" in by_other["error"]
    assert unknown["ok"] is False and "not running" in unknown["error"]
    assert stopped["ok"] is True
    answered = next(m for m in mine.sent if m.get("requestId") == "a")
    assert answered["ok"] is True and answered["stopped"] is True
    assert not any(o.label == "made after the Stop" for o in built.hub.scene.objects.values())
    assert seen[0].stopped and seen[0].ok is False and seen[0].wire()["stopped"] is True
    said = lines(sent)[-1]
    assert said["text"] == "Stopped: “Slow thing” (you pressed Stop)" and said["state"] == "done"  # never a failure
    after = Client()  # its run is over: nothing left to stop
    run(handle(built, after, {"type": "action-cancel", "requestId": "late", "run": "run-1"}))
    assert reply(after)["ok"] is False


def test_an_action_nobody_stopped_runs_to_its_end_with_its_run_id(tmp_path, monkeypatch):
    built, sent = app(tmp_path)
    seen = []
    slow_action(built, monkeypatch, seen)
    client = Client()
    run(handle(built, client, {"type": "action", "requestId": "a", "capability": "slow-thing", "args": {}, "run": "run-2"}))
    answered = reply(client)
    assert answered["ok"] and not answered.get("stopped") and not seen[0].stopped
    assert any(o.label == "made after the Stop" for o in built.hub.scene.objects.values())
    assert lines(sent)[-1]["text"].startswith("You ran “Slow thing”")


def test_a_stop_whose_work_has_not_ended_is_said_stopping_until_it_ends(tmp_path, monkeypatch):
    """Codex's pre-review of 36a0237b, 1: work still ending past operations.STOP_WAIT_S is never reported stopped; the
    action's reply and its Activity line say stopping, and the line becomes how it ended once it does."""
    from orchestrator import capabilities, operations
    from orchestrator.brain.registry import Tool
    from orchestrator.operations import Runner
    from tests.test_operations import CLICK, SlowToStop, hub, view

    monkeypatch.setattr(operations, "STOP_WAIT_S", 0.05)
    built, sent = app(tmp_path)
    runner = Runner(hub(SlowToStop(delay=1.0, ending=0.3)))

    async def handler(ctx, args):
        await runner.run("outline", view(), CLICK)
        outline(built, "made after the Stop")
        return "outlined it"

    built.brain.registry.add(Tool("slow_job", "runs one job", {"type": "object", "properties": {}}, handler, family="scene"))
    real = capabilities.load
    monkeypatch.setattr(capabilities, "load", lambda *a, **k: {**real(*a, **k), "capabilities": [
        {"id": "slow-job", "actor": "agent", "title": "Slow job", "how": {"tool": "slow_job"}}]})
    client = Client()

    async def go():
        running = asyncio.ensure_future(handle(built, client, {"type": "action", "requestId": "a", "capability": "slow-job",
                                                               "args": {}, "run": "run-3"}))
        await asyncio.sleep(0.05)
        await handle(built, client, {"type": "action-cancel", "requestId": "s", "run": "run-3"})
        await asyncio.wait_for(running, 5)
        answered = next(m for m in client.sent if m.get("requestId") == "a")
        then = lines(sent)[-1]
        await asyncio.sleep(0.5)  # its work ends (its own cancellation takes 0.3 s)
        return answered, then

    answered, then = run(go())
    assert answered["ok"] and answered["stopping"] and not answered["stopped"]
    assert then["text"] == "Stopping: “Slow job” hasn't ended yet (you pressed Stop)" and then["state"] == "running"
    now = [m for m in lines(sent) if m.get("id") == then["id"]][-1]
    assert now["text"] == "Stopped: “Slow job” (you pressed Stop)" and now["state"] == "done"
    assert not any(o.label == "made after the Stop" for o in built.hub.scene.objects.values())
    settled = [m for m in client.sent if m.get("type") == "action-settled"]
    # Codex's review of 8bd09b6a, 2: the studio that started it is told how that exact run ended, once.
    assert settled == [{"type": "action-settled", "run": "run-3", "outcome": "stopped",
                        "text": "Stopped: “Slow job” (you pressed Stop)",
                        "said": settled[0]["said"]}]
    # What the Stop did, once its late work ended (user_stop.report, L7b): the row and Activity's details say it.
    assert settled[0]["said"].startswith("Stopped “Slow job” (you pressed Stop).") and "Kept: none recorded." in settled[0]["said"]
    assert settled[0]["said"] in now["details"]["lines"]


def test_a_rerun_right_after_a_stopping_reply_runs_fresh_and_every_action_gets_its_reply(tmp_path, monkeypatch):
    """Codex's review of 8bd09b6a, 1: the same action run again while its stopped job is still ending never joins that
    dying job (it would inherit its cancellation and the studio would get no reply): it runs a fresh job to its end."""
    from orchestrator import capabilities, operations
    from orchestrator.brain.registry import Tool
    from orchestrator.operations import Runner
    from tests.test_operations import CLICK, SlowToStop, hub, view

    monkeypatch.setattr(operations, "STOP_WAIT_S", 0.05)
    built, sent = app(tmp_path)
    gpu = SlowToStop(delay=0.3, ending=0.3)
    runner = Runner(hub(gpu))

    async def handler(ctx, args):
        await runner.run("outline", view(), CLICK)
        outline(built, "made by a run")
        return "outlined it"

    built.brain.registry.add(Tool("slow_job", "runs one job", {"type": "object", "properties": {}}, handler, family="scene"))
    real = capabilities.load
    monkeypatch.setattr(capabilities, "load", lambda *a, **k: {**real(*a, **k), "capabilities": [
        {"id": "slow-job", "actor": "agent", "title": "Slow job", "how": {"tool": "slow_job"}}]})
    client = Client()

    async def go():
        first = asyncio.ensure_future(handle(built, client, {"type": "action", "requestId": "a", "capability": "slow-job",
                                                             "args": {}, "run": "run-4"}))
        await asyncio.sleep(0.05)
        await handle(built, client, {"type": "action-cancel", "requestId": "s", "run": "run-4"})
        await asyncio.wait_for(first, 5)
        dying = set(runner.stopping)  # its cleanup is still pending
        await asyncio.wait_for(handle(built, client, {"type": "action", "requestId": "b", "capability": "slow-job",
                                                      "args": {}, "run": "run-5"}), 5)
        await asyncio.sleep(0.4)
        return dying

    dying = run(go())
    replies = {m["requestId"]: m for m in client.sent if m.get("type") == "result" and m.get("for") == "action"}
    assert dying and set(replies) == {"a", "b"}  # every action got its reply
    assert replies["a"]["stopping"] and replies["b"]["ok"] and not replies["b"].get("stopped")
    assert len(gpu.calls) == 2 and gpu.finished == 1  # the rerun ran a fresh job, to its end
    assert [o.label for o in built.hub.scene.objects.values()].count("made by a run") == 1


def test_an_action_whose_work_is_cancelled_from_inside_without_a_stop_still_gets_a_reply(tmp_path, monkeypatch):
    """Codex's review of 8bd09b6a, 1: a cancellation the user did not ask for (work it waited on was cancelled) is a
    terminal reply in words, never a studio left waiting."""
    from orchestrator import capabilities
    from orchestrator.brain.registry import Tool

    built, sent = app(tmp_path)

    async def handler(ctx, args):
        raise asyncio.CancelledError  # what it waited on was cancelled, not by the user

    built.brain.registry.add(Tool("cut_job", "cut off", {"type": "object", "properties": {}}, handler, family="scene"))
    real = capabilities.load
    monkeypatch.setattr(capabilities, "load", lambda *a, **k: {**real(*a, **k), "capabilities": [
        {"id": "cut-job", "actor": "agent", "title": "Cut job", "how": {"tool": "cut_job"}}]})
    client = Client()
    run(handle(built, client, {"type": "action", "requestId": "a", "capability": "cut-job", "args": {}, "run": "run-6"}))
    answered = reply(client)
    assert answered["ok"] is False and "stopped before it finished" in answered["error"]
    assert lines(sent)[-1]["state"] == "failed"


def test_a_late_stops_lines_are_filed_where_the_action_was_asked_after_a_switch_and_an_eviction(tmp_path, monkeypatch):
    """The Standards review of 8bd09b6a: the "Stopping" line and how it ended are filed in the session the action was
    asked in, by its own `asked`, even after a session switch and after Activity's recent-line cache let its line go."""
    from orchestrator import activity as activities, capabilities, operations
    from orchestrator.brain.registry import Tool
    from orchestrator.operations import Runner
    from tests.test_operations import CLICK, SlowToStop, hub, view

    monkeypatch.setattr(operations, "STOP_WAIT_S", 0.05)
    built, sent = app(tmp_path)
    runner = Runner(hub(SlowToStop(delay=2.0, ending=0.6)))

    async def handler(ctx, args):
        await runner.run("outline", view(), CLICK)
        return "outlined it"

    built.brain.registry.add(Tool("slow_job", "runs one job", {"type": "object", "properties": {}}, handler, family="scene"))
    real = capabilities.load
    monkeypatch.setattr(capabilities, "load", lambda *a, **k: {**real(*a, **k), "capabilities": [
        {"id": "slow-job", "actor": "agent", "title": "Slow job", "how": {"tool": "slow_job"}}]})
    filed = []
    show = built.hub.activities.dialogue.show
    monkeypatch.setattr(built.hub.activities.dialogue, "show", lambda line, asked=None, rid=None: (
        filed.append((line.get("id"), getattr(asked, "session", None))), show(line, asked=asked, rid=rid))[1])
    client = Client()
    original = built.day.sid

    async def go():
        running = asyncio.ensure_future(handle(built, client, {"type": "action", "requestId": "a", "capability": "slow-job",
                                                               "args": {}, "run": "run-7"}))
        await asyncio.sleep(0.05)
        await handle(built, client, {"type": "action-cancel", "requestId": "s", "run": "run-7"})
        await asyncio.wait_for(running, 5)
        await handle(built, client, {"type": "session", "requestId": "n", "action": "new"})
        for i in range(activities.KEPT + 10):  # its line leaves Activity's recent-line cache
            activities.activity(built.hub, "command", f"filler {i}")
        assert "action-stop:run-7" not in built.hub.activities.recent
        newer = built.day.sid
        await asyncio.sleep(0.8)  # its work ends (0.6 s)
        return newer

    newer = run(go())
    assert newer != original
    mine = [session for line, session in filed if line == "action-stop:run-7"]
    assert len(mine) == 2 and set(mine) == {original}  # "Stopping", then how it ended: both where it was asked
    assert any(m.get("type") == "action-settled" and m["run"] == "run-7" for m in client.sent)


def _ends_within_the_wait(how: str):
    """Begun work that ends 0.3 s after the user's Stop, within operations.STOP_WAIT_S: cancelled ("stopped"), finished
    on its own ("completed") or with an error ("failed")."""
    from tests.test_operations import Begun

    class Ending(Begun):
        async def call(self, op, timeout=30.0, priority=None, **payload):
            try:
                return await super().call(op, timeout, priority, **payload)
            except asyncio.CancelledError:
                await asyncio.sleep(0.3)
                if how == "completed":
                    return {"polygons": []}
                if how == "failed":
                    raise RuntimeError("its child would not end")
                raise

    return Ending(delay=2.0)


@pytest.mark.parametrize("how, said, state", [
    ("stopped", "Stopped: “Slow job” (you pressed Stop)", "done"),
    ("completed", "“Slow job” ended on its own after you pressed Stop: nothing it made was used", "done"),
    ("failed", "You pressed Stop on “Slow job”: it didn't work (its work did not stop cleanly)", "failed"),
])
def test_a_stop_that_ends_within_the_wait_is_filed_where_the_action_was_asked_after_a_switch(tmp_path, monkeypatch,
                                                                                              how, said, state):
    """The Standards re-review of 1cc3db12: a Stop whose work ends within STOP_WAIT_S is said with how it really ended,
    and its one line is filed in the session the action was asked in, even when the user opened another session during
    the Stop's cleanup; the session opened since gets none."""
    from orchestrator import capabilities
    from orchestrator.brain.registry import Tool
    from orchestrator.operations import Runner
    from tests.test_operations import CLICK, hub, view

    built, sent = app(tmp_path)
    runner = Runner(hub(_ends_within_the_wait(how)))

    async def handler(ctx, args):
        await runner.run("outline", view(), CLICK)
        outline(built, "made after the Stop")
        return "outlined it"

    built.brain.registry.add(Tool("slow_job", "runs one job", {"type": "object", "properties": {}}, handler, family="scene"))
    real = capabilities.load
    monkeypatch.setattr(capabilities, "load", lambda *a, **k: {**real(*a, **k), "capabilities": [
        {"id": "slow-job", "actor": "agent", "title": "Slow job", "how": {"tool": "slow_job"}}]})
    filed = []
    show = built.hub.activities.dialogue.show
    monkeypatch.setattr(built.hub.activities.dialogue, "show", lambda line, asked=None, rid=None: (
        filed.append((line.get("text"), line.get("state"), getattr(asked, "session", None))),
        show(line, asked=asked, rid=rid))[1])
    client = Client()
    original = built.day.sid

    async def go():
        running = asyncio.ensure_future(handle(built, client, {"type": "action", "requestId": "a", "capability": "slow-job",
                                                               "args": {}, "run": "run-8"}))
        await asyncio.sleep(0.05)
        await handle(built, client, {"type": "action-cancel", "requestId": "s", "run": "run-8"})
        await handle(built, client, {"type": "session", "requestId": "n", "action": "new"})  # during its 0.3 s cleanup
        newer = built.day.sid
        assert not running.done()  # the switch came before the Stop's cleanup ended
        await asyncio.wait_for(running, 5)
        return newer

    newer = run(go())
    assert newer != original
    answered = next(m for m in client.sent if m.get("requestId") == "a")
    assert answered["outcome"] == how and not answered.get("stopping")
    assert not any(m.get("type") == "action-settled" for m in client.sent)  # settled within the wait: the reply says it
    mine = [(text, st, session) for text, st, session in filed if "Slow job" in (text or "")]
    assert mine == [(said, state, original)]  # one line, how it ended, where it was asked; none in the session opened since
    assert not any(o.label == "made after the Stop" for o in built.hub.scene.objects.values())


def test_close_magnifier_keeps_legacy_action_targeting(tmp_path):
    built, _ = app(tmp_path)
    client = Client()
    run(handle(built, client, {"type": "action", "capability": "close-magnifier", "args": {}}))
    assert reply(client)["ok"], reply(client)


def test_the_same_action_twice_at_once_runs_once_and_other_work_runs_beside_it(tmp_path, monkeypatch):
    """L7b (ticket 62 row 12, F094): another window (or this one, reloaded mid-run) pressing Run on the same action,
    settings and input while it runs is refused before anything starts; other settings or actions run beside it."""
    built, sent = app(tmp_path)
    seen = []
    slow_action(built, monkeypatch, seen)
    first, second = Client(), Client()

    async def go():
        running = asyncio.ensure_future(handle(built, first, {"type": "action", "requestId": "a", "capability": "slow-thing",
                                                              "args": {}, "run": "run-a"}))
        await asyncio.sleep(0.05)
        await handle(built, second, {"type": "action", "requestId": "b", "capability": "slow-thing", "args": {},
                                     "run": "run-b"})
        refused = reply(second)
        await asyncio.wait_for(running, 5)
        await handle(built, second, {"type": "action", "requestId": "c", "capability": "slow-thing", "args": {},
                                     "run": "run-c"})
        return refused, reply(second)

    refused, again = run(go())
    assert refused["ok"] is False and "already running" in refused["error"] and "not started twice" in refused["error"]
    assert len(seen) == 2, "the refused one never started; once the first ended, the same action ran"
    assert again["ok"] is True


def test_a_stop_says_what_stopped_what_was_kept_and_what_was_discarded(tmp_path, monkeypatch):
    built, sent = app(tmp_path)
    seen = []
    slow_action(built, monkeypatch, seen)
    client = Client()

    async def go():
        running = asyncio.ensure_future(handle(built, client, {"type": "action", "requestId": "a",
                                                               "capability": "slow-thing", "args": {}, "run": "run-s"}))
        await asyncio.sleep(0.05)
        await handle(built, client, {"type": "action-cancel", "requestId": "s", "run": "run-s"})
        await asyncio.wait_for(running, 5)

    run(go())
    answered = next(m for m in client.sent if m.get("requestId") == "a")
    assert answered["stopped"] is True
    assert answered["summary"].startswith("Stopped “Slow thing” (you pressed Stop)")
    assert answered["outcome"] == "stopped" and answered["stop"]["stopped"] == ["“Slow thing”: stopped"]
    assert answered["summary"].endswith("Kept: none recorded. Nothing of its unfinished work was applied.")


def test_a_stop_of_work_another_request_shares_says_it_carries_on(tmp_path, monkeypatch):
    """Contract 4: a job another request still waits for is not ended by one request's Stop; that Stop says so."""
    from orchestrator import capabilities
    from orchestrator.brain.registry import Tool
    from orchestrator.operations import Runner
    from tests.test_operations import CLICK, Begun, hub, view

    built, sent = app(tmp_path)
    gpu = Begun(delay=0.6)
    runner = Runner(hub(gpu))
    shared_view = view()

    async def handler(ctx, args):
        await runner.run("outline", shared_view, CLICK)
        return "outlined it"

    for name in ("job_one", "job_two"):
        built.brain.registry.add(Tool(name, "runs the shared job", {"type": "object", "properties": {}}, handler,
                                      family="scene"))
    real = capabilities.load
    monkeypatch.setattr(capabilities, "load", lambda *a, **k: {**real(*a, **k), "capabilities": [
        {"id": "one", "actor": "agent", "title": "One", "how": {"tool": "job_one"}},
        {"id": "two", "actor": "agent", "title": "Two", "how": {"tool": "job_two"}}]})
    client = Client()

    async def go():
        one = asyncio.ensure_future(handle(built, client, {"type": "action", "requestId": "a", "capability": "one",
                                                           "args": {}, "run": "run-one"}))
        two = asyncio.ensure_future(handle(built, client, {"type": "action", "requestId": "b", "capability": "two",
                                                           "args": {}, "run": "run-two"}))
        await asyncio.sleep(0.1)
        await handle(built, client, {"type": "action-cancel", "requestId": "s", "run": "run-one"})
        await asyncio.wait_for(asyncio.gather(one, two), 5)

    run(go())
    stopped = next(m for m in client.sent if m.get("requestId") == "a")
    finished = next(m for m in client.sent if m.get("requestId") == "b")
    assert stopped["stop"]["outcome"] == "nothing" and stopped["stop"]["carriesOn"]
    assert stopped["stopped"] is False and stopped["outcome"] == "nothing", "never said stopped: its work carries on"
    assert "its work was not ended" in stopped["summary"] and "another request" in stopped["summary"]
    filed = [line["text"] for line in lines(sent)]
    assert not any(text.startswith("Stopped: “One”") for text in filed), filed
    assert any(text.startswith("Stopped waiting for “One”") for text in filed), filed
    assert finished["ok"] is True and not finished.get("stopped")
    assert len(gpu.calls) == 1, "one shared job, never ended by the Stop"


def test_the_same_action_with_another_model_is_other_work(tmp_path, monkeypatch):
    """Review of #365: the run's models are part of what makes two runs the same, from its "Run with" choice or the
    current setting; the same action with another model runs beside the first."""
    from orchestrator import commands

    built, sent = app(tmp_path)
    row = {"roles": ["cells"]}
    monkeypatch.setattr(built.hub.settings, "to_wire", lambda: {"cells": "cellpose-sam"})
    assert commands._run_models(built, row, {}) == {"cells": "cellpose-sam"}
    assert commands._run_models(built, row, {"cells": "instanseg-brightfield"}) == {"cells": "instanseg-brightfield"}
    monkeypatch.setattr(built.hub.settings, "to_wire", lambda: {"cells": "basic-bright-objects"})
    assert commands._run_models(built, row, {}) == {"cells": "basic-bright-objects"}, "a changed setting is other work"
