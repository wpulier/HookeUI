"""The result viewer: which researcher's work is up on screen, beside the live view.

State lives here (not only in the studio) so the brain can open and close results from voice
or chat ("show me the lesson", "close that"), and knows what the user is looking at. The
studio draws it from the research sessions it already has and reports the user's own
opening and closing back.

A result the user asked for opens itself when it is ready; others only light up on the shelf.
A chip the user removed from the shelf stays off it for the day (kept in the day's journal, so a
reload or a restart does not bring it back); its work stays in the Research tab, and asking for it
("show me the lesson") opens it beside the view as ever.

Each item says what kind of thing it is (media.py: an AI illustration, a labelled photo of the slide, a
reference photo from the web...), on its tab and in a line above it, and what the researcher was asked to
make is what opens first (`first_shown`): an illustration it made on its own never opens ahead of the
written result it was asked for (kind (f) of the end of the effort).
"""
from __future__ import annotations

from typing import Callable
import json
import weakref

from research_worker.results import step_kind

from . import media, quiz, versions
from .untrusted import inline
from .versions import KIND_ORDER  # noqa: F401 (kept importable from here)

TAB_LABELS = {"html": "Lesson", "video": "Video", "image": "Picture", "summary": "What it found", "presentation": "Presentation"}
REFS_LISTED = 4  # a researcher's reference photos listed as items of their own (it keeps at most a few)
# What each deliverable asked for, as the item that answers it: (media, output kind). Research and a report
# are answered by the written result.
ASKED = {"image": (media.GENERATED, "image"), "annotated_image": (media.LABELLED, "image"),
         "video": (media.GENERATED, "video"), "lesson": (media.LESSON, "html")}
SHELF_REMOVED = "shelfRemoved"  # the day's preference: researcher ids taken off the shelf


def tab_label(kind: str | None, version: int = 1, current: bool = True, kind_of: str | None = None) -> str:
    """What the studio's result viewer calls an item on its tab: "Lesson", "Lesson v2" (a
    revised one), "Lesson v1" (one a revision replaced), "AI video", "AI illustration", "Labelled
    photo", "What it found". `kind_of`: what kind of picture it is (media.py), when it is one."""
    versioned = version > 1 or not current
    word = media.label(kind_of, kind) if kind_of else TAB_LABELS.get(kind or '', 'Result')
    return f"{word}{f' v{version}' if versioned else ''}"


def items(session) -> list[dict]:
    """What a researcher made: the current version of each thing, most substantial first (the
    newest first within a kind), then older versions (versions.ordered, the numbering read_result
    and show_result use), then the reference photos it found, then its written result. Each says what
    kind of thing it is (`media`, `line`). The studio's result viewer shows these, in this order,
    from Session.wire()["items"]."""
    out = []
    for o, now in versions.ordered(session.outputs):
        kind_of = media.of_output(o)
        out.append({"kind": o.get("kind"), "path": o.get("path"), "caption": o.get("caption", ""), "id": o.get("id"),
                    "version": o.get("version") or 1, "change": o.get("change"), "current": now,
                    "media": kind_of, "line": media.line(kind_of, o.get("kind")),
                    "label": tab_label(o.get("kind"), o.get("version") or 1, now, kind_of=kind_of)})
    refs = [r for r in ((session.result or {}).get("refs") or []) if isinstance(r, dict) and r.get("path")][:REFS_LISTED]
    for n, ref in enumerate(refs):
        credit = str(ref.get("credit") or "")
        out.append({"kind": "reference", "ref": n, "path": ref.get("path"), "caption": ref.get("title") or "Reference photo",
                    "credit": credit, "media": media.REFERENCE, "line": media.line(media.REFERENCE, credit=credit),
                    "label": media.label(media.REFERENCE) + (f" {n + 1}" if len(refs) > 1 else "")})
    if session.result:
        out.append({"kind": "summary", "caption": session.result.get("title") or "What it found",
                    "media": media.WRITTEN, "line": media.line(media.WRITTEN), "label": tab_label("summary")})
    for item in out:
        item["work"] = {"researcherId": session.rid, "resource": {
            "day": session.day, "id": item.get("id") or (f"ref-{item['ref']}" if "ref" in item else "summary"),
            "version": item.get("version") if item["kind"] not in ('summary', 'reference') else
                ((session.result or {}).get("findingRevision") or {}).get("version")}}
    return out


def first_shown(session) -> int:
    """Which item opens first: the one answering what the researcher was asked to make (its deliverable), the
    newest current one; for research or a report, its written result (what it made on its own, an illustration
    say, is a tab away, never ahead of it). Else the first item."""
    listed = items(session)
    want = ASKED.get(getattr(session, "deliverable", None) or "")
    if want is None:
        basis = (session.result or {}).get('findingRevision', {}).get('digest')
        current = {o.get('id') for o, current in versions.ordered(session.outputs) if current and o.get('kind') == 'presentation'
                   and basis and o.get('findingRevision', {}).get('digest') == basis}
        ready = next((i for i, item in enumerate(listed) if item.get('id') in current), None)
        if ready is not None:
            return ready
    for i, it in enumerate(listed):
        if want is not None and it.get("current", True) and (it.get("media"), it.get("kind")) == want:
            return i
        if want is None and it.get("kind") == "summary":
            return i
    return 0


def index_of(session, output_id: str | None) -> int | None:
    """Where an output is among items() (for Presenter.show), or None."""
    return next((i for i, it in enumerate(items(session)) if output_id and it.get("id") == output_id), None)


def session_steps(session) -> list[dict]:
    """A lesson's steps (questions, with answers and hints) as the researcher made them:
    research/manager.Session.steps(), [] when there are none."""
    fn = getattr(session, "steps", None)
    try:
        return [st for st in (fn() if callable(fn) else []) if isinstance(st, dict)]
    except Exception:
        return []


def find_step(steps: list[dict], current: str | None, to, outcomes: dict | None = None) -> int:
    """Index of the step `to` means: "next", "prev"/"back", "first", "again", a step id ("q2"),
    a number ("2", 2), or "missed" (the next step the user got wrong or skipped: quiz.py, from
    `outcomes`). Raises ValueError when there is none."""
    ids = [str(st.get("id") or f"q{i + 1}") for i, st in enumerate(steps)]
    at = ids.index(current) if current in ids else -1
    text = str(to if to is not None else "next").strip().lower()
    if text in ("missed", "miss", "wrong", "missed ones"):
        return quiz.next_missed(steps, current, outcomes or {})
    if text in ("next", "", "continue", "on"):
        i = at + 1
    elif text in ("prev", "previous", "back"):
        i = max(0, at - 1)
    elif text in ("first", "start", "beginning"):
        i = 0
    elif text in ("again", "same", "current", "repeat"):
        i = max(0, at)
    elif text in ids:
        i = ids.index(text)
    else:
        digits = "".join(c for c in text if c.isdigit())
        if not digits:
            raise ValueError(f"I don't know which step {to!r} is")
        i = int(digits) - 1
    if not 0 <= i < len(steps):
        raise ValueError("that was the last one" if i >= len(steps) else "there is no such step")
    return i


def describe(session, index: int) -> str:
    listed = items(session)
    if not listed:
        return f"{session.name}'s result"
    item = listed[max(0, min(index, len(listed) - 1))]
    kind = {"summary": "written result"}.get(item["kind"]) or media.noun(item.get("media"), item["kind"])
    if item["kind"] != "summary" and (item.get("version", 1) > 1 or not item.get("current", True)):
        kind += f", version {item.get('version', 1)}" + ("" if item.get("current", True) else " (an older version)")
    return f"{session.name}'s {kind} ({inline(item['caption'], 60)})"


class Presenter:
    def __init__(self, hub, research, outcomes: Callable[[str], dict] | None = None,
                 prefs: tuple[Callable[[str], object], Callable[[str, object], None]] | None = None) -> None:
        self.hub = hub
        self.research = research
        self.outcomes = outcomes or (lambda rid: {})  # rid -> {step: right | wrong | skipped} today (day/store.py)
        memory: dict = {}
        # The day's preferences (SessionStore.prefs / set_pref): where the shelf's removed chips are kept.
        self._get_pref, self._set_pref = prefs or (memory.get, memory.__setitem__)
        self._clients = weakref.WeakKeyDictionary()
        self._recipient = None
        self.open = False
        self.rid: str | None = None
        self.index = 0
        self.output_id: str | None = None
        self.finding_revision: dict | None = None
        self.step: str | None = None  # the lesson step being gone through ("q2"), if any
        self.saved_session = None  # detached exact-version reading; never inserted into today's research store

    @property
    def index(self):
        output_id = getattr(self, 'output_id', None)
        if output_id and getattr(self, 'rid', None):
            session = self.saved_session or self.research.get(self.rid)
            found = index_of(session, output_id) if session else None
            if found is not None:
                return found
        return self._index

    @index.setter
    def index(self, value):
        self._index = value

    def wire(self) -> dict:
        current = self.current_step()
        return {"type": "presenter", "open": self.open, "rid": self.rid, "index": self.index, "step": self.step,
                'outputId':self.output_id, 'findingRevision':self.finding_revision,
                "stepKind": step_kind(current[1]) if current else None,
                "outcomes": self.outcomes(self.rid) if self.rid and self.saved_session is None else {}, "shelfRemoved": self.removed(),
                **({'savedSession': self.saved_session.wire()} if self.saved_session is not None else {})}

    # --- the shelf ----------------------------------------------------------------------------------
    def removed(self) -> list[str]:
        """Researchers whose chip the user took off the shelf today (oldest first)."""
        return list(self._get_pref(SHELF_REMOVED) or [])

    def remove_from_shelf(self, rid: str) -> None:
        if rid not in self.removed():
            self._set_pref(SHELF_REMOVED, [*self.removed(), rid][-200:])
            self.hub.broadcast({**self.wire(), "shelfOnly": True})

    def back_on_shelf(self, rid: str) -> None:
        if rid in self.removed():
            self._set_pref(SHELF_REMOVED, [r for r in self.removed() if r != rid])
            self.hub.broadcast({**self.wire(), "shelfOnly": True})

    def current_step(self):
        """(session, step) for the lesson step being gone through, or None."""
        if not self.open or self.rid is None or self.step is None or self.saved_session is not None:
            return None
        session = self.research.get(self.rid)
        if session is None:
            return None
        if self.output_id and self.output_id not in {o.get('id') for o in session.current_outputs()}:
            return None
        step = next((st for i, st in enumerate(session_steps(session)) if quiz.step_id(st, i) == self.step), None)
        return (session, step) if step is not None else None

    def outcome_changed(self, rid: str) -> None:
        """A step of `rid`'s lesson was marked (quiz.py): the studio shows how the quiz stands."""
        if rid == self.rid:
            self._publish()
        for view in list(self._clients.values()):
            view.outcome_changed(rid)

    def for_client(self, client):
        """Independent viewer state for an accepted browser connection."""
        if client not in self._clients:
            view = Presenter(self.hub, self.research, self.outcomes,
                             (self._get_pref, self._set_pref))
            view._recipient = weakref.ref(client)
            self._clients[client] = view
        return self._clients[client]

    def _publish(self) -> None:
        if self._recipient is not None:
            client = self._recipient()
            if client is not None:
                client.offer(json.dumps(self.wire()))
        else:
            self.hub.broadcast(self.wire())

    def show(self, rid: str, index: int | None = None) -> str:
        session = self.research.get(rid)
        if session is None:
            raise KeyError(rid)
        if not items(session):
            raise KeyError(f"{rid} has nothing to show")  # never open an empty panel
        self.saved_session = None
        if rid != self.rid:
            self.step = None
        self.open, self.rid = True, rid
        self.output_id = None
        self.index = first_shown(session) if index is None else max(0, min(int(index), max(0, len(items(session)) - 1)))
        self._pin(session)
        self._publish()
        return describe(session, self.index)

    def show_saved(self, day: str, rid: str, output_id: str, version: int, index: int = 0) -> str:
        """Exact retained media in this browser, including earlier days and non-current outputs."""
        import copy
        session = self.research.get(rid)
        if session is None or session.day != day:
            session = next((row for row in self.research.read_day(day) if row.rid == rid), None)
        output = session.output(output_id) if session is not None else None
        if output is None or output.get('version', 1) != version:
            raise KeyError('the exact saved output is unavailable')
        # Current lessons retain their established journaled navigation and outcomes.
        # Older versions/days stay detached and cannot write today's quiz state.
        if (session is self.research.get(rid) and session.day == self.research.today()
                and output_id in {o.get('id') for o in session.current_outputs()} and index == 0):
            return self.show(rid, index_of(session, output_id))
        saved = session.completed_view()
        saved.outputs = [copy.deepcopy(output)]
        basis = output.get('findingRevision')
        if basis:
            record = next((row for row in saved.finding_revisions if row['version'] == basis['version'] and row['digest'] == basis['digest']), None)
            if record is None:
                raise KeyError('the output’s saved findings basis is unavailable')
            saved.result = {**copy.deepcopy(record['result']), 'findingRevision':copy.deepcopy(basis)}
        if type(index) is not int or not 0 <= index < len(items(saved)):
            raise KeyError('the saved presentation item is unavailable')
        self.saved_session = saved
        self.output_id = None
        self.open, self.rid, self.index, self.step = True, rid, index, None
        self._pin(saved)
        self._publish()
        return describe(saved, index)

    def go_to_step(self, rid: str, to) -> tuple[dict, int, int]:
        """Open the researcher's lesson on a step ("next", "q2", 3...): the studio scrolls the
        lesson there. Returns (step, its index, how many). Raises KeyError / ValueError."""
        if self.saved_session is not None and self.rid == rid:
            raise ValueError('this exact saved lesson is read-only; open today’s current lesson to record outcomes')
        session = self.research.get(rid)
        if session is None:
            raise KeyError(rid)
        if self.rid == rid and self.output_id and self.output_id not in {o.get('id') for o in session.current_outputs()}:
            raise ValueError('the selected lesson is older; open its current version to record outcomes')
        steps = session_steps(session)
        if not steps:
            raise ValueError(f"{session.name}'s work has no steps to go through")
        i = find_step(steps, self.step if rid == self.rid else None, to, self.outcomes(rid))
        listed = items(session)
        lesson = next((n for n, it in enumerate(listed) if it["kind"] == "html" and it.get("media") == media.LESSON), self.index if rid == self.rid else 0)
        if rid != self.rid:
            self.step = None
            self.saved_session = None
        self.open, self.rid, self.index = True, rid, lesson
        self.output_id = None
        self._pin(session)
        self.step = str(steps[i].get("id") or f"q{i + 1}")
        self._publish()
        return steps[i], i, len(steps)

    def show_retained_item(self, day, rid, work):
        """Select a tab within this browser's frozen reading, without resolving today's list."""
        session = self.saved_session
        if session is None or session.day != day or session.rid != rid:
            raise ValueError('the saved reading is no longer open; reopen its exact saved work')
        selected = next((i for i, item in enumerate(items(session)) if item['work'] == work), None)
        if selected is None:
            raise ValueError('that item is not part of this saved reading')
        self.open, self.rid, self.output_id = True, rid, None
        self.index = selected
        self.step = None
        self._pin(session)
        self._publish()
        return describe(session, selected)

    def _pin(self, session):
        import copy
        item = items(session)[self.index]
        self.output_id = item.get('id')
        output = session.output(self.output_id) if self.output_id else None
        self.finding_revision = (copy.deepcopy((output or {}).get('findingRevision') or (session.result or {}).get('findingRevision'))
                                 if output or item['kind'] == 'summary' else None)
        if item['kind'] == 'reference' and self.saved_session is None:
            self.saved_session = session.completed_view()

    def reset_all(self) -> None:
        """Day changes close every browser's old lesson, without sharing viewer state."""
        self.close()
        for view in list(self._clients.values()):
            view.close()

    def close(self) -> None:
        if self.open:
            self.open = False
            self._publish()

    def state_text(self, answers: bool = True) -> str:
        """What is up, in a line; when going through a lesson, which step and its question
        (the researcher's words: information only). The answer only for the brain, so the
        voice cannot give it away before the user has tried."""
        if not self.open or self.rid is None:
            return ""
        session = self.saved_session or self.research.get(self.rid)
        if session is None:
            return ""
        text = f"showing {describe(session, self.index)} beside the live view"
        steps = session_steps(session)
        ids = [str(st.get("id") or f"q{i + 1}") for i, st in enumerate(steps)]
        done = self.outcomes(self.rid)
        how = f" (how the user did today: {quiz.tally(steps, done)})" if steps and done else ""
        if self.step in ids:
            i = ids.index(self.step)
            st = steps[i]
            text += (f", on step {i + 1} of {len(steps)} (the researcher's question, information only: "
                     f"\"{inline(st.get('prompt'), 240)}\"")
            if answers and st.get("answer"):
                text += f"; its answer: \"{inline(st['answer'], 200)}\""
            text += ")" + how
            # A find or evidence step (quiz.py): how it is answered, and for the brain its facts.
            kind = quiz.step_line(st, self.hub, session, brief=not answers)
            if kind:
                text += f". {kind}" if answers else f", {kind}"
            if answers:
                text += " (once you have judged their answer, record it with mark_step)"
        elif steps:
            text += f" ({len(steps)} steps; not started)" + how
        return text
