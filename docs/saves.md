# Saves: three homes, and how long each is kept

Everything ObservationUI saves is one of three kinds, kept apart so nothing is lost, broken or
mistaken for something else (`orchestrator/paths.py`: `KINDS`, `LIFETIMES`; a test fails if a save has
no kind):

| Kind | What | Where, once moved | Kept |
|---|---|---|---|
| **Datasets** | What Record collected, for later analysis (docs/datasets.md) | `~/MicroscopeMind/datasets/` | Until you delete one (the Datasets tab moves it to `datasets/.trash/`) |
| **Performance logs** | For us, to audit a session and find problems: the interaction log, the chat export, the pictures models were shown (`crops/`), the workspace pictures `look_at_screen` took (`screens/`), each researcher's conversation with its model, round by round (`transcripts/<day>/<rid>.jsonl`: an audit trail of why it did what it did, with web pages as they came; its work itself is an asset), saved logs, the servers' logs, and the app's own check of each model (`checks.jsonl`: works or broken and why, made again by Check now or `make capability-check`) | `~/MicroscopeMind/logs/` | Temporary: pruned once used (`make logs-prune`) |
| **Assets** | Yours, to share, review and play: the session journals (`sessions/<sid>/journal.jsonl`: a session is the project, ADR 0017) and their conversations, your preferences (below), researchers' work, recordings, snapshots, evidence (what a cell finder or a matcher found, below), results' pictures (below), taught labels, what was seen today, bookmarks (places on the slide you kept, with a picture), fine-tuned models (below), identity (things followed over time: which outlines and cells are one thing, how that is known, and what was measured of them, `identity/<day>/events.jsonl`), volumes and jobs (a whole-brain volume you added, with its axes, voxel sizes and orientation and where each came from, and each registration run on it: its request, log, outputs and record, `jobs/<date>/`) | `~/MicroscopeMind/assets/` | Until you delete them |

**Kept for good.** Votes, corrections and capability verdicts are judgments people made. They sit
with the logs, in `~/MicroscopeMind/logs/kept/`, and pruning never touches them.

Dataset tables keep each version's rows under the same home, `results/tables/<session>/<table>/v<n>.json`, with
the SHA-256 the session's journal recorded for them (`datatables.py`); a version is never rewritten.

Scientific documents and native array components live under the retained results home,
`results/projects/<document-id>/`, independently of software environments. The existing
ProjectStore accepts a document revision by compare-and-set. Its `arrays/` directory contains
immutable, content-addressed raw chunks and manifests; completing those files alone does not
accept an edit. Only components referenced by an accepted document revision can receive a
regional read grant. Old document revisions retain their component references for reopening
and undo. Package update, rollback and removal do not delete them.

The initial native storage path preserves scalar type, byte order, axes and declared scientific
metadata. Reads verify the exact chunk bytes, cap returned regions and source-read amplification,
and do not materialize whole arrays. Acceptance verifies referenced chunks before changing the
document head. Its initial publication limit is 64 MiB and 65,536 chunks per save, with a shared
analysis-memory reservation on Studio saves. Larger publication needs the scientific-host job
admission path; this prerequisite does not qualify large-data viewing or editing. Interrupted
or conflicting candidates can leave unaccepted immutable chunks. There is no automatic cleanup
of those files; future collection must account for every accepted historical reference.

Native edits use that same document head. An admitted view binds its gesture to an exact
component revision and an acknowledged frame; replacing the view or losing its interaction
ownership cancels pending authority. Workers submit bounded binary chunks through opaque
grants. They cannot choose saved paths, replace scientific metadata or accept their own output.
An accepted edit records its transaction identity in the immutable revision envelope. Repeating
that exact save after a lost acknowledgement returns its original receipt, even if a later
writer has already saved; it never rewinds the current document. Older records without a
transaction remain readable. These internal boundaries do not yet supply a viewer or editor.

**The test footage library** (`footage/`, `sources/`, `bench/`, `assets/manifest.json`) is already in
`~/MicroscopeMind` and stays where it is.

## Moving them out of the checkout

Until they are moved, the saves stay where they always were: `services/` and `recordings/` of the
checkout that runs. Moving is one command, run with the app stopped:

```
make saves         # where each kind is now, and how big
make saves-move    # lists what would move, changes nothing
make saves-move APPLY=1
```

**What it does:**
- Every file is hard-linked into its new home: the same disk, so it is instant and uses no extra
  space. Where linking is not possible, it is copied.
- Each file is checked by size, and small files by content.
- Only when every file is there does it write `~/MicroscopeMind/.saves.json`. From then on
  `paths.py` puts every save in the three homes, so the app, the reports, `make save-logs` and the
  demo script all follow.
- A different file already at a new place is never overwritten. Nothing switches until that is
  sorted out.

**The originals stay** in `services/` and `recordings/` until you remove them. While both names
point at the same file, removing one name frees no space.

**After the move, `make start` brings along anything written in the old places since** (an older
checkout, or code before this change): `python -m orchestrator.saves sync`. It only brings files
written after the move, so what was pruned does not come back.

**Which layout is used:** `MICROMIND_HOME` (tests and the sim, each in its own folder) >
`MICROMIND_SAVES` > the marker > `services/`.

## Keeping it clean

```
make logs-prune            # performance logs older than 14 days: lists them
make logs-prune DAYS=30 APPLY=1
```

- **What it removes:** only the performance logs' dated folders (`crops/<day>`, `screens/<day>`, `transcripts/<day>`,
  `saved/<time>`).
  Never kept judgments, assets or datasets.
- **The two growing logs** (`interactions.jsonl`, `conversation.jsonl`) are single files. Splitting
  them by day is a later step.
- **Automatic pruning** is off. It runs only when you say so.

## Datasets and the other two

A dataset never points into the logs or the assets: it holds its own copies (hard links for video
and pictures). Pruning logs or deleting assets never breaks a dataset.

## Preferences

Your settings are yours, not a day's (`orchestrator/day/prefs.py`): who leads, the look of outlines, how to
explain, the research cap's amount, the models chosen from now on and the scale. They are kept until you change
them, in one append-only file beside the session journals, `assets/sessions/preferences.jsonl` (`MICROMIND_SESSIONS_DIR`
moves it with them): `preferences-started` once, then one `preference` line per change, the last value of a key
counting. The first start with it seeded it once from the newest day journal that had them (each seeded line says
which: `seededFrom`). Only the session's own stay in its journal (the researchers taken off the shelf). The file also keeps which session the app was last in (`current-session`): a start opens it again. Spend
still counts per calendar day, in `research/<date>/`.

## Evidence

What an operation a request made found (contract 8 of
`docs/design/2026-10-06-captures-operations-evidence.md`): every cell a cell finder segmented, every
match of an example, an automatic-mask set a request used. One folder per population, in its day,
under `assets/evidence/` (`MICROMIND_EVIDENCE_DIR`):

```
evidence/2026-10-07/e3/
  capture.jpg     the view it was made on, as the camera sent it
  masks.npz       each member's own mask, as the model returned it (bit-packed, in its box)
  manifest.json   written last: schema, ids, what made it, the view's facts and scale, the members,
                  their measurements, revisions, links, and the other files' checksums
```

- **Atomic:** each file is written to `.tmp`, flushed and renamed; the manifest last. "Saved" is said
  only after that. The day's journal files it (`evidence-saved`), and at start each one is read back and
  checked against its checksums; a damaged one is reported "incomplete, not loaded", never loaded.
- **Kept:** nothing is deleted automatically. Past `MICROMIND_EVIDENCE_GB` (10 GB), new analyses are
  refused with the reason until older days' evidence is cleared by you.
- **A dataset being recorded** keeps each saved population's manifest and capture in its pictures
  (`evidence-<day>-<id>-r<revision>-<file>`).

**Motion tracks** (M3.2, `orchestrator/motiontracks.py`) are evidence too, in the same folder of their
day, one folder per item (`m1`, `m2`...: never given twice in a day; the journal's `tracks-made`):

```
evidence/2026-10-07/m1/
  capture.jpg     the last frame it was measured on, as the camera sent it
  tracks.jpg      that frame with the tracks drawn by the app (its captured view)
  tracks.npz      each track's segments: x, y in slide px, wall time, frame id (one array a segment)
  manifest.json   written last: schema tracks/1, what made it (execution, producer, version, settings),
                  the frames ((boot, seq), times, offsets), epoch, span, summary, scale, and checksums
```

Saved, journaled (`tracks-saved`) and read back the same way, under the same disk budget; a dataset being
recorded keeps the whole item (`tracks-<day>-<id>-{manifest.json,tracks.npz,tracks.jpg,capture.jpg}`), and
Save this session copies it whole too.

## Results

What a tool measured or made that has no store of its own (a count, a measurement, a
comparison, a clearer picture, a watch that fired; `orchestrator/resources.py`, Amendment 3 of the
captures plan). Each is a `result` event in the day's journal (`d1`, `d2`...: never given twice in a
day), and the pictures its tool showed are files beside it, under `assets/results/<day>/`
(`MICROMIND_RESULTS_DIR`): `d4-0.jpg`. Kept until you delete them; the session's index reads them back
after a restart.


## Fine-tuned models

Weights a fine-tune job made from your corrected cells (`make finetune-cells`; session knowledge K4), under
`assets/models/<name>/v<n>/` (`MICROMIND_MODELS_DIR`; `orchestrator/finetuned.py`):

```
models/cellpose-sam-tuned/v1/
  weights      the trained network (cellpose's own file)
  model.json   written last: the base model, the corrected populations it trained on, the sessions held out,
               the settings and losses, the weights' size and SHA-256, its licence tier (non-commercial)
```

- **Versions:** each run is the next version, written in a hidden folder and renamed into place once whole; an
  earlier version is never written over and nothing deletes one. The app uses the newest whole version.
- **Kept** until you delete them, like the other assets. About 1.2 GB a version: the job checks there is room first.
- **Never committed:** `services/models/` is git-ignored, for a checkout whose saves have not moved.

## The models folder is a store, not a save

`~/MicroscopeMind/catalog/` (`MICROMIND_CATALOG`; `catalog/README.md` "The models folder") holds the models' downloaded
revisions, one read-only folder each, and your own entries under `mine/`. Like the test assets store it sits beside the
three homes, not in them: every checkout, the sim and a second stack read the same models, and a downloaded revision can
always be downloaded again. The app never deletes a revision; a hand-made folder that isn't a whole revision is moved
aside (`.incomplete-…`), never removed. Tests use a temporary one.

Three folders inside it hold things of yours. The first two can't be downloaded again and are kept until you remove
them (the app never removes them); the third is temporary:
- **`own-trials/<id>/`:** a picture you chose to compare a capability on, with its outputs. Kept until you remove it
  (the folder); a picture that can't be decoded is removed at once, and nothing else removes one.
- **`shared/results/<sha256>.observationui.zip`:** the exact packed entry behind a result or snapshot you keep, so its
  picture can carry it (Download with capability). Kept until you remove it, also after the result is gone: the app
never removes one. Never sent anywhere by itself.
- **`shared/exports/`:** temporary portable copies being downloaded. Removed after the download is served, and any left
  from before (a download never started) is removed after an hour, when the app starts and at each new export
(`result_entries.clear_exports`). The original
  is never in it.

## Session bundles are exports, not saves

A session bundle (`make bundle`; docs/datasets.md, "Session bundles") is written into a folder you name, outside
the three homes: the app never reads it back. It only reads its sources (never under the app's saves, never over
anything) and keeps its own copies: every file copied except a dataset's camera videos, which are hard-linked (copied
across disks) and never written to. So nothing done to a bundle reaches your files, deleting a bundle never touches a
save, and a save pruned or deleted later leaves the bundle whole.
