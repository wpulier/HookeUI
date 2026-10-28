"""Participate in the machine-wide heavy-work slot without reclaiming another owner."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import time

# Explicit operator/test override; ordinary app launches share the same machine slot.
DEFAULT_SLOT = Path('/tmp/hooke-heavy-slot')


@contextmanager
def heavy_work(purpose, *, wait_seconds=0, check=lambda: None, waiting=lambda value: None):
    folder = Path(os.environ.get('MICROMIND_HEAVY_SLOT', str(DEFAULT_SLOT)))
    deadline = time.monotonic() + wait_seconds
    announced = False
    while True:
        check()
        try:
            folder.mkdir(mode=0o700)
            break
        except FileExistsError as error:
            if time.monotonic() >= deadline:
                raise RuntimeError('The machine heavy-work slot stayed occupied beyond this admission’s waiting allowance') from error
            if not announced:
                waiting(True)
                announced = True
            time.sleep(min(.25, max(0, deadline-time.monotonic())))
    token = secrets.token_hex(16)
    identity = folder.stat().st_ino
    record = {'pid': os.getpid(), 'token': token, 'purpose': purpose}
    receipt = folder / 'owner.json'
    try:
        with receipt.open('x') as stream:
            json.dump(record, stream)
            stream.flush(); os.fsync(stream.fileno())
        check()
        if announced:
            waiting(False)
        yield
    finally:
        # A replacement reservation, changed receipt or extra evidence is never removed.
        if folder.exists() and not folder.is_symlink() and folder.stat().st_ino == identity:
            try:
                if json.loads(receipt.read_text()) == record and set(folder.iterdir()) == {receipt}:
                    receipt.unlink(); folder.rmdir()
            except (OSError, ValueError):
                pass
