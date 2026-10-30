"""Owned exact-view PNGs; native values and renderer qualification stay separate.

Only server-issued, displayed frames enter this module. A retained frame is the
renderer output, never a browser screenshot. Re-export uses a newly opened,
accepted renderer and the original saved inputs/view; unsupported replay refuses.
"""
import asyncio
import copy
from itertools import islice
import hashlib
import json
import os
from pathlib import Path

import numpy as np
from env_worker.scientific_view_preferences import from_frame
from gpu_worker import manifests as catalog
from . import view_export
from .scientific_file_intake import FileIntake, REQUEST
from .scientific_projects import InvalidProject

WORKING_BYTES = 32 << 20
MAX_PNG = 8 << 20
# Everything else (including future operations) conservatively requires reopening
# accepted work. Navigation/style changes are not unsaved scientific data.
PRESENTATION = frozenset(('camera', 'camera-reset', 'camera-gesture', 'grid', 'axis-order', 'dimensions',
    'layer-presentation', 'slice-margins', 'image-projection', 'text-presentation', 'labels-tool',
    'point-presentation', 'surface-presentation', 'path-presentation', 'image-presentation',
    'image-histogram', 'labels-presentation', 'image-colormap', 'image-sampling', 'clipping-planes',
    'image-plane', 'layer-selection', 'layer-order', 'restore-view'))
READ_ONLY = frozenset(('render', 'pick', 'inspect', 'python-assist', 'history-save', 'save-status',
    'layer-inventory', 'family-drafts', 'native-panel-render', 'observe'))


def changes_data(request):
    if request['op'] == 'change':
        return request.get('request', {}).get('operation') not in PRESENTATION
    return request['op'] not in READ_ONLY


def reference(saved):
    return {'id': saved.id, 'session': saved.session, 'revision': saved.revision, 'digest': saved.digest}


def source_signature(saved):
    spec = copy.deepcopy(saved.document['state'].get('scientificView', {}))
    runtime = spec.pop('runtime', None)
    if not runtime or not spec.get('inputs') or spec.get('initializeTool'):
        raise InvalidProject('Save the scientific inputs and reopen their accepted document before exporting')
    return {'view': spec, 'resources': saved.document['resources']}, runtime


def renderer(runtime, environ):
    from . import bundle_review, env_runner, own_trials
    from .maintenance import workflow
    owner = catalog.get(runtime['bundle'])
    installed = env_runner.generation(owner, runtime['generation'], environ) if owner is not None else None
    if installed is None:
        raise InvalidProject('This renderer needs its current checks. Review and enable it in Models, then reopen the saved view')
    digest = bundle_review.identity(installed)
    try: accepted = json.loads(own_trials.marker(owner.key, environ).read_text()).get('adoptedDigest') == digest
    except (OSError, ValueError): accepted = False
    if not accepted and not workflow.accepted(owner, runtime['generation'], digest, environ):
        raise InvalidProject('Review and enable this exact renderer generation in Models before exporting')
    return {'entry': owner.key, 'generation': installed.folder.name, 'version': installed.m.version}


def folder(store, saved):
    store = store.for_session(saved.session, write=False)
    root = store._folder(saved.id) / 'view-exports'
    if root.is_symlink(): raise InvalidProject('The export folder must remain inside its saved document')
    return root


def record(store, saved, identity):
    if type(identity) is not str or not REQUEST.fullmatch(identity):
        raise InvalidProject('Choose an existing export from this saved document')
    path = folder(store, saved) / identity / 'receipt.json'
    if path.parent.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size > 2 << 20:
        raise InvalidProject('This export receipt is unavailable; keep the original PNG and choose its saved view again')
    value = json.loads(path.read_text())
    if value.get('source', {}).get('id') != saved.id or value['source'].get('session') != saved.session:
        raise InvalidProject('The export belongs to another saved document')
    return value


def listing(store, saved, environ=os.environ):
    from .maintenance import revocations
    rows = []
    root = folder(store, saved)
    # Bounded discovery is explicit in the UI. No renderer/preparation runs here.
    if not root.exists(): return {'items': [], 'limited': False}
    entries = list(islice(root.iterdir(), 101))
    entries.sort(key=lambda path: path.name)
    for path in entries[:100]:
        try:
            kept = record(store, saved, path.name)
            owner = catalog.get(kept['renderer']['entry'])
            fixes = revocations.fixes(owner, environ) if owner is not None else []
            affected = [row for row in fixes if kept['renderer']['version'] in row['versions']]
            rows.append({'request': path.name, 'scope': kept['scopeWords'], 'scopeKind': kept['scope'], 'region': kept['region'], 'at': kept['at'],
                'ok': kept['ok'], 'problems': kept['problems'], 'project': kept.get('project'),
                'preferences': kept['preferences'], 'renderer': kept['renderer'],
                'affected': [{'issue': row.get('issue'), 'fixedIn': row.get('fixedIn')} for row in affected]})
        except (OSError, ValueError, KeyError):
            rows.append({'request': path.name, 'ok': False, 'problems': ['Export receipt unavailable; original evidence is preserved.']})
    return {'items': rows, 'limited': len(entries) > 100}


async def capture(manager, client, identity, frame, draw, snapshot):
    owner = manager._owner(client, identity)
    if owner.busy or owner.export_data_changed:
        raise InvalidProject('Save scientific changes, open the accepted revision and refresh its preview before exporting')
    await manager._check(owner, snapshot=snapshot, draw=draw, interaction=True)
    published = owner.frame
    if published is None or published[0]['frame'] != frame:
        raise InvalidProject('The scientific view changed. Refresh preview and choose the export again')
    saved = await asyncio.to_thread(manager.store.read, owner.execution.document,
        owner.execution.project, revision=owner.execution.revision)
    await manager._check(owner, snapshot=snapshot, draw=draw, interaction=True)
    if owner.busy or owner.export_data_changed or owner.frame is not published or saved.digest != owner.execution.digest:
        raise InvalidProject('The scientific view changed while capturing the export; choose it again')
    source_signature(saved)
    return saved, copy.deepcopy(published[0]), published[1]


class ViewIntake(FileIntake):
    def __init__(self, *args, provenance, runtime, **kwargs):
        self.provenance, self.runtime = provenance, runtime
        super().__init__(*args, **kwargs)

    def document(self, ref):
        doc = super().document(ref)
        doc['state']['viewExport'] = self.provenance
        doc['runtimes'] = [self.runtime]
        return doc


def publish(store, saved, frame, pixels, request, scope, region=None, *, previous=None, stopping=lambda: False, environ=os.environ):
    store = store.for_session(saved.session, write=True)
    if type(request) is not str or not REQUEST.fullmatch(request):
        raise InvalidProject('Use the original bounded export request identity')
    signature, runtime = source_signature(saved)
    rendered = renderer(runtime, environ)
    preferences = from_frame(frame)
    frame = {**frame, 'resource': reference(saved)}
    if scope == 'selected-region' and (type(region) is not dict or set(region) != {'x', 'y', 'width', 'height'}
            or any(type(value) is not int for value in region.values())):
        raise InvalidProject('Choose explicit integer pixel bounds for the export region')
    if scope != 'selected-region' and region is not None:
        raise InvalidProject('Current view export does not accept an implicit region')
    dispatch = view_export.capture(frame, scope=scope, region=region)
    dispatch['size'] = [region['width'], region['height']] if region else [frame['width'], frame['height']]
    if previous is not None:
        old = record(store, saved, previous)
        if (old['sourceSignature'] != signature or old['preferences'] != preferences
                or old['viewport'] != [frame['width'], frame['height']] or old['scope'] != scope or old['region'] != region
                or old['renderer']['entry'] != rendered['entry']):
            raise InvalidProject('Re-export pending: reopen the same saved inputs and restore the captured view and preview size; no old PNG was copied')
        if old['renderer']['generation'] == rendered['generation']:
            raise InvalidProject('Re-export pending: apply and explicitly open the reviewed fixed renderer first; the original remains unchanged')
        from .maintenance import revocations
        fixes = revocations.fixes(catalog.get(rendered['entry']), environ)
        if not any(old['renderer']['version'] in fix['versions'] and rendered['version'] == fix['fixedIn'] for fix in fixes):
            raise InvalidProject('Re-export pending: this renderer has no matching declared fixed build for the original export')
    if stopping(): raise InvalidProject('Export cancelled before publication')
    out = folder(store, saved) / request / 'view.png'
    if out.parent.is_symlink(): raise InvalidProject('The export destination must remain in its saved document')
    if out.parent.exists():
        old = record(store, saved, request)
        if (old['source'] != reference(saved) or old['preferences'] != preferences or old['scope'] != scope
                or old['region'] != region or old.get('reexportOf') != previous
                or old['frame'] != frame['frame'] or old['viewport'] != [frame['width'], frame['height']]
                or old['size'] != dispatch['size'] or old['renderer'] != rendered):
            raise InvalidProject('This export request already belongs to a different captured view; choose a new export')
        if old.get('project'):
            return store.read(old['project']['id'], saved.session, revision=1)
        raise InvalidProject('This export did not finish acceptance. Its evidence is preserved; choose a new export')
    out.parent.mkdir(parents=True, exist_ok=False)  # One request owns its evidence; never overwrite another attempt.
    rgb = np.frombuffer(pixels, dtype=np.uint8).reshape(frame['height'], frame['width'], 4)[..., :3].copy()
    # The server-held native renderer frame is immutable. Exporting it does not
    # rerun algorithms or switch to a later preview while disk work is queued.
    kept = view_export.export(dispatch, lambda captured: view_export._crop(rgb, captured['region']).copy(), rgb, out, **rendered, environ=environ)
    kept.update(source=reference(saved), sourceSignature=signature, preferences=preferences,
                viewport=[frame['width'], frame['height']], reexportOf=previous,
                meaning='Retained native rendered display pixels; not native scientific values or scientific accuracy')
    receipt = out.parent / 'receipt.json'
    receipt.write_text(json.dumps(kept, allow_nan=False) + '\n')
    if not kept['ok']:
        raise InvalidProject('Export pixel verification failed; failed evidence is retained and no result was accepted')
    body = out.read_bytes()
    key = hashlib.sha256(('view-export\0' + saved.id + '\0' + request).encode()).hexdigest()
    intake = ViewIntake(store, saved.session, key, name='Scientific view.png', size=len(body), max_bytes=MAX_PNG,
                        provenance=kept, runtime=runtime)
    try:
        intake.write(body)
        accepted = intake.publish(stopping)
        kept['project'] = {'id': accepted.id, 'session': accepted.session, 'revision': accepted.revision}
        receipt.write_text(json.dumps(kept, allow_nan=False) + '\n')
        return accepted
    finally: intake.close()
