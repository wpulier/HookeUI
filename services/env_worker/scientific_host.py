"""Worker-local scientific view for a private candidate, not a catalog readiness gate.

The parent must authorize resources, confine and supervise the process, and retain
its generation. All mutations here are private previews. Publishing scientific
edits and acknowledging browser presentation remain parent-owned operations.
"""
from __future__ import annotations

import json

import copy

from dataclasses import dataclass, field
import math
import secrets
import threading
from types import MappingProxyType

from . import scientific_text_appearance, scientific_grid, scientific_slices, scientific_point_appearance, scientific_surface_appearance, scientific_path_appearance, scientific_colormap, scientific_image_sampling, scientific_clipping, scientific_image_plane, scientific_camera, scientific_image_appearance, napari_cgl, scientific_view_wire, scientific_console, scientific_layer_exports, scientific_view_bindings, scientific_view_preferences, scientific_labels, scientific_label_wire, scientific_labels_appearance, scientific_image_histogram, scientific_labels_tool, scientific_annotate, scientific_geometry_style
from .scientific_controls import Panel, Stale, Unsupported
from .scientific_callback_errors import recoverable_callback
from .scientific_input_refusal import InputRefused

class ChangeRefused(ValueError):
    """View validation failed before applying any requested change."""


class ControlFailed(RuntimeError):
    """An admitted plugin callback failed; private work may already have changed."""


class RestoreRefused(ChangeRefused):
    """Saved presentation is incompatible; no view or scientific value was changed."""


def number(value):
    if type(value) not in (int, float) or abs(value) > 2**53 - 1 or not math.isfinite(value):
        raise ValueError('Scientific view coordinates must be finite numbers')
    return value


def vector(value, size):
    if type(value) is not list or len(value) != size:
        raise ValueError('The scientific view vector has the wrong dimensions')
    return [number(item) for item in value]


def input_meaning(layer, source):
    """A bound input's accepted meaning in coordinate axes: RGB storage keeps a trailing color axis."""
    rgb = bool(getattr(layer, 'rgb', False))
    return {'semantic': source.descriptor.get('semantic'), 'axes': list(source.axes[:-1] if rgb else source.axes)}


@dataclass(frozen=True)
class Context:
    viewer: object
    inputs: object
    bind_editable: object
    bind_view: object
    files: object = None
    bind_native_panel: object = None
    bind_document: object = None
    restored_layers: object = None
    bind_sequence: object = None
    bind_view_output: object = None
    bind_recipe: object = None
    output_binding_version: int = 2
    _decoded_cache: object = field(default=None, init=False, repr=False, compare=False)

    @property
    def streamed_image_publication(self):
        """Supported immutable grey Save admission, not scientific qualification.

        The aggregate byte ceiling does not replace the eight-input/chunk-index,
        disk, source-identity, read-rate, deadline or resident-memory gates.
        Return a fresh description so integrations cannot change the host limit.
        """
        from .scientific_output_wire import MAX_STREAMED_BYTES
        return {'version': 1, 'maximum_bytes': MAX_STREAMED_BYTES}

    def decode_image(self, *, input, shape, dtype, axes, decode):
        """Create a lazy image from synchronous regions of a declared file input."""
        from .lazy_arrays import ChunkCache
        from .scientific_decoders import image
        if self._decoded_cache is None:
            object.__setattr__(self, '_decoded_cache', ChunkCache())
        return image(self.files, input=input, shape=shape, dtype=dtype, axes=axes,
                     decode=decode, cache=self._decoded_cache)

    def refuse_input(self, reason, next_step):
        """Stop opening with explicit user guidance, never arbitrary exception text."""
        raise InputRefused(reason, next_step)


# What a starting worker reports, in order, before it is ready: the integration's code (imported before the session),
# napari's viewer, any saved layers, then the integration's panel. Only these names cross the protocol; the parent
# words them. A step may be skipped (no saved layers, no code to load), never repeated or reordered.
STARTUP_STEPS = ('code', 'viewer', 'layers', 'tool')


class ReviewSession:
    def __init__(self, factory, inputs, *, retained_layers=None, retained_tool=None, initialize_tool=None, recipe_tool=None, interrupts=None, files=None, progress=None):
        """progress(step) names each startup step as it begins (STARTUP_STEPS), for the parent to show."""
        if not callable(factory) or type(inputs) is not dict:
            raise ValueError('Scientific review needs a declared factory and authorized native inputs')
        if initialize_tool is not None and (type(initialize_tool) is not int or initialize_tool != 1
                or retained_layers is None or retained_tool is not None or files):
            raise ValueError('Unsupported tool initialization; retain the original scientific document')
        if recipe_tool is not None:
            from .scientific_tool_document import document
            recipe_tool = document(recipe_tool)
            if initialize_tool != 1 or retained_tool is not None or retained_layers is None or files:
                raise ValueError('Recipe configuration requires a new retained-input tool session')
        self._thread = threading.get_ident()
        self._closed = False
        self._frame = None
        self._layers = []
        self._label_editors = {}
        self._annotations = None
        self._retired_label_histories = []
        self._connections = []
        self._files = dict(files or {})
        self.native_panel = None
        self.tool_document = None
        self.recipe_binding = None
        self.sequence = None
        self._binding_contributions = False
        self.viewer = self.panel = self.container = self.backend = self.console = self.editable = self.view_bindings = None
        self.families = None
        self.dirty = False
        self._size = None
        self._description = None
        self._restoring = None
        starting = progress or (lambda step: None)
        try:
            starting('viewer')
            self.backend = napari_cgl.install(reference=napari_cgl.REFERENCE_REVISION,
                                            limits=napari_cgl.ViewportLimits(max_side=1024, max_working_bytes=32 << 20))
            import napari
            self.viewer = napari.Viewer(show=False, show_welcome_screen=False)
            self.editable = scientific_layer_exports.EditableLayers(self.viewer, inputs)
            from .scientific_family_drafts import FamilyDrafts
            self.families = FamilyDrafts(inputs, lambda layer: self.editable.input_for(layer),
                                         lambda name: self.editable.values_for(name))
            self.console = scientific_console.Console(self.viewer, inputs, interrupts=interrupts, files=files,
                                                      families=self.families)
            self.view_bindings = scientific_view_bindings.ViewBindings(self.viewer)
            restored = None
            before = None
            if retained_tool is not None or initialize_tool is not None:
                from .scientific_tool_document import snapshot, scientific_identity
            if retained_tool is not None:
                if retained_layers is None:
                    raise ValueError('A saved tool requires its retained scientific layers')
                retained_tool = snapshot(retained_tool, retained_layers)
            if retained_layers is not None:
                starting('layers')
                from .scientific_outputs import restore
                self.container = restore(self.viewer, inputs, retained_layers, self.editable, self.view_bindings,
                    keys=retained_tool['bindings'] if retained_tool is not None else None)
                if retained_tool is not None or initialize_tool is not None:
                    restored = MappingProxyType({self.view_bindings.key_for(layer): layer for layer in self.viewer.layers})
                    before = scientific_identity(self.viewer)
            if retained_layers is None or retained_tool is not None or initialize_tool is not None:
                if self.container is not None:
                    self.container.close()
                    self.container = None
                self._binding_contributions = True
                starting('tool')
                try:
                    self.container = factory(Context(self.viewer, MappingProxyType(inputs.copy()), self.editable.bind,
                        self.view_bindings.bind, MappingProxyType(dict(files or {})), self.bind_native_panel,
                        self.bind_document, restored, self.bind_sequence, self.bind_view_output, self.bind_recipe))
                finally:
                    self._binding_contributions = False
                if retained_tool is not None:
                    if self.tool_document is None:
                        raise ValueError('This integration no longer declares restoration for its saved tool')
                    self.tool_document.restore(retained_tool['document'])
                    if 'recipe' in retained_tool and (self.recipe_binding is None or json.dumps(self.recipe_binding.capture(), sort_keys=True) != json.dumps(retained_tool['recipe'], sort_keys=True)):
                        raise ValueError('Restored recipe settings differ from the retained tool; preserve the original document')
                if recipe_tool is not None:
                    if self.recipe_binding is None:
                        raise ValueError('This integration does not declare recipe configuration; use its original saved tool')
                    self.recipe_binding.restore(recipe_tool)
                if retained_tool is not None or initialize_tool is not None:
                    if scientific_identity(self.viewer) != before:
                        raise ValueError('Tool initialization or restoration changed saved scientific layers; the original document is preserved')
            self.view_bindings.seal()
            self.editable.seal()
            self.panel = Panel(self.container)
            self.panel.snapshot()
            for events in self._view_events():
                self._connect(events)
        except BaseException as error:
            try:
                self.close()
            except Exception as cleanup:
                error.add_note(f'Scientific host cleanup also failed: {cleanup}')
            raise

    def _check(self):
        if self._closed or threading.get_ident() != self._thread:
            raise Stale('This scientific view is closed or called outside its GUI thread')

    def _invalidate(self, *args):
        if self.editable is not None:
            self.editable.refresh()
        if self.view_bindings is not None:
            self.view_bindings.refresh()
        self._frame = None
        self.panel.invalidate()
        self._invalidate_native_panel()
        if getattr(self, 'native_panel', None) is not None:
            self._refresh_layer_connections()

    def _invalidate_native_panel(self):
        native = getattr(self, 'native_panel', None)
        if native is not None:
            native.invalidate()

    def bind_native_panel(self, widget):
        """Explicit private factory integration; never an automatic Qt fallback."""
        self._check()
        if not self._binding_contributions or self.native_panel is not None:
            raise ChangeRefused('Declare one native panel while creating this scientific session')
        from .scientific_native_panel import Panel as NativePanel
        self.native_panel = NativePanel(widget)

    def bind_view_output(self, *, version, layer, key, meaning=None, coordinates_from=None):
        """Name a new GUI-thread output without granting edits or publication."""
        self._check()
        if (type(version) is not int or version not in (1, 2)
                or (version == 1 and coordinates_from is not None)
                or (version == 2 and (coordinates_from is None or meaning is None))):
            raise ChangeRefused('Use version 1, or version 2 with meaning and an explicit coordinates_from input')
        if meaning is not None and self.editable.input_for(layer) is not None:
            raise ChangeRefused('Retained inputs keep their accepted meaning; declare a new output layer')
        self.view_bindings.bind_output(layer=layer, key=key, meaning=meaning,
            coordinates_from=coordinates_from, editable=self.editable if version == 2 else None)

    def bind_document(self, *, schema, version, capture, restore):
        """Declare data-only tool state; accepted layer/document saves stay parent-owned."""
        self._check()
        if not self._binding_contributions or self.tool_document is not None:
            raise ChangeRefused('Declare one tool document while creating this scientific session')
        from .scientific_tool_document import Binding
        self.tool_document = Binding(schema=schema, version=version, capture=capture, restore=restore)

    def bind_recipe(self, *, schema, version, capture, configure):
        """Declare reproducible settings only; configuration must not execute analysis."""
        self._check()
        if not self._binding_contributions or self.recipe_binding is not None:
            raise ChangeRefused('Declare one recipe configuration while creating this scientific session')
        from .scientific_tool_document import Binding
        self.recipe_binding = Binding(schema=schema, version=version, capture=capture, restore=configure)

    def bind_sequence(self, *, version, name, count, select):
        """Declare frame selection, without granting files, encoding or publication."""
        self._check()
        if not self._binding_contributions or self.sequence is not None:
            raise ChangeRefused('Declare one frame sequence while creating this scientific session')
        from .scientific_sequence import Binding
        self.sequence = Binding(version=version, name=name, count=count, select=select)

    def begin_sequence(self):
        self._check()
        if self.sequence is None:
            raise ChangeRefused('This integration has no declared movie sequence; retain its original document')
        from .scientific_native_panel import callback_errors
        with callback_errors():
            return self.sequence.begin()

    def render_sequence(self, index, width, height):
        self._check()
        if self.sequence is None:
            raise ChangeRefused('This integration has no declared movie sequence')
        from .scientific_native_panel import callback_errors
        with callback_errors():
            self.sequence.frame(index)
            self._frame = None
            return self.render(width, height)

    def observe(self):
        """Deliver queued GUI callbacks without invoking an operation or painting."""
        self._check()
        if self.native_panel is None:
            raise ChangeRefused('This integration has no declared native panel')
        from .scientific_native_panel import callback_errors, CallbackFailed
        self._refresh_layer_connections()
        try:
            with callback_errors():
                self.native_panel.app.processEvents()
        except CallbackFailed as error:
            self._frame = None
            self.dirty = True
            self.panel.invalidate()
            from .scientific_callback_errors import callback_summary
            raise ControlFailed('A background plugin callback failed. Inspect private work before continuing.\n'
                                + callback_summary(error)) from error
        return {'imageFrame': self._frame}

    def render_native_panel(self, width, height):
        self._check()
        if self.native_panel is None:
            raise ChangeRefused('This integration has no declared native panel')
        from .scientific_native_panel import CallbackFailed
        self._refresh_layer_connections()
        try:
            return self.native_panel.paint(width, height)
        except CallbackFailed as error:
            self._frame = None
            self.dirty = True
            self.panel.invalidate()
            from .scientific_callback_errors import callback_summary
            raise ControlFailed('A plugin callback failed while refreshing its controls. '
                                'Inspect private work before continuing.\n' + callback_summary(error)) from error

    def change_native_panel(self, request):
        self._check()
        from . import scientific_native_panel_wire
        from .scientific_native_panel import CallbackFailed, Refused
        if self.native_panel is None or self.native_panel.current is None:
            raise ChangeRefused('Inspect the current native panel before interacting')
        try:
            scientific_native_panel_wire.command(request, self.native_panel.current)
        except ValueError as error:
            raise ChangeRefused('The native panel changed; inspect it before interacting') from error
        self._frame = None
        self.dirty = True
        try:
            self.native_panel.dispatch(request)
        except Refused as error:
            raise ChangeRefused(str(error)) from error
        except CallbackFailed as error:
            from .scientific_callback_errors import callback_summary
            raise ControlFailed('The panel callback failed; private work may have changed.\n'
                                + callback_summary(error)) from error
        finally:
            self.panel.invalidate()
            self._invalidate_native_panel()

    def _connect(self, events):
        if not any(previous is events for previous in self._connections):
            events.connect(self._invalidate)
            self._connections.append(events)

    def _view_events(self):
        return (self.viewer.dims.events, self.viewer.camera.events, self.viewer.layers.events,
                self.viewer.layers.selection.events, self.viewer.canvas.events, self.viewer.grid.events)

    @staticmethod
    def _layer_events(layer):
        return (layer.events,*scientific_text_appearance.events(layer),*scientific_clipping.events(layer),*scientific_surface_appearance.events(layer),
                *([layer.colormap.events] if layer._type_string in ('image', 'labels') else []),
                *([layer.histogram.events] if layer._type_string == 'image' else []))

    def _refresh_layer_connections(self):
        if len(self.viewer.layers) > 64 or not 2 <= self.viewer.dims.ndim <= 8:
            raise Unsupported('Private scientific review supports at most 64 layers and 8 dimensions')
        wanted = [*self._view_events(), *(event for layer in self.viewer.layers for event in self._layer_events(layer))]
        for events in self._connections[:]:
            if not any(events is current for current in wanted):
                events.disconnect(self._invalidate)
                self._connections.remove(events)
        for event in wanted:
            self._connect(event)

    def _layer_rows(self):
        self._refresh_layer_connections()
        layers, rows = [], []
        for layer in self.viewer.layers:
            if layer._type_string not in ('image', 'labels', 'points', 'shapes', 'surface', 'vectors', 'tracks'):
                raise Unsupported('This layer needs a declared viewer integration')
            old = next((item for item in self._layers if item[1] is layer), None)
            identity = old[0] if old else secrets.token_hex(16)
            if len(layer.name) > 1000:
                raise Unsupported('The layer name exceeds its display allowance')
            layers.append((identity, layer))
            rows.append({'id': identity, 'name': layer.name, 'type': layer._type_string,
                         'visible': bool(layer.visible), 'opacity': float(layer.opacity),
                         'scale': [float(n) for n in layer.scale], 'translate': [float(n) for n in layer.translate]})
            if layer._type_string in ('points','vectors','tracks','shapes'):
                rows[-1]['elementCount']=len(layer.data)
            if layer._type_string in ('points','shapes') and layer.ndim >= 2:
                book = self._annotations.history.get(identity) if self._annotations is not None else None
                rows[-1]['annotation'] = scientific_annotate.summary(layer, book)
            if hasattr(layer, 'experimental_clipping_planes'):
                rows[-1]['clippingPlanes'] = scientific_clipping.snapshot(layer)
            if hasattr(layer, 'blending'):
                rows[-1]['blending'] = str(layer.blending)
            if layer._type_string == 'image':
                if not layer.rgb:
                    rows[-1]['imageColormap'] = scientific_colormap.snapshot_map(layer.colormap)
                    rows[-1]['colormapChoices'] = scientific_colormap.choices(layer)
                rows[-1]['imageProjection'] = scientific_slices.projection(str(layer.projection_mode))
                rows[-1]['imageSampling'] = scientific_image_sampling.snapshot(layer)
                rows[-1]['imageAppearance'] = scientific_image_appearance.snapshot(layer)
                rows[-1]['imageHistogram'] = scientific_image_histogram.snapshot(layer)
                rows[-1]['imagePlane'] = scientific_image_plane.snapshot(layer)
            if layer._type_string == 'labels':
                rows[-1]['labelsAppearance'] = scientific_labels_appearance.snapshot(layer)
                rows[-1]['labelsSelectedColor'] = scientific_labels_appearance.selected_color(layer)
            if layer._type_string in ('points','shapes'):
                rows[-1]['textAppearance'] = scientific_text_appearance.snapshot(layer)
                rows[-1]['textContent'] = scientific_geometry_style.text_snapshot(layer)
                rows[-1]['styleChoices'] = scientific_geometry_style.style_choices(layer)
            if layer._type_string == 'points':
                rows[-1]['pointAppearance'] = scientific_point_appearance.snapshot(layer)
                rows[-1]['pointStyle'] = scientific_geometry_style.point_snapshot(layer)
            if layer._type_string == 'shapes':
                rows[-1]['shapeStyle'] = scientific_geometry_style.shape_snapshot(layer)
            if layer._type_string == 'surface':
                rows[-1]['surfaceAppearance'] = scientific_surface_appearance.snapshot(layer)
            if layer._type_string in ('vectors','tracks'):
                rows[-1]['pathAppearance'] = scientific_path_appearance.snapshot(layer)
            view_key = self.view_bindings.key_for(layer)
            if view_key is not None:
                rows[-1]['viewKey'] = view_key
                rows[-1]['viewGeometry'] = {'affine': layer._data_to_world.affine_matrix.tolist(),
                    'axes': list(layer.axis_labels), 'units': [str(unit) for unit in layer.units]}
            output_meaning = self.view_bindings.meaning_for(layer)
            if output_meaning is not None:
                rows[-1]['outputMeaning'] = output_meaning
            editable = self.editable.input_for(layer)
            if editable is not None:
                rows[-1]['editableInput'] = editable
                if layer._type_string == 'labels':
                    rows[-1]['labelsTool'] = scientific_labels_tool.snapshot(layer)
                source = self.editable.inputs[editable]
                meaning = input_meaning(layer, source)
                if len(meaning['axes']) == len(rows[-1]['scale']):
                    rows[-1]['inputMeaning'] = meaning
        self._layers = layers
        return rows

    def inventory(self):
        self._check()
        return scientific_view_wire.inventory({'format': 'scientific-layers-v1', 'layers': self._layer_rows()})

    def render(self, width, height):
        self._check()
        napari_cgl.ViewportLimits(max_side=1024, max_working_bytes=32 << 20).check(width, height)
        self._frame = None
        scientific_path_appearance.refresh_ranges(self.viewer)
        rows = self._layer_rows()
        grid = scientific_grid.admit(self.viewer, scientific_grid.snapshot(self.viewer.grid), (width, height))
        canvas = self.viewer.window._qt_viewer.canvas
        if not grid['enabled'] and canvas.grid_cameras:
            # Native single-view mode reparents layers but leaves old grid
            # cameras alive. Collapse their empty layout before a smaller
            # viewport can make those inactive panels singular.
            canvas._init_or_update_grid()
        # Normalize spacing for the destination before native resize callbacks.
        # Keeping an old large gap while shrinking can briefly produce negative
        # viewboxes and a singular camera transform before native clamping runs.
        if self.viewer.grid.spacing != grid['spacing']:
            self.viewer.grid.spacing = grid['spacing']
        canvas.size = (height, width)
        if grid['enabled']:
            # Offscreen VisPy resolves new grid viewbox geometry during drawing.
            # Native camera synchronization runs before that draw, so its first
            # pixels may use degenerate/stale panel dimensions. Complete one
            # bounded layout draw, release it, then screenshot synchronizes the
            # cameras against the actual panel sizes. Never publish that draft.
            self.viewer.screenshot(size=(height, width), canvas_only=True, flash=False)
        pixels = self.viewer.screenshot(size=(height, width), canvas_only=True, flash=False)
        if (pixels.dtype.str != '|u1' or pixels.shape != (height, width, 4)
                or tuple(canvas.size) != (height, width)):
            raise Unsupported('The scientific renderer changed its requested viewport')
        # Native resize may clamp spacing. Describe the model that produced
        # these pixels, not the settings sampled before canvas events.
        grid = scientific_grid.snapshot(self.viewer.grid)
        scientific_grid.admit(self.viewer, grid, (width, height))
        dims, camera = self.viewer.dims, self.viewer.camera
        from . import scientific_layer_selection
        body = {'format': 'scientific-host-v1', 'frame': secrets.token_hex(32),
                'width': width, 'height': height, 'pixelRatio': 1, 'derivedDisplay': True,
                'privatePreview': True, 'dirty': self.dirty, 'layers': rows, 'grid': grid,
                'layerSelection': scientific_layer_selection.snapshot(self),
                'dimensions': {'order': list(dims.order), 'displayed': list(dims.displayed),
                               'ndisplay': dims.ndisplay, 'steps': list(dims.current_step),
                               'counts': [int(n) for n in dims.nsteps], 'labels': list(dims.axis_labels),
                               'ranges': [[float(n) for n in bounds] for bounds in dims.range],
                               'point': [float(n) for n in dims.point], 'margins': scientific_slices.snapshot(dims),
                               **scientific_slices.navigation(dims)},
                'camera': {'center': [float(n) for n in camera.center], 'zoom': float(camera.zoom),
                           'orientation': [str(v) for v in camera.orientation],
                           'angles': [float(n) for n in camera.angles], 'perspective': float(camera.perspective)},
                'controls': self.panel.snapshot(), 'nativePanelAvailable': self.native_panel is not None, 'toolDocumentAvailable': getattr(self, 'tool_document', None) is not None}
        body = scientific_view_wire.frame(body)
        if self._restoring is not None:
            if not scientific_view_preferences.restored(self._restoring, scientific_view_preferences.from_frame(body)):
                raise ValueError('A callback changed the restored view. Start a new session with its default view.')
            self._restoring = None
        self._frame, self._size = body['frame'], (width, height)
        self._description = body
        return body, pixels.tobytes(order='C')

    def _current(self, frame):
        self._check()
        if type(frame) is not str or frame != self._frame or self._frame is None:
            raise Stale('Wait for the current scientific frame before changing this view')

    def labels(self, request):
        request = scientific_label_wire.command(request)
        self._current(request['frame'])
        if self._size != (request['width'], request['height']):
            raise Stale('The gesture belongs to a different viewport')
        layer = next((layer for identity, layer in self._layers if identity == request['layer']), None)
        if layer is None:
            raise Stale('The edited layer is no longer present')
        # Resolve the entire gesture while the admitted camera/frame is still current.
        points = [self.data_position(request['frame'], request['layer'], point) for point in request['points']]
        outcome = {'request': request['request'], 'state': 'refused', 'changed': False, 'label': None, 'message': ''}
        try:
            for key, previous in list(self._label_editors.items()):
                if not any(current is previous.layer for current in self.viewer.layers):
                    self._retired_label_histories.append(scientific_labels.histories(previous.layer))
                    del self._label_editors[key]
            editor = self._label_editors.get(request['layer'])
            if editor is None:
                editor = scientific_labels.LabelsEditor(layer, self.editable, history=lambda: [
                    scientific_labels.histories(current) for current in self.viewer.layers if current._type_string == 'labels'] + self._retired_label_histories)
                self._label_editors[request['layer']] = editor
            self._frame = None
            if request['action'] == 'reset':
                editor.reset()
                result = {'changed': False, 'label': str(layer.selected_label)}
            else:
                settings = {key: request[key] for key in ('label','brush','preserve','contiguous','dimensions') if key in request}
                result = editor.apply(request['action'], points=points, **settings)
            self.dirty = self.dirty or result['changed']
            outcome.update(state='applied', **result)
        except scientific_labels.RollbackFailed:
            raise
        except Exception as error:
            outcome['message'] = str(error)[:2000] or 'The edit was refused. Inspect the current layer before retrying.'
        finally:
            self._frame = None
            self.panel.invalidate()
            self._invalidate_native_panel()
        return scientific_label_wire.receipt(outcome, request['request'])

    def navigate(self, identity, first, request):
        from .native_regions import IDENTITY
        try:
            if type(identity) is not str or not IDENTITY.fullmatch(identity) or type(first) is not bool:
                raise ValueError('Navigation requires an admitted identity')
            if type(request) is not dict or set(request) != {'frame', 'operation', 'value'} or request['operation'] != 'dimensions':
                raise ValueError('Navigation requires exact absolute dimension controls')
            anchor = (identity, request['frame'])
            if first:
                self._current(request['frame'])
            elif getattr(self, '_navigation_anchor', None) != anchor:
                raise ValueError('The navigation no longer owns this private view')
            value = request['value']
            if (type(value) is not dict or set(value) != {'order', 'ndisplay', 'steps'}
                    or value['order'] != list(self.viewer.dims.order) or value['ndisplay'] != self.viewer.dims.ndisplay
                    or type(value['steps']) is not list):
                raise ValueError('Latest navigation changes absolute slice indices only')
            same_slice = tuple(value['steps']) == tuple(self.viewer.dims.current_step)
        except ValueError as error:
            raise ChangeRefused(str(error)) from error
        if first:
            self._navigation_anchor = anchor
        self.change(request, navigation_frame=anchor[1])
        if not first and same_slice and getattr(self, '_navigation_needs_refresh', False):
            # A superseded slice may already have changed dims but never completed
            # its pixels. Repeating that absolute target must request its slice again.
            try:
                for layer in self.viewer.layers:
                    if layer.visible:
                        layer.refresh()
            except (ChangeRefused, ControlFailed) as error:
                raise RuntimeError('Scientific refresh failed after navigation started') from error
        self._navigation_needs_refresh = False

    def change(self, request, *, navigation_frame=None):
        self._check()
        try:
            apply = self._prepare_change(request, navigation_frame=navigation_frame)
        except RestoreRefused:
            raise
        except ValueError as error:
            raise ChangeRefused(str(error)) from error
        self._frame = None
        self.dirty = True
        self._invalidate_native_panel()
        try:
            try:
                apply()
            except Exception as error:
                if request['operation'] == 'controls' and recoverable_callback(error):
                    raise ControlFailed('The tool callback failed; private work may have changed') from error
                if isinstance(error, (ChangeRefused, ControlFailed)):
                    # Callback-originated receipt types cannot bypass classification.
                    raise RuntimeError('Scientific change failed after application started') from error
                raise
        finally:
            # A failure in our own invalidation is fatal, not a plugin refusal.
            self.panel.invalidate()

    def _prepare_change(self, request, *, navigation_frame=None):
        # Validation and mutation have distinct outcomes. An exception from an
        # admitted callback must never be reported as a change that did not start.
        if type(request) is not dict or set(request) != {'frame', 'operation', 'value'}:
            raise ValueError('Scientific view changes need an exact frame, operation and value')
        if navigation_frame is None:
            self._current(request['frame'])
        elif request['frame'] != navigation_frame or request['operation'] != 'dimensions':
            raise ValueError('A navigation continuation only changes its admitted dimensions')
        operation, value = request['operation'], request['value']
        if operation == 'camera':
            if type(value) is not dict or set(value) not in ({'center', 'zoom', 'angles'}, {'center','zoom','angles','orientation','perspective'}):
                raise ValueError('Camera changes require center, zoom and angles')
            center, angles = vector(value['center'], 3), vector(value['angles'], 3)
            zoom = number(value['zoom'])
            if not 1e-6 <= zoom <= 1e6:
                raise ValueError('Camera zoom is outside the supported review range')
            orientation = value['orientation'] if 'orientation' in value else [str(v) for v in self.viewer.camera.orientation]
            perspective = number(value.get('perspective', self.viewer.camera.perspective))
            if (type(orientation) not in (list,tuple) or len(orientation)!=3
                    or any(type(v) is not str or v not in options for v,options in zip(orientation,
                        (('towards','away'),('up','down'),('left','right')))) or not 0 <= perspective < 180):
                raise ValueError('Choose valid camera directions and a perspective from 0 to below 180 degrees')
            def apply():
                self.viewer.camera.orientation = orientation
                self.viewer.camera.perspective = perspective
                self.viewer.camera.center = center
                self.viewer.camera.angles = angles
                self.viewer.camera.zoom = zoom
        elif operation == 'camera-reset':
            if value != {}:
                raise ValueError('Reset view takes no additional settings')
            apply = self.viewer.reset_view
        elif operation == 'camera-gesture':
            apply = scientific_camera.prepare(self.viewer, self._size, value)
        elif operation == 'grid':
            settings = scientific_grid.admit(self.viewer, value, self._size)
            def apply():
                scientific_grid.apply(self.viewer.grid, settings)
        elif operation == 'axis-order':
            if type(value) is not str or value not in ('roll', 'transpose'):
                raise ValueError('Choose Roll axes or Swap displayed axes')
            apply = self.viewer.dims.roll if value == 'roll' else self.viewer.dims.transpose
        elif operation == 'dimensions':
            if type(value) is not dict or set(value) != {'order', 'ndisplay', 'steps'}:
                raise ValueError('Dimension changes require order, display mode and steps')
            ndim = self.viewer.dims.ndim
            order, steps = value['order'], value['steps']
            current = self.viewer.dims.current_step
            points = self.viewer.dims.point
            ranges = self.viewer.dims.range
            if (type(order) is not list or any(type(n) is not int for n in order)
                    or sorted(order) != list(range(ndim)) or type(value['ndisplay']) is not int
                    or value['ndisplay'] not in (2, 3) or value['ndisplay'] > ndim
                    or type(steps) is not list or len(steps) != ndim
                    or any(type(n) is not int or not (0 <= n < maximum or n == previous)
                           for n, maximum, previous in zip(steps, self.viewer.dims.nsteps, current))):
                raise ValueError('The requested dimensions do not fit this scientific view')
            target = tuple(point if step == previous else bounds[0] + step * bounds[2]
                           for point, step, previous, bounds in zip(points, steps, current, ranges))
            def apply():
                self.viewer.dims.order = order
                self.viewer.dims.ndisplay = value['ndisplay']
                self.viewer.dims.point = target
        elif operation in ('points-selection','shapes-selection'):
            from .scientific_geometry_selection import prepare
            apply=prepare(self,value,operation.removesuffix('-selection'))
        elif operation == 'layer-presentation':
            if type(value) is not dict or set(value) not in ({'layer', 'visible', 'opacity'}, {'layer', 'visible', 'opacity', 'blending'}):
                raise ValueError('Layer display changes require a current layer, visibility and opacity')
            layer = next((layer for identity, layer in self._layers if identity == value['layer']), None)
            opacity = number(value['opacity'])
            if layer is None or type(value['visible']) is not bool or not 0 <= opacity <= 1:
                raise ValueError('Choose a current layer and an opacity between 0 and 1')
            if 'blending' in value and (type(value['blending']) is not str or value['blending'] not in scientific_view_wire.BLENDING):
                raise ValueError('Choose a supported layer blending mode')
            if getattr(getattr(self.viewer, 'grid', None), 'enabled', False):
                scientific_grid.admit(self.viewer, scientific_grid.snapshot(self.viewer.grid), self._size,
                    visibility=[value['visible'] if current is layer else current.visible for current in self.viewer.layers])
            def apply():
                if 'blending' in value:
                    layer.blending = value['blending']
                layer.visible = value['visible']
                layer.opacity = opacity
        elif operation in scientific_slices.NAVIGATION:
            apply = scientific_slices.prepare_navigation(self.viewer, operation, value)
        elif operation == 'slice-margins':
            settings = scientific_slices.margins(value, self.viewer.dims.ndim)
            def apply():
                scientific_slices.apply(self.viewer.dims, settings)
        elif operation == 'image-projection':
            if type(value) is not dict or set(value) != {'layer', 'mode'}:
                raise ValueError('Image projection requires a current Image layer and mode')
            layer = next((layer for identity, layer in self._layers if identity == value['layer']), None)
            if layer is None or layer._type_string != 'image':
                raise ValueError('Choose a current Image layer')
            mode = scientific_slices.projection(value['mode'])
            def apply():
                layer.projection_mode = mode
        elif operation == 'text-presentation':
            if type(value) is not dict or set(value) != {'layer','settings'}:
                raise ValueError('Text display requires a current layer and complete settings')
            layer=next((layer for identity,layer in self._layers if identity==value['layer']),None)
            if layer is None or layer._type_string not in ('points','shapes'):
                raise ValueError('Choose a current Points or Shapes layer')
            settings=scientific_text_appearance.settings(value['settings'],layer.ndim)
            def apply():
                scientific_text_appearance.apply(layer,settings)
        elif operation == 'annotate':
            width, height = self._size
            value = scientific_annotate.command(value, width, height)
            identity = value['layer']
            layer = next((layer for key, layer in self._layers if key == identity), None)
            if layer is None or layer._type_string not in ('points', 'shapes'):
                raise ValueError('Choose a current Points or Shapes layer')
            if self._annotations is None:
                self._annotations = scientific_annotate.Annotations(self.viewer)
            self._annotations.check(identity, layer, value)
            def apply():
                self._annotations.apply(identity, layer, value, (width, height))
        elif operation == 'labels-tool':
            value = scientific_labels_tool.command(value)
            layer = next((layer for identity, layer in self._layers if identity == value['layer']), None)
            if layer is None or layer._type_string != 'labels' or self.editable.input_for(layer) is None:
                raise ValueError('Choose a current editable Labels layer')
            scientific_labels_tool.admit(layer, value)
            def apply():
                scientific_labels_tool.apply(layer, value)
        elif operation == 'point-presentation':
            if type(value) is not dict or set(value) != {'layer', 'settings'}:
                raise ValueError('Point display requires a current layer and complete settings')
            layer = next((layer for identity, layer in self._layers if identity == value['layer']), None)
            if layer is None or layer._type_string != 'points':
                raise ValueError('Choose a current Points layer')
            settings = scientific_point_appearance.settings(value['settings'])
            def apply():
                scientific_point_appearance.apply(layer, settings)
        elif operation in ('points-style', 'shapes-style', 'text-content', 'shapes-order'):
            kinds = {'points-style': ('points',), 'shapes-style': ('shapes',), 'shapes-order': ('shapes',), 'text-content': ('points', 'shapes')}[operation]
            field = 'action' if operation == 'shapes-order' else 'settings'
            if type(value) is not dict or set(value) != {'layer', field}:
                raise ValueError(f'This style change requires a current layer and its {field}')
            layer = next((layer for identity, layer in self._layers if identity == value['layer']), None)
            if layer is None or layer._type_string not in kinds:
                raise ValueError('Choose a current ' + ' or '.join(kind.capitalize() for kind in kinds) + ' layer')
            prepare = {'points-style': scientific_geometry_style.prepare_points, 'shapes-style': scientific_geometry_style.prepare_shapes,
                       'text-content': scientific_geometry_style.prepare_text, 'shapes-order': scientific_geometry_style.prepare_order}[operation]
            apply = prepare(layer, value[field])
            if operation in ('points-style', 'shapes-style'):
                scientific_geometry_style.reserve(self._description, value['layer'], 'pointStyle' if operation == 'points-style' else 'shapeStyle', value[field])
        elif operation == 'surface-presentation':
            if type(value) is not dict or set(value) != {'layer','settings'}:
                raise ValueError('Surface display requires a current layer and complete settings')
            layer=next((layer for identity,layer in self._layers if identity==value['layer']),None)
            if layer is None or layer._type_string != 'surface':
                raise ValueError('Choose a current Surface layer')
            settings=scientific_surface_appearance.settings(value['settings'])
            def apply():
                scientific_surface_appearance.apply(layer,settings)
        elif operation == 'path-presentation':
            if type(value) is not dict or set(value) != {'layer','settings'}:
                raise ValueError('Path display requires a current layer and complete settings')
            layer=next((layer for identity,layer in self._layers if identity==value['layer']),None)
            if layer is None or layer._type_string not in ('vectors','tracks'):
                raise ValueError('Choose a current Vector or Track layer')
            settings=scientific_path_appearance.settings(value['settings'],layer._type_string)
            scientific_path_appearance.admit(self.viewer,layer,settings)
            def apply():
                scientific_path_appearance.apply(layer,settings)
        elif operation == 'image-presentation':
            if type(value) is not dict or set(value) != {'layer', 'settings'}:
                raise ValueError('Image appearance requires a current image layer and its settings')
            layer = next((layer for identity, layer in self._layers if identity == value['layer']), None)
            if layer is None or layer._type_string != 'image':
                raise ValueError('Choose a current image layer for contrast and volume rendering')
            settings = scientific_image_appearance.settings(value['settings'])
            def apply():
                scientific_image_appearance.apply(layer, settings)
        elif operation in ('image-histogram', 'image-contrast'):
            if type(value) is not dict or set(value) != {'layer', 'settings'}:
                raise ValueError('Histogram and contrast changes require a current image layer and settings')
            layer = next((layer for identity, layer in self._layers if identity == value['layer']), None)
            if layer is None or layer._type_string != 'image':
                raise ValueError('Choose a current image layer')
            if operation == 'image-histogram':
                settings = scientific_image_histogram.settings(value['settings'])
                if settings['enabled']:
                    scientific_image_histogram.reserve(self._description, value['layer'], settings['bins'])
                def apply():
                    scientific_image_histogram.apply(layer, settings)
            else:
                settings = scientific_image_histogram.contrast(value['settings'])
                scientific_image_histogram.admit_contrast(layer, settings)
                def apply():
                    scientific_image_histogram.apply_contrast(layer, settings)
        elif operation == 'labels-presentation':
            if type(value) is not dict or set(value) != {'layer', 'settings'}:
                raise ValueError('Label display requires a current Labels layer and complete settings')
            layer = next((layer for identity, layer in self._layers if identity == value['layer']), None)
            if layer is None or layer._type_string != 'labels':
                raise ValueError('Choose a current Labels layer')
            prepared = scientific_labels_appearance.prepare(layer, value['settings'])
            scientific_labels_appearance.reserve(self._description, value['layer'], prepared.normalized())
            apply = prepared.apply
        elif operation == 'image-colormap':
            if type(value) is not dict or set(value) != {'layer','settings'}:
                raise ValueError('Color changes require a current image layer and color settings')
            layer=next((layer for identity,layer in self._layers if identity==value['layer']),None)
            if layer is None or layer._type_string != 'image' or getattr(layer,'rgb',False):
                raise ValueError('Choose a current scalar image layer for intensity colors; RGB images already contain colors')
            cmap,settings=scientific_colormap.prepare(layer,value['settings'])
            scientific_colormap.reserve(self._description,{value['layer']:cmap})
            def apply():
                layer.colormap=cmap
        elif operation == 'image-sampling':
            if type(value) is not dict or set(value) != {'layer','settings'}:
                raise ValueError('Image interpolation requires a current image layer and complete settings')
            layer=next((layer for identity,layer in self._layers if identity==value['layer']),None)
            if layer is None or layer._type_string != 'image':
                raise ValueError('Choose a current image layer for display interpolation')
            settings=scientific_image_sampling.settings(value['settings'])
            projected=copy.deepcopy(self._description)
            row=next(row for row in projected['layers'] if row['id']==value['layer'])
            row['imageSampling']={**settings,'kernel2d':[['x'*24 for n in line] for line in settings['kernel2d']]}
            scientific_view_wire.bounded(projected,scientific_view_wire.MAX_BYTES-256)
            def apply():
                scientific_image_sampling.apply(layer,settings)
        elif operation == 'clipping-planes':
            if type(value) is not dict or set(value) != {'layer', 'planes'}:
                raise ValueError('Clipping requires a current layer and its complete plane list')
            scientific_view_wire.bounded(value, scientific_view_wire.MAX_BYTES)
            layer = next((layer for identity, layer in self._layers if identity == value['layer']), None)
            if layer is None or not hasattr(layer, 'experimental_clipping_planes'):
                raise ValueError('Choose a current layer that supports clipping planes')
            planes = scientific_clipping.settings(value['planes'])
            # Reserve the complete response before changing native shader state.
            # Native normalization can expand short input numbers substantially.
            projected = copy.deepcopy(self._description)
            row = next(row for row in projected['layers'] if row['id'] == value['layer'])
            row['clippingPlanes'] = [{'position':['x'*24]*3, 'normal':['x'*24]*3,
                                     'enabled':False} for _ in planes]
            scientific_view_wire.bounded(projected, scientific_view_wire.MAX_BYTES - 256)
            def apply():
                scientific_clipping.apply(layer, planes)
        elif operation == 'image-plane':
            if type(value) is not dict or set(value) != {'layer', 'settings'}:
                raise ValueError('An image plane requires a current image layer and its settings')
            layer = next((layer for identity, layer in self._layers if identity == value['layer']), None)
            if layer is None or layer._type_string != 'image':
                raise ValueError('Choose a current image layer for its slicing plane')
            settings = scientific_image_plane.settings(value['settings'])
            def apply():
                scientific_image_plane.apply(layer, settings)
        elif operation == 'layer-selection':
            from .scientific_layer_selection import prepare
            apply = prepare(self, value)
        elif operation == 'layer-order':
            current = dict(self._layers)
            if (type(value) is not list or any(type(identity) is not str for identity in value)
                    or len(value) != len(current) or set(value) != set(current)):
                raise ValueError('Layer order must contain every current layer exactly once')
            grid = None
            if getattr(getattr(self.viewer, 'grid', None), 'enabled', False):
                grid = scientific_grid.admit(self.viewer, scientific_grid.snapshot(self.viewer.grid), self._size,
                    visibility=[current[identity].visible for identity in value])
            def apply():
                if grid is not None: self.viewer.grid.enabled = False
                # Build the prefix from left to right: each remaining layer is at
                # or after its destination, matching napari's pre-move indices.
                for index, identity in enumerate(value):
                    self.viewer.layers.move(self.viewer.layers.index(current[identity]), index)
                if grid is not None: scientific_grid.apply(self.viewer.grid, grid)
        elif operation == 'restore-view':
            try:
                validated = scientific_view_preferences.preferences(value)
                ranges = scientific_path_appearance.projected_ranges(self.viewer,self.view_bindings,validated) if any('pathAppearance' in row for row in validated['layers']) else None
                settings = scientific_view_preferences.compatible(validated, self._description, ranges=ranges)
                if 'grid' in settings:
                    settings['grid'] = scientific_grid.admit(self.viewer, settings['grid'], self._size,
                        visibility=[row['visible'] for row in settings['layers']])
                current = {self.view_bindings.key_for(layer): layer for _, layer in self._layers}
                prepared_labels = {}
                for row in settings['layers']:
                    if 'labelsAppearance' in row:
                        prepared_labels[row['key']] = scientific_labels_appearance.prepare(current[row['key']], row['labelsAppearance'])
                        row['labelsAppearance'] = prepared_labels[row['key']].normalized()
                    if 'pathAppearance' in row:
                        scientific_path_appearance.admit(self.viewer,current[row['key']],row['pathAppearance'])
                prepared_colors = {}
                if any('imageColormap' in row for row in settings['layers']):
                    from napari.utils.colormaps.colormap_utils import AVAILABLE_COLORMAPS
                    palette_registry = dict(AVAILABLE_COLORMAPS)
                    for row in settings['layers']:
                        if 'imageColormap' in row:
                            cmap, row['imageColormap'] = scientific_colormap.prepare(current[row['key']], row['imageColormap'], registry=palette_registry)
                            prepared_colors[row['key']] = cmap
                            palette_registry[cmap.name] = cmap
                    scientific_view_preferences.preferences(settings)
                    scientific_colormap.reserve(scientific_view_preferences.restored_frame_budget(settings,self._description), {identity:prepared_colors[key] for identity,layer in self._layers
                        if (key := self.view_bindings.key_for(layer)) in prepared_colors})
            except ValueError as error:
                raise RestoreRefused(str(error)) from error
            def apply():
                dims, camera = settings['dimensions'], settings['camera']
                if 'grid' in settings: self.viewer.grid.enabled = False
                for row in settings['layers']:
                    if 'pathAppearance' in row:
                        scientific_path_appearance.apply(current[row['key']],row['pathAppearance'])
                scientific_path_appearance.refresh_ranges(self.viewer)
                self.viewer.dims.order = dims['order']
                self.viewer.dims.ndisplay = dims['ndisplay']
                self.viewer.dims.point = dims['point']
                if 'margins' in dims:
                    scientific_slices.apply(self.viewer.dims, dims['margins'])
                self._restoring = settings
                for index, row in enumerate(settings['layers']):
                    self.viewer.layers.move(self.viewer.layers.index(current[row['key']]), index)
                    current[row['key']].visible = row['visible']
                    current[row['key']].opacity = row['opacity']
                    if 'blending' in row:
                        current[row['key']].blending = row['blending']
                    if 'imageColormap' in row:
                        current[row['key']].colormap = prepared_colors[row['key']]
                    if 'imageSampling' in row:
                        scientific_image_sampling.apply(current[row['key']], row['imageSampling'])
                    if 'clippingPlanes' in row:
                        scientific_clipping.apply(current[row['key']], row['clippingPlanes'])
                    if 'imagePlane' in row:
                        scientific_image_plane.apply(current[row['key']], row['imagePlane'])
                    if 'imageProjection' in row:
                        current[row['key']].projection_mode = row['imageProjection']
                    if 'textAppearance' in row:
                        scientific_text_appearance.apply(current[row['key']], row['textAppearance'])
                    if 'pointAppearance' in row:
                        scientific_point_appearance.apply(current[row['key']], row['pointAppearance'])
                    if 'surfaceAppearance' in row:
                        scientific_surface_appearance.apply(current[row['key']], row['surfaceAppearance'])
                    if 'imageAppearance' in row:
                        scientific_image_appearance.apply(current[row['key']], row['imageAppearance'])
                    if row['key'] in prepared_labels:
                        prepared_labels[row['key']].apply()
                if 'grid' in settings:
                    scientific_grid.apply(self.viewer.grid, settings['grid'])
                self.viewer.camera.orientation = camera['orientation']
                self.viewer.camera.center = camera['center']
                self.viewer.camera.angles = camera['angles']
                self.viewer.camera.zoom = camera['zoom']
                self.viewer.camera.perspective = camera['perspective']
        elif operation == 'controls':
            apply = self.panel.prepare(value)
        else:
            raise Unsupported('This scientific interaction needs a declared integration')
        return apply

    def data_position(self, frame, layer_id, position):
        self._current(frame)
        layer = next((layer for identity, layer in self._layers if identity == layer_id), None)
        if layer is None or self.viewer.dims.ndisplay != 2 or self.viewer.grid.enabled:
            raise Unsupported('Picking needs a current layer in a single 2D view')
        y, x = vector(position, 2)
        width, height = self._size
        if not 0 <= x < width or not 0 <= y < height:
            raise ValueError('The pointer is outside the displayed scientific frame')
        vertical, horizontal = self.viewer.camera.orientation2d
        if vertical not in ('up', 'down') or horizontal not in ('left', 'right'):
            raise Unsupported('This camera orientation needs a qualified picking transform')
        world = self.viewer.canvas_to_world((height - y if vertical == 'up' else y,
                                            width - x if horizontal == 'left' else x))
        # The canvas uses the layer-list's common units; world_to_data only
        # inverts the layer affine, whose values use that layer's declared units.
        # Match napari's renderer, including its no-conversion fallback when the
        # layers have incompatible units. Leading viewer axes are not layer axes.
        if (units := self.viewer.layers.extent.units) is not None:
            import pint
            registry = pint.get_application_registry()
            world = [value * registry.get_base_units(source)[0] / registry.get_base_units(target)[0]
                     for value, source, target in zip(world[-layer.ndim:], units[-layer.ndim:], layer.units, strict=True)]
        return [float(n) for n in layer.world_to_data(world)]

    def describe_output(self, layer_id):
        from .scientific_outputs import describe
        self._check(); self._layer_rows()
        layer = next((layer for identity,layer in self._layers if identity == layer_id), None)
        if layer is None or self.editable.input_for(layer) is not None:
            raise ValueError('Choose a newly created native layer; use Save changes for an existing input')
        return self.view_bindings.describe_coordinates(layer, describe(layer))

    def describe_tool_layer(self, layer_id):
        from .scientific_outputs import describe
        self._check(); self._layer_rows()
        layer = next((layer for identity, layer in self._layers if identity == layer_id), None)
        if self.tool_document is None or layer is None or self.view_bindings.key_for(layer) is None:
            raise ValueError('A persistent tool needs declared stable bindings for every scientific layer')
        return self.view_bindings.describe_coordinates(layer, describe(layer))

    def stage_output(self, layer_id, expected, meaning, specification, exchange, *, snapshot='files'):
        from .native_edits import bind_output
        from .scientific_outputs import capture
        if self.describe_output(layer_id) != expected:
            raise ValueError('The new layer changed before capture; inspect it and save again')
        layer = next(layer for identity,layer in self._layers if identity == layer_id)
        self.view_bindings.check_meaning(layer, meaning)
        grant = bind_output(specification, exchange)
        from .scientific_output_coordinates import plain
        with capture(self.viewer, layer, plain(expected), meaning, grant, snapshot) as chunks:
            if 'coordinatesFrom' in expected and self.describe_output(layer_id) != expected:
                raise ValueError('The coordinate input changed during output capture')
            self._frame = None
            for index,values in chunks: grant.write(index,values)
        return {'ok':True,'staged':specification['token']}

    def stage_output_components(self, layer_id, expected, meaning, specifications, exchange, *, snapshot='files'):
        from .scientific_outputs import capture_components
        if self.describe_output(layer_id) != expected:
            raise ValueError('The surface changed before capture; inspect and save again')
        layer = next(layer for identity,layer in self._layers if identity == layer_id)
        self.view_bindings.check_meaning(layer, meaning)
        with capture_components(layer,expected,meaning,specifications,exchange,snapshot) as snapshots:
            self._frame = None
            for grant,chunks in snapshots:
                for index,values in chunks: grant.write(index,values)
        return {'ok':True,'staged':[row['edit']['token'] for row in specifications]}

    def stage_outputs(self, rows, exchange, *, snapshot='files', tool=False):
        from contextlib import ExitStack
        from .native_edits import bind_output
        from .scientific_outputs import capture, capture_components, capture_pyramid
        from .scientific_output_wire import COMPOUND, selections
        from .scientific_output_coordinates import plain
        if (type(rows) is not list or any(type(row) is not dict or
                set(row) != ({'layer','metadata','meaning','components','input'} if tool else
                             {'layer','metadata','meaning','components'}) for row in rows)):
            raise ValueError('A multi-output capture requires its exact selected layers')
        one_pyramid = len(rows)==1 and 'levels' in rows[0]['metadata']
        selections([{'layer':row['layer'],'meaning':row['meaning']} for row in rows], minimum=1 if tool or one_pyramid else 2)
        saved_tool = None
        if tool:
            from .scientific_tool_document import snapshot as tool_snapshot, scientific_identity
            self._check(); self._layer_rows()
            if self._files:
                raise ValueError('This tool needs a declared source-file persistence integration before it can be saved')
            if self.tool_document is None or [row['layer'] for row in rows] != [key for key, _ in self._layers]:
                raise ValueError('Save all declared tool layers together; no layer can be silently omitted')
            original_bindings = {key: self.view_bindings.key_for(layer) for key, layer in self._layers}
            original_science = scientific_identity(self.viewer)
            self._frame = None
            self._invalidate_native_panel()
            self.panel.invalidate()
            self.dirty = True
            # Capture once, before freezing arrays and before any external write.
            # No Qt event dispatch occurs between state and array snapshots.
            try:
                captured_document = self.tool_document.capture()
                captured_recipe = self.recipe_binding.capture() if getattr(self, 'recipe_binding', None) is not None else None
            except Exception as error:
                from .scientific_native_panel import CallbackFailed
                if isinstance(error, CallbackFailed):
                    raise ValueError('The tool state callback failed before capture. Private work remains; inspect it before trying again.') from error
                raise
            finally:
                self._frame = None
                self._invalidate_native_panel()
                self.panel.invalidate()
            self._layer_rows()
            if ([row['layer'] for row in rows] != [key for key, _ in self._layers]
                    or {key: self.view_bindings.key_for(layer) for key, layer in self._layers} != original_bindings):
                raise ValueError('The tool callback changed its layer set or bindings. Inspect private work before saving again.')
            if scientific_identity(self.viewer) != original_science:
                raise ValueError('The tool capture callback changed scientific values or layer metadata. Inspect private work before saving again.')
            saved_tool = tool_snapshot({'document': captured_document,
                'bindings': {original_bindings[row['layer']]: row['input'] for row in rows},
                **({'recipe': captured_recipe} if captured_recipe is not None else {})}, rows)
        with ExitStack() as frozen:
            snapshots=[]
            for row in rows:
                describe = self.describe_tool_layer if tool else self.describe_output
                if describe(row['layer']) != row['metadata']:
                    raise ValueError('A selected output changed before capture; inspect and save again')
                layer=next(layer for identity,layer in self._layers if identity==row['layer'])
                self.view_bindings.check_meaning(layer, row['meaning'])
                if tool:
                    original = self.editable.input_for(layer)
                    if original is not None:
                        source = self.editable.inputs[original]
                        if row['meaning'] != input_meaning(layer, source):
                            raise ValueError('Keep the bound input’s declared scientific meaning and axes; conversions require an explicit new layer')
                specs=row['components']
                if row['metadata']['type'] in COMPOUND:
                    snapshots.extend(frozen.enter_context(capture_components(layer,row['metadata'],row['meaning'],specs,exchange,snapshot)))
                elif 'levels' in row['metadata']:
                    snapshots.extend(frozen.enter_context(capture_pyramid(layer,plain(row['metadata']),row['meaning'],specs,exchange,snapshot)))
                else:
                    if (type(specs) is not list or len(specs)!=1 or type(specs[0]) is not dict
                            or set(specs[0]) != {'name','edit'} or specs[0]['name']!='output'):
                        raise ValueError('Each array output needs exactly one declared component grant')
                    grant=bind_output(specs[0]['edit'],exchange)
                    chunks=frozen.enter_context(capture(self.viewer,layer,plain(row['metadata']),row['meaning'],grant,snapshot))
                    snapshots.append((grant,chunks))
            for row in rows:
                describe = self.describe_tool_layer if tool else self.describe_output
                if 'coordinatesFrom' in row['metadata'] and describe(row['layer']) != row['metadata']:
                    raise ValueError('A coordinate input or selected output changed during capture')
            # Every layer, including compound geometry, is frozen before a parent
            # callback can mutate another layer or before the first byte is sent.
            self._frame=None
            for grant,chunks in snapshots:
                for index,values in chunks: grant.write(index,values)
        return {'ok':True,'staged':[row['edit']['token'] for item in rows for row in item['components']],
                **({'tool': saved_tool} if tool else {})}

    def stage_layer(self, frame, layer_id, input, specification, exchange, *, snapshot='files', base=None):
        self._current(frame)
        return self._stage_resource(layer_id, input, specification, exchange, snapshot=snapshot, frame=frame, base=base)

    def stage_resource(self, layer_id, input, specification, exchange, *, snapshot='files', base=None):
        self._check()
        self._layer_rows()  # Refresh real identities; removed/replaced layers cannot inherit a binding.
        return self._stage_resource(layer_id, input, specification, exchange, snapshot=snapshot, base=base)

    def stage_resources(self, rows, exchange, *, snapshot='files', base=None):
        from contextlib import ExitStack
        from .native_edits import bind
        self._check()
        self._layer_rows()
        if (type(rows) is not list or not 1 < len(rows) <= 64
                or any(type(row) is not dict or set(row) != {'layer', 'input', 'edit'} for row in rows)
                or len({row['layer'] for row in rows}) != len(rows)
                or len({row['input'] for row in rows}) != len(rows)):
            raise ValueError('Choose distinct editable layers for one scientific save')
        from .scientific_layer_exports import FreezeBudget
        budget = FreezeBudget()  # one memory allowance for the whole save, not one per layer
        with ExitStack() as frozen:
            snapshots = []
            for number, row in enumerate(rows):
                layer = next((layer for identity, layer in self._layers if identity == row['layer']), None)
                if layer is None:
                    raise ValueError('An editable layer changed; refresh layers before saving')
                grant = bind(row['edit'], exchange)
                chunks = frozen.enter_context(self.editable.staged_chunks(
                    layer, row['input'], grant, storage=snapshot, base=None if base is None else base[number],
                    budget=budget))
                snapshots.append((grant, chunks))
            # No Qt event dispatch, Python callback or transport occurs between
            # snapshots. All are frozen before the first externally supplied call.
            self._frame = None
            for grant, chunks in snapshots:
                for index, values in chunks:
                    grant.put(index, values)
        return {'ok': True, 'staged': [row['edit']['token'] for row in rows]}

    def _stage_resource(self, layer_id, input, specification, exchange, *, snapshot, frame=None, base=None):
        from .native_edits import bind
        layer = next((layer for identity, layer in self._layers if identity == layer_id), None)
        if layer is None:
            raise ValueError('Choose the current editable layer before saving')
        grant = bind(specification, exchange)
        with self.editable.staged_chunks(layer, input, grant, storage=snapshot, base=base) as chunks:
            if frame is not None:
                self._current(frame)
            self._frame = None
            for index, values in chunks:
                grant.put(index, values)
        return {'ok': True, 'staged': specification['token']}

    def python(self, frame, source, *, interrupt_token=None):
        self._current(frame)
        return self.python_resources(source, interrupt_token=interrupt_token)

    def family_drafts(self):
        """The tables, embeddings and artifacts Python or a plugin declared in this session, not yet saved."""
        self._check()
        return self.families.describe()

    def stage_families(self, names, exchange, *, unchanged=(), base=()):
        """Send the named drafts' exact parts to the parent, which checks and publishes them."""
        self._check()
        return self.families.stage(names, exchange, unchanged=unchanged, base=base)

    def python_assist(self, mode, expression):
        self._check()
        return self.console.assist(mode, expression)

    def python_resources(self, source, *, interrupt_token=None):
        """Run against this session's inputs; no pointer or rendered-frame authority."""
        self._check()
        scientific_console.source(source)
        self._frame = None
        self.dirty = True
        try:
            return self.console.run(source, interrupt_token=interrupt_token)
        finally:
            self.panel.invalidate()
            self._invalidate_native_panel()

    def close(self):
        if self._closed:
            return
        self._closed, self._frame = True, None
        errors = []
        for events in self._connections:
            try:
                events.disconnect(self._invalidate)
            except Exception as error:
                errors.append(error)
        self._connections.clear()
        for resource in (getattr(self, 'recipe_binding', None), getattr(self, 'tool_document', None), getattr(self, 'native_panel', None), self.view_bindings, self.editable, getattr(self, 'families', None), self.console, self.panel, self.container, self.viewer, self.backend, *self._files.values()):
            if resource is not None:
                try:
                    resource.close()
                except Exception as error:
                    errors.append(error)
        self._layers.clear()
        getattr(self, '_label_editors', {}).clear()
        getattr(self, '_retired_label_histories', []).clear()
        if errors:
            raise RuntimeError('A scientific host resource could not close') from errors[0]
