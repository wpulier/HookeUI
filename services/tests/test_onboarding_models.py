"""The small-models offer's answer (orchestrator/onboarding.py models_offer; Will, 2026-10-26): asked once in the
workspace, kept in the data home so it never asks again. Only the answer is kept: nothing here downloads."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestrator import commands, produces
from orchestrator.brain.registry import ToolFailed
from orchestrator.onboarding import answer_models_offer, models_offer, models_offer_path


def run(app, **msg):
    return asyncio.run(commands.COMMANDS["onboarding-models"](app, None, msg))


def test_unanswered_until_a_person_answers(tmp_path):
    app = SimpleNamespace(paths=SimpleNamespace(home=tmp_path))
    assert run(app) == {"ok": True, "offer": {"answered": False, "answer": None, "at": None, "models": []}}
    assert not models_offer_path(tmp_path).exists()  # asking writes nothing


def test_not_now_is_kept_in_the_data_home_and_downloads_nothing(tmp_path, monkeypatch):
    app = SimpleNamespace(paths=SimpleNamespace(home=tmp_path))
    offer = run(app, action="not-now", models=["edgetam"])["offer"]
    assert offer["answered"] and offer["answer"] == "not-now" and offer["models"] == []
    saved = json.loads((tmp_path / "onboarding" / "models.json").read_text())
    assert saved["answer"] == "not-now" and saved["at"]
    assert run(app)["offer"]["answered"]  # never asks again: the next status reads it back


def test_download_keeps_the_models_chosen(tmp_path):
    offer = answer_models_offer(tmp_path, "download", ["edgetam", "dinov2-small", "edgetam"])
    assert offer["answer"] == "download" and offer["models"] == ["edgetam", "dinov2-small"]
    assert models_offer(tmp_path) == offer


def test_unknown_answers_and_models_are_refused(tmp_path):
    with pytest.raises(ToolFailed):
        answer_models_offer(tmp_path, "maybe")
    with pytest.raises(ToolFailed, match="no-such-model"):
        answer_models_offer(tmp_path, "download", ["no-such-model"])
    assert not models_offer_path(tmp_path).exists()


def test_an_unreadable_record_asks_again(tmp_path):
    path = models_offer_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert models_offer(tmp_path)["answered"] is False
    path.write_text(json.dumps({"answer": "later"}))
    assert models_offer(tmp_path)["answered"] is False


def test_the_command_says_it_makes_nothing():
    assert produces.COMMANDS["onboarding-models"].startswith("none:")


def test_show_in_finder_with_no_model_opens_the_models_folder(tmp_path, monkeypatch):
    # The offer's folder line: "Kept in ~/MicroscopeMind/catalog/models · Show in Finder", before any model is there.
    import subprocess
    import sys
    from gpu_worker import manifests

    opened = []
    monkeypatch.setattr(manifests, "catalog_root", lambda *a, **k: tmp_path / "catalog")
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: opened.append(args))
    monkeypatch.setattr(sys, "platform", "darwin")
    reply = asyncio.run(commands.COMMANDS["model-reveal"](SimpleNamespace(), None, {"model": ""}))
    assert reply == {"ok": True, "path": str(tmp_path / "catalog" / "models")}
    assert opened == [["open", str(tmp_path / "catalog" / "models")]]
    with pytest.raises(ToolFailed):
        asyncio.run(commands.COMMANDS["model-reveal"](SimpleNamespace(), None, {"model": "no-such-model"}))
