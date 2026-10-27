# First installation and a clean Studio start

The supported starting point is macOS on Apple Silicon. The Studio can open with no
camera, cloud keys, downloaded models or private example recordings. Those capabilities
have separate setup steps; reaching the Studio does not prove an analysis is ready.

## Install

Install Xcode Command Line Tools with `xcode-select --install`. With Homebrew already
installed, install the remaining tools:

```sh
brew install uv node@24 ffmpeg
export PATH="$(brew --prefix node@24)/bin:$PATH"
```

Node.js must satisfy `^22.12.0 || ^24.0.0 || >=26.0.0`, the intersection of the locked
Studio build and test-runner requirements. Node 20 and early Node 22 are insufficient.
FFmpeg includes `ffprobe`; the native camera helper needs `swiftc` and `codesign`.

From the repository root:

```sh
make setup
make start
```

Setup checks prerequisites first, obtains Python 3.13 through `uv` when needed, installs
the committed Python lockfile, then runs `npm ci` against the committed web lockfile.
It does not download model weights or provision cloud accounts. Existing optional
Python adapters are preserved by `uv sync --inexact`.

Open the Studio URL printed by the launcher (normally <http://127.0.0.1:8770/>). The
first build also builds the camera page and native camera helper. With no incoming
frames, the new session offers **Add a recording or picture**, **Connect live camera**
and **Reconnect**. Open **Models** to see missing keys, downloads and installation
steps. The app does not need all catalog models installed to open.

Keep the launch terminal open. Ctrl-C, closing that terminal, or `make stop` from the
same checkout stops its servers. `make status` reports their state; `make start`
starts them again and retains the profile. Starting again replaces any running
ObservationUI stack from that checkout, including stacks on other ports. Use a
separate checkout when testing beside someone else's running Studio.

For a camera, follow [the camera setup guide](../experiments/iphone-camera-feed/README.md#connecting-a-camera).
A first run starts on No camera (pictures and videos only: nothing searches for a camera); choose
Blackmagic Camera, the iPhone or another camera in the header's camera menu, and the choice is kept.
Continuity Camera requires `CAMERA_TARGET` to match the name this Mac reports. The no-camera generated
example needs neither.

## Recover from setup problems

- **Missing prerequisite or unsupported Node:** follow the named install command,
  check `node --version`, then repeat `make setup`. Check the Node bin directory is
  on this terminal's `PATH`.
- **Package installation interrupted:** repeat `make setup`. Keep both lockfiles;
  do not solve a clean-install failure by silently regenerating dependency versions.
- **Port occupied:** the launcher leaves another program alone and names the port.
  Stop its owning application or choose different `CAMERA_PORT`, `ORCH_PORT` and
  `DEV_PORT` values. Use the same values for start, status and stop.
- **Studio or worker fails:** retain the log directory printed by the launcher.
  Missing model weights belong on the Models page with their next action; an
  installation/startup failure is not a successful clean-start check.
- **No camera picture:** open Connect live camera for the checklist, or add a picture.
  Private test recordings are optional and are not an installation prerequisite.

## Verify an isolated profile beside a live checkout

Use a separate checkout with its own `make setup`. Choose an empty absolute folder
for the verification profile and unused ports. The following subshell removes
inherited provider keys and overrides while retaining the current OS account and
tool path; it does not modify the account's `HOME` or Keychain.

```sh
export MICROMIND_START_CHECK="$(mktemp -d -t micromind-clean-start)"
env -i HOME="$HOME" PATH="$PATH" MICROMIND_START_CHECK="$MICROMIND_START_CHECK" /bin/bash
export MICROMIND_HOME="$MICROMIND_START_CHECK/profile"
export MICROMIND_ASSETS="$MICROMIND_START_CHECK/assets"
export MICROMIND_CATALOG="$MICROMIND_START_CHECK/catalog"
export MICROMIND_MODEL_FILES="$MICROMIND_START_CHECK/model-cache"
export MICROMIND_RECORDINGS_DIR="$MICROMIND_HOME/recordings"
export MICROMIND_KEYS_FILE="$MICROMIND_START_CHECK/absent-keys.txt"
export MICROMIND_KEYCHAIN=off
export HF_HOME="$MICROMIND_START_CHECK/huggingface"
export TORCH_HOME="$MICROMIND_START_CHECK/torch"
export CAMERA_PORT=18765 ORCH_PORT=18770 DEV_PORT=15180 CV_PORT=18766
export CAMERA_NO_PHONE=1
export CAMERA_SOURCE='srt://127.0.0.1:19710?mode=listener&latency=20000'
make start
```

`CAMERA_NO_PHONE=1` passes the bridge's existing `--no-phone` option. Pair it with the
separate loopback stream above: the bridge waits for frames without opening a camera,
discovering Blackmagic Camera or controlling a phone. The downstream Studio and
workers are the real application. No model stand-ins are selected. All legacy model
lookups, catalog files, recordings, saved keys and app data use empty isolated paths.

Open <http://127.0.0.1:18770/>; confirm the no-camera start choices and Models setup
state. Press Ctrl-C, run `make status`, then `make start` again in the same subshell.
Verify the browser reconnects. Finally press Ctrl-C and `exit`. Keep the temporary
profile and logs until the evidence has been reviewed. For a cold package-cache test,
set `UV_CACHE_DIR` and `npm_config_cache` to empty folders before `make setup` too.

Record the Git revision, OS/hardware, dependency versions, commands, elapsed-time
scope, failures and browser observations. This check proves startup and lifecycle;
it does not establish camera hardware, model accuracy or the scientific export journey.

Continue with A saved result, corrected and verified in Fiji
for the prepared example's complete scientific workflow.
