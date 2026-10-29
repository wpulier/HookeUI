"""Hands: the one-brain partner acting in Hooke, visibly and verified (spec step 1, section 4.2;
docs/architecture/on-screen.md "Hands: the partner's drive").

The partner acts through Hooke's own operations (the registry's tools), never through pixels. Hands wraps each call that
changes what is on screen, for the voice and for think's backend alike:
- a **typed intent** (`INTENTS`: open, close, focus, highlight, select, zoom, outline, present) with its target, taken
  from the tool and its arguments; a target nothing on screen or in the pointing history knows is filled when one
  candidate of its kind is there and asked about when two or more are, never guessed;
- a **visible cue** first (`drive`: the studio's partner cursor moves to the control or rings the target), under the
  partner's **control**, bound to a control version and the screen revision it was planned against and checked again
  just before the effect;
- **takeover**: the user's mouse, Escape or talk key raises the version, sets the one cancel signal the running
  intent's every await races against and discards what was queued; control is the user's at once, and an effect cut
  off is *uncertain* now and reconciled in the background (its real receipt in Activity when it finishes);
- a **verifier** that decides the result from the app, not the model: the tool's own run (report-confirmed tools fail
  when the studio does not show the change) and the studio's report showing what was asked (`expected`). The result is
  verified, failed, uncertain or running, and the voice says exactly that;
- **permission** once, by voice, for an intent that spends or deletes;
- one **Activity** line per intent, beside the user's own.

Think-check-2 and the broad journey (29 October): 7 of 13 failed steps were actions in Hooke unconfirmed or on the
wrong target, and 3 more said "done" without the app's confirmation.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

CUE_WAIT_S = 0.5  # the studio's drive-shown, at most; the cue never blocks the action
RECONCILE_S = 30.0  # after a takeover, a cut-off effect is reconciled in the background for this long
APPROVAL_S = 120.0  # a permission asked is good for this long
RESULTS = ("verified", "failed", "uncertain", "running")
# Tools whose own run fails when the studio does not show the change (present's completion, the Tab broker, the
# table window, the magnifier): their ok is the studio's report.
REPORT_CONFIRMED = frozenset({"present", "tabs", "show_measurements", "zoom"})


@dataclass
class Intent:
    """One action in Hooke: its kind, the tool and arguments that do it, its target (marks grammar) and caption."""
    kind: str
    tool: str
    args: dict
    target: str = ""
    caption: str = ""
    ids: list[str] = field(default_factory=list)  # what the verifier expects (outline, member, Tab, window ids)
    version: int = 0
    revision: int | None = None
    id: str = field(default_factory=lambda: f"dr-{uuid.uuid4().hex[:8]}")
    project: str | None = None  # the precondition: the Project it was planned in
    before: dict | None = None  # the screen it was planned against (its facts), for what the receipt says


def _target_of(value) -> str:
    if isinstance(value, dict):
        for key in ("instanceId", "id", "placement", "target"):
            if isinstance(value.get(key), str):
                return value[key]
        return ""
    return str(value or "").strip()


def intent_of(tool: str, args: dict) -> Intent | None:
    """The typed intent of a call that changes the screen (None for one that does not)."""
    args = dict(args or {})
    if tool == "present":
        verb = str(args.get("do") or "").lower()
        what = _target_of(args.get("what") or args.get("instance"))
        kind = {"open": "open", "close": "close"}.get(verb, "present")
        caption = {"open": "Opening it", "close": "Closing it"}.get(verb, f"{verb.capitalize() or 'Showing'} it")
        return Intent(kind, tool, args, target=_window_target(what), caption=caption, ids=[what] if what else [])
    if tool == "tabs":
        action, tab = str(args.get("action") or ""), _target_of(args.get("tab"))
        if action in ("list", ""):
            return None
        kind = {"switch": "focus", "create": "open", "close": "close"}.get(action, "present")
        caption = {"switch": "Switching Tabs", "create": "Making a Tab", "close": "Closing the Tab"}.get(action,
                                                                                                         "Arranging Tabs")
        return Intent(kind, tool, args, target=f"tab:{tab}" if tab else "panel:tabs", caption=caption,
                      ids=[tab] if tab else [])
    if tool == "show_measurements":
        tab = _target_of(args.get("tab"))
        return Intent("open", tool, args, target=f"tab:{tab}" if tab else "panel:tabs", caption="Opening the table",
                      ids=[_target_of(args.get("of"))] if args.get("of") else [])
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
    if tool in ("outline_cells", "find"):
        return Intent("outline", tool, args, target="frame", caption="Outlining")
    return None


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
    and documents by id, the outlines drawn, emphasised members, the selection, the magnifier, the layers."""
    facts: dict[str, Any] = {"seq": (observation or {}).get("seq"), "tabs": [], "front": None, "windows": set(),
                             "drawn": None, "emphasis": set(), "selected": [], "magnifier": None, "layers": {}}
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
    interaction = (observation or {}).get("interaction") or {}
    facts["selected"] = list(interaction.get("selected") or [])
    return facts


def _tab_is(tab: dict | None, name: str) -> bool:
    return bool(tab) and name in (tab.get("id"), tab.get("title"))


def expected(intent: Intent, facts: dict) -> bool | None:
    """Whether the report shows what the intent asked for: True, False, or None when this report cannot tell (an older
    studio without the field)."""
    ids = [i for i in intent.ids if i]
    if intent.kind == "focus" and ids:
        if facts["front"] is None:
            return None
        return _tab_is(facts["front"], ids[0])
    if intent.tool == "tabs" and intent.kind == "close" and ids:
        if not facts["tabs"]:
            return None
        return not any(_tab_is(t, ids[0]) for t in facts["tabs"])
    if intent.tool == "clear":
        if facts["drawn"] is None:
            return None
        what = str(intent.args.get("what") or "all")
        return what == "cells" or not [d for d in facts["drawn"] if d]
    if intent.kind == "highlight":
        members = [i for i in ids if i in facts["emphasis"]]
        if facts["drawn"] is None and not facts["emphasis"]:
            return None
        if not ids:
            return bool(facts["emphasis"]) or bool(facts["drawn"])
        drawn = set(facts["drawn"] or [])
        return all(i in drawn or i in facts["emphasis"] for i in ids) or bool(members)
    if intent.kind == "select" and ids:
        if not facts["selected"]:
            return None
        return ids[0] in facts["selected"]
    if intent.kind == "zoom" and ids:
        if facts["magnifier"] is None or facts["magnifier"].get("shows") is None:
            return None
        return facts["magnifier"]["shows"] == ids[0]
    if intent.kind == "outline" and intent.tool == "outline_cells":
        counted = [layer["members"] for layer in facts["layers"].values() if layer.get("members")]
        if not counted:
            return None if not facts["layers"] else True
        return any(m.get("count", 0) > 0 for m in counted)
    if intent.tool == "present" and intent.kind == "close" and ids:
        return not any(i in facts["windows"] or f"document:{i}" in facts["windows"] for i in ids)
    return None


def candidates(intent: Intent, facts: dict, pointed: list[str]) -> list[str]:
    """What an unknown target could be: what was pointed at lately (newest first), then what the screen shows of the
    intent's kind (its Tabs for a Tab, its windows for a window)."""
    out: list[str] = []
    prefix = intent.target.split(":", 1)[0] if ":" in intent.target else ""
    on_screen = facts["windows"] | {t.get("id") for t in facts["tabs"]} | {t.get("title") for t in facts["tabs"]}
    for target in pointed:
        if not prefix or target.startswith(prefix + ":"):
            bare = target.split(":", 1)[1] if ":" in target else target
            if bare in on_screen or f"document:{bare}" in on_screen:  # pointed at and still there
                out.append(bare)
    if intent.tool == "tabs" or intent.kind == "focus":
        out += [t.get("id") for t in facts["tabs"] if t.get("id")]
    elif intent.kind in ("close", "open") and intent.tool == "present":
        out += [w for w in facts["windows"] if w.startswith(("w-", "document:"))]
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
    """One per partner: the control version and owner, the cancel signal, the cue's answers, approvals."""

    def __init__(self, *, send: Callable[[dict], Awaitable[None]] | None, observe: Callable[[], dict | None],
                 log: Callable[[dict], None] = lambda record: None, hub=None, clock=time.monotonic) -> None:
        self.send, self.observe, self.log, self.hub, self.clock = send, observe, log, hub, clock
        self.version = 0
        self.owner = "user"
        self.cancel = asyncio.Event()
        self.active = 0
        self.looked = True  # a fresh look since the last takeover (the next turn's pictures, or a look)
        self.pointed: list[str] = []  # the conversation's pointing history, newest first (marks, pointed_at)
        self._shown: dict[str, asyncio.Future] = {}
        self._approvals: dict[str, Approval] = {}
        self._lock = asyncio.Lock()
        self._receipts: dict[str, dict] = {}  # action id -> its receipt (a retry is never a second effect)
        self._background: set[asyncio.Task] = set()
        self.project: Callable[[], str | None] | None = None  # the open Project, for the precondition
        self._ids = itertools.count(1)

    # --- facts --------------------------------------------------------------------------------------------------
    def facts(self) -> dict:
        try:
            return screen_facts(self.observe())
        except Exception:
            return screen_facts(None)

    def point(self, targets: list[str]) -> None:
        for target in targets:
            if target and target != "frame":
                self.pointed = [target] + [t for t in self.pointed if t != target]
        del self.pointed[20:]

    def fresh_look(self) -> None:
        self.looked = True

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
        """The user's mouse, Escape or talk key while the partner acts: the version rises, the cancel signal is set,
        and the studio is answered with the time the server knew."""
        at = time.time() * 1000
        why = str(message.get("why") or "")
        stale = message.get("version") not in (None, self.version)
        if self.owner != "partner":
            self.log({"kind": "partner-takeover", "why": why, "version": message.get("version"), "ignored": "not acting",
                      "atStudio": message.get("at"), "atServer": at})
            return
        self.owner, self.version = "user", self.version + 1
        self.cancel.set()
        self.looked = False
        self.log({"kind": "partner-takeover", "why": why, "version": self.version, "atStudio": message.get("at"),
                  "atServer": at, "stale": stale})
        await self._send({"type": "control", "version": self.version, "owner": "user", "ack": True, "at": at})

    def shown(self, message: dict) -> None:
        future = self._shown.get(str(message.get("id") or ""))
        if future is not None and not future.done():
            future.set_result(message)

    # --- the cue ------------------------------------------------------------------------------------------------
    async def _cue(self, intent: Intent, frame: dict | None) -> dict | None:
        message = {"type": "drive", "id": intent.id, "version": intent.version,
                   "intent": {"kind": intent.kind, "target": intent.target or "panel:workspace",
                              "caption": intent.caption}, **({"frame": frame} if frame else {})}
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
        """Words for what an intent spends or deletes, asked once; None for a reversible action in Hooke."""
        if intent.kind != "outline" or self.hub is None:
            return None
        try:
            from .. import options
            from ..capabilities import runs

            group = "cells" if intent.tool == "outline_cells" else "outline"
            chosen = getattr(self.hub.settings, group, None)
            option = options.GROUPS[group].get(chosen)
            if option is not None and "cloud" in runs(option.uses):
                return f"{option.label} runs in the cloud and is paid per use"
        except Exception:
            return None
        return None

    async def act(self, tool: str, args: dict, effect: Callable[[], Awaitable[dict]], *, approved: str | None = None,
                  frame: dict | None = None, from_think: bool = False, action_id: str | None = None,
                  project: str | None = None) -> dict:
        """One action under its contract (intent, target, precondition, action id, expected result), ending in its
        receipt: verified, failed(reason), uncertain or running, checked against the app's state."""
        intent = intent_of(tool, args)
        if intent is None:
            return await effect()
        if action_id:  # a retry of the same action (the same call) gets its receipt, never a second effect
            kept = self._receipts.get(action_id)
            if kept is not None:
                return {**kept, "note": "already done for this call; not done again"}
            intent.id = f"dr-{action_id[-12:]}"
        if from_think and not self.looked:
            return {"ok": False, "result": "failed", "error": "the user took over; look at the screen again before acting",
                    "_hands": {"intent": intent.kind, "rejected": "no fresh look since the takeover"}}
        facts = self.facts()
        planned = known(intent, facts)
        if not planned:
            options_ = candidates(intent, facts, self.pointed)
            if len(options_) == 1:
                intent = self._retarget(intent, options_[0])
                planned = True
            elif options_:
                return {"ok": None, "result": "ask", "candidates": options_[:4],
                        "note": "Not done. Ask the user, in one short question, which one they mean.",
                        "_hands": {"intent": intent.kind, "ask": options_[:4]}}
        cost = self.spends(intent)
        if cost and not self._approved(approved, intent):
            approval = Approval(f"ap-{next(self._ids)}", intent, self.clock() + APPROVAL_S)
            self._approvals[approval.id] = approval
            return {"ok": None, "result": "permission", "approval": approval.id,
                    "ask": f"{cost}. Go ahead?",
                    "note": "Not done. Ask the user this once; if they say yes, call it again with approved set to this "
                            "approval.", "_hands": {"intent": intent.kind, "permission": cost}}
        self.active += 1
        try:
            await self._take()
            intent.version, intent.revision, intent.project = self.version, facts.get("seq"), project
            intent.before = facts
            async with self._lock:  # one at a time; one queued behind a takeover is discarded
                line = self._activity(intent, "running")
                # Checked at the moment of dispatch: the same control lease, the same Project, the target still there.
                why = self._precondition(intent, planned, project)
                if why is not None:
                    return self._ended(intent, {"ok": False, "result": "failed", "error": why}, line, "failed")
                # The cue runs alongside the action and never delays it (the spec, 4.2: the cursor is feedback).
                self._spawn(self._cue(intent, frame))
                outcome = await self._run(intent, effect, line)
                await self._send({"type": "drive-end", "id": intent.id, "outcome": outcome["result"]})
                receipt = self._ended(intent, outcome, line, outcome["result"])
                if action_id and receipt["result"] != "running":
                    self._receipts[action_id] = {k: v for k, v in receipt.items() if not k.startswith("_")}
                return receipt
        finally:
            self.active -= 1
            await self._release()

    def _precondition(self, intent: Intent, planned: bool, project: str | None) -> str | None:
        if intent.version != self.version or self.cancel.is_set() or self.owner != "partner":
            return "the user took over before it ran; nothing was done"
        if project is not None and self.project is not None and self.project() != project:
            return "another Project opened before it ran; nothing was done"
        if planned and not known(intent, self.facts()):
            return "the screen changed before it ran; look again"
        return None

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
        found = intent_of(intent.tool, args) or intent
        return found

    def _approved(self, approval: str | None, intent: Intent) -> bool:
        found = self._approvals.pop(approval or "", None)
        return (found is not None and found.expires > self.clock() and found.intent.tool == intent.tool
                and found.intent.ids == intent.ids)

    async def _run(self, intent: Intent, effect: Callable[[], Awaitable[dict]], line: str | None) -> dict:
        task = asyncio.ensure_future(effect())
        stop = asyncio.ensure_future(self.cancel.wait())
        try:
            done, _ = await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not stop.done():
                stop.cancel()
        if task not in done:
            # Taken over mid-effect: control is the user's at once; the receipt now is uncertain, and the effect is
            # reconciled in the background (finished, it gets its real receipt in Activity and the log).
            self._spawn(self._reconcile(intent, task, line))
            return {"ok": None, "result": "uncertain", "error": "you took over while it was being done; it may or may "
                    "not have happened", "_hands": {"intent": intent.kind, "reconciling": True}}
        try:
            result = task.result()
        except Exception as exc:
            return {"ok": False, "result": "failed", "error": f"it did not run ({type(exc).__name__})"}
        return self.verify(intent, result)

    async def _reconcile(self, intent: Intent, task: asyncio.Future, line: str | None) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait({task}, timeout=RECONCILE_S)
        if not task.done() or task.cancelled() or task.exception() is not None:
            receipt = {"ok": None, "result": "uncertain", "error": "cut off by the user's takeover; not known"}
        else:
            receipt = self.verify(intent, task.result())
        self._ended(intent, receipt, line, receipt["result"], reconciled=True)

    def verify(self, intent: Intent, result: dict) -> dict:
        """The receipt, from the app's state: the tool's own run and the studio's report showing what was asked
        (`expected`). A report that cannot show it is uncertain, unless the tool's own run checked the report."""
        result = dict(result)
        ok = result.get("ok")
        facts = self.facts()
        if ok is False:
            result["result"] = "failed"
        elif ok is None or result.get("still_working"):
            result["result"] = "running"
        else:
            shows = expected(intent, facts)
            if shows is True or (shows is None and intent.tool in REPORT_CONFIRMED):
                result["result"] = "verified"
            elif shows is False:
                result["result"] = "failed"
                result["error"] = "the studio does not show it: " + (result.get("summary") or "not on screen")
            else:
                result["result"] = "uncertain"
                result["error"] = "the studio's report cannot show whether it happened"
        result["said"] = said(intent, result, facts, getattr(intent, "before", None))
        result.setdefault("_hands", {})["expected"] = result["result"]
        return result

    def _activity(self, intent: Intent, state: str, text: str | None = None) -> str | None:
        if self.hub is None:
            return None
        from .. import activity

        line_id = f"command:hands-{intent.id}"
        with contextlib.suppress(Exception):
            activity.activity(self.hub, "command", text or f"Assistant: {intent.caption.lower()}", state=state,
                              id=line_id, details={"intent": intent.kind, "tool": intent.tool,
                                                   **({"target": intent.target} if intent.target else {})})
        return line_id

    def _ended(self, intent: Intent, outcome: dict, line: str | None, result: str, *, reconciled: bool = False) -> dict:
        outcome.setdefault("result", result)
        state = {"verified": "done", "failed": "failed", "running": "running"}.get(outcome["result"], "done")
        words = {"verified": "done", "failed": "did not work", "running": "still going",
                 "uncertain": "not known whether it happened (you took over)"}.get(outcome["result"], "")
        if line is not None:
            self._activity(intent, state, f"Assistant: {intent.caption.lower()}: {words}")
        hands = outcome.setdefault("_hands", {})
        hands.update(intent=intent.kind, tool=intent.tool, target=intent.target, driveId=intent.id,
                     version=intent.version, revision=intent.revision, project=getattr(intent, "project", None),
                     result=outcome["result"], **({"reconciled": True} if reconciled else {}))
        self.log({"kind": "partner-hands", **hands, "error": outcome.get("error")})
        return outcome
