"""A second stack for the voice journeys bench: a bridge playing test footage (or a still image) and an orchestrator,
on their own ports with their own home, from any checkout (main for the baseline, a branch to compare). It never
touches the live app: no studio.sh (which stops every MicroscopeMind), no shared home. It also writes every plan's
recorded words and the player beside that checkout's studio build (dist/voice-test/<plan>/), which only a stack
started here serves.

  cd services && .venv/bin/python -m bench.voice_journeys.stack up --checkout .. --out /tmp/vj-main
  cd services && .venv/bin/python -m bench.voice_journeys.stack up --checkout .. --out /tmp/vj-rt \\
      --plan part-2.5 --voice gpt-realtime-2.1 --talk hold --faults
  cd services && .venv/bin/python -m bench.voice_journeys.stack restart --out /tmp/vj-rt   # "quit and reopen Hooke"
  cd services && .venv/bin/python -m bench.voice_journeys.stack down --out /tmp/vj-main

--voice and --talk choose the voice option and the talk mode for this stack (MICROMIND_SETTINGS, refused ones logged).
--faults turns on the one brain's bench faults (MICROMIND_BENCH_FAULTS=1: a forced provider close, a failing tool),
only ever on a second stack.
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
PLANS = HERE / "plans"
ASSETS = Path(os.environ.get("MICROMIND_ASSETS", Path.home() / "MicroscopeMind"))
KEYS = Path.home() / "Desktop" / "_Documents" / "MicroscopeMind" / "micromind.txt"
DEFAULT_PLAN = "demo-2026-10-11"


def plan_path(name: str) -> Path:
    """A plan by its id (plans/<id>.json) or a path to one."""
    path = Path(name)
    return path if path.suffix == ".json" and path.exists() else PLANS / f"{name}.json"


def load_plan(name: str) -> dict:
    plan = json.loads(plan_path(name).read_text())
    plan.setdefault("id", plan_path(name).stem)
    return plan


def words(plan: dict, folder: Path) -> None:
    """Each step's words as 24 kHz mono WAV, said by macOS's own voices (the user's, or the audience's); "[[slnc N]]"
    in the words is a pause of N ms. Kept when the words and voice are unchanged."""
    folder.mkdir(parents=True, exist_ok=True)
    for step in [*plan.get("setup", []), *plan["steps"]]:
        if step["kind"] in ("typed", "idle", "restart") or not step.get("say"):
            continue
        voice = plan["voices"][step.get("voice", "user")]
        aiff, wav, said = folder / f"{step['id']}.aiff", folder / f"{step['id']}.wav", folder / f"{step['id']}.said"
        key = f"{voice}\n{step['say']}"
        if wav.exists() and said.exists() and said.read_text() == key:
            continue
        subprocess.run(["say", "-v", voice, "-o", str(aiff), step["say"]], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@24000", "-c", "1", str(aiff), str(wav)], check=True)
        aiff.unlink()
        said.write_text(key)


NOISE_S = 20  # the still's loop: long enough that its seam is rare, short enough to make in a second


def noisy_loop(still: Path, folder: Path) -> Path:
    """The still image as a short looping video with sensor-like noise, so no two frames are byte-identical: the bridge
    heals a camera whose frames are (FROZEN_HEAL_S: a frozen iPhone), which stopped a still's live view about 17 s after
    a Project's Use live camera (2026-10-29). A file encode, never a capture; made once per stack."""
    out = folder / f"{still.stem}-noise.mp4"
    if not out.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "25", "-i", str(still),
                        "-t", str(NOISE_S), "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,noise=alls=3:allf=t+u,format=yuv420p",
                        "-c:v", "libx264", "-crf", "18", "-r", "25", str(out)], check=True)
    return out


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


def settings_for(args: argparse.Namespace) -> str:
    """This arm's settings: --settings JSON, with --voice and --talk on top."""
    settings = json.loads(args.settings) if args.settings else {}
    if args.voice:
        settings["voice"] = args.voice
    if args.talk:
        settings["talk"] = args.talk
    return json.dumps(settings) if settings else ""


def publish(checkout: Path, build: bool) -> Path:
    """The player and every plan's words beside the studio build."""
    dist = checkout / "apps" / "studio" / "dist"
    if build or not (dist / "index.html").exists():
        subprocess.run(["npm", "run", "build"], cwd=checkout / "apps" / "studio", check=True)
    test = dist / "voice-test"
    test.mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "harness.js", test / "harness.js")
    for path in sorted(PLANS.glob("*.json")):
        plan = load_plan(str(path))
        words(plan, test / plan["id"])
        (test / f"{plan['id']}.json").write_text(json.dumps(plan))
    return test


def start_app(stack: dict, out: Path) -> int:
    """The orchestrator, from the stack's record (its checkout, ports and settings); returns its pid."""
    env = {**os.environ, **stack["env"]}
    env.pop("MICROMIND_SAVES", None)
    if not stack["env"].get("MICROMIND_BENCH_FAULTS"):
        env.pop("MICROMIND_BENCH_FAULTS", None)
    app = subprocess.Popen(
        [sys.executable, "-m", "orchestrator.server", "--port", str(stack["port"]),
         "--bridge-url", f"http://127.0.0.1:{stack['bridgePort']}"],
        cwd=Path(stack["checkout"]) / "services", env=env, stdout=(out / "orchestrator.log").open("ab"),
        stderr=subprocess.STDOUT, start_new_session=True)
    return app.pid


def up(args: argparse.Namespace) -> dict:
    checkout, out = Path(args.checkout).resolve(), Path(args.out).resolve()
    plan = load_plan(args.plan)
    for sub in ("home", "recordings", "datasets", "footage"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    publish(checkout, args.build)
    camera = plan.get("camera") or {}
    if camera.get("still"):  # a still image as the camera (the bundled example), with sensor-like noise: no store needed
        footage = out / "footage"
        first = noisy_loop(checkout / camera["still"], footage)
    else:
        first, footage = ASSETS / "footage" / plan["steps"][0]["footage"], ASSETS / "footage"
    bridge = subprocess.Popen(
        [sys.executable, str(checkout / "experiments/iphone-camera-feed/scripts/iphone_bridge_server.py"),
         "--port", str(args.bridge_port), "--footage-dir", str(footage),
         "--recording-dir", str(out / "recordings"), "--datasets-dir", str(out / "datasets"), "--simulate", str(first),
         # Its own snapshots and clearer pictures (orchestrator/paths.py under its home), playable as sources.
         "--media-root", str(out / "home" / "snapshots"), "--media-root", str(out / "home" / "results"),
         # Its "camera" is no device at all: a second stack never opens the phone the live app holds.
         "--target-device", "MicroscopeMind second stack (no camera)"],
        stdout=(out / "bridge.log").open("ab"), stderr=subprocess.STDOUT, start_new_session=True)
    env = {"MICROMIND_EDITION": "research", "MICROMIND_HOME": str(out / "home"),
           "MICROMIND_RECORDINGS_DIR": str(out / "recordings"), "MICROMIND_DATASETS_DIR": str(out / "datasets"),
           "MICROMIND_KEYS_FILE": str(Path(args.keys)), "MICROMIND_KEYCHAIN": "off", "CAMERA_NO_PHONE": "1"}
    settings = settings_for(args)
    if settings:
        env["MICROMIND_SETTINGS"] = settings  # this arm's settings (app.py), e.g. {"voice": "gpt-realtime-2.1"}
    if args.faults:
        env["MICROMIND_BENCH_FAULTS"] = "1"
    if getattr(args, "research_cap", None) is not None:
        env["MICROMIND_RESEARCH_CAP"] = f"{args.research_cap:.2f}"  # what researchers may spend today on this stack
    stack = {"bridge": bridge.pid, "port": args.port, "bridgePort": args.bridge_port, "checkout": str(checkout),
             "plan": plan["id"], "settings": settings, "faults": bool(args.faults), "env": env, "started": time.time()}
    stack["app"] = start_app(stack, out)
    (out / "stack.json").write_text(json.dumps(stack))
    ok = healthy(f"http://127.0.0.1:{args.port}/api/health", 180)
    told = {"ok": ok, "studio": f"http://127.0.0.1:{args.port}/", "bridge": f"http://127.0.0.1:{args.bridge_port}",
            "home": str(out / "home"), "plan": plan["id"], "settings": settings,
            "run": "await import('/voice-test/harness.js'); "
                   f"await window.__bench.run(await (await fetch('/voice-test/{plan['id']}.json')).json(), "
                   f"{{bridge: 'http://127.0.0.1:{args.bridge_port}'}})"}
    print(json.dumps(told, indent=1))
    return told


def stop_app(stack: dict) -> None:
    try:
        os.killpg(stack["app"], signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    end = time.monotonic() + 20
    while time.monotonic() < end:
        try:
            os.killpg(stack["app"], 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.25)
    try:
        os.killpg(stack["app"], signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def restart(args: argparse.Namespace) -> dict:
    """Quit and reopen Hooke: the orchestrator stopped and started again on the same home (the bridge keeps going,
    as the camera would). Prints how long it took to be healthy."""
    out = Path(args.out).resolve()
    stack = json.loads((out / "stack.json").read_text())
    t0 = time.monotonic()
    stop_app(stack)
    stack["app"] = start_app(stack, out)
    (out / "stack.json").write_text(json.dumps(stack))
    ok = healthy(f"http://127.0.0.1:{stack['port']}/api/health", 180)
    told = {"ok": ok, "seconds": round(time.monotonic() - t0, 1)}
    print(json.dumps(told))
    return told


def down(args: argparse.Namespace) -> None:
    out = Path(args.out).resolve()
    stack = json.loads((out / "stack.json").read_text())
    stop_app(stack)
    try:
        os.killpg(stack["bridge"], signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    print("stopped", stack["checkout"])


def up_arguments(u: argparse.ArgumentParser) -> None:
    u.add_argument("--checkout", required=True, help="the repo checkout to run (main for the baseline, a worktree)")
    u.add_argument("--out", required=True, help="a scratch folder for this run's home, logs and recordings")
    u.add_argument("--plan", default=DEFAULT_PLAN, help="the plan: plans/<id>.json, or a path")
    u.add_argument("--port", type=int, default=28870)
    u.add_argument("--bridge-port", type=int, default=28865)
    u.add_argument("--keys", default=str(KEYS if KEYS.exists() else Path.home() / "Desktop" / "micromind.txt"))
    u.add_argument("--build", action="store_true", help="rebuild the studio even if dist exists")
    u.add_argument("--settings", default="", help='settings for this arm, JSON: {"reflex": "lean"}')
    u.add_argument("--voice", default="", help="the voice option: gpt-live-1 or gpt-realtime-2.1")
    u.add_argument("--talk", default="", help="the talk mode for the one brain: hold or open")
    u.add_argument("--faults", action="store_true", help="accept the bench's injected faults (second stack only)")
    u.add_argument("--research-cap", type=float, help="the day's research cap on this stack, in dollars")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    up_arguments(sub.add_parser("up"))
    for name in ("down", "restart"):
        sub.add_parser(name).add_argument("--out", required=True)
    args = parser.parse_args()
    {"up": up, "down": down, "restart": restart}[args.cmd](args)


if __name__ == "__main__":
    main()
