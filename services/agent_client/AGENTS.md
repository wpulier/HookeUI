# Scoped external clients

This optional stdio MCP client uses the official SDK and the already-connected
`hooke` command. It never reads Keychain tokens, opens a second server port, runs
scientific code or writes Project storage. The operator fixes the CLI, named
connection, loopback door, Project and optional Study before launch.

The first qualification surface is read-only: paged Project state, exact retained
artifacts, one Study and observation of reads admitted by this client. Schemas and
availability come from the existing door, with shared references resolved locally.
The read policy is default-no; it is not a scientific capability list. Host-path
imports, project listing, arbitrary HTTP routes and workflow execution are absent.
Extending it requires an indirect-access review and tests, not just exposing more
names. No experimental result can select executable code or a client configuration.

This is a tool-scope boundary, not a whole-agent sandbox. A caller with another
connection, unrestricted shell, network, browser, apps or writable adapter source
can bypass it. Scientific blind-run qualification must separately restrict and
audit all those paths, including child agents. A local operator can still inspect
the underlying credentials and host data; do not claim protection from that user.

Reads use ordinary tracked door operations. Observed handles are held only for
the client lifetime, not a second execution ledger. After restarting this client,
repeat the original read request key to recover its handle. Do not replay a failed
scientific operation or weaken server grants. Cancelling an MCP call stops its CLI
observation, not an admitted Hooke operation. Missing/revoked credentials refuse.

Install the optional `agent-client` dependency group in a prepared environment.
Run from services with `python -m agent_client --help`; do not install packages at
research runtime. SDK protocol tests use synthetic transports. Actual external
client/Keychain and science qualification are separate evidence.
