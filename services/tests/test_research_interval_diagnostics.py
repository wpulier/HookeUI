"""Synthetic camera ingress, actual fresh acquisition/manager/worker failure boundary.

No model, measurements, capture implementation or operation handler is substituted.
The proposed bounded `interval` facts are failure diagnostics, not authority to retry,
publish a measurement, extend the stall allowance or write an assistant answer.
"""
import asyncio
import json

import pytest

from orchestrator.motiontracks import tracks_of
from tests.test_motion_tracks import MotionHub, moving_slide
from tests.test_research_fresh_motion import worker
from tests.waiting import HANG_S, until_async


@pytest.mark.parametrize("change,reason", [
    ("epoch", "basis_changed"),
    ("boot", "basis_changed"),
    ("tracked", "registration_lost"),
    ("time", "nonmonotonic_time"),
])
def test_actual_interrupted_interval_facts_reach_worker_and_research_log(tmp_path, change, reason):
    async def go():
        # Bridge/registration metadata is external synthetic ingress. Its original
        # offsets remain meaningful only while the existing registration guard holds.
        hub = MotionHub(disc=False)
        image = moving_slide(seconds=.1)[0].image
        boundary = hub.add(image, 500.0, -300.0, epoch=7, wall=103.0)
        boundary.boot, boundary.seq = "bridge-original", 601
        r, manager, original, messages, replies = worker(tmp_path, hub)
        logged = []
        manager.log = logged.append
        baseline = dict(hub.captures.memory.reserved)
        task = asyncio.create_task(r.tool_run({"operation": "motion", "seconds": 1.5,
                                               "fresh": True, "why": "compare actual camera intervals"}))
        try:
            await asyncio.sleep(0)  # actual gather selects the original held boundary
            first = hub.add(image, 501.0, -300.0, epoch=7, wall=104.0)
            first.boot, first.seq = "bridge-original", 602
            await until_async(lambda: first.id in {c.id for c in hub.captures.held()}, every=1 / 120)
            assert first.id in {c.id for c in hub.captures.held()}, "real acquisition must enter before failure"
            assert not task.done()
            bad = hub.add(image, 502.0, -300.0, epoch=8 if change == "epoch" else 7,
                          wall=104.0 if change == "time" else 104.1)
            bad.seq = 603
            bad.boot = "bridge-replaced" if change == "boot" else "bridge-original"
            bad.tracked = change != "tracked"
            result, picture = await asyncio.wait_for(task, HANG_S)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        # The real refusal and release precede the new diagnostic assertion, so
        # missing diagnostics cannot be confused with absent fixture prerequisites.
        assert picture is None and result.get("error") and replies[0]["ok"] is False
        assert result.get("next") == replies[0].get("next") and result.get("next")
        assert r.runs == 0 and r.progress() == 0 and not original.ran
        assert not tracks_of(hub).items and manager.hands.busy is None
        assert not hub.captures.held() and hub.captures.memory.reserved == baseline
        assert len(messages) == len(replies) == 1
        assert messages[0]["rid"] == original.rid and messages[0]["fresh"] is True
        failures = [row for row in logged if row.get("kind") == "research-run"]
        assert len(failures) == 1 and failures[0]["ok"] is False
        assert failures[0]["rid"] == original.rid and failures[0]["operation"] == "motion"

        expected = {"reason": reason,
                    "original": {"frame": 1, "seq": 601, "epoch": 7, "tracked": True,
                                 "at": 103.0, "boot": "bridge-original"},
                    "offending": {"frame": 3, "seq": 603, "epoch": 8 if change == "epoch" else 7,
                                  "tracked": change != "tracked", "at": 104.0 if change == "time" else 104.1,
                                  "boot": "bridge-replaced" if change == "boot" else "bridge-original"}}
        assert result.get("interval") == expected, "worker must receive the actual original/refused Capture facts"
        assert replies[0].get("interval") == expected and failures[0].get("interval") == expected
        # Byte-free bounded facts must survive process-message serialization and
        # cannot become the next/latest FrameInfo after an acquisition has failed.
        assert len(json.dumps(expected)) < 1024
        boundary.epoch, boundary.boot, boundary.wall_at = 99, "later-original-mutation", 999.0
        bad.epoch, bad.boot, bad.wall_at, bad.tracked = 100, "later-offending-mutation", 1000.0, True
        hub.add(image, 900.0, 800.0, epoch=101, wall=1001.0)
        assert result["interval"] == replies[0]["interval"] == failures[0]["interval"] == expected

    asyncio.run(go())
