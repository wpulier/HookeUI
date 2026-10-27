"""Python bundle members share an installed owner and require per-configuration references."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import time

import numpy as np

from gpu_worker import bundle_contract, manifests as catalog

BOUNDARY = 1
MAX_FILE = 8 << 20
MAX_PIXELS = 1 << 20


def declarations(owner):
    return bundle_contract.parse_members(list(owner.members), owner)


def adapters_present(owner, folder):
    return all((folder.joinpath(*member.adapter.partition(':')[0].split('.')).with_suffix('.py')).is_file()
               for member in declarations(owner))


@dataclass(frozen=True)
class InstalledMember:
    owner: object
    m: bundle_contract.CapabilityView

    folder = property(lambda self: self.owner.folder)
    python = property(lambda self: self.owner.python)
    entry = property(lambda self: self.owner.entry)
    current = property(lambda self: self.owner.current)
    proof = property(lambda self: self.owner.proof)


def expand(installed):
    if not installed.m.members:
        return (installed,)
    return tuple(InstalledMember(installed, bundle_contract.view(installed.m, member))
                 for member in declarations(installed.m))


def case_key(member, case):
    return hashlib.sha256(json.dumps({'member': member.id, 'case': case}, sort_keys=True,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def worker_identity():
    from . import bundle_review, env_runner, envs, fiji_sandbox, reference_checks, sandbox, worker_resources, scientific_ports
    from gpu_worker import semantic_resources
    from env_worker import region_reader
    from . import region_inputs, admission, typed_files
    sources = [env_runner.SERVER, Path(env_runner.__file__), Path(sandbox.__file__), Path(__file__),
               Path(bundle_contract.__file__), Path(reference_checks.__file__), Path(worker_resources.__file__),
               Path(fiji_sandbox.__file__), Path(bundle_review.__file__), Path(scientific_ports.__file__),
               Path(semantic_resources.__file__), Path(region_reader.__file__), Path(region_inputs.__file__),
               Path(admission.__file__), Path(typed_files.__file__)]
    return hashlib.sha256(b''.join(envs._file_sha(path) for path in sources)).hexdigest()


def _typed_case(installed, member, worker, snapshot, case):
    """A typed member's reference check: each port's exact example array, its declared output compared to the reference."""
    from . import admission, reference_checks, typed_files, env_runner
    files = {port.id: {'path': snapshot / case['inputs'][port.id],
                      'descriptor': case.get('scientific', {}).get('inputs', {}).get(port.id, {}),
                      'reference': {'kind': 'example', 'file': case['inputs'][port.id]}}
             for port in admission.data_inputs(member.m)}
    try:
        rows = typed_files.load(member.m, files, maximum_file=MAX_FILE).typed
    except admission.Refused as exc:
        raise env_runner.Failed(admission.refusal_words(exc)) from exc
    reference = _bytes(snapshot, case['reference'])
    output = member.m.outputs[0].type
    actual = worker.run_typed(rows, case['params'], output=output)
    verdict = reference_checks.compare(actual, json.loads(reference, object_pairs_hook=_pairs), case['tolerance'],
                                       output_type=output)
    # The input evidence names each port's exact example array (typed inputs are not one picture).
    inputs = json.dumps({row.port: {'file': case['inputs'][row.port], 'sha256': row.sha256, 'shape': list(row.array.shape),
                                    'dtype': row.array.dtype.str, 'descriptor': row.descriptor} for row in rows}, sort_keys=True).encode()
    from . import bundle_review
    key = case_key(member.m.member, case)
    record = {'case': key, 'member': member.m.key, 'operation': member.m.operation,
              'device': case['device'], 'parameters': case['params'], 'source': case['source'],
              'tolerance': case['tolerance'], 'inputs': {row.port: row.sha256 for row in rows},
              'artifacts': bundle_review.store(installed, key, inputs, reference, actual, output, input_kind='json'),
              'inputSha256': hashlib.sha256(inputs).hexdigest(), 'referenceSha256': hashlib.sha256(reference).hexdigest(), **verdict}
    if not verdict['ok']:
        raise env_runner.Failed(f"{member.m.name}'s reference check failed: {verdict['why']}")
    return record


def proof_complete(m, folder, proof):
    if not m.members:
        return bool(proof and proof.get('ok'))
    if not isinstance(proof, dict) or proof.get('ok') is not True:
        return False
    try:
        from .bundle_review import descriptors_valid
        if proof.get('scope') == 'local-interactive-workflow-v1':
            from . import local_workflow
            return local_workflow.complete(m, folder, proof)
        if any(member.bridge == 'scientific-host-v1' for member in declarations(m)):
            from . import reader_proof
            return reader_proof.complete(m, folder, proof)
        wanted = [case_key(member, case) for member in declarations(m) for case in member.reference_cases]
        if any(not member.reference_cases for member in declarations(m)):
            return False
        records = proof.get('cases')
        examples = {bundle_contract.capability_key(m.key, member.id): [
            {'input': test['input'], 'outcome': test.get('outcome', 'result'),
             **({'count': test['count']} if 'count' in test else {})} for test in member.tests]
                    for member in declarations(m)}
        return (proof.get('boundary') == BOUNDARY and proof.get('deviceBoundary') == 'macos-no-iokit-v1' and proof.get('workerSha256') == worker_identity()
                and proof.get('generation') == folder.name and proof.get('scope') == 'bundle-reference'
                and proof.get('examples') == examples
                and isinstance(records, list) and len(records) == len(wanted)
                and [r.get('case') for r in records] == wanted
                and all(r.get('ok') is True and r.get('device') == 'cpu'
                        and descriptors_valid(r.get('artifacts')) for r in records))
    except (ValueError, TypeError, AttributeError, OSError):
        return False


def checked_parameters(installed, params):
    if not isinstance(installed, InstalledMember):
        return True
    wanted = {k: v for k, v in params.items() if k not in catalog.HIDDEN}
    return any(case['params'] == wanted for case in installed.m.member.reference_cases)


def _bytes(folder, name):
    path = folder / name
    if path.is_symlink() or path.parent != folder or not path.is_file():
        raise ValueError('The reference check needs a regular example or reference file in its installed bundle')
    with path.open('rb') as stream:
        body = stream.read(MAX_FILE + 1)
    if len(body) > MAX_FILE:
        raise ValueError('The reference file exceeds the 8 MiB check limit')
    return body


def _picture(body):
    from PIL import Image
    with Image.open(io.BytesIO(body)) as image:
        if image.width * image.height > MAX_PIXELS or image.width < 1 or image.height < 1:
            raise ValueError('The reference picture exceeds the one-megapixel check limit')
        return np.array(image.convert('RGB'), dtype=np.uint8)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('A reference field was repeated')
        result[key] = value
    return result


def prove(m, environ, stop):
    from . import bundle_review, env_runner, envs, owned_process, reference_checks
    env_runner.preserve_active(m, environ)
    folder = envs.folder(m, environ)
    snapshot = folder / envs.SNAPSHOT / m.id
    kept, problems = catalog.read(snapshot / catalog.MANIFEST, m.namespace, m.channel)
    if kept is None:
        raise env_runner.Failed('The installed bundle declaration is invalid: ' + '; '.join(problems))
    from . import bundle_admission
    if bundle_admission.local_workflow(kept):
        previous = env_runner._proof_of(folder)
        if proof_complete(kept, folder, previous):
            return previous
        raise env_runner.Failed('Try every declared tool on a saved Project, use its controls, Save tool and reopen it; then review the observed local workflow in Models. An RGB check cannot replace this interaction.')
    installed = env_runner.Installed(kept, folder, {}, True)
    started, records = time.monotonic(), []
    result = {'ok': False, 'scope': 'bundle-reference', 'boundary': BOUNDARY,
              'workerSha256': worker_identity(), 'deviceBoundary': 'macos-no-iokit-v1', 'generation': folder.name, 'cases': records,
              'at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'examples': {}}
    try:
        with owned_process.scope(seconds=envs.STEP_S, stop=stop):
            if any(member.bridge == 'scientific-host-v1' for member in declarations(kept)):
                from . import reader_proof
                result.update(scope='reader-data-contract-v1', fullQualification=False)
                result = reader_proof.prove(installed, environ, records=records)
            else:
                _prove_operations(installed, environ, snapshot, records, result)
        if result.get('scope') == 'bundle-reference':
            result.update(ok=True, says=f'{len(records)} operation reference checks passed on CPU')

    except (env_runner.Failed, ValueError, OSError, owned_process.Stopped) as exc:
        result['why'] = str(exc)
        if stop():
            result['stopped'] = True
    result['seconds'] = round(time.monotonic() - started, 2)
    result.setdefault('at', time.strftime('%Y-%m-%dT%H:%M:%S'))
    if result.get('scope') == 'reader-data-contract-v1' and result.get('ok'):
        result = reader_proof.seal(installed, {key: value for key, value in result.items() if key != 'issuerSeal'})
    path = folder.with_name(folder.name + '.proof.json')
    previous = env_runner._proof_of(folder)
    if not result['ok'] and proof_complete(kept, folder, previous):
        path = folder.with_name(folder.name + '.proof-attempt.json')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, allow_nan=False))
    temporary.replace(path)
    return result


def _prove_operations(installed, environ, snapshot, records, result):
    from . import bundle_review, env_runner, reference_checks
    for member in expand(installed):
        from . import kinds
        if member.m.bridge == 'scientific-host-v1':
            raise env_runner.Failed(f'{member.m.name} is a private scientific-host candidate. '
                                    'Interactive workspace and independent qualification checks are still required; '
                                    'an RGB operation check cannot enable it.')
        from gpu_worker.bundle_contract import typed as typed_member
        many = typed_member(member.m)
        why = kinds.takes_why(member.m)
        if not many and (len(member.m.data_inputs) != 1 or member.m.data_inputs[0].type != 'image.2d.rgb8'):
            why = 'bundle reference checks currently require one 2D RGB8 image'
        if why or kinds.implements(member.m) is None:
            raise env_runner.Failed(f'{member.m.name} needs an integration before it can run: {why or "this built-in job is not supported by bundles yet"}')
        cases = member.m.member.reference_cases
        if not cases:
            raise env_runner.Failed(f'{member.m.name} needs a pinned reference check before it can be enabled')
        worker = env_runner.Client(member, environ)
        try:
            for case in cases:
                if many:
                    records.append(_typed_case(installed, member, worker, snapshot, case))
                    continue
                body = _bytes(snapshot, case['input'])
                reference = _bytes(snapshot, case['reference'])
                picture = _picture(body)
                output = member.m.outputs[0].type
                expected = ({'picture': _picture(reference)} if output == 'image.2d.rgb8'
                            else json.loads(reference, object_pairs_hook=_pairs))
                semantic_case = case.get('scientific', {})
                actual = worker.run(picture, case['params'], output=output,
                                    **({'scientific': semantic_case.get('input')}
                                       if member.m.scientific else {}))
                if member.m.scientific:
                    from .scientific_ports import input_for, output_for, reference_agrees
                    input_descriptors = input_for(member.m, picture, semantic_case.get('input'))
                    reference_descriptor = output_for(member.m, expected, semantic_case.get('reference', {}), input_descriptors)['scientific']
                    reference_agrees(actual['scientific'], reference_descriptor)
                payload = {key: value for key, value in actual.items() if key not in ('scientific', 'scientificEvidence')}
                verdict = reference_checks.compare(payload, expected, case['tolerance'], output_type=output)
                records.append({'case': case_key(member.m.member, case), 'member': member.m.key,
                                'operation': member.m.operation, 'device': case['device'],
                                'parameters': case['params'], 'source': case['source'], 'tolerance': case['tolerance'],
                                'artifacts': bundle_review.store(installed, case_key(member.m.member, case), body, reference, payload, output),
                                'inputSha256': hashlib.sha256(body).hexdigest(),
                                **({'scientific': {'inputs': input_descriptors,
                                                  'actual': actual['scientific'], 'reference': reference_descriptor,
                                                  'evidence': actual['scientificEvidence']}}
                                   if member.m.scientific else {}),
                                'referenceSha256': hashlib.sha256(reference).hexdigest(), **verdict})
                if not verdict['ok']:
                    raise env_runner.Failed(f"{member.m.name}'s reference check failed: {verdict['why']}")
            def example_descriptor(name):
                descriptors = [case.get('scientific', {}).get('input', {}) for case in cases if case['input'] == name]
                if not descriptors or any(value != descriptors[0] for value in descriptors):
                    raise env_runner.Failed('A scientific example needs one unambiguous reference input descriptor')
                return descriptors[0]

            if many:  # typed inputs are chosen resources, not example pictures
                result['examples'][member.m.key] = []
                continue
            result['examples'][member.m.key] = env_runner.declared_examples(
                member.m, worker, picture_loader=lambda name: _picture(_bytes(snapshot, name)),
                descriptor_loader=example_descriptor if member.m.scientific else None)
        finally:
            worker.stop()
