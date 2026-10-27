"""One log record kind for everything done with researchers' work."""
from __future__ import annotations

from ..registry import Registry, ToolContext

# --- one record of everything done with researchers --------------------------------------
RESEARCH_OPS = {"start_researcher": "start", "researcher_followup": "followup", "revise_work": "revise",
                "researcher_note": "note", "cancel_researcher": "cancel", "show_result": "show", "close_result": "close",
                "result_step": "step", "read_result": "read", "show_in_magnifier": "magnify", "set_research_cap": "cap"}


def log_research_control(registry: Registry, log) -> None:
    """Every action on a researcher's work, by voice or chat, as one log record kind
    ("research-control": what, with which arguments, whether it worked and what it said), so a
    session can be checked afterwards for what was done to which researcher and why."""
    for name, op in RESEARCH_OPS.items():
        tool = registry.tools.get(name)
        if tool is None or getattr(tool.handler, "logged", False):
            continue

        async def wrapped(ctx: ToolContext, args: dict, _handler=tool.handler, _op=op) -> str:
            record = {"kind": "research-control", "op": _op, "args": args, "channel": ctx.channel,
                      "delegationId": ctx.delegation_id, "question": (ctx.question or "")[:200]}
            def note(entry: dict) -> None:
                try:
                    log(entry)
                except Exception:  # a log that cannot be written never changes what the tool did
                    pass

            try:
                out = await _handler(ctx, args)
            except Exception as exc:
                note({**record, "ok": False, "summary": str(exc)[:300]})
                raise
            note({**record, "ok": True, "summary": str(out)[:300]})
            return out

        wrapped.logged = True  # type: ignore[attr-defined]
        tool.handler = wrapped

