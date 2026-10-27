"""Dialogue: the chat as shown, each line filed with the conversation it belongs to.

A line (CONTEXT.md) is a turn as the chat showed it, or a later update to one (a tool that
finished, `chat-action`). Every line goes through `show`, which files it under one
conversation: the one the request was asked in (`asked`, fixed when the request started), else
the one its researcher was started in (`rid`), else the active one. It then goes into the
session's journal, the conversation log (services/logs/conversation.jsonl, kept for `make
save-logs`) and to every open studio. A reload or a restart replays the journal's lines
(`history`), so what is shown again is exactly what was shown, in the conversation it was
shown in (ADR 0001).

Before this module the hub kept the chat (in memory and the log) and each caller found the
conversation its own way; replies, late results and narrated results could land in whichever
conversation was open.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from .day import sid as sids
from .day.model import Asked
from .day.store import SessionStore

REPLAY = 1500  # lines replayed to a studio: one conversation can run for hours


class Dialogue:
    def __init__(self, day: SessionStore, publish: Callable[[dict], None], log: Path | None = None) -> None:
        self.day = day
        self.publish = publish  # to every open studio (hub.broadcast)
        self.log = log

    def here(self) -> Asked:
        return self.day.here()

    def show(self, line: dict, *, asked: Asked | None = None, rid: str | None = None) -> dict:
        """A shown line or application activity for its conversation; returns it as sent. A line of a request asked in
        another session (ADR 0017: a late answer after a switch) is filed in that session and never shown in this one:
        it reaches the conversation log under its own session, and no studio."""
        line = {"at": self.day.clock(), **line}
        line = self.day.add_line(line, asked=asked, rid=rid)
        other = line.pop("session", None)
        if other:
            self._export({"day": other, **line})
            return line
        self._export({"day": self.day.sid, **line})
        self.publish(line)
        return line

    def _filing_session(self, asked: Asked | None) -> str:
        """The session a line asked as `asked` is filed in: its own when that session is there, else this one."""
        sid = getattr(asked, "session", None)
        if not sid or sid == self.day.sid:
            return self.day.sid
        try:
            return self.day.elsewhere(sid).sid
        except KeyError:
            return self.day.sid  # its folder is gone: filed here, never lost

    def notice(self, *, producer: dict, event: str, summary: str, facts: dict,
               asked: Asked | None = None, rid: str | None = None, origin: dict | None = None,
               tone: str = "info", refs: dict | None = None, filing_asked: Asked | None = None,
               from_session: dict | None = None) -> dict:
        """Append factual application activity, independent of assistant text or voice.

        Original identity is metadata, not a new filing rule: show/add_line use
        the existing Asked/research routing, including the stale-generation case.
        Missing origins stay unknown; the current filing is not their evidence.
        Producers supply actual events/IDs, never manufactured tool receipts.
        `from_session` ({"id", "name"}): the work it is about was asked in another session (a researcher started
        there); the studio says "from 'Blood smear'".
        """
        if not isinstance(producer, dict) or not isinstance(producer.get("kind"), str) or not producer["kind"]:
            raise ValueError("a notice needs its actual producer kind")
        who = {"kind": producer["kind"]}
        if "id" in producer:
            if not isinstance(producer["id"], str) or not producer["id"]:
                raise ValueError("a notice producer id must be an existing nonempty string")
            who["id"] = producer["id"]
        if not isinstance(event, str) or not event or not isinstance(summary, str) or not summary:
            raise ValueError("a notice needs an event and factual summary")
        if not isinstance(facts, dict) or tone not in ("info", "error"):
            raise ValueError("a notice needs JSON facts and an info/error presentation tone")
        # Optional existing producer references travel with replay. They cannot
        # overwrite notice authorship, identity, timing or factual content.
        if refs is not None and (not isinstance(refs, dict) or set(refs) - {"objectIds", "quiz"}):
            raise ValueError("notice references must be objectIds or quiz metadata")
        supplied = origin or {}
        identity = {"day": supplied.get("day")}
        for key in ("asked", "thread", "researchId", "ticketId", "requestId", "actionId", "turnId", "delegationId"):
            if key in supplied and supplied[key] is not None:
                identity[key] = supplied[key]
        if asked is not None:
            identity["asked"] = {"thread": asked.thread, "generation": asked.generation}
            if asked.day is not None:
                identity["day"] = asked.day
        if rid is not None:
            identity["researchId"] = rid
        # Detach before journaling/publishing: mutable producer data never changes
        # the saved notice later. No line/turn/notice ID or second ledger is made.
        elsewhere = {}
        if from_session is not None:
            if not isinstance(from_session, dict) or not isinstance(from_session.get("id"), str) or not from_session["id"]:
                raise ValueError("from_session names the session by its id")
            elsewhere = {"fromSession": {"id": from_session["id"], "name": str(from_session.get("name") or "")}}
        filed_in = self._filing_session(filing_asked if filing_asked is not None else asked)
        line = json.loads(json.dumps({"type": "app-notice", "day": filed_in, "at": self.day.clock(),
                                      "producer": who, "event": event, "summary": summary, "tone": tone,
                                      "facts": facts, "origin": identity, **elsewhere, **(refs or {})},
                                     ensure_ascii=False, allow_nan=False))
        # A producer reopening its actual earlier journal can route on that
        # journal's load while preserving the original Asked in the notice.
        return self.show(line, asked=filing_asked if filing_asked is not None else asked, rid=rid)

    def stream(self, line: dict, *, asked: Asked | None = None) -> dict:
        """A line that is still growing (a spoken one), sent to every open studio only: not filed, not
        in the log. The studios update the line with this `lineId` in place; it was filed when it began,
        and `show` with the same `lineId` files it again whole, in its place (SessionStore)."""
        line = {**line, "thread": self.day.thread_for(asked, None), "open": True}
        if self._filing_session(asked) != self.day.sid:
            return line  # asked in another session: filed there whole, never shown in this one
        self.publish(line)
        return line

    def marker(self, record: dict) -> None:
        """A record in the conversation log that is not a line ("session-cleared")."""
        self._export(record)

    def history(self, limit: int = REPLAY) -> dict:
        """The active conversation as it was shown: the studio replaces its log with this when
        the conversation changes or it reconnects. A conversation from before lines were kept
        is shown from its turns."""
        day = self.day
        lines = day.active.lines[-limit:]
        if all(line.get("type") == "app-notice" and (line.get("producer") or {}).get("kind") == "session"
               for line in day.active.lines):
            # Its turns, then the lines saying it was opened again (a session's resume line must not hide what was said
            # before lines were kept).
            said = [{"type": "chat", "role": t.role, "text": t.text, "turnId": t.id, "voice": t.channel == "voice",
                     "objectIds": t.refs.get("objectIds") or [], "thread": day.active_id}
                    for t in day.active.turns if t.role != "note"]
            lines = (said + list(day.active.lines))[-limit:]
        return {"type": "thread", "day": day.sid, "thread": day.active_id, "title": day.active.title, "lines": lines,
                "summary": day.active.summary}

    # --- the conversation log ----------------------------------------------------------
    def today_in_log(self) -> list[dict]:
        """The session's filed records, after the last `session-cleared`.

        A valid explicit filing session (its "day", a session id: day/sid.py) owns a record even when it was
        exported on another day. Timestamp-only legacy records keep their existing import, by the session's date.
        """
        if self.log is None:
            return []
        from .looplag import LINES

        LINES.flush()  # lines still with the writer thread are read too
        records = []
        try:
            with self.log.open() as f:
                for raw in f:
                    try:
                        record = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    filing_day = record.get("day")
                    belongs = (filing_day == self.day.sid if sids.is_sid(filing_day) else
                               str(record.get("loggedAt", record.get("at", ""))).startswith(self.day.date))
                    if belongs:
                        records.append(record)
        except OSError:
            return []
        start = max((i + 1 for i, r in enumerate(records) if r.get("type") == "session-cleared"), default=0)
        return records[start:]

    def import_log(self, records: list[dict]) -> int:
        """Today's lines from the conversation log into the journal, once (a day from before
        lines were journaled): a restart then replays them from the journal. Lines from before
        conversations existed belong to the first one."""
        day = self.day
        if any(t.lines for t in day.threads.values()):
            return 0
        n = 0
        for record in records:
            if record.get("type") not in ("chat", "chat-action", "app-notice", "activity"):
                continue
            tid = record.get("thread") if record.get("thread") in day.threads else next(iter(day.threads))
            removed = ("loggedAt", "thread") if record.get("type") == "app-notice" else ("at", "day", "thread")
            line = {k: v for k, v in record.items() if k not in removed}
            if isinstance(record.get("at"), (int, float)) and not isinstance(record["at"], bool):
                line["at"] = record["at"]
            day.add_line(line, asked=Asked(tid, day.generation))
            n += 1
        return n

    def _export(self, record: dict) -> None:
        if self.log is None:
            return
        from .looplag import LINES

        try:
            # Notices keep their numeric event time. The export timestamp
            # remains distinct so today's import can find them unchanged.
            stamp = "loggedAt" if "at" in record else "at"
            line = json.dumps({stamp: time.strftime("%Y-%m-%dT%H:%M:%S"), **record})
        except ValueError:
            return
        LINES.append(self.log, line)  # on the loop, by the writer thread, in order (looplag.LINES)
