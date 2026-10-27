"""Bookmarks (M3 of docs/design/2026-10-06-captures-operations-evidence.md): places on the slide the user
asked to keep ("bookmark this", "mark this spot"), and the way back to one from the view now.

- **What a bookmark keeps:** where the view was (the eyepiece's centre, in its frame's motion epoch and
  offset, in this run), a small picture of the view, when, the session and conversation, the specimen named then,
  the objective, and the user's own words for it. Kept per date under `bookmarks/<day>/` (an asset, kept until
  the user deletes it), each written whole before it is listed. A session's bookmarks are those it kept on every
  date it touched (ADR 0017); ids are new for the session and for the date folder (day/ids.py).
- **The way back** is measured by the app from how far the slide has moved since (slidemap.place), never
  guessed. Across a lost motion epoch, or from another run of the app, it says it can't tell from here;
  the picture is still there to compare by eye.
- **One index:** the session's index lists them (kind "bookmark"); opening one shows its picture.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import cv2

THUMB_PX = 480  # the picture kept with a bookmark, its longer side
FITTED_SHARE = 0.05  # the fitted eyepiece circle's jitter (a few per cent): more than this is a change of scale


class Bookmarks:
    def __init__(self, root: Path | None, today: Callable[[], str] | None = None, store=None) -> None:
        self.root = Path(root) if root is not None else None
        self.today = today or (lambda: time.strftime("%Y-%m-%d"))
        self.store = store  # the session store (day/store.py): the session open now, its dates and its journal
        # Taking an id and writing its files is one step: two bookmarks at once (each in its own thread) never take
        # the same id, so neither overwrites the other (the M3.3 re-check).
        self._lock = threading.Lock()
        self._given = 0  # the highest number given in this run (day/ids.py)

    def folder(self, day: str | None = None) -> Path | None:
        """A date's folder (default: today's, where new ones go)."""
        return self.root / (day or self.today()) if self.root is not None else None

    def _next_id(self, folder: Path, session: str | None = None) -> str:
        """New for the session it is kept in (on any of its days) and for the date folder (day/ids.py), and never a
        file already there."""
        from .day.ids import next_number, owner

        used = [int(m.group(1)) for p in folder.glob("b*.json") if (m := re.fullmatch(r"b(\d+)\.json", p.name))]
        self._given = next_number(owner(self.store, session), "b", max([self._given, *used]), folder.name)
        return f"b{self._given}"

    def session(self) -> str:
        """The session open now (today's date with no session store: a test's, a bench's)."""
        return getattr(self.store, "sid", None) or self.today()

    def add(self, capture, note: str = "", thread: str | None = None, subject: str | None = None,
            objective: str | None = None, hub=None, asked: str = "", session: str | None = None,
            actor: str | None = None) -> dict:
        """Keep the view of `capture` (a held Capture) as a bookmark of `session` (the one it was asked in; default
        the one open). Raises ValueError when there is nowhere to keep it, no eyepiece to say where it is, or the
        slide's motion was not registered on it (its place could not be related to a later view: M3.3 review B3).
        Off the event loop: it decodes and writes."""
        from .capture import RUN

        folder = self.folder()
        session = session or self.session()
        if folder is None:
            raise ValueError("there is no folder for bookmarks in this run")
        fov = getattr(capture, "fov", None)
        if fov is None:
            raise ValueError("the eyepiece circle is not in view, so where this is can't be kept")
        if not getattr(capture, "tracked", True):
            raise ValueError("the slide's motion was not registered on this view (it was moving), so its place can't be kept")
        jpeg, missing = _thumbnail(capture, hub)  # decoded before the id is taken: the slow part outside the lock
        record = {"note": " ".join(str(note or "").split())[:120], "at": round(time.time(), 3),
                  "wallAt": getattr(capture, "wall_at", None), "frame": getattr(capture, "id", None),
                  "seq": getattr(capture, "seq", None), "boot": getattr(capture, "boot", None) or None, "run": RUN,
                  "epoch": getattr(capture, "epoch", None), "offset": [float(capture.dx), float(capture.dy)],
                  "centre": [float(fov.cx), float(fov.cy)], "radius": float(fov.radius),
                  "session": session, "day": folder.name if folder is not None else None,
                  "thread": thread, "subject": subject, "objective": objective, "asked": " ".join(str(asked).split())[:200],
                  "width": int(getattr(capture, "width", 0) or 0), "height": int(getattr(capture, "height", 0) or 0),
                  # The eyepiece circle's measured radius, or 0 when it was not measured: never the fitted radius in its
                  # place (the two differ by a few per cent; the re-check's note). The fitted one is `radius`.
                  "disc": float(getattr(capture, "disc", 0.0) or 0.0), "tracked": True,
                  **({"actor": actor} if actor else {})}  # who kept it (ticket 66, S2): Data's "Made by"
        with self._lock:
            folder.mkdir(parents=True, exist_ok=True)
            bid = self._next_id(folder, session)
            record = {"id": bid, **record}
            if jpeg:
                _atomic(folder / f"{bid}.jpg", jpeg)
                record["picture"] = f"{bid}.jpg"
            elif missing:
                record["pictureMissing"] = missing  # said with it: kept without its picture, and why
            _atomic(folder / f"{bid}.json", json.dumps(record).encode())  # the record last: listed once whole
        return record

    def journal(self, mark: dict) -> None:
        """File a bookmark kept (`add`) in its session's journal: a product no other event files, so the session list
        counts what Made in this session lists (catalogue.product_of). On the event loop, where the session's state
        lives (`add` runs in a thread)."""
        made = getattr(self.store, "made", None)
        if made is not None:
            try:
                made("bookmark", mark["id"], day=mark.get("day"), thread=mark.get("thread"), session=mark.get("session"))
            except Exception:
                pass  # the bookmark is kept whatever happens to its line in the journal

    def in_folder(self, day: str) -> list[dict]:
        """Every bookmark kept in a date's folder, whatever session (each with its date: "day")."""
        folder = self.folder(day)
        if folder is None or not folder.is_dir():
            return []
        out = []
        for path in sorted(folder.glob("b*.json"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0)):
            try:
                mark = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(mark, dict):
                out.append({**mark, "day": mark.get("day") or folder.name})
        return out

    def session_marks(self, sid: str | None = None, dates: list[str] | None = None) -> list[dict]:
        """A session's bookmarks (default: the one open), oldest first, from every date it touched (`dates`; the open
        one's from its store): each by its own "session", one from before sessions its date's."""
        from .day.sid import session_of

        sid = sid or self.session()
        if dates is None:
            store = self.store
            dates = (sorted(set(getattr(store, "days", []) or []) | {self.today()})
                     if store is not None and sid == getattr(store, "sid", None) else [self.today()])
        marks = [m for date in dates for m in self.in_folder(date) if session_of(m, date) == sid]
        return sorted(marks, key=lambda m: float(m.get("at") or 0.0))

    def all(self, day: str | None = None) -> list[dict]:
        """The session open now's bookmarks, from every date it touched; `day` (a date): that folder's alone."""
        return self.in_folder(day) if day else self.session_marks()

    ORDINALS = {"first": 0, "the first": 0, "the first one": 0, "second": 1, "the second": 1, "the second one": 1,
                "third": 2, "the third": 2, "the third one": 2}

    def find(self, ref: str | None, day: str | None = None) -> list[dict]:
        """The bookmarks `ref` means, newest first: its id ("b2"), "the first one", whole words of its note
        (every one that fits: two with the same words are both said, never one picked silently; review S2), or
        none / "last" for the newest."""
        marks = self.all(day)
        if not marks:
            return []
        key = " ".join(str(ref or "").lower().split())
        if key in ("", "last", "the last", "the last one", "latest", "the bookmark", "it", "that"):
            return [marks[-1]]
        exact = [m for m in marks if m["id"] == key]
        if exact:
            return exact
        if key in self.ORDINALS:
            n = self.ORDINALS[key]
            return [marks[n]] if n < len(marks) else []
        wanted = set(re.findall(r"[a-z0-9]+", key)) - {"the", "a", "an", "my", "bookmark", "one", "place", "spot"}
        named = [m for m in marks if wanted and wanted <= set(re.findall(r"[a-z0-9]+", (m.get("note") or "").lower()))]
        return list(reversed(named))

    def picture(self, bid: str, day: str | None = None, max_bytes: int | None = None) -> bytes | None:
        """A bookmark's picture from its date's folder (`day`; default: the open session's bookmark by that id)."""
        from .resources import read_picture_bytes
        if day is None:
            mark = next((m for m in self.session_marks() if m.get("id") == bid), None)
            day = mark.get("day") if mark is not None else None
            if day is None:
                return None
        folder = self.folder(day)
        path = folder / f"{bid}.jpg" if folder is not None else None
        return read_picture_bytes(path, max_bytes) if path is not None and path.is_file() else None


def conversation_of(mark: dict, day, session: str | None = None) -> str | None:
    """The conversation a bookmark was kept in, by its (session, thread): a session's conversations keep their ids
    for good (ADR 0017), so one kept with its session names its own. One kept before sessions (its date's session)
    names it only while it is still that conversation of the session loaded (`day`, the store): after "start today
    afresh" a new conversation of the same date could have the same id, and one started after the bookmark was kept
    never holds it (the M3.3 re-check, S1). `session`: the session it is listed in (default: the one loaded)."""
    tid = mark.get("thread")
    if not tid or mark.get("session"):
        return tid
    threads = getattr(day, "threads", None)
    if not isinstance(threads, dict) or (session or getattr(day, "sid", None)) != getattr(day, "sid", None):
        return tid  # another session's: its own conversation, by its id
    thread = threads.get(tid)
    if thread is None or float(getattr(thread, "started", 0.0) or 0.0) > float(mark.get("at") or 0.0) + 1.0:
        return None
    return tid


def place_of(mark: dict, info, objective=None):
    """Where the bookmarked view's centre is from the view now (slidemap.Place), or None and why: only when the
    two views can be related by the slide's move alone (contract 3's preconditions, apply.preconditions: the same
    epoch, picture size, eyepiece scale and objective; review B3)."""
    from .apply import preconditions
    from .capture import RUN
    from .slidemap import place

    if mark.get("run") != RUN:
        return None, "it was kept in an earlier run of the app, so the slide's movement since is not known"
    if mark.get("tracked") is False:
        return None, "the slide's motion was not registered when it was kept"
    then = SimpleNamespace(epoch=mark.get("epoch"), width=mark.get("width"), height=mark.get("height"),
                           disc=mark.get("disc") or 0.0, scale={"objective": mark.get("objective")})
    if mark.get("width") is not None:  # a bookmark kept before these facts were: the epoch is all it can check
        why = preconditions(then, info, objective)
        if why:
            return None, why
    measured = (mark.get("disc") or 0) > 0 and (getattr(info, "disc", 0) or 0) > 0
    fov = getattr(info, "fov", None)
    if not measured and mark.get("radius") and fov is not None and getattr(fov, "radius", 0):
        # Without the measured radius on both, the fitted eyepiece circle (it jitters by a few per cent) still shows a
        # zoom or a lens change: a halved circle never gives a direction (the re-check's note).
        r0, r1 = float(mark["radius"]), float(fov.radius)
        if abs(r0 - r1) / max(r0, r1) > FITTED_SHARE:
            return None, "the eyepiece's scale changed since (the lens refocused or zoomed)"
    if not getattr(info, "tracked", True):
        return None, "the slide is moving and its motion is not registered on the view now; ask again when it is still"
    point = SimpleNamespace(center=tuple(mark["centre"]), anchor_epoch=mark.get("epoch"), anchor_offset=tuple(mark["offset"]))
    found = place(point, info)
    if found is None:
        return None, "the app lost track of the slide's movement since (the view jumped or moved too fast)"
    return found, ""


def _thumbnail(capture, hub=None) -> tuple[bytes | None, str]:
    """The kept picture and, when there is none, why. Decoded, made small and encoded inside the memory bound
    (invariant 3: review B4); when the bound refuses, the bookmark is kept without its picture, never decoded
    uncounted (the re-check)."""
    if hub is None:
        return None, "no picture: there was no view to decode it from"
    from .capture import CaptureGone, Refused, decoding

    try:
        with decoding(hub, capture, "look", owner="a bookmark") as held:
            image = held.bgr()
            if image is None:
                return None, "no picture: the view could not be decoded"
            h, w = image.shape[:2]
            scale = THUMB_PX / max(h, w)
            if scale < 1:
                image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 88])
            return (jpeg.tobytes(), "") if ok else (None, "no picture: it could not be encoded")
    except Refused as exc:
        return None, f"no picture: {exc}"
    except (CaptureGone, AttributeError):
        return None, "no picture: the view was no longer held"


def _atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
