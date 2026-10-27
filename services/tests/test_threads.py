"""The orchestrator's own threads (orchestrator/threads.py): OpenCV's count and the frame path's scheduling class."""
import sys
import threading

import pytest

from orchestrator import threads


def test_opencvs_threads_follow_the_choice_and_its_override(monkeypatch):
    monkeypatch.delenv("MICROMIND_ORCH_CV_THREADS", raising=False)
    assert threads.cv_threads() == threads.CV_THREADS
    monkeypatch.setenv("MICROMIND_ORCH_CV_THREADS", "3")
    assert threads.cv_threads() == 3
    monkeypatch.setenv("MICROMIND_ORCH_CV_THREADS", "")
    assert threads.cv_threads() == threads.CV_THREADS
    monkeypatch.setenv("MICROMIND_ORCH_CV_THREADS", "-1")
    assert threads.cv_threads() is None  # OpenCV's own default


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS scheduling classes")
def test_a_frame_path_thread_asks_for_its_scheduling_class(monkeypatch):
    monkeypatch.setenv("MICROMIND_FRAME_QOS", "interactive")
    asked = []
    t = threading.Thread(target=lambda: asked.append(threads.raise_this_thread()))
    t.start()
    t.join()
    assert asked == ["interactive"]
    monkeypatch.setenv("MICROMIND_FRAME_QOS", "")
    assert threads.raise_this_thread() == ""
