# Contribute to HookeUI

Help build the full successor to Fiji/ImageJ with methods, adapters, examples,
reproducible failures, documentation and scientific review. Will Pulier (`wpulier`)
is the accountable reviewer for these routes and may explicitly delegate review.
Official inclusion and releases follow that review; contributors retain their rights.
The repository is `wpulier/HookeUI`; the initial candidate remains private until review.

First-party contributions use Apache-2.0 plus DCO. Third-party material retains
its own notices and terms; see THIRD_PARTY.md. Each component's code, weights, data and services keep their
separate terms. Declare original, copied and generated material, sources, required
notices and employer/institution permissions. A component field or passing test is
not proof that the contributor holds those rights.

## Add a capability: one folder, every actor gets it

Follow the skill [`add-a-capability`](../../.agents/skills/add-a-capability/SKILL.md), written for people and for AI
coders alike (Codex reads it from `.agents/skills`, Claude Code from `.claude/skills`; the in-app builder keeps its
rules). In short: `make capability-preflight FROM=<link or folder>`, `make capability-new ID=<id>`, fill it in,
`make capability-lock ID=<id>`, `make capability-test ID=<id>`, then a pull request. A capability is one catalog folder
in the contract (catalog format 2, [ADR 0024](../adr/0024-one-capability-contract.md)), and the app gives it a form in
Actions, Try on its card, the agent's tool and researchers' `run` with no app code of its own. Proven examples:
[an algorithm](../../catalog/models/spot-measure), [a Hugging Face model](../../catalog/models/swin2sr-enlarge),
[a Fiji function](../../catalog/models/fiji-gaussian-blur) and [a derived version](../../catalog/models/spot-shape).

## Start with a small reproducible change

Use the [clean install](../clean-start.md) and [public contributor check](../testing/public-contributor-check.md).
`make check-public` uses generated inputs without owner recordings, credentials or
models. Report exact source and dependency versions, input hashes, commands,
results, failures/skips and known limitations. The broader `make check` still has
its documented integration prerequisites. Generated mechanics, reference agreement
and biological qualification are separate kinds of evidence.

A catalog adapter follows the [existing E4 route](../../catalog/README.md#add-a-model-with-its-own-adapter-runner-python-env).
A supported `python-env` contribution consists of a manifest, adapter and hashed
lockfile. Use a unique ID, explicit version and `requires = { catalog = 2 }` (the capability contract above);
name its operation and exact input/output contract, sources, terms and author/paper
credit. Keep dependencies pinned and runtime fetching out of the adapter. The
[InstanSeg folder](../../catalog/models/instanseg-brightfield) is a fuller example;
its scientific claims and separate component terms need their own assessment.

## Local adapter walkthrough

1. Create an isolated profile using the clean-install guide. Put your three-file
   entry at `$MICROMIND_CATALOG/mine/models/<id>/` and run `make catalog-check`.
   Use an empty assets store and absent keys for generated mechanics. A local
   Python entry's install requirement is `install:mine/<id>`.
2. Inspect its card on **Models**, source, dependencies and licence declarations.
   Discovery reads metadata and executes no added code. **Install** is the explicit
   activation boundary for `python-env` entries: it builds pinned Python, verifies
   package hashes, checks imports and runs the adapter's contract proof. Cellpose
   weight files use their separate **Turn on** route.
3. Read the retained proof's actual input identity, hashes and generation. An empty
   assets store uses generated `three-discs-rgb8-v1`; it proves interface mechanics.
   Then open the [three-particle example](../../examples/three-particles/README.md),
   explicitly select your method under **Actions**, and inspect the original 96×96
   pixels and three regions. A threshold below 128 plus connected components on this
   example yields areas 149, 208 and 317 px². This deterministic exercise is not a
   trained model or a biological accuracy result.
4. Check useful refusals: omit a package hash (preparation/static CI must refuse),
   supply a false hash (Install must fail verification), declare an unsupported catalog format
   (the current reader must refuse), or return a 3×3 label map for the generated
   128×128 input (the new proof must fail). A previously proved generation remains
   available while a new candidate fails; inspect which version is in use.
5. After verifying the official base and your fork, fetch the official branch with
   ordinary Git. Prepare a new private review package using the commands below.
   Review its exact files, diff, body, base/head destination and proof. The proof
   input stays local; review it separately before any sharing. Preparation makes
   no commit, fetch, push or GitHub request.

```sh
make catalog-pr ID=<id> BASE_REMOTE=upstream BASE=main REMOTE=origin OUTPUT=<new-private-folder>
```

Use your actual remote and branch names. The package is the proposed catalog folder,
not a copy of your private profile. Missing/changed proof, files or routing require
fresh preparation. A reviewer checks the prepared folder through
`make catalog-ci IDS=<id> STATIC=1` in a checkout containing it at
`catalog/models/<id>/`; verify that file exists first. Non-static `catalog-ci` can
fetch dependencies and execute the adapter. CI's Mac proof is conditional on its
repository configuration; a skipped job needs an explicitly retained maintainer
proof. Updating to an accepted official commit discovers the entry; users still
install/prove it locally before running it.

Each adapter has a separate locked Python environment. The worker strips the
shell environment and installs a Python socket/DNS guard before import. For
reviewed catalog entries, that guard allows loopback/Unix sockets and does not
contain filesystem access, subprocesses or native networking; their code runs
with your user permissions.

Your own entries, shared files and unreviewed community entries additionally
require the [macOS sandbox](../../catalog/README.md#code-nobody-reviewed-runs-inside-macoss-sandbox),
including when a reviewed template loads someone else's model. Their installation
accepts ready-built packages only, and their imports, proofs and runs are confined
or refused if the sandbox is unavailable. Allowed reads include the runtime,
installed entry, pinned files and explicit job inputs; writes belong to the
entry's work area. The OS profile denies network access and unrelated user-file
contents. File metadata and Metal access remain allowed. Read the linked boundary
before allowing a publisher; a separate environment or Python guard alone does
not provide it. Dated contributor receipts describe only the controls actually
tested at their recorded revision.

Compatibility, review status, functional proof and scientific qualification remain
separate facts.

## Certify only your own submission

Read the exact [Developer Certificate of Origin 1.1](DCO.txt), copied from the
[official DCO](https://developercertificate.org/). Configure your own Git name and
email deliberately; the helper refuses missing/invalid identity and author or
committer overrides that differ from that configuration. The name, email and
sign-off become public and may be retained and redistributed. DCO is not a
copyright assignment, a cryptographic signature or a scientific certification.
Do not sign on someone else's behalf or sign historical work in bulk.

Preparation prints the full certificate. Only after reviewing it and truthfully
certifying this exact contribution, explicitly request submission:

```sh
make catalog-pr ID=<id> PACKAGE=<reviewed-private-folder> PUSH=1 SIGNOFF=1
```

The equivalent CLI flag is `--signoff`. Without it the helper refuses before any
process or network call. With it, the helper checks the configured identity, exact
package and routing, then fetches the base; an advanced base requires preparation
again. Its commit has your `Signed-off-by: Name <email>` trailer and only the
reviewed catalog payload. It pushes to the recorded contributor fork and opens a
PR to the explicit official base. The local package survives success or failure.
For ordinary commits use `git commit -s`; preserve valid certification through
review and integration. No helper or maintainer can certify rights for you.

## Non-code and upstream work

For a documentation/example correction, file a report or proposed edit at the
verified public repository once available. Include the confusing step, a small
rights-cleared reproducer, expected result and observed result. Will or an explicit
delegate reviews it; completion means an accepted correction linked to its released
revision, or a documented disposition explaining why it was not included. Credit
contributors and original scientific authors in the accepted material.

For scientific review, state the exact method/version, input and reference,
measurement definitions, units, calibration, comparisons and limits. For an upstream
bug, first reduce it to the same upstream engine without ObservationUI, keeping
parameters and actual outputs. Remove private inputs, paths and keys; retain
upstream notices and citation. Submit upstream only with the relevant contributor's
authorization, and link the eventual upstream issue/fix without claiming credit for
its original method. An internal rehearsal is preparation, not an outside contribution
or community adoption.
