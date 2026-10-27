"""Actual App/Registry/Results/crop retention, with synthetic pixels and renderer transport.

These local-only controls run no providers, camera, server or browser. They complement the
unchanged 31 workspace-action cases; they do not certify generic actions or browser drawing.
Only bounded storage failures/delays are injected, never a replacement producer or executor.
"""
import asyncio
import copy
import hashlib
import json
import threading
import weakref

import cv2
import numpy as np
import pytest

from orchestrator.app import App
from orchestrator.brain.registry import ToolContext
from orchestrator.capture import RUN, captures
from orchestrator.paths import Paths
from orchestrator.ticket import issue, using
from orchestrator.workspace_observation import ClientRef
from tests.test_retained_reopen_structural_renderer import AcceptedReopenStudio
from tests.test_workspace_actions_app import CLIENT, STUDIOS, WORKSPACE, comparison_fixture, plan, saved_picture, succeeded
from tests.waiting import HANG_S, until_async


async def built_async(tmp_path):
    """Actual accepted structural root, including at an async fresh-App restart.

    The retained-entry compatibility path needs its magnifier root on the same
    accepted socket before Send. This renderer decodes actual published panes;
    it supplies neither native source authority nor browser paint proof.
    """
    app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
    studio = AcceptedReopenStudio(app)
    app.accept_workspace(studio, ClientRef(CLIENT['workspaceId'], CLIENT['clientId'],
                                          CLIENT['pageEpoch'], CLIENT['connectionEpoch']))
    original = app.hub.broadcast

    def broadcast(message):
        original(message)
        studio.hear(message)

    app.hub.broadcast = app.hub.screen.broadcast = broadcast
    # The unchanged Runner helper locates its initiating synthetic socket and
    # passively joins that renderer's actual observation tasks.
    studio.observation_tasks = studio.tasks
    STUDIOS[id(app)] = weakref.ref(studio)
    await studio.send_report()
    observed = app.hub.screen.observation(studio).wire()
    assert len(observed['items']) == 1 and observed['items'][0]['open'] is False
    assert observed['items'][0]['rootId'] == studio.row['rootId']
    assert observed['sources'] == observed['commandResults'] == []
    return app, studio


def built(tmp_path):
    return asyncio.run(built_async(tmp_path))


async def until(predicate, message):
    await until_async(predicate, every=0.005, what=message)


def context(app):
    ctx = ToolContext('chat', 1, None, 'Synthetic comparison retention control', asked=app.day.here())
    ctx.client, ctx.workspace_id = dict(CLIENT), WORKSPACE
    ctx.when_asked = app.brain.board.when_asked()
    return ctx


def pixel_state(app):
    return copy.deepcopy(app.magnifier.wire()), dict(app.magnifier.pictures)


async def compare_saved_members(app, ids):
    ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
    try:
        return await plan(app, [('compare', {'a': ids[0], 'b': ids[1]})], ticket=ticket)
    finally:
        ticket.close()


async def retained_sibling(app):
    entry, pixels = saved_picture(app, value=99)
    studio = STUDIOS[id(app)]()
    await studio.send_report()  # actual accepted root, before the original request is pinned
    ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
    try:
        pin = app.hub.screen.pin(studio, request_id=ticket.admission.request_id,
                                 timing='server-receive-legacy')
        ctx = context(app)
        ctx.asked = ticket.asked
        ctx.client, ctx.workspace_id = studio, WORKSPACE
        ctx.admission, ctx.request_id = ticket.admission, ticket.admission.request_id
        ctx.request_snapshot = pin
        with using(ticket):
            run = app.brain.registry.start('present', {'what': entry.id, 'do': 'open'}, ctx)
            await run.task
            await asyncio.gather(*studio.tasks)  # external renderer errors cannot be swallowed
        assert run.occurrence.request_id == ticket.admission.request_id
        assert ctx.request_snapshot is pin and pin.observation.client.wire() == CLIENT
    finally:
        ticket.seal()
    succeeded(run)
    results = app.hub.resources.results
    record = results.get(entry.id, entry.day)
    path = results.root / entry.day / record['pictures'][0]
    await until(path.is_file, 'unrelated fixture picture did not finish saving')
    assert path.read_bytes() == pixels
    return entry, pixels, path


def test_comparison_restart_reads_exact_ordered_originals_and_frozen_crop_facts(tmp_path, monkeypatch):
    app, studio = built(tmp_path)
    population, ids, originals = comparison_fixture(app)
    capture = population.capture
    original_view = capture.jpeg
    # This synthetic producer declares its original run/bridge clock explicitly; the
    # simpler shared fixture intentionally leaves those Population.view facts unknown.
    population.view.update(run=RUN, boot=capture.boot, seq=capture.seq, at=capture.wall_at)

    async def go():
        runs, _ = await compare_saved_members(app, ids)
        succeeded(runs[0])
        resource = runs[0].resource
        entry = app.hub.resources.get(resource['id'], resource['day'])
        source = copy.deepcopy(entry.source)
        composite = app.hub.resources.picture(entry.id, 0, entry.day)[0]
        assert source['digest'] == hashlib.sha256(composite).hexdigest()
        assert len(source['components']) == 2
        app.hub.evidence.revise(population, 'synthetic later correction', 'user', [ids[0]])

        # A new actual App reads the journal/files, with no inherited Results cache.
        again, restored_studio = await built_async(tmp_path)
        assert again.hub.resources.results is not app.hub.resources.results

        def recrop_forbidden(*args, **kwargs):
            raise AssertionError('retained comparison originals must not be cropped again')
        monkeypatch.setattr('orchestrator.population.captured_picture', recrop_forbidden)
        back = again.hub.resources.get(entry.id, entry.day)
        assert back is not None and back.source == source
        assert back.picture_source(0) == source
        assert again.hub.resources.picture(back.id, 0, back.day)[0] == composite
        for n, (mid, original, child) in enumerate(zip(ids, originals, source['components']), 1):
            ref, facts = child['identity']['resource'], child['provenance']
            assert ref == {'day': entry.day, 'id': entry.id, 'version': None, 'picture': n}
            assert back.picture_source(n) == child
            assert child['availability'] == 'retained'
            assert child['digest'] == hashlib.sha256(original).hexdigest()
            assert (facts['population'], facts['member'], facts['revision']) == (population.id, mid, 0)
            assert (facts['frame'], facts['run'], facts['boot'], facts['seq']) == (
                population.capture_id, population.view.get('run'), population.view.get('boot'), population.view.get('seq'))
            x0, y0, x1, y1 = facts['rect']
            assert facts['cropAffine'] == [1, 0, 0, 1, -x0, -y0]
            assert facts['naturalSize'] == [x1 - x0, y1 - y0]
            assert facts['originalSize'] == [population.view['width'], population.view['height']]
            assert facts['scale'] == population.scale_id and facts['quality'] == 92 and facts['outlines'] is False
            assert len(facts['clipped']) == 4 and facts['padding']['pixels'] >= 40
            data, mime = again.hub.resources.picture(ref['id'], ref['picture'], ref['day'])
            assert mime == 'image/jpeg' and data == original
            decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            assert [decoded.shape[1], decoded.shape[0]] == facts['naturalSize']
            view = cv2.imdecode(np.frombuffer(original_view, np.uint8), cv2.IMREAD_COLOR)
            assert 0 <= x0 < x1 <= view.shape[1] and 0 <= y0 < y1 <= view.shape[0]
            reconstructed = cv2.imencode('.jpg', view[y0:y1, x0:x1],
                                         [cv2.IMWRITE_JPEG_QUALITY, facts['quality']])[1].tobytes()
            assert reconstructed == data, 'crop coordinates must explain the actual retained pixels'
        assert len(again.hub.resources.results.entries(entry.day)) == 1
        # Keep both synthetic transport owners alive through their actual app calls.
        assert studio.app is app and restored_studio.app is again
    asyncio.run(go())


@pytest.mark.parametrize('boundary', ['child-one', 'child-two', 'source-metadata', 'journal'])
def test_essential_retention_failure_precedes_visible_compare_and_preserves_sibling(tmp_path, monkeypatch, boundary):
    app, studio = built(tmp_path)
    _, ids, originals = comparison_fixture(app)

    async def go():
        sibling, pixels, sibling_path = await retained_sibling(app)
        before = pixel_state(app)
        memory = captures(app.hub).memory
        unrelated = memory.reserve('unsaved', 4096, 'synthetic unrelated owner')
        baseline = memory.reserved['unsaved']
        results = app.hub.resources.results
        write, append = results._write, app.day.journal.append
        injected = []

        def failed_write(day, name, data, **kwargs):
            selected = (boundary == 'child-one' and name.endswith('-1.jpg') or
                        boundary == 'child-two' and name.endswith('-2.jpg'))
            if boundary == 'source-metadata' and name.endswith('.json'):
                selected = bool((json.loads(data).get('source') or {}).get('components'))
            if selected and kwargs.get('durable'):
                injected.append((day, name))
                return False  # the real writer's documented I/O-failure return
            return write(day, name, data, **kwargs)

        def failed_append(event):
            if boundary == 'journal' and (event.get('source') or {}).get('components'):
                injected.append((event['day'], 'journal'))
                raise OSError('synthetic required journal write failure')
            return append(event)

        monkeypatch.setattr(results, '_write', failed_write)
        monkeypatch.setattr(app.day.journal, 'append', failed_append)
        try:
            runs, _ = await compare_saved_members(app, ids)
            run = runs[0]
            assert injected and run.ok is False and run.resource is None
            assert 'sav' in run.summary.lower() and 'Created a new comparison artifact' not in run.summary
            assert pixel_state(app) == before
            assert app.hub.resources.picture(sibling.id, 0, sibling.day)[0] == pixels
            assert sibling_path.read_bytes() == pixels
            assert [entry['id'] for entry in results.entries(sibling.day)] == [sibling.id]
            assert memory.reserved['unsaved'] == baseline and not unrelated.released
            for mid, original in zip(ids, originals):
                assert app.hub.resources.picture(mid, 0, sibling.day)[0] == original
            assert sorted(path.name for path in sibling_path.parent.glob('d*-*.jpg')) == [sibling_path.name]
            assert studio.app is app
        finally:
            unrelated.release()
    asyncio.run(go())


@pytest.mark.parametrize('boundary', ['ticket-cancel', 'task-cancel', 'new-session'])
def test_stop_during_save_discards_only_its_own_staging_and_releases_after_worker(tmp_path, monkeypatch, boundary):
    app, studio = built(tmp_path)
    _, ids, _ = comparison_fixture(app)

    async def go():
        sibling, pixels, sibling_path = await retained_sibling(app)
        before = pixel_state(app)
        results, memory = app.hub.resources.results, captures(app.hub).memory
        unrelated = memory.reserve('unsaved', 4096, 'synthetic sibling owner')
        baseline = memory.reserved['unsaved']
        entered, release = threading.Event(), threading.Event()
        staged = {}
        write = results._write

        def delayed_write(day, name, data, **kwargs):
            if name.endswith('.json'):
                metadata = json.loads(data)
                if (metadata.get('source') or {}).get('components'):
                    staged.update(day=day, id=metadata['id'])
                    entered.set()
                    assert release.wait(HANG_S), 'test failed to release its bounded storage delay'
            return write(day, name, data, **kwargs)

        monkeypatch.setattr(results, '_write', delayed_write)
        ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
        run = None
        try:
            with using(ticket):
                run = app.brain.registry.start('compare', {'a': ids[0], 'b': ids[1]}, context(app))
                await until(entered.is_set, 'actual Results source-metadata save was not reached')
                assert not run.task.done() and pixel_state(app) == before
                assert memory.reserved['unsaved'] > baseline
                assert staged['id'] != sibling.id
                if boundary == 'task-cancel':
                    run.task.cancel()
                    await asyncio.gather(run.task, return_exceptions=True)
                    assert run.task.cancelled()
                elif boundary == 'ticket-cancel':
                    ticket.cancel('synthetic save-time cancellation')
                else:
                    generation = app.day.generation
                    app.new_session()  # another session (ADR 0017)
                    assert app.day.generation != generation
                # A canceled await cannot release the pixels a blocked worker still uses.
                assert memory.reserved['unsaved'] > baseline and not unrelated.released
                release.set()
                await asyncio.gather(run.task, return_exceptions=True)
                if boundary != 'task-cancel':
                    assert run.ok is False and ('called off' in run.summary or 'another session' in run.summary)
                await until(lambda: memory.reserved['unsaved'] == baseline,
                            'stopped comparison retained its own memory reservation')
                own_files = lambda: list((results.root / staged['day']).glob(f"{staged['id']}-*.jpg"))
                await until(lambda: not own_files(), 'stopped comparison retained its own pixel partials')
                assert run.resource is None and not unrelated.released
                assert sibling_path.read_bytes() == pixels
                assert all(entry['id'] != staged['id'] for entry in results.entries(staged['day']))
                if boundary != 'new-session':
                    assert pixel_state(app) == before
                    assert app.hub.resources.picture(sibling.id, 0, sibling.day)[0] == pixels
                # A fresh store cannot resurrect a stopped preparation from its journal.
                again, restored_studio = await built_async(tmp_path)
                assert again.hub.resources.get(staged['id'], staged['day']) is None
                assert sibling_path.read_bytes() == pixels
                assert studio.app is app and restored_studio.app is again
        finally:
            release.set()
            if run is not None and not run.task.done():
                await asyncio.gather(run.task, return_exceptions=True)
            ticket.close()
            unrelated.release()
    asyncio.run(go())


def test_repeated_finished_consumption_keeps_one_artifact_and_original_step_output(tmp_path):
    app, studio = built(tmp_path)
    _, ids, originals = comparison_fixture(app)

    async def go():
        runs, ctx = await compare_saved_members(app, ids)
        run = runs[0]
        succeeded(run)
        results, resource = app.hub.resources.results, dict(run.resource)
        source = copy.deepcopy(app.hub.resources.get(resource['id'], resource['day']).source)
        produced = list(ctx.steps.produced[1])
        assert produced.count(resource['id']) == 1
        for _ in range(2):
            entry = results.from_run(run, ctx, app.hub)
            assert (entry['day'], entry['id'], entry['source']) == (resource['day'], resource['id'], source)
            assert run.resource == resource and ctx.steps.produced[1] == produced
            assert len(results.entries(resource['day'])) == 1
            for n, pixels in enumerate(originals, 1):
                assert app.hub.resources.picture(resource['id'], n, resource['day'])[0] == pixels
        assert captures(app.hub).memory.reserved['unsaved'] == 0
        assert studio.app is app
    asyncio.run(go())
