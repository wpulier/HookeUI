# Releasing HookeUI and HookeUI Beta

## Shared everyday process — both developers

**Work → dev → frozen Beta → Will accepts → the same source on main → Stable.**
Dev may keep moving while Beta is tried. The release owner promotes the accepted
commit, never the newer dev tip. Merges/tags alone do not update anyone. Both developers
use this file and the commands below; no second release checklist or tracker.

Write changes, a short ordinary-UI walkthrough and known limitations **once**, in the
version's release notes. Link existing evidence; do not rerun unchanged tests to move
between branches. During development use affected checks. For a candidate, reuse valid
recorded gates and check only invalidated boundaries. Owner acceptance asks: update,
use the changed workflow, Save and reopen; retain any failures and their scope. It is
not acceptance of every future plan requirement or permission to restart the owner's app.

### Release Beta

Freeze a full SHA from checked `origin/dev`. Use the existing release Mac credentials
and optional `TOOLS` cache. `--publish` explicitly authorizes publication; omit it when
approval still needs an exact prepared artifact. No command below runs test suites.

```sh
python3 packaging/macos/release-channel.py beta VERSION FULL_DEV_SHA \
  --notes packaging/macos/release-notes/VERSION.md --evidence HTTPS_EVIDENCE_LINK \
  --companions /private/path/companions.json --out /private/path/beta-VERSION --publish
```

Omit `--companions` if there are none. Preparation freezes notes and companions. The
command signs/notarizes once, publishes the same bytes, and confirms the public offer.
Running it again with the same directory reuses the candidate; it never rebuilds it.
For an older unpublished candidate, supplied notes are frozen as metadata without
rebuilding its signed artifact. A partial build is preserved for inspection, not silently overwritten. An unconfirmed
public offer still follows the recovery section below, never a blind retry.

### Promote the accepted Beta

Record Will's actual acceptance. Open/review the main promotion PR with its head pinned
to the accepted Beta commit. If dev advanced, use a branch at that exact commit. Then:

```sh
python3 packaging/macos/release-channel.py promote /private/path/beta-VERSION \
  --accepted "Will: updated Beta VERSION (BUILD), tried CHANGED_FLOW, saved and reopened" \
  --promotion-pr PR_NUMBER --evidence HTTPS_ACCEPTANCE_LINK \
  --out /private/path/stable-VERSION --publish
```

Quote only what Will actually confirmed; a narrower acceptance stays narrower. The
command verifies the published Beta and prospective merge tree, merges that reviewed PR with an expected-head
check, verifies main's tree equals the accepted source, then builds/signs/publishes
Stable using that source and those companions. It never chooses a new dev revision,
squashes history or overrides branch protection. Omit `--promotion-pr` if the exact
source is already merged. `--notes` can supply Stable wording; otherwise frozen Beta
notes are reused. Older candidate folders without frozen notes require `--notes`.
Do not create or merge a promotion PR before the owner accepts its Beta.

### One generated handoff

`OUT/release-record.md` is generated from the existing immutable `release.json`, intent,
frozen notes and confirmed publication receipt. It contains version/build/source,
download/release/evidence links, companion identity, actual Beta acceptance when promoted,
walkthrough and limitations. After publication it becomes the GitHub release description,
shared by both developers. Edit source notes before preparing; do not edit the generated
record or the frozen notes. Current/tickets link that record and name only the next
owner/blocker; do not copy the whole release ledger into several planning files.

```sh
python3 packaging/macos/release-channel.py record /private/path/beta-VERSION
```

This regenerates the handoff locally without builds, tests or publication. If only
GitHub notes synchronization failed **after confirmed publication**, use `record DIR
--sync`; that checks the published receipt and refreshes notes only. Never rebuild a
published candidate to fix a handoff. The low-level prepare/publish commands below
remain available for artifact-specific approval and recovery.

The public download page links to the current Stable release, so version changes need
no website edit. Its release record supplies the current files and limitations.

**Finish line:** the public feed/download bytes are confirmed and the owner receives
one update instruction plus the changed-workflow walkthrough. Installed Stable
acceptance remains distinct from publication. No percentage or broader parity claim
substitutes for either. The phased plan and all qualification obligations stay intact.

## Design pass to Beta to Stable checklist

**October23 delivery update:** Will accepted the installed Beta0.2.3(4465), source
`12bea2035`, and authorized its main promotion and Stable publication. PR#446 merged
the identical source tree to main (`903916fd6`). Stable0.2.3(4466) is prepared from that
accepted Beta source with unchanged companions; publication and anonymous offer/byte
verification passed. Stable owner installation remains separate. The final
visual design pass follows in the next Beta, per Will, rather than holding this bounded
functional release. Performance/cloud/large-data and full Part1 limitations remain open.
See Current/ticket62 for the authoritative delivery record; the older4437 paragraph
below is preserved historical preparation evidence, not the current release status.


There is one integration branch, `dev`, and one accepted release branch, `main`.
Both apps use frozen bundled backends: neither follows a branch as it moves.

| Step | Owner and closing record |
| --- | --- |
| Converge | Design and Study owners start from current `origin/dev`; root reviews/merges their checked changes into dev. Ticket 63 lists every included head and any intentionally preserved follow-up. Uncommitted owner designs must be reconciled, never overwritten. |
| Freeze | Root records the combined full dev SHA in ticket 62. No missing design/research changes may be implied by an older candidate. Use affected local checks while editing; dispatch `tests` on the frozen candidate branch once, recording its resolved SHA and run. Quiet simulations and applicable capability checks retain their own gates. |
| Prepare Beta | Release owner uses `prepare beta` below with that SHA, frozen dependencies and companion manifest. Verify the exact package and record the walkthrough, limitations, DMG/manifest hashes. Preparing or merging offers no update. |
| Offer Beta | Will approves the exact artifacts/destination. Release owner uses `publish beta` on the retained output without rebuilding, then confirms the anonymous feed and download bytes. The installed Beta checks its own feed; users choose install/restart. |
| Accept and promote | Will's installed acceptance names the Beta version/build/source. Pin the promotion PR to the accepted Beta commit and merge without squash/rebase; dev can continue moving. Check `git diff --exit-code ACCEPTED_BETA_SHA origin/main` and ancestry. Any extra source changes or conflict resolutions return through dev/Beta; never promote the moving tip blindly. |
| Offer Stable | Prepare from `ACCEPTED_BETA_SHA --promote-from BETA_VERSION`, not an arbitrary newer main SHA. Preserve locks/companions, verify Stable channel and saved-work differences, obtain approval of its distinct artifact, publish and confirm its public offer. |

For the manual paid check, use Actions → **tests** → Run workflow → the frozen
candidate branch (normally `dev`). Confirm the run's SHA before accepting its result.
Routine PRs and dev pushes no longer launch that suite once they contain the updated
workflow. Older branches must sync dev; main keeps its older workflow until promotion.
No running check is cancelled by this policy, and existing failures remain evidence.

As of October 23, Beta **0.2.3 (4437)** at `4dd5d70a3` is a verified **unpublished**
baseline; Beta 0.2.2 and Stable 0.2.1 remain live. The branch audit found missing
journal-save correctness work, so 4437 is not the converged publication target. Include
that repair, the incoming design pass and Study recovery in a new candidate. Keep 4437 and its receipts;
do not mutate its output directory or claim its evidence qualifies changed behavior.
See Current and tickets 62/63/65 for the active heads and remaining cloud/owner gates.
Cloud activation can remain an explicit limitation of a bounded local release; it does
not become passed, or full Part 1 completion, because that release ships.

## Everyday process (active release-Mac path)

**Feature branch → checked dev → explicit Beta release → Will accepts Beta → promote dev to main → explicit Stable release.**
Merging or pushing a tag publishes nothing. Neither app runs a moving branch or a hosted development backend:
each runs its bundled source revision locally. App updates happen only when a signed artifact and feed are published.
Users choose when to install and restart. Beta and Stable never silently switch channels.

Use this Mac's existing Developer ID, notarization profile and GitHub login. Beta has its own permanent Sparkle key,
public releases repository (`wpulier/hookeui-beta`) and feed. Stable remains `wpulier/hookeui.org`.
Private signing keys stay in Keychain and `~/.config/hookeui-release`; only their public halves belong in Git.

1. Merge the milestone into `dev` after the repository checks. Record the exact commit, checks and remaining limitations in
   the existing release ticket. Select a new three-part version, for example `0.2.2`.
2. Prepare Beta, using a **new** output directory (the command does not publish):

   ```sh
   python3 packaging/macos/release-channel.py prepare beta 0.2.2 FULL_DEV_SHA \
     --evidence https://github.com/wpulier/HookeUI/blob/FULL_DEV_SHA/docs/planning/open-source-successor/implementation/issues/62-production-launch.md \
     --out /absolute/private/path/beta-0.2.2
   ```

   Optional `TOOLS=/absolute/path/to/cached-tools` reuses FFmpeg, uv and Sparkle. `NOTES=/path/to/notes.md`
   supplies release notes. These are the existing build tools, not a second packaging system.
3. Verify the exact signed Beta candidate in an isolated profile/VM. Record its `release.json` hash and limitations.
   After Will approves that candidate and Beta destination, publish the **same bytes**, without rebuilding:

   ```sh
   python3 packaging/macos/release-channel.py publish beta \
     --out /absolute/private/path/beta-0.2.2 --sha256 APPROVED_DMG_SHA256
   ```

4. Testers initially install [HookeUI Beta](https://github.com/wpulier/hookeui-beta/releases/latest): open the DMG,
   drag the app into Applications in Finder, then launch that installed copy. Do not launch from the disk image.
   Verify this ordinary installation path; a terminal copy can remain macOS-translocated and cannot update.
   Subsequent Beta milestones arrive through its own updater. Local test Betas signed with a throwaway key need this
   one-time replacement; they cannot accept the permanent key. Preserve `~/MicroscopeMind-Beta` when replacing the app.
   Beta never reads or migrates Stable's `~/MicroscopeMind`. Import copies when needed; do not link the profiles.
5. After Will accepts Beta, freeze its dev revision and merge the reviewed dev → main promotion PR using a
   merge commit (no squash/rebase). The resulting main tree must match the accepted Beta source tree. Resolve any
   conflict or extra behavior on dev and repeat affected Beta acceptance before promotion. Prepare Stable from
   **the accepted Beta commit itself**, now reachable from main, with the same dependency locks:

   ```sh
   python3 packaging/macos/release-channel.py prepare stable 0.2.2 ACCEPTED_BETA_SHA \
     --promote-from 0.2.2 --evidence https://github.com/wpulier/HookeUI/blob/main/docs/planning/open-source-successor/implementation/issues/64-usable-core-baseline.md \
     --out /absolute/private/path/stable-0.2.2
   ```

   Promotion refuses a different source, unpublished/withdrawn Beta or unnotarized release. App identities differ,
   so Stable is a new signed artifact, not the Beta file renamed. Verify its updater and saved-work reopening.
6. After approval of the Stable hash/destination, publish it with the same `publish` command using `stable` and its
   output directory/hash. Verify the public feed and update the website download link. No merge automatically does this.

Both `prepare` and `publish` refresh and check the channel's remote source branch: Beta requires `origin/dev`,
Stable requires `origin/main` plus the exact published Beta source. A missing/unreachable branch fails closed.
The dev/main split changes source eligibility only; it does not migrate profiles or change existing releases.

`prepare` allocates a build number above **every** published channel build, including withdrawn releases.
`publish` checks the approval hash, retained source/channel/version/build and current numbering again; an intervening
release requires a fresh candidate. It uses `publish.sh` for signature verification, draft upload, downloaded-byte
comparison, activation and anonymous verification. A failure after publication must be investigated, not blindly retried.
A machine-wide lock serializes local prepare/publish operations. Only this release Mac is active; do not run a second
release host or enable CI concurrently. Keep the old installers and private acceptance receipts.

## Optional companion tools and example input

The existing companion ZIP delivery is an explicit release allowlist, separate from
scientific qualification. Prepare accepts `--companions /path/to/companions.json`:

```json
{"format":1,"sourceCommit":"FULL_PACKAGE_SOURCE_SHA","assets":[{"name":"tool.observationui.zip","sha256":"EXACT_SHA256","bytes":1234}]}
```

Use actual 40/64-character lowercase hashes. Each of one to eight assets is a safe
flat ZIP filename beside the manifest (at most128MiB each,512MiB total). No paths,
symlinks, duplicate/case-colliding names or undeclared manifest fields are accepted.
The manifest may also explicitly list a bounded example-input ZIP. Runtime profiles,
credentials, caches and prepared environments never belong in this allowlist.

Prepare freezes verified copies before the app build, retains them read-only under
`OUT/companions/`, and binds the original manifest SHA256 and package source commit
in both `release-intent.json` and `release.json`. Original author folders may then
change without changing the candidate. The package source can differ from the app
revision; retain the exact adapter/lock comparison and final packaged workflow
checks rather than claiming equal source identities or automatic qualification.

After inspecting all assets, publish requires both the existing DMG approval and
`--companions-sha256 EXACT_MANIFEST_SHA256`. A missing, changed or unexpected manifest,
asset or binding refuses before any release upload. The publisher attaches only
listed ZIPs plus `companions.json`, downloads and verifies all of them while the
release is still a draft, then checks the same bytes anonymously after activation.
A failed draft verification leaves the draft for inspection; a failed public check
is an unconfirmed offer under the existing recovery procedure. Never blindly retry.
No companion metadata or proof changes trust, candidate status or scientific gates.

Stable preparation must explicitly provide the exact manifest/assets of its accepted
Beta when that Beta has companions; omission, addition or substitution refuses.
Stable publication rechecks the binding against the published Beta again. A Beta
without companions retains the existing no-companion workflow. These commands do
not upload companions separately or alter any existing release.

## Activation record — October 21, 2026

Will authorized activating Beta updates and explicit promotion, with no updates triggered by merges.
The public Beta repository and permanent Beta key were created. No Stable feed or installed owner profile was changed
by channel setup. Public Beta 0.2.2 (3766) is live. The real public-feed update from 0.2.1, deferred restart and saved-work checks
are recorded in ticket 64.
Stable remains 0.2.1; this channel activation does not promote another Stable release.
The old `app-v*` publishing trigger was removed; `mac-app` is now build-only in source and disabled in GitHub.
Disabling it also prevents a tag on historical source from executing its old publication trigger. Leave it disabled.

**Hosted protected automation is not active.** GitHub rejected required-reviewer protection on this private repository
because its billing plan does not support it (HTTP 422). The incomplete environment created by that failed request
was removed. CI also lacks `MACOS_CERTIFICATE`, `MACOS_CERTIFICATE_PASSWORD`, `HOOKEUI_RELEASE_TOKEN`,
`SPARKLE_ED_KEY_BETA` and `HOOKEUI_BETA_RELEASE_TOKEN`. No broad personal token or private signing key was copied to
an unprotected runner. `HOOKEUI_CHANNELS_ENABLED` stays off. This does not prevent the explicit release-Mac path above.

The remaining sections describe the optional hosted setup. Do not claim it is active or dispatch publication until
its protections and narrowly scoped credentials exist. No account/billing change is required for the active local path.

## Optional hosted setup, authorized by Will

Each of these is a setting only Will makes. Until all are done, the two workflows do nothing.

1. **Beta's releases repository.** Create `wpulier/hookeui-beta`, public, so testers and the feed can download
   without signing in. Its latest release's `appcast.xml` is Beta's feed. A separate repository means a Beta release
   can never become Stable's latest release; a prerelease flag alone wouldn't guarantee that.
2. **Beta's update key.** On the release Mac: `generate_keys --account hookeui-beta` (Sparkle's tool, in
   `packaging/macos/build/sparkle/bin` after a build), then `generate_keys --account hookeui-beta -x
   ~/.config/hookeui-release/sparkle_ed25519-beta.key`. Commit the public half it prints as
   `packaging/macos/sparkle-public-key-beta.txt`. Back the private key up as Stable's is backed up; without it, no
   installed Beta accepts another update.
3. **Secrets** on `wpulier/HookeUI` (Settings → Secrets → Actions): `SPARKLE_ED_KEY_BETA` (the Beta private
   key) and `HOOKEUI_BETA_RELEASE_TOKEN` (fine-grained, Contents read and write on `wpulier/hookeui-beta` only). Stable
   keeps `SPARKLE_ED_KEY` and `HOOKEUI_RELEASE_TOKEN`. The workflows pass each job only its own channel's secret and
   stop if it is empty; they never fall back to the other channel's.
4. **Environments** (Settings → Environments), each with Will as required reviewer: `build-beta`, `build-stable`,
   `publish-beta`, `publish-stable`. An environment that doesn't exist is created by GitHub with no reviewers when a
   job first names it, so create all four before step 5.
5. **Turn it on:** repository variable `HOOKEUI_CHANNELS_ENABLED` = `true`.

## Optional hosted Beta release

1. Merge the milestone to `main` with its checks (`make check`, and `make capability-check` for model work). Write
   `packaging/macos/release-notes/<version>.md`. Put the commit, version and the evidence (checks, known issues) in
   the release ticket.
2. Actions → `mac-app-channel` → Run workflow: channel `beta`, the version, the full commit SHA, the ticket link as
   evidence, publish **off**. Approve `build-beta`. The run checks that the commit is on `main` and the version is
   new, takes a build number above every build either channel has published (`release_number.py`), builds, signs,
   notarizes, and verifies the feed and dmg against Beta's key (`verify_update.swift`).
3. Test that artifact in a clean VM (README "Testing on a clean Mac"): install it beside a Stable install; check that
   Beta's Projects and data are in `~/MicroscopeMind-Beta`, that Stable's `~/MicroscopeMind` is byte-identical before
   and after, and that quitting Beta leaves Stable's processes running. For an update test, point the VM's Beta at a
   private feed (`defaults write org.hookeui.HookeUI.beta HookeUIFeedURL …`).
4. Present the artifact's sha256 (from `release.json` in the run summary), destination (`wpulier/hookeui-beta`) and
   known issues to Will for approval.
5. With approval, run the workflow again with the same inputs and publish **on**; approve `build-beta`, then
   `publish-beta`. `publish.sh` checks the build is Beta's and signed by Beta's key, creates the release as a draft,
   downloads its files again and compares them with the build, and only then makes it the latest release. It then
   fetches the feed and dmg as anyone does and compares again. Exit status 2 means published but not confirmed: go to
   "A bad or unconfirmed offer".
6. Tell Will the version and one task to try. Will checks for updates in HookeUI Beta, chooses when to restart, and
   records acceptance in the ticket.

Two releases never run at once: both workflows and `mac-app.yml` share one concurrency group.

## Optional hosted Stable promotion

Stable is released only by promoting a Beta: same commit, same dependency locks. The two apps have different bundle
identifiers, so the Stable dmg is new signed bytes; it is verified on its own, never called "the same file".

1. Will has accepted the Beta in the ticket.
2. Run `mac-app-channel` with channel `stable`, the same commit, `promote_from` = the Beta version, the ticket as
   evidence, publish off. The check refuses unless that Beta release's `release.json` names this exact commit.
3. Verify the Stable artifact in a clean VM: install over the previous Stable, update, reopen saved work.
4. With approval, run again with publish on; approve `build-stable` and `publish-stable`. Then check the website's
   download links, and the original beta's bridge (`https://hookeui.org/beta/appcast.xml`) as the README says.

A hotfix follows the same path: a Beta release of the fix, then its promotion, both on the same day if needed.

## A bad or unconfirmed offer

1. Stop further offers: Actions → `mac-app-feed-recovery`, with the channel, the last good version, the bad version
   and why. Approve `publish-<channel>`. It checks that the good release still verifies against the channel's key,
   marks the bad release withdrawn (a prerelease with the reason in its notes, never deleted), makes the good one the
   latest again, and checks the feed as anyone fetches it.
   Without the workflow: `packaging/macos/feed-recover.sh <channel> GOOD BAD "why"`.
2. Copies that already installed the bad version keep it. Withdrawing doesn't undo an installation, and a lower build
   is never offered as an update. Fix forward: release a fixed version through Beta. Its build number is above the
   withdrawn one, because `release_number.py` reads every release, withdrawn ones too.
3. Never delete a release, a dmg or a user's Projects to repair an update. An older app may not read newer Projects;
   promise going back only for Project formats verified to be compatible.
4. Record what happened, what was offered, to whom and for how long, in the release ticket.

## Legacy commands

`mac-app.yml` has no push/tag trigger and no publishing step. `make release` / `release.sh` now point to the explicit
channel procedure rather than combining a build and immediate Stable publication. `publish.sh` remains the internal
publication primitive; developers use `release-channel.py` so promotion, exact-artifact approval and numbering are
checked together. A future cloud cutover must not restore merge/tag publication or bypass candidate approval.
