"""Real immutable scene storage and HTTP, with synthetic prepared integrations."""
from contextlib import contextmanager
from types import SimpleNamespace
import numpy as np
import pytest
from gpu_worker.bundle_contract import parse_members, view
from orchestrator import scientific_tools as tools
from orchestrator.scientific_projects import Conflict, InvalidProject, ProjectStore
from tests.test_scientific_project_review import saved_view
from tests.test_scientific_edits import scene  # noqa: F401
from tests.test_scientific_project_api import web, ORIGIN  # noqa: F401

OWNER = SimpleNamespace(key='mine/next-tool', runner='python-env', requires={'catalog': 3})
KEY = 'mine/next-tool#viewer'
GEN = 'b' * 16
RAW = {'id': 'viewer', 'name': 'Next tool', 'adapter': 'adapter:open_host', 'bridge': 'scientific-host-v1',
       'inputs': [{'id': 'scene', 'type': 'project.scientific'}], 'outputs': [{'id': 'result', 'type': 'project.scientific'}]}

@pytest.fixture
def member(monkeypatch):
    parsed = parse_members([RAW], OWNER)[0]
    owner = SimpleNamespace(**vars(OWNER), members=(RAW,))
    monkeypatch.setattr(tools.env_runner, 'in_use', lambda *a: None)
    monkeypatch.setattr(tools.env_runner, 'generation', lambda *a: None)
    @contextmanager
    def prepared(key, generation, environ):
        if key != KEY or generation != GEN:
            raise ValueError('Wrong prepared generation')
        yield SimpleNamespace(m=view(owner, parsed))
    monkeypatch.setattr(tools.prepared_bundles, 'prepared_member', prepared)
    return parsed


def test_choice_retains_exact_values_and_previous_runtime_without_new_array_bytes(tmp_path, member):
    store = ProjectStore(tmp_path)
    saved, data, ref = saved_view(store)
    before = sorted((p.name, p.stat().st_size) for p in (tmp_path / saved.id / 'arrays').iterdir())
    chosen = tools.bind(store, saved.id, saved.session, saved.revision, KEY, GEN)
    assert chosen.id == saved.id and chosen.revision == saved.revision + 1
    assert store.read(saved.id, saved.session, revision=saved.revision) == saved
    assert chosen.document['resources'] == saved.document['resources']
    assert chosen.document['runtimes'][0] == saved.document['runtimes'][0]
    view = chosen.document['state']['scientificView']
    assert view['runtime'] == {'bundle': 'mine/next-tool', 'generation': GEN, 'operation': 'viewer'}
    assert view['inputs'] == saved.document['state']['scientificView']['inputs']
    assert before == sorted((p.name, p.stat().st_size) for p in (tmp_path / saved.id / 'arrays').iterdir())
    assert np.array_equal(store.array_source(saved.id, saved.session, chosen.revision, ref).read(tuple(slice(0, n) for n in data.shape)), data)
    assert tools.bind(store, saved.id, saved.session, saved.revision, KEY, GEN) == chosen
    assert chosen.document['state']['scientificToolChoice']['source']['digest'] == saved.digest


def test_concurrent_edit_refuses_choice_without_overwriting_or_rebasing(tmp_path, member):
    store = ProjectStore(tmp_path); saved, _, _ = saved_view(store)
    newer = store.save(saved.id, saved.session, {**saved.document, 'title': 'Other accepted work'}, expected_revision=saved.revision)
    with pytest.raises(Conflict):
        tools.bind(store, saved.id, saved.session, saved.revision, KEY, GEN)
    assert store.read(saved.id, saved.session) == newer


@pytest.fixture
def two_tools(member, monkeypatch):
    """The fixture's tool and a second one, as Cellpose-SAM and napari-animation for one retained input (final052)."""
    second = {**RAW, 'id': 'animate', 'name': 'Second tool'}
    owner = SimpleNamespace(**vars(OWNER), members=(RAW, second))
    parsed = {member.id: member, 'animate': parse_members([second], OWNER)[0]}
    @contextmanager
    def prepared(key, generation, environ):
        if key not in (KEY, SECOND) or generation != GEN:
            raise ValueError('Wrong prepared generation')
        yield SimpleNamespace(m=view(owner, parsed[key.split('#')[1]]))
    monkeypatch.setattr(tools.prepared_bundles, 'prepared_member', prepared)


SECOND = 'mine/next-tool#animate'


def test_a_second_tool_chosen_from_the_same_input_revision_follows_the_first(tmp_path, two_tools):
    # final052: Cellpose was chosen from the retained image and labels; Continue with napari-animation in the same window
    # then refused "This project changed" and nothing opened.
    from orchestrator.scientific_provenance import resolve
    store = ProjectStore(tmp_path); saved, _, ref = saved_view(store)
    first = tools.bind(store, saved.id, saved.session, saved.revision, KEY, GEN)
    second = tools.bind(store, saved.id, saved.session, saved.revision, SECOND, GEN)
    assert second.revision == first.revision + 1 and store.read(saved.id, saved.session) == second
    assert store.read(saved.id, saved.session, revision=first.revision) == first
    view = second.document['state']['scientificView']
    assert view['runtime'] == {'bundle': 'mine/next-tool', 'generation': GEN, 'operation': 'animate'}
    assert view['inputs'] == saved.document['state']['scientificView']['inputs']
    assert second.document['state']['scientificToolChoice']['source'] == {
        'id': saved.id, 'session': saved.session, 'revision': first.revision, 'digest': first.digest}
    explained = resolve(store, saved.id, saved.session, second.revision)
    assert explained['problems'] == [] and explained['inputSelection']['capability'] == SECOND
    # A retried request is already the latest choice: nothing new is saved.
    assert tools.bind(store, saved.id, saved.session, saved.revision, SECOND, GEN) == second
    assert store.read(saved.id, saved.session) == second


def test_a_choice_never_follows_later_work_that_changed_the_input(tmp_path, two_tools):
    store = ProjectStore(tmp_path); saved, _, _ = saved_view(store)
    first = tools.bind(store, saved.id, saved.session, saved.revision, KEY, GEN)
    edited = store.save(saved.id, saved.session, {**first.document, 'title': 'Other accepted work'}, expected_revision=first.revision)
    with pytest.raises(Conflict):
        tools.bind(store, saved.id, saved.session, saved.revision, SECOND, GEN)
    assert store.read(saved.id, saved.session) == edited


def test_cancellation_or_unprepared_generation_leaves_all_saved_work_unchanged(tmp_path, member):
    store = ProjectStore(tmp_path); saved, _, _ = saved_view(store)
    with pytest.raises(ValueError, match='Wrong prepared'):
        tools.bind(store, saved.id, saved.session, saved.revision, KEY, 'c' * 16)
    with pytest.raises(RuntimeError, match='Stopped'):
        tools.bind(store, saved.id, saved.session, saved.revision, KEY, GEN, check=lambda: (_ for _ in ()).throw(RuntimeError('Stopped')))
    assert store.read(saved.id, saved.session) == saved


def test_declared_scene_compatibility_excludes_readers_and_parameterized_or_other_hosts(member):
    assert tools.accepts(member)
    candidate = SimpleNamespace(bridge='other', reader={}, data_inputs=member.data_inputs, params=())
    assert not tools.accepts(candidate)
    candidate.bridge = 'scientific-host-v1'
    candidate.reader = {'input': 'selected'}
    assert not tools.accepts(candidate)



def test_discovery_is_metadata_only_and_preserves_missing_preparation(tmp_path, member, monkeypatch):
    store = ProjectStore(tmp_path / 'projects'); saved, _, _ = saved_view(store)
    owner = SimpleNamespace(key='mine/next-tool', runner='python-env', members=(RAW,), requires={'catalog': 3})
    monkeypatch.setattr(tools.catalog, 'manifests', lambda: {})
    monkeypatch.setattr(tools.catalog, 'mine', lambda environ: {owner.key: owner})
    monkeypatch.setattr(tools.envs, 'generation', lambda *args: GEN)
    monkeypatch.setattr(tools.envs, 'root', lambda *args: tmp_path / 'envs')
    monkeypatch.setattr(tools.readiness, 'misfit', lambda *args: None)
    monkeypatch.setattr(tools.channels, 'trusted', lambda *args: True)
    row = tools.discover(store, saved.id, saved.session, saved.revision)['tools'][0]
    assert not row['canReview'] and 'Prepare' in row['reason']
    (tmp_path / 'envs' / GEN).mkdir(parents=True)
    (tmp_path / 'envs' / GEN / tools.envs.RECEIPT).write_text('{}')
    ready = tools.discover(store, saved.id, saved.session, saved.revision)['tools'][0]
    assert ready['canReview'] and ready['reason'] == ''
    monkeypatch.setattr(tools.channels, 'trusted', lambda *args: False)
    assert 'Allow' in tools.discover(store, saved.id, saved.session, saved.revision)['tools'][0]['reason']


def test_http_choice_requires_studio_origin_and_current_project(web, member):
    client, built = web
    store = built.hub.resources.projects
    saved, _, _ = saved_view(store, session='2026-10-18.999')
    path = f'/api/scientific-projects/{saved.id}/tool'
    body = {'session': saved.session, 'revision': saved.revision, 'capability': KEY, 'generation': GEN}
    headers = {'Origin': ORIGIN, 'X-MicroscopeMind': 'choose-scientific-tool'}
    assert client.post(path, json=body).status_code == 403
    assert client.post(path, headers=headers, json=body).status_code == 409
    # The active session is explicit; no test retargets a source or resource.
    built.day.sid = saved.session
    result = client.post(path, headers=headers, json=body)
    assert result.status_code == 200, result.text
    assert result.json()['project']['revision'] == saved.revision + 1
    assert client.post(path, headers=headers, json=body).json() == result.json()


def test_next_tool_keeps_native_layers_but_never_restores_another_authors_codec(scene, member):
    from tests.test_scientific_outputs import attempt
    from orchestrator.scientific_project_review import _resolve
    store, _, owner, worker = attempt(scene)
    try:
        owner.capture(worker)
        owner.publish()
        output = store.read(owner.target, owner.saved.session)
        document = output.document
        original_view = document['state']['scientificView']
        original_view['tool'] = {'document': {'format': 'scientific-tool-document-v1', 'schema': 'another.author', 'version': 1, 'state': {'keyframes': [1, 2]}},
                                 'bindings': {'original_layer': 'output'}}
        saved = store.save(output.id, output.session, document, expected_revision=output.revision)
        chosen = tools.bind(store, saved.id, saved.session, saved.revision, KEY, GEN)
        view = chosen.document['state']['scientificView']
        assert view['layers'] == original_view['layers']
        assert 'tool' not in view and view['initializeTool'] == 1
        assert store.read(saved.id, saved.session, revision=saved.revision).document['state']['scientificView']['tool'] == original_view['tool']
        _, _, sources = _resolve(store, chosen.id, chosen.session, chosen.revision)
        assert np.array_equal(sources['output'].read((slice(0, 5), slice(0, 7))), worker.data)
    finally:
        owner.close()


def test_same_runtime_preserves_its_own_codec(scene, member):
    from tests.test_scientific_outputs import attempt
    store, _, owner, worker = attempt(scene)
    try:
        owner.capture(worker); owner.publish()
        output = store.read(owner.target, owner.saved.session)
        document = output.document
        runtime = {'bundle': 'mine/next-tool', 'generation': GEN, 'operation': 'viewer'}
        document['runtimes'].append(runtime)
        view = document['state']['scientificView']
        view['runtime'] = runtime
        view['tool'] = {'document': {'format': 'scientific-tool-document-v1', 'schema': 'same.author', 'version': 1, 'state': {'keyframes': [1, 2]}}, 'bindings': {'original_layer': 'output'}}
        saved = store.save(output.id, output.session, document, expected_revision=output.revision)
        chosen = tools.bind(store, saved.id, saved.session, saved.revision, KEY, GEN)
        assert chosen.document['state']['scientificView'] == view
    finally:
        owner.close()


@pytest.mark.parametrize('initialize', [None, 1])
def test_worker_initializes_only_explicit_next_tool_around_restored_layers(monkeypatch, initialize):
    import sys
    from env_worker import scientific_host as host, scientific_outputs, scientific_tool_document
    layer = object()
    viewer = SimpleNamespace(layers=[layer], close=lambda: None)
    empty = lambda *args, **kwargs: SimpleNamespace(close=lambda: None, seal=lambda: None, bind=lambda **kw: None)
    monkeypatch.setitem(sys.modules, 'napari', SimpleNamespace(Viewer=lambda **kwargs: viewer))
    monkeypatch.setattr(host.napari_cgl, 'install', empty)
    monkeypatch.setattr(host.scientific_console, 'Console', empty)
    monkeypatch.setattr(host.scientific_layer_exports, 'EditableLayers', empty)
    monkeypatch.setattr(host.scientific_view_bindings, 'ViewBindings', lambda *args: SimpleNamespace(close=lambda: None, seal=lambda: None, bind=lambda **kw: None, key_for=lambda layer: 'retained'))
    monkeypatch.setattr(scientific_outputs, 'restore', empty)
    monkeypatch.setattr(scientific_tool_document, 'scientific_identity', lambda viewer: ['exact original values'])
    monkeypatch.setattr(host, 'Panel', lambda *args: SimpleNamespace(snapshot=lambda: None, close=lambda: None))
    monkeypatch.setattr(host.ReviewSession, '_view_events', lambda self: [])
    calls = []
    def factory(context):
        calls.append(context)
        assert context.restored_layers['retained'] is layer
        assert context.viewer is viewer
        return empty()
    session = host.ReviewSession(factory, {}, retained_layers=[], initialize_tool=initialize)
    session.close()
    assert len(calls) == (1 if initialize else 0)


@pytest.mark.parametrize('version', [True, False, 0, 2, '1'])
def test_worker_rejects_unknown_initialization_before_renderer_or_code(version):
    from env_worker.scientific_host import ReviewSession
    with pytest.raises(ValueError, match='Unsupported tool initialization'):
        ReviewSession(lambda context: pytest.fail('must not execute'), {}, retained_layers=[], initialize_tool=version)


@pytest.mark.parametrize('mutation', [False, True])
def test_recipe_configures_once_without_running_or_mutating_inputs(monkeypatch, mutation):
    import sys
    from env_worker import scientific_host as host, scientific_outputs, scientific_tool_document
    layer = object(); values = ['original']; calls = []; settings = {'threshold': 0.4}
    viewer = SimpleNamespace(layers=[layer], close=lambda: None)
    empty = lambda *a, **kw: SimpleNamespace(close=lambda: None, seal=lambda: None, bind=lambda **kw: None)
    monkeypatch.setitem(sys.modules, 'napari', SimpleNamespace(Viewer=lambda **kw: viewer))
    monkeypatch.setattr(host.napari_cgl, 'install', empty)
    monkeypatch.setattr(host.scientific_console, 'Console', empty)
    monkeypatch.setattr(host.scientific_layer_exports, 'EditableLayers', empty)
    monkeypatch.setattr(host.scientific_view_bindings, 'ViewBindings', lambda *a: SimpleNamespace(close=lambda: None, seal=lambda: None, bind=lambda **kw: None, key_for=lambda layer: 'input'))
    monkeypatch.setattr(scientific_outputs, 'restore', empty)
    monkeypatch.setattr(scientific_tool_document, 'scientific_identity', lambda viewer: list(values))
    monkeypatch.setattr(host, 'Panel', lambda *a: SimpleNamespace(snapshot=lambda: None, close=lambda: None))
    monkeypatch.setattr(host.ReviewSession, '_view_events', lambda self: [])
    def factory(context):
        def configure(value):
            calls.append(dict(value)); settings.update(value)
            if mutation: values.append('changed')
        context.bind_recipe(schema='test.parameters', version=1, capture=lambda: settings, configure=configure)
        return empty()
    recipe = {'format':'scientific-tool-document-v1','schema':'test.parameters','version':1,'state':{'threshold':0.7}}
    if mutation:
        with pytest.raises(ValueError, match='changed saved scientific layers'):
            host.ReviewSession(factory, {}, retained_layers=[], initialize_tool=1, recipe_tool=recipe)
    else:
        session = host.ReviewSession(factory, {}, retained_layers=[], initialize_tool=1, recipe_tool=recipe)
        assert session.recipe_binding.capture() == recipe
        session.close()
    assert calls == [{'threshold':0.7}]


def test_prepared_update_requires_explicit_current_candidate_trial(tmp_path, member, monkeypatch):
    from orchestrator.maintenance import workflow
    store=ProjectStore(tmp_path);saved,_,_=saved_view(store)
    active=SimpleNamespace(folder=tmp_path/('a'*16),m=OWNER)
    monkeypatch.setattr(tools.env_runner,'in_use',lambda *a:active)
    with pytest.raises(InvalidProject,match='selected local tool'):
        tools.bind(store,saved.id,saved.session,saved.revision,KEY,GEN)
    calls=[]
    def current(owner,generation,environ):
        calls.append(generation)
        if generation!=GEN:raise ValueError('candidate changed')
    monkeypatch.setattr(workflow,'target',current)
    chosen=tools.bind(store,saved.id,saved.session,saved.revision,KEY,GEN,update_trial=True)
    assert calls==[GEN,GEN] and chosen.document['state']['scientificToolChoice']['updateTrial'] is True
    assert store.read(saved.id,saved.session,revision=saved.revision)==saved
