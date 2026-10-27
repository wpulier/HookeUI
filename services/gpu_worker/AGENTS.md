# Model loads and the GPU workers

Part of the working context the root `AGENTS.md` maps. "Above" and "below" may mean parts it lists in
other files.

Nothing freezes, whatever model loads (live 2026-10-11 02:38-02:39: 8 frames in 80 s, one frame's publish
of 46.8 s, the follow worker reported hung after 42 s, load 142 on 14 cores while micro-sam loaded cold):
- **The event loop is watched** (`orchestrator/looplag.py`): a tick late past 200 ms is logged as `loop-lag`
  with the loop thread's stack taken while it was stuck; `/api/health` has the count (`loopLag`). Modules its stacks
  found first imported on the loop (scipy.ndimage at the first cell measurement, openai and its live types at the first
  voice session: about 240 ms each, live 2026-10-11; google.genai at the first look, up to 1.4 s in the loaded sims of
  2026-10-19/20) are imported at start on a thread of their own (`looplag.warm_imports`).
- **Nothing it found stays on the loop** (L1b, ticket 62): a log line logged on the loop (`agent.log`,
  `dialogue._export`, and so `loop-lag` records themselves) goes to one writer thread, in order and whole, flushed by
  `App.stop` and at exit (`looplag.LINES`; off the loop a line is written before `append` returns, as before); a vision
  client is made on a thread at its first call, and the selected ones at start (`vision._Client`); the brain's tool
  declarations are made again only when a tool changed (`Registry._declarations`); a change of the scale builds the
  capabilities again (170-260 ms) only when the objective they read changed. `MICROMIND_LOOP_WORK=on-loop` keeps
  the old way for each, to measure them side by side.
- **Workers start capped and at their role's priority** (`gpu_worker/boot.py`): OMP/MKL/VECLIB/OPENBLAS/
  NUMEXPR, cv2 and torch threads by role (the click and follow workers 4, the analysis and Molmo workers
  2), at normal priority, and micro-sam's own Python, which inherits the caps, niced (`docs/bench/priority.md`:
  niceing the whole analysis worker slowed its CPU-bound requests past 20% under load, background policy
  starved them); Trackastra's torch is capped too.
- **Loads are said, measured and kept within memory** (`orchestrator/loads.py`, `gpu_worker/loading.py`): a
  worker tells when a model starts and ends loading; the call's hang limit is then the load's own (micro-sam's
  `STARTUP_S`) and its timeout counts its run only; the request's step says "loading micro-sam (usually
  about 2 min)" or "for the first time on this Mac", Activity gets a line, running while it loads and done or
  failed on the same line when it ends (`Loads.say`, through `activity.model_loading` / `model_loaded`), and the
  times and sizes are kept in `models/loads.json`. The design for many more models is
  `docs/design/2026-10-11-model-runtime.md`:
  - **The catalog** (a folder per model, keys, readiness, environments, downloads, the Toolbox, channels...):
    `catalog/AGENTS.md`.
  - **Sizes** are the kernel's count of what the worker holds, Metal's memory included (`gpu_worker/footprint.py`),
    with the peak while it loaded. Resident size had measured MedSigLIP at 0 MB and SAM 2.1 tiny at 9 MB.
  - **Before a load,** its peak must fit in the memory free. When it does not, the model least worth keeping is
    unloaded (op `unload`): the seconds its reload costs here, weighed by how likely it is wanted soon, per GB it holds
    (`Loads.worth`), not simply the oldest. It is said in Activity (`activity.model_unloaded`, with what it made room
    for). Never the click worker's EdgeTAM or the follower's model, and never for the capability
    check inside the live app (`loads.sparing`: a load of its that does not fit is refused, `NoRoom`; live 2026-10-11
    16:31 Check now's Molmo pushed out Cellpose-SAM and DINOv2, swap-ins 6,387/s).
  - **Under pressure** (the meter's free memory or swapping rated poor two samples in a row), one idle model at a time
    is let go and said (`Loads.ease`).
  - **The wanted set** loads quietly once frames flow and again when there is room (`Loads.keep`;
    `MICROMIND_PRELOAD=0` stops it): the follower's model, the chosen outline model when it runs on this Mac, the
    default cell finder and picture model.
  - **The models choosing one** are told which are loaded and what a load costs here (`alternatives_text`).
  - **Big models have workers of their own** (a manifest's `host` "own" or "own-python": SAM 3.1, micro-sam;
    `hub.host_for`). Their load blocks no other model's requests, and letting one go stops its worker
    (`Loads.let_go`). `MICROMIND_OWN_HOSTS=0` keeps them on the analysis worker.
- **The follower's model before its first outline** (`orchestrator/objtrack.py`, `MICROMIND_FOLLOW_READY`): "frames"
  loads EdgeTAM video on the follow worker, through the preload's path, once the click worker is ready and a frame has
  come; "first-use" leaves it to the preload or the first step, as before (L1a: the first step came first in three
  stacks of five, a 2.7-18 s load). "first-use" stays the default until a quiet A/B shows "frames" (first-step loads 9 of 15 stacks before, 0 of 11
  after) leaves the click margins no worse.
- **Clicks ahead of background inference** (`gpu_worker/lane.py`, `MICROMIND_CLICK_LANE`): while a worker answers a
  click, another worker's Cellpose tiles ("cells") and follower steps ("all") wait, at most 0.3 s each, through a small
  shared file of one slot per worker; "off" is as before. Measured by `bench/click_lane_bench.py`
  (`docs/bench/click-lane.md`), with the tighter caps (`MICROMIND_HEAVY_THREADS`, `MICROMIND_FOLLOW_THREADS`) beside it.
- **A click worker that dies is started again** (`orchestrator/gpu_client.py` `_respawn`; L7e 2026-10-20, `kill -9` left
  every click on "Loading the outline model"): its calls fail saying it stopped and how, calls while it restarts fail
  fast saying "restarting the outline model", the conversation, the log and Activity say it and its return, with
  backoff and at most 5 restarts in 10 min. Workers started on first use start again on their next use.
- The sim scenario `model-load` proves it: no loop stall, click p95 under 300 ms, the follower stepping,
  no frame gap of a second, and with no outside load the frame rate and delay as before, through an outside
  CPU load, a test load on the analysis worker (command `gpu-test`, the harness's only), micro-sam loaded
  cold, and all of them at once (about 75 s).

- **Optional click diagnosis** (`MICROMIND_CLICK_DIAGNOSTICS=1`): traced point-model calls retain bounded numeric
  host observations in existing execution receipts: decode/embedding (including any first load), prompt/submission,
  and existing CPU readback/postprocessing wall and calling-thread CPU times; whole-call process CPU and fault
  deltas; exact-pixel embedding cache hit. No extra device sync, profiler, scheduling or threshold change.
  `sim.harness --only cells-clicks` prints chronological request times/frame IDs and matching-window receipts only
  after the timed burst and following checks. Receipt IDs/RPC/frame IDs retain association; sorted p95 samples must
  not be paired by index with chronological worker times. Readback includes existing synchronization and CPU work,
  not isolated GPU kernel time. Process faults include all worker threads and are not exact macOS swap-in counts;
  compare the existing machine/load observations before attributing paging or device contention. Off by default.

- GPU execution receipts retain worker-started/final-reply observation times in the host wall-clock domain;
  queued submission is never recorded as execution start. These are RPC observations, not kernel timestamps.
  The `cells-clicks` scenario requires the selected Cellpose resident, then its exact started RPC before timing;
  after timing it requires at least ten complete successful click intervals inside that same RPC interval.
  No fixed sleep establishes overlap. The isolated 108 ms receipt of 2026-10-23 at source `a79bcd6a` preceded
  Cellpose finishing its first load and proves preparation responsiveness only; the earlier 438 ms overlap
  failure remains retained. No production scheduling or 300 ms threshold changed.
