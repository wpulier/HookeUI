"""Tables, embeddings and artifacts that Python or a plugin made, kept as frozen drafts in the confined host.

Python reaches this as ``families`` in Hooke's console; a plugin's per-object results arrive the way napari
keeps them, as a layer's ``features``. Declaring a draft copies its values once, read-only, and records each
part's size and SHA-256. The parent lists the drafts (``describe``) and, on Save, receives each part's exact
bytes (``stage``); it checks everything again and publishes them in one Project transaction. Nothing here
publishes, and no draft names a saved resource except through an input this session was granted.
"""
from __future__ import annotations

import hashlib
import json
import re

from .native_regions import DTYPES, MAX_REGION_BYTES

NAME = re.compile(r'[a-z][a-z0-9_]{0,63}')
MAX_DRAFTS = 16
MAX_COLUMNS = 48
MAX_BYTES = 256 << 20  # every draft's parts together, frozen in the worker's memory
MAX_DESCRIPTION = 96 << 10  # the listing travels as one message header
TABLE_SEMANTICS = ('object-table', 'lineage-graph', 'relation-table')
ROLES = ('key', 'feature', 'measurement', 'relation')
ARTIFACT_SEMANTICS = ('trained-artifact', 'animation-document')
FLOATS = ('<f2', '>f2', '<f4', '>f4', '<f8', '>f8')


def _name(value, what):
    if type(value) is not str or not NAME.fullmatch(value):
        raise ValueError(f'{what} uses lowercase letters, digits and underscores, starting with a letter')
    return value


def _text(value, what, limit=256):
    if value is not None and (type(value) is not str or not 0 < len(value) <= limit or not value.strip()):
        raise ValueError(f'{what} needs text of at most {limit} characters')
    return value


def _frozen(values, what):
    import numpy as np
    array = np.array(values, copy=True)
    if array.dtype.kind == 'O' or array.dtype.str not in DTYPES:
        raise ValueError(f'{what} needs numeric values of one supported scalar type')
    array.setflags(write=False)
    return array


def _bytes(array):
    import numpy as np
    return memoryview(np.ascontiguousarray(array)).cast('B')


def _same(data, chunks, digests):
    """Whether these values hash, chunk by chunk, to an accepted array's chunk digests."""
    from itertools import product
    if (len(chunks) != data.ndim or any(type(c) is not int or c < 1 for c in chunks)
            or type(digests) is not bytes):
        return False
    grid = [-(-n // c) for n, c in zip(data.shape, chunks)]
    indices = list(product(*(range(n) for n in grid)))
    if len(digests) != 16 * len(indices):
        return False
    for number, index in enumerate(indices):
        region = tuple(slice(i * c, min((i + 1) * c, n)) for i, c, n in zip(index, chunks, data.shape))
        if hashlib.sha256(_bytes(data[region])).digest()[:16] != digests[16 * number:16 * (number + 1)]:
            return False
    return True


def _part(name, array):
    body = _bytes(array)
    return {'part': name, 'bytes': body.nbytes, 'sha256': hashlib.sha256(body).hexdigest()}


def _missing(value, column, dtype):
    if value is None:
        return {'policy': 'reject', 'representation': 'none'}
    if value == 'nan':
        if dtype[1] != 'f':
            raise ValueError(f'Column {column}: only floating columns mark missing values as NaN')
        return {'policy': 'preserve', 'representation': 'nan'}
    if type(value) is tuple and len(value) == 2 and value[0] == 'sentinel' and type(value[1]) in (int, float):
        return {'policy': 'preserve', 'representation': 'sentinel', 'value': value[1]}
    raise ValueError(f"Column {column}: missing values are None, 'nan' or ('sentinel', value)")


class FamilyDrafts:
    """``families`` in the console: declare, list and discard drafts. The parent stages and saves them."""

    def __init__(self, inputs, input_for, values_for):
        self._inputs = frozenset(inputs)
        self._input_for = input_for  # the granted input an editable layer is bound to, or None
        self._values_for = values_for  # the current values of the layer bound to an input, or None
        self._drafts = {}
        self._made_from = {}  # each draft's inputs, with the SHA-256 of the values their layers held when it was declared

    def _digest(self, name):
        data = self._values_for(name)
        return None if data is None else hashlib.sha256(repr((data.dtype.str, data.shape)).encode() + _bytes(data)).hexdigest()

    def _input(self, value, what):
        if value is None:
            return None
        if type(value) is not str:
            value = self._input_for(value)
            if value is None:
                raise ValueError(f'{what} must be a granted input or a layer bound to one')
        if value not in self._inputs:
            raise ValueError(f'{what} names {value!r}, which is not an input of this session')
        return value

    def _add(self, name, record, arrays):
        _name(name, 'A draft name')
        if name not in self._drafts and len(self._drafts) >= MAX_DRAFTS:
            raise ValueError(f'Keep at most {MAX_DRAFTS} drafts; save or discard some first')
        parts = [_part(part, array) for part, array in arrays.items()]
        others = sum(p['bytes'] for draft, (r, _) in self._drafts.items() if draft != name for p in r['parts'])
        if others + sum(p['bytes'] for p in parts) > MAX_BYTES:
            raise ValueError(f'Drafts are held in memory up to {MAX_BYTES >> 20} MiB together; save or discard some first')
        entry = {'name': name, **record, 'parts': parts}
        listed = [r for draft, (r, _) in self._drafts.items() if draft != name] + [entry]
        if len(json.dumps(listed, separators=(',', ':'))) > MAX_DESCRIPTION:
            raise ValueError('These drafts are described in more than the session allows; save or discard some first')
        made_from = {record[field]: self._digest(record[field]) for field in ('objectSpace', 'region', 'coordinates')
                     if record.get(field) is not None}
        self._drafts[name] = (entry, dict(arrays))
        self._made_from[name] = made_from
        return self.describe(name)

    def table(self, name, columns, *, semantic='object-table', key=None, roles=None, units=None, descriptions=None,
              missing=None, vocabularies=None, references=None, object_space=None, producer='python'):
        """A typed table: ``columns`` maps column names to one-dimensional values (a DataFrame works too)."""
        roles, units, descriptions = dict(roles or {}), dict(units or {}), dict(descriptions or {})
        missing, vocabularies, references = dict(missing or {}), dict(vocabularies or {}), dict(references or {})
        if hasattr(columns, 'columns') and hasattr(columns, 'to_numpy'):  # a DataFrame, such as napari features
            frame, columns = columns, {}
            for label in frame.columns:
                series, column = frame[label], str(label)
                if str(series.dtype) == 'category':  # category codes, with their labels as the class vocabulary
                    codes = series.cat.codes.to_numpy().astype('<i4')
                    columns[column] = codes
                    vocabularies.setdefault(column, dict(enumerate(str(c) for c in series.cat.categories)))
                    if (codes < 0).any():
                        missing.setdefault(column, ('sentinel', -1))
                    roles.setdefault(column, 'feature')
                else:
                    columns[column] = series.to_numpy()
        if type(columns) is not dict or not 0 < len(columns) <= MAX_COLUMNS:
            raise ValueError(f'A table needs one to {MAX_COLUMNS} named columns')
        if semantic not in TABLE_SEMANTICS:
            raise ValueError(f'Choose a table semantic: {", ".join(TABLE_SEMANTICS)}')
        for option in (roles, units, descriptions, missing, vocabularies, references):
            if set(option) - set(columns):
                raise ValueError(f'Options name columns the table does not have: {sorted(set(option) - set(columns))}')
        arrays, declared, rows = {}, [], None
        for column, values in columns.items():
            _name(column, 'A column name')
            array = _frozen(values, f'Column {column}')
            if array.ndim != 1 or (rows is not None and len(array) != rows):
                raise ValueError('Every column needs the same number of one-dimensional values')
            rows = len(array)
            role = roles.get(column, 'key' if column == key else 'relation' if column in references else 'measurement')
            if role not in ROLES:
                raise ValueError(f'Column {column}: choose a key, feature, measurement or relation role')
            vocabulary = vocabularies.get(column)
            if vocabulary is not None:
                vocabulary = [{'id': int(code), 'name': _text(str(label), f'Column {column} class')}
                              for code, label in dict(vocabulary).items()]
            target = references.get(column)
            if target is not None:
                table, target_column = target
                target = {'table': _name(table, 'A referenced table'), 'column': _name(target_column, 'A referenced key')}
            declared.append({'name': column, 'dtype': array.dtype.str, 'role': role,
                             'unit': _text(units.get(column), f'Column {column} unit'),
                             'description': _text(descriptions.get(column), f'Column {column} description'),
                             'missing': _missing(missing.get(column), column, array.dtype.str),
                             'vocabulary': vocabulary, 'references': target})
            arrays[column] = array
        if key is not None and key not in columns:
            raise ValueError(f'The key {key!r} is not a column of this table')
        return self._add(name, {'family': 'table', 'semantic': semantic, 'key': key, 'rows': rows,
                                'objectSpace': self._input(object_space, 'The object space'), 'columns': declared,
                                'producer': {'kind': producer}}, arrays)

    def features(self, name, layer, *, key=None, object_space=None, **options):
        """A layer's napari ``features`` as an object table: how plugins hand Hooke their per-object results.

        Labels keep each row's label value in an ``index`` column, the default key. The object space is the
        input the layer is bound to, when it is bound to one.
        """
        table = getattr(layer, 'features', None)
        if table is None or not hasattr(table, 'columns') or not len(table.columns):
            raise ValueError('This layer has no features to save as a table')
        if key is None and 'index' in table.columns:
            key = 'index'
        if object_space is None and key is not None and self._input_for(layer) is not None:
            object_space = layer
        return self.table(name, table, key=key, object_space=object_space, producer='layer-features', **options)

    def embedding(self, name, matrix, *, table, key, unit=None, region=None, coordinates=None, producer='python'):
        """An ``[object, feature]`` matrix of floating features, row i describing row i of an object table's key."""
        array = _frozen(matrix, 'An embedding')
        if array.ndim != 2 or array.dtype.str not in FLOATS:
            raise ValueError('An embedding is a two-dimensional matrix of floating-point features')
        return self._add(name, {'family': 'embeddings', 'table': _name(table, 'The object table'),
                                'key': _name(key, 'The key column'), 'rows': int(array.shape[0]),
                                'dims': int(array.shape[1]), 'dtype': array.dtype.str,
                                'unit': _text(unit, 'The embedding unit'),
                                'region': self._input(region, 'The region'),
                                'coordinates': self._input(coordinates, 'The coordinates'),
                                'producer': {'kind': producer}}, {'matrix': array})

    def artifact(self, name, content, *, semantic, media_type, file_name, description=None, compatibility=None,
                 producer='python'):
        """A trained artifact or animation document as retained bytes; Hooke never executes or loads it."""
        import numpy as np
        if isinstance(content, str):
            content = content.encode('utf-8')
        if not isinstance(content, (bytes, bytearray, memoryview)):
            raise ValueError('An artifact is bytes or text')
        if semantic not in ARTIFACT_SEMANTICS:
            raise ValueError(f'Choose an artifact semantic: {", ".join(ARTIFACT_SEMANTICS)}')
        if compatibility is not None:
            package, version = compatibility
            compatibility = {'package': _text(package, 'The package', 128), 'version': _text(version, 'The version', 128)}
        body = np.frombuffer(bytes(content), dtype='|u1')
        return self._add(name, {'family': 'artifact', 'semantic': semantic,
                                'mediaType': _text(media_type, 'The media type', 128),
                                'fileName': _text(file_name, 'The file name'),
                                'description': _text(description, 'The description', 512),
                                'compatibility': compatibility, 'producer': {'kind': producer}}, {'content': body})

    def describe(self, name=None):
        if name is not None:
            return dict(self._drafts[name][0])
        return [dict(record) for record, _ in self._drafts.values()]

    def drafts(self):
        return self.describe()

    def discard(self, name):
        self._drafts.pop(name, None)
        self._made_from.pop(name, None)

    def stage(self, names, exchange, *, unchanged=(), base=()):
        """Send each named draft's parts, in declared order, as bounded messages; ``exchange`` sends one.

        ``unchanged`` names each input the drafts refer to, with its accepted chunk layout, and ``base`` its
        accepted chunk digests (16-byte prefixes, grid order). A layer bound to such an input must hold exactly
        those values now and must have held the same values when each draft naming it was declared, or a draft
        would name an object space its values never had. Every check runs before anything is sent.
        """
        if (type(names) is not list or not names or len(set(names)) != len(names)
                or any(name not in self._drafts for name in names)):
            raise ValueError('Save drafts that exist, each once')
        now = {}  # each bound input's values, hashed once for this save
        for name in names:
            record, arrays = self._drafts[name]
            for part in record['parts']:
                if hashlib.sha256(_bytes(arrays[part['part']])).hexdigest() != part['sha256']:
                    raise ValueError(f'Draft {name} changed after it was declared; declare it again')
            for source, digest in self._made_from[name].items():
                if source not in now:
                    now[source] = self._digest(source)
                if now[source] != digest:
                    raise ValueError(f'Input {source} changed after draft {name} was declared from it; save that '
                                     'layer first, then declare the draft again, so it names the values it was made from')
        for spec, digests in zip(unchanged, base, strict=True):
            if (type(spec) is not dict or set(spec) != {'input', 'chunks'} or spec['input'] not in self._inputs
                    or type(spec['chunks']) is not list):
                raise ValueError('An accepted input check names its input and chunk layout')
            data = self._values_for(spec['input'])
            if data is not None and not _same(data, spec['chunks'], digests):
                raise ValueError(f'Input {spec["input"]} has unsaved changes; save that layer first, '
                                 'so these drafts name the values they were made from')
        for name in names:
            record, arrays = self._drafts[name]
            for part in record['parts']:
                body = _bytes(arrays[part['part']])
                count = max(1, -(-body.nbytes // MAX_REGION_BYTES))
                for index in range(count):
                    reply, _ = exchange({'op': 'write-family-part', 'draft': name, 'part': part['part'],
                                         'index': index, 'count': count},
                                        bytes(body[index * MAX_REGION_BYTES:(index + 1) * MAX_REGION_BYTES]))
                    if reply != {'ok': True}:
                        raise ValueError('The parent refused a draft part; nothing was saved')
        return {'ok': True, 'staged': list(names)}

    def close(self):
        self._drafts.clear()
        self._made_from.clear()
