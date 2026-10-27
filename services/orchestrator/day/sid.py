"""Session ids: the only parser of them (the plan "sessions are the project, not the day").

A session id (sid) is a date, `YYYY-MM-DD`, or a date and a number, `YYYY-MM-DD.N` with N >= 2: the
date's first session is the date itself and later ones number on from 2. Every day journal written
before sessions existed IS a session whose id is its date, so no file moves (`session_of`).

Nothing else may split a sid on "." or slice a date out of it: code that needs the date asks
`date_of`, code that orders sessions asks `sort_key`, and the studio treats a sid as an opaque string.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

_SID = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})(?:\.([1-9][0-9]*))?")
JOURNAL = "journal.jsonl"


def parse(sid) -> tuple[str, int]:
    """(date "YYYY-MM-DD", n): n is 1 for the date's own session, N for "date.N". Anything else, a
    "date.1", a "date.02" or a date that is not on the calendar, raises ValueError."""
    if not isinstance(sid, str):
        raise ValueError(f"a session id is a string, not {type(sid).__name__}")
    found = _SID.fullmatch(sid)
    if found is None:
        raise ValueError(f"not a session id: {sid!r} (YYYY-MM-DD or YYYY-MM-DD.N, N >= 2)")
    date, number = found.group(1), found.group(2)
    try:
        time.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"not a session id: {sid!r} ({date} is not a date)") from None
    n = int(number) if number is not None else 1
    if number is not None and n < 2:
        raise ValueError(f"not a session id: {sid!r} (a date's later sessions number from 2)")
    return date, n


def is_sid(text) -> bool:
    try:
        parse(text)
    except ValueError:
        return False
    return True


def date_of(sid: str) -> str:
    """The calendar date a session began on, "YYYY-MM-DD"."""
    return parse(sid)[0]


def sort_key(sid: str) -> tuple[str, int]:
    """Oldest first: by date, then by number ("2026-10-11" < "2026-10-11.2" < "2026-10-11.10")."""
    return parse(sid)


def label(sid: str) -> str:
    """How a session is named when it has no name of its own: "Oct 11", "Oct 11 (2)"."""
    date, n = parse(sid)
    text = time.strftime("%b %-d", time.strptime(date, "%Y-%m-%d"))
    return text if n == 1 else f"{text} ({n})"


def unnamed(sid: str, created: float, now: float) -> str:
    """What an unnamed session is called, said the same everywhere (the studio's rail and chip, Activity, the resume
    line): "Untitled session" while it began today, else its date ("Oct 11", "Oct 11 (2)")."""
    if created and time.strftime("%Y-%m-%d", time.localtime(created)) == time.strftime("%Y-%m-%d", time.localtime(now)):
        return "Untitled project"
    return label(sid)


def next_sid(root: Path | str, date: str) -> str:
    """The id a new session begun on `date` (a date, or a session id: its date) takes in the sessions folder
    `root`: the date itself while no session of that date has a journal, else "date.N", one past the highest N
    used. A numbered folder counts as used whatever it holds (an id is never given twice); the date's own folder
    counts only once it holds a journal (it may hold a studio's workspaces before anything is said)."""
    date = date_of(date)
    root = Path(root)
    used = [1] if (root / date / JOURNAL).exists() else []
    try:
        names = [p.name for p in root.iterdir() if p.is_dir()]
    except OSError:
        names = []
    for name in names:
        if name.startswith(date + ".") and is_sid(name):
            used.append(parse(name)[1])
    return f"{date}.{max(used) + 1}" if used else date


def session_of(record: dict, folder_day: str) -> str:
    """The session a journaled record belongs to: its own "session", else the session of the date folder it
    was found in (a record written before sessions belongs to the session whose id is its date)."""
    return record.get("session") or folder_day
