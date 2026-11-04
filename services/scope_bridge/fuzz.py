"""Random commands at the scope bridge, and a witness that reads the microscope itself after each (AGENTS.md beside this).

The commands are what a confused model, a misheard sentence or a hostile caller could send: moves inside, at the edge
of and far outside the limits, single steps far larger than allowed, numbers that are not numbers (NaN, strings,
booleans), unknown kinds and fields, exposures out of range, channels that do not exist, the light on with no
approval, a forged one, one used twice or one given for another action, acquisitions too large, out of the box or with
a bad channel, autofocus searches of every size, and stops fired from another thread in the middle of all of it. The
fuzz plays the user too: some offers are answered yes (the same action again with its offer), most are not.

A breach is anything the limits or the yes forbid that happened on the microscope itself, read from the backend, never
from the bridge's own receipts:
- the stage outside the box (read back after every command);
- one motor command that moved further than the step the user allows (each command the backend was sent, with where
  the stage was when it was sent), outside an acquisition the user said yes to;
- an exposure outside its range;
- the light on when the user's last word on it was not a yes to turning it on;
- an acquisition larger than the limits that ran without its yes;
- a receipt that says done for anything the bridge should have refused.

  cd services && scope_bridge/.venv/bin/python -m scope_bridge.fuzz --backend mm-demo --n 2000    # Micro-Manager demo
  (tests/test_scope_bridge.py runs it against the virtual OpenFlexure microscope in every make check)
"""
from __future__ import annotations

import json
import math
import random
import tempfile
import threading
import time
from pathlib import Path

from .bridge import ScopeBridge, TOLERANCE
from .limits import Limits


class Witness:
    """Wraps the backend: every motor and light command, with where the stage was when it was sent."""

    def __init__(self, backend) -> None:
        self._b = backend
        self.name = backend.name
        self.moves: list[tuple[tuple[float, float, float], tuple]] = []
        self.lights: list[bool] = []
        self.exposures: list[float] = []
        self.in_approved_acquisition = False
        self.lock = threading.Lock()

    def __getattr__(self, name):
        return getattr(self._b, name)

    def move_to(self, x, y, z):
        before = self._b.position()
        with self.lock:
            self.moves.append((before, (x, y, z), self.in_approved_acquisition))
        return self._b.move_to(x, y, z)

    def set_light(self, on):
        with self.lock:
            self.lights.append(bool(on))
        return self._b.set_light(on)

    def set_exposure(self, ms):
        with self.lock:
            self.exposures.append(float(ms))
        return self._b.set_exposure(ms)


JUNK = [float("nan"), float("inf"), -float("inf"), "12", None, True, [], {}, 1e308, -0.0]


def random_action(rng: random.Random, limits: Limits, here: dict, channels: list[str]) -> object:
    roll = rng.random()
    box = {a: limits.box(a) for a in ("x", "y", "z")}

    def coordinate(axis: str, relative: bool):
        lo, hi = box[axis] or (-100.0, 100.0)
        step = limits.step_xy if axis != "z" else limits.step_z
        kind = rng.random()
        if kind < 0.08:
            return rng.choice(JUNK)
        if relative:
            if kind < 0.6:
                return rng.uniform(-step, step) * 0.9
            if kind < 0.8:
                return rng.uniform(-3 * step, 3 * step)
            return rng.choice([1, -1]) * rng.uniform(hi - lo, 10 * (hi - lo) + 1e6)
        if kind < 0.5:
            return min(hi, max(lo, here[axis] + rng.uniform(-step, step) * 0.95))
        if kind < 0.7:
            return rng.choice([lo, hi]) + rng.uniform(-step * 0.1, step * 0.1)  # at the edge, either side
        return rng.uniform(lo - 5 * (hi - lo), hi + 5 * (hi - lo))

    if roll < 0.40:
        relative = rng.random() < 0.6
        axes = rng.sample(["x", "y", "z"], rng.randint(1, 3))
        action = {"kind": "move", **{a: coordinate(a, relative) for a in axes}}
        if rng.random() < 0.9:
            action["relative"] = relative if rng.random() < 0.95 else rng.choice(JUNK)
        if rng.random() < 0.03:
            action["speed"] = 5  # an unknown field
        return action
    if roll < 0.48:
        return {"kind": "exposure", "ms": rng.choice([rng.uniform(-50, 3000), rng.uniform(*limits.exposure_ms),
                                                      rng.choice(JUNK)])}
    if roll < 0.53:
        return {"kind": "channel", "name": rng.choice(channels + ["Brightfield", "", "DAPI ", 7, None])}
    if roll < 0.63:
        return {"kind": "light", "on": rng.choice([True, True, False, "yes", 1, None])}
    if roll < 0.75:
        return {"kind": "acquire", "plan": random_plan(rng, limits, here, channels)}
    if roll < 0.82:
        return {"kind": "autofocus", "range": rng.choice([rng.uniform(1, 60), rng.uniform(100, 900), rng.choice(JUNK)]),
                "step": rng.choice([rng.uniform(0.5, 10), rng.uniform(-1, 0.4), rng.choice(JUNK)])}
    if roll < 0.88:
        return {"kind": "snap"}
    if roll < 0.92:
        return {"kind": "stop"}
    return rng.choice([None, "move", [], {"kind": "teleport"}, {"kind": "move"}, {"kind": "snap", "x": 1},
                       {"x": 5}, {"kind": "acquire", "plan": "everything"}, {"kind": "light"}])


def random_plan(rng: random.Random, limits: Limits, here: dict, channels: list[str]) -> object:
    if rng.random() < 0.05:
        return rng.choice(JUNK)
    plan: dict = {}
    if channels and rng.random() < 0.6:
        plan["channels"] = [rng.choice(channels + ["Nope"]) if rng.random() < 0.7 else
                            {"config": rng.choice(channels), "exposure": rng.choice([rng.uniform(1, 50),
                                                                                       rng.uniform(-5, 5000)])}
                            for _ in range(rng.randint(1, 3))]
    if rng.random() < 0.5:
        plan["time_plan"] = {"interval": rng.choice([0.0, 0.01, rng.uniform(0, 30)]),
                             "loops": rng.choice([1, 2, 3, rng.randint(1, 400)])}
    if rng.random() < 0.5:
        plan["z_plan"] = {"range": rng.choice([2.0, 4.0, rng.uniform(0, 400)]), "step": rng.choice([1.0, 2.0, 0.5])}
    if rng.random() < 0.5:
        (xlo, xhi), (ylo, yhi) = limits.x or (-1, 1), limits.y or (-1, 1)
        spread = rng.choice([0.05, 0.5, 3.0])
        plan["stage_positions"] = [{"x": rng.uniform(xlo, xhi) if spread < 1 else rng.uniform(xlo - 1e4, xhi + 1e4),
                                    "y": rng.uniform(ylo, yhi) if spread < 1 else rng.uniform(ylo - 1e4, yhi + 1e4)}
                                   for _ in range(rng.randint(1, 4))]
    return plan


def fuzz(bridge: ScopeBridge, witness: Witness, n: int = 500, seed: int = 1, stopper: bool = True,
         yes_share: float = 0.4) -> dict:
    rng = random.Random(seed)
    breaches: list[str] = []
    counts: dict[str, int] = {}
    offers: dict[str, str] = {}  # offer -> the action it was given for (JSON)
    used: set[str] = set()
    light_said_yes = False  # the user's last word on the light: a yes to on
    stop_flag = threading.Event()

    timing = random.Random(seed + 1)  # the stops' own: the commands stay the same whatever the threads' timing

    def stops():
        while not stop_flag.wait(timing.uniform(0.05, 0.6)):
            bridge.stop()

    stopper_thread = threading.Thread(target=stops, daemon=True) if stopper else None
    if stopper_thread:
        stopper_thread.start()

    def key(action) -> str:
        return json.dumps(action, sort_keys=True, default=str)

    pending: list[tuple[dict, str]] = []  # offers the fuzz may answer yes to
    try:
        for i in range(n):
            here = bridge.state()
            approval = None
            if pending and rng.random() < yes_share:  # the user says yes to one
                action, offer = pending.pop(rng.randrange(len(pending)))
                approval = offer
            elif offers and rng.random() < 0.1:  # a forged, replayed or misdirected approval
                action = random_action(rng, bridge.limits, here, bridge.backend.channels())
                approval = rng.choice([rng.choice(list(offers)), "y999", "", "yes"])
            else:
                action = random_action(rng, bridge.limits, here, bridge.backend.channels())
            legitimate = (approval is not None and approval in offers and approval not in used
                          and offers[approval] == key(action))
            moves_before = len(witness.moves)
            witness.in_approved_acquisition = legitimate and isinstance(action, dict) and action.get("kind") == "acquire"
            receipt = bridge.do(action, approval)
            witness.in_approved_acquisition = False
            status = receipt["status"]
            counts[status] = counts.get(status, 0) + 1
            if approval is not None and approval in offers:
                used.add(approval)
            if status == "needs-yes":
                offers[receipt["offer"]] = key(action)
                pending.append((action, receipt["offer"]))
            kind = receipt.get("kind")
            # --- the witness: the microscope itself ---
            state = bridge.state()
            for axis in ("x", "y", "z"):
                box = bridge.limits.box(axis)
                if box and not box[0] - TOLERANCE[axis] <= state[axis] <= box[1] + TOLERANCE[axis]:
                    breaches.append(f"#{i} {axis} at {state[axis]:.2f} outside {box} after {action!r}")
            lim = bridge.limits
            for before, (x, y, z), approved in witness.moves[moves_before:]:
                tx, ty, tz = (before[0] if x is None else x, before[1] if y is None else y, before[2] if z is None else z)
                if not approved and math.hypot(tx - before[0], ty - before[1]) > lim.step_xy + 1e-6:
                    breaches.append(f"#{i} one command moved {math.hypot(tx - before[0], ty - before[1]):.1f} µm across")
                if not approved and abs(tz - before[2]) > lim.step_z + 1e-6:
                    breaches.append(f"#{i} one command moved focus {abs(tz - before[2]):.2f} µm")
            exposure = state.get("exposureMs")
            if exposure is not None and witness.exposures and not lim.exposure_ms[0] <= exposure <= lim.exposure_ms[1]:
                breaches.append(f"#{i} exposure {exposure} outside {lim.exposure_ms}")
            if kind == "light" and status == "done":
                on = (receipt.get("action") or {}).get("on")
                if on and not legitimate:
                    breaches.append(f"#{i} the light went on without the user's yes ({approval!r})")
                light_said_yes = bool(on)
            if state.get("light") and not light_said_yes:
                breaches.append(f"#{i} the light is on and the user did not say yes to it")
            if kind == "acquire" and status in ("done", "stopped") and not legitimate:
                plan_frames = (receipt.get("action") or {}).get("frames") or 0
                if plan_frames > lim.frames:
                    breaches.append(f"#{i} an acquisition of {plan_frames} pictures ran without a yes")
            if status == "done" and kind == "move" and not receipt.get("verified", True) and not bridge.stopping.is_set():
                pass  # a move stopped short is said as such (verified False), not a breach
    finally:
        stop_flag.set()
        if stopper_thread:
            stopper_thread.join(2)
        bridge.do({"kind": "light", "on": False})
    return {"commands": n, "statuses": counts, "breaches": breaches, "motorCommands": len(witness.moves),
            "offers": len(offers), "yesSaid": len(used)}


def main() -> None:
    import argparse
    import logging

    logging.getLogger("pymmcore-plus").setLevel(logging.ERROR)  # every stop of an acquisition is a warning there

    from .backends import make

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend", default="mm-demo")
    parser.add_argument("--adapters")
    parser.add_argument("--url")
    parser.add_argument("--n", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    witness = Witness(make(args.backend, adapters=args.adapters, url=args.url))
    with tempfile.TemporaryDirectory() as folder:
        bridge = ScopeBridge(witness, Path(folder))
        started = time.time()
        result = fuzz(bridge, witness, n=args.n, seed=args.seed)
        result["seconds"] = round(time.time() - started, 1)
        result["backend"] = args.backend
    print(json.dumps({**result, "breaches": result["breaches"][:20], "breachCount": len(result["breaches"])}, indent=1))
    raise SystemExit(1 if result["breaches"] else 0)


if __name__ == "__main__":
    main()
