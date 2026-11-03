"""Projects by voice (brain/toolbox/projects.py): list, rename, tidy, archive and undo, in Will's words, on a real app
with a fresh MICROMIND_HOME (Paths.under) and seeded Projects. Nothing reaches a model: each tool is run as the brain
runs it (Registry.start), and what it changed is read back from the journals, the catalogue and the Project store."""
from __future__ import annotations

import asyncio
import datetime
import json
import time

import pytest

from orchestrator.app import App
from orchestrator.brain.registry import ToolContext
from orchestrator.paths import Paths
from tests.test_project_material import document


def ago(days: int, hour: int = 10) -> float:
    """A time `days` calendar days before today, at `hour` local."""
    date = datetime.date.today() - datetime.timedelta(days=days)
    return time.mktime((date.year, date.month, date.day, hour, 0, 0, 0, 0, -1))


def last_week(weekday: int) -> tuple[str, float]:
    """A day of last calendar week (0 Monday) and a time on it."""
    today = datetime.date.today()
    date = today - datetime.timedelta(days=today.weekday() + 7 - weekday)
    return date.isoformat(), time.mktime((date.year, date.month, date.day, 10, 0, 0, 0, 0, -1))


def write(root, sid, events):
    folder = root / sid
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "journal.jsonl").open("a") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


@pytest.fixture
def seeded(tmp_path):
    """Will's Projects: a leaf one last week, an older leaf one, a guard-cell one, an onion one whose conversation was
    about guard cells, two test runs; the app open in a new Project of today's."""
    paths = Paths.under(tmp_path)
    root = paths.sessions
    leaf_day, leaf_at = last_week(2)
    old = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    projects = {
        "leaf": (leaf_day, [
            {"type": "session-started", "session": leaf_day, "name": "Leaf epidermis", "by": "user", "at": leaf_at},
            {"type": "thread-opened", "thread": "t1", "n": 1, "title": "", "at": leaf_at},
            {"type": "subject", "thread": "t1", "name": "leaf epidermis", "at": leaf_at + 60}]),
        "old-leaf": (old, [
            {"type": "thread-opened", "thread": "t1", "n": 1, "title": "", "at": ago(30)},
            {"type": "subject", "thread": "t1", "name": "leaves", "at": ago(30)}]),
        "guard": (f"{old}.2", [
            {"type": "session-started", "session": f"{old}.2", "name": "Stomata in light", "by": "user", "at": ago(30, 12)},
            {"type": "thread-opened", "thread": "t1", "n": 1, "title": "Aperture", "at": ago(30, 12)},
            {"type": "subject", "thread": "t1", "name": "guard cells", "at": ago(30, 12)}]),
        "onion": (f"{old}.3", [
            {"type": "session-started", "session": f"{old}.3", "name": "Onion skin", "by": "user", "at": ago(30, 14)},
            {"type": "thread-opened", "thread": "t1", "n": 1, "title": "", "at": ago(30, 14)},
            {"type": "summary", "thread": "t1", "text": "Compared onion cells with the guard cells of a leaf.",
             "upto": 4, "at": ago(30, 15)}]),
        "test1": (f"{old}.4", [
            {"type": "session-started", "session": f"{old}.4", "name": "Test run", "by": "user", "at": ago(30, 16)}]),
        "test2": (f"{old}.5", [
            {"type": "session-started", "session": f"{old}.5", "name": "Test run 2", "by": "user", "at": ago(30, 17)}]),
    }
    for sid, events in projects.values():
        write(root, sid, events)
    built = App.build("http://127.0.0.1:1", paths, use_gpu=False)
    built.sent = []
    built.hub.broadcast = lambda payload: built.sent.append(json.loads(json.dumps(payload, default=str)))
    built.dialogue.publish = built.hub.broadcast
    built.day.new("", by="user")
    return built, {k: sid for k, (sid, _) in projects.items()}


def call(built, name, args, said="", channel="chat"):
    async def go():
        run = built.brain.registry.start(name, args, ToolContext(channel, 1, None, said))
        await run.task
        return run
    return asyncio.run(go())


def test_what_projects_do_i_have_on_guard_cells(seeded):
    built, ids = seeded
    run = call(built, "list_projects", {"about": "guard cells"}, "what Projects do I have on guard cells")
    assert run.ok, run.summary
    text = run.summary
    assert text.index('"Stomata in light"') < text.index('"Onion skin"')  # what was seen, before what was only said
    assert "matched cells, guard in what was seen in it" in text and "in what was said in it" in text
    assert "Leaf epidermis" not in text and "One clear match" in text


def test_open_the_leaf_one_from_last_week(seeded):
    built, ids = seeded
    run = call(built, "switch_session", {"session": "the leaf one from last week"}, "open the leaf one from last week")
    assert run.ok, run.summary
    assert built.day.sid == ids["leaf"] and 'opened the session "Leaf epidermis"' in run.summary
    # Without the time, two leaf Projects are meant: nothing is opened, both are said, best first.
    built.day.switch_session(ids["guard"])
    unsure = call(built, "switch_session", {"session": "the leaf one"}, "open the leaf one")
    assert built.day.sid == ids["guard"] and "nothing was opened" in unsure.summary
    assert unsure.summary.index("Leaf epidermis") < unsure.summary.index(ids["old-leaf"])


def test_the_list_says_the_time_it_read_and_leaves_archived_ones_out(seeded):
    built, ids = seeded
    run = call(built, "list_projects", {"about": "leaf ones from last week"})
    assert "time last week (" in run.summary and 'One clear match: "Leaf epidermis"' in run.summary
    whole = call(built, "list_projects", {})
    assert whole.ok and all(f"id for tools {sid}" in whole.summary for sid in ids.values())


def test_rename_a_project_and_undo_it(seeded):
    built, ids = seeded
    run = call(built, "rename_project", {"project": "the leaf one from last week", "name": "Leaf peel, stained"},
               "rename the leaf one from last week to Leaf peel, stained")
    assert run.ok, run.summary
    assert built.day.catalogue.get(ids["leaf"]).name == "Leaf peel, stained"
    assert 'renamed "Leaf epidermis" to "Leaf peel, stained"' in run.summary
    vague = call(built, "rename_project", {"project": "the leaf one", "name": "x"}, "rename the leaf one")
    assert not vague.ok and "several Projects may be meant" in vague.summary
    undo = call(built, "undo_project_change", {}, "undo that")
    assert undo.ok, undo.summary
    info = built.day.catalogue.get(ids["leaf"])
    assert info.name == "Leaf epidermis" and info.named
    again = call(built, "undo_project_change", {}, "undo that")
    assert not again.ok and "no change Hooke made to Projects is left to undo" in again.summary


def test_renaming_this_untitled_project_is_undone_to_no_name(seeded):
    built, _ = seeded
    assert built.day.name == ""
    assert call(built, "rename_project", {"name": "Stomata, day 2"}, "call this Project Stomata, day 2").ok
    assert built.day.name == "Stomata, day 2" and built.day.named
    assert call(built, "undo_project_change", {}, "undo").ok
    assert built.day.name == "" and not built.day.named and built.day.catalogue.get(built.day.sid).name == ""


def test_archive_the_old_test_ones_and_undo(seeded):
    built, ids = seeded
    said = "archive the old test ones"
    refused = call(built, "archive_project", {"projects": [ids["test1"]], "asked_with": "please archive everything"},
                   "what Projects do I have?")
    assert not refused.ok and built.day.catalogue.get(ids["test1"]).archived is False
    run = call(built, "archive_project", {"projects": [ids["test1"], "Test run 2"], "asked_with": said}, said)
    assert run.ok, run.summary
    assert 'archived 2 Projects: "Test run", "Test run 2"' in run.summary
    cat = built.day.catalogue
    assert cat.get(ids["test1"]).archived and cat.get(ids["test2"]).archived
    assert (built.paths.sessions / ids["test1"] / "journal.jsonl").is_file()  # nothing moved or deleted
    listed = call(built, "list_projects", {})
    assert f"id for tools {ids['test1']}" not in listed.summary and "2 archived not listed" in listed.summary
    found = call(built, "list_projects", {"about": "test run"})
    assert f"id for tools {ids['test1']}" in found.summary and "archived" in found.summary
    wire = {r["id"]: r for r in built.sessions_wire()["sessions"]}
    assert wire[ids["test1"]]["archived"] is True and "archived" not in wire[ids["leaf"]]
    undo = call(built, "undo_project_change", {}, "unarchive them")
    assert undo.ok, undo.summary
    assert not cat.get(ids["test1"]).archived and not cat.get(ids["test2"]).archived


def tidy_project(built):
    """The open Project as one left messy: two untitled conversations, an empty Tab beside Main, an imported file."""
    day = built.day
    day.add_turn("user", "chat", "outline the guard cells")
    day.set_summary("t1", "Outlined guard cells and counted 14 stomata.", 1)
    day.open_thread("")
    day.add_turn("user", "chat", "compare the aperture in light and dark")
    layout = {"schema": 2, "coordinates": "stage", "views": [
        {"id": "main", "title": "Main", "specimen": {"visible": True, "rect": None}, "items": [], "notes": []},
        {"id": "v2", "title": "Tab 2", "specimen": {"visible": False, "rect": None}, "items": [], "notes": []}]}
    day.workspace_layout = {"type": "workspace-layout", "layout": layout, "revision": 1}
    return built.hub.resources.projects.create(day.sid, document("research-note", {}, "leaf-light.tif"))


def test_tidy_this_project_proposes_then_applies_on_yes_and_undoes(seeded):
    built, _ = seeded
    original = tidy_project(built)
    facts = call(built, "organize_project", {}, "tidy this Project")
    assert facts.ok, facts.summary
    assert ('t1 "Outline the guard cells" (the app\'s title, from its first request): 1 requests' in facts.summary
            and "counted 14 stomata" in facts.summary)
    assert 'v2 "Tab 2" (workspace): 0 windows' in facts.summary and f'{original.id} "leaf-light.tif"' in facts.summary
    plan = {"name": "Stomata in light and dark",
            "conversations": [{"id": "t1", "title": "Guard cell count"}, {"id": "t2", "title": "Aperture"},
                              {"id": "t9", "title": "Nothing"}],
            "groups": [{"item": original.id, "group": "Light series"}],
            "closeTabs": ["v2", "main"]}
    proposed = call(built, "organize_project", {"plan": plan}, "tidy this Project")
    assert proposed.ok, proposed.summary
    assert proposed.summary.startswith("Proposal o1 ") and "nothing changed yet" in proposed.summary
    assert "no conversation t9" in proposed.summary and "Main stays open" in proposed.summary
    titles = [built.day.threads[t].title for t in ("t1", "t2")]
    assert built.day.name == "" and titles == ["Outline the guard cells", "Compare the aperture in light and dark"]

    unsaid = call(built, "apply_organization", {"confirms": "o1", "asked_with": "sure go ahead"}, "what is in Tab 2?")
    assert not unsaid.ok and built.day.threads["t1"].title == titles[0]  # a yes the user never said applies nothing
    assert '[5] close the Tab "Tab 2"' in proposed.summary  # parts by number: name, two titles, a group, the Tab
    done = call(built, "apply_organization", {"confirms": "o1", "asked_with": "yes, but don't close the Tab",
                                              "skip": ["5"]}, "yes, but don't close the Tab")
    assert done.ok, done.summary
    assert built.day.name == "Stomata in light and dark"
    assert [built.day.threads[t].title for t in ("t1", "t2")] == ["Guard cell count", "Aperture"]
    from orchestrator.project_material_edit import membership_id
    from orchestrator.scientific_comparisons import identity

    store = built.hub.resources.projects
    group = store.read(membership_id(identity(original)), built.day.sid)
    assert group.document["state"]["materialMembership"]["role"] == "Light series"
    assert group.document["state"]["materialMembership"]["by"] == "agent:chat"
    assert "closed the Tab" not in done.summary

    undo = call(built, "undo_project_change", {}, "undo that")
    assert undo.ok, undo.summary
    assert built.day.name == "" and [built.day.threads[t].title for t in ("t1", "t2")] == titles
    assert built.day.threads["t1"].auto_title  # the app's own title again, so a later request may still rename it
    reverted = store.read(membership_id(identity(original)), built.day.sid)
    assert reverted.revision == group.revision + 1 and reverted.document["state"]["materialMembership"]["role"] == ""
    assert store.read(original.id, built.day.sid).digest == original.digest  # the material itself never changes


def test_a_proposal_is_applied_only_where_it_was_made(seeded):
    built, ids = seeded
    tidy_project(built)
    assert call(built, "organize_project", {"plan": {"conversations": [{"id": "t1", "title": "Count"}]}}).ok
    built.day.switch_session(ids["leaf"])
    moved = call(built, "apply_organization", {"confirms": "o1", "asked_with": "yes"}, "yes")
    assert not moved.ok and "another Project" in moved.summary


def test_a_tab_close_that_no_studio_did_is_said_not_done(seeded):
    built, _ = seeded
    tidy_project(built)
    assert call(built, "organize_project", {"plan": {"conversations": [{"id": "t1", "title": "Count"}],
                                                     "closeTabs": ["v2"]}}).ok
    done = call(built, "apply_organization", {"confirms": "o1", "asked_with": "yes go ahead"}, "yes go ahead")
    assert done.ok, done.summary
    assert 'renamed the conversation “Outline the guard cells” to “Count”' in done.summary and " t1" not in done.summary
    assert 'Not done: Close the Tab “Tab 2” (empty): not done (' in done.summary
    assert "closed the Tab" not in done.summary and "INVALID" not in done.summary


def test_the_journey_plans_seeded_home_answers_its_steps(tmp_path):
    """bench/voice_journeys/plans/will-projects.json: its `projects`, written into a fresh home as the stack does,
    are found the way its steps ask."""
    from bench.voice_journeys import stack
    from orchestrator.day.catalogue import Catalogue

    plan = stack.load_plan("will-projects")
    written = stack.seed_projects(plan, tmp_path / "sessions")
    assert len(written) == len(plan["projects"]) == len(set(written))
    cat = Catalogue(tmp_path / "sessions")
    assert cat.rank("the leaf one from last week").sure.info.name == "Leaf epidermis"
    guard = cat.rank("what Projects do I have on guard cells")
    assert [m.info.name for m in guard.matches] == ["Stomata in light", "Onion skin"]
    assert {i.name for i in cat.find("the test ones")} == {"Test run", "Test run 2"}


def cards(built, kind):
    """The newest wire of each Project card of `kind` the studios were sent, by id."""
    out = {}
    for message in built.sent:
        if message.get("type") == "project-card" and message["card"]["kind"] == kind:
            out[message["card"]["id"]] = message["card"]
    return out


def press(built, card, action):
    from orchestrator.commands import handle
    from orchestrator.hub import Client

    async def go():
        client = Client(None)
        await handle(built, client, {"type": "project-card", "id": card, "action": action, "requestId": "q1"})
        return json.loads(client.control.get_nowait())
    return asyncio.run(go())


def test_the_tidy_is_shown_as_a_card_and_its_do_it_is_the_yes(seeded):
    built, _ = seeded
    tidy_project(built)
    assert call(built, "organize_project", {"plan": {"name": "Stomata in light and dark",
                                                     "conversations": [{"id": "t1", "title": "Guard cell count"}]}}).ok
    [card] = cards(built, "tidy").values()
    assert card["status"] == "proposed" and card["title"].startswith("Tidy “")
    assert card["lines"] == ["Name the Project “Stomata in light and dark”",
                             "Rename the conversation “Outline the guard cells” to “Guard cell count”"]
    assert [a["label"] for a in card["actions"]] == ["Do it", "Not now"] and card["session"] == built.day.sid
    assert built.project_cards_wire()[0]["cards"][0]["id"] == card["id"]  # a studio that connects is sent it
    reply = press(built, card["id"], "apply")
    assert reply["ok"] is True, reply
    assert built.day.name == "Stomata in light and dark" and built.day.threads["t1"].title == "Guard cell count"
    done = cards(built, "tidy")[card["id"]]
    assert done["status"] == "done" and [a["id"] for a in done["actions"]] == ["undo"]
    assert press(built, card["id"], "apply")["ok"] is False  # done: its Do it is gone
    assert press(built, card["id"], "undo")["ok"] is True
    assert built.day.name == "" and cards(built, "tidy")[card["id"]]["status"] == "undone"


def test_not_now_drops_the_tidy_and_the_voice_yes_then_finds_nothing(seeded):
    built, _ = seeded
    tidy_project(built)
    assert call(built, "organize_project", {"plan": {"conversations": [{"id": "t1", "title": "Count"}]}}).ok
    [card] = cards(built, "tidy").values()
    assert press(built, card["id"], "dismiss")["ok"] is True
    assert cards(built, "tidy")[card["id"]]["status"] == "declined"
    late = call(built, "apply_organization", {"confirms": "o1", "asked_with": "yes"}, "yes")
    assert not late.ok and built.day.threads["t1"].title == "Outline the guard cells"


def test_the_leaf_one_shows_which_project_on_screen(seeded):
    built, ids = seeded
    unsure = call(built, "switch_session", {"session": "the leaf one"}, "open the leaf one")
    assert "nothing was opened" in unsure.summary and "ask in one short question" in unsure.summary
    [card] = cards(built, "choose").values()
    assert card["title"] == "Which Project?" and card["actions"] == []
    assert [c["session"] for c in card["choices"]] == [ids["leaf"], ids["old-leaf"]]
    assert card["choices"][0]["name"] == "Leaf epidermis" and "leaf epidermis" in card["choices"][0]["detail"]
    sure = call(built, "switch_session", {"session": "the leaf one from last week"}, "open the leaf one from last week")
    assert sure.ok and built.day.sid == ids["leaf"] and len(cards(built, "choose")) == 1  # a sure one asks nothing


def test_the_open_project_is_not_found_by_the_question_being_asked(seeded):
    """Live, 3 Nov (typed on a second stack): the open Project's conversation was titled from the very question, so
    "what Projects do I have on guard cells" listed it."""
    built, ids = seeded
    built.day.add_turn("user", "chat", "What Projects do I have on guard cells?")
    assert "guard" in built.day.active.title.lower()  # titled from the question itself
    run = call(built, "list_projects", {"about": "guard cells"}, "What Projects do I have on guard cells?")
    assert f"id for tools {built.day.sid}" not in run.summary and 'One clear match: "Stomata in light"' in run.summary


def test_a_project_name_the_model_carried_over_is_refused_when_the_words_mean_another(seeded):
    """Live, 3 Nov: "Open the leaf one from last week." was called with session "Stomata in light", the Project the
    answer before had named; the words mean Leaf epidermis beyond doubt, so nothing is opened and the model is told."""
    built, ids = seeded
    here = built.day.sid
    wrong = call(built, "switch_session", {"session": "Stomata in light"}, "Open the leaf one from last week.")
    assert wrong.ok and built.day.sid == here and wrong.summary.startswith("nothing was opened")
    assert 'you picked "Stomata in light"' in wrong.summary and 'read as "Leaf epidermis"' in wrong.summary
    [card] = cards(built, "choose").values()  # both readings, for the user to pick
    assert [c["session"] for c in card["choices"]] == [ids["leaf"], ids["guard"]]  # the user's words first
    right = call(built, "switch_session", {"session": "the leaf one from last week"}, "Open the leaf one from last week.")
    assert right.ok and built.day.sid == ids["leaf"]
    # The user's own choice of a name is theirs: "open Stomata in light" opens it.
    assert call(built, "switch_session", {"session": "Stomata in light"}, "open Stomata in light").ok
    assert built.day.sid == ids["guard"]


def test_a_name_the_model_chose_for_words_that_mean_several_asks_which(seeded):
    """Live, 3 Nov (the studio's chat): "Open the leaf one." was called with session "Leaf epidermis" and opened it,
    though two leaf Projects matched the words: nothing is opened and both are on the card."""
    built, ids = seeded
    here = built.day.sid
    run = call(built, "switch_session", {"session": "Leaf epidermis"}, "Open the leaf one.")
    assert run.ok and built.day.sid == here and run.summary.startswith("nothing was opened")
    [card] = cards(built, "choose").values()
    assert [c["session"] for c in card["choices"]] == [ids["leaf"], ids["old-leaf"]]
    assert call(built, "switch_session", {"session": "Leaf epidermis"}, "open Leaf epidermis").ok
    assert built.day.sid == ids["leaf"]


def test_a_plan_said_in_words_is_refused_with_its_shape(seeded):
    """Live, 3 Nov: the fast model called organize_project with plan "Close empty Tab 2 and Tab 3.": nothing was kept
    and no card shown. Now it is told the shape and calls again."""
    built, _ = seeded
    tidy_project(built)
    words = call(built, "organize_project", {"plan": "Close empty Tab 2 and Tab 3."}, "tidy this Project")
    assert not words.ok and '"closeTabs"' in words.summary and cards(built, "tidy") == {}
    assert call(built, "organize_project", {"plan": {"closeTabs": ["v2"]}}, "tidy this Project").ok
    assert len(cards(built, "tidy")) == 1


def test_words_meaning_the_open_project_never_open_another(seeded):
    """Live, 3 Nov (voice): in Leaf epidermis already, "open the leaf one from last week" was called with "Stomata in
    light" and opened it. Now nothing is opened and the model is told the words mean the Project open now."""
    built, ids = seeded
    built.day.switch_session(ids["leaf"])
    wrong = call(built, "switch_session", {"session": "Stomata in light"}, "Open the leaf one from last week.")
    assert wrong.ok and built.day.sid == ids["leaf"] and wrong.summary.startswith("nothing was opened")
    assert 'read as "Leaf epidermis"' in wrong.summary


def test_a_proposed_tidy_is_noted_for_the_next_turn(seeded):
    """Live, 3 Nov (voice): the next turn reads the conversation, not the tool's result; the note says the yes is
    apply_organization."""
    built, _ = seeded
    tidy_project(built)
    assert call(built, "organize_project", {"plan": {"closeTabs": ["v2"]}}, "tidy this Project").ok
    notes = [t.text for t in built.day.active.turns if t.role == "note"]
    assert any("waits for the user's yes on its card" in n and "apply_organization with confirms o1" in n for n in notes)


def test_a_voice_hand_off_is_read_as_the_words_heard_and_the_voices_reading(seeded):
    """Live, 3 Nov (voice): heard "Open the leave one from last week." with the voice's reading "Open the leaf one from
    last week." on the next line; called with "Stomata in light". Each is read on its own: the reading means Leaf
    epidermis beyond doubt, so nothing is opened."""
    built, ids = seeded
    here = built.day.sid
    handed = ("Open the leave one from last week.\n(The voice partner, who sees the same view, took this to mean: "
              "Open the leaf one from last week.)")
    wrong = call(built, "switch_session", {"session": "Stomata in light"}, handed, channel="voice")
    assert wrong.ok and built.day.sid == here and 'read as "Leaf epidermis"' in wrong.summary
    right = call(built, "switch_session", {"session": "the leaf one from last week"}, handed, channel="voice")
    assert right.ok and built.day.sid == ids["leaf"]


def test_do_it_on_the_card_closes_tabs_through_the_studio_that_pressed_it(seeded):
    """Review of #189 (the blocker): the card's Do it ran without the studio's screen pin, so every Tab close failed
    and the card showed "INVALID_REPORT: ...". Through a connected studio (test_voice_follows_project's), it closes."""
    from orchestrator.commands import COMMANDS
    from orchestrator.workspace_observation import ClientRef
    from tests.test_table_window import CLIENT
    from tests.test_voice_follows_project import LandingStudio

    built, _ = seeded

    async def go():
        studio = ReopeningStudio.of(built)
        studio.tabs = [{"id": "main", "title": "Main"}, {"id": "v2", "title": "Tab 2"}]
        built.accept_workspace(studio, ClientRef(CLIENT["workspaceId"], CLIENT["clientId"], CLIENT["pageEpoch"],
                                                 CLIENT["connectionEpoch"]))
        built.hub.clients.add(studio)
        await studio.send_report()
        tidy_project(built)
        run = built.brain.registry.start("organize_project", {"plan": {
            "closeTabs": ["v2"], "conversations": [{"id": "t1", "title": "Count"}]}}, ToolContext("chat", 1, None, "tidy"))
        await run.task
        assert run.ok, run.summary
        [card] = cards(built, "tidy").values()
        reply = await COMMANDS["project-card"](built, studio, {"type": "project-card", "id": card["id"],
                                                              "action": "apply"})
        assert reply["ok"] is True, reply
        assert [t["id"] for t in studio.tabs] == ["main"]  # the studio closed it
        done = cards(built, "tidy")[card["id"]]
        assert done["status"] == "done" and not any("Not done" in line for line in done["lines"]), done
        assert 'Closed the Tab "Tab 2"' in done["lines"] and built.day.threads["t1"].title == "Count"
        assert done["said"] == "Done · Undo puts it back"
        assert any(m.get("type") == "project-card" and m["card"]["status"] == "applying" for m in built.sent)
        # Undo on the card reopens the Tab quietly (Lane A's tabs reopen, show false) and says Undone once it is back.
        reply = await COMMANDS["project-card"](built, studio, {"type": "project-card", "id": card["id"], "action": "undo"})
        assert reply["ok"] is True, reply
        assert [t["id"] for t in studio.tabs] == ["main", "v2"] and studio.active == "main"  # back, not in front
        reopened = [m for m in studio.messages if m.get("type") == "workspace-tabs"][-1]["action"]
        assert reopened == {"action": "reopen", "tab": "v2", "show": False}
        undone = cards(built, "tidy")[card["id"]]
        assert undone["status"] == "undone" and undone["said"] == "Undone · put back as it was"
        assert "The Tab “Tab 2” is open again (not brought to the front)" in undone["lines"]
        assert built.day.threads["t1"].title == "Outline the guard cells"

    asyncio.run(go())


class ReopeningStudio:
    """The studio of test_voice_follows_project, which also reopens a closed Tab (workspace-views.ts agentCommand)."""

    @staticmethod
    def of(app, reopens=True):
        from tests.test_voice_follows_project import LandingStudio

        class Studio(LandingStudio):
            async def tabs_reply(self, message):
                action = message["action"]
                if action["action"] == "reopen":
                    if reopens and not any(t["id"] == action["tab"] for t in self.tabs):
                        self.tabs.append({"id": action["tab"], "title": "Tab 2"})
                    message = {**message, "action": {"action": "list"}}
                await super().tabs_reply(message)
        return Studio(app)


def connected(built, studio):
    from orchestrator.workspace_observation import ClientRef
    from tests.test_table_window import CLIENT

    built.accept_workspace(studio, ClientRef(CLIENT["workspaceId"], CLIENT["clientId"], CLIENT["pageEpoch"],
                                             CLIENT["connectionEpoch"]))
    built.hub.clients.add(studio)


def test_a_tab_that_does_not_come_back_is_named_and_the_undo_is_partly(seeded):
    from orchestrator.commands import COMMANDS

    built, _ = seeded

    async def go():
        studio = ReopeningStudio.of(built, reopens=False)
        studio.tabs = [{"id": "main", "title": "Main"}, {"id": "v2", "title": "Tab 2"}]
        connected(built, studio)
        await studio.send_report()
        tidy_project(built)
        run = built.brain.registry.start("organize_project", {"plan": {
            "closeTabs": ["v2"], "conversations": [{"id": "t1", "title": "Count"}]}}, ToolContext("chat", 1, None, "tidy"))
        await run.task
        [card] = cards(built, "tidy").values()
        assert (await COMMANDS["project-card"](built, studio, {"type": "project-card", "id": card["id"],
                                                               "action": "apply"}))["ok"] is True
        reply = await COMMANDS["project-card"](built, studio, {"type": "project-card", "id": card["id"], "action": "undo"})
        assert reply["ok"] is True and "Not put back: the Tab “Tab 2” (the Tab was not reopened)" in reply["said"]
        assert "Not back: the Tab “Tab 2” (the Tab was not reopened)" in cards(built, "tidy")[card["id"]]["lines"]
        undone = cards(built, "tidy")[card["id"]]
        assert undone["status"] == "partly" and undone["said"] == "Partly undone"
        assert built.day.threads["t1"].title == "Outline the guard cells"  # the rest is back

    asyncio.run(go())


@pytest.mark.parametrize("reply", ["Absolutely", "Go for it", "Yup", "Yes, no problem", "Sure, I don't mind"])
def test_any_yes_the_model_judges_applies_the_tidy(seeded, reply):
    """Review of #189: a keyword list refused "Absolutely" and read "Yes, no problem" as a partial no. The model judges;
    code only checks the words are the user's, said after the proposal."""
    built, _ = seeded
    tidy_project(built)
    assert call(built, "organize_project", {"plan": {"conversations": [{"id": "t1", "title": "Count"}]}}).ok
    done = call(built, "apply_organization", {"confirms": "o1", "asked_with": reply}, reply)
    assert done.ok, done.summary
    assert built.day.threads["t1"].title == "Count"


def test_a_yes_in_the_request_that_asked_for_the_tidy_applies_nothing(seeded):
    """Review of #189: "Yeah, can you tidy this Project?" proposed and applied in one turn."""
    built, _ = seeded
    tidy_project(built)

    async def go():
        ctx = ToolContext("chat", 1, None, "Yeah, can you tidy this Project?")  # one request: one context
        proposed = built.brain.registry.start("organize_project", {"plan": {
            "conversations": [{"id": "t1", "title": "Count"}]}}, ctx)
        await proposed.task
        applied = built.brain.registry.start("apply_organization", {"confirms": "o1", "asked_with": "Yeah"}, ctx)
        await applied.task
        return proposed, applied

    proposed, applied = asyncio.run(go())
    assert proposed.ok and not applied.ok and "said after it was proposed" in applied.summary
    assert built.day.threads["t1"].title == "Outline the guard cells"
    later = call(built, "apply_organization", {"confirms": "o1", "asked_with": "yes"}, "yes")  # the next turn's yes
    assert later.ok and built.day.threads["t1"].title == "Count"


def test_a_no_declines_the_tidy_and_its_card_says_so(seeded):
    built, _ = seeded
    tidy_project(built)
    assert call(built, "organize_project", {"plan": {"conversations": [{"id": "t1", "title": "Count"}]}}).ok
    no = call(built, "apply_organization", {"confirms": "o1", "asked_with": "no thanks", "decline": True}, "no thanks")
    assert no.ok and "nothing changed" in no.summary
    [card] = cards(built, "tidy").values()
    assert card["status"] == "declined" and card["actions"] == []
    assert built.day.threads["t1"].title == "Outline the guard cells"


def test_undo_by_kind_reverts_that_change_and_results_name_their_change(seeded):
    """Review of #189: "archive the test ones", "rename this", "unarchive the test ones" undid the rename."""
    built, ids = seeded
    archived = call(built, "archive_project", {"projects": [ids["test1"], ids["test2"]], "asked_with": "archive the test ones"},
                    "archive the test ones")
    renamed = call(built, "rename_project", {"name": "Leaf series"}, "rename this Project to Leaf series")
    assert "(change pc" in archived.summary and "(change pc" in renamed.summary
    undo = call(built, "undo_project_change", {"kind": "archive"}, "actually, unarchive the test ones")
    assert undo.ok, undo.summary
    cat = built.day.catalogue
    assert not cat.get(ids["test1"]).archived and not cat.get(ids["test2"]).archived
    assert built.day.name == "Leaf series"  # the rename stays
    by_id = call(built, "undo_project_change", {"change": renamed.summary.split("(change ")[1].split()[0]}, "undo the rename")
    assert by_id.ok and built.day.name == ""


def test_undo_of_a_change_from_another_conversation_asks_first(seeded):
    built, ids = seeded
    call(built, "rename_project", {"project": ids["guard"], "name": "Guard cells"}, "rename the guard cell one")
    built.day.open_thread("Another topic")  # a later conversation

    async def ask(args):
        ctx = ToolContext("chat", 1, None, "undo that", asked=built.day.here())
        run = built.brain.registry.start("undo_project_change", args, ctx)
        await run.task
        return run

    asked = asyncio.run(ask({}))
    assert asked.ok and asked.summary.startswith("nothing was undone yet")
    assert built.day.catalogue.get(ids["guard"]).name == "Guard cells"
    change = asked.summary.split("undo_project_change with change ")[1].strip()
    assert asyncio.run(ask({"change": change})).ok and built.day.catalogue.get(ids["guard"]).name == "Stomata in light"


def test_the_word_check_asks_and_logs_its_override(seeded):
    built, ids = seeded
    logged = []
    built.brain.agent.log = lambda row: logged.append(row)
    run = call(built, "switch_session", {"session": "Stomata in light"}, "Open the leaf one from last week.")
    assert run.ok and run.summary.startswith("nothing was opened")
    [override] = [row for row in logged if row.get("kind") == "override"]
    assert override["rule"] == "switch-session-words" and override["picked"] == ids["guard"]
    assert override["read"] == [ids["leaf"]]


def test_the_other_leaf_one_is_not_the_one_open(seeded):
    """Review of #189: "Open the other leaf one." from Leaf epidermis was refused as meaning Leaf epidermis."""
    built, ids = seeded
    built.day.switch_session(ids["leaf"])
    run = call(built, "switch_session", {"session": "the other leaf one"}, "Open the other leaf one.")
    assert run.ok and built.day.sid == ids["old-leaf"], run.summary


def test_a_which_project_card_says_which_was_opened(seeded):
    built, ids = seeded
    assert call(built, "switch_session", {"session": "the leaf one"}, "open the leaf one").summary.startswith("nothing")
    assert call(built, "switch_session", {"session": ids["old-leaf"]}, "").ok  # the card's Open: the studio's switch
    [card] = cards(built, "choose").values()
    assert card["status"] == "chosen" and card["said"].startswith("Opened “")


def test_a_card_press_records_the_user_as_who_grouped_it(seeded):
    from orchestrator.project_material_edit import membership_id
    from orchestrator.scientific_comparisons import identity

    built, _ = seeded
    original = tidy_project(built)
    assert call(built, "organize_project", {"plan": {"groups": [{"item": original.id, "group": "Light"}]}}).ok
    [card] = cards(built, "tidy").values()
    assert press(built, card["id"], "apply")["ok"] is True
    group = built.hub.resources.projects.read(membership_id(identity(original)), built.day.sid)
    assert group.document["state"]["materialMembership"]["by"] == "user"
