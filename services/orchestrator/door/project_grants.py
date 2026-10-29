"""One-use native owner confirmations; preparing a choice grants no authority."""
from __future__ import annotations

import copy
import secrets
import threading
import time

from . import native
from .connections import Refused
from .projects import access, owner_project


def message(challenge, *, cancel=False):
    payload = ("hooke-project-grant\n" + "\n".join(challenge[key] for key in
            ('id', 'session', 'connection', 'role'))).encode()
    return payload + (b'\ncancel' if cancel else b'')


class GrantConfirmations:
    def __init__(self, built, connections, clock=time.monotonic):
        self.built, self.connections, self.clock = built, connections, clock
        self.pending = {}
        self.lock = threading.Lock()

    def _live(self, connection):
        return next((c for c in self.connections.connections() if c.id == connection and c.revoked_at is None), None)

    def prepare(self, value):
        if native.public_key() is None:
            raise Refused(403, 'native-grant-required', 'This copy has no native owner confirmation.', next='use its development owner controls')
        if set(value) != {'session', 'connection', 'role'} or value['role'] not in ('read', 'contribute', None):
            raise ValueError('Choose a Project, connection and role')
        connection = self._live(value['connection'])
        if connection is None:
            raise ValueError('Choose a live confirmed connection')
        project = owner_project(self.built, value['session'])
        row = access(self.built).rows().get(value['session'], {'by': 'user', 'grants': {}})
        if row['by'] == connection.actor:
            raise ValueError('Choose an existing Project and a connection other than its creator')
        with self.lock:
            now = self.clock()
            self.pending = {key: entry for key, entry in self.pending.items() if entry['expires'] > now}
            if len(self.pending) >= 5:
                raise Refused(429, 'grant-confirmations-full', 'Too many Project choices await confirmation.', next='finish a dialog or wait five minutes')
            challenge = {'id': 'pg_' + secrets.token_hex(12), 'session': value['session'],
                         'projectName': project.name or value['session'], 'connection': connection.id,
                         'label': connection.label, 'role': value['role'] or 'none'}
            self.pending[challenge['id']] = {'choice': challenge, 'expires': now + 300,
                'previous': row.get('grants', {}).get(connection.actor)}
            return copy.deepcopy(challenge)

    def confirm(self, value):
        if (set(value) not in ({'id', 'signature'}, {'id', 'signature', 'answer'})
                or not isinstance(value['id'], str) or ('answer' in value and value['answer'] != 'cancel')):
            raise ValueError('Confirm the exact pending Project choice')
        with self.lock:
            pending = self.pending.get(value['id'])
            if pending is None or pending['expires'] <= self.clock():
                self.pending.pop(value['id'], None)
                raise Refused(410, 'grant-confirmation-expired', 'This Project choice expired or was already answered.', next='choose the access again in Settings')
            choice = pending['choice']
            # Source development consent never qualifies a packaged grant signature.
            if native.public_key() is None:
                raise Refused(403, 'native-grant-required', 'The native owner confirmation is unavailable.', next='restart Hooke')
            native.check_message(message(choice, cancel=value.get('answer') == 'cancel'), value['signature'], studio_ok=False)
            self.pending.pop(value['id'])
            if value.get('answer') == 'cancel':
                return {**choice, 'cancelled': True}
            connection = self._live(choice['connection'])
            if connection is None:
                raise ValueError('The connection was revoked before confirmation')
            owner_project(self.built, choice['session'])
            current = access(self.built).rows().get(choice['session'], {})
            if current.get('grants', {}).get(connection.actor) != pending['previous']:
                raise ValueError('Project access changed while the dialog was open; review it again')
            access(self.built).grant(choice['session'], connection.actor,
                                    None if choice['role'] == 'none' else choice['role'], authority='native', user_project=True)
            return choice
