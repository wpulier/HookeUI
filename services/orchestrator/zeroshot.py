"""MedSigLIP's zero-shot ranking of a few names, told to naming a click as one line of data (roadmap
W7, "MedSigLIP: blood-cell zero-shot"; option ZEROSHOT).

MedSigLIP (google/medsiglip-448, gpu_worker/zeroshot.py, on the heavy GPU worker) is a SigLIP model
Google trained on medical images. Given a crop of the clicked thing and a list of names, it says how
well each name fits (a share among those names). Which names: the candidate list of the subject the
session is looking at (CANDIDATES: the cell types of a blood smear, the cells of a Pap smear), found
from the subject's words ("subject"), or every list at once whatever the subject ("always"). The
lists are names to rank, never an answer: the line says what MedSigLIP ranked and how often its first
name has been right (each list's `calibration`, measured), and the vision model decides the name
(docs/design/2026-10-06-facts-not-verdicts.md). Code never writes it into the outline's label.

Measured on the naming bench's 180 cells (bench/zeroshot_bench.py, docs/bench/naming.md "W7:
MedSigLIP"): exact blood-cell names were no better with the line (58% -> 59-61%, moved both ways), coarse
names were (91% -> 95-97%), so the option is "off" by default.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .taught import crop_for, encode

SIDE = 448  # px, what MedSigLIP is given
MARGIN = 1.5  # the crop is this many times the outline's longer side (as taught.py crops)
WAIT_S = 1.5  # naming a click waits at most this long for the ranking (warm: ~0.3 s through the heavy worker)
TOLD = 3  # names told to the model, best first


@dataclass(frozen=True)
class Candidates:
    """Names to rank for one kind of specimen, and the sentence each is put in."""
    id: str
    specimen: str  # in words, for the line ("a blood smear")
    subject: str  # a regular expression over the session's subject: this list is for it
    names: tuple[str, ...]
    template: str  # "a {} in a blood smear"
    calibration: str  # what its first name has been worth, measured (bench/zeroshot_bench.py)


CANDIDATES = (  # the first whose words the subject has: the more specific first ("a Pap smear with blood")
    # Not diagnostic: kinds of cell a person looking names, no grades (the app is not a diagnostic tool).
    Candidates("cervical", "a Pap smear", r"\bpap\b|papanicolaou|cervi[cx]",
               ("superficial squamous cell", "intermediate squamous cell", "parabasal cell", "endocervical cell",
                "neutrophil", "lymphocyte", "red blood cell", "cell debris"),
               "a {} in a Pap smear",
               "on 60 Pap smear cell pictures its first name was a squamous cell 90% of the time, but nearly always "
               "\"parabasal cell\" (54 of 60) whatever the kind of cell"),
    Candidates("blood", "a blood smear", r"\bblood\b|ha?ematolog|leu[ck]ocyte|erythrocyte|\bwbc\b|\brbc\b|platelet",
               ("neutrophil", "eosinophil", "basophil", "lymphocyte", "monocyte", "immature granulocyte",
                "erythroblast", "platelet", "red blood cell"),
               "a {} in a blood smear",
               "on 120 pictures of 8 kinds of blood cell its first name was right 32% of the time (chance 11%; in its "
               "top 3: 53%), never for eosinophils, lymphocytes, monocytes or platelets; it mostly says neutrophil, "
               "immature granulocyte or erythroblast, and its first name's share was the same right or wrong (about "
               "0.17)"),
)
# Mode "always": every list at once, and a few things of other specimens, in a neutral sentence.
EVERYTHING = Candidates(
    "always", "blood smears, Pap smears and other specimens", "",
    tuple(dict.fromkeys([n for c in reversed(CANDIDATES) for n in c.names] + ["plant cell", "pollen grain", "diatom", "protozoan",
                                                                      "air bubble"])),
    "a microscopy image of a {}",
    "on 120 pictures of 8 kinds of blood cell its first name was right 9% of the time (mostly \"immature granulocyte\" "
    "or \"parabasal cell\"); on 60 Pap smear cells it was a squamous cell 97% of the time, nearly always \"parabasal "
    "cell\"")


def by_id(list_id: str) -> Candidates:
    return next(c for c in (*CANDIDATES, EVERYTHING) if c.id == list_id)


def candidates_for(subject: str | None, mode: str) -> Candidates | None:
    """The names to rank for this subject: "subject" (the list whose words the subject has; none for
    another specimen or no subject), "always" (every list), anything else none."""
    if mode == "always":
        return EVERYTHING
    if mode != "subject" or not subject:
        return None
    for c in CANDIDATES:
        if re.search(c.subject, subject, re.I):
            return c
    return None


@dataclass(frozen=True)
class Ranking:
    candidates: Candidates
    ranked: tuple[tuple[str, float], ...]  # every name with its share, best first
    ms: float | None = None

    def wire(self) -> dict:
        return {"model": "medsiglip", "list": self.candidates.id,
                "top": [[name, round(share, 2)] for name, share in self.ranked[:TOLD]]}


def context_line(ranking: Ranking | None) -> str:
    """What naming a click is told: one line of data. The model decides the name."""
    if ranking is None or not ranking.ranked:
        return ""
    c = ranking.candidates
    top = ", ".join(f"{name} {share:.2f}" for name, share in ranking.ranked[:TOLD])
    return (f"MedSigLIP, a medical image model, was given {len(c.names)} names of things in {c.specimen} to choose "
            f"from and no examples (zero-shot); its top {min(TOLD, len(ranking.ranked))} for the close-up, with their "
            f"shares: {top}. Measured: {c.calibration}. A clue, not its name.")


class ZeroShotHint:
    """Ranks the clicked thing's crop on the heavy GPU worker while option ZEROSHOT is on.
    `rank(jpegs, names, template) -> [[[name, share], ...]]` is the worker's `zeroshot` op
    (`gpu_rank`, or a stand-in in tests); `settings()` the hub's settings; `subject()` the session's
    subject (scene.context["specimen"])."""

    def __init__(self, rank, settings: Callable[[], object], subject: Callable[[], str | None], log=None) -> None:
        self._rank = rank
        self.settings = settings
        self.subject = subject
        self.log = log or (lambda record: None)
        self.warmed = False

    def mode(self) -> str:
        option = getattr(self.settings(), "zeroshot", "off")
        return {"medsiglip-subject": "subject", "medsiglip-always": "always"}.get(option, "off")

    def candidates(self) -> Candidates | None:
        return candidates_for(self.subject(), self.mode())

    async def rank_crop(self, image_bgr: np.ndarray, bbox, object_id: str = "", frame_id: int | None = None) -> Ranking | None:
        """The ranking for the thing at `bbox` in `image_bgr`; None when the option is off, no list fits
        the subject, or the picture cannot be cut. A failure raises (the caller logs it)."""
        candidates = self.candidates()
        if candidates is None or self._rank is None:
            return None

        def cut():  # off the event loop: frames and clicks go first
            crop = crop_for(image_bgr, bbox, margin=MARGIN, side=SIDE)
            return encode(crop) if crop is not None else None

        jpeg = await asyncio.to_thread(cut)
        if jpeg is None:
            return None
        t0 = time.perf_counter()
        rows = await self._rank([jpeg], list(candidates.names), candidates.template)
        ms = (time.perf_counter() - t0) * 1000
        ranked = tuple((str(name), float(share)) for name, share in (rows[0] if rows else []))
        ranking = Ranking(candidates, ranked, ms)
        self.log({"kind": "zeroshot", "objectId": object_id, "frameId": frame_id, "ms": round(ms), **ranking.wire()})
        return ranking

    async def warm(self) -> None:
        """Load the model before a click needs it (about 10-20 s the first time): once, and only while
        the option is on and a list fits the subject (a day without blood never loads it). Called at
        start, when the subject changes and when the settings do."""
        if self.warmed or self._rank is None or self.candidates() is None:
            return
        self.warmed = True
        try:
            await self._rank([encode(np.zeros((SIDE, SIDE, 3), np.uint8))], ["cell"], "{}")
        except Exception as exc:
            self.warmed = False
            self.log({"kind": "zeroshot-warm", "error": str(exc)[:200]})


async def gpu_rank(hub, jpegs: list[bytes], names: list[str], template: str) -> list:
    """MedSigLIP on the heavy GPU worker (started on first use), so a click never waits behind it."""
    heavy = hub.gpu_heavy if getattr(hub, "gpu_heavy", None) is not None else getattr(hub, "gpu", None)
    if heavy is None:
        raise RuntimeError("no GPU worker")
    heavy = await heavy.ensure_started()
    result = await heavy.call("zeroshot", timeout=60.0, jpegs=jpegs, names=names, template=template)
    return result["rankings"]
