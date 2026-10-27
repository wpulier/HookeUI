"""What the user's words are is the model's judgment; code only carries it out (AGENTS.md "Act
only when asked"; the user's direction, 2026-10-06: code supplies facts, the model decides).

The fast pass says what the words are (`intent`):
- **request:** asks the app to do something;
- **question:** asks something; it points only if the model chose to;
- **statement:** a remark, thinking aloud;
- **filler:** "okay", "um";
- **unfinished:** cut off mid-sentence, more is coming;
- **cancel:** calls off what was just asked ("never mind", "wait, no").

Code acts on that and nothing else:
- only a request or a question keeps the actions the model gave (`allowed`);
- unfinished words are joined with what comes next (brain/speech.py);
- a cancel calls off what was about to be done.

There are no keyword lists or word counts deciding instead of the model. They were wrong in
ways the model is not: "wait for it to settle, then zoom" contains "wait", and "Okay" was never
sent to the model at all.

What stays in code is the user's own setting: while the user leads, the model is told so
(fast.LEAD, context.LEAD_TABLE), and pointing tools run only for a request, the user's own ask.
Every place code still overrides the model writes an `override` log line (`log_override`), so
how often it happens, and whether it was right, can be measured.
"""
from __future__ import annotations

from typing import Callable

ACTS = ("request", "question")  # intents whose actions run
WAIT = "unfinished"  # the words stop mid-sentence: wait for the rest
CANCEL = "cancel"  # the words call off what was just asked
POINTING = ("highlight", "select")


def allowed(actions: list[tuple[str, dict]], *, intent: str = "request", user_leads: bool = False,
            point_only: bool = False) -> list[tuple[str, dict]]:
    """The actions the model's own judgment allows: a request's or a question's, as it gave them;
    none for a statement, a filler, unfinished words or a cancel. While the user leads, pointing
    only for a request (the user asked for it). point_only: only pointing (the voice pointing
    along with an answer)."""
    if intent and intent not in ACTS:
        return []
    if user_leads and intent != "request":
        actions = [action for action in actions if action[0] not in POINTING]
    if point_only:
        actions = [action for action in actions if action[0] in POINTING]
    return actions


def log_override(log: Callable[[dict], None] | None, rule: str, **detail) -> None:
    """Code decided something the model did not: one line in the interaction log, to measure."""
    if log is not None:
        try:
            log({"kind": "override", "rule": rule, **detail})
        except Exception:
            pass
