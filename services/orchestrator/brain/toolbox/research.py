"""Tools on the researchers: start one, ask how it is doing, ask it more, change what it made,
steer it, stop it, show its pictures, suggest a correction.

Whether a researcher may start or run again, and at what effort, is decided in one place
(research/policy.py, inside ResearchManager.start and followup); these tools pass the user's
last half minute of speech for it. Which researcher a request means is the desk's
(research/desk.py).
"""
from __future__ import annotations

import re
import time

from research_worker.budget import EFFORTS, RUN_OPERATIONS
from research_worker.presentation import PROVIDERS as PRESENTATION_PROVIDERS



def _hands_words() -> str:
    """What a researcher's hands run (research/hands.py, budget.RUN_OPERATIONS), in a few words, for
    start_researcher's description (kind (c): a request that needs them gets a researcher that has them)."""
    words = {"workflow": "run saved scientific workflows", "segment": "segment every cell", "enhance": "make a clearer picture", "motion": "measure what moves",
             "measure_saved_objects": "measure saved labels"}
    said = [words.get(op, op) for op in RUN_OPERATIONS]
    return said[0] if len(said) == 1 else ", ".join(said[:-1]) + " or " + said[-1]


HANDS_WORDS = _hands_words()

from ...research.desk import made, named
from ...research.policy import CHOOSABLE, DELIVERABLES, NeedsTheirWords, ResearchRefused
from ...research.run import Delivery
from ...untrusted import block
from ..registry import CURRENT_RUN, Registry, Tool, ToolContext, ToolFailed, record_made, record_output, schema, typed_step_output
from .common import OBJECT, RESEARCHER, describe, resolve_object

RECENT_SPEECH_S = 30.0  # the explicit request may have come a moment earlier, in other words
SUMMARY_MAX = 6000  # characters of summarize_research's whole lines (newest first); the rest are counted
MAKING = {"research": "a research note", "report": "a report", "lesson": "a lesson", "video": "a short video", "annotated_image": "a labelled picture", "image": "a picture"}


def _effort_words() -> str:
    """Expose the worker's real planning bounds to the model choosing its effort."""
    return "; ".join(
        f"{name} (about {effort.seconds:g} s and {effort.max_rounds} model rounds before it is told to write up)"
        for name in CHOOSABLE for effort in [EFFORTS[name]]
    )

RESEARCH_REFERENCE = {"description": 'researcher number ("2") or id, or exact {step,output} from an earlier successful '
                                   'action in this plan (step is 1-based; output is 0-based)',
                     "oneOf": [RESEARCHER, {"type": "object", "properties": {
    "step": {"type": "integer", "minimum": 1}, "output": {"type": "integer", "minimum": 0}},
    "required": ["step", "output"], "additionalProperties": False}]}


def presentation_model(text) -> str | None:
    """The presentation provider `text` means (names.py: its id or its name, "Claude Opus 5.5"), None when none is
    named. A name that means none of them is refused with the choices (live 2026-10-11: only exact ids were taken)."""
    from ... import names
    from ...options import PRESENTATION

    if text is None or not str(text).strip():
        return None
    meaning = names.option("presentation", text)
    if meaning is None:
        choices = "; ".join(f"{p} = {PRESENTATION[p].label}" for p in PRESENTATION_PROVIDERS if p in PRESENTATION)
        raise ToolFailed(f"there is no presentation model {str(text)!r}; the choices (id = name): {choices}",
                         next="pick one of these and try again")
    return meaning.id


def bound_researcher(ctx, value, manager, desk):
    """Resolve a typed original run separately from legacy semantic desk references."""
    try:
        if isinstance(value, dict):
            ref = typed_step_output(ctx, value, kind="resource")["resource"]
            session, attempt = manager.resolve_attempt(ref)
            return session, attempt, ref
        target = getattr(ctx, 'research_selection', None)
        if target and value in (None, '', 'this'):
            value = target['rid']
        session = desk.find(value)
        return session, getattr(session, "_attempt", None), None
    except ResearchRefused as exc:
        raise ToolFailed(str(exc), next=getattr(exc, "next", None)) from exc  # next: passed on


def research_request_current(ctx, hub, day):
    from .resources import require_current
    from ...ticket import current
    require_current(ctx, day)
    request = current()
    if request is None or request.closed or request.expired():
        raise ToolFailed("the original request is no longer available to wait or present its result", next=None)  # no next: a retired original request cannot be resumed by this operation
    if ctx.admission is not None and request.admission is not ctx.admission:
        raise ToolFailed("the original researcher request admission changed", next=None)  # no next: another admission cannot replace the original request owner
    if ctx.request_snapshot is not None:
        from ...workspace_observation import ObservationError
        try:
            client, _profile = hub.screen._origin(ctx.request_snapshot)
            if client is not ctx.client or client not in hub.clients:
                raise ToolFailed("the original workspace connection changed", next=None)  # no next: the original accepted connection cannot be replaced while waiting or presenting
        except ObservationError as exc:
            raise ToolFailed(str(exc), next=None) from exc  # no next: the original workspace observation no longer validates for this request




def recent_request(services, ctx: ToolContext) -> str:
    """What the user asked in the last half minute, joined: a spoken request arrives in
    pieces, and the piece that starts the researcher may not be the one that said "lesson".
    By voice, once the transcript is heard at all, it is what the user was heard saying and what they
    typed, in the last half minute: never a hand-off's own text or the spoken turn filed from it. Today
    a hand-off's question is the user's transcript since the previous hand-off (voice/openai_live.py),
    which can hold words from minutes back; and a transport that sent the voice model's own account
    of the request would still be checked against the user's words."""
    conversation = getattr(services.agent, "conversation", None)
    if conversation is None:
        return ctx.question or ""
    now = time.time()
    heard = getattr(conversation, "heard", None)
    if ctx.channel == "voice" and heard:
        typed = [(t.at, t.text) for t in conversation.turns[-8:]
                 if t.role == "user" and t.channel != "voice" and now - t.at < RECENT_SPEECH_S]
        spoken = [(at, text) for at, text in heard if now - at < RECENT_SPEECH_S]
        if not spoken:  # nothing in the half minute: the newest words heard are still the request's (never none)
            spoken = [max(heard, key=lambda pair: pair[0])]
        return " ".join(text for _, text in sorted([*typed, *spoken], key=lambda pair: pair[0]))
    said = [t.text for t in conversation.turns[-8:] if t.role == "user" and now - t.at < RECENT_SPEECH_S]
    return " ".join([*said, ctx.question or ""])


def add_research_tools(registry: Registry, services) -> None:
    hub, manager, desk = services.hub, services.research, services.desk
    if manager is None or desk is None:
        return

    def find(ref, ctx):
        if ctx.research_selection and ref in (None, '', 'this'):
            ref = ctx.research_selection['rid']
        scoped = desk
        if ctx.client is not None and services.presenter is not None:
            from ...research.desk import Desk
            view = services.presenter.for_client(ctx.client)
            scoped = Desk(manager, shown=lambda: view.rid if view.open else None)
        try:
            return scoped.find(ref)
        except ResearchRefused as exc:
            raise ToolFailed(str(exc), next=getattr(exc, "next", None)) from exc  # next: passed on

    def origin(ctx: ToolContext, **more) -> dict:
        retained = {"channel": ctx.channel, "delegation_id": ctx.delegation_id, "turn_id": ctx.turn_id, **more}
        run = CURRENT_RUN.get()
        occurrence = run.occurrence if run is not None else None
        if occurrence is not None:
            retained.update(request_id=occurrence.request_id, action_id=occurrence.action_id)
        return retained

    def delivery(ctx: ToolContext) -> Delivery:
        # Resolve only the actual original request, before any manager I/O. A
        # missing/closed voice is not an invitation to use the current session.
        asked = ctx.asked
        original_day = getattr(asked, "day", None)
        return Delivery(asked=asked, day=original_day, client=ctx.client,
                        request_snapshot=ctx.request_snapshot,
                        request_id=ctx.admission.request_id if ctx.admission is not None else ctx.request_id,
                        voice_owner=ctx.voice_owner if ctx.channel == "voice" else None)

    async def start_researcher(ctx: ToolContext, args: dict) -> str:
        authorized_words = recent_request(services, ctx)
        if args.get("confirms"):
            # The user's yes to a proposal the app made (below): exactly that proposal starts, in its conversation.
            from ...research.policy import quoted

            day = services.day
            thread = day.thread_for(ctx.asked) if day is not None else None
            if not quoted(str(args.get("asked_with") or ""), authorized_words, least=1):
                raise ToolFailed("a proposal starts on the user's own yes", next="quote their reply in asked_with")
            try:
                proposal = manager.take_proposal(str(args["confirms"]), thread)
            except ResearchRefused as exc:
                raise ToolFailed(str(exc), next=getattr(exc, "next", None)) from exc  # next: passed on
            args = {**args, "brief": proposal["brief"], "effort": proposal["effort"], "deliverable": proposal["deliverable"],
                    **({"object": proposal["object_id"]} if proposal.get("object_id") else {})}
        confirmed = bool(args.get("confirms"))
        deliverable = args.get("deliverable") or "research"
        dependencies = []
        for value in args.get('build_on') or []:
            # A finished researcher's kept work, by how the user named it ("researcher 2", "the quiz").
            source = find(value, ctx)
            dependencies.append({'kind': 'research-work', 'researcherId': source.rid, 'day': source.day,
                                 'name': source.name, 'version': ((source.result or {}).get('findingRevision') or {}).get('version')})
        for value in args.get('inputs') or []:
            research_request_current(ctx, hub, services.day)
            source, attempt, ref = bound_researcher(ctx, value, manager, desk)
            if ref is None or attempt is None:
                raise ToolFailed('a handoff input needs an exact earlier step output', next=None)  # no next: a semantic current researcher cannot identify the requested original run
            try:
                if ref['kind'] == 'research-run':
                    outcome = await manager.wait_attempt(source, attempt, lambda: research_request_current(ctx, hub, services.day))
                    ref = manager.result_ref(source, attempt, outcome)
                manager.resolve_attempt(ref)
            except ResearchRefused as exc:
                raise ToolFailed(str(exc), next=getattr(exc, 'next', None)) from exc  # next: passed on from the original dependency refusal
            dependencies.append(ref)
        if dependencies:
            research_request_current(ctx, hub, services.day)
        obj = None
        unresolved = ""
        if args.get("object"):
            try:
                obj = resolve_object(hub, args.get("object"), ctx)
            except ToolFailed:
                obj = None  # e.g. a thing only seen, not outlined: research it without an outline, and say so
                unresolved = f"; {args.get('object')} is not an outline on the view, so it works from the whole view"
        if obj is None and not args.get("object"):
            # What this request outlined, else what its own step selected, else what was selected when the user
            # asked (C2: not one clicked while it is answered); nothing selected: research without an outline.
            from ...board import pointed_last

            # What the user pointed at last when they asked: the newer of the selection and a click (C2, as "that").
            selected = pointed_last(hub.scene, getattr(ctx, "when_asked", None), time.time())
            ids = [i for i in [*ctx.objects, getattr(ctx, "pointed", None), selected] if i in hub.scene.objects]
            obj = hub.scene.objects[ids[0]] if ids else None
        if deliverable == "annotated_image" and obj is None:
            raise ToolFailed("an annotated picture needs an outlined object", next="click the thing first, then ask")
        day = services.day
        # It belongs to the conversation the request was made in (even if the user has switched
        # since); its results are written there.
        thread = day.thread_for(ctx.asked) if day is not None else None
        try:
            session = await manager.start(str(args.get("brief") or ctx.question), effort=str(args.get("effort") or "quick"),
                                          deliverable=deliverable, object_id=obj.id if obj else None,
                                          origin=origin(ctx, thread=thread), words=authorized_words,
                                          asked_with=str(args.get("asked_with") or ""), delivery=delivery(ctx), dependencies=dependencies,
                                          confirmed=confirmed)
        except NeedsTheirWords as exc:
            # Not refused outright: kept as a proposal the user can say yes to (a "yes" then confirms exactly this).
            pid = manager.propose_start(brief=str(args.get("brief") or ctx.question), effort=args.get("effort"),
                                  deliverable=deliverable, thread=thread, object_id=obj.id if obj else None, why=str(exc))
            raise ToolFailed(str(exc), next=f"ask the user whether to make it; on their yes call start_researcher with "
                                            f"confirms {pid!r} and asked_with quoting their reply") from exc
        except ResearchRefused as exc:
            raise ToolFailed(str(exc), next=getattr(exc, "next", None)) from exc  # next: passed on
        if day is not None:
            day.link_research(session.rid, thread, session=session.sitting)  # in the session it was asked in
        if CURRENT_RUN.get() is not None:
            record_made(ctx, session.rid)
            record_output(ctx, {"kind": "resource", "resource": manager.attempt_ref(session)})
        seconds = EFFORTS[session.effort].seconds
        when = f"in about {seconds:.0f} s" if seconds < 120 else f"within {seconds / 60:.0f} min"
        queued = ", after the ones already working" if session.status == "queued" else ""
        making = MAKING.get(session.deliverable or "", "a research note")
        # What happens, said as it is (ADR 0006): it waits in Researchers, and its work is in Data once made (it opens in
        # the reader from there, or when asked; ticket 66, D6), beside the view only where a viewer shows it.
        return (f"{session.name} is making {making} at {session.effort} effort{queued} ({when}){unresolved}; when ready "
                f"it is in Researchers and Data")

    registry.add(Tool(
        "start_researcher",
        "Start a researcher for sustained investigation, comparison across sequences, independent confirmation, "
        "or producing a requested report, illustration, video or lesson while conversation continues. "
        "For retrieving a photo or existing work use reference_photo or show_result directly; a failed lookup "
        "does not itself call for a report. Use your own tools for ordinary live questions and actions. When the user "
        "asks for a researcher, always start a new one. effort: "
        + _effort_words() + ". No clock cuts its work off (it is told to write up, and asks the user before going past an "
        "hour), but choose the effort that fits the work: the app's operations take about 5-7 s for one motion "
        "measurement, about 10 s to segment a view and a model round 10-30 s, plus the write-up of everything it read. "
        "Quick fits a lookup or one or two measurements; several measurements with a comparison or average need "
        "standard. Deep requires an explicit request for thorough/deep research. "
        "For an explicitly requested handoff, inputs names exact earlier step outputs: wait for those original runs, then use only their completed inputs. "
        "deliverable: research (default), annotated_image "
        "(labels on the object's photo), image (an AI illustration, never a real photo: for a real photo use "
        "reference_photo), report, video or lesson (only when the user explicitly asks for one). "
        "Honor the user's effort limit: never increase it to fit a format. Video needs standard or deep, a lesson quick or more; "
        "image/annotated_image need quick or above. For a quick explanation or teaching, use research: it can "
        "explain in writing without a full lesson. If the requested format cannot fit, explain that before starting. From quick effort "
        "up it can have the app " + HANDS_WORDS + " for its own reading "
        "(an instant one cannot); whatever else the app does, run it yourself with your own tool, the first time.",
        schema({"brief": {"type": "string", "description": "the user's request in full, with every constraint they gave "
                                                           "(what to find, what to keep, how long, for whom); never shortened"},
                "effort": {"type": "string", "enum": CHOOSABLE},
                "deliverable": {"type": "string", "enum": list(DELIVERABLES)},
                "object": OBJECT,
                "inputs": {"type": "array", "maxItems": 6, "items": RESEARCH_REFERENCE['oneOf'][1],
                           "description": "Exact earlier step outputs when the user asks to use those researchers’ completed work"},
                "build_on": {"type": "array", "maxItems": 6, "items": RESEARCHER,
                             "description": "finished researchers whose kept work this one builds on (\"turn Researcher 2's "
                                            "notes into a lesson\"), as the user named them"},
                "asked_with": {"type": "string", "description": "the user's own words asking for this research, quoted as they "
                              "said them, whenever the user asked for it (any kind: it marks that they asked, so its result "
                              "is told to them); left out only when you start it on your own. Needed for a video, lesson or "
                              "deep research"},
                "confirms": {"type": "string", "description": "the proposal id a refusal gave (p1), when the user has just "
                                                              "said yes to it; asked_with then quotes their yes"}}, ["brief"]),
        start_researcher, inline_ms=1500, family="researchers", outline_refs=("object",)))

    async def schedule_acquisition(ctx: ToolContext, args: dict) -> str:
        from ...research.policy import quoted
        # The user's own words (by voice: what they were heard saying, never the hand-off's paraphrase), quoted by the
        # model: no word list decides what counts as asking for clips ("film it", "grab footage every minute").
        if not quoted(str(args.get("asked_with") or ""), recent_request(services, ctx)):
            raise ToolFailed("scheduled camera acquisition needs the user's explicit request",
                             next="quote the user's request for the clips in asked_with")
        session, _attempt, _ref = bound_researcher(ctx, args.get("researcher"), manager, desk)
        count, every = args.get("count"), args.get("every_seconds")
        if type(count) is not int or not 2 <= count <= 481 or type(every) not in (int, float):
            raise ToolFailed("give a clip count from 2 to 481 and a numeric start interval", next=None)  # no next: invalid plan arguments
        try:
            mission = manager.start_mission(session.rid, "capture", every, (count - 1) * every,
                acquisition={"seconds": args.get("clip_seconds")},
                origin=origin(ctx, thread=services.day.thread_for(ctx.asked) if services.day else None))
        except ResearchRefused as exc:
            raise ToolFailed(str(exc), next=exc.next) from exc  # next: the scheduler's actionable refusal
        return (f"Scheduled {count} fresh {mission.acquisition['seconds']:g}-second camera clips for {session.name}, "
                f"starting every {every:g} seconds. The first starts now; the last starts after {(count - 1) * every:g} "
                "seconds. Missed slots stay missed. Analysis follows acquisition; no microphone or screen recording.")

    registry.add(Tool("schedule_acquisition",
        "On an explicit request, schedule fresh camera clips for a researcher. Start a researcher first if needed, "
        "then use its exact returned reference. The app owns the schedule beyond individual model calls. "
        "For ten five-second clips every 30 seconds use count=10, clip_seconds=5, every_seconds=30: "
        "starts 0 through 270 seconds, finishes around 275. No intermediate model commentary delays capture. "
        "This retains camera frames only and does not switch Record. Do not use for photo retrieval or restyling.",
        schema({"researcher": RESEARCH_REFERENCE, "count": {"type": "integer", "minimum": 2, "maximum": 481},
                "every_seconds": {"type": "number", "minimum": 30},
                "clip_seconds": {"type": "number", "minimum": 1.5, "maximum": 10},
                "asked_with": {"type": "string", "description": "quote the user's request for camera acquisition"}},
               ["researcher", "count", "every_seconds", "clip_seconds", "asked_with"]),
        schedule_acquisition, inline_ms=1500, family="researchers"))

    async def research_status(ctx: ToolContext, args: dict) -> str:
        if "wait" in args and type(args["wait"]) is not bool:
            raise ToolFailed("research status wait must be a boolean", next=None)  # no next: the caller supplied an invalid wait argument, not an executable operation
        if args.get("wait"):
            if not args.get("researcher"):
                raise ToolFailed("waiting needs an explicit original researcher reference", next=None)  # no next: selecting a researcher implicitly would invent the requested completion owner
            research_request_current(ctx, hub, services.day)
            session, attempt, _ref = bound_researcher(ctx, args["researcher"], manager, desk)
            if attempt is None:
                raise ToolFailed("that researcher has no original completion to wait for", next=None)  # no next: retained work alone cannot reconstruct an original live attempt
            try:
                outcome = await manager.wait_attempt(session, attempt,
                    lambda: research_request_current(ctx, hub, services.day))
                research_request_current(ctx, hub, services.day)
                ref = manager.result_ref(session, attempt, outcome)
                manager.resolve_attempt(ref)  # no current follow-up can heal the completed reference
            except ResearchRefused as exc:
                raise ToolFailed(str(exc), next=getattr(exc, "next", None)) from exc  # next: passed on
            record_output(ctx, {"kind": "resource", "resource": ref})
            return block(f"{session.name}, original run {attempt.number}: done. " + str((outcome.get("result") or {}).get("summary") or ""))
        if args.get("researcher"):
            s, _attempt, _ref = bound_researcher(ctx, args["researcher"], manager, desk)
            text = s.line()
            if s.result:  # its summary whole: read_result reads the rest of its work
                text += f". Summary: {s.result.get('summary', '')}"
            if s.outputs:
                kinds = {"html": "lesson", "video": "video", "image": "picture"}
                text += " (made: " + ", ".join(kinds.get(o.get("kind"), "result") for o in s.outputs) + ")"
            return block(text)
        lines = manager.lines(limit=None)  # every one of this session's, not the brain's capped roster
        return block(lines) if lines else "no researchers yet"

    registry.add(Tool("research_status", "How the researchers are doing, or one researcher's result. "
                      "wait=true waits for the explicitly selected original run while conversation continues; "
                      "only actual successful completion returns a result reference. Use after_steps for dependent actions.",
                      schema({"researcher": RESEARCH_REFERENCE, "wait": {"type": "boolean"}}),
                      research_status, kind="query", family="researchers"))

    if services.magnifier is not None:
        async def show_in_magnifier(ctx: ToolContext, args: dict) -> str:
            from ... import media

            s = find(args.get("researcher"), ctx)
            made = [(o["url"] if "url" in o else f"{s.base}/{o['path']}", o.get("caption") or "researcher picture", "",
                     media.of_output(o)) for o in s.outputs if o.get("kind") == "image"]
            found = [(f"{s.base}/{r['path']}", r.get("title") or "reference picture", r.get("credit") or "", media.REFERENCE)
                     for r in (s.result or {}).get("refs", [])]
            # Numbered as said in the description: what it was asked to make first, else the real photos it found
            # before what it made (an illustration made on its own never comes up ahead of a real photo: kind (f)).
            pictures = made + found if s.deliverable in ("image", "annotated_image") else found + made
            if not pictures:
                raise ToolFailed(f"{s.name} has no pictures yet", next="wait until it has made one, or ask it for one")
            n = args.get("picture")
            if n is not None and not (isinstance(n, (int, float)) and 1 <= int(n) <= len(pictures)):
                raise ToolFailed(f"{s.name} has pictures 1 to {len(pictures)}", next=None)  # no next: the model's own argument
            url, label, credit, kind_of = pictures[int(n) - 1] if n is not None else pictures[0]
            from ...screen import image_shown

            await image_shown(services.magnifier, url, f"{s.name}'s {media.noun(kind_of)}: {label}",
                              credit=credit or s.name, kind_of=kind_of,
                              object_id=s.object_id if s.object_id in hub.scene.objects else None)
            return f"showing {s.name}'s {media.noun(kind_of)} in the magnifier ({media.line(kind_of, credit=credit)})"

        registry.add(Tool("show_in_magnifier", "Show a researcher's picture in the magnifier: the reference photos it found "
                          "(real photos from the web, not this slide) first, then what it made (a labelled photo of this "
                          "slide, or an AI illustration); when it was asked for an illustration or a labelled photo, that "
                          "first. picture: which, 1-based in that order.",
                          schema({"researcher": RESEARCHER, "picture": {"type": "integer"}}),
                          show_in_magnifier, family="magnifier"))

    async def cancel_researcher(ctx: ToolContext, args: dict) -> str:
        s = find(args.get("researcher"), ctx)
        if not await manager.cancel(s.rid):
            raise ToolFailed(f"{s.name} is not running", next="ask it a follow-up question instead")
        return f"stopped {s.name}"

    registry.add(Tool("cancel_researcher", "Stop a researcher.", schema({"researcher": RESEARCHER}), cancel_researcher, family="researchers"))

    async def propose_correction(ctx: ToolContext, args: dict) -> str:
        obj = resolve_object(hub, args.get("object"), ctx)
        to_label = " ".join(str(args.get("to_label") or "").split())[:60]
        if not to_label:
            raise ToolFailed("say what it should be called", next=None)  # no next: the message says what to do
        manager.propose(manager.brain_session(), obj.id, to_label, str(args.get("reason") or ""), args.get("confidence"))
        return f"suggested relabelling {describe(obj)} as {to_label}; the user decides"

    registry.add(Tool("propose_correction", "Suggest that an outlined object is labelled wrongly and what it probably is; "
                      "nothing changes until the user accepts. Use when a closer look or a researcher's findings "
                      "contradict the label.", schema({"object": OBJECT, "to_label": {"type": "string"},
                                                       "reason": {"type": "string"}, "confidence": {"type": "number"}},
                                                      ["to_label", "reason"]), propose_correction, family="researchers", outline_refs=("object",)))

    async def summarize_research(ctx: ToolContext, args: dict) -> str:
        """Everything researchers found about an object or label, for the brain to summarise."""
        from ...labels import label_key

        if args.get("object"):
            obj = resolve_object(hub, args.get("object"), ctx)
            ids, key = {obj.id}, label_key(obj.label)
        else:
            ids, key = set(), label_key(str(args.get("label") or ""))
        lines = []
        for s in reversed(list(manager.sessions.values())):  # the newest researcher first
            if s.purpose == "brain" or not (s.object_id in ids or (key and label_key(s.label) == key) or not (ids or key)):
                continue
            if s.sitting != manager.session():
                continue  # another session's work is that session's (ADR 0017)
            if s.result:
                lines.append(f"{s.name}: {s.result.get('summary', '')}")
            lines += [f"{s.name} noted: {f['text']}" for f in s.findings]
            lines += [f"{s.name} suggested calling it {c['to_label']} ({c['status']})" for c in s.corrections]
        if not lines:
            return "no researcher has looked into that yet"
        kept, size = [], 0
        for line in lines:  # whole lines, newest first, within what one answer is given; the rest counted, never cut mid-way
            if size + len(line) > SUMMARY_MAX and kept:
                break
            kept.append(line)
            size += len(line) + 1
        more = (f"\n(and {len(lines) - len(kept)} older lines: read_result reads each researcher's work in full)"
                if len(kept) < len(lines) else "")
        return block("\n".join(kept) + more)

    registry.add(Tool("summarize_research", "Everything the researchers found about an object or label (results, notes, "
                      "suggested corrections), to summarise for the user when they ask what was found.",
                      schema({"object": OBJECT, "label": {"type": "string"}}), summarize_research, kind="query", family="researchers", outline_refs=("object",)))

    async def researcher_followup(ctx: ToolContext, args: dict) -> str:
        s = find(args.get("researcher"), ctx)
        from ...research.manager import ACTIVE

        busy = s.status in ACTIVE
        question = " ".join(str(args.get("question") or ctx.question).split())
        # A question about what it made is answered from that work (quick, no new picture or
        # lesson); "continue" re-runs it as it was (a new picture, a longer lesson).
        mode = "continue" if str(args.get("mode") or "") == "continue" else "answer"
        try:
            if mode == 'continue':
                from ...research.selection import require_current
                require_current(s, ctx.research_selection)
            # "continue" on a lesson or video needs the user's request: the question, in their words,
            # is checked against what they said (policy.quoted), as start_researcher's asked_with is.
            await manager.followup(s.rid, question, origin=origin(ctx, asked=True), mode=mode,
                                   words=recent_request(services, ctx), asked_with=str(args.get("asked_with") or ""), delivery=delivery(ctx),
                                   selection=ctx.research_selection, effort=args.get("effort") if mode == "continue" else None)
        except ResearchRefused as exc:
            raise ToolFailed(str(exc), next=getattr(exc, "next", None)) from exc  # next: passed on
        if busy and mode == "answer":  # kept, not refused (ResearchManager.followup)
            return (f"{s.name} is still working, so it will answer this from its work as soon as this run ends: "
                    f"{question}. What it has found so far can be read now with read_result.")
        return f"asked {s.name}: {question}; it answers from its work and will report back"

    registry.add(Tool("researcher_followup", "Ask a researcher a further question about its work (when read_result does "
                      "not answer it); it answers from what it already made and found, in about a minute. Asked while "
                      "it is still working, the question is kept and answered when its run ends (read_result reads what "
                      "it has found so far, now). "
                      "mode \"continue\" makes more of the same kind (a new picture, more of the lesson). To change "
                      "something it made, use revise_work instead. For continue, quote the user’s authorizing words in asked_with; "
                      "question is the work brief and may be paraphrased.",
                      schema({"researcher": RESEARCHER, "question": {"type": "string"},
                              "asked_with": {"type": "string", "description": "For continue: quote the user’s actual request authorizing more work, without added artifact metadata."},
                              "mode": {"type": "string", "enum": ["answer", "continue"]},
                              "effort": {"type": "string", "enum": CHOOSABLE,
                                         "description": "for continue: the effort the user asked for (\"go deeper\": standard or "
                                                        "deep, which needs their own words); omitted: its own"}}, ["question"]),
                      researcher_followup, family="researchers"))

    async def revise_work(ctx: ToolContext, args: dict) -> str:
        presenter = services.presenter
        if presenter is not None and ctx.client is not None:
            presenter = presenter.for_client(ctx.client)
        s = find(args.get("researcher"), ctx)
        change = " ".join(str(args.get("change") or ctx.question).split())
        target = str(args.get("target") or "").strip() or None
        shown = None  # the version the user is looking at, when its viewer shows this researcher
        if presenter is not None and presenter.open and presenter.rid == s.rid:
            from ...presenter import items as presenter_items

            listed = presenter_items(s)
            if 0 <= presenter.index < len(listed):
                shown = listed[presenter.index].get("id")
        target = target or shown
        try:
            from ...research.selection import require_current
            require_current(s, ctx.research_selection)
            if ctx.research_selection and ctx.research_selection.get('rid') == s.rid:
                target = ctx.research_selection.get('outputId') or target
                shown = ctx.research_selection.get('outputId') or shown
            selected_output = s.output(target) if target else None
            if (target == 'presentation' or selected_output and selected_output.get('kind') == 'presentation'
                    or args.get('scope') == 'presentation'):
                import hashlib
                from ...research.presentation import content_kind

                def restylable(output):
                    return output is not None and (output.get('kind') == 'presentation' or output.get('kind') == 'html')

                # What is restyled is what the user means: the lesson or presentation named or on screen, else this
                # researcher's own deliverable's (its lesson when it made one): never a new report beside it.
                restyled = next((o for o in (selected_output, s.output(shown) if shown else None) if restylable(o)), None)
                kind = content_kind(s, restyled)
                base = manager.presentations.current(s, kind)
                basis = (restyled or base or {}).get('findingRevision') or (s.result or {}).get('findingRevision')
                if not basis:
                    raise ResearchRefused('no exact findings version is available for this presentation', next='open its retained findings to inspect the available work')
                expected_base = (ctx.research_selection.get('presentationBase', (restyled or base or {}).get('id'))
                                 if ctx.research_selection and ctx.research_selection.get('rid') == s.rid
                                 else (restyled or base or {}).get('id'))
                job = manager.presentations.begin(s.rid, hashlib.sha256(f"{ctx.request_id}:{origin(ctx).get('action_id')}:{change}".encode()).hexdigest(),
                    version=basis['version'], digest=basis['digest'], model=presentation_model(args.get('model')), change=change,
                    expected_base=expected_base, kind=kind, notify=True, delivery=delivery(ctx))
                was = s.output(expected_base) if expected_base else None
                what = f"its {kind}" + (f" (v{int(was.get('version') or 1)})" if was else "")
                if job['state'] in ('queued', 'preparing'):
                    return (f"{s.name} is restyling {what} as a new version from what it kept; its findings and "
                            f"{'questions' if kind == 'lesson' else 'numbers'} stay as they are. The new version "
                            f"replaces it in the viewer when ready, or why it could not is said.")
                if job['state'] in ('ready', 'conflict'):
                    return f"{s.name} already restyled {what} for this request (output {job.get('output')})."
                raise ResearchRefused(f"could not restyle {what}: {job.get('error') or job['state']}",
                                      next="its current version stays as it is; ask again once that is resolved")
            output = manager.target_output(s, target)
            await manager.followup(s.rid, change, origin=origin(ctx, asked=True), mode="revise", target=output.get("id"),
                                   change=change, words=recent_request(services, ctx),
                                   asked_with=str(args.get("asked_with") or ""), delivery=delivery(ctx), selection=ctx.research_selection,
                                   edit_fields=args.get('edit_fields'))
        except ResearchRefused as exc:
            raise ToolFailed(str(exc), next=getattr(exc, "next", None)) from exc  # next: passed on
        kind = {"html": "lesson", "video": "video"}.get(output.get("kind"), "picture")
        seconds = EFFORTS[s.run_effort].seconds
        when = f"about {seconds:.0f} s" if seconds < 200 else f"about {seconds / 60:.0f} min"
        return (f"{s.name} is making version {int(output.get('version') or 1) + 1} of its {kind} ({change}), within "
                f"{when}; when ready it is in Researchers and Data")

    registry.add(Tool("revise_work", "Change something a researcher made, as a new version (the old one is kept): use it "
                      "when the user wants a change to its lesson, quiz, labels, picture or video (\"make question 2 "
                      "easier\", \"rename that label\", \"make the picture simpler\"). Not for questions about the work "
                      "(read_result, or researcher_followup) and not for new work (start_researcher). target: lesson, "
                      "labels, picture, video, presentation (layout only, without scientific changes), or an output id; nothing means the one on screen. "
                      "For changed scientific conclusions or measurements use researcher_followup mode continue.",
                      schema({"researcher": RESEARCHER, "target": {"type": "string"},
                              "scope": {"type":"string", "enum":["content", "presentation"],
                                        "description":"presentation restyles the retained report/lesson without changing scientific content or question text; content changes the work itself"},
                              "change": {"type": "string", "description": "what to change, in the user's words"},
                              "edit_fields": {"type":"array", "minItems":1, "maxItems":3,
                                  "items":{"type":"string", "enum":["prompt", "answer", "hint"]},
                                  "description":"For lesson step edits, name only the fields the user asks to change. A simpler question with its answer unchanged uses [prompt]. Protected fields are copied exactly; omit only for whole-lesson restructuring."},
                              "model": {"type": "string", "enum": list(PRESENTATION_PROVIDERS),
                                        "description": "explicit presentation provider only when requested by the user"},
                              "asked_with": {"type": "string", "description": "for a new version of a video: the user's own "
                                             "words asking for it, quoted as they said them"}}, ["change"]),
                      revise_work, inline_ms=1500, family="researchers"))

    async def researcher_note(ctx: ToolContext, args: dict) -> str:
        from ...held import MAX_SECONDS, held_of
        from ...research.policy import quoted
        from ..registry import CURRENT_RUN

        # The user's yes owns this hold, including after a late answer resumes a
        # researcher. Capture it before that manager I/O can await or switch days.
        asked, day = ctx.asked, services.day
        original_day = getattr(asked, "day", None)
        run = CURRENT_RUN.get()
        occurrence = run.occurrence if run is not None else None
        said = {"channel": ctx.channel, "delegation_id": ctx.delegation_id, "asked": asked,
                "day": original_day, "thread": getattr(asked, "thread", None),
                "voice_owner": ctx.voice_owner if ctx.channel == "voice" else None,
                "turn_id": ctx.turn_id or None,
                "request_id": occurrence.request_id if occurrence is not None else ctx.request_id,
                "action_id": occurrence.action_id if occurrence is not None else None}

        hold = args.get("hold_seconds")
        hold = float(hold) if isinstance(hold, (int, float)) and not isinstance(hold, bool) and hold >= 0 else None
        answers = str(args.get("answers") or "").strip() or None
        try:
            s = desk.find_working(args.get("researcher"))
        except ResearchRefused as exc:
            raise ToolFailed(str(exc), next=getattr(exc, "next", None)) from exc  # next: passed on
        if hold == 0 and not answers:  # the user stops footage being held for it, in their own words
            if not quoted(str(args.get("asked_with") or ""), recent_request(services, ctx), least=1):
                raise ToolFailed("a hold is stopped only when the user asks: quote the words they said",
                                 next="ask them whether to stop it")
            cancelled = held_of(hub).cancel(s.rid)
            if not cancelled:
                raise ToolFailed(f"nothing is being held for {s.name}", next=None)  # no next: nothing to cancel
            return f"stopped holding footage for {s.name} ({', '.join(c.id for c in cancelled)}); what was held so far stays for it"
        declined = hold == 0 and bool(answers)  # "no" to its footage, said as hold_seconds 0 with the answer
        if hold == 0:
            hold = None  # an answer with no footage ("no thanks"): passed on; nothing is held or stopped
        from ...research.manager import ACTIVE

        late = bool(answers) and s.status not in ACTIVE  # its answer came after it finished (kind (h))
        request = None
        if hold:
            # Footage is held only on the user's own yes (the partner §2.2): to one of its questions that asked for
            # footage, in words the user really said (quoted, and found in what they said), never only the model's word.
            if not answers:
                raise ToolFailed("footage is held only when the user agrees to a researcher's question for it",
                                 next="pass the note with the question it answers (answers=q1)")
            request = next((r for r in s.requests if r.get("id") == answers), None)  # an answer too late is carry_on's to say
            if request is None or not request.get("seconds"):
                raise ToolFailed(f"{s.name}'s question {answers} did not ask for footage", next="pass the answer as a plain note")
            if not quoted(str(args.get("asked_with") or ""), recent_request(services, ctx), least=1):
                raise ToolFailed("footage is held only on the user's own yes: quote the words they said",
                                 next="pass the answer without hold_seconds, or ask them")
        try:
            if late and declined:
                # "No" to its footage after it finished: filed on its question; nothing more is run for a no (#194 SF3).
                manager.close_question(s.rid, answers, str(args.get("text") or ctx.question))
                return (f"{s.name} had finished; your answer is filed on its question {answers}, and nothing more is run "
                        "for it")
            if late:
                # Its answer came after it finished (kind (h)): never dropped; it carries on with it.
                await manager.carry_on(s.rid, answers, str(args.get("text") or ctx.question), origin=origin(ctx, asked=True),
                                       words=recent_request(services, ctx), asked_with=str(args.get("asked_with") or ""),
                                       delivery=delivery(ctx))
            else:
                manager.note(s.rid, str(args.get("text") or ctx.question), answers=answers)
        except ResearchRefused as exc:
            if late:
                raise ToolFailed(f"{s.name} had finished before the answer came, and can't carry on with it: {exc}",
                                 next=getattr(exc, "next", None)) from exc  # next: passed on
            raise ToolFailed(str(exc), next=getattr(exc, "next", None)) from exc  # next: passed on
        if answers and hold and request is not None:
            # Real frames of the live view, held in memory for it, read with its session tool. Not a recording:
            # Record stays the user's. At most the seconds it asked for.
            seconds = min(hold, float(request["seconds"]), MAX_SECONDS)
            clip = held_of(hub).start(s.rid, s.name, answers, seconds, s.day,
                                      said=said)
            try:
                manager.note(s.rid, f"The app is holding {clip.seconds:g} s of the live view for you as {clip.id} (a frame "
                                    f"every 0.5 s); you are told when it is held; then read it with the session tool, id {clip.id}.",
                             about={"holding": clip.id})
            except ResearchRefused:
                pass  # it finished meanwhile: the clip is still in the session's index for a follow-up
            asked_for = float(request["seconds"])
            capped = (f" (it asked for {asked_for:g} s; the app holds at most {MAX_SECONDS:g} s)" if asked_for > MAX_SECONDS
                      else f" (it asked for {asked_for:g} s)" if hold > clip.seconds else "")
            if late:
                return (f"{s.name} had finished before your answer came, so it carries on with it; holding "
                        f"{clip.seconds:g} s of the live view for it as {clip.id}{capped}; it reports back when done")
            return (f"passed the answer to {s.name}'s question {answers}; holding {clip.seconds:g} s of the live view "
                    f"for it as {clip.id}{capped}")
        if late:
            return f"{s.name} had finished before your answer came, so it carries on with it; it reports back when done"
        if answers:
            return f"passed the answer to {s.name}'s question {answers}; it reads it before its next step"
        return f"passed the note to {s.name}; it reads it before its next step"

    registry.add(Tool("researcher_note", "Pass a note to a researcher that is still working, to steer it (\"focus on the "
                      "guard cells\", \"keep it short\"); it reads it before its next step. The user's answer to its "
                      "question, even \"yes\", goes here with answers, also after it finished (it then carries on with "
                      "it). Other words once it has finished: researcher_followup or revise_work.",
                      schema({"researcher": RESEARCHER, "text": {"type": "string"},
                              "answers": {"type": "string", "description": "its question's id (q1)"},
                              "hold_seconds": {"type": "number", "description": "the user agreed to footage it asked for: "
                                               "seconds to hold for it (0, without answers: the user stops a hold)"},
                              "asked_with": {"type": "string", "description": "with hold_seconds: the user's own words, quoted"}},
                             ["text"]), researcher_note,
                      inline_ms=500, family="researchers"))

    async def set_research_cap(ctx: ToolContext, args: dict) -> str:
        from ...options import parse_cap

        # Spending changes only in the user's own words: a researcher's text (web pages) can reach the model, and must
        # never raise or remove the cap. The model quotes what the user said, checked against what they said (as every
        # admission is): no word list decides what counts as asking ("unlimited", "allow twenty a day").
        from ...research.policy import quoted

        if not quoted(str(args.get("asked_with") or ""), recent_request(services, ctx)):
            raise ToolFailed("the research cap changes only when the user asks for it",
                             next="quote what the user said asking for it in asked_with")
        try:
            cap = parse_cap(args.get("dollars"))
        except ValueError as exc:
            raise ToolFailed("say an amount in dollars, or none for no cap",
                             next=None) from exc  # no next: the message says what to do
        hub.settings.research_cap = cap
        if services.day is not None:
            services.day.set_pref("researchCap", cap)  # kept until changed, across restarts and days
        if services.publish_settings is not None:
            services.publish_settings()
        spent = manager.spent_today()
        return (f"no daily research cap now (${spent:.2f} spent today)" if cap is None
                else f"the daily research cap is ${cap:.2f} now (${spent:.2f} spent today)")

    registry.add(Tool("set_research_cap", "Set or remove the daily spending cap for researchers when the user asks "
                      "(\"cap research at $10 a day\", \"remove the research cap\"); dollars: an amount, or \"none\".",
                      schema({"dollars": {"type": "string", "description": 'an amount like "10", or "none"'},
                              "asked_with": {"type": "string", "description": "the user's own words asking for it, quoted as said"}},
                             ["dollars", "asked_with"]),
                      set_research_cap, inline_ms=100, family="researchers", sets=("researchCap",)))


__all__ = ["add_research_tools", "named", "made", "recent_request"]
