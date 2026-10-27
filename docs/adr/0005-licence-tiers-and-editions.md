# 5. Every component has a licence tier, and the edition decides which may run

**Context.** Some of the best tools for a job are licensed for non-commercial use only
(Cellpose-SAM's weights; later SuperPoint's weights, Quilt, BiomedCLIP, anything derived from
CRISP's code). The user wants a commercial core that can still use them for research, kept
apart clearly enough that the core can be open-sourced and a commercial arm added later.

**Decision (with the user, 2026-10-05).**
- `services/orchestrator/licences.py` lists every model, set of weights and service with
  its licence and one tier: open (Apache, MIT, BSD), terms (commercial use under the
  provider's conditions: cloud APIs, the SAM licence) or noncommercial.
- Each option says which components it uses (`Option.uses`), and takes the strictest tier
  among them.
- The edition is `MICROMIND_EDITION`: `commercial`, the default and what anything packaged
  runs, offers open and terms components; `research`, which `scripts/studio.sh` sets for
  local use, offers all of them.
- The edition is read from the environment, not from settings, so nothing switches it while
  the app runs, and the GPU worker processes inherit it.
- A default the edition may not use resolves to the first one it may. Choosing a blocked
  option is refused with the reason. A tool that falls back says so in its answer.
- The studio shows such an option as unavailable, with the reason.
- Code for noncommercial components lives in `services/noncommercial/`, with its own
  dependency group. Core code reaches it only through `licences.adapter()`, and a test
  checks that nothing else imports it. A future `services/commercial/` plugs in the same
  way.
- The GPU worker refuses ops the edition may not run, as a second gate.
- `make licences` writes `docs/licences.md`, and a test keeps it current.

**Cost.**
- At the time of this decision, in the commercial edition, outlining every cell at once was
  off until a permitted method existed (SAM 3.1 keeping all its matches).
- Every new option has to name its components.
- Code derived from a non-commercial codebase must go in the gated folder. A clean-room
  reimplementation from a paper may go in the core, with `derived_from` saying so.

**Amendment (explicit user decision, 2026-10-08).** The user designated the whole educational,
Jarvis, dynamic and data-production platform as open-source and noncommercial and requested
Cellpose-SAM and micro-sam be restored immediately. This supersedes the original commercial
default above. `research` is now the default when `MICROMIND_EDITION` is absent or empty in
every entry point, including a directly built app, its GPU workers and the simulation harness;
the studio launcher no longer supplies a different default from those paths. Cellpose-SAM
remains the research cell finder, and micro-sam remains an offered cell and click-outline
option. Fine-tuned Cellpose is unavailable until usable weights exist here.

Explicit `MICROMIND_EDITION=commercial` still permits only open and terms components and
resolves the cell finder to EdgeTAM's automatic masks (a permitted method now exists). An
invalid nonempty override also uses the commercial gate, so a typo never grants research
components. Component tiers, provenance, the gated adapters and the worker's second gate
remain unchanged; a future commercial build must set its edition explicitly.

**Scope clarification (2026-10-13).** The current project direction
keeps the application licence and contributor terms provisional until the release
contents and dependency rights are reviewed. The earlier description of the whole
platform as open-source and noncommercial records the 2026-10-08 direction; it does
not select the application's licence. The research default and component gates
defined by this ADR remain in force.
