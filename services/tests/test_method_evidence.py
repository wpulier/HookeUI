"""Synthetic actions and native masks in real accepted Project storage; no UI/MT6 qualification."""
import copy

import numpy as np
import pytest

from orchestrator import method_evidence
from orchestrator.scientific_edits import ResourceEdits
from orchestrator.scientific_history import ScientificHistory
from orchestrator.scientific_projects import Conflict, InvalidProject, ProjectStore
from tests.test_scientific_edits import scene, values


def selection(saved, component='array.labels'):
    return {'kind': 'array', 'session': saved.session, 'id': saved.id, 'revision': saved.revision,
            'component': component, 'digest': saved.digest}


def correct(store, saved, label, name):
    history = ScientificHistory(store, saved, 'kernel-' + name, tab='tab', placement='place')
    identity = 'gesture-' + name + '-1234567890123456'
    history.begin_action(identity, {'op': 'labels', 'tool': 'paint', 'label': str(label)}, {'source': saved.digest})
    history.finish_action(identity, {'edit': {'state': 'applied', 'changed': True}})
    owner = ResourceEdits(store, saved.id, saved.session, saved.revision, saved.document['resources'][0], layer='labels')
    source = store.array_source(saved.id, saved.session, saved.revision, saved.document['resources'][0])
    patch = np.full(source.chunks, label, dtype=source.dtype)
    edit = owner.begin(max_written_bytes=patch.nbytes, producer=history.producer())
    edit.write([0, 0], patch)
    return edit.commit(), history


def comparison_inputs(scene):
    store, source, _, _ = scene
    a, first = correct(store, source, 31, 'a')
    b, second = correct(store, a, 32, 'b')
    arms = [{'id': 'a', 'factor': 'boundary-definition', 'says': 'membrane only', 'status': 'completed', 'mask': selection(a)},
            {'id': 'b', 'factor': 'boundary-definition', 'says': 'including fringe', 'status': 'completed', 'mask': selection(b)}]
    return store, source, arms, (a, b), (first, second)


def test_retains_exact_native_arms_source_and_manual_history_without_rerunning(scene):
    store, source, arms, masks, histories = comparison_inputs(scene)
    original = source.document
    kept = method_evidence.create(store, source.session, selection(source), arms, actor='studio')
    arms[0]['says'] = 'mutated after admission'
    reopened = ProjectStore(store.root)
    evidence = method_evidence.read(reopened, kept.id, source.session, kept.revision)
    assert evidence['source']['revisionDigest'] == source.digest
    assert evidence['arms'][0]['says'] == 'membrane only'
    assert evidence['evidenceComplete'] is True and evidence['qualification'] == 'not-assessed'
    assert evidence['chosen'] is None
    for arm, mask, history in zip(evidence['arms'], masks, histories):
        assert arm['mask']['revisionDigest'] == mask.digest
        assert arm['mask']['resource'] == mask.document['resources'][0]
        assert history.record.digest in [h['digest'] for h in arm['histories']]
        assert arm['timing']['scope'] == 'recorded actions and cells; not model inference timing'
        assert arm['timing']['seconds'] >= 0
        assert 'implementation' not in arm['declaration'], 'manual methods need no invented model'
        assert np.array_equal(values(reopened, mask, mask.document['resources'][0]), values(store, mask, mask.document['resources'][0]))
    assert reopened.read(source.id, source.session, revision=source.revision).document == original


def test_choice_is_one_cas_revision_and_original_alternatives_survive_export(scene, tmp_path):
    from orchestrator import scientific_exchange
    store, source, arms, masks, _ = comparison_inputs(scene)
    kept = method_evidence.create(store, source.session, selection(source), arms, actor='studio')
    chosen = method_evidence.choose(store, kept.id, source.session, kept.revision, 'b',
                                    'The question includes the fringe', actor='researcher')
    assert chosen.revision == kept.revision + 1
    assert method_evidence.read(store, kept.id, source.session, kept.revision)['chosen'] is None
    before = method_evidence.read(store, kept.id, source.session, chosen.revision)
    assert before['chosen']['arm'] == 'b' and before['chosen']['by'] == 'researcher'
    with pytest.raises(Conflict):
        method_evidence.choose(store, kept.id, source.session, kept.revision, 'a', 'Another reason', actor='studio')
    with pytest.raises(InvalidProject, match='already chosen'):
        method_evidence.choose(store, kept.id, source.session, chosen.revision, 'a', 'Replace it', actor='studio')
    bundle = tmp_path / 'comparison.zip'
    scientific_exchange.export(store, kept.id, source.session, chosen.revision, destination=bundle)
    fresh = ProjectStore(tmp_path / 'reopened')
    scientific_exchange.reimport(fresh, bundle)
    assert method_evidence.read(fresh, kept.id, source.session, chosen.revision) == before
    for saved in (source, *masks):
        assert fresh.read(saved.id, source.session, revision=saved.revision).digest == saved.digest
        assert np.array_equal(values(fresh, saved, saved.document['resources'][0]),
                              values(store, saved, saved.document['resources'][0]))


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
def test_unsuccessful_arm_retains_declared_outcome_without_fake_mask_or_zero_area(scene, status):
    store, source, arms, _, _ = comparison_inputs(scene)
    arms[1] = {'id': 'b', 'factor': 'algorithm', 'says': 'Second procedure', 'status': status,
               'outcome': {'says': 'Procedure stopped before accepting a result'}}
    kept = method_evidence.create(store, source.session, selection(source), arms, actor='researcher')
    evidence = method_evidence.read(store, kept.id, source.session, kept.revision)
    failed = evidence['arms'][1]
    assert failed['status'] == status and failed['declaredBy'] == 'researcher'
    assert 'mask' not in failed and 'area' not in failed and 'pixels' not in failed
    assert failed['timing']['seconds'] is None
    assert failed['limitations'] and evidence['evidenceComplete'] is False
    with pytest.raises(InvalidProject, match='accepted mask'):
        method_evidence.choose(store, kept.id, source.session, kept.revision, 'b', 'Prefer this', actor='studio')


def test_recorded_error_keeps_exact_history_and_cannot_name_a_success_as_failure(scene):
    from tests.test_scientific_history import transcript
    store, source, arms, _, _ = comparison_inputs(scene)
    history = ScientificHistory(store, source, 'failed-kernel', tab='tab', placement='place')
    cell = 'failed-cell-1234567890123456'
    history.begin(cell, 'raise ValueError("fixture")')
    history.finish(cell, transcript(error='ValueError: fixture'))
    ref = history.producer()['history']
    arms[1] = {'id': 'b', 'factor': 'algorithm', 'says': 'Failed alternative', 'status': 'failed',
               'outcome': {'says': 'The procedure raised an exception', 'history': ref,
                           'event': {'kind': 'cell', 'id': cell}}}
    kept = method_evidence.create(store, source.session, selection(source), arms, actor='studio')
    failed = method_evidence.read(store, kept.id, source.session, kept.revision)['arms'][1]
    assert failed['histories'] == [ref]
    assert failed['observed']['transcript']['error'] == 'ValueError: fixture'
    assert failed['timing']['seconds'] >= 0
    arms[1]['outcome']['event']['id'] = 'not-the-recorded-cell'
    with pytest.raises(InvalidProject, match='terminal event'):
        method_evidence.create(store, source.session, selection(source), arms, actor='studio')


def test_ordinary_document_save_cannot_forge_or_rewrite_method_evidence(scene):
    from orchestrator.scientific_provenance import guard
    store, source, arms, _, _ = comparison_inputs(scene)
    kept = method_evidence.create(store, source.session, selection(source), arms, actor='studio')
    forged = kept.document
    forged['state']['methodEvidence']['chosen'] = {'arm': 'a', 'rationale': 'invented'}
    with pytest.raises(InvalidProject, match='document save'):
        guard(kept, forged)
    with pytest.raises(InvalidProject, match='document save'):
        guard(None, kept.document)


def native_mask(store, *, shape=(4, 6), axes=('y', 'x'), calibration=None, session='s1'):
    from tests.test_scientific_arrays import accept, fill, project
    owner = project(store, session)
    data = np.ones(shape, dtype='<u8') * (2**60 + 1)
    scientific = {'semantic': 'instance-labels'}
    if calibration is not None:
        scientific['calibration'] = calibration
    writer = store.array_draft(owner.id, session, component='array.labels', shape=shape,
                               dtype=data.dtype.str, axes=axes, chunks=shape, scientific=scientific,
                               max_written_bytes=data.nbytes)
    return accept(store, owner, fill(writer, data))


def test_unknown_manual_provenance_is_explicitly_incomplete_and_uint64_bytes_stay_native(scene):
    store, source, arms, _, _ = comparison_inputs(scene)
    unknown = native_mask(store)
    arms[1]['mask'] = selection(unknown)
    kept = method_evidence.create(store, source.session, selection(source), arms, actor='researcher')
    evidence = method_evidence.read(store, kept.id, source.session, kept.revision)
    assert not evidence['evidenceComplete']
    assert evidence['arms'][1]['histories'] == [] and evidence['arms'][1]['limitations']
    assert evidence['arms'][1]['declaration'] == {}
    assert values(store, unknown, unknown.document['resources'][0])[0, 0] == 2**60 + 1


@pytest.mark.parametrize('change', ['digest', 'revision', 'session', 'component'])
def test_wrong_exact_native_selection_refuses_before_publishing(scene, change):
    store, source, arms, _, _ = comparison_inputs(scene)
    selected = selection(source)
    selected[change] = {'digest': '0' * 64, 'revision': source.revision + 1,
                        'session': 'different-session', 'component': 'array.absent'}[change]
    heads = [(saved.id, saved.digest) for saved in store.list(source.session)]
    with pytest.raises(ValueError):
        method_evidence.create(store, source.session, selected, arms, actor='studio')
    assert [(saved.id, saved.digest) for saved in store.list(source.session)] == heads


@pytest.mark.parametrize('different', ['shape', 'axes', 'calibration'])
def test_native_mask_layout_must_match_frozen_source(scene, different):
    store, source, arms, _, _ = comparison_inputs(scene)
    options = {'shape': {'shape': (3, 6)}, 'axes': {'axes': ('x', 'y')},
               'calibration': {'calibration': {'space': {'session': 's1', 'id': 'field', 'revision': 1},
                                              'axes': ['y', 'x'], 'units': ['um', 'um'],
                                              'affine': [[2, 0, 0], [0, 3, 0]]}}}[different]
    arms[1]['mask'] = selection(native_mask(store, **options))
    before = [(saved.id, saved.digest) for saved in store.list(source.session)]
    with pytest.raises(InvalidProject, match='shape|axes|calibration'):
        method_evidence.create(store, source.session, selection(source), arms, actor='studio')
    assert [(saved.id, saved.digest) for saved in store.list(source.session)] == before


def test_known_other_source_lineage_is_not_downgraded_to_unrecorded(scene):
    store, source, arms, _, _ = comparison_inputs(scene)
    other = native_mask(store)
    changed, _ = correct(store, other, 51, 'other-source')
    arms[1]['mask'] = selection(changed)
    with pytest.raises(InvalidProject, match='different frozen input'):
        method_evidence.create(store, source.session, selection(source), arms, actor='studio')


def test_metadata_allowance_and_publication_failure_do_not_change_any_accepted_inputs(scene, monkeypatch):
    store, source, arms, _, _ = comparison_inputs(scene)
    before = [(saved.id, saved.digest) for saved in store.list(source.session)]
    oversized = copy.deepcopy(arms)
    oversized[1]['declaration'] = {'notes': 'x' * method_evidence.MAX_METADATA}
    with pytest.raises(InvalidProject, match='allowance'):
        method_evidence.create(store, source.session, selection(source), oversized, actor='studio')
    write = store._write
    def fail_head(path, data, **kwargs):
        if path.name == 'head.json':
            raise OSError('fixture interrupted atomic publication')
        return write(path, data, **kwargs)
    monkeypatch.setattr(store, '_write', fail_head)
    with pytest.raises(OSError, match='fixture'):
        method_evidence.create(store, source.session, selection(source), arms, actor='studio')
    assert [(saved.id, saved.digest) for saved in store.list(source.session)] == before
    for saved in store.list(source.session):
        assert saved.document['kind'] != method_evidence.KIND


@pytest.mark.parametrize('revision', [None, True, 0])
def test_reopen_requires_an_exact_accepted_comparison_revision(scene, revision):
    store, source, arms, _, _ = comparison_inputs(scene)
    kept = method_evidence.create(store, source.session, selection(source), arms, actor='studio')
    with pytest.raises(InvalidProject, match='exact'):
        method_evidence.read(store, kept.id, source.session, revision)


def test_actual_output_publication_retains_calibration_histories_and_runtime_holds_on_reimport(scene, tmp_path):
    from env_worker.scientific_output_wire import calibration
    from orchestrator import scientific_exchange, scientific_retention
    from orchestrator.scientific_outputs import OutputSave
    from tests.test_scientific_history import transcript
    from tests.test_scientific_outputs import attempt
    from tests.test_scientific_tool_save import ToolWorker
    store, saved, ordinary, _ = attempt(scene)
    worker = ToolWorker()
    axes = ['z', 'x']
    coordinates = calibration(worker.info, axes, {'session': saved.session, 'id': saved.id, 'revision': 1})
    coordinates['units'] = ['um', 'µm']  # alias names retain the same anisotropic 2×3 scale and origin
    writer = store.array_draft(saved.id, saved.session, component='array.known', shape=worker.data.shape,
                               dtype=worker.data.dtype.str, axes=axes, chunks=worker.data.shape,
                               scientific={'semantic': 'instance-labels', 'calibration': coordinates},
                               max_written_bytes=worker.data.nbytes)
    writer.write([0, 0], worker.data)
    reference = writer.finish()
    document = saved.document
    document['resources'].append(reference)
    document['state']['scientificView']['inputs'] = {'known': reference}
    source = store.save(saved.id, saved.session, document, expected_revision=saved.revision)
    worker.request = lambda _: ({'inventory': {'layers': [{'id': 'a' * 32, 'editableInput': 'known'}]}}, b'')
    arms, outputs, histories = [], [], []
    for name, value in [('a', 41), ('b', 42)]:
        history = ScientificHistory(store, source, 'output-' + name, tab='tab', placement='place')
        cell = 'cell-' + name + '-1234567890123456'
        history.begin(cell, f'labels.data[:] = {value}')
        history.finish(cell, transcript(error='', stdout='', result=''))
        worker.data[:] = value  # synthetic transport; no worker/code execution claim
        save = OutputSave(store, source, 'save-' + name + '-1234567890123456', ordinary.layer,
                          {'semantic': 'instance-labels', 'axes': axes}, producer=history.producer(), tool=True)
        try:
            save.capture(worker)
            save.publish()
            output = store.read(save.target, source.session)
        finally:
            save.close()
        if name == 'b':
            output, correction = correct(store, output, 43, 'corrected-output')
            from orchestrator.scientific_provenance import resolve
            assert any('no longer holds every output' in problem for problem in
                       resolve(store, output.id, output.session, output.revision)['problems'])
            selected = resolve(store, output.id, output.session, output.revision,
                               resource=output.document['resources'][0])
            assert not selected['problems'] and selected['outputs'] is None
            assert selected['edits'][0]['producer']['history']['digest'] == correction.record.digest
            with pytest.raises(InvalidProject, match='not held'):
                resolve(store, output.id, output.session, output.revision, resource=reference)
        outputs.append(output)
        histories.append(history.record)
        arms.append({'id': name, 'factor': 'settings', 'says': f'Declared setting {value}', 'status': 'completed',
                     'mask': selection(output, 'array.output'), 'declaration': {'settings': {'label': value}}})
    runtime_root = tmp_path / 'runtime-holds'
    scientific_retention.bind(store, runtime_root)
    kept = method_evidence.create(store, source.session, selection(source, 'array.known'), arms, actor='studio')
    evidence = method_evidence.read(store, kept.id, source.session, kept.revision)
    assert evidence['evidenceComplete'] and evidence['descriptor']['calibration'] == coordinates
    assert evidence['runtimes'] == [{'bundle': 'mine/runtime', 'generation': 'g1', 'operation': 'viewer'}]
    assert all(history.digest in [ref['digest'] for ref in row['histories']]
               for row, history in zip(evidence['arms'], histories))
    assert correction.record.digest in [ref['digest'] for ref in evidence['arms'][1]['histories']]
    with scientific_retention.locked(runtime_root):
        held = [ref for ref in scientific_retention.references(runtime_root) if ref['document'] == kept.id]
        assert len(held) == 1 and held[0]['generation'] == 'g1'
    bundle = tmp_path / 'output-comparison.zip'
    scientific_exchange.export(store, kept.id, source.session, kept.revision, destination=bundle)
    fresh_root = tmp_path / 'fresh-runtime-holds'
    fresh = ProjectStore(tmp_path / 'fresh-profile', runtime_root=fresh_root)
    scientific_exchange.reimport(fresh, bundle)
    assert method_evidence.read(fresh, kept.id, source.session, kept.revision) == evidence
    for output in outputs:
        ref = output.document['resources'][0]
        assert values(fresh, output, ref).tobytes() == values(store, output, ref).tobytes()
    with scientific_retention.locked(fresh_root), pytest.raises(scientific_retention.Retained):
        scientific_retention.require_removable(fresh_root, models={'mine/runtime'}, generations={'g1'})
    changed = store.save(source.id, source.session, {**source.document, 'title': 'Later revision'},
                         expected_revision=source.revision)
    with pytest.raises(InvalidProject, match='different frozen input revision'):
        method_evidence.create(store, source.session, selection(changed, 'array.known'), arms, actor='studio')


def test_observed_interruption_retains_uncertainty_and_failure_history_exports(scene, tmp_path):
    from orchestrator import scientific_exchange
    store, source, arms, _, _ = comparison_inputs(scene)
    history = ScientificHistory(store, source, 'interrupted-kernel', tab='tab', placement='place')
    cell = 'cancelled-cell-1234567890123456'
    history.begin(cell, 'long_procedure()')
    history.finish(cell, interrupted=True)
    arms[1] = {'id': 'b', 'factor': 'algorithm', 'says': 'Interrupted alternative', 'status': 'cancelled',
               'outcome': {'says': 'Stopped while running', 'history': history.producer()['history'],
                           'event': {'kind': 'cell', 'id': cell}}}
    kept = method_evidence.create(store, source.session, selection(source), arms, actor='researcher')
    before = method_evidence.read(store, kept.id, source.session, kept.revision)
    assert before['arms'][1]['observed']['state'] == 'interrupted'
    assert 'may have run' in before['arms'][1]['observed']['explanation']
    assert before['arms'][1]['timing']['seconds'] is None and not before['evidenceComplete']
    bundle = tmp_path / 'interrupted-comparison.zip'
    scientific_exchange.export(store, kept.id, source.session, kept.revision, destination=bundle)
    fresh = ProjectStore(tmp_path / 'reopened')
    scientific_exchange.reimport(fresh, bundle)
    assert method_evidence.read(fresh, kept.id, source.session, kept.revision) == before
    assert fresh.read(history.record.id, source.session, revision=history.record.revision).digest == history.record.digest


@pytest.mark.parametrize('corruption', ['digest', 'missing', 'kernel'])
def test_resource_selection_does_not_hide_malformed_or_mismatched_producer_history(scene, corruption):
    from orchestrator.scientific_provenance import resolve
    store, source, arms, masks, _ = comparison_inputs(scene)
    latest = masks[1]
    document = latest.document
    producer = document['state']['hookeEdit']['producer']
    if corruption == 'digest':
        producer['history']['digest'] = '0' * 64
    elif corruption == 'missing':
        producer['history'] = {'session': source.session}
    else:
        producer['kernel'] = 'another-kernel'
    # Deliberately bypass the ordinary-save guard to simulate an inconsistent imported producer record.
    forged = store.save(latest.id, latest.session, document, expected_revision=latest.revision)
    arms[1]['mask'] = selection(forged)
    observed = resolve(store, forged.id, forged.session, forged.revision, resource=forged.document['resources'][0])
    assert any('history' in problem for problem in observed['problems'])
    with pytest.raises(InvalidProject, match='history'):
        method_evidence.create(store, source.session, selection(source), arms, actor='studio')


def test_retention_and_reopen_use_metadata_without_reading_or_rerunning_native_values(scene, monkeypatch):
    from dataclasses import replace
    store, source, arms, _, _ = comparison_inputs(scene)
    original = store.array_source
    def forbidden(*args, **kwargs):
        pytest.fail('Method evidence must not read native values or rerun a producer')
    def metadata_only(*args, **kwargs):
        return replace(original(*args, **kwargs), read=forbidden)
    monkeypatch.setattr(store, 'array_source', metadata_only)
    kept = method_evidence.create(store, source.session, selection(source), arms, actor='studio')
    assert method_evidence.read(store, kept.id, source.session, kept.revision)['evidenceComplete']


@pytest.mark.parametrize('field', ['arms', 'source', 'actor'])
def test_malformed_imported_evidence_refuses_as_invalid_project(scene, field):
    store, source, arms, _, _ = comparison_inputs(scene)
    kept = method_evidence.create(store, source.session, selection(source), arms, actor='studio')
    document = kept.document
    document['state']['methodEvidence'][field] = None
    malformed = store.create(source.session, document)  # a self-consistent but unsupported imported document
    with pytest.raises(InvalidProject):
        method_evidence.read(store, malformed.id, source.session, malformed.revision)


@pytest.mark.parametrize('correct_input', [False, True])
def test_reader_output_then_tool_selection_preserves_original_and_corrected_method_lineage(scene, monkeypatch, correct_input):
    """Actual retained journey shape: reader output → selection → output → correction.

    Synthetic transport uses real publication, selection transactions and native storage;
    the separate ordinary-UI receipt proves the actual Cellpose/reader execution.
    """
    from contextlib import contextmanager
    from types import SimpleNamespace
    from gpu_worker.bundle_contract import parse_members, view
    from orchestrator import scientific_tools, scientific_provenance
    from orchestrator.scientific_outputs import OutputSave
    from tests.test_scientific_outputs import attempt
    from tests.test_scientific_tool_save import ToolWorker
    from tests.test_scientific_output_coordinates import proof
    from tests.test_scientific_tools import OWNER, RAW, KEY, GEN

    store, saved, ordinary, _ = attempt(scene)
    worker = ToolWorker()
    first = OutputSave(store, saved, 'reader-output-1234567890123', ordinary.layer, ordinary.meaning, tool=True)
    try:
        first.capture(worker); first.publish()
        source = store.read(first.target, saved.session)
    finally:
        first.close()
    if correct_input:
        source, _ = correct(store, source, 44, 'before-tool-selection')
        worker.data = values(store, source, source.document['resources'][0]).copy()
    owner = SimpleNamespace(**vars(OWNER), members=(RAW,))
    parsed = parse_members([RAW], owner)[0]
    @contextmanager
    def prepared(key, generation, environ):
        assert key == KEY and generation == GEN
        yield SimpleNamespace(m=view(owner, parsed))
    monkeypatch.setattr(scientific_tools.prepared_bundles, 'prepared_member', prepared)
    monkeypatch.setattr(scientific_tools.env_runner, 'in_use', lambda *args: None)
    monkeypatch.setattr(scientific_tools.env_runner, 'generation', lambda *args: None)
    chosen = scientific_tools.bind(store, source.id, source.session, source.revision, KEY, GEN)
    assert chosen.document['state']['hookeOutput'] == source.document['state']['hookeOutput']
    reference = source.document['resources'][0]
    worker.info['coordinatesFrom'] = {'format': 'same-input-grid-v1', 'input': 'output',
                                     'reference': reference, 'sha256': proof(worker.data)}
    history = ScientificHistory(store, chosen, 'selected-tool', tab='tab', placement='place')
    action = 'tool-action-1234567890123456'
    history.begin_action(action, {'op': 'native-panel-change'}, {})
    history.finish_action(action, {'nativeChanged': True})
    result = OutputSave(store, chosen, 'tool-output-1234567890123', ordinary.layer, ordinary.meaning,
                        producer=history.producer(), tool=True)
    try:
        result.capture(worker); result.publish()
        output = store.read(result.target, chosen.session)
    finally:
        result.close()
    corrected, correction = correct(store, output, 43, 'selected-tool-correction')
    arms = [{'id': name, 'factor': 'boundary-definition', 'says': name, 'status': 'completed',
             'mask': selection(record, 'array.output')} for name, record in [('a', output), ('b', corrected)]]
    evidence = method_evidence.prepare(store, source.session, selection(source, 'array.output'), arms, 'studio')
    resolved = scientific_provenance.resolve(store, chosen.id, chosen.session, chosen.revision, resource=reference)
    assert resolved['edits'] if correct_input else resolved['outputs'] is not None
    assert resolved['inputSelection']['base']['digest'] == source.digest
    assert not resolved['problems']
    assert evidence['evidenceComplete']
    for arm in evidence['arms']:
        assert arm['inputSelections'][0]['base']['digest'] == source.digest
        assert history.record.digest in [h['digest'] for h in arm['histories']]
    assert correction.record.digest in [h['digest'] for h in evidence['arms'][1]['histories']]
    wrong_source = {**selection(source, 'array.output'), 'digest': '0' * 64}
    with pytest.raises(InvalidProject, match='digest'):
        method_evidence.prepare(store, source.session, wrong_source, arms, 'studio')
