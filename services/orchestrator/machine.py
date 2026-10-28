"""How the computer is doing, for the studio's performance meter (apps/studio/src/perf.ts):
what slows the live view besides the app itself. Other programs wanting the CPU make every
frame wait its turn; memory swapped to disk stops a process until the disk gives it back; a
nearly full disk leaves swap no room to grow (2026-10-06: a sim run at load 200-300 on 14
cores, swap 8.8 of 9.2 GB, the disk 95% full, gave 16 fps and clicks of 910 ms at p95).

Sampled on its own thread every SAMPLE_S from the app's start (load average, one sysctl, statvfs:
about a millisecond), never on the frame path. The programs using the most are listed only
while the meter's panel is open (`top`, one `ps`). Plain facts: the studio rates them against
the contract's ideals (contract/overlay.json "performance").

The app's own workers are sampled on every tick (one `ps -p` of their pids, with their role and
whether a model is loading there: `ours`), so the meter can say when the load is ours ("micro-sam is
loading: the live view is slowed") and the logs keep it (live 2026-10-11: the meter blamed
WindowServer while micro-sam loaded at load 142). Each time a reading crosses one of the contract's
lines (good, fair, poor) a `machine` record is logged with every reading and our workers, so a slow
stretch in the interaction log says what the computer was doing then.

Swapping is the pages swapped back in a second over the last `windowS` (the contract's, 30 s), and its level leaves
a worse one only once past the line by the contract's `hysteresis` (perf.ts rates it the same way): live 2026-10-11
16:32-16:35 one 5 s sample a time flipped it between good, fair and poor every 5 s (1.6, 31, 9.3, 58.7 pages/s...).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from . import contract

SAMPLE_S = 5.0
CLICKS_KEPT = 20  # the click time is the p95 of this many recent clicks
TOP_N = 6


def parse_vm_stat(text: str) -> dict[str, int]:
    """macOS `vm_stat`: its counters by name ("Swapins": pages swapped back in since boot, "Swapouts" ...)."""
    out = {}
    for line in text.splitlines():
        name, _, value = line.partition(":")
        value = value.strip().rstrip(".")
        if value.isdigit():
            out[name.strip()] = int(value)
    return out


def swap_counts() -> tuple[int, int] | None:
    """Pages swapped in and out since boot (macOS vm_stat, Linux /proc/vmstat), or None where not said."""
    if sys.platform == "darwin":
        try:
            done = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2)
        except (OSError, subprocess.SubprocessError):
            return None
        counts = parse_vm_stat(done.stdout)
        if "Swapins" in counts and "Swapouts" in counts:
            return counts["Swapins"], counts["Swapouts"]
        return None
    try:
        rows = dict(line.split()[:2] for line in Path("/proc/vmstat").read_text().splitlines() if line.strip())
        return int(rows["pswpin"]), int(rows["pswpout"])
    except (OSError, KeyError, ValueError):
        return None


def parse_swapusage(text: str) -> tuple[float, float] | None:
    """macOS `sysctl -n vm.swapusage` ("total = 9216.00M  used = 8846.38M ...") as (total, used) MB."""
    found = dict(re.findall(r"(total|used)\s*=\s*([\d.]+)M", text))
    if "total" not in found or "used" not in found:
        return None
    return float(found["total"]), float(found["used"])


def parse_meminfo(text: str) -> dict:
    """Linux /proc/meminfo: free memory %, swap total and used MB."""
    kb = {k: float(v) for k, v in re.findall(r"^(\w+):\s+(\d+)", text, re.M)}
    out: dict = {}
    if kb.get("MemTotal"):
        out["memFreePct"] = round(100 * kb.get("MemAvailable", kb.get("MemFree", 0)) / kb["MemTotal"])
    if "SwapTotal" in kb:
        out["swapTotalMb"] = round(kb["SwapTotal"] / 1024)
        out["swapUsedMb"] = round((kb["SwapTotal"] - kb.get("SwapFree", 0)) / 1024)
    return out


def _sysctl(*names: str) -> list[str]:
    try:
        done = subprocess.run(["sysctl", "-n", *names], capture_output=True, text=True, timeout=2)
        return done.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []


def label(args: str) -> str:
    """A program as a person knows it, from its command line."""
    words = args.split()
    first = words[0] if words else args
    if ".simruntime/" in args:  # the runtime's path has a space in it ("iOS 26.5.simruntime")
        rest = args.split(".simruntime/", 1)[1].split()
        return f"iOS Simulator: {Path(rest[0]).name if rest else 'runtime'}"
    if "iphone_bridge_server" in args:
        return "Camera bridge"
    if "orchestrator.server" in args:
        port = re.search(r"--port\s+(\d+)", args)
        return f"Hooke server{' :' + port.group(1) if port else ''}"
    if re.search(r"/Python$|python3?(\.\d+)?$", first):
        module = re.search(r"\s-m\s+(\S+)", args)
        if module:
            return f"Python: {module.group(1)}"
        script = next((Path(a).name for a in words[1:] if a.endswith(".py")), None)
        return f"Python: {script}" if script else "Python"
    app = re.search(r"/([^/]+)\.app/", first)
    if app:
        return app.group(1)
    return Path(first).name[:40]


def parse_ps(text: str, own: int, n: int = TOP_N, names: dict | None = None) -> list[dict]:
    """`ps -Ao pid=,ppid=,pcpu=,rss=,args=` as the n programs using the most CPU, each with its
    share of one core (%), its memory (MB) and whether it is HookeUI's (this process,
    what it started, or the camera bridge). `names`: our own workers by pid, named by their role
    ("HookeUI: analysis worker (loading micro-sam)") instead of "Python"."""
    names = names or {}
    rows = []
    for line in text.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]), float(parts[2]), float(parts[3]), parts[4].strip()))
        except ValueError:
            continue
    parent = {pid: ppid for pid, ppid, *_ in rows}
    command = {pid: args for pid, _, _, _, args in rows}

    def ours(pid: int, args: str) -> bool:
        """This process, the camera bridge, or anything either started (the GPU workers, the
        bridge's ffmpeg)."""
        for _ in range(20):
            if pid == own or "iphone_bridge_server" in command.get(pid, ""):
                return True
            if pid not in parent or parent[pid] == pid:
                return False
            pid = parent[pid]
        return False

    rows.sort(key=lambda r: r[2], reverse=True)
    return [{"name": names.get(pid) or label(args), "cpu": round(cpu), "memMb": round(rss / 1024),
             "ours": pid in names or ours(pid, args)}
            for pid, _, cpu, rss, args in rows[:n]]


def parse_pids(text: str) -> dict[int, tuple[float, float]]:
    """`ps -o pid=,pcpu=,rss= -p ...` as pid -> (share of one core %, memory MB)."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        try:
            out[int(parts[0])] = (float(parts[1]), float(parts[2]) / 1024)
        except ValueError:
            continue
    return out


def worker_name(worker: dict) -> str:
    """One of our workers as the meter names it."""
    name = f"HookeUI: {worker.get('role') or 'worker'}"
    if worker.get("loading"):
        name += f" (loading {worker['loading']})"
    return name


# The contract's lines for the computer (contract/overlay.json "performance"; perf.ts rates the same way).
_P = contract.OVERLAY["performance"]
LINES = {"loadPerCore": ("most", _P["loadPerCore"]), "memFreePct": ("least", _P["memFreePct"]),
         "swapInsPerS": ("most", _P["swapInsPerS"]), "diskFreeGb": ("least", _P["diskFreeGb"])}
SWAP_WINDOW_S = float(_P["swapInsPerS"]["windowS"])  # swapping is a rate over this long, not one sample's
RANK = {"good": 0, "fair": 1, "poor": 2}


def _rated(value: float, way: str, line: dict) -> str:
    if way == "most":
        return "good" if value <= line["good"] else "fair" if value <= line["fair"] else "poor"
    return "good" if value >= line["good"] else "fair" if value >= line["fair"] else "poor"


def levels(state: dict, was: dict | None = None) -> dict:
    """Each of the computer's readings as the meter rates it: good, fair or poor (missing ones left out). `was`: the
    levels before; a line with a `hysteresis` leaves a worse level only once the reading is past the line it crossed by
    that share of it (lower is better: at most hysteresis x the line), so a reading that hovers at a line holds."""
    values = {"memFreePct": state.get("memFreePct"), "diskFreeGb": state.get("diskFreeGb")}
    if state.get("load1") is not None and state.get("cores"):
        values["loadPerCore"] = state["load1"] / state["cores"]
    # Swapping now, not how full swap is: macOS grows swap and keeps it near full long after memory ran short, so a
    # "used" share was red all of 2026-10-11 while nothing waited; pages swapped back in are programs waiting on disk.
    values["swapInsPerS"] = state.get("swapInsPerS")
    out = {}
    for key, value in values.items():
        if value is None:
            continue
        way, line = LINES[key]
        out[key] = _rated(value, way, line)
        before, margin = (was or {}).get(key), line.get("hysteresis")
        if margin and before in RANK and RANK[out[key]] < RANK[before]:
            back = _rated(value / margin if way == "most" else value * margin, way, line)
            out[key] = back if RANK[back] < RANK[before] else before
    return out


def free_mb() -> float | None:
    """Memory free now (MB), as the system counts what it can give without swapping, or None."""
    try:
        import psutil  # not a declared dependency: used when it is there

        return psutil.virtual_memory().available / 2**20
    except Exception:
        pass
    if sys.platform == "darwin":
        lines = _sysctl("kern.memorystatus_level", "hw.memsize")
        try:
            return int(lines[0]) / 100 * int(lines[1]) / 2**20
        except (IndexError, ValueError):
            return None
    try:
        kb = {k: float(v) for k, v in re.findall(r"^(\w+):\s+(\d+)", Path("/proc/meminfo").read_text(), re.M)}
    except OSError:
        return None
    value = kb.get("MemAvailable", kb.get("MemFree"))
    return None if value is None else value / 1024


class Machine:
    def __init__(self, disk_path: str | os.PathLike = "/", sample_s: float = SAMPLE_S) -> None:
        self.disk_path = str(disk_path)
        self.sample_s = sample_s
        self.state: dict = {}
        self.click_ms: deque[float] = deque(maxlen=CLICKS_KEPT)
        # Our own workers: () -> [{"pid", "role", "loading"?}] (the hub's GPU clients and what they started).
        self.workers = None
        self.log = None  # (record) -> None, on the loop: a `machine` record when a reading crosses a line
        self.loop = None
        self._levels: dict = {}
        self._swaps: deque[tuple[int, int, float]] = deque()  # swap counters (in, out) and when read, over the window
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._lifecycle = threading.Lock()
        self._stopped = threading.Event()
        self._resample = threading.Event()

    def start(self, loop=None) -> None:
        """Sample during the owning app's lifetime, every SAMPLE_S."""
        with self._lifecycle:
            if loop is not None:
                self.loop = loop
            if self._thread is None:
                self._stopped.clear()
                self._resample.clear()
                self._thread = threading.Thread(target=self._run, name="machine", daemon=True)
                self._thread.start()

    def stop(self) -> None:
        """Finish the current OS probe and join the sampler; async owners call off-loop."""
        with self._lifecycle:
            self._stopped.set()
            self._resample.set()
            if self._thread is not None:
                self._thread.join()
                self._thread = None
            self.loop = None

    def snapshot(self) -> dict:
        """The latest sample as it is (for a loop-lag record)."""
        with self._lock:
            return dict(self.state)

    def ours(self) -> list[dict]:
        """Our own workers now, each with its share of the CPU and its memory (one `ps -p`)."""
        workers = []
        if self.workers is not None:
            try:
                workers = [dict(w) for w in self.workers() if w.get("pid")]
            except Exception:
                workers = []
        if not workers:
            return []
        try:
            done = subprocess.run(["ps", "-o", "pid=,pcpu=,rss=", "-p", ",".join(str(w["pid"]) for w in workers)],
                                  capture_output=True, text=True, timeout=2)
            usage = parse_pids(done.stdout)
        except (OSError, subprocess.SubprocessError):
            usage = {}
        out = []
        for w in workers:
            if w["pid"] not in usage:
                continue  # gone since
            cpu, mem = usage[w["pid"]]
            out.append({**w, "name": worker_name(w), "cpu": round(cpu), "memMb": round(mem)})
        return out

    def sample(self) -> dict:
        state: dict = {"cores": os.cpu_count() or 1, "at": round(time.time(), 1)}
        try:
            state["load1"], state["load5"], _ = (round(v, 1) for v in os.getloadavg())
        except OSError:
            pass
        try:
            disk = shutil.disk_usage(self.disk_path)
            state["diskFreeGb"], state["diskTotalGb"] = round(disk.free / 1e9), round(disk.total / 1e9)
        except OSError:
            pass
        if sys.platform == "darwin":
            lines = _sysctl("vm.swapusage", "kern.memorystatus_level", "hw.perflevel0.physicalcpu")
            swap = parse_swapusage(lines[0]) if lines else None
            if swap:
                state["swapTotalMb"], state["swapUsedMb"] = round(swap[0]), round(swap[1])
            if len(lines) > 1 and lines[1].strip().isdigit():
                state["memFreePct"] = int(lines[1])  # what `memory_pressure` calls the free percentage
            if len(lines) > 2 and lines[2].strip().isdigit():
                state["perfCores"] = int(lines[2])
        else:
            try:
                state.update(parse_meminfo(Path("/proc/meminfo").read_text()))
            except OSError:
                pass
        counts, now = swap_counts(), time.monotonic()
        if counts is not None:
            rates = self.swap_rates(counts, now)
            if rates is not None:
                state["swapInsPerS"], state["swapOutsPerS"] = rates
        ours = self.ours()
        if ours:
            state["ours"] = ours
        with self._lock:
            self.state = state
        self._crossed(state)
        return state

    def swap_rates(self, counts: tuple[int, int], now: float) -> tuple[float, float] | None:
        """Pages swapped in and out a second over the last SWAP_WINDOW_S (from the newest sample at least that old,
        else the oldest kept), or None before a second sample: one 5 s burst counts for a sixth of a 30 s window."""
        self._swaps.append((counts[0], counts[1], now))
        while len(self._swaps) > 2 and now - self._swaps[1][2] >= SWAP_WINDOW_S:
            self._swaps.popleft()
        ins, outs, then = self._swaps[0]
        if now <= then:
            return None
        seconds = now - then
        return round(max(0, counts[0] - ins) / seconds, 1), round(max(0, counts[1] - outs) / seconds, 1)

    def _crossed(self, state: dict) -> None:
        """A `machine` record when any reading crossed one of the contract's lines since the last sample."""
        now = levels(state, self._levels)
        was, self._levels = self._levels, now
        if now == was or self.log is None or self.loop is None:
            return
        changed = {k: [was.get(k), v] for k, v in now.items() if was.get(k) != v}
        record = {"kind": "machine", "changed": changed, "levels": now, "readings": state}
        try:
            self.loop.call_soon_threadsafe(self._logged, record)
        except RuntimeError:
            pass

    def _logged(self, record: dict) -> None:
        try:
            self.log(record)
        except Exception:
            pass  # the meter never takes the app down

    def request_sample(self) -> None:
        """Wake the owned sampler for a load transition; never probe or start a thread here."""
        self._resample.set()

    def _run(self) -> None:
        while not self._stopped.is_set():
            # Coalesce transitions. A request during a probe remains set so a
            # follow-up sample observes it; no thread or OS probe runs on callers.
            self._resample.clear()
            if self._stopped.is_set():
                break
            try:
                self.sample()
            except Exception:
                pass  # the meter never takes the app down
            self._resample.wait(self.sample_s)

    def wire(self) -> dict:
        """The latest sample and recent clicks; reading never starts background work."""
        with self._lock:
            state = dict(self.state)
        clicks = sorted(self.click_ms)
        if clicks:
            state["clickP95Ms"] = round(clicks[min(len(clicks) - 1, round(0.95 * (len(clicks) - 1)))])
            state["clicks"] = len(clicks)
        return state

    def clicked(self, ms: float) -> None:
        self.click_ms.append(float(ms))

    def top(self, n: int = TOP_N) -> list[dict]:
        """The programs using the most CPU now (one `ps`; asked for only while the panel is open), our own
        workers named by their role."""
        try:
            done = subprocess.run(["ps", "-Ao", "pid=,ppid=,pcpu=,rss=,args="], capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            return []
        names = {}
        if self.workers is not None:
            try:
                names = {w["pid"]: worker_name(w) for w in self.workers() if w.get("pid")}
            except Exception:
                names = {}
        return parse_ps(done.stdout, os.getpid(), n, names)
