"""MT5 (ticket 59): BigDataViewer / Huygens HDF5 through the public reader interface, and its updates through the shared
maintenance lifecycle.

Synthetic evidence only: the folders are written here with h5py in the published BigDataViewer layout (XML + HDF5,
s00/resolutions, s00/subdivisions, t00000/s00/<level>/cells); no real Huygens export is read. They are read through a
real selected-folder grant (env_worker/source_directory.py). The maintenance part is a fixture integration (the
dark-spots entry carrying the example's bdv.py and a sample folder), installed with a stand-in uv as
tests/test_maintenance.py; the napari host itself isn't run here."""
import importlib.util
import io
from pathlib import Path

import h5py
import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import channels, env_runner
from orchestrator.maintenance import discover, pins, policy, prepare, status
from orchestrator.scientific_projects import ProjectStore
from tests.test_channels import _git, home  # noqa: F401 - `home` is the fixture
from tests.test_env_runner import ADAPTER
from tests.test_maintenance import KEY, version
from tests.test_scientific_directories import granted, transfer

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "beta-scientific-tools" / "bdv-hdf5-reader"
spec = importlib.util.spec_from_file_location("example_bdv", EXAMPLE / "bdv.py")
bdv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bdv)

Z, Y, X = 8, 24, 32
VOXEL = (0.25, 0.25, 1.0)  # x y z, µm: anisotropic, as most confocal stacks
FACTORS = [(1, 1, 1), (2, 2, 1), (4, 4, 2)]


def stack(t: int = 0) -> np.ndarray:
    """Distinct uint16 values per voxel, some above 32767 (BigDataViewer's signed storage must give them back)."""
    z, y, x = np.indices((Z, Y, X))
    return (40000 + 1000 * t + 300 * z + 7 * y + x).astype(np.uint16)


def xml(h5_name: str = "dataset.h5", *, loader: str = "bdv.hdf5", kind: str = "relative", timepoints: int = 2) -> bytes:
    vx, vy, vz = VOXEL
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<SpimData version="0.2">
  <BasePath type="relative">.</BasePath>
  <SequenceDescription>
    <ImageLoader format="{loader}"><hdf5 type="{kind}">{h5_name}</hdf5></ImageLoader>
    <ViewSetups>
      <ViewSetup><id>0</id><name>GFP</name><size>{X} {Y} {Z}</size>
        <voxelSize><unit>µm</unit><size>{vx} {vy} {vz}</size></voxelSize></ViewSetup>
    </ViewSetups>
    <Timepoints type="range"><first>0</first><last>{timepoints - 1}</last></Timepoints>
  </SequenceDescription>
  <ViewRegistrations>
    <ViewRegistration timepoint="0" setup="0">
      <ViewTransform type="affine"><affine>{vx} 0 0 0 0 {vy} 0 0 0 0 {vz} 0</affine></ViewTransform>
    </ViewRegistration>
  </ViewRegistrations>
</SpimData>""".encode()


def h5(levels=FACTORS, timepoints: int = 2) -> bytes:
    """The BigDataViewer HDF5 layout, written with h5py: values stored signed, as BigDataViewer stores uint16."""
    out = io.BytesIO()
    with h5py.File(out, "w") as f:
        f["s00/resolutions"] = np.array(levels, "f8")
        f["s00/subdivisions"] = np.array([(16, 16, 4)] * len(levels), "i4")
        for t in range(timepoints):
            full = stack(t)
            for n, (fx, fy, fz) in enumerate(levels):
                f.create_dataset(f"t{t:05d}/s00/{n}/cells", data=full[::fz, ::fy, ::fx].view(np.int16), chunks=True)
    return out.getvalue()


def folder(tmp_path, files: dict[str, bytes]):
    """A real selected-folder grant over these files (the store's own transfer and grant)."""
    store = ProjectStore(tmp_path / "projects")
    grants, source = granted(store, transfer(store, files))
    return grants, source


@pytest.fixture
def dataset(tmp_path):
    grants, source = folder(tmp_path, {"dataset.xml": xml(), "dataset.h5": h5()})
    yield source
    grants.close()


def test_the_xml_and_hdf5_layout_are_read_with_each_levels_own_scale(dataset):
    described = bdv.describe(dataset)
    assert described["hdf5"] == "dataset.h5" and described["timepoints"] == [0, 1]
    assert described["setups"] == [{"id": 0, "name": "GFP", "size": [X, Y, Z], "voxel": list(VOXEL), "unit": "µm"}]
    close, levels = bdv.open_levels(dataset, described)
    try:
        pyramid = levels[(0, 0)]
        assert [one["shape"] for one in pyramid] == [[Z, Y, X], [Z, Y // 2, X // 2], [Z // 2, Y // 4, X // 4]]
        assert [one["scale"] for one in pyramid] == [[1.0, 0.25, 0.25], [1.0, 0.5, 0.5], [2.0, 1.0, 1.0]]
        assert [one["measurable"] for one in pyramid] == [True, False, False], "a downsampled level is never measured"
        assert pyramid[0]["data"].dtype == np.uint16
        assert np.array_equal(pyramid[0]["data"][3], stack(0)[3]), "values above 32767 come back exact"
        assert np.array_equal(levels[(1, 0)][0]["data"][...], stack(1))
        assert bdv.calibration(pyramid[0]) == {"axes": ["z", "y", "x"], "units": ["µm"] * 3,
                                               "affine": [[1.0, 0, 0, 0], [0, 0.25, 0, 0], [0, 0, 0.25, 0]]}
    finally:
        close()


def test_levels_are_read_lazily_through_the_grant(tmp_path, monkeypatch):
    from env_worker import source_directory

    grants, source = folder(tmp_path, {"dataset.xml": xml(), "dataset.h5": h5()})
    read = []
    real = source_directory.Entry.read
    monkeypatch.setattr(source_directory.Entry, "read", lambda self, size=-1: read.append(size) or real(self, size))
    described = bdv.describe(source)
    close, levels = bdv.open_levels(source, described)
    opened = sum(n for n in read if n > 0)
    plane = levels[(0, 0)][0]["data"][2]
    after = sum(n for n in read if n > 0)
    close()
    grants.close()
    total = len(h5())
    assert opened < total / 2, f"opening read {opened} of {total} bytes: the levels weren't read up front"
    assert after > opened and plane.shape == (Y, X), "a plane is read when it is asked for"


def test_v1_reads_level_0_only(dataset):
    close, levels = bdv.open_levels(dataset, bdv.describe(dataset), pyramid=False)
    close()
    assert [one["level"] for one in levels[(0, 0)]] == [0]


@pytest.mark.parametrize("files,words", [
    ({"notes.txt": b"x"}, "no .xml file is in it"),
    ({"a.xml": xml(), "b.xml": xml(), "dataset.h5": b""}, "more than one dataset XML"),
    ({"dataset.xml": xml(), "Dataset.H5": b"x"}, "names dataset.h5, which isn't in the folder (it has Dataset.H5)"),
    ({"dataset.xml": xml("../elsewhere.h5"), "dataset.h5": b""}, "outside the chosen folder"),
    ({"dataset.xml": xml(kind="absolute"), "dataset.h5": b""}, "outside the chosen folder"),
    ({"dataset.xml": xml(loader="bdv.n5"), "dataset.h5": b""}, "bdv.n5 image loader; this reader reads bdv.hdf5 only"),
    ({"dataset.xml": b"<SpimData><unclosed>", "dataset.h5": b""}, "isn't readable XML"),
])
def test_a_missing_or_misnamed_companion_is_said_never_a_crash(tmp_path, files, words):
    grants, source = folder(tmp_path, files)
    try:
        with pytest.raises(bdv.Intake, match=words.replace("(", r"\(").replace(")", r"\)")):
            bdv.describe(source)
    finally:
        grants.close()


def test_an_hdf5_without_the_bdv_layout_or_a_level_is_said(tmp_path):
    grants, source = folder(tmp_path, {"dataset.xml": xml(), "dataset.h5": b"not hdf5 at all"})
    with pytest.raises(bdv.Intake, match="isn't an HDF5 file"):
        bdv.open_levels(source, bdv.describe(source))
    grants.close()
    one = io.BytesIO()
    with h5py.File(one, "w") as f:
        f["s00/resolutions"] = np.array(FACTORS, "f8")
        f["t00000/s00/0/cells"] = stack().view(np.int16)
    grants, source = folder(tmp_path / "b", {"dataset.xml": xml(timepoints=1), "dataset.h5": one.getvalue()})
    with pytest.raises(bdv.Intake, match="has no t00000/s00/1/cells"):
        bdv.open_levels(source, bdv.describe(source))
    grants.close()


def test_the_example_declares_a_format_3_directory_reader_the_contract_accepts():
    import tomllib

    from gpu_worker import bundle_contract

    member = tomllib.loads((EXAMPLE / "model.toml").read_text())["members"][0]
    bundle_contract._reader_v3(member["reader"])
    assert member["reader"]["kind"] == "directory" and set(member["reader"]["entries"]) == {"*.xml", "*.h5"}


# --- MT5 through maintenance: v1 level 0, v2 the pyramid, v3 broken -------------------------------------------------
READER = '''

def _bdv():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("entry_bdv", Path(__file__).with_name("bdv.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Folder(dict):
    """The entry's own sample folder as the reader takes a grant: names, and a seekable stream per name."""
    def open(self, key):
        import io
        stream = io.BytesIO(self[key])
        stream.size = len(self[key])
        return stream


def read(folder):
    """What this version gives for a BigDataViewer folder: {{(t, setup): [level shapes]}} and level 0's values."""
    bdv = _bdv()
    described = bdv.describe(folder)
    close, levels = bdv.open_levels(folder, described, pyramid={pyramid})
    try:
        return ({{k: [one["shape"] for one in v] for k, v in levels.items()}},
                {{k: np.asarray(v[0]["data"][...]){broken} for k, v in levels.items()}})
    finally:
        close()


def _proof_of_reading():
    """Its own proof: the shipped sample reads back as its declared values (sample/expected.npy)."""
    from pathlib import Path
    here = Path(__file__).with_name("sample")
    folder = _Folder({{p.name: p.read_bytes() for p in here.iterdir() if p.suffix in (".xml", ".h5")}})
    _, values = read(folder)
    if not np.array_equal(values[(0, 0)], np.load(here / "expected.npy")):
        raise ValueError("its sample didn't read back as its declared values")


_segment = segment


def segment(picture, params):
    _proof_of_reading()
    return _segment(picture, params)
'''
V1 = ADAPTER + READER.format(pyramid="False", broken="")
V2 = ADAPTER + READER.format(pyramid="True", broken="")
V3 = ADAPTER + READER.format(pyramid="True", broken=".T")  # a broken update: level 0 read transposed


def publish(home, v: str, adapter: str) -> None:
    folder = home / "upstream" / "catalog" / "models" / KEY
    sample = folder / "sample"
    sample.mkdir(parents=True, exist_ok=True)
    (folder / "model.toml").write_text(version(v))
    (folder / "env.lock").write_text("numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
    (folder / "adapter.py").write_text(adapter)
    (folder / "bdv.py").write_bytes((EXAMPLE / "bdv.py").read_bytes())  # the example's own reader module
    (sample / "dataset.xml").write_bytes(xml(timepoints=1))
    (sample / "dataset.h5").write_bytes(h5(timepoints=1))
    np.save(sample / "expected.npy", stack(0))
    _git("add", ".", cwd=home / "upstream")
    _git("commit", "-q", "-m", f"bdv reader {v}", cwd=home / "upstream")


def reader_of(generation_folder):
    path = generation_folder / "entry" / KEY / "adapter.py"
    one = importlib.util.spec_from_file_location(f"reader_{generation_folder.name}", path)
    module = importlib.util.module_from_spec(one)
    one.loader.exec_module(module)
    return module


def test_reader_updates_go_through_maintenance_and_a_saved_project_keeps_v1(home, monkeypatch, tmp_path):
    from tests.test_env_runner import install

    monkeypatch.setattr(discover, "NEWEST_S", 0)
    publish(home, "0.1", V1)
    channels.update()
    assert install(home, monkeypatch)["state"] == "done", "v1 proves by reading its sample"
    m = catalog.get(KEY)
    v1 = env_runner.selected(m).folder
    pins.add(KEY, v1.name, "session", "project-opened-with-v1")  # a saved Project opened with v1
    policy.record(m, "auto", by="you", how="screen")
    grants, source = folder(tmp_path, {"dataset.xml": xml(), "dataset.h5": h5()})
    shapes, _ = reader_of(v1).read(source)
    assert shapes[(0, 0)] == [[Z, Y, X]], "v1 reads level 0 only"

    publish(home, "0.2", V2)
    row = prepare.run(KEY)
    assert row["state"] == "applied" and env_runner.selected(catalog.get(KEY)).m.version == "dark-spots 0.2", row
    v2 = env_runner.selected(catalog.get(KEY)).folder
    shapes, values = reader_of(v2).read(source)
    assert len(shapes[(0, 0)]) == 3 and np.array_equal(values[(1, 0)], stack(1)), "v2 adds the pyramid"

    publish(home, "0.3", V3)
    row = prepare.run(KEY)
    assert row["state"] == "failed" and "declared values" in (row.get("why") or row["says"]), row
    assert env_runner.selected(catalog.get(KEY)).folder == v2, "a broken v3 fails its proof and v2 is kept"
    assert status.of(catalog.get(KEY))["state"] == "failed"

    kept = env_runner.generation(catalog.get(KEY), v1.name)
    assert kept is not None and kept.m.version == "dark-spots 0.1", "the saved Project keeps opening with v1"
    shapes, _ = reader_of(kept.folder).read(source)
    assert shapes[(0, 0)] == [[Z, Y, X]]
    grants.close()


@pytest.mark.parametrize('kind', ['external-link', 'external-group', 'soft-link', 'virtual', 'external-storage'])
def test_unbrokered_hdf5_dependencies_are_refused_before_dataset_reads(tmp_path, kind):
    outside = tmp_path / 'outside.h5'
    with h5py.File(outside, 'w') as remote:
        remote['pixels'] = stack().view(np.int16)
        remote['setup/resolutions'] = np.array(FACTORS, 'f8')
        remote['setup/subdivisions'] = np.array([(16, 16, 4)] * len(FACTORS), 'i4')
    content = io.BytesIO(h5())
    path = 't00000/s00/0/cells'
    with h5py.File(content, 'r+') as current:
        if kind == 'external-group':
            del current['s00']
            current['s00'] = h5py.ExternalLink(str(outside), '/setup')
        else:
            del current[path]
            if kind == 'external-link':
                current[path] = h5py.ExternalLink(str(outside), '/pixels')
            elif kind == 'soft-link':
                current[path] = h5py.SoftLink('/t00000/s00/1/cells')
            elif kind == 'virtual':
                layout = h5py.VirtualLayout(shape=(Z, Y, X), dtype='i2')
                layout[:] = h5py.VirtualSource(str(outside), 'pixels', shape=(Z, Y, X))
                current.create_virtual_dataset(path, layout)
            else:
                current.create_dataset(path, shape=(Z, Y, X), dtype='i2',
                                       external=[(str(tmp_path/'outside.raw'), 0, h5py.h5f.UNLIMITED)])
    grants, source = folder(tmp_path, {'dataset.xml': xml(), 'dataset.h5': content.getvalue()})
    try:
        with pytest.raises(bdv.Intake, match='dependency|link|storage'):
            close, _ = bdv.open_levels(source, bdv.describe(source))
            close()
    finally:
        grants.close()


def test_resolution_metadata_is_bounded_before_hdf5_array_allocation(tmp_path, monkeypatch):
    content = io.BytesIO(h5())
    with h5py.File(content, 'r+') as current:
        del current['s00/resolutions']
        current.create_dataset('s00/resolutions', shape=(1 << 30, 3), dtype='f8', chunks=(1, 3))
    grants, source = folder(tmp_path, {'dataset.xml': xml(), 'dataset.h5': content.getvalue()})
    arrays = []
    original = h5py.Dataset.__array__
    def intercepted(dataset, *args, **kwargs):
        if dataset.shape[0] == 1 << 30:
            arrays.append(dataset.shape)
            raise AssertionError('unbounded metadata reached allocation')
        return original(dataset, *args, **kwargs)
    monkeypatch.setattr(h5py.Dataset, '__array__', intercepted)
    try:
        with pytest.raises(bdv.Intake, match='metadata'):
            bdv.open_levels(source, bdv.describe(source))
        assert arrays == []
    finally:
        grants.close()


def test_xml_timepoint_count_is_refused_before_range_materialization(tmp_path, monkeypatch):
    text = xml().replace(b'<last>1</last>', b'<last>1000000000</last>')
    grants, source = folder(tmp_path, {'dataset.xml': text, 'dataset.h5': h5()})
    def bounded_range(first, last):
        assert last-first <= 4096, 'unbounded timepoint range reached allocation'
        return range(first, last)
    monkeypatch.setattr(bdv, 'range', bounded_range, raising=False)
    try:
        with pytest.raises(bdv.Intake, match='timepoint'):
            bdv.describe(source)
    finally:
        grants.close()


@pytest.mark.parametrize('setups,timepoints', [(257, 1), (65, 65)])
def test_xml_aggregate_views_have_a_bounded_cardinality(tmp_path, setups, timepoints):
    import copy
    root = bdv.ET.fromstring(xml(timepoints=timepoints))
    owner = root.find('SequenceDescription/ViewSetups')
    template = copy.deepcopy(owner[0]); owner.clear()
    for n in range(setups):
        node = copy.deepcopy(template); node.find('id').text = str(n); owner.append(node)
    grants, source = folder(tmp_path, {'dataset.xml': bdv.ET.tostring(root), 'dataset.h5': h5()})
    try:
        with pytest.raises(bdv.Intake, match='metadata'):
            bdv.describe(source)
    finally:
        grants.close()


def test_setup_timepoint_level_product_is_bounded_before_reading_pixels(tmp_path):
    content = io.BytesIO()
    with h5py.File(content, 'w') as f:
        f['s00/resolutions'] = np.ones((256, 3))
    grants, source = folder(tmp_path, {'dataset.xml': xml(timepoints=17), 'dataset.h5': content.getvalue()})
    try:
        with pytest.raises(bdv.Intake, match='setup/timepoint/level metadata'):
            bdv.open_levels(source, bdv.describe(source))
    finally:
        grants.close()


@pytest.mark.parametrize('order', ['<', '>'])
@pytest.mark.parametrize('storage', ['i2', 'u2'])
def test_bdv_signed_encoding_preserves_native_values_and_file_byte_order(tmp_path, order, storage):
    """BDV uint16 semantics over either HDF5 signed-bit encoding, including non-native order."""
    values = np.array([0, 1, 255, 256, 32767, 32768, 40000, 65535], dtype=order+'u2').reshape(2, 2, 2)
    raw = io.BytesIO()
    with h5py.File(raw, 'w') as f:
        f['s00/resolutions'] = [[1., 1., 1.]]
        f.create_dataset('t00000/s00/0/cells', data=values.view(order+storage), dtype=order+storage)
    grants, source = folder(tmp_path, {'dataset.xml': xml(timepoints=1).replace(b'<size>32 24 8</size>',b'<size>2 2 2</size>'), 'dataset.h5': raw.getvalue()})
    try:
        close, levels = bdv.open_levels(source, bdv.describe(source))
        try:
            data = levels[(0, 0)][0]['data']
            assert data.dtype.str == order+'u2'
            whole = np.asarray(data)
            assert whole.dtype.str == order+'u2'
            assert whole.tolist() == [[[0, 1], [255, 256]], [[32767, 32768], [40000, 65535]]]
            assert whole.tobytes() == values.tobytes()
            region = data[1, :, 1]
            assert region.dtype.str == order+'u2' and region.tolist() == [32768, 65535]
            assert region.tobytes() == values[1, :, 1].tobytes()
            scalar = data[1, 1, 0]
            assert scalar.dtype.str == order+'u2' and scalar.item() == 40000
            assert scalar.tobytes() == values[1:2, 1:2, 0:1].tobytes()
        finally:
            close()
    finally:
        grants.close()
