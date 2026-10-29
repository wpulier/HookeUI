"""The live partner reaches every capability the Actions panel can run, and each acting one ends in a receipt (the
live-partner spec, 4.2: "Every capability the Actions panel can run is reachable this way, and a test checks that from
the catalog and the tool registry"). Cheap: the registry and the contract are read, nothing is run."""
from __future__ import annotations

from orchestrator import capabilities, kinds
from orchestrator.voice.hands import LOOKS, contract_of
from orchestrator.voice.partner_tools import PARTNER_TOOLS, THINK_TOOLS
from tests.test_actions_panel import offered
from tests.test_activity import app

REACH = set(PARTNER_TOOLS) | set(THINK_TOOLS)


def contract(registry, name: str) -> str:
    tool = registry.tools[name]
    return contract_of(name, kind=tool.kind, produces=tool.produces, family=tool.family, sets=tool.sets)


def test_every_action_the_panel_offers_is_reachable_by_the_partner_and_acting_ones_end_in_a_receipt(tmp_path):
    built, _ = app(tmp_path)
    registry = built.brain.registry
    rows = offered(registry)
    assert len(rows) >= 30, [c["id"] for c, _ in rows]  # the panel offers the view's work, not a handful
    tools = sorted({cap["how"]["tool"] for cap, _ in rows})
    assert [t for t in tools if t not in REACH] == [], "the voice or think can call every tool the panel runs"
    for name in tools:
        found = contract(registry, name)
        if registry.tools[name].kind == "effect" and name not in LOOKS:
            assert found in ("screen", "record"), f"{name} acts, so it has a receipt contract"
        else:
            assert found in ("look", "screen", "record"), name


def test_every_tool_the_partner_calls_has_a_contract_and_a_query_is_a_look(tmp_path):
    built, _ = app(tmp_path)
    registry = built.brain.registry
    for name in sorted(REACH & set(registry.tools)):
        found = contract(registry, name)
        tool = registry.tools[name]
        if tool.kind == "effect" and name not in LOOKS:
            assert found in ("screen", "record"), f"{name} acts, so it ends in a receipt"
    # Looks are never "done": a count's or a measurement's numbers are its answer.
    assert contract(registry, "count") == contract(registry, "measure") == "look"
    assert contract(registry, "start_researcher") == "record" and contract(registry, "tabs") == "screen"


def test_the_catalogs_rows_are_reachable_too(tmp_path, monkeypatch):
    """A capability the catalog added runs through run_capability, or, on retained files, through a workflow."""
    built, _ = app(tmp_path)
    registry = built.brain.registry
    added = [{"key": "fiji.gaussian", "name": "Gaussian blur", "what": "blurs", "family": "seeing", "runner": "fiji",
              "params": []},
             {"key": "matrix.count", "name": "Count spots", "what": "counts", "family": "seeing", "runner": "python-env",
              "params": [], "executionRoute": "workflow"}]
    monkeypatch.setattr(kinds, "available", lambda *a, **k: added)
    rows = [r for r in capabilities.contributed(set(registry.tools)) if r["actor"] == "agent"]
    assert {r["how"]["tool"] for r in rows} == {"run_capability", "find_workflows"}
    assert {r["how"]["tool"] for r in rows} <= REACH and "workflow" in REACH
    assert contract_of("run_capability") == contract_of("workflow") == "record"
