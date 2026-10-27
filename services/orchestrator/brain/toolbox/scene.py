"""Tools on what is outlined and on the day's preferences: select, clear, name an outline (and
teach it: taught.py), name the specimen, let the user lead, how to explain (level, hints first),
dissect."""
from __future__ import annotations

from ...day.sid import session_of
from ..registry import Registry, Tool, ToolContext, ToolFailed, current_step, record_made, schema
from .common import OBJECT, describe, resolve_object, visible_object


def add_scene_tools(registry: Registry, services) -> None:
    hub = services.hub

    async def select(ctx: ToolContext, args: dict) -> str:
        obj = visible_object(hub, args.get("object"), ctx)
        hub.scene.select(obj.id)
        ctx.pointed = obj.id  # "it" in this request's later steps (board.py)
        if services.magnifier is not None:
            services.magnifier.follow(obj.id)
        return f"selected {describe(obj)}"

    registry.add(Tool("select", "Select an object so its details open and the magnifier follows it.",
                      schema({"object": OBJECT}), select, family="scene", outline_refs=("object",)))

    async def clear(ctx: ToolContext, args: dict) -> str:
        from ...ticket import current

        # A clear supersedes requests made before it (their late results add nothing); this
        # request's own later steps may still add (ticket.py).
        request = current()
        what = str(args.get("what") or "all")
        # What a later step of this request names is kept: "clear and re-highlight o20" must not remove o20 before the
        # highlight names it (live 2026-10-11 16:36:22: "I can't find 'o20'", and the answer said it re-highlighted it).
        # The step order stays the model's (or the look's clearing first); only the clear leaves those outlines.
        number, steps = current_step(ctx)
        kept = steps.named_later(number, [o.id for o in hub.scene.objects.values() if o.kind == "highlight"])
        if what == "cells":
            from ...population import layer_of

            hub.scene.generations.bump_kind("cell", by=request)
            n = len(layer_of(hub).population().live()) if layer_of(hub).population() is not None else 0
            layer_of(hub).hide()  # the population is kept (evidence), only no longer shown
            kept = []
        elif what == "outlines":
            n = hub.scene.clear("highlight", by=request, keep=kept)
        elif what == "ai":
            hub.scene.generations.bump("outline:app", why="the app's outlines were cleared", by=request)
            ids = [o.id for o in hub.scene.objects.values() if o.kind == "highlight" and o.source.startswith("ai")
                   and o.id not in kept]
            for oid in ids:
                hub.scene.remove(oid)
            n = len(ids)
        else:
            from ...motiontracks import track_layer_of
            from ...population import layer_of

            n = hub.scene.clear(by=request, keep=kept)
            layer_of(hub).hide()
            # Motion tracks shown are taken off too (kept as evidence), and a measurement still running
            # shows nothing when it lands (M3.2).
            hub.scene.generations.bump("motion", why="the view was cleared", by=request)
            track_layer_of(hub).hide()
        said = f"cleared {n} outline{'s' if n != 1 else ''}"
        kept = [hub.scene.objects[oid] for oid in kept if oid in hub.scene.objects]
        if kept:
            said += (f"; kept {', '.join(f'{describe(o)} ({o.id})' for o in kept)}: a later step of this request "
                     f"names {'it' if len(kept) == 1 else 'them'}")
        return said

    registry.add(Tool("clear", "Remove outlines from the view.",
                      schema({"what": {"type": "string", "description": '"all", "outlines", "ai" (only the ones you made) '
                                                                       'or "cells"'}}), clear, family="scene"))

    async def outline_cells(ctx: ToolContext, args: dict) -> str:
        from ... import tools

        try:
            population, shown = await tools.outline_cells(hub, compare_reference=bool(args.get("compare_reference")))
        except tools.ToolError as exc:
            raise ToolFailed(str(exc), next=exc.next) from exc
        record_made(ctx, population.id)  # "@n" to the steps after this one (the step fixed when it started)
        from ...population import drawn_on_stage

        original = bool(population.view.get("sourceImage"))
        return (f"{population.title} ({population.id}): {population.count_words()}, measured; "
                + ("kept on the original image; open the saved result in Made" if shown and not drawn_on_stage(hub, population)
                   else "the layer is on the picture on the Stage; they are drawn once that picture is checked" if shown and original
                   else "the layer is on; each is drawn once its own pixels are checked" if shown
                   else "kept, not shown (the request was superseded)"))

    async def name_outline(ctx: ToolContext, args: dict) -> str:
        from ...labels import label_key
        from ...taught import MIN_EXAMPLES

        obj = resolve_object(hub, args.get("object"), ctx)
        name = " ".join(str(args.get("name") or "").split())[:60]
        if not label_key(name):
            raise ToolFailed("no name was given for it", next="say what to call it")
        # The user's own name: confirmed, the one status the model never sets itself.
        from ...known import renamed

        hub.scene.update(obj.id, label=name, status="confirmed", maybe=[], revised_by=None, naming=renamed(obj, "you", name))
        said = f"named {obj.id} '{name}', as the user says"
        taught = services.taught
        if taught is None:
            return said
        try:
            await taught.teach(name, obj.id, source="named")
        except ValueError as exc:
            return f"{said}; it could not be kept as an example to match: {exc}"
        n = taught.examples_of(name)
        more = (f"; new outlines are compared with them from {MIN_EXAMPLES}" if n < MIN_EXAMPLES else
                "; new outlines like it are marked 'your label'")
        return f"{said}; kept as example {n} of '{name}'{more}"

    registry.add(Tool("name_outline", "The user says what an outlined thing is (\"this is a guard cell\", \"that one's a "
                      "neutrophil\"): their name replaces yours and is kept as an example; from 3 examples, new outlines "
                      "that look like them show the user's label beside yours. Not for the whole specimen (name_specimen).",
                      schema({"object": OBJECT, "name": {"type": "string", "description": "what the user calls it"}}, ["name"]),
                      name_outline, inline_ms=1500, family="scene", outline_refs=("object",)))

    registry.add(Tool("outline_cells", "Segment every cell in view at once with the cell finder (Cellpose-SAM, micro-sam "
                      "or EdgeTAM's automatic masks, whichever the settings and this edition use; about 1-8 s) and show "
                      "them as a layer. Its result is a population (e3) of members (c12) with measurements, which "
                      "query_population and highlight's selector use (\"@1\" names it in the same request). It says so "
                      "when it cannot run here.", schema({"compare_reference": {"type": "boolean", "description": "For an explicitly selected Fiji Analyze Particles run, compare with its ImageJ macro on the same input and generation. Agreement is not accuracy."}}), outline_cells, inline_ms=4000, family="scene"))

    if services.subjects is None:
        return

    async def name_specimen(ctx: ToolContext, args: dict) -> str:
        try:
            name = services.subjects.name(str(args.get("name") or ""))
        except ValueError as exc:
            raise ToolFailed(str(exc), next=None) from exc  # no next: the message says what was wrong
        return f"the specimen is {name} now, as the user says"

    registry.add(Tool("name_specimen", "The user says what the specimen is (\"it's a lucerne stem\", \"this is a Musca leg\"): "
                      "use their name for it from now on. Speech can mishear names (\"lucent\" is likely \"lucerne\"): pass "
                      "the real name you think they meant.",
                      schema({"name": {"type": "string", "description": "the specimen, as the user named it"}}, ["name"]),
                      name_specimen, inline_ms=100, family="conversation"))


def add_lead_tool(registry: Registry, services) -> None:
    hub = services.hub

    async def set_lead(ctx: ToolContext, args: dict) -> str:
        on = bool(args.get("user_leads", True))
        hub.settings.user_leads = on
        if services.day is not None:
            services.day.set_pref("userLeads", on)  # kept until changed, across restarts and days
        if services.publish_settings is not None:
            services.publish_settings()
        return ("the user leads now: I point things out only when asked" if on
                else "I can point things out again when it helps")

    registry.add(Tool("set_lead", "Let the user lead (\"let me find it myself\", \"stop pointing things out\", \"don't give it "
                      "away\"): from then on you highlight, select or name what to look for only when asked in so many "
                      "words. user_leads=false when they say you can point things out again.",
                      schema({"user_leads": {"type": "boolean"}}), set_lead, inline_ms=100, family="conversation", sets=("userLeads",)))

    async def set_level(ctx: ToolContext, args: dict) -> str:
        """How to explain (learner.py), kept until changed. The model judges when the user asked."""
        changes = {}
        if args.get("level") is not None:
            changes["level"] = str(args["level"]).strip().lower()
        if args.get("hints_first") is not None:
            changes["hintsFirst"] = bool(args["hints_first"])
        if not changes:
            raise ToolFailed("say simple or technical, or hints first on or off",
                             next=None)  # no next: the message says what to do
        problems = hub.settings.update(changes)
        if problems:
            raise ToolFailed("; ".join(problems), next=None)  # no next: the problems say what was wrong
        if services.day is not None:
            for key, value in changes.items():
                services.day.set_pref(key, value)  # kept until changed, across restarts and days
        if services.publish_settings is not None:
            services.publish_settings()
        said = []
        if "level" in changes:
            said.append("I'll explain things simply" if hub.settings.level == "simple" else "I'll use the technical terms")
        if "hintsFirst" in changes:
            said.append("I'll give a hint before each answer" if hub.settings.hints_first else "I'll answer straight away")
        return " and ".join(said)

    registry.add(Tool("set_level", "How the user wants things explained, for the rest of today: level \"simple\" (\"explain it "
                      "more simply\", \"I'm new to this\") or \"technical\" (\"I'm a biology student, be technical\", \"use "
                      "the proper terms\"); hints_first true when they want a hint before the answer (\"give me hints\", "
                      "\"don't tell me straight away\"), false to answer straight away again. Only when they say how you should explain; "
                      "wanting to learn or to observe better is not that.",
                      schema({"level": {"type": "string", "enum": ["simple", "technical"]},
                              "hints_first": {"type": "boolean"}}), set_level, inline_ms=100, family="conversation", sets=("level", "hintsFirst")))


def add_dissect_tool(registry: Registry, services) -> None:
    hub = services.hub
    if services.dissector is None:
        return

    async def dissect(ctx: ToolContext, args: dict) -> str:
        obj = resolve_object(hub, args.get("object"), ctx)
        if obj.kind != "highlight":
            raise ToolFailed("dissect works on an outlined object", next="click the thing first, then ask")
        hub.scene.select(obj.id)
        ctx.pointed = obj.id  # "it" in this request's later steps (board.py)
        shown_note = ""
        native = getattr(services, "presentations", None) is not None and getattr(ctx, "client", None) is not None
        if services.magnifier is not None and not native:
            from ...screen import on_screen, open_state, screen_of

            before = screen_of(hub).reported_at
            services.magnifier.zoom(obj.id)  # the parts are drawn and named on its live close-up
            try:
                await on_screen(hub, open_state("magnifier", True, shows=obj.id), before, "the magnifier is not open on it",
                                "click the outline to open it")
            except ToolFailed as exc:  # the parts are found all the same; where they show is said
                shown_note = f"; {exc.said()}"
        result = await services.dissector.dissect(obj)
        if not result["parts"]:
            return f"no distinct parts are visible in {describe(obj)} at this magnification; zooming in further would help"
        from ..registry import CURRENT_RUN, attach

        values = {"parts": list(result["parts"]), "of": obj.id, "summary": result.get("summary") or ""}
        if native:
            # The dissected view is its own window, from its own kept picture (the object and its named parts on the
            # frame they were found in): independent of a live magnification of the same object, closed on its own,
            # opened again from Made today (live 2026-10-11: closing the magnifier closed it, and it could not be
            # opened again).
            if not result.get("picture"):
                shown_note = "; its picture could not be made, so the parts are shown on the live view only"
            else:
                from .present import _native_present

                attach(values=values, picture_bytes=result["picture"])
                entry = hub.resources.results.from_run(CURRENT_RUN.get(), ctx, hub, produced=True)
                if entry is None:
                    shown_note = "; its picture could not be kept, so the parts are shown on the live view only"
                else:
                    title = f"{obj.label or 'Object'} · dissected parts"
                    try:
                        shown_note = "; " + await _native_present(services, ctx, {
                            "do": "open", "title": title,
                            "source": {"day": session_of(entry, entry["day"]), "id": entry["id"], "version": None, "picture": 0}})
                    except ToolFailed as exc:  # the parts are found and kept all the same; where they show is said
                        shown_note = f"; {exc.said()}"
                    values = None
        names = ", ".join(result["parts"]) + shown_note
        if values is not None:
            attach(values=values)  # a result's values
        return f"{describe(obj)}: {names}" + (f". {result['summary']}" if result["summary"] else "")

    registry.add(Tool("dissect", "Find, outline and name the visible parts inside an object, shown in a window of its "
                      "own (the dissected view: the object and its named parts on the frame they were found in). "
                      "Use it whenever the user asks what something is made of, what parts it has, or to break it down "
                      "or dissect it; then describe the parts it finds rather than parts you only know about.",
                      schema({"object": OBJECT}), dissect, inline_ms=1500, family="finding", outline_refs=("object",)))
