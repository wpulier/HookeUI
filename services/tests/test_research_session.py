"""A researcher reads the session's one index (scaffolding Amendment 2, point 7; research_worker/session_index.py)
through the app's own read-only HTTP API, against a stub serving the routes and shapes orchestrator/resources.py
serves: the summary, a listing, one entry in full and its picture."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import cv2
import numpy as np
import pytest

from tests.test_population import world  # noqa: F401  (the fixture)

from orchestrator.research import ResearchManager
from research_worker.session_index import SessionIndex
from tests.test_research import FakeHub
from tests.test_research_content import researcher

DAY = "2026-10-07"
E3 = {"id": "e3", "kind": "population", "title": "Cell outlines · 18:04", "day": DAY, "at": 1791410651.2, "time": "18:04:11",
      "thread": "t1", "state": "done", "placement": "12 of 14 verified in place",
      "madeBy": {"operation": "cells", "implementation": "cellpose-sam", "execution": "x41", "origin": "brain-default",
                 "policy": "route"},
      "source": {"frame": 812, "seq": 9120, "boot": "b1"}, "summary": "14 instances inside the eyepiece disc",
      "actions": ["show", "colour", "query"], "pictures": ["/api/resources/e3/picture?n=0"]}
DETAIL = {"text": "e3: 14 instances by Cellpose-SAM on the view of 18:04:11. Area median 51 µm².",
          "values": {"count": 14}, "columns": {"measure": ["area", "elongation"]},
          "members": [{"id": "c1", "area": 49.0}, {"id": "c2", "area": 53.5}]}
PNG = cv2.imencode(".png", np.full((24, 32, 3), 120, np.uint8))[1].tobytes()


class Stub(BaseHTTPRequestHandler):
    seen: list[str] = []
    e5_version = 1  # e5's revision now (a test corrects it)

    def log_message(self, *args):  # quiet
        pass

    def do_GET(self):  # noqa: N802 - http.server's name
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        Stub.seen.append(self.path)
        if query.get("day", [DAY])[0] != DAY:
            return self._send(400, {"error": "bad day"})
        if url.path == "/api/resources/summary":
            return self._send(200, {"day": DAY, "kinds": [{"kind": "population", "name": "Cell outlines", "count": 1,
                                                          "newest": "Cell outlines · 18:04"}]})
        if url.path == "/api/resources":
            if query.get("kind", [""])[0] == "result":  # a busy day: 400 results, oldest first, as Index.list gives them
                return self._send(200, {"day": DAY, "entries": [
                    {"id": f"x{i}", "kind": "result", "title": f"Count · {i}", "at": 1791400000.0 + i, "summary": "y" * 60}
                    for i in range(400)]})
            entries = [E3] if not query.get("kind") or query["kind"][0] == "population" else []
            return self._send(200, {"day": DAY, "entries": entries})
        if url.path == "/api/resources/away":
            self.send_response(302)
            self.send_header("Location", "http://example.org/elsewhere")
            return self.end_headers()
        if url.path == "/api/resources/e3":
            return self._send(200, {"entry": E3, "detail": DETAIL})
        if url.path == "/api/resources/e5":  # a population at its revision 1
            return self._send(200, {"entry": {**E3, "id": "e5", "title": "Cell outlines · 18:20", "version": Stub.e5_version},
                                    "detail": DETAIL})
        if url.path == "/api/resources/e6/picture":  # not a picture the researcher can read
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            return self.wfile.write(b"not a picture")
        if url.path == "/api/resources/e3/picture":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            return self.wfile.write(PNG)
        return self._send(404, {"error": "no such entry"})

    def _send(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def app_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    Stub.seen = []
    Stub.e5_version = 1
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_a_researcher_reads_the_session_index_through_the_apps_own_api(tmp_path, app_api):
    r, _ = researcher(tmp_path, {"effort": "quick", "resources_url": app_api, "day": DAY})
    assert "session" in r.tool_names()
    summary, _ = asyncio.run(r.call_tool("session", {"action": "summary"}))
    assert summary["ok"] and "Cell outlines" in summary["data"]
    listing, _ = asyncio.run(r.call_tool("session", {"action": "list", "kind": "population"}))
    assert '"e3"' in listing["data"]
    read, picture = asyncio.run(r.call_tool("session", {"action": "read", "id": "e3"}))
    assert picture is None and read["id"] == "e3"
    assert read["data"].startswith('<session source="session" untrusted="true">')  # data, never instructions
    assert "Area median 51" in read["data"] and "18:04:11" in read["data"] and '"c2"' in read["data"]
    assert "e3" in r.session_read and r.progress() >= 1  # a new entry read counts as progress (the stall rule)
    shown, jpeg = asyncio.run(r.call_tool("session", {"action": "picture", "id": "e3"}))
    assert shown["ok"] and jpeg[:2] == b"\xff\xd8"  # a PNG snapshot comes to the model as JPEG
    gone, _ = asyncio.run(r.call_tool("session", {"action": "read", "id": "e9"}))
    assert "no entry 'e9'" in gone["error"]
    assert all(f"day={DAY}" in path for path in Stub.seen)  # scoped to the researcher's own day


def test_without_the_apps_address_there_is_no_session_tool_and_it_never_leaves_this_machine(tmp_path):
    r, _ = researcher(tmp_path, {"effort": "quick"})
    assert "session" not in r.tool_names()
    elsewhere, _ = researcher(tmp_path / "b", {"effort": "quick", "resources_url": "http://example.org", "day": DAY})
    assert "session" not in elsewhere.tool_names()
    with pytest.raises(ValueError):
        SessionIndex("https://10.0.0.5:8770", DAY)
    describing, _ = researcher(tmp_path / "c", {"effort": "describe", "purpose": "description", "resources_url": "http://127.0.0.1:1"})
    assert "session" not in describing.tool_names()  # about a kind of thing, not this session


def test_paged_read_requires_contiguous_evidence_and_new_pages_count_as_progress(tmp_path, monkeypatch):
    r, _ = researcher(tmp_path, {"effort": "quick", "resources_url": "http://127.0.0.1:1", "day": DAY})
    revision = "v1"

    def page(entry_id, offset=0, expected=None):
        return {"entry": E3, "text": "abcde", "page": {
            "offset": offset, "revision": revision, "totalChars": 15,
            "nextOffset": offset + 5 if offset < 10 else None}}

    monkeypatch.setattr(r.session_index, "read_page", page)

    def read(offset=0):
        return asyncio.run(r.call_tool("session", {"action": "read", "id": "e3", "offset": offset,
                                                   "revision": revision if offset else None}))[0]

    first = read()
    initial = r.progress()
    assert first["next"] == {"action": "read", "id": "e3", "offset": 5, "revision": "v1"}
    assert "e3" not in r.session_read
    read(10)  # skipping the middle does not count as reading the complete record
    assert "e3" not in r.session_read
    read(5)
    assert r.progress() > initial
    read(10)
    assert "e3" in r.session_read
    complete = r.progress()
    read(10)
    assert r.progress() == complete  # replaying a page is not new progress
    revision = "v2"
    read()
    assert "e3" not in r.session_read  # old completion cannot certify a newer revision


def test_an_index_that_does_not_answer_is_said_and_the_researcher_carries_on(tmp_path):
    r, events = researcher(tmp_path, {"effort": "quick", "resources_url": "http://127.0.0.1:9", "day": DAY})
    result, _ = asyncio.run(r.call_tool("session", {"action": "summary"}))
    assert "could not be read" in result["error"]
    for args in ({"action": "read", "id": "e3"}, {"action": "picture", "id": "e3"}):
        asyncio.run(r.call_tool("session", args))
    assert not [e for e in events if e.get("event") == "looked"]  # nothing read, nothing claimed (#166 review SF6)


def test_asked_about_the_cells_we_outlined_a_researcher_cites_e3_and_when_it_was_made(tmp_path, app_api, monkeypatch):
    """Amendment 2's acceptance, with the scripted engine: it reads e3 through `session` and its finding
    cites e3, its title and when it was made, from what the tool returned."""
    from tests.test_research import until

    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "fake")

    async def go():
        manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: DAY)
        manager.resources_url = app_api
        session = await manager.start("what can you tell me about the cells we outlined?", effort="quick",
                                      fake={"seconds": 0.2, "session": [{"action": "list", "kind": "population"},
                                                                        {"action": "read", "id": "e3"}]})
        await until(lambda: session.status == "done")
        manager.stop()
        return session

    session = asyncio.run(go())
    summary = session.result["summary"]
    assert "From the session: e3 (Cell outlines · 18:04, made 18:04:11)" in summary
    assert any("/api/resources/e3" in path for path in Stub.seen)


def test_a_busy_listing_keeps_the_newest_whole_entries_and_says_what_it_left_out(tmp_path, app_api):
    r, _ = researcher(tmp_path, {"effort": "quick", "resources_url": app_api, "day": DAY})
    listing, _ = asyncio.run(r.call_tool("session", {"action": "list", "kind": "result"}))
    body = listing["data"].split("\n", 1)[1].rsplit("\n", 1)[0]
    data = json.loads(body.split(" [", 1)[0])
    ids = [e["id"] for e in data["entries"]]
    assert ids[0] == "x399" and ids == sorted(ids, key=lambda i: -int(i[1:]))  # newest first, whole entries
    assert listing["olderLeftOut"] == 400 - len(ids) > 0 and "older entries left out" in listing["data"]


def test_the_index_is_never_followed_off_this_machine_and_a_missing_picture_is_said_as_such(tmp_path, app_api):
    from research_worker.session_index import IndexUnavailable

    index = SessionIndex(app_api, DAY)
    with pytest.raises(IndexUnavailable):  # a redirect elsewhere is refused, not followed
        index._get("/api/resources/away")
    r, _ = researcher(tmp_path, {"effort": "quick", "resources_url": app_api, "day": DAY})
    missing, _ = asyncio.run(r.call_tool("session", {"action": "picture", "id": "e9"}))
    assert "has no picture 0" in missing["error"]


def test_odd_ids_and_numbers_are_refused_in_plain_words(tmp_path, app_api):
    r, _ = researcher(tmp_path, {"effort": "quick", "resources_url": app_api, "day": DAY})
    slash, _ = asyncio.run(r.call_tool("session", {"action": "read", "id": "e3/picture"}))
    assert "is not an entry's id" in slash["error"]
    bad_n, _ = asyncio.run(r.call_tool("session", {"action": "picture", "id": "e3", "n": "first"}))
    assert bad_n["error"] == "n is a picture's number: 0 for the first"


# --- a finding's evidence, looked up in the session before it is announced (research_worker/evidence.py) ------
def test_evidence_ids_are_resolved_and_a_point_on_missing_evidence_is_marked_unavailable():
    from research_worker import evidence
    from research_worker.session_index import IndexUnavailable, NoEntry

    class Index:
        def read(self, entry_id):
            if entry_id == "e3":
                return {"entry": E3}
            if entry_id == "r2":
                raise IndexUnavailable("the app did not answer")
            raise NoEntry(entry_id)

    assert evidence.ids_of(["e3", "e3", "bad id/", "", "r2"]) == ["e3", "r2"]
    result = {"evidence": ["e3", "e9", "r2"],
              "session_claims": evidence.session_claims_of([{"point": "  the cells   are round ", "evidence": ["e3"]},
                                                            {"point": "the odd one moved", "evidence": ["e3", "e9"]},
                                                            {"point": "the lesson says so", "evidence": ["r2"]},
                                                            {"point": "no evidence given", "evidence": []}])}
    evidence.apply(result, evidence.resolve(result["evidence"], Index()))
    assert [(e["id"], e["found"]) for e in result["evidence"]] == [("e3", True), ("e9", False), ("r2", None)]
    assert result["evidence"][0]["title"] == "Cell outlines · 18:04" and result["evidence"][0]["time"] == "18:04:11"
    assert [(c["point"], c["found"]) for c in result["session_claims"]] == [
        ("the cells are round", True), ("the odd one moved", False), ("the lesson says so", None)]
    assert result["session_claims"][1]["missing"] == ["e9"]
    line = evidence.summary_line(result)
    assert "1 of 2 checked entries were found in the session's index; not found: e9" in line
    assert "1 cited entry could not be checked" in line and "1 of 3 points rest on evidence not found" in line
    nowhere = {"evidence": ["e3"]}
    evidence.apply(nowhere, evidence.resolve(["e3"], None))  # no address: not checked, said
    assert nowhere["evidence"][0]["found"] is None and "not available" in nowhere["evidence"][0]["why"]


def test_a_finding_on_the_session_is_checked_there_before_it_is_announced(tmp_path, app_api, monkeypatch):
    from tests.test_research import until

    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "fake")

    async def go():
        manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: DAY)
        manager.resources_url = app_api
        session = await manager.start("are the cells we outlined round?", effort="quick", fake={
            "seconds": 0.2, "session": [{"action": "read", "id": "e3"}],
            "session_claims": [{"point": "their median area is 51 µm²", "evidence": ["e3"]},
                               {"point": "they were bigger this morning", "evidence": ["e1"]}]})
        await until(lambda: session.status == "done")
        manager.stop()
        return session

    session = asyncio.run(go())
    found = {e["id"]: e["found"] for e in session.result["evidence"]}
    assert found == {"e3": True, "e1": False}
    assert [c["found"] for c in session.result["session_claims"]] == [True, False]
    text = session.readable()
    assert "Evidence it used from the session: e3 (Cell outlines · 18:04, 18:04:11); e1 (not found in the session's index)." in text
    assert "- from the session: their median area is 51 µm² (e3)" in text
    assert "- evidence unavailable: they were bigger this morning (e1)" in text



def test_an_index_that_does_not_answer_is_said_as_not_checked_and_stops_the_lookups():
    from research_worker import evidence
    from research_worker.session_index import IndexUnavailable

    calls = []

    class Down:
        def read(self, entry_id):
            calls.append(entry_id)
            raise IndexUnavailable("the app did not answer")

    result = {"evidence": ["e1", "e2", "e3"], "session_claims": [{"point": "they are round", "evidence": ["e1"]}]}
    evidence.apply(result, evidence.resolve(result["evidence"], Down()))
    assert calls == ["e1"]  # the first time it does not answer, the rest are not looked up
    assert [e["found"] for e in result["evidence"]] == [None, None, None]
    line = evidence.summary_line(result)
    assert "0 of" not in line and "3 cited entries could not be checked (the session's index did not answer" in line
    assert evidence.note(result) == " Note: some of the session's records it cites could not be checked."
    many = [f"e{i}" for i in range(40)]

    class Up:
        def read(self, entry_id):
            return {"entry": {"id": entry_id}}

    resolved = evidence.resolve(many, Up())
    assert sum(r["found"] is True for r in resolved.values()) == evidence.MAX_RESOLVED
    assert "only the first" in resolved["e39"]["why"]
    ticks = iter([0.0, 0.0, 9.0, 9.0, 9.0])
    timed = evidence.resolve(["e1", "e2", "e3"], Up(), clock=lambda: next(ticks))
    assert timed["e1"]["found"] is True and "ran out of time" in timed["e3"]["why"]


def test_a_point_on_missing_evidence_is_said_with_the_result_and_in_its_line(tmp_path):
    from orchestrator.announcer import checked_note
    from orchestrator.research import Session

    result = {"title": "t", "summary": "s", "one_liner": "They grew since the morning.", "significance": "low",
              "evidence": [{"id": "e1", "found": False}],
              "session_claims": [{"point": "they grew since the morning", "evidence": ["e1"], "found": False}]}
    assert "1 of its points rest on evidence not found in the session's index, for example: they grew" in checked_note(result)
    session = Session(rid="r3", day=DAY, n=3, brief="b", deliverable="", effort="quick", engine="fake", purpose="research",
                      label="", object_id=None, origin={}, status="done", result=result)
    assert "(1 of its points rest on evidence not found in the session)" in session.line()


def test_finish_names_the_sessions_records_only_for_a_researcher_with_the_session_tool(tmp_path, app_api):
    with_index, _ = researcher(tmp_path, {"effort": "quick", "resources_url": app_api, "day": DAY})
    without, _ = researcher(tmp_path / "b", {"effort": "quick"})
    assert "evidence" in with_index.schema("finish")["parameters"]["properties"]
    assert "evidence" not in without.schema("finish")["parameters"]["properties"]
    assert "session_claims" not in without.schema("finish")["parameters"]["properties"]


def working_session(manager=None, rid="r4", day=DAY):
    from orchestrator.research import Session

    session = Session(rid=rid, day=day, n=4, brief="b", deliverable="", effort="quick", engine="gpt-6.1-sol",
                      purpose="research", label="", object_id=None, origin={"channel": "chat"}, status="running")
    if manager is not None:
        session.revision_now = manager._revision_now
        manager.sessions[session.rid] = session
    return session


def test_what_it_looked_at_is_its_working_set_once_each_in_the_indexs_own_words(tmp_path, app_api):
    """The mission card's working set (the partner §2.2): each record it read or looked at, once each way, with
    the index's title, kind and the revision it read (C1), kept on the researcher, read by the brain, shown on its
    card and kept across a restart; only what worked is claimed (a missing record, a picture that can't be read)."""
    r, events = researcher(tmp_path, {"effort": "quick", "resources_url": app_api, "day": DAY})
    for args in ({"action": "read", "id": "e3"}, {"action": "read", "id": "e3"}, {"action": "picture", "id": "e3"},
                 {"action": "read", "id": "e9"}, {"action": "picture", "id": "e6"}, {"action": "read", "id": "e5"}):
        asyncio.run(r.call_tool("session", args))  # twice, a picture, one not there, a picture unreadable, a revision
    looked = [e for e in events if e.get("event") == "looked"]
    assert [(e["id"], e["how"], e["title"], e["kind"], e["version"]) for e in looked] == [
        ("e3", "read", "Cell outlines · 18:04", "population", None), ("e3", "picture 0", "", "", None),
        ("e5", "read", "Cell outlines · 18:20", "population", 1)]

    Stub.e5_version = 2  # corrected since: read again, it is another record of what it read
    asyncio.run(r.call_tool("session", {"action": "read", "id": "e5"}))
    again_read = [e for e in events if e.get("event") == "looked"][len(looked):]
    assert [(e["id"], e["version"]) for e in again_read] == [("e5", 2)]

    manager = ResearchManager(FakeHub(), root=tmp_path / "research", today=lambda: DAY)
    session = working_session(manager)
    for e in looked + looked:  # the same twice: kept once
        manager._event({**e, "rid": "r4"})
    assert [(w["id"], w["how"], w["version"], w["run"]) for w in session.working] == [
        ("e3", "read", None, 0), ("e3", "picture 0", None, 0), ("e5", "read", 1, 0)]
    assert session.wire()["working"][0]["title"] == "Cell outlines · 18:04"
    assert "It looked at: Cell outlines · 18:04 (e3), 1 picture; Cell outlines · 18:20 (e5)." in session.readable()
    manager._save(session)
    again = ResearchManager(FakeHub(), root=tmp_path / "research", today=lambda: DAY).get("r4")
    assert again is not None and [w["id"] for w in again.working] == ["e3", "e3", "e5"]  # kept across a restart


def test_what_it_had_the_app_make_is_the_apps_own_record_never_said_as_looked_at(tmp_path):
    """The executor's review of #166, B2 (ADR 0006): a run returns an id and a title, never the contents, so what the
    app made for it is "it had the app make", from the app's own record (`ran`), and never "it looked at"."""
    session = working_session()
    session.ran.append({"id": "e7", "operation": "segment", "run": 0, "at": 10.0, "count": 6,
                        "title": "Cell outlines, for Researcher 4 · 18:10", "kind": "population", "version": 0})
    text = session.readable()
    assert "It had the app make: Cell outlines, for Researcher 4 · 18:10 (e7)." in text and "It looked at" not in text


def test_a_record_corrected_since_it_was_read_is_said_so(tmp_path, world):
    """B3 (Amendment 1, C1): what it read keeps the revision it read; when the population has been corrected
    since, the brain and the card are told, for today's records only."""
    from orchestrator.population import correct
    from tests.test_population import cells

    population, _ = cells(world)
    manager = ResearchManager(world, root=tmp_path / "research", today=lambda: population.day)
    session = working_session(manager, day=population.day)
    manager._event({"event": "looked", "rid": "r4", "id": population.id, "how": "read", "title": population.title,
                    "kind": "population", "version": population.revision})
    assert "corrected since" not in session.readable()
    correct(world, population, "remove", [population.live()[0].id], by="user")
    assert f"({population.id}, read at revision 0, corrected since (now 1))" in session.readable()
    assert session.wire()["working"][0]["now"] == 1
    manager._event({"event": "looked", "rid": "r4", "id": population.id, "how": "read", "title": population.title,
                    "kind": "population", "version": population.revision})  # read again, as it is now
    assert [w["version"] for w in session.working] == [0, 1] and "corrected since" not in session.readable()
    other_day = working_session(manager, rid="r5", day="2026-10-01")
    manager._event({"event": "looked", "rid": "r5", "id": population.id, "how": "read", "title": "t", "kind": "population",
                    "version": 0})
    assert "corrected since" not in other_day.readable()  # another day's e-numbers are not today's


def test_the_working_set_comes_after_the_apps_qualifiers_and_each_run_is_said_apart():
    """SF1: a cut never parts a point from "evidence unavailable"; SF3: a follow-up's reads are not told as part of the
    original result."""
    session = working_session()
    session.result = {"summary": "Mostly normal red cells.",
                      "session_claims": [{"point": "Most are round", "evidence": ["e3"], "found": False}]}
    session.working = [{"id": "e3", "how": "read", "title": "Cell outlines · 18:04", "kind": "population", "run": 0, "at": 1.0},
                       {"id": "e12", "how": "read", "title": "Cell outlines · 19:30", "kind": "population", "run": 1, "at": 2.0}]
    text = session.readable()
    assert text.index("evidence unavailable") < text.index("It looked at")
    assert "It looked at for its result: Cell outlines · 18:04 (e3)." in text
    assert "It looked at for follow-up 1: Cell outlines · 19:30 (e12)." in text


def test_the_working_set_keeps_the_newest_counts_the_rest_and_checks_what_it_keeps(tmp_path):
    """SF2, SF4 and the notes: the newest WORKING_MAX are kept and the ones let go counted (on adding and on loading);
    the brain is told the newest; a bad id or way is never kept; a broken saved record is dropped when read."""
    from orchestrator.research.manager import WORKING_MAX, WORKING_SHOWN

    manager = ResearchManager(FakeHub(), root=tmp_path / "research", today=lambda: DAY)
    session = working_session(manager)
    for i in range(WORKING_MAX + 5):
        manager._event({"event": "looked", "rid": "r4", "id": f"e{i}", "how": "read", "title": f"T{i}", "kind": "population"})
    for bad in ({"id": "e3 img src=x", "how": "read"}, {"id": "e3", "how": "everything"}, {"id": "", "how": "read"}):
        manager._event({"event": "looked", "rid": "r4", **bad})
    assert len(session.working) == WORKING_MAX and session.working_dropped == 5 and session.working[0]["id"] == "e5"
    line = session.working_lines()[0]
    assert line.startswith(f"It looked at: T{WORKING_MAX + 5 - WORKING_SHOWN} ") and line.endswith(
        f"T{WORKING_MAX + 4} (e{WORKING_MAX + 4}); and {WORKING_MAX - WORKING_SHOWN} more.")
    assert session.working_lines()[-1] == "(5 earlier records it looked at are not listed.)"
    manager._save(session)
    path = tmp_path / "research" / DAY / "r4" / "session.json"
    data = json.loads(path.read_text())
    data["working"] += [{"id": f"e{100 + i}", "how": "read", "title": "", "kind": "", "at": 9e9 + i} for i in range(3)]
    data["working"] += [{"id": "e99"}, {"how": "read", "at": 1.0}, "x", {"id": "e3 <b>", "how": "read", "at": 1.0}]
    path.write_text(json.dumps(data))  # an older or hand-edited file: too many, and some it can't use
    again = ResearchManager(FakeHub(), root=tmp_path / "research", today=lambda: DAY).get("r4")
    assert len(again.working) == WORKING_MAX and again.working[-1]["id"] == "e102" and again.working_dropped == 8
    assert again.readable() and again.wire()


def test_a_picture_only_record_takes_its_title_from_the_store_that_keeps_it_never_a_full_index_read(tmp_path, monkeypatch):
    """SF5 and the re-check's SF-a: the research process gives no title for a picture it looked at; the title is the
    one the app's own store keeps for that id (a held clip, a result), never found by reading the whole index on the
    event loop; else the kind in plain words, never "an entry"."""
    from orchestrator.held import HeldClip, held_of
    from orchestrator.resources import Index, Results

    def no_full_read(*a, **k):
        raise AssertionError("the whole index was read on the event loop")

    monkeypatch.setattr(Index, "collect", no_full_read)
    hub = FakeHub()
    held_of(hub).clips["k1"] = HeldClip("k1", "r4", "Researcher 4", "q1", 15, DAY, started_at=1791410000.0)
    day = SimpleNamespace(day=DAY, sid=DAY, date=DAY, results={})  # the day's store, as far as results go (any day the test runs on)
    day.record_result = lambda entry: day.results.__setitem__(entry["id"], entry)
    hub.resources = Index(hub, results=Results(None, day=day))
    made = hub.resources.results.add("enhance", {}, "averaged 8 real frames")
    manager = ResearchManager(hub, root=tmp_path / "research", today=lambda: DAY)
    session = working_session(manager)
    for eid, kind in (("k1", ""), (made["id"], ""), ("k2", "clip"), ("b2", ""), ("snapshot-181004-f12", ""),
                      ("r1817001", ""), ("x9", "")):
        manager._event({"event": "looked", "rid": "r4", "id": eid, "how": "picture 0", "title": "", "kind": kind})
    assert [(w["title"].split(" · ")[0], w["kind"]) for w in session.working] == [
        ("Held footage", "clip"), ("Clearer picture", "result"), ("", "clip"), ("", "bookmark"), ("", "snapshot"),
        ("", "research"), ("", "")]
    text = session.readable()
    assert "a held clip (k2), 1 picture" in text and "a bookmark (b2), 1 picture" in text  # the kind its id's form says
    assert "a snapshot (snapshot-181004-f12)" in text and "a researcher's work (r1817001)" in text
    assert "a record (x9), 1 picture" in text  # a form the index doesn't use: no kind made up


def test_the_manager_files_each_record_with_its_run_and_keeps_its_title_one_plain_line(tmp_path):
    """SF3 at the source: a record looked at during a follow-up is filed with that run; and a title is the index's,
    made one plain line before it is kept (never markup or a second line)."""
    manager = ResearchManager(FakeHub(), root=tmp_path / "research", today=lambda: DAY)
    session = working_session(manager)
    manager._event({"event": "looked", "rid": "r4", "id": "e3", "how": "read", "title": 'Cells\n<b>"x"</b>', "kind": "population"})
    session.history.append({"mode": "answer", "brief": "how big are they?", "status": "done"})
    manager._event({"event": "looked", "rid": "r4", "id": "e12", "how": "read", "title": "Cell outlines · 19:30", "kind": "population"})
    assert [(w["id"], w["run"]) for w in session.working] == [("e3", 0), ("e12", 1)]
    title = session.working[0]["title"]
    assert "\n" not in title and "<" not in title and '"' not in title and title.startswith("Cells")


def test_a_follow_ups_reads_are_filed_with_it_while_it_runs_and_said_by_its_question(tmp_path):
    """The re-check's B1: an answer files its history entry only when it finishes, so what a follow-up reads while it
    runs belongs to it, never to the result it follows; each later run is said by the question it answered."""
    from orchestrator.research.run import Run

    manager = ResearchManager(FakeHub(), root=tmp_path / "research", today=lambda: DAY)
    session = working_session(manager)
    session.result = {"summary": "Mostly normal red cells."}
    manager._event({"event": "looked", "rid": "r4", "id": "e3", "how": "read", "title": "Cell outlines · 18:04", "kind": "population"})
    session.run = Run("answer", "done", "quick", "b", question="how big are they?")
    manager._event({"event": "looked", "rid": "r4", "id": "e12", "how": "read", "title": "Cell outlines · 19:30", "kind": "population"})
    assert [(w["id"], w["run"]) for w in session.working] == [("e3", 0), ("e12", 1)]
    text = session.readable()
    assert "It looked at for its result: Cell outlines · 18:04 (e3)." in text
    assert 'It looked at for the later question "how big are they?": Cell outlines · 19:30 (e12).' in text
    session.history.append(session.run.finish("done", {"summary": "about 7 µm"}, []))
    session.run = None
    assert "It looked at for its result: Cell outlines · 18:04 (e3)." in session.readable()  # an answer is no newer result
    session.run = Run("answer", "done", "quick", "b", question="and the white cells?")
    manager._event({"event": "looked", "rid": "r4", "id": "e20", "how": "read", "title": "Cell outlines · 20:00", "kind": "population"})
    assert session.working[-1]["run"] == 2 and 'for the later question "and the white cells?": Cell outlines · 20:00 (e20).' in session.readable()
    session.history.append(session.run.finish("done", {"summary": "lymphocytes"}, []))
    session.run = Run("answer", "done", "quick", "b",
                      question='what is the "biggest" cell compared with the ones next to it,\nand is it a neutrophil or a lymphocyte?')
    manager._event({"event": "looked", "rid": "r4", "id": "e21", "how": "read", "title": "Cell outlines · 20:10", "kind": "population"})
    assert ('for the later question "what is the biggest cell compared with the ones next to it, and is it a neutroph…": '
            "Cell outlines · 20:10 (e21).") in session.readable()  # one line, its own quotes out, cut with a mark


def test_what_it_had_the_app_make_corrected_since_is_said_as_made_and_a_broken_run_record_is_dropped(tmp_path, world):
    """The re-check's SF-b: a population it had the app make and the user corrected since is "made at revision 0,
    corrected since", in what the brain reads and on the wire the card reads; and a hand-edited run record that
    the readers could not use is dropped when read back."""
    from orchestrator.population import correct
    from tests.test_population import cells

    population, _ = cells(world)
    manager = ResearchManager(world, root=tmp_path / "research", today=lambda: population.day)
    session = working_session(manager, day=population.day)
    session.ran.append({"id": population.id, "operation": "segment", "run": 0, "at": 1.0, "title": population.title,
                        "kind": "population", "version": population.revision})
    correct(world, population, "remove", [population.live()[0].id], by="user")
    assert f"({population.id}, made at revision 0, corrected since (now 1))" in session.readable()
    assert session.wire()["ran"][0]["now"] == 1
    session.ran.append({"id": "e9", "operation": "segment", "run": "x", "at": 2.0})
    session.ran.append({"id": "e10", "operation": "segment", "run": 0, "at": "soon"})  # a time that isn't one
    session.ran.append({"id": "e11; and more", "operation": "segment", "run": 0, "at": 3.0})  # not an id
    session.ran.append({"id": 12, "operation": "segment", "run": 0, "at": 4.0})
    manager._save(session)
    again = ResearchManager(world, root=tmp_path / "research", today=lambda: population.day).get("r4")
    assert [r["id"] for r in again.ran] == [population.id] and again.readable()


def test_a_point_on_a_record_corrected_since_the_researcher_read_it_is_said_so():
    """The partner's Amendment 1, C1: what a researcher read is recorded with its version (a population's
    revision); a finding resting on an entry corrected since is announced as based on what it was then."""
    from research_worker import evidence

    class Index:
        def __init__(self, revision):
            self.revision = revision

        def read(self, entry_id):
            return {"entry": {**E3, "version": self.revision}}

    result = {"evidence": ["e3"],
              "session_claims": evidence.session_claims_of([{"point": "six cells, one elongated", "evidence": ["e3"]}])}
    evidence.apply(result, evidence.resolve(["e3"], Index(1), seen={"e3": [0]}))  # read at revision 0, corrected to 1
    claim = result["session_claims"][0]
    assert claim["found"] is True and claim["corrected"] == [{"id": "e3", "read": [0], "now": 1}]
    assert evidence.note(result) == (" Note: 1 of its points rest on records corrected since it read them "
                                     "(e3: read at revision 0, now 1): based on what they were then.")
    both = {"evidence": ["e3"], "session_claims": evidence.session_claims_of([{"point": "x", "evidence": ["e3"]}])}
    evidence.apply(both, evidence.resolve(["e3"], Index(1), seen={"e3": [0, 1]}))  # read before and after: said both
    assert "(e3: read at revisions 0 and 1, now 1)" in evidence.note(both)
    assert "1 of 1 points rest on records corrected since it read them" in evidence.summary_line(result)
    same = {"evidence": ["e3"], "session_claims": evidence.session_claims_of([{"point": "x", "evidence": ["e3"]}])}
    evidence.apply(same, evidence.resolve(["e3"], Index(1), seen={"e3": [1]}))  # read after the correction only: nothing to say
    assert "corrected" not in same["session_claims"][0] and evidence.note(same) == ""
    unread = {"evidence": ["e3"], "session_claims": evidence.session_claims_of([{"point": "x", "evidence": ["e3"]}])}
    evidence.apply(unread, evidence.resolve(["e3"], Index(1)))  # never read it: no version to compare, nothing claimed
    assert "corrected" not in unread["session_claims"][0]


def test_the_researcher_records_the_version_it_listed_or_read(tmp_path, app_api, monkeypatch):
    """Through the session tool: each entry's version when it listed or read it (what its points rest on)."""
    monkeypatch.setitem(E3, "version", 2)
    r, _ = researcher(tmp_path, {"effort": "quick", "resources_url": app_api, "day": DAY})
    asyncio.run(r.call_tool("session", {"action": "list", "kind": "population"}))
    assert r.session_seen == {"e3": [2]}
    monkeypatch.setitem(E3, "version", 3)
    asyncio.run(r.call_tool("session", {"action": "read", "id": "e3"}))
    assert r.session_seen == {"e3": [2, 3]}  # every version it read


def test_the_index_gives_a_populations_revision_as_its_version(world):
    """The app's index: a population's entry carries its revision (a correction makes a new one, M2.3)."""
    from types import SimpleNamespace

    from orchestrator.resources import Index
    from tests.test_population import cells

    population, _ = cells(world)
    index = Index(world, day=SimpleNamespace(day=population.day, sid=population.day, date=population.day))
    assert index.get(population.id).wire()["version"] == 0
    population.revision = 1
    assert index.get(population.id).wire()["version"] == 1


def test_a_finding_on_a_corrected_record_is_said_so_when_announced_and_when_read(tmp_path):
    """The announcement (checked_note) and the brain's read of the researcher (readable) both say it."""
    from orchestrator.announcer import checked_note
    from orchestrator.research import Session

    result = {"title": "t", "summary": "s", "one_liner": "Six cells, one elongated.", "significance": "low",
              "evidence": [{"id": "e3", "found": True, "title": "Cell outlines · 18:04", "corrected": {"read": [0], "now": 1}}],
              "session_claims": [{"point": "six cells, one elongated", "evidence": ["e3"], "found": True,
                                  "corrected": [{"id": "e3", "read": [0], "now": 1}]}]}
    assert "rest on records corrected since it read them (e3: read at revision 0, now 1)" in checked_note(result)
    session = Session(rid="r3", day=DAY, n=3, brief="b", deliverable="", effort="quick", engine="fake", purpose="research",
                      label="", object_id=None, origin={}, status="done", result=result)
    assert "from the session as it was then, corrected since (e3 read at 0, now 1): six cells, one elongated" in session.readable()


def test_after_a_continue_each_run_is_said_by_its_own_question(tmp_path):
    """The re-check's B1 for "continue": a continue files its entry (the run before it) as it starts, so its own
    question is kept there; what each run read is said by the question it answered, and the first result as the
    first once a continue has made a newer one. An answer after it is said by its own question too."""
    from tests.test_mission_manager_interleavings import ControlledWorker

    manager = ResearchManager(FakeHub(), root=tmp_path / "research", today=lambda: DAY)
    manager._pump = lambda: None  # launch each queued attempt below through the real dispatch boundary
    manager._worker = ControlledWorker(manager)  # synthetic external transport; no worker process
    session = working_session(manager)
    session.brief = "what are these cells?"
    session.result = {"title": "Red cells", "summary": "Mostly normal red cells."}
    looked = lambda eid, title: manager._event({"event": "looked", "rid": "r4", "id": eid, "how": "read",  # noqa: E731
                                                "title": title, "kind": "population"})
    looked("e3", "Cell outlines · 18:04")
    session.status, session.finished = "done", 1.0
    async def go():
        try:
            await manager.followup("r4", "look closer at the nuclei", mode="continue")
            await manager._launch(session)
            looked("e12", "Cell outlines · 19:30")
            manager._event({"event": "done", "rid": "r4", "status": "done", "result": {"summary": "The nuclei are round."}})
            await manager.followup("r4", "how big are they?", mode="answer")
            await manager._launch(session)
            looked("e20", "Cell outlines · 20:00")
        finally:
            manager.stop()
    asyncio.run(go())
    lines = session.working_lines()
    assert "It looked at for its first result: Cell outlines · 18:04 (e3)." in lines
    assert 'It looked at for the later question "look closer at the nuclei": Cell outlines · 19:30 (e12).' in lines
    assert 'It looked at for the later question "how big are they?": Cell outlines · 20:00 (e20).' in lines
    session.history[0].pop("next")  # a continue saved before its question was kept: said by its number
    assert "It looked at for follow-up 1: Cell outlines · 19:30 (e12)." in session.working_lines()


def test_a_population_pictures_title_is_its_own_and_another_days_stores_are_never_read(tmp_path, world, monkeypatch):
    """The re-check's SF-d: a population's picture takes the population's own title; another day's population, clip
    or result never gives its title to this day's record (ids start again each day)."""
    from orchestrator.held import HeldClip, held_of
    from tests.test_population import cells

    population, _ = cells(world)
    manager = ResearchManager(world, root=tmp_path / "research", today=lambda: population.day)
    session = working_session(manager, day=population.day)
    manager._event({"event": "looked", "rid": "r4", "id": population.id, "how": "picture 0", "title": "", "kind": ""})
    assert session.working[-1]["title"] == population.title and session.working[-1]["kind"] == "population"
    other = working_session(manager, rid="r5", day="2026-10-01")
    held_of(world).clips["k7"] = HeldClip("k7", "r5", "Researcher 5", "q1", 15, population.day)
    for eid in (population.id, "k7"):
        manager._event({"event": "looked", "rid": "r5", "id": eid, "how": "picture 0", "title": "", "kind": ""})
    assert [w["title"] for w in other.working] == ["", ""]  # today's stores are not that day's
    from orchestrator.resources import resources_of

    world.resources = resources_of(world)  # one index the manager reads too
    results, asked = world.resources.results, []
    real_get = results.get
    monkeypatch.setattr(results, "get", lambda eid, day=None: (asked.append(day), real_get(eid, day))[1])
    manager._event({"event": "looked", "rid": "r5", "id": "d1", "how": "picture 0", "title": "", "kind": ""})
    assert asked == [] and other.working[-1]["kind"] == "result"  # another day's results: never read here, on the loop
