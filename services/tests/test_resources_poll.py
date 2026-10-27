"""The studio's poll of the index (ticket 66, S7): `GET /api/resources` returns a revision of the whole listing, and
`since=<revision>` answers `{day, revision, unchanged: true}` with no entries when nothing changed. Built and encoded
off the event loop. Real index and HTTP routes, five researchers with their sources; no camera or model."""
import asyncio
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tests.test_research_entries import researcher
from tests.test_resources import app_at


def five(tmp_path):
    web = app_at(tmp_path)
    index = web.state.app.hub.resources
    root = tmp_path / "research-root"
    sessions = [researcher(root, index.today(), index.session(), rid=f"r{n}", n=n) for n in range(1, 6)]
    index.research = SimpleNamespace(researchers=lambda day=None, session=None: list(sessions), root=root,
                                     get=lambda rid: next((s for s in sessions if s.rid == rid), None))
    return web, index, sessions


def test_an_unchanged_index_answers_unchanged_with_no_entries_and_a_change_gives_a_new_revision(tmp_path):
    web, index, sessions = five(tmp_path)
    client = TestClient(web)
    first = client.get("/api/resources").json()
    assert len(first["entries"]) >= 5 * 9 and isinstance(first["revision"], str)
    assert client.get("/api/resources").json()["revision"] == first["revision"]  # the same listing, the same revision
    same = client.get(f"/api/resources?since={first['revision']}")
    assert same.json() == {"day": first["day"], "revision": first["revision"], "unchanged": True}
    assert len(same.content) < 200
    # Any entry that changes changes the revision: a new result, a researcher's new findings table.
    index.results.add("count", {}, "6 cells", values={"count": 6})
    changed = client.get(f"/api/resources?since={first['revision']}").json()
    assert changed["revision"] != first["revision"] and "unchanged" not in changed
    assert len(changed["entries"]) == len(first["entries"]) + 1
    sessions[0].result["tables"].append({"title": "More", "columns": ["a"], "rows": [["1"]]})
    again = client.get(f"/api/resources?since={changed['revision']}").json()
    assert again["revision"] != changed["revision"] and any(e["id"] == "r1:table-2" for e in again["entries"])
    # A stale or unknown revision is no claim of anything: the whole listing.
    assert "entries" in client.get("/api/resources?since=not-a-revision").json()
    assert client.get("/api/resources?since=" + "x" * 65).status_code == 400


def test_a_kind_filter_keeps_the_sessions_revision(tmp_path):
    web, index, _ = five(tmp_path)
    client = TestClient(web)
    whole = client.get("/api/resources").json()
    sources = client.get("/api/resources?kind=source").json()
    assert sources["revision"] == whole["revision"] and {e["kind"] for e in sources["entries"]} == {"source"}
    assert client.get(f"/api/resources?kind=source&since={whole['revision']}").json()["unchanged"] is True


def test_the_listing_is_built_and_encoded_off_the_event_loop(tmp_path):
    web, index, _ = five(tmp_path)
    seen = {}
    original = index.listing

    def listing(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            seen["loop"] = True
        except RuntimeError:
            seen["loop"] = False
        seen["thread"] = threading.current_thread().name
        return original(*args, **kwargs)

    index.listing = listing
    assert TestClient(web).get("/api/resources").status_code == 200
    assert seen["loop"] is False


def test_polling_an_unchanged_index_with_five_researchers_is_cheap(tmp_path, wall_clock):
    """Measured with make test-timing (wall_clock bounds): five researchers, each with a report in two versions, a
    presentation, a lesson, a video, a picture, a table, a chart and three sources."""
    web, index, _ = five(tmp_path)
    client = TestClient(web)
    revision = client.get("/api/resources").json()["revision"]
    for _ in range(3):
        client.get(f"/api/resources?since={revision}")
    times = []
    for _ in range(20):
        t0 = time.perf_counter()
        body = client.get(f"/api/resources?since={revision}").json()
        times.append(time.perf_counter() - t0)
        assert body["unchanged"] is True
    times.sort()
    wall_clock.within(times[len(times) // 2], 0.05, "an unchanged poll's p50")
    wall_clock.within(times[18], 0.1, "an unchanged poll's p95")
