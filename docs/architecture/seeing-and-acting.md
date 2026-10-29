# Seeing and acting at once

Part of the working context the root `AGENTS.md` maps. "Above" and "below" may mean parts it lists in
other files.

- Sight (`orchestrator/sight.py`) is the app's running look: after the slide settles following
  a change, one fast vision call describes the view and boxes its notable things (s1, s2...),
  stored with its frame so the boxes follow the slide; a look from before the slide moved
  offers nothing to point at. Runs only while someone is connected; option SIGHT.
  - It also looks again when something new appears while the slide is still
    (`quality.StillChange`): a compact change in the motion-compensated view, such as
    something swimming in.
  - A change of light, focus or a whole-view vibration is not new content.
  - At most 4 such looks a minute. Each decision is logged as `still-change`.
  - Each look is a paid vision call, so time alone never buys one: a minute after its last look it looks again only
    if the picture changed since (`FrameInfo.changed_at`, `quality.Content`: a change of light or focus). An
    unchanged picture, a still on the Stage or a frozen feed, is looked at once (Beta 0.5.1 took a Gemini look every
    60 s on a still picture).
- The slide map (`orchestrator/slidemap.py`, W5-4): each steady, unblurred frame's eyepiece disc is
  pasted at quarter size at its offset into one map per motion epoch (at most 4, each at most 1536 px
  a side), on its own thread; nothing is blended or invented. `where_is` says where an outline is from
  the view now (fields of view, µm with a scale) from the offsets, and that it can't tell across a lost
  epoch. `show_map` or the studio's Slide map button opens it in the side column
  (`apps/studio/src/slidemap.ts`; a bottom sheet on phones), served at `/api/slidemap.jpg`; the studio
  draws the view's circle and the outlines over it.
- Bookmarks (M3.3, `orchestrator/bookmarks.py`, tools `bookmark` and `way_to_bookmark`, family "map"): "bookmark
  this" keeps where the view is (the eyepiece centre in its frame's motion epoch and offset, in this run),
  a small picture of the camera's view, the user's words, the conversation, the subject and the objective,
  per day under `bookmarks/<day>/` (an asset), and logs it whole (kind "bookmark"). The way back is
  measured from how far the slide has moved since, as `where_is` is; across a lost epoch or from another
  run of the app it says it can't tell, and the picture shows what to look for. Bookmarks are in the
  index (kind "bookmark"); opening one shows its picture in the magnifier, said to be the camera's view.
- A first guess, then the measurement (M2): "which is the biggest cell?" with nothing measured is
  answered by eye at once, the guess outlined and called a first guess, and every cell is segmented and
  the measured one highlighted in the same answer (`highlight` `checks: "target 1"`). The guess is made
  one in code (the answer says "a first guess", the outline is marked "?"); the measurement says it holds
  (the measured outline takes its place and its name), or that it was not it and where it ranks (its name
  stays off the measured cell), or that the two can't be compared and why; it is never silent. Only an
  outline this request's look made, unnamed since, is taken off (`population.member_at`, finding 80,
  `tests/test_provisional.py`).
- Groups by look (session knowledge K1, `orchestrator/enrichment.py`): every model adds facts about the same
  cells as **column groups** beside the measurements (`population.columns`), each with the execution that
  made it, run as operations (`looks`, `groups`, `light`, `names`, `your-label`; each declares its output keys,
  so a run that made something is never recorded "empty") and reused as kept evidence for the same revision.
  `group_cells` embeds each cell's crop with option EMBED's model (its own group per model: two models'
  vectors are never compared), groups them by k-means with the number chosen by silhouette (2-8, its score,
  every k tried and what the groups bench measured said as data; "into 8 groups" can be asked) and measures
  their colour inside each cell's own mask (read back off the loop in reserved room, never kept);
  `describe_groups` gives each group's size, measured medians and examples, and, asked, MedSigLIP's names for
  it as data with its calibration (measured one picture at a time, said so; blood and Pap smears only). A
  group is cells that look alike to the model, never a type. One grouping is read everywhere (describing,
  naming, colouring, querying: `enrichment.grouping`): the request's `use`, else option EMBED's, else the
  newest, said with its picture model. Naming a group ("those are platelets", finding K1-5) keeps its
  representatives with the population as that name's examples, apart from your taught examples (they are the
  app's picks, so naming a click's taught line never counts them), and gives every cell its nearest named
  group by look ("nearest group you named"), a label only from the picture model's measured floor (borrowed
  from the seen-before bench; none for a model never measured), never written into a label; earlier names are
  carried over corrections and regroupings, and any replaced or dropped is said. The cells' layer can be coloured by
  look group or your label (`present`, a legend). One stale check (`Population.stale`): a group is stamped with the revision
  its job was asked at, so one made before (or while) a correction is refused by every reader with "group them
  again", and the layer never sends colours by a stale or replaced group. Vectors stay resident
  only until saved (`vectors.npz`), reserved in the unsaved allowance; a population is read back without them
  when they are lost. Bench: `bench/groups_bench.py` (`docs/bench/groups.md`).
- Identity (session knowledge K2, `orchestrator/identity.py`; ADR 0012): two kinds of link, never mixed.
  *Same object* (promotion, a mask outlined, continuity, a carry, a linking model with its score, the user's
  "that's the same one": `same_one`) joins an **individual** (`i4`, numbered through the day, never reused; its
  confidence its weakest link); *same kind* (looks, a group, a taught label) and a box overlap never do.
  - **Runs:** outline nodes and observations carry the run they were made in (`capture.RUN`, with the frame's
    boot and seq); another run's are history only, never this run's outline with the same id, whatever their
    time (a population's link names an outline only when made in this run, or older with no run, and no older
    than it). Following ends with the run. A cell segmented before a restart is not followed ("outline the cells
    again on the view now"), and an outline made on a view read back from another run (no epoch) is never re-found
    by the hub, carried or placed: its offsets were that run's.
  - **Carries:** an outline brought back by its own pixels after a gap, a jump (template re-find), a rejoin or
    by the follower is a link with that basis and the match's score (`hub.carried`, off the frame path).
  - **Following:** `follow_cells` ("keep an eye on the odd ones") outlines chosen cells from their own masks; the
    follower follows them before later clicks (at most 4; the longest followed stops, said); each is measured
    again every 15 s while its own pixels match where it is, for up to 30 minutes in this run (its box grown on
    each side, the new outline counted only when it is the same thing, off the loop).
  - **Where and whether it changed:** `individuals` says where each is from its outline and the slide's measured
    movement only while its own pixels match ("I can't confirm it's still there: ... since hh:mm:ss" when they
    don't), or "can't tell: continuity was lost at" when the view left its epoch, or "an earlier run of the
    app" (never guessed); a change only between observations on a shared basis, else "not comparable" with why.
    In plain words: cells by number, outlines by name, the id last. `same_one` with `undo` ("those aren't the
    same") undoes a link as a new event; nothing is deleted.
  - **Comparing captures:** `query_population` with `against` compares two populations (counts, medians,
    coverage, only on a shared basis) and cell by cell only through links (cells counted once); `link` runs
    Trackastra only when asked and only on views it can relate (this run, one epoch, overlapping fields, the same
    objective and geometry, the slide's motion registered on both; the model is given their overlap,
    aligned, once their pictures are measured to line up within 10 px), keeping one-to-one links once (both ends of
    the rest said). Cells segmented for a researcher or a comparison (`made_for`) are followed, linked or compared
    only when their population is named or shown.
  - Kept per day under `identity/<day>/events.jsonl` (an asset) and read back; in the index as kind
    "individual", titled by what made it. A missing link means can't tell, never different.
- Facts, not verdicts (`docs/design/2026-10-06-facts-not-verdicts.md`): code gives the models
  what it measures and checks what they claim afterwards; the models make the judgments. A rule
  that decides instead of a model is a non-default option that has to beat "model alone".
- How a poor view is handled (`orchestrator/quality.py`, option QUALITY):
  - **Told, by default ("tell"):** Sight and naming a click get what the app measured on the
    picture, as numbers (`quality.measured`: sharpness against this slide's recent best,
    brightness, contrast, share at full white), and decide themselves whether they can tell.
  - **Flags:** every frame is also flagged blurry (against this slide's own recent sharpness),
    empty, dark, bright or glare, for the logs, frame choice and the gate option.
  - **Frame choice:** Sight and a spoken request look at the best frame of the moment, not just
    the newest (`Hub.best_frame`).
  - **Gate (an option):** code decides instead: Sight and naming a click say what is wrong ("Can't
    tell yet: the view is out of focus") instead of naming a bad view, and the coach writes advice.
- The app helps get a good view (`orchestrator/coach.py`):
  - **Focus meter:** while the focus knob turns, the studio shows a meter and "past the
    sharpest: turn back a little" (below 85% of the sweep's best). Only on a view with texture
    enough for sharpness to mean anything (`quality.MIN_PEAK`, as for the blurry flag): on a blank
    field sharpness is noise that halves and doubles from frame to frame (`coach.is_sweep`, shared
    with the focus stack's `sweep.py`).
  - **The phone, not the knob (option COACH, default "lens"):** the iPhone's autofocus hunting
    swings sharpness the same way, and Continuity Camera tells the Mac nothing about its lens (no
    focus mode, lens position or lock; `probe-camera-controls.sh`). The app measures the eyepiece
    disc's radius to a fraction of a pixel every frame (`fov.edge_radius`, `FrameInfo.disc`): the
    knob never changes it, the phone's lens does (focus breathing). A sweep that changed it by more
    than 0.3% is the phone's, and the meter says "the phone is refocusing: leave the knob" instead
    (2026-10-06 demo, 38 min: knob turns moved it at most 0.05%, hunts 1-2.4%; 76 of 192 sweeps).
    Every sweep is logged (`coach-sweep`: phone or knob, how much the disc and the brightness
    changed, whether it said "past the sharpest"). Brightness decides nothing yet: on the demo it
    moved opposite to sharpness (defocus brightens a brightfield view), not as an exposure gain would.
  - **Advice (only with the QUALITY gate):** a bad view that lasts 4 s gets one plain sentence on
    what to do (too dark, washed out, empty, out of focus), shown in the studio; so does the
    phone's hunting (let it settle; it hunts most on an empty or faint view).
  - **When it is spoken:** only while the voice is on, through the narrator's rules for
    unrequested lines. Never in quieter or "let me find things" mode, the same advice at
    most once a minute, and never in the chat.
- The voice sees through view notes (thinking.append, silent, at most every 3 s, plus chat
  notes batched into them): it answers "what are we looking at?" itself, at once, and never
  says "let me check". GPT-Live takes no images and runs no tools (OpenAI docs, Sept 2026).
  One mind (ADR 0016): every model that speaks to the user is told the same paragraph, `prompts.MIND`
  (the voice, the brain's answers and rewrites, researchers); the voice is the one who sees, its notes
  what it sees now, each with an age (a window listing is not pixels; earlier notes are not continuous
  sight). Its prompt follows OpenAI's live-prompting sections (role, backchannel, interruption, Backend
  tools / Delegate when / Do not delegate when, the waiting rule with "Do not guess the result while
  waiting") and holds no example of what to hand over (the "the dark dot?" example sent the 2026-10-11
  demo's "what's the little dot" to the app with a "One sec, I'll check that").
  Three loops, none waiting on another (ADR 0016; `docs/bench/voice-journeys.md` measures them on the real GPT-Live):
  - **See:** the user's first words of an utterance call `Sight.question_coming` (voice/route.py): the eyes look
    again when the slide moved, something new appeared, things are moving (movement.py's share over the last 4 s,
    measured off the loop only when nothing else calls for a look; what moved is a "Moving:" line while fresh) or the
    look is older than 20 s, at most 6 a minute, through `due()`; a still slide with a recent look costs nothing. The
    look it asked for (`question_view`) is sent at once while the user talks and the model does not; every note
    says how long before it its look was seen (a note that announced the slide moving was spoken aloud again and
    again: there is none). Other notes wait for a second of quiet as before.
  - **Talk:** the voice answers from its notes; it hands over to act, measure, open or look closer.
  - **Act:** option REFLEX, default "lean-3.5-flash" (since 2026-10-11, `bench/point_bench.py`: 26/28 at p50 0.75 s,
    the full pass 28/28 at 1.71 s; voice journeys: 3 of 7 view questions pointed at p50 2.7 s after the words, main
    1 of 7 at 4.7 s): at once and whether or not the voice hands over, `fast.LEAN_POINT` (the words, the eyes' things
    and the board only) names the listed ids the words are about and code highlights them ("lean" with the fast
    model, "lean-lite" on Flash-Lite). "after-voice", as before: the full fast pass told to only point, 1.5 s after
    the words and only without a hand-off. With a lean reflex, a question the voice answered alone that needed a
    look (the fast pass's `needs_look`) gets the look hands started finished (`SpokenRequests._look_closer`): its
    outlines are drawn, and the voice is given what it found beside what it said, to add or correct only if it
    differs (the model judges); the demo's looks were dropped unused (voice journeys run 5: "what's the little dot",
    no dot in the notes, the voice guessed). The voice is told it cannot point, outline or mark anything itself. Outlines that worked are told to the voice as a
    silent note and filed in the chat as the request's steps; pointing nobody asked for that failed is never told.
  - **Waits:** a narrator line re-reads whether it still holds when spoken (`Line.still`): a progress line whose
    step has settled, or a late outcome the user has since followed with a newer question (`SpokenRequests.newest`),
    is told to the voice, never said over them; a hand-off's outcome after a newer question is context ("stale").
    With Talk ended, a spoken request's answer is filed in the chat. A spoken request to act is the voice's own
    acknowledgement, its card in the chat, then the outcome said once (the user's decision, 2026-10-11: "Sure", the
    card, "There it is"): the answer written before its steps run is told to the voice, not said (a question's
    answer is said), and a wait is on its row and told, never said ("It's still being done" was noise).
- One spoken request, one record: each settled request is its hands Ticket (ticket.py) carrying
  what was heard (`speech.Heard`: its words, their span on GPT-Live's own clock, what was pointed
  at, the Studio pin) and its own work by role (hands, the look, the turn). Nothing is matched by
  comparing words. A GPT-Live delegation carries no task text, only `offset_ms`: it is matched to
  the newest request said before it on that clock; missing or inconsistent timing never selects
  an older request, words still being joined come first, and the brain gets that request's own
  words (live 2026-10-10: stale words joined to "magnify the eye" made the look plan a clear and a
  zoom on a target it never outlined). Other requests in progress keep their identity: context,
  never joined in. The fast pass judges a correction ("corrects": true, in its existing reply: "no,
  the other one"): the request before is called off where unfinished, keeps what it made, and its
  late result is not spoken as current.
- Hands: every finished spoken request goes through the fast pass as soon as it is heard;
  what can be done from notes (magnify, highlight, close, show a result, break down, start a
  researcher) is done at once, and the request's hand-off reuses that result: what hands did,
  the look it started, or its decision that the notes answer it, so the fast pass is asked once
  per request (2026-10-06: a second ask cost about 2 s in 40 of 47 hand-offs). A hand-off that
  arrives while hands is still deciding starts the look at once beside it, and hands uses that same
  look; unneeded, it is dropped (refunded once to the request). A typed question's fast pass decides
  first and its look starts only when needed (option EARLY_LOOK_TYPED "sequential", since ee959f12);
  "alongside" starts it beside the fast pass and refunds it when unused, and measured no faster: the fast
  pass gave steps for every typed request on the acting bench, so no early look was used
  (`docs/bench/acting.md`, Slice L).
  One owner per request: steps that need no look are done at once even when the rest does ("close
  the magnifier and highlight the stomata", stomata not in the notes: the fast pass gives
  close_magnifier with needs_look), and the look continues from their receipts; a look entered
  before them is replaced by one that knows them, so a correct fast plan is never dropped. Looks and
  outlines are always on the newest frame (a frame from when the user began to speak was 10-17 s
  old by the hand-off on 2026-10-06).
  If nothing was done and the voice did not hand over, the app points along with its answer
  to a question; a request the voice answered itself without handing it over is done by the
  app anyway, and said. Pieces of one request are joined first; words the fast pass judges
  `unfinished` ("Can you close the") wait for the rest and are joined with it, and a `cancel`
  ("never mind") calls off what was about to be done. All of this turn-taking is
  `brain/speech.py` (`SpokenRequests`, one per Talk press, its timings as named constants,
  tested with a stand-in voice in `tests/test_speech.py`); `voice/route.py` only connects the
  browser, GPT-Live and it.
- Act only when asked, as the model judges it: the fast pass says what the words are (request,
  question, statement, filler, unfinished, cancel) and code follows that and nothing else; only
  a request or a question keeps the actions the model gave, never a remark, a filler, unfinished
  words or a cancel. No keyword lists or word counts decide for the model (the user's direction,
  2026-10-06: "wait for it to settle, then zoom" is a request, which a word list called a
  cancel); every word goes to the model (`bench/fast_bench.py` has the cases, including those).
  Where code still overrides the model (the board picking "that" when the model named nothing,
  the look's clearing steps before its outlines, a rewritten or trimmed answer), it writes an
  `override` line in the interaction log. "Let me find things" (setting
  `userLeads`, or by voice: "let me find it myself", tool `set_lead`) stops all unasked
  pointing and naming (the fast pass and the main look alike), and quiets the narrator, for
  learning to observe: the model is told, and only the user's own request points. This is one
  module, `brain/intent.py`.
- How to explain is the user's (`orchestrator/learner.py`, CONTEXT.md "Level"): a preference,
  `level` simple (the default) or technical and `hintsFirst`, set in the studio's settings or by
  voice or chat (`set_level`, called when the model judges the user asked; no keyword rules), kept
  until changed (`day/prefs.py`). It reaches the models only as one line, the same
  everywhere (`learner.line`): the brain's scene table, the fast pass, the voice's view notes and
  recap, every researcher's system prompt (snapshot `learner`) and the lesson writer, so
  description cards too (one written for another level is redone). Nothing filters or rewrites an
  answer for it. The app's own ids are formatting, not level: at the simple level a parenthesis piece naming one of the app's ids now
  (the board, the look, the index: `Brain.is_app_id`) is taken out of the answer in code (`loop.without_app_ids`), and
  words that only look like ids ("(p53)") stay; the technical level keeps them. The arena compares wordings blind: an ask arm `model/pointing/level` is told that
  level's line, an arm without one no line.
- What every model is told is captured once per request as a `Moment` and rendered by
  `brain/context.py` (the brain's scene table, the fast pass's notes, the voice's view note
  and recap); the vision model is used only through `Agent.snapshot` / `look` / `point` (the
  brain and the arena alike); a request's tools run through `brain/actions.py`.
- The fast pass (`brain/fast.py`) runs beside every vision call; it acts only when its actions
  work (else the look answers). Seen ids become outlines first, reusing an outline that
  already covers the thing. Option BRAIN_FAST; benches: `bench/fast_bench.py`,
  `bench/sight_bench.py`, `bench/tool_choice.py`, `bench/brain_prompt.py`.
- How a look points is option POINTING: "box" (the default: the model boxes, the outline model
  draws each box), "marks" (numbered DoG spots), "snap-first" (an instant ring, then the box) and
  "masks" (segmenter-first Set-of-Mark, `orchestrator/masks.py`): after the slide settles, EdgeTAM
  masks the disc on the heavy GPU worker (`gpu_worker/automask.py`: a point grid plus the spots;
  ~1 s, clicks and tracking served between chunks, click p95 176 ms while it runs); the masks
  follow the slide like outlines and are dropped when it moved far or continuity was lost. The look
  is shown them numbered (`marks.render_masks`), boxes what it means and names a number; a number
  whose mask agrees with that box is the outline as it is (no second segmentation), otherwise the
  box is outlined (`override` logged); no masks ready: a box look (`masks-none` logged). Grounding
  bench 26/30 against box's 30/30 (the outlines draw the model to an outlined thing beside an
  unoutlined speck), so not the default (`docs/bench/finding.md`).
  "molmo" (`orchestrator/molmo.py`, `gpu_worker/molmo.py`): Molmo 2 4B (Ai2, Apache-2.0) on this Mac
  points at what the user's words are about (`query_for`: the request quoted after "Point to what
  this request is about"; asked the bare question it answers in words, asked for the model's label it
  points at every thing of that kind), and each point is outlined as a click is. The vision model
  still answers and decides whether anything is pointed at and what it is called (no targets: nothing
  is outlined); each point takes the label of the target box holding it, else the nearest
  (`molmo.assign`; `molmo-point` logs whether it was inside a box); no points, a failure or no Molmo:
  the model's boxes (`molmo-none`). Molmo runs in its own GPU worker process (`hub.gpu_molmo`,
  started and loaded on first use, ops `molmo_load`/`molmo_read`/`molmo_point`), so clicks, tracking
  and embeddings never wait behind it; its published code (transformers 4.57) runs on our 5.x
  through small shims and our own greedy loop (`gpu_worker/molmo.py`). Its image comes first in its
  prompt, so the settled view is read ahead (`MolmoPointer`, as "masks" masks it) and a question on
  it reads only its words; points move with the slide to the look's frame. Grounding bench 26/30
  (box 30/30), Molmo p50 3.6 s on a new view and 2.0 s on one read ahead (box 1.3 s), find bench
  worse on every class, ~20 s and ~12 GB of GPU memory to load; not the default, offered as a
  blind-comparison arm (`docs/bench/finding.md`, "Molmo 2").
- Nothing covers the specimen's object of interest: the magnifier floats over the dark
  surround beside the eyepiece (`apps/studio/src/magnifier.ts`), on the side away from its
  object and never over it (no room there: the corner farthest from it; on phones, top right),
  and the view never shrinks or moves for it; the description card floats too (dragged by its
  title). The card's **Look deeper** button asks "Look into this one more closely" with the card's outline
  (`objectId`), which the server makes the selection, so "this one" is that outline. The brain chooses what
  deeper means (a closer look, a measurement, following it, a quick researcher), so nobody picks a model
  first (the partner §2.7). Its words name no effort: a deep or standard run, a lesson or a video still
  needs the user to ask for one. Researchers' work sits in the side column (`apps/studio/src/side.ts`) and the eyepiece
  glides aside for it; on phones it is a bottom sheet. Both can be open at once. Panels are one
  stack (`apps/studio/src/panels.ts`): one Escape listener closes exactly the top open panel per
  press, what each panel takes from the view is reserved in one place, and where the magnifier
  floats is the pure `placeAway` (it never covers its object, the card or the side column, and
  labels keep out of its way; tested in `panels.test.ts`). Studio and server messages are
  typed both ways (`protocol.ts`: `ServerMessage`, `ClientCommand`), and `memorylink.ts` is an
  in-memory connection for tests.
- What moves is measured, not judged (`orchestrator/movement.py`, tool `track_motion`, family
  "motion"): small spots in each frame, placed by the slide's own offset (so moving the slide is
  not counted), linked into tracks; the model is given how many moved, their net and path speed
  (µm/s with a scale), straightness and any shared drift, and says what that suggests. It
  measures the last 4 s the hub holds at once and watches on for longer asks. Checked against dots
  of known speed (`tests/test_movement.py`); `bench/motion_bench.py` runs it on a recording.
  - **Kept as evidence** (`orchestrator/motiontracks.py`, M3.2): it runs the "motion" operation (one
    implementation, an algorithm) through the runner, so it has an Execution, and what it measured is a
    tracks item (`m1`, index kind "tracks", "Motion · 18:04, 4 s"): every track in slide coordinates with
    its times and frames (`(boot, seq)`), the epoch, the span, the summary, the scale and where it came
    from, the producer with its version and configuration, and the last frame with the tracks drawn (its
    captured view). Held in the unsaved allowance, saved beside the populations (manifest last), journaled
    and read back. Two requests over the same frames share one run, and the second says it reused it.
  - **Shown live only where verified** (contract 3): the tracks layer draws an item while its own view's
    pixels around the tracks match where the slide's move puts them (NCC 0.8, same epoch and geometry),
    older parts fainter, breaks never joined, the interval and units said; otherwise it is shown on its
    captured view in the magnifier, labelled as not the live view. `present` shows, hides, fades, colours
    (by speed, the default) and narrows it ("keep the fastest"); done once the screen report shows it.
  - **A followed thing's trail** (`track_motion` `of`: "where did it go?") is kept the same way, its
    segments' breaks (gap, lost, corrected, another epoch) as the follower made them.
- Time (tools `what_happened`, `watch`, family "time"): `orchestrator/clip.py` keeps a frame every
  0.5 s for the last minute (references to the camera's JPEGs, nothing decoded); `what_happened`
  shows up to 8 of them, oldest first, to the vision model with what the slide did between them
  (so the slide moving is not taken for things moving), and the answer is what the model saw.
  `orchestrator/watcher.py` watches for something to happen and says it at once when it does
  (narrator, on the delegation that asked; in the chat otherwise): by motion (the moving share
  from `movement.py`, below or above a value the model sets, two checks in a row), by something
  new appearing in a still view (`quality.StillChange`), or by a yes/no question the vision
  model answers every 15 s. At most 3 at once, 15 minutes at most; running out of time is said.
- Outcomes (`orchestrator/outcomes.py`, tool `outcomes`, K3): how each way of doing something has gone on this
  specimen, from the executions and only unambiguous signals (the user's corrections of their members, blind
  votes of the outline trials that compared them, won or lost only for a letter winner, ties and "none good" apart,
  per policy; nothing found, the model's own failures, time end to end); past tense with n, "too few runs to say"
  below 3, never across subjects (asked with no specimen named, each line names its own; runs made while none was
  named are said apart). Only runs where the model ran count: one cancelled, replaced, turned away or expired in the
  queue, never sent to a worker marked hung, given an input that could not be read, refused or unable to run here is
  said apart, "not counted" (the runner records why: `notRun`); a request nothing could run for is its own line;
  background re-measuring is its own line. At most 12 lines, what is left out said. Read cheaply: the log only where
  it grew, each manifest and the votes once per change, today's corrections from memory first; the index reads them
  only when outcomes are asked for. Pulled by the tool; pushing them (OUTCOME_LINES, made in a thread before the
  brain's capture) or a capped section of referenced index entries (SESSION_CONTEXT) into the brain's own prompt are
  options, off until a bench shows they help, and neither is a role a capability needs.
- Replay and fine-tuning (K4; ADR 0013), all explicit jobs outside the app:
  - **Session bundle** (`make bundle OUT= DATASET=|FROM= TO=|DAY=`, `bench/bundle.py`; docs/datasets.md "Session
    bundles"): a dataset or a window with its evidence, executions, outcomes, corrections, journal and the pictures
    its records name, in a new folder you name: its own copies (only a dataset's camera video is hard-linked), never
    under the app's saves or over anything, a Record dataset only once saved; it says plainly what it lacks (no
    dataset, a reconstructed one, no video, no corrections, views not kept).
  - **Replay bench** (`make replay-bench BUNDLE= IMPL=`, `bench/replay_bench.py`, docs/bench/replay.md): a cell finder
    run again on each population's own view and prompt through `population.segment` and the runner (origin
    "replay", its own GPU worker process; nothing kept, nothing logged as the app's, never an outcome). The user's
    corrections (of that day's population) are the truth where there are any; elsewhere it is agreement (IoU >= 0.5,
    one to one), never accuracy; per subject; a finder run on what it made itself is said apart, never as a
    candidate. Winning governs only its recommendation line (on days it did not train on: at least 20 corrections,
    half agreed, 90% of the uncorrected cells still found); it never limits a choice, and a person changes a default.
  - **Fine-tune** (`make finetune-cells`, `bench/finetune_cells.py`): Cellpose-SAM trained by cellpose's own trainer
    on the corrections export (corrected populations as they stand now), the newest days held out (a session is a
    day; a split is refused when a held-out view is on the training side); each run a new version under
    `models/cellpose-sam-tuned/v<n>/` (`orchestrator/finetuned.py`, an asset, never written over) with what it
    trained on and held out. It is CELLS `cellpose-sam-tuned` (licence tier non-commercial, inherited), available as
    soon as its weights exist, each version its own line in the outcomes, recommended only if it wins on its
    held-out days. Nothing trains from clicks.
  - **Live first:** the replay bench and the fine-tune each put another Cellpose-SAM (1.2 GB) on the GPU, so both
    refuse while the app runs (`bench/live.py`) unless given `--beside-live` (BESIDE_LIVE=1), which their results
    say.
- Memory of the view (tool `seen_before`, family "memory"): about an outlined object (option SEEN
  on), the outlines named earlier today that look most like it (from 0.85; BioCLIP 2: 0.90), with when, in which
  conversation, their names and who named them, past tense, no model call (`seen.py`). About the
  whole view, or when none is alike, the vision model is shown the view now (or the object) beside
  a numbered sheet of today's earlier views (the running look's saved pictures, one per stretch of
  the same specimen, at most 20 spread over the day) and says which show the same kind of thing;
  the times and what was seen then come from the log.
- The menu (`brain/menu.py`, docs/design/2026-10-06-capability-menu.md): every tool declares
  its family; the model is always shown every tool, and option MENU only sets the layout (flat,
  by family, compact, or brief: the longest seven said shortly; flat by `bench/tool_choice.py`, 110/118
  against brief's 104/118). Outline colour and style are asked for
  (`set_style`); the voice is told the tools by family, so "what can you do?" is answered in
  families). `contrast` shows two objects side by side and the vision model says how they differ
  (`compare` only shows them).
- Named comparisons (`compare_models`, M2): the models the user names outline the same thing (or segment
  every cell of the same view), raw unless the full route is asked for; the app draws what each made side
  by side on that view's pixels in the magnifier and measures it (areas, IoU; counts and cells both
  found), and says when a result was reused (when it was made; its time not counted). Not the blind test.
  Its view is held (the unsaved allowance) for 5 minutes, until the next comparison or a keep (an outline comparison
  says so; a cells comparison says each result is kept in Data, as its populations are): `keep=<id>`
  ("keep the SAM 2.1 one") puts that model's outline on the board, made by that arm's execution, in place of
  the app's earlier outline of that thing (never the user's), or shows that arm's cells; cells are framed on
  the whole view when it has no eyepiece circle. A cells comparison the user runs by hand from the workspace's
  Tools is drawn by the workspace instead (each model's retained masks on its Stage, in that model's colour,
  each hideable: `apps/studio/AGENTS.md` "Tools"), so it gets no side-by-side picture of its own.
- Choosing a model (`use` for this request, `set_default` from now on, `compare_models`;
  `brain/toolbox/choosing.py`): a name means what `orchestrator/names.py` says, an exact key only (the id, the
  name, the name with where it runs said any way: "SAM 3.1 (local)", an old label for one release, a spoken
  form), never a part of a name. A family named alone ("SAM 3.1", "cellpose", "dino") is its default variant (FAMILIES:
  SAM 3.1 (cloud), DINOv2 small) and the receipt says so; the user is asked only when that one can't run here and another of
  it can. The tools take fixed lists of ids (`enum`), told once as "id = name" on `use`; a name that means
  nothing is refused with those choices and `next` "pick one of these and try again", so the model corrects
  itself in the request's recovery round, and a later `use` that works replaces the refusal (live 2026-10-11:
  the brain shortened a name and its own `use` refused it). `use` takes `by` (user or brain), required, so a
  choice the brain made is never recorded as the user's. `find`'s `by` and `revise_work`'s `model` are fixed
  lists too.
  - **Every model role** (Slice 5, the user's Q14-Q17 of 2026-10-11): `use` and `set_default` take `operation`,
    required: an operation with a choice (outline, cells, embed...) or a setting (`names.SETTINGS`: vision, fast,
    outline_check, research, image, presentation for one request or from now on; sight and zeroshot from now on
    only). A setting chosen for a request is what that request's settings reads see (`options.overriding`); a
    vision or fast choice made after the request's look or quick decision ran says that one used the default.
    An operation done one way (following, motion) is refused naming its one way, and one with no model to choose
    (dissecting) as such; both say where the model named is a choice ("DINO is a choice for looks":
    `choosing.choice_for`; live 2026-10-11 16:28:55, "use the dino model to dissect").
  - **A choice per step:** a `use` holds for the steps after it until the next `use` of that operation (only the
    first of each moves before the request's outlines: `actions.first_choices`), so "outline it with SAM 2.1, then
    with EdgeTAM" draws it with each; the turn log keeps every choice with its step (`used.steps`). Highlighting an
    outline a chosen model did not draw draws it again with the choice (`view.redraw_with_choice`,
    `ticket.reusable`), replacing the app's earlier one unless `keep`; so is a thing the look saw (s1) that an
    earlier step outlined with another model (`actions.outline_seen`). A choice alone changes nothing on screen
    (`use`'s description: the step follows; to draw an outlined thing again, highlight its outline, never find inside
    it), and a `use` that is its plan's last step says that nothing has been done with it yet
    (`choosing.nothing_after`; journeys round 2, live "use SAM 2.1 for this" 0 of 3 as `use` alone).
  - **From now on is kept until changed** (`orchestrator/defaults.py`, the preference `defaults`): the Models menu
    and `set_default` change settings one way (`commands.change_settings`); a restart applies the kept choices,
    each checked as runnable (`options.unavailable`), and says them in the conversation ("Outlining still uses
    SAM 2.1 tiny (on this Mac), as you asked at 02:41", "on Oct 11 at 02:41" for an earlier day's, or that one
    can't run now and what is used); never a line of the day's digest. One that couldn't run is the user's choice
    again the moment it can (a download, an install, a key: `App.resume_defaults` on every catalog change), said
    ("Look-alikes uses DINOv3 small (on this Mac) again: it can run now, as you asked at 22:46"). `defaults.RESTORED` is the hook for preloading a restored model.
  - **Researchers name a cell finder:** `run` "segment" takes `implementation` (`research_worker/budget.CELL_FINDERS`,
    resolved by `names.resolve("cells")`); "motion" is done one way (spots), said.
- "Close that" is the window in front (`screen.frontmost`: the highest open, shown window that takes close), one
  rule whichever tool the model picked: `present` close or hide with no window named, and `close_result` when its own
  window is not open (`screen.close_frontmost`, an `override` logged). "The magnifier" is its own kind:
  `close_magnifier` closes its panel, else the live magnification window in front (a native window on a live capture
  of an outline, zoom's "… · live magnification": `screen.live_magnifications`), never a window of another kind; with
  none open it says so and closes nothing (live 2026-10-11 16:36:38: the window in front, a reference photo, was
  closed and the answer said the live magnification was). Closing a window an earlier step of the same request closed
  is done, not failed (`present`'s native close; 16:17:34).
- Zoom does what is asked: the magnifier's level (`orchestrator/magnifier.py`, x the fitted
  view) goes "more", "less", "fit", "max" or any number, by voice, chat, wheel, pinch or + and -;
  it never refuses, and past the camera's resolution it says so and shows the pixels
  ("pixel-enlarged") rather than inventing detail. The server holds the level so the brain
  sees it, and is the only one to work out how close it goes and when it is pixel-enlarged
  (`maxLevel`, `pixelated` on its wire); the studio shows a wheel or pinch level at once and
  keeps it until the server echoes it. The result viewer's state is the server's
  (`orchestrator/presenter.py`), as are its tabs (order and labels: `items` on each
  researcher's wire), so voice and chat can show and close results; what the user asked for
  opens itself when ready. The shelf (`apps/studio/src/shelf.ts`, CONTEXT.md "Shelf") is the researchers' pills on
  the Working strip, the fixed 40 px row under the Stage (`apps/studio/src/working-strip.ts`; it never takes the
  Stage's room, so the eyepiece never rescales as pills come and go): every researcher working, needing you, or
  with finished work not yet opened, each its lit cell, its name, and its elapsed time or its state in words.
  Clicking one opens the researcher's Tab, and opening its page anywhere counts as opened, so a finished pill
  leaves once read; its × takes it off the strip for the session (kept in its journal as a preference the models
  are never told), and the researcher stays in Researchers. Every researcher of the session stays in the side
  bar's Researchers (`researcher-cards.ts`), whose count is its cards: research only, never the descriptions'
  quick lookups. One still working in another session is listed apart ("Still working in other sessions").
  Will's decision of 2026-10-11 put the shelf back beside the view (the redesign handoff had none there), and his
  decision of 2026-10-24 gave it the strip: the Tab is the open view, the pill the work happening now.
- A clearer picture is made only from real frames (`enhance`, `orchestrator/enhance.py`): "average"
  (the default) averages the still frames of the last second or so, aligned by the slide offset,
  one epoch only; "sharpest" is the sharpest recent frame, unaltered; "stack" focus-stacks the frames
  of the last turn of the focus knob (`orchestrator/sweep.py` keeps up to 60 of them, by reference,
  for 2 minutes), each place from the frame where it was sharpest; a frame taken while the phone's
  own lens refocused is first scaled back to the newest frame's eyepiece disc (centre to centre, at
  the ratio of the radii). The model picks which. It shows in
  the magnifier labelled with how it was made ("Averaged: 24 real frames"), and when it cannot (the
  slide moving, no sweep yet) it says what to do. `snapshot` is a picture of what the user was looking
  at when they asked, with the outlines, labels and scale bar drawn by the app as the studio draws them
  (`orchestrator/snapshot.py`): the window of theirs it came from (from the request's own pin: a live
  close-up's region as its zoom and pan showed it, on the frame held for the request, never a newer one;
  "no longer kept" when that frame is gone), or the object named, else the best recent frame of the view.
  It is saved as PNG and JSON under `snapshots/<day>/` (`paths.snapshots`, git-ignored, kept) and kept as a
  result by its own id, opened beside that window (the legacy single magnifier without native windows);
  "I don't see it" opens that id again rather than taking another. Its picture is one of the session's sources (it
  joins on its own, ADR 0022), listed in the side bar's Assets, not in Made; the index keeps the snapshot entry, which opens as
  its own PNG as a saved image (labelled with overlays, capture without; `presentation_delivery._select`).
- Close-ups (option CLOSEUP, `orchestrator/agent.py`): naming a click sends the view with the
  thing outlined and one close-up (3x its size, a small one enlarged to 384 px). "naming": the model
  may instead ask to see part of the newest close-up closer; the app cuts it from the full-resolution
  frame, enlarges it and asks again, at most 3 calls (`name_closer`; "naming+answers" lets answers do
  the same, `answer_closer`). Every round is logged (`rounds`, `closer`, the pictures). Default
  "single": on the naming bench Gemini 3.5 Flash never asked for a closer look in 720 namings, things
  down to about 20 px included (`docs/bench/naming.md`, W4-3).
- Dust on the optics (option DUST, `orchestrator/dust.py`): while the slide moves, views at least a
  fifth of the eyepiece apart are kept by reference (at most 45, 10 minutes), all aligned with one
  reference disc (the median of their discs: the disc fitter jitters by a few per cent); their median is
  the flat field, and dark spots of its fixed residue that at least 3 in 4 views show are dust (found:
  28 of 32 synthetic spots 12-25% deep, none falsely, none on the real June 14 footage). It is measured
  in a thread every 3 s (40-75 ms), never on the frame path (about 15 µs a frame). "tell" (default):
  naming a click, Sight and answers are told where the spots are as boxes on image 1 and, naming a
  click, that the outline is at one; the model decides (clicks on dust named as part of the specimen,
  "Howell-Jolly body" and the like: 29 of 48 told nothing, 0 of 48 told). "filter" (code also drops
  the app's outlines and Sight's things that lie on a spot, logged as an override) did not beat "tell"
  and stays an option (`bench/dust_bench.py`, `docs/bench/dust.md`).
- A drag of more than 8 px on the live view draws a dashed box and outlines what is inside it
  (one `click` with `box` in frame px, `apps/studio/src/boxdrag.ts`; Escape cancels), and every
  click, a failed one too, is logged as `kind: "click"` in the interaction log.
- A click on an outline selects it and opens its details; a click on it once selected removes it (after the
  double-click window, which magnifies it), with "Removed · Undo" for 6 s. The scene holds a removed outline for
  `scene.UNDO_S` (30 s), and the studio command `restore` brings it back as it was (its name, how it was made, its
  parts). Its details close with their × or Escape. A dissected part is drawn as a part and never caught by a click; a
  click inside the object is the object's. A tap or drag to outline while the app's outlines are hidden (`present`
  hide, "remove the overlay") shows them again as the user's change and says so (live 2026-10-12: taps made outlines
  nobody saw), and a tap never selects an outline it cannot see.
- What the studio shows about a state goes with it (the user's notes of 2026-10-11):
  - **Hints:** the selected cell's measurements and "Talking with ..." are hints owned by their state, cleared on
    deselect and when Talk ends, and every hint has its own ×.
  - **The cells' control:** its × hides the cells and closes it until they are shown again, and it closes itself once
    its population is gone.
  - **Made in this session** (the side bar's Made): a click or Enter on a row opens the item over the live workspace.
  - **New windows** keep clear of the stage's corners (the footage player, pills) where there is room.
- A reference photo's web search (`reference.retrieve`) gives each search its own cap (`SEARCH_CAP_S`, 15 s). It
  sends the same search again beside one not back by `HEDGE_AFTER_S` (8 s, its p75 over 42 live lookups), and the
  first usable photo wins. The recovery search still runs with what was rejected. Live 2026-10-11, by voice: one hung
  search took the whole of a single 20 s deadline and nothing was shown. The search and its check of the photo's
  pixels are told what the user was looking at when they asked, as data (`brain/toolbox/magnifier.on_screen_words`: the running
  look and its specimen, the subject named, the names of the outlines pointed at, selected, magnified or shown in a
  window); the search model reads the words in it (live 2026-10-11: "an iris" after zooming on the user's eye found
  the plant).
  - **Where it searches** (option REFERENCE_SEARCH): "web+commons" (the default) searches Wikimedia Commons'
    catalogue beside the web from the start (`reference.catalogue`, its pages outside the web's page budget), and
    the first photo the pixel check accepts wins; the other search is called off. "web" is the web alone. On 20
    subjects asked live (`bench/reference_bench.py`, `docs/bench/reference.md`): the web 20/20 found, 18/20 right by
    a judge of another family, 10.7 s p50; Commons 11/20, all right, 5.0 s; the two raced 20/20, 20/20, 6.2 s.
- The chat is the conversation (typed and spoken), not an activity log: a click names the thing
  on its outline and card (`SceneObject.seen`), a tool that finishes late updates its answer's
  line in place (`chat-action`), a voice hand-off shows on the Talk dot, and researchers the
  brain started on its own wait on the shelf. Every chat line goes through
  `orchestrator/dialogue.py`, which files it with its conversation (below) and journals it; a
  browser that reloads, and a restart, replay the journal's lines (ADR 0001). The studio's
  chat is one module (`apps/studio/src/chatlog.ts`, a pure `ChatModel` underneath). All of the
  session's researchers are back in the Researchers tab after a restart, with the numbers they had.
  When something finishes on its own time (a researcher, a late tool, a correction), who hears
  it and where is `orchestrator/announcer.py`. The model's answers render a safe markdown subset (lists, bold,
  code, tables: `apps/studio/src/markdown.ts`, DOM nodes only). A step that did not work is never a card of its
  own: its answer's ⓘ turns to a "!" that opens Activity (a step tried again successfully is not shown as failed),
  and a late failure with no answer line goes to Activity, never a toast of its raw summary (2026-10-11).
- Activity is what happened, newest first (CONTEXT.md "Activity"; the user's decision Q2, 2026-10-11), every line
  from a recorded fact (`orchestrator/activity.py`; drawn by `apps/studio/src/activity.ts` and `chatlog.ts`):
  - **One entry per request:** the brain files its words, typed or spoken, and when, as its turn starts (running),
    then its steps, the answering model and its look's own outlines when it ends (`request_started`,
    `request_finished`; a turn that raises is marked failed in plain words). A request that takes the user to another
    conversation ("back to the blood smear: …") has its entry, with its words, there too, where its answer is filed.
    Each step is one line: what was done,
    its models briefly ("Gemini 3.8 Flash → EdgeTAM"), how long, its time and a dot (done, running, failed); it opens
    to plain details (each model with its role, a fallback with what could not run, "tried again: it worked", the
    failure's next step) and the request and step ids sit behind a further "Technical". A late step updates its own
    occurrence. The answer's ⓘ (or "!") opens its entry, with what did not work opened; its hover names the
    request's models on one line ("Gemini 3.8 Flash · drawn by EdgeTAM · 4.8 s"); the "What powers it" ⓘ stays on
    the request's heading for the execution's full detail.
  - **What ran, per step:** `ToolRun.models` (on its wire, never in what a model is told: `registry.ACTIVITY_KEYS`)
    is named when the run ends (`activity.models_of`): the implementation each execution really ran (an algorithm
    only when it ran in a model's place), what drew the outlines it put on the board (a kept comparison: its arm's
    model; a redraw under `use`: the model chosen for that step), and the vision model in effect for that request
    from the settings it read; a step still running says what it waits on (`ToolRun.progress`: a model loading);
    names from names.py. A run with an execution of an unvoted blind trial names none (`Execution.revealed`, set by
    the vote), nor does a click during one.
  - **Everything else is one line:** the studio's commands (`commands.USER_ACTIONS`, in plain words; a pointer
    resting on an outline and the magnifier's own reports are not the user's act; a wheel's zoom levels are one
    line), clicks and drags with what outlined them and how long (and, once named, the name and the model that gave
    it), setting changes by the user ("You chose … (Models menu)") or by the app for a request ("The app set … (you
    asked: …)"), researchers' runs nothing in the panel started, such as a mission's slot (running until they stop;
    a run a request started is that request's step, said once), their plans, questions and finishes (the
    announcer's notices, said in plain words), and model loads and unloads.
  - **Filing:** each is a line of the conversation it happened in (`Dialogue.show`: the day's journal, the
    conversation log, every studio), replayed after a reload or a restart; a later line with the same id replaces it
    in place (running, then done), and an update is filed where its line began. The tab's count is the entries shown.
  - **For code that loads models** (the meter's "our own load"): `activity.model_loading(hub, "micro-sam",
    expected_s=120)` returns a key; `activity.model_loaded(hub, key, seconds=…)` (or `ok=False, why=…`) and
    `activity.model_unloaded(hub, "micro-sam", why="to free memory for …")`; safe from any thread, a no-op on a hub
    without Activity. Anything else that is not a request: `activity.activity(hub, kind, text, state=…, details=…)`.

## The one-brain loop (option VOICE `gpt-realtime-2.1`)

One model hears the user, sees fresh pixels, calls the app's tools with the user's own words, reads their results and
speaks, all in one context (PARTNER-CONTRACT of 2026-10-29; `voice/openai_realtime.py`, `voice/partner.py`,
`voice/partner_tools.py`). It is an option beside GPT-Live, which stays the default until the journey bench shows it
better on the packaged build. Where GPT-Live hands the brain an id and the brain guesses the words (`brain/speech.py`),
this voice has no hops to fall between.

- **Transport:** WebRTC only. The route posts the browser's SDP offer and the session config to `POST
  /v1/realtime/calls` (the SDK's `client.realtime.calls.with_raw_response.create`: the SDP answer is the body, the call
  id the last part of the `Location` header), keeps the key, and attaches the sideband
  (`client.realtime.connect(call_id=…)`), sending and reading raw JSON. Session config: `turn_detection: null` for
  `TALK hold` (default), `semantic_vad` with `interrupt_response: true` and `create_response: false` for `TALK open`
  (headphones), `noise_reduction: far_field`, input transcription by GPT-4o mini Transcribe (a fixed role: the chat's
  line and the tools' checks of what was asked), `reasoning.effort: low`, `truncation.retention_ratio: 0.8`.
- **A turn (hold to talk):** key down → the camera's eyepiece crop (`scene_tools.view_frame`, with its frame id and
  crop) is grabbed off the loop, the screen as text (`context._voice_windows` of the accepted studio's report) is read
  and the brief refreshed, all as the message is read; the studio sends its window picture (`snapshot`, id `talk-<n>`)
  right behind it. A key down while it speaks is a barge-in: `response.cancel`, `output_audio_buffer.clear`, then
  `input_audio_buffer.clear` (the studio mutes its audio element at once and clears the marks). Release → last turn's
  picture item deleted, this turn's text note (kept) and picture item (camera then window) added, then after
  `RELEASE_TAIL_S` (0.15 s, the WebRTC audio still in flight) `input_audio_buffer.commit` and `response.create` at once:
  nothing is fetched after release. A tap shorter than 0.25 s and Escape (`cancel`) ask for nothing.
- **One response at a time** (the review of 2026-10-29): every `response.create` (a turn, a typed line, a tool round,
  a look, a progress line, a line nobody asked for, a reconnect's line) is queued at one gate
  (`RealtimeSession._gate`), sent only when the response in progress has ended, and checked again just before it is
  sent, with nothing awaited between that check and claiming the slot: dropped when the user has pressed talk or
  started a newer turn since, or the call it was asked on was replaced. The user's own turns go first and are never
  dropped for waiting; a response whose end never comes is cancelled and let go after 3 s. Provider `error` events are
  logged (`partner-provider-error`, or `partner-provider-note` for a best-effort step's refusal), never a session error:
  the studio ends Talk on one, and every error the API sends here is about one request of ours (a refused create is
  asked again once the running response ends).
- **The Project brief** (`project_state.brief`, at most 3000 characters, no times in it) is one system item, deleted and
  made again only when its text changed. The realtime API's items take `system`, `user` and `assistant` roles, not the
  contract's "developer": the app's context is `system`.
- **Tools** (`partner_tools.PARTNER_TOOLS`, those the registry has): present, tabs, show_measurements, highlight,
  find, outline_cells, select, zoom, clear, start_researcher, research_status, researcher_note, cancel_researcher,
  show_me, resources, models_in_use, capabilities, workflow, new_project, retrieve_page (web search), run_capability
  (once a capability is on; the session's tools follow the registry at each key-down) and reference_photo, plus
  `mark`, `look_hard` (the frame, and the window if asked, to `Settings.vision`) and `think` (below; it replaced
  `do_in_hooke`, which is kept but no longer offered). Each call is one voice request (Ticket "turn", channel voice, its admission and
  request id, the accepted studio's pin, `voice_owner` this session so researchers report back to it) run through the
  brain's `ActionRunner`; the user's words of that turn (their transcription, waited for at most 1.5 s) are its
  `question`. Each call's row goes into the chat (`chat-action`). start_researcher, new_project and cancel_researcher
  run once per turn: the turn's slot is taken before anything awaits, so a second call in the same turn, in parallel or
  later and whatever its words, gets the first's result (live 2026-10-28: two researchers from one request).
  `parallel_tool_calls` is on; tools that change the screen run one after another, each with its own look after it.
  Only a completed call whose arguments parse runs (a cancelled response's cut-off call is dropped and logged). A call
  still running after 12 s (look_hard and do_in_hooke too) is answered `still_working`; its outcome is one line when it
  lands. do_in_hooke reports what the brain really did (its failed steps as `failed`) and looks after a screen change.
- **Act, then look:** after a call that changes the screen, the studio is asked for a report newer than the change
  (`Screen.until`, 2 s), then for a window picture (`snapshot-request`, 1.5 s); both go into the conversation after the
  call's output, and only then is the model asked to go on (`cause: "look"`), so it says done only when the picture
  shows it.
- **Lines nobody asked for:** a researcher finished or failed (`ResearchManager.on_done`; one asked for by voice is said
  through the announcer's narrator line, tagged with its card), a long tool done, a capability turned on
  (`App.turned_on` → `brain.voice.happened`), a reconnect while it spoke. Each is a system item and one
  `response.create` with `tool_choice: none` and "one short sentence", only when no key is held, nothing is being
  said and nobody spoke for 1.5 s; once per event in two minutes, by its kind and reference or, without one, its whole
  text's digest (never a prefix: the narrator's lines share a long preamble); a mark on the researcher's card is drawn
  as it is said, and a narrator line is the researcher's only when its own facts carry its `researchId`. A researcher's
  own words (its one-liner) are inside the untrusted wrapper. Waiting more than a minute, it stays in the conversation
  unsaid.
- **One conversation:** the user's words (`conversation.item.input_audio_transcription.completed`) and the voice's
  (`response.output_audio_transcript.delta`, one line per response) are filed with `SpokenRequests`' caption lines, as
  GPT-Live's are; they never reach its hands. Typed chat while talking (the brain's voice notes, which GPT-Live reads
  with its view notes) is added as context as it happens, so you can type, talk, then type. A new call is seeded with
  the last 8 exchanges, the brief and the per-turn notes.
- **Reconnect:** a sideband that drops is reattached to the same call, and nothing is seeded again (the call's
  conversation still holds the brief and the pictures, by their ids); a call that ended (or a reattached sideband
  dropping again within 10 s) asks the studio for a new offer (`reoffer` → `offer` with the same id), makes a new
  call and sends its answer (`data: {sdp, id}`); at 55 minutes, when nobody has spoken for 5 s, it renews itself the
  same way. When the studio's own peer connection fails it sends an offer of its own (`id: "rc-<n>"`, every 3.5 s
  for 10 s): a new call for it, answered with that id, only the newest. A new call is made first and the old one
  closed in the background; outputs of the old call's tool calls are dropped (it never had those ids); a release while
  reconnecting waits for it, or, when its audio went to a call since replaced, is answered with one line asking to say
  it again. The same session object goes on (researchers' delivery and the narrator still reach it).
- **Speaking:** the sideband's `output_audio_buffer.started` / `stopped` / `cleared` (checked over a real WebRTC call,
  below), and the studio's own `{type:"voice-phase", phase:"speaking"|"listening"}`, set whether the voice is audible.
- **Usage:** each `response.done`'s usage (text, audio and image tokens in; text and audio out) is recorded at list
  price (`budget.PRICES["gpt-realtime-2.1"]`; cached input at the full price, so it can only over-count); its
  open time writes no charge, and its minutes ahead under a monthly cap are held at an estimated $0.30 a minute.
- **What the bench reads** (`interactions.jsonl`): partner-talk, partner-turn, partner-response (and partner-audio
  for when the browser stopped playing it), partner-barge, partner-tool, partner-marks, partner-proactive,
  partner-reconnect. `MICROMIND_BENCH_FAULTS=1` (never in a packaged build) lets the bench close the provider
  (`{type:"bench", fault:"close-provider"}`) or fail the next tool.

Measured on the real API, 2026-10-29 (one paid smoke run over the WebSocket, no browser; WebRTC adds the browser's
network leg and was not measured here): a held turn with a picture and synthesized speech, response.create to the first
audio delta **842 ms**; a response that spoke a preamble then called the tool, **1051 ms**; the answer after the tool's
`function_call_output`, **758 ms**. The input transcription completed before the first audio. Two responses with a
768 px picture cost $0.013 each at list price; a picture at `detail: low` was 65 tokens, at `auto` 194. Events, in
order, for the tool round: `session.created`, `session.updated`, `conversation.item.added`/`.done`,
`response.created`, `response.output_item.added`, `response.content_part.added`,
`response.output_audio_transcript.delta`, `response.output_audio.delta`, `response.function_call_arguments.delta`,
`response.output_audio.done`, `response.output_audio_transcript.done`, `response.output_item.done`,
`response.function_call_arguments.done`, `response.done` (its `output` holds the `function_call` with `call_id`, `name`
and `arguments`), `rate_limits.updated`. A held turn adds `input_audio_buffer.cleared`, `conversation.item.deleted`,
`input_audio_buffer.committed`, `conversation.item.input_audio_transcription.delta`/`.completed`. Client-chosen item
ids (`hk_…`, at most 32 characters) and `response.metadata` (`cause`, `n`) came back as sent. Over WebRTC,
`response.output_audio.done` comes while the browser still plays the answer: only `output_audio_buffer.started` /
`stopped` / `cleared` say when it is audible. A second run confirmed the rest the partner relies on: a mid-session `session.update` of
`tools` takes effect (its `session.updated` lists them); a refused step's `error` echoes our `event_id`
(`item_delete_invalid_item_id`, `response_cancel_not_active`), which is how a best-effort step's refusal is logged and
not said; and a proactive `response.create` with `instructions`, `tool_choice: "none"` and `metadata` spoke one
sentence from a system item of facts ("Researcher 3 finished and reported finding two size groups."), its first audio
**692 ms** after asking. Both runs cost $0.04 at list price. A third run (a session.update only) took all 78 registry
tools of the golden fixture as the partner reshapes them, with mark, look_hard and do_in_hooke: 81 of 81 accepted. A
fourth made the WebRTC call itself as `RealtimeSession` does, from a headless Chrome's SDP offer: the call was created
in **1511 ms** (the body is the SDP answer, the `Location` header ends in the `rtc_…` id), the sideband attached by
`call_id` in **263 ms**, and a `session.update` over it was answered `session.updated` with the 19 partner tools the
fixture has (the sideband's first event; no `session.created` comes on it). So a reconnect that needs a new call is
about 1.8 s plus the browser's new offer: at the edge of the 2 s target. A fifth run (the review's item 10) connected a
headless Chrome peer to a real call and asked for one spoken line: **`output_audio_buffer.started` and `.stopped` do
reach the sideband**, with no `response.output_audio.delta` on it. From `response.create`: `response.created` 88 ms,
the first transcript 308 ms, `output_audio_buffer.started` **448 ms**, `response.output_audio.done` and
`response.done` 1466 ms, `output_audio_buffer.stopped` 6300 ms (the browser received 44,647 bytes of audio): the answer
plays for seconds after `response.done`, which is why only the buffer's events (and the studio's voice-phase) say when
it is audible. Not measured without the studio: whether `RELEASE_TAIL_S` keeps the last word.

### Tuning against the journey bench (2026-10-29)

Measured over real WebRTC calls (a headless Chrome peer) and the part-2.5 journey, then kept:
- **Reasoning effort `minimal`:** response.create to the first server audio 0.82 s median vs 1.10 s at `low`, with the
  partner's prompt, tools, brief and pictures; with spoken input, a tool round's preamble 0.74 s vs 3.10 s.
- **Perception sent while the key is held:** the note at once, the camera and the window as each is ready
  (`RealtimeSession.early`); release sends only commit and response.create. The API prepares items as they arrive:
  0.74 s vs 1.33 s with the same pictures sent at release. A tap or Escape deletes them again.
- **Pictures at full detail** (`auto`): at `low` the model took a new layer of outlines for "hidden behind the tabs
  panel". Sent during the hold, their cost is small.
- **Speak first, briefly:** every reply starts with a few words before any tool (a tool called in silence cost 2-3 s
  to the first audio); one or two sentences by default (the judge's one-or-two-sentence line went from 0.5 to 1.0).
- **Pointing without a round trip:** a mark before speaking cost a silent response (3.5 s of silence). The app points
  at a tool's result itself (`population:<id>`); the model marks only when asked, after speaking.
- **Progress lines** say what is still running and for how long (`partner_tools.running_facts`), at most two a round,
  capped at 100 tokens, asked for so they start as the current words end (their length from response.done's audio
  tokens, 50 ms each) rather than after a silence.
- **The release tail is 0.05 s:** committing 0.12 s before the words' end lost none of them.
- **A Project named exactly that opens** (`day/catalogue.find`): "Open the Nuclei project" no longer matches every
  session that saw nuclei.
- **A reconnect storm ends Talk:** more than 3 reconnects in 60 s stop it with a plain message (the mini model's
  sideband dropped every few seconds: 14 calls in a minute).

Measured and not kept: `gpt-realtime-2.1-mini` (slower to first audio, 1 of 2 right tools, its sideband dropping);
a cache warm-up response at the start (no gain on the first turn); shorter tool descriptions (within noise).

**Where release to first audio goes** (quiet machine): commit 0.05 s, model to server audio 0.6-1.2 s (p50 ~0.85),
playout 0.15-0.2 s (WebRTC jitter buffer 100-137 ms, then the words' onset). About 1.05-1.1 s p50 release to heard:
the standard's 0.8 s is below what this model gives here.

**The outline step** is Cellpose-SAM on the camera's frame: on the bench's 1280 x 720 frame 5.8-6.4 s warm (the app's
own path adds about 0.2 s), 10-35 s when the machine is loaded by other work. micro-sam (light microscopy), measured on
the same frame: 0.65 s, 308 outlines against Cellpose's 343, 298 of Cellpose's matched one to one at IoU >= 0.5 (mean
IoU 0.78): an option for the user to choose; the default is unchanged.

### `think`: the frontier mind behind the voice (option THINK)

The voice in front answers at once and sees; a hard question, a request with several steps, a comparison of models
or a close look goes to `think(request, why)` (`voice/think.py`, `partner_tools.think`). On τ-Voice a fast voice
with a frontier backend scored 74.5% where the speech model alone scored 45.7%.
- **Started at once:** the tool returns `{"started": true, "task": ...}` in well under 200 ms, and the voice says one
  short acknowledgement. Spoken before it called think: nothing more is asked for. Silent: one "On it." (`ack`, at most
  60 tokens), never a second (HeyClicky's duplicate acknowledgement).
- **The backend** (Claude Opus 5.5 by default over httpx, effort low; GPT-6.1 Sol through the Responses API) gets the
  user's words, the turn's two pictures as at key-down, the Project brief, the recent conversation and the per-turn
  notes, and runs its own tool loop (at most 8 rounds) through `PartnerTools.run`: a voice request each, attributed
  to the Assistant, act then look. A change on screen comes back with the studio's report and a fresh window picture.
  It ends with `report(say, points, checked, done)`: "done" counts only when it also says every change was checked.
  Its tools are the voice's plus the brain's longer ones (`compare_models`, counts, tables, graphs, research reads).
- **Long tools are waited for:** behind the voice, a tool still running at the voice's 12 s (a comparison of three
  outline models loading cold took ~40 s) is waited for up to 70 s (`THINK_TOOL_WAIT_S`), and its real outcome goes
  to the backend; while think waits, the outcome is filed but not said as a line of its own (`PartnerTools.held`), so
  it is said once, in think's result. Past that wait the backend is told it is still running, and the outcome is said
  as one line when it lands, as any long tool's is (think-broad-1: think reported "they're still running" and the
  comparison came 40 s later during another step).
- **Progress while it works:** think's call is answered at once, so no tool round is pending; behind a quiet voice
  (no key held, nothing said for 2 s since its acknowledgement), a short line on what is under way and for how long
  (`running_facts`: "thinking with claude-opus-5-5, now running compare_models, 15 s so far"), at most 3, 8 s apart,
  never once its result is due (`RealtimeSession.tell_progress`).
- **Prompt caching:** the tools and the system prompt (the same for every task) are cached by a marker on the system,
  each round's growing conversation by the automatic marker; cache writes and reads are priced in its spend.
- **Which questions go to it:** the voice answers what is already here (what was found, how something was made or
  measured, what a researcher reported) itself from the brief or resources; think is for what needs working out,
  several steps, comparisons and close looks (think-broad-1: a question about how the nuclei were measured went to
  think and was spoken 16 s later). The model it runs on is a setting like the others: `set_default` think
  ("switch your brain to Claude"), and `models_in_use` has its row, "Thinking behind the voice".
- **The result** waits for quiet (no key held, nothing said), is written out of band in the voice's own words
  (`response.create` with `conversation: "none"`, text only, `metadata.task`; the backend's own words when that
  does not come back in 4 s), is added to the conversation, and is said once through the gate, with marks on the ids
  it names drawn as the words start. A key press while it waits holds it; it is never said twice.
- **Said when settled:** the result waits until nobody is talking, nothing is asked for, no tool of a turn is still
  running or unanswered and the voice has been idle `IDLE_S` (`RealtimeSession.settled`); its words are prepared
  once, and a delivery dropped for a key press keeps them (think-broad-2: said in the gap between the user's next turn
  and its tool round, the voice talked about that turn instead, and the second preparation held the user's turn 1.6 s).
- **Behind any partner session:** RealtimeSession's `deliver` and `tell_progress` when the session has them; otherwise
  the result is the one `proactive` line every partner session says (`think-done`), with a mark on its first point
  (Gemini 3.8 Live needs only `n`, `closed` and `proactive`).
- **Slow tools the same way** (`partner_tools.BACKGROUND`: `reference_photo`, `retrieve_page`): "started" at once with
  one acknowledgement, a progress line naming the tool, the result said once when it lands (`partner-background`,
  then `partner-think-said` and a `think` response), a reference photo's window marked as the words start; past 70 s
  still running, it is said as one line when it lands. `retrieve_page` waits up to 45 s and says a timeout as one.
- **A refusal** (Anthropic's safeguards may refuse life-science requests) is said as a refusal, the other model
  offered by name; another model never answers in its place. A missing key is said, with where to set it.
- Records: `partner-think` (task, model, rounds, tools with confirmed, checked, refused, ms, tokens) and
  `partner-think-said`.

