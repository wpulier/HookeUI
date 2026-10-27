import json
"""TEST ONLY: proposed prepared-delivery boundary over actual retained stores.

Synthetic JPEGs, actual App/Index/Results/research allow-list and Presentations core.
No providers, camera, server, HTTP route or browser drawing. The parent owns execution.
Import the missing adapter per case: missing implementation is RED, never a skip.
The concrete proposal and ownership/guard rules are in actions-adapter-handoff.md §14.
"""
import asyncio
import importlib
import threading
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from orchestrator.capture import CLASSES, Memory, RUN, captures
from orchestrator.presentations import PresentationAction, PresentationContext, ResolvedSource, ResourceRef, SourceRef
from orchestrator.ticket import generations, issue, using
from tests.test_comparison_retention import compare_saved_members, until
from tests.test_workspace_actions_app import CLIENT, WORKSPACE, built, comparison_fixture, digest, jpeg, research_fixture, saved_picture, succeeded
from tests.waiting import HANG_S


@pytest.fixture
def api():
    return importlib.import_module('orchestrator.presentation_delivery')


def context(ticket):
    return PresentationContext(WORKSPACE, CLIENT['clientId'], CLIENT['pageEpoch'],
                               ticket.admission.request_id, 'action:synthetic-delivery', CLIENT['connectionEpoch'])


def resource(entry, n=0):
    return ResourceRef(entry.day, entry.id, entry.version, n)


def store(api, app, memory=None):
    return api.PreparedSources(app.hub.resources, memory or captures(app.hub).memory, RUN)


def held(memory):
    return sum(memory.reserved.values())


@pytest.mark.parametrize('extension,mime', [('png', 'image/png'), ('jpg', 'image/jpeg')])
def test_retained_result_delivers_its_original_format_and_pixels(api, tmp_path, extension, mime):
    app, _ = built(tmp_path)
    pixels = np.arange(18 * 23 * 3, dtype=np.uint8).reshape(18, 23, 3)
    data = cv2.imencode(f'.{extension}', pixels)[1].tobytes()
    results = app.hub.resources.results
    record = results.add('compare', {}, 'Retained native output', picture_bytes=[data])
    entry = app.hub.resources.get(record['id'], record['day'])

    async def go():
        delivery = store(api, app)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        lease = None
        try:
            with using(ticket):
                lease = await delivery.prepare(resource(entry), context(ticket))
            wire = lease.to_wire()
            assert wire['mediaType'] == mime
            assert wire['payloadBinding']['originalDigest'] == digest(data)
            assert wire['payloadBinding']['servedDigest'] == digest(data)
            served = delivery.get(lease.token, context(ticket))
            assert served == data
            np.testing.assert_array_equal(cv2.imdecode(np.frombuffer(served, np.uint8), cv2.IMREAD_UNCHANGED),
                                          cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED))
        finally:
            if lease:
                lease.release()
            ticket.close()
    asyncio.run(go())


@pytest.mark.parametrize('condition', ['other-result', 'other-ordinal', 'traversal', 'symlink', 'changed-bytes'])
def test_native_png_delivery_rejects_wrong_addresses_and_changed_originals(api, tmp_path, condition):
    app, _ = built(tmp_path)
    data = cv2.imencode('.png', np.full((18, 23, 3), 70, np.uint8))[1].tobytes()
    results = app.hub.resources.results
    record = results.add('compare', {}, 'Retained native output', picture_bytes=[data])
    entry = app.hub.resources.get(record['id'], record['day'])

    async def go():
        path = results.root / record['day'] / record['pictures'][0]
        await until(lambda: path.is_file() and (record['day'], path.name) not in results._pending,
                    'native PNG fixture did not finish writing')
        if condition in ('other-result', 'other-ordinal', 'traversal'):
            record['pictures'][0] = {'other-result':'d999-0.png', 'other-ordinal':f'{entry.id}-1.png',
                                     'traversal':f'../{path.name}'}[condition]
        elif condition == 'symlink':
            outside = tmp_path / 'unrelated.png'
            outside.write_bytes(data)
            path.unlink()
            path.symlink_to(outside)
        else:
            path.write_bytes(cv2.imencode('.png', np.full((18, 23, 3), 120, np.uint8))[1].tobytes())
        memory = captures(app.hub).memory
        before = held(memory)
        delivery = store(api, app)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        try:
            with using(ticket), pytest.raises(api.DeliveryUnavailable):
                await delivery.prepare(resource(entry), context(ticket))
            assert held(memory) == before
        finally:
            ticket.close()
    asyncio.run(go())


@pytest.mark.parametrize('condition', ['unchanged', 'changed', 'missing-hash'])
def test_initial_job_open_enforces_the_producers_recorded_picture_digest(api, tmp_path, condition):
    app, _ = built(tmp_path)
    regs = app.registrations
    original = cv2.imencode('.png', np.full((20, 30, 3), 70, np.uint8))[1].tobytes()
    record = {'id': 'j1', 'kind': 'job', 'session': app.day.sid, 'date': app.day.date,
              'thread': app.day.active_id, 'at': 1.0, 'state': 'done', 'entry': 'brainreg',
              'volume': {'id': 'v1', 'name': 'brain'}, 'seconds': 1, 'regions': [],
              'picture': 'brainreg/picture.png', 'outputs': {'picture': {'sha256': digest(original)}}}
    folder = regs.folder(record) / 'out' / 'brainreg'
    folder.mkdir(parents=True)
    (folder / 'picture.png').write_bytes(original if condition != 'changed' else
                                       cv2.imencode('.png', np.full((20, 30, 3), 120, np.uint8))[1].tobytes())
    if condition == 'missing-hash':
        record['outputs'] = {}
    regs._write(record)
    entry = app.hub.resources.get('j1', app.day.sid)

    async def go():
        delivery = store(api, app)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        lease = None
        try:
            with using(ticket):
                if condition == 'unchanged':
                    lease = await delivery.prepare(resource(entry), context(ticket))
                    assert lease.source.to_wire()['digest'] == digest(original)
                else:
                    with pytest.raises(api.DeliveryUnavailable):
                        lease = await delivery.prepare(resource(entry), context(ticket))
        finally:
            if lease:
                lease.release()
            ticket.close()
    asyncio.run(go())


def test_written_report_keeps_its_original_content_when_research_changes(api, tmp_path):
    app, _ = built(tmp_path)
    _, session = research_fixture(app)
    session.result = {'title': 'Five sequences', 'summary': 'Five independent measurements.',
                      'details': ['Mean speed: 12 px/s'], 'limitations': ['No calibrated scale.']}

    async def go():
        delivery = store(api, app)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        ctx = context(ticket)
        lease = None
        try:
            with using(ticket):
                lease = await delivery.prepare(ResourceRef(session.day, session.rid, None, None), ctx)
            original = lease.to_wire()
            assert original['sourceRef']['kind'] == 'written'
            assert original['report']['details'] == ['Mean speed: 12 px/s']
            assert original['report']['limitations'] == ['No calibrated scale.']
            assert json.loads(delivery.get(lease.token, ctx)) == original['report']
            session.result['summary'] = 'A later revision'
            assert lease.to_wire() == original
            with using(ticket), pytest.raises(api.DeliveryUnavailable, match='changed'):
                await delivery.prepare(lease.source, ctx)
        finally:
            if lease:
                lease.release()
            ticket.close()
    asyncio.run(go())


async def saved_on_disk(app, value=70):
    entry, data = saved_picture(app, value)
    results = app.hub.resources.results
    record = results.get(entry.id, entry.day)
    path = results.root / entry.day / record['pictures'][0]
    await until(lambda: path.is_file() and (entry.day, path.name) not in results._pending,
                'actual retained fixture did not finish writing')
    assert path.read_bytes() == data
    return entry, data, path


def research_source(app, session, oid, n):
    from orchestrator.media import of_output

    # Exact historical ID/version/path comes from the existing version authority.
    output = session.output(oid)
    path = Path(session.root) / session.day / session.rid / output['path']
    basis = {'researcher': session.rid, 'output': oid, 'version': output['version'], 'path': output['path']}
    return SourceRef.from_wire({
        'key': f'{session.day}/{session.rid}/{oid}/v{output["version"]}',
        'kind': 'saved-image', 'media': of_output(output), 'availability': 'retained',
        'identity': {'resource': {'day': session.day, 'id': session.rid, 'version': output['version'],
                                  'picture': n, 'outputId': oid}},
        'provenance': basis, 'digest': digest(path.read_bytes()),
    })


def assert_native(lease, data):
    wire = lease.to_wire()
    pixels = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert pixels is not None
    assert wire['sourceRef'] == lease.source.to_wire()
    assert wire['sourceToken'] == lease.token
    assert wire['payloadBinding'] == dict(lease.payload_binding)
    assert wire['payloadBinding']['servedDigest'] == digest(data)
    assert wire['payloadBinding']['originalDigest'] == digest(data)
    assert wire['originalSize'] == [pixels.shape[1], pixels.shape[0]]
    assert wire['transform'] == [1, 0, 0, 1, 0, 0]
    assert wire['mediaType'] == 'image/jpeg'
    assert isinstance(lease.token, str) and lease.token
    assert lease.token not in (lease.source.key, digest(data))
    assert '/' not in lease.token and ':' not in lease.token
    return wire


@pytest.mark.parametrize('ordinal', [0, 1, 2])
def test_actual_retained_comparison_prepares_selected_native_picture(api, tmp_path, ordinal):
    app, studio = built(tmp_path)
    population, ids, originals = comparison_fixture(app)

    async def go():
        runs, _ = await compare_saved_members(app, ids)
        succeeded(runs[0])
        ref = runs[0].resource
        entry = app.hub.resources.get(ref['id'], ref['day'])
        expected = SourceRef.from_wire(entry.picture_source(ordinal))
        data = app.hub.resources.picture(entry.id, ordinal, entry.day)[0]
        if ordinal:
            assert data == originals[ordinal - 1]
        # Later correction cannot change a retained component's producing revision.
        app.hub.evidence.revise(population, 'synthetic later correction', 'user', [ids[0]])
        delivery = store(api, app)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        ctx = context(ticket)
        lease = None
        try:
            with using(ticket):
                lease = await delivery.prepare(resource(entry, ordinal), ctx)
            assert lease.source.to_wire() == expected.to_wire()
            assert lease.source.identity['resource']['picture'] == ordinal
            assert lease.source.identity['resource']['version'] is None
            assert_native(lease, data)
            assert delivery.get(lease.token, ctx) == data
            resolved = delivery.resolve(expected)
            assert isinstance(resolved, ResolvedSource) and resolved.source.to_wire() == expected.to_wire()
            assert callable(resolved.release)
            resolved.release()
            assert studio.app is app
        finally:
            if lease:
                lease.release()
            ticket.close()
    asyncio.run(go())


def test_equal_native_jpegs_from_distinct_producers_have_distinct_opaque_tokens(api, tmp_path):
    app, studio = built(tmp_path)

    async def go():
        a, raw, _ = await saved_on_disk(app)
        b, same, _ = await saved_on_disk(app)
        assert raw == same and a.id != b.id
        delivery = store(api, app)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        ctx, leases = context(ticket), []
        try:
            with using(ticket):
                for entry in (a, b):
                    leases.append(await delivery.prepare(resource(entry), ctx))
            assert leases[0].token != leases[1].token
            assert leases[0].source.key != leases[1].source.key
            for entry, lease in zip((a, b), leases):
                assert lease.source.identity['resource']['id'] == entry.id
                assert_native(lease, raw)
                assert delivery.get(lease.token, ctx) == raw
            assert studio.app is app
        finally:
            for lease in leases:
                lease.release()
            ticket.close()
    asyncio.run(go())


def test_synchronous_resolver_cannot_prepare_read_or_substitute_a_source(api, tmp_path, monkeypatch):
    app, _ = built(tmp_path)
    _, session = research_fixture(app)
    ref = research_source(app, session, 'o2', 1)
    delivery = store(api, app)

    def forbidden(*args, **kwargs):
        raise AssertionError('resolve must not perform I/O or recrop')

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', forbidden)
        patch.setattr(app.hub.resources, 'picture', forbidden)
        patch.setattr('orchestrator.population.captured_picture', forbidden)
        with pytest.raises(api.DeliveryUnavailable) as failure:
            delivery.resolve(ref)
    assert failure.value.code == 'source_not_prepared'


def test_native_token_is_frozen_after_disk_replacement_and_wire_copies_are_detached(api, tmp_path, monkeypatch):
    app, _ = built(tmp_path)

    async def go():
        entry, raw, path = await saved_on_disk(app)
        delivery, memory = store(api, app), captures(app.hub).memory
        baseline = held(memory)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        ctx, lease = context(ticket), None
        try:
            with using(ticket):
                lease = await delivery.prepare(resource(entry), ctx)
            before = assert_native(lease, raw)
            assert held(memory) >= baseline + len(raw)
            path.write_bytes(jpeg(220))
            changed = lease.to_wire()
            changed['sourceRef']['identity']['resource']['id'] = 'd999'
            changed['payloadBinding']['servedDigest'] = 'forged'
            changed['originalSize'][0] = 999
            assert lease.to_wire() == before
            with pytest.raises(TypeError):
                lease.payload_binding['servedDigest'] = 'forged'

            def forbidden(*args, **kwargs):
                raise AssertionError('prepared retrieval/resolution must use pinned native bytes')

            with monkeypatch.context() as patch:
                patch.setattr(Path, 'open', forbidden)
                patch.setattr(app.hub.resources, 'picture', forbidden)
                assert delivery.get(lease.token, ctx) == raw
                resolved = delivery.resolve(lease.source)
                assert isinstance(resolved, ResolvedSource)
                resolved.release()
            lease.release()
            lease.release()  # a handle is idempotent; it cannot consume another owner
            with pytest.raises(api.DeliveryUnavailable) as failure:
                delivery.get(lease.token, ctx)
            assert failure.value.code == 'delivery_revoked'
            with pytest.raises(api.DeliveryUnavailable) as unresolved:
                delivery.resolve(lease.source)
            assert unresolved.value.code == 'source_not_prepared'
            assert held(memory) == baseline
            assert path.read_bytes() == jpeg(220)
        finally:
            if lease:
                lease.release()
            ticket.close()
    asyncio.run(go())


def test_native_dimensions_are_not_replaced_by_screen_acquisition_or_model_thumbnail_limits(api, tmp_path, monkeypatch):
    app, _ = built(tmp_path)

    async def go():
        raw = jpeg(70, width=2048, height=120)
        record = app.hub.resources.results.add('compare', {}, 'Synthetic native wide picture', picture_bytes=[raw])
        entry = app.hub.resources.get(record['id'], record['day'])
        memory, delivery = captures(app.hub).memory, store(api, app)
        baseline, actual_decode = held(memory), cv2.imdecode

        def reserved_decode(*args, **kwargs):
            assert held(memory) >= baseline + len(raw) + 2048 * 120 * 3, 'reserve decoded pixels before decoding'
            return actual_decode(*args, **kwargs)

        ticket = issue(app.hub, 'turn', asked=app.day.here())
        ctx, lease = context(ticket), None
        try:
            with monkeypatch.context() as patch, using(ticket):
                patch.setattr(cv2, 'imdecode', reserved_decode)
                lease = await delivery.prepare(resource(entry), ctx)
            assert_native(lease, raw)
            assert lease.to_wire()['originalSize'] == [2048, 120]
            assert delivery.get(lease.token, ctx) == raw
        finally:
            if lease:
                lease.release()
            ticket.close()
    asyncio.run(go())


def test_actual_presentation_siblings_borrow_independent_reference_counted_leases(api, tmp_path):
    from orchestrator.presentations import Presentations
    app, _ = built(tmp_path)

    async def go():
        entry, raw, _ = await saved_on_disk(app)
        memory, delivery = captures(app.hub).memory, store(api, app)
        baseline = held(memory)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        ctx, lease, retained = context(ticket), None, None
        windows = Presentations(resolve_source=delivery.resolve, app_run=RUN)
        windows.connect(ctx)
        try:
            with using(ticket):
                lease = await delivery.prepare(resource(entry), ctx)
            retained = lease.retain()
            assert retained is not lease and retained.token == lease.token
            first = windows.commit(windows.admit(PresentationAction('open', source=lease.source), ctx)).targets[0]
            second = windows.commit(windows.admit(PresentationAction('duplicate', instance=first), ctx)).targets[0]
            assert first != second
            lease.release()
            retained.release()
            retained.release()
            windows.commit(windows.admit(PresentationAction('close', instance=first), ctx))
            assert delivery.get(lease.token, ctx) == raw and held(memory) >= baseline + len(raw)
            assert windows.get(WORKSPACE, second).source.to_wire() == lease.source.to_wire()
            windows.commit(windows.admit(PresentationAction('close', instance=second), ctx))
            with pytest.raises(api.DeliveryUnavailable) as failure:
                delivery.get(lease.token, ctx)
            assert failure.value.code == 'delivery_revoked'
            assert held(memory) == baseline
        finally:
            windows.reset(WORKSPACE)
            if retained:
                retained.release()
            if lease:
                lease.release()
            ticket.close()
    asyncio.run(go())


def test_exact_historical_research_output_uses_actual_allowlist_and_version_authority(api, tmp_path, monkeypatch):
    import orchestrator.research.files as files
    app, _ = built(tmp_path)
    manager, session = research_fixture(app)
    ref = research_source(app, session, 'o2', 1)
    manager._event({'rid': session.rid, 'event': 'output', 'kind': 'image', 'path': 'outputs/revised.jpg',
                    'caption': 'Synthetic replacement', 'replaces': 'o2', 'change': 'Synthetic new version'})
    assert session.current_outputs()[1]['id'] != 'o2'
    checked, actual = [], files.research_file

    def allowlisted(*args, **kwargs):
        checked.append(args)
        return actual(*args, **kwargs)

    monkeypatch.setattr(files, 'research_file', allowlisted)

    async def go():
        delivery = store(api, app)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        ctx, lease = context(ticket), None
        try:
            with using(ticket):
                lease = await delivery.prepare(ref, ctx)
            assert lease.source.to_wire() == ref.to_wire()
            assert_native(lease, jpeg(180))
            assert delivery.get(lease.token, ctx) == jpeg(180)
            assert checked and any(tuple(call[-2:]) == ('outputs', 'second.jpg') for call in checked)
            assert session.output('o2')['path'] == 'outputs/second.jpg'
        finally:
            if lease:
                lease.release()
            ticket.close()
    asyncio.run(go())


class BoundedReader:
    """Observe only real file reads; never supplies alternate source bytes."""
    def __init__(self, stream, limit, memory, baseline):
        self.stream, self.limit, self.memory, self.baseline = stream, limit, memory, baseline

    def __enter__(self):
        self.stream.__enter__()
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def read(self, size=-1):
        assert 0 < size <= self.limit + 1, 'native reads must have a bounded size'
        assert held(self.memory) >= self.baseline + size, 'reserve before allocating file bytes'
        return self.stream.read(size)

    def __getattr__(self, name):
        return getattr(self.stream, name)


@pytest.mark.parametrize('stop', ['ticket-cancel', 'task-cancel', 'day-reset'])
def test_reservation_precedes_real_read_and_survives_stop_until_worker_exits(api, tmp_path, monkeypatch, stop):
    app, _ = built(tmp_path)

    async def go():
        entry, raw, path = await saved_on_disk(app)
        sibling, same, _ = await saved_on_disk(app, 120)
        memory, delivery = captures(app.hub).memory, store(api, app)
        sibling_ticket = issue(app.hub, 'turn', asked=app.day.here())
        with using(sibling_ticket):
            sibling_lease = await delivery.prepare(resource(sibling), context(sibling_ticket))
        unrelated = memory.reserve('unsaved', 4096, 'synthetic unrelated delivery owner')
        baseline = held(memory)
        entered, leave, exited = threading.Event(), threading.Event(), threading.Event()
        actual = Path.open

        def blocked(file, *args, **kwargs):
            if file == path and (args[0] if args else kwargs.get('mode', 'r')) == 'rb':
                assert held(memory) > baseline, 'source read started before reservation'
                entered.set()
                try:
                    assert leave.wait(HANG_S), 'parent delivery control did not release its worker'
                    return BoundedReader(actual(file, *args, **kwargs), api.MAX_BYTES, memory, baseline)
                finally:
                    exited.set()
            return actual(file, *args, **kwargs)

        monkeypatch.setattr(Path, 'open', blocked)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        task = None
        try:
            with using(ticket):
                task = asyncio.create_task(delivery.prepare(resource(entry), context(ticket)))
                await until(entered.is_set, 'delivery never entered its real bounded source read')
                assert not task.done()
                if stop == 'ticket-cancel':
                    ticket.cancel('synthetic delivery cancellation')
                elif stop == 'task-cancel':
                    task.cancel()
                else:
                    generations(app.hub).bump('session', why='synthetic delivery day reset')
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                assert held(memory) > baseline and not exited.is_set()
                leave.set()
                result = (await asyncio.gather(task, return_exceptions=True))[0]
            assert isinstance(result, (api.DeliveryUnavailable, asyncio.CancelledError))
            if isinstance(result, api.DeliveryUnavailable):
                assert result.code == 'request_stale'
            await until(lambda: exited.is_set() and held(memory) == baseline,
                        'stopped preparation leaked its worker reservation')
            assert delivery.get(sibling_lease.token, context(sibling_ticket)) == same
            with actual(path, 'rb') as retained:
                assert not unrelated.released and retained.read(len(raw) + 1) == raw
        finally:
            leave.set()
            if task and not task.done():
                await asyncio.gather(task, return_exceptions=True)
            sibling_lease.release()
            unrelated.release()
            sibling_ticket.close()
            ticket.close()
    asyncio.run(go())


def test_current_research_ordinal_change_after_read_refuses_without_retargeting(api, tmp_path, monkeypatch):
    app, _ = built(tmp_path)
    manager, session = research_fixture(app)
    entry = app.hub.resources.get(session.rid, session.day)
    path = Path(session.root) / session.day / session.rid / 'outputs/second.jpg'
    actual = Path.open
    changed = []

    def change_after_open(file, *args, **kwargs):
        stream = actual(file, *args, **kwargs)
        if file == path and not changed:
            manager._event({'rid': session.rid, 'event': 'output', 'kind': 'image', 'path': 'outputs/revised.jpg',
                            'caption': 'Synthetic replacement during read', 'replaces': 'o2', 'change': 'Synthetic change'})
            changed.append(True)
        return stream

    monkeypatch.setattr(Path, 'open', change_after_open)

    async def go():
        delivery, memory = store(api, app), captures(app.hub).memory
        baseline = held(memory)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        try:
            with using(ticket), pytest.raises(api.DeliveryUnavailable) as failure:
                await delivery.prepare(resource(entry, 1), context(ticket))
            assert changed and failure.value.code == 'source_identity_mismatch'
            assert held(memory) == baseline
            assert session.output('o2')['path'] == 'outputs/second.jpg'
        finally:
            ticket.close()
    asyncio.run(go())


def test_exact_source_metadata_changed_during_read_is_not_accepted_as_original(api, tmp_path, monkeypatch):
    app, _ = built(tmp_path)
    _, session = research_fixture(app)
    ref = research_source(app, session, 'o2', 1)
    path = Path(session.root) / session.day / session.rid / 'outputs/second.jpg'
    actual, changed = Path.open, []

    def changed_producer(file, *args, **kwargs):
        stream = actual(file, *args, **kwargs)
        if file == path and not changed:
            # Simulate the selected output record changing, rather than a new valid
            # historical replacement; the exact ID/version/path must be checked again.
            session.output('o2')['path'] = 'outputs/revised.jpg'
            changed.append(True)
        return stream

    monkeypatch.setattr(Path, 'open', changed_producer)

    async def go():
        delivery, memory = store(api, app), captures(app.hub).memory
        baseline = held(memory)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        try:
            with using(ticket), pytest.raises(api.DeliveryUnavailable) as failure:
                await delivery.prepare(ref, context(ticket))
            assert changed and failure.value.code == 'source_identity_mismatch'
            assert held(memory) == baseline
            with pytest.raises(api.DeliveryUnavailable) as unresolved:
                delivery.resolve(ref)
            assert unresolved.value.code == 'source_not_prepared'
        finally:
            ticket.close()
    asyncio.run(go())


@pytest.mark.parametrize('bad_path', ['outputs/../second.jpg', 'outputs/second.txt', 'outputs/outside.jpg'])
def test_unsafe_research_paths_use_actual_allowlist_and_never_serve_other_files(api, tmp_path, bad_path):
    app, _ = built(tmp_path)
    _, session = research_fixture(app)
    folder = Path(session.root) / session.day / session.rid / 'outputs'
    (folder / 'second.txt').write_bytes(jpeg(180))
    outside = tmp_path / 'outside.jpg'
    outside.write_bytes(jpeg(180))
    (folder / 'outside.jpg').symlink_to(outside)
    session.output('o2')['path'] = bad_path  # corrupt producer metadata, not a replacement reader
    ref = SourceRef.from_wire({
        'key': 'unsafe-exact-output', 'kind': 'saved-image', 'media': 'generated', 'availability': 'retained',
        'identity': {'resource': {'day': session.day, 'id': session.rid, 'version': 1, 'picture': 1, 'outputId': 'o2'}},
        'provenance': {'researcher': session.rid, 'output': 'o2', 'version': 1, 'path': bad_path},
    })

    async def go():
        delivery, memory = store(api, app), captures(app.hub).memory
        baseline = held(memory)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        try:
            with using(ticket), pytest.raises(api.DeliveryUnavailable) as failure:
                await delivery.prepare(ref, context(ticket))
            assert failure.value.code == 'unsafe_source'
            assert held(memory) == baseline and outside.read_bytes() == jpeg(180)
        finally:
            ticket.close()
    asyncio.run(go())


@pytest.mark.parametrize('damage', ['missing', 'invalid-jpeg', 'different-valid-jpeg'])
def test_missing_or_changed_retained_original_cannot_fall_back_to_composite_or_live(api, tmp_path, damage):
    app, _ = built(tmp_path)
    _, ids, _ = comparison_fixture(app)

    async def go():
        runs, _ = await compare_saved_members(app, ids)
        succeeded(runs[0])
        entry = app.hub.resources.get(runs[0].resource['id'], runs[0].resource['day'])
        child = SourceRef.from_wire(entry.picture_source(1))
        results = app.hub.resources.results
        path = results.root / entry.day / results.get(entry.id, entry.day)['pictures'][1]
        if damage == 'missing':
            path.unlink()
        else:
            path.write_bytes(b'not a JPEG' if damage == 'invalid-jpeg' else jpeg(220))
        delivery, memory = store(api, app), captures(app.hub).memory
        baseline = held(memory)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        try:
            with using(ticket), pytest.raises(api.DeliveryUnavailable) as failure:
                await delivery.prepare(child, context(ticket))
            assert failure.value.code in {'source_unavailable', 'source_identity_mismatch'}
            assert held(memory) == baseline
            assert app.hub.resources.picture(entry.id, 0, entry.day) is not None
            assert app.hub.resources.picture(entry.id, 2, entry.day) is not None
            with pytest.raises(api.DeliveryUnavailable) as unresolved:
                delivery.resolve(child)
            assert unresolved.value.code == 'source_not_prepared'
        finally:
            ticket.close()
    asyncio.run(go())


def test_exhausted_actual_memory_budget_refuses_before_any_picture_read(api, tmp_path, monkeypatch):
    app, _ = built(tmp_path)

    async def go():
        entry, _, path = await saved_on_disk(app)
        memory = Memory({cls: 0 for cls in CLASSES})
        delivery = store(api, app, memory)
        actual = Path.open

        def forbidden_picture(file, *args, **kwargs):
            assert file != path, 'exhausted budget must refuse before reading picture bytes'
            return actual(file, *args, **kwargs)

        monkeypatch.setattr(Path, 'open', forbidden_picture)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        try:
            with using(ticket), pytest.raises(api.DeliveryUnavailable) as failure:
                await delivery.prepare(resource(entry), context(ticket))
            assert failure.value.code == 'source_too_large'
            assert held(memory) == 0
        finally:
            ticket.close()
    asyncio.run(go())


def test_oversized_real_file_read_is_bounded_and_releases_only_its_reservation(api, tmp_path, monkeypatch):
    app, _ = built(tmp_path)

    async def go():
        entry, raw, path = await saved_on_disk(app)
        assert 0 < api.MAX_BYTES <= 10 * 1024 * 1024
        path.write_bytes(raw + b'\0' * (api.MAX_BYTES + 1 - len(raw)))
        memory, delivery = captures(app.hub).memory, store(api, app)
        unrelated = memory.reserve('unsaved', 4096, 'synthetic independent oversized-file owner')
        baseline, actual = held(memory), Path.open

        def bounded(file, *args, **kwargs):
            stream = actual(file, *args, **kwargs)
            if file == path:
                return BoundedReader(stream, api.MAX_BYTES, memory, baseline)
            return stream

        monkeypatch.setattr(Path, 'open', bounded)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        try:
            with using(ticket), pytest.raises(api.DeliveryUnavailable) as failure:
                await delivery.prepare(resource(entry), context(ticket))
            assert failure.value.code == 'source_too_large'
            assert held(memory) == baseline and not unrelated.released
        finally:
            unrelated.release()
            ticket.close()
    asyncio.run(go())


def test_prepare_cannot_borrow_a_different_request_admission(api, tmp_path, monkeypatch):
    app, _ = built(tmp_path)

    async def go():
        entry, _, path = await saved_on_disk(app)
        delivery, actual = store(api, app), Path.open

        def forbidden_picture(file, *args, **kwargs):
            assert file != path, 'mismatched request must refuse before source I/O'
            return actual(file, *args, **kwargs)

        monkeypatch.setattr(Path, 'open', forbidden_picture)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        try:
            with using(ticket), pytest.raises(api.DeliveryUnavailable) as failure:
                await delivery.prepare(resource(entry), replace(context(ticket), request_id='another-request'))
            assert failure.value.code == 'request_stale'
        finally:
            ticket.close()
    asyncio.run(go())


@pytest.mark.parametrize('field', ['workspace_id', 'client_id', 'client_epoch', 'connection_epoch'])
def test_prepared_tokens_cannot_cross_accepted_client_or_workspace_context(api, tmp_path, field):
    app, _ = built(tmp_path)

    async def go():
        entry, raw, _ = await saved_on_disk(app)
        delivery = store(api, app)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        ctx, lease = context(ticket), None
        try:
            with using(ticket):
                lease = await delivery.prepare(resource(entry), ctx)
            with pytest.raises(api.DeliveryUnavailable) as failure:
                delivery.get(lease.token, replace(ctx, **{field: 'another-accepted-origin'}))
            assert failure.value.code == 'context_mismatch'
            # Request/action changes alone do not revoke a still-open same-client pin.
            later = replace(ctx, request_id='later-request', owner='later-action')
            assert delivery.get(lease.token, later) == raw
        finally:
            if lease:
                lease.release()
            ticket.close()
    asyncio.run(go())


@pytest.mark.parametrize('output_id', ['', None, 'o' + '1' * 256],
                         ids=['empty-explicit-id', 'non-string-explicit-id', 'unbounded-explicit-id'])
def test_supplied_research_source_requires_nonempty_bounded_explicit_output_identity(api, tmp_path, output_id):
    """PND-C1: change only the claimed output ID, retaining true current-o2 facts.

    Empty identity must not select the real current ordinal and publish those
    bytes under the false explicit identity. Exercise the real public preparer.
    """
    app, _ = built(tmp_path)
    _, session = research_fixture(app)
    exact = research_source(app, session, 'o2', 1)
    wire = exact.to_wire()
    wire['identity']['resource']['outputId'] = output_id
    malformed = SourceRef.from_wire(wire)
    assert session.current_outputs()[1]['id'] == 'o2'
    assert wire['provenance']['output'] == 'o2' and wire['provenance']['path'] == 'outputs/second.jpg'

    async def go():
        delivery, memory = store(api, app), captures(app.hub).memory
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        ctx, control, unexpected = context(ticket), None, None
        try:
            # A positive exact-ID pin is independently owned before the refusal.
            with using(ticket):
                control = await delivery.prepare(exact, ctx)
            assert control.source.to_wire() == exact.to_wire()
            assert delivery.get(control.token, ctx) == jpeg(180)
            baseline = held(memory)
            with using(ticket), pytest.raises(api.DeliveryUnavailable) as failure:
                unexpected = await delivery.prepare(malformed, ctx)
            assert failure.value.code == 'source_identity_mismatch'
            assert held(memory) == baseline
            assert delivery.get(control.token, ctx) == jpeg(180)
            assert control.source.identity['resource']['outputId'] == 'o2'
            with pytest.raises(api.DeliveryUnavailable) as unresolved:
                delivery.resolve(malformed)
            assert unresolved.value.code == 'source_not_prepared'
        finally:
            # If the old production bug returns a lease, preserve RED while
            # releasing that unexpected test-owned lease rather than leaking it.
            if unexpected:
                unexpected.release()
            if control:
                control.release()
            ticket.close()
    asyncio.run(go())


def test_explicit_historical_output_and_current_resource_ordinal_remain_distinct_positive_controls(api, tmp_path):
    app, _ = built(tmp_path)
    manager, session = research_fixture(app)
    exact = research_source(app, session, 'o2', 1)
    manager._event({'rid': session.rid, 'event': 'output', 'kind': 'image', 'path': 'outputs/revised.jpg',
                    'caption': 'Synthetic replacement for identity control', 'replaces': 'o2', 'change': 'Synthetic new version'})
    entry = app.hub.resources.get(session.rid, session.day)
    current_output = session.current_outputs()[1]
    assert current_output['id'] != 'o2' and current_output['version'] == 2

    async def go():
        delivery = store(api, app)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        ctx, leases = context(ticket), []
        try:
            with using(ticket):
                leases.append(await delivery.prepare(exact, ctx))
                leases.append(await delivery.prepare(resource(entry, 1), ctx))
            historical, current_ordinal = leases
            assert historical.source.to_wire() == exact.to_wire()
            assert historical.source.identity['resource']['outputId'] == 'o2'
            assert historical.source.identity['resource']['version'] == 1
            assert historical.source.to_wire()['provenance']['path'] == 'outputs/second.jpg'
            assert_native(historical, jpeg(180))
            assert delivery.get(historical.token, ctx) == jpeg(180)
            assert current_ordinal.source.identity['resource']['outputId'] == current_output['id']
            assert current_ordinal.source.identity['resource']['version'] == current_output['version']
            assert current_ordinal.source.identity['resource']['picture'] == 1
            assert current_ordinal.source.to_wire()['provenance']['path'] == 'outputs/revised.jpg'
            assert_native(current_ordinal, jpeg(220))
            assert delivery.get(current_ordinal.token, ctx) == jpeg(220)
            assert historical.token != current_ordinal.token
        finally:
            for lease in leases:
                lease.release()
            ticket.close()
    asyncio.run(go())


def test_exact_token_borrow_owns_native_bytes_until_final_http_handle_release_without_io(api, tmp_path, monkeypatch):
    app, _ = built(tmp_path)

    async def go():
        entry, raw, _ = await saved_on_disk(app)
        delivery, memory = store(api, app), captures(app.hub).memory
        baseline = held(memory)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        ctx, original, http_handle = context(ticket), None, None
        try:
            with using(ticket):
                original = await delivery.prepare(resource(entry), ctx)
            descriptor = original.to_wire()
            later = replace(ctx, request_id='synthetic-http-request', owner='synthetic-http-response')

            def forbidden(*args, **kwargs):
                raise AssertionError('token borrow/get must not read a source, resolve an ordinal or recrop')

            with monkeypatch.context() as patch:
                patch.setattr(Path, 'open', forbidden)
                patch.setattr(app.hub.resources, 'get', forbidden)
                patch.setattr(app.hub.resources, 'picture', forbidden)
                patch.setattr('orchestrator.population.captured_picture', forbidden)
                http_handle = delivery.borrow(original.token, later)
                assert http_handle is not original
                assert http_handle.to_wire() == descriptor
                assert http_handle.token == original.token
                assert http_handle.source.to_wire() == original.source.to_wire()
                assert http_handle.to_wire()['mediaType'] == 'image/jpeg'
                original.release()
                original.release()
                assert held(memory) >= baseline + len(raw)
                assert delivery.get(http_handle.token, later) == raw
                assert delivery.get(original.token, ctx) == raw
                http_handle.release()
                http_handle.release()
                assert held(memory) == baseline
                with pytest.raises(api.DeliveryUnavailable) as revoked:
                    delivery.borrow(original.token, later)
                assert revoked.value.code == 'delivery_revoked'
                with pytest.raises(api.DeliveryUnavailable) as unavailable:
                    delivery.get(original.token, ctx)
                assert unavailable.value.code == 'delivery_revoked'
        finally:
            if http_handle:
                http_handle.release()
            if original:
                original.release()
            ticket.close()
    asyncio.run(go())


@pytest.mark.parametrize('field', ['workspace_id', 'client_id', 'client_epoch', 'connection_epoch'])
def test_token_borrow_denies_other_or_replacement_origin_and_revoked_old_token(api, tmp_path, field):
    app, _ = built(tmp_path)

    async def go():
        entry, raw, _ = await saved_on_disk(app)
        delivery, memory = store(api, app), captures(app.hub).memory
        baseline = held(memory)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        ctx, original, unexpected = context(ticket), None, None
        try:
            with using(ticket):
                original = await delivery.prepare(resource(entry), ctx)
            owned = held(memory)
            replacement = replace(ctx, **{field: 'another-accepted-origin'})
            with pytest.raises(api.DeliveryUnavailable) as mismatch:
                unexpected = delivery.borrow(original.token, replacement)
            assert mismatch.value.code == 'context_mismatch'
            assert held(memory) == owned and delivery.get(original.token, ctx) == raw
            # The trusted owner retires its pin; an old context cannot resurrect
            # it. Actual retired-socket routing remains the App's authority.
            original.release()
            assert held(memory) == baseline
            with pytest.raises(api.DeliveryUnavailable) as revoked:
                unexpected = delivery.borrow(original.token, ctx)
            assert revoked.value.code == 'delivery_revoked'
        finally:
            if unexpected:
                unexpected.release()
            if original:
                original.release()
            ticket.close()
    asyncio.run(go())


@pytest.mark.parametrize('token_kind', ['source-key', 'digest', 'ordinal', 'source-ref', 'resource-ref', 'unknown-token'])
def test_token_borrow_cannot_mint_access_from_a_source_address_or_digest(api, tmp_path, monkeypatch, token_kind):
    app, _ = built(tmp_path)

    async def go():
        entry, raw, _ = await saved_on_disk(app)
        delivery, memory = store(api, app), captures(app.hub).memory
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        ctx, original, unexpected = context(ticket), None, None
        try:
            with using(ticket):
                original = await delivery.prepare(resource(entry), ctx)
            candidates = {'source-key': original.source.key, 'digest': digest(raw), 'ordinal': 0,
                          'source-ref': original.source, 'resource-ref': resource(entry),
                          'unknown-token': original.token[:-1] + ('0' if original.token[-1] != '0' else '1')}
            candidate = candidates[token_kind]
            assert candidate != original.token
            owned = held(memory)

            def forbidden(*args, **kwargs):
                raise AssertionError('an invalid token must not trigger source lookup or I/O')

            with monkeypatch.context() as patch:
                patch.setattr(Path, 'open', forbidden)
                patch.setattr(app.hub.resources, 'get', forbidden)
                patch.setattr(app.hub.resources, 'picture', forbidden)
                with pytest.raises(api.DeliveryUnavailable) as unavailable:
                    unexpected = delivery.borrow(candidate, ctx)
                assert unavailable.value.code == 'delivery_revoked'
                assert held(memory) == owned and delivery.get(original.token, ctx) == raw
        finally:
            if unexpected:
                unexpected.release()
            if original:
                original.release()
            ticket.close()
    asyncio.run(go())


def test_exact_findings_revision_keeps_its_report_and_reference_urls(api, tmp_path):
    from orchestrator.research.findings import retain
    app, _ = built(tmp_path)
    _, session = research_fixture(app)
    first = {'title': 'Original findings', 'summary': 'Three cells',
             'report': {'methods': 'Retained capture', 'observations': 'Three cells'},
             'refs': [{'path': 'sources/photo.jpg', 'page': 'https://example.org/cells', 'title': 'Reference'}]}
    retain(session.finding_revisions, first, [], None)
    session.result = {'title': 'New findings', 'summary': 'Four cells'}

    async def go():
        delivery = store(api, app)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        ctx = context(ticket)
        lease = None
        try:
            with using(ticket):
                lease = await delivery.prepare(ResourceRef(session.day, session.rid, 1, None), ctx)
            report = lease.to_wire()['report']
            assert report['summary'] == 'Three cells'
            assert report['findingRevision']['version'] == 1
            assert report['refs'][0]['url'] == f'/research/{session.day}/{session.rid}/sources/photo.jpg'
            assert json.loads(delivery.get(lease.token, ctx)) == report
        finally:
            if lease:
                lease.release()
            ticket.close()
    asyncio.run(go())


def test_saved_presentation_reads_original_findings_after_revision(api, tmp_path):
    from orchestrator.research.findings import retain, reference
    from orchestrator.presentation_delivery import written_source
    app, _ = built(tmp_path)
    _, session = research_fixture(app)
    first = {'title': 'Original study', 'summary': 'Three cells', 'report': {'limitations': 'One field'}}
    record = retain(session.finding_revisions, first, [], None)
    session.outputs.append({'id': 'o9', 'kind': 'presentation', 'version': 1, 'path': 'outputs/o9-presentation.json',
        'findingRevision': reference(record), 'content': {'layout': 'briefing', 'blocks': [{'ref':'summary'}, {'ref':'report:limitations'}]}})
    session.result = {'summary': 'Later science: four cells'}
    async def go():
        delivery = store(api, app)
        ticket = issue(app.hub, 'turn', asked=app.day.here())
        ctx = context(ticket)
        lease = None
        try:
            source, _ = written_source(session, ResourceRef(session.day, session.rid, 1, None), 'o9')
            with using(ticket):
                lease = await delivery.prepare(source, ctx)
            report = lease.to_wire()['report']
            assert report['summary'] == 'Three cells'
            assert report['presentation']['id'] == 'o9'
            assert report['findingRevision']['digest'] == record['digest']
            assert json.loads(delivery.get(lease.token, ctx)) == report
            session.result['summary'] = 'Five cells now'
            assert json.loads(delivery.get(lease.token, ctx))['summary'] == 'Three cells'
        finally:
            if lease: lease.release()
            ticket.close()
    asyncio.run(go())


def test_latest_research_reopen_canonicalizes_exact_persisted_findings(api, tmp_path):
    from orchestrator.research.findings import retain, reference
    from orchestrator.presentation_delivery import written_source
    app, _ = built(tmp_path)
    manager, session = research_fixture(app)
    def revision(summary):
        result = {'summary':summary, 'report':{'observations':summary}}
        record = retain(session.finding_revisions, result, [], None)
        session.result = {**result, 'findingRevision':reference(record)}
        manager._save(session)
        return record
    revision('Original')
    first, first_text = written_source(session, ResourceRef(session.day, session.rid, None, None))
    revision('Revised')
    latest, latest_text = written_source(session, ResourceRef(session.day, session.rid, None, None))
    old, old_text = written_source(session, ResourceRef(session.day, session.rid, 1, None))
    assert first.identity['resource']['version'] == old.identity['resource']['version'] == 1
    assert latest.identity['resource']['version'] == 2
    assert next(entry for entry in app.hub.resources._research(session.day) if entry.id == session.rid).version == 2
    assert first_text == old_text and first.key != latest.key
    assert json.loads(latest_text)['summary'] == 'Revised'


@pytest.mark.parametrize('overlays', [True, False], ids=['with-outlines', 'camera-pixels-only'])
def test_a_snapshot_opens_from_made_today_as_its_own_picture(tmp_path, overlays):
    """Live 2026-10-11 16:22:44: Made today's Open of "Snapshot · 16:22" failed in 5.8 ms, before any studio round
    trip: the snapshot entry (Made today's product; its result copy is not listed) was refused as "no immutable
    retained original for delivery", which only a result's picture had. Its own PNG is its original."""
    import json as _json
    import time as _time

    from tests.test_native_requested_goal_binding import close_app
    from tests.test_workspace_actions_app import output_window, plan

    app, studio = built(tmp_path)
    index = app.hub.resources
    day = index.today()
    folder = index.snapshots / day
    folder.mkdir(parents=True, exist_ok=True)
    name = 'snapshot-162244-0123456789ab-f42'
    ok, png = cv2.imencode('.png', np.full((120, 160, 3), 90, np.uint8))
    (folder / f'{name}.png').write_bytes(png.tobytes())
    (folder / f'{name}.json').write_text(_json.dumps({'frameId': 42, 'wallAt': _time.time(), 'overlays': overlays,
                                                      'outlines': []}))
    entry = index.get(name, day)
    assert entry is not None and entry.kind == 'snapshot'

    async def go():
        runs, ctx = await plan(app, [('present', {'do': 'open', 'title': entry.title, 'source': {
            'day': day, 'id': name, 'version': entry.version, 'picture': 0}})])
        succeeded(runs[0])
        state = studio.store().get(WORKSPACE, output_window(ctx))
        source = state.source.to_wire()
        assert source['identity']['resource'] == {'day': day, 'id': name, 'version': entry.version, 'picture': 0}
        assert source['media'] == ('labelled' if overlays else 'capture')
        assert source['digest'] == digest(png.tobytes())

    try:
        asyncio.run(go())
    finally:
        close_app(app)
