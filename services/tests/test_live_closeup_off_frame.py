"""A live close-up whose object the slide carries off the frame holds its last frame; it never fails for good.

will-tabs on 951e683 (reviews/2026-10-29-live-partner/tabs-951e683, 11:07:08) and ab-fast-off-b1e2727 (05:54:37): a
live magnifier window reported "The renderer could not load the requested content" (MEDIA_LOAD_FAILED, the studio's
load error `source_unavailable`). Reproduced on an isolated instance on the same June 14 leaf recording (30 Oct): the
slide moved about 490 px, the outlined stoma's predicted box went above the frame while its centre stayed inside the
eyepiece disc (that footage's disc, centre (758, 386) radius 635, reaches past the 1280x720 frame's top and bottom), so
`in_view` passed and the padded crop came out empty ([685, 0, 771, -21]). The encoder refused it
(`source_identity_mismatch`: "the current object crop is outside its camera frame"), the delivery answered 404, and the
studio's live delivery (live-window-delivery.ts) failed every window of that source and stopped for good. Every later
step on that window (a zoom, a move) then failed on it.

An object off the frame is out of view: the window holds its last frame (or waits for its first), as for an object out
of the eyepiece, and the encoder is never handed a crop it refuses."""
from types import SimpleNamespace

import numpy as np
import pytest

from orchestrator import live_sources
from orchestrator.live_sources import LiveSources, _ObjectLease
from orchestrator.presentation_delivery import PreparedSources

WIDTH, HEIGHT = 1280, 720


class Outline(SimpleNamespace):
    """An outline as the scene keeps it (a lease holds it weakly, as the real one)."""
DISC = SimpleNamespace(cx=758, cy=386, radius=635)  # the leaf recording's eyepiece disc (/api/health fov)


def capture(frame, dx, dy):
    """A camera frame as the capture store keeps it, at slide offset (dx, dy) from where the outline was made."""
    return SimpleNamespace(id=frame, width=WIDTH, height=HEIGHT, dx=dx, dy=dy, epoch=0, fov=DISC, boot="b1", seq=frame,
                           wall_at=1000.0 + frame, bgr=lambda: np.full((HEIGHT, WIDTH, 3), 90, np.uint8))


class Store:
    """The hub's capture store: the latest frame, and holds that need no memory accounting here."""
    def __init__(self):
        self.frames = {}

    def get(self, frame):
        return self.frames.get(frame)

    def hold(self, capture, *_why):
        return SimpleNamespace(capture=capture, release=lambda: None)


@pytest.fixture
def world(monkeypatch):
    store = Store()
    monkeypatch.setattr(live_sources, "captures", lambda hub: store)
    # The stoma as it was outlined (the instance's log, 30 Oct): its box, centre and anchor, tracked in this epoch.
    stoma = Outline(id="o1", bbox=(688.0, 312.0, 754.0, 451.0), center=(721.0, 381.5),
                    anchor_offset=(-0.52, 1.20), anchor_epoch=0, track="tracking", shown=True, origin=1261,
                    created_at=1.0)
    hub = SimpleNamespace(scene=SimpleNamespace(objects={"o1": stoma}), latest_info=None,
                          resources=SimpleNamespace(session=lambda: "2026-10-30"))
    sources = LiveSources(hub, motion=None)

    def show(frame, dx, dy):
        store.frames[frame] = capture(frame, dx, dy)
        hub.latest_info = SimpleNamespace(id=frame)
    return SimpleNamespace(hub=hub, sources=sources, stoma=stoma, show=show)


def encodable(snapshot) -> bool:
    """Whether the delivery's encoder takes this snapshot's crop (PreparedSources._read_live's own check)."""
    x0, y0, x1, y1 = snapshot.wire["crop"]
    return 0 <= x0 < x1 <= snapshot.capture.width and 0 <= y0 < y1 <= snapshot.capture.height


def encode(snapshot):
    """The delivery's real encoder on that snapshot, as one live image response makes it."""
    return PreparedSources._read_live(SimpleNamespace(app_run=live_sources.RUN), snapshot, None, lambda: None)


def test_an_object_carried_off_the_frame_holds_its_last_live_frame(world):
    lease = _ObjectLease(world.sources, world.stoma, "window w-1")
    world.show(1261, -0.57, 1.30)  # where it was outlined: on the frame
    first = lease.acquire_snapshot("response 1")
    assert first is not None and first.wire["liveState"] == "live" and encodable(first)
    assert encode(first).live_state == "live"

    world.show(1386, 6.94, -492.04)  # the slide moved: the stoma's box is above the frame, its centre in the disc
    later = lease.acquire_snapshot("response 2")
    assert later is not None, "the window keeps its last frame"
    assert encodable(later), f"never a crop the encoder refuses: {later.wire['crop']}"
    assert later.wire["liveState"] == "held" and later.capture.id == 1261
    assert encode(later).live_state == "held"
    assert lease.current(), "the window's source stays deliverable"


def test_an_object_off_the_frame_before_its_first_frame_waits_for_one(world):
    """A close-up opened after the slide carried the object away (ab-fast-off, 05:54:37: zoom 28 s after the find): no
    sample yet, so the window waits (the delivery's 425), never a mismatch that fails it."""
    lease = _ObjectLease(world.sources, world.stoma, "window w-1")
    world.show(1386, 6.94, -492.04)
    assert lease.acquire_snapshot("response 1") is None
    assert lease.current(), "still waiting for its first frame, not ended"
    source = world.sources.object_source(world.stoma)
    assert world.sources.acquire_snapshot(source, "response 2") is None

    world.show(1400, -0.57, 1.30)  # back on the frame: live again
    snapshot = world.sources.acquire_snapshot(source, "response 3")
    assert snapshot is not None and encodable(snapshot)
