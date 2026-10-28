"""The app's own check of each model (orchestrator/model_checks.py; the user's Q8 and Q20 of 2026-10-11, Slice 8):
every model the capabilities list names is an entry; a check runs each on this Mac once, through the runner and the
workers, at the lowest priority, letting go of what it loaded; what can't run is said without running it; the last
check is on the capabilities wire beside the user's verdict, told to the agent when broken, and `use`/`set_default`
refuse a model broken on this build. The real models' check is bench/capability_check.py (make capability-check)."""
import asyncio
import json

import cv2
import numpy as np
import pytest

from orchestrator import capabilities, model_checks, names
from orchestrator.model_checks import Checker, Checks, before, entries
from orchestrator.options import GROUPS
from tests.standin_workers import build_app

LOCAL = [e for e in entries() if e.where == "mac"]


def test_every_model_the_list_names_is_an_entry_with_a_probe_and_one_name():
    """Every option of a model role that runs a model, every model fixed in the code (and its fallback), and each method
    option that runs one of its own: one entry each, run by a probe the checker has, named as names.py means it."""
    cat = capabilities.load()
    keys = {e.key for e in entries()}
    for role in cat["roles"]:
        if role["id"].startswith("fixed:"):
            assert f"{role['id']}:model" in keys, role["id"]
            if role.get("fallback"):
                assert f"{role['id']}:fallback" in keys
        elif role["kind"] == "model":
            for option in GROUPS[role["id"]].values():
                if capabilities.runs(option.uses):
                    assert f"{role['id']}:{option.id}" in keys, (role["id"], option.id)
    for (group, option) in model_checks.METHOD_PROBES:
        assert f"{group}:{option}" in keys
    for e in entries():
        assert hasattr(Checker, "_" + e.probe[0]) or e.probe[0] == "webrtc", e.key
        assert "," not in e.name
        if e.group_option is not None:  # the name shown resolves to the option it names (names.py, Slice 3)
            assert names.option(e.role, e.name).id == e.option, e.key
    assert len({e.key for e in entries()}) == len(entries())


def run_check(app, chosen, **kw):
    # The workers are stand-ins. Explicit generated pixels keep their wiring checks
    # independent of the named, real leaf frame used by make capability-check.
    image = np.full((720, 1280, 3), 180, np.uint8)
    cv2.rectangle(image, (825, 350), (885, 430), (40, 70, 90), -1)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return asyncio.run(Checker(app.hub, app.agent, app.hub.checks, by="test", jpeg=encoded.tobytes(), **kw).run(chosen))


@pytest.fixture
def app(tmp_path, monkeypatch):
    from orchestrator.operations import INSTALLED

    for name in ("sam3", "micro-sam", "cellpose", "open_clip", "trackastra"):
        monkeypatch.setitem(INSTALLED, name, lambda: "")
    monkeypatch.setattr(model_checks, "weights_here", lambda model, environ=None: True)
    built = build_app(tmp_path)
    yield built


@pytest.mark.parametrize("empty", [False, True], ids=["missing", "empty"])
def test_unavailable_check_input_does_not_mark_a_model_broken(app, tmp_path, monkeypatch, empty):
    from orchestrator.assets import Store

    assets = Store(tmp_path / "check-inputs")
    monkeypatch.setenv("MICROMIND_ASSETS", str(assets.root))
    if empty:
        path = assets.bench(model_checks.FRAME)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
    lines = asyncio.run(Checker(app.hub, app.agent, app.hub.checks, by="test").run(
        [model_checks.find("outline", "edgetam")]))
    assert lines[0]["state"] == "not-checked"
    assert model_checks.FRAME in lines[0]["why"]
    assert "check input" in lines[0]["why"]
    assert not model_checks.broken(app.hub, "outline", "edgetam")
    assert not app.hub.gpu.calls


def test_a_local_check_runs_each_model_once_at_the_lowest_priority_and_lets_go_of_what_it_loaded(app):
    lines = run_check(app, LOCAL)
    states = {f"{l['role']}:{l['option']}": l["state"] for l in lines}
    assert {k for k, s in states.items() if s == "works"} >= {
        "outline:edgetam", "outline:sam2.1-tiny", "outline:micro-sam-lm", "cells:cellpose-sam", "cells:micro-sam-lm",
        "cells:edgetam-automask", "pointing:masks", "embed:dinov2-small", "zeroshot:medsiglip-subject",
        "fixed:group-names:model", "dissect:grid", "track:objects", "pointing:molmo"}
    assert states["cells:cellpose-sam-tuned"] == "not-installed"  # no fine-tuned weights made here
    workers = [app.hub.gpu, app.hub.gpu_heavy, app.hub.gpu_follow, app.hub.gpu_molmo, *app.hub.hosts.values()]
    ran = [(w.name, op, p) for w in workers for op, p in w.calls if op not in ("unload", "molmo_load")]
    assert all(p["priority"] in ("research", "interactive") for _, _, p in ran)
    assert all(p["priority"] == "research" for name, op, p in ran if op != "track")  # the follower's op is its own
    # Shared probes run once: the medical names (two options and a fixed role) and the automatic masks (two).
    assert sum(op == "zeroshot" for _, op, _ in ran) == 1 and sum(op == "automask" for _, op, _ in ran) == 1
    # What it loaded is let go after, except the click worker's EdgeTAM and the follower's model.
    unloaded = {(w.name, p["model"]) for w in workers for op, p in w.calls if op == "unload"}
    # micro-sam in its own worker (hub.host_for), let go of by stopping it (Loads.let_go); SAM 2.1 tiny unloaded.
    assert ("micromind-gpu", "sam2.1-tiny") in unloaded and "micro-sam-lm" not in app.hub.hosts["micro-sam-lm"].resident
    assert ("micromind-gpu", "edgetam") not in unloaded and ("micromind-gpu-follow", "edgetam-video") not in unloaded
    # Kept: one line a check, the latest per entry counts; the runner's executions are origin "check", never outcomes.
    saved = [json.loads(x) for x in app.paths.checks.read_text().splitlines()]
    assert len(saved) == len(LOCAL) and app.hub.checks.of("outline", "edgetam")["state"] == "works"
    assert all(l["by"] == "test" and l["at"] and "build" in l for l in saved)
    executions = list(app.hub.runner.executions.values())
    assert executions and {e.origin for e in executions} == {"check"}
    from orchestrator.outcomes import run_of

    assert all(run_of({"kind": "execution", **e.wire()}) is None for e in executions)
    assert app.hub.scene.objects == {}  # nothing on the user's view


def test_the_followers_check_is_a_step_its_worker_takes(app):
    """make capability-check, 2026-10-11 08:27: the follower's model was "broken: a tracking step from before a newer
    one": the check sent session -time, and the worker's inbox refuses a step older than the newest it took (from
    (-1, -1)). Its steps are now ones a fresh follow worker takes, in order."""
    from gpu_worker.inbox import Inbox

    run_check(app, [model_checks.find("track", "objects")])
    steps = [p for op, p in app.hub.gpu_follow.calls if op == "track"]
    inbox = Inbox({"ping", "segment"})
    assert len(steps) == 2 and all(inbox.refusal({"op": "track", "id": n, **p}) is None for n, p in enumerate(steps))


def test_a_model_that_fails_is_recorded_broken_in_its_own_words(app):
    app.hub.host_for("micro-sam-lm").fail["micro-sam-lm"] = "micro-sam's own Python exited (status 1)"  # its own worker
    lines = run_check(app, [e for e in LOCAL if "micro-sam" in e.option])
    assert [(l["role"], l["state"], l["why"]) for l in lines] == [
        ("outline", "broken", "micro-sam's own Python exited (status 1)"),
        ("outline_repair", "broken", "micro-sam's own Python exited (status 1)"),
        ("cells", "broken", "micro-sam's own Python exited (status 1)")]


def test_what_cannot_run_here_is_said_without_running_it(monkeypatch):
    from orchestrator import operations

    find = model_checks.find
    env = {"MICROMIND_EDITION": "commercial"}
    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    state, why = before(find("cells", "cellpose-sam"), environ=env)
    assert state == "not-in-edition" and "non-commercial" in why
    monkeypatch.delenv("MICROMIND_EDITION")
    assert before(find("outline", "sam3.1-fal"), environ={}) == ("needs-key", "needs FAL_KEY")
    assert before(find("fixed:lesson-writer", "model"), environ={}) == ("needs-key", "needs ANTHROPIC_API_KEY")
    monkeypatch.setitem(operations.INSTALLED, "sam3", lambda: "not installed here (sam3: `make sam3-setup`)")
    assert before(find("outline", "sam3.1-local"), environ={}, download=True) == (
        "not-installed", "not installed here (sam3: `make sam3-setup`)")
    monkeypatch.setitem(operations.INSTALLED, "sam3", lambda: "")
    monkeypatch.setitem(model_checks.__dict__, "weights_here", lambda model, environ=None: False)
    state, why = before(find("outline", "sam3.1-local"), environ={})
    assert state == "not-installed" and "not downloaded here yet" in why
    assert before(find("outline", "sam3.1-local"), environ={}, download=True) == ("", "")
    monkeypatch.setitem(operations.INSTALLED, "micro-sam", lambda: "not installed here (micro-sam's own Python)")
    assert before(find("outline", "micro-sam-lm"), environ={})[0] == "not-installed"
    keys = {"OPENAI_API_KEY": "x", "GEMINI_API_KEY": "x"}
    assert before(find("voicelink", "webrtc"), environ=keys)[0] == "not-checked"
    assert before(find("fixed:video", "model"), environ=keys)[0] == "not-checked"
    assert before(find("fixed:video", "model"), environ=keys, video=True) == ("", "")
    monkeypatch.setitem(model_checks.__dict__, "weights_here", lambda model, environ=None: True)
    assert before(find("track", "objects"), environ={}, live=True)[0] == "not-checked"  # the follower's own session
    assert before(find("track", "objects"), environ={}) == ("", "")


def test_the_wire_carries_the_last_check_beside_the_verdict_and_the_agent_hears_of_broken_ones(tmp_path):
    from orchestrator.options import Settings

    store = Checks(tmp_path / "checks.jsonl", version="abc1234")
    store.record(model_checks.find("outline", "micro-sam-lm"), "broken", "micro-sam's own Python exited", ms=1200)
    store.record(model_checks.find("outline", "edgetam"), "works", ms=40, loadMs=900)
    store.record(model_checks.find("fixed:link", "model"), "not-installed", "not installed here (trackastra)")
    store.record(model_checks.find("outline", "sam2.1-tiny"), "broken", "out of memory")
    store.version = "def5678"  # a later build: the SAM 2.1 line is from an earlier one
    store.record(model_checks.find("outline", "micro-sam-lm"), "broken", "micro-sam's own Python exited")
    w = capabilities.wire(Settings(), {}, version="def5678", checks=store.latest(),
                          verdicts={"click-outline": {"id": "click-outline", "state": "works", "at": "x"}})
    roles = {r["id"]: r for r in w["roles"]}
    options = {o["id"]: o for o in roles["outline"]["options"]}
    assert options["edgetam"]["check"]["state"] == "works" and options["edgetam"]["check"]["loadMs"] == 900
    found = options["micro-sam-lm"]["check"]
    assert (found["state"], found["why"]) == ("broken", "micro-sam's own Python exited")
    assert found["at"] and "ms" not in found and "earlierBuild" not in found  # the newest line, made on this build
    assert options["sam2.1-tiny"]["check"]["earlierBuild"] == "abc1234"
    assert "check" not in options["sam3.1-fal"]  # never checked: no check, never a guess
    assert roles["fixed:link"]["check"]["state"] == "not-installed" and roles["fixed:link"]["primary"]["check"]
    assert next(c for c in w["capabilities"] if c["id"] == "click-outline")["verdict"]["state"] == "works"  # untouched
    text = capabilities.text(w)
    assert ("micro-sam (light microscopy) on this Mac is broken here: micro-sam's own Python exited (checked at "
            in text)
    assert "SAM 2.1 tiny (on this Mac) was broken here at" in text and "on an earlier build (out of memory)" in text
    assert "EdgeTAM (on this Mac) is broken" not in text


def test_use_and_set_default_refuse_a_model_broken_on_this_build(app, monkeypatch):
    from orchestrator.brain.registry import ToolContext, ToolFailed
    from orchestrator.operations import alternatives_text
    from orchestrator.ticket import issue, using

    store = app.hub.checks
    store.record(model_checks.find("outline", "sam2.1-tiny"), "broken", "out of memory")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    store.record(model_checks.find("vision", "gpt-6.1-sol"), "broken", "the model answered 404")
    tools = app.brain.registry.tools
    ctx = ToolContext("chat", 1, None, "use SAM 2.1")

    async def use(args):
        with using(issue(app.hub, "turn")):
            return await tools["use"].handler(ctx, {"by": "user", **args})

    with pytest.raises(ToolFailed) as refused:
        asyncio.run(use({"operation": "outline", "implementation": "SAM 2.1"}))
    assert str(refused.value).startswith("SAM 2.1 tiny (on this Mac) is broken here: out of memory (checked at ")
    assert "Check now" in refused.value.next
    with pytest.raises(ToolFailed) as refused:
        asyncio.run(use({"operation": "vision", "implementation": "gpt-6.1-sol"}))
    assert str(refused.value).startswith("GPT-6.1 Sol (cloud) is broken here: the model answered 404")
    with pytest.raises(ToolFailed):
        asyncio.run(tools["set_default"].handler(ctx, {"by": "user", "operation": "outline", "implementation": "sam2.1-tiny"}))
    assert app.hub.settings.outline == "edgetam"
    assert "SAM 2.1 tiny (on this Mac) is broken here: out of memory" in alternatives_text(app.hub, "outline", "box")
    # From an earlier build it is said, never refused: the code may have changed since.
    store.version = "newer"
    assert asyncio.run(use({"operation": "outline", "implementation": "sam2.1-tiny"})).startswith("for this request")


def test_check_now_runs_in_the_live_app_one_check_at_a_time(app, monkeypatch):
    from orchestrator.commands import COMMANDS

    monkeypatch.setattr(model_checks, "frame", lambda: b"")
    sent = []
    app.hub.broadcast = sent.append

    async def go():
        first = await COMMANDS["capability-check"](app, None, {})
        again = await COMMANDS["capability-check"](app, None, {})
        await asyncio.gather(*app._tasks)
        return first, again

    first, again = asyncio.run(go())
    assert first["started"] and first["of"] == len(LOCAL) and again["said"] == "a check is already running"
    assert app.hub.checks.running is None and app.hub.checks.of("track", "objects")["state"] == "not-checked"
    assert app.hub.checks.of("outline", "edgetam")["by"] == "Check now"
    wires = [m for m in sent if m.get("type") == "capabilities"]
    assert any(w.get("checking") for w in wires) and "checking" not in wires[-1]
    assert not [w for w in (app.hub.gpu_follow,) for op, _ in w.calls if op == "track"]  # never the follower's session


def guarded(worker):
    """The stand-in's calls with the real client's memory guard before a load (gpu_client.GpuClient.call), and its role
    as the real client takes it from its name (what the guard may unload)."""
    from gpu_worker.loading import model_of
    from orchestrator.gpu_client import ROLES

    worker.role = worker.role or ROLES.get(worker.name)
    plain = worker.call

    async def call(op, timeout=30.0, priority=None, **payload):
        model = model_of(op, payload)
        if model and model not in worker.resident and worker.loads is not None and op not in ("unload", "ping"):
            await worker.loads.before_load(worker, model)
        return await plain(op, timeout, priority, **payload)

    worker.call = call
    return worker


def test_check_now_never_pushes_out_what_the_live_view_keeps_loaded(app):
    """Live 2026-10-11 16:31: Check now loaded Molmo (12.5 GB) and the memory guard unloaded the live app's Cellpose-SAM
    and DINOv2 to make room; swap-ins reached 6,387/s, and 7,583/s when they loaded again at 16:34. Inside the live app a
    model there is no room for beside the live view's is not checked, said why, and nothing is unloaded."""
    heavy, molmo = guarded(app.hub.gpu_heavy), guarded(app.hub.gpu_molmo)
    now = 1.0
    heavy.resident.update({"cellpose-sam": {"usedAt": now}, "dinov2-small": {"usedAt": now}})  # the live view's
    app.hub.loads.measured["molmo2-4b"] = [{"seconds": 32.7, "memoryMb": 11381}]
    app.hub.loads.free_mb = lambda: 11656.0  # the demo's: less than Molmo's 11.4 GB and the margin
    lines = run_check(app, [model_checks.find("pointing", "molmo"), model_checks.find("embed", "dinov2-small")], live=True)
    states = {f"{l['role']}:{l['option']}": (l["state"], l["why"]) for l in lines}
    state, why = states["pointing:molmo"]
    assert state == "not-checked" and why == (
        "inside the live app it needs 11.6 GB of memory and 11.4 GB is free: loading it would push out Cellpose-SAM (on "
        "this Mac) and DINOv2 small (on this Mac), which the live view keeps loaded; make capability-check checks it with "
        "the app stopped")
    assert molmo.calls == []  # its worker never asked to load it (not even started for it)
    assert states["embed:dinov2-small"][0] == "works"  # loaded already: nothing to make room for
    assert set(heavy.resident) == {"cellpose-sam", "dinov2-small"} and not [c for c in heavy.calls if c[0] == "unload"]
    saved = app.hub.checks.of("pointing", "molmo")
    assert saved["state"] == "not-checked" and saved["by"] == "test"


def test_check_now_refuses_a_load_with_no_room_however_deep_in_the_runner_it_is_asked(app):
    """Room is looked at again where the load is asked for (the memory guard, loads.before_load): memory taken between the
    check's first look and the worker's call is never made by unloading the live view's models either."""
    # A model the shared analysis worker loads (SAM 3.1 has a worker of its own since the model runtime: hub.host_for).
    heavy = guarded(app.hub.gpu_heavy)
    heavy.resident.update({"dinov2-small": {"usedAt": 1.0}})
    free = iter([20000.0, 1000.0])  # room when the check looks first, none when the cell finder loads
    app.hub.loads.free_mb = lambda: next(free, 1000.0)
    lines = run_check(app, [model_checks.find("cells", "cellpose-sam")], live=True)
    assert lines[0]["state"] == "not-checked" and "push out DINOv2 small (on this Mac)" in lines[0]["why"]
    assert set(heavy.resident) == {"dinov2-small"} and not [c for c in heavy.calls if c[0] == "unload"]


def test_make_capability_check_with_the_app_stopped_still_makes_room(app):
    """Outside the live app (make capability-check: the app stopped) the guard makes room as it always has."""
    heavy = guarded(app.hub.gpu_heavy)
    heavy.resident.update({"dinov2-small": {"usedAt": 1.0}})
    free = iter([1000.0, 20000.0])
    app.hub.loads.free_mb = lambda: next(free, 20000.0)
    lines = run_check(app, [model_checks.find("cells", "cellpose-sam")])
    assert lines[0]["state"] == "works" and ("unload", {"model": "dinov2-small", "priority": "interactive"}) in heavy.calls


def test_the_voice_is_checked_by_a_spoken_request_it_hands_to_the_app(app, monkeypatch):
    """GPT-Live hears the request spoken (as the studio's microphone sends it) and hands it over as a delegation; the
    check waits for that, through the app's own session code and voice prompt. Typed words never made it delegate (the
    first cloud check, 2026-10-11). The fake connection stands in for OpenAI."""
    from tests.test_voice_openai import FakeCM, FakeConn

    monkeypatch.setenv("OPENAI_API_KEY", "x")
    conn = FakeConn()

    class Heard:  # GPT-Live hears the words and, after a few chunks, delegates them
        async def append(self, **kw):
            conn.sent.append(("audio", kw))
            if sum(kind == "audio" for kind, _ in conn.sent) == 5:
                await conn.inbox.put({"type": "session.delegation.created", "offset_ms": 100, "delegation": {"id": "d1"}})

    conn.session.input_audio = Heard()
    checker = Checker(app.hub, app.agent, app.hub.checks, by="test", jpeg=b"")
    checker.voice_connect, checker.voice_speech = (lambda: FakeCM(conn)), bytes(16000 * 2 // 10)  # 0.1 s of "speech"
    lines = asyncio.run(checker.run([model_checks.find("voice", "gpt-live-1"), model_checks.find("voicelink", "relay")]))
    assert [l["state"] for l in lines] == ["works", "works"] and "after the words ended" in lines[0]["detail"]
    started = next(cfg for kind, cfg in conn.sent if kind == "start")
    assert "HookeUI" in started["instructions"]  # the app's own voice prompt (prompts.LIVE_VOICE_DELEGATE)
    assert any(kind == "commentary" for kind, _ in conn.sent)  # the app's answer went back
    silent = FakeConn()
    checker.voice_connect = lambda: FakeCM(silent)  # a voice that never delegates is broken, in words
    monkeypatch.setitem(model_checks.TIMEOUT_S, "voice", 0.2)
    lines = asyncio.run(checker.run([model_checks.find("voice", "gpt-live-1")]))
    assert lines[0]["state"] == "broken" and "no answer within" in lines[0]["why"]


def test_a_cloud_model_answers_through_the_apps_own_provider(app, monkeypatch):
    from types import SimpleNamespace

    asked = []

    class Provider:
        async def ask(self, images, prompt, max_output_tokens=0):
            asked.append((len(images), prompt))
            return SimpleNamespace(data={"ok": True, "what": "leaf cells"}, latency_ms=900, error=None,
                                   usage={"input": 1000, "output": 20})

    monkeypatch.setattr(app.agent, "vision", lambda option: Provider())
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    lines = run_check(app, [model_checks.find("vision", "gemini-3.8-flash"), model_checks.find("fast", "gemini-3.8-flash")])
    assert [l["state"] for l in lines] == ["works", "works"] and len(asked) == 1  # one call for both
    assert lines[0]["detail"] == "answered: leaf cells" and lines[0]["costUsd"] > 0
