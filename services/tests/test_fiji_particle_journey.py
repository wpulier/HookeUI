"""Actions → original picture → Fiji process → retained resource.

The external Fiji launcher is a deterministic process stand-in, not scientific
parity evidence. The app, request dispatch, adapter, evidence and resources are real.
"""
import asyncio
import json
import sys

import pytest

from orchestrator import fiji_bindings, fiji_generations, population
from orchestrator.commands import handle
from tests.test_original_picture_analysis import prepare
from tests.test_sources import Client
from tests.waiting import until_async


@pytest.fixture
def journey(tmp_path, monkeypatch):
    built, _, aid, image = prepare(tmp_path, monkeypatch)
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    root = fiji_generations.root()
    for n in (1, 2):
        folder = root / str(n) / "Fiji"
        folder.mkdir(parents=True)
        (folder.parent / "receipt.json").write_text(json.dumps({
            "n": n, "base": {"sha256": "a" * 64, "version": "stand-in Fiji with JDK 21"},
            "sites": [], "files": [], "proofs": {fiji_bindings.BINDING: {**fiji_bindings.proof_identity(), "passed": True, "summary": "process stand-in proof"}}}))
        launcher = folder / "fiji"
        launcher.write_text(f'''#!{sys.executable}
import json, sys, time, base64, zlib
import cv2
import numpy as np
from pathlib import Path
here = Path(__file__).parent
root = here.parent.parent
mode = (root / "mode").read_text() if (root / "mode").exists() else "ok"
polygons = [[[70,10],[86,10],[86,23],[70,23]], [[15,15],[36,15],[36,36],[15,36]], [[58,53],[73,53],[73,68],[58,68]]]
labels = np.zeros((96,96), np.uint16)
labels[10:23,70:86] = 1
cv2.circle(labels, (25,25), 10, 2, -1)
cv2.circle(labels, (65,60), 7, 3, -1)
if mode == "crop":
    labels = labels[10:40,10:40].copy()
    labels[labels == 2] = 1
    polygons = [[[5,5],[26,5],[26,26],[5,26]]]
if mode == "zero":
    polygons = []
    labels[:] = 0
if mode == "bad-polygon": polygons[0] = [[1,2]]
if any("reference_particles.groovy" in arg for arg in sys.argv):
    import re
    args = dict(re.findall(r"(\\w+)='([^']*)'", sys.argv[-1]))
    source = cv2.imread(args["imagePath"], cv2.IMREAD_GRAYSCALE)
    if mode == "reference-shift": labels = np.roll(labels, 1, axis=1)
    rows = []
    for label in range(1, len(polygons)+1):
        ys, xs = np.where(labels == label)
        rows.append({{"label":label, "area":len(xs), "cx":float(xs.mean()+.5), "cy":float(ys.mean()+.5), "mean":float(source[ys,xs].mean())}})
    out = Path(args["outputDir"])
    cv2.imwrite(str(out / "reference-labels.tif"), labels)
    (out / "reference.json").write_text(json.dumps({{"ok":True, "particles":rows, "width":labels.shape[1], "height":labels.shape[0]}}))
    sys.exit(0)
if "-macro" in sys.argv:
    if mode == "macro-empty": sys.exit(0)
    print("n " + str(7 if mode == "macro-count" else len(polygons)))
    for poly in polygons:
        print("p " + ";".join(str(x)+","+str(y) for x,y in poly)+";")
    if mode != "macro-incomplete": print("done particles")
else:
    print(json.dumps({{"ok": True, "ready": "1.54p"}}), flush=True)
    for line in sys.stdin:
        req = json.loads(line)
        if mode == "wait":
            (root / "started").touch()
            while not (root / "release").exists(): time.sleep(0.01)
        if mode == "stopped": sys.exit(0)
        if mode == "binding-list":
            print('@@[]', flush=True)
            continue
        if mode == "binding-missing":
            print('@@{{"ok": true}}', flush=True)
            continue
        source = cv2.imread(req["image"], cv2.IMREAD_GRAYSCALE)
        particles = []
        for label, poly in enumerate(polygons, 1):
            ys, xs = np.where(labels == label)
            particles.append({{"label": label, "polygon": poly, "area": len(xs), "cx": float(xs.mean()+.5),
                              "cy": float(ys.mean()+.5), "mean": float(source[ys,xs].mean())}})
        if mode == "bad-mean": particles[0]["mean"] = 180
        (root / "current").write_text("2")
        reply = {{"ok": True, "particles": particles,
             "maskContract": "imagej-particle-pixels-v1",
             "labels": {{"dtype": "<u2", "shape": list(labels.shape), "base64": base64.b64encode(zlib.compress(labels.tobytes())).decode()}},
             "threshold": [0,129], "width": labels.shape[1], "height": labels.shape[0],
             "runtime": {{"imagej": "1.54p", "java": "21", "javaVendor": "stand-in"}}}}
        if mode == "missing-mask": del reply["labels"]
        if mode == "corrupt-mask": reply["labels"]["base64"] = "!bad!"
        if mode == "wrong-grid": reply["labels"]["shape"] = [95,96]
        if mode == "wrong-dtype": reply["labels"]["dtype"] = "float32"
        if mode == "outside-polygon": particles[0]["polygon"][0][0] = 97
        if mode == "duplicate-label": particles[1]["label"] = 1
        if mode == "wrong-area": particles[0]["area"] += 1
        if mode == "overwritten-overlap":
            labels[10,70] = 2
            reply["labels"]["base64"] = base64.b64encode(zlib.compress(labels.tobytes())).decode()
        if mode == "trailing-mask": reply["labels"]["base64"] = base64.b64encode(zlib.compress(labels.tobytes())+b"junk").decode()
        if mode == "wrong-contract": reply["maskContract"] = "old"
        print("@@"+json.dumps(reply), flush=True)
''')
        launcher.chmod(0o755)
    (root / "current").write_text("1")
    fiji_bindings.WORKER.stop()
    yield built, aid, image
    fiji_bindings.WORKER.stop()


def test_explicit_action_keeps_original_input_and_pinned_comparison_receipt(journey):
    built, aid, image = journey

    async def go():
        await handle(built, Client(), {"type": "source", "open": aid})
        client = Client()
        await handle(built, client, {"type": "action", "capability": "cells",
                                     "args": {"compare_reference": True}, "use": {"cells": "fiji-analyze-particles"}})
        assert client.replies[-1]["ok"], client.replies[-1]
        result = population.evidence_of(built.hub).get(population.layer_of(built.hub).shown)
        assert result is not None
        document = built.hub.resources.document(result.id)
        producer = document["detail"]["producer"]
        assert producer["runtime"]["generation"] == 1
        assert producer["comparison"]["generation"] == 1
        assert producer["comparison"]["agreement"] is True
        assert producer["comparison"]["count"] == 3
        assert producer["input"]["pixelsSha256"] == "1343019a13d406a783dd80da0933f798c78b86d34886331e693ff92407767506"
        assert producer["settings"] == {"conversion": "8-bit", "thresholdMethod": "Default", "minimumSizePx2": 20.0}
        assert document["detail"]["columns"]["measure"]["definitions"]["areaPx"] == "Retained mask pixel count multiplied by mask scale squared (capture px²)"
        assert "pixel centers" in document["detail"]["columns"]["measure"]["definitions"]["centroid"]
        from orchestrator.operations import runner
        execution = runner(built.hub).get(result.made_by["execution"]).wire()
        assert execution["producer"]["runtime"]["generation"] == 1
        assert execution["producer"]["comparison"]["agreement"] is True
        assert execution["capture"] == result.capture_id
        assert result.id in execution["outputs"]
        assert result.made_by["origin"] == "user-chosen"
        assert "Particles" in result.title and "agreement" in document["detail"]["text"].lower()
        await until_async(lambda: result.status == "saved")
        assert result.status == "saved"
        sid = built.day.sid
        await handle(built, Client(), {"type": "session", "action": "new", "name": "Other session"})
        saved = built.hub.resources.document(result.id, sid)
        assert saved["detail"]["producer"] == producer
        assert saved["entry"]["madeBy"]["execution"] == result.made_by["execution"]
        assert {m["id"] for m in saved["detail"]["members"]} == set(result.members)

    asyncio.run(go())


@pytest.mark.parametrize("mode", ["binding-missing", "binding-list", "bad-polygon", "macro-empty", "macro-incomplete", "macro-count", "stopped", "bad-mean", "missing-mask", "corrupt-mask", "wrong-grid", "wrong-dtype",
    "outside-polygon", "duplicate-label", "wrong-area", "overwritten-overlap", "trailing-mask", "wrong-contract"])
def test_invalid_or_stopped_fiji_never_becomes_successful_empty_evidence(journey, mode):
    built, aid, _ = journey
    (fiji_generations.root() / "mode").write_text(mode)

    async def go():
        await handle(built, Client(), {"type": "source", "open": aid})
        client = Client()
        await handle(built, client, {"type": "action", "capability": "cells",
                                     "args": {"compare_reference": True}, "use": {"cells": "fiji-analyze-particles"}})
        assert client.replies[-1]["ok"] is False, client.replies[-1]
        assert "retry" in client.replies[-1]["error"].lower(), client.replies[-1]
        assert population.evidence_of(built.hub).latest() is None
        assert population.layer_of(built.hub).shown is None

    asyncio.run(go())


def test_complete_zero_particle_results_are_valid_and_compared(journey):
    built, aid, _ = journey
    (fiji_generations.root() / "mode").write_text("zero")

    async def go():
        await handle(built, Client(), {"type": "source", "open": aid})
        client = Client()
        await handle(built, client, {"type": "action", "capability": "cells",
                                     "args": {"compare_reference": True}, "use": {"cells": "fiji-analyze-particles"}})
        assert client.replies[-1]["ok"], client.replies[-1]
        result = population.evidence_of(built.hub).latest()
        assert result is not None and not result.members
        comparison = result.producer["comparison"]
        assert comparison["count"] == comparison["referenceCount"] == 0
        assert comparison["agreement"] is True

    asyncio.run(go())


def test_reference_comparison_cannot_silently_run_an_unsupported_method(journey):
    built, aid, _ = journey

    async def go():
        await handle(built, Client(), {"type": "source", "open": aid})
        client = Client()
        await handle(built, client, {"type": "action", "capability": "cells",
                                     "args": {"compare_reference": True}, "use": {"cells": "cellpose-sam"}})
        assert client.replies[-1]["ok"] is False, client.replies[-1]
        assert "Fiji" in client.replies[-1]["error"]
        assert not built.hub.gpu_heavy.inputs
        assert population.evidence_of(built.hub).latest() is None

    asyncio.run(go())


@pytest.mark.parametrize("change", ["cancel", "clear", "source", "session"])
def test_late_fiji_comparison_cannot_mutate_a_cancelled_or_superseded_destination(journey, change):
    built, aid, _ = journey
    root = fiji_generations.root()
    (root / "mode").write_text("wait")

    async def go():
        await handle(built, Client(), {"type": "source", "open": aid})
        client = Client()
        task = asyncio.create_task(handle(built, client, {"type": "action", "capability": "cells",
            "args": {"compare_reference": True}, "use": {"cells": "fiji-analyze-particles"}}))
        try:
            await until_async(lambda: (root / "started").exists())
            assert (root / "started").exists()
            if change == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif change == "clear":
                await handle(built, Client(), {"type": "action", "capability": "clear", "args": {"what": "cells"}})
            elif change == "source":
                await handle(built, Client(), {"type": "source", "open": "live"})
            else:
                await handle(built, Client(), {"type": "session", "action": "new", "name": "Other"})
        finally:
            (root / "release").touch()
        if change != "cancel":
            await task
            assert client.replies[-1]["ok"], client.replies[-1]
            assert "not shown" in client.replies[-1]["summary"]
        assert population.layer_of(built.hub).shown is None

    asyncio.run(go())


def test_original_particle_result_is_presented_as_an_import_not_a_lost_camera_view(journey):
    from types import SimpleNamespace
    import cv2
    import numpy as np
    from orchestrator import media
    from orchestrator.brain.toolbox.present import _on_their_view

    built, aid, _ = journey

    async def go():
        await handle(built, Client(), {"type": "source", "open": aid})
        await built.hub.source._accept(cv2.imencode(".jpg", np.full((180, 320), 200, np.uint8))[1].tobytes())
        result = await population.segment(built.hub, choice="fiji-analyze-particles")
        layer = population.layer_of(built.hub)
        layer.show(result)
        await layer.check()
        assert "original image" in layer.wire()["why"]
        assert "motion" not in layer.wire()["why"]
        entry = built.hub.resources.get(result.id)
        assert entry.media == "raw"
        assert "imported image" in media.line(entry.media)
        assert entry.source["sourceImage"]["asset"] == aid
        shown = []
        magnifier = SimpleNamespace(show_picture=lambda *args, **kwargs: shown.append(kwargs) or "/picture")
        await _on_their_view(SimpleNamespace(hub=built.hub, magnifier=magnifier), layer, result)
        assert shown[0]["kind_of"] == media.RAW

    asyncio.run(go())


def test_native_fiji_pixels_survive_the_action_population_and_saved_evidence_boundary(journey):
    """Literal fixture truth; producer transport is a process stand-in, not a Fiji scientific proof."""
    import numpy as np
    from tests.test_particle_corrections import analyzed
    built, aid, _ = journey
    async def go():
        result = await analyzed(built, aid)
        assert population.evidence_of(built.hub).reload_masks(result)
        assert [int(np.count_nonzero(m.mask.bits)) for m in result.live()] == [208, 317, 149]
        assert [m.instance for m in result.live()] == [1, 2, 3]
        assert [m.mask.box for m in result.live()] == [(70, 10, 86, 23), (15, 15, 36, 36), (58, 53, 73, 68)]
        assert [p['area'] for p in result.producer['particles']] == [208, 317, 149]
        assert [(p['cx'], p['cy']) for p in result.producer['particles']] == [(78, 16.5), (25.5, 25.5), (65.5, 60.5)]
        assert [p['mean'] for p in result.producer['particles']] == [40, 40, 40]
    asyncio.run(go())


def test_old_polygon_proof_requires_explicit_reproof_in_readiness_choices_and_execution(journey):
    from orchestrator import readiness, options, catalog_view
    from gpu_worker import manifests
    built, aid, _ = journey
    file = fiji_generations.root() / '1' / 'receipt.json'
    old = json.loads(file.read_text())
    old['proofs'][fiji_bindings.BINDING] = 'matches its macro: 3 on particles-fixture'
    file.write_text(json.dumps(old))
    before = file.read_bytes()
    status = readiness.model(fiji_bindings.BINDING, {})
    assert status.steps[0].says == 'Prove it on Fiji'
    assert 'Prove it' in options.unavailable("cells", fiji_bindings.BINDING)
    facts = catalog_view.facts(manifests.get(fiji_bindings.BINDING), {}, lambda k: False, [])
    assert any(f['kind'] == 'check' and not f['ok'] and 'Prove it' in f['says'] for f in facts)
    async def go():
        await handle(built, Client(), {'type': 'source', 'open': aid})
        client = Client()
        await handle(built, client, {'type': 'action', 'capability': 'cells', 'use': {'cells': fiji_bindings.BINDING}})
        assert client.replies[-1]['ok'] is False
        assert 'Prove it' in str(client.replies[-1])
        assert population.evidence_of(built.hub).latest() is None
        assert file.read_bytes() == before
    asyncio.run(go())


def test_reference_agreement_compares_pixels_even_when_outline_comparison_agrees(journey):
    from tests.test_particle_corrections import analyzed
    built, aid, _ = journey
    (fiji_generations.root() / 'mode').write_text('reference-shift')
    async def go():
        result = await analyzed(built, aid)
        assert result.producer['comparison']['agreement'] is False
        assert result.producer['comparison']['pixelAgreement'] is False
    asyncio.run(go())


def test_native_crop_origin_survives_saved_population_and_exchange(journey):
    """An explicit original-pixel crop; the preview-selection path is separately refused."""
    import csv, io
    from orchestrator import source_images
    from tests.test_particle_corrections import saved
    from tests.test_particle_exchange import download, unpack
    built, aid, _ = journey
    (fiji_generations.root() / 'mode').write_text('crop')
    async def go():
        await handle(built, Client(), {'type':'source', 'open':aid})
        capture = await source_images.for_analysis(built.hub)
        result = await population.segment(built.hub, capture=capture, choice=fiji_bindings.BINDING,
                                          region=(10,10,30,30), compare_reference=True)
        await saved(result)
        assert result.live()[0].box == (15,15,36,36)
        assert result.producer['nativeMeasurements']['origin'] == [10,10]
        assert result.producer['particles'][0]['cx'] == 15.5
        files = unpack(await download(built, result))
        row, = list(csv.DictReader(io.StringIO(files['measurements.csv'].decode())))
        assert (int(row['areaPx2']), float(row['centroidX']), float(row['centroidY']), float(row['meanIntensity'])) == (317,25.5,25.5,40)
    asyncio.run(go())
