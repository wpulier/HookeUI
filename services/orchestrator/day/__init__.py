"""The session and its threads: one store for everything said and decided in it.

A session (day/sid.py) is still one per day: its id is the date. It holds threads (conversations the
user starts on demand, each able to run for hours and be resumed), the subjects seen and a short digest.
Everything is an event in an append-only journal per session (services/sessions/<sid>/journal.jsonl; a
day journal written before sessions is the session whose id is its date), and the in-memory state is
rebuilt from it at start, so a restart comes back exactly where it was. The user's preferences are the
user's, kept until changed in their own journal beside it (prefs.py). See docs/design and AGENTS.md
"Sessions".
"""
from .model import Thread, Turn
from .store import DayStore, SessionStore

__all__ = ["DayStore", "SessionStore", "Thread", "Turn"]
