"""Synthetic capture transport through the real App/Registry/Screen acquisition.

Only accepted browser/source/capture authority inputs are synthetic. No provider,
tool run, observation, decoder, request admission or memory owner is substituted.
This first tracer covers the leased originating capture and wrong-client refusal;
it does not certify a browser capture or exercise later concurrency/retry slices.
"""
import asyncio
import gc
import json

from orchestrator.brain.registry import ToolContext
from orchestrator.capture import captures
from orchestrator.commands import COMMANDS
from orchestrator.inspection import PictureInput
from orchestrator.ticket import issue, using
from orchestrator.workspace_observation import ClientRef, Limits
from tests.test_screen_workspace import REF, Rig, capture_reply, digest, jpeg
from tests.waiting import HANG_S


async def origin_and_peer(rig, request):
    # Tight real Screen limits leave room for one actual transport/decode lease,
    # but cannot hide the old tool's additional 18 MiB blanket reservation.
    limits = Limits(image_bytes=4096, image_pixels=96 * 64, image_edge=96, wire_bytes=32768)
    rig.accept(limits=limits)
    peer_ref = ClientRef(REF.workspace_id, 'peer-client', 'peer-page', 'peer-socket')
    rig.accept(rig.other, peer_ref, limits=limits)
    assert (await rig.ingest())['ok']
    pinned = rig.pin(request_id=request.admission.request_id)
    peer = rig.message(seq=90, ref=peer_ref)
    peer['items'][0]['rect'][0] = 700
    assert (await rig.ingest(peer, rig.other))['ok']
    assert rig.observed().wire()['seq'] == 10
    assert rig.observed(rig.other).wire()['seq'] == 90
    return pinned


def context(request, pinned, client):
    return ToolContext('chat', 1, None, 'Inspect my originating workspace',
                       admission=request.admission, client=client, workspace_id=REF.workspace_id,
                       request_snapshot=pinned, request_id=request.admission.request_id)


def test_registered_tool_attaches_originating_pinned_input_with_the_existing_screen_lease(tmp_path):
    rig = Rig(tmp_path)

    async def go():
        request = issue(rig.app.hub, 'turn', channel='chat')
        memory = captures(rig.app.hub).memory
        memory.allowances['analysis'] = 256 * 1024
        run, delivered = None, None
        try:
            pinned = await origin_and_peer(rig, request)
            ctx = context(request, pinned, rig.client)
            with using(request):
                run = rig.app.brain.registry.start('look_at_screen', {'question': 'What is visible?'}, ctx)
                delivered = asyncio.create_task(rig.client.control.get())
                done, _ = await asyncio.wait((run.task, delivered), timeout=0.5,
                                             return_when=asyncio.FIRST_COMPLETED)
                assert delivered in done, (
                    'The registered tool must acquire from the accepted request socket and pin', run.summary)
                wire = delivered.result()
                asked = json.loads(wire) if isinstance(wire, str) else wire
                assert asked['type'] == 'screen-look'
                assert asked['client'] == REF.wire()
                assert asked['requestId'] == request.admission.request_id
                assert asked['originSnapshotId'] == pinned.snapshot_id
                assert rig.other.control.empty(), 'A newer peer report cannot receive the capture request'
                assert memory.reserved['analysis'] > 0, 'Capture transport needs its real pre-await lease'
                raw = jpeg()
                reply = capture_reply(asked, raw)
                reply['requestId'] = request.admission.request_id
                assert (await COMMANDS['screen-image'](rig.app, rig.client, reply))['ok']
                await asyncio.wait_for(run.task, HANG_S)
                assert run.ok is True, run.summary
                assert len(run.images) == 1
                image = run.images[0]
                assert isinstance(image, PictureInput) and image.jpeg.startswith(b'\xff\xd8')
                assert image.facts['channel'] == 'workspace'
                assert image.facts['fidelity'] == 'actual-tab-capture'
                assert image.facts['client'] == REF.wire()
                assert image.facts['requestId'] == request.admission.request_id
                assert image.facts['originSnapshotId'] == pinned.snapshot_id
                assert image.facts['sha256'] == digest(raw)
                assert image.facts['inputSha256'] == digest(image.jpeg)
                assert image.facts['originalSize'] == image.facts['inputSize'] == [96, 64]
                assert rig.surface_checks and all(client is rig.client for client, _ in rig.surface_checks)
                assert run.occurrence.request_id == request.admission.request_id
                assert request.admission.used == 0, 'Acquisition must not make an inner answer-model call'
                before = (memory.reserved['analysis'], memory.peak['analysis'])
                cached = await rig.screen.look(snapshot=pinned, client=rig.client,
                    request_id=request.admission.request_id, targets=(), memory=memory)
                assert cached is image, 'Attach Screen\'s actual leased input; do not copy/redecode/release it'
                assert (memory.reserved['analysis'], memory.peak['analysis']) == before
                assert rig.client.control.empty() and rig.other.control.empty()
                del cached, image
                run.images.clear()
                gc.collect()
                assert memory.reserved['analysis'] == 0
        finally:
            for task in (delivered, run.task if run is not None else None):
                if task is not None:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            if run is not None:
                run.images.clear()
            request.seal()
            gc.collect()
    asyncio.run(go())


def test_registered_tool_refuses_a_peer_context_for_the_original_accepted_pin(tmp_path):
    rig = Rig(tmp_path)

    async def go():
        request = issue(rig.app.hub, 'turn', channel='chat')
        try:
            pinned = await origin_and_peer(rig, request)
            ctx = context(request, pinned, rig.other)
            with using(request):
                run = rig.app.brain.registry.start('look_at_screen', {}, ctx)
                await asyncio.wait_for(run.task, HANG_S)
            assert run.ok is False, 'A peer must not acquire pixels for the original socket\'s pin'
            assert not run.images
            assert rig.client.control.empty() and rig.other.control.empty()
            assert not rig.surface_checks
            assert captures(rig.app.hub).memory.reserved['analysis'] == 0
            assert request.admission.used == 0
        finally:
            request.seal()
    asyncio.run(go())


# Subsequent synthetic acquisition/quota slice; the original tracer above is frozen.
import pytest


def start_registered(rig, ctx, runs, tool='look_at_screen', args=None):
    run = rig.app.brain.registry.start(tool, {} if args is None else args, ctx)
    runs.append(run)
    return run


async def queued_workspace(rig, ctx, runs):
    run = start_registered(rig, ctx, runs)
    delivered = asyncio.create_task(rig.client.control.get())
    try:
        done, _ = await asyncio.wait((run.task, delivered), timeout=0.5,
                                     return_when=asyncio.FIRST_COMPLETED)
        assert delivered in done, ('Original registered workspace acquisition did not request pixels', run.summary)
        wire = delivered.result()
        asked = json.loads(wire) if isinstance(wire, str) else wire
        assert asked['requestId'] == ctx.admission.request_id
        assert asked['originSnapshotId'] == ctx.request_snapshot.snapshot_id
        assert rig.other.control.empty()
        return run, asked
    finally:
        if not delivered.done():
            delivered.cancel()
        await asyncio.gather(delivered, return_exceptions=True)


async def deliver_workspace(rig, ctx, run, asked):
    reply = capture_reply(asked)
    reply['requestId'] = ctx.admission.request_id
    assert (await COMMANDS['screen-image'](rig.app, rig.client, reply))['ok']
    await asyncio.wait_for(run.task, HANG_S)
    assert run.ok is True and len(run.images) == 1, run.summary


async def close_registered(request, runs):
    for run in runs:
        if not run.task.done():
            run.task.cancel()
        await asyncio.gather(run.task, return_exceptions=True)
        run.images.clear()
    request.seal()
    gc.collect()


def resource_args(rig, key):
    return {'id': rig.sources[key]['identity']['resource']['id'], 'inspect': True}


@pytest.mark.parametrize('reuse_after_other', [False, True], ids=['reuse-before-other', 'reuse-at-full-quota'])
def test_registered_reuse_retains_original_owner_and_allows_one_other_picture(tmp_path, reuse_after_other):
    rig = Rig(tmp_path)

    async def go():
        request, runs = issue(rig.app.hub, 'turn', channel='chat'), []
        try:
            pinned = await origin_and_peer(rig, request)
            ctx = context(request, pinned, rig.client)
            with using(request):
                original, asked = await queued_workspace(rig, ctx, runs)
                await deliver_workspace(rig, ctx, original, asked)
                if reuse_after_other:
                    other = start_registered(rig, ctx, runs, 'resources', resource_args(rig, 'source-a'))
                    await asyncio.wait_for(other.task, HANG_S)
                    assert other.ok is True, other.summary
                memory = captures(rig.app.hub).memory
                before = (memory.reserved['analysis'], memory.peak['analysis'])
                repeated = start_registered(rig, ctx, runs)
                await asyncio.wait_for(repeated.task, HANG_S)
                assert repeated.ok is True, 'Reusing an acquired input must succeed even with both unique slots occupied'
                assert not repeated.images, 'The original producer owns the one workspace attachment'
                assert len(original.images) == 1 and original.images[0].facts['originSnapshotId'] == pinned.snapshot_id
                assert (memory.reserved['analysis'], memory.peak['analysis']) == before
                assert rig.client.control.empty() and rig.other.control.empty()
                assert repeated.occurrence.action_id != original.occurrence.action_id
                assert repeated.occurrence.request_id == original.occurrence.request_id == request.admission.request_id
                if not reuse_after_other:
                    other = start_registered(rig, ctx, runs, 'resources', resource_args(rig, 'source-a'))
                    await asyncio.wait_for(other.task, HANG_S)
                    assert other.ok is True, 'Registered reuse must not consume another unique image slot'
                extra = start_registered(rig, ctx, runs, 'resources', resource_args(rig, 'source-b'))
                await asyncio.wait_for(extra.task, HANG_S)
                assert extra.ok is False and not extra.images and 'two' in extra.summary
                supplied = [image for actual in runs if actual.ok is True for image in actual.images]
                assert len(supplied) == 2 and supplied[0] is original.images[0] and supplied[1] is other.images[0]
                from orchestrator.inspection import image_context
                facts = [json.loads(line) for line in image_context(supplied).splitlines()[1:]]
                assert facts[0]['image'] == 1 and facts[0]['originSnapshotId'] == pinned.snapshot_id
                assert facts[1]['image'] == 2 and facts[1]['id'] == resource_args(rig, 'source-a')['id']
                assert request.admission.used == 0
        finally:
            await close_registered(request, runs)
    asyncio.run(go())


def test_pending_registered_capture_reserves_quota_before_two_real_resource_readers(tmp_path):
    rig = Rig(tmp_path)

    async def go():
        request, runs = issue(rig.app.hub, 'turn', channel='chat'), []
        try:
            pinned = await origin_and_peer(rig, request)
            ctx = context(request, pinned, rig.client)
            with using(request):
                pending, asked = await queued_workspace(rig, ctx, runs)
                # The external reply is held: the real workspace producer is still awaiting it.
                assert pending.ok is None and not pending.images
                readers = [start_registered(rig, ctx, runs, 'resources', resource_args(rig, key))
                           for key in ('source-a', 'source-b')]
                await asyncio.wait_for(asyncio.gather(*(reader.task for reader in readers)), HANG_S)
                assert sorted(reader.ok for reader in readers) == [False, True], (
                    'A pending capture must occupy one shared slot before any resource-reader await',
                    [reader.summary for reader in readers])
                refused = next(reader for reader in readers if reader.ok is False)
                assert not refused.images and 'two' in refused.summary
                await deliver_workspace(rig, ctx, pending, asked)
                assert sum(len(actual.images) for actual in runs if actual.ok is True) == 2
                assert pending.images[0].facts['originSnapshotId'] == pinned.snapshot_id
                assert request.admission.used == 0
        finally:
            await close_registered(request, runs)
    asyncio.run(go())


@pytest.mark.parametrize('failure', ['canceled', 'aborted-authority-denial', 'invalid-bytes'])
def test_aborted_or_failed_registered_acquisition_releases_quota_and_allows_original_ticket_retry(tmp_path, failure):
    rig = Rig(tmp_path)

    async def go():
        request, runs = issue(rig.app.hub, 'turn', channel='chat'), []
        try:
            pinned = await origin_and_peer(rig, request)
            ctx = context(request, pinned, rig.client)
            with using(request):
                first, asked = await queued_workspace(rig, ctx, runs)
                if failure == 'aborted-authority-denial':
                    rig.surface_allowed = False
                    reply = capture_reply(asked)
                    reply['requestId'] = request.admission.request_id
                    denied = await COMMANDS['screen-image'](rig.app, rig.client, reply)
                    assert denied['ok'] is False and denied['code'] == 'CAPTURE_WRONG_SURFACE'
                    assert not first.images
                    # A refused envelope cannot terminate another valid pending reply;
                    # the acquisition caller now explicitly aborts this real tool task.
                if failure == 'invalid-bytes':
                    reply = capture_reply(asked, b'\xff\xd8not-a-decodable-JPEG')
                    reply['requestId'] = request.admission.request_id
                    rejected = await COMMANDS['screen-image'](rig.app, rig.client, reply)
                    assert rejected['ok'] is False
                    await asyncio.wait_for(first.task, HANG_S)
                    assert first.ok is False
                else:
                    first.task.cancel()
                    await asyncio.gather(first.task, return_exceptions=True)
                    assert first.task.cancelled() and first.ok is not True
                gc.collect()
                assert not first.images and captures(rig.app.hub).memory.reserved['analysis'] == 0
                rig.surface_allowed = True
                retry, again = await queued_workspace(rig, ctx, runs)
                assert again['id'] != asked['id'] and again['requestId'] == asked['requestId']
                await deliver_workspace(rig, ctx, retry, again)
                other = start_registered(rig, ctx, runs, 'resources', resource_args(rig, 'source-a'))
                await asyncio.wait_for(other.task, HANG_S)
                assert other.ok is True, 'A failed reservation must not displace a valid second unique input'
                extra = start_registered(rig, ctx, runs, 'resources', resource_args(rig, 'source-b'))
                await asyncio.wait_for(extra.task, HANG_S)
                assert extra.ok is False and not extra.images and 'two' in extra.summary
                assert retry.occurrence.request_id == first.occurrence.request_id == request.admission.request_id
                assert retry.occurrence.action_id != first.occurrence.action_id
                assert request.admission.used == 0
        finally:
            await close_registered(request, runs)
    asyncio.run(go())


def test_accepted_registered_context_without_pin_refuses_legacy_or_peer_capture(tmp_path):
    rig = Rig(tmp_path)

    async def go():
        request, runs = issue(rig.app.hub, 'turn', channel='chat'), []
        try:
            await origin_and_peer(rig, request)
            with using(request):
                ctx = context(request, None, rig.client)
                refused = start_registered(rig, ctx, runs)
                await asyncio.wait_for(refused.task, HANG_S)
                assert refused.ok is False and not refused.images
                assert 'snapshot' in refused.summary.lower() or 'pin' in refused.summary.lower()
                assert rig.client.control.empty() and rig.other.control.empty()
                assert not rig.surface_checks and captures(rig.app.hub).memory.reserved['analysis'] == 0
                assert request.admission.used == 0
        finally:
            await close_registered(request, runs)
    asyncio.run(go())


def test_canceling_original_ticket_prevents_retry_despite_freed_acquisition_quota(tmp_path):
    rig = Rig(tmp_path)

    async def go():
        request, runs = issue(rig.app.hub, 'turn', channel='chat'), []
        try:
            pinned = await origin_and_peer(rig, request)
            ctx = context(request, pinned, rig.client)
            with using(request):
                first, _ = await queued_workspace(rig, ctx, runs)
                first.task.cancel()
                await asyncio.gather(first.task, return_exceptions=True)
                request.cancel('synthetic user canceled the original request')
                retry = start_registered(rig, ctx, runs)
                await asyncio.wait_for(retry.task, HANG_S)
                assert retry.ok is False and not retry.images
                assert 'called off' in retry.summary.lower() or 'cancel' in retry.summary.lower()
                assert retry.occurrence.request_id == first.occurrence.request_id == request.admission.request_id
                assert rig.client.control.empty() and rig.other.control.empty()
                assert captures(rig.app.hub).memory.reserved['analysis'] == 0
                assert request.admission.used == 0
        finally:
            await close_registered(request, runs)
    asyncio.run(go())


def test_concurrent_registered_workspace_waiters_share_original_transport_input_and_unique_quota(tmp_path):
    rig = Rig(tmp_path)

    async def go():
        request, runs = issue(rig.app.hub, 'turn', channel='chat'), []
        try:
            pinned = await origin_and_peer(rig, request)
            ctx = context(request, pinned, rig.client)
            with using(request):
                original, asked = await queued_workspace(rig, ctx, runs)
                memory = captures(rig.app.hub).memory
                before = (memory.reserved['analysis'], memory.peak['analysis'])
                repeated = start_registered(rig, ctx, runs)
                await asyncio.sleep(0)  # let the actual duplicate handler reach its held acquisition
                assert not repeated.task.done() and repeated.ok is None and not repeated.images
                assert (memory.reserved['analysis'], memory.peak['analysis']) == before
                assert rig.client.control.empty() and rig.other.control.empty()
                other = start_registered(rig, ctx, runs, 'resources', resource_args(rig, 'source-a'))
                await asyncio.wait_for(other.task, HANG_S)
                assert other.ok is True, 'A duplicate waiter cannot occupy the remaining unique picture slot'
                third = start_registered(rig, ctx, runs, 'resources', resource_args(rig, 'source-b'))
                await asyncio.wait_for(third.task, HANG_S)
                assert third.ok is False and not third.images and 'two' in third.summary
                await deliver_workspace(rig, ctx, original, asked)
                await asyncio.wait_for(repeated.task, HANG_S)
                assert repeated.ok is True and not repeated.images
                assert len(original.images) == 1 and original.images[0].facts['originSnapshotId'] == pinned.snapshot_id
                assert sum(len(actual.images) for actual in runs if actual.ok is True) == 2
                assert rig.client.control.empty() and rig.other.control.empty()
                assert repeated.occurrence.action_id != original.occurrence.action_id
                assert repeated.occurrence.request_id == original.occurrence.request_id == request.admission.request_id
                assert request.admission.used == 0
        finally:
            await close_registered(request, runs)
        assert captures(rig.app.hub).memory.reserved['analysis'] == 0
    asyncio.run(go())


def test_canceling_only_duplicate_waiter_preserves_original_pending_capture_and_lease(tmp_path):
    rig = Rig(tmp_path)

    async def go():
        request, runs = issue(rig.app.hub, 'turn', channel='chat'), []
        try:
            pinned = await origin_and_peer(rig, request)
            ctx = context(request, pinned, rig.client)
            with using(request):
                original, asked = await queued_workspace(rig, ctx, runs)
                memory = captures(rig.app.hub).memory
                before = (memory.reserved['analysis'], memory.peak['analysis'])
                repeated = start_registered(rig, ctx, runs)
                await asyncio.sleep(0)  # cancellation lands on the real duplicate's pending wait
                assert not repeated.task.done() and repeated.ok is None
                repeated.task.cancel()
                await asyncio.gather(repeated.task, return_exceptions=True)
                assert repeated.task.cancelled() and not repeated.images
                assert not original.task.done() and not original.task.cancelled() and original.ok is None
                assert request.cancelled is None
                assert (memory.reserved['analysis'], memory.peak['analysis']) == before
                assert rig.client.control.empty() and rig.other.control.empty()
                other = start_registered(rig, ctx, runs, 'resources', resource_args(rig, 'source-a'))
                await asyncio.wait_for(other.task, HANG_S)
                assert other.ok is True, 'Canceling a duplicate must neither spend nor release its owner\'s unique slot'
                third = start_registered(rig, ctx, runs, 'resources', resource_args(rig, 'source-b'))
                await asyncio.wait_for(third.task, HANG_S)
                assert third.ok is False and not third.images and 'two' in third.summary
                await deliver_workspace(rig, ctx, original, asked)
                assert len(original.images) == 1 and original.images[0].facts['originSnapshotId'] == pinned.snapshot_id
                assert sum(len(actual.images) for actual in runs if actual.ok is True) == 2
                assert repeated.occurrence.request_id == original.occurrence.request_id == request.admission.request_id
                assert repeated.occurrence.action_id != original.occurrence.action_id
                assert rig.client.control.empty() and rig.other.control.empty()
                assert request.admission.used == 0
        finally:
            await close_registered(request, runs)
        assert captures(rig.app.hub).memory.reserved['analysis'] == 0
    asyncio.run(go())
