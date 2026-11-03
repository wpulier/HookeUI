# 30. Only the scope bridge moves the microscope, inside the user's limits, with a yes before light and large runs

**Context.**
- **What Will wants.** On 3 Nov 2026 Will picked Hooke moving the microscope as the research idea that excited him.
  Hooke first guides the user's hands (`orchestrator/guide.py`); then it drives motors, light and acquisitions itself,
  so experiments can move the stage and switch light without a person at the knobs.
- **What can go wrong.** A misheard number moves a stage millimetres. A confused model sends a move past the slide's
  edge into the objective. Light left on bleaches a fluorescent sample. A long acquisition runs while nobody watches.
  The camera bridge's rule (only it opens the camera) kept device code in one place; motors need the same, plus limits.

**Decision.**
- **One process touches the hardware: the scope bridge** (`services/scope_bridge/`). Everything else asks it over HTTP
  on this Mac (`orchestrator/scope.py`). It runs in its own environment (`make scope-setup`), never in the app's
  runtime, and starts with the app only when asked (`SCOPE_BACKEND`).
- **The user's limits are checked in the bridge, before anything moves, whoever asked:** a box per axis, the most one
  move may travel, the exposure's range, and the size of acquisition that needs asking. They change only on the user's
  own words, kept with them. A first run gets a small box around where the stage is.
- **A yes before the light goes on and before a large acquisition:** the bridge answers with an offer; only the same
  action with that offer, within 3 minutes and once, goes ahead. The brain passes it only with the user's reply quoted
  from the request. Turning the light off and stopping never wait.
- **A receipt for every action**, whatever happened, kept by the bridge and filed in the session's journal.
- **Autofocus and acquisitions are the bridge's own bounded steps**, so focusing and acquiring never leave the limits
  and every move is one the bridge made and the fuzz can witness.
- **Loosening the limits waits for a yes too**; tightening them is at once.
- **Proof:** a fuzz of random and hostile commands with a witness reading the hardware, zero breaches, in every
  `make check` (the virtual OpenFlexure microscope; Micro-Manager's demo when its environment is set up).

**Consequences.**
- Micro-Manager rigs whose adapters are Windows only need a Windows machine running the bridge, and a reviewed
  transport beyond loopback before Hooke on a Mac can reach it (`services/scope_bridge/AGENTS.md`, "Real rigs").
- The live view still comes from the camera bridge; pictures from the scope's own camera are made on request.
- The voice's direct partner tools do not list the microscope tools yet; the voice reaches them through Hooke's brain.
- Guidance with motors (the guide's lines done by the stage instead of the user's hands) builds on both, next.
