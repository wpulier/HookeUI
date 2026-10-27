"""The real hub lifecycle checks a returned outline before global motion settles.

Synthetic decoded-camera pixels and measured frame facts enter at the frame-source
boundary; actual matching, scheduled rechecks and scene writes run without a GPU.
This is a scheduling/refusal regression, not camera or model qualification.
"""
import asyncio
import copy
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator.frames import Frame
from orchestrator.hub import FrameInfo, Hub
from orchestrator.scene import SceneObject, capture_template
from tests.waiting import until_async


@pytest.mark.parametrize('condition,expected', [('same-pixels', 'tracking'), ('wrong-place', 'refinding'),
                                                ('other-epoch', 'refinding'), ('blurred', 'refinding'),
                                                # its matching slowed as on a loaded host (the 2026-10-15 gate)
                                                ('same-pixels-slow-worker', 'tracking')])
def test_unsettled_view_can_only_restore_an_outline_verified_at_its_unchanged_place(condition, expected):
    async def check():
        gray = cv2.GaussianBlur(np.random.default_rng(28).integers(0, 255, (240, 320), dtype=np.uint8), (0, 0), 1)
        obj = SceneObject('o1', 'highlight', [[[130, 90], [150, 90], [150, 110], [130, 110]]],
                          1, 0, (0., 0.), 'click', track='refinding', lost_since=time.time())
        obj.template, obj.template_origin = capture_template(gray, obj.bbox)
        if condition == 'wrong-place':
            gray = np.roll(gray, 24, axis=1)
        elif condition == 'blurred':
            gray = cv2.GaussianBlur(gray, (0, 0), 12)
        elif condition == 'other-epoch':
            obj.anchor_epoch = 1
        before = copy.deepcopy((obj.polygons, obj.template_origin, obj.anchor_frame, obj.anchor_offset))
        jpeg = cv2.imencode('.jpg', gray)[1].tobytes()
        frame = Frame(2, jpeg, time.monotonic(), time.time())
        hub = Hub('unused', use_gpu=False)
        if condition == 'same-pixels-slow-worker':
            match = hub._reanchor_from

            def slow(*args, **kwargs):  # the decode and match on its worker thread, held up as a busy host does
                time.sleep(.3)
                return match(*args, **kwargs)

            hub._reanchor_from = slow

        async def stop_source():
            pass

        # A fresh source with unsettled motion, as the retained focus-recovery frames.
        hub.source = SimpleNamespace(start=lambda: None, stop=stop_source, is_fresh=lambda: True,
                                     get=lambda fid: frame)
        hub.latest_info = FrameInfo(2, 320, 240, 0., 0., 0, True, False, None, time.time())
        hub.scene.add(obj)
        carried = []
        hub.on_carry.append(lambda *event: carried.append(event))
        await hub.start()

        # The scheduled check's own completion, not a wall-clock guess: it sleeps 300 ms, then decodes and matches on a
        # worker thread, which a loaded host can delay past any fixed sleep (the 2026-10-15 gate: still 'refinding'
        # after 0.4 s). The re-anchor pass reads the follower once per pass (no other loop does), so a follower that
        # follows nothing, as None does, counts them: its second read means the first pass, decode and writes included,
        # is over. It also proves the refusals below come from a completed pass.
        passes = []

        class Counting:
            @property
            def placed_recently(self):
                passes.append(time.monotonic())
                return lambda oid: False

        hub.follower = Counting()
        try:
            await until_async(lambda: len(passes) >= 2, every=.02)
            assert len(passes) >= 2, 'the scheduled check never completed a pass'  # no frame arrives: no stillness
            assert obj.track == expected
            assert (obj.polygons, obj.template_origin, obj.anchor_frame, obj.anchor_offset) == before
            assert bool(carried) == (expected == 'tracking')
        finally:
            await hub.stop()
            hub._analysis.shutdown(wait=True)
            hub._background.shutdown(wait=True)

    asyncio.run(check())
