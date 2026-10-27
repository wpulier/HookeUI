"""How do you know? What an outline's name and outline rest on, read one step from it.

The partner §1a (the Understand depth: "how do you know?" one step from any result). Each path that
names an outline keeps on it how (`SceneObject.naming`): naming a click keeps the model, the view,
what it noted, what else it weighed and what it was told besides the pictures; a SAM 3.1 match keeps
its example and mask score; a promoted cell keeps its population and member; the look's own outlines
keep the look and its model; a repair by the outline check is marked; a rename keeps who changed
what, from what to what. A path that keeps nothing says so; nothing is inferred from an
outline's `source` beyond what it says (review of #157, H2).

Where it is kept: on the outline, in memory, while the outline exists. It is not yet filed with the
persisted named-outline entry (seen.py), so it does not outlive the outline or a restart (design
finding in the partner doc's progress); a dataset being recorded keeps naming's log record, which holds
less than this.

`chain` is the one account of which models made an outline, each in order (found by, drawn by, checked by, named by,
followed by), read from its own record: `outline_facts` gives it first, and the board's rows (board.how_made) say it
(Slice 2, 2026-10-11: "which model did you use?" was answered with an invented "built-in vision model").

`resources(id="o3")` reads it (resources.Index.read). It is a read-only lookup, not an index
entry: outlines are listed on the board, and this is the step behind one. The text is facts for
the brain, which answers in the user's words and level (`learner.py`); nothing here is a verdict.
"""
from __future__ import annotations

import time

from .names import said_impl
from .scene import UNSURE_BELOW

TOLD_MAX = 1200  # characters of what the naming model was told besides the pictures (a longer one is marked cut)
RENAMES_KEPT = 5


def naming_record(*, model: str, option: str, frame_id: int, info, reading: dict, told: str, closer: int = 0) -> dict:
    """What naming a click keeps on its outline (agent.identify)."""
    specific = reading.get("specific") or ""
    alternatives = list(reading.get("alternatives") or [m for m in reading.get("maybe") or [] if m != specific])
    told = " ".join(str(told or "").split())
    return {"by": "model", "label": reading.get("label") or "", "model": model, "option": option, "at": time.time(),
            "frame": frame_id, "seq": getattr(info, "seq", None), "observations": list(reading.get("observations") or [])[:3],
            "specific": specific, "alternatives": alternatives[:3], "quality": list(getattr(info, "quality", ()) or ()),
            "told": told[:TOLD_MAX] + (" … (cut here)" if len(told) > TOLD_MAX else ""), "closer": int(closer)}


def match_record(*, label: str, example: str, score: float | None, model: str = "SAM 3.1") -> dict:
    """A thing outlined as like an example (find like=): segmentation, its name the example's."""
    return {"by": "match", "label": label, "model": model, "example": example, "score": score, "at": time.time()}


def concept_record(*, label: str, words: str, score: float | None, model: str = "SAM 3.1") -> dict:
    """A thing SAM 3.1 found for the user's words (find by="concept"): its name is those words."""
    return {"by": "concept", "label": label, "model": model, "words": words, "score": score, "at": time.time()}


def promoted_record(*, label: str, population: str, member: str, basis: str = "") -> dict:
    """A cell promoted from a population to an outline (population.promote): its name the request's, and
    why it was chosen when the request said (`basis`: "picked by the vision model as ...")."""
    return {"by": "request", "label": label, "population": population, "member": member, "basis": basis[:160],
            "at": time.time()}


def asked_record(*, label: str, model: str) -> dict:
    """An outline the look found for a request but did not name itself: its name is the request's word."""
    return {"by": "asked", "label": label, "model": model, "at": time.time()}


def look_record(*, label: str, model: str, confidence: float | None = None) -> dict:
    """An outline the app's look made and named when it pointed at it (agent.point, the finder's look)."""
    return {"by": "look", "label": label, "model": model, "confidence": confidence, "at": time.time()}


def renamed(obj, by: str, to: str) -> dict:
    """The naming record after a rename the user made or accepted (`by`: "you", or the researcher whose
    suggestion it was): what was said before stays, and the rename is added (from, to, by, when)."""
    record = dict(getattr(obj, "naming", None) or {})
    renames = list(record.get("renames") or [])
    renames.append({"by": by, "from": obj.label, "to": to, "at": time.time()})
    if len(renames) > RENAMES_KEPT:
        record["renamesDropped"] = int(record.get("renamesDropped") or 0) + len(renames) - RENAMES_KEPT
    record["renames"] = renames[-RENAMES_KEPT:]
    return record


def _model_label(model_or_option: str | None) -> str:
    from .options import VISION

    if not model_or_option:
        return "the vision model"
    option = VISION.get(model_or_option)
    return option.label if option is not None else str(model_or_option)


def _when(at: float | None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(at)) if at else "an unknown time"


def _labels() -> dict:
    from .operations import OPERATIONS

    return {i.id: i.label for op in OPERATIONS.values() for i in op.implementations}


WHO = {"click": "your click", "box": "your box", "user": "you"}


def _outlined_by(obj) -> str:
    source = str(getattr(obj, "source", "") or "")
    made = getattr(obj, "made_by", None) or {}
    naming = getattr(obj, "naming", None) or {}
    labels = _labels()
    impl = made.get("implementation")
    if naming.get("by") == "request" or made.get("how") == "measured":  # a measured cell, whoever named it
        line = (f"Outlined by {labels.get(impl, impl) or 'the cell finder'} as cell {made.get('member') or naming.get('member')} "
                f"of {made.get('evidence') or naming.get('population')}, then promoted at {_when(getattr(obj, 'created_at', None))}")
        return line + (f" ({obj.note})" if getattr(obj, "note", "") else "") + "."
    if naming.get("by") == "match":
        who = f"a match for {naming.get('example')}"
    elif naming.get("by") == "concept":
        who = f"a match for the words \"{naming.get('words')}\""
    elif naming.get("by") in ("look", "asked"):
        who = "the app's look"
    elif source in WHO:
        who = WHO[source]
    elif source.startswith("ai:") and _model_label(source[3:]) != source[3:]:
        who = "the app's look"
    else:
        who = source or "the app"
    line = f"Outlined from {who}" + (f" by {labels.get(impl, impl)}" if impl else "") + f" at {_when(getattr(obj, 'created_at', None))}"
    if made.get("fallbackFrom"):
        line += f", after {labels.get(made['fallbackFrom'], made['fallbackFrom'])} could not"
    if made.get("reused"):
        line += ", reusing a result made for an earlier request"
    if getattr(obj, "note", ""):
        line += f" ({obj.note})"
    return line + "."


# The chain is told to models (the brain, the voice), so the user is "the user", never "you".
THE_USERS = {"click": "the user's click", "box": "the user's box", "user": "the user"}
FITS_WORDS = {"yes": "it fits", "too_big": "it took in too much", "too_small": "it covered only part",
              "wrong": "it was on something else", "unclear": "it could not tell"}


def _found_by(obj, naming: dict, made: dict) -> str:
    source = str(getattr(obj, "source", "") or "")
    if naming.get("by") == "request" or made.get("how") == "measured":
        finder = said_impl(made.get("implementation")) or "the cell finder"
        member = made.get("member") or naming.get("member")
        population = made.get("evidence") or naming.get("population")
        return f"found by {finder} as cell {member} of {population}" if member else f"found by {finder}"
    if naming.get("by") == "match":
        return f"found as like {naming.get('example')} by {naming.get('model')}"
    if naming.get("by") == "concept":
        return f"found by {naming.get('model')} for the words \"{naming.get('words')}\""
    if naming.get("by") in ("look", "asked"):
        return f"found by {_model_label(naming.get('model'))}"
    if source == "click":
        how = "drag" if getattr(obj, "origin_box", None) is not None else "click"
        return f"found by the user's {how} at {_when(getattr(obj, 'created_at', None))}"
    if source in WHO:
        return f"found by {THE_USERS[source]}"
    if source.startswith("ai:") and _model_label(source[3:]) != source[3:]:
        return f"found by {_model_label(source[3:])}"
    if source == "ai:sight":
        return "found by the running look"
    return "found by the app" if source.startswith("ai") else ""


def _drawn_by(obj, made: dict, checked: dict) -> str:
    """What drew it first (a part has no commas: the chain is joined with them). Redrawn after the check, the first
    drawer is kept by the check (`before`) and the redraw is said with the check."""
    if checked.get("redrawn") and checked.get("before"):
        return f"drawn by {said_impl(checked['before'])}"
    impl = made.get("implementation")
    if not impl:
        return ""  # nothing recorded (an unvoted blind arm says nothing of its model)
    line = f"drawn by {said_impl(impl)}"
    if made.get("how") == "measured":
        line += " as its own mask"
    if made.get("fallbackFrom"):
        line += f" after {said_impl(made['fallbackFrom'])} could not"
    if made.get("origin") in ("user-chosen", "brain-chosen"):
        line += " as chosen for that request"
    if made.get("reused"):
        line += " for an earlier request and reused"
    return line


def _checked_by(checked: dict, made: dict) -> str:
    if not checked:
        return ""
    line = f"checked by {_model_label(checked.get('option') or checked.get('model'))} ({FITS_WORDS.get(checked.get('fits'), checked.get('fits'))})"
    if checked.get("redrawn"):
        line += f" and redrawn by {said_impl(made.get('implementation'))}" if made.get("implementation") else " and redrawn"
    return line


def _named_by(obj, naming: dict) -> str:
    status = getattr(obj, "status", "")
    if status in ("outline", "looking") or not getattr(obj, "label", ""):
        return "not named yet" if status != "looking" else "being named"
    renames = naming.get("renames") or []
    if renames and renames[-1].get("by") == "you":
        return "named by the user" if renames[-1].get("from") != renames[-1].get("to") else "the user confirmed its name"
    if status == "confirmed":
        return "the user confirmed its name"
    by = naming.get("by")
    if by == "model":
        return f"named by {_model_label(naming.get('option') or naming.get('model'))}"
    if by == "look":
        return f"named by {_model_label(naming.get('model'))}"
    if by == "asked":
        return "named with the request's word"
    if by in ("match", "concept"):
        return f"named by the request ({'its example' if by == 'match' else 'the words asked for'})"
    if by == "request":
        return "named by the request that chose it"
    source = str(getattr(obj, "source", "") or "")
    if source.startswith("ai:") and _model_label(source[3:]) != source[3:]:
        return f"named by {_model_label(source[3:])}"
    return ""


def _followed_by(obj) -> str:
    """The follower's stamp (objtrack.py, `followed_by`), read as it is: the model, its shape kept or not."""
    followed = getattr(obj, "followed_by", None)
    if not followed:
        return ""
    if isinstance(followed, str):
        return f"followed by {followed}"
    model = followed.get("model") or followed.get("implementation") or ""
    line = f"followed by {said_impl(model) if model else 'the follower'}"
    shape = str(followed.get("shape") or "")
    if shape:
        line += " with its shape kept" if "kept" in shape else f" with its shape {shape}"
    if followed.get("until") or followed.get("stopped"):
        line += f" until {_when(followed.get('until') or followed.get('stopped'))}"
    return line


def chain(obj) -> list[str]:
    """How an outline came to be, each model in order, from its own record: found by, drawn by, checked by, named by,
    followed by. The one account of "which model did you use?" and "how do you know?" (board.how_made, outline_facts;
    live 2026-10-11: "our built-in vision model" was invented, Gemini 3.8 Flash had found it and EdgeTAM drawn it)."""
    naming = getattr(obj, "naming", None) or {}
    made = getattr(obj, "made_by", None) or {}
    checked = getattr(obj, "checked", None) or {}
    if getattr(obj, "trial", None) is not None:
        return ["made for a blind comparison (which model made it is said after the vote)"]
    parts = (_found_by(obj, naming, made), _drawn_by(obj, made, checked), _checked_by(checked, made), _named_by(obj, naming),
             _followed_by(obj))
    return [p for p in parts if p]


def _first_name(obj, naming: dict) -> str:
    """The name the path that named it gave (before any rename)."""
    renames = naming.get("renames") or []
    return naming.get("label") or (renames[0]["from"] if renames else "")


def _name_lines(obj) -> list[str]:
    naming = getattr(obj, "naming", None) or {}
    status = getattr(obj, "status", "")
    by = naming.get("by")
    lines = []
    if status in ("outline", "looking") or not obj.label:
        lines.append("Not named yet." if status != "looking" else "Being named now.")
        return lines
    first = _first_name(obj, naming)
    if by == "model":
        model = _model_label(naming.get("option") or naming.get("model"))
        pictures = "the whole view with the outline drawn and a close-up of it"
        if naming.get("closer"):
            pictures += f", then {naming['closer']} closer look{'s' if naming['closer'] != 1 else ''} it asked for"
        named = f"Named \"{first}\"" if first else "Named"
        lines.append(f"{named} by {model} at {_when(naming.get('at'))}, shown {pictures}, on the view your click was made on "
                     f"(frame {naming.get('frame')}).")
        if naming.get("specific") and naming.get("specific") != first:
            lines.append(f"Its own first guess was \"{naming['specific']}\"; it gave the broader name because it was not sure.")
        if naming.get("observations"):
            lines.append("What it noted: " + "; ".join(naming["observations"]) + ".")
        if getattr(obj, "seen", "") and obj.label == first:
            lines.append(f"What it said about this one: {obj.seen}")
        if naming.get("alternatives"):
            lines.append("What else it weighed: " + ", ".join(naming["alternatives"]) + ".")
        if naming.get("quality"):
            lines.append("The view was flagged: " + ", ".join(naming["quality"]) + ".")
        if naming.get("told"):
            lines.append(f"What it was told besides the pictures: {naming['told']}")
    elif by == "match":
        score = naming.get("score")
        lines.append(f"Outlined as like {naming.get('example')} by {naming.get('model')} at {_when(naming.get('at'))}"
                     + (f" (mask score {score:.2f}, how well the outline fits, not how sure a name is)" if isinstance(score, (int, float)) else "")
                     + f"; its name \"{first}\" is the example's, not recognised on its own.")
    elif by == "concept":
        score = naming.get("score")
        lines.append(f"Found by {naming.get('model')} for the words \"{naming.get('words')}\" at {_when(naming.get('at'))}"
                     + (f" (its match score {score:.2f}, not how sure a name is)" if isinstance(score, (int, float)) else "")
                     + f"; its name \"{first}\" is the words it was asked to find, not recognised by a model that names things.")
    elif by == "request":
        basis = naming.get("basis")
        lines.append((f"Its name \"{first}\" came with the request that chose it" + (f" ({basis})." if basis else ".")) if first
                     else "It was not named.")
    elif by == "asked":
        lines.append(f"Found by the app's look at the view ({_model_label(naming.get('model'))}) at {_when(naming.get('at'))} "
                     f"for a request; its name \"{first}\" is the request's word, not the look's.")
    elif by == "look":
        lines.append(f"Named \"{first}\" by the app's look at the view ({_model_label(naming.get('model'))}) at "
                     f"{_when(naming.get('at'))}, when it pointed at it; what it noted about this one was not kept.")
    elif str(getattr(obj, "source", "")).startswith("ai:") and _model_label(str(obj.source)[3:]) != str(obj.source)[3:]:
        lines.append(f"Named by the app's look at the view ({_model_label(str(obj.source)[3:])}) when it pointed at it; "
                     "what it noted about this one was not kept.")
    else:
        lines.append("This path does not keep how it was named.")
    # The names it has had, in order: each rename, and any change no rename explains (the app relabelled it).
    current = first
    renames = naming.get("renames") or []
    if naming.get("renamesDropped"):
        lines.append(f"{naming['renamesDropped']} earlier rename{'s' if naming['renamesDropped'] != 1 else ''} not kept.")
        current = renames[0].get("from") if renames else current
    for r in renames:
        frm, to = r.get("from") or "", r.get("to") or ""
        if current and frm != current:
            lines.append(f"Relabelled by the app to \"{frm}\".")
        by_ = r.get("by")
        who = "you" if by_ == "you" else "the app's look" if by_ == "look" else f"you, from {by_}'s suggestion"
        lines.append(f"Renamed \"{frm}\" to \"{to}\" by {who} at {_when(r.get('at'))}." if frm and frm != to
                     else f"\"{to}\" confirmed by {who} at {_when(r.get('at'))}.")
        current = to
    if current and obj.label == f"{current}?":  # marked a first guess while it is measured (M2.1), not a new name
        note = getattr(obj, "note", "") or ""
        found = note.split(";", 2)[1].strip() if note.startswith("a first guess by eye;") else "being measured"
        lines.append(f"The \"?\" marks it a first guess by eye ({found}).")
    elif current and obj.label != current:
        lines.append(f"Relabelled by the app to \"{obj.label}\" since.")
    if first and obj.label not in (first, f"{first}?") and by in ("model", "match", "concept", "request", "look", "asked"):
        lines.append(f"The account above is for \"{first}\", not for \"{obj.label}\".")
    confidence = getattr(obj, "confidence", None)
    if status == "confirmed" and not naming.get("renames"):
        lines.append(f"\"{obj.label}\" is confirmed by you.")
    elif by == "look" and obj.label == first and isinstance(confidence, (int, float)):
        lines.append(f"The look's own confidence: {confidence:.2f} (not calibrated on the naming bench).")
    elif by == "model" and obj.label == first:
        if status == "unsure":
            maybe = list(getattr(obj, "maybe", []) or [])
            lines.append("It said it was not sure" + (f": maybe {' or '.join(maybe)}" if maybe else "") +
                         (f" (confidence {confidence:.2f})" if isinstance(confidence, (int, float)) else "") + ".")
        elif isinstance(confidence, (int, float)):
            lines.append(f"Its confidence: {confidence:.2f}. On the naming bench, its names below {UNSURE_BELOW} were right about "
                         "45% of the time, and 64-76% at or above it (docs/bench/naming.md).")
    repaired = naming.get("repaired")
    if repaired:
        lines.append(f"Its outline was redrawn at {_when(repaired.get('at'))} by the app's outline check (the first did not "
                     "fit what it was meant to be)" + ("; the score above was for the first outline." if by in ("match", "concept") else "."))
    taught = getattr(obj, "taught", None)
    if taught:
        lines.append(f"Closest to your own \"{taught.get('label')}\" examples (similarity {taught.get('similarity', 0):.2f}); "
                     "shown beside the name, never written into it.")
    return lines


def outline_facts(hub, obj) -> str:
    """Everything the app knows about how an outline and its name were made, for "how do you know?"."""
    title = f"\"{obj.label}\" ({obj.id})" if obj.label else f"an unnamed outline ({obj.id})"
    made = chain(obj)
    lines = [title, *([f"How it was made, each model in order: {', '.join(made)}."] if made else []), _outlined_by(obj),
             *_name_lines(obj)]
    if getattr(obj, "shown", True):
        lines.append("Where it is now: drawn in place (its place checked against the view).")
    else:
        lines.append(f"Where it is now: not drawn on the live view ({obj.not_shown_why()}).")
    size = hub.size_of(obj) if hasattr(hub, "size_of") else None
    reading = hub.scale_reading() if hasattr(hub, "scale_reading") else None
    if size and reading is not None:
        from .scale import round_um

        lines.append(f"Its size: about {round_um(size['lengthUm'])} by {round_um(size['widthUm'])} µm, measured by the app on "
                     f"its outline; {reading.words}.")
    elif obj.kind in ("highlight", "part"):
        lines.append("Its size: not known, the app has no scale yet (say which objective is in use).")
    return "\n".join(lines)
