# 8. A dataset is switched only by the user, and its edges are the switch

*The switch is now Record, and Data is a light that checks it (ADR 0009). The rest stands.*

**Context.** On 2026-10-06 a sperm sample was looked at for 40 minutes, and nothing of it was
"the data". The raw camera recording had no index and would not open (recovered by hand). The
recorder stamped frames at a made-up 30 fps, so the file was 24 s short of the time it covered.
The running look renamed the subject 13 times. The scale used the default field number while a
25x eyepiece was on.

The user wants a dataset: everything about a stretch of looking, kept for later analysis by
programs and agents. The edges should be rough but 100% reliable. Waste is fine; losing the core
of a collection effort is not. Recording always was ruled out (too much CPU and disk).

**Decision (with the user, 2026-10-06).**
- **Edges:** a dataset is collected only while the user's switch, the blue dot, is on.
  - On is its start and off is its end.
  - No model, rule or tool starts, ends or trims one. Voice and chat can say whether it is on,
    never switch it.
  - Labels (what the running look saw, quality flags, the objective) are kept as data for a
    later reviewer, never used to cut.
- **State is the dataset's own:** `datasets/<id>/events.jsonl`, summarized in `dataset.json`.
  - This is a second store the app reads back at start, beside the day journal (ADR 0001), on
    purpose: a dataset outlives midnight and "start today afresh".
  - After any restart the same dataset carries on, with the gap written down. Only the user
    turns it off.
- **Copies, never references:**
  - Records are copied as they happen.
  - Pictures and the camera's video are hard-linked (the same file on disk).
  - Deleting logs or recordings later never breaks a dataset. The originals' paths and sizes
    are noted as a second way to cut the window back out.
- **Every fact says where it came from:** measured, user, default, requested or unknown.
  - Times are what they are: the bridge's `bridgeAt` is when the bridge took a frame. The
    video's own timestamps are not times.
  - Whether a frame was the camera's or test footage is said only by the bridge, in its sidecar.
    The orchestrator copies that file and never reads it (AGENTS.md, test footage).
- **The live view never waits:** taps only queue, and one thread writes. Under pressure only
  per-frame facts may be dropped, counted; records never are.

**Cost.**
- A forgotten dot means nothing is collected, by design.
- A dot left on collects everything until it is turned off, including a restart days later
  (the gap is recorded).
- A dataset that joins a recording the user started with Record holds more video than its
  window (the sidecar's times say which frames are inside it).
- Hard links share disk with the recordings until both are deleted.
