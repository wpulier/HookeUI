"""The typed actions the scope bridge takes (services/scope_bridge/AGENTS.md): each read from JSON and checked for its
shape before anything else looks at it. An unknown action, an unknown field, a value of the wrong type or a number
that is not finite is refused with what was wrong; nothing is guessed or quietly dropped.

  snap                                   a picture with the scope's camera
  move        x, y, z (µm), relative     the stage, absolute or by so much (each axis optional)
  autofocus   range (µm), step (µm)      the bridge's own sweep through focus, inside the limits
  exposure    ms                         the camera's exposure
  channel     name                       one of the scope's channel presets
  light       on                         the light; on waits for the user's yes
  acquire     plan                       a multi-dimensional acquisition (useq-schema's MDASequence, a subset)
  stop                                   stop moving and acquiring, now

The acquisition plan is the part of useq-schema's MDASequence a plan needs here: `channels` (names, or {config,
exposure}), `time_plan` {interval (s), loops}, `z_plan` {range, step} or {above, below, step} (µm, around each
position's z), `stage_positions` [{x, y, z}] (µm; none: where the stage is) and `axis_order`. `Plan.frames` and
`Plan.seconds` are what it will make and take, so a large one can be asked about before it starts.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .limits import finite, number

KINDS = ("snap", "move", "autofocus", "exposure", "channel", "light", "acquire", "stop")


@dataclass(frozen=True)
class Action:
    kind: str

    def wire(self) -> dict:
        return {"kind": self.kind}


@dataclass(frozen=True)
class Snap(Action):
    kind: str = "snap"


@dataclass(frozen=True)
class Move(Action):
    x: float | None = None
    y: float | None = None
    z: float | None = None
    relative: bool = False
    kind: str = "move"

    def wire(self) -> dict:
        return {"kind": "move", **{a: getattr(self, a) for a in ("x", "y", "z") if getattr(self, a) is not None},
                "relative": self.relative}


@dataclass(frozen=True)
class Autofocus(Action):
    range: float = 20.0  # µm searched, centred on where the focus is
    step: float = 2.0
    kind: str = "autofocus"

    def wire(self) -> dict:
        return {"kind": "autofocus", "range": self.range, "step": self.step}


@dataclass(frozen=True)
class Exposure(Action):
    ms: float = 10.0
    kind: str = "exposure"

    def wire(self) -> dict:
        return {"kind": "exposure", "ms": self.ms}


@dataclass(frozen=True)
class Channel(Action):
    name: str = ""
    kind: str = "channel"

    def wire(self) -> dict:
        return {"kind": "channel", "name": self.name}


@dataclass(frozen=True)
class Light(Action):
    on: bool = False
    kind: str = "light"

    def wire(self) -> dict:
        return {"kind": "light", "on": self.on}


@dataclass(frozen=True)
class Stop(Action):
    kind: str = "stop"


@dataclass(frozen=True)
class Plan:
    channels: tuple[tuple[str, float | None], ...] = ()  # (preset, exposure ms or None: the camera's own)
    loops: int = 1
    interval: float = 0.0  # s between time points
    z_offsets: tuple[float, ...] = (0.0,)  # µm around each position's z
    positions: tuple[tuple[float | None, float | None, float | None], ...] = ()  # absolute µm; empty: here
    axis_order: str = "tpcz"

    @property
    def frames(self) -> int:
        return max(1, len(self.positions)) * self.loops * max(1, len(self.channels)) * len(self.z_offsets)

    def seconds(self, exposure_ms: float) -> float:
        """How long it takes at least: the time plan, or every frame's exposure and a little to move between them."""
        per_frame = 0.05 + max([e for _, e in self.channels if e is not None] or [exposure_ms]) / 1000.0
        return max((self.loops - 1) * self.interval, self.frames * per_frame)

    def wire(self) -> dict:
        out: dict = {"axis_order": self.axis_order}
        if self.channels:
            out["channels"] = [{"config": c, **({"exposure": e} if e is not None else {})} for c, e in self.channels]
        if self.loops > 1:
            out["time_plan"] = {"interval": self.interval, "loops": self.loops}
        if self.z_offsets != (0.0,):
            out["z_offsets"] = list(self.z_offsets)
        if self.positions:
            out["stage_positions"] = [{k: v for k, v in zip("xyz", p) if v is not None} for p in self.positions]
        return out


@dataclass(frozen=True)
class Acquire(Action):
    plan: Plan = field(default_factory=Plan)
    kind: str = "acquire"

    def wire(self) -> dict:
        return {"kind": "acquire", "plan": self.plan.wire(), "frames": self.plan.frames}


MAX_FRAMES = 100_000  # a plan larger than this is not a plan
MAX_Z = 2_000


def _only(data: dict, allowed: set[str], what: str) -> None:
    extra = sorted(set(data) - allowed)
    if extra:
        raise ValueError(f"{what} takes {', '.join(sorted(allowed)) or 'nothing'}, not {', '.join(extra)}")


def _flag(v, what: str) -> bool:
    if not isinstance(v, bool):
        raise ValueError(f"{what} is true or false")
    return v


def parse(data) -> Action:
    """The typed action this JSON asks for; ValueError says what is wrong with it."""
    if not isinstance(data, dict):
        raise ValueError("an action is an object with a kind")
    kind = data.get("kind")
    if kind not in KINDS:
        raise ValueError(f"the kind is one of {', '.join(KINDS)}, not {kind!r}")
    rest = {k: v for k, v in data.items() if k != "kind"}
    if kind in ("snap", "stop"):
        _only(rest, set(), kind)
        return Snap() if kind == "snap" else Stop()
    if kind == "move":
        _only(rest, {"x", "y", "z", "relative"}, "move")
        axes = {a: number(rest[a], a) for a in ("x", "y", "z") if a in rest and rest[a] is not None}
        if not axes:
            raise ValueError("a move names at least one of x, y and z")
        return Move(**axes, relative=_flag(rest.get("relative", False), "relative"))
    if kind == "autofocus":
        _only(rest, {"range", "step"}, "autofocus")
        rng = number(rest.get("range", 20.0), "range")
        step = number(rest.get("step", 2.0), "step")
        if not 0 < step <= rng <= 500:
            raise ValueError("autofocus searches a range of up to 500 µm in steps more than 0 and no larger than it")
        if rng / step > 200:
            raise ValueError("autofocus takes at most 200 steps")
        return Autofocus(range=rng, step=step)
    if kind == "exposure":
        _only(rest, {"ms"}, "exposure")
        ms = number(rest.get("ms"), "ms")
        if ms <= 0:
            raise ValueError("an exposure is more than 0 ms")
        return Exposure(ms=ms)
    if kind == "channel":
        _only(rest, {"name"}, "channel")
        name = rest.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("a channel is named")
        return Channel(name=name.strip())
    if kind == "light":
        _only(rest, {"on"}, "light")
        return Light(on=_flag(rest.get("on"), "on"))
    _only(rest, {"plan"}, "acquire")
    return Acquire(plan=parse_plan(rest.get("plan")))


def parse_plan(plan) -> Plan:
    if not isinstance(plan, dict):
        raise ValueError("an acquisition has a plan (an object)")
    _only(plan, {"channels", "time_plan", "z_plan", "stage_positions", "axis_order"}, "a plan")
    channels: list[tuple[str, float | None]] = []
    for c in plan.get("channels") or []:
        if isinstance(c, str):
            channels.append((c, None))
        elif isinstance(c, dict):
            _only(c, {"config", "exposure"}, "a plan's channel")
            name = c.get("config")
            if not isinstance(name, str) or not name:
                raise ValueError("a plan's channel names its config")
            e = number(c["exposure"], "a channel's exposure") if c.get("exposure") is not None else None
            if e is not None and e <= 0:
                raise ValueError("a channel's exposure is more than 0 ms")
            channels.append((name, e))
        else:
            raise ValueError("a plan's channels are names or {config, exposure}")
    loops, interval = 1, 0.0
    tp = plan.get("time_plan")
    if tp is not None:
        if not isinstance(tp, dict):
            raise ValueError("time_plan is {interval, loops}")
        _only(tp, {"interval", "loops"}, "time_plan")
        loops_f = number(tp.get("loops", 1), "loops")
        if loops_f < 1 or loops_f != int(loops_f):
            raise ValueError("loops is a whole number, at least 1")
        loops, interval = int(loops_f), number(tp.get("interval", 0.0), "interval")
        if interval < 0:
            raise ValueError("an interval is not negative")
    offsets: tuple[float, ...] = (0.0,)
    zp = plan.get("z_plan")
    if zp is not None:
        if not isinstance(zp, dict):
            raise ValueError("z_plan is {range, step} or {above, below, step}")
        _only(zp, {"range", "step", "above", "below"}, "z_plan")
        step = number(zp.get("step"), "z step")
        if step <= 0:
            raise ValueError("a z step is more than 0")
        if "range" in zp:
            half = number(zp["range"], "z range") / 2
            lo, hi = -half, half
        else:
            lo, hi = -number(zp.get("below", 0.0), "below"), number(zp.get("above", 0.0), "above")
        if hi < lo:
            raise ValueError("a z plan's range is not negative")
        n = int(math.floor((hi - lo) / step + 1e-9)) + 1
        if n > MAX_Z:
            raise ValueError(f"a z plan takes at most {MAX_Z} planes")
        offsets = tuple(round(lo + i * step, 6) for i in range(n))
    positions = []
    for p in plan.get("stage_positions") or []:
        if not isinstance(p, dict):
            raise ValueError("a stage position is {x, y, z}")
        _only(p, {"x", "y", "z"}, "a stage position")
        positions.append(tuple(number(p[a], a) if p.get(a) is not None else None for a in ("x", "y", "z")))
    order = plan.get("axis_order", "tpcz")
    if not isinstance(order, str) or sorted(order) != sorted("tpcz"):
        raise ValueError("axis_order is an order of t, p, c and z")
    out = Plan(tuple(channels), loops, interval, offsets, tuple(positions), order)
    if out.frames > MAX_FRAMES:
        raise ValueError(f"a plan makes at most {MAX_FRAMES} pictures, not {out.frames}")
    return out


