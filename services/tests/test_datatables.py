"""Dataset tables (orchestrator/datatables.py; plan D8): measured tables pooled in their own units, mismatched units
refused with the source that lacks a scale; rows keyed "<sourceId>:<memberId>" so what the user gave them survives a
removal, a merge and an undo; every version's rows kept with their SHA-256 and checked when read; current, updating,
stale and frozen said in words; following a correction off the event loop; `tb` ids that no other id pattern reads;
the index's dataset-table entries; and the routes."""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator import datatables
from orchestrator.datatables import DataTableRefused, DataTables, freshness, pool
from orchestrator.population import correct
from tests.test_corrections import keeping, saved
from tests.test_population import cells, world  # noqa: F401 (the fixture)


def table(tid: str, unit: str, areas: list[float], *, kind: str = "population", title: str = "") -> dict:
    """A measurements table as tables.read_table gives one."""
    member = "cell" if kind == "population" else "track"
    return {"id": tid, "kind": kind, "title": title or f"Cells {tid}", "revision": 0,
            "basis": {"umPerPx": 0.25} if unit.startswith("µm") else {},
            "columns": [{"key": member, "label": "Cell", "unit": "", "type": "text"},
                        {"key": "areaPx", "label": "Area", "unit": unit, "type": "number"},
                        {"key": "x", "label": "x", "unit": "px", "type": "number"}],
            "rows": [{member: f"c{i + 1}", "areaPx": a, "x": 1.0} for i, a in enumerate(areas)]}


# --- pooling ---------------------------------------------------------------------------------------------------------
def test_pooling_keeps_each_sources_rows_keyed_by_source_and_member():
    columns, rows = pool([table("e3", "µm²", [10.0, 20.0]), table("e4", "µm²", [30.0])])
    assert [r["row"] for r in rows] == ["e3:c1", "e3:c2", "e4:c1"]
    assert [r["areaPx"] for r in rows] == [10.0, 20.0, 30.0]  # as each source measured it, by its own scale
    assert [c["key"] for c in columns][:3] == ["row", "source", "member"]
    assert next(c for c in columns if c["key"] == "areaPx")["unit"] == "µm²"


def test_pooling_a_source_without_a_scale_with_one_in_micrometres_is_refused_and_says_which():
    with pytest.raises(DataTableRefused) as refused:
        pool([table("e3", "µm²", [10.0]), table("e4", "px²", [400.0])])
    said = str(refused.value)
    assert said.startswith("e4 has no scale") and "e3 (µm²)" in said and "nothing was pooled" in said
    assert "calibrate e4" in refused.value.next


def test_pixel_positions_never_count_as_a_unit_mismatch():
    pool([table("e3", "µm²", [1.0]), table("e4", "µm²", [2.0])])  # both carry x in px beside their µm sizes


# --- edits ------------------------------------------------------------------------------------------------------------
def build_from(record, tables):
    columns, rows = pool(tables)
    return datatables.build(record, columns, rows)


def blank(tid="tb1") -> dict:
    return {"id": tid, "version": 1, "title": "t", "by": "user", "mode": "follow", "sources": [],
            "added": [], "values": {}, "excluded": {}, "computed": []}


def test_a_condition_by_rule_values_by_hand_exclusions_and_computed_columns():
    tables = [table("e3", "µm²", [10.0, 20.0]), table("e4", "µm²", [40.0])]
    columns, rows = pool(tables)
    record = blank()
    shown = {c["key"]: c for c in columns}
    keys = {r["row"] for r in rows}
    datatables.apply_edit(record, {"op": "add-column", "label": "Condition", "type": "category",
                                   "rule": {"cases": [{"where": {"source": "e3"}, "value": "control"}], "otherwise": "treated"}},
                          shown, keys)
    datatables.apply_edit(record, {"op": "add-column", "label": "Note", "type": "text"}, shown, keys)
    datatables.apply_edit(record, {"op": "set", "column": "note", "rows": ["e3:c2"], "value": "dividing"}, shown, keys)
    datatables.apply_edit(record, {"op": "exclude", "rows": ["e4:c1"], "reason": "out of focus"}, shown, keys)
    datatables.apply_edit(record, {"op": "compute", "label": "Log area", "kind": "log10", "a": "areaPx"}, shown, keys)
    datatables.apply_edit(record, {"op": "compute", "label": "Area per x", "kind": "ratio", "a": "areaPx", "b": "x"}, shown, keys)
    snapshot, _v, _e, _n = datatables.build(record, columns, rows)
    by = {r["row"]: r for r in snapshot["rows"]}
    assert [by[k]["condition"] for k in ("e3:c1", "e3:c2", "e4:c1")] == ["control", "control", "treated"]
    assert by["e3:c2"]["note"] == "dividing" and by["e3:c1"]["note"] is None
    assert by["e4:c1"]["excluded"] == "out of focus" and snapshot["counts"] == {"rows": 3, "included": 2, "excluded": 1}
    assert by["e3:c1"]["log_area"] == pytest.approx(1.0)
    units = {c["key"]: c["unit"] for c in snapshot["columns"]}
    assert units["log_area"] == "log10 µm²" and units["area_per_x"] == "µm²/px"
    with pytest.raises(DataTableRefused, match="different units"):
        datatables.apply_edit(record, {"op": "compute", "label": "d", "kind": "difference", "a": "areaPx", "b": "x"},
                              {c["key"]: c for c in snapshot["columns"]}, keys)
    with pytest.raises(DataTableRefused, match="one of ratio"):
        datatables.apply_edit(record, {"op": "compute", "label": "e", "kind": "__import__('os')", "a": "areaPx"}, shown, keys)
    with pytest.raises(DataTableRefused, match="say why"):
        datatables.apply_edit(record, {"op": "exclude", "rows": ["e3:c1"]}, shown, keys)


# --- freshness --------------------------------------------------------------------------------------------------------
def test_every_freshness_state_in_words():
    record = {**blank(), "version": 3, "sources": [{"id": "e3", "revision": 1}, {"id": "e4", "revision": 0}]}
    assert freshness(record, {"e3": 1, "e4": 0}) == {"state": "current", "words": "Current · e3 revision 1, e4 revision 0"}
    assert freshness(record, {"e3": 2, "e4": 0}) == {"state": "stale", "words": "e3 corrected → revision 2 · Update"}
    assert freshness(record, {"e3": 2}, updating=True) == {"state": "updating", "words": "Updating… e3 corrected → revision 2"}
    assert freshness(record, {"e3": None})["words"] == "e3 can't be read now · Update"
    frozen = {**record, "mode": "frozen"}
    assert freshness(frozen, {"e3": 1}) == {"state": "frozen", "words": "Frozen at version 3"}
    assert freshness(frozen, {"e3": 2})["words"] == "Frozen at version 3 · since then e3 corrected → revision 2"


# --- real populations, corrected ---------------------------------------------------------------------------------------
def over(world, tmp_path, *, debounce: float = 0.05) -> DataTables:
    keeping(world, tmp_path)
    tables = DataTables(SimpleNamespace(hub=world, day=world.evidence.day), tmp_path / "tables")
    tables.debounce = debounce
    world.datatables = tables
    world.evidence.listeners.append(tables.population_changed)
    return tables


def test_row_keys_carry_what_the_user_gave_through_a_removal_a_merge_and_an_undo(world, tmp_path):
    tables = over(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    pid = population.id
    ids = [m.id for m in population.live()]
    a, b, c, d, gone = ids[0], ids[1], ids[2], ids[3], ids[4]

    async def go():
        made = await tables.create([pid], title="Control cells")
        assert made["id"] == "tb1" and made["sources"][0]["revision"] == 0
        made = await tables.edit("tb1", 1, [
            {"op": "add-column", "label": "Condition", "type": "category"},
            {"op": "set", "column": "condition", "rows": [f"{pid}:{x}" for x in ids], "value": "control"},
            {"op": "set", "column": "condition", "rows": [f"{pid}:{d}"], "value": "treated"},
            {"op": "exclude", "rows": [f"{pid}:{gone}"], "reason": "touches the edge"}])
        assert made["version"] == 2

        correct(world, population, "remove", [gone], by="user")
        assert tables.freshness(tables.latest("tb1"))["state"] == "updating"
        await tables.settled()
        after = tables.latest("tb1")
        assert after["version"] == 3 and after["sources"][0]["revision"] == 1 and after["by"] == "follow"
        rows = {r["row"]: r for r in tables.read("tb1")["rows"]}
        assert f"{pid}:{gone}" not in rows and rows[f"{pid}:{a}"]["condition"] == "control"
        assert after["excluded"][f"{pid}:{gone}"] == "touches the edge"  # kept for an undo, not shown

        correct(world, population, "merge", [a, b], by="user")  # both control: the merged cell is control
        await tables.settled()
        merged = next(m for m in population.live() if (m.extras or {}).get("mergedFrom") == [a, b])
        rows = {r["row"]: r for r in tables.read("tb1")["rows"]}
        assert rows[f"{pid}:{merged.id}"]["condition"] == "control" and f"{pid}:{a}" not in rows

        correct(world, population, "merge", [c, d], by="user")  # control and treated: left empty, and said
        await tables.settled()
        mixed = next(m for m in population.live() if (m.extras or {}).get("mergedFrom") == [c, d])
        latest = tables.latest("tb1")
        rows = {r["row"]: r for r in tables.read("tb1")["rows"]}
        assert rows[f"{pid}:{mixed.id}"]["condition"] is None and "its parts differ in condition" in latest["change"]

        for _ in range(3):  # undo both merges and the removal: every part comes back with what it was given
            correct(world, population, "undo", [], by="user")
        await tables.settled()
        rows = {r["row"]: r for r in tables.read("tb1")["rows"]}
        assert {rows[f"{pid}:{x}"]["condition"] for x in (a, b, c)} == {"control"} and rows[f"{pid}:{d}"]["condition"] == "treated"
        assert rows[f"{pid}:{gone}"]["excluded"] == "touches the edge"
        assert tables.freshness(tables.latest("tb1"))["state"] == "current"
        assert [r["version"] for r in tables.history("tb1")] == list(range(1, tables.latest("tb1")["version"] + 1))

    asyncio.run(go())


def test_a_frozen_table_never_follows_and_an_old_version_keeps_its_own_rows(world, tmp_path):
    tables = over(world, tmp_path)
    population, _ = cells(world)
    saved(population)

    async def go():
        await tables.create([population.id], mode="frozen")
        first = tables.read("tb1", 1)
        correct(world, population, "remove", [population.live()[0].id], by="user")
        await asyncio.sleep(0.15)
        await tables.settled()
        record = tables.latest("tb1")
        assert record["version"] == 1 and tables.freshness(record) == {
            "state": "frozen", "words": f"Frozen at version 1 · since then {population.id} corrected → revision 1"}
        await tables.edit("tb1", 1, [{"op": "mode", "mode": "follow"}])  # unfrozen: it follows from now on
        await tables.settled()
        assert tables.latest("tb1")["version"] == 3 and tables.freshness(tables.latest("tb1"))["state"] == "current"
        assert tables.read("tb1", 1) == first and len(tables.read("tb1")["rows"]) == len(first["rows"]) - 1
        with pytest.raises(DataTableRefused, match="version 3 now, not 2"):
            await tables.edit("tb1", 2, [{"op": "title", "title": "late"}])

    asyncio.run(go())


def test_a_follow_that_cannot_read_its_source_is_stale_and_says_why(world, tmp_path, monkeypatch):
    from orchestrator import tables as measured

    tables = over(world, tmp_path)
    population, _ = cells(world)
    saved(population)

    async def go():
        await tables.create([population.id])

        async def refused(*_a, **_k):
            raise measured.TableRefused("These measurements are unavailable.")

        monkeypatch.setattr(measured, "read_table", refused)
        correct(world, population, "remove", [population.live()[0].id], by="user")
        await tables.settled()
        fresh = tables.freshness(tables.latest("tb1"))
        assert fresh["state"] == "stale" and fresh["words"].startswith(f"{population.id} corrected → revision 1 (")
        assert "unavailable" in fresh["words"] and fresh["words"].endswith("· Update")

    asyncio.run(go())


def test_a_follow_asked_in_one_session_never_changes_another_sessions_table(world, tmp_path):
    tables = over(world, tmp_path, debounce=0.1)
    population, _ = cells(world)
    saved(population)
    own = tables.app

    async def go():
        await tables.create([population.id])
        correct(world, population, "remove", [population.live()[0].id], by="user")
        tables.app = SimpleNamespace(hub=world, day=SimpleNamespace(sid="another-project", datatables={}))
        await tables.settled()
        tables.app = own
        assert tables.latest("tb1")["version"] == 1  # not followed in the other session's name
        assert tables.freshness(tables.latest("tb1"))["state"] == "stale"
        assert (await tables.update("tb1", by="user"))["version"] == 2  # Update, back in its own session

    asyncio.run(go())


def test_a_snapshot_whose_bytes_changed_is_never_read(world, tmp_path):
    tables = over(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    record = asyncio.run(tables.create([population.id]))
    path = tables.path_of(record)
    assert path.is_file() and record["snapshot"]["path"] == f"{world.evidence.day.sid}/tb1/v1.json"
    assert tables.read("tb1")["counts"]["rows"] == len(population.live())
    path.write_bytes(path.read_bytes().replace(b'"rows":', b'"rows" :', 1))  # the same rows, other bytes
    with pytest.raises(DataTableRefused, match="SHA-256 differs"):
        tables.read("tb1")
    path.unlink()
    with pytest.raises(DataTableRefused, match="can't be read from disk"):
        tables.read("tb1")


def test_the_journal_brings_every_version_back_after_a_restart(world, tmp_path):
    from orchestrator.day.store import DayStore
    from tests.test_resources import OCT_7

    tables = over(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    asyncio.run(tables.create([population.id], title="Before"))
    asyncio.run(tables.edit("tb1", 1, [{"op": "title", "title": "After"}]))
    again = DayStore(root=tmp_path / "sessions", day=world.evidence.day.sid, clock=lambda: OCT_7)
    assert [r["title"] for r in again.datatables["tb1"]] == ["Before", "After"]
    assert again.ids_used["tb"] == 1 and again.new_datatable_id() == "tb2"


def test_following_a_correction_reads_and_writes_off_the_event_loop(world, tmp_path, monkeypatch):
    """The build of a version (pooling, rules, computed columns) and its snapshot's write run in a thread: a slow build
    never stalls the loop the live view runs on."""
    tables = over(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    built = datatables.build

    def slow(*args, **kwargs):
        time.sleep(0.4)
        return built(*args, **kwargs)

    monkeypatch.setattr(datatables, "build", slow)

    async def go():
        gaps, stop = [], asyncio.Event()

        async def tick():
            last = time.perf_counter()
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        ticker = asyncio.create_task(tick())
        await tables.create([population.id])
        correct(world, population, "remove", [population.live()[0].id], by="user")
        await tables.settled()
        stop.set()
        await ticker
        assert tables.latest("tb1")["version"] == 2
        return max(gaps)

    assert asyncio.run(go()) < 0.2


# --- ids and the index ------------------------------------------------------------------------------------------------
def test_tb_ids_are_read_as_tb_and_no_other_id_pattern_matches_them():
    from orchestrator.day.catalogue import ids_in
    from orchestrator.day.ids import PREFIXES

    assert "tb" in PREFIXES
    assert ids_in({"id": "tb12"}) == [("tb", 12)] and ids_in({"id": "b12"}) == [("b", 12)]
    assert ids_in({"id": "e3", "members": ["c1", "tb2"]}) == [("e", 3), ("c", 1), ("tb", 2)]
    # Every id pattern the orchestrator matches ids with ([em]\d+, c\d+, [vj]\d+, d\d+, p<hex>, u/f<hex>...): none
    # takes a dataset table's id for one of its own.
    root = Path(__file__).resolve().parents[1] / "orchestrator"
    literal = re.compile(r"""r?(["'])(\[?[a-z]+\]?(?:\\d\+|\[0-9\]\+|\[0-9a-f\]\{\d+\}))\1""")
    found = set()
    for path in root.rglob("*.py"):
        if path.name == "datatables.py":
            continue
        for m in literal.finditer(path.read_text(encoding="utf-8")):
            found.add(m.group(2))
    assert {r"[em]\d+", r"c\d+", r"d\d+"} <= found, found
    for pattern in found:
        assert not re.fullmatch(pattern, "tb12") and not re.match(pattern + "$", "tb12"), pattern
    from orchestrator.resources import HASHED_ID
    assert not HASHED_ID.fullmatch("tb12")


def test_new_ids_respect_what_any_session_gave_on_the_day():
    from orchestrator.day.store import DayStore

    store = SimpleNamespace(ids_used={"tb": 4}, datatables={}, date="2026-10-23",
                            catalogue=SimpleNamespace(used=lambda day, prefix: 9 if prefix == "tb" else 0))
    recorded = []
    store.active_id = 't1'
    store._record = lambda event, *, strict: recorded.append((event, strict))
    assert DayStore.new_datatable_id(store) == "tb10"
    assert recorded[0][0]['id'] == 'tb10' and recorded[0][0]['kept'] is False
    assert recorded[0][1] is True


def test_the_index_lists_each_dataset_table_with_its_freshness(world, tmp_path):
    from orchestrator.resources import Index

    tables = over(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    asyncio.run(tables.create([population.id], title="Control"))
    index = Index(world, day=world.evidence.day)
    entry = next(e for e in index.collect()[0] if e.kind == "dataset-table")
    assert entry.id == "tb1" and entry.title == "Control" and entry.version == 1
    assert entry.values["freshness"]["state"] == "current" and f"pooled from {population.id}" in entry.summary
    wire = entry.wire()
    assert wire["product"] and wire["actions"] == ["read"]
    assert index.get("tb1").id == "tb1"


# --- the routes -------------------------------------------------------------------------------------------------------
def test_the_routes_make_read_edit_and_export_a_table(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from orchestrator import tables as measured
    from orchestrator.paths import Paths
    from orchestrator.server import create_app

    # Capabilities' kept tables (d<n>): never corrected, so a table of them is current until edited.
    sources = {"d3": table("d3", "µm²", [10.0, 20.0], kind="result-table"), "d4": table("d4", "px²", [400.0], kind="result-table")}

    async def read(app, eid, session, revision):
        if eid not in sources:
            raise measured.TableRefused("These measurements are unavailable in this session.")
        return {**sources[eid], "session": session, "revision": revision}

    encoded = set()
    def off_loop(name, fn):
        def run(*args, **kwargs):
            with pytest.raises(RuntimeError):
                asyncio.get_running_loop()
            encoded.add(name)
            return fn(*args, **kwargs)
        return run
    for name in ('snapshot_bytes', 'csv_text'):
        monkeypatch.setattr(datatables, name, off_loop(name, getattr(datatables, name)))
    monkeypatch.setattr(measured, "read_table", read)
    client = TestClient(create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path)))
    studio = {"X-MicroscopeMind": "datatable"}
    listed = client.get('/api/datatables').json()
    assert listed['tables'] == []
    session = listed['session']
    assert client.post('/api/datatables', headers=studio, json={'sources': ['d3']}).status_code == 400
    assert client.post('/api/datatables', headers=studio, json={'session': 'another-project', 'sources': ['d3']}).status_code == 409
    assert client.post("/api/datatables", json={"sources": ["d3"]}).status_code == 403
    mixed = client.post("/api/datatables", headers=studio, json={"session": session, "sources": ["d3", "d4"]})
    assert mixed.status_code == 400 and mixed.json()["error"].startswith("d4 has no scale")
    assert client.post("/api/datatables", headers=studio, json={"session": session, "sources": ["d9"]}).status_code == 409
    made = client.post("/api/datatables", headers=studio, json={"session": session, "sources": ["d3"], "title": "Control"}).json()
    assert made["id"].startswith("tb") and made["counts"]["rows"] == 2 and made["freshness"]["state"] == "current"
    tid = made["id"]
    edited = client.patch(f"/api/datatables/{tid}", headers=studio, json={"session": session, "version": 1, "edits": [
        {"op": "exclude", "rows": ["d3:2"], "reason": "a doublet"}]})
    assert edited.status_code == 200 and edited.json()["version"] == 2 and edited.json()["counts"]["excluded"] == 1
    late = client.patch(f"/api/datatables/{tid}", headers=studio, json={"session": session, "version": 1, "edits": [{"op": "title", "title": "x"}]})
    assert late.status_code == 409 and "version 2 now" in late.json()["error"]
    assert client.get(f"/api/datatables/{tid}", params={"version": 1, "session": session}).json()["counts"]["excluded"] == 0
    csv = client.get(f"/api/datatables/{tid}.csv", params={"session": session})
    assert csv.status_code == 200 and csv.text.splitlines()[0].startswith("Row,Source,Member,")
    assert "Area (µm²)" in csv.text.splitlines()[0] and csv.text.splitlines()[2].startswith("d3:2,d3,2,")
    assert "a doublet" in csv.text and f"_{tid}_Control_v2.csv" in csv.headers["content-disposition"]
    assert encoded == {'snapshot_bytes', 'csv_text'}
    assert client.get(f'/api/datatables/{tid}', params={'session': 'other-project'}).status_code == 409
    assert client.patch(f'/api/datatables/{tid}', headers=studio,
                        json={'session': 'other-project', 'version': 2, 'edits': [{'op': 'title', 'title': 'wrong'}]}).status_code == 409
    assert client.get("/api/datatables/tb99", params={"session": session}).status_code == 404
    listed = client.get("/api/resources?kind=dataset-table").json()["entries"]
    assert [e["id"] for e in listed] == [tid]


@pytest.mark.parametrize('conflict', ['unit', 'type'])
def test_pool_refuses_conflicting_coordinate_descriptors(conflict):
    left, right = table('e1', 'px²', [1.0]), table('d2', 'px²', [2.0], kind='result-table')
    right['columns'][-1][conflict] = 'µm' if conflict == 'unit' else 'text'
    with pytest.raises(DataTableRefused, match='different|mix units'):
        pool([left, right])


def test_concurrent_creates_preserve_both_accepted_snapshots(world, tmp_path, monkeypatch):
    import threading
    tables = over(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    second = threading.Event()
    allocate = tables.day.new_datatable_id
    allocated = []
    def reserve():
        tid = allocate()
        allocated.append(tid)
        if len(allocated) == 2:
            second.set()
        return tid
    monkeypatch.setattr(tables.day, 'new_datatable_id', reserve)
    write = tables._write
    def held_write(*args, **kwargs):
        assert second.wait(5), 'second create never reached allocation'
        return write(*args, **kwargs)
    monkeypatch.setattr(tables, '_write', held_write)
    async def go():
        records = await asyncio.gather(tables.create([population.id]), tables.create([population.id]))
        assert len({r['id'] for r in records}) == 2
        assert all(tables.read(r['id'])['counts']['rows'] for r in records)
    asyncio.run(go())


@pytest.mark.parametrize('transition', ['replace', 'reload', 'cancel'])
def test_project_change_during_snapshot_write_cannot_publish(world, tmp_path, monkeypatch, transition):
    import threading
    tables = over(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    original = tables.day
    entered, release = threading.Event(), threading.Event()
    write = tables._write
    def held_write(*args, **kwargs):
        result = write(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(tables, '_write', held_write)
    async def go():
        task = asyncio.create_task(tables.create([population.id]))
        assert await asyncio.to_thread(entered.wait, 5)
        if transition == 'replace':
            tables.app.day = SimpleNamespace(sid='other-project', generation=1, datatables={})
        elif transition == 'reload':
            original.generation += 1
        else:
            task.cancel()
        release.set()
        if transition == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(DataTableRefused, match='Project changed'):
                await task
        assert not original.datatables and not tables.app.day.datatables
        assert not list(tables.root.glob('*/tb*/v*.json'))
    asyncio.run(go())


def test_existing_snapshot_is_never_replaced_and_reserved_ids_survive_reload(world, tmp_path):
    from orchestrator.day.store import DayStore
    tables = over(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    record = asyncio.run(tables.create([population.id]))
    original = tables.path_of(record).read_bytes()
    snapshot = tables.read(record['id'])
    snapshot['rows'][0]['x'] = -1234
    with pytest.raises(DataTableRefused, match='already exists'):
        tables._write(record, snapshot, tables.day.sid)
    assert tables.path_of(record).read_bytes() == original
    reserved = tables.day.new_datatable_id()
    reopened = DayStore(root=tables.day.root, day=tables.day.sid, clock=tables.day.clock)
    assert int(reopened.new_datatable_id()[2:]) > int(reserved[2:])


def test_pool_refuses_oversized_rows_before_hashing_or_build(monkeypatch):
    monkeypatch.setattr(datatables, 'MAX_ROWS', 1)
    with pytest.raises(DataTableRefused, match='at most 1 rows'):
        pool([table('e1', 'px²', [1.0, 2.0])])


def test_table_transforms_run_outside_live_loop(world, tmp_path, monkeypatch):
    tables = over(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    called = set()
    def guarded(name, fn):
        def run(*args, **kwargs):
            with pytest.raises(RuntimeError):
                asyncio.get_running_loop()
            called.add(name)
            return fn(*args, **kwargs)
        return run
    for name in ('source_of', 'base_of', 'copy_record', 'snapshot_bytes'):
        monkeypatch.setattr(datatables, name, guarded(name, getattr(datatables, name)))
    async def go():
        record = await tables.create([population.id])
        await tables.edit(record['id'], 1, [{'op': 'title', 'title': 'Accepted'}])
    asyncio.run(go())
    assert called == {'source_of', 'base_of', 'copy_record', 'snapshot_bytes'}
