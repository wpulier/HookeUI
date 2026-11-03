"""Driving Hooke ends in receipts from Hooke's own records (Will, 2026-11-03: "activating capabilities, creating and
organizing tabs, activating researchers, workflows"): turning a capability on is read back from its card, a model's
download from the queue, the Models page from the studio's word; and think reaches every one of them."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.voice.hands import intent_of
from orchestrator.voice.partner_tools import PARTNER_TOOLS, THINK_TOOLS
from orchestrator.voice.records import Records

DRIVING = ("turn_on", "get_model", "open_models", "models", "experiment", "experiment_status", "write_document",
           "tabs", "present", "start_researcher", "research_status", "researcher_note", "workflow", "run_capability")


def test_think_reaches_every_driving_tool():
    assert [t for t in DRIVING if t not in set(PARTNER_TOOLS) | set(THINK_TOOLS)] == []


@pytest.mark.parametrize("tool, kind, ui", [("turn_on", "setup", False), ("get_model", "setup", False),
                                             ("open_models", "open", True)])
def test_driving_intents_are_checked_against_records(tool, kind, ui):
    intent = intent_of(tool, {"job": "Find every cell"})
    assert (intent.kind, intent.ui, intent.check) == (kind, ui, "record")


def _shelf(card: dict) -> dict:
    return {"setup": {"shelf": {"cards": [{"id": "cells", "title": "Find every cell", "word": "", **card}],
                                "behind": []}}}


def _receipt(records: Records, tool: str, args: dict, result: dict) -> dict:
    intent = intent_of(tool, args)
    intent.record = records.before(intent)
    return asyncio.run(records.check(intent, result, None, {}))


@pytest.mark.parametrize("card, expected", [
    ({"colour": "green"}, "verified"),
    ({"colour": "yellow", "turning": {"state": "running", "says": "downloading Cellpose-SAM"}}, "running"),
    ({"colour": "red", "turning": {"state": "waiting"}, "word": "Needs your OpenAI key"}, "running"),
    ({"colour": "red", "turning": {"state": "failed", "says": "no room on the disk"}}, "failed"),
    ({"colour": "red"}, "uncertain"),
])
def test_turn_on_is_read_back_from_its_card(card, expected):
    records = Records(None, services=SimpleNamespace(capabilities=lambda: _shelf(card)))
    receipt = _receipt(records, "turn_on", {"job": "find every cell"}, {"ok": True, "summary": "Turning on…"})
    assert receipt["result"] == expected


def test_turn_on_with_no_such_card_is_never_done():
    records = Records(None, services=SimpleNamespace(capabilities=lambda: _shelf({"colour": "green"})))
    assert _receipt(records, "turn_on", {"job": "Talk with you"}, {"ok": True})["result"] == "uncertain"


def test_a_download_is_read_back_from_the_queue():
    rows = []
    queue = SimpleNamespace(wire=lambda: [dict(r) for r in rows])
    records = Records(None, services=SimpleNamespace(downloads=queue))
    intent = intent_of("get_model", {"model": "DINOv3"})
    intent.record = records.before(intent)  # nothing queued before the call
    rows.append({"model": "dinov3", "name": "DINOv3 small", "state": "downloading", "says": "Downloading DINOv3 small"})
    receipt = asyncio.run(records.check(intent, {"ok": True}, None, {}))
    assert receipt["result"] == "running" and "DINOv3" in receipt["said"]
    rows[0]["state"] = "done"
    assert asyncio.run(records.check(intent, {"ok": True}, None, {}))["result"] == "verified"
    rows[0].update(state="failed", says="its licence must be accepted first")
    assert asyncio.run(records.check(intent, {"ok": True}, None, {}))["result"] == "failed"


def test_a_download_nothing_shows_is_not_claimed():
    records = Records(None, services=SimpleNamespace(downloads=SimpleNamespace(wire=lambda: [])))
    assert _receipt(records, "get_model", {"model": "DINOv3"}, {"ok": True})["result"] == "uncertain"


def test_the_models_page_is_done_on_the_studios_word():
    records = Records(None, services=SimpleNamespace())
    receipt = _receipt(records, "open_models", {}, {"ok": True, "summary": "the studio shows the Models page"})
    assert receipt["result"] == "verified"
    refused = _receipt(records, "open_models", {}, {"ok": False, "error": "the studio did not say it shows it"})
    assert refused["result"] == "failed"
