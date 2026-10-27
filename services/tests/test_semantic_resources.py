"""Metadata contract counterexamples only: no runtime, provider, payload or scientific accuracy claims."""
import copy

import pytest

from orchestrator.semantic_resources import Contract, Descriptor, InvalidDescriptor, SameAs, parse_contract, parse_descriptor, validate


def ref(id='pixels', revision=0, **extra):
    return {'session': 'session-1', 'id': id, 'revision': revision, **extra}


def labels():
    return {
        'format': 'scientific-resource', 'version': 1,
        'reference': ref('labels', 2), 'semantic': 'instance-labels', 'dtype': 'uint32',
        'axes': ['t', 'y', 'x'], 'shape': [3, 16, 32],
        'calibration': {'space': ref('field'), 'axes': ['y', 'x'], 'units': ['um', 'um'],
                        'affine': [[0.5, 0, 0], [0, 0.5, 0]]},
        'timeline': {'reference': ref('acquisition'), 'axis': 't', 'unit': 's', 'samples': [0, 1, 2]},
        'object_space': ref('labels', 2), 'sources': [ref()],
    }


def test_legacy_dtype_shape_and_type_name_do_not_infer_meaning():
    legacy = parse_descriptor({'dtype': 'uint32', 'axes': ['y', 'x'], 'shape': [16, 32]})
    problem, = validate(legacy, Contract(exact={'semantic': 'instance-labels'}))
    assert problem.code == 'unknown' and problem.field == 'semantic'
    assert 'semantic' not in legacy.wire()
    assert validate({'semantic': None}, Contract(exact={'semantic': 'intensity'}))[0].code == 'unknown'
    # A transport type is not a semantic descriptor. Its caller must explicitly retain only observed fields.
    assert validate({'type': 'labels.instance.2d'}, Contract())[0].code == 'invalid-descriptor'


@pytest.mark.parametrize('semantic', ['intensity', 'probabilities', 'class-labels', 'embeddings', 'object-table', 'lineage-graph'])
def test_same_integer_storage_does_not_make_an_instance_label_map(semantic):
    actual = {**labels(), 'semantic': semantic}
    problem, = validate(actual, Contract(exact={'semantic': 'instance-labels'}))
    assert problem.field == 'semantic' and problem.code == 'mismatch'
    assert semantic in problem.message and 'instance-labels' in problem.message
    assert 'explicit conversion' in problem.next and 'new identity' in problem.next


def test_open_custom_semantics_match_exactly_and_any_is_not_a_wildcard():
    assert validate({'semantic': 'spectra.raman'}, Contract(exact={'semantic': 'spectra.raman'})) == ()
    assert validate({'semantic': 'any'}, Contract(exact={'semantic': 'instance-labels'}))
    assert validate({'semantic': 'instance-labels'}, Contract(exact={'semantic': 'any'}))


def test_exact_axes_order_shape_and_dtype_are_separate_constraints():
    contract = Contract(exact={'axes': ['y', 'x'], 'shape': [16, 32], 'dtype': 'uint16'})
    problems = validate({'axes': ['x', 'y'], 'shape': [32, 16], 'dtype': 'uint32'}, contract)
    assert {p.field for p in problems} == {'axes[0]', 'shape[0]', 'dtype'}
    assert all(p.code == 'mismatch' for p in problems)
    # A contract asking only for semantic role makes no unasked promise about dtype or dimension.
    assert validate(labels(), Contract(exact={'semantic': 'instance-labels'})) == ()


@pytest.mark.parametrize('change,field', [
    (lambda v: v['timeline']['samples'].__setitem__(1, 1.5), 'timeline.samples[1]'),
    (lambda v: v['timeline']['reference'].__setitem__('revision', 1), 'timeline.reference.revision'),
    (lambda v: v['timeline'].__setitem__('unit', 'ms'), 'timeline.unit'),
])
def test_equal_frame_counts_do_not_establish_time_compatibility(change, field):
    peer, actual = labels(), labels()
    change(actual)
    problem, = validate(actual, Contract(same_as=(SameAs('image', ('timeline',)),)), peers={'image': peer})
    assert problem.field == field
    assert 'explicit' in problem.next


def test_unknown_time_on_both_sides_is_not_a_match():
    problem, = validate({}, Contract(same_as=(SameAs('image', ('timeline',)),)), peers={'image': {}})
    assert problem.code == 'unknown'


@pytest.mark.parametrize('change,field', [
    (lambda v: v['calibration']['affine'][0].__setitem__(2, 12), 'calibration.affine[0][2]'),
    (lambda v: v['calibration']['space'].__setitem__('id', 'other-field'), 'calibration.space.id'),
    (lambda v: v['calibration']['units'].__setitem__(0, 'nm'), 'calibration.units[0]'),
])
def test_equal_shapes_do_not_establish_calibration_compatibility(change, field):
    original, result = labels(), labels()
    change(result)
    problem, = validate(result, Contract(same_as=(SameAs('source', ('calibration',)),)), peers={'source': original})
    assert problem.field == field and problem.code == 'mismatch'


def test_tables_and_graphs_bind_exact_object_domain_and_relation_revisions():
    objects = labels()
    table = {'semantic': 'object-table', 'object_space': ref('labels', 2), 'object_key': 'instance_id',
             'relations': {'labels': ref('labels', 2)}, 'sources': [ref('labels', 2)]}
    contract = Contract(exact={'semantic': 'object-table'}, known=('object_key',),
                        same_as=(SameAs('objects', ('object_space',)),))
    assert validate(table, contract, peers={'objects': objects}) == ()
    wrong = {**table, 'object_space': ref('labels', 3)}
    problem, = validate(wrong, contract, peers={'objects': objects})
    assert problem.field == 'object_space.revision'
    assert 'newest revision' in problem.next
    graph = {'semantic': 'lineage-graph', 'object_space': ref('labels', 2),
             'relations': {'nodes': ref('measurements', 4)}, 'timeline': objects['timeline']}
    expected = Contract(exact={'semantic': 'lineage-graph', 'relations': {'nodes': ref('measurements', 4)}},
                        same_as=(SameAs('objects', ('object_space', 'timeline')),))
    assert validate(graph, expected, peers={'objects': objects}) == ()
    graph['relations']['nodes']['revision'] = 5
    assert validate(graph, expected, peers={'objects': objects})[0].field == 'relations.nodes.revision'


def test_channels_and_multiscale_levels_are_identities_not_equal_shapes():
    actual = {'axes': ['c', 'y', 'x'], 'shape': [2, 16, 32],
              'channels': {'axis': 'c', 'ids': ['DAPI', 'GFP']}, 'pyramid': ref('pyramid'), 'level': 1}
    expected = copy.deepcopy(actual)
    expected['channels']['ids'].reverse()
    expected['level'] = 0
    constraint = Contract(same_as=(SameAs('source', ('channels', 'pyramid', 'level')),))
    problems = validate(actual, constraint, peers={'source': expected})
    assert {p.field for p in problems} == {'channels.ids[0]', 'level'}
    actual['pyramid']['revision'] = 1
    assert 'pyramid.revision' in {p.field for p in validate(actual, constraint, peers={'source': expected})}


def test_same_validator_checks_actual_returned_descriptor_not_declared_output():
    contract = Contract(exact={'format': 'scientific-resource', 'version': 1, 'semantic': 'instance-labels'},
                        same_as=(SameAs('input', ('axes', 'shape', 'calibration', 'timeline')),))
    input = labels()
    assert validate(input, contract, peers={'input': input}, resource='input.image') == ()
    returned = {**labels(), 'semantic': 'class-labels'}
    problem, = validate(returned, contract, peers={'input': input}, resource='output.labels')
    assert problem.resource == 'output.labels' and problem.field == 'semantic'
    returned = labels()
    returned['timeline']['samples'][2] = 4
    assert validate(returned, contract, peers={'input': input}, resource='output.labels')[0].field == 'timeline.samples[2]'


@pytest.mark.parametrize('bad,path', [
    ({'version': 2}, 'version'),
    ({'shape': [True]}, 'shape[0]'),
    ({'axes': ['x', 'x']}, 'axes'),
    ({'axes': ['y', 'x'], 'shape': [16]}, 'shape'),
    ({'reference': ref(revision='latest')}, 'reference.revision'),
    ({'reference': ref(component='../private')}, 'reference.component'),
    ({'axes': ['t'], 'shape': [2], 'timeline': {'reference': ref(), 'axis': 't', 'unit': 's', 'samples': [0]}}, 'timeline.samples'),
    ({'timeline': {'reference': ref(), 'axis': 't', 'unit': 's', 'samples': [float('nan')]}}, 'timeline.samples[0]'),
    ({'axes': ['y'], 'channels': {'axis': 'c', 'ids': ['GFP']}}, 'channels.axis'),
])
def test_malformed_actual_metadata_is_refused_before_compatibility(bad, path):
    problem, = validate(bad, Contract())
    assert problem.code == 'invalid-descriptor' and problem.field == path


def test_contract_typos_missing_peers_and_unknown_peer_metadata_do_not_pass():
    with pytest.raises(InvalidDescriptor):
        Contract(exact={'semantics': 'instance-labels'})
    with pytest.raises(ValueError):
        Contract(exact={'semantic': None})
    with pytest.raises(ValueError):
        Contract(known=('axis',))
    constraint = Contract(same_as=(SameAs('labels', ('object_space',)),))
    assert validate({}, constraint)[0].code == 'missing-peer'
    assert validate({}, constraint, peers={'labels': {'shape': [-1]}})[0].code == 'invalid-peer'


def test_descriptor_and_contract_cannot_be_changed_through_original_dictionaries():
    original = labels()
    descriptor = parse_descriptor(original)
    exact = {'semantic': 'instance-labels'}
    contract = Contract(exact=exact)
    original['semantic'] = exact['semantic'] = 'class-labels'
    descriptor.wire()['semantic'] = 'class-labels'
    assert validate(descriptor, contract) == ()
    assert validate(Descriptor(b'invalid JSON'), Contract())[0].code == 'invalid-descriptor'


def runtime(operation='encode', generation='generation-1'):
    return {'bundle': 'mine/features', 'generation': generation, 'operation': operation}


def embeddings():
    return {'semantic': 'embeddings', 'axes': ['object', 'feature'], 'shape': [12, 256], 'dtype': 'float32',
            'feature_model': {'runtime': runtime(), 'recipe': ref('feature-recipe', 2)},
            'region': ref('regions', 3), 'coordinates': ref('coordinate-map', 4),
            'object_space': ref('labels', 2)}


@pytest.mark.parametrize('change,field', [
    (lambda d: d['feature_model']['runtime'].__setitem__('generation', 'generation-2'), 'feature_model.runtime.generation'),
    (lambda d: d['feature_model']['runtime'].__setitem__('operation', 'encode-other'), 'feature_model.runtime.operation'),
    (lambda d: d['feature_model']['recipe'].__setitem__('revision', 3), 'feature_model.recipe.revision'),
    (lambda d: d['region'].__setitem__('revision', 4), 'region.revision'),
    (lambda d: d['coordinates'].__setitem__('revision', 5), 'coordinates.revision'),
])
def test_equal_shaped_embeddings_are_not_interchangeable_across_feature_or_spatial_identity(change, field):
    expected, actual = embeddings(), embeddings()
    change(actual)
    constraint = Contract(exact={'semantic': 'embeddings'}, same_as=(
        SameAs('training', ('shape', 'dtype', 'feature_model', 'region', 'coordinates', 'object_space')),))
    problem, = validate(actual, constraint, peers={'training': expected})
    assert problem.field == field and problem.code == 'mismatch'
    assert 'exact resource revision' in problem.next


@pytest.mark.parametrize('changed', [
    [{'id': 0, 'name': 'background'}, {'id': 1, 'name': 'cytoplasm'}],
    [{'id': 1, 'name': 'nucleus'}, {'id': 0, 'name': 'background'}],
    [{'id': '0', 'name': 'background'}, {'id': '1', 'name': 'nucleus'}],
])
def test_class_vocabulary_names_order_and_id_types_are_preserved(changed):
    vocabulary = [{'id': 0, 'name': 'background'}, {'id': 1, 'name': 'nucleus'}]
    contract = Contract(exact={'semantic': 'class-labels', 'class_vocabulary': vocabulary})
    problems = validate({'semantic': 'class-labels', 'class_vocabulary': changed}, contract)
    assert len(problems) == 1 and problems[0].field.startswith('class_vocabulary[')
    assert 'explicit conversion' in problems[0].next
    assert validate({'semantic': 'class-labels'}, contract)[0].code == 'unknown'


def test_storage_format_and_version_are_separate_from_descriptor_schema():
    actual = {'format': 'scientific-resource', 'version': 1, 'storage_format': 'ome-zarr', 'storage_version': '0.4'}
    assert validate(actual, Contract(exact=actual)) == ()
    contract = Contract(exact={'storage_format': 'ome-zarr', 'storage_version': '0.5'})
    problem, = validate(actual, contract)
    assert problem.field == 'storage_version' and 'format conversion' in problem.next
    actual['storage_format'] = 'ome-tiff'
    assert {p.field for p in validate(actual, contract)} == {'storage_format', 'storage_version'}
    assert validate({'format': 'scientific-resource', 'version': 1}, contract)[0].code == 'unknown'


def test_embedding_metadata_is_unknown_until_explicitly_observed():
    contract = Contract(known=('feature_model', 'region', 'coordinates', 'missing_values', 'transformations'))
    problems = validate({'semantic': 'embeddings', 'shape': [12, 256], 'dtype': 'float32'}, contract)
    assert len(problems) == 5 and all(p.code == 'unknown' for p in problems)
    # No guessed model, coordinate system, lossless conversion history or default null policy.
    assert parse_descriptor({}).wire() == {}


def test_missing_value_policy_and_exact_mask_identity_are_contract_facts():
    wanted = {'policy': 'preserve', 'representation': 'mask', 'mask': ref('valid-pixels', 2)}
    contract = Contract(exact={'missing_values': wanted})
    assert validate({'missing_values': wanted}, contract) == ()
    actual = copy.deepcopy(wanted)
    actual['mask']['revision'] = 3
    assert validate({'missing_values': actual}, contract)[0].field == 'missing_values.mask.revision'
    actual = {**wanted, 'policy': 'impute'}
    problem, = validate({'missing_values': actual}, contract)
    assert problem.field == 'missing_values.policy' and 'explicit transformation' in problem.next
    assert validate({}, contract)[0].code == 'unknown'


def transformation():
    return {'operation': runtime('resample'), 'inputs': [ref('pixels', 3)],
            'parameters': {'interpolation': 'nearest', 'normalize': False, 'scale': [0.5, 0.5]},
            'loss': {'kind': 'lossy', 'details': 'Spatial downsampling discards source samples'}}


def test_transformation_provenance_pins_operation_inputs_parameters_and_declared_loss():
    record = transformation()
    contract = Contract(exact={'transformations': [record]})
    assert validate({'transformations': [record]}, contract) == ()
    for change, field in [
        (lambda t: t['operation'].__setitem__('generation', 'generation-2'), 'operation.generation'),
        (lambda t: t['inputs'][0].__setitem__('revision', 4), 'inputs[0].revision'),
        (lambda t: t['parameters'].__setitem__('normalize', 0), 'parameters.normalize'),
        (lambda t: t['loss'].__setitem__('kind', 'lossless'), 'loss.kind'),
    ]:
        changed = copy.deepcopy(record)
        change(changed)
        problem, = validate({'transformations': [changed]}, contract, resource='output.resampled')
        assert problem.field == f'transformations[0].{field}'
        assert problem.resource == 'output.resampled' and 'provenance' in problem.next
    assert validate({}, contract)[0].code == 'unknown'


@pytest.mark.parametrize('bad,path', [
    ({'class_vocabulary': [{'id': 1, 'name': 'a'}, {'id': 1, 'name': 'b'}]}, 'class_vocabulary'),
    ({'feature_model': {'runtime': runtime(), 'recipe': ref('recipe', 'latest')}}, 'feature_model.recipe.revision'),
    ({'storage_version': 0.5}, 'storage_version'),
    ({'missing_values': {'policy': 'preserve', 'representation': 'mask'}}, 'missing_values.mask'),
    ({'missing_values': {'policy': 'preserve', 'representation': 'sentinel'}}, 'missing_values.value'),
])
def test_added_metadata_rejects_ambiguous_or_incomplete_identity(bad, path):
    problem, = validate(bad, Contract())
    assert problem.code == 'invalid-descriptor' and problem.field == path


def test_transformation_parameters_are_bounded_json_native_values():
    for parameters in ({'unsafe': object()}, {'unsafe': float('inf')}, {'too_many': list(range(4097))}):
        record = transformation()
        record['parameters'] = parameters
        problem, = validate({'transformations': [record]}, Contract())
        assert problem.code == 'invalid-descriptor' and problem.field == 'transformations[0].parameters'
    record = transformation()
    record['parameters'] = {'cyclic': record}
    assert validate({'transformations': [record]}, Contract())[0].code == 'invalid-descriptor'


def test_transformation_cannot_claim_to_have_overwritten_its_exact_input_revision():
    record = transformation()
    problem, = validate({'reference': ref('pixels', 3), 'transformations': [record]}, Contract())
    assert problem.field == 'transformations[0].inputs' and 'new exact resource identity' in problem.message
    assert validate({'reference': ref('pixels', 4), 'transformations': [record]}, Contract()) == ()


def test_iterable_constraints_are_materialized_without_dropping_requirements():
    known = Contract(known=iter(['semantic']))
    problem, = validate({}, known)
    assert problem.field == 'semantic' and problem.code == 'unknown'
    assert known.known == ('semantic',)


def test_same_as_field_iterator_is_materialized_without_dropping_constraints():
    relation = SameAs('source', iter(['semantic']))
    assert relation.fields == ('semantic',)
    problem, = validate({'semantic': 'class-labels'}, Contract(same_as=(relation,)),
                        peers={'source': {'semantic': 'instance-labels'}})
    assert problem.field == 'semantic' and problem.code == 'mismatch'


def test_same_as_iterator_is_materialized_without_dropping_relationships():
    relation = SameAs('source', ('semantic',))
    contract = Contract(same_as=iter([relation]))
    problem, = validate({'semantic': 'class-labels'}, contract,
                        peers={'source': {'semantic': 'instance-labels'}})
    assert problem.field == 'semantic' and problem.code == 'mismatch'
    assert contract.same_as == (relation,)


@pytest.mark.parametrize('body', [
    b'{"semantic":"class-labels","semantic":"instance-labels"}',
    b'{"reference":{"session":"s1","id":"other","id":"pixels","revision":0},"semantic":"instance-labels"}',
])
def test_raw_descriptor_bytes_with_duplicate_keys_are_refused(body):
    problem, = validate(Descriptor(body), Contract(exact={'semantic': 'instance-labels'}))
    assert problem.code == 'invalid-descriptor' and 'duplicate' in problem.message


def test_raw_descriptor_size_is_bounded_before_decoding_or_normalizing():
    from orchestrator.semantic_resources import MAX_BYTES
    problem, = validate(Descriptor(b' ' * MAX_BYTES + b'{}'), Contract())
    assert problem.code == 'invalid-descriptor' and 'exceeds' in problem.message


@pytest.mark.parametrize('revision', [0, 23, 'a' * 64, '0123456789abcdef' * 4])
def test_resource_reference_preserves_exact_integer_or_content_revision(revision):
    value = {'reference': ref(revision=revision)}
    assert parse_descriptor(value).wire() == value
    assert validate(value, Contract(exact=value)) == ()


@pytest.mark.parametrize('revision', [True, -1, 2**53, 1.0, '', '0', 'A' * 64, 'g' * 64, 'a' * 63, 'a' * 65])
def test_resource_reference_rejects_ambiguous_content_revisions(revision):
    problem, = validate({'reference': ref(revision=revision)}, Contract())
    assert problem.code == 'invalid-descriptor' and problem.field == 'reference.revision'


def test_content_revision_changes_are_exact_mismatches_and_never_coerced_to_numbers():
    original = {'reference': ref(revision='a' * 64)}
    changed = {'reference': ref(revision='b' * 64)}
    problem, = validate(changed, Contract(exact=original))
    assert problem.field == 'reference.revision' and problem.code == 'mismatch'
    assert 'newest revision' in problem.next
    problem, = validate({'reference': ref(revision=0)}, Contract(exact={'reference': ref(revision='0' * 64)}))
    assert problem.field == 'reference.revision' and problem.code == 'mismatch'
    contract = Contract(same_as=(SameAs('input', ('reference',)),))
    assert validate(changed, contract, peers={'input': original})[0].code == 'mismatch'


def test_contract_wire_roundtrip_keeps_constraints_and_detaches_mutable_values():
    wire = {'exact': {'semantic': 'instance-labels', 'reference': ref(revision='a' * 64)},
            'known': ['object_space'], 'same_as': [{'peer': 'image', 'fields': ['axes', 'shape']}]}
    contract = parse_contract(wire)
    assert contract.wire() == wire
    assert parse_contract(contract).wire() == wire
    actual = {**wire['exact'], 'object_space': ref('objects'), 'axes': ['y', 'x'], 'shape': [3, 4]}
    assert validate(actual, contract, peers={'image': {'axes': ['y', 'x'], 'shape': [3, 4]}}) == ()
    wire['exact']['semantic'] = 'class-labels'
    wire['same_as'][0]['fields'].clear()
    exported = contract.wire()
    exported['exact']['reference']['revision'] = 'b' * 64
    exported['known'].clear()
    assert contract.wire()['exact']['reference']['revision'] == 'a' * 64
    assert contract.known == ('object_space',) and contract.same_as[0].fields == ('axes', 'shape')


def test_empty_contract_has_explicit_empty_wire_and_never_infers_requirements():
    assert parse_contract({}).wire() == {'exact': {}, 'known': [], 'same_as': []}
    assert validate({}, parse_contract({})) == ()


@pytest.mark.parametrize('value', [
    None, [], 'any', {'unknown': {}}, {'exact': None}, {'exact': {'semantics': 'intensity'}},
    {'exact': {'semantic': None}}, {'known': 'semantic'}, {'known': ['semantics']},
    {'known': ['semantic', 'semantic']}, {'known': [None]}, {'known': ('semantic',)},
    {'same_as': {}}, {'same_as': [{'peer': 'image'}]},
    {'same_as': [{'peer': 'image', 'fields': ['shape'], 'optional': True}]},
    {'same_as': [{'peer': '', 'fields': ['shape']}]},
    {'same_as': [{'peer': 'image', 'fields': 'shape'}]},
    {'same_as': [{'peer': 'image', 'fields': []}]},
    {'same_as': [{'peer': 'image', 'fields': ['axes', 'axes']}]},
    {'same_as': [{'peer': 'image', 'fields': ['axis']}]},
    {'same_as': [{'peer': 'image', 'fields': [None]}]},
    {'exact': {'shape': [float('inf')]}}, {'exact': {'shape': [object()]}},
])
def test_contract_wire_refuses_malformed_and_unknown_fields(value):
    with pytest.raises(ValueError):
        parse_contract(value)


def test_contract_wire_is_bounded_before_recursive_validation():
    from orchestrator.semantic_resources import MAX_CONTRACT_BYTES, MAX_CONTRACT_NODES, MAX_SAME_AS
    bad = [
        {'exact': {'semantic': 'x' * (MAX_CONTRACT_BYTES + 1)}},
        {'exact': {'shape': [1] * (MAX_CONTRACT_NODES + 1)}},
        {'same_as': [{'peer': f'p{i}', 'fields': ['shape']} for i in range(MAX_SAME_AS + 1)]},
    ]
    cyclic = {}
    cyclic['exact'] = cyclic
    bad.append(cyclic)
    nested = {}
    for _ in range(40):
        nested = {'exact': nested}
    bad.append(nested)
    for value in bad:
        with pytest.raises(ValueError):
            parse_contract(value)


def test_contract_wire_byte_limit_counts_utf8_and_json_overhead():
    from orchestrator.semantic_resources import MAX_CONTRACT_BYTES
    # Large valid descriptor content must fit the *whole* contract, including JSON syntax.
    with pytest.raises(ValueError):
        parse_contract({'exact': {'unit': 'é' * (MAX_CONTRACT_BYTES // 2)}})
