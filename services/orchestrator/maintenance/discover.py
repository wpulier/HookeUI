"""Finding an entry's newer integration revision or upstream release, from its own source, for that entry alone.

An entry from a channel is newer when the channel's default branch is at another commit than the one pinned here
(channels.newest: one bounded read, no code). Under a policy that allows obtaining (review, auto) that commit is fetched,
manifests and adapters, read only, never run, into a candidate snapshot of maintenance's own
(`<catalog>/maintenance/candidates/<channel>/<commit>/`), and the entry's update candidate is read from there. The
channel itself is never moved: its pin is shared by every entry from it, and moving it would change the others (their
versions, licences, a Fiji binding's readiness) without a press. Moving a channel stays a person's Check for catalog
updates. Under manual nothing is fetched and the newer commit is only said.

When no newer commit exists, the candidate is the generation the pinned catalog's entry makes (envs.generation), unless
it is one a promotion already moved away from: then the catalog is behind the entry, not ahead of it.

Each check is kept in checks.json with its outcome. A failed check is tried again after RETRY_S, doubling each time up to
EVERY_S; it never erases a working installation or means that no update exists.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Mapping

from gpu_worker import manifests as catalog

from . import store

EVERY_S = 24 * 3600  # one check a day, as catalog_news's
RETRY_S = 15 * 60  # after a failed check, doubling up to EVERY_S


def _all(environ: Mapping[str, str]) -> dict:
    return store.read(store.path("checks.json", environ), {})


def last(key: str, environ: Mapping[str, str] = os.environ) -> dict:
    rec = _all(environ).get(key)
    return dict(rec) if isinstance(rec, dict) else {}


def due(key: str, environ: Mapping[str, str] = os.environ, now: float | None = None) -> bool:
    rec = last(key, environ)
    return float(rec.get("next", 0)) <= (time.time() if now is None else now)


def _save(key: str, rec: dict, environ: Mapping[str, str]) -> dict:
    before = last(key, environ)
    failures = 0 if rec.get("ok") else int(before.get("failures", 0)) + 1
    wait = EVERY_S if not failures else min(EVERY_S, RETRY_S * 2 ** (failures - 1))
    rec.update(failures=failures, next=time.time() + wait)
    if failures:  # since when its source can't be reached (status: needs attention after UNREACHABLE checks over UNREACHABLE_S)
        rec["failingSince"] = before.get("failingSince") or rec["at"]
    with store.locked(environ):
        found = _all(environ)
        found[key] = rec
        store.write(store.path("checks.json", environ), found)
    return rec


def _channel(name: str, environ: Mapping[str, str]) -> dict | None:
    return next((c for c in catalog.channels(environ) if c["name"] == name), None)


UNREACHABLE = 3  # failed checks in a row, and
UNREACHABLE_S = 3 * 24 * 3600  # for at least this long, after which "couldn't reach its source since …" is said (MT3)
NEWEST_S = 600.0  # how long one look at a channel's newest commit serves every entry from it (MT4)
LOOKS = "_channels"  # in checks.json: each channel's last look, {url: {"commit", "t"}}; attempts are processes of their own


def newest(c: dict, environ: Mapping[str, str] = os.environ, *, again: bool = False) -> str:
    """The channel's newest commit, looked at once per NEWEST_S whatever the number of its entries; `again` (a person's
    Check or Retry) looks now."""
    from .. import channels

    kept = (_all(environ).get(LOOKS) or {}).get(c["url"]) or {}
    if not again and kept.get("commit") and time.time() - float(kept.get("t", 0)) < NEWEST_S:
        return kept["commit"]
    commit = channels.newest(c["url"])
    with store.locked(environ):
        found = _all(environ)
        found.setdefault(LOOKS, {})[c["url"]] = {"commit": commit, "t": time.time()}
        store.write(store.path("checks.json", environ), found)
    return commit


def fresh(key: str, environ: Mapping[str, str] = os.environ) -> catalog.Manifest | None:
    catalog.manifests.cache_clear()
    return catalog.get(key)


UPDATE = "#update"  # an observation's target for what an entry's update candidate needs (observed.py)


def update_target(key: str, version: str) -> str:
    """"<key>#update@<version>": one candidate's own observation, so a later candidate is asked again."""
    return f"{key}{UPDATE}@{version}"


def update_needing_licence(key: str, environ: Mapping[str, str] = os.environ) -> catalog.Manifest | None:
    """The entry's fetched candidate when it newly needs a licence its own source can be asked about, else None."""
    m, update = catalog.get(key), candidate(key, environ)
    new = [n for n in (update.needs_to_get if update else ()) if n.startswith("licence:")
           and n not in (m.needs_to_get if m else ())]
    return update if new and update.source.get("hf") and update.files else None


def candidate(key: str, environ: Mapping[str, str] = os.environ) -> catalog.Manifest | None:
    """The entry as its newest fetched candidate snapshot has it (read only), or None without one."""
    found = last(key, environ).get("found") or {}
    m = catalog.get(key) if found.get("snapshot") else None
    return candidate_manifest(m, found["snapshot"], environ) if m is not None else None


def with_candidates(environ: Mapping[str, str] = os.environ) -> list[str]:
    """The entries whose newest check fetched a candidate snapshot."""
    return [k for k, r in _all(environ).items() if isinstance(r, dict) and (r.get("found") or {}).get("snapshot")]


def snapshot_dir(channel: str, commit: str, environ: Mapping[str, str] = os.environ) -> Path:
    return store.path(f"candidates/{channel}/{commit}", environ)


def candidate_manifest(m: catalog.Manifest, folder: str | Path, environ: Mapping[str, str] = os.environ
                       ) -> catalog.Manifest | None:
    """The entry as a candidate snapshot has it (read only), or None when that commit has no such entry."""
    path = Path(folder) / m.id / catalog.MANIFEST
    if not path.is_file():
        return None
    found, _problems = catalog.read(path, m.namespace, m.channel)
    return found


def superseded(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> set[str]:
    """Generations a promotion moved away from: the pinned catalog naming one of them is behind the entry."""
    from .promote import decisions

    return {d.get("from", "") for d in decisions(m.key, environ) if d.get("action") == "promote"} - {""}


def check(m: catalog.Manifest, environ: Mapping[str, str] = os.environ, *, obtain: bool = False,
          again: bool = True) -> dict:
    """Look once. {"at", "ok", "why", "commit", "found": None | {"generation", "version", "commit", "fetched",
    "snapshot"}, "removed"?}."""
    from .. import channels, env_observations, env_runner, envs

    rec: dict = {"at": store.now(), "ok": True, "found": None}
    commit, source, snap = "", None, None
    try:
        if m.channel:
            c = _channel(m.channel, environ)
            if c is None:
                raise channels.CantDo(f"its channel {m.channel} is no longer listed here")
            commit = c.get("commit", "")
            target = commit if c.get("source") == "shared-file" else newest(c, environ, again=again)
            rec["commit"] = target
            if target != commit:
                if not obtain:
                    rec["found"] = {"commit": target, "fetched": False}
                    return _save(m.key, rec, environ)
                snap = snapshot_dir(m.channel, target, environ)
                if not snap.is_dir():  # this entry's candidate only: the channel's own pin stays where it is
                    snap.parent.mkdir(parents=True, exist_ok=True)
                    channels.fetch(c["url"], target, snap, c.get("path", channels.CATALOG_PATH))
                source = candidate_manifest(m, snap, environ)
                if source is None:  # its catalog's newest commit no longer has it: it keeps working here
                    rec["removed"] = {"commit": target}
                    return _save(m.key, rec, environ)
                commit = target
        now = fresh(m.key, environ)
        if now is None:
            raise channels.CantDo(f"{m.name} is no longer in its catalog")
        from . import adapt
        if adapt.derived(now):  # yours, made from another entry: its candidate is derived from the recorded base (MT2)
            report = adapt.candidate(now, environ)
            if report:
                rec["found"] = {"derived": report["folder"], "version": report["base"]["to"], "fetched": True}
                return _save(m.key, rec, environ)
        with env_observations.live():
            active = env_runner.selected(now, environ)
            generation = envs.generation(source or now, environ)
        behind = source is None and generation in superseded(now, environ)
        if active is not None and active.folder.name != generation and not behind:
            rec["found"] = {"generation": generation, "version": (source or now).version, "commit": commit,
                            "fetched": True, **({"snapshot": str(snap)} if source is not None else {})}
    except (channels.CantDo, OSError) as exc:
        rec.update(ok=False, why=str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__)
    return _save(m.key, rec, environ)
