"""Supplemental I6 regressions: real Brain outputs reach the next model input.

Model replies are synthetic boundary inputs. Brain, tool registry, highlight runner,
Board, Conversation, DayStore and context renderers are real. No image/model quality
or browser acceptance is inferred. Moment.awareness is the approved bounded facts seam.
"""
import asyncio
import json
from types import SimpleNamespace
from functools import partial
from datetime import datetime, timezone

import pytest

from orchestrator.board import Board
from orchestrator.brain import context
from orchestrator.brain.actions import outline_seen
from orchestrator.brain.registry import ToolContext, ToolFailed
from orchestrator.brain.toolbox import Services, build
from orchestrator.scene import AnchorGeometry
from orchestrator.scene import SceneObject
from orchestrator.brain.state import Conversation
from orchestrator.day import DayStore
from orchestrator.hub import Hub
from orchestrator.sight import Seen, SightView
from tests.test_brain import make
from tests.waiting import HANG_S


def _world():
    brain, hub, agent = make([
        {"answer": "Highlighting the other stoma.", "targets": [],
         "actions": [{"tool": "highlight", "args": {"things": ["s2"], "keep": True}}]},
        {"answer": "", "targets": [], "actions": []},
    ])
    hub.settings.fast = "off"
    geometry = AnchorGeometry(1280, 720, 600, 40, "run-highlight-A", epoch=0)

    def outline(x):
        return hub.scene.add(SceneObject(
            id=hub.scene.new_id(), kind="highlight",
            polygons=[[[x, 100], [x + 20, 100], [x, 120]]],
            anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai:test",
            label="stoma", anchor_geometry=geometry))

    first, second = outline(100), outline(300)
    sight = SimpleNamespace(view=SightView(
        frame_id=7, epoch=0, dx=0, dy=0, radius=600, summary="two stomata",
        specimen="leaf", things=[Seen("s1", "stoma", "left", (100, 100, 120, 120), .8),
                                 Seen("s2", "stoma", "right", (300, 100, 320, 120), .8)], focus=""))

    async def from_seen(ref, ctx):
        return await outline_seen(hub, lambda: sight.view, ref, ctx)

    brain.sight = sight
    brain.board = Board(hub, sight=sight)
    brain.services = Services(hub=hub, agent=agent, sight=sight, board=brain.board, outline_seen=from_seen)
    brain.registry = build(brain.services)
    return brain, hub, agent, sight, first, second


def _facts(moment):
    facts = getattr(moment, "awareness", {})
    assert isinstance(facts, dict), "Moment.awareness must contain bounded facts, not a second planner"
    return facts


def _model_inputs(brain, moment):
    """Actual existing consumer boundaries; wording may change, fact values may not."""
    return {
        "typed": "\n".join(context.scene_table(moment)) + "\n" + moment.history,
        "fast": "\n".join(str(v) for v in context.fast_fields(moment).values()),
        "voice_note": context.view_note(moment),
        "voice_recap": brain.voice_recap(),
    }


@pytest.mark.parametrize("channel", ["chat", "voice"])
def test_completed_highlight_output_has_exact_identity_in_next_typed_and_voice_inputs(channel):
    """Break caught: models retain ephemeral s2 while the actual second outline is omitted."""
    brain, hub, agent, sight, first, second = _world()

    async def sequence():
        result = await brain.turn("highlight th other stomma on page too", channel)
        assert result["actions"][0]["ok"] is True  # real highlight, not a hand-written success receipt
        assert result["objectIds"] == [second.id]
        sight.view = None  # the Sight observation is gone, both actual outlines remain
        # Exact retained resource identity must coexist with outline output receipts.
        brain.conversation.add("note", "system", "A saved source is available.", resourceIds=["image-previous-A"])
        moment = brain.moment()
        outputs = _facts(moment).get("outputRefs", [])
        assert any(row.get("id") == second.id and row.get("run") == "run-highlight-A"
                   and row.get("frame") == 7 and row.get("epoch") == 0
                   and row.get("turn_id") == result["turnId"] for row in outputs), outputs
        assert all(row.get("id") != "s2" for row in outputs), "Sight IDs are not durable output IDs"
        for reader, text in _model_inputs(brain, moment).items():
            assert second.id in text and "run-highlight-A" in text, (reader, text)
            assert "image-previous-A" in text, (reader, text)
        await brain.turn("also pull up zoom of that", "chat")
        # Inspect the actual next Brain.look boundary, not a surrogate planner's expected prose.
        next_input = json.dumps(agent.calls[-1], default=str)
        assert second.id in next_input and "run-highlight-A" in next_input
        assert hub.scene.objects[first.id] is first and hub.scene.objects[second.id] is second

    asyncio.run(sequence())


def test_unexecuted_assistant_claim_does_not_create_an_output_reference():
    brain, _, _, _, _, _ = _world()
    brain.conversation.add("assistant", "chat", "I highlighted s2 and opened o999.")
    outputs = _facts(brain.moment()).get("outputRefs", [])
    assert not any(row.get("id") in ("s2", "o999") for row in outputs)


def test_new_user_pointing_is_a_separate_fact_not_a_rewrite_of_completed_output():
    brain, hub, _, sight, first, second = _world()

    async def sequence():
        result = await brain.turn("highlight th other stomma on page too")
        sight.view = None
        hub.scene.select(first.id)  # legitimate new pointing before the next request
        asked = brain.board.when_asked()
        hub.scene.select(second.id)  # later change must not redirect the saved pointing
        ctx = ToolContext("chat", 2, 7, when_asked=asked)
        assert brain.board.resolve("that", ctx) is first
        moment = brain.moment(asked)
        outputs = _facts(moment).get("outputRefs", [])
        assert any(row.get("id") == second.id and row.get("turn_id") == result["turnId"] for row in outputs)
        assert first.id in moment.selected and second.id in moment.selected
        assert brain.board.resolve(second.id, ctx) is second  # explicit actual output still means itself

    asyncio.run(sequence())


@pytest.mark.parametrize("with_current_output", [False, True])
def test_unknown_explicit_outline_id_never_redirects_to_selection_or_this_turn_output(with_current_output):
    brain, hub, _, _, first, second = _world()
    hub.scene.select(first.id)
    ctx = ToolContext("chat", 2, 7, objects=[second.id] if with_current_output else [],
                      when_asked=brain.board.when_asked())
    with pytest.raises(ToolFailed):
        brain.board.resolve("o999", ctx)
    assert hub.scene.selected == first.id
    assert brain.board.resolve("that", ctx) is (second if with_current_output else first)


def test_stale_sight_id_is_not_an_alias_for_a_durable_outline_without_an_exact_request_binding():
    brain, _, _, sight, _, second = _world()
    sight.view = None
    ctx = ToolContext("chat", 2, 7, objects=[second.id])
    with pytest.raises(ToolFailed):
        brain.board.resolve("s2", ctx)
    assert brain.board.resolve(second.id, ctx) is second
    # An actual same-request binding is a valid positive control, not a guessed global alias.
    ctx.aliases["s2"] = second.id
    assert brain.board.resolve("s2", ctx) is second


def test_known_replaced_outline_alias_and_ordinal_selection_remain_valid():
    brain, hub, _, _, first, second = _world()
    ctx = ToolContext("voice", 2, 7, objects=[first.id, second.id], aliases={"o99": second.id})
    assert brain.board.resolve("o99", ctx) is second
    assert brain.board.resolve("target 2", ctx) is second
    assert brain.board.resolve("the second one", ctx) is second
    hub.scene.select(first.id)
    assert brain.board.resolve("that", ToolContext("voice", 3, 7, when_asked=brain.board.when_asked())) is first


def test_output_reference_reports_current_source_loss_without_retargeting_its_origin():
    brain, hub, _, sight, first, second = _world()

    async def sequence():
        result = await brain.turn("highlight th other stomma on page too")
        sight.view = None
        # Capture the actual facts before loss; a later moment may not mutate that snapshot.
        before = brain.moment()
        saved = json.dumps(_facts(before), sort_keys=True)
        hub.scene.remove(second.id)
        hub.scene.select(first.id)
        after = _facts(brain.moment()).get("outputRefs", [])
        assert any(row.get("id") == second.id and row.get("turn_id") == result["turnId"]
                   and row.get("available") is False for row in after), after
        assert json.dumps(_facts(before), sort_keys=True) == saved
        with pytest.raises(ToolFailed):
            brain.board.resolve(second.id, ToolContext("chat", 2, 7, when_asked=brain.board.when_asked()))

    asyncio.run(sequence())


def _birth_world(tmp_path, *, origin=7, epoch=0):
    """Real Scene/DayStore; camera and model are the existing synthetic boundaries."""
    brain, hub, agent, sight, first, previous = _world()
    hub.scene.remove(previous.id)
    root = tmp_path / "synthetic-birth-journal"
    at = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp()
    store = DayStore(root=root, day="2026-10-08", clock=lambda: at)
    conversation = Conversation(store=store)
    brain.conversation = agent.conversation = conversation
    agent.log = lambda _record: None
    geometry = AnchorGeometry(1280, 720, 600, 40, "run-highlight-A")
    # Historical frame 3 is intentionally absent; no current-frame substitution.
    hub.scene.geometry_at = lambda frame: geometry if frame in (7, 9, 10) and not (frame == 7 and epoch is None) else None
    second = hub.scene.add(SceneObject(
        id=hub.scene.new_id(), kind="highlight", polygons=[[[300, 100], [320, 100], [300, 120]]],
        anchor_frame=7, anchor_epoch=epoch, anchor_offset=(0.0, 0.0), source="ai:test",
        origin=origin, label="stoma", created_at=1000.0))
    return brain, hub, sight, second, root, at


def _commit_reanchor(hub, obj, frame, epoch):
    """Actual Hub reanchor/carry commit over synthetic measured-match output.

    This tests source identity across a real Scene.update, not image match quality.
    No CV worker/camera is executed by the fixture.
    """
    info = SimpleNamespace(id=frame, epoch=epoch, dx=0.0, dy=0.0, width=1280, height=720, fov=None)
    events = []
    carrier = SimpleNamespace(scene=hub.scene, _rejoined={}, on_carry=[lambda *row: events.append(row)])
    carrier.carried = partial(Hub.carried, carrier)
    Hub._apply_reanchor(carrier, [(obj.id, {"anchor_frame": frame, "anchor_epoch": epoch,
        "anchor_offset": (0.0, 0.0), "track": "tracking", "carried": ("re-find", .95)})], info)
    assert events == [(obj.id, "re-find", .95)]
    hub.latest_info = info


def _reload_birth(brain, root, at):
    store = DayStore(root=root, day="2026-10-08", clock=lambda: at)
    brain.conversation = brain.agent.conversation = Conversation(store=store)
    return store


@pytest.mark.parametrize("channel", ["chat", "voice"])
@pytest.mark.parametrize("carry_at", ["before-receipt", "after-receipt"])
def test_output_birth_capture_stays_coherent_across_real_carry_and_journal_reload(tmp_path, channel, carry_at):
    brain, hub, sight, obj, root, at = _birth_world(tmp_path)
    if carry_at == "before-receipt":
        _commit_reanchor(hub, obj, 9, 1)
        sight.view.frame_id, sight.view.epoch = 9, 1
    result = asyncio.run(brain.turn("highlight the other stoma too", channel))
    assert result["actions"][0]["ok"] is True and result["objectIds"] == [obj.id]
    if carry_at == "after-receipt":
        _commit_reanchor(hub, obj, 9, 1)
    _reload_birth(brain, root, at)
    refs = brain.conversation.output_refs()
    assert len(refs) == 1
    assert refs[0] == {"id": obj.id, "run": "run-highlight-A", "frame": 7, "epoch": 0,
                       "created_at": 1000.0, "turn_id": result["turnId"]}
    assert (obj.origin, obj.anchor_frame, obj.anchor_epoch) == (7, 9, 1)
    _commit_reanchor(hub, obj, 10, 2)
    current = brain.moment().awareness["outputRefs"]
    assert current[0]["frame"] == 7 and current[0]["epoch"] == 0
    assert current[0]["run"] == "run-highlight-A" and current[0]["available"] is True


@pytest.mark.parametrize("basis", ["historical-frame-unavailable", "unknown-producing-epoch"])
def test_missing_birth_basis_is_unknown_without_adopting_current_anchor_on_reload(tmp_path, basis):
    brain, hub, _, obj, root, at = _birth_world(tmp_path,
        origin=3 if basis == "historical-frame-unavailable" else 7,
        epoch=None if basis == "unknown-producing-epoch" else 0)
    brain.moment()  # installs the real qualification callback before the completion write
    asked = brain.conversation.add("user", "chat", "Keep this already-produced outline's exact source.")
    # Synthetic completion boundary: no claim that a historical outline was placed live.
    brain.conversation.add("note", "system", "An exact earlier outline is retained.", objectIds=[obj.id])
    _reload_birth(brain, root, at)
    refs = brain.conversation.output_refs()
    assert len(refs) == 1 and refs[0]["turn_id"] == asked.id
    assert refs[0]["frame"] == obj.origin and refs[0]["epoch"] is None
    if basis == "historical-frame-unavailable":
        assert refs[0]["run"] is None, "current anchor run cannot qualify missing producing capture"
    saved = dict(refs[0])
    assert brain.moment().awareness["outputRefs"][0]["available"] is False
    assert brain.conversation.output_refs() == [saved]


def test_actual_delayed_highlight_completion_keeps_its_originating_output_receipt(tmp_path):
    brain, hub, sight, obj, root, at = _birth_world(tmp_path)
    original = brain.registry.tools["highlight"].handler
    brain.registry.tools["highlight"].inline_ms = 1

    async def sequence():
        release, reported = asyncio.Event(), asyncio.Event()

        async def delayed(ctx, args):
            await release.wait()  # synthetic latency only; original registered highlight still acts
            return await original(ctx, args)

        async def late(ctx, run):
            assert run.tool == "highlight" and run.ok is True
            reported.set()

        brain.registry.tools["highlight"].handler = delayed
        brain.report_late = late
        result = await brain.turn("highlight the other stoma too", "voice")
        assert result["actions"][0]["ok"] is None
        release.set()
        await asyncio.wait_for(reported.wait(), timeout=HANG_S)
        sight.view = None
        _reload_birth(brain, root, at)
        refs = brain.conversation.output_refs()
        assert refs == [{"id": obj.id, "run": "run-highlight-A", "frame": 7, "epoch": 0,
                         "created_at": 1000.0, "turn_id": result["turnId"]}]
        assert brain.moment().awareness["outputRefs"][0]["available"] is True

    asyncio.run(sequence())
