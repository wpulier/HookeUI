"""Measurement tables (orchestrator/tables.py): a population's cells and a motion item's tracks as rows, in µm only by
their own capture's scale, with medians and middle halves; the same as CSV; the routes; what an action by hand
measured, in plain words with its table; the agent's show_measurements."""
from __future__ import annotations

import asyncio
import csv
import io

import pytest

from orchestrator.commands import handle
from orchestrator.evidence import evidence_of
from orchestrator.tables import csv_text, file_name, table_of
from tests.test_actions_panel import Client, reply
from tests.test_capability_matrix import world  # noqa: F401 - keys, code and the cloud stood in for


def rig_with_cells(world, name: str, *, objective: bool = False):
    from tests import capability_rig as setups
    from tests.capability_rig import Rig

    rig = Rig(world["root"] / name)
    if objective:
        asyncio.run(setups.prepare(rig, ("objective",)))
        rig.publish(rig.hub.latest_info.id + 1)  # a capture takes its own frame's scale: the next frame has it
    asyncio.run(setups.prepare(rig, ("cells",)))
    return rig


def test_a_populations_table_is_its_cells_measured_in_pixels_without_a_scale(world):
    rig = rig_with_cells(world, "px")
    population = evidence_of(rig.hub).latest()
    table = table_of(rig.hub, population.id)
    keys = [c["key"] for c in table["columns"]]
    assert table["kind"] == "population" and table["count"] == len(population.live()) == len(table["rows"]) > 0
    assert keys[0] == "cell" and "areaPx" in keys and "circularity" in keys and keys[-2:] == ["x", "y"]
    area = next(c for c in table["columns"] if c["key"] == "areaPx")
    assert (area["label"], area["unit"]) == ("Area", "px²")
    first = population.live()[0]
    assert table["rows"][0]["areaPx"] == population.value(first.id, "areaPx")  # as measured, never measured again
    assert "No scale" in table["note"]
    stat = next(s for s in table["stats"] if s["key"] == "areaPx")
    assert stat["n"] == table["count"] and stat["q1"] <= stat["median"] <= stat["q3"]


def test_with_the_objective_set_sizes_are_in_micrometres_by_the_captures_own_scale(world):
    rig = rig_with_cells(world, "um", objective=True)
    population = evidence_of(rig.hub).latest()
    um = population.basis()["umPerPx"]
    assert um
    table = table_of(rig.hub, population.id)
    columns = {c["key"]: c for c in table["columns"]}
    assert columns["areaPx"]["unit"] == "µm²" and columns["feretMaxPx"]["unit"] == "µm"
    first = population.live()[0]
    row = table["rows"][0]
    assert abs(row["areaPx"] - population.value(first.id, "areaPx") * um * um) < 1e-9
    assert abs(row["feretMaxPx"] - population.value(first.id, "feretMaxPx") * um) < 1e-9
    assert row["circularity"] == population.value(first.id, "circularity")  # no unit: as measured
    assert "Sizes in µm at 40×" in table["note"]


def test_the_csv_has_each_columns_unit_in_its_header_and_one_row_a_cell(world):
    rig = rig_with_cells(world, "csv", objective=True)
    table = table_of(rig.hub, evidence_of(rig.hub).latest().id)
    rows = list(csv.reader(io.StringIO(csv_text(table))))
    assert rows[0][:2] == ["Cell", "Area (µm²)"] and len(rows) == table["count"] + 1
    assert rows[1][0].startswith("c")
    assert file_name(table, "2026-10-13").startswith(f"2026-10-13_{table['id']}_Cell-outlines")


def test_an_action_that_measures_says_what_it_measured_in_plain_words_with_its_table(world):
    from tests import capability_rig as setups
    from tests.capability_rig import Rig

    rig = Rig(world["root"] / "said")
    asyncio.run(setups.prepare(rig, ("still",)))
    client = Client()
    asyncio.run(handle(rig.app, client, {"type": "action", "capability": "cells", "args": {}}))
    answered = reply(client)
    population = evidence_of(rig.hub).latest()
    assert answered["ok"] and answered["table"] == population.id
    assert answered["tableIdentity"] == {"session": population.session, "revision": population.revision}
    n = len(population.live())
    assert answered["said"].startswith(f"Outlined and measured {n} cell") and "instances" not in answered["said"]
    client = Client()
    asyncio.run(handle(rig.app, client, {"type": "action", "capability": "track-motion", "args": {}}))
    answered = reply(client)
    assert answered["ok"] and answered["table"].startswith("m") and answered["said"].startswith("Followed ")
    assert "Display:" not in answered["said"]


@pytest.mark.parametrize('transition', ['none', 'before', 'during'])
def test_the_agent_opens_the_table_in_the_studio_that_asked(world, monkeypatch, transition):
    rig = rig_with_cells(world, "agent")
    opened: list[str] = []
    from orchestrator import tables as table_module
    original = table_module._stats
    def checked(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise AssertionError('measurement statistics ran on the event loop')
        return original(*args, **kwargs)
    monkeypatch.setattr(table_module, '_stats', checked)
    read = table_module.read_table
    async def interrupted(*args, **kwargs):
        result = await read(*args, **kwargs)
        if transition == 'during':
            rig.app.day.generation += 1
        return result
    monkeypatch.setattr(table_module, 'read_table', interrupted)

    async def open_page(client, page):
        opened.append(page)
        return {"shown": True, "visible": True}

    from orchestrator.brain.toolbox.tables import add_table_tools
    from orchestrator.brain.registry import Registry, ToolContext

    registry = Registry()
    add_table_tools(registry, type("S", (), {"hub": rig.hub, "day": rig.app.day, "open_page": staticmethod(open_page)})())

    async def ask():
        context = ToolContext('chat', 0, None, asked=rig.app.day.here())
        if transition == 'before':
            rig.app.day.generation += 1
        run = registry.start('show_measurements', {}, context)
        if run.task is not None:
            await run.task
        return run

    run = asyncio.run(ask())
    if transition != 'none':
        assert opened == [] and not run.ok and 'Project' in run.summary
        return
    eid = evidence_of(rig.hub).latest().id
    from urllib.parse import urlencode
    pin = urlencode({'session': rig.app.day.sid, 'revision': evidence_of(rig.hub).latest().revision})
    assert opened == [f"#measurements/{eid}?{pin}"] and run.ok and "Download CSV" in run.summary


def test_an_action_never_claims_measurements_from_another_request_while_it_waits(world):
    from tests.capability_rig import Rig
    from tests import capability_rig as setups
    rig = Rig(world['root'] / 'overlapping-actions')

    async def go():
        await setups.prepare(rig, ('still',))
        entered, release = asyncio.Event(), asyncio.Event()
        tool = rig.app.brain.registry.tools['clear']
        original = tool.handler
        async def delayed(ctx, args):
            answer = await original(ctx, args)
            entered.set()
            await release.wait()
            return answer
        tool.handler = delayed
        slow, cells = Client(), Client()
        task = asyncio.create_task(handle(rig.app, slow, {'type': 'action', 'capability': 'clear', 'args': {}}))
        await entered.wait()
        try:
            await handle(rig.app, cells, {'type': 'action', 'capability': 'cells', 'args': {}})
        finally:
            release.set()
        await task
        assert reply(cells)['ok'] and reply(cells)['table']
        assert reply(slow)['ok'] and 'table' not in reply(slow) and 'said' not in reply(slow)
    asyncio.run(go())


def test_overlapping_measurements_link_each_actions_own_retained_execution(world):
    from tests.capability_rig import Rig
    from tests import capability_rig as setups
    from orchestrator.capabilities import load
    rig = Rig(world['root'] / 'two-measurements')

    async def go():
        await setups.prepare(rig, ('still',))
        name = next(c['how']['tool'] for c in load()['capabilities'] if c['id'] == 'cells' and c.get('actor') == 'agent')
        tool = rig.app.brain.registry.tools[name]
        original = tool.handler
        entered, release = asyncio.Event(), asyncio.Event()
        first_id = None
        async def delayed(ctx, args):
            nonlocal first_id
            answer = await original(ctx, args)
            if first_id is None:
                first_id = evidence_of(rig.hub).latest().id
                entered.set()
                await release.wait()
            return answer
        tool.handler = delayed
        first, second = Client(), Client()
        slow = asyncio.create_task(handle(rig.app, first, {'type': 'action', 'capability': 'cells', 'args': {}}))
        await entered.wait()
        try:
            rig.publish(rig.hub.latest_info.id + 1)
            await handle(rig.app, second, {'type': 'action', 'capability': 'cells', 'args': {}})
        finally:
            release.set()
        await slow
        assert reply(first)['ok'] and reply(second)['ok']
        assert reply(first)['table'] == first_id
        assert reply(second)['table'] != first_id
    asyncio.run(go())
