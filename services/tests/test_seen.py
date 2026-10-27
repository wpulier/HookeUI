"""Seen one like this before today? (orchestrator/seen.py, option SEEN): every outline named today is
kept with its embedding, name, time and conversation, per day, and read back after a restart; it is
embedded in the background, never on the click's path; naming a click is told the most alike earlier
one as one line of data in the past tense; seen_before lists them. Embeddings are stand-ins here
(bench/seen_bench.py measures the real DINOv2)."""
import asyncio
import json
import time
from types import SimpleNamespace

import cv2
import numpy as np

from orchestrator import seen as seen_module
from orchestrator.agent import Agent
from orchestrator.brain.registry import ToolContext
from orchestrator.brain.toolbox import Services, build
from orchestrator.scene import SceneObject
from orchestrator.seen import Kept, SeenToday, Similar, context_line, most_alike
from orchestrator.taught import Example, TaughtLabels
from tests.fakes import Hub

DAY = "2026-10-06"
SQUARE = [[[600, 300], [660, 300], [660, 360], [600, 360]]]
NINE = time.mktime((2026, 10, 6, 9, 12, 0, 0, 0, -1))  # 09:12 local


def unit(*values):
    v = np.asarray(values, np.float32)
    return list(v / np.linalg.norm(v))


class Embed:
    """A stand-in embedding: a crop's mean brightness picks one of three directions. Records each
    call's batch size, and can be held (to show nothing waits for it)."""

    def __init__(self):
        self.batches = []
        self.release = None

    async def __call__(self, jpegs, model="dinov2-small"):
        self.batches.append(len(jpegs))
        if self.release is not None:
            await self.release.wait()
        out = []
        for j in jpegs:
            v = float(cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR).mean())
            out.append(unit(1, 0.1, 0) if v < 85 else unit(0, 1, 0.1) if v < 170 else unit(0.1, 0, 1))
        return out


def hub_with_frames(brightness=None):
    """Frames 7, 8, 9: plain dark, middle and light pictures (one kind of thing each)."""
    hub = Hub()
    brightness = brightness or {7: 30, 8: 120, 9: 220}
    jpegs = {fid: cv2.imencode(".jpg", np.full((720, 1280, 3), b, np.uint8))[1].tobytes() for fid, b in brightness.items()}
    hub.infos = {fid: SimpleNamespace(id=fid, dx=0.0, dy=0.0, epoch=0, fov=None, quality=()) for fid in jpegs}
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=jpegs[fid]) if fid in jpegs else None,
                                 is_fresh=lambda: True)
    return hub


class Day:
    def __init__(self):
        self.active_id = "t1"
        self.titles = {"t1": "Blood smear", "t2": "Pond water"}


def make(tmp_path, hub=None, embed=None, now=None, day=None, today=DAY, session=None, catalogue=None):
    hub = hub or hub_with_frames()
    embed = embed or Embed()
    taught = TaughtLabels(tmp_path / "taught", today=lambda: today, embed=embed, hub=hub)
    taught.watch(hub.scene)
    day = day or Day()
    clock = now or [NINE]
    logged = []
    seen = SeenToday(tmp_path / "seen", tmp_path / "logs", today=lambda: today, thread=lambda: day.active_id,
                     title=lambda tid: day.titles.get(tid, ""), taught=taught, hub=hub, log=logged.append,
                     clock=lambda: clock[0], session=session, catalogue=catalogue)
    seen.watch(hub.scene)
    return SimpleNamespace(hub=hub, embed=embed, taught=taught, seen=seen, day=day, clock=clock, logged=logged)


def outline(hub, oid, frame=7, kind="highlight", label="", status="outline", **extra):
    return hub.scene.add(SceneObject(id=oid, kind=kind, polygons=SQUARE, anchor_frame=frame, anchor_epoch=0,
                                     anchor_offset=(0.0, 0.0), source=extra.pop("source", "click"), label=label,
                                     status=status, **extra))


async def settle(n=10):
    for _ in range(n):
        await asyncio.sleep(0.02)


# --- keeping and reading back -------------------------------------------------------------------
def test_named_outlines_are_kept_per_day_and_read_back_after_a_restart(tmp_path):
    s = make(tmp_path)

    async def day_of_clicks():
        outline(s.hub, "o1", 7)
        s.hub.scene.update("o1", label="neutrophil", status="likely")  # naming a click
        outline(s.hub, "o2", 8, label="lymphocyte", status="likely", source="ai:look")  # the look, named as it appears
        s.day.active_id = "t2"
        outline(s.hub, "o3", 9)
        s.hub.scene.update("o3", label="diatom", status="confirmed")  # the user named it
        await settle()

    asyncio.run(day_of_clicks())
    kept = {k.object_id: k for k in s.seen.kept}
    assert {o: (k.label, k.thread, k.status) for o, k in kept.items()} == {
        "o1": ("neutrophil", "t1", "likely"), "o2": ("lymphocyte", "t1", "likely"), "o3": ("diatom", "t2", "confirmed")}
    assert all(len(k.vector) == 3 and k.at == NINE for k in kept.values())
    crop = tmp_path / "logs" / kept["o1"].crop
    assert kept["o1"].crop.startswith(f"crops/{DAY}/") and crop.exists()  # beside the logs' other crops

    again = make(tmp_path)  # a restart: object ids start again, the day's outlines do not
    assert sorted(k.label for k in again.seen.kept) == ["diatom", "lymphocyte", "neutrophil"]

    async def one_more():
        outline(again.hub, "o1", 7, label="platelet", status="likely")
        await settle()

    asyncio.run(one_more())
    assert sorted(k.id for k in again.seen.kept) == ["s1", "s2", "s3", "s4"]  # never reused, "o1" is a new one

    # Seen-before is the user's (ADR 0017): the next day reads every earlier date's back, each with its date folder.
    tomorrow = make(tmp_path, today="2026-10-07", now=[NINE + 86400])
    assert sorted(k.label for k in tomorrow.seen.kept) == ["diatom", "lymphocyte", "neutrophil", "platelet"]
    assert {k.day for k in tomorrow.seen.kept} == {DAY} and len(tomorrow.seen.earlier()) == 4

    async def tomorrows():
        outline(tomorrow.hub, "o1", 7, label="neutrophil", status="likely")
        await settle()

    asyncio.run(tomorrows())
    assert [(k.day, k.id) for k in tomorrow.seen.kept if k.day != DAY] == [("2026-10-07", "s1")]  # a date folder's ids
    assert (tmp_path / "seen" / "2026-10-07" / "seen.jsonl").exists() and (tmp_path / "seen" / DAY / "seen.jsonl").exists()


def test_a_named_outline_keeps_which_model_drew_it_and_the_index_says_so(tmp_path):
    """Slice 2 (2026-10-11): which model drew a named outline is kept with it (seen.Kept.made_by), so the index's
    named-outline entry says it after the outline itself is gone and after a restart (madeBy.implementation)."""
    from orchestrator.resources import Index

    s = make(tmp_path)

    async def name_one():
        outline(s.hub, "o1", 7, made_by={"operation": "outline", "implementation": "sam2.1-tiny", "policy": "raw",
                                         "origin": "user-chosen", "execution": "x1"})
        s.hub.scene.update("o1", label="neutrophil", status="likely")
        await settle()

    asyncio.run(name_one())
    assert s.seen.kept[0].made_by == {"operation": "outline", "implementation": "sam2.1-tiny"}
    again = make(tmp_path)  # read back after a restart
    entry = next(e for e in Index(again.hub, seen=again.seen)._named(DAY))
    assert entry.made_by == {"by": "app", "operation": "outline", "implementation": "sam2.1-tiny"}


def test_a_renamed_outline_keeps_one_record_with_its_new_name(tmp_path):
    s = make(tmp_path)

    async def rename():
        outline(s.hub, "o1", 7, label="cell", status="likely", source="ai:look")
        await settle()
        s.hub.scene.update("o1", label="neutrophil", status="confirmed")  # name_outline
        await settle()

    asyncio.run(rename())
    assert [(k.label, k.status) for k in s.seen.kept] == [("neutrophil", "confirmed")]
    assert [(k.label, k.status) for k in make(tmp_path).seen.kept] == [("neutrophil", "confirmed")]  # newest line wins
    assert s.embed.batches == [1]  # the rename used the vector it had


def test_a_line_cut_off_by_a_crash_does_not_lose_the_rest(tmp_path):
    s = make(tmp_path)

    async def keep():
        outline(s.hub, "o1", 7, label="neutrophil", status="likely", source="ai:look")
        await settle()

    asyncio.run(keep())
    with (tmp_path / "seen" / DAY / "seen.jsonl").open("a") as f:
        f.write('{"id": "s2", "label": "lymph')
    assert [k.label for k in make(tmp_path).seen.kept] == ["neutrophil"]


def test_another_session_opened_reads_again_and_moves_nothing_aside(tmp_path):
    """ADR 0017: what was seen is the user's, so opening another session (reset_run) reads every session's again; no
    file moves aside (the old "start today afresh" did)."""
    s = make(tmp_path)

    async def keep():
        outline(s.hub, "o1", 7, label="neutrophil", status="likely", source="ai:look")
        await settle()

    asyncio.run(keep())
    s.seen.reset_run()
    assert [k.label for k in s.seen.kept] == ["neutrophil"] and [k.label for k in make(tmp_path).seen.kept] == ["neutrophil"]
    assert not list((tmp_path / "seen" / DAY).glob("seen-cleared-*.jsonl"))


def test_seen_before_looks_across_sessions_and_says_which_one(tmp_path, monkeypatch):
    """ADR 0017: each line keeps the session it was named in; naming a click in another session is told the earlier
    one with that session's name and conversation; the per-name cap is per session and conversation."""
    from orchestrator.day.store import DayStore

    monkeypatch.setattr(seen_module, "PER_NAME", 2)
    store = DayStore(root=tmp_path / "sessions", day=DAY, clock=lambda: NINE)
    store.rename_session("Blood smear")
    store.rename("t1", "First look")
    s = make(tmp_path, session=lambda: store.sid, catalogue=store.catalogue)

    async def name(*oids):
        for oid in oids:
            outline(s.hub, oid, 7, label="neutrophil", status="likely", source="ai:find")
        await settle()

    asyncio.run(name("o1", "o2", "o3"))
    assert [(k.object_id, k.session, k.thread) for k in s.seen.kept] == [("o1", DAY, "t1"), ("o2", DAY, "t1")]  # capped
    store.new()  # another session, its own t1
    s.seen.reset_run()
    asyncio.run(name("o4"))
    assert [(k.object_id, k.session) for k in s.seen.kept][-1] == ("o4", f"{DAY}.2")  # a new session's own cap
    lines = [json.loads(x) for x in (tmp_path / "seen" / DAY / "seen.jsonl").read_text().splitlines()]
    assert [x["session"] for x in lines] == [DAY, DAY, f"{DAY}.2"]
    first = next(k for k in s.seen.kept if k.object_id == "o1")
    said = s.seen.line(Similar(first, 0.95))
    assert said.startswith('Earlier today (09:12, in the session "Blood smear", conversation "First look") an outline')
    mine = next(k for k in s.seen.kept if k.object_id == "o4")
    assert s.seen.line(Similar(mine, 0.95)).startswith('Earlier today (09:12, in the conversation "Blood smear")')
    assert "session" not in s.seen.line(Similar(mine, 0.95))


def test_parts_arena_arms_first_guesses_and_unnamed_outlines_are_not_kept(tmp_path):
    s = make(tmp_path)

    async def outlines():
        outline(s.hub, "p1", 7, kind="part", label="nucleus", status="likely", parent="o9")
        outline(s.hub, "o2", 7, label="cell", status="likely", trial="a1", arm="A", source="arena")
        outline(s.hub, "o3", 7, label="", status="looking", source="ai:first-guess")
        outline(s.hub, "o4", 7)  # outlined, never named
        await settle()

    asyncio.run(outlines())
    assert s.seen.kept == []


def test_kept_outlines_are_bounded_per_name_and_per_day(tmp_path, monkeypatch):
    s = make(tmp_path)
    monkeypatch.setattr(seen_module, "MAX_KEPT", 14)

    async def a_finder_outlines_many():
        for i in range(12):
            outline(s.hub, f"r{i}", 7, label="red blood cell", status="likely", source="ai:find")
        for i in range(6):
            outline(s.hub, f"w{i}", 8, label=f"white cell {i}", status="likely", source="ai:find")
        await settle(20)

    asyncio.run(a_finder_outlines_many())
    names = [k.label for k in s.seen.kept]
    assert names.count("red blood cell") == seen_module.PER_NAME == 10  # the first ten stand for the rest
    assert len(names) == 14 and any(r.get("skipped") for r in s.logged)  # the day's cap, said once in the log


# --- off the click's path ------------------------------------------------------------------------
def test_embedding_runs_in_the_background_and_outlines_together_go_in_one_call(tmp_path):
    embed = Embed()
    s = make(tmp_path, embed=embed)

    async def clicks():
        embed.release = asyncio.Event()  # the heavy worker is slow: nothing waits for it
        t0 = time.perf_counter()
        outline(s.hub, "o1", 7)
        outline(s.hub, "o2", 8, label="lymphocyte", status="likely", source="ai:find")
        added_ms = (time.perf_counter() - t0) * 1000
        assert embed.batches == []  # adding an outline asked nothing of the worker yet
        await settle(3)
        assert embed.batches == [2]  # both, in one call, after the outlines were drawn
        assert s.seen.kept == []  # still waiting for the worker
        embed.release.set()
        await settle()
        return added_ms

    assert asyncio.run(clicks()) < 50
    assert [k.object_id for k in s.seen.kept] == ["o2"]  # o1 is kept once it is named


def test_one_embedding_per_outline_for_taught_and_seen_alike(tmp_path):
    s = make(tmp_path)
    s.taught.examples = [Example(id=f"t{i}", key="cell", label="cell", vector=unit(1, 0.1, 0), crop="", at=float(i)) for i in range(3)]

    async def click_and_name():
        outline(s.hub, "o1", 7)  # TAUGHT matches it, SEEN embeds it
        await settle()
        s.hub.scene.update("o1", label="neutrophil", status="likely")
        await settle()

    asyncio.run(click_and_name())
    assert sum(s.embed.batches) == 1 and s.hub.scene.objects["o1"].taught["label"] == "cell"
    assert [k.object_id for k in s.seen.kept] == ["o1"]


def test_seen_off_embeds_nothing_and_keeps_nothing(tmp_path):
    s = make(tmp_path)
    s.hub.settings.update({"seen": "off"})

    async def click():
        outline(s.hub, "o1", 7, label="neutrophil", status="likely", source="ai:look")
        await settle()
        return await s.seen.match("o1")

    assert asyncio.run(click()) is None and s.embed.batches == [] and s.seen.kept == []


# --- what naming is told -------------------------------------------------------------------------
def earlier(label="neutrophil", status="likely", thread="t1", at=NINE, vector=None):
    return Kept(id="s1", object_id="o1", label=label, key=label, status=status, thread=thread,
                vector=vector or unit(1, 0.1, 0), crop="", at=at)


def test_the_line_is_one_line_of_data_in_the_past_tense():
    assert context_line(None) == ""
    line = context_line(Similar(earlier('neutro"phil"\n<b>'), 0.9512), 'Blood "smear"')
    assert "\n" not in line and "<" not in line
    assert line.startswith('Earlier today (09:12, in the conversation "Blood smear") an outline that looked like this one '
                           'was named "neutro phil b" (you named it; similarity 0.95).')
    assert "93% of the time" in line and "earlier, not this view" in line and "clue" in line
    assert "the user named it" in context_line(Similar(earlier(status="confirmed"), 0.95))
    assert "you named it, not sure" in context_line(Similar(earlier(status="unsure"), 0.95))


def test_only_alike_enough_and_earlier_outlines_count():
    kept = [earlier(), Kept(id="s2", object_id="o2", label="lymphocyte", key="lymphocyte", status="likely", thread="t1",
                            vector=unit(0.6, 1, 0), crop="", at=NINE + 30)]
    assert most_alike(unit(1, 0.1, 0), kept)[0].kept.label == "neutrophil"
    assert most_alike(unit(1, 0.1, 0), kept, exclude={"s1"}) == []  # the lymphocyte is not alike enough
    assert most_alike(unit(1, 0.1, 0), kept, before=NINE - 1) == []  # nothing named before then
    assert [s.kept.label for s in most_alike(unit(1, 0.1, 0), kept, floor=-1, k=3)] == ["neutrophil", "lymphocyte"]


def naming_agent(s, tmp_path, label="neutrophil"):
    agent = Agent(s.hub, interaction_log=tmp_path / "interactions.jsonl")
    agent.taught, agent.seen = s.taught, s.seen
    prompts, logged = [], []

    async def ask(images, prompt, max_output_tokens=0):
        prompts.append(prompt)
        return SimpleNamespace(data={"label": label, "answer": "A cell with a lobed nucleus.", "confidence": 0.8},
                               model="stand-in", latency_ms=5.0, error=None)

    agent.vision = lambda _o: SimpleNamespace(ask=ask)
    agent.log = logged.append
    agent.keep_image = lambda *a, **k: None
    return agent, prompts, logged


def test_naming_a_click_is_told_the_earlier_one_and_the_model_keeps_its_name(tmp_path):
    s = make(tmp_path)
    agent, prompts, logged = naming_agent(s, tmp_path, label="band neutrophil")

    async def morning_then_now():
        outline(s.hub, "o1", 7, label="neutrophil", status="likely", source="ai:look")
        await settle()
        s.clock[0] = NINE + 3600
        s.day.active_id = "t2"
        outline(s.hub, "o2", 7)  # a click on one like it, an hour later, in another conversation
        await agent.identify("o2")
        await settle()

    asyncio.run(morning_then_now())
    assert ('Earlier today (09:12, in the conversation "Blood smear") an outline that looked like this one was named '
            '"neutrophil"') in prompts[0]
    assert s.hub.scene.objects["o2"].label == "band neutrophil"  # the model's name; code never sets it
    record = next(r for r in logged if r["kind"] == "identify")
    assert record["seenBefore"]["label"] == "neutrophil" and record["seenBefore"]["thread"] == "t1"
    assert [k.object_id for k in s.seen.kept] == ["o1", "o2"]  # the click is kept too, after naming
    assert sum(s.embed.batches) == 2  # one embedding each


def test_nothing_earlier_or_nothing_alike_tells_naming_nothing(tmp_path):
    s = make(tmp_path)
    agent, prompts, _ = naming_agent(s, tmp_path)

    async def clicks():
        outline(s.hub, "o1", 7)
        await agent.identify("o1")  # the first of the day: nothing to compare, nothing waited for
        await settle()
        outline(s.hub, "o2", 7)
        await agent.identify("o2")  # alike, but named a moment ago: this view, not "earlier"
        await settle()
        s.clock[0] = NINE + 3600
        outline(s.hub, "o3", 9)
        await agent.identify("o3")  # an hour on, but not alike
        await settle()

    asyncio.run(clicks())
    assert not any("Earlier today" in p for p in prompts)


# --- seen_before -----------------------------------------------------------------------------------
def tool(s, tmp_path, ask=None):
    agent = SimpleNamespace(vision=lambda o: SimpleNamespace(ask=ask), snapshot=lambda frame_id=None: None)
    return build(Services(hub=s.hub, agent=agent, history_log=tmp_path / "logs" / "interactions.jsonl",
                          day=SimpleNamespace(day=DAY, sid=DAY, date=DAY), seen=s.seen))


def test_seen_before_lists_earlier_alike_outlines_with_time_conversation_and_name(tmp_path):
    s = make(tmp_path)

    async def go():
        outline(s.hub, "o1", 7, label="neutrophil", status="likely", source="ai:look")
        s.day.active_id = "t2"
        outline(s.hub, "o2", 7, label="neutrophil", status="confirmed", source="ai:look")
        outline(s.hub, "o3", 9, label="diatom", status="likely", source="ai:look")
        await settle()
        s.clock[0] = NINE + 3600
        outline(s.hub, "o4", 7, label="cell", status="likely", source="ai:look")
        await settle()
        run = tool(s, tmp_path).start("seen_before", {"object": "o4"}, ToolContext("voice", 1, 7, "where did I see one like this?"))
        await run.task
        return run

    run = asyncio.run(go())
    assert run.ok, run.summary
    said = run.summary
    assert said.startswith("Outlines named earlier today that look most like the cell")
    assert '09:12 in "Blood smear": "neutrophil" (1.00; the app named it)' in said
    assert '09:12 in "Pond water": "neutrophil" (1.00; the user named it)' in said
    assert "diatom" not in said  # not alike
    assert said.endswith("These were earlier, not in view now.")  # past tense: memory, not the view


def test_seen_before_says_when_nothing_named_earlier_is_alike(tmp_path):
    s = make(tmp_path)

    async def go():
        outline(s.hub, "o1", 9, label="diatom", status="likely", source="ai:look")
        await settle()
        s.clock[0] = NINE + 3600
        outline(s.hub, "o2", 7, label="neutrophil", status="likely", source="ai:look")
        await settle()
        run = tool(s, tmp_path).start("seen_before", {"object": "o2"}, ToolContext("chat", 1, 7, "have we seen this before?"))
        await run.task
        return run

    run = asyncio.run(go())
    assert run.ok, run.summary
    assert run.summary.startswith('No outline named earlier today looks much like the neutrophil (the closest, "diatom" '
                                  'at 09:12, scored 0.')
    assert "no earlier views from today" in run.summary  # and no views to compare the whole view with


def test_seen_before_with_seen_off_compares_whole_views_only(tmp_path):
    s = make(tmp_path)
    s.hub.settings.update({"seen": "off"})
    asked = []

    async def ask(images, prompt, max_output_tokens=0):
        asked.append(prompt)
        return SimpleNamespace(data={"same": [], "answer": "No."}, error="")

    async def go():
        outline(s.hub, "o1", 7, label="neutrophil", status="likely", source="ai:look")
        await settle()
        run = tool(s, tmp_path, ask).start("seen_before", {"object": "o1"}, ToolContext("chat", 1, 7, "seen this?"))
        await run.task
        return run

    run = asyncio.run(go())
    assert not run.ok and "no earlier views" in run.summary and s.embed.batches == []


def test_without_a_worker_naming_goes_on_and_says_why_in_the_log(tmp_path):
    s = make(tmp_path)
    agent, prompts, logged = naming_agent(s, tmp_path)
    s.seen.kept = [earlier(at=NINE - 3600)]

    async def broken(jpegs, model="dinov2-small"):
        raise RuntimeError("no GPU worker")

    s.taught.embed = broken

    async def click():
        outline(s.hub, "o2", 7)
        return await agent.identify("o2")

    assert asyncio.run(click())["label"] == "neutrophil"
    assert "Earlier today" not in prompts[0]
    assert any(r["kind"] == "seen-match" and "no GPU worker" in r["error"] for r in logged)


def test_only_highlights_are_kept():
    from orchestrator.seen import KINDS

    assert KINDS == ("highlight",)  # invariant 10: the follower and seen-before use highlights only
