"""The brain's eyes: look at the view with the selected vision model and point.

One vision call returns both the answer text and boxes for the things it mentions; the
boxes become precise outlines through the GPU worker and land in the scene with the
model's labels, shown as "likely" guesses. Its interface is `snapshot` (the frame a request
is about), `look` (one vision call: answer, targets, actions) and `point` (targets become
outlines). The brain (orchestrator/brain/loop.py) and the blind A/B arena use the same three,
so every arm looks the same way.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import cv2
import numpy as np

from . import dust as dustmod
from . import tools, execution
from .ticket import reusable
from .coords import Crop, gemini_box_to_frame
from .hub import Hub
from .marks import encode_crop, fov_crop, render_marks, render_masks
from .masks import RegionMarks, agrees
from .molmo import assign as molmo_assign, point_box
from .options import VISION
from .paths import Paths
from .brain.state import Conversation
from .brain.dispatch import not_answered
from .prompts import ASK, ASK_MARKS, ASK_MASKS, IDENTIFY, with_tools
from .labels import label_key
from .quality import describe, measured, unnameable
from .scene import status_for
from .taught import context_line as taught_line
from .zeroshot import WAIT_S as ZEROSHOT_WAIT_S, context_line as zeroshot_line
from .vision import make_vision

MAX_TARGETS = 15


def _box_in(obj, info) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = obj.bbox
    sx, sy = info.dx - obj.anchor_offset[0], info.dy - obj.anchor_offset[1]
    return x0 + sx, y0 + sy, x1 + sx, y1 + sy


def _same_thing(a, b) -> bool:
    """A model box and an existing outline's box point at the same thing: they overlap a lot,
    or each one's centre lies inside the other (outlines are tighter than model boxes)."""
    if _iou(a, b) >= 0.4:
        return True
    area = lambda r: max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])
    if min(area(a), area(b)) < 0.25 * max(area(a), area(b)):
        return False  # one inside the other but much smaller: a part of it (a nucleus in its cell)
    ca, cb = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2), ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    inside = lambda p, r: r[0] <= p[0] <= r[2] and r[1] <= p[1] <= r[3]
    return inside(ca, b) and inside(cb, a)


def _contains(outer, inner) -> bool:
    """`inner` lies inside `outer` and is clearly smaller (a part of it, not the same thing)."""
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    area = lambda r: max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3] and area(inner) * 2 < area(outer)


def target_box(target: dict, crop: Crop, marks=None) -> tuple[float, float, float, float] | None:
    """Where a look's target is, in frame pixels: its numbered mask's box ("masks"), else its box,
    else, for a point with no box (pointing "molmo"), a small box around the point."""
    mark, box, model_box = target.get("mark"), target.get("box_2d"), None
    if isinstance(box, list) and len(box) == 4:
        try:
            model_box = gemini_box_to_frame([float(v) for v in box], crop)
        except (TypeError, ValueError):
            model_box = None
    if isinstance(marks, RegionMarks) and isinstance(mark, int) and mark in marks.regions:
        mask_box = marks.box(mark)
        if model_box is None or agrees(mask_box, model_box):
            return mask_box
    point = target.get("point")
    if model_box is None and isinstance(point, list) and len(point) == 2:
        return point_box(point)
    return model_box


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def identify_context(specimen: str | None, scale: str = "", view: str = "", taught: str = "", seen: str = "",
                     dust: str = "", zeroshot: str = "") -> str:
    """The session's specimen as the naming prompt is told it (bench/naming measures how much
    this steers the name), the field width when the scale is known (scale.py: size helps
    tell things apart; the app states sizes itself), what the app measured on the picture
    (quality.measured: numbers the model weighs, never a verdict), and how close it is to the
    labels the user taught (taught.context_line: one line of data; the model decides), and the
    outline named earlier today most like it (seen.context_line: one line of data, past tense),
    MedSigLIP's zero-shot ranking of the subject's candidate names (zeroshot.context_line: one line of
    data with what its first name has been worth; the model decides), and where spots stay put while
    the slide moves (dust.line: measured, "likely dust on the optics")."""
    lines = [view] if view else []
    if specimen:
        lines.append(f"Earlier in this session you described the view as: {specimen}. Trust the image if it now shows "
                     f"something else.")
    if scale:
        lines.append(scale_line(scale))
    if taught:
        lines.append(taught)
    if seen:
        lines.append(seen)
    if zeroshot:
        lines.append(zeroshot)
    if dust:
        lines.append(dust)
    return "\n".join(lines)


def scale_line(words: str) -> str:
    """'The view is about 450 µm across at 40x (estimated from the objective)...' for a prompt."""
    return (f"Scale: {words}; a red blood cell is about 7.5 µm across. Use size to tell things apart, but do not "
            f"state sizes: the app measures them.")


def identify_reading(data: dict) -> dict:
    """What naming a click shows, from the model's reply (shared with bench/naming): when it is
    sure, its specific name; when it says it is not ("unsure", the model's own call), the broad
    group, with the specific name and the alternatives offered as "maybe"."""
    name = " ".join(str(data.get("label") or "").split())[:60]
    group = " ".join(str(data.get("group") or "").split())[:60]
    conf = data.get("confidence")
    confidence = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None
    alternatives = []
    for alt in data.get("alternatives") or []:
        alt_name = " ".join(str(alt.get("name") if isinstance(alt, dict) else alt or "").split())[:60]
        if alt_name and label_key(alt_name) not in {label_key(a) for a in alternatives}:
            alternatives.append(alt_name)
    unsure = bool(data.get("unsure"))
    observations = [" ".join(str(o).split())[:120] for o in data.get("observations") or [] if str(o).strip()][:3]
    if unsure and group:
        maybe = [n for n in [name, *alternatives] if n and label_key(n) != label_key(group)]
        return {"label": group, "status": "unsure", "maybe": maybe[:3], "confidence": confidence,
                "observations": observations, "specific": name}
    return {"label": name or group, "status": "unsure" if unsure else "likely",
            "maybe": [a for a in alternatives if label_key(a) != label_key(name)][:2] if unsure else [],
            "alternatives": alternatives[:2], "confidence": confidence, "observations": observations, "specific": name}


TAUGHT_WAIT_S = 1.0  # naming a click waits at most this long for the taught and seen matches (13 ms when warm)
CLOSE_MIN_SIDE = 384  # px: the close-up of a click is at least this big (smaller ones are enlarged)


def identify_images(image, polygons, crop: Crop, bbox) -> tuple[bytes, bytes]:
    """What naming a click sends: the view with the thing outlined in magenta (on a dark casing, so it shows on a
    magenta stain: drawing.py), and a close-up of it (3x its size plus 60 px, at most 512 px). Shared with
    bench/naming."""
    from .drawing import PALETTE, outline, points

    marked = image.copy()
    outline(marked, points(polygons), PALETTE["magenta"], 3)
    view_jpeg, _ = encode_crop(marked, crop)
    close = close_region(image, bbox)
    return view_jpeg, enlarged(image, close)


def close_region(image, bbox) -> Crop:
    """The close-up's part of the frame: 3x the thing's size plus 60 px, centred on it."""
    x0, y0, x1, y1 = bbox
    side = max(x1 - x0, y1 - y0) * 3 + 60
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return Crop(
        max(0, int(cx - side / 2)), max(0, int(cy - side / 2)),
        int(min(image.shape[1], cx + side / 2) - max(0, cx - side / 2)),
        int(min(image.shape[0], cy + side / 2) - max(0, cy - side / 2)),
    )


def enlarged(image, close: Crop, box: Crop | None = None) -> bytes:
    """`close` cut from the full-resolution frame as a JPEG, at least CLOSE_MIN_SIDE and at most
    512 px; with `box` (a part of it, frame pixels), that part drawn in cyan (the closer look asked for)."""
    region = image[close.y: close.y + close.height, close.x: close.x + close.width]
    if 0 < max(close.width, close.height) < CLOSE_MIN_SIDE:
        # A small thing (a fly's bristle socket) would reach the model as a few dozen pixels:
        # enlarged, beside the whole view (Perception Magnifier, 2025: magnifying while keeping
        # the context helped where a crop alone did not). It adds no detail; it lets the model
        # look at the detail there is.
        k = CLOSE_MIN_SIDE / max(close.width, close.height)
        region = cv2.resize(region, (round(close.width * k), round(close.height * k)), interpolation=cv2.INTER_CUBIC)
    if box is not None:
        k = region.shape[1] / max(1, close.width)
        region = region.copy()
        cv2.rectangle(region, (round((box.x - close.x) * k), round((box.y - close.y) * k)),
                      (round((box.x + box.width - close.x) * k), round((box.y + box.height - close.y) * k)),
                      (255, 220, 0), 2, cv2.LINE_AA)
    jpeg, _ = encode_crop(region, Crop(0, 0, region.shape[1], region.shape[0]), max_side=512)
    return jpeg


CLOSER_ROUNDS = 3  # vision calls at most when the model may ask for closer looks (option CLOSEUP)
CLOSER_MIN_SIDE = 12  # px of the camera frame: a closer look is never smaller (there is nothing finer)


def closer_box(box_2d, on: Crop, image_shape) -> Crop | None:
    """The part of `on` (frame pixels) the model asked to see closer ([ymin, xmin, ymax, xmax] on
    0-1000 of the image of `on`), squared up a little and kept inside the frame; None if unreadable."""
    try:
        x0, y0, x1, y1 = gemini_box_to_frame([float(v) for v in box_2d], on)
    except (TypeError, ValueError):
        return None
    side = max(x1 - x0, y1 - y0, CLOSER_MIN_SIDE) * 1.15  # a margin, so its edge is in view
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    h, w = image_shape[:2]
    bx0, by0 = max(0, int(cx - side / 2)), max(0, int(cy - side / 2))
    bx1, by1 = min(w, int(round(cx + side / 2))), min(h, int(round(cy + side / 2)))
    if bx1 - bx0 < 2 or by1 - by0 < 2:
        return None
    return Crop(bx0, by0, bx1 - bx0, by1 - by0)


def closer_offer(left: int, image: int) -> str:
    """The paragraph that lets the model ask for a closer look (option CLOSEUP; tests/golden/)."""
    return (f"\n\nCloser look: if the thing is too small or unclear in image {image} to name, you may ask to see "
            f"part of image {image} closer instead of naming it: reply only {{\"closer\": [ymin, xmin, ymax, xmax]}}, "
            f"a box on a 0-1000 scale of image {image}. The app cuts it from the full-resolution camera frame and "
            f"enlarges it (that adds no detail the camera did not capture). {left} closer look{'s' if left != 1 else ''} "
            f"left. Name it now if you can.")


def closer_shown(image: int, final: bool) -> str:
    """What the newest image is, after a closer look was asked for."""
    return (f"\n\nImage {image} is the closer look you asked for: the region boxed in cyan in image {image - 1}, cut "
            f"from the full-resolution camera frame and enlarged."
            + (" No more closer looks: name it now." if final else ""))


async def name_closer(ask, image, view_jpeg: bytes, close: Crop, prompt: str, rounds: int = CLOSER_ROUNDS):
    """Naming with successive close-ups (option CLOSEUP): the model sees the view and the close-up
    and either names the thing or asks for a part of the newest close-up closer; the app cuts that
    from the full-resolution frame, enlarges it and asks again, at most `rounds` calls in all.
    `ask(images, prompt)` is the vision call. Returns (the last reply, the last result, a record
    per round: {"ms", "closer": frame box or None}, the close-up JPEGs shown)."""
    shown = [enlarged(image, close)]
    regions = [close]
    steps, result = [], None
    for n in range(1, rounds + 1):
        last = n == rounds
        if n == 1:
            images, text = [view_jpeg, shown[0]], prompt + ("" if last else closer_offer(rounds - n, 2))
        else:
            # The view, the previous close-up with the asked-for part boxed, and that part enlarged.
            images = [view_jpeg, enlarged(image, regions[-2], regions[-1]), shown[-1]]
            text = prompt + closer_shown(3, last) + ("" if last else closer_offer(rounds - n, 3))
        result = await ask(images, text)
        data = result.data or {}
        box = data.get("closer")
        wanted = closer_box(box, regions[-1], image.shape) if isinstance(box, list) and len(box) == 4 and not last else None
        steps.append({"ms": round(result.latency_ms), "closer": None if wanted is None else
                      [wanted.x, wanted.y, wanted.x + wanted.width, wanted.y + wanted.height]})
        if wanted is None:
            return data, result, steps, shown
        regions.append(wanted)
        shown.append(enlarged(image, wanted))
    return (result.data or {}) if result is not None else {}, result, steps, shown


def answer_closer_offer(left: int) -> str:
    """Lets the model look closer before it answers (option CLOSEUP "naming+answers"; tests/golden/)."""
    return (f"\n\nCloser look: if the question is about something too small to see well in image 1, you may first "
            f"ask to see part of image 1 closer: reply only {{\"closer\": [ymin, xmin, ymax, xmax]}}, a box on a 0-1000 "
            f"scale of image 1. The app cuts it from the full-resolution camera frame, enlarges it (that adds no detail "
            f"the camera did not capture) and asks you again. {left} closer look{'s' if left != 1 else ''} left. "
            f"Answer now if you can.")


def answer_closer_shown(final: bool) -> str:
    return ("\n\nImage 2 is the closer look you asked for, cut from the full-resolution camera frame and enlarged. "
            "Every box in your reply is still on image 1 (the whole view), never on image 2."
            + (" No more closer looks: answer now." if final else ""))


async def answer_closer(ask, image, view_jpeg: bytes, crop: Crop, prompt: str, rounds: int = CLOSER_ROUNDS):
    """Answering with closer looks: the model may ask for a box of image 1 (the eyepiece crop) closer;
    the app cuts it from the full-resolution frame, enlarges it and asks again (at most `rounds` calls).
    Returns (the last reply, the last result, a record per round)."""
    steps, result, closer = [], None, None
    for n in range(1, rounds + 1):
        last = n == rounds
        if closer is None:
            images, text = [view_jpeg], prompt + ("" if last else answer_closer_offer(rounds - n))
        else:
            images = [view_jpeg, enlarged(image, closer)]
            text = prompt + answer_closer_shown(last) + ("" if last else answer_closer_offer(rounds - n))
        result = await ask(images, text)
        data = result.data or {}
        box = data.get("closer")
        wanted = closer_box(box, crop, image.shape) if isinstance(box, list) and len(box) == 4 and not last else None
        steps.append({"ms": round(result.latency_ms), "closer": None if wanted is None else
                      [wanted.x, wanted.y, wanted.x + wanted.width, wanted.y + wanted.height]})
        if wanted is None:
            return data, result, steps
        closer = wanted
    return (result.data or {}) if result is not None else {}, result, steps



def key_needed(option_id: str | None):
    """A vision option asked without its provider's key: what the person is told (a ToolFailed: the model, the key it
    needs, and where to add it), else None. Asked without one, the client's own KeyError reached the studio as "something
    went wrong in the app" (2026-10-25 review: every first question on an instance with no keys)."""
    option = VISION.get(option_id or "")
    if option is None or not option.needs or os.environ.get(option.needs):
        return None
    from gpu_worker.manifests import services

    from .brain.registry import ToolFailed
    from .key_admin import _short

    service = next((s for s in services().values() if s.key == option.needs), None)
    return ToolFailed(f"{option.label} needs your {_short(service.name) if service else option.needs} key",
                      next="add it in Models › Keys, then ask again")

class Agent:
    """Uses whichever vision model and pointing strategy are selected in hub.settings, so
    they can be switched live and compared."""

    def __init__(self, hub: Hub, conversation: Conversation | None = None, interaction_log: Path | None = None,
                 tags=None) -> None:
        self.hub = hub
        self.tags = tags  # () -> {"day", "thread"}: where each logged interaction happened
        # A test stack (services/sim/harness.py, an isolated preview) keeps its own, so the real log
        # holds only real use (the first live session's log mixed in test runs).
        self.interaction_log = Path(interaction_log) if interaction_log else Paths.from_env().interaction_log
        self._visions: dict[str, object] = {}
        self.conversation = conversation  # the brain's (a stand-alone agent, e.g. a bench, has none)
        self.taught = None  # taught.TaughtLabels: the labels the user taught (app.py); None: none here
        self.seen = None  # seen.SeenToday: the outlines named earlier today (app.py); None: none here
        self.masks = None  # masks.Masker: the settled view's automatic masks (pointing "masks"; app.py)
        self.molmo = None  # molmo.MolmoPointer: Molmo 2 points at what was asked (pointing "molmo"; app.py)
        self.dust = None  # dust.DustMap: spots that stay put while the slide moves (app.py); None: none here
        self.zeroshot = None  # zeroshot.ZeroShotHint: MedSigLIP's ranking of candidate names (app.py); None: none here
        # Called after each record is written, and each picture kept: a dataset being collected
        # takes its copy here (datasets/collector.py). Modules hold `log` itself, so the taps are inside.
        self.taps: list = []  # (record) -> None
        self.image_taps: list = []  # (path, path relative to the logs folder) -> None
        from .brain.llm import FunctionCallingBrain

        self.fc = FunctionCallingBrain()  # brain mode "fc": the model calls the tools itself

    def available(self, option_id: str | None = None) -> bool:
        if getattr(self.hub, "local_only", lambda: False)():
            return False
        option = VISION[option_id or self.hub.settings.vision]
        return bool(os.environ.get(option.needs)) if option.needs else True

    def vision(self, option_id: str):
        sampling = getattr(self.hub.settings, "sampling", "temperature-0.2")  # how Gemini samples (SAMPLING)
        key = option_id if sampling == "temperature-0.2" else f"{option_id}|{sampling}"
        if key not in self._visions:
            failed = key_needed(option_id)
            if failed is not None:
                raise failed
            self._visions[key] = make_vision(VISION[option_id].spec,
                                             temperature=None if sampling == "provider-default" else 0.2)
        return self._visions[key]

    def log(self, record: dict) -> None:
        """Every interaction with the options that produced it: the A/B record. On the event loop the line is handed to
        the writer thread (looplag.LINES), in order; the taps get it at once."""
        tags = {}
        if self.tags is not None:
            try:
                tags = self.tags()  # the day and conversation it happened in (day/store.py)
            except Exception:
                tags = {}
        now = time.time()  # "t": the same moment to the millisecond, so timings can be joined across logs (benches)
        entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)), "t": round(now, 3), **tags, **record}
        from .looplag import LINES

        LINES.append(self.interaction_log, json.dumps(entry))
        for tap in list(self.taps):
            try:
                tap(entry)
            except Exception:
                pass

    def keep_image(self, kind: str, jpeg: bytes, frame_id: int | None, object_id: str | None = None) -> str | None:
        """Keep the picture a model was shown, beside the logs (logs/crops/<day>/), so a log
        line can be joined back to it: naming benches, exported corrections. Returns its path
        relative to the logs folder, or None if it could not be written (never fails a request)."""
        try:
            folder = self.interaction_log.parent / "crops" / time.strftime("%Y-%m-%d")
            folder.mkdir(parents=True, exist_ok=True)
            stamp = f"{time.strftime('%H%M%S')}{int(time.time() * 1000) % 1000:03d}"
            name = f"{stamp}-{kind}-f{frame_id}{f'-{object_id}' if object_id else ''}.jpg"
            (folder / name).write_bytes(jpeg)
            relative = str((folder / name).relative_to(self.interaction_log.parent))
        except OSError:
            return None
        for tap in list(self.image_taps):
            try:
                tap(folder / name, relative)
            except Exception:
                pass
        return relative

    def keep_view(self, kind: str, image_bgr, crop: Crop, frame_id: int | None) -> str | None:
        """keep_image() for a decoded view: a small copy of the eyepiece crop."""
        jpeg, _ = encode_crop(image_bgr, crop, max_side=512, jpeg_quality=80)
        return self.keep_image(kind, jpeg, frame_id)

    # --- context ---------------------------------------------------------------------
    def _context(self, info=None, taught: str = "", seen: str = "", dust: str = "", zeroshot: str = "") -> str:
        from .ticket import current
        from . import scientific_ask
        capture = getattr(current(), 'scientific_input', None)
        if capture is not None:
            import json
            return scientific_ask.notes(capture) + '\nImage 1 manifest: ' + json.dumps(scientific_ask.facts(capture))
        reading = self.hub.scale_reading() if hasattr(self.hub, "scale_reading") else None
        view = measured(info) if info is not None and getattr(self.hub.settings, "quality", "") == "tell" else ""
        return identify_context(self.hub.scene.context.get("specimen"), reading.words if reading else "", view, taught, seen,
                                dust, zeroshot)

    def identify_prompt(self, info=None, taught: str = "", seen: str = "", dust: str = "", zeroshot: str = "") -> str:
        """What the vision model is told when naming a clicked outline (tests/golden/); with the
        picture's `info`, what the app measured on it (QUALITY "tell"); with `taught`, how close
        it is to the labels the user taught (taught.context_line); with `seen`, the outline named
        earlier today most like it (seen.context_line); with `dust`, the spots that stay put while
        the slide moves (dust_line); with `zeroshot`, MedSigLIP's ranking of candidate names
        (zeroshot.context_line)."""
        return IDENTIFY.format(context=self._context(info, taught, seen, dust, zeroshot))

    # --- dust on the optics (orchestrator/dust.py, option DUST) ------------------------------
    def dust_spots(self) -> list:
        """The measured dust spots, when DUST is not "off" (else none)."""
        dust = self.dust
        if dust is None or getattr(self.hub.settings, "dust", "off") == "off" or dust.ref is None:
            return []
        return list(dust.spots)

    def dust_line(self, info, crop: Crop, outlined=None) -> str:
        """Where spots stay put while the slide moves, as one line of data with boxes on image 1
        (`crop`), and, naming a click, whether the outlined thing is at one; "" when none are known."""
        spots = self.dust_spots()
        return dustmod.line(spots, self.dust.ref, crop, outlined) if spots else ""

    def dust_wire(self, info=None) -> dict | None:
        return self.dust.wire() if self.dust_spots() else None

    def on_dust(self, box) -> bool:
        """`box` (frame pixels) is about a dust spot (dust.spots_on)."""
        spots = self.dust_spots()
        return bool(spots and dustmod.spots_on(spots, self.dust.ref, box))

    async def taught_match(self, object_id: str):
        """The outline's match against the labels the user taught (taught.py), when TAUGHT is on
        and a name has enough examples; None otherwise, or when it is not ready in time."""
        if self.taught is None:
            return None
        try:
            return await asyncio.wait_for(asyncio.shield(self.taught.match_object(object_id)), TAUGHT_WAIT_S)
        except Exception as exc:
            self.log({"kind": "taught-match", "objectId": object_id, "error": f"{type(exc).__name__}: {exc}"[:200]})
            return None

    async def seen_match(self, object_id: str):
        """The outline named earlier today most like this one (seen.py), when SEEN is on and one is
        alike enough; None otherwise, or when it is not ready in time."""
        if self.seen is None:
            return None
        try:
            return await asyncio.wait_for(self.seen.match(object_id), TAUGHT_WAIT_S)
        except Exception as exc:
            self.log({"kind": "seen-match", "objectId": object_id, "error": f"{type(exc).__name__}: {exc}"[:200]})
            return None

    async def zeroshot_rank(self, object_id: str, image, bbox, frame_id: int | None = None):
        """MedSigLIP's ranking of the subject's candidate names for the thing (zeroshot.py), when
        ZEROSHOT is on and a list fits the subject; None otherwise, or when it is not ready in time."""
        if self.zeroshot is None:
            return None
        try:
            return await asyncio.wait_for(self.zeroshot.rank_crop(image, bbox, object_id, frame_id), ZEROSHOT_WAIT_S)
        except Exception as exc:
            self.log({"kind": "zeroshot", "objectId": object_id, "error": f"{type(exc).__name__}: {exc}"[:200]})
            return None

    def _scene_summary(self) -> str:
        from .ticket import current
        if getattr(current(), 'scientific_input', None) is not None:
            return 'No live outlines apply to this saved input.'
        labels = [o.label for o in self.hub.scene.objects.values() if o.kind == "highlight" and o.label]
        return ", ".join(labels[:20]) or "nothing yet"

    def _history_text(self) -> str:
        return self.conversation.text() if self.conversation is not None else "(none)"

    def snapshot(self, frame_id: int | None = None):
        """The view the question is about, as (capture, image, crop): the one shown when it was
        asked if it is still held, otherwise the best of the last second. The capture
        (capture.py) stands in for the frame's facts and is held by the request's ticket, so
        everything the request does later works on this view (ADR 0010)."""
        from .capture import captures
        from . import ticket as tickets

        store = captures(self.hub)
        capture = store.get(frame_id) if frame_id is not None else None
        if capture is None:
            # No frame named (a spoken request): the best of the last second, not just the newest.
            best = getattr(self.hub, "best_frame", None)
            info = (best(1.0) if best is not None else None) or self.hub.latest_info
            capture = store.get(info.id) if info is not None else None
        if capture is None or not capture.jpeg or not self.hub.source.is_fresh():
            from .frames import no_picture
            why, next_step = no_picture(self.hub, "there is no fresh picture from the camera to look at",
                                         "check the camera is connected and the view is live, then ask again")
            raise tools.ToolError(why, next=next_step)
        request = tickets.current()
        if request is not None:
            request.hold(capture, "analysis", "look")
        image = capture.bgr()
        crop = fov_crop(capture.fov, image.shape[1], image.shape[0])
        return capture, image, crop

    # --- answering -------------------------------------------------------------------
    async def compare_result(self, task) -> dict | None:
        try:
            data, used = await task
        except Exception as exc:
            return {"error": str(exc)}
        answer = str(data.get("answer") or "").strip()
        labels = [str(t.get("label")) for t in (data.get("targets") or []) if isinstance(t, dict) and t.get("label")]
        self.log({"kind": "ask-compare", "vision": used["option"], "thinkMs": round(used["latency_ms"]), "answer": answer, "labels": labels})
        return {"answer": answer, "labels": labels, "model": VISION[used["option"]].label, "thinkMs": round(used["latency_ms"])}

    @execution.traced("look")
    async def look(self, question: str, view, *, option: str, pointing: str = "box",
                   notes: tuple[str, str, str] | None = None, mode: str = "json-actions", registry=None,
                   learner: str = "") -> tuple[dict, dict]:
        """One vision call on `view` (a snapshot): the answer and targets, plus actions when
        `notes` = (tool list, scene table, selected) is given (the brain). mode "fc": the same
        with the model's own function calling (`registry` gives the tools). `learner`: a level's
        line (learner.py) for a call without the brain's notes, which carry their own (the
        arena's level arms). Returns (data, {"option", "model", "latency_ms", "marks"})."""
        info, image, crop = view
        native = (getattr(info, 'source_image', None) or {}).get('nativeSource')
        if native:
            pointing = 'box'  # live masks/marks are not evidence about a saved image
        execution.note(selected=option, selection="resolved-option", frameId=info.id, policy=pointing)
        if mode == "fc" and notes is not None:
            from .prompts import BRAIN_FC, OBSERVATION_BASIS

            jpeg = info.analysis_bytes if native else encode_crop(image, crop)[0]
            tool_list, table, selected = notes
            prompt = BRAIN_FC.format(context=self._context(), selected=selected, scene=table,
                                     history=self._history_text(), question=question)
            # This is the existing scoped-context policy marker, not question/intent routing.
            if OBSERVATION_BASIS in table:
                prompt += ('\nFor this scoped reply, FIRST write a JSON text object with answer, '
                           'observation_scope and input_refs, THEN call any tools in their action order. '
                           'Keep the friendly answer inside answer; function calls carry actions, not the text object.')
            data, used = await self.fc.ask(VISION[option].spec, jpeg, prompt, registry)
            return data, {"option": option, "marks": None, **used}
        return await self._ask_model(option, pointing, question, info, image, crop, notes=notes, learner=learner)

    async def _ask_model(self, option_id: str, pointing: str, question: str, info, image, crop,
                         notes: tuple[str, str, str] | None = None, learner: str = "") -> tuple[dict, dict]:
        from .brain.dispatch import AnswerProvider, PLANNER_OUTPUT_TOKENS, in_answer_phase

        vision = AnswerProvider(self.vision(option_id), option_id, VISION[option_id].spec.partition("@")[2],
                                max_output_tokens=PLANNER_OUTPUT_TOKENS if notes is not None else None)
        native = (getattr(info, 'source_image', None) or {}).get('nativeSource')
        jpeg = info.analysis_bytes if native else encode_crop(image, crop)[0]
        context = "\n".join(part for part in (self._context(dust='' if native else self.dust_line(info, crop)), learner) if part)
        fields = dict(context=context, scene=self._scene_summary(), history=self._history_text(), question=question)
        marks = None
        regions = None
        if pointing == "masks":
            # The settled view's own masks, numbered (orchestrator/masks.py); none ready: a box look.
            regions = self.masks.for_view(info) if self.masks is not None else None
            if not regions:
                self.log({"kind": "masks-none", "frameId": info.id,
                          "why": "no masker" if self.masks is None else "no masks for this view yet"})
        template = ASK_MARKS if pointing == "marks" else ASK_MASKS if regions else ASK
        if notes is not None:  # (named so it never hides the tools module, whose ToolError is raised below)
            template = with_tools(template, *notes)
        if pointing == "marks":
            marks = render_marks(image, self.hub.candidates_in(info.id), crop)
            result = await vision.ask([jpeg, marks.jpeg], template.format(**fields))
        elif pointing == "molmo":
            # The model answers and names what it means, as "box"; beside it, Molmo 2 points at what the
            # user's words are about, and its points are what is outlined (orchestrator/molmo.py).
            pointed = asyncio.create_task(self._molmo_points(question, (info, image, crop)))
            result = await vision.ask([jpeg], template.format(**fields))
            reading = await pointed
            if result.data is not None:
                result.data = self._molmo_targets(result.data, reading, info, crop, question)
        elif regions:
            _clean, marked, _scale = await asyncio.to_thread(render_masks, image, crop, regions)
            made_on = self.masks.current.frame_id if self.masks.current is not None else -1
            made_ms = self.masks.current.ms if self.masks.current is not None else 0.0
            marks = RegionMarks(marked, crop, dict(enumerate(regions, start=1)), info.id, info.epoch, info.dx, info.dy, made_on,
                                made_ms)
            result = await vision.ask([jpeg, marked], template.format(**fields))
        elif not native and getattr(self.hub.settings, "closeup", "single") == "naming+answers":
            # The model may ask to see part of the view closer before it answers (option CLOSEUP).
            from .ticket import current
            request = current()
            budget = request.admission if request is not None and in_answer_phase() else None
            rounds = min(CLOSER_ROUNDS, max(1, budget.remaining)) if budget is not None else CLOSER_ROUNDS
            data, result, steps = await answer_closer(vision.ask, image, jpeg, crop, template.format(**fields), rounds=rounds)
            if not data:
                # A refused call (local-only example, monthly cap, no connection) is said in its own words (row 23).
                raise not_answered(VISION[option_id].label, result)
            return data, {"option": option_id, "model": result.model, "latency_ms": sum(st["ms"] for st in steps),
                          "marks": None, "rounds": len(steps), "closer": [st["closer"] for st in steps if st["closer"]]}
        else:
            result = await vision.ask([jpeg], template.format(**fields))
        if result.data is None:
            # A refused call (local-only example, monthly cap, no connection) is said in its own words (row 23).
            raise not_answered(VISION[option_id].label, result)
        return result.data, {"option": option_id, "model": result.model, "latency_ms": result.latency_ms, "marks": marks}

    async def _molmo_points(self, question: str, view) -> dict | None:
        """Molmo's points for the user's words (molmo.MolmoPointer.point), or None when it has none to
        give (no Molmo here, not loaded, failed: logged)."""
        if self.molmo is None:
            self.log({"kind": "molmo-none", "why": "no Molmo in this app", "question": question})
            return None
        try:
            return await self.molmo.point(question, view)
        except Exception as exc:
            self.log({"kind": "molmo-none", "why": f"{type(exc).__name__}: {exc}"[:200], "question": question})
            return None

    def _molmo_targets(self, data: dict, reading: dict | None, info, crop: Crop, question: str) -> dict:
        """The look's reply with Molmo's points as its targets (molmo.assign): the model says whether
        and what, Molmo where. No points: the model's boxes stay (logged "molmo-none")."""
        targets = [t for t in (data.get("targets") or []) if isinstance(t, dict)]
        points = (reading or {}).get("points") or []
        record = {"kind": "molmo-point", "frameId": info.id, "question": question,
                  "query": (reading or {}).get("query"), "text": (reading or {}).get("text"),
                  "points": [[round(x), round(y)] for x, y in points], "modelTargets": len(targets),
                  "ms": round((reading or {}).get("ms") or 0), "ahead": (reading or {}).get("ahead"),
                  "read": (reading or {}).get("read")}
        if reading is None:
            return data
        if not targets:
            self.log({**record, "used": "none", "why": "the model pointed at nothing"})
            return data
        if not points:
            self.log({"kind": "molmo-none", **{k: v for k, v in record.items() if k != "kind"},
                      "why": "Molmo pointed at nothing; the model's boxes are outlined"})
            return data
        assigned = molmo_assign(points, targets, crop)
        self.log({**record, "used": "points", "inBox": [t["inBox"] for t in assigned],
                  "labels": [t["label"] for t in assigned]})
        return {**data, "targets": assigned, "modelTargets": targets}

    @execution.traced("point")
    async def point(self, targets: list[dict], frame_id: int, crop: Crop, marks=None, model_name: str = "",
                    trial: str | None = None, arm: str | None = None, errors: list[str] | None = None,
                    by_index: list | None = None, own: set[str] | frozenset[str] = frozenset()):
        """The look's targets as outlines (likely guesses, labelled by the model). `own`: the outlines this
        request's own steps made, which its look never replaces ("outline my nose and my mouth")."""
        # A new answer's highlights replace the previous answer's, so the view never piles
        # up stale pointers; things the user outlined by clicking stay. Arena trials manage
        # their own outlines (services/orchestrator/arena.py).
        from .capture import Capture, captures

        kept: dict[int, object] = {}
        # The look's own view (a Capture: its frame's facts and pixels, held by the request), or
        # a frame id while one is still held.
        view = frame_id if isinstance(frame_id, Capture) else captures(self.hub).get(frame_id)
        frame_id = view.id if view is not None else frame_id
        on = view if view is not None else frame_id
        execution.note(frameId=frame_id, trialId=trial, arm=arm)
        info = view if view is not None else self.hub.infos.get(frame_id)
        if trial is None:
            # A target that matches an outline already on screen keeps that outline (same id,
            # so a selection, the magnifier and anything attached to it stay with it); the
            # previous answer's other outlines are replaced.
            previous = [o for o in self.hub.scene.objects.values()
                        if o.kind == "highlight" and o.source.startswith("ai") and o.trial is None and o.status != "looking"]
            remade: dict[int, str] = {}  # target -> the same thing's outline another model made: made again by the choice
            if info is not None:
                for i, target in enumerate(targets):
                    if marks is not None and not isinstance(marks, RegionMarks) and isinstance(target.get("mark"), int):
                        continue  # a candidate spot: outlined afresh
                    tb = target_box(target, crop, marks)
                    if tb is None:
                        continue
                    best = max(previous, key=lambda o: _iou(tb, _box_in(o, info)), default=None)
                    # Under a request's choice of model (`use`), an outline another model made is made
                    # again with the choice and replaces it (ADR 0011; scaffolding review, M1.4 item 2).
                    if best is not None and best not in kept.values() and _same_thing(tb, _box_in(best, info)):
                        if reusable("outline", best.made_by):
                            kept[i] = best
                        else:
                            remade[i] = best.id
        # The previous answer's outlines go only once something new is outlined: a failed
        # outline must not leave the view empty while the answer talks about it.
        replaced = [o for o in previous if o not in kept.values() and o.id not in own] if trial is None else []
        if replaced and info is not None:
            # An outline a new target lies inside is what the user is pointing within ("the
            # nucleus in the highlight"): it stays.
            boxes = [b for b in (target_box(t, crop, marks) for t in targets) if b is not None]
            replaced = [o for o in replaced if o.id in remade.values() or not any(_contains(_box_in(o, info), b) for b in boxes)]
        group = self.hub.scene.new_id("a")

        source = "ai:" + (model_name or self.hub.settings.vision)
        tags = {"trial": trial, "arm": arm}

        @execution.traced("target")
        async def one(i: int, target: dict):
            label = str(target.get("label") or "").strip()
            if i in kept:
                execution.bind(object_id=kept[i].id)
                execution.finish("reused", adapter="orchestrator.agent.Agent.point", provider="app",
                                 cache="existing-outline; original producer may be expired")
                conf = target.get("confidence")
                confidence = float(conf) if isinstance(conf, (int, float)) else kept[i].confidence
                from .apply import apply

                if kept[i].status == "confirmed":
                    # The user named it (or agreed to its name): the model's word never replaces theirs.
                    changes = {"group": group}
                else:
                    changes = {"label": label or kept[i].label, "group": group, "confidence": confidence,
                               "status": status_for(confidence)}
                    if label and label != kept[i].label:
                        # How it is named now (known.py): the look's rename of one named before, or its own name.
                        from .known import look_record, renamed

                        changes["naming"] = (renamed(kept[i], "look", label) if kept[i].naming
                                             else look_record(label=label, model=source[3:], confidence=confidence))
                done = await apply(self.hub, lambda _: self.hub.scene.update(kept[i].id, **changes), scope="outline:app",
                                   target=(kept[i].id, None))
                if not done.ok and errors is not None:
                    errors.append(f"{label or 'target'}: {done.reason}")
                return done.value
            mark, box = target.get("mark"), target.get("box_2d")
            point = target.get("point")
            has_box = isinstance(box, list) and len(box) == 4
            use_mask = isinstance(marks, RegionMarks) and isinstance(mark, int) and mark in marks.regions \
                and reusable("outline", {"implementation": "edgetam-automask"})  # a chosen model outlines the box instead
            if use_mask and has_box and target_box({"box_2d": box}, crop) is not None \
                    and not agrees(marks.box(mark), target_box({"box_2d": box}, crop)):
                # The number the model named is not the thing it boxed (grounding bench: a nearby
                # outline named for a speck that had none): its own box is outlined instead.
                use_mask = False
                from .brain.intent import log_override

                log_override(self.log, "mask-disagrees-with-box", mark=mark, label=label, frameId=frame_id)
            try:
                if use_mask:
                    # Pointing "masks": the number is an outline already; no second segmentation.
                    obj = await self._outline_mask(marks, mark, on, source, label, tags)
                elif isinstance(point, list) and len(point) == 2:
                    # Pointing "molmo": Molmo's point, outlined as a click is.
                    obj = await tools.outline_at(self.hub, on, float(point[0]), float(point[1]), source=source,
                                                 label=label, status="likely", **tags)
                elif isinstance(marks, RegionMarks) and isinstance(mark, int) and not has_box and mark in marks.regions \
                        and marks.box(mark) is not None:
                    # A numbered mask the request's chosen model must make again: its box, outlined by the choice.
                    x0, y0, x1, y1 = marks.box(mark)
                    obj = await tools.outline_at(self.hub, on, (x0 + x1) / 2, (y0 + y1) / 2, box=(x0, y0, x1, y1),
                                                 source=source, label=label, status="likely", **tags)
                elif isinstance(marks, RegionMarks) and isinstance(mark, int) and not has_box:
                    raise tools.ToolError(f"there is no outline numbered {mark}",
                                          next=None)  # no next: the model's own call; it corrects it
                elif marks is not None and not isinstance(marks, RegionMarks) and isinstance(mark, int) and mark in marks.marks:
                    cand = marks.marks[mark]
                    obj = await tools.outline_at(self.hub, on, cand.x, cand.y, source=source, label=label, status="likely", **tags)
                elif isinstance(box, list) and len(box) == 4:
                    x0, y0, x1, y1 = gemini_box_to_frame([float(v) for v in box], crop)
                    if getattr(self.hub.settings, "dust", "off") == "filter" and self.on_dust((x0, y0, x1, y1)):
                        # A code rule, a non-default option (DUST "filter"): it has to beat the model told.
                        self.log({"kind": "override", "rule": "dust-filter", "model": label,
                                  "did": "not outlined: it lies on a spot that stays put while the slide moves",
                                  "box": [round(v) for v in (x0, y0, x1, y1)], "frameId": frame_id})
                        return None
                    obj = await tools.outline_at(
                        self.hub, on, (x0 + x1) / 2, (y0 + y1) / 2,
                        box=(x0, y0, x1, y1), source=source, label=label, status="likely", **tags,
                    )
                else:
                    return None
            except tools.ToolError as exc:
                execution.finish("failed", errorType=type(exc).__name__)
                if errors is not None:
                    errors.append(f"{label or 'target'}: {exc.said()}")
                return None
            conf = target.get("confidence")
            confidence = float(conf) if isinstance(conf, (int, float)) else None
            # Written in the same step as the outline (no wait between): it is the outline's own data.
            from .known import look_record

            self.hub.scene.update(obj.id, confidence=confidence, group=group,
                                  status=status_for(confidence) if label else obj.status,
                                  naming=look_record(label=label, model=source[3:], confidence=confidence) if label else obj.naming)
            return obj

        outcomes = await asyncio.gather(*(one(i, t) for i, t in enumerate(targets)))
        results = [o for o in outcomes if o is not None]
        if by_index is not None:  # each target by its number, None where it could not be outlined
            by_index[:] = [o.id if o is not None else None for o in outcomes]
        # An outline being made again by the request's choice goes only once its own remake is there: a
        # remake that failed leaves it as it was (the failure is said), whatever the other targets did.
        kept_as_was = {old for i, old in (remade if trial is None else {}).items() if outcomes[i] is None}
        replaced = [o for o in replaced if o.id not in kept_as_was]
        if results and replaced:
            from . import ticket as tickets

            request = tickets.current()
            if request is None or not request.superseded("outline:app"):
                # This answer replaces the previous answer's outlines: requests still running from
                # before it may no longer add theirs (their tickets are superseded), this one may.
                self.hub.scene.generations.bump("outline:app", why="a newer answer replaced the app's outlines",
                                                by=request)
                for obj in replaced:
                    self.hub.scene.remove(obj.id)
        return results

    @execution.traced("reuse-mask")
    async def _outline_mask(self, marks: RegionMarks, number: int, frame_id, source: str, label: str, tags: dict):
        """A numbered automatic mask as an outline, on the look's view (a Capture) if it is still
        held, otherwise on the newest one (moved with the slide)."""
        from .capture import Capture, captures

        store = captures(self.hub)
        info = frame_id if isinstance(frame_id, Capture) else store.get(frame_id)
        if info is None:
            latest = self.hub.latest_info
            info = store.get(latest.id) if latest is not None else None
        polygons = marks.polygons_in(number, info) if info is not None else None
        if not polygons or info is None:
            raise tools.ToolError("the slide moved too far before it could be outlined",
                                  next="hold the slide still and click the thing again")
        region = marks.regions[number]
        execution.finish("reused", adapter="orchestrator.agent.Agent._outline_mask", cache="previous-automask",
                         policy="no-second-segmentation", frameId=frame_id,
                         model="edgetam", provider="local", identity="cached fixed automask route; original revision unverified")
        obj = await tools.add_outline(self.hub, info, polygons, source=source, label=label, status="likely",
                                      note=f"automatic mask {number} (score {region.get('score', 0):.2f}), no second outline",
                                      **tags)
        # The background mask set this request used becomes evidence (contract 8), its mask linked to the outline.
        from .evidence import evidence_of
        from .population import from_maskset

        try:
            population, by_number = from_maskset(self.hub, marks)
            if population is not None and number in by_number:
                evidence_of(self.hub).link(population, "same-object", by_number[number], obj.id, "outlined from this mask")
        except Exception as exc:  # keeping it must never cost the outline
            self.log({"kind": "evidence-not-kept", "from": "automask", "error": f"{type(exc).__name__}: {exc}"[:200]})
        return obj

    @execution.traced("look-in")
    async def look_in(self, prompt: str, view, region: tuple[float, float, float, float] | None = None,
                      option: str | None = None) -> tuple[dict, dict]:
        """One vision call that boxes things: in the whole view, or in `region` (frame pixels),
        given as the view with the region outlined plus an enlarged close-up of it (magnifying
        while keeping the whole view beside it beats a crop alone: arXiv 2503.10183). Targets
        come back with "box" in frame pixels."""
        info, image, crop = view
        option = option or self.hub.settings.vision
        execution.note(selected=option, frameId=info.id)
        if region is None:
            jpeg, _ = encode_crop(image, crop)
            images, on = [jpeg], crop
        else:
            x0, y0, x1, y1 = (int(round(v)) for v in region)
            on = Crop(x0, y0, max(1, x1 - x0), max(1, y1 - y0))
            marked = image.copy()
            cv2.rectangle(marked, (x0, y0), (x1, y1), (163, 79, 255), 3, cv2.LINE_AA)
            whole, _ = encode_crop(marked, crop)
            close = image[on.y:on.y + on.height, on.x:on.x + on.width]
            grow = max(1.0, 512 / max(on.width, on.height))  # small regions are enlarged
            if grow > 1.0:
                close = cv2.resize(close, None, fx=grow, fy=grow, interpolation=cv2.INTER_CUBIC)
            ok, enc = cv2.imencode(".jpg", close, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            images = [whole, enc.tobytes()]
        result = await self.vision(option).ask(images, prompt)
        data = dict(result.data or {})
        targets = []
        for t in data.get("targets") or []:
            box = t.get("box_2d") if isinstance(t, dict) else None
            if isinstance(box, list) and len(box) == 4:
                targets.append({**t, "box": gemini_box_to_frame([float(v) for v in box], on)})
        data["targets"] = targets
        return data, {"option": option, "model": result.model, "latency_ms": result.latency_ms}

    # --- identify --------------------------------------------------------------------
    @execution.traced("name")
    async def identify(self, object_id: str, quiet: bool = False) -> dict:
        """Name something the user outlined by clicking, or any outline the app drew (option NAMING, Will's decision of
        2026-10-12: one labeler). `quiet`: an outline the app drew keeps the name and state it was drawn with until the
        model answers (never "Looking..." meanwhile, so a highlight is never slower to read)."""
        obj = self.hub.scene.objects.get(object_id)
        execution.bind(object_id=object_id)
        execution.note(selected=self.hub.settings.vision, selection="settings")
        if obj is None:
            raise tools.ToolError("that outline is gone", next="click the thing again")
        from .apply import apply, next_for
        from .capture import captures

        # The view it was made on, and the outline as it was drawn there (re-anchoring and
        # following move `anchor_frame`; the origin stays): the request that made it holds it.
        origin = getattr(obj, "origin", None)
        origin = origin if origin is not None else obj.anchor_frame
        polygons = getattr(obj, "origin_polygons", None) or getattr(obj, "polygons", None)
        info = captures(self.hub).get(origin)
        if info is None:
            raise tools.ToolError("the view it was outlined on is no longer held",
                                  next="click it again to outline it on the view now")
        # Its ticket's scope (a spoken cancel or a clear stops it), and the revision it was asked
        # about: a name the user gave or confirmed meanwhile is never overwritten.
        scope, revision = tools.outline_scope(getattr(obj, "source", "click")), getattr(obj, "revision", 0)

        async def note(**changes):
            done = await apply(self.hub, lambda _: self.hub.scene.update(object_id, **changes),
                               scope=scope, target=(object_id, revision))
            if not done.ok:
                raise tools.ToolError(done.reason, next=next_for(done.reason, "click the thing again"))

        async def mark(status: str) -> None:  # the state while it is named, or when it can't be: a tap's, never a drawn one's
            if not quiet:
                await note(status=status)

        await mark("looking")
        frame_id = info.id
        bad = unnameable(getattr(info, "quality", ()))
        if bad and self.hub.settings.quality == "gate":
            # Naming needs a view that can show it (orchestrator/quality.py): say what is wrong.
            await mark("outline")
            self.log({"kind": "identify-skip", "objectId": object_id, "frameId": frame_id, "quality": list(bad)})
            raise tools.ToolError(f"can't tell yet: the view is {describe(bad)}; fix that, then click it again",
                                  next=None)  # no next: the message says what to do
        image = info.bgr()
        if image is None or not polygons:
            await mark("outline")
            raise tools.ToolError("the view it was outlined on is no longer held",
                                  next="click it again to outline it on the view now")
        crop = fov_crop(info.fov, image.shape[1], image.shape[0])
        pts = np.array([p for poly in polygons for p in poly], dtype=float)
        bbox = (float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max()))
        view_jpeg, close_jpeg = identify_images(image, polygons, crop, bbox)
        option = self.hub.settings.vision
        # The user's own examples and the most alike outline named earlier today, as data (taught.py,
        # seen.py; one embedding, waited for together): the model decides the name; code never sets it.
        # MedSigLIP's zero-shot ranking of the subject's candidate names, beside them (zeroshot.py, option
        # ZEROSHOT): data with what it has been worth, never the name.
        match, similar, ranking = await asyncio.gather(self.taught_match(object_id), self.seen_match(object_id),
                                                       self.zeroshot_rank(object_id, image, bbox, frame_id))
        earlier = self.seen.line(similar) if similar is not None else ""  # with its session when another (ADR 0017)
        taught, dust, zeroshot = taught_line(match), self.dust_line(info, crop, bbox), zeroshot_line(ranking)
        prompt = self.identify_prompt(info, taught, earlier, dust=dust, zeroshot=zeroshot)
        told = self._context(info, taught, earlier, dust, zeroshot)  # kept with the name (known.py)
        closer_steps: list[dict] = []
        if getattr(self.hub.settings, "closeup", "single") in ("naming", "naming+answers"):
            # The model may ask to see part of the close-up closer, cut from the full-resolution frame
            # (option CLOSEUP; at most CLOSER_ROUNDS calls).
            data, result, closer_steps, shown = await name_closer(self.vision(option).ask, image, view_jpeg,
                                                                  close_region(image, bbox), prompt)
            latency = sum(step["ms"] for step in closer_steps)
        else:
            result = await self.vision(option).ask([view_jpeg, close_jpeg], prompt)
            data, shown, latency = result.data or {}, [close_jpeg], result.latency_ms
        reading = identify_reading(data)
        label = reading["label"]
        answer = str(data.get("answer") or "").strip()
        confidence = reading["confidence"]
        if not label:
            await mark("outline")
            # A refused call (local-only example, monthly cap, no connection) is said in its own words (row 23).
            raise not_answered("the vision model", result)
        from .known import naming_record

        naming = naming_record(model=result.model or option, option=option, frame_id=frame_id, info=info, reading=reading,
                               told=told, closer=sum(1 for step in closer_steps if step.get("closer")))
        await note(label=label, status=reading["status"], seen=answer, confidence=confidence, maybe=reading["maybe"],
                   naming=naming)
        revision = self.hub.scene.objects[object_id].revision if object_id in self.hub.scene.objects else revision
        if self.conversation is not None:
            unsure = f" (not sure; maybe {' or '.join(reading['maybe'])})" if reading["status"] == "unsure" and reading["maybe"] else ""
            self.conversation.add("assistant", "chat", f"(about the outlined {label}{unsure}) {answer}", objectIds=[object_id])
        self.log({"kind": "identify", **self.hub.settings.to_wire(), "thinkMs": round(latency), "label": label, "answer": answer,
                  "specific": reading["specific"], "maybe": reading["maybe"], "observations": reading["observations"],
                  "status": reading["status"],
                  "quality": list(getattr(info, "quality", ())),
                  "objectId": object_id, "frameId": frame_id, "epoch": info.epoch,
                  "confidence": confidence,
                  "taught": match.wire() if match is not None else None,
                  "seenBefore": similar.wire() if similar is not None else None,
                  "zeroshot": ranking.wire() if ranking is not None else None,
                  "specimen": self.hub.scene.context.get("specimen"),
                  "view": self.keep_image("identify-view", view_jpeg, frame_id, object_id),
                  "close": self.keep_image("identify-close", close_jpeg, frame_id, object_id),
                  "rounds": len(closer_steps) or 1, "closer": [step["closer"] for step in closer_steps if step["closer"]],
                  "closerImages": [self.keep_image(f"identify-closer{i}", jpeg, frame_id, object_id)
                                   for i, jpeg in enumerate(shown[1:], start=1)],
                  "dust": self.dust_wire(info)})
        return {"answer": answer, "label": label, "objectId": object_id, "thinkMs": round(latency),
                "model": VISION[option].label}
