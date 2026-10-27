"""Versions of what a researcher made.

Every output has an `id` (stable within its researcher: "o1", "o2"...), a `version` (1, 2...),
`replaces` (the id of the output it is a new version of, or None) and `change` (what the user
asked to change, or None). An output and everything that replaces it, transitively, is one
line; the newest of a line is its current version. A revision always replaces the current
version of its line, so lines never branch. Outputs saved before versions existed load as
version 1 of a line of their own (`number`).

No imports from the rest of the app but the shared numbers (contract.py): the manager, the
presenter and the brain's tools all use this.
"""
from __future__ import annotations

import re

from .contract import KIND_ORDER  # html, video, image: most substantial first

KIND_NAMES = {"html": "lesson", "video": "video", "image": "picture", "presentation": "presentation"}
# How people name each kind of thing a researcher makes: one table for the revise target (manager.target_output) and
# for finding a researcher by what it made (desk.py). Only a fallback: the model names an output by its id (o2) or a
# researcher by its number, and a word match is logged as an override.
KIND_WORDS = {
    "lesson": frozenset({"lesson", "quiz", "test", "questions", "question", "worksheet", "walkthrough", "html"}),
    "video": frozenset({"video", "clip", "movie", "animation"}),
    "labelled picture": frozenset({"labels", "label", "labelled", "labeled", "annotated", "annotation"}),
    "illustration": frozenset({"illustration", "drawing", "sketch"}),
    "picture": frozenset({"picture", "image", "photo", "diagram"}),  # any picture it made
    "presentation": frozenset({"presentation", "slides", "deck"}),
    "notes": frozenset({"notes", "report", "research", "findings", "summary", "answer"}),
}
DELIVERABLE_KIND = {"lesson": "lesson", "video": "video", "annotated_image": "labelled picture", "image": "illustration",
                    "research": "notes", "report": "notes", "answer": "notes"}


def _index(oid: object) -> int:
    m = re.fullmatch(r"o(\d+)", str(oid or ""))
    return int(m.group(1)) if m else 0


def number(outputs: list[dict]) -> list[dict]:
    """Give outputs that have none an id, version 1, no `replaces` and no `change` (in place)."""
    used = {o.get("id") for o in outputs if o.get("id")}
    n = max([_index(i) for i in used] + [0])
    for o in outputs:
        if not o.get("id"):
            n += 1
            while f"o{n}" in used:
                n += 1
            o["id"] = f"o{n}"
            used.add(o["id"])
        o.setdefault("version", 1)
        o.setdefault("replaces", None)
        o.setdefault("change", None)
    return outputs


def next_id(outputs: list[dict]) -> str:
    return f"o{max([_index(o.get('id')) for o in outputs] + [0]) + 1}"


def find(outputs: list[dict], oid: object) -> dict | None:
    return next((o for o in outputs if oid and o.get("id") == oid), None)


def line(outputs: list[dict], oid: object) -> list[dict]:
    """The versions of the output `oid` belongs to, oldest first ([] when there is none)."""
    by_id = {o["id"]: o for o in outputs if o.get("id")}
    first = by_id.get(oid)
    if first is None:
        return []
    seen = {first["id"]}
    while first.get("replaces") in by_id and first["replaces"] not in seen:
        first = by_id[first["replaces"]]
        seen.add(first["id"])
    after = {}
    for o in outputs:
        if o.get("replaces") and o.get("id") and not o.get('conflict'):
            after.setdefault(o["replaces"], o)  # a line never branches; the first one wins if it did
    chain = [first]
    while chain[-1]["id"] in after and after[chain[-1]["id"]]["id"] not in {c["id"] for c in chain}:
        chain.append(after[chain[-1]["id"]])
    return chain


def head(outputs: list[dict], oid: object) -> dict | None:
    """The current version of the line `oid` belongs to."""
    found = line(outputs, oid)
    return found[-1] if found else None


def replaced_ids(outputs: list[dict]) -> set:
    return {o.get("replaces") for o in outputs if o.get("replaces") and not o.get('conflict')}


def current(outputs: list[dict]) -> list[dict]:
    """The current version of each line, in the order they were made."""
    gone = replaced_ids(outputs)
    return [o for o in outputs if not o.get('conflict') and not (o.get("id") and o["id"] in gone)]


def older(outputs: list[dict]) -> list[dict]:
    gone = replaced_ids(outputs)
    return [o for o in outputs if o.get('conflict') or (o.get("id") and o["id"] in gone)]


def ordered(outputs: list[dict]) -> list[tuple[dict, bool]]:
    """What a researcher made as it is shown and numbered (presenter.items, read_result):
    current versions first, most substantial kind first and the newest first within a kind,
    then older versions the same way. Pairs of (output, is it current)."""
    position = {id(o): i for i, o in enumerate(outputs)}

    def key(o):
        return KIND_ORDER.get(o.get("kind"), 9), -position[id(o)]

    return ([(o, True) for o in sorted(current(outputs), key=key)]
            + [(o, False) for o in sorted(older(outputs), key=key)])


def kind_name(output: dict) -> str:
    """lesson, video, labelled picture, illustration or picture."""
    kind = output.get("kind")
    if kind == "image":
        if (output.get("content") or {}).get("kind") == "labels":
            return "labelled picture"
        return "illustration" if output.get("generated") else "picture"
    return KIND_NAMES.get(kind, "result")


def kinds_made(outputs: list[dict], deliverable: str | None) -> set[str]:
    """What a researcher made, as kinds (its current outputs' and its deliverable's)."""
    kinds = {kind_name(o) for o in current(outputs)}
    if deliverable in DELIVERABLE_KIND:
        kinds.add(DELIVERABLE_KIND[deliverable])
    if kinds & {"illustration", "labelled picture"}:
        kinds.add("picture")  # "the picture" names any picture it made
    return kinds


def kind_meant(text: str) -> str | None:
    """The kind of thing words name ("the quiz": lesson), by whole words; None when they name none."""
    words = set(re.findall(r"[a-z]+", str(text or "").lower()))
    return next((kind for kind, said in KIND_WORDS.items() if words & said), None)


def fits(output: dict, kind: str) -> bool:
    """An output is of `kind` (a "picture" is any picture it made)."""
    name = kind_name(output)
    return name == kind or (kind == "picture" and output.get("kind") == "image")


def made(outputs: list[dict], deliverable: str | None, nothing: str = "notes") -> str:
    """One word for what it made, most substantial first (a lesson, a video, a picture), else its deliverable's."""
    kinds = {o.get("kind") for o in outputs or []}
    for kind in ("html", "video", "presentation", "image"):
        if kind in kinds:
            return KIND_NAMES[kind]
    return {"lesson": "lesson", "video": "video", "annotated_image": "labelled picture", "image": "picture"}.get(
        deliverable or "", nothing)
