"""The stores and the session's index once a session is the project (ADR 0017; the plan's P3 "Records and ids" and
P4 "Index", "Identity", "Bookmarks and snapshots"):

- every id the user sees (e3, c12, m2, d4, b1, i5, k1) is new both for the session and for the date folder it is
  filed in (day/ids.py), so two sessions on one date never share one and a session resumed on a later date never
  gives one of its own again;
- every record says the session it was made in, and is filed in that session's journal;
- the session list's "made" count (catalogue.product_of) is what Made in this session lists (produces.is_product), and
  the user's own pictures and recordings the session made (a snapshot, a clearer picture, a Record dataset: its
  sources, ADR 0022) are its own all the same, in the index and claimed by its journal, but no products;
- the index answers for a session: an entry is (session, id), found wherever it was filed;
- a session resumed two days later brings back its work as history, and no live outline.
"""
import json
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from orchestrator.bookmarks import Bookmarks
from orchestrator.capture import Capture
from orchestrator.day.store import DayStore
from orchestrator.evidence import Evidence
from orchestrator.held import Held
from orchestrator.identity import Identity, member_node
from orchestrator.motiontracks import Tracks, TracksStore
from orchestrator.produces import is_product
from orchestrator.resources import Index, Results
from tests.test_evidence import population, saved
from tests.test_motion_tracks import wait_saved
from tests.test_resources import OCT_7

DAY = "2026-10-07"


def jpeg(value=120, w=160, h=120):
    return cv2.imencode(".jpg", np.full((h, w, 3), value, np.uint8))[1].tobytes()


def view(fid=7, value=120):
    info = SimpleNamespace(id=fid, width=160, height=120, epoch=0, dx=0.0, dy=0.0, fov=None, disc=0.0, wall_at=1000.0)
    return Capture(fid, jpeg(value), info)


class Stores:
    """The per-date stores over one session store, as App.build wires them (no GPU, no camera)."""

    def __init__(self, root, day):
        self.root, self.day = root, day
        self.hub = SimpleNamespace(execution_day=day, scene=SimpleNamespace(objects={}))
        self.evidence = self.hub.evidence = Evidence(self.hub, root / "evidence", day=day)
        self.tracks = self.hub.tracks_store = TracksStore(self.hub, root / "evidence", day=day)
        self.results = Results(root / "results", day=day, pictures=lambda key: jpeg(90))
        self.bookmarks = Bookmarks(root / "bookmarks", today=lambda: day.date, store=day)
        self.identity = self.hub.identity = Identity(root / "identity", today=lambda: day.date, hub=self.hub, store=day)
        self.held = self.hub.held = Held(self.hub)
        self.held.store = day
        self.index = self.hub.resources = Index(self.hub, results=self.results, snapshots=root / "snapshots", day=day,
                                                bookmarks=self.bookmarks, identity=self.identity,
                                                recordings=root / "recordings", datasets=root / "datasets")

    def population(self):
        p = population(self.evidence, view())
        p.day, p.session, p.thread = self.day.date, self.day.sid, self.day.active_id
        self.evidence.add(p)
        self.evidence.schedule_save(p)
        assert saved(p).status == "saved"
        return p

    def motion(self):
        capture = view(8, 60)
        rows = [{"id": "t1", "values": {"net": 20.0, "path": 21.0, "straightness": 0.95, "moving": True},
                 "segments": [{"epoch": 0, "start": "first", "end": "end", "n": 2}]}]
        points = [[np.array([[10.0, 10.0, 100.0, 8], [30.0, 10.0, 101.0, 8]])]]
        item = Tracks(self.tracks.new_id(), "measured", "motion", "spots", {"execution": "x-test", "origin": "brain-default"},
                      8, {"epoch": 0, "dx": 0.0, "dy": 0.0, "disc": None, "width": 160, "height": 120, "at": 1000.0},
                      None, {"from": 100.0, "to": 101.0, "seconds": 1.0, "frames": 2}, {"counted": 1, "moving": 1},
                      {"umPerPx": None, "basis": None}, rows, points, capture=capture, picture=capture.jpeg,
                      thread=self.day.active_id, day=self.day.date, session=self.day.sid)
        self.tracks.add(item)
        self.tracks.schedule_save(item)
        assert wait_saved(item) == "saved"
        return item

    def bookmark(self):
        fov = SimpleNamespace(cx=80.0, cy=60.0, radius=50.0)
        capture = SimpleNamespace(fov=fov, tracked=True, dx=0.0, dy=0.0, id=7, seq=3, boot="b", epoch=0, wall_at=1000.0,
                                  width=160, height=120, disc=0.0)
        mark = self.bookmarks.add(capture, note="the joint", thread=self.day.active_id)
        self.bookmarks.journal(mark)
        folder = self.root / "bookmarks" / mark["day"]  # its picture, as a view decoded on a hub keeps one
        (folder / f"{mark['id']}.jpg").write_bytes(jpeg(70))
        mark = {**mark, "picture": f"{mark['id']}.jpg"}
        (folder / f"{mark['id']}.json").write_text(json.dumps(mark))
        return mark

    def snapshot(self, name="snapshot-101500-0123456789ab-f7"):
        folder = self.root / "snapshots" / self.day.date
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.png").write_bytes(cv2.imencode(".png", np.zeros((8, 8, 3), np.uint8))[1].tobytes())
        (folder / f"{name}.json").write_text(json.dumps({"frameId": 7, "wallAt": 1000.0, "outlines": [], "session": self.day.sid}))
        self.day.made("snapshot", name, day=self.day.date)  # as the snapshot tool does (brain/toolbox/seeing.py)
        return name

    def every_product(self):
        """One of each product kind (produces.PRODUCT_KINDS, and a result kept as a picture: a reference photo), as
        their producers make them, and the ids made; with them the user's own pictures and recording the session
        makes, which are its sources and no products (ADR 0022; their keys are SOURCES): a snapshot, a clearer picture
        and a Record dataset."""
        p = self.population()
        made = {"population": p.id, "tracks": self.motion().id, "bookmark": self.bookmark()["id"],
                "snapshot": self.snapshot(), "individual": self.identity.make([member_node(p.id, next(iter(p.members)))]),
                "clip": self.held.start("r1", "Researcher 1", "q1", 5, self.day.date).id,
                "result": self.results.add("reference_photo", {}, "a reference photo", picture_bytes=(jpeg(180),),
                                           web={"site": "commons.wikimedia.org"})["id"],
                "clearer": self.results.add("enhance", {}, "a clearer picture", picture_bytes=(jpeg(200),))["id"]}
        self.results.add("count", {}, "6 cells")  # an answer, not a product
        recordings = self.root / "recordings"
        recordings.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.day.clock()))
        rid = f"MicroscopeMind-{stamp}-abcdef123456"
        (recordings / f"{rid}.webm").write_bytes(b"x" * 10)
        self.day.made("recording", rid)  # as the recording route does (recordings.py `made`)
        made["recording"] = rid
        dataset = f"{time.strftime('%Y-%m-%d-%H%M%S', time.localtime(self.day.clock()))}"
        self.index._datasets_cache = (time.monotonic() + 3600, [{"id": dataset, "startedAtEpoch": self.day.clock(),
                                                                 "status": "done", "counts": {}}])
        self.datasets_dir()
        self.day.made("dataset", dataset)  # as Record does (commands.py `dataset {on}`)
        made["dataset"] = dataset
        self.day.link_research("r1")  # a researcher started in the session (its research-linked event)
        made["research"] = "r1"
        return made

    def datasets_dir(self):
        (self.root / "datasets").mkdir(exist_ok=True)


SOURCES = ("snapshot", "clearer", "dataset")  # every_product's keys of what is a source, not a product (ADR 0022)


def researchers_of(*sessions):
    """A research manager's `researchers(session=)` (research/manager.py) over stand-in researchers."""
    def researchers(day=None, session=None):
        return [s for s in sessions if s.sitting == session]
    return SimpleNamespace(researchers=researchers, read_day=lambda day, kept_only=True: [], get=lambda rid: None,
                           root=Path("/nonexistent-research"))


def researcher(rid, sitting):
    return SimpleNamespace(rid=rid, n=1, name="Researcher 1", deliverable="research", created=1000.0, status="done",
                           origin={"thread": "t1"}, result=None, effort="quick", engine="x", day=sitting[:10],
                           sitting=sitting, line=lambda: "Researcher 1: notes", readable=lambda **kw: "notes",
                           current_outputs=lambda: [])


# --- ids ----------------------------------------------------------------------------------------------------
def test_two_sessions_on_one_date_never_share_an_id_and_a_resumed_session_never_reuses_its_own(tmp_path):
    clock = [OCT_7]
    day = DayStore(root=tmp_path / "sessions", day=DAY, clock=lambda: clock[0])
    first = Stores(tmp_path, day).every_product()
    members_a = set(Stores(tmp_path, day).evidence.get(first["population"]).members)

    assert day.new() == f"{DAY}.2"  # another session, the same date: every store taken afresh (a restart)
    later = Stores(tmp_path, day)
    second = later.every_product()
    members_b = set(later.evidence.get(second["population"]).members)
    for kind in ("population", "tracks", "result", "bookmark", "individual", "clip"):
        assert first[kind] != second[kind], kind  # e, m, d, b, i, k: new for the date folder
    assert not members_a & members_b  # c: new for the date folder too

    clock[0] = OCT_7 + 2 * 86400  # session one, resumed two days later in a new run of the app
    day.switch_session(DAY)
    again = Stores(tmp_path, day)
    resumed = again.every_product()
    members_c = set(again.evidence.get(resumed["population"]).members)
    for kind in ("population", "tracks", "result", "bookmark", "individual", "clip"):
        assert resumed[kind] != first[kind], kind  # never one the session gave before, on any of its dates
    assert not members_a & members_c
    journal = [json.loads(line) for line in (tmp_path / "sessions" / DAY / "journal.jsonl").read_text().splitlines()]
    assert {e.get("day") for e in journal if e["type"] in ("evidence-made", "tracks-made", "made", "result")} == {
        DAY, time.strftime("%Y-%m-%d", time.localtime(OCT_7 + 2 * 86400))}


# --- records say their session ------------------------------------------------------------------------------
def test_every_record_says_the_session_it_was_made_in(tmp_path):
    day = DayStore(root=tmp_path / "sessions", day=DAY, clock=lambda: OCT_7)
    day.new()
    stores = Stores(tmp_path, day)
    made = stores.every_product()
    sid = day.sid
    manifest = json.loads((tmp_path / "evidence" / DAY / made["population"] / "manifest.json").read_text())
    tracks = json.loads((tmp_path / "evidence" / DAY / made["tracks"] / "manifest.json").read_text())
    mark = json.loads((tmp_path / "bookmarks" / DAY / f"{made['bookmark']}.json").read_text())
    events = [json.loads(x) for x in (tmp_path / "identity" / DAY / "events.jsonl").read_text().splitlines()]
    assert manifest["session"] == tracks["session"] == mark["session"] == sid
    assert {e["session"] for e in events} == {sid}
    assert day.results[made["result"]]["session"] == sid and stores.held.clips[made["clip"]].session == sid
    journal = [json.loads(x) for x in day.journal.path.read_text().splitlines()]
    filed = {(e["type"], e.get("id")) for e in journal}
    assert {("evidence-saved", made["population"]), ("tracks-saved", made["tracks"]), ("result", made["result"]),
            ("made", made["bookmark"]), ("made", made["individual"]), ("made", made["clip"])} <= filed
    assert not (tmp_path / "sessions" / DAY / "journal.jsonl").read_text().count('"evidence-saved"')  # never the other's


# --- the session list counts what Made in this session lists --------------------------------------------------
def test_the_session_lists_made_count_is_what_made_in_this_session_lists(tmp_path):
    day = DayStore(root=tmp_path / "sessions", day=DAY, clock=lambda: OCT_7)
    stores = Stores(tmp_path, day)
    stores.index.research = researchers_of(researcher("r1", DAY))
    made = stores.every_product()
    entries, unreadable = stores.index.collect()
    products = sorted((e.kind, e.id) for e in entries if is_product(e.kind, e.made_by))
    assert unreadable == []
    # Footage held for a researcher is memory only, gone at a restart: the list counts what is kept, and the open
    # session's wire adds what is held now (held.Held), so the two still agree.
    held = len(stores.held.clips)
    assert held == 1 and len(products) == day.catalogue.get(DAY).counts["made"] + held == 8
    kinds = {"population": "population", "tracks": "tracks", "bookmark": "bookmark", "snapshot": "snapshot",
             "individual": "individual", "clip": "clip", "result": "result", "clearer": "result", "recording": "recording",
             "dataset": "dataset", "research": "research"}
    assert products == sorted((kinds[k], v) for k, v in made.items() if k not in SOURCES)
    assert {k: v for k, v in day.catalogue.get(DAY).made.items()} == {
        (kinds[k], v): DAY for k, v in made.items() if k not in SOURCES and k != "clip"}
    # Its sources are the session's all the same: in the index (no products), and claimed by its journal.
    listed = {(e.kind, e.id): e for e in entries}
    for k in SOURCES:
        assert (kinds[k], made[k]) in listed and listed[(kinds[k], made[k])].wire()["product"] is False, k
    assert {("snapshot", made["snapshot"]), ("dataset", made["dataset"])} <= set(day.catalogue.get(DAY).claims)
    # A result discarded after it was kept (a comparison stopped while saving) is taken back from both.
    gone = stores.results.add("reference_photo", {}, "another", picture_bytes=(jpeg(10),), web={"site": "commons"})
    assert day.catalogue.get(DAY).counts["made"] + held == 9
    stores.results._discard_comparison(SimpleNamespace(entry=gone))
    assert len([e for e in stores.index.collect()[0] if is_product(e.kind, e.made_by)]) == \
        day.catalogue.get(DAY).counts["made"] + held == 8


# --- the index answers for a session ---------------------------------------------------------------------------
def test_the_index_reads_a_session_by_its_id_at_every_entry_point(tmp_path):
    """`day` is a session id everywhere in the index (None: the one open); an entry is (session, id), found in the
    date folder its session's journal filed it in. An old day's id is its date's session."""
    clock = [OCT_7]
    day = DayStore(root=tmp_path / "sessions", day=DAY, clock=lambda: clock[0])
    old = Stores(tmp_path, day).every_product()  # the date's own session, as every day journal before sessions
    clock[0] = OCT_7 + 86400  # the next day: a new session, the date's own, then a second one that date (".2")
    assert day.new() == time.strftime("%Y-%m-%d", time.localtime(clock[0]))
    clock[0] = OCT_7 + 86400 + 60
    newer_sid = day.new()
    assert newer_sid.endswith(".2")
    stores = Stores(tmp_path, day)
    new = stores.every_product()
    index = stores.index
    assert index.session() == newer_sid and index.is_open(None) and not index.is_open(DAY)
    for sid, made in ((newer_sid, new), (DAY, old)):
        for kind in ("population", "tracks", "result", "bookmark", "snapshot"):
            entry = index.get(made[kind], sid)
            assert entry is not None and entry.day == sid and entry.date, (sid, kind)
            assert entry.wire()["day"] == sid and entry.wire()["date"] == entry.date
            found = index.picture(made[kind], 0, sid)
            assert found is not None and found[0], (sid, kind)
            assert all(f"day={sid}" in url for url in entry.pictures)
        assert index.masks(made["population"], sid) is not None
        document = index.document(made["population"], sid)
        assert document is not None and document["entry"]["day"] == sid
        assert [e.id for e in index.list("bookmark", sid)] == [made["bookmark"]]
    # Another session's ids that the open one shares are never taken for its own.
    if new["population"] == old["population"]:
        assert index.get(new["population"]).date != index.get(old["population"], DAY).date
    assert index.summary(DAY)["day"] == DAY and index.summary()["day"] == newer_sid


# --- resume two days later -------------------------------------------------------------------------------------
def test_a_session_resumed_two_days_later_brings_back_its_work_as_history_and_no_live_outline(tmp_path):
    from orchestrator.paths import Paths
    from orchestrator.server import create_app

    paths = Paths.under(tmp_path)
    then = time.time() - 2 * 86400
    day = DayStore(root=paths.sessions, day=time.strftime("%Y-%m-%d", time.localtime(then)), clock=lambda: then)
    sid = day.sid
    stores = Stores(paths.home, day)  # Paths.under: every store's folder under the one home, as here
    p = stores.population()
    item = stores.motion()
    result = stores.results.add("enhance", {}, "a clearer picture", picture_bytes=(jpeg(200),))
    mark = stores.bookmark()
    snap = stores.snapshot()
    day.add_turn("user", "chat", "outline every cell")  # the last session used
    day.user_prefs.point(sid)

    app = create_app("http://127.0.0.1:1", use_gpu=False, paths=paths).state.app  # a new run, two days later
    index = app.hub.resources
    assert app.day.sid == sid and app.day.date != sid  # the same session, on today's date
    assert not app.hub.scene.objects and app.hub.layer.shown is None  # its live outlines are never restored
    for eid, kind in ((p.id, "population"), (item.id, "tracks"), (result["id"], "result"), (mark["id"], "bookmark"),
                      (snap, "snapshot")):
        entry = index.get(eid, sid)
        assert entry is not None and entry.kind == kind and entry.day == sid, (eid, kind)
        assert index.picture(eid, 0, sid) is not None, (eid, kind)
    assert app.hub.evidence.get(p.id).status == "saved" and app.hub.tracks_store.get(item.id).status == "saved"
    assert [m["id"] for m in app.services.bookmarks.all()] == [mark["id"]]
    # Ids stay new for the session after the resume.
    assert app.hub.evidence.new_id() != p.id and app.hub.tracks_store.new_id() != item.id
    assert index.results.add("count", {}, "7 cells")["id"] != result["id"]
    assert app.services.bookmarks._next_id(paths.bookmarks / app.day.date, sid) != mark["id"]  # in today's folder too


def test_a_save_filed_from_another_thread_during_a_session_switch_stays_in_its_own_session(tmp_path, monkeypatch):
    """CI, 2026-10-26 (test_motion_tracks, 1 run in 8 here): a tracks save finishing on its writer thread chose its
    session ("the one open: this store") just before the user's new session opened on another thread, then wrote; the
    write landed in the new session's journal and its list. Here the save pauses between choosing and writing while
    the new session opens; it must still be filed in the session it was made in."""
    import threading

    day = DayStore(root=tmp_path / "sessions", day="2026-10-07", clock=lambda: 1791374400.0)
    made_in = day.sid
    chosen, go = threading.Event(), threading.Event()
    real = DayStore.elsewhere

    def pausing(self, sid):
        store = real(self, sid)
        if threading.current_thread().name == "saver":
            chosen.set()
            go.wait(2)
        return store

    monkeypatch.setattr(DayStore, "elsewhere", pausing)
    saver = threading.Thread(name="saver", target=lambda: day.record_tracks("m1", str(tmp_path / "m1"), None,
                                                                                session=made_in))
    saver.start()
    assert chosen.wait(2)
    opener = threading.Thread(target=day.new)
    opener.start()
    opener.join(0.3)  # the new session opens now, or waits for the save being filed
    go.set()
    saver.join(3)
    opener.join(3)
    assert not saver.is_alive() and not opener.is_alive()
    assert day.sid != made_in and "m1" not in day.tracks  # never in the session opened meanwhile
    assert "m1" in DayStore(root=tmp_path / "sessions", session=made_in, clock=lambda: 1791374400.0).tracks
