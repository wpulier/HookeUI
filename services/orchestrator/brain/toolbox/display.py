"""How the app draws things, changed by asking ("make the outlines easier to see", "yellow
outlines"; asked live on 2026-10-06 and refused for want of this tool)."""
from __future__ import annotations

from ...options import OUTLINE_COLORS, STYLE
from ..registry import Registry, Tool, ToolContext, ToolFailed, schema


async def outline_style(services, color: str = "", style: str = "") -> list[str]:
    """The outlines' colour and style, set as the user asked: kept until changed (day/prefs.py), published to
    every studio, and done only once the studio's report shows it (one function for set_style and the
    present verbs; M1.5b review S9). Returns what was done, in words."""
    from ...screen import on_screen, screen_of

    hub = services.hub
    before = screen_of(hub).reported_at
    done = []
    if color:
        hub.settings.outline_color = color
        done.append(f"outlines are {color}")
    if style:
        hub.settings.style = style
        done.append(f"outlines are drawn {STYLE[style].label.lower()}")
    if services.day is not None:  # kept until changed, across restarts and days
        for key, value in (("outlineColor", color), ("style", style)):
            if value:
                services.day.set_pref(key, value)
    if services.publish_settings is not None:
        services.publish_settings()

    def shown(report: dict) -> bool:
        item = next((i for i in report.get("items", []) if i.get("id") == "outlines"), None)
        got = (item or {}).get("style") or {}
        return item is not None and (not color or got.get("colour") == color) and (not style or got.get("fill") == (style == "classic"))

    await on_screen(hub, shown, before, "the outlines' new look is not", "look at the studio, or ask again")
    return done


def add_display_tools(registry: Registry, services) -> None:
    hub = services.hub
    if services.publish_settings is None:
        return

    async def set_style(ctx: ToolContext, args: dict) -> str:
        color = str(args.get("color") or "").strip().lower()
        style = str(args.get("style") or "").strip().lower()
        if not color and not style:
            raise ToolFailed(f"which colour ({', '.join(OUTLINE_COLORS)}) or style ({', '.join(STYLE)})?",
                             next=None)  # no next: the message says what to do
        if color and color not in OUTLINE_COLORS:
            raise ToolFailed(f"outlines can be {', '.join(OUTLINE_COLORS)}, not {color}",
                             next=None)  # no next: the message says what to do
        if style and style not in STYLE:
            raise ToolFailed(f"outlines can be drawn {' or '.join(STYLE)}, not {style}",
                             next=None)  # no next: the message says what to do
        return "; ".join(await outline_style(services, color, style))

    registry.add(Tool("set_style", "Change how outlines are drawn: their colour, or the style (\"classic\": filled, on "
                      "the edge; \"edge\": a line just outside, no fill, so the thing's own edge stays visible). Use it "
                      "when the user asks for outlines that are easier to see, another colour, or no fill.",
                      schema({"color": {"type": "string", "description": " | ".join(OUTLINE_COLORS)},
                              "style": {"type": "string", "description": " | ".join(STYLE)}}),
                      set_style, inline_ms=100, family="display", sets=("style", "outlineColor")))
