"""No-model pipe peers exercise public worker lifecycle across owner-loop shutdown."""
import asyncio
import multiprocessing as mp
import threading
from types import SimpleNamespace

import pytest


def pipe_peer(conn, release):
    """An OS process boundary stand-in: ping, refusal, abrupt exit, or gated EOF."""
    try:
        while (message := conn.recv()) is not None:
            op = message['op']
            if op == 'exit':
                return
            key = 'id' if '_priority' in message else 'reply_to'
            reply = {key: message['id'], 'ok': op != 'refuse', 'device': 'no-model',
                     'error': 'scripted request refusal'}
            conn.send(reply)
            if op == 'finish-after-loop':
                release.wait(5)
                if message.get('after') == 'event':
                    conn.send({'event': 'late-worker-event'})
                elif message.get('after') == 'request':
                    conn.send({'request': 99, 'op': 'snapshot'})
                return
    finally:
        conn.close()


@pytest.fixture(params=['gpu', 'research'])
def channel(request, monkeypatch):
    from orchestrator import gpu_client
    from orchestrator.research import ipc

    context = mp.get_context('spawn')
    release = context.Event()

    def process(**kwargs):
        return context.Process(target=pipe_peer, args=(kwargs['args'][0], release), daemon=True,
                               name=kwargs['name'])

    transport = SimpleNamespace(Pipe=context.Pipe, Process=process)
    module = gpu_client if request.param == 'gpu' else ipc
    monkeypatch.setattr(module, 'mp', SimpleNamespace(get_context=lambda _: transport))
    name = f'no-model-lifecycle-{request.param}'
    if request.param == 'gpu':
        client = gpu_client.GpuClient(name, warm=False)
    else:
        async def answer(_):
            return {'ok': True}
        client = ipc.ProcessChannel(pipe_peer, name, lambda _: None, answer)
    yield client, release, name
    release.set()
    client.stop()
    for thread in threading.enumerate():
        if thread.name.startswith(name):
            thread.join(timeout=2)
            assert not thread.is_alive(), f"worker thread outlived shutdown: {thread.name}"


@pytest.mark.parametrize('after', ['eof', 'event', 'request'])
def test_worker_eof_after_the_owner_loop_closes_does_not_raise_in_a_reader(channel, monkeypatch, after):
    client, release, name = channel
    failures = []
    monkeypatch.setattr(threading, 'excepthook', lambda error: failures.append(
        f'{error.thread.name}: {error.exc_type.__name__}: {error.exc_value}'))
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(client.start())
        loop.run_until_complete(client.call('finish-after-loop', timeout=2, after=after))
    finally:
        loop.close()
    release.set()
    client.stop()
    for thread in threading.enumerate():
        if thread.name.startswith(name):
            thread.join(timeout=2)
            assert not thread.is_alive(), f"worker thread outlived shutdown: {thread.name}"
    assert not failures, failures


def test_worker_death_with_a_live_owner_fails_the_pending_request(channel):
    from orchestrator.research.ipc import WorkerGone

    client, _, _ = channel

    async def check():
        await client.start()
        with pytest.raises((RuntimeError, WorkerGone), match='exited'):
            await asyncio.wait_for(client.call('exit', timeout=2), timeout=1)
        client.stop()

    asyncio.run(check())


def test_worker_refusal_with_a_live_owner_reaches_the_caller_and_preserves_the_channel(channel):
    client, _, _ = channel

    async def check():
        await client.start()
        with pytest.raises(RuntimeError, match='scripted request refusal'):
            await client.call('refuse', timeout=2)
        assert (await client.call('ping', timeout=2))['ok']
        client.stop()

    asyncio.run(check())


def waiting_peer(conn, started):
    """A live worker that cannot drain its pipe until the test releases it."""
    started.set()
    import time
    time.sleep(60)
    conn.close()


def test_gpu_stop_reaps_worker_even_when_its_outgoing_pipe_is_blocked():
    import queue
    from orchestrator.gpu_client import GpuClient
    from tests.waiting import until
    context = mp.get_context('spawn')
    started = context.Event()
    parent, child = context.Pipe()
    process = context.Process(target=waiting_peer, args=(child, started), daemon=True)
    process.start(); child.close()
    client = GpuClient('blocked-pipe-shutdown', warm=False)
    client._conn, client._process = parent, process
    client._outbox = queue.SimpleQueue()
    writer = threading.Thread(target=client._writer, args=(client._outbox,parent), daemon=True)
    writer.start()
    failures = []
    def stop():
        try: client.stop()
        except BaseException as error: failures.append(error)
    stopper = threading.Thread(target=stop, daemon=True)
    try:
        assert started.wait(10)
        client._send({'op':'busy', 'payload':b'x'*(4 << 20)})
        assert until(client._send_lock.locked)
        stopper.start()
        # A bounded stop must terminate the owned child without needing the
        # send lock or a worker read. The original path deadlocks here.
        stopper.join(timeout=10)
        assert not stopper.is_alive(), 'shutdown waited on the blocked outgoing pipe'
        assert not failures, failures
        assert not process.is_alive()
        assert process.exitcode is not None
    finally:
        if process.is_alive(): process.kill()
        process.join(timeout=3)
        parent.close()
        client._outbox.put(None)
        writer.join(timeout=3)
        if stopper.ident is not None: stopper.join(timeout=3)


@pytest.mark.parametrize('unreaped', [False, True])
def test_gpu_stop_keeps_child_ownership_when_eof_clears_live_state(unreaped):
    import subprocess
    import sys
    from orchestrator.gpu_client import GpuClient
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    client = GpuClient('child-ownership-shutdown', warm=False)
    client.children['model'] = child.pid
    def joined(**kwargs):
        client.children.clear()  # Reader's EOF callback can run while stop joins.
    client._process = SimpleNamespace(join=joined, is_alive=lambda:unreaped,
                                      terminate=lambda:None, kill=lambda:None)
    try:
        if unreaped:
            with pytest.raises(RuntimeError,match='termination not confirmed'): client.stop()
        else: client.stop()
        assert child.wait(timeout=3) is not None
        assert client.children == {}
    finally:
        if child.poll() is None: child.kill()
        child.wait(timeout=3)



def test_gpu_stop_never_terminates_a_replacement_worker():
    from orchestrator.gpu_client import GpuClient
    client = GpuClient('replacement-shutdown', warm=False)
    touched = []
    replacement = SimpleNamespace(join=lambda **kw:touched.append('join-new'),
                                  is_alive=lambda:True, terminate=lambda:touched.append('terminate-new'))
    old_alive = [True]
    def join_old(**kwargs):
        client._process = replacement
        client.ready = True
    def terminate_old(): old_alive[0] = False
    old = SimpleNamespace(join=join_old, is_alive=lambda:old_alive[0], terminate=terminate_old)
    client._process = old
    client.stop()
    assert not touched and not old_alive[0]
    assert client._process is replacement and client.ready



def test_model_child_reported_during_shutdown_remains_owned_after_eof():
    import subprocess
    import sys
    from orchestrator.gpu_client import GpuClient
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    client = GpuClient('late-child-shutdown', warm=False)
    def join(**kwargs):
        client._mark({'load':'loaded', 'model':'micro-sam-lm', 'pid':child.pid})
        client._fail_pending()
    client._process = SimpleNamespace(join=join, is_alive=lambda:False)
    try:
        client.stop()
        assert child.wait(timeout=3) is not None
        assert client.children == {}
    finally:
        if child.poll() is None: child.kill()
        child.wait(timeout=3)


def test_old_worker_eof_cannot_clear_replacement_state():
    from orchestrator.gpu_client import GpuClient
    client = GpuClient('old-eof', warm=False)
    old, current = object(), object()
    client._conn, client.ready = current, True
    client.children['new-model'] = 12345
    client._exited(old)
    assert client.ready and client.children == {'new-model':12345}


def test_late_child_does_not_reenter_public_ownership_before_eof(monkeypatch):
    from orchestrator import gpu_client
    client = gpu_client.GpuClient('late-before-eof', warm=False)
    signals = []
    monkeypatch.setattr(gpu_client.os, 'kill', lambda pid, signal: signals.append(pid))
    sent = [False]
    def join(**kwargs):
        if not sent[0]:
            sent[0] = True
            client._mark({'load':'loaded', 'model':'late-model', 'pid':12345})
    client._process = SimpleNamespace(join=join, is_alive=lambda:False)
    client.stop()
    assert client.children == {}
    client.stop()
    assert signals == [12345]


def clicking_peer(conn, release):
    """A click worker that answers every request (its warm-up too) and says which process it is."""
    import os
    try:
        while (message := conn.recv()) is not None:
            conn.send({'id': message['id'], 'ok': True, 'device': 'no-model', 'pid': os.getpid()})
    finally:
        conn.close()


def test_a_killed_click_worker_is_started_again_and_the_next_click_works(monkeypatch):
    """L7e 2026-10-20 (`kill -9` of the click worker): every click said "Loading the outline model" for good. Now its
    in-flight call fails saying it stopped, calls while it restarts fail fast saying so, the restart is said (a problem
    and its recovery, an Activity line), and the next click works."""
    import os
    import signal
    from orchestrator import activity, gpu_client

    context = mp.get_context('spawn')
    processes = []

    def process(**kwargs):
        made = context.Process(target=clicking_peer, args=(kwargs['args'][0], None), daemon=True, name=kwargs['name'])
        processes.append(made)
        return made

    monkeypatch.setattr(gpu_client, 'mp', SimpleNamespace(get_context=lambda _: SimpleNamespace(Pipe=context.Pipe, Process=process)))
    monkeypatch.setattr(gpu_client, 'RESPAWN_S', (0.05, 0.05))
    lines = []
    monkeypatch.setattr(activity, 'activity', lambda hub, kind, text, **kw: lines.append((text, kw.get('state'))))
    client = gpu_client.GpuClient('respawn-click-worker', warm=True)
    told = []
    client.on_problem.append(told.append)

    async def check():
        await client.start()
        first = (await client.call('segment', timeout=5, priority='click'))['pid']
        os.kill(processes[-1].pid, signal.SIGKILL)
        for _ in range(100):
            if client.restarting or client.ready is False:
                break
            await asyncio.sleep(0.02)
        assert 'killed, signal 9' in client.restarting and 'restarting' in client.problem()
        with pytest.raises(gpu_client.WorkerRestarting, match='restarting the outline model|restarting'):
            await client.call('segment', timeout=5, priority='click')
        for _ in range(300):
            if client.ready and not client.restarting:
                break
            await asyncio.sleep(0.05)
        second = (await client.call('segment', timeout=5, priority='click'))['pid']
        return first, second

    try:
        first, second = asyncio.run(check())
    finally:
        client.stop()
    assert first != second and len(processes) == 2
    assert told[0].startswith('the respawn-click-worker worker stopped (killed, signal 9)') and told[-1] == ''
    assert [state for _, state in lines] == ['running', 'done'] and len(client.restarts) == 1


def test_a_worker_that_keeps_dying_is_not_restarted_forever(monkeypatch):
    from orchestrator import activity, gpu_client

    monkeypatch.setattr(activity, 'activity', lambda *a, **kw: None)
    client = gpu_client.GpuClient('respawn-budget', warm=True)
    client.restarts = [__import__('time').monotonic()] * gpu_client.RESPAWN_MAX
    assert asyncio.run(client._respawn('killed, signal 9')) is False
    assert 'not restarted again' in client.problem()

    async def refused():
        with pytest.raises(gpu_client.WorkerRestarting):
            await client.call('segment', timeout=1)

    asyncio.run(refused())


def test_a_stopped_worker_is_never_restarted(monkeypatch):
    from orchestrator import gpu_client

    client = gpu_client.GpuClient('respawn-stopped', warm=True)
    client._conn, client._loop = SimpleNamespace(send=lambda message: None), asyncio.new_event_loop()
    try:
        client.stop()
        client._exited(client._conn)
        assert client._respawning is None and not client.restarting
    finally:
        client._loop.close()


def warmup_dying_peer(conn, release):
    """A click worker that answers its ping and then exits on its warm-up (the review's probe of #364)."""
    try:
        while (message := conn.recv()) is not None:
            if message.get('op') == 'warmup':
                return
            conn.send({'id': message['id'], 'ok': True, 'device': 'no-model'})
    finally:
        conn.close()


def test_a_restart_whose_worker_dies_in_its_warm_up_is_not_called_running_again(monkeypatch):
    """The first restart's worker exits during its warm-up (which says nothing of that): it is a failed try, never
    "running again"; the next try's worker runs, and the next click works. Nothing is left outstanding."""
    import os
    import signal
    from orchestrator import activity, gpu_client

    context = mp.get_context('spawn')
    targets = [clicking_peer, warmup_dying_peer, clicking_peer]
    processes = []

    def process(**kwargs):
        made = context.Process(target=targets[min(len(processes), len(targets) - 1)], args=(kwargs['args'][0], None),
                               daemon=True, name=kwargs['name'])
        processes.append(made)
        return made

    monkeypatch.setattr(gpu_client, 'mp', SimpleNamespace(get_context=lambda _: SimpleNamespace(Pipe=context.Pipe, Process=process)))
    monkeypatch.setattr(gpu_client, 'RESPAWN_S', (0.05, 0.05, 0.05))
    lines = []
    monkeypatch.setattr(activity, 'activity', lambda hub, kind, text, **kw: lines.append((text, kw.get('state'))))
    client = gpu_client.GpuClient('respawn-warmup-dies', warm=True)

    async def check():
        await client.start()
        os.kill(processes[-1].pid, signal.SIGKILL)
        for _ in range(400):
            if len(processes) >= 3 and client.ready and not client.restarting:
                break
            await asyncio.sleep(0.05)
        reply = await client.call('segment', timeout=5, priority='click')
        return reply

    try:
        assert asyncio.run(check())['ok']
    finally:
        client.stop()
    assert len(processes) == 3  # the one that died in its warm-up was not taken as running
    assert [state for _, state in lines] == ['running', 'done'] and len(client.restarts) == 1
    assert not client._outstanding and not client.clicks_waiting()


def test_a_call_to_a_worker_that_went_away_fails_truthfully_and_is_forgotten():
    """With its writer gone (an exit) a call is never sent: it fails saying the worker is not running, never with a raw
    BrokenPipeError, and leaves nothing outstanding (a leaked click kept clicks_waiting() True)."""
    from orchestrator import gpu_client

    class Closed:
        def send(self, message):
            raise BrokenPipeError(32, 'Broken pipe')

    client = gpu_client.GpuClient('send-after-exit', warm=False)
    client._conn = Closed()

    async def check():
        client._loop = asyncio.get_running_loop()
        with pytest.raises(RuntimeError, match='gpu worker exited: .*never sent: BrokenPipeError'):
            await client.call('segment', timeout=1, priority='click')

    asyncio.run(check())
    assert not client._outstanding and not client._pending and not client.clicks_waiting()
