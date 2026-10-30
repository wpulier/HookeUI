"""What a model is told (CONTEXT.md "Moment"): captured once, rendered for each reader.

`capture` takes everything a request needs to know from the app at one moment: what is
outlined and where, the running look, what is on screen, the researchers, the conversation,
the rest of the day, the user's preferences (who leads; how to explain: learner.py, one line
the same for every reader). The renderers turn a Moment into text for one
reader: the brain's scene table (`scene_table`), the fast pass (`fast_fields`), the voice's view
note (`view_note`) and its recap for a new session (`voice_recap`). They are pure: the same
Moment gives the same text (tests/golden/), and researchers' words reach every one of them
only through untrusted.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import re
import time

from ..learner import of as learner_of
from ..media import WORDS as MEDIA_WORDS
from ..untrusted import block, inline

LEAD_TABLE = ("\nThe user is leading: they find things themselves. Highlight, select or point only when they ask "
              "in so many words, and do not name what to look for unless asked.")
LEAD_NOTE = " The user is leading: do not point things out or name what to look for unless asked."
ROSTER_MADE = {"lesson": "a lesson", "video": "a video", "annotated_image": "a labelled picture", "image": "a picture",
               "research": "research", "answer": "an answer"}
# While the studio covers the live view (a new session's home, until the user chooses what it looks at; hub.view_held,
# commands.py `view`): told to the brain and the voice in one line, only then, so what is not on screen is not described
# as if it were.
HELD_LINE = "The live view is hidden: the user is choosing what this session looks at."
NOTE_CHARS = 1800  # a view note (one context update)
RECAP_CHARS = 1900  # a recap: one thinking.append carries at most ~500 tokens


@dataclass(frozen=True)
class Moment:
    outlined: tuple[str, ...]  # `id "label" (x,y)` in the current view; out of view ones marked
    outlined_plain: tuple[str, ...]  # the same without positions (the voice)
    selected: str | None
    seen: str  # the running look, or ""
    view: object  # the running look these notes came from (its s-ids), or None
    screen: str  # what is on screen besides the live view (with a lesson step's answer: the brain only)
    screen_voice: str  # the same for the voice (no answers)
    researchers: str  # the researchers' lines (safe in a template; wrapped when rendered)
    roster: str  # the latest researchers in a line, for the voice
    history: str  # the conversation as prompt text (summary, window, notes)
    recap_lines: tuple[str, ...]  # the last few exchanges for a new voice session
    title: str  # the active conversation's title
    summary: str  # its running summary
    thread_tag: str  # "(Conversation: ...)" when there is more than one today
    digest: str  # the rest of the day
    user_leads: bool
    notes: tuple[str, ...] = field(default=())  # what happened in the chat while the voice was off
    learner: str = ""  # how to explain (learner.line): the same line for every reader
    collecting: str = ""  # a dataset being collected (datasets/collector.py): read-only, the user's switch
    held: str = ""  # HELD_LINE while the studio covers the live view (a new session's home), else ""
    # What is on stage while it is one of the user's assets (sources.stage_line: its metadata as facts, ADR 0022);
    # "" for the live camera, which costs nothing.
    stage: str = ""
    outcomes: str = ""  # how each model has done here (option OUTCOME_LINES, off by default; K3)
    session: str = ""  # index entries this conversation refers to, capped (option SESSION_CONTEXT, off by default; K3)
    awareness: dict = field(default_factory=dict)  # detached bounded facts, not attached pixels or a plan
    request_snapshot: object = None  # canonical immutable originating Send pin, never global latest UI
    sight_selection: object = None  # detached original observation; not an attached model picture
    conversation_id: str = ""  # active conversation captured with these notes
    conversation_day: str = ""  # unknown when there is no day store
    project_title: str = ""  # captured from the owning store, never a later active Project
    moving: str = ""  # what moved, measured when the user last started talking (sight.motion_words), while fresh


def capture(brain, notes: tuple[str, ...] = (), when_asked=None, pushes: bool = False, request: str = "",
            request_snapshot=None) -> Moment:
    """The app as a request sees it now. `when_asked` (board.WhenAsked): what was pointed at when the request was
    made; the selection is told as of then, and what is selected now when it has changed since (C2). `pushes`: also
    what options OUTCOME_LINES and SESSION_CONTEXT push into the brain's own prompt (made only for it, never for the
    voice's notes, its recap or the fast pass, which never carry them; K3 review SF1); `request`: the words of a
    request not yet in the conversation (a look hands started)."""
    hub, services = brain.hub, brain.services
    research = services.research if services is not None else None
    sight = brain.sight
    from ..ticket import current

    selector = getattr(sight, "select", None)
    sight_selection = selector(current()) if callable(selector) else None
    view = sight_selection.view if sight_selection is not None else sight.view if sight is not None else None
    day = getattr(brain.conversation, "store", None)
    state = board_now(brain)
    brain.conversation.bind_output_source(lambda oid: _outline_source(hub, oid))
    # Which models made each outline, in the brain's own rows only, when option ROW_PROVENANCE says so (off until the
    # benches show it loses nothing); the fast pass's rows never, the voice's always (known.chain).
    provenance = pushes and getattr(hub.settings, "row_provenance", "off") == "on"
    return Moment(
        outlined=tuple(_outlined(state, hub.latest_info, positions=True, workspace_scoped=request_snapshot is not None,
                                 provenance=provenance)),
        outlined_plain=tuple(_outlined(state, hub.latest_info, positions=False, workspace_scoped=request_snapshot is not None)),
        selected=selected_as_asked(hub, when_asked), seen=view.text(hub.latest_info) if view is not None else "", view=view,
        screen=_screen(brain, for_voice=False, request_snapshot=request_snapshot),
        screen_voice=_screen(brain, for_voice=True, request_snapshot=request_snapshot),
        researchers=research.lines() if research is not None else "", roster=_roster(research),
        history=brain.conversation.text(), recap_lines=tuple(_recap_lines(brain.conversation)),
        title=day.active.title if day is not None else "", summary=day.active.summary if day is not None else "",
        thread_tag=(f'(Conversation: "{day.active.title or "new"}", {day.active.id} of {len(day.threads)} in this session.) '
                    if day is not None and len(day.threads) >= 2 else ""),
        digest=digest(day, services), user_leads=bool(getattr(hub.settings, "user_leads", False)), notes=tuple(notes),
        learner=learner_of(hub.settings), collecting=_collecting(hub), held=HELD_LINE if getattr(hub, "view_held", False) is True else "",
        stage=_stage(hub),
        outcomes=_outcome_lines(hub) if pushes else "",
        session=_session_context(hub, brain.conversation.text() + ("\n" + request if request else "")) if pushes else "",
        awareness=_awareness(brain, request_snapshot), request_snapshot=request_snapshot,
        sight_selection=sight_selection,
        moving=sight.motion_words() if hasattr(sight, "motion_words") else "",
        conversation_id=day.active.id if day is not None else "",
        conversation_day=day.sid if day is not None else "",
        project_title=getattr(day, "label", "") if day is not None else "")


def _outline_source(hub, oid) -> dict | None:
    obj = hub.scene.objects.get(oid)
    if obj is None or obj.trial is not None:
        return None
    basis = getattr(obj, "producing_basis", None)
    return {"id": obj.id, "run": getattr(basis, "run", None),
            "frame": basis.frame if basis is not None else obj.origin,
            "epoch": getattr(basis, "epoch", None), "created_at": obj.created_at}


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _words(value, count=6, chars=40) -> list[str]:
    return [inline(v, chars) for v in value[:count] if isinstance(v, str)] if isinstance(value, (list, tuple)) else []


def _awareness(brain, request_snapshot=None) -> dict:
    """Read the existing authorities once. No capture, model, tool execution or inferred goal."""
    from ..screen import REPORT_FRESH_S

    hub, services = brain.hub, brain.services
    info, source = hub.latest_info, hub.source
    frame = source.get(info.id) if info is not None else None
    camera = {"available": frame is not None, "current": frame is not None and source.is_fresh(),
              "provenance": "raw-camera", "frame": getattr(frame, "id", None),
              "boot": getattr(frame, "boot", None), "seq": getattr(frame, "seq", None),
              "at": getattr(frame, "wall_at", None)}
    no_camera = getattr(source, "no_camera", None)
    if frame is None and callable(no_camera) and no_camera() is True:
        camera["chosen"] = "no camera in use: the user chose pictures and videos only"  # a choice, not a fault
    screen = getattr(hub, "screen", None)
    client = getattr(hub, "workspace_client", None) if request_snapshot is None else None
    # A report is paired by the app, never by whichever other tab most recently reported.
    report = getattr(screen, "_reports", {}).get(client) if screen is not None and client is not None else None
    now = screen.clock() if screen is not None else None
    age = now - report["at"] if report is not None else None
    current = bool(report and report.get("visible") and _finite(age) and 0 <= age < REPORT_FRESH_S)
    sequence = (report or {}).get("seq")
    if not isinstance(sequence, int) or isinstance(sequence, bool):
        sequence = None
    items = (report or {}).get("items", [])
    placements, sources = {}, []
    for item in items[:8]:
        if isinstance(item.get("placement"), str):
            placements[inline(item["id"], 60)] = inline(item["placement"], 120)
        binding = item.get("source")
        if isinstance(binding, dict) and binding.get("id"):
            sources.append({k: inline(binding[k], 80) if isinstance(binding[k], str) else binding[k]
                            for k in ("id", "kind", "run", "frame") if isinstance(binding.get(k), (str, int))})
    workspace = {"available": report is not None, "current": current, "provenance": "structured-report",
                 "client": inline(client, 80) if isinstance(client, str) else None, "seq": sequence,
                 "age_s": round(age, 2) if _finite(age) else None, "placements": placements, "sources": sources}
    image = {"available": False, "current": False, "verified": False, "provenance": "unavailable"}
    # Optional server-owned validated adapter result. Browser report fields are never authority.
    capture = getattr(screen, "workspace_capture", None)
    identity = getattr(hub, "workspace_identity", None)
    if isinstance(capture, dict):
        capture_age = now - capture["at"] if _finite(now) and _finite(capture.get("at")) else None
        fidelity = capture.get("fidelity")
        matched = (isinstance(identity, dict) and client is not None
                   and all(isinstance(identity.get(k), str) and identity[k] and capture.get(k) == identity[k]
                           for k in ("client", "pageEpoch", "connectionEpoch", "appRun"))
                   and capture.get("client") == client
                   and sequence is not None and isinstance(capture.get("layoutSeq"), int)
                   and not isinstance(capture.get("layoutSeq"), bool) and capture["layoutSeq"] == sequence)
        fresh = current and _finite(capture_age) and 0 <= capture_age < REPORT_FRESH_S
        verified = bool(matched and fresh and fidelity == "actual-tab-capture"
                        and capture.get("verified") is True and capture.get("authority") == "authorized-originating-tab")
        image = {"available": bool(verified or (matched and fresh and fidelity == "approximate-composition")),
                 "current": bool(matched and fresh), "verified": verified,
                 "provenance": fidelity if fidelity in ("approximate-composition", "actual-tab-capture") else "unknown",
                 "id": inline(capture.get("id") or "", 80), "client": inline(capture.get("client") or "", 80),
                 "age_s": round(capture_age, 2) if _finite(capture_age) else None,
                 "coverage": _words(capture.get("coverage")), "omissions": _words(capture.get("omissions"))}
    if request_snapshot is not None:
        pinned = request_snapshot.observation.wire()
        items = pinned["items"]
        workspace = {"available": True, "current": request_snapshot.availability != "stale",
                     "provenance": "structured-request-pin", "client": pinned["client"],
                     "seq": pinned["seq"], "timing": request_snapshot.timing,
                     "age_s": request_snapshot.report_age_ms / 1000,
                     "visible": pinned["viewport"]["visible"], "complete": pinned["complete"],
                     "viewport": {key: pinned["viewport"][key]
                                  for key in ("layoutSize", "visualRect")},
                     "omitted": pinned["omitted"],
                     "placements": {i["id"]: i.get("rect") for i in items[:8]},
                     "sources": [{k: source[k] for k in ("key", "kind", "media", "identity")}
                                 for source in pinned["sources"][:8]],
                     "projectionOmittedSources": max(0, len(pinned["sources"]) - 8)}
        # Structural observation is no attachment/capture authority. In particular,
        # a callable Screen.look is not proof an actual capture adapter is installed.
        image = {"available": False, "current": False, "verified": False, "provenance": "not-attached"}
    tools = brain.registry.tools
    declared = None
    # Its rows alone (App.capability_status), else the whole wire: the same resolver either way, read fresh.
    read = (getattr(services, "capability_status", None) or getattr(services, "capabilities", None)) if services else None
    if read is not None:
        try:
            declared = read()
        except Exception:
            declared = None  # unavailable catalogue never grants unsupported capability
    capabilities, operations = {}, []
    candidates = declared.get("capabilities") if isinstance(declared, dict) else None
    rows = [row for row in candidates if isinstance(row, dict) and row.get("actor") == "agent"
            and isinstance(row.get("id"), str) and isinstance(row.get("how"), dict)
            and isinstance(row["how"].get("tool"), str)] if isinstance(candidates, list) else []
    # Generic presentation families first; omitted capabilities require catalogue lookup.
    rows.sort(key=lambda row: getattr(tools.get((row.get("how") or {}).get("tool")), "family", "")
              not in ("display", "magnifier"))
    for row in rows[:16]:
        cid, tool = row.get("id"), (row.get("how") or {}).get("tool")
        if not isinstance(cid, str) or row.get("actor") != "agent":
            continue
        registered = tools.get(tool)
        available = row.get("available") is True and registered is not None
        capabilities[inline(cid, 80)] = {"available": available, "tool": inline(tool or "", 80),
                                       "why": inline(row.get("why") or ("" if registered else "not registered here"), 120)}
        if available:
            operation = registered.params.get("properties", {}).get("do", {})
            if registered.family in ("display", "magnifier"):
                operations.append(set(_words(operation.get("enum"), count=32)))
    renderer = next((i.get("capabilities") for i in items if isinstance(i.get("capabilities"), dict)), {}) if current else {}
    renderer_verbs = set(_words(renderer.get("verbs"), count=32))
    maximum = renderer.get("maxInstances")
    independent = (_finite(maximum) and maximum > 1 and renderer.get("independentControls") is True
                   and any({"open", "duplicate"} <= verbs & renderer_verbs for verbs in operations))
    split = independent and any({"open", "duplicate", "split"} <= verbs & renderer_verbs for verbs in operations)
    # Native presentation support belongs to the installed tool and its actual
    # owners, not to a displayed item's invented renderer manifest. An accepted
    # empty workspace can open independent windows. Keep legacy-only displays
    # and unpaired camera context distinct from that originating connection.
    present = tools.get("present")
    properties = present.params.get("properties", {}) if present is not None else {}
    native = getattr(services, "presentations", None)
    installed = (present is not None and present.family == "display"
                 and properties.get("source", {}).get("type") == "object"
                 and properties.get("instance", {}).get("type") == "object"
                 and native is not None and native is getattr(hub, "presentations", None)
                 and getattr(native, "max_windows", 0) > 1
                 and getattr(services, "prepared_sources", None) is not None
                 and callable(getattr(services, "workspace_context", None))
                 and any(row["how"]["tool"] == "present" and row.get("available") is True for row in rows))
    if installed and request_snapshot is not None and workspace["current"] and workspace["visible"]:
        from ..workspace_observation import ObservationError

        try:
            screen._origin(request_snapshot)  # read-only check of the accepted pin's connection/run
        except ObservationError:
            pass
        else:
            independent = split = True
    outputs = []
    for origin in brain.conversation.output_refs():
        actual = _outline_source(hub, origin["id"])
        obj = hub.scene.objects.get(origin["id"])
        current_run = getattr(getattr(obj, "anchor_geometry", None), "run", None)
        available = bool(actual and origin.get("run") and actual["run"] == origin.get("run")
                         and current_run == origin.get("run")
                         and actual["created_at"] == origin.get("created_at"))
        why = "" if available else ("origin run unavailable" if not origin.get("run") else "exact outline no longer available")
        outputs.append({**origin, "available": available, "why": why})
    resources = []
    for turn in brain.conversation.window(5):
        resources.extend(inline(rid, 80) for rid in (turn.refs or {}).get("resourceIds", []) if isinstance(rid, str))
    view = getattr(getattr(brain, "sight", None), "view", None)
    sight = {"available": view is not None, "current": bool(view is not None and view.current(info)),
             "provenance": "sight-report", "frame": getattr(view, "frame_id", None), "at": getattr(view, "at", None)}
    facts = {"outputRefs": outputs, "resources": list(dict.fromkeys(resources))[-8:], "capabilities": capabilities,
             "presentation": {"independent_windows": bool(independent),
                              "split": bool(split)},
             "observations": {"camera": camera, "workspace_report": workspace, "workspace_image": image, "sight": sight},
             "input": {"basis": "context-only", "camera_pixels": False, "workspace_pixels": False}}
    if len(rows) > 16:
        facts["omitted_capabilities"] = len(rows) - 16
    # Bounds apply to the projection, not the authorities. Omissions are explicit.
    _bound_facts(facts, 8192, compact=False)
    return facts


def _bound_facts(facts: dict, limit: int, *, compact: bool = True) -> str:
    """Budget a detached projection without changing any truth value or cutting an ID."""
    def encoded():
        return json.dumps(facts, separators=(",", ":") if compact else None, ensure_ascii=True)

    text = encoded()
    caps = facts["capabilities"]
    while len(text) > limit and caps:
        caps.pop(next(reversed(caps)))
        facts["omitted_capabilities"] = True
        text = encoded()
    report = facts["observations"]["workspace_report"]
    image = facts["observations"]["workspace_image"]
    # First keep the latest of each source/reference; only extreme inputs lose it,
    # explicitly, rather than acquiring a shortened, misleading identity.
    collections = [(report, "sources"), (report, "placements"), (facts, "outputRefs"),
                   (facts, "resources"), (image, "coverage"), (image, "omissions")]
    for minimum in (1, 0):
        for container, key in collections:
            while len(text) > limit and len(container.get(key, [])) > minimum:
                if isinstance(container[key], dict):
                    container[key].pop(next(iter(container[key])))
                else:
                    container[key] = container[key][1:]
                facts["omitted_details"] = True
                text = encoded()
    if len(text) > limit:
        # Keep channel provenance, availability, freshness and input basis even
        # when optional source metadata exceeds a voice update's remaining room.
        facts["observations"] = {kind: {k: v for k, v in row.items()
                                       if k in ("available", "current", "verified", "provenance")}
                                 for kind, row in facts["observations"].items()}
        facts["omitted_details"] = True
        text = encoded()
    return text


def _awareness_text(m: Moment, limit: int = 1400, *, temporal: bool = True) -> str:
    """Compact model facts, with explicit truncation rather than a cut JSON document."""
    if not m.awareness:
        return ""
    facts = json.loads(json.dumps(m.awareness))  # rendering never changes the captured nested facts
    # Keep identity/provenance before optional catalogue detail in the voice's small input.
    if not temporal:
        # What changes every report (a sequence number, an age, a clock) says nothing new to the voice: left out at
        # every depth, so a note is sent only when what it says has changed (the voice journeys bench, 2026-10-11:
        # the workspace report's seq and age_s inside its source sent the voice an unchanged note every 3 s).
        facts["observations"] = {kind: _without_clocks(row) for kind, row in facts["observations"].items()}
    facts["outputRefs"] = [{k: v for k, v in row.items() if k != "created_at"} for row in facts["outputRefs"][-3:]]
    for row in facts["capabilities"].values():
        row.pop("tool", None)
    # Nested maps are copied before slimming; rendering never mutates the Moment.
    text = _bound_facts(facts, limit)
    return "App facts (not attached pixels or fulfillment; omissions are unknown): " + text


_CLOCKS = ("age_s", "boot", "seq", "frame", "client", "at", "timing", "created_at")


def _without_clocks(value):
    if isinstance(value, dict):
        return {k: _without_clocks(v) for k, v in value.items() if k not in _CLOCKS}
    if isinstance(value, list):
        return [_without_clocks(v) for v in value]
    return value


SESSION_LINES, SESSION_CHARS = 6, 600  # option SESSION_CONTEXT's hard cap (a resource limit, not a verdict)
OUTCOME_LINE_MAX = 4


def _outcome_lines(hub) -> str:
    """Option OUTCOME_LINES (off by default): how each model has done on this specimen today, for the brain only."""
    if getattr(hub.settings, "outcome_lines", "off") != "on":
        return ""
    import time as _time

    from ..labels import subject_key
    from ..outcomes import outcomes_of

    specimen = (getattr(hub.scene, "context", None) or {}).get("specimen")
    try:  # with no specimen named, every line names its own (never told as this one's)
        # As last made in a thread (the brain's turn makes them before it captures): nothing is read here, on the loop.
        lines = outcomes_of(hub).pushed(subject_key(specimen) if specimen else None, _time.strftime("%Y-%m-%d"))
    except Exception:
        return ""
    return "; ".join(lines[:OUTCOME_LINE_MAX])


def make_outcome_lines(hub) -> None:
    """Option OUTCOME_LINES: make the lines `_outcome_lines` pushes, reading the log; run in a thread before a capture."""
    if getattr(hub.settings, "outcome_lines", "off") != "on":
        return
    import time as _time

    from ..labels import subject_key
    from ..outcomes import outcomes_of

    specimen = (getattr(hub.scene, "context", None) or {}).get("specimen")
    try:
        outcomes_of(hub).make_pushed(subject_key(specimen) if specimen else None, _time.strftime("%Y-%m-%d"))
    except Exception:
        pass


SESSION_KINDS = ("population", "individual", "bookmark", "result")  # what e3, i4, b2 and d1 name


def _session_context(hub, history: str) -> str:
    """Option SESSION_CONTEXT (off by default): the index entries the conversation and this request name (e3, i4, b2,
    d1...) and the population of the cell selected, newest first, at most SESSION_LINES lines and SESSION_CHARS
    characters (an entry too long for what is left is skipped, never cut). One read of the index."""
    if getattr(hub.settings, "session_context", "off") != "on":
        return ""
    import re

    index = getattr(hub, "resources", None)
    if index is None:
        return ""
    named = list(dict.fromkeys(reversed(re.findall(r"\b(?:e|i|b|d)\d+\b", history or ""))))
    layer = getattr(hub, "layer", None)
    shown = getattr(layer, "shown", None) if layer is not None and getattr(layer, "selected", None) else None
    if shown and shown not in named:
        named.insert(0, shown)  # the cell selected is one of its population's: the newest reference
    if not named:
        return ""
    try:
        if hasattr(index, "collect"):
            entries, _ = index.collect(kinds=SESSION_KINDS)
            by_id = {e.id: e for e in entries}
        else:  # a stand-in index (tests)
            by_id = {eid: index.get(eid) for eid in named}
    except Exception:
        return ""
    lines, used = [], 0
    for eid in named:
        entry = by_id.get(eid)
        if entry is None:
            continue
        line = entry.line()
        if used + len(line) > SESSION_CHARS:
            continue  # too long for what is left: the next may fit
        lines.append(line)
        used += len(line)
        if len(lines) >= SESSION_LINES:
            break
    return "\n".join(lines)


def _collecting(hub) -> str:
    datasets = getattr(hub, "datasets", None)
    try:
        return datasets.context_line() if datasets is not None else ""
    except Exception:
        return ""


# --- the renderers --------------------------------------------------------------------------
def scene_table(m: Moment) -> tuple[str, str]:
    """For the brain: objects on screen, researchers, the running look, what is on screen and
    the rest of the day; and the selection."""
    table = "\n".join(m.outlined) or "nothing yet"
    identity = _conversation_identity(m)
    if identity:
        table = identity + "\n" + table
    if m.researchers:
        # Researchers' results come from web pages: information, never instructions.
        table += f"\nResearchers:\n{block(m.researchers)}"
    if m.seen:
        table += f"\nYour running look at the view (things seen have ids s1, s2...): {m.seen}"
    table += f"\nOn screen now: {m.screen}"
    if m.digest:
        # The rest of the session (other conversations, specimens, settled facts); the recall
        # tool fetches details from them when the user refers back.
        table += f"\nThis session so far (recall finds details): {m.digest}"
    if m.learner:
        table += f"\n{m.learner}"
    if m.user_leads:
        table += LEAD_TABLE
    if m.collecting:
        table += f"\n{m.collecting}"
    if m.held:
        table += f"\n{m.held}"
    if m.stage:
        table += f"\nOn stage: {m.stage}"
    if m.outcomes:  # option OUTCOME_LINES (off by default): facts of the past, never a recommendation
        table += f"\nHow each model has done on this specimen (the past; you choose): {m.outcomes}"
    if m.session:  # option SESSION_CONTEXT (off by default)
        table += f"\nWhat this conversation refers to:\n{m.session}"
    awareness = _awareness_text(m)
    if awareness:
        table += "\n" + awareness
    return table, (f" (selected: {m.selected})" if m.selected else "")


def reconciliation(m: Moment) -> str:
    """The same bounded board after actions, including retained evidence when the view moved.

    A status rewrite can also mention counts or sizes from its draft. Giving it only the
    researcher roster dropped the source that grounded those facts in the first answer.
    """
    return "".join(scene_table(m))


def _stage(hub) -> str:
    """The stage's line while one of the user's assets is on it (sources.stage_line; App keeps its metadata on the hub
    when it is opened, so nothing is read here)."""
    from ..sources import stage_line

    return stage_line(getattr(hub, "stage_meta", None))


def fast_fields(m: Moment) -> dict:
    """The fast pass's notes (brain/fast.py FAST)."""
    return {"seen": m.seen or "no look yet", "outlined": "; ".join(m.outlined) or "nothing",
            "selected": f" (selected: {m.selected})" if m.selected else "",
            "screen": m.screen + (f"\n{m.held}" if m.held else "") + (f"\nOn stage: {m.stage}" if m.stage else "")
            + ("\n" + _awareness_text(m) if m.awareness else ""),
            "researchers": block(m.researchers or "none"),
            "history": "\n".join(part for part in (_conversation_identity(m), m.history) if part),
            "day": m.digest or "nothing else yet",
            "learner": f"\n{m.learner}" if m.learner else ""}


def _conversation_identity(m: Moment) -> str:
    if not m.conversation_id and not m.conversation_day:
        return ""
    return (f"Current conversation: {inline(m.conversation_id or 'unknown', 60)} "
            f"day={inline(m.conversation_day or 'unknown', 20)}")


def view_note(m: Moment) -> str:
    """What the voice should know it sees right now (silent context for GPT-Live)."""
    if m.awareness:
        return _voice_context(m, recap=False)
    outlined = "; ".join(m.outlined_plain) or "nothing"
    note = (f"View notes (what you see right now; context only, do not read this aloud): {m.thread_tag}{m.seen or 'no look yet'} "
            f"Outlined on screen: {outlined}. On screen: {m.screen_voice}."
            + (f" Researchers' work so far (hand over anything about it): {m.roster}." if m.roster else ""))
    # Who leads and how to explain are kept whole: a long look is what gets cut.
    tail = (LEAD_NOTE if m.user_leads else "") + (f" {m.learner}" if m.learner else "") + (
        f" {m.collecting}" if m.collecting else "") + (f" {m.held}" if m.held else "") + (
        f" On stage: {m.stage}." if m.stage else "")
    awareness = _awareness_text(m, limit=max(600, NOTE_CHARS - len(tail) - 180))
    if awareness:
        tail = " " + awareness + tail
    return note[:max(0, NOTE_CHARS - len(tail))] + tail


def view_key(m: Moment) -> tuple:
    """What a view note says that matters to the voice: what is outlined and on screen, the
    specimen and the things the running look saw, the researchers, the conversation, who leads
    and how to explain. Not the look's wording, and not whether it still matches the view to the
    pixel: while the phone's autofocus hunts that flips every few seconds (live demo 2026-10-06),
    and each new note can set GPT-Live talking."""
    view = m.view
    seen = (getattr(view, "specimen", ""), tuple(t.name for t in getattr(view, "things", ())[:8])) if view is not None else ()
    screen = m.screen_voice
    if m.request_snapshot is not None:
        wire = m.request_snapshot.observation.wire()
        # Each pin has a new request/sequence/age, even when the workspace is unchanged.
        # Compare visible meaning, not its delivery envelope: unchanged notes can provoke
        # unsolicited speech and occupy the Live context timeline.
        screen = (tuple(_voice_windows(m.request_snapshot)),
                  json.dumps(_without_clocks({"sources": wire["sources"],
                                              "selected": wire["interaction"]["selected"]}), sort_keys=True))
    return (m.project_title, (m.conversation_day, m.conversation_id), m.outlined_plain, screen, m.roster, m.user_leads, m.learner, seen, m.collecting,
            _awareness_text(m, temporal=False), m.held, m.stage)


def voice_recap(m: Moment) -> str:
    """Everything a new voice session should know at once (each Talk press opens a fresh
    GPT-Live session that remembers nothing): the conversation so far in this session, the researchers'
    work, what is on screen, and what happened while the voice was off."""
    if m.awareness:
        return _voice_context(m, recap=True)
    title = f' ("{m.title}")' if m.title else ""
    parts = ["Context only, do not read aloud and do not reply to this: you are continuing a conversation "
             f"with the user{title}. Most recent last:"]
    if m.summary:
        # Model-written from turns that include researchers' notes: one line, no tags.
        # Said to be past: a summary read as the present made a voice describe what used to be
        # on screen as if it still were (microclaw saw the same with old tool calls).
        parts.append(f"Earlier in it (past, not what is on screen now), summarised: {inline(m.summary, 500)}")
    parts.append("\n".join(m.recap_lines) or "(nothing yet)")
    if m.digest:
        parts.append(f"Earlier in this session (past, not now): {m.digest[:500]}")
    if m.roster:
        parts.append(f"Researchers' work so far: {m.roster}.")
    parts.append(f"On screen now: {m.screen_voice}.")
    if m.held:
        parts.append(m.held)
    if m.stage:
        parts.append(f"On stage: {m.stage}.")
    if m.notes:
        parts.append("While you were off: " + " ".join(m.notes[-6:]))
    if m.collecting:
        parts.append(m.collecting)
    if m.learner:
        parts.append(m.learner)  # last: the latest lines are what a long recap keeps
    awareness = _awareness_text(m)
    if awareness:
        parts.append(awareness)
    text = "\n".join(parts)  # the latest lines are kept
    return text if len(text) <= RECAP_CHARS else parts[0] + "\n…" + text[-(RECAP_CHARS - len(parts[0]) - 2):]


def _voice_clauses(text: str) -> list[str]:
    """Whole app-generated screen/day clauses; semicolons in parentheses stay inside."""
    parts, start, depth = [], 0, 0
    for n, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ";" and not depth:
            parts.append(text[start:n].strip())
            start = n + 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def _voice_awareness(m: Moment) -> tuple[list[str], list[str], list[str]]:
    """Whole compact facts from the captured authorities, never a pixel manifest.

    Repeated empty maps/null metadata need no voice room. False availability,
    provenance and coverage still do. Whole source/receipt identities follow
    current user/work facts, ahead of optional history and catalogue rows.
    """
    facts = m.awareness
    encode = lambda value: json.dumps(value, separators=(",", ":"), ensure_ascii=True)
    basis = facts.get("input", {})
    optional, sources, states = [], [], []
    for kind, row in facts.get("observations", {}).items():
        identity = row.get("provenance", "unknown")
        fields = {k: v for k, v in row.items() if k != "provenance" and v is not None and v != {} and v != []}
        # Each source's freshness in a word (current, stale, unavailable), with an image's identity and
        # coverage beside it. Camera/Sight timing is optional once their channel and freshness are said.
        primary = {k: v for k, v in fields.items()
                   if k in ("available", "current", "verified", "id", "coverage", "omissions", "chosen")}
        state = "current" if row.get("current") else "stale" if row.get("available") else "unavailable"
        detail = {k: v for k, v in primary.items() if k in ("id", "coverage", "omissions", "chosen")}
        label = identity if identity not in ("unavailable", "not-attached") else kind.replace("_", " ")
        states.append(f"{label} {state}" + (", verified" if row.get("verified") else "")
                      + (" " + encode(detail) if detail else ""))
        metadata = {k: v for k, v in fields.items() if k not in primary}
        if metadata:
            line = f"{kind} source: " + encode(metadata)
            (sources if kind == "workspace_report" and (row.get("sources") or row.get("placements")) else optional).append(line)
    core = [f"Sources ({basis.get('basis', 'unknown')}; descriptions, not pixels): " + "; ".join(states)]
    presentation = facts.get("presentation", {})
    core.append("Presentation: " + ", ".join(f"{key}={encode(presentation.get(key))}"
                                            for key in ("independent_windows", "split")))
    refs = facts.get("outputRefs", [])
    receipts = []
    for row in reversed(refs[-3:]):
        # Creation time is already pinned in Moment; run/capture/turn and current
        # availability distinguish this occurrence from any later same-ID outline.
        receipts.append("Output ref: " + encode({k: v for k, v in row.items() if k != "created_at"}))
    resources = facts.get("resources", [])
    if resources:
        receipts.append("Resource receipts: " + encode(resources[-8:]))
    for cid, row in facts.get("capabilities", {}).items():
        optional.append(f"Capability {cid}: " + encode({k: v for k, v in row.items() if k != "tool" and v != ""}))
    if len(refs) > 3 or facts.get("omitted_capabilities") or facts.get("omitted_details"):
        optional.append("Omitted earlier output/catalogue details; unknown here.")
    return core, receipts + sources, optional


def _voice_workspace(snapshot) -> list[str]:
    """Whole source and control addresses from the same accepted Send as chat.

    The typed structural projection carries geometry and property fences; voice
    needs the short source/instance/control addresses first, rather than losing
    an entire multi-window report to its smaller budget. Groups share only their
    observed target and availability flags, never inferred ownership or outcome.
    """
    wire = snapshot.observation.wire()
    encode = lambda value: json.dumps(value, separators=(",", ":"), ensure_ascii=True)
    lines = []
    for source in wire["sources"][:8]:
        lines.append("Workspace source: " + encode({k: source[k] for k in ("key", "kind", "media", "identity")}))
    items, observed_items = sorted(wire["items"], key=lambda i: (not i["id"].startswith("w-"), not i["visible"]))[:16], []
    for item in items:
        observed = {k: item[k] for k in ("kind", "sourceKey", "open", "visible", "connected") if k in item}
        if "legacy" in item.get("view", {}):
            observed["legacy"] = item["view"]["legacy"]
        observed_items.append(observed)
    # These exact shared observed fields apply to every instance line below.
    shared = ({key: value for key, value in observed_items[0].items()
               if all(key in row and encode(row[key]) == encode(value) for row in observed_items)}
              if len(observed_items) > 1 else {})
    if shared:
        lines.append("Instances share: " + encode(shared))
    for item, observed in zip(items, observed_items):
        observed = {key: value for key, value in observed.items() if key not in shared}
        line = f"Instance {item['id']}" + (": " + encode(observed) if observed else "")
        groups = {}
        for control in item["controls"][:8]:
            key = (control["targetId"], control.get("present"), control.get("enabled"))
            groups.setdefault(key, []).append(control["id"])
        for (target, present, enabled), ids in groups.items():
            target = "self" if target == item["id"] else target
            line += (f"; controls({target},present={encode(present)},enabled={encode(enabled)}):"
                     + encode(ids))
        lines.append(line)
        if len(item["controls"]) > 8:
            lines.append(f"Omitted {len(item['controls']) - 8} controls of {item['id']}; unknown here.")
    if len(wire["sources"]) > 8 or len(wire["items"]) > 8:
        lines.append("Omitted workspace sources/instances; unknown here.")
    interaction = {k: wire["interaction"][k] for k in
                   ("selected", "focus", "lastContentPointer", "lastContentFocus")
                   if wire["interaction"][k] is not None}
    if interaction.get("focus") == {"presentationId": None, "controlId": None, "role": "none", "editable": False}:
        interaction["focus"] = {"role": "none", "editable": False}
    if interaction:
        lines.insert(0, "Workspace interaction: " + encode(interaction))
    from ..workspace_observation import front

    shown = {key: value for key, value in front(wire).items()
             if value is not None and key in ("tab", "window", "specimen", "holds", "overlays", "magnifiers",
                                              "studioVisible", "studioFocused")}
    if shown:
        lines.append("Workspace front: " + encode(shown))  # the Tab and window in front, the studio in view (observed)
    return lines


KIND_WORDS = {"table": ("a table", "tables"), "outlines": ("cell outlines", "sets of cell outlines"),
              "tracks": ("motion tracks", "sets of motion tracks"), "image": ("an image", "images"),
              "reference": ("a reference photo", "reference photos"), "document": ("a document", "documents"),
              "graph": ("a graph", "graphs"), "video": ("a video", "videos"), "web-page": ("a web page", "web pages"),
              "magnifier": ("a magnifier", "magnifiers"), "note": ("a note", "notes")}


def holds_words(holds: dict | None) -> str:
    """What a Tab holds (the Tab strip's counts by kind, workspace-model.ts) in plain words: "a table and cell
    outlines", "2 images"; "" when it holds nothing or the report does not say."""
    parts = []
    for kind, (one, many) in KIND_WORDS.items():
        n = (holds or {}).get(kind)
        if isinstance(n, int) and n > 0:
            parts.append(one if n == 1 else f"{n} {many}")
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1] if parts else ""


def overlay_words(drawn: list | None) -> str:
    """The layers drawn on the specimen (workspace_observation.overlays) in plain words: "cell outlines (142)"."""
    words = []
    for layer in drawn or ():
        what = "motion tracks" if layer.get("kind") == "tracks" else "outlines" if layer.get("id") == "outlines" else "cell outlines"
        n = layer.get("members")
        words.append(f"{what} ({n})" if isinstance(n, int) else what)
    return ", ".join(words)


def other_tabs_words(wire) -> str:
    """The other open Tabs of this Project and what each holds, from the Tab strip: 'Other Tabs: "Cell results" (a
    table and cell outlines), "Reference" (a reference photo)'; "" when there are none or none says."""
    strip = next((i for i in wire.get("items") or () if i.get("id") == "workspace-tabs"), None)
    workspace = ((strip or {}).get("view") or {}).get("legacy", {}).get("workspace") or {}
    rows = []
    for tab in workspace.get("tabs") or ():
        if tab.get("front") or "holds" not in tab:
            continue
        held = holds_words(tab.get("holds"))
        rows.append(f'"{inline(tab.get("title") or tab.get("id"), 80)}" ({held or "empty"})')
    return ("Other Tabs: " + ", ".join(rows[:12])) if rows else ""


def specimen_words(fact: dict | None) -> str:
    """A Tab's specimen (the live view) as its Tab strip reports it (workspace_observation `specimen`), in a few words
    after "the specimen", or "" when the report does not say."""
    state = (fact or {}).get("state")
    rect = (fact or {}).get("rect")
    if state == "fill":
        return "fills it, behind its windows"
    if state == "float":
        return "floats in it as a window" + (f" ({round(rect[2])}x{round(rect[3])} px at {round(rect[0])},"
                                              f"{round(rect[1])})" if rect else "")
    return "is hidden in it" if state == "hidden" else ""


def _voice_windows(snapshot) -> list[str]:
    """One plain line per window of the accepted Send: its id, what it shows and its source.

    The voice talks about windows; it never operates their controls, so their control
    addresses stay optional detail (`_voice_workspace`)."""
    from ..workspace_observation import front

    wire = snapshot.observation.wire()
    sources = {source["key"]: source for source in wire["sources"]}
    items = sorted(wire["items"], key=lambda i: (not i.get("visible"), not i["id"].startswith("w-")))
    lines = []
    # Which Tab of the Project is in front and the window highest on it, as the report shows them (on-screen.md); the
    # studio out of view is said, since nothing on it is then on the user's display.
    shown = front(wire)
    # The Tab's specimen is part of what it holds (filling it, floating, or hidden), said with the Tab.
    specimen = specimen_words(shown.get("specimen"))
    held, drawn = holds_words(shown.get("holds")), overlay_words(shown.get("overlays"))
    if shown["tabTitle"] or shown["studioVisible"] is False:
        lines.append((f'Tab "{inline(shown["tabTitle"], 80)}" of this Project' if shown["tabTitle"] else "This Project")
                     + (f", the specimen {specimen}" if specimen else "")
                     + (f", holding {held}" if held else "")
                     + (f"; drawn on the specimen: {drawn}" if drawn else "")
                     + (", the studio not in view" if shown["studioVisible"] is False else ""))
    others = other_tabs_words(wire)
    if others:
        lines.append(others)
    for item in items[:8]:
        source = sources.get(item.get("sourceKey")) or {}
        identity = source.get("identity") or {}
        resource = identity.get("resource") if isinstance(identity.get("resource"), dict) else {}
        shows = identity.get("object") or identity.get("objectId") or resource.get("id")
        what = " ".join(part for part in (source.get("kind"), source.get("media")) if isinstance(part, str))
        line = f"{item['id']}: {inline(item.get('title') or item['kind'], 120)}"
        if what or shows:
            line += f" ({what}{' of ' + str(shows) if shows else ''})".replace("( of", "(of")
        if not item.get("open"):
            line += ", closed"
        elif not item.get("visible"):
            line += ", not visible"
        elif item["id"] == shown["window"]:
            line += ", in front"
        lines.append(line)
    if len(wire["items"]) > 8:
        lines.append(f"{len(wire['items']) - 8} more windows; unknown here.")
    return lines or ["no windows open"]


def _voice_context(m: Moment, *, recap: bool) -> str:
    """Allocate the existing voice cap to whole facts, most needed first.

    The order is the priority: the user's current words, what is on screen and selected,
    how fresh the sources are, then the scene the running look saw (all of it: the voice
    answers "what are we looking at?" from it), then work and the day. Excess history and
    catalogue detail is explicitly omitted; no line is ever cut into a different claim.
    """
    limit = RECAP_CHARS if recap else NOTE_CHARS
    header = (f'Context only; do not read aloud/reply. Conversation "{inline(m.title, 100)}".'
              if recap else "View notes (context only, do not read aloud):")
    if m.project_title:
        header += f' Project: "{inline(m.project_title, 120)}".'
    awareness, identities, catalogue = _voice_awareness(m)
    pinned = _voice_workspace(m.request_snapshot) if m.request_snapshot is not None else []
    current = (_voice_windows(m.request_snapshot) if m.request_snapshot is not None
               else _voice_clauses(m.screen_voice))
    # The generated source-switch menu repeats the displayed source. Retain it as
    # optional detail; the magnifier's actual source and shown work stay first.
    menus = [line for line in current if line.startswith("magnifier sources (")]
    current = [line for line in current if line not in menus]
    # Presenter annotations include quiz questions/outcomes, beyond the current
    # work's identity and step. Preserve their complete original line as optional
    # context, never a prefix sliced by the final character cap.
    annotated = [line for line in current if line.startswith("showing Researcher")
                 and (" (the researcher's question" in line or " (how the user did today:" in line)]
    current = [line.split(" (the researcher's question", 1)[0].split(" (how the user did today:", 1)[0]
               if line in annotated else line for line in current]
    if m.selected:
        current = [line for line in current if not (line.endswith(" is selected") and m.selected in line)]
    seen = [line for line in re.split(r"(?<=[.!?])\s+", m.seen) if line]
    roster = _voice_clauses(m.roster)
    users = [line for line in m.recap_lines if line.startswith("User")]
    day = _voice_clauses(m.digest)
    # Each list element is one complete fact. Its text is never sliced for room.
    learner = m.learner.replace("The user's level (their choice; it changes only how you word answers, never which tools you use): ",
                                "User level (wording only): ")
    important = ([learner] if learner else []) + ([LEAD_NOTE.strip()] if m.user_leads else [])
    important += users[-1:] if recap else []
    important += ["On screen: " + line for line in current]
    important += [m.held] if m.held else []
    important += [f"On stage: {m.stage}"] if m.stage else []
    if m.selected:
        important.append("Selected: " + m.selected)
    important += ["Outlined: " + line for line in m.outlined_plain[:1]]
    important += ["While you were off: " + inline(line, len(line)) for line in m.notes[-1:]] if recap else []
    important += awareness
    if seen and m.view is not None and isinstance(getattr(m.view, "at", None), (int, float)):
        # Each note says when its look was seen, and whether that is the view now (OpenAI's live guidance; ADR 0016).
        # No "the view now": a note is read later than it is written, and the slide may move in between (run 4 of
        # the voice journeys: a separate "the slide moved" note was spoken aloud again and again, so there is none).
        important.append(f"Seen {max(0, round(time.time() - m.view.at))} s before this note:")
    important += ["View: " + line for line in seen]
    if m.moving:
        important.append("Moving: " + m.moving)
    important += ["Work: " + line for line in roster]
    important += ["Session so far: " + line for line in day[:2]] if recap else []
    # Expanded report maps and potentially long/old receipts cannot consume the
    # room needed by this request, current work, roster or day. Once those facts
    # are present, prefer complete newest output identities to optional history.
    extras = identities + (["Earlier conversation (past): " + inline(m.summary, len(m.summary))] if recap and m.summary else [])
    extras += ["Earlier exchange (past): " + inline(line, len(line))
               for line in m.recap_lines if recap and line not in users[-1:]]
    extras += ["While you were off: " + inline(line, len(line)) for line in m.notes[-6:-1]] if recap else []
    extras += ["Window detail: " + line for line in pinned]
    extras += ["Outlined: " + line for line in m.outlined_plain[1:]]
    extras += ["On screen: " + line for line in menus + annotated]
    extras += ["Session so far: " + line for line in day[2:]] if recap else []
    extras += ([m.collecting] if m.collecting else []) + catalogue
    omitted = "Omitted details; unknown here."
    lines, used, dropped = [header], len(header), False
    # Reserve an omission marker, then add only whole lines. Small later facts
    # still fit when an optional large history/menu line cannot.
    for line in important + extras:
        if used + 1 + len(line) <= limit - len(omitted) - 1:
            lines.append(line)
            used += 1 + len(line)
        else:
            dropped = True
    if dropped:
        lines.append(omitted)
    return "\n".join(lines)


# --- capturing ------------------------------------------------------------------------------
def board_now(brain):
    """The board now (board.py): the app's, or one read off the brain's parts (tests, benches)."""
    from ..board import Board

    if getattr(brain, "board", None) is not None:
        return brain.board.now()
    services = brain.services
    return Board(brain.hub, services.magnifier if services is not None else None, getattr(brain, "presenter", None),
                 brain.sight, listen=False).now()


WHO = {"click": "the user's"}
STATUS_WORDS = {"outline": "not named yet", "looking": "being named"}


def _outlined(state, info, positions: bool, *, workspace_scoped=False, provenance=False) -> list[str]:
    """One row per outline: for the brain and the fast pass where it is (a box on 0-1 of the
    view) and how big, its status, who made it ("yours": the model's own, the app's; "the user's"), and whether it is
    selected or magnified, and with `provenance` (the brain's rows, option ROW_PROVENANCE) which models made it; for
    the voice the id, the name and which models made it. Cells are one line."""
    rows, away = [], []
    for item in state.outlines:
        label = item.label or "unnamed outline"
        extra = " part of " + item.parent if item.parent else ""
        if not item.in_view:
            # Still kept (it comes back when the slide does), but not something to point at now.
            away.append(f'{item.id} "{label}" (out of view now: do not select, zoom or point at it){extra}')
            continue
        if not positions or info is None:
            # The voice: how it came to be, each model in order ("found by Gemini 3.8 Flash, drawn by EdgeTAM (on this
            # Mac), named by Gemini 3.8 Flash"; "found by your click at 00:47:31"), so "what did you use?" and "why that
            # one?" are answered from facts (known.chain).
            made = getattr(item, "made", None)
            rows.append(f'{item.id} "{label}"{f" ({made})" if made else ""} {extra}')
            continue
        x0, y0, x1, y1 = item.box
        w, h = float(info.width), float(info.height)
        share = f"{item.share * 100:.1f}%" if item.share >= 0.001 else "<0.1%"
        notes = [f"box x {x0 / w:.2f}-{x1 / w:.2f}, y {y0 / h:.2f}-{y1 / h:.2f}, {share} of the view",
                 STATUS_WORDS.get(item.status, item.status),
                 WHO.get(item.source, "yours")]
        if provenance and getattr(item, "made", None):
            notes.append(f"made: {item.made}")  # which models, from its own record (known.chain)
        if item.taught:
            # A fact from the user's own examples (taught.py), beside the model's name, never instead of it.
            notes.append(f'closest to the user\'s own "{inline(item.taught["label"], 60)}" examples '
                         f'(similarity {item.taught["similarity"]:.2f})')
        if item.moved:
            # Measured by the follower, slide motion taken out (trail.py); only when it went somewhere.
            notes.append(f"moved on its own: {item.moved}")
        if item.id == state.selected:
            notes.append("selected")
        if not workspace_scoped and item.id == state.magnifier["object"]:
            notes.append("in the magnifier")
        rows.append(f'{item.id} "{label}" ({"; ".join(notes)}){extra}')
    # Evidence (contract 9): what a cell finder or a matcher found, as a population; query_population
    # gives its counts and measurements, highlight shows members of it.
    tail = [f"Evidence: {line}" for line in getattr(state, "evidence", ())]
    # What the studio reports it shows (contract 10), so "the outlined cells" or "the notes" mean what is there.
    if not workspace_scoped:
        tail += [f"On screen: {line}" for line in getattr(state, "shown", ())]
    return rows[-20:] + away[-6:] + tail


def _screen(brain, for_voice: bool, request_snapshot=None) -> str:
    """What the user has on screen besides the live view, in a line."""
    if request_snapshot is not None:
        from ..prompts import OBSERVATION_BASIS

        wire = request_snapshot.observation.wire()
        facts = {"snapshotId": request_snapshot.snapshot_id,
                 "client": wire["client"], "seq": wire["seq"], "timing": request_snapshot.timing,
                 "reportAgeMs": request_snapshot.report_age_ms, "availability": request_snapshot.availability,
                 "viewport": wire["viewport"], "complete": wire["complete"], "omitted": wire["omitted"],
                 "items": [], "selected": wire["interaction"]["selected"],
                 "targets": wire["targets"][:8], "projectionOmittedTargets": max(0, len(wire["targets"]) - 8),
                 "pointer": wire["interaction"]["pointer"], "focus": wire["interaction"]["focus"],
                 "lastContentPointer": wire["interaction"]["lastContentPointer"],
                 "lastContentFocus": wire["interaction"]["lastContentFocus"]}
        source_map = {source["key"]: source for source in wire["sources"]}
        # The model needs every actionable window and what it contains. Revision
        # and owner fences remain in the immutable server pin, not in its prose.
        # Never evict a second window to repeat the first window's control fences.
        # A window the user or a step closed is not on screen: listed apart, its id and title only (enough to open it
        # again), never among what is shown (live 2026-10-11: "what do you see" named a closed diagram as on screen).
        shown_items = [i for i in wire["items"] if i.get("open") is not False]
        closed = [i for i in wire["items"] if i.get("open") is False]
        if closed:
            facts["closedNotOnScreen"] = [{"id": i["id"], "title": inline(i.get("title"), 120)} for i in closed[:12]]
        ordered = sorted(shown_items, key=lambda i: (not i["id"].startswith("w-"), not i["visible"]))
        for item in ordered[:24]:
            source = source_map.get(item.get("sourceKey"), {})
            identity, provenance = source.get("identity", {}), source.get("provenance", {})
            row = {k: item[k] for k in ("id", "title", "kind", "rect", "visible", "open", "view") if k in item}
            row["content"] = {"kind": source.get("kind"), "media": source.get("media"),
                              "resource": identity.get("resource"), "object": identity.get("object"),
                              "basis": provenance.get("basis"), "researcher": provenance.get("researcher")}
            web = provenance.get("web")
            if isinstance(web, dict):
                row["content"]["web"] = {key: inline(web.get(key), limit)
                                         for key, limit in (("page", 1000), ("site", 200), ("title", 200))}
            row["state"] = (item.get("load") or {}).get("state")
            row["controls"] = [c["id"] for c in item["controls"] if c.get("present") and c.get("enabled")]
            facts["items"].append(row)
        facts["projectionOmittedItems"] = max(0, len(ordered) - 24)
        def encoded():
            return json.dumps(facts, ensure_ascii=True, separators=(",", ":"))

        # Make the actual display inventory readable beside older conversation
        # claims. Absence is only meaningful for a complete, current report.
        from ..screen import laid_out

        background = wire["viewport"].get("visible") is False
        visible = [i["id"] for i in shown_items if i["visible"] or (background and laid_out(i))]
        hidden = [i["id"] for i in shown_items if i["id"] not in visible]
        coverage = ("Complete" if wire["complete"] and not any(wire["omitted"].values())
                    and request_snapshot.availability != "stale" else "Partial or stale")
        inventory = ("The studio is in the background (another app or tab is in front of it): nothing in it is "
                     "painted now, and the surfaces listed visible show again once it is in view.\n"
                     if background else "")
        inventory += (f"{coverage} display inventory at request Send: visible surfaces {visible}; "
                     f"reported nonvisible surfaces {hidden}"
                     + (f"; closed, not on screen: {[i['id'] for i in closed]}" if closed else "")
                     + ". Current action receipts supersede this starting state; "
                     "earlier conversation claims do not establish what is displayed now.\n")
        from ..workspace_observation import front

        # The Tab in front holds the specimen too (the live view: filling it, floating, or hidden; its Tab strip says).
        shown_front = front(wire)
        specimen = specimen_words(shown_front.get("specimen"))
        if specimen:
            inventory += f"On the Tab in front, the specimen (the live view) {specimen}.\n"
        # What each Tab holds, by kind (workspace-model.ts): "which Tab has the measurements?" is read from these.
        held, drawn = holds_words(shown_front.get("holds")), overlay_words(shown_front.get("overlays"))
        if held:
            inventory += f"The Tab in front holds {held}.\n"
        if drawn:
            inventory += f"Drawn on its specimen: {drawn}.\n"
        others = other_tabs_words(wire)
        if others:
            inventory += others + ".\n"
        inventory += ("Resolve the original versus new reference from these exact window resource IDs, "
                      "source websites and this conversation's action receipts, not a nearby Made today entry. "
                      "When the requested retained source is already visible, keep that window without another action; "
                      "do not create a duplicate unless asked. If its identity is ambiguous, ask rather than substitute.\n")
        return (inventory + "Originating workspace structural facts (no attached pixels): "
                + encoded() + "\n" + OBSERVATION_BASIS)
    scene = brain.hub.scene
    bits = []
    magnifier = brain.services.magnifier if brain.services is not None else None
    if magnifier is not None and magnifier.open:
        obj = scene.objects.get(magnifier.object_id) if magnifier.object_id else None
        source = magnifier.sources[magnifier.index] if magnifier.sources else {"kind": "live"}
        kind = source["kind"]
        level = getattr(magnifier, "level", None) or 1
        closer = f" ×{round(level, 1):g}" if kind in ("live", "held") and level > 1 else ""
        # A picture said as what it is (media.py: a reference photo from the web is not this slide; an AI
        # illustration is not the specimen), so no answer calls one real that is not (kind (f)).
        picture = MEDIA_WORDS.get(source.get("media") or "unknown", MEDIA_WORDS["unknown"])
        what = {"live": f"live zoom{closer or ' fitted'}", "held": f"its sharpest frame{closer}",
                "image": picture}.get(kind, kind)
        beside = magnifier.shown_beside() if hasattr(magnifier, "shown_beside") else None
        if beside is not None:
            what += f", beside {MEDIA_WORDS.get(beside.get('media') or 'unknown', MEDIA_WORDS['unknown'])}, in the one window"
        # Over what it shows only because someone put it there (the studio's report; the review of G3, NOTE 2).
        report = getattr(brain.hub, "screen", None)
        item = report.item("magnifier") if report is not None and hasattr(report, "item") else None
        covers = ", covering it" if obj is not None and (item or {}).get("covers") else ""
        subject = inline(source.get("label") or picture, 140) if kind == "image" else \
            (obj.id + " " + repr(obj.label) if obj else picture)
        bits.append(f"the magnifier is open on {subject} ({what}){covers}")
        if magnifier.sources:
            choices = []
            for option in magnifier.sources:
                key = option.get("url") if option.get("kind") == "image" else option.get("kind")
                words = MEDIA_WORDS.get(option.get("media") or "unknown", MEDIA_WORDS["unknown"])
                if option.get("kind") == "image":
                    words += f", image display ×{option.get('displayScale', 1):g}; enlarge with magnifier_picture"
                identity = option.get("resource")
                if identity:
                    words += f", retained resource {identity['id']} from {identity['day']}"
                selected = ", selected" if option is source else ""
                choices.append(f"{inline(key or '', 240)}: {inline(option.get('label') or '', 100)} ({words}{selected})")
            bits.append("magnifier sources (switch with magnifier_source; preserves the others): " + "; ".join(choices))
    shown = brain.presenter.state_text(answers=not for_voice) if brain.presenter is not None else ""
    if shown:
        bits.append(shown)
    cell = selected_cell(brain.hub)
    if cell:
        bits.append(f"{cell} is selected")
    elif scene.selected in scene.objects:
        obj = scene.objects[scene.selected]
        bits.append(f"{obj.id} {obj.label or 'an outline'!r} is selected")
    return "; ".join(bits) or "just the live view"


def selected_cell(hub, cell: tuple[str, str] | None = None) -> str:
    """The cell the user selected on the shown layer ("c12, a cell of Cell outlines · 18:04"), or "": the
    latest selection, as an outline selected since clears it (M1.5c review S3). `cell`: (population, member)
    as it was when a request was made (board.WhenAsked), instead of now."""
    if cell is None:
        layer = getattr(hub, "layer", None)
        if layer is None or not getattr(layer, "selected", None) or not getattr(layer, "shown", None):
            return ""
        population, member = layer.population(), layer.selected
    else:
        from ..evidence import evidence_of

        population, member = evidence_of(hub).get(cell[0]), cell[1]
    if population is None or member not in population.members:
        return ""  # only a cell of the population shown (re-check S3)
    return f"{member}, a cell of {population.title}"


def selected_now(hub) -> str | None:
    return selected_cell(hub) or hub.scene.selected


def selected_as_asked(hub, when_asked) -> str | None:
    """What was selected when the request was made (board.WhenAsked), and what is now when that changed since:
    the models are told what "that" means to the tools (C2), and what the user has selected while they answer."""
    now = selected_now(hub)
    if when_asked is None:
        return now
    then = (selected_cell(hub, when_asked.cell) if when_asked.cell else "") or when_asked.selected
    return then if then == now else f"{then or 'nothing'} when the user asked, {now or 'nothing'} now"


def _roster(research, n: int = 4) -> str:
    """Researchers in a line, in the Project view's one order (project_state.ordered: running first, then finished,
    then stopped; newest first in each), at most `n`, so a stopped one comes last or not at all: who made what, and
    whether it is ready."""
    if research is None:
        return ""
    from ..project_state import ordered

    rows = []
    for s in ordered(research.researchers())[:n]:
        title = inline((s.result or {}).get("title") or s.label or s.brief, 60)
        open_ = next((q for q in getattr(s, "requests", None) or [] if q.get("status") == "asked"), None)
        asking = f", asking the user for {inline(open_.get('what'), 80)} ({open_['id']})" if open_ else ""
        rows.append(f"{s.name}: {ROSTER_MADE.get(s.deliverable or '', 'research')} about \"{title}\", {s.status}{asking}")
    return "; ".join(rows)


def _research_note(text: str) -> str | None:
    """A researcher's lifecycle note (announcer._research_context: JSON facts in an untrusted block) as what it
    found, for the recap: its name, status and one-liner, never the opening characters of its JSON."""
    start, end = text.find("{"), text.rfind("}")
    try:
        facts = json.loads(text[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        return None
    if not isinstance(facts, dict) or not facts.get("researchId"):
        return None
    result = facts.get("result") if isinstance(facts.get("result"), dict) else {}
    found = result.get("one_liner") or result.get("summary") or ""
    who = facts.get("name") or facts["researchId"]
    return f"{who} {facts.get('status') or 'changed'}" + (f": {found}" if found else "")


def _recap_lines(conversation) -> list[str]:
    lines = []
    for t in conversation.window(5):
        if t.role == "note":
            said = _research_note(t.text)  # notes quote researchers
            lines.append(f"(the app: {inline(said if said is not None else t.text, 140)})")
        else:
            who = "User" if t.role == "user" else "You"
            lines.append(f"{who}{' (typed)' if t.channel == 'chat' else ''}: {t.text[:160]}")
        resources = (t.refs or {}).get("resourceIds") or []
        if resources:
            lines.append("App resource receipts: " + ", ".join(inline(rid, 80) for rid in resources[:4]))
    return lines


def digest(day, services) -> str:
    """The rest of the session in a few lines (day/context.py); empty for a session with nothing else."""
    from ..day.context import day_digest

    if day is None:
        return ""
    memory = services.memory if services is not None else None
    research = services.research if services is not None else None
    return day_digest(day, research, memory.facts if memory is not None else None)
