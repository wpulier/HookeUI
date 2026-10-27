import json

from orchestrator import build_info


def test_rebuilt_frontend_does_not_claim_to_match_older_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(build_info, 'ROOT', tmp_path)
    backend = {'revision': 'original', 'dirty': True, 'sourceDigest': 'old-source'}
    dist = tmp_path / 'apps/studio/dist'
    dist.mkdir(parents=True)
    manifest = dist / 'build.json'
    manifest.write_text(json.dumps(backend))
    assert build_info.runtime_identity(backend)['matches']
    manifest.write_text(json.dumps({**backend, 'sourceDigest': 'new-source'}))
    result = build_info.runtime_identity(backend)
    assert result['backend'] == backend and not result['matches']
    manifest.unlink()
    assert not build_info.runtime_identity(backend)['matches']


def test_packaged_copy_reads_its_identity_and_never_runs_git(tmp_path, monkeypatch):
    built = {'revision': 'a' * 40, 'dirty': False, 'sourceDigest': 'digest'}
    packaged = tmp_path / 'packaged.json'
    packaged.write_text(json.dumps({'app': 'HookeUI', 'identity': built}))
    monkeypatch.setattr(build_info, 'PACKAGED', packaged)
    monkeypatch.setattr(build_info.subprocess, 'check_output', lambda *a, **k: (_ for _ in ()).throw(AssertionError('git ran')))
    assert build_info.identity() == built
    packaged.write_text('{"identity": {}}')
    assert build_info.identity() == {'revision': None, 'dirty': None, 'sourceDigest': None}
