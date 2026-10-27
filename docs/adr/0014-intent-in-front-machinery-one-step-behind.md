# 14. Intent in front, machinery one step behind

**Context.** MicroscopeMind has about fifty tools, a dozen models, a capability catalogue, execution
policies and an evidence index. All of it is real and worth inspecting, and none of it is what a
first-time user needs in order to point at a cell and ask what it is.

The plans so far were written machinery-first in places:
- one M1 acceptance flow put choosing a model in the core path;
- another's sample answer read “e3: N instances by Cellpose-SAM”.

The user's UX document (“JARVIS for Microscopy”, `docs/design/2026-10-07-microscopemind-ux.md`) states the
standard: “A novice should be able to use it by pointing and talking. An expert should be able to
inspect everything underneath.”

**Decision.**
- **Three depths.**
  - **Use** needs no knowledge of models, computer vision, microscopy software or the app's data model.
  - **Understand** (“how do you know?”, what made it, the basis of a measurement) is one step from any result.
  - **Inspect** (the catalogue, the execution inspector, comparisons, options, the API) is always reachable.

  Nothing is hidden or removed. Model choice stays one step away, never in the core path.
- **No technical knowledge required, but technical language isn't forbidden.** A user who asks about a model, or uses its name, gets a direct answer in those terms. The technical level speaks in proper terms. Nothing rewrites or filters an answer for wording (`learner.py`). A scan of beginner cases measures, and gates nothing.
- **Controls the user asked for stay where they are** unless the user approves a change.
- **The user's intent authorizes the ordinary steps needed to accomplish it:** crops, queries, captures, compatible model calls, outlines, closer looks, a quick researcher within its budget.
- **The app asks only when a step is genuinely required by:**
  - cost beyond the intent's bounds;
  - invasiveness;
  - recording rules (Record and saving a dataset stay the user's controls, ADR 0008 and 0009);
  - a change of scope.

  It doesn't generalise those into a rule that all useful work needs another confirmation.
- **Every refusal carries a plain next step,** or a declared reason why it has none.

**Cost.** Every fact has two ways to be said: a plain one at Use, and its detail at Understand. A
beginner-case vocabulary scan and the `next` declarations have to be maintained. Some answers name less
than a developer would like, until they ask.

Design: `docs/design/2026-10-07-the-partner.md`.
