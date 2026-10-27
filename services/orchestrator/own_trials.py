"""Private, explicit own-picture comparisons. A setup proof is not adoption.

The retained input, output, installed entry digest and current-default identity form one comparison. Use accepts
exactly that installed generation; a new generation needs another comparison. Pictures never leave this store
except when the user explicitly runs a configured comparison model. No cloud comparator is invoked implicitly.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from gpu_worker import manifests as catalog

JOBS = {'find': ('Find or count things', 'labels.instance.2d'), 'name': ('Describe or name things', 'text'),
        'measure': ('Measure things', 'table.objects'), 'clear': ('Clear up a picture', 'image.2d.rgb8')}
MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 4 * 1024 * 1024


def root(environ=os.environ):
    return catalog.catalog_root(environ) / 'own-trials'


def folder(tid, environ=os.environ):
    if not re.fullmatch(r'[a-f0-9]{32}', tid or ''):
        raise ValueError('choose your comparison picture first')
    return root(environ) / tid


def state(tid, environ=os.environ):
    try:
        return json.loads((folder(tid, environ) / 'trial.json').read_text())
    except (OSError, ValueError):
        raise ValueError('choose your comparison picture again') from None


def save(tid, value, environ=os.environ):
    p = folder(tid, environ) / 'trial.json'
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(value))
    tmp.replace(p)


def picture(tid, environ=os.environ):
    data = (folder(tid, environ) / 'input').read_bytes()
    if hashlib.sha256(data).hexdigest() != state(tid, environ)['sha256']:
        raise ValueError('the chosen picture changed; choose it again')
    return data


def add_input(data, job, hub, environ=os.environ):
    from .capture import captures
    from .source_images import _shape, _read
    if job not in JOBS:
        raise ValueError('choose a picture job: find, name, measure or clear; following needs a recording workflow')
    if not data or len(data) > MAX_BYTES:
        raise ValueError('choose a PNG or JPEG picture of at most 8 MiB')
    try:
        width, height, _, media = _shape(data)
        if min(width, height) < 1 or width * height > MAX_PIXELS:
            raise ValueError('choose a picture of at most four million pixels')
    except ValueError as exc:
        raise ValueError(f'Cannot use that picture: {exc}') from None
    tid = uuid.uuid4().hex
    d = folder(tid, environ)
    d.mkdir(parents=True)
    (d / 'input').write_bytes(data)
    try:
        _read(captures(hub), {'path': d / 'input', 'asset': tid, 'session': ''})  # validates actual decode under admission
    except Exception:
        import shutil
        shutil.rmtree(d)
        raise
    value = {'id': tid, 'job': job, 'sha256': hashlib.sha256(data).hexdigest(), 'media': media,
             'width': width, 'height': height, 'at': time.time()}
    save(tid, value, environ)
    return value


def marker(key, environ=os.environ):
    return root(environ) / 'models' / (hashlib.sha256(key.encode()).hexdigest() + '.json')


def prepare(key, tid, environ=os.environ):
    s = state(tid, environ)
    p = marker(key, environ)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:  # an entry added again keeps what was accepted for it: Use it covers that exact generation still
        adopted = {k: v for k, v in json.loads(p.read_text()).items() if k == 'adoptedDigest'}
    except (OSError, ValueError):
        adopted = {}
    p.write_text(json.dumps({'model': key, 'trial': tid, **adopted}))
    save(tid, {**s, 'model': key}, environ)


def await_comparison(key, environ=os.environ, *, review_kind=None):
    """A model added from a link with no picture yet: it is compared before it is a choice all the same (C3), so it waits
    for one. Turning it on installs and checks it; Set up for comparison with a picture (prepare) and Use it add the
    choice. What was accepted for it before (adoptedDigest) still covers that exact generation."""
    p = marker(key, environ)
    try:
        record = json.loads(p.read_text())
    except (OSError, ValueError):
        record = {}
    if record.get('trial'):
        return  # a comparison is already set up for it
    p.parent.mkdir(parents=True, exist_ok=True)
    adopted = {k: v for k, v in record.items() if k == 'adoptedDigest'}
    p.write_text(json.dumps({'model': key, 'trial': None, **adopted, **({'reviewKind': review_kind} if review_kind else {})}))


def awaiting_picture(key, environ=os.environ) -> bool:
    """It waits for a comparison and no picture has been chosen for it yet."""
    try:
        return not json.loads(marker(key, environ).read_text()).get('trial')
    except (OSError, ValueError):
        return False


def installed_entry(m, environ=os.environ):
    from . import env_runner, fiji_runner

    if m is None:
        return None
    if m.runner == 'python-env':
        return env_runner.in_use(m, environ)
    if m.runner == 'fiji':
        return fiji_runner.in_use(m, environ)
    return None


def comparison_digest(m, environ=os.environ, *, installed=None):
    from .envs import entry_digest

    if m.members:
        from . import bundle_review
        found = installed if installed is not None else installed_entry(m, environ)
        return bundle_review.identity(found) if found is not None else None
    if m.runner == 'fiji':
        found = installed if installed is not None else installed_entry(m, environ)
        if found is None:
            return None
        identity = {'entry': found.proof.get('content') or entry_digest(found.m, environ), 'generation': found.n,
                    'boundary': found.proof.get('boundary'), 'runtime': found.proof.get('runtime')}
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return entry_digest(m, environ)


def pending(m, environ=os.environ):
    p = marker(m.key, environ)
    if not p.exists():
        return False  # existing CLI/import workflows retain their explicit activation contract
    try:
        digest = comparison_digest(m, environ)
        from .bundle_admission import local_workflow
        if local_workflow(m):
            from .maintenance import workflow
            selected = installed_entry(m, environ)
            if selected is not None and workflow.accepted(m, selected.folder.name, digest, environ):
                return False
        return digest is None or json.loads(p.read_text()).get('adoptedDigest') != digest
    except (OSError, ValueError):
        return True


def output(d: Path, name, out, data, ms):
    """Keep actual bounded output, plus a display preview; an overlay is never the scientific original."""
    import cv2
    import numpy as np
    preview = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    original = out.get('picture')
    if isinstance(original, np.ndarray):
        original = cv2.imencode('.png', cv2.cvtColor(original, cv2.COLOR_RGB2BGR))[1].tobytes()
    if isinstance(original, bytes):
        (d / f'{name}.png').write_bytes(original)
    else:
        for poly in out.get('polygons') or []:
            cv2.polylines(preview, [np.asarray(poly, np.int32)], True, (40, 240, 80), 1)
        (d / f'{name}.png').write_bytes(cv2.imencode('.png', preview)[1].tobytes())
    body = {k: v for k, v in out.items() if k in ('count', 'polygons', 'labels', 'columns', 'rows', 'text', 'points', 'producer', 'version', 'sha256')}
    labels = body.get('labels')
    if isinstance(labels, dict) and isinstance(labels.get('zlib'), bytes):
        import base64
        # Preserve the exact compressed native labels in the downloadable JSON, alongside dtype and shape.
        body['labels'] = {k: v for k, v in labels.items() if k != 'zlib'}
        body['labels']['zlibBase64'] = base64.b64encode(labels['zlib']).decode('ascii')
    (d / f'{name}.json').write_text(json.dumps(body))
    count = int(out.get('count') or len(out.get('polygons') or [])) if 'count' in out or 'polygons' in out else None
    return {'count': count, 'ms': round(ms, 1), 'text': str(out.get('text') or '')[:4000],
            'rows': len(out['rows']) if 'rows' in out else None, 'preview': f'/api/own-trials/{d.name}/{name}.png'}


async def compare(app, tid, *, model='', draft='', environ=os.environ):
    from . import drafts, env_runner, mine, operations, owned_process
    from .capture import captures
    from .source_images import _read
    # Every file read, hash and lock below runs off the event loop (AGENTS.md: nothing slow on the loop).
    s = await asyncio.to_thread(state, tid, environ)
    data = await asyncio.to_thread(picture, tid, environ)
    d = folder(tid, environ)
    store = captures(app.hub)
    capture = await asyncio.to_thread(_read, store, {'path': d / 'input', 'asset': tid, 'session': ''})
    s.pop('comparison', None)

    def forget_earlier():  # a failed retry must not leave an earlier comparison looking current
        save(tid, s, environ)
        if draft:
            with drafts._LOCK:
                ds = drafts._state(draft, environ)
                ds.pop('comparison', None)
                drafts._save_state(draft, ds, environ)

    await asyncio.to_thread(forget_earlier)
    run = operations.runner(app.hub)
    # Covers comparison previews and labels/table retention in addition to the runtime's own admission.
    with store.memory.reserve('analysis', s['width'] * s['height'] * 28 + MAX_BYTES * 2, 'own-picture comparison'):
        if draft:
            def tried():
                ds = drafts._state(draft, environ)
                if ds.get('trial') != tid or ds.get('state') != 'ran' or drafts.digest(drafts.folder(draft, environ), ds) != ds.get('tried'):
                    raise ValueError('try the current draft on this chosen picture first')
                got = json.loads((drafts.folder(draft, environ) / 'result.json').read_text())
                if ds.get('output') == 'image.2d.rgb8':
                    got['picture'] = (drafts.folder(draft, environ) / 'result.png').read_bytes()
                return ds, got

            ds, got = await asyncio.to_thread(tried)
            candidate = await owned_process.off_loop(output, d, 'candidate', got, data, ds['tries'][-1]['ms'])
            fingerprint, title = ds['tried'], (ds.get('fields') or {}).get('name') or draft
        else:
            if s.get('model') != model:
                raise ValueError('this picture was prepared for a different entry')
            def resolve():  # catalog scans, proof reads and the entry's state: files, so off the loop
                m = catalog.get(model) or catalog.mine(environ).get(model)
                installed = installed_entry(m, environ)
                if installed:
                    m = installed.m
                if m is None or (installed is None and (m.runner != mine.RUNNER or not mine.state(m, environ)['on'])):
                    raise ValueError('finish setup and its functional check before comparing')
                return m, installed

            m, installed = await asyncio.to_thread(resolve)
            i = next((i for i in m.implements if i.input == 'image.2d.rgb8' and i.output == JOBS[s['job']][1]), None)
            if i is None:
                raise ValueError('this entry does not declare the selected job; choose a matching job and picture')
            impl = SimpleNamespace(id=m.key if m.runner == 'fiji' else m.id if installed else mine.option_id(m),
                                   label=m.name, worker='analysis', version=m.version)
            fingerprint = await asyncio.to_thread(comparison_digest, m, environ, installed=installed)
            started = time.monotonic()
            from . import fiji_runner

            execute = fiji_runner._run if m.runner == 'fiji' else env_runner._run if installed else operations._cells_yours
            got = await execute(run, capture, operations.Prompt('field'), impl, {})
            if m.runner == 'fiji':
                generation = installed.folder.name if m.namespace else f'fiji-{installed.n}'
                if ((got.get('producer') or {}).get('generation') != generation
                        or await asyncio.to_thread(comparison_digest, m, environ) != fingerprint):
                    raise ValueError('The Fiji candidate changed during comparison; Compare again')
            candidate = await owned_process.off_loop(output, d, 'candidate', got, data, (time.monotonic() - started) * 1000)
            title = m.name
        baseline = {'says': 'No current default exists for this picture job; review its actual output below.', 'available': False}
        if s['job'] == 'find':
            choice = app.hub.settings.cells
            from . import options
            chosen = options.GROUPS['cells'].get(choice)
            default_model = catalog.get(chosen.model) if chosen and chosen.model else None
            if default_model and default_model.runs == 'cloud':
                raise ValueError('the current default uses a provider; choose a local Cells default before this comparison')
            # The default not set up here leaves the candidate's own result to review, never a refused comparison
            # (acceptance 2026-10-13: a fresh install's Cellpose-SAM not downloaded refused the whole comparison).
            why = await asyncio.to_thread(options.unavailable, 'cells', choice, environ)
            label = chosen.label if chosen else choice
            if why:
                baseline = {'says': f"{label} can't run here yet ({why}); set it up on its card to compare against it",
                            'available': False, 'choice': choice}
            else:
                started = time.monotonic()
                result = await run.run('cells', capture, operations.Prompt('field'), choice=choice, policy='raw',
                                       origin='user-chosen')
                baseline = {**await owned_process.off_loop(output, d, 'baseline', result.output, data,
                                                           (time.monotonic() - started) * 1000),
                            'available': True, 'says': label, 'choice': choice}
        def keep():
            comparison = {'inputSha256': s['sha256'], 'candidate': {**candidate, 'says': title}, 'baseline': baseline,
                          'digest': fingerprint, 'model': model, 'draft': draft, 'at': time.time(),
                          'artifacts': artifact_digest(d)}
            save(tid, {**s, 'comparison': comparison}, environ)
            if draft:
                with drafts._LOCK:
                    current = drafts._state(draft, environ)
                    if current.get('tried') != fingerprint or drafts.digest(drafts.folder(draft, environ), current) != fingerprint:
                        raise ValueError('the draft changed during comparison; try and compare again')
                    current['comparison'] = comparison
                    drafts._save_state(draft, current, environ)
            return comparison

        return await asyncio.to_thread(keep)


def artifact_digest(d):
    h = hashlib.sha256()
    for name in ('candidate.json', 'candidate.png', 'baseline.json', 'baseline.png'):
        p = d / name
        h.update(name.encode() + b'\0' + (p.read_bytes() if p.exists() else b''))
    return h.hexdigest()


def use(model, tid, environ=os.environ):
    s = state(tid, environ)
    c = s.get('comparison') or {}
    m = catalog.get(model) or catalog.mine(environ).get(model)
    installed = installed_entry(m, environ)
    if installed:
        m = installed.m
    if m is None or c.get('model') != model or c.get('inputSha256') != s['sha256'] or c.get('digest') != comparison_digest(m, environ):
        raise ValueError('the entry or comparison changed; Compare again before Use it')
    picture(tid, environ)
    if c.get('artifacts') != artifact_digest(folder(tid, environ)):
        raise ValueError('the comparison output changed; Compare again before Use it')
    p = marker(model, environ)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps({'model': model, 'trial': tid, 'adoptedDigest': c['digest']}))
    tmp.replace(p)
    return {'model': model, 'says': f'{m.name} is now available as a choice. Its results remain yours to judge.'}


def detail(m):
    """What tells two entries of one name apart in the list: the reviewed template version it was made with, else its
    version (added again with another template version, they are two entries of one name)."""
    made = re.match(r'catalog/templates/(\S+) v(\S+)', str((m.pins or {}).get('adapter') or ''))
    return f'made with the {made.group(1)} template v{made.group(2)}' if made else str(m.version or '')


def wire(environ=os.environ):
    rows = []
    for p in (root(environ) / 'models').glob('*.json'):
        try:
            record = json.loads(p.read_text())
            m = catalog.get(record['model']) or catalog.mine(environ).get(record['model'])
            if m is not None and record.get('trial'):  # none yet: its card says to choose a picture (await_comparison)
                installed = installed_entry(m, environ)
                if pending(installed.m if installed else m, environ):
                    rows.append({**state(record['trial'], environ), 'name': m.name, 'link': m.source.get('url', ''),
                                 'needsAdapter': m.runner not in ('python-env', 'cellpose-file', 'fiji'), 'detail': detail(m)})
        except (OSError, ValueError, KeyError):
            continue
    return rows
