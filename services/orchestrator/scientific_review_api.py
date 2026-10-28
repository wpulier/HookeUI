"""Manual Studio access to an owned scientific review of one saved document window."""
import asyncio
import json

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from env_worker.scientific_console import SOURCE_BYTES

from .capture import Refused as MemoryRefused, captures
from .jobs import MemoryRefused as JobMemoryRefused
from .day.sid import is_sid
from .scientific_workspace import ReviewOrigin
from .scientific_history import HistoryUnavailable
from .scientific_host import ChangeRefused, ControlFailed, InputRefused, ToolFailed
from .scientific_saves import SaveConflict, SaveUnavailable, SaveNotStarted
from .workspace_observation import ObservationError

MAX_REQUEST = 128 << 10
# Source has its own bound; escaped UTF-8 JSON can require up to six bytes per
# source byte. Other scientific interactions retain their original allowance.
MAX_SCRIPT_REQUEST = 6 * SOURCE_BYTES + 4096
HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
           'Cross-Origin-Resource-Policy': 'same-origin'}


def refuse(message):
    raise ObservationError('SCIENTIFIC_REVIEW_UNAVAILABLE', message)


def pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            refuse('Send each scientific interaction field only once')
        result[key] = value
    return result


async def body(request, fields, optional=(), *, limit=None):
    limit = MAX_REQUEST if limit is None else limit
    data = bytearray()
    async for part in request.stream():
        if len(data) + len(part) > limit:
            refuse('This scientific interaction is too large; reduce it before retrying')
        data.extend(part)
    try:
        value = json.loads(data, object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError):
        refuse('Send a valid scientific interaction from its workspace window')
    if type(value) is not dict or (not set(fields) <= set(value) or not set(value) <= set(fields) | set(optional)):
        refuse('This scientific interaction has missing or unsupported fields')
    return value


def error_response(error):
    next_step = 'Reopen the saved scientific document and retry from its window.'
    if isinstance(error, ObservationError):
        status, code, says = 409, error.code, str(error)
        if code == 'CLIENT_MISMATCH': status = 403
    elif isinstance(error, (MemoryRefused, JobMemoryRefused)):
        status, code, says = 503, 'SCIENTIFIC_MEMORY', str(error)
        next_step = ('Choose Review running sessions to inspect other work, or wait for it to finish. '
                     'Save any unsaved changes before choosing Stop on a session you no longer need. '
                     'Saved Project data stays; no other session was stopped. ')
        next_step += ('Then choose Open session again.' if isinstance(error, JobMemoryRefused) else
                      'Refresh preview to inspect the completed work. Do not rerun code or a plugin action.')
    elif isinstance(error, InputRefused):
        status, code, says = 422, 'SCIENTIFIC_INPUT_REFUSED', str(error)
        next_step = error.next_step
    elif isinstance(error, ToolFailed):
        status, code, says = 422, 'SCIENTIFIC_TOOL_FAILED', str(error)
        next_step = 'Saved work is unchanged. Choose an input it accepts or another tool; its full error is in this Mac’s logs.'
    elif isinstance(error, ChangeRefused):
        status, code, says = 409, 'SCIENTIFIC_CHANGE_NOT_APPLIED', str(error)
        next_step = 'Choose Check current view, review the fresh preview, then correct your request. Your private variables and unsaved work are kept.'
    elif isinstance(error, ControlFailed):
        status, code, says = 409, 'SCIENTIFIC_CONTROL_FAILED', str(error)
        next_step = 'Your private work may have changed. Choose Check current view, inspect the current settings and correct the tool before another action. The action was not run again; private variables and unsaved work are kept.'
    elif isinstance(error, SaveConflict):
        status, code, says = 409, 'SCIENTIFIC_SAVE_CONFLICT', str(error)
        next_step = 'Choose Check array save for the recovery action, or inspect saved Project Data. Your private edits remain until Stop; saved revisions are preserved.'
    elif isinstance(error, SaveNotStarted):
        status, code, says = 409, 'SCIENTIFIC_SAVE_NOT_STARTED', str(error)
        next_step = 'Correct the declared layer or input in this private session, then save again. Other private changes and accepted revisions are preserved.'
    elif isinstance(error, SaveUnavailable):
        status, code, says = 503, 'SCIENTIFIC_SAVE_UNCONFIRMED', str(error)
        next_step = 'Inspect the array save in Scientific sessions. Retry saving these changes to publish the same staged data, or open its accepted revision. Do not rerun Python or the tool.'
    elif isinstance(error, HistoryUnavailable):
        status, code, says = 503, 'SCIENTIFIC_HISTORY_UNCONFIRMED', str(error)
        next_step = 'Inspect saved Python history in Project Data. If saving failed and this session is still open, choose Retry saving Python history. Do not automatically rerun the code.'
    else:
        status, code, says = 400, 'SCIENTIFIC_REVIEW_FAILED', str(error)
    return JSONResponse({'ok': False, 'code': code, 'error': says[:4096],
                         'next': next_step},
                        status_code=status, headers=HEADERS)


class FrameResponse(Response):
    def __init__(self, delivery):
        super().__init__(delivery.pixels, media_type='application/octet-stream', headers=HEADERS)
        self.delivery = delivery

    async def __call__(self, scope, receive, send):
        async def checked_send(message):
            await self.delivery.check()
            await send(message)
        try:
            await super().__call__(scope, receive, checked_send)
        finally:
            self.body = b''
            self.delivery.release()


def register(app, built, studio_origin, header):
    manager = built.scientific_reviews

    def client(request):
        origin = request.headers.get('origin')
        same_origin_get = request.method == 'GET' and origin is None and request.headers.get('sec-fetch-site') == 'same-origin'
        if not (request.headers.get(header) == 'scientific-review'
                and (same_origin_get or studio_origin(origin, request.url.port))):
            raise ObservationError('CLIENT_MISMATCH', 'Open scientific tools from their Studio window')
        return built.workspace_transport.voice_client(request.headers.get('X-Micromind-Workspace-Grant'))

    async def admitted_origin(request, socket, *, opening=False):
        fields = await body(request, ('session', 'document', 'revision', 'item'), ('defaultView', 'recoveryKey') if opening else ())
        if 'recoveryKey' in fields and type(fields['recoveryKey']) is not str:
            refuse('The private session recovery key is invalid')
        if type(fields.get('defaultView', False)) is not bool:
            refuse('Choose whether to start with the integration’s default view')
        if (type(fields['session']) is not str or not is_sid(fields['session'])
                or fields['session'] != built.day.sid or type(fields['revision']) is not int
                or fields['revision'] < 1 or any(type(fields[key]) is not str or not 0 < len(fields[key]) <= 256
                                               for key in ('document', 'item'))):
            refuse('Choose the exact saved document in the current Project')
        snapshot = built.hub.screen.pin(socket, request_id='open-scientific-review')
        saved = await asyncio.to_thread(manager.store.read, fields['document'], fields['session'], revision=fields['revision'])
        layout = await asyncio.to_thread(manager.read_layout, fields['session'])
        origin = ReviewOrigin.admit(built.hub.screen, socket, snapshot, fields['item'], saved, layout)
        return (origin, fields.get('defaultView', False), fields.get('recoveryKey')) if opening else origin

    @app.get('/api/scientific-reviews')
    async def sessions(request: Request):
        try:
            return JSONResponse({'ok': True, 'reviews': manager.sessions(client(request))}, headers=HEADERS)
        except (ObservationError, MemoryRefused, ValueError, OSError, RuntimeError) as error:
            return error_response(error)

    @app.post('/api/scientific-reviews')
    async def open_review(request: Request):
        try:
            socket = client(request)
            origin, default_view, recovery_key = await admitted_origin(request, socket, opening=True)
            identity = await manager.open(origin, default_view=default_view, **({'recovery_key': recovery_key} if recovery_key is not None else {}))
            notice = manager.recovery_notice(socket, identity)
            return JSONResponse({'ok': True, 'review': identity, **({'sessionNotice': notice} if notice else {})}, headers=HEADERS)
        except (ObservationError, MemoryRefused, ValueError, OSError, RuntimeError) as error:
            return error_response(error)

    @app.post('/api/scientific-reviews/{identity}/attach')
    async def attach(identity: str, request: Request):
        try:
            socket = client(request)
            origin = await admitted_origin(request, socket)
            identity = await manager.attach(socket, identity, origin)
            notice = manager.recovery_notice(socket, identity)
            return JSONResponse({'ok': True, 'review': identity, **({'sessionNotice': notice} if notice else {})}, headers=HEADERS)
        except (ObservationError, MemoryRefused, ValueError, OSError, RuntimeError) as error:
            return error_response(error)

    async def interaction(identity: str, request: Request, *, script=False):
        try:
            socket = client(request)
            fields = await body(request, ('request', 'draw'), limit=MAX_SCRIPT_REQUEST if script else MAX_REQUEST)
            if script:
                from env_worker.scientific_console import source
                if type(fields['request']) is not dict or fields['request'].get('op') not in ('python', 'python-resources'):
                    refuse('The script route accepts only explicitly requested Python execution')
                source(fields['request'].get('source'))
            if fields['draw'] is not None and (type(fields['draw']) is not str or not 0 < len(fields['draw']) <= 256):
                refuse('Use the current scientific preview before interacting')
            snapshot = built.hub.screen.pin(socket, request_id='scientific-interaction')
            reply = await manager.request(socket, identity, fields['request'], snapshot=snapshot, draw=fields['draw'])
            return JSONResponse(reply, headers=HEADERS)
        except (ObservationError, MemoryRefused, ValueError, OSError, RuntimeError) as error:
            return error_response(error)

    @app.post('/api/scientific-reviews/{identity}/request')
    async def interact(identity: str, request: Request):
        return await interaction(identity, request)

    @app.post('/api/scientific-reviews/{identity}/script')
    async def script(identity: str, request: Request):
        return await interaction(identity, request, script=True)

    @app.get('/api/scientific-reviews/{identity}/frames/{frame}')
    async def frame(identity: str, frame: str, request: Request):
        delivery = None
        try:
            delivery = await manager.frame(client(request), identity, frame, captures(built.hub).memory)
            response = FrameResponse(delivery)
            delivery = None
            return response
        except (ObservationError, MemoryRefused, ValueError, OSError, RuntimeError) as error:
            return error_response(error)
        finally:
            if delivery is not None: delivery.release()

    @app.get('/api/scientific-reviews/{identity}/exports')
    async def view_exports(identity: str, request: Request):
        from . import scientific_view_export
        try:
            socket = client(request)
            owner = manager._owner(socket, identity)
            await manager._check(owner)
            saved = await asyncio.to_thread(manager.store.read, owner.execution.document,
                owner.execution.project, revision=owner.execution.revision)
            result = await asyncio.to_thread(scientific_view_export.listing, manager.store, saved)
            await manager._check(owner)
            return JSONResponse({'ok': True, **result}, headers=HEADERS)
        except (ObservationError, MemoryRefused, ValueError, OSError, RuntimeError) as error:
            return error_response(error)

    @app.post('/api/scientific-reviews/{identity}/exports')
    async def export_view(identity: str, request: Request):
        import threading
        from contextlib import suppress
        from . import scientific_view_export
        from .scientific_file_api import watch_disconnect
        from .scientific_project_api import _owned, _wire
        stopping, watcher = threading.Event(), None
        try:
            socket = client(request)
            fields = await body(request, ('frame', 'draw', 'request', 'scope', 'region'), ('previous',))
            snapshot = built.hub.screen.pin(socket, request_id='scientific-view-export')
            with captures(built.hub).memory.reserve('analysis', scientific_view_export.WORKING_BYTES, 'retaining scientific view export'):
                saved, frame, pixels = await scientific_view_export.capture(manager, socket, identity,
                    fields['frame'], fields['draw'], snapshot)
                watcher = asyncio.create_task(watch_disconnect(request, stopping))
                exported = await _owned(scientific_view_export.publish, manager.store, saved, frame, pixels,
                    fields['request'], fields['scope'], fields['region'], previous=fields.get('previous'),
                    stopping=stopping.is_set, on_cancel=stopping.set)
            return JSONResponse({'ok': True, 'project': _wire(exported), 'sessionChanged': saved.session != built.day.sid}, headers=HEADERS)
        except (ObservationError, MemoryRefused, ValueError, OSError, RuntimeError) as error:
            return error_response(error)
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError): await watcher

    @app.delete('/api/scientific-reviews/{identity}')
    async def stop(identity: str, request: Request):
        try:
            error = await manager.stop(client(request), identity)
            if error is not None: return error_response(error)
            return JSONResponse({'ok': True}, headers=HEADERS)
        except (ObservationError, MemoryRefused, ValueError, OSError, RuntimeError) as error:
            return error_response(error)

    from .scientific_jupyter import JupyterGrants, JupyterRefused
    home = getattr(getattr(built, 'paths', None), 'home', None)
    notebooks = JupyterGrants(manager, home / 'jupyter') if home is not None else None
    built.scientific_notebooks = notebooks
    if notebooks is not None and hasattr(manager, 'ended_hooks'):
        manager.ended_hooks.append(notebooks.forget)

    def granting():
        if notebooks is None:
            raise JupyterRefused('Notebook connections need an application home; this instance has none')
        return notebooks

    @app.post('/api/scientific-reviews/{identity}/jupyter')
    async def connect_notebook(identity: str, request: Request):
        """Grant this session a local Jupyter connection (scientific_jupyter); the token stays on disk."""
        try:
            socket = client(request)
            await body(request, ())
            granted = await asyncio.to_thread(granting().issue, socket, identity, str(request.base_url))
            return JSONResponse({'ok': True, 'notebook': granted}, headers=HEADERS)
        except (ObservationError, JupyterRefused, ValueError, OSError) as error:
            return error_response(error)

    @app.delete('/api/scientific-reviews/{identity}/jupyter')
    async def revoke_notebook(identity: str, request: Request):
        try:
            revoked = await asyncio.to_thread(granting().revoke, client(request), identity)
            return JSONResponse({'ok': True, 'revoked': revoked}, headers=HEADERS)
        except (ObservationError, JupyterRefused, ValueError, OSError) as error:
            return error_response(error)

    @app.post('/api/jupyter/{action}')
    async def notebook_action(action: str, request: Request):
        """A connected Jupyter kernel's cell, interrupt or assistance, run as the Studio console would run it."""
        try:
            # Only the local kernel process calls this; a browser page never may, whatever it presents.
            if request.headers.get('origin') is not None or request.headers.get('sec-fetch-site') is not None:
                raise ObservationError('CLIENT_MISMATCH', 'Notebook actions come from the connected Jupyter kernel, not a web page')
            scheme, _, token = (request.headers.get('authorization') or '').partition(' ')
            if scheme != 'Bearer':
                raise ObservationError('CLIENT_MISMATCH', 'This notebook connection has no Hooke token')
            socket, identity, reference = granting().resolve(token)
            fields = await body(request, ('cell', 'source') if action == 'execute' else ('cell',) if action == 'interrupt'
                                else ('mode', 'expression'), limit=MAX_SCRIPT_REQUEST)
            reply = await manager.request(socket, identity, granting().request(action, fields, reference), notebook=True)
            return JSONResponse(reply, headers=HEADERS)
        except JupyterRefused as error:
            return JSONResponse({'ok': False, 'code': 'NOTEBOOK_CONNECTION', 'error': str(error),
                                 'next': 'Choose Connect a notebook in the Hooke session again.'}, status_code=403, headers=HEADERS)
        except (ObservationError, MemoryRefused, ValueError, OSError, RuntimeError) as error:
            return error_response(error)

    @app.get('/api/scientific-reviews/{identity}/native-panels/{frame}')
    async def native_panel(identity: str, frame: str, request: Request):
        delivery = None
        try:
            delivery = await manager.frame(client(request), identity, frame, captures(built.hub).memory, native=True)
            response = FrameResponse(delivery)
            delivery = None
            return response
        except (ObservationError, MemoryRefused, ValueError, OSError, RuntimeError) as error:
            return error_response(error)
        finally:
            if delivery is not None: delivery.release()
