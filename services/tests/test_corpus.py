"""The test corpus (bench/corpus): eyepiece tours of pictures and videos, with the truth about
every frame, and the dataset readers behind them. Nothing is downloaded here."""
import json

import cv2
import numpy as np
import pytest

from bench.corpus import adapters
from bench.corpus.tour import Look, Tour, objects_in_view, tour_still, tour_video
from bench.corpus.truth import Truth

COLOURS = [(40, 40, 220), (40, 200, 40), (220, 60, 40), (30, 200, 220), (200, 40, 200), (230, 230, 30)]


def slide():
    """A grey slide with six coloured squares, each a labelled thing."""
    image = np.full((600, 900, 3), 128, np.uint8)
    things = []
    for i, colour in enumerate(COLOURS):
        x, y = 80 + (i % 3) * 300, 120 + (i // 3) * 300
        image[y:y + 60, x:x + 60] = colour
        things.append({"label": f"square {i}", "box": [x, y, x + 60, y + 60]})
    return image, things


def frames(path):
    cap = cv2.VideoCapture(str(path))  # a file on disk, never the phone (AGENTS.md)
    out = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        out.append(frame)
    cap.release()
    return out


def test_a_tour_of_a_still_shows_each_thing_where_the_truth_says(tmp_path):
    image, things = slide()
    look = Look(width=320, height=180, fps=10, noise=0.0, jpeg=95, vignette=0.0)
    out = tmp_path / "tour.mp4"
    truth = tour_still(image, things, out, specimen="squares", look=look, tour=Tour(seconds=6.0, seed=3))
    assert out.exists() and (tmp_path / "tour.truth.json").exists()
    video = frames(out)
    assert len(video) == len(truth["frames"]) == 60
    checked = 0
    for frame, info in zip(video, truth["frames"]):
        if info["speed"] > 0.5:
            continue  # blurred while the slide moves
        for thing in info["objects"]:
            x0, y0, x1, y1 = thing["box"]
            cx, cy = int((x0 + x1) / 2), int((y0 + y1) / 2)
            want = COLOURS[int(thing["label"].split()[-1])]
            got = frame[cy, cx].astype(int)
            assert np.abs(got - np.array(want)).max() < 40, (thing, got)
            checked += 1
    assert checked > 10
    loaded = Truth.load(out)
    assert loaded.specimen == "squares" and loaded.labels() and loaded.objects(0) == truth["frames"][0]["objects"]


def test_outside_the_eyepiece_is_dark_and_nothing_there_counts():
    look = Look(width=320, height=180)
    inside = objects_in_view([{"label": "a", "box": [95, 95, 105, 105]}], 100, 100, look)
    corner = objects_in_view([{"label": "b", "box": [-60, -60, -50, -50]}], 100, 100, look)
    assert [o["label"] for o in inside] == ["a"] and corner == []


def test_a_video_plays_inside_the_eyepiece(tmp_path):
    src = tmp_path / "src.mp4"
    writer = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"mp4v"), 20, (160, 120))
    for i in range(20):
        writer.write(np.full((120, 160, 3), (i * 10) % 255, np.uint8))
    writer.release()
    out = tmp_path / "in-eyepiece.mp4"
    truth = tour_video(src, out, specimen="grey", look=Look(width=320, height=180, fps=10, noise=0.0), seconds=0.8)
    assert truth["kind"] == "video" and len(truth["frames"]) == 8 and truth["frames"][-1]["source_frame"] == 14
    corner = frames(out)[0][2, 2]
    assert corner.max() < 20  # the dark surround of the eyepiece


def test_dataset_readers_and_the_mosaic(tmp_path):
    (tmp_path / "a.xml").write_text("<annotation><object><name>RBC</name><bndbox><xmin>10</xmin><ymin>20</ymin>"
                                    "<xmax>30</xmax><ymax>40</ymax></bndbox></object></annotation>")
    assert adapters.voc(tmp_path / "a.xml") == [{"label": "RBC", "box": [10.0, 20.0, 30.0, 40.0]}]
    (tmp_path / "a.txt").write_text("1 0.5 0.5 0.2 0.4\n")
    assert adapters.yolo(tmp_path / "a.txt", 100, 50, ["x", "rotifer"]) == [
        {"label": "rotifer", "box": [40.0, 15.0, 60.0, 35.0]}]
    (tmp_path / "c.json").write_text(json.dumps({"images": [{"id": 1, "file_name": "dir/p.jpg"}],
                                                 "categories": [{"id": 3, "name": "pollen"}],
                                                 "annotations": [{"image_id": 1, "category_id": 3, "bbox": [1, 2, 3, 4]}]}))
    assert adapters.coco(tmp_path / "c.json", "p.jpg") == [{"label": "pollen", "box": [1, 2, 4, 6]}]
    a, b = np.zeros((10, 20, 3), np.uint8), np.zeros((12, 8, 3), np.uint8)
    canvas, things = adapters.mosaic([(a, [{"label": "x", "box": [0, 0, 5, 5]}]), (b, [{"label": "y", "box": [1, 1, 2, 2]}])],
                                     columns=2)
    assert canvas.shape == (12, 28, 3) and things[1]["box"] == [21, 1, 22, 2]


def test_the_manifest_says_licence_and_how_to_fetch_each_item():
    from bench.corpus.fetch import HERE

    items = json.loads((HERE / "corpus.json").read_text())["items"]
    assert len({i["id"] for i in items}) == len(items) >= 25
    for item in items:
        assert item["fetch"]["kind"] in ("commons", "commons-search", "url", "zenodo", "hf", "github", "manual"), item["id"]
        assert item["fetch"]["kind"].startswith("commons") or item.get("licence"), item["id"]  # Commons says its own
    assert len({i["specimen"] for i in items}) >= 20
