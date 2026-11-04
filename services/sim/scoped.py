"""The scope bridge moving a microscope the live view sees, end to end on the simulated camera (services/scope_bridge).

The camera bridge plays the virtual microscope (sim/virtual_scope.py) as the camera, and a scope bridge drives the same
virtual microscope through its OpenFlexure API, as one rig's camera and motors would be. Every move the scope bridge
makes must show in the live view as the orchestrator measures it (the picture moves opposite to the slide, by the
magnification), and a move the limits refuse must show nothing. The truth is the virtual microscope's own.
"""
from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path


async def _offset(viewer, settle: float = 1.5, timeout: float = 15.0) -> tuple[float, float, int] | None:
    """The live view's measured slide offset once it has been still for `settle` s: (dx, dy, epoch)."""
    end, since, last = time.monotonic() + timeout, None, None
    while time.monotonic() < end:
        _, h, _ = viewer.latest()
        if h.get("tracked") and h.get("steady"):
            now = (float(h.get("dx") or 0), float(h.get("dy") or 0), int(h.get("epoch") or 0))
            if last is not None and abs(now[0] - last[0]) < 0.5 and abs(now[1] - last[1]) < 0.5 and now[2] == last[2]:
                since = since or time.monotonic()
                if time.monotonic() - since >= settle:
                    return now
            else:
                since = None
            last = now
        await asyncio.sleep(0.05)
    return None


async def scenario_scope_move(stack, viewer):
    """The scope bridge's moves seen in the live view; refusals move nothing; a large acquisition waits for a yes."""
    from scope_bridge.backends.openflexure import OpenFlexure
    from scope_bridge.bridge import ScopeBridge
    from scope_bridge.limits import Limits

    from .guided import _on_stage, _put_back
    from .harness import Outcome, default_video
    from .virtual_scope import leaf_scope

    out = Outcome("scope bridge: its moves show in the live view, its refusals move nothing")
    if stack.video.resolve() != default_video().resolve():
        out.check("the labelled leaf recording is the stack's footage", False, str(stack.video))
        return out
    before_feed = (await stack.http.get(f"{stack.bridge_url}/feed")).json()
    scope, _ = leaf_scope(stack.video, off=(0.2, 0.1), z=0.0, motor_speed=400.0)
    scope.serve()
    folder = Path(tempfile.mkdtemp(prefix="scope-move-"))
    try:
        if not await _on_stage(stack, viewer, out, scope):
            return out
        bridge = ScopeBridge(OpenFlexure(scope.url, um_per_step=1.0, um_per_z_step=0.01), folder)
        x, y, z = bridge.backend.position()
        bridge.limits = Limits(x=(x - 150, x + 150), y=(y - 150, y + 150), z=(z - 5, z + 5), step_xy=100, step_z=2,
                               frames=4, seconds=30)
        start = await _offset(viewer)
        if not out.check("the live view is still and tracked before the move", start is not None):
            return out
        r = await asyncio.to_thread(bridge.do, {"kind": "move", "x": 60.0, "relative": True})
        out.check("a move inside the limits is done and verified", r["status"] == "done" and r.get("verified"), str(r)[:300])
        after = await _offset(viewer)
        want = -60.0 * scope.zoom  # the picture moves opposite to the slide, magnified
        moved = None if after is None or after[2] != start[2] else after[0] - start[0]
        out.observations.append({"move": {"slideUm": 60, "pictureDxPx": moved, "expectedPx": want}})
        out.check(f"the live view saw it: the picture moved {moved if moved is None else round(moved, 1)} px "
                  f"(expected {want:.0f})", moved is not None and abs(moved - want) <= 0.1 * abs(want) + 3)
        for action, what in (({"kind": "move", "x": 500.0, "relative": True}, "past the limits"),
                             ({"kind": "move", "y": 120.0, "relative": True}, "a step over the limit")):
            r = await asyncio.to_thread(bridge.do, action)
            still = await _offset(viewer)
            shift = None if still is None or after is None else abs(still[0] - after[0]) + abs(still[1] - after[1])
            out.check(f"a move {what} is refused and the live view shows no move", r["status"] == "refused"
                      and shift is not None and shift < 3, f"{r.get('why')}; shift {shift}")
        big = {"kind": "acquire", "plan": {"time_plan": {"interval": 0, "loops": 3}, "z_plan": {"range": 2, "step": 1}}}
        asked = await asyncio.to_thread(bridge.do, big)
        done = await asyncio.to_thread(bridge.do, big, asked.get("offer"))
        out.check("a large acquisition waits for the yes, then makes its 9 pictures",
                  asked["status"] == "needs-yes" and done["status"] == "done" and len(done.get("made") or []) == 9,
                  f"{asked.get('ask')} / {done['status']}")
        receipts = (folder / "receipts.jsonl").read_text().splitlines()
        out.check("every action has a receipt", len(receipts) == 5, str(len(receipts)))
        health = await stack.orch_health()
        out.check("the live view kept going throughout", health.get("state") == "live", str(health.get("state")))
    finally:
        await _put_back(stack, before_feed)
        scope.close()
    return out
