"""Synthetic declaration validation only; no format-3 installation or runtime claims."""
import copy
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest


@pytest.fixture
def api():
    from gpu_worker import bundle_contract
    return bundle_contract


@pytest.fixture
def owner(tmp_path):
    return SimpleNamespace(key='mine/imaging', id='imaging', runner='python-env',
                           requires={'catalog': 3}, folder=str(tmp_path), version='1', licence=('apache-2.0',))


def declaration(**changes):
    return {'id': 'measure', 'name': 'Measure spots', 'adapter': 'adapters.measure:run',
            'inputs': [{'id': 'image', 'type': 'image.2d.rgb8'},
                       {'id': 'threshold', 'type': 'number', 'default': 0.5, 'min': 0, 'max': 1}],
            'outputs': [{'id': 'table', 'type': 'table.objects'}], **changes}


def test_scientific_host_is_a_distinct_private_review_contract(api, owner):
    raw = declaration(bridge='scientific-host-v1', inputs=[{'id': 'work', 'type': 'project.scientific'}],
                      outputs=[{'id': 'result', 'type': 'project.scientific'}])
    member, = api.parse_members([raw], owner)
    assert member.bridge == 'scientific-host-v1' and member.reference_cases == ()
    for changes in ({'hidden': ['session']}, {'inputs': raw['inputs'] + [{'id': 'x', 'type': 'int'}]},
                    {'inputs': [{**raw['inputs'][0], 'optional': True}]},
                    {'outputs': [{'id': 'result', 'type': 'image.2d.rgb8'}]},
                    {'tests': [{'input': 'example.png'}]}):
        with pytest.raises(ValueError):
            api.parse_members([{**raw, **changes}], owner)
    with pytest.raises(ValueError):
        api.parse_members([declaration(bridge='scientific-host-v1')], owner)


def test_two_members_have_exact_distinct_keys_and_existing_parameter_contract(api, owner):
    from gpu_worker import manifests
    from orchestrator.kinds import param_wire, takes_why
    first, second = api.parse_members([declaration(), declaration(id='outline', adapter='adapters.outline:run')], owner)
    chosen = api.view(owner, first)
    assert chosen.owner is owner and chosen.member is first
    assert chosen.key == chosen.id == 'mine/imaging#measure'
    assert api.view(owner, second).key == 'mine/imaging#outline'
    assert chosen.implements[0].impl == chosen.key
    assert chosen.operation == 'measure' and chosen.contract
    assert chosen.runner == owner.runner and chosen.licence == owner.licence and chosen.version == owner.version
    assert manifests.params_given(chosen, {'threshold': 0.75}) == ({'threshold': 0.75}, [])
    assert manifests.params_given(chosen, {'threshold': 4})[1]
    assert param_wire(chosen.params[0])['default'] == 0.5
    assert takes_why(chosen) == ''
    assert not hasattr(chosen, 'env') and not hasattr(chosen, 'folder')


def test_members_are_deeply_detached_and_do_not_import_adapters(api, owner, tmp_path):
    source = declaration(tests=[{'input': 'example.png', 'params': {'threshold': .6}}],
                         outcomes={'empty': 'No objects'}, search_aliases=['spots'])
    member, = api.parse_members([source], owner)
    source['inputs'][1]['default'] = .9
    source['tests'][0]['params']['threshold'] = .8
    member.tests[0]['params']['threshold'] = 0
    member.outcomes['empty'] = 'changed'
    member.wire()['inputs'][1]['default'] = 1
    assert member.inputs[1].default == .5
    assert member.tests[0]['params']['threshold'] == .6
    assert member.outcomes == {'empty': 'No objects'}
    with pytest.raises(FrozenInstanceError):
        member.id = 'other'
    assert not (tmp_path / 'adapters').exists()


@pytest.mark.parametrize('raw', [None, {}, [], [declaration()] * 65,
                               [declaration(), declaration()], [None]])
def test_invalid_member_collection_is_refused(api, owner, raw):
    with pytest.raises(ValueError):
        api.parse_members(raw, owner)


@pytest.mark.parametrize('field', ['env', 'files', 'licence', 'component', 'namespace', 'folder', 'channel',
                                  'requires', 'runner', 'needs_to_run', 'host', 'version', 'owner'])
def test_members_cannot_override_owner_policy_or_lifecycle(api, owner, field):
    with pytest.raises(ValueError):
        api.parse_members([declaration(**{field: 'override'})], owner)


@pytest.mark.parametrize('change', [
    {'id': 'bad#id'}, {'id': '../escape'}, {'name': ''}, {'operation': 'bad/op'},
    {'adapter': '../adapter:run'}, {'adapter': 'adapters..measure:run'}, {'adapter': 'adapter.py:run'},
    {'adapter': 'adapter:run()'}, {'adapter': 'adapter:run:other'}, {'adapter': 'class:run'},
    {'outputs': []}, {'inputs': [{'id': 'x', 'type': 'int', 'default': True}]},
    {'inputs': [{'id': 'x', 'type': 'number', 'default': 2, 'max': 1}]},
    {'inputs': [{'id': 'x', 'type': 'string', 'default': []}]},
    {'inputs': [{'id': 'x', 'type': 'image.2d.rgb8', 'optional': 'yes'}]},
    {'outputs': [{'id': 'image', 'type': 'table.objects'}]},
    {'hidden': ['secret']}, {'experimental': 'yes'}, {'outcomes': {'result': 'anything'}},
    {'tests': [{'input': '../example.png'}]}, {'tests': [{'input': 'example.png', 'params': {'threshold': 9}}]},
    {'tests': [{'input': 'example.png', 'params': {'unknown': 1}}]},
    {'search_aliases': ['x'] * 13}, {'description': 'x' * 1001},
])
def test_malformed_declaration_is_refused(api, owner, change):
    with pytest.raises(ValueError):
        api.parse_members([declaration(**change)], owner)


def test_unknown_owner_format_and_runner_are_refused(api, owner):
    for field, value in [('runner', 'fiji'), ('requires', {'catalog': 2}), ('requires', {'catalog': True})]:
        other = copy.copy(owner)
        setattr(other, field, value)
        with pytest.raises(ValueError):
            api.parse_members([declaration()], other)


@pytest.mark.parametrize('key', ['imaging#measure', 'mine/imaging#measure', 'community/imaging#measure'])
def test_exact_key_roundtrip(api, key):
    owner, member = api.parse_capability_key(key)
    assert api.capability_key(owner, member) == key


@pytest.mark.parametrize('key', ['imaging', 'imaging##measure', 'mine/imaging#measure#other',
                               '/imaging#measure', 'a/b/c#measure', 'mine/../imaging#measure',
                               'mine/imaging#', 'mine/imaging#Measure', ' imaging#measure',
                               'imaging#measure ', 'imaging%23measure', None])
def test_ambiguous_keys_are_refused(api, key):
    with pytest.raises(ValueError):
        api.parse_capability_key(key)


def test_descriptor_limits_and_cycles_fail_closed(api, owner):
    raw = declaration(description='x' * (1 << 20))
    with pytest.raises(ValueError):
        api.parse_members([raw], owner)
    cyclic = declaration()
    cyclic['tests'] = [cyclic]
    with pytest.raises(ValueError):
        api.parse_members([cyclic], owner)
    with pytest.raises(ValueError):
        api.parse_members([declaration(inputs=[{'id': 'v', 'type': 'number', 'default': float('nan')}])], owner)


@pytest.mark.parametrize('field,value', [('min', 'bad'), ('max', []), ('step', True)])
def test_malformed_numeric_limits_raise_validation_errors(api, owner, field, value):
    data = declaration()
    data['inputs'][1][field] = value
    with pytest.raises(ValueError):
        api.parse_members([data], owner)


def test_proof_extensions_are_not_silently_accepted_as_legacy_examples(api, owner):
    with pytest.raises(ValueError):
        api.parse_members([declaration(tests=[{'input': 'example.png', 'reference': 'expected.csv',
                                              'tolerance': 0.01}])], owner)
    with pytest.raises(ValueError):
        api.parse_members([declaration(reference_cases=[])], owner)


def reference_case(**changes):
    return {'input': 'example.png', 'reference': 'reference.json',
            'source': 'Upstream release 1.0 reference command', 'device': 'cpu', **changes}


def test_reference_cases_normalize_defaults_and_remain_detached(api, owner):
    raw = declaration(reference_cases=[reference_case(), reference_case(params={'threshold': .75},
                                                                       tolerance={'absolute': .01})])
    member, = api.parse_members([raw], owner)
    assert member.reference_cases[0]['params'] == {'threshold': .5}
    assert member.reference_cases[0]['tolerance'] == {}
    assert api.view(owner, member).reference_cases == member.reference_cases
    raw['reference_cases'][0]['source'] = 'changed'
    member.reference_cases[1]['tolerance']['absolute'] = 99
    assert member.reference_cases[0]['source'] == 'Upstream release 1.0 reference command'
    assert member.reference_cases[1]['tolerance'] == {'absolute': .01}
    assert api.parse_members([declaration()], owner)[0].reference_cases == ()


@pytest.mark.parametrize('change', [
    {'device': 'cuda'}, {'device': None}, {'source': ''}, {'source': 'x' * 1001},
    {'input': '../example.png'}, {'input': 'reference.png'}, {'input': 'example/../example.png'},
    {'reference': '../reference.json'}, {'reference': 'reference\\x.json'},
    {'reference': 'reference.png'}, {'reference': 'expected.json'}, {'reference': 'reference.json\x00'},
    {'params': {'threshold': 2}}, {'params': {'pixel_size_um': 1}}, {'params': {'unknown': 1}},
    {'params': []}, {'tolerance': float('nan')}, {'tolerance': []},
    {'tolerance': {'absolute': float('inf')}}, {'extra': True},
])
def test_invalid_reference_case_refused(api, owner, change):
    with pytest.raises(ValueError):
        api.parse_members([declaration(reference_cases=[reference_case(**change)])], owner)


def test_reference_cases_require_cpu_and_default_parameter_coverage(api, owner):
    missing_device = reference_case()
    del missing_device['device']
    for cases in ([missing_device], [reference_case(params={'threshold': .75})],
                  [reference_case()] * 65, None, {}):
        with pytest.raises(ValueError):
            api.parse_members([declaration(reference_cases=cases)], owner)
    explicit_defaults = reference_case(params={'threshold': .5})
    assert api.parse_members([declaration(reference_cases=[explicit_defaults])], owner)[0].reference_cases


@pytest.mark.parametrize('output,reference', [('image.2d.rgb8', 'reference.png'),
                                            ('text', 'reference.json'), ('points.2d', 'reference.json'),
                                            ('table.objects', 'reference.json')])
def test_reference_format_matches_supported_output(api, owner, output, reference):
    member, = api.parse_members([declaration(outputs=[{'id': 'result', 'type': output}],
                                            reference_cases=[reference_case(reference=reference)])], owner)
    assert member.reference_cases[0]['reference'] == reference


def test_future_output_contracts_parse_but_reference_checks_explain_limits(api, owner):
    for outputs in ([{'id': 'result', 'type': 'tracking.graph'}],
                    [{'id': 'first', 'type': 'text'}, {'id': 'second', 'type': 'text'}]):
        assert api.parse_members([declaration(outputs=outputs)], owner)
        with pytest.raises(ValueError, match='one supported output'):
            api.parse_members([declaration(outputs=outputs, reference_cases=[reference_case()])], owner)


def test_reader_declaration_is_explicit_detached_and_cannot_borrow_operation_proof(api,owner):
    reader={'format':1,'input':'selected','extensions':['.npy','.ome.tif'],'maximum_bytes':1<<20}
    raw=declaration(bridge='scientific-host-v1',inputs=[{'id':'work','type':'project.scientific'}],
                    outputs=[{'id':'result','type':'project.scientific'}],reader=reader)
    member,=api.parse_members([raw],owner)
    assert member.reader==reader and api.view(owner,member).reader==reader
    reader['extensions'].append('.bad');assert member.reader['extensions']==['.npy','.ome.tif']
    member.reader['extensions'].clear();assert member.reader['extensions']
    for field,value in [('format',True),('input','../file'),('extensions',['.*']),('extensions',['.npy','.npy']),
                        ('extensions',['.npy.']),('maximum_bytes',0),('maximum_bytes',True),('maximum_bytes',(64<<20)+1)]:
        with pytest.raises(ValueError):api.parse_members([{**raw,'reader':{**member.reader,field:value}}],owner)
    with pytest.raises(ValueError):api.parse_members([declaration(reader=member.reader)],owner)


def test_large_reader_requires_explicit_regional_access(api,owner):
    reader={'format':3,'input':'source','kind':'directory','patterns':['*.zarr'],
        'maximum_bytes':64<<30,'keeps':['values','dtype','byte-order','axes','calibration']}
    raw=declaration(bridge='scientific-host-v1',inputs=[{'id':'work','type':'project.scientific'}],
        outputs=[{'id':'result','type':'project.scientific'}],reader=reader)
    with pytest.raises(ValueError):api.parse_members([raw],owner)
    reader['access']='regional'
    assert api.parse_members([raw],owner)[0].reader==reader
    for change in ({'maximum_bytes':(64<<30)+1},{'access':'unbounded'},{'access':True}):
        with pytest.raises(ValueError):api.parse_members([{**raw,'reader':{**reader,**change}}],owner)
