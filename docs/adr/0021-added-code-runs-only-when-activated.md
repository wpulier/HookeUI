# 21. Code a user adds runs only when they activate it, and is called sandboxed only when the OS enforces it

**Context.** The catalog lets a user add a model from a repository, with an adapter they or the agent write. That is
arbitrary code. A separate process and environment isolate dependencies, not access: such code can read private files
and the keys, and reach the network. "Sandboxed" would promise what nothing enforces.

**Decision.**
- **Discovery reads metadata only.** Finding a folder parses its TOML and stats its files. It never imports its code.
- **Activation is deliberate and per entry.** Installing and running need the user's action on the screen. The agent
  may propose it, quoting the user's words.
- **Until an OS-enforced boundary exists and passes its test,** such a run is labelled "runs code you added, on this
  Mac, with your permissions". It runs in its own process and environment. It never gets the app's keys unless its
  manifest names one and the user allows it. It is recorded as unreviewed.
- **The word "sandboxed" appears only once the boundary is enforced.** It must allow reading only the run's inputs,
  writing only its scratch folder, and no network unless declared and allowed (macOS `sandbox-exec` or an
  equivalent). Its test is an adapter that tries to read the home folder, the keys file and the network, and is
  refused each time.
- **Reviewed entries** are reviewed by PR with CI, and CODEOWNERS names who keeps each one. Review is a code-trust fact,
  separate from compatibility and qualification.

**Cost.**
- Until the sandbox slice lands, unreviewed code runs with the user's permissions, and the app says so.
- Contributors wait for review before an entry is offered to everyone.

Design: `docs/design/2026-10-12-the-open-catalog.md` §5.

**Amended 2026-10-20 by [ADR 0025](0025-a-recorded-maintenance-policy-authorizes-later-updates.md)** (accepted
provisionally by the coordinator; Will may revise). First activation stays deliberate and per entry. Later updates of
an activated entry, from the same source, may be obtained, prepared, installed and promoted under the user's recorded
maintenance policy. Unreviewed code is never promoted unattended. The text above is kept as decided.
