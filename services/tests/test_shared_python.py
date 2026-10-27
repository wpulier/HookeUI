"""The app's own Python as an environment (orchestrator/shared_python.py): what the code run in it imports is read from
that code and must come from services/uv.lock; nothing installs beside the lock what the lock holds; readiness says when a
built-in model's pinned code needs a package this Python lacks. Live 2026-10-19 (PR #346): Molmo 2's pinned code
imported einops, which only another model's setup had put here, and SAM 3.1's setup re-pinned two locked packages."""
import re
from pathlib import Path

import pytest

from gpu_worker import manifests as catalog
from orchestrator import operations, readiness, shared_python

SERVICES = Path(__file__).resolve().parent.parent
REPO = SERVICES.parent

# Code that runs in a Python of its own, never the app's: what it imports is its own environment's (envs.py).
OWN_PYTHON_CODE = {
    "noncommercial/microsam_server.py": "micro-sam's own Python (catalog/models/micro-sam-lm, its env)",
    "noncommercial/trackastra_server.py": "Trackastra's own Python (catalog/models/trackastra, its env)",
    "orchestrator/jupyter_kernel.py": "Jupyter's own Python (it has ipykernel; the kernelspec runs this file there, docs/jupyter.md)",
}


def test_imports_are_read_from_the_code_as_transformers_reads_remote_code():
    source = """
import os, einops
import torch.nn as nn
from transformers.utils import logging
from .configuration_molmo2 import Molmo2Config
try:
    import flash_attn
except ImportError:
    flash_attn = None
if is_flash_attn_2_available():
    from flash_attn import flash_attn_func
else:
    import xformers_fallback
def later():
    import requests
"""
    assert shared_python.source_imports(source) == {"os", "einops", "torch", "transformers", "xformers_fallback",
                                                    "requests"}
    assert shared_python.third_party({"os", "einops", "orchestrator", "configuration_molmo2", "__future__"},
                                     own={"configuration_molmo2"}) == {"einops"}


def _lock(tmp_path: Path, *names: str) -> Path:
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1\n" + "".join(f'[[package]]\nname = "{n}"\nversion = "1.0"\n' for n in names))
    return lock


def test_a_module_is_unlocked_unless_the_lock_provides_it(tmp_path):
    lock = _lock(tmp_path, "numpy", "PyYAML")
    found = shared_python.unlocked({"numpy", "yaml", "pytest", "surely_no_such_module_here"}, lock=lock)
    assert set(found) == {"pytest", "surely_no_such_module_here"}  # yaml is PyYAML's: matched by canonical name
    assert "services/uv.lock doesn't hold it" in found["pytest"]
    assert "holds no distribution of that name" in found["surely_no_such_module_here"]
    assert shared_python.unlocked({"surely_no_such_module_here"}, allowed={"surely_no_such_module_here"}, lock=lock) == {}


def test_not_installed_here_is_never_said_as_not_in_the_lock(tmp_path):
    """Whether this Python has it and whether the lock holds it are two facts: a locked package the Python lacks (a
    `uv sync` of the default group only) is missing here, not unlocked (review of #347)."""
    lock = _lock(tmp_path, "surely-locked-module")
    assert shared_python.unlocked({"surely_locked_module"}, lock=lock) == {}
    assert shared_python.missing({"surely_locked_module", "numpy"}) == ["surely_locked_module"]


def test_every_module_the_apps_own_code_imports_comes_from_the_lock_or_an_install_step_it_says():
    """A package the app's code imports that only some other install put here works on one Mac and fails on a fresh setup
    and in the Mac app (einops, 2026-10-19). Each one must be in services/uv.lock, or be an install step the catalog says
    (operations.INSTALLED: readiness gives its step), or be imported only by code that runs in a Python of its own."""
    offenders = []
    for root in ("orchestrator", "gpu_worker", "research_worker", "noncommercial", "bench", "sim"):
        for path in sorted((SERVICES / root).rglob("*.py")):
            rel = str(path.relative_to(SERVICES))
            if rel in OWN_PYTHON_CODE:
                continue
            needed = shared_python.third_party(shared_python.source_imports(path.read_text()))
            for module, why in shared_python.unlocked(needed, allowed=operations.INSTALLED).items():
                offenders.append(f"{rel}: {module}: {why}")
    assert not offenders, ("add it to services/pyproject.toml (`uv lock`), or give it an install step:\n"
                           + "\n".join(offenders))
    assert all((SERVICES / rel).is_file() for rel in OWN_PYTHON_CODE)


def _installs_beside_the_lock() -> list[tuple[str, str]]:
    """Every package a setup command installs into the app's Python by hand (`uv pip install`), with where."""
    found = []
    sources = [(p, p.read_text()) for p in sorted((REPO / "scripts").glob("*.sh"))] + [(REPO / "Makefile", (REPO / "Makefile").read_text())]
    for path, text in sources:
        for line in text.splitlines():
            if "uv pip install" not in line or line.lstrip().startswith("#"):
                continue
            words = line.split("uv pip install", 1)[1].split()
            skip = False
            for word in words:
                if skip:
                    skip = False
                    continue
                if word in ("--python", "-e", "--editable", "-r"):
                    skip = True
                    continue
                if word.startswith("-") or "$" in word:
                    continue
                name = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", word.strip("\"'"))
                if name:
                    found.append((name.group(0), path.name))
    return found


def test_nothing_installed_beside_the_lock_is_a_package_the_lock_holds():
    """Two owners of one package fight: `make sam3-setup` pinned setuptools<81 and ftfy==6.1.1, the next `make setup` moved
    them back to the lock's, and SAM 3.1 stopped importing. A setup step adds only what the lock lacks."""
    installs = _installs_beside_the_lock()
    assert ("iopath", "setup-sam3.sh") in installs and ("trackastra", "Makefile") in installs  # the reader still reads them
    held = shared_python.locked()
    clashes = [f"{name} ({where})" for name, where in installs if shared_python.canonical(name) in held]
    assert not clashes, "installed by hand although services/uv.lock holds it: " + ", ".join(clashes)


@pytest.fixture(autouse=True)
def _fresh_answers():
    shared_python._said.clear()
    yield
    shared_python._said.clear()


def _molmo_needs(monkeypatch, modules):
    monkeypatch.setattr(shared_python, "pinned_code_imports", lambda m, environ=None: frozenset(modules))


def test_readiness_says_what_a_built_in_models_pinned_code_imports_and_this_python_lacks(monkeypatch):
    _molmo_needs(monkeypatch, {"numpy", "surely_no_such_module_here"})
    r = readiness.model("molmo2-4b")
    assert (r.status, r.word) == ("needs-setup", readiness.PINNED_CODE)
    assert "surely_no_such_module_here: its pinned code imports it" in r.next.how
    o = readiness.option("pointing", "molmo")  # the one use of it the app chooses: it can't be chosen, and says why
    assert o.status == "needs-setup" and "surely_no_such_module_here" in o.why
    _molmo_needs(monkeypatch, {"numpy"})
    assert readiness.model("molmo2-4b").status == "needs-setup"  # kept: nothing about this Python changed
    shared_python._said.clear()
    assert readiness.model("molmo2-4b").status == "ready"


def test_the_answer_is_kept_until_this_python_or_the_revision_changes(monkeypatch):
    """Every card, option and refusal asks it, outside readiness.batch too: asked again, it costs a few stats."""
    asked = []
    monkeypatch.setattr(shared_python, "pinned_code_imports",
                        lambda m, environ=None: asked.append(m.id) or frozenset({"surely_no_such_module_here"}))
    m = catalog.get("molmo2-4b")
    first = shared_python.missing_from_pinned_code(m)
    assert "surely_no_such_module_here" in first
    assert [shared_python.missing_from_pinned_code(m) for _ in range(50)] == [first] * 50 and asked == ["molmo2-4b"]
    monkeypatch.setattr(shared_python, "_python_identity", lambda: ("a package was installed",))
    assert shared_python.missing_from_pinned_code(m) == first and asked == ["molmo2-4b"] * 2
    assert shared_python.missing_from_pinned_code(catalog.get("edgetam")) == ""  # no code of its own: nothing asked
    assert asked == ["molmo2-4b"] * 2


def test_a_pinned_code_files_imports_are_read_from_the_downloaded_revision(tmp_path, monkeypatch):
    from gpu_worker import model_files

    m = catalog.get("molmo2-4b")
    (tmp_path / "modeling_molmo2.py").write_text("import einops\nfrom .configuration_molmo2 import X\nimport torch\n")
    (tmp_path / "configuration_molmo2.py").write_text("import json\nfrom transformers import PretrainedConfig\n")
    monkeypatch.setattr(model_files, "standin", lambda environ=None: False)
    monkeypatch.setattr(model_files, "model_dir", lambda model_id, environ=None: tmp_path)
    shared_python._pinned.clear()
    assert shared_python.pinned_code_imports(catalog.get("edgetam")) is None  # no code of its own pinned
    files = [f for f in m.files if f["path"] in ("modeling_molmo2.py", "configuration_molmo2.py")]
    monkeypatch.setattr(model_files, "files", lambda _m: files if _m.id == m.id else _m.files)
    assert shared_python.pinned_code_imports(m) == {"einops", "torch", "transformers"}
    shared_python._pinned.clear()


def test_drift_says_how_this_python_differs_from_its_lock(tmp_path):
    d = shared_python.drift(_lock(tmp_path, "numpy"))
    assert ("pytest", __import__("pytest").__version__) in d["extra"]
    assert any(n == "numpy" for n, *_ in d["differs"])  # the lock above says 1.0
    line = shared_python.says(d)
    assert line.startswith(f"the app's Python (services/uv.lock {d['lock']}) differs") and "beyond the lock: " in line
    assert shared_python.says({"lock": "abc", "extra": [], "differs": []}) == \
        "the app's Python (services/uv.lock abc) matches its lock"


@pytest.mark.parametrize("entry", sorted(m.id for m in catalog.local().values()))
def test_every_built_in_model_runs_in_the_python_its_readiness_reads(entry):
    """Every runner gpu-worker entry is read by this check (no other runner shares the app's Python)."""
    m = catalog.get(entry)
    assert m.runner == "gpu-worker" and shared_python.missing_from_pinned_code(m) == ""  # stand-in files: nothing read
