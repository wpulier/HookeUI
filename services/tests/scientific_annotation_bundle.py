"""The private H07 integration, `mine/annotation-review`: its catalog entry, its adapter and how it is prepared.

Not a catalog model and no biological claim: the evidence probe whose factory opens `instance-labels` inputs as editable
napari Labels (`test_scientific_annotation_browser.py`, `test_scientific_annotate_worker.py`). Its lock is the pinned
H04/H06a scientific-host lock with napari's wheel at a local path (the same sha256 is checked).

    cd services && .venv/bin/python -m tests.scientific_annotation_bundle ROOT NATIVE_LOCK NAPARI_WHEEL

writes the bundle under ROOT/catalog, builds its environment with Hooke's own builder (`envs.build`, the development
workspace step) and writes ROOT/annotation-preparation.json, the file NAPARI_ANNOTATION_PREPARATION names. Building
an environment is a heavy run: take the slot first.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

KEY = 'mine/annotation-review'
MODEL = '''requires = { catalog = 3 }
id = "annotation-review"
name = "Private annotation review probe"
runs = "cpu"
version = "1"
licence = ["annotation-review"]
runner = "python-env"
env = { python = "3.12.11", lock = "env.lock", imports = ["napari", "magicgui", "vispy"] }
needs_to_run = ["install:annotation-review"]
component = { what = "Private synthetic annotation host (H07 evidence)", licence = "MIT", url = "https://github.com/wpulier/HookeUI", tier = "open" }
[[members]]
id = "viewer"
name = "Private annotation view"
adapter = "adapter:open_host"
bridge = "scientific-host-v1"
inputs = [{id = "project", type = "project.scientific"}]
outputs = [{id = "result", type = "project.scientific"}]
'''
ADAPTER = '''"""H07 evidence probe: every retained input opens as its declared napari layer and is bound as editable.

An `intensity` input opens as an Image, `instance-labels` or `class-labels` as Labels. A descriptor's own diagonal
calibration (its affine over the input's axes) becomes the layer's scale and translate before binding, so the bound
geometry is the input's.
"""
import numpy as np
from magicgui.widgets import Container

NAMES = {'volume': 'Native input', 'labels': 'Annotation labels'}


def _calibrate(source):
    calibration = (source.descriptor or {}).get('calibration')
    axes = list(source.axes)
    scale, translate = [1.0] * len(axes), [0.0] * len(axes)
    if calibration:
        for row, axis in zip(calibration['affine'], calibration['axes']):
            i = axes.index(axis)
            scale[i], translate[i] = float(row[calibration['axes'].index(axis)]), float(row[-1])
    return scale, translate


def open_host(context):
    for name in sorted(context.inputs):
        source = context.inputs[name]
        # The read returns the transport's immutable buffer; napari edits an owned array, as from any reader.
        data = np.array(source.read([[0, n] for n in source.shape]))
        scale, translate = _calibrate(source)
        meaning = (source.descriptor or {}).get('semantic')
        title = NAMES.get(name, name)
        if meaning in ('instance-labels', 'class-labels'):
            layer = context.viewer.add_labels(data, name=title, scale=scale, translate=translate)
        else:
            layer = context.viewer.add_image(data, name=title, scale=scale, translate=translate)
        context.bind_editable(layer=layer, input=name)
        context.bind_view(layer=layer, key=f'native-{name}')
    return Container()
'''


def lock(native_lock, wheel):
    """The pinned lock with napari's wheel at `wheel`; the wheel's sha256 must be the one the lock pins."""
    text, wheel = Path(native_lock).read_text(), Path(wheel).resolve()
    line = re.search(r'^napari @ file://\S+#sha256=([0-9a-f]{64})', text, re.M)
    assert line, 'the lock pins napari as a local wheel'
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == line.group(1), 'the wheel is not the pinned napari'
    return text.replace(line.group(0), f'napari @ file://{wheel}#sha256={line.group(1)}')


def prepare(root, native_lock, wheel):
    root = Path(root).resolve()
    folder = root / 'catalog' / 'mine' / 'models' / 'annotation-review'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'model.toml').write_text(MODEL)
    (folder / 'adapter.py').write_text(ADAPTER)
    (folder / 'env.lock').write_text(lock(native_lock, wheel))
    env = {'MICROMIND_CATALOG': str(root / 'catalog'), 'MICROMIND_ENVS': str(root / 'catalog/envs'),
           'MICROMIND_ASSETS': str(root), 'MICROMIND_HOME': str(root / 'home'), 'MICROMIND_KEYCHAIN': 'off',
           'MICROMIND_CATALOG_AUTO': '0', 'MICROMIND_KEYS_FILE': str(root / 'absent-keys')}
    os.environ.update(env)
    from gpu_worker import manifests as catalog
    from orchestrator import envs
    catalog.manifests.cache_clear()
    built = envs.build(catalog.get(KEY), os.environ, say=lambda s: print('  ', s, flush=True))
    preparation = {'key': f'{KEY}#viewer', 'folder': str(built),
                   'environment': {k: env[k] for k in ('MICROMIND_ASSETS', 'MICROMIND_CATALOG', 'MICROMIND_ENVS',
                                                       'MICROMIND_KEYCHAIN', 'MICROMIND_CATALOG_AUTO')}}
    (root / 'annotation-preparation.json').write_text(json.dumps(preparation, indent=1))
    return preparation


if __name__ == '__main__':
    print(json.dumps(prepare(*sys.argv[1:4])))
