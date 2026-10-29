"""Knowing (voice/knowing.py, spec 4.3): what the partner knows of what happened in Hooke comes from Activity, only the
open Project's, with the partner's own receipts; the user's own actions (a click, a command, a researcher started from
the panel) reach it as silent context with their ids, which also feed what "that one" can mean; and the resources tool
reads one part of the Project's view in full."""
import asyncio
import os
from types import SimpleNamespace

from orchestrator.activity import Activity
from orchestrator.voice import knowing


def hub_with_activity(sid="p1"):
    day = SimpleNamespace(sid=sid)
    dialogue = SimpleNamespace(here=lambda: None, show=lambda line, **k: None, day=day)
    hub = SimpleNamespace()
    hub.activities = Activity(hub, dialogue, clock=lambda: 100.0)
    return hub, day


def test_the_activity_tool_reads_only_the_open_projects_lines_with_the_partners_receipts():
    hub, day = hub_with_activity("p1")
    hub.activities.add("command", "You selected stoma")
    hub.activities.add("command", "Assistant: opening the table: not known whether it happened", state="failed",
                       details={"ids": {"receipt": "uncertain"}})
    day.sid = "p2"
    hub.activities.add("command", "You cleared the outlines")
    read = knowing.read(hub, {}, project="p1", clock=lambda: 101.0)
    assert [line["said"] for line in read["lines"]] == ["Assistant: opening the table: not known whether it happened",
                                                        "You selected stoma"]
    assert read["lines"][0]["receipt"] == "uncertain"
    assert [line["said"] for line in knowing.read(hub, {}, project="p2")["lines"]] == ["You cleared the outlines"]


def test_the_users_own_actions_are_on_by_default_and_the_switch_still_turns_them_off(monkeypatch):
    monkeypatch.delenv("MICROMIND_PARTNER_USER_EVENTS", raising=False)
    assert knowing.UserActions.enabled()
    monkeypatch.setenv("MICROMIND_PARTNER_USER_EVENTS", "0")
    assert not knowing.UserActions.enabled()
    assert "MICROMIND_PARTNER_USER_EVENTS" in os.environ


def test_the_users_own_actions_carry_their_ids_feed_the_pointing_history_and_include_researchers_they_started():
    async def go():
        knowing.COALESCE_S, was = 0.01, knowing.COALESCE_S
        hub, _ = hub_with_activity("p1")
        told, pointed = [], []
        mine = SimpleNamespace(rid="r4", n=4, origin={"channel": "chat"})
        partners = SimpleNamespace(rid="r5", n=5, origin={"channel": "voice"})

        async def tell(text):
            told.append(text)
        actions = knowing.UserActions(tell, sink=hub.activities, point=pointed.append,
                                      researcher={"r4": mine, "r5": partners}.get, project=lambda: "p1")
        hub.activities.listeners.append(actions.line)
        hub.activities.add("click", "You clicked: outlined by EdgeTAM", details={"ids": {"objectId": "o7"}})
        hub.activities.add("command", "You opened a window", details={"ids": {"command": "present", "what": "w-12"}})
        hub.activities.add("command", "Assistant: highlighting: done")  # the partner's own: not the user's
        hub.activities.add("research", "Researcher 4 started: stomata density", state="running", rid="r4")
        hub.activities.add("research", "Researcher 5 started: the table", state="running", rid="r5")
        await asyncio.sleep(0.1)
        assert len(told) == 1, "one note for a burst"
        note = told[0]
        assert "[outline:o7]" in note and "[window:w-12]" in note and "researcher:4" in note
        assert "Researcher 5" not in note and "Assistant" not in note
        assert ["outline:o7"] in pointed and ["window:w-12"] in pointed and ["researcher:4"] in pointed
        knowing.COALESCE_S = was
    asyncio.run(go())


def test_another_projects_lines_are_not_told_as_the_users_actions_here():
    async def go():
        knowing.COALESCE_S, was = 0.01, knowing.COALESCE_S
        hub, day = hub_with_activity("p1")
        told = []

        async def tell(text):
            told.append(text)
        actions = knowing.UserActions(tell, sink=hub.activities, project=lambda: "p2")
        hub.activities.listeners.append(actions.line)
        hub.activities.add("command", "You selected stoma")
        await asyncio.sleep(0.05)
        assert told == []
        knowing.COALESCE_S = was
    asyncio.run(go())


def test_the_resources_tool_reads_one_part_of_the_project_in_full(tmp_path):
    from orchestrator.brain.toolbox.resources import SECTIONS, section_text

    state = {"project": {"session": "p1", "name": ""},
             "researchers": {"total": 2, "items": [
                 {"name": "Researcher 1", "ref": {"id": "r1"}, "status": "running", "group": "running", "about": "",
                  "step": "", "finding": ""},
                 {"name": "Researcher 2", "ref": {"id": "r2"}, "status": "done", "group": "finished", "about": "",
                  "step": "", "finding": ""}]},
             "tabs": {"items": [{"id": "t1", "title": "Main", "windows": 3}]},
             "ui": {"state": "reported", "ageMs": 0, "activeTab": "t1", "front": None},
             "unreadable": []}
    text = section_text(state, "researchers")
    assert "Researcher 1 (r1): running" in text and "Researcher 2 (r2): done" in text
    assert "Main (t1): 3 windows" in section_text(state, "tabs")
    assert set(SECTIONS) >= {"data", "researchers", "studies", "tabs", "stage", "models"}
