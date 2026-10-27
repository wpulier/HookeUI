"""Bounded camera-only gestures on the pinned native VisPy viewbox.

This deliberately targets the camera, not arbitrary plugin mouse callbacks.
The owner supplies an admitted current rendered frame before preparing it.
"""
from .scientific_view_wire import integer, number, require, fields, vector


def prepare(viewer, size, value):
    fields(value, 'mode width height points')
    mode, points = value['mode'], value['points']
    require(type(mode) is str and mode in ('orbit','pan','zoom')
            and integer(value['width'],1,1024) and integer(value['height'],1,1024)
            and size == (value['width'],value['height'])
            and type(points) is list and 2 <= len(points) <= 256)
    for point in points:
        vector(point,2)
        require(0 <= point[0] < size[1] and 0 <= point[1] < size[0])
    if viewer.dims.ndisplay != 3 or viewer.grid.enabled:
        raise ValueError('Camera gestures need a current single 3D view')
    canvas=viewer.window._qt_viewer.canvas
    if tuple(canvas.size) != (size[1],size[0]):
        raise ValueError('The scientific viewport changed; refresh it before moving the camera')
    camera=canvas.camera._view.camera
    if not camera.interactive or not camera.mouse_pan:
        raise ValueError('This integration has disabled camera interaction')
    if mode == 'zoom':
        factor=(1+camera.zoom_factor)**(points[-1][0]-points[0][0])
        zoom=viewer.camera.zoom/factor
        if not number(zoom) or not 1e-6 <= zoom <= 1e6:
            raise ValueError('This zoom gesture exceeds the supported camera range')
    # Imports and native event construction cannot execute contributor callbacks.
    from vispy.app.canvas import MouseEvent
    from vispy.scene.events import SceneMouseEvent
    from vispy.util import keys
    modifiers=(keys.SHIFT,) if mode=='pan' else ()
    button=2 if mode=='zoom' else 1
    def apply():
        view=canvas.camera._view
        start=MouseEvent('mouse_press',pos=points[0][::-1],button=button,buttons=[button],modifiers=modifiers)
        previous=start
        try:
            camera.viewbox_mouse_event(SceneMouseEvent(start,view))
            # The initial move seeds arcball rotation just as native dragging does.
            for point in points:
                event=MouseEvent('mouse_move',pos=point[::-1],button=button,buttons=[button],modifiers=modifiers,
                                 press_event=start,last_event=previous)
                camera.viewbox_mouse_event(SceneMouseEvent(event,view));previous=event
        finally:
            end=MouseEvent('mouse_release',pos=previous.pos,button=button,buttons=[],modifiers=modifiers,
                           press_event=start,last_event=previous)
            camera.viewbox_mouse_event(SceneMouseEvent(end,view))
        canvas.camera.on_draw(None)
    return apply
