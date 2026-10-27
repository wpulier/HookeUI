import subprocess
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from orchestrator import owned_process, worker_resources
from tests.waiting import HANG_S

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='The capability resource boundary uses macOS libproc')


@pytest.fixture
def children():
    processes = []
    guards = []

    def launch(code, work, limits=None):
        process = subprocess.Popen([sys.executable, '-u', '-c', code], cwd=work,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        processes.append(process)
        if limits is None:
            return process
        guard = worker_resources.Guard(process, work, limits)
        guards.append(guard)
        guard.start()
        return process, guard

    yield launch
    for guard in guards:
        guard.close()
    for process in processes:
        if process.poll() is None:
            owned_process.terminate(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def failure(guard):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            guard.check()
        except owned_process.Stopped as exc:
            return str(exc)
        time.sleep(.02)
    pytest.fail('Worker did not report the resource refusal')


def send(process):
    process.stdin.write(b'go\n')
    process.stdin.flush()


def test_idle_memory_failure_preserves_other_worker_and_retry(children, tmp_path):
    healthy = children('import time; print("ready", flush=True); time.sleep(30)', tmp_path)
    assert healthy.stdout.readline() == b'ready\n'
    work = tmp_path / 'bad'
    work.mkdir()
    process, guard = children('import sys,time; print("ready",flush=True); sys.stdin.readline(); '
                              'data=bytearray(48*1024*1024); time.sleep(30)', work,
                              replace(worker_resources.Limits(), memory_bytes=32 * 1024 * 1024))
    assert process.stdout.readline() == b'ready\n'
    send(process)
    assert 'memory limit' in failure(guard)
    process.wait(timeout=5)
    assert healthy.poll() is None
    with pytest.raises(owned_process.Stopped, match='memory limit'):
        guard.check()
    retry = tmp_path / 'retry'
    retry.mkdir()
    resumed, retry_guard = children('import sys; print("ready",flush=True); sys.stdin.readline(); print("result",flush=True)',
                                   retry, worker_resources.Limits())
    assert resumed.stdout.readline() == b'ready\n'
    send(resumed)
    assert resumed.stdout.readline() == b'result\n'
    resumed.wait(timeout=5)
    retry_guard.check()


@pytest.mark.parametrize(('files', 'limits', 'message'), [
    ({'large': 8192}, {'file_bytes': 4096}, 'per-file output limit'),
    ({'one': 4096, 'two': 4096}, {'work_bytes': 4096}, 'work folder limit'),
    ({'one': 1, 'two': 1, 'three': 1}, {'work_entries': 2}, 'work folder limit'),
])
def test_idle_disk_failure_is_bounded(children, tmp_path, files, limits, message):
    code = ('import sys,time; from pathlib import Path; print("ready",flush=True); sys.stdin.readline(); '
            f'files={files!r}; [(Path(k).write_bytes(bytes(v))) for k,v in files.items()]; time.sleep(30)')
    process, guard = children(code, tmp_path, replace(worker_resources.Limits(), **limits))
    assert process.stdout.readline() == b'ready\n'
    send(process)
    assert message in failure(guard)
    process.wait(timeout=5)


def test_process_count_failure_reaps_owned_tree(children, tmp_path):
    code = ('import subprocess,sys,time; print("ready",flush=True); sys.stdin.readline(); '
            'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
            'print(child.pid,flush=True); time.sleep(30)')
    process, guard = children(code, tmp_path, replace(worker_resources.Limits(), processes=1))
    assert process.stdout.readline() == b'ready\n'
    send(process)
    child_pid = int(process.stdout.readline())
    assert 'process-count limit' in failure(guard)
    process.wait(timeout=5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        info = worker_resources.fiji_sandbox._native_process(child_pid)
        if info is None or info.bsd.status == 5:
            break
        time.sleep(.02)
    else:
        pytest.fail('Owned child survived its worker resource refusal')


def test_unavailable_supervision_fails_closed(children, tmp_path, monkeypatch):
    def unavailable(pid):
        raise OSError('native inspection unavailable')
    monkeypatch.setattr(worker_resources.fiji_sandbox, '_native_process', unavailable)
    process = children('import time; time.sleep(30)', tmp_path)
    guard = worker_resources.Guard(process, tmp_path)
    with pytest.raises(owned_process.Stopped, match='could not be supervised'):
        guard.start()
    assert process.poll() is not None
    guard.close()


def test_exited_unreaped_child_is_not_mistaken_for_uninspectable_live_process(tmp_path):
    process = subprocess.Popen(['/usr/bin/true'], start_new_session=True)
    guard = worker_resources.Guard(process, tmp_path)
    try:
        with process._waitpid_lock:
            ended = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
            assert ended.si_pid == process.pid
            assert process.poll() is None
            os.kill(process.pid, 0)
            assert worker_resources.fiji_sandbox._native_process(process.pid) is None
            guard._sample()
            again = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            assert again == ended, 'supervision must leave the exit status for Popen to reap'
        assert process.wait(timeout=HANG_S) == 0
    finally:
        process.wait(timeout=HANG_S)
        guard.close()


def test_reaped_child_is_not_mistaken_for_uninspectable_live_process(tmp_path):
    process = subprocess.Popen(['/usr/bin/true'], start_new_session=True)
    status = None
    try:
        with owned_process.watching(process, HANG_S, 'process exit regression'):
            with process._waitpid_lock:
                pid, status = os.waitpid(process.pid, 0)
                assert pid == process.pid and process.poll() is None
                assert worker_resources.fiji_sandbox._native_process(pid) is None
                guard = worker_resources.Guard(process, tmp_path)
                guard._sample()
                assert guard._identities == {}
    finally:
        if status is not None:
            process._handle_exitstatus(status)
        else:
            owned_process.terminate(process)


def test_missing_native_info_for_a_live_child_still_fails_closed(children, tmp_path, monkeypatch):
    process = children('import time; time.sleep(30)', tmp_path)
    monkeypatch.setattr(worker_resources.fiji_sandbox, '_native_process', lambda pid: None)
    guard = worker_resources.Guard(process, tmp_path)
    with pytest.raises(owned_process.Stopped, match='live process could not be supervised'):
        guard.start()
    assert process.poll() is not None


def test_child_finishes_between_missing_task_record_and_terminal_status(children, tmp_path, monkeypatch):
    process = children('import sys; print("ready",flush=True); sys.stdin.readline()', tmp_path)
    assert process.stdout.readline() == b'ready\n'
    monkeypatch.setattr(worker_resources.fiji_sandbox, '_native_process', lambda pid: None)
    original_wait = process.wait
    called = []
    def finish_at_terminal_wait(timeout):
        assert process.poll() is None
        assert os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None
        os.kill(process.pid, 0)
        called.append(timeout)
        send(process)
        with owned_process.watching(process, HANG_S, 'terminal transition'):
            ended = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
            assert ended.si_pid == process.pid
        return original_wait(timeout=timeout)
    monkeypatch.setattr(process, 'wait', finish_at_terminal_wait)
    guard = worker_resources.Guard(process, tmp_path)
    guard._sample()
    assert called == [worker_resources.TERMINAL_SETTLE_S]
    assert process.poll() == 0 and guard._identities == {}
    monkeypatch.setattr(process, 'wait', original_wait)
    guard.close()


def test_unlinked_file_during_stat_is_not_a_hard_link(tmp_path, monkeypatch):
    path = tmp_path / 'temporary'
    path.write_bytes(b'private temporary data')
    (tmp_path / 'kept').write_bytes(b'kept')
    original = Path.lstat
    def removed_between_lookup_and_attributes(self):
        info = original(self)
        if self == path:
            self.unlink()
            fields = list(info)
            fields[3] = 0
            return os.stat_result(fields)
        return info
    monkeypatch.setattr(Path, 'lstat', removed_between_lookup_and_attributes)
    monkeypatch.setattr(worker_resources.fiji_sandbox.os, 'walk',
                        lambda *args, **kwargs: iter([(tmp_path, [], ['temporary', 'kept'])]))
    worker_resources.fiji_sandbox._work_size(tmp_path, worker_resources.Limits(work_entries=1))
    assert not path.exists()


def test_actual_hard_link_is_still_refused(tmp_path):
    source = tmp_path / 'first'
    source.write_bytes(b'owned')
    os.link(source, tmp_path / 'second')
    with pytest.raises(worker_resources.fiji_sandbox.Failed, match='hard-linked output'):
        worker_resources.fiji_sandbox._work_size(tmp_path, worker_resources.Limits())


def test_close_stops_idle_worker_and_is_repeatable(children, tmp_path):
    process, guard = children('import time; print("ready",flush=True); time.sleep(30)', tmp_path,
                              worker_resources.Limits())
    assert process.stdout.readline() == b'ready\n'
    guard.close()
    guard.close()
    assert process.poll() is not None
    guard.check()


@pytest.mark.parametrize('name', ['memory_bytes', 'processes', 'file_bytes', 'work_bytes', 'work_entries'])
@pytest.mark.parametrize('value', [0, -1, True, 1.5])
def test_invalid_limit_cannot_start(name, value):
    with pytest.raises(ValueError):
        worker_resources.Limits(**{name: value})


def test_memory_limit_counts_descendant_allocation(children, tmp_path):
    child_code = 'import time; data=bytearray(48*1024*1024); time.sleep(30)'
    code = ('import subprocess,sys,time; print("ready",flush=True); sys.stdin.readline(); '
            f'child=subprocess.Popen([sys.executable,"-c",{child_code!r}]); '
            'print(child.pid,flush=True); time.sleep(30)')
    process, guard = children(code, tmp_path, replace(worker_resources.Limits(), memory_bytes=32*1024*1024))
    assert process.stdout.readline() == b'ready\n'
    send(process)
    child_pid = int(process.stdout.readline())
    assert 'memory limit' in failure(guard)
    process.wait(timeout=5)
    assert child_pid != process.pid


def test_seen_detached_child_is_stopped_when_parent_exits(children, tmp_path):
    child_code = 'import time; time.sleep(30)'
    code = ('import subprocess,sys; '
            f'child=subprocess.Popen([sys.executable,"-c",{child_code!r}],start_new_session=True); '
            'print(child.pid,flush=True); sys.stdin.readline()')
    process, guard = children(code, tmp_path, worker_resources.Limits())
    child_pid = int(process.stdout.readline())
    deadline = time.monotonic() + 8
    while child_pid not in guard._identities and time.monotonic() < deadline:
        time.sleep(.02)
    assert child_pid in guard._identities
    send(process)
    process.wait(timeout=5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        info = worker_resources.fiji_sandbox._native_process(child_pid)
        if info is None or info.bsd.status == 5:
            break
        time.sleep(.02)
    else:
        pytest.fail('Observed detached child survived its parent exit')
    guard.close()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo'])
def test_unsafe_work_outputs_stop_idle_worker(children, tmp_path, kind):
    command = {
        'symlink': 'os.symlink("source", "bad")',
        'hardlink': 'os.link("source", "bad")',
        'fifo': 'os.mkfifo("bad")',
    }[kind]
    code = ('import os,sys,time; from pathlib import Path; print("ready",flush=True); '
            f'sys.stdin.readline(); Path("source").write_bytes(b"data"); {command}; time.sleep(30)')
    process, guard = children(code, tmp_path, worker_resources.Limits())
    assert process.stdout.readline() == b'ready\n'
    send(process)
    assert 'link' in failure(guard) or kind == 'fifo'
    process.wait(timeout=5)


def test_return_check_catches_output_before_next_monitor_tick(children, tmp_path, monkeypatch):
    monkeypatch.setattr(worker_resources, 'POLL_S', 30)
    code = ('import sys,time; from pathlib import Path; print("ready",flush=True); sys.stdin.readline(); '
            'Path("too-large").write_bytes(bytes(8192)); print("result",flush=True); time.sleep(30)')
    process, guard = children(code, tmp_path, replace(worker_resources.Limits(), file_bytes=4096))
    assert process.stdout.readline() == b'ready\n'
    send(process)
    assert process.stdout.readline() == b'result\n'
    with pytest.raises(owned_process.Stopped, match='per-file output limit'):
        guard.check()
    assert process.poll() is not None
    guard.close()
    with pytest.raises(owned_process.Stopped, match='per-file output limit'):
        guard.check()


def test_child_that_exits_just_before_its_group_check_is_a_normal_end(tmp_path):
    # CI run 39585836721: start() saw poll() report the child running, the child exited, and getpgid() then raised
    # ESRCH (Darwin no longer finds an exited, unreaped child), which became "resource supervision failed".
    # Holding Popen's waitpid lock freezes exactly that window: poll() says running while the child is a zombie.
    process = subprocess.Popen(['/usr/bin/true'], start_new_session=True)
    guard = worker_resources.Guard(process, tmp_path)
    try:
        with process._waitpid_lock:
            ended = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
            assert ended.si_pid == process.pid and process.poll() is None
            with pytest.raises(ProcessLookupError):
                os.getpgid(process.pid)
            guard.start()
            guard.check()
        assert process.wait(timeout=HANG_S) == 0, 'supervision must leave the real exit status for Popen to reap'
        guard.check()
    finally:
        process.wait(timeout=HANG_S)
        guard.close()
    guard.check()


def test_live_child_whose_group_cannot_be_found_still_fails_closed(children, tmp_path, monkeypatch):
    process = children('import time; time.sleep(30)', tmp_path)
    def missing(pid):
        raise ProcessLookupError(3, 'No such process')
    monkeypatch.setattr(worker_resources.os, 'getpgid', missing)
    guard = worker_resources.Guard(process, tmp_path)
    with pytest.raises(owned_process.Stopped, match='resource supervision failed'):
        guard.start()
    assert process.poll() is not None
    guard.close()
