"""Labelled things from datasets, as [{"label", "box": [x0, y0, x1, y1]}] in the picture's pixels
(what bench/corpus/tour.py pans across). One reader per annotation format: Pascal VOC XML
(BCCD), YOLO text (many Roboflow and Hugging Face sets), COCO JSON."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path


def voc(xml_path: Path) -> list[dict]:
    """Pascal VOC: one XML per picture, <object><name/><bndbox>xmin ymin xmax ymax</bndbox>."""
    root = ET.parse(xml_path).getroot()
    out = []
    for obj in root.iter("object"):
        box = obj.find("bndbox")
        if box is None:
            continue
        vals = [float(box.findtext(k) or 0) for k in ("xmin", "ymin", "xmax", "ymax")]
        out.append({"label": (obj.findtext("name") or "").strip(), "box": vals})
    return out


def yolo(txt_path: Path, width: int, height: int, names: list[str] | dict | None = None) -> list[dict]:
    """YOLO: one line per thing, "class cx cy w h" on 0-1; `names` maps class numbers to labels."""
    out = []
    for line in Path(txt_path).read_text().splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        cls, cx, cy, w, h = int(float(parts[0])), *map(float, parts[1:5])
        label = (names[cls] if isinstance(names, list) and cls < len(names) else
                 names.get(cls, str(cls)) if isinstance(names, dict) else str(cls))
        out.append({"label": label, "box": [(cx - w / 2) * width, (cy - h / 2) * height,
                                            (cx + w / 2) * width, (cy + h / 2) * height]})
    return out


def coco(json_path: Path, image_name: str) -> list[dict]:
    """COCO: one JSON for many pictures; bbox is [x, y, w, h]."""
    data = json.loads(Path(json_path).read_text())
    image = next((i for i in data["images"] if Path(i["file_name"]).name == Path(image_name).name), None)
    if image is None:
        return []
    names = {c["id"]: c["name"] for c in data.get("categories", [])}
    return [{"label": names.get(a["category_id"], str(a["category_id"])),
             "box": [a["bbox"][0], a["bbox"][1], a["bbox"][0] + a["bbox"][2], a["bbox"][1] + a["bbox"][3]]}
            for a in data.get("annotations", []) if a["image_id"] == image["id"]]


def mosaic(pictures: list[tuple["object", list[dict]]], columns: int = 4, gap: int = 0):
    """Several pictures side by side as one big slide (a tour then crosses them), with their
    labelled things moved to match. Pictures are numpy BGR arrays."""
    import numpy as np

    rows = [pictures[i:i + columns] for i in range(0, len(pictures), columns)]
    row_h = [max(p.shape[0] for p, _ in row) for row in rows]
    width = max(sum(p.shape[1] for p, _ in row) + gap * (len(row) - 1) for row in rows)
    canvas = np.zeros((sum(row_h) + gap * (len(rows) - 1), width, 3), np.uint8)
    objects, y = [], 0
    for row, h in zip(rows, row_h):
        x = 0
        for picture, things in row:
            ph, pw = picture.shape[:2]
            canvas[y:y + ph, x:x + pw] = picture
            objects += [{**t, "box": [t["box"][0] + x, t["box"][1] + y, t["box"][2] + x, t["box"][3] + y]} for t in things]
            x += pw + gap
        y += h + gap
    return canvas, objects
