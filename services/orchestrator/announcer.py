"""The Announcer: who hears what, and where, when something finishes on its own time.

A researcher finished, a tool finished after its answer, a researcher suggested a correction,
the user decided on one. Each is said at the next pause on the delegation that asked (voice),
or shown as a line in the conversation it belongs to (chat), and noted for the brain; what the
user asked for opens beside the view where its viewer shows it, else it is in Data. Research lifecycle events are factual Dialogue notices,
with original run ownership separate from their filing. Research words remain data for
the original voice only. Requested notifications ask that model to compose its own update;
they never become application-authored assistant replies.

A researcher belongs to the session it was started in (ADR 0017) and keeps working when the user opens another: what
it does is filed in its own session, never shown in the one open, and the session open gets one line about it (the
studio's "from 'Blood smear'", `fromSession`). Nothing of it opens or is spoken there.
"""
from __future__ import annotations

import asyncio
import copy
import json
from typing import Awaitable, Callable

from .brain.claims import DOING
from .day.model import Asked
from .brain.narrator import Line
from .brain.registry import ToolContext, ToolRun
from .dialogue import Dialogue
from .labels import label_key
from .presenter import index_of
from .untrusted import block, inline
from research_worker.claims import summary_line as claims_line, unsupported as unsupported_claims

MADE = {"html": "lesson", "video": "video", "image": "picture"}


def checked_note(result: dict | None) -> str:
    """What the check of its sources found (research_worker/claims.py), for the line that announces
    a result: the user always hears when points were not found in their pages, never only what
    was. "" when nothing was checked or everything was found."""
    from research_worker.evidence import note as evidence_note

    missing = unsupported_claims(result)
    session = inline(evidence_note(result), 240)  # the session's records it cites (ADR 0015)
    session = f" {session}" if session else ""
    if not missing:
        return session
    first = inline(missing[0].get("point"), 90)
    return (f" Note: {claims_line(result)} (the app looked for them on the pages it cited), "
            f"for example: {first}" + ("" if first.endswith((".", "!", "?")) else ".") + session)


def shown_words(name: str, viewer: str | None) -> str:
    """What a researcher's work being opened beside the view is said as: shown only once the studio confirmed it
    rendered that exact work; sent, not yet seen, when it was sent (ADR 0006: done means the screen shows it); and when
    nothing was sent (no viewer showed it: the Hooke workspace, where it opens from Data in the reader; ticket 66, D6),
    only where it is. It replaced "Researcher 1: result viewer status" (the app's machinery, 2026-10-11)."""
    if viewer == "viewer rendered the exact retained work":
        return f"{name}: its work is shown beside the view"
    if viewer is None or viewer.startswith(("retained", "not opened")):
        return f"{name}: its work is in Data"
    return f"{name}: its work was sent to the viewer; the studio has not confirmed it is shown"



def _newest(voice, request_id) -> bool:
    """The voice's spoken requests say whether this request is still the user's latest (brain/speech.py newest)."""
    spoken = getattr(voice, "spoken", None)
    return spoken.newest(request_id) if spoken is not None and hasattr(spoken, "newest") else True

class Announcer:
    def __init__(self, dialogue: Dialogue, conversation, narrator, presenter, research, scene,
                 voice: Callable[[], object | None], note_to_voice: Callable[[str], Awaitable[None]],
                 log: Callable[[dict], None], taught=None) -> None:
        self.dialogue = dialogue
        self.conversation = conversation
        self.narrator = narrator
        self.presenter = presenter
        self.research = research
        self.scene = scene
        self.voice = voice  # the open GPT-Live session, if any
        self.note_to_voice = note_to_voice
        self.log = log
        self.taught = taught  # taught.TaughtLabels: an accepted name is kept as an example

    def _voice_open(self) -> bool:
        voice = self.voice()
        return voice is not None and not voice.closed

    # --- a tool that finished after its answer ---------------------------------------------
    async def late_tool(self, ctx: ToolContext, run: ToolRun) -> None:
        """File the original activity first; only an actual model answer is speech.

        A raw receipt is bounded context for the existing voice session. Its activity
        survives a busy or closed voice independently of the narration queue.
        """
        from .brain.claims import answer_facts

        # A later model round can create an operation absent from the initial
        # chat. Retain its actual occurrence before its model answer arrives;
        # an already retained occurrence is updated by the same identity.
        self.dialogue.show({"type": "chat-action", "turnId": ctx.turn_id,
                            "standalone": True, **run.wire()}, asked=ctx.asked)
        voice = ctx.voice_owner if ctx.channel == "voice" else None
        if voice is not None and not voice.closed:
            occurrence = run.occurrence
            self.narrator.offer(Line(
                run.answer if run.answer else answer_facts([run]), "requested",
                delegation_id=ctx.delegation_id, asked=ctx.asked,
                delivery="when_idle" if run.ok else "interrupt",
                provenance="model" if run.answer else "data", turn_id=ctx.turn_id,
                request_id=occurrence.request_id if occurrence is not None else None,
                action_id=occurrence.action_id if occurrence is not None else None,
                voice_owner=voice,
                # The outcome the user asked for by voice is said, in the voice's own words from its facts, even
                # with no model answer (live 2026-10-11 12:21: a failed photo was silent context, and the voice
                # improvised "I found the photo, it's on your screen").
                notify=True,
                # Said once: an outcome the voice was asked to say already, by any path (the hand-off's findings, a
                # second hand-off), raw or a model's own late answer, is told, not said again (narrator.Said; live
                # 2026-10-28: one outline said three times).
                outcomes=frozenset({f"action:{occurrence.action_id}"}) if occurrence is not None else frozenset(),
                # ...and unless the user has asked something newer since: then it is context, never said over them.
                still=lambda: _newest(voice, occurrence.request_id if occurrence is not None else None)))

    # --- a step still running that now waits on something ------------------------------------------
    def progressed(self, run: ToolRun, ctx: ToolContext) -> None:
        """A late step's progress changed (ToolRun.report_progress: a model loading, the studio out of view): its row
        in the chat says so, typed or spoken, and the voice is told it silently. One request, one acknowledgement,
        its card, one outcome."""
        if not run.late or run.ok is not None:
            return  # before its answer, the answer carries it; after its end, its outcome does
        self.dialogue.show({"type": "chat-action", "turnId": ctx.turn_id, "standalone": True, **run.wire()},
                           asked=ctx.asked)
        voice = ctx.voice_owner if ctx.channel == "voice" else None
        if not run.progress or voice is None or voice.closed:
            return
        occurrence = run.occurrence
        request_id = occurrence.request_id if occurrence is not None else None
        # The wait is shown on its row and told to the voice, never said (the user's 2026-10-11 decision: one
        # acknowledgement, the card, then the outcome); the voice can answer "what's taking so long?" from it.
        self.narrator.offer(Line(
            f"{run.tool} is still being done ({DOING}): {run.progress}.", "requested",
            delegation_id=ctx.delegation_id, asked=ctx.asked, delivery="silent", provenance="data",
            turn_id=ctx.turn_id, request_id=request_id,
            action_id=occurrence.action_id if occurrence is not None else None, voice_owner=voice))

    # --- corrections -----------------------------------------------------------------------
    def correction(self, session, correction: dict) -> None:
        """Filed as a note (the voice is given it as data: narrator.py); nothing changes on screen until the user accepts.
        A researcher of another session suggests it there: filed in its own session, never said in the one open."""
        was = f"the {inline(correction['label'], 40)}" if correction.get("label") else "this outline"
        text = (f"{session.name} suggests {was} may be a {inline(correction['to_label'], 60)}: "
                f"{inline(correction.get('reason'), 160)}. Select it to accept or dismiss.")
        if self._elsewhere(session):
            self.conversation.add("note", "system", text, asked=self._filing(session), correction=correction["id"])
            return
        self.conversation.add("note", "system", text, correction=correction["id"])
        self.narrator.offer(Line(text, "correction", rid=session.rid if session.rid != "brain" else None))

    def decided(self, cid: str, accept: bool) -> dict:
        found = self.research.decide(cid, accept)
        if found is None:
            return {"ok": False, "error": "that suggestion is no longer open"}
        session, correction = found
        if not accept:
            self.conversation.add("note", "system", f"The user dismissed {session.name}'s suggestion "
                                                    f"({inline(correction['to_label'], 60)}).")
            return {"ok": True}
        # Relabel the object it was about, or every outline with the same name.
        key = label_key(correction.get("label") or "")
        targets = [self.scene.objects[correction["object_id"]]] if correction.get("object_id") in self.scene.objects else [
            o for o in self.scene.objects.values() if o.kind in ("highlight", "part") and key and label_key(o.label) == key]
        for obj in targets:
            confidence = correction.get("confidence")
            # The new name is a researcher's words (from the web): one line, nothing to break out of.
            # The user accepted it: confirmed, the one status the model never sets itself.
            from .known import renamed

            self.scene.update(obj.id, label=inline(correction["to_label"], 60), revised_by=inline(session.name, 40), status="confirmed", maybe=[],
                              naming=renamed(obj, inline(session.name, 40), inline(correction["to_label"], 60)),
                              confidence=confidence if isinstance(confidence, (int, float)) else obj.confidence)
        self.conversation.add("note", "system", f"The user accepted {session.name}'s correction: "
                                                f"{inline(correction.get('label'), 60)} is now labelled "
                                                f"{inline(correction['to_label'], 60)} ({len(targets)} outline"
                                                f"{'s' if len(targets) != 1 else ''}).")
        self.log({"kind": "correction", "id": cid, "from": correction.get("label"), "to": correction["to_label"],
                  "by": session.name, "objects": [o.id for o in targets]})
        if self.taught is not None and targets:
            # The user agreed to this name for this thing: one example of it (taught.py).
            asyncio.ensure_future(self._teach(inline(correction["to_label"], 60), [o.id for o in targets]))
        return {"ok": True, "relabelled": [o.id for o in targets]}

    async def _teach(self, label: str, ids: list[str]) -> None:
        for oid in ids:
            try:
                await self.taught.teach(label, oid, source="correction")
            except ValueError as exc:
                self.log({"kind": "taught", "label": label, "objectId": oid, "source": "correction", "error": str(exc)})

    # --- a deep researcher's plan, before it starts (W6-4d) ------------------------------------
    def research_plan(self, session) -> None:
        """Keep the actual plan as activity and original-voice context; never a reply."""
        if not (session.plan or {}).get("searches"):
            return
        facts = self._research_facts(session)
        self._research_notice(session, "plan", f"{session.name}: research plan available", facts)
        self._research_voice(session, "plan", facts)
        self.log({"kind": "research-plan", "rid": session.rid, **copy.deepcopy(session.plan)})

    def research_request(self, session, request: dict) -> None:
        """Keep the numbered evidence question as data, including its actual run and origin."""
        if not request.get("what"):
            return
        # Put the current question first so a bounded voice context retains its
        # q-ID/reason/run even when this researcher's history is long.
        facts = {"request": copy.deepcopy(request), **self._research_facts(session)}
        summary = (f"{session.name} asks whether to keep going ({request['id']}): {inline(request.get('why'), 120)}"
                   if request.get("keepGoing") else f"{session.name} asks for {inline(request.get('what'), 120)}")
        self._research_notice(session, "request", summary, facts)
        self.log({"kind": "research-request", "rid": session.rid, "request": request.get("id"), "what": request.get("what"),
                  "why": request.get("why"), "seconds": request.get("seconds")})
        if (session.origin or {}).get("asked", True):
            self._research_voice(session, "request", facts)

    # --- footage held for a researcher stopped early ---------------------------------------
    def held_stopped(self, clip, text: str | None = None) -> None:
        """Keep a hold's actual stop/release as activity at its original consent.

        Its frames and memory belong to Held; this reads their state at delivery,
        including a release after stopping while tell was queued. Voice gets only
        data on the consent's original live owner, independently of saved activity.
        """
        import json

        text = text or clip.stop_words()
        said = clip.said or {}
        asked = said.get("asked")
        released = clip.status == "released"
        frames, span = clip.kept if released else (len(clip.frames), clip.span())
        facts = {"researchId": clip.rid, "request": clip.request, "seconds": clip.seconds,
                 "frames": frames, "span": span, "status": clip.status, "why": clip.why,
                 "released": released, "cutShort": clip.cut_short, "stoppedWhy": clip.stopped_why,
                 "byUser": clip.by_user}
        self.conversation.add("note", "system", text, asked=asked, rid=clip.rid)
        self.log({"kind": "held-stopped", "clip": clip.id, "rid": clip.rid, "status": clip.status, "why": clip.why,
                  "cutShort": clip.cut_short})
        row = self.dialogue.notice(producer={"kind": "held", "id": clip.id},
                                   event="released" if released else "stopped", summary=text, facts=facts,
                                   asked=asked, rid=clip.rid,
                                   origin={"day": said.get("day"), "thread": said.get("thread"),
                                           "delegationId": said.get("delegation_id"), "turnId": said.get("turn_id"),
                                           "requestId": said.get("request_id"), "actionId": said.get("action_id")},
                                   tone="error")
        voice = said.get("voice_owner")
        if voice is not None and not voice.closed and self.voice() is voice:
            self.narrator.offer(Line(json.dumps(row, ensure_ascii=False), "requested", rid=clip.rid,
                                     delegation_id=said.get("delegation_id"), asked=asked, delivery="interrupt",
                                     provenance="data", turn_id=said.get("turn_id"),
                                     request_id=said.get("request_id"), action_id=said.get("action_id"),
                                     voice_owner=voice, notify=True))

    # --- a researcher finished -------------------------------------------------------------
    async def research_finished(self, session) -> None:
        """File the manager's detached completion, with original identity across viewer I/O."""
        if session.purpose != "research":
            return
        # The manager freezes before scheduling listeners; make the data used after
        # this await explicit. No result, question, run or owner is read from a later
        # mutable projection of the same researcher.
        facts = self._research_facts(session)
        origin = session.origin or {}
        asked_for = origin.get("asked", True)
        pending = next((job for job in reversed(session.presentation_jobs) if job['state'] in ('queued', 'preparing')), None)
        if pending:
            facts['presentation'] = {'job':pending['id'], 'state':pending['state'], 'contentKind':pending.get('contentKind'),
                                     'contentVerified':False}
        summary = f"{session.name}: findings ready; presentation preparing" if pending else f"{session.name}: {facts['status']}"
        self._research_notice(session, "completed", summary, facts,
                              tone="info" if facts["status"] == "done" else "error")
        if self._elsewhere(session):
            return  # filed in its own session; the one open was told one line (_research_notice)
        if asked_for and self.presenter is not None:
            if session.revised is not None and session.revised.get("id"):
                viewer = await self._open_beside(session, output=session.revised["id"])
            elif (session.answered is None and session.revised is None and session.status in ("done", "partial")
                  and (session.outputs or session.result)):
                viewer = await self._open_beside(session)
            else:
                viewer = None
            if viewer is not None:
                facts["presentation"] = {**facts.get('presentation', {}), "researchId": session.rid, "report": viewer,
                                         "contentVerified": viewer == 'viewer rendered the exact retained work'}
                self._research_notice(session, "presentation", shown_words(session.name, viewer), facts)
        significance = (facts.get("result") or {}).get("significance")
        why = ("requested" if asked_for else "correction" if facts["corrections"]
               else "answer" if significance == "high" else None)
        if why is not None:
            self._research_voice(session, "completed", facts, why=why)

    def presentation_finished(self, session, job) -> None:
        """Notify the original conversation, naming what was restyled; a late artifact never opens a closed window.
        A restyle the user asked for (its delivery kept) is shown in their viewer when it shows the version it
        replaces (_open_beside), and said done only once the viewer shows it."""
        kind = job.get('contentKind') or 'report'
        output = session.output(job.get('output')) if job.get('output') else None
        facts = self._research_facts(session)
        facts['presentation'] = {'job':job['id'], 'state':job['state'], 'contentKind':kind, 'output':job.get('output'),
                                 'version':(output or {}).get('version'), 'replaces':job.get('expectedBase'),
                                 'change':job.get('change'), 'findingRevision':job['findingRevision'],
                                 'contentVerified':False, 'error':job.get('error')}
        summary = (f"{session.name}: {kind} restyled, v{(output or {}).get('version')}" if job['state'] == 'ready' and output
                   else f"{session.name}: could not restyle its {kind}: {job.get('error') or job['state']}")
        self._research_notice(session, 'presentation-finished', summary, facts,
                              tone='info' if job['state'] == 'ready' else 'error')
        asked = (session.origin or {}).get('asked', True)
        if job['state'] == 'ready' and output and session._delivery is not None and self.presenter is not None:
            async def shown() -> None:
                viewer = await self._open_beside(session, output=output['id'])
                facts['presentation'] = {**facts['presentation'], 'report': viewer,
                                         'contentVerified': viewer == 'viewer rendered the exact retained work'}
                self._research_notice(session, 'presentation', shown_words(session.name, viewer), facts)
                if asked:
                    self._research_voice(session, 'presentation-finished', facts, why='requested')

            try:
                asyncio.get_running_loop().create_task(shown())
                return
            except RuntimeError:
                pass  # no loop (a test calling it directly): said without the viewer's report
        if asked:
            self._research_voice(session, 'presentation-finished', facts, why='requested')

    def _research_facts(self, session) -> dict:
        """Detached JSON facts; neither worker text nor partial fallback is a model reply."""
        completion = session._completion
        attempt = session._attempt
        run = (completion or {}).get("run") or (attempt.facts() if attempt is not None else session.result_run)
        return copy.deepcopy({
            "researchId": session.rid, "name": session.name, "day": session.day,
            "thread": (session.origin or {}).get("thread"), "brief": session.brief,
            "status": completion["status"] if completion is not None else session.status,
            "cause": completion.get("cause") if completion is not None else None,
            "run": run, "plan": session.plan, "requests": session.requests,
            "result": completion.get("result") if completion is not None else session.result,
            "outputs": session.outputs, "last": session.last, "history": session.history,
            "findings": session.findings, "corrections": session.corrections,
            "spent": session.spent, "spentEstimated": session.spent_estimated,
        })

    @staticmethod
    def _research_context(event: str, facts: dict) -> str:
        """Whole facts for model input, separate from the full public notice.

        The existing voice wrapper cuts at 24,576 characters. Stay below it,
        including JSON punctuation and omission accounting, before either model
        path sees this data. Current facts precede optional history; an entry
        that does not fit is omitted whole, never sliced into a different fact.
        """
        limit = 12000
        result = facts.get("result")
        omitted = {key: len(value) if isinstance(value, list) else 1
                   for key, value in facts.items() if key != "result"}
        if isinstance(result, dict):
            omitted.update({f"result.{key}": len(value) if isinstance(value, list) else 1
                            for key, value in result.items()})
        else:
            omitted["result"] = 1
        # Reserve empty containers too: introducing an empty list after filling
        # the budget must not push an otherwise bounded projection over it.
        order = ("researchId", "day", "thread", "status", "cause", "run", "request", "result",
                 "outputs", "presentation", "plan", "name", "brief", "spent", "spentEstimated", "last",
                 "requests", "history", "findings", "corrections")
        retained = {key: [] if isinstance(facts[key], list) else None for key in order if key in facts}
        retained.update(omitted=omitted,
                        read_result={"tool": "read_result", "args": {"researcher": facts["researchId"]},
                                     "byVoice": "ask the app to read it (ask_app)"},
                        result={key: [] for key, value in result.items() if isinstance(value, list)}
                        if isinstance(result, dict) else None)
        payload = {"event": event, "facts": retained}

        def encoded() -> str:
            return json.dumps(payload, ensure_ascii=False)

        def keep(target: dict, key: str, value, path: str, *, newest: bool = False) -> None:
            if isinstance(value, list):
                # The omission counter is reserved before choosing entries, so
                # adding it afterwards cannot overflow a previously full body.
                entries = target.setdefault(key, [])
                for entry in reversed(value) if newest else value:
                    if newest:
                        entries.insert(0, entry)
                    else:
                        entries.append(entry)
                    omitted[path] -= 1
                    if len(encoded()) > limit:
                        entries.pop(0 if newest else -1)
                        omitted[path] += 1
                return
            target[key] = value
            omitted[path] = 0
            if len(encoded()) > limit:
                del target[key]
                omitted[path] = 1

        # Generated ownership and the current q-ID/run travel before worker
        # prose. Even an unusually large field is either whole or explicitly
        # omitted with the existing reader reference; no enlarged voice cap.
        for key in ("researchId", "day", "thread", "status", "cause", "run", "request"):
            if key in facts:
                keep(retained, key, facts[key], key)
        if isinstance(result, dict):
            # Preserve the current answer and its limitations/sources before
            # optional detail. Checks remain factual entries, with exact counts
            # if any qualifier cannot fit; omission never means it passed.
            order = ("summary", "one_liner", "limitations", "sources", "claims", "session_claims", "evidence")
            for key in dict.fromkeys((*order, *result)):
                if key in result:
                    keep(retained["result"], key, result[key], f"result.{key}")
        else:
            keep(retained, "result", result, "result")
        for key in ("outputs", "presentation", "plan", "name", "brief", "spent", "spentEstimated", "last"):
            if key in facts:
                keep(retained, key, facts[key], key)
        # Recent history is more useful at the next turn. Each accepted entry
        # still has its original contents, identity and chronological position.
        for key in ("requests", "history", "findings", "corrections"):
            if key in facts:
                keep(retained, key, facts[key], key, newest=True)
        return encoded()

    def _elsewhere(self, session) -> bool:
        """Whether a researcher belongs to a session other than the one open (ADR 0017)."""
        sitting = getattr(session, "sitting", "")
        return bool(sitting) and sitting != self.dialogue.day.sid

    def _filing(self, session) -> Asked | None:
        """Where a researcher's lifecycle is filed: the request that started it, when that was asked in its session;
        else, for a researcher of another session, that session (its conversation), never the one open."""
        delivery = session._delivery
        asked = delivery.asked if delivery is not None else None
        if not self._elsewhere(session) or getattr(asked, "session", None) == session.sitting:
            return asked
        thread = (session.origin or {}).get("thread") or ""
        return Asked(thread, 0, day=session.sitting, stale=True, session=session.sitting)

    def _research_notice(self, session, event: str, summary: str, facts: dict, *, tone: str = "info") -> None:
        delivery = session._delivery
        asked = delivery.asked if delivery is not None else None
        original = session.origin or {}
        # The resource's day is known in facts; without a captured request its
        # request-origin day remains unknown, rather than borrowing the filing day.
        origin = {"day": delivery.day if delivery is not None else original.get("day")}
        for key, field in (("thread", "thread"), ("turnId", "turn_id"), ("delegationId", "delegation_id"),
                           ("requestId", "request_id"), ("actionId", "action_id")):
            if original.get(field) is not None:
                origin[key] = original[field]
        elsewhere = self._elsewhere(session)
        filing = self._filing(session)
        self.dialogue.notice(producer={"kind": "research", "id": session.rid}, event=event,
                             summary=summary, facts=facts, asked=asked, rid=session.rid, origin=origin, tone=tone,
                             filing_asked=filing if filing is not asked else None)
        if elsewhere:
            if event != "presentation":  # whether its viewer showed it is its own session's matter
                self._one_line(session, event, summary, facts, tone)
                # Its useful facts go in its own conversation, read when its session is open again.
                self.conversation.add("note", "system", block(self._research_context(event, facts),
                                                              source="research lifecycle"), asked=filing, rid=session.rid)
            return
        # Keep useful facts in the original conversation while it is still loaded.
        # An earlier session's notice may be filed now, but is never a note attributed
        # to a new conversation just because its old thread id was reused.
        day = self.dialogue.day
        same_request = (asked is not None and day.current(getattr(asked, "routing_generation", asked.generation))
                        and asked.thread in day.threads)
        same_research = (asked is None and session.sitting == day.sid
                         and original.get("thread") in day.threads
                         and day.thread_for(rid=session.rid) == original["thread"])
        if event != "presentation" and (same_request or same_research):
            self.conversation.add("note", "system", block(self._research_context(event, facts),
                                                          source="research lifecycle"), asked=asked, rid=session.rid)

    def _one_line(self, session, event: str, summary: str, facts: dict, tone: str) -> None:
        """The one line the session open gets about a researcher of another session (ADR 0017): which researcher,
        what happened, and which session it is in (`fromSession`, said by the studio: "from 'Blood smear'"); its work
        stays filed in its own session. Filed in the open session's active conversation."""
        sid = session.sitting
        name = self.research.session_name(sid) if hasattr(self.research, "session_name") else sid
        result = facts.get("result") if isinstance(facts.get("result"), dict) else {}
        brief = {"researchId": facts.get("researchId"), "name": facts.get("name"), "day": facts.get("day"),
                 "status": facts.get("status"), "cause": facts.get("cause"),
                 **({"result": {k: result[k] for k in ("title", "one_liner") if result.get(k)}} if result else {}),
                 **({"request": facts["request"]} if facts.get("request") else {}),
                 **({"plan": facts["plan"]} if facts.get("plan") else {}),
                 **({"presentation": facts["presentation"]} if facts.get("presentation") else {})}
        self.dialogue.notice(producer={"kind": "research", "id": session.rid}, event=event, summary=summary,
                             facts=brief, origin={"day": sid, "researchId": session.rid}, tone=tone,
                             from_session={"id": sid, "name": name})

    def _research_voice(self, session, event: str, facts: dict, *, why: str = "requested") -> None:
        delivery = session._delivery
        voice = delivery.voice_owner if delivery is not None else None
        original = session.origin or {}
        if original.get("channel") != "voice" or self._elsewhere(session):
            return  # another session's researcher is never spoken in the one open (its one line is shown)
        if voice is None or voice.closed or self.voice() is not voice:
            # The Talk session that asked has ended: a later one hears it only when it is that one's verified successor
            # (the same browser, and the conversation the request was made in, now); otherwise nothing is spoken
            # anywhere, and the notice stays filed in the conversation (Activity, and the next recap) for that browser.
            voice = self._successor(delivery)
            if voice is None:
                return
        self.narrator.offer(Line(self._research_context(event, facts), why,
                                 rid=session.rid, delegation_id=original.get("delegation_id"),
                                 asked=delivery.asked, provenance="data", turn_id=original.get("turn_id"),
                                 request_id=original.get("request_id"), action_id=original.get("action_id"),
                                 voice_owner=voice, notify=why == "requested" and event in ("request", "completed")))

    def _successor(self, delivery):
        """The open Talk session that may hear a notice its asking session can't: the same browser (its client) and
        the request's conversation, still the conversation now. None when there is no such session."""
        current = self.voice()
        if (current is None or current.closed or delivery is None or delivery.client is None
                or getattr(current, "browser_client", None) is not delivery.client):
            return None
        asked = delivery.asked
        day = self.dialogue.day
        if (asked is None or not day.current(getattr(asked, "routing_generation", asked.generation))
                or asked.thread != day.active.id):
            return None
        return current

    async def _open_beside(self, session, *, output: str | None = None) -> str:
        """Request the original work's viewer and report only the renderer's actual status.

        An acknowledgement must identify the exact retained item and report its
        content ready, from the original accepted browser after this request.
        """
        from .screen import Screen, open_state
        from .presenter import items, first_shown

        # A delayed listener cannot open another run's result, or the old session's
        # view after it has been cleared. Retained work remains in its own session.
        delivery = session._delivery
        day = self.dialogue.day
        current = self.research.get(session.rid)
        if (session.sitting != day.sid or (delivery is not None and delivery.asked is not None
                                          and not day.current(getattr(delivery.asked, "routing_generation",
                                                                     delivery.asked.generation)))):
            return "not opened: the original session is no longer open"
        if current is None or current._attempt is not session._attempt:
            return "not opened: the researcher has a newer run"
        hub = getattr(self.presenter, "hub", None)
        screen = getattr(hub, "screen", None) if hub is not None else None
        if delivery is None or delivery.client is None or not isinstance(screen, Screen):
            return "retained in Researchers; original browser unavailable"
        # A reconnect is not authority to redirect old background work. The original
        # browser/connection must still satisfy its accepted request snapshot.
        try:
            from .brain.toolbox.present import _original_screen
            scope = _original_screen(screen, delivery)
        except Exception as exc:
            self.log({"kind": "research-not-shown", "rid": session.rid, "error": str(exc)})
            return "retained in Researchers; original browser ownership expired"
        view = self.presenter.for_client(delivery.client)
        if not view.open or view.rid != session.rid or view.saved_session is not None:
            return 'retained in Researchers; open its work when ready'
        selected = index_of(session, output) if output is not None else first_shown(session)
        listed = items(session)
        intended = listed[selected] if selected is not None and selected < len(listed) else None
        if intended is None:
            return 'retained work has no available item to display'
        expected = intended['work']
        current_items = items(current)
        index = next((i for i, item in enumerate(current_items) if item['work'] == expected), None)
        if index is None:
            return 'retained in Researchers; a newer item is selected'
        if view.index != index or (view.finding_revision and
                view.finding_revision.get('version') != expected['resource']['version'] and not view.output_id):
            return 'retained in Researchers; the selected version was preserved'
        change = screen.send("viewer", "entry", "open", {"open": True, "shows": session.rid}, "brain", **scope)
        def rendered(report):
            item = next((item for item in report.get('items', []) if item.get('id') == 'viewer'), {})
            return (open_state('viewer', True, session.rid)(report) and item.get('work') == expected
                    and item.get('workReady') is True and expected['resource']['version'] is not None)
        shown = await screen.confirm(change, rendered)
        if (session.sitting != day.sid or (delivery is not None and delivery.asked is not None
                                          and not day.current(getattr(delivery.asked, "routing_generation",
                                                                     delivery.asked.generation)))):
            return "another session was opened while waiting for the viewer"
        if shown:
            return "viewer rendered the exact retained work"
        self.log({"kind": "research-not-shown", "rid": session.rid})
        return "viewer was not confirmed within the deadline"

    def _outcome(self, session) -> str:
        """What it did, in a line: a new version, an answer to a follow-up, its result, or why it stopped."""
        what = inline(session.label or session.brief, 60)
        revised, answer = session.revised, session.answered
        if revised is not None:
            kind = revised.get("kind") or "work"
            return (f"{session.name} revised its {kind} (v{revised.get('version')}: {inline(revised.get('change'), 80)})"
                    if revised.get("id") else
                    f"{session.name} could not revise its {kind}: {str(revised.get('status')).replace('_', ' ')}")
        if answer is not None:
            reply = answer.get("result") or {}
            return (f"{session.name} answered ({what}): {inline(reply.get('one_liner') or reply.get('summary'), 200)}"
                    + checked_note(reply)
                    if reply else f"{session.name} ({what}) could not answer: {answer.get('status', '').replace('_', ' ')}")
        if session.status in ("done", "partial") and session.result:
            return (f"{session.name} finished ({what}): "
                    f"{inline(session.result.get('one_liner') or session.result.get('summary'), 200)}"
                    + checked_note(session.result))
        return f"{session.name} ({what}) stopped: {session.status.replace('_', ' ')}"
