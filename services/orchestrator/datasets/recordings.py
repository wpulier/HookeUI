"""A session's recordings (GET /api/sessions/{sid}/recordings): the camera video of the Record datasets it holds, for
the studio's camera menu ("This session's recordings") and for what a returning session shows.

Which datasets are the session's is the index's one rule (resources.Index.session_datasets: those its journal says it
started, and older ones no session claims by the date they began). Each part of a dataset's camera video that is on
disk is one recording, newest first: when it began and how long it is, from its sidecar (docs/datasets.md), its name
(the dataset's, else "Recording · Oct 12, 1:44 am") and the dataset's thumbnail. It is a read of datasets and nothing
else: playing one is the studio's choice and the camera bridge's work (POST /feed?source=recording), and what the feed
shows then is never known here (AGENTS.md "Architecture").
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from . import manifest
from .snapshot import sidecar_span


def when_words(epoch: float) -> str:
    """"Oct 12, 1:44 am" (the camera bridge titles a playing recording the same way)."""
    text = time.strftime("%b %-d, %-I:%M %p", time.localtime(epoch))
    return text[:-2] + text[-2:].lower()


def thumb_url(sid: str, dataset: str, summary: dict) -> str:
    """The dataset's picture, as Made in this session shows it (the index's thumbnail of its entry), by a version that
    changes when its parts or state do."""
    version = f"{summary.get('status') or 'done'}.{len((summary.get('video') or {}).get('parts') or [])}"
    return f"/api/resources/{quote(dataset)}/thumbnail?day={quote(sid)}&v={quote(version[:64])}"


def of_datasets(sid: str, rows: list[dict], thumb: Callable[[str, str, dict], str | None] = thumb_url) -> list[dict]:
    """The recordings of the datasets `rows` (datasets/store.row, each with its folder's `path`), newest first."""
    out = []
    for row in rows:
        folder = Path(str(row.get("path") or ""))
        summary = manifest.read(folder) if row.get("path") else None
        if not summary:
            continue
        dataset = str(summary.get("id") or folder.name)
        name = " ".join(str(summary.get("name") or "").split())
        for part in (summary.get("video") or {}).get("parts") or []:
            n = part.get("n") if isinstance(part, dict) else None
            if not isinstance(n, int) or isinstance(n, bool) or n < 1:
                continue
            video = folder / "video" / f"part-{n}.mp4"  # where the bridge plays it from (and where the dataset keeps it)
            if not video.is_file():
                continue  # not linked in yet (a copy across disks waits for the file to finish), or gone
            span = sidecar_span(folder / str(part["sidecar"])) if part.get("sidecar") else None
            begun = span[1] if span is not None else part.get("noticedAtEpoch") or summary.get("startedAtEpoch")
            if not isinstance(begun, (int, float)):
                begun = video.stat().st_mtime
            seconds = round(span[2] - span[1], 1) if span is not None and span[2] > span[1] else None
            out.append({"dataset": dataset, "part": n, "name": name or f"Recording · {when_words(float(begun))}",
                        "startedAt": round(float(begun), 3), "seconds": seconds, "thumb": thumb(sid, dataset, summary)})
    return sorted(out, key=lambda r: (-r["startedAt"], r["dataset"], -r["part"]))


def of_session(index, sid: str) -> list[dict]:
    """Session `sid`'s recordings; none for a session that does not exist (the open one, or one with a journal)."""
    day = getattr(index, "day", None)
    catalogue = getattr(day, "catalogue", None)
    known = sid == index.session() or (catalogue is not None and catalogue.get(sid) is not None)
    return of_datasets(sid, index.session_datasets(sid, fresh=True)) if known else []
