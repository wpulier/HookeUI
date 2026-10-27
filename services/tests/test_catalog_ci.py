"""The repository's check of catalog entries (bench/catalog_ci.py) says each passed proof in its own words: a job's proof
(jobs.prove) has `says` and no `found` (after brainreg's job proof passed, printing it raised KeyError: 'found')."""
from types import SimpleNamespace

import pytest

from bench import catalog_ci


@pytest.fixture
def entry(tmp_path, monkeypatch):
    """One python-env entry whose files, environment and proof are stand-ins: only what check() prints is under test."""
    from gpu_worker import manifests as catalog
    from orchestrator import downloads, env_runner, envs, publish

    (tmp_path / "brainreg").mkdir()
    (tmp_path / "brainreg" / catalog.MANIFEST).write_text("")
    monkeypatch.setattr(catalog, "ROOT", tmp_path)
    monkeypatch.setattr(catalog, "read", lambda path: (SimpleNamespace(files=[], needs_to_run=[], runner="python-env"), []))
    monkeypatch.setattr(publish, "problems", lambda m, environ, proven=False: [])
    monkeypatch.setattr(downloads, "download", lambda m, environ=None: None)
    monkeypatch.setattr(envs, "build", lambda m, environ, say=None: None)
    proofs = []
    monkeypatch.setattr(env_runner, "prove", lambda m, environ: proofs.pop(0))
    return proofs


def test_a_passed_job_proof_is_said_by_its_own_words(entry, capsys):
    entry.append({"ok": True, "says": "ran on its example in 193 s", "outputs": ["registered_atlas.tiff"],
                  "resources": {}, "seconds": 192.6, "at": "2026-10-14T16:05:00"})  # jobs.prove's shape: no found
    assert catalog_ci.check("brainreg", static=False, environ={}) == []
    assert "brainreg: its proof: ran on its example in 193 s; not scientific qualification" in capsys.readouterr().out


def test_an_adapter_proof_is_still_said_by_what_it_found(entry, capsys):
    entry.append({"ok": True, "found": 12, "picture": "512x512", "seconds": 1.4,
                  "input": {"evidenceClass": "generated-fixture", "identity": "cells-512"}})
    assert catalog_ci.check("brainreg", static=False, environ={}) == []
    assert ("adapter contract smoke, found 12 on generated fixture cells-512 (512x512) in 1.4 s"
            in capsys.readouterr().out)


@pytest.fixture
def lock_change(monkeypatch):
    """A change to services/uv.lock alone, with Molmo 2's pinned code read as importing einops and torch."""
    from bench import catalog_pin
    from orchestrator import downloads

    monkeypatch.setattr(catalog_ci, "changed", lambda base: [])
    monkeypatch.setattr(catalog_ci, "lock_changed", lambda base: True)
    monkeypatch.setattr(catalog_pin, "code_imports", lambda repo, revision, files: {"einops", "torch"})

    def no_download(*a, **k):
        raise AssertionError("a lock change downloads no weights")

    monkeypatch.setattr(downloads, "download", no_download)
    monkeypatch.setattr(catalog_ci, "check", lambda *a, **k: pytest.fail("a lock change proves no entry"))


def test_a_lock_change_checks_the_code_imports_of_built_in_models_and_downloads_nothing(lock_change, monkeypatch, capsys):
    from orchestrator import shared_python

    assert catalog_ci.with_code() == ["molmo2-4b"]  # the built-in models that pin code of their own
    monkeypatch.setattr(shared_python, "locked", lambda lock=None: {"torch": "2.9.0"})  # a lock that lost einops
    assert catalog_ci.main(["--base", "origin/main"]) == 1
    out = capsys.readouterr().out
    assert "molmo2-4b (its code's imports): failed" in out and "its pinned code imports einops: " in out
    assert "0 of 1 passed" in out
    monkeypatch.setattr(shared_python, "locked", lambda lock=None: {"torch": "2.9.0", "einops": "0.8.2"})
    assert catalog_ci.main(["--base", "origin/main"]) == 0


def test_a_built_in_entrys_code_imports_are_checked_before_any_download(monkeypatch):
    from bench import catalog_pin
    from orchestrator import shared_python

    monkeypatch.setattr(catalog_pin, "code_imports", lambda repo, revision, files: {"surely_no_such_module_here"})
    why = catalog_ci.check("molmo2-4b", static=True, environ={})
    assert why == ["its pinned code imports surely_no_such_module_here: " + shared_python.unlocked(
        {"surely_no_such_module_here"})["surely_no_such_module_here"]]
    assert catalog_ci.check("edgetam", static=True, environ={}) == []  # no code of its own: nothing to read
