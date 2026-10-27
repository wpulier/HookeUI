"""The distributable core contains cleared media and no private managed backend."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_core_excludes_private_backend_brand_and_personal_manifest():
    for name in ('brand', 'services/hosted', 'services/research_runtime', 'docs/handoff', 'docs/planning'):
        assert not (ROOT / name).exists(), name
    manifest = json.loads((ROOT / 'assets/manifest.json').read_text())
    assert manifest['real'] == [] and manifest['backup'] == {}
    assert (ROOT / 'contract/verdicts.jsonl').read_text() == ''
    assert 'Apache License' in (ROOT / 'LICENSE').read_text()

def test_distributed_media_matches_its_rights_inventory():
    records = json.loads((ROOT / 'MEDIA.json').read_text())['assets']
    assert len({r['path'] for r in records}) == len(records)
    for row in records:
        path = ROOT / row['path']
        assert path.is_file(), row['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256'], row['path']
        assert row['license'] and row['provenance']
