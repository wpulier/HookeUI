from orchestrator.magnifier import Magnifier
"""Bookmarks (M3 of docs/design/2026-10-06-captures-operations-evidence.md): a place on the slide kept with the
user's words, listed in the session's one index with its picture, and the way back to it measured from how
far the slide has moved since; said when that can't be told (another motion epoch, another run)."""
import asyncio
import time
from types import SimpleNamespace

from orchestrator.bookmarks import Bookmarks
from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.brain.toolbox.where import add_where_tools
from orchestrator.fov import FieldOfView
from orchestrator.ticket import issue, using
from tests.test_apply import World

import pytest


def setting_up(tmp_path):
    world = World()
    world.latest_info.fov = FieldOfView(640.0, 360.0, 300.0, 1.0)
    bookmarks = Bookmarks(tmp_path / "bookmarks", today=lambda: "2026-10-07")
    registry = Registry()
    add_where_tools(registry, SimpleNamespace(hub=world, slidemap=None, bookmarks=bookmarks, day=None, log=None))
    return world, bookmarks, registry


def call(world, registry, name, args):
    async def go():
        with using(issue(world, "turn")):
            return await registry.tools[name].handler(ToolContext("chat", 1, None, name), args)
    return asyncio.run(go())


def frame(world, dx, dy, epoch=0):
    info = world.frame(dx, dy, epoch=epoch)
    info.fov = FieldOfView(640.0, 360.0, 300.0, 1.0)
    return info


def test_a_kept_place_is_found_again_by_how_far_the_slide_moved(tmp_path):
    world, bookmarks, registry = setting_up(tmp_path)
    said = call(world, registry, "bookmark", {"note": "the nice stomata"})
    assert said.startswith('kept this place on the slide as "the nice stomata"')
    (mark,) = bookmarks.all()
    assert mark["id"] == "b1" and mark["picture"] == "b1.jpg" and (tmp_path / "bookmarks/2026-10-07/b1.jpg").is_file()
    frame(world, 900.0, 0.0)  # the slide moved 900 px: the bookmarked view is 1.5 fields away
    back = call(world, registry, "way_to_bookmark", {"which": "stomata"})
    assert back.startswith('the bookmark "the nice stomata" (') and "is about 1.5 fields" in back and "Measured from how far" in back


def test_the_way_back_is_never_guessed_across_a_lost_epoch_or_another_run(tmp_path):
    world, bookmarks, registry = setting_up(tmp_path)
    call(world, registry, "bookmark", {})
    frame(world, 0.0, 0.0, epoch=1)  # the slide's movement was lost since
    assert "can't tell the way from here: the slide's motion lost continuity" in call(world, registry, "way_to_bookmark", {})
    mark = bookmarks.all()[-1]
    mark_file = tmp_path / "bookmarks/2026-10-07/b1.json"
    import json

    kept = json.loads(mark_file.read_text())
    kept["run"], kept["epoch"] = "an-earlier-run", 1
    mark_file.write_text(json.dumps(kept))
    assert "kept in an earlier run of the app" in call(world, registry, "way_to_bookmark", {})
    assert mark["id"] == "b1"


def test_nothing_bookmarked_is_said_with_what_to_do(tmp_path):
    world, _, registry = setting_up(tmp_path)
    with pytest.raises(ToolFailed) as refused:
        call(world, registry, "way_to_bookmark", {})
    assert "nothing has been bookmarked today" in str(refused.value) and refused.value.next


def test_bookmarks_are_in_the_one_index_and_open_as_their_picture(tmp_path):
    from orchestrator.resources import Index

    world, bookmarks, registry = setting_up(tmp_path)
    call(world, registry, "bookmark", {"note": "the joint"})
    index = Index(world, bookmarks=bookmarks, day=SimpleNamespace(day="2026-10-07", sid="2026-10-07", date="2026-10-07"))
    (entry,) = index.list("bookmark")
    assert entry.title.startswith("Bookmark · ") and entry.title.endswith("· the joint") and "open" in entry.actions
    data, media = index.picture("b1")
    assert media == "image/jpeg" and data[:2] == b"\xff\xd8"


def test_a_kept_place_is_logged_whole_so_a_dataset_has_it(tmp_path):
    world = World()
    world.latest_info.fov = FieldOfView(640.0, 360.0, 300.0, 1.0)
    bookmarks = Bookmarks(tmp_path / "bookmarks", today=lambda: "2026-10-07")
    logged = []
    registry = Registry()
    add_where_tools(registry, SimpleNamespace(hub=world, slidemap=None, bookmarks=bookmarks, day=None, log=logged.append))
    call(world, registry, "bookmark", {"note": "the joint"})
    (line,) = [r for r in logged if r.get("kind") == "bookmark"]
    record = line["bookmark"]  # nested: its own time never overwrites the log's (review B1)
    assert record["id"] == "b1" and record["note"] == "the joint" and record["centre"] == [640.0, 360.0]
    assert {"epoch", "offset", "seq", "run", "frame", "width", "height", "disc", "tracked"} <= set(record)


def test_opening_a_bookmark_shows_its_picture_said_to_be_the_cameras_view(tmp_path):
    from orchestrator.brain.toolbox.present import add_present_tools
    from orchestrator.resources import Index
    from tests.test_screen import present, setup

    world, bookmarks, registry = setting_up(tmp_path)
    call(world, registry, "bookmark", {"note": "the joint"})
    world.broadcast = lambda message: None
    _, _, studio = setup(world)


    world.resources = Index(world, bookmarks=bookmarks, day=SimpleNamespace(day="2026-10-07", sid="2026-10-07", date="2026-10-07"))
    tools = Registry()
    magnifier = Magnifier(world)
    add_present_tools(tools, SimpleNamespace(hub=world, magnifier=magnifier, presenter=None,
                                             slidemap=None, agent=None, publish_settings=None, log=None, history_log=None))
    said = present(tools, world, what="b1", do="open")
    assert "Opened existing Bookmark" in said and "b1" in said
    source = studio.items["magnifier"]
    assert source["source"].startswith("/api/picture/") and source["pictures"]
    assert "camera's view when it was kept" in magnifier.picture_now()["label"]
    assert "not the live view" in magnifier.picture_now()["label"]




# --- the partner's review of M3.3 ---------------------------------------------------------------------------
def _bookmark_logged(tmp_path):
    """A bookmark kept through the where tools and the agent's real log (Agent.log, looplag.LINES), then the window
    around it gathered as a dataset would gather it. Returns what was gathered and the pictures kept."""
    import time as _time

    from orchestrator.agent import Agent
    from orchestrator.datasets.importer import gather
    from orchestrator.paths import Paths

    world = World()
    world.latest_info.fov = FieldOfView(640.0, 360.0, 300.0, 1.0)
    bookmarks = Bookmarks(tmp_path / "bookmarks", today=lambda: "2026-10-07")
    paths = Paths.under(tmp_path) if hasattr(Paths, "under") else Paths.from_env({"MICROMIND_HOME": str(tmp_path)})
    agent = Agent.__new__(Agent)
    agent.interaction_log, agent.tags, agent.taps = paths.interaction_log, None, []
    kept = []
    registry = Registry()
    add_where_tools(registry, SimpleNamespace(hub=world, slidemap=None, bookmarks=bookmarks, day=None,
                                              log=lambda r: Agent.log(agent, r), keep=lambda path, name: kept.append((path, name))))
    t0 = _time.time() - 2
    call(world, registry, "bookmark", {"note": "the joint"})
    return gather(paths, t0, _time.time() + 2, None), kept


def test_a_bookmark_logged_through_the_real_log_reaches_a_dataset_with_its_picture(tmp_path, monkeypatch):
    """Review B1 and B2: the bookmark's own "at" overwrote the log's and the importer skipped the line; with Record
    on, its picture was never put in the dataset."""
    got, kept = _bookmark_logged(tmp_path)
    (line,) = [e["record"] for e in got.events if e["record"].get("kind") == "bookmark"]
    assert line["bookmark"]["note"] == "the joint" and isinstance(line["at"], str)
    assert kept and kept[0][1] == "bookmark-2026-10-07-b1.jpg" and kept[0][0].is_file()


def test_a_dataset_reads_a_line_the_log_writer_has_not_written_yet(tmp_path, monkeypatch):
    """CI, 2026-10-26 (run 40398880650, attempt 2): the tool ran on an event loop, so its log line went to the writer
    thread (looplag.LINES) and the window was gathered before that thread wrote it: no bookmark in the dataset. Here
    the writer thread neither starts nor wakes, so the line stays queued; gather writes waiting lines before it reads,
    as dialogue's reader does."""
    from orchestrator.looplag import LINES

    monkeypatch.setattr(LINES, "_start", lambda: None)
    monkeypatch.setattr(LINES._ready, "notify", lambda *args, **kwargs: None)
    got, _kept = _bookmark_logged(tmp_path)
    assert [e for e in got.events if e["record"].get("kind") == "bookmark"], "the queued bookmark line was not read"


def test_the_way_back_is_never_measured_where_the_views_cant_be_related(tmp_path):
    """Review B3: an untracked view is never kept; a changed eyepiece scale or objective is "can't tell"."""
    world, bookmarks, registry = setting_up(tmp_path)
    world.latest_info.tracked = False
    with pytest.raises(ToolFailed) as moving:
        call(world, registry, "bookmark", {})
    assert "was not registered" in str(moving.value)
    info = frame(world, 0.0, 0.0)  # a new view, its motion registered
    info.tracked, info.disc = True, 300.0
    call(world, registry, "bookmark", {"note": "joint"})
    info = frame(world, 10.0, 0.0)
    info.disc = 150.0  # the lens zoomed
    assert "can't tell the way from here: the eyepiece's scale changed" in call(world, registry, "way_to_bookmark", {})
    info.disc = 300.0
    mark_file = tmp_path / "bookmarks/2026-10-07/b1.json"
    import json

    kept = json.loads(mark_file.read_text())
    kept["objective"] = "10x"
    mark_file.write_text(json.dumps(kept))
    world.scale = SimpleNamespace(objective="40x")
    assert "can't tell the way from here: the objective changed" in call(world, registry, "way_to_bookmark", {})


def test_same_words_are_all_said_and_a_letter_matches_no_word(tmp_path):
    """Review S2."""
    world, bookmarks, registry = setting_up(tmp_path)
    call(world, registry, "bookmark", {"note": "nice stomata"})
    call(world, registry, "bookmark", {"note": "stomata near the vein"})
    said = call(world, registry, "way_to_bookmark", {"which": "stomata"})
    assert said.count("the bookmark") == 2
    with pytest.raises(ToolFailed) as none:
        call(world, registry, "way_to_bookmark", {"which": "a"})
    assert "today's are b1" in str(none.value) and none.value.next == "name one of those"
    assert call(world, registry, "way_to_bookmark", {"which": "the first one"}).startswith('the bookmark "nice stomata"')


def test_in_view_is_said_from_the_slides_movement_never_as_seen(tmp_path):
    """Review S3."""
    world, bookmarks, registry = setting_up(tmp_path)
    call(world, registry, "bookmark", {})
    said = call(world, registry, "way_to_bookmark", {})
    assert "by the slide's movement, it should be in view" in said and "compare with its picture" in said


def test_a_bookmark_is_filed_under_the_conversation_it_was_asked_in(tmp_path):
    """Review S1."""
    world, bookmarks, registry = setting_up(tmp_path)

    async def go():
        context = ToolContext("chat", 1, None, "bookmark this")
        context.asked = SimpleNamespace(thread="t1", generation=None)
        with using(issue(world, "turn")):
            return await registry.tools["bookmark"].handler(context, {})
    asyncio.run(go())
    assert bookmarks.all()[-1]["thread"] == "t1" and bookmarks.all()[-1]["asked"] == "bookmark this"


def test_a_full_memory_bound_keeps_the_bookmark_without_its_picture_and_never_decodes_uncounted(tmp_path, monkeypatch):
    """The M3.3 re-check, B4: when the allowance refuses, no raw decode in its place; said with the bookmark."""
    import contextlib
    import threading

    import orchestrator.capture as capture_mod
    from orchestrator import bookmarks as bookmarks_mod

    world, bookmarks, registry = setting_up(tmp_path)
    where = []

    @contextlib.contextmanager
    def refused(*a, **k):
        where.append(threading.current_thread() is threading.main_thread())
        raise capture_mod.Refused("held views are full: 900 MB; clear earlier analyses or wait")
        yield

    monkeypatch.setattr(capture_mod, "decoding", refused)
    monkeypatch.setattr(bookmarks_mod.cv2, "imdecode", lambda *a, **k: pytest.fail("decoded outside the memory bound"))
    said = call(world, registry, "bookmark", {"note": "the joint"})
    (mark,) = bookmarks.all()
    assert "picture" not in mark and mark["pictureMissing"].startswith("no picture: held views are full")
    assert "(no picture: held views are full" in said and said.startswith('kept this place on the slide as "the joint"')
    assert where == [False]  # decoded (here: refused) in a thread, never on the loop


def test_two_bookmarks_at_once_are_both_kept(tmp_path, monkeypatch):
    """The M3.3 re-check: taking an id and writing are one step; two at once never share an id."""
    import threading

    world, bookmarks, registry = setting_up(tmp_path)
    real = Bookmarks._next_id

    def slow(self, folder, session=None):
        found = real(self, folder, session)
        time.sleep(0.05)  # widen the window between taking the id and writing it
        return found

    monkeypatch.setattr(Bookmarks, "_next_id", slow)
    capture = world.latest_info
    capture.jpeg = None
    out = []
    threads = [threading.Thread(target=lambda n=n: out.append(bookmarks.add(capture, note=f"place {n}"))) for n in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(m["id"] for m in out) == ["b1", "b2"] and sorted(m["note"] for m in bookmarks.all()) == ["place 1", "place 2"]


def test_a_bookmark_is_listed_in_its_own_session_under_its_conversation_and_never_in_another(tmp_path):
    """The M3.3 re-check, S1, re-scoped to sessions (ADR 0017; the plan's P4): a bookmark keeps its session and
    conversation; its session lists it under that conversation (a session's conversations keep their ids for good), on
    whatever date it is opened; a new session, with its own t2, never lists it. One kept before sessions names its
    conversation only while it is still that one (the old rule), and a request from before the session was loaded
    again files it in no conversation."""
    from orchestrator.bookmarks import conversation_of
    from orchestrator.day.store import DayStore
    from orchestrator.resources import Index
    from tests.test_resources import OCT_7

    world, bookmarks, registry = setting_up(tmp_path)
    old = {"id": "b1", "thread": "t2", "at": 1000.0}  # kept before sessions: no "session"
    loaded = lambda threads: SimpleNamespace(sid="2026-10-07", threads=threads)
    assert conversation_of(old, loaded({"t2": SimpleNamespace(started=900.0)})) == "t2"
    assert conversation_of(old, loaded({"t2": SimpleNamespace(started=2000.0)})) is None
    assert conversation_of(old, loaded({})) is None
    assert conversation_of({**old, "session": "2026-10-07"}, loaded({})) == "t2"  # its session's, whatever is loaded
    stale = SimpleNamespace(current=lambda generation: generation == 2)
    registry = Registry()
    add_where_tools(registry, SimpleNamespace(hub=world, slidemap=None, bookmarks=bookmarks, day=stale, log=None))

    async def go():
        context = ToolContext("chat", 1, None, "bookmark this")
        context.asked = SimpleNamespace(thread="t2", generation=1)  # asked before the session was loaded again
        with using(issue(world, "turn")):
            return await registry.tools["bookmark"].handler(context, {})
    asyncio.run(go())
    assert bookmarks.all()[-1]["thread"] is None

    clock = [OCT_7]
    day = DayStore(root=tmp_path / "sessions", day="2026-10-07", clock=lambda: clock[0])
    day.open_thread("second look")  # t2
    marks = Bookmarks(tmp_path / "kept", today=lambda: day.date, store=day)
    mark = marks.add(frame(world, 0.0, 0.0), note="old t2", thread="t2", hub=world, session=day.sid)
    marks.journal(mark)
    assert (mark["session"], mark["day"]) == ("2026-10-07", "2026-10-07")
    index = Index(world, bookmarks=marks, day=day)
    entry = next(e for e in index.list("bookmark") if e.summary.startswith("old t2"))
    assert (entry.thread, entry.day, entry.date) == ("t2", "2026-10-07", "2026-10-07")
    clock[0] = OCT_7 + 2 * 86400  # two days later, a new session with its own t2
    day.new()
    day.open_thread("another second look")
    assert index.list("bookmark") == [] and marks.all() == []
    assert [e.thread for e in index.list("bookmark", day="2026-10-07")] == ["t2"]  # its own session's
    assert index.picture(mark["id"], 0, "2026-10-07") is not None and index.picture(mark["id"]) is None
    newer = marks.add(frame(world, 0.0, 0.0), note="new t2", thread="t2", hub=world)
    marks.journal(newer)
    assert newer["id"] != mark["id"] or newer["day"] != mark["day"]  # ids: new for the date folder and the session
    assert [e.summary for e in index.list("bookmark")] == ["new t2"]


def test_without_the_eyepiece_nothing_is_kept_or_measured_and_the_way_back_is_logged(tmp_path):
    """The M3.3 re-check, S4: both no-eyepiece refusals, and the measurement record."""
    world = World()
    bookmarks = Bookmarks(tmp_path / "bookmarks", today=lambda: "2026-10-07")
    logged = []
    registry = Registry()
    add_where_tools(registry, SimpleNamespace(hub=world, slidemap=None, bookmarks=bookmarks, day=None, log=logged.append))
    with pytest.raises(ToolFailed) as kept:
        call(world, registry, "bookmark", {})
    assert "eyepiece circle is not in view" in str(kept.value) and not bookmarks.all()
    frame(world, 0.0, 0.0)  # a view with the eyepiece in it
    call(world, registry, "bookmark", {})
    frame(world, 0.0, 0.0).fov = None
    with pytest.raises(ToolFailed) as way:
        call(world, registry, "way_to_bookmark", {})
    assert "can't see the eyepiece circle" in str(way.value)
    frame(world, 40.0, 0.0)
    call(world, registry, "way_to_bookmark", {})
    (record,) = [r for r in logged if r.get("kind") == "measurement" and r.get("what") == "bookmark"]
    assert record["bookmark"] == "b1" and record["known"] is True and record["dxPx"] is not None


def test_the_tools_and_the_index_are_wired_in_the_app(tmp_path):
    """The M3.3 re-check, S4: the app's tool list has both tools when there are bookmarks, and the index lists them."""
    import ast

    from orchestrator.brain.toolbox import Services, build
    from orchestrator.paths import SERVICES

    world, bookmarks, _ = setting_up(tmp_path)
    tools = build(Services(hub=world, bookmarks=bookmarks)).tools
    assert {"bookmark", "way_to_bookmark"} <= set(tools)
    app = ast.parse((SERVICES / "orchestrator" / "app.py").read_text())
    given = [kw.value for node in ast.walk(app) if isinstance(node, ast.Call) for kw in node.keywords if kw.arg == "bookmarks"]
    assert len(given) >= 2 and all(isinstance(v, ast.Name) and v.id == "bookmarks" for v in given)  # the store, never None


def test_a_changed_eyepiece_scale_or_a_moving_view_gives_no_direction(tmp_path):
    """The M3.3 re-check's notes: an unmeasured disc is kept as 0 (never the fitted radius in its place); a halved
    fitted circle then still says the scale changed; a view now whose motion is not registered says so."""
    from orchestrator.bookmarks import place_of

    world, bookmarks, registry = setting_up(tmp_path)
    call(world, registry, "bookmark", {})
    (mark,) = bookmarks.all()
    assert mark["disc"] == 0.0 and mark["radius"] == 300.0
    now = frame(world, 10.0, 0.0)
    now.fov = FieldOfView(640.0, 360.0, 150.0, 1.0)
    assert place_of(mark, now)[1] == "the eyepiece's scale changed since (the lens refocused or zoomed)"
    now = frame(world, 10.0, 0.0)
    now.tracked = False
    assert place_of(mark, now)[1].startswith("the slide is moving and its motion is not registered")


def test_a_dataset_made_afterwards_has_the_bookmarks_pictures(tmp_path):
    """The M3.3 re-check: gather follows a bookmark to its picture (ADR 0008)."""
    from orchestrator.datasets.importer import gather
    from orchestrator.paths import Paths

    world = World()
    world.latest_info.fov = FieldOfView(640.0, 360.0, 300.0, 1.0)
    paths = Paths.under(tmp_path)
    bookmarks = Bookmarks(paths.bookmarks, today=lambda: time.strftime("%Y-%m-%d"))
    registry = Registry()
    add_where_tools(registry, SimpleNamespace(hub=world, slidemap=None, bookmarks=bookmarks, day=None, log=None))
    call(world, registry, "bookmark", {"note": "the joint"})
    got = gather(paths, time.time() - 60, time.time() + 60, tmp_path / "dataset")
    day = time.strftime("%Y-%m-%d")
    assert got.pictures == 1 and (tmp_path / "dataset" / "pictures" / "bookmarks" / day / "b1.jpg").exists()
    assert (tmp_path / "dataset" / "pictures" / "bookmarks" / day / "b1.json").exists()  # its record goes with it


def test_a_bookmark_kept_without_its_picture_is_never_pointed_to_it_and_its_picture_is_made_inside_the_hold(tmp_path, monkeypatch):
    """M3.3's third re-check: no "its picture shows what to look for" when there is none, and the index says why; the
    picture is resized and encoded while the decode is still held (invariant 3)."""
    import contextlib

    import orchestrator.capture as capture_mod
    from orchestrator import bookmarks as bookmarks_mod

    world, bookmarks, registry = setting_up(tmp_path)
    held = {"now": False, "encoded_inside": None}
    real = capture_mod.decoding

    @contextlib.contextmanager
    def watched(*a, **k):
        with real(*a, **k) as h:
            held["now"] = True
            try:
                yield h
            finally:
                held["now"] = False

    real_encode = bookmarks_mod.cv2.imencode
    monkeypatch.setattr(capture_mod, "decoding", watched)
    monkeypatch.setattr(bookmarks_mod.cv2, "imencode", lambda *a, **k: held.update(encoded_inside=held["now"]) or real_encode(*a, **k))
    call(world, registry, "bookmark", {"note": "with"})
    assert held["encoded_inside"] is True

    @contextlib.contextmanager
    def refused(*a, **k):
        raise capture_mod.Refused("held views are full: 900 MB; clear earlier analyses or wait")
        yield

    monkeypatch.setattr(capture_mod, "decoding", refused)
    call(world, registry, "bookmark", {"note": "the joint"})
    frame(world, 0.0, 0.0, epoch=3)  # another epoch: the way can't be told
    said = call(world, registry, "way_to_bookmark", {"which": "the joint"})
    assert "its picture" not in said and "it was kept without a picture (no picture: held views are full" in said
    from orchestrator.resources import Index

    index = Index(world, bookmarks=bookmarks, day=SimpleNamespace(day="2026-10-07", sid="2026-10-07", date="2026-10-07"))
    index.today = lambda: "2026-10-07"
    entry = next(e for e in index.list("bookmark") if "the joint" in e.summary)
    assert "kept without a picture: no picture: held views are full" in entry.summary
