# 9. Record is the dataset's switch, and Data is the light that checks it

Supersedes the switch in ADR 0008. Everything else in 0008 stands: the edges, the dataset's own state,
copies and not references, sources on every fact, and the live view never waiting.

**Context.** After 0008 the header had two pills side by side: Record (the studio and the camera) and
Data (the blue dot, the dataset's switch). On 2026-10-06 the user found the pair confusing. Recording
did not turn Data blue, and Data being blue did not say anything was recording. The user's direction:
Data should be an indicator, not a button. They trust Record because it says it is recording. Data
should say the dataset is being produced around the recording, and be a little more cautious than
Record, because losing a data session costs so much.

**Decision (with the user, 2026-10-06).**
- **One switch, Record:**
  - Record starts the dataset once the user has chosen the tab to record, before anything is
    recorded. Its Stop ends the dataset after the studio's file is saved, so the dataset's edges
    are around the recording, never inside it.
  - No model, rule or tool starts, ends or trims a dataset; the `dataset` command stays, sent only
    by Record.
  - Record's time counts from the dataset's start, which the server holds, so a reload keeps it.
    After a reload, Stop ends the camera recording and the dataset that the page no longer holds.
- **Data is a light:** it never switches anything, and clicking it opens the Datasets tab.
  - It turns blue only after it has seen each part of the dataset grow in the dataset's own folder:
    the camera video, its frame times, the frame facts (joinable to the video) and the records.
  - Anything that stops growing makes it amber, with the reason. The recording goes on.
  - After Stop it waits for the bridge to finish the video, checks that every file is in place,
    and says "Saved", or says what is missing. The result is written into the dataset (`endCheck`).
- **Recording without a dataset is possible but never silent:** if the dataset cannot be started, the
  studio and the camera still record, and the light is amber saying so.
- **The studio's own recording is part of the dataset:** it is hard-linked in as it grows.

**Cost.**
- A dataset needs the screen-share prompt: no tab chosen means no recording and no dataset.
- Every recording is a dataset: waste is accepted, a forgotten dataset is not possible.
- The check reads file sizes every second (a few `stat` calls). Growth windows of 3 to 5 s mean a
  real stall shows after about 4 s, and a pause shorter than that never shows.


## Owner amendment — explicit recording scope, 2026-10-22

Installed Beta acceptance showed that the system window chooser was unexpected when
Will intended to record the camera. Record now asks what to capture before requesting
permissions: Camera source uses the existing bridge recording and dataset without a
screen prompt; Workspace keeps the screen and available camera with explicit optional
app audio/microphone. This supersedes the cost statement that every dataset requires a
screen-share prompt. One Record/Stop lifecycle, dataset edges, owner-scoped bridge
release, and Data's independent durability check remain. The proposed Annotated view
must obtain its own implementation/media evidence and is currently named unavailable.
No unavailable channel is represented as captured, and permission denial cannot imply
that a recording started.
