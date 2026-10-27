> **Current release process (October 21):** [RELEASING.md](RELEASING.md) is the operating procedure.
> Beta has a separate public updater. Build and publish are explicit separate commands; merges and tags publish nothing.

# HookeUI.app: the Mac app

The app people download. Inside it is this repository's app (only the files the running app reads), its studio
already built, uv, an LGPL FFmpeg, the camera helper and Sparkle. Internal names (`MICROMIND_*`, `~/MicroscopeMind`)
are unchanged; only the outside is HookeUI.

## What happens on a Mac

The native launch screen plans each launch before it shows anything, from the same marker files each step checks
(`main.swift` `Services.plan`: the bundle's own `uv.lock`, hashed, and `envs/<lock>/` directly, since an update's new
runtime folder does not exist yet). Its words and the plan-to-stages mapping are `HookeUI/SetupWords.swift`, drawn by
`HookeUI/SetupView.swift`, both checked by `make native-test` (`tests/setup-words`, which can also draw each screen:
`<built test> --draw DIR`).

- **Only planned stages show**, and a stage is ticked only when its step reports that it ran; a planned stage that
  turns out to have nothing to do is removed, never ticked. No estimated percentage, size or startup-time promise.
- **First launch:** before anything starts, one question: "Hooke downloads its scientific software first", with the
  build's measured numbers (packages, sources, download and disk size) and **Download and Open** (Return) or **Quit**.
  It is asked only when this Mac has no Python of any lockfile, so an update never waits on it; once agreed in a run,
  Try again does not ask again. Then Set up · Download · Get ready · Open, the download showing uv's own reports in
  plain words ("Downloading torch (121.4 MiB)"). The native screen never says Welcome: the workspace's first-launch
  Welcome is the one welcome.
- **Update, same software:** Set up <version> · Open, "Updating to Hooke Beta <version>" / "Setting up the new
  version. Nothing to download: its scientific software is unchanged."
- **Update, changed software:** Set up <version> · Download · Get ready · Open; "Downloading this version's software
  changes", with uv's reports and no invented size (what downloads is what the uv cache lacks).
- **Ordinary launch:** no stages, only "Opening your workspace" and its lines (the camera connection, the workspace,
  your analysis tools).

Failure retains Show log, Quit and keyboard-default Try again. Reduced Motion suppresses the spinner; Increase Contrast
removes the background glow. The Hooke mark and dark workspace palette are shared with Studio.


- **First launch** (about 2 to 3 minutes on a clean Apple Silicon Mac. Measured in fresh macOS 26 VMs: 118 s and
  187 s; 650 s once while other work loaded this Mac to 45 to 72 on 14 cores):
  1. The source is copied to `~/Library/Application Support/HookeUI/runtime/<version>-<content>/`, once per version.
  2. After the person agrees, uv makes Python 3.13 and the packages from the hashed `services/uv.lock`, into
     `~/Library/Application Support/HookeUI/envs/<lock>/`. The build measures the size the question says
     (`runtime_download.py`, into `packaged.json`'s `runtimeDownload`, outside the content hash): it makes that Python
     with the bundled uv and sums uv.lock's sizes of the wheels really installed, plus the Python archive uv downloads.
     For 0.4.1's lockfile, measured 2026-10-26: 126 packages (299 MB) from pypi.org and CPython 3.13.5 (16 MB) from
     GitHub (astral-sh/python-build-standalone), about 310 MB to download and about 1.2 GB on disk; 11 s on a fast line.
  3. Every module is compiled and imported once: macOS checks each new native library the first time it loads (the
     55 to 138 s of the first launch).
  4. `scripts/studio.sh start` starts what `make start` starts, in its packaged mode: no Node, Xcode tools or system
     Python; the bridge runs on the app's own Python.
  5. The studio opens in the app's window.
- **Later launches:** 8 to 45 s, mostly the analysis workers loading.
- **Your data** stays in `~/MicroscopeMind`, as with `make start`; the log is `~/Library/Logs/HookeUI/HookeUI.log`.
- **Quitting** stops every process it started.
- **Developer tools:** on a Mac without them, running `git`, `xcrun`, `python3` and the like opens macOS's
  install-the-developer-tools dialog. The app's PATH puts guards first that say "isn't installed" instead.
- **Ports:** taken by something else, 8770 and 8765 move to the next free ports.

## Updates (Sparkle 2)

The app can't later update itself into having an updater, so every version carries it.

- **Checks:** at launch and every 4 hours, every copy reads `SUFeedURL`, the latest release's `appcast.xml` on
  [wpulier/hookeui.org](https://github.com/wpulier/hookeui.org/releases), over HTTPS.
- **Your choice:** Sparkle offers the release notes before downloading. Choose Install Update or Remind Me Later;
  after downloading, choose when to install and relaunch. HookeUI → Check for Updates… checks immediately.
  Automatic installation is disabled, including for users whose older preferences enabled it. Every appcast also
  requires an interactive offer for older builds, so the original beta cannot silently install this transition.
- **Reminder:** available updates appear quietly above Settings in the packaged workspace. Settings also offers Check for updates; Sparkle remains the release/download/install authority. Before the workspace loads, the native offer remains the fallback.
- **Before restarting:** Hooke reads what restarting would stop or lose (the workspace's unsaved notes and arrangements,
  and its running work in every Project: runs, researchers, Record, voice, the Assistant's answer, unsent messages,
  tool sessions, downloads and installs), then asks once. With nothing at stake, "Restart Hooke to finish updating?"
  has **Restart Now** as the default (Will, 2026-10-26: "Restart Now as the default when nothing is running"; until
  0.4.0 the default was Keep Working). Running work is named, with no Return default ("Stop and Restart" or "Later").
  Unsaved work, a lost connection or a read that fails refuses the restart. Status is read again after the choice.
  Later keeps the update downloaded: the workspace says Restart to update. The update does not certify that every
  plugin's transient state is saved. Saved Projects live outside the app and remain available after restart.
- **Quitting:** every quit reads the same way: nothing at stake quits at once; running or unsaved work is named with
  Cancel and Stop and Quit (or Quit Anyway). A waiting update installs as Hooke quits.
- **What's new:** the installed version shows its notes once, after the workspace opens.
- **Original beta:** `https://hookeui.org/beta/appcast.xml` is a signed bridge to 0.1.2. That release uses the normal
  latest-release feed above; keep the bridge available for people who have not updated yet.
- **Trust:** the feed itself is signed (`SURequireSignedFeed`), and so is every dmg. The EdDSA key's public half is
  `sparkle-public-key.txt`; the private key is `~/.config/hookeui-release/sparkle_ed25519.key` on the release Mac,
  and the CI secret. **Back the private key up**: without it, no installed copy will accept another update.
- **Python, per lockfile:** each `uv.lock` gets its own Python under `envs/<lock hash>`. A version that keeps the
  lockfile reuses it and starts in seconds. A new lockfile builds beside the old one, which stays for going back.
  An interrupted build has no completion marker, so it is made again at the next launch.
- **Historical measurement:** an earlier VM update took 23 s. This is not a startup promise or qualification of
  every later release. The 0.1.1 preservation receipt is recorded in the milestone handoff.
- **Your data:** never in the bundle. The runtime is a copy in Application Support, and the user's data is in
  `~/MicroscopeMind`; the bundle's signature still verifies after use and after an update.
- **Moving into Applications:** opened straight from the disk image, the app offers to move itself there, so it can
  update itself.
- **Testing an update:** point a copy at another feed with
  `defaults write org.hookeui.HookeUI HookeUIFeedURL http://.../appcast.xml` (a Beta: `org.hookeui.HookeUI.beta`).

## What actually triggers an update

Verified 2026-10-19 against `.github/workflows/mac-app.yml`, `build.sh`,
`publish.sh`, `Info.plist` and `Updates.swift`:

| Action | Effect on installed apps |
|---|---|
| Commit or merge to application `main` | None. Changes source only. |
| Build/sign/notarize, or manually run CI with publish off | None. Produces a candidate artifact only. |
| Push an `app-v*` tag | None. The legacy publishing trigger was removed. |
| Explicitly publish a verified candidate through `release-channel.py` | Publishes its signed artifact and appcast after approval; see RELEASING.md. |
| App performs its next scheduled or manual check | Reads the published feed; an eligible newer signed build becomes an offer. The user chooses installation/restart. |

The configured schedule is automatic checking at startup/periodically (four-hour
interval); publication is not a push notification and does not guarantee an
immediate popup in an already-running app. **HookeUI → Check for Updates…** checks
now. From 0.1.2, Settings also exposes checking and the quiet available-update
reminder. A 0.1.1 client must first install 0.1.2 before it has those new controls.

The normal feed is
`https://github.com/wpulier/hookeui.org/releases/latest/download/appcast.xml`.
It is shared by installed users. An offer published there is not limited to Will.
The original beta's website feed remains a signed compatibility bridge and must
be kept aligned. Merging website `main` deploys website files, including that bridge;
it does not build/sign/publish an application release.

Tags and merges no longer publish. The legacy `mac-app` workflow is build-only in source and disabled in GitHub, including historical tag triggers.
Use the explicit channel prepare/publish procedure; protected hosted publication remains disabled.

## Milestone delivery to the owner

Use the installed app's real updater for future owner milestone acceptance. A dev
browser, `make start`, or an installer built but not published is not evidence that
the owner's installed app has the change. Use Beta first, then explicitly promote accepted source to Stable:

1. Finish a coherent milestone, run affected checks and record unresolved failures;
   satisfy existing merge gates or obtain a specific exception.
2. Freeze the exact source, version, build number and dependency locks. Build/sign/
   notarize once. Do not rebuild an already approved artifact silently.
3. In an isolated profile/VM, exercise the changed journey and real update path;
   compare saved work and verify reopen. Preserve failures. No repeated unchanged
   suites seeking a lucky pass. Do not operate/restart the owner's app as a test.
4. Present the exact artifact/hash, release notes, known issues and destination for
   release approval. The current normal-feed release reaches everyone on it.
5. Publish that artifact/feed, verify anonymous download hash and feed signature,
   update the website/legacy bridge, and record source/release/evidence links in
   the existing milestone handoff and release ticket.
6. Tell Will the offered version and one changed task to try. Will uses Check for
   Updates, decides when to restart, checks the installed version and tests that
   task. Record owner acceptance separately from developer/VM verification.

The 0.1.2 receipt in
the milestone handoff
records actual update delivery, preserved scientific files and remaining limits.
This is a working milestone-delivery mechanism, not proof that every future
migration or arbitrary plugin's unsaved state survives. Saved notes/layouts block
update restart when pending or conflicted; active work stops and transient state
may be lost. Maintain explicit user consent and do not promise automatic resume.

## Channels: Stable and Beta

Two apps come from one source. `channels.json` lists each release channel and `channels.py` checks and applies it;
`build.sh` builds one with `RELEASE_CHANNEL` (default `stable`), or `make app-build VERSION=… RELEASE_CHANNEL=beta`.
Building publishes nothing.

| | Stable | Beta |
|---|---|---|
| App, bundle identifier | HookeUI.app, `org.hookeui.HookeUI` | HookeUI Beta.app, `org.hookeui.HookeUI.beta` |
| Camera helper | `org.hookeui.camera` | `org.hookeui.camera.beta` |
| Runtime, Python, uv cache | `~/Library/Application Support/HookeUI` | `~/Library/Application Support/HookeUI Beta` |
| Log | `~/Library/Logs/HookeUI` | `~/Library/Logs/HookeUI Beta` |
| Projects, saves, assets, catalog, environments | `~/MicroscopeMind` | `~/MicroscopeMind-Beta` |
| Saved API keys (Keychain service) | `MicroscopeMind` | `MicroscopeMind Beta` |
| Keys file | `~/Desktop/micromind.txt` (as before) | `~/MicroscopeMind-Beta/micromind.txt`, only if put there |
| Model downloads and caches | the Mac-wide ones (Hugging Face, Cellpose, micro-sam, torch) | its own, under its Application Support |
| Preferred ports (orchestrator, camera) | 8770, 8765 | 8870, 8865 |
| Preferences, WebKit data | by bundle identifier | by bundle identifier |
| Feed | latest release on `wpulier/hookeui.org` | latest release on `wpulier/hookeui-beta` |
| Update key (public half) | `sparkle-public-key.txt` | `sparkle-public-key-beta.txt` |

What keeps them apart:

- **Data:** the launcher (`main.swift`) takes every folder, port, name and the Keychain service from its Info.plist,
  which `channels.py` writes from `channels.json`. A Beta never opens or migrates Stable's Projects; testing work
  there means exporting it from Stable and importing it into Beta.
- **Processes:** each app stops only the processes of its own runtime folder (`scripts/studio.sh`'s `in_checkout`),
  so quitting or updating Beta never stops Stable's workers.
- **The bundle:** Move to Applications installs `HookeUI Beta.app`, never over `HookeUI.app`.
- **The feed:** `channels.py` refuses a channel that shares a folder, identifier, Keychain service, port, releases
  repository, feed or update key with another, and `build.sh` refuses to build one channel with another channel's
  feed or the original beta's bridge (`https://hookeui.org/beta/appcast.xml`, read by Stable's identifier).
  Beta's own releases repository means a Beta release can never become the latest Stable release.
- **No shared credentials by default:** Beta reads no Mac-wide keys file and no Stable Keychain item. Giving it a key
  is a deliberate act: Add key in Beta, or a keys file in `~/MicroscopeMind-Beta`.

Stable's identity is unchanged: its rendered Info.plist has every earlier key and value, plus the seven `HookeUI*`
channel keys (`services/tests/test_mac_channels.py`).

Beta's permanent public key is `sparkle-public-key-beta.txt`; its private key lives only in the release Mac's
Keychain and `~/.config/hookeui-release/sparkle_ed25519-beta.key`, with a private backup. Published Betas use this key.
Throwaway-key test builds are not public channel members and need one initial replacement with the public Beta.

Two checks a release runs before it publishes, usable on their own:

- `release_number.py --commit-count N --feed <stable feed> --feed <beta feed>` gives the next build number: above
  every build either channel has published, and never below the commit count. Commit counts alone don't order builds
  across branches.
- `swift verify_update.swift <channel public key> appcast.xml <dmg>` checks that the feed and the dmg are signed by
  that channel's update key (CryptoKit, no Sparkle needed), so a Beta signed with Stable's key, or the reverse, stops
  there.

**Status (2026-10-20):** identity, isolation and the build path are in the source and checked by unit tests and a
Swift type check. One build-only Beta was built from this source (ad-hoc signed, not notarized, a throwaway update
key): `HookeUI Beta.app` and `HookeUI-Beta-0.2.0.dmg` carry Beta's identifiers, folders, ports and feed, and its
appcast verified against that key. No Beta has been run beside Stable yet, so side-by-side behaviour is not yet
verified; that needs a clean VM (below). Beta's release, promotion and recovery are in [RELEASING.md](RELEASING.md)
and `.github/workflows/mac-app-channel.yml` and `mac-app-feed-recovery.yml`, which do nothing until Will has done its
optional hosted setup. This paragraph records October 20 history; use RELEASING.md for current activation.

Known limits: two running apps are two camera bridges, so only one of them should use the iPhone at a time. Beta's
icon is Stable's; the name in the menu bar, Dock and window says Beta.

## The proposed branch and release model

Requested outline, 2026-10-19. The Beta app identity above implements its isolation section; Beta publication, promotion and release numbering are now available through the explicit release-Mac
procedure in [RELEASING.md](RELEASING.md). Hosted automation remains disabled until its protected setup is possible.

Recommended for this team: short-lived feature branches → reviewed `main` →
explicit milestone candidate → beta acceptance → explicit stable release.
Keep `main` releasable and unfinished features out of ordinary flows. Stable users
receive only a selected, qualified release; their app does not run the moving main
branch. A permanent `dev` branch is optional, not the mechanism that keeps their
installed app stable. If sustained integration work needs one, use feature → dev
→ tested release PR to main, with stable publication still a separate approval.
Bring stable hotfixes back into dev to avoid divergence.

| Surface | Audience | Intended trigger |
|---|---|---|
| Local development | Developers | Local code changes; no published offer. |
| Hooke Beta (separately installable signed app) | Will and opt-in testers | Explicit milestone release to a beta feed after focused checks. |
| Hooke (stable channel) | Ordinary researchers | Approved stable release of the beta-tested source after final artifact verification. |

Beta must have a distinct bundle identity, visible app/version/channel label,
update feed, preferences, data/runtime roots and process ownership. It must not
replace the stable app, stop its workers or write/migrate its Projects implicitly.
Use copied/imported Projects for testing; share only deliberately immutable caches
where safe. A GitHub prerelease flag or a different icon alone does not provide
this isolation. No silent switching between channels.

A separately identified Beta bundle and stable bundle are different signed bytes.
Promote the same tested source/dependency locks, then verify the exact stable
package; do not claim byte-identical promotion across app identities. Use explicit,
monotonically increasing release build numbers across all branch/channel paths.
Keep earlier installers and backed-up Projects; fix forward if a migration makes
downgrading unsafe. Automatic background download may be a later preference;
installation/restart remains user-controlled.

### Comfy Desktop precedent (checked 2026-10-19)

The current [Comfy Desktop release documentation](https://github.com/Comfy-Org/Comfy-Desktop#releasing)
describes manually starting a version-bump workflow with `stable` or `rc`, reviewing
and merging its release PR, then tagged builds through ToDesktop. Their
[release-PR workflow](https://github.com/Comfy-Org/Comfy-Desktop/blob/main/.github/workflows/release-from-pr-label.yml)
requires a merged same-repository PR labeled Release and a version change before
creating a tag. An ordinary feature merge is not that release trigger. Their
[build workflow](https://github.com/Comfy-Org/Comfy-Desktop/blob/main/.github/workflows/build-release.yml)
publishes stable/RC artifacts, and their README documents separate development and
packaged-app settings directories. This supports separating source integration,
release selection and test state; it does not establish that Comfy uses Hooke's
proposed separate Beta app or a feature → dev → main branch topology.

## Releasing

[RELEASING.md](RELEASING.md) is the single operating procedure: explicit Beta preparation/publication, then accepted
source promoted to a separately verified Stable artifact. `release-channel.py` uses the existing `build.sh` and
`publish.sh` from a pinned source with monotonically increasing build numbers. It never rebuilds during publication.
`NOTES=/path/to/notes.md` supplies release notes. `TOOLS=/path/to/cache` reuses cached build tools.

The release Mac already has its Developer ID and notarization profile. Hosted CI is optional and currently disabled:
required-reviewer environments are unavailable on the current private-repository plan, and CI signing/publishing
credentials are incomplete. Do not upload a broad personal token or enable unprotected publishing to work around it.

## What ships, and what doesn't

- **The snapshot** (`snapshot.py`): only what the running app reads. It reads the commit's Git objects, never the
  working tree. It applies the public-candidate builder's rules:
  - its named private files and generated results stay out;
  - pictures ship only when listed;
  - the owner's recording list and backup destination are removed from `assets/manifest.json`;
  - the verdict ledger ships empty.

  It then scans every shipped file for keys, tokens and home paths, and stops on any finding. Its report is
  `build/snapshot/report.json`.
- **Included media:** the three leaf crops in `catalog/models/*/example-leaf-crop.png` come from the owner's own
  leaf recording. They are the catalog's pinned test inputs (without them three entries can't be read). They are
  listed in `MEDIA`; remove them there before a public release if they aren't cleared.
- **FFmpeg:** `ffmpeg.sh` builds 7.1.5 from its signed release as a static LGPLv3 build
  (VideoToolbox for H.264; no libx264). `stream-deps.sh` builds pinned SRT 1.5.4 and OpenSSL 3.5.9
  statically, with encryption enabled. Only system libraries are dynamically linked. The build host
  needs pkg-config; uv provides a pinned CMake build tool. End users need neither.
  A recipe hash invalidates older decoder caches. `verify_receiver.py --smoke` requires SRT input/output
  and verifies one encrypted synthetic frame before packaging. Actual Blackmagic/native permission
  checks remain separate. Source archives, recipes and license notices accompany each release.
  Earlier packages omitted SRT and could not receive Blackmagic video; this was a packaging limitation,
  not something a phone reconnect could repair.

## Testing on a clean Mac

Never in your own session: `studio.sh` stops every copy of the app it owns first. The test VM is Tart's
`ghcr.io/cirruslabs/macos-tahoe-vanilla` (no developer tools, no Homebrew). Two catches:

- **Gatekeeper is off in that image** (`spctl --status` says "assessments disabled"), and its Developer ID rules are
  disabled too: after `sudo spctl --global-enable` alone, `spctl --status --verbose` says "developer id disabled", the
  policy is App Store only and every notarized download, Hooke's included, is refused ("Apple could not verify…";
  macOS 26.6.2, 2026-10-19). `spctl` can no longer change that rule; in the guest choose System Settings → Privacy &
  Security → Allow applications from → **App Store & Known Developers** (the admin password is `admin`), and check
  that `spctl --status --verbose` says "developer id enabled" before an install test.
- **No developer tools:** an observer's command over SSH that runs one of `/usr/bin`'s developer-tool stubs (`strings`,
  `git`, `python3`…) opens the install dialog itself; don't count that dialog against the app.
- **The guest's Screen Sharing** (`sudo launchctl bootstrap system /System/Library/LaunchDaemons/com.apple.screensharing.plist`)
  gives screenshots and clicks; Virtualization.framework's own VNC server crashed the VM twice.

First-install regression: from a mounted disk image, choose **Move to Applications**
and verify that copying finishes and the installed copy reaches bootstrap. Closing
the installation alert must not trigger last-window shutdown before the main
window exists. Also exercise **Not Now**, then quit normally. A successful signed
build alone does not verify this AppKit lifecycle path.

Scientific folder selection uses a native, bounded snapshot rather than WebKit's
`webkitdirectory` enumeration (which omits hidden files). It includes metadata such
as `.zattrs`, rejects links and special files, and checks source identity before
and after copying. The Studio receives only a short-lived token and ≤1MiB chunks
of the same versioned folder frame used by the ordinary import endpoint. Selection
has a 64 GiB aggregate storage ceiling and a 4,096-file limit, with a separate disk reserve.
These are transport admission limits, not demonstrated large-data workflow capacity.
Reader, native-output, memory and saved-work limits remain separate; actual large-data
and installed-app qualification remain open. Tokens are
restricted to the exact Studio origin/main frame, released after transfer, expired
when idle and cleared on quit. The OS chooser grants the folder; JavaScript cannot
supply a path. Run `swiftc -swift-version 5 packaging/macos/HookeUI/FolderSnapshot.swift
packaging/macos/tests/folder-selection.swift -o <private-test-executable>` and the
resulting executable for focused native snapshot checks. Packaged native UI remains
required to verify selection, import, preparation, acceptance and reopen together.
