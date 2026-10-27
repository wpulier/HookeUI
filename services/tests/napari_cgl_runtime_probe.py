"""Executed only inside the real sandbox by test_napari_cgl_runtime.py."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import sys

# The profile grants this exact bootstrap file, not the services package tree.
spec = importlib.util.spec_from_file_location("napari_cgl", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

for forbidden in (lambda: Path(sys.argv[2]).read_bytes(),
                  lambda: socket.create_connection(("1.1.1.1", 443), timeout=2),
                  lambda: os.kill(int(sys.argv[3]), signal.SIGUSR1)):
    try:
        forbidden()
    except PermissionError:
        pass
    else:
        raise AssertionError("sandbox canary was not denied")

import numpy as np
from qtpy.QtWidgets import QApplication
from magicgui import magicgui
import napari

reference = sys.argv[4] if len(sys.argv) > 4 else None
installation = module.install(reference=reference)
app = QApplication.instance()
volume_changes = None
try:
    # Failure after native context construction must still delete the partial Qt
    # canvas and CGL handles, without poisoning a subsequent viewer.
    from vispy.scene import SceneCanvas
    original_init = installation.backend._init_specific
    acquired = []
    def fail_after_native(self, params, kwargs):
        original_init(self, params, kwargs)
        acquired.append(self._surface)
        raise RuntimeError("controlled failure after native allocation")
    installation.backend._init_specific = fail_after_native
    try:
        try:
            SceneCanvas(size=(128, 128), show=False)
        except RuntimeError as error:
            assert "controlled failure" in str(error)
        else:
            raise AssertionError("constructor failure was swallowed")
    finally:
        installation.backend._init_specific = original_init
    assert len(acquired) == 1 and acquired[0].closed and not acquired[0].context.value
    assert not installation.live
    reference_frames = []
    for attempt in range(3):
        viewer = napari.Viewer(show=False, show_welcome_screen=False)
        try:
            y, x = np.mgrid[:128, :128]
            data = np.exp(-((x-40)**2 + (y-50)**2)/300).astype(np.float32)
            layer = viewer.add_image(data, colormap="viridis", contrast_limits=(0, 1))
            @magicgui(call_button="Apply", opacity={"min": 0., "max": 1.})
            def control(opacity: float = 1.):
                layer.opacity = opacity
            try:
                control.opacity.value = .6
                control.call_button.native.click()
                assert layer.opacity == .6
                before = viewer.screenshot(size=(256, 256), canvas_only=True, flash=False)
                assert before.shape == (256, 256, 4) and before.dtype == np.uint8
                assert len(np.unique(before.reshape(-1, 4), axis=0)) > 10
                first = viewer.window._qt_viewer.canvas._scene_canvas
                backend = first._backend
                # A VisPy shared namespace would reuse GL object names, but this
                # backend intentionally owns independent native contexts. Refuse
                # before allocating another native context, preserving the first.
                original_surface = module._Surface
                new_surfaces = []
                def track_surface(*args, **kwargs):
                    surface = original_surface(*args, **kwargs)
                    new_surfaces.append(surface)
                    return surface
                module._Surface = track_surface
                capability = installation.application.backend_module.capability
                original_context_support = capability["context"]
                try:
                    # Pinned Qt currently refuses this upstream as well. Exercise
                    # our own guard with that flag enabled so its safety does not
                    # depend on the upstream advertisement staying unchanged.
                    for upstream_support in (False, True):
                        capability["context"] = upstream_support
                        try:
                            shared_canvas = SceneCanvas(size=(128, 128), shared=first.context, show=False)
                        except RuntimeError as error:
                            assert "shar" in str(error).lower()
                            if upstream_support:
                                assert isinstance(error, module.RendererUnavailable)
                        else:
                            shared_canvas.close()
                            raise AssertionError("unsupported shared native context was accepted")
                finally:
                    capability["context"] = original_context_support
                    module._Surface = original_surface
                assert not new_surfaces, "shared request allocated a native context before refusal"
                assert installation.live == {backend} and not backend._surface.closed
                after_shared_refusal = viewer.screenshot(size=(256, 256), canvas_only=True, flash=False)
                assert np.array_equal(before, after_shared_refusal)
                backend.screen_changed(None)
                try:
                    backend._vispy_set_size(99999, 99999)
                except ValueError:
                    pass
                else:
                    raise AssertionError("oversized framebuffer was allowed")
                after_refusal = viewer.screenshot(size=(256, 256), canvas_only=True, flash=False)
                assert np.array_equal(before, after_refusal)
                viewer.camera.zoom *= 1.5
                moved = viewer.screenshot(size=(256, 256), canvas_only=True, flash=False)
                assert np.any(before != moved)
                # A failed request leaves existing pixels reproducible; a fresh viewer
                # reproduces the same image after the previous viewer is fully closed.
                reference_frames.append(before)
            finally:
                control.close()
        finally:
            viewer.close()
            app.processEvents()
        assert not installation.live, "deleted Qt viewer left a native surface owned"
    assert all(np.array_equal(frame, reference_frames[0]) for frame in reference_frames)
    if reference is not None:
        viewer = napari.Viewer(ndisplay=3, show=False, show_welcome_screen=False)
        control = None
        try:
            z, y, x = np.mgrid[:24, :32, :40]
            volume = (np.exp(-((z-7)**2/8 + (y-11)**2/20 + (x-13)**2/30))
                      + .7*np.exp(-((z-18)**2/5 + (y-23)**2/11 + (x-29)**2/7))).astype(np.float32)
            original = volume.copy()
            layer = viewer.add_image(volume, name='volume', rendering='mip', scale=(2, 1.5, 1))
            viewer.reset_view()
            viewer.camera.angles = (0, 0, 90)
            first = viewer.screenshot(size=(256, 256), canvas_only=True, flash=False)
            viewer.camera.angles = (25, 40, 15)
            second = viewer.screenshot(size=(256, 256), canvas_only=True, flash=False)
            volume_changes = int(np.count_nonzero(first != second))
            assert volume_changes > 256
            assert np.array_equal(volume, original)
            def choices(widget):
                return [item.name for item in viewer.layers if isinstance(item, napari.layers.Image)]
            @magicgui(call_button='Apply', layer_name={'choices': choices})
            def control(layer_name: str):
                viewer.layers[layer_name].opacity = .35
            viewer.layers.events.inserted.connect(control.layer_name.reset_choices)
            viewer.layers.events.removed.connect(control.layer_name.reset_choices)
            another = viewer.add_image(np.arange(48).reshape(6, 8), name='another')
            assert set(control.layer_name.choices) == {'volume', 'another'}
            control.layer_name.value = 'another'
            control.call_button.native.click()
            assert another.opacity == .35 and layer.opacity == 1
            viewer.layers.remove(another)
            assert tuple(control.layer_name.choices) == ('volume',)
            try:
                control.layer_name.value = 'another'
            except ValueError:
                pass
            else:
                raise AssertionError('removed layer remained selectable')
        finally:
            if control is not None:
                viewer.layers.events.inserted.disconnect(control.layer_name.reset_choices)
                viewer.layers.events.removed.disconnect(control.layer_name.reset_choices)
                control.close()
            viewer.close()
            app.processEvents()
        assert not installation.live
finally:
    installation.close()
    installation.close()
    app.quit()
print(json.dumps({"passed": True, "viewers": 4 if reference else 3, "frame_shape": [256, 256, 4],
                  "volume_changed_components": volume_changes,
                  "versions": {name: importlib.metadata.version(name) for name in module.PINNED},
                  "reference": reference, "backend_version": module.BACKEND_VERSION}), flush=True)
