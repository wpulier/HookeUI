"""Synthetic native-call failures, not evidence of actual Qt/GPU rendering."""
import ctypes as c
import json
from types import SimpleNamespace
import threading

import pytest

from env_worker import napari_cgl as renderer


class Native:
    def __init__(self, fail=None):
        self.fail, self.events = fail, []

    def CGLChoosePixelFormat(self, attrs, fmt, count):
        self.events.append("choose")
        c.cast(fmt, c.POINTER(c.c_void_p))[0] = 1
        c.cast(count, c.POINTER(c.c_int))[0] = 1
        return 1 if self.fail == "choose" else 0

    def CGLCreateContext(self, fmt, shared, context):
        self.events.append("create")
        c.cast(context, c.POINTER(c.c_void_p))[0] = 2
        return 1 if self.fail == "create" else 0

    def CGLSetCurrentContext(self, context):
        self.events.append("current" if context else "clear")
        return 1 if self.fail == "current" and context else 0

    def CGLDestroyContext(self, context):
        self.events.append("destroy")
        return 0

    def CGLDestroyPixelFormat(self, fmt):
        self.events.append("format-destroy")
        return 1 if self.fail == "format-destroy" else 0


class GL:
    GL_FRAMEBUFFER_COMPLETE = 42

    def __init__(self, fail=None):
        self.fail, self.live, self.events, self.next = fail, set(), [], 10

    def __getattr__(self, name):
        if name.startswith("GL_"):
            return 1
        if name.startswith("glGen"):
            def generate(*args):
                self.events.append(name)
                if self.fail == name:
                    raise RuntimeError(name)
                self.next += 1
                self.live.add(self.next)
                return self.next
            return generate
        if name.startswith("glDelete"):
            def delete(*args):
                self.events.append(name)
                self.live.difference_update(args[-1])
                if self.fail == name:
                    raise RuntimeError(name)
            return delete
        def call(*args):
            self.events.append(name)
            if self.fail == name:
                raise RuntimeError(name)
            if name == "glCheckFramebufferStatus":
                return self.GL_FRAMEBUFFER_COMPLETE
        return call


@pytest.mark.parametrize("size", [(0, 1), (-1, 1), (2049, 1), (True, 1), (1.0, 1), (1, "2"), (10**100, 1)])
def test_invalid_viewport_is_refused_before_native_allocation(size):
    lib, gl = Native(), GL()
    with pytest.raises(ValueError):
        renderer._Surface(lib, gl, renderer.ViewportLimits(), *size)
    assert not lib.events and not gl.events


def test_viewport_allowance_is_conservative_and_cannot_raise_hard_cap():
    with pytest.raises(ValueError, match="working memory"):
        renderer.ViewportLimits(max_working_bytes=1024).check(8, 8)
    assert renderer.ViewportLimits(max_working_bytes=2048).check(8, 8) == (8, 8)
    with pytest.raises(ValueError):
        renderer.ViewportLimits(max_side=4096)
    with pytest.raises(ValueError):
        renderer.ViewportLimits(max_working_bytes=2**30)


@pytest.mark.parametrize("failure", ["choose", "create", "current", "format-destroy"])
def test_context_construction_failure_releases_all_acquired_native_handles(failure):
    lib, gl = Native(failure), GL()
    with pytest.raises(renderer.RendererUnavailable):
        renderer._Surface(lib, gl, renderer.ViewportLimits(), 64, 64)
    assert lib.events.count("format-destroy") == 1
    assert lib.events.count("destroy") == (0 if failure == "choose" else 1)
    assert not gl.live


@pytest.mark.parametrize("failure", ["glGenFramebuffers", "glGenTextures", "glGenRenderbuffers", "glTexImage2D",
                                     "glRenderbufferStorage", "glFramebufferTexture2D", "glCheckFramebufferStatus"])
def test_partial_framebuffer_allocation_is_reaped(failure):
    lib, gl = Native(), GL(failure)
    with pytest.raises(RuntimeError):
        renderer._Surface(lib, gl, renderer.ViewportLimits(), 64, 64)
    assert not gl.live
    assert lib.events.count("destroy") == lib.events.count("format-destroy") == 1


def test_resize_failure_closes_surface_without_preserving_broken_handles():
    lib, gl = Native(), GL()
    surface = renderer._Surface(lib, gl, renderer.ViewportLimits(), 64, 64)
    gl.fail = "glTexImage2D"
    with pytest.raises(RuntimeError):
        surface.resize(128, 128)
    assert surface.closed and not gl.live
    surface.close()
    assert lib.events.count("destroy") == 1
    with pytest.raises(renderer.RendererUnavailable, match="closed"):
        surface.current()


def test_bad_resize_preserves_working_surface_and_repeated_close_is_safe():
    lib, gl = Native(), GL()
    surface = renderer._Surface(lib, gl, renderer.ViewportLimits(), 64, 64)
    owned = gl.live.copy()
    with pytest.raises(ValueError):
        surface.resize(99999, 1)
    assert gl.live == owned and not surface.closed
    surface.current()
    surface.close()
    surface.close()
    assert not gl.live and lib.events.count("destroy") == 1


def test_delete_failure_still_attempts_every_attachment_and_context_cleanup():
    lib, gl = Native(), GL()
    surface = renderer._Surface(lib, gl, renderer.ViewportLimits(), 64, 64)
    gl.fail = "glDeleteFramebuffers"
    with pytest.raises(renderer.RendererUnavailable, match="terminate"):
        surface.close()
    assert not gl.live and lib.events.count("destroy") == 1
    surface.close()
    assert lib.events.count("destroy") == 1


def test_other_thread_cannot_use_or_close_the_context():
    surface = renderer._Surface(Native(), GL(), renderer.ViewportLimits(), 64, 64)
    errors = []
    def wrong_thread():
        for action in (surface.current, surface.close):
            try:
                action()
            except renderer.RendererUnavailable as error:
                errors.append(str(error))
    thread = threading.Thread(target=wrong_thread)
    thread.start()
    thread.join()
    assert len(errors) == 2 and not surface.closed
    surface.close()


def supported_runtime(monkeypatch):
    monkeypatch.setattr(renderer.sys, "platform", "darwin")
    monkeypatch.setattr(renderer.platform, "machine", lambda: "arm64")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("QT_API", "pyside6")
    monkeypatch.setattr(renderer.importlib.metadata, "version", renderer.PINNED.__getitem__)


def test_unknown_package_version_refuses_before_loading_any_gui(monkeypatch):
    supported_runtime(monkeypatch)
    monkeypatch.setattr(renderer.importlib.metadata, "version", lambda name: "unknown")
    with pytest.raises(renderer.RendererUnavailable, match="unsupported renderer generation"):
        renderer.install()


@pytest.mark.parametrize("variable,value", [("QT_QPA_PLATFORM", "cocoa"), ("QT_API", "pyqt6")])
def test_wrong_gui_mode_never_falls_back(monkeypatch, variable, value):
    supported_runtime(monkeypatch)
    monkeypatch.setenv(variable, value)
    with pytest.raises(renderer.RendererUnavailable, match="offscreen PySide6"):
        renderer.install()


def test_unsupported_host_refuses_before_loading_gui(monkeypatch):
    monkeypatch.setattr(renderer.sys, "platform", "linux")
    with pytest.raises(renderer.RendererUnavailable, match="Apple Silicon"):
        renderer.install()


def test_reference_does_not_weaken_default_version_gate(monkeypatch):
    supported_runtime(monkeypatch)
    versions = {**renderer.PINNED, "napari": "0.7.1.dev0"}
    monkeypatch.setattr(renderer.importlib.metadata, "version", versions.__getitem__)
    with pytest.raises(renderer.RendererUnavailable, match="napari requires 0.9.2"):
        renderer._runtime()
    with pytest.raises(renderer.RendererUnavailable, match="unknown renderer reference"):
        renderer._runtime("arbitrary-source")


@pytest.mark.parametrize("origin", [None, "{}", "[]", "x" * 16385, "invalid JSON",
    json.dumps({"url": "file:///candidate.whl", "archive_info": {"hashes": {"sha256": "0" * 64}}}),
    json.dumps({"url": "file:///candidate.whl#sha256=" + "0" * 64,
                "archive_info": {"hashes": {"sha256": renderer.REFERENCE_WHEEL}}}),
    json.dumps({"url": "file:///candidate.whl#sha256=" + renderer.REFERENCE_WHEEL,
                "archive_info": {"hash": "sha256=" + "0" * 64}}),
    json.dumps({"url": "file:///candidate.whl#sha256=" + renderer.REFERENCE_WHEEL + "&sha256=",
                "archive_info": {}}),
    json.dumps({"url": "file:///candidate.whl", "dir_info": {"editable": True},
                "archive_info": {"hashes": {"sha256": renderer.REFERENCE_WHEEL}}})])
def test_same_version_without_exact_managed_artifact_is_refused(monkeypatch, origin):
    supported_runtime(monkeypatch)
    versions = {**renderer.PINNED, "napari": "0.7.1.dev0"}
    monkeypatch.setattr(renderer.importlib.metadata, "version", versions.__getitem__)
    monkeypatch.setattr(renderer.importlib.metadata, "distribution",
                        lambda name: SimpleNamespace(read_text=lambda filename: origin))
    with pytest.raises(renderer.RendererUnavailable, match="exact prepared napari wheel"):
        renderer._runtime(renderer.REFERENCE_REVISION)


@pytest.mark.parametrize("fragment", [False, True])
def test_explicit_reference_accepts_exact_wheel_but_not_changed_native_dependencies(monkeypatch, fragment):
    supported_runtime(monkeypatch)
    versions = {**renderer.PINNED, "napari": "0.7.1.dev0"}
    monkeypatch.setattr(renderer.importlib.metadata, "version", versions.__getitem__)
    origin = json.dumps({"url": "file:///private/napari-0.7.1.dev0-py3-none-any.whl"
                         + ("#sha256=" + renderer.REFERENCE_WHEEL if fragment else ""),
                         "archive_info": {} if fragment else {"hashes": {"sha256": renderer.REFERENCE_WHEEL}}})
    monkeypatch.setattr(renderer.importlib.metadata, "distribution",
                        lambda name: SimpleNamespace(read_text=lambda filename: origin))
    renderer._runtime(renderer.REFERENCE_REVISION)
    versions["vispy"] = "unsupported"
    with pytest.raises(renderer.RendererUnavailable, match="vispy requires"):
        renderer._runtime(renderer.REFERENCE_REVISION)


def test_installation_closes_all_canvases_even_if_one_cleanup_fails():
    events = []
    class Canvas:
        def __init__(self, fail):
            self.fail = fail
        def _vispy_close(self):
            events.append(self.fail)
            if self.fail:
                raise RuntimeError("native failure")
    original, backend = object(), object()
    application = SimpleNamespace(backend_module=SimpleNamespace(CanvasBackend=backend))
    installation = renderer.Installation(application, original, backend, {Canvas(True), Canvas(False)})
    with pytest.raises(renderer.RendererUnavailable, match="terminate"):
        installation.close()
    assert sorted(events) == [False, True]
    assert application.backend_module.CanvasBackend is original
    installation.close()
    assert len(events) == 2


def test_incomplete_framebuffer_is_refused_and_reaped():
    lib, gl = Native(), GL()
    gl.glCheckFramebufferStatus = lambda target: -1
    with pytest.raises(renderer.RendererUnavailable, match="incomplete"):
        renderer._Surface(lib, gl, renderer.ViewportLimits(), 64, 64)
    assert not gl.live and lib.events.count("destroy") == 1


def test_integer_scalar_dimensions_from_upstream_remain_supported():
    import numpy as np
    assert renderer.ViewportLimits().check(np.int64(256), np.int64(256)) == (256, 256)
    with pytest.raises(ValueError):
        renderer.ViewportLimits().check(np.bool_(True), 256)


def test_small_integer_scalars_cannot_overflow_memory_admission():
    import numpy as np
    with pytest.raises(ValueError, match="working memory"):
        renderer.ViewportLimits(max_working_bytes=4096).check(np.uint8(200), np.uint8(200))
