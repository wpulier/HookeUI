"""macOS's sandbox around code nobody reviewed (docs/design/2026-10-13-capabilities-for-everyone.md, C7).

An entry's adapter runs in a process of its own (env_runner.Client, jobs.run). When what it runs was **not reviewed** (a
community catalog's entry, a file someone shared, or any of yours: its own adapter, or a reviewed template's runner
loading a model someone else made, since a TorchScript model is code), the process runs inside `sandbox-exec` with a
profile that **denies by default**:

- **reads** only the system (`/usr`, `/System`, …), its own Python (the interpreter's install and the entry's
  environment), the entry as it was installed and its pinned files, and the app's two small wire files it imports;
- **writes** only its own work folder (also its TMPDIR);
- **no network**, and nothing else of the home folder: not the keys file, not the Keychain's files, not your sessions.

- **other processes:** it may signal only processes inside its own sandbox (its own children), look up only the system
  services a Python process needs (`MACH`: never LaunchServices, which could open a link in a browser outside it, nor the
  Keychain's or the pasteboard's), and share memory only under the names Python's and torch's own workers make;
- **its environment** (`confine`): HOME, its temporary folder and its caches are its work folder, and nothing names
  the catalog or the app's folders. Descriptors the app holds don't reach it (subprocess closes them: `close_fds`).

What it is not: `file-read-metadata` is allowed everywhere (a file's existence and size, never its contents), and
GPU access (`iokit-open`) is allowed for Metal. The Python guard (MICROMIND_REFUSE_NETWORK) stays as well. Where the sandbox can't run (`available` is false), code
nobody reviewed is **refused**, never run without it (`required`). The app's reviewed catalog runs as before.

A wheel's own native libraries load inside it (its compiled parts and the runtimes it bundles, such as scipy's Fortran
runtime: tests/test_sandbox.py). A library macOS itself refuses to load (scipy 1.15.3's wheels on macOS 27, C4's builder
on 2026-10-14) is refused outside it too, and is said by its package, version and macOS's reason (envs.native_words).
"""
from __future__ import annotations

import functools
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from gpu_worker import manifests as catalog

EXEC = "/usr/bin/sandbox-exec"
SYSTEM = ("/usr", "/System", "/Library/Apple", "/Library/Frameworks", "/private/var/db/dyld", "/private/var/db/timezone",
          "/dev", "/bin", "/private/etc", "/etc", "/Library/Preferences", "/opt/homebrew/Cellar", "/opt/homebrew/opt")


def _q(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _real(p: Path | str) -> str:
    return os.path.realpath(str(p))


# The system services a sandboxed Python, NumPy, torch or ONNX Runtime process looks up: user and group names, the
# notification centre, the system log, preferences, and Metal's shader compiler. Nothing that acts for it outside the
# sandbox (LaunchServices, the pasteboard, the Keychain's securityd, the network's own daemons).
MACH = ("com.apple.system.opendirectoryd.libinfo", "com.apple.system.notification_center", "com.apple.logd",
        "com.apple.system.logger", "com.apple.diagnosticd", "com.apple.cfprefsd.daemon", "com.apple.cfprefsd.agent",
        "com.apple.MTLCompilerService", "com.apple.CARenderServer", "com.apple.gpumemd.source")
# Named shared memory and semaphores: only those Python's multiprocessing, loky and torch make for their own workers.
IPC_NAMES = r'^/(psm_|mp-|loky-|torch_|sem\.mp-)'


def confine(env: dict, work: Path) -> dict:
    """The environment of a sandboxed process (see the module's docstring): its home, temporary folder and caches are its
    work folder, and nothing names the catalog or the app's folders."""
    work = Path(work)
    out = {k: v for k, v in env.items() if not k.startswith("MICROMIND_") or k in ("MICROMIND_REFUSE_NETWORK",
                                                                                     "MICROMIND_ENTRY_FILES",
                                                                                     "MICROMIND_EDITION")}
    out.update(HOME=str(work), TMPDIR=str(work), XDG_CACHE_HOME=str(work / ".cache"), TORCH_HOME=str(work / ".cache"),
               PYTHONDONTWRITEBYTECODE="1")
    return out


def profile(read: Iterable[Path | str], write: Iterable[Path | str], *, gpu: bool = True, children: bool = True) -> str:
    """The profile (see the module's docstring): system and `read` readable, `write` read-write, nothing else."""
    # MICROMIND_SANDBOX_READ: more readable places, for the tests' stand-in environments (their Python wraps the app's own).
    extra = [p for p in os.environ.get("MICROMIND_SANDBOX_READ", "").split(os.pathsep) if p]
    reads = " ".join(f"(subpath {_q(_real(p))})" for p in (*SYSTEM, *extra, *read) if p)
    writes = " ".join(f"(subpath {_q(_real(p))})" for p in write)
    mach = MACH if gpu else tuple(name for name in MACH if name not in
                                  ('com.apple.MTLCompilerService', 'com.apple.CARenderServer', 'com.apple.gpumemd.source'))
    return f"""(version 1)
(deny default)
(allow process-exec)
{"(allow process-fork)" if children else "(deny process-fork)"}
(allow signal (target same-sandbox))
(allow sysctl-read)
{"(allow iokit-open)" if gpu else "(deny iokit-open)"}
(allow mach-lookup {" ".join(f"(global-name {_q(n)})" for n in mach)})
(allow ipc-posix-shm ipc-posix-sem (ipc-posix-name-regex #"{IPC_NAMES}"))
(allow file-read-metadata)
(allow file-read* (literal "/") {reads})
(allow file-read* file-write* {writes} (literal "/dev/null") (literal "/dev/tty") (literal "/dev/dtracehelper"))
(deny network*)
"""


def bootstrap() -> tuple[str, ...]:
    """The app's own files a sandboxed worker reads to start: its wire (env_worker/) and the network guard
    (gpu_worker/offline.py), never the rest of the app's folder."""
    services = Path(__file__).resolve().parent.parent
    return str(services / "env_worker"), str(services / "gpu_worker" / "offline.py")


def wrap(cmd: list[str], read: Iterable[Path | str], write: Iterable[Path | str], *, gpu: bool = True, children: bool = True) -> list[str]:
    return [EXEC, "-p", profile(read, write, gpu=gpu, children=children), *cmd]


@functools.lru_cache(maxsize=1)
def available() -> bool:
    """Whether macOS's sandbox runs here: present, and a probe inside it runs."""
    if not Path(EXEC).exists():
        return False
    try:
        done = subprocess.run(wrap(["/usr/bin/true"], (), ()), capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def required(m: catalog.Manifest) -> bool:
    """Whether what this entry runs was not reviewed: a channel's that isn't the reviewed one, or any of yours. A reviewed
    template's runner is reviewed, but the model it loads is its makers' (a TorchScript model is code: PyTorch's security
    policy), so one of yours made from a template is sandboxed too; its hashes say which model it is, not that it is safe."""
    if m.members:
        return True
    if m.channel:
        c = next((c for c in catalog.channels() if c["name"] == m.channel), {})
        return not c.get("reviewed")
    return m.namespace == "mine"


def python_home(exe: Path) -> Path:
    """The interpreter's own install (what a venv's python links to): readable inside the sandbox."""
    return Path(_real(exe)).parents[1]


OS_NAMES = {"darwin": "macOS", "linux": "Linux", "win32": "Windows"}


def refused(m: catalog.Manifest, platform: str | None = None) -> str:
    """Why code nobody reviewed doesn't run here, in this computer's own terms: on a Mac, macOS's sandbox is missing;
    elsewhere Hooke has no sandbox for that OS yet (docs/research/2026-10-20-h11-linux-windows-feasibility.md, G2)."""
    platform = platform or sys.platform
    if platform == "darwin":
        return (f"{m.name}'s code wasn't reviewed, and macOS's sandbox it must run in isn't available on this Mac: it is "
                "not run without it")
    os_name = OS_NAMES.get(platform, platform)
    return (f"{m.name}'s code wasn't reviewed, and Hooke has no sandbox for {os_name} yet to run it in: it is not run on "
            "this computer without one")
