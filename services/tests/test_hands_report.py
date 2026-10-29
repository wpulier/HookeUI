"""The studio's report carries what Hands' verifier reads (p25-hands-studio's additions, all optional and bounded):
the Project's open Tabs, the outlines drawn on the live view, and a population layer's member counts."""
import pytest

from orchestrator.workspace_observation import ObservationError, _legacy


def surface(**facts):
    return {"schema": "studio-surface-v1", "scope": "dom-observation", **facts}


def test_the_open_tabs_the_drawn_outlines_and_a_layers_members_are_accepted():
    tabs = [{"id": "main", "title": "Main", "front": False}, {"id": "t2", "title": "Measurements", "front": True}]
    out = _legacy(surface(workspace={"project": "2026-10-29.2", "tab": "t2", "tabTitle": "Measurements", "tabs": tabs}))
    assert out["workspace"]["tabs"] == tabs
    drawn = _legacy(surface(drawn=[{"id": "o3", "kind": "highlight"}, {"id": "o4", "kind": "part"}]))
    assert drawn["drawn"] == [{"id": "o3", "kind": "highlight"}, {"id": "o4", "kind": "part"}]
    members = _legacy(surface(shows="e2", members={"count": 344, "drawn": 289}))
    assert members["members"] == {"count": 344, "drawn": 289}


@pytest.mark.parametrize("bad", [
    {"workspace": {"project": "p", "tab": "t", "tabs": [{"id": "a", "title": "A", "front": True},
                                                         {"id": "b", "title": "B", "front": True}]}},  # two in front
    {"workspace": {"project": "p", "tab": "t", "tabs": [{"id": f"t{i}", "title": "T", "front": i == 0}
                                                         for i in range(25)]}},  # more than 24
    {"drawn": [{"id": f"o{i}", "kind": "highlight"} for i in range(61)]},  # more than 60
    {"members": {"count": -1, "drawn": 0}},
    {"members": {"count": 3, "drawn": 4}},  # more drawn than there are
])
def test_out_of_bounds_additions_are_refused(bad):
    with pytest.raises(ObservationError):
        _legacy(surface(**bad))
