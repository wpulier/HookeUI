"""Designing experiments with the user (orchestrator/experiment_design.py; Lane C of the "Hooke drives everything"
plan): "help me design an experiment on whether light opens these stomata".

`propose_experiments` drafts two or three designs (the brain's own model, from the Project, what Hooke can run here and
a researcher's literature pass when named; code checks every fact) and shows them as plan cards in a document in a Tab
of its own. `revise_design` makes the user's spoken change to one ("three replicates, add a dark control"): its next
version, shown again. `study_plan` saves a design as a Study plan (a draft: nothing runs), approves a saved plan on the
user's own yes (the owner review; limits past the owner's own stay the owner's, in the Study), or proposes an
assignment for a contributor. Running a plan's steps is `experiment` (design=...), on the user's yes.
"""
from __future__ import annotations

import asyncio

from ..registry import Registry, Tool, ToolContext, ToolFailed, attach, schema
from .resources import require_current

PLAN_ACTIONS = ("save", "approve", "assign")
DESIGN = {"type": "string", "description": "the design by its id as its card shows it (d2), or its number"}


def add_experiment_design_tools(registry: Registry, services) -> None:
    hub = services.hub
    if services.day is None:  # a Project to keep designs and Studies in
        return
    from ... import experiment_design as E

    def designs():
        return E.designs_of(hub, services.day)

    def session_of(ctx: ToolContext) -> str:
        require_current(ctx, services.day)
        return getattr(ctx.asked, "session", None) or services.day.sid

    def actor_of(ctx: ToolContext) -> tuple[str, str]:
        actor = {"chat": "agent:chat", "voice": "agent:voice"}.get(ctx.channel, "agent:system")
        return actor, f"Hooke {actor[6:]}"

    async def facts(session: str, literature=()):
        return await designs().facts(session, capabilities=services.capability_status, literature=literature)

    async def show(ctx: ToolContext, proposal: dict, *, tool: str, args: dict, open_it: bool) -> str:
        """The plan cards kept as one document in Data (Write-ups) and opened in a Tab of its own."""
        from ... import documents
        from ...data_reader import reader_studio
        from ...resources import resources_of
        from ...screen import screen_of
        from ..registry import CURRENT_RUN

        values = E.values_of(proposal, hub.settings.vision)
        index = resources_of(hub)
        results = index.results
        run = CURRENT_RUN.get()
        if run is not None and run.tool == tool:  # its own run's result, kept once (resources.from_run)
            attach(values=values)
            kept = results.from_run(run, ctx, hub, produced=True)
        else:
            kept = results.add(tool, args, E.summary_of(proposal), values=values, request=ctx.question or "",
                               session=proposal["session"], actor="assistant", media="document")
        if kept is None:
            return "they could not be kept in Data, so no cards are shown"
        proposal["documents"] = [*proposal.get("documents", []), kept["id"]]
        await designs().keep_document(proposal)
        saved = f"its plan cards are saved in Data as {kept['id']} (Write-ups)"
        if not open_it:
            return saved + "; not opened"
        entry = await asyncio.to_thread(index.get, kept["id"], proposal["session"])
        studio = reader_studio(screen_of(hub), ctx, services.day.sid)
        if entry is None or studio is None:
            return saved + "; not shown: no studio shows this Project now"

        def guard():
            require_current(ctx, services.day)

        try:
            where = await documents.show(services, ctx, studio, entry, placement="new-tab", title=E.TAB_TITLE,
                                         guard=guard)
        except ToolFailed as exc:
            return f"{saved}; not shown: {exc}"
        return f"{saved} and {where}"

    def progress(text: str) -> None:
        from ..registry import CURRENT_RUN

        run = CURRENT_RUN.get()
        if run is not None:
            run.report_progress(text)

    async def propose_experiments(ctx: ToolContext, args: dict) -> str:
        session = session_of(ctx)
        question = " ".join(str(args.get("question") or ctx.question or "").split())[:600]
        if not question:
            raise ToolFailed("say what the experiment should find out", next="ask the user what they want to test")
        literature = []
        for ref in args.get("build_on") or []:
            manager = services.research
            found = None
            if manager is not None and services.desk is not None:
                try:
                    found = E.research_text(manager, services.desk.find(ref).rid)
                except Exception:  # noqa: BLE001 - a researcher not found is said
                    found = None
            if found is None:
                raise ToolFailed(f"{ref} has no finished work to draw on", next="wait for it to finish, or leave it out")
            literature.append(found)
        known = await facts(session, literature)
        require_current(ctx, services.day)
        try:
            proposal = await designs().propose(session=session, asked=ctx.question or question, question=question,
                                               count=args.get("designs") or E.DESIGNS_MAX, facts=known,
                                               agent=services.agent, model=hub.settings.vision,
                                               thread=services.day.thread_for(ctx.asked) if ctx.asked else None,
                                               progress=progress)
        finally:
            progress(None)
        require_current(ctx, services.day)
        where = await show(ctx, proposal, tool="propose_experiments", args={"question": question},
                           open_it=args.get("open", True) is not False)
        left = f"; {len(proposal['left'])} more could not be run here as drafted" if proposal["left"] else ""
        return (f"{len(proposal['designs'])} designs, nothing run{left}: "
                + " | ".join(E.said(d) for d in proposal["designs"])
                + f". {where[:1].upper()}{where[1:]}. Say the designs briefly, one line each by title and how they "
                  "differ, then ask which they like or what to change; a change is revise_design, a pick to keep is "
                  "study_plan save, and nothing runs before their yes to experiment's plan")

    registry.add(Tool(
        "propose_experiments", "Design experiments with the user (\"help me design an experiment on whether light opens "
        "these stomata\"): two or three designs drafted from this Project, what Hooke can capture and measure here and, "
        "when named, a researcher's literature pass (build_on), each with its question, conditions, controls, "
        "replicates, measures, duration and steps (what the user does by hand, what Hooke captures on a schedule and "
        "analyses); shown as plan cards in a Tab of their own. Nothing runs. For a literature pass first, start a quick "
        "researcher and name it in build_on once it is done.",
        schema({"question": {"type": "string", "maxLength": 600,
                             "description": "what the experiment should find out, in the user's words"},
                "designs": {"type": "integer", "minimum": E.DESIGNS_MIN, "maximum": E.DESIGNS_MAX,
                            "description": f"how many designs (default {E.DESIGNS_MAX})"},
                "build_on": {"type": "array", "maxItems": 3, "items": {"type": "string"},
                             "description": "finished researchers whose work (a literature pass) the designs draw on, "
                                            "as the user named them"},
                "open": {"type": "boolean", "description": "show the plan cards in a Tab (default true)"}},
               ["question"]),
        propose_experiments, kind="effect", inline_ms=1500, family="researchers"))

    async def revise_design(ctx: ToolContext, args: dict) -> str:
        session = session_of(ctx)
        change = " ".join(str(args.get("change") or "").split())[:600]
        if not change:
            raise ToolFailed("say what to change in the design", next="quote the user's change in change")
        known = await facts(session)
        progress("revising the design")
        try:
            proposal, d = await designs().revise(session=session, ref=args.get("design"), change=change, facts=known,
                                                 agent=services.agent, model=hub.settings.vision)
        finally:
            progress(None)
        require_current(ctx, services.day)
        where = await show(ctx, proposal, tool="revise_design", args={"design": d["id"], "change": change},
                           open_it=args.get("open", True) is not False)
        unsaved = (f" Its Study plan is still version {d['study']['version']}: study_plan save keeps this version"
                   if d.get("study") else "")
        return (f"Revised as asked (\"{change}\"): {E.said(d)}. {where[:1].upper()}{where[1:]}.{unsaved} Say what "
                "changed in one sentence; nothing runs")

    registry.add(Tool(
        "revise_design", "Change one experiment design as the user says (\"three replicates, add a dark control\"): it "
        "becomes the design's next version (the old one kept), checked like the first, its cards shown again.",
        schema({"design": DESIGN,
                "change": {"type": "string", "maxLength": 600, "description": "the user's change, in their words"},
                "open": {"type": "boolean", "description": "show the cards again (default true)"}},
               ["design", "change"]),
        revise_design, kind="effect", inline_ms=1500, family="researchers"))

    async def study_plan(ctx: ToolContext, args: dict) -> str:
        session = session_of(ctx)
        do = str(args.get("do") or "save")
        actor, label = actor_of(ctx)
        if do not in PLAN_ACTIONS:
            raise ToolFailed(f"do is one of {', '.join(PLAN_ACTIONS)}", next="save the design as a plan first")
        if do == "save":
            proposal, d, plan = await designs().save_plan(session=session, ref=args.get("design"), actor=actor,
                                                          label=label)
            attach(values={"study": {"id": d["study"]["id"], "design": d["id"], "version": d["version"],
                                     "plan": (plan.get("plan") or {}).get("status")}})
            return (f"Saved {d['id']} (version {d['version']}, \"{d['title']}\") as the plan of a Study of its own "
                    f"({d['study']['id']}), a draft: nothing reviewed, nothing run. Its run is experiment "
                    f"do=plan design={d['id']}, which shows the run's card and runs only on the user's yes (their yes "
                    "approves the plan)")
        from ...research.coordination import Coordination
        from ...experiment_design import studies_of
        from ...scientific_projects import Conflict, InvalidProject

        _proposal, d = designs().get(args.get("design"), session)
        study = (d.get("study") or {}).get("id")
        if not study:
            raise ToolFailed(f"{d['id']} is not saved as a Study plan yet", next=f"study_plan do=save design={d['id']}")
        studies = studies_of(hub, services.day)
        coordination = Coordination(studies)
        if do == "approve":
            from ...research.policy import quoted
            from .research import recent_request

            words = str(args.get("asked_with") or "")
            if not quoted(words, recent_request(services, ctx), least=1):
                raise ToolFailed("a plan is approved only on the user's own yes to it",
                                 next="ask whether they approve the plan, then quote their yes in asked_with")
            try:
                async with studies._locks.setdefault((session, study), asyncio.Lock()):
                    current = await asyncio.to_thread(coordination.read, study, session)
                    result = await asyncio.to_thread(coordination.review_plan, study, session, basis=current["basis"],
                                                     actor=actor, label=label, owner_yes=" ".join(words.split())[:300])
            except (InvalidProject, Conflict) as exc:
                raise ToolFailed(f"the plan was not approved: {exc}",
                                 next="the owner reviews its limits in the Study in Hooke") from None
            attach(values={"study": {"id": study, "design": d["id"], "plan": result["plan"]["status"]}})
            return (f"{d['id']}'s Study plan is approved on your yes (\"{words}\"), within its limits: "
                    f"{result['plan']['limits']}; nothing has run")
        fields = {"objective": args.get("objective"), "deliverable": args.get("deliverable"),
                  "uncertainty": args.get("uncertainty") or d["question"],
                  "decisionImpact": args.get("decision_impact") or f"Part of {d['id']}'s plan: {d['title']}",
                  "whyNewWork": args.get("why_new") or "The plan needs it and no one has done it",
                  "evidence": [], "dependsOn": []}
        if not fields["objective"] or not fields["deliverable"]:
            raise ToolFailed("an assignment says its objective and its deliverable",
                             next="say what the contributor should do and what they hand back")
        fields = {k: (" ".join(str(v).split())[:8000] if isinstance(v, str) else v) for k, v in fields.items()}
        request = " ".join(str(args.get("request") or fields["objective"]).split())[:128]
        try:
            assigned = await asyncio.to_thread(coordination.propose, study, session, request=request,
                                               assignment=fields, actor=actor, label=label)
        except (InvalidProject, Conflict, ValueError) as exc:
            raise ToolFailed(f"the assignment was not proposed: {exc}", next=None) from None  # no next: said
        attach(values={"study": {"id": study, "design": d["id"], "assignment": assigned["id"]}})
        return (f"Proposed an assignment in {d['id']}'s Study ({assigned['id']}, {assigned['observedState']}): "
                f"{fields['objective']}. A contributor (a connected agent) claims it through Hooke's door; nothing "
                "has run")

    registry.add(Tool(
        "study_plan", "A design's Study plan: do=save keeps a design (by id) as the plan of a Study of its own, a "
        "draft (nothing runs; saving again after a revision saves its next version); do=approve approves a saved "
        "plan on the user's own yes (asked_with: their words; their yes is the owner review, within the limits the "
        "owner already approved, raising them is the owner's in the Study); do=assign proposes an assignment in its "
        "Study for a contributor (a connected agent) to claim. Running the plan is experiment design=...",
        schema({"do": {"type": "string", "enum": list(PLAN_ACTIONS)}, "design": DESIGN,
                "asked_with": {"type": "string", "description": "approve: the user's own yes, quoted"},
                "objective": {"type": "string", "maxLength": 2000, "description": "assign: what to do"},
                "deliverable": {"type": "string", "maxLength": 2000, "description": "assign: what to hand back"},
                "uncertainty": {"type": "string", "maxLength": 2000},
                "decision_impact": {"type": "string", "maxLength": 2000},
                "why_new": {"type": "string", "maxLength": 2000},
                "request": {"type": "string", "maxLength": 128,
                            "description": "assign: a short name for it; the same name is the same assignment"}},
               ["do", "design"]),
        study_plan, kind="effect", inline_ms=1500, family="researchers"))
