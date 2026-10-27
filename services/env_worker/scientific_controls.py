"""Worker-local adapter for pinned magicgui controls; never grants execution authority.

The scientific host must confine, admit and supervise callbacks on its GUI thread,
and bind every request to the owning Project, panel and accepted resource revision.
This adapter keeps Python choice objects inside that worker. It does not support
arbitrary Qt widgets, publish returned objects or accept scientific edits.
"""
from __future__ import annotations

import copy
import importlib.metadata
import inspect
import json
import math
import secrets
import threading

FORMAT = 'scientific-controls-v1'
MAX_CONTROLS = 128
MAX_CHOICES = 256
MAX_BYTES = 64 << 10
MAGICGUI_VERSION = '0.10.2'


class Unsupported(ValueError):
    pass


class Stale(ValueError):
    pass


def _text(value):
    if type(value) is not str or len(value) > 1000:
        raise Unsupported('Control text must be at most 1000 characters')
    return value


def _number(value):
    if type(value) not in (int, float) or abs(value) > 2**53 - 1 or not math.isfinite(value):
        raise Unsupported('Controls need finite, exactly representable numbers')
    return value


def _same(a, b):
    return a is b or (type(a) in (str, bool, int, float, type(None)) and type(a) is type(b) and a == b)


def _duplicate(a, b):
    return _same(a, b) or (type(a) in (bool, int, float) and type(b) in (bool, int, float) and a == b)


class Panel:
    def __init__(self, container):
        if threading.current_thread() is not threading.main_thread():
            raise Unsupported('Scientific controls must start on the worker’s GUI thread')
        from magicgui.widgets import Container
        if importlib.metadata.version('magicgui') != MAGICGUI_VERSION:
            raise Unsupported('Prepare the pinned magicgui control bridge')
        if not isinstance(container, Container):
            raise Unsupported('This control bridge requires a magicgui container')
        function = getattr(container, '_function', None)
        if inspect.iscoroutinefunction(function) or inspect.isasyncgenfunction(function) or inspect.isgeneratorfunction(function):
            raise Unsupported('Asynchronous controls need the host’s background-task integration')
        self.container = container
        self.thread = threading.get_ident()
        self.identity = secrets.token_hex(16)
        self.revision = 0
        self._widgets = []
        self._fingerprint = None
        self._closed = False
        self._dispatching = False

    def _check(self):
        if threading.get_ident() != self.thread:
            raise Unsupported('Scientific controls must run on their owning GUI thread')
        if self._closed:
            raise Stale('This panel is closed; reopen its saved work')

    def invalidate(self):
        """Host resource, selection or view changes revoke prior control requests."""
        self._check()
        self.revision += 1
        self._fingerprint = None

    def close(self):
        self._check()
        self._widgets.clear()
        self._closed = True

    def snapshot(self):
        self._check()
        from magicgui import widgets
        children = list(self.container)
        if len(children) > MAX_CONTROLS:
            raise Unsupported('This panel exceeds the control limit')
        current, rows = [], []
        for child in children:
            old = next((item for item in self._widgets if item['widget'] is child), None)
            entry = old or {'widget': child, 'id': secrets.token_hex(16), 'choices': []}
            row = {'id': entry['id'], 'label': _text(child.label), 'help': _text(child.tooltip or ''),
                   'enabled': bool(self.container.enabled and child.enabled),
                   'visible': not (self.container._explicitly_hidden or child._explicitly_hidden)}
            kind = type(child)
            if kind is widgets.ComboBox:
                values = child._widget._mgui_get_choices()
                if len(values) > MAX_CHOICES:
                    raise Unsupported('This control exceeds the choice limit')
                choices = []
                for label, value in values:
                    if any(_duplicate(item['value'], value) for item in choices):
                        raise Unsupported('Duplicate choice values need an explicit integration')
                    previous = next((item for item in entry['choices'] if _same(item['value'], value)), None)
                    choices.append({'id': previous['id'] if previous else secrets.token_hex(16),
                                    'label': _text(label), 'value': value})
                if len({item['id'] for item in choices}) != len(choices):
                    raise Unsupported('Duplicate choice values need an explicit integration')
                selected = next((item['id'] for item in choices if _same(item['value'], child.value)), None)
                entry['choices'] = choices
                row.update(kind='choice', choices=[{'id': item['id'], 'label': item['label']} for item in choices], value=selected)
            elif kind in (widgets.SpinBox, widgets.Slider, widgets.FloatSpinBox, widgets.FloatSlider):
                row.update(kind='integer' if kind in (widgets.SpinBox, widgets.Slider) else 'number',
                           value=_number(child.value), min=_number(child.min), max=_number(child.max),
                           step=None if child.step is None else _number(child.step))
            elif kind is widgets.CheckBox:
                if type(child.value) is not bool:
                    raise Unsupported('Checkbox values must be boolean')
                row.update(kind='boolean', value=child.value)
            elif kind in (widgets.LineEdit, widgets.TextEdit):
                row.update(kind='text', value=_text(child.value), multiline=kind is widgets.TextEdit)
                if kind is widgets.TextEdit and child.read_only:
                    row['enabled'] = False
            elif kind is widgets.Label:
                row.update(kind='label', value=_text(child.value))
            elif kind is widgets.PushButton:
                row.update(kind='button', label=_text(child.text))
            else:
                raise Unsupported(f'{kind.__name__} needs a declared custom-panel integration')
            current.append(entry)
            rows.append(row)
        body = json.dumps(rows, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()
        if len(body) > MAX_BYTES - 256:
            raise Unsupported('This panel exceeds its serialized allowance')
        if body != self._fingerprint:
            self.revision += 1
            self._fingerprint = body
        self._widgets = current
        return {'format': FORMAT, 'panel': self.identity, 'revision': self.revision, 'controls': copy.deepcopy(rows)}

    def dispatch(self, request):
        return self.prepare(request)()

    def prepare(self, request):
        """Validate before admission; apply once immediately on this GUI thread.

        The host distinguishes these refusals from exceptions after a widget
        assignment or callback starts. The returned action must not be queued.
        """
        self._check()
        if self._dispatching:
            raise Stale('Finish the current control callback before starting another')
        state = self.snapshot()
        if (type(request) is not dict or set(request) not in ({'panel', 'revision', 'control', 'op'},
                                                           {'panel', 'revision', 'control', 'op', 'value'})
                or type(request.get('revision')) is not int or request['panel'] != self.identity
                or request['revision'] != self.revision):
            raise Stale('Settings changed; review the current panel before trying again')
        row = next((item for item in state['controls'] if item['id'] == request['control']), None)
        if row is None or not row['enabled'] or not row['visible']:
            raise Stale('This control is no longer available')
        widget = next(item for item in self._widgets if item['id'] == row['id'])
        action = row['kind'] == 'button'
        if request['op'] != ('invoke' if action else 'set') or ('value' in request) == action or row['kind'] == 'label':
            raise Unsupported('This control does not accept that action')
        value = request.get('value')
        if not action:
            if row['kind'] == 'choice':
                selected = next((item for item in widget['choices'] if type(value) is str and item['id'] == value), None)
                if selected is None:
                    raise Stale('That choice is no longer available')
                value = selected['value']
            elif row['kind'] in ('number', 'integer'):
                _number(value)
                if (row['kind'] == 'integer' and type(value) is not int) or not row['min'] <= value <= row['max']:
                    raise Unsupported('The value is outside this control’s range')
            elif row['kind'] == 'boolean' and type(value) is not bool:
                raise Unsupported('This control needs a boolean')
            elif row['kind'] == 'text':
                _text(value)
        revision, used = self.revision, False
        def apply():
            nonlocal used
            self._check()
            if used or self._dispatching or self.revision != revision:
                raise Stale('Prepare the current control before applying it once')
            used = True
            self._dispatching = True
            try:
                if action:
                    widget['widget'].changed.emit(True)
                else:
                    widget['widget'].value = value
            finally:
                self._dispatching = False
                self.invalidate()
            return self.snapshot()
        return apply
