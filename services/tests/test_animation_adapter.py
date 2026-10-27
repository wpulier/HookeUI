"""Private integration codec regressions. Real upstream tests are separate."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest


def module():
    path = Path(__file__).resolve().parents[2] / 'examples/scientific-animation/adapter.py'
    spec = importlib.util.spec_from_file_location('animation_adapter', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize('dtype', ['>u2', '<f8', '|u1', '<U6', '|S6', '?'])
def test_typed_values_tuples_and_integer_mapping_keys_survive_json(dtype):
    import json
    adapter = module()
    original = np.array([[1, 2], [3, 4]], dtype=dtype)
    source = {'contrast': (1, 9), 'colors': {0: original}, 'visible': True}
    encoded = adapter.encode(source)
    returned = adapter.decode(json.loads(json.dumps(encoded)))
    assert returned['contrast'] == (1, 9)
    assert returned['colors'][0].dtype == original.dtype
    assert np.array_equal(returned['colors'][0], original)
    assert adapter.encode(returned) == encoded
    returned['colors'][0].flat[0] = 0
    assert np.array_equal(source['colors'][0], original)


@pytest.mark.parametrize('value', [
    {'kind': '__import__', 'value': 'os'},
    {'kind': 'array', 'dtype': '|O', 'shape': [1], 'bytes': 'AAAAAAAAAAA='},
    {'kind': 'array', 'dtype': '<u2', 'shape': [1], 'bytes': ''},
    {'kind': 'array', 'dtype': '<u2', 'shape': [True], 'bytes': 'AAA='},
    {'kind': 'array', 'dtype': '<u2', 'shape': [1000000000], 'bytes': 'AAA='},
    {'kind': 'array', 'dtype': '<u2', 'shape': [1], 'bytes': 'AAA=', 'ignored': 1},
    {'kind': 'mapping', 'items': [['a', 1], ['a', 2]]},
    {'kind': 'list', 'items': [1] * 4097},
    {'kind': 'unit', 'value': ['micrometer']},
])
def test_malformed_or_executable_document_settings_are_refused(value):
    with pytest.raises((TypeError, ValueError)):
        module().decode(value)


@pytest.mark.parametrize('value', [np.array([object()]), np.zeros(32769, dtype='u1'), float('inf')])
def test_live_objects_and_unbounded_settings_cannot_be_saved(value):
    with pytest.raises(ValueError):
        module().encode(value)


def test_recursive_settings_are_refused_in_both_directions():
    cyclic = []; cyclic.append(cyclic)
    with pytest.raises(ValueError): module().encode(cyclic)
    encoded = {'kind': 'list', 'items': []}; encoded['items'].append(encoded)
    with pytest.raises(ValueError): module().decode(encoded)


@pytest.mark.parametrize('columns', [[1], [np.zeros(20000, dtype='u1')]])
def test_feature_table_cannot_broadcast_scalars_or_exceed_aggregate_allowance(columns, monkeypatch):
    import sys
    from types import SimpleNamespace
    adapter = module()
    monkeypatch.setitem(sys.modules, 'pandas', SimpleNamespace(DataFrame=lambda *a, **k: pytest.fail('Invalid table allocated')))
    state = {'kind': 'features', 'columns': adapter.encode(('one',)),
             'index': adapter.encode(np.zeros(20000, dtype='u1')),
             'values': [adapter.encode(value) for value in columns]}
    with pytest.raises(ValueError, match='bounded arrays'):
        adapter.decode(state)


def test_large_sources_are_refused_before_allocation_or_read():
    from types import SimpleNamespace
    adapter = module()
    source = SimpleNamespace(shape=(6000, 4000), dtype='<u2', axes=('y', 'x'),
                             descriptor={'semantic': 'intensity'})
    adapter.admit_sources({'first': source})
    with pytest.raises(ValueError, match='64 MiB'):
        adapter.admit_sources({'first': source, 'second': source})
    source.descriptor = {'semantic': 'intensity', 'calibration': {'axes': ['x', 'y']}}
    with pytest.raises(ValueError, match='ordered input axes'):
        adapter.admit_sources({'first': source})
