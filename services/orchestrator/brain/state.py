"""The conversation the brain keeps, whichever way you talk to it: the active thread.

Typed chat, speech (GPT-Live), the brain's own answers and notes about what happened on
the other channel all go into one list of turns. Every request carries only the last few
turns (`window`); what falls out of it is folded into the thread's summary, and the rest of
the day is a short digest and a recall tool (day/context.py, day/memory.py).

It is a view of the day store's active thread: turns are written to the day's journal, so
they survive restarts with their ids, and switching threads switches what this shows.
"""
from __future__ import annotations

import time
from contextvars import ContextVar
from typing import TYPE_CHECKING

from ..day.model import Asked, Channel, Role, Turn
from ..untrusted import strip

if TYPE_CHECKING:
    from ..day.store import SessionStore

__all__ = ["Conversation", "Turn"]

# User origin follows the async request, including its late children. It does
# not depend on optional execution logging or the latest user in another task.
_reply_origin = ContextVar("conversation_reply_origin", default=None)


def _norm(text: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


class Conversation:
    WINDOW = 8  # exchanges: the last 8 things the user said, with the replies around them
    NOTES = 4  # notes (what the app did, what changed) ride along, but only the latest few
    MERGE_S = 30.0  # spoken pieces of one request within this long are one turn
    SAME_S = 3.0  # the same words reported twice this close together (heard, then handed over) are one turn

    HEARD_KEPT = 16  # transcript pieces kept (a request's pieces and a little before)

    def __init__(self, store: "SessionStore") -> None:
        self.store = store
        # What the user was heard saying (each final transcript piece, as heard; brain/speech.py), apart from
        # the turns: turns merge a hand-off's text with what was heard, and explicit asks are checked against
        # the user's own words only (recent_request).
        self.heard: list[tuple[float, str]] = []
        self._output_source = None  # bounded source lookup supplied by context, no observer/store

    def was_heard(self, piece: str) -> None:
        """A piece of what the user said, as transcribed: the only words a spoken request's explicit asks are
        checked against (research.policy.quoted; recent_request)."""
        if piece.strip():
            self.heard.append((time.time(), piece.strip()))
            del self.heard[:-self.HEARD_KEPT]

    @property
    def turns(self) -> list[Turn]:
        return self.store.active.turns

    def here(self) -> Asked:
        """Where a request made now belongs (day/model.Asked)."""
        return self.store.here()

    def asked_of(self, turn: Turn) -> Asked:
        """The origin retained with the actual turn, never the latest store's day/token."""
        if turn.asked is None:
            raise ValueError("the turn's original ownership was not retained")
        return turn.asked

    def _remember_origin(self, turn: Turn) -> None:
        original = self.asked_of(turn)
        _reply_origin.set((id(self), original.routing_generation, original.thread, turn.id))

    def add(self, role: Role, channel: Channel, text: str, *, frame_id: int | None = None, asked: Asked | None = None,
            **refs) -> Turn:
        """`asked`: the conversation this belongs to (the one the question was asked in), not
        necessarily the one open now (the user may have switched while it was answered);
        a turn about a researcher (refs rid) without one goes where the researcher started."""
        if (role != "user" and "outputRefs" not in refs and refs.get("objectIds")
                and self._output_source is not None):
            origin = _reply_origin.get()
            thread = self.store.thread_for(asked, refs.get("rid"))
            turn_id = origin[3] if origin and origin[:3] == (id(self), self.store.generation, thread) else None
            rows = []
            for oid in dict.fromkeys(i for i in refs["objectIds"] if isinstance(i, str)):
                if len(rows) >= 8:
                    break
                source = self._output_source(oid)
                if source is not None:
                    rows.append({**source, "turn_id": turn_id})
            refs["outputRefs"] = rows
        turn = self.store.add_turn(role, channel, text, frame_id=frame_id, refs=refs, asked=asked,
                                   rid=refs.get("rid") if asked is None else None)
        if role == "user":
            self._remember_origin(turn)
        return turn

    def bind_output_source(self, lookup) -> None:
        """Qualify actual output metadata at the same write as its conversation receipt."""
        self._output_source = lookup

    def output_refs(self) -> list[dict]:
        """Recent journaled origins. Never reconstruct a legacy ID from today's scene."""
        rows = []
        for turn in self.window(5):
            if turn.role == "user":
                continue
            rows.extend(dict(row) for row in (turn.refs or {}).get("outputRefs", [])
                        if isinstance(row, dict) and isinstance(row.get("id"), str))
        return rows[-8:]

    def _edit(self, turn: Turn, text: str) -> None:
        self.store.edit_turn(turn, text)

    def add_once(self, role: Role, channel: Channel, text: str, *, asked: Asked | None = None, **refs) -> Turn:
        """Add unless a recent turn already says this. Two parts of the system report the same
        spoken request (the voice route as heard, the hand-off as GPT-Live passed it on), and
        a request heard in pieces arrives again joined: one turn holds the fullest version.
        The turn returned says which conversation the request is in (asked_of)."""
        new = _norm(text)
        now = getattr(self.store, "clock", time.time)()  # the clock the store stamps its turns with
        recent = list(self.store.threads[self.store.thread_for(asked)].turns[-4:])
        last = self.store.last_turn
        if last is not None and last not in recent:
            # Said in the conversation open a moment ago ("new topic" heard there, then handed
            # over after hands opened the new one): still the same request, not a new first turn.
            recent.append(last)
        for turn in reversed(recent):
            if turn.role != role or turn.channel != channel:
                continue
            if turn.text == text.strip() and now - turn.at < self.SAME_S:
                if role == "user":
                    self._remember_origin(turn)
                return turn
            if role == "user" and channel == "voice" and new and now - turn.at < self.MERGE_S:
                old = _norm(turn.text)
                if new in old:
                    self._remember_origin(turn)
                    return turn
                if old and old in new:
                    self._edit(turn, text)
                    self._remember_origin(turn)
                    return turn
        return self.add(role, channel, text, asked=asked, **refs)

    def window(self, n: int = WINDOW) -> list[Turn]:
        """The last n exchanges (n user turns and the replies around them), with only the
        latest few notes, so notes about what the app did never push the conversation out."""
        out, users, notes = [], 0, 0
        for t in reversed(self.turns):
            if t.role == "note":
                if notes < self.NOTES:
                    out.append(t)
                    notes += 1
                continue
            if t.role == "user":
                if users >= n:
                    break
                users += 1
            out.append(t)
        return out[::-1]

    def restore(self, records: list[dict]) -> int:
        """Today's conversation from services/logs/conversation.jsonl after a restart (the
        chat as shown: typed and spoken lines), so the brain and a new voice session still
        know what was said. Only lines after the last "session-cleared" marker count.
        With a SessionStore the journal is what is read back; this only imports the chat log
        once, into a day that has no turns yet (the first start with a journal)."""
        if any(t.turns for t in self.store.threads.values()):
            return 0
        start = max((i + 1 for i, r in enumerate(records) if r.get("type") == "session-cleared"), default=0)
        n = 0
        for r in records[start:]:
            role, text = r.get("role"), str(r.get("text") or "").strip()
            if r.get("type") != "chat" or role not in ("user", "assistant") or not text or r.get("update"):
                continue
            channel = "voice" if r.get("voice") else "chat"
            # This legacy chat log has no original Asked. Import ownership is
            # this load's, while the words' original day remains unknown.
            imported = Asked(self.store.active_id, self.store.generation)
            turn = self.add_once(role, channel, text, asked=imported)
            at = r.get("at")
            if isinstance(at, str):
                try:
                    turn.at = time.mktime(time.strptime(at, "%Y-%m-%dT%H:%M:%S"))
                except ValueError:
                    pass
            n += 1
        return n

    def text(self, n: int = WINDOW) -> str:
        """The recent conversation as prompt text, marking how each line arrived. In a long
        thread it starts with the thread's summary and, briefly, any older lines the summary
        does not cover yet (day/memory.py folds them in, in the background)."""
        lines = []
        window = self.window(n)
        thread = self.store.active
        if thread.summary:
            # Model-written from turns that include researchers' notes: no tags of its own.
            summary = strip(thread.summary)
            lines.append(f"(Earlier in this conversation, summarised: {summary})")
        first = window[0].id if window else 0
        gap = [t for t in thread.turns if thread.summary_upto < t.id < first and t.role != "note"]
        if gap:
            lines.append("(Before that, briefly: " + " / ".join(
                f"{'User' if t.role == 'user' else 'You'}: {t.text[:90]}" for t in gap[-12:]) + ")")
        for t in window:
            if t.role == "note":
                lines.append(f"(Note: {strip(t.text)})")  # notes quote researchers: no tags survive
            else:
                who = "User" if t.role == "user" else "You"
                how = " (spoken)" if t.channel == "voice" else ""
                lines.append(f"{who}{how}: {t.text}")
            ids = (t.refs or {}).get("resourceIds") or []
            if ids:
                lines.append("(App resource receipts: " + ", ".join(str(i) for i in ids[:8]) + "; read/open by exact ID.)")
            outputs = (t.refs or {}).get("outputRefs") or []
            if outputs:
                lines.append("(App outline output receipts: " + "; ".join(
                    f"{row['id']} run={row.get('run') or 'unknown'} frame={row.get('frame')}"
                    for row in outputs[:8] if isinstance(row, dict) and row.get("id")) + "; use exact identity.)")
        return "\n".join(lines) or "(none)"
