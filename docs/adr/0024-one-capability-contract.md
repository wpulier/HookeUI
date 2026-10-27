# 24. One capability contract, one door: a folder declares its inputs and outputs, and every actor gets it

**Context.**
- **Today's limits.** Before this, a contributed capability could declare only one operation, one input and one output
  type, from a fixed list. It could declare no parameters.
- **Who could reach it.** Only the agent (`run_capability`) and the card's Try. The Actions panel read a static list,
  and researchers a static list of three operations.
- **What it cost.** Every new kind of thing (a new output type, a parameter, a Fiji function) needed app code. Adding a
  capability was therefore never "one folder".
- **The model.** ComfyUI solved this for its community. A node declares its inputs (data and widgets in one list) and
  its outputs by open type strings. One registry, `/object_info`, serves every node's declaration, and the client builds
  its widgets from it. A new custom node needs no change to ComfyUI.

**Decision.**
- **One contract (catalog format 2).** A capability is one catalog folder whose `model.toml` declares:
  - `[[inputs]]`: data inputs and parameters, in one list as ComfyUI's `INPUT_TYPES`. A parameter is `int`, `number`,
    `string`, `bool` or `choice`, with its default, range or options, unit, label and tooltip.
  - `[[outputs]]`: as ComfyUI's `RETURN_TYPES`.
  - `category`, `hidden` (what the app gives unasked), `outcomes`, `derived_from` and `tests`.

  Each field is traced to its reference in `docs/design/2026-10-15-one-way-in.md`.
- **Types are open.** The types in `DATA_TYPES` are the ones the app shows and keeps in a place of its own. Any other
  lowercase type id is still a type: its values are kept and handed on as data. A new kind of thing never waits for the
  app to learn it.
- **One door.** Every proven capability is in one registry (`orchestrator/kinds.py`). Every actor reads it, with no
  per-capability code:
  - the Actions panel and Try, each with a form built from its parameters;
  - the agent (`run_capability` with its parameters);
  - researchers (`run`).
- **One check of a run's parameters** (`manifests.params_given`) for every actor.
- **Four outcomes,** never confused:
  - result;
  - empty (a valid input, nothing found);
  - unsupported (refused, with why);
  - failed.
- **Later parts are reserved, not unknown:** axes beyond one plane, processing modes, human steps and saved methods. A
  folder using them is told "not supported by this app yet". A later format fills them in without changing the door.
- **What we keep that ComfyUI does not have:**
  - pinned files and a hashed lock;
  - a proof before anything is offered (ADR 0020);
  - activation by the user (ADR 0021);
  - the sandbox for code nobody reviewed.

  ComfyUI runs whatever a custom node ships. We don't.

**Cost.**
- **Two formats.** Format 1 entries (`[[implements]]`) still read, and each format-2 entry is also read as the
  operation contract the runner already knows. Two shapes are read until format 1 is retired.
- **Older apps.** An older app reads a format-2 folder as "needs a newer ObservationUI", never as half-understood.
- **Generic display.** An output of a type the app has no viewer for is shown as data, not drawn, until the app
  learns it.

Design: `docs/design/2026-10-15-one-way-in.md`. Builds on ADRs 0018, 0020 and 0021.
