"""The agent's tools offer what can be chosen now (toolbox.refresh, App.catalog_changed): a model turned on while the app
runs (an entry with its own adapter proven, one of yours turned on) is a choice of `use` and `set_default` at once, and
one taken away is not; their handlers and what they keep stay the ones in use. Live 2026-10-13: Dark spots, installed
while the app ran, was Ready on its card and, to the agent, "isn't listed among the selectable segmentation tools"."""
import dataclasses
import json

from orchestrator import names, operations, options
from tests.capability_rig import Rig


def implementations(rig, tool: str) -> list[str]:
    return rig.brain.registry.tools[tool].params["properties"]["implementation"]["enum"]


def test_a_model_turned_on_while_the_app_runs_is_a_choice_of_the_agents_tools_at_once(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    handler = rig.brain.registry.tools["use"].handler
    offer = rig.brain.services.offer_download
    assert "dark-spots" not in implementations(rig, "use")

    cells = operations.OPERATIONS["cells"]
    monkeypatch.setitem(operations.OPERATIONS, "cells", dataclasses.replace(cells, implementations=cells.implementations + (
        operations.Impl("dark-spots", "Dark spots", "model", ("field",), "analysis", "dark-spots", ()),)))
    monkeypatch.setitem(options.GROUPS["cells"], "dark-spots", options.Option("dark-spots", "Dark spots", "contributed"))
    monkeypatch.setitem(operations.ADAPTERS, ("cells", "dark-spots"), operations.ADAPTERS[("cells", "cellpose-sam")])
    names.index.cache_clear()  # what env_runner.register does for a proven entry, as it does it
    rig.app.catalog_changed()

    assert "dark-spots" in implementations(rig, "use") and "dark-spots" in implementations(rig, "set_default")
    assert "dark-spots = Dark spots" in json.dumps(rig.brain.registry.tools["use"].params), "said by its name too"
    assert rig.brain.registry.tools["use"].handler is handler, "the tool in use, only its choices made again"
    assert rig.brain.services.offer_download is offer, "the live tools' offers, never a fresh build's"

    monkeypatch.setitem(operations.OPERATIONS, "cells", cells)
    options.GROUPS["cells"].pop("dark-spots")
    names.index.cache_clear()
    rig.app.catalog_changed()
    assert "dark-spots" not in implementations(rig, "use"), "taken away, it is no choice"
