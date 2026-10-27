"""Explicit scientific resource descriptors and narrowly declared compatibility checks.

This module validates metadata only. It does not inspect pixels, infer scientific meaning,
convert resources, grant access, register ownership or dispatch a capability.
Reference revisions are either nonnegative integers for revisioned resources or exact
lowercase SHA256 strings for immutable content. These identities are never coerced or
substituted for one another; accepting a digest does not establish its bytes exist. The same
``validate`` function accepts an input descriptor or an actual returned descriptor. Missing
legacy fields remain unknown; a legacy type name, dtype or shape never supplies a semantic role.

Schema format/version are separate from storage_format/storage_version. Ordered class vocabularies
retain IDs and names. Embeddings can name a feature_model (existing runtime handle plus exact retained
recipe), region and coordinate-map references. Transformation records retain operation, exact inputs,
JSON parameters and declared loss; they are provenance claims to check, not an execution registry.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Mapping

# Descriptor schema identity; storage_format/storage_version describe the actual retained encoding.
FORMAT = 'scientific-resource'
VERSION = 1
MAX_BYTES = 1 << 20
MAX_CONTRACT_BYTES = MAX_BYTES
MAX_CONTRACT_NODES = 32768
MAX_CONTRACT_DEPTH = 32
MAX_SAME_AS = 128
# These are distinct named roles, not a closed list or a hierarchy. Custom roles match exactly.
SEMANTICS = ('intensity', 'probabilities', 'class-labels', 'instance-labels',
             'embeddings', 'object-table', 'lineage-graph')
FIELDS = frozenset(('format', 'version', 'reference', 'semantic', 'dtype', 'axes', 'shape',
                    'calibration', 'timeline', 'channels', 'object_space', 'object_key',
                    'relations', 'sources', 'pyramid', 'level', 'unit', 'storage_format', 'storage_version',
                    'class_vocabulary', 'feature_model', 'region', 'coordinates', 'coordinate_columns',
                    'missing_values', 'transformations'))
IDENTITY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}')
ROLE = re.compile(r'[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*')


class InvalidDescriptor(ValueError):
    def __init__(self, path, why):
        self.path = path
        super().__init__(f'{path}: {why}')


def _bad(path, why):
    raise InvalidDescriptor(path, why)


def _text(value, path):
    if type(value) is not str or not 0 < len(value) <= 256 or not value.strip():
        _bad(path, 'needs a nonempty string of at most 256 characters')


def _integer(value, path, minimum=0):
    if type(value) is not int or not minimum <= value < 2**53:
        _bad(path, f'needs an integer from {minimum} to 2**53 - 1')


def _number(value, path):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        _bad(path, 'needs a finite number')


def _object(value, fields, path, *, required=None):
    if type(value) is not dict or set(value) - set(fields):
        _bad(path, f'needs an object with only {", ".join(sorted(fields))}')
    if required is not None and set(value) != set(required):
        _bad(path, f'needs exactly {", ".join(sorted(required))}')


def _list(value, path, *, maximum=65536):
    if type(value) is not list or len(value) > maximum:
        _bad(path, f'needs a list of at most {maximum} values')


def _names(value, path, *, maximum=65536):
    _list(value, path, maximum=maximum)
    for i, name in enumerate(value):
        _text(name, f'{path}[{i}]')
    if len(set(value)) != len(value):
        _bad(path, 'identities must be unique')


def _reference(value, path):
    _object(value, ('session', 'id', 'revision', 'component'), path)
    for name in ('session', 'id') + (('component',) if 'component' in value else ()):
        item = value.get(name)
        if type(item) is not str or IDENTITY.fullmatch(item) is None:
            _bad(f'{path}.{name}', 'needs an exact resource identity')
    revision = value.get('revision')
    if type(revision) is str:
        if re.fullmatch(r'[0-9a-f]{64}', revision) is None:
            _bad(f'{path}.revision', 'needs an exact lowercase SHA256 content revision or nonnegative integer revision')
    else:
        _integer(revision, f'{path}.revision')


def parse_reference(value) -> dict:
    """Validate identity without resolving access, existence or scientific meaning."""
    _reference(value, 'reference')
    return dict(value)


def _runtime(value, path):
    """The existing bundle/generation/operation reference shape; no registry is created here."""
    fields = ('bundle', 'generation', 'operation')
    _object(value, fields, path, required=fields)
    bundle = value['bundle']
    if (type(bundle) is not str or not 0 < len(bundle) <= 200
            or any(IDENTITY.fullmatch(part) is None for part in bundle.split('/'))):
        _bad(f'{path}.bundle', 'needs an exact bundle identity')
    for name in ('generation', 'operation'):
        item = value[name]
        if type(item) is not str or IDENTITY.fullmatch(item) is None:
            _bad(f'{path}.{name}', 'needs an exact runtime identity')


def _parameters(value, path):
    """Bounded JSON-native recipe metadata, never arrays or executable Python objects."""
    if type(value) is not dict:
        _bad(path, 'parameters must be a JSON object')
    pending, count, characters = [(value, 0)], 0, 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 4096 or depth > 16:
            _bad(path, 'parameters exceed 4096 values or 16 nesting levels')
        if type(item) in (dict, list) and count + len(pending) + len(item) > 4096:
            _bad(path, 'parameters exceed 4096 values')
        if type(item) is dict:
            for key in item:
                _text(key, path)
                characters += len(key)
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            characters += len(item)
        elif item is not None and type(item) is not bool:
            _number(item, path)
        if characters > 65536:
            _bad(path, 'parameter text exceeds 65536 characters; retain bulk data separately')


def _vocabulary(value, path):
    _list(value, path)
    ids = []
    for i, item in enumerate(value):
        at = f'{path}[{i}]'
        _object(item, ('id', 'name'), at, required=('id', 'name'))
        if type(item['id']) is int:
            _integer(item['id'], f'{at}.id', minimum=-(2**53 - 1))
        else:
            _text(item['id'], f'{at}.id')
        _text(item['name'], f'{at}.name')
        ids.append((type(item['id']), item['id']))
    if len(set(ids)) != len(ids):
        _bad(path, 'class IDs must be unique; ordering and names are retained exactly')


def _missing_values(value, path):
    fields = ('policy', 'representation', 'mask', 'value')
    _object(value, fields, path)
    if value.get('policy') not in ('reject', 'preserve', 'omit', 'impute'):
        _bad(f'{path}.policy', 'needs an explicit reject, preserve, omit or impute policy')
    representation = value.get('representation')
    if representation not in ('none', 'nan', 'null', 'mask', 'sentinel'):
        _bad(f'{path}.representation', 'needs none, nan, null, mask or sentinel')
    if representation == 'mask':
        _reference(value.get('mask'), f'{path}.mask')
    elif 'mask' in value:
        _bad(f'{path}.mask', 'a mask reference belongs only to the mask representation')
    if representation == 'sentinel':
        if 'value' not in value or type(value['value']) not in (str, int, float, bool):
            _bad(f'{path}.value', 'a sentinel needs its explicit finite scalar value')
        if type(value['value']) in (int, float):
            _number(value['value'], f'{path}.value')
        elif type(value['value']) is str:
            _text(value['value'], f'{path}.value')
    elif 'value' in value:
        _bad(f'{path}.value', 'a sentinel value belongs only to the sentinel representation')


def _transformations(value, path):
    _list(value, path, maximum=256)
    for i, item in enumerate(value):
        at = f'{path}[{i}]'
        fields = ('operation', 'inputs', 'parameters', 'loss')
        _object(item, fields, at, required=fields)
        _runtime(item['operation'], f'{at}.operation')
        _list(item['inputs'], f'{at}.inputs', maximum=256)
        for j, source in enumerate(item['inputs']):
            _reference(source, f'{at}.inputs[{j}]')
        _parameters(item['parameters'], f'{at}.parameters')
        _object(item['loss'], ('kind', 'details'), f'{at}.loss', required=('kind', 'details'))
        if item['loss']['kind'] not in ('lossless', 'lossy', 'unknown'):
            _bad(f'{at}.loss.kind', 'needs an explicit lossless, lossy or unknown declaration')
        _text(item['loss']['details'], f'{at}.loss.details')


def _calibration(item, path):
    fields = ('space', 'axes', 'units', 'affine')
    _object(item, fields, path, required=fields)
    _reference(item['space'], f'{path}.space')
    _names(item['axes'], f'{path}.axes', maximum=16)
    n = len(item['axes'])
    if not n:
        _bad(path, 'needs at least one calibrated axis')
    _list(item['units'], f'{path}.units', maximum=16)
    for i, unit in enumerate(item['units']):
        _text(unit, f'{path}.units[{i}]')
    _list(item['affine'], f'{path}.affine', maximum=16)
    if len(item['units']) != n or len(item['affine']) != n:
        _bad(path, 'units and affine rows must match the ordered calibrated axes')
    for i, row in enumerate(item['affine']):
        _list(row, f'{path}.affine[{i}]', maximum=17)
        if len(row) != n + 1:
            _bad(f'{path}.affine[{i}]', 'needs one coefficient per axis and one offset')
        for j, number in enumerate(row):
            _number(number, f'{path}.affine[{i}][{j}]')


def _coordinate_columns(item, path):
    """Geometry stored in array values, distinct from calibration of array indices.

    For N points in D dimensions the storage axes can be [object, coordinate].
    The selected coordinate columns carry D spatial/time axes in their declared
    order. Neither N nor the coordinate-column index is a spatial dimension.
    Optional components distinguish vector origins from displacements. Apply the
    full affine transform to origins; apply only its linear part to displacements.
    Missing components retains the original point-coordinate interpretation.
    """
    fields = ('axis', 'columns', 'calibration')
    _object(item, (*fields,'components'), path)
    if not set(fields)<=set(item):
        _bad(path,'needs coordinate axis, columns and calibration')
    if 'components' in item:
        components=item['components']; component_fields=('axis','origin','displacement')
        _object(components,component_fields,f'{path}.components',required=component_fields)
        _text(components['axis'],f'{path}.components.axis')
        for name in ('origin','displacement'):_integer(components[name],f'{path}.components.{name}')
        if components['origin']==components['displacement'] or components['axis']==item['axis']:
            _bad(f'{path}.components','origin and displacement need distinct components on their own storage axis')
    _text(item['axis'], f'{path}.axis')
    _list(item['columns'], f'{path}.columns', maximum=16)
    for i, column in enumerate(item['columns']):
        _integer(column, f'{path}.columns[{i}]')
    if not item['columns'] or len(set(item['columns'])) != len(item['columns']):
        _bad(f'{path}.columns', 'needs distinct coordinate column indices')
    _calibration(item['calibration'], f'{path}.calibration')
    if len(item['columns']) != len(item['calibration']['axes']):
        _bad(f'{path}.columns', 'one column must be declared for each ordered calibrated axis')


def _descriptor(value):
    _object(value, FIELDS, 'descriptor')
    for name, item in value.items():
        if item is None:
            continue  # explicitly unknown, just as an absent field is unknown
        if name == 'format':
            if item != FORMAT:
                _bad(name, f'unsupported descriptor format; expected {FORMAT}')
        elif name == 'version':
            if type(item) is not int or item != VERSION:
                _bad(name, f'unsupported descriptor version; expected {VERSION}')
        elif name in ('reference', 'object_space', 'pyramid', 'region', 'coordinates'):
            _reference(item, name)
        elif name == 'semantic':
            if type(item) is not str or len(item) > 128 or ROLE.fullmatch(item) is None:
                _bad(name, 'needs an exact semantic role identifier')
        elif name in ('dtype', 'unit', 'object_key', 'storage_format', 'storage_version'):
            _text(item, name)
        elif name == 'class_vocabulary':
            _vocabulary(item, name)
        elif name == 'feature_model':
            _object(item, ('runtime', 'recipe'), name, required=('runtime', 'recipe'))
            _runtime(item['runtime'], 'feature_model.runtime')
            _reference(item['recipe'], 'feature_model.recipe')
        elif name == 'missing_values':
            _missing_values(item, name)
        elif name == 'transformations':
            _transformations(item, name)
        elif name == 'axes':
            _names(item, name, maximum=16)
        elif name == 'shape':
            _list(item, name, maximum=16)
            for i, size in enumerate(item):
                _integer(size, f'shape[{i}]')  # zero-sized results can be valid empty outcomes
        elif name == 'level':
            _integer(item, name)
        elif name == 'calibration':
            _calibration(item, name)
        elif name == 'coordinate_columns':
            _coordinate_columns(item, name)
        elif name == 'timeline':
            fields = ('reference', 'axis', 'unit', 'samples')
            _object(item, fields, name, required=fields)
            _reference(item['reference'], 'timeline.reference')
            _text(item['axis'], 'timeline.axis')
            _text(item['unit'], 'timeline.unit')
            _list(item['samples'], 'timeline.samples')
            for i, number in enumerate(item['samples']):
                _number(number, f'timeline.samples[{i}]')
        elif name == 'channels':
            fields = ('axis', 'ids')
            _object(item, fields, name, required=fields)
            _text(item['axis'], 'channels.axis')
            _names(item['ids'], 'channels.ids')
        elif name == 'relations':
            if type(item) is not dict or len(item) > 256:
                _bad(name, 'needs at most 256 named exact resource relationships')
            for role, target in item.items():
                _text(role, 'relations.role')
                _reference(target, f'relations.{role}')
        elif name == 'sources':
            _list(item, name, maximum=256)
            for i, source in enumerate(item):
                _reference(source, f'sources[{i}]')
    reference = value.get('reference')
    if reference is not None:
        for i, transformation in enumerate(value.get('transformations') or ()):
            if reference in transformation['inputs']:
                _bad(f'transformations[{i}].inputs',
                     'a transformed output needs a new exact resource identity or revision, not an input identity')
    axes, shape = value.get('axes'), value.get('shape')
    if axes is not None and shape is not None and len(axes) != len(shape):
        _bad('shape', 'shape rank must match the explicitly ordered axes')
    coordinate_columns = value.get('coordinate_columns')
    if coordinate_columns is not None and axes is not None:
        axis = coordinate_columns['axis']
        if axis not in axes:
            _bad('coordinate_columns.axis', 'coordinate columns must belong to a declared storage axis')
        if shape is not None and any(column >= shape[axes.index(axis)] for column in coordinate_columns['columns']):
            _bad('coordinate_columns.columns', 'coordinate columns must lie inside their storage axis')
        components=coordinate_columns.get('components')
        if components is not None:
            if components['axis'] not in axes:
                _bad('coordinate_columns.components.axis','vector components must belong to a declared storage axis')
            if shape is not None and any(components[key]>=shape[axes.index(components['axis'])] for key in ('origin','displacement')):
                _bad('coordinate_columns.components','vector components must lie inside their storage axis')
    if axes is not None:
        calibration = value.get('calibration')
        if calibration is not None and not set(calibration['axes']) <= set(axes):
            _bad('calibration.axes', 'calibrated axes must belong to this resource')
        for name, samples in (('timeline', 'samples'), ('channels', 'ids')):
            part = value.get(name)
            if part is None:
                continue
            if part['axis'] not in axes:
                _bad(f'{name}.axis', 'axis must belong to this resource')
            if shape is not None and len(part[samples]) != shape[axes.index(part['axis'])]:
                _bad(f'{name}.{samples}', 'count must match its declared axis length')


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _bad('descriptor', f'duplicate JSON object key: {key}')
        value[key] = item
    return value


@dataclass(frozen=True)
class Descriptor:
    """An immutable JSON descriptor. ``wire`` returns a copy, never shared mutable state."""
    _body: bytes = field(repr=False)

    def wire(self) -> dict:
        # Raw construction is possible, so no caller may decode unbounded or ambiguous bytes.
        if type(self._body) is not bytes:
            _bad('descriptor', 'encoded descriptor must be immutable bytes')
        if len(self._body) > MAX_BYTES:
            _bad('descriptor', f'encoded metadata exceeds {MAX_BYTES} bytes')
        try:
            return json.loads(self._body, object_pairs_hook=_unique_object)
        except InvalidDescriptor:
            raise
        except (ValueError, TypeError, RecursionError) as exc:
            raise InvalidDescriptor('descriptor', 'needs valid descriptor JSON') from exc


def parse_descriptor(value: Mapping | Descriptor) -> Descriptor:
    if isinstance(value, Descriptor):
        try:
            value = value.wire()
        except InvalidDescriptor:
            raise
        except (ValueError, TypeError, RecursionError) as exc:
            raise InvalidDescriptor('descriptor', 'needs valid descriptor JSON') from exc
    _descriptor(value)
    try:
        body = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidDescriptor('descriptor', 'needs finite JSON-native metadata') from exc
    if len(body) > MAX_BYTES:
        _bad('descriptor', f'metadata exceeds {MAX_BYTES} bytes; retain bulk data separately')
    return Descriptor(body)


@dataclass(frozen=True)
class SameAs:
    """Only these named fields must equal one named peer's actual descriptor."""
    peer: str
    fields: tuple[str, ...]

    def __post_init__(self):
        _text(self.peer, 'peer')
        fields = tuple(self.fields)
        if not fields or any(name not in FIELDS for name in fields):
            raise ValueError('same-as needs supported descriptor fields')
        object.__setattr__(self, 'fields', fields)


@dataclass(frozen=True)
class Contract:
    """Exact constraints, known-field requirements and explicit relationships; no implicit wildcards.

    ``exact`` uses descriptor wire values, e.g. ``{'semantic': 'instance-labels', 'axes': ['y', 'x']}``.
    Omitting a field leaves it unconstrained; requiring it never treats missing metadata as a match.
    """
    exact: Mapping | Descriptor = field(default_factory=dict)
    known: tuple[str, ...] = ()
    same_as: tuple[SameAs, ...] = ()

    def __post_init__(self):
        known, same_as = tuple(self.known), tuple(self.same_as)
        exact = parse_descriptor(self.exact)
        if any(v is None for v in exact.wire().values()):
            raise ValueError('an exact constraint must be known, not null')
        if any(name not in FIELDS for name in known):
            raise ValueError('known-field requirement names an unsupported descriptor field')
        if any(not isinstance(relation, SameAs) for relation in same_as):
            raise ValueError('same-as constraints must name a peer and fields')
        object.__setattr__(self, 'exact', exact)
        object.__setattr__(self, 'known', known)
        object.__setattr__(self, 'same_as', same_as)

    def wire(self) -> dict:
        """Detached JSON representation; only declaration metadata, never resolved resources."""
        return {'exact': self.exact.wire(), 'known': list(self.known),
                'same_as': [{'peer': relation.peer, 'fields': list(relation.fields)} for relation in self.same_as]}


def _contract_bound(value):
    """Bound JSON-native input before recursive validation, including cycles and encoded text."""
    pending, count, characters = [(value, 0)], 0, 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > MAX_CONTRACT_NODES or depth > MAX_CONTRACT_DEPTH:
            _bad('contract', 'declaration exceeds its value or nesting limit')
        if type(item) in (dict, list):
            if count + len(pending) + len(item) > MAX_CONTRACT_NODES:
                _bad('contract', 'declaration exceeds its value limit')
            if type(item) is dict:
                for key in item:
                    if type(key) is not str:
                        _bad('contract', 'JSON object keys must be strings')
                    characters += len(key)
                children = item.values()
            else:
                children = item
            pending.extend((child, depth + 1) for child in children)
        elif type(item) is str:
            characters += len(item)
        elif item is not None and type(item) is not bool:
            _number(item, 'contract')
        if characters > MAX_CONTRACT_BYTES:
            _bad('contract', f'declaration exceeds {MAX_CONTRACT_BYTES} bytes')
    try:
        body = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError) as exc:
        raise InvalidDescriptor('contract', 'needs finite UTF-8 JSON-native metadata') from exc
    if len(body) > MAX_CONTRACT_BYTES:
        _bad('contract', f'declaration exceeds {MAX_CONTRACT_BYTES} bytes')


def parse_contract(value: Mapping | Contract) -> Contract:
    """Parse the bounded public wire shape: optional exact, known and same_as declarations.

    ``same_as`` is a list of {peer, fields}; peer names must be resolved by the caller.
    All names are strict and duplicate fields are rejected. An omitted constraint means
    unconstrained, never a wildcard or permission to infer missing scientific metadata.
    Existing Python constructors continue to accept iterables; the public wire is JSON only.
    """
    if isinstance(value, Contract):
        value = value.wire()
    _contract_bound(value)
    _object(value, ('exact', 'known', 'same_as'), 'contract')
    exact = value.get('exact', {})
    known = value.get('known', [])
    _names(known, 'contract.known', maximum=len(FIELDS))
    if any(name not in FIELDS for name in known):
        _bad('contract.known', 'names an unsupported descriptor field')
    relations = value.get('same_as', [])
    _list(relations, 'contract.same_as', maximum=MAX_SAME_AS)
    same_as = []
    for i, relation in enumerate(relations):
        at = f'contract.same_as[{i}]'
        _object(relation, ('peer', 'fields'), at, required=('peer', 'fields'))
        _text(relation['peer'], f'{at}.peer')
        _names(relation['fields'], f'{at}.fields', maximum=len(FIELDS))
        if not relation['fields'] or any(name not in FIELDS for name in relation['fields']):
            _bad(f'{at}.fields', 'needs at least one supported descriptor field')
        same_as.append(SameAs(relation['peer'], tuple(relation['fields'])))
    return Contract(exact=exact, known=tuple(known), same_as=tuple(same_as))


@dataclass(frozen=True)
class Mismatch:
    resource: str
    field: str
    code: str
    message: str
    next: str


def _action(name):
    if name in ('semantic', 'class_vocabulary'):
        return 'Choose a resource with the required meaning or run an explicit conversion capability; keep its new identity and provenance.'
    if name in ('object_space', 'object_key', 'relations', 'sources', 'reference', 'region', 'coordinates', 'feature_model'):
        return 'Select the matching exact resource revision or explicitly remap the relationship; do not substitute the newest revision.'
    if name in ('calibration', 'coordinate_columns', 'unit', 'timeline', 'channels', 'axes', 'shape', 'level', 'pyramid'):
        return 'Select matching coordinates and identities or run an explicit reindexing, resampling or calibration capability with a new resource identity.'
    if name in ('storage_format', 'storage_version'):
        return 'Use a resource in the declared storage format/version or run an explicit format conversion preserving a new identity and provenance.'
    if name == 'missing_values':
        return 'Choose the declared missing-value representation and policy; dropping or imputing values requires an explicit transformation.'
    if name == 'transformations':
        return 'Select the exact retained transformation history or explicitly rerun its operation; do not invent provenance or loss claims.'
    return 'Provide a descriptor satisfying this declared constraint; conversion must be an explicit capability.'


def _same(actual, expected):
    # JSON booleans are not numeric recipe values (Python otherwise considers True == 1).
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(_same(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(_same(a, e) for a, e in zip(actual, expected))
    return actual == expected


def _difference(actual, expected, path):
    """First precise mismatch, without returning an entire potentially large coordinate array."""
    if isinstance(actual, dict) and isinstance(expected, dict):
        for key in sorted(set(actual) | set(expected)):
            if key not in actual or key not in expected:
                return f'{path}.{key}', actual.get(key, '<missing>'), expected.get(key, '<missing>')
            if not _same(actual[key], expected[key]):
                return _difference(actual.get(key), expected.get(key), f'{path}.{key}')
    if isinstance(actual, list) and isinstance(expected, list):
        if len(actual) != len(expected):
            return f'{path}.length', len(actual), len(expected)
        for i, (a, e) in enumerate(zip(actual, expected)):
            if not _same(a, e):
                return _difference(a, e, f'{path}[{i}]')
    return path, actual, expected


def _brief(value):
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 120 else text[:117] + '...'


def validate(actual: Mapping | Descriptor, contract: Contract, *, peers: Mapping[str, Mapping | Descriptor] | None = None,
             resource: str = 'resource') -> tuple[Mismatch, ...]:
    """Validate an actual input or returned descriptor against the same explicit contract.

    Success means the declared metadata constraints match. It does not prove payload accuracy,
    scientific suitability, permissions, runtime support or the existence of referenced data.
    """
    try:
        value = parse_descriptor(actual).wire()
    except InvalidDescriptor as exc:
        return (Mismatch(resource, exc.path, 'invalid-descriptor', str(exc),
                         'Correct the producing descriptor from observed source facts before dispatch or accepting the output.'),)
    problems, compared = [], set()

    def compare(name, expected, target):
        if value.get(name) is None or expected is None:
            problems.append(Mismatch(resource, name, 'unknown', f'{name} is unknown on {resource} or {target}',
                                     f'Supply observed {name} metadata for both resources; do not infer it from shape, dtype or a legacy type.'))
        elif not _same(value[name], expected):
            path, got, want = _difference(value[name], expected, name)
            problems.append(Mismatch(resource, path, 'mismatch',
                                     f'{path} is {_brief(got)}; expected {_brief(want)} from {target}', _action(name)))
        compared.add(name)

    for name, expected in contract.exact.wire().items():
        compare(name, expected, 'the declared contract')
    for relation in contract.same_as:
        peer = (peers or {}).get(relation.peer)
        if peer is None:
            problems.append(Mismatch(resource, relation.peer, 'missing-peer', f'Peer {relation.peer} is unavailable',
                                     'Resolve the named peer to its actual exact-revision descriptor before validating this edge.'))
            continue
        try:
            other = parse_descriptor(peer).wire()
        except InvalidDescriptor as exc:
            problems.append(Mismatch(resource, relation.peer, 'invalid-peer', str(exc), 'Correct the peer descriptor from observed facts.'))
            continue
        for name in relation.fields:
            compare(name, other.get(name), f'peer {relation.peer}')
    for name in contract.known:
        if name not in compared and value.get(name) is None:
            problems.append(Mismatch(resource, name, 'unknown', f'{name} is unknown on {resource}',
                                     f'Supply observed {name} metadata; do not infer scientific meaning from storage or shape.'))
    return tuple(problems)
