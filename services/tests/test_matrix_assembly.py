"""Tiny real AnnData assembly checks; no full-size or scientific-quality claim."""
import numpy as np
import pytest
from scipy import sparse
from scientific_compute import engine
from scientific_compute.catalog import parameters
from tests.test_matrix_research_capabilities import adata, output


def inputs(tmp_path):
    left=adata(tmp_path/'left.h5ad',[[1,2],[3,4]],['a']*2,['x']*2,genes=['g1','g2'])
    right=adata(tmp_path/'right.h5ad',[[5,6]],['b'],['y'],genes=['g1','g2'])
    anndata=pytest.importorskip('anndata')
    data=anndata.read_h5ad(right);data.obs_names=['other'];data.write_h5ad(right)
    return {'left':left,'right':right}


def assemble(tmp_path, source, **params):
    return engine.OPERATIONS['matrix-concatenate'](source,parameters('matrix-concatenate',params),
        output(tmp_path,'assembly'),lambda:None)


def test_exact_rows_axes_labels_and_inputs_survive(tmp_path):
    anndata=pytest.importorskip('anndata')
    source=inputs(tmp_path); original={k:p.read_bytes() for k,p in source.items()}
    path,meta=assemble(tmp_path,source)
    result=anndata.read_h5ad(path)
    np.testing.assert_array_equal(result.X.toarray(),[[1,2],[3,4],[5,6]])
    assert sparse.issparse(result.X) and result.X.dtype==np.float32
    assert result.obs_names.tolist()==['cell0','cell1','other']
    assert result.obs.context.tolist()==['a','a','b']
    assert result.obs.target_gene.tolist()==['x','x','y']
    assert result.var_names.tolist()==['g1','g2']
    assert meta['storedEntries']==6 and meta['maximumCellCounts']==11
    assert meta['shape']==[3,2] and not meta['packagedForSubmission']
    assert all(p.read_bytes()==original[k] for k,p in source.items())


@pytest.mark.parametrize('fault',['duplicate','gene-order','fraction','negative','cell-limit','entry-limit','precision'])
def test_incompatible_or_excessive_data_refuses(tmp_path,fault):
    anndata=pytest.importorskip('anndata')
    source=inputs(tmp_path); data=anndata.read_h5ad(source['right']);params={}
    if fault=='duplicate':data.obs_names=['cell0']
    if fault=='gene-order':data.var_names=['g2','g1']
    if fault=='fraction':data.X[0,0]=.5
    if fault=='negative':data.X[0,0]=-1
    if fault=='precision':
        data.X=data.X.astype(np.float64);data.X[0,0]=2**24+1
        params['maximum_cell_counts']=100000000
    if fault=='cell-limit':params['maximum_cell_counts']=10
    if fault=='entry-limit':params['maximum_stored_entries']=5
    data.write_h5ad(source['right'])
    with pytest.raises(engine.Refused):assemble(tmp_path,source,**params)


def test_unpreserved_annotations_are_refused(tmp_path):
    anndata=pytest.importorskip('anndata');source=inputs(tmp_path)
    data=anndata.read_h5ad(source['right']);data.obs['annotation']=['keep me'];data.write_h5ad(source['right'])
    with pytest.raises(engine.Refused,match='extra annotations'):assemble(tmp_path,source)


def test_assembly_detects_changed_values_even_when_shape_nnz_and_max_match(tmp_path,monkeypatch):
    anndata=pytest.importorskip('anndata');source=inputs(tmp_path);original=engine._concat
    def corrupt(parts,path,check):
        original(parts,path,check)
        data=anndata.read_h5ad(path);data.X[0,0]=2;data.X[0,1]=1;data.write_h5ad(path)
    monkeypatch.setattr(engine,'_concat',corrupt)
    with pytest.raises(engine.Refused,match='changed stored counts'):assemble(tmp_path,source)


@pytest.mark.parametrize('fault',['pointer','index','wide-label','oversized-block','csc'])
def test_storage_preflight_refuses_before_anndata_decoding(tmp_path,monkeypatch,fault):
    anndata=pytest.importorskip('anndata');h5py=pytest.importorskip('h5py')
    from scientific_compute import bounded_h5
    source=inputs(tmp_path)
    if fault=='csc':
        data=anndata.read_h5ad(source['left']);data.X=data.X.tocsc();data.write_h5ad(source['left'])
    with h5py.File(source['left'],'r+') as file:
        if fault=='pointer':file['X/indptr'][0]=1
        if fault=='index':file['X/indices'][0]=99
        if fault=='wide-label':
            key=file['var'].attrs['_index'];del file['var'][key]
            file['var'].create_dataset(key,data=np.array(['g1','g2'],dtype='S2048'))
    if fault=='oversized-block':monkeypatch.setattr(bounded_h5,'BLOCK_BYTES',32)
    monkeypatch.setattr(anndata,'read_h5ad',lambda *a,**k:pytest.fail('Unvalidated storage reached AnnData decoding'))
    with pytest.raises(engine.Refused):assemble(tmp_path,source)
