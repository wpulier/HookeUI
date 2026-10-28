"""The app's own Python as an environment (catalog/README.md "The app's own Python"): what it is made from, what the code
run in it imports, and how a Python in use differs from it.

Entries with an adapter of their own (runner python-env) each have a Python of their own, made from a hashed lockfile and
proven per generation (envs.py). The built-in models (runner gpu-worker) share the app's own Python instead, made by
`make setup` and by the Mac app's first launch from one lockfile, `services/uv.lock`. Live 2026-10-19 (PR #346), two
of them broke in it because nothing tied their code to that lock:
- Molmo 2's pinned code imported einops, which the lock never held: it worked only where `make sam3-setup` had happened
  to install it, so a fresh setup and the shipped app failed at load, 15 s in, after readiness had said Ready.
- `make sam3-setup` pinned two packages below the lock's versions, so the next `make setup` moved them back and SAM 3.1
  stopped importing while its package was still found (`find_spec` is not an import).
- The capability check ran in a Python that differed from the shipped one, and nothing said so.

So here: the imports of a model's pinned code are derived from that code (its hashes fix its imports; nothing is
declared that could drift from it), checked against this Python at readiness and against the lock by the tests, the pin
and the catalog's CI; and `drift` says how a Python in use differs from the lock, which the capability check prints.
Reading any of this imports no model library and runs nothing.
"""
from __future__ import annotations

import ast
import functools
import hashlib
import importlib.metadata
import importlib.util
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Iterable, Mapping

from .paths import SERVICES

LOCK = SERVICES / "uv.lock"
FIRST_PARTY = frozenset({"orchestrator", "gpu_worker", "research_worker", "scientific_compute", "env_worker", "fiji_worker", "bench", "sim",
                         "noncommercial", "tests"})
SOURCE_LIMIT = 4 << 20  # a pinned code file larger than this is not read for its imports (Molmo 2's largest: 72 KB)
OPTIONAL = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}  # a try that handles one: optional
AVAILABLE = re.compile(r"is_\w+_available")  # transformers' guards (`if is_flash_attn_2_available():`): optional


def canonical(name: str) -> str:
    """A distribution's name as the lock and the package index compare them (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _optional_try(node: ast.Try) -> bool:
    for handler in node.handlers:
        names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        if handler.type is None or any(isinstance(n, ast.Name) and n.id in OPTIONAL for n in names):
            return True
    return False


def _guarded(node: ast.If) -> bool:
    return any(isinstance(n, ast.Call) and AVAILABLE.fullmatch(getattr(n.func, "id", getattr(n.func, "attr", "")) or "")
               for n in ast.walk(node.test))


def source_imports(source: str) -> set[str]:
    """The top-level modules a Python source needs: every absolute import, except those it treats as optional (inside a
    try that handles ImportError, or under transformers' `if is_..._available():`), as transformers' own check of a
    model's remote code does. Relative imports are its own files. Raises SyntaxError."""
    needed: set[str] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.Try) and _optional_try(node):
            for child in node.finalbody:
                visit(child)
            return
        if isinstance(node, ast.If) and _guarded(node):
            for child in node.orelse:
                visit(child)
            return
        if isinstance(node, ast.Import):
            needed.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            needed.add(node.module.split(".")[0])
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(ast.parse(source))
    return needed


def third_party(modules: Iterable[str], own: Iterable[str] = ()) -> set[str]:
    """`modules` without Python's own, the app's own and `own` (a model's own code files, by their names)."""
    return {m for m in modules if m not in sys.stdlib_module_names and m not in FIRST_PARTY and m not in set(own)
            and m != "__future__"}


_pinned: dict[tuple, frozenset[str]] = {}


def pinned_code_imports(m, environ: Mapping[str, str] = os.environ) -> frozenset[str] | None:
    """The third-party modules a model's pinned Python files import (a Hugging Face model's own code, run with
    `trust_remote_code`), read from its downloaded revision; None when it pins no code or the files aren't here. Read once
    per revision: a published revision is never written over."""
    from gpu_worker import model_files

    code = [f for f in model_files.files(m) if str(f.get("path", "")).endswith(".py")]
    if not code or model_files.standin(environ):
        return None
    try:
        where = model_files.model_dir(m.id, environ)
    except Exception:  # not downloaded (NotDownloaded) or not a model folder: nothing to read yet
        return None
    key = (str(where), tuple(sorted(str(f.get("sha256") or f.get("git_sha1")) for f in code)))
    if key not in _pinned:
        found: set[str] = set()
        for f in code:
            path = where / f["path"]
            try:
                if path.stat().st_size > SOURCE_LIMIT:
                    continue
                found |= source_imports(path.read_text())
            except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
                continue  # what can't be read is the download's to report (its check by hash), not this one's
        own = {Path(str(f["path"])).stem for f in code}
        _pinned[key] = frozenset(third_party(found, own))
    return _pinned[key]


def missing(modules: Iterable[str]) -> list[str]:
    """Those of `modules` this Python can't find, sorted. Finding is not importing: an import can still fail (the capability
    check's run says so); this says only what is certainly absent, cheaply."""
    importlib.invalidate_caches()
    return sorted(m for m in modules if importlib.util.find_spec(m) is None)


def _python_identity() -> tuple:
    """What changes when a package is installed into or removed from this Python: the lock, and each folder imports are
    read from (adding or removing a package changes its folder's entries, so its mtime). A few stats."""
    out = []
    for p in (str(LOCK), *sys.path):
        try:
            out.append(os.stat(p or ".").st_mtime_ns)
        except OSError:
            out.append(None)
    return tuple(out)


_said: dict[tuple, str] = {}


def missing_from_pinned_code(m, environ: Mapping[str, str] = os.environ) -> str:
    """Why a built-in model's own pinned code can't run in this Python ("" when nothing is certainly missing). Kept per
    pinned revision and this Python's identity, so asking again costs a few stats: the readiness of every card, option
    and refusal asks it, outside readiness.batch too (`implementation`)."""
    from gpu_worker import manifests as catalog
    from gpu_worker import model_files

    if m.runner != "gpu-worker" or not any(str(f.get("path", "")).endswith(".py") for f in model_files.files(m)):
        return ""
    owner = model_files.owner(m)
    key = (owner.key, catalog.revision_of(owner), _python_identity())
    if key in _said:
        return _said[key]
    needed = pinned_code_imports(m, environ)
    if needed is None:  # not downloaded (or the tests' stand-ins): asked again next time, never kept
        return ""
    gone = missing(needed)
    said = "" if not gone else (
        f"not installed here ({', '.join(gone)}: its pinned code imports {'it' if len(gone) == 1 else 'them'} and the "
        "app's own Python doesn't have " + ("it" if len(gone) == 1 else "them") + "; `make setup`, or the app update "
        "that adds it to services/uv.lock)")
    if len(_said) > 256:
        _said.clear()
    _said[key] = said
    return said


_locked: dict = {}


def locked(lock: Path = LOCK) -> dict[str, str]:
    """Every distribution the lock holds, by canonical name, with its version (all groups, every platform)."""
    try:
        stamp = lock.stat().st_mtime_ns
    except OSError:
        return {}
    if _locked.get("key") != (str(lock), stamp):
        packages = tomllib.loads(lock.read_text()).get("package", [])
        _locked.update(key=(str(lock), stamp), value={canonical(p["name"]): str(p.get("version", ""))
                                                      for p in packages if "name" in p})
    return _locked["value"]


def lock_id(lock: Path = LOCK) -> str:
    try:
        return hashlib.sha256(lock.read_bytes()).hexdigest()[:12]
    except OSError:
        return "missing"


@functools.cache
def _distributions() -> Mapping[str, list[str]]:
    return importlib.metadata.packages_distributions()  # every installed distribution read: once per process


def providers(module: str) -> list[str]:
    """The installed distributions that provide a top-level module (canonical names; none when it isn't installed)."""
    return sorted({canonical(d) for d in _distributions().get(module, [])})


def unlocked(modules: Iterable[str], allowed: Iterable[str] = (), lock: Path = LOCK) -> dict[str, str]:
    """Of `modules` (third-party, top-level), each one services/uv.lock doesn't provide, with why. Only the lock is asked
    here: whether this Python has a module installed is another fact (`missing`), never a reason the lock lacks it. An
    installed module is matched to the lock by the distributions that provide it (yaml is PyYAML's); one not installed,
    by its own name, which is all the lock can say without installing it. `allowed` are modules an entry's own install
    step puts here (`install:sam3`), said by that step instead."""
    held, out = locked(lock), {}
    for module in sorted(set(modules) - set(allowed)):
        dists = providers(module)
        if dists and not any(d in held for d in dists):
            out[module] = (f"installed here as {', '.join(dists)}, but services/uv.lock doesn't hold it (another install "
                           "put it here)")
        elif not dists and canonical(module) not in held:
            out[module] = ("services/uv.lock holds no distribution of that name, and none installed here provides it "
                           "(a module named unlike its distribution is read once installed)")
    return out


def drift(lock: Path = LOCK) -> dict:
    """How this Python differs from the lock: distributions installed beyond it, and locked ones at another version.
    Locked distributions absent here are not drift (the lock holds every platform's and every group's)."""
    held = locked(lock)
    extra, differs = [], []
    for dist in importlib.metadata.distributions():
        name = canonical(dist.metadata["Name"] or "")
        if not name:
            continue
        if name not in held:
            extra.append((name, dist.version))
        elif held[name] and held[name] != dist.version:
            differs.append((name, dist.version, held[name]))
    return {"lock": lock_id(lock), "extra": sorted(set(extra)), "differs": sorted(set(differs))}


def says(d: dict) -> str:
    """One line for a report: which lock, and how this Python differs from it."""
    head = f"the app's Python (services/uv.lock {d['lock']})"
    if not d["extra"] and not d["differs"]:
        return f"{head} matches its lock"
    parts = []
    if d["extra"]:
        parts.append("beyond the lock: " + ", ".join(f"{n} {v}" for n, v in d["extra"]))
    if d["differs"]:
        parts.append("not the lock's version: " + ", ".join(f"{n} {v} (lock {w})" for n, v, w in d["differs"]))
    return f"{head} differs from what a fresh setup and the Mac app get; " + "; ".join(parts)
