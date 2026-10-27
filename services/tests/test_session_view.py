"""The view follows the session (the plan "the view follows the session"; ADR 0017), the server's half:
- a session's recordings are the camera video of the Record datasets it holds, by the index's one rule
  (GET /api/sessions/{sid}/recordings, datasets/recordings.py);
- `session-cleared` says which session opened, the one left and why, so the studio can tell new from returning;
- while the studio covers the live view (`view {held}`: a new session's home, session-home.ts), the running look makes
  no looks, the subject tracker takes none and the brain and the voice are told in one line; it is cleared by the
  studio, another session's opening and the last studio leaving;
- the left sidebar is a screen kind (`sidebar`: this session's assets, made, researchers, activity), and the sessions
  list is the session title's menu, a kind of its own (`sessions`).
Which feed is shown is the studio's and the camera bridge's (test_bridge.py): nothing here mentions footage."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from orchestrator.app import App
from orchestrator.brain import context
from orchestrator.commands import COMMANDS, handle
from orchestrator.datasets import recordings
from orchestrator.day.store import DayStore
from orchestrator.paths import Paths
from orchestrator.resources import Index
from orchestrator.server import create_app
from tests.test_population import world  # noqa: F401  (the fixture)
from tests.waiting import until

DAY = "2026-10-12"
NIGHT = time.mktime((2026, 10, 12, 1, 44, 11, 0, 0, -1))  # 1:44 am, local time


def dataset(root: Path, did: str, started: float, *, name: str = "", parts=(1,), on_disk=None, seconds: float = 90.0):
    """A Record dataset as the collector leaves it (datasets/collector.py): its summary, and each part's video and
    sidecar linked into video/ (`on_disk`: the parts whose video is there; the rest are not linked in yet)."""
    folder = root / did
    (folder / "video").mkdir(parents=True)
    listed = []
    for n in parts:
        begun = started + (n - 1) * 600
        listed.append({"n": n, "video": f"video/part-{n}.mp4", "sidecar": f"video/part-{n}.frames.jsonl",
                       "source": f"/recordings/MicroscopeMind-{n}.mp4", "noticedAtEpoch": begun + 1})
        if on_disk is None or n in on_disk:
            (folder / "video" / f"part-{n}.mp4").write_bytes(b"\x00" * 64)
            lines = [{"type": "header", "startedAtEpoch": begun, "owner": "dataset"},
                     {"i": 0, "seq": 1, "bridgeAt": begun + 0.1, "src": "camera"},
                     {"i": 1, "seq": 2, "bridgeAt": begun + seconds, "src": "camera"},
                     {"type": "saved", "at": begun + seconds + 1}]
            (folder / "video" / f"part-{n}.frames.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    (folder / "dataset.json").write_text(json.dumps({"id": did, "name": name, "status": "stopped",
                                                     "startedAtEpoch": started, "video": {"parts": listed}}))
    return folder


def index_over(tmp_path, day) -> Index:
    hub = SimpleNamespace(execution_day=day, scene=SimpleNamespace(objects={}))
    return Index(hub, day=day, datasets=tmp_path / "datasets", recordings=tmp_path / "recordings")


# --- a session's recordings ----------------------------------------------------------------------------------
def test_a_sessions_recordings_are_its_datasets_video_by_the_indexs_rule(tmp_path):
    clock = [NIGHT]
    day = DayStore(root=tmp_path / "sessions", day=DAY, clock=lambda: clock[0])
    root = tmp_path / "datasets"
    # From before sessions: no journal claims it, so it is its date's session's.
    dataset(root, "ds-20261012-014411", NIGHT, parts=(1, 2, 3), on_disk=(1, 3))
    clock[0] = NIGHT + 3600
    assert day.new() == f"{DAY}.2"
    dataset(root, "ds-20261012-024411", NIGHT + 3600, name="Sperm motility")
    day.made("dataset", "ds-20261012-024411")  # as Record does (commands.py `dataset {on}`)
    dataset(root, "ds-20201231-090000", time.mktime((2020, 12, 31, 9, 0, 0, 0, 0, -1)))  # a date with no session
    index = index_over(tmp_path, day)

    first = recordings.of_session(index, DAY)
    # Newest first; a part not linked in yet is not listed; each with its start and length from its sidecar.
    assert [(r["dataset"], r["part"]) for r in first] == [("ds-20261012-014411", 3), ("ds-20261012-014411", 1)]
    one = first[1]
    assert one["startedAt"] == round(NIGHT, 3) and one["seconds"] == 90.0
    assert one["name"] == "Recording · Oct 12, 1:44 am" and first[0]["name"] == "Recording · Oct 12, 2:04 am"
    assert one["thumb"] == f"/api/resources/ds-20261012-014411/thumbnail?day={DAY}&v=stopped.3"
    # The dataset journaled by the second session is its, never its date's session's, and named by its name.
    second = recordings.of_session(index, f"{DAY}.2")
    assert [(r["dataset"], r["part"], r["name"]) for r in second] == [("ds-20261012-024411", 1, "Sperm motility")]
    # The same rule as the index's dataset entries.
    assert [e.id for e in index.list("dataset", f"{DAY}.2")] == ["ds-20261012-024411"]
    assert [e.id for e in index.list("dataset", DAY)] == ["ds-20261012-014411"]
    # A session that does not exist has none, even with a dataset of its date.
    assert recordings.of_session(index, "2020-12-31") == [] and recordings.of_session(index, "2026-10-13") == []


def test_a_recordings_length_and_start_fall_back_without_a_sidecar(tmp_path):
    root = tmp_path / "datasets"
    folder = dataset(root, "ds-20261012-014411", NIGHT)
    (folder / "video" / "part-1.frames.jsonl").unlink()
    rows = [{"path": str(folder)}]
    got, = recordings.of_datasets(DAY, rows)
    assert got["startedAt"] == round(NIGHT + 1, 3) and got["seconds"] is None  # when the dataset noticed the part


def test_the_recordings_route_reads_a_session_off_the_loop_and_says_none_for_an_unknown_one(tmp_path):
    web = create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path))
    http = TestClient(web)
    app = web.state.app
    first = app.day.sid
    now = app.day.clock()
    legacy = time.strftime("ds-%Y%m%d-%H%M%S", time.localtime(now - 60))
    dataset(app.paths.datasets, legacy, now - 60)  # claimed by no session: its date's (this one)
    second = app.new_session()["session"]
    mine = time.strftime("ds-%Y%m%d-%H%M%S", time.localtime(now))
    dataset(app.paths.datasets, mine, now, name="Leaf")
    app.day.made("dataset", mine)
    got = http.get(f"/api/sessions/{first}/recordings")
    assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
    assert [(r["dataset"], r["part"]) for r in got.json()["recordings"]] == [(legacy, 1)]
    listed = http.get(f"/api/sessions/{second}/recordings").json()
    assert listed["session"] == second and [r["name"] for r in listed["recordings"]] == ["Leaf"]
    assert set(listed["recordings"][0]) == {"dataset", "part", "name", "startedAt", "seconds", "thumb"}
    for sid in ("2020-01-01", "not-a-session"):
        assert http.get(f"/api/sessions/{sid}/recordings").json() == {"session": sid, "recordings": []}


# --- the view held while a new session's home covers it ---------------------------------------------------------
def build(root) -> App:
    return App.build("http://127.0.0.1:1", Paths.under(root), use_gpu=False)


class Client:
    def __init__(self):
        self.replies = []

    def offer(self, message):
        self.replies.append(json.loads(message))


def test_the_view_command_holds_the_running_look_and_the_subject_and_is_no_line_of_activity(tmp_path):
    built = build(tmp_path)
    client = Client()
    built.hub.clients.add(client)  # a studio is there: the running look is wanted
    assert built.sight.wanted() and built.hub.view_held is False

    def send(**msg) -> dict:
        asyncio.run(handle(built, client, {"type": "view", "requestId": "v", **msg}))
        return client.replies[-1]

    def activity() -> list[str]:
        return [line["text"] for line in built.day.active.lines if line.get("type") == "activity"]

    assert send(held=True) == {"type": "result", "for": "view", "requestId": "v", "ok": True, "held": True, "changed": True}
    assert built.hub.view_held is True and not built.sight.wanted()
    # A look that was in flight when the view was covered is not the subject.
    assert built.subjects.observe(SimpleNamespace(specimen="onion epidermis", same=None)) is False
    assert built.subjects.subject == ""
    assert send(held=True)["changed"] is False  # said again as the home comes and goes: nothing new
    # Nothing the user did: the opening is said as the session's, and the choice that ends it as its `source`.
    assert activity() == []

    built.sight.recheck = False
    assert send(held=False, live=True)["held"] is False and built.hub.view_held is False and built.sight.wanted()
    assert built.sight.recheck is True  # it looks again soon
    send(held=True)
    send(held=False)
    assert activity() == []
    built.subjects.observe(SimpleNamespace(specimen="onion epidermis", same=None))
    assert built.subjects.subject == "onion epidermis"

    refused = send(held="yes")
    assert refused["ok"] is False and "held is true or false" in refused["error"] and built.hub.view_held is False


def test_another_sessions_opening_clears_the_held_view_and_says_why(tmp_path):
    built = build(tmp_path)
    sent: list[dict] = []
    built.hub.broadcast = built.subjects.publish = sent.append
    first = built.day.sid
    asyncio.run(COMMANDS["view"](built, None, {"type": "view", "held": True}))
    second = built.new_session()["session"]
    assert built.hub.view_held is False
    asyncio.run(COMMANDS["view"](built, None, {"type": "view", "held": True}))
    built.switch_session(first)
    assert built.hub.view_held is False
    cleared = [m for m in sent if m["type"] == "session-cleared"]
    assert cleared == [{"type": "session-cleared", "session": second, "previous": first, "why": "new"},
                       {"type": "session-cleared", "session": first, "previous": second, "why": "switch"}]


def test_the_last_studio_leaving_clears_the_held_view(tmp_path):
    web = create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path))
    http = TestClient(web)
    hub = web.state.hub

    def held(ws, value: bool) -> None:
        ws.send_json({"type": "view", "held": value, "requestId": "held"})
        for _ in range(200):
            reply = ws.receive_json()
            if reply.get("requestId") == "held":
                assert reply["ok"] is True
                return
        raise AssertionError("no reply to the view command")

    with http.websocket_connect("/ws", headers={"origin": "http://127.0.0.1:8770"}) as first:
        with http.websocket_connect("/ws", headers={"origin": "http://127.0.0.1:8770"}):
            held(first, True)
            assert hub.view_held is True
        time.sleep(0.3)
        assert hub.view_held is True  # another studio is still there
    until(lambda: hub.view_held is False)


def test_the_brain_and_the_voice_are_told_only_while_the_view_is_held(tmp_path):
    built = build(tmp_path)

    def rendered() -> list[str]:
        m = context.capture(built.brain)
        return ["".join(context.scene_table(m)), context.fast_fields(m)["screen"], context.view_note(m),
                context.voice_recap(m)]

    assert not any(context.HELD_LINE in text for text in rendered())
    built.hub.view_held = True
    assert all(context.HELD_LINE in text for text in rendered())
    assert context.HELD_LINE == "The live view is hidden: the user is choosing what this session looks at."
    held = context.capture(built.brain)
    assert context.view_key(held) != context.view_key(context.Moment(**{**held.__dict__, "held": ""}))
    # The voice's compact notes (with what the studio reported) keep it among the facts it needs first.
    awareness = context.Moment(**{**held.__dict__, "awareness": {"input": {"basis": "test"}}})
    assert context.HELD_LINE in context.view_note(awareness) and context.HELD_LINE in context.voice_recap(awareness)


# --- the sidebar on the screen report --------------------------------------------------------------------------
def test_the_sidebar_and_the_sessions_list_are_kinds_of_their_own(tmp_path):
    from orchestrator import screen

    hub = SimpleNamespace(scene=SimpleNamespace(objects={}), screen=screen.Screen())
    hub.screen.report({"items": [{"id": "live", "kind": "live"},
                                 {"id": "sidebar", "kind": "sidebar", "title": "Activity", "shows": "activity", "open": True}]})
    assert screen.resolve(hub, "the sidebar") == ("sidebar", "sidebar")
    assert screen.resolve(hub, "sidebar") == ("sidebar", "sidebar")  # its id
    assert screen.resolve(hub, "the sessions list") == ("sessions", "sessions")  # the session title's menu
    assert screen.resolve(hub, "sessions") == ("sessions", "sessions")
    assert set(screen.TAKES["sessions"]) == {"open", "close"} and screen.why_not("sessions", "hide") is not None
    assert screen.why_not("sidebar", "hide") is None and screen.why_not("sidebar", "move") is not None
    assert screen.NAMES["sidebar"] == "the sidebar" and set(screen.TAKES["sidebar"]) == {"open", "close", "hide"}


def test_present_opens_closes_and_hides_the_sidebar_and_opens_and_closes_the_sessions_list(world):
    import pytest

    from orchestrator.brain.registry import ToolFailed
    from tests.test_screen import present, setup

    registry, screen, studio = setup(world)
    studio.items["sidebar"] = {"id": "sidebar", "kind": "sidebar", "title": "Activity", "shows": "activity",
                               "open": False, "visible": True, "z": 2}
    studio.items["sessions"] = {"id": "sessions", "kind": "sessions", "title": "Sessions", "open": False, "visible": False, "z": 40}
    heard: list[tuple[str, dict]] = []
    others = studio.hear

    def hear(message: dict) -> None:  # the studio's sidebar (sidebar.ts, its section as `shows`) and sessions list
        if message.get("type") == "present" and message["id"] in ("sidebar", "sessions"):
            heard.append((message["kind"], message["set"]))
            item = studio.items[message["id"]]
            values = message["set"]
            if values.get("open") is True:
                item.update(open=True, visible=True, **({"shows": values.get("shows", item["shows"])} if "shows" in item else {}))
            elif values.get("open") is False or values.get("visible") is False:
                item.update(open=False, **({"visible": False} if item["kind"] == "sessions" else {}))
            studio.applied.append(message["change"])
            asyncio.get_running_loop().call_soon(studio.report)
            return
        others(message)

    studio.hear = hear
    studio.report()
    present(registry, world, do="open", what="the sessions list")
    assert heard[-1] == ("sessions", {"open": True}) and studio.items["sessions"]["open"]
    present(registry, world, do="close", what="sessions")
    assert heard[-1] == ("sessions", {"open": False}) and not studio.items["sessions"]["open"]
    present(registry, world, do="open", what="the sidebar")
    assert heard[-1] == ("sidebar", {"open": True})  # the section this viewer last had open
    present(registry, world, do="hide", what="sidebar")
    assert heard[-1] == ("sidebar", {"visible": False}) and not studio.items["sidebar"]["open"]
    with pytest.raises(ToolFailed) as refused:
        present(registry, world, do="move", what="sidebar", rect=[0, 0, 100, 100])
    assert "the sidebar can't be moved" in str(refused.value)
