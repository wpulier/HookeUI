"""What a microscope the bridge drives must do (`Backend`), and the backends: Micro-Manager through pymmcore-plus
(`mmcore.py`: the demo configuration, or a rig's own) and an OpenFlexure microscope over its HTTP API
(`openflexure.py`). Only the bridge process holds one; nothing else in Hooke opens the hardware.

A backend does what it is told and reports what is: it checks nothing against the user's limits (the bridge does, before
calling it). Units are µm for the stage and ms for exposure. Each method blocks until the hardware is done (a move has
stopped, a picture is read). Something the microscope cannot do raises `Unavailable` with words for the user.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Protocol


class Unavailable(Exception):
    """This microscope cannot do that (no shutter, no channels, no exposure control...)."""


@dataclass
class Frame:
    """One picture: its pixels (a numpy array, 2D or 3D) or an encoded JPEG, and what was set when it was taken."""
    pixels: object = None
    jpeg: bytes | None = None
    meta: dict = field(default_factory=dict)


class Backend(Protocol):
    name: str

    def describe(self) -> dict: ...
    def position(self) -> tuple[float, float, float]: ...
    def move_to(self, x: float | None, y: float | None, z: float | None) -> None: ...
    def snap(self) -> Frame: ...
    def exposure(self) -> float | None: ...
    def set_exposure(self, ms: float) -> None: ...
    def channels(self) -> list[str]: ...
    def channel(self) -> str | None: ...
    def set_channel(self, name: str) -> None: ...
    def light(self) -> bool | None: ...
    def set_light(self, on: bool) -> None: ...
    def stop(self) -> None: ...


def make(name: str, **options) -> Backend:
    """A backend by name: "mm-demo" (Micro-Manager's demo configuration), "mm" (a configuration file: config=), or
    "openflexure" (url=)."""
    if name in ("mm-demo", "mm"):
        from .mmcore import MMBackend

        return MMBackend(config=options.get("config") if name == "mm" else None, adapters=options.get("adapters"))
    if name == "openflexure":
        from .openflexure import OpenFlexure

        return OpenFlexure(options["url"], um_per_step=float(options.get("um_per_step") or 1.0),
                           um_per_z_step=options.get("um_per_z_step"))
    raise ValueError(f"no backend called {name!r}: mm-demo, mm or openflexure")


class Recorder:
    """Wraps a backend and keeps every command sent to it (the fuzz test's witness: what the hardware was told, not
    what the bridge meant to tell it)."""

    def __init__(self, backend: Backend) -> None:
        self._b = backend
        self.name = backend.name
        self.commands: list[tuple] = []
        self.lock = threading.Lock()

    def __getattr__(self, name: str):
        attr = getattr(self._b, name)
        if name in ("move_to", "set_exposure", "set_channel", "set_light", "stop") and callable(attr):
            def recorded(*args, _attr=attr, _name=name):
                with self.lock:
                    self.commands.append((_name, *args))
                return _attr(*args)
            return recorded
        return attr


def acquire_by_steps(backend: Backend, plan, *, here: tuple[float, float, float], exposure_ms: float,
                     on_frame: Callable[[Frame, dict], None], stopping: threading.Event,
                     move: Callable[[float | None, float | None, float | None], None] | None = None) -> int:
    """An acquisition made of moves and snaps, in the plan's axis order, for a backend with no acquisition engine of its
    own. `move` is how the bridge moves (in steps no larger than the user allows). Returns the frames made."""
    import itertools
    import time

    move = move or backend.move_to
    positions = list(plan.positions) or [here]
    channels = list(plan.channels) or [(None, None)]
    axes = {"t": range(plan.loops), "p": range(len(positions)), "c": range(len(channels)), "z": range(len(plan.z_offsets))}
    order = [a for a in plan.axis_order if a in axes]
    made, started, last_t = 0, time.monotonic(), -1
    for index in itertools.product(*(axes[a] for a in order)):
        if stopping.is_set():
            break
        at = dict(zip(order, index))
        if at["t"] != last_t:  # wait for this time point
            wait = started + at["t"] * plan.interval - time.monotonic()
            if wait > 0 and stopping.wait(wait):
                break
            last_t = at["t"]
        px, py, pz = positions[at["p"]]
        base_z = pz if pz is not None else here[2]
        reached = move(px, py, base_z + plan.z_offsets[at["z"]])
        if reached is False or stopping.is_set():
            break  # stopped on the way: nothing is taken at a place it did not reach
        name, exposure = channels[at["c"]]
        if name is not None:
            backend.set_channel(name)
        if exposure is not None:
            backend.set_exposure(exposure)
        frame = backend.snap()
        on_frame(frame, {"t": at["t"], "p": at["p"], "c": at["c"], "z": at["z"], "channel": name,
                         "exposureMs": exposure if exposure is not None else exposure_ms})
        made += 1
    return made
