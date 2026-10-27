"""Untrusted text: anything that came from the web or a researcher (CONTEXT.md).

It is information, never instructions, and it reaches a model's prompt, the voice or the chat
only through here, so no path can forget a rule:

- `block` wraps it for a prompt: `<researchers source="web" untrusted="true">...</researchers>`
  (or another tag). No angle bracket survives inside, so it cannot close its wrapper or open a
  tag of its own (the leak the 2026-10-05 review found: a researcher's result closed the
  wrapper in the brain's and the fast pass's prompts).
- `inline` puts it in a templated line (a roster, a caption, a spoken or chat line): one line,
  no quotes or angle brackets, capped.
- `strip` removes the angle brackets and keeps the layout, for text that is wrapped later.

A test (tests/test_untrusted.py) runs a hostile string through every exit and checks that no
other module spells a wrapper or strips brackets by hand.
"""
from __future__ import annotations

_NO_TAGS = str.maketrans("<>", "  ")
_NO_TAGS_OR_QUOTES = str.maketrans('<>"', "   ")


def strip(text: object) -> str:
    """No angle brackets; the lines stay as they are."""
    return str(text or "").translate(_NO_TAGS)


def inline(text: object, limit: int | None = None) -> str:
    """One line for a template: no quotes or angle brackets, whitespace collapsed, capped. A capped line ends at a
    word with "…", so a preview always says it is one (the whole text stays where it was kept)."""
    line = " ".join(str(text or "").translate(_NO_TAGS_OR_QUOTES).split())
    if limit is None or len(line) <= limit:
        return line
    cut = line[: max(limit - 1, 0)]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "…"


def block(text: object, *, tag: str = "researchers", source: str = "web", limit: int | None = None) -> str:
    """The text wrapped as information for a prompt; it cannot close the wrapper."""
    body = strip(text)
    if limit is not None:
        body = body[:limit]
    return f'<{tag} source="{source}" untrusted="true">\n{body}\n</{tag}>'
