"""A dataset's summary (dataset.json), written whole and atomically.

`events.jsonl` is the dataset's record of itself (started, resumed, gaps, video parts, stopped); this
file is a summary of it for readers, rewritten whole each time: written to a temporary file, synced
and renamed over the old one, so a crash leaves either the old summary or the new one, never half
of one (docs/datasets.md).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

SCHEMA = 1
NAME = "dataset.json"
EVENTS = "events.jsonl"
FRAMES = "frames.jsonl"
STATES = "start-state.jsonl"
VIDEO = "video"
PICTURES = "pictures"


def iso(epoch: float | None) -> str | None:
    """Local time with seconds and its UTC offset: 2026-10-06T01:29:23-04:00."""
    if not epoch:
        return None
    text = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(epoch))
    return text[:-2] + ":" + text[-2:]


def write(folder: Path, summary: dict) -> None:
    path = folder / NAME
    temp = folder / f".{NAME}.tmp"
    data = json.dumps(summary, indent=1, ensure_ascii=False, default=str)
    with temp.open("w", encoding="utf-8") as f:
        f.write(data + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def read(folder: Path) -> dict | None:
    try:
        data = json.loads((folder / NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def folder_bytes(folder: Path) -> int:
    """Everything in a dataset folder (its linked video included)."""
    total = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass
    return total


def all_in(root: Path) -> list[tuple[Path, dict]]:
    """Every dataset under `root`, newest first (by id, which is its start time)."""
    if not root.is_dir():
        return []
    found = []
    for folder in root.iterdir():
        if folder.is_dir() and not folder.name.startswith("."):
            summary = read(folder)
            if summary is not None:
                found.append((folder, summary))
    return sorted(found, key=lambda pair: pair[0].name, reverse=True)
