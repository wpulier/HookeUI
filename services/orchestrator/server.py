"""HookeUI orchestrator HTTP/WebSocket server: the routes on top of the App (app.py).

  GET  /api/health        service, source, motion, GPU and scene status, how the computer is doing
  GET  /api/machine/top   the programs using the most CPU (the performance meter's panel)
  GET  /api/day           today's conversations and digest
  GET  /api/execution[/id] bounded recent references / durable inspected facts (never chat memory)
  GET  /api/sessions      every session, newest first, and the one open now (the header chip's menu, the rail)
  GET  /api/sessions/<sid>/recordings  a session's recordings: its Record datasets' camera video (the camera menu)
  GET  /api/sessions/<sid>/sources     a session's sources: what is on stage, its assets, those removed (ADR 0022)
  GET  /api/actions                    the agent's tools with their arguments' schemas (the Actions panel)
  GET  /api/resources/<id>/table[.csv] a population's or motion item's measurements as rows, or CSV (tables.py)
  GET  /api/datatables[/<id>[.csv]]  dataset tables: measured tables pooled and edited, a version's rows (datatables.py)
  POST /api/datatables     a new one from {sources, title?, mode?, edits?}; PATCH /api/datatables/<id> {version, edits}
                          (or {version, update: true}) makes its next version (the studio only: X-MicroscopeMind: datatable)
  GET  /api/library[/<id>[/thumbnail|/file]] the user's own recordings and pictures, with their metadata (library.py)
  POST /api/session/new   a new session (make new-session; needs the X-MicroscopeMind header)
  POST /api/day/archive   its old name (make clear-session)
  GET  /api/crop/<id>     an object at full resolution from its sharpest recent frame
  GET  /api/capabilities  everything the app can do, who does it, what powers it, the user's verdicts
  GET  /api/capabilities/media/<name>  a capability's preview (docs/capabilities/media/)
  GET  /api/picture/<k>   a picture the app made from real frames (comparison, tracks, enhanced, snapshot)
  GET  /api/slidemap.jpg  the slide map of an epoch (the current one by default): real pixels of steady views
  GET  /api/datasets[/id] what Record collected: where each dataset is and what it holds
  GET  /api/session-dataset?scope=  what saving the session as a dataset would hold and miss (a preview)
  POST /api/session-dataset  save it, on the user's press (needs the X-MicroscopeMind header)
  POST /api/catalog/mine-file?name=  a model file dropped on the Models page, added as one of your own (the studio only)
  GET  /api/catalog/example/<key>  a capability's picture of it working (its manifest's example), for its card
  POST /api/volumes/upload?name=  a whole-brain volume chosen on the brainreg card, kept as a volume (the studio only)
  POST /api/keys/<service>  test and save an API key in the Keychain, {value, unchecked?} (the studio only:
                          X-MicroscopeMind: save-key and the studio's own origin); DELETE removes one the app saved
  GET  /api/research[/r]  researcher sessions (orchestrator/research/files.py)
  GET  /research/...      researchers' pictures and pages, served read-only
  WS   /ws/voice          talk with GPT-Live (orchestrator/voice/route.py)
  WS   /ws                binary frames (MMF1 + header + JPEG) and JSON state out; JSON
                          commands in (orchestrator/commands.py)
  GET  /                  the studio app build (apps/studio/dist), when present
"""
from __future__ import annotations

import argparse
import os
import asyncio
import json
import math
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from research_worker import api_usage

from .app import App
from .commands import handle
from .day import sid as sids
from .hub import Client
from .magnifier import crop_jpeg
from .origin import allowed_origin, studio_origin
from .paths import Paths
from .recordings import add_recording_routes
from .research.files import add_research_routes
from .voice.route import add_voice_route
from .workspace_transport import WorkspaceTransport

STUDIO_DIST = Path(__file__).resolve().parents[2] / "apps" / "studio" / "dist"
# A new session clears the live view: only the studio's own scripts may ask (a page from another local
# server cannot send this header without a CORS preflight, which is never answered).
ARCHIVE_HEADER = "X-MicroscopeMind"


def _installed_studio_tab_capture(client, _pending, metadata) -> bool:
    """Authorize the cooperative installed adapter against its original socket.

    The Screen has already normalized the reply and paired structural report.
    A tab bitmap still provides no atomic manifest of individual painted roots.
    Direct App users retain default refusal; reply fields cannot install this policy.
    """
    socket = getattr(client, "websocket", None)
    headers = getattr(socket, "headers", {})
    origin = headers.get("origin")
    basis = metadata.get("acquisitionBasis")
    if not isinstance(origin, str) or not allowed_origin(origin) or not isinstance(basis, dict):
        return False
    if (metadata.get("schema") != "workspace-capture-v1" or metadata.get("status") != "captured"
            or metadata.get("fidelity") != "actual-tab-capture"
            or metadata.get("captureMethod") != "authorized-tab-track"
            or metadata.get("surfaceProof") != "same-origin-page-handle"
            or basis.get("surfaceOrigin") != origin
            or basis.get("pageHandle") != metadata["client"]["pageEpoch"]):
        return False
    viewport = basis["viewport"]
    width, height = viewport["layoutSize"]
    if (not viewport["visible"] or not viewport["documentFocused"]
            or viewport["visualScale"] != 1
            or viewport["visualRect"] != [0, 0, width, height]):
        return False
    original_width, original_height = metadata["originalSize"]
    if abs(original_width / original_height - width / height) > 0.02:
        return False
    input_width, input_height = metadata["inputSize"]
    expected = [input_width / width, 0, 0, input_height / height, 0, 0]
    return all(math.isclose(actual, value, rel_tol=1e-9, abs_tol=1e-9)
               for actual, value in zip(metadata["workspaceToImage"], expected))


def create_app(bridge_url: str, use_gpu: bool = True, paths: Paths | None = None) -> FastAPI:
    from .build_info import identity, runtime_identity
    build = identity()  # backend source identity is fixed at process startup
    built = App.build(bridge_url, paths, use_gpu=use_gpu)
    built._workspace_capture_authority = _installed_studio_tab_capture
    hub = built.hub
    hub.execution.context_provider = lambda: {
        'build': dict(build),
        'settings': {key: getattr(hub.settings, key, None)
                     for key in ('vision', 'fast', 'sight', 'research', 'voicelink')},
    }

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        from .execution_archive import ExecutionArchive
        archive = ExecutionArchive(built.paths.home / 'diagnostics' / 'execution')
        hub.execution.archive = archive
        ledger = api_usage.UsageLedger(built.paths.research / "api-usage")
        ledger.seed_research(built.research.today(), built.research.spending.spent())
        app.state.usage = ledger
        api_usage.configure(ledger, provider_allowed=lambda: not built.day.local_only)
        lifecycle = app.state.research_lifecycle
        try:
            await built.start()
            lifecycle.start()
            yield
        finally:
            try:
                await app.state.workflow_service.close()
            finally:
                try:
                    await lifecycle.stop()
                finally:
                    try:
                        await built.stop()
                    finally:
                        api_usage.configure(None)
                        try:
                            ledger.close()
                        finally:
                            await asyncio.to_thread(archive.close)

    app = FastAPI(lifespan=lifespan)
    from .capability_journey_api import register as add_capability_journey_routes
    add_capability_journey_routes(app, built, studio_origin, ARCHIVE_HEADER)
    from .scientific_project_api import register as add_scientific_project_routes
    add_scientific_project_routes(app, built, studio_origin, ARCHIVE_HEADER)
    from .door.api import register as add_agent_door  # outside agents' one door (ADR 0026)
    add_agent_door(app, built, studio_origin, ARCHIVE_HEADER)
    from .scientific_import_transaction import ImportPending

    @app.exception_handler(ImportPending)
    async def import_pending(_request, error):
        return JSONResponse({'ok': False, 'says': str(error),
                             'next': 'retry after import completes; restart Hooke if an import was interrupted'},
                            status_code=503, headers={'Cache-Control': 'no-store', 'Retry-After': '1'})

    app.state.app = built
    app.state.hub, app.state.agent, app.state.brain = hub, built.agent, built.brain
    app.state.workspace_transport = built.workspace_transport = WorkspaceTransport(built)
    from .scientific_review_api import register as add_scientific_review_routes
    add_scientific_review_routes(app, built, studio_origin, ARCHIVE_HEADER)
    app.state.services, app.state.research, app.state.enrich, app.state.arena = (
        built.services, built.research, built.enrich, built.arena)
    add_research_routes(app, built.research)
    # The studio's own recording, saved as it goes (and kept in the dataset Record collects).
    from .resources import resources_of
    add_recording_routes(app, built.paths.recordings, keep=built.datasets.keep_screen if built.datasets else None,
                         made=lambda pid: built.day.made("recording", pid),
                         resolve=lambda rid, sid: resources_of(hub).get(rid, sid), library=getattr(hub, "library", None))
    add_voice_route(app, hub, built.brain, built.dialogue, built.workspace_transport, home=built.paths.home)

    @app.get("/api/presentation-sources/{token}")
    async def presentation_source(token: str, request: Request) -> Response:
        if not allowed_origin(request.headers.get("origin")):
            return JSONResponse({"ok": False, "code": "CLIENT_MISMATCH", "error": "not from this workspace"},
                                status_code=403, headers={"Cache-Control": "no-store"})
        return await built.workspace_transport.delivery_image(token, request.headers.get("X-Micromind-Workspace-Grant"))

    @app.get("/api/capabilities/usage")
    async def capability_usage() -> JSONResponse:
        archive = hub.execution.archive
        if archive is None:
            return JSONResponse({'error': 'Recorded usage is unavailable.'}, status_code=503)
        try:
            return JSONResponse(await asyncio.to_thread(archive.usage), headers={'Cache-Control': 'no-store'})
        except (OSError, sqlite3.Error):
            return JSONResponse({'error': 'Recorded usage could not be read.'}, status_code=503)

    @app.get("/api/execution/history/search")
    async def execution_search(q: str = '', cursor: str | None = None, limit: int = 30) -> JSONResponse:
        archive = hub.execution.archive
        if archive is None:
            return JSONResponse({'error': 'Execution archive is unavailable.'}, status_code=503)
        try:
            result = await asyncio.to_thread(archive.search, q, cursor=cursor, limit=limit)
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)
        except (OSError, sqlite3.Error):
            return JSONResponse({'error': 'Execution history could not be read.'}, status_code=503)

    @app.get("/api/execution/{receipt_id}")
    async def execution_detail(receipt_id: str) -> JSONResponse:
        return JSONResponse(await asyncio.to_thread(hub.execution.inspect, receipt_id), headers={"Cache-Control": "no-store"})

    @app.get('/api/execution/{receipt_id}/export')
    async def execution_export(receipt_id: str) -> JSONResponse:
        from .execution_export import redacted
        detail = await asyncio.to_thread(hub.execution.inspect, receipt_id)
        return JSONResponse(redacted(detail), headers={'Cache-Control': 'no-store',
                            'Content-Disposition': 'attachment; filename="execution-redacted.json"'})

    @app.get("/api/execution")
    async def execution_refs(thread: str | None = None) -> JSONResponse:
        archive = hub.execution.archive
        retained = await asyncio.to_thread(archive.references, thread) if archive else []
        entries = {entry['id']: entry for entry in retained}
        entries.update({entry['id']: entry for entry in hub.execution.references(thread)})
        return JSONResponse({"enabled": hub.execution.enabled, "entries": sorted(entries.values(), key=lambda e: (e['at'], e['id'])),
                             "archive": hub.execution.archive.status() if hub.execution.archive else None},
                            headers={"Cache-Control": "no-store"})

    @app.post("/api/execution/{receipt_id}/mark")
    async def mark_execution(receipt_id: str, request: Request) -> JSONResponse:
        if not allowed_origin(request.headers.get('origin')) or request.headers.get(ARCHIVE_HEADER) != 'mark-problem':
            return JSONResponse({'error': 'Mark a problem from the studio.'}, status_code=403)
        archive = hub.execution.archive
        if archive is None:
            return JSONResponse({'error': 'Execution archive is unavailable.'}, status_code=503)
        if len(await request.body()) > 4096:
            return JSONResponse({'error': 'Marker is too large.'}, status_code=400)
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) - {'id', 'note'}:
                raise ValueError('invalid marker fields')
            result = await asyncio.to_thread(archive.mark, receipt_id, body.get('id', ''), body.get('note', ''))
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except (ValueError, TypeError):
            return JSONResponse({'error': 'Marker could not be saved: invalid input or unavailable execution.'}, status_code=400)
        except OSError:
            return JSONResponse({'error': 'Marker could not be saved to disk. Your work continues.'}, status_code=503)

    @app.get("/api/health")
    async def health() -> JSONResponse:
        # A rebuilt bundle can replace dist while the old backend still runs.
        # Read its tiny manifest off-loop; never report the stale startup match.
        runtime = await asyncio.to_thread(runtime_identity, build)
        # The running look's own looks (failed ones too): none while the view is held (commands.py `view`; sim sessions).
        sight = {"looks": built.sight.looks} if getattr(built, "sight", None) is not None else None
        return JSONResponse({**hub.health(), 'build': runtime, 'sight': sight}, headers={"Cache-Control": "no-store"})

    @app.get("/api/spending")
    async def spending() -> JSONResponse:
        day = built.research.today()
        spent = built.research.spending.spent(day)
        summary = await asyncio.to_thread(app.state.usage.summary, day, spent)
        month = await asyncio.to_thread(app.state.usage.month)  # this month, and the optional cap on cloud models (C7)
        # The analytics: tracked charges only, by provider (today and this month the same way); the limit's counted
        # balance (month.estimatedUsd, holds and unknown allowances included) stays apart (Codex's recheck of e38fc57b).
        from research_worker.api_usage import UNKNOWN_USD

        month["entries"] = await asyncio.to_thread(app.state.usage.breakdown, month["month"])
        month["unknownEachUsd"] = UNKNOWN_USD
        summary["entries"] = await asyncio.to_thread(app.state.usage.breakdown, day) + [
            e for e in summary["entries"] if e["provider"] == "Earlier research"]
        # The day's totals are its rows' own sums, so a total never counts what its rows leave out (a hold)
        summary["estimatedUsd"] = round(sum(e["estimatedUsd"] for e in summary["entries"]), 6)
        summary["unpriced"] = sum(int(e.get("unpriced") or 0) for e in summary["entries"])
        return JSONResponse({**summary, "cap": hub.settings.research_cap, "month": month},
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/machine/top")
    async def machine_top() -> JSONResponse:
        """The programs using the most CPU now, for the performance meter's panel while it is open."""
        top = await asyncio.get_running_loop().run_in_executor(None, hub.machine.top)
        return JSONResponse({"top": top}, headers={"Cache-Control": "no-store"})

    @app.get("/api/day")
    async def day_state() -> JSONResponse:
        """Today: its conversations (title, times, specimens, researchers), specimens, preferences."""
        return JSONResponse({**built.day.wire(), "digest": built.brain.day_digest()}, headers={"Cache-Control": "no-store"})

    @app.get("/api/sessions")
    async def sessions() -> JSONResponse:
        """Every session, the most recently used first, and the one open now: what the header chip's menu and the
        sessions rail list (the `sessions` message's own rows)."""
        listing = await built.sessions_message()
        return JSONResponse({"current": listing["current"], "sessions": listing["sessions"]},
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/sessions/{sid}/recordings")
    async def session_recordings(sid: str) -> JSONResponse:
        """A session's recordings, newest first (datasets/recordings.py): the camera video of the Record datasets it
        holds, by the index's rule, for the camera menu and a returning session's view. None for an unknown session.
        Read off the event loop (each dataset's summary and sidecar)."""
        from .datasets import recordings
        from .resources import resources_of

        found = await asyncio.to_thread(recordings.of_session, resources_of(hub), sid) if sids.is_sid(sid) else []
        return JSONResponse({"session": sid, "recordings": found}, headers={"Cache-Control": "no-store"})

    @app.get("/api/sessions/{sid}/sources")
    async def session_sources(sid: str) -> JSONResponse:
        """A session's sources (sources.py; ADR 0022): what is on stage, its assets newest first (opened in it, or
        made in it), and those removed (for Undo). None for an unknown session."""
        if not sids.is_sid(sid):
            return JSONResponse({"error": "no such session"}, status_code=404)
        return JSONResponse(await built.session_sources(sid), headers={"Cache-Control": "no-store"})

    @app.get("/api/actions")
    async def actions_list() -> JSONResponse:
        """The Actions panel's forms (actions-panel.ts): each tool the agent has here, with its arguments' JSON schema, its
        kind and family. Which of them the panel offers, and their titles, are the capability list's (the
        `capabilities` message)."""
        registry = built.brain.registry
        return JSONResponse({"tools": {name: {"params": tool.params, "kind": tool.kind, "family": tool.family}
                                       for name, tool in registry.tools.items()}}, headers={"Cache-Control": "no-store"})

    @app.get("/api/library")
    async def library_list(q: str = "", kind: str | None = None, fresh: bool = False, scope: str | None = None,
                           specimen: str | None = None, category: str | None = None, offset: int = 0,
                           limit: int = 500) -> JSONResponse:
        """Every recording and picture in the library (library.py), newest first, matching `q` (title, specimen, file,
        notes, category): All assets, the empty stage's newest. Test footage is never listed.
        - `scope`: yours ("mine": your own, and the examples a session has used) or the examples ("example", the public
          corpus).
        - `categories`: the library's categories among what matches (library.CATEGORIES, in that order, then "Not sure
          yet" for what has had no look), each with how many and its newest as a cover: All assets opens on them.
        - `category`: only those of one category (or "Not sure yet").
        - `specimen`: only those of one specimen, as `specimens` names it.
        - `looks`: whether Library looks is on (LIBRARY_LOOKS), so the page can say why nothing is filed by look.
        - `offset`, `limit`: a page of them (`count` is how many match), so about 1,500 clips are never sent at once.
        - The examples also say their specimens (`specimens`: each with how many and its newest).
        - `fresh`: the folders read again now (the studio finding a file it has just added; the scan is otherwise
          reused for a few seconds)."""
        from .library import CATEGORIES, NOT_SURE, by_specimen, said_specimen

        library = getattr(hub, "library", None)
        if (library is None or len(q) > 200 or kind not in (None, "recording", "still") or scope not in (None, "mine", "example")
                or len(specimen or "") > 200 or category not in (None, NOT_SURE, *CATEGORIES) or offset < 0 or not 1 <= limit <= 500):
            return JSONResponse({"assets": []}, headers={"Cache-Control": "no-store"})
        found = await asyncio.to_thread(library.search, q, kind, fresh, scope)

        def wire(asset) -> dict:
            return {**asset.metadata(), "thumb": f"/api/library/{asset.id}/thumbnail"}

        def group_of(asset) -> str:
            return (asset.category() or {}).get("group") or NOT_SURE

        counts: dict[str, list] = {}
        for asset in found:
            counts.setdefault(group_of(asset), []).append(asset)
        cards = [{"group": g, "count": len(counts[g]), "cover": wire(counts[g][0])} for g in (*CATEGORIES, NOT_SURE) if g in counts]
        if category is not None:
            found = [a for a in found if group_of(a) == category]
        extra = {"specimens": [{"name": name, "count": n, "first": wire(first)} for name, n, first in by_specimen(found)]} \
            if scope == "example" else {}
        if specimen is not None:
            found = [a for a in found if said_specimen(a).lower() == " ".join(specimen.split()).lower()]
        return JSONResponse({"assets": [wire(a) for a in found[offset:offset + limit]], "count": len(found),
                             "categories": cards, "looks": hub.settings.library_looks, **extra},
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/library/{aid}")
    async def library_asset(aid: str) -> JSONResponse:
        """One asset: its metadata, and every session that uses it (its preview's "In sessions", the trash's confirm)."""
        from .sources import sessions_using

        library = getattr(hub, "library", None)
        asset = await asyncio.to_thread(library.get, aid) if library is not None else None
        if asset is None:
            return JSONResponse({"error": "no such asset"}, status_code=404)
        assets, infos = await asyncio.to_thread(lambda: (library.assets(), built.day.catalogue.list()))

        def made_with() -> list[dict]:  # what a picture says made it, and where each stands here (made_with.py)
            from . import made_with as mw

            return mw.offers(mw.of_asset(asset.path))  # a picture's own, or its recording's dataset summary

        offers = await asyncio.to_thread(made_with)
        return JSONResponse({**asset.metadata(), "thumb": f"/api/library/{aid}/thumbnail",
                             "sessions": sessions_using(aid, assets, infos), **({"madeWith": offers} if offers else {})},
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/library/{aid}/file")
    async def library_file(aid: str) -> Response:
        """An asset's own file, for its preview in All assets (a recording plays there, seeking by range; a picture
        shows). Only the library's assets: nothing else on disk is served."""
        from .library import is_asset_id

        library = getattr(hub, "library", None)
        asset = await asyncio.to_thread(library.get, aid) if library is not None and is_asset_id(aid) else None
        if asset is None or not asset.path.is_file():
            return JSONResponse({"error": "no such asset"}, status_code=404)
        return FileResponse(asset.path, headers={"Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/library/{aid}/share")
    async def library_share(aid: str) -> Response:
        from . import made_with, result_entries
        from .capture import captures
        from .library import is_asset_id
        asset = await asyncio.to_thread(hub.library.get, aid) if is_asset_id(aid) else None
        if asset is None:
            return JSONResponse({"error": "that original asset is no longer available"}, status_code=404)
        def prepare():
            source = asset.path
            # A snapshot is listed by its clean copy (<date>/clean/<name>.png), but the outlines its Made with names are
            # drawn only on the snapshot itself (<date>/<name>.png): that is the picture that carries them. Download
            # original keeps serving the clean file.
            if source.parent.name == "clean" and source.stem.startswith("snapshot-"):
                drawn = source.parent.parent / source.name
                if drawn.is_file():
                    source = drawn
            return result_entries.export_path(source, made_with.of_asset(source, include_local=True), captures(hub).memory)
        return await portable_response(prepare)

    async def portable_response(prepare) -> Response:
        """One worker/download/error/cleanup lifecycle for both original-source resolvers."""
        try:
            exported = await asyncio.to_thread(prepare)
            return FileResponse(exported["path"], filename="portable-result" + exported["path"].suffix,
                                background=BackgroundTask(exported["path"].unlink, missing_ok=True),
                                headers={"Cache-Control": "no-store", "X-Original-SHA256": exported["originalSha256"],
                                         "X-Share-SHA256": exported["shareSha256"],
                                         # What it carries, so the studio says no more than that (ADR 0006)
                                         "X-Carried-Entries": str(exported.get("carried", 0)),
                                         "X-Named-Entries": str(exported.get("named", 0))})
        except (OSError, ValueError, RuntimeError) as exc:
            return JSONResponse({"error": f"Original kept. Portable sharing unavailable: {exc}"}, status_code=409)

    @app.get("/api/onboarding/example")
    async def onboarding_example():
        from .examples import human_mitosis
        try:
            path = await asyncio.to_thread(human_mitosis, preview=True)
        except (OSError, ValueError):
            return Response("The example image is missing or damaged. Reinstall this release to restore it.", status_code=503)
        return FileResponse(path, media_type="image/png")

    @app.get("/api/library/{aid}/thumbnail")
    async def library_thumbnail(aid: str, v: str = "") -> Response:
        """An asset's small picture (thumbnails.py: one at a time at background priority, kept in memory by `v`)."""
        from .library import is_asset_id
        from .thumbnails import LIBRARY, MISS_S, thumbnails_of

        if not is_asset_id(aid) or len(v) > 64:
            return JSONResponse({"error": "no such asset"}, status_code=404)
        data = await thumbnails_of(built.hub).get(aid, LIBRARY, v)
        if data is None:
            return JSONResponse({"error": "no picture to make a thumbnail from"}, status_code=404,
                                headers={"Cache-Control": f"private, max-age={int(MISS_S)}"})
        return Response(data, media_type="image/jpeg", headers={
            "Cache-Control": "private, max-age=3600" if v else "no-store", "X-Content-Type-Options": "nosniff"})

    async def _new_session(request: Request) -> JSONResponse:
        if not allowed_origin(request.headers.get("origin")) or \
                request.headers.get(ARCHIVE_HEADER) not in ("new-session", "clear-today"):
            return JSONResponse({"ok": False, "error": "not from here"}, status_code=403)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        name = str(body.get("name") or "") if isinstance(body, dict) else ""
        opened = built.new_session(name, by="user")
        return JSONResponse({"ok": True, **opened, "name": built.day.name})

    def _key_request(request: Request) -> bool:
        return (request.headers.get(ARCHIVE_HEADER) == "save-key"
                and studio_origin(request.headers.get("origin"), request.url.port))

    @app.post("/api/own-trials")
    async def own_trial_input(request: Request) -> JSONResponse:
        from . import own_trials
        from .brain.registry import ToolFailed
        if not (request.headers.get(ARCHIVE_HEADER) == "own-picture" and studio_origin(request.headers.get("origin"), request.url.port)):
            return JSONResponse({"says": "Choose a picture from the studio."}, status_code=403)
        from .capture import Refused, captures
        try:
            reservation = captures(built.hub).memory.reserve("analysis", own_trials.MAX_BYTES * 2, "receiving comparison picture")
        except Refused as exc:  # memory is full right now: said as the app says it, never a bare 500
            return JSONResponse({"says": f"Not now: {exc}. {getattr(exc, 'next', '') or ''}".strip()}, status_code=503)
        with reservation:
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > own_trials.MAX_BYTES:
                    return JSONResponse({"says": "Choose a picture of at most 8 MiB."}, status_code=413)
                data.extend(chunk)
            try:
                row = await asyncio.to_thread(own_trials.add_input, bytes(data), request.query_params.get("job"), built.hub)
            except (ValueError, ToolFailed) as exc:
                return JSONResponse({"says": str(exc)}, status_code=400)
        return JSONResponse(row)

    @app.get("/api/own-trials/{tid}/{name}")
    async def own_trial_picture(tid: str, name: str):
        from . import own_trials
        if name not in ("input", "candidate.png", "baseline.png", "candidate.json", "baseline.json"):
            return Response(status_code=404)
        try:
            row = own_trials.state(tid)
            path = own_trials.folder(tid) / name
        except ValueError:
            return Response(status_code=404)
        if not path.is_file():
            return Response(status_code=404)
        from fastapi.responses import FileResponse
        return FileResponse(path, media_type=row["media"] if name == "input" else "application/json" if name.endswith(".json") else "image/png",
                            headers={"Cache-Control": "no-store"})

    @app.post("/api/catalog/mine-file")
    async def add_model_file(request: Request) -> JSONResponse:
        """A model file dropped on the Models page (mine.py): streamed to <catalog>/uploads/, then added as one of your
        own (its entry written, its file cloned into a checked, read-only revision). The studio only (its origin and
        X-MicroscopeMind: add-model); nothing of it runs until it is turned on."""
        import uuid

        from gpu_worker import manifests

        from . import mine

        if not (request.headers.get(ARCHIVE_HEADER) == "add-model" and studio_origin(request.headers.get("origin"), request.url.port)):
            return JSONResponse({"ok": False, "says": "Add a model from the studio."}, status_code=403)
        name = re.sub(r"[^\w.\- ]", "_", Path(request.query_params.get("name") or "model").name)[:120] or "model"
        folder = manifests.catalog_root() / "uploads" / uuid.uuid4().hex[:12]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        size = 0
        with path.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > 8 * 1024 ** 3:
                    out.close()
                    path.unlink(missing_ok=True)
                    return JSONResponse({"ok": False, "says": "That file is over 8 GB: put it in the folder instead"},
                                        status_code=413)
                await asyncio.to_thread(out.write, chunk)
        try:
            added = await asyncio.to_thread(mine.add, str(path), request.query_params.get("title") or "")
        except mine.CantDo as exc:
            return JSONResponse({"ok": False, "says": str(exc)}, status_code=400)
        if request.query_params.get("trial"):
            from . import own_trials
            try:
                await asyncio.to_thread(own_trials.prepare, added["model"], request.query_params["trial"])
            except ValueError as exc:
                return JSONResponse({"ok": False, "says": str(exc)}, status_code=400)
        else:
            from . import own_trials
            await asyncio.to_thread(own_trials.await_comparison, added["model"])
        await built.sync_mine()
        return JSONResponse({"ok": True, **added})

    @app.post("/api/volumes/upload")
    async def add_volume_file(request: Request) -> JSONResponse:
        """A whole-brain volume chosen on the brainreg card (a TIFF or OME-TIFF), streamed into the jobs folder, then kept
        as a volume of the session (registrations.add_volume: what its file states read by brainreg's own Python). The
        studio only (its origin and X-MicroscopeMind: add-volume)."""
        import uuid

        from .registrations import CantDo

        if not (request.headers.get(ARCHIVE_HEADER) == "add-volume" and studio_origin(request.headers.get("origin"), request.url.port)):
            return JSONResponse({"ok": False, "says": "Add a volume from the studio."}, status_code=403)
        regs = getattr(built, "registrations", None)
        if regs is None or regs.root is None:
            return JSONResponse({"ok": False, "says": "Volumes can't be kept in this run."}, status_code=400)
        session = regs.session()  # admitted owner, before receiving any streamed body or yielding to disk work
        thread = built.day.active_id if built.day is not None else None
        name = re.sub(r"[^\w.\- ]", "_", Path(request.query_params.get("name") or "volume.tif").name)[:120] or "volume.tif"
        folder = regs.root / "uploads" / uuid.uuid4().hex[:12]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        size = 0
        with path.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > 16 * 1024 ** 3:
                    out.close()
                    path.unlink(missing_ok=True)
                    return JSONResponse({"ok": False, "says": "That file is over 16 GB: add it by its path instead"},
                                        status_code=413)
                await asyncio.to_thread(out.write, chunk)
        try:
            record = await asyncio.to_thread(regs.add_volume, str(path), name=Path(name).stem,
                                             thread=thread, session=session)
        except CantDo as exc:
            return JSONResponse({"ok": False, "says": str(exc)}, status_code=400)
        finally:
            path.unlink(missing_ok=True)  # kept as the volume's own copy; the upload's staging goes
        return JSONResponse({"ok": True, "volume": record, "missing": regs.missing(record), "says": regs.words(record)})

    @app.post("/api/keys/{service}")
    async def save_key(service: str, request: Request) -> JSONResponse:
        """Test a key and save it in the Keychain (key_admin.py): never logged, echoed or sent anywhere but its service's
        own test. A refused key is not saved; one that could not be checked only with {"unchecked": true}."""
        if not _key_request(request):
            return JSONResponse({"ok": False, "says": "Add a key from the studio."}, status_code=403)
        if len(await request.body()) > 8192:
            return JSONResponse({"ok": False, "says": "That is too long to be a key."}, status_code=400)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("value"), str):
            return JSONResponse({"ok": False, "says": "Paste the key, then save."}, status_code=400)
        result = await built.key_admin.save(service, body["value"], unchecked=body.get("unchecked") is True)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.delete("/api/keys/{service}")
    async def remove_key(service: str, request: Request) -> JSONResponse:
        """Remove a key this app saved; a key from the keys file or the environment is said to be changed there."""
        if not _key_request(request):
            return JSONResponse({"ok": False, "says": "Remove a key from the studio."}, status_code=403)
        return JSONResponse(await built.key_admin.remove(service), headers={"Cache-Control": "no-store"})

    @app.post("/api/session/new")
    async def new_session(request: Request) -> JSONResponse:
        """A new session without a restart (make new-session; ADR 0017): the live view is cleared and one empty
        conversation opens; the session left is kept as it is (nothing moves), and a researcher working there
        finishes in it. Optional JSON body {"name"}."""
        return await _new_session(request)

    @app.post("/api/day/archive")
    async def archive_day(request: Request) -> JSONResponse:
        """The old "start today afresh" (make clear-session): a new session now, as /api/session/new."""
        return await _new_session(request)

    @app.get("/api/crop/{object_id}")
    async def crop(object_id: str, pad: float = 0.15) -> Response:
        """The object at full camera resolution, from its sharpest recent frame."""
        obj = hub.scene.objects.get(object_id)
        if obj is None:
            return JSONResponse({"error": "no such object"}, status_code=404)
        found = await asyncio.get_running_loop().run_in_executor(None, crop_jpeg, hub, obj, max(0.0, min(pad, 2.0)))
        if found is None:
            return JSONResponse({"error": "no recent frame of that object"}, status_code=404)
        jpeg, meta = found
        return Response(jpeg, media_type="image/jpeg", headers={
            "Cache-Control": "no-store", "X-Frame-Id": str(meta["frame_id"]),
            "X-Crop": ",".join(map(str, meta["rect"])), "X-Sharpness": f"{meta['sharpness']:.4f}"})

    @app.get("/api/catalog/share/{key:path}")
    async def catalog_share(key: str, request: Request) -> Response:
        """Share a file (share.py, C5): an entry with its own adapter as one small file to send any way (never its
        weights). The studio only; `by` is the name the sharer gives, said where it is opened."""
        from . import share

        from urllib.parse import urlsplit

        referer = urlsplit(request.headers.get("referer") or "")
        origin = request.headers.get("origin") or (f"{referer.scheme}://{referer.netloc}" if referer.netloc else "")
        if not studio_origin(origin, request.url.port):  # a download link sends its page's address, not an Origin
            return JSONResponse({"ok": False, "says": "Share from the studio."}, status_code=403)
        try:
            path = await asyncio.to_thread(share.export, key, (request.query_params.get("by") or "")[:80])
        except share.CantShare as exc:
            return JSONResponse({"ok": False, "says": str(exc)}, status_code=400)
        return FileResponse(path, media_type="application/zip", filename=path.name,
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/catalog/example/{key:path}")
    async def catalog_example(key: str, preview: str = "") -> Response:
        """A capability's picture of it working (its manifest's `example`, a file beside it): its card and New for you
        show it. Only that file, only a picture."""
        from pathlib import Path as _Path

        from gpu_worker import manifests as catalog

        m = catalog.get(key)
        if preview or m is None or not m.example:  # one only peeked at (catalog_news.peeked): its picture, from its peeked folder
            from . import catalog_news

            peeked = await asyncio.to_thread(catalog_news.preview_example, key, commit=preview)
            if peeked is None:
                return JSONResponse({"error": "no example picture"}, status_code=404)
            folder, found = peeked.parent, peeked
        else:
            folder = _Path(m.folder).resolve()
            found = (folder / m.example).resolve()
        types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
        if folder not in found.parents or found.suffix.lower() not in types or not found.is_file():
            return JSONResponse({"error": "no example picture"}, status_code=404)
        return FileResponse(found, media_type=types[found.suffix.lower()],
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/catalog/space")
    async def capability_storage() -> JSONResponse:
        from . import capability_space

        return JSONResponse(await asyncio.to_thread(capability_space.inventory, built.hub.resources.projects), headers={"Cache-Control": "no-store"})

    @app.get("/api/catalog/inventories/fiji")
    async def fiji_inventory() -> JSONResponse:
        """Every Fiji extension listed (orchestrator/inventories.py), each with how it installs, whether it is in this
        Fiji and whether a reviewed binding connects it, and `summary.bound`, the functions connected (whether one runs
        here is its own row's readiness): a Toolbox view asks for it when it is shown."""
        from . import inventories

        from . import fiji_generations

        listed = await asyncio.to_thread(lambda: inventories.rows(installed=fiji_generations.sites()))
        functions = await asyncio.to_thread(inventories.bound)
        return JSONResponse({"extensions": listed, "summary": inventories.summary(listed, functions),
                             "why": inventories.fiji().get("why", "")}, headers={"Cache-Control": "no-store"})

    @app.get("/api/capabilities")
    async def capabilities() -> JSONResponse:
        """Everything the app can do, as the Capabilities page and the agent see it (ADR 0007)."""
        return JSONResponse(built.capabilities_wire(), headers={"Cache-Control": "no-store"})

    @app.get("/api/capabilities/media/{name}")
    async def capability_media(name: str) -> Response:
        """A capability's preview (docs/capabilities/media/): a known kind of file, by its bare name."""
        from .capabilities import MEDIA, MEDIA_TYPES

        kind = MEDIA_TYPES.get(name.rsplit(".", 1)[-1].lower()) if "." in name else None
        path = MEDIA / name
        if kind is None or "/" in name or ".." in name or not path.is_file():
            return JSONResponse({"error": "no such preview"}, status_code=404)
        return Response(path.read_bytes(), media_type=kind, headers={
            "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600",
            "Cross-Origin-Resource-Policy": "same-origin"})

    @app.get("/api/datasets")
    async def datasets() -> JSONResponse:
        """Every dataset (the Datasets tab): where it is, what it holds, its state."""
        from .datasets import store

        listing = await asyncio.to_thread(store.listing, built.paths.datasets)
        collecting = built.datasets.wire() if built.datasets is not None else None
        if collecting and collecting["on"]:  # its counts as they are now
            for row in listing["datasets"]:
                if row["id"] == collecting["id"]:
                    row["counts"] = built.datasets.counts()
        return JSONResponse({**listing, "collecting": collecting},
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/datasets/{dataset_id}/citations")
    async def dataset_citations(dataset_id: str) -> JSONResponse:
        from .datasets import citations, store

        folder = store.find(built.paths.datasets, dataset_id)
        if folder is None:
            return JSONResponse({"error": "no such dataset"}, status_code=404)
        facts = await asyncio.to_thread(citations.recorded_models, folder)
        return JSONResponse(facts, headers={"Cache-Control": "no-store"})

    @app.get("/api/datasets/{dataset_id}/thumb")
    async def dataset_thumb(dataset_id: str) -> Response:
        """The picture the Datasets tab shows for a dataset, from its own pictures (store.thumb)."""
        from .datasets import manifest, store

        folder = store.find(built.paths.datasets, dataset_id)
        picture = store.thumb(folder, manifest.read(folder) or {}) if folder is not None else None
        if picture is None:
            return JSONResponse({"error": "no picture"}, status_code=404)
        kind = "image/png" if picture.suffix == ".png" else "image/jpeg"
        return Response(picture.read_bytes(), media_type=kind, headers={
            "Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/datasets/{dataset_id}")
    async def dataset(dataset_id: str) -> JSONResponse:
        """One dataset's whole summary (dataset.json)."""
        from .datasets import manifest, store

        folder = store.find(built.paths.datasets, dataset_id)
        summary = manifest.read(folder) if folder is not None else None
        if summary is None:
            return JSONResponse({"error": "no such dataset"}, status_code=404)
        return JSONResponse({"path": str(folder), "summary": summary}, headers={"Cache-Control": "no-store"})

    @app.get("/api/session-dataset")
    async def session_dataset_preview(scope: str = "conversation") -> JSONResponse:
        """What saving this conversation (or today so far) as a dataset would hold, miss and leave
        pending, and where it would go: the review the Datasets tab shows before Save (datasets/snapshot.py)."""
        from .datasets import snapshot

        try:
            start, stop = snapshot.window(built.day, scope, time.time())
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        review = await asyncio.to_thread(snapshot.preview, built.paths, start, stop, scope)
        return JSONResponse(review, headers={"Cache-Control": "no-store"})

    @app.post("/api/session-dataset")
    async def session_dataset_save(request: Request) -> JSONResponse:
        """Save the session as a dataset, without stopping it: only the user's Save in the Datasets tab sends
        this (ADR 0008), so it needs the X-MicroscopeMind header another local page cannot send."""
        from .capabilities import build as app_build
        from .datasets import snapshot

        if not allowed_origin(request.headers.get("origin")) or request.headers.get(ARCHIVE_HEADER) != "save-session":
            return JSONResponse({"ok": False, "error": "not from here"}, status_code=403)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        try:
            start, stop = snapshot.window(built.day, str(body.get("scope") or "conversation"), time.time())
            folder = await asyncio.to_thread(snapshot.save, built.paths, start, stop, scope=str(body.get("scope") or ""),
                                             name=str(body.get("name") or ""), build=app_build())
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "id": folder.name, "path": str(folder)})

    # The session's one index, read-only (resources.py; Amendment 2.7): the same entries, ids and provenance
    # the brain and the researchers' session tool read, for the studio and programs outside the app.
    def _day_ok(day: str | None) -> bool:
        return day is None or sids.is_sid(day)  # `day` names a session (its id: day/sid.py)

    def _listing(researcher: bool):
        async def resources(kind: str | None = None, q: str | None = None, day: str | None = None,
                            since: str | None = None) -> Response:
            """The session's entries and their revision (Index.listing): built and encoded off the event loop, so a
            listing of many entries never stalls the live view; `since` (the revision the studio has) answers
            `unchanged` with no entries when nothing changed (ticket 66, S7)."""
            if not _day_ok(day) or (since is not None and len(since) > 64):
                return JSONResponse({"error": "day is a session id and since a revision"}, status_code=400)
            index = built.hub.resources

            def build() -> bytes:
                listing = index.listing(day, kind=kind, query=q, since=None if researcher else since, researcher=researcher)
                return json.dumps(listing, ensure_ascii=False, separators=(",", ":"), default=str).encode()

            return Response(await asyncio.to_thread(build), media_type="application/json",
                            headers={"Cache-Control": "no-store"})
        return resources

    def _summary():
        async def resources_summary(day: str | None = None) -> JSONResponse:
            if not _day_ok(day):
                return JSONResponse({"error": "day is a session id"}, status_code=400)
            summary = await asyncio.to_thread(built.hub.resources.summary, day)
            return JSONResponse(summary, headers={"Cache-Control": "no-store"})
        return resources_summary

    def _resource(researcher: bool):
        async def resource(eid: str, day: str | None = None, offset: int | None = None,
                           revision: str | None = None) -> JSONResponse:
            """One entry in full. With no offset (the studio's reading) it adds the listing's fields and how it was
            made (Index.detail); a researcher's reading, and every paged one, is the canonical document."""
            return await _read_resource(eid, day, offset, revision, researcher)
        return resource

    # Researchers read the index here (their session tool, ResearchManager.resources_url): what they read stays as it
    # was before ticket 66 (no Data fields, and a researcher's work only when asked for by kind; context earns its place).
    RESEARCHER_VIEW = "/for-researchers"
    for prefix, researcher in (("", False), (RESEARCHER_VIEW, True)):
        app.add_api_route(f"{prefix}/api/resources", _listing(researcher), methods=["GET"])
        app.add_api_route(f"{prefix}/api/resources/summary", _summary(), methods=["GET"])
        app.add_api_route(f"{prefix}/api/resources/{{eid}}", _resource(researcher), methods=["GET"])

    async def _read_resource(eid: str, day: str | None, offset: int | None, revision: str | None,
                             researcher: bool) -> JSONResponse:
        if not _day_ok(day):
            return JSONResponse({"error": "day is a session id"}, status_code=400)
        index = built.hub.resources
        if offset is None and not researcher:
            document = await asyncio.to_thread(index.detail, eid, day)
        else:
            document = await asyncio.to_thread(index.document, eid, day, discover_tables=True)
        if document is None:
            return JSONResponse({"error": "nothing by that id"}, status_code=404)
        if offset is not None:
            # Pages identify the complete reading, including rich values/member rows.
            # A changed document invalidates continuation instead of mixing revisions.
            import hashlib
            text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
            digest = hashlib.sha256(text.encode()).hexdigest()
            if revision is not None and revision != digest:
                return JSONResponse({'error': 'the resource changed; restart reading at offset 0'}, status_code=409)
            if offset < 0 or offset > len(text) or (offset > 0 and revision is None):
                return JSONResponse({'error': 'continuation needs a valid offset and the original revision'}, status_code=400)
            end = min(len(text), offset + 8000)
            document = {'entry': document['entry'], 'text': text[offset:end], 'page': {
                'offset': offset, 'nextOffset': end if end < len(text) else None,
                'totalChars': len(text), 'revision': digest, 'encoding': 'JSON text fragments; concatenate in offset order'}}
        return JSONResponse(document, headers={"Cache-Control": "no-store"})

    @app.get("/api/resources/{eid}/overlay")
    async def resource_overlay(eid: str, request: Request, session: str, revision: int) -> Response:
        from .artifact_overlay import read_overlay, OverlayRefused
        try:
            data = await read_overlay(built, eid, session, revision)
            if await request.is_disconnected():
                return Response(status_code=499)
            return JSONResponse(data, headers={"Cache-Control": "no-store"})
        except OverlayRefused as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @app.get("/api/resources/{eid}/table")
    async def resource_table(eid: str, request: Request, session: str, revision: int) -> Response:
        """Measurements of the exact session/result/revision selected in the Studio."""
        from .tables import read_table, TableRefused
        try:
            if await request.is_disconnected():
                return Response(status_code=499)
            table = await read_table(built, eid, session, revision)
            return JSONResponse(table, headers={"Cache-Control": "no-store"})
        except TableRefused as exc:
            return JSONResponse({"error": str(exc)}, status_code=409, headers={"Cache-Control": "no-store"})

    @app.get("/api/resources/{eid}/table.csv")
    async def resource_table_csv(eid: str, request: Request, session: str, revision: int) -> Response:
        """The selected Measurements table as CSV; distinct from the exact native-pixel exchange bundle."""
        from .tables import csv_text, file_name, read_table, TableRefused
        try:
            if await request.is_disconnected():
                return Response(status_code=499)
            table = await read_table(built, eid, session, revision)
            return Response(csv_text(table), media_type="text/csv; charset=utf-8", headers={
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                "Content-Disposition": f'attachment; filename="{file_name(table, session)}"'})
        except TableRefused as exc:
            return JSONResponse({"error": str(exc)}, status_code=409, headers={"Cache-Control": "no-store"})

    # Dataset tables: explicit Project admission; large response encoding stays off the live loop.
    def _datatable_write(request: Request) -> bool:
        return allowed_origin(request.headers.get("origin")) and request.headers.get(ARCHIVE_HEADER) == "datatable"

    def _datatable_refused(exc) -> JSONResponse:
        return JSONResponse({"error": str(exc), "next": exc.next}, status_code=exc.status, headers={"Cache-Control": "no-store"})

    def _datatable_owner(tables, session):
        from .datatables import DataTableRefused
        if not isinstance(session, str) or not session:
            raise DataTableRefused('say which Project owns this table', next='send its session ID', status=400)
        return tables.capture(session)

    async def _datatable_body(request: Request) -> dict:
        try:
            body = await request.json()
        except ValueError:
            body = None
        return body if isinstance(body, dict) else {}

    async def _datatable_json(tables, owner, value) -> Response:
        from .datatables import snapshot_bytes
        body = await asyncio.to_thread(snapshot_bytes, value)
        owner.check(built)
        return Response(body, media_type='application/json', headers={'Cache-Control': 'no-store'})

    async def _datatable_result(tables, owner, record) -> Response:
        snapshot = await asyncio.to_thread(tables.read, record['id'], record['version'], owner=owner)
        owner.check(built)
        return await _datatable_json(tables, owner, tables.wire(record, snapshot))

    @app.get("/api/datatables")
    async def datatables_list() -> Response:
        from .datatables import DataTableRefused, datatables_of
        tables = datatables_of(built)
        owner = tables.capture()
        try:
            records = [tables.latest(tid) for tid in tables.ids()]
            return await _datatable_json(tables, owner, {'session': owner.session,
                                          'tables': [tables.wire(r) for r in records if r]})
        except DataTableRefused as exc:
            return _datatable_refused(exc)

    @app.post("/api/datatables")
    async def datatables_create(request: Request) -> Response:
        from .datatables import DataTableRefused, datatables_of
        if not _datatable_write(request):
            return JSONResponse({"error": "not from here"}, status_code=403)
        body, tables = await _datatable_body(request), datatables_of(built)
        try:
            owner = _datatable_owner(tables, body.get('session'))
            record = await tables.create(body.get('sources') or [], title=str(body.get('title') or ''), by='user',
                                         mode=str(body.get('mode') or 'follow'), edits=body.get('edits') or None, owner=owner)
            return await _datatable_result(tables, owner, record)
        except DataTableRefused as exc:
            return _datatable_refused(exc)

    @app.get("/api/datatables/{tid}.csv")
    async def datatable_csv(tid: str, version: int | None = None, session: str | None = None) -> Response:
        from .datatables import DataTableRefused, csv_text, datatables_of, file_name
        tables = datatables_of(built)
        try:
            owner = _datatable_owner(tables, session)
            record = tables.version(tid, version)
            snapshot = await asyncio.to_thread(tables.read, tid, record['version'], owner=owner)
            text = await asyncio.to_thread(lambda: csv_text(snapshot).encode('utf-8'))
            owner.check(built)
            return Response(text, media_type='text/csv; charset=utf-8', headers={
                'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                'Content-Disposition': f'attachment; filename="{file_name(record, owner.session)}"'})
        except DataTableRefused as exc:
            return _datatable_refused(exc)

    from .table_routes import add_table_routes
    add_table_routes(app, built, allowed_origin, ARCHIVE_HEADER)

    @app.get("/api/datatables/{tid}")
    async def datatable(tid: str, version: int | None = None, session: str | None = None) -> Response:
        from .datatables import DataTableRefused, datatables_of
        tables = datatables_of(built)
        try:
            owner = _datatable_owner(tables, session)
            return await _datatable_result(tables, owner, tables.version(tid, version))
        except DataTableRefused as exc:
            return _datatable_refused(exc)

    @app.patch("/api/datatables/{tid}")
    async def datatable_edit(tid: str, request: Request) -> Response:
        from .datatables import DataTableRefused, datatables_of
        if not _datatable_write(request):
            return JSONResponse({"error": "not from here"}, status_code=403)
        body, tables = await _datatable_body(request), datatables_of(built)
        version = body.get('version')
        if type(version) is not int:
            return JSONResponse({'error': 'say which version you edited', 'next': 'send its version'}, status_code=400)
        try:
            owner = _datatable_owner(tables, body.get('session'))
            if body.get('update') is True:
                record = await tables.update(tid, by='user', owner=owner, version=version)
            else:
                record = await tables.edit(tid, version, body.get('edits') or [], by='user', owner=owner)
            return await _datatable_result(tables, owner, record)
        except DataTableRefused as exc:
            return _datatable_refused(exc)

    @app.get("/api/resources/{eid}/picture")
    @app.get(RESEARCHER_VIEW + "/api/resources/{eid}/picture")
    async def resource_picture(eid: str, n: int = 0, day: str | None = None) -> Response:
        if not _day_ok(day):
            return JSONResponse({"error": "day is a session id"}, status_code=400)
        found = await asyncio.to_thread(built.hub.resources.picture, eid, n, day)
        if found is None:
            return JSONResponse({"error": "no such picture"}, status_code=404)
        data, kind = found
        return Response(data, media_type=kind, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/resources/{eid}/share")
    async def resource_share(eid: str, day: str, n: int = 0) -> Response:
        from . import made_with, result_entries
        from .capture import captures
        if not _day_ok(day):
            return JSONResponse({"error": "day is a session id"}, status_code=400)
        def prepare():
            index = built.hub.resources
            entry = index.get(eid, day)
            if entry is None:
                raise ValueError("that original is no longer in this session")
            if entry.kind == "result":
                return index.results.export_picture(eid, n, day, captures(hub).memory)
            if entry.kind == "snapshot" and n == 0 and index.snapshots is not None:
                path = index.snapshots / entry.date / f"{eid}.png"
                return result_entries.export_path(path, made_with.of_asset(path, include_local=True), captures(hub).memory)
            raise ValueError("open a retained result picture or snapshot to download a portable copy")
        return await portable_response(prepare)

    @app.get("/api/resources/{eid}/thumbnail")
    async def resource_thumbnail(eid: str, day: str | None = None, v: str = "") -> Response:
        """A product's small picture for Made today (thumbnails.py): made from what it keeps, one at a time at
        background priority, off the loop, kept in memory by `v` (what the studio says the entry is now)."""
        if not _day_ok(day) or len(v) > 64:
            return JSONResponse({"error": "day is a session id and v a short version"}, status_code=400)
        from .thumbnails import MISS_S, thumbnails_of

        data = await thumbnails_of(built.hub).get(eid, day, v)
        if data is None:
            return JSONResponse({"error": "no picture to make a thumbnail from"}, status_code=404,
                                headers={"Cache-Control": f"private, max-age={int(MISS_S)}"})
        return Response(data, media_type="image/jpeg", headers={
            "Cache-Control": "private, max-age=3600" if v else "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/resources/{eid}/exchange")
    async def resource_exchange(eid: str, request: Request, session: str, revision: int) -> Response:
        from .particle_exchange import export, ExchangeRefused
        try:
            data = await export(built, eid, session, revision)
            if await request.is_disconnected():
                return Response(status_code=499)
            return Response(data, media_type="application/zip", headers={
                "Content-Disposition": f'attachment; filename="particles-{eid}-r{revision}.zip"',
                "Cache-Control": "no-store"})
        except ExchangeRefused as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @app.get("/api/resources/{eid}/masks.npz")
    async def resource_masks(eid: str, day: str | None = None) -> Response:
        if not _day_ok(day):
            return JSONResponse({"error": "day is a session id"}, status_code=400)
        path = await asyncio.to_thread(built.hub.resources.masks, eid, day)
        if path is None:
            return JSONResponse({"error": "no saved masks for that id"}, status_code=404)
        data = await asyncio.to_thread(path.read_bytes)
        return Response(data, media_type="application/octet-stream",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/picture/{key}")
    async def picture(key: str) -> Response:
        jpeg = built.magnifier.pictures.get(key)
        if jpeg is None:
            return JSONResponse({"error": "no such picture"}, status_code=404)
        return Response(jpeg, media_type="image/png" if jpeg.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/slidemap.jpg")
    async def slidemap(epoch: int | None = None, x: int | None = None, y: int | None = None, w: int | None = None,
                       h: int | None = None) -> Response:
        if built.slidemap is None:
            return JSONResponse({"error": "no slide map"}, status_code=404)
        rect = (x, y, w, h) if None not in (x, y, w, h) else None  # as the slidemap message named it
        jpeg = await asyncio.get_running_loop().run_in_executor(None, built.slidemap.jpeg, epoch, rect)
        if jpeg is None:
            return JSONResponse({"error": "nothing on the map yet"}, status_code=404)
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        from .origin import socket_kind
        kind = socket_kind(websocket.headers, websocket.query_params, built.paths.home)  # ADR 0026: no Origin alone
        if kind is None:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        client = Client(websocket)
        client.kind = kind  # "studio" or "script": the door's status counts only the studio
        snapshot = built.snapshot(client)  # taken before joining, so nothing arrives twice
        hub.clients.add(client)
        sender = None
        try:
            # The first snapshot can also be interrupted by a disconnect or cancellation.
            # Registration must have the same lifetime as the socket, even before its sender starts.
            for message in snapshot:
                await websocket.send_text(json.dumps(message))
            sender = asyncio.create_task(_send_loop(client))
            # Every session (the header chip's menu, the sessions rail), its journals read off the event loop.
            asyncio.create_task(_send_sessions(built, client))
            while True:
                command = json.loads(await websocket.receive_text())
                asyncio.create_task(handle(built, client, command))
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(client)
            if not hub.clients:
                hub.view_held = False  # nobody covers the live view any more (commands.py `view`)
            built.workspace_transport.gone(client)
            if sender is not None:
                sender.cancel()

    if STUDIO_DIST.exists():
        app.mount("/", StaticFiles(directory=STUDIO_DIST, html=True), name="studio")
    return app


async def _send_sessions(built: App, client: Client) -> None:
    try:
        client.offer(json.dumps(await built.sessions_message()))
    except Exception:
        pass  # the studio reads GET /api/sessions when its menu or rail opens


async def _send_loop(client: Client) -> None:
    while True:
        message = await client.next_message()
        try:
            if isinstance(message, bytes):
                await client.websocket.send_bytes(message)
            else:
                await client.websocket.send_text(message)
        except Exception:
            return


def main() -> None:
    parser = argparse.ArgumentParser(description="HookeUI orchestrator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--bridge-url", default="http://127.0.0.1:8765")
    parser.add_argument("--no-gpu", action="store_true")
    args = parser.parse_args()
    server = create_app(args.bridge_url, use_gpu=not args.no_gpu)
    # Where researchers read the session's index (their session tool; resources.py, Amendment 2.7): its researchers'
    # view, which reads as it did before ticket 66 (RESEARCHER_VIEW in create_app).
    server.state.app.research.resources_url = f"http://127.0.0.1:{args.port}/for-researchers"
    # No per-message compression on the WebSocket: it ran on the event loop for every frame, and a camera JPEG does not
    # compress (live 2026-10-11 before 11:22: 123 of 186 loop stalls were in it). MICROMIND_WS_DEFLATE=1 turns it on.
    deflate = os.environ.get("MICROMIND_WS_DEFLATE", "0") == "1"
    uvicorn.run(server, host=args.host, port=args.port, log_level="warning", ws_per_message_deflate=deflate)


if __name__ == "__main__":
    main()
