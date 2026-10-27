"""Your own version when the one it was made from changes (ticket 59's MT2: conflicting adaptations).

A version of yours (`mine/<id>`, made by contribute.derive or an AI adaptation) names its base in `derived_from`: the
base's key, version and the digest of the base's own files as they were. When the base changes, the update candidate
for yours is derived from that recorded base, never guessed:

1. The base as you derived it is found by its digest among what this Mac kept: the channels' fetched commits
   (`<catalog>/channels/<name>/<commit>/`), maintenance's candidate snapshots
   (`<catalog>/maintenance/candidates/<channel>/<commit>/`), the installed generations' copies of the base
   (`envs/<key>/<gen>/entry/`), and the app's own catalog. When none is kept, every file both sides have differently
   is a conflict to resolve; once each is resolved, it merges like any other.
2. Each file is merged three ways (the base then, the base now, yours): only the update changed it, the update's; only
   you, yours; both alike, either; both differently, `git merge-file`: a clean merge is taken, a conflict keeps both
   sides with its markers (and both whole files beside it, `<file>.yours` and `<file>.update`).
3. The manifest is merged by field: what you changed stays yours, what the update changed is taken, a field both changed
   differently is a conflict; your id, name and setup steps stay yours, `derived_from` names the base now, and your
   version goes up by one.

The candidate is written to `<catalog>/maintenance/derived/<key>/<base digest>-<your digest>/` (16 characters of each)
with `merge.json` (each file's side). `apply` writes a clean candidate into your folder, keeping your files as they were
in `previous-<time>/` beside it; it refuses while a conflicted file is unresolved (unchanged since it was made, or still
holding a conflict marker), and names only those. Your folder is never written without that copy. Nothing here runs code.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Mapping

from gpu_worker import manifests as catalog

from . import store

MARKERS = ("<<<<<<< ", ">>>>>>> ")
MANAGED = ("id", "name", "version", "derived_from", "needs_to_get", "needs_to_run")  # set by derive, not merged


def derived(m: catalog.Manifest) -> bool:
    return m.namespace == "mine" and bool(m.derived_from)


_digests: dict[str, tuple[tuple, str]] = {}  # a folder's digest by its files' stats: a display read hashes once


def digest(folder: Path) -> str:
    """contribute.derive's digest of a folder's own files (envs.entry_files, top level only)."""
    files = sorted(p for p in folder.iterdir() if p.is_file() and _counted(p))
    stamp = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size, p.stat().st_ino) for p in files)
    kept = _digests.get(str(folder))
    if kept is not None and kept[0] == stamp:
        return kept[1]
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(folder).as_posix().encode() + b"\0" + p.read_bytes())
    _digests[str(folder)] = (stamp, h.hexdigest())
    return h.hexdigest()


def latest(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> tuple[catalog.Manifest | None, dict | None]:
    """(the base now when yours is stale, the merge report already made for it); a cheap read for status."""
    base = stale(m, environ)
    if base is None:
        return None, None
    now = digest(Path(catalog.entry_folder(base, environ)))
    yours = digest(Path(catalog.entry_folder(m, environ)))
    report = store.read(_target(m, now, yours, environ) / "merge.json", {})
    return base, report or None


def _counted(p: Path) -> bool:
    from .. import envs
    import fnmatch

    return (p.name not in envs.LOCAL and not p.name.startswith(".")
            and not any(fnmatch.fnmatch(p.name, pat) for pat in envs.SKIP))


def _kept_bases(base_key: str, base_id: str, environ: Mapping[str, str]) -> list[Path]:
    """Every copy of the base this Mac kept: fetched channel commits, installed generations, the app's own catalog."""
    root = catalog.catalog_root(environ)
    out = sorted(root.glob(f"channels/*/*/{base_id}"))
    out += sorted(store.path("candidates", environ).glob(f"*/*/{base_id}"))
    out += sorted((root / "envs" / base_key.replace("/", "--")).glob(f"*/entry/{base_id}"))
    if (catalog.ROOT / base_id).is_dir():
        out.append(catalog.ROOT / base_id)
    return [p for p in out if (p / catalog.MANIFEST).is_file()]


def base_then(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> Path | None:
    want = m.derived_from.get("sha256")
    base = catalog.get(m.derived_from["id"])
    base_id = base.id if base is not None else str(m.derived_from["id"]).rsplit("/", 1)[-1]
    for folder in _kept_bases(m.derived_from["id"], base_id, environ):
        if want and digest(folder) == want:
            return folder
    return None


def stale(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> catalog.Manifest | None:
    """The base now, when it is not what yours was made from; else None."""
    if not derived(m):
        return None
    base = catalog.get(m.derived_from["id"])
    if base is None:
        return None
    now = Path(catalog.entry_folder(base, environ))
    return base if digest(now) != m.derived_from.get("sha256") else None


def _merge_text(yours: Path, then: Path | None, update: Path, out: Path) -> str:
    """"same", "yours", "update", "merged" or "conflict"; writes `out`."""
    y = yours.read_bytes() if yours.is_file() else None
    t = then.read_bytes() if then is not None and then.is_file() else None
    u = update.read_bytes() if update.is_file() else None
    if y == u:
        if y is not None:
            out.write_bytes(y)
        return "same"
    if y == t:
        if u is not None:
            out.write_bytes(u)
        return "update"
    if u == t:
        if y is not None:
            out.write_bytes(y)
        return "yours"
    if y is None or u is None:  # removed on one side, changed on the other: the changed side kept, both said
        kept = y if y is not None else u
        out.write_bytes(kept)
        side = ".yours" if y is not None else ".update"
        out.with_name(out.name + side).write_bytes(kept)
        out.with_name(out.name + (".update" if y is not None else ".yours")).write_bytes(
            b"(removed on this side; delete the merged file to keep it removed)\n")
        return "conflict"
    with tempfile.TemporaryDirectory(prefix="merge-") as d:
        a, o, b = Path(d) / "yours", Path(d) / "then", Path(d) / "update"
        a.write_bytes(y)
        o.write_bytes(t or b"")
        b.write_bytes(u)
        done = subprocess.run(["git", "merge-file", "-p", "-L", "yours", "-L", "base then", "-L", "update",
                               str(a), str(o), str(b)], capture_output=True)
    out.write_bytes(done.stdout)
    if done.returncode == 0:
        return "merged"
    out.with_name(out.name + ".yours").write_bytes(y)
    out.with_name(out.name + ".update").write_bytes(u)
    return "conflict"


def _merge_manifest(yours: Path, then: Path | None, update: Path, base_now: catalog.Manifest,
                    base_digest: str) -> tuple[dict, list[str]]:
    y = tomllib.loads(yours.read_text())
    t = tomllib.loads(then.read_text()) if then is not None else {}
    u = tomllib.loads(update.read_text())
    out, conflicts = dict(u), []
    for key in sorted(set(y) | set(u) | set(t)):
        if key in MANAGED:
            continue
        yv, tv, uv = y.get(key), t.get(key), u.get(key)
        if yv == tv:
            continue  # yours left it as the base had it: the update's
        if uv == tv or uv == yv:
            if yv is None:
                out.pop(key, None)
            else:
                out[key] = yv  # only yours changed it
            continue
        # Both changed it differently: yours stays in the file, the update's is said beside it, never dropped.
        conflicts.append({"key": key, "yours": yv, "update": uv, "then": tv})
        if yv is None:
            out.pop(key, None)
        else:
            out[key] = yv
    out["id"], out["name"] = y["id"], y.get("name", u.get("name"))
    own = f"install:mine/{y['id']}"
    for need in ("needs_to_get", "needs_to_run"):
        theirs = [own if step in (f"install:{u.get('id')}", f"install:{base_now.key}") else step for step in u.get(need, [])]
        added = [step for step in y.get(need, []) if step not in t.get(need, []) and step not in theirs]
        if theirs or added:  # the update's steps, and any you added to yours (kept, never lost)
            out[need] = theirs + added
    v = str(y.get("version", "1"))
    out["version"] = str(int(v) + 1) if v.isdigit() else v + ".1"
    out["derived_from"] = {"id": y["derived_from"]["id"], "version": base_now.version, "sha256": base_digest}
    return out, conflicts


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def _target(m: catalog.Manifest, base_digest: str, yours_digest: str, environ: Mapping[str, str]) -> Path:
    return store.path(f"derived/{store.safe(m.key)}/{base_digest[:16]}-{yours_digest[:16]}", environ)


def candidate(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict | None:
    """Derive the update candidate for your version from its recorded base, or None when the base hasn't changed.
    Returns its merge report (also merge.json in its folder)."""
    base = stale(m, environ)
    if base is None:
        return None
    from ..contribute import _manifest_text

    now_folder = Path(catalog.entry_folder(base, environ))
    now_digest = digest(now_folder)
    then = base_then(m, environ)
    yours = Path(catalog.entry_folder(m, environ))
    target = _target(m, now_digest, digest(yours), environ)  # a later edit of yours is a new merge, never overwritten
    if (target / "merge.json").is_file():
        return store.read(target / "merge.json", {})
    target.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".deriving-", dir=target.parent))
    files: dict[str, str] = {}
    names = {p.name for p in yours.iterdir() if p.is_file() and _counted(p)} | \
        {p.name for p in now_folder.iterdir() if p.is_file() and _counted(p)}
    for name in sorted(names - {catalog.MANIFEST}):
        files[name] = _merge_text(yours / name, (then / name) if then else None, now_folder / name, work / name)
    fields, conflicts = _merge_manifest(yours / catalog.MANIFEST, (then / catalog.MANIFEST) if then else None,
                                        now_folder / catalog.MANIFEST, base, now_digest)
    text = _manifest_text(fields)
    if conflicts:  # each field both changed, said at the top of the file that holds yours, both sides beside it
        notes = [f"# Both your version and the update changed `{c['key']}`. Yours is below; the update's is "
                 f"{json.dumps(c['update'])}. Edit this file to choose, then use the merged version." for c in conflicts]
        text = "\n".join(notes) + "\n" + text
        shutil.copyfile(yours / catalog.MANIFEST, work / (catalog.MANIFEST + ".yours"))
        shutil.copyfile(now_folder / catalog.MANIFEST, work / (catalog.MANIFEST + ".update"))
    (work / catalog.MANIFEST).write_text(text)
    files[catalog.MANIFEST] = "conflict" if conflicts else "merged"
    conflicted = sorted(n for n, side in files.items() if side == "conflict")
    report = {"key": m.key, "at": store.now(), "folder": str(target),
              "base": {"id": m.derived_from["id"], "from": m.derived_from.get("version", ""), "to": base.version,
                       "fromDigest": m.derived_from.get("sha256", ""), "toDigest": now_digest,
                       "found": then is not None},
              "yoursDigest": digest(yours), "files": files, "fields": conflicts, "conflicts": conflicted,
              # A conflicted file is resolved only once the person changed it (or removed it): its hash as made.
              "conflictHashes": {n: _hash(work / n) for n in conflicted}}
    (work / "merge.json").write_text(json.dumps(report, indent=1) + "\n")
    try:
        work.rename(target)
    except OSError:  # another attempt made the same candidate first: keep it
        shutil.rmtree(work, ignore_errors=True)
    return store.read(target / "merge.json", {}) or report


def words(report: dict, left: list[str] | None = None) -> str:
    """What is left to do, in one line. `left`: the conflicted files not resolved yet (unresolved)."""
    base = report["base"]
    left = report.get("conflicts", []) if left is None else left
    if not base["found"] and left:  # once you resolved each, it merges like any other (#402 review round 2)
        return (f"Needs review: the base of yours changed ({base['from']} to {base['to']}), and the version you made "
                f"yours from isn't kept here, so every file you and it both changed is kept both ways: resolve "
                f"{', '.join(left)} in {report['folder']}")
    if left:
        fields = [c["key"] if isinstance(c, dict) else str(c) for c in report.get("fields", [])]
        said = f" (fields: {', '.join(fields)})" if fields and catalog.MANIFEST in left else ""
        return (f"Needs review: the update and your version both changed {', '.join(left)}{said}; both are kept, "
                f"marked where they differ, in {report['folder']}")
    return f"Ready to apply: your version on {base['id']} {base['to']}, merged" + \
        (" (its conflicts resolved by you)" if report.get("conflicts") else " without conflicts")


def unresolved(folder: Path, report: dict | None = None) -> list[str]:
    """Conflicted files of a candidate not resolved yet: still as made (their hash unchanged), or still holding a
    conflict marker."""
    report = report if report is not None else store.read(Path(folder) / "merge.json", {})
    out = []
    for name in report.get("conflicts", []):
        p = Path(folder) / name
        if _hash(p) == report.get("conflictHashes", {}).get(name):
            out.append(name)
        elif p.is_file() and any(line.startswith(MARKERS) for line in p.read_text(errors="replace").splitlines()):
            out.append(name)
    return out


def apply(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict:
    """Write the candidate made for your version as it is now into your folder, keeping your files as they were beside
    it. Raises ValueError with what is left when it can't: no candidate for your files as they are now (you edited them
    since: Check again), or conflicts not resolved yet."""
    base = stale(m, environ)
    if base is None:
        raise ValueError(f"{m.name} is already on the newest version of what it was made from")
    yours = Path(catalog.entry_folder(m, environ))
    folder = _target(m, digest(Path(catalog.entry_folder(base, environ))), digest(yours), environ)
    report = store.read(folder / "merge.json", {})
    if not report:
        raise ValueError(f"your version of {m.name} changed since its merge was prepared: Check for an update to "
                         "prepare it again")
    left = unresolved(folder, report)
    if left:  # only what is still unresolved is named (#402 review round 2)
        raise ValueError(f"resolve {', '.join(left)} in {folder} first: change each to what you want (its .yours and "
                         ".update are beside it)")
    kept = folder.parent / f"previous-{time.strftime('%Y%m%dT%H%M%S')}"
    kept.mkdir(parents=True)
    for p in yours.iterdir():
        if p.is_file() and _counted(p):
            shutil.copy2(p, kept / p.name)
    for name, side in report["files"].items():
        src, dest = folder / name, yours / name
        if dest.exists():
            dest.chmod(dest.stat().st_mode | 0o200)  # a copy of a channel's read-only file is yours to change
        if src.is_file():
            shutil.copyfile(src, dest)
        else:  # removed by the update with yours unchanged, or by you to keep it removed
            dest.unlink(missing_ok=True)
    row = {"at": store.now(), "key": m.key, "action": "merge", "base": report["base"], "kept": str(kept),
           "files": report["files"]}
    store.append(store.path("decisions.jsonl", environ), row)
    catalog.manifests.cache_clear()
    return row
