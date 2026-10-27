"""Real confined reader access; synthetic input, not ordinary-user qualification."""
import io,hashlib
import numpy as np
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_project_review import review
from orchestrator.scientific_outputs import OutputSave
from tests.test_scientific_surface_output import source
from tests.test_scientific_host_worker import actual_host  # noqa: F401


def test_actual_confined_reader_creates_and_reopens_native_output(tmp_path,actual_host):
    store=ProjectStore(tmp_path/'retained');original=source(store,actual_host)
    values=np.arange(60,dtype='>u2').reshape(3,4,5);stream=io.BytesIO();np.save(stream,values,allow_pickle=False);body=stream.getvalue()
    draft=store.file_draft(original.id,'s1',component='file.specimen',name='specimen.npy',size=len(body),
        sha256=hashlib.sha256(body).hexdigest(),max_written_bytes=len(body));draft.write(body);ref=draft.finish()
    doc=original.document;doc['resources'].append(ref);doc['state']['scientificView']['files']={'specimen':ref}
    saved=store.save(original.id,'s1',doc,expected_revision=original.revision);draft.close()
    with review(store,saved.id,'s1',saved.revision) as (_,worker):
        result,_=worker.request({'op':'python-resources','source':"""import numpy as np
selected=files['specimen']
assert selected.name=='specimen.npy'
read_array=np.load(selected,allow_pickle=False)
assert read_array.dtype.str=='>u2' and np.array_equal(read_array,np.arange(60).reshape(3,4,5))
viewer.layers.clear()
viewer.add_image(read_array,name='Selected scientific file')
print('PASS: selected bytes read through the scoped broker, exact dtype preserved')
"""})
        assert not result['console']['error'],result['console']['error']
        assert 'PASS:' in result['console']['stdout']
        inventory,_=worker.request({'op':'layer-inventory'});layer=inventory['inventory']['layers'][0]
        save=OutputSave(store,saved,'file-reader-output',layer['id'],{'semantic':'intensity','axes':['z','y','x']})
        save.capture(worker);save.publish();target=save.target;save.close()
    with review(store,target,'s1',1) as (_,worker):
        result,_=worker.request({'op':'python-resources','source':"""import numpy as np
assert not files and 'read_array' not in globals()
assert viewer.layers[0].data.dtype.str=='>u2'
assert np.array_equal(viewer.layers[0].data,np.arange(60).reshape(3,4,5))
"""})
        assert not result['console']['error'],result['console']['error']
        frame,pixels=worker.request({'op':'render','width':320,'height':240});assert pixels
    assert store.read(original.id,'s1',revision=original.revision)==original
    assert store.file_source(saved.id,'s1',saved.revision,ref).read(0,len(body))==body
