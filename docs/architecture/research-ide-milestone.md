# Research IDE foundation — November 3, 2026

This is a bounded positioning and navigation milestone. It makes the existing
Project storage and private-backup features easier to find; it does not establish
complete research-platform, swarm-scale or portability qualification.

## Delivery

- Website: **The IDE for Research**, with local/cloud model choice, shared research
  assignments, Project access through Hooke, local files and optional private
  GitHub backups. Published on [hookeui.org](https://hookeui.org/); the deployment
  receipt is retained in the private operator handoff.
- Mac: the existing Project-folder inspector explains the local research home,
  puts private backup ahead of export/publication, and offers **Back up to GitHub…**
  directly. [Core PR 194](https://github.com/wpulier/HookeUI/pull/194) tracks
  integration into `dev`; merge and app release are separate events.

## User journey and compatibility

Open a Project → open its folder inspector → inspect its local location or choose
Back up to GitHub → complete the existing owner sign-in and private-repository
setup → explicitly confirm the backup. Opening or cancelling setup uploads nothing.
Existing destinations continue to offer Back up again.

Keep Project storage, snapshot manifests, backup services, owner confirmation and
agent grants authoritative. This change adds no API, saved format, permissions or
execution path. Connected agents continue through the supported door and existing
authorized destinations. Access through Hooke is not a sandbox for an external
agent's independent filesystem access.

## Evidence and completion gates

- Sixteen focused Project-folder/backup tests and TypeScript passed on the core
  implementation. The added test checks direct GitHub selection and cancellation
  without a backup request.
- Actual browser inspection used the production component with synthetic
  Project/authentication responses. It does not prove a real GitHub backup.
- The website's complete build and link checks passed. Desktop and 390px browser
  checks covered the live landing content and responsive layout.
- The core's required CI and frozen `make check` remain merge gates. Consult PR 194
  for their current outcome; focused checks are not a substitute.

## Next milestones, deliberately separate

1. Qualify real owner-authorized backup and restoration with complete dependency
   accounting; preserve existing collection limitations in the interface.
2. Specify any existing-repository association, automatic synchronization or
   multi-host collaboration before adding it. A private snapshot backup does not
   promise those behaviors.
3. Measure research continuity and useful collaboration through the shared
   [initiation](research-initiation.md) and [navigation](research-navigation.md)
   services. Marketing language does not qualify agent concurrency or scientific
   outcomes.

Remote-format work builds on [Project format v2](hooke-project-format-v2.md).
Do not introduce a second Project store, agent-specific backup path or credentials
inside portable Project files.

Repository names already in use are refused, including interrupted first backups without a durable creation receipt. Choose a new name; a single starter commit is not proof that Hooke owns that repository. Existing configured destinations continue to update through their retained identities.
