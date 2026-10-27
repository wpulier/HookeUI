"""Reader choice by declared capability over real retained sources and HTTP; no reader runs here."""
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from gpu_worker import bundle_contract
from orchestrator import scientific_readers as readers
from orchestrator.scientific_projects import ProjectStore
from tests.test_scientific_directories import transfer
from tests.test_scientific_file_intake import attempt
from tests.test_scientific_file_api import upload
from tests.test_scientific_project_api import web,ORIGIN  # noqa: F401

GEN='a'*16
EXACT=['values','dtype','byte-order','axes','calibration']


def declaration(**changes):
    return {'format':3,'input':'source','kind':'file','patterns':['*.npy'],'maximum_bytes':1024,'keeps':list(EXACT),**changes}


def member(id,name,reader):
    return {'id':id,'name':name,'adapter':'adapter:create','bridge':'scientific-host-v1',
            'inputs':[{'id':'project','type':'project.scientific'}],'outputs':[{'id':'result','type':'project.scientific'}],
            'reader':reader}


@pytest.fixture
def installed(tmp_path,monkeypatch):
    """Synthetic prepared/selected/consented boundaries; real declaration and source checks."""
    owners={};accepted={}
    def install(key,*members,enabled=True):
        owners[key]=SimpleNamespace(key=key,runner='python-env',requires={'catalog':3},members=members)
        accepted[key]=enabled
    runtime=tmp_path/'runtime'/GEN;runtime.mkdir(parents=True);(runtime/readers.envs.RECEIPT).touch()
    monkeypatch.setattr(readers.catalog,'manifests',lambda:{})
    monkeypatch.setattr(readers.catalog,'mine',lambda environ:dict(owners))
    monkeypatch.setattr(readers.envs,'generation',lambda *a:GEN)
    monkeypatch.setattr(readers.envs,'root',lambda *a:runtime.parent)
    monkeypatch.setattr(readers.readiness,'misfit',lambda *a:'')
    monkeypatch.setattr(readers.channels,'trusted',lambda *a:True)
    monkeypatch.setattr(readers.env_runner,'in_use',lambda owner,*a:SimpleNamespace(m=owners[owner.key],folder=runtime))
    monkeypatch.setattr(readers.own_trials,'pending',lambda owner,*a:not accepted[owner.key])
    @contextmanager
    def prepared(key,generation,environ):
        owner,member_id=bundle_contract.parse_capability_key(key)
        assert generation==GEN
        parsed=next(m for m in bundle_contract.parse_members(list(owners[owner].members),owners[owner]) if m.id==member_id)
        yield SimpleNamespace(m=bundle_contract.view(owners[owner],parsed))
    monkeypatch.setattr(readers.prepared_bundles,'prepared_member',prepared)
    return install


def source(store):
    task=attempt(store);saved=task.publish();task.close();return saved


def test_one_exact_reader_is_chosen_and_inexact_or_undeclared_readers_are_refused_precisely(tmp_path,installed):
    store=ProjectStore(tmp_path);saved=source(store)
    installed('mine/exact',member('read','Exact NumPy',declaration()))
    installed('mine/rgb',member('read','Picture reader',declaration(keeps=['values','axes'])))
    installed('mine/legacy',member('read','Old reader',{'format':1,'input':'source','extensions':['.npy'],'maximum_bytes':1024}))
    found=readers.discover(store,saved.id,'s1',1)
    rows={row['name']:row for row in found['readers']}
    assert rows['Exact NumPy']['canReview'] and rows['Exact NumPy']['keeps']==EXACT
    assert rows['Picture reader']['matches'] and not rows['Picture reader']['canReview']
    assert rows['Picture reader']['reason']==('This reader does not declare that it keeps data type, byte order and '
                                              'calibration exact, so Hooke will not open scientific data with it')
    assert rows['Old reader']['matches'] and rows['Old reader']['keeps'] is None
    assert 'format 1' in rows['Old reader']['reason'] and 'format-3 reader declaration' in rows['Old reader']['reason']
    assert found['choice']=={'state':'chosen','capability':'mine/exact#read','generation':GEN,
        'says':'Exact NumPy is the one installed reader that declares this source and keeps its native facts exact.'}
    bound=readers.bind(store,saved.id,'s1',1,'mine/exact#read',GEN)
    assert bound.document['state']['sourceReader']['keeps']==EXACT
    for refused in ('mine/rgb#read','mine/legacy#read'):
        with pytest.raises(readers.InvalidProject,match='will not open scientific data'):readers.bind(store,saved.id,'s1',1,refused,GEN)
    assert store.read(saved.id,'s1')==saved


def test_several_exact_readers_are_the_researchers_choice(tmp_path,installed):
    store=ProjectStore(tmp_path);saved=source(store)
    installed('mine/first',member('read','First',declaration()))
    installed('mine/second',member('read','Second',declaration(patterns=['*.np?'])))
    found=readers.discover(store,saved.id,'s1',1)
    assert found['choice']['state']=='ambiguous'
    assert found['choice']['capabilities']==['mine/first#read','mine/second#read']
    assert 'Hooke does not choose between them' in found['choice']['says']
    first=readers.bind(store,saved.id,'s1',1,'mine/first#read',GEN)
    second=readers.bind(store,saved.id,'s1',1,'mine/second#read',GEN)
    assert first.id!=second.id and store.read(saved.id,'s1')==saved


def test_no_reader_and_no_declared_match_say_what_is_installed(tmp_path,installed):
    store=ProjectStore(tmp_path);saved=source(store)
    nothing=readers.discover(store,saved.id,'s1',1)['choice']
    assert nothing['state']=='refused' and nothing['says']=='No installed integration declares a scientific reader.'
    installed('mine/tiff',member('read','TIFF reader',declaration(patterns=['*.tif','*.tiff'])))
    installed('mine/zarr',member('read','Zarr reader',declaration(kind='directory',patterns=['*.zarr'])))
    found=readers.discover(store,saved.id,'s1',1)
    assert [row['matches'] for row in found['readers']]==[False,False]
    assert found['readers'][0]['reason']=='This reader opens names matching *.tif, *.tiff; this source is named “sample.npy”'
    assert found['readers'][1]['reason']=='This reader opens folders; this source is a single file'
    assert found['choice']['says']==('No installed reader declares a single file named “sample.npy”. Installed readers open: '
                                     'TIFF reader (single files *.tif, *.tiff); Zarr reader (folders *.zarr).')
    assert 'saved source stays as it is' in found['choice']['next']


def test_matching_readers_that_cannot_open_exactly_now_each_say_why(tmp_path,installed,monkeypatch):
    store=ProjectStore(tmp_path);saved=source(store)
    installed('mine/small',member('read','Small reader',declaration(maximum_bytes=4)))
    installed('mine/blocked',member('read','Blocked reader',declaration()))
    monkeypatch.setattr(readers.readiness,'misfit',lambda owner:'This device is unavailable on this Mac' if owner.key=='mine/blocked' else '')
    choice=readers.discover(store,saved.id,'s1',1)['choice']
    assert choice['state']=='refused'
    assert choice['says']==('2 installed readers declare this source, but none can open it exactly now. '
                            'Small reader: This reader opens sources up to 4 bytes; this one is 14 bytes. '
                            'Blocked reader: This device is unavailable on this Mac.')


def test_folder_readers_check_their_required_metadata_and_entry_names(tmp_path,installed):
    store=ProjectStore(tmp_path)
    zarr=transfer(store,{'.zattrs':b'{}','.zgroup':b'{}','0/.zarray':b'{}','0/0.0':b'\x01\x02'})
    series=transfer(store,{'z000.npy':b'a','z001.npy':b'b','notes.txt':b'c'},request='folder-request-series-0001')
    installed('mine/ome',member('read','OME-Zarr',declaration(kind='directory',patterns=['*.zarr'],requires=['.zattrs','.zgroup'],maximum_bytes=1<<20)))
    installed('mine/v3',member('read','Zarr v3',declaration(kind='directory',patterns=['*.zarr'],requires=['zarr.json'],maximum_bytes=1<<20)))
    installed('mine/series',member('read','Plane series',declaration(kind='directory',patterns=['*'],entries=['*.npy'],maximum_bytes=1<<20)))
    found=readers.discover(store,zarr.id,'s1',1)
    rows={row['name']:row for row in found['readers']}
    assert rows['OME-Zarr']['canReview'] and found['choice']['capability']=='mine/ome#read'
    assert rows['Zarr v3']['reason']=='This reader needs the folder to contain zarr.json; this one does not'
    assert rows['Plane series']['reason'].startswith('This reader needs every file to match *.npy; “.zattrs” and 3 more do not')
    found=readers.discover(store,series.id,'s1',1)
    rows={row['name']:row for row in found['readers']}
    assert rows['OME-Zarr']['reason']=='This reader needs the folder to contain .zattrs and .zgroup; this one does not'
    assert rows['Plane series']['reason']=='This reader needs every file to match *.npy; “notes.txt” does not'
    assert found['choice']['state']=='refused'
    bound=readers.bind(store,zarr.id,'s1',1,'mine/ome#read',GEN)
    assert bound.document['state']['scientificView']['directories']['source']==zarr.document['state']['sourceDirectory']


def test_refused_reader_choice_over_http_keeps_the_source_and_says_the_next_step(web,installed):
    client,built=web;saved=upload(client,built).json()['project']
    installed('mine/rgb',member('read','Picture reader',declaration(keeps=['values'])))
    installed('mine/exact',member('read','Exact NumPy',declaration()))
    listed=client.get(f'/api/scientific-files/{saved["id"]}/readers',params={'session':built.day.sid,'revision':1}).json()
    assert listed['choice']['capability']=='mine/exact#read'
    headers={'Origin':ORIGIN,'X-MicroscopeMind':'choose-scientific-reader'}
    path=f'/api/scientific-files/{saved["id"]}/reader'
    refused=client.post(path,headers=headers,json={'session':built.day.sid,'revision':1,'capability':'mine/rgb#read','generation':GEN})
    assert refused.status_code==400
    assert refused.json()['says'].startswith('This reader does not declare that it keeps data type, byte order, axes and calibration exact')
    assert 'saved source is unchanged' in refused.json()['next']
    assert len(built.hub.resources.projects.list(built.day.sid))==1
    accepted=client.post(path,headers=headers,json={'session':built.day.sid,'revision':1,'capability':'mine/exact#read','generation':GEN})
    assert accepted.status_code==200 and accepted.json()['project']['document']['state']['sourceReader']['keeps']==EXACT


@pytest.mark.parametrize('change',[{'kind':'archive'},{'patterns':['*.NPY']},{'patterns':['a/b']},{'patterns':[]},
    {'keeps':['values','colour']},{'keeps':['values','values']},{'requires':['.zattrs']},{'kind':'directory','requires':['../x']},
    {'kind':'stack','entries':['*/x']},{'maximum_bytes':(64<<20)+1},{'extensions':['.npy']},{'keeps':None}])
def test_format_three_declarations_are_bounded_and_explicit(change):
    owner=SimpleNamespace(key='mine/reader',runner='python-env',requires={'catalog':3},members=())
    good=member('read','Reader',declaration(kind='directory',patterns=['*.zarr','*'],requires=['.zattrs'],entries=['*']))
    parsed,=bundle_contract.parse_members([good],owner)
    assert parsed.reader['keeps']==EXACT and parsed.reader['requires']==['.zattrs']
    bad=member('read','Reader',{**declaration(),**change})
    with pytest.raises(ValueError,match='format-3 reader'):bundle_contract.parse_members([bad],owner)


def test_missing_setup_and_device_limits_are_said_apart_from_incompatibility(tmp_path,installed,monkeypatch):
    store=ProjectStore(tmp_path);saved=source(store)
    installed('mine/wrong',member('read','Wrong format',declaration(patterns=['*.tif'])))
    installed('mine/device',member('read','Needs a GPU',declaration()))
    installed('mine/untrusted',member('read','Untrusted',declaration()))
    installed('mine/ready',member('read','Ready',declaration()))
    monkeypatch.setattr(readers.readiness,'misfit',lambda owner:'This device is unavailable on this Mac' if owner.key=='mine/device' else '')
    monkeypatch.setattr(readers.channels,'trusted',lambda owner,environ:owner.key!='mine/untrusted')
    rows={row['name']:row for row in readers.discover(store,saved.id,'s1',1)['readers']}
    assert {name:row['blocked'] for name,row in rows.items()}=={'Wrong format':'incompatible','Needs a GPU':'device',
                                                               'Untrusted':'setup','Ready':''}
    assert rows['Untrusted']['reason']=='Allow this catalog’s code before reviewing its reader'


def test_a_declaration_that_states_no_kept_facts_is_the_integrations_to_update(tmp_path,installed):
    store=ProjectStore(tmp_path);saved=source(store)
    installed('mine/legacy',member('read','Old reader',{'format':1,'input':'source','extensions':['.npy'],'maximum_bytes':1024}))
    installed('mine/small',member('read','Old small reader',{'format':1,'input':'source','extensions':['.npy'],'maximum_bytes':4}))
    rows={row['name']:row for row in readers.discover(store,saved.id,'s1',1)['readers']}
    assert rows['Old reader']['blocked']=='setup' and rows['Old small reader']['blocked']=='incompatible'
