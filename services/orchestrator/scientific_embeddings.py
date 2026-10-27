"""Embeddings: one feature vector per object, keyed to an object table of the same revision.

The matrix is one native ``[row, feature]`` array component; row ``i`` describes the object in row
``i`` of the table's key column, so identity comes from that key, never from position alone. A
feature model (runtime and exact recipe), the region the features were computed from and the
coordinates they refer to are exact references that the shared resource contract validates. Values
must be finite: an embedding has no missing-value representation. Nothing here computes features.
"""
from __future__ import annotations

import copy
import hashlib
import re

import numpy as np

from .scientific_projects import InvalidProject, _json
from .semantic_resources import InvalidDescriptor, parse_descriptor

FORMAT = 'scientific-embeddings-v1'
STATE = 'scientificEmbeddings'
NAME = re.compile(r'[a-z][a-z0-9_]{0,63}')
MAX_EMBEDDINGS = 8
MAX_DIMS = 65536
FIELDS = {'format', 'table', 'key', 'key_sha256', 'rows', 'dims', 'dtype', 'component', 'unit', 'feature_model',
          'region', 'coordinates', 'producer'}
OBSERVED = {'reference', 'shape', 'dtype', 'axes', 'storage_format', 'storage_version'}


def component(name):
    return f'array.embedding.{name}'


def declare(*, table, key, unit=None, feature_model=None, region=None, coordinates=None, producer=None):
    return {'format': FORMAT, 'table': table, 'key': key, 'unit': unit, 'feature_model': feature_model,
            'region': region, 'coordinates': coordinates, 'producer': producer}


def key_digest(keys):
    """The exact key values, in row order, that an embedding's rows describe (dtype and bytes)."""
    return hashlib.sha256(keys.dtype.str.encode() + b'\0' + np.ascontiguousarray(keys).tobytes()).hexdigest()


def _descriptor(record):
    """What the matrix component itself records; references to other resources stay in the schema."""
    value = {'semantic': 'embeddings', 'object_key': record['key']}
    if record['unit'] is not None:
        value['unit'] = record['unit']
    return parse_descriptor(value).wire()


def _references(record, name):
    try:
        parse_descriptor({key: record[key] for key in ('feature_model', 'region', 'coordinates', 'unit')
                          if record[key] is not None})
    except InvalidDescriptor as error:
        raise InvalidProject(f'Embedding {name}: {error}') from error


def schema(value, *, name):
    if type(name) is not str or not NAME.fullmatch(name):
        raise InvalidProject('An embedding name uses lowercase letters, digits and underscores')
    if type(value) is not dict or set(value) != FIELDS or value['format'] != FORMAT:
        raise InvalidProject(f'Embedding {name} needs a complete {FORMAT} declaration')
    if type(value['table']) is not str or type(value['key']) is not str:
        raise InvalidProject(f'Embedding {name} names the object table and key its rows follow')
    if type(value['rows']) is not int or value['rows'] < 0 or type(value['dims']) is not int \
            or not 1 <= value['dims'] <= MAX_DIMS:
        raise InvalidProject(f'Embedding {name} needs its row count and 1 to {MAX_DIMS} feature dimensions')
    if value['dtype'] not in ('<f2', '>f2', '<f4', '>f4', '<f8', '>f8'):
        raise InvalidProject(f'Embedding {name} stores floating-point features with their byte order')
    if value['component'] != component(name):
        raise InvalidProject(f'Embedding {name} must name its own component')
    if type(value['key_sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', value['key_sha256']):
        raise InvalidProject(f'Embedding {name} needs the digest of the keys its rows describe')
    if value['producer'] is not None and (type(value['producer']) is not dict or len(_json(value['producer'])) > 16384):
        raise InvalidProject(f'Embedding {name}: its producer is a bounded record')
    _references(value, name)
    return copy.deepcopy(value)


def records(document):
    value = document['state'].get(STATE, {})
    if type(value) is not dict or len(value) > MAX_EMBEDDINGS:
        raise InvalidProject('This revision has an unsupported embedding record')
    return value


def _table(store, saved, pending, name, key):
    from . import scientific_tables
    if name in pending:
        table, values = pending[name]
        return table, values[key] if table['key'] == key else None
    if name not in scientific_tables.tables(saved.document):
        return None, None
    table = scientific_tables.open_table(store, saved.id, saved.session, saved.revision, name)
    return table.schema, table.read(key) if table.schema['key'] == key else None


def prepare(store, saved, embeddings, pending, *, replace=(), check=lambda: None):
    """Check each matrix against the object table it follows and write it; the CAS is the caller's."""
    from .scientific_families import Prepared, write_array
    existing = records(saved.document)
    if type(embeddings) is not dict or not embeddings or len(set(existing) | set(embeddings)) > MAX_EMBEDDINGS:
        raise InvalidProject(f'Publish one to {MAX_EMBEDDINGS} embeddings per document')
    declared, references = {}, []
    for name, item in embeddings.items():
        if name in existing and name not in replace:
            raise InvalidProject(f'Embedding {name} already exists; replace it explicitly')
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not dict:
            raise InvalidProject(f'Embedding {name} needs its declaration and matrix')
        declaration, matrix = item
        if type(matrix) is not np.ndarray or matrix.ndim != 2:
            raise InvalidProject(f'Embedding {name} needs a two-dimensional [object, feature] matrix')
        table, keys = _table(store, saved, pending, declaration.get('table'), declaration.get('key'))
        if keys is None or table is None or table['semantic'] != 'object-table':
            raise InvalidProject(f'Embedding {name} must follow the key of an object table in this revision')
        record = schema({**declaration, 'rows': matrix.shape[0], 'dims': matrix.shape[1], 'dtype': matrix.dtype.str,
                         'component': component(name), 'key_sha256': key_digest(keys)}, name=name)
        if table['rows'] != record['rows']:
            raise InvalidProject(f'Embedding {name} needs one row per object of table {record["table"]}')
        if not np.isfinite(matrix).all():
            raise InvalidProject(f'Embedding {name} has missing or infinite values; embeddings have no missing-value form')
        check()
        references.append(write_array(store, saved, record['component'], matrix, ['row', 'feature'],
                                      _descriptor(record)))
        declared[name] = record
    replaced = {existing[name]['component'] for name in embeddings if name in existing}
    return Prepared(STATE, replaced, references, {**existing, **declared}, declared)


def check_tables_kept(document, replaced_tables, *, republished=frozenset()):
    """Replacing a table changes its rows; an embedding following it must be published again with it."""
    for name, record in records(document).items():
        if name not in republished and record.get('table') in replaced_tables:
            raise InvalidProject(f'Embedding {name} follows table {record["table"]}; publish it again with that table')


class Embedding:
    def __init__(self, name, record, source):
        self.name, self.schema, self.source = name, record, source

    def read(self, start=0, stop=None):
        stop = self.schema['rows'] if stop is None else stop
        if not 0 <= start <= stop <= self.schema['rows']:
            raise InvalidProject('Choose rows inside the embedding')
        out = np.empty((stop - start, self.schema['dims']), dtype=self.source.dtype)
        step = self.source.chunks[0]
        position = start
        while position < stop:
            end = min(stop, (position // step + 1) * step)
            out[position - start:end - start] = self.source.read((slice(position, end), slice(0, self.schema['dims'])))
            position = end
        return out


def open_embedding(store, project, session, revision, name):
    from . import scientific_tables
    saved = store.read(project, session, revision=revision)
    declared = records(saved.document)
    if name not in declared:
        raise InvalidProject(f'This saved revision has no embedding named {name}')
    record = schema(declared[name], name=name)
    table = scientific_tables.open_table(store, project, session, revision, record['table'])
    if (table.schema['key'] != record['key'] or table.rows != record['rows']
            or key_digest(table.read(record['key'])) != record['key_sha256']):
        raise InvalidProject(f'Embedding {name} no longer follows the keys of table {record["table"]}; '
                             'publish it again with that table')
    reference = next((ref for ref in saved.document['resources'] if ref.get('component') == record['component']
                      and ref.get('id') == project and ref.get('session') == session), None)
    if reference is None:
        raise InvalidProject(f'Embedding {name} is missing its matrix; the revision was kept unchanged')
    source = store.array_source(project, session, revision, reference)
    described = {k: v for k, v in source.descriptor.items() if k not in OBSERVED}
    if (list(source.shape) != [record['rows'], record['dims']] or source.dtype != record['dtype']
            or list(source.axes) != ['row', 'feature'] or described != _descriptor(record)):
        raise InvalidProject(f'Embedding {name} disagrees with its retained matrix')
    return Embedding(name, record, source)


def remap(state, mapping):
    """A copy's embeddings name the copied region, coordinates and recipe, when those were copied with it."""
    for record in (state.get(STATE) or {}).values():
        if type(record) is not dict:
            continue
        for name in ('region', 'coordinates'):
            if type(record.get(name)) is dict:
                record[name] = mapping.get(_json(record[name]), record[name])
        model = record.get('feature_model')
        if type(model) is dict and type(model.get('recipe')) is dict:
            model['recipe'] = mapping.get(_json(model['recipe']), model['recipe'])
