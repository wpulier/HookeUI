"""Integration-declared identities for a view's layers, separate from edit grants."""
from __future__ import annotations

import re

from .scientific_output_wire import declaration, SEMANTICS


class ViewBindings:
    def __init__(self, viewer):
        self.viewer = viewer
        self._layers = {}
        self._meanings = {}
        self._coordinates = {}
        self._coordinate_sources = {}
        self._sealed = False
        self._closed = False

    def bind(self, *, layer, key):
        if self._sealed or self._closed:
            raise ValueError('Bind initial layers during integration creation')
        self._bind(layer, key)

    def bind_output(self, *, layer, key, meaning=None, coordinates_from=None, editable=None):
        if not self._sealed or self._closed:
            raise ValueError('Bind new outputs only in an open initialized session')
        checked = self._meaning(layer, meaning) if meaning is not None else None
        coordinates = None
        if coordinates_from is not None:
            from .scientific_output_coordinates import Binding
            coordinates = Binding(editable, coordinates_from, layer, meaning, self._coordinate_sources)
        self.refresh()
        self._bind(layer, key)
        if checked is not None:
            self._meanings[key] = checked
        if coordinates is not None:
            self._coordinates[key] = coordinates

    def _bind(self, layer, key):
        if (type(key) is not str or re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', key) is None
                or len(self._layers) >= 64 or key in self._layers
                or any(bound is layer for bound in self._layers.values())
                or not any(current is layer for current in self.viewer.layers)):
            raise ValueError('Bind each current view layer once with a unique lifetime key; at most 64 identities')
        self._layers[key] = layer

    def seal(self):
        self.refresh()
        self._sealed = True

    def refresh(self):
        for key, layer in self._layers.items():
            if layer is not None and not any(current is layer for current in self.viewer.layers):
                self._layers[key] = None
                self._meanings.pop(key, None)
                self._coordinates.pop(key, None)

    def key_for(self, layer):
        self.refresh()
        return next((key for key, bound in self._layers.items() if bound is layer), None)

    @staticmethod
    def _meaning(layer, value):
        meaning = declaration(value)
        if (SEMANTICS[meaning['semantic']] != layer._type_string
                or len(meaning['axes']) != layer.ndim):
            raise ValueError('Declared output meaning or dimensions no longer match this layer')
        if getattr(layer, 'rgb', False) and (meaning['semantic'] != 'intensity' or 'color' in meaning['axes']):
            raise ValueError('RGB outputs require intensity and spatial axes distinct from color')
        if layer._type_string == 'tracks' and meaning['axes'][0] != 't':
            raise ValueError('Track output axes must begin with time t')
        return meaning

    def meaning_for(self, layer):
        value = self._meanings.get(self.key_for(layer))
        return self._meaning(layer, value) if value is not None else None

    def check_meaning(self, layer, meaning):
        declared = self.meaning_for(layer)
        if declared is not None and declared != meaning:
            raise ValueError('Keep the integration-declared output meaning and axes; conversions require a new layer')

    def describe_coordinates(self, layer, info):
        binding = self._coordinates.get(self.key_for(layer))
        return {**info, **({'coordinatesFrom':binding.check()} if binding is not None else {})}

    def close(self):
        self._closed = True
        self._sealed = True
        self._layers.clear()
        self._meanings.clear()
        self._coordinates.clear()
        self._coordinate_sources.clear()
