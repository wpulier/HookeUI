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
  - **A verb a kind does not take fails with the reason and what it takes.** The live view is only dimmed, never hidden.
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
