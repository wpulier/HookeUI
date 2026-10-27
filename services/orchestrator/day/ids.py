"""Ids the user sees (e3, c12, m2, d4, b1, i5, k1, tb2): new both for the session and for the day's folder (ADR 0017).

A session can span days and a day can hold several sessions, while each store keeps its files in a folder per calendar
day (`evidence/<day>/e3`). So an id is never one this session gave on any day (`(session, id)` names one thing), nor
one any session gave on the day it is filed under (`(day, id)` names one folder). Turn and conversation ids stay the
session journal's own (`t2`, turn 41).

Every store asks `floor` for the number to start above, then keeps its own counter and its folder check as before.
"""
from __future__ import annotations

PREFIXES = ("e", "c", "m", "d", "b", "i", "k", "v", "j", "tb", "an", "gr")  # populations, cells, motion tracks, results, bookmarks,
# individuals, clips, volumes and jobs (registrations.py), and dataset tables (datatables.py): no id of one prefix is
# ever an id of another ("tb2" is no "b2": catalogue.ID reads "tb" first)


def floor(store, prefix: str, day: str | None = None) -> int:
    """The highest number with `prefix` taken so far: by the session `store` holds (on any of its days), or by any
    session on `day` (default: the store's date). A new id takes a higher one. 0 with no store (a test, a bench)."""
    if store is None:
        return 0
    day = day or getattr(store, "date", None)
    mine = int(getattr(store, "ids_used", {}).get(prefix, 0) or 0)
    catalogue = getattr(store, "catalogue", None)
    theirs = catalogue.used(day, prefix) if catalogue is not None and day else 0
    return max(mine, theirs)


def next_number(store, prefix: str, counter: int = 0, day: str | None = None) -> int:
    """The next number to give: above `floor` and above the caller's own counter (its in-memory ids)."""
    return max(floor(store, prefix, day), counter) + 1


def owner(store, session: str | None):
    """The store of the session an id is given in: `store` itself for its own session (or none), else that session's
    (`elsewhere`), so work asked in one session and finished in another takes an id new for the session it is filed in.
    `store` when that session can't be found (its work is filed here then: day/store.py `_session_store`)."""
    if store is None or not session or session == getattr(store, "sid", None):
        return store
    try:
        return store.elsewhere(session)
    except (KeyError, AttributeError):
        return store
