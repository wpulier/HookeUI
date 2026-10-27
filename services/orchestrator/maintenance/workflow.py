"""Exact local interactive update trials, acceptance and restore-only first checks.

Uses the existing maintenance target/decision ledger and private workflow receipts.
It never activates a candidate during trial or replays the scientific operation.
"""
import hashlib
import os

from .. import bundle_admission, bundle_review, env_runner, envs
from . import compat, discover, prepare, review, revocations, store


def target(m, generation=None, environ=os.environ):
    """Resolve only the current update, not an arbitrary prepared generation."""
    active = env_runner.selected(m, environ)
    found = discover.last(m.key, environ).get('found') or {}
    snapshot = bool(found.get('fetched') and found.get('snapshot') and found.get('generation'))
    candidate = discover.candidate_manifest(m, found['snapshot'], environ) if snapshot else discover.fresh(m.key, environ)
    if candidate is None or candidate.key != m.key or not bundle_admission.local_workflow(candidate):
        raise ValueError('This update has no supported local interactive workflow')
    current = envs.generation(candidate, environ)
    if (active is None or active.folder.name == current or generation is not None and generation != current
            or snapshot and found['generation'] != current
            or not snapshot and current in discover.superseded(m, environ)):
        raise ValueError('The update candidate changed; check its current update card')
    if revocations.revoked(candidate, candidate.version, environ):
        raise ValueError('This update was revoked; the active version stays selected')
    changed = compat.compare(active.m, candidate)
    if not changed['compatible']:
        mark = review.fingerprint(candidate, current, changed['review'])
        if review.accepted(candidate, mark, environ) is None and not review.prepare_first(changed['review']):
            raise ValueError('Accept this update’s changed requirements before trying it')
        if step := review.missing_setup(active.m, candidate, environ):
            raise ValueError(f'Before trying this update: {step}')
    return candidate, current


def installed(m, generation, environ=os.environ):
    target(m, generation, environ)
    result = env_runner.generation(m, generation, environ)
    if result is None or not bundle_admission.local_workflow(result.m):
        raise ValueError('Try this exact update, use its controls, Save tool and reopen before reviewing it')
    return result


def accepted(m, generation, digest, environ=os.environ):
    from .promote import decisions
    return bool(digest and any(row.get('action') == 'accept-workflow' and row.get('generation') == generation
                and row.get('digest') == digest for row in decisions(m.key, environ)))


def accept(m, generation, digest, environ=os.environ, still_current=lambda:True):
    with prepare._entry_lock(m.key, environ) as acquired:
        if not acquired:
            raise ValueError('This update is changing; review it again')
        with store.locked(environ):
            current = installed(m, generation, environ)
            receipt = bundle_review.projection(current)
            checked = installed(m, generation, environ)
            if receipt['digest'] != digest or bundle_review.identity(checked) != digest or not still_current():
                raise ValueError('The Project or update receipt changed; review it again')
            store.append(store.path('decisions.jsonl', environ), {
                'at':store.now(), 'key':m.key, 'action':'accept-workflow', 'generation':generation,
                'version':current.m.version, 'digest':digest, 'by':'user', 'how':'screen'})
    return {'model':m.key,'generation':generation,
            'says':'This exact update is approved for Apply. The active version has not changed.'}


def require_accepted(m, generation, digest, environ=os.environ):
    result = installed(m, generation, environ)
    actual = bundle_review.identity(result)
    if actual != digest or not accepted(m, generation, actual, environ):
        raise ValueError('Review and enable this exact update receipt before Apply')
    return result


def first_check(installed, environ=os.environ, *, check_bound=lambda:None):
    """Fresh confined restore/render of every exact saved member; no controls or code."""
    from .. import paths, scientific_project_review
    from ..scientific_projects import ProjectStore
    from gpu_worker.bundle_contract import capability_key
    projects = ProjectStore(paths.Paths.from_env(environ).results / 'projects', runtime_root=paths.envs(environ))
    digest = bundle_review.identity(installed)
    if not accepted(installed.m, installed.folder.name, digest, environ):
        raise ValueError('The applied update no longer has its exact accepted workflow receipt')
    def check():
        check_bound()
        current = env_runner.selected(installed.m, environ)
        if current is None or current.folder != installed.folder or bundle_review.identity(current) != digest:
            raise ValueError('The selected update changed during its restore check')
    check()
    for case in installed.proof['cases']:
        ref = case['saved']
        saved = projects.read(ref['id'], ref['session'], revision=ref['revision'])
        view = saved.document['state']['scientificView']
        runtime = view['runtime']
        if (saved.digest != ref['digest'] or runtime['bundle'] != installed.m.key
                or runtime['generation'] != installed.folder.name
                or capability_key(runtime['bundle'],runtime['operation']) != case['member']
                or hashlib.sha256(bundle_review._json(view.get('tool'))).hexdigest() != case['toolSha256']):
            raise ValueError('The saved update trial no longer matches its accepted input and tool state')
        check()
        with scientific_project_review.review(projects, ref['id'], ref['session'], ref['revision'], environ=environ) as (restored, worker):
            if restored.digest != saved.digest:
                raise ValueError('The saved update trial changed before reopening')
            reply, pixels = worker.request({'op':'render','width':256,'height':192})
            if not isinstance(reply.get('frame'),dict) or len(pixels) != 256*192*4:
                raise ValueError('The applied update did not render its retained state')
            check()
