# Contributing to HookeUI

Start with the README and clean-install guide. Use focused changes, explain the
scientific or user-visible behavior, and include checks that exercise it. No Hooke
account is required for local development. Report exact source and input identities;
do not describe simulated providers as live scientific validation.

Contributions use the Developer Certificate of Origin in
[docs/contributing/DCO.txt](docs/contributing/DCO.txt). Sign your commits with
`git commit -s` after confirming that you have the right to submit the work under
this repository's license. Disclose copied or generated material and retain upstream
notices. Model weights, input data and service terms are separate from code rights.

For capabilities, see [the contribution guide](docs/contributing/README.md).
Use synthetic or permission-cleared fixtures. Never submit keys, private recordings,
patient information, personal filesystem inventories or private cloud configuration.

## Reviews and maintainer access

Create your branch from `dev` and target **`wpulier/HookeUI:dev`**. Maintainers review
and merge contributions after the relevant checks pass. Will Pulier (`@wpulier`)
appoints maintainers; contributing, forking or submitting a review does not grant
write access. Approved reviewers are recorded in [.github/CODEOWNERS](.github/CODEOWNERS).

Anyone may comment or review a public PR. **An outside contributor's approval does
not satisfy the required maintainer approval.** Merging into `dev` requires an
approval from a code owner with repository write access, approval of the latest
reviewable push by someone other than its pusher, and resolution of review threads.
New code changes dismiss stale approvals. A PR author cannot approve their own PR.
The CODEOWNERS file on the target branch governs review, so changing that file in
a PR does not grant the author approval rights over that PR.

Every PR into `dev` runs the `tests` check (`.github/workflows/tests.yml`: the Python suite,
the Studio's type check and tests, and the native checks), and it must pass before the PR
merges; no bypass skips it. A first-time contributor's PR waits for a maintainer to approve
its workflow runs, which use a read-only token and no secrets.

Ownership/review-policy changes and GitHub workflows require Will's approval.
Both `dev` and `main` reject direct pushes, force-pushes and branch deletion under
the configured rules. Will retains a **PR-only administrator bypass** for owner
maintenance, including while there is only one maintainer; other contributors
and maintainers have no bypass. This exception is not a second person's review.

## Beta and Stable

**PR → `dev` → publish Beta → Will accepts → promote to `main` → publish Stable.**

Maintainers can merge reviewed work into `dev`. Only Will can merge the promotion
PR into `main`. Releases use fixed commits and explicit publication; a merge or
tag does not update installed apps. Maintainer access to the public repository
does not itself grant access to official signing keys or release publication.
See [the release policy](packaging/macos/RELEASING.md) for the handoff between roles.

Community proposals and forks do not imply official endorsement. See
[the code of conduct](CODE_OF_CONDUCT.md) and [security policy](SECURITY.md).
