# Capabilities: the one list

Part of the working context the root `AGENTS.md` maps. "Above" and "below" may mean parts it lists in
other files.

`contract/capabilities.json` lists everything the app can do, one entry per thing per actor (you,
the agent, on its own, a researcher), with the model roles behind it and where it is measured
(ADR 0007; CONTEXT.md "Capability", "Role", "Verdict"). `orchestrator/capabilities.py` checks it
against the code and builds the one wire the Capabilities page and the agent's `capabilities`
tool both show. A new tool, studio command, researcher deliverable or option group needs an
entry (title of at most 5 words, what it does in at most 12, an example to say, its roles; a
role's first entry is the one it cannot do without, `needs` names a setting it only runs with):
`tests/test_capabilities.py` names what is missing. The user's verdicts (works, broken, not yet) are written by the
app only to its own log (`services/capabilities/verdicts.jsonl`, git-ignored; the `validate` command);
`make verdicts` moves them into `contract/verdicts.jsonl` (append-only, merged by union) by a PR from a
temporary worktree, so the live checkout is never edited. Run it before updating the live checkout. The Toolbox's
Everything it can do view (`#toolbox/all`, `apps/studio/src/capabilities.ts`) draws the wire inside the Toolbox frame (its
tabs and the Keys band above it). By family (Will, 2026-10-15: the folded row per family was too spread out), every family
is a tile at once, packed in columns, nothing folded: what it covers, "N of M run here" (by `available`, never by your
verdicts), "B broken" when
any, and each capability as a chip with your verdict's dot (dashed when it cannot run here; two of one title say who: "by
you", "by asking"). Fiji is a tile of its own: the functions connected, each chip dashed unless its own row is ready, apart from what only
installs (the same two lines as the Toolbox view's). Search, Who and
Show filter tiles and chips. A click on a chip opens its card (what it does, how to ask, what powers it with each power's
mark: cloud model, model on this Mac (`runs`, from the option's components' kind in `licences.py`), approach, algorithm or
app code; its options as a switch with the one in use outlined green, or "fixed in the code"; its file, whether the agent
can change it, every capability it powers; its evidence and the verdict buttons; a preview from `docs/capabilities/media/`
when there is one: `make capability-clip ID= FROM= START= DUR=` cuts one from a recording, cropped to leave the chat out).
By model lists the roles as cards; preferences sit under the tiles with their values. The gear's two pickers draw only the capability list (one source: the
`capabilities` message; `settings` carries values for what draws with them, and no option lists) and
split the roles by kind (`models.ts` `pickerGroups`): "Models" (the vision model's name on its row)
lists the roles of kind "model", where a change changes the model itself, plus "Compare with", and
under "Fixed in the code" the models no menu chooses; "Behaviour" lists the methods and policies (the
same models, used differently) and the user's own preferences (speaking up, explaining, research
spending, the objective). Each job says what it is used by and what it takes over when another model
can't run. Every setting is in exactly one; a model role whose options run no model fails
`test_capabilities`.
- **Models fixed in the code** are roles `fixed:...`, read from the code's constant, with a `label`
  that must name it (checked) and, when the code takes one, a `fallback` (`when`; the wire says which
  is active: lessons are Claude Sonnet 5.5 with an Anthropic key, else GPT-6.1 Sol). A model named in
  the code (a `...MODEL`/`ENDPOINT` constant) that is no option and no fixed role, or a model passed
  as a literal, fails `test_every_model_named_in_the_code_is_in_the_capability_list`.
- **One name per model** (`orchestrator/names.py`, CONTEXT.md "Name"): each implementation's and option's label
  is its one display name, with no commas and no place it runs unless two variants differ only by that
  ("SAM 3.1 (cloud)", "SAM 3.1 (this Mac)"). Where it runs is its own field (`runs`), said beside the name the
  same way everywhere (`names.said`, the wire's `said`: "EdgeTAM (on this Mac)"; the Models menu and the agent's
  model lines show it). `tests/test_names.py` checks that every model name any model is shown (goldens, tool
  descriptions, every model line) means exactly one thing, and that no key means two.
- **Available means it can run:** an implementation whose code must be installed here declares it
  (`Impl.installed`, a key of `operations.INSTALLED`: micro-sam's own Python, SAM 3.1's `sam3`, Cellpose,
  open_clip, Trackastra), and both availability checks (`operations.availability`, the options' catalogue) say
  "not installed here (what: how to install)". Weights not downloaded are unavailable too, with a Download step.
- **The app's own check of each model** (`orchestrator/model_checks.py`, the user's Q8 and Q20 of 2026-10-11): every
  option of a model role, every method option that runs a model of its own (pointing, dissect, following, the voice's
  link, the lesson and claim checks) and every fixed model (its fallback too) is an entry, run once on the assets
  store's leaf frame through the app's own code (the runner, origin "check", never an outcome; the GPU workers in
  their lowest class, "research"; the app's provider classes for the cloud), one model at a time, a model it loaded
  let go after (`unload`) unless loaded before or kept by the live view. Its state (works; broken with the error's
  words; not installed; needs a key; not in this edition; not checked, with why) is one line in `paths.checks`
  (`checks.jsonl`, a performance log), the latest per entry counting. The wire carries each option's `check` (a
  fixed role's on the role, its primary and its fallback), the Capabilities page shows it beside the verdicts
  ("Last check: works · 02:41", "Broken: why"), the agent's `capabilities` text lists the broken ones ("<name> is
  broken here: <why>"), and `use`/`set_default` refuse a model whose last check on this build was broken (one from an
  earlier build is said, never refused). "Check now" on the page (command `capability-check`) runs the models on this
  Mac inside the live app, never the follower's model (its session is the live view's), and never unloads a model
  the live view keeps loaded to make room: a model that does not fit in the memory free is "not checked: inside the
  live app it needs 12.2 GB of memory and 11.4 GB is free ..." (`loads.sparing`); `make capability-check`
  (with the app stopped, BESIDE_LIVE=1 otherwise; DOWNLOAD=1 lets weights download) and `make
  capability-check-cloud` (keys; its cost said; VIDEO=1 adds the video model, Gemini Omni 1.1 Flash, about $0.40) run them from `bench/capability_check.py`
  and keep the result where the live app reads it. The local check must pass before a model-related PR merges; a
  change to `services/uv.lock` or `pyproject.toml` is model-related (every built-in model runs in that Python), and
  the check's last line says which Python it ran in and how it differs from that lock (`shared_python.drift`).
  The check is not the only evidence a card reads: a real use of the model here that worked is too (`observed.py`
  "use", catalog/AGENTS.md "Observed, not declared"), so a model in use says Working here, never Not checked. A real use
  is never recorded as the check: `use`/`set_default`'s refusals, a click trial's gate (`capability_shelf._proven`) and
  "Last check" on the Capabilities page still read the check alone.
- **Fallbacks** a capability takes are listed with it (`fallbacks`: when, what happens, the roles), only
  as the code does them (find all like this: the vision and outline models when SAM 3.1 can't run
  here, not when it finds nothing; `test_finder` pins it). A capability whose own model can't run
  but whose fallback can is available (`viaFallback`).
- **What tools really read:** every tool run records the settings it read (`options.recording_reads`,
  `ToolRun.reads`, kept in the interaction log's actions); every one the acting bench's runs read
  must be a role or fallback role of that tool's capabilities (`test_what_tools_really_read...`). A tool that changes a setting declares it
(`Tool.sets`), which is how the page shows what the agent can change. Every capability also names the code it runs on
(`parts`: the camera bridge, the slide tracker, the outline store..., each a file with one line of
what it does), so nothing on the page is without what powers it; a part nobody uses, a file that is
gone, or a capability with no parts fails the test. Each family says in a line what it covers.
- **A run with no studio** (`orchestrator/capability_runs.py`, hosted ticket 01): `run_on_resource` is the door's second
  caller beside `run_capability`, for a caller with no studio, camera or screen (the hosted compute worker). It uses the
  same registry, parameter check, input reader (`source_images._read`, refused unless its sha256 is the one authorized),
  runner (an explicit choice, raw, origin `headless`) and result store, and checks that the output is what the
  capability declares. An implementation whose adapter runs in its own process (Fiji, python-env:
  `kinds.Backend.own_process`) needs no GPU worker to be available.

`agent/study-question` and `agent/study-answer` describe retained correspondence: original
actor, selected contributor, exact Study basis and exact answer evidence. No schema field
implies execution readiness, verified scientific accuracy or human review. Local conversation
routes enforce these schemas. Hosted services without this path must remain explicitly
unavailable rather than inventing a different meaning or launching a provider.

- **Who's powering what** (`orchestrator/models_in_use.py`, Part 2.5; live 2026-10-28 the voice said "Gemini and GPT"):
  one row per job (the brain, which is the Seeing model answering by the Acting method; the voice; seeing; the running
  look; quick decisions; pointing; outlining; cells; look-alikes; researching; presentation; pictures; video) with its
  model's one name, cloud or this Mac (`runs`), the typical time of that model's calls here in the last ten minutes
  (`research_worker/api_usage.latency`: a cloud call by its API name in `attempt`, an operation's run by
  "<setting>:<option>" in `operations.Runner`; none is "no calls in the last 10 minutes", never a stale number) and where
  to change it (the Models menu's job, and by asking when `set_default` can). Read from `options.GROUPS` and the settings
  through `to_wire` (a list, never the work in progress's reads), so it is cheap enough for every health poll. The
  agent's `models_in_use` tool says it in words; the performance meter's panel shows the same rows ("Doing each job
  now", `/api/health` `models.inUse`).
- **On is not chosen** (`orchestrator/capability_shelf.py`, `catalog_view.py`; live 2026-10-28: "I just enabled BioCLIP
  2" was answered "I've set BioCLIP 2 as the model"): turning a capability on prepares it and offers it; the job's model
  (the setting) changes only when someone chooses it (`use` for one request, `set_default` or the setup sheet's Use for
  from now on). Every word the agent reads says the two apart: the shelf's line ("chosen (its model): DINOv2 small; also
  on, not chosen: BioCLIP 2"), a model's uses ("Look-alikes (not chosen: the job uses another model)") and what Turn on
  says once it is on (`on_words`: "BioCLIP 2 is on for Spot look-alikes, not chosen: it still uses DINOv2 small. Use it
  for one request, or make it the default?"). When the job's own choice can't run, the first option on is offered to use
  now: the card's `offerUse`, and the setup sheet of an entry turned on from its own card ("Use it for Look-alikes",
  `modelspage.ts` `useForJob`, through `capability-select`).

## The agent contract's Project view: `project-state`

`contract/agent/` holds the shared JSON schemas every door answer is checked against (`door/schema.py`, which lists them
in `NAMES` and needs no dependency, so the hosted side can use the same files). `project-state.schema.json` is
`project.state`, version `project-state/1` (its `contract` field; a change that is not additive is a new version): the
Project as every reader sees it, built by `orchestrator/project_state.py` and described in
`docs/architecture/session-index.md`. It is a scoped, read-only projection, never a store or an instruction source:
the Project's identity (`project-reference`) and each source's revision, `observedAt` with an explicit consistency
statement (the reads are not atomic), its `stage` (what it has on stage and its specimen, from its journal), data items
each with an exact reference (kind, id, revision, a Project document's digest), its source and whether its words are
trusted, and its maker, in pages with a cursor bound to the data's revisions only (the index's and the Studies' as the
role reads them: the studio's repeated reports and a researcher's next step never break a cursor);
researchers running first, then finished, then stopped (labelled); Studies; agents' operations; the saved Tabs; the
app's model choices (only for the Project open now); and the studio's state, `unavailable` when no studio reports this
Project, never empty (with its open Tabs as the Tab strip reports them: each Tab's kind, whether it is in front, its
specimen and what it holds by kind); its revision holds no time. A field added
to it is additive within `project-state/1`, as `stage` was. The door checks every `project.state` answer against it before returning it; `project.read`'s
output is unchanged. `tests/test_project_state.py` checks the schema, the permissions (no cross-Project leak, a
contributor's own view and cache, an unrelated Project invisible), the unavailable studio state and the revision-keyed
cache.
