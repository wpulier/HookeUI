# 20. Host, don't rewrite; agreement is not accuracy

**Context.** Fiji, CellProfiler and the bioimage.io models hold decades of methods. We could re-implement them, or
host them behind our runner.
- **Definitions differ.** Our measurements and Fiji's differ for the same masks (perimeter: 26.5 against 28.8 µm).
- **Exit codes lie.** Headless ImageJ can exit 0 while its ROI Manager failed.
- **Comparing is already easy.** Several models can already be compared on one view. Two models agreeing is easy to
  mistake for both being right.

**Decision.**
- **Host, don't rewrite.** An external method runs in its own engine, in its own isolated worker, behind the
  operation contracts. A function is offered only through a reviewed binding with typed outputs and validators.
- **Equivalence needs a test.** Ours and theirs are never called the same until a parity test on the same inputs,
  per measurement definition, says so. Each column carries its definition.
- **Agreement is not accuracy.** Model-to-model IoU is shown as agreement, alongside counts, measurements and where
  they disagree. Accuracy is claimed only against an independent reference: the user's corrections, or labelled
  ground truth.
- **Each engine is described by what it does under its settings.** Analyze Particles "detects particles under these
  settings", and does not find cells.
- **Comparisons match the task and the output's meaning.** An entry enters "find cells" only when its declared output
  is instance labels.

**Cost.**
- Every hosted function needs a binding, a validator and a parity case before it is offered.
- Comparisons say less than a ranking would.

Design: `docs/design/2026-10-12-the-open-catalog.md`; `docs/design/2026-10-11-the-open-context.md` §§3, 6.
