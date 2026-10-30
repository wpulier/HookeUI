"""Read-only Project location facts; a folder is not proof of collected dependencies.

Callers authorize the Project before reading. This projection never registers a
location, takes a writer lease, creates a journal or inspects another Project.
"""
from pathlib import Path

from .day.ownership import retained_owner
from .day.sid import is_sid
from .project_repository import verify_owner
from .project_roots import ProjectLocationError


def unavailable(session):
    """The same meaning for a host that cannot inspect local Project storage."""
    return _value(session, layout='unavailable', availability='unavailable',
                  reason='This host does not support local Project storage operations.')


def _value(session, *, layout, availability, origin=None, location=None, reason=None):
    return {'session': session, 'origin': origin, 'layout': layout,
            'location': location, 'availability': availability, 'reason': reason,
            'collection': {'state': 'not-assessed',
                'reason': 'Complete dependency collection has not been verified. A retained folder or scientific bundle alone does not establish completeness.'}}


def describe(day, session):
    """Return captured-owner facts, including unavailable locations without fallback."""
    if not is_sid(session):
        raise ProjectLocationError('Choose a valid Project handle')
    locations = getattr(day, 'locations', None)
    if locations is not None:
        origin = locations.handles().get(session)
        if origin is not None:
            locator = locations.registry.locations()[origin]
            value = dict(layout='project-folder', origin=origin, location=locator['path'])
            try:
                root = locations.project(session)
                verify_owner(root)  # check even when this process already holds its lease
            except (OSError, ValueError) as error:
                return _value(session, availability='unavailable', reason=str(error)[:500], **value)
            return _value(session, availability='available', **value)
    # Legacy stores may contain several Projects. Never advertise the shared home
    # as a self-contained Project folder or mint an origin while inspecting it.
    try:
        owner = retained_owner(Path(day.root), session)
    except (OSError, ValueError) as error:
        return _value(session, layout='legacy', availability='unavailable', reason=str(error)[:500])
    if owner is None:
        return _value(session, layout='legacy', availability='unavailable',
                      reason='No verified Project origin is available in this legacy journal. Explicit migration is required.')
    return _value(session, layout='legacy', availability='available', origin=owner['origin']['origin'],
                  reason='Research is retained in existing Hooke storage. It has not been collected into a portable Project folder.')
