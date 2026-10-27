"""Synthetic I6 receipts through the real DayStore journal and shared context.

The highlight uses existing Brain/Registry/Board/Conversation code with synthetic
model decisions. Serialization adversaries are explicitly synthetic metadata, not
private fixtures or browser captures. Only the parent runs these regressions.
"""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from orchestrator.brain import context
from orchestrator.brain.state import Conversation
from orchestrator.day import DayStore
from orchestrator.scene import AnchorGeometry
from tests.test_context_output_identity import _world


DAY = "2026-10-08"
DAY_TIME = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp()


def _store(root):
    return DayStore(root=root, day=DAY, clock=lambda: DAY_TIME)


def _attach(brain, store):
    conversation = Conversation(store=store)
    brain.conversation = conversation
    brain.agent.conversation = conversation
    return conversation


def _persistent_world(tmp_path):
    brain, hub, agent, sight, first, second = _world()
    root = tmp_path / "synthetic-sessions"
    store = _store(root)
    _attach(brain, store)
    second.created_at = 1000.0
    agent.log = lambda record: None  # optional diagnostic logging is off, journal remains real
    return brain, hub, sight, first, second, store, root


def _row(oid="o2", **changes):
    return {"id": oid, "run": "run-highlight-A", "frame": 7, "epoch": 0,
            "created_at": 1000.0, "turn_id": 1, **changes}


def _readers(brain, moment):
    return {"typed": "\n".join(context.scene_table(moment)) + "\n" + moment.history,
            "fast": "\n".join(str(value) for value in context.fast_fields(moment).values()),
            "voice_note": context.view_note(moment), "voice_recap": brain.voice_recap()}


@pytest.mark.parametrize("channel", ["chat", "voice"])
def test_completed_highlight_receipt_survives_journal_reload_with_logging_off(tmp_path, channel):
    brain, hub, sight, first, second, store, root = _persistent_world(tmp_path)
    result = asyncio.run(brain.turn("highlight th other stomma on page too", channel))
    assert result["actions"][0]["ok"] is True and result["objectIds"] == [second.id]
    assert hub.scene.objects[first.id] is first and hub.scene.objects[second.id] is second
    original_thread = store.active_id
    brain.conversation.add("note", "system", "Synthetic retained picture receipt.",
                           resourceIds=["image-previous-A"])
    sight.view = None

    reloaded = _store(root)
    conversation = _attach(brain, reloaded)
    completion = next(turn for turn in reloaded.active.turns if (turn.refs or {}).get("objectIds"))
    expected = _row(second.id, turn_id=result["turnId"])
    assert completion.thread == original_thread and completion.refs.get("outputRefs") == [expected]
    assert conversation.output_refs() == [expected]
    moment = brain.moment()
    refs = getattr(moment, "awareness", {}).get("outputRefs", [])
    assert any(row.get("id") == second.id and row.get("run") == "run-highlight-A"
               and row.get("frame") == 7 and row.get("epoch") == 0
               and row.get("turn_id") == result["turnId"] and row.get("available") is True for row in refs)
    for reader, text in _readers(brain, moment).items():
        assert second.id in text and "run-highlight-A" in text and "image-previous-A" in text, (reader, text)
    assert all(row.get("id") != "s2" for row in refs)


def test_reloaded_output_receipt_belongs_to_its_originating_conversation(tmp_path):
    brain, _, sight, _, second, store, root = _persistent_world(tmp_path)
    result = asyncio.run(brain.turn("highlight th other stomma on page too"))
    original_thread = store.active_id
    other = store.open_thread("Another synthetic conversation")
    brain.conversation.add("user", "chat", "A separate request without output.")
    sight.view = None

    reloaded = _store(root)
    conversation = _attach(brain, reloaded)
    assert reloaded.active_id == other.id and conversation.output_refs() == []
    assert getattr(brain.moment(), "awareness", {}).get("outputRefs") == []
    reloaded.switch(original_thread)
    assert conversation.output_refs() == [_row(second.id, turn_id=result["turnId"])]
    assert getattr(brain.moment(), "awareness", {}).get("outputRefs", [])[0]["available"] is True


@pytest.mark.parametrize("loss", ["removed", "another-run"])
def test_reloaded_output_preserves_origin_when_its_exact_outline_becomes_unavailable(tmp_path, loss):
    brain, hub, sight, first, second, _, root = _persistent_world(tmp_path)
    result = asyncio.run(brain.turn("highlight th other stomma on page too"))
    sight.view = None
    _attach(brain, _store(root))
    before = brain.moment()
    snapshot = json.dumps(getattr(before, "awareness", {}), sort_keys=True)
    if loss == "removed":
        hub.scene.remove(second.id)
    else:
        # An identical numeric ID/creation time in another run is not the old identity.
        second.anchor_geometry = AnchorGeometry(1280, 720, 600, 40, "run-restarted-B")
    hub.scene.select(first.id)
    after = getattr(brain.moment(), "awareness", {}).get("outputRefs", [])
    assert any(row.get("id") == second.id and row.get("run") == "run-highlight-A"
               and row.get("frame") == 7 and row.get("epoch") == 0
               and row.get("turn_id") == result["turnId"] and row.get("available") is False for row in after)
    assert json.dumps(getattr(before, "awareness", {}), sort_keys=True) == snapshot


def test_output_receipt_serialization_is_allowlisted_detached_and_bounded(tmp_path):
    root = tmp_path / "synthetic-sessions"
    store = _store(root)
    rows = [_row(f"o{number}", image="SYNTHETIC_PRIVATE_PIXELS", prompt="SYNTHETIC_PRIVATE_PROMPT",
                 nested={"secret": "SYNTHETIC_PRIVATE_NESTED"}) for number in range(1, 41)]
    # The serializer must neither retain arbitrary nested refs nor shorten identities.
    rows.insert(0, _row("o" + "9" * 10000))
    rows.insert(1, _row("o90", run="r" * 10000))
    rows.insert(3, _row("o1"))  # repeated receipt must not consume another retained slot
    turn = store.add_turn("assistant", "chat", "Synthetic receipt serialization boundary.",
                          refs={"objectIds": ["o1"], "resourceIds": ["image-previous-A"], "outputRefs": rows,
                                "privateGraph": {"secret": "SYNTHETIC_PRIVATE_GRAPH"},
                                "arbitraryRows": [{"secret": "SYNTHETIC_PRIVATE_ARBITRARY"}]})
    expected = [_row(f"o{number}") for number in range(1, 9)]
    assert turn.refs.get("outputRefs") == expected
    assert len(json.dumps(turn.refs)) <= 8192
    frozen = json.dumps(turn.refs, sort_keys=True)
    rows[2]["run"] = "MUTATED_AFTER_WRITE"
    assert json.dumps(turn.refs, sort_keys=True) == frozen
    reloaded = _store(root).active.turns[-1]
    assert reloaded.refs.get("outputRefs") == expected
    persisted = json.dumps(_store(root).journal.load())
    assert "SYNTHETIC_PRIVATE" not in persisted and "MUTATED_AFTER_WRITE" not in persisted
    assert "privateGraph" not in reloaded.refs and reloaded.refs.get("arbitraryRows") == []
    assert reloaded.refs["objectIds"] == ["o1"] and reloaded.refs["resourceIds"] == ["image-previous-A"]


@pytest.mark.parametrize("changes", [
    {"id": "s2"}, {"run": {"private": "SYNTHETIC_PRIVATE_BAD_RUN"}}, {"frame": True},
    {"epoch": -1}, {"created_at": float("nan")}, {"created_at": float("inf")},
    {"turn_id": True}, {"turn_id": 0},
], ids=["sight-id", "nested-run", "boolean-frame", "negative-epoch", "nan-time", "infinite-time",
        "boolean-turn", "invalid-turn"])
def test_malformed_output_row_cannot_displace_a_valid_qualified_receipt(tmp_path, changes):
    root = tmp_path / "synthetic-sessions"
    store = _store(root)
    store.add_turn("assistant", "chat", "Synthetic malformed metadata boundary.",
                   refs={"outputRefs": [_row("o99", **changes), _row("o2")]})
    assert _store(root).active.turns[-1].refs.get("outputRefs") == [_row("o2")]


def test_unknown_run_is_retained_as_unknown_without_granting_current_identity(tmp_path):
    brain, _, sight, _, second, store, root = _persistent_world(tmp_path)
    store.add_turn("assistant", "chat", "Synthetic historical output with absent run proof.",
                   refs={"outputRefs": [_row(second.id, run=None)]})
    sight.view = None
    conversation = _attach(brain, _store(root))
    assert conversation.output_refs() == [_row(second.id, run=None)]
    row = getattr(brain.moment(), "awareness", {}).get("outputRefs", [])[0]
    assert row["run"] is None and row["available"] is False


@pytest.mark.parametrize("history", ["legacy-object-ids", "unknown-current-output"])
def test_legacy_or_unknown_object_id_never_fabricates_a_historical_output_origin(tmp_path, history):
    brain, _, sight, _, second, store, root = _persistent_world(tmp_path)
    if history == "legacy-object-ids":
        # A legacy journal entry names an ID but has no qualified completion origin.
        store.add_turn("assistant", "chat", "Synthetic legacy outline receipt.",
                       refs={"objectIds": [second.id], "resourceIds": ["image-previous-A"]})
    else:
        brain.moment()  # bind the real source lookup, then offer an ID absent from that source
        brain.conversation.add("assistant", "chat", "Synthetic unknown output receipt.", objectIds=["o999"])
    sight.view = None
    conversation = _attach(brain, _store(root))
    assert conversation.output_refs() == []
    assert getattr(brain.moment(), "awareness", {}).get("outputRefs") == []
    if history == "legacy-object-ids":
        assert "image-previous-A" in conversation.text()


def test_ordinary_scalar_ref_lists_keep_their_existing_privacy_boundary(tmp_path):
    root = tmp_path / "synthetic-sessions"
    store = _store(root)
    turn = store.add_turn("note", "system", "Synthetic scalar compatibility boundary.", refs={
        "objectIds": ["o1", {"id": "o2"}, ["o3"], None],
        "resourceIds": ("image-previous-A", {"secret": "SYNTHETIC_PRIVATE_RESOURCE"}),
        "mixed": ["value", 3, 2.5, True, None, {"nested": "SYNTHETIC_PRIVATE_MIXED"}],
        "scalar": "unchanged", "empty": None, "privateGraph": {"secret": "SYNTHETIC_PRIVATE_GRAPH"},
    })
    expected = {"objectIds": ["o1"], "resourceIds": ["image-previous-A"],
                "mixed": ["value", 3, 2.5, True], "scalar": "unchanged", "empty": None}
    assert turn.refs == expected and _store(root).active.turns[-1].refs == expected
    assert "SYNTHETIC_PRIVATE" not in json.dumps(store.journal.load())
