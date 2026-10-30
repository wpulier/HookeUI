"""The measurements table (orchestrator/tables.py): `show_measurements` opens it in the studio that asked, at a cell
population or a motion item, when the user asks to see the numbers ("show me the table", "give me the measurements as a
spreadsheet"), in the window a person's Open in Measurements opens, on the Tab in front or on a Tab it names (made when
needed): one action, done once the studio's report shows it there (orchestrator/table_window.py). The window has the
rows, their median and spread and Download CSV; the agent never reads the rows out (query_population and track_motion
answer about them). A studio without the Project workspace is shown the Measurements page as before."""
from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlencode

from ..registry import Registry, Tool, ToolContext, ToolFailed, schema
from .resources import require_current


def _accepted(ctx, screen) -> bool:
    """Whether an accepted studio (the Hooke workspace) asked, or counts: a `#measurements` page is no surface there."""
    client = getattr(ctx, "client", None)
    if client is not None:
        return client in getattr(screen, "_accepted", {})
    return screen.current_accepted() is not None


async def open_table(services, ctx, *, session: str, target: str, revision: int, title: str, rows: int, what: str,
                     tab: str | None, guard, placement: str | None = None, instead: bool = False) -> str:
    """The measurements table of `target` on screen, said as the studio showed it (table_window.py): in its table
    window where the studio has the Project workspace; the Measurements page in a studio without it (an older studio, a
    test's fixture), as before. Refuses, saying where the table is saved, when nothing shows it."""
    from ...data_reader import reader_studio
    from ...screen import screen_of

    hub = services.hub
    screen = screen_of(hub)
    studio = reader_studio(screen, ctx, session)
    if studio is not None and getattr(services, "workspace_tabs", None) is not None:
        from ...placement import default
        from ...table_window import place, show

        # With a Tab named and no placement, the table fills that Tab as before; otherwise it goes where it was asked,
        # by default beside what is open (placement.py: a table goes beside).
        if placement is None and tab:
            return await show(services, ctx, studio, target=target, revision=revision, title=title, rows=rows,
                              what=what, tab=tab, guard=guard)
        return await place(services, ctx, studio, target=target, revision=revision, title=title, rows=rows, what=what,
                           tab=tab, guard=guard, placement=placement or default("table"), instead=instead)
    saved = f"it is saved in Data as {target}, where its row's Open in Measurements opens it"
    if _accepted(ctx, screen):
        raise ToolFailed(f"{what} is not shown: the studio that asked is not showing this Project's Tabs; {saved}",
                         next="ask again once the Project is open")
    if tab:
        raise ToolFailed(f"{what} is not shown: this studio has no Project Tabs to put it in; {saved}",
                         next="ask for it without a Tab")
    pin = urlencode({'session': session, 'revision': revision})
    shown = await services.open_page(getattr(ctx, 'client', None), f'#measurements/{target}?{pin}')
    guard()
    if shown is None or not shown.get("shown"):
        raise ToolFailed(f"no studio said it shows {what}; {saved}", next="ask again in a moment")
    if not shown.get("visible", True):
        return f"opened {what}; it shows when the user looks at the studio again; Download CSV is there"
    return f"the studio shows {what}; sortable, with the median and middle half, and Download CSV"


def add_table_tools(registry: Registry, services) -> None:
    hub = services.hub
    if services.open_page is None or getattr(hub, "evidence", None) is None:
        return

    async def show_measurements(ctx: ToolContext, args: dict) -> str:
        from ...motiontracks import tracks_of
        from ...population import resolve
        from ...tables import TableRefused, read_table

        day = getattr(services, 'day', None)
        if day is None:
            raise ToolFailed('no Project is open for these measurements', next='open their Project')
        session, generation = day.sid, day.generation
        asked = getattr(ctx, 'asked', None)
        if asked is not None and ((asked.session and asked.session != session)
                                  or asked.routing_generation != generation):
            raise ToolFailed('the request belongs to an earlier Project view', next='ask again in the result’s Project')

        def current():
            if services.day is not day or day.sid != session or day.generation != generation:
                raise ToolFailed('the Project changed before its measurements opened', next='reopen the result’s Project')

        ref = str(args.get("of") or "").strip()
        store = tracks_of(hub) if getattr(hub, "tracks_store", None) is not None else None
        if ref[:1] in ("d", "j") and ref[1:].isdigit():  # a new kind's kept table, or its table job on a volume (kinds.py)
            target = ref
        elif ref.startswith("m") and ref[1:].isdigit():
            item = store.get(ref) if store is not None else None
            if item is None:
                raise ToolFailed(f"there is no motion item {ref}", next="ask what was measured (resources)")
            target = item.id
        elif ref:
            population = resolve(hub, ref, ctx)
            if population is None:
                raise ToolFailed(f"there are no cells called {ref}", next="ask what was measured (resources)")
            target = population.id
        else:  # nothing named: the cells shown or newest, else the newest motion
            population = resolve(hub, None, ctx)
            item = store.latest() if store is not None else None
            if population is None and item is None:
                raise ToolFailed("nothing has been measured in this session yet",
                                 next="outline the cells (outline_cells) or measure motion (track_motion) first")
            target = population.id if population is not None else item.id
        source = hub.evidence.get(target)
        if source is None and store is not None:
            source = store.get(target)
        revision = int(getattr(source, 'revision', 0) or 0)
        try:
            table = await read_table(SimpleNamespace(hub=hub, day=day), target, session, revision)
        except TableRefused as exc:
            raise ToolFailed(str(exc), next='reopen the result’s Project and measurements') from None
        current()
        what = f"the measurements of {table['title']} ({table['count']} rows; {table['note']})"
        tab = " ".join(str(args.get("tab") or "").split())[:80] or None

        def guard():
            current()
            require_current(ctx, day)

        placement = args.get("placement")
        from ...placement import PLACEMENTS

        if placement is not None and placement not in PLACEMENTS:
            raise ToolFailed(f"{placement!r} is not a place for it", next="say one of: " + ", ".join(PLACEMENTS))
        return await open_table(services, ctx, session=session, target=target, revision=revision,
                                title=str(table['title']), rows=int(table['count']), what=what, tab=tab, guard=guard,
                                placement=placement, instead=args.get("instead") is True)

    registry.add(Tool(
        "show_measurements", "Open the measurements table in a table window on a Tab, the window the user's own Open in "
        "Measurements opens: every cell's (or motion track's) numbers as rows, their median and spread, and Download CSV, "
        "when the user wants to see the numbers or take them away (\"show me the table\", \"export the cell sizes\", "
        "\"spreadsheet of the speeds\"). `of`: a population (e3) or motion item (m1), or a table a capability the catalog "
        "added made (d4); with none, the cells shown or newest, else the newest motion. `tab`: a Tab inside this Project to "
        "put it in (\"put the table in a new tab called Measurements\"): its title, or exact id from tabs list; a title no "
        "Tab has makes that Tab. One action: the Tab is made or brought forward and the table fitted to it, in front. "
        "`placement`: beside (the default: side by side with what is open, the live view included; a new Tab has no "
        "live view, so for beside the live view name tab Main), new-tab (a Tab of its own: a population's outlines on "
        "their capture with this table beside them, \"open the cell results in a new tab\"), only, replace, window, "
        "close. `instead`: move it from the Tab it is open in. Done only when the studio shows it; otherwise its answer "
        "says where the table is saved.",
        schema({"of": {"type": "string", "description": "a population (e3) or motion item (m1); empty: the newest"},
                "tab": {"type": "string", "maxLength": 80,
                        "description": "a Tab inside this Project to open it in, by title or id; made when none has it; "
                                       "empty: the Tab in front"},
                "placement": {"type": "string", "enum": ["beside", "new-tab", "only", "replace", "window", "close"],
                              "description": "where it goes (default beside)"},
                "instead": {"type": "boolean", "description": "move it here from the Tab it is open in"}}),
        show_measurements, kind="effect", inline_ms=4000, family="measuring"))
