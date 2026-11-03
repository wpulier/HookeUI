"""The microscope's own motors, camera and light, through the scope bridge (services/scope_bridge; orchestrator/scope.py).

Only when a scope bridge is connected (`MICROMIND_SCOPE_URL`). Every call is one typed action the bridge checks against
the limits the user set and answers with a receipt, filed in the session's journal. The light going on and a large
acquisition wait for the user's yes: the first call gets the bridge's offer and the words to ask with; the same call
again with `confirms` (the offer) and `asked_with` (the user's reply, quoted as they said it) goes ahead. The quote is
checked against what the user said in this request; the yes itself is the model's to judge.
"""
from __future__ import annotations

import json

from ..registry import Registry, Tool, ToolContext, ToolFailed, schema

AXIS = {"type": "number", "description": "µm"}
YES = {"confirms": {"type": "string", "description": "the offer id (y3) the last answer asked the user about"},
       "asked_with": {"type": "string", "description": "the user's own reply to that question, quoted exactly"}}


def add_scope_tools(registry: Registry, services) -> None:
    client = getattr(services, "scope", None)
    if client is None:
        return
    journal = getattr(services, "scope_journal", None)

    async def act(ctx: ToolContext, action: dict, approval: str | None = None, *, yes: str = "") -> str:
        import httpx

        from ...scope import said

        try:
            receipt = await client.do(action, approval)
        except httpx.HTTPError as exc:
            raise ToolFailed(f"the microscope's bridge did not answer ({type(exc).__name__})",
                             next="check that the scope bridge is running (make status), then ask again") from exc
        if callable(journal):
            try:
                journal(receipt, ctx)
            except Exception:
                pass
        words = said(receipt)
        if receipt.get("status") == "refused":
            raise ToolFailed(words, next="say why, and what would be inside the limits; the user can change the limits")
        if receipt.get("status") == "failed":
            raise ToolFailed(words, next="say what happened and where the stage is now")
        if receipt.get("status") == "needs-yes":
            return (f"{words} Not done yet. Ask the user exactly this; on their yes call {yes} again with the same "
                    f"arguments, confirms={receipt['offer']!r} and asked_with set to their reply as they said it.")
        return words

    def approval_of(ctx: ToolContext, args: dict) -> str | None:
        offer = str(args.get("confirms") or "").strip()
        if not offer:
            return None
        quote = " ".join(str(args.get("asked_with") or "").split()).lower()
        heard = " ".join(str(ctx.question or "").split()).lower()
        if not quote or quote not in heard:
            raise ToolFailed("a yes is the user's own words in this request, quoted in asked_with",
                             next="ask the user, and quote their reply exactly", argument=True)
        return offer

    async def microscope(ctx: ToolContext, args: dict) -> str:
        import httpx

        try:
            state = await client.state()
        except httpx.HTTPError as exc:
            raise ToolFailed("the microscope's bridge did not answer", next="check that it is running (make status)") from exc
        s, lim, b = state.get("state") or {}, state.get("limits") or {}, state.get("backend") or {}
        box = "; ".join(f"{a} {lim[a][0]:.0f} to {lim[a][1]:.0f} µm" for a in ("x", "y", "z") if lim.get(a))
        return (f"{b.get('backend', 'a microscope')}: stage at x {s.get('x')}, y {s.get('y')}, z {s.get('z')} µm; "
                f"exposure {s.get('exposureMs')} ms; channel {s.get('channel')}; light "
                f"{'on' if s.get('light') else 'off' if s.get('light') is False else 'not switched from here'}; "
                f"channels: {', '.join(state.get('channels') or []) or 'none'}. The user's limits: {box or 'none set'}; "
                f"at most {lim.get('step_xy')} µm across and {lim.get('step_z')} µm in focus a move; "
                f"exposure {lim.get('exposure_ms')} ms; an acquisition over {lim.get('frames')} pictures or "
                f"{lim.get('seconds')} s asks first" + (f"; busy: {state['busy']}" if state.get("busy") else ""))

    registry.add(Tool("microscope", "The microscope's motors, camera and light as they are now (a connected scope): "
                      "where the stage is, exposure, channel, light, its channels, and the limits the user set.",
                      schema({}), microscope, kind="query", inline_ms=1500, family="scope"))

    async def move_stage(ctx: ToolContext, args: dict) -> str:
        action = {"kind": "move", "relative": args.get("relative", True) is not False,
                  **{a: args[a] for a in ("x", "y", "z") if args.get(a) is not None}}
        return await act(ctx, action)

    registry.add(Tool("move_stage", "Move the microscope's motorised stage (x, y across the slide; z the focus), in µm, "
                      "by so much (relative, the default) or to a place. The bridge refuses anything past the limits "
                      "the user set, or a single move larger than they allow; say so and offer a smaller move.",
                      schema({"x": AXIS, "y": AXIS, "z": AXIS,
                              "relative": {"type": "boolean", "description": "true (default): by so much; false: to"}}),
                      move_stage, inline_ms=10000, family="scope"))

    async def scope_snap(ctx: ToolContext, args: dict) -> str:
        return await act(ctx, {"kind": "snap"})

    registry.add(Tool("scope_snap", "Take a picture with the microscope's own camera (kept by the scope bridge, with "
                      "what was set when it was taken).", schema({}), scope_snap, inline_ms=5000, family="scope"))

    async def scope_focus(ctx: ToolContext, args: dict) -> str:
        action = {"kind": "autofocus", **{k: args[k] for k in ("range", "step") if args.get(k) is not None}}
        return await act(ctx, action)

    registry.add(Tool("scope_focus", "Autofocus the microscope: a sweep through focus (range µm, step µm) to the "
                      "sharpest plane, never outside the user's focus limits.",
                      schema({"range": {"type": "number", "description": "µm searched (default 20)"},
                              "step": {"type": "number", "description": "µm between planes (default 2)"}}),
                      scope_focus, inline_ms=30000, family="scope"))

    async def scope_exposure(ctx: ToolContext, args: dict) -> str:
        return await act(ctx, {"kind": "exposure", "ms": args.get("ms")})

    registry.add(Tool("scope_exposure", "Set the microscope camera's exposure in ms, within the user's limits.",
                      schema({"ms": {"type": "number"}}, ["ms"]), scope_exposure, inline_ms=3000, family="scope"))

    async def scope_channel(ctx: ToolContext, args: dict) -> str:
        return await act(ctx, {"kind": "channel", "name": args.get("name")})

    registry.add(Tool("scope_channel", "Switch the microscope to one of its channel presets (microscope lists them).",
                      schema({"name": {"type": "string"}}, ["name"]), scope_channel, inline_ms=5000, family="scope"))

    async def scope_light(ctx: ToolContext, args: dict) -> str:
        on = args.get("on") is True
        return await act(ctx, {"kind": "light", "on": on}, approval_of(ctx, args) if on else None, yes="scope_light")

    registry.add(Tool("scope_light", "Turn the microscope's light on or off. On waits for the user's yes (ask them, then "
                      "call again with confirms and asked_with); off is done at once.",
                      schema({"on": {"type": "boolean"}, **YES}, ["on"]), scope_light, inline_ms=3000, family="scope"))

    async def scope_acquire(ctx: ToolContext, args: dict) -> str:
        plan = args.get("plan")
        if isinstance(plan, str):
            try:
                plan = json.loads(plan)
            except ValueError:
                raise ToolFailed("the plan is a JSON object (useq-schema's MDASequence)", argument=True) from None
        return await act(ctx, {"kind": "acquire", "plan": plan}, approval_of(ctx, args), yes="scope_acquire")

    registry.add(Tool("scope_acquire", "Run an acquisition on the microscope: a useq-schema MDASequence plan with "
                      "channels (names or {config, exposure}), time_plan {interval s, loops}, z_plan {range, step} µm "
                      "and stage_positions [{x, y, z}] µm. One larger than the user's limits waits for their yes "
                      "(ask, then call again with confirms and asked_with).",
                      schema({"plan": {"type": "object", "description": "the MDASequence (JSON)"}, **YES}, ["plan"]),
                      scope_acquire, inline_ms=60000, family="scope"))

    async def stop_scope(ctx: ToolContext, args: dict) -> str:
        import httpx

        try:
            receipt = await client.stop()
        except httpx.HTTPError as exc:
            raise ToolFailed("the microscope's bridge did not answer", next="check that it is running") from exc
        if callable(journal):
            try:
                journal(receipt, ctx)
            except Exception:
                pass
        from ...scope import said

        return said(receipt)

    registry.add(Tool("stop_scope", "Stop the microscope's motors and any acquisition now (\"stop\", \"hold it\").",
                      schema({}), stop_scope, inline_ms=2000, family="scope"))

    async def scope_limits(ctx: ToolContext, args: dict) -> str:
        change = args.get("set")
        if not change:
            return await microscope(ctx, {})
        words = " ".join(str(args.get("asked_with") or "").split())
        if not words or words.lower() not in " ".join(str(ctx.question or "").split()).lower():
            raise ToolFailed("the limits change on the user's own words in this request, quoted in asked_with",
                             next="quote what the user said", argument=True)
        try:
            limits = await client.set_limits(change if isinstance(change, dict) else {}, words)
        except ValueError as exc:
            raise ToolFailed(str(exc), next="say what was wrong", argument=True) from None
        return "the limits are now: " + json.dumps({k: v for k, v in limits.items() if k != "notes"})

    registry.add(Tool("scope_limits", "Read, or on the user's own words change, the limits for the microscope's motors "
                      "and light: x, y, z boxes [lowest, highest] µm, step_xy and step_z (µm a move), exposure_ms "
                      "[lowest, highest], frames and seconds (above which an acquisition asks first).",
                      schema({"set": {"type": "object", "description": "the limits to change; empty: read them"},
                              "asked_with": {"type": "string", "description": "the user's words asking for it"}}),
                      scope_limits, inline_ms=3000, family="scope"))
