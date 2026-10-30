"""Exact retained media for the existing Stage and original-image analysis.

The bridge gets a verified disposable decoder file. Scientific analysis gets the
accepted reference, so a later Stage switch cannot replace its input or depend on
the decoder cache's lifetime. No global Library lookup or newest-revision guess.
"""
import asyncio
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
import threading

from .library import STILL, VIDEO, OPTICS
from .project_media import MediaReference, ProjectMedia
from .project_media_decoder import retained_path
from .project_roots import ProjectLocationError

# A stopped host cannot establish that the separately running bridge stopped
# reading its input. Keep these owners alive through process teardown.
_retired = []


def reference(value, session):
    from .door.schema import check
    if type(value) is not dict or set(value) != {'origin', 'session', 'document', 'revision', 'digest'}:
        raise ProjectLocationError('Choose an exact retained media revision')
    check({'id': value['document'], **{k: value[k] for k in ('session', 'revision', 'digest')}}, 'artifact-reference')
    if value['session'] != session or type(value['origin']) is not str:
        raise ProjectLocationError('This retained source belongs to another Project')
    return MediaReference(**value)


@dataclass
class StageAsset:
    """The existing asset interface backed by one immutable Project revision."""
    retained: MediaReference
    path: Path
    facts: dict

    @property
    def id(self): return self.facts['id']

    @property
    def kind(self): return self.facts['kind']

    @property
    def group(self): return self.facts['from']

    @property
    def origin(self): return self.facts.get('origin')

    @property
    def at(self): return self.facts.get('capturedAt')

    def metadata(self): return {**deepcopy(self.facts), 'retained': self.exact_reference()}
    def title_now(self): return self.facts['title']
    def specimen_now(self): return self.facts.get('specimen') or ''
    def user_specimen(self): return self.specimen_now() if self.facts.get('specimenBy') == 'user' else ''
    def scale_optics(self): return {k: v for k, v in self.facts.get('optics', {}).items() if k in OPTICS}

    def exact_reference(self): return asdict(self.retained)


class PreparedSource:
    def __init__(self, context, source):
        self.context = context
        self.asset = StageAsset(source.reference, source.path, source.metadata['asset'])
        self._closing = threading.Lock()

    def close(self):
        with self._closing:
            if self.context is not None:
                self.context.__exit__(None, None, None)
                self.context = None


class StageMedia:
    """Own decoder files until an observed feed replacement makes them disposable.

    An uncertain/cancelled bridge request may have opened its file: retain it until
    a later acknowledged replacement. Contexts survive host stop until process exit,
    like other work whose cancellation is not proof of external termination.
    """
    def __init__(self, stores, cache, *, max_bytes):
        self.media, self.cache, self.max_bytes = ProjectMedia(stores), cache, max_bytes
        self.held = []
        self.closed = threading.Event()

    def retire(self):
        self.closed.set()
        if self not in _retired:
            _retired.append(self)

    async def prepare(self, value, session, asset_id):
        selected = reference(value, session)
        stopping = threading.Event()
        def open_source():
            if self.closed.is_set():
                raise ProjectLocationError('The media host has stopped')
            metadata, _ = self.media.read(selected)
            facts = metadata['asset']
            if facts.get('id') != asset_id or facts.get('kind') not in ('still', 'recording'):
                raise ProjectLocationError('The retained source does not match the selected media')
            suffix = Path(str(facts.get('file') or '')).suffix.lower()
            if suffix not in (STILL if facts['kind'] == 'still' else VIDEO):
                raise ProjectLocationError('This retained media format cannot open on the Stage')
            context = retained_path(self.media, selected, host_cache=self.cache,
                max_bytes=self.max_bytes, stopping=lambda: stopping.is_set() or self.closed.is_set(), suffix=suffix)
            return PreparedSource(context, context.__enter__())
        task = asyncio.create_task(asyncio.to_thread(open_source))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            stopping.set()
            # Repeated caller cancellation must not cancel the thread's future.
            # Its completed decoder context stays owned even if this await is lost.
            def retain_if_open(finished):
                if not finished.cancelled() and finished.exception() is None:
                    self.offered(finished.result())
            task.add_done_callback(retain_if_open)
            # Wait for the real copy worker, never leave an unowned cache on a lost await.
            try:
                pending = await asyncio.shield(task)
            except (OSError, ValueError):
                pass
            else:
                self.offered(pending)
                await self._release(pending)
            raise

    def offered(self, source):
        if source is not None and source not in self.held:
            self.held.append(source)

    async def replaced(self, source):
        self.offered(source)
        old = [item for item in self.held if item is not source]
        for item in old:
            await self._release(item)

    async def _release(self, item):
        # Keep ownership until cleanup actually completes. Repeated cancellation
        # may leave a closed item here; a later acknowledged replacement retires it.
        await asyncio.shield(asyncio.create_task(asyncio.to_thread(item.close)))
        if item in self.held:
            self.held.remove(item)


def manager(app):
    found = getattr(app, '_project_stage_media', None)
    if found is None:
        # The bridge already admits the host results root. Hidden scratch files are
        # outside every accepted Project and cannot become retained result records.
        from .scientific_files import CHUNK_BYTES, MAX_CHUNKS
        found = app._project_stage_media = StageMedia(app.hub.resources.projects,
            app.paths.results / '.project-media-cache', max_bytes=CHUNK_BYTES * MAX_CHUNKS)
    return found


async def source_asset(app, identity):
    """Read source metadata in its Project namespace, never through an ID collision."""
    sid = app.day.sid
    locations = getattr(app.day, 'locations', None)
    portable = await asyncio.to_thread(locations.project, sid) if locations is not None else None
    if app.day.sid != sid:
        raise ProjectLocationError('That Project is no longer open')
    if portable is not None:
        value = getattr(app.day, 'source_references', {}).get(identity)
        if value is None:
            return None
        selected = reference(value, sid)
        metadata, _ = await asyncio.to_thread(ProjectMedia(app.hub.resources.projects).read, selected)
        if metadata['asset'].get('id') != identity:
            raise ProjectLocationError('The retained source identity conflicts')
        asset = StageAsset(selected, Path('.'), metadata['asset'])
    else:
        library = getattr(app.hub, 'library', None)
        asset = await asyncio.to_thread(library.get, identity) if library is not None else None
    if app.day.sid != sid:
        raise ProjectLocationError('That Project is no longer open')
    return asset


async def require_library_project(app):
    """Legacy mutation commands carry only host IDs, so cannot edit retained media."""
    sid = app.day.sid
    locations = getattr(app.day, 'locations', None)
    portable = await asyncio.to_thread(locations.project, sid) if locations is not None else None
    if app.day.sid != sid:
        raise ProjectLocationError('That Project is no longer open')
    if portable is not None:
        raise ProjectLocationError('Retained Project media requires an explicit revision; Library edits are unavailable here')


def sources(stores, session, record):
    """Project-scoped source rows, independent of another host's Library IDs."""
    from .sources import rows
    from urllib.parse import urlencode
    media, assets, previews = ProjectMedia(stores), {}, {}
    refs = getattr(record, 'source_references', {})
    if len(refs) > 10_000:
        raise ProjectLocationError('Project source listing exceeds its supported allowance')
    for identity, value in refs.items():
        selected = reference(value, session)
        metadata, source = media.read(selected)
        if metadata['asset'].get('id') != identity:
            raise ProjectLocationError('A retained source binding has a conflicting identity')
        assets[identity] = StageAsset(selected, Path('.'), metadata['asset'])
        previews[identity] = f'/api/scientific-files/{selected.document}/media?' + urlencode({
            'session': session, 'revision': selected.revision, 'sha256': source.sha256})
    result = rows(session, assets, record)
    for row in result['sources']:
        row.update(thumb=previews[row['id']], retained=dict(refs[row['id']]))
    result['unavailableSources'] = [{'id': identity, 'reason': 'No exact retained source is selected'}
        for identity in getattr(record, 'sources', {}) if identity != 'live' and identity not in assets]
    return result
