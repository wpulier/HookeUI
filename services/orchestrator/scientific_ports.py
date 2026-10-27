"""Scientific requirements at the existing worker boundary, before and after execution.

Declarations constrain metadata; they never supply missing runtime facts. Producer
claims remain attributed to the producer. Only fields measured from payloads are
host observations, and output calibration is never inherited from the input.
"""
from gpu_worker.semantic_resources import Contract, parse_contract, parse_descriptor, validate


def problem_words(problem, actual, contract):
    if problem.field == 'semantic':
        meanings = {'intensity': 'intensity pixels', 'class-labels': 'class labels',
                    'instance-labels': 'individual-object labels', 'probabilities': 'probabilities',
                    'embeddings': 'embeddings', 'object-table': 'object measurements', 'lineage-graph': 'a lineage graph'}
        expected = contract.exact.wire().get('semantic')
        required = meanings.get(expected, expected or 'a declared scientific meaning')
        found = actual.get('semantic')
        description = f'This result contains {meanings.get(found, found)}' if found else 'This result’s scientific meaning is unknown'
        return f'{description}. This tool needs {required}. Choose a result with that meaning or run an explicit conversion.'
    return f'{problem.message}. {problem.next}'


def _require(actual, contract, *, peers=None, resource):
    problems = validate(actual, contract, peers=peers, resource=resource)
    if problems:
        raise ValueError(problem_words(problems[0], actual, contract))


def observed(descriptor, facts, *, resource):
    declared = parse_descriptor(descriptor).wire()
    _require(facts, Contract(exact={key: declared[key] for key in facts if declared.get(key) is not None}),
             resource=resource)
    return parse_descriptor({**declared, **facts}).wire()


def input_for(declaration, picture, descriptor):
    if (str(picture.dtype) != 'uint8' or picture.ndim != 3 or picture.shape[2] != 3
            or not 0 < picture.shape[0] * picture.shape[1] <= 1 << 20):
        raise ValueError('This scientific bridge requires a one-megapixel or smaller RGB8 array; conversion must be explicit.')
    port = declaration.data_inputs[0].id
    facts = {'dtype': str(picture.dtype), 'axes': ['y', 'x', 'c'], 'shape': list(picture.shape)}
    actual = observed(descriptor if descriptor is not None else {}, facts, resource=port)
    inputs = {port: actual}
    for name, requirement in declaration.scientific.get('inputs', {}).items():
        _require(inputs[name], parse_contract(requirement), peers=inputs, resource=name)
    return inputs


def compatibility(declaration, descriptor):
    """Metadata-only preview; byte observation and dispatch still perform full checks."""
    ports = getattr(declaration, 'scientific', {})
    if not ports:
        return None
    port = declaration.data_inputs[0].id
    actual = parse_descriptor(descriptor if descriptor is not None else {}).wire()
    contract = parse_contract(ports['inputs'][port])
    problems = validate(actual, contract, peers={port: actual}, resource=port)
    return {'compatible': not problems,
            'says': (problem_words(problems[0], actual, contract) if problems else
                     'Recorded metadata matches. Original pixels are checked again when you run.')}


def output_for(declaration, output, descriptor, inputs):
    port = declaration.outputs[0].id
    actual = parse_descriptor(descriptor).wire()
    if actual.get('reference') is not None:
        raise ValueError('An output cannot assign its retained identity; the app assigns it after validation.')
    facts = {}
    if 'picture' in output:
        picture = output['picture']
        facts = {'dtype': str(picture.dtype), 'axes': ['y', 'x', 'c'], 'shape': list(picture.shape),
                 'storage_format': 'png'}
    actual = observed(actual, facts, resource=port)
    for name, requirement in declaration.scientific.get('outputs', {}).items():
        _require(actual, parse_contract(requirement), peers=inputs, resource=name)
    return {**output, 'scientific': actual,
            'scientificEvidence': {'producerFields': sorted(set(actual) - set(facts)),
                                   'observedFields': sorted(facts)}}


def reference_agrees(actual, reference):
    expected = parse_descriptor(reference).wire()
    _require(actual, Contract(exact={key: value for key, value in expected.items() if value is not None}),
             resource='reference output')
