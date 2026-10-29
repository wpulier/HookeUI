"""Activity (orchestrator/activity.py; the user's decision Q2, 2026-10-11): each step says the models that worked on it,
from its executions and the settings it read; everything that is not a chat request is one line in plain words, filed
with its conversation and replayed; and nothing names a model of a blind comparison before its vote."""
import asyncio
import json
import threading
from types import SimpleNamespace

from orchestrator import activity, names
from orchestrator.activity import Activity, models_of
from orchestrator.app import App
from orchestrator.brain.claims import answer_facts
from orchestrator.brain.registry import Registry, Tool, ToolContext, ToolRun, attach, schema
from orchestrator.commands import handle
from orchestrator.day import DayStore
from orchestrator.dialogue import Dialogue
from orchestrator.operations import Attempt, Execution, runner
from orchestrator.options import choices_of
from orchestrator.paths import Paths
from orchestrator.scene import SceneObject
from tests.fakes import Hub


def run(coro):
    return asyncio.run(coro)


def execution(operation="outline", requested="edgetam", executed="edgetam", *, trial=None, attempts=None,
              selection="settings"):
    record = Execution(operation, requested, "route", "brain-default", 7, selection, trial=trial,
                       attempts=attempts if attempts is not None else [Attempt(executed, True, why="chosen")],
                       executed=executed)
    return record


def hub_with(*records):
    hub = Hub()
    found = runner(hub)
    for record in records:
        found.executions[record.id] = record
    return hub


def step(hub, records, reads=()):
    return ToolRun("find", {}, ok=True, summary="found 2 stomata", ms=812, reads=sorted(reads),
                   executions=[r.id for r in records])


# --- what ran, per step -------------------------------------------------------------------------
def test_a_find_step_names_the_vision_model_that_found_and_edgetam_that_drew():
    drawn = execution()
    hub = hub_with(drawn)
    models = models_of(hub, step(hub, [drawn], reads=("outline", "pointing", "vision")))
    # The default eyes (Claude Opus 5.5 in fast mode) found them.
    assert models == [{"role": "found by", "name": "Claude Opus 5.5 (fast mode)", "said": "Claude Opus 5.5 (fast mode) in the cloud"},
                      {"role": "drawn by", "name": "EdgeTAM", "said": "EdgeTAM (on this Mac)"}]
    assert activity.brief(models) == "Claude Opus 5.5 (fast mode) → EdgeTAM"


def test_a_request_that_named_sam_2_1_and_another_vision_model_says_those():
    drawn = execution(requested="sam2.1-tiny", executed="sam2.1-tiny", selection="request")
    hub = hub_with(drawn)
    with choices_of({"vision": "gpt-6-luna"}):  # this request's own choice of the vision model (`use`)
        models = models_of(hub, step(hub, [drawn], reads=("outline", "vision")))
    assert [(m["role"], m["name"]) for m in models] == [("found by", "GPT-6 Luna"), ("drawn by", "SAM 2.1 tiny")]
    assert names.anywhere(models[1]["name"]), "every name Activity says is one names.py resolves"


def test_a_fallback_says_what_could_not_run():
    drawn = execution(executed="sam3.1-fal", attempts=[Attempt("edgetam", True, empty=True, why="chosen"),
                                                       Attempt("sam3.1-fal", True, why="empty-box")])
    hub = hub_with(drawn)
    (model,) = models_of(hub, step(hub, [drawn]))
    assert model == {"role": "drawn by", "name": "SAM 3.1 (cloud)", "said": "SAM 3.1 (cloud)",
                     "instead": "EdgeTAM (on this Mac)"}


def test_an_algorithm_is_named_only_when_it_ran_in_a_models_place():
    measured = execution("motion", "spots", "spots")
    ring = execution(executed="spot-ring", attempts=[Attempt("edgetam", True, empty=True, why="chosen"),
                                                     Attempt("spot-ring", True, why="unsure-spot")])
    hub = hub_with(measured, ring)
    assert models_of(hub, step(hub, [measured])) == []
    assert [m["name"] for m in models_of(hub, step(hub, [ring]))] == ["A ring around the detected spot"]


def test_a_blind_trials_step_names_no_model_until_the_vote():
    arm = execution(executed="sam2.1-tiny", trial="t1")
    hub = hub_with(arm)
    blind = step(hub, [arm], reads=("vision",))
    assert models_of(hub, blind) == []  # not even the vision model: nothing of the run is said
    runner(hub).reveal(arm.id)
    assert [m["name"] for m in models_of(hub, blind)] == ["Claude Opus 5.5 (fast mode)", "SAM 2.1 tiny"]


def test_a_settings_read_that_runs_no_model_is_not_a_model():
    hub = Hub()
    hub.settings.fast = "off"
    assert models_of(hub, ToolRun("count", {}, reads=["fast", "outline"])) == []


def test_the_registry_names_a_runs_models_when_it_ends_and_the_model_is_never_told_them():
    drawn = execution()
    hub = hub_with(drawn)
    registry = Registry()
    registry.models = lambda r: models_of(hub, r)

    async def find(ctx, args):
        hub.settings.vision  # the look (a read, as Agent.look makes it)
        attach(execution=drawn.id)
        return "found 2 stomata"

    registry.add(Tool("find", "", schema({}), find))

    async def go():
        r = registry.start("find", {}, ToolContext("chat", 3, 7))
        await r.task
        return r

    done = run(go())
    assert [m["name"] for m in done.models] == ["Claude Opus 5.5 (fast mode)", "EdgeTAM"]
    assert done.wire()["models"] == done.models
    block = answer_facts([done])
    told = json.loads(block.split('untrusted="true">', 1)[1].split("</answer-facts>", 1)[0])
    assert told["operations"][0]["tool"] == "find" and "models" not in told["operations"][0]  # Activity's only


# --- Slice 5's paths (2026-10-11): a choice per step, a redraw, a kept comparison ---------------
def started(registry, world, steps):
    """The steps of one request, through the registry (so each run is named when it ends), in order."""
    from orchestrator.ticket import issue, using

    registry.models = lambda r: models_of(world, r)

    async def go():
        runs, ctx = [], ToolContext("chat", 1, world.latest_info.id, "outline it with SAM 2.1, then with EdgeTAM")
        with using(issue(world, "turn")):
            for name, args in steps:
                r = registry.start(name, args, ctx)
                await r.task
                runs.append(r)
        return runs

    return run(go())


def test_a_choice_per_step_and_a_redraw_name_the_model_each_step_drew_with():
    from types import SimpleNamespace as NS

    from orchestrator import tools
    from orchestrator.brain.toolbox.choosing import add_choosing_tools
    from orchestrator.brain.toolbox.view import add_highlight
    from tests.test_apply import World

    world = World()
    registry = Registry()
    add_choosing_tools(registry, NS(hub=world, publish_settings=None))
    add_highlight(registry, world, NS(view=None), outline_seen=None)
    stoma = run(tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="ai:test", label="stoma",
                                 status="likely"))
    runs = started(registry, world, [("use", {"by": "user", "operation": "outline", "implementation": "sam2.1-tiny"}),
                                     ("highlight", {"things": [stoma.id]}),
                                     ("use", {"by": "user", "operation": "outline", "implementation": "edgetam"}),
                                     ("highlight", {"things": [stoma.id]})])
    assert [r.ok for r in runs] == [True] * 4
    assert runs[0].models == [] and runs[2].models == []  # a choice runs no model: the steps after it say what drew
    assert [m["name"] for m in runs[1].models if m["role"] == "drawn by"] == ["SAM 2.1 tiny"]  # drawn again with it
    assert [m["name"] for m in runs[3].models if m["role"] == "drawn by"] == ["EdgeTAM"]


def test_a_kept_comparison_names_the_model_of_the_arm_it_kept():
    from orchestrator import tools
    from tests.test_compare_models import setting_up

    world, registry, user_obj, shown = setting_up()
    app_obj = run(tools.outline_at(world, world.latest_info.id, 600, 300, box=(560, 260, 640, 340), source="ai:test",
                                   label="stoma", status="likely"))
    compared, = started(registry, world, [("compare_models", {"implementations": ["EdgeTAM", "SAM 2.1 tiny"],
                                                              "object": app_obj.id})])
    assert sorted(m["name"] for m in compared.models) == ["EdgeTAM", "SAM 2.1 tiny"]
    kept, = started(registry, world, [("compare_models", {"keep": "SAM 2.1 tiny"})])
    assert kept.ok and kept.models == [{"role": "drawn by", "name": "SAM 2.1 tiny", "said": "SAM 2.1 tiny (on this Mac)"}]


def test_a_restarts_still_uses_note_is_a_line_of_activity_with_its_model(tmp_path):
    """The day's "from now on" choices, said again on a restart (defaults.py), reach Activity as a line naming the
    model (an app notice, said in its own words)."""
    from orchestrator import defaults as kept

    built = App.build("http://127.0.0.1:1", Paths.under(tmp_path), use_gpu=False)
    kept.change(built.hub, built.day, {"outline": "sam2.1-tiny"}, "user")
    again = App.build("http://127.0.0.1:1", Paths.under(tmp_path), use_gpu=False)
    notes = [line for line in again.dialogue.history()["lines"]
             if line.get("type") == "app-notice" and line.get("event") == "defaults-restored"]
    assert notes and "Outlining still uses SAM 2.1 tiny (on this Mac), as you asked at " in notes[-1]["summary"]


# --- one line, filed with its conversation ------------------------------------------------------
def sink(tmp_path, clock=lambda: 1791712000.0):
    day = DayStore(root=tmp_path / "sessions", clock=clock)
    shown = []
    hub = Hub()
    dialogue = Dialogue(day, shown.append)
    hub.activities = Activity(hub, dialogue, clock=clock)
    return hub, day, shown


def test_a_line_is_journaled_replayed_and_replaced_by_its_id(tmp_path):
    hub, day, shown = sink(tmp_path)
    key = activity.model_loading(hub, "micro-sam", expected_s=120)
    assert shown[-1]["type"] == "activity" and shown[-1]["state"] == "running"
    assert shown[-1]["text"] == "Loading micro-sam (usually about 2 min)" and shown[-1]["thread"] == "t1"
    activity.model_loaded(hub, key, seconds=118.4)
    assert shown[-1]["id"] == key and shown[-1]["state"] == "done"
    assert shown[-1]["text"] == "micro-sam is ready (loaded in 1 min 58 s)"
    activity.model_unloaded(hub, "micro-sam", why="to free memory for Cellpose-SAM")
    # Read back on the same day's clock as it was written (the real clock's day moves at midnight: 2026-10-12).
    again = Dialogue(DayStore(root=tmp_path / "sessions", clock=lambda: 1791712000.0), lambda _: None).history()["lines"]
    assert [(line["text"], line["state"]) for line in again] == [
        ("micro-sam is ready (loaded in 1 min 58 s)", "done"), ("Unloaded micro-sam to free memory for Cellpose-SAM", "done")]
    failed = activity.model_loading(hub, "Molmo 2")
    activity.model_loaded(hub, failed, ok=False, why="out of memory")
    assert shown[-1]["state"] == "failed" and shown[-1]["text"] == "Molmo 2 did not load: out of memory"


def test_an_update_is_filed_in_the_conversation_its_line_began_in(tmp_path):
    hub, day, shown = sink(tmp_path)
    key = activity.model_loading(hub, "micro-sam")
    day.open_thread("Onion")  # the user moved on while it loaded
    activity.model_loaded(hub, key, seconds=3)
    assert shown[-1]["thread"] == "t1" and not day.threads["t2"].lines


def test_a_load_said_from_another_thread_is_filed_on_the_apps_loop(tmp_path):
    hub, day, shown = sink(tmp_path)

    async def go():
        activity.model_unloaded(hub, "EdgeTAM", why="to free memory")  # on the loop: the sink learns it
        worker = threading.Thread(target=lambda: activity.model_loading(hub, "micro-sam", key="model:micro-sam:1"))
        worker.start()
        worker.join()
        await asyncio.sleep(0.01)

    run(go())
    assert [line["text"] for line in shown] == ["Unloaded EdgeTAM to free memory", "Loading micro-sam"]


def test_without_activity_a_producer_does_nothing():
    assert activity.activity(SimpleNamespace(), "model", "Loading micro-sam") is None
    assert activity.model_loading(SimpleNamespace(), "micro-sam")


def test_a_kind_or_state_outside_the_contract_is_refused(tmp_path):
    hub, _, _ = sink(tmp_path)
    for bad in ({"kind": "chat"}, {"kind": "model", "state": "pending"}):
        try:
            activity.activity(hub, bad["kind"], "x", state=bad.get("state", "done"))
        except ValueError:
            continue
        raise AssertionError(bad)


# --- requests (the brain's turn) ----------------------------------------------------------------
def test_a_request_is_filed_with_its_words_channel_and_steps_running_then_done(tmp_path):
    from tests.test_app import brain_over_a_day

    brain, hub, day, dialogue, shown = brain_over_a_day(
        tmp_path, [{"answer": "Selecting it.", "targets": [], "actions": [{"tool": "select", "args": {"object": "stoma"}}]}])
    hub.activities = Activity(hub, dialogue)
    result = run(brain.turn("select the stoma", "chat"))
    records = [line for line in shown if line["type"] == "activity"]
    assert [r["state"] for r in records] == ["running", "done"] and len({r["id"] for r in records}) == 1
    done = records[-1]
    assert done["kind"] == "request" and done["text"] == "select the stoma"
    assert done["ref"] == {"turnId": result["turnId"], "channel": "chat"}
    assert [s["tool"] for s in done["steps"]] == ["select"] and done["steps"][0]["actionId"]
    assert done["details"]["answer"]["published"] is bool(result["answer"])
    assert done["details"]["answer"]["model"] == result["model"] and done["details"]["answer"]["calls"] == result["calls"]
    assert [line["state"] for line in day.active.lines] == ["done"]  # replaced in place in the journal


def test_a_request_that_takes_the_user_to_another_conversation_keeps_its_words_in_both(tmp_path):
    from orchestrator.brain.toolbox import Services, build
    from tests.test_app import brain_over_a_day

    brain, hub, day, dialogue, shown = brain_over_a_day(tmp_path, [
        {"answer": "Going back to it.", "targets": [], "actions": [{"tool": "switch_thread", "args": {"thread": "blood smear"}}]},
        {"answer": "We concluded the platelet looked green to you.", "targets": [], "actions": []}])
    brain.services = Services(hub=hub, agent=brain.agent, day=day)
    brain.registry = build(brain.services)
    hub.activities = Activity(hub, dialogue)
    brain.conversation.add("user", "voice", "is that a platelet? it looks green")
    day.set_subject("blood smear", "look")
    day.open_thread("Onion")
    brain.agent.fast_model.rewrite = {"answer": "We concluded the platelet looked green to you."}
    result = run(brain.turn("back to the blood smear, what did we conclude?", "chat"))
    assert result["asked"].thread == "t1" and day.active_id == "t1"  # the request took the user back (its answer goes there)
    for thread in ("t2", "t1"):  # asked in t2, answered in t1: its entry, with its words, in each, finished
        (record,) = [line for line in day.threads[thread].lines if line["type"] == "activity"]
        assert record["text"] == "back to the blood smear, what did we conclude?" and record["state"] == "done"
        assert [s["tool"] for s in record["steps"]] == ["switch_thread"]


def test_a_request_that_could_not_finish_says_so(tmp_path):
    from tests.test_app import brain_over_a_day

    brain, hub, day, dialogue, shown = brain_over_a_day(tmp_path, [{"answer": "x", "targets": [], "actions": []}])
    hub.activities = Activity(hub, dialogue)

    async def broken(*a, **k):
        raise RuntimeError("the provider fell over")

    brain.agent.look = broken
    try:
        run(brain.turn("what is that?", "voice"))
    except Exception:
        pass
    records = [line for line in shown if line["type"] == "activity"]
    assert records and records[-1]["state"] == "failed" and records[-1]["ref"]["channel"] == "voice"
    assert "something went wrong" in records[-1]["details"]["lines"][0]
    assert "fell over" not in json.dumps(records)  # plain words, never the raw error


# --- the studio's commands, clicks and settings (as wired: App.build) ---------------------------
class Client:
    def __init__(self):
        self.sent = []

    def offer(self, text):
        self.sent.append(json.loads(text))


def app(tmp_path):
    built = App.build("http://127.0.0.1:1", Paths.under(tmp_path), use_gpu=False)
    sent = []
    built.hub.broadcast = lambda payload: sent.append(json.loads(json.dumps(payload, default=str)))
    built.dialogue.publish = built.hub.broadcast
    return built, sent


def lines(sent):
    return [m for m in sent if m.get("type") == "activity"]


def outline(built, label="stoma"):
    return built.hub.scene.add(SceneObject(id=built.hub.scene.new_id(), kind="highlight", polygons=[[[1, 1], [9, 1], [5, 9]]],
                                           anchor_frame=1, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="click",
                                           label=label))


def test_the_studios_actions_are_said_in_plain_words(tmp_path):
    built, sent = app(tmp_path)
    obj = outline(built)
    client = Client()
    run(handle(built, client, {"type": "select", "id": obj.id}))
    run(handle(built, client, {"type": "focus", "id": obj.id}))  # the pointer resting: not the user's act
    run(handle(built, client, {"type": "clear"}))
    run(handle(built, client, {"type": "remove", "id": "o999"}))
    said = [(m["kind"], m["text"], m["state"]) for m in lines(sent)]
    assert said == [("command", "You selected stoma", "done"), ("command", "You cleared the outlines (1 taken off)", "done"),
                    ("command", "You removed an outline: it didn't work", "failed")]
    assert all(m["thread"] == "t1" and m["details"]["ids"]["command"] for m in lines(sent))


def test_taking_a_researcher_off_the_working_strip_and_back_is_said_in_plain_words(tmp_path, monkeypatch):
    """The studio's bottom row is the Working strip (it is no longer called a shelf). Taking a researcher's chip off it
    leaves the researcher listed in Researchers, so the line says only what happened on the strip."""
    built, sent = app(tmp_path)
    named = SimpleNamespace(name="Researcher 2")
    monkeypatch.setattr(built.research, "get", lambda rid: named if rid == "r1" else None)
    client = Client()
    run(handle(built, client, {"type": "presenter", "action": "remove", "rid": "r1"}))
    run(handle(built, client, {"type": "presenter", "action": "restore", "rid": "r1"}))
    run(handle(built, client, {"type": "presenter", "action": "remove", "rid": "r9"}))
    said = [(m["text"], m["state"]) for m in lines(sent)]
    assert said == [("You took Researcher 2 off the Working strip", "done"),
                    ("You put Researcher 2 back on the Working strip", "done"),
                    ("You took a researcher off the Working strip", "done")]
    assert not any("shelf" in m["text"].lower() for m in lines(sent))


def test_a_refused_studio_command_says_why_in_its_details(tmp_path, monkeypatch):
    """The demo of 2026-10-11: Activity said "it didn't work" ten times and never why. The refusal's own words are its
    details, as a plain sentence, and its code is under Technical."""
    from orchestrator import commands

    built, sent = app(tmp_path)
    said = "the magnifier is at least 280 px wide; say a width of 280 px or more"

    async def refused(*_):
        return {"ok": False, "summary": said, "code": "TARGET_REVISED",
                "run": {"tool": "present", "ok": False, "summary": said, "next": "say a width of 280 px or more"}}

    monkeypatch.setitem(commands.COMMANDS, "present", refused)
    run(handle(built, Client(), {"type": "present", "schema": "presentation-manual-v2", "op": "resize"}))
    line = lines(sent)[-1]
    assert line["state"] == "failed" and line["text"].endswith(": it didn't work")
    assert line["details"]["lines"] == ["Why: The magnifier is at least 280 px wide; say a width of 280 px or more."]
    assert line["details"]["ids"] == {"command": "present", "code": "TARGET_REVISED"}


def test_a_setting_the_user_chose_and_one_the_app_set_for_a_request(tmp_path):
    from orchestrator.commands import run_tool

    built, sent = app(tmp_path)
    run(handle(built, Client(), {"type": "settings", "settings": {"vision": "gemini-3.5-flash"}}))
    run(run_tool(built, "set_level", {"level": "technical"}, question="be technical with me"))
    said = [m["text"] for m in lines(sent) if m["kind"] == "setting"]
    assert said == ["You chose Gemini 3.5 Flash (cloud) for seeing (Models menu)",
                    "The app set explaining to technical (you asked: “be technical with me”)"]
    run(handle(built, Client(), {"type": "settings", "settings": {"vision": "no-such-model"}}))
    assert lines(sent)[-1]["state"] == "failed" and "unknown vision option" in lines(sent)[-1]["text"]


def test_magnifier_levels_from_a_wheel_are_one_line(tmp_path):
    built, sent = app(tmp_path)
    obj = outline(built)
    client = Client()
    run(handle(built, client, {"type": "magnifier", "action": "zoom", "id": obj.id}))
    for level in (2, 3, 4.5):
        run(handle(built, client, {"type": "magnifier", "action": "level", "level": level}))
    run(handle(built, client, {"type": "magnifier", "action": "index", "index": 0}))  # sent by the studio itself
    texts = [m["text"] for m in lines(sent)]
    assert texts[0] == "You magnified stoma" and texts[-1].startswith("You zoomed the magnifier to ")
    assert len({m["id"] for m in lines(sent)[1:]}) == 1  # the wheel's levels replaced one line


def stub_outline(monkeypatch, built, impl="edgetam", trial_fail=()):
    from orchestrator import tools

    async def outline_at(hub, frame_id, x, y, *, box=None, source="click", model=None, trial=None, arm=None, **kw):
        executed = model or impl
        if executed in trial_fail:
            raise tools.ToolError("nothing distinct to outline there")
        record = execution(requested=executed, executed=executed, trial=trial)
        runner(hub).executions[record.id] = record
        obj = SceneObject(id=hub.scene.new_id(), kind="highlight", polygons=[[[x, y], [x + 9, y], [x, y + 9]]],
                          anchor_frame=frame_id, anchor_epoch=0, anchor_offset=(0, 0), source=source, trial=trial, arm=arm,
                          made_by={**record.made_by()})
        return hub.scene.add(obj)

    monkeypatch.setattr(tools, "outline_at", outline_at)


def test_a_click_says_what_outlined_it_and_how_long(tmp_path, monkeypatch):
    built, sent = app(tmp_path)
    stub_outline(monkeypatch, built)
    built.agent.available = lambda *a, **k: False  # no naming in this test
    run(handle(built, Client(), {"type": "click", "frameId": 1, "x": 50, "y": 60}))
    (click,) = [m for m in lines(sent) if m["kind"] == "click"]
    assert click["text"].startswith("You clicked: outlined by EdgeTAM (on this Mac) · ")
    assert click["details"]["models"] == [{"role": "drawn by", "name": "EdgeTAM", "said": "EdgeTAM (on this Mac)"}]
    assert click["ref"]["objectId"] in built.hub.scene.objects and click["state"] == "done"
    run(handle(built, Client(), {"type": "click", "frameId": 1, "x": 50, "y": 60, "box": [1, 1, 2, 2]}))
    assert lines(sent)[-1]["state"] == "failed" and lines(sent)[-1]["text"].startswith("You dragged a box: nothing was outlined")


def test_naming_a_click_adds_the_name_and_the_model_that_gave_it(tmp_path, monkeypatch):
    built, sent = app(tmp_path)
    stub_outline(monkeypatch, built)
    built.agent.available = lambda *a, **k: True

    async def identify(object_id):
        built.hub.scene.update(object_id, label="guard cell", status="likely",
                               naming={"by": "model", "option": "gemini-3.8-flash", "model": "gemini-3.8-flash"})

    built.agent.identify = identify

    async def go():
        await handle(built, Client(), {"type": "click", "frameId": 1, "x": 50, "y": 60})
        await asyncio.sleep(0.05)

    run(go())
    clicks = [m for m in lines(sent) if m["kind"] == "click"]
    assert len({m["id"] for m in clicks}) == 1
    assert clicks[-1]["text"].endswith(', named “guard cell” by Gemini 3.8 Flash (cloud)')
    assert [m["role"] for m in clicks[-1]["details"]["models"]] == ["drawn by", "named by"]


def test_before_a_vote_no_broadcast_names_a_model_of_the_trials_arms(tmp_path, monkeypatch):
    """The generic check: run a blind outline trial through a click as the studio sends it, then look for every name
    names.py has for its arms (ids, names, names said with where they run) in everything sent to the studios about it.
    The comparison's own settings (`arena`: the options the user chose to compare, never which letter is which) and
    the catalogues of every model (`settings`, `capabilities`) name them by design and are left out."""
    built, sent = app(tmp_path)
    stub_outline(monkeypatch, built, trial_fail=("sam2.1-tiny",))
    run(handle(built, Client(), {"type": "arena", "arena": {"enabled": True}}))
    run(handle(built, Client(), {"type": "click", "frameId": 1, "x": 50, "y": 60}))
    trial = built.arena.current
    assert trial is not None and trial.winner is None
    space = names.index()["outline"]
    words = set()
    for arm in trial.arms.values():
        entry = space.entries[arm.option]
        words |= {entry.name, names.said(entry.name, entry.where), names.said_impl(arm.option)}
    words |= {a.option for a in trial.arms.values() if len(a.option) > 6}  # ids long enough not to be ordinary words
    about = [m for m in sent if m.get("type") not in ("arena", "settings", "capabilities")]
    assert {m.get("type") for m in about} >= {"trial", "activity"}
    text = json.dumps(about + [{"type": "scene", **built.hub.scene.to_wire()}, built.dialogue.history()])
    assert not [w for w in words if w in text], [w for w in words if w in text]
    (click,) = [m for m in lines(sent) if m["kind"] == "click"]
    assert click["details"]["models"] == [] and "blind comparison" in click["text"]


def test_a_researchers_start_is_a_line_running_until_it_stops(tmp_path):
    hub, day, shown = sink(tmp_path)
    found = hub.activities
    session = SimpleNamespace(rid="r1", name="Researcher 2", purpose="research", status="queued", run=None, history=[],
                              label="", brief="find out how stomata open", effort="quick", asking="")
    found.research_changed(session)
    session.status = "running"
    found.research_changed(session)  # a step within the same run: nothing new
    session.status = "timed_out"
    found.research_changed(session)
    texts = [(line["text"], line["state"]) for line in shown]
    assert texts == [("Researcher 2 started: find out how stomata open", "running"),
                     ("Researcher 2 started: find out how stomata open", "failed")]
    assert shown[-1]["details"]["lines"][-1] == "Researcher 2: ran out of time"
    description = SimpleNamespace(**{**vars(session), "rid": "r2", "purpose": "description", "status": "running"})
    found.research_changed(description)  # descriptions under labels are not researchers here
    assert len(shown) == 2


def test_a_researcher_a_request_started_is_said_once_as_that_requests_step(tmp_path):
    """start_researcher (and a follow-up or a new version) is the request's own step; its plan, questions and finish
    are the announcer's notices. Only a run nothing in the panel started gets a line of its own (a mission's slot)."""
    hub, day, shown = sink(tmp_path)
    found = hub.activities
    asked = {"channel": "chat", "turn_id": 12, "request_id": "q1", "action_id": "a1", "thread": "t1"}
    session = SimpleNamespace(rid="r1", name="Researcher 2", purpose="research", status="queued", run=None, history=[],
                              label="stomata", brief="find out how stomata open", effort="quick", asking="", origin=asked)
    found.research_changed(session)
    session.status = "done"
    found.research_changed(session)
    assert shown == []  # the request's step said it started; the announcer's notice says it finished
    slot = SimpleNamespace(**{**vars(session), "rid": "r3", "status": "running",
                              "_attempt": SimpleNamespace(mission=SimpleNamespace(id="m1"))})
    found.research_changed(slot)
    slot.status = "done"
    found.research_changed(slot)
    assert [(line["text"], line["state"]) for line in shown] == [("Researcher 2 started: stomata", "running"),
                                                                 ("Researcher 2 started: stomata", "done")]


def test_every_activity_line_the_code_files_has_a_kind_activity_takes():
    """A kind Activity refuses raises where the line is filed (live 2026-10-13: Check for catalog updates failed with
    "an activity kind is one of ..." because its lines were filed as "catalog")."""
    import re
    from pathlib import Path

    from orchestrator.activity import KINDS

    root = Path(__file__).resolve().parent.parent / "orchestrator"
    used = {}
    for path in root.rglob("*.py"):
        for m in re.finditer(r"\bactivity\.activity\(\s*[\w.]+,\s*\"([a-z-]+)\"", path.read_text()):
            used.setdefault(m.group(1), []).append(str(path.relative_to(root)))
    assert used and not {k: v for k, v in used.items() if k not in KINDS}
