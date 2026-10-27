"""Models loading on the GPU workers (orchestrator/loads.py): said on the request's step and in the chat, measured,
kept within memory without ever unloading the live outline model or the follower's, and the defaults loaded early."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from orchestrator.brain.registry import CURRENT_RUN, ToolRun
from orchestrator.loads import Loads, duration, model_name, took
from tests.waiting import HANG_S, until

MICRO_SAM = model_name("micro-sam-lm")  # "micro-sam (light microscopy) on this Mac"


class Worker:
    """A worker's client as the guard sees it: its role, what it holds, and its unload op."""

    def __init__(self, name, role, resident=None):
        self.name, self.role, self.ready, self.loads = name, role, True, None
        self.resident = {m: {"usedAt": used} for m, used in (resident or {}).items()}
        self.loading, self.unloaded = {}, []

    async def call(self, op, timeout=0, priority=None, **req):
        assert op == "unload"
        self.unloaded.append(req["model"])
        self.resident.pop(req["model"], None)
        return {"ok": True, "unloaded": [req["model"]], "freedMb": 1000}


def activity_hub(tmp_path):
    """A hub with Activity (activity.py), filing its lines in a day's journal; `shown` is what the studios are sent."""
    from orchestrator.activity import Activity
    from orchestrator.day import DayStore
    from orchestrator.dialogue import Dialogue

    shown = []
    hub = SimpleNamespace()
    day = DayStore(root=tmp_path / "sessions")
    hub.activities = Activity(hub, Dialogue(day, shown.append))
    return hub, shown, day


def test_a_load_is_said_on_the_requests_step_and_in_activity_running_then_done_and_measured(tmp_path):
    hub, said, day = activity_hub(tmp_path)
    logged = []
    loads = Loads(tmp_path / "models" / "loads.json", log=logged.append, hub=hub)
    client = SimpleNamespace(name="micromind-gpu-heavy")
    run = ToolRun("highlight", {})
    token = CURRENT_RUN.set(run)
    try:
        loads.told(client, {"id": 4, "load": "loading", "model": "micro-sam-lm", "limitS": 300})
        assert run.progress == f"loading {MICRO_SAM} for the first time"  # the one name (names.said_impl)
        assert run.wire()["progress"] == run.progress
        assert [(s["kind"], s["text"], s["state"]) for s in said] == [
            ("model", f"Loading {MICRO_SAM} for the first time", "running")]  # a running line of Activity
        loads.told(client, {"id": 4, "load": "loaded", "model": "micro-sam-lm", "seconds": 118.0, "memoryMb": 2400})
    finally:
        CURRENT_RUN.reset(token)
    assert run.progress is None
    assert said[1]["id"] == said[0]["id"] and said[1]["state"] == "done"  # the same line, now done
    assert said[1]["text"] == f"{MICRO_SAM} is ready (loaded in 1 min 58 s)"
    assert said[1]["details"]["lines"] == ["Loaded in 1 min 58 s", "It holds 2.3 GB of memory"]
    assert [line["state"] for line in day.active.lines] == ["done"]  # one line in the journal, replaced in place
    assert [r["state"] for r in logged] == ["loading", "loaded"] and logged[0]["first"] is True
    loads.told(client, {"id": 5, "load": "loading", "model": "medsiglip-448"})
    loads.told(client, {"id": 5, "load": "failed", "model": "medsiglip-448", "error": "out of memory"})
    assert (said[-1]["state"], said[-1]["text"]) == ("failed", f"{model_name('medsiglip-448')} did not load: out of memory")
    # Measured: kept beside the models, and said as "usually" next time.
    until(lambda: (tmp_path / "models" / "loads.json").exists())
    assert json.loads((tmp_path / "models" / "loads.json").read_text())["models"]["micro-sam-lm"][0]["seconds"] == 118.0
    again = Loads(tmp_path / "models" / "loads.json")
    assert again.words("micro-sam-lm") == f"loading {MICRO_SAM}, usually about 2 min"
    assert again.size_mb("micro-sam-lm") == 2400 and again.size_mb("never-seen") == 2048
    assert duration(40) == "about 40 s" and took(118) == "1 min 58 s"


def test_a_preload_and_a_quick_load_are_measured_but_not_said(tmp_path):
    from orchestrator.gpu_client import _QUIET

    hub, said, _day = activity_hub(tmp_path)
    logged = []
    loads = Loads(tmp_path / "loads.json", log=logged.append, hub=hub)
    loads.measured["edgetam"] = [{"seconds": 1.2, "memoryMb": 400}]
    client = SimpleNamespace(name="micromind-gpu")
    loads.told(client, {"id": 1, "load": "loading", "model": "edgetam"})  # usually 1.2 s: not worth a line
    loads.told(client, {"id": 1, "load": "loaded", "model": "edgetam", "seconds": 1.1, "memoryMb": 410})
    token = _QUIET.set(True)
    try:
        loads.told(client, {"id": 2, "load": "loading", "model": "cellpose-sam"})  # a preload
        loads.told(client, {"id": 2, "load": "loaded", "model": "cellpose-sam", "seconds": 6.0, "memoryMb": 1300})
    finally:
        _QUIET.reset(token)
    assert said == [] and len(logged) == 4 and logged[2]["quiet"] is True


def test_the_memory_guard_unloads_the_least_recently_used_never_the_live_ones(tmp_path):
    hub, said, _day = activity_hub(tmp_path)

    async def go(free_now):
        free = iter(free_now)
        loads = Loads(None, log=logs.append, hub=hub, free_mb=lambda: next(free))
        main = Worker("micromind-gpu", "main", {"edgetam": 1.0, "sam2.1-tiny": 5.0})
        follow = Worker("micromind-gpu-follow", "follow", {"edgetam-video": 0.0})
        heavy = Worker("micromind-gpu-heavy", "heavy", {"cellpose-sam": 3.0, "dinov2-small": 9.0})
        molmo = Worker("micromind-gpu-molmo", "molmo", {"molmo2-4b": 2.0})
        for w in (main, follow, heavy, molmo):
            loads.register(w)
        await loads.before_load(heavy, "sam3.1-local")  # 3.5 GB wanted
        return main, follow, heavy, molmo

    logs = []
    main, follow, heavy, molmo = asyncio.run(go([2000, 2500, 9000]))
    # Least recently used first: Molmo (used at 2), then Cellpose-SAM (3), then enough room.
    assert molmo.unloaded == ["molmo2-4b"] and heavy.unloaded == ["cellpose-sam"]
    assert main.unloaded == [] and follow.unloaded == []
    assert [(s["kind"], s["text"], s["state"]) for s in said] == [
        ("model", f"Unloaded Molmo 2 4B to make room for {model_name('sam3.1-local')}", "done"),
        ("model", f"Unloaded {model_name('cellpose-sam')} to make room for {model_name('sam3.1-local')}", "done")]
    logs.clear(), said.clear()
    main, follow, heavy, molmo = asyncio.run(go([500] * 10))  # never enough: everything that may go goes, and it is said
    assert "edgetam" not in main.unloaded and follow.unloaded == [] and main.unloaded == ["sam2.1-tiny"]
    assert logs[-1]["kind"] == "memory-short"
    logs.clear()
    asyncio.run(go([20000]))  # room enough: nothing unloaded
    assert logs == []


def test_inside_sparing_a_load_with_no_room_is_refused_and_nothing_is_unloaded(tmp_path):
    """The capability check inside the live app (model_checks.Checker live, loads.sparing): live 2026-10-11 16:31 its
    Molmo pushed out the live app's Cellpose-SAM and DINOv2. A load that does not fit is refused (NoRoom), noted for the
    check and logged with what stayed loaded; the same load outside `sparing` makes room as before."""
    import pytest

    from orchestrator.loads import NoRoom, sparing

    hub, said, _day = activity_hub(tmp_path)
    logs = []
    loads = Loads(None, log=logs.append, hub=hub, free_mb=lambda: 11656.0)
    heavy = Worker("micromind-gpu-heavy", "heavy", {"cellpose-sam": 3.0, "dinov2-small": 9.0})
    molmo = Worker("micromind-gpu-molmo", "molmo")
    for w in (heavy, molmo):
        loads.register(w)

    async def go():
        with sparing() as refused:
            with pytest.raises(NoRoom) as before:
                await loads.before_load(molmo, "molmo2-4b")  # 12 GB documented and the margin
            with pytest.raises(NoRoom):
                await loads.fits(molmo, "molmo2-4b")  # before its worker is even started
            await loads.fits(heavy, "cellpose-sam")  # loaded there already: nothing to make room for
        return refused, before.value

    refused, refusal = asyncio.run(go())
    assert heavy.unloaded == [] and said == [] and len(refused) == 2 and refused[0] is refusal
    assert (refusal.model, refusal.need_mb, refusal.free_mb, refusal.kept) == ("molmo2-4b", 12512.0, 11656.0,
                                                                              ["cellpose-sam", "dinov2-small"])
    assert str(refusal) == "Molmo 2 4B needs 12.2 GB of memory and 11.4 GB is free"
    assert logs[0] == {"kind": "memory-short", "model": "molmo2-4b", "worker": "micromind-gpu-molmo", "freeMb": 11656,
                       "needMb": 12512, "unloaded": [], "spared": ["cellpose-sam", "dinov2-small"]}
    asyncio.run(loads.fits(molmo, "molmo2-4b"))  # outside `sparing`: never refused
    asyncio.run(loads.before_load(molmo, "molmo2-4b"))
    assert heavy.unloaded == ["cellpose-sam", "dinov2-small"]  # ...and room is made as it always was
def test_sizes_measured_by_footprint_replace_the_older_resident_ones_and_a_load_is_admitted_by_its_peak(tmp_path):
    # Resident size missed Metal's memory: MedSigLIP read "0 MB" and SAM 2.1 tiny "9 MB" (loads.json, 2026-10-10/11).
    # The kernel's footprint (gpu_worker/footprint.py) is what a load is measured by now, and its peak (a checkpoint
    # held whole before the GPU has it) is what must fit.
    loads = Loads(tmp_path / "loads.json")
    worker = SimpleNamespace(name="micromind-gpu-heavy")
    loads.told(worker, {"load": "loaded", "model": "medsiglip", "seconds": 3.8, "memoryMb": 0})
    loads.told(worker, {"load": "loaded", "model": "sam2.1-tiny", "seconds": 0.6, "memoryMb": 9})
    assert loads.size_mb("medsiglip") == 2400 and loads.peak_mb("medsiglip") == 3000  # nothing measured that can be read: its manifest's
    assert loads.size_mb("sam2.1-tiny") == 9  # the old way's number, while it is the only one
    loads.told(worker, {"load": "loaded", "model": "sam2.1-tiny", "seconds": 0.5, "memoryMb": 279, "peakMb": 283,
                        "how": "footprint"})
    loads.told(worker, {"load": "loaded", "model": "medsiglip", "seconds": 3.6, "memoryMb": 2097, "peakMb": 2753,
                        "how": "footprint"})
    assert loads.size_mb("sam2.1-tiny") == 279 and loads.peak_mb("sam2.1-tiny") == 283
    assert loads.size_mb("medsiglip") == 2097 and loads.peak_mb("medsiglip") == 2753
    kept = json.loads((tmp_path / "loads.json").read_text()) if (tmp_path / "loads.json").exists() else None
    assert kept is None or kept["models"]["medsiglip"][-1]["how"] == "footprint"

    async def admitted(free):
        heavy = Worker("micromind-gpu-heavy", "heavy", {"dinov2-small": 1.0})
        loads.free_mb = lambda: free
        loads.register(heavy)
        await loads.before_load(heavy, "medsiglip")
        return heavy.unloaded

    # 2,600 MB free holds its size and the margin (2,609) only just not, its peak and the margin (3,265) not at all.
    assert asyncio.run(admitted(2600)) == ["dinov2-small"]
    assert asyncio.run(admitted(3300)) == []


def test_the_defaults_are_the_followers_model_the_cell_finder_and_the_picture_model():
    from orchestrator.options import Settings

    follow, heavy = Worker("micromind-gpu-follow", "follow"), Worker("micromind-gpu-heavy", "heavy")
    hub = SimpleNamespace(settings=Settings(), gpu_follow=follow, gpu_heavy=heavy)
    jobs = [(c.name, m) for c, m in Loads(None).defaults(hub)]
    assert jobs == [("micromind-gpu-follow", "edgetam-video"), ("micromind-gpu-heavy", hub.settings.cells),
                    ("micromind-gpu-heavy", "dinov2-small")]
    hub.settings = Settings(track="slide", cells="off", taught="off", seen="off")
    assert Loads(None).defaults(hub) == []


def test_the_defaults_load_once_frames_flow_quietly_one_at_a_time(monkeypatch):
    from orchestrator import loads as module
    from orchestrator.options import Settings

    monkeypatch.setattr(module, "PRELOAD_AFTER_S", 0.0)
    calls, logged = [], []

    class Starting(Worker):
        async def ensure_started(self):
            return self

        async def call(self, op, timeout=0, priority=None, **req):
            calls.append((self.name, op, req.get("model"), priority, req.get("_quiet")))
            self.resident[req["model"]] = {"usedAt": 0.0}
            return {"ok": True}

    follow, heavy = Starting("micromind-gpu-follow", "follow"), Starting("micromind-gpu-heavy", "heavy")
    heavy.resident["dinov2-small"] = {"usedAt": 1.0}  # loaded already (a taught label's embedding): not again
    hub = SimpleNamespace(settings=Settings(), gpu=SimpleNamespace(ready=True), latest_info=object(),
                          gpu_follow=follow, gpu_heavy=heavy)
    asyncio.run(asyncio.wait_for(Loads(None, log=logged.append).preload(hub), HANG_S))
    assert calls == [("micromind-gpu-follow", "load", "edgetam-video", "background", True),
                     ("micromind-gpu-heavy", "load", hub.settings.cells, "background", True)]
    assert [r["model"] for r in logged if r["kind"] == "model-preload"] == ["edgetam-video", hub.settings.cells, "dinov2-small"]
    assert logged[-1]['alreadyResident'] is True


@pytest.mark.parametrize('at_start', [False, True])
def test_existing_resident_default_has_an_explicit_checked_receipt(at_start):
    from sim.harness import preload_accounted_for
    logged, loaded = [], []
    class Existing(Worker):
        async def ensure_started(self):
            self.ready = True
            self.resident['dinov2-small'] = {'usedAt': 1.0}
        async def call(self, *args, **kwargs):
            loaded.append(kwargs)
    worker = Existing('heavy', 'heavy', {'dinov2-small': 1.0} if at_start else {})
    worker.hung = ''
    asyncio.run(Loads(None, log=logged.append)._ready(worker, 'dinov2-small'))
    assert not loaded
    assert preload_accounted_for('heavy', 'dinov2-small',
        {'heavy': {'ready': True, 'resident': ['dinov2-small']}}, logged, [], [])
    assert logged[0]['alreadyResident'] is True


@pytest.mark.parametrize('ready,hung', [(False, ''), (True, 'stuck')])
def test_unhealthy_worker_cannot_certify_a_stale_resident_default(ready, hung):
    logged = []
    class Unhealthy(Worker):
        async def ensure_started(self):
            pass
    worker = Unhealthy('heavy', 'heavy', {'dinov2-small': 1.0})
    worker.ready, worker.hung = ready, hung
    assert asyncio.run(Loads(None, log=logged.append)._ready(worker, 'dinov2-small')) is False
    assert len(logged) == 1 and logged[0]['ok'] is False
    assert 'alreadyResident' not in logged[0]


def test_the_model_let_go_first_is_the_one_least_worth_keeping_per_gb_not_simply_the_oldest():
    # Cellpose-SAM used 12 minutes ago loads again in 2.5 s and holds 3.4 GB; micro-sam used 15 minutes ago takes
    # 8.8 s and holds 1.4 GB: Cellpose-SAM goes first, though micro-sam is older. A wanted model stays longest.
    loads = Loads(None)
    loads.measured = {"cellpose-sam": [{"seconds": 2.5, "memoryMb": 3520, "how": "footprint"}],
                      "micro-sam-lm": [{"seconds": 41.4, "memoryMb": 1399}, {"seconds": 8.8, "memoryMb": 1360},
                                       {"seconds": 5.2, "memoryMb": 1356}],
                      "dinov2-small": [{"seconds": 1.4, "memoryMb": 300, "how": "footprint"}]}
    now = time.monotonic()
    heavy = Worker("micromind-gpu-heavy", "heavy", {"cellpose-sam": now - 720, "micro-sam-lm": now - 900,
                                                    "dinov2-small": now - 3000})
    loads.register(heavy)
    assert [m for _w, _c, m in loads.unloadable()] == ["dinov2-small", "cellpose-sam", "micro-sam-lm"]
    loads.wanted = {("micromind-gpu-heavy", "dinov2-small")}  # the picture model of taught labels, wanted
    assert [m for _w, _c, m in loads.unloadable()] == ["cellpose-sam", "micro-sam-lm", "dinov2-small"]


def test_memory_under_pressure_lets_go_of_one_idle_model_at_a_time_and_says_why(tmp_path):
    hub, said, _day = activity_hub(tmp_path)
    logs = []
    loads = Loads(None, log=logs.append, hub=hub)
    now = time.monotonic()
    main = Worker("micromind-gpu", "main", {"edgetam": now - 3000, "sam2.1-tiny": now - 10})
    heavy = Worker("micromind-gpu-heavy", "heavy", {"cellpose-sam": now - 600, "medsiglip": now - 1200})
    follow = Worker("micromind-gpu-follow", "follow", {"edgetam-video": now - 3000})
    for w in (main, heavy, follow):
        loads.register(w)
    poor, good = {"memFreePct": "poor", "swapInsPerS": "good"}, {"memFreePct": "good", "swapInsPerS": "good"}

    async def go():
        assert await loads.ease(poor) is None  # one poor sample is not yet pressure
        assert await loads.ease(good) is None and await loads.ease(poor) is None  # nor two apart
        first = await loads.ease({"memFreePct": "fair", "swapInsPerS": "poor"})  # swapping counts as much
        assert await loads.ease(poor) is None  # one at a time
        loads._eased_at -= 31
        second = await loads.ease(poor)
        loads._eased_at -= 31
        third = await loads.ease(poor)  # only SAM 2.1 tiny is left, used 10 s ago: kept
        return first, second, third

    first, second, third = asyncio.run(go())
    assert (first, second, third) == ("medsiglip", "cellpose-sam", None)
    assert main.unloaded == [] and follow.unloaded == []
    assert [r["for"] for r in logs if r["kind"] == "model-unload"] == ["pressure", "pressure"]
    assert said[0]["text"] == "Unloaded MedSigLIP 448 to ease the computer's memory"


def test_the_wanted_set_holds_the_chosen_outline_model_and_comes_back_only_with_room_and_calm(monkeypatch):
    from orchestrator import loads as module
    from orchestrator.options import Settings

    class Starting(Worker):
        async def ensure_started(self):
            return self

        async def call(self, op, timeout=0, priority=None, **req):
            self.resident[req["model"]] = {"usedAt": time.monotonic()}
            return {"ok": True}

    main, heavy = Starting("micromind-gpu", "main", {"edgetam": 0.0}), Starting("micromind-gpu-heavy", "heavy")
    hub = SimpleNamespace(settings=Settings(outline="sam2.1-tiny", track="slide", cells="cellpose-sam", taught="off",
                                            seen="off"), gpu=main, gpu_heavy=heavy)
    free = [3100.0]  # SAM 2.1 tiny (500 MB documented) with the margin and the room to spare, not Cellpose-SAM
    loads = Loads(None, free_mb=lambda: free[0])
    loads.measured = {"cellpose-sam": [{"seconds": 2.5, "memoryMb": 3520, "peakMb": 3700, "how": "footprint"}]}
    assert [(c.name, m) for c, m in loads.defaults(hub)] == [("micromind-gpu", "sam2.1-tiny"),
                                                             ("micromind-gpu-heavy", "cellpose-sam")]
    assert asyncio.run(loads.rewarm(hub)) == ["sam2.1-tiny"]
    free[0] = 3700 + module.MARGIN_MB + module.ROOM_MB
    loads._eased_at = time.monotonic()  # a model was just let go for pressure: nothing comes back yet
    assert asyncio.run(loads.rewarm(hub)) == []
    loads._eased_at -= module.CALM_S
    assert asyncio.run(loads.rewarm(hub)) == ["cellpose-sam"]


def test_a_worker_is_named_by_its_role_and_what_is_loading_there():
    from orchestrator.gpu_client import GpuClient

    client = GpuClient("micromind-gpu-heavy")
    client._process = SimpleNamespace(pid=10, is_alive=lambda: True)
    client.loading["micro-sam-lm"] = {"since": time.monotonic()}
    client.children["micro-sam-lm"] = 11
    assert client.processes() == [
        {"pid": 10, "role": "analysis worker", "worker": "micromind-gpu-heavy", "loading": MICRO_SAM},
        {"pid": 11, "role": f"{MICRO_SAM} (analysis worker)", "worker": "micromind-gpu-heavy", "model": "micro-sam-lm",
         "loading": MICRO_SAM}]
    assert client.wire()["loading"] == {"micro-sam-lm": 0}


def test_a_step_still_loading_is_said_to_be_loading_to_the_voice():
    from orchestrator.brain.claims import hands_note

    run = ToolRun("highlight", {})
    run.progress = "loading micro-sam (usually about 2 min)"
    note = hands_note([run], [])
    assert "highlight (loading micro-sam (usually about 2 min))" in note and "not done yet" in note


def test_a_big_model_has_a_worker_of_its_own_and_letting_it_go_stops_that_worker(monkeypatch):
    # R4 (docs/design/2026-10-11-model-runtime.md): live 2026-10-11 micro-sam loaded cold for 120 s on the analysis
    # worker and every cell finder, embedding and zero-shot call waited behind it. A model whose manifest gives it a
    # host of its own (SAM 3.1, micro-sam) loads in a worker of its own; the rest share the analysis worker.
    from orchestrator.hub import Hub
    from orchestrator.operations import worker_client

    monkeypatch.setenv("MICROMIND_OWN_HOSTS", "1")
    hub = Hub.__new__(Hub)
    hub.gpu, hub.gpu_heavy, hub.hosts, hub.host_hooks, hub.own_hosts = object(), Worker("micromind-gpu-heavy", "heavy"), {}, [], True
    hub._client_class = lambda name, warm, role: Worker(name, role)  # the hub's own workers' kind
    made = []
    hub.host_hooks.append(made.append)
    for shared in ("cellpose-sam", "dinov2-small", "medsiglip", None, "never-heard-of"):
        assert worker_client(hub, "analysis", model=shared) is hub.gpu_heavy
    sam = worker_client(hub, "analysis", model="sam3.1-local")
    assert sam is not hub.gpu_heavy and sam.name == "micromind-gpu-sam3.1-local" and sam.own == "sam3.1-local"
    assert sam.role == "heavy"
    assert worker_client(hub, "analysis", model="sam3.1-local") is sam and made == [sam]  # one, made once, told
    assert worker_client(hub, "analysis", model="micro-sam-lm").own == "micro-sam-lm"
    assert sam in hub.gpu_clients()
    hub.own_hosts = False  # MICROMIND_OWN_HOSTS=0: everything on the analysis worker, as before
    assert worker_client(hub, "analysis", model="sam3.1-local") is hub.gpu_heavy

    stopped = []
    sam.stop = lambda: stopped.append(sam.name)
    sam.resident["sam3.1-local"] = {"sizeMb": 5300, "usedAt": 0.0}
    reply = asyncio.run(Loads(None).let_go(sam, "sam3.1-local"))
    assert stopped == ["micromind-gpu-sam3.1-local"] and reply == {"unloaded": ["sam3.1-local"], "freedMb": 5300, "stopped": True}
    assert "sam3.1-local" not in sam.resident
    shared = Worker("micromind-gpu-heavy", "heavy", {"cellpose-sam": 0.0})
    asyncio.run(Loads(None).let_go(shared, "cellpose-sam"))
    assert shared.unloaded == ["cellpose-sam"]  # a shared worker's model goes by the worker's own unload op
