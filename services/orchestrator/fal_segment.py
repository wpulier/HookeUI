"""SAM 3.1 on fal's GPUs as an outline option (`sam3.1-fal`)."""
from __future__ import annotations

from research_worker import api_usage

import asyncio
import base64
import os
import time

import numpy as np
from . import execution
from .brain.registry import ToolFailed

ENDPOINT = "fal-ai/sam-3-1/image-rle"


def decode_rle(rle: str, width: int, height: int) -> np.ndarray:
    """fal's RLE: space-separated (start, length) pairs over the row-major pixel grid."""
    values = np.array(rle.split(), dtype=np.int64)
    mask = np.zeros(width * height, dtype=bool)
    for start, length in zip(values[0::2], values[1::2]):
        mask[start : start + length] = True
    return mask.reshape(height, width)


TIMEOUT = 30.0  # seconds: fal answers an outline in about 2 s warm; past this it is said, never waited on for ever
TOOK = f"{TIMEOUT:.0f} s"


class FalRefused(ToolFailed):
    """fal refused the call before running it, or it was never sent: nothing was billed (`ran` False). A ToolFailed, so
    the runner records it as never run (operations.not_run_of: "refused"). `outcome` is api_usage.OUTCOMES' word."""

    ran = False

    def __init__(self, message: str, next: str, *, outcome: str, status: int | None = None) -> None:
        super().__init__(message, next=next)
        self.outcome, self.status_code = outcome, status


class Unconfirmed(RuntimeError):
    """A call fal may have run and billed without saying so (a timeout, a dropped connection, a provider error): `ran`
    None. Deliberately not a ToolFailed, which the runner records as refused before running (operations.not_run_of):
    this one ran and failed, as its cost row says it may have been billed. Its name is the label its words are said
    with ("Unconfirmed: fal didn't answer…"); operations.RAW_ERROR rewrites only names ending in Error, Exception…"""

    ran = None

    def __init__(self, message: str, *, outcome: str, status: int | None = None) -> None:
        super().__init__(message)
        self.outcome, self.status_code = outcome, status


def _client():
    import httpx

    return httpx.Client(timeout=httpx.Timeout(TIMEOUT, connect=10.0))


def _detail(response) -> str:
    try:
        said = response.json().get("detail")
    except (ValueError, AttributeError):
        said = None
    words = " ".join(str(said).split())[:200] if isinstance(said, str) else ""
    key = os.environ.get("FAL_KEY")
    return f" (fal said: “{words.replace(key, '<key>') if key else words}”)" if words else ""


def _run(endpoint: str, arguments: dict) -> dict:
    """One call with the key in force now (a key replaced in the app, key_admin.py, is used at once), sent once.
    fal_client's own run() retried a timeout, a dropped connection, 408/409/429 and gateway errors up to ten times
    with no read timeout of its own, so a call fal had accepted could run, and be billed, again while the ledger counted
    one (F065): here nothing is retried underneath, and asking again is the user's new call. FAL_RUN_HOST, as fal_client
    reads it, moves the host (a test's stand-in)."""
    import httpx

    counted = _counted(endpoint)
    url = f"https://{os.environ.get('FAL_RUN_HOST', 'fal.run')}/{endpoint}"
    with _client() as client:
        try:
            response = client.post(url, json=arguments, headers={"Authorization": f"Key {os.environ.get('FAL_KEY', '')}"})
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:  # no connection was made: nothing was sent
            raise FalRefused("Couldn't reach fal (no connection): nothing was sent",
                             next="check the connection, then ask again", outcome="connection") from exc
        except httpx.TimeoutException as exc:
            raise Unconfirmed(f"fal didn't answer within {TOOK}. It may have run and been billed, so {counted}; "
                              "asking again sends a new call", outcome="cut-off") from exc
        except httpx.TransportError as exc:
            raise Unconfirmed(f"The connection to fal dropped after the request was sent. It may have run and been "
                              f"billed, so {counted}; asking again sends a new call", outcome="failed") from exc
    status = response.status_code
    if status in (401, 403):
        raise FalRefused(f"fal refused your key (HTTP {status}){_detail(response)}: it didn't run and nothing was billed",
                         next="replace your fal key in Models › Keys, then ask again", outcome="auth",
                         status=status)
    if status == 429:
        raise FalRefused("fal is limiting requests or your fal account is out of credit (HTTP 429): it didn't run and "
                         "nothing was billed", next="wait a minute and ask again, or check your balance on fal.ai",
                         outcome="rate-limit", status=status)
    if status >= 500:
        raise Unconfirmed(f"fal had an error (HTTP {status}){_detail(response)}. It may have run and been billed, so "
                          f"{counted}; asking again sends a new call", outcome="server", status=status)
    if status >= 400:
        raise Unconfirmed(f"fal didn't accept the request (HTTP {status}){_detail(response)}. It may have been billed, "
                          f"so {counted}", outcome="failed", status=status)
    return response.json()


def _counted(endpoint: str) -> str:
    """What a call that may have run counts toward the month, as the ledger counts it (api_usage.counted_unknown)."""
    usd, at_most = api_usage.counted_unknown(endpoint)
    return f"it is counted at its most (${usd:.2f})" if at_most else f"it is counted as an unknown cost (${usd:.2f})"


def _priced(area: str) -> None:
    """A call that returned: fal's list price for one call (api_usage.CALL_PRICES)."""
    api_usage.record(ENDPOINT, api_usage.call_price(ENDPOINT), provider="fal", area=area, basis=api_usage.call_basis(ENDPOINT))


@execution.traced("fal-segment")
async def segment_box(jpeg: bytes, box: tuple[float, float, float, float], width: int, height: int, text: str = "") -> dict:
    execution.note(adapter="orchestrator.fal_segment.segment_box", provider="fal", model="sam3.1", endpoint=ENDPOINT,
                   identity="endpoint; hosted revision unconfirmed", role="outline", optionId="sam3.1-fal")

    arguments = {
        "image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(),
        "box_prompts": [{"x_min": int(box[0]), "y_min": int(box[1]), "x_max": int(box[2]), "y_max": int(box[3])}],
        "include_scores": True,
        "apply_mask": False,
        "max_masks": 4,
    }
    if text:
        arguments["prompt"] = text
    t0 = time.perf_counter()
    async with api_usage.attempt(ENDPOINT, provider="fal", area="Segmentation"):
        result = await asyncio.to_thread(_run, ENDPOINT, arguments)
    _priced("Segmentation")
    ms = (time.perf_counter() - t0) * 1000
    rles = result.get("rle") or []
    rles = [rles] if isinstance(rles, str) else rles
    scores = result.get("scores") or []
    if not rles:
        execution.finish("empty")
        return {"mask": None, "score": 0.0, "ms": ms}
    best = int(np.argmax(scores)) if scores else 0
    return {"mask": decode_rle(rles[best], width, height), "score": float(scores[best]) if scores else 0.0, "ms": ms}


@execution.traced("fal-exemplar-matches")
async def segment_all(jpeg: bytes, width: int, height: int, box: tuple[float, float, float, float] | None = None,
                      text: str = "", max_masks: int = 32, min_score: float = 0.4) -> list[dict]:
    """Every instance SAM 3.1 finds: like the thing in `box` (an exemplar), and/or matching `text`
    ("find all like this one"). [{"mask", "score"}], best first; fal's limit is 32."""
    execution.note(adapter="orchestrator.fal_segment.segment_all", provider="fal", model="sam3.1", endpoint=ENDPOINT,
                   identity="endpoint; hosted revision unconfirmed", role="fixed:find-all")

    arguments = {"image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(), "include_scores": True,
                 "apply_mask": False, "max_masks": max_masks,
                 "return_multiple_masks": True}
    if box is not None:
        arguments["box_prompts"] = [{"x_min": int(box[0]), "y_min": int(box[1]), "x_max": int(box[2]), "y_max": int(box[3])}]
    if text:
        arguments["prompt"] = text
    async with api_usage.attempt(ENDPOINT, provider="fal", area="Segmentation"):
        result = await asyncio.to_thread(_run, ENDPOINT, arguments)
    _priced("Segmentation")
    rles = result.get("rle") or []
    rles = [rles] if isinstance(rles, str) else rles
    scores = result.get("scores") or [1.0] * len(rles)
    found = [{"mask": decode_rle(r, width, height), "score": float(s)} for r, s in zip(rles, scores) if float(s) >= min_score]
    if not found:
        execution.finish("empty")
    return sorted(found, key=lambda f: -f["score"])
