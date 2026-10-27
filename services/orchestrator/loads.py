"""Models loading on the GPU workers: said while they load, measured, kept within memory, and the defaults
loaded before anyone waits for them.

Live 2026-10-11 02:37-02:40: "okay use that" loaded micro-sam cold, 120 s under load, then outlined in 139 s;
nothing said it was loading, the meter blamed WindowServer, and the follow worker was reported hung.

- **Said** (`told`, on the loop, in the context of the call the load is for; gpu_client.py reads the worker's
  messages, gpu_worker/loading.py), with the one name per model (names.said_impl): the request's running step
  carries "loading micro-sam (light microscopy) on this Mac, usually about 2 min" or "... for the first time"
  (ToolRun.progress), which the answer and the voice are given as what is still being done (brain/claims.py,
  loop.py); the user is told when it begins and when it ends (`say`, the one place: a line of Activity,
  activity.model_loading, running while it loads and done or failed when it ends), unless the load is a preload
  or quick; the interaction log gets a `model-load` record either way.
- **Measured:** each load's seconds and memory are kept in loads.json beside the fine-tuned models (paths.models;
  the last few per model), and "usually" is their median.
- **Memory** (`before_load`, awaited by a call whose model is not loaded there): the most the model's load took (its
  peak: a checkpoint is often held whole before its weights move to the GPU), else its measured size, else its
  documented one, else 2 GB, is checked against the memory free now. Sizes are the kernel's count of what the worker
  holds (gpu_worker/footprint.py, `how` "footprint"); the resident sizes measured before 2026-10-11 missed Metal's
  memory (MedSigLIP "0 MB", SAM 2.1 tiny "9 MB") and are read only for a model with none newer. When short, the least recently used
  model of the analysis or Molmo worker (or one of the click worker's besides EdgeTAM) is unloaded (worker op
  `unload`), logged (`model-unload`) and said; never the click worker's EdgeTAM or the follower's model.
  Inside `sparing` (the capability check's Check now, model_checks.py) nothing is unloaded: a load that does not fit
  is refused (NoRoom) and the check says it was not checked (live 2026-10-11 16:31: Check now's Molmo, 12.5 GB, pushed
  out the live app's Cellpose-SAM and DINOv2; swap-ins reached 6,387/s, and 7,583/s when they loaded again).
- **Which goes first** (`unloadable`): the model least worth keeping per GB it holds, `worth`: the seconds bringing it
  back would cost (measured here), weighed by how likely it is wanted soon (the wanted set below always; any other
  falling with the time since it was last used, `LIKELY_S`). Not simply the oldest: Cellpose-SAM used 12 minutes ago
  (2.5 s to load again here, 3.4 GB) goes before micro-sam used 15 minutes ago (8.8 s usually, 1.4 GB).
- **Under pressure** (`ease`, every meter sample): when the computer's memory is rated poor (the meter's free memory or
  swapping, contract/overlay.json) for `PRESSURE_SAMPLES` samples in a row, the idle model least worth keeping is let
  go, at most one every `PRESSURE_EVERY_S`, logged (`model-unload`, for "pressure") and said; never one used in the last
  `IDLE_S`, never the click worker's EdgeTAM or the follower's.
- **Ready** (`preload`, then `rewarm`): once frames have flowed for a while, the wanted set loads in the background, one
  at a time, at their workers' low priority, quietly (`model-preload` logged): the follower's EdgeTAM video model, the
  outline model chosen when it runs on this Mac (SAM 2.1 tiny, micro-sam, SAM 3.1: the Models menu's choice, and the
  day's restored one after a restart), the default cell finder (CELLS) and picture model (EMBED). One let go since
  is loaded again only with `ROOM_MB` to spare beside its peak, never within `CALM_S` of a model let go for pressure.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import math
import statistics
import time
from pathlib import Path

from gpu_worker.loading import DEFAULT_MB, DOCUMENTED_MB, DOCUMENTED_PEAK_MB, limit_s, name as worker_name


def model_name(model: str | None) -> str:
    """A model as the app says it to the user: the one name per model (names.said_impl: "micro-sam (light
    microscopy) on this Mac"), else the worker's own name for one no operation offers ("MedSigLIP")."""
    if model == "cellpose-file":  # a Cellpose model of the user's own (mine.py): its worker loads it by its runner
        return "your Cellpose model"
    try:
        from .names import said_impl

        said = said_impl(model)
    except Exception:
        said = ""
    return said if said and said != model else worker_name(model)


KEPT = 5  # loads kept per model; "usually" is their median
NOTICE_S = 3.0  # a load expected to take less than this is not said in Activity (a measured quick one)
MARGIN_MB = 512.0  # free memory left after a load, beside its size
UNLOAD_WAIT_S = 20.0  # how long the guard waits for one unload before going on (said either way)
PRELOAD_AFTER_S = 8.0  # frames flowing this long before the defaults load
CELL_FINDERS = ("cellpose-sam", "micro-sam-lm")  # CELLS options that load a model of their own on the analysis worker
# The worker class each role's worker is (operations.worker_client), for the manifests' `protected_on`.
ROLE_CLASS = {"main": "interactive", "heavy": "analysis", "follow": "follow", "molmo": "molmo"}


def _local_outline() -> dict[str, str]:
    """OUTLINE options that load a model of their own on this Mac, and the worker class they run on, from the models'
    manifests (catalog/models): every outline model but the one the click worker keeps loaded."""
    from gpu_worker.manifests import local

    return {impl.split("/", 1)[1]: m.workers[0] for m in local().values() for impl in m.implementations
            if impl.startswith("outline/") and "interactive" not in m.protected_on}


LOCAL_OUTLINE = _local_outline()
LIKELY_S = 600.0  # a model last used this long ago counts as about a third as likely to be wanted soon (e^-1)
RELOAD_S = 10.0  # what bringing back a model costs when no load of it has been measured here
PRESSURE_SAMPLES = 2  # meter samples in a row with memory rated poor before a model is let go for it
PRESSURE_EVERY_S = 30.0  # at most one model let go for pressure in this long
IDLE_S = 60.0  # a model used this recently is never let go for pressure
ROOM_MB = 2048.0  # free memory left beside a wanted model's peak before it is loaded again in the background
CALM_S = 300.0  # after a model is let go for pressure, nothing is loaded again in the background for this long
REWARM_EVERY_S = 60.0


def duration(seconds: float) -> str:
    """"about 40 s", "about 2 min"."""
    if seconds < 90:
        return f"about {max(1, round(seconds))} s"
    return f"about {round(seconds / 60)} min"


def took(seconds: float) -> str:
    """"12 s", "1 min 58 s"."""
    seconds = max(0, round(seconds))
    return f"{seconds} s" if seconds < 60 else f"{seconds // 60} min {seconds % 60} s"


def gb(mb: float) -> str:
    """"12.1 GB"."""
    return f"{mb / 1024:.1f} GB"


class NoRoom(RuntimeError):
    """A load asked for inside `sparing` that does not fit in the memory free now: refused before it is sent, since
    making room would mean unloading what the live view keeps loaded."""

    def __init__(self, model: str, need_mb: float, free_mb: float, kept: list[str] | None = None) -> None:
        self.model, self.need_mb, self.free_mb = model, float(need_mb), float(free_mb)
        self.kept = list(kept or [])  # the loaded models it would have pushed out, kept
        super().__init__(f"{model_name(model)} needs {gb(need_mb)} of memory and {gb(free_mb)} is free")


# Loads that never push out a loaded model (a capability check inside the live app): the list of refusals made, else None.
_SPARING: contextvars.ContextVar = contextvars.ContextVar("loads_sparing", default=None)


@contextlib.contextmanager
def sparing():
    """Inside it (and the tasks it starts), a load that does not fit in the memory free now is refused (NoRoom) rather
    than making room by unloading another model. Yields the refusals made inside it: the caller knows a refusal happened
    however deep the load was (the runner turns its error into its own words)."""
    refused: list[NoRoom] = []
    token = _SPARING.set(refused)
    try:
        yield refused
    finally:
        _SPARING.reset(token)


class Loads:
    def __init__(self, path: Path | None, log=lambda record: None, hub=None, free_mb=None) -> None:
        self.path = Path(path) if path is not None else None  # MICROMIND_HOME/models/loads.json
        self.log = log
        self.hub = hub  # whose Activity says loads (activity.py); None: nothing is said
        if free_mb is None:
            from .machine import free_mb
        self.free_mb = free_mb
        self.clients: list = []
        self.measured: dict[str, list[dict]] = self._read()
        # (worker, model) loads said as beginning -> their Activity line, so their end is said on the same line
        self._noticed: dict[tuple[str, str], str] = {}
        self._guard: asyncio.Lock | None = None
        self._writing: asyncio.Future | None = None
        self.wanted: set[tuple[str, str]] = set()  # (worker, model) of the wanted set, as `defaults` last gave it
        self._poor = 0  # meter samples in a row with memory rated poor
        self._eased_at: float | None = None  # when a model was last let go for pressure (monotonic)

    # --- measurements ---------------------------------------------------------------
    def _read(self) -> dict[str, list[dict]]:
        if self.path is None or not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
            return {k: list(v)[-KEPT:] for k, v in (data.get("models") or {}).items() if isinstance(v, list)}
        except (OSError, ValueError, AttributeError):
            return {}

    def _write(self, snapshot: dict) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"models": snapshot}, indent=1))
            tmp.replace(self.path)
        except OSError:
            pass  # a measurement not kept never fails a load

    def usual_s(self, model: str) -> float | None:
        """How long a load of it usually takes here (s), or None when it has not been measured on this Mac."""
        times = [m["seconds"] for m in self.measured.get(model, []) if isinstance(m.get("seconds"), (int, float))]
        return float(statistics.median(times)) if times else None

    def _sized(self, model: str) -> list[dict]:
        """Its loads with a size: measured by footprint when there are any, else the older resident sizes."""
        rows = [m for m in self.measured.get(model, []) if isinstance(m.get("memoryMb"), (int, float)) and m["memoryMb"] > 0]
        return [m for m in rows if m.get("how") == "footprint"] or rows

    def size_mb(self, model: str) -> float:
        """Its measured size here, else its documented one, else DEFAULT_MB."""
        sizes = [m["memoryMb"] for m in self._sized(model)]
        if sizes:
            return float(max(sizes))
        return float(DOCUMENTED_MB.get(model, DEFAULT_MB))

    def peak_mb(self, model: str) -> float:
        """The most its load has taken here (its peak, never less than its size), else its manifest's measured peak
        (catalog/models), else its size as `size_mb` gives it."""
        peaks = [m["peakMb"] for m in self._sized(model) if isinstance(m.get("peakMb"), (int, float))]
        if not peaks and not self._sized(model):
            peaks = [DOCUMENTED_PEAK_MB.get(model, 0)]
        return max([self.size_mb(model), *map(float, peaks)])

    def state_words(self, client, model: str) -> str:
        """Whether a model is ready on its worker, for the models choosing one (operations.alternatives_text): "loaded",
        "loading now", "not loaded: about 9 s to load" (measured here), or "not loaded, never loaded on this Mac yet"."""
        if model in (getattr(client, "loading", None) or {}):
            return "loading now"
        if model in (getattr(client, "resident", None) or {}):
            return "loaded"
        usual = self.usual_s(model)
        return f"not loaded: {duration(usual)} to load" if usual is not None else "not loaded, never loaded on this Mac yet"

    def words(self, model: str) -> str:
        """What a request's step says while it loads."""
        usual = self.usual_s(model)
        if usual is None:
            return f"loading {model_name(model)} for the first time"
        return f"loading {model_name(model)}, usually {duration(usual)}"

    # --- told by a worker's client -------------------------------------------------
    def register(self, client) -> None:
        if client not in self.clients:
            self.clients.append(client)
            client.loads = self

    def told(self, client, reply: dict) -> None:
        """A worker's load message, on the loop, in the context of the call it is for (gpu_client._replied)."""
        from .gpu_client import quiet

        state, model = reply.get("load"), reply.get("model") or ""
        quietly = quiet()
        run = _current_run()
        key = (client.name, model)
        record = {"kind": "model-load", "state": state, "model": model, "worker": client.name,
                  **{k: reply[k] for k in ("seconds", "memoryMb", "peakMb", "how", "limitS", "freedMb", "error", "pid") if k in reply}}
        if state == "loading":
            usual = self.usual_s(model)
            record.update(usualS=usual, first=usual is None, quiet=quietly)
            words = self.words(model)
            if run is not None:
                run.report_progress(words)
            if not quietly and self.hub is not None and (usual is None or usual >= NOTICE_S):
                line = self.say(model, "loading", client, reply, usual=usual)
                if line:
                    self._noticed[key] = line
        elif state in ("loaded", "failed"):
            if run is not None and run.progress and model_name(model) in run.progress:
                run.report_progress(None)
            if state == "loaded" and model != "test-load":
                self._measured(model, reply, client)
            line = self._noticed.pop(key, None)
            if line is not None:
                self.say(model, state, client, reply, line=line)
        self.log(record)
        machine = getattr(self.hub, "machine", None)
        if machine is not None:
            machine.request_sample()

    def say(self, model: str, event: str, client, reply: dict, *, line: str | None = None, usual: float | None = None,
            **facts) -> str | None:
        """The one place a load, its end or an unload is said to the user: a line of Activity (activity.py), running
        while it loads ("Loading micro-sam (light microscopy) on this Mac (usually about 2 min)"), then done or failed
        on the same line (`line`), and an unload with what it made room for. Returns a loading line's key."""
        if self.hub is None:
            return None
        from . import activity

        try:
            if event == "loading":
                return activity.model_loading(self.hub, model_name(model), expected_s=usual, first=usual is None,
                                              key=f"model:{client.name}:{model}:{time.time_ns()}")
            if event in ("loaded", "failed") and line:
                activity.model_loaded(self.hub, line, ok=event == "loaded", seconds=reply.get("seconds"),
                                      memory_mb=reply.get("memoryMb"), why=reply.get("error") or "it failed")
            elif event == "unloaded":
                room = facts.get("for")
                why = (f"to make room for {model_name(room)}" if room
                       else "to ease the computer's memory" if facts.get("pressure") else None)
                activity.model_unloaded(self.hub, model_name(model), why=why)
        except Exception:
            pass  # saying it never fails the load
        return None

    def _measured(self, model: str, reply: dict, client) -> None:
        kept = self.measured.setdefault(model, [])
        kept.append({"seconds": reply.get("seconds"), "memoryMb": reply.get("memoryMb"), "worker": client.name,
                     "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                     **{k: reply[k] for k in ("peakMb", "how") if reply.get(k) is not None}})
        del kept[:-KEPT]
        snapshot = json.loads(json.dumps(self.measured))
        try:
            asyncio.get_running_loop().run_in_executor(None, self._write, snapshot)  # off the loop
        except RuntimeError:
            self._write(snapshot)

    # --- memory -------------------------------------------------------------------
    def worth(self, client, model: str, held: dict, now: float) -> float:
        """What keeping a loaded model is worth per GB it holds: the seconds a reload would cost (measured here, else
        RELOAD_S), weighed by how likely it is wanted soon (1 in the wanted set, else falling with the time since it was
        last used)."""
        reload_s = self.usual_s(model) or RELOAD_S
        wanted = (getattr(client, "name", ""), model) in self.wanted
        likely = 1.0 if wanted else math.exp(-max(0.0, now - (held.get("usedAt") or 0.0)) / LIKELY_S)
        return reload_s * likely / (max(self.size_mb(model), 64.0) / 1024)

    def unloadable(self, loading_client=None, loading_model: str | None = None) -> list[tuple]:
        """(worth, client, model) of every loaded model that may be let go of, the least worth keeping first (equal
        worth: the least recently used): every worker's models but those its manifest protects there (the click
        worker's EdgeTAM, the follower's model: catalog/models, `protected_on`)."""
        from gpu_worker.manifests import get

        out = []
        now = time.monotonic()
        for client in self.clients:
            role = getattr(client, "role", None)
            if role not in ROLE_CLASS or not getattr(client, "ready", False):
                continue
            for model, held in list(client.resident.items()):
                manifest = get(model)
                if (manifest is not None and ROLE_CLASS[role] in manifest.protected_on) or model == "test-load":
                    continue
                if client is loading_client and model == loading_model:
                    continue
                out.append((self.worth(client, model, held, now), held.get("usedAt") or 0.0, client, model))
        return [(worth, client, model) for worth, _used, client, model in sorted(out, key=lambda o: (o[0], o[1]))]

    async def before_load(self, client, model: str) -> None:
        """Room for `model` before it loads on `client`: when the memory free now is less than its size, the least
        recently used models elsewhere are unloaded until it fits (or none is left), each logged and said. Inside
        `sparing` nothing is unloaded: the load is refused (NoRoom)."""
        if model in client.loading or model == "test-load":
            return
        if self._guard is None:
            self._guard = asyncio.Lock()
        async with self._guard:
            need = self.peak_mb(model) + MARGIN_MB
            free = await asyncio.to_thread(self.free_mb)
            if free is None or free >= need:
                return
            if _SPARING.get() is not None:
                raise self._refused(client, model, need, free)
            freed = []
            for _used, victim_client, victim in self.unloadable(client, model):
                try:
                    reply = await self.let_go(victim_client, victim)
                    ok = bool(reply.get("unloaded"))
                except Exception as exc:  # a worker busy past the wait, or gone: said, and the load goes on
                    reply, ok = {"error": f"{type(exc).__name__}: {exc}"[:200]}, False
                self.log({"kind": "model-unload", "model": victim, "worker": victim_client.name, "for": model,
                          "freeMb": round(free), "needMb": round(need), "ok": ok, **{k: reply[k] for k in ("freedMb", "error") if k in reply}})
                if ok:
                    freed.append(victim)
                    self.say(victim, "unloaded", victim_client, reply, **{"for": model})
                free = await asyncio.to_thread(self.free_mb)
                if free is None or free >= need:
                    return
            self.log({"kind": "memory-short", "model": model, "worker": client.name, "freeMb": round(free or 0),
                      "needMb": round(need), "unloaded": freed})

    async def fits(self, client, model: str) -> None:
        """Inside `sparing`, before a worker is even started for `model`: NoRoom when it is not loaded there and would
        not fit in the memory free now (the check skips it rather than start a worker it can't load in); otherwise, and
        outside `sparing`, nothing."""
        if _SPARING.get() is None or model in (getattr(client, "resident", None) or {}) or model == "test-load":
            return
        need = self.peak_mb(model) + MARGIN_MB
        free = await asyncio.to_thread(self.free_mb)
        if free is not None and free < need:
            raise self._refused(client, model, need, free)

    def _refused(self, client, model: str, need: float, free: float) -> NoRoom:
        """A load `sparing` refuses: noted in its list and logged with what stayed loaded."""
        kept = [m for _used, _client, m in self.unloadable(client, model)]
        refusal = NoRoom(model, need, free, kept)
        refused = _SPARING.get()
        if refused is not None:
            refused.append(refusal)
        self.log({"kind": "memory-short", "model": model, "worker": getattr(client, "name", ""), "freeMb": round(free),
                  "needMb": round(need), "unloaded": [], "spared": kept})
        return refusal

    async def let_go(self, client, model: str) -> dict:
        """Let go of one model: the worker's `unload` op, or, for a worker that is its alone (hub.host_for), stopping
        that worker, which gives every byte back (in-process unloading keeps what the allocator holds)."""
        if getattr(client, "own", None) == model:
            held = (client.resident.get(model) or {}).get("sizeMb") or 0
            await asyncio.to_thread(client.stop)
            client.resident.pop(model, None)
            return {"unloaded": [model], "freedMb": held, "stopped": True}
        return await client.call("unload", timeout=UNLOAD_WAIT_S, priority="interactive", model=model)

    # --- under pressure -------------------------------------------------------------
    async def ease(self, levels: dict) -> str | None:
        """One meter sample's ratings (machine.levels): after PRESSURE_SAMPLES in a row with free memory or swapping
        rated poor, the idle model least worth keeping is let go (one every PRESSURE_EVERY_S at most), logged and said.
        Returns the model let go, if any."""
        poor = "poor" in (levels.get("memFreePct"), levels.get("swapInsPerS"))
        self._poor = self._poor + 1 if poor else 0
        now = time.monotonic()
        if self._poor < PRESSURE_SAMPLES or (self._eased_at is not None and now - self._eased_at < PRESSURE_EVERY_S):
            return None
        for _worth, client, model in self.unloadable():
            held = client.resident.get(model) or {}
            if model in getattr(client, "loading", {}) or now - (held.get("usedAt") or 0.0) < IDLE_S:
                continue
            self._eased_at = now
            try:
                reply = await self.let_go(client, model)
                ok = bool(reply.get("unloaded"))
            except Exception as exc:  # a worker busy past the wait, or gone: logged, tried again next time
                reply, ok = {"error": f"{type(exc).__name__}: {exc}"[:200]}, False
            self.log({"kind": "model-unload", "model": model, "worker": client.name, "for": "pressure", "levels": levels,
                      "ok": ok, **{k: reply[k] for k in ("freedMb", "error") if k in reply}})
            if ok:
                self.say(model, "unloaded", client, reply, pressure=True)
                return model
            return None
        return None

    # --- the defaults, ready ------------------------------------------------------
    def defaults(self, hub) -> list[tuple]:
        """(client, model) to load before anyone waits for them (the wanted set): the follower's model while
        following is on, the outline model chosen when it runs on this Mac (not EdgeTAM, which the click worker loads
        as it starts), the default cell finder and the picture model of taught labels and things seen before."""
        settings = hub.settings
        jobs = []
        follow = getattr(hub, "gpu_follow", None)
        if follow is not None and getattr(settings, "track", "") == "objects":
            jobs.append((follow, "edgetam-video"))
        outline = getattr(settings, "outline", "edgetam")
        if outline in LOCAL_OUTLINE:
            from .operations import worker_client

            client = worker_client(hub, LOCAL_OUTLINE[outline], start=False)
            if client is not None:
                jobs.append((client, outline))
        heavy = getattr(hub, "gpu_heavy", None)
        if heavy is not None:
            host_for = getattr(hub, "host_for", None) or (lambda model: heavy)  # a big model's own worker (hub.py)
            if getattr(settings, "cells", "") in CELL_FINDERS:
                jobs.append((host_for(settings.cells), settings.cells))
            if "on" in (getattr(settings, "taught", "off"), getattr(settings, "seen", "off")):
                jobs.append((host_for(settings.embed), settings.embed))
        self.wanted = {(getattr(c, "name", ""), m) for c, m in jobs}
        return jobs

    async def preload(self, hub) -> None:
        """Once frames have flowed for PRELOAD_AFTER_S with the click worker ready, the defaults load one at a
        time, quietly, at their workers' priority; each is logged with how long it took."""
        first = None
        while True:
            info = hub.latest_info
            ready = getattr(hub.gpu, "ready", False) if getattr(hub, "gpu", None) is not None else False
            if info is not None and ready:
                first = first or time.monotonic()
                if time.monotonic() - first >= PRELOAD_AFTER_S:
                    break
            await asyncio.sleep(1.0)
        for client, model in self.defaults(hub):
            await self._ready(client, model)

    async def keep(self, hub, every_s: float = 5.0) -> None:
        """The app's one task for what stays loaded: the preload, then each meter sample read for pressure (`ease`)
        and the wanted set loaded again every REWARM_EVERY_S (`rewarm`)."""
        from .machine import levels

        await self.preload(hub)
        last_rewarm = time.monotonic()
        while True:
            await asyncio.sleep(every_s)
            machine = getattr(hub, "machine", None)
            try:
                if machine is not None:
                    await self.ease(levels(machine.snapshot()))
                if time.monotonic() - last_rewarm >= REWARM_EVERY_S:
                    last_rewarm = time.monotonic()
                    await self.rewarm(hub)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # what stays loaded is looked after again next sample; never the app's end
                self.log({"kind": "model-keep", "error": f"{type(exc).__name__}: {exc}"[:200]})

    async def rewarm(self, hub) -> list[str]:
        """The wanted set loaded again where one is missing (let go for room or pressure, or chosen since), one at a
        time, only with ROOM_MB free beside its peak and never within CALM_S of a model let go for pressure."""
        loaded = []
        for client, model in self.defaults(hub):
            if model in client.resident or model in getattr(client, "loading", {}):
                continue
            if self._eased_at is not None and time.monotonic() - self._eased_at < CALM_S:
                break
            free = await asyncio.to_thread(self.free_mb)
            if free is None or free < self.peak_mb(model) + MARGIN_MB + ROOM_MB:
                continue
            if await self._ready(client, model, again=True):
                loaded.append(model)
        return loaded

    async def _ready(self, client, model: str, again: bool = False) -> bool:
        """One model of the wanted set loaded in the background, quietly, logged with how long it took."""
        t0 = time.monotonic()
        already_resident = False
        try:
            await client.ensure_started()
            if not client.ready or getattr(client, "hung", ""):
                raise RuntimeError("the model worker is not ready for its preload check")
            already_resident = model in client.resident and model not in getattr(client, "loading", {})
            if not already_resident:
                await client.call("load", timeout=limit_s(model) + 30.0, priority="background", model=model, _quiet=True)
            ok, error = True, None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            ok, error = False, f"{type(exc).__name__}: {exc}"[:200]
        self.log({"kind": "model-preload", "model": model, "worker": client.name, "ok": ok,
                  "seconds": round(time.monotonic() - t0, 1), **({"again": True} if again else {}),
                  **({"alreadyResident": True} if already_resident else {}),
                  **({"error": error} if error else {})})
        return ok and not already_resident


def _current_run():
    """The brain's tool run the load is for (its step carries the words), or None (a click, a preload)."""
    try:
        from .brain.registry import CURRENT_RUN

        return CURRENT_RUN.get()
    except Exception:
        return None
