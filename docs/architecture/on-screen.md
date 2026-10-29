# What is on screen: one set of verbs, done when the studio shows it

Part of the working context the root `AGENTS.md` maps. "Above" and "below" may mean parts it lists in
other files.

- **The studio reports what it displays** (`apps/studio/src/screen.ts` → `orchestrator/screen.py`).
  - **What a report holds:** the live view, the population layer, the app's outlines, the magnifier, the
    result viewer, the slide map and the details card. Each comes with what it shows, where it is
    (workspace px), its stacking order and its style.
  - **The Data reader** (ticket 66; `apps/studio/src/data-reader.ts`) is in it too, with its item: one surface for the
    reader on the right edge (`document:reader-side`), one for a reader Tab's (`document:reader-tab`) and one for the
    reader on a researcher's Tab beside its conversation (`document:reader-research`, ticket 66 D3), each saying the
    Data item it shows by its exact identity (`shows` its id; `document.resource` its day, id and version), its view
    ("Reader · Bright regions · How it was made"), whether its content has drawn (`state`: loading, rendered or
    failed), its place and whether it has focus. They are document surfaces (`readerSurfaces` in
    `workspace-document-surfaces.ts`, representation `reader` / `reader-tab` / `reader-research`, placement
    `reader:<side|tab|research>:<Tab>`), the
    one structural kind every server version takes for a thing on screen, so the Board's "On screen" lines name what the
    reader shows and "done" stays what the studio shows. Its Show on picture is the `present` verb `show` (or `hide`)
    on the population, confirmed by the report like any other. The title it reports is the item's own only for what
    you and the app made; a researcher's work, a web source or any untrusted entry is said by its kind and maker
    ("Reader · Report · Researcher 3 · Page", `DataReader.reportTitle`), so words a researcher or a web page wrote reach
    the Assistant only inside the untrusted wrapper, never through the report; the item is named by its id.
  - **When it is sent:** whenever any of that changes, and every few seconds.
  - **Who reads it:** the Board reads it as "On screen:" lines.
- **One verb tool, `present`** (show, hide, opacity, colour, fill, edge, emphasise, open, close, move, resize,
  front), for anything on screen and any index entry ("show me the motion one").
  - **Open puts saved work in the Data reader** on the Tab the person is on, the way a person's click on its Data row
    does (ticket 66, D6; `orchestrator/data_reader.py`): every kind Data lists (`screen.PRESENTED_AS` `READER`, a
    document surface; the rest keep their panel or say why not), cell outlines and motion tracks when opened (shown,
    they are drawn on the picture, the reader's Show on picture), and `show_result`'s researcher's work by its Data
    entry. The studio is sent a `reader` message (the item's exact day, id and version, and its entry as Data lists it,
    so an item Data has not caught up with still opens), which it acts on only in the workspace opening it was asked in
    (the Tab strip's pin, as the tabs tool does) through `openInReader`. It is done once the report shows the reader
    with that item and its content drawn (`state` rendered; content still loading after 8 s is said so, a failed one
    is a failure, and nothing shown is "not open in the reader"); a studio in the background is said to show it once in
    view. Close (by its id, `document:reader-side`, "close that" with the reader in front, `close_result` for a
    researcher's work) is done once no reader shows it; a reader Tab closes with its Tab, and the reader does not move.
    A request is answered by the reader of the studio that sent it; a tool run no studio sent (the sim's `tool`) by
    the studio whose report counts. A studio without the reader (no Project Tab strip in its report: an older studio, a
    test's fixture) is shown things as before (`screen.WITHOUT_READER`). Nothing is put back by a showing's Stop here: an
    open in the reader is not a Change the showing owns, and the person closes it with ×.
  - **Colouring:** "colour them by size" colours the cells' layer by a measured column, with a legend.
  - **Emphasising:** "keep the five largest and fade the rest" emphasises a selection.
  - **A verb a kind does not take fails with the reason and what it takes.** `present` only dims the live view, never
    hides it; in a Tab it is the specimen, which `tabs` floats or hides (below).
  - **`set_style` stays** the outlines' own colour and style.
- **One surface per thing: the measurements table** (Part 2.5; `orchestrator/table_window.py`,
  `apps/studio/src/agent-table.ts`). `show_measurements` opens the table in the window a person's Open in Measurements
  opens (`openStudyTable`), never a `#measurements` page (voice log 2026-10-28: that page's `page-opened` answer was
  taken as shown while the Tab held no table, and the Assistant said the table was up when it was not). The studio is
  sent `table-window` (the table's exact day, id and revision, the Tab, the Tab strip's pin), opens it with that code on
  that Tab and answers with the window's placement (`table-window-result`). It is done only once the report shows that
  window (`document:<placement>`, representation `table`) in that Project and Tab with that revision, its rows drawn
  (`state` rendered), shown, its Tab in front, and no window covering half of it or more.
  - **One action with a Tab named** ("put the table in a new tab called Measurements"): `tab` finds the Tab by its id or
    title, or makes it, and puts it in front through the tabs broker (`workspace_tabs.py`, the person's own Tab
    gestures); the window is then fitted to the Tab with its own Expand. It is never a `tabs create` the model has to
    follow with the table (the log's empty Measurements Tab, said to hold it).
  - **Separate facts:** every answer ends with saved (in Data as its id, at its revision), computed (its rows),
    displayed, in front and accepted (not asked): one is never said for another.
  - **A slow report:** the tool waits past its inline wait (`TABLE_WAIT_S` of the studio's time in view), so the answer
    says it is being done and the outcome is said when it comes, as any late step.
  - **A studio in the background:** waited for as present's changes are ("waiting for the studio to be in view"; only
    its time in view counts, at most `BACKGROUND_WAIT_S`), then "placed ... not shown yet: the studio was not in view
    when it last reported", said only because the report observed it, and never a failure.
  - **Not shown in time, or refused:** said with what the report showed (the Tab open without it, its rows still
    loading, behind the window named) and where the table is saved ("saved in Data as e3, where its row's Open in
    Measurements opens it"); never done, never "reopen". Rows that could not be read there are a failure.
  - A studio without the Project workspace (no Tab strip: an older studio, a test's fixture) is shown the Measurements
    page as before, and a Tab named there is refused. A capability's table output (`kinds.py`) opens the same way.
- **What is in front** (`workspace_observation.front`): the Tab strip reports the Tab in front with its title
  (`workspace.tabTitle`); in front is that Tab, the highest open window on it, and whether the studio was in view and
  focused when it reported, each unknown when unreported and never inferred for another Project or Tab. `tabs list`
  returns it as `front`; the voice's window lines start `Tab "<title>" of this Project` (", the studio not in view" when
  observed) and mark the front window ", in front", and its Window detail has a `Workspace front` line.
- **A Tab's specimen** (the live view; `workspace-views.ts` `specimenReport`, `setSpecimen`; live 2026-10-29: asked
  for "only the image open in the Stoma Reference tab", the Assistant said the Tab held only the image while the
  specimen filled it behind). Every Tab holds it: filling the Tab behind its windows, floating as a window, or hidden
  (Main's floats, never hides).
  - **Reported** on each open Tab of the Tab strip (`workspace.tabs[].specimen`: `state` fill, float or hidden, and
    `rect` in workspace px when floating; the Tab in front's as the Stage shows it, another's as its arrangement keeps
    it). `front()` returns the front Tab's, and it is told wherever the screen is told to a model: the voice's Tab line
    (`Tab "Main" of this Project, the specimen fills it, behind its windows`), `Workspace front`, the chat's display
    inventory, Hands' facts and `tabs list` (each Tab's `specimen`).
  - **Changed** by `tabs` action `specimen` (fill, float or hide) on a named Tab, through the code the Tab's own Float
    or Fill and Hide run. Done only once the report shows it on that Tab (`workspace_tabs.SPECIMEN_WAIT_S`; otherwise
    said with what the report shows); Hands checks the same fact. The tool's words say the specimen counts in what a
    Tab shows, so "only X open" floats or hides it too.
  - **A new Tab** ("+", ⌘T, `tabs create`, a window sent to a new Tab) opens with its specimen floating small in the
    Stage's lower-left corner (`floatingSpecimen`, also where Float puts it). Main, an input's Tab and a run's result
    Tab (it draws there) keep it filling; a comparison hides it.
  - `project.state` does not carry it yet: its schema (`contract/agent/project-state.schema.json`) is another change's.
- **A failure on the report path says what did not happen** (`screen.REPORT_NEXT`): not confirmed on screen yet, saved
  work kept, asking again reads the next report. The studio is the app's own window: nothing tells the model to check,
  open, refresh or restart it (the log ended "I can't see your screen ... quit and reopen the app"). A look at the screen
  that did not happen says no picture was taken and that this says nothing about what the user sees.
- **Done means the screen shows it** (ADR 0006 extended to the screen). A change is sent with an id. It is
  done only once a report says it was applied and shows it; otherwise it failed, and that is said. Live 2026-10-11,
  10 of the user's own window changes were refused in under 35 ms; their causes:
  - **A layer shown is waited for:** showing cells or motion (Made's "show cells", or a request) waits for the
    requesting studio to report the layer before its change is sent (`present._original_layer_ready`).
  - **A window the studio placed itself** (never moved) is resized where it was observed, not at its never-set 0,0
    (`Presentations.admit`).
  - **Numbers compare by value:** a window's displayed opacity of 1 is the server's 1.0 (`completion._same_number`);
    booleans and strings stay exact.
  - **A refused change of the user's** puts back only what it changed, where the window was before it
    (`WorkspaceWindows.rejectManual`, FloatingWindow `onUserStart`), never at the top-left.
  - **A run drawn on the specimen** (S5a): a This Tab run on the Stage's still loses its masks window only once the
    layer's check verified its members there (`workspace-specimen` "drawn"; otherwise its window is placed), and its
    footer says "drawn on the specimen" only once the studio draws them (`view.stillDrawn`); until then "saved in Data",
    never a Tab (docs/architecture/freshness-and-overlay.md).
- **A studio in the background is waiting to be seen, never a failure** (`completion.compare`, live 2026-10-11:
  a photo shown in the studio was answered "could not ... the window was hidden"). With another app or tab in
  front the browser paints nothing, and paints what it was given once the studio is in view again
  (`WorkspaceWindows.shownAgain`). So two facts are kept apart: shown in the studio's layout (an item's
  `visibility` "tab-hidden") and on the user's display (the report's `visible`).
  - **Confirmed from a background report:** what needs no painting (a close, a window's own paint before it
    went to the background), and a mismatch there still fails.
  - **Waiting:** the rest is still being done ("waiting for the studio to be in view", the step's `progress`).
    Its confirmation deadline counts only the time the studio is in view, and it is confirmed (a late ✓) when
    painted.
  - **After `BACKGROUND_WAIT_S` (5 min):** it is said to be sent and not yet confirmed, never that it could not
    be done.
  - **Reports:** a background studio's last report stands until it reports again
    (`Screen._current_observation`). It reports at once what it is sent (`WorkspaceSession.reportApplied`), and
    "close that" there closes the window in front of the layout (`screen.laid_out`).
- **Who set what is kept per property** (`Screen.owners`; Amendment 4, item 4). The user's drags and Escape
  closes are reported as theirs.
- **The studio's own controls use the same verbs** (the `present` command, the user's change): the cells'
  layer controls (shown, fade, colour, fill, and Show on the earlier layers listed under the card) and Made in this
  session (each entry opened as its presentation). It
  lists products only (CONTEXT.md "Product"; `produces.is_product`, the `product` flag on every entry): the analyses.
  - **Products:** cell outlines, motion tracks, researchers' work, screen recordings (they show the app, not the
    specimen), bookmarks, things followed, footage held and reference photos (the web's, not the user's).
  - **Sources, not products** (the user's Q11, ADR 0022): the user's own recordings and pictures of the specimen (Record
    videos, snapshots, clearer pictures) are the session's Assets. Their datasets stay on the Datasets page.
  - **Neither:** named outlines, answers, outcomes, facts, taught labels, the scale and notes.

  Everything stays in the index for the brain and researchers. It is "Made in this session" (ADR 0017): the session's
  products from every day it touched, with quiet day dividers; another session's are its own. "On screen" lists
  whatever is shown.
- **The existing open and close tools wait for the report too** while a studio is reporting: zoom (opening),
  `close_magnifier`, `show_result`, `close_result` and `show_map`.
- **A cell's close-up is its own mask.** "Show that cell in HD": zoom on a cell promotes it from its own
  mask and magnifies it live while it is verified in place, otherwise its captured view's real pixels,
  labelled. The magnifier has a resize grip.
- **`look_at_screen`** looks at a composed picture of the workspace when the report leaves a doubt.
  - It goes to that one vision call only.
  - It is kept only as a performance-log picture (`logs/screens/<day>/`, pruned with the other dated logs).
  - It never goes to Sight, naming or any specimen model.

## Marks, and act then look (the one-brain voice)

- **Marks point by Hooke's own ids** (`mark`, `voice/partner_tools.py`; drawn by the studio's overlay). The server
  sends `{type:"marks", id:"mk-<n>", marks:[{target, style, label?, region?, polygon?}], until:"speech-end", frame?}`
  over `/ws/voice`; targets are `outline:<id>`, `population:<id>` (or `layer:<id>`: a whole layer of cell outlines, as
  the box around its members drawn now, worked out once per layer and placed each frame), `member:<population>:<member>`,
  `window:<presentationId>`,
  `document:<placementId>`, `tab:<id or exact title>`, `researcher:<number or id>`, `panel:<name>`, or `frame` with a
  region or polygon in 0-1 of the camera picture the model saw (the message then carries `frame:{id, crop}`, that
  picture's frame and its crop in source pixels, so the studio maps it as it maps outlines and it follows the slide).
  The studio resolves each target every animation frame, keeps the marks while the voice speaks, clears them about
  1.5 s after, and answers `{type:"marks-shown", id, resolved:[{target, ok, rect?, why?}]}`. **What it answered is what
  the tool says** (shown, and not shown with why); no answer within 1 s is "not shown", never claimed. `marks-clear` goes
  out at a barge-in.
- **The app points at what a tool just showed** (a layer of cell outlines, an outline): the tool's result names it
  (`pointed_at`), and the mark is drawn as the answer about it starts to be heard (the first `output_audio_buffer.started`
  of the response after the tool round), with no round trip through the model's `mark`. The model calls `mark` when
  the user asks where or which, or when its answer is about one thing in view, after speaking.
- **A mark goes with the words** (Will's feel check, 29 Oct): a mark shown after the reply's words asks for no reply of
  its own (the Realtime API leaves the next `response.create` to the client); only a mark that was not shown, or one
  called before any words, goes on to a reply. Nothing on screen is said twice.
- **Its first line points too:** as Talk connects, the voice's opening line (seeing-and-acting.md "It talks first") may
  call `mark` on what it names in the camera picture, and nothing else.
- **Act, then look:** a tool that changes the screen (`partner_tools.SCREEN_CHANGING`) is followed by a report newer than
  the change (`Screen.until`) and a window picture (`snapshot-request` → `snapshot`), and the model reads both before it
  is asked to go on: "done" is said from the picture, not from the tool's own success. The record of the call says
  whether a report came (`confirmed`, `reportT`).


## The microphone moment and Talk's status (the studio)

- **Before the browser's prompt** (`apps/studio/src/mic-card.ts`; Will's feel check, 29 Oct): the microphone
  permission is read ahead of any press (`navigator.permissions`). Not allowed yet, a press of Talk or the talk key
  shows one card in the Assistant column, above the composer: what happens in one line ("Hold Control+Option and talk;
  Hooke listens only while you hold it") and one button whose click starts Talk, so the prompt answers a click the user
  meant and the call connects at once, before the first hold. While the prompt is up the card says so and Talk's word
  is "Microphone", never "Connecting"; refused, it says how to turn the microphone back on (the address bar's site
  settings; in the Mac app, System Settings). A browser that cannot say, or the Mac app once it has heard you, starts
  at once and the browser asks itself. "Connecting" is only the real connect.
- **The status says only what is true:** after a release it says the turn was heard only when the server took it
  (`heard`); a turn the server drops never shows as waiting for an answer.

## Hands: the partner's drive (spec step 1, section 4.2)

The one-brain partner acts in Hooke through Hooke's own operations (the registry's tools), never through pixels. Every
acting tool goes through Hands (`voice/hands.py`), for the voice and for `think`'s backend alike: the screen tools
(`present`, `tabs`, `show_measurements`, `highlight`, `select`, `zoom`, `clear`, `outline_cells`, `find`), Projects
(`new_project`, `switch_session`), `add_asset`, `reference_photo`, researchers (start, note, follow-up, revise,
cancel), capability and workflow runs, settings and choices, and every analysis that saves something. A look (the
registry's queries: `count`, `measure`, `resources`, `show_me`...) runs directly: its numbers are its answer, never
"done". `tests/test_partner_reach.py` checks that every tool the Actions panel runs, and the catalog's rows, are
reachable by the voice or think, and that each acting one has a receipt contract.

- **Typed intents.** Each call is one intent: `open` (window, Tab, table, asset), `close`, `focus` (a Tab),
  `highlight`, `select`, `zoom`, `outline` (with its model), `find`, `present`, `project`, `research`, `cancel`,
  `steer`, `run`, `setting`, `choice`, `make`, typed from the tool, its arguments and the registry's own words for it
  (kind, produces, family, sets: `hands.intent_of`). UI intents (the screen tools, Projects, an asset, a reference
  photo's window) take the lease, the cue and the lock below; the others (researchers, runs, settings, analysis, a
  reference photo's lookup, kept without a window: `show=false`, opened afterwards with `present`) do not, and a
  takeover never stops them, but they keep the action id, the Project precondition, permission, the Activity line and
  the receipt.
- **Hooke's own record** is the check where the screen is not the right one (`voice/records.py`): a researcher's record
  (`running` while it works, its finish an event later), a cancel only once the record says cancelled, a note or
  question recorded on that researcher, a capability or workflow run's record, a setting read back changed, the
  Project changed and shown ("already in" and "nothing opened" are not done), a setting already reading what was
  asked ("already the default"), the asset on stage, a reference photo's window when its result lands, and what an
  analysis saved, resolving in its store with its numbers. `think`'s jobs are done only when every acting call they
  made ended verified (`voice/jobs.py`).
- **The target by id.** A target the screen now shows (the report's ids and Tab titles) or the conversation pointed at
  (marks and `pointed_at`, newest first) is used as given. A target given as nothing the screen or the pointing
  history knows is filled from them when one candidate of its kind is there, and asked about in one short question
  when two or more are (`{"result": "ask", "candidates": [...]}`), never guessed.
- **A visible cue first.** Before the effect the server takes control and sends the cue over `/ws/voice`:
  - `{type:"control", version, owner:"partner"}` before the first cue of a turn's intents, and
    `{type:"control", version, owner:"user"}` when they are done. `version` is an integer that only rises; the studio
    ignores a cue with a lower version than the last control it saw.
  - `{type:"drive", id, version, intent:{kind, target, caption, style?}, frame?}`: the target in the marks grammar
    (above) plus `control:<css id>`; `caption` 2-4 words ("Opening the table"); `style` `ring` for things on the picture
    (`outline:`, `member:`, `population:`, `frame`), `cursor` for windows, Tabs and controls, chosen by that rule when
    missing; `frame`, `region` and `polygon` as in marks. The studio moves its partner cursor there (about 300 ms,
    eased, never the Mac pointer) or rings the target, and answers `{type:"drive-shown", id, ok, rect?, why?}` when it
    arrives, or at once with `ok:false` and why (the marks' why values, plus `stale` for a drive below the last control
    version, answered at once and never drawn). The server runs the intent after drive-shown,
    waiting at most `CUE_WAIT_S` (0.5 s); a cue not shown never blocks the action and is logged.
  - `{type:"drive-end", id, outcome:"verified"|"failed"|"uncertain"|"running"}`: the cursor settles and fades, with a
    tick for verified and a cross for failed.
- **Bound and re-checked.** An intent is bound to what it was planned against (`hands.Plan`): the voice turn's key-down
  (or its last look after an action), or think's call at its start, with the takeover epoch, the Project and the
  screen (the report's `seq` and facts). It is checked again at the moment of dispatch (after the lock, after a
  permission wait): the same lease and epoch, the same turn, the same Project (or the one the partner itself just
  opened), and its target still on screen where it needs one. Otherwise nothing runs and the receipt says why.
- **Talk and takeover, kept apart.** While the owner is the partner, the studio sends `{type:"takeover", version,
  why:"mouse"|"escape"|"talk", at}` in the same task as the user's input (real pointer movement over the window of at
  least 3 px, Escape, the talk key or button; on top of talk-down). Either way the server raises the version and the
  epoch, answers `{type:"control", version, owner:"user", ack:true, at}` (its receive time, ms), and acts on the UI
  again only with a new lease and a fresh look (the next turn's pictures, or the look after an action nobody took
  over), for the voice and think alike.
  - **The talk key is a pause:** nothing queued runs (its receipt: "not done, you spoke", `paused`, so the voice plans
    again from the new words); the action being done finishes and its receipt is said; the studio's cursor stays on
    it until its `drive-end`.
  - **The mouse or Escape is a takeover:** the cancel signal is set, the action being done is stopped through its
    run's Stop (`user_stop.Stop`: `Registry.start` says it stopped) and is otherwise **uncertain** now and reconciled
    in the background (its real receipt to Activity, the log and the voice as context). Background work goes on.
- **Verified by the app.** The result is `verified | failed(reason) | uncertain | running`, decided from the tool's own
  run (its record; present's own receipt of the studio's report, `ToolRun.outcome`) and the studio's report showing
  the expected state, compared with what was asked (`hands.expected`), with no shortcut: the Tab in front for `focus`,
  the Tab there (and in front when new) for `create`, the id gone for `close`, the table on the Tab in front showing
  the dataset asked for for `show_measurements`, every outline or member asked for drawn or emphasised for
  `highlight`, the selection for `select`, the magnifier open on the outline for `zoom`, the layer it made with its
  member count (`legacy.members`) for `outline`, what it found drawn for `find` (said as found, never "done"), the
  window in the report for `open`. A report that cannot show the state is `uncertain`. The voice says exactly the
  receipt's `said`; "done" is never its own judgement.
- **Permission.** Reversible actions in Hooke need none, and starting a researcher at its usual effort asks nothing. An
  intent that spends (a paid cloud model or capability, a deep researcher run, a video) or deletes asks once,
  by voice, with the cost said (`{"result": "permission", "ask": ..., "approval": id}`); the approval is bound to the
  intent and expires after 2 minutes, and the intent is re-checked after it.
- **Activity.** Each intent files one Activity line (kind `command`, "Assistant: <caption>"), running, then its receipt
  (`details.ids.receipt`): done, failed, or "not known whether it happened" for an uncertain one, never done; an ask
  and a permission get lines too. The `activity` tool reads the open Project's lines only (`Activity.project_of`), and
  the user's own actions (clicks, commands, a researcher started by hand) reach the voice as silent context with the
  ids they touched, which also feed the pointing history (`voice/knowing.py`, on by default).
- **The report's additions** (all optional and bounded; `workspace_observation._legacy`): `workspace.tabs` (the
  Project's open Tabs in strip order, at most 24, exactly one in front, each with its specimen), `legacy.drawn` on the
  outlines item (the outlines drawn on the live view, at most 60, with their kind) and `legacy.members` on a layer
  (`{count, drawn}`).
