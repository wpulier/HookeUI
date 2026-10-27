# HookeUI

HookeUI is a local scientific imaging workspace for viewing images and video,
working with scientific arrays, measuring specimens, and running reproducible tools.
The core runs on your computer and requires **no Hooke account**.

This repository is the source for HookeUI, prepared as a private review candidate
before its first public release. It starts with fresh history. Official Hooke
branding, the website, lessons, showcases, hosted accounts and managed cloud
implementation are maintained separately and are not required to build this core.

## Run locally

The supported developer-preview environment is macOS on Apple Silicon, with Xcode
Command Line Tools, FFmpeg, `uv`, and Node 22.12+ (22.x), 24.x or 26+.

```sh
make setup
make start
```

Open the local Studio URL printed by the launcher. Start without a camera using a
picture or the bundled example. `make stop` stops this checkout's servers;
`make status` reports them. See [clean installation](docs/clean-start.md).

Local files, the workspace, viewing and measurements do not require sign-in or
provider credentials. Optional model downloads and third-party AI providers have
separate setup, costs and terms. Choosing a provider may transmit selected data;
local-only Projects prevent provider admission. No model or camera is implicitly
qualified just because the workspace opens.

## Develop and verify

```sh
make check-public                # generated inputs, no account or private recordings
make test-python PY_TESTS="tests/test_core_boundary.py"
make typecheck
make test-studio STUDIO_TESTS="src/research-efforts.test.ts"
make check                       # complete integration gate; see its prerequisites
```

Tests use isolated local stores. Generated fixtures establish software behavior,
not biological accuracy, real model quality or live hardware qualification.
See [contributing](CONTRIBUTING.md), [testing](docs/testing/public-contributor-check.md),
[architecture](docs/CORE-BOUNDARY.md) and [component terms](docs/licences.md).
The Python services, Studio and camera bridge use some historical `MicroscopeMind`
and `ObservationUI` identifiers for data and API compatibility.

## Source and brand rights

First-party code is licensed under [Apache-2.0](LICENSE), subject to the
[third-party exceptions and notices](THIRD_PARTY.md). Model weights, datasets,
fonts and optional dependencies retain their own terms. [MEDIA.json](MEDIA.json)
records the distributed media and hashes. No personal recordings are included.

The software license does not grant rights to the official Hooke brand or imply
endorsement. See [TRADEMARKS.md](TRADEMARKS.md). Private managed extensions are
optional; the public core must remain independently buildable and usable.
