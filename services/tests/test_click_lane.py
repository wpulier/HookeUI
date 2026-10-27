"""Clicks ahead of background inference on the other workers (gpu_worker/lane.py, MICROMIND_CLICK_LANE)."""
import threading
import time

from gpu_worker import lane as lanes
from gpu_worker.inbox import Inbox
from gpu_worker.lane import Lane


def test_a_click_marked_by_one_worker_is_waited_for_by_the_others_never_by_itself(tmp_path):
    where = tmp_path / "lane"
    main, heavy, follow = Lane(where, role="main"), Lane(where, role="heavy"), Lane(where, role="follow")
    assert heavy.others() == 0
    main.enter()
    assert heavy.others() == 1 and follow.others() == 1 and main.others() == 0
    main.enter()
    main.leave()
    assert heavy.others() == 1
    main.leave()
    main.leave()  # never below none
    assert heavy.others() == 0 and main.own == 0


def test_a_stale_mark_is_not_waited_for_and_a_restarted_worker_starts_with_none(tmp_path):
    where = tmp_path / "lane"
    main, heavy = Lane(where, role="main"), Lane(where, role="heavy")
    main.enter()  # and then the click worker went away mid-click
    assert heavy.others(now=time.time() + lanes.STALE_S + 1) == 0
    Lane(where, role="main")  # the click worker started again
    assert heavy.others() == 0


def test_wait_gives_way_while_a_click_is_answered_and_at_most_its_limit(tmp_path):
    where = tmp_path / "lane"
    main, heavy = Lane(where, role="main"), Lane(where, role="heavy")
    assert heavy.wait() == 0.0
    main.enter()
    assert 0.05 <= heavy.wait(limit_s=0.05) < 0.5  # held: it waits its limit, then goes on
    threading.Timer(0.05, main.leave).start()
    waited = heavy.wait(limit_s=2.0)
    assert 0.03 <= waited < 1.0  # it went on once the click was answered
    assert heavy.waited_s >= 0.08


def test_a_network_waits_before_each_forward(tmp_path):
    where = tmp_path / "lane"
    main, heavy = Lane(where, role="main"), Lane(where, role="heavy")
    hooks = []

    class Net:
        def register_forward_pre_hook(self, hook):
            hooks.append(hook)

    assert heavy.hook(Net()) and not heavy.hook(None)
    main.enter()
    t0 = time.monotonic()
    assert hooks[0](None, ()) is None  # a pre-hook that returns None leaves the forward's input as it is
    assert time.monotonic() - t0 >= lanes.WAIT_S * 0.9


def test_the_lane_is_an_option_and_off_is_as_before(tmp_path, monkeypatch):
    assert lanes.mode({}) == lanes.DEFAULT
    assert lanes.mode({"MICROMIND_CLICK_LANE": "all"}) == "all"
    assert lanes.mode({"MICROMIND_CLICK_LANE": "nonsense"}) == lanes.DEFAULT
    assert lanes.opened("heavy", {"MICROMIND_CLICK_LANE": "off"}) is None
    monkeypatch.setattr(lanes.tempfile, "gettempdir", lambda: str(tmp_path))
    opened = lanes.opened("heavy", {"MICROMIND_CLICK_LANE": "cells"})
    assert opened is not None and opened.how == "cells" and opened.slot == lanes.SLOTS["heavy"]
    assert lanes.path(123).name == "micromind-click-lane-123"


def test_the_inbox_says_which_requests_are_clicks():
    inbox = Inbox({"ping", "segment"})
    assert inbox.klass({"op": "segment", "_priority": "click"}) == 0
    assert inbox.klass({"op": "segment"}) == 0  # an urgent op that does not say
    assert inbox.klass({"op": "segment", "_priority": "interactive"}) == 1
    assert inbox.klass({"op": "cells"}) == 2


def test_the_files_of_apps_no_longer_running_are_tidied(tmp_path):
    import os
    import subprocess
    import sys

    gone = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    dead = tmp_path / f"micromind-click-lane-{int(gone.stdout)}"
    alive = tmp_path / f"micromind-click-lane-{os.getpid()}"
    other = tmp_path / "micromind-click-lane-notapid"
    for f in (dead, alive, other):
        f.write_bytes(b"\0" * 8)
    assert lanes.tidy(tmp_path) == [dead]
    assert alive.exists() and other.exists() and not dead.exists()
