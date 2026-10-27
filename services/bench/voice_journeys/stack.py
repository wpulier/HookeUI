"""A second stack for the voice journeys bench: a bridge playing test footage and an orchestrator,
on their own ports with their own home, from any checkout (main for the baseline, a branch to
compare). It never touches the live app: no studio.sh (which stops every MicroscopeMind), no
shared home. It also writes the steps' recorded words and the player beside that checkout's
studio build (dist/voice-test/), which only a stack started here serves.

  cd services && .venv/bin/python -m bench.voice_journeys.stack up --checkout ../.. --out /tmp/vj-main
  cd services && .venv/bin/python -m bench.voice_journeys.stack down --out /tmp/vj-main
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = Path(os.environ.get("MICROMIND_ASSETS", Path.home() / "MicroscopeMind"))
KEYS = Path.home() / "Desktop" / "_Documents" / "MicroscopeMind" / "micromind.txt"


def words(plan: dict, folder: Path) -> None:
    """Each step's words as 24 kHz mono WAV, said by macOS's own voices (the user's, or the audience's)."""
    folder.mkdir(parents=True, exist_ok=True)
    for step in plan["steps"]:
        if step["kind"] == "typed":
            continue
        voice = plan["voices"][step.get("voice", "user")]
        aiff, wav = folder / f"{step['id']}.aiff", folder / f"{step['id']}.wav"
        subprocess.run(["say", "-v", voice, "-o", str(aiff), step["say"]], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@24000", "-c", "1", str(aiff), str(wav)], check=True)
        aiff.unlink()


def healthy(url: str, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def up(args: argparse.Namespace) -> None:
    checkout, out = Path(args.checkout).resolve(), Path(args.out).resolve()
    plan = json.loads((HERE / "steps.json").read_text())
    for sub in ("home", "recordings", "datasets"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    dist = checkout / "apps" / "studio" / "dist"
    if args.build or not (dist / "index.html").exists():
        subprocess.run(["npm", "run", "build"], cwd=checkout / "apps" / "studio", check=True)
    test = dist / "voice-test"
    words(plan, test)
    shutil.copy(HERE / "harness.js", test / "harness.js")
    (test / "steps.json").write_text(json.dumps(plan))
    python = Path(sys.executable)
    first = ASSETS / "footage" / plan["steps"][0]["footage"]
    bridge = subprocess.Popen(
        [str(python), str(checkout / "experiments/iphone-camera-feed/scripts/iphone_bridge_server.py"),
         "--port", str(args.bridge_port), "--footage-dir", str(ASSETS / "footage"),
         "--recording-dir", str(out / "recordings"), "--datasets-dir", str(out / "datasets"), "--simulate", str(first),
         # Its own snapshots and clearer pictures (orchestrator/paths.py under its home), playable as sources.
         "--media-root", str(out / "home" / "snapshots"), "--media-root", str(out / "home" / "results"),
         # Its "camera" is no device at all: a second stack never opens the phone the live app holds.
         "--target-device", "MicroscopeMind second stack (no camera)"],
        stdout=(out / "bridge.log").open("ab"), stderr=subprocess.STDOUT, start_new_session=True)
    env = {**os.environ, "MICROMIND_EDITION": "research", "MICROMIND_HOME": str(out / "home"),
           "MICROMIND_RECORDINGS_DIR": str(out / "recordings"), "MICROMIND_DATASETS_DIR": str(out / "datasets"),
           "MICROMIND_KEYS_FILE": str(Path(args.keys))}
    if args.settings:
        env["MICROMIND_SETTINGS"] = args.settings  # this arm's settings (app.py), e.g. {"reflex": "lean"}
    env.pop("MICROMIND_SAVES", None)
    app = subprocess.Popen(
        [str(python), "-m", "orchestrator.server", "--port", str(args.port),
         "--bridge-url", f"http://127.0.0.1:{args.bridge_port}"],
        cwd=checkout / "services", env=env, stdout=(out / "orchestrator.log").open("ab"), stderr=subprocess.STDOUT,
        start_new_session=True)
    (out / "stack.json").write_text(json.dumps({"bridge": bridge.pid, "app": app.pid, "port": args.port,
                                               "bridgePort": args.bridge_port, "checkout": str(checkout),
                                               "started": time.time()}))
    ok = healthy(f"http://127.0.0.1:{args.port}/api/health", 180)
    print(json.dumps({"ok": ok, "studio": f"http://127.0.0.1:{args.port}/", "bridge": f"http://127.0.0.1:{args.bridge_port}",
                      "home": str(out / "home"),
                      "run": "await import('/voice-test/harness.js'); "
                             f"await window.__bench.run(await (await fetch('/voice-test/steps.json')).json(), "
                             f"{{bridge: 'http://127.0.0.1:{args.bridge_port}'}})"}, indent=1))


def down(args: argparse.Namespace) -> None:
    out = Path(args.out).resolve()
    stack = json.loads((out / "stack.json").read_text())
    for pid in (stack["app"], stack["bridge"]):
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    print("stopped", stack["checkout"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("up")
    u.add_argument("--checkout", required=True, help="the repo checkout to run (main for the baseline, a worktree)")
    u.add_argument("--out", required=True, help="a scratch folder for this run's home, logs and recordings")
    u.add_argument("--port", type=int, default=28870)
    u.add_argument("--bridge-port", type=int, default=28865)
    u.add_argument("--keys", default=str(KEYS if KEYS.exists() else Path.home() / "Desktop" / "micromind.txt"))
    u.add_argument("--build", action="store_true", help="rebuild the studio even if dist exists")
    u.add_argument("--settings", default="", help='settings for this arm, JSON: {"reflex": "lean"}')
    d = sub.add_parser("down")
    d.add_argument("--out", required=True)
    args = parser.parse_args()
    up(args) if args.cmd == "up" else down(args)


if __name__ == "__main__":
    main()
