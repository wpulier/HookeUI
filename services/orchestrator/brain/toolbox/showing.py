"""Show me (orchestrator/showing.py; the partner §2.3, ADR 0015): `show_me` starts a showing of a
researcher's finding and gives the brain the session's records it rests on, each with how it is shown;
the brain then shows them with `present`, as beats the showing owns. `show_me(stop=true)` ends it and puts back
only what it still owns (`show_me` with stop: one tool line, not two). The finding's own words reach the
brain only inside the untrusted wrapper."""
from __future__ import annotations

from ...research.policy import ResearchRefused
from ...untrusted import block, inline
from ..registry import Registry, Tool, ToolContext, ToolFailed, schema
from .common import RESEARCHER


def evidence_lines(result: dict) -> list[str]:
    """Each of the finding's records as the brain is told it: open it by its id, or why it can't be shown."""
    from ...screen import presented

    lines = []
    for e in result.get("evidence") or []:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        eid = inline(e["id"], 40)
        if e.get("found") is True:
            title, kind = inline(e.get("title") or eid, 80), str(e.get("kind") or "")
            shown_as, why = presented(kind)
            # Cell outlines and motion tracks are drawn on the picture (show); the rest open in the reader (open).
            verb = "show" if shown_as in ("population", "tracks") else "open"
            lines.append(f"- {title} ({eid}): " + (f'show it with present(what="{eid}", do="{verb}")' if shown_as
                                                    else f"not shown on screen ({why}); say it instead"))
        elif e.get("found") is False:
            lines.append(f"- {eid}: not found in the session's index; say so, never show or describe it as if it were")
        else:
            lines.append(f"- {eid}: could not be checked ({inline(e.get('why') or 'not checked', 80)}); say so")
    return lines


def add_showing_tools(registry: Registry, services) -> None:
    hub, desk = services.hub, services.desk
    if services.research is None or desk is None or services.presenter is None:
        return

    async def show_me(ctx: ToolContext, args: dict) -> str:
        from ... import ticket as tickets
        from ...showing import SESSION_CHANGE, showings_of, stop

        if args.get("stop") in (True, "true", "yes"):
            why = str(args.get("why") or "asked")
            return await stop(services, why=why if why in ("asked", SESSION_CHANGE, "the user's Stop") else "asked")
        scoped = desk
        if ctx.client is not None:  # "this" is what the asking browser's viewer shows, never another browser's
            from ...research.desk import Desk

            view = services.presenter.for_client(ctx.client)
            scoped = Desk(services.research, shown=lambda: view.rid if view.open else None)
        try:
            s = scoped.find(args.get("researcher"))
        except ResearchRefused as exc:
            raise ToolFailed(str(exc), next=getattr(exc, "next", None)) from exc  # next: passed on
        result = s.result or {}
        if not result:
            raise ToolFailed(f"{s.name} has no finding to show yet", next="wait until it has finished, or ask how it is going")
        showings = showings_of(hub)
        before = ""
        if showings.current is not None and showings.current.active:
            before = await stop(services, why="a new showing") + ". "
        request = tickets.current()
        # Its beats are those of the request that started it; with no request, no beat is the showing's.
        showing = showings.start(s.rid, f"{s.name}'s finding", f"request:{request.id}" if request is not None else None)
        lines = [f"{before}Showing {s.name}'s finding ({showing.id}). What it found, in its own words:"]
        lines.append(block("\n".join(t for t in (result.get("title"), result.get("one_liner") or result.get("summary")) if t)))
        records = evidence_lines(result)
        if records:
            lines.append("The session's records it rests on:")
            lines.extend(records)
        else:
            lines.append("It cites none of the session's records: say what it found, and that there is nothing of this "
                         "session's to show for it.")
        missing = [c for c in result.get("session_claims") or [] if isinstance(c, dict) and c.get("found") is False]
        if missing:
            lines.append(f"{len(missing)} of its points rest on records not in the session: say they can't be shown.")
        lines.append("Show it now with present (open its records; dim the live view while one is open, opacity 0.4; "
                     "emphasise the cells it is about). Each beat counts once the studio shows it. Say what you show, in "
                     "its own words, and add nothing it did not find. The user can stop it at any time (Escape, Stop, "
                     "\"stop\"); stopping puts back only what this showing changed.")
        return "\n".join(lines)

    registry.add(Tool("show_me", "\"Show me what it found\": shows a researcher's finding on screen from the session's "
                      "records it rests on, to open with present as beats; stop=true ends it (\"stop\", \"put it back\"), "
                      "putting back only what it changed.",
                      schema({"researcher": RESEARCHER, "stop": {"type": "boolean"}}), show_me, kind="query", inline_ms=4500,
                      family="results"))
