# Install HookeUI

The supported packaged app is for **Apple Silicon Macs running macOS 14 or later**.
Official installers are signed and notarized. You do not need developer tools or
a Hooke account to use the Mac app.

## Choose Stable or Beta

| Channel | Use it for | Download |
| --- | --- | --- |
| Stable | Regular use; the recommended starting point | [Stable installers and release notes](https://github.com/wpulier/hookeui.org/releases/latest) |
| Beta | Testing newer features and reporting problems | [Beta installers and release notes](https://github.com/wpulier/hookeui-beta/releases/latest) |

1. Follow the appropriate release link and read its notes and known limitations.
2. Download the **`.dmg`** file from the release's assets.
3. Open it and drag **HookeUI** or **HookeUI Beta** into **Applications**.
4. Launch the installed app. Follow the first-run download and setup prompts.
   An internet connection is needed to download the scientific runtime dependencies.
5. Start with the [guided image example](first-image.md). Camera access, model
   weights and optional external providers have their own setup steps.

The app reports which setup step is running. Download size and timing depend on
the release and what is already installed; use the values shown by that version.
If a step fails, keep its log and use **Try again** after addressing the reported cause.

## Updates and separate channels

Use **Check for Updates** in the installed app. Each channel checks its own feed;
you choose when to install and restart. Save active work first.

Stable and Beta can be installed side by side. They have separate application
identities and workspace locations. Beta does not automatically open or migrate
Stable's data. Copy or import work deliberately when testing, keeping your originals.

## Run from source

Developers should follow the repository's
[clean-start guide](https://github.com/wpulier/HookeUI/blob/dev/docs/clean-start.md)
for the current macOS prerequisites and Node version requirements. From a suitable
development environment:

```sh
git clone --branch dev https://github.com/wpulier/HookeUI.git
cd HookeUI
make setup
make start
```

Open the Studio URL printed by the launcher and keep its terminal open.
`make status` reports that checkout's servers; `make stop` stops them.
Use a separate checkout and an isolated profile when testing beside an existing
workspace. Do not run a second test instance over your real saved work.

Source setup is separate from the normal Mac installation. See the
[contributor guide](contributing.md) before changing code.
