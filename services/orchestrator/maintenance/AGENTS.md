# Capability maintenance

Part of the working context the root `AGENTS.md` maps (under Catalog). The lifecycle meaning, the generation states and
the status vocabulary are ticket 59's (`docs/planning/open-source-successor/implementation/issues/59-hooke-h13.md`),
kept by the H13 lifecycle owner; the authority for anything unattended is ADR 0025. Change the words there first.

**What it does.** An entry the user already turned on (ADR 0021's first activation) can have a recorded maintenance
policy. Under that policy, its later updates are found, prepared beside the active generation, proven, shown and promoted
for new work, with pins that survive restarts and rollback:

| Policy | Unattended |
|---|---|
| `manual` (no record) | nothing: a check may say a newer commit exists; obtaining, installing and switching each need a press |
| `review` | fetch the newer commit, build and prove the candidate, then wait at Ready to apply |
| `auto` | the same, then promote a compatible, proven candidate of reviewed code, with its first check |
| `pin` | nothing; the entry stays on the named generation |

For now it covers entries with their own environment (runner `python-env`); Fiji generations and file-only entries come
in MT4 through the same package.

**Rules it keeps.**
- **A policy is a person's.** It is recorded on the screen, or by confirming an agent's proposal that quotes the user's
  words. The agent never records one. Every change goes to `policies.jsonl` and is never rewritten.
- **Code nobody reviewed is never promoted unattended.** For your own entries and a community channel's, `auto` acts as
  `review` (`policy.effective`).
- **Some changes always wait for a person.** Anything `compat.py` finds changed among the declared facts stops the
  attempt at Needs review, said exactly: source, licence, keys, what the app gives it unasked, cost, where it runs,
  weights and files, the job, settings and defaults, outputs, outcomes. The adapter and lockfile are the fix and are not
  compared.
- **One entry at a time, never the channel.** A newer commit is fetched for the entry alone, into
  `maintenance/candidates/<channel>/<commit>/`. The channel's own pin is shared by every entry from it and never moves
  unattended. The pinned catalog may then be behind an entry; a generation a promotion moved away from is never offered
  as an update again (`discover.superseded`).
- **Promotion replaces one pointer** (`env_runner.promote`, `chosen.json`) and appends a decision to `decisions.jsonl`.
  The replaced generation gets a rollback pin. Running work, pins and saved work keep resolving their own generation.
- **`applying` is written before the pointer moves.** An attempt that stops after it resumes with the first check
  (`prepare._resume`). Each check's result is a `checked` decision.
  A person's Apply uses the same attempt journal and entry lock, including under manual policy. If the app exits
  during its first check, the next app resumes that check and rolls back a failed update without enabling auto updates.
  An admission refusal keeps the previous Needs review card and its exact acceptance fingerprint.
  Recovery distinguishes a passed check from a retained failed check: it completes the failed rollback, or finishes
  the policy hold/status if that rollback already happened, without running the proof or moving the pointer again.
- **A pin holds.** Nothing moves a pinned entry unattended. A person's own choice of another version ends the pin, and
  that is recorded. The pin is a `user` hold in `pins.json`, which removal respects.
- **Builds are locked across processes** (`envs.building`: `<generation>.lock`). The app's Install and the worker
  never delete or race each other's builds, and Install waits while an update attempt runs.
- **A failed first check after an unattended promotion rolls back by itself.** The policy is then marked `held`, and
  `auto` acts as `review` until a person records it again. Undo, and going back by hand (`env_runner.choose`), hold it
  the same way.
- **Nothing runs on the event loop.** An attempt (`prepare.run`) runs in its own process
  (`python -m orchestrator.maintenance attempt KEY`). The app's `Maintainer` thread starts it at the lowest priority the
  system has (`taskpolicy -b`), in its own process group, one at a time. Stop, revoking the policy, or quitting ends it
  with everything it started. On a busy Mac that priority can starve it for minutes; that is the intent.
- **Reading is cheap.** `status.of` reads the profile's maintenance files (each parsed once per change), receipts and the
  observed environments. It starts no check, download, build or package code (`test_reading_status_starts_nothing`).
  The existing off-loop environment observer also captures policy, check, pin, decision and per-entry attempt
  records. Synchronous display scopes reuse those exact-path observations, including missing files. A new observation
  replaces them; unrelated files and other profiles still use live reads. Maintenance writer locks enter live mode,
  so a stale display cannot supply a mutation's input state. Pin removal prechecks, pin-based removal authority and
  runtime removal (including its active-attempt check) also explicitly read live facts before any mutation.
  Entry-attempt locks likewise enter live mode before Apply or worker preflight and cancellation checks.
  This does not remove all catalog filesystem reads.
- **Checks back off.** A failed check is tried again after 15 minutes, doubling up to a day. It never means "no update"
  and never touches the installation.
- **Removal keeps what is pinned** (`envs.removal` → `pins.require_removable`); a rollback hold goes with the software.

**Beyond the first journey (MT2–MT4).**
- **Accepting what changed** (`review.py`). A Needs review candidate carries a fingerprint (the entry, its version and
  generation, the items).
  - `maintenance-accept` records a person's acceptance of exactly that, as a decision; it needs the fingerprint the
    card showed. The attempt then continues:
    `auto` promotes, `review` waits.
    Acceptance holds the entry-attempt and maintenance-store locks while resolving the reviewed target and
    rechecking its version, generation and fingerprint. Its receipt records that candidate's version/generation,
    never the older catalog pin's metadata. A busy or changed target refuses without appending a decision.
  - Changes only of kind science or hardware are built and proven before the person looks.
  - A key the candidate newly needs and this Mac lacks keeps it at Needs review with that step named. Acceptance is
    never setup.
  - Promotion itself checks the exact active/candidate changes and their acceptance, including new setup needs.
    A prepared proof or a stale Apply request cannot bypass Needs review or clear a pin before that refusal.
    The active read, admission, pin changes and pointer/decision share the maintenance store lock with Undo/rollback
    and policy publication. The lock is reentrant only within its owning thread/process; other writers still wait.
- **Your version when its base changes** (`adapt.py`).
  - The base as you derived it is found by its recorded digest among the kept channel commits and installed
    generations.
  - Files merge three ways with `git merge-file`; the manifest merges field by field.
  - A conflict keeps both sides: markers in text files, a field's update value said at the manifest's top, and
    `<file>.yours` and `<file>.update` beside each. A conflicted file is resolved only once you change it (its hash).
  - The candidate is keyed on the base's digest and yours, so an edit after the check is never overwritten.
  - `maintenance-merge` writes a resolved candidate into your folder, keeping your previous files beside it.
- **Revoked versions and unmaintained entries** (`revocations.py`).
  - A catalog's `maintenance.toml` beside its entries lists `[[revoked]]` versions (severity `warn` or `block`) and
    `[[unmaintained]]` entries. No entry changes with it, and older apps ignore it.
  - The daily metadata look takes it too.
  - A revoked active version: attention plus a known defect. `block` refuses its runs (`env_runner`).
  - A revoked candidate is never promoted.
  - A cloud model's version revoked with `block` (its provider retired it) is never offered to run: its card says it
    can't be used, with why (`catalog_view.execution_block`); `replaced_by` names the entry that replaces it. The app's
    own catalog says this of Veo 3.1 Fast (Google retired it on 22 Oct 2026); its successor, Gemini Omni 1.1 Flash, is
    a new entry and the code's video model from that version of Hooke, a change of model reviewed as its pull request
    (ADR 0025: new weights wait for a person).
- **Missing sources.** An entry is unmaintained when its catalog says so, or when its candidate snapshot at the
  newest commit no longer has it. A source unreachable for `UNREACHABLE` checks and `UNREACHABLE_S` needs attention
  ("couldn't reach its source since T"). Its working version stays in every case, and other entries of the same channel
  are not affected. Nothing ever says an entry is maintained.
- **Many entries.**
  - One look at a channel's newest commit serves every entry for `NEWEST_S`, kept in `checks.json` under `_channels`.
    A person's Check or Retry looks again (`attempt --again`).
  - `attempts/<key>.lock` keeps each entry to one attempt at a time across apps.
  - Free space is checked before anything is obtained.
  - Due entries go longest-waiting first, and one that died early waits `RETRY_S`.

**The three forum scenarios (MT5–MT7).** Each takes its update through this lifecycle; none adds a package-name branch.
- **Known fixes** (`revocations.fix`, MT7, the QuPath #2211 case). A catalog's `maintenance.toml` may list `[[fixes]]`
  (entry, versions, issue, why, fixed_in). An affected active version gets a known defect, whatever the policy; with no
  `fixed_in` yet the state is `fix-pending` ("Fix identified — compatible build pending"); with one, the fixed version
  is an ordinary candidate whose proof must pass. Once a version without the defect runs, the exports an affected
  version made are listed as `affected`, with the uncertainty said, and offered for re-export, never rewritten.
- **The exact-view export contract** (`orchestrator/view_export.py`): the dispatch record is taken when the export is
  asked (resource revision and digest, frame token, every axis's step, camera, layers, scope, an explicit region); it
  refuses a stale view, an unknown plane ("no plane is assumed") or an inferred region, checks the written image
  against its preview within `TOLERANCE`, and indexes each export in `exports.jsonl`.
- **Calibrated area and method comparison** (MT6: `orchestrator/calibrated_area.py`, `method_comparison.py`,
  `task_facts.py`): areas from the labels' own axis-aligned calibration or pixels with why; two to six arms on one
  frozen source, each declaring its factor, boundary definitions never ranked, a choice kept with its rationale.
- **A reader update** (MT5: `examples/beta-scientific-tools/bdv-hdf5-reader/`, BigDataViewer HDF5): its tests take v1
  (level 0), v2 (the pyramid) and a broken v3 through `prepare.run`; v3 fails its proof and v2 stays, and a Project
  held on v1 keeps v1. Synthetic h5py fixtures only; the example isn't packaged.

**Where things are.**

```text
<catalog>/maintenance/
  policies.json, policies.jsonl    each entry's policy, and every change to it
  checks.json                      each entry's last check: when, its outcome, what it found, when the next is due
  attempts/<key>.json              the current or last attempt: target, step, state, why (resumed from receipts)
  decisions.jsonl                  every promotion, rollback and undo
  pins.json                        durable holds on generations (recipe, session, job, user, rollback)
  attempts/<key>.lock              held by the entry's running attempt
  exports.jsonl                    each retained view export: file, renderer entry, generation, version (view_export.py)
  derived/<key>/<base>-<yours>/    your version merged onto its base (16 characters of each digest), with merge.json;
                                   previous-<time>/ beside it
```

**Surfaces.**
- The capabilities wire carries `setup.maintenance` (`status.wire`): each installed entry's update state, policy, last
  check, active generation, candidate and holds. A check that found nothing newer says so: "Up to date · checked Oct
  27, 11:42 pm" (state `none`); every time a line says is readable (`status._when`), never ISO.
- Each built-in entry (the app's own catalog, not cloud, no environment of its own) gets one line instead
  (`status.built_in`): "Updated with Hooke 0.5.2" (`build_info.version`, packaged.json; a source checkout says it comes
  with this copy), `builtIn: true`, no policy, check or action: it is updated when Hooke is. `status.of` still covers
  only entries with their own Python.
- An entry from a shared file (`status.shared_file`: its channel's source) has no online revisions: opening a newer
  shared file adds an entry of its own (`channels.update` refuses to move one). It is never "Up to date": its line is
  `status.SHARED_FILE` ("From a shared file: it doesn't update itself. A newer shared file is added as its own entry"),
  with `sharedFile: true`, so the studio offers no policy or Check and says no check fact; its Check
  (`maintenance-check`) answers the same. Wiring a newer shared file as an update of the first is not done.
- The card's existing update line reads the same words (`env_runner.update_waiting`).
- The studio's line is `apps/studio/src/maintenance-status.ts`. Details → Updates stays open, and keeps its last reply
  to Check or a policy, while the page draws it again (the page's `maintenanceMemory`), until the update state moves on
  from the one the reply came with (the command's `update.state`): "Checking … for an update" goes once it is Ready to
  apply. Its facts' times are readable (`readableTime`); a built-in shows its line and nothing to choose. The block is a
  column the width of Details (`.model-maintenance`, modelspage.css), never a card's one-row `model-card-action`.
- The agent's model answer adds the same line (`brain/toolbox/catalog.py`).
- The commands are `maintenance-policy`, `-check`, `-apply`, `-undo`, `-stop`, `-accept` and `-merge`
  (`commands.py` here).

**Tests and the sim.**
- `services/tests/test_maintenance.py` (MT1), `test_maintenance_lifecycle.py` (MT2, MT3) and
  `test_maintenance_portfolio.py` (MT4) use a local reviewed channel and a stand-in uv, as do the forum scenarios'
  `test_maintenance_scenarios.py` (MT7) and `test_bdv_reader.py` (MT5); `test_method_comparison.py` is MT6.
- `sim/maintenance_fixture.py` builds a scratch catalog from `catalog/models/spot-shape` (real numpy and SciPy) and never
  touches this Mac's catalog. `python -m sim.maintenance_fixture` runs the journey from the command line, and `… portfolio` ten real entries.
- The `maintenance-during-live` scenario measures the live view while the real worker prepares, proves, promotes, fails a
  broken fix and undoes.

**Exact export and notice boundaries.** The view-export contract owns a finite JSON copy of every nested dispatch
value. Renderer callbacks receive a separate copy, and retained preview pixels stay fixed. Only the native view or
crop size is currently supported; a different requested size is refused before renderer execution or file writes.
This is a shared contract with synthetic renderer evidence, not an implemented ordinary-UI resizing workflow.
Fix notices from metadata, candidate snapshots and the pinned channel use one local observation modification-time
order, including later explicit channel updates; reading notices never moves the channel pin. The same issue/version notice appears once in
fix lists. Revocation severity continues to choose the most severe retained notice.
Task facts use structured readiness steps (edition, platform, route, check) and preserve their original details.
An unavailable or broken installation is not relabelled as missing hardware or a missing integration.

MT5's reader proof dispatch now has a separate `reader-data-contract-v1` path for
explicit directory cases on scientific-host members. Promotion still uses the
ordinary prepared/proven/compatible gates; first-check replays every reader case
through its confined prepared host, without using an RGB surrogate. Failure keeps
the candidate unproven or uses the existing post-promotion rollback/policy hold.
Member input/output/hidden/reader/resource/semantic contracts participate in
compatibility review; code and declared proof fixtures may change under the
recorded policy. A reader data proof is explicitly not full qualification.
Its implementation fingerprint includes the parent resource/process supervision,
prepared-file grants and native-input grants as well as the copied worker; a
change to those confinement implementations invalidates prior reader proof.
The unit evidence uses synthetic files, a stand-in installer and stand-in reader
worker with genuine preparation, immutable intake, Project retention and lifecycle
admission. The genuine BDV package/runtime workflow remains staged, not executed.

Local all-reader bundles with explicit reference checks may enter the same lifecycle. Structural eligibility
is not proof: exact sealed reader checks, trust/rights, changed-fact consent, pins and first-check rollback
still govern admission. Unsupported scientific packages and public distribution retain their separate refusal.

Supported nonreader interactive updates use the current maintenance target for an explicit
**Try update** from a saved Project. The exact prepared generation remains beside the active
one; normal choices and running sessions keep the active generation. Controls, Save tool and
reopening must produce a new complete local receipt for every declared member. Models exposes
**Review update results → Enable this update for Apply**. Acceptance appends the exact generation
and receipt digest to `decisions.jsonl`; it never replaces the active own-use acceptance marker.
The current target, changed requirements, trust/rights, machine fit and explicit acceptance
remain separate gates. Apply carries the card's generation/digest and refuses stale receipts.
Automatic policy cannot promote these interactive updates, and Use newest cannot bypass Apply.

Apply uses the existing attempt journal, selection pointer, generation leases and first-check
rollback. Its bounded check restores each member's exact retained native data/tool state and
renders once through the confined scientific host. It never replays controls, Python, scientific
operations or exports. Missing/changed retained state or a failed restore/render records failure
and rolls back, holding automatic policy where one exists. Resume retains the same recovery
contract. This functional check does not establish scientific correctness, movie fidelity or
preservation of a separate in-flight session; those require actual workflow evidence.
