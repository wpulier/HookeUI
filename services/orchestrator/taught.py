"""Labels the user teaches (roadmap W7, N14; option TAUGHT).

When the user names an outlined thing ("this is a guard cell": the `name_outline` tool, or
accepting a suggested correction), the app keeps an example: a square crop of the thing from
the frame it was outlined on, its embedding (gpu_worker/embed.py, on the heavy GPU worker; option
EMBED: DINOv2 small by default, or BioCLIP 2) and the user's name. Examples are written per date in the
taught folder (paths.py), taught/<day>/ (git-ignored, kept), each with the session it was taught in. They are the
user's, not a session's (ADR 0017): every date's are read back, newest first, the newest MAX_EXAMPLES of each name.

Every vector says which model made it, and vectors of two models are never compared: after EMBED
changes, the examples are embedded again from their crops (one call, the first time a match
needs them), kept beside the old ones under the same id, in their own date's file.

Once a name has at least MIN_EXAMPLES examples, something new that is clicked or outlined is
compared with them (cosine similarity, the mean of its nearest few per name). The result is a
fact, never a verdict (docs/design/2026-10-06-facts-not-verdicts.md): naming a click is told it
in one line (Looks like the user's own "guard cell" examples (similarity 0.82, 4 examples)...)
and the model decides the name; the studio shows it apart from the model's name ("your label:
guard cell (0.82)"). Code never writes it into the outline's label.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from functools import partial
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .labels import label_key
from .untrusted import inline

MIN_EXAMPLES = 3  # a name is matched against once it has this many examples
MAX_EXAMPLES = 10  # per name, the newest are used (older ones stay on disk)
TOP_K = 3  # a name's similarity: the mean of its K nearest examples (bench/taught_bench.py)
MARGIN = 1.5  # the crop is this many times the outline's longer side, square, around its centre
CROP_SIDE = 224  # px, what the embedding model is given
DEFAULT_MODEL = "dinov2-small"  # option EMBED's default (gpu_worker/embed.py)
REMEMBERED = 100  # recent outlines' crops (or frames) kept in memory, for teaching after the ring has moved on


@dataclass
class Example:
    id: str  # "t3": a date folder's own (an example is its date and id)
    label: str
    key: str
    vector: list[float]
    crop: str  # file name beside its date's examples.jsonl
    object_id: str = ""
    frame_id: int | None = None
    source: str = ""  # how it was taught: "named" (the user said it), "correction" (accepted)
    at: float = field(default_factory=time.time)
    model: str = DEFAULT_MODEL  # the embedding model that made `vector` (lines from before EMBED: DINOv2)
    day: str = ""  # the date folder it is kept in (lines from before sessions: the folder they were read from)
    session: str = ""  # the session it was taught in ("" on lines from before sessions: its date's)


@dataclass(frozen=True)
class Match:
    label: str
    similarity: float
    examples: int  # how many examples of that name were compared
    next_label: str = ""
    next_similarity: float | None = None
    model: str = DEFAULT_MODEL  # the embedding model the similarity is measured with

    def wire(self) -> dict:
        return {"label": self.label, "similarity": round(self.similarity, 2), "examples": self.examples}


def crop_for(image_bgr: np.ndarray, bbox: tuple[float, float, float, float], margin: float = MARGIN,
             side: int = CROP_SIDE) -> np.ndarray | None:
    """A square crop around an outline's box (its longer side x `margin`), padded with black
    where it runs off the frame, resized to `side`. None for an empty box."""
    x0, y0, x1, y1 = bbox
    size = max(x1 - x0, y1 - y0) * margin
    if size < 4:
        return None
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left, top = int(round(cx - size / 2)), int(round(cy - size / 2))
    n = int(round(size))
    h, w = image_bgr.shape[:2]
    out = np.zeros((n, n, 3), np.uint8)
    sx0, sy0, sx1, sy1 = max(0, left), max(0, top), min(w, left + n), min(h, top + n)
    if sx1 <= sx0 or sy1 <= sy0:
        return None
    out[sy0 - top: sy1 - top, sx0 - left: sx1 - left] = image_bgr[sy0:sy1, sx0:sx1]
    return cv2.resize(out, (side, side), interpolation=cv2.INTER_AREA if n > side else cv2.INTER_CUBIC)


def encode(crop_bgr: np.ndarray) -> bytes:
    ok, jpeg = cv2.imencode(".jpg", crop_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise ValueError("could not encode the crop")
    return jpeg.tobytes()


def nearest(vector, examples: list[Example], k: int = TOP_K, min_examples: int = MIN_EXAMPLES,
            model: str = DEFAULT_MODEL) -> Match | None:
    """The taught name `vector` (made by `model`) is most like: per name with at least
    `min_examples` examples (its newest MAX_EXAMPLES), the mean cosine similarity of its `k`
    nearest; None when no name has enough. Only examples embedded by the same model are compared.
    Vectors are unit length, so a dot product is the cosine."""
    by_key: dict[str, list[Example]] = {}
    for ex in examples:
        if ex.model == model:
            by_key.setdefault(ex.key, []).append(ex)
    v = np.asarray(vector, np.float32)
    norm = float(np.linalg.norm(v))
    if norm == 0:
        return None
    v = v / norm
    scores = []
    for group in by_key.values():
        group = sorted(group, key=lambda e: e.at)[-MAX_EXAMPLES:]
        if len(group) < min_examples:
            continue
        mat = np.asarray([e.vector for e in group], np.float32)
        mat = mat / np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-9)
        sims = np.sort(mat @ v)[::-1]
        scores.append((float(sims[: max(1, k)].mean()), group[-1].label, len(group)))
    if not scores:
        return None
    scores.sort(key=lambda s: s[0], reverse=True)
    best = scores[0]
    runner = scores[1] if len(scores) > 1 else None
    return Match(best[1], best[0], best[2], runner[1] if runner else "", runner[0] if runner else None, model)


def context_line(match: Match | None) -> str:
    """What naming a click is told: one line of data. The model decides the name."""
    if match is None:
        return ""
    # The names are the user's words, in double quotes they cannot contain (untrusted.inline).
    line = (f'Looks like the user\'s own "{inline(match.label, 60)}" examples (similarity {match.similarity:.2f}, '
            f'{match.examples} examples)')
    if match.next_label:
        line += f'; next: their "{inline(match.next_label, 60)}" examples ({match.next_similarity:.2f})'
    calibration = CALIBRATIONS.get(match.model, f"Not yet measured with this picture model ({match.model}): how often its "
                                   "closest taught name is right is unknown.")  # never another model's numbers (M3)
    return (line + f". {calibration + ' ' if calibration else ''}The user named those; it is a clue, not their name for this one.")


# What the similarity has meant, measured per embedding model (bench/taught_bench.py, told to the model as data).
CALIBRATIONS = {
    "dinov2-small": ("Measured with 5 examples per kind: the closest taught name was right 72% of the time over 12 kinds "
                     "of cell (80% over 8 kinds of blood cell); right and wrong matches both scored about 0.88, and things "
                     "of a kind never taught about 0.77."),
    "bioclip-2": ("Measured with 5 examples per kind: the closest taught name was right 68% of the time over 12 kinds "
                  "of cell (74% over 8 kinds of blood cell); right and wrong matches both scored about 0.93, and things "
                  "of a kind never taught about 0.86."),
}
CALIBRATION = CALIBRATIONS[DEFAULT_MODEL]


class TaughtLabels:
    """The user's examples, on disk and in memory, and the matches of what is outlined now.

    `embed(jpegs, model=...) -> vectors` is the heavy GPU worker's `embed` op (`gpu_embed`, or a
    stand-in in tests); `today()` today's date (new examples go to its folder); `session()` the session open now
    (each example says where it was taught); `hub` (optional) gives the frames, the scene
    and the settings (options TAUGHT and EMBED): with it, every new outline is cropped as it appears
    (`watch`), so it can be taught after its frame has left the buffer, and matched while option
    TAUGHT is "on"."""

    def __init__(self, root: Path, today: Callable[[], str], embed=None, log=None, hub=None,
                 session: Callable[[], str] | None = None) -> None:
        self.root = Path(root)
        self.today = today
        self.session = session or (lambda: "")
        self.embed = embed
        self.log = log or (lambda record: None)
        self.hub = hub
        self.day = ""
        self.examples: list[Example] = []  # one per id and model: after EMBED changes, an id has one of each
        # object id -> (its crop as it appeared, its vector or None, the model that made the vector)
        self.recent: OrderedDict[str, tuple[bytes, list[float] | None, str]] = OrderedDict()
        self._matching: dict[str, asyncio.Future] = {}  # object id -> its match, while it runs
        self._vectors: dict[tuple[str, str], asyncio.Future] = {}  # (object id, model) -> its vector, while it runs
        self._refreshing: dict[str, asyncio.Future] = {}  # model -> the day's examples being embedded again
        # object id -> (its frame's JPEG, box, frame id) as it appeared, cut only if it is taught
        self.sources: OrderedDict[str, tuple[bytes, tuple, int]] = OrderedDict()
        self._load()

    # --- the files ---------------------------------------------------------------------------
    def folder(self, day: str | None = None) -> Path:
        """A date's folder (default: today's, where new examples go)."""
        return self.root / (day or self.today())

    def _load(self) -> None:
        """Every date's examples, newest date first, keeping the newest MAX_EXAMPLES of each name (all that matching
        uses; older ones stay on disk), each with every model's line of it."""
        from .day.sid import is_sid

        self.day = self.today()
        lines: list[Example] = []
        try:
            folders = sorted((p for p in self.root.iterdir() if p.is_dir() and is_sid(p.name)), key=lambda p: p.name,
                             reverse=True)
        except OSError:
            folders = []
        for folder in folders:
            path = folder / "examples.jsonl"
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                try:
                    ex = Example(**json.loads(line))
                except (ValueError, TypeError):
                    continue  # a line cut off by a crash: the rest still load
                ex.day = ex.day or folder.name
                lines.append(ex)
        newest: dict[tuple[str, str], Example] = {}
        for ex in lines:
            newest[(ex.day, ex.id)] = ex
        kept: dict[str, list[tuple[str, str]]] = {}
        for ref, ex in sorted(newest.items(), key=lambda item: item[1].at, reverse=True):
            group = kept.setdefault(ex.key, [])
            if len(group) < MAX_EXAMPLES:
                group.append(ref)
        keep = {ref for refs in kept.values() for ref in refs}
        self.examples = sorted((ex for ex in lines if (ex.day, ex.id) in keep), key=lambda e: e.at)

    def _current(self) -> None:
        """The user's examples are kept until changed, whatever the date or the session: nothing to reload. (After
        midnight new ones go to the new date's folder.)"""
        return None

    # --- state ---------------------------------------------------------------------------------
    def model(self) -> str:
        """The embedding model (option EMBED)."""
        settings = getattr(self.hub, "settings", None)
        return getattr(settings, "embed", DEFAULT_MODEL) or DEFAULT_MODEL

    def _taught(self) -> list[Example]:
        """One example per date and id (the newest line of it), whatever model embedded it."""
        by_id: dict[tuple[str, str], Example] = {}
        for ex in self.examples:
            by_id[(ex.day, ex.id)] = ex
        return list(by_id.values())

    def counts(self) -> dict[str, int]:
        """Examples per name (the newest spelling of each), as many as are matched against."""
        self._current()
        groups: dict[str, list[Example]] = {}
        for ex in self._taught():
            groups.setdefault(ex.key, []).append(ex)
        return {max(g, key=lambda e: e.at).label: min(len(g), MAX_EXAMPLES) for g in groups.values()}

    def ready(self) -> bool:
        """At least one name has enough examples to match against."""
        return any(n >= MIN_EXAMPLES for n in self.counts().values())

    def on(self) -> bool:
        settings = getattr(self.hub, "settings", None)
        return getattr(settings, "taught", "on") == "on"

    def remember(self, object_id: str, crop_jpeg: bytes, vector: list[float] | None = None, model: str | None = None) -> None:
        """The crop (and vector) of an outline as it appeared: teaching it later needs no frame. A
        vector already known (seen.py embedded it) is not dropped for a crop without one."""
        model = model or self.model()
        known = self.recent.get(object_id)
        if vector is None and known is not None and known[1] is not None:
            crop_jpeg, vector, model = known
        self.recent[object_id] = (crop_jpeg, vector, model)
        self.recent.move_to_end(object_id)
        while len(self.recent) > REMEMBERED:
            self.recent.popitem(last=False)

    def _remembered_vector(self, object_id: str, model: str) -> tuple[bytes, list[float]] | None:
        known = self.recent.get(object_id)
        if known is None or known[1] is None or known[2] != model:
            return None
        return known[0], known[1]

    # --- teaching ------------------------------------------------------------------------------
    async def vectors(self, jpegs: list[bytes], model: str | None = None) -> list[list[float]]:
        if self.embed is None:
            raise RuntimeError("there is no embedding model here")
        return await self.embed(jpegs, model=model or self.model())

    async def teach(self, label: str, object_id: str, crop_jpeg: bytes | None = None, frame_id: int | None = None,
                    source: str = "named") -> Example:
        """Keep one example of `label`: the outline's crop as it appeared (or `crop_jpeg`, or one
        cut now from the frames still here). Raises ValueError, in words, when there is nothing
        to keep."""
        self._current()
        model = self.model()
        label = " ".join(str(label or "").split())[:60]
        if not label_key(label):
            raise ValueError("there is no name to keep")
        remembered = self.recent.get(object_id)
        jpeg = crop_jpeg or (remembered[0] if remembered else None)
        if jpeg is None and object_id in self.sources:
            cut = await asyncio.to_thread(cut_crop, *self.sources[object_id], hub=self.hub)
            if cut is not None:
                jpeg, frame_id = cut[0], frame_id if frame_id is not None else cut[1]
        if jpeg is None and self.hub is not None and object_id in self.hub.scene.objects:
            cut = object_crop(self.hub, self.hub.scene.objects[object_id])
            if cut is not None:
                jpeg, frame_id = cut[0], frame_id if frame_id is not None else cut[1]
        if jpeg is None:
            raise ValueError("its picture is no longer here (the slide moved on)")
        known = self._remembered_vector(object_id, model)
        vector = known[1] if known is not None and jpeg is known[0] else None
        if vector is None:
            try:
                vector = (await self.vectors([jpeg], model))[0]
            except Exception as exc:
                raise ValueError(f"the picture could not be read ({exc})") from exc
        folder = self.folder()
        folder.mkdir(parents=True, exist_ok=True)
        on_disk = [int(p.stem[1:]) for p in folder.glob("t*.jpg") if p.stem[1:].isdigit()]
        n = 1 + max([*(int(e.id[1:]) for e in self.examples if e.day == folder.name and e.id[1:].isdigit()), *on_disk],
                    default=0)
        ex = Example(id=f"t{n}", label=label, key=label_key(label), vector=[float(v) for v in vector], crop=f"t{n}.jpg",
                     object_id=object_id, frame_id=frame_id, source=source, model=model, day=folder.name,
                     session=self.session() or "")
        (folder / ex.crop).write_bytes(jpeg)
        self._append(ex)
        self.log({"kind": "taught", "label": label, "objectId": object_id, "frameId": frame_id, "source": source,
                  "examples": self.counts().get(label, 0), "example": ex.id, "model": model})
        return ex

    def _append(self, ex: Example) -> None:
        folder = self.folder(ex.day or None)  # its own date's file (a re-embedding of an older one too)
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "examples.jsonl").open("a") as f:
            f.write(json.dumps(asdict(ex)) + "\n")
        self.examples.append(ex)

    def examples_of(self, label: str) -> int:
        key = label_key(label)
        return min(sum(1 for e in self._taught() if e.key == key), MAX_EXAMPLES)

    async def refresh(self, model: str | None = None) -> int:
        """Embed again, with `model`, the day's examples it has no vector for (EMBED changed since
        they were taught), from their crops, in one call; kept beside the old vectors under the same
        id. Shared by everyone who asks meanwhile. Returns how many were embedded."""
        model = model or self.model()
        running = self._refreshing.get(model)
        if running is not None:
            return await asyncio.shield(running)
        have = {(e.day, e.id) for e in self.examples if e.model == model}
        todo = [e for e in self._taught() if (e.day, e.id) not in have]
        if not todo:
            return 0
        from .options import unrelated

        future = unrelated(self._refresh(model, todo))
        self._refreshing[model] = future
        future.add_done_callback(lambda f: self._refreshing.pop(model, None))
        return await asyncio.shield(future)

    async def _refresh(self, model: str, todo: list[Example]) -> int:
        def read():
            return [(ex, (self.folder(ex.day or None) / ex.crop).read_bytes()) for ex in todo
                    if ex.crop and (self.folder(ex.day or None) / ex.crop).exists()]

        crops = await asyncio.to_thread(read)
        if not crops:
            return 0
        vectors = await self.vectors([jpeg for _, jpeg in crops], model)
        for (ex, _), vector in zip(crops, vectors):
            self._append(Example(**{**asdict(ex), "vector": [float(v) for v in vector], "model": model}))
        self.log({"kind": "taught-refresh", "model": model, "examples": len(crops)})
        return len(crops)

    # --- matching ------------------------------------------------------------------------------
    def match_vector(self, vector, model: str | None = None) -> Match | None:
        self._current()
        return nearest(vector, self.examples, model=model or self.model())

    async def match(self, object_id: str, crop_jpeg: bytes) -> Match | None:
        """Embed one outline's crop (kept for teaching it later) and compare it with the
        examples; None when matching is off or no name has enough examples (then nothing is
        embedded)."""
        if not self.on() or not self.ready():
            self.remember(object_id, crop_jpeg)
            return None
        model = self.model()
        vector = (await self.vectors([crop_jpeg], model))[0]
        self.remember(object_id, crop_jpeg, vector, model)
        await self.refresh(model)
        return self.match_vector(vector, model)

    def watch(self, scene) -> None:
        """Crop (and, while TAUGHT is on, match) every outline as it is added."""
        scene.on_change.append(self._scene_changed)

    def _scene_changed(self, op: str, ids) -> None:
        if op != "add":
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        matching = self.on() and self.ready()
        for oid in ids:
            obj = self.hub.scene.objects.get(oid) if self.hub is not None else None
            if obj is None or obj.kind not in ("highlight", "part") or obj.trial is not None or not obj.polygons:
                continue
            if matching:
                self.match_object(oid)
            else:
                # Nothing to match yet: only where its picture is (no decoding), to teach it later.
                where = crop_source(self.hub, obj)
                if where is not None:
                    self.sources[oid] = where
                    self.sources.move_to_end(oid)
                    while len(self.sources) > REMEMBERED:
                        self.sources.popitem(last=False)

    def match_object(self, object_id: str) -> asyncio.Future:
        """The outline's match (a future, shared: naming a click awaits the one its outline
        started); it lands on the outline (`taught`), shown apart from the model's name."""
        running = self._matching.get(object_id)
        if running is not None:
            return running
        from .options import unrelated

        future = unrelated(self._match_object(object_id))  # not for the request that outlined it (contract 7)
        self._matching[object_id] = future
        future.add_done_callback(lambda f: self._matching.pop(object_id, None))
        return future

    async def _match_object(self, object_id: str) -> Match | None:
        obj = self.hub.scene.objects.get(object_id)
        revision = getattr(obj, "revision", 0) if obj is not None else None
        # Where its crop comes from, decided before anything is awaited (ADR 0010).
        where = crop_source(self.hub, obj) if obj is not None and obj.polygons else None
        model = self.model()
        try:
            if self.on() and self.ready():
                # One embedding per outline, shared with seen.py ("seen one like this before today?").
                got, _ = await asyncio.gather(asyncio.shield(self.object_vectors([object_id], model)[object_id]),
                                              self.refresh(model))
                cut = (got[0], got[2]) if got is not None else None
                match = self.match_vector(got[1], got[3]) if got is not None else None
            else:
                # Decoding the frame and cutting the crop run off the event loop: frames and clicks go first.
                cut = await asyncio.to_thread(cut_crop, *where, hub=self.hub) if where is not None else None
                if cut is None:
                    return None
                match = await self.match(object_id, cut[0])
        except Exception as exc:  # no worker, a busy worker: naming goes on without it
            self.log({"kind": "taught-match", "objectId": object_id, "error": str(exc)[:200]})
            return None
        if match is not None:
            from .apply import apply

            # A fact beside the outline's name, through the one gate (apply.py): only while the
            # outline is still there.
            from .tools import outline_scope

            await apply(self.hub, lambda _: self.hub.scene.update(object_id, taught=match.wire()),
                        scope=outline_scope(obj.source) if obj is not None else "outline:user",
                        target=(object_id, revision))
            self.log({"kind": "taught-match", "objectId": object_id, "frameId": cut[1], **match.wire(),
                      "next": match.next_label, "nextSimilarity": match.next_similarity, "model": match.model})
        return match

    def object_vectors(self, ids: list[str], model: str | None = None) -> dict[str, asyncio.Future]:
        """Each outline's (crop JPEG, vector, frame id, model), or None when its picture is gone: one
        embedding per outline and model, whoever asks first (matching here, seen.py), and the
        outlines asked for together go to the heavy worker in one call. Futures; a failed embedding
        raises."""
        model = model or self.model()
        loop = asyncio.get_running_loop()
        out: dict[str, asyncio.Future] = {}
        todo = []
        for oid in dict.fromkeys(ids):
            known = self._remembered_vector(oid, model)
            if (oid, model) in self._vectors:
                out[oid] = self._vectors[(oid, model)]
            elif known is not None:
                out[oid] = loop.create_future()
                out[oid].set_result((known[0], known[1], None, model))
            else:
                todo.append(oid)
        if todo:
            batch = asyncio.ensure_future(self._embed_objects(todo, model))

            async def pick(oid: str):
                return (await batch).get(oid)

            for oid in todo:
                future = asyncio.ensure_future(pick(oid))
                self._vectors[(oid, model)] = out[oid] = future
                future.add_done_callback(partial(self._vector_done, (oid, model)))
        return out

    def _vector_done(self, key: tuple[str, str], future: asyncio.Future) -> None:
        self._vectors.pop(key, None)
        if not future.cancelled():
            future.exception()  # a failure is the asker's to handle (and log), not "never retrieved"

    async def _embed_objects(self, ids: list[str], model: str) -> dict:
        wheres = {}
        for oid in ids:
            obj = self.hub.scene.objects.get(oid) if self.hub is not None else None
            remembered = self.recent.get(oid)
            if remembered is not None:
                wheres[oid] = remembered[0]  # its crop as it appeared
            elif obj is not None and obj.polygons:
                where = crop_source(self.hub, obj) or self.sources.get(oid)
                if where is not None:
                    wheres[oid] = where

        def cut_all():  # decoding frames off the event loop: frames and clicks go first
            return {oid: (w, None) if isinstance(w, bytes) else cut_crop(*w, hub=self.hub) for oid, w in wheres.items()}

        cuts = {oid: c for oid, c in (await asyncio.to_thread(cut_all)).items() if c is not None}
        if not cuts:
            return {}
        order = list(cuts)
        vectors = await self.vectors([cuts[oid][0] for oid in order], model)
        out = {}
        for oid, vector in zip(order, vectors):
            self.remember(oid, cuts[oid][0], vector, model)
            out[oid] = (cuts[oid][0], vector, cuts[oid][1], model)
        return out

    async def warm(self) -> None:
        """Load the model before the first click needs it, when there is something to match."""
        if self.on() and self.ready() and self.embed is not None:
            try:
                await self.vectors([encode(np.zeros((CROP_SIDE, CROP_SIDE, 3), np.uint8))])
                await self.refresh()
            except Exception as exc:
                self.log({"kind": "taught-warm", "error": str(exc)[:200]})


async def gpu_embed(hub, jpegs: list[bytes], model: str = DEFAULT_MODEL) -> list[list[float]]:
    """The embedding model (option EMBED) on the heavy GPU worker (started on first use), so a click
    never waits behind it. The first call of a model loads it (BioCLIP 2: 1.7 GB, several seconds)."""
    heavy = hub.gpu_heavy if getattr(hub, "gpu_heavy", None) is not None else getattr(hub, "gpu", None)
    if heavy is None:
        raise RuntimeError("no GPU worker")
    heavy = await heavy.ensure_started()
    # Background work nobody waits on (taught labels, things seen before): its model's load is measured, not said.
    result = await heavy.call("embed", timeout=60.0, jpegs=jpegs, model=model, _quiet=True)
    if result.get("model", model) != model:
        raise RuntimeError(f"the worker embedded with {result.get('model')}, not {model}")
    return result["vectors"]


def crop_source(hub, obj) -> tuple[bytes, tuple[float, float, float, float], int] | None:
    """Where an outline's crop comes from: the view it was made on (its origin capture, while the
    request that made it holds it) and its box there; else its anchor frame; else, once those have
    left the buffer, the newest frame (box moved with the slide) when the slide has not lost track
    since (same motion epoch). None otherwise. Bytes and box are taken together on the loop, so a
    worker thread cutting the crop never looks a frame up again (ADR 0010)."""
    from .capture import captures

    store = captures(hub)
    origin = store.get(obj.origin) if getattr(obj, "origin", None) is not None else None
    if origin is not None and origin.jpeg and getattr(obj, "origin_polygons", None):
        pts = [p for poly in obj.origin_polygons for p in poly]
        bbox = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
        return origin.jpeg, bbox, origin.id
    frame = hub.source.get(obj.anchor_frame)
    bbox = obj.bbox
    frame_id = obj.anchor_frame
    if frame is None:
        info = hub.latest_info
        if info is None or info.epoch != obj.anchor_epoch:
            return None
        frame = hub.source.get(info.id)
        if frame is None:
            return None
        sx, sy = info.dx - obj.anchor_offset[0], info.dy - obj.anchor_offset[1]
        bbox = (bbox[0] + sx, bbox[1] + sy, bbox[2] + sx, bbox[3] + sy)
        frame_id = info.id
    return frame.jpeg, bbox, frame_id


def cut_crop(jpeg: bytes, bbox, frame_id: int, hub=None) -> tuple[bytes, int] | None:
    """The crop around `bbox` of the camera picture `jpeg`. With `hub`, the full picture is decoded in
    room reserved for it (invariant 3; the camera's size from the latest frame); Refused when none."""
    from contextlib import nullcontext

    from .capture import reserving

    info = getattr(hub, "latest_info", None) if hub is not None else None
    room = (reserving(hub, getattr(info, "width", 0) or 0, getattr(info, "height", 0) or 0, "look",
                      "cutting an outline's crop") if hub is not None else nullcontext())
    with room:
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        crop = crop_for(image, bbox) if image is not None else None
        return (encode(crop), frame_id) if crop is not None else None


def object_crop(hub, obj) -> tuple[bytes, int] | None:
    """The outline's crop (JPEG) and the frame it was cut from, or None (crop_source)."""
    where = crop_source(hub, obj)
    return cut_crop(*where, hub=hub) if where is not None else None
