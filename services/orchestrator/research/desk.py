"""The desk: finding a researcher the way people refer to one, and reading its work.

"Researcher 3", "the quiz", "the one on screen", "the joint one": `find` resolves a reference
to one researcher, or asks which one ("Do you mean ... or ...?") when two fit about as well and
neither is on screen. Numbers name only researchers (never the brain's own corrections or a
label's description), and only the session open's: each session counts its own from 1 (ADR 0017). `read` gives a researcher's work as text that is safe in a template
(untrusted.py); `named` and `made` say what it made in the app's own words.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .policy import ResearchRefused

from .. import versions
from .manager import ACTIVE

ON_SCREEN = re.compile(r"\b(this|that|it|the one (on screen|open|up)|on screen|open|current|showing)\b", re.I)
# Words that say which one only by pointing at the screen ("this one", "the one open"): never a description of it.
POINTING = {"this", "that", "it", "on", "screen", "open", "up", "current", "showing"}
STOP_WORDS = {"the", "a", "an", "one", "about", "on", "of", "researcher", "researchers", "work", "result", "made", "that",
              "this", "it", "s", "and", "with", "for"}


class Ambiguous(ResearchRefused):
    """Two researchers fit a reference about as well: the message is the question to ask."""


def made(session) -> str:
    """What it made: "lesson", "video", "picture" or "notes" (from its outputs, else its deliverable: versions.made)."""
    return versions.made(getattr(session, "outputs", None) or [], getattr(session, "deliverable", None))


def named(session) -> str:
    """"Researcher 8's lesson on stomata", from the app's own words (its label or brief)."""
    from ..untrusted import inline

    about = inline(session.label or session.brief, 40)
    return f"{session.name}'s {made(session)}" + (f" on {about}" if about else "")


class Desk:
    def __init__(self, manager, shown: Callable[[], str | None] = lambda: None) -> None:
        self.manager = manager
        self.shown = shown  # the researcher whose work is open beside the view, if any

    def researchers(self) -> list:
        """The session open's researchers (manager.researchers)."""
        return self.manager.researchers()

    def working(self) -> list:
        return [s for s in self.researchers() if s.status in ACTIVE]

    def find(self, ref: Any = None):
        """A researcher from how people refer to one: its number ("2", "researcher 2") or id; "the
        one on screen"; what it made ("the quiz", "the video"); or words of its subject ("the
        joint one"). Nothing given: the one on screen, else the latest. Raises Ambiguous when two
        fit about as well and neither is on screen, ResearchRefused when none fits."""
        text = str(ref or "").strip()
        research = self.researchers()
        m = re.fullmatch(r"(?:researcher\s*)?(\d+)", text, re.I) or re.search(r"\bresearcher\s*#?\s*(\d+)\b", text, re.I)
        # "Researcher 3's lesson", "the researcher 3 one": the number decides (an answer to "Do you
        # mean Researcher 3's ... or Researcher 5's ...?" must settle it).
        if m:
            session = next((s for s in research if s.n == int(m.group(1))), None)
        else:
            # By its id also one of another session still in memory: what may be done with it is the manager's to say
            # ("Researcher 2 belongs to …; switch to it to continue").
            session = next((s for s in research if s.rid == text), None) or next(
                (s for s in list(getattr(self.manager, "sessions", {}).values())
                 if s.rid == text and s.purpose == "research"), None)
        rid = self.shown()
        shown = next((s for s in research if s.rid == rid), None) if rid else None
        words = {w for w in re.findall(r"[a-z]+", text.lower()) if w not in STOP_WORDS | POINTING}
        # "This one", "the one open": the one on screen. A description ("the video showing mitosis") is matched by
        # what it says, never overruled by a pronoun or "showing" inside it.
        if session is None and (not text or (ON_SCREEN.search(text) and not words)) and shown is not None:
            session = shown
        if session is None and text and not m and words:

            def kind_words(s) -> int:
                made_kinds = versions.kinds_made(s.outputs or [], s.deliverable)
                return len(words & set().union(*(versions.KIND_WORDS[k] for k in made_kinds if k in versions.KIND_WORDS)))

            def score(s) -> int:  # whole words of its subject: "art" never matches "heart"
                about = set(re.findall(r"[a-z]+", " ".join([s.label or "", s.brief or "", (s.result or {}).get("title") or ""]).lower()))
                return 3 * kind_words(s) + sum(1 for w in words if len(w) > 2 and w in about)

            ranked = sorted(research, key=lambda s: (score(s), s.created), reverse=True)
            if ranked and score(ranked[0]) > 0:
                session = ranked[0]
                # Matched by words, not named by number or id: code chose for the model, so it is logged as such.
                note = getattr(self.manager, "_log", None)
                if callable(note):
                    note(session, {"kind": "override", "what": "researcher by words", "words": text})
                close = [s for s in ranked[1:] if score(s) > 0 and (score(s) == score(session) or (
                    score(session) - score(s) == 1 and kind_words(s) != kind_words(session)))]
                if close and shown not in [session, *close]:
                    # Two fit about as well (the same words, or one by what it made and the other
                    # by its subject): ask rather than guess. The one on screen wins a tie.
                    raise Ambiguous(f"Do you mean {named(session)} or {named(close[0])}?", next=None)  # no next: the message is the question to answer
                if close and shown is not None:
                    session = shown
        if session is None and (not text or (ON_SCREEN.search(text) and not words)):
            session = research[-1] if research else None
        if session is None:
            raise ResearchRefused(f"there is no researcher {text or 'yet'}", next="say which researcher, or look in the Research tab")
        return session

    def find_working(self, ref: Any = None):
        """The researcher a note is for: the one named, else the only one working; two working
        and none named is asked about, never guessed."""
        working = self.working()
        if not ref and len(working) > 1:
            raise Ambiguous(f"Do you mean {named(working[0])} or {named(working[1])}?", next=None)  # no next: the message is the question to answer
        if not ref and len(working) == 1:
            return working[0]
        return self.find(ref)

    def read(self, ref: Any = None, part: str | None = None, limit: int = 2500) -> tuple[Any, str]:
        """The researcher and its work as text (Session.readable: safe in a template, still to be
        wrapped as untrusted in a prompt)."""
        session = self.find(ref)
        return session, session.readable(part, limit=limit)
