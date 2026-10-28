"""Project access for every door surface; credentials never imply Project membership.

The existing creator registry is preserved. Grants are owner-authored changes, not
agent actions. Revision history is append-only within each registry row; revocation
removes authority, never attribution. Corrupt registries refuse rather than reset.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from .connections import Refused
from .operations import _write
from ..day.sid import is_sid

_LOCK = threading.RLock()
_LIMIT = 4 << 20
_ROLES = {'read', 'contribute'}
_ACTOR = re.compile(r'agent:ac_[a-f0-9]{24}\Z')

def _actor(value):
    return isinstance(value, str) and _ACTOR.fullmatch(value) is not None


class ProjectAccess:
    def __init__(self, home):
        self.path = Path(home) / 'agents' / 'projects.json'

    def _read(self):
        try:
            if self.path.stat().st_size > _LIMIT:
                raise ValueError('registry size limit')
            rows = json.loads(self.path.read_text())
            if type(rows) is not dict:
                raise ValueError('registry object required')
            for sid, row in rows.items():
                if not is_sid(sid) or type(row) is not dict or not (row.get('by') == 'user' or _actor(row.get('by'))):
                    raise ValueError('invalid Project owner')
                grants = row.get('grants', {})
                if type(grants) is not dict or any(not _actor(actor) or role not in _ROLES
                                                  for actor, role in grants.items()):
                    raise ValueError('invalid Project grant')
                if type(row.get('history', [])) is not list or type(row.get('workflows', [])) is not list:
                    raise ValueError('invalid Project history or workflows')
                if any(not isinstance(value, str) for value in row.get('workflows', [])):
                    raise ValueError('invalid Project workflow')
            return rows
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError):
            raise Refused(503, 'project-access-unreadable', 'Project permissions could not be read. Nothing was changed.',
                          next='restore the permissions registry or report the problem') from None

    def _save(self, rows):
        if len(json.dumps(rows).encode('utf-8')) > _LIMIT:
            raise Refused(409, 'project-access-full', 'Project permission storage is full. Nothing was changed.',
                          next='retain the current registry and report the limit')
        _write(self.path, rows)

    def rows(self):
        with _LOCK:
            return self._read()

    def remember(self, project, actor):
        if not is_sid(project) or not _actor(actor):
            raise ValueError('Use an exact Project and authenticated agent identity')
        with _LOCK:
            rows = self._read()
            old = rows.get(project)
            if old:
                if old['by'] != actor:
                    raise Refused(409, 'project-owned', 'That Project already has an owner.', next='use its existing access')
                return
            rows[project] = {'by': actor, 'at': time.time(), 'grants': {}, 'history': []}
            self._save(rows)

    def role(self, project, actor):
        with _LOCK:
            row = self._read().get(project, {})
            return 'owner' if row.get('by') == actor else row.get('grants', {}).get(actor)

    def require(self, project, actor, *, write=False):
        if not isinstance(project, str) or not is_sid(project):
            raise Refused(400, 'project-required', 'Choose an exact Project.', next='list your Projects')
        role = self.role(project, actor)
        if role is None or (write and role == 'read'):
            raise Refused(403, 'project-access-refused', 'This connection has no permission for that Project action.',
                          next='ask the owner to grant read or contribute access in Hooke')
        return role

    def remember_workflow(self, project, actor, workflow):
        with _LOCK:
            self.require(project, actor, write=True)
            rows = self._read()
            ids = rows[project].setdefault('workflows', [])
            if workflow not in ids:
                if len(ids) >= 1024:
                    raise ValueError('Project workflow limit reached')
                ids.append(workflow)
                self._save(rows)

    def require_workflow(self, project, workflow):
        if workflow not in self.rows().get(project, {}).get('workflows', []):
            raise Refused(403, 'workflow-access-refused', 'That workflow is not available in this Project.',
                          next='create or select a workflow associated with this Project')

    def grant(self, project, actor, role, *, authority, user_project=False):
        """Only the trusted owner transport calls this, never a door action."""
        if authority not in ('native', 'development') or role not in (*_ROLES, None):
            raise ValueError('An owner-confirmed read/contribute grant or revocation is required')
        if not isinstance(project, str) or not is_sid(project) or not _actor(actor):
            raise ValueError('Select an exact Project and stable connection ID')
        with _LOCK:
            rows = self._read()
            if project not in rows:
                if not user_project:
                    raise Refused(404, 'unknown-project', 'This Project is not registered for agents.', next='choose an existing Project in owner access settings')
                # The owner transport already verified the retained Project. Listing or preparing
                # consent never registers it. A revoke of absent authority needs no new record.
                if role is None:
                    return
                rows[project] = {'by': 'user', 'at': time.time(), 'grants': {}, 'history': []}
            row = rows[project]
            if row['by'] == actor:
                raise ValueError('The creator keeps its original ownership; disconnect it to revoke its credential')
            grants = row.setdefault('grants', {})
            if grants.get(actor) == role:
                return
            history = row.setdefault('history', [])
            if len(history) >= 1024:
                raise Refused(409, 'grant-history-full', 'The permission history is full.', next='retain this Project and report the limit')
            history.append({'actor': actor, 'role': role, 'by': 'user', 'authority': authority, 'at': time.time()})
            if role is None:
                grants.pop(actor, None)
            else:
                grants[actor] = role
            self._save(rows)


def access(built):
    return ProjectAccess(built.paths.home)


def owner_project(built, session):
    """Resolve owner-selected retained input without creating/opening a Project on stage."""
    if not isinstance(session, str) or not is_sid(session):
        raise ValueError('Choose an exact retained Project')
    if built.day.catalogue.get(session) is None:
        raise Refused(404, 'unknown-project', 'This Project is not available.', next='choose an existing Project')
    return built.day.elsewhere(session)
