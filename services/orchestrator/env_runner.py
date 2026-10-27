"""Entries that bring their own adapter (runner "python-env"; phase 3's E4): how a contributed model or algorithm runs
with no change to the app's code.

An entry's folder holds its manifest, its lockfile (every package by its hash, the adapter's own dependencies and the
model's library among them) and its adapter, `adapter.py`, a small reviewed file whose function takes the picture and
returns a label map or polygons. Its weights, when it has any, are the manifest's pinned `files` (Download).

- **Install** (envs.py) makes its environment from the lockfile, then **proves it by function**: its adapter runs once
  in that environment, on an identified asset image or generated fixture, with Python socket/DNS refusal. Its output
  is checked (a label map the picture's size, or polygons inside it). The proof is kept beside the environment
  (`<generation>.proof.json`).
- **Only a proven entry is a choice:** an implementation of the operation it declares, and an option of that operation's
  group (Cells, Outlining...), under its own name, registered at start and whenever the catalog changes
  (`register`). Its licence is its declared component when it has one (`component` in its manifest).
- **It runs only in its own environment:** `services/env_worker/server.py` with the environment's Python and a clean
  environment, one process per entry, kept, started on first use, with Python socket/DNS refusal (gpu_worker/offline.py).
  The catalog pins and fetches dependencies. Own, shared, community and template adapters for which
  `sandbox.required()` is true also require the OS filesystem/network sandbox at proof and runtime; unavailable
  isolation refuses execution. Reviewed built-ins use Python socket/DNS refusal. Dependency installation uses
  pinned wheels separately from the adapter's isolated proof; neither path certifies scientific suitability.
- **A new version installs beside the one in use** (the review's correction 4): a generation keeps the entry as it was
  installed (envs.SNAPSHOT: its manifest and adapter), and runs from that copy with the weights its own manifest pins.
  When the catalog brings a new version, the one installed keeps running (`in_use`), the card says "Update available",
  and the new one is used only after its proof passes and the user explicitly selects it.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import json
import os
import signal
import subprocess
import threading
import tempfile
import time
import weakref
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, Mapping

import numpy as np

from gpu_worker import manifests as catalog
from gpu_worker import model_files

from . import envs, generation_leases, owned_process

SERVER = Path(__file__).resolve().parent.parent / "env_worker" / "server.py"
OUTPUTS = {"labels.instance.2d", "masks.2d"}  # what an adapter may give (a label map; polygons are filled the same way)
RUN_S = 180.0  # analysis runtime, with the manifest's load allowance added on the first call


class Failed(RuntimeError):
    pass


class Unsupported(Failed):
    """Its adapter refused this input in words ({"unsupported": "why"}): an outcome of its own, never a failure."""

    def __init__(self, message: str, next: str | None = None) -> None:
        super().__init__(message)
        self.next = next


class AdmittedOutput(dict):
    """The encoded output owns its admitted bytes until the operation's result is released."""
    def __init__(self, output: dict, reservation):
        super().__init__(output)
        self._release = weakref.finalize(self, reservation.release)


def entries(environ: Mapping[str, str] = os.environ) -> list[catalog.Manifest]:
    return [m for m in {**catalog.manifests(), **catalog.mine(environ)}.values() if m.runner == "python-env"]


def proof_path(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> Path:
    d = envs.folder(m, environ)
    return d.with_name(d.name + ".proof.json")


def proof(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict | None:
    from . import env_observations
    observed = env_observations.get(m, environ)
    if observed is not None:
        return observed.candidate_proof
    try:
        return json.loads(proof_path(m, environ).read_text())
    except (OSError, ValueError):
        return None


def proof_complete(m, folder, receipt):
    from .bundle_runtime import proof_complete as checked
    return checked(m, folder, receipt)


def expanded(installed):
    from .bundle_runtime import expand
    return expand(installed)


def _proof_of(d: Path) -> dict | None:
    try:
        return json.loads(d.with_name(d.name + ".proof.json").read_text())
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class Installed:
    """One generation that runs: the entry as it was installed (its snapshot's manifest), its Python and its proof."""
    m: catalog.Manifest
    folder: Path
    proof: dict
    current: bool  # the catalog's version; an earlier selection stays active until explicitly changed

    @property
    def python(self) -> Path:
        return self.folder / "bin" / "python"

    @property
    def entry(self) -> Path:
        return self.folder / envs.SNAPSHOT / self.m.id


CHOSEN = "chosen.json"  # exact active generation; also preserves legacy resolution before preparing updates


def chosen(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> str:
    """The selected generation, or empty for a legacy installation without an explicit pointer."""
    from . import env_observations
    observed = env_observations.get(m, environ)
    if observed is not None:
        return observed.selection
    try:
        return str(json.loads((envs.root(m, environ) / CHOSEN).read_text()).get("generation") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def _select(m: catalog.Manifest, found: Installed, environ: Mapping[str, str], *, preserve: bool = False,
            by: str = "user") -> None:
    path = envs.root(m, environ) / CHOSEN
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".selection-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"generation": found.folder.name, "version": found.m.version,
                       "by": "setup" if preserve else by, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}, stream)
        if preserve:
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        else:
            os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def preserve_active(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> None:
    """Pin legacy resolution before a mutation; concurrent explicit selection wins."""
    if (envs.root(m, environ) / CHOSEN).exists():
        return
    found = _resolved_generation(m, environ)
    if found is not None:
        _select(m, found, environ, preserve=True)


class Revoked(Failed):
    """A revoked version asked to be made active: refused, the one in use stays."""


def choose(m: catalog.Manifest, name: str | None, environ: Mapping[str, str] = os.environ) -> Installed:
    """Explicitly select an exact proven generation, including Use the newest."""
    from . import env_observations
    with env_observations.live():
        name = name or envs.folder(m, environ).name
        found = generation(m, name, environ)
    if found is None:
        raise Failed(f"that version of {m.name} is no longer here, ready and proven")
    from .maintenance import policy, revocations
    notice = revocations.revoked(found.m, found.m.version, environ)
    now = selected(m, environ)  # the selection even while turned off: Turn on again would activate it (round 3)
    if notice is not None and (now is None or now.folder != found.folder):
        # the same refusal as maintenance's promote: a revoked version is never made active (#402 review round 2)
        raise Revoked(f"{found.m.name} {found.m.version} was revoked by its maintainers: {notice['why']}; it isn't "
                     "applied")
    from .bundle_admission import local_workflow
    if now is not None and now.folder != found.folder and local_workflow(found.m):
        raise Failed('Use the exact update’s Review and Apply, or Undo, so its saved-state check and rollback remain recorded')
    policy.leave_pin(m, found.folder.name, environ)  # your choice of another version ends a pin, recorded
    _select(m, found, environ)
    from .maintenance import discover
    # Going back by hand (to an older catalog version, or one a promotion moved away from) holds automatic maintenance
    # until a person records a policy again.
    if not found.current or found.folder.name in discover.superseded(m, environ):
        policy.hold(m.key, f"you went back to {found.m.version}", environ)
    return found


def promote(m: catalog.Manifest, name: str, by: str, environ: Mapping[str, str] = os.environ) -> Installed:
    """Make an exact proven generation the one new work uses, recording who decided (ADR 0025: `by` is "user" or
    "maintenance:<policy>"). One atomic replace of the selection: a reader sees the old one or the new one. Work that
    already holds a generation keeps it. orchestrator/maintenance/promote.py writes the auditable decision."""
    from . import env_observations
    with env_observations.live():
        found = generation(m, name, environ)
    if found is None:
        raise Failed(f"that version of {m.name} is no longer here, ready and proven")
    _select(m, found, environ, by=by)
    return found


def chosen_by(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict:
    """The selection's own record ({"generation", "version", "by", "at"}), or {} without one. A cheap file read."""
    try:
        value = json.loads((envs.root(m, environ) / CHOSEN).read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


OFF = "off.json"  # beside the entry's generations: the user turned it off (its installed versions kept, none runs)


def turned_off(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> bool:
    from . import env_observations
    observed = env_observations.get(m, environ)
    return observed.off if observed is not None else (envs.root(m, environ) / OFF).is_file()


class InUse(RuntimeError):
    """Turn off was refused because a run is using the version that is on: nothing was changed."""


def turn_off(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> None:
    """The card's Turn off, for an entry with its own adapter (any: yours, the reviewed catalog's, a community channel's):
    it is no longer a choice and its worker is retired by the register that follows; nothing is deleted, and Turn on makes
    it a choice again with the version it had, nothing installed again (C7 Window B, finding 5).

    Refused (InUse) while a run uses the version that is on: retiring its worker would end that run unsaid (L7b, row 12).
    The check holds the generation for the moment the marker is written, so no run can start in between."""
    found = in_use(m, environ)
    try:
        with generation_leases.withdraw(found.folder) if found is not None else contextlib.nullcontext():
            root = envs.root(m, environ)
            root.mkdir(parents=True, exist_ok=True)
            tmp = root / (OFF + ".tmp")
            tmp.write_text(json.dumps({"by": "user", "at": time.strftime("%Y-%m-%dT%H:%M:%S")}))
            tmp.replace(root / OFF)
    except generation_leases.Busy as exc:
        raise InUse(f"{m.name} is running now: let that run finish, or press Stop on it, then Turn off. Nothing was "
                    "changed.") from exc


def turn_on_again(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> bool:
    """Reenable the retained proven selection only after checking its own requirements."""
    from . import env_observations
    with env_observations.live():
        return _turn_on_again(m, environ)


def _turn_on_again(m: catalog.Manifest, environ: Mapping[str, str]) -> bool:
    path = envs.root(m, environ) / OFF
    if not path.is_file():
        return False
    found = selected(m, environ)
    if found is None:
        return False
    from . import readiness
    if why := readiness.misfit(found.m):
        raise envs.Failed(f"{found.m.name} cannot be prepared on this computer: {why}")
    path.unlink(missing_ok=True)
    return True


def versions(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> list[dict]:
    """Every proven generation, current catalog candidate first, then retained versions: {"generation", "version", "at", "inUse", "chosen"}."""
    from . import channels, env_observations
    observed = env_observations.get(m, environ) or env_observations.observe(m, environ)
    if not channels.trusted(m, environ):
        return []
    running = None if observed.off else observed.resolved()
    from .maintenance import revocations
    # A revoked version is never offered (Use the newest, Go back): only the one in use stays listed (#402 round 2).
    return [{"generation": g.folder.name, "version": one.m.version, "at": g.receipt.get("at", ""),
             "inUse": using, "chosen": g.folder.name == observed.selection}
            for g in sorted(observed.generations, key=lambda g: g.folder != observed.candidate)
            if (one := observed.installed(g.folder.name)) is not None
            and ((using := running is not None and running.folder == g.folder)
                 or revocations.revoked(one.m, one.m.version, environ) is None)]


def in_use(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> Installed | None:
    """Resolve the exact selection; missing or invalid selections never silently fall back. Legacy installations
    without a selection retain their existing resolution until a setup mutation pins it. Reads write nothing."""
    if turned_off(m, environ):
        return None
    return selected(m, environ)


def selected(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> Installed | None:
    """The exact trusted retained selection, including while disabled; reads never enable it."""
    from . import channels
    if not channels.trusted(m, environ):
        return None
    from . import env_observations
    observed = env_observations.get(m, environ)
    return observed.resolved() if observed is not None else _resolved_generation(m, environ)


def _resolved_generation(m: catalog.Manifest, environ: Mapping[str, str]) -> Installed | None:
    """Selection independent of enablement, for preserving disabled legacy installations too."""
    picked = chosen(m, environ)
    if (envs.root(m, environ) / CHOSEN).exists():
        return generation(m, picked, environ) if picked else None
    try:
        current = envs.folder(m, environ)
    except OSError:
        current = None
    generations = envs.generations(m, environ)
    # The matching proved generation wins even when two installation receipts have the same second's timestamp.
    for d, receipt in sorted(generations, key=lambda pair: pair[0] != current):
        p = _proof_of(d)
        kept_at = d / envs.SNAPSHOT / m.id / catalog.MANIFEST
        if not (p and p.get("ok") and kept_at.is_file() and (d / "bin" / "python").exists()):
            continue
        kept, _ = catalog.read(kept_at, m.namespace, m.channel)
        if kept is not None and proof_complete(kept, d, p):
            return Installed(kept, d, p, d == current)
    return None


def generation(m: catalog.Manifest, name: str, environ: Mapping[str, str] = os.environ) -> Installed | None:
    """The generation `name` of the entry as it was installed (its snapshot's manifest), while it is still here, ready and
    proven; else None. What a recorded job ran in, to run it again as it was (registrations.again)."""
    from . import env_observations
    with env_observations.live():
        return _generation(m, name, environ)


def _generation(m, name, environ):
    from . import channels

    if not channels.trusted(m, environ):
        return None
    try:
        current = envs.folder(m, environ)
    except OSError:
        current = None
    for d, receipt in envs.generations(m, environ):
        if d.name != name:
            continue
        p = _proof_of(d)
        kept_at = d / envs.SNAPSHOT / m.id / catalog.MANIFEST
        if not (p and p.get("ok") and kept_at.is_file() and (d / "bin" / "python").exists()):
            return None
        kept, _ = catalog.read(kept_at, m.namespace, m.channel)
        return Installed(kept, d, p, d == current) if kept is not None and proof_complete(kept, d, p) else None
    return None


def update_waiting(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> str:
    """"" unless an earlier version runs while the catalog has a newer one: then what the card says."""
    found = in_use(m, environ)
    if found is None or found.current:
        return ""
    from .maintenance import policy, promote, revocations, status
    # Maintained, ever moved by maintenance, or revoked: its words only (ticket 59). The pinned catalog may then be
    # behind the entry, and must never be offered as an update; a revoked one is never offered at all.
    if (policy.get(m.key, environ)["policy"] != "manual" or promote.decisions(m.key, environ, moves=True)
            or revocations.revoked(m, m.version, environ) is not None):
        now = status.of(m, environ)
        said = now.get("says", "") if now.get("state") not in ("none", "applied", "pinned") else ""
        if not said:
            return ""
        return f"{said}. {found.m.version} stays in use" if "stays in use" not in said else said
    return (f"Update available: {m.version}. Review it before switching; {found.m.version} stays in use. "
            "Install checks the candidate beside it; Use the newest explicitly selects the proven update.")



def operational(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> catalog.Manifest:
    """Requirements of the generation that will run; the discovered manifest still describes the pending update."""
    found = selected(m, environ) if m.runner == "python-env" else None
    return found.m if found is not None else m


def ready(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> str:
    """"" when it can run here: an environment of it made and its adapter proven by function; else why not."""
    if in_use(m, environ) is not None:
        return ""
    from . import env_observations
    observed = env_observations.get(m, environ)
    invalid_selection = (observed.selection_exists and observed.installed(observed.selection) is None
                         if observed is not None else
                         (envs.root(m, environ) / CHOSEN).exists() and not generation(m, chosen(m, environ), environ))
    if invalid_selection:
        return "the selected version is unavailable; restore it or explicitly choose another proven version"
    from . import channels

    if not channels.trusted(m, environ):
        return f"code from the community channel {m.channel} isn't allowed here (it isn't reviewed here)"
    if turned_off(m, environ) and installed_any(m, environ):
        return "you turned it off: Turn on makes it a choice again with the version it had (nothing is installed again)"
    from .bundle_admission import local_workflow
    if local_workflow(m):
        return 'prepare it, try each tool on a saved Project, Save tool and reopen; then review its local workflow in Models'
    if m.members:
        return "its bundle needs all operation reference checks to pass before it is ready"
    adapter = m.adapter.partition(":")[0] + ".py"
    if not (observed.adapter if observed is not None else (catalog.entry_folder(m, environ) / adapter).is_file()):
        return f"its adapter {adapter} is missing: restore it before Install"
    if envs.python(m, environ) is None:
        return envs.state(m, environ)["says"]
    p = proof(m, environ)
    if p is None:
        return "its adapter hasn't been proven yet (Install proves it)"
    if p.get("stopped"):  # the user's Stop, not a failure of its code
        return "its install was stopped before its proof finished (you pressed Stop): Install proves it"
    return f"its adapter failed its proof: {p.get('why')}"


def installed_any(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> bool:
    """A proven generation of it is here (one Turn on can use again without installing)."""
    from . import env_observations
    observed = env_observations.get(m, environ)
    if observed is not None:
        return any(g.proven for g in observed.generations)
    return any(_ready_and_proven(m, d) for d, _ in envs.generations(m, environ))


def _ready_and_proven(m: catalog.Manifest, d: Path) -> bool:
    p = _proof_of(d)
    kept_at = d / envs.SNAPSHOT / m.id / catalog.MANIFEST
    if not kept_at.is_file() or not (d / "bin" / "python").exists():
        return False
    kept, _ = catalog.read(kept_at, m.namespace, m.channel)
    return bool(kept is not None and proof_complete(kept, d, p))


def newest_installed(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> bool:
    """The catalog's own version of the entry is installed here and proven (an Install of it would add nothing)."""
    from . import env_observations
    observed = env_observations.get(m, environ)
    if observed is not None:
        return observed.candidate_proven
    try:
        return _ready_and_proven(m, envs.folder(m, environ))
    except OSError:
        return False


def update_failed(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> str:
    """"" unless the catalog's newest version was installed here and its proof didn't pass while an earlier one runs:
    then what the card says (C7 Window B, finding 1: a failed update was said only in Activity)."""
    found = in_use(m, environ)
    if found is None or found.current:
        return ""
    try:
        d = envs.folder(m, environ)
    except OSError:
        return ""
    from . import env_observations
    observed = env_observations.get(m, environ)
    p = observed.candidate_proof if observed is not None else _proof_of(d) if d.is_dir() else None
    if not p or p.get("ok"):
        return ""
    if p.get("stopped"):
        return (f"Update {m.version} wasn't installed: you pressed Stop before its check finished. "
                f"{found.m.version} stays in use")
    return f"Update {m.version} failed its check: {p.get('why')}. {found.m.version} stays in use"


def adapter_fact(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict:
    """The same installed snapshot used by execution, without importing any contributed code."""
    installed = in_use(m, environ)
    from .link_recognise import reviewed_template

    template = reviewed_template(installed.m if installed is not None else m)
    if template:  # made from a link (C3): the app's reviewed runner on the model's own files, the model inside the sandbox
        says = (f"Runs the app's reviewed {template} runner (catalog/templates/{template}) on its makers' model, on this "
                "Mac, inside macOS's sandbox: the model is code its makers wrote"
                + ("; in use after its own test and its contract check" if installed is not None else
                   f"; {ready(m, environ)}"))
        return {"kind": "runs", "ok": installed is not None, "says": says}
    if m.members:
        says = (f"{len(m.members)} operations share this package's isolated environment. "
                + (("Local controls, Save and reopen were observed; scientific accuracy remains unverified."
                    if installed.proof.get('scope') == 'local-interactive-workflow-v1' else "Its declared reference checks passed.")
                   if installed is not None else ready(m, environ)))
        return {"kind": "runs", "ok": installed is not None, "says": says}
    if installed is not None:
        says = (f"Adapter {installed.m.adapter} ({installed.m.version}) is in use after its contract check; "
                "runs code you added, on this Mac, with your permissions, in its own process")
    else:
        says = (f"Adapter {m.adapter}: {ready(m, environ)}. Install runs code you added with your permissions "
                "and checks its contract before it becomes a choice")
    return {"kind": "runs", "ok": installed is not None, "says": says}


def files_dir(installed: Installed, environ: Mapping[str, str] = os.environ) -> Path:
    """The weights its own manifest pins: their published revision (an earlier version's stays where it was)."""
    d = model_files.published(installed.m, environ)
    if d is not None:
        return d
    if installed.current:
        return model_files.model_dir(installed.m.key, environ)  # an older cache, or the tests' stand-ins
    raise model_files.NotDownloaded(installed.m, len(installed.m.files))


class Said:
    """What a worker writes on stderr (its prints too: the worker sends them there), read to its end on a thread of its
    own so it never blocks on a full pipe, passed on to the app's own stderr (its log, as when it was inherited) and its
    last words kept, so a worker that ends says why. Before, a worker whose adapter failed to import ended with
    "didn't start (closed)" and its reason only in the app's log (C4's builder, 2026-10-14: a paid round spent guessing)."""

    KEEP = 16 * 1024  # what is kept of its words, whatever it writes (it runs code nobody reviewed)

    def __init__(self, stream, within: Path | None = None) -> None:
        self.within = within
        self._tail = bytearray()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._read, args=(stream,), name="env-worker-stderr", daemon=True)
        self._thread.start()

    def _read(self, stream) -> None:
        try:
            while True:
                chunk = os.read(stream.fileno(), 65536)
                if not chunk:
                    break
                try:
                    os.write(2, chunk)
                except OSError:
                    pass
                with self._lock:
                    self._tail += chunk
                    if len(self._tail) > self.KEEP:
                        del self._tail[:len(self._tail) - self.KEEP]
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def last_words(self, process: subprocess.Popen, wait_s: float = 2.0) -> str:
        """Why it ended, plainly: its last line (Python's error is its last line, not its frames), a library macOS
        wouldn't load said by package and reason (envs.native_words), else how it ended."""
        try:
            process.wait(timeout=wait_s)
        except subprocess.TimeoutExpired:
            pass
        self._thread.join(wait_s)
        with self._lock:
            text = self._tail.decode("utf-8", "replace")
        words = envs.last_reason(text, self.within)
        if words:
            return words if len(words) <= 600 else words[:600] + "…"
        code = process.returncode
        if code is None:
            return "it closed its output without a word"
        if code < 0:
            try:
                return f"it was ended by {signal.Signals(-code).name}, without a word"
            except ValueError:
                return f"it was ended by signal {-code}, without a word"
        return f"it exited (code {code}) without a word"


class Client:
    """One installed generation's adapter (server.py), in its own environment, started on first use and kept."""

    def __init__(self, installed: Installed, environ: Mapping[str, str] = os.environ, files: Path | None = None) -> None:
        self.installed, self.declaration, self.environ = installed, installed.m, environ
        self.owner = installed.owner if hasattr(installed, 'owner') else installed
        self.m = self.owner.m
        self.files = files  # its files' folder when not the models folder's (a draft's: drafts.py)
        self._process: subprocess.Popen | None = None
        self._private_work = None
        self._resources = None
        self._said: Said | None = None
        self._lock = threading.Lock()

    def _resource_stop(self):
        if self._resources is not None:
            self._resources.check()
        return False

    def _failure_words(self, process, fallback=''):
        try:
            self._resource_stop()
        except owned_process.Stopped as exc:
            return str(exc)
        return self._said.last_words(process) if self._said is not None else fallback

    def _start(self) -> subprocess.Popen:
        self._resource_stop()
        if self._process is not None and self._process.poll() is None:
            return self._process
        exe = self.installed.python
        if not exe.exists():
            raise Failed(f"{self.m.name}'s environment isn't installed")
        from env_worker.server import read_message  # its wire, standard library only

        env = envs.clean_env(self.m, self.installed.folder, self.environ)
        env["MICROMIND_REFUSE_NETWORK"] = "1"  # Python socket/DNS refusal; not an OS sandbox (ADR 0021)
        if self.files is not None:
            env["MICROMIND_ENTRY_FILES"] = str(self.files)
        elif self.m.files:
            try:
                env["MICROMIND_ENTRY_FILES"] = str(files_dir(self.owner, self.environ))  # its pinned weights
            except model_files.NotDownloaded as exc:
                raise Failed(str(exc)) from exc
        folder = self.installed.entry  # the entry as it was installed, never the catalog's newer copy
        cmd = [str(exe), "-I", str(SERVER), str(folder), self.declaration.adapter]
        from . import sandbox

        if sandbox.required(self.m) or self.m.members:
            if not sandbox.available():
                raise Failed("Bundle CPU execution requires macOS confinement; this platform cannot establish it"
                             if self.m.members else sandbox.refused(self.m))
            work = catalog.catalog_root(self.environ) / "sandbox" / self.m.key.replace("/", "--")
            work.mkdir(parents=True, exist_ok=True)
            if self.m.members:
                self._private_work = tempfile.TemporaryDirectory(prefix='attempt-', dir=work)
                work = Path(self._private_work.name)
            env = sandbox.confine(env, work)
            cmd = sandbox.wrap(cmd, read=[self.installed.folder, sandbox.python_home(exe), folder, *sandbox.bootstrap(),
                                          env.get("MICROMIND_ENTRY_FILES", "")], write=[work],
                               **({'gpu': False, 'children': False} if self.m.members else {}))
        self._process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         cwd=str(folder), env=env, start_new_session=True)
        if self.m.members:
            from . import worker_resources
            self._resources = worker_resources.Guard(self._process, work)
            self._resources.start()
        self._said = Said(self._process.stderr, self.installed.folder)
        with owned_process.watching(self._process, self.m.load_limit_s or 120, f"{self.m.name}'s startup"):
            try:  # bounded before it is read: its hello is a small object and nothing else (it ran code nobody reviewed)
                hello, extra = read_message(self._process.stdout, max_header=4096, max_payload=0)
            except EOFError as exc:  # it ended before it was ready: its own last words say why
                why = self._failure_words(self._process)
                self.stop()
                raise Failed(f"{self.m.name}'s adapter stopped before it was ready: {why}") from exc
            except (ValueError, OSError) as exc:
                self.stop()
                raise Failed(f"{self.m.name}'s adapter didn't start ({exc})") from exc
        if not isinstance(hello, dict) or hello.get("ok") is not True or hello.get("ready") != self.declaration.adapter or extra:
            self.stop()
            raise Failed(f"{self.m.name}'s adapter didn't start")
        return self._process

    def run(self, picture: np.ndarray, params: dict | None = None, output: str = "", *, admit=None,
            outputs: tuple = (), scientific=None) -> dict:
        """Native labels in the picture's pixels, plus polygons for drawing; polygon-only adapters stay explicit. With
        `output`, a new kind of job's declared output (kinds.py): {"picture": uint8 RGB, "shape"}, {"columns", "rows"},
        {"text"} or {"points"}, each checked again here."""
        try:
            with generation_leases.use(self.installed.folder):
                descriptors = None
                if getattr(self.declaration, 'scientific', {}):
                    from .scientific_ports import input_for
                    try:
                        declared = self.declaration.outputs[0]
                        if output != declared.type or (outputs and tuple(outputs) != ({'id': declared.id, 'type': declared.type},)):
                            raise ValueError('The requested output must match this scientific operation’s declared port.')
                        descriptors = input_for(self.declaration, picture, scientific)
                    except ValueError as exc:
                        raise Failed(f'Incompatible scientific input: {exc}') from exc
                return self._run_owned(picture, params, output, admit=admit, outputs=outputs,
                                       scientific=descriptors)
        except (generation_leases.Missing, generation_leases.Busy) as exc:
            raise Failed(str(exc)) from exc

    def run_typed(self, typed, params: dict | None = None, output: str = "", *, admit=None, outputs: tuple = ()) -> dict:
        """Typed inputs (H01 slice d, admitted by admission.bind): each port's native array crosses the pipe exactly as
        bound, never converted, and the adapter gets {port: array}."""
        try:
            with generation_leases.use(self.installed.folder):
                return self._run_owned(None, params, output, admit=admit, outputs=outputs, typed=typed)
        except (generation_leases.Missing, generation_leases.Busy) as exc:
            raise Failed(str(exc)) from exc

    def _run_owned(self, picture, params, output, *, admit, outputs, scientific=None, typed=None):
        from env_worker.server import read_message, write_message
        from .capture import Refused

        if typed is not None:
            arrays = [np.ascontiguousarray(row.array) for row in typed]
            specs = [{'id': row.port, 'type': row.type, 'shape': list(a.shape), 'dtype': a.dtype.str}
                     for row, a in zip(typed, arrays)]
            picture = arrays[0]  # replies bounded by an input's size follow the first, as the worker checks them
        else:
            picture = np.ascontiguousarray(picture, np.uint8)
        regional = None
        if getattr(self.declaration, 'bridge', '') == 'regions-v1':
            from .region_inputs import RegionInputs
            regional = RegionInputs(picture, scientific)
        try:
            with owned_process.locked(self._lock), owned_process.scope(stop=self._resource_stop):
                try:
                    cold = self._process is None or self._process.poll() is not None
                    process = self._start()
                    with owned_process.watching(process, RUN_S + (self.m.load_limit_s if cold else 0),
                                                f"{self.m.name}'s run"):
                        several = [{"id": o["id"], "type": o["type"]} for o in outputs] if len(outputs) > 1 else []
                        if typed is not None:
                            write_message(process.stdin, {"op": "run", "inputs": specs, "params": params or {},
                                                          **({"output": output} if output else {}),
                                                          **({"outputs": several} if several else {})},
                                          b''.join(memoryview(a).cast('B') for a in arrays))
                        else:
                            write_message(process.stdin, {"op": "run", "shape": list(picture.shape), "params": params or {},
                                                          **({'scientific': scientific} if scientific is not None else {}),
                                                          **({'regionInputs': regional.wire()} if regional else {}),
                                                          **({"output": output} if output else {}),
                                                          **({"outputs": several} if several else {})}, b'' if regional else memoryview(picture))
                        # Bounded before it is read: a label map is the picture's size; a new kind's reply its limit.
                        limit = (sum(reply_limit(o["type"], picture.shape) for o in several) if several
                                 else reply_limit(output, picture.shape))
                        def admit_frame(header, size):
                            if regional:
                                regional.check_frame(header, size)
                            if admit and not (regional and regional.is_request(header)):
                                admit(header, size)

                        while True:
                            reply, data = read_message(process.stdout, max_header=(1 << 20) if output else 64 << 20,
                                                       max_payload=limit, admit=admit_frame)
                            if not regional or not regional.is_request(reply):
                                break
                            region_header, region_bytes = regional.answer(reply)
                            write_message(process.stdin, region_header, region_bytes)
                            del region_bytes
                except Failed:  # its start's own words, already said with its name
                    self.stop()
                    raise
                except EOFError as exc:  # it ended while it ran: its own last words say why
                    why = self._failure_words(process, str(exc))
                    self.stop()
                    raise Failed(f"{self.m.name}'s adapter stopped while it ran: {why}") from exc
                except (owned_process.Stopped, Refused, OSError, ValueError) as exc:
                    self.stop()
                    raise Failed(f"{self.m.name}'s adapter: {exc}") from exc
        except owned_process.Stopped as exc:
            raise Failed(str(exc)) from exc
        if not reply.get("ok"):
            raise Failed(f"{self.m.name}: {envs.native_words(str(reply.get('error')), self.installed.folder)}")
        if reply.get("kind") == "unsupported":
            raise Unsupported(str(reply.get("why") or "it does not take this input")[:300], next=None)  # no next: the adapter's own words say what it takes
        if len(outputs) > 1:
            return {"outputs": self._parts(reply, data, picture, outputs)}
        if output and output != "labels.instance.2d":
            result = kind_output(output, reply, data, picture.shape)
            if scientific is not None:
                from .scientific_ports import output_for
                try:
                    result = output_for(self.declaration, result, reply.get('scientific'), scientific)
                except ValueError as exc:
                    raise Failed(f'Invalid scientific output: {exc}') from exc
            return result
        return self._labels(reply, data, picture)

    def _parts(self, reply: dict, data: bytes, picture: np.ndarray, outputs: tuple) -> dict:
        """Several outputs: each part checked as its declared type, on this side too."""
        parts, at = reply.get("parts"), 0
        if reply.get("kind") != "multi" or not isinstance(parts, list) or [x.get("id") for x in parts if isinstance(x, dict)] \
                != [o["id"] for o in outputs]:
            raise Failed(f"it gave {reply.get('kind')!r}, not its {len(outputs)} declared outputs")
        out = {}
        for o, part in zip(outputs, parts):
            n = part.get("bytes")
            if not isinstance(n, int) or n < 0 or at + n > len(data) or not isinstance(part.get("reply"), dict) \
                    or not isinstance(part["reply"].get("kind"), str):
                raise Failed("its outputs' bytes don't add up")
            chunk = data[at:at + n]
            at += n
            got = (self._labels(part["reply"], chunk, picture) if o["type"] in ("labels.instance.2d", "masks.2d")
                   else kind_output(o["type"], part["reply"], chunk, picture.shape))
            out[o["id"]] = {**got, "output": o["type"]}
        if at != len(data):
            raise Failed("its outputs' bytes don't add up")
        return out

    def _labels(self, reply: dict, data: bytes, picture: np.ndarray) -> dict:
        if reply["kind"] == "polygons":
            return {"polygons": reply["polygons"]}
        from .evidence import encode_labels

        if reply.get("kind") != "labels" or reply.get("shape") != list(picture.shape[:2]):
            raise Failed("its label map does not match the input picture")
        if len(data) != picture.shape[0] * picture.shape[1] * 4:
            raise Failed("its label map has incomplete pixel data")
        labels = np.frombuffer(data, np.int32).reshape(reply["shape"])
        if labels.min(initial=0) < 0:
            raise Failed("its labels must be nonnegative instance numbers")
        return {"labels": encode_labels(labels), "polygons": polygons_of(labels),
                "count": int(len(np.unique(labels[labels > 0]))), "sha256": hashlib.sha256(data).hexdigest()}

    def stop(self) -> None:
        if self._resources is not None:
            self._resources.close()
            self._resources = None
        elif self._process is not None:
            owned_process.terminate(self._process)
        self._process, self._said = None, None
        if self._private_work is not None:
            self._private_work.cleanup()
            self._private_work = None


def reply_limit(output: str, shape: tuple) -> int:
    """The most bytes a reply may hold (kinds.py's limits): checked before any of it is read."""
    from . import kinds

    if output == "image.2d.rgb8":
        return kinds.MAX_SIDE * kinds.MAX_SIDE * 3
    if output in ("table.objects", "points.2d", "tracks.graph"):
        return 32 << 20
    if output == "text":
        return kinds.MAX_TEXT * 4
    if output and output not in ("labels.instance.2d", "masks.2d"):
        return kinds.MAX_DATA  # any other type: its value as JSON (catalog format 2's open types)
    return int(shape[0]) * int(shape[1]) * 4  # an int32 label map of the picture's size (polygons ride in the header)


def kind_output(output: str, reply: dict, data: bytes, shape: tuple) -> dict:
    """A new kind of job's reply (kinds.py), checked on this side too: the worker's process ran code nobody reviewed."""
    import json

    from . import kinds

    want = {"image.2d.rgb8": "picture", "table.objects": "table", "text": "text", "points.2d": "points",
            "tracks.graph": "tracks"}.get(output, kinds.DATA_OUTPUT)
    if reply.get("kind") != want:
        raise Failed(f"it gave {reply.get('kind')!r}, not the {kinds.gives(output)} it declares")
    if want == kinds.DATA_OUTPUT:  # a type the app has no place for: its value, kept and handed on as data
        if len(data) > kinds.MAX_DATA:
            raise Failed("its data is too large")
        try:
            return {"data": json.loads(data)}
        except (ValueError, RecursionError) as exc:
            raise Failed("its data can't be read") from exc
    if want == "picture":
        h, w, c = (int(v) for v in reply.get("shape") or (0, 0, 0))
        if c != 3 or not (0 < h <= kinds.MAX_SIDE and 0 < w <= kinds.MAX_SIDE) or len(data) != h * w * 3:
            raise Failed("its picture is not the 8-bit RGB picture it says")
        return {"picture": np.frombuffer(data, np.uint8).reshape(h, w, 3), "shape": [h, w, 3]}
    if want == "text":
        text = data.decode("utf-8", "replace")
        if len(text) > kinds.MAX_TEXT:
            raise Failed("its words are too long")
        return {"text": text}
    try:
        got = json.loads(data)
    except ValueError as exc:
        raise Failed("its output can't be read") from exc
    if want == "tracks":  # frames (n, h, w): each track's frames rising within them, its points on them
        n, h, w = (shape[0], shape[1], shape[2]) if len(shape) == 4 else (1, shape[0], shape[1])
        # The parent's own contract, the same as the worker's (Codex's diagnosis of 621d0721): a frame is an integer
        # index (never a fraction or a bool), a place two finite numbers on that frame; anything else is refused here.
        ok = isinstance(got, list) and len(got) <= kinds.MAX_TRACKS and all(
            isinstance(t, dict) and isinstance(t.get("points"), list) and 0 < len(t["points"]) <= n
            and all(isinstance(p, list) and len(p) == 3 and _index(p[0]) and _finite(p[1]) and _finite(p[2])
                    and 0 <= p[0] < n and 0 <= p[1] <= w and 0 <= p[2] <= h for p in t["points"])
            and all(a[0] < b[0] for a, b in zip(t["points"], t["points"][1:])) for t in got)
        if not ok:
            raise Failed("its tracks are not tracks over the frames it was given")
        return {"tracks": [{"points": [[int(f), float(x), float(y)] for f, x, y in t["points"]]} for t in got]}
    if want == "table":
        if not isinstance(got, dict):
            raise Failed("its table is not the table it says")
        columns, rows = got.get("columns"), got.get("rows")
        if (not isinstance(columns, list) or not isinstance(rows, list) or not 0 < len(columns) <= kinds.MAX_COLUMNS
                or len(rows) > kinds.MAX_ROWS
                or any(not isinstance(c, dict) or not isinstance(c.get("name"), str) or not 0 < len(c["name"]) <= 80
                       or set(c) - {"name", "unit", "definition"}
                       or any(not isinstance(c[k], str) or len(c[k]) > 200 for k in ("unit", "definition") if k in c)
                       for c in columns)
                or any(not isinstance(r, list) or len(r) != len(columns)
                       or any(not (v is None or isinstance(v, bool) or _finite(v)
                                   or isinstance(v, str) and len(v) <= 500) for v in r) for r in rows)):
            raise Failed("its table is not the table it says")
        return {"columns": columns, "rows": rows}
    h, w = shape[:2]
    if (not isinstance(got, list) or len(got) > kinds.MAX_POINTS
            or any(not isinstance(p, list) or len(p) != 2 or not (_finite(p[0]) and _finite(p[1]))
                   or not (0 <= p[0] <= w and 0 <= p[1] <= h) for p in got)):
        raise Failed("its points are not points on the picture")
    return {"points": [[float(x), float(y)] for x, y in got]}


def _finite(v) -> bool:
    import math

    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _index(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def polygons_of(labels: np.ndarray, min_area: float = 4) -> list:
    """One polygon per label (its largest piece), in the label map's px."""
    import cv2

    out = []
    for value in np.unique(labels[labels > 0]):
        mask = (labels == value).astype(np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = [c for c in contours if cv2.contourArea(c) >= min_area]
        if contours:
            out.append(max(contours, key=cv2.contourArea)[:, 0, :].astype(float).tolist())
    return out


_clients: dict[str, Client] = {}
_retiring: set[Client] = set()
_clients_lock = threading.RLock()


def stop() -> None:
    """Stop every retained adapter, including generations finishing admitted work."""
    with _clients_lock:
        workers = set(_clients.values()) | _retiring
        _clients.clear()
        _retiring.clear()
    for worker in workers:
        worker.stop()


def _retire(worker: Client, retire=None) -> None:
    # Keep ownership visible to shutdown until the last admitted call has released this generation.
    _retiring.add(worker)
    try:
        callback_loop = asyncio.get_running_loop() if retire is not None else None
    except RuntimeError:
        callback_loop = None
    def reap():
        try:
            if retire is not None and (callback_loop is None or not callback_loop.is_closed()):
                retire(worker)
            else:
                worker.stop()
        finally:
            with _clients_lock:
                _retiring.discard(worker)
    def finish():
        if callback_loop is not None and not callback_loop.is_closed():
            # App retirement creates an owned task on this loop; keep our reference until that handoff runs.
            callback_loop.call_soon_threadsafe(reap, context=contextvars.Context())
        else:
            reap()
    generation_leases.when_idle(worker.installed.folder, finish)


def client(installed: Installed) -> Client:
    with _clients_lock:
        found = _clients.get(installed.m.key)
        if found is None or found.installed.folder != installed.folder:
            if found is not None:
                _retire(found)
            found = _clients[installed.m.key] = Client(installed)
        return found


def representative(environ: Mapping[str, str] = os.environ) -> tuple[np.ndarray, dict]:
    """Proof input and observed origin; an available asset is not a claim of scientific qualification."""
    import cv2
    from .assets import Store
    from .model_checks import FRAME

    try:
        data = Store.from_env(environ).bench(FRAME).read_bytes()
        # Keep encoded orientation and pixel grid, as for an ordinary imported source.
        picture = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
        if picture is not None:
            return cv2.cvtColor(picture, cv2.COLOR_BGR2RGB), {
                "evidenceClass": "asset-image", "identity": f"bench/{FRAME}",
                "sourceSha256": hashlib.sha256(data).hexdigest()}
    except (OSError, cv2.error):
        pass
    picture = np.full((128, 128, 3), 220, np.uint8)
    for (x, y, r) in ((30, 30, 12), (80, 40, 9), (60, 90, 14)):
        cv2.circle(picture, (x, y), r, (40, 40, 40), -1)
    return picture, {"evidenceClass": "generated-fixture", "identity": "three-discs-rgb8-v1"}


def proof_picture(m: catalog.Manifest, generation: Path, environ: Mapping[str, str]) -> tuple[np.ndarray, dict]:
    """Prefer an author's successful example at defaults; parameter overrides never stand in for default proof.
    Read the immutable installed snapshot, not the mutable source folder. Legacy entries keep their original probe.
    """
    example = next((t for t in m.tests if t.get("outcome", "result") == "result" and not t.get("params")), None)
    if example is None:
        return representative(environ)
    import cv2

    path = generation / envs.SNAPSHOT / m.id / example["input"]
    data = path.read_bytes()
    picture = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    if picture is None:
        raise Failed(f"its default example ({example['input']}) can't be read")
    return np.ascontiguousarray(cv2.cvtColor(picture, cv2.COLOR_BGR2RGB)), {
        "evidenceClass": "declared-example", "identity": example["input"],
        "sourceSha256": hashlib.sha256(data).hexdigest()}


def proof_input_says(p: dict) -> str:
    """Truthful input wording for old receipts too; absent provenance is never inferred."""
    source = p.get("input") or {}
    kind = {"generated-fixture": "generated fixture", "asset-image": "asset image", "declared-example": "declared example"}.get(
        source.get("evidenceClass"), "input of unrecorded origin")
    return f"{kind} {source['identity']}" if source.get("identity") else kind


def prove(m: catalog.Manifest, environ: Mapping[str, str] = os.environ, stop=lambda: False) -> dict:
    """Run and retain the exact RGB8 input once, checking the adapter contract, not scientific accuracy."""
    from . import jobs

    preserve_active(m, environ)
    if m.members:
        from .bundle_runtime import prove as prove_bundle
        return prove_bundle(m, environ, stop)
    if jobs.is_job(m):
        return jobs.prove(m, environ, stop=stop)  # ended with its Install, and past its limit
    t0 = time.monotonic()
    d = envs.folder(m, environ)
    one = Client(Installed(m, d, {}, True), environ)
    picture, source = proof_picture(m, d, environ)
    path = proof_path(m, environ)
    # A later check can use another image without destroying the input behind earlier evidence.
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=d.parent, prefix=d.name + ".proof-input-", delete=False) as stream:
            tmp = Path(stream.name)
            np.save(stream, picture, allow_pickle=False)
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        kept = d.with_name(f"{d.name}.{digest}.proof-input.npy")
        tmp.chmod(0o400)
        try:
            os.link(tmp, kept)  # create-only publication; a concurrent identical check reuses the same bytes
        except FileExistsError:
            if kept.is_symlink() or hashlib.sha256(kept.read_bytes()).hexdigest() != digest:
                raise Failed("its retained proof input changed; the previous evidence was not overwritten")
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    source.update({"shape": list(picture.shape), "dtype": "uint8", "color": "RGB", "file": kept.name,
                   "pixelSha256": hashlib.sha256(picture.tobytes()).hexdigest(),
                   "fileSha256": hashlib.sha256(kept.read_bytes()).hexdigest()})
    values: dict = {}
    try:
        if not (d / envs.SNAPSHOT / m.id).is_dir():
            raise Failed("its environment keeps no copy of the entry: install it again")
        from . import kinds

        cant = kinds.takes_why(m)
        if cant:  # nothing to prove it on until the app can give it its input
            raise Failed(f"the app can't run it yet: {cant}")

        kind = kinds.implements(m)  # a new kind of job: its declared output, checked (kinds.py)
        # A capability (catalog format 2) is proven with its parameters' defaults and every output it declares.
        values, _problems = catalog.params_given(m, {})
        declared = tuple({"id": o.id, "type": o.type} for o in m.outputs)
        with owned_process.scope(seconds=envs.STEP_S, stop=stop):
            if kind is not None and kind.input == "clip.2d.rgb8":  # its own clip: the picture moved 2 px a frame
                clip = np.stack([np.roll(picture, 2 * i, axis=1) for i in range(6)])
                out = one.run(clip, {**values, "times": [round(0.5 * i, 2) for i in range(6)]}, output=kind.output)
            else:
                out = one.run(picture, values, output=kind.output, outputs=declared) if kind is not None \
                    else one.run(picture, values)
        h, w = picture.shape[:2]
        if kind is not None:
            made = kinds.made_words(kind.output, out)
            out = {"polygons": [], "count": made}  # its proof says what it made, in the found's place
        outside = [p for p in out["polygons"] if any(not (0 <= x <= w and 0 <= y <= h) for x, y in p)]
        if outside:
            raise Failed(f"{len(outside)} of what it found lie outside the picture")
        result = {"ok": True, "found": out.get("count", len(out["polygons"])), "picture": f"{w}x{h}",
                  "seconds": round(time.monotonic() - t0, 1)}
        if kind is not None:
            result["says"] = f"made {out['count']} from {proof_input_says({'input': source})} ({w}x{h})"
        if m.tests:  # its declared examples too, each giving the outcome (and count) it declares (ported from #316)
            result["tests"] = declared_examples(m, one)
            result["scope"] = "adapter-contract-smoke and declared examples"
    except Unsupported as exc:  # a refusal proves nothing of what it gives
        result = {"ok": False, "why": f"it refused the proof's picture ({exc}): give it one it takes as its example"}
    except (Failed, owned_process.Stopped) as exc:
        result = {"ok": False, "why": str(exc)}
        if stop():  # the user's Stop ended it: said as stopped, never as a failure of its code
            result["stopped"] = True
    finally:
        one.stop()
    result.update({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "input": source,
                   "scope": result.get("scope", "adapter-contract-smoke"), "parameters": values, "generation": d.name,
                   "entryDigest": envs.entry_digest(m, environ)})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result))
    tmp.replace(path)
    return result


def run_once(installed: Installed, environ: Mapping[str, str] = os.environ) -> dict:
    """Run a generation's proof input once in a worker of its own, as its proof did, and stop that worker: the first
    check after a promotion (orchestrator/maintenance/promote.py). Raises what the run raises."""
    from . import kinds

    if installed.m.members:
        from . import bundle_runtime, reader_proof
        if any(member.bridge == 'scientific-host-v1' for member in bundle_runtime.declarations(installed.m)):
            if not bundle_runtime.proof_complete(installed.m, installed.folder, installed.proof):
                raise Failed('The reader needs its exact parent-issued proof before the first check')
            return {'readerChecks': reader_proof.check(installed, environ), 'fullQualification': False}

    one = Client(installed, environ)
    try:
        picture, _source = proof_picture(installed.m, installed.folder, environ)
        values, _problems = catalog.params_given(installed.m, {})
        kind = kinds.implements(installed.m)
        declared = tuple({"id": o.id, "type": o.type} for o in installed.m.outputs)
        if kind is not None and kind.input == "clip.2d.rgb8":
            clip = np.stack([np.roll(picture, 2 * i, axis=1) for i in range(6)])
            return one.run(clip, {**values, "times": [round(0.5 * i, 2) for i in range(6)]}, output=kind.output)
        return one.run(picture, values, output=kind.output, outputs=declared) if kind is not None \
            else one.run(picture, values)
    finally:
        one.stop()


def declared_examples(m: catalog.Manifest, one: "Client", *, picture_loader=None, descriptor_loader=None) -> list[dict]:
    """Each of its `tests` run through its adapter: its picture, its parameters, the outcome it declares and, when it
    says, how many it gives. Raises Failed naming the first that doesn't."""
    import cv2

    from . import kinds

    out = []
    declared = tuple({"id": o.id, "type": o.type} for o in m.outputs)
    for n, t in enumerate(m.tests, 1):
        if picture_loader is not None:
            picture = picture_loader(t['input'])
        else:
            bgr = cv2.imread(str(one.installed.folder / envs.SNAPSHOT / m.id / t["input"]),
                             cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
            if bgr is None:
                raise Failed(f"its test {n}'s picture ({t['input']}) can't be read")
            picture = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        values, problems = catalog.params_given(m, t.get("params") or {})
        if problems:
            raise Failed(f"its test {n}: " + "; ".join(problems))
        i = kinds.implements(m)
        try:
            extra = {'scientific': descriptor_loader(t['input'])} if descriptor_loader else {}
            got = (one.run(picture, values, output=i.output, outputs=declared, **extra) if i is not None
                   else one.run(picture, values))
            count = got.get("count") if isinstance(got.get("count"), int) else len(
                got.get("rows") or got.get("points") or got.get("polygons") or got.get("tracks") or [])
            outcome = "result" if count or got.get("picture") is not None or got.get("text") or "data" in got else "empty"
        except Unsupported:
            outcome, count = "unsupported", 0
        want = t.get("outcome", "result")
        if outcome != want or ("count" in t and count != t["count"]):
            raise Failed(f"its test {n} ({t['input']}) gave {outcome}" + (f" with {count}" if "count" in t else "")
                         + f", and it declares {want}" + (f" with {t['count']}" if "count" in t else ""))
        out.append({"input": t["input"], "outcome": outcome, **({"count": count} if "count" in t else {})})
    return out


def register(settings=None, environ: Mapping[str, str] = os.environ, *, retire: Callable[[Client], None] | None = None) -> dict:
    """Each proven entry a choice of the operation it implements, and each new kind of job an operation of its own; one
    no longer proven taken away (a setting that chose it goes back to the default): the door's registration
    (kinds.register), the same for every runner. Python-env's own part is its workers: one whose generation is no
    longer in use is retired. Returns {"added", "removed", "reset"}. The live App owns off-loop retirement; synchronous
    callers stop withdrawn clients here."""
    from . import jobs, kinds, own_trials

    owners = {m.key: found for m in entries(environ)
              if not jobs.is_job(m) and (found := in_use(m, environ)) is not None and not own_trials.pending(found.m, environ)}
    wanted = {member.m.key: member for found in owners.values() for member in expanded(found)}
    with _clients_lock:
        for key in [k for k, c in _clients.items() if k not in wanted or c.installed.folder != wanted[k].folder]:
            withdrawn = _clients.pop(key)
            previous = withdrawn.installed.owner if hasattr(withdrawn.installed, 'owner') else withdrawn.installed
            successor = owners.get(previous.m.key)
            if key in wanted or (successor is not None and successor.folder != previous.folder):
                _retire(withdrawn, retire)
            elif retire is not None:  # explicit removal, disabling or trust withdrawal still stops it
                retire(withdrawn)
            else:
                withdrawn.stop()
    return kinds.register(settings, environ)


def hidden_values(m: catalog.Manifest, hub, capture=None) -> dict:
    """What the app gives a capability unasked (catalog format 2's `hidden`, manifests.HIDDEN), only what it asks for
    and only what the app knows: the scale and the objective."""
    out = {}
    scale = getattr(hub, "scale", None)
    if "objective" in m.hidden and getattr(scale, "objective", None) is not None:
        out["objective"] = scale.objective
    if "pixel_size_um" in m.hidden and hasattr(hub, "scale_reading"):
        try:
            reading = hub.scale_reading()
        except Exception:  # noqa: BLE001 - a scale that can't be read is absent, never guessed
            reading = None
        if reading is not None and getattr(reading, "um_per_px", None):
            out["pixel_size_um"] = float(reading.um_per_px)
    return out


async def _run(run, capture, prompt, impl, params: dict) -> dict:
    """The operations' adapter for every python-env entry: the capture's picture to its own adapter, in a thread."""
    import cv2

    from .operations import Unavailable
    from .source_images import worker_input

    # By its id among every entry with its own adapter: the catalog's, yours (mine/<id>) or a channel's (<channel>/<id>).
    member_id = None
    if '#' in impl.id:
        from gpu_worker.bundle_contract import parse_capability_key
        owner_key, member_id = parse_capability_key(impl.id)
        m = next((x for x in entries() if x.key == owner_key), None)
    else:
        m = next((x for x in entries() if x.id == impl.id and not x.members), None)
    from . import env_observations
    with env_observations.live():
        installed = in_use(m) if m is not None else None
        if installed is not None and member_id is not None:
            installed = next((one for one in expanded(installed) if one.m.key == impl.id), None)
    if installed is None:
        with env_observations.live():
            why = ready(m) if m is not None else "it is no longer in the catalog"
        raise Unavailable(f"{impl.label} can't run here ({why})", next="its card on the Models page")
    from .capability_identity import check_generation, check_environment
    identity = check_generation(installed.owner.m if hasattr(installed, "owner") else installed.m, params)
    identity.update(check_environment(installed, params))
    from .maintenance import revocations
    if revoked := revocations.blocked(installed):  # its maintainers revoked this version with severity block (MT3)
        raise Unavailable(revoked, next="update it, or choose another version on its card")
    if params.get('crop') and getattr(installed.m, 'scientific', {}):
        raise Unavailable('Scientific capabilities need an explicit cropped resource; crop coordinates and calibration cannot be silently changed.',
                          next='choose the whole input or a saved crop with its own coordinates and calibration')
    from . import kinds
    from .capture import MB, Refused, captures

    clip_kind = kinds.implements(installed.m)
    clip_used, lease = None, None
    # The view's picture (or frames) is admitted in the analysis allowance for every caller, as the app's own analyses
    # are: a camera frame's decode, colour and encode peak (~33 MB at 1280x720) is past the researchers' own 24 MB, which
    # holds their small reads. A researcher's run is still one at a time and below the user's work on the GPU.
    allowance = "analysis"
    typed = getattr(capture, 'typed', None)  # admission.bind: the exact native inputs bound at Run, one per port
    if typed is not None:
        from . import admission
        try:
            admission.check_declaration(installed.m, typed)
            need = 3 * sum(row.array.nbytes for row in typed) + 16 * MB
            lease = captures(run.hub).memory.reserve(allowance, need, f"{impl.label}'s inputs")
        except admission.Refused as exc:
            raise Unavailable(f"{impl.label} wasn't given its inputs: {exc}", next=exc.next) from exc
        except Refused as exc:
            raise Unavailable(f"{impl.label} wasn't given its inputs: {exc}", next=exc.next) from exc
    takes_clip = clip_kind is not None and clip_kind.input == "clip.2d.rgb8" and typed is None
    gives_picture = clip_kind is not None and (clip_kind.output == "image.2d.rgb8" or any(
        o.type == "image.2d.rgb8" for o in installed.m.outputs))
    if (gives_picture or getattr(installed.m, 'bridge', '') == 'regions-v1') and not takes_clip and typed is None:
        # Input decode/colour/crop and EXIF-neutral encoding, including their transient copies. The reply has its
        # own dimensions: admit that peak from its bounded header before the pipe allocates any pixel bytes.
        need = capture.width * capture.height * 18 + 2 * len(capture.analysis_bytes) + 16 * MB
        try:
            lease = captures(run.hub).memory.reserve(allowance, need, f"{impl.label}'s input picture")
        except Refused as exc:
            raise Unavailable(f"{impl.label} wasn't given its picture: {exc}", next=exc.next) from exc
    if takes_clip:
        # Admitted before anything is decoded (Codex's diagnosis of 621d0721): the frames' one stack, a decode at a time,
        # and the reply's limit, in the analysis allowance, held until the owned worker has finished (off_loop waits).
        from .capture import TEMPORARY_SHARE, captures

        clip = list(getattr(prompt, "clip", None) or ())
        if len(clip) < 2:
            raise Unavailable(f"{impl.label} needs a few seconds of the view: there were no frames to give it", next=None)  # no next: the message identifies the missing or changed clip input
        picks = sorted({round(i * (len(clip) - 1) / max(1, min(kinds.MAX_CLIP_FRAMES, len(clip)) - 1))
                        for i in range(min(kinds.MAX_CLIP_FRAMES, len(clip)))})
        clip_used = [clip[i] for i in picks]
        sizes = {(int(info.width), int(info.height)) for _frame, info in clip_used}
        if len(sizes) != 1:
            raise Unavailable(f"{impl.label}: its frames are not all one size (the view changed while they were taken)",
                              next=None)  # no next: the message identifies the missing or changed clip input
        (w, h), = sizes
        need = int((len(clip_used) * w * h * 3 + w * h * 3 + reply_limit(clip_kind.output, ())) * (1 + TEMPORARY_SHARE))
        from .capture import Refused

        try:
            lease = captures(run.hub).memory.reserve(allowance, need, f"{impl.label}'s frames")
        except Refused as exc:  # nothing decoded, nothing sent: said as the run's refusal
            raise Unavailable(f"{impl.label} wasn't given its frames: {exc}", next=getattr(exc, "next", None)) from exc  # next: passed on from Refused

    def clip_go(kind, producer: dict) -> dict:
        """A tracks kind: at most kinds.MAX_CLIP_FRAMES of the clip, evenly spread, each decoded once straight into one
        stack (all one size, as admitted), and their times from the first; its tracks checked and hashed as this side
        read them. The stack crosses the pipe as it is, never copied."""
        used = clip_used
        w, h = int(used[0][1].width), int(used[0][1].height)
        stack = np.empty((len(used), h, w, 3), np.uint8)
        for i, (frame, _info) in enumerate(used):
            bgr = cv2.imdecode(np.frombuffer(frame.jpeg, np.uint8), cv2.IMREAD_COLOR)
            if bgr is None or bgr.shape != (h, w, 3):
                raise Failed("its frames are not all one size: the view changed while they were taken")
            cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB, dst=stack[i])
            del bgr
        t0 = float(used[0][1].wall_at)
        times = [round(float(info.wall_at) - t0, 4) for _frame, info in used]
        out = worker.run(stack, {**values, "times": times}, output=kind.output)
        del stack
        body = json.dumps({"tracks": out["tracks"]}, sort_keys=True).encode()
        return {"tracks": out["tracks"], "times": times, "frames": [int(info.id) for _f, info in used],
                "output": kind.output, "version": installed.m.version,
                "producer": {**producer, "output": kind.output,
                             "sha256": hashlib.sha256(body).hexdigest()}}

    def typed_go(producer: dict) -> dict:
        """Typed inputs: each port's array as bound, the adapter's declared output checked as any kind's is, and the
        exact inputs (references and content digests) in what made it."""
        kind = kinds.implements(installed.m)
        out = worker.run_typed(typed, values, output=kind.output, outputs=declared)
        if "outputs" in out:
            raise Failed("several outputs from typed inputs are not kept yet; declare one output")
        if "picture" in out or "points" in out:  # both are kept on a view; a typed run has none (admission.why_not)
            raise Failed("a picture or points from typed inputs are not kept yet; declare a table, text or data output")
        body = json.dumps({k: out[k] for k in ("columns", "rows", "text", "data") if k in out},
                          sort_keys=True).encode()
        inputs = [row.record() for row in typed]
        return {**out, "output": kind.output, "version": installed.m.version, "params": values, "inputs": inputs,
                "producer": {**producer, "output": kind.output, "params": values, "inputs": inputs,
                             "sha256": hashlib.sha256(body).hexdigest()}}

    # Its parameters' values (checked by the caller against its declaration: kinds.values_for) and what it asked the app
    # to give unasked (manifests.HIDDEN): the adapter's `params`, and part of the job's identity (Runner.run's key).
    # Every caller gets its defaults: the door's callers send checked values with the hidden ones they read when the job
    # was keyed (kinds.inputs_for); another (`use` of a capability that is a cell finder) sends none.
    given = json.loads(params["inputs"]) if params.get("inputs") else None
    values, problems = catalog.params_given(installed.m, {k: v for k, v in (given or {}).items()
                                                          if k not in catalog.HIDDEN})
    from .bundle_runtime import checked_parameters
    if not problems and not checked_parameters(installed, values):
        problems = ["this configuration has no passing reference check; add and check a reference case before running it"]
    if problems:
        if lease is not None:
            lease.release()
        raise Unavailable(f"{impl.label}: " + "; ".join(problems), next=None)  # no next: the words say which value
    values.update({k: v for k, v in given.items() if k in catalog.HIDDEN} if given is not None
                  else hidden_values(installed.m, getattr(run, "hub", None)))
    declared = tuple({"id": o.id, "type": o.type} for o in installed.m.outputs)

    def go() -> dict:
        from . import made_with

        # A run through the operations runner has its hub's memory to admit the entry it carries; one driven without a
        # runner (a check, a direct call) carries none, and its catalog identity says why (made_with.capture).
        hub = getattr(run, "hub", None)
        producer = {**identity, "implementation": installed.m.id, "version": installed.m.version,
                    "generation": installed.folder.name,
                    "catalog": made_with.capture(installed.owner.m if hasattr(installed, "owner") else installed.m, generation=installed.folder.name,
                                                 memory=captures(hub).memory if hub is not None else None)}
        if clip_used is not None:
            return clip_go(clip_kind, producer)
        if typed is not None:
            return typed_go(producer)
        picture = cv2.cvtColor(cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        x0 = y0 = 0
        if params.get("crop"):
            x0, y0, width, height = (int(v) for v in params["crop"])
            if (min(x0, y0) < 0 or min(width, height) <= 0
                    or x0 + width > picture.shape[1] or y0 + height > picture.shape[0]):
                raise Failed("its crop must be a nonempty rectangle inside the input picture")
            picture = picture[y0:y0 + height, x0:x0 + width]
        from . import kinds

        kind = kinds.implements(installed.m)
        if kind is not None and (kind.output != "labels.instance.2d" or len(declared) > 1):  # a new kind's declared output(s)
            def admit_picture(header, size):
                # A fragmented pipe read can temporarily hold two payload copies. Encoding holds RGB, BGR, PNG
                # and its bytes; five payloads plus codec/header room covers their peak without guessing a ratio.
                lease.grow(5 * size + 16 * MB, f"{impl.label}'s output picture")

            pictures = any(o["type"] == "image.2d.rgb8" for o in declared) if len(declared) > 1 else \
                kind.output == "image.2d.rgb8"
            out = worker.run(picture, values, output=kind.output, outputs=declared,
                                        **({'scientific': (getattr(capture, 'source_image', None) or {}).get('scientific')}
                                           if getattr(installed.m, 'scientific', {}) else {}),
                                        **({"admit": admit_picture} if pictures else {}))
            if "outputs" in out:  # several outputs: each kept as its type says, with what made all of them
                parts = {}
                for oid, part in out["outputs"].items():
                    part = dict(part)
                    if "points" in part:
                        part["points"] = [[x + x0, y + y0] for x, y in part["points"]]
                    if "polygons" in part:
                        part["polygons"] = [[[x + x0, y + y0] for x, y in poly] for poly in part["polygons"]]
                    if "picture" in part:
                        ok, png = cv2.imencode(".png", cv2.cvtColor(part["picture"], cv2.COLOR_RGB2BGR))
                        if not ok:
                            raise Failed("its picture could not be encoded")
                        part["picture"] = png.tobytes()
                    parts[oid] = part
                body = json.dumps({k: {x: v for x, v in p.items() if x in ("columns", "rows", "text", "points", "data",
                                                                           "sha256", "count")}
                                   for k, p in parts.items()}, sort_keys=True, default=str).encode()
                result = {"outputs": parts, "output": kind.output, "version": installed.m.version, "params": values,
                          "producer": {**producer, "output": kind.output, "params": values,
                                       "sha256": hashlib.sha256(body).hexdigest()}}
                kept = sum(len(p["picture"]) for p in parts.values() if p.get("picture"))
                return AdmittedOutput(result, lease.split(kept, f"{impl.label}'s kept pictures")) \
                    if kept and lease is not None else result
            if "points" in out:
                out["points"] = [[x + x0, y + y0] for x, y in out["points"]]
            # What it made, by its bytes as this side read them (checked by kind_output): a kept result names it.
            body = memoryview(out["picture"]) if "picture" in out else json.dumps(
                {k: out[k] for k in ("columns", "rows", "text", "points", "data") if k in out}, sort_keys=True).encode()
            digest = hashlib.sha256(body).hexdigest()
            del body
            if "picture" in out:  # through the runner as lossless PNG bytes (its sha256 is of the pixels it gave)
                ok, png = cv2.imencode(".png", cv2.cvtColor(out["picture"], cv2.COLOR_RGB2BGR))
                if not ok:
                    raise Failed("its picture could not be encoded")
                out = {**out, "picture": png.tobytes()}
            result = {**out, "output": kind.output, "version": installed.m.version, "params": values,
                      "producer": {**producer, "output": kind.output, "params": values, "sha256": digest}}
            return AdmittedOutput(result, lease.split(len(out["picture"]), f"{impl.label}'s kept picture")) \
                if "picture" in out else result
        out = worker.run(picture, values)
        if "labels" in out:
            out["labels"] = {**out["labels"], "origin": [x0, y0]}
        made = {**out, "polygons": [[[x + x0, y + y0] for x, y in p] for p in out["polygons"]],
                "version": installed.m.version, "producer": producer}
        if values:
            made.update(params=values, producer={**made["producer"], "params": values})
        if kind is not None:  # a new kind's outlines: what made them, by the label map's own sha256
            made.update(output=kind.output, producer={**made["producer"], "output": kind.output,
                                                      "sha256": out.get("sha256")})
        return made

    try:
        owner = generation_leases.use(installed.folder)
        owner.__enter__()
        try:
            worker = await owned_process.off_loop(client, installed)
            encoded = await worker_input(capture) if clip_used is None and typed is None else None
            return await owned_process.off_loop(go)
        finally:
            # The final release may retire an idle process; process cleanup never runs on the event loop.
            await generation_leases.release(owner)
    except Unsupported as exc:  # it refused this input in words: an outcome, said with why, never a failure
        from .operations import Unsupported as Refusal

        raise Refusal(f"{impl.label} doesn't take this: {exc}", next=None) from exc  # no next: its own words say why
    except (Failed, generation_leases.Missing, generation_leases.Busy) as exc:
        raise Unavailable(f"{impl.label} failed: {exc}", next="its card on the Models page") from exc
    finally:
        if lease is not None:
            lease.release()


def _note(installed: Installed) -> str:
    p = installed.proof
    if p.get('scope') == 'bundle-reference':
        return p.get('says', 'bundle reference checks passed')
    return (f"proven {p.get('at', '')[:16].replace('T', ' ')}, found {p.get('found')} on {proof_input_says(p)} "
            "(contract smoke)")


def _plug() -> None:
    from . import kinds

    # Each looked up when used, so what a test stands in for (or a reload) is what the door calls.
    kinds.plug(kinds.Backend("python-env", lambda environ: entries(environ), lambda m, environ: in_use(m, environ),
                             lambda: _run, lambda installed: _note(installed), expand=expanded,
                             own_process=True))  # each adapter runs in its environment's own worker process


_plug()
