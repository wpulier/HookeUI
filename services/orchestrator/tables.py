"""Measurement tables (docs/design/2026-10-13-researcher-ready.md): what a cell population or a motion item measured, as
rows a researcher reads, sorts and takes away as CSV (`GET /api/resources/<id>/table[.csv]`, the studio's
Measurements page, `measurements.ts`). Read from the kept evidence itself, never measured again here: a population's
`measure` column group (measure.py, in capture pixels) converted to µm only by its own capture's scale
(`Population.basis`), its newest current look group and nearest named group beside; a motion item's tracks
(motiontracks.py) in the unit they were measured in. Nothing is invented: a value the evidence does not hold is empty,
and the note says what the sizes rest on."""
from __future__ import annotations

import asyncio
import copy
import csv
import io
import re

import numpy as np

from .stats import describe

# A population's measured columns, in the order a researcher reads them: (column, label, what it is in µm).
MEASURED = (("areaPx", "Area", "area"), ("perimeterPx", "Perimeter", "length"), ("feretMaxPx", "Length", "length"),
            ("feretMinPx", "Width", "length"), ("elongation", "Elongation", None), ("circularity", "Roundness", None),
            ("solidity", "Solidity", None), ("intensity", "Brightness", None), ("holeAreaPx", "Hole area", "area"))
# Beside them, when the population has them: the look group and the nearest group the user named (K1).
GROUPED = (("group", "Look group"), ("yourLabel", "Nearest group you named"))
# A motion item's per-track values (motiontracks.from_movement / trail_values).
TRACKED = (("net", "Net speed", "speed"), ("path", "Path speed", "speed"), ("straightness", "Straightness", None),
           ("direction", "Direction", "degrees"), ("moving", "Moving", "yes/no"), ("points", "Points", None))


def _unit(kind: str | None, um: float | None) -> str:
    if kind == "area":
        return "µm²" if um else "px²"
    if kind == "length":
        return "µm" if um else "px"
    return ""


def _scaled(value, kind: str | None, um: float | None):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not um:
        return value
    return value * um * um if kind == "area" else value * um if kind == "length" else value


def _stats(rows: list[dict], columns: list[dict]) -> list[dict]:
    """Per number column: how many, its median and middle half (the quartiles), as `population.summary` says them, then
    its mean, SD, SEM and the mean's 95% confidence interval (stats.describe; None where one value cannot give them)."""
    out = []
    for column in columns:
        if column["type"] != "number":
            continue
        values = np.array([r[column["key"]] for r in rows if isinstance(r.get(column["key"]), (int, float))
                           and not isinstance(r.get(column["key"]), bool)], float)
        described = describe(values)
        if described['n']:
            # One admitted population supplies n and every statistic, including the interval's df.
            out.append({'key': column['key'], **{key: described[key] for key in
                        ('n', 'median', 'q1', 'q3', 'min', 'max', 'mean', 'sd', 'sem', 'ci95')}})
    return out


def _scale_note(basis: dict, view: dict | None = None) -> str:
    um, scale, objective = basis.get("umPerPx"), basis.get("scale"), basis.get("objective")
    if not um and (view or {}).get("sourceImage"):
        return "No scale: sizes are in pixels of the original image. Calibrate that image with a known length for µm."
    if not um:
        return "No scale: sizes are in pixels of the camera's picture. Set the objective (or calibrate) for µm."
    at = f" at {objective}×" if objective else ""
    how = "measured with a known length" if scale == "measured" else "estimated from the objective and eyepiece"
    return f"Sizes in µm{at}, {how} ({um:.4g} µm per pixel)."


def population_table(population) -> dict:
    """A cell population's members (live ones: removed and merged cells are not rows), one row per cell."""
    basis = population.basis()
    um = basis.get("umPerPx")
    members = population.live()
    present = {c for m in members[:50] for c, _l, _k in MEASURED if population.value(m.id, c) is not None}
    noun = population.member_noun
    columns = [{"key": "cell", "label": noun.capitalize(), "unit": "", "type": "text"}]
    columns += [{"key": c, "label": label, "unit": _unit(kind, um), "type": "number"} for c, label, kind in MEASURED if c in present]
    grouped = [(c, label) for c, label in GROUPED if any(population.value(m.id, c) is not None for m in members[:200])]
    columns += [{"key": c, "label": label, "unit": "", "type": "text"} for c, label in grouped]
    columns += [{"key": "edgeCut", "label": "Cut by the edge", "unit": "", "type": "bool"},
                {"key": "x", "label": "x", "unit": "px", "type": "number"}, {"key": "y", "label": "y", "unit": "px", "type": "number"}]
    rows = []
    for m in members:
        row: dict = {"cell": m.id}
        for c, _label, kind in MEASURED:
            if c in present:
                row[c] = _scaled(population.value(m.id, c), kind, um)
        for c, _label in grouped:
            v = population.value(m.id, c)
            row[c] = None if v is None else str(v)
        row["edgeCut"] = bool(population.value(m.id, "edgeCut"))
        centroid = population.value(m.id, "centroid")
        if isinstance(centroid, (list, tuple)) and len(centroid) == 2:
            row["x"], row["y"] = float(centroid[0]), float(centroid[1])
        rows.append(row)
    rest = population.revision
    note = _scale_note(basis, population.view) + (f" Revision {rest}: after your corrections." if rest else " Revision 0.")
    return {"id": population.id, "kind": "population", "title": population.title, "count": len(rows), "note": note,
            "basis": basis, "revision": rest, "memberLabel": noun,
            "definitions": (population.columns.get("measure") or {}).get("definitions") or {},
            "columns": columns, "rows": rows, "stats": _stats(rows, columns)}


def tracks_table(item) -> dict:
    """A motion item's tracks, one row per track, in the unit they were measured in (µm/s with a scale, else px/s)."""
    unit = item.unit
    columns = [{"key": "track", "label": "Track", "unit": "", "type": "text"}]
    for key, label, kind in TRACKED:
        columns.append({"key": key, "label": label, "unit": unit if kind == "speed" else "°" if kind == "degrees" else "",
                        "type": "bool" if kind == "yes/no" else "number"})
    rows = []
    for track in item.rows:
        values = track.get("values") or {}
        rows.append({"track": track.get("id"), **{k: values.get(k) for k, _l, _k in TRACKED}})
    span = item.span or {}
    seconds = span.get("seconds")
    note = (f"Speeds in {unit}" + (f", over {seconds:.0f} s" if isinstance(seconds, (int, float)) else "")
            + "; the slide's own movement is taken out." + ("" if "µm" in unit else " Set the objective for µm/s."))
    return {"id": item.id, "kind": "tracks", "title": item.title, "count": len(rows), "note": note,
            "basis": item.scale or {}, "revision": 0, "memberLabel": "track", "columns": columns, "rows": rows, "stats": _stats(rows, columns)}


def kept_table_digest(values: dict) -> str:
    """The sha256 a new kind's table or points had as the app read them (env_runner._run's producer.sha256), from what
    was kept."""
    import hashlib
    import json

    body = {"points": values.get("points")} if values.get("points") is not None and values.get("columns") is None else \
        {"columns": values.get("columns"), "rows": values.get("rows")}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def result_table(rid: str, entry: dict, values: dict) -> dict | None:
    """A new kind of job's table (kinds.py, C6), kept as a result: its columns with their names, units and definitions as
    it gave them, its rows as they were, bound to the sha256 it was read with (None when it isn't a table, or when what is
    kept is no longer what it gave)."""
    from .resources import keeps_table
    if not keeps_table(values):
        return None
    columns_in, rows_in = values.get("columns"), values.get("rows")
    points = (values or {}).get("points")
    if columns_in is None and isinstance(points, list):  # a new kind's points: where each is, in its frame's pixels
        frame = (values or {}).get("frame") or {}
        columns_in = [{"name": "Point"}, {"name": "x", "unit": "px", "definition": "across the frame it ran on"},
                      {"name": "y", "unit": "px", "definition": "down the frame it ran on"}]
        rows_in = [[i + 1, float(x), float(y)] for i, (x, y) in enumerate(points)]
        values = {**values, "_frame": frame}
    producer = (values or {}).get("producer") or {}
    if not producer.get("sha256") or kept_table_digest(values) != producer["sha256"]:
        return None
    if producer.get("implementation") == "model-comparison":  # the app's own comparison of runs (model_comparison.py)
        from .model_comparison import NOT_ACCURACY

        compared = (values.get("comparison") or {}).get("arms") or ()
        return _given_table(rid, entry.get("title") or rid, columns_in, rows_in,
                            f"Hooke's comparison of {len(compared)} models' runs on the same input, a row per model, "
                            f"from each run's own record. {NOT_ACCURACY}")
    made = f"{producer.get('implementation') or values.get('capability')} ({producer.get('version') or 'its version'})"
    frame = values.get("_frame")
    note = (f"Points as {made} gave them, in pixels of frame {frame.get('seq') or frame.get('capture')} "
            f"({frame.get('width')}×{frame.get('height')}); points only, no sizes." if frame is not None else
            f"As {made} gave them: its own values and units, from code nobody reviewed.")
    return _given_table(rid, entry.get("title") or rid, columns_in, rows_in, note)


def job_table(rid: str, got: dict) -> dict:
    """A new kind of job's table on a volume (registrations.table: its CSV read back by the sha256 recorded when it
    ended), shaped as the measurements table shows every table."""
    producer = got.get("producer") or {}
    note = (f"As {producer.get('implementation')} ({producer.get('version') or 'its version'}) gave it on a volume: its "
            "own columns and values, from code nobody reviewed; its file read back by its sha256.")
    return _given_table(rid, got.get("title") or rid, got["columns"], got["rows"], note)


def _given_table(rid: str, title: str, columns_in: list, rows_in: list, note: str) -> dict:
    columns, definitions, keys = [], {}, set()
    for i, c in enumerate(columns_in):
        name = str((c or {}).get("name") or f"Column {i + 1}")
        key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or f"c{i}"
        while key in keys:
            key += "_"
        keys.add(key)
        cells = [r[i] for r in rows_in if isinstance(r, list) and i < len(r) and r[i] is not None]
        kind = ("bool" if cells and all(isinstance(v, bool) for v in cells) else
                "number" if cells and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in cells) else "text")
        columns.append({"key": key, "label": name, "unit": str((c or {}).get("unit") or ""), "type": kind})
        if (c or {}).get("definition"):
            definitions[key] = str(c["definition"])
    rows = [{col["key"]: (r[i] if isinstance(r, list) and i < len(r) else None) for i, col in enumerate(columns)}
            for r in rows_in]
    return {"id": rid, "kind": "result-table", "title": title, "count": len(rows),
            "note": note, "basis": {},
            "revision": 0, "memberLabel": "row", "definitions": definitions, "columns": columns, "rows": rows,
            "stats": _stats(rows, columns)}


def table_of(hub, eid: str) -> dict | None:
    """The table of a population, a motion item or a new kind's kept table this app holds, or None."""
    from .evidence import evidence_of
    from .motiontracks import tracks_of

    if re.fullmatch(r"d\d+", eid or ""):
        results = getattr(getattr(hub, "resources", None), "results", None)
        entry = results.get(eid) if results is not None else None
        return result_table(eid, entry, results.values(eid)) if entry is not None else None
    if re.fullmatch(r"j\d+", eid or ""):  # a new kind of job's table on a volume (registrations.table, C6)
        regs = getattr(getattr(hub, "resources", None), "registrations", None)
        got = regs.table(eid) if regs is not None else None
        return job_table(eid, got) if got is not None else None

    population = evidence_of(hub).get(eid) if getattr(hub, "evidence", None) is not None else None
    if population is not None:
        return population_table(population)
    store = getattr(hub, "tracks_store", None)
    item = tracks_of(hub).get(eid) if store is not None else None
    return tracks_table(item) if item is not None else None


class TableRefused(ValueError):
    """The requested table is no longer the session/result/revision the reader selected."""


def native_measurement_table(store, eid, session, revision):
    """Adapt accepted native columns into the existing editable-table source contract.

    This reads the measured revision, never labels through another algorithm. Its
    exact Project digest and producing label reference remain with the source.
    """
    from . import scientific_resource_actions as actions, scientific_tables
    from .scientific_projects import InvalidProject
    if type(revision) is not int or revision < 1:
        raise TableRefused('Choose an exact saved native measurement revision')
    try:
        saved = store.read(eid, session, revision=revision)
        measured = actions.table(store, eid, session, revision)
        if measured['format'] != actions.MEASUREMENTS:
            raise TableRefused('Measure the retained labels with the current native table format first')
        typed = scientific_tables.open_table(store, eid, session, revision, measured['table'])
    except (InvalidProject, OSError) as error:
        raise TableRefused(str(error)) from None
    columns = [{'key': c['name'], 'label': 'Object ID' if c['name'] == 'object_id' else c['name'],
                'unit': c['unit'] or '', 'type': 'text' if c['name'] == 'object_id' else 'number'}
               for c in typed.schema['columns']]
    rows = [dict(zip(measured['columns'], row)) for row in measured['rows']]
    return {'id': eid, 'kind': 'native-measurements', 'title': saved.document['title'] + ' measurements',
            'count': len(rows), 'revision': revision, 'session': session, 'basis': {},
            'memberLabel': 'object', 'columns': columns, 'rows': rows,
            'definitions': {c['name']: c['description'] for c in typed.schema['columns']},
            'note': 'Exact retained measurements; edits and analysis never rerun the producing tool.',
            'nativeSource': {'id': eid, 'session': session, 'revision': revision, 'digest': saved.digest,
                             'table': measured['table'], 'labels': measured['source'], 'schema': copy.deepcopy(typed.schema)},
            'stats': _stats(rows, columns)}


async def read_table(app, eid: str, session: str, revision: int) -> dict:
    """Pin a current result before crossing a thread boundary and recheck before publishing.
    Only table metadata is copied: masks, captures and vectors stay with their evidence store.
    Corrections run synchronously on the loop; no await separates the check from the snapshot.
    """
    hub = app.hub
    if re.fullmatch(r'p[0-9a-f]{32}', eid or ''):
        day, generation = app.day, getattr(app.day, 'generation', 0)
        def current():
            if app.day is not day or day.sid != session or getattr(day, 'generation', 0) != generation:
                raise TableRefused('The Project changed. Reopen the original saved measurements.')
        current()
        store = getattr(getattr(hub, 'resources', None), 'projects', None)
        if store is None:
            raise TableRefused('The native scientific store is unavailable')
        table = await asyncio.to_thread(native_measurement_table, store, eid, session, revision)
        current()
        return table
    if re.fullmatch(r"d\d+", eid or ""):  # a new kind's kept table: read and checked by its sha256 (C6)
        results = getattr(getattr(hub, "resources", None), "results", None)
        if app.day.sid != session or results is None or results.get(eid, session) is None:
            raise TableRefused("This table is unavailable in this session. Reopen its session.")
        if revision != 0:
            raise TableRefused("The measurements revision changed. Reopen Measurements before downloading.")
        entry = results.get(eid, session)
        table = await asyncio.to_thread(lambda: result_table(eid, entry, results.values(eid, session)))
        if table is None:
            raise TableRefused("What is kept is no longer the table it gave (its sha256 differs), so it isn't shown.")
        return {**table, "session": session, "revision": 0}
    if re.fullmatch(r"j\d+", eid or ""):  # a new kind of job's table on a volume: read back by its sha256 (C6)
        regs = getattr(getattr(hub, "resources", None), "registrations", None)
        if app.day.sid != session or regs is None or regs.get(eid, session) is None:
            raise TableRefused("This table is unavailable in this session. Reopen its session.")
        if revision != 0:
            raise TableRefused("The measurements revision changed. Reopen Measurements before downloading.")
        got = await asyncio.to_thread(regs.table, eid, session)
        if got is None:
            raise TableRefused("What is kept is no longer the table it gave (its sha256 differs), so it isn't shown.")
        return {**job_table(eid, got), "session": session, "revision": 0}
    population = getattr(hub, "evidence", None)
    tracks = getattr(hub, "tracks_store", None)
    store = population if population is not None and population.get(eid) is not None else tracks
    item = store.get(eid) if store is not None else None
    generation = app.day.generation

    def current():
        if app.day.sid != session or app.day.generation != generation:
            raise TableRefused("The session changed. Open the result's session and reopen Measurements.")
        if item is None or store.get(eid) is not item or item.session != session:
            raise TableRefused("These measurements are unavailable in this session. Reopen their session or restore the retained evidence.")
        if getattr(item, "revision", 0) != revision:
            raise TableRefused("The measurements revision changed. Reopen Measurements before downloading.")
        if item.status in ("failed", "unavailable"):
            raise TableRefused("These measurements are unavailable. Restore the retained evidence and reopen the session.")

    current()
    snapshot = copy.copy(item)
    if store is population:
        snapshot.members = {mid: copy.copy(member) for mid, member in item.members.items()}
        snapshot.columns = copy.deepcopy(item.columns)
        snapshot.view = copy.deepcopy(item.view)
        snapshot.scale_id = copy.deepcopy(item.scale_id)
    else:
        snapshot.rows = copy.deepcopy(item.rows)
        snapshot.span = copy.deepcopy(item.span)
        snapshot.scale = copy.deepcopy(item.scale)

    def read():
        if store is population:
            from .brain.registry import ToolFailed
            try:
                store.require_retained(snapshot)
            except ToolFailed as exc:
                raise TableRefused(str(exc)) from None
            return population_table(snapshot)
        return tracks_table(snapshot)

    table = await asyncio.to_thread(read)
    current()
    return {**table, "session": session, "revision": revision}


def csv_text(table: dict) -> str:
    """CSV preserves round-trip numeric values, units, yes/no flags and empty missing evidence.
    Compact display formatting must never round away the retained exported measurements.
    """
    out = io.StringIO()
    writer = csv.writer(out)
    columns = table["columns"]
    writer.writerow([f"{c['label']} ({c['unit']})" if c["unit"] else c["label"] for c in columns])
    for row in table["rows"]:
        cells = []
        for c in columns:
            v = row.get(c["key"])
            if v is None:
                cells.append("")
            elif isinstance(v, bool):
                cells.append("yes" if v else "no")
            elif isinstance(v, float):
                cells.append(repr(v))
            else:
                cells.append(str(v))
        writer.writerow(cells)
    return out.getvalue()


def file_name(table: dict, session: str) -> str:
    """`<session>_<id>_<title>.csv`, in safe characters."""
    words = re.sub(r"[^A-Za-z0-9]+", "-", str(table.get("title") or "")).strip("-")[:48]
    return f"{session}_{table['id']}{('_' + words) if words else ''}_r{table['revision']}.csv"


def _live_made_words(hub, executions: list[str]) -> tuple[str, str, dict] | None:
    """Describe only evidence retained by the executions this ToolRun actually used (including explicit reuse).
    A different request completing during an await cannot become this action's result."""
    from .evidence import evidence_of
    from .motiontracks import tracks_of

    if getattr(hub, "evidence", None) is not None:
        made = [p for pid, p in evidence_of(hub).items.items() if p.made_by.get("execution") in executions and not p.made_for]
        if made:
            p = made[-1]
            from .operations import label_of
            n, model = len(p.live()), label_of(p.implementation) if p.implementation else ""
            noun = p.member_noun
            with_model = f" with {model}" if model else ""
            device = ((p.producer or {}).get("device") or {}) if isinstance(p.producer, dict) else {}
            if n == 0:
                # Never a bare success for nothing found (ticket 62, F-L8-1): say it found none, and how its device
                # was proven, so a broken device can't pass for an empty picture.
                said = f"Found no {noun}s{with_model}."
                if device.get("device") == "cpu":
                    said += " It ran on the CPU, the reference device."
                elif device.get("checked"):
                    said += f" Its device was checked first: it {device['checked']}."
            else:
                said = f"Outlined and measured {n} {noun if n == 1 else noun + 's'}{with_model}."
            if device.get("says"):
                said += f" {device['says']}."
            return (said + " " + _scale_note(p.basis(), p.view).rstrip(".") + "."), p.id, {"session": p.session, "revision": p.revision}
    if getattr(hub, "tracks_store", None) is not None:
        made = [t for tid, t in tracks_of(hub).items.items() if t.made_by.get("execution") in executions and not t.made_for]
        if made:
            t = made[-1]
            s, unit = t.summary or {}, t.unit
            seconds = float((t.span or {}).get("seconds") or 0.0)
            words = (f"Followed {s.get('counted', 0)} things for {seconds:.0f} s: {s.get('moving', 0)} moving "
                     f"(at least {s.get('threshold', 0):g} {unit} net)")
            if s.get("netMedian") is not None:
                words += f", median speed {s['netMedian']:.3g} {unit}"
            return words + "." + ("" if "µm" in unit else " Set the objective for µm/s."), t.id, {"session": t.session, "revision": 0}
    return None


def _result_words(results, entries):
    if results is not None:
        # A capability's table (kinds.py), kept as a result by the run's own execution: said in plain words, with its
        # Table, never the agent's wrapped data (live 2026-10-15: the panel showed the <capability> block and JSON rows).
        from .day.sid import session_of
        made = [e for e in entries if results.table_candidate(e, session_of(e, e['day']), discover=True)]
        if made:
            entry = made[-1]
            values = results.values(entry['id'], session_of(entry, entry['day']))
            table = result_table(entry["id"], entry, values)
            if table is not None:
                name = values.get("name") or values.get("capability") or "It"  # said_name, carried with the run
                n = table["count"]
                return (f"{name} made a table of {n} row{'s' if n != 1 else ''}.", entry["id"],
                        {"session": session_of(entry, entry["day"]), "revision": 0})
    return None


def _owned_results(hub, executions):
    results = getattr(getattr(hub, 'resources', None), 'results', None)
    entries = [copy.deepcopy(e) for e in results.entries()
               if set(e.get('executions') or ()) & set(executions) and not e.get('for')] if results is not None else []
    return results, entries


def made_words(hub, executions: list[str]) -> tuple[str, str, dict] | None:
    """Synchronous reader for callers already off the event loop."""
    return _live_made_words(hub, executions) or _result_words(*_owned_results(hub, executions))


async def made_words_async(hub, executions: list[str]) -> tuple[str, str, dict] | None:
    """Capture mutable live evidence/owned journal identities before awaiting file work.

    Result discovery, full-value loading and digest/statistics work run off-loop;
    the pinned result's session survives a Project switch during that work.
    """
    live = _live_made_words(hub, executions)
    if live is not None:
        return live
    return await asyncio.to_thread(_result_words, *_owned_results(hub, executions))
