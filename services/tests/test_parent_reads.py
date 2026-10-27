"""The parent reads code nobody reviewed only within fixed bounds (Codex's parent-read boundary at 511205a2): a worker's
startup hello is a small object read within its limit, and a child's own output (an import check, a draft's uv) is drained
to its end with only its last words kept, so printing without end can't fill the app's memory or hang it."""
import subprocess
import time

import numpy as np
import pytest

from orchestrator import env_runner, envs, owned_process
from tests.test_env_runner import ENTRY, contribute, home, install  # noqa: F401 - a scratch catalog, a stand-in uv

LOUD_HELLO = '''
import os
import struct

for fd in range(3, 12):  # wherever the worker's reply pipe is: a header of 4 GB announced before its hello
    try:
        os.write(fd, struct.pack(">I", 0xFFFFFFF0))
    except OSError:
        pass

def segment(picture, params):
    return picture[:, :, 0] * 0
'''


def test_a_hello_past_its_limit_is_refused_before_it_is_read(home, monkeypatch):
    m = contribute(home)
    assert install(home, monkeypatch)["state"] == "done"
    (home / "repo" / "models" / "dark-spots" / "adapter.py").write_text(LOUD_HELLO)
    installed = env_runner.in_use(m)
    (installed.entry / "adapter.py").write_text(LOUD_HELLO)  # as if the installed copy were the loud one
    client = env_runner.Client(installed)
    t0 = time.monotonic()
    with pytest.raises(env_runner.Failed, match="didn't start"):
        client.run(np.zeros((8, 8, 3), np.uint8))
    assert time.monotonic() - t0 < 30 and client._process is None, "refused at once, its worker stopped"


def test_a_child_that_prints_without_end_is_drained_bounded_and_ended_by_its_deadline():
    process = subprocess.Popen(["/usr/bin/yes", "x" * 100], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True)
    t0 = time.monotonic()
    with pytest.raises(owned_process.Stopped, match="timed out"):
        with owned_process.watching(process, 1.0, "a loud child"):
            out = owned_process.drained(process)
    assert time.monotonic() - t0 < 10 and process.poll() is not None
    owned_process.terminate(process)
    loud = subprocess.Popen(["/bin/sh", "-c", "yes xxxxxxxxxxxxxxx | head -c 5000000; echo; echo LAST"],
                            stdout=subprocess.PIPE, start_new_session=True)
    out = owned_process.drained(loud)
    assert len(out) <= owned_process.KEEP_BYTES and out.rstrip().endswith("LAST"), "only its last words, all read"


def test_an_install_step_that_prints_without_end_fails_with_its_last_words_only():
    with pytest.raises(envs.Failed) as failed:
        envs._run(["/bin/sh", "-c", "yes xxxxxxxxxxxxxxx | head -c 20000000; echo; echo the real reason; exit 3"],
                  {"PATH": "/usr/bin:/bin"}, lambda: False, "its proof")
    assert "the real reason" in str(failed.value) and len(str(failed.value)) < 2000
