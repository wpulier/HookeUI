"""A result drawn on the live view is brought into sight (the live partner spec 4.8, horizontal 2: one truth for what is on
screen; docs/architecture/on-screen.md "A Tab's specimen").

will-research "classify-outline", measure-c4acc90, all runs 2/3: "Outline every cell." was said while the findings
document's Tab was in front, its specimen hidden. outline_cells answered "the layer is on the live view" and the outlines
stayed out of sight ("left the outlines hidden behind the reader instead of bringing the live view forward"; the voice:
"The outlines are on the live view, but that view is hidden behind the reader tab"). In will-workspace the same request
worked only because the user said "Back on the Main tab". Will's rule: a new Tab is a canvas with no specimen; Main
always keeps it. So a result on the live view (the cells' layer, a find's or a highlight's outlines) brings Main to the
front when the Tab in front hides the specimen, unless the request's own steps say where, and its receipt says where it
is shown.

Actual App, Registry, Screen, tabs broker and COMMANDS with the synthetic studio of test_workspace_display.py. The cell
finder is the seam (tools.outline_cells: its own tests run it); no model, camera or worker."""
import asyncio
from types import SimpleNamespace

from orchestrator import population as population_mod
from orchestrator import tools
from tests.test_table_window import WORKSPACE, asked
from tests.test_workspace_display import built


def cells_found(monkeypatch):
    """The cell finder's result as tools.outline_cells returns it: a population of 161 cells, shown as a layer."""
    cells = SimpleNamespace(id="e2", title="Cell outlines · Cellpose-SAM · 01:40", view={},
                            count_words=lambda: "161 instances segmented by cellpose-sam inside the eyepiece disc")

    async def outline_cells(hub, compare_reference=False):
        return cells, True

    monkeypatch.setattr(tools, "outline_cells", outline_cells)
    monkeypatch.setattr(population_mod, "drawn_on_stage", lambda hub, population: True)
    return cells


def reader_in_front(studio, title="Findings so far"):
    """The findings document in a Tab of its own, in front, its specimen hidden (a reader Tab, as write_document opens
    one)."""
    studio.tabs.append({"id": "tab-1", "title": title})
    studio.active = "tab-1"
    studio.specimens["tab-1"] = {"state": "hidden"}


def switches(studio):
    return [m["action"] for m in studio.sent("workspace-tabs") if m["action"]["action"] == "switch"]


def test_outlining_every_cell_while_a_reader_tab_is_in_front_brings_main_forward(tmp_path, monkeypatch):
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        cells_found(monkeypatch)
        reader_in_front(studio)
        await studio.send_report()
        run = await asked(app, studio, "outline_cells", {})
        assert run.ok is True, run.summary
        assert switches(studio) == [{"action": "switch", "tab": "main"}], "Main, which always keeps the specimen"
        assert studio.active == "main" and studio.specimens["main"]["state"] == "fill"
        assert "the layer is on the live view" in run.summary
        assert 'on Tab "Main", brought to the front' in run.summary, run.summary
        assert 'Tab "Findings so far" in front hid the live view' in run.summary, run.summary
    asyncio.run(go())


def test_the_live_view_already_in_sight_is_left_as_it_is(tmp_path, monkeypatch):
    """Main in front, or a Tab whose specimen floats (a new Tab opens so): nothing is switched, nothing more is said."""
    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        cells_found(monkeypatch)
        run = await asked(app, studio, "outline_cells", {})
        assert run.ok is True and not switches(studio)
        assert run.summary.endswith("the layer is on the live view"), run.summary
        studio.tabs.append({"id": "tab-1", "title": "Graphic"})
        studio.active = "tab-1"
        studio.specimens["tab-1"] = {"state": "float", "rect": [16, 620, 240, 216]}
        run = await asked(app, studio, "outline_cells", {})
        assert run.ok is True and not switches(studio) and studio.active == "tab-1"
    asyncio.run(go())


def test_a_tab_the_request_named_is_where_it_stays(tmp_path, monkeypatch):
    """Unless the user said where: a Tab step in the request's own plan is the model's reading of their words."""
    from orchestrator.brain.registry import Steps

    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        cells_found(monkeypatch)
        reader_in_front(studio)
        await studio.send_report()
        steps = Steps()
        steps.planned[1] = ("tabs", {"action": "switch", "tab": "tab-1"})
        steps.planned[2] = ("outline_cells", {})
        original = app.brain.registry.start

        def start(name, args, ctx, run=None):
            ctx.steps, ctx.step = steps, 2
            return original(name, args, ctx, run=run)

        monkeypatch.setattr(app.brain.registry, "start", start)
        run = await asked(app, studio, "outline_cells", {})
        assert run.ok is True and not switches(studio) and studio.active == "tab-1", run.summary
        assert run.summary.endswith('; Tab "Findings so far" in front hides the live view'), "said, never claimed seen"
    asyncio.run(go())


def test_a_find_while_a_reader_tab_is_in_front_brings_main_forward_too(tmp_path, monkeypatch):
    """The outlines a find or a highlight draws go through the one check that they are seen (present.reshow_outlines),
    so they are brought into sight the same way."""
    from orchestrator.brain.registry import ToolContext
    from orchestrator.brain.toolbox.present import reshow_outlines
    from orchestrator.ticket import issue, using

    async def go():
        app, studio = await built(tmp_path, monkeypatch)
        reader_in_front(studio, title="Graphic")
        await studio.send_report()
        ticket = issue(app.hub, "turn", asked=app.day.here(), channel="chat")
        ctx = ToolContext("chat", 1, None, "find the stomata", asked=ticket.asked)
        ctx.client, ctx.workspace_id = studio, WORKSPACE
        ctx.admission, ctx.request_id = ticket.admission, ticket.admission.request_id
        ctx.request_snapshot = app.hub.screen.pin(studio, request_id=ticket.admission.request_id,
                                                  timing="server-receive-legacy")
        with using(ticket):
            said = await reshow_outlines(SimpleNamespace(hub=app.hub), ctx)
        ticket.seal()
        assert switches(studio) == [{"action": "switch", "tab": "main"}] and studio.active == "main"
        assert 'on Tab "Main", brought to the front' in said and 'Tab "Graphic" in front hid the live view' in said
    asyncio.run(go())
