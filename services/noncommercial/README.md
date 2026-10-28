# Optional model integrations and edition policy

This folder contains HookeUI wrappers, adapters and helper processes for components
assigned to the application's `noncommercial` policy tier. The folder name does not apply a
blanket licence to this source or its dependencies. Project-authored integration source,
fetched software, trained weights and training or input data have separate rights; see
[NOTICE](NOTICE) for that distinction. Training-data declarations alone do not determine the
legal effect on weights or clear a particular use.

The core of HookeUI (`orchestrator`, `gpu_worker`, `research_worker`, `sim`, `bench`, the
studio) reaches these adapters through `orchestrator/licences.py`'s `adapter()`. The research
edition permits their tier and is the default when `MICROMIND_EDITION` is unset or empty
(the user's 2026-10-08 decision, ADR 0005). Explicit `MICROMIND_EDITION=commercial` and invalid
nonempty overrides use the commercial gate, which permits only `open` and `terms` tiers.
With that edition, or in a copy without this folder, the options that need these adapters
are unavailable and an automatic fallback, when supported, is identified. Edition eligibility
is an application availability rule, not a finding of legal suitability.

| Module | Component | Recorded software, weights and data declarations |
|---|---|---|
| `cellpose_cells.py` | Cellpose-SAM | code BSD-3-Clause; weights trained on data licensed for non-commercial use |
| `trackastra_link.py` | Trackastra (`general_2d`), linking cells between two views | code BSD-3-Clause; the weights' training data terms are not stated upstream; its install brings Gurobi (proprietary, never called: linking is greedy) |
| `cellpose_cells.py` (`CellposeTunedCells`, `train`) | Cellpose-SAM fine-tuned on your corrected cells (`make finetune-cells`) | weights derived from Cellpose-SAM's; the app keeps the same noncommercial policy tier |
| `microsam_cells.py`, `microsam_server.py` | micro-sam light-microscopy SAM (`vit_b_lm`) | code MIT; weights CC-BY-4.0; trained on LIVECell (CC BY-NC 4.0), TissueNet and DynamicNuclearNet (non-commercial) and Cellpose's dataset (non-commercial), among 14 sets |

**Cellpose-SAM's device self-check** (`cellpose_device.py`; ticket 62, F-L8-1). The device that runs Cellpose-SAM is
proven by its answer before it makes any result. On a macOS virtual machine's paravirtual GPU, Cellpose-SAM returned zero
cells with no error.
- **The check:** the device runs a probe, the bundled CC0 specimen's 128×128 crop, and must find the cells the CPU finds
  there. It is tried in bfloat16 first, then in float32. If neither agrees, Cellpose-SAM runs on the CPU, and the
  fallback is said plainly.
- **The verdict** is kept per machine and setup in `.device-check.json` in the model's own folder, beside its revisions
  (each published revision is read-only). A verdict covers the software, the Mac's hardware model and whether it is a
  virtual machine, so a verdict from one machine is never reused on another.
- **A probe that raised** (out of memory, say) is not taken as wrong results. It is said differently and never kept, so
  the next load probes again.
- **Your own Cellpose model** needs only its own file. Without Cellpose-SAM's download the device can't be checked, so
  the model runs on the CPU, the app says so, and nothing is kept.
- **Training and the own-model file check** use the proven device too.
- **What the result keeps:** each result records the device that made it and how that device was proven, in
  `producer.device`.
- **Nothing found:** a result with no cells says "Found no cells" and how its device was proven, never a plain success.
- **The panel** (`examples/beta-scientific-tools/cellposesam-panel`) applies the same check on its first run, using the
  probe it ships as its example.

A Cellpose model added by a user has its maker's weight terms recorded on its catalog entry;
the app currently assigns it to the same noncommercial policy tier. Cellpose's Python package
is in the `noncommercial` dependency group in `services/pyproject.toml`; that group name is not
the package's licence.

micro-sam brings 187 packages (napari and friends), so it is not installed into the app's
environment: it has its own, and `microsam_cells.py` runs `microsam_server.py` there as a child
process of the GPU worker (started on first use, kept, gone when the worker goes). Its card's
**Install** makes that Python from its pinned lockfile (`catalog/models/micro-sam-lm/env.lock`;
`orchestrator/envs.py`, catalog/README.md "Environments the app makes"). One made by hand before
(`MICROMIND_MICROSAM_PYTHON`, default `~/.cache/micromind/venvs/microsam/bin/python`) still runs it.

Trackastra likewise runs in its own Python (`trackastra_server.py`, `catalog/models/trackastra/env.lock`),
so its packages (Gurobi among them) never enter the app's.

Its weights (`vit_b_lm` and its decoder, 413 MB) are the catalog's pinned files (`catalog/models/micro-sam-lm`):
Download on the Models page puts them in the models folder, and the server is given their paths; it never fetches
them itself (it refuses to run without them).
`make licences` lists every component, its tier and where it is used (`docs/licences.md`).
