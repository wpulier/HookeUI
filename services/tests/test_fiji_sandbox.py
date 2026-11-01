"""The contributed JVM boundary, including opt-in actual macOS Fiji checks.

Set FIJI_SANDBOX_TEST_RUNTIME to an isolated, pinned Fiji clone. These
checks never discover the user's runtime, download one, or use the production profile.
"""
import asyncio
import ctypes
import errno
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from orchestrator import fiji_sandbox as boundary, owned_process


PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
                    "0000000c49444154089963606060000000040001a30a15e30000000049454e44ae426082")


@pytest.fixture
def invocation(tmp_path):
    image = tmp_path / "input.png"
    image.write_bytes(b"test input")
    script = tmp_path / "script.groovy"
    script.write_text("return imp")
    return {"op": "run", "image": str(image), "script": str(script),
            "scriptSha256": hashlib.sha256(script.read_bytes()).hexdigest(), "output": "image.2d.rgb8"}


@pytest.fixture
def child(monkeypatch):
    processes = []
    source = ["import json,sys,pathlib; r=json.loads(sys.stdin.readline()); "
              f"pathlib.Path(r['out']).write_bytes(bytes.fromhex({PNG.hex()!r})); "
              "print('@@'+json.dumps({'ok':True,'kind':'picture','path':'/untrusted'}),flush=True)"]
    popen = subprocess.Popen

    def start(args, **kwargs):
        if args[0] == boundary.EXEC:
            assert kwargs["close_fds"] and kwargs["start_new_session"]
            assert set(kwargs["env"]) == {"HOME", "TMPDIR", "PATH"}
            process = popen([sys.executable, "-c", source[0]], **kwargs)
            processes.append((process, Path(kwargs["cwd"])))
            return process
        return popen(args, **kwargs)

    monkeypatch.setattr(boundary, "supported", lambda: True)
    monkeypatch.setattr(boundary, "_java", lambda fiji, limits: (Path(sys.executable), []))
    monkeypatch.setattr(subprocess, "Popen", start)
    yield source, processes, popen
    assert all(process.poll() is not None for process, _ in processes)
    assert all(not work.exists() for _, work in processes)


def test_result_bytes_come_from_private_output_and_each_invocation_is_fresh(child, invocation, tmp_path):
    invocation["out"] = str(tmp_path / "must-not-write")
    for _ in range(2):
        result = boundary.call(tmp_path, invocation, entry=tmp_path)
        assert result.output == PNG and "path" not in result.reply
    assert not (tmp_path / "must-not-write").exists()
    assert len({p.pid for p, _ in child[1]}) == len({w for _, w in child[1]}) == 2


def test_no_sandbox_means_no_process(monkeypatch, invocation, tmp_path):
    monkeypatch.setattr(boundary, "supported", lambda: False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("unconfined launch"))
    with pytest.raises(boundary.Failed, match="requires a working macOS sandbox"):
        boundary.call(tmp_path, invocation, entry=tmp_path)


@pytest.mark.parametrize("alter,reason", [
    (lambda r: r.update(scriptSha256="wrong"), "changed-script"),
    (lambda r: r.update(output="volume"), "request"),
    (lambda r: r.update(op="arbitrary"), "request"),
    (lambda r: r.update(params={"number": float("nan")}), "request"),
])
def test_invalid_requests_never_launch(child, invocation, tmp_path, alter, reason):
    alter(invocation)
    with pytest.raises(boundary.Failed) as caught:
        boundary.call(tmp_path, invocation, entry=tmp_path)
    assert caught.value.reason == reason and not child[1]


def test_scripts_outside_the_snapshot_are_refused(child, invocation, tmp_path):
    elsewhere = tmp_path / "entry"
    elsewhere.mkdir()
    with pytest.raises(boundary.Failed, match="immutable capability"):
        boundary.call(tmp_path, invocation, entry=elsewhere)
    assert not child[1]


@pytest.mark.parametrize("source,reason,limits", [
    ("import sys; sys.exit(17)", "crash", {}),
    ("print('no reply')", "protocol", {}),
    ("print('@@not-json')", "protocol", {}),
    ("print('@@{\"ok\":true,\"kind\":\"table\"}')", "protocol", {}),
    ("print('x'*10000)", "output-limit", {"stdout_bytes": 1000}),
    ("print('x'*10000)", "output-limit", {"protocol_bytes": 1000}),
    ("print('@@{\"ok\":false,\"error\":\"bad script\"}')", "worker", {}),
    ("import os; open(os.environ['HOME']+'/large','wb').write(b'x'*2048)", "file-limit", {"file_bytes": 1024}),
    ("import os,time; os.symlink('/etc/passwd',os.environ['HOME']+'/link'); time.sleep(2)", "unsafe-output", {}),
    ("import time; time.sleep(10)", "memory-limit", {"memory_bytes": 1}),
])
def test_failure_is_contained_reaped_and_retry_works(child, invocation, tmp_path, source, reason, limits):
    good = child[0][0]
    child[0][0] = source
    with pytest.raises(boundary.Failed) as caught:
        boundary.call(tmp_path, invocation, entry=tmp_path, limits=boundary.Limits(**limits))
    assert caught.value.reason == reason
    child[0][0] = good
    assert boundary.call(tmp_path, invocation, entry=tmp_path).output == PNG


def test_reply_does_not_leave_a_live_worker(child, invocation, tmp_path):
    child[0][0] += "; import time; time.sleep(100)"
    assert boundary.call(tmp_path, invocation, entry=tmp_path).output == PNG
    assert child[1][0][0].poll() is not None


def test_deadline_and_inherited_cancellation_reap_owned_child(child, invocation, tmp_path):
    child[0][0] = "import time; time.sleep(100)"
    with pytest.raises(owned_process.Stopped, match="timed out"):
        boundary.call(tmp_path, invocation, entry=tmp_path, limits=boundary.Limits(seconds=.15))
    stop = threading.Event()
    timer = threading.Timer(.15, stop.set)
    timer.start()
    try:
        with pytest.raises(owned_process.Stopped, match="cancelled"), owned_process.scope(stop=stop.is_set):
            boundary.call(tmp_path, invocation, entry=tmp_path)
    finally:
        timer.cancel()


def test_async_cancellation_waits_for_cleanup(child, invocation, tmp_path):
    child[0][0] = "import time; time.sleep(100)"

    async def run():
        task = asyncio.create_task(owned_process.off_loop(boundary.call, tmp_path, invocation, entry=tmp_path))
        while not child[1]:
            await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert child[1][0][0].poll() is not None
        assert not child[1][0][1].exists()

    asyncio.run(run())


def test_memory_supervision_counts_only_owned_tree(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0,
                        "1 0 900\n2 1 10\n3 2 20\n4 3 30\n5 1 800\n"))
    assert boundary._memory(2) == 60 * 1024


def test_failed_memory_inspection_stops_execution(child, invocation, tmp_path, monkeypatch):
    child[0][0] = "import time; time.sleep(100)"
    monkeypatch.setattr(boundary, "_memory", lambda *_: (_ for _ in ()).throw(boundary.Failed("supervision", "no ps")))
    with pytest.raises(boundary.Failed, match="no ps"):
        boundary.call(tmp_path, invocation, entry=tmp_path)


@pytest.mark.parametrize("changes", [{"seconds": float("inf")}, {"heap_mb": 1.2}, {"work_entries": 0}])
def test_limits_must_be_finite_and_positive(changes):
    with pytest.raises(ValueError):
        boundary.Limits(**changes)


@pytest.fixture
def real_runtime():
    value = os.environ.get("FIJI_SANDBOX_TEST_RUNTIME")
    if not value:
        pytest.skip("opt-in isolated Fiji runtime not supplied")
    if not boundary.supported():
        pytest.skip("macOS sandbox unavailable")
    return Path(value).resolve(strict=True)


@pytest.fixture
def real_processes(monkeypatch):
    processes = []
    popen = subprocess.Popen

    def start(args, **kwargs):
        process = popen(args, **kwargs)
        if args[0] == boundary.EXEC:
            processes.append((process, Path(kwargs["cwd"])))
        return process

    monkeypatch.setattr(subprocess, "Popen", start)
    yield processes
    assert processes
    assert all(process.poll() is not None for process, _ in processes)
    assert all(not work.exists() for _, work in processes)


@pytest.fixture
def real_request(invocation, real_processes):
    import cv2
    import numpy as np

    pixels = np.zeros((32, 32, 3), np.uint8)
    pixels[8:24, 8:24] = 200
    assert cv2.imwrite(invocation["image"], pixels)
    return invocation


def script(invocation, text):
    path = Path(invocation["script"])
    path.write_text(text)
    invocation["scriptSha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


def test_real_command_and_independent_macro_agree(real_runtime, real_request, tmp_path):
    import cv2
    import numpy as np

    real_request.pop("script")
    real_request.update(command="Gaussian Blur...", options="sigma=2")
    got = boundary.call(real_runtime, real_request)
    reference = boundary.run_macro(real_runtime, Path(real_request["image"]), "Gaussian Blur...", "sigma=2")
    assert np.array_equal(cv2.imdecode(np.frombuffer(got.output, np.uint8), 1),
                          cv2.imdecode(np.frombuffer(reference.output, np.uint8), 1))
    assert boundary.call(real_runtime, {"op": "has_command", "command": "Gaussian Blur..."}).reply["found"]


def test_real_native_hostile_script_is_confined(real_runtime, real_request, tmp_path, monkeypatch):
    protected = tmp_path / "private-test-fixture"
    protected.write_text("FAKE-PRIVATE-TEST")
    marker = real_runtime / "must-not-be-created-by-contribution"
    assert not marker.exists()
    sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(180)", "FAKE-SENTINEL-ARGS"],
                                env={"FAKE_SENTINEL_SECRET": "FAKE-SENTINEL-ARGS"}, close_fds=True)
    held = os.open(protected, os.O_RDONLY)
    os.dup2(held, 700, inheritable=True)
    monkeypatch.setenv("OPENAI_API_KEY", "FAKE-KEY-MUST-NOT-INHERIT")
    code = '''import groovy.json.JsonOutput
import com.sun.jna.NativeLibrary
import com.sun.jna.Memory
def c = NativeLibrary.getInstance('c')
def results = [:]
def check = { name, action -> try { results[name] = action() } catch(Throwable e) { results[name] = 'DENIED' } }
check('file') { new File(PROTECTED).text }
check('alias') { new File('/System/Volumes/Data'+PROTECTED).text }
check('metadata') { new File(PROTECTED).exists() }
check('nativeOpen') { c.getFunction('open').invokeInt([PROTECTED, 0] as Object[]) }
check('fd') { c.getFunction('read').invokeLong([700, new Memory(128), 128L] as Object[]) }
check('fork') { c.getFunction('fork').invokeInt([] as Object[]) }
check('signal') { c.getFunction('kill').invokeInt([SENTINEL, 15] as Object[]) }
check('network') { new Socket('127.0.0.1', PORT).close(); 'ALLOWED' }
check('runtime') { new File(MARKER).text = 'ALLOWED'; 'ALLOWED' }
check('processInfo') { ProcessHandle.of(SENTINEL as long).get().info().commandLine().orElse('unavailable') }
check('sysctl') { def mib=new Memory(12); mib.setInt(0,1); mib.setInt(4,49); mib.setInt(8,SENTINEL); def data=new Memory(8192); data.clear(); def size=new Memory(8); size.setLong(0,8192); [rc:c.getFunction('sysctl').invokeInt([mib,3,data,size,null,0L] as Object[]), leaked:new String(data.getByteArray(0,8192)).contains('FAKE-SENTINEL-ARGS')] }
check('env') { System.getenv('OPENAI_API_KEY') }
check('spawn') { new ProcessBuilder('/bin/echo','ALLOWED').start().waitFor() }
println('FACTS '+JsonOutput.toJson(results))
return imp
'''
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    for key, value in {"PROTECTED": str(protected), "MARKER": str(marker), "SENTINEL": sentinel.pid,
                       "PORT": listener.getsockname()[1]}.items():
        code = code.replace(key, json.dumps(value))
    script(real_request, code)
    try:
        result = boundary.call(real_runtime, real_request, entry=tmp_path)
        assert sentinel.poll() is None
        assert protected.read_text() == "FAKE-PRIVATE-TEST" and not marker.exists()
        facts = json.loads(next(line[6:] for line in result.log.splitlines() if line.startswith("FACTS ")))
        assert facts == {"file": "DENIED", "alias": "DENIED", "metadata": False, "nativeOpen": -1,
                         "fd": -1, "fork": -1, "signal": -1, "network": "DENIED", "runtime": "DENIED",
                         "processInfo": "DENIED", "sysctl": {"rc": -1, "leaked": False}, "env": None,
                         "spawn": "DENIED"}
        assert result.output.startswith(b"\x89PNG")
    finally:
        listener.close()
        os.close(700)
        os.close(held)
        sentinel.terminate()
        sentinel.wait(timeout=5)


@pytest.mark.parametrize("code,reason,limits", [
    ("System.exit(17)", "crash", {}),
    ("println('x'*200000); return imp", "output-limit", {"stdout_bytes": 65536}),
    ("new File(System.getProperty('user.home'),'large').bytes=new byte[2097152]; return imp", "file-limit", {"file_bytes": 1048576}),
    ("return imp", "memory-limit", {"memory_bytes": 1024}),
    ("new byte[400*1024*1024]; return imp", "worker", {}),
])
def test_real_resource_failures_and_recovery(real_runtime, real_request, tmp_path, code, reason, limits):
    script(real_request, code)
    with pytest.raises(boundary.Failed) as caught:
        boundary.call(real_runtime, real_request, entry=tmp_path, limits=boundary.Limits(**limits))
    assert caught.value.reason == reason
    script(real_request, "return imp")
    assert boundary.call(real_runtime, real_request, entry=tmp_path).output.startswith(b"\x89PNG")


def test_real_cancellation_deadline_and_retry(real_runtime, real_request, tmp_path):
    script(real_request, "while(true) Thread.sleep(1000)")
    stop = threading.Event()
    timer = threading.Timer(1, stop.set)
    timer.start()
    try:
        with pytest.raises(owned_process.Stopped, match="cancelled"), owned_process.scope(stop=stop.is_set):
            boundary.call(real_runtime, real_request, entry=tmp_path)
    finally:
        timer.cancel()
    with pytest.raises(owned_process.Stopped, match="timed out"):
        boundary.call(real_runtime, real_request, entry=tmp_path, limits=boundary.Limits(seconds=1))
    script(real_request, "return imp")
    assert boundary.call(real_runtime, real_request, entry=tmp_path).output.startswith(b"\x89PNG")


def test_private_output_cannot_be_a_link_or_fifo(tmp_path):
    target = tmp_path / 'target'
    target.write_bytes(b'private')
    link = tmp_path / 'link'
    link.symlink_to(target)
    with pytest.raises(boundary.Failed):
        boundary._output(link, boundary.Limits())
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo)
    with pytest.raises(boundary.Failed, match='regular private file'):
        boundary._output(fifo, boundary.Limits())


def test_work_folder_limits_count_entries_and_total_bytes(tmp_path):
    for i in range(3):
        (tmp_path / str(i)).write_bytes(b'12345')
    with pytest.raises(boundary.Failed, match='work folder limit'):
        boundary._work_size(tmp_path, boundary.Limits(work_entries=2))
    with pytest.raises(boundary.Failed, match='work folder limit'):
        boundary._work_size(tmp_path, boundary.Limits(work_bytes=10))


def test_macro_injection_is_refused_before_launch(child, invocation, tmp_path):
    with pytest.raises(boundary.Failed, match='written safely'):
        boundary.run_macro(tmp_path, Path(invocation['image']), 'Gaussian Blur...', 'sigma=2"); exec("evil')
    assert not child[1]


def test_runtime_cannot_point_java_outside_the_generation(tmp_path):
    java = tmp_path / 'java/test/Contents/Home/bin/java'
    java.parent.mkdir(parents=True)
    java.symlink_to(sys.executable)
    with pytest.raises(boundary.Failed):
        boundary._java(tmp_path, boundary.Limits())


def test_log_retention_is_bounded(child, invocation, tmp_path):
    child[0][0] = "print('x'*100000); " + child[0][0]
    got = boundary.call(tmp_path, invocation, entry=tmp_path, limits=boundary.Limits(log_bytes=1024))
    assert len(got.log.encode()) <= 1024 and got.output == PNG


def test_removed_temporary_directory_does_not_fail_supervision(monkeypatch, tmp_path):
    def walk(*args, onerror, **kwargs):
        onerror(FileNotFoundError('temporary directory already removed'))
        return iter(())

    monkeypatch.setattr(os, 'walk', walk)
    boundary._work_size(tmp_path, boundary.Limits())


def test_unreadable_work_directory_fails_supervision(monkeypatch, tmp_path):
    def walk(*args, onerror, **kwargs):
        onerror(PermissionError('unreadable directory'))
        return iter(())

    monkeypatch.setattr(os, 'walk', walk)
    with pytest.raises(PermissionError):
        boundary._work_size(tmp_path, boundary.Limits())


def test_real_table_and_reference_csv_are_available(real_runtime, real_request):
    import csv
    import io

    real_request.pop('script')
    real_request.update(command='Measure', options='', output='table.objects')
    got = boundary.call(real_runtime, real_request)
    reference = boundary.run_macro(real_runtime, Path(real_request['image']), 'Measure', '', table=True)
    rows = list(csv.reader(io.StringIO(reference.output.decode())))
    assert got.output is None and got.reply['kind'] == 'table'
    assert len(got.reply['rows']) == len(rows)-1 == 1
    assert 'Area' in [c['name'] for c in got.reply['columns']] and 'Area' in rows[0]


def test_missing_live_memory_sample_is_not_zero(child, invocation, tmp_path, monkeypatch):
    child[0][0] = "import time; time.sleep(100)"
    monkeypatch.setattr(boundary, '_memory', lambda *_: None)
    with pytest.raises(boundary.Failed, match='missing from its memory check'):
        boundary.call(tmp_path, invocation, entry=tmp_path)


def test_missing_memory_during_exit_waits_for_confirmed_exit(child, tmp_path, monkeypatch):
    child[0][0] = ("import pathlib,time\npathlib.Path('ready').touch()\n"
                   "while not pathlib.Path('exit').exists(): time.sleep(.001)\n")
    samples = []
    memory = boundary._memory

    def missing(pid, identities):
        process, work = child[1][0]
        if not (work / 'ready').exists():
            return memory(pid, identities)
        samples.append(pid)
        assert process.poll() is None
        (work / 'exit').touch()
        return None

    monkeypatch.setattr(boundary, '_memory', missing)
    with tempfile.TemporaryDirectory(dir=tmp_path) as work:
        assert boundary._execute(tmp_path, Path(work), (), [], None, boundary.Limits())[0] == {'ok': True}
    assert samples and child[1][0][0].returncode == 0


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS native process API')
def test_native_exit_while_another_thread_reaps_is_not_a_supervision_failure(child, tmp_path, monkeypatch):
    from tests.waiting import HANG_S

    # EOF lets the caller enter blocking waitpid. Keep the child alive until that
    # waiter holds the process lock, instead of racing a 30 ms child lifetime.
    child[0][0] = ("import os,pathlib,time\nos.close(1); os.close(2)\n"
                   "while not pathlib.Path('exit').exists(): time.sleep(.001)\n")
    reaped = threading.Event()
    inspected = threading.Event()
    native_memory = boundary._memory
    waitpid = child[2]._try_wait

    def waiting(process, flags):
        if flags == 0:
            (child[1][0][1] / 'exit').touch()
        result = waitpid(process, flags)
        if flags == 0 and result[0] == process.pid:
            reaped.set()
            assert inspected.wait(HANG_S)
        return result

    def memory(pid, identities):
        # A sample started before reaping may legitimately describe a live child.
        after_reap = reaped.is_set()
        result = native_memory(pid, identities)
        if after_reap:
            assert result is None
            assert child[1][0][0].poll() is None
            inspected.set()
        return result

    monkeypatch.setattr(child[2], '_try_wait', waiting)
    monkeypatch.setattr(boundary, '_memory', memory)
    with tempfile.TemporaryDirectory(dir=tmp_path) as work:
        assert boundary._execute(tmp_path, Path(work), (), [], None, boundary.Limits())[0] == {'ok': True}
    assert reaped.is_set() and inspected.is_set() and child[1][0][0].returncode == 0


def test_compressed_picture_dimensions_are_bounded_before_parent_decode(child, invocation, tmp_path):
    bomb = PNG[:16] + (100000).to_bytes(4) + (100000).to_bytes(4) + PNG[24:]
    child[0][0] = child[0][0].replace(PNG.hex(), bomb.hex())
    with pytest.raises(boundary.Failed, match='16 million pixels'):
        boundary.call(tmp_path, invocation, entry=tmp_path)


def test_invalid_image_header_is_rejected_before_parent_decode(child, invocation, tmp_path):
    child[0][0] = child[0][0].replace(PNG.hex(), b'not a picture'.hex())
    with pytest.raises(boundary.Failed, match='not a PNG'):
        boundary.call(tmp_path, invocation, entry=tmp_path)


def native_info(pid, parent=1, memory=100, started=(10, 20)):
    info = boundary._TaskAllInfo()
    info.bsd.pid, info.bsd.ppid = pid, parent
    info.bsd.start_seconds, info.bsd.start_microseconds = started
    info.task.resident_bytes = memory
    return info


def test_native_memory_tracks_only_descendants_and_keeps_creation_identity(monkeypatch):
    rows = {2: native_info(2, memory=10), 3: native_info(3, 2, 20), 4: native_info(4, 3, 30)}
    monkeypatch.setattr(boundary, '_native_process', rows.get)
    monkeypatch.setattr(boundary, '_native_children', lambda p: {2: (3,), 3: (4,)}.get(p, ()))
    identities = {}
    assert boundary._native_memory(2, identities) == 60
    assert identities == {2: (10, 20), 3: (10, 20), 4: (10, 20)}
    rows[2] = native_info(2, started=(11, 20))
    with pytest.raises(boundary.Failed, match='identity changed'):
        boundary._native_memory(2, identities)


def test_native_memory_handles_exited_children_and_missing_root(monkeypatch):
    monkeypatch.setattr(boundary, '_native_process', lambda p: native_info(2) if p == 2 else None)
    monkeypatch.setattr(boundary, '_native_children', lambda p: (3,))
    assert boundary._native_memory(2, {}) == 100
    assert boundary._native_memory(3, {}) is None


def test_native_memory_rejects_reparented_or_reused_child(monkeypatch):
    monkeypatch.setattr(boundary, '_native_process', lambda p: native_info(p, parent=99))
    monkeypatch.setattr(boundary, '_native_children', lambda p: (3,) if p == 2 else ())
    with pytest.raises(boundary.Failed, match='identity changed'):
        boundary._native_memory(2, {})


def test_native_memory_bounds_deep_trees(monkeypatch):
    monkeypatch.setattr(boundary, 'PROCESS_LIMIT', 3)
    monkeypatch.setattr(boundary, '_native_process', lambda p: native_info(p, p - 1))
    monkeypatch.setattr(boundary, '_native_children', lambda p: (p + 1,))
    with pytest.raises(boundary.Failed, match='inspection limit'):
        boundary._native_memory(2, {})


@pytest.mark.parametrize('size,error,missing', [
    (0, errno.ESRCH, True), (0, errno.EPERM, False), (0, 0, False),
    (ctypes.sizeof(boundary._TaskAllInfo) - 1, 0, False),
])
def test_native_memory_read_errors_fail_closed(monkeypatch, size, error, missing):
    from types import SimpleNamespace

    def query(*args):
        ctypes.set_errno(error)
        return size

    monkeypatch.setattr(boundary, '_libproc', lambda: SimpleNamespace(proc_pidinfo=query))
    if missing:
        assert boundary._native_process(5) is None
    else:
        with pytest.raises(boundary.Failed, match='identity and memory'):
            boundary._native_process(5)


@pytest.mark.parametrize('count,error', [(256, 0), (-1, errno.EPERM), (0, errno.EPERM), (1, 0)])
def test_native_children_refuses_truncation_errors_and_invalid_pids(monkeypatch, count, error):
    from types import SimpleNamespace

    def query(*args):
        ctypes.set_errno(error)
        return count

    monkeypatch.setattr(boundary, '_libproc', lambda: SimpleNamespace(proc_listchildpids=query))
    with pytest.raises(boundary.Failed):
        boundary._native_children(5)


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS native process API')
def test_native_api_reads_owned_child_memory_without_spawning_a_sampler(monkeypatch):
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('spawned sampler'))
        assert process.pid in boundary._native_children(os.getpid())
        identity = {}
        assert boundary._memory(process.pid, identity) > 0
        assert boundary._memory(process.pid, identity) > 0
        assert identity[process.pid][0] > 0
    finally:
        process.terminate()
        process.wait(timeout=5)
    assert boundary._native_process(process.pid) is None


def test_native_memory_bounds_identity_history(monkeypatch):
    monkeypatch.setattr(boundary, 'PROCESS_LIMIT', 2)
    monkeypatch.setattr(boundary, '_native_process', lambda p: native_info(p))
    monkeypatch.setattr(boundary, '_native_children', lambda p: ())
    with pytest.raises(boundary.Failed, match='inspection limit'):
        boundary._native_memory(4, {2: (10, 20), 3: (10, 20)})
