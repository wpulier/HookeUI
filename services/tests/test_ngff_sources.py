"""OME-Zarr multiscale images as native regional sources, local and remote (orchestrator/ngff_sources.py, H08)."""
import json
import threading
import zlib

import numpy as np
import pytest

from bench import h14_fixtures, h14_remote_server
from orchestrator import ngff_sources as ngff
from orchestrator.native_regions import NativeRegionInputs
from env_worker import lazy_arrays, native_regions
from tests.ngff_fixtures import selected

SHAPE = (3, 1100, 700)


@pytest.fixture(scope="module")
def pyramid_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("ngff") / "image.zarr"
    h14_fixtures.write_ome_zarr(root, SHAPE, levels=3)
    return root


def captured(root, projects):
    store, saved = selected(root, projects)
    return ngff.SelectedStore(store, saved.id, saved.session, saved.revision)


def test_a_local_pyramid_reads_exact_values_at_every_level(pyramid_dir, tmp_path):
    store = captured(pyramid_dir, tmp_path / "projects")
    p = ngff.open_multiscale(store)
    assert [l.shape for l in p.levels] == [(3, 1100, 700), (3, 550, 350), (3, 275, 175)]
    assert p.axes == ("z", "y", "x") and p.units[0] == "micrometer" and len(p.revision) == 64
    z, y, x = np.ogrid[1:2, 500:530, 600:700]
    assert np.array_equal(p.levels[0].read((slice(1, 2), slice(500, 530), slice(600, 700))), h14_fixtures.big_value(z, y, x))
    zz, yy, xx = np.ogrid[2:3, 10:20, 100:175]
    got = p.levels[2].read((slice(2, 3), slice(10, 20), slice(100, 175)))
    assert np.array_equal(got, h14_fixtures.big_value(zz, yy * 4, xx * 4))
    assert all(row["sha256"] and row["status"] == "ok" for row in store.audit if row["key"].endswith(("0", "1")))


def test_the_worker_navigates_the_pyramid_lazily_through_the_native_grant(pyramid_dir, tmp_path):
    """Parent provider -> NativeRegionInputs -> worker NativeArray -> LazyNative levels, end to end in one process."""
    p = ngff.open_multiscale(captured(pyramid_dir, tmp_path / "projects"))
    sources = {f"level{i}": s for i, s in enumerate(p.sources())}
    host = NativeRegionInputs(sources)
    arrays = native_regions.bind(host.specifications(), lambda header: host.answer(header))
    pyramid = lazy_arrays.Multiscale([arrays[f"level{i}"] for i in range(3)], lazy_arrays.ChunkCache(8 << 20))
    assert np.array_equal(pyramid[2][1, :5, :5], h14_fixtures.big_value(1, np.arange(5)[:, None] * 4, np.arange(5)[None] * 4))
    assert pyramid[0][2, 1099, 699] == h14_fixtures.big_value(2, 1099, 699)
    assert pyramid.stats["readBytes"] < SHAPE[1] * SHAPE[2] * 2  # far less than one full plane


def test_compressed_missing_and_unsupported_chunks(tmp_path):
    root = tmp_path / "c.zarr"
    (root / "0").mkdir(parents=True)
    (root / ".zattrs").write_text(json.dumps({"multiscales": [{"version": "0.4", "axes": [
        {"name": "y", "type": "space"}, {"name": "x", "type": "space"}],
        "datasets": [{"path": "0", "coordinateTransformations": [{"type": "scale", "scale": [1, 1]}]}]}]}))
    meta = {"zarr_format": 2, "shape": [4, 6], "chunks": [2, 3], "dtype": "<u2", "compressor": {"id": "zlib", "level": 1},
            "fill_value": 7, "order": "C", "filters": None}
    (root / "0" / ".zarray").write_text(json.dumps(meta))
    block = np.arange(6, dtype="<u2").reshape(2, 3)
    (root / "0" / "0.0").write_bytes(zlib.compress(block.tobytes()))
    p = ngff.open_multiscale(captured(root, tmp_path / "projects"))
    got = p.levels[0].read((slice(0, 4), slice(0, 6)))
    assert np.array_equal(got[:2, :3], block) and (got[2:] == 7).all() and (got[:2, 3:] == 7).all()
    (root / "0" / ".zarray").write_text(json.dumps({**meta, "compressor": {"id": "blosc"}}))
    with pytest.raises(ngff.NgffError, match="blosc codec isn't supported"):
        ngff.open_multiscale(captured(root, tmp_path / "projects"))


def test_keys_cannot_leave_the_chosen_folder(tmp_path, pyramid_dir):
    store = ngff.LocalStore(pyramid_dir)
    for key in ("../secret", "/etc/passwd", "0/../../x", "."):
        with pytest.raises(ngff.NgffError):
            store.get(key)
    (tmp_path / "secret").write_text("x")
    link = pyramid_dir / "escape"
    link.symlink_to(tmp_path / "secret")
    try:
        with pytest.raises(ngff.NgffError, match="outside the chosen folder"):
            store.get("escape")
    finally:
        link.unlink()


@pytest.fixture
def served(pyramid_dir):
    server = h14_remote_server.serve(pyramid_dir.parent, port=0, after=10 ** 9, seconds=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server, f"http://127.0.0.1:{server.server_address[1]}/{pyramid_dir.name}"
    server.shutdown()


def test_a_remote_pyramid_reads_through_the_broker_and_never_logs_credentials(served):
    server, url = served
    broker = ngff.Broker(backoff_s=0.01)
    broker.grant(url, {"Authorization": "Bearer s3cret-token"})
    store = ngff.RemoteStore(url, broker)
    assert json.loads(store.get(".zattrs"))["multiscales"][0]["version"] == "0.4"
    with pytest.raises(ngff.NgffError, match="accepted.*folder"):
        ngff.open_multiscale(store)
    text = json.dumps(store.audit) + repr(broker.describe())
    assert "s3cret" not in text and "with credentials" in text
    with pytest.raises(ngff.NgffError, match="not inside a granted dataset"):
        ngff.RemoteStore(url.replace("127.0.0.1", "localhost"), broker)
    with pytest.raises(ngff.NgffError, match="https"):
        ngff.Broker().grant("http://example.org/data.zarr")


def test_a_cut_connection_is_retried_and_recovers(pyramid_dir):
    state = h14_remote_server.CutState(after=3, seconds=0.3)
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), h14_remote_server.handler(pyramid_dir.parent, state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/{pyramid_dir.name}"
        broker = ngff.Broker(attempts=6, backoff_s=0.1)
        broker.grant(url)
        store = ngff.RemoteStore(url, broker)
        for _ in range(4):
            assert json.loads(store.get(".zattrs"))["multiscales"][0]["version"] == "0.4"
        statuses = [row["status"] for row in store.audit]
        assert any(s.startswith("failed") for s in statuses) and statuses[-1] == "ok"
    finally:
        server.shutdown()


def test_a_server_that_stays_down_is_said_and_cancel_stops_retrying(pyramid_dir):
    state = h14_remote_server.CutState(after=0, seconds=60)
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), h14_remote_server.handler(pyramid_dir.parent, state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/{pyramid_dir.name}"
        broker = ngff.Broker(attempts=3, backoff_s=0.01)
        broker.grant(url)
        with pytest.raises(ngff.RemoteUnavailable, match="after 3 attempts.*can be retried"):
            ngff.RemoteStore(url, broker).get(".zattrs")
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(ngff.Cancelled):
            ngff.RemoteStore(url, broker, cancel=cancel).get(".zattrs")
    finally:
        server.shutdown()
