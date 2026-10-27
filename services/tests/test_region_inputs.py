"""Host refuses invalid read grants before slicing or allocating output pixels."""
import numpy as np
import pytest

from orchestrator.region_inputs import RegionInputs


def source():
    picture = np.arange(8 * 9 * 3, dtype=np.uint8).reshape(8, 9, 3)
    return RegionInputs(picture, {'image': {'semantic': 'intensity', 'shape': [8, 9, 3]}})


def test_selected_region_is_exact_and_next_invocation_has_a_new_grant():
    one, other = source(), source()
    assert one.wire()['image']['token'] != other.wire()['image']['token']
    request = {'op': 'read-region', 'token': one.token, 'region': [[1, 3], [2, 5], [0, 3]]}
    header, data = one.answer(request)
    assert header == {'ok': True, 'kind': 'region', 'shape': [2, 3, 3], 'dtype': 'uint8'}
    assert bytes(data) == one.picture[1:3, 2:5, :].tobytes()
    assert one.read_bytes == 18 and one.requests == 1
    with pytest.raises(ValueError, match='no grant'):
        other.answer(request)
    wire = one.wire()
    wire['image']['scientific']['shape'][0] = 99
    assert one.wire()['image']['scientific']['shape'][0] == 8


@pytest.mark.parametrize('change', [
    {'token': 'forged'}, {'path': '/unrelated/file'}, {'region': [[0, 9], [0, 9], [0, 3]]},
    {'region': [[0, 1], [0, 1], [True, 3]]}, {'region': [[0, 1], [0, 1]]},
    {'region': [[0, 0], [0, 1], [0, 3]]},
])
def test_invalid_request_cannot_consume_or_redirect_grant(change):
    one = source()
    request = {'op': 'read-region', 'token': one.token, 'region': [[0, 1], [0, 1], [0, 3]]}
    with pytest.raises(ValueError):
        one.answer({**request, **change})
    assert one.requests == one.read_bytes == 0
    assert bytes(one.answer(request)[1]) == bytes([0, 1, 2])


def test_payload_and_cumulative_limits_are_independent_of_worker_sdk(monkeypatch):
    from orchestrator import region_inputs
    one = source()
    request = {'op': 'read-region', 'token': one.token, 'region': [[0, 1], [0, 1], [0, 3]]}
    with pytest.raises(ValueError, match='payload'):
        one.check_frame(request, 1)
    with pytest.raises(ValueError, match='object header'):
        one.check_frame([], 0)
    monkeypatch.setattr(region_inputs, 'MAX_READ_BYTES', 5)
    one.answer(request)
    with pytest.raises(ValueError, match='allowance'):
        one.answer(request)
    another = source()
    monkeypatch.setattr(region_inputs, 'MAX_READ_REQUESTS', 1)
    another.answer(request | {'token': another.token})
    with pytest.raises(ValueError, match='allowance'):
        another.answer(request | {'token': another.token})
