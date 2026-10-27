"""A run (CONTEXT.md): one stretch of a researcher working again on finished work.

A researcher's first run (and "continue", which carries on the same work) is its own status
and result. A follow-up answer or a new version runs on top of finished work: the work, its
result and its status stay as they were, and what the run made is recorded in its history.
`Run` holds that run while it lasts (what it is, the status to go back to, its effort and
brief); `finish` turns it into its history entry, which the announcer reads (`Session.last`).

Before, five fields (asking, revising, answered, revised, status_before) had to agree in eleven
places; a revise cut by a restart, and a note sent during a revise, were lost between them.
Saved researchers keep the old keys (`answering`, `revising`), so older days load.
"""
from __future__ import annotations

import time
import copy
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Delivery:
    """Transient request ownership, never part of a researcher's wire or saved facts.

    A missing owner remains unknown; it is never resolved from the current voice.
    """
    asked: object = None
    day: str | None = None
    voice_owner: object = field(default=None, repr=False, compare=False)
    client: object = field(default=None, repr=False, compare=False)
    request_snapshot: object = field(default=None, repr=False, compare=False)
    request_id: str | None = None


@dataclass
class Run:
    kind: str  # "answer" (a question about its work) or "revise" (a new version of one thing it made)
    status_before: str  # what its status goes back to when the run ends
    effort: str  # the run's effort (research_worker/budget.py)
    brief: str  # what the worker is asked (an answer: the question with the earlier work; a revise: the change)
    question: str = ""  # an answer: the user's question
    target: dict | None = None  # a revise: the output it makes a new version of ({id, kind, path, ...content})
    kind_name: str = ""  # a revise: what that is ("lesson", "labelled picture", "illustration", "video")
    started: float = field(default_factory=time.time)

    @property
    def change(self) -> str:
        return self.brief if self.kind == "revise" else ""

    def wire(self) -> dict:
        """What the studio and the saved session show of it (the keys saved before runs existed)."""
        return {"answering": self.question if self.kind == "answer" else None,
                "revising": {"target": (self.target or {}).get("id"), "kind": self.kind_name, "change": self.change, **self.edit_scope()}
                if self.kind == "revise" else None}

    def edit_scope(self) -> dict:
        target = self.target or {}
        return ({'edit_fields':copy.deepcopy(target['edit_fields']), 'anchor':target.get('anchor')}
                if target.get('edit_fields') is not None else {})

    def line(self) -> str:
        """What it is doing, for the brain's line about the researcher."""
        from ..untrusted import inline

        if self.kind == "revise":
            return f'making a new version of its {self.kind_name} ("{inline(self.change, 80)}")'
        return f'answering a follow-up question ("{inline(self.question, 80)}")'

    def worker_job(self) -> dict:
        """What the worker is given for this run."""
        if self.kind == "revise":
            return {"brief": self.change, "deliverable": "revise",
                    "revise": {"kind": self.kind_name, "change": self.change, "target": self.target}}
        return {"brief": self.brief, "deliverable": "answer", "revise": None}

    def finish(self, status: str, result: dict | None, made: list[dict]) -> dict:
        """The history entry for this run (`made`: the outputs it emitted)."""
        at = round(time.time(), 2)
        if self.kind == "answer":
            return {"brief": self.question, "mode": "answer", "status": status, "result": result, "at": at}
        new = next((o for o in reversed(made) if o.get("replaces")), None)
        outcome = "done" if new is not None else "failed" if status == "done" else status
        return {"mode": "revise", "target": (self.target or {}).get("id"), "kind": self.kind_name, "change": self.change,
                **self.edit_scope(),
                "status": outcome, "version": new.get("version") if new else None, "output": new.get("id") if new else None,
                "at": at}


def interrupted(saved: dict) -> dict | None:
    """A run that was going when the app stopped, as its history entry (from a saved session)."""
    at = round(time.time(), 2)
    if saved.get("answering"):
        return {"brief": str(saved["answering"]), "mode": "answer", "status": "interrupted", "result": None, "at": at}
    revising = saved.get("revising")
    if isinstance(revising, dict):
        return {"mode": "revise", "target": revising.get("target"), "kind": revising.get("kind"),
                **{key:copy.deepcopy(revising[key]) for key in ('edit_fields', 'anchor') if key in revising},
                "change": revising.get("change"), "status": "interrupted", "version": None, "output": None, "at": at}
    return None
