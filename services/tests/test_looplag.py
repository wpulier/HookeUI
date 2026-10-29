"""The event loop's lateness, measured from outside it, with the loop's own stack (orchestrator/looplag.py)."""
import asyncio
import time

from orchestrator.looplag import LIMIT_MS, LoopLag


def stuck_on_purpose(observed):
    from tests.waiting import HANG_S
    # Keep the real loop blocked until the monitor has sampled its stack. A fixed
    # 450 ms sleep can finish before the daemon gets CPU on a loaded CI runner.
    assert observed.wait(HANG_S), "the loop monitor did not sample the deliberate stall"


def test_a_stall_is_logged_with_what_the_loop_was_doing(monkeypatch):
    import threading
    from orchestrator import looplag
    from tests.waiting import HANG_S

    observed = threading.Event()
    original = looplag.stack_of

    def capture(thread):
        stack = original(thread)
        if "stuck_on_purpose" in stack:
            observed.set()
        return stack

    monkeypatch.setattr(looplag, "stack_of", capture)

    async def go():
        logged = []
        completed = asyncio.Event()
        def record(value):
            logged.append(value)
            completed.set()
        lag = LoopLag(log=record, readings=lambda: {"load1": 142})
        lag.start(asyncio.get_running_loop())
        try:
            await asyncio.sleep(0.2)
            assert logged == []  # an unblocked loop must not report a stall
            stuck_on_purpose(observed)
            await asyncio.wait_for(completed.wait(), HANG_S)
        finally:
            lag.stop()
        return logged, lag

    logged, lag = asyncio.run(go())
    assert len(logged) == 1 and logged[0]["kind"] == "loop-lag" and logged[0]["ms"] >= LIMIT_MS
    assert "stuck_on_purpose" in logged[0]["stacks"][0]  # taken while it was stuck, not after
    assert logged[0]["machine"] == {"load1": 142}
    assert lag.wire()["count"] == 1 and lag.wire()["worstMs"] >= LIMIT_MS


def test_first_use_imports_are_made_on_a_thread_of_their_own():
    """Live 2026-10-11 the loop stalled ~240 ms on scipy.ndimage's first import (the first cell measurement) and on
    openai's live types (the first voice session after a restart): they are imported off the loop, in a fresh
    interpreter here so nothing the tests imported before counts."""
    import subprocess
    import sys

    code = (
        "import sys, threading\n"
        "from orchestrator.looplag import WARM_IMPORTS, warm_imports\n"
        "assert not [m for m in WARM_IMPORTS if m in sys.modules], 'imported already'\n"
        "where = {}\n"
        "class Seen:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        where.setdefault(name, threading.current_thread().name)\n"
        "sys.meta_path.insert(0, Seen())\n"
        "thread = warm_imports()\n"
        "assert thread.daemon and thread.name == 'warm-imports'\n"
        "thread.join(60)\n"
        "assert all(m in sys.modules for m in WARM_IMPORTS), [m for m in WARM_IMPORTS if m not in sys.modules]\n"
        "assert {where[m] for m in WARM_IMPORTS} == {'warm-imports'}, where\n"
        "assert where['scipy.special'] == 'warm-imports'\n"
    )
    from pathlib import Path

    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                          cwd=Path(__file__).resolve().parents[1])
    assert done.returncode == 0, done.stderr[-2000:]


def test_the_app_makes_them_first_thing_at_start(tmp_path, monkeypatch):
    import pytest

    from orchestrator import looplag
    from tests.standin_workers import build_app

    class Started(Exception):
        pass

    def warm(modules=looplag.WARM_IMPORTS):
        raise Started(modules)

    app = build_app(tmp_path)
    monkeypatch.setattr(looplag, "warm_imports", warm)
    with pytest.raises(Started) as started:
        asyncio.run(app.start())  # stopped right there: nothing else of the app is started
    assert started.value.args[0] == ("scipy.ndimage", "openai", "openai.resources.live", "google.genai",
                                     "google.genai.types")


def test_lines_from_the_loop_are_written_by_the_writer_in_order_and_whole(tmp_path):
    """L1b item 5: a line logged on the loop is handed to the writer thread; lines reach each file in the order they
    were handed in, whole, from the loop and from other threads at once, and `flush` leaves none waiting."""
    import json
    import threading

    from orchestrator.looplag import Lines

    lines = Lines()
    one, two = tmp_path / "logs" / "one.jsonl", tmp_path / "two.jsonl"

    def from_a_thread(name):
        for i in range(300):
            lines.append(one, json.dumps({"who": name, "i": i}))  # off the loop: on disk when it returns

    async def go():
        threads = [threading.Thread(target=from_a_thread, args=(f"t{n}",)) for n in range(3)]
        for t in threads:
            t.start()
        for i in range(2000):
            lines.append(one if i % 2 else two, json.dumps({"who": "loop", "i": i, "pad": "x" * (i % 97)}))
            if i % 100 == 0:
                await asyncio.sleep(0)
        for t in threads:
            t.join()

    asyncio.run(go())
    lines.flush()
    records = [json.loads(raw) for raw in one.read_text().splitlines()] + \
        [json.loads(raw) for raw in two.read_text().splitlines()]  # a cut or interleaved line would not parse
    assert len(records) == 2000 + 900 and lines.pending() == 0
    assert lines.wire() == {"handed": 2900, "written": 2900, "pending": 0, "failed": 0}
    for who in ("loop", "t0", "t1", "t2"):
        mine = [r["i"] for r in records if r["who"] == who and (who != "loop" or r["i"] % 2)]
        assert mine == sorted(mine), who  # never reordered within a file
    assert [r["i"] for r in records if r["who"] == "loop" and r["i"] % 2 == 0] == list(range(0, 2000, 2))


def test_a_line_on_the_loop_never_waits_for_the_disk(tmp_path, monkeypatch):
    """The loop hands the line over and goes on while a slow disk writes it; off the loop it is written first."""
    from orchestrator.looplag import Lines

    lines = Lines()
    real = Lines._write

    def slow(self, batch):
        time.sleep(0.3)
        real(self, batch)

    monkeypatch.setattr(Lines, "_write", slow)
    path = tmp_path / "slow.jsonl"

    async def go():
        t0 = time.perf_counter()
        lines.append(path, "{}")
        return time.perf_counter() - t0

    assert asyncio.run(go()) < 0.1
    lines.flush()
    assert path.read_text() == "{}\n"
    t0 = time.perf_counter()
    lines.append(path, "{}")  # no loop here: written before append returns, as before
    assert time.perf_counter() - t0 >= 0.3 and path.read_text() == "{}\n{}\n"


def test_on_loop_keeps_todays_way(tmp_path, monkeypatch):
    """MICROMIND_LOOP_WORK=on-loop: the line is written where it is logged, so the two ways can be measured."""
    from orchestrator.looplag import Lines

    monkeypatch.setenv("MICROMIND_LOOP_WORK", "on-loop")
    lines, path = Lines(), tmp_path / "inline.jsonl"

    async def go():
        lines.append(path, "{}")
        return path.read_text()

    assert asyncio.run(go()) == "{}\n" and lines._thread is None


def test_a_line_that_cannot_be_written_is_counted_and_the_next_ones_still_are(tmp_path):
    from orchestrator.looplag import Lines

    lines = Lines()
    (tmp_path / "a-folder").mkdir()
    lines.append(tmp_path / "a-folder", "{}")  # a folder cannot be appended to
    lines.append(tmp_path / "ok.jsonl", "{}")
    assert lines.failed == 1 and "a-folder" in lines.error and lines.wire()["written"] == 1
    assert (tmp_path / "ok.jsonl").read_text() == "{}\n"


def test_lines_still_waiting_at_exit_are_written(tmp_path):
    """Lines handed in on the loop just before the process ends are in the file (looplag's atexit flush)."""
    import subprocess
    import sys
    from pathlib import Path

    path = tmp_path / "exit.jsonl"
    code = (
        "import asyncio, sys, time\n"
        "from orchestrator.looplag import LINES, Lines\n"
        "real = Lines._write\n"
        "def slow(self, batch):\n"
        "    time.sleep(0.01)\n"
        "    real(self, batch)\n"
        "Lines._write = slow\n"
        "async def go():\n"
        "    for i in range(500):\n"
        "        LINES.append(sys.argv[1], str(i))\n"
        "    assert LINES.pending() > 0, 'all written already'\n"
        "asyncio.run(go())\n"
    )
    done = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=60,
                          cwd=Path(__file__).resolve().parents[1])
    assert done.returncode == 0, done.stderr[-2000:]
    assert path.read_text().splitlines() == [str(i) for i in range(500)]


def test_loop_lag_records_go_through_the_writer(tmp_path):
    """looplag._logged calls agent.log on the loop: the stall's record is handed to the writer, not written there."""
    import json
    from types import SimpleNamespace

    from orchestrator.agent import Agent
    from orchestrator.looplag import LINES

    agent = Agent(SimpleNamespace(settings=SimpleNamespace()), interaction_log=tmp_path / "logs" / "interactions.jsonl")

    async def go():
        lag = LoopLag(log=agent.log)
        lag._loop = asyncio.get_running_loop()
        lag._stalled(250.0, time.time(), ["stack"])
        await asyncio.sleep(0)  # _logged runs on the loop

    asyncio.run(go())
    LINES.flush()
    records = [json.loads(raw) for raw in agent.interaction_log.read_text().splitlines()]
    assert [r["kind"] for r in records] == ["loop-lag"] and records[0]["ms"] == 250
