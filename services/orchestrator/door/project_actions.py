"""General retained Project actions, shared storage and bounded source intake."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from .connections import Refused
from .projects import access
from .. import agent_runnable

# Schemas describe transport only; scientific capabilities remain catalog-owned.
SCHEMAS = {
    'project.create': {'name': {'type': 'string', 'minLength': 1, 'maxLength': 80}},
    'project.list': {},
    'project.read': {},
    'project.import': {'file': {'type': 'string', 'minLength': 1}},
    'artifact.read': {'reference': {'$ref': 'artifact-reference.schema.json'}, 'offset': {'type':'integer','minimum':0}, 'length': {'type':'integer','minimum':1,'maximum':65536}},
}


def descriptors(build):
    from .listing import app_generation
    return [{'id': name, 'name': name, 'task': agent_runnable.DOOR_RESOURCES[name],
             'inputs': [], 'outputs': [{'type': 'project-evidence', 'label': 'retained Project evidence'}],
             'inputSchema': {'type': 'object', 'properties': fields, 'required': ['reference'] if name == 'artifact.read' else list(fields), 'additionalProperties': False},
             'generation': app_generation(build), 'state': 'available', 'runsOn': 'this Mac',
             'cost': 'local computation only'} for name, fields in SCHEMAS.items() if agent_runnable.door_resource(name)]


def authorize(built, name, project, actor):
    if name not in SCHEMAS or not agent_runnable.door_resource(name):
        raise Refused(403, 'action-not-admitted', 'This action is not declared for agents.', next='discover available tools')
    if name not in ('project.create', 'project.list'):
        access(built).require(project, actor, write=name == 'project.import')


def _summary(built, project, actor):
    role = access(built).require(project, actor)
    owner = built.day.elsewhere(project)
    from .schema import check
    result = {'session': project, 'name': owner.name, 'role': role, 'localOnly': owner.local_only}
    check(result, 'project-reference')
    return result


def _import(built, record, stopped):
    from ..scientific_file_intake import FileIntake, WORKING_BYTES
    from ..scientific_files import CHUNK_BYTES, MAX_CHUNKS, RETAINED_METADATA_BYTES
    from ..scientific_comparisons import identity
    from ..capture import captures
    session, actor = record['project'], record['by']
    access(built).require(session, actor, write=True)
    built.day.elsewhere(session)  # establish a real retained Project before intake
    path = Path(record['params']['file']).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ValueError('Choose an existing file by its absolute path')
    store = built.hub.resources.projects
    # Stream the exact opened descriptor; no source file is loaded as one in-memory matrix.
    with path.open('rb') as source:
        import os
        info = os.fstat(source.fileno())
        request = hashlib.sha256((actor + '\0' + record['requestKey']).encode()).hexdigest()
        room = captures(built.hub).memory.reserve('analysis', WORKING_BYTES + RETAINED_METADATA_BYTES + 3*CHUNK_BYTES,
                                                 'agent source import')
        with room:
            intake = FileIntake(store, session, request, name=path.name, size=info.st_size,
                                max_bytes=CHUNK_BYTES * MAX_CHUNKS, producer={
                                    'kind':'agent-import', 'actor':actor, 'label':record['byLabel'],
                                    'operation':record['id']})
            try:
                while data := source.read(CHUNK_BYTES):
                    if stopped():
                        raise ValueError('Import stopped before publication')
                    intake.write(data)
                if any(getattr(os.fstat(source.fileno()), k) != getattr(info, k) for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')):
                    raise ValueError('The source changed while it was imported')
                access(built).require(session, actor, write=True)
                saved = intake.publish(stopped)
                return {'project': session, 'artifact': identity(saved), 'source': saved.document['state']['sourceFile'],
                        'by': actor, 'says': 'File retained in the Project; reader readiness is separate.'}
            finally:
                intake.close()


async def run(built, record, progress):
    name, actor, project, args = record['tool'], record['by'], record['project'], record['params']
    authorize(built, name, project, actor)
    await progress(0, 1, agent_runnable.DOOR_RESOURCES[name])
    if name == 'project.create':
        def create():
            owner = built.day.create_retained(args['name'], actor=actor)
            access(built).remember(owner.sid, actor)
            return _summary(built, owner.sid, actor)
        result = {'project': await asyncio.to_thread(create), 'says': 'Project created without changing the workspace.'}
    elif name == 'project.list':
        def listing():
            rows = access(built).rows()
            return [_summary(built, sid, actor) for sid, row in rows.items()
                    if row['by'] == actor or actor in row.get('grants', {})]
        result = {'projects': await asyncio.to_thread(listing), 'says': 'Projects available to this connection.'}
    elif name == 'project.read':
        def read():
            from ..scientific_comparisons import identity
            owner = _summary(built, project, actor)
            rows, errors = built.hub.resources.projects.scan(project)
            return {'project': owner, 'artifacts': [{**identity(r), 'kind': r.document['kind'],
                     'title': r.document['title']} for r in rows], 'unreadable': errors}
        result = await asyncio.to_thread(read)
    elif name == 'project.import':
        stopped = lambda: record.get('state') == 'cancelRequested'
        # Shield the thread: cancellation signals stop and waits for publication/cleanup observation.
        task = asyncio.create_task(asyncio.to_thread(_import, built, record, stopped))
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            record['state'] = 'cancelRequested'
            try:
                result = await task
            except (ValueError, OSError, Refused):
                raise asyncio.CancelledError from None
            # Publication won the race. Retain its receipt and report completion;
            # Operations adds the explicit late-stop note.
    else:
        def read_artifact():
            from ..scientific_comparisons import exact, identity
            saved = exact(built.hub.resources.projects, args['reference'], project)
            result = {'artifact': identity(saved), 'document': saved.document, 'untrusted': True}
            file = saved.document.get('state', {}).get('sourceFile')
            if file:
                from urllib.parse import urlencode
                result['download'] = 'artifacts/' + saved.id + '/bytes?' + urlencode({'session':project, 'revision':saved.revision})
                if Path(file['name']).suffix.lower() in ('.txt', '.md', '.csv', '.json', '.toml', '.yaml', '.yml', '.py', '.html'):
                    source = built.hub.resources.projects.file_source(saved.id, project, saved.revision, file['reference'])
                    offset = args.get('offset', 0)
                    if offset > source.size:
                        raise ValueError('Text offset exceeds the retained source')
                    length = min(args.get('length', 65536), source.size-offset)
                    result['content'] = {'text': source.read(offset, length).decode('utf-8', errors='replace'),
                        'offset': offset, 'bytes': length, 'totalBytes': source.size,
                        'nextOffset': offset+length if offset+length<source.size else None,
                        'encoding': 'utf-8; invalid bytes and split characters replaced'}
                else:
                    result['contentUnavailable'] = 'Use a ready catalog reader or download the retained bytes; no text was inferred.'
            return result
        result = await asyncio.to_thread(read_artifact)
    await progress(1, 1, 'Retained result ready')
    return result
