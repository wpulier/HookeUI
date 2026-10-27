"""What the user is told a request did, from what its steps did (ADR 0006).

A step is **done** only when it has run and worked, **in progress** while it runs or waits for a
new look, and **failed** when it did not work; a failure is always said. The voice is told this
silently as soon as hands has acted (`hands_note`), so it never says "done" for a step still in
progress (live session 2026-10-04: told "cleared 2 outlines", it said "Done" while the highlight
had missed). The findings of a hand-off list the steps still in progress too
(voice/openai_live.findings_text), and when one lands after the voice has replied, the app says
so (announcer.Announcer.late_tool).
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from itertools import islice

from .registry import ToolRun
from ..untrusted import block

DOING = "not done yet: if you speak about it, say it is being done, never that it is done"


def camera_input_limit(required: bool, images) -> str:
    """An available feed or historical camera source is not a supplied current input."""
    supplied = any(pixels and facts.get('channel') == 'specimen-camera' for pixels, facts in images)
    return 'The current camera view was requested but is not supplied to this call.' if required and not supplied else ''


def running(run: ToolRun) -> str:
    """A step still running, with what it is doing when that is said ("highlight (loading micro-sam for the first
    time on this Mac)": loads.py), so a slow step is said to be loading, not left unexplained."""
    return f"{run.tool} ({run.progress})" if run.progress else run.tool


def later_text(later: list[tuple[str, dict]], view=None) -> list[str]:
    """Steps waiting for a new look, in words ("outlining the xylem vessels")."""
    names = {t.id: t.name for t in (getattr(view, "things", None) or [])}
    out = []
    for name, args in later:
        if name == "highlight":
            things = [names.get(str(t), "it") for t in ((args or {}).get("things") or [(args or {}).get("thing")]) if t]
            out.append(f"outlining {', '.join(dict.fromkeys(things)) or 'it'} (it needs a new look first)")
        else:
            out.append(f"{name} (after a new look)")
    return out


def hands_note(runs: list[ToolRun], later: list[tuple[str, dict]], kind=lambda _tool: "effect", view=None,
               needs_look: bool = False) -> str | None:
    """What the voice is told once hands has acted on the user's words, or None if nothing ran.
    `needs_look`: part of the request waits for a new look of the view, so it is still being done
    even when every step hands ran is (a choice of model made, the outline it is for still to come)."""
    # Query tools' results (a quiz step with its answer, a researcher's text) are for the brain's
    # answer, not for the voice to repeat: only say they ran.
    done = [r.summary if kind(r.tool) != "query" else f"{r.tool} done" for r in runs if r.ok and r.summary]
    doing = [running(r) for r in runs if r.ok is None] + later_text(later, view)
    if needs_look and not later and done:
        doing.append("the rest of the request, which needs a new look at the view")
    # A query's results go back to the model, which may still act on them ("find the eye" before its zoom):
    # a finished query is never the request done.
    queried = list(dict.fromkeys(r.tool for r in runs if r.ok and kind(r.tool) == "query"))
    if queried and not doing:
        doing.append(f"the rest of the request, from what {' and '.join(queried)} found")
    failed = [r.summary or r.tool for r in runs if r.ok is False]
    if not (done or doing or failed):
        return None
    if not (doing or failed):
        return f"Context only, do not read aloud: the app has already done this for the user's last request: {'; '.join(done)}."
    parts = [f"done: {'; '.join(done)}"] if done else []
    if doing:
        parts.append(f"still working on ({DOING}): {'; '.join(doing)}")
    if failed:
        parts.append(f"could not: {'; '.join(failed)}")
    return "Context only, do not read aloud: for the user's last request, " + ". ".join(parts) + "."


def observation_limit(data, *, camera_pixels=False, images=(), require_scope=False) -> str | None:
    """Check a model-declared visual scope against this call's actual attachments.

    No text/intent router and no extra judge. An image on another planning call,
    an available camera, or a callable capture API grants nothing to this reply.
    Empty legacy declarations keep their existing behavior during adoption.
    """
    if not isinstance(data, dict):
        return "The provider supplied no structured observation declaration."
    if "observation_basis" in data:
        basis = data["observation_basis"]
        invalid = "The reply's declared input basis is invalid or unavailable."
        if not isinstance(basis, list) or len(basis) > 2:
            return invalid
        used = set()
        for entry in basis:
            if (not isinstance(entry, dict) or set(entry) != {"scope", "input_refs"}
                    or not isinstance(entry["scope"], str)):
                return invalid
            scope, refs = entry["scope"], entry["input_refs"]
            if (not isinstance(refs, list) or len(refs) > 2
                    or any(type(ref) is not int or not 1 <= ref <= len(images) for ref in refs)):
                return invalid
            if scope in ("none", "structural-workspace"):
                if refs:
                    return invalid
            elif scope in ("retained-source", "specimen-camera", "workspace-pixels"):
                if not refs:
                    return invalid
            else:
                return invalid
            for ref in refs:
                if ref in used:
                    return invalid
                used.add(ref)
            limit = _supplied_scope(scope, refs, images)
            if limit:
                return limit
        if len(used) > 2:
            return invalid
        # A meaningful legacy declaration may accompany a matching single basis,
        # but cannot override/contradict the typed per-input channels.
        legacy_scope, legacy_refs = data.get("observation_scope"), data.get("input_refs", [])
        if not isinstance(legacy_refs, list):
            return invalid
        if legacy_scope not in (None, "") or legacy_refs:
            if len(basis) != 1 or basis[0] != {"scope": legacy_scope, "input_refs": legacy_refs}:
                return invalid
        return None
    scope = data.get("observation_scope")
    if scope in (None, ""):
        # A missing optional annotation is not evidence that the answer is false.
        # Actual attached inputs and concrete operation receipts remain authoritative.
        # Reject an explicitly impossible declared source below, not ordinary prose
        # merely because the model omitted an output-format field.
        return None
    if scope in ("none", "structural-workspace"):
        return None
    refs = data.get("input_refs")
    if (not isinstance(refs, (list, tuple)) or len(refs) > 8
            or any(type(ref) not in (str, int) or isinstance(ref, str) and len(ref) > 256 for ref in refs)):
        return "The reply's input references are invalid or unavailable."
    if scope == "specimen-camera":
        if list(refs) == ["camera"]:
            return None if camera_pixels else "This call did not receive the camera pixels referenced by the reply."
    return _supplied_scope(scope, refs, images)


def _supplied_scope(scope, refs, images):
    """Every numbered member must name one physically attached positive channel."""
    if scope in ("none", "structural-workspace"):
        return None
    valid_refs = bool(refs) and all(type(ref) is int and 1 <= ref <= len(images) for ref in refs)
    supplied = []
    for number, (pixels, facts) in enumerate(images, 1):
        if valid_refs and number in refs and pixels and isinstance(facts, dict):
            supplied.append(facts)
    complete = valid_refs and len(supplied) == len(refs) and len(set(refs)) == len(refs)
    if scope == "specimen-camera":
        return None if complete and all(f.get("channel") == "specimen-camera" and f.get("source") for f in supplied) else "This call did not receive the camera pixels referenced by the reply."
    if scope == "retained-source":
        return None if complete and all(f.get("channel") == "retained-source" and f.get("source") for f in supplied) else "This call did not receive the exact retained-source pixels referenced by the reply."
    if scope == "workspace-pixels":
        supported = complete and all(f.get("channel") == "workspace"
            and f.get("fidelity") == "actual-tab-capture" and f.get("coverage") == "complete"
            and f.get("surfaceProof") in ("same-origin-page-handle", "verified-tab-track")
            and not f.get("missing") and not f.get("redactedRects") for f in supplied)
        return None if supported else "This call received no complete verified workspace pixels supporting the reply's declaration."
    return "The reply's declared observation scope is unavailable."


def unresolved(runs) -> list:
    """Steps that did not work and were not tried again successfully in the same request: a later run of the same
    tool that worked settles the earlier failure (live 2026-10-11: a refused `use` "SAM 3.1 (local)" and then
    `use` micro-sam that worked). The studio's "!" applies the same rule (chatlog.ts unresolvedFailures)."""
    runs = list(runs)
    return [r for n, r in enumerate(runs) if getattr(r, "ok", None) is False
            and not any(getattr(later, "tool", None) == r.tool and getattr(later, "ok", None) is True for later in runs[n + 1:])]


def choice_scopes(runs) -> list[dict]:
    """What each model choice of the request applies to, as state: a `use` is this request only and the default is
    unchanged; a `set_default` is from now on. Live 2026-10-11: the receipt said "for this request" and the answer
    still said "I switched to SAM 2.1"; the writer is given the scope as a fact, never a rule about wording."""
    scopes = []
    for r in runs:
        ok, tool = getattr(r, "ok", None), getattr(r, "tool", None)
        if ok is True and tool == "use":
            scopes.append({"tool": "use", "said": (r.summary or "")[:240],
                           "applies": "this request only; the default setting is unchanged"})
        elif ok is True and tool == "set_default":
            scopes.append({"tool": "set_default", "said": (r.summary or "")[:240], "applies": "from now on"})
    return scopes


@lru_cache(maxsize=1)
def _model_keys() -> tuple[dict[tuple[str, ...], tuple[str, frozenset]], int]:
    """Every key of the app's model names (names.py: the model roles' options, the models fixed in the code and the
    operations' implementations, only entries that run somewhere: never "off", "auto" or a way of working), as its words,
    mapped to (the name said, the names it can mean: a family named alone is any of its members); and the longest key's
    length in words."""
    from .. import capabilities, names

    roles = [r["id"] for r in capabilities.load()["roles"] if r.get("kind") == "model"]
    spaces = [r if r.startswith("fixed:") else f"option:{r}" for r in roles]
    index = names.index()
    spaces += [ns for ns in index if not ns.startswith(("option:", "fixed:"))]
    keys: dict[tuple[str, ...], tuple[str, frozenset]] = {}
    for ns in spaces:
        space = index.get(ns)
        if space is None:
            continue
        for k, meaning in space.keys.items():
            entry = space.entries[meaning.id]
            if entry.where not in ("mac", "cloud"):
                continue
            members = meaning.members if meaning.family else (meaning.id,)
            means = frozenset(names.key(space.entries[m].name) for m in members if m in space.entries)
            words = tuple(k.split())
            said, before = (meaning.family or entry.name), keys.get(words)
            keys[words] = (before[0] if before else said, means | (before[1] if before else frozenset()))
    return keys, max((len(w) for w in keys), default=0)


def models_named(text: str) -> list[tuple[str, frozenset]]:
    """The app's models a text names, by their exact names (names.py's keys: never a part of a name, never a word
    list), each as (the name said, the names it can mean), in order, once each."""
    import re

    from ..names import key

    keys, longest = _model_keys()
    words = key(re.sub(r"[.!?](?=\s|$)", " ", text or "")).split()
    found, i = [], 0
    while i < len(words):
        for n in range(min(longest, len(words) - i), 0, -1):
            hit = keys.get(tuple(words[i:i + n]))
            if hit is not None:
                if hit not in found:
                    found.append(hit)
                i += n
                break
        else:
            i += 1
    return found


def unreceipted_models(text: str, runs=(), also=()) -> list[str]:
    """The models `text` names that no receipt of the request names (live 2026-10-11 16:28:55: "dissecting the stoma
    with the DINO model", and no step used DINO): receipts are each step's summary and the models that worked on it
    (ToolRun.models), and `also`: what else the answer was given (its notes, the models of its own looks and drawings,
    the defaults of the roles it chose a model for). Never the user's words. A fact for the answer's writer, never a
    rewrite in code."""
    from ..names import key

    receipted: set[str] = set()
    for run in runs:
        receipted |= {key(m.get(f)) for m in (getattr(run, "models", None) or []) for f in ("name", "said") if m.get(f)}
        for _, means in models_named(getattr(run, "summary", "") or ""):
            receipted |= means
    for given in also:
        for _, means in models_named(given):
            receipted |= means
    return [said for said, means in models_named(text) if not means & receipted]


def model_problems(text: str, runs=(), also=()) -> list[str]:
    """Each model the answer names that no receipt names, as a problem the answer's writer is told (an id, like a
    failed step's), with the models its steps did use."""
    named = unreceipted_models(text, runs, also)
    if not named:
        return []
    used = list(dict.fromkeys(m.get("said") or m.get("name") for run in runs
                              for m in (getattr(run, "models", None) or []) if m.get("said") or m.get("name")))
    tail = f"; its steps used {', '.join(used)}" if used else ""
    return [f"the answer names {name}, which no step of this request used{tail}" for name in named]


def answer_facts(runs=(), *, camera_pixels=False, images=(), limitations=(), targets=0, outlined=0):
    """Bounded prompt information, never an assistant answer or a second ledger.

    Original occurrence, source, output and Goal facts come from the existing
    receipts. Only this call's supplied images are listed; a feed's availability
    or a previous call's image does not grant visual authority here.

    `camera_pixels` records only the actual legacy camera crop referenced by
    input_refs ["camera"], projected as `legacyCameraCropSupplied`. Numbered
    attachments are listed independently in `images`; a false legacy flag does
    not mean that a numbered specimen-camera input is absent.
    """
    nodes, omitted = 0, 0

    def bounded(value, depth=0):
        nonlocal nodes, omitted
        nodes += 1
        if nodes > 2048 or depth > 12:
            omitted += 1
            return {"omitted": True}
        if value is None or type(value) in (bool, int):
            return value
        if type(value) is float and math.isfinite(value):
            return value
        if isinstance(value, str):
            if len(value) <= 2000:
                return value
            omitted += 1  # never a silent cut: the text says how much is not here
            return value[:1940] + f" … [cut: {len(value) - 1940} more characters not shown here]"
        if isinstance(value, dict):
            omitted += max(0, len(value) - 64)
            out = {}
            for key, item in islice(value.items(), 64):
                if isinstance(key, str) and len(key) <= 256:
                    out[key] = bounded(item, depth + 1)
                else:
                    omitted += 1
            return out
        if isinstance(value, (list, tuple)):
            omitted += max(0, len(value) - 64)
            return [bounded(item, depth + 1) for item in value[:64]]
        omitted += 1
        return {"omitted": True}

    runs = tuple(runs)
    # What Activity says of a step (the models that worked on it) is for the user, not new facts for this model call:
    # context earns its place (the golden facts prompt stays as it was).
    from .registry import ACTIVITY_KEYS

    operations = [{k: v for k, v in run.wire().items() if k not in ACTIVITY_KEYS} for run in runs[:32]]
    compacted = False
    for operation in operations:
        outcome = operation.get("outcome")
        if not isinstance(outcome, dict):
            continue
        try:
            bulky = len(json.dumps(outcome, ensure_ascii=False)) > 4000
        except (TypeError, ValueError, RecursionError):
            bulky = True  # an unrepresentable optional trace cannot consume later facts
        if bulky:
            # The full receipt stays in its original journal. Omit only this
            # prompt's trace before it exhausts the shared node allowance;
            # omittedTrace counts excluded top-level outcome fields.
            kept = {key: outcome[key] for key in ("status", "error", "unmatched") if key in outcome}
            removed = len(outcome) - len(kept)
            operation["outcome"] = {**kept, "omittedTrace": removed}
            omitted += removed
            compacted = True
    if compacted:
        # Continuation planning needs the original attempted arguments beside
        # every remaining output/failure. Ordinary small receipt wire is unchanged.
        for run, operation in zip(runs, operations):
            operation["args"] = run.args
    statuses = []
    for operation in operations:
        row = {key: operation[key] for key in ("requestId", "actionId", "tool", "ok") if key in operation}
        summary = operation.get("summary") or ""
        row["summary"] = summary[:240]
        outcome = operation.get("outcome")
        if isinstance(outcome, dict):
            row["outcome"] = {"status": outcome.get("status")}
            error = outcome.get("error")
            if isinstance(error, dict):
                row["outcome"]["error"] = {}
                for key in ("code", "reason"):
                    if key in error:
                        value = error[key]
                        row["outcome"]["error"][key] = value[:240] if isinstance(value, str) else bounded(value)
        goal = operation.get("goal")
        if isinstance(goal, dict):
            row["goal"] = {key: goal[key] for key in ("id", "status") if key in goal}
        statuses.append(row)
    # Optional source detail must not consume the node allowance for this
    # call's inputs or erase a later original failure/skip. Status rows have
    # their own bounded text; they are not inferred from nested source records.
    # Shortening duplicate status text is not a lost fact: the full operation
    # below either retains it or contributes its own explicit omission count.
    facts = {"omittedOperations": max(0, len(runs) - 32),
             "targets": {"requested": targets, "outlined": outlined},
             **({"modelChoices": choice_scopes(runs)} if choice_scopes(runs) else {}),
             "inputs": {"legacyCameraCropSupplied": bool(camera_pixels),
                        "images": [metadata for pixels, metadata in images[:2] if pixels]},
             "limitations": [str(item)[:2000] for item in limitations if item][:16]}
    core = bounded(facts)
    details = bounded(operations)
    if not isinstance(details, list):
        details = [{"omitted": True} for _ in operations]
    projection = {"operationStatuses": statuses, **core, "operations": details}
    projection["omittedFacts"] = omitted

    def encoded():
        return json.dumps(projection, ensure_ascii=False)

    # Remove whole optional records rather than cutting through JSON or losing
    # the later status section. Small receipts/outputs retain their full wire.
    for index in sorted(range(len(projection["operations"])),
                        key=lambda i: len(json.dumps(projection["operations"][i], ensure_ascii=False)), reverse=True):
        if len(encoded()) <= 24000:
            break
        projection["operations"][index] = {"omitted": True}
        omitted += 1
        projection["omittedFacts"] = omitted
    # Unusually large input metadata or limits remain explicitly unknown. The
    # separate image manifest and camera obligation still travel with this call.
    for key in ("inputs", "limitations"):
        if len(encoded()) > 24000:
            projection[key] = {"omitted": True}
            omitted += 1
            projection["omittedFacts"] = omitted
    # Retain every bounded occurrence/status even when its error text is large.
    for row in statuses:
        if len(encoded()) <= 24000:
            break
        if "outcome" in row:
            row["outcome"] = {"status": row["outcome"]["status"], "omitted": True}
            omitted += 1
        if row["summary"]:
            row["summary"] = ""
            omitted += 1
        projection["omittedFacts"] = omitted
    # Oversized identities are omitted whole, never shortened into another
    # apparently valid source/occurrence. Ordinary admitted IDs fit unchanged.
    for row in statuses:
        if len(encoded()) <= 24000:
            break
        for key in ("requestId", "actionId", "tool", "goal"):
            if key in row:
                row[key] = {"omitted": True}
                omitted += 1
                projection["omittedFacts"] = omitted
    return block(encoded(), tag="answer-facts", source="app") + (
        "\nOmitted or truncated facts are unknown, not successful outcomes." if omitted else "")
