"""The three homes of everything saved (docs/saves.md): moving the saves out of the checkout, keeping
them in step, pruning the performance logs, and saying where everything is.

  python -m orchestrator.saves report                 where each kind is now, and how big (make saves)
  python -m orchestrator.saves move [--apply]         services/... and recordings/ into ~/MicroscopeMind
  python -m orchestrator.saves sync                   at each start once moved: what older code wrote since
  python -m orchestrator.saves prune [--days N] [--apply]   performance logs older than N days

Moving never deletes anything: every file is hard-linked into its new home (the same disk: instant, no
extra space) or copied, checked by size (and content for small files), and only when every file is
there is the marker written that switches the app to the new homes (paths.saves_root). The originals
stay where they were until the user removes them. Pruning lists what it would remove unless --apply,
and only ever touches the performance logs' dated folders, never kept judgments, assets or datasets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import fields
from datetime import date, datetime
from pathlib import Path

from .paths import KINDS, LIFETIMES, MARKER, REPO, SERVICES, Paths, assets_root, saves_root

CHECK_CONTENT_BYTES = 50 * 1024 * 1024  # files smaller than this are compared by content too
KEPT_LOGS = ("votes.jsonl", "corrections.jsonl")  # judgments people made, in the old logs folder
# Research transcripts, including the legacy logs/transcripts location, are retained
# evidence (ADR 0027). Only disposable performance telemetry belongs in this list.
DATED_LOGS = ("crops", "screens", "saved", "tool-failures")


def old_layout() -> Paths:
    """Where the saves were before they were moved: this checkout's services/ and recordings/."""
    return Paths._layout(SERVICES, recordings=REPO / "recordings", keys_file=Path.home() / "Desktop" / "micromind.txt")


def pairs(old: Paths, new: Paths) -> list[tuple[Path, Path, str]]:
    """(from, to, kind) for every file of every save, old layout to new."""
    out: list[tuple[Path, Path, str]] = []
    mapped_logs: set[Path] = set()
    for f in fields(Paths):
        kind = KINDS.get(f.name)
        if kind in (None, "secret"):
            continue
        source, target = getattr(old, f.name), getattr(new, f.name)
        if source.is_dir():
            out += [(p, target / p.relative_to(source), kind) for p in sorted(source.rglob("*")) if p.is_file()]
        elif source.is_file():
            out.append((source, target, kind))
            mapped_logs.add(source)
    # The rest of the old logs folder (pictures models were shown, saved logs, the servers' logs): with
    # the logs, except the judgments people made, which are kept.
    if old.logs.is_dir():
        for p in sorted(old.logs.rglob("*")):
            if p.is_file() and p not in mapped_logs:
                relative = p.relative_to(old.logs)
                kept = len(relative.parts) == 1 and p.name in KEPT_LOGS
                out.append((p, (new.votes_log.parent if kept else new.logs) / relative, "kept" if kept else "asset" if relative.parts[0] == "transcripts" else "log"))
    return out


def same(a: Path, b: Path) -> bool:
    try:
        sa, sb = a.stat(), b.stat()
    except OSError:
        return False
    if (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino):
        return True
    if sa.st_size != sb.st_size:
        return False
    return sa.st_size > CHECK_CONTENT_BYTES or digest(a) == digest(b)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def place(source: Path, target: Path) -> str:
    """Put `source` at `target`: linked (same disk), copied, already there, or a conflict (a different
    file is there: both are kept, nothing is overwritten)."""
    if target.exists():
        return "there" if same(source, target) else "conflict"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
        return "linked"
    except OSError:
        shutil.copy2(source, target)
        return "copied"


def move(root: Path, apply: bool, out=print) -> int:
    old, new = old_layout(), Paths.saves(root)
    todo = pairs(old, new)
    by_kind: dict[str, list[int]] = {}
    for source, _, kind in todo:
        files_bytes = by_kind.setdefault(kind, [0, 0])
        files_bytes[0] += 1
        files_bytes[1] += source.stat().st_size
    out(f"From {SERVICES} and {REPO / 'recordings'} to {root}:")
    for kind, (n, size) in sorted(by_kind.items()):
        out(f"  {kind:8} {n:7,} files {size / 1e9:8.2f} GB  ({LIFETIMES[kind]})")
    if not apply:
        out("Nothing was changed. Run again with --apply (make saves-move APPLY=1) to move them; the originals stay.")
        return 0
    counts: dict[str, int] = {}
    conflicts = []
    for source, target, _ in todo:
        how = place(source, target)
        counts[how] = counts.get(how, 0) + 1
        if how == "conflict":
            conflicts.append((source, target))
    missing = [t for _, t, _ in todo if not t.exists()]
    out("  " + ", ".join(f"{n:,} {how}" for how, n in sorted(counts.items())))
    if conflicts or missing:
        for source, target in conflicts[:20]:
            out(f"  conflict: {target} differs from {source}; both kept, nothing overwritten")
        out(f"Not switched: {len(conflicts)} conflicts, {len(missing)} missing. The app keeps using {SERVICES}.")
        return 1
    marker = {"version": 1, "movedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
              "movedAtEpoch": round(time.time(), 3), "from": str(SERVICES), "recordings": str(REPO / "recordings"),
              "files": len(todo), "layout": {f.name: str(getattr(new, f.name)) for f in fields(Paths) if KINDS.get(f.name) != "secret"}}
    (root / MARKER).write_text(json.dumps(marker, indent=1) + "\n")
    out(f"Moved. From the next start the app saves in {root}. The originals are still in {SERVICES} and "
        f"{REPO / 'recordings'}; remove them yourself once you are happy.")
    return 0


def sync(root: Path, out=print) -> int:
    """At each start once moved: files older code (or another checkout of it) wrote in the old places
    since the move are linked into the new homes. Only those: what was pruned since is not brought back."""
    try:
        marker = json.loads((root / MARKER).read_text())
    except (OSError, ValueError):
        return 0
    since = float(marker.get("movedAtEpoch") or 0)
    new = Paths.saves(root)
    linked = conflicts = 0
    for source, target, _ in pairs(old_layout(), new):
        if source.stat().st_mtime <= since:
            continue
        how = place(source, target)
        linked += how in ("linked", "copied")
        conflicts += how == "conflict"
    if linked or conflicts:
        out(f"Saves: {linked} files written in the old places since the move linked into {root}"
            + (f"; {conflicts} differ and were left (both kept)" if conflicts else ""))
    return 0


def dated(path: Path) -> date | None:
    """The day a performance-log folder is from: crops/2026-10-06, saved/20261006-191721."""
    name = path.name
    for fmt, length in (("%Y-%m-%d", 10), ("%Y%m%d", 8)):
        try:
            return datetime.strptime(name[:length], fmt).date()
        except ValueError:
            continue
    return None


def prune(paths: Paths, days: int, apply: bool, today: date | None = None, out=print) -> list[Path]:
    """The performance logs' dated folders older than `days` days. Lists them; removes them with `apply`.
    Never kept judgments, assets or datasets."""
    today = today or date.today()
    old: list[Path] = []
    for name in DATED_LOGS:
        folder = paths.logs / name
        if folder.is_dir():
            old += [p for p in sorted(folder.iterdir()) if p.is_dir() and (d := dated(p)) and (today - d).days > days]
    total = sum(size_of(p) for p in old)
    out(f"Performance logs older than {days} days in {paths.logs}: {len(old)} folders, {total / 1e6:.1f} MB")
    for p in old:
        out(f"  {p.relative_to(paths.logs)}")
    if not apply:
        out("Nothing was removed. Run again with --apply (make logs-prune APPLY=1) to remove them.")
        return old
    for p in old:
        shutil.rmtree(p)
    out(f"Removed {len(old)} folders.")
    return old


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def report(out=print) -> None:
    paths, root = Paths.from_env(), saves_root()
    out(f"Saves are in {root} (moved)" if root else f"Saves are in {SERVICES} and {REPO / 'recordings'} (not moved yet: make saves-move)")
    rows = [(f.name, KINDS[f.name], getattr(paths, f.name)) for f in fields(Paths) if KINDS.get(f.name) not in (None, "secret")]
    if root is None:  # the live app's datasets are outside the checkout already (scripts/studio.sh)
        rows = [(name, kind, assets_root() / "datasets" if name == "datasets" else path) for name, kind, path in rows]
    for kind in ("dataset", "asset", "log", "kept"):
        out(f"{kind}: {LIFETIMES[kind]}")
        for name, k, path in rows:
            if k == kind:
                size = size_of(path) if path.exists() else 0
                out(f"  {name:17} {size / 1e6:10.1f} MB  {path}")
    out(f"test assets (the footage library): {assets_root()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("report", "move", "sync", "prune"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--root", help="where the three homes go (default: the test assets' root, ~/MicroscopeMind)")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser() if args.root else (saves_root() or assets_root())
    if args.action == "report":
        report()
        return 0
    if args.action == "move":
        return move(root, args.apply)
    if args.action == "sync":
        return sync(root)
    prune(Paths.from_env(), args.days, args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
