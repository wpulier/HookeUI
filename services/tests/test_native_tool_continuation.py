"""Real accepted population/measurement storage; synthetic producer and prepared member."""
import asyncio
import copy

import numpy as np
import pytest

from orchestrator import commands, population, population_native, scientific_measurement, scientific_tools
from orchestrator.scientific_project_review import _resolve
from orchestrator.scientific_projects import Conflict, InvalidProject
from tests.test_original_picture_analysis import prepare
from tests.test_particle_corrections import saved
from tests.test_sources import Client
from tests.test_scientific_tools import member, KEY, GEN  # noqa: F401
from tests.test_scientific_project_api import web, ORIGIN  # noqa: F401


def test_corrected_measured_native_inputs_continue_without_repeating_producer(tmp_path, monkeypatch, member):
    built, worker, aid, _ = prepare(tmp_path, monkeypatch)

    async def go():
        await commands.handle(built, Client(), {'type': 'source', 'open': aid})
        result = await population.segment(built.hub, choice='cellpose-sam')
        await saved(result)
        population.correct(built.hub, result, 'remove', [next(m.id for m in result.live() if m.box[0] == 70)], 'user')
        await saved(result)
        kept = (await population_native.run(built.hub, built.day, population_native.OPERATION,
            {'session': result.session, 'id': result.id, 'revision': 1, 'request': 'native-tool-source-01'}, caller='studio'))['project']
        measured = (await scientific_measurement.run(built.hub, built.day, scientific_measurement.OPERATION,
            {'project': kept.id, 'session': kept.session, 'revision': kept.revision, 'input': 'labels'}, caller='studio'))['project']
        store = built.hub.resources.projects
        original = copy.deepcopy(measured.document)
        chosen = scientific_tools.bind(store, measured.id, measured.session, measured.revision, KEY, GEN)
        assert chosen.id == measured.id and chosen.session == measured.session and chosen.revision == measured.revision + 1
        assert chosen.document['kind'] == 'scene'
        assert chosen.document['resources'] == original['resources']
        assert chosen.document['state']['populationSource'] == original['state']['populationSource']
        assert chosen.document['state']['scientificMeasurements'] == original['state']['scientificMeasurements']
        assert store.read(measured.id, measured.session, revision=measured.revision).document == original
        _, runtime, inputs = _resolve(store, chosen.id, chosen.session, chosen.revision)
        assert runtime == {'bundle': 'mine/next-tool', 'operation': 'viewer', 'generation': GEN}
        assert set(inputs) == {'image', 'labels'}
        labels = inputs['labels'].read((slice(0,96), slice(0,96)))
        assert labels.dtype == np.dtype('<u2') and set(np.unique(labels)) == {0,2,3}
        assert np.count_nonzero(labels == 2) == 317 and np.count_nonzero(labels == 3) == 149
        assert inputs['labels'].descriptor['calibration'] == inputs['image'].descriptor['calibration']
        assert scientific_tools.bind(store, measured.id, measured.session, measured.revision, KEY, GEN) == chosen
        assert len(worker.inputs) == 1

    asyncio.run(go())


def native_data(store, *, session='native-owner'):
    from tests.test_scientific_project_review import saved_view
    retained, _, _ = saved_view(store, session=session)
    document=retained.document
    document['kind']='population-labels'
    document['state']['scientificView'].pop('runtime')
    return store.save(retained.id,retained.session,document,expected_revision=retained.revision)


@pytest.mark.parametrize('refusal', ['stale', 'owner', 'unaccepted-input', 'unknown-view-field', 'cancelled'])
def test_native_tool_choice_refuses_wrong_or_changed_capture(tmp_path, member, refusal):
    from orchestrator.scientific_projects import ProjectStore
    store=ProjectStore(tmp_path);source=native_data(store)
    document=source.document
    if refusal=='stale':
        store.save(source.id,source.session,{**document,'title':'Other accepted change'},expected_revision=source.revision)
    if refusal in {'unaccepted-input','unknown-view-field'}:
        if refusal=='unaccepted-input': document['resources']=[]
        else: document['state']['scientificView']['unexpected']=True
        source=store.save(source.id,source.session,document,expected_revision=source.revision)
    before=store.read(source.id,source.session)
    def check():
        if refusal=='cancelled': raise RuntimeError('Stopped before publication')
    with pytest.raises((InvalidProject,Conflict,RuntimeError)):
        scientific_tools.bind(store,source.id,'another-owner' if refusal=='owner' else source.session,
                              source.revision,KEY,GEN,check=check)
    assert store.read(source.id,source.session)==before


def test_native_data_cannot_open_without_explicit_runtime_selection(tmp_path):
    from orchestrator.scientific_projects import ProjectStore
    store=ProjectStore(tmp_path);source=native_data(store)
    with pytest.raises(InvalidProject,match='declared scientific view'):
        _resolve(store,source.id,source.session,source.revision)


def test_native_discovery_and_choice_share_existing_http_ownership(web, member, monkeypatch):
    client,built=web;store=built.hub.resources.projects
    source=native_data(store,session=built.day.sid)
    monkeypatch.setattr(scientific_tools.catalog,'manifests',lambda:{})
    monkeypatch.setattr(scientific_tools.catalog,'mine',lambda environ:{})
    path=f'/api/scientific-projects/{source.id}'
    discovered=client.get(path+'/tools',params={'session':source.session,'revision':source.revision})
    assert discovered.status_code==200,discovered.text
    assert discovered.json()['source']=={'id':source.id,'session':source.session,'revision':source.revision,'digest':source.digest}
    assert discovered.json()['tools']==[]
    assert discovered.json()['comparison']=={'available':False,'reason':'Choose an interactive tool first; compare alternatives from its saved scene.'}
    body={'session':source.session,'revision':source.revision,'capability':KEY,'generation':GEN}
    assert client.post(path+'/tool',json=body).status_code==403
    headers={'Origin':ORIGIN,'X-MicroscopeMind':'choose-scientific-tool'}
    bound=client.post(path+'/tool',json=body,headers=headers)
    assert bound.status_code==200,bound.text
    assert bound.json()['project']['document']['kind']=='scene'
    assert client.get(path+'/tools',params={'session':source.session,'revision':source.revision+1}).json()['comparison']=={'available':True,'reason':''}
    assert client.post(path+'/tool',json=body,headers=headers).json()==bound.json()
    assert store.read(source.id,source.session,revision=source.revision)==source


def test_comparison_refuses_runtime_free_input_before_retaining_a_dead_end(tmp_path, monkeypatch):
    from orchestrator import scientific_comparisons
    from orchestrator.scientific_projects import ProjectStore
    store=ProjectStore(tmp_path);source=native_data(store)
    monkeypatch.setattr(scientific_tools,'discover',lambda *a,**kw:{'tools':[{'key':KEY,'generation':GEN,'owner':'mine/next-tool','name':'Next tool'}]})
    before=store.list(source.session)
    with pytest.raises(InvalidProject,match='Choose an interactive tool first'):
        scientific_comparisons.create(store,source.id,source.session,source.revision,[KEY,KEY],'c'*32)
    assert store.list(source.session)==before
