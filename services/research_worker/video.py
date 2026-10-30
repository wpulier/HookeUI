"""The video model: a short illustration video made from the user's photo, for researchers (and the app's own check of it,
orchestrator/model_checks.py). One call, one place.

Gemini Omni 1.1 Flash (`gemini-omni-1.1-flash`, catalog/models/gemini-omni-flash) since 29 Oct 2026. Google stopped serving
Veo 3.1 Fast (`veo-3.1-fast-generate-preview`) on 22 Oct 2026 and names Gemini Omni 1.1 Flash its replacement
(https://ai.google.dev/gemini-api/docs/deprecations, updated 2026-10-22); the catalog records the retirement
(catalog/models/maintenance.toml). It is served through the Interactions API, not `generate_videos`
(https://ai.google.dev/gemini-api/docs/omni): the picture goes in as an image part with the words, and the video comes back
in the interaction itself (`output_video`, base64), or as a file to download when it is large. Its price is about $0.10 a
second of 720p (5,792 output tokens a second at $17.50 a million: https://ai.google.dev/gemini-api/docs/pricing), as Veo
3.1 Fast's was.
"""
from __future__ import annotations

import asyncio
import base64

MODEL = "gemini-omni-1.1-flash"
NAME = "Gemini Omni 1.1 Flash"  # its one name (catalog/models/gemini-omni-flash), said when it can't be used
ENTRY = "gemini-omni-flash"  # its catalog entry and its price's key (budget.PRICES)
SECONDS = (4, 6, 8)  # the lengths asked for (it makes 3 to 10 s)
TIMEOUT_S = 360.0  # one video takes about a minute; the call is held open while it is made


class NoVideo(RuntimeError):
    """The model answered without a video: its own errors (the interaction's `errors`), when it gave any."""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        first = errors[0] if errors else {}
        super().__init__(str(first.get("message") or "it answered with no video"))
        self.message = str(self)
        self.code = first.get("code") if isinstance(first.get("code"), str) else None


async def make(client, prompt: str, picture_png: bytes | None, seconds: int, *, resolution: str = "720p") -> bytes:
    """The video's bytes (MP4) for `prompt`, starting from `picture_png` when there is one, `seconds` long, 16:9. One
    attempt: a refusal or failure is the provider's own error (api_usage.refusal says why), NoVideo when it answered
    without one."""
    words = {"type": "text", "text": prompt}
    parts = ([{"type": "image", "data": base64.b64encode(picture_png).decode("ascii"), "mime_type": "image/png"}, words]
             if picture_png else prompt)
    interaction = await client.aio.interactions.create(
        model=MODEL, input=parts, timeout=TIMEOUT_S,
        response_format={"type": "video", "aspect_ratio": "16:9", "resolution": resolution, "duration": f"{seconds}s"},
        **({"generation_config": {"video_config": {"task": "image_to_video"}}} if picture_png else {}))
    video = getattr(interaction, "output_video", None)
    if video is not None and getattr(video, "data", None):
        return base64.b64decode(video.data)
    if video is not None and getattr(video, "uri", None):
        return await asyncio.to_thread(lambda: client.files.download(file=video.uri))
    raise NoVideo([{"code": getattr(e, "code", None), "message": getattr(e, "message", None)}
                   for e in getattr(interaction, "errors", None) or []])
