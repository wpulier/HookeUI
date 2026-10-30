"""All assets, the server's half (docs/design/2026-10-12-workspace-and-assets.md; ADR 0022's amendment):
- categories: the library files each asset by what is in it, a fixed top level the vision model picks (library_looks.py)
  with its own name below; the user's correction wins; an example is filed by its specimen's one look; what has had no
  look is "Not sure yet";
- yours: the user's own and the examples a session has used;
- the trash: out of the library and every session's list, a file the library holds moved aside (never deleted), Undo;
- the routes: categories with their covers, one category's assets, an asset's sessions, its file for the preview."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from orchestrator.library import CATEGORIES, NOT_SURE, Library
from orchestrator.library_looks import LibraryLooks, prompt
from orchestrator.paths import Paths
from orchestrator.server import create_app
from tests.test_sources import library_over, store_with


def with_examples(tmp_path: Path) -> Library:
    manifest = store_with(tmp_path)
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps({"items": [{"id": "rot", "specimen": "rotifer"}, {"id": "smear", "specimen": "blood smear"}]}))
    folder = tmp_path / "assets" / "footage" / "corpus"
    folder.mkdir()
    for name in ("corpus-rot.mp4", "corpus-rot-1.mp4", "corpus-smear.mp4"):
        (folder / name).write_bytes(b"\x00")
    return Library(tmp_path / "library.jsonl", footage=tmp_path / "assets" / "footage", manifest=manifest, corpus=corpus,
                   datasets=tmp_path / "datasets", snapshots=tmp_path / "snapshots", origin_of=lambda kind, ident, at: None)


def by_title(library: Library, scope=None) -> dict:
    return {a.title_now(): a for a in library.search(scope=scope)}


def test_a_look_files_an_asset_and_the_users_category_wins(tmp_path):
    library = library_over(tmp_path)
    sperm = by_title(library)["Sperm motility"]
    assert sperm.category() is None and sperm.metadata()["category"] is None  # no look yet: "Not sure yet"
    library.looked(sperm.id, "Invertebrates", " Sperm   cells ", True, "gemini-3.8-flash")
    sperm = library.get(sperm.id)
    assert sperm.metadata()["category"] == {"group": "Invertebrates", "name": "Sperm cells", "by": "app", "sure": True}
    library.looked(sperm.id, "Mushrooms", "?", False, "m")  # a category not in the list is filed as Other
    assert library.get(sperm.id).category()["group"] == "Other"
    library.set_meta(sperm.id, {"category": "Human & animal tissue", "categoryName": "Human sperm"})
    assert library.get(sperm.id).category() == {"group": "Human & animal tissue", "name": "Human sperm", "by": "user", "sure": True}
    with pytest.raises(ValueError, match="a category is one of"):
        library.set_meta(sperm.id, {"category": "Fish"})
    # Kept across a restart, as the library's file says it.
    again = Library(tmp_path / "library.jsonl", footage=tmp_path / "assets" / "footage", manifest=tmp_path / "manifest.json",
                    datasets=tmp_path / "datasets", snapshots=tmp_path / "snapshots")
    assert again.get(sperm.id).category()["by"] == "user"
    # Searching finds it by its category's words too.
    assert [a.id for a in library.search("animal tissue")] == [sperm.id]


def test_examples_are_filed_by_one_look_per_specimen_and_wait_in_turn(tmp_path):
    library = with_examples(tmp_path)
    waiting = library.unlooked()
    examples = [a for a in waiting if a.group == "example"]
    assert sorted(a.specimen for a in examples) == ["blood smear", "rotifer"]  # one per specimen, never every clip
    assert all(a.group != "example" for a in waiting[: len(waiting) - len(examples)])  # yours first
    library.looked_specimen("rotifer", "Invertebrates", "Rotifers", True, "m")
    rotifers = [a for a in library.search(scope="example") if a.specimen == "rotifer"]
    assert len(rotifers) == 2 and all(a.category()["name"] == "Rotifers" for a in rotifers)
    assert "rotifer" not in [a.specimen for a in library.unlooked() if a.group == "example"]


def test_the_examples_show_every_specimen_before_a_second_clip_of_any(tmp_path):
    """1,506 of the 1,566 public examples are clips of one specimen (Gram-stained bacteria), all newer than the rest.
    Newest first, a person opening Examples saw a screen of those, and the tardigrade, the hydra or the paramecium only
    past item 1,500. Each specimen's newest clip comes first, then the other clips newest first; a search too."""
    import os

    manifest = store_with(tmp_path)
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps({"items": [{"id": "bac", "specimen": "Gram-stained bacteria"},
                                            {"id": "tar", "specimen": "tardigrade"}, {"id": "hyd", "specimen": "hydra"}]}))
    folder = tmp_path / "assets" / "footage" / "corpus"
    folder.mkdir()
    names = ["corpus-tar.mp4", "corpus-hyd.mp4", "corpus-bac.mp4"] + [f"corpus-bac-{n}.mp4" for n in range(1, 5)]
    for n, name in enumerate(names):  # the bacteria newest, as in the real corpus
        (folder / name).write_bytes(b"\x00")
        os.utime(folder / name, (1_000 + n, 1_000 + n))
    library = Library(tmp_path / "library.jsonl", footage=tmp_path / "assets" / "footage", manifest=manifest,
                      corpus=corpus, datasets=tmp_path / "datasets", snapshots=tmp_path / "snapshots",
                      origin_of=lambda kind, ident, at: None)

    shown = [a.specimen for a in library.search(scope="example")]
    every = len(set(shown))  # these three and the example that comes with the app
    assert {"Gram-stained bacteria", "hydra", "tardigrade"} <= set(shown[:every]) and len(set(shown[:every])) == every
    assert shown[0] == "Gram-stained bacteria"  # newest first among the firsts
    assert shown[every:] == ["Gram-stained bacteria"] * 4
    assert [a.title_now() for a in library.search(scope="example")][0] == "Gram-stained bacteria (5)"
    searched = [a.specimen for a in library.search("corpus", scope="example")]  # every corpus clip's file name
    assert sorted(searched[:3]) == ["Gram-stained bacteria", "hydra", "tardigrade"]
    assert searched[3:] == ["Gram-stained bacteria"] * 4


def test_yours_holds_the_examples_a_session_used(tmp_path):
    library = with_examples(tmp_path)
    smear = next(a for a in library.search(scope="example") if a.specimen == "blood smear")
    assert smear.id not in {a.id for a in library.search(scope="mine")}
    library.used = lambda: {smear.id}
    assert smear.id in {a.id for a in library.search(scope="mine")}
    assert smear.id in {a.id for a in library.search(scope="example")}  # still among the examples too


def test_the_trash_takes_an_asset_out_moves_a_file_the_library_holds_and_undo_brings_it_back(tmp_path):
    library = library_over(tmp_path)
    sperm = by_title(library)["Sperm motility"]
    leaf = by_title(library)["Leaf epidermis"]
    added = tmp_path / "assets" / "footage" / "added"
    assert library.trash(sperm.id) == {"id": sperm.id, "title": "Sperm motility", "moved": True}
    assert not (added / "sperm.mp4").exists() and (added / ".trash" / "sperm.mp4").is_file()
    assert (added / ".trash" / "sperm.mp4.json").is_file()  # its record goes with it
    assert library.get(sperm.id) is None and sperm.id not in {a.id for a in library.search()}
    with pytest.raises(ValueError, match="in the trash already"):
        library.trash(sperm.id)
    # A real microscope recording is only marked: the assets store's file stays where it is.
    assert library.trash(leaf.id)["moved"] is False
    assert (tmp_path / "assets" / "footage" / "real" / "2026-06-14-leaf.mp4").is_file() and library.get(leaf.id) is None
    # Kept across a restart.
    again = Library(tmp_path / "library.jsonl", footage=tmp_path / "assets" / "footage", manifest=tmp_path / "manifest.json")
    assert set(again.trashed()) == {sperm.id, leaf.id}
    library.restore(sperm.id)
    assert (added / "sperm.mp4").is_file() and (added / "sperm.mp4.json").is_file()
    assert library.get(sperm.id).title_now() == "Sperm motility"  # the same id, its record back
    with pytest.raises(ValueError, match="not in the trash"):
        library.restore(sperm.id)


def test_the_routes_offer_categories_one_categorys_assets_an_assets_sessions_and_its_file(tmp_path):
    library = with_examples(tmp_path)
    web = create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path / "home"))
    web.state.hub.library = library
    http = TestClient(web)
    mine = by_title(library, "mine")
    library.looked(mine["Sperm motility"].id, "Invertebrates", "Sperm", True, "m")
    library.looked(mine["smear"].id, "Blood", "Red blood cells", True, "m")
    body = http.get("/api/library", params={"scope": "mine"}).json()
    groups = [(c["group"], c["count"]) for c in body["categories"]]
    assert groups[:2] == [("Blood", 1), ("Invertebrates", 1)] and groups[-1][0] == NOT_SURE  # the fixed order, then the rest
    assert [c["group"] for c in body["categories"]][:-1] == [g for g in CATEGORIES if g in dict(groups)]
    assert body["categories"][0]["cover"]["title"] == "smear"
    blood = http.get("/api/library", params={"scope": "mine", "category": "Blood"}).json()
    assert [a["title"] for a in blood["assets"]] == ["smear"] and blood["count"] == 1
    unsure = http.get("/api/library", params={"scope": "mine", "category": NOT_SURE}).json()
    assert "Sperm motility" not in [a["title"] for a in unsure["assets"]] and unsure["count"] == groups[-1][1]
    assert http.get("/api/library", params={"category": "Fish"}).json() == {"assets": []}
    one = http.get(f"/api/library/{mine['smear'].id}").json()
    assert one["category"]["name"] == "Red blood cells" and one["sessions"] == []
    file = http.get(f"/api/library/{mine['smear'].id}/file")
    assert file.status_code == 200 and file.content == b"\x89PNG"
    assert http.get("/api/library/a999/file").status_code == 404
    assert http.get("/api/library/nonsense/file").status_code == 404


class Vision:
    """A stand-in fast vision model: says what it was told to, and records what it was asked."""
    def __init__(self, reply):
        self.reply, self.asked = reply, []

    async def ask(self, images, text, max_output_tokens=0):
        self.asked.append((images, text))
        return SimpleNamespace(data=self.reply, model="stand-in")


def test_a_look_is_one_fast_call_on_the_assets_picture_told_what_is_known_and_never_over_the_users(tmp_path):
    library = library_over(tmp_path)
    sperm = by_title(library)["Sperm motility"]
    for asset in library.unlooked():
        if asset.id != sperm.id:
            library.set_meta(asset.id, {"category": "Other"})  # the user's own: never looked at
    pictures: list[str] = []

    async def picture(aid):
        pictures.append(aid)
        return b"jpeg"

    logged: list[dict] = []
    vision = Vision({"category": "Invertebrates", "name": "Sperm cells", "sure": True})
    off = LibraryLooks(library, picture, lambda: vision, log=logged.append)
    assert asyncio.run(off.step()) is False and pictures == [] and not vision.asked, \
        "off by default: no picture is sent until the user turns looks on (Will, 2026-10-13)"
    looks = LibraryLooks(library, picture, lambda: vision, seen_on=lambda aid: ["sperm"] if aid == sperm.id else [],
                         log=logged.append, chosen=lambda: True)
    assert asyncio.run(looks.step()) is True
    assert pictures == [sperm.id] and vision.asked[0][0] == [b"jpeg"]
    told = vision.asked[0][1]
    assert "Its record says it shows: sperm." in told and "the app's running look named: sperm" in told
    assert all(category in told for category in CATEGORIES)
    assert library.get(sperm.id).category() == {"group": "Invertebrates", "name": "Sperm cells", "by": "app", "sure": True}
    assert logged[-1]["kind"] == "library-look" and logged[-1]["ok"] is True
    assert asyncio.run(looks.step()) is False  # nothing waits now
    # No model that can run here: nothing is looked at.
    assert asyncio.run(LibraryLooks(library, picture, lambda: None, chosen=lambda: True).step()) is False


def test_a_reply_with_no_category_files_nothing_and_is_not_asked_again_at_once(tmp_path):
    library = library_over(tmp_path)
    vision = Vision({"name": "something"})
    looks = LibraryLooks(library, lambda aid: asyncio.sleep(0, b"jpeg"), lambda: vision, chosen=lambda: True)
    assert asyncio.run(looks.step()) is False
    first = len(vision.asked)
    assert first == 1
    assert all(a.category() is None for a in library.search())
    asyncio.run(looks.step())
    assert vision.asked[1][1] != "" and len(vision.asked) == 2  # the next one waiting, not the same again
    assert "sure false" in prompt("", [], "")


# --- the agent and the library (brain/toolbox/library.py) ----------------------------------------------------
def _tools(library, day=None, put_on_stage=None):
    from orchestrator.brain import toolbox
    from orchestrator.brain.toolbox import Services
    from tests.fakes import Hub

    hub = Hub()
    hub.library = library
    return toolbox.build(Services(hub=hub, day=day, put_on_stage=put_on_stage))


def _run(registry, name, args):
    from orchestrator.brain.registry import ToolContext

    async def go():
        run = registry.start(name, args, ToolContext("chat", 1, 9))
        await run.task
        return run

    return asyncio.run(go())


def test_the_agent_finds_in_the_library_and_puts_one_on_stage_as_the_users_add_does(tmp_path):
    library = with_examples(tmp_path)
    mine = by_title(library, "mine")
    library.looked(mine["Sperm motility"].id, "Invertebrates", "Sperm", True, "m")
    asked: list[str] = []

    async def put_on_stage(aid):
        asked.append(aid)
        return {"ok": True, "onStage": aid, "title": library.get(aid).title_now()}

    registry = _tools(library, put_on_stage=put_on_stage)
    assert {"assets", "add_asset"} <= set(registry.tools)
    found = _run(registry, "assets", {"words": "sperm"})
    assert found.ok and f'{mine["Sperm motility"].id}: "Sperm motility", a recording' in found.summary
    assert "Invertebrates › Sperm; yours" in found.summary and "nothing was put on stage" in found.summary
    assert asked == []  # looking touches no session
    pictures = _run(registry, "assets", {"kind": "still"})
    assert pictures.ok and "smear" in pictures.summary and "Sperm motility" not in pictures.summary
    by_category = _run(registry, "assets", {"category": "Invertebrates"})
    assert by_category.ok and by_category.summary.startswith("1 in your assets")
    examples = _run(registry, "assets", {"whose": "examples", "words": "rotifer"})
    assert examples.ok and "an example" in examples.summary and examples.summary.startswith("2 in the examples")
    assert "nothing in your assets matches" in _run(registry, "assets", {"words": "kidney"}).summary
    refused = _run(registry, "assets", {"category": "Fish"})
    assert not refused.ok and "a category is one of" in refused.summary
    added = _run(registry, "add_asset", {"asset": mine["Sperm motility"].id})
    assert added.ok and asked == [mine["Sperm motility"].id] and "it is one of this session's assets now" in added.summary
    wrong = _run(registry, "add_asset", {"asset": "the sperm one"})
    assert not wrong.ok and asked == [mine["Sperm motility"].id]


def test_without_a_library_or_a_way_to_put_one_on_stage_there_are_no_library_tools(tmp_path):
    assert "assets" not in _tools(None).tools
    assert "add_asset" not in _tools(library_over(tmp_path), put_on_stage=None).tools
