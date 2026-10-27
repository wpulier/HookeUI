"""Host integration ownership; native runtime cases complement these refusals."""
import threading
from types import SimpleNamespace

import pytest

from env_worker.scientific_host import ReviewSession, ChangeRefused, ControlFailed
from env_worker.scientific_native_panel import CallbackFailed, BoundaryFailed, Refused
from tests.test_scientific_native_panel_wire import state


def host():
    session = object.__new__(ReviewSession)
    session._closed = False
    session._thread = threading.get_ident()
    session._frame = 'scientific-image'
    session.dirty = False
    session.panel = SimpleNamespace(invalidate=lambda: None)
    session.native_panel = None
    session._binding_contributions = False
    return session


def test_native_panel_requires_explicit_factory_binding():
    session = host()
    with pytest.raises(ChangeRefused):
        session.render_native_panel(32, 24)
    with pytest.raises(ChangeRefused):
        session.bind_native_panel(object())


def test_queued_callback_cause_reaches_the_private_recovery_message():
    session = host()
    session._refresh_layer_connections = lambda: None
    def paint(width, height):
        try:
            raise PermissionError('Prepared model file is unavailable')
        except PermissionError as cause:
            raise CallbackFailed('Queued callback failed') from cause
    session.native_panel = SimpleNamespace(paint=paint)
    with pytest.raises(ControlFailed, match='PermissionError: Prepared model file is unavailable'):
        session.render_native_panel(32, 24)
    assert session.dirty and session._frame is None


@pytest.mark.parametrize('error,expected', [
    (CallbackFailed('may have changed'), ControlFailed),
    (BoundaryFailed('fatal'), BoundaryFailed),
    (Refused('stale'), ChangeRefused),
])
def test_panel_failure_revokes_both_frames_without_replaying(error, expected):
    session = host()
    attempts = []
    def dispatch(value):
        attempts.append(value)
        raise error
    session.native_panel = SimpleNamespace(current=state(), dispatch=dispatch,
        invalidate=lambda: setattr(session.native_panel, 'current', None))
    request = dict(panel='a'*32, frame='b'*64, op='click', value=[1, 2])
    with pytest.raises(expected):
        session.change_native_panel(request)
    assert session._frame is None and session.native_panel.current is None
    assert session.dirty and attempts == [request]
    with pytest.raises(ChangeRefused):
        session.change_native_panel(request)
    assert len(attempts) == 1


def test_invalid_panel_request_never_reaches_plugin_or_revokes_image():
    session = host()
    session.native_panel = SimpleNamespace(current=state(), dispatch=lambda value: pytest.fail('not admitted'))
    with pytest.raises(ChangeRefused):
        session.change_native_panel(dict(panel='a'*32, frame='c'*64, op='click', value=[1, 2]))
    assert session._frame == 'scientific-image' and not session.dirty


def test_panel_paint_preserves_image_only_when_callbacks_do_not_mutate_it():
    session = host()
    session._refresh_layer_connections = lambda: None
    session.native_panel = SimpleNamespace(paint=lambda w, h: (state(), b''))
    session.render_native_panel(32, 24)
    assert session._frame == 'scientific-image'
    def changed(w, h):
        session._frame = None
        return state(), b''
    session.native_panel.paint = changed
    session.render_native_panel(32, 24)
    assert session._frame is None


def test_background_observation_delivers_queued_changes_without_replaying(monkeypatch):
    session = host()
    events = []
    def pump():
        events.append('callback')
        session._frame = None
    session.native_panel = SimpleNamespace(app=SimpleNamespace(processEvents=pump))
    session._refresh_layer_connections = lambda: None
    assert session.observe() == {'imageFrame': None}
    assert events == ['callback']
    session.native_panel.app.processEvents = lambda: None
    session._frame = 'a' * 64
    assert session.observe() == {'imageFrame': 'a' * 64}


def test_background_observation_keeps_callback_failure_recoverable():
    session = host()
    def pump():
        raise RuntimeError('Late result delivery failed')
    session.native_panel = SimpleNamespace(app=SimpleNamespace(processEvents=pump))
    session._refresh_layer_connections = lambda: None
    with pytest.raises(ControlFailed, match='Late result delivery failed'):
        session.observe()
    assert session._frame is None
