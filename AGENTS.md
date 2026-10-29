# HookeUI contributor instructions

Read README.md, docs/CORE-BOUNDARY.md and the nearest directory's AGENTS.md before
editing. CONTEXT.md is the domain glossary; docs/adr contains architectural decisions.
The maintained user handbook is https://hookeui.org/docs/; its Fumadocs source
lives in hooke-web/documentation. Core architecture and component docs stay here.
Do not recreate a second user-handbook build in this repository.

The scientific core runs locally without a Hooke account. Keep official brand assets,
hosted authentication, billing, managed-cloud implementation, private configuration,
credentials, personal recordings and internal evidence out of this repository.
Execution extensions are explicitly selected by the local operator; requests or
saved documents must never choose executable modules.

Use scripts/studio.sh through make start/dev/stop/status for servers. A second
checkout and an isolated MICROMIND_HOME protect an existing running workspace.
Never use the owner's saved data or provider credentials in tests. Tests use
synthetic inputs or explicitly cleared fixtures. Preserve user data and judgments.

The camera bridge alone owns capture hardware. Keep live frame intake responsive;
slow work runs outside the event loop. A rendered or scientific result is not
complete until observed; distinguish tests, simulations, actual providers and hardware.

Run affected checks during edits; run make check on a frozen integration candidate.
Do not waive failures or remove scientific coverage to make cleanup pass. Document
intended golden changes. Keep source, weights, data and service terms separate.
Public API names and stored formats may retain historical identifiers for compatibility.

Release and review authority: read `packaging/macos/RELEASING.md` and
`CONTRIBUTING.md` before release work. Contributions target `dev`. Since 2026-10-29 Will's own
release agents accept a Beta by its fresh-Mac run, promote its exact source to `main` and publish, under his standing
authorization and the private runbook's guards. Never infer acceptance or publication authorization from a merge, tag or
passing check alone. Official commands
and credentials belong to the private publisher, not this core repository.

Component contracts (read the relevant file before editing):
- `apps/studio/AGENTS.md`
- `catalog/AGENTS.md`
- `contract/AGENTS.md`
- `docs/architecture/freshness-and-overlay.md`
- `docs/architecture/on-screen.md`
- `docs/architecture/scientific-matrices.md`
- `docs/architecture/scientific-workflows.md`
- `docs/architecture/seeing-and-acting.md`
- `docs/architecture/session-index.md`
- `docs/architecture/testing-states.md`
- `experiments/iphone-camera-feed/AGENTS.md`
- `packaging/AGENTS.md`
- `services/gpu_worker/AGENTS.md`
- `services/orchestrator/AGENTS.md`
- `services/orchestrator/door/AGENTS.md` (outside agents' one door, ADR 0026)
- `services/orchestrator/brain/AGENTS.md`
- `services/orchestrator/datasets/AGENTS.md`
- `services/orchestrator/day/AGENTS.md`
- `services/orchestrator/maintenance/AGENTS.md`
- `services/orchestrator/research/AGENTS.md`
- `services/tests/AGENTS.md`
