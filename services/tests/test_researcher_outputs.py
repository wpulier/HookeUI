"""Researchers make real outputs from the Project's data (Part 2.5, PR 3; the 2026-10-28 voice log).

Asked to "create an animation" of the measurements table, Researcher 3 asked Veo for an eight-second video of the
photograph; the video model was unavailable and the user heard "I can't see the exact error". Here: the word
"animation" decides nothing; a research researcher is offered a chart and an animation drawn by the app from the
table's own values, read whole at an exact revision through the app's own index; the output is kept with the
researcher like its other work, so it is an entry of the session's Data and its picture reopens; a video that fails
says the model and the provider's status. A stub serves the index's routes and paging as server.py does. No model,
network or camera.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from orchestrator.research import policy
from research_worker.researcher import Researcher

DAY = "2026-10-28"
ASKED = "now can you create a researcher that looks at the data on the table and creates an animation of it"
random.seed(7)
MEMBERS = [{"id": f"c{i}", "instance": i, "box": [0, 0, 4, 4], "extras": {"areaPx": round(random.gauss(400, 80), 1),
                                                                            "elongation": round(random.uniform(1, 2), 3)},
            "status": "dividing" if i % 9 == 0 else "ok"} for i in range(1, 342)]
MEMBERS.append({"id": "c342", "instance": 342, "box": [0, 0, 4, 4], "extras": {}, "status": "ok"})  # no area: left out
DOCUMENT = {"entry": {"id": "e3", "kind": "population", "title": "Cell outlines · 18:04", "day": DAY, "version": 1},
            "detail": {"text": "e3: 341 nuclei by Cellpose-SAM", "values": {"count": 341},
                       "columns": {"measure": ["areaPx", "elongation"]}, "members": MEMBERS}}
TEXT = json.dumps(DOCUMENT, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
REVISION = hashlib.sha256(TEXT.encode()).hexdigest()
PAGE = 8000  # server.py's page


class Index(BaseHTTPRequestHandler):
    """The index's paged JSON reading of one entry, as server.py serves it to a researcher."""
    reads: list[str] = []

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        Index.reads.append(self.path)
        if url.path != "/api/resources/e3":
            return self._send(404, {"error": "nothing by that id"})
        offset = int(query.get("offset", ["0"])[0])
        revision = query.get("revision", [None])[0]
        if revision is not None and revision != REVISION:
            return self._send(409, {"error": "the resource changed; restart reading at offset 0"})
        end = min(len(TEXT), offset + PAGE)
        return self._send(200, {"entry": DOCUMENT["entry"], "text": TEXT[offset:end], "page": {
            "offset": offset, "nextOffset": end if end < len(TEXT) else None, "totalChars": len(TEXT),
            "revision": REVISION, "encoding": "JSON text fragments; concatenate in offset order"}})

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
    Index.reads = []
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def worker(folder, api=None, **spec):
    events = []
    r = Researcher({"rid": "r1", "dir": str(folder), "brief": ASKED, "effort": "quick", "deliverable": "research",
                    "day": DAY, **({"resources_url": api} if api else {}), **spec}, events.append, None)
    return r, events


# --- the log's request, reproduced ------------------------------------------------------------------------------------
def test_the_word_animation_neither_chooses_nor_admits_a_video():
    """Before: the keyword list matched "animat*" to a video and admitted one on that word alone. Now no word list
    decides: a video starts only on the model's quote of the user's words, and the effort's checks stand."""
    with pytest.raises(policy.NeedsTheirWords):
        policy.admit("start", words=ASKED, effort="standard", deliverable="video", mode="keywords")
    with pytest.raises(policy.NeedsTheirWords):
        policy.admit("start", words=ASKED, effort="standard", deliverable="video")
    from orchestrator.options import ADMIT

    assert list(ADMIT) == ["quote"]
    # A generated video stays an explicit choice the user names, kept with its budget and consent checks.
    asked = "make me a short video of the cells dividing"
    assert policy.admit("start", words=asked, effort="standard", deliverable="video", asked_with=asked).deliverable == "video"
    with pytest.raises(policy.ResearchRefused, match="cannot be made at quick effort"):
        policy.admit("start", words=asked, effort="quick", deliverable="video", asked_with=asked)
    # Charting or animating the table is research: admitted at quick effort without a video's consent.
    assert policy.admit("start", words=ASKED, effort="quick", deliverable="research", asked_with=ASKED).deliverable == "research"


def test_a_research_researcher_is_offered_the_chart_and_the_animation_never_the_video(tmp_path, index_api):
    r, _ = worker(tmp_path / "r1", index_api)
    tools = r.tool_names()
    assert {"make_chart", "animate_measurements"} <= set(tools) and "make_video" not in tools
    standard, _ = worker(tmp_path / "r2", index_api, effort="standard")
    assert {"make_chart", "animate_measurements"} <= set(standard.tool_names())
    # Nothing to chart without the session's index; a follow-up answer and a description make nothing new.
    alone, _ = worker(tmp_path / "r3")
    assert not {"make_chart", "animate_measurements"} & set(alone.tool_names())
    reply, _ = asyncio.run(alone.call_tool("animate_measurements", {"title": "x", "table": {"id": "e3"}, "rows": "/detail/members",
                                                                    "value": "/extras/areaPx"}))
    assert "index can't be read here" in reply["error"]
    answer, _ = worker(tmp_path / "r4", index_api, deliverable="answer")
    assert not {"make_chart", "animate_measurements"} & set(answer.tool_names())
    prompt = r.system_prompt()
    assert "animate_measurements" in prompt and "generated video is a different thing" in prompt


def test_an_animation_of_the_measurements_table_is_drawn_from_the_table_and_kept_as_its_work(tmp_path, index_api):
    from PIL import Image

    root = tmp_path / "research"
    r, events = worker(root / DAY / "r1", index_api)
    reply, picture = asyncio.run(r.call_tool("animate_measurements", {
        "title": "Nucleus areas, smallest to largest", "table": {"id": "e3"}, "rows": "/detail/members",
        "value": "/extras/areaPx", "label": "/id", "group": "/status", "kind": "histogram"}))
    assert reply["ok"] and reply["animated"] and reply["rows"] == 341 and reply["excluded"] == 1, reply
    assert picture[:2] == b"\xff\xd8"  # the whole chart, for the model to see
    assert len([p for p in Index.reads if "/api/resources/e3" in p]) == -(-len(TEXT) // PAGE)  # every page, once
    # Its file: an animated picture, frames built row by row, the source in the file itself.
    moving = Image.open(r.dir / reply["output"])
    assert moving.is_animated and moving.n_frames == reply["frames"] > 2
    assert "e3" in moving.info["Description"] and "not a video of the specimen" in moving.info["Description"]
    # Its values are the table's own, in the order drawn, at the exact revision read.
    output = next(e for e in events if e["event"] == "output")
    content = output["content"]
    areas = sorted(m["extras"]["areaPx"] for m in MEMBERS if "areaPx" in m["extras"])
    assert [row["value"] for row in content["values"]] == areas
    assert content["source"] == {"id": "e3", "revision": REVISION, "day": DAY, "title": "Cell outlines · 18:04", "version": 1}
    assert content["kind"] == "animation" and content["total"] == 342 and content["excluded"] == 1
    assert output["derived"]["source"]["revision"] == REVISION and output["derived"]["animated"]
    assert output["generated"] is False and "not a video of the specimen" in output["caption"]
    assert any(e["event"] == "looked" and e["id"] == "e3" for e in events)  # what it looked at, with the revision

    # Kept with the researcher like its other work: an entry of the session's Data, its picture reopens.
    from fastapi.testclient import TestClient
    from orchestrator.research.manager import ResearchManager, Session
    from tests.test_research import FakeHub
    from tests.test_research_entries import index_with

    manager = ResearchManager(FakeHub(), root=root)
    s = Session(rid="r1", n=3, brief=ASKED, deliverable="research", effort="quick", engine="gpt-6.1-sol",
                purpose="research", label="measurements", object_id=None, origin={"thread": "t1"}, day=DAY,
                status="running", created=100.0, root=root)
    manager.sessions["r1"] = s
    manager._event(output)
    kept = s.outputs[-1]
    assert kept["derived"]["source"]["id"] == "e3" and kept["path"] == reply["output"]
    s.status, s.finished = "done", 200.0
    web, built, index = index_with(tmp_path, s)
    index.research.root = root
    s.day, s.sitting = index.today(), index.session()  # the open session's folder, as the app files it
    (root / s.day).mkdir(exist_ok=True)
    (root / DAY / "r1").rename(root / s.day / "r1")
    entry = index.get("r1:" + kept["id"])
    assert entry.kind == "figure" and entry.title == "Researcher 3's animation" and entry.media == "derived"
    assert "not a video of the specimen" in entry.summary
    from orchestrator.research_entries import made_from

    assert "e3" in made_from(s)  # How it was made: the table it was drawn from (resource_how.py)
    with TestClient(web) as client:
        shown = client.get(entry.pictures[0])  # the entry's own picture address
        assert shown.status_code == 200 and shown.headers["content-type"] == "image/png"
        assert Image.open(io.BytesIO(shown.content)).is_animated


def test_a_scripted_research_run_animates_the_table_and_finishes_with_it(tmp_path, index_api):
    """The whole run as the sims play it (FakeEngine's `charts`): a research researcher at quick effort, asked for an
    animation of the measurements table, finishes with that animation among its work, nothing generated, nothing spent
    on it."""
    from research_worker.engines import FakeEngine

    r, events = worker(tmp_path / "r1", index_api, fake={"seconds": 0.01, "charts": [{
        "tool": "animate_measurements", "title": "Nucleus areas", "table": {"id": "e3"}, "rows": "/detail/members",
        "value": "/extras/areaPx", "kind": "sorted"}]})
    asyncio.run(r.run(FakeEngine()))
    outputs = [e for e in events if e["event"] == "output"]
    assert len(outputs) == 1 and outputs[0]["derived"]["animated"] and outputs[0]["generated"] is False
    assert events[-1]["event"] == "done" and events[-1]["status"] == "done"
    assert not any(e["event"] == "cost" and (e.get("charge") or {}).get("model", "").startswith("veo") for e in events)


def test_a_chart_reads_only_the_revision_it_names_and_never_imputes(tmp_path, index_api):
    r, events = worker(tmp_path / "r1", index_api)
    stale, _ = asyncio.run(r.call_tool("make_chart", {"title": "Areas", "table": {"id": "e3", "revision": "0" * 64},
                                                      "rows": "/detail/members", "value": "/extras/areaPx"}))
    assert "changed since you read it" in stale["error"]
    wrong, _ = asyncio.run(r.call_tool("make_chart", {"title": "Areas", "table": {"id": "e3"}, "rows": "/detail/members",
                                                      "value": "/extras/perimeter"}))
    assert "no row has a number" in wrong["error"]
    made, _ = asyncio.run(r.call_tool("make_chart", {"title": "Elongation", "table": {"id": "e3", "revision": REVISION},
                                                     "rows": "/detail/members", "value": "/extras/elongation",
                                                     "order": "descending"}))
    assert made["ok"] and made["rows"] == 341 and made["excluded"] == 1
    output = [e for e in events if e["event"] == "output"][-1]
    values = [row["value"] for row in output["content"]["values"]]
    assert values == sorted(values, reverse=True) and output["derived"]["animated"] is False
    assert output["content"]["kind"] == "chart"


def test_when_the_animation_cannot_be_drawn_the_chart_is_made_and_that_is_said(tmp_path, index_api, monkeypatch):
    from research_worker import measurement_charts

    monkeypatch.setattr(measurement_charts, "ready", lambda: "this picture library can't write animated pictures")
    r, events = worker(tmp_path / "r1", index_api)
    reply, _ = asyncio.run(r.call_tool("animate_measurements", {"title": "Areas", "table": {"id": "e3"},
                                                                "rows": "/detail/members", "value": "/extras/areaPx"}))
    assert reply["ok"] and reply["animated"] is False
    assert "couldn't be animated here (this picture library can't write animated pictures), so this is the chart instead" in reply["note"]
    output = next(e for e in events if e["event"] == "output")
    assert "so this is the chart instead" in output["caption"] and output["content"]["kind"] == "chart"
    assert any(e["event"] == "step" and e.get("kind") == "warning" and "drew the chart instead" in e["text"] for e in events)


def test_a_video_the_provider_refuses_says_the_model_and_its_status(tmp_path, monkeypatch):
    """Live 2026-10-28: "It was a third-party video generation service ... I can't see the exact error message"."""
    import google.genai as genai

    class NotFound(Exception):
        code, status = 404, "NOT_FOUND"
        message = "models/veo-3.1-fast-generate-preview is not found for API version v1beta, or is not supported"

    class Client:
        def __init__(self, **kwargs):
            async def generate_videos(**kwargs):
                raise NotFound(NotFound.message)
            self.aio = SimpleNamespace(models=SimpleNamespace(generate_videos=generate_videos))

    monkeypatch.setattr(genai, "Client", Client)
    r, events = worker(tmp_path / "r1", "http://127.0.0.1:1", effort="standard", deliverable="video")
    reply, _ = asyncio.run(r.call_tool("make_video", {"motion": "the nuclei grow", "seconds": 8}))
    said = "the video model was unavailable (Veo 3.1 Fast, HTTP 404, NOT_FOUND: models/veo-3.1-fast-generate-preview is not found"
    assert reply["error"].startswith(said) and "animate_measurements" in reply["error"]
    card = [e for e in events if e["event"] == "step" and e.get("kind") == "warning"]
    assert card and card[-1]["text"].startswith("The video model was unavailable (Veo 3.1 Fast, HTTP 404, NOT_FOUND")
    assert r.budget.spent == 0 and not [e for e in events if e["event"] == "output"]
