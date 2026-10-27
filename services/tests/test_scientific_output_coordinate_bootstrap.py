"""Actual closed bootstrap imports with synthetic layers; no Qt/model/native execution."""
import subprocess
import sys

from orchestrator.scientific_host import _bootstrap


PROBE = r'''
import sys, threading
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import numpy as np
from env_worker.scientific_host import ReviewSession, Context
from env_worker.scientific_layer_exports import EditableLayers
from env_worker.scientific_view_bindings import ViewBindings

image = SimpleNamespace(data=np.arange(6,dtype='uint8').reshape(2,3), _type_string='image', ndim=2,
    rgb=False, multiscale=False, axis_labels=('y','x'), units=('um','um'),
    name='Input', visible=True, opacity=1., data_to_world=lambda point:tuple(float(v) for v in point))
output = SimpleNamespace(**vars(image)); output._type_string='labels'; output.data=image.data.astype('uint16')
legacy = SimpleNamespace(**vars(output))
reference={'session':'s1','id':'p1','component':'array.image','revision':'a'*64}
source=SimpleNamespace(shape=image.data.shape,dtype=image.data.dtype.str,axes=('y','x'),reference=reference,
    descriptor={'semantic':'intensity','calibration':{'space':{'session':'s1','id':'p1','revision':1},
        'axes':['y','x'],'units':['micrometer','micrometer'],'affine':[[1.,0.,0.],[0.,1.,0.]]}})
accepted=image.data.copy()
source.read=lambda bounds, *, view=False: accepted[tuple(slice(*pair) for pair in bounds)].copy()
viewer=SimpleNamespace(layers=[image,output,legacy])
host=object.__new__(ReviewSession); host._thread=threading.get_ident(); host._closed=False
host.viewer=viewer; host.editable=EditableLayers(viewer,{'image':source})
host.editable.bind(layer=image,input='image');host.editable.seal()
host.view_bindings=ViewBindings(viewer);host.view_bindings.bind(layer=image,key='image');host.view_bindings.seal()
host._layers=[('a'*32,output),('b'*32,legacy)];host._layer_rows=lambda:None
host.bind_view_output(version=1,layer=legacy,key='legacy')
meaning={'semantic':'instance-labels','axes':['y','x']}
host.bind_view_output(version=2,layer=output,key='output',meaning=meaning,coordinates_from=image)
assert 'coordinatesFrom' not in host.describe_output('b'*32)
info=host.describe_output('a'*32)
assert info['coordinatesFrom']['reference']==reference
from env_worker import scientific_output_coordinates as coordinates
assert Path(coordinates.__file__).parent == Path(sys.argv[1])/'env_worker'
# Exercise the imported alias/calibration path too, including the existing Pint dependency.
assert coordinates.grid(info,meaning,source)==source.descriptor['calibration']
assert Context(None,{},None,None).output_binding_version==2
print('v1, v2, exact source proof and existing Pint aliases work in the closed bootstrap')
'''


def test_isolated_bootstrap_supports_legacy_and_declared_coordinate_outputs(tmp_path):
    bootstrap = tmp_path/'bootstrap'; _bootstrap(bootstrap)
    result = subprocess.run([sys.executable,'-I','-B','-c',PROBE,str(bootstrap)],
        cwd=tmp_path,capture_output=True,text=True,timeout=15)
    assert result.returncode == 0, result.stdout+result.stderr
    assert 'v1, v2, exact source proof' in result.stdout
