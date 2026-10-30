"""Review declared retained-document links without following arbitrary saved paths.

This is the scientific-record part of collection preflight. External owners are
reported, never opened here. A resolved execution locator is not exact evidence.
The caller must separately collect journals, media and other owned stores.
"""
import hashlib

from .project_roots import ProjectLocationError
from .scientific_projects import DIGEST, PROJECT_ID, _json

# Only producer-owned reference fields, never prose, arbitrary parameters or URLs.
# A wildcard visits a declared sequence or keyed collection; no recursive search
# for lookalike dictionaries in uploaded data is performed.
PATHS = (
    'materialMembership.reference',
    'hookeEdit.base', 'hookeEdit.producer.history',
    'hookeEdit.changes.*.base', 'hookeEdit.changes.*.producer.history',
    'hookeOutput.base', 'hookeOutput.producer.history',
    'hookeCopy.source', 'hookeCopy.history.*.source',
    'populationSource.nativeSource.nativeSource',
    'methodEvidence.source', 'methodEvidence.arms.*.mask',
    'methodEvidence.arms.*.lineage.*', 'methodEvidence.arms.*.histories.*',
    'matrixOperation.inputs.*',
    'workflowRun.inputs.*', 'workflowRun.bindings.*', 'workflowRun.derivedFrom.result',
    'workflowAttempt.inputs.*', 'workflowAttempt.output', 'workflowAttempt.working',
    'workflowOutput.attempt', 'workflowOutput.inputs.*',
    'workflowLate.attempt', 'workflowLate.outcome.output', 'workflowLate.outcome.working',
    'comparison.input', 'comparison.arms.*.scene', 'comparison.retained.*.result',
    'comparison.attempts.*.result', 'comparison.chosen.result',
    'researchEffort.inputs.*', 'researchEffort.evidenceReads.*.artifact',
    'researchEffort.claims.*.evidence.*.artifact', 'researchEffort.claimChanges.*.evidence.*.artifact',
    'researchEffort.questions.*.basis', 'researchEffort.questions.*.answer.evidence.*',
    'researchEffort.tasks.*.result.artifact',
    'researchEffort.experiments.*.specification.inputs.*',
    'researchEffort.experiments.*.specification.bindings.*',
)
LOCATORS = (
    'workflowAttempt.run', 'workflowAttempt.originAttempt', 'workflowOutput.run',
    'workflowRun.derivedFrom.run', 'workflowRun.derivedFrom.attempt',
    'researchEffort.experiments.*.execution',
)
MAX_LINKS = 1_000_000
MAX_ISSUES = 10_000


def _shown_reference(ref):
    if type(ref) is not dict:
        return None
    return {k: v for k, v in ref.items() if k in ('id', 'session', 'revision', 'digest')
            and (type(v) is int or type(v) is str and len(v) <= 128)}


def _values(value, parts, path='state'):
    if not parts:
        if value is not None:
            yield path, value
        return
    key, *rest = parts
    if key == '*':
        children = value.items() if type(value) is dict else enumerate(value) if type(value) is list else ()
        for name, child in children:
            yield from _values(child, rest, f'{path}.{name}')
    elif type(value) is dict and key in value:
        yield from _values(value[key], rest, f'{path}.{key}')


def declared(saved):
    """Yield normalized links from their documented fields, with source locations."""
    state = saved.document['state']
    for path, ref in _values(saved.document, ['resources', '*'], ''):
        # File/array component hashes are checked by the existing byte validators.
        if type(ref) is dict and 'component' not in ref and type(ref.get('revision')) is int:
            yield path.lstrip('.'), ref, False
    for pattern in PATHS:
        for path, ref in _values(state, pattern.split('.')):
            # Captured native inputs predate artifact-reference's digest name.
            # Normalize this producer's exact shape, not arbitrary saved objects.
            if pattern == 'populationSource.nativeSource.nativeSource' and type(ref) is dict:
                ref = {**ref, 'digest': ref.get('revisionDigest')}
            yield path, ref, False
    if saved.document['kind'] == 'scientific-python-history':
        for path, ref in _values(state, ['input']):
            yield path, ref, False
    for pattern in LOCATORS:
        for path, value in _values(state, pattern.split('.')):
            if type(value) is str:
                value = {'id': value, 'session': saved.session}
            yield path, value, True


class RecordDependencyReview:
    """Bounded review reused while the snapshot verifier reads accepted history.

    The supplied store is the captured Project's own record store. It must never
    be a multi-Project resolver: external links cannot authorize another read.
    """
    def __init__(self, store, session):
        self.store, self.session = store, session
        self.links = 0
        self.resolved = {}
        self.issues = []
        self._seen_issues = set()

    def inspect(self, saved, *, check=lambda: None):
        for path, ref, locator in declared(saved):
            check()
            self.links += 1
            if self.links > MAX_LINKS:
                raise ProjectLocationError('Project dependency review exceeds its reference allowance')
            issue = self._resolve(ref, locator)
            if issue:
                key = (saved.id, hashlib.sha256(_json([path, ref])).hexdigest(), issue)
                if key in self._seen_issues:
                    continue
                if len(self.issues) >= MAX_ISSUES:
                    raise ProjectLocationError('Project dependency review exceeds its issue allowance')
                self._seen_issues.add(key)
                self.issues.append({'source': {'id': saved.id, 'session': saved.session,
                    'revision': saved.revision, 'digest': saved.digest},
                    'field': path[:1000], 'status': issue, 'reference': _shown_reference(ref)})

    def _resolve(self, ref, locator):
        if type(ref) is not dict or type(ref.get('id')) is not str or not PROJECT_ID.fullmatch(ref['id']):
            return 'unsupported-reference'
        owner = ref.get('session', self.session)
        if type(owner) is not str:
            return 'unsupported-reference'
        # Do not probe another Project, even if this process could access it.
        if owner != self.session:
            return 'external-project-inclusion-required'
        revision = ref.get('revision')
        digest = ref.get('digest')
        if not locator and (type(revision) is not int or revision < 1):
            return 'unsupported-reference'
        if 'digest' in ref and (type(digest) is not str or not DIGEST.fullmatch(digest)):
            return 'unsupported-reference'
        key = (ref['id'], None if locator else revision)
        try:
            if key not in self.resolved:
                self.resolved[key] = self.store.read(ref['id'], self.session, revision=key[1]).digest
            result = self.resolved[key]
        except (OSError, ValueError):
            return 'missing-or-unreadable'
        if digest is not None and result != digest:
            return 'digest-mismatch'
        return 'unpinned-execution-locator' if locator else None

    def result(self):
        return {'scope': 'declared-scientific-record-references', 'referencesChecked': self.links,
                'resolvedRevisions': len(self.resolved), 'issues': self.issues,
                'completeProject': False,
                'reason': 'This review does not assess journal, media, shared capture or extension dependencies.'}
