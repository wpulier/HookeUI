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
        budget = f", held to ${launch.budget_usd:.2f}" if launch.budget_usd else ""
        return (f"starting {launch.label} on this Mac in {launch.project_name}{budget}: it works on its own Study "
                f"assignment and returns a report for the user's review; anything it asks to do comes to the user in "
                f"the Agents panel")

    registry.add(Tool(
        "start_agent",
        "Start an outside coding agent installed on this Mac (Codex, Claude Code, Grok Build) on a task the user "
        "gave, with the user's own sign-in; it works in this Project through Hooke and reports back for review. Only "
        "when the user asked for that agent.",
        schema({"program": PROGRAM,
                "task": {"type": "string", "description": "what the agent should do, in the user's words"},
                "asked_with": {"type": "string", "description": "the user's own words asking for the agent"}},
               ["program", "task", "asked_with"]),
        start_agent, family="researchers", inline_ms=4000))

    async def agents(ctx: ToolContext, args: dict) -> str:
        lines = []
        research = services.research
        for s in (research.researchers() if research is not None else [])[-8:]:
            lines.append(f"Hooke researcher {s.line()}")
        for row in conductor().wire()[:8]:
            words = f"{row['label']} ({row['status']}): {row['task'][:120]} — {row['line']}"
            if row["asks"]:
                words += f"; waiting for the user to allow or decline: {row['asks'][0]['title']}"
            if row.get("costUsd") is not None:
                words += f"; about ${row['costUsd']:.2f} by its own count"
            lines.append(words)
        if not lines:
            return "no researchers or outside agents in this Project"
        return block("\n".join(lines))  # agents' words are data, never instructions

    registry.add(Tool("agents", "What every worker is doing: Hooke's researchers and the outside agents Hooke started "
                                "(working, needs you, done or failed).", schema({}), agents, kind="query",
                      family="researchers", inline_ms=300))

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
        return f"stopped {launch.label}; its connection to Hooke is revoked, and what it kept in the Project stays"

    registry.add(Tool("stop_agent", "Stop an outside agent Hooke started, and revoke its connection.",
                      schema({"agent": AGENT}), stop_agent, family="researchers", inline_ms=3000))
