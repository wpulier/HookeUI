"""Lifetime supervision for one owned, confined capability worker."""
from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from . import fiji_sandbox, owned_process

BOUNDARY_VERSION = "bundle-resources-v1"
POLL_S = .05
TERMINAL_SETTLE_S = .05


@dataclass(frozen=True)
class Limits:
    memory_bytes: int = 1024 * 1024 * 1024
    processes: int = 32
    file_bytes: int = 64 * 1024 * 1024
    work_bytes: int = 128 * 1024 * 1024
    work_entries: int = 256

    def __post_init__(self):
        for name, value in vars(self).items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.processes > fiji_sandbox.PROCESS_LIMIT:
            raise ValueError("processes exceeds the native inspection limit")


def _identity(info):
    return info.bsd.start_seconds, info.bsd.start_microseconds


def _terminal(process):
    if process.poll() is not None:
        return True
    try:
        ended = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        ended = None
    if ended is not None and ended.si_pid == process.pid and ended.si_code in (
            os.CLD_EXITED, os.CLD_KILLED, os.CLD_DUMPED):
        return True
    try:
        os.kill(process.pid, 0)
    except ProcessLookupError:
        return True
    return False


def _ended(process):
    """Whether a child the kernel no longer finds (ESRCH) has really ended, leaving its exit status for Popen.

    Darwin stops finding a child as soon as it exits, before it is reaped, and can drop the task record just before
    publishing wait status: confirm that transition once. A live child that cannot be found has not ended."""
    if _terminal(process):
        return True
    try:
        process.wait(timeout=TERMINAL_SETTLE_S)
    except subprocess.TimeoutExpired:
        pass
    return _terminal(process)


def _leads_own_group(process):
    """True or False for a running child; None once it has ended, which is a normal end rather than a failure."""
    if process.poll() is not None:
        return None
    try:
        return os.getpgid(process.pid) == process.pid
    except ProcessLookupError:
        if _ended(process):
            return None
        raise


class Guard:
    """Start immediately after Popen(start_new_session=True); close before releasing its work folder.

    A failure remains observable by check() after cleanup. close() stops and reaps this
    worker, so a caller never releases a work folder while background code still owns it.
    """
    def __init__(self, process, work: Path, limits: Limits = Limits()):
        self.process = process
        self.work = Path(work)
        self.limits = limits
        self._identities: dict[int, tuple[int, int]] = {}
        self._failure: owned_process.Stopped | None = None
        self._done = threading.Event()
        self._inspection = threading.Lock()
        self._termination = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._owns_group = False
        self._terminated = False

    def start(self):
        if self._thread is not None or self._closed or self._done.is_set():
            raise RuntimeError("Worker supervision can only start once")
        try:
            if sys.platform != "darwin":
                raise owned_process.Stopped("Capability resource supervision requires macOS")
            leads = _leads_own_group(self.process)
            if leads is False:
                raise owned_process.Stopped("Capability worker has no owned process group")
            self._owns_group = leads is True and self.process.poll() is None
            self._sample()
        except Exception as exc:
            self._fail(exc)
            self.check()
        self._thread = threading.Thread(target=self._watch, name="capability-worker-resources", daemon=True)
        self._thread.start()
        return self

    def check(self):
        if self._failure is None and self._thread is not None and not self._closed:
            try:
                self._sample()
            except Exception as exc:
                self._fail(exc)
        if self._failure is not None:
            raise self._failure

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._done.set()
        if self._thread is not None:
            self._thread.join()
        self._terminate()

    def _watch(self):
        while not self._done.wait(POLL_S):
            try:
                self._sample()
                if self.process.poll() is not None:
                    self._terminate()
                    return
            except Exception as exc:
                self._fail(exc)
                return

    def _sample(self):
        with self._inspection:
            self._inspect()

    def _inspect(self):
        try:
            if not stat.S_ISDIR(self.work.lstat().st_mode):
                raise owned_process.Stopped("Capability private work folder is unavailable")
            fiji_sandbox._work_size(self.work, self.limits)
            memory = self._tree()
            if memory > self.limits.memory_bytes:
                raise owned_process.Stopped("Capability exceeded its process-tree memory limit")
        except fiji_sandbox.Failed as exc:
            raise owned_process.Stopped(str(exc).replace("Fiji", "Capability")) from exc
        except (OSError, ValueError, AttributeError) as exc:
            raise owned_process.Stopped("Capability resources could not be supervised") from exc

    def _tree(self):
        total = 0
        live = {}
        pending = [(pid, None) for pid in self._identities if pid != self.process.pid]
        pending.append((self.process.pid, None))
        while pending:
            pid, parent = pending.pop()
            if pid in live:
                continue
            info = fiji_sandbox._native_process(pid)
            if info is None:
                if pid == self.process.pid and self.process.poll() is None:
                    if _ended(self.process):
                        continue
                    raise owned_process.Stopped("Capability live process could not be supervised")
                continue
            identity = _identity(info)
            if self._identities.get(pid, identity) != identity:
                raise owned_process.Stopped("Capability process identity changed during supervision")
            if parent is not None and info.bsd.ppid != parent:
                raise owned_process.Stopped("Capability process ownership changed during supervision")
            live[pid] = identity
            self._identities[pid] = identity
            if len(live) > self.limits.processes:
                raise owned_process.Stopped("Capability exceeded its process-count limit")
            total += info.task.resident_bytes
            children = fiji_sandbox._native_children(pid)
            if len(children) > self.limits.processes:
                raise owned_process.Stopped("Capability exceeded its process-count limit")
            pending.extend((child, pid) for child in children)
        self._identities = live
        return total

    def _fail(self, exc):
        if self._failure is None:
            self._failure = exc if isinstance(exc, owned_process.Stopped) else owned_process.Stopped(
                "Capability resource supervision failed")
        self._done.set()
        self._terminate()

    def _terminate(self):
        with self._termination:
            self._stop()

    def _stop(self):
        if self._terminated:
            return
        self._terminated = True
        try:
            if self._owns_group and self.process.poll() is None:
                owned_process.terminate(self.process)
            elif self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=owned_process.REAP_S)
        except (OSError, subprocess.SubprocessError) as exc:
            if self._failure is None:
                self._failure = owned_process.Stopped(f"Capability worker cleanup failed ({type(exc).__name__})")
        finally:
            for pid, identity in tuple(self._identities.items()):
                if pid == self.process.pid:
                    continue
                try:
                    info = fiji_sandbox._native_process(pid)
                    if info is not None and _identity(info) == identity:
                        os.kill(pid, signal.SIGKILL)
                except (OSError, fiji_sandbox.Failed):
                    continue
