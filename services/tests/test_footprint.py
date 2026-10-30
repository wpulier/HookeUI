"""What a process really holds (gpu_worker/footprint.py): the kernel's physical footprint, which a load's size and
peak are measured by (docs/design/2026-10-11-model-runtime.md)."""
import mmap
import os
import sys

import pytest

from gpu_worker.footprint import Peak, footprint_mb, peak_mb
from tests.waiting import until

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="the footprint is macOS's count")


def touched(mb: int) -> mmap.mmap:
    """Memory the process holds until closed (mapped and written, so every page counts; closing gives it back, which
    Python's own allocator does not promise)."""
    block = mmap.mmap(-1, mb * 2**20)
    for i in range(0, len(block), mmap.PAGESIZE):
        block[i] = 1
    return block


def test_the_footprint_counts_what_the_process_holds_and_its_peak_what_it_held():
    before = footprint_mb()
    block = touched(200)
    held = footprint_mb()
    assert before is not None and held - before > 150
    block.close()
    assert footprint_mb() < held - 150 and peak_mb() >= held - 1


def test_a_peak_sees_what_a_block_held_and_let_go_of_before_it_ended():
    with Peak(every_s=0.01) as peak:
        start = peak.mb
        block = touched(200)
        try:
            until(lambda: peak.mb - start > 150, what="background sampler observes the held allocation")
        finally:
            block.close()
    assert peak.mb - start > 150 and peak.mb - footprint_mb() > 150


def test_another_process_is_read_by_its_pid_and_one_gone_is_none():
    assert footprint_mb(os.getppid()) is not None
    assert footprint_mb(2**22 + 12345) is None
