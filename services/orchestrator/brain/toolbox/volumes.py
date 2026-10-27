"""Volumes and their registration (registrations.py; phase 3's E5): "register this brain to the Allen atlas" on a volume
the user added runs brainreg as a job, asking only for what is missing (voxel sizes the file doesn't state, the
orientation, which channel or time point). A live 2D view is no volume: said, never registered."""
from __future__ import annotations

import asyncio

from ..registry import Registry, Tool, ToolContext, ToolFailed, schema


def add_volume_tools(registry: Registry, services) -> None:
    regs = getattr(services, "registrations", None)
    if regs is None:
        return

    async def register_volume(ctx: ToolContext, args: dict) -> str:
        from ...registrations import CantDo

        asked = getattr(ctx, "asked", None)
        session = getattr(asked, "session", None) or regs.session()
        thread = getattr(asked, "thread", None)
        if args.get("cancel"):
            def cancel_running():
                running = [r for r in regs.records("j", session) if r["state"] in ("queued", "running")]
                if not running:
                    raise CantDo("no registration is running")
                return regs.cancel(running[-1]["id"], session)

            try:
                record = await asyncio.to_thread(cancel_running)
            except CantDo as exc:
                raise ToolFailed(str(exc), next=None) from exc  # no next: the job may finish while cancellation waits
            return f"Stopping the registration of {record['volume']['name']}: what it wrote is kept, marked, never a result"
        volumes = sorted(regs.records("v", session), key=lambda r: r["at"])
        vid = str(args.get("volume") or "").strip() or (volumes[-1]["id"] if volumes else "")
        if not vid:
            raise ToolFailed("there is no volume in this session to register: the live view is a 2D picture, and brainreg "
                             "registers a whole brain imaged as a volume",
                             next="add the volume (a TIFF or OME-TIFF) on brainreg's card on the Models page")
        try:
            facts = {k: args[k] for k in ("voxel_um", "orientation", "series", "channel") if args.get(k) is not None}
            if args.get("time") is not None:
                facts["time_point"] = args["time"]
            if facts:
                regs.describe(vid, session=session, **facts)
            record = regs.get(vid, session)
            if record is None:
                raise ToolFailed(f"there is no volume {vid} in this session", next="name one of the session's volumes")
            missing = regs.missing(record)
            if missing:
                raise ToolFailed(f"before {record['name']} can be registered, it needs " + "; ".join(missing),
                                 next="ask the user for exactly these, then call register_volume again with them")
            job = await asyncio.to_thread(regs.start, vid, by="agent", thread=thread, session=session)
        except CantDo as exc:
            raise ToolFailed(str(exc), next=None) from exc  # no next: its words say what is missing or to install
        return (f"Started registering {record['name']} ({vid}) to the Allen mouse atlas at 25 µm with brainreg, as job "
                f"{job['id']} (usually several minutes); it runs in the background, its progress is in Activity, and its "
                "result, shown in brainreg's downsampled space, opens from Made in this session when done")

    registry.add(Tool(
        "register_volume", "Register a whole-brain volume the user added (v1...) to the Allen mouse brain atlas at 25 µm "
        "with brainreg, as a background job of several minutes (\"register this brain to the Allen atlas\"). Volumes "
        "are never in the live view or the notes: call it for any such request, whatever the view shows; it knows the "
        "session's volumes, takes the user's newest unless named, and refuses saying so when there is none. Give voxel_um, orientation, series, channel or time only when the user said "
        "them; what is still missing is refused with exactly what to ask (voxel sizes the file doesn't state, its "
        "orientation in BrainGlobe's three letters, never guessed). cancel stops the running one. The live view is a 2D "
        "picture and is never registered.",
        schema({"volume": {"type": "string", "description": "the volume's id (v1); default: the session's newest"},
                "voxel_um": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3,
                             "description": "voxel sizes in µm, z then y then x, as the user said them"},
                "orientation": {"type": "string", "description": "BrainGlobe's three letters for its axes as the user said "
                                "them (psl: towards posterior, superior, left)"},
                "series": {"type": "integer"}, "channel": {"type": "integer"}, "time": {"type": "integer"},
                "cancel": {"type": "boolean", "description": "stop the registration that is running"}}),
        register_volume, kind="effect", inline_ms=1500, family="volumes",
        uses=("brainreg", "niftyreg", "allen-mouse-atlas")))
