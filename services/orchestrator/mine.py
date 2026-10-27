"""Your own models (P5 of docs/design/2026-10-12-the-open-catalog.md; ADR 0021): added by a link, a path or a file
dropped in, shown at once, run only once you turn them on.

- **Where they go:** `~/MicroscopeMind/catalog/mine/models/<id>/`. That folder holds:
  - `model.toml`, the entry: the app writes it, or you do;
  - `<revision>/`, its files: checked and read-only, as every model's are (downloads.py);
  - `activation.json`, whether it is on, and its last check.
- **Adding** (`add`: the Models page's Add your own, the command `model-add`, `make model-add FROM=`):
  - **A Cellpose model file** (a path, or a Hugging Face link to a repository or a file) becomes an entry with
    runner "cellpose-file": Cellpose's own code will run its weights. A path is cloned into its revision folder at
    once; a link is downloaded with Download, as any model is.
  - **A BioImage Model Zoo model** (its page, its nickname or its files' address; link_recognise.py, C3) becomes an entry
    run by a **reviewed template** (`catalog/templates/bioimageio`: its adapter and locked environment, copied byte for
    byte) on the model's own files, pinned by their hashes; its cover is the entry's picture. Install makes its Python and
    proves it (its own test, then a real picture). The model is its makers' code (TorchScript), so it runs inside macOS's
    sandbox (sandbox.required).
  - **A GitHub link** is read by link_recognise.from_github: a bioimage.io model in the repository (at a pinned commit)
    or in a release's zip becomes an entry run by the same reviewed template; an issue or pull request adds nothing and
    says what to paste instead; anything else is a skeleton pinned at the commit it was read at, its `why` (what it holds
    and lacks) returned for the studio to say.
  - **Any other link** (a zoo model the template can't run, a Hugging Face repository with no single weights file, a
    GitHub repository with no model) becomes a **skeleton**: an entry that says what it needs, listed, never runnable.
- **Added once:** adding the same thing again (the same link at the same pinned revision, made with the same reviewed
  template, or the same file) is said as already added, with its next step; nothing is written or downloaded again
  (`_already`). An entry made again with another version of a template is a new entry, and its files, when another
  entry already has exactly those at that revision here, are cloned from it and checked, never downloaded again
  (downloads.adopt_twin).
- **Appearing is not running:** nothing here imports a model library, loads a checkpoint or runs anything an entry
  names. Reading an entry reads its manifest, sizes and hashes only.
- **Turning on** (`activate`; ADR 0021's activation, a real gate): its check runs in a process of its own
  (`noncommercial.cellpose_file_check`):
  1. the file holds only weights (`torch.load(weights_only=True)`), else it is refused without running it;
  2. this Cellpose builds a model from it;
  3. it finds a sane number of cells on a real frame.

  Only then is it **on**: a choice of its own under Cells (option `mine-<id>`), in the Models menu, Capabilities,
  `use`, `set_default` and `compare_models`. It runs in a GPU worker of its own (host "own"), never in the live view's
  workers, with no credentials.
- **Turning off** takes the choice away. A setting that chose it goes back to the default, said.

Turned-on models are registered at start and whenever the folder changes (`register`, the app's rescan every
RESCAN_S), so a restart keeps them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Mapping

from gpu_worker import manifests as catalog
from gpu_worker import model_files

ACTIVATION = "activation.json"
RESCAN_S = 5.0
RUNNER = "cellpose-file"
COMPONENT = "cellpose-sam-yours"  # licences.COMPONENTS: Cellpose's code, the weights their maker's
OPTION_PREFIX = "mine-"
from .paths import SERVICES  # where this code is (the check runs from it)
CHECK_TIMEOUT_S = 900.0  # a cold first load of a 1.2 GB model on a busy Mac, and its run on one frame
HF = "https://huggingface.co"
SKIP = re.compile(r"(^|/)(README|LICENSE|\.gitattributes)|\.(md|txt|json|png|jpg|jpeg|gif|py|yaml|yml)$", re.I)


class CantDo(RuntimeError):
    """What could not be added or turned on, in plain words."""


def root(environ: Mapping[str, str] = os.environ) -> Path:
    return catalog.mine_root(environ)


def slug(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return out[:48] or "my-model"


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(v)}" for k, v in value.items()) + " }"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return json.dumps(str(value))  # a TOML basic string is a JSON string


def write_entry(folder: Path, fields: dict, implements: list[dict] | None = None) -> None:
    """The entry's model.toml, written whole (a temporary file renamed into place)."""
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["# Added by ObservationUI (orchestrator/mine.py). Yours to edit: catalog/README.md says what each key means."]
    for key, value in fields.items():
        if key == "files":
            lines.append("files = [")
            lines += [f"  {_toml_value(f)}," for f in value]
            lines.append("]")
        elif value not in ("", None, [], {}, ()):
            lines.append(f"{key} = {_toml_value(value)}")
    for i in implements or []:
        lines += ["", "[[implements]]"] + [f"{k} = {_toml_value(v)}" for k, v in i.items()]
    tmp = folder / ".model.toml.tmp"
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(folder / catalog.MANIFEST)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 23):
            h.update(block)
    return h.hexdigest()


def _cellpose_fields(id_: str, name: str, files: list[dict], source: dict, credit: dict, note: str) -> dict:
    return {"id": id_, "name": name, "runs": "this-mac", "version": "your Cellpose model", "licence": [COMPONENT],
            "licence_note": note, "kind": "model", "what": "Your Cellpose model: outlines every cell in a view",
            "runner": RUNNER, "source": source, "files": files, "needs_to_get": ["download"],
            "needs_to_run": ["install:cellpose"], "credit": credit, "workers": ["analysis"], "host": "own",
            "documented_mb": 1300, "peak_mb": 1300, "load_limit_s": 300}


CELLS_IMPLEMENTS = [{"operation": "cells", "impl": "cellpose-file", "input": "image.2d.rgb8", "output": "labels.instance.2d"}]


def _free_id(base: str, environ: Mapping[str, str]) -> str:
    id_ = base
    n = 2
    while (root(environ) / id_).exists() or catalog.manifests().get(id_) is not None:
        id_ = f"{base}-{n}"
        n += 1
    return id_


def _already(same, environ: Mapping[str, str]) -> catalog.Manifest | None:
    """The first of your entries `same` says is the one being added again, else None."""
    return next((m for _, m in sorted(catalog.mine(environ).items()) if same(m)), None)


def _next_step(m: catalog.Manifest, environ: Mapping[str, str]) -> str:
    """An entry's next step, in plain words, for "already added": the readiness resolver's first step (a key, a licence,
    Allow, a download, an install, Turn on), the same as its card's (ADR 0019); once nothing is left, its comparison
    (own_trials), then that it is a choice already. Never a second reading of setup of its own."""
    from . import env_runner, own_trials, readiness

    try:
        steps = readiness.model(m.key, environ=environ).steps
    except KeyError:
        steps = ()
    if steps:
        says = steps[0].says
        return says[:1].lower() + says[1:]
    installed = env_runner.in_use(m, environ) if m.runner == "python-env" else None
    if own_trials.pending(installed.m if installed else m, environ):
        return ("choose a picture of yours and Set up for comparison, then Use it" if own_trials.awaiting_picture(m.key, environ)
                else "compare it on your picture, then Use it")
    return "it is already a choice"


def _said_again(m: catalog.Manifest, kind: str, environ: Mapping[str, str], **more) -> dict:
    pinned = m.source.get("rev") or (catalog.short_revision(m) if m.files else "")
    at = f" at {str(pinned)[:7]}" if pinned else ""
    return {"model": m.key, "kind": kind, "already": True, **more,
            "says": f"Already added: {m.name} is one of yours, from the same source{at}. Nothing was added or downloaded "
                    f"again. Next, {_next_step(m, environ)}."}


def _reuse_files(m: catalog.Manifest, environ: Mapping[str, str]) -> str:
    """Words for files another entry already has here at this revision: cloned from it and checked (downloads.adopt_twin),
    never downloaded again. "" when there are none (or they couldn't be reused now: Download does it then)."""
    from . import downloads

    try:
        reused = downloads.adopt_twin(m, environ)
    except (downloads.Failed, OSError):
        return ""
    return f" Its files were already on this Mac (in {reused[1].name}), so they were reused, not downloaded again." if reused else ""


def add(source: str, name: str = "", environ: Mapping[str, str] = os.environ) -> dict:
    """Add an entry from a path or a link (see the module's docstring). Returns {"model", "kind", "says"}; CantDo when
    there is nothing to add there."""
    source = (source or "").strip().strip('"').strip("'")
    if not source:
        raise CantDo("give a link to the model, or the path of its file")
    path = Path(source).expanduser()
    if source.startswith("file://"):
        path = Path(source[len("file://"):])
    if path.exists() and path.name.endswith(".observationui.zip"):  # a capability someone shared (share.py, C5)
        from . import share

        try:
            return share.open_file(path, environ)
        except share.CantShare as exc:
            raise CantDo(str(exc)) from exc
    if path.exists():
        return _add_file(path, name, environ)
    from . import link_recognise

    if link_recognise.zoo_id(source):
        try:
            found = link_recognise.recognise(source)
        except link_recognise.CantRead as exc:
            raise CantDo(str(exc)) from exc
        if found.kind == "template":
            return _add_from_template(found, name, environ)
        return _add_skeleton(found.link or source, name or found.name, environ, why=found.why)
    if re.match(r"https?://(www\.)?huggingface\.co/", source):
        # A transformers segmentation model: run by the reviewed hf-segmentation template; else a Cellpose model file.
        try:
            found = link_recognise.recognise(source, token=environ.get("HF_TOKEN", ""))
        except link_recognise.CantRead as exc:
            raise CantDo(str(exc)) from exc
        if found.kind == "template":
            return _add_from_template(found, name, environ)
        if found.kind in ("needs-code", "unsupported"):
            return _add_skeleton(found.link or source, name or found.name, environ, why=found.why)
        return _add_huggingface(source, name, environ)
    gh = link_recognise.github(source)
    if gh:
        # What the GitHub link holds (link_recognise.from_github; ticket 62 row 7): a model a reviewed template runs, found
        # at a pinned commit or in a release's zip; an issue or a pull request is no source at all; anything else is kept
        # as a skeleton at the commit it was read at, with what it holds and lacks. Reading it reads metadata only.
        try:
            found = link_recognise.recognise(source, github_token=environ.get("GITHUB_TOKEN", ""))
        except link_recognise.CantRead as exc:
            raise CantDo(str(exc)) from exc
        if found.kind == "template":
            return _add_from_template(found, name, environ)
        if gh["kind"] == "page":
            raise CantDo(found.why)
        # Its `why` goes with it, added now or already, for the studio to say (modelspage.integrationWords).
        return {**_add_skeleton(source, name or found.name, environ, why=found.why, source=found.source), "why": found.why}
    if re.match(r"https?://", source):
        return _add_skeleton(source, name, environ)
    raise CantDo(f"there is no file at {source}, and it is not a link")


def _add_file(path: Path, name: str, environ: Mapping[str, str]) -> dict:
    if path.is_dir():
        candidates = sorted((p for p in path.iterdir() if p.is_file() and not SKIP.search(p.name)), key=lambda p: -p.stat().st_size)
        if not candidates:
            raise CantDo(f"{path} holds no model file")
        path = candidates[0]
    size = path.stat().st_size
    if size < 1_000_000:
        raise CantDo(f"{path.name} is {size} bytes: too small to be a Cellpose model")
    title = name or path.stem.replace("_", " ").replace("-", " ").strip() or "My Cellpose model"
    sha = _sha256(path)
    again = _already(lambda m: m.runner == RUNNER and [f.get("sha256") for f in m.files] == [sha], environ)
    if again is not None:
        return _said_again(again, RUNNER, environ)
    id_ = _free_id(slug(name or path.stem), environ)
    entry = {"path": path.name, "bytes": size, "sha256": sha, "url": path.resolve().as_uri()}
    folder = root(environ) / id_
    write_entry(folder, _cellpose_fields(id_, title, [entry], {"weights": str(path.resolve())}, {},
                                         "the licence its maker gave it (not stated where it came from)"), CELLS_IMPLEMENTS)
    m = catalog.mine(environ).get(f"mine/{id_}")
    if m is None:
        raise CantDo(f"the entry for {path.name} could not be read: {catalog.problems(environ).get(f'mine/{id_}')}")
    from . import downloads

    downloads.adopt_file(m, path, environ)  # cloned into its revision folder, checked, read-only
    return {"model": m.key, "kind": RUNNER, "says": f"Added {title} ({_size(size)}) from your file. Turn it on to check it."}


def _hf_parts(url: str) -> tuple[str, str, str]:
    """(repo, revision, file) from a Hugging Face link: a repository, or a file in it (blob/resolve/<rev>/<path>)."""
    m = re.match(r"https?://(?:www\.)?huggingface\.co/([^/]+/[^/?#]+)(?:/(?:blob|resolve|tree)/([^/]+)(?:/(.+?))?)?/?(?:[?#].*)?$", url)
    if not m:
        raise CantDo(f"{url} is not a Hugging Face model link")
    return m.group(1), m.group(2) or "main", (m.group(3) or "")


def _add_huggingface(url: str, name: str, environ: Mapping[str, str]) -> dict:
    import httpx

    repo, revision, wanted = _hf_parts(url)
    headers = {"Authorization": f"Bearer {environ['HF_TOKEN']}"} if environ.get("HF_TOKEN") else {}
    try:
        r = httpx.get(f"{HF}/api/models/{repo}/revision/{revision}", params={"blobs": "true"}, headers=headers, timeout=30)
    except httpx.HTTPError as exc:
        raise CantDo(f"couldn't reach Hugging Face to read {repo} ({type(exc).__name__}); try again") from exc
    if r.status_code in (401, 403):
        raise CantDo(f"{repo} is gated: add your Hugging Face key and accept its licence on {HF}/{repo}, then add it again")
    if r.status_code == 404:
        raise CantDo(f"Hugging Face has no model {repo} (at {revision})")
    r.raise_for_status()
    info = r.json()
    pinned = info.get("sha") or revision
    files = [s for s in info.get("siblings") or [] if not SKIP.search(s["rfilename"])]
    if wanted:
        files = [s for s in files if s["rfilename"] == wanted]
    big = [s for s in files if ((s.get("lfs") or {}).get("size") or s.get("size") or 0) >= 5_000_000]
    card = info.get("cardData") or {}
    note = str(card.get("license") or "not stated on its card")
    credit = {"authors": repo.split("/")[0], "link": f"{HF}/{repo}"}
    title = name or repo.split("/")[1].replace("-", " ").replace("_", " ")
    if len(big) != 1:
        return _add_skeleton(f"{HF}/{repo}", name or title, environ,
                             why=(f"it holds {len(big)} large files ({', '.join(s['rfilename'] for s in big[:4])}): add the "
                                  "one that is the model by its file link" if big else "it holds no model file this app can run"))
    s = big[0]
    lfs = s.get("lfs") or {}
    entry = {"path": s["rfilename"], "bytes": int(lfs.get("size") or s.get("size") or 0)}
    if lfs.get("sha256"):
        entry["sha256"] = lfs["sha256"]
    else:
        entry["git_sha1"] = s["blobId"]
    source = {"hf": repo, "revision": pinned}
    again = _already(lambda m: m.runner == RUNNER and m.source == source and model_files.pinned_identity(m.files) == model_files.pinned_identity([entry]), environ)
    if again is not None:
        return _said_again(again, RUNNER, environ)
    id_ = _free_id(slug(name or repo.split("/")[1]), environ)
    write_entry(root(environ) / id_, _cellpose_fields(id_, title.strip() or id_, [entry], source,
                                                      credit, note), CELLS_IMPLEMENTS)
    m = catalog.mine(environ).get(f"mine/{id_}")
    if m is None:
        raise CantDo(f"the entry for {repo} could not be read: {catalog.problems(environ).get(f'mine/{id_}')}")
    reused = _reuse_files(m, environ)
    return {"model": m.key, "kind": RUNNER, "says": f"Added {m.name} from Hugging Face ({repo} at {pinned[:7]}, "
                                                    f"{_size(entry['bytes'])})." + (
                                                        reused + " Turn it on to check it." if reused else
                                                        " Download it, then turn it on to check it.")}


def _add_from_template(found, name: str, environ: Mapping[str, str]) -> dict:
    """An entry run by a reviewed template (link_recognise.py): the template's adapter and lockfile copied byte for byte,
    the model's files pinned, its cover beside the manifest as its picture, its licence as its own component."""
    import shutil
    import tomllib

    from . import link_recognise

    tpl_dir = link_recognise.template_folder(found.template)
    tpl = tomllib.loads((tpl_dir / "template.toml").read_text())
    pins = {"adapter": f"catalog/templates/{found.template} v{tpl['version']} (reviewed)",
            "environment": f"catalog/templates/{found.template} v{tpl['version']} (reviewed)",
            **({"output_conversion": found.conversion} if found.conversion else {})}
    # The same source at the same pinned files, made with this very template (its version, its files byte for byte).
    again = _already(lambda m: m.runner == tpl["runner"] and m.source == found.source and m.pins == pins
                     and model_files.pinned_identity(m.files) == model_files.pinned_identity(found.files)
                     and link_recognise.reviewed_template(m) == found.template, environ)
    if again is not None:
        return _said_again(again, f"template:{found.template}", environ, found=found.wire())
    id_ = _free_id(slug(name or found.id_hint or found.source.get("bioimageio") or found.name), environ)
    folder = root(environ) / id_
    folder.mkdir(parents=True, exist_ok=True)
    for f in ("adapter.py", "env.lock", *tpl.get("entry_files", [])):
        shutil.copyfile(tpl_dir / f, folder / f)
    example = ""
    if found.cover:
        (folder / found.cover_name).write_bytes(found.cover)
        example = found.cover_name
    impl = tpl["implements"]
    title = (name or found.name).strip() or id_
    write_entry(folder, {
        "id": id_, "name": title, "runs": "this-mac", "version": found.version, "licence": [id_],
        "licence_note": found.licence, "kind": "model", "what": found.what or tpl["runs"][:60], "runner": tpl["runner"],
        "source": found.source, "files": found.files, "adapter": tpl["adapter"],
        "env": {**tpl["env"]}, "component": {"what": f"{title} (its weights, from {found.link})", "licence": found.licence,
                                             "url": found.link, "tier": found.tier},
        "pins": pins,
        "needs_to_get": ["download"], "needs_to_run": [f"install:mine/{id_}"],
        "credit": {k: v for k, v in {"authors": found.makers, "link": found.link, "paper": found.paper}.items() if v},
        "tags": found.tags, "example": example, "host": tpl["host"], "documented_mb": tpl["documented_mb"],
        "load_limit_s": tpl["load_limit_s"]},
        [{"operation": impl["operation"], "impl": id_, "input": impl["input"], "output": impl["output"]}])
    m = catalog.mine(environ).get(f"mine/{id_}")
    if m is None:
        problems = catalog.problems(environ).get(f"mine/{id_}")
        shutil.rmtree(folder, ignore_errors=True)
        raise CantDo(f"the entry for {found.name} could not be written: {problems}")
    size = sum(f.get("bytes", 0) for f in found.files)
    reused = _reuse_files(m, environ)
    return {"model": m.key, "kind": f"template:{found.template}", "found": found.wire(),
            "says": f"Added {title} from {tpl.get('where', 'its link')} ({_size(size)}, {found.licence}).{reused} Turn it "
                    f"on: it {'' if reused else 'downloads, '}makes its Python and "
                    f"{tpl.get('proves', 'checks it on a real picture')}. " + found.conversion}


def _add_skeleton(url: str, name: str, environ: Mapping[str, str], why: str = "", source: dict | None = None) -> dict:
    """An entry for a link the app can't run as it is: listed, with what it needs (an adapter), never runnable. `source`,
    when the link was read (a GitHub repository at the commit it was read at), else what the link itself pins."""
    from . import link_recognise

    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    again = _already(lambda m: not m.runner and str(m.source.get("url") or "").rstrip("/") == url.rstrip("/"), environ)
    if again is not None:
        return _said_again(again, "skeleton", environ)
    title = name or link_recognise.link_name(url) or host  # its repository's name, never a commit's hash or a file's
    source = source or link_recognise.link_source(url)
    id_ = _free_id(slug(title), environ)
    write_entry(root(environ) / id_, {
        "id": id_, "name": title, "runs": "this-mac",
        "version": f"commit {source['rev'][:12]}" if source.get("rev") else "not pinned yet", "licence": [COMPONENT],
        "licence_note": "not read yet: see its page", "kind": "model", "what": f"Added from {host}: needs an adapter to run",
        "runner": "", "source": source, "needs_to_run": ["install:adapter"], "credit": {"link": url},
        "workers": ["analysis"], "host": "own", "documented_mb": 1, "load_limit_s": 60})
    return {"model": f"mine/{id_}", "kind": "skeleton",
            "says": f"Added {title} as a skeleton: {why or 'the app can not run it as it is'}. It needs an adapter written "
                    "for it before it can run (catalog/README.md, “Add your own”)."}


def _size(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{max(1, round(n / 1e6))} MB"


# --- on and off ------------------------------------------------------------------------------------------------------
def state(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict:
    """{"on": bool, "check": the last check, "revision": what it checked}, from activation.json (none: off, unchecked)."""
    try:
        found = json.loads((model_files.folder(m, environ) / ACTIVATION).read_text())
    except (OSError, ValueError):
        return {"on": False, "check": None}
    on = bool(found.get("on")) and found.get("revision") == catalog.revision_of(m)  # a new revision is checked again
    return {"on": on, "check": found.get("check"), "at": found.get("at", ""), "revision": found.get("revision", "")}


class Activation:
    """One activation's terminal choice: withdraw, or atomically publish its receipt. Used off the live loop."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = False
        self.committed = False

    def cancel(self) -> bool:
        with self._lock:
            if self.committed:
                return False  # completion won; its owner must finish the original notification
            self._cancelled = True
            return True

    def publish(self, staged: Path, destination: Path, bound) -> None:
        from .owned_process import Stopped
        with self._lock:
            if self._cancelled:
                raise Stopped('cancelled')
            bound.check()
            staged.replace(destination)
            self.committed = True


def _write_state(m: catalog.Manifest, on: bool, check: dict | None, environ: Mapping[str, str],
                 *, attempt: Activation | None = None) -> None:
    from . import owned_process
    folder = model_files.folder(m, environ)
    tmp = folder / f".{ACTIVATION}.{uuid.uuid4().hex}.tmp"
    try:
        with owned_process.scope() as bound:
            tmp.write_text(json.dumps({"on": on, "check": check, "revision": catalog.revision_of(m),
                                       "at": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=1))
        # The potentially slow preparation is outside the terminal lock. Cancellation and the final rename
        # share that lock; a completed publication is never subsequently reported as accepted cancellation.
        (attempt or Activation()).publish(tmp, folder / ACTIVATION, bound)
    finally:
        tmp.unlink(missing_ok=True)


def check_command(weights: Path, frame: Path) -> list[str]:
    return [sys.executable, "-m", "noncommercial.cellpose_file_check", str(weights), str(frame)]


def activate(key: str, frame: Path, environ: Mapping[str, str] = os.environ, run=None,
             *, attempt: Activation | None = None) -> dict:
    """Turn an entry on: its check in a process of its own (the module's docstring), and on only if it passed. Returns
    the check; CantDo when it can't be checked yet (not a Cellpose file, not downloaded, Cellpose not installed)."""
    m = catalog.mine(environ).get(key)
    if m is None:
        raise CantDo(f"there is no entry of yours called {key}")
    if m.runner != RUNNER:
        raise CantDo(f"{m.name} needs an adapter written for it before it can run; it can't be turned on as it is")
    files = model_files.files(m)
    if model_files.published(m, environ) is None:
        raise CantDo(f"{m.name} isn't downloaded yet ({_size(sum(f['bytes'] for f in files))}): download it first")
    from .operations import INSTALLED

    missing = INSTALLED["cellpose"]() if "cellpose" in INSTALLED else ""
    if missing:
        raise CantDo(f"Cellpose isn't installed here, so it can't be checked ({missing})")
    weights = model_files.revision_dir(m, environ) / files[0]["path"]
    env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_TOKEN", "_KEY"))}  # no credentials
    env["PYTHONPATH"] = str(SERVICES)
    try:
        if run is not None:  # deterministic check supplier in tests
            done = run(check_command(weights, frame), capture_output=True, text=True, timeout=CHECK_TIMEOUT_S,
                       cwd=str(SERVICES), env=env)
        else:
            from . import owned_process
            process = subprocess.Popen(check_command(weights, frame), cwd=str(SERVICES), env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                with owned_process.watching(process, CHECK_TIMEOUT_S, 'Own-model functional check'):
                    output = owned_process.drained(process)
                done = subprocess.CompletedProcess(process.args, process.returncode, stdout=output, stderr='')
            finally:
                owned_process.terminate(process)
        lines = [line for line in (done.stdout or "").splitlines() if line.startswith("{")]
        check = json.loads(lines[-1]) if lines else {"ok": False, "steps": [
            {"says": f"its check ended without an answer ({(done.stderr or '').strip().splitlines()[-1:] or ['no output']})",
             "ok": False}]}
    except subprocess.TimeoutExpired:
        check = {"ok": False, "steps": [{"says": f"its check took over {CHECK_TIMEOUT_S / 60:.0f} minutes and was stopped",
                                         "ok": False}]}
    check["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _write_state(m, bool(check.get("ok")), check, environ, attempt=attempt)
    return check


def deactivate(key: str, environ: Mapping[str, str] = os.environ) -> None:
    m = catalog.mine(environ).get(key)
    if m is None:
        raise CantDo(f"there is no entry of yours called {key}")
    _write_state(m, False, state(m, environ).get("check"), environ)


def active(environ: Mapping[str, str] = os.environ) -> list[catalog.Manifest]:
    """Your entries that are on now: a Cellpose file, its revision published, its check passed for that revision."""
    return [m for m in catalog.mine(environ).values()
            if m.runner == RUNNER and state(m, environ)["on"] and model_files.published(m, environ) is not None]


# --- as choices ------------------------------------------------------------------------------------------------------
def option_id(m: catalog.Manifest) -> str:
    return OPTION_PREFIX + m.id


def entry_of(option: str, environ: Mapping[str, str] = os.environ) -> catalog.Manifest | None:
    return catalog.mine(environ).get("mine/" + option[len(OPTION_PREFIX):]) if option.startswith(OPTION_PREFIX) else None


def register(environ: Mapping[str, str] = os.environ, settings=None) -> dict:
    """Make the choices match what is on: each turned-on entry an option under Cells and an implementation of the cells
    operation; each turned off taken away. Returns {"added": [...], "removed": [...], "reset": bool} (reset: the setting
    chose one now off, and went back to the default)."""
    import dataclasses

    from . import names, operations, options

    from . import own_trials

    wanted = {option_id(m): m for m in active(environ) if not own_trials.pending(m, environ)}
    have = {k for k in options.GROUPS["cells"] if k.startswith(OPTION_PREFIX)}
    added, removed = sorted(set(wanted) - have), sorted(have - set(wanted))
    put = {}
    for oid in added:
        m = wanted[oid]
        check = state(m, environ).get("check") or {}
        put[oid] = options.Option(oid, m.name, f"yours, turned on {check.get('at', '')[:16].replace('T', ' ')}; its check "
                                               f"found {check.get('cells', '?')} cells on a real frame",
                                  uses=(COMPONENT,), also=(m.key,))
    options.change_choices("cells", put=put, drop=removed)  # never in place: the loop may be reading Cells (catalogue)
    yours = tuple(operations.Impl(oid, m.name, "model", ("field",), "analysis", oid, (COMPONENT,),
                                  version=f"{m.name} {catalog.short_revision(m)}", installed="cellpose",
                                  note="your model, run by Cellpose's own code in a worker of its own")
                  for oid, m in sorted(wanted.items()))

    def with_yours(now):  # built on Cells as it is now, under the writers' lock: a contributed entry's change is kept
        impls = tuple(i for i in now.implementations if not i.id.startswith(OPTION_PREFIX)) + yours
        return now if impls == now.implementations else dataclasses.replace(now, implementations=impls)

    run = operations.ADAPTERS[("cells", "cellpose-file")]
    if operations.change_operation("cells", with_yours, put={oid: run for oid in wanted}):  # never in place (catalogue)
        names.index.cache_clear()
    reset = False
    if settings is not None and getattr(settings, "cells", None) in removed:
        settings.cells = options.Settings().cells  # it chose one now off: the default again, said by the caller
        reset = True
    return {"added": added, "removed": removed, "reset": reset}


def signature(environ: Mapping[str, str] = os.environ) -> str:
    """What the rescan compares: each entry's manifest and activation, and the files lying in the folder."""
    base = root(environ)
    if not base.is_dir():
        return ""
    parts = []
    for item in sorted(base.iterdir()):
        for p in ([item / catalog.MANIFEST, item / ACTIVATION] if item.is_dir() else [item]):
            try:
                st = p.stat()
                parts.append(f"{p.name}:{item.name}:{st.st_mtime_ns}:{st.st_size}")
            except OSError:
                continue
    return "|".join(parts)


def loose(environ: Mapping[str, str] = os.environ) -> list[dict]:
    """Files dropped straight into the folder (no entry yet): offered to be made into one."""
    base = root(environ)
    if not base.is_dir():
        return []
    return [{"path": str(p), "name": p.name, "bytes": p.stat().st_size} for p in sorted(base.iterdir())
            if p.is_file() and not p.name.startswith(".") and p.stat().st_size >= 1_000_000]


def main(argv: list[str] | None = None) -> int:
    """make model-add FROM=<path or link> [NAME=] (make catalog-pr ID= is orchestrator/publish.py)."""
    import argparse

    parser = argparse.ArgumentParser(description="Add a model of your own to ~/MicroscopeMind/catalog/mine/models.")
    parser.add_argument("source", nargs="?", default="")
    parser.add_argument("--name", default="")
    args = parser.parse_args(argv)
    try:
        print(add(args.source, args.name)["says"])
    except CantDo as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
