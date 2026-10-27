"""Descriptions under labels: what each named thing is, looked up ahead of time.

Every labelled outline gets a short description of what its label means for this specimen
(title, summary, up to 4 details, what to look for, sources), written by a
researcher at "describe" effort (25 s, $0.08, instant pool), once per label (so five
stomata make one job). Labels are normalised by label_key ("Stomata (likely)",
"central stoma" -> "stoma"; synonyms such as "phloem fibres" and "sclerenchyma fibers" are
one key).

- Jobs are queued by priority: things the user just asked about, then the focused or
  selected outline, then the rest; at most RATE_PER_MIN start a minute and descriptions use
  at most two researcher slots (research/manager.py).
- Reference pictures are not fetched by the researcher (that would slow it): it names
  candidates, and they are fetched safely (research_worker/safe_fetch.py) the first time
  the studio shows that description (`reveal`).
- Descriptions are kept per day (services/research/<day>/descriptions.json) and redone when
  the subject changes (labels.subject_key: "blood smear, blurry" and "blood smear, ~400x"
  are the same subject) or the user changes how things are explained (learner.py: the
  researcher is told the user's level like every researcher; a description made for another
  level is redone).
- The studio gets them as a separate `notes` message ({full} on connect, then {upsert}),
  never inside scene updates, which go out up to 20 times a second.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Callable

from research_worker.safe_fetch import FetchRefused, commons_search, fetch_image

from .labels import label_key, subject_key
from .learner import DEFAULT_LEVEL
from .research import ResearchRefused, Session

RATE_PER_MIN = 6
SCAN_EVERY_S = 0.25
EFFORT = "describe"


class Enricher:
    def __init__(self, hub, research, broadcast: Callable[[dict], None], root: Path | None = None) -> None:
        self.hub = hub
        self.research = research
        self.broadcast = broadcast
        self.root = root or research.root
        self.entries: dict[str, dict] = {}
        self.queue: dict[str, dict] = {}  # original outline revision and specimen, never rebound to a later view
        self.asked: set[str] = set()
        self.started: list[float] = []
        self._version = -1
        self._task: asyncio.Task | None = None
        self._load()
        research.on_done.append(self._done)

    # --- storage ----------------------------------------------------------------------
    def _file(self) -> Path:
        return self.root / self.research.today() / "descriptions.json"  # the day as the day store has it

    def _load(self) -> None:
        try:
            saved = json.loads(self._file().read_text())
        except (OSError, ValueError):
            return
        self.entries = {k: v for k, v in saved.items() if v.get("status") == "ready"}

    def _save(self) -> None:
        path = self._file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({k: v for k, v in self.entries.items() if v.get("status") == "ready"}, indent=2))

    def wire_full(self) -> dict:
        return {"type": "notes", "full": self.entries}

    def _publish(self, key: str) -> None:
        self.broadcast({"type": "notes", "upsert": {key: self.entries[key]}})

    # --- what to describe -------------------------------------------------------------
    def mark_asked(self, object_ids) -> None:
        self.asked.update(object_ids or [])

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                self.scan()
                await self.pump()
            except Exception:
                pass
            await asyncio.sleep(SCAN_EVERY_S)

    def _level(self) -> str:
        return getattr(self.hub.settings, "level", DEFAULT_LEVEL)

    def _stale(self, entry: dict) -> bool:
        if entry.get("status") != "ready":
            return False
        if entry.get("level", DEFAULT_LEVEL) != self._level():  # written for another level (made before: simple)
            return True
        specimen = subject_key(self.hub.scene.context.get("specimen"))
        return bool(specimen) and bool(subject_key(entry.get("specimen"))) and subject_key(entry.get("specimen")) != specimen

    def _current(self, obj) -> bool:
        """Automatic lookups use only outlines still verified in the current view."""
        info = self.hub.latest_info
        return obj is not None and obj.shown and info is not None and obj.anchor_epoch == info.epoch

    def scan(self) -> None:
        scene = self.hub.scene
        if scene.version == self._version:
            return
        self._version = scene.version
        for obj in list(scene.objects.values()):
            if (obj.kind not in ("highlight", "part") or obj.trial is not None or not obj.label
                    or not self._current(obj)):
                continue
            key = label_key(obj.label)
            if not key:
                continue
            entry = self.entries.get(key)
            if entry is not None and not self._stale(entry):
                if entry.get("status") in ("ready", "pending"):
                    continue
                if entry.get("status") in ("failed", "unavailable") and time.time() - entry.get("at", 0) < 300:
                    continue
            if key not in self.queue:
                self.queue[key] = {"object_id": obj.id, "label": obj.label, "revision": obj.revision,
                                   "specimen": scene.context.get("specimen") or "",
                                   "since": time.monotonic()}

    def _priority(self, item: dict) -> tuple:
        oid = item["object_id"]
        rank = 0 if oid in self.asked else 1 if oid in (self.hub.focused, self.hub.scene.selected) else 2
        return rank, item["since"]

    async def pump(self) -> None:
        now = time.monotonic()
        self.started = [t for t in self.started if now - t < 60]
        while self.queue and len(self.started) < RATE_PER_MIN:
            key, item = min(self.queue.items(), key=lambda kv: self._priority(kv[1]))
            del self.queue[key]
            obj = self.hub.scene.objects.get(item["object_id"])
            if (not self._current(obj) or obj.revision != item["revision"] or obj.label != item["label"]
                    or (self.hub.scene.context.get("specimen") or "") != item["specimen"]):
                # scan may already have observed a replacement while this key
                # was queued; reconsider it without requiring another scene edit.
                self._version = -1
                continue
            specimen = item["specimen"] or "a specimen under a light microscope"
            level = self._level()
            # About the kind of thing, for someone looking at it under the microscope. The card
            # already says how sure the name is; the lookup never sees the image, so it is not
            # asked to judge it (that only added "without an image I cannot confirm...").
            brief = (f'Describe "{item["label"]}", as seen in {specimen}, for someone looking at one under a microscope: '
                     "what it is, what it does and what to look for. Do not comment on any image or on whether the "
                     "name is right.")
            try:
                session = await self.research.start(brief, effort=EFFORT, deliverable="description", purpose="description",
                                                    label=item["label"], object_id=item["object_id"])
            except ResearchRefused as exc:
                self.entries[key] = {"key": key, "label": item["label"], "status": "unavailable", "reason": str(exc), "at": time.time()}
                self._publish(key)
                continue
            self.started.append(now)
            self.entries[key] = {"key": key, "label": item["label"], "status": "pending", "rid": session.rid, "at": time.time(),
                                 "level": level, "specimen": item["specimen"]}
            self._publish(key)

    # --- results ----------------------------------------------------------------------
    def _done(self, session: Session) -> None:
        if session.purpose != "description":
            return
        key = label_key(session.label)
        result = session.result or {}
        if session.status == "done" and result.get("summary"):
            self.entries[key] = {
                "key": key, "label": session.label, "status": "ready", "rid": session.rid, "at": time.time(),
                "specimen": session.specimen,
                "level": (self.entries.get(key) or {}).get("level") or self._level(),
                "description": {k: result.get(k) for k in ("title", "summary", "details", "look_for", "confidence", "sources")},
                "refCandidates": result.get("ref_candidates") or [], "refs": [], "spent": session.spent,
            }
            self._save()
        else:
            self.entries[key] = {"key": key, "label": session.label, "status": "failed", "rid": session.rid,
                                 "reason": session.status, "at": time.time()}
        self._publish(key)

    async def reveal(self, key: str) -> None:
        """The studio is showing this description: fetch its reference pictures once."""
        entry = self.entries.get(key)
        if not entry or entry.get("status") != "ready" or entry.get("refs") or entry.get("refsTried"):
            return
        entry["refsTried"] = True
        session = self.research.get(entry.get("rid", ""))
        day = session.day if session else self.research.today()
        directory = self.root / day / entry["rid"] / "sources"
        directory.mkdir(parents=True, exist_ok=True)
        refs = []
        # The researcher's candidates, else a Commons search for the kind of thing.
        candidates = (entry.get("refCandidates") or [])[:3]
        if not candidates:
            try:
                candidates = await commons_search(f"{entry['key']} microscope")
            except FetchRefused:
                candidates = []  # optional pictures do not prevent showing the description
        for url in candidates:
            try:
                image = await fetch_image(url)
            except FetchRefused:
                continue
            (directory / f"{image.sha1}.jpg").write_bytes(image.jpeg)
            refs.append({"url": f"/research/{day}/{entry['rid']}/sources/{image.sha1}.jpg", "page": image.page,
                         "title": image.title, "credit": image.credit()})
            if len(refs) == 2:
                break
        entry["refs"] = refs
        self._save()
        self._publish(key)
