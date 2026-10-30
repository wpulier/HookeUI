"""Classifying (Part 2.5, W5): one model names each member of a population from a list of classes (operation
`classify`, operations.CLASSIFY), so several models' names for the same cells can be compared from their records
(model_comparison.py). Will, 29 October: "Have a researcher run 5 different classifying models… report comparing."

- **What classifies:** every vision model a person can choose for looking (options.VISION: Gemini, Claude, GPT; in the
  cloud) and MedSigLIP 448 (this Mac's zero-shot model, the one `names` uses for look groups). Each is an
  implementation of `classify`, run by the runner (operations.Runner), so each run is an Execution with its time,
  attempts and what ran; a run with the same inputs is one job, shared.
- **The same cells, the same classes:** a run names the members it is given (at most MAX_MEMBERS of a population, the
  same ones for every model of a comparison), from the classes given, else the subject's candidate list
  (zeroshot.CANDIDATES: a blood or a Pap smear), else, for the vision models only, in their own words ("open"). Every
  model may also say "unsure" or "none of these"; that is kept as said, never mapped to a class.
- **Facts, not verdicts:** the vision models see each member's crop as a numbered tile of a contact sheet the app draws
  (numbers drawn by app code, the crops as taught.py cuts them) and judge it themselves; MedSigLIP ranks the classes per
  crop and its first class is its label, with its share. Code never renames a label, and no label is written into an
  outline.
- **Cost from the record:** a cloud model's spend is its reported tokens at the list price (research_worker.budget
  PRICES, before discounts; fast mode twice), or "unavailable" when the provider reported none; a model on this Mac
  costs nothing. Both are kept in the run's output with their basis.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
from typing import Any

MAX_MEMBERS = 60  # members named in one run (every model of a comparison names the same ones)
PER_SHEET = 20  # tiles in one picture a vision model is shown
TILE = 160  # px, one member's crop on a sheet
# A sheet's answer: room for a model's own thinking (counted in its output by OpenAI and Gemini), then ~50 a tile.
REPLY_TOKENS = 600
UNSURE, NONE_OF_THESE = "unsure", "none of these"
OPEN = "open"  # no classes given and no subject list: the vision models name each in their own words
MAX_CLASSES = 24
MEDSIGLIP_TEMPLATE = "a microscopy image of a {}"  # a list the user gave, in a neutral sentence (zeroshot.EVERYTHING's)
ON_THIS_MAC = "medsiglip-448"  # MedSigLIP 448, the one classifier on this Mac (its catalog entry: medsiglip)


# --- what can classify -------------------------------------------------------------------------------------------
def implementations() -> tuple:
    """`classify`'s implementations (operations.CLASSIFY): each vision model a person can choose (its id and name as
    the vision option's: one name per model), then MedSigLIP on this Mac. Read once, when operations is imported."""
    from .operations import _model
    from .options import VISION

    vlms = tuple(_model(o.id, ("members",), "cloud", o.id, model=o.model, label=o.label,
                        note="names each member's crop on a numbered sheet; may say unsure or none of these")
                 for o in VISION.values() if o.model)
    local = _model(ON_THIS_MAC, ("members",), "analysis", ON_THIS_MAC, model="medsiglip",
                   note="its first-ranked class for each member's crop, with its share; needs a list of classes")
    return (*vlms, local)


def classes_for(hub, given) -> tuple[list[str], str, str, str]:
    """(classes, vocabulary, MedSigLIP's sentence, what its first class has been worth): the classes given ("given"),
    else the subject's candidate list ("subject", zeroshot.CANDIDATES), else none ("open")."""
    from .brain.registry import ToolFailed
    from .zeroshot import candidates_for

    if given:
        if not isinstance(given, (list, tuple)) or not all(isinstance(c, str) for c in given):
            raise ToolFailed("classes are a list of names", next="say the classes as a list of words")
        names = list(dict.fromkeys(" ".join(c.split())[:60] for c in given if c.strip()))
        if not 2 <= len(names) <= MAX_CLASSES:
            raise ToolFailed(f"a list of 2 to {MAX_CLASSES} classes is needed to classify", next="name the classes")
        return names, "given", MEDSIGLIP_TEMPLATE, "not measured for this list"
    subject = (getattr(getattr(hub, "scene", None), "context", None) or {}).get("specimen")
    candidates = candidates_for(subject, "subject")
    if candidates is not None:
        return list(candidates.names), "subject", candidates.template, candidates.calibration
    return [], OPEN, "", ""


def chosen_members(population, limit: int = MAX_MEMBERS) -> list:
    """The members a comparison names: the population's live ones, in their order, at most `limit`."""
    return list(population.live())[:limit]


# --- running it through the runner -------------------------------------------------------------------------------
def prompt_of(population):
    from .operations import Prompt

    return Prompt("members", text=f"{population.id}@r{population.revision}")


async def run(hub, population, choice: str, *, classes: list[str], vocabulary: str, members: list, template: str = "",
              calibration: str = "", origin: str = "compare", policy: str = "raw", made_for: str | None = None,
              priority: str | None = None):
    """One model's names for `members` of `population`, through the runner (an Execution of its own): its Result."""
    from .operations import runner
    from .population import capture_of

    params = {"population": population.id, "revision": population.revision,
              "members": json.dumps([m.id for m in members]), "classes": json.dumps(classes), "vocabulary": vocabulary,
              "template": template, "calibration": calibration}
    if priority:
        params["priority"] = priority
    return await runner(hub).run("classify", capture_of(hub, population), prompt_of(population), choice=choice,
                                 policy=policy, origin=origin, params=params, made_for=made_for)


# --- the vision models ---------------------------------------------------------------------------------------------
_CLIENTS: dict[tuple, Any] = {}


def vision_for(option_id: str):
    """A vision client for `option_id` where the hub has none of its own (the app gives it the agent's, `hub.vision_for`:
    a stand-in hub, a bench), made once per key: a key saved since makes a new one. A test replaces it."""
    from .agent import key_needed
    from .options import VISION
    from .vision import make_vision

    option = VISION[option_id]
    failed = key_needed(option_id)
    if failed is not None:
        raise failed
    key = (option_id, hashlib.sha256(os.environ.get(option.needs, "").encode()).hexdigest()[:16] if option.needs else "")
    if key not in _CLIENTS:
        _CLIENTS[key] = make_vision(option.spec)
    return _CLIENTS[key]


def sheets(crops: list[tuple[int, bytes]], per_sheet: int = PER_SHEET, tile: int = TILE) -> list[tuple[bytes, list[int]]]:
    """Contact sheets: each crop a tile with its number drawn in its corner by the app, `per_sheet` to a picture.
    [(jpeg, the numbers on it)]."""
    import cv2
    import numpy as np

    out = []
    for start in range(0, len(crops), per_sheet):
        chunk = crops[start:start + per_sheet]
        cols = max(1, math.ceil(math.sqrt(len(chunk))))
        rows = math.ceil(len(chunk) / cols)
        sheet = np.full((rows * tile, cols * tile, 3), 32, np.uint8)
        for i, (n, jpeg) in enumerate(chunk):
            image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                continue
            image = cv2.resize(image, (tile - 4, tile - 4), interpolation=cv2.INTER_AREA)
            r, c = divmod(i, cols)
            y, x = r * tile + 2, c * tile + 2
            sheet[y:y + tile - 4, x:x + tile - 4] = image
            label = str(n)
            (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(sheet, (x, y), (x + w + 8, y + h + 10), (255, 255, 255), -1)
            cv2.putText(sheet, label, (x + 4, y + h + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2, cv2.LINE_AA)
        ok, jpeg = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if ok:
            out.append((jpeg.tobytes(), [n for n, _ in chunk]))
    return out


def ask_words(numbers: list[int], classes: list[str], vocabulary: str, specimen: str | None) -> str:
    """What a vision model is asked about one sheet: the facts (what the tiles are, the classes) and the answer's form;
    the model judges each tile."""
    where = f" of {specimen}" if specimen else ""
    head = (f"This picture is a contact sheet of {len(numbers)} numbered tiles, cut by the app from one microscope "
            f"view{where}: each tile is one segmented thing (usually a cell), centred, with its number in the top-left "
            f"corner (tiles {numbers[0]} to {numbers[-1]}).")
    if vocabulary == OPEN:
        task = ("Name what each tile shows in your own words, two or three (the kind of cell or thing), or say "
                "\"unsure\" when you can't tell.")
    else:
        task = ("Classify each tile as exactly one of these classes: " + "; ".join(classes)
                + f". If it is none of them, say \"{NONE_OF_THESE}\"; if you can't tell, say \"{UNSURE}\".")
    return (f"{head} {task} Judge each tile on its own pixels. Reply with JSON only: "
            '{"tiles": [{"n": <tile number>, "class": "<class>", "sure": true|false}, ...]}, one entry per tile.')


def _norm(text) -> str:
    return " ".join(str(text or "").lower().replace("_", " ").split()).strip(" .")


def label_of(said, classes: list[str]) -> str:
    """A model's class for a tile as it said it: one of the classes when it is that class's own words (case and spaces
    aside), "unsure"/"none of these" as said, else its own words lowercased (never mapped to a class by code)."""
    said = _norm(said)
    by = {_norm(c): c for c in classes}
    if said in by:
        return by[said]
    if said in (UNSURE, "unknown", "not sure", "cannot tell", "can't tell"):
        return UNSURE
    if said in (NONE_OF_THESE, "none", "other", "none of them"):
        return NONE_OF_THESE
    return said[:60]


def price_of(model: str, usage: dict, fast: bool = False) -> float | None:
    """A cloud call's spend from its reported tokens at the list price (None when either is unknown)."""
    from research_worker.budget import PRICES

    price = PRICES.get(model)
    tokens_in, tokens_out = usage.get("input"), usage.get("output")
    if not isinstance(price, dict) or "in" not in price or not isinstance(tokens_in, int) or not isinstance(tokens_out, int):
        return None
    thinking = usage.get("thinking") if isinstance(usage.get("thinking"), int) else 0
    return (tokens_in * price["in"] + (tokens_out + thinking) * price["out"]) / 1e6 * (2 if fast else 1)


def _members(run, params) -> tuple[Any, list]:
    from .brain.registry import ToolFailed
    from .enrichment import _population

    population = _population(run, params)
    wanted = json.loads(params.get("members") or "[]")
    live = {m.id: m for m in population.live()}
    members = [live[m] for m in wanted if m in live]
    if not members:
        raise ToolFailed(f"none of {population.title}'s members asked for are there now", next="outline the cells again")
    return population, members


def register() -> None:
    from .brain.registry import ToolFailed
    from .operations import OPERATIONS, InputUnread, adapter

    vlms = [i.id for i in OPERATIONS["classify"].implementations if i.worker == "cloud"]

    @adapter("classify", *vlms)
    async def _vision(run, capture, prompt, impl, params) -> dict:
        from .enrichment import member_crops

        population, members = _members(run, params)
        classes = json.loads(params.get("classes") or "[]")
        vocabulary = params.get("vocabulary") or (OPEN if not classes else "given")
        crops = await asyncio.to_thread(member_crops, run.hub, population, members)
        if not crops:
            raise InputUnread(f"{population.title}'s view can't be read now", next="ask again in a moment")
        numbered = [(n, m.id) for n, m in enumerate(members, 1) if m.id in crops]
        pictures = await asyncio.to_thread(sheets, [(n, crops[mid]) for n, mid in numbered])
        by_number = dict(numbered)
        specimen = (getattr(getattr(run.hub, "scene", None), "context", None) or {}).get("specimen")
        client = (getattr(run.hub, "vision_for", None) or vision_for)(impl.option)
        labels: dict[str, dict] = {}
        usage = {"input": 0, "output": 0}
        cost, priced, calls = 0.0, True, 0
        for jpeg, numbers in pictures:
            result = await client.ask([jpeg], ask_words(numbers, classes, vocabulary, specimen),
                                      max_output_tokens=REPLY_TOKENS + 50 * len(numbers))
            calls += 1
            if result.error or not isinstance(result.data, dict):
                why = result.error.split(": ", 1)[-1][:160] if result.error else "its answer could not be read"
                raise ToolFailed(f"{impl.label} did not classify them ({why})", next="try it again, or compare without it")
            for k in ("input", "output", "thinking"):
                if isinstance(result.usage.get(k), int):
                    usage[k] = usage.get(k, 0) + result.usage[k]
            spent = price_of(result.model, result.usage, fast=impl.option.endswith("-fast"))
            cost, priced = (cost + spent, priced) if spent is not None else (cost, False)
            rows = result.data.get("tiles") or result.data.get("labels") or []
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                try:
                    n = int(row.get("n"))
                except (TypeError, ValueError):
                    continue
                if n in numbers and by_number.get(n) and by_number[n] not in labels:
                    sure = row.get("sure")
                    labels[by_number[n]] = {"label": label_of(row.get("class") or row.get("label"), classes),
                                            **({"sure": bool(sure)} if isinstance(sure, bool) else {})}
        unanswered = [m.id for m in members if m.id not in labels]
        return {"labels": labels, "unanswered": unanswered, "classes": classes, "vocabulary": vocabulary,
                "model": impl.option, "implementation": impl.id, "calls": calls, "usage": usage,
                "costUsd": round(cost, 6) if priced else None,
                "costBasis": ("reported tokens at the list price, before discounts" if priced
                              else "the provider reported no usage, or the model has no list price here"),
                "producer": {"implementation": impl.id, "model": impl.version}}

    @adapter("classify", ON_THIS_MAC)
    async def _medsiglip(run, capture, prompt, impl, params) -> dict:
        from .enrichment import member_crops
        from .operations import started, worker_client

        population, members = _members(run, params)
        classes = json.loads(params.get("classes") or "[]")
        if not classes:
            raise ToolFailed(f"{impl.label} ranks a list of classes, and none was given or known for this specimen",
                             next="name the classes to classify into")
        crops = await asyncio.to_thread(member_crops, run.hub, population, members)
        if not crops:
            raise InputUnread(f"{population.title}'s view can't be read now", next="ask again in a moment")
        client = await started(worker_client(run.hub, "analysis", model="medsiglip"))
        if client is None:
            raise ToolFailed("no GPU worker here for MedSigLIP", next="compare without it")
        template = params.get("template") or MEDSIGLIP_TEMPLATE
        ids = [m.id for m in members if m.id in crops]
        labels = {}
        for i in range(0, len(ids), 32):
            batch = ids[i:i + 32]
            reply = await client.call("zeroshot", timeout=60.0, priority=params.get("priority"),
                                      jpegs=[crops[m] for m in batch], names=classes, template=template)
            for mid, ranked in zip(batch, reply.get("rankings") or []):
                best = max(ranked, key=lambda row: row[1]) if ranked else None
                if best is not None:
                    labels[mid] = {"label": best[0], "share": round(float(best[1]), 3)}
        return {"labels": labels, "unanswered": [m.id for m in members if m.id not in labels], "classes": classes,
                "vocabulary": params.get("vocabulary") or "given", "model": ON_THIS_MAC, "implementation": impl.id,
                "calls": math.ceil(len(ids) / 32), "costUsd": 0.0, "costBasis": "runs on this Mac",
                "calibration": params.get("calibration") or "not measured for this list", "template": template,
                "producer": {"implementation": impl.id, "model": impl.version}}


def said_labels(labels: dict) -> str:
    """A run's labels in a few words, most common first ("12 neutrophil, 3 lymphocyte, 1 unsure")."""
    counts: dict[str, int] = {}
    for row in labels.values():
        counts[row["label"]] = counts.get(row["label"], 0) + 1
    return ", ".join(f"{n} {name}" for name, n in sorted(counts.items(), key=lambda t: (-t[1], t[0])))


