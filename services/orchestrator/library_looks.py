"""The library's looks (docs/design/2026-10-12-workspace-and-assets.md; CONTEXT.md "Category"): the app looks once at each
new asset and files it by what is in it, so All assets opens on categories, as a gallery sorted by what is in its
pictures.

- **One look each:** the fast vision model (the setting `fast`, else `vision`) is shown the asset's small picture (a
  recording's poster frame: thumbnails.py) with what is known about it (its record's specimen, what the running look saw
  while it was on stage in any session) as data, and picks one of the library's fixed top level (library.CATEGORIES)
  and names what it is below it ("Rotifers"). The model makes the judgment; nothing in code names a category.
- **Examples** are looked at once per specimen (its cover): some 30 looks for about 1,500 clips.
- **Never over the user:** an asset whose category the user set is never looked at.
- **Only when chosen:** the setting `library_looks` is "off" by default, so no picture leaves this Mac for it until the
  user turns it on (Will, 2026-10-13); `MICROMIND_LIBRARY_LOOKS=0` keeps it off whatever the setting says.
- **Never in the way:** one look at a time, at most LOOKS_PER_MIN a minute; it is a cloud call, and its picture is made
  at background priority (thumbnails.py), so the live view loses nothing. A look that fails is tried again only after
  RETRY_S. With no key for the fast model it waits, and the library says "Not sure yet".
- Every look is logged (`library-look`, with its model, what it was told and what it said).
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Callable

from .library import CATEGORIES

LOOKS_PER_MIN = 12  # at most this many looks a minute
IDLE_S = 20.0  # how often the library is read for what still waits, when nothing did
RETRY_S = 600.0  # a look that failed is tried again only after this
LOOK_TOKENS = 600  # its reply: one category and a name (thinking included)


def prompt(record_specimen: str, seen: list[str], title: str) -> str:
    """What the model is asked: one of the library's categories and its own name for what the picture shows."""
    facts = []
    if title:
        facts.append(f"Its title: {title}.")
    if record_specimen:
        facts.append(f"Its record says it shows: {record_specimen}.")
    if seen:
        facts.append(f"While it was on stage, the app's running look named: {', '.join(seen[:4])}.")
    return (
        "This is a picture through a light microscope (one frame of a recording, or a still), from a user's library.\n"
        "File it by what it shows. Pick the one category it belongs in, from exactly these: "
        f"{'; '.join(CATEGORIES)}. Then name what it is, in one to four plain words, as a biologist would file it "
        "(for example \"Rotifers\", \"Leaf epidermis\", \"Red blood cells\", \"Diatoms\"). "
        "Use the facts below as data; the picture decides.\n"
        + (" ".join(facts) + "\n" if facts else "")
        + "Reply with JSON only: {\"category\": \"<one of the categories>\", \"name\": \"<what it is>\", \"sure\": true|false}. "
        "Say sure false when the picture does not let you tell."
    )


class LibraryLooks:
    def __init__(self, library, picture: Callable[[str], "asyncio.Future"], vision: Callable[[], object | None],
                 seen_on: Callable[[str], list[str]] | None = None, log: Callable[[dict], None] | None = None,
                 chosen: Callable[[], bool] = lambda: False) -> None:
        """`picture(aid)`: the asset's small picture (JPEG bytes, or None), awaited; `vision()`: the fast vision model's
        client, or None when none can run; `seen_on(aid)`: the specimens the running look named while it was on stage."""
        self.library = library
        self.picture = picture
        self.vision = vision
        self.seen_on = seen_on or (lambda aid: [])
        self.log = log or (lambda record: None)
        self.chosen = chosen  # the user turned looks on (the setting `library_looks`), read at each step
        self._tried: dict[str, float] = {}
        self._recent: list[float] = []
        self.looks = 0

    def enabled(self) -> bool:
        """Whether the loop runs in this process at all (MICROMIND_LIBRARY_LOOKS=0 stops it); each look also waits for
        the user's choice (`chosen`)."""
        return os.environ.get("MICROMIND_LIBRARY_LOOKS", "1") != "0"

    async def run_forever(self) -> None:
        while True:
            try:
                looked = await self.step()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a look that fails never stops the next
                looked = False
            await asyncio.sleep(60.0 / LOOKS_PER_MIN if looked else IDLE_S)

    async def step(self) -> bool:
        """One look, at the oldest asset that still waits for one (not tried lately). True when a look was made. None
        while the user hasn't turned looks on: nothing is sent."""
        if not self.chosen():
            return False
        vision = self.vision()
        if vision is None:
            return False
        now = time.monotonic()
        waiting = await asyncio.to_thread(self.library.unlooked)
        asset = next((a for a in waiting if now - self._tried.get(a.id, -RETRY_S) >= RETRY_S), None)
        if asset is None:
            return False
        self._tried[asset.id] = now
        return await self.look(asset, vision)

    async def look(self, asset, vision) -> bool:
        if asset.key.startswith("onboarding:"):
            return False  # this bundled local example is never sent for cloud categorization
        data = await self.picture(asset.id)
        if not data:
            self.log({"kind": "library-look", "asset": asset.id, "ok": False, "why": "no picture to look at"})
            return False
        seen = [s for s in await asyncio.to_thread(self.seen_on, asset.id) if s]  # the catalogue reads journals: off the loop
        ask = prompt(asset.specimen, seen, asset.title_now())
        t0 = time.perf_counter()
        result = await vision.ask([data], ask, max_output_tokens=LOOK_TOKENS)
        reply = result.data if isinstance(getattr(result, "data", None), dict) else None
        category = str((reply or {}).get("category") or "").strip()
        model = str(getattr(result, "model", "") or "")
        record = {"kind": "library-look", "asset": asset.id, "example": asset.group == "example", "model": model,
                  "told": {"specimen": asset.specimen, "seen": seen}, "reply": reply, "ms": round((time.perf_counter() - t0) * 1000)}
        if category not in CATEGORIES:
            self.log({**record, "ok": False, "why": "no category in its reply"})
            return False
        name, sure = str(reply.get("name") or ""), reply.get("sure") is not False
        if asset.group == "example":
            await asyncio.to_thread(self.library.looked_specimen, asset.specimen, category, name, sure, model)
        else:
            await asyncio.to_thread(self.library.looked, asset.id, category, name, sure, model)
        self.looks += 1
        self.log({**record, "ok": True})
        return True
