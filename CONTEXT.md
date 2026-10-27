# ObservationUI: the words we use

The domain glossary. Module names use these words, and reviews and plans use them the same way.
How things are built is in `AGENTS.md` and the files it maps; why some things are the way they are is in `docs/adr/`.

## The project and its conversations

- **Project** (called **Session** in existing protocols and historical documentation): the durable owner (ADR 0017): its name, its conversations, what was seen in it, its memory (summaries, settled
  facts) and its work (researchers, cell outlines, motion, bookmarks, snapshots, results). A day can hold several and
  one can span days. Its id is the date it began, with ".N" for a date's later ones (`day/sid.py`). One journal per
  session (`services/sessions/<sid>/journal.jsonl`), rebuilt into `SessionStore` (old name `DayStore`). Only the user
  starts one; the app may suggest one. The app opens in the last one used. Opening one clears the live view and
  brings back the rest as history. Until named it is *Untitled project*, and the app names it from its first specimen;
  its title, top left, is where it is renamed and where sessions are switched. _Avoid_: day (for the project),
  timeline.
- **Tab**: a named arrangement within one project: the specimen, retained artifacts and notes placed for the current task. A project can have many tabs. Data belongs to the project; moving or closing a placement, or removing a tab, does not delete its retained Data. A work Tab remembers its selected input and recording position. Switching Tabs changes the displayed input, not the owning Project or an already captured operation. Previewing Data changes neither. Existing layout protocols call these arrangements `views`.
- **Open projects**: the projects available in the top navigation bar. Their controls switch projects; the workspace's tab strip switches arrangements inside the current project. Closing a project in navigation does not delete it.
- **Workspace**: where the user always is: the stage and the chat, with one session loaded or a new one starting. A
  new or empty session is the workspace with an empty stage offering the live camera, an asset or a dropped file.
  _Avoid_: home.
- **Asset**: a recording or picture of the user's in the library (`library.py`, ADR 0022), whether or not any session
  uses it: real microscope recordings, files added, Record videos, snapshots, clearer pictures. The library organizes
  them by what is in them, the user's corrections over the app's. One asset can be in several sessions. Each has a
  library-wide id (`a7`) and metadata (the app's, with the user's specimen, magnification, title and notes over it).
  Moved to the trash, it leaves the library and every session's list; the work made from it stays. Test footage is
  never an asset.
- **Category**: where the library files an asset by what is in it ("Invertebrates › Rotifers"): the vision model's
  best guess from one look at a new asset and what was seen while it was on stage, the user's correction over it.
  _Avoid_: group (cells by look), folder, album.
- **Preview**: inspecting saved Data without changing the active input or starting analysis. Opening a work Tab for a library asset associates it with the Project; previewing alone does not.
- **Examples**: the public corpus's recordings (`services/bench/corpus/corpus.json`), about 1,500, each with its
  specimen: a collection of their own in the library, apart from the user's assets. Added to a session, one is an asset
  like any other, with its specimen and scale, and is listed among the user's assets from then on. Played as test footage (a developer setting), the same clip stands for
  the live camera instead (ADR 0022's amendment). _Avoid_: example data, corpus, public library.
- **Source**: what a session looks at: the live camera, or one of the session's assets. Adding associates, and removing
  unlinks without deleting anything. The one on stage is what the view shows. Its Record videos, snapshots and clearer
  pictures are its assets, not products. Each says the other sessions that use it ("Also in"). The user is told *this
  session's assets*. _Avoid_ (to the user): sources.
- **Returning**: opening a session other than a new one. The view lands on that session's last source, never the last
  session's: a recording paused where it was left, a picture held, or the live camera. A new session opens on the empty
  stage instead.
- **Day**: only a calendar date. The research spending cap counts per day, per-day stores keep their folders by date
  (`evidence/<date>/`), and the performance logs are dated. A session's *days* are the dates it touched.
- **Run**: one start of the app. Live outlines, positions and following are the run's and never come back after it.
- **Conversation** (thread, `t1`, `t2`...): a stretch of talk inside a session, started only when
  the user asks ("new topic"), resumed by name ("back to the blood smear"). The app never
  starts one by itself. The *active* conversation is the one open now.
- **Turn**: one thing said in a conversation: by the user, by the app (assistant), or a note
  about what the app did. Turn ids count up through the session and are never reused.
- **Line**: a turn as the chat showed it (the model that answered, its actions and timings,
  later updates such as a tool finishing).
- **Asked**: the session and conversation a request was made in, fixed when the request
  starts. Everything that answers it, however late, belongs there, even after the user opened another session.
- **Subject**: the specimen in view, as the running look confirmed it (two looks agree) or as
  the user named it. It belongs to the conversation; a new subject inside a conversation is
  recorded there, never a new conversation.
- **Summary**: a conversation's older turns, folded in by the fast model in the background.
- **Digest**: the rest of the session in at most 900 characters (which session, other conversations, subjects,
  preferences, researchers' work, what was settled).
- **Recall**: looking up something said earlier in the session (on any of its days), only when the user refers back;
  in another session only when the user names it.
- **Preferences**: the user's settings (who leads, the look of outlines, the level, the research
  cap's amount, the models chosen from now on, the scale). They are the user's, not a session's: kept
  until changed in one file beside the session journals (`services/sessions/preferences.jsonl`,
  `day/prefs.py`). Only the session's own (the researchers taken off the shelf) stay in its journal.

## The camera

- **Camera bridge**: the one program that opens the phone's camera (`experiments/iphone-camera-feed`) and makes the
  chosen live source one feed of frames for the app.
- **Live source**: where the bridge's picture comes from: the iPhone (Continuity Camera), Blackmagic (the Blackmagic
  Camera app's stream; always the app on the iPhone, never a Blackmagic camera) or the other camera (any camera this Mac
  sees, or a stream address), or No camera (pictures and videos only: nothing connects; the checklist's mode `off`, never
  a fault). Not a session's Source, which is what a session looks at (the live camera or an asset).
- **Link**: how Blackmagic's stream reaches this Mac, the cable or Wi-Fi. The bridge picks it, never the user.
- **Checklist**: the camera's facts as the bridge read them (`camera_doctor.py`), in order. The first that fails has
  the fix. The camera page and the Phone panel show it.
- **Not connected yet**: the checklist's `connect` mode: the camera needs a step from the user (open the app, lock the
  iPhone, plug it in). A guide, never a fault. Something that broke (a frozen picture, a crash) is a **problem**.
- **Camera page**: the bridge's own page (`/camera/`), to get the picture live with nothing else running.
- **Phone panel**: the same checklist and the phone's controls in the studio's header.
- **Phone check**: the guided check that learns, on the phone, which of Blackmagic Camera's settings this Mac can
  change safely.
- **Manifest**: what the Phone check proved, per Blackmagic Camera version (`blackmagic-api.json`). Only writes it marks
  safe are sent.
- **Smoothness**: how evenly frames arrive (smooth, slight stutter, stutters), from the gaps between them.

## Seeing and acting

- **Mind**: ObservationUI as one assistant that speaks as "I" (ADR 0016, `prompts.MIND`).
  - **Eyes:** the look.
  - **Voice:** GPT-Live, speaking from what the eyes last saw.
  - **Reflexes:** hands.
  - **Closer look:** the brain looking again and planning.
  - **Instruments:** the outline, cell, measuring and motion models.
  - **Researchers:** helpers it sends off, credited by number.

  Parts are named when asked and in Activity.
- **View**: the live picture through the eyepiece.
- **Look** (the running look, Sight): one fast vision call after the slide settles that
  describes the view and boxes its notable things (`s1`, `s2`...).
- **Outline** (`o1`...): a thing marked on the view, kept in the coordinates of its frame.
- **Moment**: everything a model is told about one request, captured once: the view, what is
  outlined and on screen, the researchers, the conversation, the digest, the preferences.
- **Hands**: acting on spoken words as soon as they are heard, before the voice hands over.
- **Hand-off** (delegation): the voice (GPT-Live) handing a request to the brain.
- **User leads**: the user finds things themselves; the app points and names only when asked.
- **Level**: how the user wants things explained, *simple* (the default) or *technical*,
  and whether a hint comes before the answer (*hints first*). A preference the user sets
  (settings, or `set_level` by voice or chat); every model writing for the user is told it in
  the same one line (`learner.py`), and nothing rewrites an answer for it.
- **Board**: what is on screen now, as one versioned state: the outlines (with their boxes and
  sizes), cells, the running look's things, the magnifier, the result viewer, the selection,
  and the latest changes. "That", "the second one" and "close that" are resolved against it
  when a step runs, not when the request was decided; but "that" is what the request's own steps
  pointed at, else what the user was pointing at when they made the request (**When asked**: the
  selection, the cells layer's selected cell and the magnifier, saved on the request; clicks by their own
  time). A selection gone since is said, never replaced by another.
- **Plan**: what one request asks the app to do, as ordered steps. **Step**: one tool call in
  a plan; a step on the board runs only after the steps before it, and sees the board they
  left. What the user is told comes from what the steps did, never from what the model
  expected them to do (ADR 0006).
- **Finder**: how a request's "what" becomes outlines: by id, name, example, appearance or
  pointer; anywhere, inside something, or near it; one, all or a number. Several ways (a box
  from the vision model, SAM 3.1's matches, the cell finder's cells (Cellpose-SAM or micro-sam),
  look-alikes) are options behind it.
- **Capability**: one thing one actor can do: *you* (in the studio: click to outline, drag a
  box, hold H), the *agent* (a tool: highlight, find, track_motion...), *on its own* (a
  background process: the running look, checking outlines, following swimming things) or a
  *researcher* (work you ask for: a video, a lesson). "You click to outline" and "the agent
  highlights" are two capabilities. Each sits in a family, depends on model roles, and carries
  your verdict. `contract/capabilities.json` is the one list (ADR 0007); the Capabilities page
  and the agent's `capabilities` tool show it.
- **Role**: a job a model, method or policy does for capabilities, with options: a *model* role
  (outlining: EdgeTAM, SAM 2.1, SAM 3.1...), a *method* (pointing, tracking) or a *policy* (what
  happens on a poor view). An option group in `options.py` is a role; a model fixed in the code
  (the video model) is a role with one option, read from its constant.
- **Name**: what a model or option is called: one each, without commas, the same everywhere; where it runs is
  said beside it ("EdgeTAM (on this Mac)"), not in it, unless two variants differ only by that ("SAM 3.1 (cloud)",
  "SAM 3.1 (this Mac)"). A **family** is one name for several variants ("SAM 3.1", "Cellpose-SAM"): said alone it
  means the family's default variant, and the answer says which. What a name means is decided in one place
  (`orchestrator/names.py`), exactly, never by a part of it. _Avoid_: label (the code's field), alias.
- **Verdict**: your flag on a capability: works, broken (with a note) or not yet checked. Kept
  apart from availability (whether the code can do it here, now).
- **Generation**: one immutable, complete runtime of a catalog entry (an environment, a Fiji generation, a published
  file revision), named by its contents; the **active generation** is the one new work uses, and work already holding
  one keeps it. An **update candidate** is a generation prepared beside the active one. A **maintenance policy**
  (manual, review, auto, pin; ADR 0025) is a person's record of what may happen to an entry's later updates unattended;
  **promotion** makes a proven candidate active, **rollback** makes the one it replaced active again, and a **pin**
  holds a generation for an owner across restarts. Ticket 59 keeps these words and the update states. _Avoid_: version
  (in this glossary a version of a piece of work), candidate alone (`candidates.py`'s regions to point at).
- **Model check**: the app's own run of one model on a real frame (Check now, `make capability-check`): works,
  broken (with the error's words), not installed, needs a key, not in this edition, or not checked (with why).
  Shown beside your verdicts, never in their place; a model broken on this build is refused when named. _Avoid_:
  verdict (yours), availability (what the code says before running anything).

- **Edition**: which licences this copy may use (`MICROMIND_EDITION`): *research* (the
  default: also non-commercial components such as Cellpose-SAM and micro-sam's light-microscopy
  weights) or explicit *commercial* (open and terms components). An invalid nonempty override
  uses the commercial gate. `docs/licences.md` lists every component's tier (ADR 0005, amended 2026-10-08).

- **Measurements table:** what a cell population or a motion item measured, one row per cell or track, as the user
  sorts and downloads it (CSV). Its sizes are in µm only by the capture's own scale. _Avoid_: spreadsheet, report.

## Researchers

- **Researcher** ("Researcher 7"): background work the brain starts, in its own process. Said to the user as an *AI
  researcher* (the side bar's AI researchers): the people using the app are researchers too.
  Its number is fixed for the session (each session starts at Researcher 1).
- **Run**: one stretch of a researcher working: the first run, or later a follow-up
  (answer, continue) or a revise. A researcher has at most one run at a time.
- **Work**: what a researcher made: written results, a lesson, a picture, a video.
- **Shelf**: the researchers on the Working strip, one pill per researcher that is working, needs
  you, or finished and not yet opened, each with its lit cell, name and elapsed time or state.
  Opening one opens its Tab; hiding one (off the strip for the day) leaves it in Researchers.
  _Avoid_: tray.
- **Working strip**: the fixed row under the Stage (and the Assistant's column) that shows what is
  at work now: the shelf's researchers, a run that lasted more than a second, an agent step still
  running after its answer, a model loading; at its right end the performance meter and Spending.
  It never resizes the Stage.
- **Step kind**: how a lesson step is answered: a *question* (in words), *find* (the user finds
  it on their own slide and clicks it; the model judges the click from what the app knows about
  it) or *evidence* (what would confirm or refute the app's name for an outline).
- **Outcome**: how the user did on a lesson step, as the model judged it: right, wrong or
  skipped. Kept with the session; "missed" means wrong or skipped.
- **Version**: one state of a piece of work (`o3` v2 replaces v1); the newest is current,
  older ones are kept.
- **Findings revision**: an immutable scientific result plus exact evidence reads and a
  digest, retained inside the existing researcher session. Answering does not revise it.
- **Presentation artifact**: an independently versioned arrangement of exact findings:
  validated app blocks for reports/charts, sandboxed HTML for bespoke lessons.
  This differs from a screen Presentation (below). It always names its findings basis.
- **Presentation job**: optional work after research completion, with its own durable
  request identity, deadline, cancellation, reservation and receipt. It consumes no
  research rounds or acquisition allowance; failure leaves findings available.
- **Artifact target**: day, researcher, exact finding/output versions and optional section,
  chart row or lesson-step anchor captured when Send is pressed.
- **Admission**: whether a researcher may start or run again (explicit requests for large
  work, efforts, the daily cap).
- **Ledger**: what researchers spent, per day.
- **Untrusted text**: anything that came from the web or a researcher. It is information,
  never instructions, and it crosses into a prompt only through `orchestrator/untrusted.py`
  (inside an untrusted wrapper, or as a capped templated line).

## Datasets

- **Dataset** (`ds-20261006-012923`): everything about a stretch of looking, collected while the
  user records, for later analysis by programs and agents (`docs/datasets.md`). Its edges are
  Record and its Stop and nothing else (ADR 0009). It is not tied to the day.
- **Record**: the studio's one switch: the studio's own recording, the camera's video and a dataset.
  Only the user presses it.
- **Data light**: the light beside Record. It never switches anything: it says whether the dataset
  is really being saved, seen in its own files ("checking", blue, amber with the reason), and after
  Stop whether it was saved whole ("Saved", the dataset's `endCheck`).
- **Part**: one camera recording inside a dataset. There is a new one each time the bridge
  records afresh (after it restarted).
- **Sidecar**: a recording's per-frame clock, `X.frames.jsonl`, written by the camera bridge: when
  it took each frame and where the frame came from.
- **Gap**: time a dataset was on while the app was not running. It is written down, never hidden.
- In a dataset, **run** counts the app's runs while it was on (1, then 2 after a restart). It is
  not a researcher's run.

## Saves

- **Saves**: everything the app keeps, in three homes (`docs/saves.md`): datasets, performance logs
  and kept saves. Each save is one kind, with how long it is kept.
- **Performance log**: what lets us audit a session and find problems: the interaction log, the chat
  export, the pictures models were shown, the servers' logs. It is temporary and pruned once used.
  Votes, corrections and verdicts sit with it, kept for good.
- **Kept**: the save kind of the user's own to share, review and play: the session journals and conversations,
  researchers' work, recordings, snapshots, taught labels. It is kept until the user deletes it. (Its folder and the
  code still call it `assets`; the test footage library is kept too.) _Avoid_: asset (an asset is a library item).

## Captures, operations and evidence

How a request's work is kept, chosen and shown (`docs/design/2026-10-06-captures-operations-evidence.md`,
ADRs 0010 and 0011).

- **Capture**: the view a piece of work is about: one frame's bytes and facts (size, eyepiece disc,
  epoch, offset, scale), held by whoever needs it, inside a memory bound. Not a **Snapshot**, which is
  a picture the user asked to save, with the outlines drawn on it.
- **Ticket**: one request's claim to change the screen (a turn, a click, a command). A clear, a
  cancel, a removed target, a tracking reset or a new day supersedes it. A superseded request keeps
  what it found but changes nothing on screen.
- **Operation**: a kind of work with a contract: what it accepts (point, box, the whole field, an
  example, text) and what it returns. Outlining a click, segmenting the whole field, finding things
  like an example, following and measuring are different operations. An **implementation** is one
  way to do one (EdgeTAM, micro-sam, Cellpose-SAM, an algorithm, or a composite); an option group
  chooses the default when there is a choice.
- **Policy** and **origin**: how a run goes and who started it. *Raw* runs one implementation or
  fails; a *route* adds that implementation's declared fallbacks. An explicit choice, by the user or
  the brain, runs raw and is never substituted (ADR 0011).
- **Job**: one computation on one capture with one set of inputs. Requests that need the same job
  share it but keep their own tickets; cancelling one never cancels another's work.
- **Execution**: the record of a job: what was asked for, every attempt, and the implementation
  whose output was used. Reusing a job is recorded as a use, never as a new run.
- **Evidence** (`e3`): what an operation a request made found, kept with its capture and execution.
  A **population** is evidence of many things at once; each is a **member** (`c12`) with the
  model's own mask and measurements computed by the app. Counts are of segmented instances in a
  stated region, never of cell types.
- **Revision** (`r1`): a correction of a population the user asked for (members removed, merged into one,
  or the last correction undone). Revision 0 is the model's own output and never changes; a revision
  records who, why and what it changed, so it can be undone, and only what it touched is measured again.
- **Placement**: whether a result can be drawn on the live view. *Verified* means its own pixels
  matched the current picture at the predicted place, recently enough. Otherwise it is *unverified*
  (never "moved" unless movement was measured), and it is shown on its captured view.
- **Presentation**: something open on screen that shows evidence, a capture or a resource (a close-up,
  a panel, an overlay), with where it shows (live or on its captured view) and its coordinate space.
- **Resource**: anything a session made that the user or the brain can pull up again: evidence,
  results, researchers' work, snapshots, things named, labels taught, the scale, datasets and
  recordings. The resources index lists them with what made them, how fresh they are and what can be
  done with them.
- **Product**: an analysis the session made as data to keep and reuse: cell outlines, motion tracks, researchers'
  work (their pictures too), screen recordings, bookmarks, things followed, footage held and reference photos.
  *Made* is the user's word for them, and *Made in this session* lists products only. The user's own recordings and
  pictures of the specimen (Record videos, snapshots, clearer pictures) are assets, listed as the session's assets, not
  products: one home per thing (the user's decision of 2026-10-12, ADR 0022). A named outline, an answer, a count or
  measurement of one thing, an outcome and a taught label are resources but not products. _Avoid_: artifact,
  creation, data (Record's Data light and Datasets have it).
- **Activity**: the newest-first account of what happened: each request with its steps (what was done,
  which model found, drew and named it, how long it took, whether it worked), the user's clicks and
  studio actions, settings changed (by the user or by the app), models loaded and unloaded, and
  researchers' progress. Every line is from a fact the app recorded. A chat answer's (i) opens its request's
  activity. Answers are not filed as products. _Avoid_: log, history, trace (at Use).
- **User annotation**: the user's own tentative observation or display name on one retained resource
  or population member (`annotate_resource`). Its stable ID, source day/capture/revision, attribution,
  and every edit are kept in the day's journal and read through the resource index. It does not name
  a live outline, teach a label, or confirm a biological identity. A failed journal write is not a save.
- **Saved-picture inspection**: actual pixels read from an exact retained resource and picture number,
  for a question about that artifact. The answer receives at most two bounded images with their source
  and hashes, inside the existing three-call turn budget. Captions alone are metadata; the live frame
  never substitutes for a missing saved image. Reopening a result uses its existing ID and original
  artifact; it does not rerun the operation that made it.
- **Screen report**: what the studio says it shows (every layer and panel, what it shows, where, its
  stacking and its style). A change on screen is done only once a report shows it.
- **Verb**: one of the actions any presentation takes (show, hide, opacity, colour, fill, edge,
  emphasise, open, close, move, resize, front); a kind that does not take one says why.
- **Resources index**: the session's one index of resources (`orchestrator/resources.py`), read the
  same way by every reader: the brain (`resources`), researchers (`session`), the studio and programs
  (`/api/resources`). It reads each store where it is, never copying it; nothing from it is put in a
  prompt unless pulled.
- **Result**: what a tool measured or made that has no store of its own (motion, a count, a
  measurement, a clearer picture, a watch that fired), kept as an index entry built from the tool's run:
  its arguments, the frame, the executions it used, its summary, its values and its pictures.
- **Producer declaration**: what each tool, studio command and background producer says it makes
  (`orchestrator/produces.py`): an entry kind, "temporary" with why, or "none" with what it does
  instead. A producer without one fails the tests.
- **Provisional answer**: an answer given by eye at once ("by eye, this one looks the biggest": a *first
  guess*, outlined and marked "?"), checked by a measurement in the same answer. The measurement then
  says the guess holds, or corrects it (where the guess ranks), or that the two can't be compared and
  why. A first guess is never left standing as if it had been measured. Not the *first-guess ring*, the
  instant ring drawn while a look is still on its way (POINTING "snap-first").
- **Named comparison**: the models the user names do the same work on the same view, and what each
  made is shown side by side and measured. Unlike the blind A/B/C test, names are shown and nothing is
  voted; a result reused from an earlier run is said to be.
- **Bookmark** (`b2`): a place on the slide the user asked to keep, with their words for it and a
  picture of the view then. The way back to it is measured from the slide's movement since, or said
  to be unknown; never guessed.
- **Coordinate spaces**: the *live frame* (frame pixels, moved by the slide's offset), a *capture's
  image* (its pixels, with enlarged views as a crop and scale of it) and the *workspace* (the
  studio's screen). Every overlay, close-up and pen mark says which it is in.

## Session knowledge

How evidence becomes knowledge that grows through a session (`docs/design/2026-10-07-session-knowledge.md`,
ADRs 0012 and 0013).

- **Enrichment**: an operation that adds facts about members it did not find: their looks (an
  embedding), their group, names ranked as data, their light. It returns a **column group**, kept
  beside the measurements with the execution that made it. It never changes the masks.
- **Group**: members an algorithm put together by a stated criterion (their looks, for example). A
  group is a group: never a type, health or viability. The user may name one; a model's names are data.
- **Same object** and **same kind**: the two kinds of identity link, never mixed. *Same object* is one
  physical thing in two captures (by continuity, re-finding, promotion, a linking model, or the user).
  *Same kind* is only alike (looks, group, the user's label). A missing link means *can't tell*.
- **Individual** (`i4`): one physical thing across captures, joined by same-object links, with its
  history (where and when it was seen, and what was measured). Its confidence is its weakest link.
- **Outcome**: what happened when an implementation ran, plus what the user unambiguously did about it
  (corrections, confirmed or corrected names, votes, verdicts). Outcomes are facts the brain looks up
  with the `outcomes` tool; they are not in its prompt by default, and code never switches by them.
- **Session bundle** and **replay**: a dataset with its window's evidence, executions, outcomes and
  journal, run again through a new model, route or prompt on exactly its captured inputs. The app
  recommends a model or default only when it won on evidence like this; anyone can still choose any.

## The partner

How the user's UX document (`docs/design/2026-10-07-microscopemind-ux.md`, “JARVIS for Microscopy”) is
built (`docs/design/2026-10-07-the-partner.md`, ADRs 0014 and 0015).

- **Depth**: how far into the machinery a surface goes.
  - *Use* needs no technical knowledge.
  - *Understand* (“how do you know?”, what made it) is one step from any result.
  - *Inspect* (catalogue, execution inspector, comparisons, options) is always reachable.

  Not the same as *Level*, which is how answers are worded. Technical words are answered directly when asked or used.
- **Title**: the plain name of an index entry or presentation (“Cell outlines · Cellpose-SAM · 18:04”: what, the model that made it when one did, when), used at Use beside its id.
- **Next step**: what a refusal tells the user they (or the app) can do instead (`ToolFailed.next`). A refusal without one declares why.
- **Look deeper**: one escalation on any result. The brain chooses what deeper means (a closer look, a measurement, following, a quick researcher) within what the user's intent authorizes.
- **Finding**: what a researcher returns. Its statement and prose, plus references (entries, members, captures, measurements, media by kind), limitations, and for each claim the references it rests on. A claim whose evidence is gone is limited, never left standing.
- **Showing**: “Show me” as data. Beats the model chooses from the general verbs, addressing artifacts by reference, each done only on the studio's report.
  - It owns only its own changes.
  - The user can interrupt it.
  - *Restore* reverts only its changes nobody has changed since.
- **Trail**: where a followed outline went on its own, from the follower's verified places, slide motion taken out (`trail.py`). Broken wherever its place was not known, never joined. Not a biological Track (an analysis workflow).
- **Evidence request**: a researcher asking for what it lacks (“15 seconds at this field?”). The user decides; Record stays theirs.
- **Offer** and **offer budget**: something the app suggests unasked, as a question, judged worth saying by the model, within its mode's budget, never repeated without new facts. Ignored is not declined.
- **Media kind**: specimen capture, derived or annotated view, external reference, or generated illustration. Evidence clips come only from recorded footage.
- **Essential provenance**: what interpreting evidence needs (source, time, producer and version, transformations, measurements, research lineage). Kept with the evidence whatever the logging switches say.

## Arranging a session (workspace entrypoint, 2026-10-16)

- **Workspace view**: a named arrangement inside one session. It places the specimen and references to retained artifacts and session notes. Main is the initial view; additional views can start empty. Switching views restores that Tab’s selected input and arrangement, not the session, source ownership, conversation or running work. Removing an item or view does not remove its Data.
- **Session note**: editable user-authored text retained as an annotation with session scope, stable identity and revision history. It does not imply an observation of a particular capture. Closing its window preserves the note in Data. Showing it in multiple views refers to the same note.
