"""Real accepted store/workflow records; synthetic inputs, no collection-complete claim."""
import pytest

from orchestrator.project_folder_copy import estimate
from orchestrator.project_record_dependencies import RecordDependencyReview
from orchestrator.project_repository import document_root
from orchestrator.project_roots import ProjectLocationError
from orchestrator.project_snapshot_integrity import review_dependencies
from orchestrator.research.efforts import Efforts
from orchestrator.scientific_comparisons import identity
from orchestrator.scientific_projects import ProjectStore
from orchestrator.workflows import Workflows
from tests.test_project_folder_copy import setup


def study(store, source):
    ref = identity(source)
    ref.pop('session')
    return Efforts(store).create(source.session, request='dependency-study', title='Study',
        objective='Inspect evidence', criteria='Exact revisions', budget_usd=0, inputs=[ref])


def test_all_old_study_inputs_checked_without_mutating_history(tmp_path):
    root, _, first, second = setup(tmp_path)
    store = ProjectStore(document_root(root))
    saved = study(store, first)
    updated = saved.document
    updated['state']['researchEffort']['inputs'] = []
    store.save(saved.id, root.session, updated, expected_revision=1)
    before = review_dependencies(root)
    assert before['referencesChecked'] == 1 and before['issues'] == []
    assert before['completeProject'] is False
    old = document_root(root) / first.id / 'revisions' / f'{first.digest}.json'
    old.unlink()
    # A missing accepted revision also refuses the surrounding history scan.
    with pytest.raises(ValueError):
        review_dependencies(root)
    assert store.read(saved.id, root.session).revision == 2


def test_missing_and_wrong_digest_are_reported_at_the_citing_revision(tmp_path):
    root, _, first, _ = setup(tmp_path)
    store = ProjectStore(document_root(root))
    saved = study(store, first)
    updated = saved.document
    refs = updated['state']['researchEffort']['inputs']
    refs[0]['digest'] = '0' * 64
    refs.append({'id': 'p' + 'f' * 32, 'revision': 1, 'digest': '1' * 64})
    changed = store.save(saved.id, root.session, updated, expected_revision=1)
    review = estimate(root)['dependencyReview']
    assert {i['status'] for i in review['issues']} == {'digest-mismatch', 'missing-or-unreadable'}
    assert all(i['source'] == identity(changed) for i in review['issues'])
    assert review['completeProject'] is False


def test_external_owner_is_never_read_and_free_text_is_not_a_dependency(tmp_path, monkeypatch):
    root, _, first, _ = setup(tmp_path)
    store = ProjectStore(document_root(root))
    ref = {**identity(first), 'session': '2026-10-30'}
    source = store.create(root.session, {'kind': 'note', 'title': 'References',
        'state': {'text': str(ref), 'uploadedJson': {'matrixOperation': {'inputs': {'matrix': ref}}}},
        'resources': [{k: v for k, v in ref.items() if k != 'digest'}], 'runtimes': []})
    calls = []
    def denied(*args, **kwargs):
        calls.append(args)
        raise AssertionError('External Project must not be opened')
    monkeypatch.setattr(store, 'read', denied)
    reviewer = RecordDependencyReview(store, root.session)
    reviewer.inspect(source)
    result = reviewer.result()
    assert not calls and result['referencesChecked'] == 1
    assert result['issues'][0]['status'] == 'external-project-inclusion-required'


def test_workflow_inputs_attempts_and_output_provenance_survive_review(tmp_path):
    root, _, first, _ = setup(tmp_path)
    store = ProjectStore(document_root(root))
    workflows = Workflows(store)
    graph = {'format': 1, 'input': {'kind': 'text-file'}, 'nodes': [
        {'id': 'export', 'name': 'Retain', 'kind': 'export', 'needs': ['input'], 'settings': {}}]}
    definition = workflows.create('Retain evidence', graph, 'a' * 32)
    run = workflows.start(definition.id, 1, root.session, [identity(first)], 'b' * 32, actor='studio')
    claim = workflows.claim(run.id, root.session)
    output = store.create(root.session, {'kind': 'note', 'title': 'Output', 'resources': [], 'runtimes': [],
        'state': {'workflowOutput': {'run': run.id, 'attempt': claim['attempt'], 'inputs': [identity(first)]}}})
    workflows.finish(claim, {'status': 'completed', 'output': identity(output), 'note': ''})
    review = review_dependencies(root)
    assert review['referencesChecked'] >= 7
    assert review['issues'] and all(i['status'] == 'unpinned-execution-locator' for i in review['issues'])
    assert workflows.status(run.id, root.session)['status'] == 'completed'
    # The run embeds its frozen graph; review never opens the host workflow library.
    assert all(i['reference']['id'] != definition.id for i in review['issues'])


def test_claim_question_and_matrix_links_keep_exact_digests(tmp_path):
    root, _, first, _ = setup(tmp_path)
    store = ProjectStore(document_root(root))
    ref = identity(first)
    saved = study(store, first)
    doc = saved.document
    state = doc['state']['researchEffort']
    state.update(claims=[{'evidence': [{'artifact': ref, 'relation': 'supports'}]}],
        claimChanges=[{'evidence': [{'artifact': ref}]}], evidenceReads=[{'artifact': ref}],
        questions=[{'basis': identity(saved), 'answer': {'evidence': [ref]}}])
    updated = store.save(saved.id, root.session, doc, expected_revision=1)
    source = store.create(root.session, {'kind': 'matrix-result', 'title': 'Report', 'resources': [], 'runtimes': [],
        'state': {'matrixOperation': {'inputs': {'matrix': {**ref, 'bytes': 10, 'sha256': '2' * 64}}}}})
    reviewer = RecordDependencyReview(store, root.session)
    reviewer.inspect(updated); reviewer.inspect(source)
    assert reviewer.result()['referencesChecked'] == 7
    assert reviewer.result()['issues'] == []


def test_limits_and_cancellation_preserve_research(tmp_path, monkeypatch):
    root, _, first, _ = setup(tmp_path)
    store = ProjectStore(document_root(root))
    saved = study(store, first)
    monkeypatch.setattr('orchestrator.project_record_dependencies.MAX_LINKS', 0)
    with pytest.raises(ProjectLocationError, match='reference allowance'):
        review_dependencies(root)
    def stop():
        raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        review_dependencies(root, check=stop)
    assert store.read(saved.id, root.session) == saved
