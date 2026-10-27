"""Tiny synthetic native pixels, actual CPU algorithm/shared admission and durable retention; no UI qualification."""
import asyncio
import copy

import cv2
import numpy as np
import pytest

from orchestrator import commands, method_evidence, native_picture_inputs as native, population_native, scientific_provenance
from orchestrator.scientific_families import write_array
from tests.standin_workers import build_app
from tests.test_particle_corrections import saved
from tests.test_sources import Client
from tests.test_method_evidence import selection


def input_at(built, dtype='uint8', shape=(32, 40), calibration=None):
    store = built.hub.resources.projects
    owner = store.create(built.day.sid, {'kind': 'scene', 'title': 'Synthetic native input', 'state': {}, 'resources': [], 'runtimes': []})
    pixels = np.zeros(shape, dtype=dtype); pixels[8:24, 8:24] = 200
    calibration = calibration or {'space': {'session': owner.session, 'id': owner.id, 'revision': 2},
                   'axes': ['y', 'x'], 'units': ['µm', 'µm'], 'affine': [[2, 0, 7], [0, 3, 11]]}
    ref = write_array(store, owner, 'array.image', pixels, list('zyx'[-len(shape):]),
                      {'semantic': 'intensity', **({'calibration': calibration} if len(shape) == 2 else {})})
    doc = owner.document; doc['resources'] = [ref]; doc['state']['scientificView'] = {'inputs': {'image': ref}}
    owner = store.save(owner.id, owner.session, doc, expected_revision=1)
    return owner, pixels, {'kind': 'array', 'session': owner.session, 'id': owner.id, 'revision': 2, 'component': 'array.image'}


def test_saved_native_input_runs_shared_cpu_action_and_retains_comparable_exact_lineage(tmp_path):
    built = build_app(tmp_path/'home')
    owner, pixels, reference = input_at(built)
    before = copy.deepcopy(owner.document)
    assert native.choices(built.hub.resources, owner.session)[0]['reference'] == reference
    async def run():
        client = Client()
        from orchestrator import workspace_views
        workspace_views.save(built.day, workspace_views.read(built.day, schema=4)['layout'], 0)
        origin = {'session': owner.session, 'tab': 'main', 'placementId': 'pending-native',
                  'run': 'native-CPU-run', 'input': {'resource': reference}}
        await commands.handle(built, client, {'type': 'action', 'capability': 'catalog:basic-bright-objects',
                                             'args': {'sigma': .5, 'min_area': 3}, 'resource': reference,
                                             'run': origin['run'], 'workspaceOrigin': origin})
        assert client.replies[-1]['ok'], client.replies[-1]
        from orchestrator.evidence import evidence_of
        population = next(iter(evidence_of(built.hub).items.values()))
        await saved(population)
        assert population.producer['settings']['gaussianSigma'] == .5
        kept = await population_native.run(built.hub, built.day, population_native.OPERATION,
            {'session': owner.session, 'id': population.id, 'revision': population.revision,
             'request': 'native-picture-acceptance-001'}, caller='studio')
        return kept['project']
    result = asyncio.run(run())
    store = built.hub.resources.projects
    refs = result.document['state']['scientificView']['inputs']
    original = store.array_source(result.id, result.session, result.revision, refs['image'])
    assert np.array_equal(original.read((slice(0,32),slice(0,40))), pixels)
    assert original.descriptor['calibration'] == store.array_source(owner.id, owner.session, 2, owner.document['resources'][0]).descriptor['calibration']
    trace = scientific_provenance.resolve(store, result.id, result.session, 1, resource=refs['labels'])
    assert trace['problems'] == []
    assert trace['population']['base'] == {'id': owner.id, 'session': owner.session, 'revision': 2, 'digest': owner.digest}
    arms = [{'id': arm, 'factor': 'algorithm', 'says': 'Same CPU result reused for synthetic lineage check',
             'status': 'completed', 'mask': selection(result, 'array.labels')} for arm in ('a','b')]
    comparison = method_evidence.create(store, owner.session, selection(owner, 'array.image'), arms, actor='studio')
    assert comparison.revision == 1
    from orchestrator import scientific_exchange
    from orchestrator.scientific_projects import ProjectStore
    archive = tmp_path/'native-result.zip'
    scientific_exchange.export(store, result.id, result.session, 1, destination=archive)
    reopened = ProjectStore(tmp_path/'reopened')
    scientific_exchange.reimport(reopened, archive)
    assert reopened.read(owner.id, owner.session, revision=2).digest == owner.digest
    assert scientific_provenance.resolve(reopened, result.id, result.session, 1, resource=refs['labels']) == trace
    assert store.read(owner.id, owner.session, revision=2).document == before
    other, _, _ = input_at(built, calibration=original.descriptor['calibration'])
    with pytest.raises(ValueError, match='frozen input'):
        method_evidence.create(store, owner.session, selection(other, 'array.image'), arms, actor='studio')


@pytest.mark.parametrize('change', ['foreign', 'missing-revision', 'dtype', 'volume'])
def test_native_input_refusals_happen_before_pixels_or_execution(tmp_path, change):
    built = build_app(tmp_path/'home')
    owner, _, reference = input_at(built, dtype='uint16' if change == 'dtype' else 'uint8',
                                    shape=(2, 8, 9) if change == 'volume' else (32, 40))
    if change == 'foreign': reference['session'] = '2026-01-01.1'
    if change == 'missing-revision': reference['revision'] = 999
    with pytest.raises(ValueError):
        native.resolve(built.hub, reference, owner.session)


def test_stopped_native_admission_never_reads_or_holds_pixels(tmp_path, monkeypatch):
    import threading
    built = build_app(tmp_path/'home'); owner, _, reference = input_at(built)
    stop = threading.Event(); stop.set()
    with pytest.raises(ValueError, match='stopped'):
        native.resolve(built.hub, reference, owner.session, cancelled=stop)
