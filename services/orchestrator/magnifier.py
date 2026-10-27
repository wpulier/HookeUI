"""The magnifier: a closer look at one object, in a panel floating over the dark surround
beside the eyepiece (never over its object; the live view never shrinks for it).

Its state lives here because the brain's tools drive it ("zoom in on that", "more", "hold
it") as well as the studio (wheel, pinch, + and -). Sources, flipped between with dots in
the studio:
  live   the object enlarged from the live frames, drawn by the browser from the full
         resolution picture it already has (no extra traffic), labelled "digital zoom xN"
  held   the sharpest recent frame of the object at full camera resolution
         (GET /api/crop/<object id>), steady while the slide moves
  image  a picture: one the app made from real frames, or a researcher's, or a reference photo
         from the web (with its source credit); each says what kind it is (`media`, media.py)
Digital zoom cannot add detail the camera did not capture; the studio says so.

A picture can be shown `beside` the live close-up, both at once in the one window ("a magnification
next to the photo": the partner's live case 7), and pictures already in it stay among its sources
(its dots) when it opens on another object.

The level is how much closer than the fitted view (the object with its padding filling the
panel): 1 = fit, 4 = a window a quarter as wide. Any level is allowed up to `max_level`
(64x, or until the window is about 4 camera pixels across); past the camera's resolution the
picture is pixel-enlarged and the studio shows the camera's pixels as blocks rather than
refusing.
"""
from __future__ import annotations

import hashlib
import re
import time

import cv2
import numpy as np

from . import contract, media, spaces


def object_box_in(obj, info) -> tuple[float, float, float, float]:
    """The object's bounding box in the coordinates of frame `info`."""
    x0, y0, x1, y1 = obj.bbox
    sx, sy = info.dx - obj.anchor_offset[0], info.dy - obj.anchor_offset[1]
    return x0 + sx, y0 + sy, x1 + sx, y1 + sy


def padded_rect(box, pad: float, width: int, height: int, min_side: int = 48,
                aspect: float | None = None) -> tuple[int, int, int, int]:
    """The box with `pad` on each side (at least min_side), widened to `aspect` (w/h) if given,
    clipped to the frame."""
    x0, y0, x1, y1 = box
    w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    side_x = max(w * (1 + 2 * pad), min_side)
    side_y = max(h * (1 + 2 * pad), min_side)
    if aspect:
        if side_x / side_y < aspect:
            side_x = side_y * aspect
        else:
            side_y = side_x / aspect
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx0, ry0 = int(max(0, cx - side_x / 2)), int(max(0, cy - side_y / 2))
    rx1, ry1 = int(min(width, cx + side_x / 2)), int(min(height, cy + side_y / 2))
    return rx0, ry0, rx1, ry1


def region_sharpness(gray: np.ndarray) -> float:
    """Contrast-normalised sharpness of a region: var(Laplacian) / mean^2."""
    if gray.size == 0:
        return 0.0
    mean = float(gray.mean()) or 1.0
    return float(cv2.Laplacian(gray, cv2.CV_32F).var()) / (mean * mean)


def sharpest_frame(hub, obj, window_s: float = 2.0, top: int = 8):
    """The view of the last `window_s` seconds where the object looks sharpest.

    Only frames in the object's current motion epoch qualify (so its position is known),
    preferring steady ones; the top few by whole-view focus are then scored on the object's
    own region. Returns (capture, rect, score) or None. Each candidate's bytes are taken with its
    facts in one step, so the frame ring can never drop one between choosing it and reading it
    (this runs in a worker thread, beside the loop that fills the ring; ADR 0010). The chosen one
    comes back as the capture store's one Capture for that frame (`Captures.adopt`), and the
    candidates are decoded one at a time in room reserved for one (invariant 3); Refused when there
    is none.
    """
    from .capture import captures, reserving

    latest = hub.latest_info
    if latest is None:
        return None
    now = latest.wall_at
    candidates = []
    for fid, info in list(hub.infos.items()):
        if info.epoch != obj.anchor_epoch or now - info.wall_at > window_s:
            continue
        frame = hub.source.get(fid)
        if frame is not None:
            candidates.append((info, frame))
    if not candidates:
        return None
    steady = [c for c in candidates if c[0].steady] or candidates
    steady.sort(key=lambda c: c[0].focus, reverse=True)
    best = None
    with reserving(hub, getattr(latest, "width", 0) or 0, getattr(latest, "height", 0) or 0, "hold", "choosing the sharpest view"):
        for info, frame in steady[:top]:
            gray = cv2.imdecode(np.frombuffer(frame.jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
            if gray is None:
                continue
            rect = padded_rect(object_box_in(obj, info), 0.0, gray.shape[1], gray.shape[0], min_side=16)
            score = region_sharpness(gray[rect[1]:rect[3], rect[0]:rect[2]])
            if best is None or score > best[3]:
                best = (info, frame.jpeg, padded_rect(object_box_in(obj, info), 0.15, gray.shape[1], gray.shape[0]), score)
            del gray
    if best is None:
        return None
    info, jpeg, rect, score = best
    return captures(hub).adopt(info, jpeg), rect, score


# contract/overlay.json "magnifier", which the studio reads too (apps/studio/src/overlay.ts).
MAGNIFIER_ASPECT = contract.MAGNIFIER_ASPECT  # the studio's magnifier panel (4:3)
MAX_LEVEL = contract.MAGNIFIER_MAX_LEVEL  # x the fitted view
MIN_WINDOW_PX = contract.MAGNIFIER_MIN_WINDOW_PX  # the smallest window of the camera picture the magnifier shows
FIT_PAD = contract.MAGNIFIER_FIT_PAD  # the fitted view: the object with 15% each side...
FIT_MIN_PX = contract.MAGNIFIER_FIT_MIN_PX  # ...at least 32 px
PANEL_CSS_PX = contract.MAGNIFIER_PANEL_CSS_PX  # the panel's usual width (22rem) for judging pixel enlargement
PIXELATED_K = contract.MAGNIFIER_PIXELATED_K  # screen px per camera px past which the camera's pixels show as blocks


def fit_window(box_w: float, box_h: float, aspect: float = MAGNIFIER_ASPECT) -> tuple[float, float]:
    """The camera-pixel window the magnifier shows at level 1 (overlay.ts fitWindow is the same)."""
    w = max(box_w * (1 + 2 * FIT_PAD), FIT_MIN_PX)
    h = max(box_h * (1 + 2 * FIT_PAD), FIT_MIN_PX)
    if w / h > aspect:
        h = w / aspect
    else:
        w = h * aspect
    return w, h


def level_limit(fit: tuple[float, float] | None) -> float:
    """The closest the magnifier goes for a fitted window: MAX_LEVEL x, or a window about
    MIN_WINDOW_PX camera px across (overlay.ts maxLevel is the same)."""
    if fit is None:
        return MAX_LEVEL
    return max(1.0, min(MAX_LEVEL, min(fit) / MIN_WINDOW_PX))


def parse_level(value) -> float | str | None:
    """A level as the brain or studio gives it: a number ("4", "x4", "4x", 4) or a word
    ("more", "less", "fit", "max"). None when it is neither."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    text = str(value).strip().lower().replace("×", "x")
    words = {"more": "more", "closer": "more", "in": "more", "less": "less", "out": "less", "fit": "fit",
             "reset": "fit", "max": "max", "maximum": "max", "most": "max"}
    if text in words:
        return words[text]
    m = re.fullmatch(r"x?\s*(\d+(?:\.\d+)?)\s*x?", text)
    return float(m.group(1)) if m and float(m.group(1)) > 0 else None


def crop_jpeg(hub, obj, pad: float = 0.15, quality: int = 92, aspect: float | None = MAGNIFIER_ASPECT,
              *, with_facts: bool = False, retained=None) -> tuple[bytes, dict] | None:
    """Native-resolution JPEG of the object from its sharpest recent frame, shaped to fill the
    magnifier."""
    from .capture import CaptureGone, Refused, decoding

    try:
        best = sharpest_frame(hub, obj)
        if best is None:
            return None
        capture, rect, score = best  # the bytes and facts of the chosen view, taken together
        with decoding(hub, capture, "look", owner="a close-up crop") as held:  # its decode is counted (invariant 3)
            image = held.bgr()
            if image is None:
                return None
            rect = padded_rect(object_box_in(obj, capture), pad, image.shape[1], image.shape[0], aspect=aspect)
            x0, y0, x1, y1 = rect
            h, w = image.shape[:2]
            if retained is not None:
                retained.grow((x1 - x0) * (y1 - y0) * 11 + 4096, "retain a clean outline crop")
            ok, jpeg = cv2.imencode(".jpg", image[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, quality])
            data = jpeg.tobytes() if ok else None
            del image, jpeg
    except (CaptureGone, Refused):
        return None
    if data is None:
        return None
    facts = {"frame_id": capture.id, "rect": rect, "sharpness": score}
    if with_facts:
        import copy
        from .capture import RUN

        facts.update({"frame": capture.id, "run": RUN, "seq": getattr(capture, "seq", None),
                      "boot": getattr(capture, "boot", None), "at": getattr(capture.info, "wall_at", None),
                      "scale": copy.deepcopy(capture.scale), "outline": obj.id,
                      "originalSize": [w, h], "naturalSize": [x1 - x0, y1 - y0],
                      "cropAffine": [1, 0, 0, 1, -x0, -y0],
                      "padding": {"fraction": pad, "minimumSide": 48, "aspect": aspect},
                      "touchesFrameEdge": [x0 == 0, y0 == 0, x1 == w, y1 == h],
                      "quality": quality, "outlines": False})
    return data, facts


def side_by_side(images: list[bytes], height: int = 360, gap: int = 8, *, same_scale: bool = False) -> bytes | None:
    """Two or more crops scaled to one height, next to each other (the compare tool)."""
    decoded = [cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR) for j in images]
    decoded = [d for d in decoded if d is not None]
    if not decoded:
        return None
    if same_scale:
        # One factor for every crop; padding, not per-image enlargement, makes the row rectangular.
        tallest = max(d.shape[0] for d in decoded)
        decoded = [cv2.copyMakeBorder(d, 0, tallest - d.shape[0], 0, 0, cv2.BORDER_CONSTANT,
                                     value=(43, 26, 19)) for d in decoded]
    scaled = [cv2.resize(d, (max(1, int(d.shape[1] * height / d.shape[0])), height), interpolation=cv2.INTER_CUBIC) for d in decoded]
    spacer = np.full((height, gap, 3), (43, 26, 19), np.uint8)  # the studio's ink colour (BGR)
    row = [scaled[0]]
    for img in scaled[1:]:
        row += [spacer, img]
    ok, jpeg = cv2.imencode(".jpg", np.hstack(row), [cv2.IMWRITE_JPEG_QUALITY, 90])
    return jpeg.tobytes() if ok else None


def comparison_picture(hub, images: list[bytes]) -> bytes | None:
    """Reserve the peak of both decodes, padding, resize and row before composing them."""
    import io
    from PIL import Image
    from .capture import captures

    sizes = []
    for jpeg in images:
        with Image.open(io.BytesIO(jpeg)) as image:
            sizes.append(image.size)  # header only; no decode before admission
    tallest = max(h for w, h in sizes)
    native = sum(w * h for w, h in sizes)
    padded = sum(w * tallest for w, h in sizes)
    output = (sum(max(1, int(w * 360 / tallest)) for w, h in sizes) + 8 * len(sizes)) * 360
    with captures(hub).memory.reserve("analysis", 6 * (native + padded + output) + 1024 * 1024, "source comparison"):
        return side_by_side(images, same_scale=True)


PICTURES_KEPT = 8
PICTURES_CARRIED = 4  # pictures that stay among its sources when it opens on another object


class Magnifier:
    def __init__(self, hub) -> None:
        self.hub = hub
        self.open = False
        self.object_id: str | None = None
        self.sources: list[dict] = []
        self.index = 0
        self.resume_live = False  # only an automatic captured-view fallback may return when tracking recovers
        self.beside_source: str | None = None  # image primary to which a comparison belongs
        self.beside: dict | None = None  # a picture shown next to the live close-up, both at once (an image source)
        # Pictures the app made from real frames (a comparison, tracks, an averaged frame, a
        # snapshot), served at /api/picture/<key>; the newest PICTURES_KEPT are kept.
        self.pictures: dict[str, bytes] = {}
        self.picture_taps: list = []  # (jpeg, label) -> None: a dataset keeps the pictures it showed
        # How much closer than the fitted view (None = fit), and an optional point to centre
        # on (frame coordinates of the object's anchor frame; None = the object's centre).
        self.level: float | None = None
        self.center: tuple[float, float] | None = None
        hub.scene.on_remove.append(self.forget)

    def identify_sources(self, produced: list[dict], resource: dict) -> None:
        """Attach identity only to the instances this operation made, even when another made identical pixels."""
        for source in self.sources + ([self.beside] if self.beside else []):
            if any(source is original for original in produced):
                source["resource"] = dict(resource)
        self._publish()

    def wire(self) -> dict:
        beside = self.shown_beside()
        return {"type": "magnifier", "open": self.open, "objectId": self.object_id,
                "space": spaces.of_wire("magnifier"),
                "sources": [{**s, "space": spaces.source_space(s.get("kind"))} for s in self.sources],
                "index": self.index,
                "beside": {**beside, "space": spaces.source_space(beside.get("kind"))} if beside else None,
                "zoom": self.level,
                "center": list(self.center) if self.center else None,
                **({"resumeLive": True} if self.resume_live else {}),
                # Worked out here only; the studio shows them (one writer).
                "maxLevel": round(self.max_level(), 3), "pixelated": self.pixel_enlarged()}

    # --- the level ------------------------------------------------------------------------
    def _fit(self) -> tuple[float, float] | None:
        obj = self.hub.scene.objects.get(self.object_id) if self.object_id else None
        if obj is None or not obj.polygons:
            return None
        x0, y0, x1, y1 = obj.bbox
        return fit_window(x1 - x0, y1 - y0)

    def max_level(self) -> float:
        """The closest it goes: 64x the fitted view, or a window about 4 camera px across."""
        return level_limit(self._fit())

    @property
    def factor(self) -> float:
        return self.level or 1.0

    def pixel_enlarged(self) -> bool:
        """Past the camera's detail: each camera pixel is drawn as a block (as the studio does)."""
        fit = self._fit()
        return fit is not None and PANEL_CSS_PX / (fit[0] / self.factor) > PIXELATED_K

    def _resolve(self, level) -> float:
        parsed = parse_level(level)
        if parsed is None:
            raise ValueError(f"not a zoom level: {level!r}")
        current = self.factor
        target = {"more": current * 2, "less": current / 2, "fit": 1.0, "max": self.max_level()}.get(parsed, parsed)
        return min(self.max_level(), max(1.0, float(target)))

    def set_level(self, level, publish: bool = True) -> float:
        """Zoom the open magnifier to `level` (number, "more", "less", "fit" or "max");
        returns the level it is at now (clamped between fit and max_level)."""
        value = self._resolve(level)
        self.level = None if value <= 1.0 + 1e-6 else round(value, 3)
        if publish:
            self._publish()
        return self.factor

    def _publish(self) -> None:
        self.hub.broadcast(self.wire())

    def _may_open(self) -> None:
        """A request's late result never reopens a magnifier closed since it was asked (ticket.py,
        scope "magnifier"): ToolError saying so, and nothing changes on screen."""
        from . import ticket as tickets
        from .tools import ToolError

        request = tickets.current()
        why = request.superseded("magnifier") if request is not None else None
        if why:
            raise ToolError(f"not shown: {why}", next="ask again to open it")

    def _object_sources(self, object_id: str) -> list[dict]:
        v = int(time.time() * 1000)
        return [{"kind": "live", "label": "Live", "media": media.CAPTURE},
                {"kind": "held", "label": "Sharpest frame", "url": f"/api/crop/{object_id}?v={v}", "media": media.CAPTURE}]

    def pictures_shown(self) -> list[dict]:
        """The pictures among its sources (not the live close-up or the sharpest frame), oldest first: the newest
        PICTURES_CARRIED of them whose picture is still served (the app's own are kept PICTURES_KEPT at a time)."""
        if not self.open:
            return []
        served = [s for s in self.sources if s.get("kind") == "image" and self.picture_kept(str(s.get("url") or ""))]
        return served[-PICTURES_CARRIED:]

    def picture_kept(self, url: str) -> bool:
        """Whether a source's bytes have survived our picture cache; other URLs are served elsewhere."""
        return not url.startswith("/api/picture/") or url.rsplit("/", 1)[-1] in self.pictures

    def scale_picture(self, key: str, scale) -> float:
        """Enlarge a displayed image's existing pixels without changing the camera or its neighbours."""
        import math
        from .tools import ToolError

        self._may_open()
        source = next((s for s in self.sources if s.get("kind") == "image" and s.get("url") == key), None)
        visible = [self.picture_now(), self.shown_beside()]
        if not self.open or source is None or source not in visible or not self.picture_kept(key):
            raise ToolError("that picture is not displayed or is no longer kept", next="show the picture first")
        current = source.get("displayScale", 1.0)
        relative = {"more": min(8.0, current * 1.25), "less": max(1.0, current / 1.25), "fit": 1.0}
        try:
            value = float(relative.get(str(scale), scale))
            if isinstance(scale, bool) or not math.isfinite(value) or not 1 <= value <= 8:
                raise ValueError()
        except (ValueError, TypeError):
            raise ToolError("picture scale must be 1 to 8, more, less or fit",
                            next="choose a picture scale from 1 to 8, or ask to fit it")
        self.resume_live = False
        source["displayScale"] = round(value, 3)
        self._publish()
        return source["displayScale"]

    def picture_now(self) -> dict | None:
        """The picture it shows now (an image source), or None while it shows the close-up."""
        if not self.open or not self.sources:
            return None
        current = self.sources[min(self.index, len(self.sources) - 1)]
        return current if current.get("kind") == "image" else None

    @property
    def displayed_object(self) -> str | None:
        """A retained picture's own subject, separate from the available live close-up."""
        picture = self.picture_now()
        return picture.get("objectId") if picture is not None else (self.object_id if self.open else None)

    def shown_beside(self) -> dict | None:
        """The picture shown next to the close-up, while the close-up (live or sharpest frame) is what it shows."""
        if not self.open or self.beside is None or not self.sources:
            return None
        current = self.sources[min(self.index, len(self.sources) - 1)]
        key = current.get("url") if current.get("kind") == "image" else current.get("kind")
        if current.get("kind") == "image":
            return self.beside if key == self.beside_source and key != self.beside.get("url") else None
        return self.beside if self.beside_source is None else None

    def zoom(self, object_id: str, hold: bool = False, level=None, beside: dict | None = None) -> float:
        """Open on an object. On the object already open the sources stay and only the level
        changes (when one is given); a new object starts fitted unless a level is given, and the
        pictures it was showing stay among its sources. `beside`: a picture to show next to the
        close-up, both at once (one of `pictures_shown()`). Returns the level it is at now."""
        self._may_open()
        self.resume_live = False
        if self.open and object_id == self.object_id and self.sources:
            if hold:
                self.index = next((i for i, s in enumerate(self.sources) if s["kind"] == "held"), self.index)
            else:
                self.index = next((i for i, s in enumerate(self.sources) if s["kind"] == "live"), self.index)
        else:
            kept = self.pictures_shown()  # a picture the user pulled up is never dropped by opening on an object
            if beside is not None and beside not in kept:
                kept = kept[-(PICTURES_CARRIED - 1):] + [beside]
            self.open, self.object_id = True, object_id
            self.sources = self._object_sources(object_id) + kept
            self.index = 1 if hold else 0
            self.level, self.center = None, None
        if beside is not None:
            self.beside_source = None
            self.beside = next((s for s in self.sources if s.get("kind") == "image" and s.get("url") == beside.get("url")), None)
        if level is not None:
            self.set_level(level, publish=False)
        self._publish()
        return self.factor

    def follow(self, object_id: str) -> None:
        """The selection moved: an open magnifier follows it."""
        if self.open and object_id != self.object_id:
            self.zoom(object_id, hold=self.sources[self.index]["kind"] == "held" if self.sources else False)

    def hold(self) -> None:
        if self.object_id is None:
            return
        self.sources = self._object_sources(self.object_id) + [s for s in self.sources if s["kind"] == "image"]
        self.index = 1
        self.resume_live = False
        self._publish()

    def _pairing_peer(self, url: str, beside: bool | dict) -> str | None:
        """Validate both public picture entry points before changing cache, sources or selection."""
        from .tools import ToolError

        current = self.sources[self.index] if self.open and self.sources else None
        peer = beside if isinstance(beside, dict) else current if beside is True else None
        if peer is None:
            return None
        key = peer.get("url") if peer.get("kind") == "image" else peer.get("kind")
        if peer not in self.sources or not key or not self.picture_kept(key):
            raise ToolError("the comparison source is no longer retained", next="open it again first")
        if key == url:
            raise ToolError("the same picture cannot be shown beside itself", next="choose a different picture")
        return key

    def show_picture(self, jpeg: bytes, label: str, object_id: str | None = None, kind_of: str = media.CAPTURE,
                     credit: str = "", resume_live: bool = False, beside: bool | dict = False) -> str:
        """Show a picture beside the live close-up; returns its URL. One the app made from real frames
        (`kind_of` CAPTURE, the default) is also given to a dataset being recorded; a reference photo from the
        web (REFERENCE) is not, as it is not from this slide."""
        self._may_open()
        key = hashlib.sha1(jpeg).hexdigest()[:12]
        url = f"/api/picture/{key}"
        current = self.sources[self.index] if self.open and self.sources else None
        peer_url = self._pairing_peer(url, beside)
        protected = {url, peer_url, (self.shown_beside() or {}).get("url"), (current or {}).get("url")}
        self.pictures[key] = jpeg
        for tap in list(self.picture_taps) if kind_of == media.CAPTURE else []:  # made from real frames
            try:
                tap(jpeg, label)
            except Exception:
                pass
        while len(self.pictures) > PICTURES_KEPT:
            self.pictures.pop(next(k for k in self.pictures if f"/api/picture/{k}" not in protected))
        url = f"/api/picture/{key}"
        self.show_image(url, label, credit=credit, object_id=object_id, kind_of=kind_of, resume_live=resume_live, beside=beside)
        from .brain.registry import attach, CURRENT_RUN

        run = CURRENT_RUN.get()
        if run is not None:
            run.picture_sources.extend(s for s in self.sources if s.get("url") == url)
        attach(picture=key)  # the tool that made it keeps it as its result's picture (resources.Results)
        return url

    def show_image(self, url: str, label: str, credit: str = "", object_id: str | None = None,
                   kind_of: str = media.UNKNOWN, resume_live: bool = False, beside: bool | dict = False) -> None:
        """Add a picture as another source and show it; the live close-up stays a source
        beside it (its dot), so the picture never replaces the live view. `kind_of`: what kind
        of picture it is (media.py), said with it."""
        self._may_open()
        self._pairing_peer(url, beside)
        previous = self.sources[self.index] if self.open and self.sources else None
        picture_object = object_id  # inference below keeps a live source, not the picture's identity
        scene = self.hub.scene
        if object_id is None and self.object_id is None and scene.selected in scene.objects:
            object_id = scene.selected  # keep a live close-up of what is selected beside it
        if object_id is not None and (object_id != self.object_id or not self.open):
            kept = self.pictures_shown()  # earlier pictures stay among its dots
            self.object_id = object_id
            self.sources = self._object_sources(object_id) + kept
            self.level, self.center = None, None
        elif not self.open:
            self.sources = []
        self.open = True
        if beside is True and previous is not None and previous not in self.sources:
            self.sources.append(previous)
        if isinstance(beside, dict) and beside not in self.sources:
            self.sources.append(beside)
        self.sources = [s for s in self.sources if s.get("url") != url] + [
            {"kind": "image", "label": label, "url": url, "credit": credit, "media": kind_of,
             "line": media.line(kind_of, credit=credit),
             **({"objectId": picture_object} if picture_object is not None else {})}]
        self.index = len(self.sources) - 1
        self.resume_live = resume_live
        self.beside, self.beside_source = None, None
        # Pair atomically: the next published state contains both sources, with no replacement flash.
        if beside is True and previous is not None:
            key = previous.get("url") if previous.get("kind") == "image" else previous.get("kind")
            self.pair_sources(key, url, publish=False)
        elif isinstance(beside, dict):
            self.pair_sources(url, beside.get("url"), publish=False)
        self._publish()

    def pair_sources(self, primary: str, peer: str, *, publish: bool = True) -> None:
        """Arrange retained sources without fetching, relabelling or changing either image's pixels."""
        from .tools import ToolError

        self._may_open()
        def key(source):
            return source.get("url") if source.get("kind") == "image" else source.get("kind")
        index = next((i for i, source in enumerate(self.sources) if key(source) == primary), None)
        other = next((s for s in self.sources if s.get("kind") == "image" and s.get("url") == peer), None)
        if not self.open or index is None or not self.picture_kept(primary):
            raise ToolError("that primary source is not available", next="open the picture first")
        if peer and (other is None or not self.picture_kept(peer) or peer == primary):
            raise ToolError("choose a different retained picture to show beside it", next="open the other picture first")
        self.index, self.resume_live = index, False
        self.beside = other if peer else None
        self.beside_source = primary if self.sources[index].get("kind") == "image" and peer else None
        if publish:
            self._publish()

    def set_index(self, index: int) -> None:
        self._may_open()
        if 0 <= index < len(self.sources):
            self.resume_live = False
            self.index = index
            self._publish()

    def close(self) -> None:
        from . import ticket as tickets

        self.open, self.object_id, self.sources, self.index = False, None, [], 0
        self.level, self.center, self.beside, self.beside_source = None, None, None, None
        self.resume_live = False
        generations = getattr(getattr(self.hub, "scene", None), "generations", None)
        if generations is not None:
            # Requests asked before the close may no longer open it again (the one closing it may).
            generations.bump("magnifier", why="the magnifier was closed since", by=tickets.current())
        self._publish()

    def forget(self, object_id: str) -> None:
        """The object was removed: close if it was being shown."""
        if self.object_id == object_id:
            self.close()
