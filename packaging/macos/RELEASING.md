# Beta and Stable release policy

**PR → `dev` → publish Beta → Will accepts → promote to `main` → publish Stable.**

This workflow is public so contributors know how work reaches users. Both `dev`
and `main` are open source. Source visibility does not grant merge, signing or
publishing permissions. Credentials and the official publishing runbook remain
in the private Hooke platform repository.

## Who does what

| Step | Responsible person | Required handoff |
| --- | --- | --- |
| Review a contribution to `dev` | An appointed maintainer/code owner | Required review and relevant passing checks |
| Prepare and publish Beta | An authorized release operator | A fixed, checked `dev` commit and explicit Beta publication authorization |
| Accept Beta | Will Pulier (`@wpulier`) | Actual installed-Beta acceptance, identifying the version and source |
| Promote to `main` | **Will only** | A reviewed PR containing exactly the accepted Beta source |
| Prepare and publish Stable | An authorized release operator acting on Will's explicit authorization | The accepted source on `main` and its retained Beta release records |

1. Contributors open PRs against `dev`. Maintainers review them under the
   [contribution rules](../../CONTRIBUTING.md). `dev` is the default branch.
2. The release operator freezes a full source SHA from checked `dev`, prepares a
   signed/notarized Beta, verifies its artifacts, and explicitly publishes those
   bytes to the Beta channel. Moving `dev` does not update an installed Beta.
3. Will tries that Beta and records what he actually accepted. A test result,
   another reviewer's approval or an agent's inference is not owner acceptance.
4. Will promotes that exact source through a PR into `main`. If `dev` has moved,
   use a branch pinned to the accepted Beta SHA. Use a merge commit, preserving
   ancestry; the resulting main tree must match the accepted Beta. Resolve extra
   changes or conflicts through `dev` and a newly accepted Beta.
5. The release operator prepares Stable from that source using the accepted
   Beta's private tooling revision, then publishes the verified Stable artifact
   only with Will's authorization. Stable and Beta keep separate feeds, identities
   and user data. Merging, pushing or tagging alone never publishes either app.

## Instructions for maintainers and agents

- Read this policy before release work. Do not promote unrelated `dev` changes or
  treat a documentation merge as approval for a new installer.
- Official commands and recovery steps live in private `hooke-web`, at
  `desktop-release/packaging/macos/RELEASING.md`. Authorized operators use that
  publisher with an explicit HookeUI checkout. Request access from Will if needed;
  do not recreate a publisher or copy credentials into this repository.
- Preserve the source SHA, candidate hashes, checks, publication receipts and
  actual owner acceptance. Never invent or broaden an acceptance statement.
- Generic core builds require no Hooke account. Historical publishing helpers in
  this repository are not the official release entry point.

GitHub requires PRs for both branches and restricts updates to `main` to the
owner's PR-only bypass. Every PR into `dev` must also pass the `tests` check, which no
bypass skips; a release candidate is a commit of `dev` that reached it this way. Maintainer approval alone cannot merge into `main`.
Public review comments remain welcome but do not grant authority to approve or
ship a release. The owner controls repository settings and maintainer appointments.

Installers published before the repository separation retain their original
release history. The fresh repository's initial main is a source baseline, not a
new Stable release; existing users keep their published version until explicitly
updated through their channel.
