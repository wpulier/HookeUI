"""Worker-local, pinned offscreen napari backend; not a package bridge or feature gate.

The parent must admit and confine the worker before importing this module. This code
never starts a process, grants access, installs dependencies or falls back to a desktop
window. It owns CGL contexts and framebuffer attachments on one GUI thread. Parent
memory/deadline/process-tree limits remain necessary: bounds here cover viewport work,
not arbitrary layer arrays, plugin code or the driver's internal allocations.

The private VisPy hook is supported only for PINNED versions on Apple Silicon macOS.
Installing it is not scientific proof, rights clearance or authorization to expose a
package. A dedicated worker calls install(), creates hidden viewers and closes the
installation after closing those viewers. Display pixels are derived presentation data.
"""
from __future__ import annotations

import ctypes as c
import importlib.metadata
import json
import os
from numbers import Integral
import platform
import sys
import threading
from urllib.parse import parse_qs, urlsplit
from dataclasses import dataclass

BACKEND_VERSION = 2
PINNED = {"napari": "0.9.2", "vispy": "0.17.0", "PySide6": "6.12.0", "QtPy": "2.4.3",
          "PyOpenGL": "3.1.10", "numpy": "2.5.4"}
REFERENCE_REVISION = "ebfc4a0a1e5386f41582761204f416fffe6cd0b6"
REFERENCE_WHEEL = "ea1b1d4681556c3d811a334d60ce3f812e91600081eec74a1874d232fecbaf4f"


class RendererUnavailable(RuntimeError):
    """This worker cannot provide the pinned renderer; do not fall back."""


@dataclass(frozen=True)
class ViewportLimits:
    max_side: int = 2048
    max_working_bytes: int = 128 * 1024**2

    def __post_init__(self):
        if type(self.max_side) is not int or not 1 <= self.max_side <= 2048:
            raise ValueError("viewport side limit must be between 1 and 2048")
        if type(self.max_working_bytes) is not int or not 32 <= self.max_working_bytes <= 128 * 1024**2:
            raise ValueError("viewport working allowance must be between 32 bytes and 128 MiB")

    def check(self, width: int, height: int) -> tuple[int, int]:
        # Conservative allowance for attachments, intermediate render/readback and Qt
        # copies. This is not a bound on the whole worker or GPU driver's memory.
        if any(isinstance(n, bool) or not isinstance(n, Integral) or not 1 <= n <= self.max_side for n in (width, height)):
            raise ValueError(f"viewport dimensions must be integers from 1 to {self.max_side}")
        width, height = int(width), int(height)
        if width * height * 32 > self.max_working_bytes:
            raise ValueError("viewport exceeds its working memory allowance")
        return int(width), int(height)


def _runtime(reference: str | None = None) -> None:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise RendererUnavailable("the CGL renderer requires Apple Silicon macOS")
    if threading.current_thread() is not threading.main_thread():
        raise RendererUnavailable("the renderer must start on the worker's main thread")
    if os.environ.get("QT_QPA_PLATFORM") != "offscreen" or os.environ.get("QT_API") != "pyside6":
        raise RendererUnavailable("the worker must select offscreen PySide6 before starting")
    if any(name in sys.modules for name in ("PyQt6", "PyQt5", "PySide2")):
        raise RendererUnavailable("another Qt binding is already loaded")
    if reference not in (None, REFERENCE_REVISION):
        raise RendererUnavailable("unknown renderer reference; prepare the pinned runtime")
    versions = PINNED if reference is None else {**PINNED, "napari": "0.7.1.dev0"}
    try:
        mismatches = [f"{name} requires {version}" for name, version in versions.items()
                      if importlib.metadata.version(name) != version]
    except importlib.metadata.PackageNotFoundError as error:
        raise RendererUnavailable(f"renderer dependency missing: {error.name}") from error
    if mismatches:
        raise RendererUnavailable("unsupported renderer generation: " + "; ".join(mismatches))
    if reference is not None:
        try:
            origin = importlib.metadata.distribution("napari").read_text("direct_url.json")
            if not origin or len(origin) > 16384:
                raise ValueError("missing or oversized origin")
            origin = json.loads(origin)
            if set(origin) != {"url", "archive_info"} or type(origin["archive_info"]) is not dict:
                raise ValueError("wrong artifact")
            digest = origin["archive_info"].get("hashes", {}).get("sha256")
            fragment = parse_qs(urlsplit(origin["url"]).fragment, keep_blank_values=True)
            hashes = ([digest] if digest is not None else []) + fragment.get("sha256", [])
            legacy = origin["archive_info"].get("hash")
            if legacy is not None:
                if type(legacy) is not str or not legacy.startswith("sha256="):
                    raise ValueError("unsupported artifact hash")
                hashes.append(legacy.removeprefix("sha256="))
            if not hashes or any(value != REFERENCE_WHEEL for value in hashes):
                raise ValueError("wrong artifact")
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
            raise RendererUnavailable("reference renderer needs the exact prepared napari wheel") from error


def _library():
    try:
        lib = c.CDLL("/System/Library/Frameworks/OpenGL.framework/OpenGL")
        for name, args in {
            "CGLChoosePixelFormat": [c.POINTER(c.c_int), c.POINTER(c.c_void_p), c.POINTER(c.c_int)],
            "CGLCreateContext": [c.c_void_p, c.c_void_p, c.POINTER(c.c_void_p)],
            "CGLSetCurrentContext": [c.c_void_p], "CGLDestroyContext": [c.c_void_p],
            "CGLDestroyPixelFormat": [c.c_void_p],
        }.items():
            function = getattr(lib, name)
            function.argtypes, function.restype = args, c.c_int
        return lib
    except (AttributeError, OSError) as error:
        raise RendererUnavailable("required Apple CGL functions are unavailable") from error


def _ok(result, operation):
    if result:
        raise RendererUnavailable(f"CGL {operation} failed ({result})")


class _Surface:
    """One native context; its attachments never outlive its explicit owner."""
    def __init__(self, lib, gl, limits: ViewportLimits, width: int, height: int):
        width, height = limits.check(width, height)  # refuse before any native allocation
        self.lib, self.gl, self.limits = lib, gl, limits
        self.thread = threading.get_ident()
        self.context, self.closed = c.c_void_p(), False
        self.fbo = self.texture = self.depth = 0
        self.size = None
        fmt, count = c.c_void_p(), c.c_int()
        try:
            # Allow offline renderer; 24-bit color/depth and 8-bit alpha. No window drawable.
            attrs = (c.c_int * 8)(96, 8, 24, 12, 24, 11, 8, 0)
            _ok(lib.CGLChoosePixelFormat(attrs, c.byref(fmt), c.byref(count)), "choose pixel format")
            if not fmt.value or count.value < 1:
                raise RendererUnavailable("CGL supplied no pixel format")
            _ok(lib.CGLCreateContext(fmt, None, c.byref(self.context)), "create context")
            if not self.context.value:
                raise RendererUnavailable("CGL supplied no context")
            self.resize(width, height)
        except BaseException as error:
            try:
                self.close()
            except Exception as cleanup:
                error.add_note(f"renderer cleanup also failed: {cleanup}")
            raise
        finally:
            if fmt.value:
                # Release even if context creation fails. A release error is surfaced.
                result = lib.CGLDestroyPixelFormat(fmt)
                if result and sys.exc_info()[0] is None:
                    self.close()
                    _ok(result, "destroy pixel format")

    def _thread(self):
        if threading.get_ident() != self.thread:
            raise RendererUnavailable("CGL accessed outside its owning GUI thread")

    def current(self):
        self._thread()
        if self.closed or not self.context.value:
            raise RendererUnavailable("renderer is closed")
        _ok(self.lib.CGLSetCurrentContext(self.context), "make current")
        self.gl.glBindFramebuffer(self.gl.GL_FRAMEBUFFER, self.fbo)

    def _delete_attachments(self):
        gl = self.gl
        failures = []
        for attr, delete in (("fbo", lambda n: gl.glDeleteFramebuffers(1, [n])),
                             ("texture", lambda n: gl.glDeleteTextures([n])),
                             ("depth", lambda n: gl.glDeleteRenderbuffers(1, [n]))):
            value = getattr(self, attr)
            setattr(self, attr, 0)
            if value:
                try:
                    delete(value)
                except Exception as error:
                    failures.append(error)
        if failures:
            raise RendererUnavailable("could not release framebuffer attachments") from failures[0]

    def resize(self, width: int, height: int):
        width, height = self.limits.check(width, height)
        self.current()
        if self.size == (width, height):
            return
        gl = self.gl
        try:
            # Release old attachments before allocating the new size; never hold two
            # admitted framebuffers at once. Failure makes this surface unusable.
            self._delete_attachments()
            self.fbo = int(gl.glGenFramebuffers(1))
            self.texture = int(gl.glGenTextures(1))
            self.depth = int(gl.glGenRenderbuffers(1))
            if not all((self.fbo, self.texture, self.depth)):
                raise RendererUnavailable("OpenGL did not allocate framebuffer attachments")
            gl.glBindTexture(gl.GL_TEXTURE_2D, self.texture)
            gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA8, width, height, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, None)
            gl.glBindRenderbuffer(gl.GL_RENDERBUFFER, self.depth)
            gl.glRenderbufferStorage(gl.GL_RENDERBUFFER, gl.GL_DEPTH_COMPONENT24, width, height)
            gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.fbo)
            gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0, gl.GL_TEXTURE_2D, self.texture, 0)
            gl.glFramebufferRenderbuffer(gl.GL_FRAMEBUFFER, gl.GL_DEPTH_ATTACHMENT, gl.GL_RENDERBUFFER, self.depth)
            if gl.glCheckFramebufferStatus(gl.GL_FRAMEBUFFER) != gl.GL_FRAMEBUFFER_COMPLETE:
                raise RendererUnavailable("OpenGL framebuffer is incomplete")
            self.size = width, height
        except BaseException as error:
            try:
                self.close()
            except Exception as cleanup:
                error.add_note(f"renderer cleanup also failed: {cleanup}")
            raise

    def close(self):
        self._thread()
        if self.closed:
            return
        self.closed = True
        failures = []
        try:
            if self.context.value:
                _ok(self.lib.CGLSetCurrentContext(self.context), "make current for cleanup")
                self._delete_attachments()
        except Exception as error:
            failures.append(error)
        finally:
            if self.context.value:
                context, self.context = self.context, c.c_void_p()
                for function, value in ((self.lib.CGLSetCurrentContext, None), (self.lib.CGLDestroyContext, context)):
                    try:
                        _ok(function(value), "release context")
                    except Exception as error:
                        failures.append(error)
        if failures:
            raise RendererUnavailable("renderer cleanup failed; terminate its owned worker") from failures[0]


class Installation:
    """A private backend patch owned by one dedicated worker, explicitly reversible."""
    def __init__(self, application, original, backend, live, cleanup_failures=None, restore_config=lambda: None):
        self.application, self.original, self.backend, self.live = application, original, backend, live
        self.thread, self.closed = threading.get_ident(), False
        self.cleanup_failures = cleanup_failures if cleanup_failures is not None else []
        self.restore_config = restore_config

    def close(self):
        if threading.get_ident() != self.thread:
            raise RendererUnavailable("renderer installation closed outside its owning GUI thread")
        if self.closed:
            return
        self.closed = True
        failures = self.cleanup_failures
        for canvas in tuple(self.live):
            try:
                canvas._vispy_close()
            except Exception as error:
                failures.append(error)
        if self.application.backend_module.CanvasBackend is self.backend:
            self.application.backend_module.CanvasBackend = self.original
            self.restore_config()
        if failures:
            raise RendererUnavailable("renderer cleanup failed; terminate its owned worker") from failures[0]


def install(limits: ViewportLimits = ViewportLimits(), *, reference: str | None = None) -> Installation:
    """Install before creating viewers, in an already confined dedicated worker.

    This intentionally does not expose plugin callbacks or unbounded screenshot APIs.
    A future bridge must validate rendering requests before calling upstream screenshot.
    """
    if not isinstance(limits, ViewportLimits):
        raise TypeError("limits must be ViewportLimits")
    _runtime(reference)
    lib = _library()
    from qtpy.QtWidgets import QApplication, QWidget
    from qtpy.QtGui import QImage, QPainter
    from qtpy.QtCore import Signal
    import vispy
    from vispy import app as vapp
    from OpenGL import GL
    from shiboken6 import isValid, delete
    import numpy as np

    # Let napari initialize its Qt defaults (including tabular numerals) before
    # VisPy claims the native application. Creating Qt through VisPy first skips
    # napari's first-application initialization when the Viewer later opens.
    from napari.qt import get_qapp
    get_qapp()
    application = vapp.use_app("pyside6")
    application.create()  # retain the same app through VisPy's native-app owner
    if QApplication.platformName() != "offscreen":
        raise RendererUnavailable("Qt application is not offscreen")
    from vispy.app.backends._qt import QtBaseCanvasBackend
    original = application.backend_module.CanvasBackend
    if getattr(original, "_observation_cgl", False):
        raise RendererUnavailable("renderer backend is already installed")
    original_dpi = vispy.config["dpi"]
    vispy.config["dpi"] = 96
    live, cleanup_failures = set(), []

    class CanvasBackendCGL(QtBaseCanvasBackend, QWidget):
        _observation_cgl = True
        resized = Signal()

        def __init__(self, *args, **kwargs):
            self._surface = None
            try:
                super().__init__(*args, **kwargs)
                live.add(self)
                # napari may delete the parent QWidget without VisPy.close(). Own
                # the native surface independently and reap it on that path too.
                def destroyed():
                    try:
                        self._surface.close()
                    except Exception as error:
                        cleanup_failures.append(error)
                    finally:
                        live.discard(self)
                self.destroyed.connect(destroyed)
            except BaseException as error:
                try:
                    if self._surface is not None:
                        self._surface.close()
                except Exception as cleanup:
                    error.add_note(f"renderer cleanup also failed: {cleanup}")
                finally:
                    if isValid(self):
                        delete(self)
                raise

        def _init_specific(self, params, kwargs):
            QWidget.__init__(self, params.parent)
            # GLShared carries GL object names/parser state. Joining it without
            # native CGL sharing can render unrelated objects with colliding names.
            # Every surface here owns an independent context; refuse any namespace
            # previously associated with a backend, even if its old owner closed.
            if params.context.shared.name is not None:
                raise RendererUnavailable("shared native contexts are unsupported by this renderer")
            self._surface = _Surface(lib, GL, limits, *params.size)
            params.context.shared.add_ref("qt-cgl", self)
            self._vispy_canvas.events.initialize()
            self._initialized = True

        def _vispy_set_current(self):
            self._surface.current()

        def _vispy_get_fb_bind_location(self):
            return self._surface.fbo

        def _vispy_swap_buffers(self):
            self._surface.current()
            GL.glFinish()

        def _vispy_get_size(self):
            return self._surface.size

        def _vispy_get_physical_size(self):
            return self._surface.size

        def _vispy_set_size(self, width, height):
            width, height = limits.check(width, height)
            if self._surface.size != (width, height):
                self._surface.resize(width, height)
                QWidget.resize(self, width, height)
                self._vispy_canvas.events.resize(size=(width, height), physical_size=(width, height))

        def screen_changed(self, new_screen):
            # This offscreen canvas has a fixed pixel ratio. The inherited Qt
            # implementation calls QOpenGLWidget.resizeGL, which we do not use.
            self._surface.current()

        def resizeEvent(self, event):
            if self._surface is not None and not self._surface.closed:
                width, height = event.size().width(), event.size().height()
                if self._surface.size != (width, height):
                    self._surface.resize(width, height)
                    self._vispy_canvas.events.resize(size=(width, height), physical_size=(width, height))
                self.resized.emit()

        def grabFramebuffer(self):
            self._surface.current()
            limits.check(*self._surface.size)
            pixels = np.ascontiguousarray(self._vispy_canvas.render())
            if pixels.dtype != np.uint8 or pixels.shape != (self._surface.size[1], self._surface.size[0], 4):
                raise RendererUnavailable("renderer returned unexpected display pixels")
            return QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0], QImage.Format.Format_RGBA8888).copy()

        def paintEvent(self, event):
            image = self.grabFramebuffer()
            painter = QPainter(self)
            try:
                painter.drawImage(self.rect(), image)
            finally:
                painter.end()

        def _vispy_close(self):
            try:
                self._surface.close()
            finally:
                live.discard(self)
                if isValid(self):
                    QWidget.close(self)

    application.backend_module.CanvasBackend = CanvasBackendCGL
    return Installation(application, original, CanvasBackendCGL, live, cleanup_failures,
                        lambda: vispy.config.__setitem__("dpi", original_dpi))
