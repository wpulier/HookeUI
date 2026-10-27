"""Real retained storage; synthetic prepared tools, not UI qualification."""
from contextlib import contextmanager
from types import SimpleNamespace
import numpy as np
import pytest
from orchestrator import scientific_comparisons as comparisons, scientific_tools as tools
from orchestrator.scientific_projects import ProjectStore, InvalidProject
from tests.test_scientific_project_review import saved_view
from tests.test_scientific_tools import RAW, OWNER, GEN
from gpu_worker.bundle_contract import parse_members, view

KEYS = ['mine/next-tool#viewer', 'mine/second-tool#viewer']

@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path)
    saved, data, ref = saved_view(store)
    rows = [{'key': key, 'generation': GEN, 'owner': key.split('#')[0], 'name': key,
             'canReview': True, 'reason': '', 'scope': ''} for key in KEYS]
    monkeypatch.setattr(tools, 'discover', lambda *a, **kw: {'tools': rows})
    # Private unqualified trial: no selected generation or accepted local workflow is claimed.
    monkeypatch.setattr(tools.env_runner, 'in_use', lambda *a: None)
    monkeypatch.setattr(tools.env_runner, 'generation', lambda *a: None)
    @contextmanager
    def prepared(key, generation, environ):
        assert key in KEYS and generation == GEN
        owner = SimpleNamespace(**{**vars(OWNER), 'key':key.split('#')[0], 'members':(RAW,)})
        yield SimpleNamespace(m=view(owner, parse_members([RAW], owner)[0]))
    monkeypatch.setattr(tools.prepared_bundles, 'prepared_member', prepared)
    return store, saved, data, ref


def test_same_captured_input_independent_scenes_and_retry(setup):
    store, source, data, ref = setup
    group = comparisons.create(store, source.id, source.session, source.revision, KEYS, 'a'*32)
    assert comparisons.create(store, source.id, source.session, source.revision, KEYS, 'a'*32) == group
    first = comparisons.prepare_arm(store, group.id, group.session, 1, 'a')
    assert comparisons.prepare_arm(store, group.id, group.session, 1, 'a') == first
    second = comparisons.prepare_arm(store, group.id, group.session, first.revision, 'b')
    _, state = comparisons.read(store, second.id, second.session, second.revision)
    assert state['attempts'] == [] and state['input'] == comparisons.identity(source)
    ids = []
    for arm in state['arms']:
        branch = comparisons.exact(store, arm['scene'], group.session)
        ids.append(branch.id)
        target = branch.document['resources'][0]
        actual = store.array_source(branch.id, branch.session, branch.revision, target)
        assert np.array_equal(actual.read(tuple(slice(0,n) for n in data.shape)), data)
        assert branch.document['state']['scientificView']['runtime']['bundle'] == arm['owner']
    assert len(set(ids+[source.id,group.id])) == 4
    assert store.read(source.id,source.session) == source


def attempts(store, group):
    return comparisons.read(store, group.id, group.session, group.revision)[1]['attempts']


def test_failed_alternative_is_recorded_beside_successful_arm_and_original(setup, monkeypatch):
    store, source, _, _ = setup
    group = comparisons.create(store, source.id, source.session, source.revision, KEYS, 'b'*32)
    first = comparisons.prepare_arm(store, group.id, group.session, 1, 'a')
    arm_a = first.document['state']['comparison']['arms'][0]
    def fail(*a, **kw): raise ValueError('Preparation incomplete')
    monkeypatch.setattr(tools.prepared_bundles, 'prepared_member', fail)
    failed = comparisons.prepare_arm(store, first.id, first.session, first.revision, 'b')
    assert failed.revision == first.revision + 1
    _, state = comparisons.read(store, failed.id, failed.session, failed.revision)
    assert state['arms'][0] == arm_a and state['arms'][1]['scene'] is None
    [row] = state['attempts']
    assert (row['arm'], row['outcome'], row['source'], row['result']) == ('b', 'failed', 'preparation', None)
    assert 'Preparation incomplete' in row['says'] and row['timing']['preparationSeconds'] >= 0
    assert row['model']['capability'] == KEYS[1] and row['model']['generation'] == GEN
    assert store.read(first.id, first.session, revision=first.revision) == first
    assert store.read(source.id,source.session) == source
    # The same failed request retried from its original revision is the same record, not a second one.
    assert comparisons.prepare_arm(store, first.id, first.session, first.revision, 'b') == failed
    with pytest.raises(InvalidProject,match='not saved from'):
        comparisons.retain(store, failed.id, failed.session, failed.revision, 'a', source.id, source.revision)


def test_cancel_before_fork_records_cancellation_and_publishes_no_arm(setup):
    store, source, _, _ = setup
    group = comparisons.create(store, source.id, source.session, source.revision, KEYS, 'c'*32)
    before = {saved.id for saved in store.list(source.session)}
    def stop(): raise RuntimeError('Cancelled')
    cancelled = comparisons.prepare_arm(store, group.id, group.session, 1, 'a', check=stop)
    assert {saved.id for saved in store.list(source.session)} == before
    [row] = attempts(store, cancelled)
    assert (row['outcome'], row['source']) == ('cancelled', 'preparation') and 'Stopped while preparing' in row['says']
    assert cancelled.document['state']['comparison']['arms'][0]['scene'] is None
    # Cancelling is not a final verdict: the arm can be prepared again.
    again = comparisons.prepare_arm(store, cancelled.id, cancelled.session, cancelled.revision, 'a')
    assert again.document['state']['comparison']['arms'][0]['scene'] is not None
    assert attempts(store, again) == [row]


def test_one_integration_may_fill_three_arms_with_their_own_scenes(setup):
    store, source, _, _ = setup
    three = [KEYS[0]] * 3
    group = comparisons.create(store, source.id, source.session, source.revision, three, '9'*32)
    for arm in 'abc':
        group = comparisons.prepare_arm(store, group.id, group.session, group.revision, arm)
    scenes = [a['scene']['id'] for a in group.document['state']['comparison']['arms']]
    assert len(set(scenes)) == 3 and source.id not in scenes


def tool_result(store, group, arm_id, threshold, *, history=None, at=None):
    """Stand-in for Save tool in an arm's private session: settings, layers and producing history."""
    arm = next(a for a in group.document['state']['comparison']['arms'] if a['id'] == arm_id)
    branch = comparisons.exact(store, arm['scene'], group.session)
    doc = branch.document
    view = doc['state']['scientificView']
    view.pop('initializeTool', None)
    view['layers'] = [{'input': key, 'metadata': {'name': key, 'type': 'labels', 'shape': [6, 24, 28]}} for key in view['inputs']]
    settings = {'format':'scientific-tool-document-v1','schema':'test.settings','version':1,'state':{'threshold':threshold}}
    view['tool'] = {'document': settings, 'recipe': settings,
                    'bindings': {f'k{i}': row['input'] for i, row in enumerate(view['layers'])}}
    doc['state']['hookeOutput'] = {'base': identity(branch), 'at': at or 1792467900.0,
                                   'producer': {'kind': 'scientific-session', 'history': history and identity(history)}}
    doc['resources'].append(reference(branch))
    return store.save(branch.id, branch.session, doc, expected_revision=branch.revision)


def history_of(store, session, *times):
    return store.create(session, {'kind': 'scientific-python-history', 'title': 'Scientific history', 'resources': [],
        'runtimes': [], 'state': {'actions': [{'id': f'x{i}', 'request': {'op': 'native-panel-change'}, 'context': {},
                                               'state': 'completed', 'at': t, 'finishedAt': t + 0.1}
                                              for i, t in enumerate(times)]}})


from orchestrator.scientific_comparisons import identity, reference  # noqa: E402


def test_three_arms_one_failed_one_cancelled_keep_success_settings_and_timing_after_reopen(setup, monkeypatch):
    store, source, _, _ = setup
    group = comparisons.create(store, source.id, source.session, source.revision, [KEYS[0]] * 3, '8'*32)
    group = comparisons.prepare_arm(store, group.id, group.session, group.revision, 'a')
    group = comparisons.prepare_arm(store, group.id, group.session, group.revision, 'b')
    history = history_of(store, group.session, 1792467800.0, 1792467836.0)
    result = tool_result(store, group, 'a', 3, history=history, at=1792467900.0)
    kept = comparisons.retain(store, group.id, group.session, group.revision, 'a', result.id, result.revision)
    cancelled = comparisons.settle(store, kept.id, kept.session, kept.revision, 'b', 'cancelled',
                                   'Stopped the private session during segmentation', '1'*32)
    assert comparisons.settle(store, kept.id, kept.session, kept.revision, 'b', 'cancelled',
                              'Stopped the private session during segmentation', '1'*32) == cancelled
    def fail(*a, **kw): raise ValueError('The integration needs preparation')
    monkeypatch.setattr(tools.prepared_bundles, 'prepared_member', fail)
    final = comparisons.prepare_arm(store, cancelled.id, cancelled.session, cancelled.revision, 'c')
    reopened = ProjectStore(store.root)
    saved, state = comparisons.read(reopened, final.id, final.session, final.revision)
    assert [(r['arm'], r['outcome'], r['source']) for r in state['attempts']] == [
        ('a', 'succeeded', 'saved-result'), ('b', 'cancelled', 'researcher'), ('c', 'failed', 'preparation')]
    success = state['attempts'][0]
    assert success['result'] == identity(result) and state['retained'] == [{'arm': 'a', 'result': identity(result)}]
    assert success['settings'] == {'schema': 'test.settings', 'version': 1, 'values': {'threshold': 3},
                                   'captured': 'panel-at-save-tool'}
    assert success['model']['device']['by'] in ('integration manifest', 'not declared')
    assert success['timing'] == {'savedAt': 1792467900.0, 'computeSeconds': None,
                                 'computeSays': 'The tool does not report its own compute time.', 'controls': 2,
                                 'firstControlAt': 1792467800.0, 'lastControlAt': 1792467836.0,
                                 'controlToSaveSeconds': 64.0, 'sessionSeconds': 100.0}
    assert [o['stored'] for o in success['outputs']] == [result.document['state']['scientificView']['inputs'][l['input']]
                                                         for l in result.document['state']['scientificView']['layers']]
    assert state['attempts'][1]['says'] == 'Stopped the private session during segmentation'
    assert state['arms'][0]['scene'] is not None and state['arms'][2]['scene'] is None
    assert reopened.read(result.id, result.session, revision=result.revision) == result
    assert reopened.read(source.id, source.session) == source
    assert state['chosen'] is None


def test_choosing_a_preferred_result_is_explicit_and_changes_nothing_else(setup):
    store, source, _, _ = setup
    group = comparisons.create(store, source.id, source.session, source.revision, KEYS, '7'*32)
    group = comparisons.prepare_arm(store, group.id, group.session, 1, 'a')
    result = tool_result(store, group, 'a', 5)
    with pytest.raises(InvalidProject, match='kept as succeeded'):
        comparisons.choose(store, group.id, group.session, group.revision, 'a', result.id, result.revision)
    kept = comparisons.retain(store, group.id, group.session, group.revision, 'a', result.id, result.revision)
    with pytest.raises(InvalidProject, match='kept as succeeded'):
        comparisons.choose(store, kept.id, kept.session, kept.revision, 'b', result.id, result.revision)
    chosen = comparisons.choose(store, kept.id, kept.session, kept.revision, 'a', result.id, result.revision)
    assert comparisons.choose(store, kept.id, kept.session, kept.revision, 'a', result.id, result.revision) == chosen
    state = chosen.document['state']['comparison']
    assert state['chosen']['arm'] == 'a' and state['chosen']['result'] == identity(result)
    assert state['arms'] == kept.document['state']['comparison']['arms']
    assert store.read(result.id, result.session) == result and store.read(source.id, source.session) == source
    cleared = comparisons.choose(store, chosen.id, chosen.session, chosen.revision, None, None, None)
    assert cleared.document['state']['comparison']['chosen'] is None
    assert comparisons.read(store, cleared.id, cleared.session, chosen.revision)[1]['chosen'] == state['chosen']


def test_researcher_failure_needs_words_and_v1_comparisons_still_open(setup):
    store, source, _, _ = setup
    group = comparisons.create(store, source.id, source.session, source.revision, KEYS, '6'*32)
    with pytest.raises(InvalidProject, match='Say what failed'):
        comparisons.settle(store, group.id, group.session, 1, 'a', 'failed', '  ', '2'*32)
    with pytest.raises(InvalidProject, match='failure or a cancellation'):
        comparisons.settle(store, group.id, group.session, 1, 'a', 'succeeded', 'fine', '2'*32)
    doc = group.document
    state = doc['state']['comparison']
    doc['state']['comparison'] = {'format': 'scientific-comparison-v1', 'input': state['input'],
                                  'arms': state['arms'], 'retained': []}
    old = store.create(group.session, doc)
    _, read = comparisons.read(store, old.id, old.session, 1)
    assert read['attempts'] == [] and read['chosen'] is None
    failed = comparisons.settle(store, old.id, old.session, 1, 'b', 'failed', 'Tool showed: CUDA error', '3'*32)
    assert failed.document['state']['comparison']['format'] == comparisons.FORMAT
    assert store.read(old.id, old.session, revision=1) == old


def test_only_exact_arm_outputs_can_be_retained_and_reopened(setup):
    from orchestrator.scientific_outputs import OutputSave
    from tests.test_scientific_outputs import Worker
    store, source, _, _ = setup
    group = comparisons.create(store, source.id, source.session, source.revision, KEYS, 'd'*32)
    group = comparisons.prepare_arm(store, group.id, group.session, 1, 'a')
    arm = group.document['state']['comparison']['arms'][0]
    branch = comparisons.exact(store, arm['scene'], group.session)
    worker = Worker()
    output = OutputSave(store, branch, 'request-1234567890123', 'a'*32, {'semantic':'instance-labels','axes':['z','x']})
    try:
        output.capture(worker); output.publish()
        saved = store.read(output.target, branch.session)
        rows = comparisons.outcomes(store, group.id, group.session, group.revision)
        assert len(rows) == 1 and rows[0]['arm'] == 'a' and not rows[0]['retained']
        assert rows[0]['configuration'] == 'not-retained'
        accepted = comparisons.retain(store, group.id, group.session, group.revision, 'a', saved.id, saved.revision)
        assert comparisons.retain(store, group.id, group.session, group.revision, 'a', saved.id, saved.revision) == accepted
        reopened, state = comparisons.read(ProjectStore(store.root), accepted.id, accepted.session, accepted.revision)
        assert state['retained'] == [{'arm':'a','result':comparisons.identity(saved)}]
        assert state['attempts'][0]['settings'] is None and state['attempts'][0]['timing']['controls'] is None
        assert comparisons.outcomes(store, reopened.id,reopened.session,reopened.revision)[0]['retained']
        assert store.read(source.id,source.session) == source
        assert worker.calls == 1
    finally: output.close()

from tests.test_scientific_project_api import web, ORIGIN  # noqa: E402,F401


def test_comparison_http_preserves_project_boundary_and_duplicate_request(web, setup):
    client, built = web
    store = built.hub.resources.projects
    source, _, _ = saved_view(store, session='2026-10-19')
    path = f'/api/scientific-projects/{source.id}/comparison'
    payload = {'session':source.session,'revision':source.revision,'action':'create','choices':KEYS,'request':'f'*32}
    headers = {'Origin':ORIGIN,'X-MicroscopeMind':'scientific-comparison'}
    assert client.post(path,json=payload).status_code == 403
    built.day.sid = '2026-10-19.999'
    assert client.post(path,json=payload,headers=headers).status_code == 409
    built.day.sid = source.session
    first = client.post(path,json=payload,headers=headers)
    assert first.status_code == 200,first.text
    assert client.post(path,json=payload,headers=headers).json() == first.json()
    group = first.json()['project']
    arm_path = f"/api/scientific-projects/{group['id']}/comparison"
    response = client.post(arm_path,headers=headers,json={'session':source.session,'revision':1,'action':'prepare','arm':'a'})
    assert response.status_code == 200,response.text
    assert response.json()['project']['document']['state']['comparison']['arms'][0]['scene']
    assert store.read(source.id,source.session) == source


def test_comparison_http_records_researcher_outcomes_and_explicit_choice(web, setup):
    client, built = web
    store = built.hub.resources.projects
    source, _, _ = saved_view(store, session='2026-10-19')
    built.day.sid = source.session
    headers = {'Origin':ORIGIN,'X-MicroscopeMind':'scientific-comparison'}
    created = client.post(f'/api/scientific-projects/{source.id}/comparison', headers=headers, json={
        'session':source.session,'revision':source.revision,'action':'create','choices':[KEYS[0]]*3,'request':'e'*32}).json()['project']
    path = f"/api/scientific-projects/{created['id']}/comparison"
    base = {'session':source.session,'revision':1}
    assert client.post(path,headers=headers,json={**base,'action':'settle','arm':'d','outcome':'failed','says':'x','request':'1'*32}).status_code == 400
    assert client.post(path,headers=headers,json={**base,'action':'settle','arm':'a','outcome':'succeeded','says':'x','request':'1'*32}).status_code == 400
    settled = client.post(path,headers=headers,json={**base,'action':'settle','arm':'c','outcome':'cancelled','says':'','request':'1'*32})
    assert settled.status_code == 200, settled.text
    state = settled.json()['project']['document']['state']['comparison']
    assert [(r['arm'],r['outcome'],r['source']) for r in state['attempts']] == [('c','cancelled','researcher')]
    refused = client.post(path,headers=headers,json={'session':source.session,'revision':2,'action':'choose','arm':'a','result':source.id,'resultRevision':source.revision})
    assert refused.status_code == 400 and 'kept as succeeded' in refused.json()['says']
    assert client.post(path,headers=headers,json={'session':source.session,'revision':2,'action':'choose','arm':None,'result':None,'resultRevision':None}).status_code == 200


def test_an_unrecorded_failure_says_both_failures_in_its_message(setup, monkeypatch):
    store, source, _, _ = setup
    group = comparisons.create(store, source.id, source.session, source.revision, KEYS, '5'*32)
    def fail(*a, **kw): raise ValueError('Preparation incomplete')
    monkeypatch.setattr(tools.prepared_bundles, 'prepared_member', fail)
    def full(*a, **kw): raise OSError('disk full')
    monkeypatch.setattr(comparisons, '_save_attempt', full)
    with pytest.raises(InvalidProject) as raised:
        comparisons.prepare_arm(store, group.id, group.session, 1, 'a')
    assert str(raised.value) == 'Preparation incomplete The failed attempt could not be recorded: disk full'
    assert store.read(group.id, group.session) == group
