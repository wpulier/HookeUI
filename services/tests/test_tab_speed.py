"""bench/tab_speed.py: Tab actions' timings, read from the `tab-action` lines Hooke logs, against their targets."""
import json

from bench.tab_speed import main, percentile, read, summarise


def test_percentiles_by_nearest_rank():
    assert percentile([], 50) is None
    assert percentile([5.0], 95) == 5.0
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10


def test_rows_per_action_and_way_against_their_targets(tmp_path):
    log = tmp_path / "interactions.jsonl"
    lines = [{"kind": "tab-action", "at": "2026-11-03T10:00:00", "action": "switch", "show": True, "outcome": "ok",
              "studioMs": 40, "verifyMs": 60, "totalMs": ms} for ms in (100, 120, 140)]
    lines += [{"kind": "tab-action", "at": "2026-11-03T10:01:00", "action": "compose", "show": False, "outcome": "ok",
               "studioMs": 300, "totalMs": 1400},
              {"kind": "tab-action", "at": "2026-11-03T10:02:00", "action": "compose", "show": False,
               "outcome": "failed", "totalMs": 50},
              {"kind": "turn", "at": "2026-11-03T10:03:00"},
              {"kind": "tab-action", "at": "2026-11-02T09:00:00", "action": "switch", "show": True, "outcome": "ok",
               "totalMs": 9000}]
    log.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    rows = {(r["action"], r["shown"]): r for r in summarise(read([log], since="2026-11-03"))}
    switch, compose = rows[("switch", True)], rows[("compose", False)]
    assert switch["n"] == 3 and switch["totalMs"]["p50"] == 120 and switch["meets"] is True and switch["target"] == 150
    assert compose["n"] == 2 and compose["failed"] == 1 and compose["totalMs"]["p50"] == 1400
    assert compose["meets"] is False and compose["target"] == 1000
    assert main(["--log", str(log), "--since", "2026-11-03"]) == 0
    assert main(["--log", str(tmp_path / "missing.jsonl")]) == 1
