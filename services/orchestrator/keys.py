"""API keys: where each comes from, and the one value the app uses (docs/design/2026-10-12-the-open-catalog.md, P1).

Every key the catalog's services name (catalog/services, KNOWN_KEYS) resolves to exactly one source, in this order:
1. **environment**: set outside the app (the shell, launch settings) before the app read any key. It wins, and the app
   never changes or removes it.
2. **keychain**: saved by this app (keychain.py), added, replaced and removed from the studio.
3. **file**: the keys file (Paths.keys_file, decided in paths.py), KEY=value lines; blank values and # comments ignored. The app only reads it.

`set_environment()` puts the resolved values into os.environ (and unsets a key no source has any more), which every client reads.
Which names it put there is kept in MICROMIND_APPLIED_KEYS (names only), so a child process (the research and GPU
workers inherit the environment) never mistakes them for keys set outside the app. Values are never printed or logged.
"""
from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path

from gpu_worker.manifests import services

from . import keychain
from .paths import Paths

KNOWN_KEYS = tuple(service.key for service in services().values())  # one per cloud service (catalog/services)
APPLIED = "MICROMIND_APPLIED_KEYS"
SOURCES = ("environment", "keychain", "file")
SOURCE_WORDS = {"environment": "set outside the app", "keychain": "saved by this app", "file": "from your keys file"}

@dataclass(frozen=True)
class Resolved:
    name: str
    source: str | None  # one of SOURCES, or None when no source has it
    value: str | None = None  # never on a wire, a log or a prompt

    @property
    def present(self) -> bool:
        return bool(self.value)


def outside(environ=os.environ) -> frozenset[str]:
    """Keys set outside the app: in the environment, and not put there by an app process (APPLIED, inherited by the
    workers from their parent)."""
    applied = {n for n in environ.get(APPLIED, "").split(",") if n}
    return frozenset(n for n in KNOWN_KEYS if environ.get(n) and n not in applied)


def file_problem(path: str | os.PathLike[str] | None = None) -> str:
    """Why the keys file is there but this app can't read it, in plain words; "" when it reads, or when there is none.
    macOS privacy (TCC) refuses a Desktop file to an app not allowed the Desktop folder with EPERM and no prompt
    (F-L8-3): every key then looked "Not added" while the truth was "can't read". Only opened, never read here."""
    keys_file = Path(path) if path else Paths.from_env().keys_file
    try:
        with keys_file.open("rb"):
            return ""
    except PermissionError:
        pass
    except OSError as exc:
        if exc.errno not in (errno.EPERM, errno.EACCES):
            return ""
    shown = str(keys_file).replace(str(Path.home()), "~", 1)
    where = "the Desktop folder" if keys_file.parent == Path.home() / "Desktop" else "its folder"
    return (f"This app isn't allowed to read your keys file ({shown}), so its keys can't be used. Allow it in System "
            f"Settings → Privacy & Security → Files and Folders ({where}), move the file somewhere it can read, or add "
            f"each key here to keep it in the Keychain.")


def read_file(path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    keys_file = Path(path) if path else Paths.from_env().keys_file
    out: dict[str, str] = {}
    try:
        lines = keys_file.read_text().splitlines() if keys_file.exists() else []
    except OSError:
        return out
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = (part.strip() for part in line.split("=", 1))
        value = value.strip("'\"")
        if name and value:
            out[name] = value
    return out


def resolve(path: str | os.PathLike[str] | None = None, environ=os.environ) -> dict[str, Resolved]:
    """Each known key's one source and value, without changing anything."""
    from_file = read_file(path)
    out = {}
    for name in KNOWN_KEYS:
        if name in outside(environ):
            out[name] = Resolved(name, "environment", environ.get(name) or None)
        elif value := keychain.get(name):
            out[name] = Resolved(name, "keychain", value)
        elif name in from_file:
            out[name] = Resolved(name, "file", from_file[name])
        else:
            out[name] = Resolved(name, None)
    return out


def set_environment(resolved: dict[str, Resolved], environ=os.environ) -> tuple[dict[str, str], list[str]]:
    """Put the resolved values into the environment: (what changed or appeared, what was removed). Keys set outside the
    app are never touched."""
    changed, removed = {}, []
    applied = {n for n in environ.get(APPLIED, "").split(",") if n}
    for name, r in resolved.items():
        if r.source == "environment":
            continue
        if r.value:
            if environ.get(name) != r.value:
                changed[name] = r.value
            environ[name] = r.value
            applied.add(name)
        else:
            if name in applied and environ.get(name):
                removed.append(name)
            if name in applied:
                environ.pop(name, None)
            applied.discard(name)
    environ[APPLIED] = ",".join(sorted(applied))
    return changed, removed


def apply_values(set_: dict[str, str], unset: list[str], environ=os.environ) -> None:
    """A parent's change, applied in a child process (the research and presentation workers, the GPU workers): only
    known keys, and never one set outside the app."""
    applied = {n for n in environ.get(APPLIED, "").split(",") if n}
    for name, value in set_.items():
        if name in KNOWN_KEYS and name not in outside(environ) and value:
            environ[name] = value
            applied.add(name)
    for name in unset:
        if name in KNOWN_KEYS and name not in outside(environ):
            environ.pop(name, None)
            applied.discard(name)
    environ[APPLIED] = ",".join(sorted(applied))


def load_keys(path: str | os.PathLike[str] | None = None) -> dict[str, bool]:
    """Resolve and apply every key; returns which are available (True/False), never the values."""
    resolved = resolve(path)
    set_environment(resolved)
    return {name: r.present for name, r in resolved.items()}


def sources(path: str | os.PathLike[str] | None = None) -> dict[str, str | None]:
    """Where each key comes from now (SOURCES or None), for the studio and the brain: never a value."""
    return {name: r.source for name, r in resolve(path).items()}
