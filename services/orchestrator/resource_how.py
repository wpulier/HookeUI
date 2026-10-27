"""How an index entry was made (ticket 66, S5): the `how` of `GET /api/resources/<id>`, the studio's one "How it was
made" view for every kind.

Each part is present only when a record says it, never inferred (ADR 0006): who made it and when (`maker`, `at`), the
words the Assistant was asked (a result's request), the model or method and its settings (`made_by`), what it was made
from (a result's input resources, a population's source picture, a table's sources, a researcher's evidence and what its
hands ran, a chart's points), what was made from it (the same records read the other way), which write-up cites a source
and which sources a write-up cites, and its versions. A link is listed only to an entry this session still has.

    {maker, at, asked?, method?: {name, implementation?}, settings?: [{name, value}], from?: [ref], usedIn?: [ref],
     citedIn?: [ref], sources?: [ref], versions?: [{label, version?, outputId?, at?, state?}]}
    ref = {id, kind, role, title, maker?}
"""
from __future__ import annotations

import json

from . import research_entries as work

SETTING_MAX = 200  # characters of one setting's value, as text when it is not a number, a word or a flag


def _where(impl_id: str) -> str:
    """Where the implementation that ran runs, as the app declares it (names.where_of): this Mac or the cloud."""
    from .names import where_of
    from .operations import OPERATIONS

    for op in OPERATIONS.values():
        found = op.impl(impl_id)
        if found is not None:
            return {"mac": "On this Mac", "cloud": "In the cloud"}.get(where_of(found), "")
    return ""


def ref(entry) -> dict:
    return {"id": entry.id, "kind": entry.kind, "role": entry.role, "title": entry.shown or entry.title,
            **({"maker": dict(entry.maker)} if entry.maker else {})}


def _setting(value):
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text[:SETTING_MAX]


def _sources_of_record(entry, record: dict | None) -> list[str]:
    """The ids an entry says it was made from (its own record only)."""
    source = entry.source if isinstance(entry.source, dict) else {}
    ids = []
    for row in source.get("resources") or []:  # a capability run on saved inputs (run_capability)
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            ids.append(row["id"])
    image = source.get("sourceImage") if isinstance(source.get("sourceImage"), dict) else {}
    if isinstance(image.get("asset"), str):  # outlines found on an asset of this session
        ids.append(image["asset"])
    for row in image.get("resources") or []:  # outlines found on saved inputs
        if isinstance(row, dict):
            ids.append(row.get("id"))
    values = entry.values if isinstance(entry.values, dict) else {}
    if entry.kind == "dataset-table":
        ids += [s.get("id") for s in values.get("sources") or [] if isinstance(s, dict)]
    if entry.kind in ("analysis", "graph") and isinstance(values.get("source"), dict):
        ids.append(values["source"].get("id"))
    if entry.kind == "finding-chart":
        ids += [(row.get("source") or {}).get("id") for row in values.get("rows") or [] if isinstance(row, dict)]
    if entry.kind == "annotation":
        ids.append(source.get("member") or source.get("id"))
    return [i for i in dict.fromkeys(ids) if isinstance(i, str) and i]


def how(index, entry, day: str) -> dict:
    """The entry's `how`, from what its records say; `index` is the session's (resources.Index)."""
    entries, _ = index.collect(day)
    by_id = {e.id: e for e in entries}
    researchers = {getattr(s, "rid", None): s for s in index._researchers(day)}
    record = index.results.get(entry.id, day) if entry.kind == "result" else None
    session, part = (index.work_session(entry.id, day) if ":" in entry.id else (None, None))
    if entry.kind == "research":
        session = researchers.get(entry.id)
    out: dict = {"maker": dict(entry.maker) if entry.maker else None, "at": index._entry_time(entry)}
    made = entry.made_by if isinstance(entry.made_by, dict) else {}

    if (entry.maker or {}).get("kind") == "assistant" and record is not None and str(record.get("request") or "").strip():
        out["asked"] = str(record["request"]).strip()

    method = None
    if entry.kind in ("population", "tracks", "named-outline", "result") and (made.get("implementation") or made.get("operation") or made.get("tool")):
        from .operations import OPERATIONS, label_of
        from .resources import RESULT_TITLES

        operation = OPERATIONS.get(made.get("operation")) if made.get("operation") else None
        if entry.kind == "result":  # what the app calls the tool or capability that made it (its title's words)
            values = entry.values if isinstance(entry.values, dict) else {}
            name = (values.get("name") if made.get("tool") == "run_capability" else None) or RESULT_TITLES.get(made.get("tool"))
        else:
            name = operation.title if operation is not None else made.get("operation")
        if name:
            method = {"name": str(name)}
            if made.get("implementation"):
                method["implementation"] = label_of(made["implementation"])
                where = _where(made["implementation"])
                if where:
                    method["where"] = where
    elif session is not None and getattr(session, "engine", None):
        from .options import RESEARCH_ENGINE

        engine = RESEARCH_ENGINE.get(str(session.engine))
        method = {"name": "Research", "implementation": engine.label if engine is not None else str(session.engine)}
    if method:
        out["method"] = method

    settings = []
    for name, value in (made.get("settings") or {}).items() if isinstance(made.get("settings"), dict) else ():
        settings.append({"name": str(name), "value": _setting(value)})
    params = ((record or {}).get("args") or {}).get("params") if isinstance((record or {}).get("args"), dict) else None
    for name, value in (params.items() if isinstance(params, dict) else ()):
        settings.append({"name": str(name), "value": _setting(value)})
    if settings:
        out["settings"] = settings

    sources_from = _sources_of_record(entry, record)
    if entry.kind == "research" and session is not None:
        sources_from = work.made_from(session)
    made_from = [ref(by_id[i]) for i in sources_from if i in by_id and i != entry.id]
    if made_from:
        out["from"] = made_from

    used = []
    for other in entries:
        if other.id == entry.id:
            continue
        if other.kind == "research":
            researcher = researchers.get(other.id)
            if researcher is not None and entry.id in work.made_from(researcher):
                used.append(other)
        elif entry.id in _sources_of_record(other, None):
            used.append(other)
    if entry.kind in ("finding-table", "finding-chart") and part is not None and session.rid in by_id:
        used.append(by_id[session.rid])  # a table or chart of its findings is in its report
    if used:
        out["usedIn"] = [ref(e) for e in used]

    if entry.kind == "source":
        cited = [by_id[r] for r in (entry.source or {}).get("citedIn") or [] if r in by_id]
        if cited:
            out["citedIn"] = [ref(e) for e in cited]
    if entry.kind == "research" and session is not None:
        root = getattr(session, "root", None) or getattr(index.research, "root", None)
        cited = [by_id[work.source_id(session.rid, row["url"])] for row in work.sources(session, root)
                 if row.get("cited") and work.source_id(session.rid, row["url"]) in by_id]
        if cited:
            out["sources"] = [ref(e) for e in cited]

    if entry.kind == "research" and entry.forms:
        out["versions"] = [dict(f) for f in entry.forms]
    elif entry.kind in ("lesson", "video", "figure") and session is not None:
        found = work.versions_of(session, part)
        if found:
            out["versions"] = found
    return out
