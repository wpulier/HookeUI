"""Render the fetched corpus (the store's sources/corpus/) into eyepiece footage (footage/corpus/): each video
played inside the eyepiece, each set of labelled pictures toured as one slide, each plain picture
toured, every one with its truth beside it (bench/corpus/tour.py).

    python -m bench.corpus.build                # everything fetched that has no tour yet
    python -m bench.corpus.build --only bccd --force

Tours are named footage/corpus/corpus-<id>[-<n>].mp4 in the assets store, grouped by item in the studio's
camera menu.
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

import cv2

from . import adapters
from .fetch import FOOTAGE, HERE, RAW
from .tour import Look, Tour, tour_still, tour_video

VIDEO = (".webm", ".ogv", ".mp4", ".mov", ".mkv", ".avi")
PICTURE = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".svs")
VIDEO_SECONDS = 60.0


def unpack(folder: Path) -> Path:
    """A zipped download, unpacked once beside it."""
    import zipfile

    for z in folder.glob("*.zip"):
        target = folder / z.stem
        if not target.exists():
            with zipfile.ZipFile(z) as zf:
                zf.extractall(target)
    return folder


def read_picture(path: Path):
    if path.suffix.lower() in (".svs", ".tif", ".tiff"):
        import tifffile

        image = tifffile.imread(path, key=0)
        return cv2.cvtColor(image[..., :3], cv2.COLOR_RGB2BGR) if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    return cv2.imread(str(path), cv2.IMREAD_COLOR)


def annotations(spec: str, folder: Path, picture: Path, image) -> list[dict]:
    """The labelled things of one picture, from the item's tour spec ("voc:path/{stem}.xml")."""
    kind, _, pattern = spec.partition(":")
    hits = glob.glob(str(folder / "**" / pattern.format(stem=picture.stem)), recursive=True)
    if not hits:
        return []
    if kind == "voc":
        return adapters.voc(Path(hits[0]))
    if kind == "yolo":
        return adapters.yolo(Path(hits[0]), image.shape[1], image.shape[0])
    raise ValueError(f"unknown annotation kind {kind}")


def build(item: dict, force: bool = False) -> list[Path]:
    folder = RAW / item["id"]
    if not folder.exists() or item.get("build") == "none":
        return []  # not fetched yet, or used as it is (a focus sweep for the enhance benches)
    unpack(folder)
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.name != "SOURCE.json")
    source = json.loads((folder / "SOURCE.json").read_text()) if (folder / "SOURCE.json").exists() else {"id": item["id"]}
    specimen, made = item.get("specimen", ""), []

    def out_for(n: int | None) -> Path:
        return FOOTAGE / f"corpus-{item['id']}{'' if n is None else f'-{n}'}.mp4"

    spec = item.get("tour")
    if spec:  # labelled pictures: a few side by side as one slide, toured
        pictures = sorted(glob.glob(str(folder / "**" / spec["images"]), recursive=True))[: spec.get("take", 12)]
        out = out_for(None)
        if pictures and (force or not out.exists()):
            loaded = []
            for p in map(Path, pictures):
                image = read_picture(p)
                loaded.append((image, annotations(spec["annotations"], folder, p, image)))
            slide, objects = adapters.mosaic(loaded, columns=spec.get("columns", 4))
            tour_still(slide, objects, out, specimen=specimen, source=source,
                       look=Look(zoom=spec.get("zoom", 1.0)), tour=Tour(seconds=spec.get("seconds", 60.0)))
            made.append(out)
        return made
    videos = [p for p in files if p.suffix.lower() in VIDEO]
    pictures = [p for p in files if p.suffix.lower() in PICTURE]
    for n, path in enumerate(videos):
        out = out_for(n if len(videos) > 1 else None)
        if force or not out.exists():
            tour_video(path, out, specimen=specimen, source={**source, "file": path.name}, seconds=VIDEO_SECONDS)
            made.append(out)
    for n, path in enumerate(pictures):
        out = out_for(n if len(pictures) > 1 else None)
        if force or not out.exists():
            image = read_picture(path)
            if image is None:
                continue
            tour_still(image, [], out, specimen=specimen, source={**source, "file": path.name},
                       tour=Tour(seconds=30.0, seed=n))
            made.append(out)
    return made


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the fetched corpus into eyepiece footage.")
    parser.add_argument("--only", help="comma-separated item ids")
    parser.add_argument("--force", action="store_true", help="render again even if the footage exists")
    args = parser.parse_args()
    items = json.loads((HERE / "corpus.json").read_text())["items"]
    if args.only:
        items = [i for i in items if i["id"] in args.only.split(",")]
    FOOTAGE.mkdir(parents=True, exist_ok=True)
    for item in items:
        for out in build(item, args.force):
            print(f"{item['id']:<26} {out.relative_to(FOOTAGE.parent)}")


if __name__ == "__main__":
    main()
