"""Display controls preserve scientific values and exact-frame admission."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from env_worker import scientific_host, scientific_view_preferences as views
from tests.test_scientific_change_recovery import host
from tests.test_scientific_view_preferences import CASES, FRAME
from tests.test_scientific_host_worker import actual_host  # noqa: F401

SETTINGS = {'contrastLimits': [2, 200], 'gamma': 1.4, 'rendering': 'iso',
            'isoThreshold': 70, 'attenuation': .25, 'autoContrast': False}


def test_remember_appearance_and_restore_legacy_without_erasing_current_settings():
    frame = copy.deepcopy(FRAME)
    frame['layers'][0].update(viewKey='raw', viewGeometry=copy.deepcopy(CASES['valid']['layers'][0]['geometry']),
                              imageAppearance=copy.deepcopy(SETTINGS))
    saved = views.from_frame(frame)
    assert saved['schema'] == 2 and saved['layers'][0]['imageAppearance'] == SETTINGS
    frame['layers'][0]['imageAppearance']['gamma'] = 2
    assert views.compatible(saved, frame) == saved
    old = copy.deepcopy(CASES['valid'])
    upgraded = views.compatible(old, frame)
    assert upgraded['schema'] == 2 and upgraded['layers'][0]['imageAppearance']['gamma'] == 2
    assert old == CASES['valid']
    del frame['layers'][0]['imageAppearance']
    with pytest.raises(ValueError, match='appearance'):
        views.compatible(saved, frame)


@pytest.mark.parametrize('key,value', [('gamma', 0), ('gamma', True), ('gamma', float('inf')), ('gamma', 10**400),
    ('contrastLimits', [200, 2]), ('contrastLimits', [2, 2]), ('rendering', 'arbitrary'),
    ('attenuation', -1), ('autoContrast', 1), ('isoThreshold', float('nan'))])
def test_invalid_appearance_keeps_frame_and_other_settings(key, value):
    session = host()
    layer = SimpleNamespace(_type_string='image', contrast_limits=[0, 100], gamma=1,
                            rendering='mip', iso_threshold=50, attenuation=.05, auto_contrast=False)
    session._layers = [('layer', layer)]
    before = vars(layer).copy()
    with pytest.raises(scientific_host.ChangeRefused):
        session.change({'frame': 'current', 'operation': 'image-presentation',
                        'value': {'layer': 'layer', 'settings': {**SETTINGS, key: value}}})
    assert vars(layer) == before and session._frame == 'current' and not session.dirty
    session.change({'frame': 'current', 'operation': 'image-presentation',
                    'value': {'layer': 'layer', 'settings': SETTINGS}})
    assert layer.gamma == 1.4 and layer.rendering == 'iso'


def test_actual_image_controls_all_modes_preserve_values(actual_host):
    import numpy as np
    from orchestrator import scientific_host as parent
    from tests.test_native_regions import source
    prepared = actual_host
    values = np.arange(6*24*28, dtype='<u2').reshape(6, 24, 28)
    before = values.tobytes()
    def launch():
        return parent.review(prepared['key'], Path(prepared['folder']).name, {'volume': source(values, [])})
    with launch() as worker:
        worker.request({'op': 'python-resources', 'source': 'viewer.dims.ndisplay = 3'})
        rendered, _ = worker.request({'op': 'render', 'width': 256, 'height': 192})
        frame = rendered['frame']
        with pytest.raises(parent.ChangeRefused):
            worker.request({'op':'change','width':256,'height':192,'request':{
                'frame':frame['frame'],'operation':'image-presentation',
                'value':{'layer':frame['layers'][0]['id'],'settings':{**SETTINGS,'gamma':10**400}}}})
        assert worker.process.poll() is None
        for mode in ['translucent', 'additive', 'iso', 'mip', 'minip', 'attenuated_mip', 'average']:
            settings = {**SETTINGS, 'rendering': mode}
            changed, pixels = worker.request({'op': 'change', 'width': 256, 'height': 192,
                'request': {'frame': frame['frame'], 'operation': 'image-presentation',
                            'value': {'layer': frame['layers'][0]['id'], 'settings': settings}}})
            frame = changed['frame']
            assert frame['layers'][0]['imageAppearance'] == settings
            assert len(pixels) == 256*192*4
            # Independent upstream setters must render the same frame. This is a
            # rendering agreement check on synthetic data, not specimen accuracy.
            direct = (f"layer = viewer.layers[0]\nlayer.gamma = 2\nlayer.gamma = {settings['gamma']}\n"
                      f"layer.contrast_limits = {settings['contrastLimits']}\nlayer.rendering = {mode!r}\n"
                      f"layer.iso_threshold = {settings['isoThreshold']}\nlayer.attenuation = {settings['attenuation']}")
            result, _ = worker.request({'op': 'python-resources', 'source': direct})
            assert not result['console']['error']
            reference, reference_pixels = worker.request({'op': 'render', 'width': 256, 'height': 192})
            assert reference_pixels == pixels
            frame = reference['frame']
        automatic = {**SETTINGS, 'autoContrast': True}
        changed, auto_pixels = worker.request({'op': 'change', 'width': 256, 'height': 192,
            'request': {'frame': frame['frame'], 'operation': 'image-presentation',
                        'value': {'layer': frame['layers'][0]['id'], 'settings': automatic}}})
        assert changed['frame']['layers'][0]['imageAppearance']['contrastLimits'] != SETTINGS['contrastLimits']
        result, _ = worker.request({'op': 'python-resources', 'source':
            'viewer.layers[0].auto_contrast = False\nviewer.layers[0].contrast_limits = (2,200)\nviewer.layers[0].auto_contrast = True'})
        assert not result['console']['error']
        reference, reference_pixels = worker.request({'op': 'render', 'width': 256, 'height': 192})
        assert reference_pixels == auto_pixels
        assert reference['frame']['layers'][0]['imageAppearance'] == changed['frame']['layers'][0]['imageAppearance']
        result, _ = worker.request({'op': 'python-resources', 'source':
            "import hashlib\nprint(hashlib.sha256(viewer.layers[0].data.tobytes()).hexdigest())\nprint(viewer.layers[0].data.dtype.str)"})
        assert result['console']['stdout'] == f'{hashlib.sha256(before).hexdigest()}\n<u2\n'
    assert values.tobytes() == before
    assert worker.process.poll() is not None


SHARED = json.loads((Path(__file__).resolve().parents[2] / 'contract/scientific-image-appearance-cases.json').read_text())

@pytest.mark.parametrize('case', SHARED['invalid'], ids=lambda c:c['name'])
def test_appearance_shared_wire_refusals(case):
    from env_worker.scientific_image_appearance import settings
    from env_worker.scientific_view_wire import frame as validate
    value = {**SHARED['valid'], **case['patch']}
    with pytest.raises(ValueError): settings(value)
    frame = copy.deepcopy(FRAME)
    frame['layers'][0]['imageAppearance'] = value
    with pytest.raises(ValueError): validate(frame)


def test_actual_floating_intensities_keep_rendering_above_integer_identity_range(actual_host):
    import numpy as np
    from orchestrator import scientific_host as parent
    from tests.test_native_regions import source
    prepared = actual_host
    values = np.linspace(1e17, 2e17, 6*24*28, dtype='<f8').reshape(6,24,28)
    with parent.review(prepared['key'], Path(prepared['folder']).name, {'volume':source(values,[])}) as worker:
        inventory, _ = worker.request({'op':'layer-inventory'})
        settings = inventory['inventory']['layers'][0]['imageAppearance']
        assert settings['contrastLimits'][0] > 2**53
        shown, pixels = worker.request({'op':'render','width':256,'height':192})
        assert len(pixels) == 256*192*4
        changed, _ = worker.request({'op':'change','width':256,'height':192,'request':{
            'frame':shown['frame']['frame'],'operation':'image-presentation',
            'value':{'layer':shown['frame']['layers'][0]['id'], 'settings':{**settings,'gamma':1.4}}}})
        assert changed['frame']['layers'][0]['imageAppearance']['contrastLimits'] == settings['contrastLimits']


def test_automatic_restore_checks_every_setting_except_derived_contrast():
    frame = copy.deepcopy(FRAME)
    frame['layers'][0].update(viewKey='raw',viewGeometry=copy.deepcopy(CASES['valid']['layers'][0]['geometry']),
                              imageAppearance={**SETTINGS,'autoContrast':True})
    wanted=views.from_frame(frame);shown=copy.deepcopy(wanted)
    shown['layers'][0]['imageAppearance']['contrastLimits']=[500,800]
    assert views.restored(wanted,shown)
    shown['layers'][0]['imageAppearance']['gamma']=2
    assert not views.restored(wanted,shown)
    shown=copy.deepcopy(wanted);shown['layers'][0]['imageAppearance']['autoContrast']=False
    assert not views.restored(wanted,shown)
