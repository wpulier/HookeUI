"""H05 (P2) production-path browser evidence: the installed Studio build, a fresh browser profile, a real confined
napari worker.

A private Hooke (its own home, assets, empty keys, no camera, no keychain, free ports) serves the production Studio
build. A retained 3D volume and a retained surface are in its Project Data. scientific_browser_p2.mjs opens each from
Data, opens its private session and operates the ordinary View controls, one recipe per child. Each child is its own
parametrized case.

The oracle is the pinned reference. Appearance replay records the complete submitted transaction; other controls
record their upstream setters and current values before Apply. After each recipe it reads the session's napari state
through Studio's own Python console. scientific_reference_replay.py then rebuilds the same layers from their layer-data
tuples in a plain pinned-napari process, replays the equivalents, and the two states must be equal, recipe by recipe.
A product-level negative opens a second copy of the volume, makes Hooke's worker drop attenuation (its own apply path),
and the attenuation child must then fail.

Needs NAPARI_NATIVE_HOST_PREPARATION (the prepared private review bundle), NAPARI_REFERENCE_PYTHON (the pinned
reference runtime) and HOOKE_BROWSER_CONFIG (a JSON file naming Playwright's `playwright` module and a Chromium `chromium`
executable). Without them every case skips and the acceptance runner reports these children missing.
"""
import fcntl
import itertools
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

import numpy as np
import pytest

from tests.shared_run import shared_result
from tests import test_scientific_reference_comparison as comparison

SERVICES = Path(__file__).resolve().parents[1]
ROOT = SERVICES.parent
TITLE = 'H05 volume'
NEGATIVE = 'H05 volume (negative)'  # its own copy: a second profile cannot reopen the first profile's session
# A product-level negative: Hooke's worker drops attenuation (its own apply path, not napari's), so the attenuation
# child must fail both the control's round trip and the reference comparison.
DROP_ATTENUATION = '''
import env_worker.scientific_image_appearance as _appearance
_kept_apply = _appearance.apply
def _drops_attenuation(layer, value):
    _kept_apply(layer, {**value, 'attenuation': float(layer.attenuation)})
_appearance.apply = _drops_attenuation
'''
ATTENUATION = 'P2.volume-parameters.image-attenuation'
SURFACE = 'H05 surface'
MESH = 'Measured mesh'
DRIVER = Path(__file__).with_name('scientific_browser_p2.mjs')
RECIPES = Path(__file__).with_name('scientific_comparison_recipes.py')
REPLAY = Path(__file__).with_name('scientific_reference_replay.py')
SURFACE_CHILDREN = [
    'P2.surface-render.shading-none', 'P2.surface-render.shading-flat', 'P2.surface-render.shading-smooth',
    'P2.surface-render.surface-shading', 'P2.surface-render.qtshadingcomboboxcontrol',
    'P2.surface-render.surface-wireframe', 'P2.surface-render.wireframe-surfacewireframe-visible',
    'P2.surface-render.wireframe-surfacewireframe-width', 'P2.surface-render.wireframe-surfacewireframe-color',
    'P2.surface-render.surface-normals', 'P2.surface-render.surface-normals-surfacenormals-face',
    'P2.surface-render.surface-normals-surfacenormals-vertex', 'P2.surface-render.normals-normals-visible',
    'P2.surface-render.normals-normals-length', 'P2.surface-render.normals-normals-width',
    'P2.surface-render.normals-normals-color',
]
# The children the driver has recipes for (it reports the same list; a test keeps them equal).
CHILDREN = sorted([
    *[f'P2.volume-modes.imagerendering-{m}' for m in ('translucent', 'additive', 'iso', 'mip', 'minip', 'attenuated-mip', 'average')],
    'P2.volume-modes.image-rendering',
    'P2.volume-parameters.image-attenuation', 'P2.volume-parameters.image-iso-threshold',
    'P2.volume-parameters.image-interpolation3d',
    'P2.camera.camera-angles', 'P2.camera.camera-center', 'P2.camera.camera-zoom', 'P2.camera.camera-perspective',
    'P2.camera.camera-orientation',
    *[f'P2.camera.api-{a}axisorientation-{v}' for a, values in (('depth', ('away', 'towards')), ('vertical', ('up', 'down')),
                                                                ('horizontal', ('left', 'right'))) for v in values],
    'P2.clipping-planes.volumedepiction-plane', 'P2.clipping-planes.volumedepiction-volume',
    'P2.clipping-planes.scalarfieldbase-depiction',
    'P2.clipping-planes.plane-position', 'P2.clipping-planes.plane-normal', 'P2.clipping-planes.scalarfieldbase-plane',
    'P2.clipping-planes.slicing-slicingplane-thickness',
    'P2.clipping-planes.clipping-clippingplane-enabled', 'P2.clipping-planes.layer-experimental-clipping-planes',
    *SURFACE_CHILDREN,
])


def _free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def _volume():
    """A 3D volume: a blob and a bright block."""
    z, y, x = np.mgrid[0:12, 0:40, 0:48]
    volume = (1000 * np.exp(-(((z - 6) / 4) ** 2 + ((y - 20) / 9) ** 2 + ((x - 30) / 10) ** 2))).astype('<u2')
    volume[2:4, 5:10, 5:12] = 3000
    return volume


def _volume_document(home, session, runtime, title=TITLE):
    """The volume, retained in this home's Project Data."""
    from orchestrator.scientific_projects import ProjectStore
    store = ProjectStore(home / 'results/projects')
    volume = _volume()
    first = store.create(session, {'kind': 'scene', 'title': title, 'state': {}, 'resources': [], 'runtimes': [runtime]})
    draft = store.array_draft(first.id, session, component='array.volume', shape=list(volume.shape), dtype='<u2',
                              axes=['z', 'y', 'x'], chunks=[6, 40, 48], scientific={'semantic': 'intensity'},
                              max_written_bytes=volume.nbytes)
    for index in itertools.product(*(range(n) for n in draft.grid)):
        draft.write(index, np.ascontiguousarray(volume[tuple(slice(i * c, (i + 1) * c) for i, c in zip(index, draft.chunks))]))
    reference = draft.finish()
    document = first.document
    document['resources'] = [reference]
    document['state']['scientificView'] = {'runtime': runtime, 'inputs': {'volume': reference}}
    return store.save(first.id, session, document, expected_revision=1)


def _surface_document(home, session, runtime):
    """A retained surface mesh in this home's Project Data, captured from the real worker as an ordinary output.

    Runs in a child process with the private Hooke's environment (its home and the prepared catalog)."""
    from orchestrator.scientific_outputs import OutputSave
    from orchestrator.scientific_project_review import review
    from orchestrator.scientific_projects import ProjectStore
    store = ProjectStore(home / 'results/projects')
    first = store.create(session, {'kind': 'scene', 'title': 'H05 surface source', 'state': {}, 'resources': [],
                                   'runtimes': [runtime]})
    data = np.zeros((3, 4, 5), '<u2')
    draft = store.array_draft(first.id, session, component='array.source', shape=list(data.shape), dtype='<u2',
                              axes=['z', 'y', 'x'], chunks=[3, 4, 5], scientific={'semantic': 'intensity'},
                              max_written_bytes=data.nbytes)
    draft.write((0, 0, 0), data)
    document = first.document
    document['resources'] = [draft.finish()]
    document['state']['scientificView'] = {'runtime': runtime, 'inputs': {'volume': document['resources'][0]}}
    saved = store.save(first.id, session, document, expected_revision=1)
    with review(store, saved.id, session, saved.revision) as (_, worker):
        reply, _ = worker.request({'op': 'python-resources', 'source': f"""import numpy as np
viewer.layers.clear()
vertices = np.array([[0, 0, 0], [0, 20, 0], [0, 0, 20], [20, 0, 0], [12, 12, 12]], dtype='<f8')
# Outward winding: inward normals disappear behind this closed mesh and cannot
# establish that an ordinary normal-length control visibly changes its render.
faces = np.array([[2, 1, 0], [3, 2, 0], [1, 3, 0], [4, 3, 1], [2, 4, 1], [3, 4, 2]], dtype='<i4')
values = np.array([1, 2, 3, 4, 5], dtype='<f4')
viewer.add_surface((vertices, faces, values), name={MESH!r}, colormap='viridis', shading='flat')
viewer.dims.ndisplay = 3
viewer.camera.angles = (15, 30, 60)
"""})
        if reply['console']['error']:
            raise RuntimeError(reply['console']['error'])
        inventory, _ = worker.request({'op': 'layer-inventory'})
        save = OutputSave(store, saved, 'h05-surface-output', inventory['inventory']['layers'][0]['id'],
                          {'semantic': 'surface-mesh', 'axes': ['z', 'y', 'x']})
        save.capture(worker)
        save.publish()
        output = store.read(save.target, session)
        save.close()
    document = output.document
    document['title'] = SURFACE
    store.save(output.id, session, document, expected_revision=output.revision)


def _studio_built():
    from orchestrator.build_info import runtime_identity
    if not runtime_identity()['matches']:
        subprocess.run(['npm', 'run', '--silent', 'build'], cwd=ROOT / 'apps/studio', check=True, timeout=900,
                       stdout=subprocess.DEVNULL)
    if not runtime_identity()['matches']:
        raise RuntimeError('the Studio build does not match this checkout')


def _replay(python, one, root):
    """The reference: the same layers rebuilt in plain pinned napari, the equivalents replayed, a state per recipe."""
    np.save(root / 'volume.npy', _volume())
    request = {'profile': comparison.PROFILE, 'canvas_size': one['canvasSize'], 'recipes': str(RECIPES), 'source_paths': sorted(comparison.PINNED_SOURCES), 'initial': one['initial'], 'ops': one['ops'],
               'arrays': {'Native input': str(root / 'volume.npy')},
               'checkpoints': [{'id': c['id'], 'ops': c['ops']} for c in one['checkpoints'] if c['id'] != 'initial']}
    folder = root / f'replay-{len(list(root.glob("replay-*")))}'
    folder.mkdir()
    environment = {'PATH': '/usr/bin:/bin', 'HOME': str(folder), 'TMPDIR': str(folder),
                   'NAPARI_CONFIG': str(folder / 'napari.yaml'), 'QT_API': 'pyside6', 'QT_QPA_PLATFORM': 'cocoa',
                   'NAPARI_ASYNC': '0', 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
                   'VECLIB_MAXIMUM_THREADS': '1', 'NUMBA_NUM_THREADS': '1'}
    done = subprocess.run([python, '-I', str(REPLAY)], input=json.dumps(request), capture_output=True, text=True,
                          cwd=folder, env=environment, timeout=900)
    if done.returncode:
        raise RuntimeError(f'the reference replay exited {done.returncode}: {done.stderr[-4000:]}')
    return json.loads(done.stdout)


def _run(prepared, browser, python, root):
    _studio_built()
    home, assets = root / 'home', root / 'assets'
    home.mkdir()
    assets.mkdir()
    (root / 'keys.txt').write_text('')
    bundle, operation = prepared['key'].split('#')
    runtime = {'bundle': bundle, 'generation': Path(prepared['folder']).name, 'operation': operation}
    session = time.strftime('%Y-%m-%d')
    _volume_document(home, session, runtime)
    _volume_document(home, session, runtime, NEGATIVE)
    port = _free_port()
    environment = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'USER', 'LANG') if key in os.environ}
    environment.update(prepared['environment'])
    environment.update(HOME=str(root), MICROMIND_HOME=str(home), MICROMIND_ASSETS=str(assets),
                       MICROMIND_KEYS_FILE=str(root / 'keys.txt'), CAMERA_SOURCE_FILE=str(root / 'camera-source.json'),
                       MICROMIND_KEYCHAIN='off', MICROMIND_CATALOG_AUTO='0', CAMERA_NO_PHONE='1', HF_HUB_OFFLINE='1',
                       PYTHONUTF8='1')
    made = subprocess.run([sys.executable, '-c', 'import json, sys; from pathlib import Path; '
                           'from tests.test_scientific_browser_p2 import _surface_document as make; '
                           'make(Path(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3]))',
                           str(home), session, json.dumps(runtime)],
                          cwd=SERVICES, env=environment, capture_output=True, text=True, timeout=600)
    if made.returncode != 0:
        return {'failure': 'the retained surface could not be made: ' + made.stderr[-4000:]}
    log = (root / 'orchestrator.log').open('wb')
    server = subprocess.Popen([sys.executable, '-m', 'orchestrator.server', '--port', str(port),
                               '--bridge-url', f'http://127.0.0.1:{_free_port()}'],
                              cwd=SERVICES, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        url = f'http://127.0.0.1:{port}/'
        for _ in range(360):
            try:
                if urllib.request.urlopen(url, timeout=2).status == 200:
                    break
            except OSError:
                pass
            if server.poll() is not None:
                return {'failure': 'the private orchestrator exited: ' + (root / 'orchestrator.log').read_text()[-4000:]}
            time.sleep(0.5)
        else:
            return {'failure': 'the private orchestrator did not answer within 180 s'}
        result = {'opened': {}, 'recipes': [], 'results': {}, 'compared': {}, 'ops': {}, 'runtimes': {}, 'profiles': {}, 'cleanup': {}}
        (root / 'drop-attenuation.py').write_text(DROP_ATTENUATION)
        sources = root / 'pinned-sources.json'
        sources.write_text(json.dumps(sorted(comparison.PINNED_SOURCES)))
        for document in (TITLE, SURFACE, NEGATIVE):  # one fresh browser profile per document, each from Project Data
            negative = document == NEGATIVE
            selection = (ATTENUATION, str(root / 'drop-attenuation.py')) if negative else ('-', '')
            driven = subprocess.run(['node', str(DRIVER), browser['playwright'], browser['chromium'],
                                     str(root / f'profile-{len(result["opened"])}'), url, document, str(RECIPES),
                                     *selection, str(sources)],
                                    capture_output=True, text=True, timeout=1800, env=environment)
            if driven.returncode != 0:
                return {'failure': f'the browser driver exited {driven.returncode}: {driven.stderr[-4000:]}'}
            # Preserve complete observations even if replay/admission fails later.
            (root / f'driver-{len(result["opened"])}.json').write_text(driven.stdout)
            one = json.loads(driven.stdout)
            result['cleanup'][document] = one.get('cleanup')
            if not (one.get('cleanup') or {}).get('ok'):
                return {'failure': f'the owned session did not stop: {one.get("cleanup")}', 'partial': result}
            if negative:  # kept apart: its states and results are the negative's evidence, never a child's
                reference = _replay(python, one, root) if one['initial'] is not None else {'states': {}}
                mine = next((c for c in one['checkpoints'] if c['id'] == ATTENUATION), None)
                result['negative'] = {'opened': one['opened'], 'result': one['results'].get(ATTENUATION),
                                      'hooke': mine and mine['state'], 'reference': reference['states'].get(ATTENUATION),
                                      'ops': one['ops']}
                result['runtimes'][document] = {'hooke': one.get('runtime'), 'reference': reference.get('runtime')}
                result['profiles'][document] = {'hooke': one.get('profile'), 'reference': reference.get('profile')}
                continue
            result['opened'][document] = one['opened']
            result['recipes'] = sorted(result['recipes'] + one['recipes'])
            result['results'].update(one['results'])
            result['ops'][document] = one['ops']
            if one['initial'] is not None:
                reference = _replay(python, one, root)
                result['runtimes'][document] = {'hooke': one.get('runtime'), 'reference': reference['runtime']}
                result['profiles'][document] = {'hooke': one.get('profile'), 'reference': reference.get('profile')}
                done = 0
                for checkpoint in one['checkpoints']:
                    result['compared'][checkpoint['id'] if checkpoint['id'] != 'initial' else f'initial:{document}'] = {
                        'hooke': checkpoint['state'], 'reference': reference['states'].get(checkpoint['id']),
                        'own_ops': one['ops'][done:checkpoint['ops']]}  # what this recipe itself applied
                    done = checkpoint['ops']
        return result
    finally:
        try:
            os.killpg(server.pid, 15)
            server.wait(timeout=30)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            os.killpg(server.pid, 9)
        log.close()


@pytest.fixture(scope='session')
def browser_run(tmp_path_factory):
    location, browser = os.environ.get('NAPARI_NATIVE_HOST_PREPARATION'), os.environ.get('HOOKE_BROWSER_CONFIG')
    python = os.environ.get('NAPARI_REFERENCE_PYTHON')
    if not location or not browser or not python:
        pytest.skip('requires a prepared review bundle (NAPARI_NATIVE_HOST_PREPARATION), the pinned reference runtime '
                    '(NAPARI_REFERENCE_PYTHON) and a browser (HOOKE_BROWSER_CONFIG)')
    shared = shared_result(tmp_path_factory, 'browser-p2')
    with open(shared.with_suffix('.lock'), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not shared.exists():
            shared.write_text(json.dumps(_run(json.loads(Path(location).read_text()), json.loads(Path(browser).read_text()),
                                              python, tmp_path_factory.mktemp('browser-p2'))))
    result = json.loads(shared.read_text())
    if 'failure' in result:
        pytest.fail(result['failure'])
    return result


@pytest.mark.parametrize('ident', CHILDREN)
def test_p2_child(ident, browser_run):
    for opened in browser_run['opened'].values():
        assert opened.get('three') and opened.get('from') == 'Project Data', browser_run['opened']
    assert browser_run['recipes'] == CHILDREN  # the driver ran exactly these recipes
    result = browser_run['results'][ident]
    assert result['ok'], result.get('error')
    for document in browser_run['opened']:  # the reference rebuilt the session's layers exactly
        start = browser_run['compared'][f'initial:{document}']
        assert start['hooke'] == start['reference'], start
    compared = browser_run['compared'][ident]
    assert compared['own_ops'], 'this recipe applied no upstream equivalent of its own'
    assert compared['hooke'] == compared['reference'], compared


def test_both_p2_sides_ran_approved_runtimes(browser_run):
    runtimes = browser_run.get('runtimes', {})
    assert set(runtimes) == {TITLE, SURFACE, NEGATIVE}, 'each actual session and negative needs runtime observations'
    for document, sides in runtimes.items():
        cleanup = browser_run.get('cleanup', {}).get(document, {})
        assert cleanup.get('ok') and cleanup.get('stopped'), 'each opened session must stop before the next document'
        profiles = browser_run.get('profiles', {}).get(document, {})
        assert all(isinstance(profiles.get(side), dict) for side in ('hooke', 'reference')), 'GUI profiles missing'
        comparison._assert_gui_profile({side: {'profile': profiles[side]} for side in profiles}, ndisplay=3)
        assert all(isinstance(sides.get(side), dict) for side in ('hooke', 'reference'))
        comparison._assert_pinned_runtime(
            {side: {'runtime': sides[side]} for side in ('hooke', 'reference')})


def test_a_worker_that_drops_attenuation_fails_the_attenuation_child(browser_run):
    negative = browser_run['negative']
    assert negative['opened'].get('three') and not negative['opened'].get('error'), negative['opened']
    assert browser_run['results'][ATTENUATION]['ok'], 'the unmodified attenuation control must work'
    submitted = [op[2]['attenuation'] for op in negative.get('ops', [])
                 if op[:2] == ['image-presentation', 'Native input'] and 'attenuation' in op[2]]
    assert submitted, negative
    assert negative.get('hooke') and negative.get('reference'), 'the negative must observe both actual states'
    from tests.scientific_comparison_recipes import normalize
    actual = negative['hooke']['Native input']['attenuation']
    expected = negative['reference']['Native input']['attenuation']
    assert expected == normalize(submitted[-1]) and actual != expected, negative


def test_the_driver_has_a_recipe_for_exactly_these_children():
    source = DRIVER.read_text()
    for ident in CHILDREN:
        assert f"'{ident}'" in source or ident.startswith(('P2.volume-modes.imagerendering-', 'P2.camera.api-',
                                                                       'P2.surface-render.shading-'))
    from bench import scientific_harnesses as harnesses
    from bench.scientific_inventory import INVENTORY
    rules = json.loads((INVENTORY / 'rules.json').read_text())
    assert next(h['ids'] for h in rules['harnesses'] if h['name'] == 'browser-p2') == CHILDREN
    data = json.loads((INVENTORY / 'atomic-cases.json').read_text())
    assert sorted(c['id'] for c in harnesses.harness_children(data) if c['harness'] == 'browser-p2') == CHILDREN
