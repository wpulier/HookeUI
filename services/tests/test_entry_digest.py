"""What an installed entry is, by hash (envs.entry_digest): a generation is current, and an adopted entry of yours stays
adopted, only while this digest holds. Format 2 added fields to Manifest and Implements; hashing them (even at their
defaults) changed every installed entry's digest, so an unedited format-1 entry's generation read as "Update available"
and an adopted entry with an own_trials marker vanished from the door. The digest is of the operational manifest: a
format-1 entry's is exactly what it was before format 2, and a display word (a title) changes nothing."""
import json
import re

import pytest

from gpu_worker import manifests as catalog
from orchestrator import contribute, envs, own_trials

# Computed with main's code at 7d453daa (before format 2), each unchanged entry as bundled; dark-spots is
# tests/test_env_runner.py's format-1 contributed entry (ENTRY, ADAPTER, its lock) in a folder of its own.
BEFORE_FORMAT_2 = {
    "allen-mouse-25um": "ae13771a660e85951a6525c3644ba22fe04dd5886cb4bf831ef9e32f98c981d0",
    "bioclip-2": "a59266fb0b19fc267cbaa942fe0af28ad0dd93591fac3fb511dfcbd22a9a3dc5",
    "brainreg": "fdf159e9756846ec9dc8c0a5cb6d7e52530db2aa1d9664200b7f72fbe9347bc2",
    "cellpose-sam": "3c200bbe354f87f1a6e1bb8d09ed5daba30d0fd92e8f1842d945cee1554d026d",
    "cellpose-sam-tuned": "e28b2f3e427987778bd68886f83232fcb40d38e357343c29e9ea71b3bd3ab03b",
    "claude-opus-5-5": "de89a74135c242dc85e1b0141189fd550c406d64e07d52c3db469e33f5b0aca3",
    "claude-sonnet-5-5": "4c036d9df7af5e87ce87a86374752cc5638f613528055691ce50778012290f67",
    "dinov2-small": "1d69539ef2b44cdd372a93a2f2b5b0c23a978fdb8f041726a3dea23f484747f3",
    "dinov3-small": "0b3f352629687c9e70802637f0ff1c312b7ab4c2cdca8f32d02e1d9d818f98f9",
    "edgetam": "244cd44c720592d502b7299e5de73467de8906d648092c568892fcee1640923f",
    "edgetam-video": "2ed4c10528dec3067603d83451a45385a0e8953677bbceb67ce91f9f509c69b9",
    "fiji": "6f3f0dfa259e4e6df5ce0e42880d1edb83bba4f53bfca29cf939c3fe017f5a73",
    "fiji-analyze-particles": "5b0f50a87e1c37f4ed17930a43823dbe3e10fbe7b10eaa1c79c90ca431867eb1",
    "gemini-3.1-flash-image": "92b085440a1b322ac38411e65201c679a363f369836e7d53be308ed7c3a526de",
    "gemini-3.5-flash": "b41e84a728eacc167827a431707205a32683be817295c9322d73171ae10ece73",
    "gemini-3.5-flash-lite": "e0d2f70ac54d6a468ad552ecc4873875f3b71245a5ed3b88a49984a65748b343",
    "gemini-3.8-flash": "40613c2a5f0835cf5ed833754e062207b1b28b04b609c98b143a04a0a064be4e",
    "gpt-5.6-luna": "e399eae3d4ded9e1f5329f8148a805e07f9e709d5ff29aa4d7165e8a269c3b0d",
    "gpt-6-luna": "2f1332343c4945bccfa8176f7d315df35ff7c8a5127a291366fa5554ef3bb990",
    "gpt-6.1-sol": "0ccab3213eb3b060ac1e8f56de9ac1f0e78658c068ee6417aea0186f078ec5dd",
    "gpt-image-2.5-flare": "3f25d12516ffe845f2385cf728df5738948d17690e76c31fc94b3c150e1f246c",
    "gpt-image-2.5-sunburst": "d571b1da780a0af18e92680fe0f6f5b6af57009ffcb7134b61249fd2df55a5d3",
    "gpt-live-1": "a5b7007e542aa382886750a2a782034d9e85d3d515375e7d3733bf9eedbe5a2d",
    "medsiglip": "17cd911f87a738c0d2a45f5d377cf3eed704ec61d6deda6ef6fabeda64a87103",
    "micro-sam-lm": "6fa8f7c698c99bd39c54812cf0f493d256526bf006b5d7cb561b84b5cbe06c09",
    "molmo2-4b": "790f4e33bff9e3c070c7b22c1307d964159809bf2f022fd810ea173c73e4e9f8",
    "sam2.1-tiny": "8e5c6dc2ac4818b7834aaaa69bd18a9e8cbf00031b4b227cf6ce24769ac70ca6",
    "sam3.1-cloud": "12d7d1ed904443e2930dbb1bd1972b0f461090c2f45701015f792140ea0b2ac6",
    "sam3.1-local": "eefe6b16f83bbed9ff20bf9ca651d771e3724bfd0526e90abfa13ee88c40b55a",
    "trackastra": "365879c37441cd7ddd484878eff03c54a8a7e085168ecd35f817456636caeb7c",
    "veo-3.1-fast": "c7ee0b3f559d3a5b8c1313522c30dd739abc6eea5d5093361158bfaa454f3ec9",
    "dark-spots": "27632a8c8a4dbfbbd05bf7dd87800fe01d50437a05112c99afbb6251034c22ee",
}


@pytest.fixture
def scratch_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    catalog.manifests.cache_clear()
    yield tmp_path
    catalog.manifests.cache_clear()


@pytest.mark.parametrize("key", sorted(k for k in BEFORE_FORMAT_2
                                     if k not in {"dark-spots", "fiji-analyze-particles", "medsiglip"}))
def test_a_bundled_entry_unchanged_since_format_2_keeps_its_digest(key, scratch_catalog):
    assert envs.entry_digest(catalog.manifests()[key]) == BEFORE_FORMAT_2[key]


def test_fiji_particle_contract_change_has_a_new_digest_and_keeps_old_contract_identity(scratch_catalog):
    from dataclasses import replace

    current = catalog.manifests()["fiji-analyze-particles"]
    # 8fd82f51 exposes the existing adapter as a shared cells operation. That is an
    # intentional operational contract change; a format migration alone is not.
    assert current.operation == "cells"
    assert envs.entry_digest(current) != BEFORE_FORMAT_2[current.key]
    historical = replace(current, requires={}, operation="", category="", search_aliases=(), inputs=(), outputs=(),
                         implements=(catalog.Implements("cells", current.id, "image.2d.rgb8", "labels.instance.2d"),))
    assert envs.entry_digest(historical) == BEFORE_FORMAT_2[current.key]


def test_medsiglip_classifying_is_a_new_digest_and_its_names_contract_keeps_the_old(scratch_catalog):
    """Part 2.5, W5: MedSigLIP also implements classify (each cell's first-ranked class), an intentional change of its
    operational manifest; without that row it is exactly the entry it was."""
    from dataclasses import replace

    current = catalog.manifests()["medsiglip"]
    assert [i.operation for i in current.implements] == ["names", "classify"]
    assert envs.entry_digest(current) != BEFORE_FORMAT_2["medsiglip"]
    historical = replace(current, implements=tuple(i for i in current.implements if i.operation != "classify"))
    assert envs.entry_digest(historical) == BEFORE_FORMAT_2["medsiglip"]


def test_a_contributed_format_1_entry_keeps_its_digest(tmp_path):
    from tests.test_env_runner import ADAPTER, ENTRY

    folder = tmp_path / "dark-spots"
    folder.mkdir()
    (folder / "model.toml").write_text(ENTRY.format())
    (folder / "env.lock").write_text("numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
    (folder / "adapter.py").write_text(ADAPTER)
    m, problems = catalog.read(folder / "model.toml")
    assert m is not None, problems
    assert envs.entry_digest(m) == BEFORE_FORMAT_2["dark-spots"]
    # an entry of yours adopted before format 2 (its own_trials marker keeps that digest) is still adopted, so
    # kinds.available and kinds.register keep it
    env = {"MICROMIND_CATALOG": str(tmp_path / "catalog")}
    marker = own_trials.marker(m.key, env)
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"model": m.key, "trial": None, "adoptedDigest": BEFORE_FORMAT_2["dark-spots"]}))
    assert own_trials.pending(m, env) is False


def test_a_display_word_changes_nothing_and_a_setting_changes_it(tmp_path):
    env = {"MICROMIND_CATALOG": str(tmp_path / "catalog")}
    folder = contribute.scaffold("worded", environ=env)
    original = (folder / "model.toml").read_text().replace('"TODO: Measure, Find, Outline, Clearer picture..."', '"Measure"')

    def digest(edit=lambda text: text):
        (folder / "model.toml").write_text(edit(original))
        m, problems = catalog.read(folder / "model.toml", "mine")
        assert m is not None, problems
        return envs.entry_digest(m, env)

    first = digest()
    words = {  # a capability's words: its name, card, row and form labels, and how "found none" is said
        "name": lambda t: t.replace('name = "Worded"', 'name = "Bright words"'),
        "category": lambda t: t.replace('category = "Measure"', 'category = "Find"'),
        "description": lambda t: t.replace('runner = "python-env"\n', 'runner = "python-env"\ndescription = "Long words"\n'),
        "aliases": lambda t: t.replace('runner = "python-env"\n', 'runner = "python-env"\nsearch_aliases = ["glow"]\n'),
        "label": lambda t: t.replace('label = "Threshold"', 'label = "Cut-off"').replace('tooltip = "TODO: what it changes"', 'tooltip = "Brighter"'),
        "empty": lambda t: t.replace('runner = "python-env"\n', 'runner = "python-env"\noutcomes = { empty = "Nothing bright" }\n'),
    }
    for what, edit in words.items():
        assert edit(original) != original, what
        assert digest(edit) == first, what
    assert digest(lambda t: re.sub(r"default = 128", "default = 100", t)) != first  # what runs counts
    assert digest(lambda t: t.replace('runner = "python-env"\n', 'runner = "python-env"\noutcomes = { unsupported = "a clip" }\n')) != first
