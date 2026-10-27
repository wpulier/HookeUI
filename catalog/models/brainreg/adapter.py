"""brainreg for ObservationUI (runner python-env, a job: orchestrator/jobs.py). Runs only in this entry's own environment.

register(request) registers one volume to the Allen mouse atlas at 25 µm and writes brainreg's own outputs under
`<output>/brainreg/`: the atlas, its hemispheres and its boundaries in the volume's downsampled space (downsampled.tiff,
the volume as brainreg reoriented and downsampled it), the region volumes (volumes.csv) and the deformation field, and
`picture.png`: three slices of that downsampled volume with the atlas's boundaries drawn, in that same space. The
request names the volume (`input`), which 3D stack of it (`series`, `channel`, `time`: the spatial axes are taken in
z, y, x order), its voxel sizes in µm (z, y, x: `voxel_um`) and its orientation in BrainGlobe's three letters
(`orientation`); with `fixture` it registers brainreg's own sample brain instead (its proof). The atlas is read from the
folder the app gives (`data`), never fetched.

inspect(request) reads what a volume's file states (each series' axes, shape and type, channels and time points, and
its voxel sizes where the file states them for that series) and writes a preview of the first series' middle slice: one
page is decoded, never the series (`_plane`), and a plane past PREVIEW_MAX_BYTES gets no preview, said.

Reading is bounded (the review of 2026-10-13): a stack is read plane by plane, only the planes of the chosen channel and
time point, and refused before any pixel is read when it would hold more than its limit (`max_bytes`, default
STACK_MAX_BYTES); a file whose series is not stored a plane per page is read whole only when the whole fits that limit.
"""
import csv
import os
import sys
import zipfile
from pathlib import Path

ATLAS = "allen_mouse_25um"
OUTPUTS = {
    "registered_atlas": "registered_atlas.tiff",
    "registered_hemispheres": "registered_hemispheres.tiff",
    "boundaries": "boundaries.tiff",
    "downsampled": "downsampled.tiff",
    "downsampled_standard": "downsampled_standard.tiff",
    "volumes": "volumes.csv",
    "parameters": "brainreg.json",
}
REQUIRED = ("registered_atlas", "downsampled", "volumes")
PREVIEW_MAX_BYTES = 8 * 2**20  # plane + float conversion + three RGB tiles stay within the job startup allowance
STACK_MAX_BYTES = 8 * 2**30  # the chosen 3D stack, at most (brainreg holds more than this while it works)


def _fixture(request):
    """The sample brain brainreg's napari plugin offers: 270 TIFF slices stacked on the first axis, 50/40/40 µm, psl."""
    import numpy as np
    import tifffile
    from skimage.io import imread

    with zipfile.ZipFile(Path(request["files"]) / "test_brain.zip") as z:
        data = np.stack([imread(z.open(f"test_brain/image_{i:04d}.tif")) for i in range(270)])
    path = Path(request["output"]) / "input" / "sample_brain.tif"
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data)
    return str(path), [50, 40, 40], "psl"


SPATIAL = "ZYX"


def _series_facts(series, voxel=None):
    axes, shape = series.axes, series.shape
    count = {a: shape[axes.index(a)] for a in "CTS" if a in axes}
    spatial = [a for a in axes if a not in "CTS"]
    return {"axes": axes, "spatial": "".join(spatial), "shape": [int(shape[axes.index(a)]) for a in spatial],
            "dtype": str(series.dtype), "channels": int(count.get("C", 1) * count.get("S", 1)), "times": int(count.get("T", 1)),
            "voxel_um": voxel}


def _voxel_um(tf, index=0):
    """Voxel sizes in µm (z, y, x) of series `index` where the file states them: that series' own OME Pixels (the n-th
    Image is the n-th series), or ImageJ's spacing and resolution (a file of one series)."""
    import xml.etree.ElementTree as ET

    if tf.is_ome:
        images = ET.fromstring(tf.ome_metadata).findall("{*}Image")
        pixels = images[index].find("{*}Pixels") if index < len(images) else None
        if pixels is None:
            return None
        units = {"µm": 1.0, "um": 1.0, "micron": 1.0, "nm": 1e-3, "mm": 1e3}
        sizes = []
        for a in "ZYX":
            value, unit = pixels.get(f"PhysicalSize{a}"), pixels.get(f"PhysicalSize{a}Unit", "µm")
            if value is None or unit not in units:
                return None
            sizes.append(float(value) * units[unit])
        return sizes
    if index != 0:
        return None
    ij = tf.imagej_metadata or {}
    page = tf.pages[0]
    if ij.get("spacing") and "XResolution" in page.tags and str(ij.get("unit", "")).lower() in ("micron", "µm", "um", "\\u00b5m"):
        x = page.tags["XResolution"].value
        y = page.tags["YResolution"].value
        return [float(ij["spacing"]), y[1] / y[0], x[1] / x[0]]
    return None


class TooBig(ValueError):
    pass


def _gib(n):
    return f"{n / 2**30:.1f} GB"


def _paged(s):
    """The series' page grid when it is stored one plane (or one plane of samples) a page: (its axes, its shape), the
    stack axis among them; else None."""
    import math

    page = len(s.keyframe.shape)
    grid_axes, grid_shape = s.axes[:-page] if page else s.axes, tuple(s.shape[:-page]) if page else tuple(s.shape)
    stack_axes = [a for a in grid_axes if a in "ZIQ"]
    plane_axes = s.axes[-page:].replace("S", "") if page else ""
    if (not grid_axes or len(stack_axes) != 1 or math.prod(grid_shape) != len(s.pages)
            or plane_axes != "YX"):
        return None
    return grid_axes, grid_shape


def _page_of(s, grid, z, channel, time):
    import numpy as np

    axes, shape = grid
    index = []
    for a, n in zip(axes, shape):
        if a in "ZIQ":
            index.append(z)
        elif a == "C":
            index.append(channel or 0)
        elif a == "T":
            index.append(time or 0)
        elif n == 1:
            index.append(0)
        else:
            raise ValueError(f"its axis {a} holds {n} and can't be chosen here")
    return s.pages[int(np.ravel_multi_index(index, shape))]


def _plane(page, sample):
    import numpy as np

    data = page.asarray()  # this one page decoded (compressed or not), nothing else of the file
    axes = page.keyframe.axes
    if "S" in axes:  # contiguous YXS and planar SYX both select the named sample axis
        data = np.take(data, sample or 0, axis=axes.index("S"))
    return data


def _stack(path, series=0, channel=None, time=None, max_bytes=STACK_MAX_BYTES):
    """One 3D stack in z, y, x order: the series chosen, at one channel and one time point, read a plane at a time (only
    its own planes), refused before reading when it would hold more than `max_bytes`."""
    import math

    import numpy as np
    import tifffile

    with tifffile.TiffFile(path) as tf:
        s = tf.series[series]
        samples = s.shape[s.axes.index("S")] if "S" in s.axes else 1
        channels = s.shape[s.axes.index("C")] if "C" in s.axes else 1
        if not 0 <= (channel or 0) < channels * samples:
            raise ValueError(f"its channel must be between 0 and {channels * samples - 1}")
        channel_index, sample_index = divmod(channel or 0, samples)  # C-major, S-minor, as the combined count is shown
        grid = _paged(s)
        if grid is not None:
            depth = grid[1][[a in "ZIQ" for a in grid[0]].index(True)]
            plane = tuple(s.keyframe.shape[s.keyframe.axes.index(a)] for a in "YX")
            need = depth * math.prod(plane) * s.dtype.itemsize
            if need > max_bytes:
                raise TooBig(f"its stack would hold {_gib(need)}, past the {_gib(max_bytes)} this registration may read")
            out = np.empty((depth, *plane), s.dtype)
            for z in range(depth):
                out[z] = _plane(_page_of(s, grid, z, channel_index, time), sample_index)
            return out
        need = math.prod(s.shape) * s.dtype.itemsize  # not stored a plane a page: read whole, only when it fits
        if need > max_bytes:
            raise TooBig(f"its series isn't stored a plane at a time, so reading it means all {_gib(need)} at once, "
                         f"past the {_gib(max_bytes)} this registration may read")
        data, axes = s.asarray(), s.axes
    for a, index in (("T", time), ("C", channel_index), ("S", sample_index)):
        if a in axes:
            data = np.take(data, index or 0, axis=axes.index(a))
            axes = axes.replace(a, "")
    axes = axes.replace("I", "Z").replace("Q", "Z")
    if sorted(axes) != sorted(SPATIAL):
        raise ValueError(f"that is not a 3D stack (its axes are {axes})")
    return np.transpose(data, [axes.index(a) for a in SPATIAL])


def _preview(path, series=0):
    """The middle slice of a series' first channel and time point, one page decoded; None and why when it can't be
    (not stored a plane a page, or a plane past PREVIEW_MAX_BYTES)."""
    import math

    import tifffile

    with tifffile.TiffFile(path) as tf:
        s = tf.series[series]
        grid = _paged(s)
        if grid is None:
            return None, "no preview: its series isn't stored a plane at a time"
        need = math.prod(s.keyframe.shape) * s.dtype.itemsize
        if need > PREVIEW_MAX_BYTES:
            return None, f"no preview: one plane holds {_gib(need)}"
        depth = grid[1][[a in "ZIQ" for a in grid[0]].index(True)]
        return _plane(_page_of(s, grid, depth // 2, 0, 0), 0), ""


def _png(path, image):
    from PIL import Image

    Image.fromarray(image).save(path)


def _grey(plane):
    import numpy as np

    lo, hi = np.percentile(plane, (1, 99.5))
    work = plane.astype(np.float32)
    work -= lo
    work /= max(hi - lo, 1e-6)
    np.clip(work, 0, 1, out=work)
    work *= 255
    return work.astype(np.uint8)


def inspect(request):
    import tifffile

    out = Path(request["output"])
    out.mkdir(parents=True, exist_ok=True)
    with tifffile.TiffFile(request["input"]) as tf:
        series = [_series_facts(s, _voxel_um(tf, i)) for i, s in enumerate(tf.series)]
    plane, why = _preview(request["input"], 0) if series and len(series[0]["shape"]) == 3 else (None, "")
    outputs = {}
    if plane is not None:
        _png(out / "preview.png", _grey(plane))
        outputs["preview"] = "preview.png"
    first = series[0] if series else {}
    voxel = first.get("voxel_um")
    return {"series": series, "voxel_um": voxel if len(series) == 1 else None, "outputs": outputs,
            "says": f"{len(series)} series; the first {' × '.join(map(str, first.get('shape', [])))} ({first.get('axes')})"
                    + (f", voxels {voxel} µm" if voxel else ", voxel sizes not stated") + (f"; {why}" if why else "")}


def _picture(result):
    """Three slices of brainreg's downsampled volume with the registered atlas's boundaries, in that space."""
    import numpy as np
    import tifffile

    tiles = []
    with tifffile.TiffFile(result / "downsampled.tiff") as down_file, tifffile.TiffFile(result / "boundaries.tiff") as edges_file:
        down, edges = down_file.series[0], edges_file.series[0]
        grids = _paged(down), _paged(edges)
        if any(grid is None for grid in grids) or down.shape != edges.shape:
            raise ValueError("registration preview needs matching volumes stored one plane per page")
        import math
        if max(math.prod(s.keyframe.shape) * s.dtype.itemsize for s in (down, edges)) > PREVIEW_MAX_BYTES:
            raise TooBig("registration preview plane exceeds its memory allowance")
        depth = grids[0][1][[a in "ZIQ" for a in grids[0][0]].index(True)]
        for at in (0.3, 0.5, 0.7):
            i = int(depth * at)
            grey = _grey(_plane(_page_of(down, grids[0], i, 0, 0), 0))
            boundary = _plane(_page_of(edges, grids[1], i, 0, 0), 0) > 0
            rgb = np.stack([grey] * 3, axis=-1)
            rgb[boundary] = (230, 60, 200)
            tiles.append(rgb)
    gap = np.zeros((tiles[0].shape[0], 4, 3), np.uint8)
    _png(result / "picture.png", np.concatenate([tiles[0], gap, tiles[1], gap, tiles[2]], axis=1))
    return "brainreg/picture.png"


def register(request):
    out = Path(request["output"])
    if request.get("fixture"):
        volume, voxels, orientation = _fixture(request)
    else:
        import tifffile

        series = request.get("series") or 0
        with tifffile.TiffFile(request["input"]) as tf:
            axes = "".join(a for a in tf.series[series].axes if a not in "CTS").replace("I", "Z").replace("Q", "Z")
        declared = request["orientation"].lower()
        if sorted(axes) != sorted(SPATIAL) or len(declared) != 3:
            raise ValueError("registration needs three spatial axes and the orientation stated for those axes")
        # The saved fact describes the source array. Transform its letters with the same named-axis permutation as
        # the pixels; never reinterpret or rewrite the user's original orientation in the volume/job record.
        orientation = "".join(declared[axes.index(a)] for a in SPATIAL)
        stack = _stack(request["input"], series, request.get("channel"), request.get("time"),
                       request.get("max_bytes") or STACK_MAX_BYTES)
        volume = Path(request["output"]) / "input" / "volume_zyx.tif"  # the chosen stack, z y x, as brainreg reads it
        volume.parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(volume, stack)
        del stack  # brainreg reopens the file; the decoded input must not remain resident throughout registration
        voxels = request["voxel_um"]  # voxel sizes are already explicitly specified in z, y, x order
    conf = out / ".brainglobe"  # where BrainGlobe looks for its atlases: the catalog's revision, read only
    conf.mkdir(parents=True, exist_ok=True)
    (conf / "bg_config.conf").write_text(f"[default_dirs]\nbrainglobe_dir = {request['data']['allen-mouse-25um']}\n"
                                         f"interm_download_dir = {conf}\n")
    os.environ["BRAINGLOBE_CONFIG_DIR"] = str(conf)  # read when brainglobe_atlasapi is first imported, below
    from brainreg.core.cli import main

    result = out / "brainreg"
    sys.argv = ["brainreg", str(volume), str(result), "-v", *(str(v) for v in voxels), "--orientation", orientation,
                "--atlas", ATLAS, "--n-free-cpus", str(request.get("free_cpus", 2))]
    main()
    outputs = {key: f"brainreg/{name}" for key, name in OUTPUTS.items() if (result / name).exists()}
    lacking = [key for key in REQUIRED if key not in outputs]
    if lacking:
        raise RuntimeError(f"brainreg finished without {', '.join(lacking)}")
    with (result / "volumes.csv").open() as f:
        regions = sum(1 for _ in csv.DictReader(f))
    picture = _picture(result)
    outputs["picture"] = picture
    what = "the sample brain" if request.get("fixture") else Path(request["input"]).name
    return {"outputs": outputs, "regions": regions, "atlas": ATLAS, "voxel_um": list(voxels), "orientation": orientation,
            "picture": picture, "says": f"registered {what} to {ATLAS}: {regions} regions measured"}
