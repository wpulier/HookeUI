"""Generated fixtures through installed adapters, operations and evidence.

The installer is a stand-in; adapter code runs in its actual separate Python worker.
"""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from gpu_worker import manifests as catalog
from gpu_worker import model_files
from orchestrator import env_runner, operations, options, readiness
from orchestrator.evidence import Evidence
from orchestrator.population import members_from
from tests.test_env_runner import home, contribute, install

LABELS = '''
import numpy as np
def segment(picture, params):
    out = np.zeros(picture.shape[:2], np.int32)
    out[2:12, 2:12] = 1
    out[5:9, 5:9] = 0
    out[14, 12:14] = 1
    out[14, 14] = 2
    return out
'''


def run_image(m, picture, params=None):
    encoded = cv2.imencode('.png', picture)[1].tobytes()
    impl = operations.OPERATIONS['cells'].impl(m.id)
    return asyncio.run(operations.ADAPTERS[('cells', m.id)](
        None, SimpleNamespace(jpeg=encoded), None, impl, params or {}))


def retained_labels(out, shape):
    result = np.zeros(shape, np.int32)
    for member in members_from(out, Evidence(None, None)).values():
        mask = member.mask
        h, w = mask.bits.shape
        view = result[mask.y0:mask.y0+h, mask.x0:mask.x0+w]
        view[mask.bits] = member.instance
    return result


def test_native_labels_keep_holes_disconnected_pieces_and_tiny_instances(home, monkeypatch):
    m = contribute(home, LABELS)
    assert install(home, monkeypatch)['state'] == 'done'
    env_runner.register()
    out = run_image(m, np.full((96, 96, 3), 220, np.uint8))
    expected = np.zeros((96, 96), np.int32)
    expected[2:12, 2:12] = 1
    expected[5:9, 5:9] = 0
    expected[14, 12:14] = 1
    expected[14, 14] = 2
    np.testing.assert_array_equal(retained_labels(out, expected.shape), expected)
    assert env_runner.proof(m)['found'] == 2


@pytest.mark.parametrize('value,dtype', [('0.5', 'float32'), ('-1', 'int32'), ('4294967296', 'uint64')])
def test_invalid_native_label_values_fail_the_install_proof(home, monkeypatch, value, dtype):
    adapter = f'import numpy as np\ndef segment(picture, params):\n    return np.full(picture.shape[:2], {value}, np.{dtype})\n'
    m = contribute(home, adapter)
    row = install(home, monkeypatch)
    assert row['state'] == 'failed', row
    assert 'label' in row['says']
    assert not env_runner.proof(m)['ok']
    assert env_runner.register()['added'] == []


def test_nonzero_crop_uses_width_height_and_keeps_native_capture_origin(home, monkeypatch):
    adapter = '''
import numpy as np
def segment(picture, params):
    return (picture[:, :, 0] < 128).astype(np.int32)
'''
    m = contribute(home, adapter)
    assert install(home, monkeypatch)['state'] == 'done'
    env_runner.register()
    picture = np.full((96, 96, 3), 220, np.uint8)
    picture[30:80, 20:60] = 30
    out = run_image(m, picture, {'crop': (20, 30, 40, 50)})
    expected = np.zeros((96, 96), np.int32)
    expected[30:80, 20:60] = 1
    assert out['labels']['shape'] == [50, 40]
    assert out['labels']['origin'] == [20, 30]
    np.testing.assert_array_equal(retained_labels(out, expected.shape), expected)


@pytest.mark.parametrize('change', ['removed', 'invalid'])
def test_removed_or_invalid_entry_is_withdrawn_and_its_worker_stops(home, monkeypatch, change):
    m = contribute(home, LABELS)
    assert install(home, monkeypatch)['state'] == 'done'
    env_runner.register()
    settings = options.Settings()
    settings.update({'cells': m.id})
    run_image(m, np.full((96, 96, 3), 220, np.uint8))
    child = env_runner.client(env_runner.in_use(m))._process
    entry = home / 'repo' / 'models' / m.id
    if change == 'removed':
        entry.rename(home / 'removed-entry')
    else:
        (entry / 'model.toml').write_text('invalid TOML')
    catalog.manifests.cache_clear()
    assert catalog.get(m.id) is None
    result = env_runner.register(settings)
    assert result == {'added': [], 'removed': [m.id], 'reset': True}
    assert settings.cells == options.Settings().cells
    assert m.id not in options.GROUPS['cells']
    assert all(i.id != m.id for i in operations.OPERATIONS['cells'].implementations)
    for choice in options.GROUPS['cells']:
        readiness.option('cells', choice, {})  # every remaining choice still has a readable licence
    child.wait(timeout=2)
    assert child.returncode is not None


def weighted_entry(home, version, contents, *, needs='', tier='open'):
    contribute(home, LABELS, version=version)
    path = home / 'repo' / 'models' / 'dark-spots' / 'model.toml'
    entry = path.read_text().replace('needs_to_run = ["install:dark-spots"]',
                                    f'needs_to_run = ["install:dark-spots"{needs}]')
    entry = entry.replace('tier = "open"', f'tier = "{tier}"')
    path.write_text('needs_to_get = ["download", "key:HF_TOKEN"]\nfiles = [{ path = "weights.bin", '
                    f'url = "https://example.org/{version}", bytes = {len(contents)}, '
                    f'sha256 = "{hashlib.sha256(contents).hexdigest()}"' + '}]\n' + entry)
    catalog.manifests.cache_clear()
    m = catalog.get('dark-spots')
    assert m is not None, catalog.problems()
    return m


def test_update_requirements_leave_proved_generation_running_but_its_own_files_still_gate(home, monkeypatch):
    from orchestrator import catalog_view
    import os

    monkeypatch.delenv('MICROMIND_MODEL_FILES', raising=False)
    monkeypatch.setenv('MICROMIND_EDITION', 'commercial')
    old = weighted_entry(home, 'v1', b'old')
    folder = model_files.revision_dir(old)
    folder.mkdir(parents=True)
    (folder / 'weights.bin').write_bytes(b'old')
    (folder / model_files.RECEIPT).write_text(json.dumps({'revision': catalog.revision_of(old)}))
    assert install(home, monkeypatch)['state'] == 'done'
    env_runner.register()
    new = weighted_entry(home, 'v2', b'new!', needs=', "key:OPENAI_API_KEY"', tier='noncommercial')
    with readiness.batch():
        assert readiness.obtained(new)[0] is False, 'update still needs its own download'
        assert readiness.model(new.id, {}).status == 'ready'
        assert readiness.option('cells', new.id, {}).status == 'ready'
    profile = dict(os.environ)
    with monkeypatch.context() as isolated:
        isolated.setenv('MICROMIND_CATALOG', str(home / 'other-profile'))
        assert readiness.model(new.id, {}, environ=profile).status == 'ready'
    facts = catalog_view.facts(new, {}, lambda k: False, [], os.environ)
    assert any(f.get('install') == new.id and 'Update available' in f['says'] for f in facts)
    assert run_image(new, np.full((96, 96, 3), 220, np.uint8))['version'] == 'v1'
    (folder / 'weights.bin').unlink()
    assert readiness.model(new.id, {}).status == 'needs-setup'
    assert readiness.option('cells', new.id, {}).status == 'needs-setup'
    assert any(s.kind == 'download' for s in readiness.model(new.id, {}).steps)
    assert readiness.implementation(operations.OPERATIONS['cells'].impl(new.id)), 'execution preflight must agree'


def test_update_cannot_remove_the_active_generations_licence_restriction(home, monkeypatch):
    old = weighted_entry(home, 'v1-restricted', b'old', tier='noncommercial')
    monkeypatch.setenv('MICROMIND_MODEL_FILES', 'standin')
    assert install(home, monkeypatch)['state'] == 'done'
    env_runner.register()
    contribute(home, LABELS, version='v2-open')
    monkeypatch.setenv('MICROMIND_EDITION', 'commercial')
    assert readiness.model(old.id, {}).status == 'unavailable'
    assert readiness.option('cells', old.id, {}).status == 'unavailable'


def test_same_version_policy_change_waits_for_its_own_install_and_proof(home, monkeypatch):
    from orchestrator import catalog_view, licences
    import os

    old = contribute(home, LABELS)
    path = home / 'repo' / 'models' / old.id / 'model.toml'
    path.write_text(path.read_text().replace('tier = "open"', 'tier = "noncommercial"'))
    catalog.manifests.cache_clear()
    old = catalog.get(old.id)
    assert install(home, monkeypatch)['state'] == 'done'
    env_runner.register()
    original = env_runner.in_use(old).folder
    monkeypatch.setenv('MICROMIND_EDITION', 'commercial')
    assert not licences.permitted(old.licence)

    path.write_text(path.read_text().replace('tier = "noncommercial"', 'tier = "open"').replace(
        'needs_to_run = ["install:dark-spots"]',
        'needs_to_run = ["install:dark-spots", "key:OPENAI_API_KEY"]'))
    catalog.manifests.cache_clear()
    pending = catalog.get(old.id)
    assert not licences.permitted(pending.licence), 'editing policy cannot grant rights for the active copy'
    assert env_runner.operational(pending).needs == '', 'the active copy remains keyless'
    assert env_runner.in_use(pending).folder == original
    assert readiness.model(pending.id, {}).status == 'unavailable'
    facts = catalog_view.facts(pending, {}, lambda key: False, [], os.environ)
    assert any(f.get('install') == pending.id and 'Update available' in f['says'] for f in facts)

    assert install(home, monkeypatch)['state'] == 'done'
    assert env_runner.in_use(pending).folder == original, 'proof does not accept a policy change'
    env_runner.choose(pending, None)
    env_runner.register()
    assert env_runner.in_use(pending).folder != original
    assert licences.permitted(pending.licence)
    assert readiness.model(pending.id, {}).status == 'needs-setup'
    assert any(s.kind == 'key' for s in readiness.model(pending.id, {}).steps)


@pytest.mark.parametrize('before,after', [
    ('needs_to_run = ["install:dark-spots"]', 'needs_to_run = ["install:dark-spots", "key:OPENAI_API_KEY"]'),
    ('needs_to_run =', 'needs_to_get = ["key:HF_TOKEN"]\nneeds_to_run ='),
    ('licence = ["dark-spots"]', 'licence = ["dark-spots", "cellpose"]'),
    ('tier = "open"', 'tier = "noncommercial"'),
])
def test_each_policy_edit_independently_offers_an_update_and_keeps_the_active_copy(home, monkeypatch, before, after):
    m = contribute(home, LABELS)
    assert install(home, monkeypatch)['state'] == 'done'
    active = env_runner.in_use(m)
    path = home / 'repo' / 'models' / m.id / 'model.toml'
    path.write_text(path.read_text().replace(before, after))
    catalog.manifests.cache_clear()
    pending = catalog.get(m.id)
    assert pending is not None, catalog.problems()
    assert env_runner.update_waiting(pending)
    assert env_runner.operational(pending) == active.m
    assert readiness.model(m.id, {}).status == 'ready'


def test_wording_edit_needs_no_reinstall_and_cannot_replace_the_retained_manifest(home, monkeypatch):
    m = contribute(home, LABELS)
    assert install(home, monkeypatch)['state'] == 'done'
    installed = env_runner.in_use(m)
    retained = installed.folder / 'entry' / m.id / 'model.toml'
    original_bytes = retained.read_bytes()
    path = home / 'repo' / 'models' / m.id / 'model.toml'
    path.write_text(path.read_text().replace('name = "Dark spots"', 'name = "Renamed dark spots"'))
    catalog.manifests.cache_clear()
    discovered = catalog.get(m.id)
    assert discovered.name == 'Renamed dark spots'
    assert not env_runner.update_waiting(discovered)
    assert env_runner.in_use(discovered).current
    operational = env_runner.operational(discovered)
    assert operational == installed.m
    assert operational.name == 'Dark spots'
    assert operational.folder == str(retained.parent) != str(path.parent)
    assert retained.read_bytes() == original_bytes


def test_sparse_native_ids_keep_exact_pixels_with_memory_bounded_by_the_picture(home, monkeypatch, record_property):
    import subprocess
    import sys
    from orchestrator.evidence import decode_labels

    adapter = '''
import numpy as np
def segment(picture, params):
    out = np.zeros(picture.shape[:2], np.int32)
    out[1, 1] = out[2, 2] = 100000
    out[5:7, 8:10] = 7
    return out
'''
    m = contribute(home, adapter)
    assert install(home, monkeypatch)['state'] == 'done'
    env_runner.register()
    out = run_image(m, np.full((16, 16, 3), 220, np.uint8))
    labels = decode_labels(out['labels'])
    # A clean measurement process excludes other tests' background threads and one-time imports.
    measured = subprocess.run([sys.executable, '-c', '''
import json, sys, tracemalloc
import numpy as np
from orchestrator.evidence import masks_from_labels
labels = np.asarray(json.loads(sys.stdin.read()), dtype=np.int32)
masks_from_labels(np.zeros((16, 16), np.int32))
tracemalloc.start()
masks = masks_from_labels(labels)
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
print(json.dumps({'peak': peak, 'instances': [[int(label), int(mask.bits.sum())] for label, mask in masks]}))
'''], input=json.dumps(labels.tolist()), text=True, capture_output=True, timeout=30, check=True)
    result = json.loads(measured.stdout)
    peak = result['peak']
    record_property('maskExtractionPeakBytes', peak)
    assert peak < 128 * 1024, f'two instances on 256 pixels allocated {peak} bytes'
    assert result['instances'] == [[7, 4], [100000, 2]]
    expected = np.zeros((16, 16), np.int32)
    expected[1, 1] = expected[2, 2] = 100000
    expected[5:7, 8:10] = 7
    np.testing.assert_array_equal(retained_labels(out, expected.shape), expected)
    assert env_runner.proof(m)['found'] == 2
