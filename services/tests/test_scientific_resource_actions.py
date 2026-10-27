"""Real accepted storage and exports; synthetic scientific precision fixtures."""
import io
import json
import zipfile
import numpy as np
import pytest
from orchestrator import scientific_resource_actions as actions
from orchestrator.scientific_projects import ProjectStore,Conflict,InvalidProject
from tests.test_scientific_arrays import fill


def labels(tmp_path):
    store=ProjectStore(tmp_path/'projects')
    original=store.create('s1',{'kind':'scene','title':'Corrected labels','state':{},'resources':[],'runtimes':[]})
    data=np.array([[0,2**63+7,2**63+7],[7,0,7]],dtype='>u8')
    draft=store.array_draft(original.id,'s1',component='array.labels',shape=data.shape,dtype=data.dtype.str,
        axes=['y','x'],chunks=[1,3],scientific={'semantic':'instance-labels'},max_written_bytes=data.nbytes)
    ref=fill(draft,data)
    doc=original.document;doc['resources']=[ref];doc['state']['scientificView']={'inputs':{'labels':ref}}
    saved=store.save(original.id,'s1',doc,expected_revision=1)
    return store,saved,data


def test_exact_object_identity_counts_centroids_and_retry(tmp_path):
    store,saved,data=labels(tmp_path)
    measured=actions.measure(store,saved.id,'s1',saved.revision,'labels')
    assert actions.measure(store,saved.id,'s1',saved.revision,'labels')==measured
    table=actions.table(store,saved.id,'s1',measured.revision)
    assert table['rows']==[['7',2,1.,1.],[str(2**63+7),2,0.,1.5]]
    assert table['columns']==['object_id','voxel_count','centroid_y_px','centroid_x_px']
    assert table['source']['digest']==saved.digest and table['source']['reference']==saved.document['resources'][0]
    # The labels stay as they were; the typed table adds exactly its four column components.
    assert measured.document['resources'][:1]==saved.document['resources']
    assert [r['component'] for r in measured.document['resources'][1:]]==[
        'array.table.objects.object_id','array.table.objects.voxel_count',
        'array.table.objects.centroid_y_px','array.table.objects.centroid_x_px']
    assert measured.document['state']['scientificMeasurements']['format']==actions.MEASUREMENTS
    assert actions.table_csv(table).decode().splitlines()[2].startswith(str(2**63+7)+',2,')
    assert store.read(saved.id,'s1',revision=saved.revision)==saved


def test_native_export_preserves_endian_values_metadata_and_provenance(tmp_path):
    store,saved,data=labels(tmp_path)
    with actions.export_array(store,saved.id,'s1',saved.revision,'labels') as stream:
        with zipfile.ZipFile(stream) as archive:
            exported=np.load(io.BytesIO(archive.read('array.npy')),allow_pickle=False)
            metadata=json.loads(archive.read('metadata.json'))
    assert exported.dtype.str==data.dtype.str and exported.tobytes()==data.tobytes()
    assert metadata['document']['digest']==saved.digest
    assert metadata['scientific']['semantic']=='instance-labels'
    assert metadata['axes']==['y','x']


def test_cancel_and_stale_revision_preserve_accepted_content(tmp_path):
    store,saved,_=labels(tmp_path)
    def cancelled():raise InvalidProject('stopped')
    with pytest.raises(InvalidProject,match='stopped'):
        actions.measure(store,saved.id,'s1',saved.revision,'labels',check=cancelled)
    assert store.read(saved.id,'s1')==saved
    doc=saved.document;doc['state']['note']='Other work'
    newer=store.save(saved.id,'s1',doc,expected_revision=saved.revision)
    with pytest.raises(Conflict):actions.measure(store,saved.id,'s1',saved.revision,'labels')
    assert store.read(saved.id,'s1')==newer


def test_wrong_input_and_object_budget_refuse_without_partial_table(tmp_path,monkeypatch):
    store,saved,_=labels(tmp_path)
    with pytest.raises(InvalidProject):actions.measure(store,saved.id,'s1',saved.revision,'unretained')
    monkeypatch.setattr(actions,'MAX_OBJECTS',1)
    with pytest.raises(InvalidProject,match='10,000'):actions.measure(store,saved.id,'s1',saved.revision,'labels')
    assert store.read(saved.id,'s1')==saved


def test_time_labels_are_not_merged_as_one_spatial_object(tmp_path):
    store,saved,data=labels(tmp_path)
    draft=store.array_draft(saved.id,'s1',component='array.time',shape=data.shape,dtype=data.dtype.str,
        axes=['t','x'],chunks=[1,3],scientific={'semantic':'instance-labels'},max_written_bytes=data.nbytes)
    ref=fill(draft,data)
    doc=saved.document;doc['resources'].append(ref);doc['state']['scientificView']['inputs']['time']=ref
    saved=store.save(saved.id,'s1',doc,expected_revision=saved.revision)
    choices=actions.inventory(store,saved.id,'s1',saved.revision)['arrays']
    assert {row['key']:row['canMeasure'] for row in choices}=={'labels':True,'time':False}
    with pytest.raises(InvalidProject,match='Time and channel'):
        actions.measure(store,saved.id,'s1',saved.revision,'time')
    assert store.read(saved.id,'s1')==saved


def test_fragmented_accepted_array_measures_and_exports_within_read_limits(tmp_path):
    store=ProjectStore(tmp_path/'projects')
    original=store.create('s1',{'kind':'scene','title':'Fragmented labels','state':{},'resources':[],'runtimes':[]})
    data=np.ones((2,1024),dtype=np.uint8)
    draft=store.array_draft(original.id,'s1',component='array.labels',shape=data.shape,dtype=data.dtype.str,
        axes=['y','x'],chunks=[1,1],scientific={'semantic':'instance-labels'},max_written_bytes=data.nbytes)
    ref=fill(draft,data)
    doc=original.document;doc['resources']=[ref];doc['state']['scientificView']={'inputs':{'labels':ref}}
    saved=store.save(original.id,'s1',doc,expected_revision=1)
    measured=actions.measure(store,saved.id,'s1',saved.revision,'labels')
    assert actions.table(store,saved.id,'s1',measured.revision)['rows']==[['1',2048,.5,511.5]]
    with actions.export_array(store,saved.id,'s1',saved.revision,'labels') as stream:
        with zipfile.ZipFile(stream) as archive:
            assert np.array_equal(np.load(io.BytesIO(archive.read('array.npy')),allow_pickle=False),data)


def test_reopen_gives_identical_measurements_csv_and_native_zip(tmp_path):
    store,saved,data=labels(tmp_path)
    measured=actions.measure(store,saved.id,'s1',saved.revision,'labels')
    def exports(owner):
        with actions.export_array(owner,saved.id,'s1',measured.revision,'labels') as stream:
            archive=stream.read()
        return actions.table_csv(actions.table(owner,saved.id,'s1',measured.revision)),archive
    before=exports(store)
    assert exports(store)==before
    reopened=ProjectStore(tmp_path/'projects')
    assert exports(reopened)==before
    assert store.read(saved.id,'s1')==measured


def test_native_export_metadata_hashes_check_the_file_and_values(tmp_path):
    import hashlib
    store,saved,data=labels(tmp_path)
    with actions.export_array(store,saved.id,'s1',saved.revision,'labels') as stream:
        with zipfile.ZipFile(stream) as archive:
            npy=archive.read('array.npy');metadata=json.loads(archive.read('metadata.json'))
            stamps={info.date_time for info in archive.infolist()}
    exported=np.load(io.BytesIO(npy),allow_pickle=False)
    assert metadata['files']['array.npy']=={'sha256':hashlib.sha256(npy).hexdigest(),'bytes':len(npy)}
    assert metadata['values']=={'sha256':hashlib.sha256(exported.tobytes(order='C')).hexdigest(),'order':'C','bytes':data.nbytes}
    assert metadata['values']['sha256']==hashlib.sha256(data.tobytes()).hexdigest()
    assert (metadata['dtype'],metadata['shape'])==('>u8',[2,3])
    import time
    stamp=max(time.gmtime(saved.at)[:6],(1980,1,1,0,0,0))
    assert stamps=={(*stamp[:5],stamp[5]//2*2)}  # ZIP keeps even seconds


def test_corrected_labels_never_serve_the_earlier_labels_measurements(tmp_path):
    store,saved,data=labels(tmp_path)
    measured=actions.measure(store,saved.id,'s1',saved.revision,'labels')
    corrected=data.copy();corrected[1,0]=0
    draft=store.array_draft(saved.id,'s1',component='array.labels',shape=data.shape,dtype=data.dtype.str,
        axes=['y','x'],chunks=[1,3],scientific={'semantic':'instance-labels'},max_written_bytes=data.nbytes)
    ref=fill(draft,corrected)
    # A correction replaces the input and keeps the rest of the state, as ArrayPublication does.
    doc=measured.document;doc['resources']=[ref];doc['state']['scientificView']['inputs']['labels']=ref
    later=store.save(saved.id,'s1',doc,expected_revision=measured.revision)
    assert later.document['state']['scientificMeasurements']==measured.document['state']['scientificMeasurements']
    with pytest.raises(InvalidProject,match=f'revision {measured.revision}'):
        actions.table(store,saved.id,'s1',later.revision)
    assert actions.table(store,saved.id,'s1',measured.revision)['rows']==[['7',2,1.,1.],[str(2**63+7),2,0.,1.5]]
    remeasured=actions.measure(store,saved.id,'s1',later.revision,'labels')
    assert actions.table(store,saved.id,'s1',remeasured.revision)['rows']==[['7',1,1.,2.],[str(2**63+7),2,0.,1.5]]



def chunked_labels(tmp_path):
    rng=np.random.default_rng(7)
    data=rng.integers(0,20,size=(6,8,10),dtype=np.uint16).astype('>u2')
    store=ProjectStore(tmp_path/'projects')
    original=store.create('s1',{'kind':'scene','title':'Volume','state':{},'resources':[],'runtimes':[]})
    draft=store.array_draft(original.id,'s1',component='array.labels',shape=list(data.shape),dtype=data.dtype.str,
        axes=['z','y','x'],chunks=[2,4,5],scientific={'semantic':'instance-labels'},max_written_bytes=data.nbytes)
    ref=fill(draft,data)
    doc=original.document;doc['resources']=[ref];doc['state']['scientificView']={'inputs':{'labels':ref}}
    return store,store.save(original.id,'s1',doc,expected_revision=1),data


def counted_reads(monkeypatch):
    from orchestrator import scientific_arrays
    read=scientific_arrays.read_at;reads=[]
    def counting(folder,name,bound):
        if name.endswith('.bin'):reads.append(name)
        return read(folder,name,bound)
    monkeypatch.setattr(scientific_arrays,'read_at',counting)
    return reads


def test_measurement_reads_each_stored_chunk_once_and_matches_every_index(tmp_path,monkeypatch):
    store,saved,data=chunked_labels(tmp_path)
    from orchestrator import scientific_arrays
    labels={digest+'.bin' for digest in scientific_arrays._manifest(store,saved.id,saved.document['resources'][0])['parts'].values()}
    reads=counted_reads(monkeypatch)
    measured=actions.measure(store,saved.id,'s1',saved.revision,'labels')
    assert len([name for name in reads if name in labels])==12  # a 3 x 2 x 2 chunk grid, each chunk read once
    rows={row[0]:row[1:] for row in actions.table(store,saved.id,'s1',measured.revision)['rows']}
    for label in range(1,20):
        where=np.argwhere(data==label)
        assert rows[str(label)][0]==len(where)
        assert rows[str(label)][1:]==pytest.approx(list(where.mean(axis=0)),abs=0,rel=1e-12)


@pytest.mark.parametrize('working,reads',[(1 << 20,12),(160,24),(100,None)])
def test_export_reads_each_chunk_a_bounded_number_of_times_or_refuses(tmp_path,monkeypatch,working,reads):
    store,saved,data=chunked_labels(tmp_path)
    monkeypatch.setattr(actions,'WORKING_BYTES',working)  # one 8 x 10 row of '>u2' is 160 bytes
    counted=counted_reads(monkeypatch)
    if reads is None:
        with pytest.raises(InvalidProject,match='more than 4 times'):
            actions.export_array(store,saved.id,'s1',saved.revision,'labels')
        assert counted==[]
        return
    with actions.export_array(store,saved.id,'s1',saved.revision,'labels') as stream:
        with zipfile.ZipFile(stream) as archive:
            exported=np.load(io.BytesIO(archive.read('array.npy')),allow_pickle=False)
    assert len(counted)==reads and exported.dtype.str=='>u2' and np.array_equal(exported,data)
