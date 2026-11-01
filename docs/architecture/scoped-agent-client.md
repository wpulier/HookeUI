# Project-bound external research clients

The Project remains the home of research. A fresh external researcher must be able
to recover its assignment and exact evidence without inheriting unrelated host or
Project access. The existing CLI intentionally lets an authorized local agent
import a host path; hiding the agent's shell does not remove that indirect route.

The first bounded implementation is an optional **read client**, in
`services/agent_client`. An operator chooses an existing named connection, Project,
optional Study, CLI executable and loopback door before starting its stdio server.
The official MCP Python SDK owns the protocol. The existing CLI owns Keychain and
door transport. Hooke's existing services own authorization, evidence and operations.

```text
external agent → scoped MCP reader → existing hooke CLI → supported agent door
                                                       → Project evidence
```

The reader discovers current declarations for `project.state` and `artifact.read`;
it does not reproduce their schemas or maintain scientific methods. Shared schema
references are resolved locally. It also reads the operator-selected Study and
observes the handles of reads admitted during this client lifetime. Repeating the
same read request key after reconnection recovers the original handle through the
server's idempotent admission. Use a new key when requesting a fresh state snapshot.

No caller can select another Project, supply an arbitrary route or host path,
list Projects, import material, change grants, launch a workflow or access provider
credentials through this client. Added actions and arguments default to refusal.
Server grants and revocation still apply on every request. The fixed connection
retains its real attribution; a new session label does not create a new identity.

This implementation is a read-access milestone, **not a qualified blind scientific
runtime**. A full research client still needs reviewed workflow admission, bounded
resource controls, result publication and fresh-agent continuation. Extend the same
door and WorkflowService; do not introduce another runner or attempt ledger.

## Optional frozen-workflow execution

An operator may supply `--workflows /absolute/operator/scope.json`, containing
`{"format": 1, "workflows": [{"id": "<retained workflow id>", "revision": 1,
"digest": "<retained revision digest>"}]}`. This file is read once, outside the
model's workspace. It must be inaccessible for modification by the caller.

This adds discovery of those exact definitions, `run_workflow`,
`observe_operation`, `cancel_operation` and `publish_finding`. The caller chooses
exact retained inputs and named bindings within the fixed Project/Study; method
settings are already frozen. Definition identity is checked before admission and
Hooke rechecks runtime generations and input digests. The client cannot create or
edit a graph, choose host paths or supply arbitrary execution parameters. An
operator qualifies each graph/runtime before granting its pin; digest equality
alone does not establish confinement or scientific validity.

WorkflowService remains the only execution owner. Its original operation/attempt
identities survive caller disconnection. A fresh client can observe an operation
linked by the selected Study, with its original parameters and attribution;
observation never starts it. Explicit Stop requests termination and returns the
observed state. Retries preserve the request key. Claims use the existing shared
claim/evidence contract and server-derived authorship; publication does not confer
human acceptance. No local execution ledger or scientific algorithm is added.

Existing method resource limits and the shared heavy-work slot still apply. This
client is not a lifetime compute-budget enforcer. Workflow admission readiness,
server grant checks, runtime confinement, output integrity and actual scientific
evaluation must be qualified separately. This optional surface does not itself
establish a blind run or an end-to-end research result.

## Effective boundary

The trusted client process can reach the CLI and its operator's host environment.
The model must not have an alternate path to that environment: unrestricted shell,
filesystem, web, apps, browser, other MCP servers, mutable adapter configuration or
unconfined children invalidate isolation. Restrict these at the harness/runtime
level and test them separately. Project grants and prompt instructions are not a
filesystem sandbox. Never infer a complete isolation claim from an SDK unit test.

Before a blind run, audit the actual tool inventory and admitted evidence, probe
forbidden content and metadata with synthetic inputs, then freeze the effective
configuration. Keep owner/evaluator records outside the solver's permitted evidence.
Public-model pretraining exposure remains a separate limitation.

## Qualification

`test_scoped_agent_client.py` tests default refusal, exact references, request-key
preservation, original-handle observation, revocation, shared schema resolution,
literal subprocess arguments and bounded complete response reading. No real token
or scientific data is used. `test_scoped_agent_mcp.py` exercises the actual official
SDK with a synthetic door. CI installs all dependency groups; a focused environment
without the optional group reports that protocol test as skipped.

An actual stdio/CLI/Keychain journey, agent runtime confinement and scientific
execution are distinct evidence classes. A reader working against an existing
Project does not establish that a researcher has produced or validated a model.

`test_scoped_agent_research.py` uses synthetic door records and the actual SDK to
check frozen pins, same-Project bindings, unchanged request identities, lost-client
recovery without replay, original authorship, revocation, cancellation intent and
claim scope. It does not run a scientific worker or a frontier model.
