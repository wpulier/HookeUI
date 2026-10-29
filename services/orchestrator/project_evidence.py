"""Immutable microscopy saves in a routed Project, with legacy paths unchanged.

The journal pins each accepted save's relative location and manifest digest. Reading
never adopts another origin or guesses a relocated absolute path. This does not
collect Library sources or cross-Project Record datasets.
"""
from pathlib import Path
import re
import stat
import uuid

from .day import sid as sids
from .project_roots import ProjectLocationError
from .scientific_projects import ProjectStore


def _project(artifacts, session):
    locations = getattr(artifacts.day, 'locations', None)
    return locations.project(session) if locations is not None else None


def _directories(root, path, *, create=False):
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        raise ProjectLocationError('Evidence is outside its owning Project') from None
    current = root
    for part in parts:
        current = current / part
        if create:
            current.mkdir(exist_ok=True)
        if not stat.S_ISDIR(current.stat(follow_symlinks=False).st_mode):
            raise ProjectLocationError('Evidence storage was replaced; restore its original folder')
    return path


def destination(artifacts, population):
    session = population.session or artifacts._session()
    root = artifacts.root_for(session, write=True)
    project = _project(artifacts, session)
    date = population.day or artifacts._date()
    if not sids.is_sid(date) or sids.parse(date)[1] != 1 or not re.fullmatch(r'e\d+', population.id):
        raise ProjectLocationError('Evidence needs its original date and identity')
    folder = root / date / population.id
    if project is None:
        folder.mkdir(parents=True, exist_ok=True)
        return folder, False
    if not population.session:
        raise ProjectLocationError('Portable evidence needs its captured Project owner')
    _directories(root, folder, create=True)
    folder = folder / uuid.uuid4().hex
    folder.mkdir()  # every accepted save stays immutable, including same-revision links
    if population.path is not None:
        _directories(root, population.path)
    return folder, True


def sync_publication(artifacts, population, folder):
    root = artifacts.root_for(population.session, write=True)
    _directories(root, folder)
    current = folder
    while current != root:
        ProjectStore._sync_directory(current)
        current = current.parent
    ProjectStore._sync_directory(root)


def reference(artifacts, population, folder):
    session = population.session or artifacts._session()
    project = _project(artifacts, session)
    if project is None:
        return str(folder), {}
    artifacts.root_for(session, write=True)  # refuse a closed/replaced host before journal acceptance
    _directories(project.path, folder)
    pin = artifacts._retained_manifests.get(folder)
    from .project_folder_copy import _stamp
    if pin is None or _stamp((folder / 'manifest.json').lstat()) != pin[1]:
        raise ProjectLocationError('Evidence publication has no unchanged retained manifest identity')
    digest = pin[0]
    return f'project:{project.origin}/{folder.relative_to(project.path).as_posix()}', {'manifestSha256': digest}


def resolve(artifacts, session, entry):
    artifacts.root_for(session)  # also enforce this service's captured folder identity
    return resolve_entry(artifacts.day, session, entry)


def resolve_entry(day, session, entry):
    """Shared journal reader, including workspace recovery without a live Evidence artifacts."""
    path = entry['path']
    locations = getattr(day, 'locations', None)
    project = locations.project(session) if locations is not None else None
    if not isinstance(path, str):
        raise ProjectLocationError('Evidence has an invalid retained location')
    if not path.startswith('project:'):
        if project is not None:
            raise ProjectLocationError('This evidence still needs explicit dependency collection')
        return Path(path)
    if project is None:
        raise ProjectLocationError('The evidence Project location is unavailable')
    match = re.fullmatch(r'project:([a-f0-9]{32})/artifacts/evidence/(\d{4}-\d{2}-\d{2})/(e\d+)/([a-f0-9]{32})', path)
    if match is None or match[1] != project.origin:
        raise ProjectLocationError('Evidence belongs to a different or invalid Project origin')
    root = _directories(project.path, project.folder('artifacts') / 'evidence')
    folder = _directories(root, root / match[2] / match[3] / match[4])
    manifest = folder / 'manifest.json'
    if not stat.S_ISREG(manifest.stat(follow_symlinks=False).st_mode):
        raise ProjectLocationError('Evidence manifest was replaced')
    expected = entry.get('manifestSha256')
    if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
        raise ProjectLocationError('Evidence is missing its accepted manifest identity')
    # Stream the original manifest: no unbounded extra copy on large populations.
    actual = _digest(manifest)
    if actual != expected:
        raise ProjectLocationError('Evidence no longer matches its accepted revision')
    return folder


def _digest(path):
    from .project_folder_copy import _stream
    return _stream(path)[1]


def remember(artifacts, folder):
    from .project_folder_copy import _stamp
    path = folder / 'manifest.json'
    before = _stamp(path.lstat())
    digest = _digest(path)
    if _stamp(path.lstat()) != before:
        raise ProjectLocationError('Evidence manifest changed during publication')
    artifacts._retained_manifests[folder] = (digest, before)


def verify_previous(artifacts, population):
    """Do not bless externally changed prior evidence by emitting fresh checksums."""
    import json
    folder = population.path
    if folder is None:
        return
    from .project_folder_copy import _stream
    import io
    pin = artifacts._retained_manifests.get(folder)
    if pin is None:
        raise ProjectLocationError('Previous evidence has no retained manifest identity')
    with io.BytesIO() as output:
        _, digest = _stream(folder / 'manifest.json', pin[1], output)
        if digest != pin[0]:
            raise ProjectLocationError('Previous evidence no longer matches its retained manifest')
        record = json.loads(output.getvalue())
    if record.get('id') != population.id or record.get('session') != population.session:
        raise ProjectLocationError('Previous evidence has a different owner')
    for name, digest in record.get('files', {}).items():
        if name == 'vectors.npz' and name in artifacts._recovered_optional.get(folder, set()):
            continue  # explicitly dropped at reopen, never read or copied into the next save
        if name not in {'capture.jpg', 'source.image', 'masks.npz', 'vectors.npz'} or _digest(folder / name) != digest:
            raise ProjectLocationError('Previous evidence bytes changed; restore the accepted save before continuing')
