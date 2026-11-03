"""The studio's routes for the Agents panel (ADR 0029): start, read, reply, interrupt, answer and stop.

Only the Hooke window may use them: a loopback Host, the studio's Origin and header, and in the Mac app the window's
own studio token (which a page or another program can't copy). No route is part of the agent door: an outside agent
can never start, steer, answer for or stop another agent.
"""
from __future__ import annotations

import os
import secrets
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

from ..door.api import _host, LOOPBACK_HOSTS
from .conductor import Refused

HEADER_VALUE = "agents"


def _json(value, status: int = 200) -> JSONResponse:
    return JSONResponse(value, status_code=status, headers={"Cache-Control": "no-store"})


def _refused(error: Refused) -> JSONResponse:
    return _json({"ok": False, "says": error.says, "next": error.next}, error.status)


def register(app, built, studio_origin, header: str) -> None:
    def window(request: Request, writes: bool) -> None:
        if _host(request) not in LOOPBACK_HOSTS or request.headers.get("authorization"):
            raise Refused("Only this Mac's Hooke window may do that.", "use Hooke on this Mac", 403)
        expected = os.environ.get("HOOKE_STUDIO_TOKEN")
        if expected:  # the Mac app: only its own window holds this
            if not secrets.compare_digest(request.headers.get("x-hooke-studio") or "", expected):
                raise Refused("Only the Hooke window may do that.", "use Hooke on this Mac", 403)
        if writes:
            if not studio_origin(request.headers.get("origin"), request.url.port) or \
                    request.headers.get(header) != HEADER_VALUE:
                raise Refused("Only the Hooke window may do that.", "use Hooke on this Mac", 403)
        elif not expected:
            source = request.headers.get("origin") or request.headers.get("referer") or ""
            parsed = urlsplit(source)
            if source and not studio_origin(f"{parsed.scheme}://{parsed.netloc}", request.url.port):
                raise Refused("Only the Hooke window may do that.", "use Hooke on this Mac", 403)
        conductor = getattr(built, "conductor", None)
        if conductor is None:
            raise Refused("Hooke can't start outside agents in this copy.", "", 503)
        if request.url.port:
            conductor.side.door = f"http://127.0.0.1:{request.url.port}"  # what a development `hooke` is pointed at

    async def body(request: Request) -> dict:
        try:
            value = await request.json()
        except ValueError:
            value = None
        if not isinstance(value, dict):
            raise Refused("Send a JSON object.", "", 400)
        return value

    @app.get("/api/agents")
    async def agents_list(request: Request):
        try:
            window(request, writes=False)
            conductor = built.conductor
            fresh = request.query_params.get("programs") == "fresh"
            return _json({"agents": conductor.wire(), "programs": await conductor.programs(fresh=fresh)})
        except Refused as error:
            return _refused(error)

    @app.get("/api/agents/{lid}")
    async def agent_read(lid: str, request: Request):
        try:
            window(request, writes=False)
            return _json({"agent": built.conductor.get(lid).wire(full=True)})
        except Refused as error:
            return _refused(error)

    @app.post("/api/agents")
    async def agent_start(request: Request):
        try:
            window(request, writes=True)
            value = await body(request)
            budget = value.get("budgetUsd")
            launch = await built.conductor.start(str(value.get("program") or ""), str(value.get("task") or ""),
                                                 project=value.get("project") or None, by="studio",
                                                 budget_usd=float(budget) if isinstance(budget, (int, float)) else None)
            return _json({"agent": launch.wire()}, 202)
        except Refused as error:
            return _refused(error)

    @app.post("/api/agents/{lid}/note")
    async def agent_note(lid: str, request: Request):
        try:
            window(request, writes=True)
            value = await body(request)
            how = await built.conductor.note(lid, str(value.get("text") or ""), by="studio")
            return _json({"sent": how})
        except Refused as error:
            return _refused(error)

    @app.post("/api/agents/{lid}/interrupt")
    async def agent_interrupt(lid: str, request: Request):
        try:
            window(request, writes=True)
            return _json({"interrupted": await built.conductor.interrupt(lid)})
        except Refused as error:
            return _refused(error)

    @app.post("/api/agents/{lid}/answer")
    async def agent_answer(lid: str, request: Request):
        """The user's own answer to an agent's ask: the only place one is ever answered."""
        try:
            window(request, writes=True)
            value = await body(request)
            answer = value.get("answer")
            if answer not in ("allow", "deny") or not isinstance(value.get("permission"), str):
                raise Refused("Answer allow or deny, for one ask.", "", 400)
            await built.conductor.answer(lid, value["permission"], answer == "allow",
                                         always=value.get("always") is True)
            return _json({"answered": answer})
        except Refused as error:
            return _refused(error)

    @app.post("/api/agents/{lid}/stop")
    async def agent_stop(lid: str, request: Request):
        try:
            window(request, writes=True)
            return _json({"agent": (await built.conductor.stop(lid)).wire()})
        except Refused as error:
            return _refused(error)
