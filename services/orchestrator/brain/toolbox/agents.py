"""Tools on the outside agents Hooke starts on this Mac (ADR 0029): start one, see what every worker is doing, pass one
a note, stop one. The conductor (agents/conductor.py) does the work; the Agents panel shows the same launches.

An agent's asks for permission are never answered here: only the user answers them, in the Agents panel. What an agent
says is untrusted text, shown as such.
"""
from __future__ import annotations

from ...agents.conductor import Refused
from ...agents.programs import BY_ID
from ...research.policy import quoted
from ...untrusted import block
from ..registry import Registry, Tool, ToolContext, ToolFailed, attach, schema
from .research import recent_request

PROGRAM = {"type": "string", "enum": list(BY_ID),
           "description": "which agent: codex (OpenAI Codex), claude-code (Claude Code), grok (Grok Build), gemini (Gemini CLI)"}
AGENT = {"type": "string", "description": "which outside agent, as the user named it (\"Codex\", \"Grok\"); omit for the "
                                          "only one running"}


def add_agent_tools(registry: Registry, services) -> None:
    if services.agents is None:
        return

    def conductor():
        found = services.agents()
        if found is None:
            raise ToolFailed("Hooke can't start outside agents in this copy", next=None)  # no next: a build without it
        return found

    started: dict[tuple, str] = {}  # (conversation, request words) -> the launch already started for it

    async def start_agent(ctx: ToolContext, args: dict) -> str:
        words = recent_request(services, ctx)
        if not quoted(str(args.get("asked_with") or ""), words):
            raise ToolFailed("an outside agent starts only when the user asked for one",
                             next="quote the user's own words asking for it in asked_with")
        key = (id(ctx.asked), words.strip())
        if key in started:
            launch = conductor().launches.get(started[key])
            if launch is not None:
                attach(values={"agentLaunch": launch.id})
                return f"{launch.label} was already started for this request ({launch.status}); no other was started"
        try:
            launch = await conductor().start(str(args.get("program") or ""), str(args.get("task") or ""),
                                             by="voice" if ctx.channel == "voice" else "chat")
        except Refused as error:
            raise ToolFailed(error.says, next=error.next or None) from error
        started[key] = launch.id
        attach(values={"agentLaunch": launch.id})
        # What the voice says once: that it is working, and how it reports back. Not the Project, the Study or a cap.
        return (f"{launch.label} is starting on it now in this Project; it will ask before doing anything outside its "
                f"sandbox, and its report comes back for the user's review")

    registry.add(Tool(
        "start_agent",
        "Start an outside coding agent installed on this Mac (Codex, Claude Code, Grok Build) on a task the user "
        "gave, with the user's own sign-in; it works in this Project through Hooke and reports back for review. Only "
        "when the user asked for that agent. Say you are getting that agent on it, not that you will do it yourself.",
        schema({"program": PROGRAM,
                "task": {"type": "string", "description": "what the agent should do, in the user's words"},
                "asked_with": {"type": "string", "description": "the user's own words asking for the agent"}},
               ["program", "task", "asked_with"]),
        start_agent, family="researchers", inline_ms=4000))

    async def agents(ctx: ToolContext, args: dict) -> str:
        research = services.research
        sessions = research.researchers() if research is not None else []
        working = [s for s in sessions if s.status in ("queued", "starting", "running")]
        outside = conductor().summary()
        if not outside and not sessions:
            return "nobody is working: no Hooke researchers and no outside agents in this Project"
        said = "; ".join(outside[:4]) if outside else "no outside agents"
        said += f". Hooke researchers working: {len(working)}" + (f" ({', '.join(s.name for s in working[:3])})"
                                                                  if working else "")
        details = [f"Hooke researcher {s.line()}" for s in sessions[-6:]]
        for row in conductor().wire()[:6]:
            line = f"{row['label']} ({row['status']}): {row['task'][:120]} — {row['line']}"
            if row.get("costUsd") is not None:
                line += f"; about ${row['costUsd']:.2f} by its own count"
            details.append(line)
        # The summary is Hooke's; the details hold agents' own words, which are data, never instructions.
        return (f"in one or two sentences: {said}.\n" + block("\n".join(details))) if details else said

    registry.add(Tool("agents", "What every worker is doing: Hooke's researchers and the outside agents Hooke started "
                                "(working, needs you, done or failed); say it in one or two sentences.", schema({}),
                      agents, kind="query", family="researchers", inline_ms=300))

    async def answer_agent(ctx: ToolContext, args: dict) -> str:
        # The user's own yes or no to what an agent asked, never Hooke's or the agent's: their words are checked.
        if not quoted(str(args.get("asked_with") or ""), recent_request(services, ctx), least=1):
            raise ToolFailed("an agent's ask is answered only by the user's own yes or no",
                             next="ask them once, then quote their answer in asked_with")
        allow = args.get("answer") == "allow"
        try:
            launch, ask = conductor().waiting(args.get("agent"))
            await conductor().answer(launch.id, ask["id"], allow, always=bool(args.get("always")) and allow)
        except Refused as error:
            raise ToolFailed(error.says, next=error.next or None) from error  # next: passed on
        attach(values={"agentLaunch": launch.id, "agentAsk": ask["id"]})
        what = ask["title"].lower()
        if not allow:
            return f"declined: {launch.label} won't {what}; it carries on without it"
        return (f"allowed {launch.label} to {what}" + (" for this session" if args.get("always") else "") +
                "; it carries on")

    registry.add(Tool(
        "answer_agent", "Give the user's own answer to what an outside agent asked to do (allow or decline), after "
        "they said it; never on Hooke's own judgement.",
        schema({"agent": AGENT, "answer": {"type": "string", "enum": ["allow", "decline"]},
                "always": {"type": "boolean", "description": "the user said yes for the rest of this session"},
                "asked_with": {"type": "string", "description": "the user's own answer, quoted"}},
               ["answer", "asked_with"]),
        answer_agent, family="researchers", inline_ms=2000))

    async def agent_note(ctx: ToolContext, args: dict) -> str:
        try:
            launch = conductor().find_one(args.get("agent"))
            how = await conductor().note(launch.id, str(args.get("text") or ""),
                                         by="voice" if ctx.channel == "voice" else "chat")
        except Refused as error:
            raise ToolFailed(error.says, next=error.next or None) from error
        attach(values={"agentLaunch": launch.id})
        return {"steered": f"told {launch.label} while it works (as Hooke's note, not the user's approval)",
                "queued": f"{launch.label} gets it when its current step ends (as Hooke's note)",
                "started": f"told {launch.label}; it is working on it (as Hooke's note)"}.get(how, f"told {launch.label}")

    registry.add(Tool("agent_note", "Pass an outside agent a note from the user (\"tell Codex to use micro-sam\"); "
                                    "it is marked as Hooke's relay, never as the user's approval of anything.",
                      schema({"agent": AGENT, "text": {"type": "string", "description": "what to tell it"}}, ["text"]),
                      agent_note, family="researchers", inline_ms=2000))

    async def stop_agent(ctx: ToolContext, args: dict) -> str:
        try:
            launch = conductor().find_one(args.get("agent"))
            launch = await conductor().stop(launch.id)
        except Refused as error:
            raise ToolFailed(error.says, next=error.next or None) from error
        attach(values={"agentLaunch": launch.id})
        return f"stopped {launch.label}"

    registry.add(Tool("stop_agent", "Stop an outside agent Hooke started, and revoke its connection.",
                      schema({"agent": AGENT}), stop_agent, family="researchers", inline_ms=3000))
