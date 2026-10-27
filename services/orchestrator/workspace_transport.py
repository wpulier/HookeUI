"""Accepted socket routing and native response lifetime for the one workspace owner.

ClientRef truth remains in App/Screen. These opaque grants route HTTP back to
that same socket; they neither resolve a source address nor admit a tool.
"""
from __future__ import annotations

import asyncio
import json
import hashlib
import time
from uuid import uuid4
from dataclasses import dataclass
from secrets import token_urlsafe

from fastapi.responses import JSONResponse, Response

from .capture import RUN
from .presentations import PresentationError
from .workspace_observation import ClientRef, ObservationError


@dataclass(frozen=True)
class _Connection:
    ref: ClientRef
    grant: str
    request_id: str
    restoration: str | None = None


class _NativeResponse(Response):
    def __init__(self, data, lease, check):
        super().__init__(data, media_type=lease.to_wire()['mediaType'], headers={
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Cross-Origin-Resource-Policy': 'same-origin', **getattr(lease, 'headers', {})})
        self._lease, self._check = lease, check

    async def __call__(self, scope, receive, send):
        async def checked_send(message):
            self._check()  # retirement before/during transmission cannot reuse a grant
            await send(message)
        try:
            await super().__call__(scope, receive, checked_send)
        finally:
            self.body = b''
            self._lease.release()


class WorkspaceTransport:
    def __init__(self, app):
        self.app = app
        self._connections = {}
        self._grants = {}
        self._restoring = set()
        self._saved = {}

    def _context(self, socket):
        return self.app.workspace_context(socket, request_id='server:source', owner='server:source')

    def _active(self, grant):
        socket = self._grants.get(grant) if isinstance(grant, str) else None
        row = self._connections.get(socket)
        if socket is None or row is None or row.grant != grant or socket not in self.app.hub.clients:
            raise ObservationError('CLIENT_MISMATCH', 'the originating workspace connection is unavailable')
        context = self._context(socket)
        if (context.workspace_id, context.client_id, context.client_epoch, context.connection_epoch) != (
                row.ref.workspace_id, row.ref.client_id, row.ref.page_epoch, row.ref.connection_epoch):
            raise ObservationError('CLIENT_MISMATCH', 'the originating workspace connection changed')
        return context

    def voice_client(self, grant):
        """Bind a separate audio socket to one accepted Studio connection."""
        self._active(grant)
        return self._grants[grant]

    def hello(self, socket, message):
        allowed = {'type', 'schema', 'requestId', 'workspaceId', 'clientId', 'pageEpoch'}
        try:
            if socket not in self.app.hub.clients:
                raise ObservationError('CLIENT_MISMATCH', 'the originating socket is no longer connected')
            if (not isinstance(message, dict) or set(message) not in (allowed, allowed | {'restoration'})
                    or message.get('type') != 'workspace-hello'
                    or message.get('schema') != 'workspace-origin-v2'
                    or ('restoration' in message and message['restoration'] != 'project-layout')):
                raise ObservationError('INVALID_REPORT', 'workspace acceptance needs the closed origin proposal')
            values = [message[key] for key in ('workspaceId', 'clientId', 'pageEpoch', 'requestId')]
            if any(not isinstance(value, str) or not value or len(value) > 256 for value in values):
                raise ObservationError('INVALID_REPORT', 'workspace origin identifiers must be nonempty and bounded')
            old = self._connections.get(socket)
            if old is not None:
                if (values != [old.ref.workspace_id, old.ref.client_id, old.ref.page_epoch, old.request_id]
                        or message.get('restoration') != old.restoration):
                    raise ObservationError('CLIENT_MISMATCH', 'this socket already accepted a different origin proposal')
                self._active(old.grant)  # replay cannot resurrect a retired socket
                row = old
            else:
                if len(self._connections) >= 32:
                    raise ObservationError('CLIENT_MISMATCH', 'workspace connection capacity reached')
                ref = ClientRef(*values[:3], token_urlsafe(24))
                self.app.accept_workspace(socket, ref)
                row = _Connection(ref, token_urlsafe(32), values[3], message.get('restoration'))
                if row.restoration == 'project-layout':
                    # Project arrangements own reopening. Retire only this socket's
                    # old native presentations, never retained results or jobs.
                    self.app.services.presentations.reset(ref.workspace_id)
                self._connections[socket] = row
                self._grants[row.grant] = socket
                # App is the authority for reconnect retirement. Retain the socket's
                # closed proposal until disconnect, but revoke its delivery grant.
                for grant in tuple(self._grants):
                    try:
                        self._active(grant)
                    except ObservationError:
                        self._grants.pop(grant, None)
            return {'type': 'workspace-ready', 'schema': 'workspace-origin-v2', 'ok': True,
                    'requestId': row.request_id, 'client': row.ref.wire(),
                    'appRunId': RUN, 'connectionGrant': row.grant,
                    **({'restoration': row.restoration} if row.restoration else {})}
        except ObservationError as exc:
            return {'ok': False, 'code': exc.code, 'error': str(exc)}

    def gone(self, socket):
        self.save(socket, force=True)
        row = self._connections.pop(socket, None)
        if row is not None:
            self._grants.pop(row.grant, None)
        self.app.hub.screen.gone(socket)

    def begin_restore(self, socket):
        row = self._connections.get(socket)
        if row is None or row.restoration == 'project-layout':
            return
        if socket not in self._restoring:
            self._restoring.add(socket)
            asyncio.create_task(self._restore(socket))

    def _path(self, workspace):
        # Workspace IDs are browser input, never filesystem paths.
        key = hashlib.sha256(workspace.encode()).hexdigest()
        return self.app.paths.sessions / self.app.day.sid / 'workspaces' / (key + '.json')

    def save(self, socket, *, force=False):
        row = self._connections.get(socket)
        if row is None or row.restoration == 'project-layout' or socket in self._restoring:
            return
        try:
            self._active(row.grant)
            snapshot = self.app.services.presentations.snapshot(row.ref.workspace_id)
            encoded = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':'))
            previous, at = self._saved.get(row.ref.workspace_id, (None, 0))
            if encoded == previous or not force and time.monotonic() - at < 2:
                return
            path = self._path(row.ref.workspace_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(encoded)
            temporary.replace(path)
            self._saved[row.ref.workspace_id] = encoded, time.monotonic()
        except (OSError, ObservationError, PresentationError):
            return  # Saving preferences never interrupts camera intake or a turn.

    def save_all(self):
        for socket in tuple(self._connections):
            self.save(socket, force=True)

    async def _restore(self, socket):
        """Reopen the same saved sources with new connection-scoped delivery leases.

        This is the reconnect lifecycle, not a model turn or a replay of old tools.
        Old live bindings are never attached to a new camera run.
        """
        from . import ticket as tickets
        from .brain.registry import ActionOccurrence
        from .presentations import SourceRef
        from .presentation_wire import command
        core, store = self.app.services.presentations, self.app.services.prepared_sources
        leases, request = {}, None
        try:
            row = self._connections[socket]
            original = core.snapshot(row.ref.workspace_id)
            snapshot = original
            if not snapshot['items']:
                path = self._path(row.ref.workspace_id)
                if path.is_file() and path.stat().st_size < 262144:
                    snapshot = json.loads(path.read_text())
            if not snapshot.get('items'):
                return
            # Wait for the new browser owners to exist, without blocking hello.
            for _ in range(120):
                self._active(row.grant)
                report = self.app.hub.screen.completion_report(socket)
                if report is not None:
                    break
                await asyncio.sleep(.025)
            else:
                return
            # A newly acted-on workspace must not be overwritten by restoration.
            if report.get('items') and any(i['id'].startswith('w-') for i in report['items']):
                return
            request = tickets.issue(self.app.hub, 'command', asked=self.app.day.here(), channel='studio')
            occurrence = ActionOccurrence(request.admission.request_id, str(uuid4()))
            ctx = self.app.workspace_context(socket, request_id=occurrence.request_id, owner=occurrence.action_id)
            with tickets.using(request):
                kept = []
                for item in snapshot['items']:
                    try:
                        ref = SourceRef.from_wire(item['source'])
                        if ref.kind == 'live' and ref.identity.get('run') != RUN:
                            continue
                        leases[item['id']] = await store.prepare(ref, ctx)
                        kept.append({**item, 'source': leases[item['id']].source.to_wire()})
                    except PresentationError:
                        continue  # One unavailable source cannot hide the other retained work.
                if core.snapshot(row.ref.workspace_id) != original:
                    return  # A new user action owns the workspace now.
                core.restore_snapshot({**snapshot, 'items': kept}, ctx, reconnect=True)
                for item in kept:
                    self._active(row.grant)
                    effect = core.reconnect_effect(ctx, item['id'])
                    now = time.monotonic()
                    packet = command(effect, op='open', states=(core.get(ctx.workspace_id, item['id']),),
                        deliveries=leases, before={}, occurrence=occurrence,
                        after_report_seq=report['seq'], issued_mono=now, deadline_mono=now + 5)
                    socket.offer(json.dumps(packet))
        except (OSError, ValueError, KeyError, ObservationError, PresentationError) as exc:
            self.app.agent.log({'kind': 'workspace-restore-failed', 'error': str(exc),
                                'code': getattr(exc, 'code', type(exc).__name__)})
        finally:
            for lease in leases.values():
                lease.release()
            if request is not None:
                request.close()
            self._restoring.discard(socket)

    def image(self, token, grant):
        lease = None
        try:
            context = self._active(grant)
            store = self.app.services.prepared_sources
            lease = store.borrow(token, context)
            data = store.get(token, context)
            response = _NativeResponse(data, lease, lambda: self._active(grant))
            lease = None  # the actual ASGI response now owns this handle
            return response
        except ObservationError as exc:
            return JSONResponse({'ok': False, 'code': exc.code, 'error': str(exc)}, status_code=403,
                                headers={'Cache-Control': 'no-store'})
        except PresentationError as exc:
            return JSONResponse({'ok': False, 'code': exc.code, 'error': str(exc)},
                                status_code=403 if exc.code == 'context_mismatch' else 404,
                                headers={'Cache-Control': 'no-store'})
        finally:
            if lease is not None:
                lease.release()

    async def delivery_image(self, token, grant):
        """Retained bytes stay synchronous; live CPU encoding never blocks this loop."""
        lease = snapshot = None
        try:
            context = self._active(grant)
            store = self.app.services.prepared_sources
            lease = store.borrow(token, context)
            live = lease.source.kind == 'live'
            lease.release()
            lease = None
            if not live:
                return self.image(token, grant)
            snapshot = await store.snapshot(token, context)

            def check(original=snapshot):
                self._active(grant)
                original.check()

            check()
            response = _NativeResponse(snapshot.raw, snapshot, check)
            snapshot = None  # actual ASGI response owns its source and encoded bytes
            return response
        except ObservationError as exc:
            return JSONResponse({'ok': False, 'code': exc.code, 'error': str(exc)}, status_code=403,
                                headers={'Cache-Control': 'no-store'})
        except PresentationError as exc:
            status = 403 if exc.code == 'context_mismatch' else 425 if exc.code == 'source_pending' else 404
            return JSONResponse({'ok': False, 'code': exc.code, 'error': str(exc)}, status_code=status,
                                headers={'Cache-Control': 'no-store'})
        finally:
            if lease is not None:
                lease.release()
            if snapshot is not None:
                snapshot.release()
