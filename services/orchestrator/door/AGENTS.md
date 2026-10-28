# Outside agents: the one door

Part of the working context the root `AGENTS.md` maps. The decision is ADR 0026
(`docs/adr/0026-outside-agents-connect-through-one-door.md`). Read it before changing anything here.

A researcher's own coding agent (Claude Code, Codex, Cursor) uses Hooke through `/api/agent/v1/` on the orchestrator,
with no Hooke account. The `hooke` command in the app bundle, the Hooke skill and any later MCP adapter are thin
clients of this door. They hold no scientific logic and no list of capabilities: agents discover those here at run time.

## Files
- `connections.py`: pending requests, the researcher's answer, the one-time claim, tokens (hashes only, in
  `<data home>/agents/connections.json`, mode 0600) and Disconnect. An unreadable list refuses the door (503) and is
  never overwritten. The app still starts.
- `api.py`: the routes, the check every door request goes through (`guard`), the listing, status, and the studio's
  routes.
- `contract.py`: `describe`, a capability's shared shape from its catalog declaration, and `input_schema`. The hosted
  listing in the private platform is meant to call these same functions.
- `schema.py`: the shared JSON schemas in `contract/agent/` and a small checker. It needs no dependency, so the hosted
  side can use it too.

## Connecting
1. `POST /api/agent/v1/connect {label, kind, secretSha256}` returns 202 with `{pending: {id, code, expiresAt}}`.
   - The command prints the code; the Hooke window shows the same code.
   - At most 5 requests wait at once, each for 10 minutes.
   - Open studios get `{"type": "agent-connections", pending, connections}` over the WebSocket.
2. The researcher answers in Hooke: `POST /api/agent-connections/pending/{id} {answer: connect|decline}`. This takes
   the studio's Origin and `X-MicroscopeMind: agent-connections`.
   - The studio's side is `apps/studio/src/agent-connections.ts`. The confirm sheet opens for the first waiting
     request and names the agent "as it calls itself". It shows the code, says what connecting allows (including that
     what the agent reads may go to its provider), and offers Don't connect and Connect.
   - The sheet closes when the request is answered elsewhere or expires.
   - Settings → Connected agents lists live connections with Disconnect.
3. `POST /api/agent/v1/connect/{id}/claim {secret}`:
   - 202 while unanswered.
   - 403 `declined`, or `wrong-secret`.
   - 200 `{connection, token}` once. After that the request is gone (410).
4. Every other door route needs `Authorization: Bearer <token>`.
   - A revoked token gets 401 `disconnected`.
   - Disconnect is `POST /api/agent-connections/{id}/disconnect` from the studio.
   - Nothing reactivates a revoked connection. Connecting again makes a new `ac_` id after a new confirm.

## Every door request
- **Host:** must be a loopback name (`not-local`).
- **Origin:** a request with any Origin is refused (`browser-refused`).
- **Contract:** a `Hooke-Contract` header other than `1` is refused (`contract-version`).
- **Refusals:** each is `{ok: false, code, says, next}` (`contract/agent/refusal.schema.json`).

## What it answers today
- `GET status`:
  - the app's name, version (from `packaged.json`) and channel (`HOOKE_CHANNEL`, "development" when unset)
  - its revision and source digest
  - the interface version with the minimum CLI and skill
  - the connection, whether a studio is open, and the current session
- `GET tools?q=&page=&perPage=`:
  - Every catalog capability with a door entry (`kinds.door_entry`): what runs now (`kinds.available`), then reviewed
    entries that could run after setup, with their next step (`readiness.model`).
  - Community and personal entries are added through the Toolbox, and appear once they run.
  - The app's own built-in tools (`contract/capabilities.json`, e.g. Cellpose outlines through `outline_cells`) are not
    listed yet.

## Operations (`operations.py`, `example.py`, `actor.py`)
- **Starting work:** `POST operations {tool, args, requestKey}`.
  - The record is written (fsynced) before the work starts. A new operation answers 202 with `made: true`.
  - The same key with the same request answers 200 with the same operation (`made: false`).
  - The same key with any other request is 409 `request-key-conflict`. The key is bound to `request_digest` (tool,
    project, input revision, generation, parameters): the same meaning as the hosted service's.
- **Reading and stopping:** `GET operations/{id}`, `GET operations`, `POST operations/{id}/cancel`.
  - A cancel is `cancelRequested` until the work's own end says how it stopped.
  - After a restart, unfinished records are `interrupted` with what they say they left. Unreadable files are listed,
    never overwritten.
- **Tools:** this version runs one, `example`: the guided example (`onboarding.py` as it is) for the agent.
  - The steps and their progress: opening the image in its own local-only Project, then `basic-bright-objects` (no key,
    no download), then reading the table.
  - The outcome: the Project, the result id and revision, the count, the columns, a 5-row preview (`truncated`), whether
    Hooke showed it (`shownInHooke`), and the `page` to open.
  - Open windows get `onboarding-changed`, so the walkthrough card catches up (`onboarding.ts` `agentAdvanced`).
- **Who did it:** the operation sets `actor.ACTOR`.
  - `commands.run_tool` and `operations._actor_now` record it, so the execution's and the result's `madeBy.actor` is
    `agent:<id>`.
  - Data's maker is the agent's label (`resources.maker_of`), with "a:<label>" in Made by (`data-roles.ts`).
  - The app's own example run by a person records no actor today: that is unchanged here.
- **Opening a page:** `POST open {page}` shows a page in the Hooke window (only `#measurements`, `#models`,
  `#toolbox`, `#assets`, `#sessions`).
  - It answers `shown: true` only when a window says it shows it; otherwise it says why.

## Still to build (ADR 0026)
- **More tools on the same operations:** run a capability on a file, results (with the full table only when asked,
  marked), compare, batch, add capabilities.
- **Built-in tools:** list the app's own built-in tools alongside catalog entries.
- **The window comes forward:** the Mac app should bring Hooke forward when a request waits.
- **WebSocket:** a token for WS clients that send no Origin (`origin.allowed_origin`).
