"""Production intake, immutable store and SDK; scientific packages tested separately."""
import io,json
from dataclasses import replace
import numpy as np
import pytest
from env_worker.source_directory import index,FORMAT
from env_worker.source_files import bind
from orchestrator.scientific_directory_intake import DirectoryTransfer
from orchestrator.scientific_projects import ProjectStore,InvalidProject,Conflict
from orchestrator.source_files import SourceFileInputs
from tests.test_scientific_project_api import web,ORIGIN  # noqa:F401


def payload(files):
    entries=[];offset=0
    for name,body in files.items():entries.append({'path':name,'offset':offset,'size':len(body)});offset+=len(body)
    description={'format':FORMAT,'entries':entries};encoded=json.dumps(description).encode()
    return description,len(encoded).to_bytes(4,'big')+encoded+b''.join(files.values())


def transfer(store,files,request='folder-request-0123456789'):
    description,body=payload(files)
    task=DirectoryTransfer(store,'s1',request,name='specimen.zarr',size=sum(map(len,files.values())),max_bytes=64<<20)
    try:
        for offset in range(0,len(body),7):task.write(body[offset:offset+7])
        return task.publish()
    finally:task.close()


def granted(store,saved):
    source=store.file_source(saved.id,'s1',1,saved.document['resources'][0])
    grants=SourceFileInputs({'source':replace(source,directory=saved.document['state']['sourceDirectory'])})
    return grants,bind(grants.specifications(),grants.answer)['source']


def test_exact_folder_reopen_retry_and_independent_entry_cursors(tmp_path):
    files={'meta.json':b'{"scale":2}','planes/a.npy':b'abcdef','planes/b.npy':b'xyz'}
    store=ProjectStore(tmp_path);saved=transfer(store,files)
    assert saved.document['kind']=='source-directory' and transfer(store,files)==saved
    store=ProjectStore(tmp_path);saved=store.read(saved.id,'s1',revision=1)
    grants,folder=granted(store,saved)
    assert dict(folder)==files and str(tmp_path) not in repr(grants.specifications())
    first=folder.open('planes/a.npy');second=folder.open('planes/b.npy')
    assert first.read(2)==b'ab' and second.read(2)==b'xy' and first.read(2)==b'cd'
    with pytest.raises(PermissionError):folder['new']=b'bad'
    with pytest.raises(PermissionError):del folder['meta.json']
    with pytest.raises(KeyError):folder.open('../outside')
    grants.close()
    with pytest.raises(ValueError,match='closed'):first.read(1)
    assert len(store.list('s1'))==1


@pytest.mark.parametrize('names',[['../x'],['/x'],['x\\y'],['x//y'],['x/./y'],['x/../y'],['C:x'],['x\x00'],['x','x/y'],['x/y','x'],['x','x']])
def test_unsafe_duplicate_and_ambiguous_names_are_rejected(names):
    with pytest.raises(ValueError):index({'format':FORMAT,'entries':[{'path':name,'offset':i,'size':1} for i,name in enumerate(names)]},len(names))


@pytest.mark.parametrize('change',[{'offset':True},{'offset':1},{'size':-1},{'size':True},{'size':2}])
def test_ranges_partition_original_without_aliases(change):
    with pytest.raises(ValueError):index({'format':FORMAT,'entries':[{'path':'a','offset':0,'size':1,**change}]},1)


def test_interruption_and_different_retry_preserve_other_work(tmp_path):
    store=ProjectStore(tmp_path);saved=transfer(store,{'a':b'original'})
    with pytest.raises(Conflict):transfer(store,{'a':b'changed'})
    _,body=payload({'b':b'new data'})
    task=DirectoryTransfer(store,'s1','new-request-0123456789',name='other.zarr',size=8,max_bytes=64<<20)
    try:
        task.write(body[:-2])
        with pytest.raises(InvalidProject):task.publish()
    finally:task.close()
    assert len(store.list('s1'))==1 and store.read(saved.id,'s1')==saved
    assert not list(store.root.glob('.scientific-import-*'))
    assert transfer(store,{'b':b'new data'},'new-request-0123456789').revision==1


def test_public_folder_upload_and_exact_entry_download(web):
    client,built=web;files={'.zgroup':b'{"zarr_format":2}','0/0.0':b'\x01\x02\x03'};_,body=payload(files)
    params={'session':built.day.sid,'name':'specimen.zarr','size':str(sum(map(len,files.values()))),'request':'folder-http-0123456789'}
    headers={'Origin':ORIGIN,'X-MicroscopeMind':'import-scientific-directory'};url='/api/scientific-directories'
    assert client.post(url,params=params,content=body).status_code==403
    response=client.post(url,headers=headers,params=params,content=body)
    assert response.status_code==200,response.text
    saved=response.json()['project'];assert client.post(url,headers=headers,params=params,content=body).json()['project']==saved
    for name,data in files.items():
        result=client.get(url+'/'+saved['id']+'/file',params={'session':built.day.sid,'revision':1,'path':name})
        assert result.status_code==200,result.text
        assert result.content==data
    assert client.get('/api/scientific-files/'+saved['id'],params={'session':built.day.sid,'revision':1}).status_code==400
    assert client.get(url+'/'+saved['id']+'/file',params={'session':built.day.sid,'revision':1,'path':'../outside'}).status_code==404


def test_numpy_sequence_is_an_actual_second_consumer(tmp_path):
    expected=np.arange(60,dtype='>u2').reshape(3,4,5);files={'calibration.json':b'{"scale":[2,3,4]}'}
    for n,plane in enumerate(expected):
        stream=io.BytesIO();np.save(stream,plane,allow_pickle=False);files[f'planes/{n}.npy']=stream.getvalue()
    store=ProjectStore(tmp_path);saved=transfer(store,files);grants,folder=granted(store,saved)
    assert json.loads(folder['calibration.json'])['scale']==[2,3,4]
    planes=[]
    for key in sorted(name for name in folder if name.endswith('.npy')):
        with folder.open(key) as stream:planes.append(np.load(stream,allow_pickle=False))
    assert all(plane.dtype.str=='>u2' for plane in planes)
    np.testing.assert_array_equal(np.stack(planes),expected)


def test_large_index_uses_bounded_grant_payload_not_opening_header(tmp_path):
    files={f'level/{i:04d}/chunk':b'x' for i in range(4096)}
    store=ProjectStore(tmp_path);saved=transfer(store,files);grants,folder=granted(store,saved)
    opening=json.dumps(grants.specifications()).encode()
    assert len(opening)<4096 and len(folder)==4096
    assert folder['level/4095/chunk']==b'x'
    assert grants._budget.requests==1 and len(grants._index_reads)==1


def test_folder_index_digest_is_verified_before_exposing_names(tmp_path):
    store=ProjectStore(tmp_path);saved=transfer(store,{'meta':b'one'})
    source=store.file_source(saved.id,'s1',1,saved.document['resources'][0])
    grants=SourceFileInputs({'source':replace(source,directory=saved.document['state']['sourceDirectory'])})
    def changed(request):
        header,body=grants.answer(request);return header,body[:-1]+b'!'
    with pytest.raises(ValueError,match='identity'):bind(grants.specifications(),changed)


def test_deep_paths_validate_without_accumulating_all_parent_prefixes():
    # Admitted long paths must not create a set containing every intermediate prefix.
    names=[f'{i:04d}/'+('a/'*190)+'chunk' for i in range(1800)]
    description={'format':FORMAT,'entries':[{'path':name,'offset':i,'size':1} for i,name in enumerate(names)]}
    assert len(index(description,1800)['entries'])==1800


def test_directory_reader_binding_keeps_exact_folder_and_existing_generation(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from orchestrator import scientific_readers as readers
    from orchestrator.scientific_project_review import _resolve,_files
    store=ProjectStore(tmp_path);saved=transfer(store,{'a/data':b'content'})
    declaration={'format':3,'kind':'directory','input':'source','patterns':['*.zarr'],'maximum_bytes':64<<20,
                 'keeps':['values','dtype','byte-order','axes','calibration']}
    from gpu_worker.bundle_contract import parse_members,view
    from tests.test_scientific_reader_choice import member
    raw=member('read','OME-Zarr test reader',declaration)
    owner=SimpleNamespace(key='mine/zarr',runner='python-env',requires={'catalog':3},members=(raw,))
    @contextmanager
    def prepared(*args):yield SimpleNamespace(m=view(owner,parse_members([raw],owner)[0]))
    monkeypatch.setattr(readers.prepared_bundles,'prepared_member',prepared)
    candidate=readers.bind(store,saved.id,'s1',1,'mine/zarr#read','a'*16)
    reopened,runtime,inputs=_resolve(store,candidate.id,'s1',1)
    assert runtime['generation']=='a'*16 and inputs=={}
    spec=reopened.document['state']['scientificView']
    sources=_files(store,candidate.id,'s1',1,spec['files'],lambda:None,spec['directories'])
    grants=SourceFileInputs(sources);folder=bind(grants.specifications(),grants.answer)['source']
    assert folder['a/data']==b'content'
    assert store.read(saved.id,'s1')==saved
    assert readers.bind(store,saved.id,'s1',1,'mine/zarr#read','a'*16)==candidate
