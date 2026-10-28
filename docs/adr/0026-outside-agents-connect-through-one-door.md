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
  2. The researcher confirms the pending connection in the Hooke window. The confirm shows the label, a short matching
     code (also printed by the CLI) and what the access includes: local projects, files and results. It also says
     that the agent may send what it reads to its own provider.
  3. The app hands the token only to a request presenting the secret whose hash it was given, after the confirm. It
     does this only once. The CLI stores the token in the Keychain. The app keeps only the token's SHA-256.
- **Each connection has a stable id (`ac_…`).** It is separate from its label. A label is a label, shown "as it names
  itself", never a verified identity. Every effect the agent causes is recorded with actor `agent:<id>`, shown by its
  label. Nothing an agent does is recorded as the researcher's.
- **Disconnect is final for that connection.** Disconnecting in Hooke revokes it at once. The next call is refused
  with "disconnected in Hooke". Only a new `hooke connect` with a new confirm restores access, and nothing reconnects
  on its own. Refreshing the Hooke skill (`hooke skill update`) touches no connection.
- **What a connection may do.** It may discover capabilities and read and work in local projects. It never reads or
  writes keys or settings, never deletes and never reaches another channel. Each channel (Stable, Beta) keeps its own
  data home and so its own connections. Trusting a community channel (unreviewed code) still takes a click in Hooke
  (ADR 0021).
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

**What it protects, and what it does not.**
- **It protects against:** web pages (Origin and Host checks), other users of the Mac (loopback and the data home's
  permissions), agents that were disconnected, and anonymous local scripts that never had a connection confirmed.
- **It does not protect against** software already running as the researcher. Such software can read their files
  without any door, and no local design changes that.

**Cost.**
- **One confirm click.** A researcher clicks once per agent, in Hooke, and a CLI is shipped in the app bundle.
- **Scripts need a token.** Local scripts that used the open WebSocket move to a token (a later step of this work), and
  the WebSocket stops accepting a missing Origin without one.
- **Two places change together.** A change to a shared meaning is a change to `contract/agent/` and to both
  implementations' tests.

**Related.** ADR 0006 (never claim what did not happen), ADR 0021 (added code runs only when activated), ADR 0024 (one
capability contract). The design: `services/orchestrator/door/AGENTS.md`.
