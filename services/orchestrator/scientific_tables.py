"""Typed scientific tables: object, feature, measurement and relationship records.

A table is a retained resource family of one scientific document. Each column is
one native array component (axis ``row``), so values keep their dtype and byte
order and every byte is verified by the existing array store. The schema in
``state.scientificTables`` names each column's role, unit, missing-value policy
and class vocabulary, the key that identifies objects, the exact object space
those keys belong to, and which key a relation column references. Columns are
named by component, resolved from the document's own resources, so copies and
reopenings keep them. Publication of one or several tables is one Project CAS.
Nothing here infers meaning, converts values or reads a table it was not given.
"""
from __future__ import annotations

import copy
import csv
import io
import math
import re

import numpy as np

from env_worker import native_regions as wire
from .scientific_projects import InvalidProject, _json
from .semantic_resources import InvalidDescriptor, parse_descriptor, parse_reference

FORMAT = 'scientific-table-v1'
COLUMN_SEMANTIC = 'table-column'
# Distinct table roles with rules of their own; another role needs its own rule before it can be stored.
SEMANTICS = ('object-table', 'lineage-graph', 'relation-table')
ROLES = ('key', 'feature', 'measurement', 'relation')
NAME = re.compile(r'[a-z][a-z0-9_]{0,63}')
MAX_TABLES = 16
MAX_COLUMNS = 48
MAX_TEXT = 256
MAX_PAGE = 1000
STATE = 'scientificTables'
SCHEMA_FIELDS = {'format', 'semantic', 'rows', 'key', 'object_space', 'calibration', 'columns', 'producer'}
COLUMN_FIELDS = {'name', 'dtype', 'role', 'unit', 'description', 'missing', 'vocabulary', 'references', 'component'}
# What the array store records about every component itself, beside the column's own descriptor.
OBSERVED = {'reference', 'shape', 'dtype', 'axes', 'storage_format', 'storage_version'}


def component(table, column):
    return f'array.table.{table}.{column}'


def _text(value, what, *, optional=True):
    if value is None and optional:
        return None
    if type(value) is not str or not 0 < len(value) <= MAX_TEXT or not value.strip():
        raise InvalidProject(f'{what} needs text of at most {MAX_TEXT} characters')
    return value


def _descriptor(column):
    """The array descriptor of one column: the shared resource contract checks its parts."""
    value = {'semantic': COLUMN_SEMANTIC, 'missing_values': column['missing']}
    if column['unit'] is not None:
        value['unit'] = column['unit']
    if column['vocabulary'] is not None:
        value['class_vocabulary'] = column['vocabulary']
    try:
        return parse_descriptor(value).wire()
    except InvalidDescriptor as error:
        raise InvalidProject(f'Column {column["name"]}: {error}') from error


def schema(value, *, name):
    """A complete table declaration, checked without reading any values."""
    if type(name) is not str or not NAME.fullmatch(name):
        raise InvalidProject('A table name uses lowercase letters, digits and underscores')
    if type(value) is not dict or set(value) != SCHEMA_FIELDS or value['format'] != FORMAT:
        raise InvalidProject(f'Table {name} needs a complete {FORMAT} declaration')
    if value['semantic'] not in SEMANTICS:
        raise InvalidProject(f'Table {name}: choose an object, lineage or relation table')
    if type(value['rows']) is not int or not 0 <= value['rows'] < 2**53:
        raise InvalidProject(f'Table {name} needs its exact row count')
    columns = value['columns']
    if type(columns) is not list or not 0 < len(columns) <= MAX_COLUMNS:
        raise InvalidProject(f'Table {name} needs one to {MAX_COLUMNS} columns')
    names = []
    for column in columns:
        if type(column) is not dict or set(column) != COLUMN_FIELDS:
            raise InvalidProject(f'Table {name}: every column needs its complete declaration')
        if type(column['name']) is not str or not NAME.fullmatch(column['name']):
            raise InvalidProject(f'Table {name}: column names use lowercase letters, digits and underscores')
        names.append(column['name'])
        if column['dtype'] not in wire.DTYPES:
            raise InvalidProject(f'Column {column["name"]} needs a supported scalar type with its byte order')
        if column['role'] not in ROLES:
            raise InvalidProject(f'Column {column["name"]} needs a key, feature, measurement or relation role')
        _text(column['unit'], f'Column {column["name"]} unit')
        _text(column['description'], f'Column {column["name"]} description')
        if column['component'] != component(name, column['name']):
            raise InvalidProject(f'Column {column["name"]} must name its own table component')
        missing = column['missing']
        if type(missing) is not dict or missing.get('representation') not in ('none', 'nan', 'sentinel'):
            raise InvalidProject(f'Column {column["name"]}: declare missing values as none, nan or a sentinel')
        kind = np.dtype(column['dtype']).kind
        if missing['representation'] == 'nan' and kind != 'f':
            raise InvalidProject(f'Column {column["name"]}: only floating columns can mark missing values as NaN')
        if missing['representation'] == 'sentinel':
            sentinel = missing.get('value')
            if (type(sentinel) not in (int, float) or type(sentinel) is bool
                    or kind in 'iu' and (type(sentinel) is not int
                        or not np.iinfo(column['dtype']).min <= sentinel <= np.iinfo(column['dtype']).max)):
                raise InvalidProject(f'Column {column["name"]}: a sentinel must be a value of the column type')
        if column['vocabulary'] is not None and (kind not in 'iu' or column['role'] == 'key'):
            raise InvalidProject(f'Column {column["name"]}: a class vocabulary needs integer category codes')
        references = column['references']
        if (column['role'] == 'relation') != (references is not None):
            raise InvalidProject(f'Column {column["name"]}: a relation column, and only it, names the key it references')
        if references is not None and (type(references) is not dict or set(references) != {'table', 'column'}
                or type(references['table']) is not str or not NAME.fullmatch(references['table'])
                or type(references['column']) is not str or not NAME.fullmatch(references['column'])):
            raise InvalidProject(f'Column {column["name"]}: a relation names one table and its key column')
        if references is not None and kind not in 'iu':
            raise InvalidProject(f'Column {column["name"]}: relations reference integer keys')
        _descriptor(column)
    if len(set(names)) != len(names):
        raise InvalidProject(f'Table {name}: column names must be distinct')
    keys = [c['name'] for c in columns if c['role'] == 'key']
    if keys != ([value['key']] if value['key'] is not None else []):
        raise InvalidProject(f'Table {name}: declare its key column, and only that column, with the key role')
    if value['key'] is not None:
        key = next(c for c in columns if c['name'] == value['key'])
        if np.dtype(key['dtype']).kind not in 'iu' or key['missing']['representation'] != 'none':
            raise InvalidProject(f'Table {name}: keys are integers with no missing values')
    if value['semantic'] == 'object-table' and value['key'] is None:
        raise InvalidProject(f'Table {name}: an object table needs the key that identifies its objects')
    if value['semantic'] == 'lineage-graph':
        relations = [c for c in columns if c['role'] == 'relation']
        if len(relations) != 2 or {c['name'] for c in relations} != {'child', 'parent'} \
                or len({(c['references']['table'], c['references']['column']) for c in relations}) != 1:
            raise InvalidProject(f'Table {name}: a lineage graph has child and parent columns referencing one key')
    if value['object_space'] is not None:
        try:
            parse_reference(value['object_space'])
        except InvalidDescriptor as error:
            raise InvalidProject(f'Table {name}: {error}') from error
        if value['key'] is None:
            raise InvalidProject(f'Table {name}: an object space is identified by a key column')
    if value['calibration'] is not None:
        try:
            parse_descriptor({'calibration': value['calibration']})
        except InvalidDescriptor as error:
            raise InvalidProject(f'Table {name}: {error}') from error
    if value['producer'] is not None and (type(value['producer']) is not dict or len(_json(value['producer'])) > 16384):
        raise InvalidProject(f'Table {name}: its producer is a bounded record')
    return copy.deepcopy(value)


def declare(*, semantic, columns, key=None, object_space=None, calibration=None, producer=None):
    """Fill the parts a producer does not choose (format, components, empty optional fields)."""
    filled = []
    for column in columns:
        unknown = set(column) - (COLUMN_FIELDS - {'component'})
        if unknown:
            raise InvalidProject(f'Unknown column fields: {sorted(unknown)}')
        filled.append({'unit': None, 'description': None, 'missing': {'policy': 'reject', 'representation': 'none'},
                       'vocabulary': None, 'references': None, **column})
    return {'format': FORMAT, 'semantic': semantic, 'key': key, 'object_space': object_space,
            'calibration': calibration, 'producer': producer, 'columns': filled}


def _check_values(name, table, values, keys_of):
    """Every value against its declaration; ``keys_of(table, column)`` gives a referenced key set."""
    present_of = {}
    for column in table['columns']:
        data = values[column['name']]
        if type(data) is not np.ndarray or data.ndim != 1 or data.dtype.str != column['dtype'] or len(data) != table['rows']:
            raise InvalidProject(f'Column {column["name"]} needs {table["rows"]} values of type {column["dtype"]}')
        missing = column['missing']
        present = np.ones(len(data), dtype=bool)
        if data.dtype.kind == 'f':
            nan = np.isnan(data)
            if missing['representation'] != 'nan' and nan.any():
                raise InvalidProject(f'Column {column["name"]} has NaN values but does not declare NaN as missing')
            if np.isinf(data).any():
                raise InvalidProject(f'Column {column["name"]} has infinite values')
            present &= ~nan
        if missing['representation'] == 'sentinel':
            present &= data != missing['value']
        if column['vocabulary'] is not None:
            codes = {row['id'] for row in column['vocabulary'] if type(row['id']) is int}
            if not np.isin(data[present], np.array(sorted(codes), dtype=data.dtype) if codes else data[:0]).all():
                raise InvalidProject(f'Column {column["name"]} has a category code outside its vocabulary')
        if column['role'] == 'key' and len(np.unique(data)) != len(data):
            raise InvalidProject(f'Table {name}: key {column["name"]} has repeated values')
        if column['references'] is not None:
            target = keys_of(column['references']['table'], column['references']['column'])
            if target is None:
                raise InvalidProject(f'Column {column["name"]} references a key that is not a declared table key')
            # Same type as the key, so no identity is compared through a lossy conversion.
            if target.dtype.str != data.dtype.str:
                raise InvalidProject(f'Column {column["name"]} must have the type of the key it references')
            if not np.isin(data[present], target).all():
                raise InvalidProject(f'Column {column["name"]} references objects that its key does not contain')
            present_of[column['name']] = present
    if table['semantic'] == 'lineage-graph':
        both = present_of['child'] & present_of['parent']
        if np.any(values['child'][both] == values['parent'][both]):
            raise InvalidProject(f'Table {name}: an object cannot be its own parent')


def publish(store, saved, tables, *, replace=(), state=None, transaction=None, check=lambda: None):
    """Add or replace tables of one exact saved revision in one CAS; every value is checked first.

    ``tables`` maps names to ``(declaration, values)``. A name that exists needs ``replace``.
    ``state`` adds the producer's own records (never a family's record) to the same revision.
    """
    from .scientific_families import publish as publish_families
    return publish_families(store, saved, tables=tables, replace=replace, state=state,
                            transaction=transaction, check=check)


def prepare(store, saved, tables, *, replace=(), check=lambda: None):
    """Check and write the columns of new tables; the family transaction accepts them in one CAS.

    Returns the components replaced, the new references, the state record and, for other families,
    the declared tables with their values.
    """
    from .scientific_families import Prepared, write_array
    document = saved.document
    existing = document['state'].get(STATE, {})
    if type(existing) is not dict:
        raise InvalidProject('This revision has an unsupported table record')
    if type(tables) is not dict or not tables or len(set(existing) | set(tables)) > MAX_TABLES:
        raise InvalidProject(f'Publish one to {MAX_TABLES} tables per document')
    for name in tables:
        if name in existing and name not in replace:
            raise InvalidProject(f'Table {name} already exists; replace it explicitly')
    declared = {}
    for name, item in tables.items():
        if type(item) is not tuple or len(item) != 2:
            raise InvalidProject(f'Table {name} needs its declaration and values')
        declaration, values = item
        if type(declaration) is not dict or type(declaration.get('columns')) is not list:
            raise InvalidProject(f'Table {name} needs its declaration')
        if type(values) is not dict or set(values) != {c.get('name') for c in declaration['columns'] if type(c) is dict}:
            raise InvalidProject(f'Table {name} needs values for exactly its declared columns')
        rows = {len(v) for v in values.values() if type(v) is np.ndarray and v.ndim == 1}
        if len(rows) != 1:
            raise InvalidProject(f'Table {name}: every column needs the same number of one-dimensional values')
        body = {**declaration, 'rows': rows.pop(),
                'columns': [{**c, 'component': component(name, c['name'])} for c in declaration['columns']]}
        declared[name] = schema(body, name=name)
    resolved = {}

    def keys_of(table, column):
        if table in declared:
            return tables[table][1][column] if declared[table]['key'] == column else None
        if table in existing and existing[table].get('key') == column:
            if (table, column) not in resolved:
                resolved[(table, column)] = open_table(store, saved.id, saved.session, saved.revision, table).read(column)
            return resolved[(table, column)]
        return None

    for name, table in declared.items():
        check()
        _check_values(name, table, tables[name][1], keys_of)
    for other, table in existing.items():
        if other not in tables and any(c['references'] and c['references']['table'] in tables
                                       for c in table['columns']):
            raise InvalidProject(f'Table {other} references a table being replaced; publish them together')
    replaced = {c['component'] for name in tables if name in existing for c in existing[name]['columns']}
    references = []
    for name, table in declared.items():
        for column in table['columns']:
            check()
            references.append(write_array(store, saved, column['component'], tables[name][1][column['name']],
                                          ['row'], _descriptor(column)))
    return Prepared(STATE, replaced, references, {**existing, **declared},
                    {name: (table, tables[name][1]) for name, table in declared.items()})


class Table:
    """One validated table of an exact accepted revision, read a column or a page at a time."""

    def __init__(self, name, schema_, sources):
        self.name, self.schema, self.sources = name, schema_, sources

    @property
    def rows(self):
        return self.schema['rows']

    def read(self, column, start=0, stop=None):
        stop = self.rows if stop is None else stop
        if column not in self.sources or not 0 <= start <= stop <= self.rows:
            raise InvalidProject('Choose a declared column and rows inside the table')
        source = self.sources[column]
        # Filled in place: np.concatenate would return native byte order, losing the retained dtype.
        out = np.empty(stop - start, dtype=source.dtype)
        step = source.chunks[0] if source.chunks else max(1, stop - start)
        position = start
        while position < stop:
            end = min(stop, (position // step + 1) * step)
            out[position - start:end - start] = source.read((slice(position, end),))
            position = end
        return out

    def page(self, offset, limit):
        """JSON-safe rows: integers beyond 2**53 as decimal text, missing values as null."""
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 0 < limit <= MAX_PAGE:
            raise InvalidProject(f'Request up to {MAX_PAGE} rows from a nonnegative offset')
        stop = min(self.rows, offset + limit)
        columns = [self.read(c['name'], min(offset, stop), stop) for c in self.schema['columns']]
        return [[_cell(column, value) for column, value in zip(self.schema['columns'], row)]
                for row in zip(*columns)] if columns and stop > offset else []

    def csv(self, check=lambda: None, block=4096):
        """The whole table as UTF-8 CSV, exact integers and round-trip floats, a block of rows at a time."""
        heading = io.StringIO(newline='')
        csv.writer(heading).writerow([c['name'] for c in self.schema['columns']])
        yield heading.getvalue().encode('utf-8')
        for start in range(0, self.rows, block):
            check()
            columns = [self.read(c['name'], start, min(self.rows, start + block)) for c in self.schema['columns']]
            out = io.StringIO(newline='')
            writer = csv.writer(out)
            for row in zip(*columns):
                writer.writerow(['' if cell is None else cell for cell in
                                 (_cell(column, value) for column, value in zip(self.schema['columns'], row))])
            yield out.getvalue().encode('utf-8')


def _cell(column, value):
    if column['missing']['representation'] == 'sentinel' and value == column['missing']['value']:
        return None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        number = int(value)
        return number if abs(number) <= 2**53 else str(number)
    number = float(value)
    return None if math.isnan(number) else number


def tables(document):
    value = document['state'].get(STATE, {})
    if type(value) is not dict or len(value) > MAX_TABLES:
        raise InvalidProject('This revision has an unsupported table record')
    return value


def open_table(store, project, session, revision, name):
    """Resolve and check one table against its retained column components."""
    saved = store.read(project, session, revision=revision)
    declared = tables(saved.document)
    if name not in declared:
        raise InvalidProject(f'This saved revision has no table named {name}')
    table = schema(declared[name], name=name)
    by_component = {ref.get('component'): ref for ref in saved.document['resources']
                    if ref.get('id') == project and ref.get('session') == session}
    sources = {}
    for column in table['columns']:
        reference = by_component.get(column['component'])
        if reference is None:
            raise InvalidProject(f'Table {name} is missing its {column["name"]} column; the revision was kept unchanged')
        source = store.array_source(project, session, revision, reference)
        described = {k: v for k, v in source.descriptor.items() if k not in OBSERVED}
        if (list(source.shape) != [table['rows']] or source.dtype != column['dtype'] or list(source.axes) != ['row']
                or described != _descriptor(column)):
            raise InvalidProject(f'Column {column["name"]} of table {name} disagrees with its retained values')
        sources[column['name']] = source
    return Table(name, table, sources)


def verify(store, project, session, revision, name, *, check=lambda: None):
    """Every retained value against its declaration, including keys and relations; returns the table."""
    table = open_table(store, project, session, revision, name)
    values = {c['name']: table.read(c['name']) for c in table.schema['columns']}
    check()

    def keys_of(other, column):
        if other == name:
            return values[column] if table.schema['key'] == column else None
        target = open_table(store, project, session, revision, other)
        return target.read(column) if target.schema['key'] == column else None

    _check_values(name, table.schema, values, keys_of)
    return table


def remap(state, mapping):
    """A copy's tables and measurement record name the copied object space; columns follow their components.

    ``mapping`` is the plain copy of each accepted component, so an object space that an edit replaced
    keeps naming the bytes that were measured, and the measurements read as belonging to earlier labels.
    """
    records = [table for table in (state.get(STATE) or {}).values() if type(table) is dict]
    for table in records:
        if type(table.get('object_space')) is dict:
            table['object_space'] = mapping.get(_json(table['object_space']), table['object_space'])
    measured = state.get('scientificMeasurements')
    source = measured.get('source') if type(measured) is dict else None
    if type(source) is dict and type(source.get('reference')) is dict:
        source['reference'] = mapping.get(_json(source['reference']), source['reference'])
