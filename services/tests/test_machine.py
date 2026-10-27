"""The performance meter's facts about the computer (orchestrator/machine.py)."""
import sys

import pytest

from orchestrator.machine import Machine, label, parse_meminfo, parse_ps, parse_swapusage
from tests.waiting import HANG_S

PS = """\
  100     1  10.0  204800 /opt/homebrew/bin/python -m orchestrator.server --port 8770 --bridge-url http://127.0.0.1:8765
  101   100  38.1  409600 /opt/homebrew/bin/python -B -c from multiprocessing.spawn import spawn_main
  102     1  22.2   51200 /usr/bin/python3 /x/microscope-mind/experiments/iphone-camera-feed/scripts/iphone_bridge_server.py --port 8765
  200     1 434.6  614400 /Library/Developer/CoreSimulator/Volumes/iOS/Profiles/Runtimes/iOS 26.5.simruntime/Contents/Resources/RuntimeRoot/System/Library/PrivateFrameworks/MediaAnalysis.framework/mediaanalysisd
  300     1 297.0 8646656 /Applications/Xcode.app/Contents/MacOS/Xcode
  400     1  78.2  102400 /opt/homebrew/Cellar/python@3.13/3.13.11_1/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python -m bench.corpus.build
  103   102  40.0  285696 /opt/homebrew/bin/ffmpeg -f avfoundation -i iPhoneWild Camera:none
  500     1   0.0    1024 /sbin/launchd
garbage line
"""


def test_swap_and_meminfo_are_read():
    assert parse_swapusage("total = 9216.00M  used = 8846.38M  free = 369.62M  (encrypted)") == (9216.0, 8846.38)
    assert parse_swapusage("") is None
    info = parse_meminfo("MemTotal: 32000000 kB\nMemAvailable: 8000000 kB\nSwapTotal: 2097152 kB\nSwapFree: 1048576 kB\n")
    assert info == {"memFreePct": 25, "swapTotalMb": 2048, "swapUsedMb": 1024}


def test_programs_are_named_as_people_know_them():
    assert label("/usr/bin/python3 -m orchestrator.server --port 8770") == "Hooke server :8770"
    assert label("/Applications/Xcode.app/Contents/MacOS/Xcode") == "Xcode"
    assert label("/a/iOS 26.5.simruntime/x/mediaanalysisd --flag") == "iOS Simulator: mediaanalysisd"
    assert label("/opt/homebrew/bin/python3.13 scripts/ios-test-session.py run") == "Python: ios-test-session.py"


def test_the_busiest_programs_with_ours_marked():
    top = parse_ps(PS, own=100, n=6)
    assert [p["name"] for p in top] == ["iOS Simulator: mediaanalysisd", "Xcode", "Python: bench.corpus.build", "ffmpeg", "Python",
                                        "Camera bridge"]
    assert top[1] == {"name": "Xcode", "cpu": 297, "memMb": 8444, "ours": False}
    # The bridge's ffmpeg, the GPU worker (started by this process) and the bridge are ours.
    assert [p["ours"] for p in top] == [False, False, False, True, True, True]


def test_the_sample_and_the_clicks():
    m = Machine(sample_s=60)
    state = m.sample()
    assert state["cores"] >= 1 and "diskFreeGb" in state
    for ms in [100] * 18 + [900, 1000]:
        m.clicked(ms)
    wire = m.wire()  # reading the sample does not start background work
    assert wire["clicks"] == 20 and wire["clickP95Ms"] == 900


def test_hub_stop_ends_its_machine_sampler():
    """An app that stopped must not keep sampling into another app's lifetime."""
    import asyncio
    import threading

    from orchestrator.hub import Hub

    before = set(threading.enumerate())
    hub = Hub("http://127.0.0.1:1", use_gpu=False)
    hub.machine.start()
    hub.machine.start()  # one sampler for the same owner
    started = set(threading.enumerate()) - before
    assert len([thread for thread in started if thread.name == "machine"]) == 1
    asyncio.run(hub.stop())
    asyncio.run(hub.stop())
    assert not [thread for thread in started if thread.is_alive()]
    hub.machine.start()  # an explicit subsequent lifetime can start again
    restarted = set(threading.enumerate()) - before
    asyncio.run(hub.stop())
    assert not [thread for thread in restarted if thread.is_alive()]


def test_reading_machine_state_does_not_start_a_sampler():
    """Read-only health/status calls need no app lifetime or background OS probes."""
    import threading

    before = set(threading.enumerate())
    Machine().wire()
    assert set(threading.enumerate()) == before


@pytest.mark.skipif(sys.platform != "darwin", reason="only macOS samples through external commands (vm_stat, sysctl); Linux reads /proc and runs none, so there is no pending probe to wait on")
def test_sampler_shutdown_waits_off_the_event_loop(monkeypatch):
    """A pending external OS command must not freeze app shutdown's event loop."""
    import asyncio
    import threading
    from types import SimpleNamespace

    from orchestrator import machine
    from orchestrator.hub import Hub

    entered, release = threading.Event(), threading.Event()

    def command(*args, **kwargs):
        entered.set()
        release.wait(2)
        return SimpleNamespace(stdout='', returncode=0)

    monkeypatch.setattr(machine, 'subprocess', SimpleNamespace(**{**vars(machine.subprocess), 'run': command}))
    hub = Hub('http://127.0.0.1:1', use_gpu=False)

    async def scenario():
        hub.machine.start()
        assert await asyncio.to_thread(entered.wait, HANG_S)
        stopping = asyncio.create_task(hub.stop())
        try:
            await asyncio.sleep(.01)
            assert not stopping.done(), 'the external probe remains blocked, while this loop can make progress'
        finally:
            release.set()
            await stopping

    asyncio.run(scenario())


def test_our_workers_are_named_by_their_role_and_what_is_loading():
    from orchestrator.machine import parse_pids, worker_name

    names = {101: worker_name({"role": "analysis worker", "loading": "micro-sam"})}
    top = parse_ps(PS, own=100, n=6, names=names)
    assert {"name": "ObservationUI: analysis worker (loading micro-sam)", "cpu": 38, "memMb": 400, "ours": True} in top
    assert parse_pids("  101  38.1  409600\n  103 40.0 285696\nbad\n") == {101: (38.1, 400.0), 103: (40.0, 279.0)}


def test_our_workers_are_sampled_on_every_tick_with_their_role_and_state(monkeypatch):
    """Live 2026-10-11: they were sampled only while the meter's panel was open, and the meter blamed WindowServer."""
    import subprocess as sp

    from orchestrator import machine as m

    monkeypatch.setattr(m.subprocess, "run", lambda args, **kw: sp.CompletedProcess(
        args, 0, stdout="  201  190.0  2457600\n" if args[0] == "ps" else "", stderr=""))
    sampler = Machine(sample_s=60)
    sampler.workers = lambda: [{"pid": 201, "role": "analysis worker", "loading": "micro-sam"},
                               {"pid": 999, "role": "outline worker"}]  # gone since: left out
    ours = sampler.sample()["ours"]
    assert ours == [{"pid": 201, "role": "analysis worker", "loading": "micro-sam", "cpu": 190, "memMb": 2400,
                     "name": "ObservationUI: analysis worker (loading micro-sam)"}]


def test_a_machine_record_when_a_reading_crosses_a_line():
    import asyncio

    from orchestrator.machine import levels

    assert levels({"cores": 14, "load1": 7, "memFreePct": 61, "diskFreeGb": 300}) == {
        "memFreePct": "good", "diskFreeGb": "good", "loadPerCore": "good"}
    assert levels({"cores": 14, "load1": 142, "swapTotalMb": 100, "swapUsedMb": 90})["loadPerCore"] == "poor"

    async def go():
        logged = []
        sampler = Machine(sample_s=60)
        sampler.log, sampler.loop = logged.append, asyncio.get_running_loop()
        sampler._crossed({"cores": 14, "load1": 7})
        sampler._crossed({"cores": 14, "load1": 8})  # still good: nothing new
        sampler._crossed({"cores": 14, "load1": 142})
        await asyncio.sleep(0)
        return logged

    logged = asyncio.run(go())
    assert [r["changed"] for r in logged] == [{"loadPerCore": [None, "good"]}, {"loadPerCore": ["good", "poor"]}]
    assert logged[-1]["kind"] == "machine" and logged[-1]["readings"]["load1"] == 142


def test_swap_is_rated_by_pages_swapped_back_in_not_by_how_full_it_is():
    """macOS keeps swap near full long after memory ran short (red all of 2026-10-11 while nothing waited): what slows
    programs is pages swapped back in, now."""
    from orchestrator.machine import levels, parse_vm_stat

    counts = parse_vm_stat("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 3041.\n"
                           "Swapins:                                  1205.\nSwapouts:                     4410.\n")
    assert counts["Swapins"] == 1205 and counts["Swapouts"] == 4410
    full_idle = {"swapTotalMb": 14300, "swapUsedMb": 13400, "swapInsPerS": 0.0}
    assert levels(full_idle)["swapInsPerS"] == "good"
    assert levels({**full_idle, "swapInsPerS": 450.0})["swapInsPerS"] == "poor"


def test_swapping_is_a_rate_over_the_contracts_window_not_one_sample():
    """Live 2026-10-11 16:32-16:35 the swap level read one 5 s sample a time and flipped between good, fair and poor every
    5 s. It is the pages swapped back in a second over the last `windowS` (contract/overlay.json): one burst counts for its
    share of the window, steady swapping for all of it."""
    from orchestrator import contract
    from orchestrator.machine import SWAP_WINDOW_S

    assert SWAP_WINDOW_S == contract.OVERLAY["performance"]["swapInsPerS"]["windowS"] == 30
    m = Machine()
    ins = 0
    rates = []
    for n, per_s in enumerate([0, 0, 0, 0, 0, 0, 0, 1426.5, 2.4, 0, 0, 0, 0, 0, 0, 0]):  # one 5 s burst at n=7
        ins += round(per_s * 5)
        rates.append(m.swap_rates((ins, 0), n * 5.0))
    assert rates[0] is None  # nothing to compare the first sample with
    assert rates[6][0] == 0.0 and rates[7][0] == 237.7  # the burst is a sixth of the window (1426.5 / 6)...
    assert rates[12][0] == 238.1 and rates[13][0] == 0.4  # ...for as long as it is in it, 30 s
    assert len(m._swaps) <= SWAP_WINDOW_S / 5 + 2  # only the window is kept


def test_the_swap_level_holds_while_a_reading_hovers_at_a_line():
    """The demo's samples at 16:33:40-16:34:35 hovered at the good line (28.6, 1.6, 31, 9.3, 58.7, 8.7, 23, 0.8, 20.6
    pages/s; the samples no record kept are filled on their side of it). Rated one sample a time the meter flipped 10
    times in two minutes; over the window, with the contract's hysteresis (a worse level is left only once the reading
    is past the line it crossed by that share of it), it is one stretch of "fair". A worse reading is said at once."""
    from orchestrator.machine import levels

    demo = [2.4, 5, 5, 5, 5, 5, 5, 5, 28.6, 1.6, 31.0, 25, 9.3, 5, 58.7, 8.7, 23.0, 0.8, 5, 20.6, 5, 5, 5, 5, 5, 5, 5, 5]
    m, ins, was, smoothed = Machine(), 0, {}, []
    m.swap_rates((0, 0), -5.0)
    for n, per_s in enumerate(demo):
        ins += round(per_s * 5)
        was = levels({"swapInsPerS": m.swap_rates((ins, 0), n * 5.0)[0]}, was)
        smoothed.append(was["swapInsPerS"])
    flips = lambda seq: sum(a != b for a, b in zip(seq, seq[1:]))  # noqa: E731
    assert flips([levels({"swapInsPerS": v})["swapInsPerS"] for v in demo]) == 10
    assert flips(smoothed) == 2 and smoothed.count("fair") == 7

    def walk(values):
        was, out = {}, []
        for v in values:
            was = levels({"swapInsPerS": v}, was)
            out.append(was["swapInsPerS"])
        return out

    assert walk([19.0, 21.0, 18.0, 15.0, 22.0, 11.0, 9.0, 300.0, 150.0, 99.0, 12.0, 10.0]) == [
        "good", "fair", "fair", "fair", "fair", "fair", "good", "poor", "poor", "fair", "fair", "good"]
    assert levels({"swapInsPerS": 15.0})["swapInsPerS"] == "good"  # with nothing before, as the lines say
    assert levels({"memFreePct": 26}, {"memFreePct": "fair"})["memFreePct"] == "good"  # lines without one: no hold


def test_model_load_transitions_refresh_meter_without_waiting_for_periodic_sample():
    """A real load shorter than the 5-second meter interval must still be observed."""
    import queue
    from types import SimpleNamespace
    from orchestrator.loads import Loads

    sampled=queue.Queue();current={'loading':None}
    sampler=Machine(sample_s=3600)
    sampler.sample=lambda:sampled.put(current['loading'])
    requested=[];refresh=getattr(sampler,'request_sample',lambda:None)
    def request():
        requested.append(current['loading']);refresh()
    sampler.request_sample=request
    loads=Loads(None,hub=SimpleNamespace(machine=sampler))
    loads.measured['edgetam']=[{'seconds':.1,'memoryMb':1}]
    client=SimpleNamespace(name='test-owned-worker')
    sampler.start()
    try:
        assert sampled.get(timeout=HANG_S) is None
        current['loading']='edgetam'
        loads.told(client,{'load':'loading','model':'edgetam'})
        assert requested==['edgetam'], 'Model-load admission must wake the existing meter sampler'
        assert sampled.get(timeout=HANG_S)=='edgetam'
        current['loading']=None
        loads.told(client,{'load':'failed','model':'edgetam','error':'controlled refusal'})
        assert requested==['edgetam',None]
        assert sampled.get(timeout=HANG_S) is None
    finally:
        sampler.stop()
    assert sampler._thread is None


def test_stop_during_resample_clear_does_not_lose_its_wakeup():
    import threading
    entered,release=threading.Event(),threading.Event()
    sampled,waited=[],[]
    class ControlledWake:
        def __init__(self):self.clears=0
        def clear(self):
            self.clears+=1
            if self.clears==2:
                entered.set();assert release.wait(HANG_S)
        def set(self):pass
        def wait(self,timeout):waited.append(timeout)
    sampler=Machine(sample_s=3600);sampler._resample=ControlledWake()
    sampler.sample=lambda:sampled.append(True)
    sampler.start();assert entered.wait(HANG_S)
    stopped=threading.Thread(target=sampler.stop);stopped.start()
    try:
        assert sampler._stopped.wait(HANG_S)
    finally:
        release.set();stopped.join(HANG_S)
    assert not stopped.is_alive()
    assert sampled==[] and waited==[], 'Stop won admission; no probe or interval wait may follow'
