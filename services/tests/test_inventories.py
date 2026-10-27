"""The Fiji extensions inventory (orchestrator/inventories.py, bench/fiji_inventory.py; phase 3's E3 and the review's
correction 4): every extension listed is a row, never left out; each says how it installs with its evidence; installed
in Fiji and usable by the app are different facts; nothing is usable without a reviewed binding."""
import json
import tomllib

from orchestrator import inventories


def test_every_listed_extension_is_a_row_with_how_it_installs():
    data = inventories.fiji()
    rows = data["extensions"]
    assert len(rows) >= 500, "imagej.net lists about 555"
    assert sum(data["counts"].values()) == len(rows) and len({r["page"] for r in rows}) == len(rows)
    for r in rows:
        route = r["route"]
        assert route["kind"] in ("ships-with-fiji", "update-site", "choose", "none-known"), r
        if route["kind"] == "update-site":
            assert route["url"].startswith("http") and route.get("evidence"), r
        if route["kind"] == "none-known":
            assert route.get("why"), r


def test_reviewed_routes_win_and_say_why():
    reviewed = tomllib.loads(inventories.FIJI.with_name("fiji-routes.toml").read_text())
    rows = {r["page"]: r for r in inventories.fiji()["extensions"]}
    for page, route in reviewed.items():
        assert rows[page]["route"]["reviewed"] is True and rows[page]["route"]["site"] == route["site"]
        assert route["evidence"]
    assert rows["/plugins/morpholibj"]["route"]["site"] == "IJPB-plugins"


def test_installed_and_usable_are_different_facts():
    rows = {r["page"]: r for r in inventories.rows(installed=None)}
    assert rows["/plugins/morpholibj"]["installed"] == "Fiji isn't installed here yet"
    assert rows["/plugins/morpholibj"]["usable"].startswith("No binding yet")
    rows = {r["page"]: r for r in inventories.rows(installed={"IJPB-plugins"})}
    assert rows["/plugins/morpholibj"]["installed"] == "Installed in Fiji"
    assert rows["/plugins/morpholibj"]["usable"].startswith("No binding yet"), "installed is never usable by itself"
    trackmate = next(r for r in inventories.rows(installed=set()) if r["name"] == "TrackMate")
    assert trackmate["how"] == "Ships with Fiji" and trackmate["installed"] == "Installed in Fiji"
    none = next(r for r in inventories.rows() if r["route"]["kind"] == "none-known")
    assert none["how"].startswith("No install route yet (") and none["installed"] == ""


def test_a_missing_inventory_says_so(tmp_path):
    assert inventories.fiji(tmp_path / "none.json")["why"].startswith("none.json isn't there")


def test_the_summary_names_the_connected_fiji_functions_and_never_says_they_run(tmp_path, monkeypatch):
    """The Toolbox's Fiji lines name the functions connected by a reviewed binding (a "fiji" tool-function in the
    catalog), never an installed extension, and claim nothing about running: whether one runs here is its own row's
    readiness, which on a Mac without Fiji set up is not ready (Will's condition, 2026-10-15: never imply a Fiji function
    runs where Fiji isn't installed and proven)."""
    from orchestrator import readiness

    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))  # no Fiji generation here
    functions = inventories.bound()
    assert {"id": "fiji-analyze-particles", "name": "Analyze Particles (Fiji)"} in functions
    assert all(f["id"] != "fiji" for f in functions), "Fiji itself is the tool, not one of its functions"
    assert all(set(f) == {"id", "name"} for f in functions), "connected only: readiness is the row's, not the inventory's"
    listed = inventories.rows(installed=None)
    said = inventories.summary(listed, functions)
    assert said["bound"] == functions and said["listed"] == len(listed)
    assert "bound" not in inventories.summary(listed), "said only when asked for"
    # The one readiness source the studio reads beside it: without Fiji, the connected function is not ready.
    r = readiness.model("fiji-analyze-particles", {})
    assert r.status != "ready" and r.steps[0].says == "Set up Fiji first"
