"""Ordinary Studio access to accepted analyses, graphs and workbook exports.

All paths capture the owning Project and recheck it after off-loop work. Reads and
exports use accepted snapshots; only explicit creation/refresh evaluates settings.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .datatables import DataTableRefused, snapshot_bytes


def add_table_routes(app, built, allowed_origin, header):
    def owner_of(session):
        if not isinstance(session, str) or not session:
            raise DataTableRefused('say which Project owns the saved work', next='open its Project')
        return built.hub.datatables.capture(session)

    def refused(exc):
        return JSONResponse({'error': str(exc), 'next': exc.next}, status_code=exc.status,
                            headers={'Cache-Control': 'no-store'})

    async def body_of(request):
        if not allowed_origin(request.headers.get('origin')) or request.headers.get(header) != 'datatable':
            raise DataTableRefused('not from here', status=403)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 64 * 1024:
                raise DataTableRefused('too many settings', next='use a smaller explicit selection', status=413)
        try:
            value = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            value = None
        if not isinstance(value, dict):
            raise DataTableRefused('settings must be an object', next='review the settings')
        return value

    async def result(record, owner):
        service = built.hub.table_derivatives
        snapshot = await asyncio.to_thread(service.read, record['id'], record['version'], owner=owner)
        owner.check(built)
        body = await asyncio.to_thread(snapshot_bytes, service.wire(record, snapshot))
        owner.check(built)
        return Response(body, media_type='application/json', headers={'Cache-Control': 'no-store'})

    @app.post('/api/datatables/comparison')
    async def comparison_table(request: Request):
        try:
            body = await body_of(request)
            owner = owner_of(body.get('session'))
            service = built.hub.datatables
            record = await service.from_comparison(body.get('tab'), body.get('layoutRevision'),
                owner=owner, title=body.get('title') or 'Comparison measurements')
            snapshot = await asyncio.to_thread(service.read, record['id'], record['version'], owner=owner)
            owner.check(built)
            data = await asyncio.to_thread(snapshot_bytes, service.wire(record, snapshot))
            owner.check(built)
            return Response(data, media_type='application/json', headers={'Cache-Control': 'no-store'})
        except DataTableRefused as exc:
            return refused(exc)

    @app.post('/api/table-derivatives')
    async def create(request: Request):
        try:
            body = await body_of(request)
            owner = owner_of(body.get('session'))
            if body.get('kind') not in ('analysis', 'graph') or body.get('mode', 'follow') not in ('follow', 'frozen'):
                raise DataTableRefused('choose analysis or graph, following or frozen', next='review the settings')
            table = body.get('table')
            if not isinstance(table, dict) or not isinstance(table.get('id'), str) or type(table.get('version')) is not int:
                raise DataTableRefused('choose an exact saved table version', next='save and select the table')
            record = await built.hub.table_derivatives.create(body.get('kind'), table['id'], table['version'],
                body.get('spec'), title=body.get('title') or '', mode=body.get('mode') or 'follow', owner=owner)
            return await result(record, owner)
        except DataTableRefused as exc:
            return refused(exc)

    @app.get('/api/table-derivatives/{rid}')
    async def read(rid: str, session: str | None = None, version: int | None = None):
        try:
            owner = owner_of(session)
            return await result(built.hub.table_derivatives.version(rid, version), owner)
        except DataTableRefused as exc:
            return refused(exc)

    @app.patch('/api/table-derivatives/{rid}')
    async def edit(rid: str, request: Request):
        try:
            body = await body_of(request)
            owner = owner_of(body.get('session'))
            version = body.get('version')
            if type(version) is not int or version < 1:
                raise DataTableRefused('say which saved version is being changed', next='open its exact version')
            service = built.hub.table_derivatives
            if body.get('update') is True and 'mode' not in body:
                record = await service.refresh(rid, version, owner=owner)
            elif 'mode' in body and 'update' not in body:
                if body['mode'] not in ('follow', 'frozen'):
                    raise DataTableRefused('choose Freeze or Follow', next='review the update setting')
                record = await service.set_mode(rid, version, body['mode'], owner=owner)
            else:
                raise DataTableRefused('choose Update, Freeze or Follow', next='use one action at a time')
            return await result(record, owner)
        except DataTableRefused as exc:
            return refused(exc)

    @app.get('/api/datatables/{tid}.xlsx')
    async def workbook(tid: str, session: str | None = None, version: int | None = None):
        from .xlsx import ExportRefused, export_table
        try:
            owner = owner_of(session)
            tables, service = built.hub.datatables, built.hub.table_derivatives
            if type(version) is not int or version < 1:
                raise DataTableRefused('export an exact saved table version', next='select the saved table')
            record = tables.version(tid, version)
            source = {'session': owner.session, 'id': tid, 'version': version, 'sha256': record['snapshot']['sha256']}
            # Capture accepted records before awaiting; later auto-refresh cannot retarget this export.
            records = [matched for history in tables.day.table_derivatives.values()
                       if (matched := next((r for r in reversed(history)
                                            if r['kind'] == 'analysis' and r['source'] == source), None)) is not None]
            def encode():
                snapshot = tables.read(tid, version, owner=owner)
                analyses = [(r, service.read(r['id'], r['version'], owner=owner)) for r in records]
                return export_table(snapshot, record, session=owner.session, analyses=analyses)
            data = await asyncio.to_thread(encode)
            owner.check(built)
            return Response(data, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                         'Content-Disposition': f'attachment; filename="table_{tid}_v{version}.xlsx"'})
        except DataTableRefused as exc:
            return refused(exc)
        except ExportRefused as exc:
            return refused(DataTableRefused(str(exc), next='review the saved table and export limits', status=409))
