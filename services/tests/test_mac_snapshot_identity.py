"""A UI-only/native-only update must not start an older cached backend."""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('finalize_snapshot',
    Path(__file__).resolve().parents[2] / 'packaging/macos/finalize_snapshot.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_assembled_payload_changes_invalidate_runtime_even_at_same_version(tmp_path):
    tree = tmp_path / 'tree'
    dist = tree / 'apps/studio/dist'
    dist.mkdir(parents=True)
    manifest = tree / 'packaged.json'
    manifest.write_text(json.dumps({'version': '0.2.1', 'identity': {'revision': 'a'}, 'contentHash': 'old'}))
    (tmp_path / 'report.json').write_text(json.dumps({'scan': {'findings': []}}))
    ui = dist / 'index.js'
    ui.write_text('old UI')
    first = module.finalize(tmp_path)
    assert module.finalize(tmp_path) == first
    ui.write_text('new UI')
    second = module.finalize(tmp_path)
    assert second != first
    packaged = json.loads(manifest.read_text())
    packaged['identity']['revision'] = 'native-only-change'
    manifest.write_text(json.dumps(packaged))
    third = module.finalize(tmp_path)
    assert third != second
    assert json.loads((tmp_path / 'report.json').read_text())['contentHash'] == third
    assert json.loads((tmp_path / 'manifest.json').read_text())[0]['path'] == 'apps/studio/dist/index.js'


def test_standalone_snapshot_excludes_managed_implementation():
    snapshot_spec = importlib.util.spec_from_file_location('source_snapshot',
        Path(__file__).resolve().parents[2] / 'packaging/macos/snapshot.py')
    snapshot = importlib.util.module_from_spec(snapshot_spec)
    snapshot_spec.loader.exec_module(snapshot)
    assert not snapshot.runtime('services/research_runtime/scientific_endpoint.py')
    assert snapshot.runtime('services/env_worker/research_python_worker.py')
    assert snapshot.runtime('services/orchestrator/native_gpu_runtime.py')
