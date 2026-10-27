"""Real public selection/output/evidence storage with synthetic prepared member and transport; no algorithm qualification."""
import pytest

from orchestrator import method_evidence, scientific_provenance, scientific_tools
from orchestrator.scientific_outputs import OutputSave
from orchestrator.scientific_projects import InvalidProject
from tests.test_method_evidence import selection
from tests.test_scientific_edits import scene  # noqa: F401
from tests.test_scientific_output_coordinates import admitted
from tests.test_scientific_tools import member, KEY, GEN  # noqa: F401


def publish(store, source, worker, name):
    save = OutputSave(store, source, 'selected-tool-output-' + name, 'a' * 32,
                      {'semantic': 'instance-labels', 'axes': ['z', 'x']}, producer={'kind': 'synthetic-transport'})
    try:
        save.capture(worker)
        save.publish()
        return store.read(save.target, source.session)
    finally:
        save.close()


def methods(first, second):
    return [{'id': letter, 'factor': 'algorithm', 'says': 'Synthetic boundary ' + letter, 'status': 'completed',
             'mask': selection(saved, 'array.output')} for letter, saved in [('a', first), ('b', second)]]


def test_selected_tool_output_resolves_to_exact_frozen_preselection_input(scene, member, tmp_path):
    store, source, unused, worker, _, _ = admitted(scene)
    unused.close()
    first = publish(store, source, worker, 'first')
    chosen = scientific_tools.bind(store, source.id, source.session, source.revision, KEY, GEN)
    worker.data[:] = 9
    second = publish(store, chosen, worker, 'second')
    calls = worker.calls
    kept = method_evidence.create(store, source.session, selection(source, 'array.image'),
                                  methods(first, second), actor='studio')
    evidence = method_evidence.read(store, kept.id, kept.session, kept.revision)
    assert [r['digest'] for r in evidence['arms'][1]['lineage']] == [second.digest, chosen.digest, source.digest]
    assert evidence['source']['revisionDigest'] == source.digest
    assert evidence['arms'][1]['inputSelections'][0]['base']['digest'] == source.digest
    assert evidence['arms'][1]['inputSelections'][0]['capability'] == KEY
    assert evidence['arms'][1]['limitations'] and not evidence['evidenceComplete']
    assert worker.calls == calls == 2
    assert store.read(source.id, source.session, revision=source.revision) == source
    from orchestrator import scientific_exchange
    from orchestrator.scientific_projects import ProjectStore
    bundle = tmp_path / 'selected-methods.zip'
    scientific_exchange.export(store, kept.id, kept.session, kept.revision, destination=bundle)
    reopened = ProjectStore(tmp_path / 'reopened')
    scientific_exchange.reimport(reopened, bundle)
    assert method_evidence.read(reopened, kept.id, kept.session, kept.revision) == evidence
    for record in (source, chosen, first, second):
        assert reopened.read(record.id, record.session, revision=record.revision) == record


@pytest.mark.parametrize('damage', ['missing', 'source-digest', 'source-owner', 'runtime', 'held-input'])
def test_untrusted_selection_cannot_admit_output_as_the_original_input(scene, member, damage):
    """Low-level imported metadata is deliberately not an ordinary accepted selection."""
    store, source, unused, worker, _, _ = admitted(scene)
    unused.close()
    first = publish(store, source, worker, 'first')
    chosen = scientific_tools.bind(store, source.id, source.session, source.revision, KEY, GEN)
    document = chosen.document
    state = document['state']
    if damage == 'missing':
        del state['scientificToolChoice']
    elif damage == 'source-digest':
        state['scientificToolChoice']['source']['digest'] = '0' * 64
    elif damage == 'source-owner':
        state['scientificToolChoice']['source']['session'] = 'different-project'
    elif damage == 'runtime':
        state['scientificView']['runtime'] = source.document['state']['scientificView']['runtime']
    else:
        state['scientificView']['inputs']['unadmitted'] = source.document['resources'][0]
    forged = store.save(chosen.id, chosen.session, document, expected_revision=chosen.revision)
    second = publish(store, forged, worker, 'second')
    before = [(row.id, row.digest) for row in store.list(source.session)]
    with pytest.raises(InvalidProject, match='selection|different|digest|selected tool'):
        method_evidence.create(store, source.session, selection(source, 'array.image'),
                               methods(first, second), actor='studio')
    assert [(row.id, row.digest) for row in store.list(source.session)] == before
    assert worker.calls == 2


def test_output_without_explicit_input_association_does_not_gain_selection_lineage(scene, member):
    store, source, unused, worker, _, _ = admitted(scene)
    unused.close()
    first = publish(store, source, worker, 'first')
    chosen = scientific_tools.bind(store, source.id, source.session, source.revision, KEY, GEN)
    del worker.info['coordinatesFrom']
    second = publish(store, chosen, worker, 'second')
    with pytest.raises(InvalidProject, match='calibration and coordinate identity'):
        method_evidence.create(store, source.session, selection(source, 'array.image'),
                               methods(first, second), actor='studio')


def test_later_selected_input_revision_is_not_rebased_to_the_original(scene, member):
    store, source, unused, worker, _, _ = admitted(scene)
    unused.close()
    first = publish(store, source, worker, 'first')
    chosen = scientific_tools.bind(store, source.id, source.session, source.revision, KEY, GEN)
    second = publish(store, chosen, worker, 'second')
    with pytest.raises(InvalidProject, match='no verified tool selection'):
        method_evidence.create(store, source.session, selection(chosen, 'array.image'),
                               methods(first, second), actor='studio')


def test_ordinary_document_save_cannot_add_change_or_remove_tool_selection(scene, member):
    store, source, unused, _, _, _ = admitted(scene)
    unused.close()
    chosen = scientific_tools.bind(store, source.id, source.session, source.revision, KEY, GEN)
    with pytest.raises(InvalidProject, match='document save'):
        scientific_provenance.guard(source, chosen.document)
    edited = chosen.document
    edited['state']['scientificToolChoice']['name'] = 'Invented selection'
    with pytest.raises(InvalidProject, match='document save'):
        scientific_provenance.guard(chosen, edited)
    del edited['state']['scientificToolChoice']
    with pytest.raises(InvalidProject, match='document save'):
        scientific_provenance.guard(chosen, edited)


def test_imported_output_cannot_claim_a_different_input_fingerprint(scene, member, tmp_path):
    """Low-level imported record preserves exact references but forges its value claim."""
    store, source, unused, worker, _, _ = admitted(scene)
    unused.close()
    first = publish(store, source, worker, 'first')
    chosen = scientific_tools.bind(store, source.id, source.session, source.revision, KEY, GEN)
    second = publish(store, chosen, worker, 'second')
    document = second.document
    document['state']['hookeOutput']['coordinatesFrom']['sha256'] = '0' * 64
    import shutil
    from orchestrator.scientific_projects import ProjectStore
    imported = ProjectStore(tmp_path / 'untrusted-archive')
    for record in (source, first):
        shutil.copytree(store.root / record.id, imported.root / record.id)
    shutil.copytree(store.root / second.id / 'arrays', imported.root / second.id / 'arrays')
    forged = imported.create(source.session, document, identity=second.id)
    store = imported
    before = [(row.id, row.digest) for row in store.list(source.session)]
    with pytest.raises(InvalidProject, match='fingerprint'):
        method_evidence.create(store, source.session, selection(source, 'array.image'),
                               methods(first, forged), actor='studio')
    assert [(row.id, row.digest) for row in store.list(source.session)] == before
    assert worker.calls == 2


def test_saved_output_reports_only_its_verified_retained_tool_name(scene, member):
    store, source, unused, worker, _, _ = admitted(scene)
    unused.close()
    chosen = scientific_tools.bind(store, source.id, source.session, source.revision, KEY, GEN)
    output = publish(store, chosen, worker, 'display')
    calls = worker.calls
    found = scientific_provenance.resolve(store, output.id, output.session, output.revision)
    assert found['savedTool']['name'] == 'Next tool'
    assert found['savedTool']['runtime'] == chosen.document['state']['scientificView']['runtime']
    assert found['savedTool']['source'] == {key: found['outputs']['base'][key] for key in ('id', 'session', 'revision', 'digest')}
    assert worker.calls == calls


@pytest.mark.parametrize('damage', ['runtime', 'choice-name', 'choice-owner'])
def test_saved_output_cannot_borrow_a_name_from_mismatched_retained_context(scene, member, damage):
    store, source, unused, worker, _, _ = admitted(scene)
    unused.close()
    chosen = scientific_tools.bind(store, source.id, source.session, source.revision, KEY, GEN)
    if damage != 'runtime':
        document = chosen.document
        choice = document['state']['scientificToolChoice']
        if damage == 'choice-name':
            choice['name'] = 'Forged display name'
        else:
            choice['source']['session'] = 'other-project'
        chosen = store.save(chosen.id, chosen.session, document, expected_revision=chosen.revision)
    output = publish(store, chosen, worker, damage)
    if damage == 'runtime':
        document = output.document
        document['state']['scientificView']['runtime'] = source.document['state']['scientificView']['runtime']
        output = store.save(output.id, output.session, document, expected_revision=output.revision)
    calls = worker.calls
    found = scientific_provenance.resolve(store, output.id, output.session, output.revision)
    assert found.get('savedTool') is None
    assert worker.calls == calls
