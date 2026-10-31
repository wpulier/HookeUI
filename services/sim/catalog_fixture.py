"""An owned simulation catalog, with published model resources copied before timing."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from gpu_worker import manifests, model_files
from orchestrator import paths as storage


def prepare(inherited: dict[str, str], paths: storage.Paths) -> dict[str, str]:
    """Exclude personal entries/channels and keep mutable model checks run-local.

    Published model revisions are immutable inputs. APFS clones avoid duplicating
    their large weight files on macOS; other hosts use ordinary independent copies.
    Prepared environments/tools stay where installed (their paths are not relocatable),
    as in catalog-proof; ordinary interpreter/dependency caches may still be written
    there. This is saved-data isolation, not a runtime filesystem sandbox.
    No installation, download or provider call occurs here.
    Missing published weights remain missing; the ordinary pinned legacy-cache
    resolver is still available to the production loaders.
    """
    env = {**inherited, "MICROMIND_CATALOG": str(paths.home / "catalog")}
    paths.home.joinpath("catalog").mkdir(parents=True)
    seen = set()
    for entry in manifests.manifests().values():
        if not _bundled(entry):
            continue
        model = model_files.owner(entry)
        if not _bundled(model) or model.id in seen or not model.files:
            continue
        seen.add(model.id)
        source = model_files.published(model, inherited)
        destination = model_files.revision_dir(model, env)
        if source is not None:
            if source.is_symlink() or any(p.is_symlink() for p in source.rglob("*")):
                raise ValueError(f"Simulation model resource contains a symlink: {model.id}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if sys.platform == "darwin":
                subprocess.run(["cp", "-cR", str(source), str(destination)], check=True)
            else:
                shutil.copytree(source, destination)
        # These are cache/proof records for the exact stock weights, not user entries.
        # Future verification/probing writes to the run's copies, never the original.
        for record in (model_files.verification_path(model, inherited), model_files.folder(model, inherited) / ".device-check.json"):
            if record.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(record, destination.parent / record.name)
    return {"MICROMIND_CATALOG": env["MICROMIND_CATALOG"],
            "MICROMIND_ENVS": str(storage.envs(inherited)),
            "MICROMIND_TOOLS": str(storage.tools(inherited))}


def _bundled(model: manifests.Manifest) -> bool:
    return (not model.namespace and not model.channel
            and Path(model.folder) == manifests.ROOT / model.id)
