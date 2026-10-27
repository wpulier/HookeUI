# Datasets: Record collects, the Data light checks

Part of the working context the root `AGENTS.md` maps. "Above" and "below" may mean parts it lists in
other files.

- A dataset (`orchestrator/datasets/`, the format in `docs/datasets.md`, ADR 0009) is collected only
  while the user records: Record is the one switch (it sends the studio command `dataset {on}`; no
  tool can). Record is its start and Stop its end, and nothing inside is ever trimmed by a rule or a
  model.
  - **Live app:** datasets live in `~/MicroscopeMind/datasets` (`MICROMIND_DATASETS_DIR`, set by
    `scripts/studio.sh`).
  - **Tests and the sim:** they keep their own under `MICROMIND_HOME`, so they never resume the
    real one.
- **State is the dataset's own** (`events.jsonl`, summarized in `dataset.json`), not the day's.
  - Midnight and opening another session leave it on (the dataset records the opening).
  - After any restart it carries on (`DatasetCollector.resume`: a new run, the gap written down).
- **What it holds:** while on, every record is copied in as it happens: the interaction log
  (`Agent.taps`), the day journal (`DayStore.listeners`), votes, scale changes, the scene, and each
  frame's facts (`Hub.on_frame`, joined to the video by the bridge's `seq`).
  - The pictures kept or shown are hard-linked or written in (`Agent.image_taps`,
    `Magnifier.picture_taps`, `Services.keep` for snapshots), plus the minute before the start (the
    clip buffer).
  - The camera's video is the bridge's recording with owner `dataset`, hard-linked as it is
    written. It is reconciled once a second against the bridge's health: asked for again when
    dropped, with a new part after a bridge restart. Across disks it is copied only once the
    bridge has finished the file.
- **Speed:** taps only queue; one writer thread does the disk. Only per-frame facts may be dropped
  under pressure, and they are counted. `importer.gather` writes the log lines still with `looplag.LINES`'s
  writer thread before it reads, so a line logged just before a dataset or a session save is in it. The `dataset` sim scenario checks it across an app restart
  and a bridge restart.
- **Test footage:** whether a frame was footage is the bridge's to say, in the recording's
  sidecar. `orchestrator/datasets/` never mentions it (a test greps).
- **October 22 scope amendment:** Record first chooses Camera source or Workspace.
  Camera source starts the same dataset and bridge recording without a browser screen
  chooser. Workspace chooses its screen before beginning the dataset. Both preserve
  the same outer dataset lifetime and final Data check; choosing scope alone starts
  nothing. Annotated view remains explicitly unavailable.
- **The studio:** Record (`#record`, `apps/studio/src/recorder.ts`) starts the dataset once you
  have chosen the tab to record, before anything is recorded, and stops it after the studio's file is
  saved, so the dataset is around the recording. Its time counts from the dataset's start (the
  server's, so a reload keeps it); after a reload it stops the camera and the dataset the page no
  longer holds. The studio's own recording is hard-linked into the dataset too (`video/screen-N`).
- **The Data light** (`#dataset`, inside Record's control and shown only while there is a dataset; a link to the Datasets
  page, not a button; `datasets/checks.py`):
  - **While recording:** it checks, once a second, from the dataset's own files that the camera video
    (linked in), its frame times, the frame facts (joinable by the bridge's `seq`) and the records are
    seen growing, and that there is room. "Checking" in the first seconds, blue once all pass, amber
    with one plain sentence per root cause when one does not (the recording goes on).
  - **After Stop:** "Saving" until the bridge has finished the camera video, then "Saved" or a
    problem, written into `dataset.json` as `endCheck` and shown in the Datasets tab.
  - Each change is a `light` event in the dataset, so analysis knows when saving faltered.
  - The Datasets tab lists where each dataset is and what it holds (`GET /api/datasets`,
    `orchestrator/datasets/store.py`): its window to the second, runs and gaps, video parts and
    counts. You can name one (`dataset-rename`, appended to its events), copy its path or show it
    in the Finder.
  - The brain and the voice are told one read-only line while it is on (`brain/context.py`
    `collecting`). There is no tool to switch it.
- **Reconstructed datasets:** `make dataset-import FROM= TO= VIDEO= VIDEO_START=`
  (`orchestrator/datasets/importer.py`) rebuilds a dataset afterwards from the logs.
  - It is marked `reconstructed`, and says what was not captured then.
  - Its microscope facts are those in force at the start; facts stated at import go in as entered
    after, with their evidence.
  - `make dataset-check` checks any dataset against `docs/datasets.md`.
- **Save this session** (the Datasets tab; `orchestrator/datasets/snapshot.py`):
  - **What it does:** keeps this conversation, or today so far, as a dataset without stopping anything, with the camera recordings that cover it linked with their sidecars.
  - **The review first:** it shows what it would hold, what it misses (per-frame facts outside Record, video outside a recording) and what is still being worked on.
  - **Who can save:** only that press (`POST /api/session-dataset` with the studio's header; no tool).
  - **What it reads:** never the optional execution log.
  - **Versions:** a later save of the same start is `-2`.
- **Microscope facts** (`orchestrator/datasets/rig.py`, `rig.json` beside the datasets): what only
  the user can say (microscope, eyepiece, field number, objectives, camera, adapter). They are
  entered in the Datasets tab (`rig` command) and outlive days. A field number given is the scale's
  from then on.
  - A dataset starts with them, and with what the app measured or assumed, each with its `source`
    (`at_start`).
  - A fact entered while one collects is appended as `rigEnteredAfter`, never written into what it
    started with.
