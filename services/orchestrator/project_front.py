"""A Project's front page record: what it is, its page, and what is planned next.

One `project-front` document per Project, in the same revisioned ProjectStore as its Studies, so it travels with the
Project's export, backup and publication. It holds the owner's or an agent's one sentence about the Project (`about`),
the exact retained HTML file chosen as the Project's page (`page`), and planned next items (`next`). Every change is
attributed. It starts no work and makes no model call (docs/superpowers/specs/2026-11-05-project-front-page-design.md).
"""
from __future__ import annotations

import hashlib
import json
import time

from fastapi import Request

from .scientific_comparisons import exact, identity
from .scientific_projects import Conflict, InvalidProject, ProjectStore

KIND = 'project-front'
KEY = 'projectFront'
TITLE = 'Project front page'
ABOUT_LIMIT = 600
NEXT_LIMIT = 64
TEXT_LIMIT = 280
PAGE_SUFFIXES = ('.html', '.htm')
# What a browser shows as a picture: the main visual is one of these, or the Project's page.
IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.webp', '.gif')


def front_id(session: str) -> str:
    return 'p' + hashlib.sha256(json.dumps({'session': session, 'projectFront': 1}, sort_keys=True).encode()).hexdigest()[:32]


def _text(value, label, limit):
    if type(value) is not str or not 0 < len(value.strip()) <= limit:
        raise InvalidProject(f'{label} needs 1 to {limit} characters')
    return value.strip()


def is_page_file(saved) -> bool:
    """A retained single file whose name says it is HTML: the only kind Hooke shows as a Project's page."""
    if saved.document.get('kind') != 'source-file':
        return False
    source = saved.document.get('state', {}).get('sourceFile')
    name = source.get('name') if isinstance(source, dict) else None
    return isinstance(name, str) and name.lower().endswith(PAGE_SUFFIXES)


def page_heading(store, saved) -> str | None:
    """The page's own <title>, as plain text from its first bytes: what the page calls itself (its file name is the
    fallback). Shown as the Project's words, never interpreted."""
    import html
    import re
    try:
        state = saved.document['state']['sourceFile']
        source = store.file_source(saved.id, saved.session, saved.revision, state['reference'], verify_content=False)
        head = source.read(0, min(source.size, 64 << 10)).decode('utf-8', 'replace')
    except (ValueError, OSError, KeyError):
        return None
    found = re.search(r'<title[^>]*>(.*?)</title>', head, re.IGNORECASE | re.DOTALL)
    words = ' '.join(html.unescape(found.group(1)).split()) if found else ''
    return words[:160] or None


def file_name(saved):
    source = saved.document.get('state', {}).get('sourceFile')
    return source.get('name') if isinstance(source, dict) and isinstance(source.get('name'), str) else ''


def visual_kind(saved) -> str | None:
    """'image' or 'page' for a retained single file that can be a Project's main visual; None otherwise."""
    if saved.document.get('kind') != 'source-file':
        return None
    name = file_name(saved).lower()
    return 'image' if name.endswith(IMAGE_SUFFIXES) else 'page' if name.endswith(PAGE_SUFFIXES) else None


def empty() -> dict:
    return {'format': 1, 'about': None, 'page': None, 'visual': None, 'next': [], 'changes': []}


class ProjectFront:
    def __init__(self, store: ProjectStore, clock=time.time):
        self.store = store
        self.clock = clock

    def _saved(self, session):
        """The record's newest revision, or None while this Project has never written one."""
        # A Project folder's store is bound to that Project (project_stores.py); locate the record there.
        if not (self.store.for_session(session)._folder(front_id(session)) / 'head.json').exists():
            return None
        return self.store.read(front_id(session), session)

    def read(self, session: str) -> dict:
        """The record as the studio and agents see it; an untouched Project has an empty one."""
        saved = self._saved(session)
        if saved is None:
            return {**empty(), 'revision': 0}
        if saved.document.get('kind') != KIND:
            raise InvalidProject('This Project front page identity belongs to another document')
        state = saved.document['state'][KEY]
        page = state.get('page')
        out = {**empty(), **state, 'revision': saved.revision, 'updatedAt': saved.at}
        visual = state.get('visual')
        if visual:
            try:
                file = self.store.read(visual['id'], session, revision=visual['revision'])
                out['visual'] = {**visual, 'name': file_name(file), 'kind': visual_kind(file),
                                 'sha256': file.document['state']['sourceFile'].get('sha256')}
            except (ValueError, OSError, KeyError):
                out['visual'] = {**visual, 'unavailable': True}
        if page:
            try:
                file = self.store.read(page['id'], session, revision=page['revision'])
                out['page'] = {**page, 'title': file.document.get('title'),
                               'name': file.document['state'].get('sourceFile', {}).get('name'),
                               'heading': page_heading(self.store, file), 'savedAt': file.at}
            except (ValueError, OSError, KeyError):
                out['page'] = {**page, 'unavailable': True}
        return out

    def _change(self, session, attribution, change):
        identity_ = front_id(session)
        for _ in range(16):
            saved = self._saved(session)
            if saved is not None and saved.document.get('kind') != KIND:
                raise InvalidProject('This Project front page identity belongs to another document')
            state = json.loads(json.dumps(saved.document['state'][KEY])) if saved else empty()
            summary = change(state)
            if summary is None:
                return self.read(session)
            state['changes'] = [*state.get('changes', []), {**attribution, 'at': self.clock(), 'did': summary}][-32:]
            document = {'kind': KIND, 'title': TITLE, 'state': {KEY: state}, 'resources': [], 'runtimes': []}
            try:
                if saved is None:
                    self.store.create(session, document, identity=identity_)
                else:
                    self.store.save(identity_, session, document, expected_revision=saved.revision)
                return self.read(session)
            except Conflict:
                continue
        raise Conflict('The Project front page changed concurrently; retry')

    def set_about(self, session, about, *, attribution):
        value = _text(about, 'What this Project is', ABOUT_LIMIT) if about is not None else None
        def change(state):
            if state.get('about') == value:
                return None
            state['about'] = value
            return 'about'
        return self._change(session, attribution, change)

    def set_page(self, session, reference, *, attribution):
        """Choose an exact retained HTML file as the Project's page, or clear it with None."""
        chosen = None
        if reference is not None:
            if type(reference) is dict and set(reference) == {'id', 'revision'} and type(reference['revision']) is int:
                # The owner's choice in the studio names a listed revision; Hooke pins its exact digest here.
                saved = self.store.read(reference['id'], session, revision=reference['revision'])
            else:
                saved = exact(self.store, reference, session)
            if not is_page_file(saved):
                raise InvalidProject("Choose a retained .html file as the Project's page")
            chosen = identity(saved)
        def change(state):
            if state.get('page') == chosen:
                return None
            state['page'] = chosen
            return 'page'
        return self._change(session, attribution, change)

    def set_visual(self, session, reference, *, attribution):
        """Choose the Project's main visual (its cover wherever Projects are listed): an exact retained picture or
        .html page, or None to fall back to the page, then the newest picture."""
        chosen = None
        if reference is not None:
            if type(reference) is dict and set(reference) == {'id', 'revision'} and type(reference['revision']) is int:
                saved = self.store.read(reference['id'], session, revision=reference['revision'])
            else:
                saved = exact(self.store, reference, session)
            if visual_kind(saved) is None:
                raise InvalidProject('Choose a retained picture (.png, .jpg, .webp, .gif) or .html page as the main visual')
            chosen = identity(saved)
        def change(state):
            if state.get('visual') == chosen:
                return None
            state['visual'] = chosen
            return 'visual'
        return self._change(session, attribution, change)

    def add_next(self, session, *, request, text, study=None, when=None, attribution):
        _text(request, 'Request identity', 128)
        words = _text(text, 'A planned step', TEXT_LIMIT)
        if study is not None and (type(study) is not str or not study.startswith('p') or len(study) != 33):
            raise InvalidProject('Name the Study by its identity')
        if when is not None:
            when = _text(when, 'When', 80)
        key = 'n' + hashlib.sha256(json.dumps({'by': attribution['by'], 'request': request}).encode()).hexdigest()[:16]
        def change(state):
            rows = state.setdefault('next', [])
            prior = next((row for row in rows if row['id'] == key), None)
            if prior:
                if (prior['text'], prior.get('study'), prior.get('when')) != (words, study, when):
                    raise Conflict('This request already names another planned step')
                return None
            if sum(1 for row in rows if not row.get('done')) >= NEXT_LIMIT:
                raise InvalidProject('Finish or remove planned steps before adding more')
            row = {'id': key, 'text': words, **attribution, 'at': self.clock()}
            if study:
                row['study'] = study
            if when:
                row['when'] = when
            rows.append(row)
            state['next'] = rows[-256:]
            return 'next'
        return self._change(session, attribution, change)

    def done_next(self, session, item, *, attribution):
        def change(state):
            row = next((row for row in state.get('next', []) if row['id'] == item), None)
            if row is None:
                raise InvalidProject('Choose a planned step of this Project')
            if row.get('done'):
                return None
            row['done'] = {**attribution, 'at': self.clock()}
            return 'done'
        return self._change(session, attribution, change)


# The page shows in a sandboxed frame and loads nothing from anywhere: its pictures, video and fonts travel inside it
# (data: URLs), as a researcher's lesson does (research/files.py LESSON_CSP), so it can reach no network.
PAGE_CSP = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src data:; media-src data:; font-src data:")
PAGE_BYTES = 16 << 20
HEADERS = {'Cache-Control': 'no-store'}


def page_bytes(store, project, session, revision) -> bytes:
    """The whole retained page, each chunk checked against its hash; refuses anything that is not an HTML file."""
    from .scientific_files import CHUNK_BYTES
    saved = store.read(project, session, revision=revision)
    if not is_page_file(saved):
        raise InvalidProject('Only a retained .html file shows as a page')
    state = saved.document['state']['sourceFile']
    source = store.file_source(project, session, revision, state['reference'])
    if source.size > PAGE_BYTES:
        raise InvalidProject('This page is larger than 16 MB; keep large media as separate Project files')
    return b''.join(source.read(offset, min(CHUNK_BYTES, source.size - offset))
                    for offset in range(0, source.size, CHUNK_BYTES))


def register(app, built, studio_origin, header):
    from fastapi.responses import JSONResponse, Response
    from .scientific_project_api import Refused, _body, _error, _owned
    front = ProjectFront(built.hub.resources.projects)
    store = built.hub.resources.projects

    @app.get('/api/project-front')
    async def read_front(session: str):
        try:
            built.day.elsewhere(session)
            return JSONResponse({'ok': True, 'front': await _owned(front.read, session)}, headers=HEADERS)
        except (ValueError, OSError, KeyError) as error:
            return _error(error)

    @app.post('/api/project-front/{action}')
    async def change_front(action: str, request: Request):
        try:
            if (request.headers.get(header) != 'project-front'
                    or not studio_origin(request.headers.get('origin'), request.url.port)):
                raise Refused('Change the front page from its Project.', 403, next='return to the Project overview')
            body = await _body(request)
            session = body.get('session')
            if session != built.day.sid:
                raise Refused('The active Project changed.', 409, next='return to the original Project')
            owner = {'by': 'studio', 'byLabel': 'You'}
            if action == 'about' and set(body) == {'session', 'about'}:
                result = await _owned(front.set_about, session, body['about'], attribution=owner)
            elif action == 'page' and set(body) == {'session', 'page'}:
                result = await _owned(front.set_page, session, body['page'], attribution=owner)
            elif action == 'visual' and set(body) == {'session', 'visual'}:
                result = await _owned(front.set_visual, session, body['visual'], attribution=owner)
            elif action == 'next' and {'session', 'request', 'text'} <= set(body) <= {'session', 'request', 'text', 'study', 'when'}:
                result = await _owned(front.add_next, session, request=body['request'], text=body['text'],
                                      study=body.get('study'), when=body.get('when'), attribution=owner)
            elif action == 'done' and set(body) == {'session', 'item'}:
                result = await _owned(front.done_next, session, body['item'], attribution=owner)
            else:
                raise InvalidProject('Use a declared front page change and its fields')
            return JSONResponse({'ok': True, 'front': result}, headers=HEADERS)
        except (ValueError, OSError, KeyError) as error:
            return _error(error)

    @app.get('/api/project-library')
    async def project_library():
        """Every Project as the Projects library lists it, beside the sessions list (names, dates, counts): its one
        sentence, its main visual and page, its Study count and where it is saved. Read-only; one bad Project says
        so in its own row and never hides the others."""
        try:
            def read():
                return library_rows(front, built, [info.id for info in built.day.catalogue.list()])
            rows = await _owned(read)
            return JSONResponse({'ok': True, 'projects': rows}, headers=HEADERS)
        except (ValueError, OSError, KeyError) as error:
            return _error(error)

    @app.get('/api/scientific-files/{project}/page')
    async def serve_page(project: str, session: str, revision: int):
        try:
            body = await _owned(page_bytes, store, project, session, revision)
            return Response(body, media_type='text/html; charset=utf-8',
                            headers={**HEADERS, 'Content-Security-Policy': PAGE_CSP,
                                     'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'})
        except (ValueError, OSError, KeyError) as error:
            return _error(error)


_LIBRARY_CACHE: dict = {}


def _stamp(path):
    try:
        info = path.stat()
        return (info.st_mtime_ns, info.st_size)
    except OSError:
        return None


def library_rows(front, built, sids):
    """Each Project's row, rebuilt only when its store gains or loses a document or its front page record changes
    (a new Study is a new document; the record's head.json moves on every change). Counting Studies reads every
    document of a Project, so unchanged Projects reuse their last row."""
    rows = []
    locations = getattr(built.day, 'locations', None)
    def stamp(sid):
        try:
            store = front.store.for_session(sid)
            return (str(store.root), _stamp(store.root), _stamp(store._folder(front_id(sid)) / 'head.json'))
        except (ValueError, OSError, KeyError):
            return None
    for sid in sids[:500]:
        key = stamp(sid)
        cached = _LIBRARY_CACHE.get(sid)
        if key is not None and cached is not None and cached[0] == key:
            rows.append(cached[1])
            continue
        row = {'session': sid}
        try:
            record = front.read(sid)
            row.update(about=record.get('about'), page=record.get('page'), visual=record.get('visual'))
        except (ValueError, OSError, KeyError) as error:
            row['problem'] = str(error)[:200]
        try:
            row['studies'] = sum(1 for saved in front.store.list(sid) if saved.document.get('kind') == 'research-effort')
        except (ValueError, OSError, KeyError):
            row['studies'] = None
        try:
            row['savedIn'] = 'folder' if locations is not None and locations.project(sid) is not None else 'mac'
        except (ValueError, OSError, KeyError):
            row['savedIn'] = None
        # Stamped again after reading: a store's first listing may add its own index file to the folder.
        key = stamp(sid)
        if key is not None and 'problem' not in row:
            _LIBRARY_CACHE[sid] = (key, row)
        rows.append(row)
    return rows
