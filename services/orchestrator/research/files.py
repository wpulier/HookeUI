"""Serving researchers' files and sessions to the studio.

GET /research/<day>/<rid>/<inputs|outputs|sources>/<file>   read-only, image/video/html only
GET /api/research                                            all sessions (newest last)
GET /api/research/<rid>                                      one session with its steps

Files are whatever a researcher made or fetched, so they are served defensively: fixed
directory layout, no traversal, an allow-list of types, `nosniff`, and HTML (lessons) under
a sandbox CSP with no network, so it cannot reach the app's WebSockets.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response

from .manager import ResearchManager

TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
         ".mp4": "video/mp4", ".html": "text/html; charset=utf-8", ".json": "application/json"}
SUBDIRS = {"inputs", "outputs", "sources"}
# Lessons carry their pictures inline (data: URLs, research_worker/researcher.py), so a lesson
# loads nothing at all from anywhere, and can connect to nothing.
LESSON_CSP = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
              "img-src data:")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STEPS_SHOWN = 200  # of a researcher's steps on its detail read (all are kept in steps.jsonl)
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


ARTIFACT_KEYBOARD_SCRIPT = """<script data-micromind-keyboard>
(() => {
  let nonce = null;
  addEventListener('message', event => {
    if (event.source === parent && event.data && event.data.type === 'micromind-artifact-keyboard-init'
        && typeof event.data.nonce === 'string' && /^[a-f0-9-]{36}$/.test(event.data.nonce)) nonce = event.data.nonce;
  });
  addEventListener('keydown', event => {
    if (event.isTrusted && event.key === 'Escape' && !event.repeat && nonce && parent !== window) {
      event.preventDefault(); event.stopPropagation();
      parent.postMessage({type:'micromind-artifact-escape',nonce}, '*');
    }
  }, true);
})();
</script>"""


def artifact_document(raw: bytes) -> bytes:
    """Response-only keyboard bridge; never rewrites the retained artifact or grants capabilities."""
    html = raw.decode('utf-8')
    opening = re.search(r'<head\b[^>]*>',html,re.I) or re.search(r'<!doctype[^>]*>',html,re.I)
    at = opening.end() if opening else 0
    return (html[:at] + ARTIFACT_KEYBOARD_SCRIPT + html[at:]).encode('utf-8')


def research_file(root: Path, day: str, rid: str, sub: str, name: str) -> tuple[Path, str] | None:
    """The file for a request path, or None if it is not one we serve."""
    if not DAY.match(day) or not NAME.match(rid) or sub not in SUBDIRS or not NAME.match(name) or ".." in name:
        return None
    kind = TYPES.get(Path(name).suffix.lower())
    if kind is None or (kind == 'application/json' and sub != 'outputs'):
        return None
    path = (root / day / rid / sub / name).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return (path, kind) if path.is_file() else None


def add_research_routes(app: FastAPI, manager: ResearchManager) -> None:
    @app.api_route("/research/{day}/{rid}/{sub}/{name}", methods=["GET", "HEAD"])
    async def research_files(day: str, rid: str, sub: str, name: str) -> Response:
        try:
            root = await asyncio.to_thread(manager.file_root, day, rid)
            found = research_file(root, day, rid, sub, name)
        except (OSError, ValueError):
            return JSONResponse({"error": "Retained research location is unavailable"}, status_code=409)
        if found is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        path, kind = found
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600",
                   "Cross-Origin-Resource-Policy": "same-origin"}
        if kind.startswith("text/html"):
            headers["Content-Security-Policy"] = LESSON_CSP
            headers["Cache-Control"] = "no-store"
            content = await asyncio.to_thread(lambda: artifact_document(path.read_bytes()))
            return Response(content, media_type=kind, headers=headers)
        return FileResponse(path, media_type=kind, headers=headers)

    @app.get("/api/research")
    async def research_list() -> JSONResponse:
        return JSONResponse(manager.list_wire(), headers={"Cache-Control": "no-store"})

    @app.get("/api/research-days")
    async def research_days() -> JSONResponse:
        """Days with research (today first): the studio's session list."""
        return JSONResponse({"today": manager.today(), "days": manager.days()}, headers={"Cache-Control": "no-store"})

    @app.get("/api/research-day/{day}")
    async def research_day(day: str) -> JSONResponse:
        """An earlier day's researchers, read from disk (today's are live on the socket)."""
        sessions = sorted(manager.read_day(day), key=lambda s: s.created)  # with the numbers they had that day
        return JSONResponse({"day": day, "sessions": [s.wire() for s in sessions]}, headers={"Cache-Control": "no-store"})

    @app.get("/api/research/{rid}")
    async def research_one(rid: str, day: str = "") -> JSONResponse:
        if (day and not DAY.fullmatch(day)) or not NAME.fullmatch(rid):
            return JSONResponse({"error": "invalid research identity"}, status_code=400)
        session = manager.get(rid)
        if day and (session is None or session.day != day):
            # Every researcher of that day, however it ended (its status is on its wire): one that stopped, failed or
            # was interrupted after saving findings is still read by its saved identity.
            session = next((s for s in await asyncio.to_thread(manager.read_day, day, False) if s.rid == rid), None)
        if session is None:
            return JSONResponse({"error": "no such researcher"}, status_code=404)
        try:
            steps = await asyncio.to_thread(manager.storage.steps, session, STEPS_SHOWN)
        except (OSError, ValueError) as error:
            steps = {"steps": [], "stepsTotal": None, "stepsUnavailable": str(error)}
        return JSONResponse({**session.wire(), **steps}, headers={"Cache-Control": "no-store"})

    @app.get('/api/research/{rid}/findings/{version}')
    async def research_findings(rid: str, version: int, day: str) -> JSONResponse:
        if not DAY.fullmatch(day) or not NAME.fullmatch(rid):
            return JSONResponse({'error': 'invalid research identity'}, status_code=400)
        session = manager.get(rid)
        if session is None or session.day != day:
            session = next((s for s in await asyncio.to_thread(manager.read_day, day, False) if s.rid == rid), None)
        record = next((r for r in session.finding_revisions if r['version'] == version), None) if session else None
        if record is None:
            return JSONResponse({'error': 'no such findings revision'}, status_code=404)
        return JSONResponse(record, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    capture_reads = asyncio.Semaphore(2)

    @app.get('/api/research/{rid}/findings/{version}/captures/{sequence}/{picture}')
    async def research_capture(rid: str, version: int, sequence: int, picture: int, day: str, digest: str) -> Response:
        if not DAY.fullmatch(day) or not NAME.fullmatch(rid) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            return JSONResponse({'error':'invalid captured finding identity'}, status_code=400)
        from ..resources import resources_of
        from .captures import resolve
        from .policy import ResearchRefused
        target = {'day':day,'rid':rid,'findingVersion':version,'findingDigest':digest,'sequence':sequence,'picture':picture}
        def read():
            index = resources_of(manager.hub)
            address = resolve(manager,index,target)
            found = index.picture(address.id,address.picture,address.day,max_bytes=4*1024*1024)
            resolve(manager,index,target)  # a correction during decoding cannot replace the original
            return found
        try:
            async with capture_reads:
                found = await asyncio.to_thread(read)
        except ResearchRefused as exc:
            return JSONResponse({'error':str(exc)}, status_code=409)
        except (OSError, ValueError):
            return JSONResponse({'error':'the original captured pixels could not be read'}, status_code=503)
        if found is None:
            return JSONResponse({'error':'the original captured pixels are unavailable'}, status_code=404)
        data, kind = found
        if len(data) > 4*1024*1024 or kind not in ('image/jpeg','image/png','image/webp'):
            return JSONResponse({'error':'the original capture exceeds this preview limit'}, status_code=413)
        return Response(data,media_type=kind,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'Cross-Origin-Resource-Policy':'same-origin'})

    @app.get('/api/research/{rid}/presentations/{output_id}')
    async def research_presentation(rid: str, output_id: str, day: str) -> JSONResponse:
        if not DAY.fullmatch(day) or not NAME.fullmatch(rid) or not NAME.fullmatch(output_id):
            return JSONResponse({'error': 'invalid research identity'}, status_code=400)
        session = manager.get(rid)
        if session is None or session.day != day:
            session = next((s for s in await asyncio.to_thread(manager.read_day, day) if s.rid == rid), None)
        output = session.output(output_id) if session else None
        if output is None or output.get('kind') != 'presentation':
            return JSONResponse({'error': 'no such retained presentation'}, status_code=404)
        from .presentation import retained_document
        try:
            document = retained_document(session, output)
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)
        return JSONResponse(document, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
