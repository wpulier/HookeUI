"""The door's two general actions (ADR 0026), neither naming any capability:

- `open`: the agent's own file, added to Hooke's library through the camera bridge (as the studio adds one) and opened
  on the stage of a Project of the agent's own.
- `run`: any capability of the listing (listing.py), run the way the Actions panel runs one (`commands.action`), on the
  stage of the open Project, recorded as the agent's.

Both change the stage, so both take the takeover guard: they run only when no Hooke window shows the researcher's own
work and Talk isn't live, or when the open Project is one an agent made (`agent_projects`).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from .connections import Refused

MOST_BYTES = 4 * 1024 ** 3
PREVIEW_ROWS = 5


# --- the Projects agents made ------------------------------------------------------------------------------------------

def _projects_file(app) -> Path:
    return Path(app.paths.home) / "agents" / "projects.json"


def agent_projects(app) -> dict:
    """{session id: {"by": actor, "at"}}: Projects an agent made through the door (its own place to work)."""
    try:
        value = json.loads(_projects_file(app).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember_project(app, sid: str, actor: str) -> None:
    import time
    path = _projects_file(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    known = agent_projects(app)
    known[sid] = {"by": actor, "at": time.time()}
    temporary = path.with_name(f".projects.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(known, indent=1))
    temporary.replace(path)


def takeover(app) -> Refused | None:
    """None when changing the stage takes nothing from the researcher: the example's rule (example.takeover), and also
    any Project an agent made."""
    from .example import takeover as example_takeover
    if getattr(app.day, "sid", None) in agent_projects(app):
        voice = getattr(getattr(app, "brain", None), "voice", None)
        if voice is not None and not getattr(voice, "closed", True):
            return Refused(409, "hooke-busy", "Talk is on in Hooke.", next="ask the researcher to stop Talk, then try again")
        return None
    return example_takeover(app)


# --- open: the agent's file in a Project of its own ----------------------------------------------------------------------

def checked_file(raw) -> Path:
    if not isinstance(raw, str) or not raw:
        raise Refused(400, "bad-request", "open takes the file's full path.", next='send args {"file": "/full/path"}')
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise Refused(400, "bad-request", "The file's path must be a full path.", next="give the full path, from /")
    try:
        if not path.is_file():
            raise Refused(404, "no-such-file", f"There is no file at {path}.", next="check the path")
        size = path.stat().st_size
    except OSError as error:
        raise Refused(403, "unreadable-file", f"{path} can't be read: {error.strerror}", next="check its permissions") from None
    if size == 0 or size > MOST_BYTES:
        raise Refused(400, "bad-file", f"{path.name} is empty or larger than 4 GB.", next="choose another file")
    return path


async def _upload(app, path: Path) -> dict:
    """The file into the library's added footage, through the camera bridge's own upload (it owns that folder)."""
    import httpx
    from urllib.parse import urlencode, urlsplit

    bridge = app.hub.source.bridge_url.rstrip("/")
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(bridge))
    query = urlencode({"filename": path.name, "title": path.stem, "specimen": "", "microscope": "0"})

    async def chunks():  # read off the event loop, a megabyte at a time
        stream = await asyncio.to_thread(path.open, "rb")
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
    except (httpx.HTTPError, ValueError) as error:
        raise Refused(503, "bridge-unreachable", f"Hooke's camera bridge couldn't take the file ({type(error).__name__}).",
                      next="check Hooke is running, then try again") from None
    if response.status_code != 200 or not body.get("ok") or not isinstance(body.get("item"), dict):
        raise Refused(400, "not-added", f"Hooke couldn't add {path.name}: {str(body.get('error') or response.status_code)[:200]}",
                      next="choose a picture or video Hooke can open")
    return body["item"]


async def open_file(app, record: dict, progress, raw_path) -> dict:
    from .. import commands
    from .actor import ACTOR

    ACTOR.set(record["by"])
    path = checked_file(raw_path)
    busy = takeover(app)
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
    busy = takeover(app)  # checked again: the researcher may have opened something meanwhile
    if busy is not None:
        raise busy
    await progress(2, 3, "Opening it in a Project of its own")
    label = record.get("byLabel") or "An agent"
    made = app.new_session(f"{label}: {path.stem}"[:80], by="agent")
    sid = made["session"]
    _remember_project(app, sid, record["by"])
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

async def run_capability(app, record: dict, progress, capability: str, args: dict) -> dict:
    from .. import commands
    from ..brain.registry import ToolFailed
    from ..tables import TableRefused, read_table
    from .actor import ACTOR

    ACTOR.set(record["by"])
    if app.day.sid != record["project"] or app.day.on_stage != record.get("inputRevision"):
        raise Refused(409, "input-changed", "What this was asked to run on is no longer open in Hooke.",
                      next="open the input again, then ask with a new request key")
    busy = takeover(app)
    if busy is not None:
        raise busy
    await progress(1, 2, f"Running {record.get('name') or capability}")
    try:
        done = await commands.action(app, None, {"capability": capability, "args": args})
    except ToolFailed as error:
        raise Refused(409, "failed", error.summary if hasattr(error, "summary") else str(error),
                      next=getattr(error, "next", None) or "check Hooke's Activity") from None
    except Exception as error:  # the tools' own refusals (tools.ToolError and others), said in their words
        raise Refused(409, "failed", str(error) or type(error).__name__,
                      next=getattr(error, "next", None) or "check Hooke's Activity") from None
    outcome = {"says": str(done.get("said") or done.get("summary") or "Done."), "project": record["project"],
               "executions": list(done.get("executions") or [])}
    table, identity = done.get("table"), done.get("tableIdentity") or {}
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
