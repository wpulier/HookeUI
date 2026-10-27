"""Admission: whether a researcher may start or run again, and at what effort (CONTEXT.md).

Big deliverables (a video, a lesson, a report, deep research) only when the user asked for one
in so many words (AGENTS.md): their last half minute of speech counts, not only the piece that
started it. The same rule holds for a new researcher, for "continue" (more of the same) and
for a new version of a video; a question about finished work, and a change to a lesson or a
picture's labels (the request names the change), are admitted. The selected effort is a
resource bound: incompatible deliverables are refused, never promoted to a larger budget.
`ResearchManager.start` and `followup` call `admit`, so no tool, studio
command or path can skip it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from research_worker.budget import EFFORTS

EXPLICIT = {
    "video": re.compile(r"\b(video|animat\w*|clip|movie|short film)\b", re.I),
    "lesson": re.compile(r"\b(lesson|interactive|tutorial|quiz|teach me|presentation|slides?|slideshow|explainer|"
                         r"walk ?through|walk me through|deck|worksheet)\b", re.I),
    "report": re.compile(r"\b(report|write[- ]?up|summary sheet)\b", re.I),
    "deep": re.compile(r"\b(deep|thorough\w*|in[- ]depth|dig into|properly research|research (it|this|that) properly)\b", re.I),
}
# The user asked for research (otherwise the brain started it on its own, and its result reaches the voice only as
# silent data: brain/narrator.py).
ASKED_FOR_RESEARCH = re.compile(r"\b(research\w*|look (it |this |that |them )?up|look into|find out|check|investigate|"
                                r"picture|diagram|image|label\w*|illustrat\w*|report|video|lesson|teach|learn|"
                                r"tell me more|explain more|more about|go deeper|dig into)\b", re.I)
# What the brain may choose. "report" is no longer offered (it ran as "research" and only added a quote to ask for);
# a caller or a saved record that still says it is read as research (admit), never refused for it.
DELIVERABLES = ("research", "annotated_image", "image", "video", "lesson")
INTERNAL = {"describe", "revise", "build"}  # efforts the app uses itself (descriptions, new versions, the builder)
CHOOSABLE = [e for e in EFFORTS if e not in INTERNAL]  # what the brain may ask for
ANSWER_EFFORT = "quick"  # a follow-up question about finished work
REVISE_EFFORT = "revise"  # a new version of one thing it made; a video is made again at standard
DELIVERY_TOOL = {"image": "generate_image", "annotated_image": "annotate_image",
                 "video": "make_video", "lesson": "make_lesson"}


class ResearchRefused(Exception):
    """Research that may not start or go on; the message says why, for the user, and `next` what they can do."""

    def __init__(self, message: str = "", next: str | None = None) -> None:
        super().__init__(message)
        self.next = next


class NeedsTheirWords(ResearchRefused):
    """A big deliverable the user has not asked for in their own words: the brain can propose it and ask them
    (brain/toolbox/research.py files the proposal; the user's yes confirms exactly that proposal)."""


@dataclass(frozen=True)
class Admitted:
    effort: str
    deliverable: str | None
    asked: bool  # the user asked for research in these words (not the brain on its own)


QUOTE_SHARE = 0.8  # of the quoted words' tokens that must appear, in order, in what the user said


NUMBERS = {w: str(n) for n, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                            "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}


def _tokens(text: str) -> list[str]:
    """Words for comparing a quote with what was said: one case, one apostrophe, numbers as digits (a transcript writes
    "two" where a quote writes "2", and a keyboard's ’ is the same as ')."""
    text = (text or "").lower().replace("\u2019", "'").replace("\u2018", "'")
    return [NUMBERS.get(t, t) for t in re.findall(r"[a-z0-9']+", text)]


def quoted(asked_with: str, words: str, least: int = 2) -> bool:
    """The model's quote of the user's request is really in what the user said (most of its words,
    in order; a transcript and a quote of it rarely match letter for letter). `least`: the fewest words a
    quote may have (a request for work: 2; an answer to a researcher's question may be just "yes")."""
    quote, said = _tokens(asked_with), _tokens(words)
    if len(quote) < least:
        return False
    i = found = 0
    for token in quote:
        try:
            i = said.index(token, i) + 1
            found += 1
        except ValueError:
            continue
    return found >= QUOTE_SHARE * len(quote)


# Words the studio composes for the user (the outline card's Look deeper, `notes.ts` DEEPER_TEXT) are the
# app's, not the user's: they never count as asking for a big deliverable, deep research or a standard run.
# A press of Look deeper authorizes a closer look or a quick researcher (the partner §1b, §2.7).
STUDIO_PHRASES = ("look into this one more closely",)


def users_own(words: str) -> str:
    """What the user said in their own words: `words` without the phrases the studio composed for them."""
    text = words or ""
    for phrase in STUDIO_PHRASES:
        text = re.sub(re.escape(phrase), " ", text, flags=re.I)
    return text


def explicit_refusal(words: str, effort: str, deliverable: str | None, asked_with: str = "", mode: str = "quote") -> str | None:
    """Why a researcher may not start without an explicit request, or None if it may.

    mode "quote" (the default; facts, not verdicts): the model judges whether the user asked for
    it and quotes the words (`asked_with`); the app checks the quote is in what the user said in the
    last half minute. mode "keywords": the request must match EXPLICIT (the rule before 2026-10-06,
    which refused "show me step by step how it works")."""
    own = users_own(words)
    if effort in ("standard", "deep") and (words or "").strip() and not own.strip():
        return ("a press of Look deeper allows a closer look or a quick researcher; a longer one starts when the user "
                "asks for it in their own words")
    words = own
    for kind in (deliverable, "deep" if effort == "deep" else None):
        if kind not in EXPLICIT:
            continue
        if mode == "keywords":
            if not EXPLICIT[kind].search(words or ""):
                return f"a {kind} researcher only starts when the user asks for one explicitly"
        elif not asked_with:
            return f"a {kind} researcher starts only when the user asked for one: quote their words in asked_with"
        elif not quoted(asked_with, words):
            return f'a {kind} researcher starts only when the user asked for one; "{asked_with}" is not what they said'
    return None


def admit(kind: str, *, words: str = "", effort: str | None = None, deliverable: str | None = None,
          target_kind: str | None = None, asked_with: str = "", mode: str = "quote", confirmed: bool = False) -> Admitted:
    """kind: "start" (a new researcher), "continue" (more of the same, at its own effort and
    deliverable), "revise" (a new version of `target_kind`: lesson, labelled picture,
    illustration, video), "answer" (a question about finished work) or "describe" (a label's
    description, enrich.py). Raises ResearchRefused with the reason."""
    # Whether the user asked for this research (its result is then spoken; otherwise the brain
    # started it on its own and only a significant result is). "quote": the model's quote checks out.
    if mode == "keywords":
        asked = bool(ASKED_FOR_RESEARCH.search(words or "") or any(rule.search(words or "") for rule in EXPLICIT.values()))
    else:
        asked = quoted(asked_with, words)
    if kind == "describe":
        return Admitted("describe", deliverable or "research", asked=False)
    if kind == "answer":
        return Admitted(ANSWER_EFFORT, "answer", asked=True)
    if kind == "revise":
        if target_kind == "video":
            why = explicit_refusal(words, "standard", "video", asked_with, mode)
            if why:
                raise ResearchRefused(why, next="ask for it in your own words")
            return Admitted("standard", "revise", asked=True)
        return Admitted(REVISE_EFFORT, "revise", asked=True)
    if deliverable == "adapter" or effort == "build":  # the builder (drafts.py): only on the studio's own press
        if not (confirmed and deliverable == "adapter" and effort == "build" and kind == "start"):
            raise ResearchRefused("an adapter is drafted only when you ask for one on a model's card", next=None)  # no next: the message names the user control required to request a draft
        return Admitted("build", "adapter", asked=True)
    if deliverable == "report":  # an older caller's or a saved record's word for research (never refused for it)
        deliverable = "research"
    if kind == "continue":
        # `confirmed`: the user's own answer to the researcher's question that proposed this (manager.carry_on).
        why = None if confirmed else explicit_refusal(words, effort or "quick", deliverable, asked_with, mode)
        if why:
            raise NeedsTheirWords(why, next="ask for it in your own words")
        require_delivery_tool(effort or "quick", deliverable)
        return Admitted(effort or "quick", deliverable, asked=True)
    if kind != "start":
        raise ResearchRefused(f"unknown kind of research {kind!r}", next=None)  # no next: the model's own call; it corrects it
    # A choice the model made, never replaced by another: an unknown one is refused with what can be chosen.
    if effort is not None and effort not in CHOOSABLE:
        raise ResearchRefused(f"effort is one of {', '.join(CHOOSABLE)}", next=None)  # no next: the model's own argument
    if deliverable is not None and deliverable not in DELIVERABLES:
        raise ResearchRefused(f"deliverable is one of {', '.join(DELIVERABLES)}", next=None)  # no next: the model's own argument
    effort, deliverable = effort or "quick", deliverable or "research"
    # `confirmed`: the user's yes to a proposal of exactly this (brain/toolbox/research.py, start_researcher confirms).
    why = None if confirmed else explicit_refusal(words, effort, deliverable, asked_with, mode)
    if why:
        raise NeedsTheirWords(why, next="ask for it in your own words")
    require_delivery_tool(effort, deliverable)
    return Admitted(effort, deliverable, asked=asked or confirmed)


def require_delivery_tool(effort: str, deliverable: str | None) -> None:
    """A format cannot silently buy a larger allowance or a tool outside its chosen effort."""
    tool = DELIVERY_TOOL.get(deliverable)
    if tool and tool not in EFFORTS[effort].tools:
        raise ResearchRefused(f"a {deliverable} cannot be made at {effort} effort; it has not started and the budget was not increased",
                              next="keep that effort with a written explanation, or ask for an effort that supports the format")
