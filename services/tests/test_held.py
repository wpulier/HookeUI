"""Footage a researcher asked for, held on the user's yes (orchestrator/held.py; the partner §2.2): real
frames of the live view, a frame every half second for the seconds agreed, in memory under the
"unsaved" allowance (never written to disk: Record stays the user's), an entry of the session's index
the researcher reads with its session tool; only with the user's answer to its question."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.brain.registry import ToolContext, ToolFailed
from orchestrator.capture import Memory
from orchestrator.held import Held
from orchestrator.research import ResearchManager, Session
from orchestrator.resources import Index
from tests.fakes import Hub, announcer, research_tools
from tests.test_research import FakeHub


def frame(i, at, jpeg=b"\xff\xd8jpeg"):
    return SimpleNamespace(id=i, wall_at=at, dx=float(i), dy=0.0, epoch=0, steady=True), jpeg


def feed(held, hub, start, seconds, fps=10, size=100):
    frames = {}
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=frames.get(fid)))
    for k in range(int(seconds * fps) + 1):
        info, _ = frame(k + 1, start + k / fps)
        frames[info.id] = b"j" * size
        held.frame(info)


def test_it_holds_a_frame_every_half_second_for_the_seconds_agreed_and_then_is_held():
    hub = Hub()
    held = Held(hub, memory=Memory({"interactive": 1, "analysis": 1, "unsaved": 1}))
    done = []
    held.on_held.append(done.append)
    clip = held.start("r2", "Researcher 2", "q1", 3, "2026-10-07")
    clip.started_at = 1000.0
    feed(held, hub, 1000.0, 4)
    assert clip.status == "held" and done == [clip]
    assert len(clip.frames) == 7 and [round(f.at - 1000.0, 1) for f in clip.frames][:3] == [0.0, 0.5, 1.0]
    assert held.picture(clip.id, 2) == b"j" * 100 and held.picture(clip.id, 99) is None
    assert "7 frames over 3 s, a frame every 0.5 s, held for Researcher 2's question q1" == clip.words()


def test_a_hold_past_the_memory_allowance_stops_and_says_so():
    hub = Hub()
    held = Held(hub, memory=Memory({"interactive": 1, "analysis": 1, "unsaved": 0.0003}))  # about 300 bytes
    clip = held.start("r2", "Researcher 2", "q1", 10, "2026-10-07")
    clip.started_at = 1000.0
    feed(held, hub, 1000.0, 3)
    assert clip.status == "stopped" and "memory for unsaved evidence is full" in clip.why
    assert len(clip.frames) == 3 and "stopped early" in clip.words()


def test_the_session_index_lists_it_and_serves_its_frames():
    hub = Hub()
    held = Held(hub, memory=Memory({"interactive": 1, "analysis": 1, "unsaved": 1}))
    hub.held = held
    clip = held.start("r2", "Researcher 2", "q1", 2, "2026-10-07")
    clip.started_at = 1000.0
    feed(held, hub, 1000.0, 3)
    index = Index(hub, day=SimpleNamespace(day="2026-10-07", sid="2026-10-07", date="2026-10-07"))
    entry = index.get(clip.id)
    assert entry.kind == "clip" and entry.title.startswith("Held footage · 2 s") and entry.state == "held"
    assert len(entry.pictures) == len(clip.frames) and entry.values["frames"][1]["at"] == 1000.5
    assert index.picture(clip.id, 1) == (b"j" * 100, "image/jpeg")
    assert index.picture(clip.id, 1, day="2026-10-06") is None  # a day's own


def researcher_with_a_footage_question(tmp_path, seconds=15):
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-07")
    session = Session(rid="r2", day="2026-10-07", n=2, brief="b", deliverable="", effort="quick", engine="fake",
                      purpose="research", label="", object_id=None, origin={"channel": "chat"}, status="running")
    manager.sessions[session.rid] = session
    manager._event({"rid": "r2", "event": "request", "id": "q1", "what": "15 more seconds of this field", "why": "x",
                    "seconds": seconds})
    manager._event({"rid": "r2", "event": "request", "id": "q2", "what": "the objective in use", "why": "size"})
    hub = Hub()
    hub.held = Held(hub, memory=Memory({"interactive": 1, "analysis": 1, "unsaved": 1}))
    return manager, session, hub, research_tools(hub, manager).tools["researcher_note"].handler


def test_footage_is_held_only_on_the_users_own_yes_to_a_question_for_footage(tmp_path):
    """Review of #163, B2: never only the model's word for a yes."""
    import re

    manager, session, hub, note = researcher_with_a_footage_question(tmp_path)
    run = lambda said, args: asyncio.run(note(ToolContext("chat", 1, None, said), {"researcher": "2", **args}))  # noqa: E731
    with pytest.raises(ToolFailed, match="only when the user agrees"):
        run("hold 15 s", {"text": "ok", "hold_seconds": 15})
    with pytest.raises(ToolFailed, match="on the user's own yes"):  # "no, not now": no yes to quote
        run("no, not now", {"text": "no", "answers": "q1", "hold_seconds": 15, "asked_with": "yes"})
    with pytest.raises(ToolFailed, match="did not ask for footage"):
        run("yes, 40x", {"text": "40x", "answers": "q2", "hold_seconds": 15, "asked_with": "yes"})
    said = run("yes go ahead", {"text": "yes", "answers": "q1", "hold_seconds": 60, "asked_with": "yes"})
    clip = next(iter(hub.held.clips.values()))
    assert re.fullmatch(r"k\d+", clip.id) and clip.seconds == 15  # capped at the seconds it asked for
    assert said == f"passed the answer to Researcher 2's question q1; holding 15 s of the live view for it as {clip.id} (it asked for 15 s)"
    assert session.requests[0]["status"] == "answered"
    assert any(f"holding 15 s of the live view for you as {clip.id}" in n["text"] for n in session.notes)
    with pytest.raises(ToolFailed, match="stopped only when the user asks"):  # "by you" only on the user's own words
        run("hmm", {"text": "stop", "hold_seconds": 0, "asked_with": "stop holding"})
    cancelled = run("stop holding that", {"text": "stop", "hold_seconds": 0, "asked_with": "stop holding"})
    assert cancelled.startswith("stopped holding footage for Researcher 2") and clip.status == "stopped" and clip.why == "cancelled by you"


def test_an_answer_with_no_footage_is_passed_on_and_stops_nothing(tmp_path):
    """Re-check of #163, NEW 2: hold_seconds 0 with an answer ("no thanks", or the other question) is a plain answer."""
    manager, session, hub, note = researcher_with_a_footage_question(tmp_path)
    run = lambda said, args: asyncio.run(note(ToolContext("chat", 1, None, said), {"researcher": "2", **args}))  # noqa: E731
    said = run("no thanks", {"text": "no thanks", "answers": "q1", "hold_seconds": 0})
    assert said.startswith("passed the answer to Researcher 2's question q1") and not hub.held.clips
    assert session.requests[0]["status"] == "answered"
    manager._event({"rid": "r2", "event": "request", "id": "q3", "what": "10 s here", "why": "x", "seconds": 10})
    run("yes", {"text": "yes", "answers": "q3", "hold_seconds": 10, "asked_with": "yes"})
    clip = next(iter(hub.held.clips.values()))
    run("40x", {"text": "40x", "answers": "q2", "hold_seconds": 0})  # the other question, while q3's footage is held
    assert clip.status == "holding" and session.requests[1]["status"] == "answered"


def test_a_question_for_more_than_the_app_holds_says_why_it_holds_less(tmp_path):
    manager, session, hub, note = researcher_with_a_footage_question(tmp_path, seconds=90)
    said = asyncio.run(note(ToolContext("chat", 1, None, "yes"), {"researcher": "2", "text": "yes", "answers": "q1",
                                                                     "hold_seconds": 90, "asked_with": "yes"}))
    assert said.endswith("(it asked for 90 s; the app holds at most 60 s)")


def test_a_hold_with_no_frames_coming_ends_and_an_early_stop_is_told_to_the_user(tmp_path):
    """Synthetic camera/note seams: a real hold stops and files app activity, not an assistant answer."""


    hub = Hub()
    held = Held(hub, memory=Memory({"interactive": 1, "analysis": 1, "unsaved": 1}))
    clip = held.start("r2", "Researcher 2", "q1", 3, "2026-10-07")
    clip.started_at = 1000.0
    feed(held, hub, 1000.0, 1)  # one second of frames, then nothing
    held.tick(1000.0 + 5)
    assert clip.status == "stopped" and clip.why == "no frames came from the camera"
    noted = []
    a = announcer(tmp_path, SimpleNamespace())
    from orchestrator.held import tell

    tell(clip, SimpleNamespace(note=lambda rid, text, **kw: noted.append(text)), a.announcer)
    assert noted and noted[0].startswith(f"{clip.id} is held:")
    row = a.lines[-1]
    assert len(a.lines) == 1 and row["type"] == "app-notice" and row["producer"] == {"kind": "held", "id": clip.id}
    assert row["summary"].startswith("Holding footage for Researcher 2 stopped early (no frames came from the camera)")
    assert row["event"] == "stopped" and row["tone"] == "error" and "role" not in row and "text" not in row
    assert row["facts"]["frames"] == 3 and row["facts"]["span"] == 1 and row["facts"]["seconds"] == 3
    assert row["facts"]["status"] == "stopped" and row["facts"]["why"] == clip.why
    assert row["facts"]["released"] is False and row["facts"]["cutShort"] is False
    assert row["origin"] == {"day": None, "researchId": "r2"} and row["thread"] == a.day.active_id
    assert not a.spoken and not a.quiet
    assert any(r.get("kind") == "held-stopped" for r in a.logged)


def test_a_hold_its_researchers_run_ended_first_is_told_where_the_yes_was_said(tmp_path):
    """Synthetic run callback: real release activity retains delegation, without inferring a voice owner."""
    from orchestrator.held import wire

    for channel in ("chat", "voice"):
        hub = Hub()
        held = Held(hub, memory=Memory({"interactive": 1, "analysis": 1, "unsaved": 1}))
        voice = SimpleNamespace(closed=False)
        a = announcer(tmp_path / channel, SimpleNamespace(), voice=voice)
        research = SimpleNamespace(on_done=[], note=lambda rid, text, **kw: None)
        wire(held, research, a.announcer)

        async def go():  # on a loop, as live: the telling comes off the frame path (call_soon)
            clip = held.start("r2", "Researcher 2", "q1", 15, "2026-10-07", said={"channel": channel, "delegation_id": "d7"})
            clip.started_at = 1000.0
            feed(held, hub, 1000.0, 3)
            for hook in research.on_done:
                hook(SimpleNamespace(rid="r2", name="Researcher 2"))
            await asyncio.sleep(0.01)
            return clip

        clip = asyncio.run(go())
        assert clip.status == "released" and clip.cut_short and clip.frames == []
        row = a.lines[-1]
        assert len(a.lines) == 1 and row["type"] == "app-notice" and row["producer"] == {"kind": "held", "id": clip.id}
        text = row["summary"]
        assert text == ("Holding footage for Researcher 2 stopped early: its run ended, after 7 frames over 3 s of the "
                        "15 s agreed; they were let go with it.")
        assert row["event"] == "released" and "role" not in row and "text" not in row
        assert row["facts"]["frames"] == 7 and row["facts"]["span"] == 3 and row["facts"]["seconds"] == 15
        assert row["facts"]["status"] == "released" and row["facts"]["why"] == "its run ended"
        assert row["facts"]["released"] is True and row["facts"]["cutShort"] is True
        assert clip.kept == (7, 3) and clip.reservation.released and held.memory.reserved["unsaved"] == 0
        assert row["origin"] == {"day": None, "researchId": "r2", "delegationId": "d7"}
        assert row["thread"] == a.day.active_id
        assert not a.spoken and not a.quiet  # neither synthetic clip retained an original owner


def test_a_held_clip_released_at_the_runs_end_is_not_told_again(tmp_path):
    """Held in full and then released with its run: nothing more to tell the user (they were told nothing failed)."""
    from orchestrator.held import wire

    hub = Hub()
    held = Held(hub, memory=Memory({"interactive": 1, "analysis": 1, "unsaved": 1}))
    a = announcer(tmp_path, SimpleNamespace())
    research = SimpleNamespace(on_done=[], note=lambda rid, text, **kw: None)
    wire(held, research, a.announcer)
    clip = held.start("r2", "Researcher 2", "q1", 2, "2026-10-07")
    clip.started_at = 1000.0
    feed(held, hub, 1000.0, 3)
    assert clip.status == "held"
    shown = len(a.lines)
    for hook in research.on_done:
        hook(SimpleNamespace(rid="r2", name="Researcher 2"))
    assert clip.status == "released" and len(a.lines) == shown


def test_a_hold_watches_itself_while_holding_and_a_broken_telling_is_logged(monkeypatch):
    """Re-check of #163, NEW 4: the 1 s check starts with the first hold (no App wiring to forget), and an error
    in telling is logged as held-error, never lost."""
    import orchestrator.held as held_module
    from orchestrator.held import wire

    monkeypatch.setattr(held_module, "WATCH_EVERY_S", 0.01)
    logged = []
    hub = Hub()
    held = Held(hub, memory=Memory({"interactive": 1, "analysis": 1, "unsaved": 1}))
    broken = SimpleNamespace(held_stopped=lambda clip, text: 1 / 0)
    wire(held, SimpleNamespace(on_done=[], note=lambda rid, text, **kw: None), broken, logged.append)

    async def go():
        clip = held.start("r2", "Researcher 2", "q1", 1, "2026-10-07")
        clip.started_at -= 5  # its time is up and no frame came
        await asyncio.sleep(0.1)
        return clip

    clip = asyncio.run(go())
    assert clip.status == "stopped" and clip.why == "no frames came from the camera"
    assert [r["kind"] for r in logged] == ["held-error"] and "ZeroDivisionError" in logged[0]["error"]


def test_ids_differ_across_a_restart(tmp_path):
    hub = Hub()
    first = Held(hub, clock=lambda: 1_760_000_000.0).start("r2", "Researcher 2", "q1", 5, "2026-10-07")
    again = Held(hub, clock=lambda: 1_760_000_030.0).start("r2", "Researcher 2", "q1", 5, "2026-10-07")
    assert first.id != again.id and "k1" not in (first.id, again.id)
    # With the session store (the app's): numbered through the session and the date folder (day/ids.py), each
    # journaled in the session it was held in, so a restart's never repeats one; held in memory only, it is not counted
    # among what the session made (it is gone at a restart).
    from orchestrator.day.store import DayStore
    from tests.test_resources import OCT_7

    day = DayStore(root=tmp_path / "sessions", day="2026-10-07", clock=lambda: OCT_7)
    held = Held(hub)
    held.store = day
    one = held.start("r2", "Researcher 2", "q1", 5, "2026-10-07")
    restarted = Held(hub)
    restarted.store = DayStore(root=tmp_path / "sessions", day="2026-10-07", clock=lambda: OCT_7)
    two = restarted.start("r2", "Researcher 2", "q1", 5, "2026-10-07")
    assert (one.id, two.id) == ("k1", "k2") and (one.session, two.session) == ("2026-10-07", "2026-10-07")
    info = restarted.store.catalogue.get("2026-10-07")
    assert info.made == {} and info.ids[("2026-10-07", "k")] == 2


def test_the_app_wires_the_telling_and_the_release(tmp_path):
    """Real App wiring, synthetic run-end input: release produces activity without assistant fallback."""
    from orchestrator.app import App
    from orchestrator.held import held_of
    from orchestrator.paths import Paths

    app = App.build("http://127.0.0.1:1", Paths.under(tmp_path), use_gpu=False)
    held = held_of(app.hub)
    held.memory = Memory({"interactive": 1, "analysis": 1, "unsaved": 1})
    shown = []
    app.dialogue.publish = shown.append
    clip = held.start("r2", "Researcher 2", "q1", 15, "2026-10-07")
    released = [hook for hook in app.research.on_done if getattr(hook, "__name__", "") == "release_with_run"]
    assert len(released) == 1
    released[0](SimpleNamespace(rid="r2", name="Researcher 2"))
    assert clip.status == "released" and any("stopped early: its run ended" in line.get("summary", "") for line in shown)
    rows = [row for row in shown if row.get("producer") == {"kind": "held", "id": clip.id}]
    assert len(rows) == 1 and rows[0]["type"] == "app-notice" and rows[0]["event"] == "released"
    assert rows[0]["facts"]["frames"] == 0 and rows[0]["facts"]["span"] == 0
    assert rows[0]["facts"]["released"] is True and rows[0]["facts"]["cutShort"] is True
    assert rows[0]["facts"]["why"] == "its run ended" and rows[0]["facts"]["seconds"] == 15
    assert rows[0]["origin"] == {"day": None, "researchId": "r2"}
    assert rows[0]["thread"] == app.day.active_id and rows[0] in app.dialogue.history()["lines"]
    assert not clip.frames and clip.reservation.released and held.memory.reserved["unsaved"] == 0
    assert not any(row.get("role") == "assistant" for row in shown)


def test_its_memory_goes_when_its_researchers_run_ends(tmp_path):
    """Review of #163, SF4: released, never kept until midnight; what it held stays said."""
    hub = Hub()
    memory = Memory({"interactive": 1, "analysis": 1, "unsaved": 1})
    held = Held(hub, memory=memory)
    clip = held.start("r2", "Researcher 2", "q1", 2, "2026-10-07")
    clip.started_at = 1000.0
    feed(held, hub, 1000.0, 3)
    assert memory.reserved["unsaved"] == 500
    released = held.release("r2", "Researcher 2's run ended")
    assert released == [clip] and clip.status == "released" and clip.frames == []
    assert "5 frames over 2 s" in clip.words() and clip.words().endswith("released: Researcher 2's run ended")
    assert memory.reserved["unsaved"] == 0  # its reservation was given back


def test_each_frame_keeps_the_bridges_own_number():
    hub = Hub()
    held = Held(hub, memory=Memory({"interactive": 1, "analysis": 1, "unsaved": 1}))
    clip = held.start("r2", "Researcher 2", "q1", 1, "2026-10-07")
    clip.started_at = 1000.0
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=b"j" * 10))
    held.frame(SimpleNamespace(id=5, wall_at=1000.0, dx=0.0, dy=0.0, epoch=0, steady=True, seq=9120, boot="b1"))
    assert clip.frames[0].facts(0)["seq"] == 9120 and clip.frames[0].facts(0)["boot"] == "b1"


# --- the third re-check of #163 ---------------------------------------------------------------------------
def a_spoken_yes(tmp_path, voice_open=True):
    """A researcher's footage question answered yes by voice through the real tool, with the live wiring."""
    from orchestrator.held import wire

    manager, session, hub, note = researcher_with_a_footage_question(tmp_path)
    a = announcer(tmp_path, SimpleNamespace(), voice=SimpleNamespace(closed=not voice_open))
    wire(hub.held, manager, a.announcer)
    return manager, session, hub, note, a


def test_a_spoken_yes_is_told_on_its_delegation_through_the_real_tool(tmp_path):
    """Synthetic tool context retains delegation as activity; unknown original owner never borrows current voice."""
    manager, session, hub, note, a = a_spoken_yes(tmp_path)

    async def go():
        await note(ToolContext("voice", 1, None, "yes go ahead", "d7"),
                   {"researcher": "2", "text": "yes", "answers": "q1", "hold_seconds": 15, "asked_with": "yes go ahead"})
        clip = next(iter(hub.held.clips.values()))
        hub.held._finish(clip, "stopped", "the memory for unsaved evidence is full")
        await asyncio.sleep(0.01)
        return clip

    clip = asyncio.run(go())
    row = a.lines[-1]
    assert len(a.lines) == 1 and row["type"] == "app-notice" and row["producer"] == {"kind": "held", "id": clip.id}
    assert row["event"] == "stopped" and "stopped early (the memory" in row["summary"]
    assert row["origin"]["delegationId"] == "d7" and row["origin"]["day"] is None
    assert row["origin"]["turnId"] == 1 and row["origin"]["researchId"] == session.rid
    assert "asked" not in row["origin"] and row["thread"] == a.day.active_id
    assert row["facts"]["frames"] == 0 and row["facts"]["span"] == 0 and row["facts"]["seconds"] == 15
    assert row["facts"]["why"] == "the memory for unsaved evidence is full" and row["facts"]["released"] is False
    assert session.requests[0]["status"] == "answered" and any(n.get("held") == clip.id for n in session.notes)
    assert clip.said["voice_owner"] is None and not a.spoken and not a.quiet
    assert "role" not in row and "text" not in row


def test_the_users_own_stop_is_not_told_back_to_them(tmp_path):
    """Re-check of #163 (3rd), NEW 1: "stop holding that" is answered by the tool; no second line, nothing spoken."""
    manager, session, hub, note, a = a_spoken_yes(tmp_path)

    async def go():
        await note(ToolContext("voice", 1, None, "yes go ahead", "d7"),
                   {"researcher": "2", "text": "yes", "answers": "q1", "hold_seconds": 15, "asked_with": "yes go ahead"})
        shown = len(a.lines)
        said = await note(ToolContext("voice", 2, None, "stop holding that", "d8"),
                          {"researcher": "2", "text": "stop", "hold_seconds": 0, "asked_with": "stop holding"})
        await asyncio.sleep(0.01)
        return said, shown

    said, shown = asyncio.run(go())
    assert said.startswith("stopped holding footage for Researcher 2")
    assert not a.spoken and len(a.lines) == shown
    assert any("is held:" in n["text"] and "cancelled by you" in n["text"] for n in session.notes)  # the researcher is told


def test_a_stop_after_the_user_switched_conversations_is_shown_where_they_are(tmp_path):
    """Synthetic camera/callbacks: activity stays in the original Asked conversation without duplicate prose."""
    from orchestrator.held import wire

    hub = Hub()
    held = Held(hub, memory=Memory({"interactive": 1, "analysis": 1, "unsaved": 1}))
    a = announcer(tmp_path, SimpleNamespace())
    wire(held, SimpleNamespace(on_done=[], note=lambda rid, text, **kw: None), a.announcer)
    asked = a.day.here()  # the yes was said in the first conversation
    clip = held.start("r2", "Researcher 2", "q1", 15, "2026-10-07", said={"channel": "chat", "asked": asked})
    a.day.open_thread("Onion")  # then the user started another
    clip.started_at = 1000.0
    feed(held, hub, 1000.0, 1)
    held.tick(1000.0 + 20)
    threads = [line["thread"] for line in a.lines]
    assert threads == [asked.thread] and asked.thread != a.day.active_id
    row = a.lines[-1]
    assert row["type"] == "app-notice" and row["producer"] == {"kind": "held", "id": clip.id}
    assert row["event"] == "stopped" and "stopped early" in row["summary"]
    assert not row["summary"].startswith('From the earlier conversation "')
    assert row["facts"]["frames"] == 3 and row["facts"]["span"] == 1
    assert row["facts"]["why"] == "no frames came from the camera" and row["facts"]["released"] is False
    assert row["origin"]["day"] == asked.day
    assert row["origin"]["asked"] == {"thread": asked.thread, "generation": asked.generation}
    assert not a.day.active.lines and a.announcer.dialogue.history()["lines"] == []
    a.day.switch(asked.thread)
    assert a.announcer.dialogue.history()["lines"] == [row]
    assert "role" not in row and "text" not in row and not a.spoken and not a.quiet


def test_a_hold_running_when_another_session_is_opened_is_told_as_cut_short(tmp_path):
    """Synthetic clear on opening another session (ADR 0017: midnight does nothing): real Held drops frames and files
    truthful cut-short release activity."""
    from orchestrator.held import wire

    hub = Hub()
    held = Held(hub, memory=Memory({"interactive": 1, "analysis": 1, "unsaved": 1}))
    a = announcer(tmp_path, SimpleNamespace())
    wire(held, SimpleNamespace(on_done=[], note=lambda rid, text, **kw: None), a.announcer)
    clip = held.start("r2", "Researcher 2", "q1", 15, "2026-10-07")
    clip.started_at = 1000.0
    feed(held, hub, 1000.0, 0.5)
    held.clear()
    assert not held.clips and a.lines[-1]["summary"] == ("Holding footage for Researcher 2 stopped early: another session was opened, "
                                                     "after 2 frames over 0.5 s of the 15 s agreed; they were let go with it.")
    row = a.lines[-1]
    assert len(a.lines) == 1 and row["type"] == "app-notice" and row["producer"] == {"kind": "held", "id": clip.id}
    assert row["event"] == "released" and row["facts"]["cutShort"] is True and row["facts"]["released"] is True
    assert row["facts"]["frames"] == 2 and row["facts"]["span"] == 0.5 and row["facts"]["seconds"] == 15
    assert row["facts"]["why"] == "another session was opened" and row["origin"] == {"day": None, "researchId": "r2"}
    assert clip.kept == (2, 0.5) and not clip.frames and clip.reservation.released
    assert held.memory.reserved["unsaved"] == 0
    assert "role" not in row and "text" not in row and not a.spoken and not a.quiet


def test_a_stop_and_the_runs_end_in_one_step_still_tell_the_stop(tmp_path):
    """Synthetic run callback: queued real telling reflects release after a stop in the same loop step."""
    from orchestrator.held import wire

    hub = Hub()
    held = Held(hub, memory=Memory({"interactive": 1, "analysis": 1, "unsaved": 1}))
    a = announcer(tmp_path, SimpleNamespace())
    research = SimpleNamespace(on_done=[], note=lambda rid, text, **kw: None)
    wire(held, research, a.announcer)

    async def go():
        clip = held.start("r2", "Researcher 2", "q1", 15, "2026-10-07")
        clip.started_at = 1000.0
        feed(held, hub, 1000.0, 1)
        held._finish(clip, "stopped", "the memory for unsaved evidence is full")
        for hook in research.on_done:  # the same loop step
            hook(SimpleNamespace(rid="r2", name="Researcher 2"))
        await asyncio.sleep(0.01)
        return clip

    clip = asyncio.run(go())
    assert a.lines[-1]["summary"] == ("Holding footage for Researcher 2 stopped early (the memory for unsaved evidence is full): "
                                   "3 frames over 1 s; then its run ended, so they were let go.")
    row = a.lines[-1]
    assert len(a.lines) == 1 and row["type"] == "app-notice" and row["producer"] == {"kind": "held", "id": clip.id}
    assert row["event"] == "released" and row["facts"]["status"] == "released" and row["facts"]["released"] is True
    assert row["facts"]["frames"] == 3 and row["facts"]["span"] == 1 and row["facts"]["seconds"] == 15
    assert row["facts"]["why"] == "its run ended" and row["facts"]["stoppedWhy"] == "the memory for unsaved evidence is full"
    assert row["facts"]["cutShort"] is False and row["origin"] == {"day": None, "researchId": "r2"}
    assert clip.kept == (3, 1) and not clip.frames and clip.reservation.released
    assert held.memory.reserved["unsaved"] == 0
    assert "role" not in row and "text" not in row and not a.spoken and not a.quiet


def test_a_researcher_note_that_fails_is_logged_and_the_user_is_still_told(tmp_path):
    """Synthetic failing inbox boundary: actual telling still files stop activity and retains its error log."""
    from orchestrator.held import wire

    hub = Hub()
    held = Held(hub, memory=Memory({"interactive": 1, "analysis": 1, "unsaved": 1}))
    a = announcer(tmp_path, SimpleNamespace())
    logged = []

    def broken(rid, text, **marks):
        raise OSError("disk full")

    wire(held, SimpleNamespace(on_done=[], note=broken), a.announcer, logged.append)
    clip = held.start("r2", "Researcher 2", "q1", 15, "2026-10-07")
    clip.started_at = 1000.0
    feed(held, hub, 1000.0, 1)
    held._finish(clip, "stopped", "no frames came from the camera")
    assert [r["kind"] for r in logged] == ["held-error"] and "OSError: disk full" in logged[0]["error"]
    row = a.lines[-1]
    assert "stopped early (no frames came from the camera)" in row["summary"]
    assert len(a.lines) == 1 and row["type"] == "app-notice" and row["producer"] == {"kind": "held", "id": clip.id}
    assert row["event"] == "stopped" and row["facts"]["frames"] == 3 and row["facts"]["span"] == 1
    assert row["facts"]["why"] == clip.why == "no frames came from the camera" and row["facts"]["released"] is False
    assert row["origin"] == {"day": None, "researchId": "r2"} and row["thread"] == a.day.active_id
    assert "role" not in row and "text" not in row and not a.spoken and not a.quiet


def test_on_another_session_a_working_researcher_is_told_and_a_stop_a_moment_before_is_not_said_kept(tmp_path):
    """Synthetic clear on opening another session: original inbox note survives and real queued status reports
    released frames."""
    from orchestrator.held import wire

    hub = Hub()
    held = Held(hub, memory=Memory({"interactive": 1, "analysis": 1, "unsaved": 1}))
    a = announcer(tmp_path, SimpleNamespace())
    notes = []
    wire(held, SimpleNamespace(on_done=[], note=lambda rid, text, **kw: notes.append((rid, text))), a.announcer)

    async def go():
        holding = held.start("r2", "Researcher 2", "q1", 15, "2026-10-07")
        stopped = held.start("r3", "Researcher 3", "q1", 15, "2026-10-07")
        for clip in (holding, stopped):
            clip.started_at = 1000.0
        feed(held, hub, 1000.0, 1)
        held._finish(stopped, "stopped", "the memory for unsaved evidence is full")
        held.clear()  # another session opened, in the same step
        await asyncio.sleep(0.01)
        return holding, stopped

    holding, stopped = asyncio.run(go())
    assert any(rid == "r2" and "was let go before it was held in full (another session was opened)" in text for rid, text in notes)
    texts = [line["summary"] for line in a.lines]
    assert any(t.startswith("Holding footage for Researcher 3 stopped early (the memory for unsaved evidence is full): "
                            "3 frames over 1 s; then another session was opened, so they were let go.") for t in texts)
    assert not any("are kept for it" in t for t in texts)
    assert len(a.lines) == 2 and all(row["type"] == "app-notice" and row["event"] == "released" for row in a.lines)
    assert {row["producer"]["id"] for row in a.lines} == {holding.id, stopped.id}
    for clip in (holding, stopped):
        row = next(row for row in a.lines if row["producer"] == {"kind": "held", "id": clip.id})
        assert row["facts"]["frames"] == 3 and row["facts"]["span"] == 1 and row["facts"]["seconds"] == 15
        assert row["facts"]["released"] is True and row["facts"]["why"] == "another session was opened"
        assert row["facts"]["cutShort"] is (clip is holding)
        assert row["origin"] == {"day": None, "researchId": clip.rid}
        assert "role" not in row and "text" not in row
        assert clip.kept == (3, 1) and not clip.frames and clip.reservation.released
    stopped_row = next(row for row in a.lines if row["producer"]["id"] == stopped.id)
    assert stopped_row["facts"]["stoppedWhy"] == "the memory for unsaved evidence is full"
    assert not held.clips and held.memory.reserved["unsaved"] == 0 and not a.spoken and not a.quiet
