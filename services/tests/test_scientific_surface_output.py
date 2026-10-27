"""Actual confined mesh publication, topology, native appearance and reopen."""
from pathlib import Path

import numpy as np
import pytest

from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_outputs import OutputSave
from orchestrator.scientific_project_review import review
from tests.test_scientific_arrays import fill
from tests.test_scientific_host_worker import actual_host  # noqa: F401

CREATE = """import numpy as np
import pandas as pd
from env_worker.scientific_surfaces import snapshot
viewer.layers.clear()
vertices=np.array([[0,0,0],[0,5,0],[0,0,5],[5,0,0]],dtype='>f8')
faces=np.array([[0,1,2],[0,2,3],[0,3,1],[1,3,2]],dtype='<i4')
values=np.array([1,2,3,4],dtype='>f4')
mesh=viewer.add_surface((vertices,faces,values),name='Measured mesh',
    scale=[2,.5,.25],translate=[10,20,30],axis_labels=['z','y','x'],units=['um']*3,
    colormap='magma',gamma=.8,shading='smooth',wireframe={'visible':True,'width':1.5},
    features=pd.DataFrame({'id':np.array([2**63+i for i in range(4)],dtype='uint64')}))
original_state=snapshot(mesh)
viewer.dims.ndisplay=3
viewer.reset_view()
creating_variable=42
"""


def source(store, prepared):
    bundle, operation = prepared['key'].split('#')
    runtime = {'bundle':bundle,'generation':Path(prepared['folder']).name,'operation':operation}
    saved = store.create('s1', {'kind':'scene','title':'Original data','state':{},'resources':[],'runtimes':[runtime]})
    data = np.arange(60,dtype='<u2').reshape(3,4,5)
    draft = store.array_draft(saved.id,'s1',component='array.source',shape=data.shape,dtype=data.dtype.str,
        axes=['z','y','x'],chunks=[1,4,5],scientific={'semantic':'intensity'},max_written_bytes=data.nbytes)
    ref = fill(draft,data); doc = saved.document; doc['resources']=[ref]
    doc['state']['scientificView']={'runtime':runtime,'inputs':{'volume':ref}}
    return store.save(saved.id,'s1',doc,expected_revision=1)


@pytest.mark.parametrize('appearance', ['values', 'colors', 'texture', 'texture-and-colors'])
def test_actual_surface_keeps_each_component_native_and_reopens_without_creator_code(tmp_path, actual_host, monkeypatch, appearance):
    store = ProjectStore(tmp_path/'retained'); saved = source(store,actual_host)
    with review(store,saved.id,'s1',saved.revision) as (_, worker):
        extras = {}
        if 'colors' in appearance:
            extras['vertex_colors'] = np.array([[1,0,0,1],[0,1,0,1],[0,0,1,1],[1,1,0,1]],dtype='<f4')
        if 'texture' in appearance:
            extras['texture'] = np.array([[[255,0,0],[0,255,0]],[[0,0,255],[255,255,0]]],dtype='uint8')
            extras['texcoords'] = np.array([[0,0],[0,1],[1,0],[1,1]],dtype='<f4')
        extra_source = '\n'.join(f'mesh.{name}=np.array({array.tolist()!r},dtype={array.dtype.str!r})' for name,array in extras.items())
        reply,_ = worker.request({'op':'python-resources','source':CREATE+'\n'+extra_source})
        assert not reply['console']['error'], reply['console']['error']
        inventory,_ = worker.request({'op':'layer-inventory'})
        layer = inventory['inventory']['layers'][0]
        expected = worker.describe_output(layer['id'])
        _, pixels = worker.request({'op':'render','width':256,'height':192})
        save = OutputSave(store,saved,'surface-output-123456789',layer['id'],{'semantic':'surface-mesh','axes':['z','y','x']})
        save.capture(worker)
        assert not store._folder(save.target).exists()
        # Failure after the atomic rename is a lost acknowledgement, not
        # permission to recapture changed live arrays or create another output.
        import orchestrator.scientific_outputs as module
        from orchestrator.scientific_saves import SaveUnavailable
        # A later component's manifest may fail after the first is finalized.
        # Retrying must reuse that receipt and all already captured native bytes.
        from orchestrator.scientific_arrays import ArrayDraft
        finish = ArrayDraft.finish
        calls = []
        def partial_manifest(draft):
            calls.append(draft)
            if len(calls) == 2: raise OSError('controlled component manifest failure')
            return finish(draft)
        with monkeypatch.context() as patch:
            patch.setattr(ArrayDraft,'finish',partial_manifest)
            with pytest.raises(SaveUnavailable): save.publish()
        assert len(save.references) == 1 and not store._folder(save.target).exists()
        rename = module.os.rename
        def lost_acknowledgement(source, target):
            rename(source,target)
            raise OSError('controlled lost publication acknowledgement')
        with monkeypatch.context() as patch:
            patch.setattr(module.os,'rename',lost_acknowledgement)
            with pytest.raises(SaveUnavailable): save.publish()
        reply,_ = worker.request({'op':'python-resources','source':'mesh.vertex_values[:]=99'})
        assert not reply['console']['error'], reply['console']['error']
        save.publish(); output = store.read(save.target,'s1'); save.close()
    assert worker.process.poll() is not None
    assert store.read(saved.id,'s1') == saved
    document = output.document
    refs = document['state']['scientificResource']['relations']
    assert set(refs) == {'vertices','faces','values'} | set(extras)
    assert document['state']['scientificResource']['semantic'] == 'surface-mesh'
    from gpu_worker.semantic_resources import parse_descriptor
    parse_descriptor(document['state']['scientificResource'])
    expected_arrays = {'vertices':np.array([[0,0,0],[0,5,0],[0,0,5],[5,0,0]],dtype='>f8'),
        'faces':np.array([[0,1,2],[0,2,3],[0,3,1],[1,3,2]],dtype='<i4'),
        'values':np.array([1,2,3,4],dtype='>f4'), **extras}
    for name, data in expected_arrays.items():
        found = store.array_source(output.id,'s1',1,refs[name])
        actual = found.read(tuple(slice(0,n) for n in found.shape))
        assert actual.dtype.str == data.dtype.str and actual.tobytes() == data.tobytes()
    with review(store,output.id,'s1',1) as (_, worker):
        inventory,_ = worker.request({'op':'layer-inventory'})
        assert worker.describe_output(inventory['inventory']['layers'][0]['id']) == expected
        reply,_ = worker.request({'op':'python-resources','source':"""m=viewer.layers[0]
assert 'creating_variable' not in globals()
assert str(m.features['id'].dtype)=='uint64'
assert list(m.features['id'])==[2**63+i for i in range(4)]
assert m.shading=='smooth' and m.wireframe.visible and m.wireframe.width==1.5
viewer.dims.ndisplay=3
viewer.reset_view()
"""})
        assert not reply['console']['error'],reply['console']['error']
        _, reopened = worker.request({'op':'render','width':256,'height':192})
        assert reopened == pixels
    assert worker.process.poll() is not None


def test_surface_partial_transfer_cleans_all_components_and_preserves_source(tmp_path, actual_host, monkeypatch):
    import orchestrator.scientific_outputs as module
    store = ProjectStore(tmp_path/'retained'); saved = source(store,actual_host)
    with review(store,saved.id,'s1',saved.revision) as (_,worker):
        reply,_ = worker.request({'op':'python-resources','source':CREATE})
        assert not reply['console']['error'],reply['console']['error']
        inventory,_ = worker.request({'op':'layer-inventory'}); layer = inventory['inventory']['layers'][0]
        save = OutputSave(store,saved,'surface-transfer-123456789',layer['id'],{'semantic':'surface-mesh','axes':['z','y','x']})
        answer = module.OutputGrant.answer; received = []
        def partial(grant,header,payload):
            if received: raise OSError('controlled component-transfer failure')
            result = answer(grant,header,payload); received.append(grant.draft.scientific['semantic']); return result
        with monkeypatch.context() as patch:
            patch.setattr(module.OutputGrant,'answer',partial)
            with pytest.raises(RuntimeError,match='transfer failed'): save.capture(worker)
        assert len(received)==1 and worker.process.poll() is not None
        assert not store._folder(save.target).exists()
        assert not list(store.root.glob('.scientific-output-*'))
        assert store.read(saved.id,'s1')==saved
    # Corrected retry starts a new owned session; the original input remains usable.
    with review(store,saved.id,'s1',saved.revision) as (_,worker):
        reply,_=worker.request({'op':'python-resources','source':CREATE})
        assert not reply['console']['error'],reply['console']['error']
        inventory,_=worker.request({'op':'layer-inventory'});layer=inventory['inventory']['layers'][0]
        corrected=OutputSave(store,saved,'surface-corrected-123456789',layer['id'],{'semantic':'surface-mesh','axes':['z','y','x']})
        corrected.capture(worker)
        from orchestrator.scientific_saves import SaveUnavailable
        with pytest.raises(SaveUnavailable,match='stopped'): corrected.publish(lambda:True)
        assert not store._folder(corrected.target).exists()
        assert store.read(saved.id,'s1')==saved
        corrected.publish();corrected.close()
        assert store.read(corrected.target,'s1').revision==1
