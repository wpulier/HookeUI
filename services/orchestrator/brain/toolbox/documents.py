"""A document of what this Project found (`write_document`; orchestrator/documents.py).

"Write out the interesting findings so far, save that doc, and open it in a new tab": the brain's own model writes it
from the Project's records (code gives it the facts and checks every paragraph cites a record), it is kept in Data
under Write-ups, and opened when asked, in a Tab of its own by default. Its receipt says where it is saved and where
the studio shows it."""
from __future__ import annotations

import asyncio

from ..registry import Registry, Tool, ToolContext, ToolFailed, schema
from .resources import require_current


def add_document_tools(registry: Registry, services) -> None:
    hub = services.hub
    if services.day is None:  # a Project's records, and where it is kept
        return

    async def write_document(ctx: ToolContext, args: dict) -> str:
        from ... import documents
        from ...data_reader import reader_studio
        from ...resources import resources_of
        from ...screen import screen_of
        from ...untrusted import inline
        from ..registry import CURRENT_RUN, attach

        placement = args.get("placement")
        if placement is not None and placement not in documents.PLACEMENTS:
            if placement == "overlay":
                raise ToolFailed("a document is not drawn on the live view; outlines and layers are",
                                 next="open it in a Tab of its own (new-tab) or beside (in the reader)")
            raise ToolFailed(f"{placement!r} is not a place for a new document",
                             next="say new-tab (a Tab of its own, the default) or beside (the reader on the right)")
        shown = args.get("open") is True or placement is not None or bool(args.get("tab"))
        placement = placement or "new-tab"
        asked = " ".join(str(args.get("about") or ctx.question or "").split())[:600] or "the findings so far"
        day = services.day
        require_current(ctx, day)
        index = resources_of(hub)
        session = index.session()
        found, total = await asyncio.to_thread(documents.records, index, session)
        require_current(ctx, day)
        if not found:
            raise ToolFailed("this Project has nothing recorded to write from yet: nothing was measured, outlined, "
                             "counted, named or reported here", next="outline, count or measure something first, or "
                                                                    "start a researcher")
        run = CURRENT_RUN.get()
        if run is not None:
            run.report_progress(f"writing it from {len(found)} of this Project's records")
        model = hub.settings.vision
        data = await documents.write(services.agent, model, documents.prompt(asked, found, total=total,
                                                                             title=args.get("title")))
        require_current(ctx, day)
        doc = documents.check(data, found, title=args.get("title"))
        values = documents.values_of(doc, found, asked=asked, model=model, total=total)
        results = index.results
        if run is not None and run.tool == "write_document":  # its own run's result, kept once (resources.from_run)
            attach(values=values)
            kept = results.from_run(run, ctx, hub, produced=True)
            run.report_progress(None)
        else:  # run some other way (a test's direct call): kept as its run would keep it
            kept = results.add("write_document", {"about": asked}, documents.written_words(doc, found),
                               values=values, request=getattr(ctx, "question", "") or "",
                               session=getattr(getattr(ctx, "asked", None), "session", None) or None)
        if kept is None:
            raise ToolFailed("it was written but could not be kept in Data", next=None)  # no next: the limitation is stated
        entry = await asyncio.to_thread(index.get, kept["id"], session)
        if entry is None:
            raise ToolFailed(f"it was written, but Data does not list {kept['id']} yet", next="ask what data there is")
        written = documents.written_words(doc, found)
        saved = f"saved in Data as {entry.id}, under Write-ups"
        if not shown:
            return (f"{written[:1].upper()}{written[1:]}. It is {saved}; not opened (asked, it opens in {documents.TAB}).")
        studio = reader_studio(screen_of(hub), ctx, day.sid)
        if studio is None:
            raise ToolFailed(f"{written[:1].upper()}{written[1:]}, and it is {saved}; this studio has no Project workspace "
                             "to show it in", next=None)  # no next: where it is saved is said

        def guard():
            require_current(ctx, day)

        tab = inline(args.get("tab"), 80) or inline(doc.title, 60) or "Document"
        where = await documents.show(services, ctx, studio, entry, placement=placement, title=tab, guard=guard)
        return f"{written[:1].upper()}{written[1:]}. It is {saved}, and {where}."

    registry.add(Tool(
        "write_document", "Write a document from what this Project already has, as the user asks (\"write out the "
        "interesting findings so far\"): its measurements, outlines, counts, names, reference photos with their sources, "
        "the scale and the researchers' findings, each paragraph citing its records; nothing they do not say. Saved in "
        "Data (Write-ups). open: true when the user asks to see it; placement new-tab (a Tab of its own, the default) or "
        "beside (the reader on the right). Done once the screen shows it. Only when asked for a document; a deep report "
        "that reads the web is start_researcher's.",
        schema({"about": {"type": "string", "maxLength": 600,
                          "description": "what to write, in the user's words (\"the interesting findings so far\")"},
                "title": {"type": "string", "maxLength": 120, "description": "a title the user gave"},
                "open": {"type": "boolean", "description": "open it once saved (the user asked to see or open it)"},
                "placement": {"type": "string", "enum": ["new-tab", "beside"],
                              "description": "with open: where it goes (default new-tab)"},
                "tab": {"type": "string", "maxLength": 80, "description": "with open: a name for its Tab"}}),
        write_document, kind="effect", inline_ms=1500, family="results"))
