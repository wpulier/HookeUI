"""New capabilities arrive by themselves (docs/design/2026-10-13-capabilities-for-everyone.md, C1).

What someone added to the catalog reaches everyone without a button: the reviewed catalog is peeked at when the app
starts and once a day (`due`, App.check_catalog: channels.peek, each entry's manifest and pictures only, never an adapter,
a lockfile or weights), and every entry this Mac hasn't known before is **new for you** until it is turned on, said "not
for me", or a month old. Nothing new downloads, installs, runs or turns on by itself: a new entry is offered, with its
picture, what it does, who added it and its one step. One only peeked at (`fetched` false) is got by the user's press
(Get it: the same commit fetched in full, as Check for catalog updates does), then turned on as any entry.

What this Mac has known is kept in `<catalog>/news.json` (this Mac's, never a model's):
- `known`: every entry key seen, with the version and when it was first seen; the first run knows everything already
  there (a fresh install is not "new" from top to bottom);
- `dismissed`: what the user said "not for me" to, never offered again;
- `mentioned`: the exact versions already offered once during an idle conversation;
- `checked`, `result`: the last check online and what it found.

An entry is relevant when its `tags` (or the words of what it does) share a word with what the session looks at; those
come first. The check online reads only the catalog's manifests (channels.update: about 100 KB), never code or weights.
`MICROMIND_CATALOG_AUTO=0` stops the check online (tests and the sim harness set it); the list still works from what this
Mac has.
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import asdict
from functools import wraps
import os
import re
import time
import tomllib
import threading
from pathlib import Path
from typing import Iterable, Mapping

from gpu_worker import manifests as catalog

EVERY_S = 24 * 3600  # a check online at most once a day
FIRST_AFTER_S = 90  # the first, a while after the app starts (never in the way of the live view coming up)
NEW_FOR_DAYS = 30  # "new" no longer after a month
STOP = {"a", "an", "and", "the", "of", "in", "on", "for", "to", "with", "its", "it", "or", "by", "from", "at", "is"}
_STATE_LOCK = threading.RLock()  # the one app's loop and discovery/command worker threads share this file


def transaction(fn):
    """Hold the entire read/mutate/publish transaction, including nested state/discovery reads."""
    @wraps(fn)
    def locked(*args, **kwargs):
        with _STATE_LOCK:
            return fn(*args, **kwargs)
    return locked


def path(environ: Mapping[str, str] = os.environ) -> Path:
    return catalog.catalog_root(environ) / "news.json"


def automatic(environ: Mapping[str, str] = os.environ) -> bool:
    return environ.get("MICROMIND_CATALOG_AUTO", "1") != "0"


def _load(environ: Mapping[str, str]) -> dict | None:
    try:
        data = json.loads(path(environ).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _save(data: dict, environ: Mapping[str, str]) -> None:
    p = path(environ)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    tmp.replace(p)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def entries(environ: Mapping[str, str] = os.environ) -> dict[str, catalog.Manifest]:
    """What the catalog offers: the app's own, the reviewed channel's and community channels' (never the user's own,
    which they added themselves)."""
    return {**catalog.manifests(), **{key: m for key, m in catalog.mine(environ).items() if m.channel}}


@transaction
def state(environ: Mapping[str, str] = os.environ) -> dict:
    """This Mac's record, made on first use: everything already in the catalog is known, nothing is new."""
    data = _load(environ)
    if data is None:
        stamp = _now()
        data = {"known": {k: {"version": m.version, "seen": stamp, "offer": fingerprint(asdict(m))}
                          for k, m in entries(environ).items()},
                "dismissed": [], "baseline": stamp}
        _save(data, environ)
    data.setdefault("known", {})
    data.setdefault("dismissed", [])
    return data


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOP}


def relevant(m: catalog.Manifest, about: Iterable[str]) -> bool:
    """Whether an entry is about what the session looks at: its tags (or its words) share a word with `about`."""
    wanted = set().union(*(words(a) for a in about)) if about else set()
    if not wanted:
        return False
    mine = set().union(*(words(t) for t in m.tags)) if m.tags else words(m.what)
    return bool(mine & wanted)


def preview_shape(data: dict) -> bool:
    """Metadata has no runtime files yet; validate its wire shapes without importing or installing it."""
    text = ('id', 'name', 'runs', 'version', 'kind', 'what', 'runner', 'adapter', 'example', 'service')
    lists = ('needs_to_get', 'needs_to_run', 'licence', 'tags', 'workers', 'protected_on')
    maps = ('source', 'pins', 'env', 'requires', 'component', 'credit', 'contributed_by')
    if any(k in data and not isinstance(data[k], str) for k in text):
        return False
    if any(k in data and (not isinstance(data[k], list) or any(not isinstance(v, str) for v in data[k])) for k in lists):
        return False
    if any(k in data and not isinstance(data[k], dict) for k in maps):
        return False
    if any(k in data and (not isinstance(data[k], list) or any(not isinstance(v, dict) for v in data[k]))
           for k in ('files', 'implements')):
        return False
    try:
        json.dumps(data, allow_nan=False)  # TOML dates and nonfinite floats are not catalog wire values
        return not catalog.incompatible(data)
    except (TypeError, ValueError, AttributeError):
        return False


def peeked(environ: Mapping[str, str] = os.environ) -> tuple[str, str, dict[str, dict]]:
    """The newest peek (channels.peek): its channel, commit and each entry's manifest as data (never loaded as an entry,
    never run), for new entries and changed channel adapters. The active catalog pin stays unchanged."""
    from . import channels

    rec = state(environ).get("peek") or {}
    name, commit = rec.get("name", catalog.REVIEWED_CHANNEL), rec.get("commit", "")
    if not commit:
        return name, "", {}
    listed = entries(environ)
    out = {}
    for p in sorted(channels.peek_dir(name, commit, environ).glob(f"*/{catalog.MANIFEST}")):
        try:
            data = tomllib.loads(p.read_text())
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            continue
        if not isinstance(data, dict) or data.get("id") != p.parent.name:
            continue
        if not preview_shape(data):  # one malformed/incompatible entry cannot take down unrelated setup
            continue
        previous = listed.get(p.parent.name)
        if previous is not None:
            # Bundled code changes only with an app update. Adapter channels already keep their proved snapshot
            # through env_runner.in_use; looking at an update never changes that snapshot or the channel pin.
            if previous.channel != name or previous.runner != 'python-env' or fingerprint(asdict(previous)) == fingerprint(data):
                continue
        out[p.parent.name] = {**data, "_folder": str(p.parent), "_update": previous is not None}
    return name, commit, out


def preview_example(key: str, environ: Mapping[str, str] = os.environ, *, commit: str = "") -> Path | None:
    """A peeked entry's picture of it working, inside its peeked folder only."""
    _, latest, found = peeked(environ)
    if commit and commit != latest:
        return None
    data = found.get(key)
    if not data or not isinstance(data.get("example"), str):
        return None
    folder = Path(data["_folder"]).resolve()
    path = (folder / data["example"]).resolve()
    return path if folder in path.parents and path.is_file() else None


@transaction
def record_peek(result: dict, environ: Mapping[str, str] = os.environ) -> None:
    data = state(environ)
    data["peek"] = {"name": result.get("name", catalog.REVIEWED_CHANNEL), "commit": result.get("commit", ""),
                    "at": _now()}
    _save(data, environ)
    record_check({"ok": True, "commit": result.get("commit", ""), "says": result.get("says", "")}, environ)


def _age_days(stamp: str) -> float:
    try:
        return (time.time() - time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%S"))) / 86400
    except (ValueError, OverflowError):
        return 0.0


def fingerprint(data: dict) -> str:
    keys = ('version', 'source', 'pins', 'files', 'env', 'adapter', 'requires', 'implements', 'needs_to_get', 'needs_to_run')
    defaults = {'files': [], 'implements': [], 'needs_to_get': [], 'needs_to_run': [], 'adapter': '', 'version': ''}
    return hashlib.sha256(json.dumps({k: data.get(k, defaults.get(k, {})) for k in keys}, sort_keys=True).encode()).hexdigest()


def requirements(data: dict) -> list[str]:
    """Plain metadata facts only: declared requirements are not an observed refusal or a permission grant."""
    rows = ['Runs on this Mac' if data.get('runs') in ('this-mac', 'cpu') else 'Uses a cloud service with your key']
    size = sum(f.get('bytes', 0) for f in data.get('files', []) if isinstance(f, dict) and type(f.get('bytes')) is int)
    if size:
        rows.append(f"Download {size / 1_000_000_000:.2f} GB" if size >= 1_000_000_000 else f"Download {size / 1_000_000:.1f} MB")
    if data.get('env'):
        rows.append('Install and check in its own environment')
    names = {'key': 'Account key', 'licence': 'Maker’s terms', 'platform': 'Computer', 'memory': 'Memory',
             'data': 'Companion data', 'weights': 'Model files', 'install': 'Setup'}
    for value in [*data.get('needs_to_get', []), *data.get('needs_to_run', [])]:
        if isinstance(value, str):
            kind, _, need = value.partition(':')
            line = f"{names.get(kind, kind.capitalize())}: {need}"[:300]
            if line not in rows:
                rows.append(line)
    return rows[:16]


@transaction
def mark_mentioned(row: dict, environ: Mapping[str, str] = os.environ) -> None:
    data = state(environ)
    mentions = data.setdefault('mentioned', [])
    identity = row['key'] + ':' + row.get('offer', row.get('version', ''))
    if identity not in mentions:
        mentions.append(identity)
        _save(data, environ)


def unmentioned(rows: list[dict], environ: Mapping[str, str] = os.environ) -> list[dict]:
    mentions = set(state(environ).get('mentioned', []))
    return [r for r in rows if r.get('relevant') and r['key'] + ':' + r.get('offer', r.get('version', '')) not in mentions]


@transaction
def new(environ: Mapping[str, str] = os.environ, about: Iterable[str] = (), ready: Iterable[str] = ()) -> list[dict]:
    """What is new for you (see the module's docstring), relevant first, then the newest; `ready` are entries already on,
    which are no longer offered unless a channel adapter has an update. Each: key, name, what, who added it, its badge, its picture's address, its tags,
    whether relevant and when it arrived."""
    data = state(environ)
    known, dismissed, on = data["known"], set(data["dismissed"]), set(ready)
    channels = {c["name"]: c for c in catalog.channels(environ)}
    channel, commit, peeks = peeked(environ)
    changed = False
    out = []
    for key, m in entries(environ).items():
        if key in dismissed or key in on or key in peeks:
            continue
        offer = fingerprint(asdict(m))
        seen = known.get(key)
        if seen and not seen.get("new"):
            continue
        if seen is None:  # first noticed now: kept as new, with when it arrived
            seen = known[key] = {"version": m.version, "seen": _now(), "new": True, "offer": offer}
            changed = True
        if _age_days(seen["seen"]) > NEW_FOR_DAYS:
            continue
        c = channels.get(m.channel, {}) if m.channel else {}
        out.append({"key": key, "name": m.name, "what": m.what, "kind": m.kind, "tags": list(m.tags),
                    "by": (m.contributed_by or {}).get("name") or "", "byLink": (m.contributed_by or {}).get("link") or "",
                    "makers": (m.credit or {}).get("authors", ""), "link": (m.credit or {}).get("link", ""),
                    "badge": "community" if m.channel and not c.get("reviewed") else "reviewed",
                    "channel": m.channel, "trusted": bool(not m.channel or c.get("reviewed") or c.get("trusted")),
                    "example": f"/api/catalog/example/{key}" if m.example else "",
                    "relevant": relevant(m, about), "arrived": seen["seen"], "fetched": True,
                    "version": m.version, "requirements": requirements(asdict(m)), "update": False, "offer": offer})
    for key, d in peeks.items():  # only peeked at: offered with Get it, nothing of its code on this Mac yet
        update, offer = bool(d.get('_update')), fingerprint(d)
        if key in dismissed or (key in on and not update):
            continue
        seen = known.get(key)
        if seen and seen.get('offer') != offer:
            seen = known[key] = {"version": str(d.get("version", "")), "seen": _now(), "new": True, "offer": offer}
            changed = True
        if seen and not seen.get("new"):
            continue
        if seen is None:
            seen = known[key] = {"version": str(d.get("version", "")), "seen": _now(), "new": True, "offer": offer}
            changed = True
        if _age_days(seen["seen"]) > NEW_FOR_DAYS:
            continue
        by = d.get("contributed_by") if isinstance(d.get("contributed_by"), dict) else {}
        credit = d.get("credit") if isinstance(d.get("credit"), dict) else {}
        tags = [t for t in d.get("tags", []) if isinstance(t, str)] if isinstance(d.get("tags"), list) else []
        about_words = set().union(*(words(a) for a in about)) if about else set()
        mine = set().union(*(words(t) for t in tags)) if tags else words(str(d.get("what", "")))
        out.append({"key": key, "name": str(d.get("name", key)), "what": str(d.get("what", "")),
                    "kind": str(d.get("kind", "model")), "tags": tags, "by": str(by.get("name", "")),
                    "byLink": str(by.get("link", "")), "makers": str(credit.get("authors", "")),
                    "link": str(credit.get("link", "")), "badge": "reviewed", "channel": channel, "trusted": True,
                    "example": f"/api/catalog/example/{key}?preview={commit}" if d.get("example") else "",
                    "relevant": bool(mine & about_words), "arrived": seen["seen"], "fetched": False, "commit": commit,
                    "version": str(d.get("version", "")), "requirements": requirements(d), "update": update, "offer": offer})
    if changed:
        _save(data, environ)
    out.sort(key=lambda r: r["name"].lower())
    out.sort(key=lambda r: r["arrived"], reverse=True)  # the newest first
    out.sort(key=lambda r: not r["relevant"])  # what fits this session before the rest (sorts are stable)
    return out


@transaction
def seen(keys: Iterable[str] | None = None, environ: Mapping[str, str] = os.environ) -> list[str]:
    """"Got it": the new ones (or `keys`) are known now, no longer new. Returns those marked."""
    data = state(environ)
    marked = []
    for key, rec in data["known"].items():
        if rec.get("new") and (keys is None or key in set(keys)):
            rec.pop("new", None)
            marked.append(key)
    _save(data, environ)
    return marked


@transaction
def dismiss(key: str, environ: Mapping[str, str] = os.environ) -> None:
    """"Not for me": never offered again (it stays in the catalog, on its page)."""
    data = state(environ)
    if key not in data["dismissed"]:
        data["dismissed"].append(key)
    rec = data["known"].setdefault(key, {"version": "", "seen": _now()})
    rec.pop("new", None)
    _save(data, environ)


def last_check(environ: Mapping[str, str] = os.environ) -> dict:
    data = state(environ)
    return {"checked": data.get("checked", ""), **(data.get("result") or {})}


def due(environ: Mapping[str, str] = os.environ, now: float | None = None) -> bool:
    """Whether a check online is due: automatic, and none in the last day."""
    if not automatic(environ):
        return False
    stamp = state(environ).get("checked", "")
    if not stamp:
        return True
    try:
        then = time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, OverflowError):
        return True
    return (now if now is not None else time.time()) - then >= EVERY_S


@transaction
def record_check(result: dict, environ: Mapping[str, str] = os.environ) -> None:
    """What a check online found (channels.update's answer, or why it couldn't): kept with when."""
    data = state(environ)
    data["checked"] = _now()
    data["result"] = {k: result[k] for k in ("ok", "commit", "added", "later", "says") if k in result}
    _save(data, environ)


def words_for_agent(rows: list[dict]) -> str:
    """"What's new?" for the agent: each new capability in a line, relevant ones said so."""
    if not rows:
        return "Nothing new in the catalog since the user last looked."
    from .untrusted import block, inline

    lines = [f"New in the catalog ({len(rows)}), offered to the user, none turned on by itself:"]
    for r in rows[:12]:
        lines.append(f"- {'Update available: ' if r.get('update') else ''}{inline(r['name'], 120)}: {inline(r['what'] or r['kind'], 400)}"
                     + (f" (added by {inline(r['by'], 100)})" if r["by"] else "")
                     + (" · community, not reviewed here" if r["badge"] == "community" else "")
                     + (" · fits what this session looks at" if r["relevant"] else "")
                     + f" · arrived {inline(r.get('arrived', ''), 30)}"
                     + (f" · needs: {inline('; '.join(r.get('requirements', [])), 800)}" if r.get("requirements") else ""))
    if len(rows) > 12:
        lines.append(f"- and {len(rows) - 12} more on the Models page")
    return block("\n".join(lines), tag="catalog-news", source="catalog manifests", limit=8000)
