"""The Models page's rows (orchestrator/catalog_view.py; P3 of the open catalog): every catalog entry, its group (needs
you only for your current choices, the review's correction 1), its facts with their references, what it is used for.
Each case in an isolated profile: keys given as a mapping, models' files in a temporary folder (or none), never this
Mac's downloads or keys."""
import json

import pytest

from gpu_worker import manifests as catalog
from gpu_worker import model_files
from orchestrator import capabilities, catalog_view, options, readiness


def rows(keys: dict, settings=None, key_facts: dict | None = None, downloads=None) -> dict[str, dict]:
    wire = capabilities.wire(settings or options.Settings(), keys)
    return {r["id"]: r for r in catalog_view.rows(wire["roles"], key_facts or {}, keys, downloads)}


@pytest.fixture
def nothing_downloaded(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_MODEL_FILES", str(tmp_path / "files"))
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    return tmp_path


def test_every_catalog_entry_has_one_row_in_one_group():
    found = rows({})
    assert set(found) == set(catalog.manifests())
    assert all(r["group"] in catalog_view.GROUP_ORDER for r in found.values())
    order = [catalog_view.GROUP_ORDER.index(r["group"]) for r in catalog_view.rows(
        capabilities.wire(options.Settings(), {})["roles"], {}, {})]
    assert order == sorted(order), "the page reads top to bottom: needs you, ready, available, unavailable"


def test_a_fresh_mac_needs_you_only_for_what_your_choices_use(nothing_downloaded):
    found = rows({})
    vision = options.Settings().vision
    seeing = next(r for r in found.values() if any(u["role"] == "vision" and u["current"] for u in r["usedFor"]))
    assert seeing["group"] == "needs-you" and seeing["next"]["kind"] == "key", vision
    edgetam = found["edgetam"]
    assert edgetam["group"] == "needs-you" and edgetam["next"] == {
        "kind": "download", "says": "Download 56 MB", "model": "edgetam",
        "how": "Its Download button on the Models page (the chip at the top), or make catalog-download ID=edgetam",
        "bytes": sum(f["bytes"] for f in model_files.files(catalog.get("edgetam")))}  # what Turn on's sheet adds up
    # Nothing you chose needs these now: available to add, never "needs you" (DINOv3 is no default; Trackastra and
    # SAM 3.1 in the cloud are fixed in the code for linking and find all like this, which only run when asked).
    for model in ("dinov3-small", "trackastra", "sam3.1-cloud", "molmo2-4b"):
        assert found[model]["group"] == "available", (model, found[model]["group"], found[model]["usedFor"])


def test_a_google_key_alone_leaves_talking_needing_openai(nothing_downloaded, monkeypatch):
    monkeypatch.setenv("MICROMIND_MODEL_FILES", "standin")  # every model on this Mac downloaded
    found = rows({"GEMINI_API_KEY": True})
    needs = {r["id"]: r for r in found.values() if r["group"] == "needs-you"}
    assert found["gemini-3.8-flash"]["status"] == "ready"
    assert found["gemini-3.8-flash"]["group"] == "available", "a key permits a check, but does not prove it"
    assert "gpt-live-1" in needs and needs["gpt-live-1"]["word"] == "Needs your OpenAI API key"
    assert all("OPENAI" in r["next"]["how"] or r["next"].get("service") == "openai" for r in needs.values()), needs.keys()
    found = rows({"GEMINI_API_KEY": True, "OPENAI_API_KEY": True})
    assert not [r["id"] for r in found.values() if r["group"] == "needs-you"], "both keys: all set"


def test_the_facts_say_why_each_with_its_reference(nothing_downloaded):
    facts = {"GEMINI_API_KEY": {"service": "gemini", "short": "Google", "sourceSays": "from your keys file (~/x)",
                                "getKey": "https://aistudio.google.com/apikey"}}
    found = rows({"GEMINI_API_KEY": True}, key_facts=facts)
    gemini = {f["kind"]: f for f in found["gemini-3.8-flash"]["facts"]}
    assert gemini["key"] == {"kind": "key", "ok": True, "says": "Your Google key: from your keys file (~/x)",
                             "service": "gemini", "ref": "https://aistudio.google.com/apikey"}
    assert gemini["edition"]["ok"] is True and "edition" in gemini["edition"]["says"]
    sam = {f["kind"]: f for f in found["sam3.1-local"]["facts"]}
    assert sam["files"]["ok"] is False and sam["files"]["model"] == "sam3.1-local"
    assert sam["files"]["says"] == "Not downloaded yet: 3.5 GB, revision daa6319"
    assert sam["files"]["ref"].endswith("/catalog/models/sam3.1-local")
    assert sam["key"]["ok"] is False and "only to download it" in sam["key"]["says"], "a gated model's key is to get it"
    assert sam["licence"]["ref"] == "https://huggingface.co/facebook/sam3.1"


def test_a_published_revision_is_said_with_its_folder(nothing_downloaded):
    m = catalog.get("dinov2-small")
    folder = model_files.revision_dir(m)
    folder.mkdir(parents=True)
    for f in m.files:
        (folder / f["path"]).write_bytes(b"x" * f["bytes"])
    (folder / "receipt.json").write_text(json.dumps({"revision": catalog.revision_of(m)}))
    files = next(f for f in rows({})["dinov2-small"]["facts"] if f["kind"] == "files")
    assert files["ok"] is True and files["says"].startswith("Downloaded: 88 MB, revision ed25f3a")
    assert files["ref"].endswith(f"/models/dinov2-small/{catalog.revision_of(m)}")


def test_what_it_is_used_for_is_one_line_per_job():
    edgetam = rows({})["edgetam"]
    outlining = [u for u in edgetam["usedFor"] if u["role"] == "outline"]
    assert len(outlining) == 1 and outlining[0]["current"] and len(outlining[0]["options"]) >= 2  # EdgeTAM and the chains
    follow = rows({})["edgetam-video"]["usedFor"]
    assert [u["title"] for u in follow] == ["Outlines follow"] and follow[0]["current"]
    lessons = rows({})["claude-sonnet-5-5"]["usedFor"]
    assert lessons[0]["fixed"] and lessons[0]["role"] == "fixed:lesson-writer"


def test_a_download_in_progress_is_on_its_row():
    row = rows({}, downloads=[{"model": "sam3.1-local", "state": "downloading", "got": 1, "total": 2}])["sam3.1-local"]
    assert row["download"]["state"] == "downloading"


def test_an_option_that_runs_more_than_one_model_needs_each(nothing_downloaded):
    # "EdgeTAM then SAM 3.1 (this Mac)" runs both (Option.also): not downloaded, it says both downloads.
    r = readiness.option("outline", "edgetam>sam3.1-local", keys={})
    assert not r.available and [s.says for s in r.steps if s.kind == "download"] == ["Download 56 MB", "Download 3.5 GB"]
    assert not readiness.option("pointing", "molmo", keys={}).available
    assert readiness.option("track", "objects", keys={}).next.model == "edgetam"  # EdgeTAM video runs EdgeTAM's files


def test_every_fixed_model_names_its_catalog_entry():
    cat = capabilities.load()
    for r in cat["roles"]:
        if r["id"].startswith("fixed:"):
            assert catalog.get(r["model"]) is not None, r["id"]
            if r.get("fallback"):
                assert catalog.get(r["fallback"]["model"]) is not None, r["id"]


def test_reading_the_rows_loads_no_model_library():
    import subprocess
    import sys

    code = ("import sys; from orchestrator import capabilities, catalog_view, options; "
            "w = capabilities.wire(options.Settings(), {}); catalog_view.rows(w['roles'], {}, {}); "
            "print(sorted(m for m in ('torch', 'transformers', 'cellpose', 'open_clip', 'sam3') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip().splitlines()[-1] == "[]", out.stdout


def test_setup_completion_requires_current_option_requirements_even_with_old_proof():
    role = {'id': 'cells', 'entry': 'edgetam', 'current': 'composite'}
    option = {'id': 'composite', 'model': 'edgetam', 'models': ['edgetam', 'sam3.1-fal'],
              'label': 'Composite', 'said': 'Composite', 'runs': 'both',
              'readiness': {'status': 'ready'}, 'check': {'state': 'works'}}
    assert catalog_view._setup_plan(role, option)['ready']
    option['readiness'] = {'status': 'needs-setup', 'steps': [
        {'kind': 'key', 'service': 'fal', 'says': 'Add your fal.ai key'}]}
    setup = catalog_view._setup_plan(role, option)
    assert not setup['ready']
    assert not setup['recheck']
    assert setup['plan']['steps'][0]['service'] == 'fal'


def test_setup_locations_are_the_installers_own_private_destinations(nothing_downloaded):
    from orchestrator import envs, fiji_generations

    found = rows({})
    m = catalog.get('micro-sam-lm')
    assert found[m.key]['installFolder'] == catalog_view._home(envs.folder(m))
    assert found['fiji']['installFolder'] == catalog_view._home(fiji_generations.root())
    assert found['dinov2-small']['folder'] == catalog_view._home(model_files.folder(catalog.get('dinov2-small')))
