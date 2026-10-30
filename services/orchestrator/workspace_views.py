"""Session-owned arrangements and free-standing notes, persisted in the session journal.

Arrangements contain references and display intent only. They never own scientific content.
"""
import copy
import json
import math
import re
import hashlib
import threading
import uuid
from functools import wraps
from weakref import WeakKeyDictionary

_LOCK = threading.RLock()
_TRANSACTIONS = threading.local()
_JOURNALS = WeakKeyDictionary()


def _journal_events(day):
    """Reuse unchanged disk snapshots inside the serialized workspace authority.

    An append by any store invalidates the token. A file changing during the read is
    never cached, including a partial append; the next call must inspect it again.
    """
    journal = day.journal
    def token():
        stat = journal.path.stat()
        return (str(journal.path), stat.st_dev, stat.st_ino, stat.st_size,
                stat.st_mtime_ns, stat.st_ctime_ns)
    try:
        before = token()
    except OSError:
        _JOURNALS.pop(journal, None)
        return journal.load()
    cached = _JOURNALS.get(journal)
    if cached is not None and cached[0] == before:
        return cached[1]
    events = journal.load()
    try:
        unchanged = token() == before
    except OSError:
        unchanged = False
    if unchanged:
        _JOURNALS[journal] = (before, events)
    else:
        _JOURNALS.pop(journal, None)
    return events


# Stage-coordinate layout formats, each passed through unchanged. Schema 5 adds output and
# compare Tabs and table, analysis and graph windows; its fields are allowed only when present.
# Schema 6 (ticket 66, S6) adds the reader Tab: one thing of Data, read full width, the specimen hidden.
SCHEMA = 7
SCHEMAS = (2, 3, 4, 5, 6, 7)
TAB_KINDS = ('output', 'compare', 'reader')
SCHEMA_OF_KIND = {'output': 5, 'compare': 5, 'reader': 6}  # the first schema each Tab kind is in
PRESENTATIONS = ('masks', 'table', 'capture')
SCHEMA5_PRESENTATIONS = ('datatable', 'analysis', 'graph')
LANE_LABELS = ('A', 'B', 'C')


class WorkspaceError(ValueError):
    def __init__(self, code, message, current=None):
        super().__init__(message)
        self.code, self.current = code, copy.deepcopy(current)


def serialized(fn):
    @wraps(fn)
    def call(day, *args, **kwargs):
        with _LOCK:
            active = getattr(_TRANSACTIONS, 'active', None)
            if active is None:
                active = _TRANSACTIONS.active = set()
            if id(day) in active:
                return fn(day, *args, **kwargs)
            active.add(id(day))
            # A Project can have a current and an elsewhere store: the journal is their authority.
            try:
                events = _journal_events(day)
                day.workspace_layout = None
                day.annotations = {}
                day.workspace_removed = {}
                day.workspace_completions = {}
                day.workspace_origins = {}
                for event in events:
                    if event.get("type", "").startswith("workspace-") or event.get("type") == "annotation":
                        day._apply(event, replay=True)
                return fn(day, *args, **kwargs)
            finally:
                active.remove(id(day))
    return call


def checked_layout(value, session):
    if not isinstance(value, dict) or type(value.get('schema')) is not int or value.get('schema') not in (1, *SCHEMAS):
        raise WorkspaceError('unsupported-schema', 'Reload this app to use this workspace layout.')
    v2 = value['schema'] in SCHEMAS
    if set(value) - ({'schema', 'views', 'coordinates'} if v2 else {'schema', 'active', 'views'}):
        raise ValueError('workspace layouts contain arrangements only')
    views = value.get('views')
    if not isinstance(views, list) or not (0 if v2 else 1) <= len(views) <= 24:
        raise ValueError('keep between 1 and 24 tabs')
    def name(v, limit=80):
        if not isinstance(v, str) or not v.strip() or len(v) > limit:
            raise ValueError('a tab needs a bounded name and identity')
    def numbers(v, n):
        if not isinstance(v, list) or len(v) != n or any(type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 100000 for x in v):
            raise ValueError('invalid window geometry')
    ids = set()
    placements = set()
    if v2 and value.get('coordinates') != 'stage':
        raise ValueError('workspace geometry must use stage coordinates')
    def placement(item):
        if v2:
            pid = item.get('placementId')
            name(pid, 128)
            if pid in placements: raise ValueError('duplicate placement identity')
            placements.add(pid)
    for view in views:
        fields = {'id', 'title', 'specimen', 'items', 'notes'}
        optional = ({'input'} if value['schema'] >= 4 else set()) | ({'kind', 'provenance', 'compare'} if value['schema'] >= 5 else set())
        if not isinstance(view, dict) or not fields <= set(view) or set(view) - (fields | optional):
            raise ValueError('invalid tab fields')
        name(view['id']); name(view['title'])
        if view['id'] in ids:
            raise ValueError('duplicate workspace tab')
        ids.add(view['id'])
        if view.get('kind') == 'reader':
            _checked_reader(view, value['schema'], session, placement)
            continue
        if 'input' in view:
            source = view['input']
            if (not isinstance(source, dict) or not {'source', 'position'} <= set(source) or
                    set(source) - ({'source', 'position', 'retained'} if value['schema'] >= 7 else {'source', 'position'}) or
                    not isinstance(source['source'], str) or
                    not re.fullmatch(r'live|a[0-9]+', source['source']) or
                    type(source['position']) not in (int, float) or
                    not math.isfinite(source['position']) or not 0 <= source['position'] <= 1e9 or
                    source['source'] == 'live' and source['position'] != 0):
                raise ValueError('invalid tab input reference')
            if 'retained' in source:
                from .project_stage_media import reference
                reference(source['retained'], session)
                if source['source'] == 'live':
                    raise ValueError('Live camera cannot name retained media')
        specimen = view['specimen']
        if not isinstance(specimen, dict) or set(specimen) != {'visible', 'rect'} or type(specimen['visible']) is not bool:
            raise ValueError('invalid specimen layout')
        if specimen['rect'] is not None:
            numbers(specimen['rect'], 4)
        if not isinstance(view['items'], list) or len(view['items']) > 64 or not isinstance(view['notes'], list) or len(view['notes']) > 64:
            raise ValueError('too many items in one tab')
        for item in view['items']:
            if not isinstance(item, dict) or set(item) - ({'resource', 'presentation', 'title', 'zoom', 'pan', 'position', 'size'} | ({'placementId', 'geometryUnavailable', 'display'} if v2 else set())):
                raise ValueError('an item must reference retained content')
            placement(item)
            if 'display' in item and (not isinstance(item['display'], dict) or set(item['display']) - ({'background', 'sort', 'direction', 'reading'} | ({'scientific'} if value['schema'] >= 3 else set()))):
                raise ValueError('invalid display preferences')
            display = item.get('display', {})
            if ('background' in display and type(display['background']) is not bool or
                'sort' in display and (not isinstance(display['sort'], str) or len(display['sort']) > 128) or
                'direction' in display and display['direction'] not in ('asc','desc') or
                'reading' in display and (type(display['reading']) not in (int,float) or not math.isfinite(display['reading']) or not 0 <= display['reading'] <= 10000000)):
                raise ValueError('invalid display preferences')
            if 'scientific' in display:
                from env_worker.scientific_view_preferences import preferences
                preferences(display['scientific'])
            from .presentations import ResourceRef
            resource = item.get('resource')
            if not isinstance(resource, dict) or set(resource) - {'day', 'id', 'version', 'picture', 'outputId'}:
                raise ValueError('a resource reference cannot contain scientific payloads')
            if 'outputId' in resource:
                name(resource['outputId'], 256)
            ref = ResourceRef.from_wire(resource)
            if 'scientific' in display and (not re.fullmatch(r'p[0-9a-f]{32}', ref.id) or ref.version is None or ref.version < 1
                                            or ref.picture is not None or item.get('presentation') is not None):
                raise ValueError('remembered scientific views require an exact scientific document revision')
            if ref.day != session:
                raise ValueError('an item must belong to this project')
            if item.get('presentation') not in (None, *PRESENTATIONS, *(SCHEMA5_PRESENTATIONS if value['schema'] >= 5 else ())):
                raise ValueError('unknown representation')
            if item.get('presentation') in SCHEMA5_PRESENTATIONS:
                prefix = {'datatable': 'tb', 'analysis': 'an', 'graph': 'gr'}[item['presentation']]
                if (not re.fullmatch(prefix + r'[1-9][0-9]*', ref.id)
                        or type(ref.version) is not int or ref.version < 1
                        or ref.picture is not None or 'outputId' in resource):
                    raise ValueError('table, analysis and graph windows need their exact saved kind and revision')
            name(item.get('title'), 512)
            numbers(item.get('pan'), 2)
            if type(item.get('zoom')) not in (int, float) or not 0 < item['zoom'] <= 1000:
                raise ValueError('invalid zoom')
            for field in ('position', 'size'):
                if item.get(field) is not None:
                    numbers(item[field], 2)
        for note in view['notes']:
            if not isinstance(note, dict) or set(note) != ({'id', 'rect', 'placementId'} if v2 else {'id', 'rect'}) or not re.fullmatch(r'a[0-9a-f]{12,32}', str(note['id'])):
                raise ValueError('a note placement needs a retained note identity')
            placement(note)
            numbers(note['rect'], 4)
        _checked_kind(view)
    if (not v2 and value.get('active') not in ids) or len(json.dumps(value, allow_nan=False).encode()) > 262144:
        raise ValueError('invalid active tab or oversized arrangement')
    return copy.deepcopy(value)


def _checked_reader(view, schema, session, placement):
    """A reader Tab (schema 6): one thing of Data, read full width with the specimen hidden. Its one item names the
    entry it reads ({resource: {day, id, version}, title}, a placement identity optional); no windows, notes, input or
    lanes. Only a reference: what it shows is read from the index each time."""
    if schema < SCHEMA_OF_KIND['reader']:
        raise ValueError('unknown tab kind: a reader Tab needs layout schema 6')
    if set(view) - {'id', 'title', 'specimen', 'items', 'notes', 'kind'}:
        raise ValueError('a reader Tab holds one item only: no input, provenance or lanes')
    if view['specimen'] != {'visible': False, 'rect': None}:
        raise ValueError('a reader Tab hides the specimen')
    if not isinstance(view['items'], list) or len(view['items']) != 1 or view['notes'] != []:
        raise ValueError('a reader Tab reads exactly one item and has no notes')
    item = view['items'][0]
    if not isinstance(item, dict) or not {'resource', 'title'} <= set(item) or set(item) - {'resource', 'title', 'placementId'}:
        raise ValueError('a reader item is its resource and title')
    if 'placementId' in item:
        placement(item)
    resource = item['resource']
    if not isinstance(resource, dict) or not {'day', 'id', 'version'} <= set(resource) or set(resource) - {'day', 'id', 'version', 'picture', 'outputId'}:
        raise ValueError('a reader item references one entry: its day, id and version')
    from .presentations import ResourceRef
    if 'outputId' in resource and (not isinstance(resource['outputId'], str) or not 0 < len(resource['outputId']) <= 256):
        raise ValueError('a reader item references one entry: its day, id and version')
    ref = ResourceRef.from_wire({**resource, 'picture': resource.get('picture')})
    if ref.day != session:
        raise ValueError('an item must belong to this project')
    if not isinstance(item['title'], str) or not item['title'].strip() or len(item['title']) > 512:
        raise ValueError('a reader item needs a bounded title')


def _checked_kind(view):
    """Schema 5's per-Tab fields: what kind of Tab it is, where it came from, and its A/B/C lanes."""
    def text(v):
        return isinstance(v, str) and v.strip() and len(v) <= 128
    if 'kind' in view and view['kind'] not in TAB_KINDS:
        raise ValueError('unknown tab kind: use output, compare or reader')
    if 'provenance' in view:
        source = view['provenance']
        if (not isinstance(source, dict) or not source or set(source) - {'researcher', 'version', 'run', 'from'} or
                any(not text(source[k]) for k in ('researcher', 'run', 'from') if k in source) or
                'version' in source and (type(source['version']) is not int or not 1 <= source['version'] <= 1000000000)):
            raise ValueError('invalid tab provenance: name its researcher, version, run or source Tab')
    if 'compare' not in view:
        return
    if view.get('kind') != 'compare':
        raise ValueError('compare lanes belong only to a compare Tab')
    compare = view['compare']
    if not isinstance(compare, dict) or set(compare) != {'sync', 'lanes'} or type(compare['sync']) is not bool:
        raise ValueError('invalid compare settings: sync and lanes only')
    lanes = compare['lanes']
    if not isinstance(lanes, list) or not 2 <= len(lanes) <= 3:
        raise ValueError('a compare Tab needs 2 or 3 lanes')
    windows = {item.get('placementId') for item in view['items']}
    for lane in lanes:
        if not isinstance(lane, dict) or set(lane) != {'placementId', 'label', 'impl'}:
            raise ValueError('invalid lane fields: placementId, label and impl only')
        if lane['label'] not in LANE_LABELS:
            raise ValueError('each lane label is A, B or C, used once')
        if not text(lane['impl']):
            raise ValueError('invalid lane implementation name')
        if not isinstance(lane['placementId'], str) or lane['placementId'] not in windows:
            raise ValueError("a lane must show one of this Tab's windows")
    if len({lane['label'] for lane in lanes}) != len(lanes):
        raise ValueError('each lane label is A, B or C, used once')
    if len({lane['placementId'] for lane in lanes}) != len(lanes):
        raise ValueError('each lane shows a different window')


def upgrade(day, layout, revision):
    if layout is None:
        return {'schema': 2, 'coordinates': 'stage', 'views': [{'id': 'main', 'title': 'Main', 'specimen': {'visible': True, 'rect': None}, 'items': [], 'notes': []}]}
    if layout['schema'] in SCHEMAS: return copy.deepcopy(layout)
    if layout.get('schema') != 1:
        raise WorkspaceError('unsupported-schema', 'Reload this app: the Project uses an unsupported layout format.')
    result = {'schema': 2, 'coordinates': 'stage', 'views': copy.deepcopy(layout['views'])}
    for view in result['views']:
        for kind in ('items', 'notes'):
            for index, item in enumerate(view[kind]):
                identity = json.dumps([day.sid, revision, view['id'], kind, index], separators=(',', ':'))
                item['placementId'] = 'p' + hashlib.sha256(identity.encode()).hexdigest()[:32]
                if kind == 'items' and item.get('position') is not None:
                    item.pop('position', None)
                    item['geometryUnavailable'] = True
    return result


@serialized
def read(day, schema=1):
    if type(schema) is not int or schema not in (1, *SCHEMAS):
        raise WorkspaceError('unsupported-schema', 'Reload this app: the requested layout format is unsupported.')
    record = day.workspace_layout
    revision = record['revision'] if record else 0
    layout = copy.deepcopy(record['layout']) if record else None
    if layout is not None and layout.get('schema') not in (1, *SCHEMAS):
        raise WorkspaceError('unsupported-schema', 'Reload this app: the Project uses an unsupported layout format.')
    if layout is not None and layout['schema'] > schema:
        raise WorkspaceError('unsupported-schema', 'Reload this app: the Project uses a newer layout format.')
    if schema in SCHEMAS:
        if layout is not None and layout['schema'] == 1:
            layout = upgrade(day, layout, revision)
            revision += 1
            day._record({'type':'workspace-layout','revision':revision,'layout':layout,'migration':{'from':1}},strict=True)
        completed = {row['origin']['run'] for row in day.workspace_completions.values()}
        pending = [origin for origin in day.workspace_origins.values() if origin['run'] not in completed]
        hidden = _dismissed_runs(day) if pending else set()
        return {'revision': revision, 'layout': upgrade(day, layout, revision),
                'removed': list(copy.deepcopy(day.workspace_removed).values()),
                'completions': list(copy.deepcopy(day.workspace_completions).values()),
                'pending': [origin for origin in pending if origin['run'] not in hidden]}
    if layout and layout['schema'] == 2:
        raise WorkspaceError('unsupported-schema', 'Reload this app: the Project uses a newer layout format.')
    return {'revision': revision, 'layout': layout}


def _completion_additions(day, base, current):
    """Prove only server additions since a known base; older/opaque history conflicts.

    Reuse the serialized authority's cached journal snapshot. Inspect at most 64
    recent events, never search an unbounded history to rescue an old draft.
    """
    if type(base) is not int or not 0 <= base < current['revision']:
        return None
    events = _journal_events(day)[-64:]
    baseline = next((e for e in events if e.get('revision') == base and
                     e.get('type') in ('workspace-layout', 'workspace-arrangement', 'workspace-completion')), None)
    if baseline is None:
        return None
    previous, revision, additions = baseline['layout'], base, []
    for event in events[events.index(baseline) + 1:]:
        if event.get('type') not in ('workspace-layout', 'workspace-arrangement', 'workspace-completion'):
            continue
        row, layout = event.get('completion', {}), event.get('layout', {})
        if (event.get('type') == 'workspace-completion' and event.get('revision') == revision and
                row.get('state') != 'placed' and layout == previous):
            continue  # kept, not placed (complete(place=False)): it changed no arrangement and took no revision
        if event.get('type') != 'workspace-completion' or event.get('revision') != revision + 1:
            return None
        origin = row.get('origin', {})
        item = row.get('item', {})
        if (row.get('state') != 'placed' or day.workspace_origins.get(origin.get('run')) != origin or
                origin.get('session') != day.sid or previous.get('schema') not in SCHEMAS or
                {k: v for k, v in previous.items() if k != 'views'} !=
                {k: v for k, v in layout.items() if k != 'views'}):
            return None
        key = hashlib.sha256(json.dumps([row.get('execution'), item.get('resource'), origin.get('tab')], sort_keys=True).encode()).hexdigest()
        if row.get('id') != key or item.get('placementId') != origin.get('placementId', '') + '-' + key[:12]:
            return None
        before, after = previous.get('views', []), layout.get('views', [])
        output = row.get('outputTab')
        target = origin['tab']
        appended = None
        if output is not None:
            if not _valid_output_tab(day, output, item['resource']['id']):
                return None
            target = output['id']
            if not any(v['id'] == target for v in before):
                appended = {**output, 'items': [item]}
                if after != before + [appended]:
                    return None
                additions.append({'tab': target, 'item': item, 'view': output})
                previous, revision = layout, event['revision']
                continue
        if len(before) != len(after):
            return None
        found = False
        for old, new in zip(before, after):
            if old.get('id') != target:
                if old != new:
                    return None
            else:
                found = True
                if ({k: v for k, v in old.items() if k != 'items'} !=
                        {k: v for k, v in new.items() if k != 'items'} or
                        new.get('items') != old.get('items', []) + [item]):
                    return None
        if not found:
            return None
        additions.append({'tab': target, 'item': item})
        previous, revision = layout, event['revision']
    if revision != current['revision'] or previous != current['layout']:
        return None
    return additions


def _merge_completions(day, clean, additions):
    if additions is None:
        return None
    merged = copy.deepcopy(clean)
    for addition in additions:
        view = next((v for v in merged['views'] if v['id'] == addition['tab']), None)
        if view is None:
            if 'view' not in addition:
                return None
            view = copy.deepcopy(addition['view'])
            merged['views'].append(view)
        elif 'view' in addition and view != addition['view'] and view != {**addition['view'], 'items': [addition['item']]}:
            return None
        existing = [(v['id'], item) for v in merged['views'] for item in v['items'] + v['notes']
                    if item.get('placementId') == addition['item']['placementId']]
        if existing:
            if existing != [(addition['tab'], addition['item'])]:
                return None
        else:
            view['items'].append(copy.deepcopy(addition['item']))
    try:
        return checked_layout(merged, day.sid)
    except ValueError:
        return None


@serialized
def save(day, layout, base):
    clean = checked_layout(layout, day.sid)
    current = read(day, schema=clean['schema'])
    if clean == current['layout'] and day.workspace_layout is not None and day.workspace_layout['layout']['schema'] == clean['schema']:
        return current
    request_digest = hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()
    record = day.workspace_layout or {}
    receipt = record.get('completionSave', {})
    if (type(base) is int and receipt.get('base') == base and
            receipt.get('requestDigest') == request_digest):
        return {**current, 'completionAdditions': copy.deepcopy(receipt['additions'])}
    additions = None
    if type(base) is not int or base != current['revision']:
        additions = _completion_additions(day, base, current)
        merged = _merge_completions(day, clean, additions) if clean['schema'] == current['layout']['schema'] else None
        if merged is None:
            raise WorkspaceError('conflict', 'This project layout changed before this save. Review your local arrangement before saving.', current)
        clean = merged
    if any(v['id'] in day.workspace_removed for v in clean['views']):
        raise WorkspaceError('validation', 'Restore a removed arrangement using Restore, or recover it with a new identity.')
    migration = {'migration': {'from': current['layout']['schema']}} if current['layout'] and clean['schema'] > current['layout']['schema'] else {}
    completion_receipt = {'completionSave': {'base': base, 'requestDigest': request_digest,
                          'additions': additions}} if additions else {}
    event = day._record({'type': 'workspace-layout', 'revision': current['revision'] + 1,
                        'layout': clean, **migration, **completion_receipt}, strict=True)
    return {**current, 'revision': event['revision'], 'layout': clean,
            **({'completionAdditions': copy.deepcopy(additions)} if additions else {})}


@serialized
def arrangement(day, action, tab, base):
    current = read(day, schema=SCHEMA)
    if base != current['revision']:
        raise WorkspaceError('conflict', 'The arrangement changed. Review the latest version.', current)
    layout = current['layout']
    if action == 'remove':
        removed = next((v for v in layout['views'] if v['id'] == tab), None)
        if not removed: raise ValueError('arrangement is unavailable')
        layout['views'] = [v for v in layout['views'] if v['id'] != tab]
    elif action == 'restore':
        removed = day.workspace_removed.get(tab)
        if not removed: raise ValueError('removed arrangement is unavailable')
        if len(layout['views']) >= 24: raise WorkspaceError('capacity', 'Remove an arrangement to make room. Its Data stays.')
        layout['views'].append(copy.deepcopy(removed))
    else: raise ValueError('unknown arrangement action')
    checked_layout(layout, day.sid)
    day._record({'type': 'workspace-arrangement', 'action': action, 'tab': tab, 'removed': removed,
                 'revision': base + 1, 'layout': layout}, strict=True)
    return read(day, schema=SCHEMA)


def checked_input(value, session):
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError('choose one exact workspace input')
    if 'resource' in value:
        from .semantic_resources import parse_reference
        raw = value['resource']
        native = isinstance(raw, dict) and raw.get('kind') == 'array'
        reference = parse_reference({k: v for k, v in raw.items() if k != 'kind'} if native else raw)
        if native:
            if type(reference['revision']) is not int or reference['revision'] < 1 or not reference.get('component', '').startswith('array.'):
                raise ValueError('choose an exact accepted native input revision and component')
            reference = {'kind': 'array', **reference}
        if reference['session'] != session:
            raise ValueError('the input belongs to another Project')
        return {'resource': reference}
    if 'source' in value and (value['source'] is None or
            isinstance(value['source'], str) and 0 < len(value['source']) <= 256):
        return dict(value)
    if 'volume' in value and isinstance(value['volume'], str) and 0 < len(value['volume']) <= 128:
        return dict(value)
    raise ValueError('the workspace input is invalid')


@serialized
def admit(day, origin):
    if not isinstance(origin, dict) or set(origin) - {'session','tab','placementId','run','input','destination'}:
        raise ValueError('invalid workspace origin')
    if 'destination' in origin and origin['destination'] != 'new':
        raise ValueError('invalid workspace origin')  # a New Tab run's: the Tab it opened is for its windows
    if origin.get('session') != day.sid: raise ValueError('the Project changed before admission')
    if 'input' in origin:
        checked_input(origin['input'], day.sid)
    for field in ('tab','placementId','run'):
        if not isinstance(origin.get(field), str) or not 1 <= len(origin[field]) <= 128: raise ValueError('invalid workspace origin')
    if not any(v['id'] == origin['tab'] for v in read(day, schema=SCHEMA)['layout']['views']):
        raise ValueError('the destination tab is unavailable')
    previous = day.workspace_origins.get(origin['run'])
    if previous:
        if previous != origin: raise ValueError('a run cannot change its destination')
        return
    day._record({'type': 'workspace-origin', 'origin': copy.deepcopy(origin)}, strict=True)


# Output Tabs are no longer made (a finished researcher's work is kept in Data and in Researchers, and a person
# opens it in a Tab). A layout that holds one made before stays readable, and its completion still proves itself.
def _valid_output_tab(day, output, researcher):
    if not isinstance(output, dict):
        return False
    origin = day.workspace_origins.get(output.get('provenance', {}).get('run'))
    if origin is None or origin.get('session') != day.sid:
        return False
    expected = _output_tab(origin, researcher, output.get('title'))
    return (all(output.get(key) == expected[key] for key in ('id', 'kind', 'provenance', 'items', 'notes'))
            and isinstance(output.get('title'), str) and 0 < len(output['title']) <= 80)


def _output_tab(origin, researcher, title):
    return {'id': 'o' + hashlib.sha256(researcher.encode()).hexdigest(),
            'title': str(title or 'Research results')[:80], 'kind': 'output',
            'provenance': {'researcher': researcher, 'run': origin['run'], 'from': origin['tab']},
            'specimen': {'visible': False, 'rect': None}, 'items': [], 'notes': []}


# Why a kept completion is in no Tab: shown beside its title where completed work is listed.
KEPT_REASON = 'Kept in Data and in Researchers.'


@serialized
def complete(day, origin, execution, item, *, place=True, reason=KEPT_REASON):
    """Place a completed result in its origin Tab, or with `place=False` only record that it is kept.

    A kept completion (a new workspace researcher's work, or a run drawn on the specimen) is placed in no Tab: it
    stays in Data (and in Researchers) until a person opens it in one; `reason` says why where completed work is
    listed. It changes no arrangement, so it takes no new revision.
    """
    if day.workspace_origins.get(origin['run']) != origin: raise ValueError('completion has no admitted origin')
    key = hashlib.sha256(json.dumps([execution, item['resource'], origin['tab']], sort_keys=True).encode()).hexdigest()
    previous = day.workspace_completions.get(key)
    if previous and previous['state'] == 'placed': return copy.deepcopy(previous)
    current = read(day, schema=SCHEMA); layout = current['layout']
    view = next((v for v in layout['views'] if v['id'] == origin['tab']), None)
    placement = {**copy.deepcopy(item), 'placementId': origin['placementId'] + '-' + key[:12]}
    row = {'id': key, 'origin': origin, 'execution': execution, 'item': placement, 'state': 'available'}
    if not place:
        row['reason'] = reason
        # An earlier output Tab's refusal (its notice promised a placement) is replaced once; a kept row stands.
        if previous and 'outputTab' not in previous and 'notice' not in previous: return copy.deepcopy(previous)
        day._record({'type': 'workspace-completion', 'completion': row, 'layout': layout,
                     'revision': current['revision']}, strict=True)
        return copy.deepcopy(row)
    if view is not None and len(view['items']) < 64 and not dismissed(day,origin):
        view['items'].append(placement)
        try: checked_layout(layout, day.sid)
        except ValueError:
            view['items'].pop()
            row['reason'] = 'The saved result does not fit this workspace layout. Open it from Researchers.'
        else: row['state'] = 'placed'
    if previous and row['state'] == 'available': return copy.deepcopy(previous)
    day._record({'type': 'workspace-completion', 'completion': row, 'layout': layout,
                 'revision': current['revision'] + 1}, strict=True)
    return copy.deepcopy(row)


@serialized
def save_note(day, message):
    aid = message.get('id')
    if not isinstance(aid, str) or not re.fullmatch(r'a[0-9a-f]{32}', aid):
        raise ValueError('invalid project note identity')
    previous = (day.annotations.get(aid) or [None])[-1]
    source = {'day': day.sid, 'scope': 'session'}
    if previous and previous['source'] != source:
        raise ValueError('this is a source-bound annotation, not a project note')
    text, title = message.get('text'), message.get('title')
    if not isinstance(text, str) or not isinstance(title, str):
        raise ValueError('note text and title are required')
    if previous and previous['text'] == text and previous['title'] == title:
        return copy.deepcopy(previous)
    if type(message.get('base')) is not int or message.get('base') != (previous['version'] if previous else 0):
        raise WorkspaceError('conflict', 'This note changed elsewhere. Compare the saved note with your draft.', previous)
    return day.record_annotation(source, text, title, annotation_id=aid if previous else '',
                                 creation_id=aid if not previous else '', asked=day.here())


@serialized
def link_execution(day, origin, execution):
    """The request-to-execution association survives the lifetime of its Ticket."""
    events = _journal_events(day)
    if not any(e.get('type') == 'workspace-execution' and e.get('execution') == execution
               and e.get('run') == origin['run'] for e in events):
        day._record({'type': 'workspace-execution', 'execution': execution, 'run': origin['run']}, strict=True)


# This process: a run left "checking" by an earlier one was never decided (its layer is gone), so its masks are placed.
_BOOT = uuid.uuid4().hex
SPECIMEN_STATES = ('checking', 'drawn', 'placed')
# Why a run drawn on the specimen is in no Tab: shown beside its title where completed work is listed.
SPECIMEN_REASON = 'Drawn on the specimen; kept in Data.'


@serialized
def specimen(day, origin, execution, population, state, why=''):
    """A This Tab run's window and the specimen (S5a; population.placed_on_stage, Layer.await_specimen), decided once:
    "checking" when its population, found on the still the Stage shows, is shown there (journaled before its save is
    filed, so reconcile never places its masks first); then "drawn" when the layer first verifies its outlines on that
    still (the specimen is its window: Will's call, 2026-10-25; it is kept in Data, listed with SPECIMEN_REASON, where
    Add to Tab… and Open in this Tab place it), or "placed" when that check does not come, or the layer is hidden or
    replaced first (its masks window, as for any other run). A decision is never changed, and nothing is decided for a
    run that was not checking. Reconciled at once. Returns the run's state, or None."""
    if state not in SPECIMEN_STATES:
        raise ValueError('unknown specimen state')
    last = None
    for e in _journal_events(day):
        if e.get('type') == 'workspace-specimen' and e.get('execution') == execution and e.get('run') == origin['run']:
            last = e
    if last is not None and (last.get('state') != 'checking' or state == 'checking'):
        return last.get('state')
    if last is None and state != 'checking':
        return None
    day._record({'type': 'workspace-specimen', 'execution': execution, 'run': origin['run'], 'population': population,
                 'state': state, 'boot': _BOOT, **({'why': str(why)[:200]} if why else {})}, strict=True)
    if state != 'checking':
        try:
            reconcile(day)
        except (OSError, ValueError):
            pass  # decided and journaled: the next reconcile (the studio's next read) places or keeps it
    return state


@serialized
def reconcile(day):
    """Replay presentation from retained output records, never repeat computation.

    Saving output and displaying it are independent. If this step fails, the output's
    journal record and execution association remain the inputs to the next retry.

    One window per execution (S5b): an execution that saved masks places its masks, and its answer (the `result`
    record) stays in Data and on the run's footer, unless that answer keeps a table, which is a window of its own.
    A This Tab run whose masks wait on the specimen (S5a, `specimen`): nothing is placed while it is checking (in this
    process); drawn there, its masks are kept in Data with no window; otherwise placed as any other run's.
    """
    from pathlib import Path
    from .resources import keeps_table
    events = _journal_events(day)
    links, on_specimen = {}, {}
    for e in events:
        if e.get('type') == 'workspace-execution':
            links.setdefault(e['execution'], set()).add(e['run'])
        elif e.get('type') == 'workspace-specimen':
            on_specimen[(e.get('execution'), e.get('run'))] = e
    if not links: return
    outputs = []
    for e in events:
        kind = e.get('type')
        if kind not in ('evidence-saved', 'result', 'tracks-saved'): continue
        # A result identity can exist before its original and journal are saved.
        # Only the completion receipt may create its automatic presentation.
        # Older result records had no state and already meant retained output.
        if kind == 'result' and e.get('state', 'done') not in ('done', 'saved'): continue
        info = e
        if kind in ('evidence-saved', 'tracks-saved'):
            try:
                from .project_evidence import resolve_entry
                folder = resolve_entry(day, day.sid, e)
                info = json.loads((folder / 'manifest.json').read_text())
            except (OSError, ValueError, KeyError): continue
        executions = info.get('executions') or [info.get('execution') or (info.get('madeBy') or {}).get('execution')]
        outputs.append((e, kind, info, executions))
    # Masks saved, or masks waiting on the specimen (S5a: the mark is filed before their save, and the run's answer can be
    # filed in between; the 0.4.0 candidate placed that answer as a window while its outlines were drawn on the still).
    masked = {execution for _, kind, _, executions in outputs if kind == 'evidence-saved' for execution in executions} \
        | {execution for execution, _run in on_specimen}
    for e, kind, info, executions in outputs:
        for execution in executions:
            if kind == 'result' and execution in masked and not keeps_table(info.get('values')):
                continue  # its masks are this execution's window
            for run in links.get(execution, ()):
                mark = on_specimen.get((execution, run)) if kind == 'evidence-saved' else None
                state = (mark or {}).get('state')
                if state == 'checking' and mark.get('boot') == _BOOT:
                    continue  # its outlines are being checked on the specimen: kept or placed once decided
                origin = day.workspace_origins.get(run)
                if origin is None: continue
                item = {'resource': {'day': day.sid, 'id': e['id'],
                                    'version': e.get('revision', info.get('version')),
                                    'picture': 0 if kind == 'evidence-saved' or info.get('pictures') else None},
                        'title': info.get('title') or e['id'], 'zoom': 1, 'pan': [.5, .5]}
                if kind == 'evidence-saved': item['presentation'] = 'masks'
                if kind == 'tracks-saved' or kind == 'result' and keeps_table(info.get('values')):
                    item['presentation'] = 'table'
                if state == 'drawn':  # drawn on the specimen, its window (Will's call): kept in Data, listed as such
                    complete(day, origin, execution, item, place=False, reason=SPECIMEN_REASON)
                else:
                    complete(day, origin, execution, item)


@serialized
def dismiss(day, run):
    if run not in day.workspace_origins: raise ValueError('pending presentation is unavailable')
    day._record({'type':'workspace-dismiss','run':run},strict=True)
    return read(day,schema=SCHEMA)


def dismissed(day, origin):
    return origin['run'] in _dismissed_runs(day)


def _dismissed_runs(day):
    """Index chronological dismissal intent once, irrespective of pending run count."""
    admitted = set()
    by_tab = {}
    hidden = set()
    for event in _journal_events(day):
        kind = event.get('type')
        if kind == 'workspace-origin':
            origin = event['origin']
            admitted.add(origin['run'])
            by_tab.setdefault(origin['tab'], set()).add(origin['run'])
        elif kind == 'workspace-dismiss' and event.get('run') in admitted:
            hidden.add(event['run'])
        elif kind == 'workspace-arrangement' and event.get('action') == 'remove':
            hidden.update(by_tab.get(event['tab'], ()))
    return hidden


def reconcile_research(day, sessions):
    """Completed researchers are durable inputs too; reconnect never reruns them.

    New workspace research (`deliver == 'tab'`) is kept, never placed in a Tab by itself: its work is in Data
    and in Researchers, and a person opens it in a Tab. An older researcher keeps its origin-Tab placement.
    """
    for session in sessions:
        origin = (session.origin or {}).get('workspace')
        if not origin or origin.get('session') != day.sid or not session.result: continue
        version = (session.result.get('findingRevision') or {}).get('version')
        complete(day, origin, f"research:{session.rid}:{version}",
                 {'resource': {'day': day.sid, 'id': session.rid, 'version': version, 'picture': None},
                  'title': session.label or session.brief[:120], 'zoom': 1, 'pan': [.5, .5]},
                 place=getattr(session, 'deliver', None) != 'tab')


def reconcile_jobs(day, records):
    for record in records:
        origin = record.get('workspaceOrigin')
        if (not isinstance(origin, dict) or origin.get('session') != day.sid or
                day.workspace_origins.get(origin.get('run')) != origin or
                record.get('state') not in ('done', 'failed', 'cancelled', 'interrupted')):
            continue
        item = {'resource': {'day': day.sid, 'id': record['id'], 'version': None, 'picture': None},
                'title': record.get('name') or record['id'], 'zoom': 1, 'pan': [.5, .5]}
        if record['state'] == 'done' and record.get('kindOf', {}).get('output') == 'table.objects':
            item['presentation'] = 'table'
        complete(day, origin, f"job:{record['id']}", item)
