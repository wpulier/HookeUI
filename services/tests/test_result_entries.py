"""Account-free result acquisition through the Studio command; synthetic entry/pixels, local catalog Git only."""
import asyncio
import random
import struct
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import commands, made_with, readiness, result_entries
from orchestrator.capture import Memory
from tests.test_channels import home  # noqa: F401
from tests.test_share import yours


@pytest.mark.parametrize("suffix", [".png", ".jpg"])
def test_unpublished_result_carries_exact_entry_to_another_mac_without_an_account(home, monkeypatch, tmp_path, suffix):
    key = yours(home)
    producer = catalog.get(key)
    folder = catalog.entry_folder(producer)
    original = (folder / "adapter.py").read_bytes()
    # Synthetic incompressible support bytes exercise JPEG's multiple-segment transport without any weights.
    support = random.Random(42).randbytes(150_000)
    (folder / "README.md").write_bytes(support)
    memory = Memory()
    stamp = made_with.capture(producer, generation="then", memory=memory)
    assert "portable" in stamp and "acquire" not in stamp
    # The entry changes after the run, before the result is saved: only the producing snapshot may travel.
    (folder / "adapter.py").write_text("raise RuntimeError('new and unreviewed')\n")
    ok, encoded = cv2.imencode(suffix, np.full((8, 8, 3), 40, np.uint8))
    assert ok
    image = tmp_path / ("result" + suffix)
    with result_entries.picture(encoded.tobytes(), [stamp], memory) as (data, kept):
        image.write_bytes(data)
    assert kept == [stamp]
    assert np.array_equal(cv2.imread(str(image)), cv2.imdecode(encoded, cv2.IMREAD_COLOR))
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "recipient"))
    catalog.manifests.cache_clear()
    assert made_with.read(image) == [stamp]
    offer = made_with.offers(made_with.read(image))[0]
    assert offer["state"] == "acquire" and "picture" in offer["says"]
    assert catalog.get(key) is None
    synced = []

    async def sync():
        synced.append(True)

    app = SimpleNamespace(hub=SimpleNamespace(library=SimpleNamespace(get=lambda aid: SimpleNamespace(path=image))),
                          sync_mine=sync)
    result = asyncio.run(commands.made_with_acquire(app, None, {"asset": "owned-result", "key": key}))
    received = catalog.get(result["model"])
    assert result["ok"] and synced
    assert (catalog.entry_folder(received) / "adapter.py").read_bytes() == original
    assert (catalog.entry_folder(received) / "README.md").read_bytes() == support
    assert readiness.model(received.key, {}).steps[0].kind == "trust"
    # The recipient can share a result made by that portable entry with a third Mac, still without publishing it.
    carried_again = made_with.capture(received, memory=memory)
    with result_entries.picture(encoded.tobytes(), [carried_again], memory) as (data, kept):
        image.write_bytes(data)
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "third-mac"))
    catalog.manifests.cache_clear()
    again = asyncio.run(commands.made_with_acquire(app, None, {"asset": "owned-result", "key": received.key}))
    third = catalog.get(again["model"])
    assert (catalog.entry_folder(third) / "adapter.py").read_bytes() == original
    assert readiness.model(third.key, {}).steps[0].kind == "trust"


@pytest.mark.parametrize("damage", ["changed-bytes", "oversized-chunk"])
def test_damaged_or_oversized_entry_is_refused_before_catalog_import(home, monkeypatch, tmp_path, damage):
    from orchestrator.result_entries import MAX_SCAN

    key = yours(home)
    memory = Memory()
    stamp = made_with.capture(catalog.get(key), memory=memory)
    ok, encoded = cv2.imencode(".png", np.zeros((8, 8, 3), np.uint8))
    assert ok
    with result_entries.picture(encoded.tobytes(), [stamp], memory) as (carried, kept):
        data = bytearray(carried)
    offset = data.index(b"obUi")
    if damage == "changed-bytes":
        data[offset + 4 + 32] ^= 1
    else:
        data[offset - 4:offset] = struct.pack(">I", MAX_SCAN + 1)
    image = tmp_path / "damaged.png"
    image.write_bytes(data)
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "recipient"))
    catalog.manifests.cache_clear()

    async def sync():
        pytest.fail("a refused acquisition must not synchronize a new entry")

    app = SimpleNamespace(hub=SimpleNamespace(library=SimpleNamespace(get=lambda aid: SimpleNamespace(path=image))),
                          sync_mine=sync)
    with pytest.raises(commands.ToolFailed, match="damaged|size limit"):
        asyncio.run(commands.made_with_acquire(app, None, {"asset": "owned-result", "key": key}))
    assert not catalog.channels()
