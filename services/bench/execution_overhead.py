"""CPU-only instrumentation budget check. No services, assets, models, I/O or network.

Run from services: python -m bench.execution_overhead --samples 2000
This measures a mocked twelve-stage request, not camera/GPU or whole-policy latency.
The recording path and bounds are real; the adapter deliberately does no work.
"""
import argparse
import asyncio
import gc
import json
import statistics
import time
import tracemalloc

from orchestrator import execution


async def adapter():
    return 1


recorded = execution.traced("mock-adapter")(adapter)


async def measure(samples, stages):
    results = {}
    for mode in ("baseline", "disabled", "switched-off", "enabled"):
        log = execution.ExecutionLog(enabled=mode in {"switched-off", "enabled"}, publish=lambda _: None)
        if mode == "switched-off":
            with log.request("click"):
                await recorded()
            log.set_enabled(False)
        durations = []
        for _ in range(samples):
            started = time.perf_counter_ns()
            if mode == "baseline":
                for _ in range(stages):
                    await adapter()
            else:
                with log.request("click"):
                    for _ in range(stages):
                        await recorded()
            durations.append((time.perf_counter_ns() - started) / 1000)
        ordered = sorted(durations)
        results[mode] = {"p50Us": round(statistics.median(ordered), 3),
                         "p95Us": round(ordered[int(len(ordered) * 0.95)], 3)}
        await asyncio.sleep(0)  # let other ready tasks progress between independent modes
    delta = results["enabled"]["p95Us"] - results["baseline"]["p95Us"]
    results["addedRequestP95Us"] = round(delta, 3)
    results["addedStageP95Us"] = round(delta / stages, 3)
    results["disabledAddedRequestP95Us"] = round(max(results[mode]["p95Us"] for mode in ("disabled", "switched-off"))
                                                - results["baseline"]["p95Us"], 3)
    return results


def memory():
    gc.collect()
    tracemalloc.start()
    log = execution.ExecutionLog(enabled=True)
    for _ in range(3000):
        with log.request("click", day="2026-10-06", thread="t1"):
            for n in range(16):
                with execution.span("segment", selected="x" * 128, model="y" * 128,
                                    adapter="z" * 128, provider="local", device="cpu"):
                    execution.bind(object_id=f"o{n}")
    kept, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"retainedMiB": round(kept / 2**20, 3), "peakMiB": round(peak / 2**20, 3),
            "steps": log._steps, "requests": len(log.references())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=2000)
    args = parser.parse_args()
    timing = asyncio.run(measure(max(100, args.samples), 12))
    usage = memory()
    report = {"scope": "CPU-only, mocked twelve-stage request; excludes live camera/GPU/UI", "timing": timing,
              "memory": usage, "budgets": {"addedStageP95Us": 100, "addedRequestP95Us": 2000,
                                           "disabledAddedRequestP95Us": 100, "retainedMiB": 8}}
    print(json.dumps(report, indent=2))
    if (timing["addedStageP95Us"] > 100 or timing["addedRequestP95Us"] > 2000
            or timing["disabledAddedRequestP95Us"] > 100 or usage["retainedMiB"] > 8):
        raise SystemExit("instrumentation budget exceeded; keep collection disabled")


if __name__ == "__main__":
    main()
