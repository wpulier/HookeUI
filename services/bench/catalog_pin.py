"""Pin a catalog model's files from its Hugging Face repository (make catalog-pin ID= [ONLY="*.pt,*.json"]).

For a manifest whose source is `{ hf, revision }`, it lists the files of that exact revision from Hugging Face's own API
and writes them into the manifest's `files`: each file's path, bytes, and content (`sha256` for a large file, kept in
Git LFS; `git_sha1` for a small one, its git blob id). What a loader reads is what gets downloaded and checked, so a
contributor never types a hash. Documentation (README, LICENSE, .gitattributes) and pictures are left out; `ONLY` keeps
just the files that match its patterns (SAM 3.1 needs only its checkpoint).

Reading the listing needs no key for an open repository; a gated one may need HF_TOKEN (read from the environment).

When a built-in model (runner gpu-worker) pins Python files of its own (a model run with `trust_remote_code`), each is read
for what it imports, and every package the app's own Python would need is said with whether services/uv.lock provides it;
it exits 1 when one isn't (orchestrator/shared_python.py: Molmo 2's einops, 2026-10-19).
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
from pathlib import Path

import httpx

from gpu_worker import manifests

SKIP = ("README*", "LICENSE*", ".gitattributes", "*.png", "*.jpg", "*.jpeg", "*.gif", "assets/*")


def listing(repo: str, revision: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {os.environ['HF_TOKEN']}"} if os.environ.get("HF_TOKEN") else {}
    r = httpx.get(f"https://huggingface.co/api/models/{repo}/revision/{revision}", params={"blobs": "true"},
                  headers=headers, timeout=30)
    r.raise_for_status()
    return r.json().get("siblings") or []


def pinned(siblings: list[dict], only: list[str]) -> list[dict]:
    out = []
    for f in siblings:
        path = f["rfilename"]
        if only and not any(fnmatch.fnmatch(path, p) for p in only):
            continue
        if not only and any(fnmatch.fnmatch(path, p) for p in SKIP):
            continue
        lfs = f.get("lfs") or {}
        entry = {"path": path, "bytes": int(lfs.get("size") or f.get("size") or 0)}
        if lfs.get("sha256"):
            entry["sha256"] = lfs["sha256"]
        else:
            entry["git_sha1"] = f["blobId"]
        out.append(entry)
    return sorted(out, key=lambda e: e["path"])


def code_imports(repo: str, revision: str, files: list[dict]) -> set[str]:
    """The third-party modules a model's pinned Python files import, read from that revision (small files, by URL)."""
    from orchestrator import shared_python

    headers = {"Authorization": f"Bearer {os.environ['HF_TOKEN']}"} if os.environ.get("HF_TOKEN") else {}
    code = [f["path"] for f in files if f["path"].endswith(".py")]
    found: set[str] = set()
    for path in code:
        r = httpx.get(f"https://huggingface.co/{repo}/resolve/{revision}/{path}", headers=headers, timeout=30,
                      follow_redirects=True)
        r.raise_for_status()
        found |= shared_python.source_imports(r.text)
    return shared_python.third_party(found, own={Path(p).stem for p in code})


def toml_files(files: list[dict]) -> str:
    rows = []
    for f in files:
        content = f'sha256 = "{f["sha256"]}"' if "sha256" in f else f'git_sha1 = "{f["git_sha1"]}"'
        rows.append(f'  {{ path = "{f["path"]}", bytes = {f["bytes"]}, {content} }},')
    return "files = [\n" + "\n".join(rows) + "\n]\n"


def write(path: Path, files: list[dict]) -> None:
    text = path.read_text()
    block = re.compile(r"^files = \[\n(?:.*\n)*?\]\n", re.M)
    if block.search(text):
        text = block.sub(lambda _: toml_files(files), text, count=1)
    else:
        text = text.replace("needs_to_get = ", toml_files(files) + "needs_to_get = ", 1)
    path.write_text(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("id")
    parser.add_argument("--only", default="", help="comma-separated patterns: keep just these files")
    args = parser.parse_args()
    import tomllib

    path = manifests.ROOT / args.id / manifests.MANIFEST
    # Its source alone, read from the file: a new entry that needs a download can't read cleanly until its files are
    # pinned, which is what this writes.
    source = (tomllib.loads(path.read_text()).get("source") or {}) if path.is_file() else {}
    if not source.get("hf") or not source.get("revision"):
        raise SystemExit(f"{args.id}: needs a manifest whose source is a Hugging Face repository at a pinned revision")
    files = pinned(listing(source["hf"], source["revision"]), [p for p in args.only.split(",") if p])
    write(path, files)
    total = sum(f["bytes"] for f in files)
    print(f"{args.id}: {len(files)} files, {total / 1e9:.2f} GB, pinned at {source['revision'][:12]}")
    if tomllib.loads(path.read_text()).get("runner") != "gpu-worker" or not any(f["path"].endswith(".py") for f in files):
        return 0
    from orchestrator import shared_python

    needed = code_imports(source["hf"], source["revision"], files)
    unlocked = shared_python.unlocked(needed)
    print(f"{args.id}: its pinned code imports {', '.join(sorted(needed)) or 'nothing beyond Python and the app'}")
    for module, why in unlocked.items():
        print(f"  {module}: {why}: add it to services/pyproject.toml's gpu group and run `uv lock`")
    return 1 if unlocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
