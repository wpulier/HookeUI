# The scope bridge

Part of the working context the root `AGENTS.md` maps. ADR 0030 records why it is shaped this way.

- **Only the bridge touches the microscope.** As the camera bridge alone opens the camera, the scope bridge alone drives
  the microscope's motors, camera and light (`services/scope_bridge/`, its own process on 127.0.0.1:8780). Hooke's
  brain, its voice and its researchers ask it over HTTP (`orchestrator/scope.py`); nothing else imports a backend or
  opens a device. The camera bridge still serves the live view; the scope bridge's camera makes pictures on request.
- **Its own environment** (`make scope-setup`: `services/scope_bridge/.venv`, pinned in `requirements.txt`, compiled from
  `requirements.in`): pymmcore-plus, Micro-Manager's test adapters as a wheel (`mm-test-adapters`: DemoCamera,
  Utilities and the demo configuration) and Pillow. It is never in the app's runtime (the Mac app installs every
  dependency group of `services/`), so only someone who drives a microscope from Hooke gets them. The bridge's core
  (`limits.py`, `actions.py`, `bridge.py`, `server.py`, `fuzz.py`, the OpenFlexure backend) is standard library and
  numpy, so the services' tests run it too.
- **Started with the app only when asked** (`make start SCOPE_BACKEND=mm-demo`, or `SCOPE_BACKEND=openflexure
  SCOPE_SERVER=http://...`, or `SCOPE_BACKEND=mm SCOPE_CONFIG=rig.cfg`; `scripts/studio.sh`): its folder is `scope/`
  in the app's home (limits, receipts, pictures), and the orchestrator is given `MICROMIND_SCOPE_URL`. Without it the
  microscope tools are not there.
- **Backends** (`backends/`, the `Backend` protocol: position, move_to, snap, exposure, channels, light, stop; µm and
  ms; each call blocks until the hardware is done; what a microscope cannot do raises `Unavailable`, said as such):
  - `mm-demo` / `mm` (`mmcore.py`): Micro-Manager through pymmcore-plus, the demo configuration (an XY stage, a Z
    stage, DemoCamera, the White Light Shutter, the Channel group DAPI, FITC, Rhodamine, Cy5) or a rig's own file. The
    light is the shutter held open (auto-shutter off); acquisitions run through pymmcore-plus's engine from a
    useq-schema MDASequence made from the checked plan.
  - `openflexure` (`openflexure.py`): an OpenFlexure microscope's v2 HTTP API (openflexure-microscope-server v2.11, the
    released one): stage position and absolute moves in motor steps (`um_per_step`, `um_per_z_step`), each move an
    action read until it completes, pictures by `ram-capture` (taken now, never the stream's last frame). It sets no
    exposure, channel or light there (v2's API has none for light); those are `Unavailable`. v3 (beta in 2026) moved
    the API under `/api/v3` (`stage/move_relative`, `illumination/set_led`): a backend of its own once released.
- **Typed actions** (`actions.py`): snap, move (x, y, z µm, absolute or relative), autofocus (range, step), exposure
  (ms), channel (name), light (on), acquire (a useq-schema MDASequence subset: channels, time_plan, z_plan,
  stage_positions, axis_order), stop. An unknown kind or field, a wrong type or a number that is not finite is refused
  with what was wrong; nothing is guessed.
- **The user's limits** (`limits.py`, `limits.json`): the stage's box on each axis (µm, absolute), the most one move
  may travel across and in focus, the exposure's range, and the pictures and seconds above which an acquisition asks
  first. A first run sets a small box around where the stage is (2 mm across, 100 µm in focus); the user widens it in
  their own words (`scope_limits`, `asked_with` quoted from the request; `PUT /api/limits` needs `words`), kept with
  the limits. A bad file is an error, never a looser limit.
- **Every action, in `ScopeBridge.do`:** read as a typed action; checked against the limits (refused with what it would
  have crossed; nothing moves); the light going on and a large acquisition (more pictures or time than the limits, or
  travel between positions larger than one step) wait for a yes: the first ask is `needs-yes` with an offer id and the
  words to ask with, and the same action again with that offer as its approval within 3 minutes goes ahead, once. Then
  done, one at a time, and read back: a move within 1 µm across and 0.25 µm in focus of its target and inside the box,
  the light and the exposure as set. `stop` (`POST /api/stop`) interrupts whatever runs, at once, from its own thread.
  The bridge's own moves (autofocus, an acquisition by steps) go in steps no larger than the user allows, each inside
  the box.
- **Autofocus is the bridge's own sweep** (snap at each plane, sharpness over contrast squared, then the sharpest), so
  it never leaves the focus limits whatever a microscope's own autofocus would do.
- **A receipt for every action**, whatever happened (done, refused, needs-yes, failed, stopped): what was asked, the
  typed action, before and after, verified, what was made, how long. Kept in `receipts.jsonl`; the orchestrator files
  each in the session's journal as an app notice (producer "scope"), with the conversation it was asked in.
- **The brain's tools** (`brain/toolbox/scope.py`, family "scope", only with a bridge): `microscope`, `move_stage`,
  `scope_snap`, `scope_focus`, `scope_exposure`, `scope_channel`, `scope_light`, `scope_acquire`, `stop_scope`,
  `scope_limits`. A refusal is a failure said with its reason; a needs-yes answer tells the model the question to ask
  and to call again with `confirms` (the offer) and `asked_with` (the user's reply), which code checks the user said in
  this request. The voice reaches them through Hooke's brain (`do_in_hooke`); the partner's own direct tools do not
  list them yet.
- **The fuzz** (`fuzz.py`; `make scope-fuzz`, and `tests/test_scope_bridge.py` in every `make check`): random,
  malformed and hostile commands (out of the box and at its edge, steps far over the limit, NaN, strings and booleans
  for numbers, unknown kinds and fields, forged, replayed and misdirected approvals, oversized plans, stops fired from
  another thread) with the user played too (some offers answered yes). A breach is read from the microscope itself
  through a witness around the backend: the stage outside the box, one motor command further than a step outside an
  approved acquisition, an exposure out of range, the light on without the user's yes, a large acquisition run without
  it. Zero breaches or it fails: 250 commands on the virtual OpenFlexure microscope and 400 on Micro-Manager's demo in
  the tests; `make scope-fuzz` runs 2,000 on the demo.
- **The virtual microscope** (`services/sim/virtual_scope.py`) is the tests' OpenFlexure: the same stage the camera
  bridge can play as the camera (`/sim/stage`), so a test can move the stage through the scope bridge and see it move
  in the live view.

## Real rigs (2026-11-03)

Micro-Manager publishes no full build for macOS on Apple silicon: `mmcore install` there downloads only the test
adapters (DemoCamera, Utilities, SequenceTester, NotificationTester); a rig's adapters are built from source
(`mmcore build-dev`, mmCoreAndDevices). And many a rig's adapters are **Windows only** (closed vendor SDKs, not in the
Unix build list):
- Nikon Ti and Ti2 (NikonTI, NikonTi2), Zeiss MTB (ZeissMTB), Olympus IX83, Hamamatsu DCAM (HamamatsuHam), PCO
  (PCO_Generic), Thorlabs cameras (TSI) and Thorlabs Kinesis motion, Lumencor Light Engine.
- Andor SDK3 is Windows and Linux (no Mac driver); PVCAM (Photometrics) builds only on Linux among the Unix builds.
- Serial-port devices are cross-platform and in the Unix build: Prior, ASI (MS2000, Tiger), Ludl, Märzhäuser Tango,
  Leica DMI, ZeissCAN, CoolLED pE-300/pE-4000, Lumencor Spectra, Arduino.

So a Mac drives an OpenFlexure, or a stage, focus and light on serial controllers; a rig built around a Nikon, Zeiss
or Olympus stand or a Hamamatsu, PCO or Thorlabs camera needs a Windows machine running the bridge (it is HTTP on
loopback today; another machine needs a reviewed transport first). Sources: micro-manager.org device pages,
mmCoreAndDevices `DeviceAdapters/Makefile.am` and `configure.ac`, mm-test-adapters, pymmcore-plus `install.py`.
