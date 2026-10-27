import json

from bench.arena_report import load, recommend, render, score


def vote(arms, winner, ms, kind="outline", by=None, note="", policy="route"):
    v = {"at": "2026-10-04T10:00:00", "trial": "t", "kind": kind, "input": {}, "frame": 1, "arms": arms,
         "ms": ms, "errors": {}, "winner": winner, "winnerOption": arms.get(winner), "note": note}
    if policy:
        v["policy"] = policy
    if by:
        v["by"] = by
    return v


def test_report_scores_wins_and_prefers_the_faster_of_near_ties(tmp_path):
    votes = []
    for i in range(4):
        # EdgeTAM and seeded SAM 3.1 win equally; EdgeTAM is much faster.
        winner = "A" if i % 2 else "B"
        votes.append(vote({"A": "edgetam", "B": "edgetam>sam3.1-fal", "C": "sam3.1-fal"}, winner,
                          {"A": 60, "B": 900, "C": 600}, note="whole stoma" if i == 0 else ""))
    votes.append(vote({"A": "edgetam", "B": "sam3.1-fal"}, "B", {"A": 60, "B": 600}, by="harness"))
    path = tmp_path / "votes.jsonl"
    path.write_text("\n".join(json.dumps(v) for v in votes))
    loaded = load(path)
    assert len(loaded) == 4  # the harness vote is skipped
    rows = score(loaded)["outline"]  # one row per option and policy (ADR 0011)
    assert rows["edgetam@route"]["wins"] == 2 and rows["sam3.1-fal@route"]["wins"] == 0
    assert recommend({k.split("@")[0]: r for k, r in rows.items()}) == "edgetam"
    text = render(loaded)
    assert "Recommended default: **EdgeTAM**" in text
    assert "whole stoma" in text


def test_votes_from_before_policies_are_kept_apart_and_recommend_nothing(tmp_path):
    """Votes recorded before ADR 0011 ran each arm with its fallbacks unrecorded: they are listed as
    "route-legacy", never pooled with new votes, and defaults are recommended from "route" votes only."""
    legacy = [vote({"A": "edgetam", "B": "sam3.1-fal"}, "A", {"A": 60, "B": 600}, policy=None) for _ in range(5)]
    rows = score(legacy)["outline"]
    assert set(rows) == {"edgetam@route-legacy", "sam3.1-fal@route-legacy"}
    assert "Not enough votes for a recommendation yet" in render(legacy)


def test_report_without_votes():
    assert "No votes yet" in render([])
