# 19. Readiness is one resolver over distinct facts

**Context.** Whether a model could run was decided in four places, each with different inputs:
- `options.unavailable` and `catalogue`;
- `operations.availability`;
- `capabilities.usable`;
- `model_checks.before`.

Some of their checks are safety, not bookkeeping: the model check won't run a tracking probe inside the live app,
because it would reset the follower's session.

"Downloaded" was guessed from a `snapshots/` folder. The Models menu greyed out what couldn't run, with no next step.

The user wants every model shown as potentially available, with a clear way to turn it on: add a key, accept a
licence, download, install.

**Decision.**
- **One resolver, `orchestrator/readiness.py`, keeps each fact apart:**
  - obtained (the pinned revision's files present and valid by size and sha256, and its environment installed);
  - credentials, to get it and to run it, apart;
  - edition and platform;
  - input fit;
  - the last check;
  - selected;
  - loaded;
  - temporarily blocked;
  - qualified.
- **It shows one plain status from those facts:**
  - **Ready,** with Selected and Loaded shown apart;
  - **Needs setup** (a key, a licence, a download, an install, an adapter), with one next action and the rest of its
    checklist;
  - **Setup incomplete,** with its errors;
  - **Unavailable here,** with why;
  - **Broken.**

  The Models menu, Capabilities, the agent's refusals and `make doctor` all show the same status.
- **Reading is cheap.** It uses manifests, receipts, file stats, the checks log and the loads log. It never imports a
  model library, starts a worker, calls a provider or downloads. A test checks this.
- **Execution still checks.** The runner checks worker health and resource admission at run time. A display answer is
  never permission to run.
- **The existing guards stay in the resolver.**
- **Nothing is obtained or installed without the user's deliberate action on the screen.**

**Cost.**
- **Counts change.** Counts of what works here change as "downloaded" becomes exact. Capability definitions and
  defaults don't.
- **Testing needs an isolated profile.** Each status is tested in an isolated profile, with temporary folders and
  mocked credentials, never by touching a working setup.

Design: `docs/design/2026-10-12-the-open-catalog.md`.

**Amended 2026-10-20 by [ADR 0025](0025-a-recorded-maintenance-policy-authorizes-later-updates.md)** (accepted
provisionally by the coordinator; Will may revise). The last bullet now also allows obtaining and installing an
entry's later updates under a maintenance policy the user recorded for that entry, within ADR 0025's limits.
Permissions only a person can grant stay on the screen. The text above is kept as decided.
