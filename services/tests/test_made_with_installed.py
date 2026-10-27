"""Real Runner/env-worker over a deterministic local adapter and synthetic retained prerequisites.

No installer, proof, model, download or external API runs. The receipt fixture is labelled synthetic;
the adapter really executes in the existing test Python, so this checks the production producer stamp.
"""
import asyncio
import json
import shlex
import shutil
import sys
from types import SimpleNamespace

import cv2

from gpu_worker import manifests as catalog
from orchestrator import env_runner, envs, made_with, operations
from orchestrator.capture import Capture
from tests.test_env_runner import home, contribute, _picture  # noqa: F401
from tests.test_operations import hub


def test_runner_credits_the_installed_generation_even_after_update_and_removal(home):
    installed = contribute(home, version="old installed version")
    folder = envs.folder(installed)
    kept = folder / "entry" / installed.id
    kept.mkdir(parents=True)
    envs.copy(installed, kept)
    (folder / "bin").mkdir()
    python = folder / "bin/python"
    python.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    python.chmod(0o755)
    (folder / "receipt.json").write_text(json.dumps({"at": "2000-01-01", "synthetic": True}))
    folder.with_name(folder.name + ".proof.json").write_text(json.dumps({"ok": True, "synthetic": True, "found": 3}))

    updated = contribute(home, version="new catalog version")
    manifest = catalog.entry_folder(updated) / "model.toml"
    manifest.write_text(manifest.read_text().replace('name = "Dark spots"', 'name = "Renamed method"'))
    catalog.manifests.cache_clear()
    env_runner.register()
    picture = cv2.imencode(".png", _picture())[1].tobytes()
    capture = Capture(7, picture, SimpleNamespace(id=7, width=96, height=96, epoch=0, dx=0., dy=0.))
    runner = operations.Runner(hub())
    result = asyncio.run(runner.run("cells", capture, operations.Prompt("field"), choice="dark-spots", origin="user-chosen"))
    assert len(result.output["polygons"]) == 3, "the deterministic adapter really ran"
    named = result.execution.producer["catalog"]
    assert named["name"] == "Dark spots" and named["version"] == "old installed version"
    assert named["generation"] == folder.name
    shutil.rmtree(catalog.entry_folder(updated))
    catalog.manifests.cache_clear()
    env_runner.stop()
    reopened = SimpleNamespace(made_by=result.made_by(), trial=None)
    assert made_with.of_objects([reopened]) == [named]
