"""A fresh, bounded JVM for a contributed Fiji invocation or its independent reference.

The caller owns admission, immutable publication and generation retention. This boundary
never borrows a reviewed worker. Use owned_process.off_loop to await cancellation cleanup.
"""
from __future__ import annotations

import ctypes
import errno
import functools
import hashlib
import json
import math
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import owned_process

BOUNDARY_VERSION = "fiji-sandbox-v1"
JVM_LOCK = threading.Lock()
EXEC = "/usr/bin/sandbox-exec"
BOOTSTRAP = Path(__file__).resolve().parents[1] / "fiji_worker" / "functions.groovy"
SYSTEM = ("/System/Library", "/usr/lib", "/private/var/db/dyld", "/private/var/db/timezone")
DEVICES = ("/dev/null", "/dev/random", "/dev/urandom")


class Failed(RuntimeError):
    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class Limits:
    seconds: float = 120
    heap_mb: int = 256
    memory_bytes: int = 1024 * 1024 * 1024
    protocol_bytes: int = 8 * 1024 * 1024
    stdout_bytes: int = 16 * 1024 * 1024
    log_bytes: int = 64 * 1024
    file_bytes: int = 64 * 1024 * 1024
    work_bytes: int = 128 * 1024 * 1024
    work_entries: int = 256

    def __post_init__(self):
        for name, value in vars(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite limit")
            if name != "seconds" and not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")


@dataclass(frozen=True)
class Result:
    reply: dict
    output: bytes | None
    log: str


def identity(bootstrap: Path = BOOTSTRAP) -> dict:
    return {"boundary": BOUNDARY_VERSION,
            "boundarySha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "workerSha256": hashlib.sha256(bootstrap.read_bytes()).hexdigest()}


def _q(path: Path | str) -> str:
    return json.dumps(str(path), ensure_ascii=False)


def profile(java: Path, runtime: Path, reads: tuple[Path, ...], work: Path) -> str:
    roots = tuple(Path(p).resolve() for p in (*SYSTEM, runtime, work))
    files = tuple(Path(p).resolve() for p in (*DEVICES, *reads))
    ancestors = sorted({str(parent) for p in (*roots, *files) for parent in p.parents})
    return f'''(version 1)
(deny default)
(allow process-exec (literal {_q(java)}))
(allow signal (target same-sandbox))
(allow sysctl-read)
(deny sysctl-read (sysctl-name-regex #"^kern[.]proc"))
(deny process-info*)
(allow process-info* (target self))
(allow file-read-metadata {" ".join(f"(literal {_q(p)})" for p in ancestors)})
(allow file-read* (literal "/") {" ".join(f"(subpath {_q(p)})" for p in roots)}
 {" ".join(f"(literal {_q(p)})" for p in files)})
(allow file-write* (subpath {_q(work)}) (literal "/dev/null"))
(deny network*)
'''


@functools.lru_cache(maxsize=1)
def supported() -> bool:
    if sys.platform != "darwin" or not Path(EXEC).is_file():
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="fiji-boundary-check-") as tmp:
            work = Path(tmp).resolve()
            rules = profile(Path("/usr/bin/true"), Path("/usr/lib"), (), work)
            done = subprocess.run([EXEC, "-p", rules, "/usr/bin/true"], cwd=work,
                                  env={"HOME": str(work), "TMPDIR": str(work), "PATH": "/usr/bin:/bin"},
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=5, close_fds=True, start_new_session=True)
            return done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def available() -> bool:
    """The warmed probe only; catalog reads never launch a process."""
    return bool(supported.cache_info().currsize) and supported()


def _file(path: Path | str, what: str, max_bytes: int | None = None) -> Path:
    p = Path(path)
    try:
        if p.is_symlink():
            raise Failed("invalid-path", f"{what} must not be a symlink")
        p = p.resolve(strict=True)
        info = p.stat()
        if not stat.S_ISREG(info.st_mode) or (max_bytes is not None and info.st_size > max_bytes):
            raise Failed("invalid-path", f"{what} must be a regular file within its size limit")
        return p
    except OSError as exc:
        raise Failed("invalid-path", f"{what} is not available") from exc


def _java(fiji: Path, limits: Limits) -> tuple[Path, list[str]]:
    executables = sorted(fiji.glob("java/**/Contents/Home/bin/java"))
    if len(executables) != 1:
        raise Failed("runtime", "This pinned Fiji generation must contain exactly one bundled macOS JDK")
    java = _file(executables[0], "Fiji's bundled Java")
    if not java.is_relative_to(fiji):
        raise Failed("runtime", "Fiji's Java must belong to its pinned generation")
    jars = sorted(p for folder in (fiji / "jars", fiji / "plugins") for p in folder.rglob("*.jar"))
    if not jars or any(not _file(p, "A Fiji JAR").is_relative_to(fiji) or ":" in str(p) for p in jars):
        raise Failed("runtime", "Fiji's JARs must belong to its pinned generation")
    args = [str(java), f"-Xmx{limits.heap_mb}m", "-Djava.awt.headless=true", "-Dapple.awt.UIElement=true",
            "--add-opens=java.base/java.lang=ALL-UNNAMED", "-Dscijava.context.strict=false",
            *[f"-D{key}={fiji}" for key in ("plugins.dir", "imagej.dir", "ij.dir", "fiji.dir")],
            "-classpath", os.pathsep.join(map(str, jars)), "org.scijava.launcher.ClassLauncher", "net.imagej.Main"]
    return java, args


def _work_size(work: Path, limits: Limits):
    total = count = 0

    def unreadable(exc):
        if not isinstance(exc, FileNotFoundError):
            raise exc

    for root, dirs, files in os.walk(work, followlinks=False, onerror=unreadable):
        for name in (*dirs, *files):
            try:
                info = (Path(root) / name).lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise Failed("unsafe-output", "Fiji created a link or special file in its work folder")
            if stat.S_ISREG(info.st_mode):
                if info.st_nlink == 0:
                    continue
                if info.st_nlink != 1:
                    raise Failed("unsafe-output", f"Fiji created a hard-linked output (links={info.st_nlink}, "
                                 f"file={str((Path(root) / name).relative_to(work))[:200]})")
                total += info.st_size
                if info.st_size > limits.file_bytes:
                    raise Failed("file-limit", "Fiji exceeded its per-file output limit")
            count += 1
            if total > limits.work_bytes or count > limits.work_entries:
                raise Failed("file-limit", "Fiji exceeded its private work folder limit")
    return {'bytes': total, 'entries': count}


# Darwin SDK sys/proc_info.h, PROC_PIDTASKALLINFO. Keep the native field widths.
class _BsdInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "reserved"
    )] + [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)] + [
        (name, ctypes.c_uint32) for name in ("nfiles", "pgid", "jobc", "tdev", "tpgid")
    ] + [("nice", ctypes.c_int32), ("start_seconds", ctypes.c_uint64), ("start_microseconds", ctypes.c_uint64)]


class _TaskInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "virtual_bytes", "resident_bytes", "total_user", "total_system", "threads_user", "threads_system"
    )] + [(name, ctypes.c_int32) for name in (
        "policy", "faults", "pageins", "cow_faults", "messages_sent", "messages_received",
        "syscalls_mach", "syscalls_unix", "csw", "threadnum", "numrunning", "priority"
    )]


class _TaskAllInfo(ctypes.Structure):
    _fields_ = [("bsd", _BsdInfo), ("task", _TaskInfo)]


@functools.lru_cache(maxsize=1)
def _libproc():
    lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidinfo.restype = ctypes.c_int
    lib.proc_listchildpids.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    lib.proc_listchildpids.restype = ctypes.c_int
    return lib


PROCESS_LIMIT = 256


def _native_process(pid: int) -> _TaskAllInfo | None:
    info = _TaskAllInfo()
    ctypes.set_errno(0)
    size = _libproc().proc_pidinfo(pid, 2, 0, ctypes.byref(info), ctypes.sizeof(info))
    if size == 0 and ctypes.get_errno() == errno.ESRCH:
        return None
    if size != ctypes.sizeof(info) or info.bsd.pid != pid or not info.bsd.start_seconds:
        raise Failed("supervision", "Fiji's process identity and memory could not be checked")
    return info


def _native_children(pid: int) -> tuple[int, ...]:
    children = (ctypes.c_int * PROCESS_LIMIT)()
    ctypes.set_errno(0)
    count = _libproc().proc_listchildpids(pid, children, ctypes.sizeof(children))
    error = ctypes.get_errno()
    if count < 0 or (count == 0 and error not in (0, errno.ESRCH)) or count >= PROCESS_LIMIT:
        raise Failed("supervision", "Fiji's process tree could not be checked within its limit")
    result = tuple(children[:count])
    if any(child <= 0 or child == pid for child in result) or len(set(result)) != count:
        raise Failed("supervision", "Fiji returned an invalid process tree")
    return result


def _native_memory(pid: int, identities: dict[int, tuple[int, int]]) -> int | None:
    total = 0
    seen = set()
    pending = [(pid, None)]
    while pending:
        child, parent = pending.pop()
        if child in seen or len(seen) >= PROCESS_LIMIT:
            raise Failed("supervision", "Fiji's process tree exceeded its inspection limit")
        seen.add(child)
        info = _native_process(child)
        if info is None:
            if child == pid:
                return None
            continue
        started = (info.bsd.start_seconds, info.bsd.start_microseconds)
        if (parent is not None and info.bsd.ppid != parent) or identities.get(child, started) != started:
            raise Failed("supervision", "Fiji's process identity changed during inspection")
        if child not in identities and len(identities) >= PROCESS_LIMIT:
            raise Failed("supervision", "Fiji's process tree exceeded its inspection limit")
        identities[child] = started
        total += info.task.resident_bytes
        pending.extend((descendant, child) for descendant in _native_children(child))
        if len(seen) + len(pending) > PROCESS_LIMIT:
            raise Failed("supervision", "Fiji's process tree exceeded its inspection limit")
    return total


def _memory(pid: int, identities: dict[int, tuple[int, int]] | None = None) -> int | None:
    try:
        if sys.platform == "darwin":
            return _native_memory(pid, identities if identities is not None else {})
        done = subprocess.run(["/bin/ps", "-A", "-o", "pid=,ppid=,rss="], capture_output=True, text=True,
                              timeout=1, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, close_fds=True)
        if done.returncode:
            raise ValueError("ps failed")
        rows = [tuple(map(int, line.split())) for line in done.stdout.splitlines()]
        if any(len(row) != 3 for row in rows):
            raise ValueError("incomplete process table")
        if not any(child == pid for child, _, _ in rows):
            return None
        owned = {pid}
        while True:
            children = {child for child, parent, _ in rows if parent in owned}
            if children <= owned:
                break
            owned |= children
        return sum(rss * 1024 for child, _, rss in rows if child in owned)
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError) as exc:
        raise Failed("supervision", "Fiji's process memory could not be checked") from exc


def _execute(fiji: Path, work: Path, reads: tuple[Path, ...], tail: list[str], payload: bytes | None,
             limits: Limits) -> tuple[dict, str]:
    with owned_process.locked(JVM_LOCK):
        return _execute_admitted(fiji, work, reads, tail, payload, limits)


def _execute_admitted(fiji: Path, work: Path, reads: tuple[Path, ...], tail: list[str], payload: bytes | None,
             limits: Limits) -> tuple[dict, str]:
    if not supported():
        raise Failed("unavailable", "Contributed Fiji code requires a working macOS sandbox; it was not run")
    java, args = _java(fiji, limits)
    rules = profile(java, fiji, reads, work)
    failure: list[Failed] = []
    checked = 0.0
    identities: dict[int, tuple[int, int]] = {}
    process = None

    def inspect() -> bool:
        nonlocal checked
        now = time.monotonic()
        if now - checked < .1:
            return False
        checked = now
        try:
            _work_size(work, limits)
            if process is not None and process.poll() is None:
                memory = _memory(process.pid, identities)
                if memory is None and process.poll() is None:
                    # poll() can report None while another thread owns waitpid for an exited child.
                    try:
                        process.wait(timeout=.1)
                    except subprocess.TimeoutExpired as exc:
                        raise Failed("supervision", "Fiji's live process was missing from its memory check") from exc
                if memory is not None and memory > limits.memory_bytes:
                    raise Failed("memory-limit", "Fiji exceeded its process memory limit")
        except (Failed, OSError) as exc:
            if not isinstance(exc, Failed):
                failure_reason = Failed("supervision", "Fiji's work could not be checked")
                failure_reason.__cause__ = exc
                exc = failure_reason
            failure.append(exc)
            return True
        return False

    logs = bytearray()
    line = bytearray()
    total = 0
    reply = None
    try:
        with owned_process.scope(seconds=limits.seconds) as bound:
            bound.check()
            process = subprocess.Popen([EXEC, "-p", rules, *args, *tail], cwd=work,
                                       env={"HOME": str(work), "TMPDIR": str(work), "PATH": "/usr/bin:/bin"},
                                       stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True, close_fds=True)
            with owned_process.scope(stop=inspect), owned_process.watching(process, limits.seconds, "Fiji"):
                if payload is not None:
                    try:
                        process.stdin.write(payload)
                        process.stdin.close()
                    except BrokenPipeError:
                        pass
                while chunk := process.stdout.read1(8192):
                    total += len(chunk)
                    if total > limits.stdout_bytes:
                        raise Failed("output-limit", "Fiji exceeded its protocol and log output limit")
                    logs.extend(chunk)
                    if len(logs) > limits.log_bytes:
                        del logs[:-limits.log_bytes]
                    if payload is None:
                        continue
                    line.extend(chunk)
                    while b"\n" in line:
                        raw, _, rest = line.partition(b"\n")
                        line = bytearray(rest)
                        if len(raw) > limits.protocol_bytes:
                            raise Failed("output-limit", "Fiji exceeded its protocol line limit")
                        if raw.startswith(b"@@"):
                            try:
                                reply = json.loads(raw[2:])
                            except (ValueError, UnicodeError, RecursionError) as exc:
                                raise Failed("protocol", "Fiji returned an invalid reply") from exc
                            if not isinstance(reply, dict) or not isinstance(reply.get("ok"), bool):
                                raise Failed("protocol", "Fiji returned an invalid reply")
                            break
                    if len(line) > limits.protocol_bytes:
                        raise Failed("output-limit", "Fiji exceeded its protocol line limit")
                    if reply is not None:
                        break
                if reply is None:
                    process.wait()
                    _work_size(work, limits)
                    if process.returncode != 0:
                        raise Failed("crash", f"Fiji stopped with exit code {process.returncode}")
                    if payload is not None:
                        raise Failed("protocol", "Fiji stopped without a reply")
            bound.check()
    except owned_process.Stopped:
        if failure:
            raise failure[0]
        raise
    except OSError as exc:
        raise Failed("launch", "The confined Fiji process could not run") from exc
    finally:
        if process is not None:
            owned_process.terminate(process)
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
            process.stdout.close()
    _work_size(work, limits)
    if reply is not None and reply["ok"] is False:
        error = str(reply.get("error", "Unknown error"))[:500]
        raise Failed("worker", f"Fiji could not complete this operation: {error}")
    return reply or {"ok": True}, logs.decode("utf-8", "replace")


def _output(path: Path, limits: Limits, admit=None) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limits.file_bytes:
                raise Failed("unsafe-output", "Fiji's result is not a bounded, regular private file")
            if admit is not None:
                admit(stream.read(33), info.st_size)
                stream.seek(0)
            data = stream.read(limits.file_bytes + 1)
            if not data or len(data) > limits.file_bytes:
                raise Failed("file-limit", "Fiji's result is empty or exceeds its output limit")
            return data
    except OSError as exc:
        raise Failed("missing-output", "Fiji did not produce a readable private result") from exc


def _picture(data: bytes) -> bytes:
    if len(data) < 33 or data[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR":
        raise Failed("invalid-output", "Fiji's picture is not a PNG with a readable image size")
    width, height = int.from_bytes(data[16:20]), int.from_bytes(data[20:24])
    if min(width, height) < 1 or width * height > 16_000_000:
        raise Failed("invalid-output", "Fiji's result must be one image plane up to 16 million pixels")
    return data


def call(fiji: Path, request: dict, *, entry: Path | None = None, bootstrap: Path = BOOTSTRAP,
         limits: Limits = Limits(), admit=None) -> Result:
    """Run one worker request; returned image bytes replace all script-supplied output paths."""
    with owned_process.scope(seconds=limits.seconds):
        fiji = Path(fiji).resolve(strict=True)
        bootstrap = _file(bootstrap, "Fiji's worker bootstrap", 1024 * 1024)
        request = dict(request)
        reads = [bootstrap]
        if request.get("op") not in ("run", "ping", "has_command"):
            raise Failed("request", "This Fiji request is unsupported")
        if request.get("op") == "run":
            image = _file(request.get("image", ""), "The selected image", limits.file_bytes)
            request["image"] = str(image)
            reads.append(image)
            if request.get("output") not in ("image.2d.rgb8", "table.objects"):
                raise Failed("request", "This Fiji output type is unsupported")
            if request.get("script"):
                script = _file(request["script"], "The capability script", 1024 * 1024)
                if entry is None or not script.is_relative_to(Path(entry).resolve(strict=True)):
                    raise Failed("invalid-path", "The script must belong to the immutable capability folder")
                if hashlib.sha256(script.read_bytes()).hexdigest() != request.get("scriptSha256"):
                    raise Failed("changed-script", "The capability script changed since this request was prepared")
                request["script"] = str(script)
                reads.append(script)
        with tempfile.TemporaryDirectory(prefix="fiji-attempt-") as tmp:
            work = Path(tmp).resolve()
            out = work / "result.png"
            request["out"] = str(out)
            try:
                payload = (json.dumps(request, allow_nan=False) + "\n").encode()
            except (TypeError, ValueError, RecursionError) as exc:
                raise Failed("request", "The Fiji request is not valid JSON") from exc
            if len(payload) > limits.protocol_bytes:
                raise Failed("request", "The Fiji request exceeds its size limit")
            reply, log = _execute(fiji, work, tuple(reads), ["--run", str(bootstrap)], payload, limits)
            picture = request.get("op") == "run" and request.get("output") == "image.2d.rgb8"
            if request.get("op") == "run" and reply.get("kind") != ("picture" if picture else "table"):
                raise Failed("protocol", "Fiji returned an unexpected result type")
            reply.pop("path", None)
            def admit_picture(header, size):
                _picture(header)
                if admit is not None:
                    admit(header, size)
            return Result(reply, _picture(_output(out, limits, admit_picture)) if picture else None, log)


def run_macro(fiji: Path, image: Path, command: str, options: str, *, table: bool = False,
              limits: Limits = Limits()) -> Result:
    """Run the reference command in its own confined JVM, returning PNG or CSV bytes."""
    with owned_process.scope(seconds=limits.seconds):
        fiji = Path(fiji).resolve(strict=True)
        image = _file(image, "The reference image", limits.file_bytes)
        with tempfile.TemporaryDirectory(prefix="fiji-reference-") as tmp:
            work = Path(tmp).resolve()
            out = work / ("result.csv" if table else "result.png")
            if any(c in str(image) + str(out) + command + options for c in '\\"\n\r'):
                raise Failed("request", "The reference command cannot be written safely as an ImageJ macro")
            macro = work / "reference.ijm"
            macro.write_text(f'open("{image}");\nrun("{command}", "{options}");\n'
                             f'saveAs("{"Results" if table else "PNG"}", "{out}");\n'
                             'eval("script", "System.exit(0);");\n')
            reply, log = _execute(fiji, work, (image,), ["-macro", str(macro)], None, limits)
            output = _output(out, limits)
            return Result(reply, output if table else _picture(output), log)
