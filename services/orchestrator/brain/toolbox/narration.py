"""The narrator's tool: one suggestion, said later at a pause."""
from __future__ import annotations

from ..registry import Registry, Tool, ToolContext, ToolFailed, schema


def add_narration_tools(registry: Registry, services) -> None:
    from ..narrator import Line

    narrator = services.narrator
    if narrator is None:
        return

    async def suggest_next(ctx: ToolContext, args: dict) -> str:
        text = " ".join(str(args.get("text") or "").split())[:200]
        if not text:
            raise ToolFailed("nothing to suggest", next=None)  # no next: the model's own call; it corrects it
        narrator.offer(Line(text, "suggestion"))
        return "suggestion queued for the next pause"

    registry.add(Tool("suggest_next", "Rarely: offer one next step, said later at a pause, only when you notice "
                      "something on screen the user has not asked about and would want to know (for example: \"There's a "
                      "second one at the top; want me to compare them?\"). Never instead of doing what the user asked, "
                      "and not for ordinary questions.", schema({"text": {"type": "string"}}, ["text"]),
                      suggest_next, inline_ms=100, family="conversation"))

