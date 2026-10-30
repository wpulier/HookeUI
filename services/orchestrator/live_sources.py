"""Original live producers shared by independent workspace windows.

Motion owns its analysis; an object magnifier only crops the existing camera
frames. Neither route opens a camera or starts another vision/model loop.
"""
import threading
import weakref

from .capture import RUN, captures
from .magnifier import object_box_in, padded_rect
from .motiontracks import LiveMotionSnapshot
from .presentations import SourceRef
from .brain.registry import ToolFailed
from .brain.toolbox.common import in_view


def _crop(obj, capture):
    """The object's padded crop on this camera frame, or None when the object is not on the frame now: in view (its
    centre inside the eyepiece disc, which on some footage reaches past the frame's edges) and still predicted off the
    frame, the slide having carried it there (the June 14 leaf recording, 30 Oct: the stoma's box went above the frame
    at a slide move of about 490 px, the crop came out empty, the encoder refused it and the studio failed the live
    window for good). None is out of view: the window holds its last frame, or waits for its first."""
    if capture is None or not in_view(obj, capture) or not getattr(obj, 'shown', True):
        return None
    x0, y0, x1, y1 = padded_rect(object_box_in(obj, capture), .15, capture.width, capture.height)
    return [x0, y0, x1, y1] if 0 <= x0 < x1 <= capture.width and 0 <= y0 < y1 <= capture.height else None


class _ObjectSnapshot(LiveMotionSnapshot):
    def __init__(self, hold, wire, lease):
        super().__init__(hold, wire)
        self.lease = lease

    def accept(self):
        """The encoder checked these original pixels before retaining them."""
        return self.lease.accept(self)


class _ObjectLease:
    def __init__(self, sources, obj, owner):
        self.sources, self.owner = sources, owner
        self.object_id, self.object = obj.id, weakref.ref(obj)
        self.released = False
        self.snapshot = None
        self.stopped = False
        self.lock = threading.RLock()

    def original(self):
        obj = self.object()
        return obj if obj is not None and self.sources.hub.scene.objects.get(self.object_id) is obj else None

    def current(self):
        with self.lock:
            return not self.released and (self.snapshot is not None or not self.stopped and self.original() is not None)

    def acquire_snapshot(self, owner):
        with self.lock:
            if not self.current():
                return None
            store = captures(self.sources.hub)
            if not self.stopped:
                obj = self.original()
                capture = store.get(getattr(self.sources.hub.latest_info, 'id', None)) if obj is not None else None
                rect = _crop(obj, capture) if obj is not None else None
                if rect is not None:
                    return _ObjectSnapshot(store.hold(capture, 'analysis', 'look', owner), {
                        'basis': 'camera-pixels', 'sample': {'run': RUN, 'frameId': capture.id,
                            'boot': capture.boot, 'seq': capture.seq, 'epoch': capture.epoch,
                            'imageSize': [capture.width, capture.height]},
                        'tracks': [], 'from': capture.wall_at, 'to': capture.wall_at,
                        'crop': list(rect), 'liveState': 'live'}, self)
                if obj is None or self.snapshot is not None:
                    # Never silently attach to a replacement or resume after loss.
                    self.stopped = True
            if self.snapshot is None:
                return None
            return LiveMotionSnapshot(store.hold(self.snapshot.capture, 'analysis', 'look', owner),
                                      {**self.snapshot.wire, 'liveState': 'held'})

    def accept(self, snapshot):
        with self.lock:
            if self.released:
                raise ToolFailed('this original image window has closed', next=None)  # no next: the owning window is gone
            # A slower HTTP request cannot roll the retained sample backwards.
            if self.snapshot is None or snapshot.capture.id >= self.snapshot.capture.id:
                store = captures(self.sources.hub)
                held = LiveMotionSnapshot(store.hold(snapshot.capture, 'analysis', 'bytes', self.owner), snapshot.wire)
                previous, self.snapshot = self.snapshot, held
                if previous is not None:
                    previous.release()
            if self.original() is None:
                self.stopped = True
            return 'held' if self.stopped else 'live'

    def release(self):
        with self.lock:
            self.released = True
            if self.snapshot is not None:
                self.snapshot.release()
                self.snapshot = None


class LiveSources:
    def __init__(self, hub, motion):
        self.hub, self.motion = hub, motion

    def object_source(self, obj):
        return SourceRef.from_wire({
            'key': f'live-object/{RUN}/{obj.id}', 'kind': 'live', 'media': 'capture',
            'availability': 'temporary',
            'identity': {'run': RUN, 'object': obj.id, 'day': self.hub.resources.session(),  # the session open now
                         'anchorCapture': str(obj.origin if obj.origin is not None else obj.created_at)},
            'provenance': {'basis': 'tracked-object', 'object': obj.id,
                           'coordinates': 'crop of the tracked object on this camera frame'}})

    def _object(self, source):
        obj = self.hub.scene.objects.get(source.identity.get('object'))
        if obj is None or source != self.object_source(obj):
            raise ToolFailed('this original camera object is no longer available', next=None)  # no next: the concrete limitation is already stated; no automatic substitute action
        return obj

    def acquire_live(self, source, owner):
        if 'object' not in source.identity:
            return self.motion.acquire_live(source, owner)
        return _ObjectLease(self, self._object(source), owner)

    def acquire_snapshot(self, source, owner):
        if 'object' not in source.identity:
            return self.motion.acquire_snapshot(source, owner)
        obj = self._object(source)
        capture = captures(self.hub).get(getattr(self.hub.latest_info, 'id', None))
        rect = _crop(obj, capture)
        if rect is None:
            return None
        return LiveMotionSnapshot(captures(self.hub).hold(capture, 'analysis', 'look', owner), {
            'basis': 'camera-pixels', 'sample': {'run': RUN, 'frameId': capture.id,
                'boot': capture.boot, 'seq': capture.seq, 'epoch': capture.epoch,
                'imageSize': [capture.width, capture.height]},
            'tracks': [], 'from': capture.wall_at, 'to': capture.wall_at, 'crop': list(rect)})
