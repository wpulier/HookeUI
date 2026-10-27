"""Actual retained storage; synthetic workers, not full H02 qualification."""
import pytest

from orchestrator.scientific_history import ScientificHistory, HistoryUnavailable
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_saves import LayerSave, SaveConflict
from tests.test_scientific_edits import scene
from tests.test_scientific_saves import setup, Worker


def test_two_kernels_keep_distinct_producing_histories_through_conflict_copy_and_restart(scene):
    store,saved,shown,data=setup(scene)
    histories=[]
    for kernel,code in [('first-kernel','labels.data[:] = 11'),('second-kernel','labels.data[:] = 22')]:
        h=ScientificHistory(store,saved,kernel,tab='tab',placement='place')
        h.begin('cell-1234567890123456',code)
        h.finish('cell-1234567890123456',{'format':'scientific-console-v1','sequence':1,'stdout':'','stderr':'','result':'','error':'','truncated':False,'privatePreview':True})
        histories.append(h)
    first=LayerSave(store,saved,'save-first-1234567890',shown,'d'*32,producer=histories[0].producer())
    second=LayerSave(store,saved,'save-second-123456789',shown,'d'*32,producer=histories[1].producer())
    one=data.copy();one[:]=11
    two=data.copy();two[:]=22
    first.capture(Worker(one));second.capture(Worker(two))
    first.publish()
    with pytest.raises(SaveConflict):second.publish()
    copied=second.copy(None,None)['resource']
    reopened=ProjectStore(store.root)
    for record,history in [(reopened.read(saved.id,saved.session),histories[0]),(reopened.read(copied['id'],saved.session),histories[1])]:
        p=record.document['state']['hookeEdit']
        assert p['base']=={'session':saved.session,'id':saved.id,'revision':saved.revision,'digest':saved.digest}
        assert p['input']==saved.document['resources'][0]
        assert p['output']==record.document['resources'][0]
        assert p['producer']['kernel']==history.document['state']['kernel']
        ref=p['producer']['history']
        actual=reopened.read(ref['id'],ref['session'],revision=ref['revision'])
        assert actual.digest==ref['digest'] and actual.document==history.document
    assert reopened.read(saved.id,saved.session,revision=saved.revision)==saved


def test_pending_history_blocks_save_provenance_and_retry_freezes_it(scene,monkeypatch):
    store,saved,shown,data=setup(scene)
    history=ScientificHistory(store,saved,'kernel-one',tab='tab',placement='place')
    with monkeypatch.context() as patch:
        patch.setattr(store,'create',lambda *a,**kw: (_ for _ in ()).throw(OSError('disk')))
        with pytest.raises(HistoryUnavailable):history.begin('cell-1234567890123456','x=1')
    with pytest.raises(HistoryUnavailable):history.producer()
    history.retry_save()
    assert history.document['state']['cells'][0]['state']=='not-dispatched'
    producer=history.producer()
    save=LayerSave(store,saved,'save-1234567890123456',shown,'d'*32,producer=producer)
    save.capture(Worker(data))
    producer['kernel']='forged-after-admission'
    original_save=store.save
    def lost_ack(*a,**kw):original_save(*a,**kw);raise OSError('lost acknowledgement')
    with monkeypatch.context() as patch:
        patch.setattr(store,'save',lost_ack)
        with pytest.raises(Exception,match='could not be confirmed'):save.publish()
    save.publish()
    result=store.read(saved.id,saved.session)
    assert result.document['state']['hookeEdit']['producer']['kernel']=='kernel-one'
    assert result.revision==saved.revision+1


def test_manual_actions_are_durable_and_duplicate_is_not_dispatched_again(scene):
    store,saved,_,_=setup(scene)
    history=ScientificHistory(store,saved,'manual-kernel',tab='tab',placement='place')
    request={'op':'labels','request':'gesture-123456789012','tool':'paint','label':'9007199254740993','points':[[10,20]]}
    context={'dimensions':{'order':[0,1]},'camera':{'zoom':2}}
    assert history.begin_action(request['request'],request,context)
    history.finish_action(request['request'],{'changed':True,'state':'applied'})
    assert not history.begin_action(request['request'],request,context)
    request['label']='changed'
    record=ProjectStore(store.root).read(history.record.id,saved.session)
    action=record.document['state']['actions'][0]
    assert action['request']['label']=='9007199254740993' and action['context']==context
    assert action['outcome']['changed'] is True
    assert history.producer()['history']['digest']==record.digest


def test_cloud_source_and_reviewed_conversion_cannot_be_rewritten(tmp_path):
    import copy
    from orchestrator.scientific_provenance import guard
    from orchestrator.scientific_projects import InvalidProject
    store=ProjectStore(tmp_path/'projects')
    state={'cloudSource':{'selection':{'id':'original','revision':2,'digest':'a'*64},
                         'conversion':{'kind':'linear-uint8-plane','inputRange':[0,65535]}}}
    saved=store.create('2026-10-23',{'kind':'scene','title':'Retained regional input','resources':[], 'runtimes':[], 'state':state})
    guard(saved,copy.deepcopy(saved.document))
    changed=copy.deepcopy(saved.document)
    changed['state']['cloudSource']['conversion']['inputRange']=[0,255]
    with pytest.raises(InvalidProject): guard(saved,changed)
    changed=copy.deepcopy(saved.document); del changed['state']['cloudSource']
    with pytest.raises(InvalidProject): guard(saved,changed)
