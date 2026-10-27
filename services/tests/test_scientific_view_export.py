"""Tiny synthetic native frames and real PNG/file retention; no renderer qualification."""
import asyncio
import copy
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from orchestrator import scientific_view_export as exports
from orchestrator.scientific_projects import ProjectStore, InvalidProject
from tests.test_scientific_reviews import ReviewsRig
from tests.test_scientific_host_worker import frame


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path/'catalog'))
    store=ProjectStore(tmp_path/'projects')
    runtime={'bundle':'example','generation':'a'*16,'operation':'viewer'}
    saved=store.create('2026-10-18', {'kind':'scene','title':'synthetic view','resources':[], 'runtimes':[runtime],
        'state':{'scientificView':{'runtime':runtime,'inputs':{'image':{'synthetic':'admission tested separately'}},'layers':[]}}})
    monkeypatch.setattr(exports,'renderer',lambda runtime,env:{'entry':runtime['bundle'],'generation':runtime['generation'],'version':'1' if runtime['generation']=='a'*16 else '2'})
    pixels=bytes(range(24))
    return store,saved,frame()['frame'],pixels


def test_current_region_exact_png_identity_and_immutable_retry(case):
    store,saved,shown,pixels=case
    region={'x':1,'y':1,'width':1,'height':2}
    kept=exports.publish(store,saved,shown,pixels,'first-region-123456','selected-region',region)
    provenance=kept.document['state']['viewExport']
    assert provenance['source']==exports.reference(saved)
    assert provenance['dimensions']['steps']==[0,0] and provenance['preferences']['camera']==shown['camera']
    source=store.file_source(kept.id,kept.session,1,kept.document['state']['sourceFile']['reference'])
    actual=cv2.imdecode(np.frombuffer(source.read(0,source.size),np.uint8),cv2.IMREAD_COLOR)[...,::-1]
    expected=np.frombuffer(pixels,np.uint8).reshape(3,2,4)[1:3,1:2,:3]
    np.testing.assert_array_equal(actual,expected)
    assert exports.publish(store,saved,shown,pixels,'first-region-123456','selected-region',region)==kept
    with pytest.raises(InvalidProject,match='different captured view'):
        exports.publish(store,saved,shown,pixels,'first-region-123456','current-view')
    for changed in ({**shown, 'frame':'b'*64}, {**shown, 'width':3}):
        with pytest.raises(InvalidProject,match='different captured view'):
            exports.publish(store,saved,changed,pixels,'first-region-123456','selected-region',region)
    assert store.read(saved.id,saved.session)==saved


def test_reexport_fixed_generation_preserves_original_and_requires_exact_view(case,monkeypatch):
    store,saved,shown,pixels=case
    from orchestrator.maintenance import revocations
    monkeypatch.setattr(exports.catalog,'get',lambda _:SimpleNamespace())
    monkeypatch.setattr(revocations,'fixes',lambda *a:[{'versions':['1'],'fixedIn':'2','issue':'synthetic-fixture'}])
    original=exports.publish(store,saved,shown,pixels,'original-view-123456','current-view')
    original_record=(exports.folder(store,saved)/'original-view-123456'/'receipt.json').read_bytes()
    doc=saved.document; doc['runtimes'][0]['generation']='b'*16;doc['state']['scientificView']['runtime']['generation']='b'*16
    newer=store.save(saved.id,saved.session,doc,expected_revision=1)
    changed=copy.deepcopy(shown);changed['camera']['zoom']=2
    with pytest.raises(InvalidProject,match='Re-export pending'):
        exports.publish(store,newer,changed,pixels,'new-fixed-view-123456','current-view',previous='original-view-123456')
    fixed=exports.publish(store,newer,shown,pixels,'new-fixed-view-123456','current-view',previous='original-view-123456')
    assert fixed.id!=original.id and fixed.document['state']['viewExport']['renderer']['generation']=='b'*16
    assert (exports.folder(store,saved)/'original-view-123456'/'receipt.json').read_bytes()==original_record
    assert next(row for row in exports.listing(store,newer)['items'] if row['request']=='original-view-123456')['affected']


def test_pixel_failure_is_kept_not_published(case,monkeypatch):
    store,saved,shown,pixels=case
    # Alter only encoder readback: a successful write does not prove fidelity.
    original=cv2.imread
    monkeypatch.setattr(cv2,'imread',lambda *a,**k:255-original(*a,**k))
    with pytest.raises(InvalidProject,match='verification failed'):
        exports.publish(store,saved,shown,pixels,'failed-view-123456','current-view')
    assert store.list(saved.session)==[saved]
    assert exports.record(store,saved,'failed-view-123456')['ok'] is False


def test_capture_refuses_stale_cross_owner_and_unsaved_data(tmp_path):
    rig=ReviewsRig(tmp_path)
    async def go():
        try:
            manager=rig.manager;identity=await manager.open(rig.origin)
            await manager.request(rig.client,identity,{'op':'render','width':2,'height':3},snapshot=rig.pin())
            rig.paint(identity)
            owner=manager._owners[identity]
            for client,token,dirty in [(object(),'a'*64,False),(rig.client,'stale',False),(rig.client,'a'*64,True)]:
                owner.export_data_changed=dirty
                with pytest.raises((InvalidProject,ValueError)):
                    await exports.capture(manager,client,identity,token,'draw-one',rig.pin())
            owner.export_data_changed=False
            # Switch frame during the awaited source read/check: never publish a newest replacement.
            check=manager._check
            count=0
            async def switching(*a,**kw):
                nonlocal count
                await check(*a,**kw);count+=1
                if count==2:owner.frame=(copy.deepcopy(owner.frame[0]),owner.frame[1])
            manager._check=switching
            with pytest.raises(InvalidProject,match='changed while capturing'):
                await exports.capture(manager,rig.client,identity,'a'*64,'draw-one',rig.pin())
        finally:await rig.manager.close()
    asyncio.run(go())


def test_only_declared_presentation_changes_leave_saved_export_admissible():
    assert not exports.changes_data({'op':'change','request':{'operation':'dimensions'}})
    assert not exports.changes_data({'op':'change','request':{'operation':'restore-view'}})
    for request in [{'op':'labels'},{'op':'python'},{'op':'native-panel-change'},{'op':'change','request':{'operation':'controls'}},{'op':'change','request':{'operation':'annotate'}},{'op':'future-action'}]:
        assert exports.changes_data(request)
