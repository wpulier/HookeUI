"""How the user wants things explained (CONTEXT.md "Level"): the user's preference, told to every model
that writes for the user as one plain line of context.

- `level`: "simple" (everyday words; the default, closest to the prompts' own "short, friendly
  sentences") or "technical" (the proper terms, the basics not explained).
- `hintsFirst`: a hint before the answer when the user asks what something is or goes through a
  quiz or lesson step.

Both are set by the user (the studio's settings, or by voice or chat: tool `set_level`, which the
model calls when it judges the user asked; no keyword rules) and kept until changed in the user's
preferences (day/prefs.py), like who leads. Nothing rewrites or filters an answer for them: `line` is the
only thing that changes, and the same line goes to the brain and the fast pass (brain/context.py),
the voice's recap and view notes, researchers and their lessons and description cards
(research/manager.py snapshot, research_worker), and a blind A/B arm (arena.py).
"""
from __future__ import annotations

LEVELS = ("simple", "technical")
DEFAULT_LEVEL = "simple"
PREFS = ("level", "hintsFirst")  # the preferences (day/prefs.py) these are kept in

WORDS = {
    "simple": ("The user's level (their choice; it changes only how you word answers, never which tools you use): "
               "simple, in everyday words; a scientific term with its plain meaning."),
    "technical": ("The user's level (their choice; it changes only how you word answers, never which tools you use): "
                  "technical, the proper scientific terms without explaining the basics."),
}
HINTS = (" Hints first: when they ask what something is, or on a quiz or lesson step, the answer gives a hint and lets "
         "them try before naming it (outline it unnamed); name it when they ask or have tried.")


def line(level: str | None = DEFAULT_LEVEL, hints_first: bool = False) -> str:
    """The one line every prompt gets about the user's level (and hints first, when on)."""
    return WORDS.get(level or DEFAULT_LEVEL, WORDS[DEFAULT_LEVEL]) + (HINTS if hints_first else "")


def of(settings) -> str:
    """The line for the app's settings now (options.Settings, or anything with the same fields)."""
    return line(getattr(settings, "level", DEFAULT_LEVEL), bool(getattr(settings, "hints_first", False)))
