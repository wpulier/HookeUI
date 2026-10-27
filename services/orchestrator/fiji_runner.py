"""Fiji functions as catalog folders: the Fiji runner, plugged into the one door (kinds.py; ADR 0024).

A Fiji function is a folder like any capability (catalog format 2): its inputs and outputs, its parameters, and a `[fiji]`
table naming the ImageJ command it runs with its options as the macro recorder writes them (each `{param}` filled from
its parameters), or a Groovy `script` beside its manifest. No app code names it: it runs in the app's function worker
(`services/fiji_worker/functions.groovy`), a headless Fiji of the generation in use apart from the reviewed Analyze
Particles binding (server.groovy), so adding one never changes another's proof.

**Proven against Fiji itself, anchored outside the folder** (`proof`, run on every new generation by
fiji_generations._prove): the command must be one the installed Fiji knows (its own menu table, never the folder's word),
and the function's result must equal the same command run as an ImageJ macro in a Fiji process of its own, on generated
pictures, at its defaults and at each numeric parameter's declared min and max and each choice. Exact unless the folder
declares a tolerance for that output with a reason. Parity is agreement with Fiji, never scientific accuracy (ADR 0020).

**Never a regression:** a function that fails its proof on a new generation is recorded as not proven there; one that
was proven on the generation in use and fails on the new one stops that generation from becoming current
(fiji_generations._prove).

Contributed folders use immutable snapshots and separate receipts. Each proof, reference and invocation runs in a
fresh confined JVM. They never enter a reviewed worker or its generation proof set.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np

from gpu_worker import manifests as catalog

from . import fiji_generations as generations

def _functions() -> Path:
    from .fiji_bindings import SERVER  # the Fiji worker's folder, decided once (fiji_bindings.py)

    return SERVER.with_name("functions.groovy")


FUNCTIONS = _functions()
CONTRACT = "fiji-function-v1"


@dataclass(frozen=True)
class Installed:
    """A Fiji function proven on the generation in use."""
    m: catalog.Manifest
    proof: dict
    n: int


def entries(environ: Mapping[str, str] = os.environ) -> list[catalog.Manifest]:
    """Every Fiji function folder; contributed entries have a separate activation boundary."""
    return [m for m in {**catalog.manifests(), **catalog.mine(environ)}.values() if m.runner == "fiji" and m.fiji]


def entry_of(key: str, environ: Mapping[str, str] = os.environ) -> catalog.Manifest | None:
    found = entries(environ)
    return next((m for m in found if m.key == key), None) or next((m for m in found if m.id == key), None)


def _sha(path: Path) -> str:
    if path == FUNCTIONS:
        # App-owned bootstrap bytes are shared by every Fiji function. Reuse the
        # existing full-signature hash cache rather than reading them for every
        # display/proof lookup. Changed/replaced files are rehashed; proof identities
        # stay byte-for-byte identical. The first read and stat remain synchronous.
        from .envs import _file_sha

        return _file_sha(path).hex()
    # Capability files and generated proof inputs retain their direct byte check.
    return hashlib.sha256(path.read_bytes()).hexdigest()


def script_of(m: catalog.Manifest) -> Path | None:
    return Path(m.folder) / m.fiji["script"] if m.fiji.get("script") else None


def identity(m: catalog.Manifest) -> dict:
    """What its proof is of: the worker, its manifest and its script, by their bytes."""
    script = script_of(m)
    return {"contract": CONTRACT, "workerSha256": _sha(FUNCTIONS),
            "manifestSha256": _sha(Path(m.folder) / catalog.MANIFEST),
            "scriptSha256": _sha(script) if script is not None else None}


def proof_current(m: catalog.Manifest, proof) -> bool:
    return isinstance(proof, dict) and proof.get("passed") is True and all(
        proof.get(k) == v for k, v in identity(m).items())


def in_use(m: catalog.Manifest | None, environ: Mapping[str, str] = os.environ) -> Installed | None:
    """Its proof on the generation in use, when it is whole and of exactly this folder; else None."""
    if m is None or m.runner != "fiji" or not m.fiji:
        return None
    if m.namespace:
        from . import fiji_contributions

        return fiji_contributions.in_use(m, environ)
    n = generations.current(environ)
    if n is None:
        return None
    proof = ((generations.receipt(n, environ) or {}).get("proofs") or {}).get(m.key)
    return Installed(m, proof, n) if proof_current(m, proof) else None


def options_of(m: catalog.Manifest, values: dict) -> str:
    """Its options as the macro recorder writes them, each {param} its value: a yes/no is its own id when yes and nothing
    when no (so its id is the recorder's keyword: `light`, `dark`); words with a space are bracketed, as the recorder
    brackets them."""
    kinds = {p.id: p.type for p in m.params}

    def one(match: re.Match) -> str:
        key = match.group(1)
        value = values.get(key)
        if kinds.get(key) == "bool":
            return key if value else ""
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        text = str(value)
        if any(c in text for c in "[]\n\r\""):
            raise generations.Failed(f"{key}'s value can't be written in a macro's options")
        return f"[{text}]" if " " in text else text

    return " ".join(re.sub(r"{(\w+)}", one, m.fiji.get("options", "")).split())


# The worker of functions: one headless Fiji of the generation in use, kept (fiji_bindings.Worker on functions.groovy).
def _worker():
    from .fiji_bindings import Worker

    return Worker(FUNCTIONS)


WORKER = None


def worker():
    global WORKER
    if WORKER is None:
        WORKER = _worker()
    return WORKER


def _request(m: catalog.Manifest, image: Path, values: dict, out: Path) -> dict:
    script = script_of(m)
    return {"op": "run", "image": str(image), "command": m.fiji["command"], "options": options_of(m, values),
            "params": values, "output": m.outputs[0].type, "out": str(out),
            **({"script": str(script), "scriptSha256": _sha(script)} if script is not None else {})}


def _picture(path: Path) -> np.ndarray:
    import cv2
    from PIL import Image, UnidentifiedImageError

    try:
        if path.stat().st_size > 64 * 1024 * 1024:
            raise generations.Failed("Fiji's picture exceeds the 64 MB file limit")
        with Image.open(path) as header:
            width, height = header.size
            if min(width, height) < 1 or width * height > 16_000_000:
                raise generations.Failed("Fiji's picture must contain at most 16 million pixels")
            if getattr(header, "n_frames", 1) != 1:
                raise generations.Failed("Fiji capabilities accept one image plane, not a stack or animation")
            if header.mode not in {"1", "L", "LA", "P", "RGB", "RGBA"}:
                raise generations.Failed("Fiji capabilities accept 8-bit pictures; convert this input explicitly first")
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise generations.Failed("Fiji's picture header could not be read safely") from exc
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None or bgr.shape != (height, width, 3) or bgr.dtype != np.uint8:
        raise generations.Failed("Fiji's result picture does not match its image header")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _table(reply: dict) -> dict:
    columns, rows = reply.get("columns"), reply.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list) or len(columns) > 64 or len(rows) > 10_000 or any(
            not isinstance(c, dict) or not isinstance(c.get("name"), str) for c in columns) or any(
            not isinstance(r, list) or len(r) != len(columns) for r in rows):
        raise generations.Failed("Fiji's results table is not a table")
    return {"columns": [{"name": c["name"][:80]} for c in columns],
            "rows": [[float(v) if isinstance(v, (int, float)) and np.isfinite(v) else None for v in r] for r in rows]}


class SandboxWorker:
    def __init__(self, m: catalog.Manifest, runtime: Path, admit=None):
        self.m, self.runtime, self.admit = m, runtime, admit

    def call(self, request: dict, n=None) -> dict:
        from . import fiji_sandbox

        try:
            result = fiji_sandbox.call(self.runtime, request, entry=Path(self.m.folder),
                                       **({"admit": self.admit} if self.admit is not None else {}))
        except fiji_sandbox.Failed as exc:
            raise generations.Failed(str(exc)) from exc
        if result.output is not None:
            Path(request["out"]).write_bytes(result.output)
        return result.reply

    def stop(self) -> None:
        pass


def run_function(m: catalog.Manifest, picture: np.ndarray, values: dict, *, n: int | None = None, using=None, admit=None) -> dict:
    """Its result on one RGB picture: {"picture", "shape"} or {"columns", "rows"}, as Fiji gave it."""
    import cv2

    if m.namespace:
        if n is None:
            raise generations.Failed("A contributed Fiji capability needs its pinned generation")
        using = using if isinstance(using, SandboxWorker) else SandboxWorker(m, generations.fiji_dir(n), admit)
    else:
        using = using or worker()
    with tempfile.TemporaryDirectory() as tmp:
        image, out = Path(tmp) / "in.png", Path(tmp) / "out.png"
        cv2.imwrite(str(image), cv2.cvtColor(np.ascontiguousarray(picture, np.uint8), cv2.COLOR_RGB2BGR))
        reply = using.call(_request(m, image, values, out), n)
        if m.outputs[0].type == "table.objects":
            return _table(reply)
        got = _picture(out)
        return {"picture": got, "shape": list(got.shape)}


def by_macro(m: catalog.Manifest, image: Path, values: dict, n: int, environ: Mapping[str, str] = os.environ) -> dict:
    """The same command and options as an ImageJ macro, in a Fiji process of its own: Fiji's own result to compare."""
    with tempfile.TemporaryDirectory() as tmp:
        if any(c in str(image) + tmp for c in "\"\\\n\r"):
            raise generations.Failed("its reference input path cannot be written in a macro safely")
        table = m.outputs[0].type == "table.objects"
        out = Path(tmp) / ("out.csv" if table else "out.png")
        macro = Path(tmp) / "reference.ijm"
        macro.write_text(f'open("{image}");\nrun("{m.fiji["command"]}", "{options_of(m, values)}");\n'
                         + (f'saveAs("Results", "{out}");\n' if table else f'saveAs("PNG", "{out}");\n')
                         # Headless Fiji keeps running after a macro that opened an image: it is told to end.
                         + 'eval("script", "System.exit(0);");\n')
        if m.namespace:
            from . import fiji_sandbox

            try:
                result = fiji_sandbox.run_macro(generations.fiji_dir(n, environ), image, m.fiji["command"],
                                                options_of(m, values), table=table)
            except fiji_sandbox.Failed as exc:
                raise generations.Failed(str(exc)) from exc
            if result.output is None:
                raise generations.Failed("The confined reference returned no output")
            out.write_bytes(result.output)
        else:
            generations.run_fiji(n, ["--console", "-macro", str(macro)], environ, what=f"{m.name} as a macro")
        if not table:
            got = _picture(out)
            return {"picture": got, "shape": list(got.shape)}
        import csv

        with out.open() as f:
            rows = list(csv.reader(f))
        head = [h for h in rows[0] if h.strip() and h != "Label"] if rows else []
        index = [rows[0].index(h) for h in head] if rows else []
        return _table({"columns": [{"name": h} for h in head],
                       "rows": [[_number(r[i]) for i in index] for r in rows[1:]]})


def _number(text: str):
    try:
        return float(text)
    except ValueError:
        return None


def value_sets(m: catalog.Manifest) -> list[dict]:
    """Its defaults, then each numeric parameter at its declared min and max and each choice, the rest at defaults."""
    base, _ = catalog.params_given(m, {})
    sets = [base]
    for p in m.params:
        if p.type in ("int", "number"):
            sets += [{**base, p.id: v} for v in (p.min, p.max) if v is not None and v != base.get(p.id)]
        elif p.type == "choice":
            sets += [{**base, p.id: v} for v in p.options if v != base.get(p.id)]
        elif p.type == "bool":
            sets.append({**base, p.id: not base.get(p.id)})
    return sets


def fixtures(folder: Path, m: catalog.Manifest) -> list[Path]:
    """Generated pictures with known shapes (RGB, light and dark things, an edge), and the folder's own test inputs."""
    import cv2

    pixels = np.full((96, 96, 3), 220, np.uint8)
    cv2.circle(pixels, (30, 30), 10, (40, 60, 200), -1)
    cv2.rectangle(pixels, (60, 50), (80, 75), (0, 120, 30), -1)
    pixels[0:6, 0:12] = (10, 10, 10)
    first = folder / "shapes.png"
    cv2.imwrite(str(first), cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR))
    return [first] + [Path(m.folder) / t["input"] for t in m.tests if str(t.get("input", "")).lower().endswith(
        (".png", ".jpg", ".jpeg", ".tif", ".tiff"))]


def same(m: catalog.Manifest, mine: dict, theirs: dict) -> float:
    """The largest difference between its result and Fiji's own (0 is identical); raises when they don't compare."""
    if "picture" in mine:
        if mine["picture"].shape != theirs["picture"].shape:
            raise generations.Failed(f"{m.name}: its picture is {mine['shape']}, Fiji's own {theirs['shape']}")
        return float(np.abs(mine["picture"].astype(np.int16) - theirs["picture"].astype(np.int16)).max(initial=0))
    if [c["name"] for c in mine["columns"]] != [c["name"] for c in theirs["columns"]] or len(mine["rows"]) != len(theirs["rows"]):
        raise generations.Failed(f"{m.name}: its table's columns or rows differ from Fiji's own")
    worst = 0.0
    for a, b in zip(mine["rows"], theirs["rows"]):
        for x, y in zip(a, b):
            if (x is None) != (y is None):
                raise generations.Failed(f"{m.name}: a cell is empty in one table and not the other")
            if x is not None:
                worst = max(worst, abs(x - y) / max(1.0, abs(y)) if abs(y) > 1 else abs(x - y))
    return worst


def _differs(a: dict, b: dict) -> bool:
    if "picture" in a:
        return a["picture"].shape != b["picture"].shape or bool((a["picture"] != b["picture"]).any())
    return a.get("columns") != b.get("columns") or a.get("rows") != b.get("rows")


def proof(m: catalog.Manifest, fiji_root: Path, environ: Mapping[str, str] = os.environ) -> dict:
    """Parity with Fiji itself, anchored outside the folder (module doc). Raises generations.Failed with why."""
    from .fiji_bindings import Worker

    n = int(fiji_root.parent.name)
    using = SandboxWorker(m, fiji_root) if m.namespace else Worker(FUNCTIONS)
    allowed = float((m.fiji.get("tolerance") or {}).get(m.outputs[0].id, {}).get("max", 0))
    cases = []
    try:
        if not using.call({"op": "has_command", "command": m.fiji["command"]}, n).get("found"):
            raise generations.Failed(f"{m.name}: Fiji generation {n} has no command {m.fiji['command']!r} (its menus "
                                     "are the reference, never the folder's word). Add its required plugin through staged Fiji setup, "
                                     "then check this capability again")
        moved = {p.id: False for p in m.params}  # whether each parameter changed the result anywhere
        with tempfile.TemporaryDirectory() as tmp:
            for picture in fixtures(Path(tmp), m):
                rgb = _picture(picture)
                base = None
                for values in value_sets(m):
                    mine = run_function(m, rgb, values, n=n, using=using)
                    theirs = by_macro(m, picture, values, n, environ)
                    worst = same(m, mine, theirs)
                    if worst > allowed:
                        raise generations.Failed(f"{m.name} differs from Fiji's own {m.fiji['command']} by {worst:g} on "
                                                 f"{picture.name} with {values}"
                                                 + (f" (its tolerance is {allowed:g})" if allowed else ""))
                    if base is None:
                        base = (values, mine)
                    else:
                        changed = [k for k in values if values[k] != base[0].get(k)]
                        if len(changed) == 1 and _differs(mine, base[1]):
                            moved[changed[0]] = True
                    cases.append({"input": picture.name, "inputSha256": _sha(picture), "values": values,
                                  "options": options_of(m, values), "largestDifference": worst})
        # Parity alone can't see an option that never reaches the command (both sides get the same words, and ImageJ
        # ignores a key it doesn't know): each parameter must change the result on some picture (the guide proof's
        # finding, 2026-10-15).
        still = [p.said for p in m.params if not moved[p.id]]
        if still:
            raise generations.Failed(f"{m.name}: {', '.join(still)} changed nothing on any picture it was proven on, so its "
                                     f"options may not reach {m.fiji['command']} (check the recorder's keyword, or add a "
                                     "test picture where it matters)")
    finally:
        using.stop()
    said = "identical to" if allowed == 0 else f"within {allowed:g} of"
    return {**identity(m), "passed": True, "cases": cases, "command": m.fiji["command"],
            "summary": f"{said} Fiji's own {m.fiji['command']} as a macro on {len(cases)} runs (its defaults, each "
                       "parameter's range and choices)"}


def note(installed: Installed) -> str:
    return f"proven on Fiji generation {installed.n}: {installed.proof.get('summary', 'parity with Fiji')}"


async def _run(run, capture, prompt, impl, params: dict) -> dict:
    """The operations' adapter for every Fiji function folder: the view's picture to Fiji, off the loop."""
    import cv2

    from . import owned_process
    from .operations import Unavailable
    from .source_images import worker_input

    m = entry_of(impl.id)
    installed = in_use(m) if m is not None else None
    if installed is None:
        why = generations.proven(m.key) if m is not None else "it is no longer in the catalog"
        raise Unavailable(f"{impl.label} can't run here ({why})", next="its card on the Models page")
    from .capability_identity import check_generation, check_environment
    identity = check_generation(installed.m, params)
    identity.update(check_environment(installed, params))
    given = json.loads(params["inputs"]) if params.get("inputs") else {}
    values, problems = catalog.params_given(installed.m, {k: v for k, v in given.items() if k not in catalog.HIDDEN})
    if problems:
        raise Unavailable(f"{impl.label}: " + "; ".join(problems), next=None)  # no next: the words say which value
    from .capture import MB, Refused, captures
    from .env_runner import AdmittedOutput

    lease = None
    if installed.m.namespace:
        need = capture.width * capture.height * 18 + 2 * len(capture.analysis_bytes) + 80 * MB
        try:
            lease = captures(run.hub).memory.reserve("analysis", need, f"{impl.label}'s input and protocol")
        except Refused as exc:
            raise Unavailable(f"{impl.label} wasn't given its picture: {exc}", next=exc.next) from exc

    def admit_picture(header, size):
        width, height = int.from_bytes(header[16:20]), int.from_bytes(header[20:24])
        lease.grow(2 * size + 15 * width * height + 16 * MB, f"{impl.label}'s output picture")

    def go() -> dict:
        picture = cv2.cvtColor(cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        if installed.m.namespace:
            from . import fiji_contributions

            with fiji_contributions.retain(installed.n):
                fresh = fiji_contributions.in_use(installed.m)
                if fresh is None or fresh.folder != installed.folder:
                    raise generations.Failed("This capability changed while its input was prepared; run it again")
                out = run_function(installed.m, picture, values, n=installed.n, admit=admit_picture)
        else:
            out = run_function(installed.m, picture, values, n=installed.n)
        producer = {**identity, "implementation": installed.m.id, "version": installed.m.version,
                    "generation": (installed.folder.name if installed.m.namespace else f"fiji-{installed.n}"),
                    "command": installed.m.fiji["command"], "options": options_of(installed.m, values), "params": values,
                    "output": installed.m.outputs[0].type}
        if "picture" in out:
            ok, png = cv2.imencode(".png", cv2.cvtColor(out["picture"], cv2.COLOR_RGB2BGR))
            if not ok:
                raise generations.Failed("its picture could not be encoded")
            digest = hashlib.sha256(memoryview(out["picture"])).hexdigest()
            result = {"picture": png.tobytes(), "shape": out["shape"], "output": installed.m.outputs[0].type,
                      "version": installed.m.version, "params": values, "producer": {**producer, "sha256": digest}}
            return AdmittedOutput(result, lease.split(len(result["picture"]), f"{impl.label}'s kept picture")) if lease else result
        body = json.dumps({"columns": out["columns"], "rows": out["rows"]}, sort_keys=True).encode()
        return {**out, "output": installed.m.outputs[0].type, "version": installed.m.version, "params": values,
                "producer": {**producer, "sha256": hashlib.sha256(body).hexdigest()}}

    try:
        encoded = await worker_input(capture)
        return await owned_process.off_loop(go)
    except Refused as exc:
        raise Unavailable(f"{impl.label} exceeded its picture allowance: {exc}", next=exc.next) from exc
    except generations.Failed as exc:
        raise Unavailable(f"{impl.label} failed: {exc}", next="its card on the Models page") from exc
    finally:
        if lease is not None:
            lease.release()


def _plug() -> None:
    from . import kinds

    # Each looked up when used, so what a test stands in for is what the door calls.
    kinds.plug(kinds.Backend("fiji", lambda environ: entries(environ), lambda m, environ: in_use(m, environ),
                             lambda: _run, lambda installed: note(installed), own_process=True))


_plug()
