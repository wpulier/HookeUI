"""Bounded, non-evaluating assistance for an owned Python namespace.

Only names and dotted attributes are inspected. Descriptors are documented but
never executed; subscripts/calls remain explicit Python cells.
"""
import builtins
import keyword
from types import FunctionType, BuiltinFunctionType, GetSetDescriptorType, MemberDescriptorType, ModuleType, MappingProxyType

MODES = ('complete', 'help')
MAX_NAMES = 64
MAX_SCAN = 4096
MAX_TEXT = 8192
_MISSING = object()


def _name(value):
    return type(value) is str and len(value) <= 256 and value.isidentifier() and len(value.encode('utf-8')) <= 256


def query(mode, expression):
    if (type(mode) is not str or mode not in MODES or type(expression) is not str
            or len(expression) > 256 or len(expression.encode('utf-8')) > 256):
        raise ValueError('Python assistance needs a name of at most 256 bytes')
    parts = expression.split('.')
    if len(parts) > 16 or any(not part.isidentifier() for part in parts[:-1]):
        raise ValueError('Use a Python name or dotted attribute; calls and indexing require Run code')
    if parts[-1] and not parts[-1].isidentifier() or (mode == 'help' and not parts[-1]):
        raise ValueError('Choose a Python name for help or completion')
    return parts


def _mro(cls):
    classes = type.__dict__['__mro__'].__get__(cls)
    if len(classes) > 128:
        raise LookupError('This type has too many bases for bounded lookup. Inspect it explicitly with Run code.')
    return classes


def _class_dict(cls):
    return type.__dict__['__dict__'].__get__(cls)


def _lookup(space, name):
    # dict.get/getattr_static can call an arbitrary key's __eq__ on hash
    # collisions. Inspect exact strings without invoking dictionary lookup.
    for count, (key, value) in enumerate(space.items()):
        if count >= MAX_SCAN:
            raise LookupError('This namespace exceeds the lookup allowance. Inspect it explicitly with Run code.')
        if type(key) is str and key == name:
            return value
    return _MISSING


def _instance_dict(value):
    if type(value) is ModuleType:
        return ModuleType.__dict__['__dict__'].__get__(value)
    if not issubclass(type(value), type):
        # A Python __dict__ property is not a namespace. Invoke only the native
        # instance-dictionary descriptor, never __getattribute__ or __dir__.
        for cls in _mro(type(value)):
            descriptor = _lookup(_class_dict(cls), '__dict__')
            if descriptor is not _MISSING:
                if (type(descriptor) in (GetSetDescriptorType, MemberDescriptorType)
                        and descriptor.__name__ == '__dict__' and descriptor.__objclass__ is cls):
                    values = descriptor.__get__(value, type(value))
                    if type(values) is dict:
                        return values
                break
    return {}


def _namespaces(value):
    if type(value) is property:
        raise LookupError('This path is a property. Inspect it explicitly with Run code.')
    yield _instance_dict(value)
    classes = _mro(value) if issubclass(type(value), type) else _mro(type(value))
    for cls in classes:
        yield _class_dict(cls)


def _class_attribute(cls, name):
    for base in _mro(cls):
        value = _lookup(_class_dict(base), name)
        if value is not _MISSING:
            return value
    return _MISSING


def _attribute(value, name):
    if issubclass(type(value), type):
        return _class_attribute(value, name)
    declared = _class_attribute(type(value), name)
    if declared is not _MISSING and any(_class_attribute(type(declared), slot) is not _MISSING for slot in ('__set__', '__delete__')):
        return declared  # Data descriptors win, but are never evaluated.
    own = _lookup(_instance_dict(value), name)
    return declared if own is _MISSING else own


def _resolve(parts, namespace):
    value = _lookup(namespace, parts[0])
    if value is _MISSING:
        value = _lookup(vars(builtins), parts[0])
    if value is _MISSING:
        raise LookupError('That name is not defined in this Python session.')
    for part in parts[1:]:
        if type(value) is property:
            raise LookupError('This path contains a property. Inspect it explicitly with Run code.')
        value = _attribute(value, part)
        if value is _MISSING:
            raise LookupError('That attribute is not available without executing Python.')
    return value


def assist(namespace, mode, expression):
    parts = query(mode, expression)
    result = {'format': 'scientific-python-assist-v1', 'mode': mode, 'expression': expression,
              'matches': [], 'text': '', 'truncated': False}
    try:
        if mode == 'help':
            value = _resolve(parts, namespace)
            doc = _attribute(value, '__doc__')
            if type(value) in (FunctionType, BuiltinFunctionType, property, staticmethod, classmethod):
                doc = value.__doc__
            if type(doc) is not str:
                doc = 'No static documentation is available. Run code explicitly to inspect its value.'
            prefix = 'Property; its value was not evaluated.\n\n' if type(value) is property else ''
            encoded = (prefix + doc[:MAX_TEXT]).encode('utf-8', errors='replace')
            result['text'] = encoded[:MAX_TEXT].decode('utf-8', errors='ignore')
            result['truncated'] = len(doc) > MAX_TEXT or len(encoded) > MAX_TEXT
        else:
            prefix = parts[-1]
            spaces = ([namespace, vars(builtins), dict.fromkeys(keyword.kwlist)] if len(parts) == 1
                      else _namespaces(_resolve(parts[:-1], namespace)))
            names, scanned = set(), 0
            for space in spaces:
                if type(space) not in (dict, MappingProxyType):
                    continue
                for name in space:
                    scanned += 1
                    if scanned > MAX_SCAN:
                        result['truncated'] = True
                        break
                    if type(name) is str and len(name) > 256:
                        result['truncated'] = True
                        continue
                    if _name(name) and name.startswith(prefix) and (prefix.startswith('_') or not name.startswith('_')):
                        names.add(name)
                if scanned > MAX_SCAN:
                    break
            result['matches'] = sorted(names)[:MAX_NAMES]
            result['truncated'] |= len(names) > MAX_NAMES
            result['text'] = 'Choose a name to insert it. No code was run.' if names else 'No matching static names. Dynamic values require explicit Run code.'
    except LookupError as error:
        result['text'] = str(error)
    except (TypeError, AttributeError, RuntimeError):
        # A plugin can change its namespace concurrently or provide an unusual
        # static descriptor. Lookup failure must not terminate its private work.
        result['matches'] = []
        result['text'] = 'The namespace changed or cannot be inspected statically. Retry the lookup; your private work is kept.'
    return result


def reply(value, mode, expression):
    query(mode, expression)
    if (type(value) is not dict or set(value) != {'format','mode','expression','matches','text','truncated'}
            or value['format'] != 'scientific-python-assist-v1' or value['mode'] != mode or value['expression'] != expression
            or type(value['truncated']) is not bool or type(value['text']) is not str
            or len(value['text']) > MAX_TEXT or len(value['text'].encode('utf-8')) > MAX_TEXT or type(value['matches']) is not list
            or len(value['matches']) > MAX_NAMES
            or any(not _name(name) for name in value['matches'])
            or (mode == 'help' and value['matches'])):
        raise ValueError('Python assistance returned an invalid bounded response')
    return value
