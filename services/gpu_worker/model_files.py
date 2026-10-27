"""Where a catalog model's files are on this Mac: one answer for the loaders and for readiness (P2 of
docs/design/2026-10-12-the-open-catalog.md).

The models folder (`<catalog>/models/<id>/<revision>/`, `downloads.py` publishes it) holds each model's pinned files,
verified, read-only, one folder per revision; a revision is never written over. Until a model's revision is there, the
caches its library filled before (the Hugging Face cache at the **pinned** revision, Cellpose's and micro-sam's model
folders) are read in its place, file by file, each file checked by its size and content id: what runs is always the
pinned revision, never "whatever the cache's main is". A model in neither is not downloaded, and nothing fetches it on
first use: `NotDownloaded` says so, and the user's Download (or the agent's, on their yes) fetches it.

Light on purpose (no model library, no orchestrator), so a GPU worker reads it as it loads. MICROMIND_MODEL_FILES moves
the old caches into one folder (hf/, cellpose/, micro_sam/), and "standin" counts every model as downloaded: the tests'
stand-in workers load no real model.
"""
from __future__ import annotations

import functools
import json
import os
import re
from pathlib import Path
from typing import Mapping

from . import manifests as catalog

SHA256 = re.compile(r"[0-9a-f]{64}")
GIT_SHA1 = re.compile(r"[0-9a-f]{40}")
RECEIPT = "receipt.json"


class NotDownloaded(RuntimeError):
    """A model whose pinned files are not on this Mac: said, never fetched behind the user's back."""

    def __init__(self, m: catalog.Manifest, missing: int) -> None:
        self.model, self.missing = m.id, missing
        size = f"{missing / 1e9:.1f} GB" if missing >= 1e9 else f"{max(1, round(missing / 1e6))} MB"
        super().__init__(f"{m.name} isn't downloaded yet ({size}): its Download button on the Models page, or "
                         f"make catalog-download ID={m.id}")


def standin(environ: Mapping[str, str] = os.environ) -> bool:
    return environ.get("MICROMIND_MODEL_FILES") == "standin"


def owner(m: catalog.Manifest) -> catalog.Manifest:
    """Whose files a model runs: its own, or another model's (EdgeTAM video runs EdgeTAM's)."""
    if m.files_from:
        other = catalog.get(m.files_from)
        if other is not None:
            return other
    return m


def files(m: catalog.Manifest) -> tuple[dict, ...]:
    return owner(m).files


def pinned_identity(files) -> list[tuple]:
    """What a set of pinned files is, whatever else its entries say of them: each file's path, size and content id (its
    sha256, or Hugging Face's git sha1 for a small file). The one rule for "the same files" (mine.py's added again,
    downloads.twin's reuse), so the two can't drift."""
    return sorted((str(f.get("path")), int(f.get("bytes") or 0), str(f.get("sha256") or ""), str(f.get("git_sha1") or ""))
                  for f in files)


def folder(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> Path:
    """The model's own folder in the models folder: <catalog>/models/<id> (its revisions inside); a user's own entry
    keeps its revisions beside its manifest, in <catalog>/mine/models/<id>."""
    o = owner(m)
    if o.namespace == "mine":
        return catalog.mine_root(environ) / o.id
    if o.namespace:  # a community channel's: its snapshot is read-only, so its files go beside the catalog's, apart
        return catalog.catalog_root(environ) / "models" / o.namespace / o.id
    return catalog.catalog_root(environ) / "models" / o.id


def revision_dir(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> Path:
    return folder(m, environ) / catalog.revision_of(owner(m))


def verification_path(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> Path:
    """Where the last check of a published revision's content is kept: beside it (the revision itself is read-only)."""
    o = owner(m)
    return folder(o, environ) / f".verified-{catalog.revision_of(o)}.json"


def verification(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict | None:
    """{"ok", "problem", "at"} from the last time each published file's content id was computed again, or None."""
    try:
        return json.loads(verification_path(m, environ).read_text())
    except (OSError, ValueError):
        return None


def published(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> Path | None:
    """The model's pinned revision in the models folder, when it is there whole: its receipt names that revision and
    every pinned file is present at its size (each was verified by its hash when it was published), and no later check
    of their content found one changed (downloads.verify_published, once per start: the catalog audit's G8)."""
    o = owner(m)
    d = revision_dir(o, environ)
    try:
        receipt = json.loads((d / RECEIPT).read_text())
    except (OSError, ValueError):
        return None
    if receipt.get("revision") != catalog.revision_of(o):
        return None
    checked = verification(o, environ)
    if checked is not None and checked.get("ok") is False:
        return None
    for f in o.files:
        try:
            if (d / f["path"]).stat().st_size != f["bytes"]:
                return None
        except OSError:
            return None
    return d


# --- the caches the libraries filled before the models folder ---------------------------------------------------
ROOT_KEYS = ("MICROMIND_MODEL_FILES", "HF_HUB_CACHE", "HF_HOME", "CELLPOSE_LOCAL_MODELS_PATH", "MICROSAM_CACHEDIR")


def roots(environ: Mapping[str, str] = os.environ) -> dict[str, list[Path]]:
    """Where each kind of old cache is, as the libraries that downloaded into it put it (manifests.FILE_ROOTS). Worked
    out once per set of the variables that move them (it is read for every file of every model on every look)."""
    return _roots(tuple(environ.get(k, "") for k in ROOT_KEYS))


@functools.lru_cache(maxsize=16)
def _roots(values: tuple[str, ...]) -> dict[str, list[Path]]:
    environ = {k: v for k, v in zip(ROOT_KEYS, values) if v}
    if environ.get("MICROMIND_MODEL_FILES") and not standin(environ):
        base = Path(environ["MICROMIND_MODEL_FILES"])
        return {root: [base / root] for root in catalog.FILE_ROOTS}
    home = Path.home()
    hf = [Path(environ["HF_HUB_CACHE"]) if environ.get("HF_HUB_CACHE") else None,
          Path(environ["HF_HOME"]) / "hub" if environ.get("HF_HOME") else None,
          home / ".cache" / "huggingface" / "hub"]
    cellpose = [Path(environ.get("CELLPOSE_LOCAL_MODELS_PATH") or home / ".cellpose" / "models")]
    micro_sam = [Path(environ["MICROSAM_CACHEDIR"]) / "models" if environ.get("MICROSAM_CACHEDIR") else
                 home / "Library" / "Caches" / "micro_sam" / "models"]
    return {"hf": [p for p in hf if p is not None], "cellpose": cellpose, "micro_sam": micro_sam}


def _legacy_root(o: catalog.Manifest, f: dict) -> str:
    return f.get("root") or ("hf" if o.source.get("hf") else "")


def _snapshot(base: Path, repo: str, revision: str) -> Path:
    return base / f"models--{repo.replace('/', '--')}" / "snapshots" / revision


def _matches(path: Path, f: dict) -> bool:
    """A file of an old cache is the pinned one: its size, and the blob its symlink names (the Hugging Face cache names
    each file's blob by its sha256, or a small file's by its git id)."""
    try:
        if path.stat().st_size != f["bytes"]:
            return False
    except OSError:
        return False
    if path.is_symlink():
        blob = os.path.basename(os.readlink(path))
        if SHA256.fullmatch(blob) and f.get("sha256") and blob != f["sha256"]:
            return False
        if GIT_SHA1.fullmatch(blob) and f.get("git_sha1") and blob != f["git_sha1"]:
            return False
    return True


def legacy_file(m: catalog.Manifest, f: dict, environ: Mapping[str, str] = os.environ) -> Path | None:
    """A pinned file in an old cache, at the pinned revision, its size and content id checked (the Hugging Face cache
    names each file's blob by its sha256, or a small file's by its git id). A companion is in its own repository's."""
    o = owner(m)
    root = "hf" if f.get("from") else _legacy_root(o, f)
    if not root:
        return None
    for base in roots(environ).get(root, []):
        if f.get("from"):
            path = _snapshot(base, f["from"]["hf"], f["from"]["revision"]) / f["from"]["path"]
        elif root == "hf":
            if not o.source.get("hf"):
                return None
            path = _snapshot(base, o.source["hf"], o.source["revision"]) / f["path"]
        else:
            path = base / f["path"]
        if _matches(path, f):
            return path
    return None


def _hf_style(o: catalog.Manifest) -> bool:
    """A model its library reads as one Hugging Face folder (`from_pretrained`): every file of its own is in its hf
    repository, so an old cache serves it only as one snapshot holding them all (legacy_dir)."""
    own = [f for f in o.files if not f.get("from")]
    return bool(o.source.get("hf") and own and all(_legacy_root(o, f) == "hf" for f in own))


def legacy_dir(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> Path | None:
    """The Hugging Face cache's snapshot at the pinned revision, when every pinned file is in it (a companion in its own
    repository's, where the library reads it), checked."""
    o = owner(m)
    own = [f for f in o.files if not f.get("from")]
    if not _hf_style(o):
        return None
    if any(legacy_file(o, f, environ) is None for f in o.files if f.get("from")):
        return None
    for base in roots(environ).get("hf", []):
        snapshot = _snapshot(base, o.source["hf"], o.source["revision"])
        if snapshot.is_dir() and all(_matches(snapshot / f["path"], f) for f in own):  # one snapshot holds them all
            return snapshot
    return None


# --- one answer -----------------------------------------------------------------------------------------------------
def location(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> tuple[str | None, int]:
    """("folder" | "old cache" | None, missing bytes): where the model's pinned files are, or how much is missing."""
    if not files(m):
        return None, 0
    if standin(environ) or published(m, environ) is not None:
        return "folder", 0
    o = owner(m)
    if _hf_style(o):
        # The same answer model_dir gives (the catalog audit's G7): one snapshot must hold them all. Files split across
        # two caches are not "there" for a loader that reads one folder; what is missing is counted from the fullest.
        if legacy_dir(o, environ) is not None:
            return "old cache", 0
        best = min((sum(f["bytes"] for f in o.files if not f.get("from") and not (
                       _snapshot(base, o.source["hf"], o.source["revision"]) / f["path"]).exists())
                    for base in roots(environ).get("hf", [])), default=0)
        best += sum(f["bytes"] for f in o.files if f.get("from") and legacy_file(o, f, environ) is None)
        return None, best or sum(f["bytes"] for f in o.files)
    missing = sum(f["bytes"] for f in files(m) if legacy_file(m, f, environ) is None)
    return ("old cache", 0) if missing == 0 else (None, missing)


def model_dir(model_id: str, environ: Mapping[str, str] = os.environ) -> Path:
    """The folder a loader reads a Hugging Face–style model from (`from_pretrained(path, local_files_only=True)`): its
    published revision, else the old cache's snapshot at the pinned revision. Raises NotDownloaded."""
    m = catalog.get(model_id)
    if m is None:
        raise KeyError(model_id)
    if standin(environ):
        return revision_dir(m, environ)  # the tests' stand-ins: where it would be, never this Mac's caches
    d = published(m, environ)
    if d is not None:
        return d
    d = legacy_dir(m, environ)
    if d is not None:
        return d
    raise NotDownloaded(owner(m), location(m, environ)[1])


_redirected: dict[str, Path] = {}


def companions(model_id: str, environ: Mapping[str, str] = os.environ) -> None:
    """Give the model's library its companion files from the model's published revision: transformers' AutoConfig is
    sent to the folder for each companion's repository name (EdgeTAM's backbone config is read as
    "timm/repvit_m1.dist_in1k"), for the rest of this process. Old caches use the
    verified pinned snapshot too: a repository-name lookup would instead need an
    unrelated mutable refs/main, which may be absent or point at another revision."""
    m = catalog.get(model_id)
    d = published(m, environ) if m is not None else None
    if m is None:
        return
    for f in files(m):
        if f.get("from"):
            local = d / f["path"] if d is not None else legacy_file(m, f, environ)
            if local is None:
                raise NotDownloaded(owner(m), location(m, environ)[1])
            _redirected[f["from"]["hf"]] = local.parent
    from transformers import AutoConfig

    original = AutoConfig.from_pretrained.__func__
    if getattr(original, "_micromind", False):
        return

    def from_pretrained(cls, name, *args, **kwargs):
        local = _redirected.get(str(name))
        return original(cls, str(local) if local is not None else name, *args, **kwargs)

    from_pretrained._micromind = True
    AutoConfig.from_pretrained = classmethod(from_pretrained)


def model_file(model_id: str, path: str, environ: Mapping[str, str] = os.environ) -> Path:
    """One pinned file of a model (a checkpoint a loader opens by path): from its published revision, else from an old
    cache at the pinned revision. Raises NotDownloaded."""
    m = catalog.get(model_id)
    if m is None:
        raise KeyError(model_id)
    f = next((f for f in files(m) if f["path"] == path), None)
    if f is None:
        raise KeyError(f"{model_id} pins no {path}")
    if standin(environ):
        return revision_dir(m, environ) / path  # the tests' stand-ins: where it would be, never this Mac's caches
    d = published(m, environ)
    if d is not None:
        return d / path
    found = legacy_file(m, f, environ)
    if found is not None:
        return found
    raise NotDownloaded(owner(m), location(m, environ)[1])
