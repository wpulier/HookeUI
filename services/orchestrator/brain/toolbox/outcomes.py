"""Outcomes (session knowledge K3): how the ways of doing something have gone on this kind of specimen, looked up
when the brain chooses or when the user asks "which model did better?". Facts in the past tense, with n; below
the minimum, "too few runs to say". The brain judges; nothing switches by them."""
from __future__ import annotations

import time

from ..registry import Registry, Tool, ToolContext, ToolFailed, schema

FRAMING = "the past, not a recommendation"  # said with every answer: the brain judges, the outcomes only report


def add_outcome_tools(registry: Registry, services) -> None:
    hub = services.hub

    async def outcomes(ctx: ToolContext, args: dict) -> str:
        import asyncio

        from ...labels import subject_key
        from ...operations import OPERATIONS
        from ...outcomes import MIN_RUNS, outcomes_of

        operation = str(args.get("operation") or "").strip().lower() or None
        if operation is not None and operation not in OPERATIONS:
            raise ToolFailed(f"outcomes are kept per operation ({', '.join(OPERATIONS)}); there is none called {operation!r}",
                             next=None)  # no next: the model's own call; it corrects it
        specimen = (getattr(hub.scene, "context", None) or {}).get("specimen")
        subject = subject_key(specimen) if specimen else None
        days = "all" if str(args.get("days") or "").lower() in ("all", "earlier", "every day") else "today"
        today = time.strftime("%Y-%m-%d")
        found = outcomes_of(hub)
        lines, left_out = await asyncio.to_thread(found.answer, operation, subject, today, days)
        before = []
        if subject is not None:  # runs made before a specimen was named: kept apart, said so
            before = await asyncio.to_thread(found.tallies, operation, "", days, today)
        where = f"on {specimen}" if specimen else "with no specimen named now (each line names its own specimen; never pooled)"
        apart = sum(t.runs for t in before)
        # A run with no subject only says no specimen was set when it ran: before one was named, between the running
        # look's two "not the same" looks, or after "start today afresh" (K3 re-check SF1): said as that, never as when.
        aside = (f"\n({apart} run{'s' if apart != 1 else ''} {'today' if days == 'today' else 'in these days'} "
                 f"{'were' if apart != 1 else 'was'} made while no specimen was named; "
                 f"{'they are' if apart != 1 else 'it is'} kept apart from these)") if apart else ""
        if not lines:
            return (f"nothing has run {where} {'today' if days == 'today' else 'yet'} for that, so there is nothing to compare; "
                    "outcomes are only what happened here, never generalised from other specimens" + aside)
        return (f"what happened {where} ({'today' if days == 'today' else 'today and earlier days'}; "
                f"under {MIN_RUNS} runs is too few to say; {FRAMING}):\n" + "\n".join(lines)
                + (f"\n({left_out})" if left_out else "") + aside)

    registry.add(Tool("outcomes", "How each model has done on this specimen (\"which model did better today?\"); which "
                      "model made an outline is resources id=o1.",
                      schema({"operation": {"type": "string", "description": "cells, outline, find-like, or another operation"},
                              "days": {"type": "string", "description": "today or all"}}),
                      outcomes, kind="query", inline_ms=1500, family="conversation"))
