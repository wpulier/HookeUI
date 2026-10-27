"""Revoked versions and unmaintained entries, as their catalog declares them (ticket 59's MT3).

A catalog says it in one file beside its entries, `maintenance.toml` (`catalog/models/maintenance.toml` in a channel's
repository; the app's own catalog for the entries it bundles). It is no entry, so no entry's generation changes with it,
and an older app ignores it:

    [[revoked]]
    entry = "spot-shape"         # the entry's id in that catalog
    version = "1"                # the version revoked (its manifest's `version`)
    why = "areas were halved"    # plain words
    link = "https://..."         # the issue or advisory
    severity = "warn"            # warn: the card and the agent say it; block: its runs are refused, with what to do

    [[fixes]]
    entry = "renderer"
    versions = ["1"]             # the versions with the defect
    why = "rendered RGB export ignored the displayed Z"
    issue = "https://github.com/qupath/qupath/pull/2211"
    fixed_in = "2"               # empty while no distributable build exists: "Fix identified - compatible build pending"

    [[unmaintained]]
    entry = "old-thing"
    since = "2026-10-01"
    why = "its maintainers stopped working on it"

It is read from the channel's pinned snapshot, the newest look at it (catalog_news's peek: metadata only) and the newest
candidate snapshot an entry's check fetched (discover.py), and
for the app's own entries from its bundled catalog. Reading is a file read; nothing is fetched here. A revoked version
that is in use stays installed: the entry is said to need attention, and only `block` refuses its runs.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Mapping

from gpu_worker import manifests as catalog

from . import store

FILE = "maintenance.toml"
SEVERITIES = ("warn", "block")


def _entry(m):
    """The catalog entry a notice names: a bundle member's view (bundle_contract.CapabilityView) is its owner's."""
    return getattr(m, "owner", None) or m


def _files(m: catalog.Manifest, environ: Mapping[str, str]) -> list[Path]:
    if not m.channel:
        return [catalog.ROOT / FILE] if m.namespace != "mine" else []
    c = next((c for c in catalog.channels(environ) if c["name"] == m.channel), None)
    out = []
    pinned = catalog.snapshot(c, environ) if c else None
    if pinned is not None:
        out.append(pinned / FILE)
    newest = []
    for folder in (catalog.catalog_root(environ) / "news" / m.channel,  # the daily look (metadata only)
                   store.path(f"candidates/{m.channel}", environ)):  # an entry's own fetched candidate (discover.py)
        found = sorted(folder.glob(f"*/{FILE}"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.is_dir() else []
        newest += found[:1]
    # One observation chronology includes later explicit channel updates; reading never moves the pin.
    def observed(path):
        try:
            return path.stat().st_mtime_ns, str(path)
        except OSError:
            return -1, str(path)  # A snapshot need not declare notices; _rows ignores absent files.
    return sorted(set(out + newest), key=observed, reverse=True)


def _rows(path: Path, table: str) -> list[dict]:
    value = store._parsed(path, tomllib.loads) if path.is_file() else None
    rows = (value or {}).get(table) if isinstance(value, dict) else None
    return [dict(r) for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def revoked(m: catalog.Manifest, version: str, environ: Mapping[str, str] = os.environ) -> dict | None:
    """The notice revoking `version` of the entry, the most severe when there are several; None when none does."""
    m = _entry(m)
    found = [r for path in _files(m, environ) for r in _rows(path, "revoked")
             if r.get("entry") == m.id and str(r.get("version")) == str(version)]
    if not found:
        return None
    worst = max(found, key=lambda r: SEVERITIES.index(r["severity"]) if r.get("severity") in SEVERITIES else 0)
    return {"version": str(version), "why": str(worst.get("why") or "its maintainers revoked it"),
            "link": str(worst.get("link") or ""),
            "severity": worst.get("severity") if worst.get("severity") in SEVERITIES else "warn"}


def unmaintained(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict | None:
    """The catalog's notice that nobody maintains the entry any more, or None."""
    m = _entry(m)
    for path in _files(m, environ):
        for r in _rows(path, "unmaintained"):
            if r.get("entry") == m.id:
                return {"since": str(r.get("since") or ""), "why": str(r.get("why") or "its catalog says so")}
    return None


def fix(m: catalog.Manifest, version: str, environ: Mapping[str, str] = os.environ) -> dict | None:
    """A known defect of `version` and its fix, as the catalog declares it (`[[fixes]]`: entry, versions, issue, why,
    fixed_in), or None. `fixed_in` empty: the fix is identified, no distributable build exists yet."""
    m = _entry(m)
    for path in _files(m, environ):
        for r in _rows(path, "fixes"):
            versions = [str(v) for v in r.get("versions") or []]
            if r.get("entry") == m.id and str(version) in versions:
                return {"version": str(version), "issue": str(r.get("issue") or ""),
                        "why": str(r.get("why") or "a known defect in its results"),
                        "fixedIn": str(r.get("fixed_in") or ""), "severity": "warn"}
    return None


def fixes(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> list[dict]:
    """Every `[[fixes]]` row of the entry, as fix() reads one, once each (the same notice may be in several files)."""
    m = _entry(m)
    out, seen = [], set()
    for path in _files(m, environ):
        for r in _rows(path, "fixes"):
            if r.get("entry") == m.id:
                row = {"versions": [str(v) for v in r.get("versions") or []], "issue": str(r.get("issue") or ""),
                       "why": str(r.get("why") or ""), "fixedIn": str(r.get("fixed_in") or "")}
                identity = (tuple(sorted(row['versions'])), row['issue'])
                if identity not in seen:
                    seen.add(identity)
                    out.append(row)
    return out


def blocked(installed, environ: Mapping[str, str] = os.environ) -> str:
    """"" unless the generation's own version is revoked with severity block: then why, and what to do."""
    notice = revoked(installed.m, installed.m.version, environ)
    if not notice or notice["severity"] != "block":
        return ""
    return (f"{installed.m.name} {installed.m.version} was revoked by its maintainers: {notice['why']}"
            + (f" ({notice['link']})" if notice["link"] else "")
            + ". Update it, or choose another version on its card")
