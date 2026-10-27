# Official releases and source branches

Official HookeUI and HookeUI Beta publication is operated from the private
`wpulier/hooke-web` repository, under `desktop-release/`. Maintainers follow its
release procedure with an explicit local HookeUI checkout and frozen source SHA.
This repository owns the application, including the native Mac shell and runtime
snapshot manifest. Generic local builds remain available; no Hooke account is
required. Historical publishing helpers here are not the official release entry
point and do not run automatically.

The process is **reviewed work → dev → frozen Beta → owner acceptance → same source
on main → Stable**. Use merge commits for promotion to retain the accepted SHA.
If dev has advanced, promote a branch pinned to the accepted Beta, not dev’s tip.
A merge, push or tag never publishes an installer. Official keys and brand assets
remain private. Installed Beta and Stable releases keep their existing separate
feeds, identifiers and user data.

`main` is the stable source branch; `dev` integrates upcoming work. This fresh
repository’s initial main is a review baseline, not a newly qualified Stable
release. Existing users keep their already published versions until a future
explicitly accepted release. The legacy repository preserves their source history.

GitHub visibility is repository-wide: when Will approves publication, both main
and dev will be readable. Public access does not grant write or release access.
Both branches require pull requests and prevent force-push/deletion. The owner
can exercise the administrator PR-only review bypass; signing/publication remains
an explicit release-owner action. The repository stays private until that approval.
