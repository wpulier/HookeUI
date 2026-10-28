"""The catalog's model manifests (catalog/models/<id>/model.toml; docs/design/2026-10-12-the-open-catalog.md, ADR 0018):
one folder per model, its manifest the one description of what it is, where it comes from, what it needs to get and to
run, the operation contracts it implements, its credit, where it runs and what it is expected to cost. The worker's
tables (gpu_worker/loading.py) are read from them, and tests/test_catalog.py checks the code's own lists against them,
so a model is described once. What each key means: catalog/README.md.

Light on purpose (tomllib only), so the orchestrator and the worker read it without a model's libraries. Reading is
metadata only: an entry's adapter is never imported here. Each entry is read on its own, so one that can't be read
reports its problems (`problems()`) and the rest load.

The user's own entries (`~/MicroscopeMind/catalog/mine/models/<id>/`, MICROMIND_CATALOG moves the root) are read the
same way, namespaced `mine/<id>`, and never take a reviewed entry's id. They are listed (`mine()`), never run from here:
running code someone added is a deliberate activation (ADR 0021).

Entries also come through **channels** (orchestrator/channels.py; phase 3's E4b): a git repository's catalog at a pinned
commit, fetched into `<catalog>/channels/<name>/<commit>/` (manifests and adapters, never weights). The reviewed channel
(this repository) adds entries this app doesn't bundle to `manifests()`; a community channel's are the user's side, keyed
`<channel>/<id>` and listed with `mine()`. Through either, only entries with their own adapter (runner python-env): any
other needs the app's own code, so it comes with an app update.
"""
from __future__ import annotations

import math
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field, fields
from functools import lru_cache
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[2] / "catalog"
ROOT = CATALOG / "models"
MANIFEST = "model.toml"
RUNS = ("this-mac", "cloud", "cpu")
HOSTS = ("shared", "own", "own-python")
WORKERS = ("interactive", "analysis", "follow", "molmo")
KINDS = ("model", "tool", "tool-function", "cloud-model", "data")  # a tool: a program with functions (Fiji), run through
# bindings; data: files another entry reads (an atlas), downloaded and checked as weights are, never run
# catalog/runners: how a kind of model is run. "gpu-worker": the app's own GPU workers; "cloud": the app's own provider
# clients (vision.py, voice, research_worker); "" with an adapter of its own (a new architecture, or code the app keeps
# apart, such as services/noncommercial). "cellpose-file": a Cellpose model file the user added (orchestrator/mine.py):
# the app's own Cellpose code runs its weights, in a worker of its own, once the user has turned it on. The bioimage.io,
# Hugging Face... runners come later.
# "fiji": a managed Fiji, kept as complete generations (orchestrator/fiji_generations.py); its functions run only
# through reviewed bindings.
RUNNERS = ("gpu-worker", "cloud", "cellpose-file", "fiji", "python-env", "builtin-cpu")  # builtin-cpu: bundled app algorithms; python-env: an entry's own adapter in its env
# What an entry may be made for (`requires`, phase 3's E4): the manifest format this app reads, and each runner's contract
# (python-env's: services/env_worker/server.py's wire, the picture in and a label map or polygons out; fiji's: a
# binding in fiji_bindings.py) as the oldest and newest version this app runs. An entry made for another is listed with
# why ("needs a newer ObservationUI", "made for an older runner"), never run.
CATALOG_FORMAT = 4  # 4: portable wheel recipes; 3: bundle members; 2: typed capability contract (ADR 0024)
RUNNER_CONTRACTS = {runner: (1, 1) for runner in RUNNERS}
REQUIRES_KEYS = {"catalog", "runner"}


class Incompatible(str):
    """A manifest made for another version of the app: one plain sentence, listed apart from entries that can't be read."""
SERVICES = CATALOG / "services"
SERVICE_FIELDS = {"id", "name", "key", "enables", "get_key", "licence", "test", "costs"}
# What an entry may need, by kind: to get it (`needs_to_get`) or to run it (`needs_to_run`). A key that obtains gated
# weights is a need to get: a downloaded model runs without it where its engine allows.
NEED_KINDS = {"key", "licence", "download", "install", "weights", "platform", "memory", "data"}  # data:<entry id>
SOURCE_KEYS = {"hf", "revision", "git", "rev", "bioimageio", "package", "weights", "url", "made_here", "tool",
               "brainglobe"}  # brainglobe: a BrainGlobe atlas's own name; tool: the
# entry a binding runs in (Fiji's Analyze Particles: "fiji"), obtained with it
CREDIT_KEYS = {"authors", "link", "paper", "cite"}
CONTRIBUTOR_KEYS = {"name", "link"}  # who added the entry to a catalog (not its makers: those are `credit`)
TAG = re.compile(r"[a-z0-9][a-z0-9 -]{0,31}")  # a plain word or two the entry is about ("diatoms", "blood smear")
EXAMPLE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
# Where a file of a model's lives (orchestrator/readiness.py resolves each): "hf", the Hugging Face cache at the pinned
# revision (the default with an `hf` source); "cellpose" and "micro_sam", those packages' own model folders.
FILE_ROOTS = ("hf", "cellpose", "micro_sam")
# A file: its path in the model's folder, its bytes, and its content: `sha256`, or for a small file Hugging Face keeps in
# git, `git_sha1` (its git blob id, which Hugging Face lists and its cache names it by); `url` where it is fetched from
# when the source is not a Hugging Face repository; `root`, where an older cache keeps it (readiness.legacy); `from`, a
# companion file of another Hugging Face repository the model's library reads by that repository's name ({ hf, revision,
# path }: EdgeTAM's backbone config, timm's), kept in the model's folder and given to the library from there
# (gpu_worker/model_files.companions); `archive`, when its `url` is an archive (a zip) the file is one member of:
# { sha256, bytes } of the archive as published, and `member`, its path inside (Trackastra's weights come as a zip).
FILE_KEYS = {"path", "bytes", "sha256", "git_sha1", "url", "root", "from", "archive", "mirror"}
# A second place a file may be fetched from when its own is gone or down (C7), only with the exact rights under which it
# may be redistributed stated; the bytes must still be the pinned ones (its sha256), checked as any download's.
MIRROR_KEYS = {"url", "rights"}
COMPANION_KEYS = {"hf", "revision", "path"}
ARCHIVE_KEYS = {"sha256", "bytes", "member"}
# What an operation takes and gives, by id (catalog/README.md "Data types"): support is said exactly, and nothing is
# converted silently into the camera's picture.
DATA_TYPES = {
    "image.2d.rgb8": "the camera's view: 8-bit RGB, one plane",
    "image.2d.gray8": "an 8-bit grey plane",
    "image.2d.gray16": "a 16-bit plane (fluorescence, scientific cameras)",
    "image.3d.zyx.float32": "a volume: z, y, x",
    "clip.2d.rgb8": "camera frames over time",
    "masks.2d": "one mask for each thing asked about",
    "labels.instance.2d": "every instance found, one label each",
    "rois.imagej": "ImageJ ROIs",
    "table.objects": "one row per object, each column with its unit and definition",
    "tracks.graph": "observations linked over time, with divisions and merges",
    "vectors": "numbers describing a picture's look, one vector per picture",
    "links": "pairs of objects in two views judged the same, each with its score",
    "scores.text": "names ranked with scores",
    "points.2d": "points on the picture",
    "text": "words",
    "volume.3d": "a volume as it was stored: its three spatial axes, voxel sizes and orientation, each with its source",
    "registration.atlas": "a volume registered to an atlas: the atlas in its downsampled space, region volumes, the field",
}

# A new kind of job, without the app's code (C6; orchestrator/kinds.py): an entry with its own adapter may declare an
# operation of its own (`denoise`, `measure-intensity`) when it takes the camera's view and gives one of these, which the
# app already knows how to show, keep and hand to the agent.
KIND_OUTPUTS = {"image.2d.rgb8": "a picture", "table.objects": "a table", "text": "words", "points.2d": "points",
                "labels.instance.2d": "outlines", "tracks.graph": "tracks"}
KIND_INPUT = {"tracks.graph": "clip.2d.rgb8"}  # what a kind giving it takes; every other output takes the view (image.2d.rgb8)
# A new kind of job on one of the user's volumes (orchestrator/registrations.py's volumes): run as a job (jobs.run), its
# outputs files in its own folder, each named, checked and hashed by the app.
VOLUME_KIND_OUTPUTS = {"volume.3d": "a volume", "table.objects": "a table"}
KIND_ID = re.compile(r"[a-z][a-z0-9-]{1,40}")


@dataclass(frozen=True)
class Implements:
    """One operation contract a model fulfils (operations.OPERATIONS): the Operation's `returns` and output keys are the
    contract; `input` and `output` say exactly what this model takes and what its result means."""
    operation: str
    impl: str  # the implementation's id in that operation
    input: str  # a DATA_TYPES id
    output: str  # a DATA_TYPES id

    @property
    def key(self) -> str:
        return f"{self.operation}/{self.impl}"


# The capability contract (catalog format 2, ADR 0024; docs/design/2026-10-15-one-way-in.md). A capability declares what it
# takes and gives the way a ComfyUI node does (its INPUT_TYPES and RETURN_TYPES): one list of inputs, where data (a
# picture, outlines, a table) and parameters (a number, a choice) are both inputs, and a list of outputs. The app builds
# everything else from that one declaration: the form in Actions and Try, the agent's arguments, a researcher's, and the
# result's viewer. Types are open, as ComfyUI's are: the ones in DATA_TYPES are those the app shows and keeps in a place
# of its own; any other type is still a type, its values kept and handed on as data, so a new kind of thing never waits
# for the app to learn it.
TYPE = re.compile(r"[a-z][a-z0-9-]*(\.[a-z0-9-]+)*")  # a data type's id: "image.2d.rgb8", "mesh.obj", "spectra"
# Parameters (ComfyUI's widget types INT, FLOAT, STRING, BOOLEAN, COMBO; SciJava's @Parameter; Galaxy's <param>).
WIDGETS = {"int": "a whole number", "number": "a number", "string": "words", "bool": "yes or no", "choice": "one of a list"}
INPUT_KEYS = {"id", "type", "label", "tooltip", "optional", "default", "min", "max", "step", "options", "unit",
              "advanced", "multiline", "agent"}
OUTPUT_KEYS = {"id", "type", "label", "tooltip"}
INPUT_ID = re.compile(r"[a-z][a-z0-9_]{0,40}")
# What the app gives a capability without the user filling it in (ComfyUI's hidden inputs): asked for by name.
HIDDEN = {"pixel_size_um": "the picture's scale in µm per pixel, when the app knows it (else absent)",
          "objective": "the objective in use, as the user named it (else absent)",
          "times": "for a clip: each frame's time in seconds from the first"}
OUTCOME_KEYS = {"empty", "unsupported"}  # what "found none" means, and which inputs it refuses
DERIVED_KEYS = {"id", "version", "sha256", "channel"}  # the capability this one was made from (bioimage.io's `parent`)
TEST_KEYS = {"input", "params", "outcome", "count"}  # a pinned example and what it must give (Galaxy's <tests>); count:
# how many outlines, rows or points it gives there, when that is known exactly (an independent reference)
OUTCOMES = ("result", "empty", "unsupported", "failed")
# Parts of the contract that come later, said as such rather than as unknown keys, so a folder written for them is told
# what this app can't do yet (and a newer catalog format will run it without any change to the door).
RESERVED = {"axes": "axes beyond one 2D plane (z-stacks, channels, time)", "mode": "plane, volume or time processing",
            "steps": "human steps (select, points, inspect, correct)", "method": "saved methods"}
FORMAT_2_KEYS = {"inputs", "outputs", "operation", "category", "description", "search_aliases", "hidden", "outcomes",
                 "derived_from", "tests", "experimental", "fiji"}
# A Fiji function as a folder (runner "fiji", orchestrator/fiji_runner.py): the ImageJ command it runs and its options
# as the macro recorder writes them, each {param} a parameter (that recorder string is also its parity reference: the
# same command run as a macro in a Fiji of its own must give the same result); optionally a Groovy `script` beside the
# manifest run instead (with `imp` and `params`), still held to that command's result; `tolerance` per output with why.
FIJI_KEYS = {"command", "options", "script", "tolerance"}
FIJI_OUTPUTS = ("image.2d.rgb8", "table.objects")  # what a Fiji folder gives back for now


@dataclass(frozen=True)
class Input:
    """One input of a capability: data it is given (`type` a data type) or a parameter (`type` one of WIDGETS)."""
    id: str
    type: str
    label: str = ""
    tooltip: str = ""
    optional: bool = False
    default: object = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: tuple = ()
    unit: str = ""
    advanced: bool = False
    multiline: bool = False
    agent: bool = True  # false: only the user sets it (its form, Try), never the agent or a researcher

    @property
    def widget(self) -> bool:
        """A parameter (a form field), not data."""
        return self.type in WIDGETS

    @property
    def said(self) -> str:
        return self.label or self.id.replace("_", " ")


@dataclass(frozen=True)
class Output:
    id: str
    type: str
    label: str = ""
    tooltip: str = ""


@dataclass(frozen=True)
class Manifest:
    id: str  # the key the worker loads it by (gpu_worker/loading.py model_of); its folder's name
    name: str  # its one display name (orchestrator/names.py)
    runs: str
    version: str  # its weights: a repository or what names them
    licence: tuple[str, ...]  # orchestrator/licences.py COMPONENTS
    kind: str = "model"
    what: str = ""  # what it does, at most 12 words
    runner: str = ""  # a catalog/runners id; "" with an adapter of its own (a new architecture)
    service: str = ""  # a catalog/services id, for a cloud model
    source: dict = field(default_factory=dict)  # where it comes from, pinned (SOURCE_KEYS)
    files: tuple[dict, ...] = ()  # what "downloaded" means: each file's path, bytes and sha256
    pins: dict = field(default_factory=dict)  # adapter, environment, preprocessing: pinned as the weights are
    needs_to_get: tuple[str, ...] = ()  # "kind:what" (NEED_KINDS): obtaining it
    needs_to_run: tuple[str, ...] = ()  # "kind:what": running it
    credit: dict = field(default_factory=dict)  # authors, link, paper, cite: shown wherever its result is
    check: str = ""  # its check file, beside the manifest (the app's own probes live in orchestrator/model_checks.py)
    implements: tuple[Implements, ...] = ()
    files_from: str = ""  # another model whose files these are (EdgeTAM video runs EdgeTAM's): one folder, one download
    workers: tuple[str, ...] = ()  # the worker classes that load it (operations.Impl.worker)
    host: str = "shared"  # shared: with other models in a worker; own: a worker of its own; own-python: a Python of its own
    documented_mb: int = 0  # a first guess of its memory, until this Mac measures it (models/loads.json)
    load_limit_s: float = 0.0  # how long its load may take before its worker counts as hung
    protected_on: tuple[str, ...] = ()  # worker classes where it is never let go of (the live view's)
    peak_mb: int = 0  # the most its load takes, measured where it could be (bench/footprint_bench.py); 0: its size
    namespace: str = ""  # "mine" for the user's own entries, a community channel's name for its; "" for reviewed ones
    folder: str = ""  # the folder its manifest was read from (entry_folder); never in a manifest
    channel: str = ""  # the channel it came through ("" when the app bundles it or it is the user's own)
    licence_note: str = ""  # the maker's own licence words, as they give them (a user's entry: "CC-BY-NC-4.0, a placeholder")
    # The Python of its own the app makes for it (orchestrator/envs.py; phase 3's E2): `python` (the version), `lock`
    # (a lockfile with every package's hash, beside this manifest) and `imports` (what its proof imports there).
    env: dict = field(default_factory=dict)
    # runner "python-env" (orchestrator/env_runner.py; phase 3's E4): its adapter, `file:function` in its own folder (a
    # reviewed file beside this manifest), run only inside its environment; and, when its licence is not one the app
    # already lists, the component it declares (what, licence, url, tier), registered under its own id.
    adapter: str = ""
    component: dict = field(default_factory=dict)
    # What it is made for (CATALOG_FORMAT, RUNNER_CONTRACTS): `catalog` and `runner`, each a version number; 1 when left out.
    requires: dict = field(default_factory=dict)
    # For the people turning it on (docs/design/2026-10-13-capabilities-for-everyone.md): what it is about (`tags`: plain
    # words, matched against what a session looks at, so a new entry that fits is offered first), who added it to the
    # catalog (`contributed_by`: name and link, said on its card), and a picture of it working (`example`: a file beside
    # this manifest, shown on its card and in "New for you"). None of them changes what runs.
    tags: tuple[str, ...] = ()
    contributed_by: dict = field(default_factory=dict)
    example: str = ""
    # The capability contract (catalog format 2; ADR 0024): what it takes and gives, ComfyUI's way. `operation`: the job
    # it does (its own id when left out, or an app operation it is a choice of, such as "cells"); `category`: where it
    # is listed ("Measure", "Filters/Denoise"); `hidden`: what the app gives it unasked (HIDDEN); `outcomes`: what an
    # empty result means and what it refuses; `derived_from`: the capability it was made from; `tests`: pinned examples.
    inputs: tuple[Input, ...] = ()
    outputs: tuple[Output, ...] = ()
    operation: str = ""
    category: str = ""
    description: str = ""
    search_aliases: tuple[str, ...] = ()
    hidden: tuple[str, ...] = ()
    outcomes: dict = field(default_factory=dict)
    derived_from: dict = field(default_factory=dict)
    tests: tuple[dict, ...] = ()
    experimental: bool = False
    fiji: dict = field(default_factory=dict)  # runner "fiji": its command, options (the recorder's), script, tolerance

    members: tuple[dict, ...] = ()

    @property
    def contract(self) -> bool:
        """Written in the capability contract (format 2): its inputs and outputs are declared."""
        return bool(self.outputs)

    @property
    def params(self) -> tuple[Input, ...]:
        """Its parameters: the inputs a person or the agent fills in."""
        return tuple(i for i in self.inputs if i.widget)

    @property
    def data_inputs(self) -> tuple[Input, ...]:
        return tuple(i for i in self.inputs if not i.widget)

    @property
    def key(self) -> str:
        """How it is named everywhere: its id, or `mine/<id>` for the user's own."""
        return f"{self.namespace}/{self.id}" if self.namespace else self.id

    @property
    def implementations(self) -> tuple[str, ...]:
        """"operation/implementation" it runs (operations.OPERATIONS)."""
        return tuple(i.key for i in self.implements)

    @property
    def installed(self) -> str:
        """The operations.INSTALLED key of code that must be installed here to run it ("" when none)."""
        return _first(self.needs_to_run, "install")

    @property
    def needs(self) -> str:
        """An environment key it cannot run without ("" when none); a key only for getting it is not one."""
        return _first(self.needs_to_run, "key")

    def keys(self) -> set[str]:
        """Every environment key it names, to get it or to run it."""
        return {need.split(":", 1)[1] for need in (*self.needs_to_get, *self.needs_to_run) if need.startswith("key:")}


def _first(needs: tuple[str, ...], kind: str) -> str:
    return next((need.split(":", 1)[1] for need in needs if need.startswith(kind + ":")), "")


FIELDS = {f.name for f in fields(Manifest)} - {"namespace", "folder", "channel"}
TUPLES = ("licence", "workers", "protected_on", "needs_to_get", "needs_to_run", "files", "tags", "search_aliases",
          "hidden", "tests", "members")


def read(path: Path, namespace: str = "", channel: str = "") -> tuple[Manifest | None, list[str]]:
    """One entry's manifest, or None with its problems in plain words (never raises: the rest of the catalog loads)."""
    try:
        return _read(path, namespace, channel)
    except (TypeError, ValueError, AttributeError) as exc:
        return None, [f"{MANIFEST} has invalid field values: {exc}"]


def _read(path: Path, namespace: str, channel: str) -> tuple[Manifest | None, list[str]]:
    folder = path.parent.name
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, UnicodeDecodeError) as exc:
        return None, [f"{MANIFEST} can't be read: {exc}"]
    except tomllib.TOMLDecodeError as exc:
        return None, [f"{MANIFEST} is not valid TOML: {exc}"]
    why = incompatible(data)
    if why:
        return None, [Incompatible(why)]
    problems = [f"`{key}` is reserved for {RESERVED[key]}: not supported by this app yet" if key in RESERVED
                else f"unknown key `{key}`" for key in sorted(set(data) - FIELDS)]
    if data.get("kind") == "method":
        problems.append(f"kind \"method\" is reserved for {RESERVED['method']}: not supported by this app yet")
    contract_keys = sorted(FORMAT_2_KEYS & set(data))
    fmt = (data.get("requires") or {}).get("catalog", 1) if isinstance(data.get("requires"), dict) else 1
    if fmt in (3, 4):
        contract_keys = [key for key in contract_keys if key != "derived_from"]
    if "members" in data and fmt not in (3, 4):
        problems.append("bundle members require catalog format 3")
    if fmt in (3, 4) and (not data.get("members") or contract_keys or data.get("adapter") or data.get("implements")):
        problems.append("a format-3 bundle declares members; only members declare adapters, inputs, outputs and operations")
    if contract_keys and fmt != 2:
        problems.append(f"{', '.join(f'`{k}`' for k in contract_keys)} belong to the capability contract: declare "
                        "`requires = { catalog = 2 }`, so an older app says it needs an update instead of misreading it")
    if contract_keys and data.get("implements"):
        problems.append("a capability declares its `inputs` and `outputs` (format 2) or `implements` (format 1), not "
                        "both")
    for key in ("id", "name", "runs", "version", "licence"):
        if key not in data:
            problems.append(f"`{key}` is missing")
    for key in TUPLES:
        if key in data and not isinstance(data[key], list):
            problems.append(f"`{key}` must be a list")
    if 'contributed_by' in data and not isinstance(data['contributed_by'], dict):
        problems.append('`contributed_by` must be a table with name and link')
    if 'example' in data and not isinstance(data['example'], str):
        problems.append('`example` must be a picture filename')
    if problems:
        return None, problems
    data = {key: value for key, value in data.items() if key in FIELDS}
    for key in TUPLES:
        data[key] = tuple(data.get(key) or ())
    inputs, outputs = _ports(data.pop("inputs", None), Input, INPUT_KEYS, "inputs", problems), \
        _ports(data.pop("outputs", None), Output, OUTPUT_KEYS, "outputs", problems)
    if problems:
        return None, problems
    implements = []
    if outputs:  # the contract, said once more as the operation contract the runner reads (format 1's implements)
        first = next((i.type for i in inputs if not i.widget), "")
        implements.append(Implements(str(data.get("operation") or data["id"]), str(data["id"]), first, outputs[0].type))
    for n, entry in enumerate(data.pop("implements", None) or (), 1):
        if not isinstance(entry, dict) or set(entry) != {"operation", "impl", "input", "output"}:
            problems.append(f"`implements` {n} needs exactly operation, impl, input and output")
            continue
        implements.append(Implements(**{key: str(value) for key, value in entry.items()}))
    try:
        manifest = Manifest(**data, implements=tuple(implements), inputs=inputs, outputs=outputs, namespace=namespace,
                            folder=str(path.parent), channel=channel)
    except TypeError as exc:
        return None, [f"{MANIFEST}: {exc}"]
    if manifest.members:
        from .bundle_contract import parse_members
        try:
            parse_members(list(manifest.members), manifest)
        except ValueError as exc:
            problems.append(str(exc))
    problems += _problems(manifest, folder)
    return (None if problems else manifest), problems


def _ports(raw, cls, keys: set[str], where: str, problems: list[str]) -> tuple:
    """A contract's inputs or outputs, each a table ([[inputs]]); what can't be read said in problems."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        problems.append(f"`{where}` is a list of tables ([[{where}]])")
        return ()
    out = []
    for n, entry in enumerate(raw, 1):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("type"), str):
            problems.append(f"`{where}` {n} needs its id and its type")
            continue
        if set(entry) - keys:
            problems.append(f"`{where}` {entry['id']}: unknown keys {sorted(set(entry) - keys)} (it takes "
                            f"{', '.join(sorted(keys))})")
            continue
        if "options" in entry:
            entry = {**entry, "options": tuple(entry["options"]) if isinstance(entry["options"], list) else entry["options"]}
        out.append(cls(**entry))
    return tuple(out)


def incompatible(data: dict) -> str:
    """Why this app can't run what a manifest is made for ("" when it can), read before anything else in it: a newer
    format may hold keys this app doesn't know."""
    requires = data.get("requires") or {}
    if not isinstance(requires, dict):
        return ""  # said as a problem of its own (_problems)
    fmt, contract, runner = requires.get("catalog", 1), requires.get("runner", 1), data.get("runner") or ""
    if isinstance(fmt, int) and fmt > CATALOG_FORMAT:
        return (f"needs a newer ObservationUI: it is written in catalog format {fmt}, and this app reads format "
                f"{CATALOG_FORMAT}")
    if isinstance(contract, int) and runner in RUNNER_CONTRACTS:
        oldest, newest = RUNNER_CONTRACTS[runner]
        if contract > newest:
            return (f"needs a newer ObservationUI: it is made for version {contract} of the {runner} runner, and this "
                    f"app runs version {newest}")
        if contract < oldest:
            return (f"made for an older {runner} runner (version {contract}): this app runs versions {oldest} to "
                    f"{newest}, so it needs an update from whoever made it")
    return ""


def _problems(m: Manifest, folder: str) -> list[str]:
    out = []
    if m.id != folder:
        out.append(f"its id is {m.id!r}, its folder {folder!r}")
    if m.kind not in KINDS:
        out.append(f"kind {m.kind!r} is none of {', '.join(KINDS)}")
    if m.runs not in RUNS:
        out.append(f"runs {m.runs!r} is none of {', '.join(RUNS)}")
    if m.runner and m.runner not in RUNNERS:
        out.append(f"runner {m.runner!r} is no runner in catalog/runners ({', '.join(RUNNERS)})")
    if len(m.what.split()) > 12:
        out.append("`what` is longer than 12 words")
    if not m.licence:
        out.append("`licence` names no component")
    for need in (*m.needs_to_get, *m.needs_to_run):
        kind, _, what = need.partition(":")
        if kind not in NEED_KINDS or (kind != "download" and not what):
            out.append(f"need {need!r} is not kind:what with a kind of {', '.join(sorted(NEED_KINDS))}")
        if kind == "memory":
            try:
                memory = float(what)
            except ValueError:
                memory = float('nan')
            if not math.isfinite(memory) or memory <= 0:
                out.append(f"need {need!r} must declare a positive finite memory amount in GB")
    if "download" in m.needs_to_get and not m.files and not m.files_from:
        # Live 2026-10-12 (the catalog audit): Trackastra said "download" and pinned nothing, so nothing downloaded it
        # and its library fetched its own weights on first use, unpinned, outside the catalog.
        out.append("it needs a download and pins no files: list them (`files`), so the app fetches and checks them")
    if set(m.source) - SOURCE_KEYS:
        out.append(f"source keys {sorted(set(m.source) - SOURCE_KEYS)} are none of {', '.join(sorted(SOURCE_KEYS))}")
    if m.source.get("hf") and not m.source.get("revision"):
        out.append("a Hugging Face source must pin its revision")
    for f in m.files:
        if not isinstance(f, dict) or set(f) - FILE_KEYS or not isinstance(f.get("path"), str) \
                or not isinstance(f.get("bytes"), int) or f["bytes"] <= 0 \
                or not (len(str(f.get("sha256", ""))) == 64 or len(str(f.get("git_sha1", ""))) == 40):
            out.append(f"file {f!r} needs a path, its bytes and its sha256 or git_sha1 (and a root of {', '.join(FILE_ROOTS)})")
        elif "from" in f and (not isinstance(f["from"], dict) or set(f["from"]) != COMPANION_KEYS
                              or len(str(f["from"].get("revision", ""))) != 40):
            out.append(f"file {f['path']}: `from` needs the companion's hf repository, its pinned revision and its path")
        elif "mirror" in f and (not isinstance(f["mirror"], dict) or set(f["mirror"]) != MIRROR_KEYS
                                or not str(f["mirror"].get("url", "")).startswith("https://")
                                or len(str(f["mirror"].get("rights", "")).strip()) < 20 or len(str(f.get("sha256", ""))) != 64):
            out.append(f"file {f['path']}: `mirror` needs its https `url` and the exact `rights` under which it may be "
                       "redistributed (a sentence with the licence), and the file's own sha256")
        elif "archive" in f and (not isinstance(f["archive"], dict) or set(f["archive"]) != ARCHIVE_KEYS
                                 or len(str(f["archive"].get("sha256", ""))) != 64 or not f.get("url")):
            out.append(f"file {f['path']}: `archive` needs the archive's sha256, its bytes and the member's path, with "
                       "the archive's `url`")
        elif not m.source.get("hf") and not f.get("url") and "from" not in f:
            out.append(f"file {f['path']}: where it comes from (`url`, or the model's Hugging Face source)")
        elif f.get("root", "hf") not in FILE_ROOTS or (f.get("root", "hf") == "hf" and not m.source.get("hf")
                                                         and "from" not in f and not f.get("url")):
            out.append(f"file {f['path']}: root {f.get('root', 'hf')!r} (a Hugging Face file needs an hf source)")
    if m.members and not any(n == f"install:{m.id}" or n == f"install:{m.key}" for n in m.needs_to_run):
        out.append("a bundle requires its own installation before any member runs")
    if m.runner == "python-env" and m.members:
        if not m.env:
            out.append("a bundle names one environment for all its members")
    elif m.runner == "python-env":
        if not m.env or not m.adapter or ":" not in m.adapter:
            out.append("a python-env entry names its environment (`env`) and its adapter (`adapter = \"file:function\"`)")
        elif m.adapter.split(":")[0].endswith((".py",)) or "/" in m.adapter.split(":")[0]:
            out.append("`adapter` is a module beside the manifest and its function: `adapter:segment`, not a path")
        if not m.implements:
            out.append("a python-env entry says which operation it implements (`[[implements]]`)")
    if not isinstance(m.requires, dict) or set(m.requires) - REQUIRES_KEYS or not all(
            isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in m.requires.values()):
        out.append("`requires` takes catalog and runner, each a version number")
    if m.component and (set(m.component) - COMPONENT_KEYS or m.component.get("tier") not in ("open", "terms", "noncommercial")
                        or not all(m.component.get(k) for k in ("what", "licence", "url"))):
        out.append("`component` needs what, licence, url and a tier (open, terms or noncommercial)")
    if m.env and (set(m.env) - ENV_KEYS or not m.env.get("python") or not m.env.get("lock")
                  or not isinstance(m.env.get("imports", []), list)):
        out.append("`env` needs its python version and its lockfile (and the imports its proof runs)")
    if 'wheels' in (m.env or {}):
        from .wheel_recipe import declared
        if not isinstance(m.requires, dict) or m.requires.get('catalog') != 4:
            out.append('portable wheels require catalog format 4')
        try:
            declared(m.env)
        except ValueError as error:
            out.append(str(error))
    for n in (m.env or {}).get("native", []) if isinstance((m.env or {}).get("native", []), list) else [None]:
        if not isinstance(n, dict) or set(n) != NATIVE_KEYS or len(str(n.get("sha256", ""))) != 64 \
                or not str(n.get("url", "")).startswith("https://") or not isinstance(n.get("bytes"), int) \
                or not str(n.get("url", "")).endswith(".tar.bz2"):
            out.append(f"native package {n!r} needs its name, https url (its .tar.bz2 build: the app unpacks it "
                       "itself), bytes and sha256")
    if not isinstance((m.env or {}).get("commands", []), list):
        out.append("`env.commands` is a list of commands its proof runs")
    if m.kind == "data" and (m.runner or m.implements or not m.files):
        out.append("a data entry pins its files and runs nothing (no runner, no implements)")
    if len(m.tags) > 12 or any(not isinstance(t, str) or not TAG.fullmatch(t) for t in m.tags):
        out.append("`tags` are at most 12 plain lowercase words (letters, digits, spaces, dashes; 32 characters each)")
    if set(m.contributed_by) - CONTRIBUTOR_KEYS or (m.contributed_by and not m.contributed_by.get("name")):
        out.append("`contributed_by` is a name and optionally a link")
    if m.example:
        where = Path(m.folder) / m.example
        if (Path(m.example).is_absolute() or ".." in Path(m.example).parts
                or not m.example.lower().endswith(EXAMPLE_SUFFIXES) or not where.is_file()):
            out.append(f"`example` is a picture beside its manifest ({', '.join(EXAMPLE_SUFFIXES)}): {m.example!r} isn't")
    if set(m.credit) - CREDIT_KEYS:
        out.append(f"credit keys {sorted(set(m.credit) - CREDIT_KEYS)} are none of {', '.join(sorted(CREDIT_KEYS))}")
    out += _contract_problems(m) if m.contract else []
    out += _lineage_problems(m) if m.contract or m.members else []
    for i in (m.implements if not m.contract else ()):
        for side in ("input", "output"):
            if getattr(i, side) not in DATA_TYPES:
                out.append(f"`implements` {i.key}: {side} {getattr(i, side)!r} is no data type (catalog/README.md)")
        volume_kind = i.input == "volume.3d" and i.output in VOLUME_KIND_OUTPUTS
        if volume_kind and m.runner == "python-env" and not KIND_ID.fullmatch(i.operation):
            out.append(f"`implements` {i.key}: its operation is a short lowercase word with hyphens ({i.operation!r})")
        if i.output in KIND_OUTPUTS and m.runner == "python-env" and not volume_kind:  # a new kind of job (kinds.py)
            takes = KIND_INPUT.get(i.output, "image.2d.rgb8")
            if i.input != takes:
                out.append(f"`implements` {i.key}: a new kind of job giving {i.output} takes {takes}")
            if not KIND_ID.fullmatch(i.operation):
                out.append(f"`implements` {i.key}: its operation is a short lowercase word with hyphens ({i.operation!r})")
    if m.kind == "cloud-model" or m.runs == "cloud":
        service = services().get(m.service)
        if m.runs != "cloud" or m.kind != "cloud-model":
            out.append("a cloud model is kind cloud-model and runs cloud")
        if service is None:
            out.append(f"service {m.service!r} is no catalog/services entry")
        elif f"key:{service.key}" not in m.needs_to_run:
            out.append(f"a {service.name} model needs its key to run: key:{service.key}")
    if m.runs == "this-mac" and m.kind not in ("tool", "tool-function", "data") and m.runner != "python-env":  # own process
        if not m.workers or set(m.workers) - set(WORKERS):
            out.append(f"workers {list(m.workers)} must be some of {', '.join(WORKERS)}")
        if m.host not in HOSTS:
            out.append(f"host {m.host!r} is none of {', '.join(HOSTS)}")
        if set(m.protected_on) - set(m.workers):
            out.append("protected on a worker it never runs on")
        if m.documented_mb <= 0 or m.load_limit_s <= 0:
            out.append("a model on this Mac needs documented_mb and load_limit_s")
        if m.peak_mb and m.peak_mb < m.documented_mb:
            out.append("peak_mb is below documented_mb")
    return out


def _lineage_problems(m: Manifest) -> list[str]:
    out = []
    if m.derived_from and (not isinstance(m.derived_from, dict) or set(m.derived_from) - DERIVED_KEYS
                           or not m.derived_from.get("id") or not m.derived_from.get("version")):
        out.append("`derived_from` names the capability it was made from: its id and version (and its sha256)")
    elif m.derived_from and m.derived_from.get("id") == m.id:
        out.append("`derived_from` is another capability: a derived version has an id of its own")
    return out


def _contract_problems(m: Manifest) -> list[str]:
    """Format 2: its inputs, outputs and parameters, as ComfyUI's Schema.validate checks a node's, and what it says
    about outcomes, lineage and tests."""
    out = []
    ids = [p.id for p in (*m.inputs, *m.outputs)]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        out.append(f"input and output ids are each used once: {dup!r} isn't")
    for p in (*m.inputs, *m.outputs):
        if not INPUT_ID.fullmatch(p.id):
            out.append(f"{p.id!r}: an input's or output's id is a short lowercase name (letters, digits, underscores)")
        if not TYPE.fullmatch(p.type):
            out.append(f"{p.id}: its type {p.type!r} is a lowercase id such as image.2d.rgb8 (or a parameter: "
                       f"{', '.join(WIDGETS)})")
    for o in m.outputs:
        if o.type in WIDGETS:
            out.append(f"output {o.id}: a parameter type ({o.type}) is an input's, not an output's")
    if not m.runner:
        out.append("a capability names its runner (python-env, fiji...)")
    if m.runner == "python-env" and not any(n.startswith("install:") and n.rsplit("/", 1)[-1].removeprefix("install:") == m.id
                                            for n in m.needs_to_run):
        out.append(f"a capability with its own adapter needs its install to run: needs_to_run = [\"install:{m.id}\"] "
                   f"(yours: install:mine/{m.id})")
    if m.operation and not KIND_ID.fullmatch(m.operation):
        out.append(f"its operation is a short lowercase word with hyphens ({m.operation!r})")
    if not KIND_ID.fullmatch(m.id):
        out.append(f"a capability's id is a short lowercase word with hyphens ({m.id!r}): it names its operation")
    for p in m.params:
        out += _param_problems(p)
        if p.id in HIDDEN:
            out.append(f"parameter {p.id}: {p.id!r} is what the app gives unasked (`hidden`), not a parameter's name")
    for p in m.data_inputs:
        if p.default is not None or p.options or p.min is not None or p.max is not None:
            out.append(f"input {p.id}: data ({p.type}) has no default, options or range: those are a parameter's")
    for h in m.hidden:
        if h not in HIDDEN:
            out.append(f"hidden {h!r} is none of what the app gives unasked: {', '.join(HIDDEN)}")
    if not isinstance(m.outcomes, dict) or set(m.outcomes) - OUTCOME_KEYS or not all(
            isinstance(v, str) and 0 < len(v) <= 200 for v in m.outcomes.values()):
        out.append("`outcomes` says, in a sentence each, what an `empty` result means and what it refuses (`unsupported`)")
    for n, t in enumerate(m.tests, 1):
        given = Path(m.folder) / str(t.get("input", "")) if isinstance(t, dict) else None
        if (not isinstance(t, dict) or set(t) - TEST_KEYS or t.get("outcome", "result") not in OUTCOMES
                or not isinstance(t.get("params", {}), dict) or not isinstance(t.get("input"), str)
                or Path(t["input"]).name != t["input"] or not t["input"].startswith("example")
                or not t["input"].lower().endswith(EXAMPLE_SUFFIXES) or not given.is_file()):
            out.append(f"test {n} names a picture beside the manifest whose name starts with example (`input`, "
                       f"example-*.png: it travels when the capability is shared), its `params` and the `outcome` it "
                       f"gives ({', '.join(OUTCOMES)})")
        elif "count" in t and (isinstance(t["count"], bool) or not isinstance(t["count"], int) or t["count"] < 0):
            out.append(f"test {n}: count is how many it gives there, a whole number")
        elif isinstance(t.get("params"), dict):
            known = {p.id for p in m.params}
            if set(t["params"]) - known:
                out.append(f"test {n}: params {sorted(set(t['params']) - known)} are none of its parameters")
    out += _fiji_problems(m)
    if len(m.search_aliases) > 12 or any(not isinstance(a, str) or not 0 < len(a) <= 40 for a in m.search_aliases):
        out.append("`search_aliases` are at most 12 short names")
    if len(m.description) > 1000:
        out.append("`description` is at most 1000 characters")
    if m.category and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 &,-]*(/[A-Za-z0-9][A-Za-z0-9 &,-]*)*", m.category):
        out.append(f"`category` is a path of plain words, such as \"Measure\" or \"Filters/Denoise\" ({m.category!r})")
    return out


def _fiji_problems(m: Manifest) -> list[str]:
    """A Fiji function folder: its command and the recorder's options, every {placeholder} one of its parameters."""
    if m.runner != "fiji":
        return ["`fiji` is a Fiji function's (runner \"fiji\")"] if m.fiji else []
    f, out = m.fiji, []
    # A built-in catalog may declare an existing app-owned adapter without a
    # function script. Discovery must match that exact adapter before exposing it.
    # User contributions still require their own confined function declaration.
    if not f and not m.namespace:
        return []
    if not isinstance(f, dict) or set(f) - FIJI_KEYS or not isinstance(f.get("command"), str) or not f.get("command") \
            or not isinstance(f.get("options", ""), str):
        return ["a Fiji function names its ImageJ `command` and its `options` as the macro recorder writes them "
                "([fiji] command = \"Gaussian Blur...\", options = \"sigma={sigma}\")"]
    used = set(re.findall(r"{(\w+)}", f.get("options", "")))
    params = {p.id for p in m.params}
    if used - params:
        out.append(f"[fiji] options name {sorted(used - params)}, none of its parameters")
    if params - used:
        out.append(f"[fiji] options leave out {sorted(params - used)}: every parameter reaches the command")
    if any(c in f["command"] for c in '"\n\r\\'):
        out.append("[fiji] command is a menu command's name, as the recorder writes it")
    if f.get("script"):
        where = Path(m.folder) / str(f["script"])
        if (not str(f["script"]).endswith(".groovy") or Path(str(f["script"])).name != str(f["script"])
                or where.is_symlink() or not where.is_file()):
            out.append("[fiji] script is a .groovy file beside the manifest")
    tol = f.get("tolerance", {})
    if not isinstance(tol, dict) or any(not isinstance(v, dict) or set(v) != {"max", "why"}
                                        or not isinstance(v["max"], (int, float)) or len(str(v["why"])) < 20
                                        for v in tol.values()) or set(tol) - {o.id for o in m.outputs}:
        out.append("[fiji] tolerance is per output: { max = <largest difference>, why = \"a sentence on why it can't "
                   "repeat exactly\" }")
    if m.kind != "tool-function":
        out.append("a Fiji function is kind \"tool-function\"")
    if len([d for d in m.data_inputs]) != 1 or m.data_inputs[0].type != "image.2d.rgb8":
        out.append("a Fiji function takes the view (one image.2d.rgb8 input) for now")
    if len(m.outputs) != 1 or m.outputs[0].type not in FIJI_OUTPUTS:
        out.append(f"a Fiji function gives one of {', '.join(FIJI_OUTPUTS)} for now")
    if not {f"install:{m.id}", f"install:{m.key}"}.intersection(m.needs_to_run):
        out.append(f"a Fiji function needs its proof on the Fiji in use: needs_to_run = [\"install:{m.id}\"]")
    return out


def _param_problems(p: Input) -> list[str]:
    out, said = [], f"parameter {p.id}"
    numeric = p.type in ("int", "number")
    if (p.min is not None or p.max is not None or p.step is not None) and not numeric:
        out.append(f"{said}: min, max and step are a number's")
    for k in ("min", "max", "step"):
        v = getattr(p, k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
            out.append(f"{said}: {k} is a number")
        elif v is not None and not _finite(v):  # with max = nan every value passes (each comparison with NaN is False)
            out.append(f"{said}: {k} is a finite number")
    if p.min is not None and p.max is not None and not isinstance(p.min, bool) and not isinstance(p.max, bool) \
            and isinstance(p.min, (int, float)) and isinstance(p.max, (int, float)) and p.min > p.max:
        out.append(f"{said}: min is above max")
    if p.type == "choice":
        if not p.options or not isinstance(p.options, tuple) or not all(
                isinstance(o, (str, int, float)) and not isinstance(o, bool) for o in p.options) or len(set(p.options)) != len(p.options):
            out.append(f"{said}: a choice lists its options (different words or numbers)")
    elif p.options:
        out.append(f"{said}: options are a choice's")
    if not isinstance(p.agent, bool):
        out.append(f"{said}: agent is true or false (false: only the user sets it)")
    if p.multiline and p.type != "string":
        out.append(f"{said}: multiline is for words")
    if not p.optional and p.default is None:
        out.append(f"{said}: a parameter that must be given has a default (said in its form), or is optional")
    if p.default is not None:
        why = (f"is none of its {len(p.options)} choices" if p.type == "choice" and p.default not in p.options
               else check_value(p, p.default))
        if why:
            out.append(f"{said}: its default {why}")
    return out


def _finite(v) -> bool:
    """A number a float can hold: never NaN or infinity, and a whole number within a float's range (a bigger one raised
    OverflowError wherever it was made a float)."""
    return abs(v) <= sys.float_info.max if isinstance(v, int) else math.isfinite(v)


def check_value(p: Input, value) -> str:
    """Why a value isn't one this parameter takes ("" when it is): the same check for its default, the form, the agent
    and a researcher."""
    if p.type == "bool":
        return "" if isinstance(value, bool) else "is yes or no (true or false)"
    if p.type in ("int", "number"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not _finite(value)):
            return "is a number"
        if p.type == "int" and isinstance(value, float) and not value.is_integer():  # an int is whole, of any size
            return "is a whole number"
        if p.min is not None and value < p.min:  # an int compares with a float exactly, however big
            return f"is at least {p.min:g}"
        if p.max is not None and value > p.max:
            return f"is at most {p.max:g}"
        return "" if _finite(value) else "is too large a number"
    if p.type == "choice":
        # Never its options' words (its makers', told to the models only inside the untrusted wrapper): how many.
        return "" if value in p.options else f"is none of its {len(p.options)} choices (its parameters, as listed, say them)"
    if p.type == "string":
        return "" if isinstance(value, str) and len(value) <= 2000 else "is words (at most 2000 characters)"
    return "is data, not a value"


def params_given(m: Manifest, given: dict | None, *, agent: bool = False) -> tuple[dict, list[str]]:
    """Its parameters' values for one run: each one given checked, each one not given its default (an optional one with
    no default left out), and anything that isn't one of its parameters refused. `agent`: given by the agent or a
    researcher, who may not set a user-only parameter (`agent = false`). Returns (values, problems)."""
    given = dict(given or {})
    values, problems = {}, []
    known = {p.id: p for p in m.params}
    for key in sorted(set(given) - set(known)):
        problems.append(f"{key!r} is none of its parameters ({', '.join(known) or 'it has none'})")
    if agent:
        for key in sorted(k for k in given if k in known and not known[k].agent):
            problems.append(f"only the user sets {known[key].said} (in its form)")
    for p in m.params:
        if p.id in given and given[p.id] is not None:
            value = given[p.id]
            if p.type == "int" and isinstance(value, float) and value.is_integer():
                value = int(value)
            why = check_value(p, value)
            if why:
                problems.append(f"{p.said} {why}")
            else:
                values[p.id] = value
        elif p.default is not None:
            values[p.id] = p.default
        elif not p.optional:
            problems.append(f"{p.said} is needed")
    return values, problems


@dataclass(frozen=True)
class Service:
    """A cloud account with a key (catalog/services/<id>.toml): its key's name, where to get one, how it is tested and
    what calls cost. Keys themselves are never here (keys.py)."""
    id: str
    name: str
    key: str  # the environment key's name
    enables: str = ""  # what its key turns on, in plain words (the Add key sheet, the brain): from the catalog, never a guess
    get_key: str = ""  # where to get one
    licence: str = ""  # its orchestrator/licences.py component ("" when it has none)
    test: str = ""  # the one cheap call that tells whether a key works
    costs: str = ""


ENV_KEYS = {"python", "lock", "imports", "native", "commands", "wheels"}
# A native recipe (`native`): what a lockfile can't hold (NiftyReg's binaries and the libraries they load), as pinned
# conda-forge packages, each by its URL, bytes and sha256, unpacked into the environment (envs.py); `commands`: what its
# proof runs from the environment's bin (each must exit 0).
NATIVE_KEYS = {"name", "url", "bytes", "sha256"}
COMPONENT_KEYS = {"what", "licence", "url", "tier"}


def entry_folder(m: "Manifest", environ=os.environ) -> Path:
    """The folder its manifest is in: the one it was read from (the catalog's catalog/models/<id>, the user's own
    mine/models/<id>, a channel's snapshot, an installed copy), else where its kind is kept."""
    if m.folder:
        return Path(m.folder)
    return mine_root(environ) / m.id if m.namespace == "mine" else ROOT / m.id


def short_revision(m: "Manifest") -> str:
    """A revision as people read it: a Hugging Face commit's first 7 characters, or of the hash a files-only revision is
    named by ("files-3b6f6a3…" is 3b6f6a3, never the cut-off "files-3")."""
    rev = revision_of(m)
    return rev.removeprefix("files-")[:7]


@lru_cache(maxsize=1)
def services() -> dict[str, Service]:
    """The catalog's cloud services, by id (a file that can't be read is left out and said by `service_problems`)."""
    return {s.id: s for s in (_service(path)[0] for path in sorted(SERVICES.glob("*.toml"))) if s is not None}


def service_problems() -> dict[str, list[str]]:
    return {path.stem: why for path in sorted(SERVICES.glob("*.toml")) if (why := _service(path)[1])}


def _service(path: Path) -> tuple[Service | None, list[str]]:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return None, [f"{path.name} can't be read: {exc}"]
    problems = [f"unknown key `{key}`" for key in sorted(set(data) - SERVICE_FIELDS)]
    problems += [f"`{key}` is missing" for key in ("id", "name", "key") if not data.get(key)]
    if not problems and data["id"] != path.stem:
        problems.append(f"its id is {data['id']!r}, its file {path.stem!r}")
    return (None, problems) if problems else (Service(**data), [])


def _scan(root: Path, namespace: str, channel: str = "") -> tuple[dict[str, Manifest], dict[str, list[str]]]:
    found, problems = {}, {}
    for path in sorted(root.glob(f"*/{MANIFEST}")) if root.is_dir() else ():
        manifest, why = read(path, namespace, channel)
        key = f"{namespace}/{path.parent.name}" if namespace else path.parent.name
        if why:
            problems[key] = why
        if manifest is not None:
            found[manifest.id] = manifest
    return found, problems


def catalog_root(environ=os.environ) -> Path:
    """This Mac's catalog folder: MICROMIND_CATALOG, else ~/MicroscopeMind/catalog (MICROMIND_ASSETS moves it). It holds
    the models' downloaded revisions (models/<id>/<revision>/) and the user's own entries (mine/models/<id>/)."""
    root = environ.get("MICROMIND_CATALOG") or Path(environ.get("MICROMIND_ASSETS") or Path.home() / "MicroscopeMind") / "catalog"
    return Path(root).expanduser()


def mine_root(environ=os.environ) -> Path:
    """Where the user's own entries are: <catalog>/mine/models."""
    return catalog_root(environ) / "mine" / "models"


def revision_of(m: "Manifest") -> str:
    """The name of the folder a model's pinned files go in: its Hugging Face revision, else what its files' hashes make
    (a new file or hash is a new revision, never written over)."""
    if m.source.get("revision"):
        return str(m.source["revision"])
    import hashlib

    ids = "".join(sorted(f"{f['path']}:{f.get('sha256') or f.get('git_sha1')}" for f in m.files))
    return "files-" + hashlib.sha256(ids.encode()).hexdigest()[:16]


_reviewed: dict = {}
SIGNATURE_S = 1.0  # how often the reviewed entries' files are looked at again (a stat of each model.toml)


def _signature(root: Path) -> tuple:
    out = [_channels_signature()]
    for path in sorted(root.glob(f"*/{MANIFEST}")) if root.is_dir() else ():
        try:
            st = path.stat()
        except OSError:
            continue
        out.append((path.parent.name, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, st.st_size))
    return tuple(out)


def manifests() -> dict[str, Manifest]:
    """The catalog's reviewed models that read cleanly, by id: on this Mac and in the cloud. Read again when an entry's
    file changes (looked at once a second at most), so a catalog update needs no restart (the catalog audit's G9).
    `manifests.cache_clear()` forgets them (tests that move ROOT)."""
    import time

    now = time.monotonic()
    if _reviewed.get("root") != ROOT or now - _reviewed.get("looked", -1e9) > SIGNATURE_S:
        sig = _signature(ROOT)
        if _reviewed.get("root") != ROOT or _reviewed.get("sig") != sig:
            found, problems = _scan(ROOT, "")
            _reviewed.update(root=ROOT, sig=sig, value={**_reviewed_channel()[0], **found}, problems=problems)
        _reviewed["looked"] = now
    return _reviewed["value"]


manifests.cache_clear = _reviewed.clear  # type: ignore[attr-defined]


def local() -> dict[str, Manifest]:
    """The reviewed models this Mac's GPU workers load (the worker's tables, the memory guard's protected models)."""
    return {k: m for k, m in manifests().items() if m.runner == "gpu-worker"}


def by_version(version: str) -> Manifest | None:
    """The reviewed model a provider's or a repository's own name for it means ("gemini-3.8-flash", "facebook/sam3.1")."""
    return next((m for m in manifests().values() if m.version == version), None)


def get(model: str | None) -> Manifest | None:
    """A reviewed entry by id, or one of the user's by `mine/<id>`, or a community channel's by `<channel>/<id>`."""
    if model and "/" in model:
        return mine().get(model)
    return manifests().get(model or "")


_mine: dict = {}


def _mine_signature(root: Path, environ) -> tuple:
    """A stat of every file directly in each of the user's entry folders (its manifest, adapter, lockfile, picture...) and
    of the channels: what `mine` reads. A folder's own revisions and staging are below it, never read for the entry."""
    out = [str(root), _channels_signature(environ)]
    for folder in sorted(root.iterdir()) if root.is_dir() else ():
        try:
            for path in sorted(folder.iterdir()) if folder.is_dir() else ():
                st = path.stat()
                out.append((folder.name, path.name, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, st.st_size))
        except OSError:
            out.append((folder.name, None))
    return tuple(out)


def mine(environ=os.environ) -> dict[str, Manifest]:
    """The entries on the user's side that read cleanly: their own, by `mine/<id>`, and each community channel's, by
    `<channel>/<id>`. One whose id a reviewed entry (or one of the user's own) has is refused (`problems()`), never
    shadowing it: an option and an implementation are named by the id. Read again only when a file of theirs, the
    channels or the reviewed entries changed (acceptance 2026-10-13: one Models page built it 54 times, 508 manifests
    parsed, ~190 ms of the event loop with eight entries of the user's own)."""
    reviewed = manifests()
    root = mine_root(environ)
    sig = (_mine_signature(root, environ), id(reviewed))
    if _mine.get("sig") == sig:
        return dict(_mine["value"])
    out = _read_mine(root, reviewed, environ)
    _mine.update(sig=sig, value=out)
    return dict(out)


mine.cache_clear = _mine.clear  # type: ignore[attr-defined]


def _read_mine(root: Path, reviewed: dict, environ) -> dict[str, Manifest]:
    found, _ = _scan(root, "mine")
    out = {m.key: m for m in found.values() if m.id not in reviewed}
    taken = set(reviewed) | {m.id for m in out.values()}
    for m in _community(environ)[0].values():
        if m.id not in taken:
            out[m.key] = m
            taken.add(m.id)
    return out


def problems(environ=os.environ) -> dict[str, list[str]]:
    """Every entry that can't be used as written, reviewed or the user's, with why in plain words."""
    # Entries and their diagnostics describe the same signature-checked files.
    # Parsing the reviewed catalog again here used to dominate every request's
    # capabilities context, even when no file changed.
    reviewed = manifests()
    out = {key: list(why) for key, why in _reviewed["problems"].items()}
    found, theirs = _scan(mine_root(environ), "mine")
    out.update(theirs)
    for m in found.values():
        if m.id in reviewed:
            out.setdefault(m.key, []).append(f"its id {m.id!r} is a reviewed entry's: give it another")
    out.update(_reviewed_channel(environ)[1])
    community, said = _community(environ)
    out.update(said)
    kept = mine(environ)
    for m in community.values():
        if m.key not in kept:
            out.setdefault(m.key, []).append(f"its id {m.id!r} is taken by an entry already here: it is left out")
    return out


# --- channels (orchestrator/channels.py writes them; read here, metadata only) ------------------------------------------
REVIEWED_CHANNEL = "reviewed"
REVIEWED_URL = "https://github.com/wpulier/HookeUI.git"
# Where the reviewed catalog lived before the repository split (2026-10-27). A Mac that updated it then saved one of
# these in channels.json; it is read as the current address, and saved so at the next update.
FORMER_REVIEWED_URLS = frozenset({"https://github.com/wpulier/ObservationUI.git",
                                  "https://github.com/wpulier/microscope-mind.git"})
CHANNEL_RUNNERS = ("python-env", "fiji")  # what a channel can bring: entries whose code is in their own folder


def channels_root(environ=os.environ) -> Path:
    return catalog_root(environ) / "channels"


def channels(environ=os.environ) -> list[dict]:
    """The channels this Mac reads, as channels.json lists them: name, url, commit, reviewed, trusted..."""
    import json

    try:
        found = json.loads((channels_root(environ) / "channels.json").read_text())
        found = [c for c in found if isinstance(c, dict) and c.get("name")]
    except (OSError, ValueError):
        return []
    for c in found:
        if c["name"] == REVIEWED_CHANNEL and c.get("url") in FORMER_REVIEWED_URLS:
            c["url"] = REVIEWED_URL
    return found


def snapshot(c: dict, environ=os.environ) -> Path | None:
    """A channel's fetched catalog at its pinned commit, when it is here."""
    if not c.get("commit"):
        return None
    d = channels_root(environ) / c["name"] / c["commit"]
    return d if d.is_dir() else None


def _channels_signature(environ=os.environ) -> tuple:
    try:
        st = (channels_root(environ) / "channels.json").stat()
        return ("channels", st.st_mtime_ns, st.st_size)
    except OSError:
        return ("channels",)


def _from_channel(c: dict, namespace: str, environ) -> tuple[dict[str, Manifest], dict[str, list[str]]]:
    d = snapshot(c, environ)
    if d is None:
        return {}, {}
    found, said = _scan(d, namespace, c["name"])
    said = {f"{c['name']}/{k.split('/')[-1]}": v for k, v in said.items()}
    out = {}
    for mid, m in found.items():
        if m.runner not in CHANNEL_RUNNERS:
            said.setdefault(f"{c['name']}/{mid}", []).append(Incompatible(
                f"comes with an app update: it needs the app's own code (runner {m.runner or 'none'}), and a channel "
                "brings only portable Python or confined Fiji capabilities"))
        else:
            out[mid] = m
    return out, said


def _reviewed_channel(environ=os.environ) -> tuple[dict[str, Manifest], dict[str, list[str]]]:
    """The reviewed channel's entries this app doesn't bundle (an update to a bundled one comes with the app's)."""
    c = next((c for c in channels(environ) if c.get("reviewed")), None)
    if c is None:
        return {}, {}
    found, said = _from_channel(c, "", environ)
    bundled = {path.parent.name for path in ROOT.glob(f"*/{MANIFEST}")} if ROOT.is_dir() else set()
    return ({k: m for k, m in found.items() if k not in bundled},
            {k: v for k, v in said.items() if k.split("/")[-1] not in bundled})


def _community(environ=os.environ) -> tuple[dict[str, Manifest], dict[str, list[str]]]:
    out, said = {}, {}
    for c in channels(environ):
        if c.get("reviewed"):
            continue
        found, why = _from_channel(c, c["name"], environ)
        out.update({m.key: m for m in found.values()})
        said.update(why)
    return out, said
