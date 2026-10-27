"""Accepted-file range exchange and ordinary scientific-library access."""
import hashlib,io,threading
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pytest
from orchestrator.scientific_projects import ProjectStore
from orchestrator.source_files import SourceFileInputs
from env_worker import source_files as wire
from tests.test_scientific_arrays import project,accept
from tests.test_scientific_files import stage
from tests.waiting import HANG_S


def retained(tmp_path,body):
    store=ProjectStore(tmp_path);owner=project(store);writer,ref=stage(store,owner,body)
    saved=accept(store,owner,ref);writer.close()
    return store.file_source(saved.id,saved.session,saved.revision,ref)


def test_npy_reader_uses_exact_native_values_without_a_host_path(tmp_path):
    values=np.arange(60,dtype='>u2').reshape(3,4,5);stream=io.BytesIO();np.save(stream,values,allow_pickle=False)
    source=retained(tmp_path,stream.getvalue());grants=SourceFileInputs({'specimen':source})
    specs=grants.specifications();assert str(tmp_path) not in repr(specs)
    files=wire.bind(specs,grants.answer);actual=np.load(files['specimen'],allow_pickle=False)
    assert actual.dtype.str=='>u2' and actual.tobytes()==values.tobytes()
    assert files['specimen'].reference==source.reference
    files['specimen'].seek(0);assert files['specimen'].read(6)==b'\x93NUMPY'
    grants.close()
    with pytest.raises(ValueError,match='closed'):files['specimen'].read(1)


def test_reads_stay_bounded_and_do_not_invent_a_whole_file(tmp_path,monkeypatch):
    source=retained(tmp_path,b'abcdefghij');grants=SourceFileInputs({'x':source});files=wire.bind(grants.specifications(),grants.answer);f=files['x']
    monkeypatch.setattr(wire,'MAX_RANGE',4)
    with pytest.raises(ValueError):f.read()
    assert f.tell()==0
    with pytest.raises(ValueError):f.seek(-1)
    with pytest.raises(ValueError):f.seek(11)
    with pytest.raises(ValueError):f.read(True)
    assert f.read(4)==b'abcd'
    f.seek(-2,io.SEEK_END);assert f.read(4)==b'ij'
    assert f.read()==b''
    f.close()
    with pytest.raises(ValueError):f.seek(0)


@pytest.mark.parametrize('change',[{'token':'unknown'},{'offset':True},{'offset':-1},{'length':True},
                                  {'length':11},{'extra':1},{'op':'open-file'},{'token':[]}])
def test_malformed_reads_do_not_reach_source(tmp_path,change):
    source=retained(tmp_path,b'abcdefghij');calls=[]
    class Observed(type(source)):
        def read(self,*args):calls.append(args);return super().read(*args)
    watched=Observed(**vars(source));grants=SourceFileInputs({'x':watched});token=grants.specifications()['x']['token']
    with pytest.raises(ValueError):grants.answer({'op':'read-source-file','token':token,'offset':0,'length':4,**change})
    assert not calls


def test_shared_budget_and_malformed_reply_do_not_advance_cursor(tmp_path,monkeypatch):
    source=retained(tmp_path,b'abcdef');grants=SourceFileInputs({'a':source,'b':source});files=wire.bind(grants.specifications(),grants.answer)
    monkeypatch.setattr(wire,'MAX_READ_BYTES',5)
    assert files['a'].read(3)==b'abc'
    with pytest.raises(ValueError,match='allowance'):files['b'].read(3)
    assert files['b'].tell()==0
    bad=wire.bind(grants.specifications(),lambda request:({'ok':True,'kind':wire.FORMAT,'offset':0,'bytes':2},b'x'))['a']
    with pytest.raises(ValueError,match='reply'):bad.read(2)
    assert bad.tell()==0


def test_stop_revokes_inflight_file_delivery(tmp_path):
    source=retained(tmp_path,b'abcdef');started=threading.Event();finish=threading.Event()
    class Delayed(type(source)):
        def read(self,*args):
            started.set();assert finish.wait(HANG_S);return super().read(*args)
    grants=SourceFileInputs({'x':Delayed(**vars(source))});token=grants.specifications()['x']['token']
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(grants.answer,{'op':'read-source-file','token':token,'offset':0,'length':3})
        assert started.wait(HANG_S)
        grants.close();finish.set()
        with pytest.raises(ValueError,match='closed before'):future.result(timeout=HANG_S)
    with pytest.raises(ValueError,match='closed'):grants.specifications()


@pytest.mark.parametrize('payload_size',[False,True,-1,1,0.0,None])
def test_payload_size_is_an_exact_empty_binary_message(tmp_path,payload_size):
    source=retained(tmp_path,b'abc');grants=SourceFileInputs({'x':source})
    token=grants.specifications()['x']['token']
    with pytest.raises(ValueError,match='Invalid source-file'):
        grants.answer({'op':'read-source-file','token':token,'offset':0,'length':1},payload_size)
    assert grants._budget.requests==0


def test_regional_navigation_renews_without_resetting_operation_or_other_source_authority(tmp_path,monkeypatch):
    from env_worker import native_regions
    source=retained(tmp_path,b'abcdefghij')
    grants=SourceFileInputs({'large':source,'ordinary':source},regional=['large'])
    files=wire.bind(grants.specifications(),grants.answer)
    monkeypatch.setattr(wire,'MAX_READ_BYTES',3)
    monkeypatch.setattr(native_regions,'MAX_VIEW_BYTES',5)
    now=[0.0]
    grants._budget.navigation.clock=files['large']._budget.navigation.clock=lambda:now[0]
    assert files['large'].read(3)==b'abc'
    assert files['large'].read(5,view=True)==b'defgh'
    with pytest.raises(ValueError,match='Navigation'):files['large'].read(1,view=True)
    assert files['large'].tell()==8
    now[0]=11
    assert files['large'].read(2,view=True)==b'ij'
    with pytest.raises(ValueError,match='allowance'):files['ordinary'].read(1)
    with pytest.raises(ValueError,match='regional'):files['ordinary'].read(1,view=True)
    token=grants.specifications()['ordinary']['token']
    with pytest.raises(ValueError,match='regional'):
        grants.answer({'op':'read-source-file','token':token,'offset':0,'length':1,'view':True})
    # A worker cannot bypass the parent's rolling limit by replacing its own budget.
    token=grants.specifications()['large']['token']
    with pytest.raises(ValueError,match='Navigation'):
        grants.answer({'op':'read-source-file','token':token,'offset':0,'length':4,'view':True})
    grants.close()
    with pytest.raises(ValueError,match='closed'):
        grants.answer({'op':'read-source-file','token':token,'offset':0,'length':1,'view':True})


def test_regional_folder_stream_keeps_bounded_reads_and_exact_selected_entries(tmp_path):
    from dataclasses import replace
    source=replace(retained(tmp_path,b'abcdefghij'),directory={'format':'source-directory-v1',
        'entries':[{'path':'0/0.0','offset':0,'size':10}]})
    grants=SourceFileInputs({'folder':source},regional=['folder'])
    directory=wire.bind(grants.specifications(),grants.answer)['folder']
    with directory.open('0/0.0',view=True) as entry:
        entry.seek(3)
        assert entry.read(4)==b'defg'
    assert grants._budget.bytes==0 and sum(n for _,n in grants._budget.navigation.views)==4
    assert directory['0/0.0']==b'abcdefghij'
    assert grants._budget.bytes==10


def test_streamed_source_capture_paces_existing_parent_and_worker_read_windows(tmp_path,monkeypatch):
    from env_worker import native_regions as regional
    source=retained(tmp_path,b'abcdefghijkl');grants=SourceFileInputs({'x':source},regional={'x'})
    files=wire.bind(grants.specifications(),grants.answer);f=files['x'];now=[0.]
    grants._budget.navigation.clock=f._budget.navigation.clock=lambda:now[0]
    monkeypatch.setattr(regional,'MAX_VIEW_BYTES',4)
    monkeypatch.setattr(regional.time,'sleep',lambda seconds:now.__setitem__(0,now[0]+seconds))
    def exchange(request):
        answer=grants.answer(request)
        now[0]+=.25  # Worker completion must be later than parent admission.
        return answer
    f._request=exchange
    with regional.paced_view_reads(lambda:None):
        assert f.read(4,view=True)==b'abcd'
        assert f.read(4,view=True)==b'efgh'
    assert now[0]>=regional.VIEW_WINDOW_S+.5
    assert f._budget.bytes==grants._budget.bytes==0
    count=[0]
    def cancel():
        count[0]+=1
        if count[0]==3:raise RuntimeError('cancelled while pacing')
    with pytest.raises(RuntimeError,match='cancelled while pacing'):
        with regional.paced_view_reads(cancel):f.read(4,view=True)
    assert f.tell()==8
    grants.close()
