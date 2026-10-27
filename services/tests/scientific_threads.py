"""Thread isolation for synthetic worker tests hosted inside pytest/xdist.

Only threads present before the case are excluded. Threads started by the case
still reach the real production quiescence guard; native workers use it unchanged.
"""
import sys
import threading

import pytest


@pytest.fixture(autouse=True)
def exclude_preexisting_test_harness_threads(monkeypatch):
    frames = sys._current_frames
    harness = set(frames()) - {threading.get_ident()}
    monkeypatch.setattr(sys, '_current_frames', lambda: {key: value for key, value in frames().items()
                                                       if key not in harness})
