#!/usr/bin/env python3
"""The Mac app's source snapshot: the files of one commit that the app runs, and nothing private.

It reads Git objects of the exact commit (never the working tree), so an uncommitted or ignored file can't ship.
What ships is decided in three steps, each recorded in the report written beside the tree:

1. RUNTIME: only what the running app reads (the services, the bridge, the catalog, the contracts, the bundled
   example). Docs, design history, planning, benches, tests and the old experiments stay out.
2. The public-candidate builder's rules (private planning backups, public-preview-<rev>/prepare.py): its named
   private files, generated results and its media rule (a picture or video ships only when it is listed in MEDIA).
3. Its runtime transforms: the asset manifest without the owner's recordings or backup destination, an empty
   verdict ledger, and no preview media that isn't shipped.

Then every shipped text file is scanned for secrets and private locators (SCAN); a finding not in ALLOWED stops it.

    python3 packaging/macos/snapshot.py --rev HEAD --out build/snapshot --identity apps/studio/dist/build.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[2]

RUNTIME = (
    "services/orchestrator/", "services/research_worker/", "services/gpu_worker/", "services/env_worker/",
    "services/fiji_worker/", "services/noncommercial/", "services/vendor/", "services/pyproject.toml",
    "services/uv.lock", "services/bench/__init__.py", "services/bench/corpus/",
    "experiments/iphone-camera-feed/scripts/", "experiments/iphone-camera-feed/capture-app/",
    "experiments/iphone-camera-feed/blackmagic-api.json",
    "scripts/studio.sh", "scripts/studio_launch.py", "scripts/studio_setup.py",
    "contract/", "catalog/", "assets/manifest.json", "assets/examples/", "examples/three-particles/",
    ".agents/skills/add-a-capability/", "docs/contributing/DCO.txt", "docs/licences.md",
)

# The builder's own exclusions that can fall inside RUNTIME (its `excluded()` and recorded dispositions).
PRIVATE = (
    "AGENTS.md", "RESTART.md", "HANDOFF-partner.md", "services/bench/results/", "services/sim/results/",
    "docs/superpowers/plans/", "docs/planning/", "docs/agents/", ".scratch/", ".playwright-cli/", "output/",
)

MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".pdf", ".tif", ".tiff"}
# Pictures that ship. The builder pinned the bundled specimen (CC0, David Root; assets/examples/NOTICE.md). The
# catalog's pinned test inputs are new since that builder's last run; without them their entries can't be read.
MEDIA = {
    "catalog/models/swin2sr-enlarge/example-default-grid.png": "generated geometric grid",
    "assets/examples/human_mitosis.tif": "the bundled CC0 specimen (builder pin)",
    "assets/examples/human_mitosis.png": "the bundled CC0 specimen (builder pin)",
    "catalog/models/basic-bright-objects/example-human-cells.png": "crop of the CC0 specimen; the example's test input",
    "catalog/models/spot-measure/example-blank.png": "synthetic test input",
    "catalog/models/spot-measure/example-three-spots.png": "synthetic test input",
    "catalog/models/spot-shape/example-blank.png": "synthetic test input",
    "catalog/models/spot-shape/example-round-and-long.png": "synthetic test input",
    "catalog/models/spot-shape/example-three-spots.png": "synthetic test input",
    "catalog/models/fiji-gaussian-blur/example-synthetic-texture.png":
        "generated texture for interface tests; not biological evidence",
    "catalog/models/fiji-subtract-background/example-synthetic-texture.png":
        "generated texture for interface tests; not biological evidence",
    "catalog/models/swin2sr-enlarge/example-synthetic-texture.png":
        "generated texture for interface tests; not biological evidence",
}

SCAN = {
    "home path": re.compile(r"/Users/(?!Shared/|you/|me/|name/|example/|<)[A-Za-z0-9._-]+/"),
    "owner email": re.compile(r"willpulier[0-9]*@"),
    "Google key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "OpenAI or Anthropic key": re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{24,}"),
    "Hugging Face token": re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghu|ghs)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"),
    "fal key": re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{32}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
# Findings reviewed and allowed: (path, kind, why). None so far.
ALLOWED: set[tuple[str, str, str]] = set()


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(REPO), *args])


def runtime(path: str) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in RUNTIME)


def private(path: str) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in PRIVATE)


def transform(path: str, data: bytes, shipped: set[str]) -> tuple[bytes, str | None]:
    """The builder's runtime transforms (its prepare.py and revisions.py R01/R08), applied to this commit's files."""
    if path == "assets/manifest.json":
        v = json.loads(data)
        v["real"], v["backup"], v["bench"] = [], {}, []
        v["about"] = ("Packaged app: no private recordings, specimen metadata or backup destination. "
                      "The bundled specimen is in assets/examples (CC0).")
        return (json.dumps(v, ensure_ascii=False, indent=1) + "\n").encode(), "owner's recordings and backup destination removed"
    if path == "contract/verdicts.jsonl":
        return b"", "the owner's verdict history removed (an empty ledger the app appends to)"
    if path == "contract/capabilities.json":
        v = json.loads(data)
        dropped = [c["id"] for c in v["capabilities"]
                   if c.get("media") and f"docs/capabilities/media/{c['media']}" not in shipped and c.pop("media")]
        return (json.dumps(v, ensure_ascii=False, indent=1) + "\n").encode(), \
            f"preview media not shipped, so not declared: {', '.join(dropped) or 'none'}"
    return data, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--identity", required=True, type=Path, help="the studio build's build.json (its identity)")
    ap.add_argument("--app-version", required=True)
    args = ap.parse_args()
    rev = git("rev-parse", "--verify", args.rev + "^{commit}").decode().strip()
    identity = json.loads(args.identity.read_text())
    if identity.get("revision") != rev or identity.get("dirty") is not False:
        raise SystemExit(f"the studio build is of {identity.get('revision')} (dirty: {identity.get('dirty')}), not a clean {rev}")
    if args.out.exists():
        raise SystemExit(f"{args.out} exists; the snapshot is always made into a new folder")

    rows = []
    for entry in git("ls-tree", "-r", "-z", "--full-tree", rev).split(b"\0"):
        if entry:
            meta, path = entry.split(b"\t", 1)
            mode, kind, oid = meta.decode().split()
            rows.append((path.decode(), mode, kind, oid))
    report = {"revision": rev, "tree": git("rev-parse", rev + "^{tree}").decode().strip(), "excluded": {},
              "media": {}, "transforms": {}, "scan": {"allowed": [], "findings": []}}
    chosen = []
    for path, mode, kind, oid in rows:
        why = None
        if not runtime(path):
            why = "not read by the running app"
        elif private(path):
            why = "private (public-candidate builder rule)"
        elif kind != "blob" or mode == "120000":
            why = f"not a plain file ({kind} {mode})"
        elif PurePosixPath(path).suffix.lower() in MEDIA_SUFFIXES and path not in MEDIA:
            why = "media not listed in MEDIA (builder's media rule)"
        if why:
            report["excluded"].setdefault(why, 0)
            report["excluded"][why] += 1
        else:
            chosen.append((path, mode, oid))
    shipped = {p for p, _, _ in chosen}
    missing = sorted(set(MEDIA) - shipped)
    if missing:
        raise SystemExit("MEDIA lists files this commit lacks: " + ", ".join(missing))

    batch = subprocess.Popen(["git", "-C", str(REPO), "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    files = []
    for path, mode, oid in chosen:
        batch.stdin.write(oid.encode() + b"\n")
        batch.stdin.flush()
        size = int(batch.stdout.readline().split()[2])
        data = batch.stdout.read(size)
        batch.stdout.read(1)
        data, note = transform(path, data, shipped)
        if note:
            report["transforms"][path] = note
        if path in MEDIA:
            report["media"][path] = MEDIA[path]
        dest = args.out / "tree" / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        dest.chmod(0o755 if mode == "100755" else 0o644)
        files.append({"path": path, "mode": mode, "sha256": hashlib.sha256(data).hexdigest()})
        try:
            text = data.decode()
        except UnicodeDecodeError:
            continue
        for kind, pattern in SCAN.items():
            for m in pattern.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                allowed = next((w for p, k, w in ALLOWED if p == path and k == kind), None)
                (report["scan"]["allowed"] if allowed else report["scan"]["findings"]).append(
                    {"path": path, "line": line, "kind": kind, **({"why": allowed} if allowed else {})})
    batch.stdin.close()
    batch.wait()

    content = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    packaged = {"app": "HookeUI", "version": args.app_version, "identity": identity, "contentHash": content,
                "note": "A packaged copy: the files of this commit the app runs (packaging/macos/snapshot.py)."}
    (args.out / "tree" / "packaged.json").write_text(json.dumps(packaged, indent=1) + "\n")
    report.update(files=len(files), contentHash=content)
    (args.out / "manifest.json").write_text(json.dumps(files, indent=1) + "\n")
    (args.out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in ("revision", "files", "contentHash", "excluded", "transforms")}, indent=1))
    if report["scan"]["findings"]:
        print("Private material or secrets found; nothing ships until each is removed or reviewed into ALLOWED:",
              file=sys.stderr)
        for f in report["scan"]["findings"]:
            print(f"  {f['path']}:{f['line']}  {f['kind']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
