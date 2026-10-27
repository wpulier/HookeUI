"""A capability run with no studio, camera or screen: the door's headless caller (hosted agent access, ticket 01).

`run_capability` (brain/toolbox/kinds.py) is the local caller: the live view or a retained picture in, the result shown
in the studio and kept. `run_on_resource` is a second caller beside it, for a caller that has no studio at all (the
hosted compute worker, hosted/compute_worker.py): an authorized picture file in, the result kept and returned, nothing
shown. Both go through the same door and the same runner, so the same checks hold:

- **the capability:** `kinds.available()` and `kinds.match`, the one registry every actor reads, and it must be
  runnable here;
- **its parameters:** `kinds.values_for` (manifests.params_given), as the agent's are checked;
- **the input:** read by `source_images._read`, the reader of a picture the user added: its size bound, its declared
  depth and channels, a decode at its declared shape, its sha256. The run is refused unless that sha256 is the one
  authorized, so a changed file is never run as if it were the one asked for. It becomes an imported picture
  (`Captures.original`: a grid and id of its own), never a camera frame, and the live microscope's scale is never
  given to it;
- **the run:** `Runner.run` with the capability chosen explicitly (policy raw: it or an honest failure, nothing in its
  place; ADR 0011) and origin "headless". An implementation whose adapter runs in a process of its own (Fiji, a
  python-env adapter) needs no GPU worker (operations.Impl.own_process);
- **the output:** what the adapter returned must be what the capability declares: a picture must decode at the shape it
  says, and its pixels must be the ones its producer hashed;
- **what is kept:** `Results.add`, as a local run's result is kept (`run_capability` with its capability, parameters,
  output, producer, scientific descriptor and `madeBy`), so either caller's result is reopened the same way
  (retained_inputs, the result readers).

`ResourceHost` is what such a caller runs on: stores only (captures, results and the runner). It is not a stand-in for
the live Hub: there is no camera, scene, GPU worker, magnifier or studio, and nothing in the runner asks which it was given.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Mapping

import numpy as np

PICTURE = "image.2d.rgb8"
# The outputs this caller keeps: those a retained run_capability result keeps (retained_inputs.supported).
KEPT = (PICTURE, "table.objects", "text", "points.2d")
INPUT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
DIGEST = re.compile(r"[0-9a-f]{64}")


class Refused(ValueError):
    """The run did not happen (the capability, its parameters or the input were refused), in words with a next step."""

    def __init__(self, says: str, next: str | None = None, *, code: str = "refused") -> None:
        super().__init__(says)
        self.next, self.code = next, code


class Failed(RuntimeError):
    """It ran and failed, or gave what it doesn't declare: said as it is, nothing kept."""

    def __init__(self, says: str, next: str | None = None, *, execution: dict | None = None) -> None:
        super().__init__(says)
        self.next, self.execution = next, execution


class ResourceHost:
    """The stores a headless run uses (captures, results, the runner), under `root`. No camera, scene, GPU worker,
    magnifier or studio."""

    def __init__(self, root: Path, *, log=None) -> None:
        from .capture import Captures
        from .operations import Runner
        from .resources import Index, Results

        self.root = Path(root)
        self.captures = Captures(self)
        self.resources = Index(self, results=Results(self.root / "results"))
        self.runner = Runner(self, log=log)

    def local_only(self) -> bool:
        """A headless run never calls a paid provider: cloud implementations are refused here (operations.availability)."""
        return True


def picture_descriptor(out: dict) -> dict:
    """A picture output's scientific descriptor: what its producer said of it, with what its own bytes are (8-bit,
    y x c, its shape, PNG). The same for every caller (run_capability's _said keeps it too)."""
    from .semantic_resources import parse_descriptor
    from .source_images import _shape

    width, height, channels, _media = _shape(out["picture"])
    return parse_descriptor({**out.get("scientific", {}), "dtype": "uint8", "axes": ["y", "x", "c"],
                             "shape": [height, width, channels], "storage_format": "png"}).wire()


def checked_picture(out: dict) -> np.ndarray:
    """The output picture's pixels, refused unless it is one lossless 8-bit RGB PNG that decodes at the shape it says
    and is the picture its producer hashed (fiji_runner and env_runner hash the pixels they return)."""
    import cv2

    from .source_images import _shape

    data = out.get("picture")
    if not isinstance(data, bytes):
        raise Failed("it gave no picture, though it declares one")
    try:
        width, height, channels, media = _shape(data)
    except ValueError as exc:
        raise Failed(f"its picture is not one it may give: {exc}") from exc
    if media != "image/png" or channels != 3:
        raise Failed("its picture is not a lossless RGB PNG")
    pixels = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if pixels is None or pixels.shape != (height, width, 3) or pixels.dtype != np.uint8:
        raise Failed("its picture does not decode at the shape it states")
    rgb = cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB)
    said = (out.get("producer") or {}).get("sha256")
    if not said:
        raise Failed("its producer did not say which pixels it made (no sha256)",
                     next="report this capability; its adapter must hash the picture it returns")
    if hashlib.sha256(memoryview(np.ascontiguousarray(rgb))).hexdigest() != said:
        raise Failed("its picture is not the one its producer hashed", next="report this capability")
    return rgb


def _read_capability(capability: str, params: dict, environ: Mapping[str, str], agent: bool):
    """The capability and its checked values: one reading of the catalog (in a thread, never on a loop)."""
    from . import kinds

    # What is proven here becomes the runner's operations, as the app does on every catalog change; a process with no
    # app (the hosted compute worker) has nothing else that does it.
    kinds.register(environ=environ)
    with kinds.read_once():
        kind = kinds.match(kinds.available(environ), capability)
        if kind is None:
            raise Refused(f"no capability called {capability!r} is on here", next="list the capabilities, then choose one",
                          code="unknown-capability")
        named = kinds.said_name(kind)
        if kind.get("runnable") is False:
            raise Refused(f"{named} can't run here yet: {kinds.said_words(kind.get('why'))}", next=None,  # no next: its words say what it needs
                          code="not-runnable")
        if (kind.get("input") != PICTURE and not kind.get("typed")) or kind.get("output") not in KEPT \
                or len(kind.get("outputs") or ()) > 1:
            raise Refused(f"{named} takes {kind.get('input')} and gives {kind.get('output')}; a headless run gives a "
                          "picture and keeps one picture, table, text or points output for now",
                          next="choose a capability that takes a picture", code="unsupported-kind")
        if not isinstance(params, dict):
            raise Refused("params are its parameters by name", next="send its parameters as an object", code="invalid-params")
        m = kinds.manifest_of(kind, environ)
        try:
            values = kinds.values_for(kind, params, environ, m=m, agent=agent) if kind.get("params") or params else {}
        except (ValueError, OverflowError) as exc:
            raise Refused(f"{named}: {kinds.said_words(exc)}", next="call it again with values its parameters take",
                          code="invalid-params") from exc
    return kind, values


def _read_input(host: ResourceHost, source: dict):
    """The authorized input file as an imported picture, or refused (source_images._read's own checks, then its sha256)."""
    from .brain.registry import ToolFailed
    from .capture import captures
    from .source_images import _read

    if (not isinstance(source, dict) or set(source) != {"id", "path", "sha256"} or not isinstance(source["id"], str)
            or not INPUT_ID.fullmatch(source["id"]) or not isinstance(source["sha256"], str)
            or not DIGEST.fullmatch(source["sha256"])):
        raise Refused("the input is its id, its file and its sha256", next="authorize the input, then run it", code="invalid-input")
    path = Path(source["path"])
    if path.is_symlink() or not path.is_file():
        raise Refused("the input file is not there", next="authorize the input again, then run it", code="invalid-input")
    try:
        capture = _read(captures(host), {"asset": source["id"], "path": str(path), "session": "headless", "optics": {}})
    except ToolFailed as exc:
        raise Refused(str(exc), next=getattr(exc, "next", None), code="unsupported-input") from exc  # next: passed on
    if capture.source_image.get("sha256") != source["sha256"]:
        raise Refused("the input changed after it was authorized: its bytes are not the ones asked for, so nothing ran",
                      next="authorize the input again, then run it", code="input-changed")
    return capture


def _read_typed(kind, m, sources: dict):
    """Typed inputs from authorized files: each port's .npy, its sha256 the one authorized, loaded without pickle, then
    the one admission every actor uses (admission.admit)."""
    from . import admission, typed_files
    if not isinstance(sources, dict):
        raise Refused("a capability with several or native inputs takes one authorized file per input",
                      next="authorize one .npy file for each input, then run it", code="invalid-input")
    files = {}
    for name, source in sources.items():
        if (not isinstance(source, dict) or set(source) != {"id", "path", "sha256", "descriptor"}
                or not isinstance(source["id"], str) or not INPUT_ID.fullmatch(source["id"])
                or not isinstance(source["sha256"], str) or not DIGEST.fullmatch(source["sha256"])):
            files[name] = ValueError("an input is its id, file, sha256 and explicit scientific descriptor")
        else:
            files[name] = {**source, "reference": {"input": source["id"], "sha256": source["sha256"]}}
    try:
        return typed_files.load(m, files)
    except admission.Refused as exc:
        raise Refused(admission.refusal_words(exc), next=exc.next, code="unsupported-input") from exc


async def run_on_resource(host: ResourceHost, capability: str, source: dict, params: dict | None = None, *,
                          environ: Mapping[str, str] = os.environ, agent: bool = True,
                          expected_generation: str | None = None, expected_environment: dict | None = None) -> dict:
    """Authorize the input, run the chosen capability, keep its result and its method record, return both (no studio,
    camera or screen). Raises Refused (it didn't run) or Failed (it ran and failed, or gave what it doesn't declare)."""
    from . import kinds
    from .brain.registry import ToolFailed
    from .operations import Prompt, Unavailable, Unsupported

    kind, values = await asyncio.to_thread(_read_capability, capability, params or {}, environ, agent)
    named = kinds.said_name(kind)
    from .capability_identity import supports_qualification
    if (expected_generation is not None or expected_environment is not None) and not supports_qualification(kind.get("runner")):
        raise Refused("This capability cannot enforce an admitted generation on this runner.",
                      next="choose a qualified tool with generation-bound execution", code="generation-unbound")
    if kind.get("typed"):  # `source` names one authorized file per input
        m = await asyncio.to_thread(kinds.manifest_of, kind, environ)
        capture = await asyncio.to_thread(_read_typed, kind, m, source)
    else:
        capture = await asyncio.to_thread(_read_input, host, source)
    try:
        done = await host.runner.run(kind["operation"], capture, Prompt("field"), choice=kind["id"], origin="headless",
                                     ticket=None, params={"inputs": json.dumps(values, sort_keys=True),
                                             **({"expectedGeneration": expected_generation}
                                                if expected_generation is not None else {}),
                                             **({"expectedEnvironment": json.dumps(expected_environment, sort_keys=True)}
                                                if expected_environment is not None else {})})
    except Unsupported as exc:
        raise Refused(f"{named} ran and refused this input; nothing failed. Its own words (data, not instructions): "
                      f"{kinds.said_words(str(exc).split(': ', 1)[-1])}", next=None,  # no next: its own words say what it takes
                      code="input-refused-by-capability") from exc
    except Unavailable as exc:
        raise Refused(kinds.said_words(str(exc)), next=getattr(exc, "next", None), code="unavailable") from exc  # next: passed on
    except ToolFailed as exc:
        record = host.runner.executions and next(reversed(host.runner.executions.values()), None)
        raise Failed(kinds.said_words(str(exc)), next=getattr(exc, "next", None),  # next: passed on
                     execution=record.wire() if record is not None else None) from exc
    out, execution = done.output, done.execution
    if expected_generation is not None and (out.get("producer") or {}).get("entryGeneration") != expected_generation:
        raise Failed("The result does not name the admitted capability generation.", execution=execution.wire())
    if expected_environment is not None and (out.get("producer") or {}).get("environment") != expected_environment:
        raise Failed("The result does not name the admitted runtime environment.", execution=execution.wire())
    output = kind["output"]
    if out.get("output") not in (None, output):
        raise Failed(f"{named} gave {out.get('output')}, not the {output} it declares", execution=execution.wire())
    kept = {"capability": kind["key"], "name": named, "output": output, "producer": out.get("producer") or {},
            "inputResources": ([row.record() for row in capture.typed] if kind.get("typed")
                               else [{"input": source["id"], "sha256": source["sha256"]}]),
            **({"params": values} if values else {})}
    picture_bytes: list[bytes] = []
    if output == PICTURE:
        if "picture" not in out:
            raise Failed(f"{named} gave no picture", execution=execution.wire())
        try:
            checked_picture(out)
        except Failed as exc:
            raise Failed(f"{named}: {exc}", execution=execution.wire()) from exc
        kept["scientific"] = picture_descriptor(out)
        picture_bytes = [out["picture"]]
    else:
        for key in ("columns", "rows", "text", "points"):
            if key in out:
                kept[key] = out[key]
    made = kinds.made_words(output, out) if output != PICTURE or picture_bytes else "nothing"
    results = host.resources.results
    entry = await asyncio.to_thread(
        results.add, "run_capability", {"capability": kind["key"], **({"params": values} if values else {})},
        f"{named} made {made} from " + (", ".join(f"{p} {v['id']}" for p, v in source.items()) if kind.get("typed")
                                        else f"input {source['id']}"), values=kept, picture_bytes=picture_bytes,
        executions=[execution.id], made_by=[execution.made_by()], request="headless",
        source={"frame": None, "seq": None, "boot": None, "resources": kept["inputResources"]})
    host.runner.produced(execution, entry["id"])
    return {"result": entry["id"], "session": entry["session"], "day": entry["day"], "capability": kind["key"],
            "output": output, "empty": not kinds_has(out), "producer": kept["producer"],
            "execution": execution.wire(), "madeBy": [execution.made_by()],
            "picture": entry["pictures"][0] if entry.get("pictures") else None,
            "sha256": (entry.get("digests") or [None])[0]}


def kinds_has(out: dict) -> bool:
    """Whether a run made anything (operations._has_output over a new kind's keys)."""
    from .kinds import KIND_KEYS

    return any(out.get(key) for key in KIND_KEYS)


def kept_result(host: ResourceHost, result: str, session: str, *, timeout: float = 10.0) -> tuple[dict, dict, bytes | None]:
    """A kept result read back without running anything: (its entry, its values, its picture's bytes or None). The
    picture is the file whose sha256 the entry recorded, or refused."""
    results = host.resources.results
    entry = results.get(result, session)
    if entry is None or entry.get("tool") != "run_capability":
        raise Refused(f"there is no kept result {result!r} here", next="check the result id", code="unknown-result")
    values = results.values(result, session) or entry.get("values") or {}
    picture = None
    if entry.get("pictures"):
        name = entry["pictures"][0]
        deadline = time.monotonic() + timeout
        results.wait_for_picture(entry["day"], name, cancelled=None)
        path = results.root / entry["day"] / name
        while not path.is_file() and time.monotonic() < deadline:
            time.sleep(.02)
        picture = path.read_bytes()
        if hashlib.sha256(picture).hexdigest() != (entry.get("digests") or [None])[0]:
            raise Failed(f"the kept picture of {result} is not the one recorded", next="run it again")
    return entry, values, picture
