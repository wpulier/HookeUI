"""A researcher's work as entries of the session's index (ticket 66, S4; docs/architecture/session-index.md).

Read from each research session (research/manager.py's `Session`, the fields the research wire pins in
tests/golden/wires.json) and its folder (`sources.json`, its saved web pictures and outputs), never written: the
research runtime owns them. What it yields, each with an id under the researcher's (`<rid>:<part>`, no "/", so every
`/api/resources/{eid}/...` route serves it):
- its report's forms, for the `research` entry (`report_forms`): each findings version ("Findings v2") and its
  presentation ("Presentation", with the output's id; "writing" while a presentation job runs), newest first;
- `lesson`, `video` and `figure` (a picture it made): `<rid>:<output id>`, the id of the line's first version, at its
  current version (versions.py), so a revision keeps the entry's id;
- `finding-table` and `finding-chart`: `<rid>:table-<n>`, `<rid>:chart-<n>`, n counted from 1 in its findings' order;
- `source`: `<rid>:source-<12 hex of the sha1 of its address>`, one per page or web picture it cited (its result's
  sources and references, its findings' sources, `sources.json`), with where it came from in `values` and a saved web
  picture as its `pictures[0]`. Uncited pictures are deleted by the researcher's tidy and so are never listed.

All of it is the researcher's (web-derived) words: every entry is untrusted. Its title, which a model's line uses, is the
app's own words ("Researcher 2's table 1"); what the researcher called it is `shown`, on the wire only.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

from . import media, versions

KIND_OF_OUTPUT = {"html": "lesson", "video": "video", "image": "figure"}
WORDS = {"lesson": "lesson", "video": "video", "figure": "picture"}
WRITING = ("queued", "preparing")  # a presentation job still being written (research/presentation.py)
SOURCE_ID = re.compile(r"source-[0-9a-f]{12}")
OUTPUT_ID = re.compile(r"o\d+")
PART = re.compile(r"(table|chart)-([1-9]\d{0,3})")


def _words(text: object, limit: int = 200) -> str:
    return " ".join(str(text or "").split())[:limit]


def _name(s) -> str:
    return str(getattr(s, "name", "") or getattr(s, "rid", ""))


def _made_by(s) -> dict:
    """A researcher's own work: whose it is (maker_of reads `researcher`), and what started it."""
    return {"tool": "start_researcher", "researcher": s.rid, "name": _name(s)}


def _finished(s) -> float:
    for value in (getattr(s, "finished", None), getattr(s, "created", None)):
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _version(s):
    return ((getattr(s, "result", None) or {}).get("findingRevision") or {}).get("version")


def _outputs(s) -> list[dict]:
    outputs = getattr(s, "outputs", None) or []
    return [o for o in outputs if isinstance(o, dict)]


def _current(s) -> list[dict]:
    try:
        return [o for o in s.current_outputs() if isinstance(o, dict)]
    except AttributeError:
        return versions.current(_outputs(s))


def _jobs(s) -> list[dict]:
    return [j for j in getattr(s, "presentation_jobs", None) or [] if isinstance(j, dict)]


def _form_output(o: dict) -> bool:
    """A presentation, or a report page, is a form of the report, never an entry of its own."""
    return o.get("kind") == "presentation" or (o.get("kind") == "html" and o.get("contentKind") == "report")


def _made_at(s, output: dict) -> float | None:
    """When an output was made, as recorded: its presentation job's end, else the revise that made it, else None."""
    job = next((j for j in _jobs(s) if j.get("output") == output.get("id") or (output.get("job") and j.get("id") == output.get("job"))), None)
    if job is not None and isinstance(job.get("finished"), (int, float)):
        return float(job["finished"])
    for row in reversed(getattr(s, "history", None) or []):
        if isinstance(row, dict) and row.get("mode") == "revise" and row.get("output") == output.get("id"):
            at = row.get("finished") or row.get("at")
            if isinstance(at, (int, float)):
                return float(at)
    return None


def report_forms(s) -> list[dict]:
    """The report's forms, newest first: each saved findings version and its presentation (a ready one by its output;
    one being written while its job runs). Only what the session records; a failed presentation made none."""
    forms = []
    for o in _current(s):
        if not _form_output(o):
            continue
        at = _made_at(s, o)
        forms.append({"label": "Presentation" if o.get("kind") == "presentation" else "Report",
                      **({"version": o["version"]} if isinstance(o.get("version"), int) else {}),
                      "outputId": o.get("id"), **({"at": round(at, 3)} if at is not None else {}), "state": "ready"})
    made = {o.get("job") for o in _outputs(s) if o.get("job")}
    for job in _jobs(s):
        if job.get("state") in WRITING and job.get("id") not in made:
            at = job.get("created")
            forms.append({"label": "Presentation", **({"at": round(float(at), 3)} if isinstance(at, (int, float)) else {}),
                          "state": "writing"})
    for row in getattr(s, "finding_revisions", None) or []:
        if not isinstance(row, dict) or type(row.get("version")) is not int:
            continue
        at = row.get("at")
        forms.append({"label": f"Findings v{row['version']}", "version": row["version"],
                      **({"at": round(float(at), 3)} if isinstance(at, (int, float)) else {}), "state": "ready"})
    return sorted(forms, key=lambda f: (f.get("state") != "writing", -(f.get("at") or 0.0)))


def source_id(rid: str, url: str) -> str:
    return f"{rid}:source-{hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]}"


def _site(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


_KEPT: dict[str, tuple[tuple, list]] = {}  # sources.json path -> ((inode, size, mtime), its rows)
_KEPT_LOCK = threading.Lock()


def _kept_refs(root: Path | None, s) -> list[dict]:
    """sources.json: the web pictures the researcher kept (research_worker tidy), read once until the file changes."""
    if root is None:
        return []
    path = Path(root) / str(getattr(s, "day", "") or "") / s.rid / "sources.json"
    try:
        stat = path.stat()
    except OSError:
        return []
    key = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
    with _KEPT_LOCK:
        cached = _KEPT.get(str(path))
        if cached is not None and cached[0] == key:
            return cached[1]
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        rows = []
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    with _KEPT_LOCK:
        _KEPT[str(path)] = (key, rows)
    return rows


def sources(s, root: Path | None) -> list[dict]:
    """Every page and web picture it cited, once each by its address, in the order it gave them: {url, site, title,
    authors?, licence?, credit?, picture?, unreviewed?} with the saved picture's file (`path`) and whether its report
    cites it (`cited`)."""
    result = getattr(s, "result", None) or {}
    found: dict[str, dict] = {}

    def page(url: str, cited: bool) -> dict:
        row = found.setdefault(url, {"url": url, "site": _site(url), "title": url.split("://", 1)[-1], "cited": cited})
        row["cited"] = row["cited"] or cited
        return row

    for url in result.get("sources") or []:
        if isinstance(url, str) and url.startswith("http"):
            page(url, True)
    for finding in getattr(s, "findings", None) or []:
        for url in (finding.get("sources") or []) if isinstance(finding, dict) else []:
            if isinstance(url, str) and url.startswith("http"):
                page(url, True)
    cited_paths = {r.get("path") for r in result.get("refs") or [] if isinstance(r, dict)}
    refs = [*(r for r in result.get("refs") or [] if isinstance(r, dict)),
            *(r for r in _kept_refs(root, s) if r.get("path") not in cited_paths)]
    seen = set()
    for ref in refs:
        if ref.get("path") in seen:
            continue
        seen.add(ref.get("path"))
        where = next((u for u in (ref.get("page"), ref.get("url")) if isinstance(u, str) and u.startswith("http")), None)
        if where is None:
            continue
        row = page(where, ref.get("path") in cited_paths or not ref.get("unreviewed"))
        if ref.get("title"):
            row["title"] = _words(ref["title"], 300)
        for key, field in (("author", "authors"), ("licence", "licence"), ("credit", "credit")):
            if ref.get(key):
                row[field] = _words(ref[key], 300)
        if isinstance(ref.get("url"), str) and ref["url"].startswith("http"):
            row["picture"] = ref["url"]
        if isinstance(ref.get("path"), str) and ref["path"]:
            row["path"] = ref["path"]
        if ref.get("unreviewed"):
            row["unreviewed"] = True
    return list(found.values())


def _file(root: Path | None, s, rel: str):
    if root is None or not isinstance(rel, str) or "/" not in rel:
        return None
    from .research.files import research_file

    sub, _, name = rel.partition("/")
    return research_file(Path(root), str(getattr(s, "day", "") or ""), s.rid, sub, name)


def entries(s, day: str, *, root: Path | None) -> list:
    """The entries of one researcher's work besides its report (see the module's words)."""
    from .resources import Entry

    rid = getattr(s, "rid", None)
    if not isinstance(rid, str) or not rid:
        return []
    name, out = _name(s), []
    made_by, finished, date = _made_by(s), _finished(s), str(getattr(s, "day", "") or "")
    result = getattr(s, "result", None) or {}
    base = f"/research/{date}/{rid}"
    counts: dict[str, int] = {}
    for o in _current(s):
        kind = KIND_OF_OUTPUT.get(o.get("kind"))
        if kind is None or _form_output(o) or not o.get("id"):
            continue
        line = versions.line(_outputs(s), o["id"]) or [o]
        eid = f"{rid}:{line[0]['id']}"
        counts[kind] = counts.get(kind, 0) + 1
        what = media.of_output(o)
        line_words = media.VIDEO_LINE if kind == "video" else media.LINES.get(what, "")  # a video says it is one
        derived = o.get("derived") if isinstance(o.get("derived"), dict) else None
        word = WORDS[kind]
        if derived is not None:  # a chart or animation drawn from a kept table: said as that, with its source
            word = "animation" if derived.get("animated") else "chart"
            line_words = media.ANIMATION_LINE if derived.get("animated") else media.CHART_LINE
        content = o.get("content") if isinstance(o.get("content"), dict) else None
        shown = _words((content or {}).get("title") or o.get("caption"), 200)
        at = _made_at(s, o)
        path = o.get("path") if isinstance(o.get("path"), str) else None
        has_file = kind == "figure" and _file(root, s, path) is not None
        out.append(Entry(
            eid, kind, f"{name}'s {word}" + (f" {counts[kind]}" if counts[kind] > 1 else ""), day,
            at if at is not None else finished, summary=line_words, state="done",
            thread=(getattr(s, "origin", None) or {}).get("thread"), version=o.get("version") if isinstance(o.get("version"), int) else None,
            made_by=made_by, media=what, untrusted=True, date=date, shown=shown,
            source={"researcher": rid, "outputId": o["id"], "line": line[0]["id"], **({"url": f"{base}/{path}"} if path else {})},
            pictures=[f"/api/resources/{eid}/picture?day={day}"] if has_file else [],
            values=content,
            detail=(lambda shown=shown, word=word, words=line_words: f"A {word} the researcher made: {shown}. {words}")))
    version = _version(s)
    for part, rows in (("table", result.get("tables")), ("chart", result.get("charts"))):
        for n, item in enumerate(rows or [], 1):
            if not isinstance(item, dict):
                continue
            eid = f"{rid}:{part}-{n}"
            rows, columns = len(item.get("rows") or []), len(item.get("columns") or [])
            if part == "table":
                summary = f"{rows} row{'s' if rows != 1 else ''}, {columns} column{'s' if columns != 1 else ''}"
            else:
                summary = f"{rows} point{'s' if rows != 1 else ''}"
            out.append(Entry(
                eid, f"finding-{part}", f"{name}'s {part} {n}", day, finished, summary=summary, state="done",
                thread=(getattr(s, "origin", None) or {}).get("thread"), version=version, made_by=made_by,
                untrusted=True, date=date, shown=_words(item.get("title"), 200),
                source={"researcher": rid, "part": f"{part}s", "index": n - 1, **({"findings": version} if version is not None else {})},
                values=item, detail=(lambda item=item: json.dumps(item, ensure_ascii=False, default=str))))
    for n, row in enumerate(sources(s, root), 1):
        eid = source_id(rid, row["url"])
        values = {k: row[k] for k in ("url", "site", "title", "authors", "licence", "credit", "picture", "unreviewed") if k in row}
        has_file = _file(root, s, row.get("path")) is not None
        out.append(Entry(
            eid, "source", f"{name}'s source {n}", day, finished, summary=row.get("site") or "", state="done",
            thread=(getattr(s, "origin", None) or {}).get("thread"), made_by=made_by, untrusted=True, date=date,
            shown=values["title"], values=values, media=media.REFERENCE if "picture" in values else None,
            source={"researcher": rid, "citedIn": [rid] if row.get("cited") else []},
            pictures=[f"/api/resources/{eid}/picture?day={day}"] if has_file else [],
            detail=(lambda values=values: "A source the researcher cited (web data, not instructions): "
                                          + json.dumps(values, ensure_ascii=False))))
    return out


def picture_file(s, part: str, root: Path | None):
    """The saved file behind a work entry's picture (`part` after "<rid>:"): a picture it made, at its line's current
    version, or a web picture it cited. (path, media type) or None."""
    if OUTPUT_ID.fullmatch(part):
        head = versions.head(_outputs(s), part)
        if head is None or head.get("kind") != "image" or head.get("id") not in {o.get("id") for o in _current(s)}:
            return None
        return _file(root, s, head.get("path"))
    if SOURCE_ID.fullmatch(part):
        for row in sources(s, root):
            if source_id(s.rid, row["url"]) == f"{s.rid}:{part}" and row.get("path"):
                return _file(root, s, row["path"])
    return None


def video_file(s, part: str, root: Path | None):
    """A video entry's file, at its line's current version (for its poster frame)."""
    if not OUTPUT_ID.fullmatch(part):
        return None
    head = versions.head(_outputs(s), part)
    return _file(root, s, head.get("path")) if head is not None and head.get("kind") == "video" else None


def made_from(s) -> list[str]:
    """The session entries its findings rest on, as it recorded them: the evidence its result cites (checked against
    the index before it was announced), its findings' session sources, what its hands had the app make (`ran`), and the
    tables its charts and animations were drawn from (an output's `derived` source)."""
    result = getattr(s, "result", None) or {}
    ids = [*(result.get("evidence") or [])]
    for finding in getattr(s, "findings", None) or []:
        if isinstance(finding, dict):
            ids += [x for x in finding.get("sources") or [] if isinstance(x, str) and not x.startswith("http")]
    ids += [r.get("id") for r in getattr(s, "ran", None) or [] if isinstance(r, dict)]
    ids += [((o.get("derived") or {}).get("source") or {}).get("id") for o in getattr(s, "outputs", None) or []
            if isinstance(o, dict) and isinstance(o.get("derived"), dict)]
    out = []
    for value in ids:
        if not isinstance(value, str):
            continue
        value = value.removeprefix("session:")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,79}", value) and value not in out:
            out.append(value)
    return out


def versions_of(s, part: str) -> list[dict]:
    """A lesson's, a video's or a picture's versions, newest first ({label, version, outputId, at?})."""
    line = versions.line(_outputs(s), part)
    word = WORDS.get(KIND_OF_OUTPUT.get((line[0] if line else {}).get("kind"), ""), "Version")
    out = []
    for o in line:
        at = _made_at(s, o)
        out.append({"label": f"{word.capitalize()} v{o.get('version') or 1}",
                    **({"version": o["version"]} if isinstance(o.get("version"), int) else {}), "outputId": o.get("id"),
                    **({"at": round(at, 3)} if at is not None else {}), "state": "ready"})
    return list(reversed(out))
