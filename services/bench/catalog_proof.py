"""The decisive proof of the catalog (make catalog-proof [ONLY=edgetam,trackastra]; phase 3's E1, the catalog audit's G12):
every model the reviewed catalog runs on this Mac is **passed**, **failed** or **missing**, and the command is red unless
every one passed.

- **Passed** means its operations really ran: the app's own check of every option and fixed job that uses it
  (orchestrator/model_checks.py, through the runner and the GPU workers, on the assets store's leaf frame), in a process
  of its own that reaches nothing beyond this Mac (MICROMIND_REFUSE_NETWORK: the GPU workers and micro-sam's own Python
  too), with a catalog holding nothing but the models' published revisions and the old caches hidden. A model that
  passes ran from its pinned files alone.
- **Missing** says what to do: download it, install its code. A model made here (fine-tuned weights) with no version yet
  is said apart and fails nothing.
- **Failed** gives the check's own words.

Every check is kept where the live app reads them (paths.checks, `by` "make catalog-proof"), so the Capabilities page and
the agent say the same. It starts its own GPU workers, so it waits for the app unless BESIDE_LIVE=1 (bench/live.py).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SERVICES = Path(__file__).resolve().parent.parent


def required() -> dict:
    """Every reviewed model run on this Mac (or its CPU), by id."""
    from gpu_worker import manifests as catalog

    return {i: m for i, m in catalog.manifests().items() if m.runs != "cloud" and m.kind != "data"}


def models_of(entry) -> list[str]:
    """The catalog models a check entry exercises, in order: the one whose files it needs, and every model its probe
    names ("edgetam>sam3.1-local" runs both)."""
    from gpu_worker import manifests as catalog

    out = [entry.weights] if entry.weights else []
    for step in entry.probe[1].split(">"):
        m = catalog.get(step.split(":")[-1])
        if m is not None and m.id not in out:
            out.append(m.id)
    return out


def missing(m) -> str:
    """Why `m` can't be proven here ("" when it can), with what to do."""
    from gpu_worker import model_files

    from orchestrator import finetuned
    from orchestrator.operations import INSTALLED

    for need in m.needs_to_run:
        kind, _, what = need.partition(":")
        if kind == "weights" and finetuned.current(what) is None:
            return "made here: no version yet (make finetune-cells)"
        if kind == "install" and what in INSTALLED and (why := INSTALLED[what]()):
            return why
    if model_files.files(m) and model_files.location(m)[0] is None:
        return f"not downloaded (make catalog-download ID={model_files.owner(m).id})"
    if m.env:  # a Python of its own, made by the app (envs.py)
        from orchestrator import envs

        if envs.python(m) is None:
            return f"not installed here (make catalog-install ID={m.id})"
    for need in m.needs_to_run:  # data it reads (an atlas), downloaded as a model's files are
        kind, _, what = need.partition(":")
        if kind == "data":
            from gpu_worker import manifests as catalog

            d = catalog.get(what)
            if d is None or model_files.location(d)[0] is None:
                return f"its data {what} is not downloaded (make catalog-download ID={what})"
    return ""


def place(m, catalog_dir: Path) -> None:
    """The model's pinned revision in the proof's catalog: the published one cloned (APFS clones), else adopted from an
    old cache at the pinned revision, each file checked (downloads.adopt)."""
    from gpu_worker import model_files

    from orchestrator import downloads

    owner = model_files.owner(m)
    env = {**os.environ, "MICROMIND_CATALOG": str(catalog_dir)}
    if not model_files.files(owner) or model_files.published(owner, env) is not None:
        return
    real = model_files.published(owner)
    if real is not None:
        dest = model_files.revision_dir(owner, env)
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["cp", "-cR", str(real), str(dest)], check=True)
    else:
        downloads.adopt(owner, env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", default=os.environ.get("ONLY", ""), help="comma-separated model ids (default: all)")
    parser.add_argument("--beside-live", action="store_true", default=bool(os.environ.get("BESIDE_LIVE")))
    args = parser.parse_args()

    from bench.capability_check import refusal
    from orchestrator import finetuned, model_checks, paths

    why = refusal(args.beside_live)
    if why:
        print(why.replace("capability check", "catalog proof"), file=sys.stderr)
        return 3
    wanted = {i: m for i, m in required().items() if not args.only or i in args.only.split(",")}
    entries: dict[str, list] = {i: [] for i in wanted}
    chained: dict[str, list] = {}
    for e in model_checks.chosen("local"):
        for key in models_of(e):
            if key in entries:
                entries[key].append(e)
                chained[e.key] = models_of(e)

    from bench.own_proof import has_own_proof

    verdicts: dict[str, tuple[str, str]] = {}
    runnable, own = [], []
    for i, m in wanted.items():
        gap = missing(m)
        if gap.startswith("made here"):
            verdicts[i] = ("not made", gap)
        elif gap:
            verdicts[i] = ("missing", gap)
        elif not entries[i] and has_own_proof(m):
            own.append(i)
        elif not entries[i]:
            verdicts[i] = ("failed", "no check runs it (orchestrator/model_checks.py)")
        else:
            runnable.append(i)
    # A check that runs two models counts only where both can be proven here: a missing one is its own line, never a
    # failure of the other (EdgeTAM then SAM 3.1 with SAM 3.1 not downloaded proves nothing about EdgeTAM).
    proven = []
    for i in runnable:
        entries[i] = [e for e in entries[i] if all(k in runnable for k in chained[e.key] if k in wanted)]
        if entries[i]:
            proven.append(i)
        else:
            verdicts[i] = ("missing", "each check of it also needs a model that can't run here (above)")
    runnable = proven

    started = time.perf_counter()
    if runnable or own:
        with tempfile.TemporaryDirectory(prefix="catalog-proof-") as tmp:
            tmp = Path(tmp)
            for i in runnable + own:
                place(wanted[i], tmp / "catalog")
                for need in wanted[i].needs_to_run:  # the data it reads, in the proof's catalog too
                    if need.startswith("data:"):
                        from gpu_worker import manifests as catalog

                        place(catalog.get(need.split(":", 1)[1]), tmp / "catalog")
            hidden = tmp / "hidden"
            env = {**os.environ, "MICROMIND_CATALOG": str(tmp / "catalog"), "MICROMIND_MODEL_FILES": str(hidden),
                   "HF_HUB_CACHE": str(hidden / "hf"), "HF_HOME": str(hidden / "hf-home"),
                   "CELLPOSE_LOCAL_MODELS_PATH": str(hidden / "cp"), "MICROSAM_CACHEDIR": str(hidden / "ms"),
                   "HF_HUB_OFFLINE": "1", "MICROMIND_REFUSE_NETWORK": "1", "BESIDE_LIVE": "1",
                   "MICROMIND_MODELS_DIR": str(finetuned.root()), "PYTHONPATH": str(SERVICES),
                   # The environments the app made, where they were built (they can't be copied), and none made by
                   # hand: a model that passes ran in the catalog's own environment.
                   "MICROMIND_ENVS": str(paths.envs()), "MICROMIND_MICROSAM_PYTHON": str(hidden / "no-python"),
                   "MICROMIND_TOOLS": str(paths.tools())}  # Fiji's generations too, where they were made
            if own:  # entries that bring their own proof (a Fiji binding, an adapter, a job), offline the same way
                from orchestrator import jobs, owned_process

                limit = jobs.PROOF_LIMIT_S * len(own) + 600
                process = subprocess.Popen([sys.executable, "-m", "bench.own_proof", *own], env=env, cwd=SERVICES,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                           start_new_session=True)
                try:
                    with owned_process.watching(process, limit, "Catalog proofs"):
                        stdout, stderr = process.communicate()
                    done = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
                except owned_process.Stopped as exc:
                    done = subprocess.CompletedProcess(process.args, 1, "", str(exc))
                finally:
                    # Each nested job also owns a supervisor pipe; ending this child closes it and ends that job's
                    # group. Never scan all processes for a generic proof-folder substring.
                    owned_process.terminate(process)
                try:
                    found = json.loads((done.stdout.strip().splitlines() or ["{}"])[-1])
                except ValueError:
                    found = {}
                for i in own:
                    ok, said = found.get(i, (False, "its proof did not finish: " + (done.stderr.strip().splitlines()
                                                                                    or ["no words"])[-1][:300]))
                    verdicts[i] = ("passed" if ok else "failed", said)
            keys = sorted({e.key for i in runnable for e in entries[i]})
            out = tmp / "lines.json"
            child = subprocess.run([sys.executable, "-m", "bench.capability_check", "--only", ",".join(keys),
                                    "--json", str(out), "--by", "make catalog-proof"], env=env, cwd=SERVICES,
                                   capture_output=True, text=True) if keys else None
            lines = {line["role"] + ":" + line["option"]: line for line in json.loads(out.read_text())} \
                if out.exists() else {}
            for i in runnable if keys else ():
                got = [lines.get(e.key) for e in entries[i]]
                if any(g is None for g in got):
                    last = (child.stderr.strip().splitlines() or ["it stopped before saying"])[-1]
                    verdicts[i] = ("failed", f"the check did not finish: {last[:300]}")
                elif all(g["state"] == "works" for g in got):
                    verdicts[i] = ("passed", "; ".join(f"{g['role']}:{g['option']} works"
                                                       + (f" in {g['ms'] / 1000:.1f} s" if g.get("ms") else "")
                                                       for g in got))
                else:
                    bad = next(g for g in got if g["state"] != "works")
                    verdicts[i] = ("failed", f"{bad['role']}:{bad['option']} {model_checks.STATE_WORDS[bad['state']]}: "
                                             f"{bad.get('why') or bad.get('detail') or ''}")

    order = {"failed": 0, "missing": 1, "not made": 2, "passed": 3}
    for i, (state, said) in sorted(verdicts.items(), key=lambda kv: (order[kv[1][0]], kv[0])):
        print(f"{state:9} {i:16} {said}")
    counts = {s: sum(1 for v in verdicts.values() if v[0] == s) for s in order}
    print(f"\n{len(verdicts)} models in {time.perf_counter() - started:.0f} s: "
          + ", ".join(f"{n} {s}" for s, n in counts.items() if n) + " (offline, from their pinned files alone)")
    return 1 if counts["failed"] or counts["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
