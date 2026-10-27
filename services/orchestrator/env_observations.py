"""Raw environment facts for synchronous display reads; never permission to execute.

One observation enumerates each entry's generations once. The App refreshes these off the loop;
keys, settings, trust and readiness remain their existing authorities at display time.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import Path
from typing import Mapping

from gpu_worker import manifests as catalog


@dataclass(frozen=True)
class Generation:
    folder: Path
    receipt: dict
    proof: dict | None
    manifest: catalog.Manifest | None
    python: bool
    snapshot: bool

    checked: bool = False

    @property
    def proven(self) -> bool:
        return self.checked


@dataclass(frozen=True)
class Observation:
    candidate: Path | None = None
    generations: tuple[Generation, ...] = ()
    candidate_proof: dict | None = None
    candidate_python: Path | None = None
    candidate_state: dict = field(default_factory=lambda: {'state': 'absent', 'says': 'Checking its environment'})
    candidate_proven: bool = False
    selection_exists: bool = False
    selection: str = ''
    off: bool = False
    adapter: bool = True
    packages: int = 0

    @cached_property
    def by_name(self):
        return {g.folder.name: g for g in self.generations}

    def installed(self, name: str):
        from .env_runner import Installed
        one = self.by_name.get(name)
        if one is None or not one.proven or one.manifest is None:
            return None
        return Installed(one.manifest, one.folder, one.proof, one.folder == self.candidate)

    def resolved(self):
        if self.selection_exists:
            return self.installed(self.selection)
        for one in sorted(self.generations, key=lambda g: g.folder != self.candidate):
            if found := self.installed(one.folder.name):
                return found
        return None


@dataclass
class Scope:
    observations: dict
    active: bool = True


_SCOPE: ContextVar[Scope | None] = ContextVar('environment_display_observations', default=None)


def key(m, environ):
    from . import envs
    return (str(envs.root(m, environ)), repr(m))


def get(m, environ) -> Observation | None:
    scope = _SCOPE.get()
    if scope is None or not scope.active or not m.env:
        return None
    # An entry arriving during refresh is pending, never an excuse to scan on the loop.
    return scope.observations.get(key(m, environ), Observation())


def memo(name: str) -> dict | None:
    """A dict that lives as long as the active display scope (one wire build), or None outside one: what a display
    read computes from these facts once and several surfaces read (maintenance/status.py)."""
    scope = _SCOPE.get()
    if scope is None or not scope.active:
        return None
    return scope.__dict__.setdefault("_memo", {}).setdefault(name, {})


def display_fact(key, missing):
    """A display-only fact, or None outside the active synchronous display scope."""
    scope = _SCOPE.get()
    if scope is None or not scope.active:
        return None
    return scope.observations.get(key, missing)


@contextmanager
def display(observations):
    scope = Scope(observations)
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        scope.active = False  # a task inheriting this context cannot keep the display's facts
        _SCOPE.reset(token)


@contextmanager
def live():
    """Execution and collection bypass even an enclosing display's observations and hash batch."""
    from . import envs
    token = _SCOPE.set(None)
    hashes = envs._READ_GENERATIONS.set(None)
    try:
        yield
    finally:
        envs._READ_GENERATIONS.reset(hashes)
        _SCOPE.reset(token)


def observe(m, environ: Mapping[str, str] = os.environ) -> Observation:
    from . import env_runner, envs
    with live(), envs.read_batch():
        try:
            candidate = envs.folder(m, environ)
        except OSError:
            candidate = None
        generations = []
        for d, receipt in envs.generations(m, environ):
            path = d / envs.SNAPSHOT / m.id / catalog.MANIFEST
            snapshot = path.is_file()
            kept = catalog.read(path, m.namespace, m.channel)[0] if snapshot else None
            proof = env_runner._proof_of(d)
            python = (d / 'bin' / 'python').exists()
            checked = bool(kept is not None and snapshot and python and env_runner.proof_complete(kept, d, proof))
            generations.append(Generation(d, receipt, proof, kept, python, snapshot, checked))
        candidate_row = next((g for g in generations if g.folder == candidate), None)
        p = candidate_row.proof if candidate_row else env_runner._proof_of(candidate) if candidate else None
        proven = candidate_row.proven if candidate_row else bool(candidate and env_runner._ready_and_proven(m, candidate))
        try:
            state = envs.state(m, environ)
        except (OSError, ValueError, TypeError, AttributeError):
            # A damaged candidate's progress marker must not hide a healthy selected generation.
            state = {'state': 'failed', 'says': 'Its setup record could not be read. Check requirements and retry setup.'}
        from .bundle_runtime import adapters_present
        adapter = (adapters_present(m, catalog.entry_folder(m, environ)) if m.members else
                   (catalog.entry_folder(m, environ) / (m.adapter.partition(':')[0] + '.py')).is_file())
        return Observation(candidate, tuple(generations), p, envs.python(m, environ), state,
                           proven, (envs.root(m, environ) / env_runner.CHOSEN).exists(),
                           env_runner.chosen(m, environ), env_runner.turned_off(m, environ),
                           adapter,
                           envs.size_hint(m, environ))


def collect(environ: Mapping[str, str] = os.environ) -> dict:
    """Metadata only; no adapter imports, workers, model loads or provider calls."""
    with live():
        entries = {**catalog.manifests(), **catalog.mine(environ)}
        from . import fiji_display
        out = {fiji_display.key(environ): fiji_display.collect(environ)}
        for m in entries.values():
            if not m.env:
                continue
            try:
                observed = observe(m, environ)
            except (OSError, ValueError, TypeError, AttributeError):
                # Clear this entry's old display facts instead of freezing the entire catalog refresh.
                # No observed approval survives an unreadable entry; execution still checks live facts.
                observed = Observation(candidate_state={
                    'state': 'failed', 'says': 'Its environment records could not be read. Check requirements and retry setup.'})
            out[key(m, environ)] = observed
            # Readiness resolves the installed manifest's requirements. Its own candidate is that exact
            # retained entry, not the edited source entry's new candidate.
            for g in observed.generations:
                if g.manifest is not None:
                    out[key(g.manifest, environ)] = replace(
                        observed, candidate=g.folder, candidate_proof=g.proof,
                        candidate_python=g.folder / 'bin' / 'python' if g.python else None,
                        candidate_proven=g.proven)
        return out


class Observer:
    """Loop-owned, coalesced refreshes. Only raw disk facts cross the thread boundary."""
    def __init__(self, changed, failed, environ=None):
        self.changed, self.failed = changed, failed
        self.environ = dict(os.environ if environ is None else environ)
        self.snapshot = {}
        self.revision = 0
        self.published = 0
        self.task = None
        self.closed = False

    def request(self):
        if self.closed:
            return None
        self.revision += 1
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._refresh())
        return self.task

    async def refresh(self):
        task = self.request()
        if task is not None:
            await asyncio.shield(task)

    async def _refresh(self):
        while not self.closed:
            revision = self.revision
            reading = asyncio.create_task(asyncio.to_thread(collect, self.environ))
            try:
                snapshot = await asyncio.shield(reading)
            except asyncio.CancelledError:
                await asyncio.gather(reading, return_exceptions=True)
                raise
            except Exception as exc:
                self.failed(exc)
                return
            if self.closed:
                return
            if revision != self.revision:
                continue  # a mutation during collection supersedes it before publication
            changed = not self.published or snapshot != self.snapshot
            self.snapshot, self.published = snapshot, revision
            if changed:
                try:
                    self.changed()
                except Exception as exc:
                    self.failed(exc)
            return

    async def close(self):
        self.closed = True
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
