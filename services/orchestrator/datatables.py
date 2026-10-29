"""Dataset tables (plan D8, "Tables, Prism-grade"): the user's editable layer over one or more measured tables.

A dataset table `tb<n>` pools populations, motion tracks and capabilities' tables, each read through
`tables.read_table` at its own revision and in its own unit (a population's sizes in µm by its own capture's scale), and
adds what the user gives it: columns (a condition, by a rule or from selected rows), rows left out with a reason, and
columns computed by a fixed operation (ratio, difference, log10, scale; never `eval`). Pooling refuses tables whose
same column is in different units, and says which source has no scale.

Every edit is a new version, journaled in the session (a `datatable` event, as annotations are: day/store.py
`record_datatable`). Each version keeps its own rows in a snapshot file with its SHA-256, read back only when the bytes
still match: a population keeps only revision 0 and its current one re-readable, so a dataset table's older versions
must keep their own rows. Rows are keyed `"<sourceId>:<memberId>"` ("e3:c12"), so what the user assigned survives a
correction: a removed cell's row leaves and comes back with an undo, and a merged cell inherits what its parts agreed on.

A table follows its sources by default (`follow`): a correction makes a new version, debounced and read off the event
loop. Freeze is explicit (`frozen`): a frozen table never changes by itself and says when its sources have moved on.
`freshness` says which of current, updating, stale or frozen a version is, in words.
"""
from __future__ import annotations

import asyncio
import copy
import csv
import hashlib
import io
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .retained_input import comparison_input_key

PREFIX = "tb"
ID = re.compile(r"tb[0-9]+")
MODES = ("follow", "frozen")
ADDED_TYPES = ("category", "number", "text")
COMPUTED = ("ratio", "difference", "log10", "scale")
DEBOUNCE_S = 0.5  # corrections close together make one new version
MAX_SOURCES = 24
MAX_COLUMNS = 48  # columns the user adds or computes
MAX_ASSIGNED = 20000  # cells the user assigned by hand, and rows left out, in one table (each version journals them)
MAX_VERSIONS = 2000
MAX_ROWS = 100_000
MAX_CELLS = 2_000_000
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
KEY = re.compile(r"[a-z][a-z0-9_]{0,39}")
# The pooled table's own columns, before each source's: which row it is, where it came from, and its member there.
OWN = ({"key": "row", "label": "Row", "unit": "", "type": "text", "origin": "source"},
       {"key": "source", "label": "Source", "unit": "", "type": "text", "origin": "source"},
       {"key": "member", "label": "Member", "unit": "", "type": "text", "origin": "source"})
EXCLUDED = {"key": "excluded", "label": "Left out because", "unit": "", "type": "text", "origin": "excluded"}
MEMBER_KEYS = {"population": "cell", "tracks": "track", "native-measurements": "object_id"}  # the column a source's own row id is in


class DataTableRefused(ValueError):
    """What was asked of a dataset table can't be done, in words, with a next step when there is one."""

    def __init__(self, message: str, next: str | None = None, status: int = 400) -> None:
        super().__init__(message)
        self.next = next
        self.status = status


@dataclass(frozen=True)
class TableOwner:
    store: object
    session: str
    generation: int
    thread: str | None

    def check(self, app) -> None:
        if (app.day is not self.store or app.day.sid != self.session
                or getattr(app.day, 'generation', 0) != self.generation):
            raise DataTableRefused('Project changed while the table was being read or saved',
                                   next='reopen the original Project and try again', status=409)

    def key(self, tid: str) -> tuple:
        return (self.session, self.generation, tid)


# --- pure parts: pooling, rules, computed columns, carrying assignments, freshness ----------------------------------
def row_key(source_id: str, member_id) -> str:
    return f"{source_id}:{member_id}"


def digest(table: dict) -> str:
    """The SHA-256 of a source table's columns and rows as they were read."""
    body = json.dumps({"columns": table.get("columns"), "rows": table.get("rows")}, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def source_of(table: dict, session: str) -> dict:
    """What a version records of one source: where it is, what it was when read, and its units."""
    units = {c["key"]: c.get("unit") or "" for c in table.get("columns") or [] if c.get("type") == "number"}
    basis = table.get("basis") or {}
    return {"day": session, "id": table["id"], "kind": table.get("kind") or "", "revision": int(table.get("revision") or 0),
            "digest": digest(table), **({"nativeSource": copy.deepcopy(table["nativeSource"])} if "nativeSource" in table else {}), "label": str(table.get("title") or table["id"]), "units": units,
            "scale": basis.get("umPerPx") if isinstance(basis.get("umPerPx"), (int, float)) else None}


def _unscaled(unit: str) -> bool:
    return unit.startswith("px")


def check_units(tables: list[dict]) -> None:
    """Refuse tables whose same measured column is in different units, saying which source lacks a scale."""
    by_key: dict[str, list[tuple[dict, str, str]]] = {}
    for table in tables:
        for c in table.get("columns") or []:
            if c.get("type") == "number":
                by_key.setdefault(c["key"], []).append((table, c.get("unit") or "", c.get("label") or c["key"]))
    for key, seen in by_key.items():
        units = {u for _t, u, _l in seen}
        if len(units) < 2:
            continue
        label = seen[0][2]
        lacking = [t["id"] for t, u, _l in seen if _unscaled(u)]
        scaled = [f"{t['id']} ({u})" for t, u, _l in seen if not _unscaled(u)]
        if lacking and scaled:
            raise DataTableRefused(
                f"{', '.join(lacking)} {'has' if len(lacking) == 1 else 'have'} no scale: {label} is in pixels there "
                f"and in µm in {', '.join(scaled)}. Pooling them would mix units, so nothing was pooled",
                next=f"set the objective or calibrate {', '.join(lacking)}, then pool again")
        given = ", ".join(f"{t['id']} ({u or 'no unit'})" for t, u, _l in seen)
        raise DataTableRefused(f"{label} is in different units in {given}, so nothing was pooled",
                               next="pool tables measured in the same unit")


def pool(tables: list[dict]) -> tuple[list[dict], list[dict]]:
    """One table of every source's rows, each keyed "<sourceId>:<memberId>", with the union of their columns."""
    if sum(len(t.get('rows') or []) for t in tables) > MAX_ROWS:
        raise DataTableRefused(f'a pooled table supports at most {MAX_ROWS} rows', next='choose fewer source tables')
    descriptors = {}
    for table in tables:
        for column in table.get('columns') or []:
            key = column['key']
            kind = column.get('type') or 'text'
            if key in descriptors and descriptors[key] != kind:
                raise DataTableRefused(f'{key} has different column types in the selected tables',
                                       next='pool tables with compatible columns')
            descriptors[key] = kind
    if (len(descriptors) + len(OWN) + MAX_COLUMNS + 1) * sum(len(t.get('rows') or []) for t in tables) > MAX_CELLS:
        raise DataTableRefused('the pooled table exceeds the supported cell budget', next='choose fewer source tables')
    check_units(tables)
    columns = [dict(c) for c in OWN]
    seen = {c["key"] for c in columns}
    rows: list[dict] = []
    for table in tables:
        member = MEMBER_KEYS.get(table.get("kind") or "")
        for c in table.get("columns") or []:
            if c["key"] == member or c["key"] in seen:
                continue
            seen.add(c["key"])
            columns.append({"key": c["key"], "label": c.get("label") or c["key"], "unit": c.get("unit") or "",
                            "type": c.get("type") or "text", "origin": "source"})
        for i, r in enumerate(table.get("rows") or []):
            mid = str(r.get(member)) if member and r.get(member) is not None else str(i + 1)
            rows.append({**{k: v for k, v in r.items() if k != member}, "row": row_key(table["id"], mid),
                         "source": table["id"], "member": mid})
    return columns, rows


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def matches(row: dict, where: dict, columns: dict[str, dict]) -> bool:
    """The `where` grammar of population.select: a number column's range ([lo, hi], either None, or a lower bound alone),
    or another column's value (one, or a list of them)."""
    for key, bounds in (where or {}).items():
        column = columns.get(key)
        if column is None:
            raise DataTableRefused(f"there is no column {key!r} in this table", next="choose one of its columns")
        value = row.get(key)
        if column["type"] == "number":
            lo, hi = (list(bounds) + [None, None])[:2] if isinstance(bounds, (list, tuple)) else (bounds, None)
            v = _number(value)
            if v is None:
                return False
            for bound, ok in ((lo, lambda b: v >= b), (hi, lambda b: v <= b)):
                if bound is None:
                    continue
                if _number(bound) is None:
                    raise DataTableRefused(f"{bound!r} is not a number, and {column['label']} is", next="give a range of numbers")
                if not ok(float(bound)):
                    return False
        else:
            wanted = [str(b) for b in (bounds if isinstance(bounds, (list, tuple, set)) else [bounds]) if b is not None]
            if value is None or str(value) not in wanted:
                return False
    return True


def _typed(value, kind: str):
    if value is None or value == "":
        return None
    if kind == "number":
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                raise DataTableRefused(f"{value!r} is not a number", next="give a number for a number column") from None
        number = _number(value)
        if number is None:
            raise DataTableRefused(f"{value!r} is not a number", next="give a number for a number column")
        return number
    return str(value)[:200]


def computed_unit(spec: dict, columns: dict[str, dict]) -> str:
    a = columns[spec["a"]]["unit"]
    if spec["op"] == "ratio":
        b = columns[spec["b"]]["unit"]
        return "" if a == b else f"{a or '1'}/{b or '1'}"
    if spec["op"] == "difference":
        return a
    if spec["op"] == "log10":
        return f"log10 {a}" if a else ""
    return str(spec.get("unit") if spec.get("unit") is not None else a)


def check_computed(spec: dict, columns: dict[str, dict]) -> None:
    op = spec.get("op")
    if op not in COMPUTED:
        raise DataTableRefused(f"a computed column is one of {', '.join(COMPUTED)}", next="choose one of them")
    for side in ("a", "b") if op in ("ratio", "difference") else ("a",):
        key = spec.get(side)
        if key not in columns or columns[key]["type"] != "number":
            raise DataTableRefused(f"{op} takes a number column for {side}, and {key!r} is not one", next="choose a number column")
    if op == "difference" and columns[spec["a"]]["unit"] != columns[spec["b"]]["unit"]:
        raise DataTableRefused(f"{columns[spec['a']]['label']} and {columns[spec['b']]['label']} are in different units, "
                               "so their difference means nothing", next="take a ratio, or the difference of columns in one unit")
    if op == "scale" and _number(spec.get("factor")) is None:
        raise DataTableRefused("scale takes a number factor", next="give the factor, e.g. 1000 for mm to µm")


def compute(spec: dict, row: dict):
    a = _number(row.get(spec["a"]))
    if a is None:
        return None
    op = spec["op"]
    if op == "log10":
        return math.log10(a) if a > 0 else None
    if op == "scale":
        return a * float(spec["factor"])
    b = _number(row.get(spec["b"]))
    if b is None:
        return None
    if op == "difference":
        return a - b
    return a / b if b != 0 else None


def carry(values: dict, excluded: dict, lineage: dict[str, list[str]], keys: set[str]) -> tuple[dict, dict, list[str]]:
    """What the user gave a merged member's parts, given to the member: each column's value where every part that had
    one agrees, and left out only when all its parts were. `lineage`: row key -> its parts' row keys."""
    values, excluded, notes = copy.deepcopy(values), dict(excluded), []
    for key, parts in lineage.items():
        if key not in keys or not parts:
            continue
        mine = values.setdefault(key, {})
        given = {col for p in parts for col in values.get(p, {})}
        for col in sorted(given - set(mine)):
            seen = {json.dumps(values[p][col]) for p in parts if col in values.get(p, {})}
            if len(seen) == 1:
                mine[col] = next(values[p][col] for p in parts if col in values.get(p, {}))
            else:
                notes.append(f"{key} (merged from {', '.join(parts)}): its parts differ in {col}, so it is left empty")
        if not mine:
            values.pop(key)
        if key not in excluded and all(p in excluded for p in parts):
            excluded[key] = f"its parts {', '.join(parts)} were left out: {excluded[parts[0]]}"
    return values, excluded, notes


def build(record: dict, base_columns: list[dict], base_rows: list[dict], lineage: dict | None = None) -> tuple[dict, dict, dict, list[str]]:
    """A version's rows from its sources' rows (`base_*`, pooled) and what the user gave it: (snapshot, values,
    excluded, notes). Pure: the same record and rows make the same snapshot."""
    keys = {r["row"] for r in base_rows}
    values, excluded, notes = carry(record.get("values") or {}, record.get("excluded") or {}, lineage or {}, keys)
    columns = [dict(c) for c in base_columns]
    by_key = {c["key"]: c for c in columns}
    rows = [dict(r) for r in base_rows]
    for added in record.get("added") or []:
        column = {"key": added["key"], "label": added["label"], "unit": added.get("unit") or "",
                  "type": "number" if added["type"] == "number" else "text", "origin": "added", "kind": added["type"]}
        rule = added.get("rule")
        for row in rows:
            if rule:
                value = next((case.get("value") for case in rule.get("cases") or [] if matches(row, case.get("where") or {}, by_key)),
                             rule.get("otherwise"))
            else:
                value = (values.get(row["row"]) or {}).get(added["key"])
            row[added["key"]] = _typed(value, added["type"])
        columns.append(column)
        by_key[column["key"]] = column
    for spec in record.get("computed") or []:
        check_computed(spec, by_key)
        column = {"key": spec["key"], "label": spec["label"], "unit": computed_unit(spec, by_key), "type": "number",
                  "origin": "computed", "op": spec["op"]}
        for row in rows:
            row[spec["key"]] = compute(spec, row)
        columns.append(column)
        by_key[column["key"]] = column
    columns.append(dict(EXCLUDED))
    for row in rows:
        row["excluded"] = excluded.get(row["row"])
    left_out = sum(1 for r in rows if r["excluded"])
    snapshot = {"id": record["id"], "version": record["version"], "columns": columns, "rows": rows,
                "counts": {"rows": len(rows), "included": len(rows) - left_out, "excluded": left_out}}
    if record.get("comparison"):
        snapshot["comparison"] = {**copy.deepcopy(record["comparison"]),
                                  "sourceRevisions": {s["id"]: s["revision"] for s in record["sources"]}}
    return snapshot, values, excluded, notes


def base_of(snapshot: dict) -> tuple[list[dict], list[dict]]:
    """The sources' pooled rows a snapshot was built from (its own columns and the user's taken away)."""
    columns = [c for c in snapshot["columns"] if c.get("origin") == "source"]
    keep = {c["key"] for c in columns}
    return columns, [{k: v for k, v in r.items() if k in keep} for r in snapshot["rows"]]


def snapshot_bytes(snapshot: dict) -> bytes:
    return json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def freshness(record: dict, current: dict | None = None, *, updating: bool = False, failed: str | None = None) -> dict:
    """{state, words}: current (its sources are as it read them), updating (a correction is being read in), stale (a
    source changed or can't be read, and it hasn't followed: "e3 corrected → revision 2 · Update"), or frozen (Freeze
    asked for: it never changes by itself). `current`: source id -> its revision now (None: can't be read); a source
    it doesn't name is taken as unchanged."""
    current = current or {}
    moved = []
    for s in record.get("sources") or []:
        now = current.get(s["id"], s["revision"])
        if now is None:
            moved.append(f"{s['id']} can't be read now")
        elif now != s["revision"]:
            moved.append(f"{s['id']} corrected → revision {now}")
    version = record.get("version")
    if record.get("mode") == "frozen":
        return {"state": "frozen", "words": f"Frozen at version {version}" + (f" · since then {', '.join(moved)}" if moved else "")}
    if updating and moved:
        return {"state": "updating", "words": f"Updating… {', '.join(moved)}"}
    if moved:
        return {"state": "stale", "words": f"{', '.join(moved)}" + (f" ({failed})" if failed else "") + " · Update"}
    read = ", ".join(f"{s['id']} revision {s['revision']}" for s in record.get("sources") or [])
    return {"state": "current", "words": f"Current · {read}" if read else "Current"}


def csv_text(snapshot: dict) -> str:
    """Every row, left-out ones too (their reason in its column), with each column's unit in its header."""
    out = io.StringIO()
    writer = csv.writer(out)
    columns = snapshot["columns"]
    writer.writerow([f"{c['label']} ({c['unit']})" if c.get("unit") else c["label"] for c in columns])
    for row in snapshot["rows"]:
        cells = []
        for c in columns:
            v = row.get(c["key"])
            cells.append("" if v is None else ("yes" if v else "no") if isinstance(v, bool) else repr(v) if isinstance(v, float) else str(v))
        writer.writerow(cells)
    return out.getvalue()


def file_name(record: dict, session: str) -> str:
    words = re.sub(r"[^A-Za-z0-9]+", "-", str(record.get("title") or "")).strip("-")[:48]
    return f"{session}_{record['id']}{('_' + words) if words else ''}_v{record['version']}.csv"


# --- edits: each a change of the record, checked before anything is built ---------------------------------------
def _new_key(label: str, taken: set[str]) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:32] or "column"
    key = key if key[0].isalpha() else "c_" + key
    while key in taken:
        key += "_"
    return key


def _rows_given(edit: dict, keys: set[str]) -> list[str]:
    rows = [str(r) for r in edit.get("rows") or []]
    if not rows:
        raise DataTableRefused("which rows? none was given", next="select the rows first")
    unknown = [r for r in rows if r not in keys]
    if unknown:
        raise DataTableRefused(f"{', '.join(unknown[:5])} {'is' if len(unknown) == 1 else 'are'} not rows of this table",
                               next="select rows the table shows")
    return rows


def apply_edit(record: dict, edit: dict, columns: dict[str, dict], keys: set[str]) -> str:
    """Change `record` (a copy) by one edit; the words for it. `columns` and `keys`: what the version edited shows."""
    op = edit.get("op")
    taken = set(columns)
    added = {a["key"]: a for a in record["added"]}
    if op == "title":
        title = " ".join(str(edit.get("title") or "").split())[:80]
        if not title:
            raise DataTableRefused("give the table a title", next="type a title")
        record["title"] = title
        return f"titled {title}"
    if op == "mode":
        if edit.get("mode") not in MODES:
            raise DataTableRefused("a table follows its sources or is frozen", next="choose follow or frozen")
        record["mode"] = edit["mode"]
        return "frozen" if edit["mode"] == "frozen" else "following its sources again"
    if op == "add-column":
        label = " ".join(str(edit.get("label") or "").split())[:60]
        kind = edit.get("type") or "category"
        if not label or kind not in ADDED_TYPES:
            raise DataTableRefused(f"a new column needs a name and a type ({', '.join(ADDED_TYPES)})", next="name it, e.g. Condition")
        if len(record["added"]) + len(record["computed"]) >= MAX_COLUMNS:
            raise DataTableRefused(f"a table takes at most {MAX_COLUMNS} added columns", next="remove one first")
        key = _new_key(label, taken)
        column = {"key": key, "label": label, "type": kind, "by": "rule" if edit.get("rule") else "rows"}
        if edit.get("rule"):
            rule = edit["rule"]
            if not isinstance(rule, dict) or not isinstance(rule.get("cases", []), list):
                raise DataTableRefused("a rule is {cases: [{where, value}], otherwise}", next="give its cases")
            for case in rule.get("cases") or []:
                if not isinstance(case, dict) or not isinstance(case.get("where") or {}, dict):
                    raise DataTableRefused("each case of a rule is {where, value}", next="give its cases")
                for name in (case.get("where") or {}):
                    if name not in columns:
                        raise DataTableRefused(f"there is no column {name!r} in this table", next="choose one of its columns")
                _typed(case.get("value"), kind)
            _typed(rule.get("otherwise"), kind)
            column["rule"] = {"cases": [{"where": dict(c.get("where") or {}), "value": c.get("value")} for c in rule.get("cases") or []],
                              "otherwise": rule.get("otherwise")}
        record["added"].append(column)
        return f"added {label}" + (" by a rule" if edit.get("rule") else "")
    if op == "set":
        key = edit.get("column")
        if key not in added or added[key].get("rule"):
            raise DataTableRefused(f"{key!r} is not a column you add values to by hand", next="add a column, then set its rows")
        value = _typed(edit.get("value"), added[key]["type"])
        rows = _rows_given(edit, keys)
        for r in rows:
            if value is None:
                record["values"].get(r, {}).pop(key, None)
                if not record["values"].get(r):
                    record["values"].pop(r, None)
            else:
                record["values"].setdefault(r, {})[key] = value
        if sum(len(v) for v in record["values"].values()) > MAX_ASSIGNED:
            raise DataTableRefused(f"a table keeps at most {MAX_ASSIGNED} values set by hand", next="set them by a rule instead")
        return f"{added[key]['label']} {'cleared' if value is None else f'set to {value}'} for {len(rows)} row{'s' if len(rows) != 1 else ''}"
    if op == "exclude":
        reason = " ".join(str(edit.get("reason") or "").split())[:200]
        if not reason:
            raise DataTableRefused("say why these rows are left out", next="give a reason, e.g. out of focus")
        rows = _rows_given(edit, keys)
        for r in rows:
            record["excluded"][r] = reason
        if len(record["excluded"]) > MAX_ASSIGNED:
            raise DataTableRefused(f"a table leaves out at most {MAX_ASSIGNED} rows by hand", next="pool fewer rows instead")
        return f"left out {len(rows)} row{'s' if len(rows) != 1 else ''}: {reason}"
    if op == "include":
        rows = [str(r) for r in edit.get("rows") or []]
        back = [r for r in rows if record["excluded"].pop(r, None) is not None]
        if not back:
            raise DataTableRefused("none of those rows is left out", next=None)  # no next: nothing to put back
        return f"put back {len(back)} row{'s' if len(back) != 1 else ''}"
    if op == "compute":
        label = " ".join(str(edit.get("label") or "").split())[:60]
        if not label:
            raise DataTableRefused("name the computed column", next="e.g. Area per length")
        if len(record["added"]) + len(record["computed"]) >= MAX_COLUMNS:
            raise DataTableRefused(f"a table takes at most {MAX_COLUMNS} added columns", next="remove one first")
        spec = {"key": _new_key(label, taken), "label": label, "op": edit.get("kind") or edit.get("operation")}
        for side in ("a", "b"):
            if edit.get(side) is not None:
                spec[side] = str(edit[side])
        if edit.get("factor") is not None:
            spec["factor"] = _number(edit.get("factor"))
        if edit.get("unit") is not None:
            spec["unit"] = str(edit["unit"])[:20]
        check_computed(spec, columns)
        record["computed"].append(spec)
        return f"computed {label} ({spec['op']})"
    if op == "remove-column":
        key = edit.get("column")
        users = [c["label"] for c in record["computed"] if key in (c.get("a"), c.get("b"))]
        if users:
            raise DataTableRefused(f"{', '.join(users)} {'is' if len(users) == 1 else 'are'} computed from it", next="remove those first")
        before = len(record["added"]) + len(record["computed"])
        record["added"] = [a for a in record["added"] if a["key"] != key]
        record["computed"] = [c for c in record["computed"] if c["key"] != key]
        if before == len(record["added"]) + len(record["computed"]):
            raise DataTableRefused(f"{key!r} is not a column you added", next="a source's own columns stay")
        for r in list(record["values"]):
            record["values"][r].pop(key, None)
            if not record["values"][r]:
                record["values"].pop(r)
        return f"removed {columns[key]['label']}"
    raise DataTableRefused("an edit is one of title, mode, add-column, set, exclude, include, compute, remove-column",
                           next="choose one of them")


# --- the store over the session's journal and the snapshot files ---------------------------------------------------
def copy_record(record: dict) -> dict:
    return {k: copy.deepcopy(v) for k, v in record.items()
            if k not in ('type', 'at', 'snapshot', 'change', 'thread', 'day')}


def publish_snapshot(root: Path, record: dict, snapshot: dict, session: str) -> dict:
    """Bounded immutable publication shared by tables, analyses and graphs; caller runs off-loop."""
    data = snapshot_bytes(snapshot)
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise DataTableRefused('the table exceeds the supported byte budget', next='choose fewer sources or columns')
    relative = f"{session}/{record['id']}/v{record['version']}.json"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.save-', dir=path.parent)
    published = False
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp, path)  # publish exclusively: never replace an accepted revision
            published = True
        except FileExistsError:
            raise DataTableRefused('the table revision already exists on disk',
                                   next='reload the table; preserve its existing saves', status=409) from None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        if published:
            path.unlink(missing_ok=True)
        raise
    finally:
        Path(temp).unlink(missing_ok=True)
    return {"path": relative, "sha256": hashlib.sha256(data).hexdigest()}


class DataTables:
    """The session's dataset tables: made, edited, followed and read. `app` has `hub`, `day` (the open session's store)
    and the snapshot `root` (paths.results / "tables")."""

    def __init__(self, app, root: Path) -> None:
        self.app = app
        self.root = Path(root)
        self._locks: dict[tuple, asyncio.Lock] = {}
        self._timers: dict[tuple, asyncio.TimerHandle] = {}
        self._updating: dict[tuple, asyncio.Task] = {}
        self._dirty: set[tuple] = set()
        self._failed: dict[tuple, str] = {}  # why the last follow of a table could not be done
        self._loop: asyncio.AbstractEventLoop | None = None
        self.debounce = DEBOUNCE_S

    def capture(self, session: str | None = None) -> TableOwner:
        day = self.day
        if session is not None and session != day.sid:
            raise DataTableRefused('This table belongs to another Project', next='reopen its Project', status=409)
        return TableOwner(day, day.sid, getattr(day, 'generation', 0), getattr(day, 'active_id', None))

    # reading the journal
    @property
    def day(self):
        return self.app.day

    def history(self, tid: str) -> list[dict]:
        return list((getattr(self.day, "datatables", {}) or {}).get(tid) or [])

    def latest(self, tid: str) -> dict | None:
        history = self.history(tid)
        return history[-1] if history else None

    def version(self, tid: str, version: int | None = None) -> dict:
        history = self.history(tid)
        if not history:
            raise DataTableRefused(f"{tid} is not a dataset table of this session", next="open its session", status=404)
        if version is None:
            return history[-1]
        found = next((r for r in history if r["version"] == version), None)
        if found is None:
            raise DataTableRefused(f"{tid} has no version {version} (its latest is {history[-1]['version']})",
                                   next="read its latest version", status=404)
        return found

    def ids(self) -> list[str]:
        return list((getattr(self.day, "datatables", {}) or {}))

    # where each source is now
    def revisions_now(self, record: dict) -> dict:
        """Each source's revision now (None: it can't be read now), from the open session's live stores; another
        session's populations by their latest save in its journal."""
        from .resources import resources_of

        hub, out = self.app.hub, {}
        open_sid = getattr(self.day, "sid", None)
        for s in record.get("sources") or []:
            if s["kind"] in ("population", "tracks") and s["day"] == open_sid:
                store = getattr(hub, "evidence" if s["kind"] == "population" else "tracks_store", None)
                item = store.get(s["id"]) if store is not None else None
                out[s["id"]] = None if item is None or getattr(item, "status", "") in ("failed", "unavailable") else int(getattr(item, "revision", 0) or 0)
            elif s["kind"] == "population":
                saved = (resources_of(hub)._journal(s["day"]).get("evidence") or {}).get(s["id"])
                out[s["id"]] = int(saved.get("revision") or 0) if saved else None
            else:
                out[s["id"]] = s["revision"]  # a kept table or motion item never changes once made
        return out

    def freshness(self, record: dict) -> dict:
        tid = record["id"]
        latest = self.latest(tid)
        if latest is not None and latest["version"] != record["version"]:
            return {"state": "stale", "words": f"Version {latest['version']} is newer · Open it"}
        key = self.capture().key(tid)
        task = self._updating.get(key)
        return freshness(record, self.revisions_now(record), failed=self._failed.get(key),
                         updating=key in self._timers or (task is not None and not task.done()))

    # reading a version's rows
    def path_of(self, record: dict) -> Path:
        return self.root / record["snapshot"]["path"]

    def read(self, tid: str, version: int | None = None, *, owner: TableOwner | None = None) -> dict:
        """A version's rows, read back only when its snapshot still has its SHA-256 (off the loop: callers use
        asyncio.to_thread)."""
        owner = owner or self.capture()
        owner.check(self.app)
        record = self.version(tid, version)
        try:
            with self.path_of(record).open('rb') as stream:
                data = stream.read(MAX_SNAPSHOT_BYTES + 1)
            if len(data) > MAX_SNAPSHOT_BYTES:
                raise DataTableRefused('the saved table exceeds the supported byte budget', next='restore a supported table')
        except OSError:
            raise DataTableRefused(f"the rows kept for {tid} version {record['version']} can't be read from disk",
                                   next="restore the session's saves", status=409) from None
        if hashlib.sha256(data).hexdigest() != record["snapshot"]["sha256"]:
            raise DataTableRefused(f"the rows kept for {tid} version {record['version']} are no longer what was saved "
                                   "(their SHA-256 differs), so they aren't shown", next="restore the session's saves", status=409)
        snapshot = json.loads(data)
        owner.check(self.app)
        return snapshot

    # making and changing
    def _lock(self, tid: str, owner: TableOwner) -> asyncio.Lock:
        return self._locks.setdefault(owner.key(tid), asyncio.Lock())

    async def _read_sources(self, sources: list[dict], session: str) -> list[dict]:
        from .tables import TableRefused, read_table

        tables = []
        for s in sources:
            try:
                table = await read_table(self.app, s['id'], session, s['revision'])
                if len(table.get('rows') or []) + sum(len(t.get('rows') or []) for t in tables) > MAX_ROWS:
                    raise DataTableRefused(f'a pooled table supports at most {MAX_ROWS} rows', next='choose fewer source tables')
                tables.append(table)
            except TableRefused as exc:
                raise DataTableRefused(f"{s['id']}: {exc}", next="reopen its session, or pool what is here", status=409) from None
        return tables

    def _current_of(self, eid: str) -> int:
        """A source's revision now, in the open session (a kept table: 0)."""
        hub = self.app.hub
        for name in ("evidence", "tracks_store"):
            store = getattr(hub, name, None)
            item = store.get(eid) if store is not None else None
            if item is not None:
                return int(getattr(item, "revision", 0) or 0)
        return 0

    def _lineage(self, sources: list[dict]) -> dict[str, list[str]]:
        """A merged member's parts, by row key, for every population pooled (read from its members' own record)."""
        hub, out = self.app.hub, {}
        for s in sources:
            if s["kind"] != "population":
                continue
            population = getattr(hub, "evidence", None) and hub.evidence.get(s["id"])
            for m in (population.members.values() if population is not None else ()):
                parts = (m.extras or {}).get("mergedFrom")
                if parts:
                    out[row_key(s["id"], m.id)] = [row_key(s["id"], p) for p in parts]
        return out

    def _write(self, record: dict, snapshot: dict, session: str) -> dict:
        """The snapshot file, written whole before anything names it (off the loop)."""
        return {**publish_snapshot(self.root, record, snapshot, session),
                'rows': snapshot['counts']['rows'], 'excluded': snapshot['counts']['excluded']}

    async def _save(self, record: dict, base_columns: list[dict], base_rows: list[dict], lineage: dict, change: str, owner: TableOwner) -> dict:
        """Build and write a version off the loop, then journal it (durable before success)."""
        owner.check(self.app)
        snapshot, values, excluded, notes = await asyncio.to_thread(build, record, base_columns, base_rows, lineage)
        owner.check(self.app)
        record = {**record, "values": values, "excluded": excluded}
        write = asyncio.create_task(asyncio.to_thread(self._write, record, snapshot, owner.session))
        try:
            record['snapshot'] = await asyncio.shield(write)
        except asyncio.CancelledError:
            # The worker cannot be cancelled halfway through publication. Await only its bounded write,
            # then remove the unaccepted file it created; never leave a misleading accepted revision.
            try:
                pending = await write
            except Exception:
                pass
            else:
                (self.root / pending['path']).unlink(missing_ok=True)
            raise
        record["change"] = "; ".join([change, *notes])[:600]
        try:
            owner.check(self.app)
            return owner.store.record_datatable({**record, 'session': owner.session, 'thread': owner.thread})
        except (OSError, ValueError) as exc:
            try:
                self.path_of(record).unlink()
            except OSError:
                pass
            if isinstance(exc, DataTableRefused):
                raise
            raise DataTableRefused(f"the table was not saved: {exc}", next="resolve the save problem and try again", status=500) from None

    async def create(self, sources: list, *, title: str = "", by: str = "user", mode: str = "follow",
                     edits: list | None = None, owner: TableOwner | None = None) -> dict:
        """A new table pooling `sources` (ids, or {id, revision}) of the open session, at their revisions now."""
        owner = owner or self.capture()
        owner.check(self.app)
        session = owner.session
        given = [s if isinstance(s, dict) else {"id": s} for s in sources or []]
        ids = [str(s.get("id") or "") for s in given]
        if not ids or len(ids) > MAX_SOURCES or len(set(ids)) != len(ids):
            raise DataTableRefused(f"a table pools 1 to {MAX_SOURCES} different tables", next="choose what to pool")
        if mode not in MODES:
            raise DataTableRefused("a table follows its sources or is frozen", next="choose follow or frozen")
        wanted = [{"id": i, "revision": int(s["revision"]) if isinstance(s.get("revision"), int) else self._current_of(i)}
                  for i, s in zip(ids, given)]
        tables = await self._read_sources(wanted, session)
        if mode != 'frozen' and any(t.get('kind') == 'native-measurements' for t in tables):
            raise DataTableRefused('Native measurements use an exact saved revision', next='create a frozen table; measure newer labels explicitly')
        return await self._create_tables(tables, title=title, by=by, mode=mode, edits=edits, owner=owner)

    async def _create_tables(self, tables, *, title, by, mode, edits, owner, comparison=None):
        session = owner.session
        ids = [t['id'] for t in tables]
        base_columns, base_rows = await asyncio.to_thread(pool, tables)
        owner.check(self.app)
        tid = owner.store.new_datatable_id()
        record = {"id": tid, "version": 1, "title": " ".join(str(title).split())[:80] or ", ".join(t.get("title") or t["id"] for t in tables)[:80],
                  "by": by, "mode": mode, "sources": await asyncio.to_thread(lambda: [source_of(t, session) for t in tables]),
                  "added": [], "values": {}, "excluded": {}, "computed": []}
        if comparison is not None:
            record['comparison'] = comparison
        words = [f"pooled {', '.join(ids)}"]
        if edits:
            words += await asyncio.to_thread(self._edited, record, edits, base_columns, base_rows)
        owner.check(self.app)
        async with self._lock(tid, owner):
            return await self._save(record, base_columns, base_rows, self._lineage(record["sources"]), "; ".join(words), owner)

    def _comparison_sources(self, comparison, owner):
        """Revalidate actual producer/input identity, never infer matches from model labels or pixels."""
        owner.check(self.app)
        evidence = getattr(self.app.hub, 'evidence', None)
        expected_input = comparison.get('inputKey')
        if not expected_input:
            raise DataTableRefused('this older comparison has no retained input identity for an update',
                                   next='keep this snapshot and create a new comparison from its original results')
        for arm in comparison['arms']:
            population = evidence.get(arm['id']) if evidence is not None else None
            if (population is None or population.session != owner.session
                    or population.implementation != arm['implementation']):
                raise DataTableRefused('a comparison result no longer belongs to the selected input and method',
                                       next='reopen the saved comparison and choose its retained results')
            actual_input = comparison_input_key(population.capture_id, population.view)
            if actual_input is None or (expected_input is not None and actual_input != expected_input):
                raise DataTableRefused('these comparison lanes use different or unknown retained inputs',
                                       next='compare methods on the same retained input')

    async def from_comparison(self, tab, layout_revision, *, owner=None, title='Comparison measurements'):
        """Pool an exact saved A/B/C Tab through the ordinary retained measurements reader."""
        from . import workspace_views
        owner = owner or self.capture()
        owner.check(self.app)
        if not isinstance(tab, str) or type(layout_revision) is not int or layout_revision < 1:
            raise DataTableRefused('choose an exact saved comparison Tab', next='save the comparison arrangement')
        state = await asyncio.to_thread(workspace_views.read, owner.store, schema=workspace_views.SCHEMA)  # a schema-6 layout too
        owner.check(self.app)
        if state['revision'] != layout_revision:
            raise DataTableRefused('the comparison arrangement changed', next='reopen its saved version', status=409)
        view = next((v for v in state['layout']['views'] if v['id'] == tab), None)
        if view is None or view.get('kind') != 'compare' or not view.get('compare'):
            raise DataTableRefused('choose a saved A/B/C comparison Tab', next='open the retained results as a comparison Tab')
        items = {i['placementId']: i for i in view['items']}
        arms, input_key = [], None
        evidence = getattr(self.app.hub, 'evidence', None)
        for lane in view['compare']['lanes']:
            ref = items[lane['placementId']]['resource']
            population = evidence.get(ref['id']) if evidence is not None else None
            if (population is None or population.session != owner.session or ref['day'] != owner.session
                    or type(ref.get('version')) is not int or ref['version'] < 0
                    or population.revision != ref['version'] or not population.capture_id
                    or population.implementation != lane['impl']):
                raise DataTableRefused('each lane needs an exact retained population from its declared method',
                                       next='retain the population measurements and reopen their exact revisions')
            actual_input = comparison_input_key(population.capture_id, population.view)
            if actual_input is None or (input_key is not None and input_key != actual_input):
                raise DataTableRefused('these comparison lanes use different captured inputs',
                                       next='compare methods on the same retained input')
            input_key = actual_input
            arms.append({'id': ref['id'], 'revision': ref['version'], 'implementation': population.implementation,
                         'capture': population.capture_id, 'method': f"{lane['label']} · {lane['impl']}"})
        if len({a['id'] for a in arms}) != len(arms):
            raise DataTableRefused('the same population appears in more than one lane', next='select distinct retained results')
        comparison = {'session': owner.session, 'tab': tab, 'layoutRevision': layout_revision,
                      'inputKey': input_key, 'arms': arms,
                      'note': 'Same objects, not independent samples. Method groups do not establish matched object pairs.'}
        tables = await self._read_sources(arms, owner.session)
        self._comparison_sources(comparison, owner)
        if any(c['key'] == 'method' for t in tables for c in t['columns']):
            raise DataTableRefused('a source already has a method column', next='review the source column before pooling')
        edits = [{'op': 'add-column', 'label': 'Method', 'type': 'category',
                  'rule': {'cases': [{'where': {'source': arm['id']}, 'value': arm['method']} for arm in arms], 'otherwise': None}}]
        return await self._create_tables(tables, title=title, by='user', mode='frozen', edits=edits,
                                         owner=owner, comparison=comparison)

    def _edited(self, record: dict, edits: list, base_columns: list[dict], base_rows: list[dict]) -> list[str]:
        if not isinstance(edits, list) or not edits or len(edits) > 200:
            raise DataTableRefused("give 1 to 200 edits", next="say what to change")
        words = []
        for edit in edits:
            # Each edit sees the columns the ones before it made (a rule on a column computed just before it).
            snapshot, _v, _e, _n = build(record, base_columns, base_rows)
            columns = {c["key"]: c for c in snapshot["columns"]}
            words.append(apply_edit(record, edit if isinstance(edit, dict) else {}, columns, {r["row"] for r in base_rows}))
        return words

    async def edit(self, tid: str, version: int, edits: list, *, by: str = "user", owner: TableOwner | None = None) -> dict:
        """A new version with `edits`, made from the version the user saw (`version`, refused once a newer one exists),
        on the rows that version kept: an edit never reads the sources again."""
        owner = owner or self.capture()
        owner.check(self.app)
        async with self._lock(tid, owner):
            owner.check(self.app)
            latest = self.version(tid)
            if version != latest["version"]:
                raise DataTableRefused(f"{tid} changed: it is at version {latest['version']} now, not {version}",
                                       next="reload it and edit again", status=409)
            snapshot = await asyncio.to_thread(self.read, tid, version, owner=owner)
            owner.check(self.app)
            base_columns, base_rows = await asyncio.to_thread(base_of, snapshot)
            record = await asyncio.to_thread(copy_record, latest)
            record.update(version=latest["version"] + 1, by=by)
            if record["version"] > MAX_VERSIONS:
                raise DataTableRefused(f"{tid} has {MAX_VERSIONS} versions", next="make a new table from it")
            words = await asyncio.to_thread(self._edited, record, edits, base_columns, base_rows)
            if record['mode'] != 'frozen' and any(source['kind'] == 'native-measurements' for source in record['sources']):
                raise DataTableRefused('Native measurement tables keep their selected revision', next='create another table explicitly from newer saved measurements')
            saved = await self._save(record, base_columns, base_rows, {}, "; ".join(words), owner)
        if saved["mode"] == "follow" and freshness(saved, self.revisions_now(saved))["state"] == "stale":
            self.poke(tid)  # unfrozen while its sources moved on: it follows them now
        return saved

    async def update(self, tid: str, *, by: str = "follow", owner: TableOwner | None = None, version: int | None = None) -> dict:
        """A new version with every source read again at its revision now (Update, and following a correction); the
        same record when nothing changed."""
        owner = owner or self.capture()
        owner.check(self.app)
        async with self._lock(tid, owner):
            owner.check(self.app)
            latest = self.version(tid)
            if version is not None and latest['version'] != version:
                raise DataTableRefused('the table version changed', next='reload it and update again', status=409)
            session = owner.session
            key = owner.key(tid)
            now = self.revisions_now(latest)
            if all(now.get(s["id"]) == s["revision"] for s in latest["sources"]):
                self._failed.pop(key, None)
                return latest
            try:
                if any(s["day"] != session for s in latest["sources"]):
                    raise DataTableRefused(f"{tid}'s sources are in another session", next="open its session to update it", status=409)
                if latest.get('comparison'):
                    self._comparison_sources(latest['comparison'], owner)
                wanted = [{"id": s["id"], "revision": self._current_of(s["id"])} for s in latest["sources"]]
                tables = await self._read_sources(wanted, session)
                base_columns, base_rows = await asyncio.to_thread(pool, tables)
            except DataTableRefused as exc:
                self._failed[key] = str(exc)
                raise
            record = await asyncio.to_thread(copy_record, latest)
            record.update(version=latest["version"] + 1, by=by, sources=await asyncio.to_thread(lambda: [source_of(t, session) for t in tables]))
            owner.check(self.app)
            moved = [f"{n['id']} revision {o['revision']} → {n['revision']}" for o, n in zip(latest["sources"], record["sources"])
                     if o["revision"] != n["revision"]]
            saved = await self._save(record, base_columns, base_rows, self._lineage(record["sources"]),
                                     ("followed " + ", ".join(moved) if moved else "read its sources again"), owner)
            self._failed.pop(key, None)
            return saved

    # following corrections
    def population_changed(self, population) -> None:
        """An evidence listener (app.py): a correction of a population a following table pooled starts its update,
        after `debounce` seconds without another. Callable from any thread."""
        try:
            for tid in self.ids():
                record = self.latest(tid)
                if record is None or record.get("mode") != "follow":
                    continue
                source = next((s for s in record["sources"]
                               if s["id"] == population.id and s["day"] == population.session), None)
                if source is not None and int(getattr(population, "revision", 0) or 0) != source["revision"]:
                    self.poke(tid)
        except Exception:
            pass  # a listener never breaks a correction

    def poke(self, tid: str) -> None:
        owner = self.capture()
        loop = self._loop
        if loop is None:
            try:
                loop = self._loop = asyncio.get_running_loop()
            except RuntimeError:
                return  # no loop: stale, with explicit Update available
        if loop.is_closed():
            return
        try:
            on_loop = asyncio.get_running_loop() is loop
        except RuntimeError:
            on_loop = False
        if on_loop:
            self._schedule(tid, owner)
        else:
            loop.call_soon_threadsafe(self._schedule, tid, owner)

    def _schedule(self, tid: str, owner: TableOwner) -> None:
        try:
            owner.check(self.app)
        except DataTableRefused:
            return
        key = owner.key(tid)
        task = self._updating.get(key)
        if task is not None and not task.done():
            self._dirty.add(key)
            return
        timer = self._timers.pop(key, None)
        if timer is not None:
            timer.cancel()
        self._timers[key] = asyncio.get_running_loop().call_later(self.debounce, self._start, tid, owner)

    def _start(self, tid: str, owner: TableOwner) -> None:
        key = owner.key(tid)
        self._timers.pop(key, None)
        self._updating[key] = asyncio.get_running_loop().create_task(self._follow(tid, owner))

    async def _follow(self, tid: str, owner: TableOwner) -> None:
        key = owner.key(tid)
        try:
            owner.check(self.app)
            record = self.latest(tid)
            if record is not None and record.get('mode') == 'follow':
                await self.update(tid, by='follow', owner=owner)
        except DataTableRefused:
            pass
        except Exception as exc:
            self._failed[key] = f'{type(exc).__name__}'
        finally:
            if key in self._dirty:
                self._dirty.discard(key)
                self._schedule(tid, owner)

    async def settled(self) -> None:
        """Wait until no follow is waiting or running (tests, and a clean stop)."""
        while self._timers or any(not t.done() for t in self._updating.values()):
            await asyncio.sleep(0.01)

    # the wire
    def wire(self, record: dict, snapshot: dict | None = None) -> dict:
        out = {k: v for k, v in record.items() if k not in ("type",)}
        out["freshness"] = self.freshness(record)
        out["versions"] = [r["version"] for r in self.history(record["id"])]
        if snapshot is not None:
            out["columns"], out["rows"], out["counts"] = snapshot["columns"], snapshot["rows"], snapshot["counts"]
            if 'comparison' in snapshot:
                out['comparison'] = snapshot['comparison']
        return out


def datatables_of(app_or_hub) -> DataTables | None:
    hub = getattr(app_or_hub, "hub", app_or_hub)
    found = getattr(hub, "datatables", None)
    return found if isinstance(found, DataTables) else None
