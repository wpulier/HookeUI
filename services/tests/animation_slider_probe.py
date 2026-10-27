"""Actual adapter/Qt callback probe; synthetic pixels and widget inputs, not UI qualification."""
from pathlib import Path
import hashlib
import importlib.metadata
import importlib.util
import json
import sys
import traceback
from types import SimpleNamespace


def run(adapter_path: Path, output: Path):
    # Imports are confined to the separately launched prepared runtime.
    import numpy as np
    import napari
    from qtpy.QtCore import Qt
    from qtpy.QtTest import QTest
    from qtpy.QtWidgets import QApplication
    import napari_animation._qt.animation_widget as upstream

    report = {'classification': 'actual source adapter + pinned upstream Qt callbacks; synthetic input and public widget actions; no browser/installed acceptance',
              'adapter': str(adapter_path), 'adapterSha256': hashlib.sha256(adapter_path.read_bytes()).hexdigest(),
              'napari': napari.__version__, 'animation': importlib.metadata.version('napari-animation'),
              'qtBinding': importlib.metadata.version('PySide6'),
              'upstreamWidgetSha256': hashlib.sha256(Path(upstream.__file__).read_bytes()).hexdigest(), 'observations': []}
    assert report['animation'] == '0.0.10'
    spec = importlib.util.spec_from_file_location('source_animation_adapter', adapter_path)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    source = np.arange(32 * 32, dtype='uint16').reshape(32, 32)
    original = source.copy()
    bindings = {}
    class Input:
        shape = source.shape
        dtype = source.dtype.str
        axes = ('y', 'x')
        descriptor = {'semantic': 'intensity'}
        def read(self, bounds):
            return source[tuple(slice(*pair) for pair in bounds)].copy()
    viewer = widget = container = None
    try:
        viewer = napari.Viewer(show=True)
        context = SimpleNamespace(viewer=viewer, inputs={'specimen': Input()}, restored_layers=None,
            bind_editable=lambda **value: bindings.update(editable=value),
            bind_view=lambda **value: bindings.update(view=value),
            bind_document=lambda **value: bindings.update(document=value),
            bind_recipe=lambda **value: bindings.update(recipe=value),
            bind_sequence=lambda **value: bindings.update(sequence=value),
            bind_native_panel=lambda value: bindings.update(panel=value))
        container = adapter.open_host(context)
        widget = bindings['panel']; widget.show(); QApplication.processEvents()
        assert isinstance(widget, upstream.AnimationWidget)
        assert np.array_equal(viewer.layers['specimen'].data, original)
        animation = widget.animation
        def click(control):
            assert control.isVisible() and control.isEnabled()
            QTest.mouseClick(control, Qt.LeftButton, pos=control.rect().center())
            QApplication.processEvents()
        for zoom in (2.2, 3.3):
            viewer.camera.zoom = zoom  # declared synthetic camera setup, not user-gesture evidence
            click(widget.keyframesListControlWidget.captureButton)
        assert len(animation.key_frames) == 2
        spin = widget.frameWidget.stepsSpinBox; click(spin)
        assert spin.hasFocus() or spin.isAncestorOf(QApplication.focusWidget())
        for expected in range(14, 9, -1):
            QTest.keyClick(spin, Qt.Key_Down); QApplication.processEvents()
            assert spin.value() == expected
        assert [frame.steps for frame in animation.key_frames] == [15, 10]
        assert len(animation._frames) == 11
        for index in (0, 1):
            view = widget.keyframesListWidget
            item = view.model().index(index, 0); view.scrollTo(item); QApplication.processEvents()
            rect = view.visualRect(item); assert rect.isValid() and not rect.isEmpty()
            QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=rect.center()); QApplication.processEvents()
            assert animation.key_frames.selection.active is animation.key_frames[index]
        capture = bindings['document']['capture']
        retained = capture()['keyframes']
        slider = widget.animationSlider
        errors = []
        for index, expected in ((0, 2.2), (5, (2.2 * 3.3) ** .5), (10, 3.3)):
            # Public widget value changes exercise its genuine valueChanged callback.
            # This avoids manufacturing keyboard focus or changing macOS policy.
            slider.setValue(index); QApplication.processEvents()
            row = {'index': slider.value(), 'expectedZoom': expected, 'zoom': float(viewer.camera.zoom),
                   'signalsBlocked': slider.signalsBlocked()}
            report['observations'].append(row)
            if row['index'] != index or row['signalsBlocked'] or abs(row['zoom'] - expected) > 1e-12:
                errors.append(f'Slider {index} did not apply its independent expected camera state: {row}')
            assert capture()['keyframes'] == retained
            assert np.array_equal(viewer.layers['specimen'].data, original) and np.array_equal(source, original)
        # A caller already blocking the real slider owns that state across a count change.
        previous = slider.blockSignals(True)
        try:
            spin.stepUp(); QApplication.processEvents()
            report['previousBlockedPreserved'] = slider.signalsBlocked()
            assert report['previousBlockedPreserved']
        finally:
            slider.blockSignals(previous)
        report['originalSha256'] = hashlib.sha256(original.tobytes()).hexdigest()
        report['originalUnchanged'] = np.array_equal(source, original) and np.array_equal(viewer.layers['specimen'].data, original)
        assert not errors, '\n'.join(errors)
        report['passed'] = True
    except BaseException as error:
        report.update(passed=False, error=repr(error), traceback=traceback.format_exc())
        raise
    finally:
        try:
            if widget is not None: widget.close()
            if container is not None: container.close()
            if viewer is not None: viewer.close()
        finally:
            output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    assert len(sys.argv) == 3
    run(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
