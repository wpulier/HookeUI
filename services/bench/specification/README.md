# Atomic acceptance inventory (H00)

This folder expands the 71 acceptance groups and nine review regressions into atomic
children, as acceptance.md §1 requires. It is
an inventory of obligations. It marks nothing as passed. Status comes only from executed checks
(`make scientific-acceptance`). The ticket
records how it was built and what is still open.

| File | What it is | Edited by |
| --- | --- | --- |
| `rules.json` | Selectors over the pinned napari archive (revision `ebfc4a0a`, SHA-256 `3a29672c…0a740a6`): which classes, fields, properties, methods, enums, events, registered actions, menu commands, shortcuts, Qt controls, settings and builtin contributions to inventory, and which group each item belongs to. Also the per-group templates (§2–§6 obligations), upstream limits, per-child evidence-class overrides, the sources not yet pinned, and the census exclusion rules. | Hand |
| `manual.json` | Hand-written children: ticket 46's A6 Labels and A8 Shapes source-review rows (ids kept), the A8 children still needing procedures, every procedure step of the 71 ledger groups, the §1 upstream limits, the §3 fixture uses and the R01–R09 breakdown; and `specifications`, a work package's procedure, oracle, criteria and Hooke equivalent for generated children. Cited symbols must exist in the pinned source. | Hand |
| `atomic-cases.json` | The generated inventory the runner reads: one child per line, the source hashes, every exclusion with its reason, and the census accounting. | `make scientific-acceptance REGENERATE=1` |

## How a child is made

Each selector names a source file (or a same-depth glob) and what to select. Every item it finds is routed by
regular expression to one group or excluded with a stated reason. An item matched by no route fails generation.
So does a selector that finds nothing, an exclusion without a reason, a duplicate id, a cited symbol absent from
the archive, or a group left with no children. Templates then add the obligations every group carries: ordinary
UI entry, input, scale, states, lifecycle and saved values (§4), failure recovery (§3), the matched comparison
(§5), Configured, Operational and Usable (§2), and a clean instance of each OS (§6).

A child records its group, owning work package, kind, requirement, the evidence class it needs, its basis and
source (path, symbol, line; hashes in `sources`), and upstream limits. `procedure`, `oracle` and `criteria` are
absent until a work package writes them. Absent means null, and null means acceptance is not ready: a child cannot
pass that way. `hooke_equivalent` names the Hooke path a child's evidence exercises. A harness must state it (the
generator refuses one that doesn't) and gives it to every child it matches. A hand-written child in `manual.json`
records it where its package writes it. The runner does not require it there: such a child names its Hooke path in
its requirement and procedure.

## Harness templates

`rules.json` `harnesses` names the templates whose children share one parameterized check
(`services/bench/scientific_harnesses.py`). A harness gives every child it matches (family, kind, evidence class,
basis) its procedure, oracle and criteria, and the runner registers that child's own parametrized case. Two
harnesses never match one child. `python -m bench.scientific_harnesses [--report <report.json>]` lists every
template with its children, how many are ready, executable here, external and passing.

The `upstream-*` templates of #400 compared upstream napari with an inventory generated from the same source and
never with Hooke's own result, so their children are missing again (review of #400, rounds 1 and 2). A harness
re-admits children only when each case runs the call or change through Hooke (the review worker's console on the
session's own layers, or the ordinary Studio control), runs the same in a separate plain pinned-napari reference
process, and compares returns, side effects and event payloads. It must fail when a trigger doesn't emit, observe a
non-default setting's effect, and have negative tests that break the behaviour itself, not only its record.

The `compare-*` templates (`services/tests/scientific_comparison_recipes.py`,
`tests/test_scientific_reference_comparison.py`) are held to that bar. Each case acts on a layer added to the
session's own viewer, or on that viewer's own dims, camera and layer list, through the review worker's console. The
same case runs on a `ViewerModel` with the same volume in a plain pinned-napari process, and Hooke's observation must
equal the reference's. Utilities that a case would have to build for itself (events, emitters, shortcuts, task
statuses, match flags, actions, plugin settings) have no harness and stay missing (review of #416). The negatives
break a behaviour inside Hooke's worker, and one breaks Hooke's own path: the console's viewer is not the session's.
Without both runtimes the cases skip and the runner reports the children **missing**.

A work package writes them for a generated child in `manual.json` `specifications`, keyed by child id, with
only those four fields. Generation refuses a specification for an absent child, a second one for the same child, any
other field (an evidence class, a status, evidence) and one that differs from what the child already has (a ledger
step keeps its own procedure and oracle; its specification only adds criteria and the Hooke equivalent). Specifying
a child makes it ready to be checked; only its registered check, run, can pass it.

## The census

`bench.scientific_inventory` also takes the conservative census of all 11,852 declarations in the archive's
Python files, examples and tests included. `atomic-cases.json` accounts for each declaration. It is cited by a
child, excluded by one of the stated rules in `rules.json` (upstream tests, benchmarks, vendored code, build
tooling, examples, private helpers and modules, the experimental dynamic controls, rendering internals), or
counted as unreviewed and listed in `census.unreviewed_items`. A citation covers only its own declaration and
its class's declaration, never the class's other members. An item a selector excluded stays unreviewed until
the coverage review accepts that exclusion. The exclusion rules are review inputs. The independent coverage reviewer has to confirm
them against source, and any unreviewed declaration blocks completion.

The napari documentation site is a separate repository and is not part of the supplied archive. Docstrings,
examples and the builtin manifest are the documentation used here. Pinning the docs repository is open H00 work.

## Status

`make scientific-acceptance` reports every group and child as one of four statuses:

- **pass**: a trusted registered check (`CHECKS` in `services/bench/scientific_acceptance.py`) ran and every
  mapped test passed with no skips. The check's evidence class must equal the child's, and the child must have
  a procedure, oracle and criteria.
- **fail**: a mapped test failed or was skipped, or the check executed nothing.
- **missing**: a null procedure, oracle or criterion (checked first, so an unspecified child is never only
  "external"), no registered check, a check of a lower evidence class, not run in this invocation, or stale
  evidence.
- **external**: a specified child that needs clean-machine, physical-device, independent-contributor or
  fresh-researcher evidence. No authenticated import exists yet, so these children never pass here.

A run binds its evidence to the code it exercised: the commit, a hash of every tracked change and a hash of
every untracked file. It is sealed with a per-checkout key in `reports/scientific-acceptance/.seal-key`.
`FROM=<dir>` reuses a retained run only if this checkout sealed it, nothing in it or its JUnit was edited, and
the tree is unchanged. Otherwise it is refused or reported stale. The seal is a local integrity check. It is not
authenticated evidence.

A group is fail if any child fails, then missing if any child is missing, then external, and pass only when
every child passes. Completion also needs a census with nothing unreviewed, an inventory that regenerates
identically from the archive, and the independent coverage review and authenticated qualification (H00/H14).
The runner has no path that can grant those last two.

Comparison-harness prerequisite admission also requires each mapped case's retained
volume, approved source/dependency runtime and all three deliberate broken-behavior
checks. The runner requires every mapped prerequisite to execute and pass. Mutual
runtime equality and equally absent behaviors cannot certify a reference. The
existing prepared dependency versions and lock digest are retained in
`services/tests/scientific_reference_dependencies.json`; the source archive and
wheel pins remain in their existing contracts. These guards strengthen evidence
admission; their unit tests do not replace actual-worker comparison or independent
qualification.

The bounded LayerList lookup/replacement reconciliation adds two runtime children
under `P3.lifecycle` (`compare-layerlist`). The pinned `__getitem__` overloads at
lines 254 and 257 describe the same runtime method at line 259; the census keys
citations by path and symbol, so its one explicit runtime obligation accounts for
all three declarations. `__setitem__` at line 262 is the second obligation. No
existing child is removed or given credit: the denominator grows from 3,383 to
3,385, and the unreviewed source list changes from 932 to 928. Every bounded
lookup/replacement subcase and both controlled actual-worker negatives remain
mandatory. Their API comparison does not qualify ordinary layer-management UI or
explicit Save/reopen; those separate lifecycle children remain required.
