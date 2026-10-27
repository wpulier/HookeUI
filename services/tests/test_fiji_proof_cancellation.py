"""A controlled child inside the actual Fiji generation/install queue; no native Fiji download."""
import sys
import threading

from orchestrator import envs, fiji_bindings, fiji_generations as fiji
from tests.test_fiji_generations import home
from tests.test_owned_catalog_workers import outer_watchdog
from tests.waiting import HANG_S, until


def test_cancelled_binding_proof_keeps_old_generation_and_releases_queue(home, monkeypatch):
    assert fiji.first() == 1
    created = threading.Event()
    children = []
    pid = home / 'proof-child-pid'

    def controlled_proof(folder):
        # Only the new, unproved fixture generation is changed. Generation 1 stays intact.
        launcher = folder / 'fiji'
        launcher.write_text(f'''#!{sys.executable}
import os, pathlib, time
pathlib.Path({str(pid)!r}).write_text(str(os.getpid()))
print('{{"ok": true}}', flush=True)
time.sleep(60)
''')
        launcher.chmod(0o700)
        worker = fiji_bindings.Worker()
        children.append(worker)
        created.set()
        try:
            return worker.call({'op': 'controlled'}, n=int(folder.parent.name))
        finally:
            worker.stop()

    monkeypatch.setitem(fiji.PROOFS, 'controlled-proof', controlled_proof)
    queue = envs.Installs()
    queue.start('fiji-analyze-particles')
    assert created.wait(HANG_S), queue.wire()
    try:
        with outer_watchdog(children[0]):
            until(pid.exists, every=.01)
            queue.start('fiji')
            queue.cancel('fiji-analyze-particles')
            queue._thread.join(timeout=HANG_S)
            rows = {r['model']: r for r in queue.wire()}
            assert rows['fiji-analyze-particles']['state'] == 'cancelled', rows  # the user's Stop, said as stopped
            assert rows['fiji-analyze-particles']['says'].startswith('Stopped: ') and 'you pressed Stop' in rows['fiji-analyze-particles']['says']
            assert rows['fiji']['state'] == 'done', rows
            assert fiji.current() == 1
            assert fiji.receipt(2) is None
            assert any(g['n'] == 2 and g['state'] == 'failed' and 'cancelled' in g['why'] for g in fiji.generations())
    finally:
        queue.cancel('fiji')
        queue._thread.join(timeout=3)
