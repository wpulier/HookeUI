"""The real models' check (the user's Q20 of 2026-10-11, Slice 8): every model the capabilities list names runs once on
a real frame (the assets store's leaf frame) through the app's own runner and GPU workers, and its state is kept where
the live app reads it, so the Capabilities page and the agent say what works and what is broken.

    make capability-check                 # every model on this Mac (exit 1 when one is broken)
    make capability-check DOWNLOAD=1      # ...letting a model download weights it does not have yet
    make capability-check-cloud           # every cloud model (needs keys; costs about $0.10-0.20, said at the end)
    make capability-check-cloud VIDEO=1   # ...and the video model (about $0.40 more)
    ONLY=outline:micro-sam-lm,cells:micro-sam-lm   # just these entries (role:option)

Live first (bench/live.py): the local check starts its own GPU workers, so it refuses while the app runs unless told it
may run beside it (BESIDE_LIVE=1, `--beside-live`), which its lines then say; the cloud check starts none. Inside the running app the same check is the
Capabilities page's "Check now" (on this Mac only). Every state goes to paths.checks of the live app's home (the paths
CLI's rule: the launch settings' home when nothing else says), one line a check, the latest per model counting; the app
the bench builds (its day, logs and loads) is a scratch one, so nothing of the user's day is touched.

The local check must pass before any model-related PR merges (the plan's Verification, item 3); the cloud check runs
when a PR touches a cloud model's path. A change to services/uv.lock or pyproject.toml is model-related: every built-in
model runs in the Python made from them. The local check ends by saying which Python it ran in and how that differs from
the lock a fresh setup and the Mac app use (shared_python.drift): live 2026-10-19, a check run where another model's setup
had added einops said Molmo worked while the shipped app's could not import it.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import os
import sys
import tempfile
import time
from pathlib import Path


def refusal(beside_live: bool) -> str:
    """Why the check waits for the app ("" when it may run)."""
    from bench.live import running

    if beside_live:
        return ""
    why = running()
    if not why:
        return ""
    return (f"the capability check waits for the app: {why}. It starts its own GPU workers and loads every model on this "
            "Mac, which would slow the live view. Stop the app (make stop), use Check now on the Capabilities page "
            "(models on this Mac), or run it anyway with BESIDE_LIVE=1; its timings then mean little.")


def table(lines: list[dict]) -> str:
    from orchestrator.model_checks import STATE_WORDS

    rows = []
    for l in lines:
        timing = f"{l['ms'] / 1000:.1f} s" if l.get("ms") is not None else ""
        if l.get("loadMs") is not None:
            timing += f" (load {l['loadMs'] / 1000:.1f} s)"
        said = l.get("why") or l.get("detail") or ""
        rows.append(f"{STATE_WORDS[l['state']]:<20} {l['role'] + ':' + l['option']:<36} {timing:<20} {l['name']}: {said}")
    return "\n".join(rows)


async def check(args) -> int:
    from orchestrator import capabilities, model_checks
    from orchestrator.app import App
    from orchestrator.keys import load_keys
    from orchestrator.paths import Paths, cli_environ, finetuned

    live_home = Paths.from_env(cli_environ())  # where the live app reads its checks (the paths CLI's rule)
    load_keys(live_home.keys_file)  # from the real keys file, before the scratch home (values never printed)
    scratch = Path(tempfile.mkdtemp(prefix="micromind-capability-check-"))
    os.environ["MICROMIND_HOME"] = str(scratch)
    # Fine-tuned weights made here are the live app's (finetuned.py), unless the caller (make catalog-proof) set its own.
    os.environ.setdefault("MICROMIND_MODELS_DIR", str(finetuned(cli_environ())))
    store = model_checks.Checks(live_home.checks, version=capabilities.build())
    # App.build reloads credentials. Preserve the explicitly selected read-only
    # source while every check write remains in the isolated scratch profile.
    app = App.build("http://127.0.0.1:1", replace(Paths.under(scratch), keys_file=live_home.keys_file), use_gpu=True)
    scope = "cloud" if args.cloud else "local"
    chosen = model_checks.chosen(scope)
    if args.only:
        wanted = {w.strip() for w in args.only.split(",") if w.strip()}
        chosen = [e for e in chosen if e.key in wanted]
        unknown = wanted - {e.key for e in chosen}
        if unknown:
            print(f"no such {scope} entry: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
    by = args.by or (("make capability-check-cloud" if args.cloud else "make capability-check")
                     + (" beside the live app" if args.beside_live else ""))
    started = time.perf_counter()

    def progress(state: dict) -> None:
        if state.get("now"):
            print(f"  checking {state['now']} ({state['done'] + 1} of {state['of']})", flush=True)

    checker = model_checks.Checker(app.hub, app.agent, store, by=by, jpeg=model_checks.frame(), download=args.download,
                                   video=args.video, log=app.agent.log, progress=progress)
    from orchestrator.voice.route import voice_prompt

    checker.voice_prompt = voice_prompt(app.brain)  # what the studio's voice is told (route.py)
    try:
        lines = await checker.run(chosen)
    finally:
        for client in (app.hub.gpu, app.hub.gpu_heavy, app.hub.gpu_follow, app.hub.gpu_molmo):
            if client is not None and client.ready:
                client.stop()
    print(table(lines))
    if args.json:
        import json

        Path(args.json).write_text(json.dumps(lines, default=str))
    found = model_checks.summary(lines)
    counts = ", ".join(f"{n} {model_checks.STATE_WORDS[s]}" for s, n in found["counts"].items())
    print(f"\n{len(lines)} models in {time.perf_counter() - started:.0f} s: {counts}"
          + (f"; estimated cost ${checker.cost:.3f}" if args.cloud else "") + f"\nkept in {live_home.checks}")
    if args.beside_live:
        print("run beside the live app: its timings mean little")
    if not args.cloud:  # what it ran in, beside what a fresh setup and the Mac app run in (shared_python.py)
        from orchestrator import shared_python

        print(f"ran in {shared_python.says(shared_python.drift())}")
    return 1 if found["broken"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run every model the capabilities list names once, and keep whether it worked.")
    parser.add_argument("--cloud", action="store_true", help="the cloud models (needs keys) instead of those on this Mac")
    parser.add_argument("--video", action="store_true", help="with --cloud: the video model too (about $0.40)")
    parser.add_argument("--download", action="store_true", help="let a model download weights it does not have yet")
    parser.add_argument("--beside-live", action="store_true", default=bool(os.environ.get("BESIDE_LIVE")),
                        help="run while the app runs (BESIDE_LIVE=1)")
    parser.add_argument("--only", default=os.environ.get("ONLY", ""), help="role:option entries, comma separated")
    parser.add_argument("--json", help=argparse.SUPPRESS)  # make catalog-proof: the lines, to read back
    parser.add_argument("--by", default="", help=argparse.SUPPRESS)  # who ran it, as the kept lines say
    args = parser.parse_args()
    from gpu_worker.offline import refuse

    refuse()  # make catalog-proof: nothing here reaches beyond this Mac (MICROMIND_REFUSE_NETWORK)
    # The cloud models start no worker here, except EdgeTAM then SAM 3.1 (cloud), whose EdgeTAM step needs the click
    # worker: only then does the cloud check wait for the app too.
    from orchestrator import model_checks

    starts_worker = any(e.probe[0] == "fal" and ">" in e.probe[1] for e in model_checks.chosen("cloud")
                        if not args.only or e.key in args.only.split(","))
    why = refusal(args.beside_live or (args.cloud and not starts_worker))
    if why:
        print(why, file=sys.stderr)
        return 3
    return asyncio.run(check(args))


if __name__ == "__main__":
    raise SystemExit(main())
