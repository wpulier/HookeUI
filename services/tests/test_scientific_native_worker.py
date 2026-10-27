"""Actual prepared native-panel worker, kept separate from browser qualification."""
from contextlib import ExitStack
import json
import os
from pathlib import Path

import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import scientific_host
from tests.test_native_regions import source
from tests.test_scientific_native_panel_wire import state


def test_native_reply_cannot_substitute_an_image_or_accept_malformed_pixels():
    request = dict(op='native-panel-render', width=32, height=24)
    pixels = bytes(32 * 24 * 4)
    scientific_host.checked_reply(request, {'ok': True, 'nativePanel': state(), 'imageFrame': None}, pixels)
    for header, body in [({'ok': True, 'frame': state()}, pixels),
                         ({'ok': True, 'nativePanel': state(), 'imageFrame': None}, pixels[:-1]),
                         ({'ok': True, 'nativePanel': {**state(), 'width': 24}, 'imageFrame': None}, pixels)]:
        with pytest.raises(scientific_host.Failed):
            scientific_host.checked_reply(request, header, body)
    with pytest.raises(scientific_host.Failed):
        scientific_host.checked_reply({'op': 'native-panel-change'}, {'ok': True, 'nativeChanged': 1}, b'')


def test_actual_panel_shares_owned_host_without_replaying_failed_callbacks(monkeypatch):
    location = os.environ.get('NAPARI_NATIVE_HOST_PREPARATION')
    if not location:
        pytest.skip('requires separately prepared native integration; not browser qualification')
    prepared = json.loads(Path(location).read_text())
    for key, value in prepared['environment'].items(): monkeypatch.setenv(key, value)
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    original_start = scientific_host.ReviewWorker.start
    def diagnostic_start(worker):
        try:
            return original_start(worker)
        except BaseException as error:
            path = worker.work / 'stderr.log'
            if path.exists(): error.add_note(path.read_text()[-8000:])
            raise
    monkeypatch.setattr(scientific_host.ReviewWorker, 'start', diagnostic_start)
    catalog.manifests.cache_clear()
    data = np.arange(6*24*28, dtype='<u2').reshape(6, 24, 28)
    before = data.tobytes()
    def open_worker(stack):
        native = source(data, [])
        native.descriptor['semantic'] = 'intensity'
        return stack.enter_context(scientific_host.review(prepared['key'], Path(prepared['folder']).name,
            {'volume': native}))
    def panel(worker):
        reply, pixels = worker.request({'op': 'native-panel-render', 'width': 320, 'height': 240})
        assert len(pixels) == 320*240*4 and len(set(pixels)) > 10
        return reply['nativePanel']
    def click(frame, title):
        row = next(row for row in frame['accessibility'] if row['name'] == title)
        x, y, width, height = row['rect']
        return {'op': 'native-panel-change', 'request': {'panel': frame['panel'], 'frame': frame['frame'],
            'op': 'click', 'value': [x+width//2, y+height//2]}}
    try:
        with ExitStack() as stack:
            good = open_worker(stack)
            worker = open_worker(stack)
            shown, _ = worker.request({'op': 'render', 'width': 256, 'height': 192})
            native, _ = worker.request({'op': 'native-panel-render', 'width': 320, 'height': 240})
            assert native['imageFrame'] == shown['frame']['frame']
            assert shown['frame']['nativePanelAvailable'] is True
            queued, _ = worker.request({'op':'python-resources','source':
                'from qtpy.QtCore import QTimer\nbackground_deliveries=[]\n'
                'def deliver_result():\n    background_deliveries.append(1)\n    viewer.layers[0].opacity=.7\n'
                'QTimer.singleShot(0, deliver_result)\nlen(background_deliveries)'})
            assert queued['console']['result']=='0'
            observed, payload = worker.request({'op':'observe'})
            assert observed=={'ok':True,'imageFrame':None} and not payload
            shown, _ = worker.request({'op':'render','width':256,'height':192})
            assert shown['frame']['layers'][0]['opacity']==.7
            observed, payload = worker.request({'op':'observe'})
            assert observed['imageFrame']==shown['frame']['frame'] and not payload
            delivered, _ = worker.request({'op':'python-resources','source':'len(background_deliveries)'})
            assert delivered['console']['result']=='1', 'Observation must not invoke the original action again'
            shown, _ = worker.request({'op':'render','width':256,'height':192})
            native, _ = worker.request({'op':'native-panel-render','width':320,'height':240})
            request = click(native['nativePanel'], 'Change opacity')
            worker.request(request)
            with pytest.raises(scientific_host.ChangeRefused): worker.request(request)
            # A panel change revokes scientific-image authority too.
            with pytest.raises(scientific_host.ChangeRefused):
                worker.request({'op': 'change', 'width': 256, 'height': 192, 'request': {
                    'frame': shown['frame']['frame'], 'operation': 'layer-presentation', 'value': {}}})
            shown, _ = worker.request({'op': 'render', 'width': 256, 'height': 192})
            assert shown['frame']['layers'][0]['opacity'] == .35 and shown['frame']['dirty']
            stale = click(panel(worker), 'Change opacity')
            worker.request({'op': 'python-resources', 'source': 'retained = 83'})
            with pytest.raises(scientific_host.ChangeRefused): worker.request(stale)
            failed = click(panel(worker), 'Fail safely')
            with pytest.raises(scientific_host.ControlFailed): worker.request(failed)
            assert worker.process.poll() is None
            with pytest.raises(scientific_host.ChangeRefused): worker.request(failed)
            shown, _ = worker.request({'op': 'render', 'width': 256, 'height': 192})
            assert shown['frame']['layers'][0]['opacity'] == .45
            worker.request(click(panel(worker), 'Change opacity'))
            result, _ = worker.request({'op': 'python-resources', 'source': 'retained + 1'})
            assert result['console']['result'] == '84'
            with pytest.raises((scientific_host.Failed, EOFError, OSError)):
                worker.request(click(panel(worker), 'Fatal failure'))
            assert worker.process.poll() is not None
            good.request(click(panel(good), 'Change opacity'))
            result, _ = good.request({'op': 'python-resources', 'source': 'int(viewer.layers[0].data[0,0,1])'})
            assert result['console']['result'] == '1' and good.process.poll() is None
        assert good.process.poll() is not None and not good.work.exists() and not worker.work.exists()
        assert data.tobytes() == before
    finally:
        catalog.manifests.cache_clear()


def test_observation_is_small_and_cannot_supply_pixels_or_execution_fields():
    for token in (None, 'a'*64):
        scientific_host.checked_reply({'op':'observe'}, {'ok':True,'imageFrame':token}, b'')
    for header, payload in [({'ok':True,'imageFrame':False},b''),
                            ({'ok':True,'imageFrame':'a'*64},b'pixels'),
                            ({'ok':True,'imageFrame':None,'nativeChanged':True},b'')]:
        with pytest.raises(scientific_host.Failed):
            scientific_host.checked_reply({'op':'observe'},header,payload)
    worker=object.__new__(scientific_host.ReviewWorker)
    with pytest.raises(scientific_host.Failed):
        worker.request({'op':'observe','source':'viewer.layers.clear()'})


@pytest.mark.parametrize('entry', ['python', 'native-button'])
def test_actual_thread_worker_delivers_layers_once_through_observation(monkeypatch, entry):
    """A finished napari worker must publish its queued result on the GUI owner."""
    location = os.environ.get('NAPARI_NATIVE_HOST_PREPARATION')
    if not location:
        pytest.skip('requires separately prepared native integration; not browser qualification')
    prepared = json.loads(Path(location).read_text())
    for key, value in prepared['environment'].items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    catalog.manifests.cache_clear()
    data = np.arange(6*24*28, dtype='<u2').reshape(6, 24, 28)
    native = source(data, [])
    native.descriptor['semantic'] = 'intensity'
    try:
        with scientific_host.review(prepared['key'], Path(prepared['folder']).name,
                                    {'volume': native}) as worker:
            worker.request({'op': 'render', 'width': 256, 'height': 192})
            worker.request({'op': 'python-resources', 'source': '''
from napari.qt.threading import thread_worker
from threading import Event, get_ident
import numpy as np
release_calculation = Event()
deliveries = []
gui_owner = get_ident()
@thread_worker
def calculate():
    if not release_calculation.wait(60):
        raise RuntimeError('Test did not release the background calculation')
    return np.ones((24, 28), dtype=np.uint16)
def deliver_result(result):
    deliveries.append(get_ident())
    viewer.add_labels(result, name='Background labels')
background_worker = calculate()
background_worker.returned.connect(deliver_result)
'''})
            if entry == 'python':
                worker.request({'op': 'python-resources', 'source': 'background_worker.start()'})
            else:
                worker.request({'op': 'python-resources', 'source': '''
from qtpy.QtWidgets import QApplication, QPushButton
start_button = next(button for root in QApplication.topLevelWidgets()
                    for button in root.findChildren(QPushButton)
                    if button.text() == 'Change opacity')
start_button.clicked.connect(background_worker.start)
'''})
                reply, _ = worker.request({'op': 'native-panel-render', 'width': 320, 'height': 240})
                panel = reply['nativePanel']
                row = next(row for row in panel['accessibility'] if row['name'] == 'Change opacity')
                x, y, width, height = row['rect']
                worker.request({'op': 'native-panel-change', 'request': {
                    'panel': panel['panel'], 'frame': panel['frame'], 'op': 'click',
                    'value': [x + width // 2, y + height // 2]}})
            # The button dispatch itself pumps Qt events. Complete the work only
            # after it returns, so that path cannot accidentally deliver it.
            reply, _ = worker.request({'op': 'python-resources', 'source':
                'release_calculation.set(); len(deliveries)'})
            assert reply['console']['result'] == '0'
            from tests.waiting import until
            def delivered():
                worker.request({'op': 'observe'})
                reply, _ = worker.request({'op': 'python-resources', 'source': 'len(deliveries)'})
                return reply['console']['result'] == '1'
            until(delivered, what='finished napari worker result delivered through observation')
            for _ in range(3):
                worker.request({'op': 'observe'})
            reply, _ = worker.request({'op': 'python-resources', 'source':
                "(deliveries == [gui_owner], int(viewer.layers[-1].data.sum()), len(viewer.layers))"})
            assert reply['console']['result'] == '(True, 672, 2)'
    finally:
        catalog.manifests.cache_clear()
