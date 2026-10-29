"""Hands: the one-brain partner acting in Hooke, visibly and verified (spec step 1, section 4.2;
docs/architecture/on-screen.md "Hands: the partner's drive").

The partner acts through Hooke's own operations (the registry's tools), never through pixels. Every acting tool goes
through Hands, for the voice and for think's backend alike. Each action has a contract and ends in a receipt:
- a **typed intent** (`intent_of`): its kind, its target, whether it acts on Hooke's UI, and how its receipt is checked
  (the studio's report, or Hooke's own record of it: records.py). A tool whose run is a look (a count, a reading) has no
  intent: its numbers are its answer, never "done";
- the **target by id**: a target nothing on screen or in the pointing history knows is filled when one candidate of its
  kind is there and asked about when two or more are, never guessed;
- a **precondition** at the moment of dispatch: the Project, the screen and the control epoch it was planned against
  (the voice turn's key-down, or the call's start), never the ones at call time;
- an **action id**, so a retry returns the earlier receipt and is never a second effect;
- **permission** once, by voice, for an action that spends or deletes;
- for UI actions, a **visible cue** (`drive`: the studio's partner cursor) under the partner's **lease** (the control
  version), one at a time;
- the **receipt**: verified, failed(reason), uncertain or running, decided from the app (the tool's own run, the
  studio's report showing what was asked, Hooke's record of what it made), and the voice says exactly that (`said`);
- one **Activity** line per action, beside the user's own.

The user keeps control (the spec's three things, kept apart):
- **talking over it** (the talk key) is a pause: queued UI actions are not run and end with a receipt saying the user
  spoke, so the voice re-plans from the new words; an action already being done finishes and its receipt is said;
- **taking over** (the mouse over the window, Escape) raises the lease: queued UI actions are dropped, the one being
  done is stopped where the tool can stop (its Stop holder) and is otherwise uncertain now and reconciled in the
  background;
- either way, background work (researchers, capability runs, think) goes on, and acting on the UI again needs a new
  lease and a fresh look.

Think-check-2 and the broad journey (29 October): 7 of 13 failed steps were actions in Hooke unconfirmed or on the
wrong target, and 3 more said "done" without the app's confirmation.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import itertools
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

WHOLE_PICTURE = (0.04, 0.04, 0.92, 0.92)  # a frame target's region for the whole picture: x, y, width, height
CUE_WAIT_S = 0.5  # the studio's drive-shown, at most; the cue never blocks the action
RECONCILE_S = 30.0  # after a takeover, a cut-off effect is reconciled in the background for this long
APPROVAL_S = 120.0  # a permission asked is good for this long
RESULTS = ("verified", "failed", "uncertain", "running")
KEPT_RESULTS = ("verified", "uncertain", "running")  # a retry of these returns the receipt; a failed one may be tried again

# Actions checked against Hooke's own record of them (records.py), never the screen: intent kind and caption.
RECORDED = {
    "start_researcher": ("research", "Starting a researcher"),
    "cancel_researcher": ("cancel", "Stopping the researcher"),
    "researcher_note": ("steer", "Passing the note on"),
    "researcher_followup": ("steer", "Asking the researcher"),
    "revise_work": ("steer", "Asking for a new version"),
    "run_capability": ("run", "Running it"),
    "workflow": ("run", "Running the workflow"),
    "set_default": ("setting", "Changing the setting"),
    "set_research_cap": ("setting", "Changing the cap"),
    "use": ("choice", "Choosing the model"),
}
# Project and stage actions: on Hooke's UI (lease, cue, one at a time), checked against the record and the studio.
PROJECT_TOOLS = frozenset({"new_project", "new_session", "switch_session"})
# Tools whose run is a look even where the registry calls it an effect: their result is the answer, never "done".
LOOKS = frozenset({"show_me"})
# Families whose effect with nothing saved changes Hooke's UI (an effect elsewhere is background work).
UI_FAMILIES = frozenset({"scene", "display", "magnifier", "results", "map"})
SPOKE = ("not done: the user started speaking before it ran, so nothing was done; plan again from their new words "
         "(what they asked before still stands unless they changed it)")
TOOK_OVER = "not done: the user took over (mouse or Escape) before it ran; nothing was done"
LOOK_AGAIN = "not done: the user took over; look at the screen again (the next turn's pictures) before acting on it"


@dataclass
class Intent:
    """One action in Hooke: its kind, the tool and arguments that do it, its target (marks grammar) and caption, whether
    it acts on Hooke's UI, and how its receipt is checked ("screen": the studio's report; "record": Hooke's own record)."""
    kind: str
    tool: str
    args: dict
    target: str = ""
    caption: str = ""
    ids: list[str] = field(default_factory=list)  # what the verifier expects (outline, member, Tab, window ids)
    ui: bool = True
    check: str = "screen"
    background: bool = False  # long work that ends on screen (a reference photo): never stopped by a takeover
    model: str = ""  # outline(model, target): the model it was asked to use, when the call says
    version: int = 0
    revision: int | None = None
    id: str = field(default_factory=lambda: f"dr-{uuid.uuid4().hex[:8]}")
    project: str | None = None  # the precondition: the Project it was planned in
    before: dict | None = None  # the screen it was planned against (its facts), for what the receipt says
    record: dict | None = None  # Hooke's record before the effect (records.Records.before)


@dataclass
class Plan:
    """What an action was planned against: the voice turn (its key-down; None for think, planned at the call's start),
    the takeover epoch, the Project, and the screen (its report's revision and facts) as the planner last saw them."""
    turn: int | None
    epoch: int
    project: str | None
    revision: int | None
    facts: dict
    at: float


def _target_of(value) -> str:
    if isinstance(value, dict):
        for key in ("instanceId", "id", "placement", "target"):
            if isinstance(value.get(key), str):
                return value[key]
        return ""
    return str(value or "").strip()


def intent_of(tool: str, args: dict, *, kind: str = "effect", produces: str | None = None,
              family: str = "", sets: tuple = ()) -> Intent | None:
    """The typed intent of an acting call; None for a look. `kind`, `produces`, `family` and `sets` are the registry's
    own words for the tool (Tool.kind, Tool.produces, Tool.family, Tool.sets): an effect without a typed intent here
    gets the generic one, checked against the settings it declares it changes, what it saved, or, with nothing saved,
    on screen."""
    args = dict(args or {})
    if tool == "present":
        verb = str(args.get("do") or "").lower()
        what = _target_of(args.get("what") or args.get("instance"))
        kind_ = {"open": "open", "close": "close"}.get(verb, "present")
        caption = {"open": "Opening it", "close": "Closing it"}.get(verb, f"{verb.capitalize() or 'Showing'} it")
        return Intent(kind_, tool, args, target=_window_target(what), caption=caption, ids=[what] if what else [])
    if tool == "tabs":
        action, tab = str(args.get("action") or ""), _target_of(args.get("tab"))
        if action in ("list", ""):
            return None
        kind_ = {"switch": "focus", "create": "open", "close": "close"}.get(action, "present")
        caption = {"switch": "Switching Tabs", "create": "Making a Tab", "close": "Closing the Tab",
                   "rename": "Renaming the Tab", "add": "Adding it to the Tab"}.get(action, "Arranging Tabs")
        return Intent(kind_, tool, args, target=f"tab:{tab}" if tab else "panel:workspace-tabs", caption=caption,
                      ids=[tab] if tab else [])
    if tool == "show_measurements":
        tab = _target_of(args.get("tab"))
        return Intent("open", tool, args, target=f"tab:{tab}" if tab else "panel:workspace-tabs",
                      caption="Opening the table", ids=[_target_of(args.get("of"))] if args.get("of") else [])
    if tool == "highlight":
        things = [str(t) for t in (args.get("things") or args.get("members") or []) if t]
        return Intent("highlight", tool, args, target=_thing_target(things[0]) if things else "panel:outlines",
                      caption="Highlighting", ids=things)
    if tool == "select":
        thing = _target_of(args.get("object"))
        return Intent("select", tool, args, target=_thing_target(thing) if thing else "", caption="Selecting it",
                      ids=[thing] if thing else [])
    if tool == "zoom":
        thing = _target_of(args.get("object"))
        return Intent("zoom", tool, args, target=_thing_target(thing) if thing else "panel:magnifier",
                      caption="Zooming in", ids=[thing] if thing else [])
    if tool == "clear":
        return Intent("close", tool, args, target="panel:outlines", caption="Clearing outlines")
    if tool == "outline_cells":
        return Intent("outline", tool, args, target="frame", caption="Outlining")
    if tool == "find":  # a search: its receipt says what was found, never "done"
        inside = _target_of(args.get("inside"))
        return Intent("find", tool, args, target=_thing_target(inside) if inside else "frame", caption="Looking for it",
                      model=str(args.get("by") or ""))
    if tool in PROJECT_TOOLS:
        caption = "Opening the Project" if tool == "switch_session" else "Making a new Project"
        return Intent("project", tool, args, target="panel:sidebar", caption=caption, check="record",
                      ids=[str(args.get("session") or args.get("name") or "")])
    if tool == "add_asset":  # open(asset): one of the user's recordings or pictures, on stage
        aid = _target_of(args.get("asset"))
        return Intent("open", tool, args, target="panel:live", caption="Putting it on stage", check="record",
                      ids=[aid] if aid else [])
    if tool == "reference_photo":  # long work that ends in a window: checked in the report when it lands
        return Intent("open", tool, args, target="panel:workspace", caption="Finding a picture", check="record",
                      background=True)
    if tool in RECORDED:
        kind_, caption = RECORDED[tool]
        return Intent(kind_, tool, args, target=_researcher_target(args) if kind_ in ("cancel", "steer") else "",
                      caption=caption, ui=False, check="record")
    if tool in LOOKS or kind == "query":
        return None
    if sets:  # it declares the settings it changes: they read back changed
        return Intent("setting", tool, args, caption="Changing the setting", ui=False, check="record")
    saved = bool(produces) and not str(produces).startswith(("none", "temporary"))
    if saved:  # analysis and the like: its record is what it saved (records.py), no UI lease
        return Intent("make", tool, args, caption="Working on it", ui=False, check="record")
    ui = family in UI_FAMILIES
    thing = _target_of(args.get("object"))
    return Intent("present" if ui else "do", tool, args, target=_thing_target(thing) if thing and ui else "",
                  caption="Changing the view" if ui else "Doing it", ui=ui, check="screen" if ui else "record",
                  ids=[thing] if thing else [])


def contract_of(tool: str, *, kind: str = "effect", produces: str | None = None, family: str = "",
                sets: tuple = ()) -> str:
    """How a tool's call ends, for the coverage test: "look" (its result is its answer), or the receipt's check ("screen",
    "record"). A tool whose intent depends on its arguments (tabs' list) is judged by its acting form."""
    probe = {"tabs": {"action": "switch", "tab": "t"}}.get(tool, {})
    intent = intent_of(tool, probe, kind=kind, produces=produces, family=family, sets=sets)
    return "look" if intent is None else intent.check


def _researcher_target(args: dict) -> str:
    ref = str(args.get("researcher") or "").strip()
    return f"researcher:{ref}" if ref and ref != "this" else ""


def _thing_target(thing: str) -> str:
    """A thing on the picture as a mark target: c12 is a member of the shown layer (resolved by the studio), o3 an
    outline, anything else as given."""
    if ":" in thing:
        return thing
    return f"outline:{thing}"


def _window_target(what: str) -> str:
    if not what:
        return ""
    if ":" in what:
        return what
    return f"window:{what}" if what.startswith("w-") else f"document:{what}" if what.startswith("p-") else what


# --- what the studio's report shows ------------------------------------------------------------------------------------
def _items(observation: dict | None) -> list[dict]:
    return list((observation or {}).get("items") or [])


def _legacy(item: dict) -> dict:
    return ((item.get("view") or {}).get("legacy") or {})


def screen_facts(observation: dict | None) -> dict:
    """What the verifier and the resolver read from a report: its seq, the Tab in front and the open Tabs, the windows
    and documents by id (with the Tab each is on), the window highest in front, the outlines drawn, emphasised members,
    the selection, the magnifier, the layers."""
    facts: dict[str, Any] = {"seq": (observation or {}).get("seq"), "tabs": [], "front": None, "windows": set(),
                             "documents": [], "top": None, "drawn": None, "emphasis": set(), "selected": [],
                             "magnifier": None, "layers": {}, "reported": observation is not None}
    for item in _items(observation):
        legacy, iid = _legacy(item), str(item.get("id") or "")
        if iid == "workspace-tabs":
            workspace = legacy.get("workspace") or {}
            facts["front"] = {"id": workspace.get("tab"), "title": workspace.get("tabTitle")}
            facts["tabs"] = list(workspace.get("tabs") or [])
        elif iid == "outlines":
            if "drawn" in legacy:
                facts["drawn"] = [d.get("id") for d in legacy.get("drawn") or []]
        elif iid == "magnifier":
            facts["magnifier"] = {"shows": legacy.get("shows"), "level": legacy.get("level"),
                                  "open": bool(item.get("open"))}
        elif iid.startswith("layer:"):
            facts["layers"][iid[len("layer:"):]] = {"members": legacy.get("members"), "open": bool(item.get("open"))}
            emphasis = (legacy.get("style") or {}).get("emphasis") or {}
            facts["emphasis"] |= set(emphasis.get("members") or [])
        if item.get("open", True):
            facts["windows"].add(iid)
            document = legacy.get("document") or {}
            for key in ("placement",):
                if document.get(key):
                    facts["windows"].add(str(document[key]))
            resource = document.get("resource") or {}
            if resource.get("id"):
                facts["windows"].add(str(resource["id"]))
            if document:
                facts["documents"].append({"window": iid, "placement": document.get("placement"),
                                           "tab": document.get("tab"), "resource": resource.get("id"),
                                           "representation": document.get("representation")})
    interaction = (observation or {}).get("interaction") or {}
    facts["selected"] = list(interaction.get("selected") or [])
    if observation is not None:
        with contextlib.suppress(Exception):
            from ..workspace_observation import front

            facts["top"] = front(observation).get("window")
    return facts


def _tab_is(tab: dict | None, name: str) -> bool:
    return bool(tab) and name in (tab.get("id"), tab.get("title"))


def _output_ids(result: dict, *kinds: str) -> list[str]:
    return [str(o.get("id")) for o in result.get("outputs") or []
            if isinstance(o, dict) and o.get("id") and (not kinds or o.get("kind") in kinds)]


def expected(intent: Intent, facts: dict, result: dict | None = None) -> bool | None:
    """Whether the report shows what the intent asked for: True, False, or None when this report cannot tell (an older
    studio without the field). Each is the state asked for, never only a report newer than the change."""
    ids = [i for i in intent.ids if i]
    result = result or {}
    front = facts["front"]
    if intent.tool == "tabs":
        action = str(intent.args.get("action") or "")
        if front is None and not facts["tabs"]:
            return None
        if action == "switch" and ids:
            return _tab_is(front, ids[0])
        if action == "close" and ids:
            return not any(_tab_is(t, ids[0]) for t in facts["tabs"])
        if action in ("create", "rename"):
            title = str(intent.args.get("title") or "").strip()
            if not title:
                return None
            made = next((t for t in facts["tabs"] if t.get("title") == title), None)
            if made is None:
                return False
            return action == "rename" or _tab_is(front, made.get("id"))  # a new Tab comes to the front
        if action == "add":
            resource = _target_of(intent.args.get("resource"))
            if not resource or not facts["documents"]:
                return None
            on = ids[0] if ids else (front or {}).get("id")
            return any(d["resource"] == resource and (on is None or d["tab"] == on
                                                      or _tab_is(next((t for t in facts["tabs"] if t.get("id") == d["tab"]),
                                                                      None), on))
                       for d in facts["documents"])
        return None
    if intent.tool == "show_measurements":
        # The table exists, is on the Tab in front, and shows the dataset asked for (when one was named).
        if front is None or not facts["reported"]:
            return None
        tab = _target_of(intent.args.get("tab"))
        if tab and not _tab_is(front, tab):
            return False
        here = [d for d in facts["documents"] if d["tab"] in (None, front.get("id"))]
        if ids:
            if not facts["documents"]:
                return None
            return any(d["resource"] == ids[0] for d in here)
        return None
    if intent.tool == "clear":
        if facts["drawn"] is None:
            return None
        what = str(intent.args.get("what") or "all")
        return what == "cells" or not [d for d in facts["drawn"] if d]
    if intent.kind == "highlight":
        if facts["drawn"] is None and not facts["emphasis"]:
            return None
        drawn = set(facts["drawn"] or [])
        if not ids:
            return bool(facts["emphasis"]) or bool(drawn)
        return all(i in drawn or i in facts["emphasis"] for i in ids)  # every one asked for, not some
    if intent.kind == "select" and ids:
        if not facts["selected"]:
            return None
        return ids[0] in facts["selected"]
    if intent.kind == "zoom":
        magnifier = facts["magnifier"]
        if magnifier is None:
            return None
        if not ids:
            return magnifier.get("open")
        if magnifier.get("shows") is None:
            return None
        return bool(magnifier.get("open", True)) and magnifier["shows"] == ids[0]
    if intent.kind == "outline":
        # The layer it made, with its members counted: a layer on screen without counts shows nothing of how many.
        # The studio names a population's layer by its presentation (population.ts: layer:<presentation id>), which
        # need not be the population's id, so a made population missing from the keys is no sign it failed; Hooke's
        # record of it decides (Hands.verify, _outlined).
        made = _output_ids(result, "population")
        layers = facts["layers"]
        if not layers:
            return None
        layer = layers.get(made[0]) if made else None
        if layer is not None:
            members = layer.get("members")
            return None if members is None else members.get("count", 0) > 0
        counted = [layer["members"] for layer in layers.values() if layer.get("members")]
        return None if not counted else any(m.get("count", 0) > 0 for m in counted)
    if intent.kind == "find":
        found = _output_ids(result, "outline", "named-outline", "population")
        if not found:
            return None
        if facts["drawn"] is None and not facts["layers"]:
            return None
        return all(f in set(facts["drawn"] or []) or f in facts["layers"] for f in found)
    if intent.tool == "present" and ids:
        shown = any(i in facts["windows"] or f"document:{i}" in facts["windows"] for i in ids)
        if intent.kind == "close":
            return not shown
        if intent.kind == "open":
            return shown if facts["reported"] else None
    if intent.tool == "close_magnifier" and facts["magnifier"] is not None:
        return not facts["magnifier"].get("open")
    return None


def candidates(intent: Intent, facts: dict, pointed: list[str]) -> list[str]:
    """What an unknown target could be: what was pointed at lately (newest first: marks, the user's own actions), then
    what the screen shows of the intent's kind (its Tabs for a Tab, its windows for a window, the selected or the one
    outline drawn for a thing on the picture)."""
    out: list[str] = []
    prefix = intent.target.split(":", 1)[0] if ":" in intent.target else ""
    on_screen = (facts["windows"] | {t.get("id") for t in facts["tabs"]} | {t.get("title") for t in facts["tabs"]}
                 | set(facts["drawn"] or []) | set(facts["selected"]))
    for target in pointed:
        if not prefix or target.startswith(prefix + ":"):
            bare = target.split(":", 1)[1] if ":" in target else target
            if bare in on_screen or f"document:{bare}" in on_screen:  # pointed at and still there
                out.append(bare)
    if intent.tool == "tabs" or intent.kind == "focus":
        out += [t.get("id") for t in facts["tabs"] if t.get("id")]
    elif intent.kind in ("close", "open") and intent.tool == "present":
        out += [w for w in facts["windows"] if w.startswith(("w-", "document:"))]
    elif intent.kind in ("select", "zoom", "highlight"):
        out += list(facts["selected"]) + [d for d in facts["drawn"] or [] if d]
    return list(dict.fromkeys(o for o in out if o))


def known(intent: Intent, facts: dict) -> bool:
    """Whether the intent's target is something the report shows (a Tab by id or title, a window, a document)."""
    ids = [i for i in intent.ids if i]
    if not ids:
        return True
    if intent.tool == "tabs" and intent.kind in ("focus", "close"):
        return not facts["tabs"] or any(_tab_is(t, ids[0]) for t in facts["tabs"])
    if intent.tool == "present" and intent.kind == "close":
        return not facts["windows"] or any(i in facts["windows"] or f"document:{i}" in facts["windows"] for i in ids)
    return True


def said(intent: Intent, result: dict, facts: dict, before: dict | None) -> str:
    """What the receipt is, in a few plain words for the voice to say as its own ("Done, Sizes is in front"): the
    facts of the receipt, never more than it shows."""
    receipt = result.get("result")
    why = str(result.get("error") or result.get("summary") or "").strip()
    if receipt == "failed":
        if intent.kind == "find":
            return f"Not found: {why}" if why else "Nothing was found."
        return f"It did not work: {why}" if why else "It did not work."
    if receipt == "running":
        return "It is under way, not done yet."
    if receipt == "uncertain":
        return f"Not sure it happened: {why}" if why else "Not sure it happened."
    front = (facts.get("front") or {})
    was = ((before or {}).get("front") or {})
    if intent.kind == "focus":
        name = front.get("title") or front.get("id") or "that Tab"
        back = was.get("title") or was.get("id")
        return f"Done: {name} is in front" + (f" (it was {back}, for \"go back\")" if back and back != name else "") + "."
    if intent.tool == "show_measurements":
        tab = front.get("title") or front.get("id")
        return "Done: the table is open" + (f" in {tab}" if tab else "") + "."
    if intent.kind == "close":
        return "Done: it is closed."
    if intent.kind == "highlight":
        return "Done: it is highlighted."
    if intent.kind == "zoom":
        return "Done: it is magnified."
    if intent.kind == "select":
        return "Done: it is selected."
    if intent.kind == "find":
        found = _output_ids(result, "outline", "named-outline", "population")
        return f"Found: {len(found)} outlined on screen." if found else "Found: outlined on screen."
    if intent.kind == "outline":
        layers = [layer.get("members") for layer in facts.get("layers", {}).values() if layer.get("members")]
        count = max((m.get("count", 0) for m in layers), default=0)
        return f"Done: {count} outlined." if count else "Done: outlined."
    return "Done."


# --- control, cue, takeover, verifier ---------------------------------------------------------------------------------
@dataclass
class Approval:
    id: str
    intent: Intent
    expires: float


class Hands:
    """One per partner: the control version and owner, the takeover epoch, the cancel signal, the cue's answers,
    approvals, receipts by action id, and the record checker (records.Records)."""

    def __init__(self, *, send: Callable[[dict], Awaitable[None]] | None, observe: Callable[[], dict | None],
                 log: Callable[[dict], None] = lambda record: None, hub=None, clock=time.monotonic,
                 records=None) -> None:
        self.send, self.observe, self.log, self.hub, self.clock = send, observe, log, hub, clock
        self.records = records  # Hooke's own record of each recorded action (records.Records); None: the screen only
        self.version = 0
        self.owner = "user"
        self.epoch = 0  # rises with each takeover (talk, mouse, Escape): a plan from before it is not run
        self.stopped_by = ""  # what the last takeover was
        self.cancel = asyncio.Event()
        self.active = 0
        self.looked = True  # a fresh look since the last takeover (the next turn's pictures, or a look)
        self.pointed: list[str] = []  # the conversation's pointing history, newest first (marks, the user's actions)
        self._shown: dict[str, asyncio.Future] = {}
        self._approvals: dict[str, Approval] = {}
        self._lock = asyncio.Lock()
        self._receipts: dict[str, dict] = {}  # action id -> its receipt (a retry is never a second effect)
        self._background: set[asyncio.Task] = set()
        self._stops: dict[str, Callable[[], None]] = {}  # UI actions being done -> their tool run's Stop
        self._moved: list[tuple[float, str]] = []  # the Projects the partner itself opened, and when
        self.project: Callable[[], str | None] | None = None  # the open Project, for the precondition
        self.turn: Callable[[], int] | None = None  # the voice's turn now, for "the user spoke since"
        self.reconciled: Callable[[Intent, dict], Awaitable[None]] | None = None  # a cut-off action's real receipt
        self._basis: dict | None = None  # what the planner last saw (fresh_look)
        self._ids = itertools.count(1)

    # --- facts --------------------------------------------------------------------------------------------------
    def facts(self) -> dict:
        try:
            return screen_facts(self.observe())
        except Exception:
            return screen_facts(None)

    def _project_now(self) -> str | None:
        if self.project is None:
            return None
        with contextlib.suppress(Exception):
            return self.project()
        return None

    def point(self, targets: list[str]) -> None:
        for target in targets:
            if target and target != "frame":
                self.pointed = [target] + [t for t in self.pointed if t != target]
        del self.pointed[20:]

    def fresh_look(self) -> None:
        """The planner has a fresh look (a turn's key-down pictures, or the look after an action): control may come back
        to the partner, and what it plans next is planned against this screen and Project."""
        self.looked = True
        facts = self.facts()
        self._basis = {"facts": facts, "project": self._project_now(), "at": self.clock(), "revision": facts.get("seq")}

    def plan(self, turn: int | None = None, *, fresh: bool = False) -> Plan:
        """What a call is planned against: the last fresh look (the voice's), or the screen now (think's call start)."""
        basis = self._basis
        if fresh or basis is None:
            facts = self.facts()
            basis = {"facts": facts, "project": self._project_now(), "at": self.clock(), "revision": facts.get("seq")}
        return Plan(turn, self.epoch, basis["project"], basis["revision"], basis["facts"], basis["at"])

    # --- control ------------------------------------------------------------------------------------------------
    async def _send(self, message: dict) -> None:
        if self.send is not None:
            with contextlib.suppress(Exception):
                await self.send(message)

    async def _take(self) -> None:
        if self.owner != "partner":
            self.owner, self.version = "partner", self.version + 1
            self.cancel = asyncio.Event()
            await self._send({"type": "control", "version": self.version, "owner": "partner"})

    async def _release(self) -> None:
        if self.active == 0 and self.owner == "partner":
            self.owner, self.version = "user", self.version + 1
            await self._send({"type": "control", "version": self.version, "owner": "user"})

    async def takeover(self, message: dict) -> None:
        """The user's input while the partner acts. The talk key pauses: the lease rises so nothing queued runs, and what
        is being done finishes. The mouse or Escape takes over: the lease rises, the cancel signal is set, and what is
        being done is stopped where its tool can stop. Either way the studio is answered with the time the server
        knew, and the UI waits for a fresh look."""
        at = time.time() * 1000
        why = str(message.get("why") or "")
        stale = message.get("version") not in (None, self.version)
        if self.owner != "partner":
            self.log({"kind": "partner-takeover", "why": why, "version": message.get("version"), "ignored": "not acting",
                      "atStudio": message.get("at"), "atServer": at})
            return
        self.owner, self.version, self.epoch = "user", self.version + 1, self.epoch + 1
        self.stopped_by = "talk" if why == "talk" else "takeover"
        self.looked = False
        stopped = []
        if why != "talk":
            self.cancel.set()
            for drive_id, stop in list(self._stops.items()):
                with contextlib.suppress(Exception):
                    stop()
                    stopped.append(drive_id)
        self.log({"kind": "partner-takeover", "why": why, "version": self.version, "epoch": self.epoch,
                  "atStudio": message.get("at"), "atServer": at, "stale": stale, "stopped": stopped,
                  "pause": why == "talk"})
        await self._send({"type": "control", "version": self.version, "owner": "user", "ack": True, "at": at})

    def shown(self, message: dict) -> None:
        future = self._shown.get(str(message.get("id") or ""))
        if future is not None and not future.done():
            future.set_result(message)

    # --- the cue ------------------------------------------------------------------------------------------------
    async def _cue(self, intent: Intent, frame: dict | None) -> dict | None:
        message = {"type": "drive", "id": intent.id, "version": intent.version,
                   "intent": {"kind": intent.kind, "target": intent.target or "panel:workspace",
                              "caption": intent.caption,
                              # The whole picture (outlining it): the studio rings a frame target only with a region
                              # (marks.ts frameShape), so it is the picture, a little inside its edges.
                              **({"region": list(WHOLE_PICTURE)} if intent.target == "frame" else {})},
                   **({"frame": frame} if frame else {})}
        future = asyncio.get_running_loop().create_future()
        self._shown[intent.id] = future
        sent = time.time()
        try:
            await self._send(message)
            done, _ = await asyncio.wait({future, asyncio.ensure_future(self.cancel.wait())}, timeout=CUE_WAIT_S,
                                         return_when=asyncio.FIRST_COMPLETED)
            answer = future.result() if future in done else None
        finally:
            self._shown.pop(intent.id, None)
        self.log({"kind": "partner-drive", "id": intent.id, "intent": intent.kind, "tool": intent.tool,
                  "target": intent.target, "sentT": sent, "shownT": time.time() if answer else None,
                  "ok": (answer or {}).get("ok"), "why": (answer or {}).get("why")})
        return answer

    # --- acting -------------------------------------------------------------------------------------------------
    def spends(self, intent: Intent) -> str | None:
        """Words for what an action spends or deletes, asked once; None for a reversible action in Hooke (starting a
        researcher at its usual effort asks nothing). Hooke's own record says what it costs (records.Records.spends)."""
        if self.records is not None:
            with contextlib.suppress(Exception):
                return self.records.spends(intent)
        return None

    async def act(self, tool: str, args: dict, effect: Callable[[], Awaitable[dict]], *, approved: str | None = None,
                  frame: dict | None = None, from_think: bool = False, action_id: str | None = None,
                  project: str | None = None, plan: Plan | None = None, stop: Callable[[], None] | None = None,
                  intent: Intent | None = None) -> dict:
        """One action under its contract (intent, target, precondition, action id, expected result), ending in its
        receipt: verified, failed(reason), uncertain or running, checked against the app's state."""
        intent = intent or intent_of(tool, args)
        if intent is None:
            return await effect()
        if action_id:  # a retry of the same action gets its receipt, never a second effect
            kept = self._receipts.get(action_id)
            if kept is not None:
                return {**kept, "note": "already done for this call; not done again"}
            intent.id = f"dr-{action_id[-12:]}"
        plan = plan or self.plan()
        if project is not None and plan.project is None:
            plan.project = project
        if intent.ui and not self.looked:
            return self._ended(intent, {"ok": False, "result": "failed", "error": LOOK_AGAIN,
                                        "_hands": {"rejected": "no fresh look since the takeover"}}, None, "failed")
        planned = known(intent, plan.facts)
        if not planned:
            options_ = candidates(intent, plan.facts, self.pointed)
            if len(options_) == 1:
                intent = self._retarget(intent, options_[0])
                planned = True
            elif options_:
                self._activity(intent, "done", f"Assistant: {intent.caption.lower()}: asked which one you meant "
                                               "(nothing done yet)")
                return {"ok": None, "result": "ask", "candidates": options_[:4],
                        "note": "Not done. Ask the user, in one short question, which one they mean.",
                        "_hands": {"intent": intent.kind, "ask": options_[:4]}}
        cost = self.spends(intent)
        if inspect.isawaitable(cost):
            try:
                cost = await cost
            except Exception:  # its cost could not be read: nothing is asked, and the tool's own checks still hold
                cost = None
        if cost and not self._approved(approved, intent):
            approval = Approval(f"ap-{next(self._ids)}", intent, self.clock() + APPROVAL_S)
            self._approvals[approval.id] = approval
            self._activity(intent, "done", f"Assistant: {intent.caption.lower()}: asked your permission ({cost}); "
                                           "nothing done yet")
            return {"ok": None, "result": "permission", "approval": approval.id,
                    "ask": f"{cost}. Go ahead?",
                    "note": "Not done. Ask the user this once; if they say yes, call it again with approved set to this "
                            "approval.", "_hands": {"intent": intent.kind, "permission": cost}}
        if not intent.ui:
            return await self._act_in_background(intent, effect, plan, action_id)
        self.active += 1
        try:
            await self._take()
            intent.version, intent.revision, intent.project = self.version, plan.revision, plan.project
            intent.before = plan.facts
            async with self._lock:  # one at a time; one queued behind a takeover is not run
                line = self._activity(intent, "running")
                # Checked at the moment of dispatch: the same lease and epoch, the same turn, the same Project, the
                # target still there.
                why = self._precondition(intent, planned, plan)
                if why is not None:
                    return self._ended(intent, {"ok": False, "result": "failed", "error": why,
                                                **({"paused": True} if why == SPOKE else {})}, line, "failed")
                intent.record = self._before(intent)
                # The cue runs alongside the action and never delays it (the spec, 4.2: the cursor is feedback).
                self._spawn(self._cue(intent, frame))
                if stop is not None and not intent.background:
                    self._stops[intent.id] = stop
                try:
                    outcome = await self._run(intent, effect, line)
                finally:
                    self._stops.pop(intent.id, None)
                await self._send({"type": "drive-end", "id": intent.id, "outcome": outcome["result"]})
                receipt = self._ended(intent, outcome, line, outcome["result"])
                self._keep(action_id, receipt)
                return receipt
        finally:
            self.active -= 1
            await self._release()

    async def _act_in_background(self, intent: Intent, effect, plan: Plan, action_id: str | None) -> dict:
        """An action that is not on Hooke's UI (a researcher, a capability run, a setting, an analysis): no lease and no
        cue, and a takeover never stops it; still its precondition, its Activity line and its checked receipt."""
        intent.revision, intent.project, intent.before = plan.revision, plan.project, plan.facts
        line = self._activity(intent, "running")
        why = self._precondition(intent, True, plan)
        if why is not None:
            return self._ended(intent, {"ok": False, "result": "failed", "error": why}, line, "failed")
        intent.record = self._before(intent)
        try:
            result = await effect()
        except Exception as exc:
            result = {"ok": False, "error": f"it did not run ({type(exc).__name__})"}
        outcome = await self.verify(intent, result)
        receipt = self._ended(intent, outcome, line, outcome["result"])
        self._keep(action_id, receipt)
        return receipt

    def _keep(self, action_id: str | None, receipt: dict) -> None:
        if action_id and receipt.get("result") in KEPT_RESULTS:
            self._receipts[action_id] = {k: v for k, v in receipt.items() if not k.startswith("_")}

    def keep(self, action_id: str | None, receipt: dict) -> None:
        """A receipt settled later (think waited for a long tool's outcome): a retry of its call returns it."""
        self._keep(action_id, receipt)

    def _before(self, intent: Intent) -> dict | None:
        if intent.check != "record" or self.records is None:
            return None
        with contextlib.suppress(Exception):
            return self.records.before(intent)
        return None

    def _precondition(self, intent: Intent, planned: bool, plan: Plan) -> str | None:
        if intent.ui:
            if plan.epoch != self.epoch or intent.version != self.version or self.cancel.is_set() \
                    or self.owner != "partner":
                return SPOKE if self.stopped_by == "talk" else TOOK_OVER
            if plan.turn is not None and self.turn is not None:
                with contextlib.suppress(Exception):
                    if self.turn() != plan.turn:
                        return SPOKE
        if plan.project is not None and self.project is not None:
            # The partner's own Project change since the plan is what it planned on (a new Project, then a Tab in it).
            meant = next((sid for at, sid in reversed(self._moved) if at >= plan.at), plan.project)
            if self._project_now() != meant:
                return "another Project opened before it ran; nothing was done"
        if intent.ui and planned and not known(intent, self.facts()):
            return "the screen changed before it ran; look again"
        return None

    def moved_to(self, sid: str | None) -> None:
        """The partner itself opened this Project: what it plans after this is planned in it."""
        if sid:
            self._moved = [*self._moved[-7:], (self.clock(), sid)]

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _retarget(self, intent: Intent, target: str) -> Intent:
        args = dict(intent.args)
        if intent.tool == "tabs":
            args["tab"] = target
        elif intent.tool == "present":
            args["what"] = target
        elif intent.tool in ("select", "zoom"):
            args["object"] = target
        found = intent_of(intent.tool, args) or intent
        return found

    def _approved(self, approval: str | None, intent: Intent) -> bool:
        found = self._approvals.pop(approval or "", None)
        return (found is not None and found.expires > self.clock() and found.intent.tool == intent.tool
                and found.intent.ids == intent.ids)

    async def _run(self, intent: Intent, effect: Callable[[], Awaitable[dict]], line: str | None) -> dict:
        task = asyncio.ensure_future(effect())
        if intent.background:  # long work behind the voice: it finishes whoever has control, and is checked then
            try:
                result = await task
            except Exception as exc:
                return {"ok": False, "result": "failed", "error": f"it did not run ({type(exc).__name__})"}
            return await self.verify(intent, result)
        stop = asyncio.ensure_future(self.cancel.wait())
        try:
            done, _ = await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not stop.done():
                stop.cancel()
        if task not in done:
            # Taken over mid-effect: control is the user's at once; the receipt now is uncertain, and the effect is
            # reconciled in the background (finished or stopped, it gets its real receipt in Activity and the log).
            self._spawn(self._reconcile(intent, task, line))
            return {"ok": None, "result": "uncertain", "error": "you took over while it was being done; it may or may "
                    "not have happened", "_hands": {"intent": intent.kind, "reconciling": True}}
        try:
            result = task.result()
        except Exception as exc:
            return {"ok": False, "result": "failed", "error": f"it did not run ({type(exc).__name__})"}
        return await self.verify(intent, result)

    async def _reconcile(self, intent: Intent, task: asyncio.Future, line: str | None) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait({task}, timeout=RECONCILE_S)
        if not task.done() or task.cancelled() or task.exception() is not None:
            receipt = {"ok": None, "result": "uncertain", "error": "cut off by the user's takeover; not known"}
        else:
            receipt = await self.verify(intent, task.result())
        receipt.pop("_run", None)
        ended = self._ended(intent, receipt, line, receipt["result"], reconciled=True)
        if self.reconciled is not None:
            with contextlib.suppress(Exception):
                await self.reconciled(intent, ended)

    async def verify(self, intent: Intent, result: dict) -> dict:
        """The receipt, from the app's state: the tool's own run (its record, and present's own receipt of the studio's
        report), and the studio's report showing what was asked (`expected`), or Hooke's record of what it made
        (records.py). A report that cannot show it is uncertain."""
        result = dict(result)
        run = result.pop("_run", None)
        if result.get("_repeat"):  # the same request's first call's receipt, already checked
            result.pop("_repeat", None)
            return result
        ok = result.get("ok")
        facts = self.facts()
        checked = None
        if intent.check == "record" and self.records is not None:
            try:
                checked = await self.records.check(intent, result, run, facts)
            except Exception as exc:  # a record that cannot be read is said as such, never as done
                checked = {"result": "uncertain", "error": f"Hooke's record of it could not be read ({type(exc).__name__})"}
        if checked is not None:
            result.update({k: v for k, v in checked.items() if v is not None})
            if intent.kind == "project" and checked.get("project"):  # it opened one, shown yet or not
                self.moved_to(checked["project"])
        elif ok is False:
            result["result"] = "failed"
            if result.get("stopped"):
                result["error"] = "stopped when you took over; nothing after the stop was applied"
        elif ok is None or result.get("still_working"):
            result["result"] = "running"
        else:
            receipt = (getattr(run, "outcome", None) or {}) if run is not None else {}
            status = receipt.get("status") if isinstance(receipt, dict) else None
            shows = expected(intent, facts, result)
            outlined = self._outlined(result) if intent.kind == "outline" else 0
            if status in ("failed", "timed_out"):
                result["result"] = "failed"
                why = (receipt.get("failure") or receipt.get("error") or {}) if isinstance(receipt, dict) else {}
                result["error"] = ("the studio did not show it: " + str(why.get("reason") or status)
                                   if isinstance(why, dict) else f"the studio did not show it ({status})")
            elif shows is True or (shows is None and status == "confirmed"):
                result["result"] = "verified"
            elif shows is False:
                result["result"] = "failed"
                result["error"] = "the studio does not show it: " + (result.get("summary") or "not on screen")
            elif shows is None and outlined:
                # The report cannot tell (it carries no member counts), but Hooke's own record of what it made can: the
                # population the run saved, with its members (smoke runs, 29 Oct: Cellpose outlined, said "not sure").
                result["result"] = "verified"
                result["said"] = f"Done: {outlined} outlined."
                result.setdefault("_hands", {})["checkedBy"] = "record"
            else:
                result["result"] = "uncertain"
                result["error"] = ("the studio did not report it (it may be in the background)" if status == "unobserved"
                                   else "the studio's report cannot show whether it happened")
        if not result.get("said"):
            result["said"] = said(intent, result, facts, getattr(intent, "before", None))
        result.setdefault("_hands", {})["expected"] = result["result"]
        return result

    def _outlined(self, result: dict) -> int:
        """How many members the populations this run made have in Hooke's evidence store (0 when none, or no hub)."""
        if self.hub is None:
            return 0
        try:
            from ..evidence import evidence_of

            store = evidence_of(self.hub)
            return sum(len(p.live()) for p in (store.get(eid) for eid in _output_ids(result, "population")) if p)
        except Exception:
            return 0

    def _activity(self, intent: Intent, state: str, text: str | None = None, receipt: str | None = None) -> str | None:
        if self.hub is None:
            return None
        from .. import activity

        line_id = f"command:hands-{intent.id}"
        with contextlib.suppress(Exception):
            activity.activity(self.hub, "command", text or f"Assistant: {intent.caption.lower()}", state=state,
                              id=line_id, details={"intent": intent.kind, "tool": intent.tool,
                                                   **({"target": intent.target} if intent.target else {}),
                                                   **({"ids": {"receipt": receipt}} if receipt else {})})
        return line_id

    def _ended(self, intent: Intent, outcome: dict, line: str | None, result: str, *, reconciled: bool = False) -> dict:
        outcome.setdefault("result", result)
        outcome.pop("_run", None)
        # Activity says the receipt as it is: an uncertain one is never "done", a running one is still going.
        state = {"verified": "done", "failed": "failed", "running": "running", "uncertain": "failed"}.get(
            outcome["result"], "done")
        words = {"verified": "done", "failed": "did not work", "running": "still going",
                 "uncertain": "not known whether it happened"}.get(outcome["result"], "")
        if outcome.get("paused"):
            words = "not done, you spoke first"
        elif outcome["result"] == "failed" and outcome.get("error") in (TOOK_OVER, LOOK_AGAIN):
            words = "not done, you took over"
        if line is not None:
            self._activity(intent, state, f"Assistant: {intent.caption.lower()}: {words}", receipt=outcome["result"])
        hands = outcome.setdefault("_hands", {})
        hands.update(intent=intent.kind, tool=intent.tool, target=intent.target, driveId=intent.id,
                     **({"model": intent.model} if intent.model else {}),
                     version=intent.version, revision=intent.revision, project=getattr(intent, "project", None),
                     result=outcome["result"], **({"reconciled": True} if reconciled else {}))
        self.log({"kind": "partner-hands", **hands, "error": outcome.get("error")})
        return outcome
