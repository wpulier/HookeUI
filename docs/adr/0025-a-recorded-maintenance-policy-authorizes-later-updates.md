# 25. A recorded maintenance policy authorizes an entry's later updates

**Status.** Accepted provisionally by the coordinator on Will's instruction to finish without asking; Will may revise.
Recorded 2026-10-20 (MT0 of the H13 ticket).
Ticket 62's Authority section names ADRs as Will's; this record stands on the coordinator's instruction until he
confirms or revises it. Amends ADR 0019 and ADR 0021, whose text is unchanged apart from a dated note.

**Context.**
- **Three rules make every update a press.**
  - ADR 0019: nothing is obtained or installed without the user's deliberate action on the screen.
  - ADR 0021: activation is deliberate and per entry.
  - `env_runner.py`: a new version is used only after its proof passes and the user explicitly selects it.
- **The direction asks for more.** The founder's direction of 2026-10-19
  (capability maintenance §2) asks
  for *Automatically maintain compatible updates*, *Review updates* and *Pin this version*. Under the first, Hooke
  applies qualified compatible fixes itself, instead of telling the researcher to watch the upstream repository.
- **Presses don't scale.** With 10–30 entries, a press per update per entry is the forum-watching the product is
  meant to remove.
- **The direction says how to change the rules.** Amend the decisions with an explicit scope, keep their history, and
  keep the permissions only a person can grant.

**Decision.**
- **A maintenance policy is a per-entry record.** Four values:

  | Policy | What Hooke may do unattended for that entry |
  |---|---|
  | `manual` (the default) | Nothing. Today's behaviour: checks may say an update exists; obtaining, installing and switching each need a press. |
  | `review` | Obtain, prepare and prove a compatible candidate beside the active generation. Promotion waits for the user's press (*Ready to apply*). |
  | `auto` | Everything `review` does, then promote a compatible, proven candidate for new work. |
  | `pin` | Nothing. The entry stays on the named generation, even when a newer one is proven here. Checks may still say an update exists. |

- **The default is today's behaviour.** An entry with no record is `manual`. No profile changes until a person
  records a policy.
- **Setting many entries at once** records one policy per entry, only for the entries named at that moment. An entry
  added later starts at `manual`.
- **A record is a deliberate act.**
  - It holds the policy, the entry, its source, who recorded it, when, and how. It is kept in the profile and survives
    restarts.
  - Every change is appended to an audit log that is never rewritten.
  - The user records it on the screen. The agent may propose one, quoting the user's words, as ADR 0021 allows for
    activation. Agent prose never records a policy.
- **Revocable at any moment, with one action.** Revoking stops any attempt in progress before it promotes. It never
  promotes a candidate. It leaves the active generation as it is.
- **The policy authorizes only later updates of an entry the user has already activated.**
  - The first activation stays ADR 0021's on-screen, per-entry action.
  - A policy covers only the entry's own key, from the same source it was activated from: the reviewed catalog, or a
    community channel the user trusted.
  - Every candidate is an immutable identity: a commit, a lockfile hash, file digests. Never a moving branch.
- **Permissions only a person can grant stay manual under every policy.** A candidate that needs any of these is
  **Needs review**, with exactly what needs review said:
  - a new or changed source, or trusting a channel (`channels.trust`);
  - a licence or terms to accept, a new or changed key or credential, or an account;
  - wider permissions: network, file access beyond the run's inputs, a key, a sandbox exemption, or code moving from
    reviewed to unreviewed (ADR 0021's code-trust fact);
  - a cost: a paid call it would make, a higher price, or a cap to raise;
  - a change in scientific behaviour: model weights, output meaning, calibration, operation contract, required
    hardware, or document format. Upstream calling it a patch doesn't make it routine.

  Hooke may still prepare such a candidate unattended under `review` or `auto` when preparing needs none of these
  permissions. Promotion waits for the person.
- **Unreviewed code is never promoted unattended.** For an entry whose code is unreviewed (your own, or from a
  community channel), `auto` acts as `review`: the candidate is prepared and proven, and the user's press applies it.
  Allowing code from a channel consented to what the user had seen, not to every later commit.
- **Compatible is decided from declared facts and a proof, never from a version string or an import.**
  - The candidate declares nothing in the list above differently from the active generation.
  - Its proof passed on this Mac: `env_runner.prove`, plus the entry's declared reference checks.
- **Promotion is atomic and applies only to new work.**
  - One pointer is replaced, and an auditable decision is written: the policy, the candidate's identity, its proof,
    the time.
  - These keep their generation: running jobs, open tools, kernels, comparisons, saved recipes, and anything pinned or
    retained by saved work.
  - Nothing reruns. No selected model changes (ADR 0011). No open scientific document is migrated.
  - Updating a pinned method is an explicit new method revision.
- **Undo is always available.**
  - The generation a promotion replaced is kept until nothing needs it, and the user can undo the promotion.
  - If the first check after an unattended promotion fails, the pointer goes back to that generation by itself. The
    entry is then held at `review` until the user looks.
- **Unattended work never costs the live view** (AGENTS.md, "The live view comes first").
  - It runs off the event loop, in a separate low-priority process.
  - It uses the existing downloads, resource admission and confinement, and yields to the live view.
  - A failure leaves the active generation untouched and is said. *Update failed*, *old version usable* and *known
    result defect* stay three different facts.
- **Reading stays cheap (ADR 0019).** Status reads the policy record, receipts and logs. Reading never starts a
  check, a download, an install or package code.
- **Out of scope.**
  - App updates (Sparkle) are a different lifecycle.
  - This record authorizes no live install, paid call, app restart or publication by itself.

**What it amends.**
- **ADR 0019,** last bullet. It now reads: nothing is obtained or installed without the user's deliberate action on
  the screen, *or under a maintenance policy the user recorded for that entry (ADR 0025)*.
- **ADR 0021,** "Activation is deliberate and per entry". First activation is unchanged. Later updates of the same
  entry, from the same source, may run under its recorded policy, within this record's limits.
- **`env_runner.py`,** "the user explicitly selects it". A recorded `auto` policy is that selection, for compatible
  proven candidates of reviewed code.

**Cost.**
- **Code the user didn't press for at that moment runs.** Bounded by:
  - the same source;
  - an immutable identity;
  - a proof here;
  - unchanged permissions;
  - reviewed code only.
- **A second set of facts to keep consistent.** The policy and the update state must read the same on every surface:
  cards, setup sheets, the agent, the overview. The H13 lifecycle owner keeps one vocabulary for them (ticket 59).
- **Space.** Candidates and kept generations take disk. Preparation checks free space first, and Space shows what
  each kept generation is kept for.
- **Testing.** Every policy and failure is tested in an isolated profile, with a local fixture channel, never against
  a working setup.

Requirements: `docs/handoff/2026-10-19-hooke-direction/capability-maintenance-and-scenarios.md` §2. Design: ticket 59's
MT1 slice. Builds on ADRs 0011, 0019, 0020 and 0021.
