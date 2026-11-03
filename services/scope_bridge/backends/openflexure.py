"""An OpenFlexure microscope over its HTTP API (openflexure-microscope-server v2, the released one: v2.11.0), standard
library only. v3 (in beta, 2026) moved the API under /api/v3 (stage/move_relative, illumination/set_led): a backend of
its own when it is released.

The stage is in motor steps there; `um_per_step` turns them into µm (an OpenFlexure's own calibration, or 1 for the
harness's virtual scope, whose steps are slide pixels). A move is an action the server runs: posted, then read until it
completes. Pictures come from the server's snapshot of its stream. The server offers no exposure, channel or light
control here (its LED is on with the microscope), so those are `Unavailable` and said so.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from . import Frame, Unavailable

MOVE_TIMEOUT_S = 60.0


class OpenFlexure:
    name = "openflexure"

    def __init__(self, url: str, um_per_step: float = 1.0, um_per_z_step: float | None = None,
                 timeout: float = 10.0) -> None:
        self.url = url.rstrip("/")
        self.um_per_step = float(um_per_step)  # across the slide
        self.um_per_z_step = float(um_per_z_step if um_per_z_step is not None else um_per_step)  # in focus
        self.timeout = timeout
        self._running: str | None = None  # the move action under way, for stop

    def _call(self, method: str, path: str, body: dict | None = None, *, raw: bool = False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                payload = r.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"the OpenFlexure server answered {exc.code} to {path}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"the OpenFlexure server at {self.url} did not answer ({exc})") from exc
        return payload if raw else json.loads(payload or b"{}")

    def describe(self) -> dict:
        return {"backend": "openflexure", "url": self.url, "umPerStep": self.um_per_step, "umPerZStep": self.um_per_z_step}

    def position(self) -> tuple[float, float, float]:
        p = self._call("GET", "/api/v2/instrument/state/stage/position")
        k, kz = self.um_per_step, self.um_per_z_step
        return float(p["x"]) * k, float(p["y"]) * k, float(p["z"]) * kz

    def move_to(self, x: float | None, y: float | None, z: float | None) -> None:
        cx, cy, cz = self.position()
        k, kz = self.um_per_step, self.um_per_z_step
        target = {"x": round((cx if x is None else x) / k), "y": round((cy if y is None else y) / k),
                  "z": round((cz if z is None else z) / kz), "absolute": True}
        action = self._call("POST", "/api/v2/actions/stage/move/", target)
        self._wait(action)

    def _wait(self, action: dict) -> dict:
        href = action.get("href") or (action.get("links") or {}).get("self", {}).get("href")
        self._running = href
        deadline = time.monotonic() + MOVE_TIMEOUT_S
        try:
            while action.get("status") not in ("completed", "error", "cancelled"):  # pending, running
                if time.monotonic() > deadline:
                    raise RuntimeError("the OpenFlexure stage did not finish its move in time")
                time.sleep(0.02)
                action = self._call("GET", href)
        finally:
            self._running = None
        if action.get("status") != "completed":
            raise RuntimeError(f"the OpenFlexure server's action ended {action.get('status')}")
        return action

    def snap(self) -> Frame:
        """A new picture (ram-capture: taken now, so it shows the stage where it is), not the stream's last frame."""
        return Frame(jpeg=self._call("POST", "/api/v2/actions/camera/ram-capture/", {"use_video_port": True}, raw=True),
                     meta={})

    def exposure(self) -> float | None:
        return None

    def set_exposure(self, ms: float) -> None:
        raise Unavailable("this OpenFlexure server does not set the exposure from here")

    def channels(self) -> list[str]:
        return []

    def channel(self) -> str | None:
        return None

    def set_channel(self, name: str) -> None:
        raise Unavailable("this OpenFlexure microscope has no channels")

    def light(self) -> bool | None:
        return None

    def set_light(self, on: bool) -> None:
        raise Unavailable("this OpenFlexure microscope's light is not switched from here")

    def stop(self) -> None:
        """The server's move runs to its end; a stop is the bridge not sending the next one."""
