"""Stopping an owned child stops its whole tree, a descendant that left the group with setsid included (C7 case 7e)."""
import os
import subprocess
import sys

from orchestrator import owned_process
from tests.waiting import until


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for(path):
    return int(until(lambda: path.exists() and path.read_text().strip(), what=f'{path.name} to be written'))


def test_a_descendant_in_a_session_of_its_own_is_stopped_with_its_owner(tmp_path):
    record = tmp_path / 'detached-pid'
    # The owner starts one child in a session of its own, as C7's c7-detached adapter did, then waits. The child expires
    # by itself after 15 s, so a failure never leaves it behind for long.
    owner_code = f'''import pathlib, subprocess, time
child = subprocess.Popen(['sleep', '15'], start_new_session=True)
pathlib.Path({str(record)!r}).write_text(str(child.pid))
time.sleep(30)
'''
    owner = subprocess.Popen([sys.executable, '-c', owner_code], start_new_session=True)
    detached = wait_for(record)
    try:
        assert alive(detached)
        assert os.getsid(detached) == detached != os.getpgid(owner.pid)  # it really left the owned group
        owned_process.terminate(owner)
        until(lambda: not alive(detached), every=.05, what='the detached descendant to end with its owner')
        assert owner.poll() is not None
    finally:
        if alive(detached):
            os.kill(detached, 9)
        if owner.poll() is None:
            owner.kill()
            owner.wait()


def test_only_the_owned_tree_is_signalled(tmp_path):
    bystander = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
    owner = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
    try:
        assert bystander.pid not in owned_process.descendants(owner.pid)
        owned_process.terminate(owner)
        assert owner.poll() is not None
        assert bystander.poll() is None
    finally:
        for p in (bystander, owner):
            if p.poll() is None:
                p.kill()
                p.wait()
