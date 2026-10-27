"""Unit-name identity is stricter than convertible quantities; no data conversion."""
import copy

import pytest

from env_worker.scientific_calibration import same_calibration


def calibration(units):
    return {'space': {'session': 'session-a', 'id': 'project-a', 'revision': 1},
            'axes': ['z', 'x'], 'units': units, 'affine': [[2, 0, 4], [0, 3, 9]]}


@pytest.mark.parametrize('left,right,equal', [
    ('um', 'micrometer', True), ('µm', 'micrometers', True),
    ('μm', 'micrometer', True), ('ms', 'millisecond', True),
    ('um/s', 'micrometer / second', True),
    ('um', 'mm', False), ('s', 'ms', False), ('degC', 'kelvin', False),
    ('degree', 'radian', False), ('percent', 'dimensionless', False),
    ('micrometer', '1e-6 meter', False),
    ('custom-detector-channel', 'custom-detector-channel', True),
    ('custom-detector-channel', 'custom-other-channel', False),
    ('this is not a unit', 'micrometer', False),
    ('meter**1.0000001', 'meter**1.0000002', False),
    ('um / s ** 2', 'micrometer / second ** 2', True),
])
def test_identity_preserves_units_without_converting(left, right, equal):
    a, b = calibration([left, 'um']), calibration([right, 'µm'])
    before = copy.deepcopy((a, b))
    assert same_calibration(a, b) is equal
    assert same_calibration(b, a) is equal
    assert (a, b) == before


@pytest.mark.parametrize('field', ['affine', 'axes', 'space', 'extra', 'units_order'])
def test_aliases_cannot_hide_other_coordinate_changes(field):
    a = calibration(['um', 'ms'])
    b = calibration(['micrometer', 'millisecond'])
    if field == 'affine': b['affine'][0][0] += 1
    elif field == 'axes': b['axes'].reverse()
    elif field == 'space': b['space']['revision'] += 1
    elif field == 'extra': b['extra'] = 'unrecognised'
    else: b['units'].reverse()
    assert not same_calibration(a, b)


@pytest.mark.parametrize('expression', [
    'meter ** (9 ** 9 ** 9)', 'meter ** 9 ** 9',
    'meter ** 1000000000', 'meter ** 1.0000001', '1e999999 * meter',
    'meter / (second * second)', 'meter*' * 8 + 'second',
    'meter⁹⁹⁹⁹', 'meter9999999999999999999',
])
def test_unsupported_arithmetic_never_reaches_unit_parser(monkeypatch, expression):
    import pint
    from env_worker.scientific_calibration import _unit_identity
    _unit_identity.cache_clear()
    def forbidden(*args, **kwargs):
        raise AssertionError('Unbounded expression reached Pint')
    monkeypatch.setattr(pint, 'UnitRegistry', forbidden)
    assert not same_calibration(calibration([expression, 'um']), calibration(['meter', 'um']))


def test_copied_worker_bootstrap_supports_calibration_identity(tmp_path):
    import os
    import subprocess
    import sys
    from orchestrator.scientific_host import _bootstrap
    _bootstrap(tmp_path)
    code = ('from env_worker.scientific_output_wire import same_calibration; '
            "assert same_calibration({'units': ['um']}, {'units': ['micrometer']})")
    environment = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=environment,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
