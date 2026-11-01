# Scoped external clients

This optional stdio MCP client uses the official SDK and the already-connected
`hooke` command. It never reads Keychain tokens, opens a second server port, runs
scientific code or writes Project storage. The operator fixes the CLI, named
connection, loopback door, Project and optional Study before launch.

The default qualification surface is read-only: paged Project state, exact retained
artifacts, one Study and observation of reads admitted by this client. Schemas and
availability come from the existing door, with shared references resolved locally.
The read policy is default-no; it is not a scientific capability list. Host-path
imports, project listing and arbitrary HTTP routes are absent.
Extending it requires an indirect-access review and tests, not just exposing more
names. No experimental result can select executable code or a client configuration.

Optional `--workflows` reads an operator-owned scope file once at launch. It pins
up to 32 existing definitions by id, revision and digest. The research client can
inspect those definitions, admit them with exact same-Project inputs, observe or
request Stop on their original Study operations, and publish attributed claims.
It cannot create/edit workflows or supply execution parameters/code. The operator
must qualify each frozen graph, catalog runtime, indirect file/network access and
resource requirements before including its pin. A matching digest is not a safety
review. Existing WorkflowService, generation checks and heavy-work reservation own
execution; this client owns no scheduler, attempt records or spending authority.

Optional `--admissions` further fixes up to 32 exact request keys and argument
sets at startup. A one-entry file permits at most that one server operation, even
after lost replies or client restarts with the same configuration/connection.
Changed keys or inputs refuse before contacting the door. An empty list permits
reads, observations and findings but no new workflow execution. Keep this file
outside the caller's writable scope; replacing it or changing connections is a
new operator allowance. Existing Hooke request idempotency owns deduplication;
the client adds no execution ledger. This bounds admissions, not tokens, elapsed
time or the cost of an individual workflow. Qualify those limits separately.

Fresh clients recover workflow handles only from the selected Study's original
experiment links, checking original parameters and preserving attribution. They
do not recover another client's read-operation handles. Lost admission replies use
the same request key, never a new speculative run. Stop reports the server's
observed state; an agent disappearing does not cancel an admitted workflow.

This is a tool-scope boundary, not a whole-agent sandbox. A caller with another
connection, unrestricted shell, network, browser, apps or writable adapter source
can bypass it. Scientific blind-run qualification must separately restrict and
audit all those paths, including child agents. A local operator can still inspect
the underlying credentials and host data; do not claim protection from that user.

Reads use ordinary tracked door operations. Observed read handles are held only for
the client lifetime, not a second execution ledger. After restarting this client,
repeat the original read request key to recover its handle. Do not replay a failed
scientific operation or weaken server grants. Cancelling an MCP call stops its CLI
observation, not an admitted Hooke operation. Missing/revoked credentials refuse.

Install the optional `agent-client` dependency group in a prepared environment.
Run from services with `python -m agent_client --help`; do not install packages at
research runtime. SDK protocol tests use synthetic transports. Actual external
client/Keychain and science qualification are separate evidence.

The scoped research client can read retained coordination, claim/renew an assignment and return its evidence-linked report. It cannot rewrite the reviewed plan, propose new executable authority or alter its fixed workflow/admission scope. Assignment identity is passed in the existing workflow request. General connector clients can initiate plans through the shared door; the confined solver surface remains narrower by design.
