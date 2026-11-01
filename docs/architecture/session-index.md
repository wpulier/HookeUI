# The session's index: everything made, read one way

Part of the working context the root `AGENTS.md` maps. "Above" and "below" may mean parts it lists in
other files.

- **One index** (`orchestrator/resources.py`, `hub.resources`; Amendments 2.7 and 3 of
  `docs/design/2026-10-06-captures-operations-evidence.md`). Everything the session made is in it:
  - evidence (populations, and motion tracks: `m1`, kind "tracks");
  - results (what tools measured or made);
  - researchers' work (each report, and its lessons, videos, pictures, findings tables and charts and the sources it
    cited: below) and snapshots;
  - things named today (`seen.py`) and labels taught (`taught.py`);
  - the scale's calibrations, datasets and recordings.
- **It reads each store where it is,** and never copies it.
- **Every reader reads it the same way:**
  - the brain, through the `resources` tool (`what data do we have?` gets the Project's one view, below; `id=` gets one
    entry in full);
  - researchers, through their read-only `session` tool, at `/for-researchers/api/resources…` (`server.main` sets
    `ResearchManager.resources_url` to it): the same entries as they read before ticket 66, with no Data fields, and a
    researcher's sources, tables, charts and other work only when it asks for that kind. Its `summary` also carries
    `project`: the first 12 data items, the researchers and the Studies of the one view;
  - the studio and programs, through `GET /api/resources`, `/summary`, `/{id}`, `/{id}/picture` and `/{id}/masks.npz`.
  - Made in this session's cards, through `/{id}/thumbnail` (`orchestrator/thumbnails.py`): every product's small picture,
    made from what it keeps (cell outlines on their captured view with the cells drawn, motion with its tracks, a
    researcher's picture or starting view, a followed cell's newest crop, a screen recording's poster frame), one at a
    time on a background-priority thread (ffmpeg as a background process), kept in memory only by the version the
    studio asks for; none (the kind's icon) when there is nothing to make one from. A source's (a Record video, a
    snapshot, a clearer picture) is the library's, `GET /api/library/{id}/thumbnail`.
- **The Project's one view: `project.state`** (`orchestrator/project_state.py`; its contract is
  `contract/agent/project-state.schema.json`, `contract/AGENTS.md`). Project context lived in four places (the journal,
  this index, the scientific ProjectStore with its Studies, the studio's own storage) and was read three ways; on
  28 Oct the Assistant answered "only the original image, no measurements" beside a measurements table and three
  researchers' findings, and called the running Researcher 3 cancelled. Every reader now shares one projection:
  - **What it is:** a scoped, read-only projection of existing records, never a store. It reads this index (one pass:
    `Index.one_pass`), the researchers (`Index.researchers_of`), each Study through `research/efforts.summary` (the read
    under `Studies.list`, from the index's own scan of the ProjectStore; `Studies.read` is not used, as it can settle
    receipts), the door's operations (in memory; the door's own reads are not listed as work), the saved Tab arrangement
    (`workspace_layout`, read, never migrated) and the studio's latest report. Nothing is written and nothing is kept on
    disk; it is never an instruction source.
  - **What it carries:** the Project's identity (as `project.read` names it) and the revision of each source it was built
    from; `observedAt` and a consistency statement (the reads are not atomic); its stage (`stage`: what it has on stage
    and the specimen it last saw, from its journal's `on_stage` and subjects); data, newest first, each item with its
    exact reference (kind, id, revision; a Project document's digest), category (table, measurement, outline layer,
    import, researcher's findings, Study result, workflow result, analysis, note, other), source store and whether its
    words are trusted, its maker (`resources.maker_of`, "not recorded" when the record cannot tell) and model; a page
    of at most 50 with a cursor bound to the data's revisions only (the index's and the Studies', as this role reads
    them): data that changed refuses an old cursor, so pages never mix two states, while a window opening or a
    researcher's next step does not; researchers in one order, `project_state.ordered` (running first with its step, then finished, then
    stopped, labelled "stopped before it finished; not running"; newest first in each); Studies; agents' operations;
    the saved Tabs; the app's model choices (role -> model, from `options.Settings`, only for the Project open now:
    never inferred for another); and the studio's state.
  - **The studio's state** comes only from its latest report (`screen.latest`): the Tab open, the windows of this
    Project and the one in front (`screen.frontmost`). With no studio reporting it is `unavailable` ("no-studio"),
    never empty; a studio showing another Project, or a report that does not say which, is `unavailable` too, and the
    other Project is never named. Its revision is what the report shows, never when it said so: the studio reports
    again every few seconds unchanged, and that changes neither the cache nor a cursor.
  - **Permissions first, then the cache:** the in-app Assistant, the studio and the app's researchers read as the owner
    (`project_state.OWNER`); an outside agent reads by the door's registry (`door/projects.py`: creator or an explicit
    grant), checked before anything is read. The cache is one entry per actor and Project, keyed on every source's
    revision and the role, so no owner's projection is served to a contributor, and a revoked grant is refused while
    its projection is still cached. The revisions are read from the stores on every call (several change in memory
    without a write), so the cache saves the projection's building, never a read.
  - **Untrusted facts are kept, labelled:** a researcher's, an imported file's or an agent's words stay in the
    projection with `source.trusted: false`. The brain's text and the brief put them, the Project's name, a source's
    title and every agent's label only inside one `untrusted.block`; their lines say "titled below", "its name is below"
    or "by a connected agent". `Index.summary_text` says a saved Project document by its id, never its title.
  - **The owner's own:** what a researcher asks the user for and the conversations it belongs to are the owner's view
    only. Any other reader gets a researcher's row without `asking`, and its report as a data item summarised by its
    public line (`_public_line`: name, subject, status, the step while it runs, its finding), never `Session.line()`.
  - **A store that cannot be read is said, never fatal:** the researchers (`researchers' work (OSError)` in
    `unreadable`, as the index says it), the Project's journal and the app's model choices each degrade alone. If the
    view still cannot be built, the brain's `resources` says so and gives the index's summary.
  - **Readers:** the brain's `resources` with no id, kind or query (`project_state.text`: each item's line, the
    researchers, Studies, agents' work and the studio, then the index's own summary by kind, `Index.summary_text`, with
    each kind's count and newest, what is live and what could be made); `research_status` with no researcher
    (the running one first, else the one last spoken of in the conversation: the newest turn filed about it, `refs.rid`;
    then the rest in the one order); the voice roster (`brain/context.py` `_roster`, the first 4 in the one order, so a
    stopped researcher is last or left out); the researchers' `session` summary (its `project`, only for a session a
    researcher of this app is working in: `ResearchManager.working`); the door's `project.state`.
  - **The brief for the realtime voice and researchers** (`project_state.brief(state, max_chars=3000)`): the owner's
    view as one compact text, injected whole as one replaced item, never written to disk or kept as an editable file.
    At most `BRIEF_MAX` (3000) characters whatever the Project holds; sections in a fixed order, each in its own share
    of the cap (`BRIEF_SHARES`) of whole lines then how many more: the Project and its stage and specimen, data newest
    first, researchers running first, the Tabs with the Tab open and the window in front, the models in use. Then the
    words that are not the app's in one untrusted wrapper cut to what is left, so it always closes. It holds no time of
    reading, so an unchanged Project gives the same brief.
- **Pulled, never pushed into a prompt.** Entries lead with a plain title ("Motion · 18:10"); the id is one step away.
  The brain's `resources` tool lists, and its summary counts, every kind but a researcher's sources, tables, charts,
  lessons, videos and pictures (`produces.ASKED_FOR_ONLY`): those are many entries of the researcher's own words, so
  they are listed only when the tool asks for that kind (`kind=source`), or read by id. What the brain sees otherwise is
  byte for byte what it saw before ticket 66 (checked on a copy of a home with 12 real research sessions).
- **Every producer declares what it makes** in one table (`orchestrator/produces.py`): each tool, studio
  command and background producer. The declaration is an entry kind, "temporary: why" or
  "none: what it does instead". `tests/test_resources.py` fails on a producer without one.
- **Data's fields** (ticket 66; the studio's Data and reader are built from them). Every entry's wire carries:
  - `role`, one of Data's sections, from one map (`produces.ROLES`, kind, and a result's media: a reference photo is a
    source): "acquired" (Pictures and recordings), "analysis", "writeup", "source", "other" (bookmarks, calibrations,
    taught labels, projects), or null for a kind Data does not list (members, outcomes, facts, jobs).
    `tests/test_resource_roles.py` fails on a kind with no role.
  - `maker`, who made it, `{kind: "you" | "assistant" | "researcher", id?, name}`, derived in one function
    (`resources.maker_of`) only from what its record says, and null when it cannot tell (ADR 0006: never a guess, so an
    older entry shows no maker): a researcher's own work; `made_by.actor`; a researcher it was made for (`for`: its
    hands' runs, footage held or captured for it; its id only when the researcher's own `ran` or the clip says it); a
    record's own "by" ("user", "Record", "agent:chat"); a click. `made_by.actor` is recorded where a run begins:
    `ToolContext.actor` is "you" for the studio's own Run (`commands.run_tool`, its Actions, its manual controls) and
    the brain's chat and voice are "assistant" (`brain/registry.actor_of`, in `Registry.start`); the run keeps it
    (`ToolRun.actor`), and what it makes keeps it: its result entry ("actor"), each execution a job starts inside it
    (`operations.Execution.actor`, else "you" for a studio request such as a click; never for a researcher's hands, which
    say `made_for`), so its populations, motion tracks and named outlines, and its snapshot and bookmark files. A table
    tool run from the studio is recorded `by: "user"`. It is kept out of a model's reading of the entry.
  - `run`, the execution that made it when it came from exactly one (`resources.single_run`): a run's outlines and
    its answer share it and are one Data row (the studio folds them, `data-roles.ts` `dataRows`), as
    `workspace_views.reconcile` places one window per execution; a result over several executions (a comparison of
    models) is a row of its own.
  - `title`: the title Data shows. For a researcher's report it is the title of its result, and for its other work
    what it called it (`Entry.shown`): its words, so those entries are `untrusted`, and every line a model reads keeps
    the app's own words (`Entry.title`, "Researcher 3's notes").
  - A report's `forms`, newest first: its presentation ("Presentation", with the output's id, "writing" while its job
    runs, "ready" once made) and each findings version ("Findings v2"). A presentation is a form, never a row.
  - A source's `values`.
  These fields are Data's only: the canonical reading of an entry (`Index.document`, what a researcher pages through
  and whose digest it records as what it read, the read badges and the original-capture check) is `Entry.wire(listed=False)`,
  unchanged, so a read recorded before them still counts.
- **A researcher's work as entries** (`orchestrator/research_entries.py`, ticket 66 S4). Read from each research
  session (the fields the `wires.json` golden pins) and its folder, never written (the research runtime owns them):
  - `lesson`, `video`, `figure` (a picture it made): `<rid>:<output id>`, the id of the line's first version at its
    current version (`versions.py`), so a revision keeps the id; its `source.outputId` is the current version's;
  - `finding-table`, `finding-chart`: `<rid>:table-<n>`, `<rid>:chart-<n>`, n from 1 in its findings' order
    (`source.index` is the 0-based place in `result.tables` / `result.charts`); Analyses;
  - `source`: `<rid>:source-<12 hex of the sha1 of its address>`, one per page or web picture it cited (its result's
    `sources` and `refs`, its findings' sources, `sources.json`), merged by the page's address. `values: {url, site,
    title, authors?, licence?, credit?, picture?, unreviewed?}` (`title` is the address when no title was kept;
    `picture` the picture's own address); a saved web picture is its `pictures[0]`; `source.citedIn` names the report
    that cites it. Uncited pictures are deleted by the researcher's tidy and are never listed.
  None has a `/` in its id, so every `/api/resources/{eid}/…` route serves it: its picture (a picture it made, at its
  line's current version; a saved web picture), its thumbnail (those, and a video's poster frame; a lesson, table or
  chart has the kind's icon), and its detail. They are not products (Made today's list is unchanged), no journal
  event makes them, so `reconcile` never places them as windows nor counts them as completions or notices.
- **How it was made** (`orchestrator/resource_how.py`, S5): `GET /api/resources/<id>` with no offset (the studio's
  reading) answers the canonical document with the entry's Data fields and `how`: `{maker, at, asked?, method?: {name,
  implementation?, where?}, settings?, from?, usedIn?, citedIn?, sources?, versions?}`, links as `{id, kind, role,
  title, maker?}`. Each part only from a recorded fact: the words asked (a result's request, for the Assistant's); the
  operation and the implementation that ran and where it runs as the app declares it (this Mac, the cloud), or a
  researcher's model; `made_by.settings` and a capability's parameters; made from a result's input resources, a
  population's source picture, a table's sources, a chart's points, a report's evidence, its findings' session sources
  and what its hands ran; used in, the same records read the other way; cited in and sources; versions (a report's
  forms, a lesson's versions). A link is listed only to an entry the session still has; a paged reading never has `how`.
- **Cheap to poll** (S7): `GET /api/resources` answers `revision`, a digest of the whole listing (every entry's wire
  and what could not be read), so it changes when any entry changes. `since=<revision>` answers `{day, revision,
  unchanged: true}` and no entries when it is the same: the studio's 10 s poll sends 85 bytes and draws nothing. The
  listing is built and encoded in a worker thread (`Index.listing`), never on the event loop. `unchanged` is decided by
  building the listing again and comparing, not from change markers: about twenty stores feed the index and several
  change in memory without a write, so a marker that missed one would say unchanged when it was not. The build is
  cheap: one collect's nested reads are shared (the researchers, and the collects the read badges' documents make),
  so a listing reads each store once plus each researcher-read entry's document.
- **A document** (`write_document`, documents.py; on-screen.md "Documents") is a result entry with `media` "document":
  role "writeup" (`produces.RESULT_ROLES`), a product, untrusted (its words are a model's from records partly not the
  app's), `shown` its own title, its line made from what it keeps (`documents.summary_of`, never the receipt of the
  request that made it, which says where it was shown then), and its text read from its file (`documents.text_of`, the
  entry's detail; its values are not read twice). In `project.state` it is category "other", not a measurement.
- **Saved scientific measurements** (`measure_saved_objects`) publish a typed table in the accepted Project,
  exposed by the index as `project`. They do not duplicate that table as a legacy `result` entry.
- **Results are built from the tool run itself.** No tool has storage code of its own:
  - a measurement it logs (`log_measurement`), a picture it shows in the magnifier and the executions it
    used are attached to its run (`registry.attach`);
  - a finished run of a tool declared "result" becomes an entry (`Results.from_run`);
  - the entry is filed in the day's journal, with its pictures under `results/<day>/` (`docs/saves.md`);
  - a watch that fires is one too.

`retain_population_labels` publishes a scientific Project containing the retained source file, original native image
and corrected instance-label array. The common resource index lists that accepted Project; it is not a legacy model
Result. `measure_saved_objects` can consume its exact labels revision and publish a typed table without invoking the
original producer. The data-only document retains attribution in `populationSource` and never launches a worker on open.

The declared `compare_retained_methods` capability produces a `scientific-method-comparison` Project document.
It retains exact native source/arm references, histories and method summary, then a separate choice revision with
its actor and rationale. Describe makes no resource. The document is read through its owning method workflow;
opening it never launches the producing software. Existing Project bundles retain every named dependency.

A dataset table (`tb<n>`, kind `dataset-table`; `services/orchestrator/datatables.py`) is the user's editable layer
over measured tables. It pools populations, motion tracks and capabilities' tables, each read through
`tables.read_table` at its own revision and in its own unit. A source with no scale beside one in µm is refused, and the
refusal names the source. Rows are keyed `<sourceId>:<memberId>`, so a condition column, a value set by hand or a row
left out (with its reason) survives a removal, a merge (a merged cell takes what its parts agreed on) and an undo.
Computed columns are a fixed set (ratio, difference, log10, scale). Every edit, and every correction a following table
reads in, is a new version journaled as a `datatable` event. Each version keeps its own rows in
`results/tables/<session>/<id>/v<n>.json` with their SHA-256, checked on every read. A table follows its sources by
default: a correction starts a debounced update off the event loop. Freeze is explicit. Its freshness is current,
updating, stale ("e3 corrected → revision 2 · Update") or frozen. Routes: `GET/POST /api/datatables`,
`GET/PATCH /api/datatables/<id>` and `GET /api/datatables/<id>.csv`.
POST/PATCH send the originating Project ID as `session` in JSON; targeted GET/CSV send it as a query parameter.
The list response supplies `session`. Clients capture the existing Project ID at dispatch and discard responses
when that UI owner changes; they do not send a local Tab generation. The server captures its own Day identity,
load generation and conversation, refusing a Project switch/reload across awaited work. Follow timers retain that
same owner. `update: true` also compares the requested version inside the table lock.

Table IDs are durably reserved before asynchronous saves. Snapshots publish exclusively; a rejected write cannot
replace or remove an accepted revision. An interrupted write may leave a reserved ID gap. Conflicting column types
or units, including x/y coordinates, refuse pooling. Admission bounds rows, cells and snapshot bytes in
`datatables.py`; hashing, table transforms and HTTP response encoding run off the live event loop. These are
bounded table contracts, not large-table performance qualification or D9 ordinary-UI acceptance.

Accepted table derivatives (`table_derivatives.TableDerivatives`) share one store for `analysis` (`an<n>`) and
`graph` (`gr<n>`). `create(kind, table_id, table_version, spec, owner=...)`, `refresh(id, version, owner=...)` and
`set_mode(id, version, mode, owner=...)` are async; `read(id, version, owner=...)` runs off-loop. Evaluators are
registered by kind and receive the same immutable table snapshot plus explicit settings, with at most two running.
The server owns routes and passes the captured DataTables owner; callers never supply paths or computed success.

Each `table-derivative` journal record carries id/kind/version/title/mode, exact
`source: {session, id, version, sha256}`, explicit spec, snapshot path/hash, author and conversation. Its immutable
snapshot is `{format: 1, kind, source, spec, result}`. Exporters verify both hashes and matching source/spec; reading
or reopening computes nothing. Follow reacts to saved table revisions; failed evaluation preserves the previous
result and reports stale with a reason. Freeze saves a new revision without evaluating; explicit refresh may update
it while retaining frozen mode. Changed Projects refuse publication. New IDs are durably reserved; tables and
both derivative kinds use the same bounded atomic, exclusive snapshot publisher.

A graph may name `analysis: {id, version}` in its settings. The shared store requires an accepted analysis
from the exact same Project/table/version/hash and reads its verified snapshot; only its accepted result is
passed to the graph evaluator. The graph evaluator checks compatible selected measurements/groups before
displaying numerical annotations. Caller-supplied computed results are not accepted.

`DataTables.from_comparison(tab, layout_revision, owner=...)` captures one saved schema-5 A/B/C Tab.
Its lanes must name distinct retained populations at exact revisions, with actual implementations and a shared
capture identity. It reads each through `tables.read_table` and the ordinary pooled-table publisher. No pixels
are converted and no algorithms execute. Native scene-only lanes must first retain supported measurements;
unsupported or stale lanes refuse. The initial table is frozen and has a source-based Method category rule.
Its immutable snapshot keeps comparison origin/arms, actual `sourceRevisions`, and the warning “Same objects,
not independent samples. Method groups do not establish matched object pairs.” Explicit table Update/Follow
still uses the shared path and rechecks each source's captured input and implementation. The table's accepted
source revisions remain authoritative if later corrections differ from the original Compare Tab selection.
Display and exports must carry the warning; the method column is not an inferred pair key.

Imported source documents expose the intake publisher’s retained `sourceImport` actor
and operation as Data’s `madeBy`. Older documents without recorded provenance stay
unattributed; the active connection never becomes the inferred author.

Project material metadata comes from `project_material.describe`: declared document kinds and accepted output summary formats, never titles. Both resource wires and project.state carry that same projection. Metadata listing does not claim to have verified all payload bytes or qualified a reader. Unknown imports remain Files; matrix result formats distinguish retained response models from analysis output. Workflow attempts carry the original run ID for grouping.

Workflow history items expose their own recorded author through the same maker projection:
the definition's `createdBy`, the run's initial `actor`, or the attempt's executing `actor`.
Later controls, reviews and recovery never replace that identity. Legacy records without
an author stay unattributed; a display label does not authenticate a connection. Reading
history performs no migration or execution and leaves accepted revisions unchanged.
The shared author contract distinguishes `system` work as Hooke system. A legacy
`agent:researcher` is a researcher with identity not recorded, not an external
connection or an invented researcher ID.

Material organization is an accepted exact-reference membership record, published by
`project_material_edit.organize` from Studio or the declared `material.organize`
agent action. It records the role/group, input selection, organizing actor and exact
basis. The input’s scientific head does not change, including for a historical Study pin.
Scientific kind, producing provenance, original bytes and historical revisions
remain unchanged. Runtime/resource checks and revision conflicts stay with ProjectStore.
Study-pinned dependencies remain Inputs even when a later revision is not selected.
Group names are human/agent-supplied context, never inferred scientific classifications.

## Portable result storage adapter

Results resolve their original Project before retaining values or image bytes.
The optional ProjectLocations resolver stores the existing date layout under that
Project's artifacts/results; legacy Projects keep paths.results. Background writes
capture the original session, pending saves are scoped to their store, and saved
reads follow the journal owner after relocation. Changed/missing result folders
refuse rather than recreating evidence. App activation still waits for the other
legacy asset/research stores; this adapter does not certify complete collection.

Study assignments are working material from creation, before findings exist.
`project.state` joins each authoritative Study once into its data list with the
same material metadata as the resource index and its exact Study reference. The
separate Studies summary remains navigation/status for that same object, not a
second output. No finding or scientific completion is inferred from an assignment.

Retained Project media has one scientific inventory entry. `_sources` omits the
legacy Library alias for an exact `source_references` binding, including inactive
Project reads. Inputs/Data join the source’s retained metadata to that exact record;
a matching host Library ID cannot contribute another item or replace its identity.

Accepted agent-import provenance supplies its recorded display label to the shared maker projection. It takes precedence over a current host label and remains available after folder restoration without connections. This is historical authorship only; reading it never registers or authenticates an agent. Legacy records without a label keep the host/unknown fallback.
