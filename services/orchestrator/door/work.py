"""The door's two general actions (ADR 0026), neither naming any capability:

- `open`: the agent's own file, added to Hooke's library through the camera bridge (as the studio adds one) and opened
  on the stage of a Project of the agent's own.
- `run`: any capability of the listing (listing.py), run the way the Actions panel runs one (`commands.action`), on the
  stage of the open Project, recorded as the agent's.

`open` changes the stage, so it runs only when no Hooke window shows the researcher's own work and Talk isn't live
(example.takeover). `run` acts only in a Project the calling agent made, named by id and open (`own_project`), never in
the researcher's own or in whatever happens to be open.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from .connections import Refused

MOST_BYTES = 4 * 1024 ** 3
PICTURES_AND_VIDEO = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".heic", ".mov", ".mp4", ".m4v",
                      ".avi", ".mkv"}
PREVIEW_ROWS = 5


# --- the Projects agents made ------------------------------------------------------------------------------------------

def agent_projects(app) -> dict:
    from .projects import access
    return access(app).rows()


def _remember_project(app, sid: str, actor: str) -> None:
    from .projects import access
    access(app).remember(sid, actor)


def owned(app, sid: str, actor: str) -> bool:
    from .projects import access
    return access(app).role(sid, actor) == 'owner'


def _talk_live(app) -> Refused | None:
    voice = getattr(getattr(app, "brain", None), "voice", None)
    if voice is not None and not getattr(voice, "closed", True):
        return Refused(409, "hooke-busy", "Talk is on in Hooke.", next="ask the researcher to stop Talk, then try again")
    return None


def takeover(app, actor: str | None = None) -> Refused | None:
    """For `open`: changing the stage must take nothing from the researcher (example.takeover's rule). Leaving a Project
    this agent made is leaving its own work, so that is fine too."""
    from .example import takeover as example_takeover
    return example_takeover(app, doing="opening your file", actor=actor)


def own_project(app, project, actor: str) -> Refused | None:
    """For `run`: it acts only in a Project this agent made (named by id, owned by its connection), and only while that
    Project is the one open. Never the researcher's, never whatever happens to be open."""
    if not isinstance(project, str) or not project:
        return Refused(400, "project-required", "Say which of your Projects to run it in.",
                       next="open a file with hooke open, then pass its Project with --project")
    if not owned(app, project, actor):
        return Refused(403, "not-your-project", "That isn't a Project you made, so nothing runs there.",
                       next="open a file with hooke open: it makes a Project of your own")
    if getattr(app.day, "sid", None) != project:
        return Refused(409, "project-not-open", "Your Project isn't the one open in Hooke now.",
                       next="open your file again with hooke open, then run it in the new Project")
    return _talk_live(app)


# --- open: the agent's file in a Project of its own ----------------------------------------------------------------------

def checked_file(raw) -> Path:
    if not isinstance(raw, str) or not raw:
        raise Refused(400, "bad-request", "open takes the file's full path.", next='send args {"file": "/full/path"}')
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise Refused(400, "bad-request", "The file's path must be a full path.", next="give the full path, from /")
    path = path.resolve()  # the file itself, never a link's name
    if path.suffix.lower() not in PICTURES_AND_VIDEO:
        raise Refused(400, "not-a-picture", f"{path.name} isn't a picture or video Hooke opens.",
                      next="open a picture (.png, .jpg, .tif…) or a video (.mov, .mp4…)")
    try:
        if not path.is_file():
            raise Refused(404, "no-such-file", f"There is no file at {path}.", next="check the path")
        size = path.stat().st_size
    except OSError as error:
        raise Refused(403, "unreadable-file", f"{path} can't be read: {error.strerror}", next="check its permissions") from None
    if size == 0 or size > MOST_BYTES:
        raise Refused(400, "bad-file", f"{path.name} is empty or larger than 4 GB.", next="choose another file")
    return path


# A refusal meaning the work was cut off by part of Hooke stopping, not that the work failed: its operation is said as
# interrupted (operations.py).
INTERRUPTED = "stopped-while-running"


async def _upload(app, path: Path) -> dict:
    """The file into the library's added footage, through the camera bridge's own upload (it owns that folder)."""
    import httpx
    from urllib.parse import urlencode, urlsplit

    bridge = app.hub.source.bridge_url.rstrip("/")
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(bridge))
    query = urlencode({"filename": path.name, "title": path.stem, "specimen": "", "microscope": "0"})

    try:  # opened first, so macOS refusing Hooke the folder is said as that, before anything is sent
        stream = await asyncio.to_thread(path.open, "rb")
    except PermissionError:
        raise Refused(403, "mac-privacy", f"macOS hasn't let Hooke read files in {path.parent} (System Settings → "
                      "Privacy & Security → Files and Folders).",
                      next="move it out of Desktop, Documents and Downloads, or allow Hooke there, then open it again") from None

    async def chunks():  # read off the event loop, a megabyte at a time
        try:
            while block := await asyncio.to_thread(stream.read, 1024 * 1024):
                yield block
        finally:
            await asyncio.to_thread(stream.close)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0)) as client:
            response = await client.post(f"{bridge}/assets/upload?{query}", content=chunks(),
                                         headers={"X-MicroscopeMind": "add-footage", "Origin": origin,
                                                  "Content-Type": "application/octet-stream",
                                                  "Content-Length": str(path.stat().st_size)})
        body = response.json()
    except httpx.ConnectError:
        await asyncio.to_thread(stream.close)
        raise Refused(503, "bridge-unreachable", "Hooke's camera bridge isn't answering, so the file wasn't added.",
                      next="check Hooke is open (hooke status --wait), then try again") from None
    except (httpx.HTTPError, ValueError) as error:  # it went away while taking the file: Hooke quit or restarted
        raise Refused(503, INTERRUPTED, f"Part of Hooke (its camera bridge) stopped while it took the file "
                      f"({type(error).__name__}), so the file wasn't added.",
                      next="check Hooke is open (hooke status --wait), then open the file again with a new request key") from None
    if response.status_code != 200 or not body.get("ok") or not isinstance(body.get("item"), dict):
        raise Refused(400, "not-added", f"Hooke couldn't add {path.name}: {str(body.get('error') or response.status_code)[:200]}",
                      next="choose a picture or video Hooke can open")
    return body["item"]


async def open_file(app, record: dict, progress, raw_path) -> dict:
    from .. import commands
    from .actor import ACTOR

    ACTOR.set(record["by"])
    path = checked_file(raw_path)
    busy = takeover(app, record["by"]) or _talk_live(app)
    if busy is not None:
        raise busy
    await progress(1, 3, f"Adding {path.name} to Hooke's library")
    item = await _upload(app, path)
    library = app.hub.library
    assets = await asyncio.to_thread(library.search, "", None, True)
    wanted = Path(str(item.get("name") or "")).name  # the bridge's item names it by its path in the footage folder
    asset = next((a for a in assets if wanted and Path(a.path).name == wanted), None)
    if asset is None:
        raise Refused(409, "not-found-after-adding", f"{path.name} was added, but Hooke's library doesn't list it yet.",
                      next="try open again in a moment")
    busy = takeover(app, record["by"]) or _talk_live(app)  # checked again: the researcher may have opened something
    if busy is not None:
        raise busy
    await progress(2, 3, "Opening it in a Project of its own")
    label = record.get("byLabel") or "An agent"
    made = app.new_session(f"{label}: {path.stem}"[:80], by="agent")
    sid = made["session"]
    _remember_project(app, sid, record["by"])
    await commands.source_retain(app, None, {'asset': asset.id, 'session': sid, 'requestId': record['id']})
    await commands.source(app, None, {"open": asset.id, "session": sid})
    for _ in range(100):
        staged = getattr(app.hub, "stage_meta", None) or {}
        if app.day.on_stage == asset.id and staged.get("id") == asset.id:
            break
        await asyncio.sleep(0.1)
    await progress(3, 3, "Opened")
    landed = app.day.on_stage == asset.id
    return {"says": f"Opened {path.name} in its own Project." if landed else
            f"{path.name} is in its own Project, but it hasn't reached the stage yet.",
            "project": sid, "asset": asset.id, "onStage": landed}


# --- run: any listed capability, on the open Project's stage --------------------------------------------------------

def check_models(args, places: dict[str, str]) -> None:
    """No argument may name a model or way of running that uses a cloud service: what runs is decided by what is sent,
    not only by the capability's preset. (The run itself is also held to local work: api_usage.local_only.)"""
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from strings(item)

    cloud = sorted({v for v in strings(args) if places.get(v) == "cloud"})
    if cloud:
        raise Refused(409, "cloud-argument", f"{', '.join(cloud)} uses a cloud service with the researcher's own keys.",
                      next="choose models that run on this Mac; the researcher can run cloud ones in Hooke")


def check_args(schema: dict, args: dict, project: str | None = None) -> None:
    """Only the arguments its schema offers (which leaves out what only the researcher may set), with its required ones,
    and none that points outside the agent's own Project (agent_runnable)."""
    from .. import agent_runnable
    for key in agent_runnable.NEVER_ARGUMENTS:
        if key in args:
            raise Refused(400, "bad-arguments", f"An agent's run can't take {key}: it stays in its own Project.",
                          next=f"leave {key} out")
    for key in agent_runnable.OWN_SESSION_ARGUMENTS:
        if key in args and args[key] != project:
            raise Refused(403, "not-your-project", "That argument names a Project that isn't yours.",
                          next=f"leave {key} out, or give your own Project")
    allowed = set((schema or {}).get("properties") or {})
    extra = sorted(set(args) - allowed)
    if extra:
        raise Refused(400, "bad-arguments", f"It takes no argument {', '.join(extra)}.",
                      next="send only the arguments its inputSchema lists")
    missing = [k for k in (schema or {}).get("required") or () if k not in args]
    if missing:
        raise Refused(400, "bad-arguments", f"It needs {', '.join(missing)}.", next="send the arguments its inputSchema requires")


async def run_capability(app, record: dict, progress, tool: str, preset: dict, args: dict) -> dict:
    """A listed capability, run by its tool as the agent (never as the researcher's own hand), in the agent's Project."""
    from .. import commands
    from ..tables import TableRefused, made_words_async, read_table
    from .actor import ACTOR

    ACTOR.set(record["by"])
    refused = own_project(app, record["project"], record["by"])
    if refused is not None:
        raise refused
    if app.day.on_stage != record.get("inputRevision"):
        raise Refused(409, "input-changed", "What this was asked to run on is no longer on your Project's stage.",
                      next="open the input again, then ask with a new request key")
    await progress(1, 2, f"Running {record.get('capabilityName') or 'it'}")
    # A catalog row names its entry in the preset, and the agent's arguments are its parameters; otherwise (any other
    # tool) the arguments are the tool's own.
    tool_args = {**preset, "params": args} if tool == "run_capability" and "capability" in preset else {**preset, **args}
    from research_worker import api_usage
    with api_usage.local_only(True):  # local work only: no paid provider, no cloud model, whatever the arguments say
        done = await commands.run_tool(app, tool, tool_args)
    if not done.get("ok"):
        raise Refused(409, "failed", str(done.get("summary") or done.get("error") or "It didn't work."),
                      next="check Hooke's Activity")
    outcome = {"says": str(done.get("summary") or "Done."), "project": record["project"],
               "executions": list(done.get("executions") or [])}
    plain = await made_words_async(app.hub, outcome["executions"])
    if plain:
        said, table, identity = plain
        outcome["says"] = said
        if isinstance(table, str) and identity.get("session") and identity.get("revision") is not None:
            await progress(2, 2, "Reading what it measured")
            outcome["result"] = {"id": table, "revision": identity["revision"]}
            outcome["page"] = f"#measurements/{table}?session={identity['session']}&revision={identity['revision']}"
            try:
                read = await read_table(app, table, identity["session"], identity["revision"])
                rows = read.get("rows") or []
                outcome.update({"count": read.get("count", len(rows)), "columns": read.get("columns") or [],
                                "preview": rows[:PREVIEW_ROWS], "truncated": len(rows) > PREVIEW_ROWS})
            except TableRefused as error:
                outcome["note"] = f"Its table couldn't be read back: {error}"
    return outcome
