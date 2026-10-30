"""Made today's thumbnails (orchestrator/thumbnails.py; GET /api/resources/{id}/thumbnail): every product kind shows a
small picture made from what it keeps, made off the loop at background priority and kept in memory; a resource that is
no product, or has no picture, gets none (the card shows its kind's icon). The user's own pictures and recordings are
sources (ADR 0022): their small picture is the library's (GET /api/library/{id}/thumbnail)."""
import asyncio
import io
import json
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image

from orchestrator.paths import Paths
from orchestrator.resources import Entry
from orchestrator.thumbnails import LIBRARY, THUMB_PX, Thumbnails, background_priority, poster, shrink
from tests.waiting import HANG_S

DAY = "2026-10-11"


def jpeg(w=640, h=360, value=200):
    image = np.full((h, w, 3), value, np.uint8)
    cv2.circle(image, (w // 2, h // 2), h // 2 - 4, (60, 160, 60), -1)  # an eyepiece of green
    return cv2.imencode(".jpg", image)[1].tobytes()


def png(w=300, h=200):
    out = io.BytesIO()
    Image.new("RGB", (w, h), (30, 90, 200)).save(out, "PNG")
    return out.getvalue()


def size_of(data: bytes) -> tuple[int, int]:
    image = Image.open(io.BytesIO(data))
    assert image.format == "JPEG"
    return image.size


class FakeIndex:
    """The parts of resources.Index the thumbnails read: one entry per id and its pictures."""

    def __init__(self, entries=(), pictures=None, **stores):
        self.entries = {e.id: e for e in entries}
        self.pictures = pictures or {}
        self.hub = stores.pop("hub", SimpleNamespace())
        self.research = stores.pop("research", None)
        self.identity = stores.pop("identity", None)
        self.datasets = stores.pop("datasets", None)
        self.recordings = stores.pop("recordings", None)
        self.asked: list[str] = []

    def today(self):
        return DAY

    def session(self):  # the session open now (a day's own session here)
        return DAY

    def is_open(self, sid):
        return not sid or sid == DAY

    def _researchers(self, sid):
        return []

    def get(self, eid, day=None):
        self.asked.append(eid)
        return self.entries.get(eid)

    def picture(self, eid, n=0, day=None, max_bytes=None):
        data = self.pictures.get((eid, n))
        return (data, "image/jpeg") if data else None


def entry(eid, kind, **fields):
    return Entry(eid, kind, f"{kind} {eid}", DAY, 1.0, **fields)


def made(thumbs, eid):
    return asyncio.run(thumbs.get(eid, DAY, "v1"))


def test_a_picture_is_made_small_and_cut_to_the_eyepiece_with_its_outlines_drawn():
    small = shrink(jpeg(1920, 1080))
    assert max(size_of(small)) == THUMB_PX  # decoded at a reduced scale, then made small
    cut = shrink(jpeg(1920, 1080), region=(960, 540, 536), outlines=[([[(900, 500), (1020, 500), (1020, 580), (900, 580)]], (255, 0, 0))])
    w, h = size_of(cut)
    assert w == h == THUMB_PX  # the disc's square
    pixels = np.asarray(Image.open(io.BytesIO(cut)).convert("RGB")).astype(int)
    red = (pixels[..., 0] > 170) & (pixels[..., 1] < 110) & (pixels[..., 2] < 110)
    assert red.sum() > 20  # the outline is drawn
    assert shrink(png()) and shrink(b"not a picture") is None and shrink(None) is None


def test_every_product_kind_has_one_and_what_is_no_product_has_none(tmp_path):
    picture = jpeg()
    entries = [entry("b1", "bookmark"), entry("k1", "clip"), entry("d1", "result", made_by={"tool": "reference_photo"}),
               entry("d2", "result", made_by={"tool": "count"}), entry("named-o3", "named-outline"), entry("m1", "tracks")]
    pictures = {(e.id, 0): picture for e in entries}
    thumbs = Thumbnails(FakeIndex(entries, pictures))
    for eid in ("b1", "k1", "d1", "m1"):
        assert size_of(made(thumbs, eid))[0] <= THUMB_PX, eid
    assert made(thumbs, "d2") is None  # a count is an answer, not a product
    assert made(thumbs, "named-o3") is None
    assert made(thumbs, "nothing") is None


def test_cell_outlines_show_their_captured_view_with_the_cells_drawn(tmp_path):
    from orchestrator.capture import Capture
    from orchestrator.evidence import evidence_of
    from tests.test_evidence import population

    hub = SimpleNamespace()
    store = evidence_of(hub)
    info = SimpleNamespace(id=7, width=160, height=120, epoch=0, dx=0.0, dy=0.0, fov=None, disc=0.0, wall_at=1000.0)
    p = population(store, Capture(7, jpeg(160, 120, 30), info))
    p.day = DAY
    store.add(p)
    index = FakeIndex([entry(p.id, "population")], {(p.id, 0): jpeg(160, 120, 30)}, hub=hub)
    plain = shrink(jpeg(160, 120, 30))
    drawn = made(Thumbnails(index), p.id)
    assert drawn is not None and drawn != plain
    pixels = np.asarray(Image.open(io.BytesIO(drawn)).convert("RGB")).astype(int)
    background = np.asarray(Image.open(io.BytesIO(plain)).convert("RGB")).astype(int)
    assert (np.abs(pixels - background).sum(axis=2) > 120).sum() > 30  # the three cells' outlines


def test_a_researchers_work_shows_its_picture_else_the_view_it_started_from(tmp_path):
    root = tmp_path / "research"
    inputs = root / DAY / "r3" / "inputs"
    inputs.mkdir(parents=True)
    (inputs / "frame.jpg").write_bytes(jpeg(320, 180, 90))
    session = SimpleNamespace(root=str(root), day=DAY, rid="r3", has_crop=False, current_outputs=lambda: [])
    research = SimpleNamespace(get=lambda rid: session if rid == "r3" else None)
    with_picture = entry("r4", "research", pictures=[f"/api/resources/r4/picture?day={DAY}&n=0"])
    index = FakeIndex([entry("r3", "research"), with_picture], {("r4", 0): jpeg(500, 500, 10)}, research=research)
    thumbs = Thumbnails(index)
    assert size_of(made(thumbs, "r3")) == (THUMB_PX, THUMB_PX * 180 // 320)  # the view it started from
    assert size_of(made(thumbs, "r4")) == (THUMB_PX, THUMB_PX)  # its own picture


def test_a_thing_followed_shows_its_newest_cell_outlined(monkeypatch):
    import orchestrator.population as population_module

    asked = []

    def captured(hub, population, members, *, outlines=True, **_):
        asked.append((population, members, outlines))
        return jpeg(120, 120, 77)

    monkeypatch.setattr(population_module, "captured_picture", captured)
    identity = SimpleNamespace(
        history=lambda i: [{"node": "e1/c2", "at": 1.0}, {"node": "o4", "at": 2.0}, {"node": "e3/c9", "at": 3.0}],
        member=lambda node: ("P" + node.split("/")[0], SimpleNamespace(id=node.split("/")[1])))
    thumbs = Thumbnails(FakeIndex([entry("i4", "individual")], identity=identity))
    assert made(thumbs, "i4") is not None
    assert asked == [("Pe3", ["c9"], True)]  # the newest observation that kept a picture, outlined
    thumbs = Thumbnails(FakeIndex([entry("i5", "individual")], identity=SimpleNamespace(history=lambda i: [{"node": "o4"}])))
    assert made(thumbs, "i5") is None  # only outlines followed: nothing kept to show


def video(path: Path, frames=30) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 15, (320, 240))
    for i in range(frames):
        writer.write(np.full((240, 320, 3), (i * 8) % 255, np.uint8))
    writer.release()
    return path


def test_the_users_own_pictures_and_recordings_are_sources_with_the_librarys_small_picture(tmp_path):
    """A Record dataset, a snapshot and a clearer picture are the session's sources, not products (ADR 0022, the user's
    Q11): Made in this session has no card for them, and their small picture is the library's, in Sources."""
    clean = tmp_path / "clean.png"
    clean.write_bytes(png())
    library = SimpleNamespace(get=lambda aid: SimpleNamespace(kind="still", path=clean) if aid == "a3" else None)
    entries = [entry("ds-20261011-031500", "dataset"), entry("snapshot-180412-f9", "snapshot"),
               entry("d1", "result", made_by={"tool": "enhance"})]
    thumbs = Thumbnails(FakeIndex(entries, {(e.id, 0): jpeg() for e in entries}, hub=SimpleNamespace(library=library)))
    for e in entries:
        assert made(thumbs, e.id) is None, e.id
    assert max(size_of(asyncio.run(thumbs.get("a3", LIBRARY, "v1")))) == THUMB_PX
    assert asyncio.run(thumbs.get("a4", LIBRARY, "v1")) is None


def test_a_library_recording_the_camera_bridge_already_pictured_is_shown_from_its_picture(tmp_path, monkeypatch):
    """The camera bridge keeps a small picture of every footage file on disk (footage_media.Library.thumb_path). The
    library's recordings in that folder (the examples, the real recordings) are shown from it, so opening Examples never
    queues one ffmpeg run per card ahead of the preview the person clicked. A file the bridge hasn't pictured yet, or
    one outside the footage folder, gets its poster frame as before."""
    import orchestrator.thumbnails as thumbnails

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "experiments" / "iphone-camera-feed" / "scripts"))
    from footage_media import Library as Footage

    footage = tmp_path / "footage"
    clip = footage / "corpus" / "corpus-amoeba.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"not decoded here")
    stat = clip.stat()
    pictured = Footage(footage, tmp_path / "none.json", tmp_path / "none.json").thumb_path(
        {"name": "corpus/corpus-amoeba.mp4", "bytes": stat.st_size, "mtime": int(stat.st_mtime)})
    pictured.parent.mkdir()
    pictured.write_bytes(jpeg(240, 135))
    later = footage / "corpus" / "corpus-hydra.mp4"
    later.write_bytes(b"not pictured yet")
    elsewhere = tmp_path / "datasets" / "camera.mp4"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"a dataset's video")
    paths = {"a7": clip, "a8": later, "a9": elsewhere}
    library = SimpleNamespace(footage=footage,
                              get=lambda aid: SimpleNamespace(kind="recording", path=paths[aid]) if aid in paths else None)
    posters = []
    monkeypatch.setattr(thumbnails, "poster", lambda path: posters.append(path) or jpeg())
    thumbs = Thumbnails(FakeIndex(hub=SimpleNamespace(library=library)))

    assert max(size_of(asyncio.run(thumbs.get("a7", LIBRARY, "v1")))) == THUMB_PX
    assert posters == []
    assert asyncio.run(thumbs.get("a8", LIBRARY, "v1")) is not None
    assert asyncio.run(thumbs.get("a9", LIBRARY, "v1")) is not None
    assert posters == [later, elsewhere]


@pytest.mark.timing  # its ffmpeg runs at background priority (taskpolicy -b), which a loaded Mac starves past any limit
def test_a_recording_shows_its_poster_frame_made_by_ffmpeg_at_background_priority(tmp_path, monkeypatch):
    if shutil.which("ffmpeg") is None:
        pytest.skip("no ffmpeg here")
    import orchestrator.thumbnails as thumbnails

    commands = []
    real = thumbnails.subprocess.run
    monkeypatch.setattr(thumbnails.subprocess, "run", lambda cmd, **kw: commands.append(cmd) or real(cmd, **kw))
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    rid = "MicroscopeMind-20261011-031500"
    (recordings / f"{rid}.webm").write_bytes(b"still being remuxed")
    video(recordings / f"{rid}-play.mp4")
    thumbs = Thumbnails(FakeIndex([entry(rid, "recording")], recordings=recordings))
    assert size_of(made(thumbs, rid))[0] <= THUMB_PX
    assert commands and commands[0][0] in ("taskpolicy", "nice") and str(recordings / f"{rid}-play.mp4") in commands[0]
    assert poster(tmp_path / "missing.mp4") is None


def test_made_once_one_at_a_time_on_a_background_thread_and_kept_in_memory():
    seen = []
    index = FakeIndex([entry("b1", "bookmark")], {("b1", 0): jpeg()})
    thumbs = Thumbnails(index)
    original = thumbs.make
    thumbs.make = lambda eid, day: seen.append(threading.current_thread().name) or original(eid, day)

    async def many():
        return await asyncio.gather(*(thumbs.get("b1", DAY, "v1") for _ in range(5)))

    results = asyncio.run(many())
    assert len(set(results)) == 1 and results[0] is not None
    assert len(seen) == 1 and seen[0].startswith("thumbnails")  # one job for the five cards, off the loop
    asyncio.run(thumbs.get("b1", DAY, "v1"))
    assert len(seen) == 1  # kept: no second job
    asyncio.run(thumbs.get("b1", DAY, "v2"))
    assert len(seen) == 2  # the entry changed: made again
    assert asyncio.run(thumbs.get("nothing", DAY, "v1")) is None
    asked = len(index.asked)
    asyncio.run(thumbs.get("nothing", DAY, "v1"))
    assert len(index.asked) == asked  # a miss is asked again only after a while
    # Never raises, whatever the system. In a thread of its own: lowered, this worker's thread would run every later
    # test on it at background priority.
    lowered = threading.Thread(target=background_priority)
    lowered.start()
    lowered.join(HANG_S)


def test_the_thumbnail_is_served_over_http_with_the_index(tmp_path):
    from fastapi.testclient import TestClient

    from orchestrator.server import create_app

    web = create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path))
    built = web.state.app
    photo = built.hub.resources.results.add("reference_photo", {"what": "a stoma"}, "a stoma, from Commons",
                                            picture_bytes=(jpeg(1200, 800),), web={"site": "commons.wikimedia.org"})
    snaps = built.paths.snapshots / built.day.day
    snaps.mkdir(parents=True)
    (snaps / "snapshot-180412-f9.json").write_text(json.dumps({"frameId": 9, "wallAt": 1.7e9, "outlines": []}))
    (snaps / "snapshot-180412-f9.png").write_bytes(png())
    count = built.hub.resources.results.add("count", {"what": "cells"}, "6 cells", values={"count": 6})
    client = TestClient(web)
    got = client.get(f"/api/resources/{photo['id']}/thumbnail?day={built.day.day}&v=abc")
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
    assert "max-age" in got.headers["cache-control"] and max(size_of(got.content)) == THUMB_PX
    for eid in (count["id"], "snapshot-180412-f9"):  # an answer; a source (its small picture is the library's)
        missing = client.get(f"/api/resources/{eid}/thumbnail?day={built.day.day}&v=abc")
        assert missing.status_code == 404 and "max-age=30" in missing.headers["cache-control"], eid
    assert client.get(f"/api/resources/{photo['id']}/thumbnail?day=../x").status_code == 400
