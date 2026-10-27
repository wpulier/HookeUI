import copy
import pytest
from orchestrator.day import DayStore
from orchestrator.workspace_views import read, save, save_note


def layout():
    return {'schema':1,'active':'main','views':[{'id':'main','title':'Main','specimen':{'visible':True,'rect':None},'items':[],'notes':[]}]}


def test_views_and_notes_replay_independently_and_keep_revisions(tmp_path):
    day=DayStore(root=tmp_path/'sessions')
    note={'id':'a'+'f'*32,'title':'Observation','text':'Measured in pixels','base':0}
    first=save_note(day,note)
    state=layout();state['views'][0]['notes']=[{'id':first['id'],'rect':[10,10,320,300]}]
    save(day,state,0)
    state['views'][0]['notes']=[]
    save(day,state,1)  # closing a presentation does not delete the note
    save_note(day,{**note,'text':'Calibration is unknown','base':1})
    restarted=DayStore(root=day.root,day=day.sid)
    assert read(restarted)['layout']==state
    assert [r['text'] for r in restarted.annotations[first['id']]]==['Measured in pixels','Calibration is unknown']
    assert restarted.annotations[first['id']][-1]['source']=={'day':day.sid,'scope':'session'}


def test_conflicts_and_failed_writes_never_claim_a_save(tmp_path,monkeypatch):
    day=DayStore(root=tmp_path/'sessions');state=layout()
    save(day,state,0)
    assert save(day,state,0)['revision']==1  # lost reply retry
    changed=copy.deepcopy(state);changed['views'][0]['title']='Other'
    with pytest.raises(ValueError,match='layout changed'):save(day,changed,0)
    def fail(*args,**kwargs):raise OSError('disk full')
    monkeypatch.setattr(day.journal,'append',fail)
    with pytest.raises(OSError):save(day,changed,1)
    assert read(day)['layout']==state
    with pytest.raises(OSError):save_note(day,{'id':'a'+'e'*32,'title':'Note','text':'Kept draft','base':0})
    assert not day.annotations


def test_note_conflict_idempotency_and_foreign_reference_refusal(tmp_path):
    day=DayStore(root=tmp_path/'sessions')
    note={'id':'a'+'e'*32,'title':'Note','text':'First','base':0}
    assert save_note(day,note)['version']==1
    assert save_note(day,note)['version']==1
    with pytest.raises(ValueError,match='changed elsewhere'):save_note(day,{**note,'text':'Stale'})
    state=layout();state['views'][0]['items']=[{'resource':{'day':'2000-01-01','id':'e1','version':0,'picture':0},'title':'Foreign','pan':[.5,.5],'zoom':1}]
    with pytest.raises(ValueError,match='belong'):save(day,state,0)
    assert read(day)['revision']==0


def test_layout_does_not_accept_scientific_payloads(tmp_path):
    day=DayStore(root=tmp_path/'sessions');state=layout();state['pixels']=[1,2]
    with pytest.raises(ValueError,match='arrangements only'):save(day,state,0)


def test_nested_resource_payload_is_rejected(tmp_path):
    day=DayStore(root=tmp_path/'sessions');state=layout()
    state['views'][0]['items']=[{'resource':{'day':day.sid,'id':'e1','version':0,'pixels':[1,2]},'title':'Image','pan':[.5,.5],'zoom':1}]
    with pytest.raises(ValueError,match='scientific payloads'):save(day,state,0)


def test_commands_refuse_stale_session_writes(tmp_path,monkeypatch):
    import asyncio
    from orchestrator.commands import handle
    from tests.test_original_picture_analysis import prepare
    from tests.test_sources import Client
    app,*_=prepare(tmp_path,monkeypatch)
    async def go():
        for kind in ('workspace-layout','session-note'):
            client=Client()
            await handle(app,client,{'type':kind,'session':'another-session','layout':layout(),'base':0,
                'action':'save','id':'a'+'c'*32,'title':'Stale','text':'Do not retarget'})
            assert client.replies[-1]['ok'] is False
        assert read(app.day)['revision']==0
        assert not app.day.annotations
    asyncio.run(go())
