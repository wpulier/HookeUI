"""Readiness: whether a model can run here, said as one status and one next step (docs/design/2026-10-12-the-open-catalog.md
§3, ADR 0019).

One resolver over facts kept apart:
- whether it is in this edition (licences.py);
- whether its files are here at the pinned revision (`obtained`: each file its manifest pins, by size and content id);
- which keys it lacks, to get it and to run it, apart;
- whether its code is installed (operations.INSTALLED), and for a built-in model whether the app's own Python has what
  its pinned code imports (shared_python.py: read from that code, never declared);
- whether fine-tuned weights it needs were made here (finetuned.py);
- the app's own last check of it (model_checks.py), added by the caller that has it.

From them, one status for every surface (the Models menu, Capabilities, the agent's refusals, `make doctor`):
- **ready**: it can run;
- **needs-setup**: a step first (a key, a licence, a download, an install, weights made here), the first of them the next;
- **unavailable**: not here, with why (this edition, this Mac, or a contributed job nothing here runs: jobs.no_route);
- **broken**: its last check on this build failed.

`why` is what the app says can't be chosen, in order: an option's edition, its key, its fine-tuned weights, its code,
and its files not downloaded yet (nothing fetches them on first use: gpu_worker/model_files.py; its step is the
Download). The status and steps say more.

Reading is cheap: manifests, file stats and the keys the caller has. Nothing here imports a model library, starts a
worker, calls a provider or downloads (tests/test_readiness.py). Execution still checks (operations.availability's live
part, Loads): a display answer is never permission to run.

`python -m orchestrator.readiness` (`make doctor`) prints every model in the catalog with its status and what to do.
"""
from __future__ import annotations

import math
import os
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Mapping

from gpu_worker import manifests as catalog
from gpu_worker import model_files

from . import observed, shared_python

STATUSES = ("ready", "needs-setup", "unavailable", "broken")
PINNED_CODE = "Install what its code imports"  # a built-in model's pinned code needs a package this Python lacks
GROUPS = {"ready": "Ready", "needs-setup": "Needs setup", "unavailable": "Unavailable here", "broken": "Unavailable here"}
# Facts read once per computation (one capabilities wire, one catalogue): a file's state, an install, the keys file's
# place. Outside a batch nothing is kept, so a change on disk is seen at once.
@dataclass
class _Batch:
    values: dict = field(default_factory=dict)
    active: bool = True


_BATCH: ContextVar[_Batch | None] = ContextVar("readiness_batch", default=None)

# Kept facts (L1b part 2, item 1 of docs/research/2026-10-20-live-view-regression-bisect.md). The voice reads every
# option's readiness every 0.5 s and a click its implementation's, on the event loop: about 250 file stats and a receipt
# read each time (8-11 ms on a quiet Mac; the loaded sims of 2026-10-19/20 stalled the loop 368-590 ms in
# `model_files.published`). The app turns keeping on (`keep`) with what keeps it true: it drops them when anything the
# catalog depends on changes (App.catalog_changed: a download, an install, your models, a check of the files) and reads
# each kept fact again off the loop every so often (`refresh_kept`, in a thread: only the facts' own reads, 2-60 ms, not
# the resolver around them), so the loop reads facts already read. A fact older than KEPT_S is read again where it is
# asked (a refresh that stalled). Only these kinds are kept: what is on disk and
# changes by those events. What changes by itself (keys, observations, loads, checks, selection) is read as before.
# MICROMIND_READINESS_KEPT=off: nothing kept beyond a batch, as before.
KEPT_KINDS = frozenset({"location", "installed", "pinned-code", "weights", "unmade", "not-installed"})
KEPT_S = 60.0
_kept: dict = {}  # key -> (generation, monotonic time read, value, its read)
_kept_lock = threading.Lock()
_keeping = {"on": False, "generation": 0}
_REFRESHING: ContextVar[bool] = ContextVar("readiness_refreshing", default=False)


def keep(on: bool = True) -> bool:
    """Keep the stable facts between computations (the app does, with `invalidate` and `refresh`). Returns it."""
    with _kept_lock:
        _keeping["on"] = bool(on)
        _keeping["generation"] += 1
        _kept.clear()
    return bool(on)


def keeping_wanted(environ: Mapping[str, str] = os.environ) -> bool:
    return (environ.get("MICROMIND_READINESS_KEPT") or "on").strip().lower() not in ("off", "0", "no")


def invalidate(why: str = "") -> None:
    """Something the kept facts depend on changed: they are read again where next asked."""
    with _kept_lock:
        _keeping["generation"] += 1
        _kept.clear()


def refresh(read: Callable[[], object]) -> None:
    """Read the kept facts `read` asks for again (the catalogue, a model's readiness), in the caller's thread."""
    token = _REFRESHING.set(True)
    try:
        with batch():
            read()
    finally:
        _REFRESHING.reset(token)


def refresh_kept() -> int:
    """Each fact kept now read again by its own read, in the caller's thread (the app calls it off the loop). Returns how
    many. One that fails is dropped: it is read where it is next asked, and fails there as it did before."""
    with _kept_lock:
        generation = _keeping["generation"]
        reads = [(key, found[3]) for key, found in _kept.items() if found[0] == generation]
    done = 0
    for key, read in reads:
        try:
            value = read()
        except Exception:  # noqa: BLE001
            with _kept_lock:
                _kept.pop(key, None)
            continue
        with _kept_lock:
            if _keeping["generation"] != generation:
                return done  # invalidated meanwhile: what is read next is read afresh
            _kept[key] = (generation, time.monotonic(), value, read)
        done += 1
    return done


def _kept_value(key: tuple, read: Callable):
    with _kept_lock:
        generation = _keeping["generation"]
        found = _kept.get(key)
    now = time.monotonic()
    if found is not None and found[0] == generation and now - found[1] < KEPT_S and not _REFRESHING.get():
        return found[2]
    value = read()
    with _kept_lock:
        if _keeping["generation"] == generation:  # an invalidation meanwhile: what was read may be from before it
            _kept[key] = (generation, now, value, read)
    return value


@contextmanager
def batch() -> Iterator[None]:
    """Read each fact once while this is open (nested batches share the outer one)."""
    from . import envs

    current = _BATCH.get()
    own = _Batch() if current is None or not current.active else None
    token = _BATCH.set(own) if own is not None else None
    try:
        with envs.read_batch():
            yield
    finally:
        if token is not None:
            own.active = False
            own.values.clear()
            _BATCH.reset(token)


def _once(key: tuple, read: Callable):
    if _keeping["on"] and key[0] in KEPT_KINDS:
        source = lambda: _kept_value(key, read)  # noqa: E731
    else:
        source = read
    batch = _BATCH.get()
    if batch is None or not batch.active:
        return source()
    kept = batch.values
    if key not in kept:
        kept[key] = source()
    return kept[key]


@dataclass(frozen=True)
class Step:
    """One thing left to do before a model runs, in the user's words."""
    kind: str  # key | licence | download | install | weights | edition | platform | route | check | activate | trust
    says: str  # what it is: "Add your Google Gemini API key"
    how: str = ""  # how: "put GEMINI_API_KEY=... in your keys file (~/Desktop/micromind.txt)"
    url: str = ""  # where: the page to get a key or accept a licence
    service: str = ""  # a key step's catalog/services id: the studio's Add key sheet saves it (key_admin.py)
    model: str = ""  # a download step's catalog model: the studio's Download fetches it (downloads.py)

    channel: str = ""  # stable catalog identity, independent of its visible label
    bytes: int = 0  # a download step's bytes still to get: Turn on's sheet adds them up and weighs them against the disk
    # An install step that only turns a kept, proven version back on (after Turn off): nothing is downloaded or installed,
    # so Turn on's sheet says so instead of an install's size and minutes (Window C, 2026-10-14).
    on_again: bool = False

    def wire(self) -> dict:
        return {k: v for k, v in {"kind": self.kind, "says": self.says, "how": self.how, "url": self.url,
                                  "service": self.service, "model": self.model, "channel": self.channel,
                                  "bytes": self.bytes, "onAgain": self.on_again}.items() if v}


@dataclass(frozen=True)
class Readiness:
    status: str
    word: str  # the status in a few words: "Ready", "Needs your Google Gemini API key", "Download 3.5 GB"
    why: str = ""  # why it can't be chosen now ("" when it can): the app's words as before
    steps: tuple[Step, ...] = ()  # what is left, in order; the first is the next step
    model: str = ""  # the catalog model it is a use of ("" for none)
    obtained: bool | None = None  # its files here at the pinned revision (None: nothing to download)
    missing_bytes: int = 0
    # What is happening to its next version (maintenance/status.py; ticket 59's vocabulary), kept apart from whether it
    # can run now: only when a policy is recorded or there is something to say.
    update: dict = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return not self.why

    @property
    def next(self) -> Step | None:
        return self.steps[0] if self.steps else None

    @property
    def group(self) -> str:
        return GROUPS[self.status]

    def wire(self) -> dict:
        """What the studio is sent beside an option's `available` and `why`."""
        return {"status": self.status, "word": self.word, "group": self.group,
                "next": self.next.wire() if self.next else None, "steps": [s.wire() for s in self.steps],
                **({"update": self.update} if self.update else {})}


# --- requirements: who fulfils each kind, and how it is read ----------------------------------------------------------
# Every kind of requirement a manifest can name (gpu_worker/manifests.NEED_KINDS) has a fulfiller and a detector, so
# nothing is ever written that the app can neither satisfy nor read (tests/test_catalog_contract.py; observed.SOURCES says
# where each is read). A fulfiller is the code that does it, "user: …" for what only the user can do and the app then
# reads, or "none: …" for a fact of this Mac the app can't change, said as Unavailable here.
FULFILLERS: dict[str, str] = {
    "key": "orchestrator.key_admin.KeyAdmin",  # Add key: tested, saved, reaches every consumer
    "licence": "user: accept it on its page (observed.probe_access asks Hugging Face; elsewhere observed.confirm)",
    "download": "orchestrator.downloads.download",  # into the models folder, verified, published read-only
    "install": "user: the install command its step says (phase 3's E2 makes it a button)",
    "weights": "orchestrator.finetuned.write",  # made here by fine-tuning (make finetune-cells)
    "platform": "none: this Mac's system",
    "memory": "none: this Mac's memory",
    "data": "orchestrator.downloads.download",  # the data entry it names (an atlas), its files fetched and checked
}


def envs_ready(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> bool:
    from . import envs

    return envs.python(m, environ) is not None


def misfit(m: catalog.Manifest) -> str:
    """Why this Mac itself can't run it ("" when it can): its system (`platform:macos-arm64`) or its memory
    (`memory:<GB>`), read here at every look. Said as Unavailable here; nothing the app or the user can install fixes it."""
    import platform as plat
    import sys

    if m.runner == "fiji" and m.namespace:
        from . import fiji_sandbox

        if not fiji_sandbox.available():
            return "The required Fiji execution sandbox is unavailable on this computer"
    for need in (*m.needs_to_get, *m.needs_to_run):
        kind, _, what = need.partition(":")
        if kind == "platform":
            here = f"{'macos' if sys.platform == 'darwin' else sys.platform}-{'arm64' if plat.machine() in ('arm64', 'aarch64') else plat.machine()}"
            if what != here:
                return f"it runs on {what.replace('macos-arm64', 'a Mac with Apple Silicon')} (this one is {here})"
        if kind == "memory":
            try:
                required = float(what)
            except ValueError:
                return "its memory requirement is invalid; the integration needs correction"
            if not math.isfinite(required) or required <= 0:
                return "its memory requirement is invalid; the integration needs correction"
            try:
                total = int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
            except (AttributeError, ValueError, OSError):
                return "this computer's memory could not be checked; retry the requirements check"
            if total <= 0:
                return "this computer's memory could not be checked; retry the requirements check"
            if total < required * 1e9:
                return f"it needs {what} GB of memory (this Mac has {total / 1e9:.0f} GB)"
    return ""


# --- facts -----------------------------------------------------------------------------------------------------------
def roots(environ: Mapping[str, str] = os.environ) -> dict[str, list[Path]]:
    """Where the old caches are (gpu_worker/model_files.py, the one answer the loaders share)."""
    return model_files.roots(environ)


def obtained(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> tuple[bool | None, int]:
    """Whether a model's pinned files are on this Mac (None when it pins none to get: a cloud model, weights made here),
    and how many bytes are missing: in the models folder, or in an old cache at the pinned revision
    (gpu_worker/model_files.py, which the loaders read the same way)."""
    if not model_files.files(m):
        return None, 0
    where, missing = _location(m, environ)
    return where is not None, missing


def _location(m: catalog.Manifest, environ: Mapping[str, str]):
    pinned = model_files.owner(m)
    return _once(("location", m.key, catalog.revision_of(pinned), repr(pinned.files),
                  tuple((k, tuple(v)) for k, v in model_files.roots(environ).items()),
                  str(catalog.catalog_root(environ)), model_files.standin(environ)),
                 lambda: model_files.location(m, environ))


def where(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> str | None:
    """"folder" (the models folder), "old cache" (make catalog-adopt moves it in), or None."""
    return _location(m, environ)[0] if model_files.files(m) else None


def size_words(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{max(1, round(n / 1e6))} MB"


def _keys(keys: Mapping[str, bool] | None, environ: Mapping[str, str]) -> Callable[[str], bool]:
    """Whether a key is there and was not refused when last tested (observed.py: a key its service refused is no key)."""
    present = (lambda k: bool(keys.get(k))) if keys is not None else (lambda k: bool(environ.get(k)))

    def have(k: str) -> bool:
        if not present(k):
            return False
        obs = observed.latest("key", k, environ)
        return not (obs and obs.get("state") == "no")

    return have


def _keys_file() -> str:
    def read() -> str:
        try:
            from .paths import Paths

            return str(Paths.from_env().keys_file).replace(str(Path.home()), "~")
        except Exception:  # noqa: BLE001 - only the words of an instruction
            return "your keys file"

    return _once(("keys-file",), read)


def _keys_file_blocked() -> str:
    def read() -> str:
        try:
            from . import keys

            return keys.file_problem()
        except Exception:  # noqa: BLE001 - only the words of an instruction
            return ""

    return _once(("keys-file-blocked",), read)


def key_step(key: str, why: str = "", environ: Mapping[str, str] = os.environ) -> Step:
    service = next((s for s in catalog.services().values() if s.key == key), None)
    name = service.name if service else key
    obs = observed.latest("key", key, environ)
    if obs and obs.get("state") == "no":  # there, and its service refused it when tested (observed.py)
        return Step("key", f"Replace your {name} key", f"{obs['says']} (checked {observed.clock(obs.get('at', ''))})",
                    service.get_key if service else "", service.id if service else "")
    from . import keychain

    blocked = _keys_file_blocked()
    if blocked:  # the file is there but this app may not read it: say so, never just "put it in the file" (F-L8-3)
        return Step("key", f"Add your {name} key{why}", blocked, service.get_key if service else "",
                    service.id if service else "")
    how = f"put {key}=... in {_keys_file()}"
    if keychain.enabled():  # the studio can save it (key_admin.py): say that first
        how = f"Add key in the Models menu, or {how}"
    return Step("key", f"Add your {name} key{why}", how, service.get_key if service else "", service.id if service else "")


def _need(needs: tuple[str, ...], kind: str) -> list[str]:
    return [n.split(":", 1)[1] for n in needs if n.startswith(kind + ":")]


def _operational(m: catalog.Manifest, environ: Mapping[str, str]) -> catalog.Manifest:
    from . import env_runner, fiji_display

    installed = fiji_display.in_use(m, environ) if m.runner == "fiji" else None
    return installed.m if installed is not None else env_runner.operational(m, environ)


def model_steps(m: catalog.Manifest, have: Callable[[str], bool], environ: Mapping[str, str] = os.environ
                ) -> tuple[list[Step], bool | None, int]:
    """What is left before a catalog model runs here, in order: getting it (a key, its licence, the download), then
    running it (a key, an install, weights made here)."""
    from .operations import INSTALLED

    m = _operational(m, environ)
    steps: list[Step] = []
    got, missing = obtained(m, environ)
    if got is False:
        for key in _need(m.needs_to_get, "key"):
            if not have(key):
                steps.append(key_step(key, " to download it", environ))
        for url in _need(m.needs_to_get, "licence"):
            # What Hugging Face said when asked (observed.py), never the manifest's declaration alone: granted is no step.
            obs = observed.latest("access", m.key, environ)
            state = (obs or {}).get("state")
            if state == "yes":
                continue
            when = f" (checked {observed.clock(obs.get('at', ''))})" if obs else ""
            if state == "no":
                steps.append(Step("licence", "Accept its licence on Hugging Face" if "huggingface.co" in url else "Accept its licence",
                                  f"{obs['says']}{when}", url))
            elif not observed.askable(m.key):  # a page no probe reads: the user's word (observed.SOURCES "user")
                steps.append(Step("licence", "Accept its licence", "accept it on its page, then say so on its card", url))
            elif state in (None, "unknown") and observed.asking_refused():  # the Example project asks no one (F-L8-4)
                steps.append(Step("licence", observed.NOT_ASKED_STEP, observed.NOT_ASKED_HERE, url))
            else:  # never asked, can't be asked yet (no key), or couldn't ask: said as not confirmed, never as refused
                steps.append(Step("licence", "Its licence: not confirmed yet",
                                  (obs or {}).get("says", "not asked yet: it is checked by itself in a moment") + when, url))
        steps.append(Step("download", f"Download {size_words(missing)}", "Its Download button on the Models page (the chip at the top), or make catalog-download ID="
                          + model_files.owner(m).key, model=model_files.owner(m).key, bytes=missing))
    for key in _need(m.needs_to_run, "key"):
        if not have(key):
            steps.append(key_step(key, environ=environ))
    for what in _need(m.needs_to_run, "data"):  # data it reads (an atlas): another entry's files, downloaded as weights are
        d = catalog.get(what)
        if d is None:
            steps.append(Step("download", f"Its data {what} is not in the catalog", "the catalog lists no entry of that id"))
            continue
        got, gap = obtained(d, environ)
        if not got:
            steps.append(Step("download", f"Download {d.name} ({size_words(gap)})", "its Download on the Models page, or make "
                              f"catalog-download ID={d.id}", model=d.key, bytes=gap))
    if m.runner == "fiji" and m.namespace:
        from . import channels, fiji_sandbox

        if not channels.trusted(m, environ):
            steps.append(Step("trust", f"Allow code from {channels.label(m.channel)}",
                              "Its code runs only in a private Fiji sandbox after its own check",
                              model=m.key, channel=m.channel))
            return steps, got, missing
        if not fiji_sandbox.available():
            steps.append(Step("unsupported", "Fiji contributions cannot run here",
                              "The app could not establish the required operating-system sandbox; nothing runs"))
            return steps, got, missing
    for what in _need(m.needs_to_run, "install"):
        if what == "adapter":  # a skeleton (mine.py): the app can't run it as it is
            steps.append(Step("install", "Write an adapter for it", "it needs an adapter written for it before it can run "
                              "(catalog/README.md, “Add your own”)"))
            continue
        if m.env and m.runner == "python-env":  # its own adapter: a proven generation of it runs (env_runner.in_use)
            from . import channels, env_runner

            if not channels.trusted(m, environ):  # a community channel's: nothing of it runs until the user allows it
                from .channels import label as channel_label

                steps.append(Step("trust", f"Allow code from {channel_label(m.channel)}",
                                  f"{m.name} comes from {channels.said(m.channel, environ)}, not reviewed here: allowing it "
                                  "lets its entries run code on this Mac with your permissions, each after its own "
                                  "Install and proof", model=m.key, channel=m.channel))
                continue

            if env_runner.in_use(m, environ) is not None:
                continue  # an update waiting for its Install is a fact on its card, not a step: the one installed runs
            if env_runner.turned_off(m, environ) and env_runner.installed_any(m, environ):  # off by the user: kept
                steps.append(Step("install", f"Turn {m.name} on", env_runner.ready(m, environ), model=m.key, on_again=True))
                continue
            from .bundle_admission import local_workflow
            if local_workflow(m):
                steps.append(Step('workflow', 'Needs a local workflow review', env_runner.ready(m, environ), model=m.key))
                continue  # preparation and an ordinary Project trial; no automatic proof/retry loop
            if envs_ready(m, environ):  # made, its proof failed or never ran
                steps.append(Step("install", f"Install {m.name} again", env_runner.ready(m, environ), model=m.key))
                continue
        if m.env:  # a Python of its own the app makes (envs.py): Install is a button, done by the app
            from . import envs

            runs_anyway = what in INSTALLED and not _once(("installed", what), INSTALLED[what])  # one made by hand works
            if envs.python(m, environ) is None and not runs_anyway:
                st = envs.state(m, environ)
                steps.append(Step("install", f"Install {m.name}" if st["state"] != "failed" else f"Install {m.name} again",
                                  (st["says"] + ": " if st["state"] in ("failed", "building") else "")
                                  + f"its own Python and {envs.size_hint(m, environ)} packages, each checked by its hash, "
                                  "made in the catalog's folder (its Install button on the Models page)", model=m.key))
            continue
        if what in INSTALLED:
            why = _once(("installed", what), INSTALLED[what])
        elif m.runner == "fiji" and m.kind == "tool-function":  # a Fiji function folder: proven on the generation in use
            from . import fiji_display as fiji_generations

            why = fiji_generations.proven(m.key if m.namespace else what, environ)
        else:
            why = f"not installed here ({what})"
        if why and m.runner == "fiji" and m.kind == "tool-function":  # a Fiji binding: proven on the generation in use
            from . import fiji_display as fiji_generations

            set_up = fiji_generations.current(environ) is not None
            steps.append(Step("install", "Prove it on Fiji" if set_up else "Set up Fiji first", why,
                              model="fiji" if not set_up else m.key))
        elif why:
            steps.append(Step("install", f"Install {_install_name(what)}", why))
    if m.runner == "gpu-worker" and got is not False:  # what its pinned code imports, read from that code (shared_python)
        why = _once(("pinned-code", m.key), lambda: shared_python.missing_from_pinned_code(m, environ))
        if why:
            steps.append(Step("install", PINNED_CODE, why))
    if m.runner == "fiji" and got is not False and not steps:  # its build is here: generation 1 sets it up
        from . import fiji_display as fiji_generations

        if fiji_generations.current(environ) is None:
            failed = next((g for g in fiji_generations.generations(environ) if g["state"] == "failed"), None)
            steps.append(Step("install", "Set up Fiji" if failed is None else "Set up Fiji again",
                              (f"it failed: {failed['why']}. " if failed else "")
                              + "unpacks the downloaded build in the catalog's tools folder and proves it runs, headless "
                              "(its first generation)", model=m.key))
    if m.namespace == "mine" and m.runner == "cellpose-file" and not steps:
        from . import mine

        if not mine.state(m, environ)["on"]:  # ADR 0021: added is not running; Turn on checks it first
            steps.append(Step("activate", "Turn it on", "its check runs once, in a process of its own, before anything uses "
                              "it", model=m.key))
    for weights in _need(m.needs_to_run, "weights"):
        from . import finetuned

        why = _once(("weights", weights), lambda: finetuned.missing(weights))
        if why:
            steps.append(Step("weights", "Make its weights here", why))
    return steps, got, missing


def _install_name(what: str) -> str:
    return {"sam3": "SAM 3.1's code", "micro-sam": "micro-sam's own Python", "cellpose": "Cellpose",
            "open_clip": "OpenCLIP", "trackastra": "Trackastra"}.get(what, what)


def _word(step: Step | None) -> str:
    if step is None:
        return "Ready"
    if step.kind == "key":
        return "Needs y" + step.says[len("Add y"):] if step.says.startswith("Add your") else step.says
    return step.says


def _status(why_edition: str, steps: list[Step], *, why: str, model: str, got: bool | None, missing: int,
            not_here: str = "", no_route: str = "") -> Readiness:
    if why_edition:
        return Readiness("unavailable", "Not in this edition", why=why or why_edition,
                         steps=(Step("edition", why_edition),), model=model, obtained=got, missing_bytes=missing)
    if not_here:
        return Readiness("unavailable", "Not on this Mac", why=not_here, steps=(Step("platform", not_here),), model=model,
                         obtained=got, missing_bytes=missing)
    if no_route:  # a contributed job nothing here runs (jobs.no_route): installing it would never make it on
        return Readiness("unavailable", "Can't be turned on here", why=no_route, steps=(Step("route", no_route),),
                         model=model, obtained=got, missing_bytes=missing)
    if steps:
        # A step left is never choosable: `available` (no `why`) and the status say one thing on every surface (L7b,
        # ticket 62 row 13; a licence or Turn it on step had no `why`, so the menu offered what the card said to set up).
        return Readiness("needs-setup", _word(steps[0]), why=why or steps[0].says, steps=tuple(steps), model=model,
                         obtained=got, missing_bytes=missing)
    return Readiness("ready", "Ready", why=why, model=model, obtained=got, missing_bytes=missing)


def said(status: str, word: str, why: str = "") -> str:
    """The status as one sentence, the same on every surface that says it in words (the agent's capability list, its
    refusals, Actions): "Ready", "Needs setup: Download 1.2 GB", "Unavailable here: it runs on …", "Broken: its last
    check failed: …". `word` is a Readiness's (or its wire's) word; `why` adds the reason when the word doesn't hold it."""
    group = GROUPS.get(status, "Unavailable here") if status != "broken" else "Broken"
    if status == "ready":
        return "Ready"
    detail = word if word and word not in (group, "Broken") else ""
    if why and why != detail and why not in detail:
        detail = f"{detail} ({why})" if detail else why
    return f"{group}: {detail}" if detail else group


def said_wire(w: Mapping | None, why: str = "") -> str:
    """`said` for a readiness already on the wire (Readiness.wire, broken_wire)."""
    if not w:
        return f"Unavailable here: {why}" if why else "Ready"
    step = (w.get("next") or {}).get("says", "")
    return said(w.get("status", ""), step if w.get("status") == "broken" and step else w.get("word", ""), why)


# --- what each surface asks ------------------------------------------------------------------------------------------
def model(model_id: str, keys: Mapping[str, bool] | None = None, environ: Mapping[str, str] = os.environ) -> Readiness:
    """A catalog model's readiness here (`make doctor`, the catalog)."""
    from . import licences

    m = catalog.get(model_id) or catalog.mine(environ).get(model_id)
    if m is None:
        raise KeyError(model_id)

    m = _operational(m, environ)
    steps, got, missing = model_steps(m, _keys(keys, environ), environ)
    edition = licences.why_not(licences.model_uses(m), environ=environ)
    run_keys = [s for s in steps if s.kind == "key" and not s.says.endswith(" to download it")]
    why = edition or (f"needs {_need(m.needs_to_run, 'key')[0]}" if run_keys else "") or next(
        (s.how for s in steps if s.kind in ("install", "weights", "trust", "unsupported")), "")
    from .jobs import no_route
    from .bundle_admission import local_problem

    r = _status(edition, steps, why=why, model=m.key, got=got, missing=missing, not_here=misfit(m),
                no_route=local_problem(m) or no_route(m))
    return _with_update(r, model_id, environ)


def _with_update(r: Readiness, model_id: str, environ: Mapping[str, str]) -> Readiness:
    """The entry's update state beside its readiness, when there is one to say (maintenance/status.py: cheap reads)."""
    from dataclasses import replace

    m = catalog.get(model_id) or catalog.mine(environ).get(model_id)
    if m is None or m.runner != "python-env":
        return r
    from .maintenance import status as maintenance

    u = maintenance.of(m, environ)
    if u and (u.get("state") not in (None, "none") or u["policy"]["policy"] != "manual" or u.get("maintained")):
        return replace(r, update=u)
    return r


def option(group: str, option_id: str, keys: Mapping[str, bool] | None = None,
           environ: Mapping[str, str] = os.environ, *, given=None) -> Readiness:
    """An option's readiness: why it can't be chosen (its edition, its key, its fine-tuned weights, its code, and, for a
    model on this Mac, its files not downloaded: nothing fetches them on first use), and the steps of the model it is a
    use of (a key or a licence to get it, the download). `given`: the Option itself, when the caller iterated a group
    that may have been swapped since (options.change_choices)."""
    from . import licences, options

    from .bundle_admission import local_problem
    from .jobs import no_route

    o = given if given is not None else options.GROUPS[group][option_id]
    have = _keys(keys, environ)
    steps, got, missing, seen = [], None, 0, set()
    not_here = route = ""
    for model_id in (o.model, *o.also):  # its model, then the others it runs (Option.also), each once
        entry = catalog.get(model_id) if model_id else None
        if entry is None or model_files.owner(entry).id in seen:
            continue
        seen.add(model_files.owner(entry).id)
        # This Mac and a job nothing here runs are the model's facts, so its options say them too (`model` does): the
        # card's "Not on this Mac" was a choosable option in the menu and a Run in Actions (L7b).
        not_here = not_here or misfit(entry)
        route = route or local_problem(entry) or no_route(entry)
        more, got_one, missing_one = model_steps(entry, have, environ)
        steps += [s for s in more if s not in steps]
        missing += missing_one
        got = got_one if got is None else (False if got_one is False else got)
    edition = licences.why_not(o.uses, environ=environ)
    why = edition or (f"needs {o.needs}" if o.needs and not have(o.needs) else "") \
        or _once(("unmade", o.weights), lambda: options.unmade(o)) \
        or _once(("not-installed", group, option_id), lambda: options.not_installed(group, option_id)) \
        or next((s.how for s in steps if s.says == PINNED_CODE), "") \
        or (f"not downloaded yet: {size_words(missing)}" + _first(steps) if got is False else "")
    if o.needs and not have(o.needs) and not any(s.kind == "key" and o.needs in s.how for s in steps):
        steps.insert(0, key_step(o.needs, environ=environ))  # a key the option itself requires to run
    if why and not steps and not edition:  # an option with no catalog model of its own: its weights or its code
        steps = [Step("weights" if options.unmade(o) else "install", why, why)]
    return _status(edition, steps, why=why or not_here or route, model=o.model or (o.also[0] if o.also else ""),
                   got=got, missing=missing, not_here=not_here, no_route=route)


def uses(uses_: tuple[str, ...], keys: Mapping[str, bool] | None = None,
         environ: Mapping[str, str] = os.environ) -> Readiness:
    """A model fixed in the code, by the licence components it uses: its edition, and its services' keys
    (capabilities.KEYS), its `why` as before ("needs GEMINI_API_KEY")."""
    from . import licences

    have = _keys(keys, environ)
    edition = licences.why_not(uses_, environ=environ)
    needed = [s.key for s in catalog.services().values() if s.licence and s.licence in uses_]
    missing = [k for k in needed if not have(k)]
    steps = [key_step(k, environ=environ) for k in missing]
    return _status(edition, steps, why=edition or (f"needs {missing[0]}" if missing else ""), model="", got=None,
                   missing=0)


def fixed(model_id: str, components: tuple[str, ...], keys: Mapping[str, bool] | None = None,
          environ: Mapping[str, str] = os.environ) -> Readiness:
    """A fixed role needs both its catalog model and every additional declared component."""
    component = uses(components, keys, environ)
    if not model_id:
        return component
    entry = model(model_id, keys, environ)
    priority = {"ready": 0, "needs-setup": 1, "broken": 2, "unavailable": 3}
    blocking = max((entry, component), key=lambda r: priority[r.status])
    steps = tuple(dict.fromkeys((*entry.steps, *component.steps)))
    # Model download prerequisites can have no legacy `why`. Status and steps
    # still forbid dispatch; an empty reason must not make a fixed role available.
    why = (blocking.why or (blocking.next.says if blocking.next else blocking.word)) \
        if blocking.status != "ready" else ""
    return Readiness(blocking.status, blocking.word, why=why, steps=steps,
                     model=entry.model, obtained=entry.obtained, missing_bytes=entry.missing_bytes)


def _first(steps: list[Step]) -> str:
    """"; first <the step before the download>" when the download can't start yet (its key, its licence), so the
    reason names the same next step as the status (MedSigLIP said "Needs your Hugging Face key" on its card and "not
    downloaded yet" in Actions and the agent's refusals, L7b)."""
    first = steps[0] if steps else None
    if first is None or first.kind not in ("key", "licence"):
        return ""
    return "; first " + first.says[0].lower() + first.says[1:]


def implementation(impl, environ: Mapping[str, str] = os.environ) -> str:
    """Why an operation's implementation can't run here, from what can be said without running it ("" when nothing
    stops it): this edition, its key, its code, its fine-tuned weights. Its worker's state is the runner's to check
    (operations.availability)."""
    from . import licences
    from .operations import not_installed

    if impl.uses and not licences.permitted(impl.uses, environ=environ):
        return licences.why_not(impl.uses, environ=environ) or "not in this edition"
    if impl.needs and not _keys(None, environ)(impl.needs):  # absent, or refused by its service when tested (observed)
        return f"needs {impl.needs}"
    missing = _once(("not-installed", "impl", impl.id), lambda: not_installed(impl))
    if missing:
        return missing
    if impl.weights:
        from . import finetuned

        return _once(("weights", impl.weights), lambda: finetuned.missing(impl.weights))
    from .operations import LOAD_KEYS

    m = catalog.get(LOAD_KEYS.get(impl.id, impl.id))
    if m is not None and misfit(m):  # this Mac, as its card and option say (L7b): never a late, generic crash
        return misfit(m)
    if m is not None and m.runner == "python-env":
        steps, _, _ = model_steps(m, _keys(None, environ), environ)
        return steps[0].how if steps else ""
    if m is not None and m.runner == "gpu-worker":  # its pinned files, never fetched on first use (model_files.py)
        if model_files.files(m):
            where_, missing_bytes = _location(m, environ)
            if where_ is None:
                return f"not downloaded yet: {size_words(missing_bytes)}"
        return _once(("pinned-code", m.key), lambda: shared_python.missing_from_pinned_code(m, environ))
    return ""


def broken(r: Readiness, check: dict | None) -> Readiness:
    """The same readiness, broken when the app's own last check of it on this build failed (`model_checks.wire`)."""
    if not check or check.get("state") != "broken" or check.get("earlierBuild") or r.status in ("unavailable", "needs-setup"):
        return r
    step = Step("check", f"Its last check failed: {check.get('why') or 'it failed when checked'}",
                "Check again on the Capabilities page (Check now), or make capability-check")
    return Readiness("broken", "Broken", why=r.why, steps=(step,), model=r.model, obtained=r.obtained,
                     missing_bytes=r.missing_bytes)


def broken_wire(w: dict, check: dict | None) -> dict:
    """`broken` on a readiness already on the wire (options.catalogue's), with the check the capabilities wire has."""
    if not check or check.get("state") != "broken" or check.get("earlierBuild") or w["status"] in ("unavailable", "needs-setup"):
        return w
    step = Step("check", f"Its last check failed: {check.get('why') or 'it failed when checked'}",
                "Check again on the Capabilities page (Check now), or make capability-check").wire()
    return {**w, "status": "broken", "word": "Broken", "group": GROUPS["broken"], "next": step, "steps": [step]}


# --- make doctor -----------------------------------------------------------------------------------------------------
def doctor(environ: Mapping[str, str] = os.environ) -> str:
    """Every model in the catalog, by status, with what to do: what `make doctor` prints. Keys are read from the keys
    file into this process only to say which are set; their values are never printed."""
    from .keys import load_keys

    keys = load_keys()
    lines = ["ObservationUI: every model in the catalog (catalog/models), and what each needs here", ""]
    rows: dict[str, list[str]] = {g: [] for g in ("Ready", "Needs setup", "Unavailable here")}
    entries = {**catalog.manifests(), **catalog.mine(environ)}
    for key, m in sorted(entries.items(), key=lambda kv: kv[1].name.lower()):
        r = model(key, keys, environ)
        where = {"this-mac": "on this Mac", "cloud": "cloud", "cpu": "on this Mac"}.get(m.runs, m.runs)
        row = f"  {m.name} ({where}): {r.word}"
        for step in r.steps:
            row += f"\n      - {step.says}" + (f": {step.how}" if step.how and step.how != step.says else "") + (
                f" ({step.url})" if step.url else "")
        rows[r.group].append(row)
    for group, items in rows.items():
        lines += [f"{group} ({len(items)})", *items, ""]
    problems = {**catalog.problems(environ), **{f"services/{k}": v for k, v in catalog.service_problems().items()}}
    if problems:
        lines.append(f"Entries that can't be read ({len(problems)})")
        lines += [f"  {k}: {'; '.join(v)}" for k, v in sorted(problems.items())]
        lines.append("")
    lines.append("Keys: " + ", ".join(f"{k} {'set' if v else 'not set'}" for k, v in keys.items()))
    return "\n".join(lines)


if __name__ == "__main__":
    print(doctor())
