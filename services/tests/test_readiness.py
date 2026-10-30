"""Readiness (orchestrator/readiness.py; ADR 0019): one resolver over facts kept apart, one status and one next step on
every surface. Each status is forced in an isolated profile (temporary model files, a temporary catalog, keys given as a
mapping), never by touching this Mac's own downloads, installs or keys."""
import importlib.machinery
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from gpu_worker import manifests as catalog
from orchestrator import licences, options, readiness

NO_KEYS = {}
ALL_KEYS = {s.key: True for s in catalog.services().values()}


def old_why(group: str, option_id: str, keys: dict) -> str:
    """What the app said before the resolver (options.catalogue before ADR 0019), kept as the reference."""
    o = options.GROUPS[group][option_id]
    has_key = not o.needs or bool(keys.get(o.needs))
    return licences.why_not(o.uses) or ("" if has_key else f"needs {o.needs}") or options.unmade(o) \
        or options.not_installed(group, o.id)


@pytest.fixture
def sam3_code(monkeypatch):
    """Observe optional SAM3 code present/absent without installing or importing a model."""
    find_spec = importlib.util.find_spec

    def observe(present):
        def probe(name, package=None):
            if name == "sam3":
                return importlib.machinery.ModuleSpec(name, loader=None) if present else None
            return find_spec(name, package)
        monkeypatch.setattr(importlib.util, "find_spec", probe)

    return observe


@pytest.mark.parametrize("keys", [NO_KEYS, ALL_KEYS], ids=["no keys", "every key"])
def test_legacy_choices_are_unchanged_with_sam3_code_available(keys, sam3_code):
    # The pre-ADR-0019 oracle only knew installation needs of operation-selected roles.
    # It missed outline_repair's catalog requirement. Compare its historical code-present
    # state explicitly; the missing-code behavior is checked independently below.
    sam3_code(True)
    for group, opts in options.GROUPS.items():
        for o in opts.values():
            r = readiness.option(group, o.id, keys=keys)
            assert r.why == old_why(group, o.id, keys), f"{group}/{o.id}"
            assert r.status in readiness.STATUSES and (r.status != "ready" or not r.steps), f"{group}/{o.id}"
            assert r.available or r.status != "ready", f"{group}/{o.id}: ready but not available"


@pytest.mark.parametrize("keys", [NO_KEYS, ALL_KEYS], ids=["no keys", "every key"])
@pytest.mark.parametrize("group", ["outline", "outline_repair"])
@pytest.mark.parametrize("present", [False, True], ids=["missing code", "code available"])
def test_sam3_choices_require_the_catalogs_code_on_each_role(keys, group, present, sam3_code):
    sam3_code(present)
    r = readiness.option(group, "sam3.1-local", keys=keys)
    if present:
        assert (r.status, r.available, r.why, r.steps) == ("ready", True, "", ())
    else:
        why = {"outline": "not installed here (sam3: `make sam3-setup`)",
               "outline_repair": "Install SAM 3.1's code"}
        assert (r.status, r.available, r.why) == ("needs-setup", False, why[group])
        assert (r.next.kind, r.next.says, r.next.how) == (
            "install", "Install SAM 3.1's code", "not installed here (sam3: `make sam3-setup`)")
    offered = next(o for o in options.catalogue(keys)[group] if o["id"] == "sam3.1-local")
    assert (offered["available"], offered["why"], offered["readiness"]) == (r.available, r.why, r.wire())


def test_a_cloud_model_needs_its_key_and_says_where_to_get_one():
    r = readiness.option("vision", "gemini-3.8-flash", keys=NO_KEYS)
    assert (r.status, r.word, r.why) == ("needs-setup", "Needs your Google Gemini API key", "needs GEMINI_API_KEY")
    assert r.next.kind == "key" and r.next.url == "https://aistudio.google.com/apikey" and "GEMINI_API_KEY=..." in r.next.how
    assert readiness.option("vision", "gemini-3.8-flash", keys={"GEMINI_API_KEY": True}).status == "ready"


@pytest.mark.parametrize('requirement', ['memory:nan', 'memory:inf', 'memory:-1', 'memory:0', 'memory:lots'])
def test_invalid_memory_requirements_cannot_claim_machine_compatibility(requirement):
    from dataclasses import replace

    m = replace(catalog.get('dinov2-small'), needs_to_run=(requirement,))
    assert 'memory requirement is invalid' in readiness.misfit(m)
    assert any('positive finite memory' in problem for problem in catalog._problems(m, m.id))


@pytest.mark.parametrize('reading', ['missing', 0, -1])
def test_unknown_machine_memory_is_not_assumed_sufficient(monkeypatch, reading):
    from dataclasses import replace

    m = replace(catalog.get('dinov2-small'), needs_to_run=('memory:8',))
    def read(name):
        if reading == 'missing':
            raise OSError('unavailable')
        return reading if name == 'SC_PHYS_PAGES' else 4096
    monkeypatch.setattr(os, 'sysconf', read)
    assert 'could not be checked' in readiness.misfit(m)
    monkeypatch.setattr(os, 'sysconf', lambda name: 16_000_000_000 if name == 'SC_PHYS_PAGES' else 1)
    assert readiness.misfit(m) == '', 'a corrected observation permits another requirements check'


def test_a_local_model_not_downloaded_cannot_run_and_its_step_is_the_download(tmp_path, monkeypatch):
    # Nothing fetches a model's files on first use (gpu_worker/model_files.py): not downloaded, it can't be chosen,
    # and its next step is the Download, with the model it downloads.
    monkeypatch.setenv("MICROMIND_MODEL_FILES", str(tmp_path / "files"))
    r = readiness.option("outline", "sam2.1-tiny", keys=NO_KEYS)
    assert not r.available and r.status == "needs-setup" and r.obtained is False
    assert r.why == "not downloaded yet: 156 MB" and r.word == "Download 156 MB"
    assert r.next.kind == "download" and r.next.model == "sam2.1-tiny" and "Download button" in r.next.how
    monkeypatch.setenv("MICROMIND_MODEL_FILES", "standin")
    assert readiness.option("outline", "sam2.1-tiny", keys=NO_KEYS).status == "ready"


# A model of our own, in a catalog of our own, with files we make: every status of a local model, forced.
def _catalog(tmp_path, monkeypatch, files: str, needs_to_get: str, needs_to_run: str = "[]", licence: str = "edgetam"):
    root = tmp_path / "catalog" / "models" / "test-finder"
    root.mkdir(parents=True)
    (root / "model.toml").write_text(f'''id = "test-finder"
name = "Test finder"
runs = "this-mac"
version = "org/test-finder"
licence = ["{licence}"]
kind = "model"
what = "Outlines what you click"
runner = "gpu-worker"
source = {{ hf = "org/test-finder", revision = "{'a' * 40}" }}
files = {files}
needs_to_get = {needs_to_get}
needs_to_run = {needs_to_run}
workers = ["analysis"]
documented_mb = 1
load_limit_s = 1
''')
    monkeypatch.setattr(catalog, "ROOT", tmp_path / "catalog" / "models")
    catalog.manifests.cache_clear()
    monkeypatch.setenv("MICROMIND_MODEL_FILES", str(tmp_path / "files"))
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "mine"))
    m = catalog.get("test-finder")
    assert m is not None, catalog.problems()
    return m


def _download(tmp_path, content: bytes, blob: str):
    """Put a file into the Hugging Face cache layout under MICROMIND_MODEL_FILES: blobs named by content id, the
    snapshot's file a link to its blob."""
    repo = tmp_path / "files" / "hf" / "models--org--test-finder"
    (repo / "blobs").mkdir(parents=True, exist_ok=True)
    (repo / "blobs" / blob).write_bytes(content)
    snap = repo / "snapshots" / ("a" * 40)
    snap.mkdir(parents=True, exist_ok=True)
    link = snap / "w.pt"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(Path("..") / ".." / "blobs" / blob)


SHA = "b" * 64
FILES = f'[{{ path = "w.pt", bytes = 5, sha256 = "{SHA}" }}]'


@pytest.fixture(autouse=False)
def fresh_catalog():
    yield
    catalog.manifests.cache_clear()


def test_obtained_means_each_pinned_file_at_its_size_and_content_id(tmp_path, monkeypatch, fresh_catalog):
    m = _catalog(tmp_path, monkeypatch, FILES, '["download"]')
    assert readiness.obtained(m) == (False, 5)
    _download(tmp_path, b"12345", SHA)
    assert readiness.obtained(m) == (True, 0)
    _download(tmp_path, b"1234", SHA)  # another size
    assert readiness.obtained(m) == (False, 5)
    _download(tmp_path, b"12345", "c" * 64)  # another content id: another revision's file
    assert readiness.obtained(m) == (False, 5)
    assert readiness.model("test-finder", NO_KEYS).status == "needs-setup"
    _download(tmp_path, b"12345", SHA)
    assert readiness.model("test-finder", NO_KEYS).status == "ready"


def test_a_published_revision_is_downloaded_and_a_staging_folder_never_is(tmp_path, monkeypatch, fresh_catalog):
    import json

    from gpu_worker import model_files

    m = _catalog(tmp_path, monkeypatch, FILES, '["download"]')
    folder = tmp_path / "mine" / "models" / "test-finder"
    (folder / f".staging-{'a' * 40}").mkdir(parents=True)
    (folder / f".staging-{'a' * 40}" / "w.pt").write_bytes(b"12345")
    assert readiness.obtained(m) == (False, 5), "a download in progress is never read"
    rev = folder / ("a" * 40)
    rev.mkdir()
    (rev / "w.pt").write_bytes(b"12345")
    assert readiness.obtained(m) == (False, 5), "no receipt: not published"
    (rev / "receipt.json").write_text(json.dumps({"revision": "b" * 40}))
    assert readiness.obtained(m) == (False, 5), "a receipt for another revision"
    (rev / "receipt.json").write_text(json.dumps({"revision": "a" * 40}))
    assert readiness.obtained(m) == (True, 0) and model_files.location(m) == ("folder", 0)
    assert model_files.model_dir("test-finder") == rev and model_files.model_file("test-finder", "w.pt") == rev / "w.pt"


def test_gated_weights_need_the_key_and_licence_only_to_get_them(tmp_path, monkeypatch, fresh_catalog):
    _catalog(tmp_path, monkeypatch, FILES, '["key:HF_TOKEN", "licence:https://huggingface.co/org/test-finder", "download"]')
    r = readiness.model("test-finder", NO_KEYS)
    assert [s.kind for s in r.steps] == ["key", "licence", "download"]
    assert r.word == "Needs your Hugging Face key to download it" and r.next.url == "https://huggingface.co/settings/tokens"
    assert r.steps[1].url == "https://huggingface.co/org/test-finder"
    r = readiness.model("test-finder", {"HF_TOKEN": True})
    assert [s.kind for s in r.steps] == ["licence", "download"]
    _download(tmp_path, b"12345", SHA)
    assert readiness.model("test-finder", NO_KEYS).status == "ready", "a downloaded copy runs without the key"


def test_code_not_installed_is_a_setup_step_with_how(tmp_path, monkeypatch, fresh_catalog):
    from orchestrator import operations

    _catalog(tmp_path, monkeypatch, FILES, '["download"]', '["install:cellpose"]')
    _download(tmp_path, b"12345", SHA)
    monkeypatch.setitem(operations.INSTALLED, "cellpose", lambda: "not installed here (cellpose: `make setup`)")
    r = readiness.model("test-finder", NO_KEYS)
    assert (r.status, r.word, r.next.how) == ("needs-setup", "Install Cellpose", "not installed here (cellpose: `make setup`)")
    monkeypatch.setitem(operations.INSTALLED, "cellpose", lambda: "")
    assert readiness.model("test-finder", NO_KEYS).status == "ready"


def test_not_in_this_edition_is_unavailable_here(monkeypatch):
    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    r = readiness.option("cells", "micro-sam-lm", keys=ALL_KEYS)
    assert r.status == "unavailable" and r.group == "Unavailable here" and r.word == "Not in this edition"
    assert not r.available and r.why == licences.why_not(options.GROUPS["cells"]["micro-sam-lm"].uses)


def test_a_model_whose_last_check_failed_on_this_build_is_broken_and_an_older_failure_is_not():
    ready = readiness.option("vision", "gemini-3.8-flash", keys=ALL_KEYS)
    broken = readiness.broken(ready, {"state": "broken", "why": "it answered nothing"})
    assert (broken.status, broken.word, broken.next.kind) == ("broken", "Broken", "check")
    assert "it answered nothing" in broken.next.says and broken.available, "broken is said, never a refusal of its own"
    assert readiness.broken(ready, {"state": "broken", "why": "x", "earlierBuild": "abc"}) is ready
    assert readiness.broken_wire(ready.wire(), {"state": "broken", "why": "x"})["status"] == "broken"


def test_every_surface_says_the_same(monkeypatch):
    """The Models menu and Capabilities (the capabilities wire), the catalogue, `use`'s refusal and `make doctor` read
    one answer."""
    from orchestrator import capabilities
    from orchestrator.brain.toolbox.choosing import turn_on

    keys = {"GEMINI_API_KEY": True}
    wire = capabilities.wire(options.Settings(), keys)
    offered = options.catalogue(keys)
    for role in wire["roles"]:
        for o in role.get("options", []):
            r = readiness.option(role["id"], o["id"], keys=keys)
            assert o["readiness"] == r.wire() == next(e for e in offered[role["id"]] if e["id"] == o["id"])["readiness"]
            assert (o["available"], o["why"]) == (r.available, r.why)
    fal = readiness.option("outline", "sam3.1-fal", keys=keys)
    assert fal.word == "Needs your fal.ai key"
    monkeypatch.delenv("FAL_KEY", raising=False)
    assert turn_on("outline", "sam3.1-fal").startswith("; to turn it on: add your fal.ai key (put FAL_KEY=...")
    doctor = readiness.doctor()
    assert "SAM 3.1 (cloud) (cloud): Needs your fal.ai key" in doctor and "https://fal.ai/dashboard/keys" in doctor


def test_the_count_line_says_what_each_missing_key_adds():
    from orchestrator import capabilities

    assert "counts" not in capabilities.wire(options.Settings(), {"GEMINI_API_KEY": True})["setup"], "the agent's wire"
    counts = capabilities.wire(options.Settings(), {"GEMINI_API_KEY": True}, counts=True)["setup"]["counts"]
    assert counts["total"] == len(capabilities.load()["capabilities"]) + len(capabilities.contributed(None)) and 0 < counts["works"] < counts["total"]
    added = {u["key"]: u["adds"] for u in counts["unlock"]}
    # The researchers' default is Claude Opus 5.5 since 30 Oct, so the Anthropic key is what unlocks them.
    assert "GEMINI_API_KEY" not in added and added.get("ANTHROPIC_API_KEY", 0) > 0
    more = capabilities.wire(options.Settings(), {"GEMINI_API_KEY": True, "ANTHROPIC_API_KEY": True}, counts=True)["setup"]["counts"]
    assert more["works"] == counts["works"] + added["ANTHROPIC_API_KEY"]


def test_reading_readiness_loads_no_model_starts_nothing_and_calls_no_one(tmp_path):
    """Every option and every catalog model's readiness, and `make doctor`, in a process where opening a socket or
    starting a program fails: and no model library is imported."""
    code = (
        "import socket, subprocess, sys\n"
        "def refuse(*a, **k): raise AssertionError('readiness must not reach the network or start a program')\n"
        "socket.socket.connect = refuse; socket.create_connection = refuse; subprocess.Popen.__init__ = refuse\n"
        "from orchestrator import options, readiness\n"
        "from gpu_worker import manifests as catalog\n"
        "[readiness.option(g, o, keys={}) for g, opts in options.GROUPS.items() for o in opts]\n"
        "[readiness.model(k, {}) for k in catalog.manifests()]\n"
        "readiness.doctor()\n"
        "heavy = {'torch', 'transformers', 'cellpose', 'open_clip', 'sam3', 'micro_sam', 'huggingface_hub', 'trackastra'}\n"
        "print(sorted(heavy & {n.split('.')[0] for n in sys.modules}))\n")
    env = {**os.environ, "MICROMIND_KEYS_FILE": str(tmp_path / "keys.txt")}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                         cwd=Path(catalog.__file__).resolve().parents[1])
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "[]"


def test_the_studios_option_groups_are_the_servers():
    protocol = (Path(catalog.__file__).resolve().parents[2] / "apps" / "studio" / "src" / "protocol.ts").read_text()
    union = re.search(r"export type OptionGroup = ([^;]+);", protocol).group(1)
    assert set(re.findall(r"'([a-z_]+)'", union)) == set(options.GROUPS)


def test_fixed_role_combines_model_and_additional_component_requirements(monkeypatch):
    model_step = readiness.Step('download', 'Download pinned files', model='trackastra', bytes=43)
    key_step = readiness.Step('key', 'Add provider key', service='fal')
    monkeypatch.setattr(readiness, 'model', lambda *args: readiness.Readiness(
        'needs-setup', model_step.says, steps=(model_step,), model='trackastra', obtained=False, missing_bytes=43))
    monkeypatch.setattr(readiness, 'uses', lambda *args: readiness.Readiness(
        'needs-setup', key_step.says, steps=(key_step,), why='needs FAL_KEY'))
    combined = readiness.fixed('trackastra', ('trackastra', 'fal'), {})
    assert combined.status == 'needs-setup' and not combined.available
    assert combined.steps == (model_step, key_step)
    assert combined.model == 'trackastra' and combined.missing_bytes == 43 and combined.obtained is False
    monkeypatch.setattr(readiness, 'uses', lambda *args: readiness.Readiness(
        'unavailable', 'Not in this edition', why='restricted component', steps=(readiness.Step('edition', 'restricted'),)))
    assert readiness.fixed('trackastra', ('extra',), {}).status == 'unavailable'
    assert readiness.fixed('trackastra', ('extra',), {}).why == 'restricted component'


def test_fixed_role_keeps_component_requirements_when_model_is_ready(monkeypatch):
    monkeypatch.setattr(readiness, 'model', lambda *args: readiness.Readiness('ready', 'Ready', model='fixture'))
    monkeypatch.setattr(readiness, 'uses', lambda *args: readiness.Readiness(
        'needs-setup', 'Add key', why='needs FAL_KEY', steps=(readiness.Step('key', 'Add key'),)))
    assert not readiness.fixed('fixture', ('fal',), {}).available


# --- L7b (ticket 62 row 13): one status, the same on every surface ---------------------------------------------------
def test_a_step_left_is_never_choosable_and_says_why(tmp_path, monkeypatch, fresh_catalog):
    """`available` and the status say one thing: a model with only its download (or its licence) left had no `why`, so
    a surface reading `available` offered what the card said to set up."""
    from orchestrator import operations  # noqa: F401 - its implementations load from the real catalog first

    _catalog(tmp_path, monkeypatch, FILES, '["download"]')
    r = readiness.model("test-finder", NO_KEYS)
    assert (r.status, r.word) == ("needs-setup", "Download 1 MB")
    assert not r.available and r.why == "Download 1 MB"


@pytest.mark.parametrize("keys", [NO_KEYS, ALL_KEYS], ids=["no keys", "every key"])
def test_every_option_is_choosable_exactly_when_its_status_allows(keys):
    for group, opts in options.GROUPS.items():
        for o in opts.values():
            r = readiness.option(group, o.id, keys=keys)
            assert r.available == (r.status in ("ready", "broken")), f"{group}/{o.id}: {r.status} but available={r.available}"


def test_an_option_says_this_macs_fit_as_its_model_does(monkeypatch):
    """A model this Mac can't run is Unavailable here on its card, its option (the menu, Actions, `use`) and at run time."""
    from orchestrator.operations import OPERATIONS

    real = readiness.misfit
    monkeypatch.setattr(readiness, "misfit", lambda m: "it needs 512 GB of memory (this Mac has 64 GB)"
                        if m.key == "cellpose-sam" else real(m))
    card = readiness.model("cellpose-sam", ALL_KEYS)
    option = readiness.option("cells", "cellpose-sam", keys=ALL_KEYS)
    assert (card.status, card.word) == (option.status, option.word) == ("unavailable", "Not on this Mac")
    assert not option.available and "512 GB" in option.why
    impl = next(i for i in OPERATIONS["cells"].implementations if i.id == "cellpose-sam")
    assert "512 GB" in readiness.implementation(impl), "the runner refuses what the card and menu say can't run here"


def test_the_run_time_check_reads_a_key_its_service_refused(monkeypatch):
    """A key present but refused when tested is no key (observed.py) for the runner too, as for the menu."""
    from orchestrator import observed
    from orchestrator.operations import OPERATIONS

    impl = next(i for i in OPERATIONS["outline"].implementations if i.id == "sam3.1-fal")
    monkeypatch.setenv("FAL_KEY", "x")
    real = observed.latest
    monkeypatch.setattr(observed, "latest", lambda kind, key, *a, **k: (
        {"state": "no", "says": "fal.ai refused it", "at": ""} if (kind, key) == ("key", "FAL_KEY") else real(kind, key, *a, **k)))
    assert readiness.implementation(impl) == "needs FAL_KEY"
    assert not readiness.option("outline", "sam3.1-fal", keys={"FAL_KEY": True}).available


def test_every_status_is_said_in_one_sentence():
    said = readiness.said
    assert said("ready", "Ready") == "Ready"
    assert said("needs-setup", "Download 1.2 GB", "not downloaded yet: 1.2 GB") == \
        "Needs setup: Download 1.2 GB (not downloaded yet: 1.2 GB)"
    assert said("needs-setup", "Needs your fal.ai key", "needs FAL_KEY") == "Needs setup: Needs your fal.ai key (needs FAL_KEY)"
    assert said("unavailable", "Not on this Mac", "it runs on a Mac with Apple Silicon") == \
        "Unavailable here: Not on this Mac (it runs on a Mac with Apple Silicon)"
    ready = readiness.option("vision", "gemini-3.8-flash", keys=ALL_KEYS)
    broken = readiness.broken_wire(ready.wire(), {"state": "broken", "why": "it answered nothing"})
    assert readiness.said_wire(broken) == "Broken: Its last check failed: it answered nothing"
    assert readiness.said_wire(ready.wire()) == "Ready"
    assert readiness.said_wire(readiness.option("vision", "gemini-3.8-flash", keys=NO_KEYS).wire(), "needs GEMINI_API_KEY") \
        == "Needs setup: Needs your Google Gemini API key (needs GEMINI_API_KEY)"


def test_a_download_that_needs_a_key_first_says_the_key_first(tmp_path, monkeypatch):
    """The status and the reason name one next step: a gated model not downloaded and without its key said "Needs your
    Hugging Face key" on its card but only "not downloaded yet" in Actions and the agent's refusals."""
    monkeypatch.setenv("MICROMIND_MODEL_FILES", str(tmp_path / "files"))
    r = readiness.option("zeroshot", "medsiglip-subject", keys=NO_KEYS)
    assert r.next.kind == "key" and r.word.startswith("Needs your Hugging Face key")
    assert r.why.startswith("not downloaded yet: ") and r.why.endswith("; first add your Hugging Face key to download it")
    keyed = readiness.option("zeroshot", "medsiglip-subject", keys={"HF_TOKEN": True})
    assert "first add your" not in keyed.why
