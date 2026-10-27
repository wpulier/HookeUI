"""Inventories the catalog lists but does not run by itself (phase 3's E3): the Fiji extensions, every row of
imagej.net/list-of-extensions (catalog/inventories/fiji-extensions.json, made by bench/fiji_inventory.py and reviewed).

Each row's status is said from facts kept apart (the review's correction 3):

- **listed:** always (an unmapped extension is never left out);
- **how it installs:** ships with Fiji, an update site (with the evidence), several to choose from, or none known yet;
- **installed in Fiji:** in the Fiji generation in use (fiji_generations.py), once there is one;
- **usable by the app:** only through a reviewed binding (a `tool-function` that declares its input, output and headless
  route, and whose proof ran it). Without one: "no binding yet", whatever is installed.

Read once and cached by the file's time; the studio asks for it when a Toolbox view is shown (the Toolbox's Fiji section
and Everything it can do's Fiji tile, `GET /api/catalog/inventories/fiji`), never on the capabilities message.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIJI = REPO / "catalog" / "inventories" / "fiji-extensions.json"
ROUTE_WORDS = {
    "ships-with-fiji": "Ships with Fiji",
    "update-site": "From the {site} update site",
    "choose": "On several update sites ({names}): not chosen yet",
    "none-known": "No install route yet ({why})",
}
_cache: dict = {}


def fiji(path: Path = FIJI) -> dict:
    """The inventory as kept, or {"extensions": []} with why when it isn't there."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {"extensions": [], "why": f"{path.name} isn't there (make fiji-inventory makes it)"}
    if _cache.get("mtime") != (path, mtime):
        _cache.update(mtime=(path, mtime), value=json.loads(path.read_text()))
    return _cache["value"]


def route_words(route: dict) -> str:
    kind = route.get("kind", "none-known")
    names = ", ".join(s["site"] if isinstance(s, dict) else s for s in route.get("sites", []))
    return ROUTE_WORDS.get(kind, kind).format(site=route.get("site", ""), names=names, why=route.get("why", ""))


def rows(installed: set[str] | None = None, bindings: dict[str, str] | None = None) -> list[dict]:
    """Every listed extension with its facts: `installed` are the update sites in the Fiji generation in use (None:
    no Fiji yet), `bindings` the reviewed functions by extension page (their status words)."""
    bindings = bindings or {}
    out = []
    for e in fiji().get("extensions", []):
        route = e.get("route") or {"kind": "none-known", "why": "not mapped"}
        sites = {route.get("site")} if route.get("kind") == "update-site" else set()
        if installed is None:
            there = "Fiji isn't installed here yet" if route["kind"] != "none-known" else ""
        elif route["kind"] == "ships-with-fiji" or (sites and sites <= installed):
            there = "Installed in Fiji"
        elif route["kind"] == "none-known":
            there = ""
        else:
            there = "Not in this Fiji yet"
        usable = bindings.get(e["page"], "No binding yet: the app can't run it (a reviewed binding makes it usable)")
        out.append({"name": e["name"], "page": e["page"], "categories": e.get("categories", []),
                    "route": route, "how": route_words(route), "installed": there, "usable": usable,
                    "link": f"https://imagej.net{e['page']}"})
    return out


def bound() -> list[dict]:
    """The Fiji functions connected to the app: each a reviewed binding (a catalog entry with runner "fiji" and kind
    "tool-function", fiji_bindings.py), by id and name only. Connected is not ready: whether one runs here is its own
    row's readiness (readiness.py through catalog_view.py, ADR 0019), which the studio reads beside this, so nothing here
    claims it runs (a Mac without Fiji set up is never told it can run Analyze Particles; Will, 2026-10-15)."""
    from gpu_worker import manifests as catalog

    return sorted(({"id": m.id, "name": m.name} for m in catalog.manifests().values()
                   if m.runner == "fiji" and m.kind == "tool-function"), key=lambda b: b["name"])


def summary(all_rows: list[dict], functions: list[dict] | None = None) -> dict:
    """How the listed extensions install, by route, and (`functions`) the Fiji functions connected by a reviewed binding
    (`bound`): the Toolbox says both, with each function's readiness from its own row, so neither installable nor
    connected is ever read as usable (Will, 2026-10-15)."""
    counts: dict[str, int] = {}
    for r in all_rows:
        counts[r["route"]["kind"]] = counts.get(r["route"]["kind"], 0) + 1
    out = {"listed": len(all_rows), "routes": counts}
    if functions is not None:
        out["bound"] = functions
    return out
