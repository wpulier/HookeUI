"""TEST-only template lifetime accounting on accepted integration 072d7533.

Pixels, bridge metadata and masks are SYNTHETIC; unchanged Producer/App intake,
Captures/Holds, saved Evidence, Layer copies and matching are real. No model,
browser, network lifespan, source repair or new production ledger is supplied.
Root owns fidelity review, execution and the first RED.

Scheduling seams delegate original _half/_match and asyncio.to_thread unchanged.
Only the _templates CPU thread's original Python line events gate real copies.
Observers retain scalar buffer facts and actual Reservation objects, never extra
pixel aliases, successful worker results, substituted arrays or made-up counters.
"""
import asyncio
import copy
import inspect
import sys
import threading
from contextlib import asynccontextmanager, contextmanager, suppress

import numpy as np
import pytest

from orchestrator import population as populations
from orchestrator.population import correct
from tests.test_live_member_producer_lineage import member_checks, producer, synthetic_slide
from tests.waiting import HANG_S


@contextmanager
def reservation_observer(monkeypatch, memory):
    """Observe this actual Memory's admissions and real once-only retirement transitions."""
    records = []
    original_reserve = memory.reserve
    with monkeypatch.context() as patch:
        def reserve(*args, **kwargs):
            reservation = original_reserve(*args, **kwargs)
            record = {'reservation': reservation, 'retirements': []}
            records.append(record)
            original_release = reservation.release

            def release():
                was_released, byte_count = reservation.released, reservation.nbytes
                original_release()  # Real _free/settle/grow are never replaced or edited.
                if not was_released and reservation.released:
                    record['retirements'].append(byte_count)

            patch.setattr(reservation, 'release', release)
            return reservation

        patch.setattr(memory, 'reserve', reserve)
        yield records


def active_analysis(records):
    return [record for record in records
            if record['reservation'].cls == 'analysis' and not record['reservation'].released]


def cache_facts(layer):
    """Read the actual cache, keeping only scalar IDs/counts outside this function."""
    assert layer.templates
    arrays = [entry[0] for entry in layer.templates.values()]
    assert all(isinstance(array, np.ndarray) and array.nbytes > 0 and not array.flags.writeable
               for array in arrays)
    assert len({id(array) for array in arrays}) == len(arrays)
    assert all(not np.shares_memory(a, b) for n, a in enumerate(arrays) for b in arrays[n + 1:])
    return {'ids': tuple(id(array) for array in arrays), 'bytes': sum(array.nbytes for array in arrays),
            'samples': tuple(int(array.flat[0]) for array in arrays)}


class CpuGate:
    def __init__(self, layer, phase=None):
        self.layer, self.phase = layer, phase
        self.loop = asyncio.get_running_loop()
        self.entered = asyncio.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.observed = {}
        self.paused = False
        self.template_calls = 0
        self.template_thread = None
        self.stage_line = None
        self.original_templates = layer._templates
        self.code = self.original_templates.__func__.__code__
        lines, first = inspect.getsourcelines(self.original_templates)
        # Locate exact original statements, not hardcoded line numbers after a later repair.
        self.stages = {}
        for name, statement in [('before-copy', 'template = gray[oy:ey, ox:ex].copy()'),
                                ('after-copy', 'template.setflags(write=False)')]:
            locations = [first + n for n, line in enumerate(lines) if line.strip() == statement]
            assert len(locations) == 1, 'the approved original template-copy seam must be identifiable'
            self.stages[name] = locations[0]

    def pause(self, gray, templates=(), full=None):
        assert not self.paused
        self.paused = True
        assert isinstance(gray, np.ndarray) and gray.nbytes > 0
        assert all(isinstance(array, np.ndarray) and array.nbytes > 0 for array in templates)
        assert all(not np.shares_memory(gray, array) for array in templates)
        assert len({id(array) for array in templates}) == len(templates)
        assert all(not np.shares_memory(a, b) for n, a in enumerate(templates) for b in templates[n + 1:])
        before = (int(gray.flat[0]), tuple(int(array.flat[0]) for array in templates),
                  int(full.flat[0]) if full is not None else None)
        self.observed = {'grayBytes': gray.nbytes, 'templateBytes': sum(array.nbytes for array in templates),
                         'templateIds': tuple(id(array) for array in templates), 'copies': len(templates),
                         'fullBytes': full.nbytes if full is not None else 0, 'before': before, 'line': self.stage_line}
        self.loop.call_soon_threadsafe(self.entered.set)
        if not self.release.wait(10):
            raise RuntimeError('test did not release the genuine CPU template reader')
        self.observed['after'] = (int(gray.flat[0]), tuple(int(array.flat[0]) for array in templates),
                                  int(full.flat[0]) if full is not None else None)
        assert self.observed['before'] == self.observed['after']

    def run(self, fn, *args, **kwargs):
        if getattr(fn, '__name__', '') != '_templates':
            return fn(*args, **kwargs)
        self.template_calls += 1
        self.template_thread = threading.get_ident()
        previous = sys.gettrace()

        def trace(frame, event, arg):
            if frame.f_code is not self.code:
                return previous(frame, event, arg) if previous is not None else None
            if event == 'line' and not self.paused:
                local = frame.f_locals
                out = local.get('out')
                if isinstance(out, dict):
                    at_copy = frame.f_lineno == self.stages['before-copy']
                    after_copy = frame.f_lineno == self.stages['after-copy']
                    first_copy = self.phase == 'first-copy' and after_copy and len(out) == 0
                    later_copy = self.phase == 'later-copy' and after_copy and len(out) == 2
                    if (self.phase == 'before-copy' and at_copy and not out) or first_copy or later_copy:
                        arrays = tuple(entry[0] for entry in out.values())
                        if after_copy:
                            arrays += (local['template'],)  # Actual new .copy(), not yet inserted into out.
                        self.stage_line = frame.f_lineno
                        self.pause(local['gray'], arrays)
            return trace

        try:
            if self.phase in ('before-copy', 'first-copy', 'later-copy'):
                sys.settrace(trace)  # Only this executing worker/thread, only the original function code.
            return fn(*args, **kwargs)
        finally:
            sys.settrace(previous)
            self.template_thread = None
            self.finished.set()


@asynccontextmanager
async def scheduling(monkeypatch, gate):
    """Keep genuine executor work joinable after cancellation; no pixel lifetime inferred from Task.done."""
    original_thread = asyncio.to_thread
    original_half, original_match = populations._half, gate.layer._match
    jobs = set()

    def half(gray):
        result = original_half(gray)
        if gate.phase == 'decode' and threading.get_ident() == gate.template_thread and not gate.paused:
            assert isinstance(result, np.ndarray) and not np.shares_memory(gray, result)
            gate.pause(result, full=gray)  # Both actual original decode and actual half are still in use.
        return result

    def match(gray, shift, now, work):
        try:
            if gate.phase == 'match' and not gate.paused:
                gate.pause(gray, tuple(entry[0] for _, entry in work.templates))
            return original_match(gray, shift, now, work)
        finally:
            if gate.phase == 'match':
                gate.finished.set()

    async def to_thread(fn, *args, **kwargs):
        if getattr(fn, '__self__', None) is not gate.layer:
            return await original_thread(fn, *args, **kwargs)
        job = asyncio.create_task(original_thread(gate.run, fn, *args, **kwargs))
        jobs.add(job)
        try:
            return await asyncio.shield(job)
        finally:
            if job.done():
                jobs.discard(job)  # Do not retain successfully delivered template results.

    with monkeypatch.context() as patch:
        patch.setattr(populations, '_half', half)
        patch.setattr(gate.layer, '_match', match)
        patch.setattr(asyncio, 'to_thread', to_thread)
        try:
            yield
        finally:
            gate.release.set()
            results = await asyncio.wait_for(asyncio.gather(*jobs, return_exceptions=True), 10)
            errors = [repr(result) for result in results if isinstance(result, BaseException)]
            results.clear()
            jobs.clear()
            assert not errors, errors  # Retain actual worker failures; never count one as successful refusal.


@asynccontextmanager
async def running_check(monkeypatch, gate):
    async with scheduling(monkeypatch, gate):
        task = asyncio.create_task(gate.layer.check())
        try:
            await asyncio.wait_for(gate.entered.wait(), HANG_S)
            assert gate.paused and not gate.finished.is_set()
            yield task
        finally:
            gate.release.set()
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(task, 10)


async def saved_fixture(rig):
    pixels, boxes = synthetic_slide()
    original = await rig.receive(pixels, warm=True)
    pop = await rig.population(original, boxes)
    assert pop.capture is None and pop.path is not None
    assert rig.hub.evidence.capture_jpeg(pop) == original.jpeg
    assert (pop.path / 'capture.jpg').read_bytes() == original.jpeg
    await rig.show(pop)
    current = await rig.receive(pixels)
    assert current is not original and current.id != original.id
    await rig.layer.check()
    assert rig.layer.verified_count() == (6, 6), 'useful real saved-source matching is a setup prerequisite'
    member_checks(rig.layer.wire(), pop)
    await rig.show(pop)  # Reset the actual cache/lifetime through its owner before the cold test.
    assert rig.layer.templates == {} and rig.layer.templates_of is None
    return pixels, boxes, original, pop, current


def source_unchanged(rig, original, pop, jpeg, scale):
    assert original.jpeg == jpeg and original.scale == scale
    assert (pop.path / 'capture.jpg').read_bytes() == jpeg
    assert rig.hub.evidence.capture_jpeg(pop) == jpeg and pop.scale_id == scale


def baseline_intact(rig, original, hold, baseline):
    assert not hold.released and not hold.reservation.released
    assert rig.store.memory.reserved['analysis'] >= baseline > 0
    assert any(capture is original for capture in rig.store.held())
    assert original.resident > len(original.jpeg), 'the independent original decode remains covered'


def test_actual_original_decode_and_half_have_useful_temporary_coverage(tmp_path, monkeypatch):
    async def exercise():
        async with producer(tmp_path) as rig:
            _, _, original, pop, _ = await saved_fixture(rig)
            with rig.store.hold(original, 'analysis', 'hold', 'independent original template baseline') as hold:
                assert original.gray() is not None
                baseline = rig.store.memory.reserved['analysis']
                gate = CpuGate(rig.layer, 'decode')
                async with running_check(monkeypatch, gate):
                    seen = gate.observed
                    assert seen['fullBytes'] > seen['grayBytes'] > 0 and seen['copies'] == 0
                    baseline_intact(rig, original, hold, baseline)
                    assert rig.store.memory.reserved['analysis'] >= baseline + seen['fullBytes'] + seen['grayBytes']
                assert gate.finished.is_set() and gate.observed['before'] == gate.observed['after']
                assert rig.layer.verified_count() == (6, 6), 'real cold copies/matching remain useful'
                member_checks(rig.layer.wire(), pop)
    asyncio.run(exercise())


@pytest.mark.parametrize('phase,copies', [('before-copy', 0), ('first-copy', 1), ('later-copy', 3)])
def test_real_copy_stages_remain_accounted_after_cancel_and_hide(tmp_path, monkeypatch, phase, copies):
    async def exercise():
        async with producer(tmp_path) as rig:
            _, _, original, pop, _ = await saved_fixture(rig)
            jpeg, scale = original.jpeg, copy.deepcopy(original.scale)
            with rig.store.hold(original, 'analysis', 'hold', 'independent original during template copies') as hold:
                assert original.gray() is not None
                memory, baseline = rig.store.memory, rig.store.memory.reserved['analysis']
                gate = CpuGate(rig.layer, phase)
                async with running_check(monkeypatch, gate) as task:
                    seen = gate.observed
                    assert seen['copies'] == copies and seen['grayBytes'] > 0
                    assert (seen['templateBytes'] > 0) is (copies > 0)
                    assert seen['line'] == gate.stages['before-copy' if copies == 0 else 'after-copy']
                    reserved_while_running = memory.reserved['analysis']
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task  # The caller is genuinely cancelled while its CPU still reads pixels.
                    rig.layer.hide()
                    placement, view, correction = copy.deepcopy(rig.layer.placement), rig.layer.view, rig.layer.correction
                    rig.publications()
                    source_unchanged(rig, original, pop, jpeg, scale)
                    baseline_intact(rig, original, hold, baseline)
                    assert not gate.finished.is_set()
                    assert memory.reserved['analysis'] == reserved_while_running, 'cancel/hide cannot retire active CPU pixels'
                    assert memory.reserved['analysis'] >= baseline + seen['grayBytes'] + seen['templateBytes'], \
                        'MISSING_FEATURE: actual half/copy allocations stay reserved until CPU use finishes'
                assert gate.finished.is_set() and gate.observed['before'] == gate.observed['after']
                assert rig.layer.shown is None and rig.layer.templates == {}
                assert rig.layer.placement == placement and (rig.layer.view, rig.layer.correction) == (view, correction)
                assert not rig.publications(), 'obsolete cancelled template work cannot install or publish'
                assert memory.reserved['analysis'] == baseline
                baseline_intact(rig, original, hold, baseline)
                await rig.show(pop)
                await rig.layer.check()
                assert rig.layer.verified_count() == (6, 6), 'later ungated CPU work remains useful'
                member_checks(rig.layer.wire(), pop)
    asyncio.run(exercise())


def test_cold_templates_remain_reserved_and_warm_check_reuses_the_actual_cache(tmp_path, monkeypatch):
    async def exercise():
        async with producer(tmp_path) as rig:
            _, _, original, pop, _ = await saved_fixture(rig)
            with rig.store.hold(original, 'analysis', 'hold', 'independent original during cache reuse') as hold:
                assert original.gray() is not None
                memory, baseline = rig.store.memory, rig.store.memory.reserved['analysis']
                gate = CpuGate(rig.layer)
                with reservation_observer(monkeypatch, memory) as records:
                    async with scheduling(monkeypatch, gate):
                        await rig.layer.check()
                        assert rig.layer.verified_count() == (6, 6)
                        cold = cache_facts(rig.layer)
                        resident = memory.reserved['analysis']
                        owners = active_analysis(records)
                        await rig.layer.check()
                        assert rig.layer.verified_count() == (6, 6)
                        assert cache_facts(rig.layer) == cold and gate.template_calls == 1
                        assert memory.reserved['analysis'] == resident, 'a warm pass cannot add another cache reservation'
                        assert active_analysis(records) == owners
                        member_checks(rig.layer.wire(), pop)
                        baseline_intact(rig, original, hold, baseline)
                        assert resident >= baseline + cold['bytes'], 'MISSING_FEATURE: resident real template copies are reserved'
                        assert owners and sum(record['reservation'].nbytes for record in owners) >= cold['bytes']
                        await rig.show(pop)  # Actual replacement clears cache interest; no test clears arrays/counters.
                        assert memory.reserved['analysis'] == baseline
                        assert all(record['reservation'].released and len(record['retirements']) == 1 for record in owners)
                        await rig.show(pop)
                        assert memory.reserved['analysis'] == baseline
                        assert all(len(record['retirements']) == 1 for record in owners)
    asyncio.run(exercise())


@pytest.mark.parametrize('change', ['hide', 'replacement', 'revision'])
def test_real_match_alias_outlives_cache_authority_and_retires_after_last_reader(tmp_path, monkeypatch, change):
    async def exercise():
        async with producer(tmp_path) as rig:
            pixels, boxes, original, pop, current = await saved_fixture(rig)
            other = await rig.population(current, boxes)
            jpeg, scale = original.jpeg, copy.deepcopy(original.scale)
            with rig.store.hold(original, 'analysis', 'hold', 'independent original during alias retirement') as hold:
                assert original.gray() is not None
                memory, baseline = rig.store.memory, rig.store.memory.reserved['analysis']
                with reservation_observer(monkeypatch, memory) as records:
                    await rig.layer.check()
                    assert rig.layer.verified_count() == (6, 6), 'useful real cache before alias race'
                    cached = cache_facts(rig.layer)
                    owners = active_analysis(records)  # Actual admissions; empty on RED is not replaced.
                    current = await rig.receive(pixels)
                    gate = CpuGate(rig.layer, 'match')
                    async with running_check(monkeypatch, gate):
                        assert gate.observed['templateIds'] == cached['ids'] and gate.observed['templateBytes'] == cached['bytes']
                        assert current.resident > len(current.jpeg) and any(c is current for c in rig.store.held())
                        if change == 'hide':
                            rig.layer.hide()  # Hide may keep a counted cache or retire it; both are allowed.
                        elif change == 'replacement':
                            await rig.show(other)
                        else:
                            removed = next(iter(pop.members))
                            with rig.hub.evidence.keeping_pixels(pop):
                                _, gone = correct(rig.hub, pop, 'remove', [removed], by='synthetic alias correction')
                            assert removed in gone and pop.revision == 1
                            await rig.saved(pop)
                        state = (rig.layer.shown, copy.deepcopy(rig.layer.placement), rig.layer.view, rig.layer.correction)
                        rig.publications()
                        source_unchanged(rig, original, pop, jpeg, scale)
                        baseline_intact(rig, original, hold, baseline)
                        assert not gate.finished.is_set()
                        assert memory.reserved['analysis'] >= baseline + current.footprint('hold') + cached['bytes'], \
                            'MISSING_FEATURE: obsolete real matcher aliases keep their template bytes reserved'
                        assert owners and all(not record['reservation'].released for record in owners)
                    assert gate.finished.is_set() and gate.observed['before'] == gate.observed['after']
                    assert (rig.layer.shown, rig.layer.placement, rig.layer.view, rig.layer.correction) == state
                    assert not rig.publications(), 'obsolete actual CPU result cannot publish'
                    assert current.resident == len(current.jpeg), 'matcher Capture coverage retires separately'
                    if change == 'hide':
                        remaining = cache_facts(rig.layer)['bytes'] if rig.layer.templates else 0
                        assert memory.reserved['analysis'] >= baseline + remaining
                        await rig.show(other)  # Ensure any legitimately hidden cache loses its final real interest.
                    assert memory.reserved['analysis'] == baseline
                    assert all(record['reservation'].released and len(record['retirements']) == 1 for record in owners)
                    await rig.show(other)
                    assert memory.reserved['analysis'] == baseline
                    assert all(len(record['retirements']) == 1 for record in owners)
                    baseline_intact(rig, original, hold, baseline)
                    target = pop if change == 'revision' else other
                    await rig.show(target)
                    await rig.layer.check()
                    expected = 5 if change == 'revision' else 6
                    assert rig.layer.verified_count() == (expected, expected), 'later real matching remains useful'
                    _, _, rows = member_checks(rig.layer.wire(), target)
                    assert set(rows) == {member.id for member in target.live()}
    asyncio.run(exercise())
