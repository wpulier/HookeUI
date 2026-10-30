"""An exact retained source for existing path-based media decoders.

The short-lived host file is disposable, never a new accepted artifact or global
Library registration. Its context owns its lifetime through decoder shutdown.
This does not select an asset, launch a decoder or restore the workspace itself.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from .bounded_files import open_directory
from .downloads import SPARE
from .project_media import MediaReference, _background
from .project_roots import ProjectLocationError
from .scientific_files import CHUNK_BYTES


@dataclass(frozen=True)
class DecoderSource:
    reference: MediaReference
    metadata: dict
    path: Path
    size: int
    sha256: str


@contextmanager
def retained_path(media, reference, *, host_cache, max_bytes, stopping=lambda: False):
    """Materialize one explicitly selected revision without following its original.

    Call off-loop, hold this context until the decoder is closed, and keep its
    exact reference with the request. A mutable host cache cannot become evidence.
    The caller supplies its disk allowance; every chunk retains normal hash checks.
    """
    _background()
    if type(max_bytes) is not int or max_bytes < 0:
        raise ProjectLocationError('Choose a nonnegative retained-media byte allowance')
    metadata, source = media.read(reference)
    root = media.stores.locations.project(reference.session)
    if source.size > max_bytes:
        raise ProjectLocationError('This retained source exceeds the decoder byte allowance')
    cache = Path(host_cache).expanduser()
    resolved = cache.resolve()
    if resolved == root.path or root.path in resolved.parents:
        raise ProjectLocationError('Decoder scratch files belong outside accepted Project storage')
    cache.mkdir(parents=True, exist_ok=True)
    directory = open_directory(cache)  # refuse linked path components
    try:
        pinned = os.fstat(directory)
        def check():
            if stopping():
                raise ProjectLocationError('Opening retained media was stopped')
            current = cache.stat(follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
                raise ProjectLocationError('The decoder scratch location changed')
            if media.stores.locations.project(reference.session) != root:
                raise ProjectLocationError('The retained media Project changed or closed')
        check()
        if shutil.disk_usage(cache).free < source.size + SPARE:
            raise ProjectLocationError('Not enough free space to open this retained media source')
        with tempfile.TemporaryDirectory(prefix='hooke-media-', dir=cache) as temporary:
            # Decoders inspect the bytes. Neither the historical filename nor its
            # extension determines the scientific meaning of this source.
            path = Path(temporary) / 'source.media'
            digest = hashlib.sha256()
            with path.open('x+b') as output:
                for offset in range(0, source.size, CHUNK_BYTES):
                    check()
                    body = source.read(offset, min(CHUNK_BYTES, source.size - offset))
                    digest.update(body)
                    output.write(body)
                output.flush()
                output.seek(0)
                written = hashlib.sha256()
                while body := output.read(CHUNK_BYTES):
                    check()
                    written.update(body)
                if written.hexdigest() != source.sha256:
                    raise ProjectLocationError('The decoder copy failed its content hash check')
            if digest.hexdigest() != source.sha256:
                raise ProjectLocationError('Retained media failed its complete content hash check')
            check()
            path.chmod(0o400)
            yield DecoderSource(reference, metadata, path, source.size, source.sha256)
    finally:
        os.close(directory)
