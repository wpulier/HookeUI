"""The catalog (catalog/models/<id>/model.toml, gpu_worker/manifests.py; docs/design/2026-10-12-the-open-catalog.md,
ADR 0018): every list in the code that names a model this Mac loads agrees with its manifest, so a model is described
once and a new one fails here until it has its folder. An entry that can't be read is contained, the user's own entries
never take a reviewed one's id, and reading the catalog loads no model library."""
import subprocess
import sys
from pathlib import Path

from gpu_worker import manifests as catalog
from gpu_worker.manifests import DATA_TYPES, HOSTS, RUNS, WORKERS, local, manifests, services


def local_models():
    """(operation, implementation) of every model an operation runs on one of this Mac's GPU workers."""
    from orchestrator.operations import OPERATIONS

    return [(op.id, impl) for op in OPERATIONS.values() for impl in op.implementations
            if impl.kind == "model" and impl.worker in WORKERS]


def test_every_reviewed_entry_reads_cleanly(monkeypatch, tmp_path):
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path))  # this Mac's own entries are not the repository's to check
    assert catalog.problems() == {}
    folders = {p.name for p in catalog.ROOT.iterdir() if p.is_dir()}
    assert folders == set(manifests()), "a folder with no model.toml, or one that did not read"


def test_every_model_the_worker_loads_has_a_manifest_and_nothing_else_does():
    from gpu_worker import loading, worker

    # "cellpose-file" is the runner of the user's own Cellpose models (orchestrator/mine.py): their entries are theirs.
    loaded = {*worker.POINT_MODELS, *(set(worker.CELL_FINDERS) - {"cellpose-file"}), *worker.EMBED_MODELS, *worker.SEGMENT_MODELS,
              "sam3.1-local", "edgetam-video", "medsiglip", "molmo2-4b",
              loading.SEGMENT_DEFAULT, loading.CELLS_DEFAULT, loading.EMBED_DEFAULT}
    assert loaded == set(local()), "a model the worker loads with no manifest, or a manifest for none"
    for model in ("track", "zeroshot", "molmo_point", "regions"):
        assert loading.model_of(model, {}) in local()


def test_every_local_implementation_is_listed_by_the_manifest_of_the_model_it_loads_and_agrees_with_it():
    from orchestrator.operations import LOAD_KEYS

    listed = {impl: m for m in local().values() for impl in m.implementations}
    assert len(listed) == sum(len(m.implementations) for m in local().values()), "an implementation listed twice"
    for op, impl in local_models():
        key = f"{op}/{impl.id}"
        m = listed.get(key)
        assert m is not None, f"{key}: no manifest lists it"
        assert m.id == LOAD_KEYS.get(impl.id, impl.id), f"{key}: listed by {m.id}, but it loads {LOAD_KEYS.get(impl.id, impl.id)}"
        assert impl.worker in m.workers, f"{key}: runs on the {impl.worker} worker, its manifest says {m.workers}"
        assert set(impl.uses) == set(m.licence), f"{key}: uses {impl.uses}, its manifest {m.licence}"
        assert impl.installed == m.installed, f"{key}: installs {impl.installed!r}, its manifest {m.installed!r}"
        assert not impl.needs or impl.needs in m.keys(), f"{key}: needs {impl.needs}, which its manifest never names"
        assert not impl.weights or f"weights:{impl.weights}" in m.needs_to_run, f"{key}: its weights are not a need"
        assert impl.version == m.version, f"{key}: version {impl.version!r}, its manifest {m.version!r}"
        if impl.id == m.id:
            assert impl.label == m.name, f"{key}: called {impl.label!r}, its manifest {m.name!r}"
    assert set(listed) == {f"{op}/{impl.id}" for op, impl in local_models()}


def test_what_a_manifest_names_exists():
    from gpu_worker import worker
    from orchestrator.licences import COMPONENTS
    from orchestrator.jobs import OPERATIONS as JOB_OPERATIONS
    from gpu_worker.manifests import KIND_OUTPUTS
    from orchestrator.operations import INSTALLED, OPERATIONS

    components = set(COMPONENTS) | {m.id for m in manifests().values() if m.component}  # a contributed entry's own
    from orchestrator import kinds

    door = set(kinds.backends())  # a capability through the one door (ADR 0024): proven, then registered, no app code
    for m in manifests().values():
        through = m.runner == "python-env" or (m.contract and m.runner in door)
        assert set(m.licence) <= components, f"{m.id}: licence {m.licence} not in licences.COMPONENTS"
        assert not m.installed or m.installed in INSTALLED or through, (  # its own env or proof: kinds.backends
            f"{m.id}: installed {m.installed!r} is no INSTALLED key")
        for i in m.implements:  # a python-env entry's implementation is registered once proven (env_runner.register)
            assert i.operation in JOB_OPERATIONS or (through and (i.output in KIND_OUTPUTS or m.contract)) or (
                i.operation in OPERATIONS and (through or OPERATIONS[i.operation].impl(i.impl))), (
                f"{m.id}: no {i.key} to implement")  # a new kind of job brings its own operation (kinds.py)
    for m in local().values():
        assert m.runs in RUNS and m.host in HOSTS and set(m.workers) <= set(WORKERS) and m.workers, m.id
        assert set(m.protected_on) <= set(m.workers), f"{m.id}: protected where it never runs"
        assert set(m.licence) <= components, f"{m.id}: licence {m.licence} not in licences.COMPONENTS"
        assert not m.installed or m.installed in INSTALLED or m.runner == "python-env", m.id
        assert m.documented_mb > 0 and m.load_limit_s > 0 and (not m.peak_mb or m.peak_mb >= m.documented_mb), m.id
        for i in m.implements:
            assert i.operation in OPERATIONS and OPERATIONS[i.operation].impl(i.impl), f"{m.id}: no {i.key} to implement"
            assert i.input in DATA_TYPES and i.output in DATA_TYPES, f"{m.id}: {i.key}'s data types"
        if m.id in worker.POINT_MODELS:
            assert worker.POINT_MODELS[m.id] == m.version, f"{m.id}: the worker loads {worker.POINT_MODELS[m.id]}"


def test_every_reviewed_entry_says_what_it_is_where_it_comes_from_and_who_made_it():
    for m in manifests().values():
        assert m.what and (m.runner or m.pins.get("adapter") or m.kind == "data") and m.source, (
            f"{m.id}: what it is, how it runs (a data entry runs nothing), its source")
        assert m.credit.get("authors") and m.credit.get("link"), f"{m.id}: its credit"
        assert (m.runs == "cloud" or "download" in m.needs_to_get or m.source.get("made_here")
                or (m.source.get("tool") and manifests().get(m.source["tool"]))), f"{m.id}: how it is obtained"
        if m.source.get("hf"):
            assert len(m.source["revision"]) == 40, f"{m.id}: a Hugging Face source pins its full revision"


def test_gated_weights_need_a_key_to_get_not_to_run():
    for model in ("sam3.1-local", "dinov3-small", "medsiglip"):
        m = manifests()[model]
        assert "key:HF_TOKEN" in m.needs_to_get and any(n.startswith("licence:https://huggingface.co/") for n in m.needs_to_get)
        assert "key:HF_TOKEN" not in m.needs_to_run, f"{model}: a downloaded copy runs without the key"


def test_the_live_views_models_are_protected_and_the_worker_tables_come_from_the_manifests():
    from gpu_worker import loading
    from orchestrator.loads import LOCAL_OUTLINE

    assert manifests()["edgetam"].protected_on == ("interactive",)
    assert manifests()["edgetam-video"].protected_on == ("follow",)
    assert LOCAL_OUTLINE == {"sam2.1-tiny": "interactive", "sam3.1-local": "analysis", "micro-sam-lm": "analysis"}
    for m in local().values():
        assert loading.NAMES[m.id] == m.name and loading.DOCUMENTED_MB[m.id] == m.documented_mb
        assert loading.limit_s(m.id) == m.load_limit_s


def _entry(root: Path, model: str, text: str) -> None:
    (root / model).mkdir(parents=True)
    (root / model / "model.toml").write_text(text)


GOOD = '''id = "{id}"
name = "A test model"
runs = "this-mac"
version = "org/repo"
licence = ["edgetam"]
kind = "model"
what = "Outlines what you click"
runner = "gpu-worker"
source = {{ url = "https://example.org/m" }}
files = [{{ path = "w.pt", bytes = 5, sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", url = "https://example.org/w.pt" }}]
needs_to_get = ["download"]
workers = ["analysis"]
documented_mb = 100
load_limit_s = 60

[[implements]]
operation = "outline"
impl = "{id}"
input = "image.2d.rgb8"
output = "masks.2d"
'''


def test_an_entry_that_cant_be_read_says_why_and_the_rest_load(monkeypatch, tmp_path):
    root = tmp_path / "models"
    _entry(root, "fine", GOOD.format(id="fine"))
    _entry(root, "typo", "colour = 1\n" + GOOD.format(id="typo"))
    _entry(root, "broken", "id = \"broken\"\nname = [unclosed\n")
    _entry(root, "elsewhere", GOOD.format(id="not-its-folder"))
    monkeypatch.setattr(catalog, "ROOT", root)
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "none"))
    found, problems = catalog._scan(root, "")
    assert set(found) == {"fine"}
    assert problems["typo"] == ["unknown key `colour`"]
    assert "not valid TOML" in problems["broken"][0]
    assert problems["elsewhere"] == ["its id is 'not-its-folder', its folder 'elsewhere'"]
    manifest, why = catalog.read(root / "typo" / "model.toml")
    assert manifest is None and why == ["unknown key `colour`"]
    (root / "typo" / "model.toml").write_text(GOOD.format(id="typo").replace('output = "masks.2d"', 'output = "mask"'))
    assert catalog.read(root / "typo" / "model.toml")[1] == [
        "`implements` outline/typo: output 'mask' is no data type (catalog/README.md)"]


def test_the_users_own_entries_are_namespaced_and_never_take_a_reviewed_id(monkeypatch, tmp_path):
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path))
    mine = tmp_path / "mine" / "models"
    _entry(mine, "my-finder", GOOD.format(id="my-finder"))
    _entry(mine, "edgetam", GOOD.format(id="edgetam"))
    assert set(catalog.mine()) == {"mine/my-finder"}
    assert catalog.mine()["mine/my-finder"].key == "mine/my-finder"
    assert catalog.problems() == {"mine/edgetam": ["its id 'edgetam' is a reviewed entry's: give it another"]}
    assert "my-finder" not in manifests() and manifests()["edgetam"].namespace == ""


def test_reading_the_catalog_loads_no_model_library():
    code = ("import sys; from gpu_worker import manifests as m; m.manifests(); m.problems(); m.mine(); "
            "heavy = {'torch', 'transformers', 'cellpose', 'open_clip', 'sam3', 'micro_sam', 'huggingface_hub', 'cv2', "
            "'numpy', 'orchestrator'} & {name.split('.')[0] for name in sys.modules}; print(sorted(heavy))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         cwd=Path(catalog.__file__).resolve().parents[1])
    assert out.stdout.strip() == "[]"


# --- S2: cloud models and services join the catalog; the code's lists are read from it ------------------------------
def test_every_service_reads_cleanly_and_the_key_lists_are_read_from_them():
    from gpu_worker.manifests import service_problems
    from orchestrator import capabilities, keys
    from orchestrator.licences import COMPONENTS

    assert service_problems() == {}
    assert set(keys.KNOWN_KEYS) == {s.key for s in services().values()}
    assert capabilities.KEYS == {s.licence: s.key for s in services().values() if s.licence}
    for s in services().values():
        assert s.get_key.startswith("https://") and s.test and s.costs, f"{s.id}: where to get a key, its test, its costs"
        assert s.enables and len(s.enables.split()) <= 16, f"{s.id}: what its key turns on, in a line"
        assert not s.licence or s.licence in COMPONENTS, f"{s.id}: licence {s.licence!r}"


def test_every_cloud_model_names_its_service_and_needs_its_key():
    cloud = [m for m in manifests().values() if m.runs == "cloud"]
    assert len(cloud) >= 14
    for m in cloud:
        service = services()[m.service]
        assert m.kind == "cloud-model" and m.runner == "cloud" and m.needs == service.key, m.id
        assert service.licence in m.licence, f"{m.id}: uses {service.name} without its licence component"


# Options that run no one model: a composite of two (its steps are each a model's), "lean" (whatever the fast setting
# uses) and the choices that turn something off or leave it to another setting.
NOT_ONE_MODEL = {"edgetam>sam3.1-fal", "edgetam>sam3.1-local", "lean", "after-voice", "off", "auto", "same"}
MODEL_ROLES = ("vision", "outline", "voice", "research", "presentation", "image", "sight", "fast", "cells", "embed",
               "zeroshot", "outline_repair", "reflex")


def test_every_model_option_is_a_use_of_a_catalog_model_and_takes_its_key_and_licence_from_it():
    from orchestrator import options

    for group in MODEL_ROLES:
        for option in options.GROUPS[group].values():
            if option.id in NOT_ONE_MODEL:
                continue
            entry = manifests().get(option.model)
            assert entry is not None, f"{group}/{option.id}: names no catalog model"
            assert set(option.uses) == set(entry.licence), f"{group}/{option.id}: its licence components"
            assert option.needs in ("", entry.needs, *entry.keys()), f"{group}/{option.id}: needs {option.needs}"
            if option.spec:
                assert option.spec.partition(":")[2].partition("@")[0] == entry.version, f"{group}/{option.id}: its spec"
    assert options.REFLEX_MODELS == {"lean-3.5-flash": "gemini-3.5-flash", "lean-lite": "gemini-3.5-flash-lite"}


def test_no_model_is_named_in_the_code_outside_the_catalog():
    """Every model a constant, a fixed role or a price names is a catalog entry (by id, by the provider's own name for it,
    by an implementation's version or by its weights' name)."""
    from orchestrator import capabilities
    from orchestrator.operations import OPERATIONS
    from research_worker import budget

    names = {n for m in manifests().values() for n in (m.id, m.version, m.source.get("weights", ""))} - {""}
    names |= {i.version for op in OPERATIONS.values() for i in op.implementations if i.kind == "model" and i.version}
    for role in capabilities.load()["roles"]:
        for entry in (role, role.get("fallback") or {}):
            if entry.get("source"):
                value = str(capabilities.constant(entry["source"]))
                assert value in names, f"{role['id']}: {value!r} is in no catalog folder"
    tools = {"google_search", "web_search"}  # priced search tools, not models
    assert set(budget.PRICES) - tools <= names, f"priced but not in the catalog: {set(budget.PRICES) - tools - names}"


def test_every_catalog_model_is_used_by_something():
    from orchestrator import capabilities, options
    from orchestrator import kinds
    from orchestrator.operations import OPERATIONS

    used = {o.model for group in options.GROUPS.values() for o in group.values() if o.model}
    implemented = {f"{op.id}/{i.id}" for op in OPERATIONS.values() for i in op.implementations}
    fixed = {str(capabilities.constant(e["source"])) for r in capabilities.load()["roles"]
             for e in (r, r.get("fallback") or {}) if e.get("source")}
    for m in manifests().values():
        assert (m.id in used or set(m.implementations) & implemented or {m.id, m.version} & fixed
                or m.source.get("weights") in fixed or m.id == "molmo2-4b"
                or any(o.source.get("tool") == m.id for o in manifests().values())
                or m.runner == "python-env"  # its own adapter: a choice once proven, or a job
                or (m.contract and m.runner in kinds.backends())  # a capability through the one door (ADR 0024)
                or any(f"data:{m.id}" in o.needs_to_run for o in manifests().values())), f"{m.id}: nothing uses it"


def test_a_download_that_names_no_files_is_a_mistake(tmp_path):
    """The catalog audit, 2026-10-12: Trackastra said "download" and pinned nothing, so nothing downloaded it and its
    library fetched its own weights on first use, unpinned and outside the catalog. A download names its files."""
    root = tmp_path / "models"
    text = GOOD.format(id="nothing")
    _entry(root, "nothing", "\n".join(line for line in text.splitlines() if not line.startswith("files = ")) + "\n")
    found, problems = catalog._scan(root, "")
    assert "nothing" not in found and any("pins no files" in p for p in problems["nothing"])


def test_installed_news_fields_are_contained_per_entry(monkeypatch, tmp_path):
    root = tmp_path / 'models'
    _entry(root, 'fine', GOOD.format(id='fine'))
    for key, field in [('bad-tags', 'tags = 1'), ('bad-by', 'contributed_by = 1'), ('bad-example', 'example = 3')]:
        _entry(root, key, GOOD.format(id=key).replace('[[implements]]', field + '\n[[implements]]', 1))
        value, problems = catalog.read(root / key / 'model.toml')
        assert value is None and problems, 'invalid metadata must be a contained entry problem'
    monkeypatch.setattr(catalog, 'ROOT', root)
    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path / 'private-catalog'))
    catalog.manifests.cache_clear()
    try:
        assert set(catalog.manifests()) == {'fine'}
        assert set(catalog.problems()) == {'bad-tags', 'bad-by', 'bad-example'}
    finally:
        catalog.manifests.cache_clear()
