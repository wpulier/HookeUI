"""Accepted analyses and graphs over exact saved table snapshots; one owned revision store.

Evaluation is off-loop. Reads, reopen and exports only read hash-checked accepted results.
Follow reacts to table saves; Freeze and explicit refresh retain their visible meaning.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from .datatables import DataTableRefused, DataTables, TableOwner, MAX_SNAPSHOT_BYTES, MAX_VERSIONS, publish_snapshot, snapshot_bytes

KINDS = {'analysis': 'an', 'graph': 'gr'}
MAX_SPEC_BYTES = 32 * 1024


def _spec(value):
    if not isinstance(value, dict):
        raise DataTableRefused('analysis or graph settings must be an object', next='review the settings')
    try:
        body = snapshot_bytes(value)
    except (TypeError, ValueError):
        raise DataTableRefused('settings must contain finite JSON values', next='review the settings') from None
    if len(body) > MAX_SPEC_BYTES:
        raise DataTableRefused('too many analysis or graph settings', next='use a smaller explicit selection')
    return json.loads(body)


def freshness(record, table, *, updating=False, failed=None):
    source = record['source']
    moved = table is None or (table['version'], table['snapshot']['sha256']) != (source['version'], source['sha256'])
    if record['mode'] == 'frozen':
        return {'state': 'frozen', 'words': f"Frozen at table version {source['version']}" + (' · source changed' if moved else '')}
    if updating:
        return {'state': 'updating', 'words': 'Updating from the saved table…'}
    if moved or failed:
        return {'state': 'stale', 'words': f"Stale · {failed or 'table changed'} · Update"}
    return {'state': 'current', 'words': f"Current · table version {source['version']}"}


class TableDerivatives:
    def __init__(self, tables: DataTables, root: Path, evaluators: dict):
        self.tables, self.root, self.evaluators = tables, Path(root), dict(evaluators)
        from .project_artifact_folder import ArtifactFolder
        self._storage = ArtifactFolder(self.root, 'table-derivatives')
        self._locks, self._timers, self._tasks, self._failed = {}, {}, {}, {}
        self._dirty = set()
        self._evaluation = asyncio.Semaphore(2)
        self._loop = None
        self.tables.day.listeners.append(self._changed)

    def root_for(self, session, *, write=False):
        return self._storage.root_for(self.tables.day, session, write=write)

    def path_of(self, record, session, *, write=False):
        from .table_reading import snapshot_path
        path = self.root_for(session, write=write) / snapshot_path(record, session, record['kind'])
        if path.resolve() != path.absolute():
            raise DataTableRefused('the saved analysis or graph path was replaced')
        return path

    def _write(self, record, snapshot, session):
        root = self.root_for(session, write=True)
        def check():
            if self.root_for(session, write=True) != root:
                raise DataTableRefused('the saved analysis or graph destination changed')
        return publish_snapshot(root, record, snapshot, session, check=check)

    def history(self, rid):
        return list(self.tables.day.table_derivatives.get(rid) or [])

    def version(self, rid, version=None):
        history = self.history(rid)
        record = next((r for r in history if r['version'] == version), None) if version is not None else (history[-1] if history else None)
        if record is None:
            raise DataTableRefused('that saved analysis or graph revision is unavailable', next='open its Project', status=404)
        return record

    def _lock(self, rid, owner):
        return self._locks.setdefault(owner.key(rid), asyncio.Lock())

    def read(self, rid, version=None, *, owner=None):
        owner = owner or self.tables.capture()
        owner.check(self.tables.app)
        record = self.version(rid, version)
        try:
            with self.path_of(record, owner.session).open('rb') as stream:
                body = stream.read(MAX_SNAPSHOT_BYTES + 1)
        except OSError:
            raise DataTableRefused('the saved analysis or graph file is unavailable', next='restore its saved file', status=409) from None
        if len(body) > MAX_SNAPSHOT_BYTES or hashlib.sha256(body).hexdigest() != record['snapshot']['sha256']:
            raise DataTableRefused('the saved analysis or graph no longer matches its accepted hash', next='restore its saved file', status=409)
        snapshot = json.loads(body)
        if (snapshot.get('format') != 1 or snapshot.get('kind') != record['kind']
                or snapshot.get('source') != record['source'] or snapshot.get('spec') != record['spec']):
            raise DataTableRefused('the saved result no longer matches its source and settings', next='restore its saved file', status=409)
        owner.check(self.tables.app)
        return snapshot

    async def _evaluate(self, table_id, table_version, spec, kind, owner):
        if type(table_version) is not int or table_version < 1:
            raise DataTableRefused('choose an exact saved table version', next='reopen the table before analyzing or graphing')
        async with self._evaluation:
            owner.check(self.tables.app)
            table = self.tables.version(table_id, table_version)
            source = {'session': owner.session, 'id': table_id, 'version': table_version, 'sha256': table['snapshot']['sha256']}
            snapshot = await asyncio.to_thread(self.tables.read, table_id, table_version, owner=owner)
            owner.check(self.tables.app)
            evaluator = self.evaluators.get(kind)
            if evaluator is None:
                raise DataTableRefused('this analysis or graph evaluator is unavailable', next='use a supported capability')
            arguments = {}
            if kind == 'graph' and spec.get('analysis') is not None:
                reference = spec['analysis']
                if (not isinstance(reference, dict) or set(reference) != {'id', 'version'}
                        or not isinstance(reference['id'], str)
                        or type(reference['version']) is not int or reference['version'] < 1):
                    raise DataTableRefused('choose an exact saved analysis revision', next='select an accepted analysis for this table')
                accepted = self.version(reference['id'], reference['version'])
                if accepted['kind'] != 'analysis' or accepted['source'] != source:
                    raise DataTableRefused('that analysis does not use this exact table revision', next='select an analysis of this saved table version')
                analysis = await asyncio.to_thread(self.read, reference['id'], reference['version'], owner=owner)
                owner.check(self.tables.app)
                arguments['analysis'] = analysis['result']
            work = asyncio.create_task(asyncio.to_thread(evaluator, snapshot, spec, **arguments))
            try:
                result = await asyncio.shield(work)
            except asyncio.CancelledError:
                # Hold the bounded evaluator slot until its thread finishes; no cancelled result publishes.
                try:
                    await work
                except Exception:
                    pass
                raise
            owner.check(self.tables.app)
            return {'format': 1, 'kind': kind, 'source': source, 'spec': spec, 'result': result,
                    **({'comparison': snapshot['comparison']} if snapshot.get('comparison') else {})}

    async def _save(self, record, snapshot, owner):
        owner.check(self.tables.app)
        if record['version'] > MAX_VERSIONS:
            raise DataTableRefused('the saved result reached its revision limit', next='create another analysis or graph')
        writer = asyncio.create_task(asyncio.to_thread(self._write, record, snapshot, owner.session))
        try:
            receipt = await asyncio.shield(writer)
        except asyncio.CancelledError:
            try:
                receipt = await writer
            except Exception:
                pass
            else:
                try:
                    self.path_of({**record,'snapshot':receipt}, owner.session, write=True).unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass
            raise
        try:
            owner.check(self.tables.app)
            saved = owner.store.record_table_derivative({**record, 'session': owner.session, 'snapshot': receipt, 'thread': owner.thread})
        except BaseException:
            try:
                self.path_of({**record,'snapshot':receipt}, owner.session, write=True).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass  # Unaccepted bytes stay for recovery when their original location is unavailable.
            raise
        self._failed.pop(owner.key(record['id']), None)
        if saved['mode'] == 'follow' and self.wire(saved)['freshness']['state'] == 'stale':
            self._schedule(saved['id'], owner)
        return saved

    async def create(self, kind, table_id, table_version, spec, *, title='', mode='follow', owner=None, by='user'):
        self._loop = asyncio.get_running_loop()
        owner = owner or self.tables.capture()
        owner.check(self.tables.app)
        if kind not in KINDS or mode not in ('follow', 'frozen'):
            raise DataTableRefused('choose analysis or graph, following or frozen', next='review the saved result settings')
        spec = await asyncio.to_thread(_spec, spec)
        snapshot = await self._evaluate(table_id, table_version, spec, kind, owner)
        owner.check(self.tables.app)
        rid = owner.store.reserve_table_derivative_id(kind)
        record = {'id': rid, 'kind': kind, 'version': 1, 'title': ' '.join(str(title).split())[:80] or kind.capitalize(),
                  'mode': mode, 'source': snapshot['source'], 'spec': spec, 'by': by, 'change': 'created from saved table'}
        async with self._lock(rid, owner):
            return await self._save(record, snapshot, owner)

    async def refresh(self, rid, version, *, owner=None, by='user'):
        self._loop = asyncio.get_running_loop()
        owner = owner or self.tables.capture()
        async with self._lock(rid, owner):
            owner.check(self.tables.app)
            record = self.version(rid)
            if record['version'] != version:
                raise DataTableRefused('the saved result changed', next='reload its current version', status=409)
            table = self.tables.version(record['source']['id'])
            if (table['version'], table['snapshot']['sha256']) == (record['source']['version'], record['source']['sha256']):
                return record
            snapshot = await self._evaluate(table['id'], table['version'], record['spec'], record['kind'], owner)
            return await self._save({**record, 'version': version + 1, 'source': snapshot['source'], 'by': by,
                                     'change': 'refreshed from saved table'}, snapshot, owner)

    async def set_mode(self, rid, version, mode, *, owner=None):
        owner = owner or self.tables.capture()
        if mode not in ('follow', 'frozen'):
            raise DataTableRefused('choose following or frozen', next='review the update setting')
        async with self._lock(rid, owner):
            owner.check(self.tables.app)
            record = self.version(rid)
            if record['version'] != version:
                raise DataTableRefused('the saved result changed', next='reload its current version', status=409)
            if record['mode'] == mode:
                return record
            snapshot = await asyncio.to_thread(self.read, rid, version, owner=owner)
            return await self._save({**record, 'version': version + 1, 'mode': mode, 'by': 'user',
                                     'change': f'update mode: {mode}'}, snapshot, owner)

    def wire(self, record, snapshot=None):
        table = self.tables.latest(record['source']['id'])
        key = self.tables.capture().key(record['id'])
        current = self.version(record['id'])
        fresh = freshness(record, table, updating=key in self._timers or key in self._tasks, failed=self._failed.get(key))
        if current['version'] != record['version']:
            fresh = {'state': 'stale', 'words': f"Version {current['version']} is newer · Open it"}
        return {**record, 'freshness': fresh, **({'result': snapshot['result'], **({'comparison': snapshot['comparison']} if snapshot.get('comparison') else {})} if snapshot is not None else {})}

    def _changed(self, event):
        if event.get('type') != 'datatable' or self._loop is None or self._loop.is_closed():
            return
        owner = self.tables.capture()
        for rid, history in self.tables.day.table_derivatives.items():
            record = history[-1]
            if record['mode'] == 'follow' and record['source']['id'] == event.get('id'):
                self._loop.call_soon_threadsafe(self._schedule, rid, owner)

    def _schedule(self, rid, owner):
        try:
            owner.check(self.tables.app)
        except DataTableRefused:
            return
        key = owner.key(rid)
        if key in self._tasks:
            self._dirty.add(key)
            return
        timer = self._timers.pop(key, None)
        if timer is not None:
            timer.cancel()
        self._timers[key] = asyncio.get_running_loop().call_later(.05, self._start, rid, owner)

    def _start(self, rid, owner):
        key = owner.key(rid)
        self._timers.pop(key, None)
        self._tasks[key] = asyncio.create_task(self._follow(rid, owner))

    async def _follow(self, rid, owner):
        key = owner.key(rid)
        try:
            owner.check(self.tables.app)
            record = self.version(rid)
            if record['mode'] == 'follow':
                await self.refresh(rid, record['version'], owner=owner, by='follow')
        except Exception as exc:
            self._failed[key] = str(exc)
        finally:
            self._tasks.pop(key, None)
            if key in self._dirty:
                self._dirty.discard(key)
                self._schedule(rid, owner)

    async def settled(self):
        await asyncio.sleep(0)  # admit already-queued save notifications
        while self._timers or self._tasks:
            await asyncio.sleep(.01)
