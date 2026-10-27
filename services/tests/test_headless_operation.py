"""One capability through two callers (hosted ticket 01): the local run_capability and the headless run_on_resource.

The boundary the founder approved: authorize an input, run the selected tool, retrieve the same saved result and its
method record. The capability is a real bundle member run in its real worker process (the stand-in is only its
installer, tests/test_env_runner.standin_uv); the expected pixels are computed here independently (255 - x).
"""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import envs, kinds, operations
from orchestrator.capability_runs import Failed, Refused, ResourceHost, kept_result, run_on_resource
from tests.test_bundle_runtime import contribute
from tests.test_env_runner import home, standin_uv  # noqa: F401

NOTHING = '''
[[members]]
id = "nothing"
name = "Nothing found"
adapter = "adapter:nothing"
inputs = [{ id = "image", type = "image.2d.rgb8" }]
outputs = [{ id = "table", type = "table.objects" }]
[[members.reference_cases]]
input = "example.png"
reference = "reference-nothing.json"
source = "Synthetic: finds nothing, a valid empty result"
device = "cpu"
tolerance = {atol = 0, rtol = 0, numeric_columns = ["mean"], identity_columns = ["region"]}
'''


@pytest.fixture
def installed(home, monkeypatch, tmp_path):
    before = dict(operations.OPERATIONS), set(kinds.registered())
    contribute(home)
    folder = home / 'repo/models/image-tools'
    (folder / 'model.toml').write_text((folder / 'model.toml').read_text() + NOTHING)
    (folder / 'adapter.py').write_text((folder / 'adapter.py').read_text() +
                                       "\ndef nothing(picture, params):\n    return {'columns': [{'name': 'region'}, {'name': 'mean'}], 'rows': []}\n")
    (folder / 'reference-nothing.json').write_text(json.dumps({'columns': [{'name': 'region'}, {'name': 'mean'}], 'rows': []}))
    catalog.manifests.cache_clear()
    owner = catalog.get('image-tools')
    assert owner is not None, catalog.problems()
    standin_uv(home, monkeypatch)
    generation, _ = envs.install(owner)
    yield SimpleNamespace(home=home, generation=generation.name)
    operations.OPERATIONS.clear()
    operations.OPERATIONS.update(before[0])
    kinds.registered().clear()
    kinds.registered().update(before[1])


def picture_file(path, depth=8):
    rows, cols = np.mgrid[0:24, 0:32]
    rgb = np.stack([rows * 5, cols * 7, (rows + cols) * 3], axis=-1).astype(np.uint8)
    data = cv2.imencode('.png', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR) if depth == 8 else
                        (rgb.astype(np.uint16) * 257))[1].tobytes()
    path.write_bytes(data)
    return rgb, {'id': 'input-1', 'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()}


def publish_with_still(rig, fid, staged):
    """rig.publish, with the frame's facts naming the still on stage (FrameInfo.source_image_json), so the local caller
    analyzes that still's original bytes (source_images.for_analysis), never the frame's preview."""
    import time

    from orchestrator.fov import FieldOfView
    from orchestrator.frames import Frame
    from orchestrator.hub import FrameInfo
    from tests.capability_rig import leaf

    now = time.time()
    frame = Frame(fid, leaf(), time.monotonic(), now, 9000 + fid, "matrix")
    info = FrameInfo(fid, 1280, 720, 0.0, 0.0, epoch=0, tracked=True, steady=True,
                     fov=FieldOfView(695.0, 360.0, 395.0, 0.99), wall_at=now, seq=frame.seq, boot=frame.boot,
                     source_image_json=staged)
    rig.hub.source.ring[fid] = frame
    rig.hub.source.last_frame_at = time.monotonic()
    rig.hub._publish(frame, info)


def headless(host, capability, source, params=None):
    return asyncio.run(run_on_resource(host, capability, source, params))


def decoded(data):
    return cv2.cvtColor(cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def test_both_callers_give_the_same_result_and_method_record(installed, tmp_path):
    from tests.capability_rig import Rig

    rgb, source = picture_file(tmp_path / 'input.png')
    expected = (255 - rgb.astype(int)).astype(np.uint8)  # independent of the adapter's code; offset 0 is its checked configuration

    rig = Rig(tmp_path / 'rig')
    rig.app.catalog_changed()
    staged = json.dumps({'asset': 'input-1', 'path': source['path'], 'session': rig.app.day.sid, 'optics': {}})
    rig.hub.stage_image_json = staged
    publish_with_still(rig, 7001, staged)  # as the hub's intake marks a frame while a still is on stage (hub.py)
    runs = asyncio.run(rig.turn([('run_capability', {'capability': 'image-tools#invert', 'params': {'offset': 0}})],
                                'Invert the picture on stage'))
    local_run = next(run for run in runs if run.tool == 'run_capability')
    assert local_run.ok, (local_run.summary, local_run.error)
    local_row = [row for row in rig.hub.resources.results.entries() if row.get('tool') == 'run_capability'][-1]
    local_entry, local_values, local_picture = kept_result(rig.hub, local_row['id'], local_row['session'])

    host = ResourceHost(tmp_path / 'host')
    got = headless(host, 'image-tools#invert', source, {'offset': 0})
    entry, values, picture = kept_result(host, got['result'], got['session'])

    np.testing.assert_array_equal(decoded(picture), expected)
    np.testing.assert_array_equal(decoded(local_picture), expected)
    for key in ('capability', 'output', 'params', 'scientific'):
        assert values[key] == local_values[key], key
    for key in ('implementation', 'version', 'generation', 'sha256', 'params', 'output'):
        assert values['producer'][key] == local_values['producer'][key], key
    assert values['producer']['generation'] == installed.generation
    assert entry['madeBy'][0]['implementation'] == local_entry['madeBy'][0]['implementation'] == 'image-tools#invert'
    assert entry['madeBy'][0]['policy'] == local_entry['madeBy'][0]['policy'] == 'raw'
    assert got['execution']['origin'] == 'headless' and got['execution']['executed'] == 'image-tools#invert'
    assert values['inputResources'] == [{'input': 'input-1', 'sha256': source['sha256']}]
    assert got['sha256'] == entry['digests'][0] == hashlib.sha256(picture).hexdigest()


def test_a_headless_run_has_no_camera_studio_screen_or_gpu_worker(installed, tmp_path):
    host = ResourceHost(tmp_path / 'host')
    for absent in ('latest_info', 'gpu', 'gpu_heavy', 'host_for', 'magnifier', 'clients', 'scene', 'settings', 'screen'):
        assert not hasattr(host, absent), absent
    _rgb, source = picture_file(tmp_path / 'input.png')
    got = headless(host, 'image-tools#mean', source)
    entry, values, picture = kept_result(host, got['result'], got['session'])
    assert picture is None and values['rows'] == [['whole', pytest.approx(float(_rgb.mean()))]]
    assert got['execution']['capture'] < 0, 'an imported picture, never a camera frame id'


def test_an_explicit_choice_fails_honestly_and_nothing_runs_in_its_place(installed, tmp_path):
    host = ResourceHost(tmp_path / 'host')
    _rgb, source = picture_file(tmp_path / 'input.png')
    with pytest.raises(Refused) as unknown:
        headless(host, 'image-tools#sharpen', source)
    assert unknown.value.code == 'unknown-capability'
    with pytest.raises(Refused) as out_of_range:
        headless(host, 'image-tools#invert', source, {'offset': 9})
    assert out_of_range.value.code == 'invalid-params' and 'offset' in str(out_of_range.value)
    assert not host.runner.executions and not host.resources.results.entries()


def test_a_valid_empty_output_is_kept_as_a_result_not_a_failure(installed, tmp_path):
    host = ResourceHost(tmp_path / 'host')
    _rgb, source = picture_file(tmp_path / 'input.png')
    got = headless(host, 'image-tools#nothing', source)
    assert got['empty'] is True
    _entry, values, _picture = kept_result(host, got['result'], got['session'])
    assert values['rows'] == [] and values['output'] == 'table.objects'


def test_an_unsupported_input_is_refused_before_anything_runs(installed, tmp_path):
    host = ResourceHost(tmp_path / 'host')
    _rgb, source = picture_file(tmp_path / 'deep.png', depth=16)
    with pytest.raises(Refused) as refused:
        headless(host, 'image-tools#invert', source)
    assert refused.value.code == 'unsupported-input'
    assert not host.runner.executions and not host.resources.results.entries()


def test_an_input_changed_after_it_was_authorized_never_runs(installed, tmp_path):
    host = ResourceHost(tmp_path / 'host')
    _rgb, source = picture_file(tmp_path / 'input.png')
    # Replaced after it was authorized: still a valid picture, but not the bytes whose sha256 was authorized.
    (tmp_path / 'input.png').write_bytes(cv2.imencode('.png', np.zeros((24, 32, 3), np.uint8))[1].tobytes())
    with pytest.raises(Refused) as refused:
        headless(host, 'image-tools#invert', source)
    assert refused.value.code == 'input-changed'
    assert not host.runner.executions and not host.resources.results.entries()


def test_an_output_that_is_not_what_it_declares_is_never_kept(installed, tmp_path, monkeypatch):
    host = ResourceHost(tmp_path / 'host')
    _rgb, source = picture_file(tmp_path / 'input.png')
    kinds.register()
    op = next(k['operation'] for k in kinds.available() if k['key'] == 'image-tools#invert')
    real = operations.ADAPTERS[(op, 'image-tools#invert')]

    async def tampered(run, capture, prompt, impl, params):
        out = await real(run, capture, prompt, impl, params)
        other = cv2.imencode('.png', np.zeros((24, 32, 3), np.uint8))[1].tobytes()
        return {**out, 'picture': other}  # not the pixels its producer hashed

    monkeypatch.setattr(kinds, 'register', lambda **_: None)  # keep the tampered adapter in place
    monkeypatch.setitem(operations.ADAPTERS, (op, 'image-tools#invert'), tampered)
    with pytest.raises(Failed, match='not the one its producer hashed'):
        headless(host, 'image-tools#invert', source)
    assert not host.resources.results.entries()


def test_an_own_process_implementation_needs_no_gpu_worker(monkeypatch):
    from orchestrator import readiness

    monkeypatch.setattr(readiness, 'implementation', lambda impl: '')
    gpu_model = operations.Impl('a-model', 'A model', 'model', ('field',), 'analysis')
    own = operations.Impl('a-function', 'A function', 'model', ('field',), 'analysis', own_process=True)
    nothing_here = SimpleNamespace()
    assert operations.availability(nothing_here, gpu_model) == 'no GPU worker here'
    assert operations.availability(nothing_here, own) == ''
    kinds.backends()
    assert kinds.BACKENDS['fiji'].own_process and kinds.BACKENDS['python-env'].own_process
    assert not kinds.BACKENDS['builtin-cpu'].own_process
