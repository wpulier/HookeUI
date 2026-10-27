"""Real retained sources and HTTP; synthetic preparation here, upstream evidence separate."""
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from gpu_worker import bundle_contract
from orchestrator import scientific_readers as readers
from orchestrator.scientific_projects import ProjectStore,InvalidProject
from tests.test_scientific_file_intake import attempt
from tests.test_scientific_file_api import upload
from tests.test_scientific_project_api import web,ORIGIN,HEADERS  # noqa: F401

KEY='mine/test-reader#read-npy';GEN='a'*16
DECL={'format':3,'input':'selected','kind':'file','patterns':['*.npy'],'maximum_bytes':1024,
      'keeps':['values','dtype','byte-order','axes','calibration']}

@pytest.fixture
def prepared(monkeypatch):
    calls=[]
    @contextmanager
    def member(key,generation,environ):
        calls.append((key,generation))
        if key!=KEY or generation!=GEN:raise ValueError('wrong prepared generation')
        yield SimpleNamespace(m=SimpleNamespace(owner=SimpleNamespace(members=()),reader=DECL,bridge='scientific-host-v1',name='NumPy reader'))
    monkeypatch.setattr(readers.prepared_bundles,'prepared_member',member)
    return calls


def test_bind_pins_original_and_generation_without_execution_or_duplicate_revision(tmp_path,prepared):
    store=ProjectStore(tmp_path);owner=attempt(store);original=owner.publish();owner.close()
    saved=readers.bind(store,original.id,'s1',1,KEY,GEN)
    assert saved.revision==1 and saved.id!=original.id and saved.document['kind']=='scene'
    assert saved.document['state']['scientificView']=={'runtime':{'bundle':'mine/test-reader','generation':GEN,'operation':'read-npy'},
        'inputs':{},'files':{'selected':saved.document['resources'][0]}}
    assert store.read(original.id,'s1',revision=1)==original
    assert readers.bind(store,original.id,'s1',1,KEY,GEN)==saved
    assert store.read(original.id,'s1')==original
    assert len(prepared)==2


@pytest.mark.parametrize('reason',['suffix','size','cancel','generation'])
def test_refused_reader_preserves_source_and_allows_corrected_retry(tmp_path,prepared,monkeypatch,reason):
    store=ProjectStore(tmp_path);owner=attempt(store);original=owner.publish();owner.close()
    generation=GEN
    with monkeypatch.context() as patch:
        if reason=='suffix':patch.setitem(DECL,'patterns',['*.tif'])
        if reason=='size':patch.setitem(DECL,'maximum_bytes',1)
        if reason=='generation':generation='b'*16
        with pytest.raises((InvalidProject,ValueError,RuntimeError)):
            readers.bind(store,original.id,'s1',1,KEY,generation,check=lambda: (_ for _ in ()).throw(RuntimeError('Stopped')) if reason=='cancel' else None)
    assert store.read(original.id,'s1',revision=1)==original
    assert readers.bind(store,original.id,'s1',1,KEY,GEN).revision==1


def test_public_reader_choice_preserves_authority_and_source(web,prepared):
    client,built=web;source=upload(client,built).json()['project']
    path=f'/api/scientific-files/{source["id"]}/reader'
    body={'session':built.day.sid,'revision':1,'capability':KEY,'generation':GEN}
    assert client.post(path,json=body).status_code==403 and not prepared
    headers={'Origin':ORIGIN,'X-MicroscopeMind':'choose-scientific-reader'}
    result=client.post(path,headers=headers,json=body)
    assert result.status_code==200,result.text
    assert result.json()['project']['revision']==1
    assert result.json()['project']['id']!=source['id']
    assert client.post(path,headers=headers,json=body).json()==result.json()
    retained=result.json()['project'];forged=retained['document']
    forged['state']['sourceReader']['name']='Forged model'
    changed=client.put('/api/scientific-projects/'+retained['id'],headers=HEADERS,
        json={'session':built.day.sid,'expectedRevision':1,'document':forged})
    assert changed.status_code==400 and 'provenance' in changed.json()['says']
    assert client.get('/api/scientific-files/'+source['id'],params={'session':built.day.sid,'revision':1}).content==b'native source bytes'


def test_discovery_is_declarative_and_hardware_mismatch_never_starts_preparation(tmp_path,monkeypatch):
    monkeypatch.setattr(readers.env_runner,'in_use',lambda *a:None)
    store=ProjectStore(tmp_path);task=attempt(store);source=task.publish();task.close()
    owner=SimpleNamespace(key='mine/test-reader',runner='python-env',requires={'catalog':3},members=())
    raw={'id':'read-npy','name':'NumPy reader','adapter':'adapter:create','bridge':'scientific-host-v1',
         'inputs':[{'id':'project','type':'project.scientific'}],'outputs':[{'id':'result','type':'project.scientific'}],'reader':DECL}
    owner.members=(raw,)
    monkeypatch.setattr(readers.catalog,'manifests',lambda:{})
    monkeypatch.setattr(readers.catalog,'mine',lambda environ:{owner.key:owner})
    monkeypatch.setattr(readers.envs,'generation',lambda *a:GEN)
    monkeypatch.setattr(readers.envs,'root',lambda *a:tmp_path/'runtime')
    monkeypatch.setattr(readers.readiness,'misfit',lambda *a:'This device is unavailable on this Mac')
    monkeypatch.setattr(readers.channels,'trusted',lambda *a:pytest.fail('hardware refusal needs no trust operation'))
    row=readers.discover(store,source.id,'s1',1)['readers'][0]
    assert not row['canReview'] and row['reason']=='This device is unavailable on this Mac'
    assert row['generation']==GEN and 'unproven' in row['scope']


def test_reader_candidates_never_replace_original_or_other_candidates(tmp_path,monkeypatch):
    store=ProjectStore(tmp_path);task=attempt(store);original=task.publish();task.close()
    @contextmanager
    def member(*args):yield SimpleNamespace(m=SimpleNamespace(owner=SimpleNamespace(members=()),reader=DECL,bridge='scientific-host-v1',name='NumPy reader'))
    monkeypatch.setattr(readers.prepared_bundles,'prepared_member',member)
    first=readers.bind(store,original.id,'s1',1,KEY,GEN)
    latest=store.save(original.id,'s1',{**original.document,'title':'Retained renamed source'},expected_revision=1)
    second=readers.bind(store,original.id,'s1',1,'mine/other-reader#read-npy',GEN)
    updated=readers.bind(store,original.id,'s1',1,KEY,'b'*16)
    assert len({original.id,first.id,second.id,updated.id})==4
    assert {first.document['title'],second.document['title'],updated.document['title']}=={'sample.npy · NumPy reader'}
    # Display names can coincide; exact retained identities and generation remain distinct.
    assert store.read(original.id,'s1')==latest
    assert store.read(first.id,'s1')==first
    assert readers.bind(store,original.id,'s1',1,KEY,GEN)==first
    for candidate in (first,second,updated):
        assert candidate.document['state']['sourceReader']['source']['digest']==original.digest
        data=store.file_source(candidate.id,'s1',1,candidate.document['resources'][0])
        assert data.sha256==original.document['state']['sourceFile']['sha256']


def test_incomplete_contribution_does_not_hide_healthy_reader(tmp_path,monkeypatch):
    monkeypatch.setattr(readers.env_runner,'in_use',lambda *a:None)
    store=ProjectStore(tmp_path);task=attempt(store);source=task.publish();task.close()
    raw={'id':'read-npy','name':'NumPy reader','adapter':'adapter:create','bridge':'scientific-host-v1',
         'inputs':[{'id':'project','type':'project.scientific'}],'outputs':[{'id':'result','type':'project.scientific'}],'reader':DECL}
    good=SimpleNamespace(key='mine/good',runner='python-env',requires={'catalog':3},members=(raw,))
    bad=SimpleNamespace(key='mine/bad',runner='python-env',requires={'catalog':3},members=(raw,))
    def generation(owner,*args):
        if owner is bad:raise ValueError('Missing lock')
        return GEN
    monkeypatch.setattr(readers.catalog,'manifests',lambda:{})
    monkeypatch.setattr(readers.catalog,'mine',lambda environ:{bad.key:bad,good.key:good})
    monkeypatch.setattr(readers.envs,'generation',generation)
    runtime=tmp_path/'runtime'/GEN;runtime.mkdir(parents=True);(runtime/readers.envs.RECEIPT).touch()
    monkeypatch.setattr(readers.envs,'root',lambda *a:runtime.parent)
    monkeypatch.setattr(readers.readiness,'misfit',lambda *a:'')
    monkeypatch.setattr(readers.channels,'trusted',lambda *a:True)
    rows=readers.discover(store,source.id,'s1',1)['readers']
    assert len(rows)==2 and rows[0]['generation'] is None and not rows[0]['canReview']
    assert 'Repair' in rows[0]['reason'] and rows[1]['canReview']


@pytest.mark.parametrize('metadata',[None,{},{'format':1},dict(format=1,name='source.npy',size='bad',sha256='a'*64,reference={})])
def test_invalid_source_metadata_has_actionable_refusal(metadata):
    store=SimpleNamespace(read=lambda *a,**k:SimpleNamespace(document={'kind':'source-file','state':{'sourceFile':metadata},'resources':[]}))
    with pytest.raises(InvalidProject,match='original source file'):readers.source_document(store,'p','s1',1)


def test_reader_name_is_recorded_from_prepared_member_and_guarded(tmp_path,prepared):
    import copy
    from orchestrator.scientific_provenance import guard
    store=ProjectStore(tmp_path);owner=attempt(store);original=owner.publish();owner.close()
    saved=readers.bind(store,original.id,'s1',1,KEY,GEN)
    assert saved.document['title']=='sample.npy · NumPy reader'
    assert saved.document['state']['sourceReader']['name']=='NumPy reader'
    assert saved.document['state']['sourceReader']['sourceName']=='sample.npy'
    assert 'scientificToolChoice' not in saved.document['state']
    assert 'methodSummary' not in saved.document['state']
    for field in ('name','sourceName','capability','generation','source'):
        forged=copy.deepcopy(saved.document);forged['state']['sourceReader'][field]='forged'
        with pytest.raises(InvalidProject,match='provenance'):
            guard(saved,forged)
    renamed=store.save(saved.id,'s1',{**saved.document,'title':'My custom title'},expected_revision=1)
    assert renamed.document['title']=='My custom title'
    assert readers.bind(store,original.id,'s1',1,KEY,GEN)==saved
    assert store.read(saved.id,'s1')==renamed


def test_legacy_reader_retry_preserves_identity_and_original_title(tmp_path,prepared,monkeypatch):
    store=ProjectStore(tmp_path);owner=attempt(store);original=owner.publish();owner.close()
    current=readers._ReaderIntake.document
    def legacy(owner,ref):
        document=current(owner,ref)
        document['state']['sourceReader']=owner.origin
        document['title']=f'{owner.name[:80]} · {owner.origin["capability"][:130]} · {owner.origin["generation"]}'
        return document
    with monkeypatch.context() as patch:
        patch.setattr(readers._ReaderIntake,'document',legacy)
        saved=readers.bind(store,original.id,'s1',1,KEY,GEN)
    renamed=store.save(saved.id,'s1',{**saved.document,'title':'My earlier reader'},expected_revision=1)
    assert readers.bind(store,original.id,'s1',1,KEY,GEN)==saved
    assert 'name' not in saved.document['state']['sourceReader']
    assert store.read(saved.id,'s1')==renamed


def test_reader_retains_owned_links_without_payload_reads_and_survives_original_removal(tmp_path,prepared,monkeypatch):
    import shutil
    from orchestrator import scientific_files as files
    store=ProjectStore(tmp_path);owner=attempt(store);original=owner.publish();owner.close()
    read=files._read
    def metadata_only(folder,name,limit):
        assert not name.endswith('.bin'),'Reader binding must not scan payloads'
        return read(folder,name,limit)
    with monkeypatch.context() as patch:
        patch.setattr(files,'_read',metadata_only)
        patch.setattr(files.FileDraft,'write',lambda *args:pytest.fail('Reader binding must not copy payloads'))
        saved=readers.bind(store,original.id,'s1',1,KEY,GEN)
        assert readers.bind(store,original.id,'s1',1,KEY,GEN)==saved
    original_chunk=next((tmp_path/original.id/'files').glob('*.bin'))
    retained_chunk=tmp_path/saved.id/'files'/original_chunk.name
    assert original_chunk.stat().st_ino==retained_chunk.stat().st_ino
    assert saved.document['state']['sourceReader']['source']['digest']==original.digest
    # Fixture-owned deletion models independent lifetime; no product deletion runs.
    shutil.rmtree(tmp_path/original.id)
    retained=store.file_source(saved.id,'s1',1,saved.document['resources'][0])
    assert retained.read(0,retained.size)==b'selected bytes'
    assert store.read(saved.id,'s1')==saved


@pytest.mark.parametrize('damage',['missing','symlink','writable'])
def test_reader_refuses_unsafe_accepted_chunks_without_publishing(tmp_path,prepared,damage):
    store=ProjectStore(tmp_path);owner=attempt(store);original=owner.publish();owner.close()
    chunk=next((tmp_path/original.id/'files').glob('*.bin'))
    if damage=='writable':chunk.chmod(0o600)
    else:
        body=chunk.read_bytes();chunk.unlink()
        if damage=='symlink':
            outside=tmp_path/'outside';outside.write_bytes(body);outside.chmod(0o400)
            chunk.symlink_to(outside)
    with pytest.raises(InvalidProject,match='retained source chunk'):
        readers.bind(store,original.id,'s1',1,KEY,GEN)
    assert store.list('s1')==[original]
    assert not list(tmp_path.glob('.scientific-import-*'))


@pytest.mark.parametrize('failure',['cancel','unsupported'])
def test_reader_link_failure_cleans_private_links_and_preserves_original(tmp_path,prepared,monkeypatch,failure):
    import errno
    from orchestrator import scientific_files as files
    store=ProjectStore(tmp_path);owner=attempt(store);original=owner.publish();owner.close()
    link=files.os.link;linked=[]
    def controlled(source,target,**kwargs):
        if str(source).endswith('.bin'):
            if failure=='unsupported':raise OSError(errno.EXDEV,'controlled unsupported filesystem')
            result=link(source,target,**kwargs);linked.append(source);return result
        return link(source,target,**kwargs)
    def check():
        if linked:raise RuntimeError('Stopped after linking')
    with monkeypatch.context() as patch:
        patch.setattr(files.os,'link',controlled)
        with pytest.raises((InvalidProject,RuntimeError),match='Stopped|cannot retain immutable'):
            readers.bind(store,original.id,'s1',1,KEY,GEN,check=check)
    assert store.list('s1')==[original]
    assert not list(tmp_path.glob('.scientific-import-*'))
    source=store.file_source(original.id,'s1',1,original.document['resources'][0])
    assert source.read(0,source.size)==b'selected bytes'


def test_linked_reader_checks_content_on_read_after_external_tampering(tmp_path,prepared):
    store=ProjectStore(tmp_path);owner=attempt(store);original=owner.publish();owner.close()
    saved=readers.bind(store,original.id,'s1',1,KEY,GEN)
    source=store.file_source(saved.id,'s1',1,saved.document['resources'][0],verify_content=False)
    chunk=next((tmp_path/original.id/'files').glob('*.bin'))
    chunk.chmod(0o600);chunk.write_bytes(b'x'*source.size);chunk.chmod(0o400)
    with pytest.raises(InvalidProject,match='chunk hash changed'):source.read(0,source.size)


@pytest.mark.parametrize('damage',[None,'runtime','reader-runtime','name'])
def test_output_saved_from_a_reader_workspace_names_that_reader_only_while_it_is_the_views_runtime(tmp_path,prepared,damage):
    import copy
    from orchestrator import scientific_provenance
    store=ProjectStore(tmp_path);owner=attempt(store);original=owner.publish();owner.close()
    workspace=readers.bind(store,original.id,'s1',1,KEY,GEN)
    runtime=copy.deepcopy(workspace.document['state']['scientificView']['runtime'])
    if damage=='reader-runtime':
        # A later revision whose view runs another tool keeps the guarded reader record, which no longer names it.
        workspace=SimpleNamespace(**{**vars(workspace),'document':copy.deepcopy(workspace.document)})
        workspace.document['state']['scientificView']['runtime']={**runtime,'generation':'b'*16}
    if damage=='name':
        workspace=SimpleNamespace(**{**vars(workspace),'document':copy.deepcopy(workspace.document)})
        workspace.document['state']['sourceReader']['name']=' '
    other={**runtime,'generation':'c'*16}
    record=SimpleNamespace(document={'state':{'scientificView':{'runtime':other if damage=='runtime' else runtime}},'runtimes':[runtime,other]})
    exact={'id':workspace.id,'session':workspace.session,'revision':workspace.revision,'digest':workspace.digest}
    read=store.read
    store.read=lambda *a,**k:workspace if a[0]==workspace.id else read(*a,**k)
    found=scientific_provenance._saved_output_tool(store,record,{'base':exact,'runtimes':[runtime,other]})
    if damage is None:
        assert found=={'name':'NumPy reader','runtime':runtime,'source':exact}
    else:
        assert found is None
