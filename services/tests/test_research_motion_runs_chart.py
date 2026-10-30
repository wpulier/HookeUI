"""A researcher runs motion several times and charts the average speed of each run (Will, 2026-10-30: "IF I ask research
to run a motion model 4 times, and to make diagram of average speed, then its gotta nail that!").

Before: make_chart read one table, one bar per row, so four motion runs (four tracks entries) could only be four
separate charts, or a chart of numbers the model typed. Now make_chart kind "means" reads every run's tracks entry whole
at its revision, the same rows and value in each, and draws one bar per run at its mean with one standard deviation and
every track a dot; `only` keeps the moving tracks. Every number is the tables' own. A stub serves the index's paged
readings as server.py does. No model, network or camera.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from research_worker.researcher import Researcher

DAY = "2026-10-30"
ASKED = "Have a researcher run a motion model 4 times on this and make a diagram of the average speed."
# Four runs of motion: each a tracks entry as resources._tracks lists it (values.tracks: per track net, path, moving).
SPEEDS = {"m1": [(12.0, True), (14.0, True), (0.4, False)], "m2": [(10.0, True), (11.0, True), (13.0, True)],
          "m3": [(20.0, True), (0.2, False)], "m4": [(15.0, True), (16.0, True), (17.0, True), (18.0, True)]}


def _document(eid: str) -> dict:
    tracks = [{"id": f"t{n}", "net": net, "path": net * 1.2, "straightness": 0.8, "moving": moving,
               "segments": [{"epoch": 1, "start": 0, "end": 30, "n": 30}]} for n, (net, moving) in enumerate(SPEEDS[eid], 1)]
    return {"entry": {"id": eid, "kind": "tracks", "title": f"Motion, for Researcher 1 ({eid})", "day": DAY},
            "detail": {"text": f"{eid}: {len(tracks)} tracks", "values": {
                "summary": {"counted": len(tracks), "moving": sum(m for _, m in SPEEDS[eid]), "unit": "px/s"},
                "scale": {}, "tracks": tracks}}}


TEXTS = {eid: json.dumps(_document(eid), sort_keys=True, separators=(",", ":")) for eid in SPEEDS}
REVISIONS = {eid: hashlib.sha256(text.encode()).hexdigest() for eid, text in TEXTS.items()}
PAGE = 300  # small pages, so every table is read across several


class Index(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        eid = url.path.rsplit("/", 1)[-1]
        if eid not in TEXTS:
            return self._send(404, {"error": "nothing by that id"})
        offset = int(query.get("offset", ["0"])[0])
        revision = query.get("revision", [None])[0]
        if revision is not None and revision != REVISIONS[eid]:
            return self._send(409, {"error": "the resource changed; restart reading at offset 0"})
        text = TEXTS[eid]
        end = min(len(text), offset + PAGE)
        return self._send(200, {"entry": {"id": eid}, "text": text[offset:end], "page": {
            "offset": offset, "nextOffset": end if end < len(text) else None, "totalChars": len(text),
            "revision": REVISIONS[eid]}})

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def index_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Index)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def worker(folder, api, effort="standard"):
    events = []
    r = Researcher({"rid": "r1", "dir": str(folder), "brief": ASKED, "effort": effort, "deliverable": "research",
                    "day": DAY, "resources_url": api}, events.append, None)
    return r, events


def test_four_motion_runs_become_one_chart_of_each_runs_mean_speed_with_its_spread(tmp_path, index_api):
    r, events = worker(tmp_path / "r1", index_api)
    reply, picture = asyncio.run(r.call_tool("make_chart", {
        "title": "Average speed of moving things, 4 runs", "kind": "means",
        "tables": [{"id": eid, "revision": REVISIONS[eid]} for eid in SPEEDS],
        "rows": "/detail/values/tracks", "value": "/net", "only": "/moving", "unit": "px/s"}))
    assert reply["ok"], reply
    assert picture  # attached for the model to see
    means = {t["id"]: t for t in reply["tables"]}
    for eid, rows in SPEEDS.items():
        moving = [net for net, m in rows if m]
        assert means[eid]["n"] == len(moving)
        assert means[eid]["mean"] == pytest.approx(statistics.fmean(moving), rel=1e-3)
        assert means[eid]["excluded"] == len(rows) - len(moving)  # still things left out and counted, never imputed
    assert [t["name"] for t in reply["tables"]] == ["Run 1", "Run 2", "Run 3", "Run 4"]
    assert reply["acrossTables"]["tables"] == 4
    assert reply["acrossTables"]["meanOfMeans"] == pytest.approx(
        statistics.fmean(statistics.fmean([n for n, m in rows if m]) for rows in SPEEDS.values()), rel=1e-3)
    output = [e for e in events if e["event"] == "output"][-1]
    assert output["kind"] == "image" and output["generated"] is False
    assert output["derived"]["chart"] == "means" and [s["id"] for s in output["derived"]["sources"]] == list(SPEEDS)
    assert output["content"]["unit"] == "px/s" and output["content"]["only"] == "/moving"
    png = (tmp_path / "r1" / output["path"]).read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_means_chart_is_refused_whole_when_one_runs_table_changed(tmp_path, index_api):
    r, events = worker(tmp_path / "r1", index_api)
    tables = [{"id": eid, "revision": REVISIONS[eid]} for eid in SPEEDS]
    tables[2]["revision"] = "0" * 64
    reply, _ = asyncio.run(r.call_tool("make_chart", {"title": "Speeds", "kind": "means", "tables": tables,
                                                      "rows": "/detail/values/tracks", "value": "/net"}))
    assert "table 3" in reply["error"] and "changed since you read it" in reply["error"]
    assert not [e for e in events if e["event"] == "output"]
    missing, _ = asyncio.run(r.call_tool("make_chart", {"title": "Speeds", "kind": "means",
                                                        "rows": "/detail/values/tracks", "value": "/net"}))
    assert "tables" in missing["error"]


def test_only_keeps_the_rows_where_its_pointer_is_true_in_an_ordinary_chart_too():
    from research_worker import measurement_charts as charts

    s = charts.series(_document("m1"), revision="r", rows="/detail/values/tracks", value="/net", only="/moving")
    assert s.values == [12.0, 14.0] and s.excluded == 1
    with pytest.raises(ValueError, match="only is a JSON pointer"):
        charts.series(_document("m1"), revision="r", rows="/detail/values/tracks", value="/net", only="moving")
    spread = charts.spread([10.0, 11.0, 13.0])
    assert spread["mean"] == pytest.approx(11.33, rel=1e-3) and spread["sd"] == pytest.approx(1.528, rel=1e-3)
    assert charts.spread([20.0])["sd"] == 0.0  # one value: no spread, never an error


def test_the_researcher_is_offered_the_means_chart():
    from research_worker.work import SCHEMAS

    schema = SCHEMAS["make_chart"]["parameters"]
    assert "means" in schema["properties"]["kind"]["enum"] and "tables" in schema["properties"]
    assert "table" not in schema["required"]  # a means chart names tables instead
    assert "means" not in SCHEMAS["animate_measurements"]["parameters"]["properties"]["kind"]["enum"]


def test_every_table_a_means_chart_read_is_what_the_researcher_made_it_from():
    from orchestrator.research_entries import made_from

    s = SimpleNamespace(result={}, findings=[], ran=[{"id": eid} for eid in SPEEDS],
                        outputs=[{"kind": "image", "derived": {"source": {"id": "m1"},
                                                               "sources": [{"id": eid} for eid in SPEEDS]}}])
    assert made_from(s) == list(SPEEDS)


def test_a_title_the_font_cannot_draw_is_drawn_in_its_latin_1_twin():
    from research_worker import measurement_charts as charts

    assert charts.plain("Average net speed — all tracked spots, 4 runs (µm/s)") == \
        "Average net speed - all tracked spots, 4 runs (µm/s)"
    assert charts.plain("“Run” … →") == '"Run" ... ?'
