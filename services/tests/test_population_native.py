"""Generic retained populations into real native Project storage; synthetic model transport."""
import asyncio
import copy

import numpy as np
import pytest

from orchestrator import commands, population, population_native, scientific_measurement
from orchestrator.scientific_projects import ProjectStore
from tests.test_original_picture_analysis import prepare
from tests.test_particle_corrections import saved
from tests.test_sources import Client
from tests.test_fiji_particle_journey import journey  # noqa: F401


def test_corrected_non_fiji_population_is_native_input_without_another_producer_run(tmp_path, monkeypatch):
    built, worker, aid, image = prepare(tmp_path, monkeypatch)

    async def go():
        await commands.handle(built, Client(), {'type': 'source', 'open': aid})
        result = await population.segment(built.hub, choice='cellpose-sam')
        await saved(result)
        original = copy.deepcopy(result.original)
        rectangle = next(m.id for m in result.live() if m.box[0] == 70)
        population.correct(built.hub, result, 'remove', [rectangle], 'user')
        await saved(result)
        selected = {'session': result.session, 'id': result.id, 'revision': 1,
                    'request': 'population-native-request-001'}
        kept = await population_native.run(built.hub, built.day, population_native.OPERATION,
                                           selected, caller='studio')
        owner = kept['project']
        store = ProjectStore(built.hub.resources.projects.root)
        reopened = store.read(owner.id, result.session, revision=owner.revision)
        inputs = reopened.document['state']['scientificView']['inputs']
        labels = store.array_source(owner.id, result.session, owner.revision, inputs['labels'])
        data = labels.read((slice(0, 96), slice(0, 96)))
        assert labels.dtype == '<u2' and labels.axes == ('y', 'x')
        assert set(np.unique(data)) == {0, 2, 3}
        assert np.count_nonzero(data == 2) == 317 and np.count_nonzero(data == 3) == 149
        assert data[25, 25] == 2 and data[60, 65] == 3 and data[16, 78] == 0
        provenance = reopened.document['state']['populationSource']
        assert provenance['source']['revision'] == 1 and provenance['source']['id'] == result.id
        assert {m['memberID'] for m in provenance['members']} == {m.id for m in result.live()}
        assert provenance['producer'] == result.producer
        assert result.original == original
        assert store.file_source(owner.id, result.session, owner.revision,
                                 reopened.document['state']['populationSource']['original']).read(0, image.stat().st_size) == image.read_bytes()
        measured = await scientific_measurement.run(built.hub, built.day, scientific_measurement.OPERATION,
            {'project': owner.id, 'session': result.session, 'revision': owner.revision, 'input': 'labels'}, caller='studio')
        from orchestrator.scientific_resource_actions import table
        rows = table(built.hub.resources.projects, owner.id, result.session, measured['project'].revision)['rows']
        assert rows == [['2', 317, 25., 25.], ['3', 149, 60., 65.]]
        assert len(worker.inputs) == 1

    asyncio.run(go())


def test_exact_fiji_generation_is_retained_through_real_space_removal_guard(journey):
    from orchestrator import capability_space, fiji_generations, scientific_retention
    from tests.test_particle_corrections import analyzed
    built, aid, _ = journey

    async def go():
        result = await analyzed(built, aid)
        generation = result.producer['runtime']['generation']
        assert fiji_generations.current() != generation  # stand-in changes active generation after admission
        kept = await population_native.run(built.hub, built.day, population_native.OPERATION,
            {'session': result.session, 'id': result.id, 'revision': 0, 'request': 'fiji-original-generation'}, caller='studio')
        assert kept['project'].document['runtimes'] == [
            {'bundle': 'fiji', 'generation': str(generation), 'operation': 'fiji-analyze-particles'}]
        item = next(row for row in capability_space._roots() if row['kind'] == 'fiji' and not row['pending'])
        assert fiji_generations.root()/str(generation) in item['paths']
        with pytest.raises(scientific_retention.Retained):
            with capability_space._removal_boundary(item):
                pytest.fail('The actual installed-generation removal boundary must refuse')
    asyncio.run(go())


def test_a_correction_at_publication_refuses_without_a_visible_empty_project(tmp_path, monkeypatch):
    built, worker, aid, _ = prepare(tmp_path, monkeypatch)

    async def go():
        await commands.handle(built, Client(), {'type': 'source', 'open': aid})
        result = await population.segment(built.hub, choice='cellpose-sam')
        await saved(result)
        before = built.hub.resources.projects.list(result.session)
        publish = population_native.PopulationIntake.publish
        def changed(owner, stopping):
            population.correct(built.hub, result, 'remove', [result.live()[0].id], 'user')
            return publish(owner, stopping)
        monkeypatch.setattr(population_native.PopulationIntake, 'publish', changed)
        with pytest.raises(ValueError, match='changed'):
            await population_native.run(built.hub, built.day, population_native.OPERATION,
                {'session': result.session, 'id': result.id, 'revision': 0, 'request': 'late-correction-001'}, caller='studio')
        assert built.hub.resources.projects.list(result.session) == before
        assert len(worker.inputs) == 1
    asyncio.run(go())


def test_same_request_recovers_accepted_revision_without_recapture_after_correction(tmp_path, monkeypatch):
    built, worker, aid, _ = prepare(tmp_path, monkeypatch)

    async def go():
        await commands.handle(built, Client(), {'type': 'source', 'open': aid})
        result = await population.segment(built.hub, choice='cellpose-sam')
        await saved(result)
        selected = {'session': result.session, 'id': result.id, 'revision': 0, 'request': 'retry-native-labels-001'}
        first = await population_native.run(built.hub, built.day, population_native.OPERATION, selected, caller='studio')
        population.correct(built.hub, result, 'remove', [result.live()[0].id], 'user')
        await saved(result)
        monkeypatch.setattr(population_native.particle_exchange, 'snapshot',
                            lambda *a, **kw: pytest.fail('A retry must not recapture or reinterpret source pixels'))
        second = await population_native.run(built.hub, built.day, population_native.OPERATION, selected, caller='agent:chat')
        assert second['project'] == first['project'] and second['reused']
        assert second['project'].document['state']['populationSource']['caller'] == 'studio'
        assert second['invocation']['caller'] == 'agent:chat'
        with pytest.raises(ValueError, match='request|revision'):
            await population_native.run(built.hub, built.day, population_native.OPERATION,
                                         {**selected, 'revision': 1}, caller='studio')
        assert len(worker.inputs) == 1
    asyncio.run(go())


def test_revision_boundary_serializes_correction_before_native_acceptance(tmp_path, monkeypatch):
    import threading
    from tests.waiting import HANG_S
    built, _, aid, _ = prepare(tmp_path, monkeypatch)

    async def go():
        await commands.handle(built, Client(), {'type': 'source', 'open': aid})
        result = await population.segment(built.hub, choice='cellpose-sam')
        await saved(result)
        evidence = population.evidence_of(built.hub)
        inside, release = threading.Event(), threading.Event()
        errors = []
        mutate = population._correct
        def held(*args, **kwargs):
            inside.set()
            assert release.wait(HANG_S)
            return mutate(*args, **kwargs)
        monkeypatch.setattr(population, '_correct', held)
        def correct():
            try: population.correct(built.hub, result, 'remove', [result.live()[0].id], 'user')
            except Exception as exc: errors.append(exc)
        thread = threading.Thread(target=correct)
        thread.start()
        try:
            assert inside.wait(HANG_S)
            # The same owner lock protects mutation, not merely its revision increment.
            entered = evidence._lock.acquire(blocking=False)
            if entered: evidence._lock.release()
            assert not entered
        finally:
            release.set()
            thread.join(HANG_S)
        assert not thread.is_alive() and not errors and result.revision == 1
        with pytest.raises(ValueError, match='changed'):
            with evidence.exact_revision(result, 0):
                pytest.fail('A stale snapshot must never publish')
    asyncio.run(go())


def test_mask_archive_shape_is_bounded_before_numpy_allocation(tmp_path, monkeypatch):
    import io
    import zipfile
    from orchestrator import particle_exchange
    # Synthetic archive header claims a payload much larger than its actual body.
    buffer = io.BytesIO()
    np.lib.format.write_array_header_1_0(buffer, {'descr': '|u1', 'fortran_order': False, 'shape': (2**40,)})
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as out:
        meta = io.BytesIO()
        np.save(meta, np.array([96, 96, 0, 0, 1], dtype='float64'))
        out.writestr('c1.meta.npy', meta.getvalue())
        out.writestr('c1.bits.npy', buffer.getvalue())
    monkeypatch.setattr(np, 'load', lambda *a, **kw: pytest.fail('Unbounded array reached NumPy allocation'))
    with pytest.raises(ValueError, match='mask|NPY|payload|allowance'):
        particle_exchange.mask_arrays(archive.getvalue(), ['c1'], 96*96)


@pytest.mark.parametrize('first', ['studio', 'agent'])
def test_ui_and_authorized_registry_share_selection_and_one_native_publication(tmp_path, monkeypatch, first):
    import httpx
    from fastapi import FastAPI
    from types import SimpleNamespace
    from orchestrator.brain.registry import Registry, ToolContext
    from orchestrator.brain.toolbox.scientific import add_scientific_tools
    from orchestrator.scientific_resource_api import register
    built, worker, aid, _ = prepare(tmp_path, monkeypatch)
    app = FastAPI()
    register(app, built, lambda origin, port: origin == 'http://127.0.0.1:8770', 'X-MicroscopeMind')
    registry = Registry()
    add_scientific_tools(registry, SimpleNamespace(hub=built.hub, day=built.day))

    async def go():
        await commands.handle(built, Client(), {'type': 'source', 'open': aid})
        result = await population.segment(built.hub, choice='cellpose-sam')
        await saved(result)
        selection = {'id': result.id, 'session': result.session, 'revision': 0, 'request': 'caller-equivalence-001'}
        async def agent(action='run'):
            call = registry.start('retain_population_labels', {'action': action, 'operation': population_native.OPERATION, **selection},
                                  ToolContext('chat', 1, None, asked=built.day.here()))
            await call.task
            assert call.ok, (call.summary, call.error)
            return call.values
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:8770') as client:
            async def studio():
                response = await client.post('/api/populations/retain-labels',
                    headers={'Origin': 'http://127.0.0.1:8770', 'X-MicroscopeMind': 'retain-population-labels'},
                    json={'operation': population_native.OPERATION, **selection})
                assert response.status_code == 200, response.text
                return response.json()
            preview = await agent('describe')
            listing = (await client.get(f'/api/populations/{result.id}/native-labels',
                params={'session': result.session, 'revision': 0})).json()
            assert listing == preview
            first_result = await (studio() if first == 'studio' else agent())
            second_result = await (agent() if first == 'studio' else studio())
        assert not first_result['reused'] and second_result['reused']
        assert first_result['project']['id'] == second_result['project']['id']
        assert first_result['operation'] == second_result['operation'] == population_native.descriptor()
        assert len(worker.inputs) == 1
    asyncio.run(go())


def test_concurrent_same_request_returns_one_acceptance_and_original_admission(tmp_path, monkeypatch):
    import threading
    built, worker, aid, _ = prepare(tmp_path, monkeypatch)
    barrier = threading.Barrier(2)
    original = population_native.PopulationIntake.publish
    def simultaneous(owner, stopping):
        barrier.wait(5)
        return original(owner, stopping)
    monkeypatch.setattr(population_native.PopulationIntake, 'publish', simultaneous)
    async def go():
        await commands.handle(built, Client(), {'type': 'source', 'open': aid})
        source = await population.segment(built.hub, choice='cellpose-sam')
        await saved(source)
        selection = {'id': source.id, 'session': source.session, 'revision': 0, 'request': 'concurrent-retention-001'}
        before = built.hub.resources.projects.list(source.session)
        results = await asyncio.gather(*(population_native.run(built.hub, built.day, population_native.OPERATION,
                    selection, caller=caller) for caller in ('studio', 'agent:chat')))
        assert results[0]['project'] == results[1]['project']
        assert sorted(r['reused'] for r in results) == [False, True]
        accepted = next(r for r in results if not r['reused'])
        assert accepted['project'].document['state']['populationSource']['admission'] == accepted['invocation']
        assert len(built.hub.resources.projects.list(source.session)) == len(before) + 1
        assert len(worker.inputs) == 1
    asyncio.run(go())


@pytest.mark.parametrize('change', ['cancel', 'session', 'stale', 'physical-scale', 'nonpositive-id', 'memory'])
def test_refused_selection_does_not_read_pixels_or_publish(tmp_path, monkeypatch, change):
    import threading
    built, worker, aid, _ = prepare(tmp_path, monkeypatch)
    async def go():
        await commands.handle(built, Client(), {'type': 'source', 'open': aid})
        result = await population.segment(built.hub, choice='cellpose-sam')
        await saved(result)
        selection = {'id': result.id, 'session': result.session, 'revision': 0, 'request': 'refused-retention-001'}
        before = built.hub.resources.projects.list(result.session)
        stop = threading.Event()
        if change == 'cancel': stop.set()
        if change == 'session': selection['session'] = 'other'
        if change == 'stale': selection['revision'] = 9
        if change == 'physical-scale': result.scale_id = {'umPerPx': 0.25, 'measured': True}
        if change == 'nonpositive-id': result.live()[0].instance = 0
        if change == 'memory':
            from orchestrator.capture import captures, Refused
            def deny(*a, **kw): raise Refused('No room for native retention')
            monkeypatch.setattr(captures(built.hub).memory, 'reserve', deny)
        monkeypatch.setattr(population_native.particle_exchange, 'snapshot', lambda *a, **kw: pytest.fail('Unadmitted pixels'))
        from orchestrator.capture import Refused
        with pytest.raises((ValueError, Refused)):
            await population_native.run(built.hub, built.day, population_native.OPERATION, selection, caller='studio', stopping=stop)
        assert built.hub.resources.projects.list(result.session) == before and len(worker.inputs) == 1
    asyncio.run(go())


@pytest.mark.parametrize('change', ['receipt', 'missing'])
def test_changed_runtime_receipt_cannot_be_accepted_as_the_original_generation(journey, change):
    from orchestrator import fiji_generations
    from tests.test_particle_corrections import analyzed
    built, aid, _ = journey
    async def go():
        result = await analyzed(built, aid)
        path = fiji_generations.root()/str(result.producer['runtime']['generation'])/'receipt.json'
        if change == 'receipt': path.write_text('{"different":"receipt"}')
        else: path.parent.rename(path.parent.with_name('removed-private-generation'))
        before = built.hub.resources.projects.list(result.session)
        with pytest.raises(ValueError, match='receipt changed|no longer available'):
            await population_native.run(built.hub, built.day, population_native.OPERATION,
                {'id': result.id, 'session': result.session, 'revision': 0, 'request': 'wrong-runtime-receipt'}, caller='studio')
        assert built.hub.resources.projects.list(result.session) == before
    asyncio.run(go())


def test_document_save_cannot_forge_or_remove_population_provenance():
    from types import SimpleNamespace
    from orchestrator.scientific_provenance import guard
    original = {'state': {'populationSource': {'source': {'id': 'e2', 'revision': 3}}}}
    previous = SimpleNamespace(document=original)
    guard(previous, copy.deepcopy(original))
    for before, document in [(None, original), (previous, {'state': {}}),
                              (previous, {'state': {'populationSource': {'source': {'id': 'e2', 'revision': 4}}}})]:
        with pytest.raises(ValueError, match='provenance'):
            guard(before, document)


def test_ticket_cancelled_at_final_boundary_leaves_no_visible_project(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from orchestrator.ticket import Generations, Ticket, using
    built, worker, aid, _ = prepare(tmp_path, monkeypatch)
    ticket = Ticket(Generations(), 'command')
    original = population_native.PopulationIntake.acceptance
    @contextmanager
    def cancelled(owner):
        with original(owner):
            ticket.cancel('user stopped retention')
            yield
    monkeypatch.setattr(population_native.PopulationIntake, 'acceptance', cancelled)
    async def go():
        await commands.handle(built, Client(), {'type': 'source', 'open': aid})
        result = await population.segment(built.hub, choice='cellpose-sam')
        await saved(result)
        before = built.hub.resources.projects.list(result.session)
        with using(ticket), pytest.raises(ValueError, match='stopped'):
            await population_native.run(built.hub, built.day, population_native.OPERATION,
                {'id': result.id, 'session': result.session, 'revision': 0, 'request': 'cancel-at-acceptance'}, caller='studio')
        assert built.hub.resources.projects.list(result.session) == before
        assert len(worker.inputs) == 1
    asyncio.run(go())
