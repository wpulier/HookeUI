"""Immutable revisions of user-owned scientific work, independent of installed software.

The store writes only beneath the supplied retained-data root. Runtime and resource references
are descriptions, never paths to read or instructions to execute. Callers validate their scientific
meaning and permissions before use. A save compares the accepted revision while holding a
process-shared lock; only the atomic head publication makes a revision accepted.
"""
from __future__ import annotations

from .file_locks import fcntl  # the standard module, or Windows' refusing stand-in
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .semantic_resources import InvalidDescriptor, parse_reference

MAX_DOCUMENT = 2 << 20
MAX_REVISIONS = 4096
MAX_HEAD = 300_000
PROJECT_ID = re.compile(r'p[0-9a-f]{32}')
DIGEST = re.compile(r'[0-9a-f]{64}')
IDENTITY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}')


class InvalidProject(ValueError):
    pass


class Conflict(InvalidProject):
    pass


class _OtherSession(InvalidProject):
    pass


def _json(value) -> bytes:
    pending, count, characters = [(value, 0)], 0, 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 64 or count > 200_000:
            raise InvalidProject('Project data is too deeply nested or has too many values')
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise InvalidProject('Project JSON object keys must be strings')
            characters += sum(len(key) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            characters += len(item)
        elif item is not None and type(item) not in (bool, int, float):
            raise InvalidProject('Project data must use JSON-native values')
        if characters > MAX_DOCUMENT + 4096:
            raise InvalidProject('Project data exceeds its size limit')
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                          separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise InvalidProject('Project data must be finite JSON values') from exc


def _identity(value, label):
    if not isinstance(value, str) or IDENTITY.fullmatch(value) is None:
        raise InvalidProject(f'Invalid {label}')
    return value


def _document(value) -> bytes:
    if not isinstance(value, dict) or set(value) != {'kind', 'title', 'state', 'resources', 'runtimes'}:
        raise InvalidProject('Project needs kind, title, state, resources and runtimes only')
    _identity(value['kind'], 'project kind')
    if not isinstance(value['title'], str) or not 0 < len(value['title'].strip()) <= 240:
        raise InvalidProject('Project title must have 1 to 240 characters')
    if not isinstance(value['state'], dict):
        raise InvalidProject('Project state must be a JSON object')
    for field in ('resources', 'runtimes'):
        if not isinstance(value[field], list) or len(value[field]) > 1024:
            raise InvalidProject(f'Project {field} must be a list of at most 1024 references')
    for ref in value['resources']:
        try:
            parse_reference(ref)
        except InvalidDescriptor as exc:
            raise InvalidProject(str(exc)) from exc
    for ref in value['runtimes']:
        if not isinstance(ref, dict) or set(ref) != {'bundle', 'generation', 'operation'}:
            raise InvalidProject('A runtime reference needs bundle, generation and operation')
        if not isinstance(ref['bundle'], str) or not 1 <= len(ref['bundle']) <= 200:
            raise InvalidProject('Invalid bundle identity')
        if any(IDENTITY.fullmatch(part) is None for part in ref['bundle'].split('/')):
            raise InvalidProject('Invalid bundle identity')
        _identity(ref['generation'], 'runtime generation')
        _identity(ref['operation'], 'runtime operation')
    body = _json(value)
    if len(body) > MAX_DOCUMENT:
        raise InvalidProject(f'Project document exceeds {MAX_DOCUMENT} bytes')
    return body


@dataclass(frozen=True)
class ProjectRevision:
    id: str
    session: str
    revision: int
    digest: str
    parent: str | None
    at: float
    _document: bytes
    transaction: str | None = None

    @property
    def document(self) -> dict:
        return json.loads(self._document)


class ProjectStore:
    def __init__(self, root: Path, *, runtime_root: Path | None = None):
        self.root = Path(root)
        self.runtime_root = runtime_root
        self.runtime_problem = None
        if runtime_root is not None:
            from .scientific_retention import Retained
            try:
                self.register_runtime()
            except (Retained, OSError) as error:
                self.runtime_problem = str(error)  # Reading saved work stays available; publication retries registration.

    @property
    def workflow_library_root(self):
        return self.root.parent / 'workflow-library'

    def for_session(self, session, *, write=False):
        """Capture the owner before a service uses storage paths or starts work."""
        _identity(session, 'session')
        return self

    def register_runtime(self):
        if self.runtime_root is not None:
            from .scientific_retention import bind
            bind(self, self.runtime_root)
            self.runtime_problem = None

    def _folder(self, project: str) -> Path:
        if not isinstance(project, str) or PROJECT_ID.fullmatch(project) is None:
            raise InvalidProject('Invalid project identity')
        folder = self.root / project
        if folder.is_symlink():
            raise InvalidProject('A project folder cannot be a symlink')
        return folder

    @staticmethod
    def _read(path: Path, limit: int) -> bytes:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                import stat
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise InvalidProject('A project record must be a regular file')
                body = stream.read(limit + 1)
        except OSError as exc:
            raise InvalidProject('Project record is missing or unreadable') from exc
        if len(body) > limit:
            raise InvalidProject('Project record exceeds its size limit')
        return body

    def _head(self, folder: Path, session: str) -> list[str]:
        from .scientific_import_transaction import reading
        with reading(self.root):
            return self._read_head(folder, session)

    def _read_head(self, folder, session):
        from .scientific_import_transaction import require_visible
        require_visible(self.root)
        try:
            head = json.loads(self._read(folder / 'head.json', MAX_HEAD))
        except (ValueError, RecursionError) as exc:
            raise InvalidProject('Project history is unreadable') from exc
        if not isinstance(head, dict) or head.get('format') != 1:
            raise InvalidProject('Project has an unsupported format')
        if head.get('session') != session:
            raise _OtherSession('Project belongs to another session')
        history = head.get('revisions')
        if (not isinstance(history, list) or not 0 < len(history) <= MAX_REVISIONS
                or any(not isinstance(item, str) or DIGEST.fullmatch(item) is None for item in history)
                or len(set(history)) != len(history)):
            raise InvalidProject('Invalid project revision history')
        return history

    @contextmanager
    def _locked(self, folder):
        fd = os.open(folder / '.write.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _write(path: Path, body: bytes, *, immutable=False):
        fd, temp = tempfile.mkstemp(prefix='.save-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            if immutable:
                try:
                    os.link(temp, path)
                except FileExistsError:
                    if ProjectStore._read(path, MAX_DOCUMENT + 4096) != body:
                        raise InvalidProject('An existing revision has different content')
            else:
                os.replace(temp, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temp).unlink(missing_ok=True)

    @staticmethod
    def _sync_directory(path: Path):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _ensure_root(self):
        missing, parent = [], self.root
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for path in reversed(missing):
            path.mkdir(mode=0o700, exist_ok=True)
            self._sync_directory(path.parent)

    def create(self, session: str, document: dict, *, identity: str | None = None, check=lambda:None) -> ProjectRevision:
        _identity(session, 'session')
        body = _document(document)
        self._ensure_root()
        project = identity if identity is not None else 'p' + uuid.uuid4().hex
        folder = self._folder(project)
        with self._locked(self.root):
            from .scientific_import_transaction import require_visible
            require_visible(self.root)
            check()
            folder.mkdir(mode=0o700, exist_ok=identity is not None)
            self._sync_directory(self.root)
            with self._locked(folder):
                if identity is not None and (folder / 'head.json').exists():
                    first = self.read(project, session, revision=1)
                    check()
                    if first._document != body:
                        raise Conflict('This scientific document identity already belongs to different content')
                    return first
                return self._publish(folder, session, body, [], check=check)

    def save(self, project: str, session: str, document: dict, *, expected_revision: int,
             transaction: str | None = None, check=lambda:None) -> ProjectRevision:
        _identity(session, 'session')
        if transaction is not None and (type(transaction) is not str or DIGEST.fullmatch(transaction) is None):
            raise InvalidProject('An edit transaction needs an exact server-issued identity')
        body = _document(document)
        folder = self._folder(project)
        from .scientific_files import validate_references as validate_files
        parsed=json.loads(body)
        self.read(project,session)
        validate_files(self,project,session,parsed,check=check)
        with self._locked(folder):
            check()
            history = self._head(folder, session)
            if type(expected_revision) is not int or not 1 <= expected_revision <= len(history):
                raise Conflict('This project changed. Reopen the accepted revision before saving.')
            if expected_revision != len(history):
                if transaction is not None:
                    previous = self.read(project, session, revision=expected_revision + 1)
                    if previous.transaction == transaction and previous._document == body:
                        return previous
                raise Conflict('This project changed. Reopen the accepted revision before saving.')
            if len(history) == MAX_REVISIONS:
                raise InvalidProject('Project revision limit reached; preserve this project and create a copy')
            # A damaged accepted record must not silently become the parent of a successful-looking save.
            self.read(project, session)
            return self._publish(folder, session, body, history, transaction=transaction,check=check,verified_files=parsed['resources'])

    def _publish(self, folder: Path, session: str, body: bytes, history: list[str], *, transaction=None, check=lambda:None, verified_files=(), native_new_bytes=None) -> ProjectRevision:
        check()
        from .scientific_arrays import accepted_parts, validate_references, MAX_NEW_BYTES
        previous = None
        if history:
            # The revision this one follows: its chunks are already verified and immutable.
            previous = self.read(folder.name, session, revision=len(history)).document
        validate_references(self, folder.name, session, json.loads(body),
                            accepted=accepted_parts(self, folder.name, session, previous), check=check,
                            new_bytes=MAX_NEW_BYTES if native_new_bytes is None else native_new_bytes)
        from .scientific_files import validate_references as validate_files
        # Save validates files before taking the publication lock. New/private
        # publications verify here under their owner's cooperative cancellation.
        validate_files(self,folder.name,session,json.loads(body),accepted=verified_files,check=check)
        records = folder / 'revisions'
        if records.is_symlink():
            raise InvalidProject('Project revisions cannot be a symlink')
        records.mkdir(mode=0o700, exist_ok=True)
        record = {'format': 1, 'id': folder.name, 'session': session, 'revision': len(history) + 1,
                  'parent': history[-1] if history else None, 'at': time.time(), 'document': json.loads(body)}
        if transaction is not None:
            record['transaction'] = transaction
        encoded = _json(record)
        digest = hashlib.sha256(encoded).hexdigest()
        self._write(records / f'{digest}.json', encoded, immutable=True)
        from .scientific_retention import publication, Retained
        try:
            with publication(self.root, self.runtime_root):
                check()
                self._write(folder / 'head.json', _json({'format': 1, 'session': session, 'revisions': [*history, digest]}))
                self.runtime_problem = None
        except Retained as error:
            raise InvalidProject(f'{error}. {error.next}') from error
        return ProjectRevision(folder.name, session, len(history) + 1, digest, record['parent'], record['at'], body, transaction)

    def read(self, project: str, session: str, *, revision: int | None = None) -> ProjectRevision:
        from .scientific_import_transaction import reading
        with reading(self.root):
            return self._read_revision(project, session, revision=revision)

    def _read_revision(self, project, session, *, revision):
        _identity(session, 'session')
        folder = self._folder(project)
        history = self._head(folder, session)
        revision = len(history) if revision is None else revision
        if type(revision) is not int or not 1 <= revision <= len(history):
            raise InvalidProject('That accepted project revision does not exist')
        if (folder / 'revisions').is_symlink():
            raise InvalidProject('Project revisions cannot be a symlink')
        digest = history[revision - 1]
        body = self._read(folder / 'revisions' / f'{digest}.json', MAX_DOCUMENT + 4096)
        if hashlib.sha256(body).hexdigest() != digest:
            raise InvalidProject('Project revision content changed')
        try:
            record = json.loads(body)
        except (ValueError, RecursionError) as exc:
            raise InvalidProject('Project revision is unreadable') from exc
        parent = history[revision - 2] if revision > 1 else None
        if (not isinstance(record, dict) or record.get('format') != 1 or record.get('id') != project
                or record.get('session') != session or record.get('revision') != revision or record.get('parent') != parent):
            raise InvalidProject('Project revision identity does not match its history')
        import math
        at = record.get('at')
        if type(at) not in (int, float) or not math.isfinite(at):
            raise InvalidProject('Invalid project timestamp')
        transaction = record.get('transaction')
        if transaction is not None and (type(transaction) is not str or DIGEST.fullmatch(transaction) is None):
            raise InvalidProject('Invalid edit transaction identity')
        return ProjectRevision(project, session, revision, digest, parent, at, _document(record.get('document')), transaction)

    def copy_candidate(self, saved, prepared):
        from .scientific_copies import ProjectCopy
        return ProjectCopy(self, saved, prepared)

    def file_draft(self, project, session, **declaration):
        """Owned selected-source writer; no decoder, loader or runtime is invoked."""
        from .scientific_files import FileDraft
        return FileDraft(self, project, session, **declaration)

    def file_source(self, project, session, revision, reference, *, check=lambda:None, verify_content=True):
        """Bounded byte reads from a file accepted in the exact Project revision."""
        from .scientific_files import source
        return source(self, project, session, revision, reference, check=check, verify_content=verify_content)

    def array_draft(self, project, session, **declaration):
        """Internal owned writer; its caller admits resources before transferring chunks."""
        from .scientific_arrays import ArrayDraft
        return ArrayDraft(self, project, session, **declaration)

    def array_source(self, project, session, revision, reference):
        """Exact accepted native component for the bounded regional read bridge."""
        from .scientific_arrays import source
        return source(self, project, session, revision, reference)

    def array_edit(self, project, session, revision, reference, *, max_written_bytes):
        from .scientific_arrays import edit
        return edit(self, project, session, revision, reference, max_written_bytes=max_written_bytes)

    def list(self, session: str) -> list[ProjectRevision]:
        return self.scan(session)[0]

    INPUT_KINDS = ('source-file', 'source-directory')

    def inputs_by_session(self) -> dict[str, int]:
        """How many scientific inputs (an imported file or folder, by its accepted head) each session holds, in one pass
        over the store: the projects list counts them beside the library's opened sources (F-L8-6). A staged import, a
        saved scene or a tool's output is not an input; an unreadable record is left out, as `scan` leaves it out."""
        from .scientific_import_transaction import reading
        with reading(self.root):
            return self._inputs_by_session()

    def _inputs_by_session(self):
        from .scientific_import_transaction import ImportPending
        out: dict[str, int] = {}
        if not self.root.is_dir():
            return out
        # A head's kind is read once: the same head.json bytes name the same immutable revision.
        known = self.__dict__.setdefault('_input_heads', {})
        for path in self.root.iterdir():
            if PROJECT_ID.fullmatch(path.name) is None or path.is_symlink():
                continue
            try:
                head = self._read(path / 'head.json', MAX_HEAD)
                if known.get(path.name, (None,))[0] != head:
                    session = json.loads(head).get('session')
                    kind = (self.read(path.name, session).document.get('kind')
                            if isinstance(session, str) else None)
                    known[path.name] = (head, session, kind)
                _, session, kind = known[path.name]
                if kind in self.INPUT_KINDS:
                    out[session] = out.get(session, 0) + 1
            except ImportPending:
                raise
            except (OSError, ValueError, RecursionError, AttributeError, InvalidProject):
                continue
        return out

    def head_references(self, session, *, max_documents, max_revisions, check=lambda: None):
        """Bounded accepted-head identities without loading document bodies.

        Discovery checks cancellation between directory entries and keeps only
        exact references. Other owners are excluded by the existing head reader.
        A later consumer still verifies each immutable revision's actual content.
        """
        from .scientific_import_transaction import reading
        _identity(session, 'session')
        check()
        result, revisions, entries = [], 0, 0
        with reading(self.root):
            if not self.root.is_dir():
                return result
            with os.scandir(self.root) as paths:
                for path in paths:
                    check()
                    entries += 1
                    if entries > 100_000:
                        raise InvalidProject('Project head inspection exceeds its directory allowance')
                    if not PROJECT_ID.fullmatch(path.name):
                        continue
                    try:
                        history = self._head(self._folder(path.name), session)
                    except _OtherSession:
                        continue
                    except (OSError, InvalidProject):
                        raise InvalidProject('Retained ownership is unreadable; recover records before dependency inspection') from None
                    revisions += len(history)
                    if len(result) >= max_documents or revisions > max_revisions:
                        raise InvalidProject('Project dependency inspection exceeds its history allowance')
                    result.append({'id': path.name, 'session': session,
                                   'revision': len(history), 'digest': history[-1]})
            check()
        return sorted(result, key=lambda item: item['id'])

    def scan_sessions(self, sessions):
        """Owner catalogue projection: one head scan, grouped by requested Project.

        This grants no access and is not an agent endpoint. Unattributable damaged
        heads mark every requested group incomplete rather than disappearing.
        """
        sessions = tuple(dict.fromkeys(sessions))
        for session in sessions:
            _identity(session, 'session')
        try:
            return self._scan_sessions(sessions)
        except (OSError, ValueError) as error:
            return {session: ([], [{'id': session, 'reason': str(error)}]) for session in sessions}

    def _scan_sessions(self, sessions):
        from .scientific_import_transaction import reading
        result = {session: ([], []) for session in sessions}
        with reading(self.root):
            if not self.root.is_dir():
                return result
            for path in self.root.iterdir():
                if PROJECT_ID.fullmatch(path.name) is None:
                    continue
                session = None
                try:
                    head = json.loads(self._read(path / 'head.json', MAX_HEAD))
                    session = head.get('session') if type(head) is dict else None
                    if not isinstance(session, str):
                        raise InvalidProject('Project head has no readable owner')
                    if session in result:
                        result[session][0].append(self.read(path.name, session))
                except (OSError, ValueError, RecursionError) as error:
                    problem = {'id': path.name, 'reason': str(error) if isinstance(error, InvalidProject) else 'Project is unreadable'}
                    for owner in ([session] if session in result else result):
                        result[owner][1].append(problem)
        return result

    def scan(self, session: str) -> tuple[list[ProjectRevision], list[dict]]:
        """Healthy projects and explicit unreadable records; another session is simply outside the query."""
        from .scientific_import_transaction import reading
        with reading(self.root):
            return self._scan(session)

    def _scan(self, session):
        _identity(session, 'session')
        if not self.root.is_dir():
            return [], []
        out, problems = [], []
        for path in self.root.iterdir():
            if PROJECT_ID.fullmatch(path.name) is None:
                continue
            try:
                out.append(self.read(path.name, session))
            except _OtherSession:
                continue
            except (OSError, InvalidProject) as exc:
                problems.append({'id': path.name, 'reason': str(exc) if isinstance(exc, InvalidProject) else 'Project is unreadable'})
        return sorted(out, key=lambda item: (item.at, item.id), reverse=True), problems
