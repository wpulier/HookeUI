"""Trained artifacts and animation documents: retained files with a typed descriptor.

The bytes are one retained file component (the store's chunked, hashed file format), and the record
in ``state.scientificArtifacts`` says what the file is: its semantic role, media type, size and
SHA-256, which package and version can use it, and who produced it. Opening an artifact checks those
bytes against the record. Nothing here loads, unpickles, imports or executes an artifact; a JSON
document is only parsed to check it is JSON. Using an artifact is its integration's job (H12).
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from .scientific_files import CHUNK_BYTES, _name
from .scientific_projects import InvalidProject, _json

FORMAT = 'scientific-artifact-v1'
STATE = 'scientificArtifacts'
SEMANTICS = ('trained-artifact', 'animation-document')
NAME = re.compile(r'[a-z][a-z0-9_]{0,63}')
MEDIA = re.compile(r'[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,63}')
MAX_ARTIFACTS = 16
MAX_BYTES = 64 << 20  # this slice's bound for in-memory publication; streamed publication is slice c
MAX_JSON = 16 << 20
FIELDS = {'format', 'semantic', 'media_type', 'file_name', 'description', 'compatibility', 'producer',
          'size', 'sha256', 'component'}


def component(name):
    return f'file.artifact.{name}'


def declare(*, semantic, media_type, file_name, description=None, compatibility=None, producer=None):
    return {'format': FORMAT, 'semantic': semantic, 'media_type': media_type, 'file_name': file_name,
            'description': description, 'compatibility': compatibility, 'producer': producer}


def schema(value, *, name):
    if type(name) is not str or not NAME.fullmatch(name):
        raise InvalidProject('An artifact name uses lowercase letters, digits and underscores')
    if type(value) is not dict or set(value) != FIELDS or value['format'] != FORMAT:
        raise InvalidProject(f'Artifact {name} needs a complete {FORMAT} declaration')
    if value['semantic'] not in SEMANTICS:
        raise InvalidProject(f'Artifact {name}: choose a trained artifact or an animation document')
    if type(value['media_type']) is not str or not MEDIA.fullmatch(value['media_type']):
        raise InvalidProject(f'Artifact {name} needs its media type, such as application/json')
    _name(value['file_name'])
    if value['description'] is not None and (type(value['description']) is not str or not 0 < len(value['description']) <= 512):
        raise InvalidProject(f'Artifact {name}: a description is at most 512 characters')
    compatibility = value['compatibility']
    if compatibility is not None and (type(compatibility) is not dict or set(compatibility) != {'package', 'version'}
            or any(type(compatibility[k]) is not str or not 0 < len(compatibility[k]) <= 128 for k in compatibility)):
        raise InvalidProject(f'Artifact {name}: compatibility names one package and its exact version')
    if value['producer'] is not None and (type(value['producer']) is not dict or len(_json(value['producer'])) > 16384):
        raise InvalidProject(f'Artifact {name}: its producer is a bounded record')
    if type(value['size']) is not int or not 0 <= value['size'] <= MAX_BYTES:
        raise InvalidProject(f'Artifact {name} is larger than this slice publishes ({MAX_BYTES >> 20} MiB)')
    if type(value['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', value['sha256']):
        raise InvalidProject(f'Artifact {name} needs its exact SHA-256')
    if value['component'] != component(name):
        raise InvalidProject(f'Artifact {name} must name its own file component')
    return copy.deepcopy(value)


def _check_content(name, record, body):
    if record['media_type'] == 'application/json' or record['file_name'].endswith('.json'):
        if len(body) > MAX_JSON:
            raise InvalidProject(f'Artifact {name}: JSON documents are checked up to {MAX_JSON >> 20} MiB')
        try:
            json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, ValueError, RecursionError) as error:
            raise InvalidProject(f'Artifact {name} is declared as JSON but is not valid JSON') from error


def records(document):
    value = document['state'].get(STATE, {})
    if type(value) is not dict or len(value) > MAX_ARTIFACTS:
        raise InvalidProject('This revision has an unsupported artifact record')
    return value


def prepare(store, saved, artifacts, *, replace=(), check=lambda: None):
    from .scientific_families import Prepared
    existing = records(saved.document)
    if type(artifacts) is not dict or not artifacts or len(set(existing) | set(artifacts)) > MAX_ARTIFACTS:
        raise InvalidProject(f'Publish one to {MAX_ARTIFACTS} artifacts per document')
    declared, references = {}, []
    for name, item in artifacts.items():
        if name in existing and name not in replace:
            raise InvalidProject(f'Artifact {name} already exists; replace it explicitly')
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not dict or type(item[1]) is not bytes:
            raise InvalidProject(f'Artifact {name} needs its declaration and bytes')
        declaration, body = item
        record = schema({**declaration, 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
                         'component': component(name)}, name=name)
        _check_content(name, record, body)
        draft = store.file_draft(saved.id, saved.session, component=record['component'], name=record['file_name'],
                                 size=record['size'], sha256=record['sha256'], max_written_bytes=record['size'])
        for start in range(0, len(body), CHUNK_BYTES):
            check()
            draft.write(body[start:start + CHUNK_BYTES])
        references.append(draft.finish())
        declared[name] = record
    replaced = {existing[name]['component'] for name in artifacts if name in existing}
    return Prepared(STATE, replaced, references, {**existing, **declared}, declared)


class Artifact:
    def __init__(self, name, record, source):
        self.name, self.schema, self.source = name, record, source

    def chunks(self, check=lambda: None):
        for offset in range(0, self.schema['size'], CHUNK_BYTES):
            check()
            yield self.source.read(offset, min(CHUNK_BYTES, self.schema['size'] - offset))


def open_artifact(store, project, session, revision, name, *, check=lambda: None):
    """The record and its retained bytes agree (name, size, SHA-256); the content is not interpreted."""
    saved = store.read(project, session, revision=revision)
    declared = records(saved.document)
    if name not in declared:
        raise InvalidProject(f'This saved revision has no artifact named {name}')
    record = schema(declared[name], name=name)
    reference = next((ref for ref in saved.document['resources'] if ref.get('component') == record['component']
                      and ref.get('id') == project and ref.get('session') == session), None)
    if reference is None:
        raise InvalidProject(f'Artifact {name} is missing its file; the revision was kept unchanged')
    source = store.file_source(project, session, revision, reference, check=check)
    if (source.name, source.size, source.sha256) != (record['file_name'], record['size'], record['sha256']):
        raise InvalidProject(f'Artifact {name} disagrees with its retained file')
    return Artifact(name, record, source)
