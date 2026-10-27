"""Saved evidence → ordinary HTTP download → independent file decoders; Fiji transport is a stand-in."""
import asyncio
import csv
import io
import json
import zipfile

import cv2
import httpx
import numpy as np

from orchestrator.commands import handle
from orchestrator.server import create_app
from tests.test_fiji_particle_journey import journey  # noqa: F401
from tests.test_particle_corrections import analyzed, saved
from tests.test_sources import Client
from tests.waiting import HANG_S


async def download(built, result):
    web = create_app('http://127.0.0.1:1', use_gpu=False, paths=built.paths)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1') as client:
        return await client.get(f'/api/resources/{result.id}/exchange', params={'session': built.day.sid, 'revision': result.revision})


def unpack(response):
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_corrected_saved_population_downloads_original_pixels_labels_and_full_provenance(journey):
    built, aid, image = journey

    async def go():
        result = await analyzed(built, aid)
        rectangle = next(m.id for m in result.live() if m.box[0] == 70)
        await handle(built, Client(), {'type': 'population-correct', 'session': built.day.sid,
            'population': result.id, 'revision': 0, 'change': 'remove', 'member': rectangle})
        await saved(result)
        files = unpack(await download(built, result))
        assert set(files) == {'source.png', 'labels.tif', 'measurements.csv', 'manifest.json'}
        assert files['source.png'] == image.read_bytes()
        labels = cv2.imdecode(np.frombuffer(files['labels.tif'], np.uint8), cv2.IMREAD_UNCHANGED)
        assert labels.dtype == np.uint16 and labels.shape == (96, 96)
        assert set(np.unique(labels)) == {0, 1, 2}
        assert labels[25, 15:36].tolist() == [1] * 21
        assert labels[60, 58:73].tolist() == [2] * 15
        assert not np.any(labels[10:24, 70:87])
        rows = list(csv.DictReader(io.StringIO(files['measurements.csv'].decode())))
        assert [r['memberID'] for r in rows] == [m.id for m in result.live()]
        assert [int(r['areaPx2']) for r in rows] == [317, 149]
        assert [float(r['centroidX']) for r in rows] == [25.5, 65.5]
        manifest = json.loads(files['manifest.json'])
        assert manifest['evidence'] == result.id and manifest['revision'] == 1
        assert manifest['session'] == result.session
        assert manifest['execution'] == result.made_by['execution']
        assert manifest['producer'] == result.producer
        assert manifest['source']['sha256'] == 'af1b6c90a4dc0ca7daadd18301e916c9e2645fe75e40bb04c3f193c0505f055f'
        assert manifest['definitions']['centroidX']['unit'] == 'px'
    asyncio.run(go())


def rewrite_masks(result, edit):
    """A malformed external retained file with its checksum updated: structural validation must still refuse it."""
    import hashlib
    path = result.path / 'masks.npz'
    with np.load(path) as archive:
        arrays = {name: archive[name] for name in archive.files}
    edit(arrays)
    np.savez_compressed(path, **arrays)
    manifest = json.loads((result.path / 'manifest.json').read_text())
    manifest['files']['masks.npz'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (result.path / 'manifest.json').write_text(json.dumps(manifest))


import pytest

@pytest.mark.parametrize('damage', ['missing', 'fractional', 'scale', 'bounds', 'overlap', 'empty', 'truncated'])
def test_incomplete_or_unrepresentable_retained_masks_refuse_download(journey, damage):
    built, aid, _ = journey
    async def go():
        result = await analyzed(built, aid)
        a, b, _ = result.members
        def edit(arrays):
            if damage == 'missing': del arrays[f'{a}.bits']
            if damage == 'fractional': arrays[f'{a}.meta'][2] += .5
            if damage == 'scale': arrays[f'{a}.meta'][4] = .5
            if damage == 'bounds': arrays[f'{a}.meta'][2] = 95
            if damage == 'overlap': arrays[f'{b}.meta'] = arrays[f'{a}.meta'].copy()
            if damage == 'empty': arrays[f'{a}.bits'][:] = 0
            if damage == 'truncated': arrays[f'{a}.bits'] = arrays[f'{a}.bits'][:-1]
        rewrite_masks(result, edit)
        response = await download(built, result)
        assert response.status_code == 409, response.text
        assert response.headers['content-type'].startswith('application/json')
    asyncio.run(go())


def test_asymmetric_nonuniform_pixels_are_measured_without_display_rounding(journey, tmp_path):
    from orchestrator.evidence import Mask
    from orchestrator import population
    built, aid, image = journey
    # Three pixels (0,0), (1,0), (0,1): area 3, centroid (5/6,5/6), mean 4/3.
    gray = np.full((96, 96), 220, np.uint8)
    gray[0, 0], gray[0, 1], gray[1, 0] = 0, 1, 3
    cv2.imwrite(str(image), gray)
    async def go():
        result = await analyzed(built, aid)
        member = result.live()[0]
        member.mask = Mask(np.array([[True, True], [True, False]]), 0, 0)
        member.box = (0, 0, 2, 2)
        for other in result.live()[1:]: other.status = 'deleted'
        # Display measurements deliberately remain the old result; the export must reduce retained pixels.
        population.evidence_of(built.hub).schedule_save(result)
        await saved(result)
        files = unpack(await download(built, result))
        with zipfile.ZipFile(tmp_path / 'asymmetric-exchange.zip', 'w') as archive:
            for name, data in files.items(): archive.writestr(name, data)
        row, = list(csv.DictReader(io.StringIO(files['measurements.csv'].decode())))
        assert int(row['areaPx2']) == 3
        assert abs(float(row['centroidX']) - 0.8333333333333334) <= 1e-12
        assert abs(float(row['centroidY']) - 0.8333333333333334) <= 1e-12
        assert abs(float(row['meanIntensity']) - 1.3333333333333333) <= 1e-12
    asyncio.run(go())


@pytest.mark.parametrize('damage', ['missing-file', 'corrupt', 'duplicate-row', 'missing-row', 'wrong-id', 'wrong-dtype', 'wrong-shape', 'missing-execution'])
def test_exchange_validation_rejects_corrupt_or_inconsistent_bundles(journey, damage):
    from orchestrator.particle_exchange import validate, ExchangeRefused, digest
    built, aid, _ = journey
    async def go():
        result = await analyzed(built, aid)
        files = unpack(await download(built, result))
        manifest = json.loads(files['manifest.json'])
        rows = files['measurements.csv'].decode().splitlines()
        if damage == 'missing-file': del files['labels.tif']
        elif damage == 'corrupt': files['labels.tif'] = b'broken'
        elif damage == 'duplicate-row': rows[-1] = rows[1]
        elif damage == 'missing-row': rows.pop()
        elif damage == 'wrong-id': rows[1] = 'c999,' + rows[1].split(',', 1)[1]
        elif damage == 'missing-execution': del manifest['execution']
        else:
            labels = cv2.imdecode(np.frombuffer(files['labels.tif'], np.uint8), cv2.IMREAD_UNCHANGED)
            labels = labels.astype(np.uint8) if damage == 'wrong-dtype' else labels[:-1]
            files['labels.tif'] = cv2.imencode('.tif', labels)[1].tobytes()
            manifest['files']['labels.tif'] = digest(files['labels.tif'])
        if damage in ('duplicate-row', 'missing-row', 'wrong-id'):
            files['measurements.csv'] = ('\n'.join(rows) + '\n').encode()
            manifest['files']['measurements.csv'] = digest(files['measurements.csv'])
        files['manifest.json'] = json.dumps(manifest).encode()
        with pytest.raises(ExchangeRefused): validate(files)
    asyncio.run(go())


def test_exchange_rows_can_be_reordered_without_changing_member_mapping(journey):
    from orchestrator.particle_exchange import validate, digest
    built, aid, _ = journey
    async def go():
        result = await analyzed(built, aid)
        files = unpack(await download(built, result))
        rows = files['measurements.csv'].decode().splitlines()
        files['measurements.csv'] = ('\n'.join([rows[0], *reversed(rows[1:])]) + '\n').encode()
        manifest = json.loads(files['manifest.json'])
        manifest['files']['measurements.csv'] = digest(files['measurements.csv'])
        files['manifest.json'] = json.dumps(manifest).encode()
        assert validate(files)['evidence'] == result.id
    asyncio.run(go())

@pytest.mark.parametrize('supersede', ['revision', 'session', 'cancel'])
def test_delayed_export_never_publishes_for_a_stale_or_cancelled_request(journey, monkeypatch, supersede):
    import threading
    from orchestrator.particle_exchange import export, ExchangeRefused
    built, aid, _ = journey
    async def go():
        result = await analyzed(built, aid)
        started, release = threading.Event(), threading.Event()
        from orchestrator.evidence import Evidence
        read = Evidence.read_retained
        def delayed(path, limit):
            if path == result.path / 'source.image':
                started.set()
                assert release.wait(HANG_S)
            return read(path, limit)
        monkeypatch.setattr(Evidence, 'read_retained', delayed)
        task = asyncio.create_task(export(built, result.id, built.day.sid, 0))
        assert await asyncio.to_thread(started.wait, HANG_S)
        try:
            if supersede == 'cancel': task.cancel()
            elif supersede == 'revision': result.revision += 1
            else: await handle(built, Client(), {'type': 'session', 'action': 'new', 'name': 'Other'})
        finally: release.set()
        with pytest.raises(asyncio.CancelledError if supersede == 'cancel' else ExchangeRefused): await task
    asyncio.run(go())


def test_export_validator_refuses_a_multiplane_label_tiff(journey, tmp_path):
    from orchestrator.particle_exchange import validate, digest, ExchangeRefused
    built, aid, _ = journey
    async def go():
        result = await analyzed(built, aid)
        files = unpack(await download(built, result))
        labels = cv2.imdecode(np.frombuffer(files['labels.tif'], np.uint8), cv2.IMREAD_UNCHANGED)
        path = tmp_path / 'stack.tif'
        assert cv2.imwritemulti(str(path), [labels, labels])
        files['labels.tif'] = path.read_bytes()
        manifest = json.loads(files['manifest.json'])
        manifest['files']['labels.tif'] = digest(files['labels.tif'])
        files['manifest.json'] = json.dumps(manifest).encode()
        with pytest.raises(ExchangeRefused, match='single'):
            validate(files)
    asyncio.run(go())
