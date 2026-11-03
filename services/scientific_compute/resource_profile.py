"""Explicit trusted worker limits; scientific parameters cannot raise allocations."""
from contextvars import ContextVar

DEFAULT = {'format': 1, 'memory_bytes': 24 << 30, 'materialization_bytes': 8 << 30,
           'cpu_seconds': 3600, 'file_bytes': 64 << 30}
_profile = ContextVar('scientific_worker_resources', default=DEFAULT)


def validate(value):
    if type(value) is not dict or set(value) != set(DEFAULT) or value.get('format') != 1:
        raise ValueError('Choose an explicit version-1 scientific worker resource profile')
    bounds = {'memory_bytes': (512 << 20, 128 << 30), 'materialization_bytes': (1 << 20, 96 << 30),
              'cpu_seconds': (60, 43200), 'file_bytes': (1 << 20, 64 << 30)}
    for key, (low, high) in bounds.items():
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError('Scientific worker resources exceed the supported bounds')
    if value['materialization_bytes'] > value['memory_bytes'] * 7 // 8:
        raise ValueError('Reserve worker memory beyond estimated evaluator materialization')
    return dict(value)


def configure(value):
    return _profile.set(validate(value))


def current():
    return dict(_profile.get())


def reset(token):
    _profile.reset(token)
