"""Read bounded existing receipts after a timed burst; keep observations chronological."""
import httpx


async def collect(stack, observations, started, finished):
    result = {"clicks": observations, "receipts": []}
    try:
        response = await stack.http.get(stack.orch_url + "/api/execution")
        response.raise_for_status()
        refs = [r for r in response.json().get("entries", [])
                if r.get("kind") == "click" and started <= r.get("at", 0) <= finished]
        for ref in sorted(refs, key=lambda r: (r["at"], r["id"]))[:16]:
            response = await stack.http.get(stack.orch_url + "/api/execution/" + ref["id"])
            response.raise_for_status()
            detail = response.json()
            rows = [{key: value for key, value in row.items()
                     if key.startswith("probe") or key in {"id", "frameId", "rpcId", "adapterMs", "model", "worker"}}
                    for row in detail.get("steps", []) if "probeWallMs" in row]
            result["receipts"].append({"id": ref["id"], "at": ref["at"], "steps": rows})
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        result["error"] = "Diagnostic receipt retrieval failed; original timing result retained."
    return result
