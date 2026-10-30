"""Experiments (orchestrator/experiments.py; Part 2.5, W5): "compare Cellpose and micro-sam on this footage and have a
researcher audit it".

`experiment` plans one (do=plan: the brain chooses the models, the input and the audit from the user's words; code
checks every fact and draws the plan's card), changes the plan (do=change), runs it on the user's own yes (do=run: the
card's Run it, or their words quoted in asked_with) and stops it (do=stop). `experiment_status` reads how one is going
from its records. Nothing runs before a yes, and what runs is exactly the plan approved.
"""
from __future__ import annotations

from ..registry import Registry, Tool, ToolContext, ToolFailed, attach, schema
from .resources import require_current

ACTIONS = ("plan", "change", "run", "stop")


def add_experiment_tools(registry: Registry, services) -> None:
    hub = services.hub
    if services.day is None:  # a Project to keep it in (its store is read when it runs)
        return
    from ... import experiments as X

    def service():
        return X.experiments_of(hub, services.day)

    def where(ctx: ToolContext) -> tuple[str, str | None]:
        require_current(ctx, services.day)
        session = getattr(ctx.asked, "session", None) or services.day.sid
        thread = services.day.thread_for(ctx.asked) if ctx.asked is not None and hasattr(services.day, "thread_for") else None
        return session, thread

    def request_of(ctx: ToolContext) -> str | None:
        from ..registry import CURRENT_RUN

        run = CURRENT_RUN.get()
        occurrence = getattr(run, "occurrence", None)
        return (getattr(occurrence, "request_id", None) or getattr(getattr(ctx, "admission", None), "request_id", None)
                or ctx.request_id)

    async def experiment(ctx: ToolContext, args: dict) -> str:
        do = str(args.get("do") or "plan")
        if do not in ACTIONS:
            raise ToolFailed(f"do is one of {', '.join(ACTIONS)}", next="plan it first, then run it on the user's yes")
        session, thread = where(ctx)
        xs = service()
        if do == "plan":
            x = xs.draft(session=session, thread=thread, models=args.get("models"), on=str(args.get("on") or "footage"),
                         frames=args.get("frames"), every=args.get("every"), audit=args.get("audit", True) is not False,
                         question=args.get("question"), effort=str(args.get("effort") or "quick"),
                         measure=args.get("measure", True) is not False, asked=ctx.question or "",
                         request_id=request_of(ctx), turn_id=ctx.turn_id or None, asked_in=ctx.asked)
            attach(values={"experiment": x.wire()})
            return X.plan_words(x) + ". Ask the user once whether to run it; nothing has run"
        x = xs.get(args.get("experiment"), session)
        if do == "change":
            x = xs.change(x, models=args.get("models"), on=args.get("on"), frames=args.get("frames"),
                          every=args.get("every"), audit=args.get("audit"), question=args.get("question"),
                          effort=args.get("effort"), measure=args.get("measure"))
            attach(values={"experiment": x.wire()})
            return "Changed: " + X.plan_words(x) + ". Ask the user once whether to run it as it is now; nothing has run"
        if do == "stop":
            said = await xs.stop(x)
            attach(values={"experiment": x.wire()})
            return said
        # do == "run": exactly the plan, on the user's own yes.
        card = args.get("by") == "card" and ctx.actor == "you" and ctx.channel in ("system", "studio")
        words = ""
        if not card:
            from ...research.policy import quoted
            from .research import recent_request

            words = str(args.get("asked_with") or "")
            if not quoted(words, recent_request(services, ctx), least=1):
                raise ToolFailed(f"{x.name} runs only on the user's own yes to its plan",
                                 next="ask whether to run it, then quote their yes in asked_with")
        actor = "user" if card else {"chat": "agent:chat", "voice": "agent:voice"}.get(ctx.channel, "agent:system")
        version = args.get("version") if type(args.get("version")) is int else None
        x = xs.approve(x, version=version, by="card" if card else "words", actor=actor, words=words,
                       client=ctx.client)
        attach(values={"experiment": x.wire()})
        return (f"Started {x.name} ({x.title}), exactly as planned: {x.plan['input']['says']}, then each model on every "
                f"frame{', then the audit' if x.plan['audit'] else ''}. It works in the background; its card shows how it "
                "goes, and its results open in a Tab of their own when it finishes, said then")

    from ... import model_comparison, names

    from ...operations import OPERATIONS as _OPERATIONS

    ids = [i.id for i in _OPERATIONS["cells"].implementations if i.id in names.choosable_ids()] or \
        [i.id for i in _OPERATIONS["cells"].implementations]
    registry.add(Tool(
        "experiment", "Plan, change, run or stop an experiment the user asks for: models compared on this footage (frames "
        "of the live view over time) or on the view now, each through the app's runner, compared frame by frame and "
        "measured, optionally audited by a researcher who checks the run's records (\"compare Cellpose and micro-sam on "
        "this footage and have a researcher audit it\"). do=plan first: it shows the plan as a card and runs nothing; "
        "say the plan and ask once. do=run only on the user's own yes to that plan (asked_with: their words); "
        "do=change when they change it. Long work: it runs in the background and its results land in a Tab of their own "
        "when it finishes. One model on one view now is compare_models.",
        schema({"do": {"type": "string", "enum": list(ACTIONS)},
                "experiment": {"type": "string", "description": "change/run/stop: its id (x1) or number; default the "
                                                                "latest"},
                "models": {"type": "array", "minItems": 2, "maxItems": model_comparison.MAX_MODELS,
                           "items": {"type": "string", "enum": ids},
                           "description": "the models the user named, by their ids as listed under use"},
                "on": {"type": "string", "enum": ["footage", "view"],
                       "description": "footage (default): frames of the live view over time; view: the view now"},
                "frames": {"type": "integer", "minimum": 1, "maximum": X.FRAMES_MAX,
                           "description": f"footage: how many frames (default {X.FRAMES_DEFAULT})"},
                "every": {"type": "number", "minimum": X.EVERY_MIN_S, "maximum": X.EVERY_MAX_S,
                          "description": f"footage: seconds between frames (default {X.EVERY_DEFAULT_S:g})"},
                "audit": {"type": "boolean", "description": "a researcher audits the run (default true when asked)"},
                "question": {"type": "string", "maxLength": 2000,
                             "description": "what the researcher should check, in the user's words"},
                "effort": {"type": "string", "enum": list(X.EFFORTS), "description": "the audit's effort (quick)"},
                "measure": {"type": "boolean", "description": "measure each model's cells (default true)"},
                "asked_with": {"type": "string", "description": "run: the user's own yes, quoted"},
                "version": {"type": "integer", "description": "run: the plan's version the user said yes to"},
                "by": {"type": "string", "enum": ["card"], "description": "run from the plan card's Run it (the studio)"}},
               ["do"]),
        experiment, kind="effect", inline_ms=1500, family="researchers"))

    async def experiment_status(ctx: ToolContext, args: dict) -> str:
        session, _ = where(ctx)
        xs = service()
        x = xs.get(args.get("experiment"), session)
        attach(values={"experiment": x.wire()})
        others = [o for o in xs.of_session(session) if o is not x][-3:]
        rest = ("; others in this Project: " + ", ".join(f"{o.name} ({o.title}, {o.status})" for o in others)) \
            if others else ""
        return xs.status_words(x) + rest

    registry.add(Tool(
        "experiment_status", "How an experiment is going, or how it ended (\"how is the experiment going?\"), from its "
        "records: its state, its frames, each model's runs so far, its audit, its results. The latest by default.",
        schema({"experiment": {"type": "string", "description": "its id (x1) or number; default the latest"}}, []),
        experiment_status, kind="query", inline_ms=300, family="researchers"))
