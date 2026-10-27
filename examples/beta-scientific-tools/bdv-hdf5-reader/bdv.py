"""BigDataViewer HDF5 (the layout Huygens and Fiji's BigDataViewer write) read through a selected-folder grant.

The folder holds the dataset's XML (SpimData) and the HDF5 file it names. Everything is read through the grant's own
streams (`source.open(name)`): no path outside the folder, no file opened by name, no network. What is read:

- the XML: each ViewSetup (id, name, size in x y z, voxel size and its unit), the timepoints, each ViewRegistration's
  affine (kept as metadata), and the HDF5 file its `bdv.hdf5` ImageLoader names (relative to the folder only);
- the HDF5 layout: `s<NN>/resolutions` (each level's downsampling in x y z), `s<NN>/subdivisions` (its chunking) and
  `t<NNNNN>/s<NN>/<level>/cells` (z, y, x). BigDataViewer stores unsigned 16-bit values in a signed dataset; they are
  read back as uint16, value for value, retaining the HDF5 byte order for full, regional and scalar reads.

Levels are lazy (`Level`: nothing is read until a plane is asked for), each with its own scale (the voxel size times
its downsampling). Only level 0 is ever a measurement input; the others are for display (`measurable`). A missing or
misnamed companion file, or a layout this doesn't know, is an `Intake` problem said in words, never a crash.

Not claimed: real Huygens exports. This reads the published BigDataViewer layout; its evidence is synthetic files
written with h5py in that layout (services/tests/test_bdv_reader.py).
"""
from __future__ import annotations

import math
import posixpath
import re
import xml.etree.ElementTree as ET

import numpy as np

UNITS = {"µm": "µm", "μm": "µm", "um": "µm", "micron": "µm", "micrometer": "µm", "nm": "nm", "mm": "mm"}
MAX_SETUPS = 256
MAX_TIMEPOINTS = 4096
MAX_LEVEL_VIEWS = 4096
MAX_XML = 1 << 20  # the XML is metadata: a megabyte is far more than any real one


class Intake(ValueError):
    """The folder can't be read as a BigDataViewer dataset; the words say what is wrong and what to choose."""


def _xml_name(source) -> str:
    names = sorted(k for k in source if "/" not in k and k.lower().endswith(".xml"))
    if not names:
        raise Intake("choose the folder that holds the BigDataViewer XML and its HDF5 file: no .xml file is in it")
    if len(names) > 1:
        raise Intake(f"the folder holds more than one dataset XML ({', '.join(names)}): choose a folder with one")
    return names[0]


def _text(node, path: str, name: str) -> str:
    found = node.find(path)
    if found is None or not (found.text or "").strip():
        raise Intake(f"{name} has no {path.split('/')[-1]}: not a BigDataViewer dataset XML")
    return found.text.strip()


def _numbers(text: str, n: int, what: str) -> list[float]:
    try:
        values = [float(v) for v in text.split()]
    except ValueError:
        values = []
    if len(values) != n or not all(np.isfinite(values)):
        raise Intake(f"its {what} isn't {n} numbers")
    return values


def describe(source) -> dict:
    """The dataset as its XML and HDF5 layout declare it, without reading any pixels: {xml, hdf5, setups, timepoints,
    levels: {setup: [{level, factors (x y z), chunks, shape (z y x), scale (z y x), unit, measurable}]}}."""
    name = _xml_name(source)
    with source.open(name) as raw:
        if raw.size > MAX_XML:
            raise Intake(f"{name} is larger than a dataset XML can be ({raw.size} bytes)")
        text = raw.read(raw.size)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise Intake(f"{name} isn't readable XML ({error})") from error
    loader = root.find("SequenceDescription/ImageLoader")
    if loader is None:
        raise Intake(f"{name} names no image loader: not a BigDataViewer dataset XML")
    if loader.get("format") != "bdv.hdf5":
        raise Intake(f"{name} names a {loader.get('format') or 'unnamed'} image loader; this reader reads bdv.hdf5 only")
    hdf5 = loader.find("hdf5")
    if hdf5 is None or not (hdf5.text or "").strip():
        raise Intake(f"{name} names no HDF5 file")
    target = hdf5.text.strip()
    if hdf5.get("type", "relative") != "relative" or target.startswith("/") or ".." in target.split("/"):
        raise Intake(f"{name} names {target}, outside the chosen folder: reads stay within it")
    target = posixpath.normpath(target)
    if target not in source:
        here = ", ".join(sorted(k for k in source if k.lower().endswith((".h5", ".hdf5")))) or "no HDF5 file"
        raise Intake(f"{name} names {target}, which isn't in the folder (it has {here}): choose the folder as it was "
                     "exported, or rename the file back")
    setups = []
    nodes = root.findall("SequenceDescription/ViewSetups/ViewSetup")
    if len(nodes) > MAX_SETUPS:
        raise Intake(f'the XML exceeds this reader’s {MAX_SETUPS}-setup metadata allowance')
    for node in nodes:
        sid = int(_text(node, "id", name))
        unit = (node.findtext("voxelSize/unit") or "").strip()
        setups.append({"id": sid, "name": (node.findtext("name") or f"setup {sid}").strip(),
                       "size": [int(v) for v in _numbers(_text(node, "size", name), 3, "setup size")],
                       "voxel": _numbers(_text(node, "voxelSize/size", name), 3, "voxel size"),
                       "unit": UNITS.get(unit, unit)})
    if not setups:
        raise Intake(f"{name} declares no view setups")
    tp = root.find("SequenceDescription/Timepoints")
    if tp is not None and tp.get("type") == "range":
        first, last = int(_text(tp, "first", name)), int(_text(tp, "last", name))
        if first < 0 or last < first or last - first + 1 > MAX_TIMEPOINTS:
            raise Intake(f'the timepoint range needs 1 to {MAX_TIMEPOINTS} nonnegative identifiers')
        timepoints = list(range(first, last + 1))
    elif tp is not None and tp.get("type") == "pattern":
        timepoints = []
        for token in re.finditer(r'[^,\s]+', _text(tp, "integerpattern", name)):
            if len(timepoints) >= MAX_TIMEPOINTS:
                raise Intake(f'the timepoint pattern exceeds its {MAX_TIMEPOINTS}-item allowance')
            timepoints.append(int(token.group()))
        if not timepoints or min(timepoints) < 0 or len(set(timepoints)) != len(timepoints):
            raise Intake('the timepoint pattern needs distinct nonnegative identifiers')
    else:
        timepoints = [0]
    if len(setups) * len(timepoints) > MAX_LEVEL_VIEWS:
        raise Intake(f'the setup/timepoint metadata exceeds {MAX_LEVEL_VIEWS} views')
    registrations = {}
    for node in root.findall("ViewRegistrations/ViewRegistration"):
        affine = node.findtext("ViewTransform/affine")
        if affine:
            registrations[(int(node.get("timepoint", 0)), int(node.get("setup", 0)))] = _numbers(affine, 12, "affine")
    return {"xml": name, "hdf5": target, "setups": setups, "timepoints": timepoints, "registrations": registrations}


class Level:
    """One resolution level of one setup and timepoint, read on demand: shape (z, y, x), dtype, and slicing reads only
    the chunks asked for (h5py)."""

    def __init__(self, dataset, unsigned: bool):
        self._dataset = dataset
        self._unsigned = unsigned
        self.shape = tuple(dataset.shape)
        self.ndim = len(self.shape)
        self.dtype = np.dtype("uint16").newbyteorder(dataset.dtype.byteorder) if unsigned else dataset.dtype

    @property
    def size(self):
        return math.prod(self.shape)

    def __getitem__(self, key):
        # h5py returns native-order NumPy scalars for fully indexed reads.
        # Restore the file dtype before reinterpreting BDV's signed storage bits.
        values = np.asarray(self._dataset[key], dtype=self._dataset.dtype)
        return values.view(self.dtype) if self._unsigned else values

    def __array__(self, dtype=None, copy=None):
        values = self[...]
        return values.astype(dtype) if dtype is not None else values


def _dataset(h5, path, *, optional=False):
    """Walk only hard links in the granted file; HDF5 must never open an unbrokered dependency."""
    import h5py

    parent = h5
    parts = path.split('/')
    for index, part in enumerate(parts):
        link = parent.get(part, getlink=True)
        if link is None:
            if optional:
                return None
            raise Intake(f'the HDF5 dataset has no {path}: not the BigDataViewer layout')
        if not isinstance(link, h5py.HardLink):
            raise Intake(f'{path} uses a link dependency outside this reader’s granted-file contract')
        item = parent[part]
        if index < len(parts) - 1:
            if not isinstance(item, h5py.Group):
                raise Intake(f'{path} does not traverse HDF5 groups')
            parent = item
        elif not isinstance(item, h5py.Dataset):
            raise Intake(f'{path} is not an HDF5 dataset')
        else:
            if item.is_virtual or item.external:
                raise Intake(f'{path} uses virtual or external storage; choose a self-contained HDF5 export')
            return item


def _metadata(dataset, dtype):
    if dataset.ndim != 2 or dataset.shape[1] != 3 or not 1 <= dataset.shape[0] <= 256 or dataset.dtype.kind not in 'fiu':
        raise Intake('the HDF5 level metadata needs at most 256 numeric x-y-z rows')
    return np.asarray(dataset, dtype=dtype)


def open_levels(source, described: dict, *, pyramid: bool = True):
    """(close, {(timepoint, setup): [level dicts with "data": Level]}). `pyramid=False`: level 0 only. The caller keeps
    the file open while the levels are used, then calls close()."""
    import h5py

    stream = source.open(described["hdf5"])
    try:
        h5 = h5py.File(stream, "r")
    except OSError as error:
        stream.close()
        raise Intake(f"{described['hdf5']} isn't an HDF5 file this reader can open ({error})") from error
    try:
        out = {}
        level_views = 0
        for setup in described["setups"]:
            s = f"s{setup['id']:02d}"
            factors = _metadata(_dataset(h5, f"{s}/resolutions"), "f8")
            subdivisions = _dataset(h5, f"{s}/subdivisions", optional=True)
            chunks = _metadata(subdivisions, "i8") if subdivisions is not None else None
            if factors.ndim != 2 or factors.shape[1] != 3 or not len(factors) or not np.all(np.isfinite(factors)) or not np.all(factors > 0):
                raise Intake(f"{described['hdf5']}'s {s}/resolutions isn't one (x y z) row per level")
            level_views += len(described['timepoints']) * (len(factors) if pyramid else 1)
            if level_views > MAX_LEVEL_VIEWS:
                raise Intake(f'the setup/timepoint/level metadata exceeds {MAX_LEVEL_VIEWS} views')
            for t in described["timepoints"]:
                levels = []
                for n in range(len(factors) if pyramid else 1):
                    path = f"t{t:05d}/{s}/{n}/cells"
                    data = _dataset(h5, path)
                    fx, fy, fz = factors[n]
                    vx, vy, vz = setup["voxel"]
                    levels.append({"level": n, "factors": [fx, fy, fz],
                                   "chunks": chunks[n].tolist() if chunks is not None and n < len(chunks) else None,
                                   "shape": list(data.shape), "scale": [vz * fz, vy * fy, vx * fx], "unit": setup["unit"],
                                   "measurable": n == 0,  # a downsampled level is for display, never measured
                                   "data": Level(data, data.dtype.kind == "i" and data.dtype.itemsize == 2)})
                out[(t, setup["id"])] = levels

        def close():
            h5.close()
            stream.close()
        return close, out
    except Exception:
        h5.close()
        stream.close()
        raise


def calibration(level: dict) -> dict:
    """A level's calibration in the labels' own shape (axes z y x, units, affine rows): its voxel size, no offset."""
    z, y, x = level["scale"]
    return {"axes": ["z", "y", "x"], "units": [level["unit"]] * 3,
            "affine": [[z, 0, 0, 0], [0, y, 0, 0], [0, 0, x, 0]]}
