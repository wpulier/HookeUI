"""Session sources (ADR 0022; docs/design/2026-10-12-session-sources.md), the server's half:
- the library: the user's own recordings and pictures, each with a library-wide id given once per stable key and its
  metadata (the app's, the user's edits over it); test footage is never an asset;
- a session's sources: opening associates, Record videos, snapshots and clearer pictures made in it join on their own,
  removing unlinks (its specimens leave the session) and Undo puts it back, and where a recording was left is kept;
- the stage: an asset's magnification is the scale while it is on stage, its user-given specimen wins over the looks,
  and the models read one line of its metadata (none for the live camera);
- the commands play it on the camera bridge (here a stand-in), and say what was done in Activity."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from orchestrator import commands, sources
from orchestrator.app import App
from orchestrator.brain import context
from orchestrator.commands import handle
from orchestrator.day.store import DayStore
from orchestrator.library import Library, is_asset_id
from orchestrator.paths import Paths
from orchestrator.scale import Scale
from orchestrator.server import create_app
from tests.test_session_view import dataset

DAY = "2026-10-12"
NIGHT = time.mktime((2026, 10, 12, 1, 44, 11, 0, 0, -1))


def store_with(tmp_path: Path) -> Path:
    """An assets store and app folders: one real microscope recording, a camera test off the microscope, an added
    video and an added picture, a Record dataset and a snapshot with outlines drawn (and its clean copy)."""
    footage = tmp_path / "assets" / "footage"
    (footage / "real").mkdir(parents=True)
    (footage / "added").mkdir()
    (footage / "real" / "2026-06-14-leaf.mp4").write_bytes(b"\x00" * 32)
    (footage / "real" / "2026-06-10-room.mp4").write_bytes(b"\x00" * 32)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"real": [
        {"id": "2026-06-14-leaf", "file": "footage/real/2026-06-14-leaf.mp4", "title": "Leaf epidermis", "specimen": "leaf epidermis",
         "date": "2026-06-14", "seconds": 223.0, "microscope": True, "optics": {"camera": "iPhone", "objective": 40, "umPerPx": None}},
        {"id": "2026-06-10-room", "file": "footage/real/2026-06-10-room.mp4", "title": "Camera test", "microscope": False}]}))
    (footage / "added" / "sperm.mp4").write_bytes(b"\x00" * 32)
    (footage / "added" / "sperm.mp4.json").write_text(json.dumps({"title": "Sperm motility", "specimen": "sperm", "seconds": 12.5}))
    (footage / "added" / "smear.png").write_bytes(b"\x89PNG")
    dataset(tmp_path / "datasets", "ds-20261012-014411", NIGHT, name="Sperm sample")
    snaps = tmp_path / "snapshots" / DAY
    snaps.mkdir(parents=True)
    stem = "snapshot-014500-abc-f12"
    (snaps / f"{stem}.json").write_text(json.dumps({"wallAt": NIGHT + 49, "overlays": True, "session": DAY,
                                                     "scale": {"objective": 40, "umPerPx": 0.31}}))
    (snaps / f"{stem}.png").write_bytes(b"\x89PNG drawn")
    (snaps / "clean").mkdir()
    (snaps / "clean" / f"{stem}.png").write_bytes(b"\x89PNG clean")
    return manifest


def library_over(tmp_path: Path, origin=None, clock=None) -> Library:
    manifest = store_with(tmp_path)
    return Library(tmp_path / "library.jsonl", footage=tmp_path / "assets" / "footage", manifest=manifest,
                   datasets=tmp_path / "datasets", snapshots=tmp_path / "snapshots",
                   origin_of=origin or (lambda kind, ident, at: DAY), clock=clock)


# --- the library ---------------------------------------------------------------------------------------------
def test_the_library_lists_the_users_own_recordings_and_pictures_never_test_footage(tmp_path):
    library = library_over(tmp_path)
    found = {a.key: a for a in library.assets().values()}
    assert set(found) == {"footage:2026-06-14-leaf", "footage:sperm", "footage:smear", "dataset:ds-20261012-014411/1",
                          "snapshot:2026-10-12/snapshot-014500-abc-f12"}  # never the camera test off the microscope
    leaf = found["footage:2026-06-14-leaf"]
    assert leaf.kind == "recording" and leaf.group == "recording" and leaf.metadata()["optics"] == {"objective": 40.0}
    assert leaf.metadata()["specimen"] == "leaf epidermis" and leaf.metadata()["specimenBy"] == "record"
    assert found["footage:smear"].kind == "still" and found["footage:sperm"].seconds == 12.5
    record = found["dataset:ds-20261012-014411/1"]
    assert record.group == "record" and record.title == "Sperm sample" and record.origin == DAY and record.seconds == 90.0
    snapshot = found["snapshot:2026-10-12/snapshot-014500-abc-f12"]
    # Its clean picture: outlines drawn on it would be taken for the specimen.
    assert snapshot.path.parent.name == "clean" and snapshot.kind == "still" and snapshot.origin == DAY
    assert snapshot.metadata()["optics"] == {"objective": 40.0, "umPerPx": 0.31}


def test_a_file_just_added_is_found_at_once_when_asked_fresh(tmp_path, monkeypatch):
    """The studio adds a file (the bridge's upload) and then finds it by its name to open it: `fresh` reads the folders
    again, where the scan of the last few seconds would not have it yet."""
    # Cache age is an external clock, not the time App.build takes on this machine.
    now = [0.0]
    monkeypatch.setattr('orchestrator.library.time', SimpleNamespace(**{**vars(time), 'monotonic': lambda: now[0]}))
    library = library_over(tmp_path)
    assert library.search("2026-10-12-onion") == []  # scanned now: not there yet
    added = tmp_path / "assets" / "footage" / "added"
    (added / "2026-10-12-onion-picture.png").write_bytes(b"\x89PNG")
    (added / "2026-10-12-onion-picture.png.json").write_text(json.dumps({"title": "Onion, 2026-10-12 (picture)", "kind": "still"}))
    assert library.search("2026-10-12-onion") == []  # the scan of a moment ago
    found = library.search("2026-10-12-onion", fresh=True)
    assert [(a.title, a.kind, a.path.name) for a in found] == [("Onion, 2026-10-12 (picture)", "still", "2026-10-12-onion-picture.png")]

    # The route takes it as a query parameter.
    web = create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path / "home"))
    web.state.hub.library = library
    http = TestClient(web)
    (added / "2026-10-12-moss-picture.png").write_bytes(b"\x89PNG")
    assert http.get("/api/library", params={"q": "moss"}).json()["assets"] == []
    got = http.get("/api/library", params={"q": "moss", "fresh": 1}).json()["assets"]
    assert [a["file"] for a in got] == ["2026-10-12-moss-picture.png"] and got[0]["thumb"] == f"/api/library/{got[0]['id']}/thumbnail"
    (added / "2026-10-12-fern-picture.png").write_bytes(b"\x89PNG")
    assert library.search("fern") == []
    now[0] = 6.0  # the unchanged five-second cache expires without fresh=True
    assert [a.path.name for a in library.search("fern")] == ["2026-10-12-fern-picture.png"]


def test_upload_staging_never_becomes_an_asset_before_publication(tmp_path):
    library = library_over(tmp_path)
    original = {a.key: a.id for a in library.assets().values()}
    added = tmp_path / 'assets' / 'footage' / 'added'
    temporary = added / '.upload-123-456.png'
    temporary.write_bytes(b'\x89PNG still uploading')
    assert {a.key: a.id for a in library.assets(fresh=True).values()} == original
    assert library.search('.upload', fresh=True) == []
    published = added / 'onion-picture.png'
    temporary.rename(published)
    found = library.search('onion-picture', fresh=True)
    assert len(found) == 1 and found[0].path == published
    assert library.get(found[0].id).path.is_file()
    assert all(library.get(aid) is not None for aid in original.values())
    reopened = Library(tmp_path / 'library.jsonl', footage=tmp_path / 'assets' / 'footage')
    assert reopened.get(found[0].id).path == published


def test_older_scan_cannot_hide_a_newly_published_upload(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    library = library_over(tmp_path)
    scanned, release = Event(), Event()
    read = library._footage
    def slow_first():
        found = read()
        if not scanned.is_set():
            scanned.set()
            assert release.wait(5)
        return found
    monkeypatch.setattr(library, '_footage', slow_first)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(library.assets, True)
        try:
            assert scanned.wait(5)
            path = tmp_path / 'assets' / 'footage' / 'added' / 'new-upload.png'
            path.write_bytes(b'\x89PNG')
            assert len(library.search('new-upload', fresh=True)) == 1
        finally:
            release.set()
        old.result(timeout=5)
    assert len(library.search('new-upload')) == 1


def test_ids_are_given_once_per_key_and_kept_across_a_restart_and_an_adoption(tmp_path):
    library = library_over(tmp_path)
    ids = {a.key: a.id for a in library.assets().values()}
    assert all(is_asset_id(i) for i in ids.values()) and len(set(ids.values())) == len(ids)
    again = Library(tmp_path / "library.jsonl", footage=tmp_path / "assets" / "footage", manifest=tmp_path / "manifest.json",
                    datasets=tmp_path / "datasets", snapshots=tmp_path / "snapshots")
    assert {a.key: a.id for a in again.assets().values()} == ids
    # `make assets-adopt` moves an added file among the real recordings: the same file name, the same id.
    footage = tmp_path / "assets" / "footage"
    (footage / "added" / "sperm.mp4").rename(footage / "real" / "sperm.mp4")
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["real"].append({"id": "sperm", "file": "footage/real/sperm.mp4", "title": "Sperm motility", "microscope": True})
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert {a.key: a.id for a in again.assets(fresh=True).values()}["footage:sperm"] == ids["footage:sperm"]


def test_the_users_fields_win_and_are_checked(tmp_path):
    library = library_over(tmp_path)
    leaf = next(a for a in library.assets().values() if a.key == "footage:2026-06-14-leaf")
    library.set_meta(leaf.id, {"specimen": "onion epidermis", "objective": 10, "notes": "the second slide"})
    meta = library.get(leaf.id).metadata()
    assert meta["specimen"] == "onion epidermis" and meta["specimenBy"] == "user" and meta["optics"]["objective"] == 10.0
    assert library.edits(leaf.id)["specimen"] == "onion epidermis"
    for bad, words in (({"objective": 900}, "objective is between"), ({"colour": "red"}, "not a field"), ({}, "nothing to set")):
        try:
            library.set_meta(leaf.id, bad)
        except ValueError as exc:
            assert words in str(exc)
        else:
            raise AssertionError(bad)
    library.set_meta(leaf.id, {"specimen": ""})  # cleared: the record's own again
    assert library.get(leaf.id).metadata()["specimen"] == "leaf epidermis"
    reread = Library(tmp_path / "library.jsonl", footage=tmp_path / "assets" / "footage", manifest=tmp_path / "manifest.json")
    assert reread.edits(leaf.id) == {"objective": 10.0, "notes": "the second slide"}


def test_a_clearer_picture_is_registered_when_made(tmp_path):
    library = library_over(tmp_path)
    picture = tmp_path / "results" / DAY / "d3-0.jpg"
    picture.parent.mkdir(parents=True)
    picture.write_bytes(b"\xff\xd8")
    aid = library.register(f"result:{DAY}/d3/0", picture, title="Clearer picture · 01:50", at=NIGHT + 360, origin=DAY,
                           optics={"objective": 40.0})
    asset = library.get(aid)
    assert asset.group == "clearer" and asset.kind == "still" and asset.origin == DAY and asset.scale_optics() == {"objective": 40.0}
    assert Library(tmp_path / "library.jsonl").get(aid) is not None  # kept with its facts


# --- a session's sources ---------------------------------------------------------------------------------------
def test_opening_associates_removing_unlinks_with_its_specimens_and_undo_brings_them_back(tmp_path):
    clock = [NIGHT]
    day = DayStore(root=tmp_path / "sessions", day=DAY, clock=lambda: clock[0])
    library = library_over(tmp_path)
    by_key = {a.key: a for a in library.assets().values()}
    sperm, leaf = by_key["footage:sperm"].id, by_key["footage:2026-06-14-leaf"].id
    day.open_source("live")
    day.set_subject("onion epidermis", "look")
    clock[0] += 60
    day.open_source(sperm)
    day.set_subject("sperm cells", "look")
    day.source_left_at(sperm, 12.5)
    clock[0] += 60
    day.open_source(leaf)
    listed = sources.rows(DAY, library.assets(), day)
    # Newest first: what was opened, and what was made in it (its Record video, its snapshot) on its own.
    assert [r["id"] for r in listed["sources"]][:2] == [leaf, sperm] and listed["onStage"] == leaf
    joined = {r["id"]: r for r in listed["sources"] if r["joined"]}
    assert {library.key_of(i) for i in joined} == {"dataset:ds-20261012-014411/1", "snapshot:2026-10-12/snapshot-014500-abc-f12"}
    assert next(r for r in listed["sources"] if r["id"] == sperm)["position"] == 12.5

    assert day.remove_source(sperm) and not day.remove_source(sperm) and not day.remove_source("live")
    assert sperm not in [r["id"] for r in sources.rows(DAY, library.assets(), day)["sources"]]
    # Its specimen leaves the session: its conversation's subjects, the session's, the sessions list's.
    assert [s["name"] for s in day.active.subjects] == ["onion epidermis"]
    assert list(day.subjects) == ["onion epidermis"]
    assert day.catalogue.get(DAY).specimens == ["onion epidermis"]
    reread = DayStore(root=tmp_path / "sessions", day=DAY, clock=lambda: clock[0])
    assert list(reread.subjects) == ["onion epidermis"] and sperm in reread.removed_sources
    assert day.restore_source(sperm)
    assert list(day.subjects) == ["onion epidermis", "sperm cells"]
    assert day.catalogue.get(DAY).specimens == ["onion epidermis", "sperm cells"]
    assert sources.rows(DAY, library.assets(), day.catalogue.get(DAY))["onStage"] == leaf  # another session reads it the same


def test_a_name_the_app_took_from_a_removed_sources_specimen_goes_with_it_and_undo_brings_it_back(tmp_path):
    clock = [NIGHT]
    day = DayStore(root=tmp_path / "sessions", day=DAY, clock=lambda: clock[0])
    day.open_source("a3")
    day.set_subject("blood smear", "look")
    day.rename_session("blood smear", by="app", auto=True)  # as subject.py names an unnamed session
    assert day.name == "Blood smear" and not day.named
    day.remove_source("a3")
    assert day.name == "" and day.catalogue.get(DAY).name == ""  # nothing else seen: unnamed again
    day.restore_source("a3")
    assert day.name == "Blood smear" and day.catalogue.get(DAY).name == "Blood smear"
    clock[0] += 60
    day.open_source("live")
    day.set_subject("onion epidermis", "look")
    day.remove_source("a3")
    assert day.name == "Onion epidermis"  # the next specimen it shows
    day.restore_source("a3")
    day.rename_session("My slides")  # the user's own name
    day.remove_source("a3")
    assert day.name == "My slides" and day.named  # stays, whatever is removed


def journal(root: Path, sid: str, name: str, events: list[dict]) -> None:
    folder = root / sid
    folder.mkdir(parents=True, exist_ok=True)
    head = {"type": "session-started", "session": sid, "name": name, "by": "user", "created": NIGHT, "at": NIGHT}
    (folder / "journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in [head, *events]))


def test_each_source_says_the_other_sessions_that_use_it_newest_first(tmp_path):
    """"Also in: <session>" (the user's Q27): each source row names the other sessions that use the same asset, so
    earlier work on it is one click away: those that opened it and did not remove it, by when they last opened it, and
    the one it was made in when it joined there on its own; at most three, newest first."""
    from orchestrator.day.catalogue import Catalogue

    library = library_over(tmp_path)
    by_key = {a.key: a for a in library.assets().values()}
    sperm, leaf = by_key["footage:sperm"].id, by_key["footage:2026-06-14-leaf"].id
    record = by_key["dataset:ds-20261012-014411/1"].id  # made in DAY (its origin): joined there on its own
    root = tmp_path / "sessions"

    def opened(aid, at):
        return {"type": "source", "asset": aid, "op": "opened", "at": NIGHT + at}

    journal(root, DAY, "Night smear", [opened(sperm, 10)])
    journal(root, f"{DAY}.2", "Sperm motility", [opened(sperm, 30), opened(leaf, 31)])
    journal(root, f"{DAY}.3", "Wrong clip", [opened(sperm, 40), {"type": "source", "asset": sperm, "op": "removed",
                                                                  "at": NIGHT + 41}, opened(record, 42)])
    journal(root, f"{DAY}.4", "Today", [opened("live", 50), opened(sperm, 51), opened(leaf, 52), opened(record, 53)])
    infos = Catalogue(root).list()
    here = next(i for i in infos if i.id == f"{DAY}.4")
    listed = {r["id"]: r for r in sources.rows(here.id, library.assets(), here, infos)["sources"]}
    assert listed[sperm]["alsoIn"] == [{"id": f"{DAY}.2", "label": "Sperm motility"}, {"id": DAY, "label": "Night smear"}]
    assert listed[leaf]["alsoIn"] == [{"id": f"{DAY}.2", "label": "Sperm motility"}]
    # The Record video: opened in .3, and made in DAY (joined there on its own, by when it was made).
    assert listed[record]["alsoIn"] == [{"id": f"{DAY}.3", "label": "Wrong clip"}, {"id": DAY, "label": "Night smear"}]
    # From the session it was made in: the others that opened it; never itself.
    first = next(i for i in infos if i.id == DAY)
    mine = {r["id"]: r for r in sources.rows(DAY, library.assets(), first, infos)["sources"]}
    assert [s["id"] for s in mine[record]["alsoIn"]] == [f"{DAY}.4", f"{DAY}.3"]
    # At most three; with no catalogue given, none named.
    for n in range(5, 8):
        journal(root, f"{DAY}.{n}", f"More {n}", [opened(sperm, 60 + n)])
    infos = Catalogue(root).list()
    listed = {r["id"]: r for r in sources.rows(here.id, library.assets(), here, infos)["sources"]}
    assert [s["id"] for s in listed[sperm]["alsoIn"]] == [f"{DAY}.7", f"{DAY}.6", f"{DAY}.5"]
    assert all(r["alsoIn"] == [] for r in sources.rows(here.id, library.assets(), here)["sources"])


def test_the_sources_route_names_the_other_sessions_that_use_each_asset(tmp_path):
    web = create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path / "home"))
    built = web.state.app
    library = library_over(tmp_path)
    web.state.hub.library = library
    sperm = next(a for a in library.assets().values() if a.key == "footage:sperm").id
    first = built.day.sid
    built.day.open_source(sperm)
    built.rename_session(first, "Sperm motility")
    built.new_session("Second look")
    second = built.day.sid
    built.day.open_source(sperm)
    http = TestClient(web)

    def row(sid):
        body = http.get(f"/api/sessions/{sid}/sources").json()
        return next(r for r in body["sources"] if r["id"] == sperm)

    assert row(second)["alsoIn"] == [{"id": first, "label": "Sperm motility"}]
    assert row(first)["alsoIn"] == [{"id": second, "label": "Second look"}]


# --- the stage -------------------------------------------------------------------------------------------------
def test_an_assets_magnification_is_the_scale_while_it_is_on_stage(tmp_path):
    scale = Scale(None)
    scale.set_objective(10)
    told = []
    scale.asset_set = lambda aid, fields: told.append((aid, fields))
    assert scale.reading(100.0).objective == 10
    scale.use_asset("a4", {"objective": 40.0})
    reading = scale.reading(100.0)
    assert reading.objective == 40 and abs(reading.field_um - 18000 / 40) < 1e-6
    assert scale.wire(100.0)["asset"] == "a4" and scale.facts()["objective"]["source"] == "asset"
    scale.use_asset("a5", {})  # an asset with no magnification states no sizes
    assert scale.reading(100.0) is None
    scale.set_objective(40)  # on stage, the objective said is the asset's (its metadata), not the microscope's
    assert told == [("a5", {"objective": 40.0})] and scale.reading(100.0).objective == 40
    reading = scale.calibrate(50.0, 25.0, 100.0)  # measured on it: its µm per pixel
    assert told[-1] == ("a5", {"umPerPx": 0.5}) and reading.um_per_px == 0.5 and scale.reading(80.0).um_per_px == 0.5
    scale.use_asset(None)
    assert scale.objective == 10 and scale.reading(100.0).objective == 10  # the live camera's, unchanged


def test_a_user_given_specimen_wins_and_a_new_source_is_named_at_once(tmp_path):
    built = App.build("http://127.0.0.1:1", Paths.under(tmp_path / "home"), use_gpu=False)
    subjects = built.subjects
    subjects.observe(SimpleNamespace(specimen="onion epidermis", same=None))
    assert subjects.subject == "onion epidermis"
    subjects.source_changed("")  # another source: the next look names it, no second look needed
    assert built.hub.scene.context.get("specimen", "") == ""
    subjects.observe(SimpleNamespace(specimen="sperm cells", same=False))
    assert subjects.subject == "sperm cells"
    subjects.pinned = lambda: "sperm sample"
    subjects.observe(SimpleNamespace(specimen="something else", same=False))
    subjects.observe(SimpleNamespace(specimen="something else", same=False))
    assert subjects.subject == "sperm cells"  # the looks never change it while the user's name holds


def test_the_models_read_the_stage_as_one_line_of_metadata_and_nothing_on_live(tmp_path):
    built = App.build("http://127.0.0.1:1", Paths.under(tmp_path / "home"), use_gpu=False)

    def rendered() -> list[str]:
        m = context.capture(built.brain)
        return ["".join(context.scene_table(m)), context.fast_fields(m)["screen"], context.view_note(m),
                context.voice_recap(m)]

    assert not any("On stage" in text for text in rendered())
    built.hub.stage_meta = {"id": "a7", "kind": "recording", "title": "Sperm sample", "captured": "Oct 12, 1:44 am",
                            "seconds": 1593.0, "optics": {"objective": 40.0}, "specimen": "sperm", "specimenBy": "user"}
    line = sources.stage_line(built.hub.stage_meta)
    assert line == ("the stage shows asset a7: a recording, 'Sperm sample', captured Oct 12, 1:44 am, 26 min 33 s long, "
                    "40x objective, specimen 'sperm' (named by the user)")
    assert all(line in text for text in rendered())


# --- the commands ----------------------------------------------------------------------------------------------
class Client:
    def __init__(self):
        self.replies = []

    def offer(self, message):
        self.replies.append(json.loads(message))


def test_the_source_commands_play_on_the_bridge_and_say_what_was_done(tmp_path, monkeypatch):
    built = App.build("http://127.0.0.1:1", Paths.under(tmp_path / "home"), use_gpu=False)
    library = library_over(tmp_path)
    built.hub.library = library
    by_key = {a.key: a for a in library.assets().values()}
    leaf, smear = by_key["footage:2026-06-14-leaf"], by_key["footage:smear"]
    calls = []

    async def bridge(app, method, path):
        calls.append((method, path))
        return {"playback": {"seconds": 31.0}} if method == "GET" else {"ok": True}

    monkeypatch.setattr(commands, "_bridge", bridge)
    client = Client()

    def send(kind, **msg) -> dict:
        asyncio.run(handle(built, client, {"type": kind, "requestId": "s", **msg}))
        return client.replies[-1]

    def activity() -> list[str]:
        return [line["text"] for line in built.day.active.lines if line.get("type") == "activity"]

    reply = send("source", open=leaf.id, paused=True, at=12.0)
    assert reply["ok"] and reply["onStage"] == leaf.id and reply["title"] == "Leaf epidermis"
    assert calls[-1][0] == "POST" and "source=asset" in calls[-1][1] and "paused=1" in calls[-1][1] and "at=12.000" in calls[-1][1]
    assert built.day.on_stage == leaf.id and built.hub.stage_meta["id"] == leaf.id
    assert built.hub.scale.reading(100.0).objective == 40  # its own magnification
    send("source", open=smear.id)  # a picture: always held; where the leaf recording was is kept
    assert "paused=1" in calls[-1][1] and built.day.positions[leaf.id] == 31.0
    assert built.hub.scale.reading(100.0) is None  # no magnification: no sizes
    send("asset-meta", asset=smear.id, set={"specimen": "blood smear", "objective": 100})
    assert built.subjects.subject == "blood smear" and built.hub.scale.reading(100.0).objective == 100
    removed = send("source-remove", asset=smear.id)
    assert removed["wasOnStage"] and removed["next"] == leaf.id and built.hub.stage_meta is None
    assert send("source-restore", asset=smear.id)["restored"] == smear.id
    send("source", open="live")
    assert calls[-1] == ("POST", "/feed?source=camera") and built.hub.stage_meta is None
    assert built.hub.scale.reading(100.0) is None or built.hub.scale.asset is None
    send("source", open="live", switch=False)  # the studio switched the feed itself (its test footage stands for live)
    assert calls[-1] == ("POST", "/feed?source=camera")  # nothing more was asked of the bridge
    assert activity() == ["You put “Leaf epidermis” on stage", "You put “smear” on stage", "You changed the details of “smear”",
                          "You took “smear” out of this session", "You put “smear” back in this session", "You went live",
                          "You went live"]
    refused = send("source", open="a999")
    assert refused["ok"] is False and "no asset a999" in refused["error"]
    assert send("source-remove", asset="live")["ok"] is False


# --- after the first live try (2026-10-12 18:20) ---------------------------------------------------------------
def test_example_data_is_in_the_library_apart_from_the_users_own(tmp_path):
    manifest = store_with(tmp_path)
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps({"items": [{"id": "pond", "specimen": "pond water"},
                                            {"id": "pond-seah", "specimen": "sea water", "optics": {"umPerPx": 0.5}}]}))
    folder = tmp_path / "assets" / "footage" / "corpus"
    folder.mkdir()
    for name in ("corpus-pond.mp4", "corpus-pond-1.mp4", "corpus-pond-seah.mp4", "corpus-pond-seah.truth.json", "corpus-unknown.mp4"):
        (folder / name).write_bytes(b"\x00")
    library = Library(None, footage=tmp_path / "assets" / "footage", manifest=manifest, corpus=corpus)
    examples = {a.title: a for a in library.search(scope="example")}
    assert set(examples) == {"Pond water", "Pond water (2)", "Sea water"}  # the longest id that fits; unknown ones left out
    assert examples["Sea water"].group == "example" and examples["Sea water"].scale_optics() == {"umPerPx": 0.5}
    assert examples["Pond water"].specimen_now() == "pond water"
    mine = library.search(scope="mine")
    assert mine and all(a.group != "example" for a in mine)
    assert len(library.search()) == len(mine) + len(examples)
    # The route offers example data by specimen (each with how many and its newest), a page at a time.
    web = create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path / "home"))
    web.state.hub.library = library
    http = TestClient(web)
    body = http.get("/api/library", params={"scope": "example"}).json()
    assert body["count"] == 3 and [(s["name"], s["count"]) for s in body["specimens"]] == [("pond water", 2), ("sea water", 1)]
    assert body["specimens"][1]["first"]["title"] == "Sea water" and body["specimens"][1]["first"]["thumb"].endswith("/thumbnail")
    pond = http.get("/api/library", params={"scope": "example", "specimen": "Pond  Water", "limit": 1}).json()
    assert pond["count"] == 2 and len(pond["assets"]) == 1
    rest = http.get("/api/library", params={"scope": "example", "specimen": "pond water", "offset": 1, "limit": 1}).json()
    assert {pond["assets"][0]["title"], rest["assets"][0]["title"]} == {"Pond water", "Pond water (2)"}
    assert "specimens" not in http.get("/api/library", params={"scope": "mine"}).json()  # only example data is offered by specimen
    assert http.get("/api/library", params={"limit": 0}).json() == {"assets": []}


def test_a_look_at_a_frame_from_before_the_change_never_names_the_session(tmp_path):
    built = App.build("http://127.0.0.1:1", Paths.under(tmp_path / "home"), use_gpu=False)
    subjects = built.subjects
    subjects.observe(SimpleNamespace(specimen="onion epidermis", same=None, frame_at=time.time()))
    before = time.time() - 1.0  # a frame taken before the source changes
    subjects.source_changed("")
    assert subjects.observe(SimpleNamespace(specimen="semen sample", same=False, frame_at=before)) is False
    assert built.hub.scene.context.get("specimen", "") == ""  # nothing named from it
    subjects.observe(SimpleNamespace(specimen="sperm cells", same=False, frame_at=time.time() + 0.1))
    assert subjects.subject == "sperm cells"


def test_another_session_opened_is_not_looked_at_until_its_source_lands(tmp_path, monkeypatch):
    built = App.build("http://127.0.0.1:1", Paths.under(tmp_path / "home"), use_gpu=False)
    built.hub.clients.add(Client())  # a studio is there to land it

    async def bridge(app, method, path):
        return {"ok": True}

    monkeypatch.setattr(commands, "_bridge", bridge)

    async def scenario():
        built.day.new()  # the user starts another session: held until the studio lands it
        assert built.hub.view_held is True and not built.sight.wanted()
        await handle(built, Client(), {"type": "source", "requestId": "s", "open": "live", "switch": False})
        assert built.hub.view_held is False and built.sight.wanted()
        # A landing for a session left meanwhile (switching quickly) is refused: nothing of it goes in this one.
        left = built.day.sid
        built.day.new()
        studio = Client()
        await handle(built, studio, {"type": "source", "requestId": "s", "open": "live", "switch": False, "session": left})
        assert studio.replies[-1]["ok"] is False and "no longer open" in studio.replies[-1]["error"] and built.hub.view_held is True
        await handle(built, Client(), {"type": "source", "requestId": "s", "open": "live", "switch": False, "session": built.day.sid})
        assert built.hub.view_held is False
        # Another session opened while the bridge switched (a seek in a long recording takes seconds): not put in it.
        async def switching(app, method, path):
            if method == "POST":
                built.day.new()
            return {"ok": True}

        monkeypatch.setattr(commands, "_bridge", switching)
        asked_in = built.day.sid
        await handle(built, studio, {"type": "source", "requestId": "s", "open": "live", "session": asked_in})
        assert studio.replies[-1]["ok"] is False and built.day.sid != asked_in and built.day.on_stage is None
        built.day.new()
        await handle(built, Client(), {"type": "view", "requestId": "v", "held": True})  # its home: the studio's own hold
        assert built._landing is None and built.hub.view_held is True
        await handle(built, Client(), {"type": "view", "requestId": "v", "held": False})
        assert built.hub.view_held is False

    asyncio.run(scenario())


def test_landings_switch_the_bridge_one_at_a_time_in_the_order_asked(tmp_path, monkeypatch):
    """Switching sessions quickly sends one landing each: run together, the first one's slow switch (a seek in a long
    recording) finished last and left the bridge on a session left (live 2026-10-12, the second stack)."""
    built = App.build("http://127.0.0.1:1", Paths.under(tmp_path / "home"), use_gpu=False)
    built.hub.clients.add(Client())
    order = []

    async def bridge(app, method, path):
        if method == "POST":
            n = len([o for o in order if o[0] == "start"]) + 1
            order.append(("start", n))
            await asyncio.sleep(0.2 if n == 1 else 0.0)  # the first is slow
            order.append(("end", n))
        return {"ok": True}

    monkeypatch.setattr(commands, "_bridge", bridge)

    async def scenario():
        first, second = Client(), Client()
        left = built.day.sid
        slow = asyncio.create_task(handle(built, first, {"type": "source", "requestId": "1", "open": "live", "session": left}))
        await asyncio.sleep(0.05)
        built.day.new()  # the user went on to another session meanwhile
        await asyncio.gather(slow, handle(built, second, {"type": "source", "requestId": "2", "open": "live", "session": built.day.sid}))
        assert order == [("start", 1), ("end", 1), ("start", 2), ("end", 2)]  # the bridge ends on the last one asked
        assert first.replies[-1]["ok"] is False and second.replies[-1]["ok"] is True
        assert built.day.on_stage == "live" and built.hub.view_held is False

    asyncio.run(scenario())
