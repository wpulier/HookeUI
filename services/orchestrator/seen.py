"""Seen one like this before? (roadmap W7, option SEEN).

Every outline the app or the user names (a click, a highlight, a finder result; never a
part, a blind arena arm or a first-guess ring) is kept with its embedding (option EMBED: DINOv2
small by default, or BioCLIP 2), the same one taught.py computes for the user's own labels
(`TaughtLabels.object_vectors`: one embedding per outline on the heavy GPU worker, in the
background as the outline appears, never on the click's path), its name, when it was named, the session and
conversation it was in and its crop (logs/crops/<day>/). They are kept per date in the seen folder (paths.py),
seen/<day>/seen.jsonl (git-ignored, kept), and read back after a restart; at most MAX_KEPT a date, PER_NAME per
session, conversation and name. Seen-before is the user's, not a session's (ADR 0017): it looks across every
session (the newest MAX_LOADED read back), and says which session and conversation an earlier one was in.

Each model's similarities are on their own scale, so each has its own threshold, list floor and
calibration (THRESHOLDS...), and only vectors of the same model are compared: after EMBED changes,
the day's kept outlines are embedded again from their crops in the background (`refresh`), kept
beside the old ones under the same id, and compared as they become ready.

What they are used for is a fact, never a verdict (docs/design/2026-10-06-facts-not-verdicts.md):
- naming a click is told, in one line, the outline named earlier today that is most like it, when
  its cosine similarity is at least THRESHOLD (bench/seen_bench.py, docs/bench/seen.md: the most
  alike earlier outline was the same kind of cell 92-94% of the time from there); the model
  decides whether it helps;
- the `seen_before` tool lists the earlier outlines most like one now, with their time,
  conversation and name, in the past tense.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .labels import label_key
from .taught import DEFAULT_MODEL
from .untrusted import inline

# Per embedding model (option EMBED), measured by bench/seen_bench.py on the naming bench's 12 kinds
# (docs/bench/seen.md). DINOv2 small: the most alike earlier outline was the same kind of cell 92-94%
# of the time at 0.93 and above (recall 6-17%): naming is told it from here. BioCLIP 2's similarities
# are higher and closer together: from 0.96 the same kind 91-92% (recall 3-15%), from 0.965 always but
# almost never (recall 0-4%).
THRESHOLDS = {"dinov2-small": 0.93, "bioclip-2": 0.96}
# From here the seen_before tool lists earlier outlines. DINOv2: above 0.85, 98% were the same specimen
# (often another kind of cell of it); a different specimen scored a median 0.68, its 99th percentile
# 0.84. BioCLIP 2: from 0.90 (98% the same specimen from 0.895; a different specimen
# scored a median 0.84, its 99th percentile 0.90).
LIST_FLOORS = {"dinov2-small": 0.85, "bioclip-2": 0.90}
THRESHOLD = THRESHOLDS[DEFAULT_MODEL]
LIST_FLOOR = LIST_FLOORS[DEFAULT_MODEL]
REFRESH_BATCH = 32  # kept outlines embedded again per call after EMBED changes
SKIP_RECENT_S = 60.0  # outlines named in the last minute are this view, not "earlier"
MAX_KEPT = 500  # a date, so memory and the date's file stay small (a finder can outline a hundred)
MAX_LOADED = 2000  # named outlines read back across every session, the newest (about four full days' worth)
PER_NAME = 10  # per session, conversation and name: the first ones stand for the rest
# What is kept once named: highlights only (invariant 10). Cells are many and unnamed one by one; a
# population's members are evidence (M1.3), with their own identity. Parts go with their object.
KINDS = ("highlight",)
BATCH_S = 0.03  # new outlines this close together are embedded in one call (a finder's results)

CALIBRATIONS = {
    "dinov2-small": ("Measured on 12 kinds of cell: from 0.93 the earlier one was the same kind 93% of the time; "
                     "below that it was often another kind of the same specimen."),
    "bioclip-2": ("Measured on 12 kinds of cell: from 0.96 the earlier one was the same kind 92% of the time; "
                  "below that it was often another kind of the same specimen."),
}
CALIBRATION = CALIBRATIONS[DEFAULT_MODEL]


def threshold(model: str = DEFAULT_MODEL) -> float:
    """From what similarity a match is told. A picture model never benched here has none (nothing matches),
    never another model's: cosines of two models are on different scales (M3, DINOv3)."""
    return THRESHOLDS.get(model, float("inf"))


def list_floor(model: str = DEFAULT_MODEL) -> float:
    return LIST_FLOORS.get(model, float("inf"))


@dataclass
class Kept:
    id: str  # "s3": never reused within a date folder (object ids start again after a restart)
    object_id: str
    label: str
    key: str
    status: str  # the outline's status when named: "confirmed" = the user named or agreed to it
    thread: str  # the conversation it was named in
    vector: list[float]
    crop: str  # the crop that was embedded, relative to the logs folder ("" if it could not be saved)
    at: float = field(default_factory=time.time)
    kind: str = "highlight"
    source: str = ""
    frame_id: int | None = None
    model: str = DEFAULT_MODEL  # the embedding model that made `vector` (lines from before EMBED: DINOv2)
    # What drew the outline (its made_by's operation and implementation, and who started that run: "actor", ticket 66),
    # so the index says which model made it after the outline is gone (2026-10-11); None on lines kept before.
    made_by: dict | None = None
    session: str = ""  # the session it was named in ("" on lines from before sessions: its date's)
    day: str = ""  # the date folder it is kept in (lines from before: the folder they were read from)


@dataclass(frozen=True)
class Similar:
    kept: Kept
    similarity: float

    def wire(self) -> dict:
        return {"seenId": self.kept.id, "objectId": self.kept.object_id, "label": self.kept.label,
                "thread": self.kept.thread, "at": round(self.kept.at, 1), "similarity": round(self.similarity, 3)}


def _same(kept: Kept, ref) -> bool:
    """Whether a kept outline is the one `ref` names: its (date, id), or its id alone."""
    return (kept.day, kept.id) == ref if isinstance(ref, tuple) else kept.id == ref


def when(at: float) -> str:
    return time.strftime("%H:%M", time.localtime(at))


def most_alike(vector, kept: list[Kept], *, exclude: set[str] = frozenset(), before: float | None = None,
               floor: float = THRESHOLD, k: int = 1) -> list[Similar]:
    """The `k` kept outlines most like `vector` with a similarity of at least `floor`, most alike
    first; not those in `exclude` (kept (date, id)s, or ids) nor any named after `before`. Vectors are unit
    length (gpu_worker/embed.py), so a dot product is the cosine."""
    candidates = [s for s in kept if (s.day, s.id) not in exclude and s.id not in exclude
                  and (before is None or s.at <= before) and s.vector]
    if not candidates:
        return []
    v = np.asarray(vector, np.float32)
    norm = float(np.linalg.norm(v))
    if norm == 0:
        return []
    mat = np.asarray([s.vector for s in candidates], np.float32)
    mat = mat / np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-9)
    sims = mat @ (v / norm)
    order = np.argsort(-sims)
    return [Similar(candidates[i], float(sims[i])) for i in order[:k] if sims[i] >= floor]


def named_by(kept: Kept, who: str = "you") -> str:
    """Who gave the earlier name, in the prompt's voice."""
    if kept.status == "confirmed":
        return "the user named it"
    return f"{who} named it{', not sure' if kept.status == 'unsure' else ''}"


def _earlier(kept: Kept, today: str = "") -> str:
    """When an earlier one was named: "today (15:02" said as before, else its date ("Oct 10 15:02")."""
    if not today or time.strftime("%Y-%m-%d", time.localtime(kept.at)) == today:
        return f"today ({when(kept.at)}"
    return f"({time.strftime('%b %-d', time.localtime(kept.at))} {when(kept.at)}"


def context_line(similar: Similar | None, title: str = "", session: str = "", today: str = "") -> str:
    """What naming a click is told: one line of data, in the past tense. The model decides. `session`: the other
    session's name it was named in ("" for this one); `today`: today's date (one from another day says its date)."""
    if similar is None:
        return ""
    kept = similar.kept
    # Names and titles are the user's or a model's words, in double quotes they cannot contain.
    where = f', in the conversation "{inline(title, 60)}"' if title else ""
    if session:
        where = f', in the session "{inline(session, 60)}"' + (f', conversation "{inline(title, 60)}"' if title else "")
    return (f'Earlier {_earlier(kept, today)}{where}) an outline that looked like this one was named '
            f'"{inline(kept.label, 60)}" ({named_by(kept)}; similarity {similar.similarity:.2f}). '
            f"{CALIBRATIONS.get(kept.model, '')} That was earlier, not this view: a clue, not a name for this one.")


def listing(found: list[Similar], title: Callable, about: str, session: Callable | None = None, today: str = "") -> str:
    """What the seen_before tool says: the earlier outlines most like this one, past tense. `title(kept)` its
    conversation's title, `session(kept)` the other session's name it was in ("" for this one)."""
    lines, all_today = [], True
    for s in found:
        t = title(s.kept)
        other = session(s.kept) if session is not None else ""
        where = (f' in the session "{inline(other, 60)}"' + (f', "{inline(t, 60)}"' if t else "")) if other else (
            f' in "{inline(t, 60)}"' if t else "")
        same_day = not today or time.strftime("%Y-%m-%d", time.localtime(s.kept.at)) == today
        all_today = all_today and same_day
        stamp = when(s.kept.at) if same_day else f"{time.strftime('%b %-d', time.localtime(s.kept.at))} {when(s.kept.at)}"
        lines.append(f'{stamp}{where}: "{inline(s.kept.label, 60)}" ({s.similarity:.2f}; '
                     f'{named_by(s.kept, "the app")})')
    model = found[0].kept.model if found else DEFAULT_MODEL
    return (f"Outlines named earlier{' today' if all_today else ''} that look most like {about} (picture similarity; "
            f"{CALIBRATIONS.get(model, '')} "
            f"From {list_floor(model):.2f} usually the same specimen): " + "; ".join(lines)
            + ". These were earlier, not in view now.")


class SeenToday:
    """Named outlines of every session, on disk and in memory.

    `taught` (taught.TaughtLabels) gives each outline's crop and vector, shared with the user's
    labels so an outline is embedded once; `today()` is today's date (new lines go to its folder), `session()` the
    session open now, `thread()` the conversation going on now, `title(tid)` a conversation's title in the session open
    now; `catalogue` (day/catalogue.py) says another session's name and conversations; `hub` gives the scene and the
    settings (option SEEN); `logs` is the logs folder (crops go in logs/crops/<day>/)."""

    def __init__(self, root: Path, logs: Path, today: Callable[[], str], thread: Callable[[], str],
                 title: Callable[[str], str], taught, hub=None, log=None, clock: Callable[[], float] = time.time,
                 session: Callable[[], str] | None = None, catalogue=None) -> None:
        self.root = Path(root)
        self.logs = Path(logs)
        self.today = today
        self.thread = thread
        self.title = title
        self.session = session or (lambda: "")
        self.catalogue = catalogue
        self.taught = taught
        self.hub = hub
        self.log = log or (lambda record: None)
        self.clock = clock
        self.day = ""
        self.kept: list[Kept] = []  # one per id and model: after EMBED changes, an id has one of each
        self._refreshing: dict[str, asyncio.Future] = {}  # model -> kept outlines being embedded again
        self._by_object: dict[str, tuple[str, str]] = {}  # object id -> its kept (date, id), this run only
        self._pending: list[str] = []  # outlines waiting to be embedded together
        self._flush_at = None
        self._full_said = False
        self._load()

    # --- the files --------------------------------------------------------------------------
    def folder(self, day: str | None = None) -> Path:
        """A date's folder (default: today's, where new lines go)."""
        return self.root / (day or self.today())

    def _load(self) -> None:
        """Every session's named outlines, newest date first, the newest MAX_LOADED of them (today's always: at most
        MAX_KEPT), each with every model's line of it."""
        from .day.sid import is_sid

        self.day = self.today()
        self.kept, self._by_object, self._full_said = [], {}, False
        try:
            folders = sorted((p for p in self.root.iterdir() if p.is_dir() and is_sid(p.name)), key=lambda p: p.name,
                             reverse=True)
        except OSError:
            folders = []
        by_id: dict[tuple[str, str, str], Kept] = {}
        outlines: set[tuple[str, str]] = set()
        for folder in folders:
            path = folder / "seen.jsonl"
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                try:
                    k = Kept(**json.loads(line))
                except (ValueError, TypeError):
                    continue  # a line cut off by a crash: the rest still load
                k.day = k.day or folder.name
                by_id[(k.day, k.id, k.model)] = k  # a renamed outline's newest line wins (a rename writes every model's)
                outlines.add((k.day, k.id))
            if len(outlines) >= MAX_LOADED and folder.name != self.day:
                break  # older dates are not read: enough to look across
        newest = {(k.day, k.id): k for k in by_id.values()}
        keep = set(sorted(newest, key=lambda ref: newest[ref].at)[-MAX_LOADED:])
        self.kept = sorted((k for k in by_id.values() if (k.day, k.id) in keep), key=lambda k: k.at)

    def _current(self) -> None:
        """What was seen is the user's, whatever the date or the session: nothing to reload. (After midnight new lines
        go to the new date's folder.)"""
        return None

    def reset_run(self) -> None:
        """Another session was opened: read again. What was seen stays (ADR 0017: seen-before looks across every
        session), so nothing moves aside."""
        self._load()

    # --- state -----------------------------------------------------------------------------
    def on(self) -> bool:
        """Option SEEN is on, and there is an embedding model (none without a GPU worker)."""
        settings = getattr(self.hub, "settings", None)
        return getattr(settings, "seen", "on") == "on" and getattr(self.taught, "embed", None) is not None

    def model(self) -> str:
        """The embedding model (option EMBED), as taught.py uses it."""
        return self.taught.model() if self.taught is not None and hasattr(self.taught, "model") else DEFAULT_MODEL

    def _ids(self, day: str | None = None) -> list[str]:
        """The ids kept in a date's folder (default: today's)."""
        day = day or self.today()
        return list(dict.fromkeys(k.id for k in self.kept if k.day == day))

    def earlier(self, model: str | None = None) -> list[Kept]:
        """Kept outlines of every session named before the last minute; with `model`, those it has embedded
        (the rest are embedded again in the background: `refresh`)."""
        self._current()
        cut = self.clock() - SKIP_RECENT_S
        if model is None:
            seen_ids: dict[tuple[str, str], Kept] = {}
            for k in self.kept:
                if k.at <= cut:
                    seen_ids.setdefault((k.day, k.id), k)
            return list(seen_ids.values())
        return [k for k in self.kept if k.at <= cut and k.model == model]

    def refresh(self, model: str | None = None) -> asyncio.Future | None:
        """Embed again, with `model`, the kept outlines it has no vector for (EMBED changed since they
        were kept), from their crops, REFRESH_BATCH a call, in the background; None when there are
        none. Shared while it runs."""
        model = model or self.model()
        running = self._refreshing.get(model)
        if running is not None:
            return running
        have = {(k.day, k.id) for k in self.kept if k.model == model}
        todo = list({(k.day, k.id): k for k in self.kept if (k.day, k.id) not in have and k.crop}.values())
        if not todo or self.taught is None:
            return None
        from .options import unrelated

        future = unrelated(self._refresh(model, todo))
        self._refreshing[model] = future

        def done(f: asyncio.Future) -> None:
            self._refreshing.pop(model, None)
            if not f.cancelled() and f.exception() is not None:
                self.log({"kind": "seen-refresh", "model": model, "error": str(f.exception())[:200]})

        future.add_done_callback(done)
        return future

    async def _refresh(self, model: str, todo: list[Kept]) -> int:
        def read(chunk):
            return [(k, (self.logs / k.crop).read_bytes()) for k in chunk if (self.logs / k.crop).exists()]

        n = 0
        for start in range(0, len(todo), REFRESH_BATCH):
            crops = await asyncio.to_thread(read, todo[start:start + REFRESH_BATCH])
            if not crops:
                continue
            vectors = await self.taught.vectors([jpeg for _, jpeg in crops], model)
            for (k, _), vector in zip(crops, vectors):
                if any(x.day == k.day and x.id == k.id and x.model == model for x in self.kept):
                    continue
                again = Kept(**{**asdict(k), "vector": [round(float(v), 5) for v in vector], "model": model})
                self.kept.append(again)
                await asyncio.to_thread(self._write, again, None)
                n += 1
        self.log({"kind": "seen-refresh", "model": model, "kept": n})
        return n

    # --- keeping ---------------------------------------------------------------------------
    def watch(self, scene) -> None:
        """Embed each new outline as it appears (in the background), and keep it once named."""
        scene.on_change.append(self._scene_changed)
        if hasattr(scene, "on_label"):
            scene.on_label.append(self._named)

    @staticmethod
    def eligible(obj) -> bool:
        return (obj is not None and obj.kind in KINDS and obj.trial is None and obj.parent is None and bool(obj.polygons)
                and not str(obj.source).endswith(":first-guess")
                and not (obj.naming or {}).get("basisLabel")
                and (obj.naming or {}).get("by") != "concept")  # words matched are not a name the user gave (M3.1 S5)  # "largest by area" is how it was chosen, not a name

    def _scene_changed(self, op: str, ids) -> None:
        if op != "add" or not self.on() or self.taught is None or self.hub is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for oid in ids:
            obj = self.hub.scene.objects.get(oid)
            if not self.eligible(obj):
                continue
            if obj.kind == "highlight":
                # Embedded now, so naming it finds its vector ready (cells only once named).
                self._pending.append(oid)
            if label_key(obj.label) and obj.status not in ("looking", "outline"):
                self._named(oid)  # outlined with its name (the look, the finder)
        if self._pending and self._flush_at is None:
            self._flush_at = loop.call_later(BATCH_S, self._flush)

    def _flush(self) -> None:
        self._flush_at = None
        ids, self._pending = self._pending, []
        if ids and self.taught is not None:
            self.taught.object_vectors(ids)  # one call to the heavy worker for all of them

    def vector_of(self, object_id: str) -> asyncio.Future:
        """The outline's (crop, vector, frame id, model), shared with taught.py; asked for now if it
        was waiting to be embedded with others."""
        if object_id in self._pending:
            self._pending.remove(object_id)
        return self.taught.object_vectors([object_id])[object_id]

    def _named(self, object_id: str) -> None:
        """Scene.on_label: an outline got a name (or a new one). Kept in the background."""
        if not self.on() or self.taught is None or self.hub is None:
            return
        obj = self.hub.scene.objects.get(object_id)
        if not self.eligible(obj) or not label_key(obj.label) or obj.status in ("looking", "outline"):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        # What it was called, where and when, fixed now: the user may switch conversations meanwhile.
        drawn = getattr(obj, "made_by", None) or {}
        what = dict(label=obj.label, status=obj.status, thread=self.thread(), at=self.clock(), kind=obj.kind,
                    source=obj.source, session=self.session() or "",
                    made_by={k: drawn[k] for k in ("operation", "implementation", "actor") if drawn.get(k)} or None)
        from .options import unrelated

        unrelated(self._keep(object_id, what))  # not for the request that named it: none of its choices (contract 7)

    async def _keep(self, object_id: str, what: dict) -> Kept | None:
        self._current()
        key = label_key(what["label"])
        session = what.get("session") or ""
        if object_id not in self._by_object and not self._room(object_id, what["thread"], key, session):
            return None
        try:
            while object_id in self._pending:  # embedded with the others outlined with it
                await asyncio.sleep(BATCH_S)
            got = await self.vector_of(object_id)
        except Exception as exc:  # no worker, a busy worker: nothing is kept, naming went on
            self.log({"kind": "seen-keep", "objectId": object_id, "error": f"{type(exc).__name__}: {exc}"[:200]})
            return None
        if got is None:
            return None
        jpeg, vector, frame_id, model = got
        existing = self._by_object.get(object_id)  # again: others were kept while this waited
        if existing is None and not self._room(object_id, what["thread"], key, session):
            return None
        if existing is not None:
            old = [k for k in self.kept if _same(k, existing)]
            if not old:
                return None
            # Renamed: every model's line of it takes the new name.
            renamed = {id(k): Kept(**{**asdict(k), "label": what["label"], "key": key, "status": what["status"]}) for k in old}
            self.kept = [renamed.get(id(k), k) for k in self.kept]
            written = list(renamed.values())
            kept = next((k for k in written if k.model == model), written[0])
        else:
            day = self.today()
            n = 1 + max((int(k.id[1:]) for k in self.kept if k.day == day and k.id[1:].isdigit()), default=0)
            kept = Kept(id=f"s{n}", object_id=object_id, label=what["label"], key=key, status=what["status"],
                        thread=what["thread"], vector=[round(float(v), 5) for v in vector], crop="", at=what["at"],
                        kind=what["kind"], source=what["source"], frame_id=frame_id, model=model,
                        made_by=what.get("made_by"), session=session, day=day)
            self._by_object[object_id] = (kept.day, kept.id)
            self.kept.append(kept)
            written = [kept]
        try:
            for one in written:
                await asyncio.to_thread(self._write, one, jpeg if existing is None else None)
        except OSError as exc:
            self.log({"kind": "seen-keep", "objectId": object_id, "error": str(exc)[:200]})
        return kept

    def _room(self, object_id: str, thread: str, key: str, session: str = "") -> bool:
        """A new outline is kept while today's date has room and its conversation (of its session) has fewer than
        PER_NAME of its name (the first ones stand for the rest of a finder's hundred)."""
        if len(self._ids()) >= MAX_KEPT:
            if not self._full_said:
                self._full_said = True
                self.log({"kind": "seen-keep", "objectId": object_id, "skipped": f"{MAX_KEPT} kept today"})
            return False
        mine = session or self.today()
        return len({(k.day, k.id) for k in self.kept if k.thread == thread and k.key == key
                    and self._session_of(k) == mine}) < PER_NAME

    @staticmethod
    def _session_of(kept: Kept) -> str:
        """The session it was named in (a line from before sessions: its date's)."""
        return kept.session or kept.day

    def _write(self, kept: Kept, jpeg: bytes | None) -> None:
        folder = self.folder(kept.day or None)  # its own date's file (a rename or a re-embedding of an older one too)
        if jpeg is not None:
            crops = self.logs / "crops" / folder.name
            crops.mkdir(parents=True, exist_ok=True)
            name = f"{time.strftime('%H%M%S', time.localtime(kept.at))}-seen-{kept.id}-{kept.object_id}.jpg"
            (crops / name).write_bytes(jpeg)
            kept.crop = str((crops / name).relative_to(self.logs))
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "seen.jsonl").open("a") as f:
            f.write(json.dumps(asdict(kept)) + "\n")

    # --- asking -------------------------------------------------------------------------------
    async def match(self, object_id: str) -> Similar | None:
        """The outline named earlier today most like this one, at the model's threshold or above;
        None when SEEN is off or nothing was named earlier today (then nothing is waited for). Only
        outlines embedded by the same model are compared."""
        if not self.on() or self.taught is None:
            return None
        if not self.earlier():
            return None
        got = await asyncio.shield(self.vector_of(object_id))  # shared: a timed-out wait leaves it running
        if got is None:
            return None
        model = got[3]
        self.refresh(model)  # those kept with another model: compared once embedded again
        mine = {self._by_object[object_id]} if object_id in self._by_object else set()
        found = most_alike(got[1], self.earlier(model), exclude=mine, floor=threshold(model))
        return found[0] if found else None

    async def alike(self, object_id: str, k: int = 3, floor: float | None = None) -> tuple[list[Similar], Similar | None]:
        """The `k` earlier outlines most like this one from `floor` (the model's list floor), and
        the closest of all (to say how far it was when none is that close)."""
        if not self.earlier():
            return [], None
        got = await asyncio.shield(self.vector_of(object_id))  # shared: a timed-out wait leaves it running
        if got is None:
            raise ValueError("its picture is no longer here (the slide moved on)")
        model = got[3]
        self.refresh(model)
        earlier = self.earlier(model)
        mine = {self._by_object[object_id]} if object_id in self._by_object else set()
        closest = most_alike(got[1], earlier, exclude=mine, floor=-1.0)
        floor = list_floor(model) if floor is None else floor
        return most_alike(got[1], earlier, exclude=mine, floor=floor, k=k), (closest[0] if closest else None)

    def thread_title(self, ref) -> str:
        """A conversation's title: of a conversation id in the session open now, or of a kept outline's own
        conversation in its own session (another session's from the session list)."""
        if isinstance(ref, Kept):
            other = self.session_name(ref)
            if other is not None:
                info = self._info(self._session_of(ref))
                return str((getattr(info, "titles", None) or {}).get(ref.thread) or "") if info is not None else ""
            ref = ref.thread
        try:
            return self.title(ref) or ""
        except Exception:
            return ""

    def _info(self, sid: str):
        try:
            return self.catalogue.get(sid) if self.catalogue is not None else None
        except Exception:
            return None

    def session_name(self, kept: Kept) -> str | None:
        """The name of the other session an outline was named in ("Blood smear", else its date "Oct 10"); None when
        it was this session."""
        from .day.sid import is_sid, label

        sid = self._session_of(kept)
        if not sid or sid == (self.session() or self.today()):
            return None
        info = self._info(sid)
        return (info.label if info is not None else label(sid) if is_sid(sid) else sid) or None

    def line(self, similar: Similar | None) -> str:
        """What naming a click is told of the outline named earlier most like it (context_line), with its session
        when that was another one and its date when that was another day."""
        if similar is None:
            return ""
        return context_line(similar, self.thread_title(similar.kept), self.session_name(similar.kept) or "",
                            today=self.today())

    def listing(self, found: list[Similar], about: str) -> str:
        """What the seen_before tool says (listing), each one's session when another and its date when another day."""
        return listing(found, self.thread_title, about, session=lambda k: self.session_name(k) or "",
                       today=self.today())
