"""Export an exact accepted scene through a declared sequence, never its live session.

One confined render host and one owned encoder at a time. Frames and encoded bytes
stream through bounded buffers; only a complete, decoded-and-verified movie is
atomically retained. Packages cannot choose executables, paths or publication.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from . import jobs, owned_process, sandbox, scientific_project_review, worker_resources
from .scientific_file_intake import FileIntake, REQUEST
from .scientific_files import CHUNK_BYTES
from .scientific_projects import InvalidProject, Conflict
from .downloads import SPARE

MAX_BYTES = 256 << 20
WORKING_BYTES = 32 << 20
ENCODER_BYTES = 256 << 20
SECONDS = 600
FORMAT = 'scientific-movie-export-v1'
CODECS = {'mov-qtrle-v1': 'qtrle', 'mov-argb-v1': 'rawvideo'}


def settings(value):
    if (type(value) is not dict or set(value) != {'format', 'width', 'height', 'fps'}
            or type(value['format']) is not str or value['format'] not in CODECS
            or any(type(value[key]) is not int for key in ('width', 'height', 'fps'))
            or not 1 <= value['width'] <= 1024 or not 1 <= value['height'] <= 1024
            or not 1 <= value['fps'] <= 60):
        raise InvalidProject('Choose lossless MOV, a viewport up to 1024 pixels and 1–60 fps')
    return dict(value)


def _sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(CHUNK_BYTES), b''):
            result.update(block)
    return result.hexdigest()


def encoder(environ):
    # Development verification explicitly names the packaged binary. A packaged
    # launch resolves its own Helpers symlink; never silently use Homebrew codecs.
    explicit = environ.get('MICROMIND_MOVIE_FFMPEG')
    found = explicit or shutil.which('ffmpeg', path=environ.get('PATH', ''))
    if not found:
        raise InvalidProject('The packaged movie encoder is unavailable; repair this Hooke installation')
    path = Path(found).resolve()
    if not explicit and path.parent.name != 'Helpers':
        raise InvalidProject('Movie export requires Hooke’s packaged encoder; no system fallback was used')
    if not path.is_file() or not os.access(path, os.X_OK):
        raise InvalidProject('The declared movie encoder is unavailable')
    return path, _sha(path)


@contextmanager
def _child(binary, arguments, work, *, input_pipe=False):
    """No shell, package options, network, GPU, child processes or inherited secrets."""
    if not sandbox.available():
        raise InvalidProject('Movie export requires supported native confinement')
    process = guard = None
    with (work / 'encoder-error.log').open('wb') as errors:
        try:
            command = sandbox.wrap([str(binary), '-hide_banner', '-loglevel', 'error', '-nostdin',
                                    *arguments], [binary], [work], gpu=False, children=False)
            process = subprocess.Popen(command, cwd=work,
                env=sandbox.confine({'PATH': '/usr/bin:/bin', 'LANG': 'C'}, work),
                stdin=subprocess.PIPE if input_pipe else subprocess.DEVNULL,
                stdout=subprocess.DEVNULL if input_pipe else subprocess.PIPE,
                stderr=errors, start_new_session=True)
            guard = worker_resources.Guard(process, work, worker_resources.Limits(
                memory_bytes=ENCODER_BYTES, processes=1, file_bytes=MAX_BYTES,
                work_bytes=MAX_BYTES + (1 << 20), work_entries=16)).start()
            with owned_process.watching(process, SECONDS, 'scientific movie encoder'):
                yield process, guard
            guard.check()
        finally:
            if guard is not None:
                guard.close()
            elif process is not None:
                owned_process.terminate(process)
            if process is not None:
                for stream in (process.stdin, process.stdout):
                    if stream is not None: stream.close()


def _encode(worker, sequence, options, binary, work, check):
    width, height, fps = (options[key] for key in ('width', 'height', 'fps'))
    size = width * height * 4
    # Conservative allowance covers raw ARGB and legacy qtrle literal-run overhead.
    # Admit before rendering the first frame, independently of compressibility.
    if 2 * (size + height * 4) * sequence['count'] + (1 << 20) > MAX_BYTES:
        raise InvalidProject('This movie exceeds the bounded export allowance; use fewer frames or a smaller viewport')
    target = work / 'movie.mov'
    expected = hashlib.sha256()
    with _child(binary, ['-f', 'rawvideo', '-pixel_format', 'rgba', '-video_size', f'{width}x{height}',
                        '-framerate', str(fps), '-protocol_whitelist', 'pipe', '-i', 'pipe:0',
                        '-an', '-threads', '1', '-c:v', CODECS[options['format']], '-pix_fmt', 'argb',
                        '-f', 'mov', '-y', str(target)], work, input_pipe=True) as (process, guard):
        for index in range(sequence['count']):
            check(); guard.check()
            _, rgba = worker.request({'op': 'sequence-render', 'index': index, 'width': width, 'height': height})
            if len(rgba) != size:
                raise InvalidProject('The integration returned an incomplete movie frame')
            expected.update(rgba)
            process.stdin.write(rgba)
        process.stdin.close()
        process.wait()
        if process.returncode:
            raise InvalidProject('The movie encoder failed; the saved tool and previous exports are unchanged')
    check()
    if not 0 < target.stat().st_size <= MAX_BYTES:
        raise InvalidProject('The movie exceeded its output allowance; no result was published')
    # Actual decode verifies lossless pixel/order/count preservation, not merely an
    # encoder exit status. Stream output: no complete frame list or movie in memory.
    actual, decoded = hashlib.sha256(), 0
    with _child(binary, ['-threads', '1', '-protocol_whitelist', 'file', '-i', str(target),
                        '-an', '-threads', '1', '-pix_fmt', 'rgba', '-f', 'rawvideo', 'pipe:1'], work) as (process, guard):
        while True:
            check(); guard.check()
            block = process.stdout.read(CHUNK_BYTES)
            if not block: break
            decoded += len(block)
            if decoded > size * sequence['count']:
                raise InvalidProject('The encoded movie contains unexpected decoded frames')
            actual.update(block)
        process.wait()
        if process.returncode or decoded != size * sequence['count'] or actual.digest() != expected.digest():
            raise InvalidProject('Movie verification failed; no rendered result was published')
    return target, expected.hexdigest()


class MovieIntake(FileIntake):
    def __init__(self, *args, provenance, **kwargs):
        self.provenance = provenance
        super().__init__(*args, **kwargs)

    def document(self, ref):
        result = super().document(ref)
        result['state']['movieExport'] = self.provenance
        result['runtimes'] = [self.provenance['runtime']]
        return result


def export(store, project, session, revision, request, options, *, stopping=lambda: False, environ=os.environ):
    with owned_process.scope(SECONDS, stopping) as bound:
        return _export(store, project, session, revision, request, options, bound=bound, environ=environ)


def _export(store, project, session, revision, request, options, *, bound, environ):
    store = store.for_session(session, write=True)
    def stopping():
        bound.check()
        return False
    options = settings(options)
    if type(revision) is not int or revision < 1:
        raise InvalidProject("Choose an exact accepted revision before exporting")
    if type(request) is not str or REQUEST.fullmatch(request) is None:
        raise InvalidProject('Use a stable movie export request identity')
    # A missing/stale named revision refuses; never resolve to the latest head.
    saved = store.read(project, session, revision=revision)
    source = {'id': saved.id, 'session': saved.session, 'revision': saved.revision, 'digest': saved.digest}
    intent = {'format': FORMAT, 'source': source, 'settings': options}
    key = hashlib.sha256(('movie\0' + request).encode()).hexdigest()
    target = 'p' + hashlib.sha256((session + '\0' + key).encode()).hexdigest()[:32]
    if store._folder(target).exists():
        previous = store.read(target, session, revision=1)
        provenance = previous.document['state'].get('movieExport', {})
        if any(provenance.get(name) != value for name, value in intent.items()):
            raise Conflict('This export request belongs to another saved revision or settings')
        store.file_source(target, session, 1, previous.document['state']['sourceFile']['reference'], check=bound.check)
        return previous
    spec = saved.document['state'].get('scientificView', {})
    if type(spec) is not dict or not {'layers', 'tool'} <= spec.keys():
        raise InvalidProject('Save the tool and all layers, then open that accepted document before exporting')
    binary, digest = encoder(environ)
    store.root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(store.root).free < 2 * MAX_BYTES + SPARE:
        raise InvalidProject('Free disk space before exporting this movie')
    with jobs.MEMORY.reserve(ENCODER_BYTES / (1 << 20)):
        with tempfile.TemporaryDirectory(prefix='.scientific-movie-', dir=store.root) as directory:
            work = Path(directory)
            with scientific_project_review.review(store, project, session, revision, environ=environ) as (captured, worker):
                if captured.digest != saved.digest:
                    raise Conflict('The selected scientific revision changed before export')
                header, _ = worker.request({'op': 'sequence-begin'})
                sequence = header['sequence']
                path, pixels = _encode(worker, sequence, options, binary, work, bound.check)
            # Worker has been reaped before accepted publication; live views were
            # never borrowed and their unsaved state is unaffected.
            provenance = {**intent, 'sequence': sequence, 'runtime': spec['runtime'],
                          'inputs': saved.document['resources'], 'encoder': {'sha256': digest, 'codec': CODECS[options['format']]},
                          'rgbaSha256': pixels, 'derivedDisplay': True}
            owner = MovieIntake(store, session, key, name='Rendered movie.mov', size=path.stat().st_size,
                                max_bytes=MAX_BYTES, provenance=provenance)
            try:
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(CHUNK_BYTES), b''):
                        bound.check(); owner.write(block)
                return owner.publish(stopping)
            finally:
                owner.close()
