"""brainreg's adapter reads only what it needs of a volume (the review of 2026-10-13, finding 1): a preview decodes one
page, never the series; a stack reads only its own channel's and time point's planes and is refused before any pixel is
read when it would pass its limit; and each series' voxel sizes are its own (finding 2: the first OME Pixels was read
for every series). Real TIFFs written by tifffile, the version brainreg's environment pins; the adapter imported from its
folder, as its environment imports it."""
import importlib.util
import json

import numpy as np
import pytest
import tifffile

from gpu_worker import manifests as catalog

spec = importlib.util.spec_from_file_location("brainreg_adapter", catalog.ROOT / "brainreg" / "adapter.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


@pytest.fixture
def decoded(monkeypatch):
    """Every page decoded, and no series ever read whole."""
    pages = []
    page_read = tifffile.TiffPage.asarray

    def one(self, *a, **kw):
        pages.append(self.index)
        return page_read(self, *a, **kw)

    def whole(self, *a, **kw):
        raise AssertionError("the whole series was read")

    monkeypatch.setattr(tifffile.TiffPage, "asarray", one)
    monkeypatch.setattr(tifffile.TiffPageSeries, "asarray", whole)
    return pages


def test_a_preview_decodes_one_page_and_a_stack_only_its_own_planes(tmp_path, decoded):
    data = np.arange(5 * 2 * 6 * 7, dtype=np.uint16).reshape(5, 2, 6, 7)  # z, c, y, x
    path = tmp_path / "two-channels.tif"
    tifffile.imwrite(path, data, imagej=True, metadata={"axes": "ZCYX"}, compression="zlib")
    plane, why = adapter._preview(path)
    assert why == "" and len(decoded) == 1 and np.array_equal(plane, data[2, 0])
    decoded.clear()
    stack = adapter._stack(path, channel=1)
    assert np.array_equal(stack, data[:, 1]) and len(decoded) == 5, "channel 1's five planes, never channel 0's"


def test_a_stack_past_its_limit_is_refused_before_a_pixel_is_read(tmp_path, decoded):
    path = tmp_path / "big.tif"
    tifffile.imwrite(path, np.zeros((6, 64, 64), np.uint16))
    with pytest.raises(adapter.TooBig, match="past the"):
        adapter._stack(path, max_bytes=6 * 64 * 64 * 2 - 1)
    assert decoded == []
    assert adapter._stack(path, max_bytes=6 * 64 * 64 * 2).shape == (6, 64, 64)


@pytest.mark.parametrize("axes", ["ZSYX", "ZYXS", "ZCSYX", "ZCYXS"])
def test_rgb_samples_and_combined_channels_keep_the_named_spatial_pixels(tmp_path, decoded, axes):
    base = np.arange(4 * 2 * 3 * 6 * 7, dtype=np.uint16).reshape(4, 2, 3, 6, 7)
    canonical_axes = "ZCSYX" if "C" in axes else "ZSYX"
    canonical = base if "C" in axes else base[:, 0]
    source = canonical.transpose([canonical_axes.index(a) for a in axes])
    path = tmp_path / "samples.tif"
    tifffile.imwrite(path, source, photometric="rgb", planarconfig="contig" if axes.endswith("S") else "separate",
                     metadata={"axes": axes})
    channel = 4 if "C" in axes else 1  # C-major, sample-minor: C=1/S=1 or C=0/S=1
    got = adapter._stack(path, channel=channel)
    assert np.array_equal(got, base[:, 1 if "C" in axes else 0, 1])
    assert len(decoded) == 4, "decode only the chosen channel's four planes"
    preview, why = adapter._preview(path)
    assert not why and np.array_equal(preview, base[2, 0, 0])


def test_each_series_has_its_own_voxel_sizes_and_axes(tmp_path):
    path = tmp_path / "two.ome.tif"
    with tifffile.TiffWriter(path, ome=True) as w:
        w.write(np.zeros((4, 6, 7), np.uint8),
                metadata={"axes": "ZYX", "PhysicalSizeX": 1.0, "PhysicalSizeY": 1.0, "PhysicalSizeZ": 2.0})
        w.write(np.zeros((3, 2, 8, 9), np.uint16),
                metadata={"axes": "ZCYX", "PhysicalSizeX": 0.5, "PhysicalSizeY": 0.5, "PhysicalSizeZ": 5.0})
    out = adapter.inspect({"input": str(path), "output": str(tmp_path / "seen")})
    first, second = out["series"]
    assert first["voxel_um"] == [2.0, 1.0, 1.0] and first["channels"] == 1 and first["shape"] == [4, 6, 7]
    assert second["voxel_um"] == [5.0, 0.5, 0.5] and second["channels"] == 2 and second["shape"] == [3, 8, 9]
    assert out["voxel_um"] is None, "two series: no one scale for the file"
    json.dumps(out)


def test_output_picture_reads_only_the_six_displayed_planes(tmp_path, decoded):
    result = tmp_path / "brainreg"
    result.mkdir()
    image = np.arange(8 * 16 * 18, dtype=np.uint16).reshape(8, 16, 18)
    tifffile.imwrite(result / "downsampled.tiff", image, photometric="minisblack")
    tifffile.imwrite(result / "boundaries.tiff", image % 7 == 0, photometric="minisblack")
    assert adapter._picture(result) == "brainreg/picture.png"
    assert len(decoded) == 6 and (result / "picture.png").is_file()


@pytest.mark.parametrize("axes", ["ZYX", "YXZ", "ZXY"])
def test_registration_normalizes_pixels_and_declared_orientation_together(tmp_path, monkeypatch, axes):
    import sys
    from types import ModuleType

    canonical = np.arange(5 * 6 * 7, dtype=np.uint16).reshape(5, 6, 7)
    source = np.transpose(canonical, ["ZYX".index(a) for a in axes])
    path = tmp_path / "source.tif"
    tifffile.imwrite(path, source, photometric="minisblack", metadata={"axes": axes})
    seen = {}

    class EngineCalled(Exception):
        pass

    def engine():
        seen["args"] = sys.argv[:]
        seen["pixels"] = tifffile.imread(sys.argv[1])
        raise EngineCalled  # stop at the external engine boundary; this is not a registration quality proof

    cli = ModuleType("brainreg.core.cli")
    cli.main = engine
    monkeypatch.setitem(sys.modules, "brainreg.core.cli", cli)
    monkeypatch.setattr(sys, "argv", sys.argv[:])
    monkeypatch.setenv("BRAINGLOBE_CONFIG_DIR", "unused-before-dispatch")
    declared = "".join({"Z": "p", "Y": "s", "X": "l"}[a] for a in axes)
    with pytest.raises(EngineCalled):
        adapter.register({"input": str(path), "output": str(tmp_path / "out"), "voxel_um": [5, 2, 1],
                          "orientation": declared, "data": {"allen-mouse-25um": str(tmp_path / "atlas")}})
    assert np.array_equal(seen["pixels"], canonical), "the normalized file must preserve named coordinates"
    argv = seen["args"]
    assert argv[argv.index("--orientation") + 1] == "psl", "orientation must follow the same axis permutation"
    assert argv[argv.index("-v") + 1:argv.index("-v") + 4] == ["5", "2", "1"]
