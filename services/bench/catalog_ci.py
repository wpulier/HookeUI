"""The repository's check of catalog entries (make catalog-ci [IDS=a,b] [BASE=origin/main]; .github/workflows/catalog.yml;
phase 3's E4): what a person reviewing a proposed entry is told, red unless every entry passed.

For each entry changed since BASE (or each named):
1. **It reads:** its manifest is one this app reads, or it is said as made for another version (manifests.incompatible).
2. **It is publishable:** a runner, every file by URL and hash, its licence known, and for runner python-env a hashed
   lockfile and nothing in its code that installs or fetches at run time (orchestrator/publish.py, all but the proof
   on the contributor's Mac).
3. **Its files are what it pins:** downloaded into a scratch catalog and each checked by its bytes and hash
   (orchestrator/downloads.py, exactly as an app's Download does).
4. **It runs:** runner python-env: its environment made from its lockfile (envs.build), its adapter run on a retained,
   identified input with Python socket/DNS refusal (env_runner.prove); any other runner: bench/catalog_proof.py (a Mac's GPU
   workers, so only where they run).

A built-in model (runner gpu-worker) that pins code of its own (a Hugging Face model's Python, run with
`trust_remote_code`) must import only what services/uv.lock provides (shared_python.unlocked; live 2026-10-19, Molmo 2's
einops, which only another model's setup had installed). That is checked at step 2 from its code files alone, fetched at
the pinned revision (kilobytes), and again after step 3 from the downloaded files, where each import must also be
installed in the Python the proof runs in (shared_python.missing: the workflow syncs every group).

A change to services/uv.lock or pyproject.toml changes the Python every built-in model runs in. It checks the imports of
each one that pins code (step 2's check only: no weights are downloaded, so it takes seconds; proving every built-in model
would download about 25 GB, Molmo 2 alone 19.4 GB and SAM 3.1's gated 3.5 GB, past the Mac job's 60 minutes and a hosted
runner's disk). Proving them run after a lock change is `make capability-check`, which a model-related PR needs.

`--static` stops after step 2 (any machine, seconds); the rest needs the Mac the entry runs on.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SERVICES = Path(__file__).resolve().parent.parent
REPO = SERVICES.parent


SHARED_PYTHON = ("services/uv.lock", "services/pyproject.toml")  # what the app's own Python is made from


def _diff(base: str, *paths: str) -> list[str]:
    return subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD", "--", *paths], cwd=REPO,
                          capture_output=True, text=True, check=True).stdout.split()


def changed(base: str) -> list[str]:
    return sorted({p.split("/")[2] for p in _diff(base, "catalog/models") if p.count("/") >= 3})


def lock_changed(base: str) -> bool:
    return bool(_diff(base, *SHARED_PYTHON))


def with_code() -> list[str]:
    """The built-in models that pin code of their own: what a change to the app's own Python is checked against."""
    from gpu_worker import manifests as catalog

    return sorted(m.id for m in catalog.local().values() if any(f["path"].endswith(".py") for f in m.files))


def code_problems(m) -> list[str]:
    """A built-in model's pinned code, fetched at its revision (its .py files only), against services/uv.lock."""
    from bench.catalog_pin import code_imports
    from orchestrator import shared_python

    if m.runner != "gpu-worker" or not m.source.get("hf") or not any(f["path"].endswith(".py") for f in m.files):
        return []
    try:
        needed = code_imports(m.source["hf"], m.source["revision"], list(m.files))
    except Exception as exc:  # the network or the host: said, never passed
        return [f"its pinned code couldn't be read for its imports: {exc}"]
    return [f"its pinned code imports {module}: {why}" for module, why in shared_python.unlocked(needed).items()]


def check(entry: str, static: bool, environ: dict) -> list[str]:
    """What is wrong with one entry (none: it passed)."""
    from gpu_worker import manifests as catalog
    from orchestrator import downloads, env_runner, envs, publish, shared_python

    folder = catalog.ROOT / entry
    if not (folder / catalog.MANIFEST).is_file():
        return []  # removed by the change: nothing to prove
    m, problems = catalog.read(folder / catalog.MANIFEST)
    if m is None:
        return [str(p) for p in problems]
    why = publish.problems(m, environ, proven=False) or code_problems(m)
    if why or static:
        return why
    try:
        if m.files:
            print(f"{entry}: downloading its pinned files and checking each")
            downloads.download(m, environ=environ)
        for need in m.needs_to_run:  # the data it reads (an atlas), its files checked the same way
            if need.startswith("data:"):
                print(f"{entry}: downloading its data {need[5:]} and checking each file")
                downloads.download(catalog.get(need[5:]), environ=environ)
        if m.runner == "python-env":
            print(f"{entry}: making its environment from its lockfile")
            envs.build(m, environ, say=lambda s: print(f"{entry}: {s}"))
            p = env_runner.prove(m, environ)
            if not p.get("ok"):
                return [f"its adapter failed its proof: {p.get('why')}"]
            if p.get("says"):  # a job's proof (jobs.prove: brainreg's has no `found`) or a new kind's says what it made
                print(f"{entry}: its proof: {p['says']}; not scientific qualification")
            else:
                print(f"{entry}: adapter contract smoke, found {p['found']} on {env_runner.proof_input_says(p)} "
                      f"({p['picture']}) in {p['seconds']} s; not scientific qualification")
            return []
    except (downloads.Failed, envs.Failed) as exc:
        return [str(getattr(exc, "says", "") or exc)]
    if m.runner == "gpu-worker":  # its pinned code, now downloaded: from the lock, and installed where the proof runs
        needed = shared_python.pinned_code_imports(m, environ) or frozenset()
        why = [f"its pinned code imports {module}: {said}" for module, said in shared_python.unlocked(needed).items()]
        why += [f"its pinned code imports {module}: services/uv.lock holds it, but this Python doesn't have it installed "
                "(sync it with every group: `uv sync --all-groups`)" for module in shared_python.missing(needed)
                if module not in shared_python.unlocked(needed)]
        if why:
            return why
    done = subprocess.run([sys.executable, "-m", "bench.catalog_proof", "--only", entry], cwd=SERVICES, env=environ)
    return [] if done.returncode == 0 else ["make catalog-proof failed for it (its own words above)"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check catalog entries as the repository does before a merge.")
    parser.add_argument("--ids", default="", help="entries to check, comma-separated (default: those changed since --base)")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--static", action="store_true", help="only what needs no download and no run")
    args = parser.parse_args(argv)
    named = [i for i in args.ids.split(",") if i]
    ids = named or changed(args.base)
    imports_only = [] if named or not lock_changed(args.base) else [i for i in with_code() if i not in ids]
    if not ids and not imports_only:
        print("No catalog entry changed.")
        return 0
    scratch = tempfile.mkdtemp(prefix="catalog-ci-")
    environ = {**os.environ, "MICROMIND_CATALOG": os.environ.get("MICROMIND_CATALOG") or scratch}
    os.environ.update(environ)
    failed = 0
    for entry in ids:
        why = check(entry, args.static, environ)
        print(f"{entry}: {'passed' if not why else 'failed'}" + "".join(f"\n  - {w}" for w in why))
        failed += bool(why)
    if imports_only:
        from gpu_worker import manifests as catalog

        print("services/uv.lock or pyproject.toml changed: the imports of each built-in model's pinned code, from the lock "
              "(proving them run is make capability-check)")
    for entry in imports_only:
        why = code_problems(catalog.get(entry))
        print(f"{entry} (its code's imports): {'passed' if not why else 'failed'}" + "".join(f"\n  - {w}" for w in why))
        failed += bool(why)
    print(f"{len(ids) + len(imports_only) - failed} of {len(ids) + len(imports_only)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
