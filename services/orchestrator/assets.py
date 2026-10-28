"""Test assets: one store outside every checkout, and two manifests read as one (assets/README.md).

The store (`MICROMIND_ASSETS`, default ~/MicroscopeMind) holds everything too big or too private
for git, shared by every checkout, worktree, bench and the camera bridge:

    manifest.json      the store's private manifest (untracked): its owner's real recordings, each with
                       its title, specimen, optics, checksum and backup. The repository's
                       assets/manifest.json is public and lists none; readers take both, the private
                       one over it (paths.layered_manifest), and add, adopt and set write only here
    footage/real/      real recordings from the iPhone on the microscope: one of a kind, so each is
                       in the private manifest with its checksum and backed up to the destination its
                       `backup` names (a private GitHub release)
    footage/corpus/    eyepiece tours rendered from the public corpus, each with a .truth.json
                       (rebuilt: make corpus-tours)
    footage/           anything else dropped here plays too (listed under "Other")
    sources/corpus/    the public corpus as downloaded (services/bench/corpus/corpus.json; make corpus FETCH=1)
    bench/<name>/      inputs benches make or fetch (bench/naming, bench/tracking, bench/frames...)

Everything under footage/ shows in the studio's camera menu (the bridge lists it). The manifests say
what each real recording is (a file in neither is listed under "Other", by its file name, with no
optics); the corpus manifest says what each public item is and its licence.

    python -m orchestrator.assets status [--verify]   what is here, missing, changed or not backed up
    python -m orchestrator.assets fetch [--corpus]    real recordings from the release (and the corpus)
    python -m orchestrator.assets add FILE --specimen "leaf epidermis" [--title ...] [--not-microscope]
    python -m orchestrator.assets adopt               recordings added in the studio into the manifest
    python -m orchestrator.assets set ID --objective 40 --eyepiece 10 [--field-number 18 --um-per-px .. --microscope ..]
    python -m orchestrator.assets backup              upload real recordings the release lacks
    python -m orchestrator.assets migrate --from CHECKOUT   move a checkout's footage/ and bench data in

`make assets`, `make assets-fetch`, `make assets-add FILE=... SPECIMEN=...` and `make assets-backup`
run these. GitHub commands use the gh CLI with the operator's authenticated account.
If it cannot see the asset repository, select an authorized account with `gh auth switch`
or configure GH_TOKEN outside the repository.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .paths import ASSETS_MANIFEST as MANIFEST
from .paths import CORPUS_MANIFEST, REPO, assets_root, layered_manifest, private_manifest
VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".mkv")


@dataclass(frozen=True)
class Store:
    root: Path

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> "Store":
        return cls(assets_root(environ))

    @property
    def footage(self) -> Path:
        return self.root / "footage"

    @property
    def real(self) -> Path:
        return self.footage / "real"

    @property
    def added(self) -> Path:
        """Recordings added from the studio's Assets view (each with a .json beside it) until adopted."""
        return self.footage / "added"

    @property
    def corpus_footage(self) -> Path:
        return self.footage / "corpus"

    @property
    def corpus_sources(self) -> Path:
        return self.root / "sources" / "corpus"

    def bench(self, name: str) -> Path:
        """A bench's own inputs (bench/naming, bench/tracking, bench/frames...)."""
        return self.root / "bench" / name

    def real_file(self, asset_id: str) -> Path:
        """A real recording by its manifest id, e.g. real_file("2026-06-14-leaf-epidermis-3m43s")."""
        for item in load_manifest()["real"]:
            if item["id"] == asset_id:
                return self.root / item["file"]
        raise KeyError(f"no real recording {asset_id!r} in {MANIFEST} or {private_manifest()}")


def store() -> Store:
    return Store.from_env()


# --- the manifest ------------------------------------------------------------------------------
def load_manifest(path: Path | None = None) -> dict:
    """`path` alone when given; else what every reader sees: the repository's manifest (`MANIFEST`, which the sim
    harness may replace with its --asset-manifest) with the store's private one read over it."""
    if path is not None:
        return json.loads(path.read_text())
    return layered_manifest(json.loads(MANIFEST.read_text()), load_private())


def load_private() -> dict:
    """The store's private manifest (paths.private_manifest), or an empty one: what add, adopt and set change."""
    path = private_manifest()
    try:
        manifest = json.loads(path.read_text())
    except FileNotFoundError:
        return {"real": []}
    if not isinstance(manifest, dict) or not isinstance(manifest.setdefault("real", []), list):
        raise ValueError(f"{path} is not an assets manifest (an object with a \"real\" list)")
    return manifest


def save_manifest(manifest: dict, path: Path | None = None) -> None:
    """Written to `path`, else to the store's private manifest: never into the repository's."""
    manifest["real"].sort(key=lambda r: r["id"])
    target = path or private_manifest()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def seconds(path: Path) -> float:
    """A video's length (ffprobe), 0 when it cannot be read."""
    result = subprocess.run([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=False)
    try:
        return round(float(result.stdout.strip()), 1)
    except ValueError:
        return 0.0


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def span(secs: float) -> str:
    """'23s', '3m43s', '37m57s'."""
    whole = int(round(secs))
    return f"{whole // 60}m{whole % 60:02d}s" if whole >= 60 else f"{whole}s"


# --- status ----------------------------------------------------------------------------------------
def real_status(s: Store, item: dict, verify: bool = False) -> str:
    path = s.root / item["file"]
    if not path.is_file():
        return "missing"
    if path.stat().st_size != item["bytes"]:
        return "changed"
    if verify and sha256(path) != item["sha256"]:
        return "changed"
    return "ok"


def corpus_status(s: Store) -> list[dict]:
    """Per public corpus item: whether it is downloaded and how many tours are rendered."""
    items = json.loads(CORPUS_MANIFEST.read_text())["items"]
    out = []
    for item in items:
        raw = s.corpus_sources / item["id"]
        fetched = raw.is_dir() and any(raw.iterdir())
        own = re.compile(rf"corpus-{re.escape(item['id'])}(-\d+)?\.mp4$")  # not another id that starts the same
        tours = sum(1 for p in s.corpus_footage.glob(f"corpus-{item['id']}*.mp4") if own.match(p.name))
        out.append({"id": item["id"], "specimen": item.get("specimen", ""), "fetched": fetched, "tours": tours,
                    "manual": (item.get("fetch") or {}).get("kind") == "manual"})
    return out


def released(manifest: dict) -> set[str] | None:
    """Names on the configured release, or None when absent or GitHub could not be asked."""
    b = manifest.get("backup")
    if not b:
        return None
    result = subprocess.run(["gh", "release", "view", b["tag"], "--repo", b["repo"], "--json", "assets",
                             "--jq", ".assets[].name"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        if "release not found" in (result.stderr or "").lower():
            return set()
        return None
    return set(result.stdout.split())


def cmd_status(args) -> int:
    s, manifest = store(), load_manifest()
    print(f"Assets store: {s.root}{'' if s.root.is_dir() else '  (does not exist yet)'}")
    backup = manifest.get("backup")
    remote = released(manifest) if args.remote else None
    print("\nReal recordings (one of a kind):")
    if backup:
        print(f"  Backup destination: {backup['repo']}@{backup['tag']}"
              + (" (could not check with gh)" if args.remote and remote is None else
                 " (not checked; use --remote)" if not args.remote else ""))
    else:
        print("  No backup destination configured; recordings are local only.")
    if not manifest["real"]:
        print("  No real recordings listed. Add your own with make assets-add or make assets-adopt.")
    bad = 0
    for item in manifest["real"]:
        state = real_status(s, item, verify=args.verify)
        backed = "  NO BACKUP CONFIGURED" if not backup else (
            "" if remote is None else ("  backed up" if Path(item["file"]).name in remote else "  NOT BACKED UP"))
        bad += state != "ok" or not backup or backed == "  NOT BACKED UP"
        print(f"  {state:<8} {item['id']:<46} {item['title']}{backed}")
    corpus = corpus_status(s)
    fetched, tours = sum(c["fetched"] for c in corpus), sum(c["tours"] for c in corpus)
    print(f"\nPublic corpus ({CORPUS_MANIFEST.relative_to(REPO)}): {fetched}/{len(corpus)} items downloaded, "
          f"{tours} tours rendered")
    for c in corpus:
        if not c["fetched"] or not c["tours"]:
            print(f"  {'not fetched' if not c['fetched'] else 'no tours':<12} {c['id']:<26} {c['specimen']}"
                  + ("  (manual download: see its 'how')" if c["manual"] and not c["fetched"] else ""))
    print("\nBench inputs:")
    for item in manifest.get("bench", []):
        path = s.root / item["path"]
        print(f"  {'ok' if path.exists() else 'missing':<8} {item['path']:<40} {item['what']}")
    added = sorted(s.added.glob("*.json")) if s.added.is_dir() else []
    if added:
        print(f"\nAdded in the studio, not in the manifest or backed up yet (make assets-adopt):")
        for side in added:
            meta = json.loads(side.read_text())
            print(f"  {side.name[:-5]:<46} {meta.get('title', '')}")
        bad += len(added)
    other = [p for p in s.footage.glob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES] if s.footage.is_dir() else []
    if other:
        print(f"\nOther footage in {s.footage} (plays, but is in no manifest): " + ", ".join(p.name for p in other))
    return 1 if bad else 0


# --- fetch, add, backup --------------------------------------------------------------------------
def cmd_fetch(args) -> int:
    s, manifest = store(), load_manifest()
    s.real.mkdir(parents=True, exist_ok=True)
    b, failed = manifest.get("backup"), 0
    if not manifest["real"]:
        print("No real recordings listed; nothing to fetch.")
    pending = [item for item in manifest["real"] if real_status(s, item) != "ok"]
    if manifest["real"] and not pending:
        print("All listed recordings are present.")
    if pending and not b:
        print("No backup destination configured. Restore these recordings locally, or configure "
              f"a backup in {private_manifest()} before fetching:")
        for item in pending:
            print(f"  {real_status(s, item):<8} {item['id']}")
        failed = len(pending)
    for item in pending if b else []:
        name, dest = Path(item["file"]).name, s.root / item["file"]
        print(f"downloading {name} from {b['repo']}@{b['tag']}")
        result = subprocess.run(["gh", "release", "download", b["tag"], "--repo", b["repo"], "--pattern", name,
                                 "--dir", str(dest.parent), "--clobber"], check=False)
        if result.returncode != 0 or not dest.is_file() or sha256(dest) != item["sha256"]:
            print(f"  FAILED: {name} (gh exit {result.returncode}; checksum {'ok' if dest.is_file() and sha256(dest) == item['sha256'] else 'wrong or missing'})")
            failed += 1
    if args.corpus:
        env = {**os.environ, "MICROMIND_ASSETS": str(s.root)}
        for module in (["bench.corpus.fetch", "--yes"], ["bench.corpus.build"]):
            subprocess.run([sys.executable, "-m", *module], cwd=REPO / "services", env=env, check=False)
    return 1 if failed else 0


OPTICS_FIELDS = ("camera", "microscope", "objective", "eyepiece", "fieldNumber", "umPerPx", "notes")


def optics_from(args, base: dict | None = None) -> dict:
    """An optics record (manifest `optics`) from command-line values over `base`; unknown stays null."""
    out = {k: None for k in OPTICS_FIELDS} | {"notes": ""} | dict(base or {})
    given = {"camera": "camera", "microscope": "microscope", "objective": "objective", "eyepiece": "eyepiece",
             "fieldNumber": "field_number", "umPerPx": "um_per_px", "notes": "optics_notes"}
    for field, value in ((field, getattr(args, arg, None)) for field, arg in given.items()):
        if value not in (None, ""):
            out[field] = value
    return out


def backup_next_step(manifest: dict) -> str:
    if not manifest.get("backup"):
        return ("No backup destination configured; recordings are local only. Back them up separately, "
                f"or configure a backup in {private_manifest()} before running make assets-backup.")
    return f"Listed in {private_manifest()} (never committed). Next: back it up: make assets-backup"


def cmd_add(args) -> int:
    s, manifest, private = store(), load_manifest(), load_private()
    source = Path(args.file).expanduser()
    if not source.is_absolute() and not source.exists():
        source = REPO / source  # make runs this from services/; a path from the repo root works too
    source = source.resolve()
    if not source.is_file():
        print(f"no file {source}")
        return 1
    length = seconds(source)
    if length <= 0:
        print(f"{source} does not play (ffprobe found no duration): not added")
        return 1
    date = args.date or dt.date.fromtimestamp(source.stat().st_mtime).isoformat()
    asset_id = f"{date}-{slug(args.specimen)}-{span(length)}"
    if any(r["id"] == asset_id for r in manifest["real"]):
        print(f"{asset_id} is already in the manifest")
        return 1
    dest = s.real / f"{asset_id}{source.suffix.lower()}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    private["real"].append({
        "id": asset_id, "file": str(dest.relative_to(s.root)),
        "title": args.title or f"{args.specimen[:1].upper()}{args.specimen[1:]}, {date} ({span(length)})",
        "specimen": args.specimen, "date": date, "seconds": length, "bytes": dest.stat().st_size,
        "sha256": sha256(dest), "microscope": not args.not_microscope, "origin": str(source), "notes": args.notes or "",
        "optics": optics_from(args),
    })
    save_manifest(private)
    print(f"added {asset_id} -> {dest}\n{backup_next_step(manifest)}")
    return 0


def cmd_adopt(args) -> int:
    """Recordings added from the studio (footage/added/, a .json beside each) into footage/real/ and the
    store's private manifest, with the title, specimen and checksum the studio recorded (checked again here)."""
    s, manifest, private = store(), load_manifest(), load_private()
    sides = sorted(s.added.glob("*.json")) if s.added.is_dir() else []
    if not sides:
        print("nothing added in the studio waits to be adopted")
        return 0
    adopted = 0
    for side in sides:
        meta = json.loads(side.read_text())
        video = side.with_suffix("")  # x.mp4.json -> x.mp4
        if not video.is_file() or sha256(video) != meta.get("sha256"):
            print(f"  skipped {video.name}: missing, or changed since it was added")
            continue
        asset_id = video.stem
        if any(r["id"] == asset_id for r in manifest["real"]):
            print(f"  skipped {asset_id}: already in the manifest")
            continue
        dest = s.real / video.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(video), str(dest))
        private["real"].append({
            "id": asset_id, "file": str(dest.relative_to(s.root)), "title": meta.get("title") or asset_id,
            "specimen": meta.get("specimen", ""), "date": meta.get("date", ""), "seconds": meta.get("seconds", 0),
            "bytes": dest.stat().st_size, "sha256": meta["sha256"], "microscope": bool(meta.get("microscope", True)),
            "origin": f"added in the studio as {meta.get('originalName', video.name)}", "notes": meta.get("notes", ""),
            "optics": {k: None for k in OPTICS_FIELDS} | {"notes": ""} | (meta.get("optics") or {}),
        })
        side.unlink()  # its facts are in the manifest now
        adopted += 1
        print(f"adopted {asset_id}")
    save_manifest(private)
    if adopted:
        print(backup_next_step(manifest))
    return 0


def cmd_set(args) -> int:
    """What a real recording was filmed with (its optics), filled in or corrected, in the store's private manifest
    (one the repository's lists is copied there with its new optics, which then read over it)."""
    item = next((r for r in load_manifest()["real"] if r["id"] == args.id), None)
    if item is None:
        print(f"no real recording {args.id!r} (make assets lists them)")
        return 1
    private = load_private()
    own = next((r for r in private["real"] if r.get("id") == args.id), None)
    if own is None:
        own = json.loads(json.dumps(item))
        private["real"].append(own)
    own["optics"] = optics_from(args, item.get("optics"))
    save_manifest(private)
    print(f"{args.id}: " + ", ".join(f"{k} {v}" for k, v in own["optics"].items() if v not in (None, "")))
    return 0


def cmd_backup(args) -> int:
    s, manifest = store(), load_manifest()
    if not manifest["real"]:
        print("No real recordings listed; nothing to back up.")
        return 0
    b = manifest.get("backup")
    if not b:
        print(backup_next_step(manifest))
        return 1
    remote = released(manifest)
    if remote is None:
        print(f"could not reach {b['repo']} with gh: is GH_TOKEN set for an account that can see it?")
        return 1
    if not remote and subprocess.run(["gh", "release", "view", b["tag"], "--repo", b["repo"]], capture_output=True,
                                     check=False).returncode != 0:
        subprocess.run(["gh", "release", "create", b["tag"], "--repo", b["repo"], "--title", "Test assets",
                        "--notes", "Real microscope recordings for tests (assets/manifest.json lists them with "
                        "checksums). Not a software release.", "--prerelease"], check=True)
    failed = 0
    for item in manifest["real"]:
        name, path = Path(item["file"]).name, s.root / item["file"]
        if name in remote:
            continue
        if real_status(s, item, verify=True) != "ok":
            print(f"  skipped {name}: the local copy is missing or does not match the manifest")
            failed += 1
            continue
        print(f"uploading {name} ({item['bytes'] / 1e6:.0f} MB)")
        failed += subprocess.run(["gh", "release", "upload", b["tag"], str(path), "--repo", b["repo"]],
                                 check=False).returncode != 0
    # Checked again from GitHub's side: an upload that said it worked but is not listed failed.
    after = released(manifest) or set()
    missing = [r["id"] for r in manifest["real"] if Path(r["file"]).name not in after]
    for asset_id in missing:
        print(f"  NOT on the release after uploading: {asset_id} (run make assets-backup again)")
    return 1 if failed or missing else 0


# --- migrate a checkout's old layout into the store -------------------------------------------------
def cmd_migrate(args) -> int:
    """Moves (never copies, never deletes) a checkout's footage/ and bench data into the store:
    corpus tours to footage/corpus/, footage/corpus/raw/<id> to sources/corpus/<id>, other videos to
    footage/, services/bench/data/<name> and services/bench/frames to bench/. Symlinks are left
    (real recordings come in with `add`, which records them). Anything already in the store stays."""
    s, checkout = store(), Path(args.source).expanduser().resolve()
    moves: list[tuple[Path, Path]] = []
    footage = checkout / "footage"
    if footage.is_dir():
        for p in sorted(footage.iterdir()):
            if p.is_symlink() or p.name == "README.md":
                continue
            if p.name.startswith("corpus-") and (p.suffix in VIDEO_SUFFIXES or p.name.endswith(".truth.json")):
                moves.append((p, s.corpus_footage / p.name))
            elif p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES:
                moves.append((p, s.footage / p.name))
        raw = footage / "corpus" / "raw"
        if raw.is_dir():
            moves += [(p, s.corpus_sources / p.name) for p in sorted(raw.iterdir())]
    bench = checkout / "services" / "bench"
    if (bench / "data").is_dir():
        moves += [(p, s.bench(p.name)) for p in sorted((bench / "data").iterdir()) if not p.is_symlink()]
    if (bench / "frames").is_dir() and not (bench / "frames").is_symlink():
        moves.append((bench / "frames", s.bench("frames")))
    done = kept = 0
    for src, dest in moves:
        if dest.exists():
            kept += 1
            continue
        if args.dry_run:
            print(f"would move {src} -> {dest}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        done += 1
    print(f"{'would move' if args.dry_run else 'moved'} {len(moves) - kept if args.dry_run else done} item(s) into {s.root}; "
          f"{kept} already in the store (left where they were)")
    return 0


def add_optics_arguments(p) -> None:
    p.add_argument("--camera")
    p.add_argument("--microscope")
    p.add_argument("--objective", type=float, help="the objective's magnification, e.g. 40")
    p.add_argument("--eyepiece", type=float, help="the eyepiece's magnification, e.g. 10")
    p.add_argument("--field-number", type=float, help="the eyepiece's field number in mm, e.g. 18")
    p.add_argument("--um-per-px", type=float, help="micrometres per camera pixel, if measured")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m orchestrator.assets", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status", help="what is here, missing, changed or not backed up")
    p.add_argument("--verify", action="store_true", help="also check every real recording's checksum")
    p.add_argument("--remote", action="store_true", help="also ask GitHub which recordings are backed up")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("fetch", help="real recordings from the release (and with --corpus the public corpus)")
    p.add_argument("--corpus", action="store_true")
    p.set_defaults(fn=cmd_fetch)
    p = sub.add_parser("add", help="add a real recording to the store and the manifest")
    p.add_argument("file")
    p.add_argument("--specimen", required=True)
    p.add_argument("--title")
    p.add_argument("--date", help="YYYY-MM-DD (default: the file's date)")
    p.add_argument("--notes")
    p.add_argument("--not-microscope", action="store_true", help="the camera was not on the microscope")
    add_optics_arguments(p)
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("set", help="what a real recording was filmed with (objective, eyepiece, scale...)")
    p.add_argument("id")
    add_optics_arguments(p)
    p.add_argument("--optics-notes")
    p.set_defaults(fn=cmd_set)
    sub.add_parser("adopt", help="recordings added in the studio into footage/real/ and the manifest").set_defaults(fn=cmd_adopt)
    sub.add_parser("backup", help="upload real recordings the GitHub release lacks").set_defaults(fn=cmd_backup)
    p = sub.add_parser("migrate", help="move a checkout's footage/ and bench data into the store")
    p.add_argument("--from", dest="source", default=str(REPO))
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_migrate)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
