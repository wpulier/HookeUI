"""H06 reference: unmodified napari GUI on Cocoa, with no Hooke renderer or scientific adapter imported.

argv: private work folder. stdin carries scene, steps, viewport and the common runtime/profile observer.
Unknown or incompatible native/runtime profiles fail; there is no offscreen or model-only fallback.
"""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys


def run(request, work):
    import importlib.metadata as md
    import numpy as np
    import napari
    from napari.plugins import _initialize_plugins
    from qtpy.QtWidgets import QApplication

    observer = {'__name__': 'scientific_comparison_recipes'}
    exec(compile(request['observer'], 'scientific_comparison_recipes.py', 'exec'), observer)
    pixels_observer = {'__name__': 'scientific_pixel_profile'}
    exec(compile(request['pixel_observer'], 'scientific_pixel_profile.py', 'exec'), pixels_observer)
    pixels_observer['configure_reference_typography']()
    _initialize_plugins()
    viewer = napari.Viewer(show=False, show_welcome_screen=False)
    try:
        # Observe before scene edits, as on the worker; neither side adjusts its settings to match.
        profile = observer['comparison_profile'](viewer, hashlib.sha256(request['observer'].encode()).hexdigest())
        namespace = {'viewer': viewer, 'np': np, '__name__': '__reference__'}
        exec(request['scene'], namespace)
        before_settings = observer['comparison_profile'](viewer, '')['settings']
        pixel_profile = pixels_observer['realize_pixel_canvas'](viewer, request['width'], request['height'])
        assert observer['comparison_profile'](viewer, '')['settings'] == before_settings, 'realization changed GUI settings'
        results = []
        for index, step in enumerate(request['steps']):
            record = {'step': index}
            exec(step['apply'], namespace)
            QApplication.processEvents()
            if step.get('observe'):
                captured = io.StringIO()
                with contextlib.redirect_stdout(captured):
                    exec(step['observe'], namespace)
                record['observed'] = json.loads(captured.getvalue())
            if step.get('render'):
                pixels, record['pixel_capture'] = pixels_observer['pixel_render'](viewer, request['width'], request['height'])
                np.save(work / f'step-{index}.npy', np.ascontiguousarray(pixels))
                record['pixels'] = f'step-{index}.npy'
            results.append(record)
        root = Path(napari.__file__).resolve().parent
        loaded = {}
        for module in list(sys.modules.values()):
            file = getattr(module, '__file__', None)
            path = Path(file).resolve() if file else None
            if path is not None and root in path.parents:
                loaded[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        return {'results': results, 'napari': napari.__version__, 'profile': profile, 'pixel_profile': pixel_profile,
                'runtime': observer['runtime'](request['source_paths']), 'loaded_napari_files': loaded,
                'napari_origin': json.loads(md.distribution('napari').read_text('direct_url.json') or '{}')}
    finally:
        viewer.close()


if __name__ == '__main__':
    request = json.loads(sys.stdin.read())
    output, sys.stdout = sys.stdout, sys.stderr
    output.write(json.dumps(run(request, Path(sys.argv[1]))))
    output.flush()
