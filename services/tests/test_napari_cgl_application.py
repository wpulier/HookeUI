"""Synthetic Qt lifecycle contract; no native canvas or platform qualification."""
import sys
from types import SimpleNamespace as S

import pytest

from env_worker import napari_cgl as renderer


@pytest.fixture
def application_runtime(monkeypatch):
    events, state = [], {'qt': None, 'platform': 'offscreen', 'fail': False}
    original = type('OriginalCanvas', (), {})
    backend = S(CanvasBackend=original)
    owner = S(backend_module=backend, native=None)
    def get_qapp():
        events.append('napari')
        if state['fail']:
            raise RuntimeError('upstream Qt initialization failed')
        if state['qt'] is None:
            state['qt'] = S(font_features={'tnum': 1})
        return state['qt']
    def create():
        events.append('vispy-create')
        if state['qt'] is None:
            state['qt'] = S(font_features={})
        owner.native = state['qt']
    owner.create = create
    def use_app(name):
        assert name == 'pyside6'
        events.append('vispy-owner')
        return owner
    config = {'dpi': None}
    modules = {
        'qtpy.QtWidgets': S(QApplication=S(instance=lambda: state['qt'], platformName=lambda: state['platform']), QWidget=type('Widget', (), {})),
        'qtpy.QtGui': S(QImage=object, QPainter=object),
        'qtpy.QtCore': S(Signal=lambda: None),
        'napari.qt': S(get_qapp=get_qapp),
        'vispy': S(config=config, app=S(use_app=use_app)),
        'vispy.app.backends._qt': S(QtBaseCanvasBackend=type('Backend', (), {})),
        'OpenGL': S(GL=object()), 'shiboken6': S(isValid=lambda obj: True, delete=lambda obj: None),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(renderer, '_runtime', lambda reference: None)
    monkeypatch.setattr(renderer, '_library', lambda: object())
    return S(state=state, events=events, owner=owner, original=original, config=config)


def test_install_preserves_upstream_font_initialization_and_native_app_ownership(application_runtime):
    runtime = application_runtime
    installation = renderer.install()
    assert runtime.owner.native.font_features == {'tnum': 1}
    assert runtime.events == ['napari', 'vispy-owner', 'vispy-create']
    assert installation.application is runtime.owner
    assert runtime.owner.native is runtime.state['qt']
    installation.close()
    assert runtime.owner.backend_module.CanvasBackend is runtime.original
    assert runtime.config['dpi'] is None
    assert runtime.owner.native is runtime.state['qt']


def test_existing_upstream_app_and_its_font_features_are_preserved(application_runtime):
    runtime = application_runtime
    existing = S(font_features={'tnum': 1, 'custom': 1})
    runtime.state['qt'] = existing
    installation = renderer.install()
    assert runtime.owner.native is existing and existing.font_features == {'tnum': 1, 'custom': 1}
    installation.close()


def test_failed_upstream_application_start_does_not_install_backend(application_runtime):
    runtime = application_runtime
    runtime.state['fail'] = True
    with pytest.raises(RuntimeError, match='upstream Qt initialization failed'):
        renderer.install()
    assert runtime.owner.backend_module.CanvasBackend is runtime.original
    assert runtime.config['dpi'] is None and runtime.owner.native is None


def test_existing_wrong_platform_still_refuses_before_backend_install(application_runtime):
    runtime = application_runtime
    runtime.state.update(qt=S(font_features={'tnum': 1}), platform='cocoa')
    with pytest.raises(renderer.RendererUnavailable, match='not offscreen'):
        renderer.install()
    assert runtime.owner.backend_module.CanvasBackend is runtime.original
    assert runtime.config['dpi'] is None
