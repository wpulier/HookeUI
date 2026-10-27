"""A BioImage Model Zoo model for ObservationUI (runner python-env; catalog/templates/bioimageio, reviewed): the picture in,
a label map of the things it segments out. Copied byte for byte into an entry the app writes from a bioimage.io link
(orchestrator/link_recognise.py); only the model's own files differ, pinned by their hashes in its manifest.

It reads the model's `rdf.yaml` (format 0.4 or 0.5) and runs its TorchScript or ONNX weights on the whole picture, with
the preprocessing the spec defines (`PRE`) applied in NumPy, the input padded to the sizes the model accepts and the
output cut back. Before its first answer it runs the model on its **own test input** and compares the result with its own
test output (`self_test`): a mismatch (weights, or this runner) fails the call, so a model that doesn't run as its makers
ran it never gives an answer. The output becomes instance labels (`to_labels`): integer labels as they are (or
whole numbers carried as floats, below 2^24, where they are exact; anything else is refused); one channel
of probability, its connected regions; foreground and boundary channels, the regions between the boundaries grown back
over the foreground (ObservationUI nearest-seed conversion v1, not the author's watershed). Unnamed outputs require
an exact RDF-and-weight binding in output-semantics.json, whose sources establish channel order. Anything else is refused with what it is. Nothing here reaches the network (the app refuses it).
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

TEST_ATOL = 1e-3  # the model's own test output, compared with what this runner gives on its test input
MIN_PIXELS = 12  # a region smaller than this is noise, not a thing

_model = None


# --- the model's description -------------------------------------------------------------------------------------------
def _axes(tensor: dict) -> list[str]:
    """Axis letters in order: b, c, z, y, x (0.4: a string "bcyx"; 0.5: a list of dicts)."""
    axes = tensor["axes"]
    if isinstance(axes, str):
        return list(axes.lower())
    out = []
    for a in axes:
        kind = a.get("type")
        out.append("b" if kind == "batch" else "c" if kind == "channel" else str(a.get("id", kind))[0].lower())
    return out


def _channels(tensor: dict) -> int:
    axes = tensor["axes"]
    if isinstance(axes, str):
        shape = tensor.get("shape")
        if isinstance(shape, list):
            return int(shape[axes.index("c")]) if "c" in axes else 1
        if isinstance(shape, dict) and "min" in shape:
            return int(shape["min"][axes.index("c")]) if "c" in axes else 1
        return 1
    for a in axes:
        if a.get("type") == "channel":
            return len(a.get("channel_names") or []) or int(a.get("size") or 1)
    return 1


def _sizes(tensor: dict) -> dict[str, tuple[int, int]]:
    """Each spatial axis' (min, step): the sizes the model accepts are min + k * step."""
    axes = tensor["axes"]
    out = {}
    if isinstance(axes, str):
        shape = tensor.get("shape")
        for i, letter in enumerate(axes):
            if letter in "yx":
                if isinstance(shape, dict):
                    out[letter] = (int(shape["min"][i]), int(shape["step"][i]))
                else:
                    out[letter] = (int(shape[i]), 0)
        return out
    for a in axes:
        if a.get("type") == "space":
            size = a.get("size")
            if isinstance(size, dict) and "min" in size:
                out[str(a["id"])[0]] = (int(size["min"]), int(size.get("step") or 0))
            elif isinstance(size, int):
                out[str(a["id"])[0]] = (size, 0)
            else:
                out[str(a["id"])[0]] = (1, 1)
    return out


# --- preprocessing (bioimage.io spec, 0.4 and 0.5 names) -----------------------------------------------------------------
def _reduce_axes(kwargs: dict, axes: list[str]) -> tuple[int, ...]:
    """The axes a statistic runs over: those named (0.4 letters, 0.5 ids), per sample (never the batch)."""
    named = kwargs.get("axes")
    if named is None:
        return tuple(i for i, a in enumerate(axes) if a != "b")
    letters = [str(n)[0].lower() for n in named] if not isinstance(named, str) else list(named.lower())
    letters = ["c" if n == "c" else n for n in letters]
    return tuple(i for i, a in enumerate(axes) if a in letters)


def _zero_mean_unit_variance(x, kwargs, axes):
    if kwargs.get("mode") == "fixed" or "mean" in kwargs and "std" in kwargs:
        mean, std = np.asarray(kwargs["mean"], np.float32), np.asarray(kwargs["std"], np.float32)
        return (x - _broadcast(mean, x, axes)) / (_broadcast(std, x, axes) + float(kwargs.get("eps", 1e-6)))
    over = _reduce_axes(kwargs, axes)
    mean = x.mean(axis=over, keepdims=True)
    std = x.std(axis=over, keepdims=True)
    return (x - mean) / (std + float(kwargs.get("eps", 1e-6)))


def _broadcast(values, x, axes):
    """Per-channel values (a list) along the channel axis; one value as it is."""
    values = np.asarray(values, np.float32)
    if values.ndim == 0 or values.size == 1:
        return values.reshape(())
    shape = [1] * x.ndim
    shape[axes.index("c")] = values.size
    return values.reshape(shape)


def _scale_range(x, kwargs, axes):
    over = _reduce_axes(kwargs, axes)
    lo = np.percentile(x, float(kwargs.get("min_percentile", 0.0)), axis=over, keepdims=True)
    hi = np.percentile(x, float(kwargs.get("max_percentile", 100.0)), axis=over, keepdims=True)
    return (x - lo) / (hi - lo + float(kwargs.get("eps", 1e-6)))


def _scale_linear(x, kwargs, axes):
    gain = _broadcast(kwargs.get("gain", 1.0), x, axes)
    offset = _broadcast(kwargs.get("offset", 0.0), x, axes)
    return x * gain + offset


PRE = {
    "zero_mean_unit_variance": _zero_mean_unit_variance,
    "fixed_zero_mean_unit_variance": lambda x, k, a: _zero_mean_unit_variance(x, {**k, "mode": "fixed"}, a),
    "scale_range": _scale_range,
    "scale_linear": _scale_linear,
    "clip": lambda x, k, a: np.clip(x, k.get("min"), k.get("max")),
    "binarize": lambda x, k, a: (x > float(k.get("threshold", 0.5))).astype(np.float32),
    "sigmoid": lambda x, k, a: 1.0 / (1.0 + np.exp(-x)),
    "ensure_dtype": lambda x, k, a: x.astype(k.get("dtype", "float32")),
}


def preprocess(x: np.ndarray, tensor: dict) -> np.ndarray:
    axes = _axes(tensor)
    x = x.astype(np.float32)
    for step in tensor.get("preprocessing") or []:
        name = step.get("id") or step.get("name")
        if name not in PRE:
            raise ValueError(f"its preprocessing {name!r} isn't one this runner knows")
        x = PRE[name](x, step.get("kwargs") or {}, axes).astype(np.float32)
    return x


# --- shapes ---------------------------------------------------------------------------------------------------------------
def fit(n: int, minimum: int, step: int) -> int:
    """The smallest size the model accepts that holds n: min + k * step; a fixed size (no step) only when n fits in it."""
    if step <= 0:
        if n > minimum:
            raise ValueError(f"it takes pictures of {minimum} px at most on a side, and this one is {n}")
        return minimum
    if n <= minimum:
        return minimum
    return minimum + -(-(n - minimum) // step) * step


def to_input(picture: np.ndarray, tensor: dict) -> tuple[np.ndarray, tuple[int, int]]:
    """The app's picture (H, W, 3 uint8) as the model's input array, padded to sizes it accepts; and the picture's (H, W)."""
    axes, channels = _axes(tensor), _channels(tensor)
    if "z" in axes:
        raise ValueError("it is a 3D model: volumes aren't run from a picture")
    height, width = picture.shape[:2]
    if channels == 1:
        planes = picture.astype(np.float32).mean(axis=2, keepdims=True)
    elif channels == 3:
        planes = picture.astype(np.float32)
    else:
        raise ValueError(f"it takes {channels} channels: a camera picture has 1 (grey) or 3 (colour)")
    sizes = _sizes(tensor)
    ty = fit(height, *sizes.get("y", (height, 0)))
    tx = fit(width, *sizes.get("x", (width, 0)))
    mode = "reflect" if ty - height < height and tx - width < width else "edge"
    planes = np.pad(planes, ((0, ty - height), (0, tx - width), (0, 0)), mode=mode)
    order = {"b": None, "c": 2, "y": 0, "x": 1}
    arr = planes.transpose([order[a] for a in axes if a != "b"])
    return (arr[None] if "b" in axes else arr), (height, width)


# --- the output as labels ---------------------------------------------------------------------------------------------------
def _regions(mask: np.ndarray) -> np.ndarray:
    from scipy import ndimage

    labels, n = ndimage.label(mask)
    if n:
        sizes = np.bincount(labels.ravel())
        small = sizes < MIN_PIXELS
        small[0] = False
        labels[small[labels]] = 0
        labels, _ = ndimage.label(labels > 0)
    return labels.astype(np.int32)


def _grow(seeds: np.ndarray, within: np.ndarray) -> np.ndarray:
    """Each foreground pixel given the nearest seed's label (the boundaries between them shared out)."""
    from scipy import ndimage

    if not seeds.max():
        return seeds
    _, nearest = ndimage.distance_transform_edt(seeds == 0, return_indices=True)
    grown = seeds[nearest[0], nearest[1]]
    return np.where(within, grown, 0).astype(np.int32)


def output_meaning(tensor: dict) -> list[str]:
    axes = tensor.get("axes") or []
    names = (next((a.get("channel_names") for a in axes if a.get("type") == "channel"), None)
             if isinstance(axes, list) else tensor.get("channel_names"))
    names = names or tensor.get("channel_names") or [tensor.get("id") or tensor.get("name") or ""]
    aliases = {"fg": "foreground", "bd": "boundary", "bg": "background"}
    names = [aliases.get(str(n).lower(), str(n).lower()) for n in names]
    known = ({"foreground"}, {"foreground", "boundary"}, {"background", "foreground", "boundary"}, {"instance_labels"})
    if len(set(names)) != len(names) or set(names) not in known or tensor.get("postprocessing"):
        raise ValueError("its output meaning needs a reviewed adapter, not a guess from its channels")
    return names


def resolved_meaning(tensor: dict, rdf_sha256: str, weights_sha256: str) -> list[str]:
    try:
        return output_meaning(tensor)
    except ValueError:
        import json
        entries = json.loads(Path(__file__).with_name("output-semantics.json").read_text())
        binding = entries.get(rdf_sha256) or {}
        if binding.get("weightsSha256") != weights_sha256 or tensor.get("postprocessing"):
            raise ValueError("its output meaning needs a reviewed adapter for these exact RDF and weight bytes") from None
        return output_meaning({"channel_names": binding.get("channels", [])})


def to_labels(output: np.ndarray, out_axes: list[str], size: tuple[int, int], tensor=None) -> np.ndarray:
    """The model's output (its axes) as instance labels of the picture's size (see the module's docstring)."""
    order = [out_axes.index(a) for a in ("c", "y", "x") if a in out_axes]
    arr = np.asarray(output)
    if "b" in out_axes:
        arr = np.take(arr, 0, axis=out_axes.index("b"))
        out_axes = [a for a in out_axes if a != "b"]
        order = [out_axes.index(a) for a in ("c", "y", "x") if a in out_axes]
    arr = arr.transpose(order)
    if arr.ndim == 2:
        arr = arr[None]
    arr = arr[:, : size[0], : size[1]]
    names = output_meaning(tensor or {})
    if len(names) != arr.shape[0] or not np.isfinite(arr).all():
        raise ValueError("its output channels differ from the declared meaning or contain nonfinite values")
    if names == ["instance_labels"] and np.issubdtype(arr.dtype, np.integer) and arr.min() >= 0:
        return arr[0].astype(np.int32)
    if (names == ["instance_labels"] and np.issubdtype(arr.dtype, np.floating) and arr.min() >= 0
            and arr.max() < 2 ** 24 and np.array_equal(arr, np.round(arr))):
        return arr[0].astype(np.int32)  # whole numbers carried as floats (InstanSeg's TorchScript): exact below 2^24
    if names == ["foreground"]:
        return _regions(arr[0] > 0.5)
    if "foreground" in names and "boundary" in names:
        fg, boundary = arr[names.index("foreground")], arr[names.index("boundary")]
        within = fg > 0.5
        return _grow(_regions(within & (boundary < 0.5)), within)
    raise ValueError(f"its output has {arr.shape[0]} channels: the app can't tell which of them are the things it found")


# --- running it -------------------------------------------------------------------------------------------------------------
class Model:
    def __init__(self, folder: Path):
        import yaml

        self.folder = folder
        raw = (folder / "rdf.yaml").read_bytes()
        self.rdf = yaml.safe_load(raw)
        weights = self.rdf.get("weights") or {}
        self.kind = "torchscript" if "torchscript" in weights else "onnx" if "onnx" in weights else ""
        if not self.kind:
            raise ValueError("it has no TorchScript or ONNX weights")
        self.weights = folder / Path(weights[self.kind]["source"]).name
        self.input, self.output = self.rdf["inputs"][0], self.rdf["outputs"][0]
        if len(self.rdf["inputs"]) != 1 or len(self.rdf["outputs"]) != 1:
            raise ValueError("multiple inputs or outputs need a reviewed adapter")
        import hashlib
        try:
            names = output_meaning(self.output)
        except ValueError:
            # Stream the actual downloaded bytes, not only the RDF's declaration, before any model is loaded.
            with self.weights.open("rb") as stream:
                weight_sha = hashlib.file_digest(stream, "sha256").hexdigest()
            names = resolved_meaning(self.output, hashlib.sha256(raw).hexdigest(), weight_sha)
        self.meaning = {"channel_names": names}  # conversion contract; the upstream RDF stays byte-for-byte intact
        if self.kind == "torchscript":
            import torch

            torch.set_num_threads(2)
            self._ts = torch.jit.load(str(self.weights), map_location="cpu").eval()
        else:
            import onnxruntime

            self._ort = onnxruntime.InferenceSession(str(self.weights), providers=["CPUExecutionProvider"])

    def run(self, x: np.ndarray) -> np.ndarray:
        if self.kind == "torchscript":
            import torch

            with torch.no_grad():
                y = self._ts(torch.from_numpy(np.ascontiguousarray(x, np.float32)))
            return (y[0] if isinstance(y, (tuple, list)) else y).numpy()
        name = self._ort.get_inputs()[0].name
        return self._ort.run(None, {name: np.ascontiguousarray(x, np.float32)})[0]

    def self_test(self) -> float:
        """Its own test input through this runner, compared with its own test output: the largest difference."""
        def tensor_file(t: dict) -> Path:
            ref = t.get("test_tensor") or {}
            source = ref.get("source") if isinstance(ref, dict) else None
            if source is None:
                tests = self.rdf.get("test_inputs" if t is self.input else "test_outputs") or []
                source = tests[0] if tests else ""
            return self.folder / Path(str(source)).name

        x = np.load(tensor_file(self.input))
        expected = np.load(tensor_file(self.output))
        got = self.run(preprocess(x, self.input))
        if got.shape != expected.shape:
            raise ValueError(f"its own test gave shape {got.shape}, expected {expected.shape}")
        worst = float(np.abs(got.astype(np.float64) - expected).max())
        if worst > TEST_ATOL:
            raise ValueError(f"its own test didn't match (largest difference {worst:.3g}): it doesn't run here as its "
                             "makers ran it")
        return worst


def segment(picture, params):
    global _model
    if _model is None:
        m = Model(Path(os.environ["MICROMIND_ENTRY_FILES"]))
        m.self_test()  # before any answer: a model that doesn't run as its makers ran it gives none
        _model = m
    x, size = to_input(np.asarray(picture), _model.input)
    y = _model.run(preprocess(x, _model.input))
    return to_labels(y, _axes(_model.output), size, _model.meaning)
