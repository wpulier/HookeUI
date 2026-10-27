"""The model judges what the words are; code follows its intent and nothing else (brain/intent.py;
the user's direction, 2026-10-06: no keyword lists or word counts deciding for the model)."""
import asyncio

from orchestrator.brain.context import LEAD_TABLE
from orchestrator.brain.intent import allowed, log_override
from tests.test_brain import make

HIGHLIGHT = ("highlight", {"things": ["s1"]})
ZOOM = ("zoom", {"object": "o1"})


def test_the_models_intent_decides_which_actions_run():
    assert allowed([HIGHLIGHT, ZOOM], intent="request") == [HIGHLIGHT, ZOOM]
    assert allowed([HIGHLIGHT, ZOOM], intent="question") == [HIGHLIGHT, ZOOM]  # it chose to point
    for intent in ("statement", "filler", "unfinished", "cancel"):
        assert allowed([HIGHLIGHT, ZOOM], intent=intent) == []
    assert allowed([HIGHLIGHT, ZOOM], intent="request", point_only=True) == [HIGHLIGHT]


def test_while_the_user_leads_only_their_own_request_points():
    assert allowed([HIGHLIGHT, ZOOM], intent="question", user_leads=True) == [ZOOM]
    assert allowed([HIGHLIGHT], intent="request", user_leads=True) == [HIGHLIGHT]


def test_the_main_look_is_told_the_user_leads_and_its_answer_is_what_happens():
    reply = {"answer": "Those are nuclei.", "targets": [{"box_2d": [1, 2, 3, 4], "label": "nucleus"}], "actions": []}
    brain, hub, agent = make([dict(reply)])
    hub.settings.user_leads = True
    result = asyncio.run(brain.turn("what are the dark spots?"))
    assert LEAD_TABLE.strip() in agent.calls[0]["tools"][1]  # told, in its notes
    assert result["objectIds"]  # and what it chose to do is done: no code filter on its words


def test_an_override_is_logged():
    logged = []
    log_override(logged.append, "board-that", picked="o3")
    assert logged == [{"kind": "override", "rule": "board-that", "picked": "o3"}]
    log_override(None, "anything")  # no log: nothing happens
