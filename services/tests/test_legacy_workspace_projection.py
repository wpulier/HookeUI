"""TEST-only accepted structural DOM reports; synthetic external renderer inputs.

Real App/Screen/normalizer/readers/change confirmation/showing ownership are used.
No browser paint, provider, native delivery or result-production success is faked.
The observed locator strings are not source grants; clocks/counters are declared
synthetic renderer observations, not server committed presentation versions.
"""
import asyncio
import copy

import pytest

from orchestrator.capture import RUN
from orchestrator.presentations import PresentationError, SourceRef
from orchestrator.screen import (
    magnifier_arrangement, magnifier_beside, magnifier_level, magnifier_source,
    open_state,
)
from orchestrator.showing import showings_of, stop
from orchestrator.ticket import issue, using
from orchestrator.workspace_observation import ObservationError
from tests.test_screen_workspace import REF, Rig


def structural_rig(tmp_path):
    rig = Rig(tmp_path)
    rig.app.accept_workspace(rig.client, REF)
    authority_calls = []

    def deny_source(client, supplied):
        # External trusted policy: no native source is registered for this slice.
        authority_calls.append((client, copy.deepcopy(supplied)))
        raise ObservationError('SOURCE_UNRESOLVED', 'synthetic structural-only policy')

    rig.screen.accept_client(rig.client, REF, app_run_id=RUN,
        source_resolver=deny_source, capture_authority=lambda client, pending, reply: False)
    return rig, authority_calls


def structural_item(id, kind, legacy, *, rect=None, generation=3, revision=7):
    return {
        'id': id, 'rootId': f'synthetic-dom:{id}', 'workspaceId': REF.workspace_id,
        'kind': kind, 'lifetimeGeneration': f'synthetic-born-root:{id}',
        'sourceGeneration': generation, 'revision': revision,
        'open': True, 'connected': True, 'visible': True,
        'rect': rect or [10, 20, 300, 200], 'z': 10,
        'space': 'workspace', 'view': {'legacy': {
            'schema': 'studio-surface-v1', 'scope': 'dom-observation', **legacy}},
        'propertyVersions': {}, 'propertyOwners': {}, 'controls': [],
        'load': {'state': 'unknown', 'unknown': ['native-source-unregistered']},
        'unknown': ['structural-observation-only'],
    }


def structural_report(rig, *, seq=10):
    raw = rig.message(seq=seq)
    resource = rig.sources['source-a']['identity']['resource']
    raw['items'] = [
        structural_item('live', 'live', {'opacity': 1.0}),
        structural_item('magnifier', 'magnifier', {
            'title': 'Synthetic stoma close-up', 'shows': 'o1',
            'source': '/synthetic/left.jpg', 'beside': '/synthetic/right.jpg',
            'level': 2.0, 'covers': False,
            'pictures': [{'source': '/synthetic/left.jpg', 'scale': 1.0},
                         {'source': '/synthetic/right.jpg', 'scale': 0.5}],
        }),
        structural_item('viewer', 'viewer', {
            'title': 'Synthetic researcher lesson', 'shows': 'r9', 'step': 'q2',
            'work': {'researcherId': 'r9', 'resource': {
                'day': resource['day'], 'id': resource['id'], 'version': None}},
        }),
        structural_item('layer:e7', 'population', {
            'title': 'Synthetic cell layer', 'shows': 'e7', 'opacity': 0.6,
            'placement': '0 of 160 verified in place',
            'style': {'colour': 'area', 'fill': True,
                'emphasis': {'members': ['c1', 'c2'], 'rest': 'fade'}},
        }),
    ]
    raw.update(sources=[], targets=[], edges=[], applied=[], user=[], commandResults=[],
               complete=False, omitted={'items': 5, 'sources': 0, 'targets': 0, 'edges': 0})
    raw['interaction'] = {
        'pointer': {'state': 'absent', 'modality': 'mouse', 'buttons': 0,
                    'hitPath': [], 'targetCandidates': []},
        'contacts': [],
        'focus': {'presentationId': None, 'controlId': None, 'role': 'none', 'editable': False},
        'lastContentPointer': None, 'lastContentFocus': None,
        'selected': [], 'availability': 'partial',
        'unknown': ['legacy-hit-registration-unavailable'],
    }
    return raw


def row(raw, id):
    return next(item for item in raw['items'] if item['id'] == id)


async def ingest(rig, raw):
    reply = await rig.ingest(raw)
    assert reply.get('ok') is True, reply


def assert_no_native_authority(rig, calls):
    wire = rig.observed().wire()
    assert calls == [], 'structural labels must not invoke a source resolver'
    assert wire['sources'] == [] and wire['commandResults'] == []
    assert wire['complete'] is False and wire['omitted']['items'] == 5
    for item in wire['items']:
        assert item.get('sourceKey') is None and item.get('sourceToken') is None
        assert item['propertyVersions'] == {} and item['propertyOwners'] == {}
        assert item['load']['state'] == 'unknown'
        assert item.get('liveSample') is None and item.get('viewDrawRef') is None
    for item in rig.screen.completion_report(rig.client)['items']:
        assert item.get('sourceRef') is None and item.get('drawRef') is None
        assert item.get('phase') == ('unknown' if item['open'] else 'closed')
        assert item.get('on_live') is None


def test_structural_report_admission_and_immutable_pin_do_not_need_a_native_source_grant(tmp_path):
    """Positive compatibility control reaches current actual accepted report/pin."""
    rig, calls = structural_rig(tmp_path)
    raw = structural_report(rig)
    asyncio.run(ingest(rig, raw))
    assert_no_native_authority(rig, calls)
    pinned = rig.pin(request_id='synthetic-source-free-control')
    raw['items'][1]['view']['legacy']['shows'] = 'later-unreported-o99'
    assert pinned.wire()['observation']['items'][1]['view']['legacy']['shows'] == 'o1'
    assert rig.observed().wire()['items'][1]['revision'] == 7


def test_structural_accepted_reports_feed_existing_magnifier_viewer_layer_readers_without_grants(tmp_path):
    rig, calls = structural_rig(tmp_path)
    asyncio.run(ingest(rig, structural_report(rig)))
    assert_no_native_authority(rig, calls)
    report = rig.screen.latest
    assert magnifier_source('/synthetic/left.jpg', 'o1')(report)
    assert magnifier_beside('/synthetic/right.jpg', 'o1')(report)
    assert magnifier_level(2.0, 'o1')(report)
    assert magnifier_arrangement('/synthetic/left.jpg', 'o1',
        ['/synthetic/left.jpg', '/synthetic/right.jpg'], '/synthetic/right.jpg')(report)
    assert open_state('viewer', True, 'r9')(report)
    assert rig.screen.item('viewer').get('step') == 'q2'
    layer = rig.screen.item('layer:e7')
    assert layer.get('shows') == 'e7'
    assert layer.get('style', {}).get('emphasis') == {'members': ['c1', 'c2'], 'rest': 'fade'}
    assert any('0 of 160 verified in place' in line for line in rig.screen.lines())
    pin = rig.pin(request_id='synthetic-structural-send')
    assert pin.wire()['observation']['items'][1]['view']['legacy']['shows'] == 'o1'
    context = rig.app.workspace_context(rig.client, request_id='synthetic-read', owner='synthetic-read')
    with pytest.raises(PresentationError):
        rig.app.services.prepared_sources.resolve(SourceRef.from_wire(rig.sources['source-a']), context=context)
    assert calls == []


def test_unknown_researcher_work_version_is_not_backfilled_and_retained_data_is_not_display(tmp_path):
    rig, calls = structural_rig(tmp_path)
    raw = structural_report(rig)
    resource = row(raw, 'viewer')['view']['legacy']['work']['resource']
    entry = rig.app.hub.resources.get(resource['id'], resource['day'])
    original = entry.picture_source(0)
    asyncio.run(ingest(rig, raw))
    work = rig.screen.item('viewer').get('work')
    assert work == {'researcherId': 'r9', 'resource': {
        'day': resource['day'], 'id': resource['id'], 'version': None}}
    closed = structural_report(rig, seq=11)
    row(closed, 'viewer').update(open=False, visible=False, revision=8)
    asyncio.run(ingest(rig, closed))
    assert open_state('viewer', False)(rig.screen.latest)
    assert rig.screen.item('viewer').get('work', {}).get('resource', {}).get('version') is None
    assert rig.app.hub.resources.get(entry.id, entry.day).picture_source(0) == original
    assert rig.app.hub.resources.picture(entry.id, 0, entry.day) is not None
    assert_no_native_authority(rig, calls)


def test_observed_structural_content_and_revision_advance_with_actual_external_content_change(tmp_path):
    rig, calls = structural_rig(tmp_path)
    asyncio.run(ingest(rig, structural_report(rig)))
    pinned = rig.pin(request_id='synthetic-before-content-change')
    later = structural_report(rig, seq=11)
    item = row(later, 'magnifier')
    item.update(sourceGeneration=4, revision=8)
    item['view']['legacy'].update(shows='o2', source='held', beside=None, pictures=[], level=3.0)
    asyncio.run(ingest(rig, later))
    assert rig.screen.item('magnifier')['sourceGeneration'] == 4
    assert rig.screen.item('magnifier')['revision'] == 8
    assert magnifier_source('held', 'o2')(rig.screen.latest)
    assert magnifier_level(3.0, 'o2')(rig.screen.latest)
    assert pinned.wire()['observation']['items'][1]['view']['legacy']['shows'] == 'o1'
    assert_no_native_authority(rig, calls)


def test_original_known_structural_change_then_user_move_prevents_conflicting_showing_restore(tmp_path):
    rig, calls = structural_rig(tmp_path)

    async def go():
        await ingest(rig, structural_report(rig))
        request = issue(rig.app.hub, 'turn', asked=rig.app.conversation.here(), channel='chat')
        try:
            with using(request):
                showing = showings_of(rig.app.hub).start('r9', 'Synthetic researcher finding', f'request:{request.id}')
                change = rig.screen.send('magnifier', 'magnifier', 'move', {'rect': [80, 90, 300, 200]}, showing.owner)
        finally:
            request.seal()
        confirmation = asyncio.create_task(rig.screen.confirm(change,
            lambda report: next((i for i in report['items'] if i['id'] == 'magnifier'), {}).get('rect') == [80, 90, 300, 200]))
        await asyncio.sleep(0)  # enter the existing real confirmation before external ACK
        try:
            applied = structural_report(rig, seq=11)
            row(applied, 'magnifier').update(rect=[80, 90, 300, 200], revision=8)
            applied['applied'] = [change.id]
            await ingest(rig, applied)
            assert await confirmation is True
        finally:
            if not confirmation.done():
                confirmation.cancel()
            await asyncio.gather(confirmation, return_exceptions=True)
        assert showings_of(rig.app.hub).noted(change, rig.screen) == 'owned'
        assert rig.screen.owner('magnifier', 'rect') == showing.owner
        user = structural_report(rig, seq=12)
        row(user, 'magnifier').update(rect=[180, 190, 300, 200], revision=9)
        user['user'] = [{'id': 'magnifier', 'prop': 'rect'}]
        await ingest(rig, user)
        assert rig.screen.owner('magnifier', 'rect') == 'user'
        assert rig.screen.owners['magnifier']['rect']['was'] == [80, 90, 300, 200]
        said = await stop(rig.app.services, why='asked')
        assert 'changed by you since' in said and 'Put back:' not in said
        assert rig.screen.item('magnifier')['rect'] == [180, 190, 300, 200]
        assert showings_of(rig.app.hub).current is None
        assert_no_native_authority(rig, calls)

    asyncio.run(go())


@pytest.mark.parametrize('change', ['unknown-key', 'wrong-scope', 'wrong-level', 'nonfinite-level',
    'title-bound', 'locator-bound', 'pane-bound', 'picture-key', 'wrong-work-version', 'style-key'])
def test_closed_legacy_projection_refuses_invalid_or_oversized_semantics_atomically(tmp_path, change):
    rig, calls = structural_rig(tmp_path)
    asyncio.run(ingest(rig, structural_report(rig)))
    before = rig.observed().wire()
    invalid = structural_report(rig, seq=11)
    legacy = row(invalid, 'magnifier')['view']['legacy']
    if change == 'unknown-key':
        legacy['verified'] = True
    elif change == 'wrong-scope':
        legacy['scope'] = 'native-committed-command'
    elif change == 'wrong-level':
        legacy['level'] = 0
    elif change == 'nonfinite-level':
        legacy['level'] = float('nan')
    elif change == 'title-bound':
        legacy['title'] = 'x' * 121
    elif change == 'locator-bound':
        legacy['source'] = '/' + 'x' * 2048
    elif change == 'pane-bound':
        legacy['pictures'].append({'source': '/synthetic/third.jpg', 'scale': 1.0})
    elif change == 'picture-key':
        legacy['pictures'][0]['sourceToken'] = 'fabricated-native-token'
    elif change == 'wrong-work-version':
        row(invalid, 'viewer')['view']['legacy']['work']['resource']['version'] = 'latest'
    else:
        row(invalid, 'layer:e7')['view']['legacy']['style']['privateDraft'] = 'PRIVATE EDITOR TEXT'
    reply = asyncio.run(rig.ingest(invalid))
    assert reply.get('ok') is False, f'unsupported legacy shape accepted: {change}'
    assert reply.get('code') in {'INVALID_REPORT', 'REPORT_TOO_LARGE'}
    assert rig.observed().wire() == before
    assert rig.screen.owner('magnifier', 'rect') is None
    assert calls == []


@pytest.mark.parametrize('change', ['frozen-content', 'generation-regression', 'revision-regression'])
def test_legacy_structural_revisions_cannot_hide_a_content_change_or_regress(tmp_path, change):
    rig, calls = structural_rig(tmp_path)
    asyncio.run(ingest(rig, structural_report(rig)))
    before = rig.observed().wire()
    invalid = structural_report(rig, seq=11)
    item = row(invalid, 'magnifier')
    if change == 'frozen-content':
        item['view']['legacy']['shows'] = 'o99'
    elif change == 'generation-regression':
        item.update(sourceGeneration=2, revision=8)
    else:
        item.update(sourceGeneration=4, revision=6)
    reply = asyncio.run(rig.ingest(invalid))
    assert reply.get('ok') is False
    assert rig.observed().wire() == before and calls == []


@pytest.mark.parametrize('change', ['missing-user-target', 'unchanged-user-claim'])
def test_a_user_claim_with_no_original_taken_is_left_unowned_and_the_report_kept(tmp_path, change):
    """The studio owns a drag against the last report it sent, which the server may not have taken (live 2026-10-29:
    "User change has no represented original structural property", and the report refused): the report is taken,
    and the claim earns no ownership."""
    rig, calls = structural_rig(tmp_path)
    asyncio.run(ingest(rig, structural_report(rig)))
    before = rig.observed().wire()
    report = structural_report(rig, seq=11)
    report['user'] = ([{'id': 'absent-legacy-root', 'prop': 'rect'}] if change == 'missing-user-target'
                      else [{'id': 'magnifier', 'prop': 'rect'}])
    reply = asyncio.run(rig.ingest(report))
    assert reply.get('ok') is True, reply
    assert rig.observed().wire()['seq'] == 11 > before['seq']
    assert rig.screen.owner('magnifier', 'rect') is None and rig.screen.owner('absent-legacy-root', 'rect') is None
    assert calls == []


@pytest.mark.parametrize('change', ['unknown-ack', 'native-ack',
    'unknown-user-property', 'native-source-token', 'native-draw-token'])
def test_structural_reports_cannot_mint_ack_ownership_source_or_draw_authority(tmp_path, change):
    rig, calls = structural_rig(tmp_path)
    asyncio.run(ingest(rig, structural_report(rig)))
    before = rig.observed().wire()
    invalid = structural_report(rig, seq=11)
    if change == 'unknown-ack':
        invalid['applied'] = ['c-not-issued-by-screen']
    elif change == 'native-ack':
        invalid['applied'] = ['native-command-id-is-not-a-legacy-change']
    elif change == 'missing-user-target':
        invalid['user'] = [{'id': 'absent-legacy-root', 'prop': 'rect'}]
    elif change == 'unknown-user-property':
        invalid['user'] = [{'id': 'magnifier', 'prop': 'sourceGrant'}]
    elif change == 'unchanged-user-claim':
        invalid['user'] = [{'id': 'magnifier', 'prop': 'rect'}]
    elif change == 'native-source-token':
        row(invalid, 'magnifier')['sourceToken'] = 'fabricated-native-token'
    else:
        row(invalid, 'magnifier')['load']['drawToken'] = 'fabricated-native-paint'
    reply = asyncio.run(rig.ingest(invalid))
    assert reply.get('ok') is False, f'structural fact acquired unearned authority: {change}'
    assert rig.observed().wire() == before
    assert rig.screen.owner('magnifier', 'rect') is None and calls == []


def test_known_original_change_ack_cannot_be_applied_after_its_exact_target_is_omitted(tmp_path):
    rig, calls = structural_rig(tmp_path)

    async def go():
        await ingest(rig, structural_report(rig))
        before = rig.observed().wire()
        change = rig.screen.send('magnifier', 'magnifier', 'move', {'rect': [80, 90, 300, 200]}, 'synthetic-owner')
        confirmation = asyncio.create_task(rig.screen.confirm(change,
            lambda report: open_state('magnifier', True)(report)))
        await asyncio.sleep(0)
        try:
            invalid = structural_report(rig, seq=11)
            invalid['items'] = [item for item in invalid['items'] if item['id'] != 'magnifier']
            invalid['omitted']['items'] += 1
            invalid['applied'] = [change.id]
            reply = await rig.ingest(invalid)
            assert reply.get('ok') is False, 'original Change does not authorize ACK without its exact target'
            assert rig.observed().wire() == before
            assert rig.screen.owner('magnifier', 'rect') is None and calls == []
        finally:
            if not confirmation.done():
                confirmation.cancel()
            await asyncio.gather(confirmation, return_exceptions=True)

    asyncio.run(go())


def emitted_structural_move(rig):
    """Read the actual original transport message; no ACK or completion is made here."""
    import json

    change = rig.screen.send('magnifier', 'magnifier', 'move',
        {'rect': [80, 90, 300, 200]}, 'synthetic-original-confirmation-owner')
    messages = []
    while not rig.client.control.empty():
        messages.append(json.loads(rig.client.control.get_nowait()))
    emitted = [message for message in messages if message.get('change') == change.id]
    assert emitted == [change.wire()], 'the original Change must really reach its accepted client transport'
    return change


async def admit_structural_move_ack(rig, change):
    """External synthetic renderer observation through actual accepted ingress/_notify."""
    applied = structural_report(rig, seq=11)
    row(applied, 'magnifier').update(rect=[80, 90, 300, 200], revision=8)
    applied['applied'] = [change.id]
    await ingest(rig, applied)
    assert rig.screen.item('magnifier')['rect'] == [80, 90, 300, 200]


def moved_structural_magnifier(report):
    return next((item for item in report['items'] if item['id'] == 'magnifier'), {}).get('rect') == [80, 90, 300, 200]


def test_canceling_original_confirmation_after_admitted_ack_cannot_be_reversed_by_joiner(tmp_path):
    rig, calls = structural_rig(tmp_path)

    async def go():
        await ingest(rig, structural_report(rig))
        change = emitted_structural_move(rig)
        owner = asyncio.create_task(rig.screen.confirm(change, moved_structural_magnifier))
        joiner = None
        try:
            await asyncio.sleep(0)  # the original public caller enters first
            joiner = asyncio.create_task(rig.screen.confirm(change, moved_structural_magnifier))
            await asyncio.sleep(0)  # the joining public caller also enters before the real ACK
            await admit_structural_move_ack(rig, change)
            assert owner.cancel(), 'cancel the original continuation before it resumes from the real ACK'
            canceled = await asyncio.gather(owner, return_exceptions=True)
            assert isinstance(canceled[0], asyncio.CancelledError)
            assert await joiner is False, 'a reader cannot overwrite the canceled original terminal result'
            assert await rig.screen.confirm(change, moved_structural_magnifier) is False
            assert rig.screen.owner('magnifier', 'rect') is None
            assert_no_native_authority(rig, calls)
        finally:
            tasks = [task for task in (owner, joiner) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(go())


@pytest.mark.parametrize('when', ['before-ack', 'after-ack'])
def test_canceling_joining_confirmation_does_not_cancel_or_poison_original(tmp_path, when):
    rig, calls = structural_rig(tmp_path)

    async def go():
        await ingest(rig, structural_report(rig))
        change = emitted_structural_move(rig)
        owner = asyncio.create_task(rig.screen.confirm(change, moved_structural_magnifier))
        joiner = None
        try:
            await asyncio.sleep(0)
            joiner = asyncio.create_task(rig.screen.confirm(change, moved_structural_magnifier))
            await asyncio.sleep(0)
            if when == 'after-ack':
                await admit_structural_move_ack(rig, change)
            assert joiner.cancel(), 'only the joining caller is canceled'
            canceled = await asyncio.gather(joiner, return_exceptions=True)
            assert isinstance(canceled[0], asyncio.CancelledError)
            assert not owner.cancelled(), 'joining cancellation cannot cancel the rightful original caller'
            if when == 'before-ack':
                assert not owner.done(), 'the original still awaits its actual accepted report'
                await admit_structural_move_ack(rig, change)
            assert await owner is True
            assert rig.screen.owner('magnifier', 'rect') == 'synthetic-original-confirmation-owner'
            ownership = copy.deepcopy(rig.screen.owners['magnifier']['rect'])
            assert await rig.screen.confirm(change, moved_structural_magnifier) is True
            assert rig.screen.owners['magnifier']['rect'] == ownership
            assert_no_native_authority(rig, calls)
        finally:
            tasks = [task for task in (owner, joiner) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(go())


def test_original_and_joining_confirmation_share_useful_success_and_repeated_read(tmp_path):
    rig, calls = structural_rig(tmp_path)

    async def go():
        await ingest(rig, structural_report(rig))
        change = emitted_structural_move(rig)
        owner = asyncio.create_task(rig.screen.confirm(change, moved_structural_magnifier))
        joiner = None
        try:
            await asyncio.sleep(0)
            joiner = asyncio.create_task(rig.screen.confirm(change, moved_structural_magnifier))
            await asyncio.sleep(0)
            await admit_structural_move_ack(rig, change)
            assert await owner is True and await joiner is True
            assert rig.screen.owner('magnifier', 'rect') == 'synthetic-original-confirmation-owner'
            ownership = copy.deepcopy(rig.screen.owners['magnifier']['rect'])
            assert ownership['was'] == [10, 20, 300, 200]
            assert await rig.screen.confirm(change, moved_structural_magnifier) is True
            assert rig.screen.owners['magnifier']['rect'] == ownership
            assert_no_native_authority(rig, calls)
        finally:
            tasks = [task for task in (owner, joiner) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(go())


def test_work_readiness_is_separate_from_selected_identity(tmp_path):
    rig, calls = structural_rig(tmp_path)
    raw = structural_report(rig)
    legacy = row(raw, 'viewer')['view']['legacy']
    legacy['workReady'] = False
    asyncio.run(ingest(rig, raw))
    original = rig.screen.item('viewer')['work']
    assert rig.screen.item('viewer')['workReady'] is False
    loaded = structural_report(rig, seq=11)
    row(loaded, 'viewer')['view']['legacy']['workReady'] = True
    row(loaded, 'viewer')['revision'] += 1
    asyncio.run(ingest(rig, loaded))
    assert rig.screen.item('viewer')['workReady'] is True
    assert rig.screen.item('viewer')['work'] == original
    assert_no_native_authority(rig, calls)


def test_made_today_show_cells_waits_for_the_studio_to_draw_the_layer_before_confirming(tmp_path):
    """Live 2026-10-11 16:22: Made today's "show cells" for e1, e5 and e6 were refused ("original studio has not
    represented this structural target"): the layer was shown and the change sent at once against the last report,
    which still had only the previous layer. The change waits for the studio to report the layer it shows."""
    import json
    from types import SimpleNamespace

    from orchestrator.capture import Capture
    from orchestrator.commands import COMMANDS
    from orchestrator.population import layer_of
    from tests.test_evidence import population
    from tests.test_workspace_actions_app import jpeg

    rig, calls = structural_rig(tmp_path)
    app = rig.app
    info = SimpleNamespace(id=7, width=160, height=120, epoch=0, dx=3.0, dy=4.0,
                           fov=None, disc=0.0, wall_at=1000.0, seq=11, boot='synthetic-boot')
    first, second = (population(app.hub.evidence, Capture(7, jpeg(), info), thread=app.day.active_id, n=2)
                     for _ in range(2))
    for p in (first, second):
        p.day = app.day.day
        app.hub.evidence.add(p)

    retained = {}

    def report(seq, shown, applied=()):
        rig.now += 0.01  # each synthetic renderer report arrives after the previous one
        raw = structural_report(rig, seq=seq)
        raw['items'] = [i for i in raw['items'] if i['kind'] != 'population'] + [structural_item(
            f'layer:{shown.id}', 'population', {'title': shown.title, 'shows': shown.id, 'opacity': 1.0})]
        raw['applied'] = list(applied)
        if retained:
            raw['items'] = [i for i in raw['items'] if i['kind'] != 'magnifier'] + [structural_item(
                'magnifier', 'magnifier', retained, generation=4, revision=8)]
        return raw

    async def studio_draws_then_applies():
        await asyncio.sleep(0.1)  # the studio draws the new layer and reports it a moment later
        await ingest(rig, report(12, second))
        seq = 12
        while True:  # the change, offered to this studio alone
            message = json.loads(await rig.client.control.get())
            if message.get('type') == 'magnifier' and message.get('open'):
                source = message['sources'][message['index']]
                url = source.get('url')
                assert url and url.startswith('/api/picture/')
                assert app.magnifier.pictures[url.rsplit('/', 1)[1]]
                retained.update(source=url, shows=message.get('objectId'), beside=None,
                                pictures=[{'source': url, 'scale': 1.0}])
                seq += 1
                await ingest(rig, report(seq, second))
            if message.get('type') == 'present' and message.get('id') == f'layer:{second.id}':
                seq += 1
                await ingest(rig, report(seq, second, [message['change']]))
                return

    async def go():
        layer_of(app.hub).show(first)
        await ingest(rig, report(11, first))
        studio = asyncio.create_task(studio_draws_then_applies())
        try:
            reply = await COMMANDS['present'](app, rig.client, {'type': 'present', 'what': second.id, 'do': 'show',
                                                                'requestId': 'made-today-show'})
        finally:
            studio.cancel()
            outcomes = await asyncio.gather(studio, return_exceptions=True)
        assert reply['ok'] is True, (reply, outcomes)
        assert layer_of(app.hub).shown == second.id
        assert retained, 'unverified live placement requires the exact retained picture to be shown'

    asyncio.run(go())


def test_note_document_context_is_truthful_and_pinned_without_native_authority(tmp_path):
    rig,calls=structural_rig(tmp_path)
    async def go():
        raw=structural_report(rig)
        doc={'project':'2026-10-16','tab':'main','placement':'p-note','resource':{'day':'2026-10-16','id':'a'+'f'*32,'version':2,'picture':None},'representation':'note','state':'draft-note','focused':True}
        raw['items'].append(structural_item('document:p-note','document',{'title':'Draft note','shows':doc['resource']['id'],'document':doc}))
        await ingest(rig,raw)
        pinned=rig.screen.pin(rig.client,request_id='note-context-check')
        changed=copy.deepcopy(raw);changed['seq']=11;row(changed,'document:p-note')['revision']+=1;row(changed,'document:p-note')['visible']=False;row(changed,'document:p-note')['view']['legacy']['document']['focused']=False
        await ingest(rig,changed)
        before=row(pinned.wire()['observation'],'document:p-note');after=row(rig.observed().wire(),'document:p-note')
        assert before['visible'] is True and after['visible'] is False
        assert before['view']['legacy']['document']['state']=='draft-note'
        assert before['load']['state']=='unknown' and calls==[]
    asyncio.run(go())
