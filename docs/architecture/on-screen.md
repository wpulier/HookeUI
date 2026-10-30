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
  - `project.state` does not carry it yet: its schema (`contract/agent/project-state.schema.json`) is another change's
    (the addition both need is under "The workspace model").
- **The workspace model: what each Tab holds, by kind** (live partner spec 4.8, horizontal 2;
  `apps/studio/src/workspace-model.ts`). Every thing on a Tab has one kind: table, outlines, tracks, image, reference,
  document, graph, video, web-page, magnifier or note (`ITEM_KINDS`; `workspace_observation.ITEM_KINDS`). A Tab holds
  its saved items (masks: outlines on their capture; table; capture; datatable; analysis; graph; a document), the
  windows open on it that its arrangement does not save (a live magnifier, a reference photo, a web page or video, by
  their source: `data-source-kind` and `data-source-media` on a native window, `data-web-window` on a web window), its
  notes and its specimen.
  - **`tabs list`** returns each work Tab's items as {placement, kind, title, presentation, resource, window, source}
    with `resourceKind` from the index, `holds` (its items counted by kind), its `input` and `specimen`; a researcher's
    or Study's Tab with what it `shows` (its conversation and the work it made, by kind). `kind=table` lists only the
    Tabs holding a table, so "which Tab has the measurements? Open that" is answered from kinds, never from a title.
  - **The screen report's Tab strip** carries each open Tab's `kind` and `holds` (compact: the counts; the items are in
    `tabs list`), read leniently: a kind or count out of bounds is dropped, never the report (the finalisation plan's
    rule 4). `front()` adds the Tab in front's `holds`, the layers drawn on its specimen (`overlays`: each cell-outline
    and motion layer shown, with its members) and its `magnifiers`; the voice's Tab line says them ("Tab "Main" of
    this Project, the specimen fills it, behind its windows, holding a table; drawn on the specimen: cell outlines
    (142)") with a line for the other Tabs ("Other Tabs: "Cell results" (a table and cell outlines)"), and so do
    `Workspace front`, the chat's display inventory and Hands' facts.
  - `project.state` does not carry it yet: its schema is Codex's (`contract/agent/project-state.schema.json`); the
    addition it needs is each Tab's `kind`, `specimen` and `items` ({placement, kind, title, resource}).
- **Placing things: one vocabulary** (`orchestrator/placement.py`). Every tool that puts something on screen takes the
  same words, each done only when the studio's report shows it, with what the report shows when not:
  - `new-tab`: a Tab of its own, a canvas with no specimen. A population's results (`show_measurements` or `present
    do=place`): its outlines on their capture and its table beside them, like Will's "Cell outlines" Tab ("Cell
    results"; the report shows both on that Tab, in front, side by side).
  - `beside`: side by side in this Tab with what is there, the live view included: the Tab's own Arrange (`tabs
    arrange`; the report shows no window, the floating specimen included, over a quarter of another). A new Tab has no
    live view, so "beside the live view" names Main. The Arrange is sent once the report shows the new window there, and
    again (3 at most, 1 s apart) while it still shows them overlapping (`table_window.show`, `_wait_for`, the tabs
    broker's `_placed`); it tiles native windows with no retained resource too (the live magnifier, through its own
    resize and move: `WorkspaceWindows.placeWindow`). will-workspace, 30 Oct: one Arrange sent before the table landed,
    and a magnifier Arrange never moved, left "its windows still overlap; ask again".
  - **No room beside a window is Hooke's to make** (`placement.make_room_beside`): a photo asked for beside a window
    with no room on either side moves that window over (narrowed only when moving is not enough), each change its own
    move or resize confirmed on screen, then opens beside it, and the answer says what it moved. Several windows and none
    pointed at: the one in front. Only a visible area too small for both is said, plainly (will-workspace, 30 Oct:
    "make room or select the original window" was handed to the user).
  - **A new Tab is always made, and told apart** (`workspace_tabs.fresh_title`): `tabs create` or `new-tab` with a
    title a Tab already has makes "Reference 2" and says so; the broker remembers the Tabs each request made, so that
    request's next step in "Reference" is the new one; of several Tabs with one title, the one in front, else the
    newest. Never "which one?" (will-workspace, 30 Oct).
  - `overlay`: drawn on the live specimen, outlines and layers only (`present` show), a layer named any way the screen
    names it (`e3`, `layer:e3`, `population:e3`, `m1`, `tracks:m1`; `placement.layer_of`); anything else is refused
    with what to do instead.
  - `window`: a floating window, in front (nothing over it in the report).
  - `replace`: in place of the window in front, which closes first.
  - `close`: closed; its Data is kept.
  - `only`: the only thing open in its Tab (`tabs only` with `keep`): every other window closed through its own ×, as
    a person's click closes it, and the specimen hidden, or on Main floated small in its corner (Main always keeps
    it); the kept window fitted to the room left. Done once the report shows only it there and the specimen so.
  - **Put X in Tab T, wherever it is now** (`placement.put`; `present` with a placement and `tab`; live 2026-10-29: a
    researcher's graphic and a reference photo "as the main item" of a named Tab were refused, and the user was told
    to save it first). The Tab named is found or made and brought forward, then the thing is: placed there when it is
    open there; moved there from the Tab it is open in with `instead` (`tabs move`, the person's own Move; the model
    decides from the user's words, "instead", "move it"), otherwise opened there too, and the answer says where else
    it is still open; opened there from Data when not open (a table or a population as the Tab's own window, `tabs
    add`; a video or page in its web window; a saved picture, or a researcher's picture from its exact retained output
    as `show_result` opens it, never a copy, in a window of its own). Then it is placed (`only` keeps the window it put
    there, by its id on screen, never the Data id it was asked by; `beside` arranges the Tab). `show_measurements`
    takes `instead` the same way and says when the table is also still open in another Tab. Hands checks `only`
    against the window opened (its presentation output). `tabs add` of a photo says it is placed this way; a
    researcher's Tab lists its work by its Data id (`r3:o1`). Tests: `tests/test_placement_put.py`.
  - **Defaults by kind** (`placement.DEFAULTS`): a run's outlines as an overlay with their table beside (the table's
    default is `beside`), a reference image beside, a document in a Tab of its own, a video or web page as a window.
  - **A window opened on a reader Tab is in front of its reader** (live 2026-10-29: a video asked for while a
    document's own Tab was in front was "hidden behind the current document"): the reader there is the Tab's own page,
    under the windows opened on it (`data-reader.css` lifts the Stage's windows layer over it; the report gives the
    reader Tab's surface z 1), and the Tab holds those windows beside its one item (`tabItems`).
  - Hands checks `tabs only`/`arrange` and `present do=place` from the same report facts (`placement.only_shown`,
    `beside_shown`) and says where: "Done: only that is open in the Main Tab; the live view floats small."
- **Web pages and videos** (`show_web`, `orchestrator/web_window.py`, `apps/studio/src/web-window.ts`). A video found on
  the web, or a link, opens in a sandboxed window of its own: its frame's scripts run in its own origin, never the
  app's (`allow-scripts allow-same-origin allow-presentation`; no top navigation, popups, forms or downloads), and the
  window always says where it is from with an "Open on the web" link. Only an allowed host's framed address is ever
  sent or framed (`web_hosts.py`, the studio's `WEB_HOSTS`): YouTube as its privacy-enhanced embed
  (`www.youtube-nocookie.com/embed/<id>`, from any YouTube link), Vimeo's player, Wikipedia and Wikimedia Commons
  pages; `presentations.SourceRef` refuses any other web source, and any other site is said with its link.
  - **The video search** is the grounded web retrieval (`research_worker.retrieval.lookup(videos=True)`, Gemini 3.8
    Flash with Google Search), checked before anything is shown (`video_search.py`): each candidate a video page on an
    allowed host, its real title and channel from the video site's own oEmbed answer (a video the site does not know
    is dropped), a model judging which one's title and description are what was asked (`retrieval.judge_videos`), and
    the code taking only one of the candidates it checked; none matching is said so. Live check, 29 Oct: "stomata on a
    leaf..." found "Stomata & Guard Cells - GCSE Biology" (Launchpad Learning) in 16 s.
  - The video or page is kept in Data as the Project's own entry (`show_web`'s result, its source the web), so it is
    in the Tab model, the reader and "resources". It is done once the report shows its window on that Tab
    (`document:<placement>`, representation `web-page` or `video`), its frame loaded (a frame's inner pixels are never
    certified from its load event) and nothing over it. Closing it stops it playing. Its window is not saved in the
    Tab's arrangement yet (that needs a layout field), so it closes with the Project.
  - `retrieve_page` with `show` opens the page it read the same way, or says its link.
- **Documents** (`write_document`, `orchestrator/documents.py`; Will, 29 Oct: "Write out the interesting findings… save
  that doc, also open it in tab"). Every claim comes from a check:
  - **The facts are code's:** the Project's records as the brain's `resources` lists them (`Index.list`: counts,
    measurements, populations with their measured columns, named outlines, the scale, facts settled, tables and
    analyses, reference photos, the user's notes, researchers' reports), each with its id, what it is, who and what
    made it and when. What is not the app's own (a researcher's words, a reference photo's web source, the user's notes)
    goes only inside one untrusted block. An earlier document is never a record: a new one is written from the records.
  - **The prose is the model's:** the brain's own model (`settings.vision`) writes the title, sections and paragraphs,
    choosing what is interesting for what was asked, each paragraph listing the ids it draws on.
  - **The checks are code's:** a paragraph that cites no record of this Project is left out (and said, in the receipt
    and under the document), a cited id the Project does not have is dropped, an id written in the text becomes the
    record's plain name ("(Count · 14:02)"); with nothing left, nothing is saved.
  - **Kept** as the Project's own result (`media` "document", Data's Write-ups, a product): its sections with each
    paragraph's citations (id, kind, plain name) in the result's file, its text read from there (`documents.text_of`:
    the brain's `resources(id=)`, a researcher's session tool, the reader's detail). Its words are untrusted to every
    prompt; its line is the app's ("Document · 14:05"), and Data shows its own title.
  - **Shown when asked**, in a Tab of its own by default (`placement.DEFAULTS` "document"): a reader Tab, the person's
    own Open in new Tab (`WorkspaceViews.openReaderTab`) through the tabs broker's `reader` action (one saved item by
    its exact id and version; an item that has a Tab goes to it), named after it. Done only once the report shows
    `document:reader-tab` with it, its Tab in front and its pages drawn (`state` rendered; only the studio's time in
    view counts, `SHOW_WAIT_S`); a studio in the background is said placed, not shown yet. `beside` opens it in the
    reader on the right of the Tab in front (data_reader.py). A studio without the reader Tab action ("not supported")
    shows it in the reader beside, and the receipt says it is not in a Tab of its own. The reader draws it on its page,
    each paragraph's records by name under it, each opening in the same reader (`data-reader.ts` `drawDocument`).
  - **Its receipt** says what was written (its title, its sections, the records it cites by name), where it is saved
    ("saved in Data as d7, under Write-ups") and where the studio shows it. Opened later, it is `present do=place`
    with its id (new-tab or beside), the same path. A deep report that reads the web is a researcher's.
- **An experiment's results** (`orchestrator/experiments.py`; Part 2.5, W5) are one document of the same kind, made by
  the app from the run's records (its byline says so: "Made by the app from Experiment 1's run records"), with the
  researcher's audit in its own words: what was compared, frame by frame (each frame's paragraph citing each model's
  cells), the comparison table and the outlines side by side on frame 1 drawn on the page (`readerDocument`'s `table`
  and `figure`), and the audit. When the experiment lands they are placed in a Tab of their own by a request of its own
  on the studio that approved it (else the one whose report counts), pinned to what it shows then: `present` do=place
  placement new-tab with the document's id, done once the report shows the reader Tab with it. With no studio showing
  its Project it is said not opened, never claimed; the card's Open results does the same later.
- **A failure on the report path says what did not happen** (`screen.REPORT_NEXT`): not confirmed on screen yet, saved
  work kept, asking again reads the next report. The studio is the app's own window: nothing tells the model to check,
  open, refresh or restart it (the log ended "I can't see your screen ... quit and reopen the app"). A look at the screen
  that did not happen says no picture was taken and that this says nothing about what the user sees.
- **An internal check is Hooke's to resolve, never the user's** (live 2026-10-29, 17:17-17:26: two closes, a highlight,
  a table, a researcher's graphic and the researcher's own result all said "out of date ... ask again" while the studio
  kept reporting). Each failure path is a state Hooke re-checks and recovers from itself:
  - **A window change the screen did not show in time** (`present._native_settled`; will-leaf, 30 Oct: front on the
    open reference photo timed out and the voice said "ask again in a moment"): a change that ends the same however often
    it is sent (front, move, resize, pan, hide, restore, opacity) is sent once more by Hooke, after a fresh report; a
    second miss is said as what the screen shows, with no next for the user. Front on a window laid out on a Tab not in
    front brings that Tab forward first (`_its_tab_in_front`).
  - **Front inside the Project workspace** (`present._window_expectations`, `completion` scope `tab-windows`): every
    native window there lives inside `#study-artifacts` (main.ts `onPresentation`), so the stage's foreground lists only
    `side` and `study-artifacts` and never proved a window in front. Such a front is proved by the window's order on its
    Tab: above every window shown there, by the z the studio measured, a window opened since counted too.
  - **A report naming a window whose grant has ended** (its pin released when it closed, or never granted to this
    connection; `App._workspace_source` sets `ended`) is taken without that window (`Screen._without_sources`: complete
    false, its items, targets, edges and pointer left out), never refused whole: refusing it froze what Hooke knew of
    the screen. The reply says what was left out (`partial`) and the log has a `screen-partial` line; the studio shows
    no notice. Facts that differ from a source still prepared here, and a live window still claiming to draw from a
    producer that stopped, are still refused whole (`screen-refused`).
    A live window's grant ends at its close too, though the close's own read-only borrow keeps its source pinned until
    the close is confirmed (live 2026-10-29 18:57: "close everything on this tab" left the magnifier's close "sent, but
    not confirmed on screen", every report in its wait refused whole): a live source with no visibility owner left
    (`PreparedSources.prepared_key`) counts as ended, so a report racing the close is taken without that window.
  - **A window the studio closed is reported closed** (`WorkspaceWindows.apply`): it is never presented again, so its
    owner does not mount it back in the Project's artifacts container. It is reported open false, not connected, with
    no controls and its close result absent, which is the inactive closed window the server takes
    (`Screen._closed_source_rows`) and its close is confirmed from (`workspace-runtime.test.ts`, with the server's own
    intake in `native_workspace_contract_consumer.py`).
  - **A user change with no original in the last report taken**, or one that changed nothing, is left unowned, never
    a refused report (a property no window has is still refused).
  - **A report that disagrees with the app about a window an action touches** (no row for it, another lifetime or
    source, another version or owner of a property: `present._placement_issue`), or is not current, is asked for again
    once (`Screen.refresh(..., newer=True)`, at most `CAUGHT_UP_S`) before the action's checks run on it, so a report
    that is only behind is never said as "that window changed".
  - **A placement beside a window** (`present` open or arrange with a `beside` relation, `reference_photo`
    beside=true) says the check that failed: the anchor "disagrees" (after that one fresh report), is "gone" (closed in
    the app), or is "hidden": open and unchanged, but laid out nowhere the person sees, with where it is ("it is on
    the "Main" Tab, and the "Reference" Tab is in front"). A window laid out in a studio in the background still
    anchors (`screen.laid_out`, as `completion` counts it shown). An open beside a hidden anchor opens where the studio
    opens any window, on the Tab in front, and its receipt says it is not beside it and why (live 2026-10-29 18:54: the
    magnifier was on Main when the web photo came, after "open a new tab called Reference"; the photo was kept and
    never shown). An anchor gone, or changed by someone else since the request, still refuses the placement (the photo
    is kept, as before).
  - **A change with no report since it was sent** asks the studio for one (`screen-ask`, every `ASK_REPORT_S`) while it
    waits. If none comes it is **uncertain**: "sent, but not confirmed on screen" (STUDIO_UNREPORTED; Hands'
    receipt `uncertain`, "I sent it but couldn't confirm it on screen"), never done, never "ask again". A step the
    whole action needs (a split's retirement of its original) still fails the action.
  - **One way in** (the spec's 4.8 horizontal 1): every actor's request gets its current report from Hooke itself,
    through one helper, `screen.fresh_report` (`Screen.current_report`), never failed as stale by each tool in turn:
    - **a typed Send's steps** (`ActionRunner._current_report`): its report, asked for again when older than
      `REPORT_FRESH_S`;
    - **a voice hand-off and every voice tool** (`now`): the bound studio's report at this moment, pinned as a typed
      Send pins the report it carries; the hand-off seals its Ticket after the turn, as a Send does;
    - **a researcher's own studio action** (`Announcer._open_beside`, showing its result: live 17:25, it failed
      "unavailable or stale"): its original studio's, when stale; a report with no viewer to change keeps its work
      retained (in Data), never an error;
    - **the studio's own Run and a connected agent's run** (`commands.run_tool`; the door's capability runs name no
      studio, so the one whose report counts): when stale.
    A report that does not come leaves the last one standing, and a change it never confirms is uncertain (above).
    Tests: `tests/test_one_way_in.py`.
  - **The Tab arrangement** (studio `workspace-views.ts`): a save that meets a newer revision is rebased on it
    (`rebasedLayout`: what was added there kept, everything shown here kept) and saved again, at most twice; a window
    is always saved with a name (`windowTitle`, `savedTitle`); a refusal of the arrangement is said on its layout line
    and in Activity (`layoutDiagnostic`), never a toast; the Tabs broker waits for a Tab still opening instead of
    "ask again". Tests: `tests/test_voice_screen_recovery.py`, `workspace-layout-recovery.test.ts`.
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
