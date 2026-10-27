"""Pinned artifact admission for scientific tools; synthetic files, no model claims."""
import hashlib
import json
import os
from dataclasses import replace
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from gpu_worker import manifests as catalog, model_files
from orchestrator import generation_leases, owned_process, prepared_artifacts


def artifact(tmp_path, *, version='1', content=b'first model'):
    home = tmp_path.resolve()
    environ = {'MICROMIND_CATALOG': str(home / 'catalog')}
    manifest = catalog.Manifest(id='model-tool', name='Model tool', version=version, runs='local', licence='MIT',
        source={'hf': 'test/model', 'revision': version * 40},
        files=({'path': 'weights/model.bin', 'bytes': len(content),
                'sha256': hashlib.sha256(content).hexdigest()},))
    folder = model_files.revision_dir(manifest, environ)
    (folder / 'weights').mkdir(parents=True)
    (folder / 'weights/model.bin').write_bytes(content)
    (folder / model_files.RECEIPT).write_text(json.dumps({'revision': catalog.revision_of(manifest)}))
    return manifest, environ, folder


def test_exact_prepared_manifest_selects_its_files_and_holds_removal(tmp_path):
    old, environ, folder = artifact(tmp_path)
    newer, _, other = artifact(tmp_path, version='2', content=b'new model')
    with prepared_artifacts.open_files(old, environ) as files:
        assert files.root == folder
        assert files.read_paths == (folder / 'weights/model.bin',)
        assert files.read_paths[0].read_bytes() == b'first model'
        assert other not in files.read_paths
        with pytest.raises(generation_leases.Busy):
            with generation_leases.withdraw(folder):
                pass
    with generation_leases.withdraw(folder):
        pass
    with prepared_artifacts.open_files(newer, environ) as files:
        assert files.root == other


@pytest.mark.parametrize('damage', ['missing', 'same-size-corruption', 'leaf-link', 'directory-link', 'fifo', 'receipt-link', 'receipt-fifo', 'wrong-receipt'])
def test_damaged_files_refuse_and_release_their_lease(tmp_path, damage):
    manifest, environ, folder = artifact(tmp_path)
    target = folder / 'weights/model.bin'
    if damage == 'missing':
        target.unlink()
    elif damage == 'same-size-corruption':
        target.write_bytes(b'other model')
    elif damage in ('leaf-link', 'fifo'):
        target.unlink()
        if damage == 'fifo':
            os.mkfifo(target)
        else:
            external = tmp_path / 'outside.bin'; external.write_bytes(b'first model')
            target.symlink_to(external)
    elif damage == 'directory-link':
        (folder / 'weights').rename(folder / 'elsewhere')
        (folder / 'weights').symlink_to(folder / 'elsewhere', target_is_directory=True)
    else:
        receipt = folder / model_files.RECEIPT
        receipt.unlink()
        if damage == 'receipt-fifo':
            os.mkfifo(receipt)
        elif damage == 'receipt-link':
            outside = tmp_path / 'receipt-copy'; outside.write_text(json.dumps({'revision': catalog.revision_of(manifest)}))
            receipt.symlink_to(outside)
        else:
            receipt.write_text(json.dumps({'revision': 'different'}))
    with pytest.raises(prepared_artifacts.NotPrepared):
        with prepared_artifacts.open_files(manifest, environ):
            pytest.fail('damaged artifact admitted')
    with generation_leases.withdraw(folder):
        pass


def test_missing_download_has_no_developer_cache_fallback(tmp_path):
    manifest, environ, folder = artifact(tmp_path)
    (folder / model_files.RECEIPT).unlink()
    with pytest.raises(prepared_artifacts.NotPrepared, match='Prepare'):
        with prepared_artifacts.open_files(manifest, environ):
            pass


def test_no_artifacts_grants_no_path(tmp_path):
    manifest, environ, _ = artifact(tmp_path)
    with prepared_artifacts.open_files(replace(manifest, files=()), environ) as files:
        assert files is None


def test_live_alias_is_not_an_immutable_artifact_declaration(tmp_path):
    manifest, environ, _ = artifact(tmp_path)
    with pytest.raises(prepared_artifacts.NotPrepared, match='directly'):
        with prepared_artifacts.open_files(replace(manifest, files_from='other-model'), environ):
            pass


def test_cancellation_during_hashing_releases_artifact_lease(tmp_path):
    manifest, environ, folder = artifact(tmp_path, content=b'x' * (3 << 20))
    checks = 0
    def stop():
        nonlocal checks
        checks += 1
        return checks >= 4
    with pytest.raises(owned_process.Stopped):
        with owned_process.scope(stop=stop):
            with prepared_artifacts.open_files(manifest, environ):
                pytest.fail('cancelled preparation admitted')
    with generation_leases.withdraw(folder):
        pass


def test_huggingface_git_blob_identity_is_supported(tmp_path):
    content = b'small tokenizer metadata'
    manifest, environ, folder = artifact(tmp_path, content=content)
    declaration = {'path': 'weights/model.bin', 'bytes': len(content),
                   'git_sha1': hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()}
    with prepared_artifacts.open_files(replace(manifest, files=(declaration,)), environ) as files:
        assert files.read_paths == (folder / 'weights/model.bin',)


@pytest.mark.parametrize('change', [
    {'path': '../outside'}, {'path': '/outside'}, {'path': 'weights/../model.bin'},
    {'path': 'weights//model.bin'}, {'bytes': True}, {'bytes': -1},
    {'sha256': None}, {'sha256': 'invalid'},
])
def test_invalid_declarations_refuse(tmp_path, change):
    manifest, environ, folder = artifact(tmp_path)
    manifest = replace(manifest, files=({**manifest.files[0], **change},))
    with pytest.raises(prepared_artifacts.NotPrepared):
        with prepared_artifacts.open_files(manifest, environ):
            pass
    with generation_leases.withdraw(folder):
        pass


def test_consumer_failure_is_not_mislabeled_as_preparation_failure(tmp_path):
    manifest, environ, folder = artifact(tmp_path)
    with pytest.raises(ValueError, match='downstream algorithm'):
        with prepared_artifacts.open_files(manifest, environ):
            raise ValueError('downstream algorithm')
    with generation_leases.withdraw(folder):
        pass


@pytest.mark.parametrize('startup_failure', [False, True])
def test_scientific_review_owns_artifacts_through_shutdown(tmp_path, monkeypatch, startup_failure):
    import numpy as np
    from orchestrator import scientific_host
    from tests.test_native_regions import source
    manifest, environ, folder = artifact(tmp_path)
    prepared = SimpleNamespace(m=SimpleNamespace(owner=manifest, bridge='scientific-host-v1', resources={}, reader={}))
    @contextmanager
    def member(*args):
        yield prepared
    monkeypatch.setattr(scientific_host.sandbox, 'available', lambda: True)
    monkeypatch.setattr(scientific_host.prepared_bundles, 'prepared_member', member)
    monkeypatch.setattr(scientific_host, '_bootstrap', lambda root: None)
    def start(worker):
        assert worker.artifacts.root == folder
        with pytest.raises(generation_leases.Busy):
            with generation_leases.withdraw(folder):
                pass
        if startup_failure:
            raise OSError('worker startup failed')
    monkeypatch.setattr(scientific_host.ReviewWorker, 'start', start)
    def run():
        with scientific_host.review('model#tool', 'generation', {'volume': source(np.ones((2, 3), dtype='<u2'), [])}, environ) as worker:
            assert worker.artifacts.read_paths == (folder / 'weights/model.bin',)
    if startup_failure:
        with pytest.raises(OSError, match='startup failed'):
            run()
    else:
        run()
    with generation_leases.withdraw(folder):
        pass


def test_worker_launch_grants_only_declared_artifacts(tmp_path, monkeypatch):
    from orchestrator import scientific_host
    manifest, environ, folder = artifact(tmp_path)
    (folder / 'undeclared-secret').write_text('not a model input')
    work = tmp_path / 'work'; work.mkdir()
    prepared = SimpleNamespace(folder=tmp_path / 'runtime', python_home=tmp_path / 'python',
        python=tmp_path / 'python/bin/python', entry=tmp_path / 'runtime/entry',
        m=SimpleNamespace(adapter='adapter:open_host'))
    grants = SimpleNamespace(specifications=lambda: {}, close=lambda: None)
    monkeypatch.setattr(scientific_host.ReviewWorker, '_allowed', lambda worker: None)
    def launch(command, **options):
        profile = command[command.index('-p') + 1]
        assert f'(subpath "{folder}/weights/model.bin")' in profile
        assert f'(subpath "{folder}")' not in profile
        assert 'undeclared-secret' not in profile
        assert '(deny network*)' in profile and '(deny process-fork)' in profile
        assert options['env']['MICROMIND_ENTRY_FILES'] == str(folder)
        assert options['env']['HOME'] == str(work)
        assert options['close_fds'] is True
        raise OSError('captured launch without executing')
    monkeypatch.setattr(scientific_host.subprocess, 'Popen', launch)
    with prepared_artifacts.open_files(manifest, environ) as files:
        worker = scientific_host.ReviewWorker(prepared, grants, work, tmp_path / 'bootstrap', environ, artifacts=files)
        try:
            with pytest.raises(OSError, match='captured launch'):
                worker.start()
        finally:
            worker.close()


def test_corrected_publication_preserves_running_files_and_staging(tmp_path):
    from orchestrator import downloads
    manifest, environ, folder = artifact(tmp_path)
    replacement = b'repaired model, different size'
    updated = replace(manifest, files=({'path': 'weights/model.bin', 'bytes': len(replacement),
                                       'sha256': hashlib.sha256(replacement).hexdigest()},))
    stage = downloads.staging(updated, environ)
    (stage / 'weights').mkdir(parents=True)
    (stage / 'weights/model.bin').write_bytes(replacement)
    with prepared_artifacts.open_files(manifest, environ):
        with pytest.raises(downloads.Failed, match='running work'):
            downloads.publish(updated, 'synthetic corrected download', environ)
        assert (folder / 'weights/model.bin').read_bytes() == b'first model'
        assert (stage / 'weights/model.bin').read_bytes() == replacement
    assert downloads.publish(updated, 'synthetic corrected download', environ) == folder
    with prepared_artifacts.open_files(updated, environ) as files:
        assert files.read_paths[0].read_bytes() == replacement


def test_other_process_cannot_replace_files_while_a_tool_uses_them(tmp_path):
    import subprocess
    import sys
    manifest, environ, folder = artifact(tmp_path)
    script = '''import fcntl, sys
with open(sys.argv[1], 'a') as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(23)
'''
    def attempt():
        return subprocess.run([sys.executable, '-I', '-c', script, str(folder.parent / '.lock')], timeout=10).returncode
    with prepared_artifacts.open_files(manifest, environ):
        with prepared_artifacts.open_files(manifest, environ):
            assert attempt() == 23
        assert attempt() == 23
    assert attempt() == 0
