"""How big things are (orchestrator/scale.py, roadmap W4-1): the objective in use, measuring an
outline, and calibrating with a known length. Sizes come from here, never from a model."""
from __future__ import annotations

from ...scale import OBJECTIVES, round_um, size_words, sizes
from ..registry import Registry, Tool, ToolContext, ToolFailed, schema
from .common import OBJECT, describe, log_measurement, resolve_object


def add_scale_tools(registry: Registry, services) -> None:
    hub, scale = services.hub, services.scale
    if scale is None:
        return

    def reading_now():
        reading = hub.scale_reading() if hasattr(hub, "scale_reading") else None
        if reading is None:
            if scale.objective is None:
                raise ToolFailed("I don't know the scale yet: tell me which objective is in use (\"I'm on 40x\"), "
                                 "or calibrate with a stage micrometer", next=None)  # no next: the message says what to do
            raise ToolFailed("I can't see the eyepiece circle right now, so I can't measure",
                             next="make sure the eyepiece circle is in view and lit, then ask again")
        return reading

    async def set_objective(ctx: ToolContext, args: dict) -> str:
        objective = args.get("objective")
        try:
            objective = int(float(str(objective).lower().rstrip("x"))) if objective not in (None, "", "unknown", 0, "0") else None
            scale.set_objective(objective, args.get("field_number"), via=ctx.channel)
        except (TypeError, ValueError) as exc:
            raise ToolFailed(str(exc), next=None) from exc  # no next: the message says what was wrong
        if objective is None:
            return "forgot the objective: sizes are not shown until it is set again"
        reading = hub.scale_reading() if hasattr(hub, "scale_reading") else None
        return (f"objective set to {objective}x; " + (reading.words if reading else
                f"sizes will show once the eyepiece circle is in view (field number {scale.field_number:g} mm)"))

    async def measure(ctx: ToolContext, args: dict) -> str:
        reading = reading_now()
        obj = resolve_object(hub, args.get("object") or args.get("thing"), ctx)
        size = sizes(obj.polygons, reading)
        if not size:
            raise ToolFailed(f"{describe(obj)} has no outline to measure", next="click it to outline it, then ask")
        log_measurement(services, ctx, "size", objectId=obj.id, label=obj.label or None,
                        lengthUm=round(size["lengthUm"], 3), widthUm=round(size["widthUm"], 3),
                        areaUm2=round(size["areaUm2"], 3), how="longest and shortest width (Feret) of the outline, and its area")
        return f"{describe(obj)} is {size_words(size)}; {reading.words}"

    async def calibrate_scale(ctx: ToolContext, args: dict) -> str:
        try:
            micrometres = float(str(args.get("micrometres")).replace("µm", "").replace("um", "").strip())
        except (TypeError, ValueError):
            raise ToolFailed("say how long it is in micrometres (\"that line is 100 µm\")",
                             next=None) from None  # no next: the message says what to do
        obj = resolve_object(hub, args.get("object") or args.get("thing"), ctx)
        info = hub.latest_info
        if info is None or info.fov is None:
            raise ToolFailed("I can't see the eyepiece circle right now",
                             next="make sure the eyepiece circle is in view and lit, then ask again")
        # The outline's longest width, in pixels, is the known length.
        from ...scale import Reading

        unit = sizes(obj.polygons, Reading(1.0, 1.0, scale.objective or 1, True))
        try:
            reading = scale.calibrate(unit["lengthUm"], micrometres, info.fov.radius)
        except (KeyError, ValueError) as exc:
            raise ToolFailed(str(exc), next=None) from exc  # no next: the message says what was wrong
        return f"calibrated at {reading.objective}x: {reading.words} (from {describe(obj)} = {round_um(micrometres)} µm)"

    registry.add(Tool("set_objective", "The user says which objective (magnification) is in use (\"I'm on 40x\", "
                      "\"switched to the 10x\", \"it's the 100x oil\"): every outline then gets a size. Also the "
                      "eyepiece's field number if they give it (\"the eyepiece is 10x/20\": 20).",
                      schema({"objective": {"type": "integer", "description": f"one of {', '.join(map(str, OBJECTIVES))}, "
                                                                            "or 0 to forget it"},
                              "field_number": {"type": "number", "description": "eyepiece field number in mm, if said"}},
                             ["objective"]),
                      set_objective, inline_ms=400, family="measuring", sets=("objective",)))
    registry.add(Tool("measure", "How big something outlined is, in µm, measured by the app (the only source of sizes: "
                      "never state a size yourself). Fails, saying what is needed, when the scale is not known yet.",
                      schema({"object": OBJECT}, ["object"]), measure, kind="query", inline_ms=400, family="measuring", outline_refs=("object",)))
    registry.add(Tool("calibrate_scale", "The user says how long something outlined really is (\"that line of the "
                      "micrometer is 100 µm\"): fixes the scale for this objective from it.",
                      schema({"object": OBJECT, "micrometres": {"type": "number"}}, ["object", "micrometres"]),
                      calibrate_scale, inline_ms=400, family="measuring", outline_refs=("object",)))
