"""L1b part 1 (ticket 62): client setup and repeated builds kept off the event loop, each beside today's way
(MICROMIND_LOOP_WORK=on-loop) so the two can be measured."""
import asyncio
from types import SimpleNamespace

import pytest


@pytest.fixture
def made(monkeypatch):
    """Every provider client made, with the thread that made it."""
    import threading

    from google import genai
    import openai

    made = []
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-value")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-value")

    def client(provider):
        def make(**kwargs):
            made.append((provider, threading.current_thread().name, kwargs))

            async def generate_content(**_):
                return SimpleNamespace(text='{"answer": "a"}', usage_metadata=None, model_version=None, candidates=[])

            async def create(**_):
                return SimpleNamespace(output_text='{"answer": "a"}', usage=None, model=None)

            return SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
                                   responses=SimpleNamespace(create=create))
        return make

    monkeypatch.setattr(genai, "Client", client("gemini"))
    monkeypatch.setattr(openai, "AsyncOpenAI", client("openai"))
    return made


@pytest.mark.parametrize("spec", ["gemini:gemini-3.8-flash@low", "openai:gpt-6-luna@none"])
def test_a_vision_client_made_on_the_loop_is_made_once_on_a_thread(made, spec):
    """sight.look -> agent.vision -> GeminiVision.__init__ stalled the loop 1050 ms (sim 2026-10-19): made on the loop,
    the adapter makes its client on a thread at the first call and reuses it after."""
    from orchestrator.vision import make_vision

    async def go():
        vision = make_vision(spec)
        assert made == []  # nothing made on the loop
        first, second = await asyncio.gather(vision.ask([], "p"), vision.ask([], "p"))
        third = await vision.ask([], "p")
        return first, second, third

    results = asyncio.run(go())
    assert [r.data for r in results] == [{"answer": "a"}] * 3
    assert len(made) == 1 and made[0][1] != "MainThread"  # once, off the loop's thread
    assert made[0][2].get("max_retries") == 0 or made[0][2]["http_options"]["retry_options"]["attempts"] == 1


def test_off_the_loop_and_on_loop_a_vision_client_is_made_at_once(made, monkeypatch):
    from orchestrator.vision import make_vision

    make_vision("gemini:gemini-3.8-flash@low")  # no loop here: as before
    assert len(made) == 1 and made[0][1] == "MainThread"
    monkeypatch.setenv("MICROMIND_LOOP_WORK", "on-loop")

    async def go():
        make_vision("openai:gpt-6-luna@none")

    asyncio.run(go())
    assert len(made) == 2 and made[1][1] == "MainThread"  # today's way: on the loop, where it is made


def test_a_missing_key_still_fails_where_the_adapter_is_made(monkeypatch):
    from orchestrator.vision import make_vision

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    async def go():
        make_vision("gemini:gemini-3.8-flash@low")

    with pytest.raises(KeyError):
        asyncio.run(go())


def test_a_failed_client_build_is_said_and_tried_again(made, monkeypatch):
    from google import genai

    from orchestrator.vision import make_vision

    calls = []

    def broken(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("certificate bundle unreadable")

    async def go():
        vision = make_vision("gemini:gemini-3.8-flash@low")
        monkeypatch.setattr(genai, "Client", broken)
        first = await vision.ask([], "p")
        second = await vision.ask([], "p")
        return first, second

    first, second = asyncio.run(go())
    assert first.data is None and "certificate bundle unreadable" in first.error
    assert second.data is None and len(calls) == 2


def test_tool_declarations_are_made_again_only_when_a_tool_changed(monkeypatch):
    """A function-calling turn sends every tool's declaration: made once, and again when toolbox.refresh replaced a
    tool's arguments or description, or added or took one away."""
    from orchestrator.brain import registry as registry_module
    from orchestrator.brain.registry import Registry, Tool, schema

    made = []
    real = registry_module.call_schema

    def counting(params):
        made.append(params)
        return real(params)

    monkeypatch.setattr(registry_module, "call_schema", counting)

    async def handler(ctx, args):
        return "ok"

    reg = Registry()
    reg.add(Tool("zoom", "Zoom in.", schema({"object": {"type": "string"}}, ["object"]), handler))
    reg.add(Tool("count", "Count.", schema({}), handler))
    first = reg.responses_tools()
    assert reg.responses_tools() == first and len(made) == 2
    names = [d.name for d in reg.gemini_declarations()]
    assert names == ["zoom", "count"] and len(made) == 4
    reg.gemini_declarations()
    assert len(made) == 4
    reg.tools["count"].description = "Count them."  # what refresh does to a live tool
    assert [d["description"] for d in reg.responses_tools()] == ["Zoom in.", "Count them."]
    reg.tools["count"].params = schema({"kind": {"type": "string"}})
    assert "kind" in reg.responses_tools()[1]["parameters"]["properties"]
    reg.tools.pop("zoom")
    assert [d["name"] for d in reg.responses_tools()] == ["count"]
    returned = reg.responses_tools()
    returned.append("not kept")
    assert reg.responses_tools() == [returned[0]]


def test_tool_declarations_on_loop_are_made_every_turn(monkeypatch):
    from orchestrator.brain import registry as registry_module
    from orchestrator.brain.registry import Registry, Tool, schema

    made = []
    real = registry_module.call_schema
    monkeypatch.setattr(registry_module, "call_schema", lambda p: made.append(p) or real(p))
    monkeypatch.setenv("MICROMIND_LOOP_WORK", "on-loop")

    async def handler(ctx, args):
        return "ok"

    reg = Registry()
    reg.add(Tool("count", "Count.", schema({}), handler))
    reg.responses_tools()
    reg.responses_tools()
    assert len(made) == 2


def test_capabilities_are_built_again_when_the_objective_changed_not_on_every_scale_change(tmp_path, monkeypatch):
    """scale_changed rebuilt the whole capabilities wire (170-260 ms on the loop, measured for L1b) on every change of
    the scale; only the objective is read from the scale by it (`preferences`), so it is rebuilt when that changed."""
    from tests.standin_workers import build_app

    app = build_app(tmp_path)
    built = []
    real = app.capabilities_wire

    def counting(counts=True):
        built.append(app.hub.scale.objective)
        return real(counts)

    monkeypatch.setattr(app, "capabilities_wire", counting)
    sent = []
    monkeypatch.setattr(app.hub, "broadcast", lambda payload: sent.append(payload.get("type")))
    scale = app.hub.scale
    scale.set_objective(40)
    assert built == [40] and sent.count("capabilities") == 1
    scale.set_field_number(20)  # the scale changed, the objective did not
    scale.use_asset("rec-1", {"objective": 10})  # an asset on stage keeps its own objective, not the microscope's
    scale.use_asset(None)
    assert built == [40] and sent.count("scale") == 4
    scale.set_objective(10)
    assert built == [40, 10]
    scale.set_objective(None)
    assert built == [40, 10, None]


def test_capabilities_on_loop_are_built_on_every_scale_change(tmp_path, monkeypatch):
    from tests.standin_workers import build_app

    monkeypatch.setenv("MICROMIND_LOOP_WORK", "on-loop")
    app = build_app(tmp_path)
    built = []
    real = app.capabilities_wire
    monkeypatch.setattr(app, "capabilities_wire", lambda counts=True: built.append(1) or real(counts))
    monkeypatch.setattr(app.hub, "broadcast", lambda payload: None)
    app.hub.scale.set_field_number(20)
    app.hub.scale.use_asset("rec-1", {"objective": 10})
    assert len(built) == 2


def test_the_app_writes_every_log_line_before_stop_returns(tmp_path, monkeypatch):
    """Lines handed to the writer on the loop are in their files when App.stop returns."""
    import json

    from orchestrator.looplag import LINES, Lines
    from tests.standin_workers import build_app

    app = build_app(tmp_path)
    flushed = []
    real = Lines.flush
    monkeypatch.setattr(Lines, "flush", lambda self: flushed.append(self.pending()) or real(self))

    async def go():
        await app.start()
        for i in range(50):
            app.agent.log({"kind": "l1b-test", "i": i})
        await app.stop()

    asyncio.run(go())
    assert LINES.pending() == 0 and flushed
    logged = [json.loads(raw) for raw in app.paths.interaction_log.read_text().splitlines()]
    assert [r["i"] for r in logged if r.get("kind") == "l1b-test"] == list(range(50))


def flood(app, ticks: int, *, every_s: float = 0.002):
    """A download's thread as downloads._run drives it: one state change, then `ticks` progress ticks (only `got`
    moves on), then done. Returns (full catalog_changed runs, progress sends, the bytes the studios were sent, the
    loop's worst gap in ms while it ran)."""
    import threading
    import time

    async def go():
        app._loop = asyncio.get_running_loop()
        downloads = app.downloads
        row = {"model": "flooded", "name": "Flooded", "state": "queued", "got": 0, "total": 1000, "by": "you"}
        downloads.rows["flooded"] = row
        full, progress, sent = [], [], []
        real_changed, real_progress = app.catalog_changed, app.catalog_progress

        def counted_changed():
            full.append(row["got"])
            real_changed()

        def counted_progress():
            progress.append(row["got"])
            real_progress()

        app.catalog_changed, app.catalog_progress = counted_changed, counted_progress
        app.hub.broadcast = lambda payload: sent.extend(
            d["got"] for d in (payload.get("setup") or {}).get("downloads", []) if d.get("model") == "flooded")

        def thread():
            with downloads._lock:
                row.update(state="downloading")
            downloads.changed()
            for i in range(1, ticks + 1):
                with downloads._lock:
                    row.update(got=i * 1000 // ticks)
                downloads.changed()
                time.sleep(every_s)
            with downloads._lock:
                row.update(state="done")
            downloads.changed()

        worst, last, running = 0.0, time.perf_counter(), threading.Thread(target=thread)
        running.start()
        while running.is_alive():
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            worst, last = max(worst, now - last), now
        for _ in range(20):  # what the last tick asked for runs
            await asyncio.sleep(0.01)
        return full, progress, sent, worst * 1000

    return asyncio.run(go())


def test_a_download_flooding_progress_ticks_builds_the_capabilities_a_bounded_number_of_times(tmp_path):
    """FIX l1b-loop-work (L7b, 2026-10-20): every 0.5 s progress tick queued a whole catalog_changed, which built the
    0.5 MB capabilities twice; under load one took longer than a tick and the loop fell behind (3.2 s, 11.8 s stalls).
    Now at most one waits, a tick that only moves bytes on renews the download rows of the last capabilities, and
    the whole wire is built for each change of state (and at most every PROGRESS_FULL_S while bytes move on)."""
    from orchestrator.app import PROGRESS_FULL_S
    from tests.standin_workers import build_app

    app = build_app(tmp_path)
    ticks = 200
    full, progress, sent, worst_ms = flood(app, ticks)
    assert PROGRESS_FULL_S >= 5.0
    assert 2 <= len(full) <= 4, full  # downloading, done, at most one or two in between: never one per tick
    assert full[-1] == 1000  # the last state (done) is the one built
    assert progress and len(progress) + len(full) <= ticks + 2
    assert sent == sorted(sent) and sent[-1] == 1000  # the studios saw it move on, in order, to its end
    assert worst_ms < 2000 + 400 * len(full), worst_ms  # bounded: a few whole builds, never the backlog


def test_a_progress_tick_renews_the_rows_the_studio_draws_and_keeps_the_rest(tmp_path):
    from tests.standin_workers import build_app

    app = build_app(tmp_path)
    app._capabilities_sent = {"type": "capabilities", "roles": ["kept"],
                              "setup": {"keys": {"k": 1}, "downloads": [{"model": "m", "got": 1}]},
                              "models": [{"id": "m", "download": {"model": "m", "got": 1}}, {"id": "other"}]}
    app.downloads.rows["m"] = {"model": "m", "state": "downloading", "got": 7, "total": 10}
    sent = []
    app.hub.broadcast = sent.append
    app.catalog_progress()
    (wire,) = sent
    assert wire["roles"] == ["kept"] and wire["setup"]["keys"] == {"k": 1}
    assert wire["setup"]["downloads"] == [{"model": "m", "state": "downloading", "got": 7, "total": 10}]
    assert wire["models"][0]["download"]["got"] == 7 and wire["models"][1] == {"id": "other"}
    assert "freeBytes" in wire["setup"]["disk"] or wire["setup"]["disk"] == {}


def test_on_loop_every_download_tick_is_a_whole_catalog_change(tmp_path, monkeypatch):
    from tests.standin_workers import build_app

    monkeypatch.setenv("MICROMIND_LOOP_WORK", "on-loop")
    app = build_app(tmp_path)
    full, progress, _sent, _worst = flood(app, 20, every_s=0.02)
    assert len(full) == 22 and progress == []  # today's way: one per call, measured beside the new
