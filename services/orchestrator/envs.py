"""Environments the app makes (phase 3's E2 of docs/design/2026-10-12-the-open-catalog.md): "install" is a button.

A model whose code needs a Python of its own (micro-sam's 187 packages; Trackastra's Gurobi) names it in its manifest
(`env`: the Python version, a lockfile with every package's hash beside the manifest, and what its proof imports). The
app makes it, and the model runs only from it:

    <catalog>/envs/<id>/<generation>/      a whole environment: built in place, ready only by its receipt
      receipt.json                         written last: the lockfile's hash, the Python, the packages, the proof
    <generation>.building                  beside it while it is made (the process); <generation>.failed: why it failed

- **Built in place, never renamed** (the review's correction 1): a virtual environment's scripts hold its absolute path,
  so it is made at its permanent path, `<generation>` named by the lockfile's and the Python's hash. It is ready only
  when its receipt is there, written atomically after the proof passed; a generation without one is "being built"
  (its `.building` names the process) or failed (its `.failed` says why), and nothing runs from it.
- **Exactly what the lockfile pins:** `uv venv` with uv's own Python at the pinned version, then `uv pip sync
  --require-hashes`: every package by its hash, nothing resolved on this Mac, nothing else installed.
- **Proven where it will run:** its imports run with that environment's Python and a clean environment (`clean_env`:
  nothing of the user's shell, no PYTHONPATH, CONDA_PREFIX or DYLD_*), as its worker will.
- **A new lockfile is a new generation** beside the old one; Remove takes the entry's environments away.

Nothing here runs on the event loop: `Installs` (the queue the Models page and the agent use) builds in a thread, one
at a time, said in Activity, and the capabilities are sent again when one is ready.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from gpu_worker import manifests as catalog
from . import owned_process

RECEIPT = "receipt.json"
BUILDING = ".building"  # beside the generation (_marker)
FAILED = ".failed"
STEP_S = 1800  # the longest one step (making the Python, installing the packages, the proof) may take


@dataclass
class _ReadGenerations:
    values: dict = field(default_factory=dict)
    active: bool = True


_READ_GENERATIONS: ContextVar[_ReadGenerations | None] = ContextVar("env_read_generations", default=None)


@contextmanager
def read_batch():
    """Share hashes during one synchronous display response, never between responses or executions."""
    current = _READ_GENERATIONS.get()
    own = _ReadGenerations() if current is None or not current.active else None
    token = _READ_GENERATIONS.set(own) if own is not None else None
    try:
        yield
    finally:
        if token is not None:
            own.active = False  # contexts inherited by later tasks cannot retain this response's hashes
            own.values.clear()
            _READ_GENERATIONS.reset(token)


class Failed(RuntimeError):
    pass


def spec(m: catalog.Manifest) -> dict:
    return dict(m.env or {})


def lock_file(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> Path:
    return catalog.entry_folder(m, environ) / spec(m)["lock"]


def generation(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> str:
    """The lockfile's and the Python's hash: a new lockfile or Python is a new folder, never written over. An entry that
    brings its own adapter (runner python-env) hashes its whole entry too (`entry_digest`): a new version of it is a new
    generation beside the one in use (env_runner.in_use)."""
    from . import env_observations
    observed = env_observations.get(m, environ)
    if observed is not None:
        if observed.candidate is None:
            raise FileNotFoundError("environment candidate has not been observed")
        return observed.candidate.name
    batch = _READ_GENERATIONS.get()
    kept = batch.values if batch is not None and batch.active else None
    key = (str(catalog.entry_folder(m, environ)), repr(m)) if kept is not None else None
    if kept is not None and key in kept:
        return kept[key]
    value = _generation_from_files(m, environ)
    if kept is not None:
        kept[key] = value
    return value


def _generation_from_files(m, environ, *, wheel_preparation=1):
    """Recompute admission identity without display observations or response caches."""
    h = hashlib.sha256(lock_file(m, environ).read_bytes())
    h.update(str(spec(m)["python"]).encode())
    if spec(m).get("wheels") and wheel_preparation == 1:
        # Materialization changes create a new generation beside retained versions.
        h.update(b"\0hooke-portable-wheels-origin-v1")
    if m.kind == "runtime":
        # An environment-only entry has no adapter proof: its declared import and
        # command checks are part of preparation identity, never reused after edits.
        h.update(b"\0hooke-runtime-checks-v1\0")
        h.update(json.dumps(spec(m), sort_keys=True, separators=(",", ":")).encode())
    if m.runner == "python-env":
        h.update(entry_digest(m, environ).encode())
    return h.hexdigest()[:16]


def prepared_generation(m, receipt, environ=os.environ):
    """Validate a retained snapshot using its original preparation identity rule."""
    version = receipt.get('wheelPreparation', 0)
    if type(version) is not int or version not in (0, 1) or version and not spec(m).get('wheels'):
        raise Failed('This wheel preparation version is unsupported; keep the saved runtime unchanged')
    return _generation_from_files(m, environ, wheel_preparation=version)


SNAPSHOT = "entry"  # in a python-env generation: entry/<id>/, its entry as it was installed (manifest, adapter, lockfile)
SKIP = ("__pycache__", "*.pyc", ".DS_Store")
LOCAL = ("activation.json",)  # what this Mac keeps in an entry's folder (mine.py), never part of the entry


def entry_files(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> list[Path]:
    """The files that make the entry, relative to its folder: its manifest, adapter, lockfile and anything beside them;
    never this Mac's own state there (a user's entry keeps its files' revisions, their staging and checks, and whether
    it is on beside its manifest), nor caches."""
    import fnmatch

    folder = catalog.entry_folder(m, environ)
    out = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder)
        top = rel.parts[0]
        if (top in LOCAL or top.startswith(".") or (len(rel.parts) > 1 and top == catalog.revision_of(m))
                or any(fnmatch.fnmatch(part, pattern) for part in rel.parts for pattern in SKIP)):
            continue
        out.append(rel)
    return out


_SHAS: dict[tuple, bytes] = {}  # a file's sha256 by what it is on disk now: read again only when it changed


RACY_NS = 2_000_000_000  # a change time this recent may not be distinguishable from the next change (coarse clocks)


def _file_sha(path: Path) -> bytes:
    """A file's sha256, read once while it stays the same file (device, inode, size and both change times): the one
    registry asks for every entry's digest on every list it makes (kinds.available), and reading every adapter, lock
    and example each time cost the capability matrix past its 90 s.

    A digest is cached only once the file's change time is RACY_NS old. Linux stamps times from a coarse clock (one
    tick, a few ms) and reuses inode numbers at once, so a file replaced within one tick by one of the same size, with
    its mtime restored, has the very same stat key; caching it then would serve the old file's digest (the Linux
    container run of 2026-10-20, test_fiji_runner). The same rule as git's "racily clean" index entries."""
    st = path.stat()
    key = (str(path), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    found = _SHAS.get(key)
    if found is None:
        found = hashlib.sha256(path.read_bytes()).digest()
        if time.time_ns() - st.st_ctime_ns > RACY_NS:
            if len(_SHAS) > 4096:
                _SHAS.clear()
            _SHAS[key] = found
    return found


def entry_digest(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> str:
    """What a python-env entry runs, by hash: every file of the entry but its manifest (its adapter and anything it
    imports beside it) and its operational manifest, including rights and requirements. Display wording alone
    (name, what, credit) does not require a new environment."""
    from dataclasses import asdict

    folder = catalog.entry_folder(m, environ)
    h = hashlib.sha256()
    for rel in entry_files(m, environ):
        if rel.name == catalog.MANIFEST and len(rel.parts) == 1:
            continue
        h.update(str(rel).encode() + b"\0" + _file_sha(folder / rel))
    h.update(json.dumps(operational(asdict(m)), sort_keys=True, default=str).encode())
    return h.hexdigest()


DISPLAY = {"name", "what", "credit", "folder", "channel"}  # a manifest's words and where it was read: never what runs
# A capability's words (format 2): its card, row, search words and form labels; changing them needs no new environment.
DISPLAY_2 = {"category", "description", "search_aliases", "experimental"}
PORT_WORDS = {"label", "tooltip", "unit", "advanced", "multiline"}  # an input's or output's words and how its field looks


def operational(manifest: dict) -> dict:
    """A manifest (dataclasses.asdict) as what runs, so an installed generation stays current and an adopted entry stays
    adopted while nothing that runs changed: its words left out, and format 2's fields left out at their defaults (a
    format-1 entry hashes exactly as it did before format 2). Of a capability its inputs' types, defaults, bounds and
    choices, its outputs' types, what it is given unasked (`hidden`), what it refuses, its tests, lineage and Fiji
    command count; its words (category, description, aliases, labels, tooltips, how an empty result is said) don't."""
    runs = {}
    for key, value in manifest.items():
        if key == "members":
            if value:
                runs[key] = [operational(member) for member in value]
            continue
        if key in DISPLAY or key in DISPLAY_2:
            continue
        if key in catalog.FORMAT_2_KEYS:
            if key in ("inputs", "outputs"):
                value = [{k: v for k, v in port.items() if k not in PORT_WORDS} for port in value]
            elif key == "outcomes":
                value = {k: v for k, v in value.items() if k != "empty"}
            if not value:  # at its default (a format-1 entry has none of them)
                continue
        runs[key] = value
    return runs


def signature(environ: Mapping[str, str] = os.environ) -> tuple:
    """Observe external installs and version choices without descending into their Python packages."""
    from . import paths

    base = paths.envs(environ)
    files = set()
    for depth in (1, 2, 3):
        prefix = '*/' * depth
        for name in (RECEIPT, 'chosen.json', '*.proof.json'):
            files.update(base.glob(prefix + name))
    files.add(paths.tools(environ) / 'fiji' / 'current')
    contributions = catalog.catalog_root(environ) / 'fiji-capabilities'
    for name in ('active.json', 'off.json', 'last-failure.json', '*/proof.json'):
        files.update(contributions.glob('*/' + name))
    result = []
    for file in sorted(files):
        try:
            st = file.stat()
            result.append((str(file), st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return tuple(result)


def root(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> Path:
    from . import paths

    return paths.envs(environ) / m.key.replace("/", "--")


def folder(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> Path:
    return root(m, environ) / generation(m, environ)


def _marker(d: Path, kind: str) -> Path:
    """Beside the generation, never in it: uv makes an environment only in an empty folder."""
    return d.with_name(d.name + kind)


def python(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> Path | None:
    """The environment's Python when it is ready (its receipt there, for this lockfile), else None."""
    from . import env_observations
    observed = env_observations.get(m, environ)
    if observed is not None:
        return observed.candidate_python
    if not spec(m):
        return None
    try:
        d = folder(m, environ)
    except OSError:
        return None
    if d.is_symlink() or d.parent.is_symlink():
        return None
    exe = d / "bin" / "python"
    return exe if (d / RECEIPT).is_file() and exe.exists() else None


def state(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict:
    """{"state": ready | building | failed | absent, "says", ...}: read from the folder, at every look."""
    from . import env_observations
    observed = env_observations.get(m, environ)
    if observed is not None:
        return dict(observed.candidate_state)
    try:
        d = folder(m, environ)
    except OSError:
        return {"state": "absent", "says": f"its lockfile ({spec(m).get('lock')}) is missing from its entry"}
    if d.is_symlink() or d.parent.is_symlink():
        return {"state": "failed", "says": "The environment location is a symbolic link; restore its managed location", "folder": str(d)}
    if (d / RECEIPT).is_file():
        receipt = json.loads((d / RECEIPT).read_text())
        return {"state": "ready", "says": f"Installed: its own Python {receipt.get('python', '')}, "
                                         f"{receipt.get('packages', 0)} packages from its lockfile", "folder": str(d),
                "at": receipt.get("at", "")}
    if _marker(d, BUILDING).is_file():
        try:
            pid = int(json.loads(_marker(d, BUILDING).read_text()).get("pid", 0))
            os.kill(pid, 0)
            return {"state": "building", "says": "Being installed", "folder": str(d)}
        except (ValueError, OSError, ProcessLookupError):
            return {"state": "failed", "says": "An install stopped before it finished: install it again", "folder": str(d)}
    if _marker(d, FAILED).is_file():
        why = json.loads(_marker(d, FAILED).read_text()).get("why", "")
        return {"state": "failed", "says": f"Its install failed: {why}", "folder": str(d)}
    return {"state": "absent", "says": "Not installed yet", "folder": str(d)}


def copy(m: catalog.Manifest, dest: Path, environ: Mapping[str, str] = os.environ) -> list[Path]:
    """The entry's files (entry_files) copied to `dest`; returns them."""
    folder, files = catalog.entry_folder(m, environ), entry_files(m, environ)
    for rel in files:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(folder / rel, dest / rel)
    return files


def generations(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> list[tuple[Path, dict]]:
    """Every generation of the entry made here, ready (its receipt there), newest first: (folder, receipt)."""
    from . import env_observations
    observed = env_observations.get(m, environ)
    if observed is not None:
        return [(g.folder, g.receipt) for g in observed.generations]
    out = []
    base = root(m, environ)
    for d in base.iterdir() if base.is_dir() and not base.is_symlink() else ():
        if not re.fullmatch(r'[0-9a-f]{16}', d.name) or d.is_symlink() or not d.is_dir():
            continue
        try:
            out.append((d, json.loads((d / RECEIPT).read_text())))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda pair: pair[1].get("at", ""), reverse=True)


def size_hint(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> int:
    """How many packages its lockfile pins (what the Install button says)."""
    from . import env_observations
    observed = env_observations.get(m, environ)
    if observed is not None:
        return observed.packages
    try:
        return sum(1 for line in lock_file(m, environ).read_text().splitlines() if line[:1].isalnum())
    except OSError:
        return 0


def uv() -> str:
    found = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    if not Path(found).exists():
        raise Failed("uv isn't installed on this Mac (make setup installs it)")
    return found


def clean_env(m: catalog.Manifest | None = None, d: Path | None = None, environ: Mapping[str, str] = os.environ) -> dict:
    """What a process of the environment runs with: its own bin first, the system's after, nothing of the user's shell
    that could make it resolve another install (PYTHONPATH, CONDA_PREFIX, DYLD_*, a virtualenv)."""
    keep = {k: v for k, v in environ.items() if k in ("HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "MICROMIND_CATALOG",
                                                       "MICROMIND_REFUSE_NETWORK", "MICROMIND_EDITION")
            or k.startswith(("OMP_", "MKL_", "VECLIB_", "OPENBLAS_", "NUMEXPR_", "PYTORCH_"))}  # the worker's thread caps
    path = ([str(d / "bin")] if d is not None else []) + ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    out = {**keep, "PATH": os.pathsep.join(path), "PYTHONNOUSERSITE": "1"}
    if m is not None and d is not None and spec(m).get("native"):
        out["CONDA_PREFIX"] = str(d)  # its native recipe's prefix: brainreg takes NiftyReg from here, never /usr/local
    return out


# Python's words for a compiled library macOS wouldn't load: `ImportError: dlopen(<file>, 0x0002): tried: '<file>' (<why>),
# '<the same file in the OS cryptex>' (no such file), '<file>' (<why>)` or `... Library not loaded: <lib> ... Reason: ...`.
_DLOPEN = re.compile(r"dlopen\((?P<path>[^,]+), 0x[0-9a-fA-F]+\): (?P<rest>.*)", re.S)
_TRIED = re.compile(r"'[^']*' \((?P<why>(?:[^()]|\([^()]*\))*)\)")


def native_words(text: str, within: Path | str | None = None) -> str:
    """A compiled library macOS wouldn't load, said plainly: which package (and its version, when the file is inside
    `within`, the environment) and which file, then macOS's own reasons, each once. Python's words name the file's whole
    path three times before any reason, so a record cut at a few hundred characters kept none of it (C4's builder,
    2026-10-14: scipy 1.15.3's wheels, which macOS 27 refuses to load anywhere, sandbox or not). Other words are unchanged."""
    found = _DLOPEN.search(text)
    if not found:
        return text
    rest = found["rest"]
    whys: list[str] = []
    for tried in _TRIED.finditer(rest):
        if tried["why"] not in whys:
            whys.append(tried["why"])
    if len(whys) > 1:  # the same file looked for in the OS's cryptex is never the reason
        whys = [w for w in whys if w != "no such file"] or whys
    missing = re.search(r"Library not loaded: (\S+)", rest)
    if missing:  # each place it was looked for said "no such file": the library it needs is the reason
        whys = [f"it needs {missing[1]}, which isn't there"] + [w for w in whys if not w.startswith("no such file")]
    path = Path(found["path"].strip())
    return (text[:found.start()] + f"macOS couldn't load {_owner(path, within)}: "
            + ("; ".join(whys) if whys else " ".join(rest.split())[:300]))



def last_reason(text: str, within: Path | str | None = None) -> str:
    """Why a Python process ended, from its last words: its error's last line (its reason, not its frames), or, when that
    error is a library macOS wouldn't load, the whole of it (its "Library not loaded" and "Reason" lines are indented
    under it), said plainly (native_words)."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    for i in range(len(lines) - 1, -1, -1):
        if "dlopen(" in lines[i]:
            if all(line[:1].isspace() for line in lines[i + 1:]):  # its own continuation lines, nothing after
                return " ".join(native_words("\n".join(lines[i:]).strip(), within).split())
            break
        if not lines[i][:1].isspace():
            break
    return lines[-1].strip()

def _owner(path: Path, within: Path | str | None) -> str:
    """'scipy 1.15.3's compiled part _spropack.cpython-313-darwin.so': its package by the record its wheel installed."""
    parts = path.parts
    if "site-packages" not in parts or parts[-1] == "site-packages":
        return path.name
    i = parts.index("site-packages")
    site, rel = Path(*parts[:i + 1]), "/".join(parts[i + 1:])
    if within is not None and os.path.realpath(site).startswith(os.path.realpath(within) + os.sep):
        for info in sorted(site.glob("*.dist-info")):
            try:
                record = (info / "RECORD").read_text(errors="replace")
            except OSError:
                continue
            if any(line.split(",", 1)[0] == rel for line in record.splitlines()):
                name, _, version = info.name[:-len(".dist-info")].partition("-")
                return f"{name} {version}'s compiled part {path.name}"
    return f"{parts[i + 1]}'s compiled part {path.name}"


NATIVE_CHUNK = 1 << 20


def fetch_native(pkg: dict, cache: Path, stop: Callable[[], bool] = lambda: False) -> Path:
    """One pinned package of a native recipe, fetched once into the entry's cache and checked by its bytes and sha256."""
    import urllib.request

    name = pkg["url"].rsplit("/", 1)[-1]
    path = cache / name
    if path.is_file() and path.stat().st_size == pkg["bytes"] and _sha256(path) == pkg["sha256"]:
        return path
    cache.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with urllib.request.urlopen(pkg["url"], timeout=60) as response, tmp.open("wb") as out:  # noqa: S310 - pinned https
        while block := response.read(NATIVE_CHUNK):
            if stop():
                raise Failed("cancelled")
            out.write(block)
    if tmp.stat().st_size != pkg["bytes"] or _sha256(tmp) != pkg["sha256"]:
        tmp.unlink(missing_ok=True)
        raise Failed(f"{pkg['name']} downloaded is not the file the catalog pins (its size or content differs)")
    tmp.replace(path)
    return path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(NATIVE_CHUNK):
            h.update(block)
    return h.hexdigest()


def unpack_native(path: Path, d: Path) -> None:
    """A conda package's files into the environment's prefix, by Python's own tarfile (nothing of this Mac's: a .conda's
    zstd needed whatever zstd the system's tar found, Homebrew's here, 2026-10-13); its `info/` folder (the package's own
    metadata) is not part of the environment."""
    import tarfile

    if not path.name.endswith(".tar.bz2"):
        raise Failed(f"{path.name}: a native package is pinned as its .tar.bz2 build")
    with tarfile.open(path) as t:
        t.extractall(d, filter="data")  # nothing outside the prefix, no devices; its relative links kept
    shutil.rmtree(d / "info", ignore_errors=True)


def _run(cmd: list[str], env: dict, stop: Callable[[], bool], what: str, cwd: str | None = None) -> str:
    process = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
                               cwd=cwd)
    try:
        with owned_process.scope(stop=stop), owned_process.watching(process, STEP_S, what):
            out = owned_process.drained(process)  # its last words only: its import check may print without end
    except owned_process.Stopped as exc:
        raise Failed(str(exc)) from exc
    finally:
        owned_process.terminate(process)
    if process.returncode:
        # uv explains a hash mismatch before the final expected/computed digest lines.
        # Keep bounded context so the user sees the cause, not an isolated hash.
        last = [line for line in out.strip().splitlines() if line.strip()][-8:] or ["(no words)"]
        if "Traceback (most recent call last)" in out:  # Python's own error: its reason is its last line, not its frames
            last = [last_reason(out)]
        detail = native_words("\n".join(last))  # a library macOS wouldn't load: which, and why, before any cut
        raise Failed(f"{what} failed: {detail[-1200:]}")  # its last words: the reason comes last
    return out


# Python's compiled files for every installed package, made once while preparing. The sandboxed worker reads its
# runtime read-only and can't write them, so without them each open compiles the packages from source again (L6: a
# freshly prepared Cellpose-SAM runtime had 3 for 15,694 sources; its imports took 40-45% less CPU with them). Only the
# standard library's compiler runs, without site (-S), so no package code (.pth lines) runs outside the sandbox; files
# a package ships deliberately invalid (templates) are skipped, as pip skips them.
# A generation compiled once says so (COMPILED, inside it, so it goes with it): walking 15k current files again took
# 115 s on a loaded Mac. It says so only when every file that can be compiled was: the script exits 1 when a valid
# source has no compiled file (a full disk, a write refused), so an incomplete compile is tried again on the next check.
# It runs serially at background priority (taskpolicy -b). Forking a compiler per CPU
# still competes with capture despite background priority; preparation never fans out. The
# compiled files add about 11% to a runtime (Cellpose-SAM beta-5: 250 MiB over 2.2 GiB); sizes the app measures from
# the disk (capability_space) include them.
COMPILE = """import compileall, importlib.util, os, sys
for p in sys.argv[1:]:
    compileall.compile_dir(p, quiet=2, workers=1)
missing = 0
for p in sys.argv[1:]:
    for folder, _dirs, files in os.walk(p):
        for name in files:
            source = os.path.join(folder, name)
            if not name.endswith('.py') or os.path.exists(importlib.util.cache_from_source(source)):
                continue
            try:
                with open(source, 'rb') as f:
                    compile(f.read(), source, 'exec', dont_inherit=True)
            except (SyntaxError, ValueError):
                continue  # shipped invalid on purpose (a template): never compilable, never a reason to retry
            missing += 1
print(f'{missing} valid sources were not compiled')
sys.exit(1 if missing else 0)
"""
COMPILED = ".bytecode"
BACKGROUND = "/usr/sbin/taskpolicy"  # macOS: run a command at background priority (CPU and disk)


def compiled(d: Path) -> bool:
    return (d / COMPILED).is_file()


def compile_bytecode(d: Path, environ: Mapping[str, str], stop: Callable[[], bool] = lambda: False) -> None:
    sites = sorted(str(p) for p in (d / "lib").glob("python*/site-packages") if p.is_dir() and not p.is_symlink())
    if sites:
        priority = [BACKGROUND, "-b"] if Path(BACKGROUND).exists() else []
        _run([*priority, str(d / "bin" / "python"), "-I", "-S", "-c", COMPILE, *sites], clean_env(environ=environ), stop,
             "compiling its Python code")
    (d / COMPILED).write_text("compileall\n")  # reached only when every compilable file was compiled


def repair_bytecode(d: Path, environ: Mapping[str, str], say: Callable[[str], None],
                    stop: Callable[[], bool] = lambda: False) -> None:
    """A runtime prepared before compile_bytecode existed, compiled on its next check. Optional: a crash, timeout or
    cancel is said and logged, and the check, which already passed, stands; the next check tries again."""
    say("compiling its Python code ahead of time, so its tools start faster")
    try:
        compile_bytecode(d, environ, stop)
    except (Failed, OSError) as exc:
        import sys

        print(f"envs: compiling {d} ahead of time did not finish ({exc}); its tools work and start slower; "
              "the next check tries again", file=sys.stderr, flush=True)
        say("its Python code wasn't compiled ahead of time this time; it works as before, and the next check tries again")


def build(m: catalog.Manifest, environ: Mapping[str, str] = os.environ, say: Callable[[str], None] = lambda s: None,
          stop: Callable[[], bool] = lambda: False) -> Path:
    """Make the entry's environment at its permanent path, prove it, and only then write its receipt (see the
    docstring). Returns its folder; raises Failed with the step's words."""
    if not spec(m):
        raise Failed(f"{m.name} names no environment of its own")
    _require_machine(m)
    d = folder(m, environ)
    with building(d, m.name):  # across processes: the app's Install and maintenance's worker never race one folder
        return _build(m, d, environ, say, stop)


GENERATION_LOCK = ".lock"  # envs/.locks/<entry>--<generation>.lock: held by the process building it, never unlinked


def lock_path(d: Path) -> Path:
    """A generation's build lock, outside the entry's own folder: removing the entry never unlinks a lock another
    process may hold or be about to take (review of #385, round 2), and the emptied entry folder can still go."""
    return d.parent.parent / ".locks" / f"{d.parent.name}--{d.name}{GENERATION_LOCK}"


@contextmanager
def building(d: Path, name: str = "it"):
    """Hold the generation's build lock, or raise Failed when another process holds it. Generation leases are per
    process; this lock is what keeps the app and the maintenance worker from deleting each other's builds."""
    import fcntl

    lock = lock_path(d)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Failed(f"{name} is being prepared by another process right now (an update, or another app): wait for "
                         "it, or stop that first") from exc
        yield
    finally:
        os.close(fd)


def _build(m, d, environ, say, stop):
    from . import generation_leases, scientific_retention
    try:
        if (d / RECEIPT).is_file():
            from . import environment_check
            with generation_leases.use(d):
                environment_check.refresh(m, d, environ, stop=stop)
                if m.runner == "python-env" and not compiled(d):  # prepared before compile_bytecode existed
                    repair_bytecode(d, environ, say, stop)
            return d
        if d.exists():
            with scientific_retention.locked(d.parent.parent) as runtime_root:
                scientific_retention.require_removable(runtime_root, models={m.key}, generations={d.name})
                with generation_leases.withdraw(d):
                    shutil.rmtree(d)
        with generation_leases.prepare(d):
            return _populate(m, d, environ, say, stop)
    except (generation_leases.Busy, generation_leases.Missing) as exc:
        raise Failed(str(exc)) from exc


def _populate(m, d, environ, say, stop):
    """Called only with preparation ownership; removal cannot race receipt publication."""
    _marker(d, FAILED).unlink(missing_ok=True)
    d.parent.mkdir(parents=True, exist_ok=True)
    _marker(d, BUILDING).write_text(json.dumps({"pid": os.getpid(), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}))
    try:
        portable = bool(spec(m).get('wheels'))
        tool = uv()
        env = clean_env(environ=environ)
        env["UV_PYTHON_PREFERENCE"] = "only-managed"  # uv's own CPython at the pinned version, never this Mac's
        say(f"making its own Python {spec(m)['python']}")
        _run([tool, "python", "install", "--quiet", str(spec(m)["python"])], env, stop, "getting its Python")
        _run([tool, "venv", "--quiet", "--python", str(spec(m)["python"]), str(d)], env, stop, "making its Python")
        if portable:
            # Freeze all inputs before package installation. A concurrent edit must
            # never install new bytes under the previously selected generation.
            copy(m, d / SNAPSHOT / m.id, environ)
            captured, problems = catalog.read(d / SNAPSHOT / m.id / catalog.MANIFEST, m.namespace, m.channel)
            if captured is None or _generation_from_files(captured, environ) != d.name:
                raise Failed('The portable candidate changed during preparation; review its current files and prepare again')
            m = captured
        from . import interpreter_identity
        interpreter = interpreter_identity.capture(d / 'bin/python')
        from .environment_wheels import prepare
        prepared_lock = prepare(m, d, environ)
        say(f"installing {size_hint(m, environ)} packages, each checked by its hash")
        from . import sandbox

        # Code nobody reviewed (C7): ready-built packages (wheels) only. A package built from source runs its build
        # script here, outside the sandbox and with the network, before any import could be confined; uv refuses it
        # before running anything (`--no-build`), and the entry says which package to publish as a wheel.
        built = ["--no-build"] if sandbox.required(m) else []
        try:
            _run([tool, "pip", "sync", "--quiet", "--require-hashes", *built, "--python", str(d / "bin" / "python"),
                  str(prepared_lock or lock_file(m, environ))], env, stop, "installing its packages")
        except Failed as exc:
            if built and "build" in str(exc).lower():
                raise Failed(f"{exc}\n{m.name} needs a package built from source here, and code nobody reviewed installs "
                             "only ready-built packages (wheels): its lockfile has to pin a wheel for every package") from exc
            raise
        native = list(spec(m).get("native") or [])
        for pkg in native:  # what a lockfile can't hold, each package by its hash (manifests.NATIVE_KEYS)
            say(f"adding {pkg['name']} (its native part)")
            unpack_native(fetch_native(pkg, root(m, environ) / ".native", stop), d)
        from . import environment_check
        interpreter_identity.verify(d / 'bin/python', interpreter)
        say("compiling its Python code ahead of time, so its tools start faster")
        compile_bytecode(d, environ, stop)
        say("checking installed dependency requirements")
        dependency_check = environment_check.check(m, d, tool, clean_env(m, d, environ), stop=stop)
        imports = list(spec(m).get("imports") or [])
        say("checking it: " + (", ".join(imports) if imports else "its Python runs"))

        # Where its check runs from: a folder the sandbox lets it read. Inherited, it was the app's own (its checkout's
        # services/), which the sandbox denies, so the first getcwd() inside failed (acceptance 2026-10-13: torch's
        # import, PermissionError in os.path.abspath, for the BioImage Model Zoo's affable-shark).
        work = d / ".check"
        checked_in = str(work) if sandbox.required(m) else None

        def confined(cmd: list[str], env_: dict) -> list[str]:
            """Its packages' code, run here for the first time: inside macOS's sandbox when nobody reviewed it (C7)."""
            if not sandbox.required(m):
                return cmd
            if not sandbox.available():
                raise Failed(sandbox.refused(m))
            work.mkdir(exist_ok=True)
            confined_env = sandbox.confine(env_, work)
            env_.clear()
            env_.update(confined_env)
            return sandbox.wrap(cmd, read=[d, sandbox.python_home(d / "bin" / "python"), catalog.entry_folder(m, environ)],
                                write=[work], **({"gpu": False, "children": False} if m.members else {}))

        check_env = clean_env(m, d, environ)
        version = _run(confined([str(d / "bin" / "python"), "-I", "-c",
                                 "import sys" + "".join(f"; import {i}" for i in imports) + "; print(sys.version.split()[0])"],
                                check_env), check_env, stop, "its proof", cwd=checked_in).strip().splitlines()[-1]
        for command in spec(m).get("commands") or []:  # its native part runs from here, as its jobs will run it
            say(f"checking it: {command}")
            program, *args = command.split()
            command_env = clean_env(m, d, environ)
            _run(confined([str(d / "bin" / program), *args], command_env), command_env, stop, f"its proof ({command})",
                 cwd=checked_in)
        uv_version = _run([tool, "--version"], env, stop, "asking uv its version").strip()
        if m.runner == "python-env" and not portable:  # portable recipes already use their frozen snapshot
            copy(m, d / SNAPSHOT / m.id, environ)
        receipt = {"model": m.key, "generation": d.name, "version": m.version, "python": version, "uv": uv_version,
                   "interpreter": interpreter,
                   "dependencyCheck": dependency_check,
                   **({"wheelPreparation": 1} if portable else {}),
                   "native": [{"name": p["name"], "sha256": p["sha256"]} for p in native],
                   "lockSha256": hashlib.sha256(lock_file(m, environ).read_bytes()).hexdigest(),
                   "packages": size_hint(m, environ), "imports": imports, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        tmp = d / (RECEIPT + ".tmp")
        tmp.write_text(json.dumps(receipt, indent=1) + "\n")
        tmp.replace(d / RECEIPT)  # last: from here on it is ready
        return d
    except BaseException as exc:
        why = str(exc) if isinstance(exc, Failed) else f"{type(exc).__name__}: {exc}"
        _marker(d, FAILED).write_text(json.dumps({"why": why, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}))
        raise Failed(why) from exc
    finally:
        _marker(d, BUILDING).unlink(missing_ok=True)


class RemovalIncomplete(Failed):
    """Runtime lookup must refresh even when withdrawn-file cleanup needs a retry."""


def remove(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> bool:
    """Remove managed environment generations, preserving proof inputs and user state."""
    return removal(m, environ)['removed']


def _freed(folder: Path) -> int:
    """At most the bytes removing `folder` frees: regular files with no other hard link (links are never followed). An
    APFS clone has one link but may share its blocks, so this is an upper bound; removal() also measures the disk."""
    total = 0
    for p in folder.rglob('*'):
        stat = p.lstat()
        if p.is_file() and not p.is_symlink() and stat.st_nlink == 1:
            total += stat.st_size
    return total


def removal(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict:
    """remove(), with what it freed: {'removed', 'filesBytes' (an upper bound, counted before the folders go),
    'freeBefore', 'freeAfter' (the disk's free space measured around the removal; other writes change it too)}."""
    r = root(m, environ)
    files, before, after = 0, 0, 0
    from . import generation_leases, scientific_retention, runtime_cleanup
    from .maintenance import prepare as maintenance
    attempt = maintenance.last(m.key, environ)
    if attempt.get("state") in maintenance.RUNNING and maintenance.alive(attempt):
        raise Failed(f"{m.name}'s update is being prepared right now: Stop it on its card first, then remove it")

    try:
        with generation_leases.withdraw(r):
            if r.is_symlink():
                raise Failed('The model environment folder is a symbolic link; restore its managed location before removing it')
            if not r.exists():
                return {'removed': False, 'filesBytes': 0, 'freeBefore': 0, 'freeAfter': 0}
            folders = sorted((p for p in r.iterdir() if not p.is_symlink() and p.is_dir()
                              and (re.fullmatch(r'[0-9a-f]{16}', p.name) or runtime_cleanup.pending(p))), key=str)
            markers = [p for p in r.iterdir() if re.fullmatch(r'[0-9a-f]{16}\.(building|failed)', p.name)
                       and (p.is_file() or p.is_symlink())]
            if not folders and not markers:
                return {'removed': False, 'filesBytes': 0, 'freeBefore': 0, 'freeAfter': 0}
            from contextlib import ExitStack
            held = ExitStack()  # no generation another process is building is taken away
            for one in folders:
                held.enter_context(building(one, m.name))
            with held, scientific_retention.locked(r.parent) as runtime_root:
                if any(not runtime_cleanup.pending(p) for p in folders):
                    scientific_retention.require_removable(runtime_root, models={m.key})
                    from .maintenance import pins
                    try:  # what a recipe, a session, a job or your Pin this version holds stays (ticket 59)
                        pins.require_removable(m.key, {p.name for p in folders}, environ)
                    except pins.Held as exc:
                        raise Failed(str(exc)) from exc
                files = sum(_freed(folder) for folder in folders)
                before = shutil.disk_usage(r).free
                trash = runtime_cleanup.withdraw(folders)
            try:
                for folder in trash:
                    runtime_cleanup.discard(folder)
            except OSError as exc:
                raise RemovalIncomplete('Software was withdrawn, but some files could not be removed. '
                    'Refresh storage and finish the pending removal. Your saved work is kept.') from exc
            # Build markers are disposable; original proof inputs, choices and other files are not.
            for marker in markers:
                marker.unlink(missing_ok=True)
            after = shutil.disk_usage(r).free
            if not any(r.iterdir()):
                r.rmdir()
    except generation_leases.Busy as exc:
        raise Failed(str(exc)) from exc
    from .maintenance import pins
    pins.remove(m.key, "rollback", None, environ)  # a rollback hold goes with the software it held
    # A generation's build lock (lock_path) stays where it is: unlinking it would let a second builder lock a new file
    # while the first still holds the old one (review of #385, round 2). It is a few bytes.
    return {'removed': True, 'filesBytes': files, 'freeBefore': before, 'freeAfter': after}


class Installs:
    """The app's install queue: one at a time, in a thread, each said in Activity (running, then done or failed with
    the step's words), the capabilities sent again when one is ready (`changed`)."""

    def __init__(self, hub=None, changed: Callable[[], None] = lambda: None,
                 environ: Mapping[str, str] = os.environ) -> None:
        self.hub, self.changed, self.environ = hub, changed, environ
        self.rows: dict[str, dict] = {}
        self._queue: list[str] = []
        self._cancel: set[str] = set()
        self._why: dict[str, str] = {}  # why each cancelled install was stopped, said instead of a failure
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stopping = False

    def wire(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.rows.values()]

    def start(self, model: str, by: str = "you", site: dict | None = None, *, recheck: bool = False,
              prepare_only: bool = False) -> dict:
        """Install `model`'s own Python (its environment), set up Fiji (its first generation), or, with `site`, add an
        update site to Fiji in its next generation (fiji_generations.py)."""
        m = catalog.get(model)
        if m is None or not (spec(m) or m.runner == "fiji"):  # a Fiji binding: Prove it on Fiji (a new generation)
            raise Failed(f"there is no model with an environment of its own called {model}")
        from . import channels

        if not channels.trusted(m, self.environ):  # a community channel's: the user allows its code first (ADR 0021)
            raise Failed(f"{m.name} comes from {channels.said(m.channel, self.environ)}, not reviewed here: allow code "
                         "from it first (its card's Allow button)")
        from .bundle_admission import local_problem, scientific
        problem = local_problem(m)
        if prepare_only and (not scientific(m) or site is not None or recheck):
            raise Failed('Prepare for review applies to a scientific integration candidate; use its ordinary setup otherwise')
        key = m.key if site is None else f"{m.key}:{site['site']}"
        name = m.name if site is None else f"{site['site']} in Fiji"
        if m.runner == "python-env" and site is None and not prepare_only:
            from . import env_runner

            # Install (or Turn on) of an entry the user turned off turns it on again; with a proven version of it still
            # here, that version runs again and nothing is built (env_runner.turn_off keeps them all).
            from . import env_observations
            with env_observations.live():
                if not recheck and env_runner.turn_on_again(m, self.environ):
                    found = env_runner.in_use(m, self.environ)
                    if found is not None:
                        with self._lock:
                            self.rows[key] = row = {"model": m.key, "name": name, "state": "done", "by": by,
                                                    "says": f"{name} is on again ({found.m.version}, as it was installed)",
                                                    "activity": f"install:{key}:{int(time.time())}", "recheck": False,
                                                    "onAgain": True}  # a Turn on press files its line over this one (TurnOn._begin)
                        self._say(row, "done", row["says"])
                        self.changed()
                        return dict(row)
        _require_machine(m)
        if problem and not prepare_only:
            raise Failed(problem)
        from .maintenance import prepare as maintenance
        attempt = maintenance.last(m.key, self.environ)
        if attempt.get("state") in maintenance.RUNNING and maintenance.alive(attempt):
            raise Failed(f"{m.name}'s update is being prepared right now: wait for it, or Stop it on its card")
        with self._lock:
            if self._stopping:
                raise Failed("the app's install queue has stopped")
            row = self.rows.get(key)
            if row and row["state"] in ("queued", "installing"):
                return dict(row)
            self.rows[key] = row = {"model": m.key, "name": name, "state": "queued", "by": by, "says":
                                    f"Waiting to {'prepare' if prepare_only else 'install'} {name}", "packages": size_hint(m, self.environ) if spec(m) else 0,
                                    "activity": f"install:{key}:{int(time.time())}", "recheck": recheck,
                                    **({'prepareOnly': True} if prepare_only else {}), **({"site": site} if site else {})}
            self._queue.append(key)
            self._cancel.discard(key)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="catalog-installs", daemon=True)
                self._thread.start()
        self._say(row, "running", f"{'Preparing' if prepare_only else 'Installing'} {name}" + (f" ({row['packages']} packages, its own Python)" if spec(m) else ""))
        self.changed()
        return dict(row)

    def cancel(self, model: str, *, by: str | None = None, why: str = "you pressed Stop") -> dict:
        """Stop an install, queued or running (its build or its proof ends with its owned processes). Said as stopped,
        with `why`, never as a failure of the model: C7 Window B (finding 8) read "Couldn't install …: its proof failed:
        … run cancelled" after the user's own Stop."""
        m = catalog.get(model)
        key = m.key if m is not None else model
        with self._lock:
            row = self.rows.get(key)
            if row is None or (by is not None and row.get("by") != by):
                return dict(row or {"model": key, "state": "none"})
            if row["state"] not in ("queued", "installing"):
                return dict(row)  # it ended already: nothing to stop, and what it said stays
            self._cancel.add(key)
            self._why[key] = why
            if key in self._queue:
                self._queue.remove(key)
                row.update(state="cancelled", says=stopped_words(row["name"], why, preparing=row.get("prepareOnly", False)))
            result = dict(row)
        if result["state"] == "cancelled":
            self._say(result, "done", result["says"])
            self.changed()
        return result

    def stop(self) -> bool:
        """Cancel this app's active proof/build, withdraw queued installs, and wait a bounded time for cleanup."""
        with self._lock:
            self._stopping = True
            self._cancel.update(self.rows)
            queued = [self.rows[key] for key in self._queue]
            self._queue.clear()
            for row in queued:
                row.update(state="failed", says="cancelled")
            thread = self._thread
        for row in queued:
            self._say(row, "failed", f"Couldn't {'prepare' if row.get('prepareOnly') else 'install'} {row['name']}: cancelled")
        if thread is not None:
            thread.join(timeout=3)
        self.changed()
        return thread is None or not thread.is_alive()

    def _say(self, row: dict, state: str, text: str, lines: list[str] | None = None) -> None:
        if self.hub is None:
            return
        from . import activity

        with __import__("contextlib").suppress(Exception):  # the wire says it either way
            activity.activity(self.hub, "model", text, id=row["activity"], state=state, ref={"model": row["model"]},
                              details={"lines": lines} if lines else None)

    def _run(self) -> None:
        from .thumbnails import background_priority

        background_priority()  # never takes the live view's time
        while True:
            with self._lock:
                if not self._queue:
                    return
                key = self._queue.pop(0)
                row = self.rows[key]
                verb = 'Preparing' if row.get('prepareOnly') else 'Installing'
                row.update(state="installing", says=f"{verb} {row['name']}")
            m = catalog.get(row["model"])

            def say(step: str) -> None:
                with self._lock:
                    row["says"] = f"{verb} {row['name']}: {step}"
                self.changed()

            try:
                if row.get('prepareOnly'):
                    d = prepare_candidate(m, self.environ, say=say, stop=lambda: key in self._cancel)
                    lines = ['Prepared for private review. Its workflow is not qualified and no operation was activated.']
                else:
                    d, lines = install(m, self.environ, say=say, stop=lambda: key in self._cancel, site=row.get("site"), recheck=row.get("recheck", False))
                with self._lock:
                    row.update(state="done", says=f"{row['name']} is {'prepared' if row.get('prepareOnly') else 'installed'}"
                               + (f"; {lines[-1]}" if m.runner == "python-env" else ""), folder=str(d))
                self._say(row, "done", f"{'Prepared' if row.get('prepareOnly') else 'Installed'} {row['name']}", lines)
            except Failed as exc:
                with self._lock:
                    stopped = key in self._cancel and not self._stopping
                    if stopped:  # its Stop ended the build or the proof: what was asked happened
                        row.update(state="cancelled", says=stopped_words(row["name"], self._why.get(key, ""), preparing=row.get("prepareOnly", False)))
                    else:
                        row.update(state="failed", says=str(exc))
                if stopped:
                    self._say(row, "done", row["says"], [f"Ended at: {exc}"])
                else:
                    self._say(row, "failed", f"Couldn't {'prepare' if row.get('prepareOnly') else 'install'} {row['name']}: {exc}")
            except Exception as exc:  # noqa: BLE001 - anything else (a folder gone, a disk error) fails this install, said,
                # and the queue goes on: it once ended the queue's thread, its row "installing" forever and the next never run
                with self._lock:
                    row.update(state="failed", says=f"{type(exc).__name__}: {exc}")
                self._say(row, "failed", f"Couldn't {'prepare' if row.get('prepareOnly') else 'install'} {row['name']}: {type(exc).__name__}: {exc}")
            finally:
                with self._lock:
                    self._cancel.discard(key)
                    self._why.pop(key, None)
            self.changed()

def stopped_words(name: str, why: str, *, preparing: bool = False) -> str:
    return f"Stopped: {name} wasn't {'prepared' if preparing else 'installed'}" + (f" ({why})" if why else "")


def _require_machine(m: catalog.Manifest, *, probe: bool = False) -> None:
    """Recheck the same machine facts as discovery before any install or reactivation."""
    from . import readiness

    if probe and m.runner == 'fiji' and m.namespace:
        from . import fiji_sandbox
        fiji_sandbox.supported()  # install runs off-loop; display reads never launch this probe
    if why := readiness.misfit(m):
        raise Failed(f"{m.name} cannot be prepared on this computer: {why}")


def prepare_candidate(m, environ=os.environ, say=lambda text: None, stop=lambda: False):
    """Prepare a scientific candidate's dependencies without operation proof or activation."""
    from . import channels
    from .bundle_admission import scientific
    if m is None or not scientific(m):
        raise Failed('Choose a scientific integration candidate to prepare for private review')
    if not channels.trusted(m, environ):
        raise Failed('Allow code from this community catalog before preparing its candidate')
    _require_machine(m)
    from . import bundle_admission, env_runner, own_trials
    if bundle_admission.local_workflow(m):
        own_trials.await_comparison(m.key, environ, review_kind='local-workflow')
        env_runner.preserve_active(m, environ)
    return build(m, environ, say=say, stop=stop)


def install(m: catalog.Manifest, environ: Mapping[str, str] = os.environ, say: Callable[[str], None] = lambda s: None,
            stop: Callable[[], bool] = lambda: False, site: dict | None = None, *, recheck: bool = False) -> tuple[Path, list[str]]:
    """What Install does, for the queue and make catalog-install alike: Fiji's next generation (an update site added, or a
    binding proven), or the entry's own Python made from its lockfile and, for an entry with its own adapter, proven by
    running it. Returns its folder and what to say; Failed with the step's words."""
    from . import channels
    from .bundle_admission import local_problem, local_reader, local_workflow

    _require_machine(m, probe=True)
    if problem := local_problem(m):
        raise Failed(problem)
    if not channels.trusted(m, environ):
        raise Failed(f"{m.name} comes from {channels.said(m.channel, environ)}, not reviewed here: allow code from "
                     "it first (its card's Allow button)")
    if local_reader(m) or local_workflow(m):
        from . import own_trials

        # Mark before proof can become usable. Setup is never the researcher's acceptance;
        # a retained acceptance still covers the same exact proof after a no-op recheck.
        own_trials.await_comparison(m.key, environ, review_kind='local-workflow' if local_workflow(m) else 'scientific-review')
    t0 = time.monotonic()
    if m.runner == "fiji":  # Fiji: a generation (fiji_generations.py), never an environment
        from . import fiji_generations as fiji

        try:
            if m.namespace:
                from . import fiji_contributions

                if site:
                    raise fiji.Failed("A contribution cannot modify Fiji; use the reviewed staged Fiji setup")
                return fiji_contributions.install(m, environ, say=say, stop=stop)
            if site:
                n = fiji.add_site(site["site"], site["url"], environ, say=say, stop=stop)
            elif m.id != "fiji" or (recheck and fiji.current(environ) is not None):  # preserve the current generation; prove a new one
                n = fiji.renew(environ, say=say, stop=stop)
            else:
                n = fiji.first(environ, say=say, stop=stop)
        except (fiji.Failed, owned_process.Stopped) as exc:
            raise Failed(str(exc)) from exc
        return fiji.root(environ) / str(n), [f"Fiji generation {n}, proven and in use, in {round(time.monotonic() - t0)} s"]
    if m.runner == "python-env":
        from . import env_runner

        env_runner.preserve_active(m, environ)
    d = build(m, environ, say=say, stop=stop)
    from . import generation_leases
    try:
        with generation_leases.use(d):
            lines = [f"Its own Python, {size_hint(m, environ)} packages each checked by its hash, in "
                     f"{round(time.monotonic() - t0)} s", f"In {d}"]
            if m.runner == "python-env":  # its own adapter: proven by running it before it is a choice
                from . import env_runner, jobs

                if local_workflow(m):
                    return d, lines + ['Prepared for a local workflow. Choose it for a saved Project, use its controls, Save tool and reopen; then review and enable it in Models. Scientific accuracy remains yours to assess.']

                say("proving it on its own example (a real run: minutes)" if jobs.is_job(m) else "checking its adapter contract; the proof records the actual input")
                try:
                    p = env_runner.prove(m, environ, stop=stop)
                except jobs.Cancelled as exc:
                    raise Failed("cancelled") from exc
                if not p.get("ok"):
                    raise Failed(f"its proof failed: {p.get('why')}")
                lines.append(f"Its proof: {p['says']}" if p.get("says") else
                             f"Adapter contract checked: found {p['found']} on {env_runner.proof_input_says(p)} "
                             f"({p['picture']}) in {p['seconds']} s")
            return d, lines
    except (generation_leases.Busy, generation_leases.Missing) as exc:
        raise Failed(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    """make catalog-install ID=: what an entry's Install button does, from the command line."""
    import argparse

    parser = argparse.ArgumentParser(description="Install an entry's own Python (or set up Fiji), as its card's Install does.")
    parser.add_argument("ids", nargs="+")
    args = parser.parse_args(argv)
    failed = 0
    for i in args.ids:
        m = catalog.get(i)
        if m is None or not (spec(m) or m.runner == "fiji"):
            print(f"{i}: no entry with an environment of its own (or Fiji) is called that")
            failed += 1
            continue
        try:
            _, lines = install(m, say=lambda s, i=i: print(f"{i}: {s}", flush=True))
            print("\n".join(f"{i}: {line}" for line in lines))
        except Failed as exc:
            print(f"{i}: {exc}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
