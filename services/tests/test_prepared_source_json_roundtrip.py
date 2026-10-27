"""Actual prepared sources survive a cooperative browser's JSON number spelling.

Synthetic CPU camera clip and external Commons/Studio inputs; real App, motion
producer, reference tool, retained stores, byte leases and Screen report ingress.
The external roundtrip models JSON.parse/JSON.stringify for safe integral floats;
no browser, provider, native paint or malicious-client attestation is certified.
"""
import asyncio
import copy
import json
import time

import pytest

from orchestrator.commands import COMMANDS
from orchestrator.presentations import ResourceRef
from orchestrator.ticket import issue, using
from tests.test_native_motion_resource import measured
from tests.test_reference_photo import commons, image
from tests.test_workspace_actions_app import built, digest, plan, succeeded


def browser_json(value):
    """External JS number serialization within the exact safe-integer domain.

    Booleans remain booleans. Nonintegral values and all original fields remain;
    this is not the production source comparator or a source-authority adapter.
    """
    def numbers(node):
        if isinstance(node, dict):
            return {key: numbers(child) for key, child in node.items()}
        if isinstance(node, list):
            return [numbers(child) for child in node]
        if type(node) is float and node.is_integer() and abs(node) <= 2**53 - 1:
            return int(node)
        return node

    return json.loads(json.dumps(numbers(value), allow_nan=False))


async def prepared_pair(app, studio, commons):
    item, motion, raw = await measured(app, studio)
    commons.found = {'stoma microscope': ['https://synthetic-commons.test/photo']}
    commons.pictures = {'https://synthetic-commons.test/photo': image('Synthetic external reference')}
    runs, _ = await plan(app, [('reference_photo', {'subject': 'stoma', 'show': False,
                                                  'description': 'External reference, not this slide.'})])
    succeeded(runs[0])
    ref = runs[0].resource
    reference = app.hub.resources.get(ref['id'], ref['day'])
    assert reference.media == 'reference' and reference.id != motion.id
    results = app.hub.resources.results
    record = results.get(reference.id, reference.day)
    path = results.root / reference.day / record['pictures'][0]
    from tests.test_comparison_retention import until
    await until(lambda: path.is_file() and (reference.day, path.name) not in results._pending,
                'reference producer did not finish its actual retained write')
    reference_raw = path.read_bytes()
    ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
    ctx = app.workspace_context(studio, request_id=ticket.admission.request_id, owner='action:source-roundtrip')
    leases = []
    try:
        with using(ticket):
            for entry in (motion, reference):
                leases.append(await app.services.prepared_sources.prepare(
                    ResourceRef(entry.day, entry.id, entry.version, 0), ctx))
        assert app.services.prepared_sources.get(leases[0].token, ctx) == raw
        assert app.services.prepared_sources.get(leases[1].token, ctx) == reference_raw
        source = leases[0].source.to_wire()
        assert type(source['provenance']['producer']['configuration']['maxStepPx']) is float
        assert source['provenance']['producer']['configuration']['maxStepPx'] == 5.0
        assert type(source['provenance']['producer']['configuration']['secondsAsked']) is float
        assert source['provenance']['producer']['configuration']['secondsAsked'] == 3.0
        assert source['provenance']['view']['tracked'] is True
        return item, ticket, ctx, leases, (raw, reference_raw)
    except BaseException:
        for lease in leases:
            lease.release()
        ticket.close()
        raise


def report(app, studio, sources):
    wire = app.hub.screen.observation(studio).wire()
    wire['seq'] += 1
    wire['layoutRevision'] += 1
    wire['capturedMonoMs'] = time.monotonic() * 1000
    wire['sources'] = copy.deepcopy(sources)
    wire['totals']['sources'] = len(sources) + wire['omitted']['sources']
    return wire


def test_actual_motion_and_reference_source_grants_survive_browser_json_number_roundtrip(tmp_path, commons):
    app, studio = built(tmp_path)

    async def go():
        item, ticket, ctx, leases, payloads = await prepared_pair(app, studio, commons)
        try:
            originals = [lease.source.to_wire() for lease in leases]
            external = browser_json(report(app, studio, originals))
            configuration = external['sources'][0]['provenance']['producer']['configuration']
            assert type(configuration['maxStepPx']) is int and configuration['maxStepPx'] == 5
            assert type(configuration['secondsAsked']) is int and configuration['secondsAsked'] == 3
            assert external['sources'][0]['provenance']['view']['tracked'] is True
            result = await COMMANDS['screen'](app, studio, external)
            assert result['ok'] is True, result
            observed = app.hub.screen.observation(studio).wire()
            assert observed['seq'] == external['seq']
            # Authority restores the original complete producing facts, not a
            # dropped provenance projection or a different retained reference.
            assert json.dumps(observed["sources"], sort_keys=True) == json.dumps(originals, sort_keys=True)
            for lease, raw in zip(leases, payloads):
                assert app.services.prepared_sources.get(lease.token, ctx) == raw
                assert lease.source.to_wire()["digest"] == digest(raw)
            assert observed['sources'][0]['provenance']['frame'] == item.capture_id
            assert observed['sources'][1]['media'] == 'reference'
            assert app.services.magnifier.picture_now() is None
            assert not studio.store().list(studio.workspace_id)  # metadata is not window/paint proof
            assert ticket.admission.used == 0
        finally:
            for lease in leases:
                lease.release()
            ticket.close()

    asyncio.run(go())


@pytest.mark.parametrize('change', ['value', 'precision', 'bool-as-number', 'number-as-bool', 'missing-field', 'digest'])
def test_actual_source_change_refuses_atomically_without_displacing_reference_grant(tmp_path, commons, change):
    app, studio = built(tmp_path)

    async def go():
        _, ticket, ctx, leases, payloads = await prepared_pair(app, studio, commons)
        try:
            originals = [lease.source.to_wire() for lease in leases]
            admitted = await COMMANDS['screen'](app, studio, report(app, studio, originals))
            assert admitted['ok'] is True, admitted
            before = app.hub.screen.observation(studio)
            external = report(app, studio, originals)
            source = external['sources'][0]
            configuration = source['provenance']['producer']['configuration']
            if change == 'value':
                configuration['maxStepPx'] = 6.0
            elif change == 'precision':
                configuration['maxStepPx'] = 5.000000000000001
            elif change == 'bool-as-number':
                source['provenance']['view']['tracked'] = 1  # True == 1 in Python is not typed JSON identity
            elif change == 'number-as-bool':
                assert source['provenance']['view']['dx'] == 0.0
                source['provenance']['view']['dx'] = False
            elif change == 'missing-field':
                del configuration['secondsAsked']
            else:
                source['digest'] = '0' * 64
            result = await COMMANDS['screen'](app, studio, external)
            assert result['ok'] is False and result['code'] == 'SOURCE_UNRESOLVED', result
            assert app.hub.screen.observation(studio) is before
            assert before.wire()['sources'] == originals
            for lease, raw in zip(leases, payloads):
                assert app.services.prepared_sources.get(lease.token, ctx) == raw
            assert not studio.store().list(studio.workspace_id)
            assert ticket.admission.used == 0
        finally:
            for lease in leases:
                lease.release()
            ticket.close()

    asyncio.run(go())
