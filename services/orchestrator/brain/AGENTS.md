# One brain

Retained `read_analysis` resolves its captured Project's numeric storage, matching
ordinary UI and index reads. It reads accepted numbers and never reruns an evaluator
to recover a saved result. Existing request/Project admission rules still apply.

Part of the working context the root `AGENTS.md` maps. "Above" and "below" may mean parts it lists in
other files.

- Chat and voice are one mind (`services/orchestrator/brain/`). `Brain.turn(text, channel)`
  answers typed questions (`ask`) and every request GPT-Live delegates by voice; both share
  one `Conversation` (the last 8 turns, spoken and typed) and one tool `Registry`. Add a
  capability once, as a tool in its family in `brain/toolbox/` (scene, threads, magnifier,
  research, narration, view), and chat and voice both have it (the voice prompt lists the
  registry's tools). `toolbox.build(Services)` is the one way the tool list is made (the app,
  tests and benches; a tool is there only when what it acts on exists; ADR 0003); a tool fails
  with `ToolFailed` (an `orchestrator/tools.ToolError` is one).
- **Two voices, one conversation** (option VOICE). **GPT-Live** (`gpt-live-1`, a choice picked by hand) hears and speaks
  and hands each request to `Brain.turn` by delegation; everything below about delegations, hands and view notes is its
  lifecycle. **The one brain** (`gpt-realtime-2.1`, the default since 29 Oct 2026, when the live partner's stack became
  Hooke's default; `voice/openai_realtime.py` + `voice/partner.py`) hears, sees the camera and Hooke's window
  at each key-down, calls a curated part of this registry itself (`voice/partner_tools.py`,
  through `ActionRunner` in a voice Ticket of its own, the Assistant as its actor), points with `mark`, and hands hard questions, many steps and comparisons to `think`'s
  frontier backend (option THINK; it replaced `do_in_hooke`). Its lifecycle per turn: key down (barge-in if it speaks; perception
  taken) → release (picture and note in, commit, `response.create`) → tool calls (output, a fresh look after a screen
  change, then the model goes on) → its words, filed as GPT-Live's are; lines nobody asked for are said only in a
  pause. Both write the same journal lines and conversation turns; neither replaces the other
  (docs/architecture/seeing-and-acting.md "The one-brain loop"). The one brain follows the open Project.
  - Its binding is the studio connection (`workspace-bind`), not a Project, so each tool's pin is that studio's
    report now.
  - On a switch it is told the new Project (`App._session_event`'s recap); its brief is refreshed at once and its
    marks on the Project left are cleared (`Partner.project_changed`). GPT-Live is told the same recap and its
    delegations take a fresh pin, so the studio no longer asks the user to end Talk before switching Projects or
    inputs, whichever voice is on.
  - A Tab tool (show_measurements, tabs, present) that fails while the studio has not yet shown the open Project's
    Tabs waits for them (`LANDING_WAIT_S`, its progress line saying so) and is tried once more. If they never show,
    that is said plainly (`partner_tools.py`; tests/test_voice_follows_project.py).
- **Lines nobody asked for (the one brain's proactive events)** all go on one bus (`voice/events.py`), the brain's
  (`events.bus_of(brain)`, handed with `Brain.said` to every Talk session by `voice/route.py`), so a result still owed
  when Talk ends, or one that lands after, is said by the next session. Publishers: `Partner.happened(event, text,
  ref=, delivery=, marks=, frame=)` → `RealtimeSession.proactive` (a researcher finished or failed, a long tool done,
  a capability turned on, the watcher's events: `orchestrator/view_watch.py`: the slide settled on a new place, the
  focus drifted, counted cells no longer where they were counted; a division is an interface with no detector yet),
  `deliver` (a job's result, a background tool's, a job's question or cancellation) and `inject` (the user's own
  actions as `user-action`, chat notes). The text is facts as JSON (what was measured, when it happened, the ids to
  point at), never words to read out: the voice model decides how to say them, in one sentence. Each kind has one
  route in `events.KINDS`; the publisher may give its own (`delivery`: the watcher's per WATCH choice,
  `view_watch.DELIVERY`): "context" (a silent system item; a burst coalesced), "idle" (said in a pause: no key held,
  nothing said or pending, nobody spoke for 1.5 s; results within 120 ms said together) or "interrupt" (said as soon
  as the line playing now ends, without waiting for a pause: a line the voice starts is finished or yielded to the
  user, never cut by its own next one, Will 29 Oct; never over the user's answer); the user's quieter or "let me find things"
  makes the watcher's all context. A result is owed until heard (the end of its audio); cut off, it comes back once
  ("as I started to say…"); cut twice it is retired, and `Partner.retired` files one Activity line. Progress is its
  own channel, spaced, never owed: a job (think, a slow tool) gets a line once the voice has been quiet 6 s, and a
  second only when what it is doing has changed; the user's own tool round gets at most one, after 5 s of silence,
  let go unheard if the result lands first and never cut once playing (seeing-and-acting.md "The one-brain loop").
  The voice's first line as Talk connects (`opener`: what is on the stage and the obvious next thing, pointed at) is
  an idle event too, once per Talk session, withdrawn if the user speaks or types first. Detector state is never pushed into the prompt every turn: only events, and only
  by their rule (a standing note would be an option that must beat "model alone"). GPT-Live has no `happened`, so it
  gets none of them; every watcher event is still logged (`watch-event`).
- **What the voice says of a result is its receipt** (`voice/hands.py`: verified, failed, uncertain, running): a
  background tool's (`reference_photo`, `retrieve_page`) and a job's go with their facts to `deliver`, and the
  out-of-band wording follows `openai_realtime.RECEIPT_RULES` ("done" only for verified), falling back to the
  receipt's own `said`. `reference_photo` in a native workspace is two actions: the lookup as background work (no UI
  lease, no screen lock), then its window opened with `present` under a lease of its own, so a takeover meanwhile
  leaves no window opened without a fresh look.
- **`think` is a job** (`voice/jobs.py`, partner_tools `think`, `jobs`, `update_job`, `cancel_job`): queued, running,
  cancelling, then done, failed or cancelled, with no cap on how many run. Jobs live on the hub (`jobs_of`), not on a
  Talk session. Interrupting speech, a takeover or Talk ending never cancels one; `cancel_job` does, and "cancelled"
  is said only from the job's own `job-cancelled` event once its task has stopped. A follow-up goes to the job
  (`update_job` → `ThinkTask.updates`, read at its next step; a job waiting on its question or one that ended runs
  again under the same id with what it did, `ThinkTask.previous`). A job's question, and a permission Hands asked
  during it with its approval id, is a `job-question` with the job's id; the voice relays only those, and the user's
  answer comes back through `update_job` (think's tools take `approved`). Done is decided from the receipts of its
  acting calls (`jobs.outcome`): done only when every one ended verified (or it made none and answered); the
  backend's `checked` is advisory, and what is not verified goes with the result. Researchers are jobs too:
  `update_job` and `cancel_job` reach one through `researcher_note` and `cancel_researcher` (Hands, their records).
  Each think job files one Activity line, and `project_state.jobs` numbers researchers, think jobs and hand-offs
  (below) together by when each started, in the brief and the `resources` answer, so "the second one" resolves the
  same way everywhere.
- **Hand-offs as the chat shows them** (`voice/handoffs.py`; the studio's `handoffs.ts`). With PARTNER_ACTS "brain", each
  `do_in_hooke` files a `handoff` line at once (working, titled by the voice's required `label`), `long` past
  TOOL_WAIT_S, then its state from the brain's own turn, before the act-then-look: done (an answer, no failed step),
  started (it started a researcher), uncertain (a step's receipt says its change was sent but not observed on screen:
  `handoffs.unconfirmed`, never "failed"), failed with its reason, or settling while a late step runs. `Dialogue.flying`
  holds those still running, so a replay shows one an app restart cut off as unknown. The voice is told the row shows a
  long one (`STILL_HANDED_OFF`) and, in PARTNER_ACTING_BRAIN, to say one short line for a new request only and each
  outcome once. **A later step** (`then`, `voice/followups.py`): the voice cannot act when work lands (its unasked
  lines have no tools), so Hooke keeps the step with the researcher the hand-off started (a hub-level `research.on_done`
  listener, Talk on or not), hands it to the brain as a hand-off of its own when that researcher lands, and says the
  researcher's result and the step's outcome once, together (`Partner._research_done` defers to it); a researcher that
  did not finish leaves it undone, and that is said. With no researcher started it runs right after the first part;
  otherwise the voice is told nothing keeps it (`not_kept`), so it is never promised. Kept in memory only.
- **A hand-off is a job** (`voice/jobs.py` `HandoffJob`, kind "hooke"; the spec's 4.8 horizontal 1): each
  `do_in_hooke` (and each kept later step) is Hooke's own work in progress under its row's id, in the hub's jobs beside
  think jobs and researchers (`project_state.jobs`, "#3 Hooke's work (h3-…): running" in the brief and `resources`).
  - **States:** running from the call; then done, failed or uncertain (a change sent but not confirmed on screen, never
    done) from its row (`Jobs.handoff_changed`); a row the brain answered while steps still ran keeps it running until
    those steps end (`Registry.finished` → `Jobs.run_finished`), and its row ends with them (`Handoffs.settled`).
  - **Activity:** one line by its label: "Assistant: <label>: working", then done, did not work, "sent, but not
    confirmed on screen" or stopped.
  - **The chat's working signal**, as a typed Send's: `{type:"thinking", on:true, id, channel:"voice", job, label}`
    at the call, `{…, on:false, state}` when the brain's turn on it returns (or once a stop has ended), `id` the row's
    and the job's; typed ones carry no id.
  - **Stopped** by the voice's `stop_work(id?)` (brain mode; the newest running hand-off when no id, a number from the
    brief too): its brain turn and every step it started are cancelled (it runs under its own `user_stop.Stop`; the
    `ActionRunner` registers its sequence and each step's run there, `Stop.stop_steps`), its ticket's later effects are
    called off, and "Stopped." is said only once they have ended within `STOP_WAIT_S`; otherwise "being stopped", and
    the stop is told once when it lands (`job-cancelled`). Work that had finished before the stop reached it says so.
    A cancelled step is stopped, never a failure. Its row ends `unknown` with `stopped`. Researchers are still stopped
    through `do_in_hooke` (the brain's `cancel_researcher`). Tests: `tests/test_one_way_in.py`.
- **The voice's state on screen** (`voice/presence.py`; W3): the one brain's session says its own phase
  (`RealtimeSession.phase`: listening, heard, thinking from the released turn to its first words and while a tool of its
  own runs, speaking, quiet) and `VoicePresence` adds Hooke's work in flight (this session's open hand-offs, the hub's
  think jobs, a hand-off kept as a job under its row's key), sending the studio `{type:"voice-state", state, n, working,
  seq, at, turn}` at each change: into a state at once, out of an active one after SETTLE_S, read again every TICK_S.
  A hand-off's wait is Hooke working, never the voice thinking. Each change is logged (`partner-state`) for the journeys'
  Alive line; tests/test_voice_presence.py.
- **Where things go and where they are** (docs/architecture/on-screen.md "The workspace model", "Placing things"):
  the brain's screen words say what each Tab holds by kind and what is drawn on the specimen (`context.holds_words`,
  `overlay_words`, `other_tabs_words`), so "which Tab has the measurements?" is read from kinds (`tabs list
  kind=table`). Every tool that places something takes the one vocabulary (`placement.py`: new-tab, beside, overlay,
  window, replace, close, only; the words in each tool's description), and its receipt says where the screen shows it
  ('Tab "Cell results" in front holds the cells' outlines on their capture, with its table beside them'), never an
  internal word. A video or web page from the web is `show_web` (a checked search, a sandboxed window).
- **A document of the findings** (`write_document`, `brain/toolbox/documents.py`, `orchestrator/documents.py`; W5):
  "write out the interesting findings so far, save that doc, and open it in a new tab" is one tool. Code gives the
  brain's own model the Project's records (ids, what made them, their numbers; researchers' and the web's words in the
  untrusted block), the model writes the prose with each paragraph's citations, and code keeps only paragraphs citing a
  record of this Project before saving it under Write-ups. Opened when asked, in a Tab of its own by default, done once
  the studio shows it (docs/architecture/on-screen.md "Documents"). Only on an explicit request (a large deliverable,
  below); a deep report that reads the web stays a researcher's.
- Keep memory lean: each request carries the eyepiece crop, a compact scene table and the
  conversation window. No long-term store or retrieval step; the common "what's that?" is
  one model call.
- One memory thread per conversation (see Sessions): the window is the last 8 exchanges
  (notes about what the app did ride along, at most 4); a spoken request is one turn with its
  pieces joined. Every Talk press opens a fresh GPT-Live session, so it starts knowing
  `Brain.voice_recap()` (given as its `session.input` history, as OpenAI's Live guide says):
  the conversation's summary and latest exchanges, the day's digest, the researchers' work and
  what is on screen, plus anything that happened while it was off; it is given it again when the
  conversation changes. Stopping Talk never cancels delegated work. View notes go out only when nobody has
  spoken for a second, and speech nobody prompted is logged (`voice-unprompted`).
  Passive workspace notes use Screen's display-report lifetime; action admission
  retains its stricter Send pin. Provider `session.closed` confirms finalization
  and final usage even if later transport cleanup fails; log that cleanup separately
  and do not hang up an already finalized WebRTC call.
- A spoken request has one lifecycle, and the voice is spoken to only at its transitions (OpenAI's live-delegation
  guide: commentary for what the user should hear, background progress as thinking on the delegation; nothing marks
  a delegation done, so the app keeps each request's state). Live 2026-10-11, without it, a still-running step was
  said twice ("Still pulling that photo. Still at it.") and a failed photo, left as silent context, became an
  improvised "I found the photo, it's on your screen".
  - **One identity per request:** a spoken request's id is its ticket's admission (`request.admission.request_id`).
    It travels with every hand-off of it (`SpokenRequests.request_for(delegation_id)`), its findings (`requestId`), the
    narrator's lines (`Line.request_id`) and each step's late result (`Line.outcomes`: `action:<action id>`; a model's
    own late answer about it is `answer:action:<action id>`). What the voice has said is kept by those identities in
    one record, `narrator.Said` (`Brain.said`, shared by every Talk session through `voice/route.py`), so an answer or
    outcome is said once whichever path reports it: the hand-off's findings, the voice handing the same request over
    again, a replayed delegation, a raw late result already said, or a new Talk session. Never by comparing words.
    An identity is marked said only once it was said (ADR 0006: silence is worse than saying it twice): the
    narrator marks it when the voice is heard starting to say it, a hand-off when its send returned; what was only told
    (a request the app did without a hand-off, `speech.outcome_line`, failures included), a send that failed, or one
    whose Talk session closed first, is not said, so the hand-off or the next session says it. A raw result said before
    never silences the model's own late answer (it is said); a model answer arriving while its step's raw facts still
    wait in the narrator's queue is said in their place (live 2026-10-28: one outline's outcome said three times).
  - **Acknowledged, once:** by the voice itself. A question's answer is said as soon as the brain has written it
    (`Brain.turn(draft=)`); a draft offered with steps still to run (`claims.Draft.pending`) is said with those steps
    named as not done yet, so only what it says of them waits for their outcome, which the model judges (live
    2026-10-28: "I've brought up the measurements table" before any tool ran), and its answer is never held back. A
    request to act's early answer is told, not said (`SpokenRequests._answer`; the user's decision, 2026-10-11:
    "Sure", the card, then "There it is").
  - **Spoken and facts apart:** `openai_live.split_findings` says apart what the voice is asked to say (`spoken`,
    commentary) and what only the model should know (`facts`, thinking on the delegation): steps still running, and
    "nothing was outlined", a fact only for a request that asked for outlines (its look's `targets`, or a step that
    `produces.py` says makes them) and got none; then the voice says it once, with the next useful thing to try. A
    failed step's own `next` goes with its "Could not". Live 2026-10-28, "Nothing's outlined" was said after a table, a
    researcher, a status and a tab request.
  - **Not over the user:** a hand-off's findings wait while the user's words are still coming
    (`OpenAILiveSession._hold_for_user`, at most `HOLD_FOR_USER_S`), and a failure from the narrator waits only on the
    same time-bounded `user_talking`; what the user says meanwhile never turns them into silent context. The route gives
    the session its spoken requests for identity only (`session.requests`), not as `spoken`, so the narrator's
    `newest` check stays off for late results, as before Part 2.5 (any later words, a cough or a "thanks", counted as
    newer). GPT-Live's
    own turn-taking has no settings (OpenAI's Live reference and guides, checked 2026-10-28: no `turn_detection`, VAD,
    `eagerness` or `interrupt_response` for `gpt-live-1`; the module docstring of `voice/openai_live.py` quotes them);
    yielding is shaped only by the prompt's Interruption section, which follows OpenAI's prompting guide ("Keep
    listening while the user pauses to think"). The app's `QUIET_TURN_END_S` and `SPEECH_PEAK` stay as they were: they
    decide only captions, `interrupted` and idleness.
  - **Still working:** findings with nothing but steps running go as thinking on the delegation, said or not before
    (`openai_live.only_in_progress`).
  - **A wait:** a late step whose progress changes (a model loading, the studio out of view:
    `ToolRun.report_progress`, `Registry.progressed`, `Announcer.progressed`) updates its chat row and is told to the
    voice silently, never said.
  - **The outcome, once:** a late step's outcome on a spoken request is said on its delegation from its facts, in the
    voice's own words (`Announcer.late_tool`, narrator `notify`).
  - The voice prompt says, as the guide recommends, "Do not guess the result while waiting".
  - A stand-in voice never speaks on its own, so what GPT-Live says is checked by replaying the user's words through
    the real one on a second stack (`docs/bench/voice-replay.md`).
- Voice and chat show a request the same way: a spoken request's steps are filed into the chat as the typed path
  files them, once each by identity (`SpokenRequests._file_steps`), so their rows, "Working...", cards and late
  updates are the same; the spoken transcript is its words. `tests/test_voice_chat_parity.py` checks every
  capability.
- Effect tools run after the answer is shown and are awaited up to `inline_ms`; slower ones
  report late (chat update, or spoken on the original delegation). Only query tools trigger
  another model call, at most 3 per turn. A request's steps run in order, each on the board the
  one before left (`brain/actions.py`): steps that take things off the screen (clear, close) go
  first, and the look's outlines are made after them, never before (a clear in the same answer
  once removed them); a clear never removes what a later step of the same request names (`Steps.planned`,
  `named_later`; live 2026-10-11 16:36:22: "clear, then highlight o20" removed o20, and the highlight failed). A step that needs a new look (a highlight of something seen before the
  slide moved) waits for the request's look; the steps beside it are done at once
  (`Brain.split_for_look`). "What is on screen" is one state, `board.py` (CONTEXT.md "Board"):
  every tool resolves "that", "the second one" or "the one I clicked" against it when it runs, and "that" and
  "the one I clicked" mean what the request's own steps pointed at, else what was pointed at when the user made
  the request (`board.WhenAsked` on the request's `ToolContext`: typed, as the turn starts; by voice, when the
  words' final transcript came; the selection, the cells layer's selected cell, the magnifier); a selection or
  click made while it was being answered is not what was meant, and a selection gone since is said (the
  partner's Amendment 1, C2). Of the selection and a click just before the request, the newer is meant
  (`board.pointed_last`; a click on empty space outlines without selecting), for "that" and for a researcher
  started on "that"; with nothing named, an open magnifier is what it showed when the user asked,
  and the brain and the fast pass are told each outline's box, size, status and who made it.
  Finding things is `orchestrator/finder/` (CONTEXT.md "Finder"; tool `find`): by name or look,
  anywhere or inside an outline, one or every one; inside an outline it shows the vision model
  the whole view with the outline marked plus an enlarged close-up (`Agent.look_in`), and the
  outline it searched in is never replaced, by `find` or by the look's own pointing
  (`agent.point` keeps an outline a new target lies inside). "Find all like this one" (`find`
  like=) takes SAM 3.1's matches for the example's box (fal, `return_multiple_masks`; its text
  prompts find nothing on our slides, so the example is what works), falling back to the vision
  model's boxes. `find` by="concept" (M3.1) gives SAM 3.1 the words themselves, no example: every match,
  named by the words (its record says so), "Not sure" with no confidence shown; an explicit choice, never replaced by
  the vision model's boxes; offered, not the default, since words found nothing on our slides (`docs/bench/phase0.md`). `find` by="cells" (the model chooses it): the cell finder outlines every cell and the model
  lists the numbers that are the thing asked for (blood smear bench: every red cell 0.90 found, against
  0.60 by looking; not for tiny things: `docs/bench/finding.md`). `count` counts what is outlined, cells included, and finds and outlines them
  first when nothing is; `outline_cells` is the cell finder (option CELLS) as a tool. The cell finder
  is Cellpose-SAM (the default) or micro-sam's light-microscopy model (`micro-sam-lm`: red cells 0.79
  found against 0.90, but ~0.7 s against several, and some platelets; it is also an OUTLINE option for
  clicks, platelets 8/10 against EdgeTAM's 2/10 but ~0.5 s a new frame). micro-sam runs in its own
  Python (`MICROMIND_MICROSAM_PYTHON`, `services/noncommercial/README.md`), a child of the GPU worker
  speaking `microsam_server.py`'s wire; its weights, like Cellpose's, were trained on non-commercial
  data, so the commercial edition's cell finder is EdgeTAM's automatic masks (`edgetam-automask`, open
  weights; not benchmarked on cells yet). Whatever finds them, the cells are a population (evidence),
  not outlines: below.
- Cells are evidence, not outlines (`orchestrator/evidence.py`, `population.py`; contracts 3, 8, 9 of
  `docs/design/2026-10-06-captures-operations-evidence.md`). `outline_cells` keeps a population (`e3`):
  each member (`c12`, never reused in a day) is the specialist's own mask, measured by app code
  (`measure.py`: area, holes, Kulpa perimeter, Feret, elongation, roundness, solidity, intensity,
  edge-cut), with what made it. The layer shows a member over the live view only after its own pixels
  match where the slide's move puts it (half resolution, every 0.3 s while shown); the rest stay on
  their capture. `query_population` and `count` read it (never segmenting); `highlight` selects members
  by measured columns (`of`, `by`, `top`, `where`, `outliers`; "@1" for what step 1 made) and promotes
  them to outlines (`made_by.how` "measured"), shown on their captured view in the magnifier where not
  verified live. A selection sorted by a column says the values behind it (`population.ranking_words`, at most 8:
  the chosen, the next few not chosen, and a tie at the cut, "c1 ties c4"), so the model judges the cut from facts
  (live 2026-10-11: "the round ones" were called the two roundest while two more were as round); a range (`where`)
  says the values inside it and the nearest outside (`population.range_words`), and highlight's `where` tells the
  models what roundness measures (`population.ROUNDNESS`: a circle about 1, a 2:1 oval 0.86-0.92, 3:1 about 0.7,
  measured on drawn shapes), so "the round ones" are a range, not a top n. The brain's board
  lists at most 5 evidence lines; a click inside a shown member selects it.
- Never claim what did not happen (ADR 0006). Answers are written before tools run, so they say what is
  being done, never that it is done; when a tool fails or nothing could be outlined,
  `Brain._reconcile` rewrites the answer to say so (and a step still running loses any claim
  that it is done), the voice is told what is done, what is still being done and what failed
  (`brain/claims.py`), never only what worked, failures are always shown and spoken, and
  a highlight that misses (the slide moved, nothing outlinable) makes the app look again
  rather than give up. When an effect of the latest plan fails and only the call reserved for the
  outcome is left, that call is the outcome round over the receipts and the notes: it says what
  happened or adds steps for what is unfinished (a "target N" the answer did not outline says "find
  it first"). The voice hears "Correction to what you said" only when receipts contradict what it
  said, never for a rewording. Every acting answer is written again from its receipts and every plan the
  fast pass carried out goes back to a model (ANSWER_CHECK "every-request", COMPLETION "every-plan", since
  2026-10-09); writing it again only on a contradiction (a failed, unfinished or unshown step, nothing
  outlined, a query no answer saw, a saved resource opened or made, a request that did nothing) and asking
  again only after a failure or a query ("contradicted", "unfinished") measured better on the acting bench's
  live cases (`docs/bench/acting.md`) and become the defaults once the tests written for the other rounds are
  reviewed. When the three calls are spent and no model has said what happened, the answer keeps one call
  of its own (`RequestAdmission.reserve_last_word`), so a request ends with the model's words. Failures are said in the answer, never a card (ADR 0006's amendment): each problem the rewrite is told
  has an id, the reply lists those it states, and a confirmed omission is written once more under its own one-time
  allowance (`RequestAdmission.reserve_recheck`; `answer-recheck` records); a model choice is written from its
  receipt too, with an id of its own (`c1`) the reply states as it states a problem, said with what it applies to ("this
  request only", "from now on": `claims.choice_scopes`); a step tried again successfully is not a failure
  (`claims.unresolved`). The completion round is given the same ids and `stated` contract (`loop.stated_ids`,
  `stated_block`: one helper for both rounds), and its answer stands, with no call after it, only when it states every
  one; otherwise it is written again from the receipts (journeys round 2: writing every choice turn again cost one more
  call, p50 4.4 → 5.6 s on those turns; stated by the completion round, p50 4.4 s and every one answered at 2 calls but
  one), also when it was the third call, then on the answer's own last call (live 2026-10-11 16:36:22: the outcome
  round's "re-highlighted the vascular bundle" stood unchecked with its highlight failed; the outcome round answers on
  the notes and is told so: no camera crop in its facts). If that call then fails or times out, the completion round's answer to the
  same settled receipts is kept, the model's grounded words, rather than no prose (`answer-recheck` with `unchecked`
  and the reason; the "!" stays when its steps hold an unresolved failure). A request whose steps ran never ends with
  no words: when its receipts leave nothing to tell beyond its answer, as ANSWER_CHECK "contradicted" judges it (no
  problem or choice id, nothing still running, no limitation, no query result to interpret, no saved resource whose
  identity the rewrite checks, no researcher changed), its own answer is kept the same way (live 2026-10-11 16:49:38: the eye
  outlined and shown, the rewrite dropped as `unsupported-input`, nothing said); everywhere else an unavailable final
  model still leaves no prose (ADR 0006's amendment). An answer names only models its receipts name: in a request whose
  steps ran, a model it names (names.py's model names, matched exactly: `claims.models_named`) that no step's summary or `ToolRun.models`, no
  default of a role the request chose for, and none of its own looks or notes names is a problem with an id for the
  rewrite, also when the rewrite's own words add it (`claims.model_problems`; live 16:28:55: "dissecting the stoma
  with the DINO model", no step used one). An answer written while a step ran is never left saying "still in
  progress" of a step that finished: when every such step finished while it was written, it is written again from
  the settled receipts on the call the late answer would have used (`progress-settled`; 16:28:55, 16:42:22,
  16:42:57). How each
  outline came to be is one account, read from its own record (`known.chain`, `board.how_made`): found by
  (the user's click or drag at a time, the vision model, the running look, a match, the cell finder), drawn by
  (with a fallback or a request's choice said), checked by (the outline check's verdict, and what redrew it),
  named by, followed by (the follower's `followed_by` stamp, its shape kept or not). The voice's rows always
  carry it; the brain's own rows only with option ROW_PROVENANCE "on" (off until the benches show it loses
  nothing); the fast pass's never. `resources(id="o3")` always gives it first, so "which model did you use?" is
  answered from the record (live 2026-10-11: "our built-in vision model" was invented). A new highlight replaces the app's earlier ones unless the user says
  "also", never what its own request just outlined; outlines the user made are never removed by the app.
  - Names say how sure they are: when the model says it is not sure, a name is "Not sure" and
    the view shows its broad group with "maybe X or Y" (`agent.identify_reading`). The bench's
    calibration (below 0.9 right 45% of the time, `docs/bench/naming.md`) is told to the model as
    data; code never overrides its call (`scene.status_for` cuts only when a bench asks).
  - Only the user makes a name "Confirmed".
  - **One labeler** (option NAMING "one", Will's decision of 2026-10-12): every outline the app draws (a highlight, a
    find, the look's pointing, a promoted cell) is named by the same step as a tapped one (`agent.identify`, through
    `tools.add_outline` → `tools.name_like_a_tap` → `commands.name_drawn`), on its own outside the request that drew
    it, at most 3 at once. Until the model answers, the outline keeps the name it was drawn with (`identify(quiet=True)`:
    never "Looking...", so a highlight is no slower to read); if naming fails it keeps that name. Live that morning
    "highlight the ball in center" kept the running look's quick name while every tap was named by the naming model.
    "per-path" keeps the old way (the look's or the request's name).
  - How do you know? (`orchestrator/known.py`; partner §1a): each path that names an outline keeps how on
    it (`SceneObject.naming`): naming a click its model, view, notes, alternatives and the context it
    was told (a cut one marked); a SAM 3.1 match its example and mask score (not a naming confidence);
    a promoted cell its population and member (its name the request's); the look's outlines the look
    and its model; a repair by the outline check; every rename from, to, by whom. `resources(id="o3")` reads it with what outlined it, how sure (the naming bench's calibration
    only for the model's own name), where it is and its size with the scale's source; a path that keeps
    nothing says so, and a relabel no rename explains is said as the app's. Its first line is the chain
    (`known.chain`: each model in order). A read-only lookup, not an index entry; kept with the outline
    while it exists; the persisted named-outline entry keeps only which model drew it (`seen.Kept.made_by`,
    the index's `madeBy.implementation`)
    (a dataset being recorded keeps naming's log record, which holds less: no sure name's alternatives,
    no scale words or measured numbers). An unvoted blind arm says only
    that it is shown after the vote; an outline no longer on the board is said to be so.
  - Labels the user teaches (`orchestrator/taught.py`, option TAUGHT): naming an outline ("this is
    a guard cell": `name_outline`, or accepting a correction) keeps its crop and embedding (option
    EMBED, below; heavy GPU worker, op `embed`), per day in `MICROMIND_HOME/taught/`. From 3 examples
    of a name, a new outline's closest taught name is told to naming as one line of data and shown
    in the studio as "your label: X (0.82)"; it is never written into the label (`docs/bench/taught.md`).
  - Seen before today (`orchestrator/seen.py`, option SEEN): every outline named today (clicks,
    highlights, finder results; not parts or blind arms) is kept with the same embedding (one per
    outline, `TaughtLabels.object_vectors`, embedded in the background as it appears), its name,
    who named it, time, conversation and crop (logs/crops/), per day in `MICROMIND_HOME/seen/`, at
    most 500 a day and 10 per conversation and name. Naming a click is told the most alike one named
    earlier today, from 0.93 (same kind 92-94% of the time on the bench, recall 6-17%), as one
    line of data in the past tense; `seen_before` lists them (`docs/bench/seen.md`).
  - The picture model of both (option EMBED, `gpu_worker/embed.py`): DINOv2 small (default) or
    BioCLIP 2 (MIT, open_clip; `open-clip-torch` in the gpu group). Every vector carries the model
    that made it and two models' vectors are never compared; after a switch the day's taught
    examples and kept outlines are embedded again from their crops (`TaughtLabels.refresh`,
    `SeenToday.refresh`). Each model has its own threshold, list floor and calibration line
    (`seen.THRESHOLDS`, `taught.CALIBRATIONS`): their cosines are on different scales. BioCLIP 2
    did not win on our cells (nearest taught name 68% vs 72%, naming with the line a tie, seen
    91-92% at 0.96 vs 92-94% at 0.93 with less recall, 72 vs 13 ms a crop), and its zero-shot names
    were at chance, so naming gets no BioCLIP name fact (`docs/bench/naming.md`, `seen.md`).
    DINOv3 small (M3.5; Meta's DINOv3 licence, tier "terms", gated weights: HF_TOKEN) is a third option, not yet
    benched: a model with no measured threshold matches nothing in `seen` and `taught` says it is uncalibrated,
    never another model's numbers. Without HF_TOKEN it is shown as unavailable; a load that fails is not tried
    again for 10 minutes (each try stalls the heavy worker).
  - Linking two views' cells (operation `link`, M3.5): Trackastra 0.5.6 (`make trackastra-setup`, CPU) links
    the members of two populations of the same specimen, each link Trackastra's guess (kind same-object, basis
    trackastra) with its own score. Only views the slide's measured move relates (one epoch, the same picture size,
    eyepiece scale and objective), compared where they overlap with the second placed by that move, and only once
    the two pictures are measured to line up there (`operations.line_up`: within 10 px, inside both eyepiece
    circles); otherwise, or when a mask can't be read back, it can't tell and says why. Its masks are read for the maps without being kept,
    and the population is never changed from its thread. Tier noncommercial (`services/noncommercial/trackastra_link.py`):
    the terms of its weights' training data are not stated, and its install brings Gurobi (proprietary, never
    called: linking is greedy). An implementation for session knowledge's identity (K2); "not installed here" until
    set up.
  - Medical names, zero-shot (`orchestrator/zeroshot.py`, option ZEROSHOT; MedSigLIP 448, Health AI
    Developer Foundations terms, tier "terms"): naming a click can be told MedSigLIP's ranking of a
    candidate list of names for its crop (heavy GPU worker, op `zeroshot`, float16, ~0.3 s a crop;
    waited for at most 1.5 s beside TAUGHT and SEEN), with what its first name has been worth, as one
    line of data; the model decides the name. The list comes from the subject ("medsiglip-subject":
    blood smear or Pap smear names, nothing for other specimens) or is every list ("medsiglip-always").
    Off by default: on the naming bench it did not improve exact blood-cell names (58% -> 59-61%, moving
    them both ways: monocytes up, neutrophils 80% -> 53%), only coarse ones (`docs/bench/naming.md`).
  - Sizes come only from the app (`orchestrator/scale.py`):
    - **Where the scale comes from:** the objective the user names (studio menu, or by
      voice: `set_objective`, "I'm on 40x") and the eyepiece field number (default 18 mm)
      give µm per pixel of the eyepiece disc, marked as an estimate. A known length
      (`calibrate_scale`, "that micrometer line is 100 µm") measures it for that objective.
    - **What it shows:** outlines carry their size (`size` on the wire), and the studio draws a
      scale bar.
    - **Prompts:** naming and Sight are told the field width, and `measure` answers
      "how big?".
    - **Numbers:** `measure`, `count`, `track_motion` and `where_is` also log their values as a
      `measurement` record (`brain/toolbox/common.log_measurement`). The record has units, the
      scale and where it came from, and the frame with its bridge `seq`. It is in a dataset as
      numbers (`docs/datasets.md`).
    - **Without a scale:** no size is stated, and models never state sizes themselves. Pixel measurements
      (a population's areas and lengths) only compare and rank things: an answer states no length or area in px
      or px² unless the user asks for pixels (`prompts.MEASUREMENT_BASIS`; live 2026-10-11 "an area of 3,472 px²").
    - **Where each value came from:** the field number is the `default` until someone gives it,
      as `user` with who and when (a day saved before this says `unknown`). The objective keeps
      who set it, and a calibration keeps the length it was made from (`Scale.facts`, for
      datasets). Field numbers 5-30 mm are accepted (a WF25x is about 9-10).
- Corrections of segmented cells (`correct_cells`, M2): only when the user says some are wrong ("that's
  not a cell" about the clicked one, "these two are one cell", "undo that"). Each is a new revision of the
  population (`population.correct`, `Evidence.revise`): revision 0, the model's own, never changes and its
  masks stay saved; a merged cell is the union of their masks, measured again; the count follows; the
  app's outlines of removed cells are taken off.
- Outlines the app draws are checked after they are drawn (`orchestrator/outline_check.py`,
  option OUTLINE_CHECK, default "fix"; nothing waits for it): the fast model is shown the view
  with the outline and a close-up, and the name it is meant to be, and says whether it fits, is
  too big, too small or the wrong thing, with a box when it does not fit; the app outlines again
  from that box on the same frame and keeps the new outline (same id, its template renewed) only
  if a second check says it fits. It draws again with the outline's own model, or with another (option
  OUTLINE_REPAIR: SAM 2.1 tiny, SAM 3.1's tracker or micro-sam; each fit 6-7 of EdgeTAM's 19 misses on the
  outline-fit bench, so the default stays the outline's own model). The user's own outlines are never second-guessed. What the check said
  is kept on the outline (`SceneObject.checked`: its model and verdict; when it was redrawn, what drew it
  first), and a redraw's own made-by becomes the outline's (`made_by`, with `repairOf`). A verdict that it does not
  fit stands when nothing replaced it (check only, no box, nothing redrawn, the redraw not fitting either, or a fix that
  would cover another outline): it is sent as the outline's `fit`, the studio draws the outline as uncertain (the
  "Not sure" dash) with "may not fit: <why>" under its label, and Activity has a line (`OutlineCheck._unresolved`;
  live 2026-10-11, 9 of 18 outlines the check called wrong stayed on screen as if they fit). Its output cap leaves
  thinking a budget of its own (`CHECK_THINKING_TOKENS`; at 200 in all, 3 of 7 live checks on 2026-10-11 were
  cut off and read "unclear"), and a reply still cut off is said to be. Every check
  is logged ("outline-check", with its `model` and what redrew it); `bench/outline_check_bench.py` measures it (30/32, no fitting
  outline called wrong, 19/24 wrong ones recovered, on gemini-3.5-flash).
- How the voice's audio travels is option VOICELINK (`voice/openai_live.py`): "relay" (the retained option,
  browser -> app -> OpenAI over WebSockets) or "webrtc" (OpenAI's recommended browser transport:
  the browser and OpenAI exchange audio directly; the app makes the session from the browser's SDP
  offer, keeps the API key, gives the browser's data channel no events, and listens and speaks on
  a trusted sideband with the same transcripts, hand-offs and commentary). WebRTC is the default
  after native media negotiation in ticket 64; explicitly saved relay choices remain. Native
  quality verification remains separate (`make voice-report`; `voice-session` logs the link).
- Live never waits on deep work. Voice must answer at once even while a researcher runs
  for 20 s or 20 minutes: each GPT-Live delegation is its own task, nothing is locked
  across turns, long work (researchers) runs in its own process, and its progress and
  results return later on the delegation id that started it (OpenAI's GPT-Live guidance:
  live speech and delegated work continue independently). Nothing slow runs on the event loop: a GPU
  worker's requests are pickled and written by its writer thread (a worker slow to drain its pipe, starved
  or loading, never holds the loop; one given up before it was written is never sent), its replies read and
  its hang checked on threads of their own (`orchestrator/gpu_client.py`), and the follower decodes and
  places in a thread (`objtrack.py`). A model loading is said, never taken for a hang (Running, above).
- Tools act only when the user asks or it clearly helps. Large deliverables (video, reports,
  lessons, deep research) run only on an explicit request.
- Dissect's result is its own view: the object and its named parts drawn by the app on the frame they were found in
  (`Dissector._picture`, `snapshot.draw`, parts thin and pale inside their object), kept as its result's picture and,
  in a native workspace, opened in a window of its own ("stoma · dissected parts"). It is independent of any live
  magnification of the same object, closed on its own and opened again from Made in this session. A close of the magnifier says
  what it showed (`view.shown_words`), and closed windows are listed apart from what is on screen
  (`context._screen` "closedNotOnScreen"): live 2026-10-11, "keep the dissected view, close live magnification"
  closed both and the answer said it had kept it.
- Long GPU work (dissect regions) yields between chunks so clicks go first; anything that
  cannot yield runs on the second, "heavy" GPU worker. Keep click p95 under 300 ms while
  a dissect runs (`dissect-clicks` scenario).
- The `tool{name, args}` WebSocket command runs one tool directly, for tests and the
  harness; it is not a user feature.

Voice boundary diagnostics (`voice/diagnostics.py`) use the existing Activity and
execution stores. Fixed stage identifiers distinguish browser reports from service
observations and retain one correlation ID; duplicate stages are bounded. No audio,
transcript, credential, URL or client-supplied error string enters these new records.
Known credit/access/rate-limit refusals retain fixed recovery text, never arbitrary
provider text. Failure remains failed after microphone updates or End; the Talk
control preserves the actual provider refusal when the socket closes. Microphone
audio is not sent before provider readiness, and pre-ready speech is not replayed.
The diagnostic export aliases correlation IDs and preserves stages/timings. Existing
private conversation logs keep their established policy.

Voice response delivery exceptions produce a retained failed Activity item and an explicit
reconnect message. Only the exception type enters the service log; provider payloads are
not copied into diagnostics. Session cleanup cannot overwrite that failed outcome.

GPT-Live captions now reach every Studio through the same journaled `lineId`, including
the talking Studio. Raw voice events carry `journaled` and update status only. Input
fragments are shown immediately; final input still drives the existing spoken-request
admission once. Provider event IDs suppress transport replays, never repeated words.
Input/output captions keep independent grouping using provider start/end timestamps
(1.2 seconds separates groups), preserve text fragments, and retain their first timestamp
for ordering and reopen. Audio silence and packet arrival do not close these captions.

Live context appends obey the provider's per-event 500-token maximum. The shared
append helper frames text by a conservative UTF-8 byte bound, preserving all text
without downloading a tokenizer. Voice context names its captured owning Project
separately from the conversation; prompt goldens intentionally include that name.

Timed Live captions checkpoint the final quiet suffix without inventing a turn
boundary. A checkpoint updates the same shared conversation turn, in its original
Project, so typed follow-ups can read the spoken answer before Talk ends. Context
change detection compares workspace meaning, not each pin's changing ID/sequence;
unchanged reports must not repeatedly inject the same observation into Live.

Provider shutdown waits for session.closed (and final usage) inside the existing
shutdown budget. A close request sent without acknowledgement is unconfirmed;
WebRTC hangup is attempted even when an earlier close step failed.

Long Live context is sent as ordered, quoted data fragments. A long spoken update
stages those fragments through thinking and requests commentary exactly once, so
transport limits do not turn a single update into repeated speech requests. Raw
SDK framing and logical notification ownership both have regression coverage.

Passive Live context uses Screen's existing display-report lifetime, separately from
the one-second action-admission deadline. A normal heartbeat gap must not repeatedly
announce the unchanged workspace as current/stale. An expired display report still
becomes stale; spoken actions continue to capture and validate their own strict pin.

Passive view notes contain current workspace facts, not conversation history. Prior
exchanges, day summaries and while-off notes enter the startup recap only; replaying
them after selection changes prompted repeated answers in the real Live probe.
A conversation's auto-title or turn count does not change its view identity.

Voice startup records the view identity already supplied in its recap. Ready does
not resend unchanged context; a genuinely changed view, typed note or requested
new look still updates the running session.

`measure_saved_objects` (`toolbox/scientific.py`) describes or runs the built-in saved-label measurement described
in `../AGENTS.md`. Both verbs require explicit operation, Project/session, revision and input, and the original
current Asked/ticket. It never derives a new input from a later window or latest revision. Registry retains the
exact table/output and producer receipt; success says saved, not shown. The user must request mutation, and
ordinary Tools uses the same schema. Scientific-host private-review admission is unaffected.

`retain_population_labels` describes or accepts the explicitly selected saved population revision through
`population_native`, the same operation as Studio's correction controls. Preserve its request identity on retries;
never substitute the latest population or claim that saved inputs are displayed. It exposes the accepted native
`image` and `labels` inputs for `measure_saved_objects`, without rerunning the producer. Unsupported masks or
calibration remain explicit refusals, and changes to saved work still require the requesting conversation's authority.

`compare_retained_methods` uses `method_workflow` for describe/retain/choose, the same operation as saved Data.
Each call retains the original Asked/session and requires exact native references/digests, explicit arm factors,
and either an accepted mask or attributed unsuccessful outcome. Retain preserves its request identity on retry;
Choose names the accepted comparison revision and a rationale. Caller/ticket provenance comes from the transport,
never the request payload. Producing histories can remain unverified, timing is scoped to recorded actions, and
agreement is never accuracy. These built-in comparisons have no model setup or inference path.


Typed Ask may carry one explicit saved scientific reference/digest from its pinned Project/Tab. `scientific_ask`
uses shared retained-input admission and Ticket holds; cancellation releases bytes only after bounded I/O ends.
The existing Agent.look modes, history, provider admission and registry receive its original lossless PNG and
retained-source manifest, without live scale, Sight targets or camera fallback. A saved-input reply cannot
automatically rename the live Stage subject. Old immutable saved revisions are
valid; digest/ownership disagreement refuses. Each such typed turn journals its input even if question text repeats.
Explicit saved pixels take the existing image-aware Agent.look path directly, preserving mode/history/tools; the
notes-only camera fast pass remains unchanged for camera requests and cannot answer instead of seeing an attachment.
`Tool.retained_input` is an explicit handler support contract, default false: saved resource reads, exact declared
capability execution and existing saved measurement/comparison/retention opt in. Other live/scene effects and
researcher handoff refuse instead of borrowing camera pixels. Capability execution binds the attachment and refuses
multiple/different inputs. These native constraints do not change normal camera Ask or Talk.

Vision and FC provider payloads share transport-light PNG/JPEG MIME selection from the admitted bytes;
native PNG is neither mislabeled JPEG nor recompressed. Camera JPEG behavior is unchanged.


Saved-label measurement's model-facing tool takes the exact scientific document ID, accepted revision
and labels input key. Its owning journal/Project is taken from the original conversation capture;
a model never supplies the workspace UUID as authority. Legacy explicit-session calls are accepted
only when they match that same captured owner. This changes no native admission, publication or
cross-Project guard, and does not turn a view attachment into an implicit measurement request.

`show_measurements` reads the captured Project/revision through the shared asynchronous `read_table` path, then opens
that exact table in the window the user's Open in Measurements opens, on the Tab in front or on the Tab `tab` names
(made when none has that title, brought forward, fitted): one action, done only when the originating Studio's screen
report shows the window there, in front, its rows drawn (`table_window.py`; docs/architecture/on-screen.md "One surface
per thing"). Its answer keeps saved, computed, displayed, in front and accepted apart, and one that is not shown says
where the table is saved. Statistics and lazy scientific imports stay in that worker path. A changed request or
Project refuses. A studio without the Project workspace is shown the Measurements page as before.

A Project is started by `new_project` ("create a new project called experiment", "start a new session"); `new_session`
is its earlier name, kept with its words so saved workflows and tool ids run. A Tab is an arrangement inside this
Project (`tabs`), never a new Project; `tabs list` also says what is in front (`workspace_observation.front`). Each
Tab holds the specimen (filling it, floating or hidden), said in the screen facts; `tabs specimen` floats, fills or
hides it (Main's never hides), done once the report shows it (docs/architecture/on-screen.md "A Tab's specimen").

`make_table`, `edit_table`, `analyze` and `make_graph` use the ordinary DataTables and TableDerivatives services.
The original Asked Project, generation and conversation own writes; request cancellation is checked at the shared
publication boundaries. Exact source/table revisions and explicit test/graph settings are required. Caller provenance
comes from the transport, never model arguments. Accepted Follow work retains its ordinary Project owner after the
turn finishes. `read_analysis` pages an exact accepted result with a content digest, without recomputation; saved
numbers, units, exclusions and input identities remain available. Saved never implies displayed.

`find_workflows` reads personal definitions, catalog file capabilities and Project run receipts. `workflow` invokes the shared WorkflowService under the request’s captured Project and actor. Manual capability invocation creates an ordinary frozen one-step run. Review and uncertainty resolution still require the user’s actual decision/evidence.
