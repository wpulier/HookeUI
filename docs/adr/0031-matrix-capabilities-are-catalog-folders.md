# 0031 — A count-matrix capability is a catalog folder, and a comparison runs only under a Study's frozen rule

**Proposed, 2026-11-05**, after the VCC 2026 final entry (C3, entry tVZkXDrbif0QDuU92SRv) was built by scripts
outside Hooke while Hooke kept only the evidence. The founder's direction: "Hooke itself should be the engine next
time." The list of what did not run through Hooke is `HOOKE-GAPS.md` in the campaign folder. This record answers its
rows 9–15, 25 and 26.

## Context

- **Every new matrix operation changed every other one.** The 18 scientific matrix operations live in
  `services/scientific_compute/engine.py` and its sibling modules. A fixed tuple, `catalog.IDS`, names them. Each one's
  generation hashes all of those files, so adding a 19th operation edits `engine.py` and changes all 18 generations:
  every frozen workflow pinned to one of them refuses until someone saves it again (gap row 10). The C1 challenger
  would have taken 2–4 hours this way, so it ran as a script.
- **The picture path already works the other way.** An image capability is one folder with a manifest and an adapter
  (ADR 0024). Nothing else in the app changes when one is added.
- **Decision rules lived in a text file.** On the night, each gate rule was written into `DECISIONS.log` before its
  results, and a script applied it. Nothing in Hooke recorded the rule or could refuse a comparison made under a rule
  written afterwards (gap rows 14, 26).

## Decision

1. **A folder is a matrix capability.** A reviewed folder under `catalog/models/<id>/` qualifies when:
   - its manifest declares `runner = "builtin-cpu"` and `adapter = "module:function"`;
   - it names exactly one prepared runtime in `needs_to_run = ["install:<runtime>"]`.

   `scientific_compute.catalog.ids()` lists the engine operations first, then these folders by id. Every caller sees
   them through the same `descriptor()`, `run_matrix`, workflow and door paths: Tools, researchers, the brain and
   connected agents. A user's entry (`mine/`), a channel's entry, a symlinked folder or a folder outside the repo's
   catalog is never one: these run Hooke's own confined worker with the user's Projects, so they stay reviewed code
   until a later decision qualifies contributed ones.
2. **A folder versions only itself.** A folder capability's generation is `matrix-folder-v1.<sha256>`. The hash covers:
   - every file in its folder;
   - `folder_worker.py`;
   - `resource_profile.py`;
   - its prepared runtime's receipt.

   It never includes `engine.py`, `catalog.py` or the run service. Adding or changing one folder changes that one
   generation and no other. Introducing this path changed `catalog.py` and `matrix_capability_runs.py` once, so the 18
   engine generations change once in this release. After this, they do not.
3. **The same boundary.** `folder_worker.py` runs the adapter as `function(inputs, params, output, check)`:
   - it runs in the same sandboxed, child-process-free, network-free worker;
   - it has the same RSS, CPU-time and file limits;
   - it holds the same machine heavy-work slot;
   - its output goes through the same retained-file intake.

   Only the folder is importable: the worker removes `scientific_compute` from the import path, so a folder cannot
   reach `engine.py`. The descriptor lists ports, parameters and readiness but never the folder's host path.
4. **A tool step binds one dependency per port, up to eight.** Comparisons keep their six and other steps their three.
5. **Frozen rules.**
   - A Study records a frozen rule with `Studies.freeze_rule`, or through the door with
     `POST studies/{study}/rules {session, requestKey, name, artifact}`.
   - The rule is an exact retained JSON file with Hooke's own time. Rows are only appended: a retry with the same
     request returns the same row, a changed body under that request conflicts, and the same file revision cannot be
     frozen twice.
   - A capability input of type `artifact.frozen-rule` admits only such a file. It must have been frozen before every
     other input of that step was saved. `run_matrix` enforces this for every caller and records which Study froze
     the rule, and when, in the operation's receipt.
6. **The VCC 2026 entry, as folders.**
   - `screen-effects`, `transfer-factors` (a, s, k, rank and a per-target strength weight), `vcc-count-emission` and
     `vcc-entry-check` run on `matrix-cpu-runtime`.
   - `vcc-fold-score` runs pinned cell-eval2 0.20.0 and AnnData 0.13.4 on `cell-evaluation-runtime`, after a tiny
     end-to-end score in the same runtime.
   - `vcc-package` runs official vcc-cli 0.3.0 on `vcc-packaging-runtime`, with a memory forecast measured on three
     real entries.
   - `frozen-gate` applies a `frozen-gate-v1` rule.

   Their arithmetic and random-number order are ported unchanged from the night's scripts. Through the door, the
   chain rebuilds C3's factors and package byte for byte (`docs/architecture/scientific-matrices.md`).

## Costs and limits

- The 18 engine operations stay where they are. Moving them into folders is separate work, one operation at a time,
  each a deliberate generation change.
- Folder capabilities are reviewed code merged into the repository, the same trust as `engine.py`. User-contributed
  matrix folders need their own qualification before this path admits them.
- The studio's workflow editor still connects one input to a tool step. Multi-input matrix steps are written as
  definitions through the door or the API.
- The emitter and its check take exactly three control inputs, which is the VCC final shape. A general N-context form
  needs either optional ports or a declared list port.
- A frozen rule is only as early as its row's time. Someone who looks at results outside Hooke and then freezes a rule
  defeats it. Hooke can refuse a rule frozen after the results it holds, but cannot prove what someone saw elsewhere.
- Study state is a mutable document whose revisions are immutable. A generic document save that forged a
  `frozenRules` row would not be caught by this check; the row's revision history is the audit trail.
