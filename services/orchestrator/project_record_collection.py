"""Review and retain a whole Record folder through ordinary immutable intake.

This internal storage adapter does not authorize access or start recording. Its
caller must establish inclusion authority for the *whole* shared dataset. The
review digest pins the bytes shown for inclusion; it is not an access credential.
Capture problems remain evidence. Folder completeness is not scientific validity
or proof that every external reference in a conversation has been collected.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat

from env_worker.source_directory import MAX_ENTRIES, index
from .downloads import SPARE
from .project_media import MediaReference, _background, _stamp
from .project_roots import ProjectLocationError
from .scientific_directory_intake import DirectoryIntake
from .scientific_file_intake import WORKING_BYTES
from .scientific_files import CHUNK_BYTES
from .scientific_projects import MAX_DOCUMENT, _json

MAX_SUMMARY = 1 << 20
MAX_DEPTH = 32


@dataclass(frozen=True)
class RecordMember:
    path: str
    size: int
    stamp: tuple
    sha256: str


@dataclass(frozen=True)
class RecordReview:
    folder: Path
    root_stamp: tuple
    directories: tuple
    members: tuple[RecordMember, ...]
    summary: bytes

    def wire(self):
        value = {'format': 'hooke-record-collection-v1',
                 'dataset': json.loads(self.summary),
                 'scope': 'whole-record-folder', 'crossProjectScope': 'included-without-trimming',
                 'externalReferences': 'not-collected',
                 'members': [{'path': m.path, 'bytes': m.size, 'sha256': m.sha256} for m in self.members]}
        value['reviewDigest'] = hashlib.sha256(_json(value)).hexdigest()
        value['bytes'] = sum(m.size for m in self.members)
        value['requiredFreeBytes'] = value['bytes'] + WORKING_BYTES + SPARE
        if len(_json(value)) + len(_json(_entries(self.members))) > MAX_DOCUMENT - (64 << 10):
            raise ProjectLocationError('Record collection metadata exceeds the supported document allowance')
        return value


@contextmanager
def _root(folder):
    descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _file(root, name):
    """Open each component relative to its verified parent, never follow a link."""
    parts = name.split('/')
    descriptor = os.dup(root)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor); descriptor = child
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(child, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ProjectLocationError('Record collection requires regular files')
            yield source
    finally:
        os.close(descriptor)


def _membership(root):
    files, directories = [], []
    def visit(descriptor, prefix='', depth=0):
        if depth > MAX_DEPTH or len(directories) >= MAX_ENTRIES:
            raise ProjectLocationError('Record folder exceeds the supported directory allowance')
        directories.append((prefix, _stamp(os.fstat(descriptor))))
        with os.scandir(descriptor) as listing:
            children = []
            for child in listing:
                children.append(child.name)
                if len(children) > MAX_ENTRIES:
                    raise ProjectLocationError('Record folder exceeds the supported entry allowance')
        for name in sorted(children):
            relative = prefix + name
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                try:
                    if _stamp(os.fstat(child)) != _stamp(info):
                        raise ProjectLocationError('Record membership changed; review it again')
                    visit(child, relative + '/', depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                files.append((relative, info.st_size, _stamp(info)))
                if len(files) > MAX_ENTRIES:
                    raise ProjectLocationError('Record folder exceeds the supported file allowance')
            else:
                raise ProjectLocationError('Record collection cannot follow links or special files')
    visit(root)
    return tuple(files), tuple(directories)


def _entries(members):
    entries, size = [], 0
    for member in members:
        entries.append({'path': member.path, 'offset': size, 'size': member.size})
        size += member.size
    return index({'format': 'source-directory-v1', 'entries': entries}, size)


def _stream(source, stamp, check, output=None):
    if _stamp(os.fstat(source.fileno())) != stamp:
        raise ProjectLocationError('Recorded bytes changed; review the whole dataset again')
    digest = hashlib.sha256()
    while True:
        check()
        block = source.read(CHUNK_BYTES)
        if not block:
            break
        digest.update(block)
        if output is not None:
            output(block)
    if _stamp(os.fstat(source.fileno())) != stamp:
        raise ProjectLocationError('Recorded bytes changed during collection')
    return digest.hexdigest()


def _summary(root, files):
    sizes = {name: size for name, size, _ in files}
    if not 0 < sizes.get('dataset.json', 0) <= MAX_SUMMARY:
        raise ProjectLocationError('Record summary is missing or exceeds its allowance')
    with _file(root, 'dataset.json') as source:
        raw = source.read(MAX_SUMMARY + 1)
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise ProjectLocationError('Record summary is unreadable') from error
    if (not isinstance(value, dict) or type(value.get('schema')) is not int or value.get('schema') != 1
            or value.get('kind') != 'microscopemind-dataset' or value.get('status') != 'complete'
            or not isinstance(value.get('id'), str) or not isinstance(value.get('endCheck'), dict)):
        raise ProjectLocationError('Wait for Record to stop and finish its saved-data check')
    if 'events.jsonl' not in sizes:
        raise ProjectLocationError('Record events are missing; recover them before collection')
    video = value.get('video')
    parts = video.get('parts') if isinstance(video, dict) else None
    if not isinstance(parts, list) or len(parts) > MAX_ENTRIES:
        raise ProjectLocationError('Record video membership is unreadable')
    for part in parts:
        if not isinstance(part, dict):
            raise ProjectLocationError('Record video membership is unreadable')
        for field in ('video', 'sidecar'):
            name = part.get(field)
            if field == 'sidecar' and name is None and isinstance(part.get('timing'), dict):
                continue
            if not isinstance(name, str) or name not in sizes:
                raise ProjectLocationError('A declared Record part is missing; recover it before collection')
    return raw


def review_record(folder, *, max_bytes, check=lambda: None):
    """Bounded whole-folder review, including hashes and an estimate before copying."""
    _background()
    folder = Path(folder).absolute()
    with _root(folder) as root:
        files, directories = _membership(root)
        if type(max_bytes) is not int or not 0 <= sum(row[1] for row in files) <= max_bytes:
            raise ProjectLocationError('Record collection exceeds the declared byte allowance')
        raw = _summary(root, files)
        members = []
        for name, size, stamp in files:
            with _file(root, name) as source:
                members.append(RecordMember(name, size, stamp, _stream(source, stamp, check)))
        _entries(members)  # same path/range limits as ordinary folder intake
        if _membership(root) != (files, directories) or _stamp(folder.lstat()) != directories[0][1]:
            raise ProjectLocationError('Record membership changed; review it again')
        # The summary was read separately; bind it to the hashed file in this review.
        summary_member = next(m for m in members if m.path == 'dataset.json')
        if hashlib.sha256(raw).hexdigest() != summary_member.sha256:
            raise ProjectLocationError('Record summary changed; review it again')
        review = RecordReview(folder, directories[0][1], directories, tuple(members), raw)
        review.wire()  # metadata must fit before a caller offers collection
        return review


class _RecordIntake(DirectoryIntake):
    def __init__(self, *args, metadata, check_source, **kwargs):
        self.metadata, self.check_source = metadata, check_source
        super().__init__(*args, **kwargs)

    def document(self, ref):
        value = super().document(ref)
        value['state']['recordCollection'] = self.metadata
        return value

    @contextmanager
    def acceptance(self):
        self.check_source()
        yield


def collect_record(stores, session, review, *, reviewed_digest, request, max_bytes,
                   producer, stopping=lambda: False):
    """Retain an admitted whole-dataset review; never select only this Project's frames."""
    _background()
    if not isinstance(review, RecordReview):
        raise ProjectLocationError('Review the whole Record dataset before collecting it')
    metadata = review.wire()
    if reviewed_digest != metadata['reviewDigest']:
        raise ProjectLocationError('The dataset differs from the inclusion review')
    owner = stores.locations.project(session)
    if owner is None:
        raise ProjectLocationError('Record collection needs an explicitly opened portable Project')
    metadata['projectOrigin'] = owner.origin
    def check():
        if stopping():
            raise ProjectLocationError('Record collection stopped; no dataset was added')
    with _root(review.folder) as root:
        expected = tuple((m.path, m.size, m.stamp) for m in review.members)
        def current():
            check()
            if (stores.locations.project(session) != owner
                    or _stamp(review.folder.lstat()) != review.root_stamp
                    or _membership(root) != (expected, review.directories)):
                raise ProjectLocationError('Dataset or destination changed; review collection again')
        current()
        intake = _RecordIntake(stores, session, request, name=review.folder.name,
            size=metadata['bytes'], max_bytes=max_bytes, entries=_entries(review.members),
            producer=producer, metadata=metadata, check_source=current)
        try:
            for member in review.members:
                with _file(root, member.path) as source:
                    if _stream(source, member.stamp, check, intake.write) != member.sha256:
                        raise ProjectLocationError('Recorded content differs from the inclusion review')
            current()
            saved = intake.publish(stopping)
        finally:
            intake.close()
    return MediaReference(owner.origin, session, saved.id, saved.revision, saved.digest)
