"""Actual macOS device access refusal, separate from synthetic operation references."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from orchestrator import envs, sandbox

PROBE = '''import ctypes, json
metal = ctypes.CDLL('/System/Library/Frameworks/Metal.framework/Metal')
metal.MTLCreateSystemDefaultDevice.restype = ctypes.c_void_p
print(json.dumps({'metal_device': bool(metal.MTLCreateSystemDefaultDevice())}))
'''


@pytest.mark.skipif(sys.platform != 'darwin' or not sandbox.available(), reason='actual macOS sandbox required')
def test_cpu_boundary_denies_actual_metal_device_while_default_allows_it(tmp_path):
    exe = Path(sys.executable)
    environment = sandbox.confine(envs.clean_env(), tmp_path)
    command = [str(exe), '-I', '-c', PROBE]
    readings = []
    for gpu in (True, False):
        result = subprocess.run(sandbox.wrap(command, read=[sandbox.python_home(exe), sys.prefix], write=[tmp_path], gpu=gpu),
                                env=environment, capture_output=True, timeout=20, check=True)
        readings.append(json.loads(result.stdout))
    assert readings == [{'metal_device': True}, {'metal_device': False}]


@pytest.mark.skipif(sys.platform != 'darwin' or not sandbox.available(), reason='actual macOS sandbox required')
def test_bundle_boundary_refuses_fork_and_subprocess_but_keeps_threads(tmp_path):
    exe = Path(sys.executable)
    program = '''import os, subprocess, threading, json
result = {}
try:
    child = os.fork()
    if child == 0: os._exit(0)
    os.waitpid(child, 0)
    result['fork'] = True
except PermissionError: result['fork'] = False
try:
    subprocess.run(['/usr/bin/true'], check=True)
    result['subprocess'] = True
except PermissionError: result['subprocess'] = False
thread = threading.Thread(target=lambda: result.update(thread=True))
thread.start(); thread.join()
print(json.dumps(result))
'''
    done = subprocess.run(sandbox.wrap([str(exe), '-I', '-c', program],
                          read=[sandbox.python_home(exe), sys.prefix], write=[tmp_path], gpu=False, children=False),
                          env=sandbox.confine(envs.clean_env(), tmp_path), cwd=tmp_path,
                          capture_output=True, text=True, timeout=10)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {'fork': False, 'subprocess': False, 'thread': True}
