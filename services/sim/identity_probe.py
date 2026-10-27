"""An identity answer witnessed through existing health, scene and result messages."""
from __future__ import annotations

import asyncio
import copy
import re
import time


def _named_lines(words: str, ids) -> dict[str, str]:
    """A complete answer names each expected individual once, one per line."""
    rows = {}
    for line in words.splitlines():
        if not line.strip():
            continue
        names = re.findall(r"\((i\d+)\):", line)
        if len(names) != 1 or names[0] not in ids or names[0] in rows:
            return {}
        rows[names[0]] = line
    return rows if set(rows) == set(ids) else {}


def followed_outlines(population: str, members: list[str], ids: list[str], objects: dict, words: str) -> dict[str, str]:
    """Bind this two-cell journey using the public cell names and each outline's promotion note.

    Receipt order and scene order do not imply identity. Missing/ambiguous links fail setup.
    """
    rows = _named_lines(words, ids)
    outlines = {oid: obj for oid, obj in objects.items() if obj.get("source") == f"ai:measured:{population}"}
    if not rows or len(set(members)) != len(ids) or len(outlines) != len(ids):
        return {}
    bound, seen = {}, set()
    for individual, line in rows.items():
        name = line.split(f" ({individual}):", 1)[0]
        match = re.fullmatch(r"(c\d+), outlined(?: as .+)?", name)
        if not match or match[1] not in members or match[1] in seen:
            return {}
        seen.add(match[1])
        own = [oid for oid, obj in outlines.items() if str(obj.get("note") or "").startswith(
            f"{match[1]} of {population} (")]
        if len(own) != 1:
            return {}
        bound[individual] = own[0]
    return bound


async def observe(stack, viewer, deadline: float, *, samples=None) -> dict:
    """Retain one answer and its scene only when an unchanged server revision brackets it.

    A changed/missing revision is inconclusive, never evidence that pixels did or did not match.
    The caller's single deadline covers health reads, send and reply; no per-request extension.
    """
    probe = {"coherent": False, "versions": [], "objects": {}, "reply": {}, "at": time.time()}
    if samples is not None:
        samples.append(probe)  # retain partial evidence even if the parent cancels at the same deadline
    try:
        async with asyncio.timeout_at(deadline):
            probe["phase"] = "health-before"
            before = await stack.orch_health()
            probe["healthBeforeAt"] = time.time()
            delivered = viewer.scene_version
            probe["objects"] = copy.deepcopy(viewer.scene)
            probe["versions"] = [delivered, before.get("scene", {}).get("version")]
            request_id = f"identity-{time.monotonic_ns()}"
            probe["requestId"] = request_id
            mark = len(viewer.results)
            probe["phase"] = "send"
            await viewer.send({"type": "tool", "name": "individuals", "args": {"ask": "where"},
                               "requestId": request_id})
            probe["phase"] = "reply"
            probe["reply"] = await viewer.result_for("tool", mark,
                timeout=max(0.0, deadline - asyncio.get_running_loop().time()), request_id=request_id) or {}
            probe["replyAt"] = time.time()
            if not probe["reply"]:
                raise TimeoutError("identity reply was not received within the scenario deadline")
            probe["phase"] = "health-after"
            after = await stack.orch_health()
            probe["healthAfterAt"] = time.time()
            probe["versions"].append(after.get("scene", {}).get("version"))
            probe["epochs"] = [(state.get("motion") or {}).get("epoch") for state in (before, after)]
            probe["coherent"] = (type(delivered) is int and all(v == delivered for v in probe["versions"])
                                 and probe["epochs"][0] is not None and probe["epochs"][0] == probe["epochs"][1]
                                 and bool(probe["reply"]))
            probe["why"] = "matched scene revision" if probe["coherent"] else "scene revision or epoch was not stable"
    except asyncio.CancelledError:
        probe['coherent'] = False
        probe['why'] = 'identity observation interrupted before completion'
        probe['error'] = f"CancelledError: {probe['why']}"
        probe['failedAt'] = time.time()
        raise
    except TimeoutError:
        probe["why"] = "identity observation reached the scenario deadline"
        probe["error"] = f"TimeoutError: {probe['why']}"
        probe["failedAt"] = time.time()
    except Exception as exc:
        # A partial reply and the preceding witnesses remain evidence on transport failure.
        # Cancellation above retains only inconclusive evidence and still propagates.
        probe["error"] = f"{type(exc).__name__}: {exc}"
        probe["why"] = f"identity observation failed during {probe['phase']}: {probe['error']}"
        probe["failedAt"] = time.time()
    return probe


async def follow_return(stack, viewer, outlines: dict[str, str], *, seconds: float = 15.0) -> dict:
    """Witness an unverified refusal (of at least one individual) and subsequent verified placement of all within one
    fixed budget; any sample saying an unverified individual is in view fails it."""
    deadline = asyncio.get_running_loop().time() + seconds
    return await _follow_return(stack, viewer, outlines, deadline, _return_result(outlines))


def _return_result(outlines):
    return {"passed": False, "refused": False, "placed": False, "violations": [], "samples": [], "summary": "",
            "error": "", "outlines": dict(outlines)}


async def during_return(stack, viewer, outlines, returning, *, seconds=15.0):
    """Arm the observer before restoration; return and observation share one fixed budget.

    The callable receives the same monotonic deadline for its epoch wait. No identity state is
    delayed or invented. A transition too short to witness still fails the original predicates.
    """
    deadline = asyncio.get_running_loop().time() + seconds
    result = _return_result(outlines)
    transition = result['transition'] = {}
    armed = asyncio.Event()
    probe = asyncio.create_task(_follow_return(stack, viewer, outlines, deadline, result, armed))
    try:
        async with asyncio.timeout_at(deadline):
            await armed.wait()
            transition['returnStartedAt'] = time.time()
            await returning(deadline)
            transition['returnCompletedAt'] = time.time()
        await probe  # the probe's own waits consume that same deadline
    except Exception as exc:
        result['passed'] = False
        result['error'] = f'{type(exc).__name__}: {exc or "return reached the scenario deadline"}'
        transition['returnFailedAt'] = time.time()
    finally:
        if not probe.done():
            probe.cancel()
        await asyncio.gather(probe, return_exceptions=True)
    return result


async def _follow_return(stack, viewer, outlines, deadline, result, armed=None):
    if armed is not None:
        result['transition']['probeStartedAt'] = time.time()
        armed.set()
    while asyncio.get_running_loop().time() < deadline:
        sample = await observe(stack, viewer, deadline, samples=result['samples'])
        if sample.get("error"):
            result["error"] = sample["error"]
            break
        reply = sample["reply"]
        if sample["coherent"] and reply.get("ok") and outlines:
            tracks = {i: sample["objects"].get(oid, {}).get("track") for i, oid in outlines.items()}
            words = str(reply.get("summary") or "")
            rows = _named_lines(words, outlines)
            if not rows:
                sample["violation"] = "identity answer is incomplete or names unexpected/duplicate individuals"
            else:
                misplaced = [i for i, line in rows.items()
                             if tracks[i] in ("refinding", "lost") and "in view now" in line]
                if misplaced:
                    sample["violation"] = f"unverified individuals placed in view: {', '.join(misplaced)}"
            if sample.get("violation"):
                result["violations"].append(sample)
            # Both public refusal wordings count: lost continuity and unmatched local pixels.
            # The same coherent, unverified outline and subsequent placement are still required.
            # A refusal is witnessed when an individual not verified in this coherent sample is said to be
            # unconfirmed; one is enough to show the observation covered a time they were not yet verified. All of
            # them at once was asked before, and failed on correct answers when one was re-found by its own pixels in
            # an earlier pass than the other (the 2026-10-15 gate: i1 "can't confirm" while i2 already matched).
            if rows and not sample.get("violation") and any(
                    tracks[i] in ("refinding", "lost") and (
                        "can't confirm it's still there" in line or "where: can't tell: continuity was lost" in line)
                    for i, line in rows.items()):
                result["refused"] = True
            if rows and all(track == "tracking" for track in tracks.values()) and all(
                    "its own pixels matched there" in line and "can't tell" not in line for line in rows.values()):
                result["placed"] = True
                result["summary"] = words
                result["passed"] = result["refused"] and not result["violations"]
        if result["passed"] or result["violations"]:
            break
        # Four probes a second at most; health/transport waits consume the same 15s budget.
        await asyncio.sleep(min(0.25, max(0.0, deadline - asyncio.get_running_loop().time())))
    return result
