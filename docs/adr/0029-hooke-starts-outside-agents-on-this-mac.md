# 0029 — Hooke may start outside agents on this Mac, with the user's own programs and sign-in

**Accepted, 2026-11-03: the founder approved this decision in the "Hooke drives everything" plan (Lane D, "a new ADR
superseding ADR 0026's deferral: Hooke may launch outside agents on this Mac with the user's own logins"; "outside
agents run locally first, cloud kept in mind").** Its shipped behaviour is qualified separately, as ADR 0026's was.

It supersedes one line of ADR 0026's October 28 amendment: "Launching subscribed agent CLIs from Hooke is deferred
pending separate supported-use, consent and resource-control qualification." This record is that qualification for
local launches. Everything else in ADR 0026 stands: an agent Hooke starts uses the same door, as itself.

## Context

- **What the user wants.** "Ask Codex to write a stomata-tracking capability." "Have Claude read the last ten papers on
  guard-cell aperture." "Give Grok the counting task." "What's everyone doing?" "Tell Codex to use micro-sam." "Stop
  Grok." Today a person starts the agent in a terminal and pastes a prompt; Hooke can't start, steer or stop it, and
  can't show what it is doing.
- **What exists.** Codex, Claude Code and Grok Build each run headless from a documented interface on this Mac:
  `codex app-server` (JSON-RPC over stdio: threads, turns, steering, interrupts, approval requests), `claude -p` with
  stream-json in and out (the control protocol's permission prompts and interrupts), and `grok agent stdio` (the Agent
  Client Protocol, also spoken by Gemini CLI and others). They connect to Hooke through the door (ADR 0026) and work in
  Studies with assignments (`research/coordination.py`). T3 Code (MIT) drives the same programs the same way.
- **What it must not become.** A second way into Hooke with its own powers; a way for Hooke's words, or an agent's, to
  pass as the user's consent; a way to spend the user's money or subscription without limit; a hidden worker.

## Decision

- **Hooke starts only what the user installed and signed into.** The conductor (`services/orchestrator/agents/`)
  finds the program where its installer put it (`programs.py`) and runs it as the user, with the user's own sign-in:
  ChatGPT or an API key for Codex, the Claude subscription or key for Claude Code, Grok's own. Hooke never sees,
  stores or passes those credentials, and strips its own keys from the program's environment. Nothing is downloaded or
  installed by Hooke.
  - **Claude Code** runs as the installed `claude` program, never the Agent SDK, because Anthropic's terms keep
    subscriptions out of the SDK. It does not use `--bare`, which reads only an API key and never the user's sign-in.
    It runs with `--safe-mode` (none of the user's own hooks, plugins, CLAUDE.md or MCP servers) and
    `--strict-mcp-config`, Claude Code's sandbox on, permission prompts to Hooke over stdio, and
    `--max-budget-usd` from the launch's budget.
  - **Codex** runs in its own `workspace-write` sandbox with approvals on request, its default.
  - **Hooke's own command.** Both sandboxes block the loopback address the `hooke` command talks to (seen live,
    2026-11-03). That command reaches only Hooke's door, with this launch's own connection, so Claude Code is told to
    run that exact path outside its sandbox without asking, and may read Hooke's skill file. Codex has no such
    setting: it asks once to run `hooke` outside its sandbox, and the user's Allow for this session is Codex's own rule
    for that command. Nothing else is allowed in advance.
  - **ACP agents** (Grok Build in its default permission mode, Gemini CLI) get no file system, terminal or MCP servers
    from Hooke: they use their own, in their own folder.
- **A launch is asked for by the user, in Hooke.** The Agents panel's Start, or the brain's `start_agent`, which runs
  only when the user's own recent words ask for that agent (the same check as `set_research_cap`). The request is the
  consent for that one agent, in one Project, so there is no connect dialog: Hooke is the requester. Nothing an agent
  or a page says starts another agent; the door has no route that does.
- **Each launch gets what a connected agent gets, made for it alone:**
  - its own door connection (`Connections.issue`, kind and label "… (started by Hooke)", `launch` recorded), handed to
    that program only, in a mode-0600 file named by `HOOKE_CONNECTION_FILE` (the `hooke` command reads it instead of
    the Keychain and never connects again);
  - contribute access to one Project (`ProjectAccess.grant`, authority `launch`);
  - one Study and one assignment in it, proposed by the user ("You"), which the agent claims through the door like any
    connected agent. Its report comes back as the assignment's report and a Study claim by the agent, both awaiting the
    researcher's review: never an accepted finding.
- **A launch never outlives its program.** Stopping it, its failure, and Hooke quitting each end the program and
  everything it started (its own process group), delete its credential file, revoke its connection and withdraw its
  Project access. After a restart, a launch that was running is said as stopped, never as working, and its connection
  is revoked if it wasn't. Nothing reconnects it.
- **Hooke's words are marked as Hooke's.** Everything Hooke relays to an agent (the user's request, a note, a reply)
  starts with `[Hooke]`, and each agent's standing instructions say such a message is Hooke's relay, never the
  researcher's consent to anything. (Codex 0.163's app-server has no separate message kind for this, so the mark is in
  the text.)
- **Every ask goes to the user.** A command outside the sandbox, a file change, a tool use: each comes to the Agents
  panel with what it would do, and is answered only by the user's Allow, Allow for this session or Decline there.
  "For this session" is always the program's own session-scoped rule, never a change to the user's settings files.
  Neither the brain nor the voice answers an ask; the `agents` tool says it is waiting.
- **Spend counts.** What an agent reports spending counts against the day's research allowance (`DayLedger.others`);
  Claude Code is held to a budget that fits what is left, and refused when less than $0.25 is. Spend billed to an API
  key also goes into Spending against the monthly cap; a subscription's notional cost does not. Codex and ACP agents
  report tokens, not dollars, and the panel says so. At most four agents work at once.
- **Everyone sees them.** The Agents panel lists Hooke's researchers and outside agents together (working, needs you,
  done, failed), with a live transcript peek and a reply box; Activity has a line for each start, ask and end; the
  agents' words are shown and given to the brain as untrusted text.
- **Cloud later, behind the same interface.** `adapter.Adapter` is the seam (start, events, send, interrupt, answer,
  stop). A cloud adapter (Claude Managed Agents, as the private hosted service's research runtime already shapes it;
  Cursor's Cloud Agents API) implements it with its own transport; the conductor, the door connection, the Study
  assignment and the rules above don't change.

## What it protects, and what it does not

- **It protects against:** an agent starting another agent (no door route); a page starting one (the studio routes
  check the window's token in the Mac app, and the studio's Origin and header); Hooke's or an agent's text passing as
  the user's approval; a launch keeping access after it ends; spend past the day's allowance for programs that report
  it.
- **It does not protect against** what any program the user runs can do: a launched agent runs as the user, so its
  sandbox is the program's own (Codex's and Claude Code's), and a shell command the user allows reaches what the user
  can. ADR 0026's known limits (programs running as the user can read the local token and edit
  `agents/connections.json`) apply unchanged; so does "the studio's other HTTP routes trust its Origin and header" in
  a source checkout. A subscription's own rate limits, not Hooke, bound what a subscription spends.

## Cost

- **The user's sign-in is the program's.** If a program isn't installed or signed in, Hooke says how to do that and
  starts nothing.
- **Three adapters to keep current.** Each program's protocol changes; the adapters are tested against recorded
  streams (`services/tests/fixtures/agents/`), re-recorded when a program changes.
- **Two places change together** when a launch's connection changes: `door/connections.py` and the `hooke` command's
  `HOOKE_CONNECTION_FILE`.

**Related.** ADR 0006 (never claim what did not happen), ADR 0016 (one mind), ADR 0026 (one door). The design:
`services/orchestrator/agents/AGENTS.md`.
