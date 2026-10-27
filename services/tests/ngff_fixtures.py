"""Tiny scientific folders captured through the production immutable intake."""
import uuid
from orchestrator.scientific_directory_intake import DirectoryIntake
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_files import CHUNK_BYTES


def selected(root, projects):
    store = ProjectStore(projects)
    paths = sorted(p for p in root.rglob('*') if p.is_file())
    entries, offset = [], 0
    for path in paths:
        size = path.stat().st_size
        entries.append({'path': path.relative_to(root).as_posix(), 'offset': offset, 'size': size})
        offset += size
    intake = DirectoryIntake(store, 's1', uuid.uuid4().hex, name=root.name, size=offset, max_bytes=offset,
                             entries={'format': 'source-directory-v1', 'entries': entries})
    try:
        for path in paths:
            with path.open('rb') as stream:
                while body := stream.read(CHUNK_BYTES): intake.write(body)
        saved = intake.publish()
    finally:
        intake.close()
    return store, saved
