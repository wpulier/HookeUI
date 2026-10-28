"""The door for outside agents: /api/agent/v1/ (ADR 0026; services/orchestrator/door/AGENTS.md).

Every door request is checked the same way before anything else: a loopback Host (DNS rebinding), no Origin (browsers
send one; the door is not for pages) and, if given, the one contract version. Asking for a pending connection and
claiming it with its secret are the only routes without a token. The studio's own routes (answering a request, the
list, Disconnect) are under /api/agent-connections and need the studio's Origin and header, as other studio writes do.
"""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import Request
from fastapi.responses import JSONResponse

from .connections import Connections, Refused
from .contract import CONTRACT, describe
from . import native
from .operations import Operations

PREFIX = "/api/agent/v1"
INTERFACE = 1  # this door's version; the CLI and skill say which they need
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
PAGE = __import__("re").compile(r"^#(measurements|models|toolbox|assets|sessions)(/[A-Za-z0-9_.-]+)?(\?[A-Za-z0-9=&._%-]*)?$")
PAGE_SIZE = 20
STATES = {"ready": "available", "needs-setup": "setup", "unavailable": "unavailable", "broken": "broken"}


def _no_store(value: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(value, status_code=status, headers={"Cache-Control": "no-store", "Hooke-Contract": CONTRACT})


def _refusal(error: Refused) -> JSONResponse:
    return _no_store(error.wire(), error.status)


def _host(request: Request) -> str:
    host = request.headers.get("host") or ""
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def guard(request: Request) -> None:
    """What every door request must be, before its token is even read."""
    if _host(request) not in LOOPBACK_HOSTS:
        raise Refused(403, "not-local", "Hooke's agent door answers only on this Mac's loopback address.",
                      next="connect through the hooke command on this Mac")
    if request.headers.get("origin") is not None:
        raise Refused(403, "browser-refused", "Web pages cannot use Hooke's agent door.",
                      next="use the hooke command, not a web page")
    asked = request.headers.get("hooke-contract")
    if asked is not None and asked != CONTRACT:
        raise Refused(400, "contract-version", f"This copy of Hooke speaks contract {CONTRACT}, not {asked}.",
                      next="update the hooke command or Hooke so they match")


@__import__("functools").lru_cache(maxsize=1)
def build_identity() -> dict:
    """This process's source identity, read once: it is fixed at startup (build_info.identity), and a source checkout
    hashes every file to say it."""
    from ..build_info import identity
    return identity()


def app_version() -> str | None:
    from ..build_info import PACKAGED
    try:
        return json.loads(PACKAGED.read_text()).get("version")
    except (OSError, ValueError, AttributeError):
        return None


def listing(built) -> list[dict]:
    """Everything this Hooke can run for an agent (listing.py): call it on the event loop."""
    from .listing import everything
    return everything(built, build_identity())


DOOR_ACTIONS = ("example", "open")  # the door's own actions; every other `tool` is a capability of the listing


def example_identity() -> tuple[str | None, str | None]:
    """The example's input revision (its image's SHA-256) and the generation of the capability it runs."""
    import hashlib
    from gpu_worker import manifests as catalog
    from .. import examples
    from ..capability_identity import generation_of
    try:
        digest = hashlib.sha256(examples.human_mitosis(preview=True).read_bytes()).hexdigest()  # what the guide analyses
    except (OSError, ValueError, KeyError):
        digest = None  # damaged or missing: the guide itself refuses and says so
    m = catalog.get("basic-bright-objects")
    return digest, (generation_of(m) if m is not None else None)


def register(app, built, studio_origin, header: str) -> Connections:
    connections = Connections(built.paths.home)
    built.agent_connections = connections
    operations = Operations(built.paths.home)
    built.agent_operations = operations

    def announce() -> None:
        """Tell open studios what is waiting and who is connected (the confirm sheet and the list draw from it)."""
        hub = getattr(built, "hub", None)
        clients = getattr(hub, "clients", None) or ()
        if not clients:
            return
        message = json.dumps({"type": "agent-connections", "pending": [p.wire() for p in connections.pending()],
                              "connections": [c.wire() for c in connections.connections()]})
        for client in list(clients):
            client.offer(message)

    def studio(request: Request, writes: bool) -> None:
        if _host(request) not in LOOPBACK_HOSTS:
            raise Refused(403, "not-local", "Only this Mac's Hooke window may do that.", next="use Hooke on this Mac")
        if writes and (not studio_origin(request.headers.get("origin"), request.url.port)
                       or request.headers.get(header) != "agent-connections"):
            raise Refused(403, "studio-only", "Only the Hooke window may answer or end a connection.",
                          next="use Hooke on this Mac")

    async def body(request: Request) -> dict:
        try:
            value = await request.json()
        except ValueError:
            value = None
        if not isinstance(value, dict):
            raise Refused(400, "bad-request", "The request needs a JSON object.", next="update the hooke command")
        return value

    # --- connecting ---------------------------------------------------------------------------------------------------
    @app.post(f"{PREFIX}/connect")
    async def connect(request: Request) -> JSONResponse:
        try:
            guard(request)
            value = await body(request)
            p = connections.request(value.get("label"), value.get("kind"), value.get("secretSha256"))
        except Refused as error:
            return _refusal(error)
        announce()
        return _no_store({"ok": True, "pending": p.wire(),
                          "next": f"confirm {p.label} in the Hooke window; its code is {p.code}"}, 202)

    @app.post(PREFIX + "/connect/{pid}/claim")
    async def claim(pid: str, request: Request) -> JSONResponse:
        try:
            guard(request)
            value = await body(request)
            p, c, token = await asyncio.to_thread(connections.claim, pid, value.get("secret"))
        except Refused as error:
            if error.code in ("declined",):
                announce()
            return _refusal(error)
        if c is None:
            return _no_store({"ok": True, "pending": p.wire(), "next": "waiting for you to confirm it in Hooke"}, 202)
        announce()
        return _no_store({"ok": True, "connection": c.wire(), "token": token})

    # --- what a connected agent reads ---------------------------------------------------------------------------------
    def connected(request: Request):
        guard(request)
        return connections.authenticate(request.headers.get("authorization"))

    @app.get(f"{PREFIX}/status")
    async def status(request: Request) -> JSONResponse:
        try:
            c = connected(request)
        except Refused as error:
            return _refusal(error)
        build = await asyncio.to_thread(build_identity)
        day = getattr(built, "day", None)
        hub = getattr(built, "hub", None)
        return _no_store({
            "ok": True, "contract": CONTRACT,
            "app": {"name": "HookeUI", "version": await asyncio.to_thread(app_version),
                    "channel": os.environ.get("HOOKE_CHANNEL") or "development",
                    "revision": build.get("revision"), "sourceDigest": build.get("sourceDigest")},
            "interface": {"version": INTERFACE, "minCli": 1, "minSkill": 1},
            "connection": c.wire(),
            "studio": {"open": any(getattr(c, "kind", None) == "studio" for c in (getattr(hub, "clients", None) or ()))},
            "project": {"session": getattr(day, "sid", None)},
        })

    @app.get(f"{PREFIX}/tools")
    async def tools(request: Request, q: str = "", page: int = 1, perPage: int = PAGE_SIZE) -> JSONResponse:
        try:
            connected(request)
        except Refused as error:
            return _refusal(error)
        try:
            found = listing(built)
        except Exception as error:  # the list couldn't be read: say so, never an empty list
            return _refusal(Refused(503, "catalog-unreadable", f"What Hooke can do could not be read: {error}",
                                    next="restart Hooke; if it persists, report a problem from Hooke"))
        terms = [t for t in q.lower().split() if t]
        if terms:
            found = [t for t in found if all(term in " ".join((t["id"], t["name"], t["task"], t.get("description", ""),
                                                                 *(o["type"] for o in t["outputs"]))).lower()
                                             for term in terms)]
        per = min(max(1, perPage), 100)
        pages = max(1, -(-len(found) // per))
        page = min(max(1, page), pages)
        return _no_store({"ok": True, "contract": CONTRACT, "tools": found[(page - 1) * per: page * per],
                          "total": len(found), "page": page, "pages": pages, "perPage": per, "query": q})

    # --- operations: work an agent starts ---------------------------------------------------------------------------
    @app.post(f"{PREFIX}/operations")
    async def start(request: Request) -> JSONResponse:
        """Work an agent starts: the guided example, opening its own file, or running any capability of the listing.
        Each is admitted to disk before it starts (operations.py) and never takes over what the researcher sees."""
        from . import example, work
        try:
            c = connected(request)
            value = await body(request)
            tool, args = value.get("tool"), value.get("args") or {}
            if not isinstance(tool, str) or not tool:
                raise Refused(400, "bad-request", "Say which capability to run.", next="list them with GET tools")
            if not isinstance(args, dict):
                raise Refused(400, "bad-request", "The arguments are one JSON object.", next="send args as an object")
            key = value.get("requestKey")
            if tool == "example":
                if args:
                    raise Refused(400, "bad-request", "The example takes no arguments.", next="send args as {}")
                busy = example.takeover(built)  # never under the researcher's eyes (review B3)
                if busy is not None:
                    raise busy
                revision, generation = await asyncio.to_thread(example_identity)
                record, made = await operations.admit(
                    by=c.actor, label=c.label, tool=tool, project="example", input_revision=revision,
                    generation=generation, params={}, request_key=key,
                    work=lambda record, progress: example.run(built, record, progress))
            elif tool == "open":
                path = work.checked_file(args.get("file"))
                if set(args) - {"file"}:
                    raise Refused(400, "bad-request", "open takes only the file's path.", next='send args {"file": "/full/path"}')
                busy = work.takeover(built)
                if busy is not None:
                    raise busy
                stat = path.stat()
                record, made = await operations.admit(
                    by=c.actor, label=c.label, tool=tool, project="new",
                    input_revision=f"{path}:{stat.st_size}:{int(stat.st_mtime)}", generation=None,
                    params={"file": str(path)}, request_key=key,
                    work=lambda record, progress: work.open_file(built, record, progress, str(path)))
            else:
                listed = {t["id"]: t for t in listing(built)}
                found = listed.get(tool)
                if found is None:
                    raise Refused(404, "unknown-capability", f"This Hooke has no capability {tool!r} an agent can run.",
                                  next="list what it has with GET tools")
                if found["state"] != "available":
                    raise Refused(409, "not-ready", f"{found['name']} can't run here now: {found.get('why') or found['state']}.",
                                  next=found.get("next") or "set it up in Hooke's Toolbox, then try again")
                busy = work.takeover(built)
                if busy is not None:
                    raise busy
                record, made = await operations.admit(
                    by=c.actor, label=c.label, tool=tool, project=built.day.sid,
                    input_revision=getattr(built.day, "on_stage", None), generation=found["generation"],
                    params=args, request_key=key,
                    work=lambda record, progress: work.run_capability(built, record, progress, tool, args))
        except Refused as error:
            return _refusal(error)
        return _no_store({"ok": True, "operation": record, "made": made}, 202 if made else 200)

    @app.get(f"{PREFIX}/operations")
    async def list_operations(request: Request, limit: int = 20) -> JSONResponse:
        try:
            connected(request)
        except Refused as error:
            return _refusal(error)
        return _no_store({"ok": True, **operations.operations(limit)})

    @app.get(PREFIX + "/operations/{op_id}")
    async def get_operation(op_id: str, request: Request) -> JSONResponse:
        try:
            connected(request)
            record = operations.get(op_id)
        except Refused as error:
            return _refusal(error)
        return _no_store({"ok": True, "operation": record})

    @app.post(PREFIX + "/operations/{op_id}/cancel")
    async def cancel_operation(op_id: str, request: Request) -> JSONResponse:
        try:
            connected(request)
            record = await operations.cancel(op_id)
        except Refused as error:
            return _refusal(error)
        return _no_store({"ok": True, "operation": record})

    @app.post(f"{PREFIX}/open")
    async def open_in_hooke(request: Request) -> JSONResponse:
        """Show a page in the Hooke window (an exact result's measurements). Done only when a window says it shows it."""
        try:
            connected(request)
            value = await body(request)
            page = value.get("page")
            if not isinstance(page, str) or not PAGE.match(page):
                raise Refused(400, "bad-page", "That is not a page Hooke can open for an agent.",
                              next="use the page an operation gave, such as #measurements/<id>?session=…&revision=…")
            opener = getattr(built, "open_page", None)
            reply = await opener(None, page) if opener is not None else None
        except Refused as error:
            return _refusal(error)
        if reply is None:
            return _no_store({"ok": True, "shown": False, "says": "No Hooke window answered, so it isn't shown yet.",
                              "next": "open Hooke (hooke open), then ask again"})
        visible = reply.get("visible") is not False
        return _no_store({"ok": True, "shown": visible,
                          "says": "Hooke shows it." if visible else "Hooke opened it, but its window is in the background.",
                          **({} if visible else {"next": "bring the Hooke window forward"})})

    # --- the studio's side --------------------------------------------------------------------------------------------
    @app.get("/api/agent-connections")
    async def studio_list(request: Request) -> JSONResponse:
        try:
            studio(request, writes=False)
        except Refused as error:
            return _refusal(error)
        return _no_store({"ok": True, "pending": [p.wire() for p in connections.pending()],
                          "connections": [c.wire() for c in connections.connections()]})

    @app.post("/api/agent-connections/pending/{pid}")
    async def studio_answer(pid: str, request: Request) -> JSONResponse:
        """The researcher's answer. It counts only when the Hooke app's native dialog signed it (door/native.py): an
        Origin and header any local program can copy are not enough."""
        try:
            studio(request, writes=False)  # a loopback Host; the signature below is what makes it the researcher's
            value = await body(request)
            answer = value.get("answer")
            if answer not in ("connect", "decline"):
                raise Refused(400, "bad-request", "The answer is connect or decline.", next="reload the workspace")
            waiting = connections.waiting(pid)
            native.check(pid, waiting.code, answer, value.get("signature"),
                         studio_ok=studio_origin(request.headers.get("origin"), request.url.port)
                         and request.headers.get(header) == "agent-connections")
            p = connections.answer(pid, answer == "connect")
        except Refused as error:
            return _refusal(error)
        announce()
        return _no_store({"ok": True, "pending": p.wire()})

    @app.post("/api/agent-connections/{cid}/disconnect")
    async def studio_disconnect(cid: str, request: Request) -> JSONResponse:
        try:
            studio(request, writes=True)
            c = await asyncio.to_thread(connections.disconnect, cid)
        except Refused as error:
            return _refusal(error)
        announce()
        return _no_store({"ok": True, "connection": c.wire()})

    return connections
