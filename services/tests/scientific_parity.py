"""Compare one behaviour in Hooke's actual review worker with plain pinned napari, and both with independent values.

A comparison is a scene (Python that builds the same layers in both) and steps. Each step applies a change in
Hooke through its ordinary worker request (a frame-bound `change`, as the Studio sends it) and the same change
in the reference process through napari's public API, then reads the same observation code in both (Hooke's own
Python console; the reference's namespace) and, when asked, renders both at one size. The caller compares the
two observations, the two renders and its own independently computed expectations.

Kept separate from any one family so lane-parity's shared harness can adopt it: `reference_run` is the plain
napari side, `HookeSide` the actual worker side, and `pixel_at`/`screen_of` the coordinate oracle for renders.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess

import numpy as np
import pytest

from orchestrator import owned_process, sandbox, worker_resources

HERE = Path(__file__).parent
SERVICES = HERE.parent
PROBE = HERE / 'scientific_parity_reference.py'
PIXEL_OBSERVER = HERE / 'scientific_pixel_profile.py'
INVENTORY = SERVICES.parent / 'services/bench/specification/atomic-cases.json'
DEADLINE_S = 600


def reference_python():
    """The prepared plain napari runtime, or a skip: an absent runtime is never a pass."""
    location = os.environ.get('NAPARI_REFERENCE_PYTHON')
    if not location:
        pytest.skip('requires the prepared pinned napari reference runtime (NAPARI_REFERENCE_PYTHON)')
    return Path(location).absolute()


def pinned_source_check(executable, paths):
    """The reference runtime's napari files hash to the inventory's pinned archive."""
    site = executable.parent.parent / 'lib/python3.12/site-packages'
    pinned = dict(json.loads(INVENTORY.read_text())['sources'])
    if not set(paths) <= set(pinned):  # files no child cites: hash them from the pinned archive itself
        from bench import scientific_acceptance, scientific_inventory
        files = scientific_inventory._archive_files(scientific_acceptance.default_archive())
        pinned.update({path: scientific_inventory.sha(files[path]) for path in paths if path not in pinned})
    changed = []
    for path in paths:
        local = site / path.removeprefix('src/')
        if hashlib.sha256(local.read_bytes()).hexdigest() != pinned[path]:
            changed.append(path)
    assert not changed, f'the reference runtime is not the pinned napari source: {changed}'
    return {path: pinned[path] for path in paths}


def runtime_identity(executable):
    root = executable.parent.parent
    receipt = json.loads((root / 'receipt.json').read_text())
    return {'generation': receipt['generation'], 'lockSha256': receipt['lockSha256'], 'python': receipt['python'],
            'packages': receipt['packages'], 'machine': platform.machine(), 'system': platform.platform(),
            'renderer': 'Hooke offscreen CGL; independent reference Cocoa GUI',
            'evidence': 'preparation metadata only; observed runtime/profile admission is required'}


def observation_source():
    """Run the common observer through the existing worker/Studio console, without changing settings."""
    from tests import test_scientific_reference_comparison as comparison
    source = comparison.RECIPES.read_text()
    return (source + '\n' + PIXEL_OBSERVER.read_text()
            + '\nimport json as _json\nprint(_json.dumps({"runtime": runtime('
            f'{sorted(comparison.PINNED_SOURCES)!r}), "profile": comparison_profile(viewer, '
            f'{hashlib.sha256(source.encode()).hexdigest()!r}), '
            '"pixel_profile": pixel_profile(viewer)}))')


def admit(report, hooke):
    """A comparison needs observed approved source/wheel/dependencies and matching actual GUI settings."""
    from tests import scientific_napari_reference
    from tests import test_scientific_reference_comparison as comparison
    report['pinned'] = scientific_napari_reference.pinned_source_check(report)
    pair = {'hooke': hooke, 'reference': report}
    comparison._assert_pinned_runtime(pair)
    comparison._assert_gui_profile(pair)
    from tests.scientific_pixel_profile import require_pixel_profile
    for side, observation in pair.items():
        require_pixel_profile(observation['pixel_profile'], side)
    assert (report['pixel_profile']['typography']['font'] == hooke['pixel_profile']['typography']['font']), \
        'worker/reference glyph font rasterization differs'
    return report


def reference_run(scene, steps, *, width, height, work):
    """Run trusted fixture steps in an independent Cocoa GUI, with a private home and owned resource bounds."""
    executable = reference_python()
    work.mkdir(parents=True, exist_ok=True)
    from tests import test_scientific_reference_comparison as comparison
    request = json.dumps({'scene': scene, 'steps': steps, 'width': width, 'height': height,
                          'observer': comparison.RECIPES.read_text(), 'pixel_observer': PIXEL_OBSERVER.read_text(),
                          'source_paths': sorted(comparison.PINNED_SOURCES)}).encode()
    # Like H00's reference, Cocoa needs WindowServer; do not broaden the production worker sandbox.
    command = [str(executable), '-I', '-u', str(PROBE), str(work)]
    environment = sandbox.confine({'PATH': '/usr/bin:/bin', 'QT_QPA_PLATFORM': 'cocoa', 'QT_API': 'pyside6',
                                   'NAPARI_ASYNC': '0', 'NAPARI_CONFIG': str(work / 'napari.yaml'),
                                   'QT_SCALE_FACTOR': '0.5',
                                   'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
                                   'VECLIB_MAXIMUM_THREADS': '1', 'NUMBA_NUM_THREADS': '1'}, work)
    with (work / 'stderr.log').open('wb') as errors:
        process = subprocess.Popen(command, cwd=work, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=errors, start_new_session=True, close_fds=True)
        guard = None
        try:
            guard = worker_resources.Guard(process, work, worker_resources.Limits(memory_bytes=2 << 30, work_entries=512))
            guard.start()
            with owned_process.watching(process, DEADLINE_S, 'pinned napari reference'):
                output, _ = process.communicate(request, timeout=DEADLINE_S)
            guard.check()
        finally:
            try:
                if guard is not None:
                    guard.close()
            finally:
                if process.poll() is None:
                    owned_process.terminate(process)
    assert process.returncode == 0, (work / 'stderr.log').read_text()[-8000:]
    (work / 'report.json').write_bytes(output)  # retain exact reference observations before admission/comparison
    result = json.loads(output)
    from tests.scientific_napari_reference import pinned_source_check as approved_sources
    result['pinned'] = approved_sources(result)
    for record in result['results']:
        if 'pixels' in record:
            record['pixels'] = np.load(work / record['pixels'])
            from tests.scientific_pixel_profile import require_pixel_capture
            require_pixel_capture(record['pixel_capture'], width, height, record['pixels'])
    return result


class HookeSide:
    """Hooke's actual review worker, driven only through its ordinary requests."""

    def __init__(self, worker, width, height):
        self.worker, self.width, self.height = worker, width, height
        self.frame = None

    def admit(self, reference):
        """Observe this actual session before comparing its behavior with the independent reference."""
        return admit(reference, self.observe(observation_source()))

    def render(self):
        reply, pixels = self.worker.request({'op': 'render', 'width': self.width, 'height': self.height})
        self.frame = reply['frame']
        return self.frame, self.pixels(pixels)

    def pixels(self, raw):
        assert self.frame['width'] == self.width and self.frame['height'] == self.height and self.frame['pixelRatio'] == 1, \
            'worker pixel viewport differs from controlled profile'
        return np.frombuffer(raw, dtype=np.uint8).reshape(self.height, self.width, 4)

    def layer(self, name):
        if self.frame is None:
            self.render()
        return next(row for row in self.frame['layers'] if row['name'] == name)

    def change(self, operation, value):
        """One frame-bound change, exactly as the Studio sends it; the next frame and its pixels."""
        if self.frame is None:
            self.render()
        reply, pixels = self.worker.request({'op': 'change', 'width': self.width, 'height': self.height,
                                             'request': {'frame': self.frame['frame'], 'operation': operation, 'value': value}})
        self.frame = reply['frame']
        return self.frame, self.pixels(pixels)

    def python(self, source):
        result, _ = self.worker.request({'op': 'python-resources', 'source': source})
        self.frame = None  # code may change the view, so the next change waits for a new rendered frame
        console = result['console']
        assert not console['error'], console
        return console['stdout']

    def observe(self, source):
        return json.loads(self.python(source))

    def inspect(self, layer, position):
        if self.frame is None:
            self.render()
        reply, _ = self.worker.request({'op': 'inspect', 'frame': self.frame['frame'], 'layer': layer,
                                        'position': [float(n) for n in position]})
        return reply['inspection']


def screen_of(data_yx, *, center_yx, zoom, width, height):
    """The canvas pixel (row, column) where a 2D data pixel's center lands, for a scale-1 layer and an explicit camera."""
    # A data pixel covers [i - .5, i + .5) in world units; the camera center sits at the canvas center.
    y, x = data_yx
    return (int(np.floor(height / 2 + (y - center_yx[0]) * zoom)), int(np.floor(width / 2 + (x - center_yx[1]) * zoom)))


def pixel_at(pixels, data_yx, **camera):
    row, column = screen_of(data_yx, **camera)
    return pixels[row, column, :3].astype(int)


def same_render(hooke, reference, *, tolerance=0):
    """Renders agree within the frozen tolerance (largest channel difference); returns that difference."""
    assert hooke.shape == reference.shape and hooke.dtype == reference.dtype == np.uint8
    difference = int(np.abs(hooke.astype(int) - reference.astype(int)).max())
    assert difference <= tolerance, f'Hooke and pinned napari renders differ by up to {difference}'
    assert len(np.unique(hooke.reshape(-1, 4), axis=0)) > 1, 'an empty render proves nothing'
    return difference
