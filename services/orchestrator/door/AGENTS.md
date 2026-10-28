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

## Still to build (ADR 0026)
- **Operations:**
  - request keys bound to a digest of tool, project, input revision, generation and parameters (409 on a mismatch)
  - admission written before work starts
  - `operation`, `operations`, `cancel` (`cancelRequested` until confirmed), and `interrupted` after a restart
- **Actor:** effects recorded with actor `agent:<id>`, through `commands.run_tool` (today `actor="you"`), evidence and
  provenance.
- **The window comes forward:** the Mac app should bring Hooke forward when a request waits, so the researcher
  sees the sheet.
- **Tools:** `example` (the onboarding flow), `open_in_hooke`, then run, results, compare, batch and adding
  capabilities.
- **Responses:** no image bytes or full tables unless asked, and marked when given.
- **WebSocket:** a token for WS clients that send no Origin (`origin.allowed_origin`).
