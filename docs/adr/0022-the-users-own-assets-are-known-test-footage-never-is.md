# 22. The user's own recordings and pictures are known to the app as sources; test footage never is

**Context.** A session looks at a specimen through the live camera, a recording or a picture. To open, add and remove
those from a session, the app must know which recording or picture is on stage, and it must keep them with the
session. Until now the app knew nothing about the feed. Its only rule was that test footage is invisible downstream
(AGENTS.md "Architecture"), so judgments made on footage carry over to the microscope. Will's design of 2026-10-12
(`docs/design/2026-10-12-session-sources.md`) settled it.

**Decision.**
- **The user's own recordings and pictures are assets** in one library (`orchestrator/library.py`):
  - real microscope recordings and files added in the studio;
  - every Record dataset's camera video, snapshots, and clearer pictures.

  Each has a library-wide id (`a7`), given once per stable key and never reused, and metadata. The app fills what it
  knows: kind, when, length, camera, optics at capture, origin session. The user's edits win: specimen, magnification,
  title, notes.
- **A session's sources** are the assets associated with it, plus the live camera. Opening associates. Its Record
  videos, snapshots and clearer pictures join on their own. Removing unlinks: the library keeps the asset and the
  session keeps the work made from it, but the asset's specimens leave the session. Each is a `source` event in the
  session's journal.
- **The agent reads it as metadata, not as instructions.**
  - **The stage line:** while an asset is on stage, the models get one line of facts about it. The live camera gets
    no line.
  - **In full:** `resources(id="a7")` gives the rest.
  - **The scale:** an asset's own magnification is the scale while it is on stage, and with none, no sizes are stated.
  - **The specimen:** a specimen the user gave the asset wins over the looks.
- **Test footage is never an asset:** the public corpus, bench clips and camera tests off the microscope. It stays in
  the camera chip's test menu and stands for the live camera: the studio tells the server the stage is "live". So
  everything said about test footage in AGENTS.md still holds.

**Cost.**
- **The server now plays the feed:** a `source` command posts to the camera bridge's `/feed` itself, besides the
  studio's camera menu.
- **Asset ids are the library's**, not a session's, so they live outside the session ids' rule (`day/ids.py`).
- **Removing a source rebuilds a session's subjects** from its journal's subject events, and the catalogue folds the
  same rule.

**Amendment (2026-10-12, after the first live try).** Example data, the public corpus's recordings, is in the library
too, apart from the user's own (`GET /api/library?scope=mine|example`), and the side bar's Assets section shows both:
Your recordings and Example data. Example data opened from Assets is an asset like any other: a session's source, with
its specimen and scale, kept in the session's journal. Played from the camera menu's test footage, the same clip still
stands for the live camera, and the app never knows it. The camera menu keeps test footage only: the user's own
recordings, which it used to list, play through Sources from anywhere they can be played (Assets, the Footage page),
since the first live try showed one played as test footage never reached the session's sources.

**Amendment (2026-10-12, late: the workspace and the assets library, `docs/design/2026-10-12-workspace-and-assets.md`).**
Will's model is a video editor's: assets are the footage library, a feature of its own, and a session is the timeline
that uses some of it.
- **Adding associates, not opening.** Previewing an asset in the library (All assets) touches no session; adding it, or
  dropping a file on the stage, puts it on stage and makes it one of the session's. Organizing a hundred clips must not
  fill a session with them; what is on stage in the workspace still always belongs to the session.
- **Words.** To the user a session's sources are "this session's assets" (the side bar's Assets); the live camera is the
  header's camera control, never a row. Example data is **Examples**, a collection apart from the user's own; one used
  in a session is listed among theirs from then on.
- **Test footage** is a developer setting in the gear, no longer the camera's menu: it is what made one corpus clip mean
  two things. Everything else this ADR says of it holds.


**Amendment (2026-10-22, owner acceptance).** Will could not find the live camera after
opening a saved recording and created a new Project to recover it. The later request
explicitly supersedes the “live camera is never a row” rule and the shared-input Tab
behavior: Inputs always offers **Live camera**; opening saved Data previews it without
retargeting analysis. **Open in Tab** opens or returns to that input's work Tab. A Tab
remembers a live/asset reference and recording position, while Data remains owned by
the Project/library. Selecting the Tab restores through the existing source command,
paused for saved media, and never reruns analysis. Existing Tabs retain their shared
source on migration. Already captured operations retain their original input; a
busy live-input operation, recording or voice session may require settling before
a source change, with preview still available. Layout format 4 preserves these input
references and refuses older readers/writers rather than silently losing them.
