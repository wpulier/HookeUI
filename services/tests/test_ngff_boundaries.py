"""Synthetic pixels and loopback transport; no native/browser or large-data qualification."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from orchestrator import ngff_sources as ngff


@pytest.mark.parametrize('destination', ['other-origin', 'outside-prefix'])
def test_remote_redirect_never_receives_granted_credentials(destination):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.path, self.headers.get('Authorization')))
            if self.path == '/chosen/chunk':
                self.send_response(302)
                self.send_header('Location', redirected)
                self.end_headers()
            else:
                self.send_response(200); self.end_headers(); self.wfile.write(b'escaped')
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    redirected = (base.replace('127.0.0.1', 'localhost') if destination == 'other-origin' else base) + '/outside'
    broker = ngff.Broker(attempts=1); broker.grant(base + '/chosen', {'Authorization': 'synthetic-secret'})
    try:
        with pytest.raises(ngff.RemoteUnavailable, match='302'):
            ngff.RemoteStore(base + '/chosen', broker).get('chunk')
        assert received == [('/chosen/chunk', 'synthetic-secret')]
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_selected_folder_revision_keeps_pixels_when_original_changes(tmp_path):
    import numpy as np
    from bench import h14_fixtures
    from tests.ngff_fixtures import selected
    root = tmp_path / 'original.zarr'
    h14_fixtures.write_ome_zarr(root, (1, 8, 8), levels=1)
    projects, saved = selected(root, tmp_path / 'projects')
    store = ngff.SelectedStore(projects, saved.id, 's1', saved.revision)
    pyramid = ngff.open_multiscale(store)
    (root / '0/0/0/0').write_bytes(b'changed original')
    values = pyramid.levels[0].read((slice(0, 1), slice(0, 2), slice(0, 3)))
    assert np.array_equal(values, [[[0, 7, 14], [13, 20, 27]]])
    assert pyramid.sources()[0].descriptor['reference']['session'] == 's1'
    assert pyramid.sources()[0].descriptor['reference']['id'] == saved.id


def test_raw_folder_cannot_claim_an_accepted_content_revision(tmp_path):
    from bench import h14_fixtures
    root = tmp_path / 'raw.zarr'
    h14_fixtures.write_ome_zarr(root, (1, 2, 3), levels=1)
    with pytest.raises(ngff.NgffError, match='accepted.*folder'):
        ngff.open_multiscale(ngff.LocalStore(root))


@pytest.mark.parametrize('codec', ['zlib', 'gzip'])
def test_chunk_decoder_never_materializes_more_than_declared_output(tmp_path, monkeypatch, codec):
    import gzip, json, zlib
    from tests.ngff_fixtures import selected
    root = tmp_path / 'compressed.zarr'; (root / '0').mkdir(parents=True)
    (root / '.zattrs').write_text(json.dumps({'multiscales': [{'version': '0.4', 'axes': ['y', 'x'],
                                                           'datasets': [{'path': '0'}]}]}))
    (root / '0/.zarray').write_text(json.dumps({'zarr_format': 2, 'shape': [2, 3], 'chunks': [2, 3],
        'dtype': '<u2', 'compressor': {'id': codec}, 'fill_value': 0, 'order': 'C', 'filters': None}))
    compressed = (gzip if codec == 'gzip' else zlib).compress(b'x' * 100_000)
    (root / '0/0.0').write_bytes(compressed)
    projects, saved = selected(root, tmp_path / 'projects')
    pyramid = ngff.open_multiscale(ngff.SelectedStore(projects, saved.id, 's1', saved.revision))
    emitted = []
    original_object = zlib.decompressobj
    class Observed:
        def __init__(self, *args, **kwargs): self.decoder = original_object(*args, **kwargs)
        def __getattr__(self, name): return getattr(self.decoder, name)
        def decompress(self, body, max_length=0):
            result = self.decoder.decompress(body, max_length); emitted.append(len(result)); return result
    monkeypatch.setattr(zlib, 'decompressobj', Observed)
    with pytest.raises(ngff.NgffError):
        pyramid.levels[0].read((slice(0, 2), slice(0, 3)))
    assert emitted and sum(emitted) <= 13  # 12 declared bytes plus one overflow sentinel


def test_tampered_retained_content_refuses_before_returning_pixels(tmp_path):
    from bench import h14_fixtures
    from tests.ngff_fixtures import selected
    root = tmp_path / 'original.zarr'; h14_fixtures.write_ome_zarr(root, (1, 8, 8), levels=1)
    projects, saved = selected(root, tmp_path / 'projects')
    store = ngff.SelectedStore(projects, saved.id, 's1', saved.revision)
    pyramid = ngff.open_multiscale(store)
    # Mutate private synthetic retained storage, preserving size; accepted digest must refuse.
    path = projects.root / saved.id / 'files' / (store.source.chunks[0] + '.bin')
    body = path.read_bytes(); path.chmod(0o600); path.write_bytes(bytes([body[0] ^ 1]) + body[1:])
    with pytest.raises(ValueError, match='hash changed'):
        pyramid.levels[0].read((slice(0, 1), slice(0, 2), slice(0, 3)))


def test_missing_chunk_is_part_of_selected_folder_snapshot(tmp_path):
    import json
    from bench import h14_fixtures
    from tests.ngff_fixtures import selected
    root = tmp_path / 'original.zarr'; h14_fixtures.write_ome_zarr(root, (1, 8, 8), levels=1)
    chunk = root / '0/0/0/0'; original = chunk.read_bytes(); chunk.unlink()
    projects, saved = selected(root, tmp_path / 'projects')
    pyramid = ngff.open_multiscale(ngff.SelectedStore(projects, saved.id, 's1', saved.revision))
    chunk.write_bytes(original)
    assert not pyramid.levels[0].read((slice(0, 1), slice(0, 2), slice(0, 3))).any()
    later_projects, later = selected(root, tmp_path / 'later')
    newer = ngff.open_multiscale(ngff.SelectedStore(later_projects, later.id, 's1', later.revision))
    assert newer.revision != pyramid.revision
    assert newer.levels[0].read((slice(0, 1), slice(0, 2), slice(0, 3)))[0, 1, 2] == 27


def test_raw_local_read_enforces_length_after_stale_stat(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    (tmp_path / 'chunk').write_bytes(b'0123456789')
    store = ngff.LocalStore(tmp_path)
    original = Path.stat
    def stale(path, *args, **kwargs):
        observed = original(path, *args, **kwargs)
        if path.name == 'chunk':
            fields = list(observed); fields[6] = 1
            return os.stat_result(fields)
        return observed
    monkeypatch.setattr(Path, 'stat', stale)
    with pytest.raises(ngff.NgffError):
        store.get('chunk', limit=4)


def test_opening_metadata_does_not_read_unrelated_retained_pixels(tmp_path):
    from bench import h14_fixtures
    from tests.ngff_fixtures import selected
    root = tmp_path / 'original.zarr'; h14_fixtures.write_ome_zarr(root, (1, 1024, 1024), levels=1)
    projects, saved = selected(root, tmp_path / 'projects')
    reference = saved.document['resources'][0]
    source = projects.file_source(saved.id, 's1', saved.revision, reference)
    path = projects.root / saved.id / 'files' / (source.chunks[-1] + '.bin')
    body = path.read_bytes(); path.chmod(0o600); path.write_bytes(bytes([body[0] ^ 1]) + body[1:])
    # The ordinary full-integrity open still refuses any changed content by default.
    with pytest.raises(ValueError, match='hash changed'):
        projects.file_source(saved.id, 's1', saved.revision, reference)
    pyramid = ngff.open_multiscale(ngff.SelectedStore(projects, saved.id, 's1', saved.revision))
    assert pyramid.levels[0].shape == (1, 1024, 1024)
    # Metadata validity is not a claim that every content chunk is freshly verified.
    with pytest.raises(ValueError, match='hash changed'):
        pyramid.levels[0].read((slice(0, 1), slice(1023, 1024), slice(1023, 1024)))


@pytest.mark.parametrize('codec', ['zlib', 'gzip'])
def test_valid_compressed_chunk_preserves_native_pixels(tmp_path, codec):
    import gzip, json, zlib
    from tests.ngff_fixtures import selected
    root = tmp_path / 'compressed.zarr'; (root / '0').mkdir(parents=True)
    (root / '.zattrs').write_text(json.dumps({'multiscales': [{'version': '0.4', 'axes': ['y', 'x'],
                                                           'datasets': [{'path': '0'}]}]}))
    (root / '0/.zarray').write_text(json.dumps({'zarr_format': 2, 'shape': [2, 3], 'chunks': [2, 3],
        'dtype': '<u2', 'compressor': {'id': codec}, 'fill_value': 0, 'order': 'C', 'filters': None}))
    (root / '0/0.0').write_bytes((gzip if codec == 'gzip' else zlib).compress(bytes([1, 0, 2, 0, 3, 0, 4, 0, 5, 0, 6, 0])))
    projects, saved = selected(root, tmp_path / 'projects')
    pyramid = ngff.open_multiscale(ngff.SelectedStore(projects, saved.id, 's1', saved.revision))
    result = pyramid.levels[0].read((slice(0, 2), slice(0, 3)))
    assert result.dtype.str == '<u2' and result.tolist() == [[1, 2, 3], [4, 5, 6]]
