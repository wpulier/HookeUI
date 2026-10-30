"""Copy a selected Library source into its captured Project's immutable file store.

The retained document scopes the historical Library ID by Project origin. It does
not register that ID in another host's Library or follow a mutable original later.
This is selected-media retention, not collection of a whole Record dataset or its
Library edit history. Callers must run the bounded file work off the event loop.
"""
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import asyncio
import json
import os
import stat

from .library import Asset, is_asset_id
from .project_roots import ProjectLocationError
from .scientific_file_intake import FileIntake
from .scientific_files import CHUNK_BYTES
from .scientific_projects import _json

MAX_MEDIA_METADATA = 1 << 20
MAX_MEDIA_REVISIONS = 10_000


def _background():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise ProjectLocationError('Retain Project media on the background intake worker')


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@dataclass(frozen=True)
class MediaReference:
    origin: str
    session: str
    document: str
    revision: int
    digest: str


class _MediaIntake(FileIntake):
    def __init__(self, *args, metadata, check_source, source_guard, **kwargs):
        body = _json(metadata)
        if len(body) > MAX_MEDIA_METADATA:
            raise ProjectLocationError('Selected media metadata exceeds its retained history allowance')
        self.metadata = json.loads(body)
        self.check_source = check_source
        self.source_guard = source_guard
        super().__init__(*args, **kwargs)

    def document(self, ref):
        value = super().document(ref)
        value['state']['librarySource'] = self.metadata
        return value

    @contextmanager
    def acceptance(self):
        with self.source_guard():
            self.check_source()
            yield


class ProjectMedia:
    def __init__(self, stores):
        self.stores = stores

    def candidates(self, session, asset_id):
        """Resolve a historical Library ID only inside its owning Project.

        Several retained revisions may explain the same historical ID. Return
        exact references for selection; never choose the newest bytes or consult
        this host's unrelated global Library as a fallback.
        """
        _background()
        if not is_asset_id(asset_id):
            raise ProjectLocationError('Choose a retained Library source identity')
        root = self.stores.locations.project(session)
        if root is None:
            raise ProjectLocationError('Retained media lookup needs a portable Project')
        documents, problems = self.stores.scan(session)
        if problems:
            raise ProjectLocationError('Project media records are unreadable; restore them before choosing a source')
        if sum(head.revision for head in documents) > MAX_MEDIA_REVISIONS:
            raise ProjectLocationError('This Project has too many revisions for Library-ID lookup; choose an exact retained media reference')
        found = []
        for head in documents:
            # A later edit must not conceal a previously accepted source binding.
            for revision in range(1, head.revision + 1):
                saved = self.stores.read(head.id, session, revision=revision)
                metadata = saved.document['state'].get('librarySource', {})
                asset = metadata.get('asset') if isinstance(metadata, dict) else None
                if not isinstance(asset, dict) or asset.get('id') != asset_id:
                    continue
                ref = MediaReference(root.origin, session, saved.id, saved.revision, saved.digest)
                self.read(ref)  # require the accepted source and original origin
                found.append(ref)
        if self.stores.locations.project(session) != root:
            raise ProjectLocationError('The Project changed while resolving its retained media')
        return found

    def retain_library(self, session, library, identity, **options):
        """Copy selected bytes and all available Library metadata revisions together."""
        _background()
        from .project_media_history import capture
        selected = capture(library, identity)
        return self.retain(session, selected.asset, _capture=selected, **options)

    def retain(self, session, asset: Asset, *, request, max_bytes, producer, stopping=lambda: False, _capture=None):
        """Retain one admitted source and its known metadata; no parsing or execution.

        A lost reply retries the same request/content through FileIntake. A changed
        original requires a new explicit request; an accepted source never follows it.
        Record parts refuse here: collecting those needs whole-dataset inclusion review.
        """
        _background()
        root = self.stores.locations.project(session)
        if root is None:
            raise ProjectLocationError('Selected-media retention needs an explicitly opened portable Project')
        if not isinstance(asset, Asset) or not is_asset_id(asset.id) or asset.kind not in ('still', 'recording'):
            raise ProjectLocationError('Choose a known image or recording from the Library')
        if asset.group == 'record' or asset.key.startswith('dataset:'):
            raise ProjectLocationError('Collect this Record dataset with its shared captures; a single part is not a complete dataset')
        metadata = {'format': 1, 'projectOrigin': root.origin, 'asset': asset.metadata(),
                    'libraryKey': asset.key, 'scope': 'selected-media', 'history': 'metadata-at-retention'}
        if _capture is not None:
            metadata.update(history='retained-library-metadata', libraryHistory=_capture.history)
        descriptor = os.open(asset.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as source:
            original = os.fstat(source.fileno())
            if not stat.S_ISREG(original.st_mode):
                raise ProjectLocationError('The selected Library source must be a regular file')
            def check_source():
                if (self.stores.locations.project(session) != root
                        or _stamp(os.fstat(source.fileno())) != _stamp(original)
                        or _stamp(asset.path.lstat()) != _stamp(original)):
                    raise ProjectLocationError('The selected source or Project changed during retention; preserve it and retry')
            intake = _MediaIntake(self.stores, session, request, name=asset.path.name,
                size=original.st_size, max_bytes=max_bytes, producer=producer,
                metadata=metadata, check_source=check_source,
                source_guard=_capture.acceptance if _capture is not None else nullcontext)
            try:
                while True:
                    if stopping():
                        raise ProjectLocationError('Media retention stopped; the source was not added')
                    block = source.read(CHUNK_BYTES)
                    if not block:
                        break
                    intake.write(block)
                check_source()
                saved = intake.publish(stopping)
            finally:
                intake.close()
        return MediaReference(root.origin, session, saved.id, saved.revision, saved.digest)

    def read(self, reference: MediaReference):
        """Read the original exact metadata and verified chunks without a Library or decoder."""
        _background()
        root = self.stores.locations.project(reference.session)
        if root is None or root.origin != reference.origin:
            raise ProjectLocationError('This media reference belongs to another Project origin')
        saved = self.stores.read(reference.document, reference.session, revision=reference.revision)
        metadata = saved.document['state'].get('librarySource', {})
        if (saved.digest != reference.digest or saved.document['kind'] != 'source-file'
                or metadata.get('format') != 1 or metadata.get('projectOrigin') != root.origin
                or metadata.get('scope') != 'selected-media'):
            raise ProjectLocationError('The retained media identity does not match this reference')
        source = saved.document['state']['sourceFile']['reference']
        return metadata, self.stores.file_source(saved.id, saved.session, saved.revision, source,
                                                verify_content=False)
