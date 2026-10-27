"""Datasets (orchestrator/datasets/): a dataset is collected only while the user records, its edges
are Record and its Stop, it carries on across restarts and midnight, the camera's video is kept in it
(hard-linked, re-asked for when the bridge drops it), the live view never waits for it, and the Data
light says only what it has seen saved. The video is the bridge's; a fake bridge status and fake
requests stand in for it here."""
import asyncio
import errno
import json
import os
import re
from pathlib import Path

from orchestrator.app import App
from orchestrator.commands import COMMANDS
from orchestrator.datasets import DatasetCollector
from orchestrator.datasets import checks
from orchestrator.datasets import collector as collector_module
from orchestrator.datasets import manifest
from orchestrator.hub import FrameInfo
from orchestrator.paths import Paths

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"


class Clock:
    def __init__(self, now: float = 1_791_264_563.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeBridge:
    """The bridge's /api/health as the collector reads it, and its record start/stop requests."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.health: dict | None = {"ok": True, "bootId": "boot1", "recording": {"running": False, "owners": []}}
        self.posts: list[str] = []
        self.next_path = folder / "iPhoneWild-Camera-20261006-012923.mp4"
        self.refuse = ""

    async def post(self, path: str) -> dict:
        self.posts.append(path)
        if self.refuse:
            return {"ok": False, "error": self.refuse}
        if path.startswith("/api/record/start"):
            sidecar = self.next_path.with_name(self.next_path.stem + ".frames.jsonl")
            sidecar.write_text('{"type":"header"}\n')
            self.health["recording"] = {"running": True, "owners": ["dataset"], "path": str(self.next_path),
                                        "sidecar": str(sidecar), "finishing": []}
            return {"ok": True, "alreadyRecording": False, **self.health["recording"]}
        self.health["recording"] = {"running": False, "owners": [], "finishing": []}
        return {"ok": True, "stopped": True}


def collector(tmp_path, clock=None, bridge=None, **extra) -> tuple[DatasetCollector, FakeBridge, list]:
    bridge = bridge or FakeBridge(tmp_path / "recordings")
    bridge.folder.mkdir(parents=True, exist_ok=True)
    sent: list = []
    c = DatasetCollector(tmp_path / "datasets", bridge_health=lambda: bridge.health, broadcast=sent.append,
                         post=bridge.post, clock=clock or Clock(), **extra)
    return c, bridge, sent


def frame_info(n: int, seq: int | None = None) -> FrameInfo:
    return FrameInfo(n, 1280, 720, 1.5 * n, -0.5 * n, 0, True, True, None, 1_791_264_563.0 + n / 30,
                     focus=12.0, stats=(12.0, 140.0, 20.0, 0.0), seq=seq, boot="boot1")


def lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def finish(c: DatasetCollector) -> None:
    for writer in [c.writer, *c.closing]:
        if writer is not None:
            writer.drain()


def test_on_collects_into_its_own_dataset_and_off_completes_it(tmp_path):
    clock = Clock()
    c, _, sent = collector(tmp_path, clock)
    wire = c.start()
    assert wire["on"] and wire["id"] == "ds-" + __import__("time").strftime("%Y%m%d-%H%M%S", __import__("time").localtime(clock.now))
    folder = tmp_path / "datasets" / wire["id"]
    c.interaction({"at": "2026-10-06T01:29:30", "kind": "click", "frameId": 7})
    c.journal({"at": clock.now, "type": "turn", "role": "user", "text": "I took a sperm sample"})
    c.vote({"trial": "t1", "winner": "A"})
    c.frame(frame_info(7, seq=1201))
    clock.now += 600
    c.stop()
    finish(c)

    summary = manifest.read(folder)
    assert summary["status"] == "complete" and summary["endedBy"] == "user"
    assert summary["stoppedAtEpoch"] - summary["startedAtEpoch"] == 600
    assert summary["counts"]["frames"] == 1 and summary["counts"]["events"] >= 5  # exact when it was written
    assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$", summary["startedAt"])  # seconds and offset
    events = lines(folder / "events.jsonl")
    streams = [(e["stream"], e["record"].get("event") or e["record"].get("kind") or e["record"].get("type")) for e in events]
    assert streams[0] == ("dataset", "started") and streams[-1] == ("dataset", "stopped")
    assert ("interaction", "click") in streams and ("journal", "turn") in streams and ("vote", None) in streams
    frames = lines(folder / "frames.jsonl")
    assert [(f["id"], f["seq"], f["boot"], f["run"]) for f in frames] == [(7, 1201, "boot1", 1)]
    assert sent[-1]["type"] == "dataset" and sent[-1]["on"] is False


def test_nothing_is_collected_while_it_is_off(tmp_path):
    c, bridge, _ = collector(tmp_path)
    c.interaction({"kind": "click"})
    c.frame(frame_info(1))
    c.keep_picture(b"jpeg", "a picture")
    asyncio.run(c.tick())
    assert not (tmp_path / "datasets").exists() and bridge.posts == []


def test_the_video_is_asked_for_and_hard_linked_as_soon_as_it_exists(tmp_path):
    clock = Clock()
    c, bridge, _ = collector(tmp_path, clock)
    folder = tmp_path / "datasets" / c.start()["id"]
    asyncio.run(c.tick())
    assert bridge.posts == ["/api/record/start?owner=dataset"]
    part = c.summary["video"]["parts"][0]
    assert part["source"] == str(bridge.next_path) and part["joined"] is False
    assert part["sidecarKept"] == "linked" and part["videoKept"] is None  # FFmpeg has not made the file yet

    bridge.next_path.write_bytes(b"fragment 1")
    clock.now += 1
    asyncio.run(c.tick())
    video = folder / "video" / "part-1.mp4"
    assert part["videoKept"] == "linked" and os.stat(video).st_ino == os.stat(bridge.next_path).st_ino
    with bridge.next_path.open("ab") as f:  # the bridge keeps writing: the dataset's copy grows with it
        f.write(b" fragment 2")
    assert video.read_bytes() == b"fragment 1 fragment 2"
    # The bridge finishes the file by writing a new one over the name: the dataset keeps what was recorded.
    replaced = bridge.next_path.with_name("tmp.mp4")
    replaced.write_bytes(b"remuxed")
    os.replace(replaced, bridge.next_path)
    bridge.next_path.unlink()  # and whoever deletes the recordings
    assert video.read_bytes() == b"fragment 1 fragment 2"
    assert bridge.posts == ["/api/record/start?owner=dataset"]  # still recording for it: not asked again


def test_a_recording_the_bridge_dropped_is_asked_for_again_and_its_file_is_a_new_part(tmp_path):
    clock = Clock()
    c, bridge, _ = collector(tmp_path, clock)
    c.start()
    asyncio.run(c.tick())
    first = bridge.next_path
    # The bridge restarted (a new run, nothing recording): asked again once its health settles.
    bridge.health = {"ok": True, "bootId": "boot2", "recording": {"running": False, "owners": []}}
    bridge.next_path = bridge.folder / "iPhoneWild-Camera-20261006-013500.mp4"
    clock.now += 1
    asyncio.run(c.tick())
    assert len(bridge.posts) == 1  # too soon after the last request
    clock.now += 3
    asyncio.run(c.tick())
    assert bridge.posts == ["/api/record/start?owner=dataset"] * 2
    assert [p["source"] for p in c.summary["video"]["parts"]] == [str(first), str(bridge.next_path)]
    finish(c)
    events = [e["record"].get("event") for e in lines(c.folder / "events.jsonl")]
    assert "bridge-restarted" in events and events.count("video-part") == 2


def test_a_refused_recording_is_shown_and_asked_for_again_later(tmp_path):
    clock = Clock()
    c, bridge, sent = collector(tmp_path, clock)
    c.start()
    bridge.refuse = "recording ffmpeg did not start: no such file"
    asyncio.run(c.tick())
    assert c.wire()["video"] == {"state": "error", "parts": 0, "error": "recording ffmpeg did not start: no such file"}
    clock.now += 5
    asyncio.run(c.tick())
    assert len(bridge.posts) == 1  # waits RETRY_S
    bridge.refuse = ""
    clock.now += 6
    asyncio.run(c.tick())
    assert len(bridge.posts) == 2 and c.wire()["video"]["state"] == "recording"
    assert c.on  # the dataset never stopped because its video failed


def test_off_tells_the_bridge_and_a_left_over_owner_is_stopped(tmp_path):
    clock = Clock()
    c, bridge, _ = collector(tmp_path, clock)
    c.start()
    asyncio.run(c.tick())
    c.stop()
    clock.now += 3
    asyncio.run(c.tick())
    assert bridge.posts[-1] == "/api/record/stop?owner=dataset"


def test_after_a_restart_the_same_dataset_carries_on_with_the_gap_written_down(tmp_path):
    clock = Clock()
    c, bridge, _ = collector(tmp_path, clock)
    started = c.start()
    c.interaction({"kind": "click"})
    c.close()  # the app stops while recording
    folder = tmp_path / "datasets" / started["id"]
    os.utime(folder / "events.jsonl", (clock.now + 10, clock.now + 10))
    assert manifest.read(folder)["status"] == "collecting"

    clock.now += 9 * 3600
    again, _, sent = collector(tmp_path, clock, bridge=bridge)
    again.resume()
    assert again.on and again.wire()["id"] == started["id"] and again.run == 2
    assert again.wire()["resumedAfter"] == 9 * 3600 - 10
    again.stop()
    finish(again)
    summary = manifest.read(folder)
    assert [r["n"] for r in summary["runs"]] == [1, 2]
    events_written = len(lines(folder / "events.jsonl"))
    assert summary["counts"]["events"] == events_written  # counted on across the restart, exactly
    assert summary["gaps"][0]["seconds"] == 9 * 3600 - 10 and summary["status"] == "complete"
    events = [(e["run"], e["record"].get("event")) for e in lines(folder / "events.jsonl") if e["stream"] == "dataset"]
    assert (2, "resumed") in events and (1, "app-stopping") in events
    states = lines(folder / "start-state.jsonl")
    assert [s["why"] for s in states] == ["start", "resume"] or not states  # kept when the app gives its state


def test_a_new_session_and_a_switch_leave_it_on(tmp_path):
    built = App.build("http://127.0.0.1:1", Paths.under(tmp_path), use_gpu=False)
    built.datasets.start()
    first = built.day.sid
    second = built.new_session("Kidney section")["session"]  # another session: the view is cleared, Record goes on
    assert built.datasets.on
    built.switch_session(first)  # and back
    assert built.datasets.on
    folder = built.datasets.folder
    built.datasets.stop()
    finish(built.datasets)
    records = [e["record"] for e in lines(folder / "events.jsonl")]
    changes = [(r.get("session"), r.get("why")) for r in records if r.get("event") == "session-opened"]
    assert changes == [(second, "new"), (first, "switch")]  # the dataset's own record of each change
    assert [r.get("session") for r in records if r.get("type") == "session-opened"] == [second, first]  # the journal's
    assert [s["why"] for s in lines(folder / "start-state.jsonl")].count("session-opened") == 2


def test_the_switch_is_a_studio_command_and_no_tool(tmp_path):
    built = App.build("http://127.0.0.1:1", Paths.under(tmp_path), use_gpu=False)
    reply = asyncio.run(COMMANDS["dataset"](built, None, {"on": True}))
    assert reply["ok"] and reply["dataset"]["on"]
    assert asyncio.run(COMMANDS["dataset"](built, None, {"on": False}))["dataset"]["on"] is False
    assert not [name for name in built.brain.registry.tools if "dataset" in name]
    assert built.paths.datasets.is_relative_to(tmp_path)  # a test never reaches the real datasets


def test_when_the_disk_falls_behind_only_frame_facts_are_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_module, "QUEUE_LIMIT", -1)
    c, _, _ = collector(tmp_path)
    c.start()
    for n in range(5):
        c.frame(frame_info(n))
    c.interaction({"kind": "click"})
    asyncio.run(c.tick())
    c.stop()
    finish(c)
    events = lines(c.folder / "events.jsonl")
    assert not lines(c.folder / "frames.jsonl")
    assert any(e["record"].get("kind") == "click" for e in events)
    dropped = [e["record"] for e in events if e["record"].get("event") == "dropped"]
    assert dropped and dropped[0]["frames"] == 5


def test_across_disks_the_video_is_copied_only_once_the_bridge_has_finished_it(tmp_path, monkeypatch):
    clock = Clock()
    c, bridge, _ = collector(tmp_path, clock)
    real_link = os.link

    def no_links(source, target, *a, **k):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(os, "link", no_links)
    c.start()
    asyncio.run(c.tick())
    bridge.next_path.write_bytes(b"half written")
    clock.now += 1
    asyncio.run(c.tick())
    part = c.summary["video"]["parts"][0]
    assert part["videoKept"] is None  # still being written: never copied half-way
    c.stop()
    bridge.next_path.write_bytes(b"complete file")
    bridge.health["recording"] = {"running": False, "owners": [], "finishing": []}
    finish(c)
    clock.now += 3
    asyncio.run(c.tick())
    assert (c.folder / "video" / "part-1.mp4").read_bytes() == b"complete file"
    assert manifest.read(c.folder)["video"]["parts"][0]["videoKept"] == "copied"
    monkeypatch.setattr(os, "link", real_link)


def test_pictures_are_kept_in_the_dataset(tmp_path):
    c, _, _ = collector(tmp_path)
    logs = tmp_path / "logs"
    crop = logs / "crops" / "2026-10-06" / "012930123-identify-f7.jpg"
    crop.parent.mkdir(parents=True)
    crop.write_bytes(b"crop")
    snap = tmp_path / "snapshots" / "2026-10-06" / "snapshot-012931-f9.png"
    snap.parent.mkdir(parents=True)
    snap.write_bytes(b"png")
    c.start()
    c.keep_log_picture(crop, "crops/2026-10-06/012930123-identify-f7.jpg")
    c.keep_file(snap, "snapshots")
    c.keep_picture(b"magnified", "Averaged: 24 real frames")
    c.stop()
    finish(c)
    pictures = c.folder / "pictures"
    assert (pictures / "crops" / "2026-10-06" / "012930123-identify-f7.jpg").read_bytes() == b"crop"
    assert (pictures / "snapshots" / "snapshot-012931-f9.png").read_bytes() == b"png"
    assert [p.read_bytes() for p in (pictures / "magnifier").iterdir()] == [b"magnified"]
    crop.unlink()  # the logs are deleted later: the dataset keeps its copy
    assert (pictures / "crops" / "2026-10-06" / "012930123-identify-f7.jpg").exists()


def test_no_code_here_knows_whether_the_camera_was_real():
    # Only the bridge says which frames were test recordings (in its sidecar); AGENTS.md.
    for path in (ORCHESTRATOR / "datasets").rglob("*.py"):
        assert not re.search(r"footage|simulat", path.read_text(), re.IGNORECASE), path


def test_a_dataset_is_named_without_changing_what_was_recorded(tmp_path):
    from orchestrator.datasets import store

    c, _, sent = collector(tmp_path)
    started = c.start()
    assert c.rename(started["id"], "  sperm   sample 1 ")
    assert sent[-1]["name"] == "sperm sample 1"
    c.stop()
    finish(c)
    assert c.rename(started["id"], "sperm sample one")  # after it is complete too
    assert not c.rename("../../etc", "x") and store.find(tmp_path / "datasets", "../x") is None
    folder = tmp_path / "datasets" / started["id"]
    assert manifest.read(folder)["name"] == "sperm sample one"
    renames = [e["record"]["name"] for e in lines(folder / "events.jsonl") if e["record"].get("event") == "renamed"]
    assert renames == ["sperm sample 1", "sperm sample one"]
    listed = store.listing(tmp_path / "datasets")["datasets"]
    assert [(d["id"], d["name"], d["status"]) for d in listed] == [(started["id"], "sperm sample one", "complete")]


def test_the_brain_and_the_voice_are_told_while_it_is_on_and_only_then(tmp_path):
    from orchestrator.brain.context import capture, scene_table, view_note

    built = App.build("http://127.0.0.1:1", Paths.under(tmp_path), use_gpu=False)
    off = capture(built.brain)
    assert off.collecting == "" and "dataset" not in scene_table(off)[0]
    built.datasets.start()
    on = capture(built.brain)
    assert "A dataset is being collected" in on.collecting and "Only the user starts or stops recording" in on.collecting
    assert on.collecting in scene_table(on)[0] and on.collecting in view_note(on)
    built.datasets.stop()


def test_the_datasets_tab_reads_the_list_and_one_dataset(tmp_path):
    from fastapi.testclient import TestClient

    from orchestrator.server import create_app

    web = create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path))
    built = web.state.app
    started = built.datasets.start()
    built.datasets.stop()
    finish(built.datasets)
    client = TestClient(web)
    listing = client.get("/api/datasets").json()
    assert listing["root"] == str(tmp_path / "datasets") and [d["id"] for d in listing["datasets"]] == [started["id"]]
    assert listing["collecting"]["on"] is False
    one = client.get(f"/api/datasets/{started['id']}").json()
    assert one["summary"]["status"] == "complete"
    assert client.get("/api/datasets/..%2F..%2Fsessions").status_code == 404


# --- microscope facts (datasets/rig.py, scale.py): every one says where it came from ------------------
def test_the_field_number_is_the_default_until_someone_gives_it(tmp_path):
    from orchestrator.day import DayStore
    from orchestrator.scale import Scale

    day = DayStore(root=tmp_path / "sessions")
    scale = Scale(day)
    assert scale.facts()["fieldNumber"]["source"] == "default"
    assert "assumed" in scale.facts()["fieldNumber"]["note"]
    scale.set_objective(40, via="voice")
    assert scale.facts()["objective"] == {"value": 40, "source": "user", "via": "voice", "at": scale.objective_at}
    assert Scale(day).facts()["fieldNumber"]["source"] == "default"  # still assumed after a restart
    scale.set_field_number(9.5, via="microscope facts")  # a WF25x: under the old 10 mm floor
    again = Scale(day)
    assert again.facts()["fieldNumber"]["value"] == 9.5 and again.facts()["fieldNumber"]["source"] == "user"
    # A day saved before sources were kept: its 18 mm may have been given or assumed.
    day.set_pref("scale", {"objective": 40, "fieldNumber": 18.0, "calibrated": {}})
    assert Scale(day).facts()["fieldNumber"]["source"] == "unknown"


def test_microscope_facts_are_kept_cleaned_and_cleared(tmp_path):
    import pytest

    from orchestrator.datasets.rig import Rig

    rig = Rig(tmp_path)
    assert rig.set("eyepiece", "25x")["value"] == 25.0
    assert rig.set("microscope", "  AmScope   M150C ")["value"] == "AmScope M150C"
    with pytest.raises(ValueError):
        rig.set("fieldNumber", "60")
    with pytest.raises(ValueError):
        rig.set("lens", "x")
    rig.set("microscope", "")
    again = Rig(tmp_path)
    assert set(again.facts) == {"eyepiece"} and again.facts["eyepiece"]["source"] == "user"


def test_a_dataset_starts_with_every_fact_and_where_it_came_from(tmp_path):
    from orchestrator.datasets.rig import Rig, at_start
    from orchestrator.fov import FieldOfView
    from orchestrator.scale import Scale

    rig = Rig(tmp_path)
    rig.set("eyepiece", 25)
    scale = Scale()
    scale.set_objective(40, via="studio")
    info = frame_info(1)
    info = FrameInfo(**{**info.__dict__, "fov": FieldOfView(640.0, 360.0, 300.0, 0.9), "disc": 300.4})
    facts = at_start(rig, scale, {"targetDevice": "iPhoneWild Camera", "settings": {"size": "1280x720", "fps": 30}}, info, 29.6)
    assert facts["eyepiece"]["source"] == "user" and facts["eyepiece"]["carriedOver"]
    assert facts["microscope"] == {"value": None, "source": "unknown"}
    assert facts["fieldNumber"]["source"] == "default" and facts["objective"]["via"] == "studio"
    assert facts["umPerPx"]["source"] == "estimate" and facts["umPerPx"]["value"] == round(18000 / 40 / 600, 5)
    assert facts["frameRate"] == {"value": 29.6, "source": "measured", "how": facts["frameRate"]["how"]}
    assert facts["cameraRequested"]["source"] == "bridge-config" and facts["eyepieceDisc"]["source"] == "measured"


def test_a_fact_entered_while_it_collects_is_appended_never_written_into_its_start(tmp_path):
    built = App.build("http://127.0.0.1:1", Paths.under(tmp_path), use_gpu=False)
    started = built.datasets.start()
    assert built.datasets.summary["rig"]["fieldNumber"]["source"] == "default"
    reply = asyncio.run(COMMANDS["rig"](built, None, {"field": "fieldNumber", "value": "9.5"}))
    assert reply["ok"] and built.hub.scale.field_number == 9.5  # the scale uses it from now on
    built.datasets.stop()
    finish(built.datasets)
    summary = manifest.read(tmp_path / "datasets" / started["id"])
    assert summary["rig"]["fieldNumber"]["source"] == "default"  # what it started with stays
    assert summary["rigEnteredAfter"][0]["field"] == "fieldNumber" and summary["rigEnteredAfter"][0]["enteredAfter"]
    # The next start of the app uses the field number given, as the user's.
    again = App.build("http://127.0.0.1:1", Paths.under(tmp_path), use_gpu=False)
    assert again.hub.scale.field_number == 9.5 and again.hub.scale.field_number_source == "user"


# --- the one check every dataset passes, and rebuilding one afterwards (check.py, importer.py) ------------
def test_the_end_checks_log_line_keeps_the_logs_own_time(tmp_path):
    """The end check's time is ISO with its offset (dataset.json's endCheck). Its interaction-log line keeps it as
    `checkedAt`, never as the line's `at`, which every reader parses in the log's own format (2026-10-12: a sim
    scenario reading the log broke on '-04:00')."""
    logged: list = []
    c, _bridge, _sent = collector(tmp_path, log=logged.append)
    c.light.end = lambda now, health: {"id": "ds-1", "ok": True, "problems": [], "at": "2026-10-12T13:54:08-04:00"}
    c._check(1.0)
    [line] = [r for r in logged if r.get("event") == "end-check"]
    assert "at" not in line and line["checkedAt"] == "2026-10-12T13:54:08-04:00" and line["ok"] is True


def test_a_collected_dataset_passes_the_check(tmp_path):
    from orchestrator.datasets.check import problems

    clock = Clock()
    c, bridge, _ = collector(tmp_path, clock)
    c.start()
    c.interaction({"kind": "click"})
    c.frame(frame_info(1, seq=5))
    asyncio.run(c.tick())
    bridge.next_path.write_bytes(b"fragment")
    clock.now += 1
    asyncio.run(c.tick())
    c.stop()
    finish(c)
    assert problems(c.folder) == []
    (c.folder / "events.jsonl").open("a").write("not json\n")
    assert any("not JSON" in p for p in problems(c.folder))


def test_the_check_holds_made_with_to_the_entries_it_carries(tmp_path):
    """docs/datasets.md "Made with": a carried entry must be in capabilities/ at the size it names."""
    from orchestrator.datasets.check import problems

    clock = Clock()
    c, bridge, _ = collector(tmp_path, clock)
    c.start()
    c.frame(frame_info(1, seq=5))
    asyncio.run(c.tick())
    bridge.next_path.write_bytes(b"fragment")
    clock.now += 1
    asyncio.run(c.tick())
    c.stop()
    finish(c)
    summary = json.loads((c.folder / manifest.NAME).read_text())
    digest = "a" * 64
    summary["madeWith"] = [{"key": "mine/dots", "portable": {"sha256": digest, "bytes": 5}},
                           {"key": "cellpose-sam"}]  # a reviewed entry is named only: nothing carried
    (c.folder / manifest.NAME).write_text(json.dumps(summary))
    assert any("capabilities/" + digest in p for p in problems(c.folder)), "a carried entry that isn't there"
    (c.folder / "capabilities").mkdir()
    (c.folder / "capabilities" / (digest + ".observationui.zip")).write_bytes(b"12345")
    assert problems(c.folder) == []
    summary["madeWith"] = [{"key": "mine/dots", "portable": {"sha256": "short"}}]  # present but unreadable
    (c.folder / manifest.NAME).write_text(json.dumps(summary))
    assert any("portable descriptor can't be read" in p for p in problems(c.folder))
    summary["madeWith"] = "not a list"
    (c.folder / manifest.NAME).write_text(json.dumps(summary))
    assert "madeWith is not a list of entries" in problems(c.folder)


def test_a_night_before_the_dot_is_rebuilt_from_its_logs_and_says_what_is_missing(tmp_path):
    import time as clock_time

    from orchestrator.datasets.importer import build

    paths = Paths.under(tmp_path)
    at = lambda h, m, s: clock_time.mktime((2026, 10, 6, h, m, s, 0, 0, -1))  # noqa: E731
    journal = paths.sessions / "2026-10-06" / "journal.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text("\n".join(json.dumps(e) for e in [
        {"at": at(1, 10, 0), "type": "turn", "text": "before the window"},
        {"at": at(1, 29, 21), "type": "turn", "role": "user", "text": "So I took a sperm sample"},
        {"at": at(1, 43, 42), "type": "preference", "key": "scale", "value": {"objective": 40, "fieldNumber": 18.0, "calibrated": {}}},
        {"at": at(2, 1, 24), "type": "research-linked", "thread": "t4", "rid": "r664846"},
    ]) + "\n")
    crop = paths.interaction_log.parent / "crops" / "2026-10-06" / "014000123-sight-f9.jpg"
    crop.parent.mkdir(parents=True)
    crop.write_bytes(b"jpeg")
    paths.interaction_log.write_text(json.dumps({"at": "2026-10-06T01:40:00", "kind": "sight", "image": "crops/2026-10-06/014000123-sight-f9.jpg"}) + "\n")
    (paths.research / "2026-10-06" / "r664846").mkdir(parents=True)
    (paths.research / "2026-10-06" / "r664846" / "session.json").write_text("{}")
    video = tmp_path / "recovered.mp4"
    video.write_bytes(b"the camera")

    folder = build(paths, at(1, 21, 39), at(2, 11, 16), video=video, video_start=at(1, 25, 36), name="sperm sample",
                   facts=['eyepiece=25|said "I think I have a... 25X" at 01:36:51'],
                   sample=['collectedAt=about 01:14|said at 01:39:01 "about, almost 25 minutes since the sample\'s collected"'])
    summary = manifest.read(folder)
    assert summary["reconstructed"] and summary["status"] == "complete" and folder.name == "ds-20261006-012139"
    texts = [e["record"].get("text") for e in lines(folder / "events.jsonl")]
    assert "So I took a sperm sample" in texts and "before the window" not in texts
    assert (folder / "pictures" / "crops" / "2026-10-06" / "014000123-sight-f9.jpg").read_bytes() == b"jpeg"
    assert (folder / "research" / "r664846" / "session.json").exists()
    part = summary["video"]["parts"][0]
    assert part["sidecar"] is None and part["timing"]["source"] == "estimate" and (folder / part["video"]).read_bytes() == b"the camera"
    assert summary["rig"]["objective"]["value"] is None  # 40x was set 20 minutes in: a change, not its start
    assert [(c["objective"], c["at"][11:19]) for c in summary["rigChanges"]] == [(40, "01:43:42")]
    assert summary["rig"]["fieldNumber"]["source"] == "default" and "no field number was given" in summary["rig"]["fieldNumber"]["evidence"]
    assert summary["rig"]["eyepiece"] == {"value": None, "source": "unknown"}  # never written into its start
    assert summary["rigEnteredAfter"][0]["value"] == 25.0 and "25X" in summary["rigEnteredAfter"][0]["evidence"]
    assert summary["sample"]["collectedAt"]["value"] == "about 01:14"
    assert "frames.jsonl" in summary["reconstruction"]["notCaptured"][0]
    import pytest

    with pytest.raises(SystemExit):  # a dataset is never written over
        build(paths, at(1, 21, 39), at(2, 11, 16))


def test_a_dataset_shows_one_of_its_own_pictures_from_the_middle_of_its_window(tmp_path):
    from orchestrator.datasets import store

    folder = tmp_path / "ds-20261006-012139"
    crops = folder / "pictures" / "crops" / "2026-10-06"
    crops.mkdir(parents=True)
    summary = {"startedAtEpoch": 1000.0, "stoppedAtEpoch": 2000.0}
    for name, at, size in (("early-sight-f1.jpg", 1050, 9000), ("middle-sight-f2.jpg", 1500, 5000),
                           ("middle-sight-f3.jpg", 1600, 3000), ("late-sight-f4.jpg", 1950, 8000)):
        path = crops / name
        path.write_bytes(b"x" * size)
        os.utime(path, (at, at))
    assert store.thumb(folder, summary).name == "middle-sight-f2.jpg"  # the most detailed of the middle half
    assert store.thumb(tmp_path / "none", summary) is None


# --- the Data light (checks.py) ------------------------------------------------------------------------
def recording_now(c: DatasetCollector, bridge: FakeBridge, clock: Clock, seq: int, video: bool = True) -> None:
    """One second of recording: the bridge writes the video and its frame times, the app a frame's facts."""
    if video:
        with bridge.next_path.open("ab") as f:
            f.write(b"fragment")
        with bridge.next_path.with_name(bridge.next_path.stem + ".frames.jsonl").open("a") as f:
            f.write(f'{{"i":{seq},"seq":{seq}}}\n')
        c.frame(frame_info(seq, seq=seq))
    c.writer.drain()
    clock.now += 1
    asyncio.run(c.tick())


def test_the_data_light_turns_blue_only_once_it_has_seen_every_file_grow(tmp_path):
    clock = Clock()
    c, bridge, sent = collector(tmp_path, clock)
    c.start()
    assert c.wire()["check"]["state"] == "checking"
    asyncio.run(c.tick())  # the bridge is asked; nothing has grown yet
    recording_now(c, bridge, clock, 1)  # the files exist: seen once, not yet seen growing
    assert c.wire()["check"]["state"] == "checking"
    recording_now(c, bridge, clock, 2)
    check = c.wire()["check"]
    assert check["state"] == "ok" and check["problems"] == [], check
    assert [i["name"] for i in check["items"]] == ["video", "times", "frames", "records", "disk"]
    assert sent[-1]["check"]["state"] == "ok"  # the studio is told as soon as it changes

    # The camera stops sending: after a few seconds the light says so, and what is still saved.
    for _ in range(int(checks.VIDEO_GROWING_S) + 1):
        recording_now(c, bridge, clock, 0, video=False)
    check = c.wire()["check"]
    assert check["state"] == "problem" and len(check["problems"]) == 1, check
    assert "No pictures from the camera" in check["problems"][0] and "Records are still saved" in check["problems"][0]
    recording_now(c, bridge, clock, 10)
    recording_now(c, bridge, clock, 11)
    assert c.wire()["check"]["state"] == "ok"
    c.stop()
    finish(c)
    lights = [e["record"]["state"] for e in lines(c.folder / "events.jsonl") if e["record"].get("event") == "light"]
    assert lights == ["ok", "problem", "ok"]  # when saving faltered is part of the dataset


def test_a_failing_check_is_a_problem_only_after_the_first_seconds(tmp_path):
    clock = Clock()
    c, bridge, _ = collector(tmp_path, clock)
    bridge.refuse = "recording ffmpeg did not start: no such file"
    c.start()
    asyncio.run(c.tick())
    assert c.wire()["check"]["state"] == "checking"
    clock.now += checks.FIRST_S
    asyncio.run(c.tick())
    check = c.wire()["check"]
    assert check["state"] == "problem"
    assert any("recording ffmpeg did not start" in p for p in check["problems"]), check["problems"]
    assert c.on  # the light only looks: the dataset goes on


def test_frames_without_the_bridges_number_cannot_be_joined_and_say_so(tmp_path):
    clock = Clock()
    c, bridge, _ = collector(tmp_path, clock)
    c.start()
    asyncio.run(c.tick())
    recording_now(c, bridge, clock, 1)
    recording_now(c, bridge, clock, 2)
    c.frame(frame_info(3, seq=None))
    recording_now(c, bridge, clock, 0, video=False)
    assert any("cannot be matched to the video" in p for p in c.wire()["check"]["problems"])


def test_after_stop_it_waits_for_the_bridge_to_finish_the_video_then_says_saved(tmp_path):
    clock = Clock()
    c, bridge, sent = collector(tmp_path, clock)
    c.start()
    asyncio.run(c.tick())
    recording_now(c, bridge, clock, 1)
    recording_now(c, bridge, clock, 2)
    c.stop()
    assert c.wire()["check"]["state"] == "saving"
    path = str(bridge.next_path)
    bridge.health["recording"] = {"running": False, "owners": [], "finishing": [{"path": path, "stage": "encoder"}]}
    finish(c)
    clock.now += 1
    asyncio.run(c.tick())
    assert c.wire()["check"]["state"] == "saving"  # the bridge is still finishing the file
    bridge.health["recording"] = {"running": False, "owners": [], "finishing": [],
                                  "lastSaved": {"path": path, "ok": True, "index": "front"}}
    clock.now += 1
    asyncio.run(c.tick())
    check = c.wire()["check"]
    assert check["state"] == "saved" and check["last"]["ok"] and check["id"] == c.folder.name, check
    end = manifest.read(c.folder)["endCheck"]
    assert end["ok"] and end["problems"] == [] and end["videoParts"] == 1
    assert end["counts"]["frames"] == 2  # the exact numbers, from the finished writer
    assert sent[-1]["check"]["state"] == "saved"
    clock.now += checks.SAVED_SHOW_S
    asyncio.run(c.tick())
    assert c.wire()["check"]["state"] == "off"


def test_a_video_that_did_not_finish_cleanly_stays_a_problem_until_the_next_recording(tmp_path):
    clock = Clock()
    c, bridge, _ = collector(tmp_path, clock)
    c.start()
    asyncio.run(c.tick())
    recording_now(c, bridge, clock, 1)
    c.stop()
    finish(c)
    bridge.health["recording"] = {"running": False, "owners": [], "finishing": [],
                                  "lastSaved": {"path": str(bridge.next_path), "ok": False, "error": "recording ffmpeg exited 1"}}
    clock.now += 1
    asyncio.run(c.tick())
    clock.now += 60
    asyncio.run(c.tick())
    check = c.wire()["check"]
    assert check["state"] == "problem" and "did not finish cleanly" in check["problems"][0]
    assert manifest.read(c.folder)["endCheck"]["ok"] is False
    c.start()
    assert c.wire()["check"]["state"] == "checking"


def test_no_camera_video_at_all_is_a_problem_at_the_end(tmp_path):
    clock = Clock()
    c, bridge, _ = collector(tmp_path, clock)
    bridge.refuse = "no camera"
    c.start()
    asyncio.run(c.tick())
    c.stop()
    finish(c)
    clock.now += 1
    asyncio.run(c.tick())
    check = c.wire()["check"]
    assert check["state"] == "problem" and check["problems"] == ["No camera video was recorded: no camera."]


def test_the_studio_recording_is_kept_in_the_dataset_as_it_grows(tmp_path):
    from orchestrator.recordings import add_recording_routes  # noqa: F401  (the route calls keep_screen)
    c, _, _ = collector(tmp_path)
    screen = tmp_path / "recordings" / "MicroscopeMind-20261006-012940.webm"
    screen.parent.mkdir(parents=True, exist_ok=True)
    screen.write_bytes(b"")
    c.start()
    c.keep_screen(screen, 1_791_264_580.0, finished=False)
    c.writer.drain()
    kept = c.folder / "video" / "screen-1.webm"
    assert os.stat(kept).st_ino == os.stat(screen).st_ino
    with screen.open("ab") as f:
        f.write(b"chunk 1")
    assert kept.read_bytes() == b"chunk 1"
    c.keep_screen(screen, 1_791_264_580.0, finished=True)
    c.stop()
    finish(c)
    screens = manifest.read(c.folder)["screen"]
    assert [(s["file"], s["finished"]) for s in screens] == [("video/screen-1.webm", True)]


# --- saving the session as a dataset, on the user's press, without stopping it (snapshot.py) -------------
def _session_on_disk(tmp_path):
    """A conversation of 20 minutes kept as the app keeps it: journal, interactions with a picture, a
    researcher still working, a finished camera recording with its sidecar, and microscope facts."""
    import time as clock_time

    paths = Paths.under(tmp_path)
    at = lambda h, m, s: clock_time.mktime((2026, 10, 7, h, m, s, 0, 0, -1))  # noqa: E731
    journal = paths.sessions / "2026-10-07" / "journal.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text("\n".join(json.dumps(e) for e in [
        {"at": at(17, 50, 0), "type": "preference", "key": "scale", "value": {"objective": 40, "fieldNumber": 18.0}},
        {"at": at(18, 0, 5), "type": "turn", "role": "user", "text": "what is that cell?"},
        {"at": at(18, 2, 0), "type": "research-linked", "thread": "t1", "rid": "r1"},
        {"at": at(18, 12, 0), "type": "preference", "key": "scale", "value": {"objective": 100, "fieldNumber": 18.0}},
    ]) + "\n")
    crop = paths.interaction_log.parent / "crops" / "2026-10-07" / "180100-click-f3.jpg"
    crop.parent.mkdir(parents=True)
    crop.write_bytes(b"jpeg")
    paths.interaction_log.write_text("\n".join(json.dumps(e) for e in [
        {"at": "2026-10-07T18:01:00", "kind": "click", "image": "crops/2026-10-07/180100-click-f3.jpg"},
        {"at": "2026-10-07T17:30:00", "kind": "click"},  # before the window
    ]) + "\n")
    research = paths.research / "2026-10-07" / "r1"
    research.mkdir(parents=True)
    (research / "session.json").write_text(json.dumps({"status": "running", "name": "Researcher 1"}))
    paths.recordings.mkdir(parents=True, exist_ok=True)
    (paths.recordings / "cam-1.mp4").write_bytes(b"the camera")
    (paths.recordings / "cam-1.frames.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"type": "header", "video": "cam-1.mp4", "bootId": "b1", "owner": "studio", "startedAtEpoch": at(18, 5, 0)},
        {"i": 0, "seq": 10, "bridgeAt": at(18, 5, 0), "src": "camera"},
        {"i": 1, "seq": 11, "bridgeAt": at(18, 8, 0), "src": "camera"},
        {"type": "saved", "at": at(18, 8, 1), "ok": True},
    ]) + "\n")
    (paths.recordings / "old.mp4").write_bytes(b"yesterday")  # another day: not in the window
    (paths.recordings / "old.frames.jsonl").write_text(json.dumps({"type": "header", "startedAtEpoch": at(9, 0, 0)}) + "\n"
                                                       + json.dumps({"i": 0, "bridgeAt": at(9, 1, 0)}) + "\n")
    import hashlib

    from orchestrator.evidence import SCHEMA

    def saved(eid, made, revisions=()):
        """As Evidence._save writes it: the files, then the manifest last, with their checksums."""
        ev = paths.evidence / "2026-10-07" / eid
        ev.mkdir(parents=True)
        files = {"capture.jpg": b"view", "masks.npz": b"masks-" + eid.encode()}
        for name, data in files.items():
            (ev / name).write_bytes(data)
        (ev / "manifest.json").write_text(json.dumps({
            "schema": SCHEMA, "id": eid, "revision": 1 + len(revisions), "madeAt": made, "operation": "cells",
            "implementation": "cellpose-sam", "revisions": list(revisions),
            "members": [{"id": "c1"}, {"id": "c2"}, {"id": "c3", "status": "deleted"}],
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}))
        return ev

    saved("e1", at(18, 4, 11))  # made in the window, whole
    saved("e2", at(17, 0, 0))  # made before the window and never touched in it: not in it
    saved("e4", at(17, 30, 0), revisions=[{"r": 1, "at": at(18, 6, 0)}])  # made before, corrected inside: in it
    torn = saved("e5", at(18, 7, 0))
    (torn / "masks.npz").write_bytes(b"a later save stopped part way")  # replaced, its manifest never rewritten
    writing = paths.evidence / "2026-10-07" / "e6"  # still being saved at the press: no manifest yet
    writing.mkdir(parents=True)
    (writing / "capture.jpg").write_bytes(b"view")
    with journal.open("a") as f:
        for eid, when in (("e6", at(18, 15, 0)), ("e7", at(18, 16, 0))):  # e7: made, nothing on disk yet
            f.write(json.dumps({"at": when, "type": "evidence-made", "id": eid, "lastMember": "c9"}) + "\n")
    paths.datasets.mkdir(parents=True, exist_ok=True)
    (paths.datasets / "rig.json").write_text(json.dumps({"facts": {
        "eyepiece": {"value": 10.0, "source": "user", "via": "studio", "at": at(17, 0, 0)},
        "microscope": {"value": "Amscope B120", "source": "user", "via": "studio", "at": at(18, 10, 0)}}}))
    return paths, at(18, 0, 0), at(18, 20, 0)


def test_a_preview_says_what_saving_the_session_would_hold_and_miss_and_writes_nothing(tmp_path):
    from orchestrator.datasets import snapshot

    paths, start, stop = _session_on_disk(tmp_path)
    review = snapshot.preview(paths, start, stop, "conversation")
    holds = review["holds"]
    assert holds["turns"] == 1 and holds["interactions"] == {"click": 1} and holds["pictures"] == 1
    assert holds["researchers"] == [{"rid": "r1", "name": "Researcher 1", "status": "running", "kept": True}]
    assert review["pending"] == [{"rid": "r1", "name": "Researcher 1", "status": "running"}]
    assert len(holds["cameraRecordings"]) == 1 and holds["cameraSeconds"] == 180.0
    assert any("Per-frame measurements are kept only while Record is on" in m for m in review["missing"])
    assert any("Camera video covers 3 min of the 20 min" in m for m in review["missing"])
    assert not any("objective was not set" in m for m in review["missing"])  # 40x was set before the window
    assert review["destination"].endswith("ds-20261007-180000") and "Nothing is uploaded" in review["staysHere"]
    assert holds["evidence"] == 2 and holds["evidenceMembers"] == 4  # e1 and e4 (corrected inside), deleted rows not counted
    assert {e["id"] for e in holds["evidenceIncomplete"]} == {"e5", "e6", "e7"}
    assert any("(e5) is not saved whole" in m for m in review["missing"])
    assert not any(paths.datasets.glob("ds-*"))  # a preview writes nothing


def test_saving_the_session_freezes_its_window_as_a_dataset_and_a_later_save_is_a_new_version(tmp_path):
    from orchestrator.datasets import snapshot
    from orchestrator.datasets.check import problems

    paths, start, stop = _session_on_disk(tmp_path)
    folder = snapshot.save(paths, start, stop, scope="conversation", name=" red cells ", build="abc123")
    summary = manifest.read(folder)
    assert folder.name == "ds-20261007-180000" and problems(folder) == []
    assert summary["endedBy"] == "snapshot" and summary["reconstructed"] and summary["name"] == "red cells"
    assert summary["snapshot"]["sessionContinues"] and summary["snapshot"]["uploaded"] is False
    assert summary["snapshot"]["pending"] == [{"rid": "r1", "status": "running"}]
    assert summary["reconstruction"]["researchers"][0]["pending"] is True
    texts = [e["record"].get("text") for e in lines(folder / "events.jsonl")]
    assert "what is that cell?" in texts
    kinds = [e["record"].get("kind") for e in lines(folder / "events.jsonl") if e["stream"] == "interaction"]
    assert kinds == ["click"]  # the click before the window is not in it
    assert (folder / "pictures" / "crops" / "2026-10-07" / "180100-click-f3.jpg").read_bytes() == b"jpeg"
    assert (folder / "research" / "r1" / "session.json").exists()
    part = summary["video"]["parts"][0]
    assert part["joined"] and part["finished"] and (folder / part["video"]).read_bytes() == b"the camera"
    assert (folder / part["sidecar"]).exists() and len(summary["video"]["parts"]) == 1  # yesterday's is not in it
    assert summary["rig"]["objective"]["value"] == 40 and [c["objective"] for c in summary["rigChanges"]] == [100]
    assert summary["rig"]["eyepiece"]["value"] == 10.0 and summary["rig"]["eyepiece"]["carriedOver"]
    assert summary["rig"]["microscope"] == {"value": None, "source": "unknown"}  # entered inside: never its past
    assert summary["rigEnteredAfter"][0]["value"] == "Amscope B120"
    assert summary["app"]["build"] == "abc123"
    status = {e["id"]: e["status"] for e in summary["evidence"]}
    assert status == {"e1": "whole", "e4": "whole", "e5": "incomplete", "e6": "incomplete", "e7": "incomplete"}
    e1 = [e for e in summary["evidence"] if e["id"] == "e1"][0]
    assert len(e1["files"]) == 3 and all(f["kept"] == "linked" for f in e1["files"])
    assert (folder / "pictures" / "evidence-2026-10-07-e1-r1-masks.npz").read_bytes() == b"masks-e1"
    e5 = [e for e in summary["evidence"] if e["id"] == "e5"][0]
    assert "does not match its manifest" in e5["why"] and e5["files"]  # kept, marked incomplete, never as whole
    assert "save the session again" in [e for e in summary["evidence"] if e["id"] == "e7"][0]["why"]
    assert sum("not saved whole" in line for line in summary["reconstruction"]["notCaptured"]) == 3
    again = snapshot.save(paths, start, stop + 60, scope="conversation")
    assert again.name == "ds-20261007-180000-2" and problems(again) == []  # never written over
    from orchestrator.datasets import store

    assert store.find(paths.datasets, again.name) == again


def test_saving_the_session_never_reads_the_optional_execution_log():
    import ast
    import inspect

    from orchestrator.datasets import importer, snapshot

    for module in (snapshot, importer):
        names = {a.name for n in ast.walk(ast.parse(inspect.getsource(module)))
                 if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        names |= {n.module for n in ast.walk(ast.parse(inspect.getsource(module))) if isinstance(n, ast.ImportFrom) and n.module}
        assert not any("execution" in name for name in names), module.__name__


def test_only_the_studios_save_makes_a_session_dataset(tmp_path):
    from fastapi.testclient import TestClient

    from orchestrator.server import create_app

    client = TestClient(create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path)))
    preview = client.get("/api/session-dataset?scope=today")
    assert preview.status_code == 200 and "missing" in preview.json()
    assert client.get("/api/session-dataset?scope=everything").status_code == 400
    assert client.post("/api/session-dataset", json={"scope": "conversation"}).status_code == 403
    assert client.post("/api/session-dataset", json={"scope": "conversation"},
                       headers={"X-MicroscopeMind": "save-session", "Origin": "https://example.org"}).status_code == 403
    saved = client.post("/api/session-dataset", json={"scope": "conversation", "name": "x"},
                        headers={"X-MicroscopeMind": "save-session"})
    assert saved.status_code in (200, 400), saved.text  # a conversation begun this instant may be empty


def test_no_tool_can_save_the_session_as_a_dataset():
    import pathlib

    toolbox = pathlib.Path(__file__).resolve().parents[1] / "orchestrator" / "brain"
    for path in toolbox.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "session-dataset" not in text and "snapshot.save" not in text, path  # ADR 0008: the user's press only


def test_a_recording_still_being_written_is_frozen_at_the_press(tmp_path):
    from orchestrator.datasets import snapshot
    from orchestrator.datasets.check import problems

    paths, start, stop = _session_on_disk(tmp_path)
    live = paths.recordings / "cam-2.frames.jsonl"
    (paths.recordings / "cam-2.mp4").write_bytes(b"still recording")
    lines_now = [{"type": "header", "video": "cam-2.mp4", "bootId": "b1", "owner": "dataset", "startedAtEpoch": stop - 60},
                 {"i": 0, "seq": 50, "bridgeAt": stop - 60, "src": "camera"},
                 {"i": 1, "seq": 51, "bridgeAt": stop - 1, "src": "camera"}]
    live.write_text("\n".join(json.dumps(e) for e in lines_now) + "\n" + '{"i": 2, "seq": 52, "bridg')  # half a line
    folder = snapshot.save(paths, start, stop, scope="conversation", clock=lambda: stop)
    with live.open("a") as f:  # the bridge carries on after the press
        f.write('eAt": %f}\n' % (stop + 5) + json.dumps({"i": 3, "seq": 53, "bridgeAt": stop + 6}) + "\n")
    summary = manifest.read(folder)
    part = [p for p in summary["video"]["parts"] if not p["finished"]][0]
    kept = lines(folder / part["sidecar"])
    assert [line.get("seq") for line in kept[1:]] == [50, 51]  # nothing after the press, no half line
    assert part["coversTo"]["seq"] == 51 and part["sidecarKept"] == "copied-at-press"
    assert os.stat(folder / part["video"]).st_ino == os.stat(paths.recordings / "cam-2.mp4").st_ino  # the video stays linked
    assert problems(folder) == []


def test_a_save_that_fails_leaves_no_dataset_behind(tmp_path, monkeypatch):
    import pytest

    from orchestrator.datasets import snapshot

    paths, start, stop = _session_on_disk(tmp_path)
    monkeypatch.setattr(snapshot, "problems", lambda folder, name=None: ["forced"])
    with pytest.raises(ValueError, match="does not pass the dataset check"):
        snapshot.save(paths, start, stop, scope="conversation")
    assert not [p.name for p in paths.datasets.iterdir() if p.name != "rig.json"]  # no dataset, no temporary folder
    monkeypatch.setattr(snapshot, "link_or_copy", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        snapshot.save(paths, start, stop, scope="conversation")
    assert not [p.name for p in paths.datasets.iterdir() if p.name != "rig.json"]
