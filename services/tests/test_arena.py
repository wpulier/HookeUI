import asyncio
import json
import random

import pytest

from orchestrator import arena as arena_mod
from orchestrator import tools
from orchestrator.arena import Arena
from orchestrator.options import OUTLINE
from orchestrator.scene import Scene, SceneObject
from tests.fakes import Hub as BaseHub


def FakeHub():
    return BaseHub(frame=False)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    async def fake_outline_at(hub, frame_id, x, y, *, source, model, trial, arm, **kw):
        if model == "sam2.1-tiny":
            raise tools.ToolError("nothing distinct to outline there")
        obj = SceneObject(id=hub.scene.new_id(), kind="highlight", polygons=[[[x, y], [x + 9, y], [x, y + 9]]],
                          anchor_frame=frame_id, anchor_epoch=0, anchor_offset=(0, 0), source=source,
                          note=f"{model} outline", trial=trial, arm=arm)
        return hub.scene.add(obj)

    monkeypatch.setattr(tools, "outline_at", fake_outline_at)
    hub = FakeHub()
    return hub, Arena(hub, agent=None, rng=random.Random(3), votes_log=tmp_path / "votes.jsonl"), tmp_path / "votes.jsonl"


def run(coro):
    return asyncio.run(coro)


def test_trial_is_blind_until_the_vote(setup):
    hub, arena, _ = setup
    arena.configure({"enabled": True})
    trial = run(arena.outline_trial(7, 100, 120))
    names = set(OUTLINE) | {o.label for o in OUTLINE.values()}
    for message in hub.sent + [{"scene": hub.scene.to_wire()}]:
        text = json.dumps(message)
        assert not any(name in text for name in names), text
    assert sorted(trial.arms) == ["A", "B", "C"]
    failed = [a for a in trial.arms.values() if a.error]
    assert len(failed) == 1 and failed[0].option == "sam2.1-tiny"


def test_letters_are_shuffled_between_trials(setup):
    _, arena, _ = setup
    orders = set()
    for _ in range(12):
        trial = run(arena.outline_trial(7, 100, 120))
        orders.add(tuple(a.option for a in trial.arms.values()))
    assert len(orders) > 1


def test_vote_keeps_only_the_winner_and_logs_it(setup):
    hub, arena, votes = setup
    trial = run(arena.outline_trial(7, 100, 120))
    winner = next(l for l, a in trial.arms.items() if a.option == "edgetam")
    result = arena.vote(trial.id, winner, "  whole stoma  ")
    assert result["reveal"][winner]["option"] == "edgetam"
    remaining = list(hub.scene.objects.values())
    assert len(remaining) == 1 and remaining[0].trial is None and remaining[0].note == "edgetam outline"
    record = json.loads(votes.read_text().splitlines()[-1])
    assert record["winnerOption"] == "edgetam" and record["note"] == "whole stoma"
    assert set(record["arms"].values()) == set(arena_mod.DEFAULT_ARMS["outline"])
    assert record["errors"] and set(record) >= {"trial", "kind", "input", "frame", "ms", "winner"}
    with pytest.raises(tools.ToolError):
        arena.vote(trial.id, winner)


def test_none_vote_clears_all_arms(setup):
    hub, arena, _ = setup
    trial = run(arena.outline_trial(7, 100, 120))
    arena.vote(trial.id, "none")
    assert not hub.scene.objects


def test_new_trial_discards_an_unvoted_one(setup):
    hub, arena, _ = setup
    first = run(arena.outline_trial(7, 100, 120))
    run(arena.outline_trial(8, 300, 320))
    assert not any(o.trial == first.id for o in hub.scene.objects.values())


def test_configure_validates_arms(setup):
    _, arena, _ = setup
    assert arena.configure({"outline": ["edgetam"]})  # fewer than two
    assert arena.configure({"ask": ["gemini-3.8-flash/box", "nope/box"]})
    assert not arena.configure({"ask": ["gemini-3.8-flash/box", "gpt-6-luna/marks"]})
    assert arena.arms["ask"] == ["gemini-3.8-flash/box", "gpt-6-luna/marks"]
    with pytest.raises(tools.ToolError):
        arena.vote("t999", "A")

