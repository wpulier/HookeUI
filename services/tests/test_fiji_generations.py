"""The app's Fiji as complete generations (orchestrator/fiji_generations.py; phase 3's E3, the review's correction 2): a
generation in use never changes; turning on an update site makes the next one, cloned, with only that site's files,
proven again, and current only once proven; a generation that fails a proof is kept apart and the current one stays;
rollback; the receipt is the whole installation. A stand-in Fiji (its launcher answers the updater's commands and a
headless macro from its own db.xml.gz) in a temporary catalog: the real one is proven by make catalog-proof."""
import gzip
import json
import sys
import zipfile
from pathlib import Path

import pytest

from gpu_worker import manifests as catalog
from gpu_worker import model_files
from orchestrator import catalog_view, downloads, fiji_generations as fiji, inventories, readiness
from tests.waiting import until

LAUNCHER = f'''#!{sys.executable}
"""A stand-in Fiji launcher: the updater's commands and a headless macro, from its own db.xml.gz."""
import gzip, sys, xml.etree.ElementTree as ET
from pathlib import Path
here = Path(__file__).parent
db = here / "db.xml.gz"
tree = ET.fromstring(gzip.decompress(db.read_bytes()))
args = [a for a in sys.argv[1:] if a != "--headless"]
def save():
    db.write_bytes(gzip.compress(ET.tostring(tree)))
if args[:1] == ["--console"] and "-macro" in args:
    print("fiji 2.18.0/1.54p")
elif args[:2] == ["--update", "add-update-site"]:
    ET.SubElement(tree, "update-site", name=args[2], url=args[3], timestamp="20261012000000")
    save()
elif args[:2] == ["--update", "list-from-site"]:
    print(f"plugins/{{args[2]}}_-1.0.jar\\t(NEW)\\t20261012000000")
elif args[:2] == ["--update", "update"]:
    for name in args[2:]:
        (here / name).parent.mkdir(parents=True, exist_ok=True)
        (here / name).write_text("jar")
        p = ET.SubElement(tree, "plugin", filename=name, **{{"update-site": name.split("/")[1].split("_")[0]}})
        ET.SubElement(p, "version", checksum="c" * 40, timestamp="20261012000000")
    save()
else:
    sys.exit(f"unknown: {{args}}")
'''


def build_zip(path: Path) -> bytes:
    db = b'<pluginRecords><update-site name="Fiji-Latest" url="https://sites.imagej.net/Fiji/" official="true"/>' \
         b'<plugin filename="jars/ij-1.54p.jar" update-site="Fiji-Latest"><version checksum="' + b"a" * 40 + \
         b'" timestamp="20261007000000"/></plugin></pluginRecords>'
    with zipfile.ZipFile(path, "w") as z:
        info = zipfile.ZipInfo("Fiji/fiji")
        info.external_attr, info.create_system = 0o100755 << 16, 3  # a Unix executable, as the real build has
        z.writestr(info, LAUNCHER)
        z.writestr("Fiji/db.xml.gz", gzip.compress(db))
        z.writestr("Fiji/jars/ij-1.54p.jar", "jar")
    return path.read_bytes()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A Fiji entry pinning the stand-in build, published in a temporary catalog."""
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    monkeypatch.setenv("MICROMIND_MODEL_FILES", str(tmp_path / "old"))
    data = build_zip(tmp_path / "fiji.zip")
    import hashlib

    real = catalog.get("fiji")
    standin = catalog.Manifest(**{**{f: getattr(real, f) for f in real.__dataclass_fields__},
                                  "files": ({"path": real.files[0]["path"], "bytes": len(data),
                                            "sha256": hashlib.sha256(data).hexdigest(), "url": "https://example.org/f.zip"},)})
    monkeypatch.setattr(catalog, "get", lambda key, *a, _get=catalog.get, **k: standin if key == "fiji" else _get(key, *a, **k))
    downloads.adopt_file(standin, tmp_path / "fiji.zip")
    from orchestrator import fiji_bindings  # noqa: F401 - its real proof registered first, then replaced for the stand-in

    monkeypatch.setattr(fiji, "PROOFS", {})
    from orchestrator import fiji_runner

    monkeypatch.setattr(fiji_runner, "entries", lambda environ=None: [])  # the catalog's Fiji functions need a real Fiji
    return tmp_path


def test_setting_up_makes_a_proven_first_generation_with_its_whole_receipt(home):
    assert readiness.model("fiji", {}).steps[0].says == "Set up Fiji"
    assert fiji.first() == 1 and fiji.current() == 1
    r = fiji.receipt(1)
    assert r["proofs"] == {"smoke": "2.18.0/1.54p"} and [s["name"] for s in r["sites"]] == ["Fiji-Latest"]
    assert r["files"] == [{"file": "jars/ij-1.54p.jar", "site": "Fiji-Latest", "checksum": "a" * 40,
                           "timestamp": "20261007000000"}]
    assert r["base"]["sha256"] == catalog.get("fiji").files[0]["sha256"]
    assert list(readiness.model("fiji", {}).steps) == []
    fact = next(f for f in catalog_view.facts(catalog.get("fiji"), {}, lambda k: False, []) if f["kind"] == "generation")
    assert fact["says"].startswith("In use: generation 1 (Fiji-Latest; 1 files") and "rollback" not in fact


def test_an_update_site_makes_the_next_generation_and_never_changes_the_one_in_use(home):
    fiji.first()
    before = (fiji.fiji_dir(1) / "db.xml.gz").read_bytes()
    assert fiji.add_site("IJPB-plugins", "https://sites.imagej.net/IJPB-plugins/") == 2
    assert (fiji.fiji_dir(1) / "db.xml.gz").read_bytes() == before, "generation 1 left as it was"
    assert not (fiji.fiji_dir(1) / "plugins").exists() and (fiji.fiji_dir(2) / "plugins" / "IJPB-plugins_-1.0.jar").exists()
    r = fiji.receipt(2)
    assert r["from"] == 1 and r["added"] == [{"site": "IJPB-plugins", "url": "https://sites.imagej.net/IJPB-plugins/"}]
    assert {s["name"] for s in r["sites"]} == {"Fiji-Latest", "IJPB-plugins"} and len(r["files"]) == 2
    assert fiji.current() == 2 and fiji.sites() == {"Fiji-Latest", "IJPB-plugins"}
    assert fiji.add_site("IJPB-plugins", "https://sites.imagej.net/IJPB-plugins/") == 2, "already on: nothing new"
    morpho = next(r for r in inventories.rows(installed=fiji.sites()) if r["page"] == "/plugins/morpholibj")
    assert morpho["installed"] == "Installed in Fiji" and morpho["usable"].startswith("No binding yet")


def test_a_generation_that_fails_a_proof_is_never_made_current(home, monkeypatch):
    fiji.first()

    def breaks_with_the_new_site(folder: Path) -> str:
        if (folder / "plugins").exists():
            raise fiji.Failed("Analyze Particles found 0 particles where its macro finds 2")
        return "matches its macro"

    monkeypatch.setitem(fiji.PROOFS, "analyze-particles", breaks_with_the_new_site)
    with pytest.raises(fiji.Failed, match="0 particles"):
        fiji.add_site("Clashing", "https://example.org/clash/")
    assert fiji.current() == 1, "the generation in use stays"
    states = {g["n"]: g["state"] for g in fiji.generations()}
    assert states == {1: "current", 2: "failed"} and "0 particles" in fiji.generations()[1]["why"]


def test_rollback_makes_the_previous_generation_current_and_keeps_the_newer(home):
    fiji.first()
    fiji.add_site("IJPB-plugins", "https://sites.imagej.net/IJPB-plugins/")
    fact = next(f for f in catalog_view.facts(catalog.get("fiji"), {}, lambda k: False, []) if f["kind"] == "generation")
    assert fact["rollback"] == 1
    assert fiji.rollback() == 1 and fiji.current() == 1
    assert {g["n"]: g["state"] for g in fiji.generations()} == {1: "current", 2: "kept"}
    with pytest.raises(fiji.Failed, match="no earlier Fiji"):
        fiji.rollback()


def test_the_queue_sets_up_fiji_and_adds_a_site(home, monkeypatch):
    from orchestrator import envs

    queue = envs.Installs()
    queue.start("fiji")
    until(lambda: queue.wire()[0]["state"] in ("done", "failed"))
    assert queue.wire()[0]["state"] == "done" and fiji.current() == 1
    queue.start("fiji", site={"site": "IJPB-plugins", "url": "https://sites.imagej.net/IJPB-plugins/"})
    until(lambda: all(r["state"] in ("done", "failed") for r in queue.wire()))
    assert [r["name"] for r in queue.wire()] == ["Fiji", "IJPB-plugins in Fiji"] and fiji.current() == 2


def test_a_binding_runs_only_once_the_generation_in_use_proved_it(home, monkeypatch):
    """Live 2026-10-13: a generation set up before its binding's proof was registered had only the smoke proof, and the
    binding was offered unproven. A binding needs its proof on the generation in use; Prove it makes a new one."""
    assert readiness.model("fiji-analyze-particles", {}).steps[0].says == "Set up Fiji first"
    fiji.first()
    step = readiness.model("fiji-analyze-particles", {}).steps[0]
    assert step.says == "Prove it on Fiji" and step.model == "fiji-analyze-particles" and "generation 1" in step.how
    from orchestrator import fiji_bindings
    stamp = {**fiji_bindings.proof_identity(), "passed": True, "summary": "process stand-in proof"}
    monkeypatch.setitem(fiji.PROOFS, "fiji-analyze-particles", lambda folder: stamp)
    assert fiji.renew() == 2 and fiji.receipt(2)["proofs"]["fiji-analyze-particles"] == stamp
    assert fiji.receipt(1)["proofs"] == {"smoke": "2.18.0/1.54p"}, "generation 1's receipt never rewritten"
    assert list(readiness.model("fiji-analyze-particles", {}).steps) == []


def test_reinstall_after_space_removal_never_reuses_an_issued_generation_identity(home):
    import shutil
    first = fiji.first()
    shutil.rmtree(fiji.root() / str(first))  # Space leaves catalog metadata and original inputs in the parent
    following = fiji.first()
    assert following > first and fiji.current() == following
