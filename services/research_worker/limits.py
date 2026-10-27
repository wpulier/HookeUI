"""How much of what the user or a model wrote a researcher keeps: all of it, up to one generous limit, else the
input is refused with the reason, never cut (the pattern retrieval.py and presentation.py already follow: cutting a
request can erase the very constraint it ends with). Read by the app (orchestrator/research/) and the research
process alike; previews (roster lines, chips, the recap) shorten separately and say so (orchestrator/untrusted.inline).
"""
from __future__ import annotations

# One field the user or a model wrote: a brief, a question, a change, a note, an answer, a finding, a finish text
# field, a researcher's question. The same as one report section (results.full_report).
FIELD_MAX = 16000
# What a run is given once assembled (a continue's request, findings and open work; an answer's earlier work): the
# same as a presentation's input (research_worker/presentation.MAX_INPUT_CHARS). Whole parts are left out, by
# priority and each named, before the required ones are refused (orchestrator/research/manager._continuation).
BRIEF_MAX = 90000


class TooLong(ValueError):
    """Text over its limit: refused, with what it was and how long, never cut."""


def whole(text: object, what: str, limit: int = FIELD_MAX) -> str:
    """`text` as it was written, or TooLong naming `what` and both lengths."""
    value = str(text or "")
    if len(value) > limit:
        raise TooLong(f"{what} is {len(value):,} characters; at most {limit:,} are kept whole, and none is cut")
    return value


# Seconds of the live view the app holds for a researcher's question (orchestrator/held.py reads it): what the
# request_evidence schema states and the worker clamps to are this one number.
HOLD_MAX_S = 60
