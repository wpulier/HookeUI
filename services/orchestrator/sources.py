"""A session's sources (ADR 0022; docs/design/2026-10-12-session-sources.md): what it looks at.

The live camera, and the user's own assets (library.py) that belong to the session:
- **Opened in it:** opening associates. Its journal's `source` events, day/store.py.
- **Made in it:** its Record videos, snapshots and clearer pictures, each the session's by the index's own rule (the
  asset's `origin`).
- **Not removed:** the user's × takes one out, and Undo puts it back. The library keeps the asset, and what was made from
  it stays the session's history.

Read from the open session's store, or from another session's journal through the catalogue (its Info), with the same
fields: sources (id -> when last opened), removed_sources, on_stage, positions.

Each row also says the other sessions that use the same asset (`alsoIn`, the user's Q27 of 2026-10-12), so earlier work
on it is one click away: those whose journal opened it and did not remove it, and the session it was made in when it
joined there on its own and was not removed there.
"""
from __future__ import annotations

from collections.abc import Iterable

from .library import Asset

JOINED = ("record", "snapshot", "clearer")  # made in a session: its own without being opened
ALSO_IN_MAX = 3  # the other sessions a source row names, newest first


def rows(sid: str, assets: dict[str, Asset], record, others: Iterable = ()) -> dict:
    """Session `sid`'s sources, newest first, each the asset's metadata with when it was opened or made, whether it
    joined on its own, where a recording was left, and the other sessions that use it (`alsoIn`: [{id, label}], newest
    first, at most ALSO_IN_MAX); the ones removed apart (their ids, for Undo). `record`: the open session's store or
    another's catalogue Info. `others`: the catalogue's Infos of every session (this one is skipped); none, no
    `alsoIn` names."""
    opened: dict[str, float] = dict(getattr(record, "sources", {}) or {})
    removed: set[str] = set(getattr(record, "removed_sources", set()) or set())
    positions: dict[str, float] = dict(getattr(record, "positions", {}) or {})
    joined = {a.id: a for a in assets.values() if a.origin == sid and a.group in JOINED}
    users = _users(sid, assets, others)
    out = []
    for aid in set(opened) | set(joined):
        if aid == "live" or aid in removed:
            continue
        asset = assets.get(aid)
        if asset is None:
            continue  # its file is gone (moved, or deleted by the user): nothing to show
        when = max(opened.get(aid, 0.0), (asset.at or 0.0) if aid in joined else 0.0)
        out.append({**asset.metadata(), "opened": round(opened[aid], 3) if aid in opened else None,
                    "joined": aid in joined and aid not in opened, "position": positions.get(aid),
                    "thumb": f"/api/library/{aid}/thumbnail", "alsoIn": users.get(aid, [])[:ALSO_IN_MAX], "_when": when})
    out.sort(key=lambda r: (-r.pop("_when"), r["id"]))
    stage = getattr(record, "on_stage", None)
    return {"session": sid, "onStage": stage if stage == "live" or stage in assets else None,
            "sources": out, "removed": sorted(a for a in removed if a in assets)}


def sessions_using(aid: str, assets: dict[str, Asset], infos: Iterable) -> list[dict]:
    """Every session that uses an asset, newest first ({id, label}): All assets' preview says them, and the trash's
    confirm names them."""
    return _users("", assets, infos).get(aid, [])


def _users(sid: str, assets: dict[str, Asset], others: Iterable) -> dict[str, list[dict]]:
    """Asset id -> the other sessions that use it, newest first ({id, label}): each that opened it and did not remove
    it (by when it last opened it there), and the session it was made in when it joined there on its own and was not
    removed there (by when it was made)."""
    found: dict[str, dict[str, tuple[float, str]]] = {}  # asset -> session -> (when, its label)
    infos = {i.id: i for i in others or () if getattr(i, "id", None) and i.id != sid}
    labels = {s: str(getattr(i, "label", "") or s) for s, i in infos.items()}
    gone = {s: set(getattr(i, "removed_sources", set()) or set()) for s, i in infos.items()}
    for s, info in infos.items():
        for aid, at in dict(getattr(info, "sources", {}) or {}).items():
            if aid != "live" and aid not in gone[s]:
                found.setdefault(aid, {})[s] = (float(at or 0.0), labels[s])
    for asset in assets.values():
        made_in = asset.origin or ""
        if made_in not in infos or asset.group not in JOINED or asset.id in gone[made_in]:
            continue
        was = found.setdefault(asset.id, {}).get(made_in)
        found[asset.id][made_in] = (max(float(asset.at or 0.0), was[0] if was else 0.0), labels[made_in])
    return {aid: [{"id": s, "label": label} for s, (_at, label) in sorted(by.items(), key=lambda kv: (-kv[1][0], kv[0]))]
            for aid, by in found.items()}


def stage_line(meta: dict | None) -> str:
    """The Board's line for the stage while an asset is on it (none for the live camera: it costs nothing, as the
    live view is not listed while it is as it always is). Facts, never an instruction: what the asset is, as its
    metadata says, for the models to read beside the picture."""
    if not meta:
        return ""
    bits = [f"the stage shows asset {meta['id']}: a {'picture' if meta.get('kind') == 'still' else 'recording'}"]
    if meta.get("title"):
        bits.append(f"'{meta['title']}'")
    if meta.get("captured"):
        bits.append(f"captured {meta['captured']}")
    if meta.get("kind") == "recording" and meta.get("seconds"):
        bits.append(f"{_length(meta['seconds'])} long")
    optics = meta.get("optics") or {}
    if optics.get("objective"):
        bits.append(f"{optics['objective']:g}x objective")
    if optics.get("umPerPx"):
        bits.append(f"{optics['umPerPx']:g} µm per pixel")
    if meta.get("specimen"):
        bits.append(f"specimen '{meta['specimen']}'" + (" (named by the user)" if meta.get("specimenBy") == "user" else ""))
    if meta.get("microscope") is False:
        bits.append("not through a microscope")
    return ", ".join(bits)


def _length(seconds: float) -> str:
    s = int(round(float(seconds)))
    return f"{s // 60} min {s % 60:02d} s" if s >= 60 else f"{s} s"
