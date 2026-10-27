"""Admission witnesses for real overlapping cells/click RPCs; no workload changes."""
import asyncio
import time

import httpx


def resident_worker(health, model):
    return next((w for w in health.get("workers", []) if w.get("ready") and not w.get("hung")
                 and model in w.get("resident", []) and model not in w.get("loading", {})), None)


def cell_rpc(detail, model):
    rows = [r for r in detail.get("steps", []) if r.get("scope") == "cells" and r.get("selected") == model]
    return rows[0] if len(rows) == 1 else None


async def detail(stack, receipt):
    response = await stack.http.get(stack.orch_url + "/api/execution/" + receipt)
    response.raise_for_status()
    return response.json()


async def wait_started(stack, viewer, since, sent_at, model, timeout=30):
    """Poll only before timing. A queued call, cold load, or completed RPC cannot admit."""
    deadline = time.monotonic() + timeout
    receipt = None
    last = {"error": "No running cells RPC was observed."}
    while time.monotonic() < deadline:
        if any(r.get("for") == "cells" for r in viewer.results[since:]):
            return {"error": "Cells finished before the timed burst could overlap its RPC."}
        try:
            if receipt is None:
                response = await stack.http.get(stack.orch_url + "/api/execution")
                response.raise_for_status()
                refs = [r for r in response.json().get("entries", [])
                        if r.get("kind") == "cells" and r.get("at", 0) >= sent_at]
                if len(refs) > 1:
                    return {"error": "Ambiguous cells request identities; overlap is unproved."}
                receipt = refs[0]["id"] if refs else None
            if receipt is not None:
                row = cell_rpc(await detail(stack, receipt), model)
                if row and row.get("workerFinishedAt") is not None:
                    return {"error": "Cells RPC already finished; overlap is unproved.", "receipt": receipt, "rpc": row}
                if row and row.get("workerStartedAt") is not None:
                    health = await stack.orch_health()
                    worker = resident_worker(health, model)
                    if worker and worker["name"] == row.get("worker"):
                        return {"receipt": receipt, "rpc": row, "residentObservedAt": time.time(), "model": model}
            last = {"error": "Cells remained queued, loading, or lacked a started execution receipt."}
        except (httpx.HTTPError, ValueError):
            last = {"error": "Cells execution observation was unavailable."}
        await asyncio.sleep(.1)
    return last


def overlap(clicks, row):
    start, end = row.get("workerStartedAt"), row.get("workerFinishedAt")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end < start:
        return []
    return [i for i, click in enumerate(clicks) if click.get("ok")
            and start <= click["sentAt"] <= click["receivedAt"] <= end]
