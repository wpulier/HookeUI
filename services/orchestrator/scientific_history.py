"""Saved Python source and outcomes, separate from accepted scientific array edits.

One owned worker thread serializes this history. Disk failures never authorize code
replay: retry_save repeats only the exact immutable document save that failed.
"""
from __future__ import annotations

import copy
import re
import secrets
import time

from env_worker.scientific_console import TEXT_BYTES, reply, source
from .scientific_projects import InvalidProject, _json

FORMAT = 'scientific-python-history-v1'
MAX_HISTORY = 1 << 20
MAX_CELLS = 128
TERMINAL_ALLOWANCE = 4 * TEXT_BYTES * 6 + 8192
CELL = re.compile(r'[A-Za-z0-9_-]{16,128}')


class HistoryUnavailable(RuntimeError):
    pass


class ScientificHistory:
    def __init__(self, store, saved, incarnation, *, tab, placement):
        self.store, self.session = store, saved.session
        self.identity = 'p' + secrets.token_hex(16)
        self.record = None
        self.pending = None
        self.undispatched = None
        self.document = {
            'kind': 'scientific-python-history',
            'title': ('Scientific history · ' + saved.document['title'])[:240],
            'resources': [], 'runtimes': saved.document['runtimes'],
            'state': {'format': FORMAT, 'input': {'session': saved.session, 'id': saved.id,
                'revision': saved.revision, 'digest': saved.digest}, 'kernel': incarnation,
                'origin': {'tab': tab, 'placement': placement}, 'cells': [], 'actions': []},
        }

    def reference(self):
        if self.record is None:
            return None
        return {'id': self.record.id, 'day': self.record.session, 'version': self.record.revision,
                'picture': None}

    def lookup(self, identity, code):
        if type(identity) is not str or CELL.fullmatch(identity) is None:
            raise InvalidProject('A Python cell needs its original request identity')
        source(code)
        if self.pending is not None:
            raise HistoryUnavailable('Python history was not confirmed saved. Retry saving its history before running another cell.')
        cell = next((row for row in self.document['state']['cells'] if row['id'] == identity), None)
        if cell is not None and cell['source'] != code:
            raise InvalidProject('This Python request identity already belongs to different code')
        return copy.deepcopy(cell)

    def _save(self, document):
        if len(_json(document)) > MAX_HISTORY:
            raise InvalidProject('Python history is full. Keep it and start a new scientific session.')
        self.pending = (copy.deepcopy(document), secrets.token_hex(32))
        return self.retry_save()

    def retry_save(self):
        if self.pending is None:
            return self.reference()
        document, transaction = self.pending
        try:
            if self.record is None:
                record = self.store.create(self.session, document, identity=self.identity)
            else:
                record = self.store.save(self.record.id, self.session, document,
                                         expected_revision=self.record.revision, transaction=transaction)
        except (OSError, InvalidProject) as error:
            raise HistoryUnavailable('Python history could not be confirmed saved. Retry saving history; do not rerun the code.') from error
        self.record, self.document, self.pending = record, record.document, None
        if self.undispatched is not None:
            collection, identity = self.undispatched
            document = copy.deepcopy(self.document)
            row = next(row for row in document['state'][collection] if row['id'] == identity)
            row.update(state='not-dispatched', finishedAt=time.time(),
                       explanation='Admission was not confirmed. This request was never sent to the worker and changed nothing.')
            if collection == 'actions' and row['request'].get('op') == 'labels':
                row['outcome'] = {'edit': {'request': identity, 'state': 'refused', 'changed': False,
                    'label': None, 'message': row['explanation']}}
            self.undispatched = None
            self._save(document)
        return self.reference()

    def _admit(self, document, collection, identity):
        try:
            self._save(document)
        except HistoryUnavailable:
            # The call has not reached the worker. Retry first confirms the original
            # write, then records this known outcome; it can never dispatch the call.
            self.undispatched = (collection, identity)
            raise

    def action_outcome(self, identity):
        row = next(row for row in self.document['state'].get('actions', []) if row['id'] == identity)
        return copy.deepcopy(row['outcome'])

    def undispatched_cell(self):
        cells = self.document['state']['cells']
        if not cells or cells[-1]['state'] != 'not-dispatched':
            return None
        return {key: cells[-1][key] for key in ('id', 'source', 'state')}

    def recovered_edits(self):
        return [copy.deepcopy(row['outcome']['edit']) for row in self.document['state'].get('actions', [])
                if row['state'] in ('completed', 'not-dispatched') and row['request'].get('op') == 'labels']

    def begin(self, identity, code):
        """True only for newly persisted admission; duplicate delivery never reruns."""
        if self.lookup(identity, code) is not None:
            return False
        document = copy.deepcopy(self.document)
        cells = document['state']['cells']
        cells.append({'id': identity, 'source': code, 'state': 'submitted', 'at': time.time()})
        if len(cells) > MAX_CELLS or len(_json(document)) + TERMINAL_ALLOWANCE > MAX_HISTORY:
            raise InvalidProject('Python history is full. Keep it and start a new scientific session.')
        if any(row['state'] == 'submitted' for row in cells[:-1]):
            raise HistoryUnavailable('A previous Python outcome is unconfirmed. Start a new scientific session; nothing will be replayed.')
        self._admit(document, 'cells', identity)
        return True

    def finish(self, identity, transcript=None, *, interrupted=False):
        if self.pending is not None:
            raise HistoryUnavailable('Retry saving the pending Python history before changing it')
        document = copy.deepcopy(self.document)
        cell = next((row for row in document['state']['cells'] if row['id'] == identity), None)
        if cell is None or cell['state'] != 'submitted':
            raise InvalidProject('This Python cell has no pending saved admission')
        cell.update(state='interrupted' if interrupted else 'completed', finishedAt=time.time())
        if interrupted:
            cell['explanation'] = 'The code may have run. Its outcome is unconfirmed and its private state was not saved. Nothing was replayed.'
        else:
            cell['transcript'] = reply(transcript)
        return self._save(document)

    def producer(self):
        """Only the owner supplies this exact persisted history at snapshot admission."""
        if self.pending is not None or any(row['state'] == 'submitted' for row in
                [*self.document['state']['cells'], *self.document['state'].get('actions', [])]):
            raise HistoryUnavailable('Producing history is not confirmed saved. Retry saving history before accepting array changes.')
        record = self.record
        return {'kind': 'scientific-session', 'kernel': self.document['state']['kernel'],
                'actor': 'studio', 'origin': copy.deepcopy(self.document['state']['origin']),
                'history': None if record is None else {'id': record.id, 'session': record.session,
                    'revision': record.revision, 'digest': record.digest}}

    def begin_action(self, identity, request, context):
        if type(identity) is not str or CELL.fullmatch(identity) is None:
            raise InvalidProject('Scientific actions need their original identity')
        if self.pending is not None:
            raise HistoryUnavailable('Retry saving pending history before another action')
        actions = self.document['state'].get('actions', [])
        existing = next((row for row in actions if row['id'] == identity), None)
        if existing is not None:
            if existing['request'] != request or existing['context'] != context:
                raise InvalidProject('This action identity belongs to different scientific work')
            if existing['state'] != 'completed':
                raise HistoryUnavailable('The prior action outcome is unconfirmed. Inspect history; do not replay it.')
            return False
        if len(actions) >= MAX_CELLS or any(row['state'] == 'submitted' for row in actions):
            raise HistoryUnavailable('Scientific action history is full or unconfirmed. Keep this session and inspect its history.')
        document = copy.deepcopy(self.document)
        document['state'].setdefault('actions', []).append({'id':identity,'request':copy.deepcopy(request),
            'context':copy.deepcopy(context),'state':'submitted','at':time.time()})
        if len(_json(document)) + TERMINAL_ALLOWANCE > MAX_HISTORY:
            raise HistoryUnavailable('Scientific action history is full. Keep this session before continuing.')
        self._admit(document, 'actions', identity)
        return True

    def finish_action(self, identity, outcome, *, interrupted=False):
        if self.pending is not None:
            raise HistoryUnavailable('Retry saving pending history before changing it')
        document = copy.deepcopy(self.document)
        row = next((row for row in document['state'].get('actions',[]) if row['id'] == identity),None)
        if row is None or row['state'] != 'submitted':
            raise InvalidProject('Scientific action has no saved admission')
        row.update(state='interrupted' if interrupted else 'completed', finishedAt=time.time(), outcome=copy.deepcopy(outcome))
        return self._save(document)
