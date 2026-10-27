"""Studio recordings: the studio's own view (outlines, labels, magnifier, results, chat and
the voice) recorded in the browser and saved here as it goes, next to the camera bridge's raw
recording of the same session. Both land in the recordings folder (paths.recordings: assets, kept
like every other recording). Record also collects a dataset; while one is collected, the studio's
recording is kept in it too (`keep`: DatasetCollector.keep_screen, hard-linked as it grows).

The browser records the studio tab with MediaRecorder and sends a chunk every few seconds, so
a reload or a crash loses at most those seconds. When it stops, the file is remuxed (or, for
WebM, converted) to an MP4 that QuickTime plays, keeping the original.
"""
from __future__ import annotations

import asyncio
import subprocess
import re
import json
import uuid
import os
import threading
from datetime import datetime, timezone
import time
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .origin import allowed_origin
from .paths import Paths

MAX_CHUNK = 64 * 1024 * 1024


def recording_label(sidecar: Path) -> str:
    """Read only bounded, owned scope metadata; legacy/unknown files keep their old label."""
    try:
        with sidecar.open() as stream:
            line=stream.readline(4097)
        if len(line)>4096:return 'Screen recording'
        event=json.loads(line)
        if isinstance(event,dict) and event.get('schema')==1 and event.get('event')=='start' and isinstance(event.get('scope'),str):
            return {'annotated':'Annotated view','workspace':'Workspace recording'}.get(event.get('scope'),'Screen recording')
    except (OSError,ValueError,AttributeError):
        pass
    return 'Screen recording'


def add_recording_routes(app: FastAPI, root: Path | None = None,
                         keep: Callable[[Path, float, bool], None] | None = None,
                         made: Callable[[str], None] | None = None,
                         resolve=None, library=None) -> None:
    """`made(id)`: a recording begun, said to the session it was made in (its index id, the file's stem), so Made in
    this session and the sessions list count it."""
    root = Path(root) if root is not None else Paths.from_env().recordings
    active: dict[str, Path] = {}
    started: dict[str, float] = {}
    sequences: dict[str, int] = {}
    clock_domain = str(uuid.uuid4())
    write_lock = threading.Lock()
    io_slots = asyncio.Semaphore(4)

    def write_event(rid: str, kind: str, **facts) -> None:
        with write_lock:
            sequences[rid] = sequences.get(rid, 0) + 1
            record = {'schema': 1, 'event': kind, 'at': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                      'monoNs': time.monotonic_ns(), 'clockDomain': clock_domain, 'sequence': sequences[rid], **facts}
            with (root / f'MicroscopeMind-{rid}.channels.jsonl').open('a') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(json.dumps(record) + '\n')

    async def event(rid: str, kind: str, **facts) -> None:
        async with io_slots:
            await asyncio.to_thread(write_event, rid, kind, **facts)

    def write_chunk(path: Path, data: bytes) -> int:
        with path.open('ab') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
        return path.stat().st_size


    def kept(path: Path, rid: str, finished: bool) -> None:
        if keep is not None:
            try:
                keep(path, started.get(rid, time.time()), finished)
            except Exception as exc:  # the recording is saved whatever happens to the dataset's copy
                print(f"Recording {path.name}: not kept in the dataset: {exc}", flush=True)

    def refuse(request: Request) -> JSONResponse | None:
        if not allowed_origin(request.headers.get("origin")):
            return JSONResponse({"error": "not from the studio"}, status_code=403)
        return None

    @app.post("/api/recordings/{recording}/asset")
    async def recording_asset(recording: str, day: str, request: Request) -> JSONResponse:
        """Resolve an exact owned recording into the existing Library preview/input path."""
        if (no := refuse(request)) is not None:
            return no
        if not re.fullmatch(r"MicroscopeMind-\d{8}-\d{6}(?:-[a-f0-9]{12})?", recording) or resolve is None or library is None:
            return JSONResponse({'error': 'This recording is unavailable.'}, status_code=404)

        def locate():
            entry = resolve(recording, day)
            if entry is None or entry.kind != 'recording' or entry.day != day:
                return None
            if recording.removeprefix('MicroscopeMind-') in active or entry.state == 'recording':
                return None
            # WebM is always the original when present; its MP4 conversion may still be writing.
            # Otherwise prefer native MP4 over its optional QuickTime remux.
            path = next((root / name for name in (f'{recording}.webm', f'{recording}.mp4', f'{recording}-play.mp4')
                         if (root / name).is_file() and not (root / name).is_symlink()
                         and (root / name).resolve().parent == root.resolve()
                         and (root / name).stat().st_size > 0), None)
            if path is None:
                return None
            aid = library.register(f'recording:{recording}', path, title=entry.title, at=entry.at,
                                   origin=entry.day, group='record')
            asset = library.get(aid)
            if asset is None or asset.path.resolve() != path.resolve() or asset.origin != day:
                return None
            return asset.metadata()

        asset = await asyncio.to_thread(locate)
        if asset is None:
            return JSONResponse({'error': 'This recording is unavailable or still saving. Retry from its original Project.'}, status_code=404)
        return JSONResponse({'asset': asset}, headers={'Cache-Control': 'no-store'})

    @app.post("/api/recordings/start")
    async def start(request: Request) -> JSONResponse:
        if (no := refuse(request)) is not None:
            return no
        body = await request.json()
        scope=body.get('scope','workspace') if isinstance(body,dict) else None
        if scope not in ('workspace','annotated'):
            return JSONResponse({'error':'invalid recording scope'},status_code=400)
        ext = "mp4" if body.get("ext") == "mp4" else "webm"
        root.mkdir(parents=True, exist_ok=True)
        rid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:12]
        path = root / f"MicroscopeMind-{rid}.{ext}"
        await asyncio.to_thread(write_chunk, path, b"")
        active[rid] = path
        started[rid] = time.time()
        await event(rid, 'start', scope=scope, channels={name: 'unavailable' for name in ('screen', 'tabAudio', 'microphone', 'camera')},
              note='Channel availability requires explicit observations; no channel is inferred from Record.')
        kept(path, rid, finished=False)
        if made is not None:
            try:
                made(f"MicroscopeMind-{rid}")
            except Exception:
                pass  # the recording goes on; only its line in the session's list is missing
        return JSONResponse({"id": rid, "path": str(path), "folder": str(root)})

    @app.post('/api/recordings/{rid}/channels')
    async def channels(rid: str, request: Request) -> JSONResponse:
        if (no := refuse(request)) is not None:
            return no
        if rid not in active:
            return JSONResponse({'error': 'no active recording'}, status_code=404)
        if len(await request.body()) > 4096:
            return JSONResponse({'error': 'channel event too large'}, status_code=413)
        body = await request.json()
        names = ('screen', 'tabAudio', 'microphone', 'camera', 'upload')
        states = ('available', 'unavailable', 'muted', 'ended', 'requested', 'captured', 'failed')
        if not isinstance(body, dict) or body.get('channel') not in names or body.get('state') not in states:
            return JSONResponse({'error': 'invalid channel observation'}, status_code=400)
        # Observed track/upload facts only: never persist browser grants, device labels or arbitrary transport data.
        await event(rid, 'channel', channel=body['channel'], state=body['state'],
                    **({'historyGap': True} if body.get('historyGap') is True else {}))
        return JSONResponse({'ok': True})

    @app.post("/api/recordings/{rid}/append")
    async def append(rid: str, request: Request) -> JSONResponse:
        if (no := refuse(request)) is not None:
            return no
        path = active.get(rid)
        if path is None:
            return JSONResponse({"error": "no such recording"}, status_code=404)
        data = await request.body()
        if len(data) > MAX_CHUNK:
            return JSONResponse({"error": "chunk too large"}, status_code=413)
        try:
            async with io_slots:
                size = await asyncio.to_thread(write_chunk, path, data)
        except OSError:
            await event(rid, 'channel', channel='upload', state='failed')
            raise
        await event(rid, 'chunk', bytes=len(data))
        return JSONResponse({"ok": True, "bytes": size})

    @app.post("/api/recordings/{rid}/stop")
    async def stop(rid: str, request: Request) -> JSONResponse:
        if (no := refuse(request)) is not None:
            return no
        history_incomplete = False
        if request.headers.get('content-type', '').startswith('application/json'):
            if len(await request.body()) > 4096:
                return JSONResponse({'error': 'stop event too large'}, status_code=413)
            try:
                body = await request.json()
            except ValueError:
                return JSONResponse({'error': 'invalid stop event'}, status_code=400)
            history_incomplete = isinstance(body, dict) and body.get('channelHistoryIncomplete') is True
        path = active.pop(rid, None)
        if path is None or not re.fullmatch(r"\d{8}-\d{6}-[a-f0-9]{12}", rid):
            return JSONResponse({"error": "no such recording"}, status_code=404)
        await event(rid, 'stop', bytes=path.stat().st_size, channelHistoryIncomplete=history_incomplete)
        sequences.pop(rid, None)
        if path.stat().st_size == 0:
            path.unlink(missing_ok=True)
            return JSONResponse({"ok": False, "error": "nothing was recorded"})
        kept(path, rid, finished=True)
        playable = path.with_name(path.stem + "-play.mp4") if path.suffix == ".mp4" else path.with_suffix(".mp4")
        # A clean MP4 for QuickTime, made in the background (the original is kept).
        asyncio.create_task(_make_playable(path, playable))
        return JSONResponse({"ok": True, "path": str(path), "playable": str(playable), "bytes": path.stat().st_size,
                             "folder": str(root)})


def _h264() -> list[str]:
    """libx264 where this FFmpeg has it (a GPL build such as Homebrew's), else Apple's VideoToolbox, which an LGPL
    build has (the Mac app's, packaging/macos)."""
    try:
        listed = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        listed = ""
    if " libx264 " in listed or " h264_videotoolbox " not in listed:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    return ["-c:v", "h264_videotoolbox", "-q:v", "65"]


async def _make_playable(source: Path, target: Path) -> None:
    if source.suffix == ".mp4":
        args = ["-c", "copy"]  # browser MP4 is fragmented: remux into one tidy file
    else:
        args = [*await asyncio.to_thread(_h264), "-pix_fmt", "yuv420p", "-c:a", "aac"]
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), *args,
            "-movflags", "+faststart", str(target), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await process.communicate()
        if process.returncode != 0:
            print(f"Recording {source.name}: could not make an MP4: {err.decode(errors='replace')[-300:]}", flush=True)
    except FileNotFoundError:
        print("Recording: ffmpeg not found; kept the original file only", flush=True)
