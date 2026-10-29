"""Records: what Hands checks an action against when the screen is not the right check (spec 4.2, "every acting tool,
not only screen actions"). Each is Hooke's own record of the thing the action was for, read after the action and
compared with what was asked, never the tool's words or the model's:

| Action | Verified means |
| --- | --- |
| start_researcher | the researcher's record exists with a live status: `running` ("Researcher 3 is working on it"); its finish comes later as an event |
| cancel_researcher | the record says cancelled; until then `running` ("cancelling"), never "cancelled" before it does |
| researcher_note, researcher_followup, revise_work | recorded on that researcher (its notes, its kept question, its new run) |
| run_capability, workflow | the run's record exists: running, then done with what it saved, or failed with its reason |
| set_default, set_research_cap | the setting reads back changed |
| use | the choice is on this request's later steps only; nothing has been done with it yet |
| new_project, switch_session | the Project open is a new one and the studio shows it; "already in" and "nothing opened" are not done |
| add_asset | the Project's stage holds that asset |
| reference_photo | the picture's window is in the studio's report when its background result lands |
| anything that saves (analysis, tables, a snapshot) | what it saved resolves in Hooke's stores, with its numbers |

`Hands` stays generic: it calls `before` at the moment of dispatch, `check` with the result, and `spends` for what an
action costs (asked once by voice).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
from typing import Any, Awaitable, Callable

LIVE = ("queued", "starting", "running")
FINISHED = ("done", "partial")
ENDED_WORDS = {"failed": "stopped: it failed", "cancelled": "was stopped", "interrupted": "was interrupted",
               "timeout": "ran out of time", "timed_out": "ran out of time"}
WORKFLOW_DONE = ("completed",)
WORKFLOW_RUNNING = ("ready", "running", "paused", "stopping", "awaiting-review", "awaiting-controls", "awaiting-research")
WORKFLOW_FAILED = ("failed", "rejected", "stopped")
LONG_EFFORTS = ("deep",)  # a researcher run long enough to ask first (spec 4.2: "a long researcher run")
PAID_DELIVERABLES = ("video",)  # a researcher's deliverable made by a paid generation model


def _said_failed(result: dict) -> str:
    why = str(result.get("error") or result.get("summary") or "").strip()
    nxt = str(result.get("next") or "").strip()
    return ("It did not work" + (f": {why}" if why else "") + "." + (f" Next: {nxt}." if nxt else ""))


class Records:
    """Hooke's records for one partner: the research manager and desk, the settings, the Project and the studio's report
    of it, the session index and the table stores."""

    def __init__(self, hub, *, services=None, shown: Callable[[], bool | None] | None = None,
                 landed: Callable[[], Awaitable[bool]] | None = None) -> None:
        self.hub, self.services = hub, services
        self.shown, self.landed = shown, landed

    # --- Hooke's stores ------------------------------------------------------------------------------------------
    def _research(self):
        manager = getattr(self.services, "research", None)
        if manager is None:
            manager = getattr(getattr(self.hub, "resources", None), "research", None)
        return manager if hasattr(manager, "get") else None

    def _index(self):
        from ..resources import resources_of

        return resources_of(self.hub)

    def _project(self) -> str | None:
        with contextlib.suppress(Exception):
            return self._index().session()
        return None

    def _settings(self) -> dict:
        """The settings as Activity reads them (activity.Activity.snapshot): the settings' wire and the objective."""
        settings, out = getattr(self.hub, "settings", None), {}
        with contextlib.suppress(Exception):
            out = dict(settings.to_wire())
        scale = getattr(self.hub, "scale", None)
        if scale is not None and hasattr(scale, "objective"):
            out["objective"] = scale.objective
        return out

    def _researcher(self, ref) -> Any:
        """The researcher a call names ("3", "researcher 3", its id), as the desk finds it; None when none is."""
        if ref in (None, ""):
            return None
        manager = self._research()
        if manager is None:
            return None
        found = manager.get(str(ref)) if isinstance(ref, str) else None
        if found is not None:
            return found
        desk = getattr(self.services, "desk", None)
        with contextlib.suppress(Exception):
            return desk.find(ref) if desk is not None else None
        return None

    def _named(self, text: str):
        """The researcher a tool's own receipt names ("stopped Researcher 3"), when the call did not say which."""
        manager = self._research()
        found = re.search(r"\bResearcher (\d+)\b", text or "")
        if manager is None or found is None:
            return None
        n = int(found.group(1))
        sessions = getattr(manager, "sessions", {}) or {}
        return next((s for s in sessions.values() if getattr(s, "n", None) == n
                     and getattr(s, "sitting", None) in (None, "", self._project())), None) or \
            next((s for s in sessions.values() if getattr(s, "n", None) == n), None)

    # --- before the effect ---------------------------------------------------------------------------------------
    def before(self, intent) -> dict:
        tool = intent.tool
        if intent.kind == "project":
            return {"project": self._project()}
        if intent.kind == "setting":
            return {"settings": self._settings()}
        if tool == "add_asset":
            return {"stage": getattr(getattr(self._index(), "day", None), "on_stage", None)}
        if intent.kind in ("cancel", "steer"):
            s = self._researcher(intent.args.get("researcher"))
            if s is None:
                return {}
            return {"rid": s.rid, "notes": len(s.notes or []), "pending": len(s.pending or []),
                    "history": len(s.history or []), "run": id(s.run) if s.run is not None else None,
                    "jobs": len(getattr(s, "presentation_jobs", None) or []), "status": s.status}
        return {}

    # --- the receipt ---------------------------------------------------------------------------------------------
    async def check(self, intent, result: dict, run, facts: dict) -> dict | None:
        """The receipt from Hooke's record: {"result", "error"?, "said"}; None when this action has no record here."""
        tool, ok = intent.tool, result.get("ok")
        if ok is False:
            # The tool's own refusal is its receipt, with its reason and the next step it named.
            out = {"result": "failed", "said": _said_failed(result)}
            if result.get("stopped"):
                out.update(error="stopped when you took over; nothing after the stop was applied",
                           said="It was stopped when you took over; nothing after the stop was applied.")
            return out
        before = intent.record or {}
        if tool == "start_researcher":
            return self._started(result, run)
        if tool == "cancel_researcher":
            return self._cancelled(result, before)
        if tool in ("researcher_note", "researcher_followup", "revise_work"):
            return self._steered(tool, result, before)
        if tool == "run_capability":
            return self._capability(result, run)
        if tool == "workflow":
            return self._workflow(result, run)
        if intent.kind == "setting":
            return self._setting(before)
        if tool == "use":
            return {"result": "verified" if ok else "running",
                    "said": "Chosen for this request only (not from now on): the steps after it in this turn use it; "
                            "nothing has been done with it yet."}
        if intent.kind == "project":
            return await self._project_opened(intent, result, before)
        if tool == "add_asset":
            return self._staged(intent, before)
        if tool == "reference_photo":
            return self._pictured(result, run, facts)
        return self._saved(result, run)

    def _started(self, result: dict, run) -> dict:
        rid = result.get("researcher") or _researcher_of(run)
        s = self._research().get(rid) if rid and self._research() is not None else None
        if s is None:
            if result.get("ok") is None:
                return {"result": "running", "said": "The researcher is being started; not working yet."}
            return {"result": "uncertain", "error": "Hooke has no record of the researcher it started",
                    "said": "Not sure a researcher started: Hooke has no record of it."}
        out = {"researcher": s.rid, "number": s.n}
        if s.status in LIVE:
            words = (f"{s.name} is queued: it starts after the ones already working." if s.status == "queued"
                     else f"{s.name} is working on it; its finding comes when it is done.")
            return {**out, "result": "running", "said": words}
        if s.status in FINISHED:
            return {**out, "result": "verified", "said": f"{s.name} has finished."}
        return {**out, "result": "failed", "error": f"{s.name} {ENDED_WORDS.get(s.status, s.status)}",
                "said": f"{s.name} {ENDED_WORDS.get(s.status, s.status)}."}

    def _cancelled(self, result: dict, before: dict) -> dict:
        manager = self._research()
        s = manager.get(before["rid"]) if before.get("rid") and manager is not None else None
        s = s or self._named(str(result.get("summary") or ""))
        if s is None:
            return {"result": "uncertain", "error": "Hooke has no record of which researcher it stopped",
                    "said": "Not sure it stopped: Hooke has no record of which researcher."}
        if s.status == "cancelled":
            return {"result": "verified", "researcher": s.rid, "said": f"{s.name} is stopped."}
        if s.status in LIVE:
            return {"result": "running", "researcher": s.rid,
                    "said": f"Cancelling {s.name}; it has not stopped yet, and you hear when it has."}
        return {"result": "uncertain", "researcher": s.rid, "error": f"{s.name} is {s.status}",
                "said": f"Not sure it was stopped: {s.name} is {s.status}."}

    def _steered(self, tool: str, result: dict, before: dict) -> dict:
        manager = self._research()
        s = manager.get(before["rid"]) if before.get("rid") and manager is not None else None
        s = s or self._named(str(result.get("summary") or ""))
        if s is None:
            return {"result": "uncertain", "error": "Hooke has no record of which researcher it reached",
                    "said": "Not sure it reached a researcher: Hooke has no record of it."}
        out = {"researcher": s.rid}
        running_now = s.run is not None and id(s.run) != before.get("run")
        if tool == "researcher_note":
            if len(s.notes or []) > before.get("notes", len(s.notes or [])):
                if s.status in LIVE and before.get("status") not in LIVE:
                    return {**out, "result": "running", "said": f"{s.name} carries on with it."}
                return {**out, "result": "verified", "said": f"{s.name} has the note; it reads it before its next step."}
            if s.status in LIVE and before.get("status") not in LIVE:  # finished before the answer: it carries on
                return {**out, "result": "running", "said": f"{s.name} had finished, so it carries on with it."}
        elif tool == "researcher_followup":
            if len(s.pending or []) > before.get("pending", len(s.pending or [])):
                return {**out, "result": "running",
                        "said": f"{s.name} has the question; it answers when its current work ends."}
            if running_now or s.status in LIVE and before.get("status") not in LIVE:
                return {**out, "result": "running", "said": f"{s.name} is answering; its answer comes when it is done."}
            if len(s.history or []) > before.get("history", len(s.history or [])):
                return {**out, "result": "verified", "said": f"{s.name} has answered."}
        else:  # revise_work
            jobs = len(getattr(s, "presentation_jobs", None) or [])
            if running_now or jobs > before.get("jobs", jobs) or s.status in LIVE and before.get("status") not in LIVE:
                return {**out, "result": "running", "said": f"{s.name} is making the new version; it comes when ready."}
        return {**out, "result": "uncertain", "error": f"{s.name}'s record does not show it",
                "said": f"Not sure {s.name} got it: its record does not show it."}

    def _resolves(self, ref: dict | None) -> dict | None:
        """A saved thing's record, from the store that keeps it: a table version, an analysis or a graph, or the
        session index's entry. None when it does not resolve."""
        if not isinstance(ref, dict) or not ref.get("id"):
            return None
        rid, day, version = str(ref["id"]), ref.get("day"), ref.get("version")
        operation = ref.get("operation")
        with contextlib.suppress(Exception):
            if operation in ("make_table", "edit_table") and getattr(self.hub, "datatables", None) is not None:
                record = self.hub.datatables.version(rid, version)
                return {"id": rid, "title": record.get("title") or rid, "version": record.get("version"),
                        "rows": len(record.get("rows") or [])}
            if operation in ("analyze", "make_graph") and getattr(self.hub, "table_derivatives", None) is not None:
                record = self.hub.table_derivatives.version(rid, version)
                return {"id": rid, "title": record.get("title") or rid, "version": record.get("version")}
        with contextlib.suppress(Exception):
            entry = self._index().get(rid, day if isinstance(day, str) else None)
            if entry is not None:
                return {"id": rid, "title": getattr(entry, "title", "") or rid,
                        "summary": getattr(entry, "summary", "") or ""}
        with contextlib.suppress(Exception):
            results = getattr(self._index(), "results", None)
            found = results.get(rid, day) if results is not None else None
            if found is not None:
                return {"id": rid, "title": str(found.get("title") or rid)}
        return None

    def _made(self, result: dict, run) -> list[dict]:
        refs: list[dict] = []
        resource = getattr(run, "resource", None) if run is not None else None
        for ref in (resource, result.get("resource")):
            if isinstance(ref, dict) and ref.get("id"):
                refs.append(ref)
        for output in result.get("outputs") or []:
            if not isinstance(output, dict):
                continue
            inner = output.get("resource")
            if isinstance(inner, dict) and inner.get("id"):
                refs.append(inner)
            elif output.get("id"):
                refs.append({"id": output["id"]})
        seen, out = set(), []
        for ref in refs:
            if ref["id"] not in seen:
                seen.add(ref["id"])
                out.append(ref)
        return out

    def _saved(self, result: dict, run) -> dict:
        """What a call saved resolves in Hooke's stores: verified, naming it; running while it saves or works in the
        background; otherwise uncertain (the tool said it worked, but nothing it made can be found)."""
        job = ((getattr(run, "values", None) or {}) if run is not None else {}).get("backgroundJob")
        if isinstance(job, dict) and job.get("id"):
            return {"result": "running", "said": f"Started as job {job['id']}; its result comes when it is done."}
        if result.get("ok") is None or result.get("still_working"):
            return {"result": "running", "said": "It is under way, not done yet."}
        found = [r for r in (self._resolves(ref) for ref in self._made(result, run)) if r is not None]
        if found:
            first = found[0]
            words = f"Saved: {first['title']} ({first['id']}"
            words += f", version {first['version']}" if first.get("version") else ""
            words += f", {first['rows']} rows" if first.get("rows") else ""
            return {"result": "verified", "saved": [r["id"] for r in found][:6], "said": words + ")."}
        return {"result": "uncertain", "error": "Hooke has no record of what it made",
                "said": "Not sure it was done: Hooke has no record of what it made."}

    def _capability(self, result: dict, run) -> dict:
        values = (getattr(run, "values", None) or {}) if run is not None else {}
        if isinstance(values.get("text"), str) and values.get("text") and result.get("ok"):
            return {"result": "verified", "said": "Done: its answer is in what it wrote."}
        return self._saved(result, run)

    def _workflow(self, result: dict, run) -> dict:
        values = (getattr(run, "values", None) or {}) if run is not None else {}
        if not values:
            with contextlib.suppress(ValueError, TypeError):
                values = json.loads(str(result.get("summary") or "").split("\n", 1)[0])
        status, where = _status_of(values)
        if status is None:
            return self._saved(result, run) if self._made(result, run) else (
                {"result": "verified", "said": "Done: the workflow took the command."} if result.get("ok")
                else {"result": "running", "said": "It is under way, not done yet."})
        if status in WORKFLOW_DONE:
            return {"result": "verified", "said": "Done: the workflow run completed."}
        if status in WORKFLOW_RUNNING:
            waits = {"awaiting-review": "waiting for your review", "awaiting-controls": "waiting for its controls",
                     "awaiting-research": "waiting for a researcher", "paused": "paused", "ready": "ready to run"}
            return {"result": "running", "said": f"The workflow run is {waits.get(status, 'running')}; not done yet."}
        if status in WORKFLOW_FAILED:
            reason = (where or {}).get("reason") or (where or {}).get("error") or status
            return {"result": "failed", "error": f"the workflow run {status}: {reason}"[:300],
                    "said": f"The workflow run {status}."}
        return {"result": "uncertain", "error": f"the workflow run is {status}",
                "said": f"Not sure how the workflow run ended: it is {status}."}

    def _setting(self, before: dict) -> dict:
        from .. import activity

        was, now = before.get("settings") or {}, self._settings()
        changed = [(k, was.get(k), v) for k, v in now.items() if k in was and was[k] != v]
        if not changed:
            return {"result": "failed", "error": "the setting did not change (it may already be that)",
                    "said": "Nothing changed: the setting already reads that way, or it was not taken."}
        key, _, value = changed[0]
        with contextlib.suppress(Exception):
            title, _menu = activity.setting_title(key)
            return {"result": "verified", "said": f"Done: {title.lower()} is {activity.setting_value(key, value)} now."}
        return {"result": "verified", "said": f"Done: {key} is {value} now."}

    async def _project_opened(self, intent, result: dict, before: dict) -> dict:
        was, now = before.get("project"), self._project()
        summary = str(result.get("summary") or "")
        if now is None or now == was:
            if summary.startswith("already in"):
                return {"result": "failed", "error": "already in that Project; nothing was opened",
                        "said": "Nothing opened: you are already in it."}
            if summary.startswith("nothing was opened"):
                return {"result": "failed", "error": "nothing was opened: more than one Project may be meant",
                        "said": "Nothing opened yet: more than one Project may be meant; ask which."}
            return {"result": "failed", "error": "the Project open did not change",
                    "said": "Nothing opened: the Project open did not change."}
        out = {"project": now}
        shown = self.shown() if self.shown is not None else None
        if shown is False and self.landed is not None:
            shown = await self.landed()
        if shown is False:
            return {**out, "result": "uncertain", "error": "it opened, but the studio has not shown it yet",
                    "said": "It opened, but the studio is not showing it yet."}
        label = re.search(r'"([^"]+)"', summary)
        name = label.group(1) if label else "the new Project"
        verb = "is open" if intent.tool == "switch_session" else "is started"
        return {**out, "result": "verified", "said": f"Done: {name} {verb}."}

    def _staged(self, intent, before: dict) -> dict:
        aid = intent.ids[0] if intent.ids else ""
        now = getattr(getattr(self._index(), "day", None), "on_stage", None)
        if aid and now == aid:
            return {"result": "verified", "said": "Done: it is on stage."}
        if now is not None and now != before.get("stage"):
            return {"result": "verified", "said": "Done: it is on stage."}
        return {"result": "uncertain", "error": "the Project's stage does not show it",
                "said": "Not sure it is on stage: the Project's record does not show it."}

    def _pictured(self, result: dict, run, facts: dict) -> dict:
        windows = re.findall(r"\bin window (w-[0-9a-f-]+)", str(result.get("summary") or ""))
        if result.get("ok") is None or result.get("still_working"):
            return {"result": "running", "said": "Still looking for the picture."}
        if windows:
            if not facts.get("reported"):
                return {"result": "uncertain", "error": "the studio sent no report to show it",
                        "said": "Not sure the picture is showing: the studio has not reported."}
            if any(w in facts["windows"] for w in windows):
                return {"result": "verified", "pointAt": [f"window:{w}" for w in windows][:2],
                        "said": "Done: the picture is open."}
            return {"result": "uncertain", "error": "the studio's report does not show its window",
                    "said": "The picture was found, but the studio is not showing it."}
        found = [r for r in (self._resolves(ref) for ref in self._made(result, run)) if r is not None]
        if found:
            return {"result": "verified", "said": "Done: the picture is kept with this Project (not shown)."}
        return {"result": "uncertain", "error": "Hooke has no record of the picture",
                "said": "Not sure a picture was found: Hooke has no record of it."}

    # --- what it costs -------------------------------------------------------------------------------------------
    async def spends(self, intent) -> str | None:
        """Words for what an action spends or deletes, asked once by voice with the cost said; None for a reversible
        action in Hooke. Starting a researcher at its usual effort asks nothing."""
        args = intent.args or {}
        verb = str(args.get("action") or args.get("do") or "").lower()
        if verb in ("delete", "trash"):
            return "This deletes it, and it cannot be undone"
        if intent.tool == "start_researcher":
            from research_worker.budget import EFFORTS

            effort = str(args.get("effort") or "quick")
            deliverable = str(args.get("deliverable") or "")
            if effort in LONG_EFFORTS and effort in EFFORTS:
                e = EFFORTS[effort]
                return (f"A {effort} researcher works up to about {e.seconds / 60:.0f} minutes and can spend up to "
                        f"${e.cap_usd:.2f}")
            if deliverable in PAID_DELIVERABLES:
                e = EFFORTS.get(effort)
                cap = f" (up to ${e.cap_usd:.2f})" if e is not None else ""
                return f"A researcher's {deliverable} is made by a paid model{cap}"
            return None
        if intent.kind in ("outline", "find"):
            from .. import options
            from ..capabilities import runs

            group = "cells" if intent.tool == "outline_cells" else "outline"
            chosen = getattr(getattr(self.hub, "settings", None), group, None)
            option = options.GROUPS[group].get(chosen) if group in options.GROUPS else None
            if option is not None and "cloud" in runs(option.uses):
                return f"{option.label} runs in the cloud and is paid per use"
            return None
        if intent.tool == "run_capability":
            kinds_of = getattr(self.services, "kinds", None)
            if not callable(kinds_of):
                return None
            from .. import kinds

            want = str(args.get("capability") or "")

            def read():
                with kinds.read_once():
                    return kinds.match(kinds_of(), want)
            kind = await asyncio.to_thread(read)
            if isinstance(kind, dict) and kind.get("runner") == "cloud":
                return f"{kind.get('name') or want} runs in the cloud and is paid per use"
        return None


def _status_of(values: Any) -> tuple[str | None, dict | None]:
    """A workflow command's run status, from its reply: the run's own record (observed_status) when it has one."""
    if not isinstance(values, dict):
        return None, None
    for key in ("run", "execution", "workflowRun"):
        inner = values.get(key)
        if isinstance(inner, dict) and isinstance(inner.get("status"), str):
            return inner["status"], inner
    if isinstance(values.get("status"), str):
        return values["status"], values
    return None, None


def _researcher_of(run) -> str | None:
    """The researcher a start_researcher run made, from its typed output (manager.attempt_ref)."""
    for output in getattr(run, "outputs", None) or []:
        with contextlib.suppress(Exception):
            ref = output.wire().get("resource")
            if isinstance(ref, dict) and ref.get("kind") == "research-run" and isinstance(ref.get("researcherId"), str):
                return ref["researcherId"]
    return None
