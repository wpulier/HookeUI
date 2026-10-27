# H14 qualification

Nothing in this folder says anything has passed. No participant, reviewer or clean machine has been involved yet.

| What | Where | State |
| --- | --- | --- |
| Pre-registered study design | [h14-study-design.md](h14-study-design.md), [power simulation](h14_power_simulation.py) | **frozen**, SHA-256 in `h14-study-design.sha256` |
| Q procedures and evidence record | [q-procedures.md](q-procedures.md) | **frozen**, SHA-256 in `q-procedures.sha256` |
| Proposed amendments (A2, A3) and interpretations (I1–I7) | [proposals/](proposals/README.md) | for Will and the reviewer; not in force |
| Participant and moderator materials | [materials/](materials/): moderator script, screener, consent template, orientation scripts, questionnaires, coding manual, run sheets, task cards, attempt log | drafts for the reviewer and the ethics review |
| Templates | [templates/](templates/): binding manifest (every field null until bound), evidence record, attempt record | the manifest refuses to start while any field is null |
| Synthetic dry run | [dry-run-2026-10-20/](dry-run-2026-10-20/) | simulated behaviour, not evidence about either app |

Code (`services/bench/`):
- `h14_tasks`: tasks, caps, groups and cards.
- `h14_fixtures`: task data packages, D1/D2, deterministic, `PACKAGE.sha256`.
- `h14_scoring`: oracles.
- `h14_analysis`: the §6 rules, the §7.4 sensitivity analyses and the verdict JSON.
- `h14_dryrun`: synthetic sessions.
- `h14_remote_server`: T13's cut.
- `qualification_evidence`: validate, sign and verify records; check the manifest.

Tests are in `services/tests/test_h14_preparation.py`. Build packages with `cd services && .venv/bin/python -m
bench.h14_fixtures <out> [--scale full]`. Run the dry run with `cd services && .venv/bin/python -m bench.h14_dryrun
<out>`.

Still to do before the pilot:
- the napari state-dump adapter (`bench.h14_state_napari`), written and tested in the pinned napari environment;
- the Hooke Project reader for observations;
- real-data packages (BBBC039 for T7–T9, and real tiles for T5 and T13) with their rights recorded;
- the reference runs at binding: the T0 check labels, the T8 Cellpose-SAM run and the T10 keyframes;
- Will's decisions on A2 and A3.
