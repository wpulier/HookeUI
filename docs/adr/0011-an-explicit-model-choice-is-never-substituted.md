# 11. An explicit model choice is never substituted

**Context.** Model choice was a global setting, and the code under it changed the model quietly.

- **Hidden substitutions.** Inside `tools.outline_at` and `tools.segment`, three paths replaced a model's output with something else, and none was declared:
  - an empty box went to SAM 3.1 on fal;
  - an unsure small spot became a ring drawn by code;
  - a SAM 3.1 refinement that found nothing fell back to EdgeTAM.
- **The blind arena was affected.** Its arms called the same code with `model=`, so an “EdgeTAM” arm could show SAM 3.1's outline or a code ring. `arena.score()` pooled every vote by option name, and those votes choose the defaults.
- **New needs.** The user wants to try a model for one request (“highlight that using SAM 3.1”), to compare models on the same captured input, and to see what actually produced a result.

**Decision.** Two fields on every run, independent of each other:
- **Origin** (who invoked it): a click, the brain by default, the brain choosing, the user choosing, the arena, a comparison, or background work.
- **Policy** (how it runs):
  - `raw` runs that implementation or fails;
  - `route` runs it with its **declared** fallbacks only.

| Origin | Policy | On failure |
|---|---|---|
| Click, brain by default (no explicit choice) | `route` | the route's own fallbacks, then an honest failure |
| The brain choosing an implementation | `raw`, unless it explicitly selects a declared route | the brain gets the facts and decides what to do next |
| The user choosing (“use SAM 3.1”) | `raw`, unless they ask for the full route | the reason, and the compatible alternatives |
| Arena, comparison | either, stated per trial | per policy |
| Background (Sight, automatic masks, following) | `route` | the result stays temporary, and the failure is logged |

- **Fallbacks are part of a contract, not code paths.** Each one is a structured entry (`after`, `when`, `to`, `needs`) beside its operation, and is listed structurally in `contract/capabilities.json`. A test compares the two.
- **One record per computation says what ran:** the implementation requested, the policy, the origin, every attempt, and the one whose output was used. Reuse of a computation is recorded as a use, never as a fresh run.
- **The arena:**
  - Votes record their policy, and are never pooled across policies or with votes from before this decision. Defaults are chosen on `route`, which is what users get.
  - Until its vote, an arm's identity is on no surface: not in “made by”, scene source or notes, error texts, the inspector, or the capability wire.

**Cost.** A `raw` request fails where the old code would have quietly shown another model's outline.
The user and the brain see that failure, with the alternatives. Old arena votes are kept but no longer
pooled with new ones. Every fallback needs a contract entry and a catalogue entry before it can run.

Design: `docs/design/2026-10-06-captures-operations-evidence.md`.
