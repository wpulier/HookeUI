"""H00 reference comparison harness: Hooke's own result against an independent pinned-napari result (contract §2).

Each case of tests/scientific_comparison_recipes.py runs twice:

- Hooke's side: a confined review of a retained volume, opened by ``orchestrator.scientific_project_review.review``
  in the prepared bundle (``NAPARI_NATIVE_HOST_PREPARATION``). The recipes run through the worker's
  ``python-resources`` console on the session's own viewer.
- The reference side: a separate process of the pinned runtime (``NAPARI_REFERENCE_PYTHON -I``) that imports only
  napari, on a hidden ``Viewer`` holding the same volume (tests/scientific_reference_process.py).

A child passes only when the reference shows the behaviour (no exception, and for an event at least one emission)
and Hooke's observation equals it exactly: return values, every emission's type and payload and source, and the
changed state, with floats to the frozen ``FLOAT_DIGITS``. Both sides record their whole runtime, and napari's files
must hash the same on both, and the session's layer must hold exactly the retained volume. The negatives break
behaviour inside Hooke's worker and the comparison must catch each: a wrong effect, a silent emitter, and Hooke's
own path broken (the console's viewer is not the session's). Without both runtimes every case skips, and the
acceptance runner reports these children missing.

The GUI comparison profile records settings, controls mode, recipe digest and both native platforms.
The original model-only comparison failures remain historical evidence. The original out-of-plane rotation
in a 2D view is an upstream GUI limitation, retained separately and never counted as positive behavior.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

import numpy as np
import pytest

from bench import scientific_harnesses as harnesses
from bench.scientific_inventory import INVENTORY
from gpu_worker import manifests as catalog
from tests.shared_run import shared_result
from tests.scientific_comparison_recipes import PROFILE

HERE = Path(__file__).resolve().parent
RECIPES = HERE / 'scientific_comparison_recipes.py'
REFERENCE = HERE / 'scientific_reference_process.py'
VOLUME = np.arange(3 * 8 * 9, dtype='<u2').reshape(3, 8, 9)
INVENTORY_DATA = json.loads((INVENTORY / 'atomic-cases.json').read_text())
PINNED_SOURCES = {path: digest for path, digest in INVENTORY_DATA['sources'].items()
                  if path.startswith(('src/napari/', 'src/napari_builtins/'))}
PINNED_DEPENDENCIES = json.loads((HERE / 'scientific_reference_dependencies.json').read_text())['versions']
CHILDREN = [c for c in harnesses.harness_children(INVENTORY_DATA) if c['harness'].startswith('compare-')]
LIMITATION = {'id': 'out-of-plane-rotation-2d', 'template': 'upstream-limitation',
              'symbol': 'out-of-plane-rotation-2d', 'path': None}
# Each negative breaks one behaviour inside Hooke's worker, then observes the case that behaviour belongs to.
BREAKS = {
    'wrong-effect': ('compare-event', 'Layer.events.opacity', '''
from napari.layers.base.base import Layer as _Layer
_kept = _Layer.opacity
_Layer.opacity = property(_kept.fget, lambda self, value: _kept.fset(self, value * 0.5))
'''),
    'silent-emitter': ('compare-event', 'Layer.events.blending', '''
from napari.layers.base.base import Layer as _Layer
_kept = _Layer.blending
_Layer.blending = property(_kept.fget, lambda self, value: setattr(self, '_blending', value))
'''),
    # A successful writer with wrong scientific values must fail the file/readback comparison.
    'writer-values': ('compare-call', 'Layer.save', '''
from napari.layers import Layer as _WriterLayer
import numpy as _writer_np
_writer_save = _WriterLayer.save
def _wrong_writer_values(self, *args, **kwargs):
    kept = self.data
    try:
        self.data = _writer_np.zeros_like(kept)
        return _writer_save(self, *args, **kwargs)
    finally:
        self.data = kept
_WriterLayer.save = _wrong_writer_values
'''),
    'layerlist-lookup': ('compare-layerlist', 'LayerList.__getitem__', '''
from napari.components import LayerList as _List
_kept_lookup = _List.__getitem__
def _wrong_name_lookup(self, key):
    if self is viewer.layers and key == '__h00_list_a':
        return self._list[-1]
    return _kept_lookup(self, key)
_List.__getitem__ = _wrong_name_lookup
'''),
    'layerlist-replacement': ('compare-layerlist', 'LayerList.__setitem__', '''
from napari.components import LayerList as _List
_kept_replace = _List.__setitem__
_original_length = len(viewer.layers)
def _dropped_replacement(self, key, value):
    if self is viewer.layers and key == _original_length:
        return
    return _kept_replace(self, key, value)
_List.__setitem__ = _dropped_replacement
'''),
    # Hooke's own path broken: the console's viewer is not the session's.
    'detached-viewer': ('compare-event', 'Dims.events.ndim', '''
from napari.components import ViewerModel as _Detached
viewer = _Detached()
'''),
}


def _cases(children):
    return [{'id': c['id'], 'template': c['harness'], 'symbol': c['source']['symbol'], 'path': c['source']['path']}
            for c in children]


def _console(worker, source):
    result, _ = worker.request({'op': 'python-resources', 'source': source})
    console = result['console']
    if console['error'] or console['truncated']:
        raise AssertionError((console['error'] or 'transcript truncated')[-4000:])
    return console['stdout'].strip().splitlines()[-1] if console['stdout'].strip() else ''


def _hooke(prepared, root, cases, broken=None):
    """Every case through the review worker's console, on the session's own viewer."""
    from orchestrator.scientific_project_review import review
    from orchestrator.scientific_projects import ProjectStore
    from tests.test_scientific_arrays import fill
    store = ProjectStore(root / 'retained')
    bundle, operation = prepared['key'].split('#')
    runtime = {'bundle': bundle, 'generation': Path(prepared['folder']).name, 'operation': operation}
    saved = store.create('s1', {'kind': 'scene', 'title': 'Comparison', 'state': {}, 'resources': [], 'runtimes': [runtime]})
    draft = store.array_draft(saved.id, 's1', component='array.volume', shape=VOLUME.shape, dtype=VOLUME.dtype.str,
                              axes=['z', 'y', 'x'], chunks=[1, 8, 9], scientific={'semantic': 'intensity'},
                              max_written_bytes=VOLUME.nbytes)
    reference = fill(draft, VOLUME)
    document = saved.document
    document['resources'] = [reference]
    document['state']['scientificView'] = {'runtime': runtime, 'inputs': {'volume': reference}}
    saved = store.save(saved.id, 's1', document, expected_revision=1)
    out = {}
    with review(store, saved.id, 's1', saved.revision) as (_, worker):
        recipe = RECIPES.read_text()
        _console(worker, recipe)
        out['scene'] = json.loads(_console(worker, 'import json as _json, hashlib as _h, numpy as _np\n'
                                                   '_d = _np.asarray(viewer.layers[0].data)\n'
                                                   'print(_json.dumps({"name": viewer.layers[0].name, '
                                                   '"ndisplay": viewer.dims.ndisplay, "layers": len(viewer.layers), '
                                                   '"data": [_d.dtype.str, list(_d.shape), _h.sha256(_d.tobytes()).hexdigest()]}))'))
        out['runtime'] = json.loads(_console(worker, f'print(_json.dumps(runtime({sorted(PINNED_SOURCES)!r})))'))
        out['profile'] = json.loads(_console(worker, 'print(_json.dumps(comparison_profile(viewer, '
                                              f'{hashlib.sha256(recipe.encode()).hexdigest()!r})))'))
        if broken:
            _console(worker, broken)
        out['results'] = {}
        for case in cases:
            try:
                line = _console(worker, f'print(_json.dumps(observe({case["template"]!r}, {case["symbol"]!r}, viewer, '
                                        f'{case["path"]!r})))')
                out['results'][case['id']] = json.loads(line)
            except AssertionError as error:  # the console itself failed: that case's evidence
                out['results'][case['id']] = {'console': str(error)}
    return out


def _reference(python, cases, scene):
    """The same cases in a plain pinned-napari process of its own."""
    with tempfile.TemporaryDirectory() as folder:
        np.save(Path(folder) / 'volume.npy', VOLUME)
        request = {'profile': PROFILE, 'recipes': str(RECIPES),
                   'volume': str(Path(folder) / 'volume.npy'), 'volume_name': scene['name'],
                   'ndisplay': scene['ndisplay'], 'cases': cases, 'source_paths': sorted(PINNED_SOURCES)}
        # The unmodified Cocoa backend is independent of Hooke's offscreen CGL implementation.
        # A plain Qt offscreen GUI has no supported OpenGL context here; it is not a fallback.
        environment = {'PATH': '/usr/bin:/bin', 'HOME': folder, 'TMPDIR': folder, 'NAPARI_CONFIG': f'{folder}/napari.yaml',
                       'QT_API': 'pyside6', 'QT_QPA_PLATFORM': 'cocoa', 'NAPARI_ASYNC': '0'}
        done = subprocess.run([python, '-I', str(REFERENCE)], input=json.dumps(request), capture_output=True, text=True,
                              cwd=folder, env=environment, timeout=1200)
        if done.returncode:
            raise RuntimeError(f'the reference process exited {done.returncode}: {done.stderr[-4000:]}')
        return json.loads(done.stdout)


def _prepared_environment(prepared):
    saved = {key: os.environ.get(key) for key in set(prepared['environment']) | {'MICROMIND_SANDBOX_READ'}}
    os.environ.update(prepared['environment'])
    os.environ.pop('MICROMIND_SANDBOX_READ', None)
    catalog.manifests.cache_clear()

    def restore():
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        catalog.manifests.cache_clear()
    return restore


@pytest.fixture(scope='session')
def compared(tmp_path_factory):
    location, python = os.environ.get('NAPARI_NATIVE_HOST_PREPARATION'), os.environ.get('NAPARI_REFERENCE_PYTHON')
    if not location or not python:
        pytest.skip('requires a prepared review bundle (NAPARI_NATIVE_HOST_PREPARATION) and the pinned reference '
                    'runtime (NAPARI_REFERENCE_PYTHON)')
    shared = shared_result(tmp_path_factory, f'reference-comparison-{PROFILE}')
    with open(shared.with_suffix('.lock'), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not shared.exists():
            prepared = json.loads(Path(location).read_text())
            restore = _prepared_environment(prepared)
            try:
                cases = _cases(CHILDREN)
                hooke = _hooke(prepared, tmp_path_factory.mktemp('hooke'), cases)
                reference = _reference(python, cases, hooke['scene'])
                negatives = {}
                for name, (template, symbol, broken) in BREAKS.items():
                    case = {'id': name, 'template': template, 'symbol': symbol,
                            'path': next(c['source']['path'] for c in INVENTORY_DATA['children']
                                         if c.get('source', {}).get('symbol') == symbol)}
                    negative_hooke = _hooke(prepared, tmp_path_factory.mktemp(name), [case], broken)
                    negatives[name] = {'hooke': negative_hooke,
                                       'reference': _reference(python, [case], negative_hooke['scene'])}
                # A failure may leave a transform mutated: isolate it from all positive cases.
                limitation_hooke = _hooke(prepared, tmp_path_factory.mktemp('limitation'), [LIMITATION])
                limitation = {'hooke': limitation_hooke,
                              'reference': _reference(python, [LIMITATION], limitation_hooke['scene'])}
            finally:
                restore()
            shared.write_text(json.dumps({'hooke': hooke, 'reference': reference, 'negatives': negatives,
                                          'limitation': limitation}))
    return json.loads(shared.read_text())


def test_the_session_holds_the_retained_volume(compared):
    import hashlib
    scene = compared['hooke']['scene']
    assert scene['data'] == [VOLUME.dtype.str, list(VOLUME.shape), hashlib.sha256(VOLUME.tobytes()).hexdigest()], scene


def _assert_gui_profile(compared, *, ndisplay=2):
    hooke, reference = compared['hooke']['profile'], compared['reference']['profile']
    for side, platform in ((hooke, 'offscreen'), (reference, 'cocoa')):
        assert set(side) == {'name', 'viewer', 'ndisplay', 'qt_api', 'qt_platform', 'settings', 'recipe_sha256'}
        assert side['name'] == PROFILE, 'unknown comparison profile'
        assert side['viewer'] == 'napari.viewer.Viewer', 'comparison requires a real GUI viewer'
        assert side['qt_api'] == 'PySide6' and side['qt_platform'] == platform, 'unexpected native GUI platform'
        assert side['ndisplay'] == ndisplay, 'comparison starts in the declared displayed dimensions'
        assert side['settings']['experimental']['async_'] is False, 'comparison requires synchronous layer loading'
        assert type(side['settings']['experimental']['dynamic_layer_controls']) is bool, 'unknown controls mode'
        assert side['recipe_sha256'] == hashlib.sha256(RECIPES.read_bytes()).hexdigest(), 'recipe changed'
    assert {k: v for k, v in hooke.items() if k != 'qt_platform'} == {
        k: v for k, v in reference.items() if k != 'qt_platform'}, 'GUI settings/profile differ'


def _assert_pinned_runtime(compared):
    hooke, reference = compared['hooke']['runtime'], compared['reference']['runtime']
    from env_worker.napari_cgl import PINNED, REFERENCE_WHEEL
    expected_versions = {**PINNED_DEPENDENCIES, 'napari': '0.7.1.dev0'}
    assert all(expected_versions[name.lower().replace('_', '-')] == version
               for name, version in PINNED.items() if name != 'napari')
    source_digest = hashlib.sha256(json.dumps(PINNED_SOURCES, sort_keys=True).encode()).hexdigest()
    for side in (hooke, reference):
        assert side.get('napari') == expected_versions['napari'], 'unapproved napari version'
        assert side.get('versions') == expected_versions, 'runtime differs from the frozen dependency lock'
        assert side.get('distributions') == len(expected_versions), 'duplicate or missing runtime distributions'
        assert side.get('napari_wheel') == REFERENCE_WHEEL, 'unapproved napari wheel'
        assert side.get('pinned_sources_sha256') == source_digest, 'runtime differs from pinned napari source'
    assert hooke['napari_files'] == reference['napari_files'], 'napari runtime files differ'
    assert hooke['distributions_sha256'] == reference['distributions_sha256'], 'runtime dependency records differ'


def test_both_sides_ran_the_same_pinned_napari(compared):
    _assert_gui_profile(compared)
    _assert_pinned_runtime(compared)


def _assert_retained_limitation(compared):
    """A known upstream failure is a regression observation, never a positive comparison child."""
    limitation = compared['limitation']
    _assert_gui_profile(limitation)
    _assert_pinned_runtime(limitation)
    for side in ('hooke', 'reference'):
        assert limitation[side]['results'][LIMITATION['id']] == {'error': 'LinAlgError: Singular matrix'}, (
            'The retained 2D out-of-plane limitation changed; inspect it before revising its classification', limitation)


@pytest.mark.parametrize('ident', [c['id'] for c in CHILDREN])
def test_compare_child(ident, compared):
    if next(c for c in CHILDREN if c['id'] == ident)['source']['symbol'] == 'Layer.events.rotate':
        _assert_retained_limitation(compared)
    reference, hooke = compared['reference']['results'][ident], compared['hooke']['results'][ident]
    assert 'error' not in reference, f'the recipe does not exercise upstream behaviour: {reference["error"]}'
    harness = next(c for c in CHILDREN if c['id'] == ident)['harness']
    if harness == 'compare-layerlist':
        from tests.scientific_comparison_recipes import validate_layerlist_observation
        symbol = next(c for c in CHILDREN if c['id'] == ident)['source']['symbol']
        validate_layerlist_observation(symbol, reference)
        validate_layerlist_observation(symbol, hooke)
    elif harness == 'compare-event':
        assert reference['observed']['events'], 'the trigger emits nothing upstream'
    elif harness == 'compare-contribution':
        assert reference.get('listed') is True, 'the contribution is not registered upstream'
    elif harness == 'compare-enum':
        assert reference.get('observed', {}).get('has') is True, 'the action was not observed upstream'
    assert hooke == reference


@pytest.mark.parametrize('name', sorted(BREAKS))
def test_a_broken_behaviour_in_hookes_worker_is_caught(name, compared):
    negative = compared['negatives'][name]
    _assert_gui_profile(negative)
    _assert_pinned_runtime(negative)
    hooke, reference = (negative[side]['results'][name] for side in ('hooke', 'reference'))
    assert 'error' not in reference and hooke != reference, negative
    if name.startswith('layerlist-'):
        _assert_layerlist_negative(name, hooke, reference)
    if name == 'writer-values':
        _assert_writer_values_negative(hooke, reference)


def _assert_layerlist_negative(name, hooke, reference):
    from tests.scientific_comparison_recipes import validate_layerlist_observation
    symbol, changed = {'layerlist-lookup': ('LayerList.__getitem__', 'name'),
                       'layerlist-replacement': ('LayerList.__setitem__', 'integer')}[name]
    validate_layerlist_observation(symbol, reference)
    assert set(hooke) == {'observed'}, 'a collection crash is not a controlled wrong-behavior proof'
    actual, expected = hooke['observed'], reference['observed']
    assert actual['restored'] is True, 'negative did not restore retained session state'
    assert set(actual['cases']) == set(expected['cases'])
    assert actual['proven'][changed] is False, 'negative did not break its intended behavior'
    assert actual['cases'][changed] != expected['cases'][changed], 'negative did not change the observed result'


def _assert_writer_values_negative(hooke, reference):
    """A writer crash is not proof that successful-but-corrupt output is detected."""
    for report in (hooke, reference):
        assert set(report) == {'observed'}, 'writer must complete successfully'
        observed = report['observed']
        assert set(observed) == {'returned', 'files', 'read_back'}, 'incomplete writer observation'
        assert observed['returned'] in (['w-layer.csv'], ['<cwd>/w-layer.csv']), 'writer returned no expected file'
        assert set(observed['files']) == {'w-layer.csv'}, 'expected writer file is absent'
        digest = observed['files']['w-layer.csv']
        assert isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest)
    actual, expected = hooke['observed'], reference['observed']
    assert actual['returned'] == expected['returned'], 'negative changed the successful API return'
    assert actual['files'] != expected['files'], 'negative did not change saved file bytes'
    for observed, values in ((expected, [['1.0', '2.0'], ['3.0', '4.0']]),
                             (actual, [['0.0', '0.0'], ['0.0', '0.0']])):
        assert observed['read_back'] == {'w-layer.csv': [['points', {
            'dtype': '<f8', 'shape': [2, 2], 'values': values}]]}, 'writer readback differs from the known values'


def test_the_harnesses_list_exactly_the_recipes():
    """CI: each rules.json comparison harness names exactly the recipe symbols, and each recipe has its child."""
    namespace = {'__name__': 'scientific_comparison_recipes'}
    exec(compile(RECIPES.read_text(), str(RECIPES), 'exec'), namespace)
    rules = json.loads((INVENTORY / 'rules.json').read_text())
    listed = {h['name']: sorted(h['symbols']) for h in rules['harnesses'] if h['name'].startswith('compare-')}
    assert listed == namespace['recipes']()
    for name in listed:
        assert harnesses.HARNESS_TESTS[name]['requires'] == ('NAPARI_NATIVE_HOST_PREPARATION', 'NAPARI_REFERENCE_PYTHON')
