"""The first result an agent gets: the app's own guided example, run for the agent (ADR 0026).

It reuses the onboarding flow as it is (onboarding.py): the bundled CC0 image in its own local-only Project, the
built-in CPU capability `basic-bright-objects` (no key, no download), and the saved result's measurement table. The
work is recorded as the agent's (actor.ACTOR, set by the operation). Saving and showing are said apart: with no Hooke
window to show the outlines, the result is still saved and the answer says it wasn't shown (ADR 0006).
"""
from __future__ import annotations

import asyncio

from .connections import Refused

PREVIEW_ROWS = 5
TOOL = "example"


def _plural(noun: str) -> str:
    return noun if noun.endswith("s") else noun + "es" if noun.endswith(("x", "ch", "sh")) else noun + "s"


def _tell_studios(app) -> None:
    """Open windows reread the walkthrough's state: the agent did its step, so its card moves on (onboarding.ts)."""
    import json
    for client in list(getattr(getattr(app, "hub", None), "clients", None) or ()):
        client.offer(json.dumps({"type": "onboarding-changed"}))


def takeover(app) -> Refused | None:
    """Why running the example now would change what the researcher sees, or None when it wouldn't (review B3; the
    plan's "the researcher's view is never taken over"). The example opens its own Project and puts its image on the
    stage, so it runs only when that takes nothing from anyone:
    - Talk isn't live (opening the example would end it);
    - no Hooke window is open, or the one open shows an empty Project (nothing opened, made or asked) or the example's
      own."""
    from .. import examples
    voice = getattr(getattr(app, "brain", None), "voice", None)
    if voice is not None and not getattr(voice, "closed", True):
        return Refused(409, "hooke-busy", "Talk is on in Hooke, and running the example would end it.",
                       next="ask the researcher to stop Talk in Hooke, then run the example")
    clients = getattr(getattr(app, "hub", None), "clients", None) or ()
    if not any(getattr(c, "kind", None) == "studio" for c in clients):
        return None
    day = app.day
    if getattr(day, "guide", None) == examples.GUIDE:
        return None
    empty = not getattr(day, "sources", None) and getattr(day, "on_stage", None) is None \
        and not getattr(day, "ids_used", None) and not getattr(day, "last_user", 0)
    if empty:
        return None
    name = getattr(day, "name", "") or "a Project"
    return Refused(409, "hooke-busy", f"Hooke shows {name!r} with the researcher's work in it, and the example would "
                   "switch it to the example's own Project.",
                   next="ask the researcher to open a new empty Project in Hooke (or close Hooke's window), then run it")


def page(result: dict, session: str) -> str:
    return f"#measurements/{result['id']}?session={session}&revision={result['revision']}"


async def run(app, record: dict, progress) -> dict:
    from .. import onboarding
    from ..brain.registry import ToolFailed
    from ..tables import TableRefused, read_table
    from .actor import ACTOR

    ACTOR.set(record["by"])
    guide = onboarding.of(app)
    busy = takeover(app)  # checked again now that it starts: the researcher may have opened something meanwhile
    if busy is not None:
        raise busy
    try:
        await progress(1, 3, "Opening the example image in its own Project")
        await guide.command(None, {"action": "start"})
        sid, asset = guide.state["session"], guide.state["assetId"]
        # The analysis runs on what is on stage; give the bridge a moment to land the example image there.
        for _ in range(100):
            staged = getattr(app.hub, "stage_meta", None) or {}
            if app.day.on_stage == asset and staged.get("id") == asset:
                break
            await asyncio.sleep(0.1)
        await progress(2, 3, "Finding bright objects with basic-bright-objects (runs on this Mac)")
        shown = True
        try:
            await guide.command(None, {"action": "analyze", "session": sid})
        except ToolFailed:
            if not guide.state.get("result"):
                raise
            shown = False  # saved, but no Hooke window showed the outlines
    except asyncio.CancelledError:
        # The guide keeps its own work going when a caller goes away (its command is shielded): wait for it to end, so
        # this operation never says "cancelled" while the analysis is still running.
        active = getattr(guide, "active", None)
        if active is not None and not active[1].done():
            await asyncio.wait({active[1]})
        if guide.state.get("result"):
            record["outcome"] = {"says": "The analysis finished before it could stop; its result is kept."}
        raise
    result = guide.state["result"]
    _tell_studios(app)
    await progress(3, 3, "Reading the measurements")
    try:
        table = await read_table(app, result["id"], sid, result["revision"])
    except TableRefused as error:
        raise Refused(409, "measurements-unavailable", str(error), next="open the example's Project in Hooke") from None
    rows = table.get("rows") or []
    count = table.get("count", len(rows))
    noun = table.get("memberLabel") or "region"
    return {"says": f"Found {count} {noun if count == 1 else _plural(noun)} in the example image.",
            "project": sid, "result": {"id": result["id"], "revision": result["revision"]},
            "count": count, "columns": table.get("columns") or [],
            "preview": rows[:PREVIEW_ROWS], "truncated": len(rows) > PREVIEW_ROWS,
            "note": table.get("note") or "", "shownInHooke": shown, "page": page(result, sid)}
