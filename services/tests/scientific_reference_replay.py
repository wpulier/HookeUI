"""P2 reference: independent pinned napari GUI, public setters and actual draw callbacks.

The initial layer tuple stays JSON text until this Python process decodes it, so
JavaScript cannot erase float spellings or round large native integer values.
No Hooke renderer is imported. The caller owns a fresh Cocoa profile.
"""
import hashlib
import json
import sys


def run(request):
    import numpy as np
    import napari
    from napari.layers import Layer
    from napari.plugins import _initialize_plugins

    namespace = {'__name__': 'scientific_comparison_recipes'}
    with open(request['recipes']) as source:
        recipe = source.read()
    exec(compile(recipe, request['recipes'], 'exec'), namespace)
    if request['profile'] != namespace['PROFILE']:
        raise ValueError('Unknown scientific comparison profile')
    start = json.loads(request['initial'])
    size = request['canvas_size']
    if type(size) is not list or len(size) != 2 or any(type(n) is not int or not 1 <= n <= 1024 for n in size):
        raise ValueError('Reference needs the observed bounded canvas size')
    _initialize_plugins()
    viewer = napari.Viewer(show=False, show_welcome_screen=False)
    try:
        for name, layer in start['layers'].items():
            data = (np.load(request['arrays'][name], allow_pickle=False) if name in request.get('arrays', {})
                    else tuple(np.asarray(part['values'], dtype=part['dtype']) for part in layer['data']))
            viewer.add_layer(Layer.create(data, layer['state'], layer['type']))
        viewer.dims.ndisplay = start['ndisplay']
        viewer.window._qt_viewer.canvas.size = tuple(size)
        namespace['apply_equivalent'](['camera', start['camera']], viewer)
        for field in ('mouse_pan', 'mouse_zoom', 'synced'):
            setattr(viewer.camera, field, start['camera'][field])
        names = list(start['layers'])

        def observe():
            # Hooke publishes a rendered frame before its console observation.
            # Let plain napari's own draw synchronize the GUI camera too.
            viewer.screenshot(size=tuple(size), canvas_only=True, flash=False)
            return namespace['snapshot'](names, viewer)

        states, done = {'initial': observe()}, 0
        profile = namespace['comparison_profile'](viewer, hashlib.sha256(recipe.encode()).hexdigest())
        for checkpoint in request['checkpoints']:
            for op in request['ops'][done:checkpoint['ops']]:
                namespace['apply_equivalent'](op, viewer)
                # Every recorded op corresponds to an Apply and its drawn preview.
                viewer.screenshot(size=tuple(size), canvas_only=True, flash=False)
            done = checkpoint['ops']
            states[checkpoint['id']] = namespace['snapshot'](names, viewer)
        return {'runtime': namespace['runtime'](request['source_paths']), 'profile': profile, 'states': states}
    finally:
        viewer.close()


if __name__ == '__main__':
    request = json.loads(sys.stdin.read())
    output, sys.stdout = sys.stdout, sys.stderr
    output.write(json.dumps(run(request)))
    output.flush()
