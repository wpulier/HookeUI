"""Getting a catalog model's pinned files into the models folder (P2 of docs/design/2026-10-12-the-open-catalog.md).

One way in, whichever way the files come (downloaded, or adopted from a cache a library filled before):

1. **Staging.** The files are written into `<catalog>/models/<id>/.staging-<revision>/`, which no loader ever reads
   (`gpu_worker/model_files.py` reads only a published revision, or an old cache at the pinned revision).
2. **Verify.** Each file is checked against the manifest: its bytes and its content id (sha256, or a small file's git
   blob id). A file that does not match is deleted and the download fails, said; nothing is published.
3. **Publish.** A receipt is written (the revision, each file's size and content id, where they came from, when), the
   folder is made read-only and renamed to `<revision>/` in one step (the same volume). A revision is never written
   over: a new revision is a new folder, and a working one is never touched.

A download resumes where it stopped when the server supports it (HTTP ranges: Hugging Face and most file servers do),
and starts that file again otherwise, so an interrupted download is safe either way. It checks the free space first,
waits while a click is being answered (the live view first), and is one at a time. A gated model needs the user's
Hugging Face key and its licence accepted on its page: the refusal says which, as a step.

**Adopt** (`make catalog-adopt`) fills the folders from what this Mac already downloaded: each file is cloned (APFS
`cp -c`, which avoids an immediate full duplicate: blocks are shared until either copy changes), verified, published.
A model whose exact pinned files another entry has already published here (your same link added again with another
version of its template) is filled from that entry's revision the same way (`adopt_twin`), never downloaded again.

The app's queue is `Downloads` (the studio's Download button: commands `model-download`, `model-download-cancel`;
the agent's on the user's yes); its state is on the capabilities wire (`setup.downloads`) and each download is a
line in Activity. `python -m orchestrator.downloads get|adopt ID` is `make catalog-download` and `catalog-adopt`.
No key's value is ever written, logged or said here: HF_TOKEN is read from the environment and sent to Hugging Face.
"""
from __future__ import annotations

import argparse
from .file_locks import fcntl  # the standard module, or Windows' refusing stand-in
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from gpu_worker import manifests as catalog
from gpu_worker import model_files

CHUNK = 1 << 20  # bytes read or written at a time
SPARE = 1 << 30  # free space kept beyond what a download needs
RECEIPT = model_files.RECEIPT
VERIFY_S = 1800  # whole background integrity pass; an interrupted pass is never a successful verification
HF = "https://huggingface.co"


class Failed(RuntimeError):
    """A download or an adopt that did not finish, in plain words, with the step that fixes it (readiness.Step kinds:
    "key", "licence", "retry", "disk") when there is one."""

    def __init__(self, says: str, step: str = "retry", url: str = "") -> None:
        super().__init__(says)
        self.says, self.step, self.url = says, step, url


class Cancelled(RuntimeError):
    pass


# --- the files and their content ids --------------------------------------------------------------------------------
def content_id(path: Path, f: dict, stop: Callable[[], bool] = lambda: False) -> str:
    """The file's content id as the manifest pins it: sha256, or the git blob id of a small file."""
    if f.get("sha256"):
        h = hashlib.sha256()
    else:
        h = hashlib.sha1()
        h.update(b"blob %d\0" % path.stat().st_size)
    with path.open("rb") as handle:
        while block := handle.read(CHUNK * 8):
            if stop():
                raise Cancelled()
            h.update(block)
    return h.hexdigest()


def problem(path: Path, f: dict, stop: Callable[[], bool] = lambda: False) -> str:
    """"" when the file is the one the manifest pins, else what is wrong."""
    try:
        size = path.stat().st_size
    except OSError:
        return f"{f['path']} is missing"
    if size != f["bytes"]:
        return f"{f['path']} is {size} bytes, not {f['bytes']}"
    want = f.get("sha256") or f.get("git_sha1")
    if want and content_id(path, f, stop) != want:
        return f"{f['path']} is not the file the catalog pins (its content differs)"
    return ""


def url_of(o: catalog.Manifest, f: dict) -> str:
    if f.get("from"):
        return f"{HF}/{f['from']['hf']}/resolve/{f['from']['revision']}/{f['from']['path']}"
    if f.get("url"):
        return f["url"]
    if o.source.get("hf") and o.source.get("revision"):
        return f"{HF}/{o.source['hf']}/resolve/{o.source['revision']}/{f['path']}"
    raise Failed(f"{o.name} says no place to download {f['path']} from", step="")


def size_words(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{max(1, round(n / 1e6))} MB"


# --- staging and publishing ------------------------------------------------------------------------------------------
def staging(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> Path:
    o = model_files.owner(m)
    return model_files.folder(o, environ) / f".staging-{catalog.revision_of(o)}"


class Lock:
    """One writer per model folder (the app's queue, or a `make catalog-download` beside it): a second is refused."""

    def __init__(self, m: catalog.Manifest, environ: Mapping[str, str] = os.environ, *, shared: bool = False) -> None:
        self.path = model_files.folder(model_files.owner(m), environ) / ".lock"
        self.handle = None
        self.shared = shared

    def __enter__(self) -> "Lock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a")
        try:
            fcntl.flock(self.handle, (fcntl.LOCK_SH if self.shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            raise Failed("it is already being downloaded or used by an open tool; wait for that work before retrying", step="") from None
        return self

    def __exit__(self, *exc) -> None:
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


def _writable(path: Path) -> None:
    for p in [path, *path.rglob("*")] if path.is_dir() else [path]:
        if not p.is_symlink():
            p.chmod(p.stat().st_mode | stat.S_IWUSR)


def _read_only(path: Path, itself: bool = True) -> None:
    """Everything in `path` read-only, and `path` too unless `itself` is False: macOS 15 refuses to rename a folder
    that is itself read-only, even within its own parent, so a staging folder is made read-only only once renamed."""
    for p in sorted(path.rglob("*"), key=lambda p: -len(p.parts)) + ([path] if itself else []):
        if not p.is_symlink():
            p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def publish(m: catalog.Manifest, how: str, environ: Mapping[str, str] = os.environ,
            stop: Callable[[], bool] = lambda: False, note: Callable[[str], None] = lambda s: None) -> Path:
    """Verify the staging folder against the manifest and publish it as the model's revision (see the docstring)."""
    o = model_files.owner(m)
    stage = staging(o, environ)
    wrong = []
    for f in o.files:
        note(f"checking {f['path']}")
        why = problem(stage / f["path"], f, stop)
        if why:
            wrong.append(why)
            if (stage / f["path"]).exists():
                (stage / f["path"]).unlink()  # never kept: the next try fetches it again
    if wrong:
        raise Failed("a downloaded file didn't match what the catalog pins, so nothing was used: "
                     + "; ".join(wrong[:3]) + ". Try again.")
    receipt = {"model": o.id, "revision": catalog.revision_of(o), "source": o.source, "how": how,
               "when": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "files": [{k: f[k] for k in ("path", "bytes", "sha256", "git_sha1") if k in f} for f in o.files]}
    (stage / RECEIPT).write_text(json.dumps(receipt, indent=1) + "\n")
    for leftover in stage.rglob("*.part"):
        leftover.unlink()
    dest = model_files.revision_dir(o, environ)
    if dest.exists() and model_files.published(o, environ) is not None:
        _writable(stage)
        shutil.rmtree(stage)  # it was published meanwhile: the one there stays as it is
        return dest
    from . import generation_leases
    try:
        with generation_leases.withdraw(dest):
            if dest.exists():
                aside = dest.with_name(f".incomplete-{dest.name}-{int(time.time())}")
                _writable(dest)
                dest.rename(aside)  # kept beside, never deleted
            _read_only(stage, itself=False)
            stage.rename(dest)
            dest.chmod(dest.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    except generation_leases.Busy as error:
        raise Failed('These model files still have running work. Close its tools, then retry; the prepared download is kept.', step='') from error
    shutil.rmtree(stage.parent / ".archives", ignore_errors=True)  # its members are published
    return dest


# --- downloading -----------------------------------------------------------------------------------------------------
@dataclass
class Progress:
    got: int = 0
    total: int = 0
    now: str = ""


def _headers(url: str, environ: Mapping[str, str]) -> dict:
    token = environ.get("HF_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token and url.startswith(HF + "/") else {}


def _said(headers: Mapping[str, str] | None, environ: Mapping[str, str]) -> str:
    """Hugging Face's own words for a refusal (its X-Error-Message: "awaiting a review", "ask for access"), one bounded
    line with no key in it, or nothing."""
    words = " ".join(str((headers or {}).get("x-error-message") or "").split())[:300]
    token = environ.get("HF_TOKEN")
    return words.replace(token, "<key>") if token else words


def _refused(o: catalog.Manifest, status: int, environ: Mapping[str, str], headers: Mapping[str, str] | None = None
             ) -> Failed:
    """Why a download was refused, as a step. Hugging Face answers a key it doesn't accept with 401 and an account it
    hasn't given access with 403 (both GatedRepo, observed 2026-10-20), so with a key a 401 is the key, never the licence
    (F057: a revoked or mistyped key sent people to accept a licence they had accepted)."""
    licence = next((g.split(":", 1)[1] for g in o.needs_to_get if g.startswith("licence:")), "")
    said = _said(headers, environ)
    quoted = f" Hugging Face said: “{said}”" if said else ""
    if status == 401 and not environ.get("HF_TOKEN"):
        return Failed(f"{o.name} needs your Hugging Face key to download", step="key")
    if status == 401:
        return Failed(f"Hugging Face didn't accept your key for {o.name} (mistyped, revoked or expired): replace it in "
                      f"Keys, then download again.{quoted}", step="key")
    if status == 403 and licence:
        return Failed(f"Hugging Face hasn't let the account your key belongs to download {o.name} yet: accept its "
                      f"licence on its page with that account, then download again.{quoted}", step="licence", url=licence)
    return Failed(f"the server refused to send {o.name}'s files ({status}).{quoted}")


def fetch(o: catalog.Manifest, f: dict, dest: Path, progress: Progress, *, client, environ: Mapping[str, str],
          stop: Callable[[], bool], wait: Callable[[], None]) -> None:
    """One file into `dest`, resuming a `.part` where the server supports it and starting again where it doesn't."""
    import httpx

    if dest.exists() and dest.stat().st_size == f["bytes"]:
        progress.got += f["bytes"]  # fetched before; checked with the rest
        return
    part = dest.with_name(dest.name + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    url = url_of(o, f)
    if f.get("archive"):  # one member of a published archive: the archive is fetched (once) and checked, then unpacked
        archive = archive_path(o, f, environ)
        whole = {"path": archive.name, "bytes": f["archive"]["bytes"], "sha256": f["archive"]["sha256"]}
        if problem(archive, whole, stop):
            archive.unlink(missing_ok=True)
            fetch(o, {**whole, "url": url}, archive, Progress(), client=client, environ=environ, stop=stop, wait=wait)
            why = problem(archive, whole, stop)
            if why:
                archive.unlink(missing_ok=True)
                raise Failed(f"{o.name}'s archive didn't match what the catalog pins, so nothing was used. Try again.")
        import zipfile

        with zipfile.ZipFile(archive) as z, z.open(f["archive"]["member"]) as src, part.open("wb") as out:
            shutil.copyfileobj(src, out, CHUNK)
        part.rename(dest)
        progress.got += f["bytes"]
        return
    if url.startswith("file://"):  # a file the user added, fetched again from where it was (mine.py)
        from urllib.parse import unquote, urlparse

        src = Path(unquote(urlparse(url).path))
        if not src.is_file():
            raise Failed(f"{f['path']} is no longer at {src}: add it again", step="")
        clone(src, dest)
        progress.got += f["bytes"]
        return
    counted = progress.got
    try:
        _stream(o, f, url, part, progress, client=client, environ=environ, stop=stop, wait=wait)
    except Failed as exc:
        mirror = f.get("mirror") or {}
        if not mirror or exc.step in ("key", "licence", "disk"):
            raise
        # Its own place is gone or down: its mirror, whose redistribution rights the entry states (C7). The bytes are
        # still checked against the pinned sha256 before anything is published (publish).
        part.unlink(missing_ok=True)
        progress.got = counted
        progress.now = f"{f['path']} from its mirror ({str(mirror['rights'])[:80]})"
        try:
            _stream(o, f, mirror["url"], part, progress, client=client, environ=environ, stop=stop, wait=wait)
        except Failed as again:
            raise Failed(f"{exc} Its mirror didn't send it either ({again}).", step=again.step) from again
    if part.exists():
        part.rename(dest)


def _stream(o: catalog.Manifest, f: dict, url: str, part: Path, progress: "Progress", *, client,
            environ: Mapping[str, str], stop: Callable[[], bool], wait: Callable[[], None]) -> None:
    """One address of one file into its `.part`, resuming where the server supports it."""
    import httpx

    for _attempt in range(2):
        start = part.stat().st_size if part.exists() else 0
        if start > f["bytes"]:
            part.unlink()
            start = 0
        headers = _headers(url, environ) | ({"Range": f"bytes={start}-"} if start else {})
        try:
            with client.stream("GET", url, headers=headers, follow_redirects=True) as r:
                if r.status_code == 416 or (start and r.status_code == 200):
                    part.unlink(missing_ok=True)  # the server can't resume here: start this file again, safely
                    if r.status_code == 416:
                        continue
                    start = 0
                if r.status_code in (401, 403):
                    raise _refused(o, r.status_code, environ, r.headers)
                if r.status_code == 404:
                    raise Failed(f"{f['path']} isn't at {url.split('?')[0]} any more: the catalog entry needs updating",
                                 step="")
                r.raise_for_status()
                progress.got += start
                with part.open("ab" if start else "wb") as out:
                    for block in r.iter_bytes(CHUNK):
                        if stop():
                            raise Cancelled()
                        wait()
                        out.write(block)
                        progress.got += len(block)
            break
        except httpx.HTTPStatusError as exc:
            raise Failed(f"the server couldn't send {f['path']} ({exc.response.status_code}). Try again: it carries "
                         "on where it stopped") from exc
        except (httpx.TransportError, OSError) as exc:
            if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
                raise Failed("the disk filled up while downloading", step="disk") from exc
            raise Failed(f"the connection dropped while downloading {f['path']}. Try again: it carries on where it "
                         "stopped") from exc


def archive_path(o: catalog.Manifest, f: dict, environ: Mapping[str, str] = os.environ) -> Path:
    """Where a pinned archive is kept while its members are taken out: beside the staging folder, by its content id,
    removed once the revision is published."""
    return staging(o, environ).parent / ".archives" / f"{f['archive']['sha256']}.zip"


def needs_bytes(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> int:
    """What is still to fetch: the pinned files not already whole in the staging folder."""
    stage = staging(m, environ)
    total = 0
    for f in model_files.files(m):
        path = stage / f["path"]
        have = path.stat().st_size if path.exists() else 0
        part = path.with_name(path.name + ".part")
        have = have if have == f["bytes"] else (part.stat().st_size if part.exists() else 0)
        total += max(0, f["bytes"] - have)
    return total


def disk(environ: Mapping[str, str] = os.environ) -> dict:
    """The space free on the disk models are downloaded to (the catalog's folder, or the nearest folder of it that exists
    yet), with the spare a download keeps beyond its own bytes: Turn on's sheet weighs a plan's downloads against it the
    way `download` does before it fetches anything. One statfs; {} when it can't be read (the sheet says so)."""
    probe = catalog.catalog_root(environ)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return {"freeBytes": shutil.disk_usage(probe).free, "spareBytes": SPARE}
    except OSError:
        return {}


def _require_machine(m: catalog.Manifest) -> None:
    from . import readiness

    if why := readiness.misfit(m):
        raise Failed(f"{m.name} cannot be prepared on this computer: {why}", step="requirements")


def download(m: catalog.Manifest, *, environ: Mapping[str, str] = os.environ, progress: Progress | None = None,
             stop: Callable[[], bool] = lambda: False, wait: Callable[[], None] = lambda: None, client=None) -> Path:
    """Fetch the model's pinned files, verify them and publish the revision. Returns its folder."""
    import httpx

    _require_machine(m)
    o = model_files.owner(m)
    progress = progress or Progress()
    if not o.files:
        raise Failed(f"{o.name} pins no files to download", step="")
    if model_files.published(o, environ) is not None:
        return model_files.revision_dir(o, environ)
    reused = adopt_twin(o, environ, stop=stop)  # already here for another entry: never downloaded again
    if reused is not None:
        return reused[0]
    missing = [g for g in o.needs_to_get if g.startswith("key:") and not environ.get(g.split(":", 1)[1])]
    if missing:
        raise _refused(o, 401, environ)
    with Lock(o, environ):
        stage = staging(o, environ)
        stage.mkdir(parents=True, exist_ok=True)
        _writable(stage)
        need = needs_bytes(o, environ)
        free = shutil.disk_usage(stage).free
        if need + SPARE > free:
            raise Failed(f"{o.name} needs {size_words(need)} and the disk has {size_words(free)} free: make room, "
                         "then download again", step="disk")
        progress.total = sum(f["bytes"] for f in o.files)
        own = client is None
        client = client or httpx.Client(timeout=httpx.Timeout(30, read=60))
        try:
            for f in o.files:
                progress.now = f["path"]
                fetch(o, f, stage / f["path"], progress, client=client, environ=environ, stop=stop, wait=wait)
        finally:
            if own:
                client.close()
        progress.now = "checking the files"
        return publish(o, "downloaded", environ, stop=stop)


# --- checking what was published, again --------------------------------------------------------------------------
def verify_published(m: catalog.Manifest, environ: Mapping[str, str] = os.environ,
                     stop: Callable[[], bool] = lambda: False) -> str | None:
    """Compute each published file's content id again and keep the answer beside the revision
    (model_files.verification): "" when every file is the one pinned, the problem when one is not (that revision then
    counts as not downloaded, and Download fetches it again, the changed one kept aside), None when nothing is
    published. The size is checked at every read; this is the content (the catalog audit's G8)."""
    o = model_files.owner(m)
    d = model_files.revision_dir(o, environ)
    try:
        json.loads((d / RECEIPT).read_text())
    except (OSError, ValueError):
        return None
    wrong = next((why for f in o.files if (why := problem(d / f["path"], f, stop))), "")
    path = model_files.verification_path(o, environ)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"ok": not wrong, "problem": wrong, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
    tmp.replace(path)
    return wrong


def verify_all(environ: Mapping[str, str] = os.environ, stop: Callable[[], bool] = lambda: False,
               changed: Callable[[], None] = lambda: None) -> dict[str, str]:
    """Every published revision checked again, one at a time at background priority (thumbnails.background_priority:
    efficiency cores, throttled disk), so the live view never waits on it. Run once per start (App.start)."""
    from .thumbnails import background_priority

    background_priority()
    out: dict[str, str] = {}
    for m in {**catalog.manifests(), **catalog.mine(environ)}.values():
        if stop() or m.files_from or not m.files:
            continue
        try:
            got = verify_published(m, environ, stop)
        except (Cancelled, OSError):
            continue
        if got is not None:
            out[m.key] = got
            if got:
                changed()
    return out


# --- adopting what this Mac already has ------------------------------------------------------------------------------
def clone(src: Path, dest: Path) -> bool:
    """Copy one file as an APFS clone (shared blocks until either changes); a plain copy across volumes. True: cloned."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        _writable(dest)
        dest.unlink()
    if sys.platform == "darwin" and subprocess.run(["cp", "-c", str(src), str(dest)], capture_output=True).returncode == 0:
        return True
    shutil.copyfile(src, dest)
    return False


def adopt_file(m: catalog.Manifest, path: Path, environ: Mapping[str, str] = os.environ) -> Path:
    """Publish a model whose one file is on this Mac already (a file the user added: mine.py): cloned, checked, published."""
    o = model_files.owner(m)
    if model_files.published(o, environ) is not None:
        return model_files.revision_dir(o, environ)
    with Lock(o, environ):
        stage = staging(o, environ)
        stage.mkdir(parents=True, exist_ok=True)
        _writable(stage)
        clone(Path(path).resolve(), stage / o.files[0]["path"])
        return publish(o, "added from your file", environ)


def adopt(m: catalog.Manifest, environ: Mapping[str, str] = os.environ, stop: Callable[[], bool] = lambda: False
          ) -> tuple[Path, bool]:
    """Publish the model's revision from the old caches (each file found at the pinned revision, checked). Returns
    (its folder, whether every file was cloned). Fails when a file is not there: that one is downloaded instead."""
    o = model_files.owner(m)
    if model_files.published(o, environ) is not None:
        return model_files.revision_dir(o, environ), True
    found = {f["path"]: model_files.legacy_file(o, f, environ) for f in o.files}
    absent = [p for p, at in found.items() if at is None]
    if not o.files or absent:
        raise Failed(f"{o.name} isn't all on this Mac at its pinned revision (missing {', '.join(absent[:3])}): "
                     "download it instead", step="")
    cloned = True
    with Lock(o, environ):
        stage = staging(o, environ)
        stage.mkdir(parents=True, exist_ok=True)
        _writable(stage)
        for f in o.files:
            cloned = clone(found[f["path"]].resolve(), stage / f["path"]) and cloned
        return publish(o, "adopted" if cloned else "copied", environ, stop=stop), cloned


def _identity(o: catalog.Manifest) -> tuple:
    return catalog.revision_of(o), model_files.pinned_identity(o.files)


def twin(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> catalog.Manifest | None:
    """Another entry (the catalog's or yours) that pins exactly this model's files at the same revision, by path, size and
    content id, and has them published here, whole and not found changed; else None."""
    o = model_files.owner(m)
    if not o.files or any(f.get("from") or f.get("archive") for f in o.files):
        return None
    want = _identity(o)
    for other in {**catalog.manifests(), **catalog.mine(environ)}.values():
        if other.key == o.key or other.files_from or not other.files:
            continue
        if _identity(other) == want and model_files.published(other, environ) is not None:
            return other
    return None


def adopt_twin(m: catalog.Manifest, environ: Mapping[str, str] = os.environ, stop: Callable[[], bool] = lambda: False
               ) -> tuple[Path, catalog.Manifest] | None:
    """Publish the model's revision from its twin's (`twin`): each file cloned (blocks shared, as `adopt`), checked by its
    content id against this model's own manifest, published read-only; the twin's revision is only read. Returns (its
    folder, the twin), or None when nothing here is the same (it is downloaded then)."""
    o = model_files.owner(m)
    if model_files.published(o, environ) is not None:
        return None
    other = twin(o, environ)
    if other is None:
        return None
    source = model_files.published(other, environ)
    if source is None:
        return None
    with Lock(o, environ):
        stage = staging(o, environ)
        stage.mkdir(parents=True, exist_ok=True)
        _writable(stage)
        for f in o.files:
            clone(source / f["path"], stage / f["path"])
        return publish(o, f"reused from {other.key} (the same files, already on this Mac)", environ, stop=stop), other


# --- the app's queue ---------------------------------------------------------------------------------------------------
class Downloads:
    """One download at a time, in a thread of its own; each a line in Activity and a row on the wire. `changed` is
    called (from that thread) when a row changes, at most twice a second while bytes arrive; `busy` says the live
    view is waiting on a click (the download waits, a few milliseconds at a time)."""

    def __init__(self, hub=None, changed: Callable[[], None] = lambda: None, busy: Callable[[], bool] = lambda: False,
                 environ: Mapping[str, str] = os.environ) -> None:
        self.hub, self.changed, self.busy, self.environ = hub, changed, busy, environ
        self.rows: dict[str, dict] = {}
        self._queue: list[str] = []
        self._cancel: set[str] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._told = 0.0

    def wire(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.rows.values()]

    def start(self, model_id: str, by: str = "you") -> dict:
        m = catalog.get(model_id)
        if m is None or m.runs == "cloud":
            raise Failed(f"there is no model on this Mac called {model_id}", step="")
        _require_machine(m)
        o = model_files.owner(m)
        if not o.files:
            raise Failed(f"{m.name} has no files to download", step="")
        missing = [g for g in o.needs_to_get if g.startswith("key:") and not self.environ.get(g.split(":", 1)[1])]
        if missing:
            raise _refused(o, 401, self.environ)
        with self._lock:
            row = self.rows.get(o.key)
            if row and row["state"] in ("queued", "downloading", "checking"):
                return dict(row)
            total = sum(f["bytes"] for f in o.files)
            self.rows[o.key] = row = {"model": o.key, "name": o.name, "state": "queued", "got": 0, "total": total,
                                     "by": by, "since": time.time(), "says": f"Waiting to download {o.name}",
                                     "activity": f"download:{o.key}:{int(time.time())}"}
            self._queue.append(o.key)
            self._cancel.discard(o.key)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="catalog-downloads", daemon=True)
                self._thread.start()
        self._say(row, "running", f"Downloading {o.name} ({size_words(total)})")
        self.changed()
        return dict(row)

    def cancel(self, model_id: str, *, by: str | None = None) -> dict:
        m = catalog.get(model_id)
        key = model_files.owner(m).key if m else model_id
        with self._lock:
            row = self.rows.get(key)
            if row is None or row["state"] not in ("queued", "downloading", "checking"):
                raise Failed("nothing is downloading for that model", step="")
            if by is not None and row.get("by") != by:
                return dict(row)
            self._cancel.add(key)
            if key in self._queue:
                self._queue.remove(key)
                row.update(state="cancelled", says=f"Download of {row['name']} cancelled")
        if row["state"] == "cancelled":
            self._say(row, "failed", row["says"])
            self.changed()
        return dict(row)

    def _say(self, row: dict, state: str, text: str, lines: list[str] | None = None) -> None:
        try:
            from . import activity

            activity.activity(self.hub, "model", text, id=row["activity"], state=state, ref={"model": row["model"]},
                              details={"lines": lines} if lines else None)
        except Exception:  # noqa: BLE001 - the wire says it either way
            pass

    def _wait(self) -> None:
        for _ in range(100):  # at most a second at a time: a click is answered in a few hundred ms
            try:
                if not self.busy():
                    return
            except Exception:  # noqa: BLE001
                return
            time.sleep(0.01)

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    return
                key = self._queue.pop(0)
                row = self.rows[key]
                row.update(state="downloading", says=f"Downloading {row['name']}")
            self.changed()
            m = catalog.get(key)
            progress = Progress(total=row["total"])

            def stop() -> bool:
                now = time.monotonic()
                if now - self._told > 0.5:
                    self._told = now
                    with self._lock:
                        row.update(got=progress.got, now=progress.now,
                                   state="checking" if progress.now == "checking the files" else row["state"])
                    self.changed()
                return key in self._cancel

            try:
                t0 = time.monotonic()
                where, how = None, "downloaded"
                if model_files.location(m, self.environ)[0] == "old cache":
                    where, cloned = adopt(m, self.environ, stop=stop)  # already on this Mac: no network
                    how = "found on this Mac" if cloned else "copied from where it was on this Mac"
                elif (reused := adopt_twin(m, self.environ, stop=stop)) is not None:
                    where, how = reused[0], f"reused from {reused[1].name} (the same files, already on this Mac)"
                else:
                    where = download(m, environ=self.environ, progress=progress, stop=stop, wait=self._wait)
                with self._lock:
                    row.update(state="done", got=row["total"], says=f"{row['name']} is downloaded", folder=str(where))
                if any(n.startswith("licence:") for n in m.needs_to_get):
                    from .observed import record

                    record("access", key, "yes", "Hugging Face: your account has access (it downloaded)", "a download",
                           self.environ)
                self._say(row, "done", f"Downloaded {row['name']} ({size_words(row['total'])})",
                          [f"{how.capitalize()} in {round(time.monotonic() - t0)} s, checked against the catalog",
                           f"Revision {catalog.short_revision(m)}, in {where}"])
            except Cancelled:
                with self._lock:
                    row.update(state="cancelled", says=f"Download of {row['name']} cancelled: what it got is kept "
                                                       "for next time")
                self._say(row, "failed", row["says"])
            except Failed as exc:
                with self._lock:
                    row.update(state="failed", says=exc.says, step=exc.step, url=exc.url)
                # What the real attempt ran into is what the app knows now (observed.py): a licence not granted yet.
                if exc.step == "licence":
                    from .observed import record

                    record("access", key, "no", "Hugging Face refused the download: your account hasn't been given access "
                                                "yet (accept its licence on its page)", "a download", self.environ)
                self._say(row, "failed", f"Couldn't download {row['name']}: {exc.says}")
            except Exception as exc:  # noqa: BLE001 - said, never a stuck "downloading"
                with self._lock:
                    row.update(state="failed", says=f"something went wrong ({type(exc).__name__}). Try again",
                               step="retry")
                self._say(row, "failed", f"Couldn't download {row['name']}: {row['says']}")
            finally:
                self._cancel.discard(key)
            self.changed()


# --- the command line: make catalog-download / catalog-adopt -------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download or adopt catalog models into the models folder.")
    parser.add_argument("action", choices=("get", "adopt", "verify"))
    parser.add_argument("ids", nargs="*", help="model ids (adopt: every model when none)")
    args = parser.parse_args(argv)
    if args.action == "verify":  # App.verify_downloads: isolated background process, only JSON on stdout
        print(json.dumps(verify_all()))
        return 0
    from .keys import load_keys
    from .paths import Paths

    load_keys(Paths.from_env().keys_file)  # as the app does at start: a gated model's key (HF_TOKEN), never printed
    local = {i: m for i, m in catalog.manifests().items() if m.runs != "cloud" and m.files and not m.files_from}
    if args.action == "adopt" and not args.ids:  # fine-tuned versions made before they were kept in the catalog
        from . import finetuned
        from .paths import Paths

        for m in catalog.manifests().values():
            if m.source.get("made_here"):
                for v in finetuned.adopt_old(Paths.from_env().models, m.id):
                    print(f"{m.id} {v}: adopted into {finetuned.root() / m.id / v}")
    ids = args.ids or (list(local) if args.action == "adopt" else [])
    if not ids:
        parser.error("name a model: make catalog-download ID=<id>")
    failed = 0
    for i in ids:
        m = catalog.get(i)
        if m is None or not model_files.files(m):
            print(f"{i}: not a model with files in the catalog")
            failed += 1
            continue
        try:
            if args.action == "adopt":
                if model_files.location(m)[0] is None:
                    print(f"{i}: not on this Mac yet: make catalog-download ID={i}")
                    continue
                where, cloned = adopt(m)
                print(f"{i}: {'cloned' if cloned else 'copied'} into {where}")
            else:
                p = Progress()
                done = threading.Event()

                def show() -> None:
                    while not done.wait(2):
                        print(f"  {p.now}: {size_words(p.got)} of {size_words(p.total)}", flush=True)

                threading.Thread(target=show, daemon=True).start()
                where = download(m, progress=p)
                done.set()
                print(f"{i}: downloaded and checked into {where}")
        except Failed as exc:
            print(f"{i}: {exc.says}" + (f" ({exc.url})" if exc.url else ""))
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
