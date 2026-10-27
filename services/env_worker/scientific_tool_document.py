"""Data-only plugin state captured inside its owning scientific worker.

This component grants no persistence or filesystem authority. The parent must
capture the corresponding scientific layers and accept both in one document.
Restore is only for a newly opened, explicitly admitted private session.
"""
import json
import math
import re
import threading

from .scientific_native_panel import callback_errors

FORMAT = 'scientific-tool-document-v1'
MAX_BYTES = 64 << 10
SCHEMA = re.compile(r'[A-Za-z][A-Za-z0-9._/-]{0,127}')
KEY = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}')


class InvalidDocument(ValueError):
    pass


def document(value):
    """Validate before copying or serializing potentially contributed objects."""
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or set(value) != {'format', 'schema', 'version', 'state'}
            or type(value['format']) is not str or value['format'] != FORMAT or type(value['schema']) is not str
            or SCHEMA.fullmatch(value['schema']) is None
            or type(value['version']) is not int or not 1 <= value['version'] <= 2**31-1
            or type(value['state']) is not dict):
        raise InvalidDocument('Tool state needs its declared schema, version and data-only object')
    pending = [(value, 0)]
    nodes = characters = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 24 or nodes > 16_384:
            raise InvalidDocument('Tool state exceeds its nesting or value allowance')
        if type(item) in (dict, list) and nodes + len(pending) + len(item) > 16_384:
            raise InvalidDocument('Tool state has too many values')
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise InvalidDocument('Tool state object keys must be text')
            characters += sum(len(key) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            characters += len(item)
        elif type(item) is float:
            if not math.isfinite(item):
                raise InvalidDocument('Tool state numbers must be finite')
        elif type(item) is int:
            if abs(item) > 2**53-1:
                raise InvalidDocument('Tool state integers must retain their exact JSON value')
        elif item is not None and type(item) is not bool:
            raise InvalidDocument('Tool state cannot contain arrays, Python objects or executable state')
        if characters > MAX_BYTES:
            raise InvalidDocument('Tool state exceeds its text allowance')
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         sort_keys=True, separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise InvalidDocument('Tool state must contain finite UTF-8 JSON values') from error
    if len(raw) > MAX_BYTES:
        raise InvalidDocument('Tool state exceeds its encoded byte allowance')
    return json.loads(raw)


def snapshot(value, layers):
    """A tool document binds each named view layer to its exact retained input."""
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or set(value) not in ({'document', 'bindings'}, {'document', 'bindings', 'recipe'}) or type(value['bindings']) is not dict):
        raise InvalidDocument('Tool restoration needs its data-only state and exact layer bindings')
    bindings = value['bindings']
    if (not 1 <= len(bindings) <= 64
            or any(type(key) is not str or KEY.fullmatch(key) is None
                   or type(name) is not str or KEY.fullmatch(name) is None for key, name in bindings.items())
            or len(set(bindings.values())) != len(bindings)
            or set(bindings.values()) != {row['input'] for row in layers}):
        raise InvalidDocument('Every saved tool layer needs one distinct stable binding')
    return {'document': document(value['document']), 'bindings': dict(bindings),
            **({'recipe': document(value['recipe'])} if 'recipe' in value else {})}


def scientific_identity(viewer):
    """Bounded exact-value guard around contributed restore callbacks."""
    import hashlib
    import numpy as np
    from .scientific_outputs import describe
    from .scientific_layer_exports import quiescent
    from .scientific_output_wire import COMPOUND, component_contract
    result = []
    total = 0
    for layer in viewer.layers:
        info = describe(layer)
        arrays = (component_contract(layer._type_string).components(layer)
                  if layer._type_string in COMPOUND else {'output': layer.data})
        hashes = {}
        for key, data in arrays.items():
            if type(data) is not np.ndarray:
                raise InvalidDocument('Tool restoration requires retained native scientific values')
            total += data.nbytes
            if total > 64 << 20:
                raise InvalidDocument('Tool state checking exceeds the current native snapshot allowance')
            quiescent(data)
            digest = hashlib.sha256()
            size = max(1, (1 << 20) // data.itemsize)
            for start in range(0, data.size, size):
                digest.update(data.flat[start:start+size].tobytes())
            hashes[key] = digest.hexdigest()
        result.append((id(layer), info, hashes))
    return result


class Binding:
    """One integration's explicit state callbacks; no automatic introspection."""
    def __init__(self, *, schema, version, capture, restore):
        self.template = document({'format': FORMAT, 'schema': schema, 'version': version, 'state': {}})
        if not callable(capture) or not callable(restore):
            raise InvalidDocument('Declare both capture and restore for a persistent tool')
        self._capture, self._restore = capture, restore
        self._thread = threading.get_ident()
        self._closed = False
        self._restored = False

    def _check(self):
        if self._closed or self._thread != threading.get_ident():
            raise InvalidDocument('Tool state belongs to its open scientific session and GUI thread')

    def capture(self):
        self._check()
        with callback_errors():
            state = self._capture()
        return document({**self.template, 'state': state})

    def restore(self, saved):
        self._check()
        if self._restored:
            raise InvalidDocument('Tool state may be restored only once into a new private session')
        saved = document(saved)
        if any(saved[key] != self.template[key] for key in ('format', 'schema', 'version')):
            raise InvalidDocument('This tool cannot restore that schema version; preserve the original document')
        # A failure cannot be retried against partially restored mutable state.
        # The host must discard this new worker and preserve the accepted source.
        self._restored = True
        with callback_errors():
            self._restore(document(saved)['state'])
        if json.dumps(self.capture(), sort_keys=True) != json.dumps(saved, sort_keys=True):
            raise InvalidDocument('Restored tool state differs from the saved document; no work was accepted')

    def close(self):
        self._closed = True
        self._capture = self._restore = None
