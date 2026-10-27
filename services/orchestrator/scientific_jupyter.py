"""An authorized local Jupyter connection to one open scientific session.

The kernel Jupyter starts (`jupyter_kernel.py`, beside this file) runs nothing itself. Each cell, completion, help
request and interrupt goes to Hooke with a bearer token that names one open review session, and Hooke runs it
through that session exactly as the Studio console does: the session's own saved resource, its history admission,
interruption and publication checks. There is no second way to execute code.

The token never reaches the browser. It is written once, readable only by this user, into a connection file under
Hooke's home, and a kernelspec beside it names that file. Grants live in memory: stopping or restarting the session,
revoking the connection, restarting the app or reaching the time limit ends them, and their files are removed.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import threading
import time

FORMAT = 'hooke-jupyter-connection-v1'
LIFETIME_S = 12 * 3600
MAX_GRANTS = 16
KERNEL = Path(__file__).with_name('jupyter_kernel.py')


class JupyterRefused(ValueError):
    pass


@dataclass
class Grant:
    identity: str
    owner: object
    reference: dict
    expires: float
    folder: Path
    connection: Path


def _digest(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class JupyterGrants:
    def __init__(self, manager, root):
        self.manager, self.root = manager, Path(root)
        self._grants: dict[str, Grant] = {}
        self._lock = threading.Lock()
        # Connections from an earlier run of the app can never be valid again.
        shutil.rmtree(self.root, ignore_errors=True)

    def issue(self, client, identity, base_url):
        """Grant one session a notebook connection; returns where its kernelspec is, never the token."""
        owner = self.manager._owner(client, identity)
        reference = getattr(owner.execution, 'reference', None)
        if type(reference) is not dict:
            raise JupyterRefused('This session has no saved resource for a notebook to use. Open a saved scientific document first.')
        token = secrets.token_urlsafe(32)
        name = 'hooke-' + hashlib.sha256(identity.encode()).hexdigest()[:12]
        folder = self.root / name
        with self._lock:
            self._drop(identity)
            if len(self._grants) >= MAX_GRANTS:
                raise JupyterRefused(f'At most {MAX_GRANTS} notebook connections can be open. Revoke one first.')
            # The kernelspec folder holds only kernel.json, because `jupyter kernelspec install` copies the folder;
            # the token stays in Hooke's home and is removed with the grant.
            folder.mkdir(parents=True, mode=0o700)
            (self.root / 'connections').mkdir(mode=0o700, exist_ok=True)
            connection = self.root / 'connections' / f'{name}.json'
            fd = os.open(connection, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump({'format': FORMAT, 'url': base_url.rstrip('/'), 'token': token, 'review': identity}, stream)
            title = 'Hooke · ' + str(owner.execution.reference.get('id', identity))[:24]
            spec = {'argv': ['python3', str(KERNEL), '-f', '{connection_file}', '--hooke', str(connection)],
                    'display_name': title, 'language': 'python', 'metadata': {'hooke': {'review': identity}}}
            os.chmod(self.root, 0o700)
            (folder / 'kernel.json').write_text(json.dumps(spec, indent=1))
            expires = time.time() + LIFETIME_S
            self._grants[_digest(token)] = Grant(identity, owner, dict(reference), expires, folder, connection)
        return {'name': name, 'display': title, 'kernel': str(folder), 'expires': expires,
                'install': f'jupyter kernelspec install --user "{folder}"',
                'meaning': 'Cells from this notebook run in this Hooke session, on its saved input, and are kept in its '
                           'Python history. The connection ends when the session stops, the app restarts or you revoke it.'}

    def revoke(self, client, identity):
        self.manager._owner(client, identity)
        with self._lock:
            return self._drop(identity)

    def forget(self, identity):
        """The session ended: remove its grant and files now, not at the notebook's next request."""
        with self._lock:
            self._drop(identity)

    def resolve(self, token):
        """The session a presented token may act for: (client, identity, reference)."""
        if type(token) is not str or not 16 <= len(token) <= 256:
            raise JupyterRefused('This notebook connection has no valid Hooke token')
        presented = _digest(token)
        with self._lock:
            grant = next((g for d, g in self._grants.items() if hmac.compare_digest(d, presented)), None)
            if grant is None:
                raise JupyterRefused('This notebook connection was revoked or ended. Connect the notebook again from Hooke.')
            owner = self.manager._owners.get(grant.identity)
            if (grant.expires < time.time() or owner is not grant.owner or owner.detached
                    or owner.stop.is_set()):
                self._drop(grant.identity)
                raise JupyterRefused('The Hooke session this notebook was connected to ended. Reopen it and connect again.')
            return owner.origin.client, grant.identity, dict(grant.reference)

    def _drop(self, identity):
        dropped = False
        for key, grant in list(self._grants.items()):
            if grant.identity == identity:
                del self._grants[key]
                shutil.rmtree(grant.folder, ignore_errors=True)
                grant.connection.unlink(missing_ok=True)
                dropped = True
        if not dropped:
            name = 'hooke-' + hashlib.sha256(identity.encode()).hexdigest()[:12]
            shutil.rmtree(self.root / name, ignore_errors=True)
            (self.root / 'connections' / f'{name}.json').unlink(missing_ok=True)
        return dropped

    def request(self, action, fields, reference):
        """The session request a notebook action becomes: the same ops the Studio console sends."""
        from .scientific_history import CELL
        if action == 'execute':
            if set(fields) != {'cell', 'source'} or type(fields['cell']) is not str or not CELL.fullmatch(fields['cell']):
                raise JupyterRefused('A notebook cell needs its identity and source')
            return {'op': 'python-resources', 'resource': reference, 'source': fields['source'], 'cell': fields['cell']}
        if action == 'interrupt':
            if set(fields) != {'cell'} or type(fields['cell']) is not str or not CELL.fullmatch(fields['cell']):
                raise JupyterRefused('Interrupt needs the exact running notebook cell')
            return {'op': 'python-interrupt', 'cell': fields['cell']}
        if action == 'assist':
            if set(fields) != {'mode', 'expression'}:
                raise JupyterRefused('Completion and help need a mode and a name')
            return {'op': 'python-assist', 'mode': fields['mode'], 'expression': fields['expression'], 'resource': reference}
        raise JupyterRefused('Choose a notebook action Hooke supports')

