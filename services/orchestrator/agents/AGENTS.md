# Outside agents Hooke starts: the conductor

Part of the working context the root `AGENTS.md` maps. The decision is ADR 0029
(`docs/adr/0029-hooke-starts-outside-agents-on-this-mac.md`); the door these agents work through is ADR 0026
(`services/orchestrator/door/AGENTS.md`). Read both before changing anything here.

The user asks Hooke for an outside agent ("ask Codex to write a stomata-tracking capability"), by voice or chat
(`brain/toolbox/agents.py`: `start_agent`, `agents`, `agent_note`, `stop_agent`) or in the studio's Agents panel
(`apps/studio/src/agents-panel.ts`). Hooke starts the program the user installed and signed into, on this Mac, gives it
its own door connection, Project access and Study assignment, shows what it does, routes every ask to the user, and ends
it when asked.

## Files
- `adapter.py`: the one interface (`start`, `events`, `send`, `interrupt`, `answer`, `stop`) and the events every
  adapter says (`session`, `turn`, `text`, `delta`, `tool`, `permission`, `resolved`, `usage`, `warning`, `exit`).
  `marked` puts `[Hooke]` in front of everything Hooke relays. A cloud adapter later implements the same class.
- `codex.py`: `codex app-server` over stdio JSON-RPC. `thread/start` (workspace-write sandbox, approvals on request,
  the standing instructions as developer text), `turn/start`, `turn/steer` while a turn runs, `turn/interrupt`. Asks:
  `item/commandExecution/requestApproval`, `item/fileChange/requestApproval`, `item/permissions/requestApproval` and the
  legacy `execCommandApproval`/`applyPatchApproval`. "For this session" on a command is `acceptForSession` (see
  `claude.py` below for why Hooke's own command matters). Anything else Codex asks (questions, elicitations, dynamic tools, sign-in
  refreshes) is refused as not Hooke's to answer.
- `claude.py`: the installed `claude -p`, stream-json in and out, `--permission-prompt-tool stdio`, `--safe-mode`,
  `--strict-mcp-config`, the sandbox on, `--max-budget-usd=…`, values always in `--flag=value` form. Only Hooke's own
  `hooke` command (that exact path) runs outside the sandbox without an ask, and Hooke's skill file is readable
  (`settings`): both sandboxes block the loopback address the command needs. Codex asks instead; after the user allows
  a `hooke` command for the session, later plain `hooke` commands are accepted for the session as their answer
  (`Codex._plain_hooke`: no shell operators outside single quotes). "For this session" is always `acceptForSession`,
  never `acceptWithExecpolicyAmendment`, which Codex keeps for good in the user's own `~/.codex/rules`. Never `--bare`
  (it reads only an API key, never the user's sign-in). The control protocol's `initialize`, `can_use_tool` (asks),
  `interrupt`; a message while a turn runs steers it (`"priority": "now"`: the segment ends `aborted_streaming` and
  the turn carries on). Its `total_cost_usd` is cumulative for the program; `apiKeySource: none` is a subscription.
- `acp.py`: one ACP client (protocol 1) for Grok Build (`grok --permission-mode default agent stdio`), Gemini CLI
  (`gemini --acp`) and others. No file system, terminal or MCP servers offered. A message while a turn runs cancels
  the prompt and is sent as the next one in the same session (T3 Code's way; ACP has no steering). Interrupt is
  `session/cancel`, which answers any waiting ask `cancelled`. An agent that wants signing in is said as that
  (`SignInNeeded`).
- `jsonrpc.py`, `transport.py`: line-delimited JSON over the program's stdio, read and written on threads, never on
  the event loop. Each program is a child of its own process group (`owned_process.py`), so Stop ends everything it
  started. `child_env` strips Hooke's own variables, provider keys and a parent Claude Code session's markers.
- `programs.py`: which programs are installed (the user's PATH and the usual install folders), and how to install
  and sign in when one isn't. Tests never find any (`MICROMIND_AGENT_PROGRAMS=none`, conftest).
- `conductor.tidy`: an agent's line as a person reads it (Hooke's command as `hooke`, its folder as "its folder",
  no shell wrapper), in the transcript and the asks. `summary` is "what's everyone doing" in a few words each.
- `brief.py`: the standing instructions and the first message: the researcher's words as Hooke relayed them, the
  Project, Study and assignment, the `hooke` command, and the steps (claim, work, keep a report, complete, claim).
- `conductor.py`: the launches. Starting checks, then makes the connection, the credential file, the Study and the
  assignment, then starts the adapter in the background. It holds each launch's status (`working`, `needs-you`, `done`,
  `failed`, `stopped`), its transcript (300 entries kept; each list message carries six), its asks and its spend; says
  them to the studios (`{"type": "agents"}`, at most five times a second) and in Activity; and keeps the list in
  `<data home>/agents/launches.json` (mode 0600). After a restart a launch that was running is stopped, and its
  connection is revoked.
- `hooke_side.py`: what a launch needs from the rest of Hooke, in one class tests replace: the `hooke` command (the
  Mac app's `Contents/Helpers/hooke`, `HOOKE_CLI`, or one built here with `swiftc` into `<data home>/agents/bin`), the
  door (`Connections.issue`, `ProjectAccess.grant(authority="launch")`), the Study and assignment (proposed as "You":
  the user asked), what the Study says was returned (`observe`, never what the agent said), the day's allowance, and
  Activity.
- `api.py`: the studio's routes, `GET/POST /api/agents`, `GET /api/agents/{id}` (the whole transcript),
  `POST /api/agents/{id}/note | interrupt | answer | stop`. Only the Hooke window: a loopback Host, no
  `Authorization`, the studio's Origin and `X-MicroscopeMind: agents` for writes, and in the Mac app the window's
  `X-Hooke-Studio` token for every call. None of these is under `/api/agent/v1/`: an outside agent can never start,
  steer, answer for or stop another.

## Rules
- **Only the user starts one**, in the Agents panel or by asking (`start_agent` checks the user's own recent words).
- **Only the user answers an ask**: Allow, Allow for this session or Decline on its approval card (over the composer)
  or its row (`POST …/answer`), or their own spoken yes or no through `answer_agent`, which needs their words quoted
  (`quoted`, as an experiment's Run it). Hooke never answers on its own judgement, and a note never answers one.
- **The voice says it once**: an ask, a finish and a failure reach the voice through the narrator (`HookeSide.say`,
  `notify`), each with a `still` check, so an ask already answered or a turn already followed is told, never said.
- **Hooke's words are Hooke's**: everything relayed starts with `[Hooke]`; the instructions say it is never consent.
- **Admission is serialized**: capture the requested Project before discovery, then check capacity and reserve the
  allowance in one admission. Switching Projects while discovery runs never changes the launch’s Project.
- **It ends whole**: program, process group, credential file, connection and Project access, on Stop, failure,
  Hooke quitting, and at the next start after a crash.
- **Spend counts**: reported spend joins the day's allowance (`research/ledger.py` `DayLedger.others`); Claude Code's
  budget fits what is left (at most $3, refused under $0.25) and is held while it runs; only API-key billing goes into
  Spending. At most four work at once, six programs alive (a finished one is ended to make room).
- **Say only what happened**: a launch is `done` when its turn completed; whether its report came back is read from
  the Study. A restart never shows a launch as working.

## Tests
- `tests/test_agents_adapters.py`: each adapter against what the real program said (`tests/fixtures/agents/`,
  `tests/agent_streams.py`): Codex 0.163, Claude Code 2.1.310, Grok Build 1.0.41 (recorded by T3 Code, MIT), Gemini CLI
  0.65's refusal. Re-record when a program's protocol changes (the README there says how they were made).
- `tests/test_agents_conductor.py`: the rules, with a scripted adapter and a stand-in Hooke side.
- `tests/test_agents_hooke_side.py`: the real door and Study storage: a launch's connection claims, keeps a report,
  returns it and publishes a claim; ending it revokes.
- `tests/test_agents_api.py`: only the window reaches the routes.
- A live run with the real programs is evidence of its own (the PR says which programs, versions and steps); unit tests
  with recordings are not.
