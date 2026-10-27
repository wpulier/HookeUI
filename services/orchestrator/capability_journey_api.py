"""Trusted Studio HTTP adapters for private coder handoffs and candidate imports."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from gpu_worker import manifests as catalog

from . import bounded_files, capability_import, capability_workspace as workspaces, own_trials, share

MAX_JSON = 16 * 1024


class RequestRefused(ValueError):
    def __init__(self, says: str, status: int = 400):
        super().__init__(says)
        self.status = status


def _session(built, session) -> None:
    if not isinstance(session, str) or not session or session != getattr(built.day, 'sid', None):
        raise RequestRefused('The session changed. Reopen this capability task in the current session.', 409)


async def _work(function, *args, **kwargs):
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.done():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError()
    return result


async def _body(request: Request) -> dict:
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_JSON:
            raise RequestRefused('This request is too large. Shorten the description or source.', 413)
        data.extend(chunk)
    try:
        body = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise RequestRefused('Send a valid capability request from the Studio.') from exc
    if not isinstance(body, dict):
        raise RequestRefused('Send one capability request from the Studio.')
    return body


def _fields(body: dict, allowed: set[str]) -> None:
    if set(body) - allowed:
        raise RequestRefused('This request contains unsupported settings. Reload the Studio and try again.')


def _error(exc: Exception, *, recovery: dict | None = None) -> JSONResponse:
    if isinstance(exc, RequestRefused):
        return JSONResponse({'ok': False, 'says': str(exc), **({'recovery': recovery} if recovery else {})}, status_code=exc.status)
    if isinstance(exc, (share.CantShare, workspaces.WorkspaceError, ValueError)):
        says = str(exc)[:4096]
    else:
        says = 'The capability file or workspace could not be read. Check the file and available disk space, then retry.'
    return JSONResponse({'ok': False, 'says': says, **({'recovery': recovery} if recovery else {})}, status_code=400)


ERRORS = (RequestRefused, share.CantShare, workspaces.WorkspaceError, ValueError, OSError,
          zipfile.BadZipFile, RuntimeError, RecursionError, subprocess.SubprocessError, zlib.error)


def _working_bytes(expanded, archive):
    return 8 * expanded + 2 * archive + (4 << 20)


def _admit(built, *, expanded=share.MAX_TOTAL, archive=share.MAX_TOTAL + (1 << 20)):
    from .capture import Refused, captures
    try:
        return captures(built.hub).memory.reserve(
            'analysis', _working_bytes(expanded, archive), 'preparing a capability package')
    except Refused as exc:
        raise RequestRefused('Not enough analysis memory is available. Let the current work finish, then retry.', 503) from exc


def _grow(room, expanded, archive):
    from .capture import Refused
    try:
        room.grow(max(0, _working_bytes(expanded, archive) - room.nbytes))
    except Refused as exc:
        raise RequestRefused('This package needs more analysis memory than is available. Use a smaller package or free current analysis work, then retry.', 503) from exc


def _workspace_package_allowance(item):
    with workspaces._directory(item.candidate) as fd:
        raw = bounded_files.read_at(fd, catalog.MANIFEST, share.MAX_FILE)
    return share.MAX_TOTAL + sum(wheel.bytes for wheel in share.declared_wheels(raw, share.WHEEL_FORMAT))


def _workspace(item) -> dict:
    return {'ok': True, 'id': item.id, 'folder': str(item.folder), 'candidate': item.candidate_id,
            'instructions': workspaces.instructions(item.id)}


def _package_bytes(id_: str, by: str, max_total: int) -> tuple[bytes, str]:
    path = workspaces.package(id_, by=by, max_total=max_total)
    with workspaces._directory(path.parent) as fd:
        data = bounded_files.read_at(fd, path.name, share.MAX_ARCHIVE)
    return data, path.name


def _reveal(id_: str) -> None:
    item = workspaces.resolve(id_)
    if sys.platform != 'darwin':
        raise RequestRefused(f'Open this folder in your coding tool: {item.folder}')
    subprocess.run(['open', '-R', str(item.folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=10, check=True)


def register(app, built, studio_origin, header: str) -> None:
    def trusted(request: Request, action: str) -> None:
        if not (request.headers.get(header) == action
                and studio_origin(request.headers.get('origin'), request.url.port)):
            raise RequestRefused('Continue this capability task from the Studio.', 403)

    @app.post('/api/capability-inputs')
    async def capability_inputs(request: Request):
        try:
            trusted(request, 'capability-inputs')
            body = await _body(request)
            _fields(body, {'session', 'capability'})
            _session(built, body.get('session'))
            from . import retained_inputs
            from .resources import resources_of
            def read_inputs():
                declaration = None
                kind = None
                if 'capability' in body:
                    from . import kinds
                    key = body['capability']
                    if not isinstance(key, str) or len(key) > 256:
                        raise ValueError('Choose a capability from the Toolbox.')
                    with kinds.read_once():
                        kind = kinds.find(key)
                        if kind is None:
                            raise ValueError('This capability is no longer available. Open its setup again.')
                        declaration = kinds.manifest_of(kind)
                from . import admission
                if declaration is not None and admission.typed(declaration):  # one picker per input (H01 slice d)
                    return [], admission.choices(resources_of(built.hub), body['session'], declaration)
                from . import native_picture_inputs
                if kind is not None and native_picture_inputs.supported(kind):
                    return native_picture_inputs.choices(resources_of(built.hub), body['session']), None
                return retained_inputs.choices(resources_of(built.hub), body['session'], declaration), None
            choices, typed = await asyncio.to_thread(read_inputs)
            _session(built, body['session'])
            return {'ok': True, 'inputs': choices, **({'typed': typed} if typed is not None else {})}
        except ERRORS as exc:
            return JSONResponse({'ok': False, 'error': str(exc)}, status_code=getattr(exc, 'status', 400))

    def review_install(model, generation=None):
        from . import env_runner
        if not isinstance(model, str) or len(model) > 256:
            raise RequestRefused('Choose a package from the Toolbox.')
        owner = catalog.get(model) or catalog.mine().get(model)
        installed = env_runner.in_use(owner) if owner is not None and owner.members else None
        if installed is not None and generation is not None and installed.folder.name != generation:
            from .maintenance import workflow
            installed = workflow.installed(owner, generation)
        if installed is None:
            raise RequestRefused('Complete this package’s installation and reference checks before reviewing it.')
        return installed

    def review_read(model, generation=None):
        from . import bundle_review
        installed = review_install(model, generation)
        result = bundle_review.projection(installed)
        for case in result['cases']:
            for descriptor in case['artifacts'].values():
                data = bundle_review.read(installed, descriptor)
                if descriptor['kind'] == 'json':
                    descriptor['preview'] = data.decode('utf-8')[:4000]
                    descriptor['truncated'] = len(data.decode('utf-8')) > 4000
        return result

    @app.post('/api/capability-review')
    async def capability_review(request: Request):
        try:
            from . import bundle_review
            trusted(request, 'capability-review')
            body = await _body(request)
            _fields(body, {'session', 'action', 'model', 'generation', 'digest'})
            _session(built, body.get('session'))
            if body.get('action') == 'read':
                result = await _work(review_read, body.get('model'), body.get('generation'))
            elif body.get('action') == 'accept':
                result = await _work(bundle_review.accept, body.get('model'), body.get('generation'), body.get('digest'),
                                     still_current=lambda: body['session'] == getattr(built.day, 'sid', None))
                await built.sync_mine()
            else:
                raise RequestRefused('Choose Review results or Enable these tools.')
            _session(built, body['session'])
            return JSONResponse({'ok': True, **result})
        except ERRORS as exc:
            return _error(exc)

    @app.get('/api/capability-review-artifact')
    async def capability_review_artifact(request: Request):
        try:
            from . import bundle_review
            query = dict(request.query_params)
            _fields(query, {'model', 'generation', 'digest', 'case', 'role'})
            def load():
                installed = review_install(query.get('model'), query.get('generation'))
                if installed.folder.name != query.get('generation') or bundle_review.identity(installed) != query.get('digest'):
                    raise RequestRefused('This package’s checks changed. Reopen its result review.', 409)
                case = next((c for c in installed.proof['cases'] if c['case'] == query.get('case')), None)
                role = query.get('role')
                if case is None or role not in ('input', 'actual', 'reference'):
                    raise RequestRefused('Choose a result from this package’s review.')
                descriptor = case['artifacts'][role]
                return bundle_review.read(installed, descriptor), descriptor['kind']
            data, kind = await _work(load)
            return Response(data, media_type='image/png' if kind == 'image' else 'application/json',
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        except ERRORS as exc:
            return _error(exc)

    @app.post('/api/capability-workspaces')
    async def create_workspace(request: Request):
        recovery = None
        try:
            trusted(request, 'capability-workspace')
            body = await _body(request)
            _fields(body, {'session', 'description', 'runner', 'input', 'output', 'source'})
            _session(built, body.get('session'))
            item = await _work(workspaces.create, body.get('description'), **{
                key: body[key] for key in ('runner', 'input', 'output', 'source') if key in body})
            # Creation is profile-owned. A session refusal must not lose its resumable identity.
            recovery = {'session': body['session'], 'workspace': {
                'id': item.id, 'folder': str(item.folder), 'candidate': item.candidate_id}}
            _session(built, body['session'])
            result = await _work(_workspace, item)
            _session(built, body['session'])
            return JSONResponse(result)
        except ERRORS as exc:
            return _error(exc, recovery=recovery)

    @app.post('/api/capability-workspaces/{id}')
    async def workspace_action(id: str, request: Request):
        try:
            trusted(request, 'capability-workspace')
            body = await _body(request)
            _fields(body, {'session', 'action', 'by'})
            _session(built, body.get('session'))
            action = body.get('action')
            if action not in ('read', 'reveal', 'package'):
                raise RequestRefused('Choose Copy instructions, Open folder or Package capability.')
            item = await _work(workspaces.resolve, id)
            _session(built, body['session'])
            if action == 'package':
                by = body.get('by', '')
                if not isinstance(by, str) or len(by.encode()) > 1024:
                    raise RequestRefused('Enter a shorter contributor name.')
                with _admit(built) as room:
                    expanded = await _work(_workspace_package_allowance, item)
                    _grow(room, expanded, expanded + (1 << 20))
                    data, filename = await _work(_package_bytes, id, by, expanded)
                _session(built, body['session'])
                if len(data) > share.MAX_ARCHIVE:
                    raise RequestRefused('The capability package is too large. Remove weights or unrelated files.')
                return Response(data, media_type='application/zip', headers={
                    'Content-Disposition': f'attachment; filename="{filename}"',
                    'Cache-Control': 'no-store'})
            if action == 'reveal':
                await _work(_reveal, id)
                _session(built, body['session'])
            result = await _work(_workspace, item)
            _session(built, body['session'])
            return JSONResponse(result)
        except ERRORS as exc:
            return _error(exc)

    @app.post('/api/capability-import')
    async def import_capability(request: Request):
        recovery = None
        try:
            trusted(request, 'capability-import')
            if set(request.query_params) - {'session', 'trial'}:
                raise RequestRefused('This import contains unsupported settings. Reload the Studio and try again.')
            session = request.query_params.get('session')
            trial = request.query_params.get('trial', '')
            _session(built, session)
            if trial:
                await _work(own_trials.state, trial)
                _session(built, session)
            uploads = catalog.catalog_root() / 'uploads'
            uploads.mkdir(mode=0o700, parents=True, exist_ok=True)
            if uploads.is_symlink():
                raise RequestRefused('The upload folder is a link. Restore its private folder before importing.')
            declared_length = request.headers.get('content-length')
            if declared_length is not None and (not declared_length.isdecimal() or len(declared_length) > 12):
                raise RequestRefused('The package upload length is invalid.', 400)
            maximum = int(declared_length) if declared_length is not None else share.MAX_ARCHIVE
            if maximum > share.MAX_ARCHIVE:
                raise RequestRefused('The capability package is too large.', 413)
            with _admit(built, expanded=0, archive=maximum) as room, tempfile.TemporaryDirectory(prefix='capability-', dir=uploads) as temporary:
                directory = Path(temporary)
                path = directory / ('upload' + share.SUFFIX)
                size = 0
                with path.open('xb') as stream:
                    os.chmod(path, 0o600)
                    async for chunk in request.stream():
                        _session(built, session)
                        size += len(chunk)
                        if size > maximum:
                            raise RequestRefused('The capability package is too large. Weights and runtimes must not be included.', 413)
                        await _work(stream.write, chunk)
                _session(built, session)
                expanded = await _work(share.expanded_size, path)
                room.settle(_working_bytes(0, size))
                _grow(room, expanded, size)
                prepared = await _work(capability_import.prepare, path, directory)
                _session(built, session)
                added = await _work(capability_import.publish, prepared, trial=trial)
                # This is only a reference: permission, proof and comparison remain required.
                recovery = {'session': session, 'candidate': added['model']}
                _session(built, session)
                await built.sync_mine()
                _session(built, session)
                return JSONResponse({'ok': True, **added})
        except ERRORS as exc:
            return _error(exc, recovery=recovery)
