# Researchers

Retained capture digests exclude the derived `source.comparisonInput` UI hint, including
legacy whole-document receipts. Original source, pixel, revision and measurement facts
remain checked; adding comparison discovery metadata cannot invalidate older findings.

Part of the working context the root `AGENTS.md` maps. "Above" and "below" may mean parts it lists in
other files.

- The live agent works with what researchers made, not just their one-liners: `read_result`
  gives a researcher's work in full (text, a lesson's steps with answers, a picture's labels,
  sources, earlier follow-ups), `result_step` goes through a lesson or quiz one step at a time
  (the lesson scrolls to it; the voice hears the question, only the brain sees the answer),
  and `researcher_followup` asks a researcher about its own work. Researchers can be named the
  way people do ("the quiz", "the one on screen", "the joint one"). View notes list the
  researchers running first, then finished, then stopped (`project_state.ordered`; at most 4, so a stopped one is last or
  left out), so the voice knows what exists and hands anything about it over. `research_status` with no researcher named
  leads with the running one, else the one last spoken of in the conversation (its newest turn filed about one, `refs.rid`),
  then lists the rest in that order (`docs/architecture/session-index.md`, "The Project's one view").
- A live quiz on the slide (`orchestrator/quiz.py`, CONTEXT.md "Step kind", "Outcome"): a lesson
  step may be a plain question, a *find* step ("find a guard cell on your slide and click it",
  `target`) or an *evidence* step (what would confirm or refute the app's name for an outline);
  the lesson writer may write them when it has the user's photo of the subject.
  - **A click during a find step** is named as usual, then `Quiz.clicked` gives the fast model the
    facts (the target, the app's name for the click and how sure, what naming saw, the running
    look's name for that spot) and the model judges it and says so (chat, or the voice at a
    pause). No code compares names.
  - **An evidence step** comes to the brain with the outline's facts; while the user leads it is
    told to ask what they see before giving the guess (an instruction, not a gate).
  - **Outcomes** (right, wrong, skipped) are the model's: a judged click or the brain's
    `mark_step`. They are `quiz-outcome` events in the day's journal (latest per step counts), so
    they survive a restart; `result_step` to="missed" goes over the wrong and skipped ones, and the
    digest, the presenter's line and the studio's note above the lesson say how the quiz stands.
- Researchers are background subagents the brain starts (`start_researcher`), run in their
  own process (`services/research_worker/`, managed by `services/orchestrator/research/`).
  Effort sets their usual time, spend and tools (`research_worker/budget.py`): instant 15 s / $0.08, describe 25 s /
  $0.08 (descriptions only, instant pool), quick 75 s / $0.25, standard 6 min / $1, deep 18 min / $5. A lesson can be made
  from quick up, a video from standard (the 2026-10-11 demo: a short lesson at quick was refused for want of the tool);
  a chart or animation of a saved table from quick up (below).
  - **One request, one researcher** (Part 2.5; live 2026-10-28, "create a researcher that looks at the data on the
    table ... creates an animation of it", split by a pause, made two): `start_researcher` admits one researcher per
    request, by its identity (the action occurrence's `request_id`, kept in the researcher's `origin`), never its
    words. The same request answered again (its hand-off after hands started one, a completion round, a retry, a
    replay) gets the researcher already started back, with its run reference, and nothing new starts; starts listed in
    one answer of the request (`Steps.occurrences`: "make two researchers, one on X, the other on Y") are each their
    own. The tool no longer says "always start a new one": words that add to what the user just asked a researcher for
    go to that one (`researcher_note`), which the model judges from Researchers (it is listed queued, starting or
    running) and the conversation; a new one starts when they ask for another or for separate work.
  - **Its model's key, first** (the 0.4.0 release candidate): `ResearchManager.start` refuses at once when the model
    researchers use has no key (`engine_key_refusal`): the model is the one the research process runs (its override
    `MICROMIND_RESEARCH_ENGINE`, else the research setting; `research_worker/engines.make_engine`), and a key is there
    as the vision models decide it (`agent.key_needed`: the keys file and the Keychain, put in the environment by
    `keys.set_environment`). The words: "GPT-6.1 Sol, the model researchers use, needs your OpenAI key; add it in
    Models › Keys, then try again" (try: a New researcher Tab or the builder may have sent it). It is checked before
    admission, so nothing is proposed, held, queued or saved; the
    tool's refusal is the Assistant's answer, and descriptions and the builder are refused the same way. Before it, the
    research process's client failed at its first round and the card said only "could not finish: Something went
    wrong" (a Google key alone, Gemini as the Assistant, GPT-6.1 Sol for researchers). A scripted researcher needs no
    key, nor one started through a worker channel that runs no model (`runs_a_model = False`: the acting bench's
    stand-in); a follow-up of a finished researcher is not checked here yet.
  - **The clock never kills work** (the user's rule, 2026-10-11, after a 75 s cut ended the write-up of five motion
    tests): an effort's time and rounds are where a researcher is told to plan its write-up (at three quarters) and
    then to write up now (`engines._final`, every round, a round already going included); a write-up in progress
    always finishes, and what it had the app measure is its work (`results.partial_result` lists each operation with
    the app's own words). It is made to write up only by the stall rule (3 rounds with nothing new; the same finding
    again is nothing new), a capped day's per-run limit, or no answer to "keep going?": after an hour of running, and
    when its spend passes 3x its effort's usual cost with no daily cap, it asks the user and pauses until they answer
    (`researcher_note answers=`; 15 minutes of silence writes up). The app's watchdog stops only a research process it
    has not heard from in 5 minutes (it says it is alive every 30 s); a model call is cut at 10 minutes and tried
    again at most twice. The brain is told how long the app's operations take so it picks the effort that fits.
  The daily cap is unlimited unless the user sets
  one (the studio's settings, by voice or chat `set_research_cap`, or `MICROMIND_RESEARCH_CAP`;
  its amount kept until changed, spend counted per calendar day): then a run reserves its effort's maximum when it starts and gives back what
  it did not spend, so the day never goes over (`research/ledger.py`, per day; ADR 0004). Pools: 3 big, 4 instant (descriptions use at most 2). A call cut off (the
  user's stop) is billed without usage, so its cost is estimated (`spentEstimated`: request size, 400 output
  tokens, one search if offered) and counted against the daily cap. A presentation dispatched with no receipt counts
  its reservation as an estimate, except one the provider refused before any work (HTTP 429, or for want of credit;
  `ledger.never_billed`, which also reads a job saved before that rule by its words), which counts nothing. The
  research wires carry `spentToday` and its estimated part `estimatedToday` (`DayLedger.estimated`, every researcher
  today, of every Project), so the researcher page's "Research today: $0.40 · up to $0.80 more, receipts pending" says
  the recorded part as Spending's research rows do (live 2026-10-25: $1.20 beside $0.40).
- A researcher's hands (`orchestrator/research/hands.py`, the partner §2.2 and Amendment 1 A): from quick effort up,
  a researcher with the session tool can `run` one of the app's own operations for its own reading.
  `measure_saved_objects` accepts an explicit `{project, session, revision, input, digest}` selection discovered
  from a named saved Project's session-index entry. Ambiguous titles need clarification, never an arbitrary choice.
  It uses the same `scientific_measurement.run` admission and typed-table publication as Studio and Ask; no camera,
  segmentation or conversion runs. The immutable dispatch choice, original researcher run and Project opening
  stay checked through publication and cancellation. The shared measurement's 32 MiB analysis reservation and
  researcher one-at-a-time/effort operation limits apply; its trusted researcher
  identity is retained in the producer admission, and exact retries preserve the original producer. The result
  remains ordinary Project Data after research ends; it is not automatically displayed. This does not enable
  visual saved-input Ask delegation or broader native capability support for researchers.
  - **What it can run:** "segment" outlines and measures every cell (the cell finder, in the GPU worker's "research"
    class, served only when nothing of the app's own waits, so clicks go first); "enhance" makes a clearer picture
    from real frames (enhance.py: the average of still frames or the sharpest one; a focus stack only of the user's
    own last turn of the focus knob). Enhance is CPU work in a thread, the same cost as the user's own `enhance`
    (about 0.1 s at 1280x720), in no GPU class; it works on the eyepiece disc alone and reserves its own peak in the
    "research" allowance, held until its thread returns even when the researcher stopped waiting; when that can't
    fit it says what it needs. Its picture names the frames it was made from (their `(boot, seq)`). "motion"
    measures what moves in the live view over its last seconds (M3.2's "motion" operation, `seconds` 4 to 20, the
    slide's own motion taken out), kept as a tracks item made for it (`Tracks.made_for`). Its job is its own: the
    "research" class is in the runner's key, so it never joins or holds back the user's measurement of the same
    frames. Its capture and decodes are in the "research" allowance. It is never measured on a held clip (a
    frame every 0.5 s, and spots are followed from frame to frame only at the live view's rate) nor on one view.
    The "research" allowance is working memory for one run at a time: once a run's cells are saved, their table stays
    loaded in an allowance of its own ("kept", capture.py; evidence.BACKGROUND), never in "research" (will-experiment,
    29 Oct: an experiment's ten saved tables filled it and the next experiment stopped at frame 4).
  - **On what:** the live view now, or a sample the session still keeps (`input`): a population's view, or a held
    clip's frames (`frame`). A sample no longer kept is said so ("that view is not kept any more"), never captured
    again in its place; anything else is refused with what it is.
  - **Said as the Stage showed it:** with no kept input, the researcher's own step names "the picture on the Stage"
    (`researcher.STAGE`: its process can't tell a still from the camera), and the app's reply names which
    (`hands.stage_words`): "segmented the picture on the Stage" when its frames carry a still's original (as
    population.py says it), else "the live view". Counts are in words ("Checked the 1 entry it cites in this Project").
  - **What it makes:** a population entry ("Cell outlines, for Researcher 2"), a result entry with its picture
    ("Clearer picture, for Researcher 2", `Results.add(made_for=)`, the "for" in its own fields too) or a tracks
    item ("Motion, for Researcher 2"). Each is read with the session tool and never shown. It is never what a request
    of the user's means unless shown or named (`Population.made_for`, as M2.2's comparisons; "the motion",
    `TracksStore.latest` and the board's lines skip a researcher's), and anyone reaches it by its id.
  - **Classifiers compared** (Part 2.5, W5; "have a researcher run five classifying models on these cells and report
    comparing them"): `run` operation "classify" with `models` (two to six ids; the run is told the ones that can run
    here, `manager._door` "classifiers") and `classes` names the same cells of a population of its own session
    (`input`, else the one on screen) with each model through the runner at research priority, made for it
    (`model_comparison.compare_classes`), and keeps the comparison as one result entry with its table ("Model
    comparison, for Researcher 2"). Every outline is classified (`classify.chosen_members`); only past 400 cells, or
    past an expected $2.00 (`classify.expected_cost`, known before anything runs), is an even sample taken, and its
    words say how it was chosen and that the rest can be classified (will-research, 29 Oct: the first 60 of 161,
    said nowhere). Its reply names the models, the cells, the closest and furthest pairs, time, cost and the expected
    cost, and carries every number a report compares (columns, a row per model, every pair by name), so the entry is
    read only for a cell's own labels; the entry keeps its table and pairs first and each cell's labels last (a quick
    researcher paged 21,000 characters to reach the table and ran over budget). Its report compares the models from
    those records, cited as evidence, never from its own guess, and its spoken outcome carries the app's own record of
    the comparison (`voice/partner.research_text`). One operation of its run, whatever the number of models.
  - **Limits:** one at a time across the app, at most 3 in a run, refused with the reason and a next step a
    researcher can take when the camera has no fresh picture (live), memory is full or another session is open; the
    session keeps what it ran and on what (`ran`).
- Missions (`orchestrator/research/missions.py`, the partner's Amendment 1 B; P2c-1, the app's side): a
  researcher with a plan the app runs on a schedule: an operation of its hands every N s (at least 60) for a
  window (at most 4 hours for now). It is not a model running for hours.
  - **Its slots:** a baseline at the start, then one every period to the end of the window (`CONVENTION` "t0+",
    the playbook's section 14: 3 h every 10 min is 19). Due times are wall-clock labels; waiting is on the monotonic
    clock, so a step of the wall clock never bursts or stalls them.
  - **The ledger:** one row per slot, written once as it happens (never created ahead), with its real time and the
    frame's own time, saved atomically with the researcher (`Session.mission`): done (the entry it made), failed,
    missed (past its grace: half its period, at most 2 minutes; nothing is backdated), or not comparable.
  - **The field:** pinned at the start, from a fresh picture (the view's facts and the objective). A slot is "not
    comparable", said why, and its operation never runs, when the view is off the field: the slide moved past a
    tenth of the eyepiece's radius, continuity lost or not registered, the eyepiece's scale changed past contract
    3's 0.5% (a refocus is 1-2.4%), or another objective, picture size or run. The view the operation took is
    checked too, its entry kept but never compared.
  - **The rules:** slots never stack, never run during their mission's checkpoint, and wait within their grace for
    the app's one research operation; a mission's slots are not counted against a run's 3 operations.
  - **Restarts and days:** after a restart it is "interrupted" with its ledger, and only an interrupted one is
    carried on: the slots due while the app was down are rows "missed: the app was not running". Midnight does
    nothing to it; while another session is open its slots are "not comparable", said why, and they go on when its
    session is opened again. A finished or cancelled mission
    is kept with its ledger when a new one starts (`past_missions`); an interrupted one must be carried on or
    cancelled first. A hand-edited mission that can't be read is dropped, never its researcher.
  - **Not yet offered** to the brain: starting one from a request (ADMIT, the day's money), checkpoints and the
    finding are P2c-2.
- Progress in plain words (2026-10-11; a card read "Read d1: characters 0–4233"): a step's `text` is what its
  chat card and its Tab's steps say (its strip pill says only its state or its clock, the step in its tooltip): never
  an entry's id, a character range, an exception's name, a file or a URL. Those go in
  the step's `detail` (`Researcher.step(text, kind, detail)`, kept in `steps.jsonl` for Activity). The app's own
  words about a run are said without the ids the researcher reads by (`researcher.said_words`). A viewer notice says
  "shown" only once the studio confirmed it (`announcer.shown_words`). `tests/test_research_progress_words.py` reads
  every step call. Showing its work beside the view (`Announcer._open_beside`) asks its original studio for a current
  report first when the last is stale, as every actor's request does (`screen.fresh_report`; live 2026-10-29 17:25 it
  failed "unavailable or stale"), and a report with no viewer keeps the work in Data instead of raising.
- Each run's conversation with its model is kept round by round (`research_worker/transcript.py`): what each request
  added, the tools offered and the model's whole reply with its tool calls, the instructions by their hash, pictures by
  their size, a line at most 64 KB (long strings cut, marked), in `research/transcripts/<day>/<rid>.jsonl` (retained research evidence; the app names it, spec `transcript`).
  Existing `logs/transcripts/` files are also excluded from pruning and remain in place until explicit collection.
  These bounded traces can be incomplete if writing failed; they are not a promise of a complete provider transcript.
  Web pages inside are untrusted data the app never reads back as instructions.
- Discipline (W6-4), all in the research process: researchers are told an honest "not found"
  outranks an invented number or claim; a researcher whose last 3 rounds (`budget.STALL_ROUNDS`)
  brought no new page, finding, picture or output is told to write up (a "stall" step, logged);
  each point it says came from a page (`finish.claims`) is looked for in that page's text before
  the result is announced (`research_worker/claims.py`; option CLAIM_CHECK "model" since 2026-10-11, the user's call:
  a point on its page word for word is found, else a fast model reads the page's closest passages; "words", the
  key words and numbers on the page, passed every negated point, `docs/bench/claims.md`), and points not found are marked "not found in its sources", kept
  word for word and said with the result; a deep run's first round only calls `plan`, which is
  shown and said at once (`Announcer.research_plan`) and steered by a note, never waited on.
- Lessons that need the picture (W6-3, option LESSON_CHECK, `research_worker/lesson_check.py`): a
  model with no picture answers each quiz step and the steps it gets right are logged (default
  "check"); "rewrite" sends them back once, with the photo, to need it (the bench found it does
  not beat the lesson as written: `bench/lesson_check_bench.py`).
- **Their model** (Will, 2026-10-30): researchers think with Claude Opus 5.5 by default (RESEARCH_ENGINE
  `claude-opus-5-5`, `research_worker/engines.ClaudeEngine`: the Messages API over httpx with Anthropic's web search
  `web_search_20260209`, ANTHROPIC_API_KEY, the effort's reasoning as `output_config.effort`). GPT-6.1 Sol stays an option
  (Settings, or the user's "use OpenAI / GPT"). This model refuses a forced tool choice, so a write-up round offers only
  `finish` and says "Call finish now".
- **Pages it writes itself** (`make_page`, from quick up, budget `PAGE_TOOLS`): one self-contained HTML document, a
  short slide presentation (`kind` "presentation", `contentKind` "report") or a small interactive or animated visual
  (`kind` "visual", `contentKind` "visual"), built from the numbers it read (`data_from` names the entries). Kept as an
  `html` output and served like a lesson under the research files' no-network sandbox CSP, so the studio's viewer and
  reader show it rendered (a sandboxed iframe), never as text. The prompt tells it to make one when the user asks for a
  presentation, slides, a visual or "a little animation explaining the data" (the 30 Oct live miss: a quick researcher
  read the table, stalled and wrote "the animation itself has not been created").
- **Charts and animations of saved data** (`research_worker/measurement_charts.py`; Part 2.5): from quick up a researcher
  has `make_chart` and `animate_measurements` (budget `CHART_TOOLS`), offered only when it can read the session's index
  and never for a follow-up answer, a new version or a description. It names a table the session kept (an entry's id,
  and the `page.revision` its read gave, or none for as it is now), the rows (a JSON pointer under `/detail/`, a
  measurements table's `/detail/members`) and the number in each row (`/extras/areaPx`), with an optional label, group,
  order and kind (one bar per row, or a histogram). The worker reads the whole entry itself, page by page off the model's
  rounds, checks it against the revision, and copies the values: a row with no number is left out and counted, never
  imputed; `only` (a pointer to a true/false in each row, motion tracks' `/moving`) keeps the rows where it is true.
  Several runs of one operation are one chart (Will, 30 Oct: "run a motion model 4 times ... make diagram of average
  speed"): make_chart kind "means" names `tables` (one to eight entries, each read whole at its revision, the same rows
  and value in each; motion: `/detail/values/tracks`, `/net`) and draws one bar per table at its mean, a whisker of one
  standard deviation and every value a dot, n under it; its reply and `content` carry each table's n, mean, SD, SEM,
  median and range and the mean of the means, and every table is among what it was made from (`derived.sources`).
  The chart is drawn on this Mac by the app (Pillow; no model, provider or money); the animation builds that
  chart row by row in the chosen order and ends on the whole chart, an animated PNG every browser plays as a picture.
  Each is kept as one of its outputs with `derived` (its exact source id, revision, kind) and its values in `content`:
  media `derived`, said "Drawn by the app from a saved table" or "An animation ... not a video of the specimen", in Data
  as "Researcher N's chart/animation" whose picture reopens, and its table is among what it was made from. When the
  animation can't be drawn here, the chart is made instead and that is said. A generated video stays its own deliverable
  (`make_video`, `research_worker/video.py`: Gemini Omni 1.1 Flash through the Interactions API since Google retired Veo
  3.1 Fast on 22 Oct 2026, recorded in `catalog/models/maintenance.toml`), made only when it is asked for by name; one
  that fails says the model, why in a person's words and what to do (`api_usage.refusal`: retired, out of credit, no key,
  rate limited...), with the provider's own status as its detail, on the researcher's card ("The video model (Gemini Omni
  1.1 Flash) couldn't be used: your Google account is out of credit; add credit to your Google account, or choose another
  model; no video was made"). The run keeps it as a fact its result carries (`unavailable`: model, reason, said, todo,
  status), whatever the write-up says, so the voice says the reason when the researcher lands; its model is told it can
  animate a table instead. `tests/test_researcher_outputs.py` reproduces the 2026-10-28 request and Will's try-out of 29 Oct.
- Out of time or money after making something, a researcher keeps its work: status "partial",
  a result described from its outputs, restored and shown like "done". A URL that failed is
  not fetched again by the same researcher.
- Every output carries readable `content` (`research_worker/results.py`): a lesson's title, key
  ideas and steps (`q1`... with prompt, answer, hint; the page has matching `id="q1"`), an
  annotated picture's labels (name, where, box 0-1 or null), an illustration's or video's
  prompt. `Session.readable(part)` and `Session.steps()` give the brain this text; older
  lessons, made before content was saved, are read from their HTML as text
  (`research/content.py`, never run).
- A result also says what it cannot settle from what it saw and found (`limitations`, at most 4)
  and what to ask next (`next_questions`, at most 3), in its own words.
  - **Where they show:** in the viewer and the Research tab, and to the brain in `readable()`.
  - **The Researchers tab's card** counts what the researcher kept ("Picture ready · Researcher 1 · 5 observations, 2 sources, 1 picture", `research-summary.ts` `holdsText`). The counts come from its retained work, never made up. Clicking its row in Researchers, or its pill on the Working strip, opens its Tab (Show beside the live view, once it has work to show, is in that page's ⋯).
- **Researchers read the session's one index** with the read-only `session` tool (`research_worker/session_index.py`; scaffolding Amendment 2, point 7). Its actions:
  - `summary`;
  - `list` by kind or query;
  - `read` an entry in full by id;
  - `picture`.
  - **How it reads:** the app's own `GET /api/resources…` on this machine, at `ResearchManager.resources_url`, which `server.main` sets once it knows its port, to the researchers' view (`/for-researchers/api/resources…`; ticket 66): the entries as they were before Data's fields, and a researcher's sources, tables, charts and other work only when it lists that kind, so nothing new reaches its listing by default (`docs/architecture/session-index.md`). It never goes through the web path, and is scoped to the researcher's day. Its `summary` also carries `project`, the first page of the Project's one view (`project_state.py`: its stage, its data with exact references and makers, its researchers, its Studies), only for a session a researcher of this app is working in (`ResearchManager.working`), else `{unavailable}`; a view that cannot be read never fails the summary.
  - **What the model gets:** the results are data inside `<session source="app" untrusted="true">`.
  - **Pictures:** fetched only on `picture`, as JPEG.
  - **The stall rule:** an entry read in full counts as progress.
  - **When it is offered:** only when the address is set, and never to descriptions.
  - **What researchers are told:** nothing is pushed into a researcher's prompt; it pulls.
- **A finding cites the session's records it rests on** (`research_worker/evidence.py`; ADR 0015):
  - `finish.evidence` lists index ids, and `finish.session_claims` gives each point with the entries it rests on.
  - **Checked before announcing,** in the session's index, as web claims are checked on their pages.
  - **A point whose evidence is not in the session** is marked "evidence unavailable", kept word for word. When the index can't be read, it is "not checked".
  - **Where it is said:** in `readable()` ("Evidence it used from the session: e3 (Cell outlines · 18:04, 18:04:11)") and in the Research tab ("From this session", "Evidence unavailable").
- **What a researcher had the app make and looked at** (its working set, the mission card; partner §2.2):
  - **Made:** what its `run`s made, from the app's own record (`Session.ran`, hands.py), never said as read.
  - **Looked at:** each record its `session` tool read or looked at (a picture), once per way, revision and run,
    with the index's own title, kind and the revision it read (`looked` events → `Session.working`; the newest 40
    kept, the rest counted, kept across restarts). A title the research process did not give is the app's index's.
  - **Where it is said:** on its card in the Research tab ("It had the app make: … It looked at: Cell outlines ·
    18:04 (e3), 1 picture", the runs together) and to the brain in `readable()`, after the app's own qualifiers, each
    run apart (its result, then each later question by its words). A population corrected since it was read says "read at revision 1, corrected
    since" (Amendment 1, C1; today's records only).
- **A researcher may ask the user for evidence it lacks** (`request_evidence`, at most 2 a run; UX §2: "I need 15 seconds at this field. Record it?").
  - **How it is said:** once, where the research was asked for (`announcer.research_request`), filed on the researcher (`requests`, kept across restarts), and shown on its Tab and in its Researchers row with the answer buttons; its pill on the Working strip says "Needs you" (its tooltip "Researcher 2 asks for …").
  - **The answer:** the user's next note (`researcher_note`, `answers=q1`) answers it.
  - **Footage on a yes** (`orchestrator/held.py`): with `hold_seconds`, the app holds that many seconds of the
    live view for it (at most 60), a frame every 0.5 s, real frames by reference with their slide offset
    and epoch, in memory under the "unsaved" allowance (a hold past it stops and says so). Nothing is
    written to disk: it is not a recording. It is an index entry (kind "clip", `k1`), read with the
    researcher's session tool; the researcher is told its id and when it is held. Opening another session clears
    it.
  - **Nothing waits, and no answer is dropped** (the end of the effort's kind (h)):
    - **While it works:** the researcher carries on, and what stays missing is a limitation of its finding.
    - **Its finish waits a little** while one of this run's questions is unanswered, or footage the user agreed to
      is still being held: 30 s at most, once each, and only when time and a round are left to read the answer
      (`research_worker/researcher.py` `wait_for_the_user`; the app's notes carry marks, `answers`, `holding` and
      `held`, so the worker never reads the words).
    - **An answer after it finished** carries it on (`ResearchManager.carry_on`): a continue run, with its footage
      held on a yes. It is held to ADMIT like any continue, so a lesson, a video or a deep run still needs the
      user's own words.
  - **The user's alone:** Record and Save are never pressed for them.
- Typed in a researcher's own composer (`research-followup`, commands.py), a question goes to the Assistant with
  which researcher and which of its versions as context for the model only (`ask(..., said=)`, the brain's
  `turn(said=)`): the chat line, the conversation's turn and Activity's request keep the user's own words, never the
  internal "[Selected retained artifact: {…}]" prefix (live 2026-10-25). The researcher's page shows it and its answer
  from its `interactionJobs`.
- A follow-up question (`ResearchManager.followup`, mode "answer", the default) is a quick
  researcher with no picture, video or lesson tools that answers from the earlier work (the selected
  version whole, given as untrusted text) and the web; the answer goes into `history`, the work is
  unchanged. It works on done, partial, timed-out and restored researchers. Asked while a researcher
  works, the question is kept and asked when its run ends (`Session.pending`), never refused;
  `read_result` reads what it has so far meanwhile (during a continue, the finding it continues).
- Mode "continue" carries on the work itself, at its own effort or one the user asked for ("go deeper"),
  from one package the same on every engine (`ResearchManager._continuation`): the original request, the
  current finding whole by version, its open questions, its earlier answers and an index of its earlier
  runs, then the new request. No response chain is carried from one run to the next (the API refuses a
  chain that ends on `finish`'s unanswered call: `docs/bench/research.md`). `brief` stays the original
  request; the newest question is `asking`. A new researcher may build on a finished one's kept work
  (start_researcher `build_on`).
- What the user or a model wrote is kept whole (`research_worker/limits.py`): a field up to 16,000
  characters, else refused saying why, never cut; a run's assembled brief up to 90,000, leaving out whole
  optional parts (said) before refusing. Previews end in "…" (`untrusted.inline`). A malformed or partly
  invalid `finish` is kept as sent and the outcome is partial, never "done"; a run that never wrote up
  keeps what it recorded.
- Versions: every output has `id` ("o1"...), `version`, `replaces` and `change`
  (`orchestrator/versions.py`); an output and what replaces it is one line, its newest is
  current, older ones are kept, listed after the current ones (presenter, Research tab, "v2 ·
  change" in the viewer) and never deleted. `Session.current_outputs()`, `lesson()`, `steps()`
  and `readable()` use current versions (the newest lesson when there are several). Outputs
  saved before versions load as version 1.
- Revising (`revise_work`, `ResearchManager.followup(mode="revise", target, change)`) makes a
  new version of one thing, in the research process, at "revise" effort (150 s / $0.20; a
  video at standard): a lesson's content is edited by one call and only what the change is
  about may differ (steps the change does not name are copied, in code), then its page is
  written again; a plain label rename or removal is done without a model (drawn by the app when
  every label has a box, otherwise annotated again on the original photo), other label changes
  take one edit call; an illustration is edited from its current version; a video is made again
  from the photo with the change added to its motion (explicit request, as for a new video).
  It shows as "revising", then the new version opens beside the view where its viewer shows the version it
  replaces (in the Hooke workspace it is in Data, "Researcher 3: its work is in Data", and opens in the reader from
  there) and is announced ("Researcher 3 revised its lesson (v2: ...)"); the result and earlier versions stay.
- Steering: `researcher_note` / `ResearchManager.note` adds the user's words to a working
  researcher's inbox (`inbox.jsonl`, kept); it reads them before its next round as a message
  from the user ("Got a note: ..." in its steps). Refused once it has finished.
- Numbers are stable and the session's (ADR 0017): `n` is saved and never reassigned, all of the session's researchers
  come back after a restart from every date it touched (whatever their status; one that was working comes back
  "interrupted"), and a new one gets the next number after every number used in the session, so each session starts at
  Researcher 1. A researcher keeps working after the user opens another session and files its work in its own; the
  session open gets one line ("from 'Blood smear'"). Only the session's own are continued: one of another session is
  refused ("Researcher 2 belongs to “Blood smear”; switch to it to continue"). Spend is charged to the calendar day a
  run happens on, whatever session or folder it is in (`research/ledger.py`, `runs.jsonl`).
  A reference that fits two researchers about equally ("the joint one") is asked about ("Do you
  mean ... or ...?") unless one of them is on screen. An `origin.thread` set by the caller is
  kept and sent as `thread`.
- Video, lessons and deep research start only when the user asked for one (option ADMIT,
  `research/policy.py`; "report" is no longer offered and reads as research). "quote", the default: the
  model judges it and quotes the user's words (`asked_with` on start_researcher, revise_work and a
  continue; never filled from a paraphrase). Refused for want of their words, the request becomes a
  proposal (`p1`) the user can say yes to (start_researcher `confirms`), which starts exactly it. The app checks
  the quote is in what the user said in the last 30 s (`quoted`; by voice, what they were heard saying or
  typed in that half minute, never a hand-off's own text: `Conversation.heard`); the same quote marks research the
  user asked for, whose result is spoken. No word list decides which deliverable a request wants or admits one: the
  "keywords" option (the EXPLICIT word lists, as before 2026-10-06) is gone (2026-10-29; it refused "show me step by step
  how it works", and its `animat\w*` matched "create an animation" of a measurements table to a Veo video, live
  2026-10-28). The model picks the deliverable from the ones and the tools it is shown: `image` is a made illustration
  or diagram of a kind of thing, labelled or not, needing nothing outlined; `annotated_image` draws labels on a photo
  of the user's own outlined thing, and with nothing outlined it is refused to the model as its call to correct
  (`argument`), never told to the user (live 2026-10-29, "a small labeled graphic of a stoma"); a record retained under
  "keywords" is checked by the quote. `admit` runs
  inside `ResearchManager.start` and `followup`, so "continue" and a new version of a video are
  held to it too and no path skips it; the tool refuses otherwise, and the answer says so.
  Which researcher a request means is `research/desk.py`; a follow-up answer or a new version
  runs on top of finished work as one `Run` (`research/run.py`), and one cut off by a restart
  is recorded in its history.
- Everything from the web is data, never instructions, and it crosses only through
  `orchestrator/untrusted.py` (no other module strips brackets or spells a wrapper; a test checks):
  it reaches the brain only inside `<researchers source="web" untrusted="true">` (`block`), a
  researcher's own follow-up or revise only inside `<earlier_work ...>`, the voice only as a
  templated, capped line (`inline`),
  and pictures only through `safe_fetch` (public addresses, re-checked redirects, size cap,
  re-encoded; Commons API first for licence and author). Research files are served read-only
  with an allow-list, `nosniff`, and HTML under a no-network sandbox CSP.
- Show me (`orchestrator/showing.py`, tool `show_me`; partner §2.3, ADR 0015): "show me what it found" starts
  a showing of a researcher's finding. The brain is given the finding's records with how each is shown
  (`screen.PRESENTED_AS`: cell outlines and motion tracks drawn on the picture with `show`, the rest opened in the Data
  reader with `open`, ticket 66 D6; one not found or not checked is said so, never shown as if it were) and the
  finding's words only inside the untrusted wrapper, then shows them with `present` as beats: a beat of
  the request that started the showing is the showing's (`act`'s `by`). For each property a beat changed
  (only what it changed, once the studio shows it) the showing keeps what it was before its first beat
  and what it set last, and for the cells' layer the whole style its first beat replaced (a colouring's
  legend and values too). Stopping it (the dock's Stop or Escape, said in the hint; "stop":
  `show_me(stop=true)`; a new showing; opening another session, which runs the same stop) puts a property back only
  while the showing still owns it and the studio still shows what the showing set (compared as the
  beat's own check did: a picture by its source, a place or size within 6 px); a panel's opening, what
  it shows and where it is go together, so a panel the user changed in any way since (work opened from
  the shelf, the chat or the Research tab is reported as theirs; closed with its ×) is left whole, and
  what it shows is put back only while it is open. What the user or a later request changed stays, and
  is said so, in plain words. After a stop its request makes no more beats, and a beat confirmed after the stop is
  put back at once. A panel it closed is said, not reopened; research and recording go on.
- Researchers' pictures are model output: shown in the magnifier or the Research tab with
  the model named, never painted over the live picture. Annotated photos are checked for
  redrawing (`research_worker/fidelity.py`) and marked "altered" when the model changed the
  specimen.
- Descriptions under labels (`orchestrator/enrich.py`) are one researcher (describe effort)
  per label key, general about the kind of thing (not one photo), rate limited 6/min, cached
  per day. Label keys merge true synonyms only (`orchestrator/labels.py` SYNONYMS: "phloem
  fibre cap" = "fiber cap", "erythrocyte" = "red blood cell"); a description is redone only
  when the subject changes, not its magnification, focus or staining words (`subject_key`).
  First live session (2026-10-04): finished descriptions took 8-14 s and 7 of 39 timed out at
  15 s, hence 25 s.
- Results the user asked for are spoken at the next pause on the delegation that started
  them (`brain/narrator.py`); results of researchers the brain started on its own are spoken
  only if significant (or a correction), otherwise noted in the notebook. Quieter speaks
  only what was asked for.
- Corrections (`propose_correction`, from a researcher or the brain) change nothing until the
  user accepts one; accepting relabels and records who suggested it (`revisedBy`).
- Generated media say so in the file itself, not only in the studio (`research_worker/provenance.py`):
  - **Pictures and videos:** a visible line is burnt in ("AI illustration, not microscope
    footage", or "Labels added by AI on a real photo"), drawn by the app. The IPTC digital
    source type and an XMP packet (PNG) or the file's metadata (MP4) are written too.
  - **Revisions:** the unmarked picture is kept as `*.clean.png`, and a revision edits that.
  - **Web pictures:** what a downloaded picture says made it (XMP/IPTC, C2PA) is read before
    `safe_fetch` re-encodes it. One made by an AI model is never used as a reference.
- Videos are labelled generated illustrations and play muted; lessons carry their pictures
  inline and are served under a sandbox that loads and connects to nothing.
- Sessions live in `services/research/<day>/<rid>/` (git-ignored, like recordings: keep
  them); `services/research/findings.jsonl` is the provenance ledger.

Research sessions optionally retain `stepAt`: the service's receipt time for an actual
worker step, not a heartbeat or estimated percentage. Older records omit it. Studio
shows elapsed wall time and last-step age, with an indeterminate working indicator.

A researcher's wire names the request that started it, or last continued it (`requestId`, from its origin's
`request_id`), and that request's typed turn (`turnId`, from `turn_id`). The studio matches a researcher's card to its
request by `requestId`: `turnId` is set only for typed turns, and is null for voice and manual starts (which have no
user turn and record 0). Each is null when it has no origin or the origin did not record one. They are identities
only: they grant nothing and change no work.

New `purpose=research` sessions with a workspace origin persist `deliver: 'tab'`: their finished work is kept
in Data and in Researchers (the studio shows it in the researcher's own Tab, which is never a saved Tab), and is
never placed in a saved Tab by the app. The existing completion hook records a kept receipt for each accepted
version (`available`, "Kept in Data and in Researchers.", no notice) and makes no Tab; a person opens the work in
a Tab when they want it. An output Tab an earlier version made stays as saved. A saved session without `deliver`
keeps legacy origin-Tab delivery. Loading never retrofits old researchers or reruns their work.

Explicit session-resource reads of dataset tables, analyses and graphs include their accepted numeric snapshots,
not merely titles or summaries. The journal-selected version, owner-relative path and SHA-256 are verified before
returning rows/results. Closed Projects read their own immutable files without rebinding the active store. The
existing paged HTTP session read keeps content-digest continuation; corruption returns unavailable data, never
recomputed replacements. Metadata lists remain cheap. This enables reading accepted calculations; it does not add
researcher mutation operations or claim a completed scientific report.

## Local Study documents and execution boundary

`efforts.py` retains objective, captured inputs, tasks, reservations and findings.
`effort_api.py` exposes local read/write/control without authentication. Managed
execution is an optional operator-selected extension; see docs/CORE-BOUNDARY.md.
No extension means no managed imports, provider calls, staging or collection.
Uncertain external work keeps its receipt and held allowance; Stop does not invent
provider termination. Local scientific tools and ordinary user-configured providers
remain independent.

A researcher's presentation (`research/presentation.py`, `research_worker/presentation.py`) is optional: with the
setting "auto", findings ready start one on the qualified automatic producer (`AUTO_PROVIDERS`, GPT-6.1 Sol when its
key is there), whichever model Researching uses, and the standard layout is shown until it is made. A refusal for want
of credit is said in fixed words ("OpenAI has no credits left."; the studio's line reads "Presentation not made: …"
with Details, the raw text in Activity). After one, an automatic presentation is not dispatched to that provider for
30 minutes (`CREDIT_HOLD_S`): it is "unavailable" with the same words and no notice, so the standard layout stays and
each later researcher does not end in a failure (live 2026-10-25). One the user asks for still asks it, and an answer
clears the hold. Researching's own model is no automatic fallback: a provider becomes automatic only through the
retained-input campaign (`docs/testing/research-lifecycle-acceptance.md`).

Researcher `run(operation="workflow", params=...)` uses the original dispatched research attempt and Project, without a camera input. It can discover, create, validate and start capability workflows, retaining an invocation receipt. It cannot accept scientific reviews or launch nested paid researcher nodes. Workflow execution outlives the research worker and keeps its own authoritative attempts.

### Studies with local workflows and connected agents

`studies.py` is the common scientific-intent boundary. A Study retains its exact input revisions,
claims, challenges and evidence reads, and references the canonical WorkflowService runs through
door operation IDs. It never creates another workflow attempt ledger. `door/studies.py` supplies
authenticated transport; local Studio and researcher Hands use the same service. Caller identity
comes from the connection or dispatched local attempt, never request-body author fields.

`study_researchers.py` starts the existing local researcher manager within the Study's allowance.
It captures the Project and original worker run before dispatch. `study_local_results.py` reads
that exact run's retained findings and usage; later work or presentation revisions are not a
substitute. Delayed usage updates may revise accounting, not scientific results. Findings are
unreviewed until a person makes a scientific judgment. Unknown termination retains the allowance.
Local-only Projects cannot launch a model researcher. Provider keys stay in the ordinary key system.

A Study researcher must use `study-run` for workflow execution. Direct workflow start/capability
execution and work in another Study are refused at Hands, so Pause/Stop and the Study's evidence
cover its child workflows. Disconnecting a client or closing a view never cancels admitted work.
Repeated request identities return the original receipt; missing admissions and lost drivers are
reported as uncertain, never silently replayed. Stop requests all children, retains per-child
failures, and stays pending until termination is observed. Managed execution remains an optional
operator extension; these local paths do not depend on a cloud project or a named agent family.

Study conversations (`study_questions.py`) retain questions and attributed answers in the
Study's versioned document. Each question pins an immutable Study revision; subsequent
changes do not rewrite its context. Exact answer evidence must resolve in the same Project.
Request retries do not duplicate questions, and different answers cannot overwrite the first.
The current UI keeps this conversation inline, separately from reviewed findings. The
connector recipient operates externally: no subprocess launcher, provider billing or new
workflow attempt ledger is implied by saving a question.

### A researcher auditing an experiment

An experiment (`orchestrator/experiments.py`, Part 2.5 W5) the user approved with an audit runs a second frozen
workflow whose one step is a researcher through this app's researchers (`workflow_research`, provider
`local-researcher`; `docs/architecture/scientific-workflows.md` "Experiments"). Its input is the run's record (a JSON
file: the plan, each frame with each model's run and counts, the matched cells, the comparison step's areas, and the
Data ids to read); its brief is the question and criteria the user approved, the records to read with its session tool
(the comparison table, each model's cells per frame) and how to judge them (check the table's counts against each
model's cells, cite the records each point rests on, say what the run cannot settle, agreement is not accuracy). It is
started `confirmed` (the plan the user said yes to admits it, at its effort's cap), with origin `experiment` and
`asked` false: no camera snapshot, no beside-the-view opening of its own, and its finish is the experiment's step, said
once in the experiment's outcome. Its finding becomes the step's `research-findings` output, not reviewed by anyone.

### One saved workflow from every entrance

`tests/test_workflow_entrances.py` qualifies the integrated WorkflowService across the entrances (Part 2.5): one saved
definition on one retained input, run from the brain's `workflow` tool, the studio's `POST /api/workflows/command`, a
researcher's hands (`run` operation `workflow`) and the door (`operations` tool `workflow`, through its Study), is the
same frozen definition (id, revision, digest), the same exact input and the same result by the same real steps, every
run in the one history the studio lists, each attributed to its actor. The brain, the studio and a researcher name a
run by the caller's request identity, so the same request from any of them is the same run; the door's runs are its
own (scoped to its Study and actor; its retry is the same run). Not shared yet: a workflow saved in the studio or by the
Assistant is not listed or runnable from the door, which reaches only the workflows created or duplicated through the
door for that Project (`door/projects.py` `require_workflow`; duplicating needs that association too).

Study listings expose `createdAt` from revision 1 and `updatedAt` from the current retained revision. These are saved-document timestamps, not inferred execution start/end times. Legacy missing initiators remain unknown. Reads never add a revision or start work.

With optional Project locations, `research/storage.py` resolves retained researcher
records and files under their captured Project. Previous metadata is preserved by
content hash; failed publication leaves a durable pending marker and readers refuse
uncertain current state. Root replacement/closure and same-ID foreign work refuse.
Historical interrupted jobs are projected without automatic dispatch or rewriting
read-only folders. Provider starts/follow-ups/presentations in portable Projects are
explicitly unavailable pending host billing and lifecycle qualification; ordinary
legacy Projects retain their existing execution behavior. Shared acquisitions and
global description caches are not yet collected by this adapter.

Study evidence reads its original workflow receipts from one bounded Project inventory scan per request. The same workflow status derivation and observed driver ownership serve individual and batch reads; no persistent status cache or dispatch is added. Missing runs remain interrupted admissions, and unreadable records prevent a false completion claim.
# Portable copy verification

Folder sealing verifies current and archived researcher metadata against their
Project/date/researcher identity, content-addressed archive names and retained
finding payload digests. Pending publication refuses. Verification starts no
manager or provider and does not qualify complete external dependency collection.

Material questions retain explicit exact attachments alongside the pinned Study basis. Changed attachments cannot reuse a request identity. Reading the question context returns those references for the authorized contributor to inspect; it does not launch an agent. Studio single-capability experiments may use Study admission, which keeps the same authoritative workflow execution and original operation identity.

Study operation observation resolves its captured Project owner off the event loop,
including a cold non-active Project journal. It continues to project the original
WorkflowService run without replaying work or changing the admission receipt.

## Retained initiation and collaboration

`coordination.py` stores reviewed plans and collaborative assignments in the existing Study revision/task store. Plans pin exact material and instruction content. Claims are atomic, five-minute renewable leases; expiry requires reconciliation by the original actor and never starts replacement work. Completion retains an attributed report and waits for linked WorkflowService executions to settle. Assignment reports are not human findings. Study workflow admission enforces a reviewed plan's experiment count and reuses identical scientific specifications across contributors. Token/time limits are declared here, but need separately qualified external harness enforcement. Reads and reconnection never dispatch. `coordination_api.py` is shared by Studio and the authorized door.

Study material selection uses the shared material projection, excludes organization and workflow receipts, and preserves exact historical inputs. Generated artifacts remain selectable without changing their origin.
