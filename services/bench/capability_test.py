"""make capability-test ID=<id>: one capability, tested the way the app will use it, in three layers never mixed
(the skill `add-a-capability`; ADR 0024):

1. **Its contract.** The preflight (orchestrator/contribute.py), then installed and proven as the app proves it: a
   python-env entry's environment made and its adapter run on a real picture (envs.py, env_runner.prove); a Fiji
   function proven against Fiji's own macro. Contributions use isolated JVMs and separate receipts.
2. **Its real code on its examples.** Each of its `tests` (a pinned input, its parameters, the outcome it gives), and
   its defaults on a generated picture, through its real adapter or the real Fiji: the outcome must be the one
   declared (a result, empty, or a refusal).
3. **Every actor.** Through the real app (App.build over stand-in GPU workers and scripted models,
   tests/capability_rig.py), its own code real: the agent's run_capability, its Actions row (its form's defaults), Try
   on its card, and a researcher's run. The agent here is scripted to call it; whether a live model chooses it is the
   live test's, said, never claimed.

Everything is installed in a catalog of its own (`--catalog`, default a fresh run under ~/MicroscopeMind/bench/capability-test;
`--workspace` keeps it under that workspace's checks folder). The live app's catalog is never written.
A Fiji function's generation is a clone of this Mac's generation in use (APFS,
instant). Nothing is downloaded but what its lockfile pins (wheels by hash).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


def say(layer: str, ok: bool | None, words: str) -> dict:
    mark = {True: "pass", False: "FAIL", None: "skip"}[ok]
    print(f"[{layer}] {mark:4}  {words}", flush=True)
    return {"layer": layer, "ok": ok, "says": words}


def picture():
    import cv2
    import numpy as np

    pixels = np.full((480, 640, 3), 225, np.uint8)  # the size of a small camera frame
    for (x, y), r in (((160, 120), 9), ((420, 200), 6), ((300, 360), 12)):
        cv2.circle(pixels, (x, y), r, (40, 50, 70), -1)
    return pixels


def setup(catalog_dir: Path, source_key: str, *, workspace: str | Path | None = None) -> str:
    """Its own catalog: the folder (when it is the user's own) copied in, and for Fiji a clone of the generation in use."""
    from orchestrator import capability_workspace as workspaces, contribute
    from gpu_worker import manifests as catalog

    real = catalog.catalog_root().absolute()
    item = workspaces.from_path(workspace) if workspace is not None else None
    if item is not None:
        workspaces.require_candidate(item, source_key)
        source = item.candidate
        runtime_catalog = item.folder.parent.parent
        if not catalog_dir.absolute().is_relative_to(item.folder / "checks"):
            raise ValueError("workspace tests must use a catalog inside this workspace's checks folder")
    else:
        source = contribute._folder_of(source_key)
        runtime_catalog = real
    target = catalog_dir.absolute()
    if (target == real or real.is_relative_to(target) or (item is None and target.is_relative_to(real))
            or target == catalog.catalog_root({}).absolute()
            or (source is not None and (target.is_relative_to(source.absolute()) or source.absolute().is_relative_to(target)))):
        raise ValueError("the test catalog must be separate from the live catalog and capability source")
    with workspaces._directory(target, create=True):
        pass
    key = source_key
    if item is not None or source_key.startswith("mine/") or Path(source_key).exists():
        if source is None:
            raise ValueError("the source capability could not be found")
        with workspaces.snapshot(source) as copied:
            manifest, problems = catalog.read(copied / catalog.MANIFEST)
            if manifest is None:
                raise ValueError("the candidate manifest cannot be read: " + "; ".join(problems))
            destination = target / "mine" / "models" / manifest.id
            with workspaces._directory(destination.parent, create=True):
                pass
            shutil.copytree(copied, destination)
            key = f"mine/{manifest.id}"
    fiji_real, fiji_here = runtime_catalog / "tools" / "fiji", target / "tools" / "fiji"
    if (fiji_real / "current").is_file() and not (fiji_here / "current").is_file():
        n = (fiji_real / "current").read_text().strip()
        if not n.isdecimal():
            raise ValueError("the installed Fiji generation reference is invalid")
        fiji_here.mkdir(parents=True, exist_ok=True)
        subprocess.run(["cp", "-cR", str(fiji_real / n), str(fiji_here / n)], check=True)
        (fiji_here / "current").write_text(n + "\n")
    os.environ["MICROMIND_CATALOG"] = str(target)
    return key


def install(m, report: list) -> bool:
    from orchestrator import env_runner, envs, fiji_generations, fiji_runner

    if m.runner == "python-env":
        if m.files:  # its pinned files first, each checked by its content (Turn on's Download)
            from orchestrator import downloads

            try:
                downloads.download(m)
            except downloads.Failed as exc:
                report.append(say("1 contract", False, f"its files didn't download: {exc}"))
                return False
        queue = envs.Installs()
        queue.start(m.key)
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline and queue.wire()[0]["state"] not in ("done", "failed"):
            time.sleep(0.5)
        row = queue.wire()[0]
        proof = env_runner.proof(m) or {}
        ok = row["state"] == "done" and bool(proof.get("ok"))
        report.append(say("1 contract", ok, f"installed and proven: {proof.get('says') or proof.get('why') or row.get('says')}"))
        return ok
    if m.runner == "fiji":
        if fiji_generations.current() is None:
            report.append(say("1 contract", False, "Fiji isn't set up on this Mac: set it up in the Toolbox first"))
            return False
        try:
            if m.namespace:
                envs.install(m)
                installed = fiji_runner.in_use(m)
                if installed is None:
                    raise envs.Failed("the contribution has no current confined proof")
                n, proof = installed.n, installed.proof
            else:
                n = fiji_generations.renew(say=lambda s: None)
                proof = ((fiji_generations.receipt(n) or {}).get("proofs") or {}).get(m.key) or {}
        except (envs.Failed, fiji_generations.Failed) as exc:
            report.append(say("1 contract", False, f"its Fiji generation was not made current: {exc}"))
            return False
        why = fiji_generations.proven(m.key)
        report.append(say("1 contract", not why, f"proven on Fiji generation {n}: {proof.get('summary') or why}"))
        compared = list(dict.fromkeys(c.get("options", "") for c in proof.get("cases") or []))
        if compared:  # the exact options both sides were given (the guide proof asked to see them)
            report.append(say("1 contract", None, f"{m.fiji['command']} compared with options: " + " | ".join(
                o or "(none)" for o in compared)))
        return not why and fiji_runner.in_use(m) is not None
    report.append(say("1 contract", False, f"runner {m.runner!r} has no test here (python-env or fiji)"))
    return False


def examples(m, report: list, installed=None) -> None:
    import cv2
    import numpy as np

    from gpu_worker import manifests as catalog
    from orchestrator import env_runner, fiji_runner

    def run(pixels, values):
        if m.runner == "fiji":
            fiji_installed = fiji_runner.in_use(m)
            return fiji_runner.run_function(fiji_installed.m, pixels, values, n=fiji_installed.n)
        outputs = tuple({"id": o.id, "type": o.type} for o in m.outputs)
        return env_runner.client(installed or env_runner.in_use(m)).run(pixels, values, output=m.outputs[0].type, outputs=outputs)

    def outcome(pixels, values) -> tuple[str, str]:
        from orchestrator import kinds

        try:
            got = run(pixels, values)
        except env_runner.Unsupported as exc:
            return "unsupported", str(exc)
        except Exception as exc:  # noqa: BLE001 - its failure, said as the outcome
            return "failed", f"{type(exc).__name__}: {exc}"
        def has(v) -> bool:  # an array is something when it holds anything (a picture always does)
            return v is not None and (v.size > 0 if hasattr(v, "size") else bool(v))

        found = any(has(got.get(k)) for k in kinds.KIND_KEYS) or "data" in got or has(got.get("count"))
        return ("result" if found else "empty"), kinds.made_words(m.outputs[0].type, got)

    defaults, _ = catalog.params_given(m, {})
    got, words = outcome(picture(), defaults)
    refused = got == "unsupported" and bool(m.outcomes.get("unsupported"))  # a refusal it declares is its own outcome
    report.append(say("2 examples", True if got in ("result", "empty") else None if refused else False,
                      f"its defaults on a generated 640x480 picture: {got} ({words})"))
    for n, t in enumerate(m.tests, 1):
        pixels = cv2.cvtColor(cv2.imread(str((installed.entry if installed is not None else Path(m.folder)) / t["input"]), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        values, problems = catalog.params_given(m, t.get("params") or {})
        if problems:
            report.append(say("2 examples", False, f"test {n}: " + "; ".join(problems)))
            continue
        got, words = outcome(np.ascontiguousarray(pixels), values)
        want = t.get("outcome", "result")
        report.append(say("2 examples", got == want, f"test {n} ({t['input']}, {values}): {got}, declared {want} ({words})"))
    if not m.tests:
        report.append(say("2 examples", None, "it declares no tests: add one (a pinned input and the outcome it gives)"))


def actors(m, report: list, root: Path) -> None:
    from orchestrator import kinds
    from orchestrator.capabilities import contributed
    from orchestrator.commands import handle
    from orchestrator.research.hands import Hands
    from tests.capability_rig import Rig
    from tests.test_research_hands import session
    from tests.test_sources import Client

    rig = Rig(root)
    rig.app.catalog_changed()
    kind = next((k for k in kinds.available() if k["key"] == m.key), None)
    if kind is None:
        report.append(say("3 actors", False, "it is not in the one registry (kinds.available) after it was proven"))
        return
    rows = {r["id"]: r for r in contributed(set(rig.brain.registry.tools))}
    row = rows.get(f"catalog:{m.key}")
    report.append(say("3 actors", bool(row and row["available"]),
                      f"listed in Actions under {kind['family']} with a form of {len(row['fields']) if row else 0} field(s)"))
    runs = asyncio.run(rig.turn([("run_capability", {"capability": m.key, "by": "user"})], f"run {m.name}"))
    run = next((r for r in runs if r.tool == "run_capability"), None)
    report.append(say("3 actors", bool(run and run.ok), f"the agent (scripted call): {run.summary[:200] if run else 'not run'}"))
    client = Client()

    async def press(msg):
        await handle(rig.app, client, msg)
        return client.replies[-1]

    reply = asyncio.run(press({"type": "action", "requestId": "a", "capability": f"catalog:{m.key}", "args": {}}))
    report.append(say("3 actors", reply.get("ok") is not False, f"its Actions row: {str(reply.get('said') or reply.get('summary') or reply.get('error'))[:200]}"))
    reply = asyncio.run(press({"type": "capability-run", "requestId": "t", "model": m.key, "session": rig.app.day.sid,
                               "input": kind["input"]}))
    report.append(say("3 actors", reply.get("ok") is not False, f"Try on its card: {str(reply.get('summary') or reply.get('error'))[:200]}"))
    reply = asyncio.run(Hands(rig.hub).run(session(sitting=rig.app.day.sid), "capability", "capability-test",
                                           capability=m.key))
    report.append(say("3 actors", bool(reply.get("ok")), f"a researcher: {str(reply.get('words') or reply.get('error'))[:200]}"))


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.capability_test")
    parser.add_argument("id")
    parser.add_argument("--catalog")
    parser.add_argument("--workspace")
    parser.add_argument("--layers", default="1,2,3")
    args = parser.parse_args(argv)
    os.environ.setdefault("MICROMIND_CATALOG_AUTO", "0")
    from orchestrator.contribute import preflight

    report: list[dict] = []
    from orchestrator import capability_workspace as workspaces

    item = workspaces.from_path(args.workspace) if args.workspace else None
    destination = Path(args.catalog).expanduser() if args.catalog else (
        item.folder / "checks" / uuid.uuid4().hex / "catalog" if item else
        Path.home() / "MicroscopeMind" / "bench" / "capability-test" / uuid.uuid4().hex / "catalog")
    found = preflight(args.id, workspace=args.workspace)
    for f in found:
        report.append(say("1 contract", False if f.blocks else None, ("blocks: " if f.blocks else "note: ") + f.says))
    if any(f.blocks for f in found):
        return 1
    key = setup(destination, args.id, workspace=args.workspace)
    from gpu_worker import manifests as catalog

    catalog.manifests.cache_clear()
    m = catalog.get(key) or catalog.mine().get(key) or catalog.mine().get(f"mine/{key}")
    if m is None:
        report.append(say("1 contract", False, f"{key} isn't in the catalog: {catalog.problems().get(key)}"))
        return 1
    layers = set(args.layers.split(","))
    ok = install(m, report)
    if ok:
        from orchestrator import bundle_runtime, env_runner
        members = bundle_runtime.expand(env_runner.in_use(m)) if m.members else ()
        if "2" in layers:
            if members:
                for member in members:
                    examples(member.m, report, member)
            else:
                examples(m, report)
        if "3" in layers:
            for declaration in [member.m for member in members] if members else [m]:
                with tempfile.TemporaryDirectory() as tmp:
                    actors(declaration, report, Path(tmp))
    failed = [r for r in report if r["ok"] is False]
    print(f"\n{m.name}: {sum(r['ok'] is True for r in report)} passed, {len(failed)} failed, "
          f"{sum(r['ok'] is None for r in report)} said. A live model choosing it, and a person's run in the Studio, are "
          "the live test's, never claimed here.")
    out = destination.parent / f"{m.id}.json"
    out.write_text(json.dumps({"id": m.key, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "report": report}, indent=1))
    return 1 if failed or not ok else 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Can't test this capability: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
