"""Acquire explicitly selected retained or dated camera pixels for an existing answer call."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import weakref
from contextvars import copy_context

from PIL import Image

from .brain.registry import ToolFailed
from .capture import RUN, captures
from .marks import encode_crop, fov_crop

MAX_BYTES = 10 * 1024 * 1024
MAX_EDGE = 768
CAMERA_EDGE = 1024


class PictureInput:
    """The input owns its memory lease for exactly as long as its pixels remain reachable."""
    def __init__(self, jpeg: bytes, facts: dict, reservation):
        self.jpeg, self.facts = jpeg, facts
        self._release = weakref.finalize(self, reservation.release)

    def __iter__(self):
        yield self.jpeg
        yield self.facts


def inspect_picture(hub, index, eid: str, number, day: str | None = None) -> PictureInput:
    entry = index.get(eid, day)
    if entry is None:
        raise ToolFailed(f'{eid} is not a retained image source', next='resolve its exact resource ID first')
    if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number < len(entry.pictures):
        raise ToolFailed(f'{eid} has no picture {number}', next='read its available picture references first')
    source = entry.picture_source(number)
    if source is None:
        raise ToolFailed(f'{eid} picture {number} has no retained source identity', next='read its available picture references first')
    version = source.get('version') if entry.kind == 'research' else entry.version
    memory = captures(hub).memory
    reserved = memory.reserve('analysis', MAX_BYTES + 1, f'read saved picture {eid}')
    try:
        found = index.picture(eid, number, entry.day, max_bytes=MAX_BYTES)
        if not found or not found[0] or len(found[0]) > MAX_BYTES:
            raise ToolFailed(f'{eid} picture {number} is unavailable or exceeds the 10 MiB inspection limit',
                             next='choose another retained picture')
        raw, _media = found
        try:
            with Image.open(io.BytesIO(raw)) as original:
                width, height = original.size
                reserved.grow(width * height * 12 + MAX_EDGE * MAX_EDGE * 8, f'decode saved picture {eid}')
                with original.convert('RGB') as converted:
                    converted.thumbnail((MAX_EDGE, MAX_EDGE))
                    output = io.BytesIO()
                    converted.save(output, format='JPEG', quality=85)
                    jpeg = output.getvalue()
                    shown = list(converted.size)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ToolFailed(f'{eid} picture {number} could not be decoded: {type(exc).__name__}', next='choose another picture') from None
        latest = index.get(eid, entry.day)
        if latest is None or latest.version != entry.version or latest.picture_source(number) != source:
            raise ToolFailed(f'{eid} changed while its picture was read', next='read the current saved version again')
        facts = {'channel': 'retained-source', 'id': entry.id, 'day': entry.day, 'version': version, 'picture': number,
                 'sha256': hashlib.sha256(raw).hexdigest(), 'source': source,
                 'originalSize': [width, height], 'inputSize': shown, 'inputSha256': hashlib.sha256(jpeg).hexdigest(),
                 'transform': 'RGB JPEG, longest edge at most 768 pixels; no invented content'}
        del raw, found, original, output
        reserved.settle(len(jpeg))
        return PictureInput(jpeg, facts, reserved)
    except BaseException:
        reserved.release()
        raise


def transport_bytes(size: int) -> int:
    """JPEG plus room for base64 bytes, text, URL and serialized transport copies."""
    return size + 4 * (4 * ((size + 2) // 3) + 64)


async def inspect_camera(hub, capture, *, timing: str) -> PictureInput:
    """Encode only the original held Capture; cancellation cannot stop its thread."""
    if not capture.jpeg or len(capture.jpeg) > MAX_BYTES:
        raise ToolFailed('the held camera view is unavailable or exceeds the 10 MiB inspection limit',
                         next='ask again on a smaller available camera view')
    width, height = int(capture.width), int(capture.height)
    crop = fov_crop(capture.fov, width, height)
    if min(width, height, crop.width, crop.height) <= 0:
        raise ToolFailed('the held camera view has no usable dimensions', next='ask again on an available view')
    scale = min(1.0, CAMERA_EDGE / max(crop.width, crop.height))
    shown = [round(crop.width * scale), round(crop.height * scale)]
    if min(shown) < 1:
        raise ToolFailed('the held camera crop is too thin to inspect', next='ask again on a usable view')
    pixels = shown[0] * shown[1]
    # Reserve the entire existing per-picture byte limit, including the encoder
    # array and bytes copy, rather than predicting compression from the scene.
    # The separate Capture hold covers full BGR decoding.
    encoded_bound = MAX_BYTES
    peak = 3 * crop.width * crop.height + 6 * pixels + encoded_bound + transport_bytes(encoded_bound)
    store = captures(hub)
    reserved = store.memory.reserve('analysis', peak, 'encode and transport held camera pixels')
    try:
        held = store.hold(capture, 'analysis', 'look', 'camera inspection worker')
    except BaseException:
        reserved.release()
        raise

    def encode():
        image = jpeg = None
        try:
            image = capture.bgr()  # the real Capture's shared decode authority
            if image is None or image.shape[:2] != (height, width):
                raise ToolFailed('the held camera view could not be decoded', next='ask again on an available view')
            jpeg, _ = encode_crop(image, crop, max_side=CAMERA_EDGE)
            if not jpeg or len(jpeg) > MAX_BYTES:
                raise ToolFailed('the encoded camera picture exceeds its inspection limit', next='ask again on a smaller view')
            facts = {'channel': 'specimen-camera', 'sha256': hashlib.sha256(capture.jpeg).hexdigest(),
                     'inputSha256': hashlib.sha256(jpeg).hexdigest(), 'originalSize': [width, height],
                     'inputSize': shown, 'crop': [crop.x, crop.y, crop.width, crop.height],
                     'source': {'run': RUN, 'boot': capture.boot, 'seq': capture.seq, 'frame': capture.id,
                                'epoch': capture.epoch, 'at': capture.wall_at},
                     'scale': capture.scale, 'timing': timing,
                     'transform': 'Original eyepiece crop, JPEG; longest edge at most 1024 pixels; no invented content'}
            return PictureInput(jpeg, facts, reserved)
        except BaseException as exc:
            # Completed encoder frames may otherwise retain large arrays through
            # an exception traceback even after its worker has returned.
            import traceback
            traceback.clear_frames(exc.__traceback__)
            exc.__traceback__ = None
            raise
        finally:
            image = jpeg = None

    try:
        worker = asyncio.get_running_loop().run_in_executor(None, copy_context().run, encode)
    except BaseException:
        held.release()
        reserved.release()
        raise

    def finished(done):
        # Runs only after the actual thread has returned and its temporaries are
        # gone. An abandoned PictureInput releases by reachability, never early.
        try:
            picture = done.result()
            reserved.settle(transport_bytes(len(picture.jpeg)))
        except BaseException:
            reserved.release()
        finally:
            held.release()

    worker.add_done_callback(finished)
    return await asyncio.shield(worker)


def image_context(images) -> str:
    return 'Supplied image inputs in order (channel/source facts, not interpretation):\n' + '\n'.join(
        json.dumps({'image': n, **facts}, default=str) for n, (_, facts) in enumerate(images, 1))
