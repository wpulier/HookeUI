# The catalog: models, keys, setup and the Toolbox

Part of the working context the root `AGENTS.md` maps. "Above" and "below" may mean parts it lists in
other files.

The design for many more models (`docs/design/2026-10-11-model-runtime.md`): these are sub-points of "Loads are
said, measured and kept within memory" in `services/gpu_worker/AGENTS.md`.

- **One folder per model in the catalog** (`catalog/models/<id>/model.toml`, `catalog/README.md`; ADR 0018,
  `docs/design/2026-10-12-the-open-catalog.md`): its name, version, licence, what it does, where it comes from
  (pinned), what it needs to get and to run, the operations it implements with their data types, its credit,
  workers, host, documented size and load limit. The worker's tables are read from it, and `test_catalog` fails
  when the code disagrees with it. An entry that can't be read is contained; the user's own entries
  (`~/MicroscopeMind/catalog/mine/`) are namespaced `mine/<id>` and listed, never run from there. Cloud models have
  folders too (`runs = "cloud"`, their `service` in `catalog/services`, whose keys are `keys.KNOWN_KEYS`); an option
  names the model it is a use of (`Option(model=...)`), an implementation the one it runs (`operations._model`), and
  their key, licence, name and version come from its folder. No model is named in the code outside the catalog.
- **Keys, one lifecycle** (`orchestrator/keys.py`, `keychain.py`, `key_tests.py`, `key_admin.py`; `catalog/README.md`
  "Keys"):
  - **Sources:** each key comes from one source, said everywhere: set outside the app (the environment: wins, never
    changed), saved by this app (the macOS Keychain), or the keys file (only read).
  - **Add key** (the Models menu's Keys, Capabilities): it tests the key with one free call. A refused key is never
    saved; one that couldn't be checked is saved only on "Save unchecked".
  - **Used at once:** a saved key reaches every consumer with no restart (client caches dropped; the research,
    presentation and GPU workers sent the change). Removing one falls back to the keys file.
  - **Only the studio:** `POST /api/keys/<service>` needs the studio's origin and `X-MicroscopeMind: save-key`. A value
    is never logged or put on a wire.
  - **Tests:** they never read this Mac's Keychain (`MICROMIND_KEYCHAIN=off`).
  - **A keys file the app can't read** (macOS privacy refusing the Desktop folder: EPERM, no prompt) is said as that on
    every key, with how to fix it (allow it, move the file, or add the key to the Keychain), never as "Not added"
    (`keys.file_problem`; F-L8-3).
- **Ready here?** One resolver (`orchestrator/readiness.py`, ADR 0019) says, for every model and option, one status
  (Ready, Needs setup, Unavailable here, Broken) and the next step with how (a key and where to get it, a licence page,
  a download's size, an install command), the same on the Models menu, the Toolbox (its Keys band says what each key adds:
  "+15 things it can do", from `setup.counts.unlock`), `use`'s refusals and `make doctor`. Downloaded means the pinned
  revision's files by size and content id. Reading it loads no model, starts nothing and calls no one; what can be
  chosen is unchanged, but a model not downloaded can't be chosen until it is (its step is the Download).
  A step left is never choosable (its first step is the reason), options read this Mac and unrunnable jobs as their
  model does, the runner refuses a key its service refused, and contributed capabilities (`kinds.available`) add the
  edition, this Mac, and a key or data still needed. Every surface that says a status in words uses `readiness.said`
  (studio: `toolbox.ts` `readinessSaid`): "Needs setup: …", "Unavailable here: …", "Broken: …" (L7b).
  Companion model configurations resolve to their verified pinned directory for
  both published revisions and legacy caches. A mutable cache `refs/main` is not
  required or trusted after readiness has verified a different pinned snapshot.
  In the app its facts on disk (files, installs, pinned code's imports, weights: `readiness.KEPT_KINDS`) are kept, dropped
  on every `catalog_changed` and each read again in a thread every 20 s (`refresh_kept`), so the loop reads facts already read: a catalogue went
  from 265 disk reads to 16 (`MICROMIND_READINESS_KEPT=off` reads them every time, as before; L1b part 2).
- **Observed, not declared** (`orchestrator/observed.py`; `catalog/README.md` "Observed, not declared"): what can't
  be read here is asked of the outside world and kept with when: a gated model's access (one HEAD to Hugging Face with
  your key), a key's working (its service's free test). Every card, the chip, readiness and the agent say that answer
  ("Hugging Face: your account has access (checked 21:29)"), never the manifest's declaration (live 2026-10-12: DINOv3
  approved, the card still said "Accept its licence"). Asked at start, on a key change, when the page opens, every 5 min
  while not granted (an approval shows by itself; a gated model's access only once there is a Hugging Face key), on
  Check again and before the agent answers; a download's outcome
  is one too. Unknown is never a refusal; a licence no probe can read is the user's word ("I've accepted it"). Every
  requirement kind has a source (`observed.SOURCES`), and no ✗ on a card is without evidence (both tested).
  The local-only Example project asks no one (`api_usage.require_provider`), so a licence not yet asked says so there
  (`observed.NOT_ASKED_STEP`: open another project to finish setting it up), never "asked in a moment" (F-L8-4).
  **Real use** is kept the same way (kind "use", `observed.used`), in the app's own home beside its checks
  (`uses.json`, opened by server.py's lifespan only, so tests and the sim keep their own): each
  run of an entry `operations.Runner` records (`operations._used`, by `IMPL_MODEL`), each cloud call through
  `api_usage.attempt` (its `outcome` hook, by the entry's API version) and each voice session that opened, as its latest
  outcome with when; the app's own cut-offs, runs that never reached the model, blind trials before their vote and the
  app's own check (`observed.not_use`) are not use. The card (`catalog_view.face`) is on with `by: "use"`, "Working here
  · last ran 14:02", when the newest evidence is a run that worked (`readiness.newest_use`: the later of it and a passed
  check counts), Broken with its words when it is one that failed (`readiness.failed_in_use`), and otherwise as before:
  ready to run with neither is "unchecked", said Ready · not checked yet (Will, 2026-10-29: Claude Opus 5.5 answered him
  all session while its card said "Not checked", grey). Its Check it here stays, in `also`.
- **One readiness for contributed choices:** a contributed or AI-made capability becomes a choice only once its own
  functional proof passed (`kinds.py`). The app has no check of its own for it (`model_checks.find` is None), so that
  proof is its role option's check, and checking it again reruns the proof (`catalog_view.own_proof`): the Models
  page, Tools and its setup say one readiness (F-L8-2: it said "Needs you: no functional check adapter yet" under Ready).
- **Environment display observations** (`orchestrator/env_observations.py`): the running App collects raw environment
  facts off the event loop, enumerating each entry's generations once and indexing receipts, proofs, retained manifests,
  interpreter availability, selection, enablement and candidate identity. One synchronous display uses that observation;
  readiness, current settings, keys, checks and queues are still resolved for each response. Explicit catalog changes
  request a coalesced revisioned refresh; unconditional polling also sees missing interpreters, off markers and lockfiles.
  Shutdown owns the outstanding read. Display scopes expire before inherited tasks can reuse them; exact-generation
  execution and selection bypass them. This is bounded to environment facts: unrelated catalog, channels, own-trial,
  Fiji and model-file reads, and synchronous registration, are not claimed to be off-loop.
- **Environments the app makes** (`orchestrator/envs.py`; `catalog/README.md` "Environments the app makes"): a model
  whose code needs a Python of its own names it (`env`: version, a hashed lockfile, its proof's imports); its card's
  **Install** (command `model-install`, the agent on the user's yes) builds it in place at
  `catalog/envs/<id>/<generation>/` with uv's own Python and `uv pip sync --require-hashes`, compiles its packages'
  bytecode once, serially at background priority (`compile_bytecode`: a sandboxed worker reads its runtime read-only and would
  otherwise compile them on every start; a generation made earlier is compiled on its next check, and `.bytecode` in it
  says so only once every compilable file is compiled; a failed repair is said and never fails the check), proves the
  imports with a clean environment, and is ready only by its receipt; the model runs only from it (micro-sam, Trackastra's
  `trackastra_server.py`). A failed install is never used; Remove (`model-uninstall`) takes it away.
- **The app's own Python** (`orchestrator/shared_python.py`; `catalog/README.md` "The app's own Python"): the built-in
  models (runner `gpu-worker`) share the Python made from `services/uv.lock`. What their code imports comes from that
  lock (`tests/test_shared_python.py`); a model's own pinned code is read for its imports, never declared (checked by
  `make catalog-pin`, the catalog's CI after download, and readiness: "Install what its code imports"); a setup step
  never re-pins a locked package; a lock change is an update of every built-in model (catalog CI checks their code's
  imports, `make capability-check` proves them run and says the Python it ran in). Live 2026-10-19: Molmo 2's einops and SAM 3.1's setup (PR #346).
- **Fiji, its plugins and bindings** (`catalog/README.md` "Fiji"): the app's own pinned Fiji, kept as complete
  generations (`orchestrator/fiji_generations.py`). Set up Fiji makes the first; Add to Fiji on an extension (the
  Toolbox's Fiji section, its folded list of every row of imagej.net's list with how it installs: `orchestrator/inventories.py`,
  `make fiji-inventory`) makes the next, cloned, with only that site's files, every binding proven again before it is
  used; one that fails stays apart; Go back makes the previous current. Installed is not usable: a Fiji function runs
  only through a reviewed binding (`fiji_bindings.py`, `services/fiji_worker/server.groovy`) proven on the generation
  in use. Analyze Particles (Fiji) is the first: headless, through the Overlay, identical to its macro, a Cells choice
  said as particles, never a fallback.
- **The models folder** (`~/MicroscopeMind/catalog/models/<id>/<revision>/`, `MICROMIND_CATALOG`; `orchestrator/
  downloads.py`, `gpu_worker/model_files.py`; `catalog/README.md` "The models folder"): a model's files are the
  pinned revision its manifest lists, everything its loader reads (config, processor, tokenizer, code, a companion such
  as EdgeTAM's backbone config). They are downloaded into a staging folder, each checked by its bytes and content id,
  and published read-only in one rename; a revision is never written over. Downloads resume when the server supports
  it and restart the file safely otherwise, check the disk first, wait while a click is answered and run one at a
  time (the Capabilities page's Download, command `model-download`, Activity, `setup.downloads`; `make
  catalog-download`). The loaders read only that revision, offline (`HF_HUB_OFFLINE`, `local_files_only`, explicit
  checkpoint paths), else an older cache at the pinned revision, each file checked; nothing downloads on first use.
  `make catalog-adopt` moves this Mac's caches in (APFS clones), `make catalog-pin` writes a manifest's files, and
  `make catalog-proof` is the decisive proof: each model the catalog runs here passed (its operations ran through the
  app's own check from its folder alone, the network refused in every process), failed or missing, red unless all
  passed. Every requirement kind has a fulfiller and a detector (`readiness.FULFILLERS`, `observed.SOURCES`), no loader
  can fetch weights (a test reads every load), a file inside an archive is pinned as a member (Trackastra), published
  files are checked again by content once per start, fine-tuned versions live in their entry's catalog folder, and a
  download or install is seen with no restart (`tests/test_catalog_contract.py`; the catalog audit of 2026-10-12). Tests count every model as
  downloaded (`MICROMIND_MODEL_FILES=standin`) and use a temporary models folder.
- **The Toolbox** (`#toolbox`, old links `#models` and `#capabilities` kept; `toolbox.ts`, Will's 2026-10-15 redesign):
  two views, Toolbox (`modelspage.ts`) and Everything it can do (`capabilities.ts`), in one frame (`toolboxFrame`): the
  tabs, the **band** (`keystrip.ts`), the header, the body, laid out alike so a switch moves nothing (it jumped before).
  - **The band**, the same on both views: **Keys**, always open (never a fold: what we most want people to add), each
    key with "+N things it can do" (`setup.counts.unlock`), where it comes from and Add key or Change, and the honest
    line "The example works without a key" (chat and vision need one); **Add a capability**: From a link, From a file,
    Have an AI build it, each taking you to Add your own's form.
- **The Toolbox view** (`orchestrator/catalog_view.py` rows on the capabilities wire's `models`; `catalog/README.md`
  "The Models page"): every catalog entry, its sections the summary's own groups in its words.
  - **Needs you:** your current choices only, the review's correction 1; models fixed in the code never count.
  - **Ready.**
  - **Available to add:** one step turns it on; grouped under it by the job each does.
  - **From the community**, always shown: New for you, the catalogs you follow (channels), how to share yours.
  - **Fiji:** the functions connected by a reviewed binding (`inventories.bound`, names only), each said ready only by its
    own row's readiness on the wire (`toolbox.ts` `fijiWords`, `rowReady`; ADR 0019), apart from what only installs ("1
    Fiji function connected · set up Fiji to use it" or "· 1 ready here", then "555 extensions listed: …"), the full
    list folded.
  - **Unavailable here** (folded).

  Each card has its one action (Add key, Download, the licence page, how to install; a setup with no plan falls back to
  it), what it is used for, and **Why?** (each fact with its reference: the key's source, the files' revision, size and
  folder, its code, this edition, the last check). A setup step the card already says is not repeated; its action sits
  on the card's last row so a row's cards line up. A pill is red only with its press on the card (else grey).
  - **The header's chip** (`#setup-chip`): green "All set" or "N things need you"; it opens the page.
  - **The live view's note** says when the Seeing model can't run.
  - **The Models menu** opens with "All models and setup →".
  - **Show in Finder** is the command `model-reveal` (with no model: the models folder itself).
  - **The small-models offer** (`apps/studio/src/starter-models.ts`; Will, 2026-10-26) sits at the top until answered
    (also on the empty Stage and the first-launch Welcome): a few small models ticked by a rule read from the wire, sizes
    from readiness, Download (the same `model-download` / `model-turn-on` and queue) or Not now, the answer kept by
    `onboarding-models`. It downloads nothing before its press.
  - **What each needs is said:** an option that runs more than one model names the others (`Option.also`), and a
    fixed role names its catalog `model`.
- **New for you and What it can do** (`orchestrator/catalog_news.py`, `capability_shelf.py`,
  `apps/studio/src/capabilityshelf.ts`; `docs/design/2026-10-13-capabilities-for-everyone.md` C1–C2; catalog/README.md):
  - **New for you:** the app looks at the reviewed catalog a while after start and daily (`channels.peek`: manifests and
    pictures only, never an adapter, lockfile or weights; `MICROMIND_CATALOG_AUTO=0` in tests and the sim). Entries this
    Mac hasn't known are offered with their picture, maker and badge, relevant ones first; Get it fetches that commit in
    full on the user's press. Nothing downloads, installs or turns on by itself.
  - **What it can do:** the catalog by job, one colour each (green on, yellow on its way, red one press away, grey not
    on this Mac; no red without its press: a card with no press of its own is grey, `modelspage.ts` `card`), where pictures go and what it costs. Turn on (`TurnOn`) carries every
    step: the app's own in their queues, the user's own (key, licence, Allow) on the card; said once on. When a plan
    downloads, installs or needs a step of yours, the card's Turn on first opens one sheet (`modelspage.ts`
    `sheetFacts`): each step, the download's size (one over 1 GB named), the free space where models go
    (`setup.disk`, `downloads.disk`, with its spare) and whether it fits (no Turn on when it doesn't), a time as a
    range by size, the licence, where pictures go, the key's source and the cost; Cancel or Escape starts nothing.
  - **The agent:** `models` with `new` ("anything new?"), and `turn_on` on the user's quoted yes.
  - **Add from a link** (`link_recognise.py`, C3): a BioImage Model Zoo link becomes an entry run by the reviewed template
    `catalog/templates/bioimageio` (adapter and lockfile copied byte for byte; the model's files pinned; its own test
    before any answer); a Hugging Face transformers segmentation model becomes an entry run by
    `catalog/templates/hf-segmentation` (pinned at its commit, safetensors only, never its makers' code); what it can't
    run is said. "Turn it on" right after (`model-turn-on`).
  - **Have an AI build it** (`drafts.py`, C4): for a skeleton, a researcher (deliverable "adapter", effort "build",
    started only by the card's press: `admit` takes it only `confirmed`) reads the link and drafts an adapter with
    `read_source`, `draft`, `pin_file`, `try_draft`; the app locks wheels only and runs each try in the sandbox. A draft is
    never a choice; Use it makes it one of yours, installed and proven as any, only after its review (`drafts.review`:
    the exact code, locked packages, pinned files and origins, every try and the preflight's checks) and with that review's
    digest, so what is admitted is what was read; every pinned file is adopted, and a failure leaves nothing half-made.
  - **Share** (`share.py`, C5): a file with no account (never weights), opened as a community catalog of its own, allowed
    once by the sharer's name. Its channel's own name (`shared-<hash>`) is never said: its row carries `channelSource`
    and `channelLabel`, a card says "from a shared file" (`model-status.ts` `sourceWords`) and every server sentence
    names it by `channels.said` ("Maya Okafor's shared file", "the community channel lab-models"). A shared file doesn't
    update itself: a newer one opened is an entry of its own, and its Details and channel row say so (never "Up to date"
    or "import a newer file" to update it; `maintenance/status.py` `SHARED_FILE`). Share with everyone through publish.py's review package and the user's DCO sign-off.
    One-click GitHub sign-in (`github_signin.py`, the device flow, its token in the Keychain) is prepared and says it
    isn't set up until the project's GitHub app is registered (`CLIENT_ID`); signed in, Share with everyone goes through
    GitHub's API with no Git or gh (`publish_api.py`: the exact reviewed package, sent only on the user's sign-off). **Made with** (`made_with.py`): what really ran
    (each execution's implementation, version and generation; never an unvoted blind arm) is named in a snapshot's PNG, a
    kept result's JPEG and entry, and a recording's or saved session's `dataset.json`; an asset's preview offers each one
    not on yet (`/api/library/{id}` `madeWith`, `of_asset`, `model-turn-on`).
  - **New kinds of job** (`kinds.py`, C6): an entry with its own adapter may declare an operation of its own taking the
    view and giving a picture, a table, words or points (`manifests.KIND_OUTPUTS`); proven by that output, it is an
    operation the runner knows (`env_runner.register`), run by `run_capability` (there only while one is on) or Try it
    (`capability-run`). Its reply is bounded before it is read and checked again by the app (`env_runner.kind_output`).
  - **A monthly cap on cloud models** (optional, none by default; `api_usage.UsageLedger.set_cap`, command `usage-cap`, the
    spending panel's "… this month"): `api_usage.attempt` admits a call only at its own most (`bound`: its list price,
    its output limit, which counts thinking with the answer (`max_output_tokens`, enforced by both providers; thinking
    levels never lowered), and its input's UTF-8 bytes, `tokens_in`; priority tier doubled), its row written before it is sent
    and settled by the usage its caller records next; one whose usage never returns stays at its most, across restarts;
    a model with no known price, or a feature billed with no limit the request can set (the web lookup's Google
    Search, `attempt(unbounded=)`), isn't asked under a cap, said; a refused call is never sent (`CapReached`, the
    meter's Providers "capped"). Admission and renewal are one SQLite write transaction (BEGIN IMMEDIATE) with the cap read
    fresh, so every connection to the ledger is one authority. Voice holds its next two minutes ahead, renewed by the
    clock every 30 s (voice/route.py `keep_allowance`) and at each usage update, counted at the larger of reported
    seconds and open time, each month its own rows (a month's end split at the boundary, the new month's minutes held
    first), released whenever it ends (a start that failed too), and ends before they don't fit. A renewal refused or
    failing closes the provider's session first, whatever the browser does; a start that fails or is cancelled closes
    what it acquired (a created WebRTC session is hung up) before settling; finish is terminal and serialized with
    renewal, so nothing restores a finished session's hold. A researcher's run is refused when its most doesn't fit,
    held against the month in a row of its own (each run's, in the month it starts) while it runs, and dispatched with
    that most as its budget, never unlimited (research/ledger.py, manager `_month_capped`); inside, every attempt (each
    round, its write-up, every retry, and its lesson, edit and check calls) is sent with an output limit sized to what
    is left of it (`budget.bounded_call`, `engines._limit`, `Researcher.bound`; thinking levels unchanged), web search
    isn't offered (neither provider's search can be bounded in the request), a call that may have been billed with no
    usage reported is counted at its most in the run's own spend, so the app's settlement keeps it, and with nothing
    left it ends keeping what it found. Everything after a voice session's provider is acquired, its first message
    included, is owned by its cleanup.
    The spend is the app's estimate from list prices, not the provider's bill. The studio's **Spending** menu (status
    line; `apps/studio/src/spending.ts`, Will's latest direction of 2026-10-13) is a private personal resource for
    understanding API usage: tracked provider estimates lead. A quiet, always-clear "Limit not set" or "Monthly
    limit: $X" gives the optional limit its place; no quota-bar hero. It sets, changes or removes the limit in place,
    with Saving/Saved/Not saved shown and drafts kept through
    polling (a confirmed save is what it shows at once; a read begun before it is never drawn; a draft closed is let
    go). The daily research allowance is its own line. Two quantities stay apart:
    - **Counted toward the limit:** `month.estimatedUsd`, conservative, with work in progress held and each
      unknown-cost call at an allowance, each named.
    - **Tracked spend:** This month or Today, the same way (`UsageLedger.breakdown`), its total the sum of the
      provider rows shown, unknown costs said beside it, never in it.
  - **Mirrors** (a file's `mirror = { url, rights }`, `downloads.fetch`, C7): tried only when its own place fails, only
    with its redistribution rights stated, checked by the same sha256, never sent the Hugging Face key.
  - **Report a problem** (`report.py`, command `model-report`, C7): a card's controlled facts of a failure (each a stage
    and a reason code, `report.REASONS`; never its error text, pictures, sessions or paths), to read and copy; the raw
    words stay on this Mac (`local_details`); nothing is sent, its page opens with a title only.
  - **Go back a version** (`env_runner.choose`, command `model-version`, C7): an entry with its own adapter runs the
    proven version the user went back to (`chosen.json` beside its environments, kept across restarts) until Use the
    newest; the worker of the version left is retired at once (`register` retires a client whose generation is no
    longer in use).
  - **The sandbox** (`sandbox.py`, C7): code nobody reviewed (community, shared, any of yours: a template's model is its
    makers' code) runs in `sandbox-exec` (deny by default: its Python, entry, files readable; its work folder writable; no
    network, no home; signals only inside it, a short list of system services (`sandbox.MACH`), shared memory by its
    workers' names; `confine`: its home is its work folder, no catalog or key in its environment); installed with ready-built
    packages only (`--no-build`: no build script runs outside it); refused where the sandbox can't run. Tests read their
    stand-in Python through `MICROMIND_SANDBOX_READ`.
- **The agent relays it** (`brain/toolbox/catalog.py`; `catalog/README.md` "Asking the agent"):
  - **`models` (query):** the page in words: what your choices need, a model, a job or a key ("what does my Google
    key turn on?"). A download it can take next is offered with an id (`p1`, kept 180 s).
  - **What Hooke has and could add for some work** (`models` `does`; live 2026-10-29, "what segmentation models do we
    have, and what others could we add?" was asked of the Outlining job alone and the rest guessed): the model names the
    kinds of work the request means (the catalog's operations, each with its title and what it gives, in the tool's
    parameters); each row's `does` is read from its manifest (`catalog_view.operations_of`), New for you's too. The answer
    (`catalog_view.work_words`) is every entry that does them, installed or not, models apart from tools (the manifest's
    kind: a tool is an algorithm or a Fiji function; will-workspace 30 Oct had Analyze Particles answered as a model), each
    by where it stands (working here, installed but not run yet, can be added with where it runs and every step: a
    download's size, or that its manifest gives none, never readiness's "1 MB" floor; a key by its service; its setup;
    can't run here), then what is new, how one not in the catalog is added
    (the reviewed templates' links, a Cellpose file, Have an AI build it) and the other kinds; a job named is answered
    the same way, and `capabilities` by operation says the entries not set up. It is compact, most needed first: the
    round that answers from a query reads about 1,900 characters of its text (`brain/claims.py` `answer_facts`).
  - **`get_model` (effect):** starts exactly that download on the user's own yes (`confirms`, `asked_with` checked by
    `research/policy.quoted`), through the page's queue. Asking for one by name ("please download DINOv3") is that
    yes: their words must name it, and only a download its card offers (live 2026-10-12 it was asked back).
  - **`open_models` (effect):** opens `#models/<id>` in the studio that asked. It is done once that studio answers
    `page-opened` (`App.open_page`, server message `open-page`).
  - **Keys:** no tool takes one (a test reads every tool's parameters), and the agent never asks for one in the
    conversation.
  - **Refusals:** a refused `use` or `set_default` says which of these turns the model on.
  - **Acting bench cases:** `models-*`.
- **Your own models** (`orchestrator/mine.py`; ADR 0021; `catalog/README.md` "Add your own"):
  - **Adding:** a Cellpose model file, by a Hugging Face link, a path, a file dropped on the Models page (`POST
    /api/catalog/mine-file`, the studio only) or one put in `~/MicroscopeMind/catalog/mine/models/`. Its entry is
    written (runner `cellpose-file`, host "own"), and its files are checked into a read-only revision beside it. Any
    other link is a skeleton ("Write an adapter for it"), never runnable.
  - **Appearing is not running:** nothing loads or runs it before **Turn on**.
  - **Turn on** (`model-activate`): its check runs once in a process of its own with no keys
    (`noncommercial/cellpose_file_check.py`: weights only, builds, checks the bundled public synthetic example).
    The setup owns its child process; Stop or app shutdown cancels and reaps it before finishing.
  - **Once on:** a choice of its own under Cells (option and implementation `mine-<id>`, registered at start and on
    the app's folder rescan, `App.watch_mine`), run in a worker of its own.
  - **Turn off** (`model-deactivate`) takes it away, and a setting that chose it goes back to the default.
  - **Commands:** `make model-add FROM=`, `make catalog-check`.
  - **Accepted live** with BlueSpotter LC (`priggelab/bluespotter-lc`, a fine-tuned Cellpose-SAM): added, checked,
    used, compared and kept across a restart.
- **A model with its own adapter** (runner `python-env`, `orchestrator/env_runner.py`; `catalog/README.md` "Add a
  model with its own adapter"): one folder (manifest, hashed `env.lock`, `adapter.py`), no change to the app's code;
  InstanSeg's brightfield nuclei model is one.
  - **Install** makes its environment, keeps a copy of the entry with it and proves the adapter by running it on a
    real picture (`services/env_worker/server.py`, the network refused, always); only then is it a choice of its
    operation (`env_runner.register`, at start and on every catalog change).
  - **A new version installs beside the one in use** (`env_runner.in_use`): the old one runs from its own copy until
    the new one's proof passes; the card says "Update available" (and, when it failed its proof, why).
  - **Turn off and Remove** (any entry with its own adapter, a community channel's too): Turn off (`model-deactivate`,
    `env_runner.turn_off`) takes its choice away and stops its worker, deleting nothing (refused while a run uses the
    version that is on: `env_runner.InUse`), and Turn on brings back the version it had; Remove (`model-uninstall`,
    after a confirm) takes its installed versions away.
  - **`requires`** (`manifests.CATALOG_FORMAT`, `RUNNER_CONTRACTS`): an entry made for another version of the app is
    listed apart with why, never run.
- **Jobs, native parts and data** (`orchestrator/jobs.py`; `catalog/README.md` "Jobs, native parts and data"):
  brainreg (`catalog/models/brainreg`) registers a whole-brain volume to the Allen mouse atlas (`allen-mouse-25um`, a
  `kind = "data"` entry, every file pinned, needed as `data:<id>`).
  - **Native part:** conda-forge's Apple Silicon NiftyReg and its libraries, pinned by sha256 in `env.native` as their
    `.tar.bz2` builds (Python's own tarfile unpacks them; a `.conda` needed this Mac's zstd and is refused), unpacked
    into its own Python's prefix and given as CONDA_PREFIX; `env.commands` prove it.
  - **Runs as a job:** `env_worker/job.py`, a process group with the network refused. Its proof, and catalog-proof's,
    registers its own sample brain.
  - **From the command line:** `make catalog-install ID=` is the Install button.
  - **Your volumes** (`orchestrator/registrations.py`, the Models page's Brain volumes, tool `register_volume`, commands
    `volume-add`, `volume-describe`, `job-start`, `job-cancel`, `job-again`; `paths.jobs`):
    - **A volume (`v1`):** kept as added, with its facts and each one's source (file, user, fixture). The orientation
      is always the user's.
    - **A registration (`j1`):** a job whose record pins its inputs, environment, atlas and outputs (sha256).
      Cancelled whole; ended and interrupted when the app stops (its own process group would outlive it), one a crash
      left running ended at start, off the loop (only when its pid is still our job's); reopened without computing.
      Every end is a notice in the conversation it was asked in. Run again repeats the record (volume sha256, its
      facts, environment generation, atlas revision) or runs nothing and says what changed; Register is a new run.
    - **Reading is bounded** (the adapter): a preview decodes one page; a stack only its own planes, refused past 8 GB
      before a pixel is read. A file of several series is told which; each series' scale and axes are its own.
    - **Proofs are bounded:** Install's Cancel reaches a job's proof, which ends past 30 minutes (`jobs.PROOF_LIMIT_S`).
    - **The agent** finds the volumes through the tool (never in the view or the notes: its description says to call
      it whatever the view shows; live, the model had answered from the 2D view that there was no volume).
    - **Shown in brainreg's downsampled space,** a product in the index (kind `job`).
- **Publishing** (`orchestrator/publish.py`, `make catalog-pr ID= [PUSH=1]`; `catalog/README.md` "Publish it"): only
  an entry that pins what it gets, names its licence and is proven on this Mac; its pull request is made from
  origin/dev in a worktree of its own, pushed only with PUSH=1. Both routes, this one and Share with everyone's
  (`publish_api.py`), open it against `dev` (`publish.BASE_BRANCH`): the repository takes pull requests into dev only.
  Share with everyone's copy of the catalog is made with dev (the official default branch), and an older copy without
  dev gets it at the reviewed base. A package an older app prepared for `main` is prepared and reviewed again before
  anything is sent; a review it already opened is still followed. The repository checks it
  (`.github/workflows/catalog.yml`, `bench/catalog_ci.py`, `make catalog-ci`); CODEOWNERS reviews `catalog/`.
- **Channels** (`orchestrator/channels.py`; `catalog/README.md` "Channels"): a git repository's catalog at a pinned
  commit, fetched (manifests and adapters only) into `<catalog>/channels/<name>/<commit>/`, read-only.
  - **The reviewed channel** (this repository): Check for catalog updates (`catalog-update`, `make catalog-update`)
    lists entries merged since the app was made, with no app update; bundled entries update with the app.
  - **Community channels** (`channel-add`, `make channel-add URL=`): keyed `<channel>/<id>`, said "not reviewed
    here", and nothing of them runs until the user allows its code (`channel-trust`; readiness step `trust`); its
    row's Check for updates (`channel-update`) pins its newest commit, said (its count as "1 entry" or "N entries",
    `channels.entries_words`), and `make catalog-update CHANNEL= COMMIT=` pins the commit given. On the Models sheet
    the channels (Where entries come from, with Add channel) are under the side's Advanced contribution.
  - **Only runner `python-env`** comes through a channel (`manifests.CHANNEL_RUNNERS`); any other is listed as coming
    with an app update.
- **Maintenance** (`services/orchestrator/maintenance/AGENTS.md`; ADR 0025; ticket 59): an entry already turned on can
  have a recorded policy. Its later updates from its own channel are then fetched, built and proven beside the active
  generation, and promoted for new work under that policy: `manual` (no record: nothing unattended), `review`, `auto`
  (reviewed code only) or `pin`. A change to anything only a person can accept stops at Needs review. The card, the
  setup sheet and the agent say its update state in one set of words (`setup.maintenance`).
  A catalog declares revoked versions and unmaintained entries in `maintenance.toml` beside its entries
  (`maintenance/revocations.py`), never in a manifest: an older app would refuse an unknown manifest key.

A format-2 capability may declare a successful `tests` example with no parameter overrides. The default installation
proof uses that exact image from the immutable installed snapshot and records its source hash, retained pixels and
actual parameter values. Examples with overridden parameters do not establish default coverage; legacy entries keep
the existing representative-image check. Every declared example still runs. Swin2SR includes a public synthetic grid
large enough for its default 256-pixel crop, in addition to its 128-pixel example. This is functional evidence only.

Portable wheel recipes use catalog/share format 4 (`gpu_worker/wheel_recipe.py`,
`orchestrator/environment_wheels.py`; authoring syntax in catalog/README.md).
Only declared, SHA256/size-verified wheel files receive the larger bound: at most
four artifacts, 64 MiB combined. All other file and result-picture limits remain.
Workspace snapshot, share and owned preparation enforce the same declarations;
source locks use `hooke-wheel:` references, resolved only to private verified copies.
Import and preparation do not confer trust, activation or independent qualification.

Scientific candidates have an explicit **Prepare for review** action in Details
(`model-prepare`). It uses the existing serialized install queue, trust and machine
checks, generation leases and Stop/retry lifecycle, but only prepares dependencies.
It never publishes operation proof, activates a member or marks its workflow green.
Scientific candidates with pinned files found only in an older cache separately offer
**Prepare cached files** through the existing `model-download` command. That queue
adopts and verifies the pinned bytes before immutable publication; cached presence
is not prepared-file authority. The file fact stays incomplete until publication,
without rebuilding dependencies, granting code trust or changing qualification.
Ordinary GPU loaders retain their existing legacy-cache readiness semantics.
Guided import admits validated scientific-host declarations as fresh untrusted
candidates; scientific review admission still binds resources and exact generations.
Package HTTP import reserves from compressed and bounded directory-expanded bytes
before inflation; larger workspace packages grow their reservation before copying.
The analysis-memory allowance and smaller embedded-result allowance are unchanged.

Portable wheel materialization retains each verified SHA-256 in the local artifact
URL so installer origin metadata can identify pinned scientific runtimes. Its
materialization version participates in portable generation identity; older
prepared generations and saved documents are preserved, never rewritten in place. The one addition: a check
compiles an older generation's Python files once (`envs.repair_bytecode`), which adds only `__pycache__` files and the
`.bytecode` marker; nothing the generation already had changes.

Scientific-host members support the optional versioned declaration
`resources = { format = 1, memory_mb = 4096 }` (integer1024–8192MiB). Missing means
the existing1024MiB budget. This declares a per-tool enforced maximum, not measured
usage or qualification. Setup shows it; Start reserves capacity before launching.
The exact declaration is retained in runtime identity. Older member readers
refuse its unknown field; unsupported formats and non-scientific uses refuse.
Prepared model artifacts are read from the exact owner's pinned published files,
without developer-cache fallback. Running tools share the download lock so a
repair or adoption cannot replace their files; retry retains prepared staging.

Typed inputs (H01 slice d; `orchestrator/admission.py`). A bundle member may declare several data inputs, or one that
is not the view's picture: `image.2d.rgb8`, `image.2d.gray8`, `image.2d.gray16`, `image.3d.zyx.float32`,
`labels.instance.2d` and `masks.2d`. Such a member never runs on the live view.
- **Reference checks:** its cases name one `example*.npy` per input (`inputs = { labels = "example-labels.npy", ... }`),
  with explicit per-port descriptors in `scientific.inputs` (for example `{axes = ["y", "x"]}`). NPY headers do not
  identify scientific axes. The shared bounded loader preflights all headers/layouts and the aggregate decoded size,
  then loads without pickle under a transient memory reservation and runs the same admission validator as execution.
- **Binding at Run:** every actor binds each input to an exact retained resource through `admission.bind`: an array
  component of an accepted scientific document revision, or an earlier run's RGB picture. The headless route takes one
  authorized `.npy` per input with `{id, path, sha256, descriptor}`. Descriptors explicitly declare ordered axes.
- **Checks:** dtype, dimensions, ordered axes and meaning are checked per input before anything runs. Volume ports
  require `z,y,x`; 2D scalar ports require `y,x`, and RGB requires `y,x,c`. General typed scientific port contracts
  remain unsupported by the bundle schema; they are refused, not silently ignored. Shared admission validates any
  supported declared input constraints, including peer relationships. A mismatch is refused with the input's reason; nothing is converted, so a 16-bit or grey input never becomes
  RGB.
- **Running and records:** the adapter is called as `function({input: array}, params)` with read-only arrays of exactly
  the stored dtypes. The result records each input's reference and content digest. For now one table, text or data output is kept; points/pictures remain unsupported for typed runs.
- **Studio:** Actions offers one picker per input, listing the session's saved arrays that the input takes as stored and
  saying why it doesn't take the others.

Directory readers may declare `reader_checks = {format=1,cases=[...]}` on a
format-3 `scientific-host-v1` member. Each case names a unique id, relative folder
keys mapped to prepared `example*` files, a `reference*.json` and source attribution.
`reader_proof_contract` bounds eight cases, 32 files/8 MiB input per case, 16 scalar
image layers/16 levels and 64 MiB aggregate decoded data. Image-only observations
remain `reader-data-contract-v1`. A Labels result uses `reader-data-contract-v2`
with an explicit image/labels type on every layer, single-resolution integer Labels
and nonnegative IDs. References compare that type exactly; an unchanged image
reference cannot qualify a Labels result. The parent-issued receipt scope/seal
remains version 1 and still does not claim full qualification. Mixed reader/operation
bundles and other reader kinds remain unsupported for this proof path.
The parent runner creates immutable private source grants, opens the exact prepared
confined host and compares ordered levels, shapes, dtypes, value digests, axes,
units and affine basis. Only it issues `reader-data-contract-v1` proof, sealed
outside worker read grants and bound to generation, preparation, producer code,
inputs, references and retained observations. Missing, partial or changed evidence
cannot select/promote a generation. This is a local exact-data check, explicitly
`fullQualification=false`; independent scientific and interactive workspace
qualification remain separate. Prepare for review still only prepares dependencies.


Format-3 scientific readers may declare `access="regional"` with `maximum_bytes` up to 64 GiB, the retained-source
ceiling. Omitted access keeps the existing 64 MiB ceiling. The exact prepared declaration grants only its named
source input bounded navigation reads (`SourceFile.read(view=True)` or `SourceDirectory.open(view=True)`).
The default operation budget and per-range bounds remain unchanged; parent and worker enforce the same rolling
navigation limits as native arrays. This is an SDK transport capability, not proof of lazy decoding, persistence
or compatibility. An adapter must still implement and qualify its declared formats and ordinary Save/reopen.

A built-in catalog-2 declaration may describe an existing app-owned adapter without
adding a runner/function implementation. Analyze Particles declares its unchanged
cells/picture/Labels contract this way. `kinds` requires the exact operation/id to
already have an app adapter and reads full normal model readiness, including the
installed Fiji generation's proof. It neither registers nor replaces that adapter.
The same row formatter/settings checks serve UI and agent callers. Built-in Fiji
entries without `[fiji]` are declarations only; namespaced contributions still need
their own confined function contract. Unknown declarations gain no execution path.

## Scientific matrix operations

`matrix-project-counts` projects explicit retained raw-cell protocols through the same
bundled runner. It never guesses target mappings or drops missing declared targets;
sampling is explicit and retains its source identities and exclusions. See the matrix
architecture contract before using it to prepare an evaluation reference.

Bundled matrix operations use format-2 named retained-file ports and the shared
`scientific_compute.catalog` descriptors. `matrix-cpu-runtime` and optional
`cell-evaluation-runtime` are prepared through ordinary catalog install; app-environment
packages do not make them ready. WorkflowService and the manual Tools action use the same
`matrix_capability_runs` adapter and exact runtime generation. Runtime preparation is not
scientific-quality proof. See `docs/architecture/scientific-matrices.md` for meaning,
resource ownership, evaluator configuration and remaining scale/holdout gates.
Aggregate profiles use the separate `aggregate-profile-prepare` retained-JSON protocol;
the raw-count reader is not relaxed for fractional means. `response-effects-merge`
joins compatible prepared context/target rows and refuses duplicates or unspecified
normalization. Both use the same catalog runtime, file admission and workflow attempts.
`response-effect-evaluate` compares fitted aggregate effects against every declared
reference row from disjoint biological contexts, with an explicit zero-effect baseline.
Fitting retains validated normalization meaning and the training-effects file hash;
older models must be refitted for this diagnostic. It is not an official competition
score or a sealed-holdout claim. No synthetic cells are manufactured for evaluation.

Environment-only dependencies declare `kind = "runtime"`, their own CPU Python and
a hashed lockfile with nonempty import checks. They declare no runnable adapter,
ports, members or workers. Ordinary Install prepares them; catalog CI verifies the
lock and performs the same environment/import checks. This is dependency readiness,
not an operation proof or scientific qualification. Consumers retain their own
capability contract and exact runtime generation. Unknown kinds fail closed in
older apps; runtime entries do not gain a community-channel execution path.

`matrix-concatenate` is a counts-only retained-file operation: exact gene order and unique cell identities,
explicit limits, no silent annotation loss. `response-predict` keeps proportional fold as an explicit legacy
approximation and offers pseudocount inversion only with compatible fitted normalization. Both are ordinary
catalog/workflow declarations; numerical qualification and full-size scientific acceptance remain separate.

Opt-in portable volumes and registration jobs retain their input copy, inspection, output files and metadata snapshots under the captured Project. Host runtime installations and atlas caches stay outside. Reopening reads saved outputs without installing or running software. Cancellation and terminal visibility are keyed by Project and job identity, so equal local IDs in different Projects cannot interfere. This staged adapter does not enable live portable creation.

`matrix-inspect` version 3 reports median row totals and, only under raw-count admission, mean reciprocal library depth over all observations. Zero-depth observations remain counted and make the reciprocal undefined (`null`); they are never silently excluded. These are numeric diagnostics, not evidence of biological accuracy or a selected count generator. The existing frozen runtime generation and result provenance apply.

Matrix-inspect version 4 retains a positional first 10 observations × 10 variables preview plus bounded annotation examples. This is not representative sampling. It slices before dense conversion; older reports remain usable without these additive fields.

Version 5 optionally counts one explicit observation text column across all rows,
with missing counts separate from empty labels and unused categories omitted.
The exact column and labels are bounded; over 4,096 distinct labels refuses, not
a truncated frequency table. No biological identity or fold independence is inferred.
