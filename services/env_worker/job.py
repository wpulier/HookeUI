"""An entry's job, run once in its own environment (orchestrator/jobs.py; phase 3's E5): a registration of a volume to an
atlas, minutes long, written to files rather than returned on a pipe.

    python -I job.py <entry folder> <module:function> <request.json>

The function takes the request (its input files, the output folder, its parameters, the folders of the data it reads)
and writes its outputs into the output folder; what it returns (the outputs it made and what it measured) is written
last as `result.json` in that folder, atomically. Everything it prints goes to the job's log. Nothing else of the app is
importable here, and the network is refused (MICROMIND_REFUSE_NETWORK): what it needs is pinned and fetched by the
catalog, never by its code.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import signal
import threading
import sys
import traceback
from pathlib import Path


def _refuse_network() -> None:
    spec = importlib.util.spec_from_file_location("micromind_env_server", Path(__file__).resolve().parent / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._refuse_network()


def main() -> int:
    # The supervisor keeps this pipe open for our lifetime. If it dies, this process group's native children must
    # stop too; no process-table search or another proof's path is involved. The runner always owns its session.
    def parent_gone():
        while os.read(0, 1):
            pass
        os.killpg(os.getpid(), signal.SIGKILL)

    threading.Thread(target=parent_gone, name="job-supervisor", daemon=True).start()
    _refuse_network()
    folder, target, request_path = sys.argv[1], sys.argv[2], sys.argv[3]
    request = json.loads(Path(request_path).read_text())
    out = Path(request["output"])
    sys.path.insert(0, folder)
    module_name, _, function_name = target.partition(":")
    try:
        result = {"ok": True, **(getattr(importlib.import_module(module_name), function_name)(request) or {})}
    except Exception as exc:  # noqa: BLE001 - the job's own error, said with its last words
        traceback.print_exc()
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:2000]}
    tmp = out / "result.json.tmp"
    tmp.write_text(json.dumps(result, indent=1, default=str))
    tmp.replace(out / "result.json")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
