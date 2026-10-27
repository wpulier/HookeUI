"""The measurements table (orchestrator/tables.py; the studio's Measurements page): `show_measurements` opens it in the
studio that asked, at a cell population or a motion item, when the user asks to see the numbers ("show me the table",
"give me the measurements as a spreadsheet"). The page has the rows, their median and spread and Download CSV; the
agent never reads the rows out (query_population and track_motion answer about them)."""
from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlencode

from ..registry import Registry, Tool, ToolContext, ToolFailed, schema


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
        pin = urlencode({'session': session, 'revision': revision})
        shown = await services.open_page(getattr(ctx, 'client', None), f'#measurements/{target}?{pin}')
        current()
        what = f"the measurements of {table['title']} ({table['count']} rows; {table['note']})"
        if shown is None or not shown.get("shown"):
            raise ToolFailed(f"the studio did not say it shows {what}", next="say it is in Made, under its row's Table")
        if not shown.get("visible", True):
            return f"opened {what}; it shows when the user looks at the studio again; Download CSV is there"
        return f"the studio shows {what}; sortable, with the median and middle half, and Download CSV"

    registry.add(Tool(
        "show_measurements", "Open the measurements table in the studio: every cell's (or motion track's) numbers as "
        "rows, their median and spread, and Download CSV, when the user wants to see the numbers or take them away "
        "(\"show me the table\", \"export the cell sizes\", \"spreadsheet of the speeds\"). `of`: a population (e3) or "
        "motion item (m1), or a table a capability the catalog added made (d4); with none, the cells shown or newest, else the "
        "newest motion.",
        schema({"of": {"type": "string", "description": "a population (e3) or motion item (m1); empty: the newest"}}),
        show_measurements, kind="effect", inline_ms=4000, family="measuring"))
