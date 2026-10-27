"""The first launch's download size (packaging/macos/runtime_download.py): what the build measures for the launcher's
question (SetupWords.swift consent) comes from the lockfile's own sizes for the wheels really installed, never a number
typed by hand. No network: the build runs the measurement itself."""
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MACOS = ROOT / "packaging/macos"
spec = importlib.util.spec_from_file_location("runtime_download", MACOS / "runtime_download.py")
rd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rd)


def lock(*packages):
    return {"package": list(packages)}


def entry(name, version, wheels, sdist=None, registry="https://pypi.org/simple"):
    return {"name": name, "version": version, "source": {"registry": registry},
            "wheels": [{"url": f"https://files.pythonhosted.org/x/{f}", "size": s} for f, s in wheels],
            **({"sdist": {"url": "https://files.pythonhosted.org/x/s.tar.gz", "size": sdist}} if sdist else {})}


def test_wheel_tags_expand_compressed_tag_sets():
    assert rd.wheel_tags("six-1.17.0-py2.py3-none-any.whl") == {"py2-none-any", "py3-none-any"}
    assert rd.wheel_tags("torch-2.14.1-cp313-cp313-macosx_14_0_arm64.whl") == {"cp313-cp313-macosx_14_0_arm64"}


def test_the_installed_wheel_is_the_one_counted_not_the_largest():
    scipy = entry("scipy", "1.18.1", [("scipy-1.18.1-cp313-cp313-macosx_12_0_arm64.whl", 28_715_106),
                                      ("scipy-1.18.1-cp313-cp313-macosx_14_0_arm64.whl", 20_456_846),
                                      ("scipy-1.18.1-cp314-cp314-macosx_14_0_arm64.whl", 20_466_839)])
    six = entry("six", "1.17.0", [("six-1.17.0-py2.py3-none-any.whl", 11_050)])
    torch = entry("torch", "2.14.1", [("torch-2.14.1-cp313-cp313-macosx_14_0_arm64.whl", 127_315_286)])
    built = entry("fill-voids", "2.1.2", [], sdist=1_000)
    dists = [{"name": "scipy", "version": "1.18.1", "tags": {"cp313-cp313-macosx_14_0_arm64"}},
             {"name": "six", "version": "1.17.0", "tags": {"py2-none-any", "py3-none-any"}},
             {"name": "torch", "version": "2.14.1", "tags": {"cp313-cp313-macosx_14_0_arm64"}},
             {"name": "fill-voids", "version": "2.1.2", "tags": {"cp313-cp313-macosx_26_0_arm64"}}]
    got = rd.sizes(lock(scipy, six, torch, built), dists)
    assert got["packages"] == 4
    assert got["packageBytes"] == 20_456_846 + 11_050 + 127_315_286 + 1_000  # a package built from source counts its sdist
    assert got["registries"] == ["pypi.org"]
    assert got["notable"] == ["PyTorch", "SciPy"]  # only names really in the set


def test_a_package_the_lockfile_does_not_size_stops_the_build():
    with pytest.raises(ValueError, match="mystery==1.0"):
        rd.sizes(lock(), [{"name": "mystery", "version": "1.0", "tags": {"py3-none-any"}}])


def test_installed_reads_names_versions_and_wheel_tags(tmp_path):
    info = tmp_path / "Open_CV_Python_Headless-5.0.0.93.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: opencv-python-headless\nVersion: 5.0.0.93\n")
    (info / "WHEEL").write_text("Wheel-Version: 1.0\nTag: cp37-abi3-macosx_13_0_arm64\n")
    assert rd.installed(tmp_path) == [{"name": "opencv-python-headless", "version": "5.0.0.93",
                                       "tags": {"cp37-abi3-macosx_13_0_arm64"}}]


def test_the_measurement_syncs_exactly_as_the_first_launch_does():
    swift = (MACOS / "HookeUI/main.swift").read_text()
    found = re.search(r'appendingPathComponent\("uv"\)\.path,\s*\[([^\]]*)\]', swift)
    assert found, "main.swift no longer runs uv sync as syncPython did"
    assert re.findall(r'"([^"]+)"', found.group(1)) == rd.SYNC


def test_the_build_measures_after_the_hash_and_before_the_app_is_assembled():
    build = (MACOS / "build.sh").read_text()
    measure = build.index("runtime_download.py")
    assert build.index("finalize_snapshot.py") < measure < build.index('ditto "$TREE" "$C/Resources/app"')
    assert build.index('"$TOOLS/uv/uv"') < measure  # the bundled uv, the one the app ships


def test_packaged_json_stays_outside_the_content_hash():
    # runtimeDownload is written after finalize_snapshot: a runtime's identity must not depend on it.
    source = (MACOS / "finalize_snapshot.py").read_text()
    assert "path == packaged_path" in source


def test_disk_is_rounded_so_a_rebuild_writes_the_same_packaged_json():
    assert rd.rounded_disk(1_153_000_000) == rd.rounded_disk(1_181_000_000) == 1_200_000_000
    assert rd.rounded_disk(10_000) == 100_000_000  # never "0.0 GB"
