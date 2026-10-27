"""What the app has observed about each requirement it can't read on this Mac (P6 of the open catalog, 2026-10-12): a
licence accepted on Hugging Face, a key that works. Never a declaration shown as a blocker.

Will, 2026-10-12: he accepted DINOv3's licence and was approved, and the Models page still said "Accept its licence on
Hugging Face", because the manifest declares the licence and nothing asked. Every requirement on a card is now one of:

1. **Read on this Mac** (files in their folder, code installed, a model of yours turned on): read fresh at every look
   (readiness.py, model_files.py, mine.py).
2. **Asked of the outside world:** a **probe**, here. Its answer is kept with its time and how it was asked, so a card
   says "Hugging Face: your account has access (checked 21:14)". It is asked again:
   - at start;
   - when a key changes (key_admin.apply);
   - when the Models page opens (what is older than PAGE_STALE_S);
   - every NOT_YET_S while it is not yet granted, so an approval arrives by itself;
   - every RECHECK_S otherwise;
   - on "Check again" (command `observe-again`), and when the agent is asked about it.
3. **Proven by running it:** a real attempt is an observation too (a download refused or done: `record`), as are the
   app's model checks (model_checks.py).

**Unknown is said as unknown** ("not checked yet", "couldn't reach Hugging Face"), never as a refusal.

**A probe is one function per kind of requirement** (PROBES), so a new runner (a script's environment, a pipeline's
endpoint) adds its own and every surface relays it: readiness, the cards, the agent.

States: "yes" (granted, accepted), "no" (refused: with what to do), "no-key" (can't be asked without a key),
"unknown" (couldn't ask: offline, rate limited, an error). Kept in <catalog>/observed.json, so a restart shows the last
answer with its time until it is asked again. No key's value is ever kept, logged or said.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Mapping

from gpu_worker import manifests as catalog

HF = "https://huggingface.co"
NOT_YET_S = 300.0  # something not yet granted is asked again this often: an approval relays by itself
RECHECK_S = 3600.0  # something granted is confirmed again this often
PAGE_STALE_S = 120.0  # opening the Models page asks again what is older than this
SWEEP_S = 60.0  # how often the observer looks for what is due
FILE = "observed.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def clock(at: str) -> str:
    """"21:14" from an observation's time, or its date and time when not today."""
    if not at:
        return ""
    day, _, hm = at.partition("T")
    return hm[:5] if day == time.strftime("%Y-%m-%d") else f"{day} {hm[:5]}"


class Store:
    """The observations, in memory and in <catalog>/observed.json (written whole, atomically)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        try:
            self.data: dict[str, dict] = json.loads(path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def get(self, kind: str, target: str) -> dict | None:
        with self.lock:
            found = self.data.get(f"{kind}:{target}")
            return dict(found) if found else None

    def put(self, kind: str, target: str, obs: dict) -> bool:
        """Keep an observation; True when what it says changed (its state, or its words)."""
        obs = {**obs, "at": obs.get("at") or _now(), "t": time.time()}
        with self.lock:
            before = self.data.get(f"{kind}:{target}")
            self.data[f"{kind}:{target}"] = obs
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.data, indent=1))
                tmp.replace(self.path)
            except OSError:
                pass  # kept in memory; asked again after a restart
        return before is None or (before.get("state"), before.get("says")) != (obs.get("state"), obs.get("says"))

    def due(self, kind: str, target: str, now: float | None = None, stale: float | None = None) -> bool:
        found = self.get(kind, target)
        if found is None:
            return True
        age = (now or time.time()) - float(found.get("t") or 0)
        if stale is not None:
            return age >= stale
        return age >= (RECHECK_S if found.get("state") == "yes" else NOT_YET_S)


_stores: dict[str, Store] = {}
_stores_lock = threading.Lock()


def store(environ: Mapping[str, str] = os.environ) -> Store:
    """The store for this catalog (MICROMIND_CATALOG moves it: tests have their own)."""
    path = catalog.catalog_root(environ) / FILE
    with _stores_lock:
        found = _stores.get(str(path))
        if found is None:
            found = _stores[str(path)] = Store(path)
        return found


def latest(kind: str, target: str, environ: Mapping[str, str] = os.environ) -> dict | None:
    """The last observation of a requirement, or None: what readiness and the cards read (cheap: memory only)."""
    return store(environ).get(kind, target)


def record(kind: str, target: str, state: str, says: str, how: str, environ: Mapping[str, str] = os.environ) -> bool:
    """A real attempt observed it (a download refused or done)."""
    return store(environ).put(kind, target, {"state": state, "says": says, "how": how})


# --- the probes: one per kind of requirement ---------------------------------------------------------------------------
def probe_access(key: str, environ: Mapping[str, str] = os.environ) -> dict:
    """Whether this account may download a gated Hugging Face model: one HEAD of its smallest pinned file, with the
    user's Hugging Face key when there is one (2xx or a redirect: granted; 401 without a key: can't ask; 401 or 403
    with one: not granted yet)."""
    import httpx
    from research_worker import api_usage
    api_usage.require_provider()

    base, update, which = key.partition("#update@")
    if update:  # what an entry's update candidate needs, asked of its own source (maintenance, #402 round 2)
        from .maintenance import discover
        m = discover.candidate(base, environ)
        if m is None or m.version != which:
            return {"state": "unknown", "says": "that update is no longer the one waiting", "how": "none"}
    else:
        m = catalog.get(key)
    if m is None or not m.source.get("hf") or not m.files:
        return {"state": "unknown", "says": "nothing to ask Hugging Face about", "how": "none"}
    f = min(m.files, key=lambda f: f["bytes"])
    url = f"{HF}/{m.source['hf']}/resolve/{m.source.get('revision', 'main')}/{f.get('from', {}).get('path') or f['path']}"
    token = environ.get("HF_TOKEN")
    how = f"asked Hugging Face for {m.source['hf']} with {'your Hugging Face key' if token else 'no key'}"
    try:
        r = httpx.head(url, headers={"Authorization": f"Bearer {token}"} if token else {}, follow_redirects=False, timeout=20)
    except httpx.HTTPError as exc:
        return {"state": "unknown", "says": f"couldn't reach Hugging Face to ask ({type(exc).__name__})", "how": how}
    if r.status_code < 400:
        return {"state": "yes", "says": "Hugging Face: your account has access", "how": how}
    if r.status_code == 401 and not token:
        return {"state": "no-key", "says": "Hugging Face needs your key to say whether your account has access", "how": how}
    if r.status_code in (401, 403):
        return {"state": "no", "says": "Hugging Face says your account hasn't been given access yet: accept its licence on "
                                       "its page (approval can take a while)", "how": how}
    return {"state": "unknown", "says": f"Hugging Face answered {r.status_code} when asked", "how": how}


def probe_key(name: str, environ: Mapping[str, str] = os.environ) -> dict:
    """Whether a key works: its service's own free test (key_tests.py), the same as Add key's."""
    from . import key_tests
    from research_worker import api_usage
    api_usage.require_provider()

    service = next((s for s in catalog.services().values() if s.key == name), None)
    value = environ.get(name) or ""
    if service is None or not value:
        return {"state": "unknown", "says": "no key to test", "how": "none"}
    outcome = key_tests.check(service.id, value)
    how = f"tested with {service.name}'s own free call"
    if outcome.state == "accepted":
        return {"state": "yes", "says": f"{service.name} accepted it", "how": how}
    if outcome.state == "rejected":
        return {"state": "no", "says": f"{service.name} refused it: replace it", "how": how}
    return {"state": "unknown", "says": f"couldn't check it with {service.name} ({outcome.why})", "how": how}


PROBES: dict[str, Callable[[str, Mapping[str, str]], dict]] = {"access": probe_access, "key": probe_key}

# How each kind of requirement a manifest can name is known (gpu_worker/manifests.NEED_KINDS). The guarantee: nothing is
# shown as a blocker because it is written down. "local": read on this Mac at every look; "probe:<kind>": asked of the
# outside world (PROBES), kept with when; "user": what only the user can say, kept as their word with when (a licence
# on a page no probe can read). tests/test_observed.py fails on a kind with no source, and a new runner's kind
# (an environment, an endpoint, a binary) is added here with its probe.
SOURCES: dict[str, tuple[str, ...]] = {
    "key": ("local", "probe:key"),  # there (read), and works (its service asked)
    "licence": ("probe:access", "user"),  # asked of Hugging Face; elsewhere, the user's word
    "download": ("local",),  # the pinned files in their folder (model_files.py)
    "install": ("local",),  # its code importable here (operations.INSTALLED)
    "weights": ("local",),  # made here (finetuned.py)
    "platform": ("local",),  # readiness.misfit
    "memory": ("local",),  # readiness.misfit
    "data": ("local",),  # the data entry's pinned files in their folder (model_files.py)
}


def confirm(kind: str, target: str, environ: Mapping[str, str] = os.environ) -> bool:
    """The user's word for what no probe can ask ("I've accepted it"): kept as theirs, with when."""
    return store(environ).put(kind, target, {"state": "yes", "says": "You said you accepted it", "how": "you"})


NOT_ASKED_STEP = "Its licence isn't asked in this example project: open another project to finish setting it up"
NOT_ASKED_HERE = ("This example project stays on this Mac, so the app doesn't ask Hugging Face whether your account "
                  "has access. Open another project to finish setting it up; it is asked there at once.")


def asking_refused() -> bool:
    """Whether this project may not ask the outside world (the local-only Example project, research_worker.api_usage):
    a licence nobody may ask about is said as that, never as "asked in a moment" (F-L8-4)."""
    from research_worker import api_usage

    try:
        api_usage.require_provider()
    except api_usage.ProviderRefused:
        return True
    return False


def askable(key: str) -> bool:
    """Whether a model's licence can be asked of its host (a Hugging Face source): else only the user can say it."""
    m = catalog.get(key)
    return bool(m is not None and m.source.get("hf"))


def targets(environ: Mapping[str, str] = os.environ) -> list[tuple[str, str]]:
    """Every requirement that is asked of the outside world on its own: each gated model's access once there is a Hugging
    Face key (without one Hugging Face can only answer "can't say", so nothing is sent: docs/privacy.md), each key that is
    there. Check again still asks a model's access when the user presses it (Observer.sweep's `only`)."""
    out = []
    for m in {**catalog.manifests(), **catalog.mine(environ)}.values() if environ.get("HF_TOKEN") else ():
        if askable(m.key) and any(n.startswith("licence:") for n in m.needs_to_get):
            out.append(("access", m.key))
    if environ.get("HF_TOKEN"):  # a licence only an entry's update candidate needs, asked of the candidate's source
        from .maintenance import discover
        for key in discover.with_candidates(environ):
            if (update := discover.update_needing_licence(key, environ)) is not None:
                out.append(("access", discover.update_target(key, update.version)))
    for s in catalog.services().values():
        if s.key and environ.get(s.key):
            out.append(("key", s.key))
    return out


class Observer:
    """Asks the outside world off the event loop, one requirement at a time, and tells `changed` when an answer changed
    (the studio is sent the Models page again). The running ones are known (`checking`), so a card says "checking…"."""

    def __init__(self, changed: Callable[[], None] = lambda: None, environ: Mapping[str, str] = os.environ,
                 allowed: Callable[[], bool] = lambda: True) -> None:
        self.allowed = allowed
        self.changed = changed
        self.environ = environ
        self.checking: set[str] = set()
        self._lock = asyncio.Lock()

    async def ask(self, kind: str, target: str) -> dict:
        from research_worker import api_usage
        if not self.allowed():
            raise api_usage.ProviderRefused(api_usage.LOCAL_ONLY)
        self.checking.add(f"{kind}:{target}")
        try:
            obs = await asyncio.to_thread(PROBES[kind], target, self.environ)
        finally:
            self.checking.discard(f"{kind}:{target}")
        if store(self.environ).put(kind, target, obs):
            self.changed()
        return obs

    async def sweep(self, *, stale: float | None = None, only: list[tuple[str, str]] | None = None, force: bool = False) -> int:
        """Ask what is due (or older than `stale`, or everything in `only`/all when `force`). Returns how many were asked."""
        async with self._lock:
            if not self.allowed():
                return 0
            asked = 0
            for kind, target in only if only is not None else await asyncio.to_thread(targets, self.environ):
                if force or store(self.environ).due(kind, target, stale=stale):
                    await self.ask(kind, target)
                    asked += 1
            return asked

    async def run_forever(self, first_after: float = 5.0) -> None:
        await asyncio.sleep(first_after)
        while True:
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - asked again next time; a probe that failed is said as unknown
                pass
            await asyncio.sleep(SWEEP_S)

    def for_model(self, key: str) -> list[tuple[str, str]]:
        """What one model's card depends on outside this Mac: its access, and the keys it needs."""
        m = catalog.get(key)
        if m is None:
            return []
        out = [("access", m.key)] if m.source.get("hf") and any(n.startswith("licence:") for n in m.needs_to_get) else []
        from .maintenance import discover
        if (update := discover.update_needing_licence(key, self.environ)) is not None:  # its update's own licence
            out.append(("access", discover.update_target(key, update.version)))
        for need in (*m.needs_to_get, *m.needs_to_run):
            if need.startswith("key:") and self.environ.get(need[4:]):
                out.append(("key", need[4:]))
        if m.service:
            s = catalog.services().get(m.service)
            if s is not None and s.key and self.environ.get(s.key):
                out.append(("key", s.key))
        return out
