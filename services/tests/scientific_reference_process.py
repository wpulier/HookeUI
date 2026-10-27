"""The H00 reference side: plain pinned napari in a process of its own, with nothing of Hooke imported.

Run as ``<NAPARI_REFERENCE_PYTHON> -I scientific_reference_process.py`` in an empty working directory, with a JSON
request on stdin: the recipe file, the volume (.npy) the review opens, and the cases. It builds a hidden ``Viewer``
holding that volume, runs ``scientific_comparison_recipes.py`` as plain text, and prints one JSON object: the
runtime, observed GUI profile and each case's observation. No Hooke renderer is imported: the caller explicitly
selects Cocoa/PySide6 in a fresh profile. Unknown profiles are refused, never replaced by a model-only comparator.
Settings are observed, not copied from Hooke to make the comparison pass.
"""
import hashlib
import json
import sys


def run(request):
    namespace = {'__name__': 'scientific_comparison_recipes'}
    with open(request['recipes']) as source:
        recipe = source.read()
    exec(compile(recipe, request['recipes'], 'exec'), namespace)
    if request['profile'] != namespace['PROFILE']:
        raise ValueError('Unknown scientific comparison profile')
    import numpy as np
    import napari
    from napari.plugins import _initialize_plugins

    _initialize_plugins()
    viewer = napari.Viewer(show=False, show_welcome_screen=False)
    try:
        viewer.add_image(np.load(request['volume']), name=request['volume_name'])
        viewer.dims.ndisplay = request['ndisplay']
        profile = namespace['comparison_profile'](viewer, hashlib.sha256(recipe.encode()).hexdigest())
        results = {case['id']: namespace['observe'](case['template'], case['symbol'], viewer, case.get('path'))
                   for case in request['cases']}
        return {'runtime': namespace['runtime'](request['source_paths']), 'profile': profile, 'results': results}
    finally:
        viewer.close()


if __name__ == '__main__':
    request = json.loads(sys.stdin.read())
    output, sys.stdout = sys.stdout, sys.stderr  # recipes may print; only the result goes to the caller
    output.write(json.dumps(run(request)))
    output.flush()
