"""Fetch the test corpus (corpus.json) into the assets store's sources/corpus/ (assets/README.md).

    python -m bench.corpus.fetch                 # list: what each item is, its licence and size (no download)
    python -m bench.corpus.fetch --yes           # download what is listed (skips what is already there)
    python -m bench.corpus.fetch --only bccd,vorticella --yes
    python -m bench.corpus.fetch --internal      # include non-commercial sets (internal benches only)

Listing only reads metadata from the sources (Wikimedia Commons, Zenodo, Hugging Face, GitHub).
Commons files say their own licence and author, which are recorded next to each download
(`<id>/SOURCE.json`), so attribution travels with the file. Nothing larger than --max-mb per file
is downloaded unless the item allows it.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]  # the repo
# The assets store (orchestrator/assets.py, MICROMIND_ASSETS): downloads in sources/corpus/, rendered
# tours in footage/corpus/, shared by every checkout and worktree.
sys.path.insert(0, str(HERE.parents[1]))
from orchestrator.assets import store  # noqa: E402

FOOTAGE = store().corpus_footage
RAW = store().corpus_sources
AGENT = "MicroscopeMind-test-corpus/1.0 (research test footage)"
COMMONS = "https://commons.wikimedia.org/w/api.php"


@dataclass
class File:
    name: str
    url: str
    size: int | None  # bytes, when the source says
    licence: str = ""
    author: str = ""
    page: str = ""


@dataclass
class Item:
    id: str
    spec: dict
    files: list[File] = field(default_factory=list)
    error: str = ""

    @property
    def nc(self) -> bool:
        return bool(self.spec.get("nc"))


def get_json(url: str, params: dict | None = None, tries: int = 5) -> dict:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == tries - 1:
                raise
            # Rate limited (Wikimedia asks for it): wait as long as it says, or back off.
            time.sleep(float(exc.headers.get("Retry-After") or 2 ** (attempt + 1)))
    raise RuntimeError("unreachable")


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def commons_files(titles: list[str]) -> list[File]:
    data = get_json(COMMONS, {"action": "query", "format": "json", "prop": "imageinfo", "titles": "|".join(titles),
                              "iiprop": "url|size|extmetadata"})
    out = []
    for page in data.get("query", {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [None])[0]
        if info is None:
            continue
        meta = info.get("extmetadata", {})
        out.append(File(page["title"].removeprefix("File:"), info["url"], info.get("size"),
                        strip_html(meta.get("LicenseShortName", {}).get("value", "")),
                        strip_html(meta.get("Artist", {}).get("value", ""))[:120], info.get("descriptionurl", "")))
    return out


def resolve(item: Item) -> Item:
    """What the item's source has, without downloading it."""
    f = item.spec["fetch"]
    kind = f["kind"]
    try:
        if kind == "commons":
            item.files = commons_files([f["title"]])
            if not item.files:
                item.error = f"not found on Commons: {f['title']}"
        elif kind == "commons-search":
            data = get_json(COMMONS, {"action": "query", "format": "json", "list": "search", "srnamespace": 6,
                                      "srsearch": f["search"], "srlimit": f.get("take", 10)})
            titles = [r["title"] for r in data.get("query", {}).get("search", [])]
            item.files = commons_files(titles) if titles else []
        elif kind == "url":
            name = f["url"].rsplit("/", 1)[-1]
            req = urllib.request.Request(f["url"], method="HEAD", headers={"User-Agent": AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                size = int(resp.headers.get("Content-Length") or 0) or None
            item.files = [File(name, f["url"], size, item.spec.get("licence", ""))]
        elif kind == "zenodo":
            data = get_json(f"https://zenodo.org/api/records/{f['record']}")
            files = [x for x in data.get("files", []) if f.get("match", "") in x["key"]][: f.get("take", 1)]
            item.files = [File(x["key"], x["links"]["self"], x.get("size"), item.spec.get("licence", "")) for x in files]
        elif kind == "hf":
            tree = []
            for path in f.get("paths") or [f.get("path", "")]:
                tree += get_json(f"https://huggingface.co/api/datasets/{f['repo']}/tree/main/{path}".rstrip("/"))
            files = [x for x in tree if x.get("type") == "file" and f.get("match", "") in x["path"]][: f.get("take", 10)]
            item.files = [File(x["path"], f"https://huggingface.co/datasets/{f['repo']}/resolve/main/{x['path']}",
                               x.get("size"), item.spec.get("licence", "")) for x in files]
        elif kind == "github":
            repo = get_json(f"https://api.github.com/repos/{f['repo']}")
            url = f"https://codeload.github.com/{f['repo']}/zip/refs/heads/{f.get('branch', repo.get('default_branch', 'main'))}"
            item.files = [File(f"{f['repo'].split('/')[1]}.zip", url, repo.get("size", 0) * 1024,
                               (repo.get("license") or {}).get("spdx_id", item.spec.get("licence", "")))]
        elif kind == "manual":
            item.error = "manual: " + f["how"]
        else:
            item.error = f"unknown fetch kind {kind}"
    except Exception as exc:  # a source that is down or moved: say so and carry on
        item.error = f"{type(exc).__name__}: {exc}"[:200]
    return item


PAUSE_S = 1.5  # between files


def fetch_file(url: str, target: Path, tries: int = 6) -> None:
    """Download to `<target>.part` and rename when complete, waiting and retrying when the
    source says too many requests."""
    part = target.with_name(target.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp, open(part, "wb") as out:
                shutil.copyfileobj(resp, out)
            part.rename(target)
            return
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == tries - 1:
                raise
            time.sleep(float(exc.headers.get("Retry-After") or 5 * 2 ** attempt))


def download(item: Item, max_mb: float) -> list[str]:
    folder = RAW / item.id
    folder.mkdir(parents=True, exist_ok=True)
    done = []
    limit = item.spec["fetch"].get("max_mb", max_mb) * 1e6
    for f in item.files:
        target = folder / Path(f.name).name
        if target.exists() and (f.size is None or target.stat().st_size == f.size):
            done.append(f"{target.name} (already here)")
            continue
        if f.size and f.size > limit:
            done.append(f"{target.name} skipped: {f.size / 1e6:.0f} MB is over {limit / 1e6:.0f} MB")
            continue
        try:
            fetch_file(f.url, target)
            done.append(target.name)
        except Exception as exc:  # one file that will not come: say so and carry on
            done.append(f"{target.name} failed: {type(exc).__name__}: {exc}"[:160])
        time.sleep(PAUSE_S)  # be gentle with the sources (Wikimedia rate-limits bursts)
    (folder / "SOURCE.json").write_text(json.dumps({
        "id": item.id, "specimen": item.spec.get("specimen"), "licence": item.spec.get("licence"),
        "files": [f.__dict__ for f in item.files]}, indent=1, ensure_ascii=False))
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch the test corpus (bench/corpus/corpus.json).")
    parser.add_argument("--yes", action="store_true", help="download (otherwise only list)")
    parser.add_argument("--only", help="comma-separated item ids")
    parser.add_argument("--internal", action="store_true", help="include non-commercial sets (internal benches only)")
    parser.add_argument("--max-mb", type=float, default=300.0, help="largest file to download, unless the item allows more")
    args = parser.parse_args()
    items = [Item(s["id"], s) for s in json.loads((HERE / "corpus.json").read_text())["items"]]
    if args.only:
        wanted = set(args.only.split(","))
        items = [i for i in items if i.id in wanted]
    total = 0.0
    for item in items:
        if item.nc and not args.internal:
            print(f"{item.id:<26} skipped (non-commercial; --internal to include)")
            continue
        resolve(item)
        size = sum(f.size or 0 for f in item.files) / 1e6
        total += size
        lic = ", ".join(sorted({f.licence for f in item.files if f.licence})) or item.spec.get("licence", "?")
        print(f"{item.id:<26} {len(item.files):>3} file(s) {size:8.1f} MB  {lic[:40]:<40} {item.error}")
        if args.yes and item.files:
            for line in download(item, args.max_mb):
                print(f"{'':<28}{line}")
    print(f"\n{total:.0f} MB listed{'' if args.yes else ' (nothing downloaded; --yes to fetch)'}", file=sys.stderr)


if __name__ == "__main__":
    main()
