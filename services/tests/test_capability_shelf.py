"""What it can do, by job, and one press to turn each on (orchestrator/capability_shelf.py;
docs/design/2026-10-13-capabilities-for-everyone.md, C2): one colour with one meaning (green on, yellow on its way, red one
press away, grey not on this Mac, never a red that can't be fixed), where pictures go and what it costs, and Turn on
carrying every step through: the app's own in their queues, the user's own asked for, the job switched to what was
turned on only when its own choice can't run, said once it is on."""
import pytest

from orchestrator import capability_shelf as shelf
from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from tests.capability_rig import Rig

READY = {"status": "ready", "word": "Ready", "steps": []}
DOWNLOAD = {"status": "needs-setup", "word": "Download 1.2 GB",
            "steps": [{"kind": "download", "says": "Download 1.2 GB", "model": "cellpose-sam"}]}
KEY_FIRST = {"status": "needs-setup", "word": "Needs your fal.ai key",
             "steps": [{"kind": "key", "says": "Add your fal.ai key", "service": "fal"}]}
EDITION = {"status": "unavailable", "word": "Not in this edition",
           "steps": [{"kind": "edition", "says": "not in the commercial edition"}]}


def role(*opts, current="a", rid="cells", proven=True):
    return {"id": rid, "kind": "model", "title": "Cells", "purpose": "Outlines every cell at once", "current": current,
            "default": current, "options": [{"id": i, "label": i.upper(), "said": i.upper(), "runs": runs,
                                              "readiness": r, **({"check": {"state": "works"}} if proven and r["status"] == "ready" else {})} for i, r, runs in opts]}


@pytest.fixture
def models(monkeypatch):
    """Options of the role "cells" named a, b, c run the catalog's Cellpose-SAM (on this Mac) or SAM 3.1 (fal)."""
    from orchestrator import options

    class O:
        def __init__(self, model):
            self.model, self.also = model, ()

    monkeypatch.setitem(options.GROUPS, "cells", {"a": O("cellpose-sam"), "b": O("sam3.1-cloud"), "c": O("cellpose-sam")})


def test_one_colour_one_meaning(models):
    on = shelf.card(role(("a", READY, "mac"), ("b", KEY_FIRST, "cloud")), "Find every cell", "", "", [], [])
    assert (on["colour"], on["word"], on["using"], on["turnOn"]) == ("green", "On", "A", None)
    assert on["privacy"] == "Runs on this Mac" and on["cost"] == "Free"
    moving = shelf.card(role(("a", DOWNLOAD, "mac")), "Find every cell", "", "",
                        [{"model": "cellpose-sam", "state": "downloading", "got": 40, "total": 100}], [])
    assert moving["colour"] == "yellow" and moving["word"] == "On its way: downloading 40%"
    off = shelf.card(role(("a", DOWNLOAD, "mac")), "Find every cell", "", "", [], [])
    assert off["colour"] == "red" and off["word"] == "Off: Download 1.2 GB"
    assert off["turnOn"]["steps"] == [{**DOWNLOAD["steps"][0], "by": "app"}]
    grey = shelf.card(role(("a", EDITION, "mac")), "Find every cell", "", "", [], [])
    assert grey["colour"] == "grey" and grey["turnOn"] is None, "no red without a press that turns it on"
    assert grey["word"] == "Not on this Mac: not in the commercial edition"


def test_a_cloud_option_says_where_pictures_go_and_what_it_costs(models):
    c = shelf.card(role(("b", READY, "cloud"), current="b"), "Find every cell", "", "", [], [])
    assert c["privacy"] == "Sends pictures to fal.ai (your key)" and c["cost"].startswith("Billed by the provider")


def test_a_press_turns_on_the_jobs_own_choice_else_one_with_no_key_on_this_mac_first(models):
    r = role(("b", KEY_FIRST, "cloud"), ("a", DOWNLOAD, "mac"), current="b")
    assert shelf.plan(r)["option"] == "b", "the job's own choice, when it can be turned on here"
    r = role(("b", KEY_FIRST, "cloud"), ("a", DOWNLOAD, "mac"), ("e", EDITION, "mac"), current="e")
    assert shelf.plan(r)["option"] == "a", "else no key to add, on this Mac"
    assert shelf.plan(role(("e", EDITION, "mac"), current="e")) is None


class App:
    """What TurnOn needs of the app: the wire (each call the next state), the queues, the settings, the line it says."""

    def __init__(self, states):
        self.states, self.started, self.said, self.settings, self.published = list(states), [], [], {}, 0
        self.downloads = self.installs = self
        self.hub = self

    def capabilities_wire(self, counts=True):
        state = self.states[0] if len(self.states) == 1 else self.states.pop(0)
        return {"roles": [state["role"]], "setup": {"downloads": state.get("downloads", []), "installs": []}}

    def start(self, model, by="you"):
        self.started.append(model)
        return {"model": model, "state": "queued"}

    def update(self, changes):
        self.settings.update(changes)

    def publish_settings(self):
        self.published += 1

    def turned_on(self, job, says, *, asked=None):
        self.said.append(says)


def test_turn_on_carries_every_step_through_and_says_it_once_on(models):
    off = {"role": role(("e", EDITION, "mac"), ("a", DOWNLOAD, "mac"), current="e")}
    downloading = {**off, "downloads": [{"model": "cellpose-sam", "state": "downloading", "got": 1, "total": 2}]}
    on = {"role": role(("e", EDITION, "mac"), ("a", READY, "mac"), current="e")}
    app = App([off, downloading, on])  # start reads the first; each advance the next
    turn = shelf.TurnOn(app)
    started = turn.start("cells")
    assert app.started == ["cellpose-sam"] and started["state"] == "running", "the app's own step, in its queue"
    turn.advance()
    assert turn.wire()["cells"]["says"] == "Downloading 50%" and app.started == ["cellpose-sam"], "started once"
    turn.advance()
    assert turn.wire() == {} and app.said == ["Find every cell is ready (A); your model choice is unchanged"]
    assert app.settings == {} and app.published == 0, "setup never changes the model choice"


def test_a_step_only_the_user_can_take_waits_on_the_card_and_a_failed_download_is_said(models):
    key = {"role": role(("b", KEY_FIRST, "cloud"), current="b")}
    app = App([key])
    turn = shelf.TurnOn(app)
    turn.start("cells")
    assert app.started == [] and turn.wire()["cells"]["state"] == "waiting"
    assert turn.wire()["cells"]["step"]["kind"] == "key", "the card asks for the key; nobody asks in the conversation"
    off = {"role": role(("a", DOWNLOAD, "mac"))}
    failed = {**off, "downloads": [{"model": "cellpose-sam", "state": "failed", "says": "The disk is full"}]}
    app = App([off, failed])
    turn = shelf.TurnOn(app)
    turn.start("cells")
    turn.advance()
    assert turn.wire()["cells"]["state"] == "failed" and turn.wire()["cells"]["says"] == "The disk is full"
    assert turn.cancel("cells") and turn.wire() == {}


def test_a_start_that_raises_ends_its_admission_and_is_said(models):
    """A press is admitted to its queue (no advance completes it meanwhile: the Spec review of 87ff1e59); a start that
    raises ends that admission too, so the press is said to have failed and is never left held."""
    off = {"role": role(("a", DOWNLOAD, "mac"))}
    app = App([off])

    def start(model, by="you"):
        assert turn.intents["cells"].get("_admitting") is True, "admitted while its queue starts it"
        raise RuntimeError("The download queue has stopped")

    app.start = start
    turn = shelf.TurnOn(app)
    turn.start("cells")
    assert "_admitting" not in turn.intents["cells"] and "_on_again" not in turn.intents["cells"]
    assert turn.wire()["cells"]["state"] == "failed" and turn.wire()["cells"]["says"] == "The download queue has stopped"


def test_an_advance_passed_over_while_a_press_was_admitted_runs_as_its_admission_ends(models):
    """A queue may move on before its start returns (its `changed`, catalog_changed's advance on the loop); that advance
    passes the press over while it is admitted, and the press is advanced as soon as the admission ends, not at the next
    3 s tick."""
    off = {"role": role(("a", DOWNLOAD, "mac"))}
    on = {"role": role(("a", READY, "mac"))}
    app = App([off, on])  # start reads the first; the queue's own advance, and every later one, the second
    skipped = []

    def start(model, by="you"):
        app.started.append(model)
        turn.advance()  # done at once, said while the press is still admitted: passed over
        skipped.append(turn.intents.get("cells", {}).get("state"))
        return {"model": model, "state": "done"}

    app.start = start
    turn = shelf.TurnOn(app)
    turn.start("cells")
    assert skipped == ["starting"], "the queue's advance came while the press was admitted"
    assert turn.wire() == {} and app.said == ["Find every cell is ready (A); your model choice is unchanged"], "on as its admission ended, no tick needed"


def test_the_app_shows_jobs_by_colour_and_the_agent_turns_one_on_only_on_the_users_yes(tmp_path):
    rig = Rig(tmp_path)
    wire = rig.app.capabilities_wire()
    cards = wire["setup"]["shelf"]["cards"]
    assert [c["id"] for c in cards][:2] == ["vision", "cells"] and all(
        c["colour"] in ("green", "yellow", "red", "grey") for c in cards)
    assert all(c["colour"] != "red" or c["turnOn"]["steps"] for c in cards), "every red has its press"
    registry = rig.brain.registry
    ctx = ToolContext("chat", 1, None, "what can you do here?")
    import asyncio

    with pytest.raises(ToolFailed, match="on the user's own yes"):
        asyncio.run(registry.tools["turn_on"].handler(ctx, {"job": "Find every cell", "asked_with": "yes please"}))
    said = asyncio.run(registry.tools["models"].handler(ctx, {}))
    assert "What the app can do here, by job:" in said and "Find every cell" in said
    assert isinstance(registry, Registry)


def test_one_entry_turns_on_the_same_way_right_after_it_is_added(models):
    """Turn it on, for one catalog entry (an added link): its steps carried as a job's are, said by its own name."""
    row = {"id": "mine/diatoms", "name": "Diatom counter", "runs": "this-mac", "status": "needs-setup", "word": "Download",
           "steps": [{"kind": "download", "says": "Download 117 MB", "model": "mine/diatoms"},
                     {"kind": "install", "says": "Install Diatom counter"}]}
    installing = {**row, "steps": [{"kind": "install", "says": "Install Diatom counter"}]}
    ready = {**row, "status": "ready", "steps": [], "functionalCheck": {"state": "works"}}

    class Entries(App):
        def capabilities_wire(self, counts=True):
            state = self.states[0] if len(self.states) == 1 else self.states.pop(0)
            return {"roles": [], "models": [state["row"]], "setup": {"downloads": [], "installs": state.get("installs", [])}}

    app = Entries([{"row": row}, {"row": installing}, {"row": installing, "installs": [
        {"model": "mine/diatoms", "state": "installing"}]}, {"row": ready}])
    turn = shelf.TurnOn(app)
    turn.start("model:mine/diatoms")
    assert app.started == ["mine/diatoms"], "its download"
    turn.advance()
    assert app.started == ["mine/diatoms", "mine/diatoms"], "then its install, in its queue"
    turn.advance()
    assert turn.wire()["model:mine/diatoms"]["says"] == "Installing and proving it"
    turn.advance()
    assert turn.wire() == {} and app.said == ["Diatom counter is ready (Diatom counter); your model choice is unchanged"] and app.settings == {}
