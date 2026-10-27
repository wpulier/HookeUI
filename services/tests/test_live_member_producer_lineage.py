"""TEST-ONLY CPU member producers, authored on exact Root94064bd3.

Images, bridge metadata and external masks are SYNTHETIC. App, FrameSource,
Captures, Evidence saving/readback and Layer templates/pixel checks are real.
from_matches is the existing external-result seam; its SAM-shaped provenance
does not mean SAM/Cellpose ran. No source grant, observer, model, GPU, network
lifespan, browser draw or pure-core live authority is supplied by this test.

Scheduling seam: cancel the real periodic Layer task immediately after show,
then call real check explicitly. Race barriers delegate asyncio.to_thread to
the real worker and delay only delivery of its completed result. Client.control
observes real Hub broadcasts. Parent owns execution and the first observed RED.
"""
import asyncio
import copy
import json
import math
import time
from contextlib import asynccontextmanager, suppress

import cv2
import numpy as np
import pytest

from orchestrator.app import App
from orchestrator.capture import RUN, captures
from orchestrator.hub import Client
from orchestrator.paths import Paths
from orchestrator.population import capture_of, captured_picture, correct, from_matches
from tests.waiting import HANG_S


def synthetic_slide(count=6):
    """Independent texture inside a real detectable disc; masks are external input."""
    rng = np.random.default_rng(940)
    pixels = np.full((640, 640), 5, np.uint8)
    disc = np.zeros_like(pixels)
    cv2.circle(disc, (320, 320), 280, 1, -1)
    texture = rng.integers(95, 210, pixels.shape, dtype=np.uint8)
    pixels[disc.astype(bool)] = texture[disc.astype(bool)]
    cols = min(16, count)
    rows = math.ceil(count / cols)
    boxes = []
    for n in range(count):
        x = 320 - cols * 10 + (n % cols) * 20
        y = 320 - rows * 10 + (n // cols) * 20
        pixels[y:y + 16, x:x + 16] = rng.integers(25, 235, (16, 16), dtype=np.uint8)
        boxes.append((x, y, x + 16, y + 16))
    return pixels, boxes


class Producer:
    def __init__(self, root):
        self.app = App.build('http://127.0.0.1:1', Paths.under(root), use_gpu=False)
        self.hub, self.layer = self.app.hub, self.app.hub.layer
        self.store = captures(self.hub)
        self.client = Client(None)
        self.hub.clients.add(self.client)
        self.seq = 940_000
        self.hub.scale.set_objective(10, via='synthetic producer input')

    async def receive(self, pixels, warm=False):
        ok, encoded = cv2.imencode('.jpg', pixels, [cv2.IMWRITE_JPEG_QUALITY, 96])
        assert ok
        jpeg = encoded.tobytes()
        for _ in range(6 if warm else 1):
            self.seq += 1
            await self.hub.source._accept(jpeg, seq=self.seq, boot='synthetic-member-bridge')
        info = self.hub.latest_info
        assert info.fov is not None and info.tracked, 'controlled pixels must really register through intake'
        held = self.store.take(info.id)
        assert held.jpeg == jpeg and held.seq == self.seq
        return held

    async def population(self, held, boxes):
        masks = []
        for x0, y0, x1, y1 in boxes:
            bits = np.zeros((held.height, held.width), bool)
            bits[y0:y1, x0:x1] = True
            masks.append({'mask': bits, 'score': 0.91})
        pop = from_matches(self.hub, held, masks,
                           {'implementation': 'synthetic-external-mask-input', 'origin': 'test'},
                           example=None, execution_id=None,
                           configuration={'dataset': 'synthetic texture and masks; no SAM execution'})
        assert len(pop.live()) == len(boxes)
        assert pop.capture_id == held.id and pop.scale_id == held.scale
        await self.saved(pop)
        return pop

    async def saved(self, pop):
        async def wait():
            while pop.status == 'saving' or pop.id in self.hub.evidence._writing:
                await asyncio.sleep(0.005)
        await asyncio.wait_for(wait(), 20)
        assert pop.status == 'saved', pop.status_why

    async def show(self, pop):
        self.layer.show(pop)
        await self.stop_periodic()

    async def stop_periodic(self):
        task = self.layer._task
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def publications(self):
        rows = []
        while not self.client.control.empty():
            row = json.loads(self.client.control.get_nowait())
            if row.get('type') == 'layer':
                rows.append(row)
        return rows

    async def close(self):
        await self.stop_periodic()
        for pop in list(self.hub.evidence.items.values()):
            await self.saved(pop)
        self.hub.clients.discard(self.client)
        if self.app.slidemap is not None and self.app.slidemap._pool is not None:
            self.app.slidemap._pool.shutdown(wait=True)
        self.hub._analysis.shutdown(wait=True)
        self.hub._background.shutdown(wait=True)


@asynccontextmanager
async def producer(root):
    rig = Producer(root)
    try:
        yield rig
    finally:
        await rig.close()


def member_checks(wire, pop):
    """Assert the approved public wire; never build a receipt or substitute proof."""
    assert 'memberChecks' in wire, 'MISSING_FEATURE: Layer publishes revision-bound memberChecks'
    proof = wire['memberChecks']
    assert isinstance(proof, dict), 'MISSING_FEATURE: useful real pixel checks expose grouped lineage'
    assert set(proof) == {'schema', 'populationId', 'revision', 'samples', 'members'}
    assert proof['schema'] == 'member-checks-v1'
    assert (proof['populationId'], proof['revision']) == (pop.id, pop.revision)
    assert 1 <= len(proof['samples']) <= 8 and len(proof['members']) <= 512
    samples = {sample['ref']: sample for sample in proof['samples']}
    assert len(samples) == len(proof['samples'])
    for ref, sample in samples.items():
        assert isinstance(ref, str) and 0 < len(ref) <= 256
        assert set(sample) == {'ref', 'checkedAtMono', 'checkedBasis'}
        assert isinstance(sample['checkedAtMono'], (int, float)) and math.isfinite(sample['checkedAtMono'])
    members = {row['id']: row for row in proof['members']}
    assert len(members) == len(proof['members'])
    for row in members.values():
        assert set(row) == {'id', 'checkRef', 'score', 'placement'}
        assert row['checkRef'] in samples
    assert set(wire['verified']) <= set(members)
    assert len(json.dumps(wire, allow_nan=False).encode()) <= 65_536

    nodes = 0
    def bounded(value, depth=0):
        nonlocal nodes
        nodes += 1
        assert nodes <= 16_384 and depth <= 16
        if isinstance(value, dict):
            for nested in value.values():
                bounded(nested, depth + 1)
        elif isinstance(value, list):
            for nested in value:
                bounded(nested, depth + 1)
    bounded(wire)
    return proof, samples, members


def checked_capture(sample, held, before, after):
    assert before <= sample['checkedAtMono'] <= after
    basis = sample['checkedBasis']
    assert set(basis) == {'frameId', 'captureId', 'run', 'boot', 'seq', 'epoch', 'imageSize',
                          'disc', 'offset', 'objective', 'umPerPx', 'scaleId', 'scale'}
    assert (basis['frameId'], basis['run'], basis['boot'], basis['seq'], basis['epoch']) == (
        held.id, RUN, held.boot, held.seq, held.epoch)
    assert basis['captureId'] is None and basis['scaleId'] is None, 'canonical identities have no producer yet'
    assert basis['imageSize'] == [held.width, held.height]
    assert basis['disc'] == pytest.approx([held.fov.cx, held.fov.cy, held.fov.radius])
    assert basis['offset'] == [held.dx, held.dy]
    assert basis['objective'] == held.scale['objective']
    assert basis['umPerPx'] == held.scale['umPerPx']
    assert basis['scale'] == held.scale, 'all actual frozen setter/calibration provenance belongs to this capture'


@asynccontextmanager
async def worker_delivery(monkeypatch, layer, method):
    """Delay delivery AFTER the real worker computes; no matching/template result is replaced."""
    real = asyncio.to_thread
    arrived, release = asyncio.Event(), asyncio.Event()
    intercepted = False
    tasks = []

    async def delivery(fn, *args, **kwargs):
        nonlocal intercepted
        result = await real(fn, *args, **kwargs)
        if not intercepted and getattr(fn, '__self__', None) is layer and getattr(fn, '__name__', '') == method:
            intercepted = True
            arrived.set()
            await release.wait()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(asyncio, 'to_thread', delivery)
        try:
            task = asyncio.create_task(layer.check())
            tasks.append(task)
            await asyncio.wait_for(arrived.wait(), HANG_S)
            yield release
        finally:
            release.set()
            await asyncio.gather(*tasks)


@pytest.mark.parametrize('count,calibrated', [(6, False), (6, True), (160, False)])
def test_compatible_check_publishes_own_capture_revision_and_grouped_monotonic_lineage(tmp_path, count, calibrated):
    async def exercise():
        async with producer(tmp_path) as rig:
            pixels, boxes = synthetic_slide(count)
            held = await rig.receive(pixels, warm=True)
            if calibrated:
                rig.hub.scale.set_objective(10, field_number=20, via='synthetic eyepiece input')
                rig.hub.scale.calibrate(100, 50, held.disc or held.fov.radius)
                held = await rig.receive(pixels)
                assert held.scale['measured'] is True
                assert held.scale['optics']['fieldNumber']['source'] == 'user'
                assert held.scale['optics']['calibrated']['10']['lengthUm'] == 50
            pop = await rig.population(held, boxes)
            await rig.show(pop)
            before = time.monotonic()
            await rig.layer.check()
            after = time.monotonic()
            wanted = {m.id for m in pop.live()}
            assert rig.layer.verified_count() == (count, count), 'useful compatible first-view control'
            wire = rig.layer.wire()
            assert set(wire['verified']) == wanted
            proof, samples, rows = member_checks(wire, pop)
            assert set(rows) == wanted and len(samples) == 1, 'one grouped basis even for 160 members'
            sample = next(iter(samples.values()))
            checked_capture(sample, held, before, after)
            for mid in wanted:
                placement = rig.layer.placement[mid]
                assert rows[mid]['score'] == placement['score'] >= 0.6
                assert rows[mid]['checkRef'] == sample['ref']
                assert rows[mid]['placement']['offset'] == [placement['dx'], placement['dy']]
                assert rows[mid]['placement']['correction'] == list(placement['correction'])
            # Collection repeats immutable evidence; it cannot refresh the check.
            assert rig.layer.wire()['memberChecks'] == proof
    asyncio.run(exercise())


def test_unavailable_or_hidden_layer_explicitly_has_no_member_checks(tmp_path):
    async def exercise():
        async with producer(tmp_path) as rig:
            wire = rig.layer.wire()
            assert 'memberChecks' in wire, 'MISSING_FEATURE: unavailable proof is explicit null'
            assert wire['memberChecks'] is None
            pixels, boxes = synthetic_slide()
            held = await rig.receive(pixels, warm=True)
            pop = await rig.population(held, boxes)
            await rig.show(pop)
            assert rig.layer.wire()['memberChecks'] is None
            await rig.layer.check()
            assert rig.layer.verified_count() == (6, 6)
            rig.layer.hide()
            assert rig.layer.wire()['memberChecks'] is None
    asyncio.run(exercise())


def test_unchanged_verified_set_renews_real_publication_without_rebinding_old_sample(tmp_path):
    async def exercise():
        async with producer(tmp_path) as rig:
            pixels, boxes = synthetic_slide()
            first = await rig.receive(pixels, warm=True)
            pop = await rig.population(first, boxes)
            await rig.show(pop)
            await rig.layer.check()
            assert rig.layer.verified_count() == (6, 6)
            first_wire = rig.layer.wire()
            rig.publications()
            later = await rig.receive(pixels)
            before = time.monotonic()
            await rig.layer.check()
            after = time.monotonic()
            assert rig.layer.verified_count() == (6, 6)
            published = rig.publications()
            assert published, 'MISSING_FEATURE: unchanged verified IDs still publish the renewed actual check'
            renewed = published[-1]
            assert renewed['verified'] == first_wire['verified']
            old_proof, old_samples, _ = member_checks(first_wire, pop)
            new_proof, new_samples, _ = member_checks(renewed, pop)
            assert len(new_samples) == 1 and set(old_samples).isdisjoint(new_samples)
            checked_capture(next(iter(new_samples.values())), later, before, after)
            assert next(iter(new_samples.values()))['checkedAtMono'] >= next(iter(old_samples.values()))['checkedAtMono']
            assert first_wire['memberChecks'] == old_proof
            assert next(iter(old_samples.values()))['checkedBasis']['frameId'] == first.id
            assert rig.layer.wire()['memberChecks'] == new_proof
    asyncio.run(exercise())


def test_failed_member_own_pixels_invalidate_at_once_with_useful_siblings(tmp_path):
    async def exercise():
        async with producer(tmp_path) as rig:
            pixels, boxes = synthetic_slide()
            first = await rig.receive(pixels, warm=True)
            pop = await rig.population(first, boxes)
            await rig.show(pop)
            await rig.layer.check()
            assert rig.layer.verified_count() == (6, 6)
            old_wire = rig.layer.wire()
            moved = pixels.copy()
            x0, y0, x1, y1 = boxes[0]
            moved[y0:y1, x0:x1] = 150  # the first member's texture vanished; surroundings/siblings stay
            later = await rig.receive(moved)
            assert later.epoch == first.epoch
            before = time.monotonic()
            await rig.layer.check()
            after = time.monotonic()
            gone = next(iter(pop.members))
            siblings = set(pop.members) - {gone}
            assert rig.layer.placement[gone]['state'] == 'unverified'
            assert rig.layer.verified_count() == (5, 6), 'own-member failure must preserve useful siblings'
            wire = rig.layer.wire()
            assert set(wire['verified']) == siblings
            _, old_samples, old_rows = member_checks(old_wire, pop)
            _, samples, rows = member_checks(wire, pop)
            for mid in siblings:
                checked_capture(samples[rows[mid]['checkRef']], later, before, after)
                assert rows[mid]['score'] == rig.layer.placement[mid]['score']
            if gone in rows:
                assert rows[gone]['checkRef'] != old_rows[gone]['checkRef'], 'failed check cannot carry the old positive proof'
            assert old_samples[old_rows[gone]['checkRef']]['checkedBasis']['frameId'] == first.id
    asyncio.run(exercise())


@pytest.mark.parametrize('worker', ['_templates', '_match'])
def test_same_population_revision_during_real_worker_delivery_cannot_commit_obsolete_result(tmp_path, monkeypatch, worker):
    async def exercise():
        async with producer(tmp_path) as rig:
            pixels, boxes = synthetic_slide()
            first = await rig.receive(pixels, warm=True)
            pop = await rig.population(first, boxes)
            await rig.show(pop)
            if worker == '_match':
                await rig.layer.check()
                assert rig.layer.verified_count() == (6, 6)
                await rig.receive(pixels)
            async with worker_delivery(monkeypatch, rig.layer, worker):
                removed = next(iter(pop.members))
                with rig.hub.evidence.keeping_pixels(pop):
                    words, gone = correct(rig.hub, pop, 'remove', [removed], by='synthetic correction')
                assert removed in gone and 'removed' in words and pop.revision == 1
                placement = copy.deepcopy(rig.layer.placement)
                templates = rig.layer.templates
                view, correction = rig.layer.view, rig.layer.correction
                rig.publications()
            assert rig.layer.placement == placement, 'MISSING_FEATURE: revision fences obsolete real matcher results'
            assert rig.layer.templates is templates, 'MISSING_FEATURE: old revision templates cannot be installed'
            assert (rig.layer.view, rig.layer.correction) == (view, correction)
            assert not rig.publications(), 'no obsolete layer publication'
            await rig.saved(pop)
            await rig.layer.check()
            assert rig.layer.verified_count() == (5, 5), 'fresh corrected revision remains usable'
            _, _, rows = member_checks(rig.layer.wire(), pop)
            assert set(rows) == {m.id for m in pop.live()} and removed not in rows
    asyncio.run(exercise())


@pytest.mark.parametrize('change', ['hide', 'hide-show', 'show-other'])
def test_real_shown_state_change_during_match_delivery_preserves_intervening_state(tmp_path, monkeypatch, change):
    async def exercise():
        async with producer(tmp_path) as rig:
            pixels, boxes = synthetic_slide()
            first = await rig.receive(pixels, warm=True)
            pop = await rig.population(first, boxes)
            other = await rig.population(first, boxes[:3]) if change == 'show-other' else None
            await rig.show(pop)
            await rig.layer.check()
            assert rig.layer.verified_count() == (6, 6)
            await rig.receive(pixels)
            async with worker_delivery(monkeypatch, rig.layer, '_match'):
                if change == 'show-other':
                    await rig.show(other)
                else:
                    rig.layer.hide()
                    if change == 'hide-show':
                        await rig.show(pop)
                shown = rig.layer.shown
                placement = copy.deepcopy(rig.layer.placement)
                templates = rig.layer.templates
                view, correction = rig.layer.view, rig.layer.correction
                rig.publications()
            assert rig.layer.shown == shown and rig.layer.placement == placement, 'obsolete check cannot refill intervening hide/show'
            assert rig.layer.templates is templates
            assert (rig.layer.view, rig.layer.correction) == (view, correction)
            assert not rig.publications()
            if change != 'hide':
                await rig.layer.check()
                active = other if other is not None else pop
                assert rig.layer.verified_count() == (len(active.live()), len(active.live()))
                member_checks(rig.layer.wire(), active)
    asyncio.run(exercise())


def test_160_original_members_remain_unregistered_on_new_40x_bitmap_and_original_capture_survives(tmp_path):
    async def exercise():
        async with producer(tmp_path) as rig:
            pixels, boxes = synthetic_slide(160)
            original = await rig.receive(pixels, warm=True)
            pop = await rig.population(original, boxes)
            requested = tuple(pop.members)
            await rig.show(pop)
            await rig.layer.check()
            assert rig.layer.verified_count() == (160, 160), '160 real own-pixel compatible controls'
            rig.hub.scale.set_objective(40, via='synthetic new-bitmap optics')
            current = await rig.receive(pixels)
            assert current.scale['objective'] == 40 and original.scale['objective'] == 10
            assert current.epoch == original.epoch and current.id != original.id
            await rig.layer.check()
            assert tuple(pop.members) == requested and pop.revision == 0
            assert rig.layer.verified_count() == (0, 160)
            assert all('objective changed' in rig.layer.placement[mid].get('why', '') for mid in requested)
            assert rig.layer.wire()['verified'] == []
            assert capture_of(rig.hub, pop).jpeg == original.jpeg
            assert pop.scale_id == original.scale
            # No actual renderer is exercised: this server gate is not a count-as-paint claim.
            assert rig.layer.drawable(current.info) == []
    asyncio.run(exercise())


def test_knob_changed_after_held_10x_does_not_relabel_the_bitmap_being_checked(tmp_path):
    async def exercise():
        async with producer(tmp_path) as rig:
            pixels, boxes = synthetic_slide()
            held = await rig.receive(pixels, warm=True)
            pop = await rig.population(held, boxes)
            await rig.show(pop)
            # No new JPEG follows this knob change. latest_info still names the 10x bitmap.
            rig.hub.scale.set_objective(40, via='synthetic later knob input')
            before = time.monotonic()
            await rig.layer.check()
            after = time.monotonic()
            assert rig.hub.latest_info.id == held.id and rig.store.take(held.id).scale['objective'] == 10
            assert rig.layer.verified_count() == (6, 6), 'MISSING_FEATURE: held check optics, not later knobs, govern registration'
            _, samples, _ = member_checks(rig.layer.wire(), pop)
            checked_capture(next(iter(samples.values())), held, before, after)
    asyncio.run(exercise())


def test_real_saved_readback_and_corrected_capture_remain_historical_after_scale_change(tmp_path):
    async def exercise():
        async with producer(tmp_path) as rig:
            pixels, boxes = synthetic_slide()
            original = await rig.receive(pixels, warm=True)
            pop = await rig.population(original, boxes)
            await rig.show(pop)
            with rig.hub.evidence.keeping_pixels(pop):
                removed = next(iter(pop.members))
                correct(rig.hub, pop, 'remove', [removed], by='synthetic saved correction')
            await rig.saved(pop)
            assert pop.revision == 1 and pop.capture is None
            assert rig.hub.evidence.read_back() == [pop.id]
            assert rig.hub.evidence.problems == []
            back = rig.hub.evidence.get(pop.id)
            assert back is not pop and back.revision == 1
            assert back.view == pop.view and back.scale_id == original.scale
            assert {m.id for m in back.live()} == set(pop.members) - {removed}
            rig.hub.scale.set_objective(40, via='synthetic live optics after saved evidence')
            current = await rig.receive(pixels)
            await rig.show(back)
            await rig.layer.check()
            assert rig.layer.verified_count() == (0, 5)
            assert all('objective changed' in p.get('why', '') for p in rig.layer.placement.values())
            own = capture_of(rig.hub, back)
            assert own.jpeg == original.jpeg and own.scale == original.scale
            assert (own.seq, own.boot, own.epoch) == (original.seq, original.boot, original.epoch)
            picture, facts = captured_picture(rig.hub, back, [back.live()[0].id], with_facts=True)
            assert picture and facts['outlines'] is True
            assert (facts['frame'], facts['run'], facts['boot'], facts['seq'], facts['revision']) == (
                original.id, RUN, original.boot, original.seq, 1)
            assert facts['scale'] == original.scale and facts['originalSize'] == [original.width, original.height]
            assert current.scale['objective'] == 40 and facts['scale']['objective'] == 10
            assert rig.layer.drawable(current.info) == []
    asyncio.run(exercise())


# TEST-only follow-up on immutable edacc546; the original 487-line prefix stays frozen.
# Immediate readers: actual intake, Layer.states -> Population.wire, App.layer_wires,
# and Evidence's existing notification -> App.evidence_changed -> real Client.control.
# Cancellation: a gate INSIDE the real CPU callable holds its actual ndarray before
# the original _half/_match runs unchanged. A transparent to_thread delegate tracks
# real worker tasks under shield for cleanup; it never supplies results or owns a Hold.


@pytest.mark.parametrize('count', [6, 160])
def test_current_population_publications_gate_new_optics_before_next_check_without_rebinding_dated_samples(tmp_path, count):
    async def exercise():
        from orchestrator.population import MEMBER_CHECK_S

        async with producer(tmp_path) as rig:
            pixels, boxes = synthetic_slide(count)
            original = await rig.receive(pixels, warm=True)
            pop = await rig.population(original, boxes)
            await rig.show(pop)
            await rig.layer.check()
            wanted = {m.id for m in pop.live()}
            assert rig.layer.verified_count() == (count, count), 'useful real own-pixel control'
            dated = rig.layer.wire()
            _, samples, _ = member_checks(dated, pop)
            at = next(iter(samples.values()))['checkedAtMono']
            rig.publications()

            # A knob alone cannot change the actual held bitmap or its placement readers.
            rig.hub.scale.set_objective(40, via='synthetic later knob, before a new bitmap')
            assert rig.hub.latest_info.id == original.id and original.scale['objective'] == 10
            assert rig.layer.drawn_frame_reason() is None
            held_states = rig.layer.states()
            held_direct = pop.wire(held_states)
            held_app = next(w for w in rig.app.layer_wires() if w['type'] == 'population')
            rig.hub.evidence._changed(pop)  # actual notification and installed App callback, no replacement
            held_published = []
            while not rig.client.control.empty():
                row = json.loads(rig.client.control.get_nowait())
                if row.get('type') == 'population' and row['id'] == pop.id:
                    held_published.append(row)
            assert held_published
            assert held_states == {mid: 'verified' for mid in wanted}
            for wire in (held_direct, held_app, *held_published):
                assert {m['id']: m['placement'] for m in wire['members']} == held_states

            # A genuinely new 40x bitmap has the same measured epoch/offset and still
            # arrives within the original check's grace window. No Layer.check follows.
            current = await rig.receive(pixels)
            assert current.id != original.id and current.scale['objective'] == 40
            assert current.epoch == original.epoch and (current.dx, current.dy) == (original.dx, original.dy)
            assert 0 <= time.monotonic() - at < MEMBER_CHECK_S, 'exercise the immediate gate before expiry'
            why = rig.layer.drawn_frame_reason()
            assert why and 'objective changed' in why
            assert rig.layer.verified_count() == (0, count) and rig.layer.drawable(current.info) == []

            states = rig.layer.states()
            direct = pop.wire(states)
            reconnect = next(w for w in rig.app.layer_wires() if w['type'] == 'population')
            rig.hub.evidence._changed(pop)
            published = []
            while not rig.client.control.empty():
                row = json.loads(rig.client.control.get_nowait())
                if row.get('type') == 'population' and row['id'] == pop.id:
                    published.append(row)
            assert published, 'the actual App evidence_changed callback publishes this current population'
            retained = rig.layer.wire()
            assert retained['memberChecks'] == dated['memberChecks'], 'dated check facts are not the new bitmap'
            assert set(retained['verified']) == wanted
            assert original.scale['objective'] == 10 and capture_of(rig.hub, pop).jpeg == original.jpeg
            expected = {mid: 'unverified' for mid in wanted}
            assert states == expected, 'MISSING_FEATURE: current placement honors actual new-frame geometry immediately'
            for wire in (direct, reconnect, *published):
                assert {m['id']: m['placement'] for m in wire['members']} == expected, why
    asyncio.run(exercise())


@pytest.mark.parametrize('phase', ['decode', 'match'])
def test_canceled_real_member_worker_keeps_capture_accounted_until_its_cpu_return(tmp_path, monkeypatch, phase):
    import threading
    from orchestrator import population as populations

    async def exercise():
        async with producer(tmp_path) as rig:
            pixels, boxes = synthetic_slide()
            first = await rig.receive(pixels, warm=True)
            pop = await rig.population(first, boxes)
            await rig.show(pop)
            await rig.layer.check()  # templates ready; only real decode/matching remain in the next pass
            assert rig.layer.verified_count() == (6, 6)
            current = await rig.receive(pixels)
            before = copy.deepcopy(rig.layer.placement)
            view, correction = rig.layer.view, rig.layer.correction
            rig.publications()
            with rig.store.hold(first, 'analysis', 'hold', 'keeping the original positive view during cancellation'):
                assert first.gray() is not None
                memory = rig.store.memory
                baseline = memory.reserved['analysis']
                assert baseline > 0, 'the original real Capture keeps an independent reservation'
                baseline_holds = tuple(rig.store.held())
                assert not any(c is current for c in baseline_holds)
                release, finished = threading.Event(), threading.Event()
                loop, entered = asyncio.get_running_loop(), asyncio.Event()
                seen, jobs = {}, []
                real_thread = asyncio.to_thread
                real_worker = populations._half if phase == 'decode' else rig.layer._match

                def held_worker(*args, **kwargs):
                    gray = args[0]  # allocated by actual Capture.gray (decode) or actual _half (match)
                    assert isinstance(gray, np.ndarray) and gray.nbytes > 0
                    seen['bufferBytes'] = gray.nbytes
                    seen['before'] = int(gray[0, 0])
                    loop.call_soon_threadsafe(entered.set)
                    try:
                        if not release.wait(10):
                            raise RuntimeError('test did not release the real CPU member worker')
                        seen['after'] = int(gray[0, 0])  # the real buffer remains owned/used inside this worker
                        return real_worker(*args, **kwargs)
                    finally:
                        finished.set()

                async def tracked_thread(fn, *args, **kwargs):
                    # Same real executor/callable/arguments; shield preserves a joinable future
                    # after caller cancellation, not Capture coverage or a Memory reservation.
                    job = asyncio.create_task(real_thread(fn, *args, **kwargs))
                    jobs.append(job)
                    return await asyncio.shield(job)

                with monkeypatch.context() as patch:
                    patch.setattr(populations if phase == 'decode' else rig.layer,
                                  '_half' if phase == 'decode' else '_match', held_worker)
                    patch.setattr(asyncio, 'to_thread', tracked_thread)
                    task = asyncio.create_task(rig.layer.check())
                    try:
                        await asyncio.wait_for(entered.wait(), HANG_S)
                        reserved = memory.reserved['analysis']
                        half_bytes = (pixels.shape[0] // 2) * (pixels.shape[1] // 2) * pixels.dtype.itemsize
                        assert reserved == baseline + current.footprint('hold') + half_bytes
                        assert seen['bufferBytes'] == (pixels.nbytes if phase == 'decode' else half_bytes)
                        assert any(c is current for c in rig.store.held())
                        assert current.resident > len(current.jpeg), 'real decoded Capture buffer is covered'
                        assert not finished.is_set() and seen['bufferBytes'] > 0
                        task.cancel()
                        await asyncio.sleep(0)
                        await asyncio.sleep(0)  # let the real caller process cancellation while the worker stays gated
                        assert not finished.is_set()
                        assert rig.layer.placement == before and (rig.layer.view, rig.layer.correction) == (view, correction)
                        assert not rig.publications(), 'cancellation never publishes a worker result'
                        assert memory.reserved['analysis'] == reserved, 'MISSING_FEATURE: running CPU buffers retain their real reservation'
                        assert any(c is current for c in rig.store.held()), 'the actual Hold outlives caller cancellation'
                    finally:
                        release.set()  # release even when an assertion, cancellation or readiness wait fails
                        results = await asyncio.wait_for(asyncio.gather(*jobs, return_exceptions=True), 10)
                        worker_errors = [result for result in results if isinstance(result, BaseException)]
                        results.clear()
                        with suppress(asyncio.CancelledError):
                            await asyncio.wait_for(task, 10)
                        jobs.clear()  # even a RED assertion cannot retain successful worker buffers
                    assert finished.is_set() and seen['before'] == seen['after']
                    assert not worker_errors, worker_errors
                    assert memory.reserved['analysis'] == baseline
                    assert tuple(rig.store.held()) == baseline_holds
                    assert current.resident == len(current.jpeg), 'real decode coverage is eventually released'
                    assert rig.layer.placement == before and (rig.layer.view, rig.layer.correction) == (view, correction)
                    assert not rig.publications()
                await rig.layer.check()  # no scheduling gate remains; actual later work stays useful
                assert rig.layer.verified_count() == (6, 6)
                assert rig.publications()
                _, samples, _ = member_checks(rig.layer.wire(), pop)
                assert next(iter(samples.values()))['checkedBasis']['frameId'] == current.id
                assert memory.reserved['analysis'] == baseline
    asyncio.run(exercise())
