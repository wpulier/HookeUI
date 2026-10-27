"""Real CPU operation through Actions and retained evidence; no model/provider stand-ins used."""
import asyncio

import cv2
import numpy as np
import pytest

from orchestrator import population
from orchestrator.commands import handle
from tests.test_original_picture_analysis import prepare
from tests.test_sources import Client
from tests.waiting import until_async


def test_local_action_retains_exact_bright_region_masks_and_measurements(tmp_path, monkeypatch):
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    # Two straight 20-pixel bright edge bands; the symmetric blur/threshold preserves both boundaries.
    pixels = np.zeros((96, 96), np.uint8)
    pixels[:, :20] = 255
    pixels[:, 76:] = 255
    cv2.imwrite(str(image), pixels)
    monkeypatch.setenv('MICROMIND_MODEL_FILES', 'real')
    import socket
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: (_ for _ in ()).throw(AssertionError('network request')))
    defaults = built.hub.settings.cells

    async def go():
        await handle(built, Client(), {'type': 'source', 'open': aid})
        client = Client()
        await handle(built, client, {'type': 'action', 'capability': 'cells', 'args': {},
                                    'use': {'cells': 'basic-bright-objects'}})
        assert client.replies[-1]['ok'], client.replies[-1]
        result = population.evidence_of(built.hub).get(population.layer_of(built.hub).shown)
        assert result is not None
        from orchestrator.operations import OPERATIONS
        implementation = next(i for i in OPERATIONS['cells'].implementations if i.id == 'basic-bright-objects')
        assert result.producer['version'] == implementation.version
        assert len(result.live()) == 2
        assert sorted(result.value(m.id, 'areaPx') for m in result.live()) == [1920, 1920]
        assert sorted(result.value(m.id, 'centroid') for m in result.live()) == [[10., 48.], [86., 48.]]
        assert all(result.value(m.id, 'intensity') == 255 for m in result.live())
        assert 'Bright regions' in result.title
        assert 'bright regions' in result.count_words()
        assert not worker.inputs
        assert result.scale_id['units'] == 'pixels'
        assert built.hub.settings.cells == defaults
    asyncio.run(go())


def test_example_bytes_and_pixel_grid_are_preserved_without_a_network(tmp_path, monkeypatch):
    import hashlib
    from orchestrator import examples
    original = examples.human_mitosis()
    preview = examples.human_mitosis(preview=True)
    assert hashlib.sha256(original.read_bytes()).hexdigest() == '2751ba667c4067c5d30817cff004aa06f6f6287f1cdbb5b8c9c6a500308cb456'
    for path in (original, preview):
        pixels = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        assert pixels.shape == (512, 512) and pixels.dtype == np.uint8
        assert hashlib.sha256(pixels.tobytes()).hexdigest() == '868c3327e26eb8e1d20fbf07c85713e9e868eba962419877bdb454bfc3418403'
    import shutil
    shutil.copytree(examples.ROOT, tmp_path / 'examples')
    monkeypatch.setattr(examples, 'ROOT', tmp_path / 'examples')
    (examples.ROOT / 'human_mitosis.png').write_bytes(b'damaged')
    import pytest
    with pytest.raises(ValueError, match='damaged'):
        examples.human_mitosis(preview=True)


def test_native_holes_survive_saved_evidence_and_measurement_tables(tmp_path, monkeypatch):
    from orchestrator.capture import captures
    from orchestrator.tables import population_table
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    pixels = np.zeros((96, 96), np.uint8)
    pixels[10:86, 10:86] = 255
    pixels[30:66, 30:66] = 0
    cv2.imwrite(str(image), pixels)

    async def go():
        await handle(built, Client(), {'type': 'source', 'open': aid})
        result = await population.segment(built.hub, choice='basic-bright-objects', policy='raw')
        assert len(result.live()) == 1
        member = result.live()[0]
        assert result.value(member.id, 'holeAreaPx') > 1000
        table = population_table(result)
        assert table['memberLabel'] == 'bright region'
        assert table['rows'][0]['areaPx'] == result.value(member.id, 'areaPx')
        await until_async(lambda: result.status == 'saved')
        assert result.status == 'saved'
        image.unlink()
        capture = population.capture_of(built.hub, result)
        with captures(built.hub).hold(capture, 'analysis', 'hold', 'checking original pixels'):
            assert np.array_equal(capture.gray(), pixels)
        assert not worker.inputs
    asyncio.run(go())


def test_unsupported_original_depth_is_refused_instead_of_downconverted(tmp_path, monkeypatch):
    import pytest
    from orchestrator.brain.registry import ToolFailed
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    cv2.imwrite(str(image), np.full((96, 96), 30000, np.uint16))
    async def go():
        await handle(built, Client(), {'type': 'source', 'open': aid})
        with pytest.raises(ToolFailed, match='depth or channels are unsupported'):
            await population.segment(built.hub, choice='basic-bright-objects', policy='raw')
        assert not worker.inputs
        assert not population.evidence_of(built.hub).items
    asyncio.run(go())


def test_human_mitosis_operation_and_export_match_independent_reference(tmp_path, monkeypatch):
    import csv
    import hashlib
    import io
    import json
    from orchestrator import examples
    from tests.bright_objects_reference import canonical, reference
    from tests.test_particle_exchange import download, unpack
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    raw = examples.human_mitosis(preview=True).read_bytes()
    image.write_bytes(raw)
    gray = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
    expected_threshold, expected_labels, expected_rows = reference(gray)

    async def go():
        await handle(built, Client(), {'type': 'source', 'open': aid})
        result = await population.segment(built.hub, choice='basic-bright-objects', policy='raw')
        await until_async(lambda: result.status == 'saved')
        assert result.status == 'saved'
        files = unpack(await download(built, result))
        labels = cv2.imdecode(np.frombuffer(files['labels.tif'], np.uint8), cv2.IMREAD_UNCHANGED)
        assert np.array_equal(canonical(labels), expected_labels)
        assert result.producer['threshold'] == expected_threshold
        assert files['source.png'] == raw
        rows = list(csv.DictReader(io.StringIO(files['measurements.csv'].decode())))
        actual = sorted((int(r['areaPx2']), float(r['centroidX']), float(r['centroidY']), float(r['meanIntensity'])) for r in rows)
        np.testing.assert_allclose(actual, sorted(expected_rows), atol=1e-6, rtol=0)
        manifest = json.loads(files['manifest.json'])
        assert manifest['producer']['implementation'] == 'basic-bright-objects'
        assert manifest['source']['pixelSha256'] == '868c3327e26eb8e1d20fbf07c85713e9e868eba962419877bdb454bfc3418403'
        assert manifest['producer']['input']['pixelsSha256'] == manifest['source']['pixelSha256']
        assert not worker.inputs
        print('Independent human-mitosis reference:', {'threshold': expected_threshold, 'regions': len(expected_rows),
              'labelsSha256': hashlib.sha256(expected_labels.tobytes()).hexdigest(),
              'measurementSha256': hashlib.sha256(json.dumps(expected_rows).encode()).hexdigest()})
    asyncio.run(go())


def test_rgb_original_uses_grayscale_brightness_without_weights_or_gpu(tmp_path, monkeypatch):
    from orchestrator import operations, readiness
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    # BGR red band: OpenCV luminance is 76; no resizing or biological interpretation.
    pixels = np.zeros((96, 96, 3), np.uint8)
    pixels[:, :20, 2] = 255
    cv2.imwrite(str(image), pixels)
    built.hub.gpu = built.hub.gpu_heavy = None
    monkeypatch.setenv('MICROMIND_MODEL_FILES', 'real')
    from orchestrator.model_checks import entries
    assert any(e.key == 'cells:basic-bright-objects' for e in entries())
    assert not operations.availability(built.hub, operations.CELLS.impl('basic-bright-objects'))
    assert readiness.model('basic-bright-objects', {}).status == 'ready'
    async def go():
        await handle(built, Client(), {'type': 'source', 'open': aid})
        result = await population.segment(built.hub, choice='basic-bright-objects', policy='raw')
        assert len(result.live()) == 1
        member = result.live()[0]
        assert result.value(member.id, 'areaPx') == 1920
        assert result.value(member.id, 'intensity') == 76
        assert result.value(member.id, 'centroid') == [10., 48.]
        assert not worker.inputs
    asyncio.run(go())


def test_empty_picture_is_a_successful_empty_result(tmp_path, monkeypatch):
    from orchestrator.operations import runner
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    cv2.imwrite(str(image), np.zeros((96, 96), np.uint8))
    async def go():
        await handle(built, Client(), {'type': 'source', 'open': aid})
        result = await population.segment(built.hub, choice='basic-bright-objects', policy='raw')
        assert result.live() == []
        execution = runner(built.hub).get(result.made_by['execution'])
        assert execution.executed == 'basic-bright-objects'
        assert '0 bright regions' in result.count_words()
        assert not worker.inputs
    asyncio.run(go())


@pytest.mark.parametrize('stage', ['its still', 'another picture'])
def test_a_hand_run_on_the_stage_picture_says_where_its_outlines_are_in_words_for_a_person(tmp_path, monkeypatch, stage):
    """S6, with S5a: a run on the still the Stage shows is drawn on that picture once the Stage's picture is checked,
    and says so; one whose picture is no longer the Stage's by the time it is shown is kept on the original image, not
    drawn, and never says "on the view now" or "the layer is on". Started by hand, it names no evidence id or tool."""
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    pixels = np.zeros((96, 96), np.uint8)
    pixels[:, :20] = 255
    pixels[:, 76:] = 255
    cv2.imwrite(str(image), pixels)
    monkeypatch.setenv('MICROMIND_MODEL_FILES', 'real')
    if stage == 'another picture':
        monkeypatch.setattr(population, 'on_stage', lambda spec, source: False)

    async def go():
        await handle(built, Client(), {'type': 'source', 'open': aid})
        client = Client()
        await handle(built, client, {'type': 'action', 'capability': 'catalog:basic-bright-objects', 'args': {}})
        reply = client.replies[-1]
        assert reply['ok'], reply
        shown = population.evidence_of(built.hub).get(population.layer_of(built.hub).shown)
        assert shown is not None
        if stage == 'its still':
            assert reply['summary'] == ('Found 2 outlines on the picture on the Stage; drawn on it once it is checked '
                                        'there, and kept in Data.')
            assert population.drawn_on_stage(built.hub, shown)
        else:
            assert reply['summary'] == 'Found 2 outlines on the original image; kept in Data. Open the saved result to see them.'
            assert not population.drawn_on_stage(built.hub, shown)
        for words in ('view now', 'layer is on', shown.id, 'show_measurements', 'Basic local method'):
            assert words not in reply['summary']
    asyncio.run(go())
