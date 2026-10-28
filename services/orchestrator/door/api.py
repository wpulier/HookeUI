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

PREFIX = "/api/agent/v1"
INTERFACE = 1  # this door's version; the CLI and skill say which they need
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
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
                      "connect through the hooke command on this Mac")
    if request.headers.get("origin") is not None:
        raise Refused(403, "browser-refused", "Web pages cannot use Hooke's agent door.",
                      "use the hooke command, not a web page")
    asked = request.headers.get("hooke-contract")
    if asked is not None and asked != CONTRACT:
        raise Refused(400, "contract-version", f"This copy of Hooke speaks contract {CONTRACT}, not {asked}.",
                      "update the hooke command or Hooke so they match")


def app_version() -> str | None:
    from ..build_info import PACKAGED
    try:
        return json.loads(PACKAGED.read_text()).get("version")
    except (OSError, ValueError, AttributeError):
        return None


def listing(environ=os.environ) -> list[dict]:
    """Every catalog capability an agent could use here, with its state: what runs now, then what could after setup.
    It reads files and hashes entry folders: call it in a thread."""
    from gpu_worker import manifests as catalog
    from .. import kinds, readiness

    out, seen = [], set()
    with kinds.read_once(), readiness.batch():
        rows, manifests = kinds.available_with_manifests(environ)
        for row in rows:
            m = manifests.get(row.get("key"))
            entry = kinds.door_entry(m) if m is not None else None
            if m is None or entry is None or m.id in seen:
                continue
            seen.add(m.id)
            if row.get("runnable", True) and not row.get("why"):
                out.append(describe(m, entry, state="available"))
            else:
                out.append(describe(m, entry, state="unavailable", why=str(row.get("why") or "it cannot run here now")))
        for m in sorted(catalog.manifests().values(), key=lambda m: (m.name.lower(), m.id)):
            if m.id in seen or m.namespace:
                continue  # listed above, or a community or personal entry: those are added through the Toolbox
            entry = kinds.door_entry(m)
            if entry is None:
                continue
            seen.add(m.id)
            try:
                r = readiness.model(m.id, environ=environ)
            except Exception as error:  # one entry's facts can't be read: say so for it, list the rest
                out.append(describe(m, entry, state="unavailable", why=f"its readiness could not be read: {error}"))
                continue
            state = STATES.get(r.status, "unavailable")
            if state == "available":
                state = "setup"  # ready to turn on, but not on yet: it isn't in what runs now
            step = r.next
            out.append(describe(m, entry, state=state, why=r.why or r.word,
                                next=(step.says if step is not None else "turn it on in Hooke's Toolbox")))
    return out


def register(app, built, studio_origin, header: str) -> Connections:
    connections = Connections(built.paths.home)
    built.agent_connections = connections

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
            raise Refused(403, "not-local", "Only this Mac's Hooke window may do that.", "use Hooke on this Mac")
        if writes and (not studio_origin(request.headers.get("origin"), request.url.port)
                       or request.headers.get(header) != "agent-connections"):
            raise Refused(403, "studio-only", "Only the Hooke window may answer or end a connection.",
                          "use Hooke on this Mac")

    async def body(request: Request) -> dict:
        try:
            value = await request.json()
        except ValueError:
            value = None
        if not isinstance(value, dict):
            raise Refused(400, "bad-request", "The request needs a JSON object.", "update the hooke command")
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
        from ..build_info import identity
        build = await asyncio.to_thread(identity)
        day = getattr(built, "day", None)
        hub = getattr(built, "hub", None)
        return _no_store({
            "ok": True, "contract": CONTRACT,
            "app": {"name": "HookeUI", "version": await asyncio.to_thread(app_version),
                    "channel": os.environ.get("HOOKE_CHANNEL") or "development",
                    "revision": build.get("revision"), "sourceDigest": build.get("sourceDigest")},
            "interface": {"version": INTERFACE, "minCli": 1, "minSkill": 1},
            "connection": c.wire(),
            "studio": {"open": bool(getattr(hub, "clients", None))},
            "project": {"session": getattr(day, "sid", None)},
        })

    @app.get(f"{PREFIX}/tools")
    async def tools(request: Request, q: str = "", page: int = 1, perPage: int = PAGE_SIZE) -> JSONResponse:
        try:
            connected(request)
        except Refused as error:
            return _refusal(error)
        try:
            found = await asyncio.to_thread(listing)
        except Exception as error:  # the catalog couldn't be read: say so, never an empty list
            return _refusal(Refused(503, "catalog-unreadable", f"Hooke's catalog could not be read: {error}",
                                    "restart Hooke; if it persists, report a problem from Hooke"))
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
        try:
            studio(request, writes=True)
            value = await body(request)
            if value.get("answer") not in ("connect", "decline"):
                raise Refused(400, "bad-request", "The answer is connect or decline.", "reload the workspace")
            p = connections.answer(pid, value["answer"] == "connect")
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
