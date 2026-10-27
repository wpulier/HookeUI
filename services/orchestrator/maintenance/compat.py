"""Whether an update candidate changes something only a person can accept (ADR 0025's list), from declared facts.

Each fact of the candidate's manifest is compared with the active generation's own copy of its manifest. A difference
in any of them is a review item, said in plain words; the adapter, lockfile and code beside them are the fix itself and
are not compared here. The proof decides whether the candidate works; this decides only what needs a person. Nothing
here judges what a model should: it reports which declared fact changed, and from what to what.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from gpu_worker import manifests as catalog


def _needs(values: tuple[str, ...], m: catalog.Manifest) -> tuple[str, ...]:
    return tuple(sorted(v for v in values if v != f"install:{m.id}" and v != f"install:{m.key}"))


def _inputs(m: catalog.Manifest) -> tuple:
    words = {"label", "tooltip"}  # how a setting is named on screen is not what it does
    return tuple(sorted((tuple(sorted((k, repr(v)) for k, v in asdict(i).items() if k not in words)) for i in m.inputs)))


def _outputs(m: catalog.Manifest) -> tuple:
    return tuple(sorted((o.id, o.type) for o in m.outputs))


def _files(m: catalog.Manifest) -> tuple:
    return tuple(sorted((f.get("path", ""), f.get("sha256") or f.get("git_sha1") or "") for f in m.files))


def _component(m: catalog.Manifest) -> tuple:
    c = m.component or {}
    return (c.get("licence", ""), c.get("tier", ""))


def _members(m: catalog.Manifest) -> str:
    from gpu_worker import bundle_contract
    import json
    # Code and proof fixtures may evolve under the existing policy; changing the
    # member's data, permissions, resource allowance or meaning requires review.
    presentation = {'name', 'description', 'search_aliases', 'what', 'category'}
    implementation = {'adapter', 'tests', 'reference_cases', 'reader_checks'}
    values = []
    if not m.members:
        return "[]"
    for member in bundle_contract.parse_members(list(m.members), m):
        wire = {key: value for key, value in member.wire().items() if key not in presentation | implementation}
        wire['inputs'] = [{key: value for key, value in item.items() if key not in {'label', 'tooltip'}}
                          for item in wire['inputs']]
        values.append(wire)
    return json.dumps(values, sort_keys=True, separators=(',', ':'))


# (what it is called, the kind of review, how it is read)
FACTS: tuple[tuple[str, str, Callable[[catalog.Manifest], object]], ...] = (
    ("source", "source", lambda m: (m.channel, m.namespace)),
    ("licence", "rights", lambda m: tuple(m.licence)),
    ("licence words", "rights", lambda m: m.licence_note),
    ("component licence", "rights", _component),
    ("what it needs to get it", "permission", lambda m: _needs(m.needs_to_get, m)),
    ("what it needs to run", "permission", lambda m: _needs(m.needs_to_run, m)),
    ("what the app gives it unasked", "permission", lambda m: tuple(sorted(m.hidden))),
    ("the service it calls", "cost", lambda m: m.service),
    ("where it runs", "hardware", lambda m: m.runs),
    ("what it is made for", "hardware", lambda m: tuple(sorted(m.requires.items()))),
    ("its weights and files", "science", _files),
    ("the job it does", "science", lambda m: (m.kind, m.runner, m.operation or m.id)),
    ("what it implements", "science", lambda m: tuple(sorted((i.operation, i.impl, i.input, i.output) for i in m.implements))),
    ("its settings and defaults", "science", _inputs),
    ("what it gives", "science", _outputs),
    ("member contracts and permissions", "science", _members),
    ("what its outcomes mean", "science", lambda m: tuple(sorted(m.outcomes.items()))),
)


def _said(value: object) -> str:
    if value in ((), "", None):
        return "nothing"
    if isinstance(value, tuple) and all(isinstance(v, str) for v in value):
        return ", ".join(value)
    return str(value) if not isinstance(value, tuple) else "the declared values"


def compare(active: catalog.Manifest, candidate: catalog.Manifest) -> dict:
    """{"compatible": bool, "review": [{"what", "kind", "from", "to", "says"}]}."""
    review = []
    for what, kind, read in FACTS:
        before, after = read(active), read(candidate)
        if before != after:
            review.append({"what": what, "kind": kind, "from": _said(before), "to": _said(after),
                           "says": f"{what} changed from {_said(before)} to {_said(after)}"})
    return {"compatible": not review, "review": review}


def words(review: list[dict]) -> str:
    """What needs review, in one line: "licence changed from MIT to GPL-3.0; its weights and files changed"."""
    out = []
    for r in review:
        out.append(r["says"] if r["from"] != "the declared values" else f"{r['what']} changed")
    return "; ".join(out)
