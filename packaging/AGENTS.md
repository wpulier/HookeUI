# The Mac app and its releases

HookeUI.app is how people get this app: a signed, notarized dmg that runs the studio on a Mac with no developer tools
and keeps itself up to date (Sparkle). Everything is in `macos/`; [macos/README.md](macos/README.md) is the reference.

## Shipping an update

Official publication has moved to private `wpulier/hooke-web/desktop-release`.
See `macos/RELEASING.md` for branch and ownership boundaries. Do not run historical
publisher helpers in this core checkout for official releases. Merges and tags
never ship. The private publisher uses the accepted core commit for both native
and scientific runtime code; the core remains runnable without a Hooke account.

## What must stay true

- **The updater key:** every release is signed with the same Sparkle key; its public half is
  `macos/sparkle-public-key.txt`. Releases are signed with the private key: on the release Mac,
  `~/.config/hookeui-release/sparkle_ed25519.key`, and the CI secret `SPARKLE_ED_KEY`. Lose it and no installed copy
  accepts another update. Never print it.
- **Nothing writes inside the app bundle:**
  - the runtime is a copy in `~/Library/Application Support/HookeUI`;
  - the user's data stays in `~/MicroscopeMind`;
  - the bundle's signature must still verify after use.
- **Blackmagic receiver:** FFmpeg includes pinned static SRT and OpenSSL with encryption support.
  Every packaged build checks both SRT directions and receives an encrypted synthetic frame.
  This prevents missing-transport releases; it does not qualify a physical phone. Source archives,
  build recipes and LGPLv3/MPL/Apache notices ship through the existing release process.
- **Camera discovery:** the native Info.plist declares `_http._tcp` in `NSBonjourServices`,
  alongside its local-network purpose string, for Blackmagic's camera-control endpoint.
  Browser/terminal discovery does not qualify the packaged app's network permission.
- **A clean Mac has no developer tools:** the app must never need git, Xcode tools, Node or the system Python.
  - The launcher's PATH guards keep `git`, `xcrun` and the like from opening macOS's install dialog.
  - `scripts/studio.sh`'s packaged mode (a `packaged.json` beside it) skips the checks and builds those need.
- **The snapshot ships only what the running app reads** (`macos/snapshot.py`). A new runtime folder or file the app
  reads must be added to `RUNTIME`. Managed research provider/scientific runtime modules ship;
  their deployment recipes, setup profiles and developer README do not. Media ships only when listed in `MEDIA`. Its privacy scan must stay clean.
- **Build numbers grow:** Beta releases from checked dev; Stable uses its accepted source promoted to main; `release-channel.py` allocates `CFBundleVersion` above every
  published build in both channels, including withdrawn releases. A same-source update still gets a larger build.
- **Channels stay apart:** a new folder, port, Keychain service or Mac-wide cache the app uses comes from its
  channel (`macos/channels.json`, `main.swift`'s `Channel`), never a fixed name, so a Beta never shares Stable's.
  Stable's identity never changes: installed copies update only within their own bundle identifier.
- **The launch screen claims only what ran** (ADR 0006; `macos/README.md` "What happens on a Mac"): each launch is
  planned from the same marker files its steps check, only planned stages show, and a stage is ticked only when its
  step reports it ran (`HookeUI/SetupWords.swift`, checked by `make native-test`). The first launch asks once before
  the required download, with the build's measured numbers (`macos/runtime_download.py`), never typed ones; an
  update never waits on that question. A new step gets a stage, words and a check there.
- **Testing:** never launch the app in the owner's own session (it shares `~/MicroscopeMind` with the live app). Use a
  clean VM, as the README's "Testing on a clean Mac" says.

- Update reminders come from Sparkle through an origin-checked main-frame bridge.
- **Restarting into an update, and quitting** (`HookeUI/UpdateWords.swift` LeaveFlow, the words checked by
  `make native-test`): before either, the app reads the workspace (`window.hookeUpdateReadiness()`:
  `{blockers, running}`, read from each kind of work's own source, `apps/studio/src/restart-readiness.ts`) and its own
  downloads and installs, showing "Checking your work…" meanwhile, and reads again after the person chooses to go on.
  - Restart (Sparkle's Install and Relaunch, or Restart to update): nothing at stake shows "Restart Hooke to finish
    updating?" with **Restart Now as the Return default** (Will, 2026-10-26: "Restart Now as the default when nothing is
    running"; this replaces the 0.1.2 rule that Keep Working was the default). Something running names it and has no
    Return default ("Later" on Escape, "Stop and Restart" destructive); unsaved work, a lost connection or a read that
    fails or takes over 5 s refuses the restart (OK only), so an unread kind is never "nothing running".
  - Later leaves the update downloaded and waiting: the rail says Restart to update and Settings says so; either, or
    the Hooke menu's Check for Updates…, asks again (never Sparkle's own window, whose retry would look like ⌘Q).
  - Every plain quit (⌘Q, closing the window, logging out), an update waiting or not, follows one rule: nothing at
    stake quits; running work asks "Quit Hooke and stop what's running?"; unsaved or unreadable work asks with
    Quit Anyway. No state leaves Hooke unable to quit. A waiting update installs as Hooke quits; in the 0.4.1
    rehearsal (a VM, 4695 → 4696) Sparkle then reopened Hooke on the new build within about a second.
  - A quit that waits for its check (`.terminateLater`) is answered with `Termination.reply` and started from a check's
    completion with `Termination.terminate` (`HookeUI/Termination.swift`), never with `DispatchQueue.main.async`: a
    `terminate` called inside a main-queue block waits in a run loop where the main queue cannot run, so that answer
    never arrives. Candidate 4695 stayed on "Closing your workspace" after Restart Now for this reason; `make
    native-test` now quits a real NSApplication each way and checks that the old answer still hangs.

Native Blackmagic access keeps one Network.framework Bonjour browser in the app
process while Blackmagic is selected. The origin-checked camera bridge accepts only
this workspace and its camera page; it returns bounded permission state, never
network identities. This supplies a lasting macOS permission operation, while the
Python camera bridge continues to own phone control and received frames. A denied
permission has a System Settings recovery step; native discovery readiness never
marks the camera live. Switching away or quitting cancels the native browser.

The runtime fingerprint is finalized after compiled Studio/camera assets are added
(`finalize_snapshot.py`), and includes build identity. UI-only/native-only rebuilds
at one marketing version must never reuse an older extracted payload. A completed
cached runtime is reused only when its packaged manifest exactly matches the bundle.

Dismissing Sparkle's update sheet is deferral, not evidence that an update disappeared.
Keep the rail reminder until an update check reports no applicable update or the
installed version changes; closing the sheet never forces a restart.

Blackmagic's SRT listener and settings-download server also use channel-owned ports:
Stable 9710/8767, Beta 9810/8867. The launcher supplies both through the bridge's
environment. Only the exact uncustomized legacy listener in early Beta source
choices is remapped; custom streams remain intact, and this never selects a camera
by itself. A physical phone still has one active destination: two apps cannot own
the same live phone simultaneously merely because their ports differ.

Optional Beta companion ZIPs use `macos/release_companions.py`: prepare freezes an
explicit bounded manifest/asset allowlist and records its hash separately from the
DMG. Publication requires both approvals, verifies downloaded draft bytes before
activation and anonymous bytes afterward. Stable preserves its accepted Beta's exact
companion binding; omission/substitution refuses. Package source and app source are
recorded separately. Packaging never grants scientific qualification or trust.

Native folder bridge v2 retains selected bytes in a private disk snapshot and exposes
bounded offset reads through an opaque workspace-origin lease. The browser no longer
needs the complete native snapshot in memory. Capture retains no-follow, local-volume,
file/directory identity checks and a free-space reserve; progress renews the existing
30-second inactivity limit, while Stop/app closure still cancels capture. Native
selection permits the existing 64 GiB source-storage ceiling and 4,096 files; this is
transport admission, not reader or full-size workflow qualification. Server acceptance
remains the shared immutable Project intake, separate from native selection. The
snapshot fixture checks small real files, retry reads and refusal/cleanup; it does not
qualify an installed large-folder workflow.

## Outside agents (ADR 0026)

- **`Contents/Helpers/hooke`** (`macos/hooke/main.swift`, with `macos/HookeUI/AgentShared.swift`): the command a
  researcher's own coding agent runs.
  - It reads its own bundle's Info.plist, so it talks only to its own channel's app. It's signed with its own
    identifier, `<bundle id>.hooke`.
  - Every command names its agent (`--agent` or `HOOKE_AGENT`). Options may come anywhere on the line.
  - **Opening Hooke:** it opens this copy by its own path and checks that macOS opened it. It waits on the app's state
    file, so `status --wait` works on a Mac where Hooke never ran. It waits through `updating` rather than opening over
    an install.
  - **Restarts:** door calls ride out a restart (the port is read again) and retry safely with their request key.
    `follow` keeps finding Hooke and says `interrupted` when that's what happened.
  - **Connecting:** the researcher confirms once in Hooke's native dialog. The token goes in the Keychain
    (`org.hookeui.agent.<channel>`, account = agent kind for the existing unnamed connection;
    `kind:name` for `--connection name` / `HOOKE_CONNECTION`). Named selectors are exact and bounded;
    they never fall back to the unnamed credential. The server `ac_` identity owns attribution.
  - **The skill:** it installs or refreshes the Hooke skill (`.agents/skills/hooke/`, in the snapshot). That never
    touches a connection, and it never replaces a `hooke` folder that lacks Hooke's `.installed-by-hooke` marker.
  - No scientific logic: everything goes through the orchestrator's door (`services/orchestrator/door/`).
  - **`HOOKE_DOOR_URL`** (a loopback source checkout's door) works only for a copy built outside an app. A copy inside
    an app never sends its token anywhere but its own app.
- **The confirm key and the studio token:** each launch the app makes them in memory, and neither is written down.
  - The server gets the confirm key's public half (`HOOKE_CONFIRM_KEY`) and the studio token (`HOOKE_STUDIO_TOKEN`) in
    the environment it is started with.
  - The window gets the studio token through a `WKUserScript`.
  - `AgentConnect` asks in a native dialog ("Don't connect" is the default button) and posts the signed answer.
  - `AgentAttention` only brings Hooke forward.
- **State file:** `~/Library/Application Support/<support folder>/agent-state.json`. It holds the channel, version,
  phase (`asking`, `downloading`, `starting`, `ready`, `failed`, `stopped`, `updating`), what the setup screen says, the
  port once open, and the pid. A file whose pid isn't this app's executable says nothing about now.
- **Links:** each channel has its own scheme (`urlScheme` in `channels.json`: `hookeui`, `hookeui-beta`), declared in
  Info.plist. `hookeui://open?page=#measurements/…` only navigates the workspace to an allowed page and brings Hooke
  forward; a link never acts.
- **Releases:** official builds come from hooke-web's `desktop-release` builder. It has to know `urlScheme` and build,
  sign and notarize `Contents/Helpers/hooke` exactly as `macos/build.sh` here does, before any Beta carries this.
- **Checks:**
  - `make native-test`: `macos/tests/agent-links`, and builds `hooke`.
  - `services/tests/test_agent_confirm_native.py`: the app's own signing code, checked by the server.
  - `services/tests/test_hooke_command.py`: the real command against a real door (macOS with a login keychain).
  - `services/tests/test_mac_channels.py`.

