#!/usr/bin/env python3
"""How big the app's own Python is, measured at build time and written into packaged.json as `runtimeDownload`, so the
launcher's first-launch question says real numbers (HookeUI/SetupWords.swift `consent`). Nothing here is typed by hand.

The build makes the Python exactly as the launcher's first launch does (main.swift `syncPython`: the bundled uv, the
hashed lockfile, SYNC's arguments), into a scratch folder with a Python of its own, then reads what it made:

- the packages: every installed distribution's wheel (its WHEEL tags) matched to its file in uv.lock, whose `size` is
  what a first launch downloads; a package built from source counts its sdist;
- Python: the archive uv downloads for the interpreter it installed (`uv python list --only-downloads`), sized by its
  server's Content-Length;
- the disk: the folders made (the environment and its Python), rounded to 100 MB so rebuilding one commit elsewhere
  writes the same packaged.json (installRuntime compares it byte for byte);
- where each comes from: the lockfile's registries and the Python archive's host.

packaged.json is outside the content hash (finalize_snapshot.py), so this changes no runtime identity.

    python3 runtime_download.py --uv UV --services TREE/services --packaged TREE/packaged.json --work DIR
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

try:
    import tomllib
except ModuleNotFoundError:  # Python before 3.11: the build's own services Python has it
    tomllib = None

# main.swift syncPython's arguments to uv, kept equal by services/tests/test_mac_runtime_download.py.
SYNC = ["sync", "--locked", "--python", "3.13", "--all-groups", "--inexact"]
# Names a person knows, said only when the package is really in the set (SetupWords: "such as PyTorch, OpenCV and SciPy").
NOTABLE = (("torch", "PyTorch"), ("opencv-python-headless", "OpenCV"), ("opencv-python", "OpenCV"), ("scipy", "SciPy"),
           ("numpy", "NumPy"))
DISK_STEP = 100_000_000


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_tags(filename: str) -> set[str]:
    """A wheel file name's tags, expanded ("py2.py3-none-any" is two): name-version[-build]-python-abi-platform.whl."""
    parts = filename[:-len(".whl")].split("-")
    python, abi, platform = parts[-3], parts[-2], parts[-1]
    return {f"{p}-{a}-{q}" for p in python.split(".") for a in abi.split(".") for q in platform.split(".")}


def installed(site: Path) -> list[dict]:
    """Each distribution in a site-packages folder: its name, version and the tags its WHEEL file records."""
    found = []
    for info in sorted(site.glob("*.dist-info")):
        meta = (info / "METADATA").read_text(errors="replace") if (info / "METADATA").is_file() else ""
        name = re.search(r"^Name: *(.+)$", meta, re.M)
        version = re.search(r"^Version: *(.+)$", meta, re.M)
        if not name or not version:
            continue
        wheel = (info / "WHEEL").read_text(errors="replace") if (info / "WHEEL").is_file() else ""
        found.append({"name": norm(name.group(1).strip()), "version": version.group(1).strip(),
                      "tags": set(re.findall(r"^Tag: *(\S+)$", wheel, re.M))})
    return found


def sizes(lock: dict, dists: list[dict]) -> dict:
    """What a first launch downloads for these distributions, from the lockfile's own file sizes."""
    entries = {(norm(p["name"]), p.get("version")): p for p in lock.get("package", [])}
    packages, registries, unmatched = [], set(), []
    for d in dists:
        entry = entries.get((d["name"], d["version"]))
        if entry is None:
            unmatched.append(f"{d['name']}=={d['version']}")
            continue
        registry = (entry.get("source") or {}).get("registry")
        if registry:
            registries.add(urlparse(registry).hostname or registry)
        chosen = next((w for w in entry.get("wheels", []) if d["tags"] and wheel_tags(w["url"].rsplit("/", 1)[-1]) == d["tags"]),
                      None)
        if chosen is None:
            chosen = next((w for w in entry.get("wheels", []) if d["tags"] & wheel_tags(w["url"].rsplit("/", 1)[-1])), None)
        file = chosen or entry.get("sdist")
        if not file or "size" not in file:
            unmatched.append(f"{d['name']}=={d['version']}")
            continue
        packages.append({"name": d["name"], "bytes": int(file["size"])})
    if unmatched:
        raise ValueError("installed packages with no sized file in uv.lock: " + ", ".join(unmatched))
    names = {p["name"] for p in packages}
    notable = []
    for name, said in NOTABLE:
        if name in names and said not in notable:
            notable.append(said)
    return {"packages": len(packages), "packageBytes": sum(p["bytes"] for p in packages),
            "registries": sorted(registries), "notable": notable}


def python_archive(uv: str, key: str, env: dict) -> dict:
    """The archive uv downloads for an installed interpreter (its key: cpython-3.13.5-macos-aarch64-none), and its size."""
    listed = json.loads(subprocess.check_output([uv, "python", "list", "--only-downloads", "--output-format", "json"],
                                                env=env, text=True))
    url = next((row.get("url") for row in listed if row.get("key") == key and row.get("url")), None)
    if not url:
        raise ValueError(f"uv lists no download for {key}")
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "hookeui-build"})
    with urllib.request.urlopen(request, timeout=60) as reply:
        length = reply.headers.get("Content-Length")
    if not length or not length.isdigit() or int(length) <= 0:
        raise ValueError(f"no size for {url}")
    parsed = urlparse(url)
    owner = "/".join(parsed.path.strip("/").split("/")[:2])  # github.com/<owner>/<repository>/releases/...
    return {"version": key.split("-")[1], "bytes": int(length), "host": parsed.hostname or "", "project": owner}


def rounded_disk(measured: int) -> int:
    """The disk measured, to the nearest 100 MB: the same for one commit built on any Mac."""
    return max(DISK_STEP, round(measured / DISK_STEP) * DISK_STEP)


def folder_bytes(*folders: Path) -> int:
    total = 0
    for folder in folders:
        out = subprocess.check_output(["du", "-sk", str(folder)], text=True)
        total += int(out.split()[0]) * 1024
    return total


def measure(uv: str, services: Path, work: Path) -> dict:
    if tomllib is None:
        raise SystemExit("runtime_download.py needs Python 3.11 or later (tomllib)")
    shutil.rmtree(work, ignore_errors=True)
    project = work / "project"
    project.mkdir(parents=True)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(services / name, project / name)
    env = {k: v for k, v in os.environ.items() if k in ("HOME", "USER", "TMPDIR", "PATH", "LANG", "UV_CACHE_DIR")}
    env.update(UV_NO_CONFIG="1", UV_PYTHON_PREFERENCE="only-managed", UV_PYTHON_DOWNLOADS="automatic",
               UV_PYTHON_INSTALL_DIR=str(work / "python"), UV_PROJECT_ENVIRONMENT=str(work / "env"))
    subprocess.run([uv, *SYNC], cwd=project, env=env, check=True)
    sites = list((work / "env").glob("lib/python*/site-packages"))
    if len(sites) != 1:
        raise ValueError(f"expected one site-packages in {work / 'env'}, found {len(sites)}")
    lock = tomllib.loads((project / "uv.lock").read_text())
    measured = sizes(lock, installed(sites[0]))
    keys = [p.name for p in (work / "python").iterdir() if p.is_dir() and p.name.startswith("cpython-")]
    if len(keys) != 1:
        raise ValueError(f"expected one Python in {work / 'python'}, found {keys}")
    python = python_archive(uv, keys[0], env)
    disk = folder_bytes(work / "env", work / "python")
    shutil.rmtree(work, ignore_errors=True)
    return {"bytes": measured["packageBytes"] + python["bytes"], "packages": measured["packages"],
            "packageBytes": measured["packageBytes"], "python": python,
            "diskBytes": rounded_disk(disk),
            "from": measured["registries"], "notable": measured["notable"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uv", required=True)
    parser.add_argument("--services", type=Path, required=True)
    parser.add_argument("--packaged", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args(argv)
    download = measure(args.uv, args.services, args.work)
    packaged = json.loads(args.packaged.read_text())
    packaged["runtimeDownload"] = download
    args.packaged.write_text(json.dumps(packaged, indent=1) + "\n")
    print(json.dumps(download, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
