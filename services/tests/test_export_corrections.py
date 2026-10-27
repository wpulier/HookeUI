"""Exporting corrections (M3): the user's corrections of segmented cells (M2's revisions) come out as
labelled data with the view, the masks and the execution they correct, beside the relabels."""
import json
import time

from bench.export_corrections import segmentation_rows
from orchestrator.population import correct
from tests.test_corrections import keeping, saved
from tests.test_population import cells, world  # noqa: F401 (the fixture)


def test_corrected_cells_are_exported_with_their_view_masks_and_execution(world, tmp_path):
    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    a, b, c = [m.id for m in population.live()][:3]
    correct(world, population, "remove", [a], by="user", why="not a cell")
    correct(world, population, "merge", [b, c], by="user", why="one cell")
    saved(population)
    rows = segmentation_rows(tmp_path / "evidence")
    assert [r["revision"] for r in rows] == [1, 2] and {r["kind"] for r in rows} == {"segmentation"}
    removed, merged = rows
    assert removed["members"][a] == {"was": "model", "became": "deleted", "current": "deleted", "box": removed["members"][a]["box"]}
    assert removed["modelWhy"] == "not a cell" and removed["undoneBy"] is None and removed["id"].startswith("2026-10-07/")
    assert merged["added"][0]["mergedFrom"] == [b, c] and merged["members"][b]["became"] == "merged"
    assert removed["masks"].endswith("masks.npz") and removed["capture"].endswith("capture.jpg")
    assert removed["execution"] == population.made_by["execution"] and removed["implementation"] == "cellpose-sam"


def test_an_undone_correction_says_so_and_each_row_keeps_what_its_revision_did(world, tmp_path):
    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    a = population.live()[0].id
    correct(world, population, "remove", [a], by="user", why="not a cell")
    correct(world, population, "undo", [], by="user")
    saved(population)
    removed, undo = segmentation_rows(tmp_path / "evidence")
    assert removed["undoneBy"] == 2 and undo["undoes"] == 1
    assert removed["members"][a]["became"] == "deleted" and removed["members"][a]["current"] == "model"
    assert undo["members"][a]["became"] == "model"



def test_the_users_own_words_are_exported_apart_from_the_models_paraphrase(world, tmp_path):
    """M3.4 review B1."""
    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    correct(world, population, "remove", [population.live()[0].id], by="user", why="debris per user",
            asked="no no, that blob at the top is just dust")
    saved(population)
    (row,) = segmentation_rows(tmp_path / "evidence")
    assert row["userWords"] == "no no, that blob at the top is just dust" and row["modelWhy"] == "debris per user"
    assert row["manifest"].endswith("manifest.json") and "source" in row and row["producer"]


def test_exports_append_to_the_kept_log_and_a_deleted_asset_never_takes_a_row_away(world, tmp_path, monkeypatch):
    """M3.4 review B2, through main()."""
    import shutil

    from bench import export_corrections
    from orchestrator.paths import Paths

    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    a, b = [m.id for m in population.live()][:2]
    correct(world, population, "merge", [a, b], by="user")
    merged = population.revisions[-1]["added"][0]
    correct(world, population, "remove", [merged], by="user")
    saved(population)
    paths = Paths.from_env({"MICROMIND_HOME": str(tmp_path)})
    monkeypatch.setattr(Paths, "from_env", classmethod(lambda cls, env=None: paths))
    monkeypatch.setattr("sys.argv", ["export"])
    export_corrections.main()
    rows = [json.loads(line) for line in paths.corrections_log.read_text().splitlines()]
    assert paths.corrections_log.parent == paths.votes_log.parent and len(rows) == 2
    merge_row = next(r for r in rows if r["added"])
    assert merge_row["added"][0]["current"] == "deleted"  # the merged cell was removed later (review S2)
    shutil.rmtree(tmp_path / "evidence")
    export_corrections.main()  # the asset is gone; the kept rows stay, nothing is rebuilt
    assert len(paths.corrections_log.read_text().splitlines()) == 2


def test_a_masks_file_that_does_not_match_its_checksum_is_not_offered(world, tmp_path):
    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    correct(world, population, "remove", [population.live()[0].id], by="user")
    saved(population)
    (population.path / "masks.npz").write_bytes(b"not the masks")
    (row,) = segmentation_rows(tmp_path / "evidence")
    assert row["masks"] is None and row["masksProblem"] == "does not match its checksum"



def test_each_row_says_who_and_when_and_an_undone_merges_cell_is_gone(world, tmp_path):
    """M3.4 review S4: what the mutations showed untested."""
    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    a, b = [m.id for m in population.live()][:2]
    correct(world, population, "merge", [a, b], by="user")
    merged = population.revisions[-1]["added"][0]
    correct(world, population, "undo", [], by="user")
    saved(population)
    merge_row, undo_row = segmentation_rows(tmp_path / "evidence")
    assert merge_row["day"] == "2026-10-07" and merge_row["by"] == "user" and merge_row["undoneBy"] == undo_row["revision"]
    assert merge_row["added"][0]["id"] == merged and merge_row["added"][0]["current"] != "model"
    assert undo_row["members"][merged]["became"] == "deleted"
    (population.path / "masks.npz").unlink()
    assert all(r["masks"] is None and "masksProblem" not in r for r in segmentation_rows(tmp_path / "evidence"))


def test_a_row_that_changed_since_is_appended_again_and_the_earlier_line_stays(tmp_path):
    from bench.export_corrections import append_new

    out = tmp_path / "kept" / "corrections.jsonl"
    row = {"kind": "segmentation", "id": "2026-10-07/e1-r1", "undoneBy": None}
    assert append_new(out, [row]) == 1 and append_new(out, [row]) == 0
    assert append_new(out, [{**row, "undoneBy": 2}]) == 1
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["undoneBy"] for r in lines] == [None, 2]



def test_a_row_that_goes_back_to_what_it_was_is_appended_again(tmp_path):
    """The re-check of M3.4: merged, then the merged cell removed, then that undone: the latest line says it is back."""
    from bench.export_corrections import append_new

    out = tmp_path / "corrections.jsonl"
    row = {"kind": "segmentation", "id": "2026-10-07/e1-r1", "added": [{"id": "c7", "current": "added"}]}
    gone = {**row, "added": [{"id": "c7", "current": "deleted"}]}
    assert [append_new(out, [r]) for r in (row, gone, row, row)] == [1, 1, 1, 0]
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert lines[-1]["added"][0]["current"] == "added"


def test_the_kept_corrections_log_is_its_own_save_beside_the_votes(tmp_path, capsys):
    """The re-check of M3.4: under the three homes it is in logs/kept/, it has a kind, and scripts can ask for it."""
    from orchestrator.paths import KINDS, Paths, main

    saves = Paths.saves(tmp_path)
    assert saves.corrections_log == tmp_path / "logs" / "kept" / "corrections.jsonl" == saves.votes_log.parent / "corrections.jsonl"
    assert KINDS["corrections_log"] == "kept"
    assert Paths.from_env({"MICROMIND_HOME": str(tmp_path)}).corrections_log == tmp_path / "logs" / "corrections.jsonl"
    assert main(["corrections_log"]) == 0 and capsys.readouterr().out.strip().endswith("corrections.jsonl")


def test_a_rows_provenance_is_the_manifests(world, tmp_path):
    """The re-check of M3.4 (S3): source, region and scale as the manifest has them, not empty."""
    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    population.region = (10, 20, 300, 200)
    population.scale_id = {"objective": "40x", "source": "estimate"}
    population.view.update(boot="boot1", seq=4242)
    correct(world, population, "remove", [population.live()[0].id], by="user")
    saved(population)
    (row,) = segmentation_rows(tmp_path / "evidence")
    assert row["region"] == [10, 20, 300, 200] and row["scaleId"]["objective"] == "40x"
    assert row["source"]["seq"] == 4242 and row["source"]["boot"] == "boot1"


def test_rows_are_kept_per_day_and_main_writes_the_kept_log_wherever_the_saves_are(tmp_path, monkeypatch):
    """The second re-check of M3.4: the same id on two days is two rows, each appended once; main() writes the kept
    log under the three homes too, and MICROMIND_CORRECTIONS_LOG moves it."""
    from bench import export_corrections
    from bench.export_corrections import append_new
    from orchestrator.paths import Paths

    out = tmp_path / "c.jsonl"
    rows = [{"kind": "label", "day": "2026-10-07", "id": "r1-c1", "status": "accepted", "crop": None},
            {"kind": "label", "day": "2026-10-08", "id": "r1-c1", "status": "dismissed", "crop": None}]
    assert append_new(out, rows) == 2 and append_new(out, rows) == 0  # alternating days never append forever
    saves = Paths.saves(tmp_path / "home")
    monkeypatch.setattr(Paths, "from_env", classmethod(lambda cls, env=None: saves))
    monkeypatch.setattr(export_corrections, "rows", lambda research, include_open=False: rows[:1])
    monkeypatch.setattr("sys.argv", ["export"])
    export_corrections.main()
    assert (tmp_path / "home" / "logs" / "kept" / "corrections.jsonl").read_text().count("r1-c1") == 1
    assert not (tmp_path / "home" / "logs" / "corrections.jsonl").exists()
    moved = tmp_path / "elsewhere.jsonl"
    monkeypatch.undo()
    assert Paths.from_env({"MICROMIND_HOME": str(tmp_path), "MICROMIND_CORRECTIONS_LOG": str(moved)}).corrections_log == moved
