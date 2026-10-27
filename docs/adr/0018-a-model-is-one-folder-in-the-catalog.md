# 18. A model, tool or service is one folder in the catalog

**Context.** By 2026-10-12, adding a local model touched about 18–20 files, of which 3–5 held real logic.
- **The registries:**
  - the manifest;
  - the worker's tables;
  - `licences.COMPONENTS`;
  - `options.py`, `operations.py` and `names.py`;
  - the model check's weights and probes;
  - `capabilities.json`;
  - a test's defaults;
  - the goldens;
  - `protocol.ts`.
- **The same facts repeated.** Version, licence, needs and label were declared up to five times, and nothing kept
  them in step. SAM 3.1 passed `HF_TOKEN` without declaring it.
- **Cloud models weren't manifests.** They were options in `options.py`.
- **What open-sourcing needs.** The user's priority for open-sourcing is ease of adding a model: one obvious place,
  read by the software, shown in Capabilities, and available to everyone with the next update.

**Decision.**
- **One folder per thing.** Every model, tool and service is one folder under `catalog/` (`models/<id>/`,
  `tools/<id>/`, `services/<id>.toml`, plus `runners/` for how a kind of model is run).
  - **Its manifest** (`model.toml`) is the one description: what it is, its pinned source, files, adapter,
    environment and preprocessing, what it needs to get and to run, the operation contracts it implements, its credit
    and its check.
  - **Everything else is derived from it:** names, versions, needs, licence components, key lists and check weights.
    A test fails when the code names a model the catalog doesn't have.
- **A model is distinct from its uses.**
  - **A use** is an option the app chooses: its own id, label, note, default, fallbacks and execution identity.
  - **One model, several uses.** One model can have several uses (Gemini 3.8 Flash for seeing, sight, the fast pass
    and research), and the uses keep their ids.
  - **Settings stay settings.** Behaviour settings that aren't models stay ordinary settings.
- **The user's own entries** live in `~/MicroscopeMind/catalog/mine/`, in the same layout, namespaced. A local id never
  overrides a reviewed one.
- **The contributor promise.** Where an existing runner supports the exact format and architecture, a folder and its
  check are enough. A new architecture also needs an adapter, and until then its entry is "Needs an adapter".

**Cost.**
- The manifest grows, and moving `contract/models/` touches every reader of it.
- Deriving the registries means S2 must keep every option id, label, default and fallback exactly as they were. The
  goldens and the capability matrix prove it.
- A runner must be written once for each kind of model before its entries need no code.

Design: `docs/design/2026-10-12-the-open-catalog.md`. Continues R2 and R7 of `docs/design/2026-10-11-model-runtime.md`.
