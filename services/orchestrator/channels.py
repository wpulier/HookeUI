"""Channels: where catalog entries come from besides this app's own (phase 3's E4b; catalog/README.md "Channels").

A channel is a git repository's catalog (`catalog/models` unless it says another folder) at one commit. Its manifests and
adapters are fetched into `<catalog>/channels/<name>/<commit>/` (gpu_worker/manifests.py reads them), never its weights:
each entry's pinned files come with its own Download, checked as every model's are.

- **The reviewed channel** is this repository. "Check for catalog updates" (`update`) pins its newest commit, so an
  entry merged after this app was built is listed at once and turned on by its card's Download and Install, with no app
  update. It adds only entries this app doesn't bundle: an update to a bundled one comes with the app's own.
- **A community channel** is any repository someone publishes a catalog in, added by its address and pinned at a commit
  (`add`; the newest when none is given, then kept until the user updates it). Everything from it is said to be "not
  reviewed here", and nothing of it runs until the user allows code from it (`trust`, ADR 0021's consent), on top of each
  entry's own Install and proof.
- **Only entries with their own adapter** (runner python-env) come through a channel; any other needs the app's own code,
  is listed with why, and comes with an app update (manifests.CHANNEL_RUNNERS).
- **Peeked at, by the app on its own** (`peek`; catalog_news.py, C1): the daily check takes only each entry's
  `model.toml` and its pictures (no adapter, no lockfile, no code of any kind) into `<catalog>/news/<name>/<commit>/`,
  which nothing installs or runs from. Its entries are offered as new; the user's Get it fetches that same commit in full
  (`update` with `commit`), as Check for catalog updates does: code arrives only on a press.
- **Fetched safely:** public GitHub catalogs use bounded HTTPS tree/blob reads at the pinned commit, each blob checked
  against its Git identity, without developer tools. Other Git hosts and local repositories retain the Git transport.
  Both publish the catalog in one rename with read-only files. A commit is never written over.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from typing import Mapping

from gpu_worker import manifests as catalog

REVIEWED_URL = "https://github.com/wpulier/HookeUI.git"
CATALOG_PATH = "catalog/models"
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
COMMIT = re.compile(r"[0-9a-f]{40}")
RESERVED = {"mine", catalog.REVIEWED_CHANNEL}
GIT_S = 300
_snapshot_lock = threading.RLock()


class CantDo(RuntimeError):
    pass


def config(environ: Mapping[str, str] = os.environ) -> Path:
    return catalog.channels_root(environ) / "channels.json"


def _save(found: list[dict], environ: Mapping[str, str]) -> None:
    path = config(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(found, indent=1) + "\n")
    tmp.replace(path)


def _git(*args: str, cwd: Path | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if k in ("HOME", "PATH", "TMPDIR", "SSL_CERT_FILE")}
    env["GIT_TERMINAL_PROMPT"] = "0"  # never asks for a password: a channel is public
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env, timeout=GIT_S)
    except FileNotFoundError as exc:
        raise CantDo("git isn't installed on this Mac (xcode-select --install)") from exc
    except subprocess.TimeoutExpired as exc:
        raise CantDo(f"git {args[0]} took longer than {GIT_S // 60} minutes") from exc
    if done.returncode:
        last = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["(no words)"]
        raise CantDo(f"git {args[0]} failed: {last[0][:300]}")
    return done.stdout


def _check_url(url: str) -> str:
    url = url.strip()
    if url.startswith("https://") or (Path(url).is_absolute() and Path(url).exists()):
        return url
    raise CantDo("a channel is a git repository's https:// address (or a folder on this Mac)")


def newest(url: str) -> str:
    """The commit a repository's default branch is at now."""
    from . import catalog_remote
    if repo := catalog_remote.repository(url):
        return catalog_remote.newest(repo)
    for line in _git("ls-remote", url, "HEAD").splitlines():
        sha = line.split()[0] if line.split() else ""
        if COMMIT.fullmatch(sha):
            return sha
    raise CantDo(f"{url} says no commit for its default branch")


PEEK = (f"*/{catalog.MANIFEST}", "*/*.png", "*/*.jpg", "*/*.jpeg", "*/*.webp")  # what a peek takes: never code


def fetch(url: str, commit: str, dest: Path, path: str = CATALOG_PATH, include: tuple[str, ...] | None = None) -> int:
    """The repository's catalog folder at `commit` into `dest` (see the module's docstring). Returns how many entries.
    `include=PEEK` uses a separate bounded metadata transport: a Git fetch cannot guarantee blob filtering."""
    if include is not None:
        from . import catalog_metadata

        if include != PEEK:
            raise CantDo('automatic discovery only reads manifests and preview pictures')
        return catalog_metadata.fetch(url, commit, dest, path)
    if not COMMIT.fullmatch(commit):
        raise CantDo(f"{commit!r} is no full commit (40 hex characters)")
    from . import catalog_remote
    if repo := catalog_remote.repository(url):
        return catalog_remote.fetch(repo, commit, dest, path)
    if dest.is_dir():
        return sum(1 for _ in dest.glob(f"*/{catalog.MANIFEST}"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".fetching-", dir=dest.parent))
    try:
        repo = work / "git"
        _git("init", "-q", str(repo))
        _git("remote", "add", "origin", url, cwd=repo)
        try:  # only the commit, and only the blobs the catalog folder needs
            _git("fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", commit, cwd=repo)
        except CantDo:  # a server that doesn't filter
            _git("fetch", "-q", "--depth", "1", "origin", commit, cwd=repo)
        got = _git("rev-parse", "FETCH_HEAD^{commit}", cwd=repo).strip()
        if got != commit:
            raise CantDo(f"asked for commit {commit[:12]}, got {got[:12]}")
        if not _git("ls-tree", "-d", commit, "--", path, cwd=repo).strip():
            raise CantDo(f"commit {commit[:12]} has no {path} folder")
        tar = work / "catalog.tar"
        _git("archive", "--format=tar", "-o", str(tar), commit, "--", path, cwd=repo)
        out = work / "out"
        with tarfile.open(tar) as archive:
            archive.extractall(out, filter="data")  # no links out of it, no absolute paths, no devices
        found = out / path
        if not found.is_dir():
            raise CantDo(f"commit {commit[:12]} has no {path} folder")
        for p in found.rglob("*"):
            if p.is_file():
                p.chmod(0o444)
        found.rename(dest)  # published whole, in one step
        return sum(1 for _ in dest.glob(f"*/{catalog.MANIFEST}"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def snapshot_dir(name: str, commit: str, environ: Mapping[str, str] = os.environ) -> Path:
    return catalog.channels_root(environ) / name / commit


def _same_snapshot(expected: Path, actual: Path) -> bool:
    """Compare an interrupted import with validated staging bytes, without following links."""
    from . import bounded_files

    def equal(folder: Path, fd: int) -> bool:
        children = list(folder.iterdir())
        names = set()
        with os.scandir(fd) as actual_children:
            for child in actual_children:
                names.add(child.name)
                if len(names) > len(children):
                    return False
        if {child.name for child in children} != names:
            return False
        for child in children:
            info = os.stat(child.name, dir_fd=fd, follow_symlinks=False)
            if child.is_dir():
                if not stat.S_ISDIR(info.st_mode):
                    return False
                nested = os.open(child.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if not equal(child, nested):
                        return False
                finally:
                    os.close(nested)
            else:
                data = child.read_bytes()
                if bounded_files.read_at(fd, child.name, len(data)) != data:
                    return False
        return True
    try:
        fd = os.open(actual, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            return equal(expected, fd)
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return False


def import_snapshot(name: str, identity: str, prepared: Path, label: str,
                    environ: Mapping[str, str] = os.environ) -> None:
    """Admit a validated share's private staging folder to the existing channel registry.

    `identity` is its complete SHA-256 content identity, stored in the legacy `commit` slot.
    The caller owns archive validation; no Git repository, process, install or execution is involved.
    """
    if not NAME.fullmatch(name) or name in RESERVED or not re.fullmatch(r'[0-9a-f]{64}', identity):
        raise CantDo('the shared catalog needs a valid name and complete content identity')
    with _snapshot_lock:
        found = catalog.channels(environ)
        existing = next((c for c in found if c['name'] == name), None)
        if existing:
            if existing.get('source') == 'shared-file' and existing.get('commit') == identity:
                return
            raise CantDo('a different catalog already uses this shared file name')
        dest = snapshot_dir(name, identity, environ)
        dest.parent.mkdir(parents=True, exist_ok=True)
        published = False
        if dest.exists() or dest.is_symlink():
            if not _same_snapshot(prepared, dest):
                raise CantDo('the stored shared snapshot differs from this file; it was kept unchanged for recovery')
        else:
            prepared.rename(dest)
            published = True
        found.append({'name': name, 'url': '', 'source': 'shared-file', 'commit': identity,
                      'reviewed': False, 'trusted': False, 'label': label,
                      'added': time.strftime('%Y-%m-%dT%H:%M:%S')})
        try:
            _save(found, environ)
        except Exception:
            # Only this attempt's newly published folder is ours to undo. If the config
            # write completed before an error surfaced, leave its referenced bytes intact.
            if published and not any(c.get('name') == name and c.get('commit') == identity for c in catalog.channels(environ)):
                shutil.rmtree(dest)
            raise
        catalog.manifests.cache_clear()


def _ids(c: dict, environ) -> set[str]:
    d = catalog.snapshot(c, environ)
    return {p.parent.name for p in d.glob(f"*/{catalog.MANIFEST}")} if d else set()


def peek_dir(name: str, commit: str, environ: Mapping[str, str] = os.environ) -> Path:
    return catalog.catalog_root(environ) / "news" / name / commit


def peek(name: str = catalog.REVIEWED_CHANNEL, environ: Mapping[str, str] = os.environ) -> dict:
    """What the reviewed catalog holds now, metadata only (see the module's docstring): {"name", "commit", "entries",
    "folder"}. Nothing here is listed in the catalog, installed or run."""
    c = next((c for c in catalog.channels(environ) if c["name"] == name), None)
    url = (c or {}).get("url") or (environ.get("MICROMIND_REVIEWED_CHANNEL") or REVIEWED_URL
                                   if name == catalog.REVIEWED_CHANNEL else "")
    if not url:
        raise CantDo(f"there is no channel called {name}")
    target = newest(url)
    dest = peek_dir(name, target, environ)
    n = fetch(url, target, dest, (c or {}).get("path", CATALOG_PATH), include=PEEK)
    return {"name": name, "commit": target, "entries": n, "folder": str(dest)}


def update(name: str = catalog.REVIEWED_CHANNEL, environ: Mapping[str, str] = os.environ,
           commit: str | None = None) -> dict:
    """Pin a channel at its newest commit (or `commit`) and fetch it. Returns {"name", "commit", "was", "entries",
    "added": new entries listed now, "later": new ones this app can't run (an app update brings them)}. The reviewed
    channel is made the first time."""
    found = catalog.channels(environ)
    c = next((c for c in found if c["name"] == name), None)
    if c is None:
        if name != catalog.REVIEWED_CHANNEL:
            raise CantDo(f"there is no channel called {name}")
        c = {"name": name, "url": environ.get("MICROMIND_REVIEWED_CHANNEL") or REVIEWED_URL, "path": CATALOG_PATH,
             "reviewed": True}
        found.append(c)
    before, was = _ids(c, environ), c.get("commit", "")
    if c.get('source') == 'shared-file':
        if commit and commit != was:
            raise CantDo('a shared file has no online revisions; import the newer file to review its changes')
        return {'name': name, 'commit': was, 'was': was, 'entries': len(before), 'added': [], 'later': [],
                'says': 'This is a shared file, not an online catalog. Import a newer file to review its changes.'}
    target = commit or newest(c["url"])
    n = fetch(c["url"], target, snapshot_dir(name, target, environ), c.get("path", CATALOG_PATH))
    c.update(commit=target, checked=time.strftime("%Y-%m-%dT%H:%M:%S"))
    _save(found, environ)
    catalog.manifests.cache_clear()
    new = _ids(c, environ) - before
    listed = {m.id for m in catalog.manifests().values() if m.channel == name} | \
        {m.id for m in catalog.mine(environ).values() if m.channel == name}
    return {"name": name, "commit": target, "was": was, "entries": n, "added": sorted(new & listed),
            "later": sorted(i for i in new - listed if f"{name}/{i}" in catalog.problems(environ))}


def label(name: str, environ: Mapping[str, str] = os.environ) -> str:
    """How a channel is said to people: its label ("Maya Okafor's shared file", share.py), else its name."""
    c = next((c for c in catalog.channels(environ) if c["name"] == name), None)
    return str((c or {}).get("label") or name)


def add(url: str, name: str = "", commit: str = "", path: str = CATALOG_PATH,
        environ: Mapping[str, str] = os.environ, label: str = "") -> dict:
    """A community channel: added, pinned and fetched; nothing of it runs until `trust`."""
    pinned = re.fullmatch(r"(https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/tree/([0-9a-f]{40})/?", url)
    if pinned:
        if commit and commit != pinned[2]:
            raise CantDo("the supplied commit differs from this pinned community link")
        url, commit = pinned[1], pinned[2]
    url = _check_url(url)
    name = (name or re.sub(r"\.git$", "", url.rstrip("/").rsplit("/", 1)[-1])).lower()
    name = re.sub(r"[^a-z0-9-]+", "-", name).strip("-")
    if not NAME.fullmatch(name) or name in RESERVED:
        raise CantDo(f"{name!r} can't name a channel: use lowercase letters, digits and dashes (not mine or reviewed)")
    found = catalog.channels(environ)
    if any(c["name"] == name for c in found):
        raise CantDo(f"there is a channel called {name} already")
    if path.startswith("/") or ".." in Path(path).parts:
        raise CantDo("its catalog folder must be a folder inside the repository")
    target = commit or newest(url)
    n = fetch(url, target, snapshot_dir(name, target, environ), path)
    found.append({"name": name, "url": url, "path": path, "commit": target, "reviewed": False, "trusted": False,
                  "added": time.strftime("%Y-%m-%dT%H:%M:%S"), **({"label": label} if label else {})})
    _save(found, environ)
    catalog.manifests.cache_clear()
    return {"name": name, "commit": target, "entries": n}


def trust(name: str, allowed: bool = True, environ: Mapping[str, str] = os.environ) -> dict:
    found = catalog.channels(environ)
    c = next((c for c in found if c["name"] == name and not c.get("reviewed")), None)
    if c is None:
        raise CantDo(f"there is no community channel called {name}")
    c["trusted"] = allowed
    c["trustedAt" if allowed else "untrustedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _save(found, environ)
    catalog.manifests.cache_clear()
    return dict(c)


def remove(name: str, environ: Mapping[str, str] = os.environ) -> dict:
    """The channel is no longer read (its fetched commits go; what was installed from it stays until its Remove)."""
    found = catalog.channels(environ)
    c = next((c for c in found if c["name"] == name), None)
    if c is None:
        raise CantDo(f"there is no channel called {name}")
    _save([x for x in found if x is not c], environ)
    shutil.rmtree(catalog.channels_root(environ) / name, ignore_errors=True)  # read-only files: rmtree handles them
    catalog.manifests.cache_clear()
    return dict(c)


def trusted(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> bool:
    """Whether code of this entry may run: the app's, the reviewed channel's and the user's own entries always may; a
    community channel's only once the user allowed code from it."""
    if not m.channel:
        return True
    c = next((c for c in catalog.channels(environ) if c["name"] == m.channel), None)
    return bool(c and (c.get("reviewed") or c.get("trusted")))


def wire(environ: Mapping[str, str] = os.environ) -> list[dict]:
    """The channels for the Models page: each with its address, commit, when checked and how many entries."""
    out = []
    for c in catalog.channels(environ):
        out.append({"name": c["name"], "label": c.get("label") or c["name"], "url": c.get("url", ""), "commit": c.get("commit", ""),
                    "reviewed": bool(c.get("reviewed")), "trusted": bool(c.get("reviewed") or c.get("trusted")),
                    "checked": c.get("checked", c.get("added", "")), "entries": len(_ids(c, environ))})
    return out


def main(argv: list[str] | None = None) -> int:
    """make catalog-update [CHANNEL=] [COMMIT=]; make channel-add URL= [NAME=] [COMMIT=]."""
    import argparse

    parser = argparse.ArgumentParser(description="Catalog channels: check for updates, or add a community one.")
    parser.add_argument("action", choices=("update", "add", "list"))
    parser.add_argument("target", nargs="?", default="")
    parser.add_argument("--name", default="")
    parser.add_argument("--commit", default="")
    args = parser.parse_args(argv)
    try:
        if args.action == "update":
            # --commit pins that commit (C7 Window B: the CLI ignored it, so only Python could pin a channel back).
            r = update(args.target or catalog.REVIEWED_CHANNEL, commit=args.commit or None)
            print(f"{r['name']}: at {r['commit'][:12]}, {r['entries']} entries"
                  + (f"; new: {', '.join(r['added'])}" if r["added"] else ""))
        elif args.action == "add":
            r = add(args.target, args.name, args.commit)
            print(f"{r['name']}: added at {r['commit'][:12]}, {r['entries']} entries (community, not reviewed here; "
                  "nothing of it runs until you allow code from it)")
        else:
            for c in wire():
                print(f"{c['name']}: {c['url']} at {c['commit'][:12]}, {c['entries']} entries"
                      + ("" if c["reviewed"] else " (community" + (", allowed)" if c["trusted"] else ", not allowed)")))
    except CantDo as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
