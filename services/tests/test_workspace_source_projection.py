"""TEST ONLY: retained composite authority through canonical Screen ingress.

Synthetic pixels/rendering, actual App/Registry/Results/PreparedSources/core/Screen.
No model, camera, browser or server. Local writes only; parent owns execution.
The original workspace-action cohort and production source remain unchanged.
"""
import asyncio
import copy
import time
from contextlib import asynccontextmanager

import pytest

from orchestrator.apply import WORKSPACE as WORKSPACE_DESTINATION, apply
from orchestrator.brain.registry import ActionOccurrence
from orchestrator.capture import RUN
from orchestrator.commands import COMMANDS
from orchestrator.presentation_wire import command
from orchestrator.presentations import PresentationAction, PresentationError, ResourceRef, SourceRef
from orchestrator.screen import CONFIRM_S
from orchestrator.ticket import issue, using
from orchestrator.workspace_observation import ClientRef, normalize_observation
from tests.test_comparison_retention import compare_saved_members
from tests.test_workspace_actions_app import built, comparison_fixture, digest, succeeded


@asynccontextmanager
async def native_comparison(app, studio):
    """Real producer and native commit, before any report fault is introduced."""
    _, ids, originals = comparison_fixture(app)
    runs, _ = await compare_saved_members(app, ids)
    succeeded(runs[0])
    resource = runs[0].resource
    entry = app.hub.resources.get(resource['id'], resource['day'])
    full = copy.deepcopy(entry.picture_source(0))
    assert 'captures' in full and full['captures']
    assert full['requestId'] and full['step'] is not None
    assert len(full['components']) == 2
    assert [child['digest'] for child in full['components']] == [digest(pixels) for pixels in originals]
    raw = app.hub.resources.picture(entry.id, 0, entry.day)[0]
    ticket = issue(app.hub, 'command', asked=app.day.here(), channel='studio')
    occurrence = ActionOccurrence(ticket.admission.request_id, 'synthetic-source-projection')
    ctx = app.workspace_context(studio, request_id=occurrence.request_id, owner=occurrence.action_id)
    core, store = studio.store(), app.services.prepared_sources
    lease = None
    try:
        with using(ticket):
            lease = await store.prepare(ResourceRef(entry.day, entry.id, entry.version, 0), ctx)
            assert lease.source.to_wire() == full
            assert store.get(lease.token, ctx) == raw
            admitted = core.admit(PresentationAction('open', source=lease.source), ctx)
            committed = await apply(app.hub, lambda _place: core.commit(admitted), scope='session',
                                    destination=WORKSPACE_DESTINATION)
            assert committed.ok, committed.reason
            state = core.get(ctx.workspace_id, committed.value.targets[0])
            issued = time.monotonic()
            wire = command(committed.value, op='open', states=(state,),
                deliveries={state.id: lease}, before={}, occurrence=occurrence,
                after_report_seq=app.hub.screen.completion_report(studio)['seq'],
                issued_mono=issued, deadline_mono=issued + CONFIRM_S)
            yield store, lease, ctx, full, raw, wire
            assert ticket.admission.used == 0
    finally:
        core.reset(ctx.workspace_id)  # release only this local fixture's core ownership
        if lease is not None:
            lease.release()
        ticket.close()


async def observed_ingress(app, studio, monkeypatch, ctx, wire, fault=None):
    """Capture the actual handler result, without replacing any executor/authority."""
    ingress = COMMANDS['screen']
    captured = {}
    previous = app.hub.screen.observation(studio)
    accepted = ClientRef(ctx.workspace_id, ctx.client_id, ctx.client_epoch, ctx.connection_epoch)

    async def receive(owner, client, message):
        assert owner is app and client is studio and not captured
        message = copy.deepcopy(message)
        if fault == 'digest':
            # Valid descriptor shape, false exact bytes; native command/token stay intact.
            message['sources'][0]['digest'] = '0' * 64
        elif fault == 'component-order':
            message['sources'][0]['components'].reverse()
        captured['projected'] = normalize_observation(message, client=accepted, app_run_id=RUN,
            received_at=time.monotonic(), previous=previous).wire()['sources'][0]
        captured['message'] = message
        captured['reply'] = await ingress(owner, client, message)
        return captured['reply']

    monkeypatch.setitem(COMMANDS, 'screen', receive)
    await studio.observe_command(wire)  # existing synthetic decoder, actual canonical Screen handler
    assert captured, 'the committed native command must reach real Screen ingress'
    return captured, previous


def test_retained_composite_projection_admits_ingress_and_resolves_full_prepared_producer(tmp_path, monkeypatch):
    app, studio = built(tmp_path)

    async def go():
        async with native_comparison(app, studio) as (store, lease, ctx, full, raw, wire):
            captured, previous = await observed_ingress(app, studio, monkeypatch, ctx, wire)
            projected = captured['projected']
            assert 'captures' not in projected and 'requestId' not in projected
            assert projected['provenance'] == full['provenance']
            assert projected['components'] == full['components'] and projected['digest'] == digest(raw)
            # RED must name ingress's first refusal, rather than a later confirmation timeout.
            assert captured['reply'] == {'ok': True}, f"actual Screen ingress first cause: {captured['reply']}"
            # WorkspaceRuntime checks a closed SourceRef before it prepares delivery.
            # Use the real observation projection as the expected wire boundary,
            # retaining the complete producer in the actual core and native pin.
            iid = wire['targets'][0]
            assert wire['instances'][0]['sourceRef'] == projected, 'native descriptor must use the closed source projection'
            assert wire['fence']['targets'][iid]['sourceRef'] == projected, 'the causal fence must bind the same projection'
            assert studio.store().get(ctx.workspace_id, iid).source.to_wire() == full
            accepted = app.hub.screen.observation(studio)
            assert accepted.wire()['seq'] == captured['message']['seq'] > previous.wire()['seq']
            assert accepted.wire()['sources'] == [projected]
            resolved = store.resolve(SourceRef.from_wire(projected), context=ctx)
            try:
                assert resolved.source.to_wire() == full, 'metadata resolution must preserve the full prepared producer'
                assert resolved.source.to_wire()['captures'] == full['captures']
                assert store.get(lease.token, ctx) == raw
            finally:
                resolved.release()
    asyncio.run(go())


@pytest.mark.parametrize('fault', ['digest', 'component-order'])
def test_composite_projection_refuses_changed_digest_or_order_without_losing_valid_pin(tmp_path, monkeypatch, fault):
    app, studio = built(tmp_path)

    async def go():
        async with native_comparison(app, studio) as (store, lease, ctx, full, raw, wire):
            # Independent positive authority control: the exact full producer is pinned.
            valid = store.resolve(SourceRef.from_wire(full), context=ctx)
            valid.release()
            captured, previous = await observed_ingress(app, studio, monkeypatch, ctx, wire, fault)
            assert captured['reply']['ok'] is False
            assert captured['reply']['code'] == 'SOURCE_UNRESOLVED', captured['reply']
            assert app.hub.screen.observation(studio) is previous, 'rejection cannot advance accepted sequence/time'
            with pytest.raises(PresentationError) as refused:
                store.resolve(SourceRef.from_wire(captured['projected']), context=ctx)
            assert refused.value.code == 'source_not_prepared'
            retained = store.resolve(SourceRef.from_wire(full), context=ctx)
            try:
                assert retained.source.to_wire() == full
                assert store.get(lease.token, ctx) == raw
            finally:
                retained.release()
    asyncio.run(go())


async def refused_projected_report(app, studio, monkeypatch, wire, mutate=None, receiver=None):
    """Change only report input; the actual registered source authority decides."""
    receiver = studio if receiver is None else receiver
    ctx = app.workspace_context(receiver, request_id=wire['requestId'], owner='synthetic-source-refusal')
    accepted = ClientRef(ctx.workspace_id, ctx.client_id, ctx.client_epoch, ctx.connection_epoch)
    previous = app.hub.screen.observation(receiver)
    ingress = COMMANDS['screen']
    captured = {}

    async def receive(owner, client, message):
        assert owner is app and client is studio and not captured
        message = copy.deepcopy(message)
        if mutate is not None:
            mutate(message['sources'][0])
        if receiver is not studio:
            message['client'] = accepted.wire()
            for item in message['items']:
                item['workspaceId'] = ctx.workspace_id
        captured['projected'] = normalize_observation(message, client=accepted, app_run_id=RUN,
            received_at=time.monotonic(), previous=previous).wire()['sources'][0]
        captured['reply'] = await ingress(owner, receiver, message)
        return captured['reply']

    monkeypatch.setitem(COMMANDS, 'screen', receive)
    await studio.observe_command(wire)
    assert captured['reply']['ok'] is False
    assert captured['reply']['code'] == 'SOURCE_UNRESOLVED', captured['reply']
    assert app.hub.screen.observation(receiver) is previous, 'refusal cannot advance accepted sequence/time'
    return captured['projected']


@pytest.mark.parametrize('fault', [
    'provenance', 'media', 'revision', 'availability', 'locator', 'digest-absent', 'digest-null',
    'json-type', 'child-revision', 'child-digest-absent',
])
def test_projected_source_facts_refuse_atomically_and_preserve_original_pin(tmp_path, monkeypatch, fault):
    app, studio = built(tmp_path)

    def mutate(source):
        if fault == 'provenance':
            source['provenance']['quality'] += 1
        elif fault == 'media':
            source['media'] = 'raw'
        elif fault == 'revision':
            source['identity']['resource']['version'] = 1
        elif fault == 'availability':
            source['availability'] = 'temporary'
        elif fault == 'locator':
            source['locator'] = 'synthetic:unprepared-locator'
        elif fault == 'digest-absent':
            source.pop('digest')
        elif fault == 'digest-null':
            source['digest'] = None  # valid wire shape, different from the prepared string digest
        elif fault == 'json-type':
            source['provenance']['componentsInOrder'] = 1  # Python equality alone would equate this with True
        elif fault == 'child-revision':
            source['components'][0]['provenance']['revision'] += 1
        elif fault == 'child-digest-absent':
            source['components'][0].pop('digest')

    async def go():
        async with native_comparison(app, studio) as (store, lease, ctx, full, raw, wire):
            with monkeypatch.context() as positive_patch:
                positive, _ = await observed_ingress(app, studio, positive_patch, ctx, wire)
            assert positive['reply'] == {'ok': True}
            baseline = app.hub.screen.observation(studio)
            valid = store.resolve(SourceRef.from_wire(positive['projected']), context=ctx)
            valid.release()
            with monkeypatch.context() as negative_patch:
                changed = await refused_projected_report(app, studio, negative_patch, wire, mutate)
            assert app.hub.screen.observation(studio) is baseline
            with pytest.raises(PresentationError) as refused:
                store.resolve(SourceRef.from_wire(changed), context=ctx)
            assert refused.value.code == 'source_not_prepared'
            valid = store.resolve(SourceRef.from_wire(positive['projected']), context=ctx)
            try:
                assert valid.source.to_wire() == full
                assert store.get(lease.token, ctx) == raw
                assert studio.store().get(ctx.workspace_id, wire['targets'][0]).source.to_wire() == full
            finally:
                valid.release()
    asyncio.run(go())


@pytest.mark.parametrize('failure', ['wrong-origin', 'released', 'ambiguous'])
def test_projected_source_grants_refuse_wrong_origin_release_and_ambiguity(tmp_path, monkeypatch, failure):
    from tests.test_workspace_actions_app import AppStudio

    app, studio = built(tmp_path)

    async def go():
        async with native_comparison(app, studio) as (store, lease, ctx, full, raw, wire):
            with monkeypatch.context() as positive_patch:
                positive, _ = await observed_ingress(app, studio, positive_patch, ctx, wire)
            assert positive['reply'] == {'ok': True}
            projected = SourceRef.from_wire(positive['projected'])
            baseline = app.hub.screen.observation(studio)
            peer, peer_ctx, peer_lease, survivor = None, None, None, None
            try:
                if failure == 'released':
                    # Independent real component pin remains owned when the composite is revoked.
                    survivor = await store.prepare(SourceRef.from_wire(full['components'][0]), ctx)
                    surviving_bytes = store.get(survivor.token, ctx)
                    assert digest(surviving_bytes) == survivor.source.to_wire()['digest']
                    studio.store().reset(ctx.workspace_id)
                    lease.release()  # reset released core ownership; this is the last actual composite handle
                    with pytest.raises(PresentationError) as revoked:
                        store.get(lease.token, ctx)
                    assert revoked.value.code == 'delivery_revoked'
                    with monkeypatch.context() as negative_patch:
                        await refused_projected_report(app, studio, negative_patch, wire)
                    with pytest.raises(PresentationError) as refused:
                        store.resolve(projected, context=ctx)
                    assert refused.value.code == 'source_not_prepared'
                    valid = store.resolve(survivor.source, context=ctx)
                    try:
                        assert valid.source.to_wire() == survivor.source.to_wire()
                        assert store.get(survivor.token, ctx) == surviving_bytes
                    finally:
                        valid.release()
                else:
                    peer = AppStudio(app)
                    peer_ref = ClientRef('synthetic-projection-peer-workspace', 'synthetic-projection-peer',
                                         'synthetic-projection-peer-page', 'synthetic-projection-peer-socket')
                    peer.client_ref = peer_ref.wire()
                    app.accept_workspace(peer, peer_ref)
                    await peer.send_report()
                    peer_ctx = app.workspace_context(peer, request_id=ctx.request_id, owner='synthetic-peer-preparation')
                    if failure == 'wrong-origin':
                        with monkeypatch.context() as negative_patch:
                            await refused_projected_report(app, studio, negative_patch, wire, receiver=peer)
                        with pytest.raises(PresentationError) as refused:
                            store.resolve(projected, context=peer_ctx)
                        assert refused.value.code == 'source_not_prepared'
                        with pytest.raises(PresentationError) as refused_token:
                            store.borrow(lease.token, peer_ctx)
                        assert refused_token.value.code == 'context_mismatch'
                    else:
                        # Two actual preparations, no fabricated resident pin or metadata resolver.
                        peer_lease = await store.prepare(SourceRef.from_wire(full), peer_ctx)
                        assert peer_lease.token != lease.token
                        assert peer_lease.source.to_wire() == full
                        assert store.get(peer_lease.token, peer_ctx) == raw
                        with pytest.raises(PresentationError) as ambiguous:
                            store.resolve(projected)
                        assert ambiguous.value.code == 'context_mismatch'
                        for origin, owned in ((ctx, lease), (peer_ctx, peer_lease)):
                            valid = store.resolve(projected, context=origin)
                            try:
                                assert valid.source.to_wire() == full
                                assert store.get(owned.token, origin) == raw
                            finally:
                                valid.release()
                    valid = store.resolve(projected, context=ctx)
                    try:
                        assert valid.source.to_wire() == full
                        assert store.get(lease.token, ctx) == raw
                    finally:
                        valid.release()
                assert app.hub.screen.observation(studio) is baseline
            finally:
                if survivor is not None:
                    survivor.release()
                if peer_lease is not None:
                    peer_lease.release()
                if peer_ctx is not None:
                    studio.store().disconnect(peer_ctx)
                if peer is not None:
                    app.hub.screen.gone(peer)
    asyncio.run(go())
