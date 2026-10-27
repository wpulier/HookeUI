"""Live first (the partner's §1g; AGENTS.md "Live never waits on deep work"): the replay bench and the fine-tune job
each put another Cellpose-SAM (about 1.2 GB) on the GPU, and `nice` lowers only their CPU priority, so they refuse to
run while the app is running, unless they are told they may run beside it (`--beside-live`, BESIDE_LIVE=1), which
says it may slow the live view's clicks and its timings mean little then.

The app is running when its orchestrator answers on MICROMIND_LIVE_URL (default http://127.0.0.1:8770), or when an
orchestrator server process runs on this Mac (its GPU workers are its children): a sim's or another checkout's
counts too, since what a job loads competes with theirs.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request

LIVE_URL = "http://127.0.0.1:8770"
SERVICE = "microscope-mind-orchestrator"
SERVER = "-m orchestrator.server"


def running() -> str:
    """Why the app counts as running here now ("" when it does not)."""
    url = os.environ.get("MICROMIND_LIVE_URL") or LIVE_URL
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=1.0) as reply:
            if json.loads(reply.read(65536) or b"{}").get("service") == SERVICE:
                return f"the live app answers on {url}"
    except (OSError, ValueError):
        pass
    try:
        listing = subprocess.run(["ps", "-Ao", "pid=,args="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    servers = [line.split(None, 1) for line in listing.splitlines() if SERVER in line and "grep" not in line]
    if servers:
        pid, args = servers[0][0], servers[0][1] if len(servers[0]) > 1 else ""
        port = args.split("--port", 1)[1].split()[0] if "--port" in args else "its default port"
        return f"an orchestrator runs here (pid {pid}, port {port}) with its GPU workers"
    return ""


def refuse(job: str, beside_live: bool) -> str:
    """The refusal for `job` while the app runs ("" when it may go ahead). With `beside_live`, it may: the caller says so
    in its result."""
    if beside_live:
        return ""
    why = running()
    if not why:
        return ""
    return (f"{job} waits for the app: {why}. It would put another Cellpose-SAM (about 1.2 GB) on the GPU beside the live "
            "view and slow its clicks. Stop the app (make stop), or run it anyway with --beside-live (BESIDE_LIVE=1); "
            "its timings then mean little.")
