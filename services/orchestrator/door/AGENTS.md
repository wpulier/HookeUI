# Outside agents: the one door

With opt-in Project routing, new Project-scoped operations keep their authoritative
receipt in `history/operations`, under the captured origin and host writer lease.
Legacy receipts remain in host storage until explicit collection. Loading retained
receipts never imports credentials/grants or launches work. Duplicate IDs/request
identities refuse before recovery writes. An unavailable writer leaves the original
receipt unchanged and exposes `storageReadOnly`: its recorded running state is not
observed execution here, and cancellation is refused. WorkflowService still owns
workflow execution. Failed admission sync retries reuse the same reserved ID.

Part of the working context the root `AGENTS.md` maps. The decision is ADR 0026
(`docs/adr/0026-outside-agents-connect-through-one-door.md`). Read it before changing anything here.
To try it by hand, from the published Beta or a `dev` checkout, follow `docs/testing/agent-connection.md`.

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
2. The researcher answers in Hooke: `POST /api/agent-connections/pending/{id} {answer: connect|decline, signature}`.
   - The answer counts only when signed by the Mac app's native dialog with this launch's key (`native.py`;
     `packaging/AGENTS.md`). The signed message is `hooke-agent-connect`, the request id, the code and the answer.
   - The studio's Origin and header, which any local program can copy, are not enough.
   - Without the app's key (a source checkout), the studio's own answer counts only with `HOOKE_DEV_CONFIRM=1`.
   - The studio's side is `apps/studio/src/agent-connections.ts`. In the Mac app it hands the request to the native
     dialog (`hookeConnect`) and shows nothing of its own. In a browser its confirm sheet opens for the first waiting
     request and names the agent "as it calls itself". It shows the code, says what connecting allows (including that
     what the agent reads may go to its provider), and offers Don't connect and Connect.
   - The sheet closes when the request is answered elsewhere or expires.
   - Settings → Connected agents lists live connections with Disconnect. Studio connection snapshots also identify
     the channel, so its setup-copy control never directs a development checkout to an installed Stable/Beta app.
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
- `GET tools?q=&page=&perPage=`: **what an agent may run here** (`listing.py`). The door names no capability.
  - **What's in it:** the one list's agent rows (`App.capabilities_wire()`: `contract/capabilities.json` and the
    catalog's proven entries, `catalog:<key>`), limited to tools declared runnable for agents in
    `orchestrator/agent_runnable.py`. The default is no, and a test fails on any undeclared tool. A tool that changes a
    setting never shows (`Tool.sets`).
  - **How each is described:** from its own declaration (a tool's parameter schema and `produces`, or a catalog
    manifest), with `runsOn` (this Mac or a cloud service, read from the models its roles use now) and `cost`.
  - **State:** cloud work is `unavailable` with `LOCAL_ONLY`. The rows are gathered on the event loop and described in
    a thread.

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
  - Request keys are each connection's own. Project reads and controls also honor the explicit grants described below.
- **What can be started:** `tool` is a capability id of the listing, or one of the door's own actions
  (`DOOR_ACTIONS`):
  - **`open {file}`** (`work.open_file`): the agent's file goes to the library through the camera bridge's own upload,
    then opens on the stage of a new Project of the agent's own (`agents/projects.json`).
  - **Any listed capability** (`work.run_capability`):
    - **Where:** only in a Project the calling connection made (`project`, from `open`), and only while it is open
      (`own_project`). Otherwise it is refused: `project-required`, `not-your-project` or `project-not-open`.
    - **Arguments:** exactly the ones its `inputSchema` offers (`bad-arguments`). None may name another session, a
      conversation or a researcher, and none may name a cloud model (`cloud-argument`, from `listing.model_places`).
    - **Local only:** the run is held to local work (`api_usage.local_only`), so no paid provider and no cloud model
      runs, whatever a fallback chooses.
    - **Catalog rows:** only reviewed entries (`agent_runnable.catalog_runnable`).
    - **How it runs:** by its own tool through `commands.run_tool`, as the agent. It doesn't go through the Actions
      path, whose runs read "You".
    - **Outcome:** when it measured something, a table preview and the page.
    - **Retries:** a retried request key returns its operation before anything else is checked.
  - **`example`:** the guided example (`onboarding.py` as it is).
    - Its steps: opening the bundled image in its own local-only Project, finding its bright regions (the example's own
      capability: no key, no download), then reading the table.
    - Its outcome: the Project, the result id and revision, the count, the columns, a 5-row preview (`truncated`),
      whether Hooke showed it (`shownInHooke`), and the `page` to open.
    - Open windows get `onboarding-changed`, so the walkthrough card catches up (`onboarding.ts` `agentAdvanced`).
- **Who did it:** the operation sets `actor.ACTOR`.
  - `commands.run_tool` and `operations._actor_now` record it, so the execution's and the result's `madeBy.actor` is
    `agent:<id>`.
  - Data's maker is the agent's label (`resources.maker_of`), with "a:<label>" in Made by (`data-roles.ts`).
  - The app's own example run by a person records no actor today: that is unchanged here.
- **Opening a page:** `POST open {page}` shows a page in the Hooke window (only `#measurements`, `#models`,
  `#toolbox`, `#assets`, `#sessions`).
  - It answers `shown: true` only when a window says it shows it; otherwise it says why.

## The command and the app
The `hooke` command, the app's state file, its links and attention are described in `packaging/AGENTS.md` ("Outside
agents"). The skill agents get is `.agents/skills/hooke/SKILL.md`.

## Sockets and the screen
- **WebSockets** (`origin.socket_kind`, both `/ws` and `/ws/voice`):
  - In the Mac app only the window's studio token makes a socket the studio's.
  - In a source checkout's browser a loopback Origin does.
  - Scripts present `Authorization: Bearer <data home>/agents/local-token` (`origin.script_headers`).
  - Anything else is refused. `status.studio.open` counts only studio sockets.
- **The example never takes over what the researcher sees** (`example.takeover`). It is refused (409 `hooke-busy`)
  while Talk is live, or while a Hooke window shows a Project that isn't empty (nothing opened, made or asked) and
  isn't the example's own. It is checked at the door and again as it starts.
- **Work Hooke stopped is interrupted, not cancelled:** only a cancel someone asked for is `cancelled`.

## Still to build (ADR 0026)
- **Live-stage runs** still require the creator's open Project. The saved-input workflow path below captures retained inputs independently; actual off-screen qualification is separate from source/tests.
- **Full results:** full tables only when asked for, and marked.
- **The studio's other HTTP routes** still trust its Origin and header, which a local program can copy (ADR 0026
  says so).

## Retained research Projects (connector integration)

`projects.py` is the shared Project access check. The existing creator registry is
preserved; owner-confirmed `read`/`contribute` grants add collaboration. Unreadable
permissions refuse rather than becoming an empty registry. Revocation retains
history. Credentials and assignment identities are distinct from display names.

`project_actions.py` describes general actions declared in
`agent_runnable.DOOR_RESOURCES`: create/list/read a Project, read its state, import an explicit local
file, and read exact artifact revisions with bounded text. `project.state {cursor?, limit?}` (read access) answers
`{state}`: the Project as every reader sees it (`orchestrator/project_state.py`, `contract/agent/project-state.schema.json`,
`docs/architecture/session-index.md`), checked against its schema before it is returned; the actor's access is checked
before anything is read or cached, and its cache is the actor's own. A cursor from a Project whose data changed since is
refused (409 `project-state-changed`); the studio's state changing does not refuse it. `project.read`'s output is unchanged. They appear in tools
without new CLI-specific scientific logic. Creation uses `SessionStore.create_retained`
without changing the active Project, stage or resume preference. Intake reuses
FileIntake, memory admission, hashes and immutable chunks. Each imported document retains its source-import actor, label and operation ID,
so provenance survives independently of the operation listing. Results retain operation
attribution. Live-stage capabilities retain their creator/open-Project restrictions.

`studies.py` scopes Study reads, evidence, claims, controls, workflow associations
and file downloads to the authenticated connection's Project permission. Definitions
are associated with selected Projects; cross-Project discovery is refused. Authoring
creates frozen definitions; execution uses a Study operation and WorkflowService's
original attempts. Agent review never supplies human acceptance. Shared operation
reads retain the original author; request keys remain per connection. A second agent
reads an existing operation by ID, rather than reusing another agent's request key.

The existing `workflow` admission appears in tools discovery when `workflow.write`
is declared. Its shared `workflow-admission` schema also validates the request.
Discovery describes the entrance, not a ready scientific run or Project grant:
contribute permission, exact inputs and frozen-step readiness remain admission checks.

Settings → Connected agents → Project access is the owner's source-Studio grant
interface. Source Studio requires explicit `HOOKE_DEV_CONFIRM=1`. A packaged window
uses `hookeProjectGrant`: the server prepares a bounded, expiring one-use choice,
the native dialog displays its canonical Project and stable connection ID, and
signs exactly that choice with this launch's key. Only a verified signature grants
or revokes access; replay, changed roles, expired choices and revoked connections
refuse. Preparation alone changes no authority. A bearer token is never sufficient
to grant. Packaged interactive qualification is recorded separately from unit tests. This development boundary, like source
connection confirmation, does not isolate against an agent with unrestricted shell.

The thin CLI's `api GET|POST <relative-path> [json]` and
`download <relative-path> --output <new-file>` stay inside `/api/agent/v1/` and use the
selected Keychain connection. Downloads stream to a new file and never overwrite an
existing output. `artifact.read` returns the download locator and exact retained
metadata. File bytes require Project authorization, rechecked during streaming.


Owner access settings list retained UI-created and agent-created Projects from the
canonical session catalogue. Unshared user Projects are not added to the agent registry
until an explicit owner-confirmed grant succeeds; their creator is recorded as `user`.
Agents do not see the owner's catalogue. Revocation preserves the Project and history.
Preparation and confirmation both require a real retained Project; neither opens it on stage.

The owner catalogue and grant preparation require the launch's Studio token in the
packaged app, rejecting bearer credentials and unsigned requests. Source development
requires explicit developer confirmation mode plus its Studio origin/referrer and
header; this is not an OS-level isolation boundary against local shell access.
Catalogue reads and complete synchronous grant operations run off the event loop.

## Study correspondence

Status advertises `interface.studyQuestions`. `GET studies/{id}/questions` lists saved
questions; `GET .../questions/{question}` returns the pinned substantive Study revision,
including earlier claims, failures' operation locators and exact inputs. Existing artifact
and operation reads supply detailed evidence. `POST .../questions` takes session, request,
question, basis (artifact-reference), recipient (stable agent actor). `POST .../{question}/answer`
takes session, statement, evidence (exact artifact references), limitations. The selected
contributing connection alone may answer. Stable caller attribution is server-derived.
Both resources are deliberately declared in agent_runnable; authorization and revocation
are checked on every call. Questions never launch agents or grant new access. Polling agents
use their own runtime and subscription; the UI never reports them running merely because
a question waits. Shared schemas: study-question and study-answer.

`material.organize` is a default-deny declared Project contribution. It takes an exact
artifact reference, a group (`role`, up to 80 characters), `input` boolean and expected membership revision (0 when absent). The
server derives the organizing actor from the connection, checks contribute authority
at admission and publication, and publishes a separate membership record, preserving source bytes, producing context and every
historical revision. Historical Study pins can be organized without advancing the source head. The shared Studio endpoint invokes the same metadata service;
request identity and revision conflict checks prevent duplicate or stale acceptance.

`project.storage` is a read-authorized metadata action using the shared
`project-storage` contract. It returns the durable origin, legacy versus routed
folder storage, known local location and observed owner availability. It takes no
writer lease and never moves, collects or creates missing research. A missing or
mismatched registered folder stays unavailable; legacy stores are not presented as
portable folders. Collection remains explicitly not assessed. Hosted callers that
cannot inspect local storage use the same unavailable meaning, never a fabricated
local path or completeness claim.

`project.dependencies` is a read-authorized, discoverable inspection on either
legacy or routed Project records. It captures the accepted heads, reads every
historical revision through the captured store, and returns the shared
`project-dependencies` contract. External references are reported without opening
their owners. Changing heads/membership, revocation or cancellation stops the
review rather than returning a mixed basis. Its tracked receipt is not a complete
collection certificate, a migration, a grant or an executable workflow. Hosts
without local storage use explicit unavailability; the CLI has no special path.

The local project.dependencies receipt includes sourceReview from the captured
Project journal and accepted media bindings. The read grant and cancellation guard
cover both scans. It accepts no caller path and never consults another host's Library.
Omission on an unavailable host is not a complete-collection claim.

Live-stage operation admission includes the exact retained media reference in
inputRevision when present, preserving the legacy asset identity otherwise.
Execution rechecks Project ownership and that revision after publishing progress,
before tool admission. Reusing a Library alias for another revision cannot silently
retarget queued work. The scientific execution retains its original input evidence.
