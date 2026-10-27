# 15. A finding is shown, not retold

**Context.** A researcher's result today is prose with sources and a confidence:
- its pictures' boxes are in its own photo, not the scene;
- it knows one object and its crop;
- “show me” can only reopen its own work.

The UX document asks for findings that return things (evidence, objects, measurements, images, clips,
overlays) and for a “Show me” that makes the microscope workspace the explanation. That has to happen
without a model re-inventing its earlier answer, and without undoing what the user does meanwhile.

**Decision.**
- **A finding carries references,** and its claims stand on them:
  - its refs (index entries, members, captures, measurements, media) are resolved before it is announced, as claims are checked against their pages today;
  - a claim whose essential ref doesn't resolve is marked “evidence unavailable” and is limited or withheld, never left standing without its evidence.
- **A showing is data run through `apply`.** Its beats, chosen by the model from the general verbs, address artifacts by reference. Each counts only once the studio's report confirms it.
- **A showing owns only its own changes.** Every change records its owner and the value it replaced.
  - The user can interrupt at any time. A change the user makes to something the showing set becomes the user's.
  - Restore reverts only the showing's own changes that nobody has changed since. It never replays a saved arrangement.
  - Stopping never cancels unrelated capture or research.
- **The kinds of media stay distinct:** specimen captures, derived or annotated views, external references, and generated illustrations.
  - Evidence clips come only from footage that was actually recorded. An interval never recorded is “not recorded”.
  - Generated imagery is never evidence or measurement truth.
- **A researcher's own operations never change the screen.** They run through the runner in the analysis class, are attributed to it, and become index entries. Only a showing the user asked for puts them on screen.

**Cost.**
- A typed result, with refs and claim support.
- Ref resolution before announcing.
- Change ownership in the presentation state.
- Findings that would have read well as prose are sometimes limited because their evidence is gone.

Design: `docs/design/2026-10-07-the-partner.md`.
