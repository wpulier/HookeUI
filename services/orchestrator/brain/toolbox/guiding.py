"""Guiding the user's hands to a thing on the slide until it is centred and sharp ("find me a stoma and centre it")
(orchestrator/guide.py). The app measures where the thing is and what each move did; the fast model judges each line."""
from __future__ import annotations

from ..registry import CURRENT_RUN, Registry, Tool, ToolContext, ToolFailed, schema
from .common import OBJECT, describe, resolve_object

FIRST_LINE_S = 2.5  # the tool waits this long for the first line, so its answer carries it


def add_guide_tools(registry: Registry, services) -> None:
    hub, guide = services.hub, getattr(services, "guide", None)
    if guide is None or getattr(hub, "scene", None) is None:
        return

    def named(what: str, info) -> list:
        """Outlines already named like `what` whose place is known from this view, nearest to the centre first."""
        from ...slidemap import place

        key = what.strip().lower().rstrip("s")
        found = []
        for o in hub.scene.objects.values():
            if o.trial is not None or o.kind not in ("highlight", "part") or not o.label:
                continue
            label = o.label.lower()
            if key and (key in label or label.rstrip("s") in key):
                p = place(o, info)
                if p is not None:
                    found.append((p.fields, o))
        return [o for _, o in sorted(found, key=lambda pair: pair[0])]

    async def guide_tool(ctx: ToolContext, args: dict) -> str:
        info = hub.latest_info
        if info is None or getattr(info, "fov", None) is None:
            raise ToolFailed("I can't see the eyepiece circle right now, so I can't guide you to anything",
                             next="make sure the eyepiece circle is in view and lit, then ask again")
        what = str(args.get("what") or "").strip()
        until = "centred" if str(args.get("until") or "").strip().lower() in ("centred", "centered", "centre") else "sharp"
        obj, how = None, ""
        if args.get("object"):
            obj = resolve_object(hub, args["object"], ctx)
        elif what:
            already = named(what, info)
            if already:
                obj, how = already[0], "already outlined"
            else:
                find = registry.tools.get("find")
                if find is None:
                    raise ToolFailed(f"nothing called {what!r} is outlined, and finding things needs the vision model",
                                     next="outline it with a click, then ask me to centre it")
                earlier = set(ctx.objects)
                found = await find.handler(ctx, {"what": what, "all": False, "keep": True})
                made = [hub.scene.objects.get(i) for i in reversed(ctx.objects) if i not in earlier]  # this find's own
                made = [o for o in made if o is not None and o.trial is None]
                if not made:
                    raise ToolFailed(f"I don't see a {what} in this view ({found})",
                                     next="move the slide slowly to another part, then ask again")
                obj, how = made[0], "found it"
        else:
            obj = resolve_object(hub, None, ctx)  # "that": the one the user means on the board
        name = what or (obj.label or "").strip() or describe(obj).removeprefix("the ")
        run = CURRENT_RUN.get()
        occurrence = run.occurrence if run is not None else None
        asked = ctx.asked
        origin = {"delegation_id": ctx.delegation_id, "asked": asked, "channel": ctx.channel,
                  "day": getattr(asked, "day", None), "thread": getattr(asked, "thread", None),
                  "voice_owner": ctx.voice_owner if ctx.channel == "voice" else None, "turn_id": ctx.turn_id or None,
                  "request_id": occurrence.request_id if occurrence is not None else ctx.request_id,
                  "action_id": occurrence.action_id if occurrence is not None else None}
        try:
            g = guide.start(obj.id, name=name, asked=ctx.question or f"centre the {name}", until=until, origin=origin,
                            hold_first=True)
        except ValueError as exc:
            raise ToolFailed(str(exc), next=None, argument=True) from exc  # no next: the model's own argument, it corrects it
        if ctx.objects is not None and obj.id not in ctx.objects:
            ctx.objects.append(obj.id)
        first = await guide.first_line(g, FIRST_LINE_S)
        lead = f"{how}: " if how else ""
        goal = "centred and sharp" if until == "sharp" else "centred"
        if first is None:
            return (f"{lead}guiding you to the {name} ({obj.id}); I'll say which way to move the slide, one short "
                    f"line at a time, until it is {goal}")
        if first.get("state") == "done":
            return f"{lead}the {name} ({obj.id}) is already {goal}: {first['say']}"
        return (f"{lead}guiding you to the {name} ({obj.id}). First: \"{first['say']}\" I'll keep saying which way, "
                f"one short line at a time, until it is {goal}")

    registry.add(Tool("guide", "Guide the user's hands to a thing on the slide until it is centred (and sharp, by "
                      "default): \"find me a stoma and centre it\", \"help me get that cell in the middle\", \"bring it "
                      "into focus\". Finds one when nothing like it is outlined yet. The app measures where it is and "
                      "what each move did, and says one short line at a time (\"move the slide left a little\", \"stop, "
                      "it's centred\", \"focus up\") until it gets there. The microscope has no motors: the user moves "
                      "the slide and the focus.",
                      schema({"what": {"type": "string", "description": "the thing in the user's words (\"stoma\"); "
                                       "empty with object"},
                              "object": OBJECT,
                              "until": {"type": "string", "enum": ["sharp", "centred"],
                                        "description": "sharp (the default): centred and in focus; centred: only "
                                                       "centred"}}),
                      guide_tool, inline_ms=6000, family="map", outline_refs=("object",)))

    async def stop_guiding(ctx: ToolContext, args: dict) -> str:
        g = guide.stop("you asked")
        if g is None:
            return "I was not guiding you to anything"
        return f"stopped guiding you to the {g.name} after {len(g.lines)} lines"

    registry.add(Tool("stop_guiding", "Stop guiding the user's hands (\"stop guiding\", \"never mind, I've got it\").",
                      schema({}), stop_guiding, inline_ms=200, family="map"))

    async def guiding(ctx: ToolContext, args: dict) -> str:
        return guide.describe()

    registry.add(Tool("guiding", "What the app is guiding the user to now: the thing, how far from the centre, the "
                      "last line said.", schema({}), guiding, kind="query", inline_ms=100, family="map"))
