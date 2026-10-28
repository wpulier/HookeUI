# 26. An outside agent connects once, through one door, as itself

**Context.**
- **What researchers want.** Researchers already work with a coding agent of their own (Claude Code, Codex, Cursor).
  They want that agent to use Hooke: see their capabilities, add community ones, run them on their files and keep the
  results in a project, with no Hooke account.
- **Nothing allowed it.** No interface for an agent outside the app existed. HTTP writes require the studio's own
  Origin (`origin.studio_origin`). The WebSocket accepted any local process that sent no Origin, with no way to tell
  who it was.
- **What it must not become.** It must not be a second implementation of Hooke, with its own list of what Hooke can do
  that drifts from the app.

**Decision.**
- **One door: `/api/agent/v1/`.** Outside agents reach Hooke only through these routes on the orchestrator. The
  `hooke` command in the app bundle, the Hooke skill and any later MCP adapter are thin clients of this door. None of
  them contains scientific logic or a fixed list of capabilities.
- **A connection is made once, deliberately:**
  1. `hooke connect` generates a one-time secret. It asks for a pending connection with the secret's SHA-256, a label
     ("Claude Code") and a kind.
  2. The researcher answers in a native dialog of the Hooke Mac app, not in the web page. The dialog shows the label, a
     short matching code (also printed by the CLI) and what the connection gives: local projects, files and results.
     It also says what it doesn't give, that the agent may send what it reads to its own provider, and that an agent
     running commands on the Mac already reaches what any program can.
  3. The answer counts only when signed by this launch's window.
     - The Mac app makes a P-256 key pair in memory at each launch, never written down.
     - It gives the server only the public half, in the environment it starts the server with
       (`HOOKE_CONFIRM_KEY`).
     - It signs `hooke-agent-connect`, the request id, its code and the answer (`door/native.py`).

     The studio's Origin and header, which any local program can copy, are not enough. A source checkout without the
     Mac app has no key: answering there takes `HOOKE_DEV_CONFIRM=1`, set knowingly by a developer.
  4. The app hands the token only to a request presenting the secret whose hash it was given, after the confirm. It
     does this only once. The CLI stores the token in the Keychain. The app keeps only the token's SHA-256.
- **Each connection has a stable id (`ac_…`).** It is separate from its label. A label is a label, shown "as it names
  itself", never a verified identity. Every effect the agent causes is recorded with actor `agent:<id>`, shown by its
  label. Nothing an agent does is recorded as the researcher's.
- **Disconnect is final for that connection.** Disconnecting in Hooke revokes it at once. The next call is refused
  with "disconnected in Hooke". Only a new `hooke connect` with a new confirm restores access, and nothing reconnects
  on its own. Refreshing the Hooke skill (`hooke skill update`) touches no connection.
- **What a connection may do.** It may list what an agent may run here, run it in a Project of its own, and open its
  own pictures and videos.
  - **The listing** (`door/listing.py`) names no capability. It is whatever this researcher's Hooke has, built in or
    added, each described from its own declaration.
  - **Default no:** a tool is offered only if `orchestrator/agent_runnable.py` declares it runnable for agents: it
    makes scientific data in the agent's Project, or reads it. Settings, navigation between Projects, what a window
    presents, researchers, conversations, the researcher's other Projects and downloads are declared not runnable. A
    test fails on any undeclared tool.
  - **Local only.** The door never authorizes paid work by itself:
    - **Up front:** where each capability runs is read from the models it runs with now (`runsOn`; a model the licence
      table doesn't know counts as cloud). A cloud capability is listed but refused, and so is any argument naming a
      cloud model (`work.check_models`).
    - **While it runs:** every agent run is held to local work (`api_usage.local_only`), so no paid provider and no
      cloud model is reached, whatever a fallback chooses.
  - **Catalog entries:** only entries of the reviewed catalog are runnable for agents. A community channel's or the
    researcher's own entry is not (`agent_runnable.catalog_runnable`).
  - **Arguments stay in the agent's Project:** none may name another session, a conversation or a researcher.
  - **Operations are each connection's own:** request keys, reading, listing and cancelling.
  - **Only the agent's own Project.** `open` adds the agent's picture or video to the library and opens it in a new
    Project recorded as that connection's. `run` names that Project by id and runs only while it is the open one,
    never in the researcher's Projects or in whatever happens to be open. It runs by the capability's own tool, as the
    agent, with exactly the arguments its schema offers.
  - **What the door has no route to:** API keys, settings and other channels. An agent that also runs commands on the
    Mac has the researcher's own access to their files, as any program they run does; `open` adds nothing it couldn't
    already read.
- **Every door request is checked:**
  - The orchestrator listens on 127.0.0.1 only.
  - The Host header must name a loopback host (DNS rebinding).
  - Any request carrying an Origin is refused, because browsers send one and the door is not for pages.
  - Effect and read routes need the bearer token of a live connection.
  - Asking for a pending connection is the only unauthenticated write. It does nothing until confirmed, is limited in
    number and expires after 10 minutes.
- **Answers carry no image bytes or full tables by default.** An outside agent may send what it reads to its cloud
  provider, so the door returns ids, paths, counts, small previews and summaries unless the call asks for more, and
  says when it gave more.
- **One contract.** The meanings shared with the hosted service are written as JSON schemas in `contract/agent/`:
  capability, generation, request key, operation state and refusal. The local door is tested against them. The private
  hosted service tests its answers against the same files, pinned through its core lock. The two implementations stay
  apart; the words mean the same.
- **Long work is an operation.** Every effect call carries a caller request key:
  - **The key is bound to its request.** It is bound to a digest of the tool, project, input revision, generation and
    parameters. The same key with the same request returns the same operation. The same key with anything else is a
    conflict.
  - **Admission is written to disk before any work starts.** An operation names its exact project, input revision,
    capability generation and parameters, so a later change of what the studio shows never changes it.
  - **Restart reports what survived.** After a restart, completed results are kept, and interrupted work is reported
    as `interrupted` with what it left. Work resumes only where an operation declares that it can.
  - **Cancel is reported, not assumed.** A cancel is `cancelRequested` until execution confirms it stopped.

- **WebSockets.** A local program can send any Origin header, so an Origin alone doesn't make a socket the studio's
  (`origin.socket_kind`):
  - **In the Mac app:** the app gives its window a per-launch studio token, in the server's environment and injected
    into the WKWebView, never served over HTTP. A socket is the studio's only when it presents that token.
  - **In a source checkout's browser:** the studio has no token, and a loopback Origin still marks it.
  - **Scripts** (the bench, the sim, replays) present the data home's local token (`agents/local-token`, mode 0600).
    Anything else is refused.
  - The door's `status` counts only the studio's sockets as an open studio.

**What it protects, and what it does not.**
- **It protects against:**
  - web pages (Origin and Host checks);
  - other users of the Mac (loopback and mode-0600 files);
  - agents that were disconnected;
  - local programs that copy the studio's Origin and header to answer a connection request. The answer must be signed
    by the Mac app's window.
- **The socket tokens stop browsers and other users, not programs running as the researcher.** In the Mac app, the
  studio token keeps a page or a copied Origin from passing as the studio. But the local token is a mode-0600 file,
  and any program running as the researcher can read it and open a script's socket, which can do what the studio can.
- **It does not protect against software that already acts with the researcher's full access.** That includes
  software that drives the Mac's interface with Accessibility permission (it could click the dialog), and software
  that reads other processes' environment or memory. Such software can read the researcher's files without any door,
  and no local design changes that.
- **Known limits, accepted (Will, 2026-10-28):**
  - Programs running as the researcher can read the local token, and can edit `agents/connections.json`. That could
    give them a working token, or keep a disconnected connection alive after a restart.
  - The studio's other HTTP routes (Disconnect among them) still trust its Origin and header, which a local program
    can copy. That predates this record, which doesn't make those routes stronger.
- **A known limit for decision:** with no Hooke window open, `open` (like the example) switches the app's open Project
  to the agent's own. A window opened later shows that Project, with its name and the agent's work in it.

**Cost.**
- **One confirm click.** A researcher clicks once per agent, in Hooke's native dialog, and a CLI is shipped in the app
  bundle.
- **Development needs a flag.** A source checkout can confirm only with `HOOKE_DEV_CONFIRM=1`.
- **Scripts need a token.** Local scripts present the local token. A browser opened on the Mac app's address has no
  studio token, and its sockets are refused.
- **Two places change together.** A change to a shared meaning is a change to `contract/agent/` and to both
  implementations' tests.

**Related.** ADR 0006 (never claim what did not happen), ADR 0021 (added code runs only when activated), ADR 0024 (one
capability contract). The design: `services/orchestrator/door/AGENTS.md`.


## October 28 amendment — durable collaborative research (approved)

The owner approved the following extension of this decision. Earlier implementation descriptions
above remain historical; each extension is exposed only once implemented and qualified.

- Saved-input scientific operations may execute off-screen against an explicit Project, immutable
  input revision, method generation and parameters. Live stage/camera operations retain their
  existing active-session rules. Opening a result remains an explicit presentation action.
- Connections may be named independently within an agent kind and channel. The unnamed Keychain
  account remains compatible. Names select credentials; server connection IDs own attribution.
  Two connections of the same kind must coexist and be independently revocable. A display label
  never establishes identity or permission.
- Agent-created Projects retain their creator. The owner's trusted interface may explicitly grant
  another connection read or contribute access to a Project. Contributor access admits scientific
  work and publishes findings; it never grants membership administration, keys, settings or spending.
  Reads, imports, Studies, workflows, operations and exports enforce the same Project boundary.
- Every new scientific door action remains default-deny with a reason and boundary tests in the
  shared agent-runnable declarations. CLI and skill remain thin discovery clients.
- Project evidence and exact revisions are shared scientific state. Study assignments refer to
  WorkflowService executions; they do not create another execution/attempt ledger. Actor kind,
  stable connection/researcher/person identity and assignment identity remain distinguishable.
- External agents are the first research entrance. Launching subscribed agent CLIs from Hooke is
  deferred pending separate supported-use, consent and resource-control qualification.
- A shell-capable program acting as this Mac's user can read that user's files. Project permissions
  cannot establish a sealed local scientific holdout; labels must be outside candidate agents'
  actual reach before claiming isolation.

Acceptance requires real supported connections, scoped collaboration, retained provenance,
UI-independent saved-input execution, recoverable outcomes and ordinary UI inspection. Fixture
connection issuance and direct writes into Project storage are test evidence only, never proof of
that complete user journey. This amendment does not itself claim shipped behavior.


### Owner confirmation for Project collaboration

Packaged Project grants use the existing launch-scoped native signing authority.
The server supplies a canonical Project, stable connection and exact role in a
five-minute, one-use choice. The native sheet shows those values with Cancel as
the default; its signature binds the choice ID, Project, connection and role.
Unsigned requests, replay, expired choices, changed authority and revoked
connections refuse. Preparing a choice is not authorization. Source development
keeps its explicit development-only owner boundary. Neither route is agent-token
administration. Signing/transport tests and actual packaged-dialog qualification
remain separate evidence classes.


### Owner-selected Projects created in the UI

The same explicit read/contribute grant applies to a retained Project created by a
person. Owner access settings list the canonical Project catalogue; listing and
preparing native confirmation grant nothing. Only successful owner confirmation
registers an unshared Project with `by: user` in the permission registry. This denotes
local-owner authority, not a reconstruction of the Project's historical creator;
existing journal authorship is unchanged.
Agents still discover only their own or explicitly granted Projects. No new agent
administration route, automatic sharing or replacement Project is introduced.
Revocation removes that connection's authority and retains prior authorship. Missing
Projects and invalid IDs refuse before permission records are written.
