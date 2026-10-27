"""TEST-only FP-C1 on exact e1eb4269: actual calibration and binary packet continuity.

Pixels/masks/bridge metadata/numeric inputs/legacy journal entries are SYNTHETIC.
App, intake, promoted outline, commands/Registry/Scale, packets and local saves
are real. Existing helpers are imported unchanged, never old test functions.
No Scale/FrameInfo/header/result replacement, safe-format fill, model, browser
or canonical source authority is supplied. Root owns first RED and execution.
"""
import asyncio
import copy
import json
import math
import struct

import pytest

from orchestrator.commands import handle
from orchestrator.day import DayStore
from orchestrator.paths import Paths
from orchestrator.population import promote
from orchestrator.scale import Reading, Scale, sizes
from tests.test_live_frame_packet_basis import actual_packet, frame_basis, parse_packet
from tests.test_live_member_producer_lineage import producer, synthetic_slide
from tests.waiting import HANG_S


def strict_header(raw):
    """Validate the original wire; never rewrite any number or build a substitute header."""
    parse_packet(raw)
    length = struct.unpack('>I', raw[4:8])[0]

    def nonstandard(token):
        raise ValueError(f'nonstandard JSON numeric token: {token}')

    try:
        header = json.loads(raw[8:8 + length], parse_constant=nonstandard)
    except ValueError as exc:
        raise AssertionError('MISSING_FEATURE: genuine frame header remains strict finite JSON') from exc

    def finite(value):
        if isinstance(value, bool) or value is None or isinstance(value, str):
            return
        if isinstance(value, (int, float)):
            assert math.isfinite(value), 'MISSING_FEATURE: every original header numeric leaf is finite'
        elif isinstance(value, dict):
            for child in value.values():
                finite(child)
        elif isinstance(value, list):
            for child in value:
                finite(child)
        else:
            raise AssertionError(f'unexpected parsed JSON value: {type(value).__name__}')
    finite(header)
    return header


async def calibrate_command(rig, target, literal, request_id):
    # External raw numeric text follows the server's actual JSON parser, including 1e309.
    text = ('{"type":"tool","name":"calibrate_scale","args":{"object":'
            + json.dumps(target.id) + ',"micrometres":' + literal + '},"requestId":'
            + json.dumps(request_id) + '}')
    message = json.loads(text)
    await asyncio.wait_for(handle(rig.app, rig.client, message), HANG_S)
    replies = []
    while not rig.client.control.empty():
        row = json.loads(rig.client.control.get_nowait())
        if row.get('type') == 'result' and row.get('requestId') == request_id:
            replies.append(row)
    assert len(replies) == 1 and replies[0]['for'] == 'tool'
    return message, replies[0]


async def useful_prior(rig):
    assert rig.hub.settings.update({'seen': 'off', 'taught': 'off'}) == []
    pixels, boxes = synthetic_slide()
    original = await rig.receive(pixels, warm=True)
    original_raw, original_header, _ = actual_packet(rig, original, pixels)
    strict_header(original_raw)
    pop = await rig.population(original, boxes)
    chosen = pop.live()[0]
    made = await promote(rig.hub, pop, [chosen.id], label='')
    assert len(made) == 1
    target = made[0]
    assert rig.hub.scene.objects[target.id] is target and target.polygons
    assert target.origin == original.id and target.made_by['member'] == chosen.id
    assert target.made_by['evidence'] == pop.id
    width = sizes(target.polygons, Reading(1.0, 1.0, 10, True))['lengthUm']
    assert math.isfinite(width) and width > 2
    _, reply = await calibrate_command(rig, target, '50', 'finite-prior')
    assert reply['ok'] is True and 'calibrated at 10x' in reply['summary']
    valid = await rig.receive(pixels)
    valid_raw, valid_header, _ = actual_packet(rig, valid, pixels)
    strict_header(valid_raw)
    frame_basis(valid_header, valid)
    assert valid.scale['measured'] is True and math.isfinite(valid.scale['umPerPx'])
    assert valid.scale['umPerPx'] > 0 and valid.scale['optics']['calibrated']['10']['lengthUm'] == 50
    assert pop.path is not None and pop.capture is None
    return {'pixels': pixels, 'target': target, 'width': width, 'population': pop,
            'original': original, 'original_scale': copy.deepcopy(original.scale),
            'original_raw': original_raw, 'original_header': original_header,
            'valid': valid, 'valid_scale': copy.deepcopy(valid.scale),
            'valid_raw': valid_raw, 'valid_header': valid_header,
            'saved_jpeg': (pop.path / 'capture.jpg').read_bytes(),
            'saved_manifest': (pop.path / 'manifest.json').read_bytes(),
            'facts': copy.deepcopy(rig.hub.scale.facts()),
            'calibrated': copy.deepcopy(rig.hub.scale.calibrated),
            'calibrations': copy.deepcopy(rig.hub.scale.calibrations),
            'preference': copy.deepcopy(rig.app.day.prefs['scale'])}


def original_sources(rig, prior):
    original, valid, pop = prior['original'], prior['valid'], prior['population']
    assert original.scale == prior['original_scale'] and valid.scale == prior['valid_scale']
    assert parse_packet(prior['original_raw']) == (prior['original_header'], original.jpeg)
    assert parse_packet(prior['valid_raw']) == (prior['valid_header'], valid.jpeg)
    assert (pop.path / 'capture.jpg').read_bytes() == prior['saved_jpeg'] == original.jpeg
    assert (pop.path / 'manifest.json').read_bytes() == prior['saved_manifest']
    assert rig.hub.evidence.capture_jpeg(pop) == original.jpeg
    assert pop.scale_id == prior['original_scale']


async def continued_packets(rig, pixels, after_id):
    observed = []
    for _ in range(2):
        held = await rig.receive(pixels)
        raw, header, jpeg = actual_packet(rig, held, pixels)
        assert held.id > after_id and jpeg == held.jpeg
        after_id = held.id
        observed.append((held, raw, header))
    assert observed[1][0].seq > observed[0][0].seq
    return observed


def unchanged_scale(rig, prior):
    assert rig.hub.scale.facts() == prior['facts']
    assert rig.hub.scale.calibrated == prior['calibrated']
    assert rig.hub.scale.calibrations == prior['calibrations']
    assert rig.app.day.prefs['scale'] == prior['preference']


@pytest.mark.parametrize('literal', ['1e309', '1e308'], ids=['json-overflow', 'derived-overflow'])
def test_actual_command_overflow_rejects_without_changing_valid_scale_or_stopping_packets(tmp_path, literal):
    async def exercise():
        async with producer(tmp_path) as rig:
            prior = await useful_prior(rig)
            radius = rig.hub.latest_info.fov.radius
            value = json.loads(literal)
            assert radius > 0 and math.isfinite(radius)
            if literal == '1e309':
                assert math.isinf(value)
            else:
                assert math.isfinite(value) and value > 0
                assert math.isinf(value / prior['width'] * 2 * radius), 'the actual outline/FOV must exercise derived overflow'
            message, reply = await calibrate_command(rig, prior['target'], literal, 'overflow-probe')
            assert message['args']['micrometres'] == value
            # Receive actual JPEGs BEFORE the missing-feature rejection assertion, including on RED.
            packets = await continued_packets(rig, prior['pixels'], prior['valid'].id)
            original_sources(rig, prior)
            assert reply['ok'] is False, 'MISSING_FEATURE: actual calibration command rejects invalid input before committing'
            assert reply.get('error') and 'something went wrong' not in reply['error']
            unchanged_scale(rig, prior)
            for held, raw, header in packets:
                strict_header(raw)
                frame_basis(header, held)
                assert held.scale['measured'] is True and held.scale['umPerPx'] > 0
                assert held.scale['optics']['calibrated']['10'] == prior['valid_scale']['optics']['calibrated']['10']
    asyncio.run(exercise())


@pytest.mark.parametrize('dimension,value', [('length_px', math.nan), ('length_px', math.inf),
                                             ('disc_radius_px', math.nan), ('disc_radius_px', math.inf)],
                         ids=['length-nan', 'length-infinity', 'radius-nan', 'radius-infinity'])
def test_scale_boundary_rejects_nonfinite_dimensions_before_mutation_and_continues_intake(tmp_path, dimension, value):
    async def exercise():
        async with producer(tmp_path) as rig:
            prior = await useful_prior(rig)
            args = {'length_px': prior['width'], 'length_um': 50.0,
                    'disc_radius_px': rig.hub.latest_info.fov.radius}
            assert all(math.isfinite(v) and v > 0 for v in args.values())
            args[dimension] = value  # external Scale API input; no Scale/FrameInfo replacement
            refused = None
            try:
                rig.hub.scale.calibrate(**args)
            except ValueError as exc:
                refused = exc
            packets = await continued_packets(rig, prior['pixels'], prior['valid'].id)
            original_sources(rig, prior)
            assert refused is not None, 'MISSING_FEATURE: Scale rejects nonfinite dimensions before mutation'
            assert str(refused)
            unchanged_scale(rig, prior)
            for held, raw, header in packets:
                strict_header(raw)
                frame_basis(header, held)
                assert held.scale['measured'] is True and math.isfinite(held.scale['umPerPx'])
    asyncio.run(exercise())


@pytest.mark.parametrize('corrupt', ['width-infinity', 'width-nan', 'length-provenance-infinity', 'zero-width'])
def test_real_legacy_invalid_calibration_is_diagnosed_unavailable_while_original_pixels_continue(tmp_path, corrupt):
    async def exercise():
        async with producer(tmp_path) as rig:
            prior = await useful_prior(rig)
        state = copy.deepcopy(prior['preference'])
        if corrupt == 'length-provenance-infinity':
            state['calibrations']['10']['lengthUm'] = math.inf
            fields = ['lengthUm']
        else:
            state['calibrated']['10'] = {'width-infinity': math.inf, 'width-nan': math.nan, 'zero-width': 0.0}[corrupt]
            fields = ['fieldUm']
        day = DayStore(root=Paths.under(tmp_path).sessions)
        day.set_pref('scale', state)  # real corrupt legacy input, retained by the actual append-only journal
        kept = day.user_prefs.path  # the user's preferences journal (day/prefs.py), where the scale is kept
        legacy_bytes = kept.read_bytes()
        if corrupt != 'zero-width':
            assert b'Infinity' in legacy_bytes or b'NaN' in legacy_bytes
        async with producer(tmp_path) as recovered:
            first = await recovered.receive(prior['pixels'], warm=True)
            raw, header, _ = actual_packet(recovered, first, prior['pixels'])
            packets = [(first, raw, header)]
            packets.extend(await continued_packets(recovered, prior['pixels'], first.id))
            assert kept.read_bytes().startswith(legacy_bytes)
            original_sources(recovered, prior)
            assert recovered.hub.evidence.read_back() == [prior['population'].id]
            assert recovered.hub.evidence.problems == []
            back = recovered.hub.evidence.get(prior['population'].id)
            assert back.capture is None and back.scale_id == prior['original_scale']
            assert back.view == prior['population'].view and recovered.hub.evidence.capture_jpeg(back) == prior['saved_jpeg']
            for held, raw, header in packets:
                # Actual source/JPEG/save/raw journal checks above precede strict-finite policy.
                strict_header(raw)
                basis = frame_basis(header, held)
                arm = held.scale['optics']['calibrated']['10']
                assert arm.get('unavailable') == {'reason': 'invalid-calibration', 'fields': fields}, \
                    'MISSING_FEATURE: invalid active calibration has its actual finite unavailable diagnostic'
                for field in fields:
                    if corrupt != 'zero-width':
                        assert arm[field] is None, 'nonfinite known numeric facts are unavailable, never invented finite values'
                for field in ('lengthPx', 'discRadiusPx', 'at'):
                    assert arm[field] == state['calibrations']['10'][field]
                if corrupt == 'length-provenance-infinity':
                    assert arm['fieldUm'] == state['calibrated']['10']
                assert basis['objective'] == 10 and basis['umPerPx'] is None
                assert 'umPerPx' not in held.scale and 'measured' not in held.scale
                assert recovered.hub.scale.reading(held.fov.radius) is None
                assert Scale.reading_from(recovered.hub.scale.facts(current_only=True), held.disc or held.fov.radius) is None
                wire = recovered.hub.scale.wire(held.fov.radius)
                assert wire['umPerPx'] is None and wire['fieldUm'] is None and wire['source'] is None
                assert 10 not in wire['measured']
                json.dumps(wire, allow_nan=False)
    asyncio.run(exercise())


@pytest.mark.parametrize('compatible', ['command', 'legacy-missing-provenance'])
def test_useful_finite_command_and_genuinely_missing_legacy_provenance_remain_available(tmp_path, compatible):
    async def exercise():
        if compatible == 'legacy-missing-provenance':
            DayStore(root=Paths.under(tmp_path).sessions).set_pref('scale', {
                'objective': 10, 'fieldNumber': 18, 'calibrated': {'10': 900.0}, 'calibrations': {}})
        async with producer(tmp_path) as rig:
            if compatible == 'command':
                prior = await useful_prior(rig)
                _, reply = await calibrate_command(rig, prior['target'], '75', 'finite-useful')
                assert reply['ok'] is True and 'calibrated at 10x' in reply['summary']
                packets = await continued_packets(rig, prior['pixels'], prior['valid'].id)
                original_sources(rig, prior)
            else:
                pixels, _ = synthetic_slide()
                held = await rig.receive(pixels, warm=True)
                raw, header, _ = actual_packet(rig, held, pixels)
                packets = [(held, raw, header)]
            for held, raw, header in packets:
                strict_header(raw)
                basis = frame_basis(header, held)
                assert held.scale['measured'] is True and basis['umPerPx'] > 0
                arm = basis['scale']['optics']['calibrated']['10']
                assert 'unavailable' not in arm
                if compatible == 'command':
                    assert arm['lengthUm'] == 75 and arm['lengthPx'] == round(prior['width'], 2)
                else:
                    assert arm['fieldUm'] == 900 and held.scale['calibratedAt'] is None
                    assert all(arm.get(key) is None for key in ('lengthUm', 'lengthPx', 'discRadiusPx', 'at'))
                    assert held.scale['fieldNumberSource'] == 'unknown'
    asyncio.run(exercise())
