"""What a process really holds on this Mac: its physical footprint, the number Activity Monitor shows as
Memory and the kernel counts against it under pressure (resident pages, compressed pages and the GPU memory
Metal gives it, all in one), and the most it has held.

Resident size (`ps -o rss`) misses Metal's memory and keeps freed pages a process's allocator holds on to,
so a model's size measured by it inside a shared worker came out at 0 MB (BioCLIP 2, MedSigLIP) or 9 MB
(SAM 2.1 tiny) on 2026-10-10/11. Light on purpose (ctypes only), so the orchestrator reads it too.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys

RUSAGE_INFO_V4 = 4


class _RusageInfoV4(ctypes.Structure):
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in (
        "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups", "ri_pageins", "ri_wired_size",
        "ri_resident_size", "ri_phys_footprint", "ri_proc_start_abstime", "ri_proc_exit_abstime", "ri_child_user_time",
        "ri_child_system_time", "ri_child_pkg_idle_wkups", "ri_child_interrupt_wkups", "ri_child_pageins",
        "ri_child_elapsed_abstime", "ri_diskio_bytesread", "ri_diskio_byteswritten", "ri_cpu_time_qos_default",
        "ri_cpu_time_qos_maintenance", "ri_cpu_time_qos_background", "ri_cpu_time_qos_utility",
        "ri_cpu_time_qos_legacy", "ri_cpu_time_qos_user_initiated", "ri_cpu_time_qos_user_interactive",
        "ri_billed_system_time", "ri_serviced_system_time", "ri_logical_writes", "ri_lifetime_max_phys_footprint",
        "ri_instructions", "ri_cycles", "ri_billed_energy", "ri_serviced_energy", "ri_interval_max_phys_footprint",
        "ri_runnable_time")]


_libproc = None


def _usage(pid: int | None) -> _RusageInfoV4 | None:
    global _libproc
    if sys.platform != "darwin":
        return None
    if _libproc is None:
        _libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib", use_errno=True)
    info = _RusageInfoV4()
    if _libproc.proc_pid_rusage(int(pid or os.getpid()), RUSAGE_INFO_V4, ctypes.byref(info)) != 0:
        return None  # gone, or not ours to read
    return info


def footprint_mb(pid: int | None = None) -> float | None:
    """The process's physical footprint now (MB), or None where it can't be read (not macOS, the process gone)."""
    info = _usage(pid)
    return info.ri_phys_footprint / 2**20 if info is not None else None


def peak_mb(pid: int | None = None) -> float | None:
    """The most the process has held since it started (MB)."""
    info = _usage(pid)
    return info.ri_lifetime_max_phys_footprint / 2**20 if info is not None else None


class Peak:
    """The most a process held while a block ran (MB), sampled every `every_s` on a thread of its own: a load's
    transient copies (a checkpoint read whole before its weights move to the GPU) are gone by the time it ends.
    `mb` stays None where the footprint can't be read."""

    def __init__(self, pid: int | None = None, every_s: float = 0.05) -> None:
        self.pid, self.every_s, self.mb = pid, every_s, None
        self._stop = None
        self._thread = None

    def _sample(self) -> None:
        now = footprint_mb(self.pid)
        if now is not None and (self.mb is None or now > self.mb):
            self.mb = now

    def __enter__(self) -> "Peak":
        import threading

        self._sample()
        self._stop = threading.Event()

        def run():
            while not self._stop.wait(self.every_s):
                self._sample()

        self._thread = threading.Thread(target=run, name="footprint-peak", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._sample()
