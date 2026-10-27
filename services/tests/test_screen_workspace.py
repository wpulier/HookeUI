"""TEST-ONLY vertical slice through actual App/COMMANDS/Screen/resource/memory seams.

Inputs are literal synthetic renderer records and locally retained synthetic JPEGs.
Trusted client/source/capture callbacks stand in for server/browser authority, not
for normalization, pinning, current-target checks, decoding or memory admission.
No model, server, lifespan, camera, browser or private account fixture is started.
Missing adapters fail behavioral assertions; no missing imports, skips or xfails.
"""
import asyncio
import base64
import copy
import gc
import hashlib
import inspect
import json
from contextlib import suppress

import cv2
import numpy as np
import pytest

from orchestrator.app import App
from orchestrator.brain.registry import ToolFailed
from orchestrator.capture import RUN, Refused, captures
from orchestrator.commands import COMMANDS
from orchestrator.hub import Client
from orchestrator.paths import Paths
from orchestrator.screen import Screen
from orchestrator.workspace_observation import (
    ClientRef, Limits, Observation, ObservationError, RequestSnapshot,
    normalize_observation,
)
from tests.test_workspace_observation import item, report


REF = ClientRef('synthetic-workspace', 'synthetic-client', 'synthetic-page', 'synthetic-socket')


def seam(screen, name):
    method = getattr(screen, name, None)
    assert callable(method), f'MISSING_FEATURE: actual Screen.{name} adapter is required'
    return method


def workspace_look(screen):
    method = screen.look
    required = {'snapshot', 'client', 'request_id', 'targets', 'memory'}
    assert required <= inspect.signature(method).parameters.keys(), (
        'MISSING_FEATURE: existing Screen.look must acquire a client-bound leased workspace input')
    return method


def jpeg(width=96, height=64, value=70):
    pixels = np.full((height, width, 3), value, np.uint8)
    pixels[5:15, 8:24] = [20, 180, 90]
    ok, encoded = cv2.imencode('.jpg', pixels)
    assert ok
    return encoded.tobytes()


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Rig:
    """Real app state; transport and authority inputs only are synthetic.

    accept_client is a proposed server-only registration seam. Nothing in a raw
    report grants that registration or capture permission. The capture authority
    result is deliberately independent of client-supplied verified/proof flags.
    """
    def __init__(self, tmp_path):
        self.app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
        self.screen = self.app.hub.screen
        assert isinstance(self.screen, Screen)
        self.now = 10.0
        self.screen.clock = lambda: self.now
        self.client = Client(None)
        self.other = Client(None)
        self.app.hub.clients.update((self.client, self.other))
        self.surface_allowed = True
        self.surface_checks = []
        self.sources = {}
        for key, value in [('source-a', 70), ('source-b', 170)]:
            data = jpeg(value=value)
            record = self.app.hub.resources.results.add(
                'compare', {}, 'Synthetic retained workspace test picture', picture_bytes=[data])
            entry = self.app.hub.resources.get(record['id'], record['day'])
            assert self.app.hub.resources.picture(entry.id, 0, entry.day)[0] == data
            self.sources[key] = {
                'key': key, 'kind': 'saved-image', 'media': 'capture', 'availability': 'retained',
                'identity': {'resource': {'day': entry.day, 'id': entry.id,
                                          'version': entry.version, 'picture': 0}},
                'provenance': {'producer': 'synthetic-retained-fixture'}, 'digest': digest(data),
            }

    def resolve_source(self, client, supplied):
        """Trusted fixture callback reads exact actual retained Index identity/bytes."""
        authoritative = self.sources.get(supplied.get('key'))
        if authoritative is None or supplied.get('identity') != authoritative['identity']:
            raise ObservationError('SOURCE_UNRESOLVED', 'unregistered synthetic source identity')
        ref = authoritative['identity']['resource']
        entry = self.app.hub.resources.get(ref['id'], ref['day'])
        found = self.app.hub.resources.picture(ref['id'], ref['picture'], ref['day'])
        if entry is None or entry.version != ref['version'] or not found or digest(found[0]) != authoritative['digest']:
            raise ObservationError('SOURCE_UNRESOLVED', 'exact retained fixture bytes unavailable')
        return copy.deepcopy(authoritative)

    def verify_capture(self, client, pending, reply):
        """Synthetic trusted browser authority; not real surface verification evidence."""
        self.surface_checks.append((client, copy.deepcopy(pending)))
        return self.surface_allowed and client is self.client

    def accept(self, client=None, ref=REF, *, limits=None):
        client = self.client if client is None else client
        # These attributes match the actions test transport field adapter. The
        # server-owned registration, rather than payload fields, is authoritative.
        client.client_ref = ref.wire()
        client.workspace_id, client.client_id = ref.workspace_id, ref.client_id
        client.page_epoch, client.connection_epoch = ref.page_epoch, ref.connection_epoch
        seam(self.screen, 'accept_client')(
            client, ref, app_run_id=RUN, source_resolver=self.resolve_source,
            capture_authority=self.verify_capture, limits=limits or Limits())
        return client

    def message(self, *, seq=10, ref=REF):
        value = report(seq=seq)
        value.update(type='screen', client=ref.wire(), appRunId=RUN)
        value['sources'] = [copy.deepcopy(s) for s in self.sources.values()]
        value['items'].append(item('window-b', 'source-b'))
        for n, drawn in enumerate(value['items']):
            drawn['workspaceId'] = ref.workspace_id
            drawn['lifetimeGeneration'] = f'ea66d0c1-b74e-41fd-94a6-489597411d8{n}'
            drawn['style'] = {'opacity': 0.5, 'colour': '#22c55e'}
            drawn['payloadBinding'] = {'decodedToken': drawn['sourceToken'],
                'servedDigest': self.sources[drawn['sourceKey']]['digest'],
                'originalDigest': self.sources[drawn['sourceKey']]['digest'], 'transform': 'original JPEG'}
            drawn['controls'].append({'id': f'opacity:{drawn["id"]}', 'targetId': drawn['id'],
                'role': 'slider', 'editable': False, 'present': True, 'enabled': True,
                'observedValue': 0.5, 'stateRevision': 8})
            drawn['propertyVersions']['opacity'] = 8
            drawn['propertyOwners']['opacity'] = 'user'
        value['targets'].append({'id': 'target-b', 'type': 'presentation',
            'presentationId': 'window-b', 'sourceKey': 'source-b', 'basis': 'registered'})
        return value

    async def ingest(self, message=None, client=None):
        return await COMMANDS['screen'](self.app, self.client if client is None else client,
                                       self.message() if message is None else message)

    def observed(self, client=None):
        value = seam(self.screen, 'observation')(self.client if client is None else client)
        assert isinstance(value, Observation), 'BEHAVIOR: Screen must retain the canonical normalized value'
        return value

    def pin(self, *, inline=None, timing='client-send', request_id='synthetic-request', client=None):
        value = seam(self.screen, 'pin')(self.client if client is None else client,
            request_id=request_id, inline=inline, timing=timing, max_report_age_ms=1000)
        assert isinstance(value, RequestSnapshot), 'BEHAVIOR: Screen must use the canonical immutable request pin'
        return value


def test_actual_screen_command_retains_canonical_normalized_value_and_private_editor_exclusion(tmp_path):
    rig = Rig(tmp_path)
    rig.accept()
    raw = rig.message()
    raw['interaction']['focus']['value'] = 'PRIVATE EDITOR DRAFT'
    raw['items'][0]['controls'].append({'id': 'notes:window-a', 'targetId': 'window-a',
        'role': 'textbox', 'editable': True, 'present': True, 'enabled': True,
        'observedValue': 'PRIVATE EDITOR DRAFT', 'text': 'PRIVATE EDITOR DRAFT'})
    result = asyncio.run(rig.ingest(raw))
    assert result['ok'] is True
    expected = normalize_observation(raw, client=REF, app_run_id=RUN, received_at=10)
    assert rig.observed().wire() == expected.wire()
    raw['items'][0]['rect'][0] = 999
    assert rig.observed().wire() == expected.wire()
    assert 'PRIVATE EDITOR DRAFT' not in json.dumps(rig.observed().wire())


@pytest.mark.parametrize('field,value,code', [
    ('workspaceId', 'other-workspace', 'CLIENT_MISMATCH'),
    ('clientId', 'other-client', 'CLIENT_MISMATCH'),
    ('pageEpoch', 'other-page', 'CLIENT_MISMATCH'),
    ('connectionEpoch', 'other-socket', 'CLIENT_MISMATCH'),
    ('appRunId', 'earlier-run', 'RUN_MISMATCH'),
    ('seq', 9, 'REPORT_REPLAYED'),
])
def test_actual_ingress_refuses_identity_or_sequence_change_without_overwriting_previous(tmp_path, field, value, code):
    rig = Rig(tmp_path)
    rig.accept()
    asyncio.run(rig.ingest())
    before = rig.observed().wire()
    bad = rig.message(seq=11)
    (bad['client'] if field in REF.wire() else bad)[field] = value
    result = asyncio.run(rig.ingest(bad))
    assert result.get('ok') is False and result.get('code') == code
    assert rig.observed().wire() == before


def test_raw_report_verified_flags_do_not_register_an_unaccepted_socket(tmp_path):
    rig = Rig(tmp_path)
    raw = rig.message()
    raw.update(verified=True, authenticated=True, accepted=True, sourceAuthority=True)
    result = asyncio.run(rig.ingest(raw))
    assert result.get('ok') is False, 'BEHAVIOR: raw flags cannot create accepted socket authority'
    assert result.get('code') == 'CLIENT_MISMATCH'
    assert not rig.screen.showing(), 'BEHAVIOR: refused ingress must not pollute the existing Screen store'


def test_equal_looking_later_client_cannot_replace_the_originating_clients_pin_or_target(tmp_path):
    rig = Rig(tmp_path)
    rig.accept()
    other_ref = ClientRef(REF.workspace_id, 'other-client', 'other-page', 'other-socket')
    rig.accept(rig.other, other_ref)
    asyncio.run(rig.ingest())
    pinned = rig.pin()
    rig.now = 11
    other = rig.message(seq=90, ref=other_ref)
    other['items'][0]['rect'][0] = 700
    other['items'][0]['propertyVersions']['opacity'] = 90
    asyncio.run(rig.ingest(other, rig.other))
    assert rig.observed().wire()['seq'] == 10
    assert rig.observed(rig.other).wire()['seq'] == 90
    guarded = seam(rig.screen, 'target')(pinned, 'target-a', properties=('opacity',))
    assert guarded.wire()['client'] == REF.wire()
    assert guarded.wire()['propertyVersions']['opacity'] == 8


def test_inline_send_pin_is_synchronous_and_cannot_be_retargeted_by_later_pointer_focus_or_page(tmp_path):
    rig = Rig(tmp_path)
    rig.accept()
    asyncio.run(rig.ingest())
    inline = rig.message(seq=11)
    inline['interaction']['pointer'].update(hitPath=['window-b'], targetCandidates=['target-b'])
    inline['interaction']['selected'] = ['target-b']
    pinned = rig.pin(inline=inline)
    assert pinned.wire()['observation']['seq'] == 11
    later = rig.message(seq=12)
    later['interaction']['pointer'].update(hitPath=['window-a'], targetCandidates=['target-a'])
    later['interaction']['focus'].update(controlId='send', role='button', editable=False)
    asyncio.run(rig.ingest(later))
    inline['interaction']['selected'].clear()
    guarded = seam(rig.screen, 'target')(pinned, 'target-b')
    assert guarded.wire()['presentationId'] == 'window-b'
    assert pinned.wire()['observation']['interaction']['selected'] == ['target-b']
    rig.screen.gone(rig.client)
    with pytest.raises(ObservationError) as refused:
        seam(rig.screen, 'target')(pinned, 'target-b')
    assert refused.value.code in {'CLIENT_MISMATCH', 'OBSERVATION_INCOMPLETE'}


def test_invalid_inline_send_does_not_fall_back_to_a_different_latest_pointer(tmp_path):
    rig = Rig(tmp_path)
    rig.accept()
    asyncio.run(rig.ingest())
    inline = rig.message(seq=11)
    inline['client']['pageEpoch'] = 'navigated-page'
    with pytest.raises(ObservationError) as refused:
        rig.pin(inline=inline)
    assert refused.value.code == 'CLIENT_MISMATCH'
    assert rig.observed().wire()['seq'] == 10


def test_partial_report_cannot_turn_omitted_original_target_into_observed_absence(tmp_path):
    rig = Rig(tmp_path)
    rig.accept()
    asyncio.run(rig.ingest())
    pinned = rig.pin()
    partial = rig.message(seq=11)
    partial.update(items=[], sources=[], targets=[], edges=[], complete=False,
                   omitted={'items': 2, 'sources': 2, 'targets': 2})
    partial['interaction'] = {'availability': 'partial', 'unknown': ['sender-truncated'],
                              'selected': [], 'contacts': []}
    assert asyncio.run(rig.ingest(partial))['ok']
    with pytest.raises(ObservationError) as refused:
        seam(rig.screen, 'target')(pinned, 'target-a')
    assert refused.value.code == 'OBSERVATION_INCOMPLETE'


def test_bound_keeps_current_registered_controls_style_payload_and_exact_source(tmp_path):
    rig = Rig(tmp_path)
    rig.accept(limits=Limits(items=1))
    raw = rig.message()
    raw['targets'] = []
    raw['interaction'] = {'availability': 'observed', 'unknown': [], 'contacts': [], 'selected': [],
        'focus': {'presentationId': 'window-b', 'controlId': 'opacity:window-b',
                  'role': 'slider', 'editable': False}}
    assert asyncio.run(rig.ingest(raw))['ok']
    observed = rig.observed().wire()
    assert [i['id'] for i in observed['items']] == ['window-b']
    actual = observed['items'][0]
    assert actual['controls'][1]['observedValue'] == 0.5
    assert actual['controls'][1]['stateRevision'] == 8
    assert actual['style'] == raw['items'][1]['style']
    assert actual['payloadBinding'] == raw['items'][1]['payloadBinding']
    assert next(s for s in observed['sources'] if s['key'] == 'source-b') == rig.sources['source-b']
    assert not observed['complete'] and observed['omitted']['items'] == 1


def test_projection_maps_page_epoch_once_and_controls_without_new_property_counters(tmp_path):
    rig = Rig(tmp_path)
    rig.accept()
    assert asyncio.run(rig.ingest())['ok']
    projected = seam(rig.screen, 'completion_report')(rig.client)
    assert projected['clientEpoch'] == REF.page_epoch
    assert projected['connectionEpoch'] == REF.connection_epoch
    assert projected['workspaceId'] == REF.workspace_id and projected['clientId'] == REF.client_id
    drawn = next(i for i in projected['items'] if i['id'] == 'window-a')
    assert drawn['sourceRef'] == rig.sources['source-a']
    assert drawn['controls']['opacity']['target'] == 'window-a'
    assert drawn['controls']['opacity']['stateRevision'] == 8
    assert drawn['propertyVersions']['opacity'] == 8 and drawn['opacity'] == 0.5
    assert drawn['phase'] == 'drawn' and drawn['drawRef'] == 'draw-a'


def test_raw_source_verified_flags_do_not_replace_exact_retained_store_authority(tmp_path):
    rig = Rig(tmp_path)
    rig.accept()
    raw = rig.message()
    raw['sources'][0]['identity']['resource']['id'] = 'nonexistent-retained-source'
    raw['sources'][0].update(verified=True, authority='server', retained=True)
    result = asyncio.run(rig.ingest(raw))
    assert result.get('ok') is False and result.get('code') == 'SOURCE_UNRESOLVED'
    assert not rig.screen.showing()


@pytest.mark.parametrize('timing', ['server-final-transcript', 'server-handoff-arrival', 'server-receive-legacy'])
def test_screen_pin_preserves_voice_age_and_distinct_fallback_timing(tmp_path, timing):
    rig = Rig(tmp_path)
    rig.accept()
    assert asyncio.run(rig.ingest())['ok']
    rig.now = 12
    original = rig.pin(timing=timing)
    assert original.wire()['timing'] == timing
    assert original.wire()['availability'] == 'stale' and original.wire()['reportAgeMs'] == 2000
    rig.now = 13
    assert asyncio.run(rig.ingest(rig.message(seq=11)))['ok']
    fresh = rig.pin(request_id='later-send')
    assert fresh.wire()['timing'] == 'client-send' and fresh.wire()['reportAgeMs'] == 0
    assert original.wire()['observation']['seq'] == 10 and original.wire()['reportAgeMs'] == 2000


async def pending_capture(rig):
    assert (await rig.ingest())['ok']
    pinned = rig.pin()
    task = asyncio.create_task(workspace_look(rig.screen)(snapshot=pinned, client=rig.client,
        request_id='synthetic-request', targets=('target-a',), memory=captures(rig.app.hub).memory, timeout=0.5))
    try:
        wire = await asyncio.wait_for(rig.client.control.get(), 0.1)
        asked = json.loads(wire) if isinstance(wire, str) else wire
        assert asked['type'] == 'screen-look'
        assert rig.other.control.empty(), 'BEHAVIOR: workspace capture must unicast to the originating connection'
        assert asked['originSnapshotId'] == pinned.snapshot_id
        return task, asked
    except BaseException:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        raise


def capture_reply(asked, data=None):
    data = jpeg() if data is None else data
    return {'type': 'screen-image', 'id': asked['id'], 'lookId': asked['id'],
        'requestId': 'synthetic-request', 'appRunId': RUN, 'client': REF.wire(),
        'originSnapshotId': asked['originSnapshotId'], 'capturedObservationId': 'synthetic-capture-now',
        'layoutRevisionBefore': 3, 'layoutRevisionAfter': 3, 'originalSize': [96, 64], 'inputSize': [96, 64],
        'workspaceToImage': [1, 0, 0, 1, 0, 0], 'captureTimeMonoMs': 7000, 'uncertaintyMs': 1,
        'captureMethod': 'one-shot-tab-track', 'surfaceProof': 'same-origin-page-handle',
        'fidelity': 'actual-tab-capture', 'coverage': 'complete', 'redactedRects': [], 'missing': [],
        'sha256': digest(data), 'jpeg': base64.b64encode(data).decode('ascii')}


async def cancel_capture(task):
    task.cancel()
    with suppress(asyncio.CancelledError, ToolFailed, ObservationError):
        await task


@pytest.mark.parametrize('bad,code', [
    ('sender', 'CLIENT_MISMATCH'), ('page', 'CLIENT_MISMATCH'), ('connection', 'CLIENT_MISMATCH'),
    ('run', 'CAPTURE_MISMATCH'), ('request', 'CAPTURE_MISMATCH'), ('origin', 'CAPTURE_MISMATCH'),
    ('layout', 'LAYOUT_CHANGED'), ('surface', 'CAPTURE_WRONG_SURFACE'),
])
def test_actual_capture_response_rejects_wrong_causal_fence_or_surface(tmp_path, bad, code):
    rig = Rig(tmp_path)
    rig.accept()

    async def go():
        task, asked = await pending_capture(rig)
        reply = capture_reply(asked)
        sender = rig.client
        if bad == 'sender': sender = rig.other
        elif bad == 'page': reply['client']['pageEpoch'] = 'other-page'
        elif bad == 'connection': reply['client']['connectionEpoch'] = 'other-socket'
        elif bad == 'run': reply['appRunId'] = 'old-run'
        elif bad == 'request': reply['requestId'] = 'unrelated-request'
        elif bad == 'origin': reply['originSnapshotId'] = 'later-send'
        elif bad == 'layout': reply['layoutRevisionAfter'] = 4
        else: rig.surface_allowed = False
        # Even perfectly spelled proof/verified labels cannot override trusted refusal.
        reply.update(verified=True, authorized=True, inspectConsent=True)
        try:
            result = await COMMANDS['screen-image'](rig.app, sender, reply)
            assert result.get('ok') is False and result.get('code') == code
            assert not (task.done() and not task.cancelled() and task.exception() is None and task.result() is not None)
        finally:
            await cancel_capture(task)
        assert captures(rig.app.hub).memory.reserved['analysis'] == 0
    asyncio.run(go())


@pytest.mark.parametrize('bad', ['invalid-jpeg', 'lying-size', 'lying-digest', 'oversized-header'])
def test_capture_bytes_header_decode_and_digest_are_checked_beyond_supplied_labels(tmp_path, bad):
    rig = Rig(tmp_path)
    rig.accept()

    async def go():
        task, asked = await pending_capture(rig)
        data = b'\xff\xd8not-a-decodable-jpeg' if bad == 'invalid-jpeg' else jpeg()
        if bad == 'oversized-header':
            # SOF dimensions rewritten without allocating/decompressing huge pixels.
            malformed = bytearray(data)
            marker = malformed.find(b'\xff\xc0')
            assert marker >= 0, 'synthetic encoded JPEG must contain a baseline SOF'
            malformed[marker + 5:marker + 9] = b'\xff\xff\xff\xff'
            data = bytes(malformed)
        reply = capture_reply(asked, data)
        if bad == 'lying-size': reply['originalSize'] = reply['inputSize'] = [48, 32]
        if bad == 'lying-digest': reply['sha256'] = '0' * 64
        try:
            result = await COMMANDS['screen-image'](rig.app, rig.client, reply)
            assert result.get('ok') is False, 'BEHAVIOR: trusted labels cannot certify mismatched/unsafe capture bytes'
            assert not (task.done() and not task.cancelled() and task.exception() is None and task.result() is not None)
        finally:
            await cancel_capture(task)
        assert captures(rig.app.hub).memory.reserved['analysis'] == 0
    asyncio.run(go())


def test_actual_capture_input_hashes_bytes_and_keeps_real_memory_lease_until_last_consumer(tmp_path):
    rig = Rig(tmp_path)
    rig.accept()

    async def go():
        task, asked = await pending_capture(rig)
        raw = jpeg()
        assert (await COMMANDS['screen-image'](rig.app, rig.client, capture_reply(asked, raw)))['ok']
        image = await task
        assert hasattr(image, 'jpeg') and hasattr(image, 'facts'), 'BEHAVIOR: acquisition returns leased image plus facts'
        facts = image.facts
        assert facts['channel'] == 'workspace' and facts['sha256'] == digest(raw)
        assert facts['inputSha256'] == digest(image.jpeg)
        assert facts['originalSize'] == [96, 64] and facts['inputSize'] == [96, 64]
        assert facts['fidelity'] == 'actual-tab-capture' and facts['originSnapshotId'] == asked['originSnapshotId']
        assert facts['captureTimeMonoMs'] == 7000 and rig.surface_checks
        memory = captures(rig.app.hub).memory
        assert memory.peak['analysis'] >= 96 * 64 * 12 + len(raw)
        assert memory.reserved['analysis'] >= len(image.jpeg) > 0
        del image, task
        gc.collect()
        assert memory.reserved['analysis'] == 0, 'BEHAVIOR: last consumer releases the actual capture allowance'
    asyncio.run(go())


def test_repeated_capture_for_one_request_reuses_one_pending_client_acquisition(tmp_path):
    rig = Rig(tmp_path)
    rig.accept()

    async def go():
        first, asked = await pending_capture(rig)
        second = asyncio.create_task(workspace_look(rig.screen)(snapshot=rig.pin(), client=rig.client,
            request_id='synthetic-request', targets=('target-a',), memory=captures(rig.app.hub).memory, timeout=0.5))
        try:
            await asyncio.sleep(0)  # allow the real second acquisition to register, not a wall-clock wait
            assert rig.client.control.empty(), 'BEHAVIOR: duplicate look cannot create another capture request'
            assert (await COMMANDS['screen-image'](rig.app, rig.client, capture_reply(asked)))['ok']
            a, b = await asyncio.gather(first, second)
            assert a is b, 'BEHAVIOR: one request shares the same leased workspace input'
            del a, b, first, second
            gc.collect()
        finally:
            for name in ('first', 'second'):
                if name in locals(): await cancel_capture(locals()[name])
        assert captures(rig.app.hub).memory.reserved['analysis'] == 0
    asyncio.run(go())


def test_capture_quota_refusal_and_cancel_release_the_actual_shared_allowance(tmp_path):
    rig = Rig(tmp_path)
    rig.accept()

    async def go():
        memory = captures(rig.app.hub).memory
        occupied = memory.reserve('analysis', memory.allowances['analysis'], 'synthetic occupied allowance')
        try:
            # Capture may refuse before sending; a reply never obtains unleased bytes.
            assert (await rig.ingest())['ok']
            with pytest.raises(Refused) as refused:
                await workspace_look(rig.screen)(snapshot=rig.pin(), client=rig.client, request_id='synthetic-request',
                    targets=('target-a',), memory=memory, timeout=0.01)
            assert 'room' in str(refused.value).lower() or 'memory' in str(refused.value).lower()
            assert memory.reserved['analysis'] == occupied.nbytes
        finally:
            occupied.release()
        task, _ = await pending_capture(rig)
        await cancel_capture(task)
        assert memory.reserved['analysis'] == 0
    asyncio.run(go())


def test_approximate_composition_is_limited_and_cannot_be_upgraded_by_actual_screen_flags(tmp_path):
    rig = Rig(tmp_path)
    rig.accept()

    async def go():
        task, asked = await pending_capture(rig)
        reply = capture_reply(asked)
        reply.update(fidelity='approximate-composition', captureMethod='approximate-dom-media-compositor',
            coverage='complete', missing=['cross-origin-frame', 'DOM-text'], surfaceProof=None,
            verified=True, actualScreen=True)
        try:
            assert (await COMMANDS['screen-image'](rig.app, rig.client, reply))['ok']
            image = await task
            assert image.facts['fidelity'] == 'approximate-composition'
            assert image.facts['coverage'] == 'partial'
            assert image.facts['missing'] == ['cross-origin-frame', 'DOM-text']
            assert image.facts.get('actualScreen') is not True
            del image, task
            gc.collect()
        finally:
            if 'task' in locals(): await cancel_capture(task)
        assert captures(rig.app.hub).memory.reserved['analysis'] == 0
    asyncio.run(go())
