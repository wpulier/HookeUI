"""Micro-Manager through pymmcore-plus (BSD-3; pymmcore and MMCore are LGPL, the device adapters each their own licence).

With no configuration it loads Micro-Manager's demo configuration (MMConfig_demo.cfg: DemoCamera's camera, XY stage,
Z stage, shutters, autofocus and the Channel group DAPI, FITC, Cy5, Rhodamine), found beside the device adapters
`mmcore install --test-adapters` downloads (`adapters`, else pymmcore-plus's own search). A rig's own configuration file
is `config`. Only in the scope bridge's own environment (`make scope-setup`); nothing else imports it.

On a Mac with Apple silicon Micro-Manager publishes only these test adapters; a real rig's adapters are built from
source (`mmcore build-dev`), and many vendors' are Windows only (AGENTS.md beside the bridge).

The light is the configuration's shutter: on is the shutter held open (auto-shutter off), off closes it and gives the
shutter back to the camera's exposures. Acquisitions run through pymmcore-plus's own engine (`core.mda`) from a
useq-schema MDASequence made from the checked plan.
"""
from __future__ import annotations

import threading
from pathlib import Path

from . import Frame, Unavailable


def adapters_folder(adapters: str | None = None) -> Path:
    """The Micro-Manager folder with the device adapters (and the demo configuration): the one given, else the pinned
    test adapters' wheel (mm-test-adapters: DemoCamera, Utilities and the demo configuration, for macOS on Apple silicon
    too), else whatever pymmcore-plus finds (`mmcore install`)."""
    if adapters:
        return Path(adapters)
    try:
        import mm_test_adapters

        return Path(mm_test_adapters.device_adapter_path())
    except ImportError:
        pass
    from pymmcore_plus import find_micromanager

    found = find_micromanager()
    if not found:
        raise Unavailable("Micro-Manager's device adapters are not installed: run make scope-setup")
    return Path(found)


class MMBackend:
    name = "mm-demo"

    def __init__(self, config: str | None = None, adapters: str | None = None) -> None:
        from pymmcore_plus import CMMCorePlus

        folder = adapters_folder(adapters)
        self.core = CMMCorePlus()
        self.core.setDeviceAdapterSearchPaths([str(folder)])
        path = Path(config) if config else folder / "MMConfig_demo.cfg"
        self.core.loadSystemConfiguration(str(path))
        self.name = "mm" if config else "mm-demo"
        self.config = str(path)
        self.channel_group = "Channel" if "Channel" in self.core.getAvailableConfigGroups() else None
        self._lock = threading.RLock()

    def describe(self) -> dict:
        c = self.core
        return {"backend": self.name, "config": Path(self.config).name, "camera": c.getCameraDevice() or None,
                "xyStage": c.getXYStageDevice() or None, "focus": c.getFocusDevice() or None,
                "shutter": c.getShutterDevice() or None, "pixelSizeUm": c.getPixelSizeUm() or None,
                "image": [c.getImageWidth(), c.getImageHeight()], "channelGroup": self.channel_group}

    def position(self) -> tuple[float, float, float]:
        c = self.core
        with self._lock:
            x, y = c.getXYPosition() if c.getXYStageDevice() else (0.0, 0.0)
            z = c.getPosition() if c.getFocusDevice() else 0.0
        return float(x), float(y), float(z)

    def move_to(self, x: float | None, y: float | None, z: float | None) -> None:
        c = self.core
        with self._lock:
            if x is not None or y is not None:
                if not c.getXYStageDevice():
                    raise Unavailable("this microscope has no motorised XY stage")
                cx, cy = c.getXYPosition()
                c.setXYPosition(cx if x is None else x, cy if y is None else y)
                c.waitForDevice(c.getXYStageDevice())
            if z is not None:
                if not c.getFocusDevice():
                    raise Unavailable("this microscope has no motorised focus")
                c.setPosition(z)
                c.waitForDevice(c.getFocusDevice())

    def snap(self) -> Frame:
        with self._lock:
            pixels = self.core.snap()
            meta = {"exposureMs": self.core.getExposure(), "channel": self.channel(),
                    "pixelSizeUm": self.core.getPixelSizeUm() or None}
        return Frame(pixels=pixels, meta=meta)

    def exposure(self) -> float | None:
        return float(self.core.getExposure()) if self.core.getCameraDevice() else None

    def set_exposure(self, ms: float) -> None:
        if not self.core.getCameraDevice():
            raise Unavailable("this microscope has no camera")
        with self._lock:
            self.core.setExposure(float(ms))

    def channels(self) -> list[str]:
        return list(self.core.getAvailableConfigs(self.channel_group)) if self.channel_group else []

    def channel(self) -> str | None:
        return self.core.getCurrentConfig(self.channel_group) or None if self.channel_group else None

    def set_channel(self, name: str) -> None:
        if name not in self.channels():
            raise Unavailable(f"there is no channel called {name!r} (there are {', '.join(self.channels()) or 'none'})")
        with self._lock:
            self.core.setConfig(self.channel_group, name)
            self.core.waitForConfig(self.channel_group, name)

    def light(self) -> bool | None:
        c = self.core
        if not c.getShutterDevice():
            return None
        return bool(c.getShutterOpen()) and not c.getAutoShutter()

    def set_light(self, on: bool) -> None:
        c = self.core
        if not c.getShutterDevice():
            raise Unavailable("this microscope's light is not under its control (no shutter)")
        with self._lock:
            if on:
                c.setAutoShutter(False)
                c.setShutterOpen(True)
            else:
                c.setShutterOpen(False)
                c.setAutoShutter(True)

    def stop(self) -> None:
        c = self.core
        try:
            if c.mda.is_running():
                c.mda.cancel()
        except Exception:
            pass
        for device in (c.getXYStageDevice(), c.getFocusDevice()):
            if device:
                try:
                    c.stop(device)
                except Exception:
                    pass

    def acquire(self, plan, *, here, exposure_ms: float, on_frame, stopping: threading.Event) -> int:
        """The plan through pymmcore-plus's acquisition engine (a useq-schema MDASequence)."""
        import useq

        positions = [{k: v for k, v in zip("xyz", p) if v is not None} for p in plan.positions]
        seq = useq.MDASequence(
            channels=[{"group": self.channel_group, "config": n, **({"exposure": e} if e is not None else {})}
                      for n, e in plan.channels] if self.channel_group else [],
            time_plan={"interval": plan.interval, "loops": plan.loops} if plan.loops > 1 else None,
            z_plan={"relative": list(plan.z_offsets)} if plan.z_offsets != (0.0,) else None,
            stage_positions=positions or [{"x": here[0], "y": here[1], "z": here[2]}],
            axis_order=plan.axis_order)
        made = 0

        def ready(img, event, meta=None):
            nonlocal made
            made += 1
            on_frame(Frame(pixels=img, meta={"exposureMs": event.exposure or exposure_ms}),
                     {k: v for k, v in dict(event.index).items()})

        finished = threading.Event()

        def watch():  # a stop cancels the run; the watch ends with it
            while not finished.is_set():
                if stopping.wait(0.1):
                    if self.core.mda.is_running():
                        self.core.mda.cancel()
                    return

        self.core.mda.events.frameReady.connect(ready)
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            self.core.mda.run(seq)
        finally:
            finished.set()
            self.core.mda.events.frameReady.disconnect(ready)
        return made
