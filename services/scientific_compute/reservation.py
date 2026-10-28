"""Participate in the machine-wide heavy-work slot without reclaiming another owner."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets

# Explicit operator/test override; ordinary app launches share the same machine slot.
DEFAULT_SLOT = Path('/tmp/hooke-heavy-slot')


@contextmanager
def heavy_work(purpose):
    folder = Path(os.environ.get('MICROMIND_HEAVY_SLOT', str(DEFAULT_SLOT)))
    try:
        folder.mkdir(mode=0o700)
    except FileExistsError as error:
        raise RuntimeError('The machine heavy-work slot is occupied; wait for its owner to release it') from error
    token = secrets.token_hex(16)
    identity = folder.stat().st_ino
    record = {'pid': os.getpid(), 'token': token, 'purpose': purpose}
    receipt = folder / 'owner.json'
    try:
        with receipt.open('x') as stream:
            json.dump(record, stream)
            stream.flush(); os.fsync(stream.fileno())
        yield
    finally:
        # A replacement reservation, changed receipt or extra evidence is never removed.
        if folder.exists() and not folder.is_symlink() and folder.stat().st_ino == identity:
            try:
                if json.loads(receipt.read_text()) == record and set(folder.iterdir()) == {receipt}:
                    receipt.unlink(); folder.rmdir()
            except (OSError, ValueError):
                pass
