"""Synthetic population transport, real public retention/edit/provenance and MT6 storage; no UI qualification."""
import asyncio
import copy

import numpy as np
import pytest

from orchestrator import commands, method_evidence, population, population_native, scientific_provenance
from orchestrator.scientific_edits import ResourceEdits
from orchestrator.scientific_history import ScientificHistory
from orchestrator.scientific_projects import InvalidProject, ProjectStore
from tests.test_method_evidence import selection
from tests.test_original_picture_analysis import prepare
from tests.test_particle_corrections import saved
from tests.test_sources import Client
from tests.test_scientific_tools import member, KEY, GEN  # noqa: F401


@pytest.fixture
def retained(tmp_path, monkeypatch, request):
    built, worker, aid, _ = prepare(tmp_path, monkeypatch)

    if getattr(request, "param", None) == "large-producer":
        original_call = worker.call

        async def large_record(op, **payload):
            result = await original_call(op, **payload)
            result["producer"] = {"polygons": [[[i, i + 1] for i in range(12000)]]}
            return result

        monkeypatch.setattr(worker, "call", large_record)

    async def retain():
        await commands.handle(built, Client(), {'type': 'source', 'open': aid})
        result = await population.segment(built.hub, choice='cellpose-sam')
        await saved(result)
        accepted = await population_native.run(built.hub, built.day, population_native.OPERATION,
            {'session': result.session, 'id': result.id, 'revision': result.revision,
             'request': 'population-method-lineage-001'}, caller='studio')
        return accepted['project'], copy.deepcopy(result.producer)

    owner, producer = asyncio.run(retain())
    return built.hub.resources.projects, owner, producer, worker


def corrected(store, owner):
    reference = owner.document['state']['scientificView']['inputs']['labels']
    history = ScientificHistory(store, owner, 'correction-kernel', tab='tab', placement='place')
    gesture = 'correction-gesture-1234567890123456'
    history.begin_action(gesture, {'op': 'labels', 'tool': 'paint', 'label': '0'}, {'source': owner.digest})
    history.finish_action(gesture, {'edit': {'state': 'applied', 'changed': True}})
    source = store.array_source(owner.id, owner.session, owner.revision, reference)
    patch = source.read(tuple(slice(0, n) for n in source.shape))
    patch[patch == 1] = 0
    transaction = ResourceEdits(store, owner.id, owner.session, owner.revision, reference, layer='labels')
    edit = transaction.begin(max_written_bytes=patch.nbytes, producer=history.producer())
    edit.write([0, 0], patch)
    return edit.commit(), history


def arms(original, correction):
    return [{'id': name, 'factor': 'boundary-definition', 'says': says, 'status': 'completed',
             'mask': selection(owner)} for name, says, owner in
            [('a', 'Original retained labels', original), ('b', 'Accepted manual correction', correction)]]


def test_retained_image_produces_original_and_corrected_labels_with_truthful_history(retained):
    store, original, producer, worker = retained
    correction, history = corrected(store, original)
    before = original.document
    kept = method_evidence.create(store, original.session, selection(original, 'array.image'),
                                  arms(original, correction), actor='studio')
    reopened = ProjectStore(store.root)
    evidence = method_evidence.read(reopened, kept.id, kept.session, kept.revision)
    assert evidence['source']['revisionDigest'] == original.digest
    assert evidence['arms'][0]['histories'] == [] and evidence['arms'][0]['timing']['seconds'] is None
    assert evidence['arms'][1]['histories'] == [history.producer()['history']]
    assert all(arm['limitations'] for arm in evidence['arms'])
    assert evidence['evidenceComplete'] is False and evidence['qualification'] == 'not-assessed'
    refs = original.document['state']['scientificView']['inputs']
    observed = scientific_provenance.resolve(reopened, original.id, original.session, original.revision,
                                             resource=refs['labels'])
    assert observed['problems'] == []
    association = observed['population']
    assert association['input'] == refs['image'] and association['output'] == refs['labels']
    assert association['producer']['record'] == producer and association['producer']['verified'] is False
    assert association['base']['digest'] == original.digest
    assert all(arm['producingAssociations'] == [association] for arm in evidence['arms'])
    assert reopened.read(original.id, original.session, revision=original.revision).document == before
    assert len(worker.inputs) == 1
    original_values = reopened.array_source(original.id, original.session, 1, refs['labels']).read((slice(0, 96), slice(0, 96)))
    assert set(np.unique(original_values)) == {0, 1, 2, 3}


def imported_record(retained, tmp_path, change):
    """Real low-level archive storage with explicitly corrupted/untrusted provenance metadata."""
    import shutil
    store, original, _, _ = retained
    destination = ProjectStore(tmp_path / 'untrusted-archive')
    folder = destination.root / original.id
    folder.mkdir(parents=True)
    for component in ('arrays', 'files'):
        shutil.copytree(store.root / original.id / component, folder / component)
    document = original.document
    change(document['state']['populationSource'])
    return destination, destination.create(original.session, document, identity=original.id)


@pytest.mark.parametrize('damage', ['input', 'output', 'producer', 'source', 'owner', 'file', 'missing', 'version'])
def test_explicit_corrupt_population_association_refuses_without_publishing(retained, tmp_path, damage):
    def corrupt(provenance):
        relation = provenance['association']
        if damage in ('input', 'output'):
            relation[damage] = provenance['nativeInputs']['labels' if damage == 'input' else 'image']
        elif damage == 'producer':
            relation['producer']['implementation'] = 'unrelated-producer'
        elif damage == 'source':
            relation['source']['revision'] += 1
        elif damage == 'owner':
            relation['input']['session'] = 'different-Project'
        elif damage == 'file':
            provenance['source']['files']['source.image'] = '0' * 64
            relation['source']['files']['source.image'] = '0' * 64
        elif damage == 'missing':
            del provenance['association']
        else:
            provenance['format'] = 1  # An explicit edge cannot be downgraded to an unknown legacy record.
    store, original = imported_record(retained, tmp_path, corrupt)
    before = [(row.id, row.digest) for row in store.list(original.session)]
    with pytest.raises(InvalidProject, match='association'):
        method_evidence.create(store, original.session, selection(original, 'array.image'),
                               arms(original, original), actor='studio')
    assert [(row.id, row.digest) for row in store.list(original.session)] == before


def test_legacy_population_record_is_not_backfilled_or_admitted_as_shared_image_lineage(retained, tmp_path):
    def legacy(provenance):
        provenance['format'] = 1
        del provenance['association']
    store, original = imported_record(retained, tmp_path, legacy)
    before = original.document
    provenance = scientific_provenance.resolve(store, original.id, original.session, 1)
    assert not provenance['problems'] and 'population' not in provenance
    with pytest.raises(InvalidProject, match='different frozen input component'):
        method_evidence.create(store, original.session, selection(original, 'array.image'),
                               arms(original, original), actor='studio')
    assert store.read(original.id, original.session, revision=1).document == before


@pytest.mark.parametrize('damage', ['session', 'digest', 'revision'])
def test_exact_source_selection_cannot_retarget_population_lineage(retained, damage):
    store, original, _, _ = retained
    selected = selection(original, 'array.image')
    selected[damage] = {'session': 'different-Project', 'digest': '0' * 64, 'revision': 99}[damage]
    before = [(row.id, row.digest) for row in store.list(original.session)]
    with pytest.raises(ValueError):
        method_evidence.create(store, original.session, selected, arms(original, original), actor='studio')
    assert [(row.id, row.digest) for row in store.list(original.session)] == before


def test_equal_pixels_and_coordinate_grid_do_not_make_an_unrelated_component_the_input(retained):
    from tests.test_scientific_arrays import fill
    store, original, _, _ = retained
    image_ref = original.document['state']['scientificView']['inputs']['image']
    image = store.array_source(original.id, original.session, 1, image_ref)
    writer = store.array_draft(original.id, original.session, component='array.unrelated', shape=image.shape,
        dtype=image.dtype, axes=image.axes, chunks=image.chunks,
        scientific={key: image.descriptor[key] for key in ('semantic', 'calibration')},
        max_written_bytes=np.prod(image.shape).item())
    other = fill(writer, image.read(tuple(slice(0, n) for n in image.shape)))
    document = original.document
    document['resources'].append(other)
    newer = store.save(original.id, original.session, document, expected_revision=1)
    with pytest.raises(InvalidProject, match='different frozen input component'):
        method_evidence.create(store, original.session, selection(newer, 'array.unrelated'),
                               arms(newer, newer), actor='studio')


def test_association_cannot_be_added_or_changed_through_ordinary_document_save(retained):
    _, original, _, _ = retained
    with pytest.raises(InvalidProject, match='document save'):
        scientific_provenance.guard(None, original.document)
    tampered = original.document
    tampered['state']['populationSource']['association']['producer']['implementation'] = 'forged'
    with pytest.raises(InvalidProject, match='document save'):
        scientific_provenance.guard(original, tampered)


def test_association_and_manual_history_survive_choice_export_and_reimport(retained, tmp_path):
    from orchestrator import scientific_exchange
    store, original, _, _ = retained
    correction, _ = corrected(store, original)
    kept = method_evidence.create(store, original.session, selection(original, 'array.image'),
                                  arms(original, correction), actor='studio')
    chosen = method_evidence.choose(store, kept.id, kept.session, kept.revision, 'b',
                                    'Exclude the first object for this question', actor='studio')
    expected = method_evidence.read(store, chosen.id, chosen.session, chosen.revision)
    archive = tmp_path / 'method-evidence.zip'
    scientific_exchange.export(store, chosen.id, chosen.session, chosen.revision, destination=archive)
    imported = ProjectStore(tmp_path / 'imported')
    scientific_exchange.reimport(imported, archive)
    assert method_evidence.read(imported, chosen.id, chosen.session, chosen.revision) == expected
    for accepted in (original, correction):
        assert imported.read(accepted.id, accepted.session, revision=accepted.revision).digest == accepted.digest


def test_public_tool_selection_before_correction_keeps_explicit_original_image_lineage(retained, member):
    from orchestrator import scientific_tools
    store, original, _, worker = retained
    chosen = scientific_tools.bind(store, original.id, original.session, original.revision, KEY, GEN)
    assert chosen.revision == 2 and chosen.document['kind'] == 'scene'
    correction, history = corrected(store, chosen)
    assert correction.revision == 3
    kept = method_evidence.create(store, original.session, selection(original, 'array.image'),
                                  arms(original, correction), actor='studio')
    evidence = method_evidence.read(store, kept.id, kept.session, kept.revision)
    assert [row['revision'] for row in evidence['arms'][1]['lineage']] == [3, 2, 1]
    assert evidence['arms'][1]['histories'] == [history.producer()['history']]
    assert evidence['arms'][1]['producingAssociations'][0]['base']['digest'] == original.digest
    assert len(worker.inputs) == 1
    # A different captured input revision is not silently rebased to the original.
    with pytest.raises(InvalidProject, match='different frozen input'):
        method_evidence.create(store, chosen.session, selection(chosen, 'array.image'),
                               arms(original, correction), actor='studio')


@pytest.mark.parametrize('retained', ['large-producer'], indirect=True)
def test_large_retained_producer_is_referenced_exactly_and_exported_without_truncation(retained, tmp_path):
    import hashlib
    from orchestrator import scientific_exchange
    from orchestrator.scientific_projects import _json
    store, original, producer, worker = retained
    body = _json(producer)
    assert len(body) > scientific_provenance.MAX_PRODUCER
    correction, _ = corrected(store, original)
    kept = method_evidence.create(store, original.session, selection(original, 'array.image'),
                                  arms(original, correction), actor='studio')
    evidence = method_evidence.read(store, kept.id, kept.session, kept.revision)
    expected = {'document': {'id': original.id, 'session': original.session, 'revision': 1,
                             'digest': original.digest},
                'path': ['state', 'populationSource', 'producer'],
                'encoding': 'canonical-json', 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)}
    for arm in evidence['arms']:
        attribution = arm['producingAssociations'][0]['producer']
        assert attribution['recordReference'] == expected
        assert 'record' not in attribution and attribution['verified'] is False
    assert len(_json(evidence)) < method_evidence.MAX_METADATA
    archive = tmp_path / 'large-producer.zip'
    scientific_exchange.export(store, kept.id, kept.session, kept.revision, destination=archive)
    imported = ProjectStore(tmp_path / 'large-producer-import')
    scientific_exchange.reimport(imported, archive)
    assert method_evidence.read(imported, kept.id, kept.session, kept.revision) == evidence
    restored = imported.read(original.id, original.session, revision=1)
    assert restored.digest == original.digest
    assert restored.document['state']['populationSource']['producer'] == producer
    assert len(worker.inputs) == 1
    # Callback snapshots retain their independent admission limit.
    with pytest.raises(InvalidProject, match='exceeds its allowance'):
        scientific_provenance.frozen(producer)
