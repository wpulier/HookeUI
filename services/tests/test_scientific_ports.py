"""Declaration and metadata checks only; these do not establish upstream runtime support."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from gpu_worker import bundle_contract as bundles
from gpu_worker.semantic_resources import parse_contract, validate


@pytest.fixture
def owner():
    return SimpleNamespace(key='mine/scientific', runner='python-env', requires={'catalog': 3})


def declaration(**changes):
    return {'id': 'measure', 'name': 'Measure', 'adapter': 'adapter:run',
            'inputs': [{'id': 'image', 'type': 'image.2d.rgb8'},
                       {'id': 'threshold', 'type': 'number', 'default': 1}],
            'outputs': [{'id': 'table', 'type': 'table.objects'}], **changes}


def scientific():
    return {'inputs': {'image': {'exact': {'semantic': 'intensity'}, 'known': ['calibration']}},
            'outputs': {'table': {'exact': {'semantic': 'object-table'},
                                  'same_as': [{'peer': 'image', 'fields': ['calibration']}]}}}


def parse(owner, **changes):
    return bundles.parse_members([declaration(**changes)], owner)[0]


def digest(member):
    return hashlib.sha256(json.dumps(member.wire(), sort_keys=True).encode()).hexdigest()


def test_legacy_wire_stays_unchanged_and_explicit_contract_changes_identity(owner):
    legacy = parse(owner)
    assert legacy.scientific == {} and 'scientific' not in legacy.wire()
    assert bundles.view(owner, legacy).scientific == {}
    declared = parse(owner, scientific=scientific())
    assert digest(legacy) != digest(declared)
    changed = scientific()
    changed['inputs']['image']['exact']['semantic'] = 'class-labels'
    assert digest(declared) != digest(parse(owner, scientific=changed))
    assert parse(owner).wire() == legacy.wire()


def test_regional_bridge_is_explicit_and_keeps_legacy_identity(owner):
    legacy = parse(owner)
    assert legacy.bridge == '' and 'bridge' not in legacy.wire()
    regional = parse(owner, bridge='regions-v1', scientific=scientific())
    assert regional.bridge == 'regions-v1'
    assert bundles.view(owner, regional).bridge == 'regions-v1'
    assert digest(regional) != digest(parse(owner, scientific=scientific()))
    with pytest.raises(ValueError, match='scientific ports'):
        parse(owner, bridge='regions-v1')
    with pytest.raises(ValueError, match='Unsupported resource bridge'):
        parse(owner, bridge='future', scientific=scientific())


def test_contract_is_detached_and_uses_one_shared_validator(owner):
    raw = scientific()
    member = parse(owner, scientific=raw)
    raw['inputs']['image']['exact']['semantic'] = 'class-labels'
    contract = parse_contract(member.scientific['inputs']['image'])
    assert validate({'semantic': 'class-labels'}, contract)
    assert validate({}, contract)
    assert contract.exact.wire() == {'semantic': 'intensity'}
    wire = bundles.view(owner, member).scientific
    wire['inputs']['image']['exact']['semantic'] = 'mutated'
    assert member.scientific['inputs']['image']['exact']['semantic'] == 'intensity'


@pytest.mark.parametrize('raw', [None, {}, {'inputs': {}},
    {'inputs': {'threshold': {}}, 'outputs': {'table': {}}},
    {'inputs': {'image': {}}, 'outputs': {}},
    {'inputs': {'image': {}, 'extra': {}}, 'outputs': {'table': {}}},
    {'inputs': {'image': {'known': ['not-a-field']}}, 'outputs': {'table': {}}},
    {'inputs': {'image': {'exact': {'shape': [float('nan')]}}}, 'outputs': {'table': {}}},
])
def test_bad_port_maps_and_nonfinite_contracts_are_refused(owner, raw):
    with pytest.raises(ValueError):
        parse(owner, scientific=raw)


@pytest.mark.parametrize('side,port,peer', [('inputs', 'image', 'image'),
    ('inputs', 'image', 'table'), ('outputs', 'table', 'table'),
    ('outputs', 'table', 'missing'), ('outputs', 'table', 'threshold')])
def test_relationships_only_resolve_supported_peer_ports(owner, side, port, peer):
    raw = scientific()
    raw[side][port] = {'same_as': [{'peer': peer, 'fields': ['calibration']}]}
    with pytest.raises(ValueError, match='peer'):
        parse(owner, scientific=raw)


@pytest.mark.parametrize('changes', [
    {'inputs': [{'id': 'image', 'type': 'image.3d'}]},
    {'inputs': []},
    {'inputs': [{'id': 'image', 'type': 'image.2d.rgb8'}, {'id': 'another', 'type': 'image.2d.rgb8'}]},
    {'outputs': [{'id': 'table', 'type': 'table.objects'}, {'id': 'words', 'type': 'text'}]},
])
def test_unsupported_scientific_execution_shapes_fail_explicitly(owner, changes):
    with pytest.raises(ValueError, match='scientific ports currently'):
        parse(owner, scientific=scientific(), **changes)


def case(**changes):
    return {'input': 'example.png', 'reference': 'reference.json', 'source': 'upstream reference',
            'device': 'cpu', **changes}


def test_case_metadata_is_optional_and_never_inherits_required_meaning(owner):
    cases = [case(scientific={'input': {'dtype': 'uint8'}, 'reference': {'semantic': 'object-table'}})]
    member = parse(owner, scientific=scientific(), reference_cases=cases)
    evidence = member.reference_cases[0]['scientific']
    assert evidence['input'] == {'dtype': 'uint8'}
    assert validate(evidence['input'], parse_contract(member.scientific['inputs']['image']))
    cases[0]['scientific']['input']['dtype'] = 'float32'
    assert member.reference_cases[0]['scientific']['input']['dtype'] == 'uint8'
    assert 'scientific' not in parse(owner, reference_cases=[case()]).reference_cases[0]


@pytest.mark.parametrize('metadata', [None, {'output': {}}, {'input': {'unknown': 1}},
    {'reference': {'shape': [float('inf')]}}, {'input': {'dtype': 3}}])
def test_case_descriptor_validation_rejects_unknown_and_nonfinite_values(owner, metadata):
    with pytest.raises(ValueError):
        parse(owner, scientific=scientific(), reference_cases=[case(scientific=metadata)])


def test_scientific_reference_cannot_be_ignored_by_a_legacy_member(owner):
    with pytest.raises(ValueError, match='requires declared scientific ports'):
        parse(owner, reference_cases=[case(scientific={'input': {'semantic': 'intensity'}})])


def test_orchestrator_compatibility_exports_the_same_shared_types():
    from gpu_worker import semantic_resources as shared
    from orchestrator import semantic_resources as previous
    for name in ('Contract', 'Descriptor', 'SameAs', 'Mismatch', 'InvalidDescriptor',
                 'parse_contract', 'parse_descriptor', 'validate'):
        assert getattr(previous, name) is getattr(shared, name)
