"""Format-3 Python bundle declarations, without discovery, installation or execution.

Owners remain catalog entries. A CapabilityView is only a declaration for the existing
capability door; it is never an Installed owner or an environment lifecycle object.
Callers must resolve and admit the owner's installed generation separately.
"""
from __future__ import annotations

import json
import keyword
import math
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manifests import Input, Manifest, Output

MAX_MEMBERS = 64
MAX_BYTES = 1 << 20
OWNER_PART = re.compile(r'[a-z0-9][a-z0-9_-]{0,127}')
MEMBER_ID = re.compile(r'[a-z][a-z0-9-]{1,40}')
PYTHON_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
FIELDS = frozenset(('id', 'name', 'adapter', 'operation', 'inputs', 'outputs', 'hidden', 'outcomes',
                    'tests', 'category', 'description', 'search_aliases', 'experimental', 'what',
                    'reference_cases', 'scientific', 'bridge', 'reader', 'resources', 'reader_checks'))


def capability_key(owner_key: str, member_id: str) -> str:
    parts = owner_key.split('/') if type(owner_key) is str else []
    if (not 1 <= len(parts) <= 2 or any(OWNER_PART.fullmatch(part) is None for part in parts)
            or type(member_id) is not str or MEMBER_ID.fullmatch(member_id) is None):
        raise ValueError('a capability key needs an exact owner key and member id')
    return owner_key + '#' + member_id


def parse_capability_key(key: str) -> tuple[str, str]:
    if type(key) is not str or key.count('#') != 1:
        raise ValueError('a member key is owner#member, with one exact separator')
    owner, member = key.split('#')
    capability_key(owner, member)
    return owner, member


def _json(value) -> bytes:
    pending, count, characters = [(value, 0)], 0, 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 16 or count > 32768:
            raise ValueError('bundle declarations exceed their nesting or value limit')
        if type(item) in (dict, list):
            if count + len(pending) + len(item) > 32768:
                raise ValueError('bundle declarations have too many values')
            if type(item) is dict:
                if any(type(key) is not str for key in item):
                    raise ValueError('declaration keys must be strings')
                characters += sum(len(key) for key in item)
                children = item.values()
            else:
                children = item
            pending.extend((child, depth + 1) for child in children)
        elif type(item) is str:
            characters += len(item)
        elif item is not None and type(item) is not bool:
            try:
                finite = type(item) in (int, float) and math.isfinite(item)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError('declarations must be finite JSON-native values')
        if characters > MAX_BYTES:
            raise ValueError('bundle declaration text exceeds its size limit')
    try:
        body = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError('declarations must be finite JSON-native values') from exc
    if len(body) > MAX_BYTES:
        raise ValueError('bundle declarations exceed their byte limit')
    return body


def _text(value, name, limit, *, required=False):
    if type(value) is not str or len(value) > limit or (required and not value.strip()):
        raise ValueError(f'{name} must be {"nonempty " if required else ""}text of at most {limit} characters')


@dataclass(frozen=True)
class Member:
    id: str
    name: str
    adapter: str
    operation: str
    inputs: tuple[Input, ...]
    outputs: tuple[Output, ...]
    hidden: tuple[str, ...]
    category: str
    description: str
    search_aliases: tuple[str, ...]
    experimental: bool
    what: str
    _body: bytes = field(repr=False)

    def wire(self) -> dict:
        """Detached JSON-native declaration, also the owner digest's member representation."""
        return json.loads(self._body)

    @property
    def outcomes(self) -> dict:
        return self.wire()['outcomes']

    @property
    def tests(self) -> tuple[dict, ...]:
        return tuple(self.wire()['tests'])

    @property
    def reference_cases(self) -> tuple[dict, ...]:
        """Exact checked configurations; a declaration alone establishes no runtime proof."""
        return tuple(self.wire().get('reference_cases', ()))

    @property
    def scientific(self) -> dict:
        return self.wire().get('scientific', {})

    @property
    def bridge(self) -> str:
        return self.wire().get('bridge', '')

    @property
    def reader(self) -> dict:
        return self.wire().get('reader', {})

    @property
    def reader_checks(self):
        return self.wire().get('reader_checks', {})

    @property
    def resources(self) -> dict:
        return self.wire().get('resources', {})

    @property
    def params(self):
        return tuple(port for port in self.inputs if port.widget)

    @property
    def data_inputs(self):
        return tuple(port for port in self.inputs if not port.widget)


def _ports(raw):
    from . import manifests as catalog
    problems = []
    ports = []
    for name, cls, keys in (('inputs', catalog.Input, catalog.INPUT_KEYS),
                            ('outputs', catalog.Output, catalog.OUTPUT_KEYS)):
        items = raw[name]
        if type(items) is not list or len(items) > 128 or (name == 'outputs' and not items):
            raise ValueError(f'{name} must be a bounded list of ports; outputs cannot be empty')
        for item in items:
            if type(item) is not dict:
                raise ValueError('each port must be a table')
            for key in ('optional', 'advanced', 'multiline', 'agent'):
                if key in item and type(item[key]) is not bool:
                    raise ValueError(f'{key} must be a boolean')
            for key in ('min', 'max', 'step'):
                if item.get(key) is not None and type(item[key]) not in (int, float):
                    raise ValueError(f'{key} must be a finite number')
            for key in ('label', 'tooltip', 'unit'):
                if key in item:
                    _text(item[key], key, 1000)
            if 'options' in item and (type(item['options']) is not list or len(item['options']) > 256
                                     or any(type(v) not in (str, int, float) for v in item['options'])):
                raise ValueError('options must be a bounded list of scalar choices')
            if item.get('default') is not None and type(item['default']) not in (str, int, float, bool):
                raise ValueError('a parameter default must be a scalar')
        result = catalog._ports(items, cls, keys, name, problems)
        for port in result:
            if not catalog.INPUT_ID.fullmatch(port.id) or not catalog.TYPE.fullmatch(port.type):
                problems.append('ports need canonical ids and types')
            if name == 'outputs' and port.type in catalog.WIDGETS:
                problems.append('a widget type cannot be an output')
            if name == 'inputs':
                if port.widget:
                    problems.extend(catalog._param_problems(port))
                    if port.id in catalog.HIDDEN:
                        problems.append('a hidden input cannot be a parameter')
                elif port.default is not None or port.options or any(v is not None for v in (port.min, port.max, port.step)):
                    problems.append('data inputs cannot have parameter defaults or ranges')
        ports.append(result)
    ids = [p.id for group in ports for p in group]
    if len(ids) != len(set(ids)):
        problems.append('input and output ids must be unique')
    if problems:
        raise ValueError('; '.join(problems))
    return ports


def _scientific_family(member):
    if (len(member.data_inputs) != 1 or member.data_inputs[0].type != 'image.2d.rgb8'
            or len(member.outputs) != 1
            or member.outputs[0].type not in {'image.2d.rgb8', 'table.objects', 'text', 'points.2d'}):
        raise ValueError('scientific ports currently require one RGB image input and one image, table, text or points output')


def _bridge(data, member):
    bridge = data.get('bridge')
    if bridge == 'regions-v1':
        if not member.scientific:
            raise ValueError('regions-v1 requires declared scientific ports')
        return
    if bridge == 'scientific-host-v1':
        if (len(member.inputs) != 1 or member.inputs[0].type != 'project.scientific'
                or member.inputs[0].optional or len(member.outputs) != 1
                or member.outputs[0].type != 'project.scientific' or member.hidden
                or any(data.get(field) for field in ('scientific', 'reference_cases', 'tests'))):
            raise ValueError('scientific-host-v1 needs one required scientific Project input and output; '
                             'RGB examples, hidden parameters and ordinary operation proof do not qualify a host')
        return
    raise ValueError('Unsupported resource bridge; use regions-v1 or scientific-host-v1 with their declared ports')


# The native facts a reader can declare it keeps exactly (format 3). Hooke opens scientific
# sources only with readers that declare every one of them (orchestrator/scientific_readers).
READER_KEEPS=('values','dtype','byte-order','axes','calibration')
READER_KINDS=('file','directory','stack')
_PATTERN=re.compile(r'[a-z0-9*?._-]{1,64}')


def _samples(items,reader):
    """Sample data this reader opens (napari's sample_data): a pinned https file, never a command."""
    if reader['kind']!='file' or type(items) is not list or not 1<=len(items)<=16:return False
    ids=set()
    for item in items:
        if (type(item) is not dict or not {'id','name','url','sha256','size'}<=set(item)<={'id','name','url','sha256','size','licence'}
                or type(item['id']) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',item['id']) or item['id'] in ids
                or type(item['name']) is not str or not 0<len(item['name'].strip())<=120
                or type(item['url']) is not str or not item['url'].startswith('https://') or len(item['url'])>2048
                or any(ord(c)<=32 or ord(c)==127 for c in item['url'])
                or type(item['sha256']) is not str or not re.fullmatch(r'[0-9a-f]{64}',item['sha256'])
                or type(item['size']) is not int or isinstance(item['size'],bool) or not 0<item['size']<=reader['maximum_bytes']
                or ('licence' in item and (type(item['licence']) is not str or not 0<len(item['licence'])<=120))):
            return False
        ids.add(item['id'])
    return True


def _reader_v3(value):
    """napari-style declared capability: name patterns, the kind of source and what stays exact."""
    allowed={'format','input','kind','patterns','maximum_bytes','keeps','requires','entries','samples','access'}
    def globs(items,most):
        return (type(items) is list and 1<=len(items)<=most and len(set(items))==len(items)
                and all(type(item) is str and _PATTERN.fullmatch(item) for item in items))
    def path(item):
        return (type(item) is str and 0<len(item)<=256 and '\\' not in item and ':' not in item
                and not any(ord(c)<32 or ord(c)==127 for c in item)
                and not any(part in ('','.','..') for part in item.split('/')))
    if (not {'format','input','kind','patterns','maximum_bytes','keeps'}<=set(value)<=allowed
            or value['kind'] not in READER_KINDS
            or type(value['input']) is not str or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',value['input'])
            or ('access' in value and value['access'] != 'regional')
            or type(value['maximum_bytes']) is not int
            or not 0<value['maximum_bytes'] <= (64*(1<<30) if value.get('access')=='regional' else 64*(1<<20))
            or not globs(value['patterns'],32)
            or type(value['keeps']) is not list or len(set(value['keeps']))!=len(value['keeps'])
            or any(item not in READER_KEEPS for item in value['keeps'])
            or ('requires' in value and (value['kind']=='file' or type(value['requires']) is not list
                                         or not 1<=len(value['requires'])<=16
                                         or len(set(value['requires']))!=len(value['requires'])
                                         or not all(path(item) for item in value['requires'])))
            or ('entries' in value and (value['kind']=='file' or not globs(value['entries'],16)))
            or ('samples' in value and not _samples(value['samples'],value))):
        raise ValueError('A format-3 reader needs scientific-host-v1, a named source input, a kind (file, folder or stack), '
                         'bounded lowercase name patterns, maximum_bytes within the source-read allowance, the native '
                         'facts it keeps exact ('+', '.join(READER_KEEPS)+') and, for samples, pinned https files')


def _reader(data, member):
    """A bounded private reader declaration, never inferred from a repository name."""
    value=data['reader']
    if member.bridge=='scientific-host-v1' and type(value) is dict and value.get('format')==3 and type(value.get('format')) is int:
        _reader_v3(value);return
    if (member.bridge!='scientific-host-v1' or type(value) is not dict
            or set(value) not in ({'format','input','extensions','maximum_bytes'}, {'format','input','extensions','maximum_bytes','kind'})
            or type(value['format']) is not int
            or not ((value['format']==1 and 'kind' not in value) or (value['format']==2 and value.get('kind')=='directory'))
            or type(value['input']) is not str or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',value['input'])
            or type(value['maximum_bytes']) is not int or not 0<value['maximum_bytes']<=64*(1<<20)
            or type(value['extensions']) is not list or not 1<=len(value['extensions'])<=32
            or any(type(suffix) is not str or not re.fullmatch(r'\.[a-z0-9][a-z0-9.]{0,31}',suffix)
                   or suffix.endswith('.') or '..' in suffix for suffix in value['extensions'])
            or len(set(value['extensions']))!=len(value['extensions'])):
        raise ValueError('A private reader needs scientific-host-v1, a named source input, bounded suffixes and maximum_bytes within the source-read allowance')


def _resources(data, member):
    value = data['resources']
    if (member.bridge != 'scientific-host-v1' or type(value) is not dict
            or set(value) != {'format', 'memory_mb'}
            or type(value['format']) is not int or value['format'] != 1
            or type(value['memory_mb']) is not int or not 1024 <= value['memory_mb'] <= 8192):
        raise ValueError('Scientific resources format 1 requires scientific-host-v1 and integer memory_mb from 1024 to 8192 MiB')


def _scientific(raw, member):
    from .semantic_resources import parse_contract
    _scientific_family(member)
    if type(raw) is not dict or set(raw) != {'inputs', 'outputs'}:
        raise ValueError('scientific must contain inputs and outputs port contracts')
    inputs = {port.id for port in member.data_inputs}
    result = {}
    for side, ports in (('inputs', member.data_inputs), ('outputs', member.outputs)):
        declarations = raw[side]
        if type(declarations) is not dict or set(declarations) != {port.id for port in ports}:
            raise ValueError(f'scientific.{side} must name exactly the declared data ports')
        result[side] = {}
        for name, value in declarations.items():
            contract = parse_contract(value)
            peers = inputs - {name} if side == 'inputs' else inputs
            if any(relation.peer not in peers for relation in contract.same_as):
                raise ValueError(f'scientific.{side}.{name} has an unknown or unsupported peer')
            result[side][name] = contract.wire()
    return result


# Types a typed member's ports may take (orchestrator/admission.py binds them at Run); their reference inputs are .npy.
TYPED_INPUTS = ('image.2d.rgb8', 'image.2d.gray8', 'image.2d.gray16', 'image.3d.zyx.float32', 'labels.instance.2d', 'masks.2d')


def typed(member) -> bool:
    """A member whose data inputs are bound at Run as native arrays (several, or other than the view's picture)."""
    ports = [port for port in member.inputs if not port.widget]  # its data inputs (manifests read the same way)
    return bool(ports) and (len(ports) > 1 or ports[0].type != 'image.2d.rgb8') and all(p.type in TYPED_INPUTS for p in ports)


def _example(path, suffixes, what):
    if (type(path) is not str or len(path) > 256 or PurePosixPath(path).name != path or '\\' in path
            or '\x00' in path or not path.startswith('example') or not path.lower().endswith(suffixes)):
        raise ValueError(what)


def _reference_cases(raw, member):
    from . import manifests as catalog
    if type(raw) is not list or not 1 <= len(raw) <= 64:
        raise ValueError('reference_cases must contain between 1 and 64 cases')
    formats = {'image.2d.rgb8': '.png', 'table.objects': '.json', 'text': '.json', 'points.2d': '.json'}
    if len(member.outputs) != 1 or member.outputs[0].type not in formats:
        raise ValueError('reference checks currently require one supported output: image.2d.rgb8, table.objects, text or points.2d')
    suffix = formats[member.outputs[0].type]
    defaults, problems = catalog.params_given(member, {})
    if problems:
        raise ValueError('reference checks require valid parameter defaults: ' + '; '.join(problems))
    result = []
    many = typed(member)
    given = 'inputs' if many else 'input'
    for case in raw:
        if (type(case) is not dict or set(case) - {given, 'reference', 'source', 'params', 'tolerance', 'device', 'scientific'}
                or not {given, 'reference', 'source', 'device'} <= set(case)):
            raise ValueError(f'a reference case needs {given}, reference, source and device; only params, tolerance and scientific are optional')
        if many:  # one exact native example per port, beside the manifest (H01 slice d)
            ports = case['inputs']
            if type(ports) is not dict or set(ports) != {port.id for port in member.data_inputs}:
                raise ValueError('a typed reference case names one example .npy file for each data input')
            for path in ports.values():
                _example(path, ('.npy',), 'typed reference inputs must be example*.npy filenames beside the owner manifest')
        else:
            _example(case['input'], catalog.EXAMPLE_SUFFIXES, 'reference input must be an example image filename beside the owner manifest')
        reference = case['reference']
        if (type(reference) is not str or len(reference) > 256 or PurePosixPath(reference).name != reference
                or '\\' in reference or '\x00' in reference or not reference.startswith('reference')
                or not reference.endswith(suffix)):
            raise ValueError(f'reference must be a reference*{suffix} filename beside the owner manifest')
        _text(case['source'], 'reference source', 1000, required=True)
        if case['device'] != 'cpu':
            raise ValueError('reference checks currently support only the explicit cpu device')
        params = case.get('params', {})
        if type(params) is not dict:
            raise ValueError('reference params must be an object')
        normalized, problems = catalog.params_given(member, params)
        if problems:
            raise ValueError('; '.join(problems))
        tolerance = case.get('tolerance', {})
        if type(tolerance) is not dict:
            raise ValueError('reference tolerance must be a finite JSON object')
        _json(tolerance)
        if 'scientific' in case:
            from .semantic_resources import parse_descriptor
            scientific = case['scientific']
            if many:
                if (type(scientific) is not dict or set(scientific) != {'inputs'}
                        or type(scientific['inputs']) is not dict
                        or set(scientific['inputs']) != {port.id for port in member.data_inputs}):
                    raise ValueError('typed reference metadata names an explicit descriptor for every input')
                case = {**case, 'scientific': {'inputs': {name: parse_descriptor(value).wire()
                                                        for name, value in scientific['inputs'].items()}}}
            else:
                if not member.scientific:
                    raise ValueError('scientific reference metadata requires declared scientific ports')
                _scientific_family(member)
                if type(scientific) is not dict or set(scientific) - {'input', 'reference'}:
                    raise ValueError('reference scientific metadata may contain only input and reference descriptors')
                case = {**case, 'scientific': {name: parse_descriptor(value).wire()
                                              for name, value in scientific.items()}}
        result.append({**case, 'params': normalized, 'tolerance': tolerance})
    if not any(case['params'] == defaults for case in result):
        raise ValueError('reference cases must include the default parameter configuration')
    return result


def parse_members(raw, owner) -> tuple[Member, ...]:
    """Validate declarations only; does not import adapters or inspect example pixels/files."""
    from . import manifests as catalog
    requires = getattr(owner, 'requires', None)
    if (getattr(owner, 'runner', None) != 'python-env' or type(requires) is not dict
            or type(requires.get('catalog')) is not int or requires['catalog'] not in (3, 4)):
        raise ValueError('inline members require a format-3 python-env owner')
    if type(raw) is not list or not 1 <= len(raw) <= MAX_MEMBERS:
        raise ValueError('a bundle declares between 1 and 64 members')
    raw = json.loads(_json(raw))
    members, ids = [], set()
    for data in raw:
        if type(data) is not dict or set(data) - FIELDS or not {'id', 'name', 'adapter', 'inputs', 'outputs'} <= set(data):
            raise ValueError('a member needs id, name, adapter, inputs and outputs; owner fields are forbidden')
        capability_key(getattr(owner, 'key', None), data['id'])
        if data['id'] in ids:
            raise ValueError('member ids must be unique within an owner')
        ids.add(data['id'])
        data = {'operation': data['id'], 'hidden': [], 'outcomes': {}, 'tests': [], 'category': '',
                'description': '', 'search_aliases': [], 'experimental': False, 'what': '', **data}
        for name, limit in (('name', 120), ('what', 256), ('category', 256), ('description', 1000), ('adapter', 256)):
            _text(data[name], name, limit, required=name in ('name', 'adapter'))
        if len(data['what'].split()) > 12:
            raise ValueError('what must contain at most 12 words')
        if type(data['operation']) is not str or not catalog.KIND_ID.fullmatch(data['operation']):
            raise ValueError('operation must be a canonical operation id')
        module, separator, function = data['adapter'].partition(':')
        names = [*module.split('.'), function]
        if (not separator or module.endswith('.py') or any(not PYTHON_NAME.fullmatch(n) or keyword.iskeyword(n) for n in names)):
            raise ValueError('adapter must be a canonical module:function identifier')
        if type(data['experimental']) is not bool:
            raise ValueError('experimental must be a boolean')
        if data['category'] and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 &,-]*(/[A-Za-z0-9][A-Za-z0-9 &,-]*)*', data['category']):
            raise ValueError('category must be a path of plain words')
        for name, maximum in (('hidden', len(catalog.HIDDEN)), ('search_aliases', 12), ('tests', 64)):
            if type(data[name]) is not list or len(data[name]) > maximum:
                raise ValueError(f'{name} must be a bounded list')
        if any(type(h) is not str or h not in catalog.HIDDEN for h in data['hidden']) or len(set(data['hidden'])) != len(data['hidden']):
            raise ValueError('hidden inputs must name distinct supported inputs')
        for alias in data['search_aliases']:
            _text(alias, 'search alias', 40, required=True)
        outcomes = data['outcomes']
        if type(outcomes) is not dict or set(outcomes) - catalog.OUTCOME_KEYS:
            raise ValueError('outcomes may describe only empty and unsupported')
        for text in outcomes.values():
            _text(text, 'outcome', 200, required=True)
        inputs, outputs = _ports(data)
        member = Member(*(data[n] for n in ('id', 'name', 'adapter', 'operation')), inputs, outputs,
                        tuple(data['hidden']), data['category'], data['description'], tuple(data['search_aliases']),
                        data['experimental'], data['what'], _json(data))
        for example in data['tests']:
            if type(example) is not dict or set(example) - catalog.TEST_KEYS:
                raise ValueError('tests must use the existing example contract')
            path = example.get('input')
            if (type(path) is not str or len(path) > 256 or PurePosixPath(path).name != path or '\\' in path
                    or not path.startswith('example') or not path.lower().endswith(catalog.EXAMPLE_SUFFIXES)):
                raise ValueError('test input must be an example image filename beside the owner manifest')
            if example.get('outcome', 'result') not in catalog.OUTCOMES:
                raise ValueError('invalid example outcome')
            if 'count' in example and (type(example['count']) is not int or not 0 <= example['count'] < 2**53):
                raise ValueError('example count must be a nonnegative exact integer')
            if type(example.get('params', {})) is not dict:
                raise ValueError('example params must be an object')
            _, problems = catalog.params_given(member, example.get('params', {}))
            if problems:
                raise ValueError('; '.join(problems))
        if 'scientific' in data:
            data['scientific'] = _scientific(data['scientific'], member)
        if 'bridge' in data:
            _bridge(data, member)
        if 'reader' in data:
            _reader(data, member)
        if 'resources' in data:
            _resources(data, member)
        if 'reader_checks' in data:
            from .reader_proof_contract import declaration
            declaration(data['reader_checks'], member)
        if 'reference_cases' in data:
            data['reference_cases'] = _reference_cases(data['reference_cases'], member)
        if 'scientific' in data or 'reference_cases' in data:
            member = Member(member.id, member.name, member.adapter, member.operation, member.inputs, member.outputs,
                            member.hidden, member.category, member.description, member.search_aliases,
                            member.experimental, member.what, _json(data))
        members.append(member)
    return tuple(members)


@dataclass(frozen=True)
class CapabilityView:
    """Member contract plus explicit owner; deliberately no environment/path forwarding."""
    owner: Manifest
    member: Member

    @property
    def key(self):
        return capability_key(self.owner.key, self.member.id)

    @property
    def id(self):
        return self.key

    @property
    def implements(self):
        from .manifests import Implements
        first = next((p.type for p in self.data_inputs), '')
        return (Implements(self.operation, self.key, first, self.outputs[0].type),)

    adapter = property(lambda self: self.member.adapter)
    name = property(lambda self: self.member.name)
    what = property(lambda self: self.member.what)
    operation = property(lambda self: self.member.operation)
    inputs = property(lambda self: self.member.inputs)
    outputs = property(lambda self: self.member.outputs)
    params = property(lambda self: self.member.params)
    data_inputs = property(lambda self: self.member.data_inputs)
    hidden = property(lambda self: self.member.hidden)
    outcomes = property(lambda self: self.member.outcomes)
    tests = property(lambda self: self.member.tests)
    reference_cases = property(lambda self: self.member.reference_cases)
    scientific = property(lambda self: self.member.scientific)
    bridge = property(lambda self: self.member.bridge)
    reader = property(lambda self: self.member.reader)
    reader_checks = property(lambda self: self.member.reader_checks)
    resources = property(lambda self: self.member.resources)
    category = property(lambda self: self.member.category)
    description = property(lambda self: self.member.description)
    search_aliases = property(lambda self: self.member.search_aliases)
    experimental = property(lambda self: self.member.experimental)
    runner = property(lambda self: self.owner.runner)
    version = property(lambda self: self.owner.version)
    licence = property(lambda self: tuple(self.owner.licence))
    contract = property(lambda self: True)
    derived_from = property(lambda self: {})


def view(owner, member: Member) -> CapabilityView:
    if not isinstance(member, Member):
        raise ValueError('a capability view needs a parsed member declaration')
    capability_key(owner.key, member.id)
    return CapabilityView(owner, member)
