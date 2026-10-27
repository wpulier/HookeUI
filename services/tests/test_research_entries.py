"""A researcher's work as entries of the session's index (ticket 66, S4 and S5; orchestrator/research_entries.py).

A real research `Session` and its real folder (outputs, sources.json, saved web pictures), read through the real index
and HTTP routes; nothing is changed in the research session. No model, network or camera.
"""
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from orchestrator.produces import ASKED_FOR_ONLY
from orchestrator.research.manager import Session
from orchestrator.research_entries import source_id
from tests.test_resources import app_at, jpeg

PAGE = "https://example.org/papers/cellpose"
PICTURE_PAGE = "https://commons.example.org/wiki/File:Nuclei.jpg"
PICTURE_URL = "https://upload.example.org/nuclei.jpg"
UNCITED_PAGE = "https://example.net/unreviewed"


def png():
    import cv2
    import numpy as np

    return cv2.imencode(".png", np.full((40, 60, 3), 160, np.uint8))[1].tobytes()


def researcher(root, day, sid, rid="r1", n=3, status="done"):
    """A finished researcher with every kind of work: a report in two findings versions, a presentation, a lesson in
    two versions, a video, a picture it made, a table, a chart and three sources (one a saved web picture)."""
    folder = root / day / rid
    (folder / "outputs").mkdir(parents=True)
    (folder / "sources").mkdir()
    (folder / "outputs" / "fig.png").write_bytes(png())
    (folder / "outputs" / "clip.mp4").write_bytes(b"not a real video")
    sha = hashlib.sha1(PICTURE_URL.encode()).hexdigest()
    (folder / "sources" / f"{sha}.jpg").write_bytes(jpeg(90))
    ref = {"path": f"sources/{sha}.jpg", "url": PICTURE_URL, "page": PICTURE_PAGE, "title": "Nuclei under DAPI",
           "licence": "CC BY 4.0", "author": "A. Person", "credit": "A. Person, CC BY 4.0"}
    (folder / "sources.json").write_text(json.dumps([ref, {"path": "sources/gone.jpg", "url": "https://x.example/p.jpg",
                                                          "page": UNCITED_PAGE, "unreviewed": True}]))
    outputs = [
        {"id": "o1", "kind": "html", "path": "outputs/lesson.html", "caption": "Lesson",
         "content": {"kind": "lesson", "title": "Counting nuclei", "steps": [{"id": "q1", "prompt": "?", "answer": "!"}]},
         "version": 1, "replaces": None},
        {"id": "o2", "kind": "presentation", "path": "outputs/o2-presentation.json", "contentKind": "report",
         "caption": "Short report", "version": 1, "replaces": None, "job": "job-ready"},
        {"id": "o3", "kind": "video", "path": "outputs/clip.mp4", "caption": "Dividing cells", "version": 1, "replaces": None},
        {"id": "o4", "kind": "image", "path": "outputs/fig.png", "caption": "Nucleus diagram", "generated": True,
         "version": 1, "replaces": None},
        {"id": "o5", "kind": "html", "path": "outputs/lesson2.html", "caption": "Lesson",
         "content": {"kind": "lesson", "title": "Counting nuclei, shorter", "steps": []}, "version": 2, "replaces": "o1",
         "change": "shorter"},
    ]
    result = {"title": "Bright regions in Human cells", "summary": "271 regions", "sources": [PAGE], "refs": [ref],
              "evidence": ["e1"], "findingRevision": {"version": 2, "digest": "b" * 64, "complete": True},
              "tables": [{"title": "Region areas", "columns": ["region", "area"], "rows": [["1", "59"], ["2", "69"]]}],
              "charts": [{"title": "Areas", "rows": [{"label": "e1", "value": 59, "unit": "px²",
                                                      "source": {"day": sid, "id": "e1", "version": 0}}]}]}
    s = Session(rid=rid, n=n, brief="Measure the bright regions", deliverable="research", effort="standard",
                engine="gemini-3.8-flash", purpose="research", label="bright regions", object_id=None,
                origin={"thread": "t1"}, day=day, status=status, created=100.0, finished=200.0, outputs=outputs,
                findings=[{"kind": "fact", "text": "271 regions", "sources": ["e1", PAGE]}], result=result,
                finding_revisions=[{"version": 1, "digest": "a" * 64, "at": 150.0, "complete": True},
                                   {"version": 2, "digest": "b" * 64, "at": 190.0, "complete": True}],
                presentation_jobs=[{"id": "job-ready", "state": "ready", "output": "o2", "created": 191.0, "finished": 195.0},
                                   {"id": "job-new", "state": "preparing", "created": 210.0},
                                   {"id": "job-failed", "state": "failed", "created": 180.0}],
                ran=[{"id": "e1", "operation": "segment", "run": 0, "at": 120.0}], sitting=sid, root=root)
    return s


def index_with(tmp_path, *sessions):
    web = app_at(tmp_path)
    built = web.state.app
    index = built.hub.resources
    root = tmp_path / "research-root"
    index.research = SimpleNamespace(researchers=lambda day=None, session=None: list(sessions), root=root,
                                     get=lambda rid: next((s for s in sessions if s.rid == rid), None))
    return web, built, index


@pytest.fixture
def work(tmp_path):
    web, built, index = index_with(tmp_path)
    root = tmp_path / "research-root"
    s = researcher(root, index.today(), index.session())
    index.research = SimpleNamespace(researchers=lambda day=None, session=None: [s], root=root, get=lambda rid: s)
    return web, built, index, s


def test_a_researchers_work_has_stable_ids_under_its_own(work):
    _, _, index, s = work
    entries = {e.id: e for e in index.collect()[0]}
    sid = index.session()
    expected = {"r1", "r1:o1", "r1:o3", "r1:o4", "r1:table-1", "r1:chart-1",
                source_id("r1", PAGE), source_id("r1", PICTURE_PAGE), source_id("r1", UNCITED_PAGE)}
    assert expected <= set(entries)
    kinds = {eid: entries[eid].kind for eid in expected}
    assert kinds["r1:o1"] == "lesson" and kinds["r1:o3"] == "video" and kinds["r1:o4"] == "figure"
    assert kinds["r1:table-1"] == "finding-table" and kinds["r1:chart-1"] == "finding-chart"
    assert {kinds[source_id("r1", u)] for u in (PAGE, PICTURE_PAGE, UNCITED_PAGE)} == {"source"}
    assert "r1:o2" not in entries and "r1:o5" not in entries  # a presentation is a form; o5 is o1's second version
    assert all("/" not in eid for eid in expected)
    for eid in expected - {"r1"}:  # every route serves the id, and each is the researcher's, untrusted
        e = entries[eid]
        assert e.untrusted and e.maker == {"kind": "researcher", "id": "r1", "name": "Researcher 3"} and e.day == sid


def test_the_report_is_titled_by_its_result_with_its_forms_newest_first(work):
    _, _, index, _ = work
    report = index.get("r1")
    wire = report.wire()
    assert wire["title"] == "Bright regions in Human cells" and wire["role"] == "writeup"
    assert report.title == "Researcher 3's notes"  # the app's words, what a model's line says
    assert [(f["label"], f.get("state")) for f in wire["forms"]] == [
        ("Presentation", "writing"), ("Presentation", "ready"), ("Findings v2", "ready"), ("Findings v1", "ready")]
    assert wire["forms"][1]["outputId"] == "o2" and wire["forms"][1]["at"] == 195.0  # its job's end, as recorded


def test_a_lesson_keeps_its_id_across_versions_and_lists_them(work):
    _, _, index, _ = work
    lesson = index.get("r1:o1")
    assert lesson.version == 2 and lesson.source["outputId"] == "o5" and lesson.shown == "Counting nuclei, shorter"
    how = index.detail("r1:o1")["how"]
    assert [v["label"] for v in how["versions"]] == ["Lesson v2", "Lesson v1"]
    assert lesson.title == "Researcher 3's lesson" and lesson.media == "lesson"


def test_tables_charts_and_sources_carry_their_contents_and_where_they_came_from(work):
    _, _, index, _ = work
    table = index.document("r1:table-1")
    assert table["detail"]["values"]["columns"] == ["region", "area"] and table["entry"]["summary"] == "2 rows, 2 columns"
    chart = index.get("r1:chart-1")
    assert chart.shown == "Areas" and chart.role == "analysis"
    picture = index.get(source_id("r1", PICTURE_PAGE)).wire()
    assert picture["values"] == {"url": PICTURE_PAGE, "site": "commons.example.org", "title": "Nuclei under DAPI",
                                 "authors": "A. Person", "licence": "CC BY 4.0", "credit": "A. Person, CC BY 4.0",
                                 "picture": PICTURE_URL}
    assert picture["untrusted"] is True and picture["role"] == "source" and len(picture["pictures"]) == 1
    page = index.get(source_id("r1", PAGE)).wire()
    assert page["values"] == {"url": PAGE, "site": "example.org", "title": "example.org/papers/cellpose"}  # no title kept
    assert page["pictures"] == [] and page["source"]["citedIn"] == ["r1"]
    unreviewed = index.get(source_id("r1", UNCITED_PAGE))
    assert unreviewed.values.get("unreviewed") is True and unreviewed.source["citedIn"] == []
    assert unreviewed.pictures == []  # its file was let go by the researcher's tidy: nothing to show


def test_pictures_thumbnails_and_detail_answer_for_the_new_ids(work):
    web, built, index, _ = work
    client = TestClient(web)
    sid = index.session()
    assert client.get(f"/api/resources/r1:o4/picture?day={sid}").content.startswith(b"\x89PNG")
    shot = client.get(f"/api/resources/{source_id('r1', PICTURE_PAGE)}/picture?day={sid}")
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/jpeg"
    for eid in ("r1:o4", source_id("r1", PICTURE_PAGE)):
        thumb = client.get(f"/api/resources/{eid}/thumbnail?day={sid}&v=1")
        assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg", eid
    assert client.get(f"/api/resources/r1:table-1/thumbnail?day={sid}&v=1").status_code == 404  # the kind's icon
    for eid in ("r1:o1", "r1:o3", "r1:o4", "r1:table-1", "r1:chart-1", source_id("r1", PAGE)):
        detail = client.get(f"/api/resources/{eid}?day={sid}")
        assert detail.status_code == 200 and detail.json()["entry"]["id"] == eid and "how" in detail.json(), eid
    assert client.get(f"/api/resources/r1:o99?day={sid}").status_code == 404


def test_how_it_was_made_links_only_recorded_facts(work):
    _, built, index, s = work
    from orchestrator.capture import Capture
    from orchestrator.evidence import evidence_of
    from tests.test_evidence import population

    info = SimpleNamespace(id=7, width=160, height=120, epoch=0, dx=0.0, dy=0.0, fov=None, disc=0.0, wall_at=1000.0)
    p = population(evidence_of(built.hub), Capture(7, jpeg(), info), thread=built.day.active_id)
    p.day = built.day.day
    evidence_of(built.hub).add(p)
    assert p.id == "e1"
    report = index.detail("r1")["how"]
    assert [r["id"] for r in report["from"]] == ["e1"]  # its evidence, as its result cites it
    assert {r["id"] for r in report["sources"]} == {source_id("r1", PAGE), source_id("r1", PICTURE_PAGE)}
    assert report["method"] == {"name": "Research", "implementation": "Gemini 3.8 Flash + Google Search"}
    assert report["maker"]["kind"] == "researcher" and "asked" not in report
    used = index.detail("e1")["how"]["usedIn"]
    assert {r["id"] for r in used} == {"r1", "r1:chart-1"}  # its report, and the chart whose points it gave
    cited = index.detail(source_id("r1", PAGE))["how"]
    assert [r["id"] for r in cited["citedIn"]] == ["r1"]
    table = index.detail("r1:table-1")["how"]
    assert [r["id"] for r in table["usedIn"]] == ["r1"] and "from" not in table  # no source recorded for its rows
    assert "citedIn" not in index.detail(source_id("r1", UNCITED_PAGE))["how"]


def test_new_kinds_are_declared_listed_for_the_studio_and_left_out_of_what_a_model_is_told(work):
    web, built, index, _ = work
    lines = [e.line() for e in index.list()]
    assert not any(":" in line.split(" (", 1)[1].split(")")[0] for line in lines)
    assert "Researcher 3's notes (r1)" in "\n".join(lines)
    summary = index.summary_text()
    for words in ("Tables in a researcher's findings", "Pages and web pictures a researcher cited", "A researcher's lessons"):
        assert words not in summary
    asked = index.list(kind="source")
    assert len(asked) == 3 and all(e.title.startswith("Researcher 3's source") for e in asked)
    assert all("Nuclei under DAPI" not in e.line() for e in asked)  # a source's title is its own words: wrapped, never a line
    client = TestClient(web)
    studio = client.get("/api/resources").json()["entries"]
    assert ASKED_FOR_ONLY <= {e["kind"] for e in studio}
    researcher_view = client.get("/for-researchers/api/resources").json()
    assert not ASKED_FOR_ONLY & {e["kind"] for e in researcher_view["entries"]}
    assert "revision" not in researcher_view and all("role" not in e for e in researcher_view["entries"])
    assert {e["kind"] for e in client.get("/for-researchers/api/resources?kind=source").json()["entries"]} == {"source"}


def test_the_researchers_session_is_only_read(work):
    _, _, index, s = work
    before = json.dumps(s.wire(), sort_keys=True, default=str)
    folder = s.root / s.day / s.rid
    files = sorted((p.name, p.stat().st_mtime_ns) for p in folder.rglob("*") if p.is_file())
    index.collect(include_reads=True, discover_tables=True)
    index.detail("r1")
    assert json.dumps(s.wire(), sort_keys=True, default=str) == before
    assert sorted((p.name, p.stat().st_mtime_ns) for p in folder.rglob("*") if p.is_file()) == files


def test_reconcile_never_places_a_researchers_work_as_windows_nor_counts_it_as_a_completion(work):
    _, built, index, _ = work
    from orchestrator import workspace_views as w

    before = w.read(built.day, schema=w.SCHEMA)
    index.collect(include_reads=True)
    w.reconcile(built.day)
    after = w.read(built.day, schema=w.SCHEMA)
    assert after["layout"] == before["layout"] and after["completions"] == before["completions"] == []


def test_a_researcher_still_working_has_its_work_so_far_and_one_with_nothing_yet_has_none(tmp_path):
    web, built, index = index_with(tmp_path)
    root = tmp_path / "research-root"
    quiet = Session(rid="r2", n=1, brief="look", deliverable="research", effort="quick", engine="gpt-6.1-sol",
                    purpose="research", label="look", object_id=None, origin={}, day=index.today(), status="running",
                    sitting=index.session(), root=root)
    index.research = SimpleNamespace(researchers=lambda day=None, session=None: [quiet], root=root, get=lambda rid: quiet)
    entries = index.collect()[0]
    assert [e.id for e in entries if e.id.startswith("r2")] == ["r2"]
    assert index.get("r2").wire()["forms"] == [] and index.get("r2").wire()["title"] == "Researcher 1's notes"
