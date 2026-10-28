# Sessions: the project and its conversations

Part of the working context the root `AGENTS.md` maps. "Above" and "below" may mean parts it lists in
other files.

Inline conversation rename captures its Project session ID. The Studio cancels the field
when that Project changes, and `commands.thread` refuses a rename carrying another
session before writing the current journal. Session-local conversation IDs alone do not
establish ownership across a Project switch.

- A session is the project, not the day (ADR 0017). The user's decisions of 2026-10-11/12 set its rules.
  - **It holds** a name, its conversations, what was seen in it, its memory and its work: researchers, populations,
    motion tracks, bookmarks, snapshots and results.
  - **Several a day, or one across days:** a day can hold several sessions, and one can go on for days ("come back
    tomorrow to the same prepared slide").
  - **Ids** are `YYYY-MM-DD`, or `YYYY-MM-DD.N` for a date's later ones (`day/sid.py`, the only parser; the studio
    treats them as opaque). Every day journal from before is the session whose id is its date.
  - **Portable ownership:** new journal creation records one durable `session-origin` UUID before its first event.
    Equal date IDs or equal event timestamps in independent profiles do not establish the same Project. The origin
    remains in the journal, not a second registry. Opening or exporting a legacy journal never invents an origin.
    `day/ownership.py` reads bounded owner envelopes (16 MiB journal, 64 KiB line); imported scientific work retains
    the original origin and an explicit `session-imported` event with name/local-only metadata. This is a partial
    work import, not restoration of absent conversations, assets or workspace layout. Re-export preserves that origin.
    Import refuses a same-ID Project with a different or unavailable origin, including the initial same-day Project
    in a fresh profile. It never deletes that Project or silently remaps scientific references. Legacy data-only
    bundles remain available for explicit offline resource recovery; public sessions-aware import refuses them before
    publication so neither an existing nor later same-ID Project can adopt their work. Explicit user-requested
    legacy-origin migration is not implemented.
    Journal creation and user New/Open Project share nonblocking namespace admission with imports; a busy namespace
    refuses before changing the active Project. Reconstructing an existing conversation for background filing uses
    the shared visibility guard, not the unrelated creation namespace. Only a complete recorded conversation admits
    this path; partial initial creation still needs the namespace. A later torn tail does not erase that admission.
    Preferences reuse the line writer but are not Project owners and never gain a session origin.
    A journal admitted by visible load/creation remembers its exact inode:
    existing live events append durably without waiting for unrelated import, using a no-create/no-follow descriptor
    and unchanged inode/path checks. A newly imported, uncommitted journal cannot obtain that admission. Replacement
    or transaction refusal propagates, never becomes memory-only success. An interrupted owner import reports retryable
    unavailability, never an empty catalogue; startup recovers its transaction before SessionStore resumes.
  - **Starting:** a new session starts only when the user asks: + New session in the session title's menu, or
    by voice or chat (`new_session`, only when they ask for a session in so many words; "new topic" is a
    conversation). Midnight does nothing.
  - **The suggestion** (`day/subject.py` `_maybe_suggest`): the app may suggest a new session and never starts one.
    It suggests one when three things hold together:
    - the running look has confirmed a different specimen (two looks);
    - the session has not seen that specimen;
    - the session was last asked in 3 hours or more ago.

    It is journaled (`session-suggested`), shown as a dot on the session's title and a row in its menu, and told once to the brain and
    the voice as a note. "Keep here" (`session-kept`) dismisses it for that specimen, and any opening clears it.
  - **Opening:** the app opens in the session it was last in (`SessionStore.resume`: the pointer in
    `preferences.jsonl`, else the one a request was last made in). Another is opened from the session title's menu or
    "go back to yesterday's leaf session" (`switch_session`, a query tool: `day/catalogue.find` by name, date, day
    word or specimen; one clear match opens it and gives the brain its facts, summaries and latest exchanges;
    several are listed for the model to ask which).
  - **What opening brings back:** the live view starts fresh (outlines, magnifier, viewer, cells' layer: `App._clear_view`
    on `session-opened`). The session's conversations, memory, researchers (continuable), evidence, tracks,
    bookmarks and snapshots come back as history. Its live outlines never do, and one app-notice says so ("Back in
    'Leaf epidermis' (last used yesterday 8:12 pm). Its outlines are in its pictures; the live view starts fresh.":
    `App.say_resumed`).
    - **Its cells' layer on their picture does** (ticket 67; the thesis check found a Project gone back to with no
      outline layer, so nothing in Data said "On this Tab"). Each session journals which population its layer shows
      (`layer-shown` events, `SessionStore.record_layer`, told by `population.Layer.journal`), in that population's own
      session while it is open: hiding it because another session opened is not a change in the session left, while
      a clear, a hide or another source on stage journals none. A session switched back to, or the one a restart
      comes back to, shows that population again once (`App.reopen_layer`, off the landing: its retained files are
      checked in a thread, then `apply` scope `cell`, as Data's Show on picture shows one), and only when its first
      landing puts on stage the picture the cells were found on (`sourceImage`). On the live camera or any other
      source it starts fresh (ADR 0010), and a picture put on stage after that first landing is the user's own choice.
      The layer then draws it only where its check on the still verifies it (`docs/architecture/freshness-and-overlay.md`).
      `tests/test_population_reopen.py` checks it through the real app, journals and evidence.
  - **Opening is using it:** an `opened` line is written in its journal, so its last use is now and today becomes one
    of its days (the title's menu lists sessions by when each began, so opening one only marks its row).
  - **The view follows the session** (`apps/studio/src/session-view.ts` decides, from `session-cleared`'s `why` and the
    session's sources, ADR 0022):
    - **new:** the empty stage (`session-home.ts`, mode `choose`): the live camera (its state and Use live camera), add a
      recording or picture (or drop files), and the newest of your assets with All assets. While it is up the studio sends
      `view {held: true}`: the running look makes no looks and the subject tracker takes none, and the brain and the voice
      are told "The live view is hidden: the user is choosing what this session looks at." (`brain/context.py`
      `HELD_LINE`). Choosing anything puts it on stage and lifts it.
      The last Studio disconnect clears that hold. Socket registration is cleaned up even if its initial
      snapshot transmission fails or is cancelled; another connected Studio still retains the held view.
    - **switch:** the session's last source (`GET /api/sessions/{sid}/sources` `onStage`): a recording paused where it
      was left, a picture held, or live. The server's "Back in '…'" notice is the only other word.
    - **start** (a studio connecting, a restart): what plays is kept.
    - Only a studio in view lands a session; one out of view lands it when it comes into view.
  - **Sources** (ADR 0022; `orchestrator/library.py`, `sources.py`; `docs/design/2026-10-12-session-sources.md`): a
    session looks at the live camera or one of the user's assets.
    - **The library:** every real microscope recording, file added in the studio, Record video, snapshot (its clean
      copy) and clearer picture, and example data (the public corpus's recordings: `scope=example`).
      - **One home per thing** (the user's Q11): Record videos, snapshots and clearer pictures are listed in the session's
        Assets, not in Made in this session (`produces.is_product`). The index still holds their entries for the brain and researchers,
        and their small picture is the library's (`GET /api/library/{id}/thumbnail`).
      - **Ids:** a library-wide id (`a7`), given once per key in `library.jsonl`.
      - **Metadata:** filled by the app (kind, when, length, camera, optics at capture, origin session), with the user's
        edits over it (specimen, magnification, title, notes: command `asset-meta`).
      - **Never in it:** test footage, played from the gear's developer setting (the same corpus clip stands for the live
        camera there).
    - **A session's sources:** opening associates (command `source {open: id | "live"}`, which plays it on the bridge).
      Its Record videos, snapshots and clearer pictures join on their own; one made here is said once in the hint,
      "Snapshot saved to this session's Assets", and the Assets tab lights up (`sources.joinedSince`: only a row the
      server says `joined` here, never one opened from another session or brought back by Undo).
    - **Removing** (`source-remove`; Undo `source-restore`) unlinks: the library keeps the asset and the session keeps
      the work made from it.
      - Its specimens leave the session: subject events carry the source they were seen on, and are applied again
        without it.
      - A name the app took from one of them goes with it (to the next specimen, else none). Undo puts it back. The
        user's own name stays.
    - **Kept in the journal** as `source` events, with where a recording was left, so a returning session can land
      there. `GET /api/sessions/{sid}/sources` and `GET /api/library[/<id>[/thumbnail]]` read them.
    - **On stage:**
      - **Scale:** an asset's magnification is the scale (`Scale.use_asset`), and with none, no size is stated.
      - **Naming:** setting the objective or calibrating describes the asset. A specimen the user gave it wins over
        the looks.
      - **What the models are told:** one line of its metadata, "On stage: the stage shows asset a7: a recording, …"
        (`sources.stage_line`; none on live). `resources(id="a7")` gives the rest.
    - **The studio** (`apps/studio/src/sources.ts`): the side bar's first section, the session's Assets (the user is
      told "this session's assets"; "sources" stays the code's word).
      - **Its rows:** the historical Sources sidebar put assets on stage directly. The October 22 workspace
        amendment (ADR 0022) adds a permanent Live camera choice to Inputs; asset clicks preview, and Open in Tab
        selects a dedicated input Tab through the shared source command. Each row has × (with "Removed · Undo") and its details: specimen, objective, title, notes.
      - **Also in** (the user's Q27): under a row, "Also in: Oct 12 · Sperm motility" names the other sessions that use
        the asset, each a link that opens that session, so earlier work on it is one click away. The server's
        `sources.rows` gives each row `alsoIn` from the catalogue: the sessions that opened it and did not remove it, and the
        one it was made in when it joined there on its own (newest first, at most 3).
      - **Add…:** from this Mac, or From All assets (the library's page).
      - **Dropping files** on the stage or the section uploads them to the bridge (`/assets/upload`, pictures too) and
        opens them.
      - **Test footage** is the gear's developer setting (its menu, and the Test footage page `#footage`: the corpus,
        bench clips, the camera off the microscope); picking test footage tells the session `source {open: "live",
        switch: false}`.
    - **All assets** (`#assets`, `apps/studio/src/library-page.ts`; `docs/design/2026-10-12-workspace-and-assets.md`): a
      page of its own, a gallery in two groups under one switch (top right), each its own search.
      - **Yours:** the user's own (`GET /api/library?scope=mine`), and the examples a session has used (`Library.used`,
        the catalogue's sources), newest first, with Recordings and Pictures; Add files and files dropped on the page join
        them, never a session.
      - **Examples:** the public corpus's recordings (`scope=example`, about 1,500, once fetched), by specimen inside a
        category (each with how many; `specimens` on the route), a few dozen at a time with Show more (`offset`, `limit`).
        The example that comes with the app (`examples.py`, Human cells) is there from the first launch, so the empty
        Stage's "open the examples in the Library" is true before any download: `Library._bundled` copies it to
        `snapshots/examples/human-mitosis-v1/` (a folder the bridge plays from) and registers it once under the
        walkthrough's key (`examples.KEY`), so the walkthrough opens the same asset.
      - **Categories first** (`categories` on the route: CONTEXT.md "Category"): the library's fixed top level
        (`library.CATEGORIES`) with each one's newest as its cover and how many, "Not sure yet" last (opened at once when
        it is all there is); a category's assets under the names the looks gave them (`category=` on the route).
      - **The looks** (`library_looks.py`): only when the user turns them on (option LIBRARY_LOOKS, off by default:
        Will, 2026-10-13, an open-source app sends none of a user's pictures until they choose), one fast vision call
        (the setting `fast`, else `vision`, only when it can run here) on each new asset's small picture, told its record's specimen and what the running look saw while it was on
        stage, picks a category and names what it is; an example's specimen is looked at once for all its clips; the
        user's category (asset-meta `category`, `categoryName`) wins and is never looked over. Logged as `library-look`;
        `MICROMIND_LIBRARY_LOOKS=0` stops them (the tests).
      - **A click previews** (`GET /api/library/{id}`: its details and `sessions`; `/file` plays it): previewing touches
        no session. **Add to this session** puts it on stage (`source {open: id}`), which makes it the session's, and goes
        back to the workspace (ADR 0022's amendment: adding associates).
      - **Move to trash** (`asset-trash`, Undo `asset-restore`), after a confirm naming the sessions that use it: out of
        the library and every session's list; the work made from it stays; a file the library holds moves to
        `added/.trash/`, never deleted. On stage here, the live camera takes its place.
      - **The sim scenario `library`** checks it: a preview adds nothing, Add associates, the trash leaves both sessions'
        lists and Undo brings it back.
      - **The agent reaches it** (`brain/toolbox/library.py`, family "library"): `assets` (query) looks by words, kind,
        category, whose and how recent, and puts nothing on stage; `add_asset` (effect) puts one on stage as the user's
        Add does (`App.put_on_stage`, the `source` command), only when the user asks to look at it ("show me last week's
        rotifer recording").
      - **The user's own recordings play as assets everywhere:** the Test footage page opens a real or added one as the
        asset it is (`assetFor`: its file among the user's own), never as test footage.
    - **A switch shows nothing of the session left** (`session-view.ts` StageGate): a click on another session (or its
      `session-cleared`) empties the stage at once and says only "Opening “…”…"; no frame is drawn until the landing's
      `source` reply (at most 8 s), and none the bridge took before it. Every landing ends in a `source` command (live
      too: `switch: false` when the camera is already on), carrying the landing's `session`. The server runs `source`
      commands one at a time in the order asked and refuses one whose session was left before or while the bridge
      switched, so switching quickly ends on the last session's own source; it looks at nothing of the new session until
      then.
    - **The sim scenario `sources`** checks it on the sim's own feed:
      - a Record video joins the session's sources and the library, and plays held;
      - a snapshot's picture plays as a still at the bridge's rate, and a click on it outlines;
      - its user-given specimen is the subject and its line of metadata, and its objective is the scale;
      - removed, its specimen leaves the session's, and Undo brings it back;
      - after a new session and back, the first lands on what was on stage;
      - then the sim's feed is put back and the session told "live".
  - **Conversations** are inside a session as before (`new_thread`, `switch_thread`). A different specimen within a
    conversation is recorded in it as a new subject, not a new conversation.
- **The list of sessions** is read from their journals (`day/catalogue.py`), never kept as a second store. It reads
  only what grew, off the event loop. Each session has its name (the user's, else the app's from its first specimen
  or request), when it began and was last used, the days it touched, its conversations, what was seen, its sources
  (opened and not removed, counted; those that joined on their own need the library's scan, so the list leaves them
  out; its scientific inputs, imported files and folders, are added by `App.sessions_wire` from the scientific store,
  F-L8-6), its researchers and what it made (`product_of`, the one rule: the products Made in this session lists).
  Every "made" event is also its claim (`Info.claims`), product or not, so a Record dataset is known to be its. Footage
  held for a researcher is memory only, so the list never counts it; the open session's own wire adds what is held now.
  The studio shows it in one place, the session's title (`session-menu.ts`, the `sessions` wire): the title is the
  session's name ("Untitled session" until named); its menu has a search and + New session, this session (renamed in
  place, what it holds), the app's suggestion, and every session newest first by when each began under day separators,
  each with a picture of what it last looked at (`Info.picture`: its asset on stage when last used, else the newest it
  added, else the camera's icon), its name, when last used and "3 assets · 5 made · 2 researchers"; ✎ renames any. On
  the screen report it is kind `sessions` ("close the sessions list").
  - **The side bar is the session** (`sidebar.ts`; once the sessions rail): a 64 px labelled rail on the live view's left
    with four toggles (Assets, Made, Researchers, Activity; each a 56x52 target, its icon over a short word, with its
    count, since the user missed a slim icon strip); one opens its section in a 300 px column beside it under the
    session's name (its toggle again closes it, another switches; the section is remembered per viewer). Each section
    is drawn by its own view, once: the session's assets (sources.ts), Made (madetoday.ts, with its filters and hidden
    windows), Researchers (researcher-cards.ts, compact), Activity (the chat's own list, chatlog.ts, so an answer's ⓘ
    opens it at its request). Under them, as wide as the whole side bar, Actions (above), its header bar the divider
    (dragged to share the height; dragged down or clicked, it closes to that bar; Will, 2026-10-15), the four above a
    row of icon tabs (the open one a lit pill with its word); the panel icon hides the whole side bar to the rail as the
    chat's hides the chat, and brings back what was open; a first visit opens on Assets with Actions at 30%. Reserved in the panel stack so the eyepiece glides aside; a sheet with the five as tabs on a phone; on
    the screen report as kind `sidebar` (`shows` "actions" with Actions alone).
- **One store, one source of truth** (`orchestrator/day/`): every turn, note, subject, summary and researcher
  started is an event in the session's journal (`services/sessions/<sid>/journal.jsonl`, git-ignored, kept), and
  `SessionStore` (`DayStore` is its old name) is rebuilt from it, so a restart comes back to the same session,
  conversation, turns (ids the session's own, never reused) and subject.
  - **Where new files go:** `store.date` is today by the clock, and per-day stores file there (`evidence/<date>/e3`).
    `store.days` are the dates the session touched; `store.holds(date)` says whether a date is one of them.
  - **Ids** the user sees (`e3`, `c12`, `m2`, `d4`, `b1`, `i5`, `k1`, and a dataset table's `tb2`) are new both for the
    session and for the date's folder (`day/ids.py`), so two sessions of one date never share one. `catalogue.ID` reads
    `tb` before a single letter, so `tb2` is never taken for `b2`. Dataset table allocation durably reserves a
    `made` ID with `kept: false` before asynchronous work; unsuccessful saves leave gaps, never reused IDs.
    Dataset table publication checks its captured Project/load generation and keeps its originating conversation.
    Its bounded snapshot/API contract is in `docs/architecture/session-index.md`.
  - **Work asked in one session is filed in it** (`Asked.session`, journaled with every turn; `store.elsewhere`): a
    researcher still working after a switch, a late tool result or a late answer goes to its own session's journal,
    and the session open gets one line. Work filed from another thread (a population's or tracks' save on its writer
    thread, a result, a made id) chooses its session and writes to it under the store's switch lock (`_filing`,
    `_switching`), which a session opening also holds, so a save finishing during a switch is never written into the
    session opened after it chose (CI, 2026-10-26: 1 run in 8 of a motion-tracks test). Listeners hear the switch
    after the lock is released.
  - **The user's preferences are not the session's** (`day/prefs.py`, CONTEXT.md "Preferences"): who leads, the
    look of outlines, the level, the research cap's amount, the models chosen from now on and the scale are kept
    until changed in `services/sessions/preferences.jsonl`, beside the session folders, so a new session, a switch
    and a cold start keep them. Only the session's own (`prefs.SESSION_KEYS`: the shelf's removed chips) stay in its
    journal. `prefs` is one dict of both, which every reader uses, and listeners hear a change as the same
    `preference` event (memory, datasets).
  - `brain/state.Conversation` is a view of the active conversation.
  - **Paths:** where everything is saved is decided in one place (`orchestrator/paths.py`). `MICROMIND_HOME`
    (default `services/`) holds sessions, research, snapshots and logs, and each `MICROMIND_*_DIR`/`_LOG` variable
    still moves its one path. Tests and the sim harness point `MICROMIND_HOME` at a temporary folder, so the real
    logs hold only real use.
- Memory in layers, so a conversation of hours costs a request what a short one does:
  - the live view;
  - the active conversation's last 8 exchanges;
  - its running summary (older turns folded in by the fast model in the background, oldest first, `day/memory.py`);
  - a digest of the rest of the session (other conversations, specimens the user named, preferences, researchers'
    work, facts settled in it; at most 900 characters, `day/context.py`);
  - `recall`, a query tool over the session's turns, summaries and researchers' work across its days, used only when
    the user refers back. With `session`, it reads another session the user names without opening it ("what did we
    find in Tuesday's blood smear session?").

  The common "what's that?" stays one call.
  - **Settled facts carry a source** (`memory.settled`): the user said or agreed to it, or a researcher reported it
    with a source. The app's own guesses are never settled. They are the session's.
  - **Memory is past tense:** summaries and the recap say "earlier, not now".
  - **Recall says where it looked** when nothing matches.
- Which conversation a line belongs to is decided once (`Asked`, day/model.py). A request fixes the session and
  conversation it was made in when it starts. Its answer, notes, late tool results, spoken reply and the researchers
  it started belong there, even if the user has switched since. A researcher's result goes to the conversation that
  started it (`origin.thread`), and the one going on now gets a line.
- Within a conversation the subject changes freely (`day/subject.py`, the only writer of the scene's
  `context["specimen"]`; the store keeps it).
  - The running look is never told the old subject as if it were still in view: it is asked `same_as_before`, and
    when two looks in a row say no, the subject is updated and the brain and voice are told the view changed.
  - A specimen the user names (`name_specimen`) is kept with the conversation across restarts, and names the
    session when it has no name yet.
- The orchestrator is built once in `orchestrator/app.py` (`App.build`: every module and how they are wired).
  `server.py` is only its routes, and the studio's commands are one handler each in `orchestrator/commands.py` (a
  command the brain can also do runs the brain's tool). `make day-report` shows a day: its sessions, conversations,
  researchers and versions, spend and voice timing.

Typed saved-image Ask turns retain `refs.scientificInput`: the accepted native source reference,
revision digest, transport digest and validated scientific descriptor. This closed, bounded JSON
receipt is written with the turn and read back unchanged; reopening never resolves it against
the current Project or substitutes a newer revision. It contains no pixels.

Table analyses (`an<n>`) and graphs (`gr<n>`) reserve IDs before publication and journal immutable
`table-derivative` revisions under their captured Project. `table_derivatives` history restores alongside
`datatables`; current source/snapshot contracts live in `docs/architecture/session-index.md`.

Evidence and Tracks publication uses strict journal writes. Disk-full, permission or
encoding failures propagate to their save owners before applying a saved event in
memory; buffers remain available for retry and the UI must not say Saved. Ordinary
conversation events retain their existing in-memory fallback. Regressions inject a
failure at the real journal append boundary and verify retry/reopen, not a replacement
recorder that bypasses the store's fallback policy.
