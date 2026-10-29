# The catalog: every model, tool and service, one folder each

Everything HookeUI can use is a folder here. The app reads these folders to know what exists, what each one does
and takes, what it needs to get and to run, and whether it can run on this machine. The design is
`docs/design/2026-10-12-the-open-catalog.md`; the decision is ADR 0018.

```
catalog/
  README.md          this page
  models/<id>/       one model, tool or capability: model.toml (its manifest), adapter.py and env.lock when it has its
                     own code (Fiji itself and each Fiji function are folders here too: fiji, fiji-analyze-particles)
  services/<id>.toml a cloud account with a key: Gemini, OpenAI, fal, Anthropic, Hugging Face, Roboflow
  templates/<id>/    reviewed adapters a recognised link runs with (bioimageio, hf-segmentation)
  inventories/       what Fiji's extensions are and how each installs (generated: make fiji-inventory)
```

Your own entries live outside the repository, in `~/MicroscopeMind/catalog/mine/models/<id>/` (`MICROMIND_CATALOG`
moves the root), in the same layout. The app lists them as `mine/<id>`. A local entry never takes a reviewed entry's id.
Code you add runs only when you activate it (ADR 0021).

## How the app reads it

- **Reading is metadata only.** `gpu_worker/manifests.py` parses each `model.toml` with tomllib. It never imports a
  model library, starts a worker, calls a provider or downloads anything (`test_reading_the_catalog_loads_no_model_library`).
- **Each entry is read on its own.** One that can't be read reports its problems in plain words
  (`manifests.problems()`), and the rest load.
- **What reads it:**
  - every option that is a use of a model (`Option(model=...)`, `orchestrator/options.py`) and every implementation that
    runs one (`_model(...)`, `orchestrator/operations.py`): their key, licence components, name, version and install
    come from the model's folder;
  - the keys the app knows (`keys.KNOWN_KEYS`) and which licence component needs which key (`capabilities.KEYS`), from
    `catalog/services`;
  - the weights the model check looks for (`model_checks.WEIGHTS`);
  - the worker's names, documented sizes and load limits (`gpu_worker/loading.py`, local models only: `manifests.local()`);
  - which models are never let go of (`orchestrator/loads.py`);
  - which models get a worker of their own (`hub.host_for`).
- **Disagreements fail a test.** `services/tests/test_catalog.py` fails when any other list in the code disagrees
  with a manifest, and when a model is named in the code (a constant, a fixed role, a price, a vision spec) that no
  folder holds.

## A model and its uses

A folder describes a model. A use is what the app chooses: an option with its own id, label, note, default and
fallbacks. One model can have several uses: Gemini 3.8 Flash is a use in seeing, its high-resolution seeing, the running
look, the fast pass and research. An option names its model (`model="gemini-3.8-flash"`) and takes its key and licence
from the folder unless it says otherwise. Behaviour settings that run no one model stay ordinary settings.

## Services

`catalog/services/<id>.toml`: `id`, `name`, `key` (the environment key's name), `get_key` (where to get one), `licence`
(its `licences.py` component), `test` (the one cheap call that tells whether a key works: `make keys-check`) and
`costs`. A cloud model names its `service` and needs its key to run.

## The manifest's keys

| Key | What it is |
|---|---|
| `id` | The key the worker loads it by (`gpu_worker/loading.model_of`); its folder's name. |
| `name` | Its one display name (`orchestrator/names.py`): no commas, where it runs said apart. The implementation of the same id is called this. |
| `runs` | `this-mac`, `cloud` or `cpu`. |
| `version` | Its weights: a repository, or what names them. Each implementation it runs has this version. |
| `licence` | Its `orchestrator/licences.py` components; the edition gate and the licences page read these. |
| `kind` | `model`, `tool` (a program with functions: Fiji), `tool-function`, `cloud-model` `data` (files another entry reads, never run), or `runtime` (a pinned CPU Python environment, with import checks and no operations). |
| `what` | What it does, in at most 12 words, for the catalog and the Models menu. |
| `runner` | How it is run: `gpu-worker` (the app's own GPU workers), `cloud` (the app's own provider clients), `cellpose-file` (Cellpose's code on your weights), `fiji` (Fiji and its bindings), `builtin-cpu` (an algorithm bundled with the app) or `python-env` (its own adapter in its own environment: "Add a model with its own adapter" below). `""` with `pins.adapter` naming its own adapter in the app's code (Trackastra's, kept with the non-commercial code). |
| `env` | The Python of its own the app makes for it: `python` (the version), `lock` (a lockfile beside the manifest, every package by its hash) and `imports` (what its install proves). |
| `adapter` | Runner `python-env`: `file:function` in its own folder, run only inside its environment. |
| `component` | Its licence when it is none the app lists: `what`, `licence`, `url` and `tier` (`open`, `terms` or `noncommercial`), registered under its own id. |
| `requires` | What it is made for: `catalog` (the manifest format) and `runner` (that runner's contract), each a version number, 1 when left out. An entry made for another version of the app is listed apart with why, never run. |
| `service` | A cloud model's `catalog/services` id. |
| `source` | Where it comes from, always pinned: `{ hf = "org/repo", revision = "<full sha>" }`, or `package`, `weights`, `git` with `rev`, `bioimageio`, `url`, or `made_here` (made on this Mac, such as fine-tuned weights). |
| `needs_to_get` | What obtaining it needs: `download`, `key:<NAME>`, `licence:<url>`. A key that only obtains gated weights is here, not in `needs_to_run`: a downloaded copy runs without it. |
| `needs_to_run` | What running it needs: `install:<INSTALLED key>` (code beyond the app's own: `sam3`, `micro-sam`, `cellpose`, `open_clip`), `key:<NAME>`, `weights:<made here>`, `platform:<os>`, `memory:<GB>`, `input:<data type>`. |
| `credit` | `authors` and `link` (and `paper`, `cite` when there are any): shown wherever its result is shown. |
| `[[inputs]]`, `[[outputs]]` | **Format 2, the capability contract** (below): what it takes, data and parameters in one list, and what it gives, as a ComfyUI node declares them. Needs `requires = { catalog = 2 }`. |
| `operation`, `category`, `description`, `search_aliases`, `hidden`, `outcomes`, `derived_from`, `tests`, `experimental` | Format 2 (below): its job, where it is listed, longer words, other names, what the app gives it unasked, what an empty result means and what it refuses, what it was made from, its pinned examples, whether it may change. |
| `[[implements]]` | Format 1: each operation contract it fulfils: `operation` and `impl` (`orchestrator/operations.py`), and what it takes and what its result means (`input`, `output`: data types below). The operation's `returns` and output keys are the contract. |
| `workers` | The worker classes that load it: `interactive` (clicks), `analysis`, `follow`, `molmo`. |
| `protected_on` | Worker classes where it is never let go of: the live view's own models. |
| `host` | `shared` with other models in a worker; `own`, a worker of its own (`hub.host_for`): its load blocks nothing else, and letting it go stops that worker; `own-python`, the same, with the model in a Python of its own, a child of that worker. For a model of 1 GB or more, or one slow to load. |
| `documented_mb` | Its memory once loaded, a first guess until this Mac measures it (`models/loads.json`: the kernel's footprint and its peak while loading). |
| `peak_mb` | The most its load takes, measured where it could be (`bench/footprint_bench.py`). |
| `load_limit_s` | How long its load may take before its worker counts as hung. |

| `pins` | What else is pinned beside its weights: `adapter`, `environment`, `preprocessing`. |

| `files` | Every file its loader reads, and so what "downloaded" means: each file's `path`, `bytes` and content (`sha256`, or `git_sha1` for a small file Hugging Face keeps in git); `url` when it is not from the model's Hugging Face repository; `root` where an older cache kept it (`cellpose`, `micro_sam`); `from = { hf, revision, path }` for a companion file of another repository its library reads by name (EdgeTAM's backbone config); `mirror = { url, rights }` for a second place to fetch it from when its own is gone or down, only with the exact rights under which it may be redistributed (a sentence with its licence) and its sha256. `make catalog-pin ID=` writes them for a Hugging Face model. |
| `files_from` | Another model whose files these are (EdgeTAM video runs EdgeTAM's): one folder, one download. |
| `tags` | At most 12 plain lowercase words it is about (`["diatoms", "plankton"]`): a new entry whose tags fit what a session looks at is offered first. |
| `contributed_by` | Who added it to the catalog (`name`, and a `link`), said on its card and in New for you; its makers are `credit`. |
| `example` | A picture of it working (`example.png`), a file beside the manifest: shown on its card and in New for you. |

Reserved for the next slices: `check` (its check file).

## Keys: where each comes from, and adding one

Every key a service names resolves to exactly one source (`orchestrator/keys.py`), said wherever the key is shown:
1. **set outside the app** (the environment it was started with): it wins, and the app never changes it;
2. **saved by this app** (the macOS Keychain, `orchestrator/keychain.py`): added, replaced and removed from the studio;
3. **from your keys file** (`MICROMIND_KEYS_FILE`, else the launch settings' `keysFile`): the app only reads it.

**Add key** (the Models menu's Keys, an option's step on Capabilities, the Toolbox's Keys band):
- **Tested first:** one free call to its service (`orchestrator/key_tests.py`, `make keys-check`): accepted, refused (never
  saved), or couldn't check (a timeout, no connection, a rate limit: saved only on "Save unchecked").
- **Used at once:** a saved key reaches every part with no restart (`orchestrator/key_admin.py`): this process's clients,
  the research and presentation workers and, for `HF_TOKEN`, the GPU workers.
- **Removed:** the next source is used (your keys file), or none.

The value goes only in the request's body to `POST /api/keys/<service>`, which only the studio can call. It is never
logged, shown, put on a wire or told to a model.

## The models folder: where downloaded models go

Every model's files live in one folder on this Mac, outside the repository, read by every checkout:

```
~/MicroscopeMind/catalog/            (MICROMIND_CATALOG moves it)
  models/<id>/
    <revision>/                      one whole revision: its pinned files, checked, read-only, and receipt.json
    .staging-<revision>/             a download in progress; nothing ever reads it
    .verified-<revision>.json        when its files' content was last checked again, and whether it still matched
    v<n>/                            a version made here (fine-tuned weights: finetuned.py), never written over
  mine/models/<id>/                  your own entries
```

- **One way in** (`services/orchestrator/downloads.py`): files go into the staging folder, each is checked against the
  manifest (its bytes and content id), and only then is the folder renamed to its revision, in one step. A file that
  doesn't match is deleted and said; nothing unchecked is ever used.
- **A revision is never written over.** A manifest that pins a new revision is a new folder; the old one stays until you
  remove it.
- **Downloads resume** where they stopped when the server supports it, and start that file again otherwise. They check
  the free space first, wait while a click is being answered, and run one at a time.
- **Gated models** (SAM 3.1, MedSigLIP, DINOv3) need your Hugging Face key and their licence accepted on their page; the
  download says which.
- **Mirrors** (a file's `mirror`, C7): when a file's own place has lost it or is down, its mirror is tried, but only one
  whose redistribution rights the entry states exactly. The bytes are checked against the same sha256 before anything is
  published, the mirror never gets your Hugging Face key, and a file whose place is gone with no mirror is said so.
- **The loaders read only that revision**, offline (`gpu_worker/model_files.py`: `HF_HUB_OFFLINE`,
  `local_files_only`, explicit checkpoint paths). Nothing downloads on first use: a model not downloaded is "Not
  downloaded yet", with a Download step.
- **Until you adopt,** a model already in a library's own cache (the Hugging Face cache at the **pinned** revision,
  Cellpose's or micro-sam's folder, each file checked) is read from there, so what runs is always the pinned revision.
  "Downloaded" means exactly what the loader would find: a model read as one Hugging Face folder counts only when one
  snapshot holds all its files.
- **A file inside an archive** (Trackastra's weights come as a zip) is pinned as a member: the archive's own hash and
  size (`archive = { sha256, bytes, member }`), and the file's. The archive is fetched once, checked, unpacked, and
  removed once the revision is published.
- **A download names its files:** a manifest that says `download` and pins nothing is a mistake (`make catalog-check`).
  Live 2026-10-12 Trackastra did, and its library fetched its own weights on first use, unpinned.
- **Checked again once per start,** each published file by its content, at background priority: a file that changed
  (only its content says: the size is checked at every read) makes its card say so, and Download fetches it again (the
  changed revision kept aside, never deleted).
- **Made here** (fine-tuned Cellpose-SAM) is kept in its entry's own folder too, version by version; `make catalog-adopt`
  brings in versions made before.
- **Seen at once:** a download, an install or a new manifest is seen with no restart (a picture model that wasn't
  downloaded loads the moment it is; an editable install is found as Python would at start).

| Command | What it does |
|---|---|
| Download (Capabilities, an option's step) | downloads it in the app, with its progress there and a line in Activity |
| `make catalog-download ID=<id>` | the same from a terminal |
| `make catalog-adopt` (`ID=` for one) | moves what this Mac already downloaded into the folder: APFS clones (no immediate full duplicate), checked, published |
| `make catalog-pin ID=<id>` | writes a Hugging Face model's `files` (sizes, hashes) into its manifest from its pinned revision (`ONLY="*.pt"` for some) |
| `make catalog-proof` | the decisive proof: every model the catalog runs on this Mac is **passed** (its operations really ran, through the app's own check, from its folder alone, the network refused in every process;
an entry with a proof of its own, a Fiji binding, a contributed adapter or a job, by that proof, the same way), **failed** (the check's words) or **missing** (what to do); red unless every one passed (`ONLY=`, `BESIDE_LIVE=1`) |

## Environments the app makes: Install is a button

A model whose code needs a Python of its own names it in its manifest:

```toml
env = { python = "3.13.5", lock = "env.lock", imports = ["micro_sam.util"] }
```

- **`lock`** sits beside the manifest: every package at an exact version with its hash (`uv pip compile
  --generate-hashes --python-version 3.13 --python-platform aarch64-apple-darwin`, `MACOSX_DEPLOYMENT_TARGET=14.0`).
- **Install** (its card, the agent on your yes, command `model-install`; `orchestrator/envs.py`) makes it in
  `~/MicroscopeMind/catalog/envs/<id>/<generation>/`:
  1. uv's own Python at the pinned version (never this Mac's);
  2. `uv pip sync --require-hashes`: exactly the lockfile, nothing resolved here;
  3. its proof: its `imports` run with that Python and a clean environment (nothing of your shell: no PYTHONPATH,
     CONDA_PREFIX or DYLD_*);
  4. its receipt, written last. Only then is it ready.
- **Built in place, never moved:** an environment's scripts hold its own path, so it is made where it stays; the
  generation is named by the lockfile's and the Python's hash, so a new lockfile is a new folder beside the old one.
- **A failed or stopped install** is never used; its card says why (a Stop is said as stopped, "you pressed Stop",
  never as a failure), and Install makes it again from the start. An install from a card's facts (an update) has its
  Stop there too. **Remove** (`model-uninstall`, its card's details, after a confirm) takes every installed version away
  and stops its worker; its entry stays.
- **The model runs only from it:** micro-sam's server and Trackastra (`noncommercial/trackastra_server.py`) are started
  with that Python; the app's own never imports them.
- **A Python made by hand before** (micro-sam's `~/.cache/micromind/venvs/microsam`) still runs it, and its card says so,
  with Install to make the catalog's pinned one.

micro-sam (187 packages) and Trackastra (71, Gurobi among them, never called) have theirs. `make catalog-proof` proves
each in the environment the app made, never one made by hand.

### The app's own Python: what the built-in models share

Every other model on this Mac (runner `gpu-worker`) runs in the app's own Python, made by `make setup` and by the Mac
app's first launch from one lockfile, `services/uv.lock`. It is an environment like the ones above, and the same rules
hold for it (`orchestrator/shared_python.py`):

- **What the code imports comes from the lock.** A package the app's code imports is in `services/pyproject.toml` (then
  `uv lock`), or is an install step the catalog says (`install:sam3`), or is imported only by code that runs in a Python
  of its own (`tests/test_shared_python.py`).
- **A model's own pinned code is read for its imports**, never declared beside it (its hashes fix its imports): a
  Hugging Face model run with `trust_remote_code` (Molmo 2) is checked when it is pinned (`make catalog-pin` says each
  import and exits 1 when the lock lacks one), by the catalog's CI after its download, and by readiness on this Mac,
  whose card says "Install what its code imports" with the missing package instead of Ready.
- **Nothing installed beside the lock re-pins what it holds.** A setup step such as `make sam3-setup` adds only what the
  lock lacks, so the next `make setup` leaves it working.
- **A change to the lock is an update of every built-in model:** the catalog's CI checks the imports of each one's
  pinned code against the new lock (its code files only, in seconds), and the PR needs `make capability-check`, which
  proves them run and ends with the Python it ran in and how that differs from the lock.

Live 2026-10-19 (PR #346): Molmo 2's pinned code imported einops, which only SAM 3.1's setup had happened to install, so
a fresh setup and the shipped app failed at load after readiness had said Ready; and SAM 3.1's setup pinned two packages
below the lock's, so the next `make setup` broke it.

## Fiji: its plugins, its generations, its bindings

The app keeps one Fiji of its own (`catalog/models/fiji`: a pinned Fiji Latest build for Apple Silicon with its own
JDK, 669 MB, by its published sha256), run headless.

- **Every extension listed** (the Toolbox's **Fiji** section, its folded list, `catalog/inventories/fiji-extensions.json`, made by
  `make fiji-inventory` and reviewed): each row of imagej.net's list of extensions, with how it installs and the
  evidence. Its page's own `update-site`, else its artifact's jar in the pinned build ("ships with Fiji") or in one
  site's index, else a reviewed route (`fiji-routes.toml`). An unmapped one stays listed: "No install route yet", with
  why.
- **Complete generations** (`orchestrator/fiji_generations.py`, `catalog/tools/fiji/<n>/`):
  - **Set up Fiji** makes generation 1: the build unpacked and proven headless.
  - **Add to Fiji** on an extension makes the next generation: the one in use cloned, the extension's update site
    added, only that site's files installed, and every proof run again. Only then does it become the one in use.
  - **Never changed in use:** a generation in use is left as it is. One that fails a proof is kept apart, and the one
    in use stays.
  - **Its receipt** is the whole installation: the base build, the sites on, and every file by its checksum.
  - **Go back** makes the previous generation the one in use.
- **Installed is not usable:** a Fiji function runs in the app only through a **reviewed binding**
  (`kind = "tool-function"`, `orchestrator/fiji_bindings.py`, `services/fiji_worker/server.groovy`). It declares its
  input, its output and its headless route, and its proof runs it on every generation. Everything else says "No binding
  yet".
- **Analyze Particles (Fiji)** is the first binding:
  - **Its input:** the picture, passed explicitly.
  - **Its output:** the particles, through the image's Overlay, never the ROI Manager window.
  - **Its proof:** on every generation it is checked against the same steps as an ImageJ macro and an independent
    reference in a separate Fiji: identical particles on three generated fixtures (three particles; an edge-touching
    L and a ring with its hole; none).
  - **Its place:** a Cells choice you pick, said as particles, never a fallback.

## The Models page (the Toolbox; the header's chip)

Every entry here, on the Toolbox's first view in the studio (`#toolbox`, old link `#models`; `orchestrator/catalog_view.py`,
`apps/studio/src/modelspage.ts`). Above it, on both of the Toolbox's views, the band (`apps/studio/src/keystrip.ts`):
**Keys**, always open, each with how many things it turns on, where it comes from and Add key or Change ("The example works
without a key"); and **Add a capability**: from a link, from a file, or have an AI build it (Add your own, below). Its
sections are the summary's groups, in its words:

| Group | What is in it |
|---|---|
| Needs you | what your current choices use and can't run yet: what the header's chip counts ("2 things need you", green "All set" when none) |
| Ready | it can run here now |
| Available to add | nothing you chose needs it now; one step turns it on (a key, a download, an install). The models fixed in the code are here when they can't run (Trackastra for linking, SAM 3.1 for find all like this) |
| Unavailable here | not in this edition, or broken when the app checked it |

Each card shows:
- **Its one action:** Add key, Download (with progress and Cancel), the licence page, or how to install it.
- **What it is used for:** "Outlining (your choice) · Cells".
- **Why?** Each fact with its reference: the key and where it comes from, the files (revision, size, Show in Finder), its
  code, this edition, the app's last check.
- **Details:** the version, revision, licence, source and its files.

After them: **From the community** (always shown: New for you, the catalogs you follow, how to share yours) and **Fiji**:
the functions connected by a reviewed binding (`inventories.bound`), each said ready only when its own card's readiness is
(never because it is connected or installed), apart from the extensions that only install ("1 Fiji function connected · set
up Fiji to use it · 555 extensions listed: 209 install in Fiji, 346 have no install route yet"; "· 1 ready here" once it
is), the full list folded under it.

**Keys lead on every visit**, the most useful first. One key never turns everything on: a Google key sees and answers, and
talking needs OpenAI.

When the model your Seeing choice uses can't run, the live view says so with Add key.

**Options that run more than one model** name the others (`Option.also`: Molmo's pointing, following, EdgeTAM then SAM 3.1),
and **models fixed in the code** name their entry (`model` in `contract/capabilities.json`), so what each needs is said.

## New for you, and what it can do (the Toolbox's From the community, and its jobs)

**New for you** (`orchestrator/catalog_news.py`): what others added to the catalog reaches this Mac by itself.
- **The app looks, never fetches code:** a while after it starts and then once a day it takes each entry's `model.toml`
  and its pictures from the reviewed catalog (`channels.peek`, into `<catalog>/news/`), never an adapter, a lockfile or
  weights. The metadata transport checks the pinned blobs and bounds bytes, images, total files, disk space and time;
  it fails closed on unsupported hosts. Nothing is installed or run from what it looked at.
- **What is new:** every entry this Mac hasn't known, with its picture, what it does, who added it and whether it was
  reviewed, the ones whose `tags` fit what the session looks at first. It stays new until you turn it on, press Got it
  or Not for me (never offered again), or a month passes. A fresh install is not new from top to bottom.
- **Updates:** a changed channel adapter is offered even when its current version works. Looking at the offer keeps
  the current channel pin and proved runtime. Its preview belongs to the offered commit; the current model card
  keeps its own picture. Bundled code still updates with the app. The news wire includes arrival date and declared
  requirements; an idle conversation receives each relevant offer once, with no automatic activation.
- **Get it** on one only looked at fetches the reviewed catalog at that same commit in full (as Check for catalog
  updates does): its adapter and lockfile arrive on your press, and its card then has its one step (Install, Download).
  The request includes the displayed commit and content identity. A changed offer requires another review. The same
  content keeps its news identity after fetching; superseded or already-enabled entries are not offered again.
- Malformed preview entries are contained. Optional news/outcome refresh failures are logged and retried without
  stopping setup progress. The app serializes news read/mutate/publication transactions so dismissal stays durable.
- Tests and the sim harness never look online (`MICROMIND_CATALOG_AUTO=0`).

**What it can do** (`orchestrator/capability_shelf.py`): the catalog by job, in plain words ("Find every cell", "Tell you
what you're looking at"), its models as the job's options. One colour with one meaning:

| Colour | Means |
|---|---|
| Green | On: one option is ready and has a current passed functional check |
| Yellow | On its way: downloading, installing and proving, or waiting for an approval |
| Red | Off, and one press turns it on here |
| Grey | Not on this Mac, with the reason (this edition, this Mac's memory or system) |

There is no red without a press that turns it on. Each card says where your pictures go ("Runs on this Mac", "Sends
pictures to fal.ai (your key)") and what it costs, including every component of a combined option.
“Using” describes the selected option; a proved alternative remains available without changing that selection.

**Turn on** carries every step through: it picks the job's own choice when it can be turned on here, else an option with
no key to add, on this Mac first; the app's own steps (a download, an install and its proof) run in their queues one
after another; a step only you can take (a key, a licence, allowing a community catalog's code) is shown on the card
with its own control (Add key opens the key sheet). Its targeted functional check uses the bundled synthetic public
example, independently of private bench footage. Completion is filed in the original conversation. If the original
choice cannot run, the job adopts the proved option only when the user has not chosen something else meanwhile.
Stop cancels work owned by this press; a download someone else started stays theirs. Nothing turns on without the
press or a scoped agent offer accepted by the user. The press submits the exact disclosed plan; changed terms require
review. A cancelled intent cannot modify a replacement for the same job. App shutdown closes admission and drains
the actual proof tasks before their workers stop.
The admitted recipe includes all model components, companion data and files. If these material facts change while
setup waits, owned work is cancelled and setup pauses for a new review; ordinary prerequisite completion continues.

**Try it** uses a proved option for one request or gesture, keeping defaults. An empty stage opens the same verified
public example through the normal library/source path and waits for its frame. Click jobs guide the next click or box,
Talk starts the normal voice path, and other jobs use the ordinary agent with their required inputs. A medical mode
requiring a blood or Pap subject says when that prerequisite is absent; generated particles do not establish a
biological identity. Tokens belong to the original conversation and connection and are consumed once.

**Space** lists managed runtime copies with their bytes and every model sharing them. Remove checks the reviewed
inventory, refuses active work, unloads resident copies and withdraws only managed revision/generation directories.
Fiji's separate JVM stops before its generations are withdrawn; issued generation numbers remain unique across
removal and reinstall.
Model entries, original model/proof inputs, pictures, datasets and saved results remain. Removed runtime copies need
setup again; this is distinct from scientific qualification.

## Asking the agent

The agent says the same thing the page does (`orchestrator/brain/toolbox/catalog.py`), in plain words, pulled when it is
asked and never pushed into a prompt:

| You say | It does |
|---|---|
| "what do I need to set up?" | `models`: what your choices need, what is ready, what is available to add, and your keys |
| "why can't you use SAM 3.1?" | `models` about it: its status, uses, facts and next step; a download is offered ("Want me to get it? 3.5 GB") |
| "yes, download it" | `get_model`: the offered download starts, only on your own yes (the offer's id, your words quoted and checked), and is said started, never done |
| "add my OpenAI key" | `open_models`: the Models page opens at a card that waits on that key, with Add key there, done once the studio says it shows it |
| "what does my Google key turn on?" | the catalog's own words for it, the models that use it, and that it turns on only what it lists |
| "anything new?" | `models` with `new`: what others added since you last looked, none turned on by itself |
| "turn on finding every cell" | `turn_on`: the job's Turn on, on your own words (quoted and checked); a key step stays on its card |

**Keys are yours alone:** no tool takes a key, and the agent never asks you to say or type one in the conversation.

## Is it ready here? (`make doctor`)

`orchestrator/readiness.py` (ADR 0019) says, for every model, one status and what to do next, the same on the Models
menu, the Capabilities page, the agent's refusals and `make doctor`:

| Status | What it means | Its next step, for example |
|---|---|---|
| Ready | it can run here | |
| Needs setup | a step first | "Add your Google Gemini API key" (where to get one, where to put it); "Accept its licence on Hugging Face" (the page); "Download 3.5 GB" (the Download button, or `make catalog-download`); "Install Trackastra" (`make trackastra-setup`); "Make its weights here" |
| Unavailable here | not in this edition | |
| Broken | its last check on this build failed | "Check again" |

**What each status is worked out from:**
- **Downloaded:** its pinned revision is in the models folder (with its receipt), or each of its `files` is in an older
  cache at the pinned revision, at its size and content id.
- **Keys:** a key that only obtains gated weights is needed until they are downloaded, and not after.
- **Reading is cheap:** it loads no model, starts nothing, downloads nothing and calls no provider. Opening a menu never
  costs the live view.
- **Run time still checks:** the runner checks the worker and memory, so "ready" is never permission to run.

`make doctor` prints every model in the catalog this way.

## Observed, not declared (what the outside world said)

Some requirements can't be read on this Mac: whether Hugging Face has given your account access to a gated model, and
whether a key works. A manifest *declares* them; the app *observes* them (`orchestrator/observed.py`), keeps the answer
with when, and every card, the chip, readiness and the agent say that answer, never the declaration. Will, 2026-10-12:
DINOv3's licence was accepted and approved, and the card still said "Accept its licence on Hugging Face".

**Where each kind of requirement comes from** (`observed.SOURCES`; a test fails on a kind with none, so a new runner's
kind arrives with its source):

| Kind | Source |
|---|---|
| key | read here (is it there), and asked of its service (does it work: one free call, as Add key's) |
| licence | asked of Hugging Face (one HEAD of the smallest pinned file, with your key); elsewhere, your word ("I've accepted it") |
| download, install, weights, platform, memory, input | read on this Mac at every look |

**When it is asked:** at start, when a key changes, when the Models page opens (what is older than 2 minutes), every
5 minutes while not yet granted (so an approval shows by itself), hourly once granted, on a card's **Check again**, and
when the agent is asked about a model. A gated model's access is asked on its own only once there is a Hugging Face key
(without one Hugging Face can only say it can't tell, so nothing is sent; Check again still asks). A real download is an
observation too: refused for want of access, or done.

**What a card says:** "Hugging Face: your account has access (checked 21:29)", "…hasn't been given access yet: accept
its licence on its page (approval can take a while) (checked 21:29)", or "Its licence: not confirmed yet" when nobody
could ask (offline, no key). **Unknown is never a refusal.** No ✗ on a card is a declaration: each is read here or
carries when it was said (a test walks every card).

**Real use is an observation too** (kind "use"; Will, 2026-10-29: Claude Opus 5.5 answered him all session while its
card said "Not checked", grey). Every run of an entry the running app makes (a run of a model or method, a cloud call, a
voice session that opened) keeps its latest outcome with when: it ran and returned, or it ran and failed with its own
error. What the app cut short itself (its deadline, a newer request, the monthly cap), what never reached the model
(busy, unavailable, refused in words), a blind trial before its vote and the app's own check are not use. The card says
**Working here · last ran 14:02** from a run that worked, **Broken** with the failure's words from one that failed (the
next run that works clears it), and **Ready · not checked yet** when it can run and nothing of it is recorded; the newer
of a real use and the app's own check counts. They are kept beside the app's own checks (`uses.json` in the logs
folder, docs/saves.md), so a test's or the sim's home keeps its own; benches and `make capability-check` record none.

Answers are kept in `<catalog>/observed.json`, so a restart shows the last one with its time until it is asked again. No
key's value is ever kept there, logged or said.

## Data types

What an operation takes and gives, by id (`manifests.DATA_TYPES`). Support is said exactly: a model that takes a 16-bit
plane or a volume says so, and is "can't run with this input" on the camera's view. Nothing is converted silently into
the camera's picture. An entry joins a task's comparison only when its output means what the task needs.

| Id | What it is |
|---|---|
| `image.2d.rgb8` | the camera's view: 8-bit RGB, one plane |
| `image.2d.gray8` | an 8-bit grey plane |
| `image.2d.gray16` | a 16-bit plane (fluorescence, scientific cameras) |
| `image.3d.zyx.float32` | a volume: z, y, x |
| `clip.2d.rgb8` | camera frames over time |
| `masks.2d` | one mask for each thing asked about |
| `labels.instance.2d` | every instance found, one label each |
| `rois.imagej` | ImageJ ROIs |
| `table.objects` | one row per object, each column with its unit and definition |
| `tracks.graph` | observations linked over time, with divisions and merges |
| `vectors` | numbers describing a picture's look, one vector per picture |
| `links` | pairs of objects in two views judged the same, each with its score |
| `scores.text` | names ranked with scores |
| `points.2d` | points on the picture |
| `text` | words |
| `volume.3d` | a volume as it was stored: its three spatial axes, voxel sizes and orientation, each with its source |
| `registration.atlas` | a volume registered to an atlas: the atlas in its downsampled space, region volumes, the field |

## Add your own (in the app)

A Cellpose model you or someone else trained becomes one of your choices in four steps, none of them code
(`services/orchestrator/mine.py`; ADR 0021):

1. **Add it.** On the Models page's **Add your own**, either:
   - paste its Hugging Face link (a repository with one model file, or the file's own link);
   - paste a file's path;
   - drop the file on the page;
   - or put it in `~/MicroscopeMind/catalog/mine/models/` and press "Add it".

   Its entry (`mine/models/<id>/model.toml`, runner `cellpose-file`) is written for you. You can also run
   `make model-add FROM=<link or path>`.
2. **Download it** (a link), as any model: checked, read-only, in its own folder beside its entry.
3. **Turn it on.** Its check runs once, in a process of its own, with no keys
   (`services/noncommercial/cellpose_file_check.py`):
   - the file holds only weights (`torch.load(weights_only=True)`): one that needs code to load is refused without
     running it;
   - this Cellpose builds a model from it;
   - it finds a sane number of cells on the bundled public synthetic example (a functional check, not scientific qualification).
   The setup owns the check: Stop or app shutdown cancels and reaps its process before finishing, and a cancelled
   check cannot publish a successful activation. No private benchmark image is needed.
4. **Use it.** Once on, it is a choice of its own under Cells, in the Models menu, Capabilities, `use`, `set_default`
   and `compare_models`. It runs in a GPU worker of its own, never the live view's. **Turn off** takes it away, and
   a setting that chose it goes back to the default.

**Adding runs nothing.** Until Turn on, nothing imports, loads or runs it. Its card shows each fact: its entry, what
runs it, its maker's licence words, its files, its check and whether it is on. A restart keeps what was on.

**A BioImage Model Zoo model** (its page `https://bioimage.io/#/?id=affable-shark`, its nickname, or its files' address;
`orchestrator/link_recognise.py`) becomes an entry run by a **reviewed template**, `catalog/templates/bioimageio`:
- **A reviewed runner, a model that isn't:** the template's adapter and locked environment are copied byte for byte; the
  model's own files (its `rdf.yaml`, its TorchScript or ONNX weights, its test tensors) are pinned by their hashes, and its
  cover is the entry's picture. A TorchScript model is code its makers wrote (PyTorch's security policy), so the runner
  loads and runs it **inside macOS's sandbox** (below), and its card says so. Its hashes say which model it is and its own
  test that it runs as its makers ran it; neither says it is safe.
- **Proven twice:** Install makes its Python, then the runner runs the model on **its own test input** and compares the
  result with its own test output before any answer, then on a real picture. A model that doesn't run here as its makers
  ran it never answers.
- **What it runs:** 2D, 1 or 3 input channels, the spec's preprocessing (zero mean unit variance, scale range, scale
  linear, clip, binarize, sigmoid), output as labels, a probability, or foreground and boundaries.
- **Said, never run:** TensorFlow weights, weights that need the makers' own architecture code (`pytorch_state_dict`
  alone), 3D models, other preprocessing. Its licence is its own component: open for the common open licences,
  non-commercial when it says so or when nothing says.
- **One more press:** "Turn it on" right after Add carries its download, its Python and its proof (`model-turn-on`).

**A Hugging Face segmentation model** (its page, `https://huggingface.co/<owner>/<model>`) whose card says
`image-segmentation` with the library transformers becomes an entry run by the reviewed template
`catalog/templates/hf-segmentation` (transformers 5.19, torch 2.14.1, torchvision 0.29.1, locked by hash; template version 3
added torchvision, which SegFormer's image processors need, the PIL one included):
- **What it runs:** SegFormer, UperNet, DPT, BEiT, MobileViT, Mask2Former and MaskFormer, through transformers' own
  classes only (`local_files_only`, `use_safetensors`, never `trust_remote_code`).
  - **Semantic models:** each connected region of a class that isn't background is one thing found. Background is only
    a class named so ("background", "bg", "unlabeled", "void"); with none named, every predicted class is kept, class 0
    included (template version 2; an entry made from version 1 kept class 0 as background until it is added again).
  - **Universal models:** the model's own instances.
- **Pinned at its commit:** `config.json`, `preprocessor_config.json` and `model.safetensors`, each by its hash. Never
  the pickle weights.
- **Inside the sandbox,** as any model someone else made.
- **Said, never run:** weights only in PyTorch's pickle format, weights split over several files, a config asking for its
  makers' code (`auto_map`), or a kind of model the runner doesn't know.
- **Gated models** are read with your Hugging Face key.
- **Any other Hugging Face link** is a Cellpose model file, as above.

**Any other link** (GitHub, a repository with several model files) is kept as a **skeleton**: listed, with what it needs,
never runnable, until you **have an AI build it**. It is named after its repository, whatever commit, folder or file the
link points to (a raw.githubusercontent.com file too); the commit a link pins is kept in its source (`git`, `rev`).

**Added once:** the same link at the same pinned revision (made with the same template version), or the same file, added
again is said as already added, with its next step: nothing is written or downloaded again. Added again with another
version of its template, it is a new entry (the comparison list says which template version each was made with), and
files another entry already has here at that revision are cloned from it and checked, never downloaded again.

**No picture to add:** Add recognises any link without one. Your picture is asked for where it is used: **Set up for
comparison** (it is compared on it before **Use it**) and **Have an AI build it** (each try runs on it).

**Have an AI build it** (offered as soon as such a link is added, its cost on the button; command `model-build`; C4,
`orchestrator/drafts.py`): a researcher at the builder's effort ("build": up to 15 minutes, about $2, under your
research key and cap) reads the link's pages, pins its weights and drafts an adapter; the app does everything that
touches this Mac:
- **Wheels only:** its requirements are locked with hashes as wheels (`uv pip compile --only-binary :all:`), so no
  package's build script ever runs.
- **Every try is sandboxed:** the draft runs on the view now (or the app's representative frame) inside macOS's sandbox
  (below), and its result or error goes back to the researcher for the next try.
- **A draft is never a choice:** the Models page lists the drafts with how each try went. **Use it** (`model-draft-use`)
  makes one that ran one of your own entries, its files adopted into the models folder; it is then installed, proven and
  turned on as any of yours, still sandboxed, its entry saying an AI drafted it. Whether its results are right is yours to
  judge: a draft that runs is not a draft that is correct.
- **Said plainly when it can't:** a model that needs a GPU, a service, an account or data that can't be pinned ends the
  run with why.

**To share it:** "Publish it" below. `make catalog-check` says what is wrong with any entry, yours too.

## Add a model with its own adapter (runner `python-env`)

A model or algorithm whose code the app doesn't have is added as **one folder and nothing else**: no change to the
app's code (`orchestrator/env_runner.py`; InstanSeg's `catalog/models/instanseg-brightfield/` is one):

```
<id>/
  model.toml     runner = "python-env", adapter = "adapter:segment", env = { python, lock, imports }, [[implements]]
  env.lock       every package by its hash: uv pip compile requirements.in --generate-hashes -o env.lock
  adapter.py     segment(picture, params): an RGB uint8 picture in, a label map (one int per object) or polygons out
```

- **Its weights** are the manifest's pinned `files`, fetched by Download like any model's (a member of a release's zip is
  pinned as one: `archive`). The adapter reads them from `MICROMIND_ENTRY_FILES` and must fetch nothing at run time.
  Its Python process uses `-I` and a scrubbed environment, with Python socket/DNS refusal installed before adapter
  import. That Python guard alone allows loopback/Unix sockets and does not contain filesystem or native/subprocess
  access. Entries from an unreviewed community catalog, shared files and your own entries (including those made from
  reviewed templates) also require the [macOS sandbox](#code-nobody-reviewed-runs-inside-macoss-sandbox), during import,
  proof and use; they are refused if it is unavailable. Their installs accept ready-built packages only. Reviewed
  catalog entries retain the Python guard and run with your permissions, without that additional OS boundary.
- **Install** makes its environment from its lockfile at its permanent place, keeps a copy of the entry with it, and
  **checks its adapter contract by running it**: output must be a label map the picture's size, or polygons inside it.
  The input is the configured assets store's leaf check image when readable, otherwise a generated three-disc RGB8
  fixture. The receipt records the actual evidence class and identity, dimensions, exact RGB8 pixel SHA-256 and retained
  input-file hash; an asset also records the original encoded file hash. The exact RGB8 input is retained beside the
  environment as immutable `<generation>.<sha256>.proof-input.npy`, with `<generation>.proof.json`.
  Rechecking retains previous input bytes; legacy `<generation>.proof-input.npy` receipts remain readable.
  Install, Models, choices and
  contribution text identify generated inputs. This is a contract smoke check, not scientific qualification.
  Older receipts without input provenance remain honest about that gap; Install again before contribution preparation.
- **Only a proven entry is a choice:** of the operation it implements (Cells...), under its own name, in the Models
  menu, `use` and `set_default`. One whose proof failed, or that is taken away, is no choice, and a setting that chose it
  goes back to the default.
- **A new version installs beside the one in use:** when the catalog brings a new version, the one installed keeps
  running from its own copy, the card says "Update available", and the new one is used only once its Install made it
  and its proof passed. A new version that fails its proof leaves the old one in use, and its card says so with why
  ("Update B failed its check: …"). Once the newest is installed and proven, no Install is offered for it.
- **Turn off** (any entry with its own adapter: yours, the reviewed catalog's or a community channel's; `model-deactivate`,
  `env_runner.turn_off`): no longer a choice and its worker stopped, nothing deleted (`off.json` beside its
  environments); **Turn on** makes it a choice again with the version it had, nothing installed again.
- **Report a problem** (a card's Details; `orchestrator/report.py`, command `model-report`): the facts of what went wrong
  (the entry, its versions, each environment's proof and its words, its last failed install, this Mac's macOS and chip),
  prepared to read and copy. **Nothing is sent,** and only whitelisted facts are in it: no picture (a proof's input is
  named by its kind, never its path), session, folder or environment value, and **no error text**: each failure is a
  stage and a reason code from a fixed list ("timed out", "gave the wrong output"…), since an error may echo a path or a
  specimen's words. The raw words stay on this Mac, shown apart, never in what is copied. Its page (the reviewed catalog's issues, a community catalog's own; one of yours or a shared file goes
  to whoever made it) opens with only a title.
- **Go back in one press:** a new version whose proof passed but that you don't want goes back to the one before from its
  card (**Go back to …**, command `model-version`; `env_runner.choose`). The choice is kept beside its environments
  across restarts, the newer one's worker is stopped at once (the register that follows retires a worker whose
  generation is no longer in use) and never used again, and **Use the newest** undoes it.
- **Made for another version of the app** (`requires`): listed under "Made for another version of HookeUI" with
  why ("needs a newer HookeUI", "made for an older python-env runner"), never run. Runner `python-env`'s contract
  (version 1) is `services/env_worker/server.py`'s: the picture in, a label map or polygons out.

Yours can start in `~/MicroscopeMind/catalog/mine/models/<id>/`: Install and its proof work there the same way.

## The capability contract (format 2): one folder, every actor gets it

A capability declares what it takes and gives the way a ComfyUI node does (ADR 0024; the full schema, each field traced
to ComfyUI, napari, bioimage.io, SciJava and Galaxy, is `docs/design/2026-10-15-one-way-in.md`):

```toml
requires = { catalog = 2 }
id = "spot-measure"
category = "Measure"
outcomes = { empty = "no spot is above the threshold" }

[[inputs]]
id = "image"
type = "image.2d.rgb8"        # data: an open type id; DATA_TYPES are the ones the app shows in a place of its own

[[inputs]]
id = "threshold"
type = "int"                  # a parameter: int, number, string, bool or choice
default = 128
min = 0
max = 255
label = "Threshold"

[[outputs]]
id = "spots"
type = "table.objects"
```

- **One declaration, every actor:** a proven capability is listed in Actions in its category's group, with a form built
  from its parameters, and is run by the agent (`run_capability` with its `params`), by Try on its card, and by
  researchers (`run` with operation `capability`), with no app code of its own (`orchestrator/kinds.py`, the one
  registry; `capabilities.contributed`, its rows).
- **Your adapter** is `function(picture, params)`: `params` are its parameters' checked values and the `hidden` ones it
  asked for. It returns its output (several: a dict by output id), `{"unsupported": "why"}` to refuse an input, or raises.
- **Runners plug into the same door** (`kinds.plug`): python-env, and Fiji (`orchestrator/fiji_runner.py`: a `[fiji]`
  table names an ImageJ command and its options as the macro recorder writes them; proven identical to Fiji's own macro).
- **How, step by step:** the skill `.agents/skills/add-a-capability/SKILL.md` (`make capability-preflight`,
  `capability-new`, `capability-lock`, `capability-derive`, `capability-test`). A test's input is a picture named
  `example-*` beside the manifest, so it travels when the capability is shared.
- **What it makes, in its words:** the run page's Makes line is its output's `label` ("Bright regions (not cells)"), and
  a finished run says the output's `tooltip` under its outcome ("Bright regions are not a cell count"); left out, the
  app says its type in a few words.
- **Its settings, before it is set up:** its parameters (its non-picture `[[inputs]]`, `catalog_view.py` `fields`)
  show on its run page (an `advanced` one under More options), each `tooltip` as the line under its field; those under
  Settings are disabled until its step is done ("Set up Fiji to change these"). An entry that declares none says its
  settings show once it is set up. None is ever invented.
- **Types are open:** any lowercase type id is a type; one the app doesn't know is kept and handed on as data.
- **Four outcomes:** a result; empty (a valid input, nothing found); unsupported (refused, with why); failed.
- **Reserved for later formats** (said as "not supported by this app yet"): `axes`, `mode`, `steps`, `kind = "method"`.
- **Format 1** (`[[implements]]`, below) still reads; a format-2 entry is read as the same operation contract.

## A new kind of job, with no app code

A model for a job the app has (outlining, finding cells) is a folder. **A new kind of job** (denoising, a measurement of
its own, an analysis that writes a table) is a folder too (`orchestrator/kinds.py`; C6): an entry with its own adapter
declares an operation of its own, taking the camera's view and giving an output the app already shows and keeps.

```toml
[[implements]]
operation = "denoise"          # its own: a short lowercase word with hyphens, never one of the app's operations
impl = "my-denoiser"
input = "image.2d.rgb8"        # the camera's view
output = "image.2d.rgb8"       # or table.objects, text, points.2d, labels.instance.2d; tracks.graph takes clip.2d.rgb8
```

| Output | Its adapter returns | Shown | Kept |
|---|---|---|---|
| `image.2d.rgb8` | an 8-bit RGB (or grey) picture, at most 4096 px a side | in the magnifier, "Made by … (not the camera's picture)" | a result, with its picture |
| `table.objects` | `{"columns": [...], "rows": [[...]]}` or a list of dicts | its first rows in the answer | a result, with its table |
| `text` | a string (at most 20,000 characters) | in the answer | a result |
| `points.2d` | `{"points": [[x, y], ...]}` on the picture | the frame it ran on, marked by the app (not the live view) | a result: its points in that frame's pixels (no sizes), their table |
| `labels.instance.2d` | an int label map, 0 the background | the cells layer | a population ("Outlines"), measured |
| `tracks.graph` (takes `clip.2d.rgb8`) | `{"tracks": [{"points": [[frame, x, y], ...]}]}` over the frames it was given (at most 12 of the last seconds) | the tracks layer | a tracks item in slide coordinates, speeds measured by the app |

- **Proven by what it gives:** Install's proof runs it on a real picture and checks the output is the one declared.
- **One operation like any other:** once proven it becomes an operation the runner knows, so Activity says what ran and
  the outcomes keep it; taken away, its operation goes too.
- **Checked on both sides:** the worker checks what the adapter returns, and the app checks the reply again, reading it
  only within its output's limit (a reply announcing more is refused before any of it is read). What it made is hashed
  by the app (its execution's `producer`).
- **Its makers' output, never the camera's view:** a picture is labelled with what made it, and words and tables reach the
  agent only as untrusted data.
- **Run it:** the card's **Try it on the view** (command `capability-run`), or ask the agent ("denoise the view with my
  denoiser": tool `run_capability`, there once one is on).

## Jobs, native parts and data (brainreg)

Some work takes minutes and writes files rather than outlining a picture: brainreg registers a whole brain imaged as a
volume to an atlas. Its entry (`catalog/models/brainreg/`) is an entry with its own adapter whose `[[implements]]`
output is a job's (`registration.atlas`; `orchestrator/jobs.py`):

- **Its native part** (`env.native`): what a lockfile can't hold. NiftyReg, the programs brainreg runs, is conda-forge's
  Apple Silicon build and the three libraries it loads (`libpng`, `libzlib`, `libcxx`, at the versions its build
  declares), each pinned by its URL, bytes and sha256 as its `.tar.bz2` build, unpacked by the app with Python's own
  tarfile into the entry's own Python's prefix (a `.conda` is refused: its zstd needed whatever zstd this Mac had). brainreg's wheel ships Intel-only macOS binaries that link a
  Homebrew libpng at `/usr/local`, so it is pointed at that prefix (`CONDA_PREFIX`), never at anything found on the Mac.
  `env.commands` (`reg_aladin -v`, `reg_f3d -v`) prove the native part runs from there.
- **Its data** (`data:allen-mouse-25um` in `needs_to_run`): the atlas is an entry of its own (`kind = "data"`): every
  file brainreg reads (132 files, 40 MB, from BrainGlobe's bucket) pinned by its bytes and sha256 and downloaded like a
  model's weights. Its card's Download fetches it; a job is given that revision's folder and never fetches anything.
  The job's readiness and execution include the data's component restrictions, including when downloaded or pinned
  by an earlier run; the Allen atlas remains restricted to the research edition.
- **Its proof is a real run:** Install builds its Python, adds its native part, runs its commands, then registers
  brainreg's own sample brain (one of its pinned files, `test_brain.zip`: 270 slices, 50/40/40 µm, orientation psl) in
  its environment with the network refused. Only then is it installed and proven (about 6 minutes).
- **How a job runs:** `services/env_worker/job.py` with the environment's Python, as a process group of its own (a cancel
  ends all of it, and any descendant that left the group with setsid: `owned_process.terminate` reads the child's own
  tree by parent before it signals anything, and signals nothing else), its request and log in its folder, its result written last. `make catalog-proof` proves it the same
  way.

`make catalog-install ID=<id>` does what an entry's Install button does, from the command line.

### Registering your own volumes

On the Models page, **Brain volumes (brainreg)** (shown once brainreg is in the catalog; `orchestrator/registrations.py`):

- **Add a volume:** choose a TIFF or OME-TIFF, or give its path on this Mac. It is kept as it was (`jobs/<date>/v1/`),
  and brainreg's own Python reads what its file states: each series' axes, shape, channels and time points, and that
  series' own voxel sizes where the file states them. A preview of the first series' middle slice is kept with it: one
  page is decoded, never the series, and a plane past 8 MiB gets none, said.
- **Bounded reading:** a registration reads its stack a plane at a time, only the chosen channel's and time point's
  planes, and is refused before any pixel is read past 8 GiB (`adapter.STACK_MAX_BYTES`); a series not stored as YX
  planes is read whole only when the whole fits. RGB samples are selected on their declared axis, including planar
  samples; combined C/S channel numbers run through all S samples of C=0, then C=1, and so on. This decode bound does
  not establish the process's memory peak.
- **Memory admission:** before starting, a job reserves its catalog baseline plus estimated decode/resampling workspace
  against free memory and the existing live-view margin, including concurrent jobs and all channels/time points a
  full-series decode may need. Runtime sampling records its process group's peak and ends an observed overrun or low
  free memory, retaining the failure and partial outputs in `resources.json`. Sampling is not an operating-system
  memory cap or scientific qualification. Capture's retained-image allowance is separate.
- **Several series:** the file is told which one first. Its scale, axes, channels and times are that series' own, and
  choosing another clears what the last one was told (its channel and time point, its scale unless the file states the
  new one's, its orientation when its axes differ), kept on the series fact as what was cleared.
- **Say the rest:** what the file doesn't state is asked, and kept as yours with when: voxel sizes in µm (z, y, x), which
  channel or time point, and always its **orientation**, BrainGlobe's three letters for its axes (`psl`: axis 0 towards
  posterior, 1 superior, 2 left), never guessed from the file. The adapter permutes these letters with the pixels when
  preparing its ZYX input; the original facts stay unchanged in the record. Status polling keeps unfinished inputs,
  selectors and focus; changed session or series facts replace the old editor. Uploads and manual commands retain
  the session and conversation that started them across streaming and thread dispatch.
- **Register:** a job (`j1`) of several minutes in brainreg's own Python, its progress in Activity.
  - **Cancel** ends all of it. What it wrote is kept, marked, never a result.
  - **Stopping the app** ends its jobs and records them "interrupted"; a job a crash left running is ended at the next
    start, off the event loop. Either way it is never resumed by itself.
  - **Run again** repeats the record exactly: the same volume file (by its sha256), the facts it ran with (whatever the
    volume says now), its environment generation (by its lockfile's hash) and its atlas revision. The record keeps
    the atlas's file identities, checked again before replay, including after the catalog changes. When any of them
    is gone, changed or cannot be verified it runs nothing and says which; **Register** is the new run, with what is
    here now.
- **Jobs are bounded:** a job inherits its caller's cancellation/deadline and defaults to 30 minutes
  (`jobs.PROOF_LIMIT_S`). An Install's Cancel ends its proof's whole process group. A supervisor pipe ends nested jobs
  when the proof process disappears, without scanning or stopping another proof's processes.
- **The record** names exactly what it ran on and with: the volume and its file's sha256, every fact and its source, the
  parameters, the environment's generation and lockfile hash, the native part, the atlas and its revision, brainreg's
  version, and every output by its sha256.
- **The result** is a product of the session (Made in this session; index kind `job`), opened without computing again:
  three slices of the volume as brainreg downsampled and reoriented it, with the registered atlas's boundaries, **in that
  space** (never over the original slices), and the region volumes from `volumes.csv`, largest first. The preview reads
  just the six displayed intensity/boundary planes, each subject to the same plane allowance. Opening the result
  checks its picture against the producer's recorded sha256; a changed picture or missing digest is refused.
- **The agent** (`register_volume`): "register this brain to the Allen atlas" registers the newest volume, asking only for
  what is missing, in the session that asked even if the user changes sessions before dispatch. A live 2D view is never
  registered, and the agent says why.

## Code nobody reviewed runs inside macOS's sandbox

An entry whose code wasn't reviewed (a community catalog's, a file someone shared, any of yours: a model made from a
reviewed template is its makers' code too) runs inside macOS's `sandbox-exec` (`orchestrator/sandbox.py`; C7): it reads only the system,
its own Python and environment, its entry and its pinned files, writes only its own work folder, and has no network and
nothing else of your home folder (not your keys file, not the Keychain's files, not your sessions). Its proof at Install
runs inside it too. Where the sandbox can't run, such code is refused, never run without it. The reviewed catalog's own
entries run as before.

- **Nothing runs while it installs:** such an entry installs **ready-built packages only** (`uv pip sync --no-build`). A
  package built from source runs its build script during install, outside the sandbox and with the network, so its
  lockfile has to pin a wheel for each one; a native part is unpacked by the app (no package scripts) and first runs inside.
- **Other processes:** it may signal only its own children, look up only the system services a Python, NumPy, torch or
  ONNX process needs (never LaunchServices, which could open a link in a browser outside it, nor the pasteboard or the
  Keychain's), and share memory only under the names Python's and torch's own workers use.
- **Its environment:** its home, temporary folder and caches are its work folder; nothing names the catalog, the app's
  folders or a key, and descriptors the app holds don't reach it.
- **Not covered:** a file's existence and size are readable everywhere (never its contents), and GPU access is allowed
  for Metal.


### Portable wheel recipes (catalog/share format 4)

A bundle may pin up to four ready-built wheel artifacts beside its manifest with
`requires = { catalog = 4 }`. Add `wheels = [{ file = "package-1.0-py3-none-any.whl",
sha256 = "<64 lowercase hex characters>", bytes = <exact size> }]` inside `env`.
Each wheel and their combined size must be at most 64 MiB. Ordinary files retain
2 MiB each and 10 MiB combined allowances. Use a plain wheel filename; links and
paths are refused. Redistribution rights must cover every artifact.

Reference each declared wheel exactly once in the hashed lock, for example
`package @ hooke-wheel:package-1.0-py3-none-any.whl --hash=sha256:<same hash>`.
Other dependencies remain exact hashed versions or HTTPS artifacts; include files,
installer options and machine-local URLs are refused. Preparation verifies the
artifact and copies it into the owned generation before resolving its private URL.
Source locks remain portable. No package build hook runs during installation.
The resolved artifact URL retains its verified SHA-256 for installed-origin
checks. New portable preparations record `wheelPreparation = 1` and use a new
generation identity. Pinned older receipts retain their original identity rule;
unknown preparation versions refuse without modifying the saved runtime.

The existing workspace preflight/package and shared-file import paths preserve
these declarations. Older clients refuse format 4. Import grants no trust,
activation or qualification; installation, confined checks and explicit selection
remain separate. Private scientific-host candidates still need their workspace
and qualification. Pictures retain their smaller embedded-package limit; larger
recipes require their separate share file. This mechanism does not establish a
complete animation workflow or clean-machine qualification.

## Share it (no account)

A portable Python capability, confined Fiji capability, or supported operation bundle travels as **one small
file** (`orchestrator/share.py`; formats 1, 2, 3 and 4). Private scientific-host candidates still require their workspace
and independent qualification before public export. The portable paths preserve the existing permission and proof gates:

- **Sharing:** its card's Details has **Share it**: type your name (said where it is opened) and download
  `<id>.hookeui.zip` (one named `.observationui.zip`, from before the rename, still opens). It holds the entry's manifest, adapter, lockfile and picture, laid out as a catalog, with
  `SHARED.json` (who, when, its proof here). Applicable `LICENSE`, `LICENCE`, `NOTICE` and `COPYING` siblings (including `.txt`, `.md` and `.rst`) retain their exact bytes; the same file-size and link restrictions apply. **Never its weights:** each is fetched from where its manifest pins it, checked
  by its hash, so one whose weights are only on this Mac is refused with what to do (put them on Hugging Face first).
- **Sending:** any way you send files: AirDrop, email, a message, a forum post.
- **Opening:** drop it on the Models page (or paste its path in Add your own). It becomes a **community catalog of its
  own**, pinned by its complete content hash, "not reviewed here": nothing of its code runs until you allow code from it (the button names
  who shared it, "Allow code from Maya Okafor's shared file"), then only after its own Install and proof. Removing the
  channel takes it away. Import needs no Git or developer tools. Older Git-backed imports and their identities remain
  readable; new imports publish read-only snapshots directly into the existing channel store. A shared file doesn't
  update itself: it has no online update feed, and a newer file opened is added as its own entry. The legacy channel `commit` field holds its SHA-256 snapshot identity.
- **Refused whole:** a file holding anything a catalog entry doesn't (another entry, a script, a link, a path out of its
  folder), one too big (weights never travel), or one that says nothing of what it is.

**Made with** (`orchestrator/made_with.py`): what a shared thing names as having made it, always what really ran (each
execution's implementation, its version and its generation as recorded when it ran), never what an id means in today's
catalog, never a key, a path or a session, and never a blind comparison's model before its vote:
- **a snapshot** with the app's outlines: inside its PNG (a `tEXt` chunk);
- **a kept result's picture** (a clearer picture, a kind's picture): inside its JPEG (a comment segment), before its
  digest is taken, and in the result's entry (`madeWith`);
- **a recording or a saved session** (a Record dataset, Save this session): its `dataset.json` `madeWith`, from its own
  execution records (`events.jsonl`).

Each recorded version and installed generation remains separate, including removed catalog entries. Legacy records
without a version keep it unknown. Blind producers appear only after their retained reveal event. The
picture, opened on another Mac (added to Assets, its preview), says where each stands there: on, "not on yet" with its **Turn it on**, can't run on this Mac, or not in that
catalog at all (one of the sharer's own: ask them for its share file). Reading it turns nothing on.

**Share it with everyone** (your own entries) sends it to the reviewed catalog through "Publish it" below, from the card:
**Prepare it for everyone** makes the review package on this Mac (nothing leaves it), you read it and its certification,
and **Send it for review** pushes and opens the pull request only with your DCO sign-off ticked. With GitHub device sign-in configured, this uses the API route below; a checkout with Git and `gh` can use the existing Git route.

**With no Git and no `gh`** (`orchestrator/publish_api.py`): signed in to GitHub, Prepare writes the exact outgoing package
on this Mac (the entry's catalog files only, the pull request's text, the signed-off commit message and the account it is
signed as, the official base and every file's sha256), and Send it for review sends exactly that through GitHub's API,
only with your sign-off: a fork of the catalog, one commit, its branch and the pull request. A changed file or another
account is refused; a catalog that moved on requires preparation and review again before any new write. Its review's state (waiting, merged, closed) is
`model-publish-status`. A separate local preparation record binds the model, account, target, base, branch, DCO message,
PR text and exact file list. Editing the review package requires preparation and review again. A retry after a lost
branch or PR response recovers that exact submission; a changed branch or PR is refused for inspection. Both Send and
Status recover an existing exact PR using only reads, even after the official base advances or a merged PR's branch is
deleted. A retained branch with no PR reports its next step; it cannot open a new PR against a changed base without
renewed preparation and review.

**One-click GitHub sign-in** (`orchestrator/github_signin.py`, command `github-signin`) is prepared, not yet working: GitHub's
device flow (a short code the user types on GitHub's own page, approved in their own browser; no password through the app),
its token kept in the Keychain only (`GITHUB_SIGNIN`, scopes `public_repo read:user user:email`). It needs the project's
own OAuth app with Device Flow enabled, registered once by its owner; its client ID is public by GitHub's design and goes
in `CLIENT_ID` (`MICROMIND_GITHUB_CLIENT_ID` overrides it). Until then every step says it isn't set up and what works
today, and GitHub is never asked. The API publisher is implemented and tested with a controlled API fixture; actual sign-in and publication still require that configuration and explicit user approval.

## Publish it

First configure the official repository and your contribution remote with ordinary Git. For a fork, use an
`upstream` remote for the official repository and `origin` for your fork, both with explicit GitHub HTTPS or SSH URLs.
Fetch the intended base beforehand, for example `git fetch upstream dev`: the official repository takes pull requests
into `dev` only (`publish.BASE_BRANCH`; `main` is promoted to from an accepted Beta). Preparation itself makes no network call.

```bash
make catalog-pr ID=<id> BASE_REMOTE=upstream BASE=dev REMOTE=origin OUTPUT=/path/to/new-review-folder
```

This checks the runner, fetchable pinned files, declared component terms, hashed lockfile, prohibited runtime-fetch
patterns and current-version proof. It then prepares the actual `catalog/models/<id>/` files, `proposal.patch`,
`body.md`, `proposal.json` (official base repository/branch/commit and contributor head repository/branch), and the
proof receipt/input in that private local folder. The source checkout stays unchanged. Missing remotes, ambiguous
URLs, unavailable base refs and missing/changed proof inputs fail with a next step. `OUTPUT` must be new; omitted,
it uses the configured catalog's `contributions/` folder. `BASE_REMOTE` defaults to `REMOTE`, and `BASE` to `dev`;
check the printed destinations, especially when using a fork.

Inspect the files, diff, recorded evidence, credit and destinations. Component declarations and technical checks do
not establish ownership or compatible contribution rights. Read the [contributor walkthrough](../docs/contributing/README.md)
and the full DCO certificate printed by preparation. Apache-2.0 plus DCO is the approved first-party direction,
subject to release rights checks; the helper cannot establish ownership or apply terms to uncleared material.

After review and authorization to submit:

```bash
make catalog-pr ID=<id> PACKAGE=/path/to/new-review-folder PUSH=1 SIGNOFF=1
```

`SIGNOFF=1` (`--signoff`) deliberately certifies DCO 1.1 for this exact contribution. Your configured Git name/email
become public in the commit and sign-off. Missing/invalid identity or differing author/committer overrides refuse;
without this explicit choice no process or network call starts. Do not sign on another person's behalf.
Only the supplied package is submitted. Changed package files, changed local routing or an advanced official base
require fresh preparation and review. The command pushes the prepared catalog files to the recorded contributor
remote, then uses an explicit `gh --repo`, base branch and owner-qualified head for the PR. The body comes from the
reviewed file. The retained proof image stays local; the PR body includes its identity and hashes. No files from the
live entry are reread for submission. The package is retained, including if PR creation fails after a successful push.
Nothing is fetched, pushed or sent to GitHub by the preparation step.

**The repository checks it** (`.github/workflows/catalog.yml`, `make catalog-ci`): every changed entry reads, pins and
names its licence (on Linux, in seconds); with the repository variable `CATALOG_PROOF_ON_MAC`, it is also downloaded,
each file checked, its environment made from its lockfile and its adapter proven on an Apple Silicon Mac. Otherwise a
maintainer runs `make catalog-ci IDS=<id>` on theirs. A person reviews every change to `catalog/` (CODEOWNERS). Once
merged, every app lists it at its next **Check for catalog updates** (below), and its card's Download and Install turn
it on, with no app update.

## Channels: new entries without an app update, and community catalogs

A channel is a git repository's catalog (`catalog/models`) at one commit, fetched into
`~/MicroscopeMind/catalog/channels/<name>/<commit>/`: its manifests and adapters only, read-only, never written over
(`orchestrator/channels.py`). Each entry's weights still come with its own Download, checked by their hashes.

Public GitHub catalogs use HTTPS to resolve the commit and read its catalog subtree, without system Git. The full
listing must fit 4 MiB, with at most 4,096 regular files, 8 MiB per file and 64 MiB total. Every file must match the
pinned Git blob identity. Links, submodules, ambiguous paths, truncated listings and redirects are refused before
publication; a failed update preserves the current snapshot and channel configuration. Automatic discovery still reads
only its smaller manifest/picture preview. Other Git hosts and local repository development retain the Git transport;
they do not yet satisfy the no-developer-tools qualification. Neither transport establishes independent qualification.

- **The reviewed catalog** (this repository): **Check for catalog updates** on the Models page ("Where entries come
  from"), or `make catalog-update`, pins its newest commit. An entry merged since this app was made is listed at once,
  as "new in the catalog". An update to an entry the app bundles comes with the app's own update.
- **A community channel** is anyone's repository with a `catalog/models` folder: **Add channel** with its address (and
  a commit, else its newest, which stays pinned until you update it), or `make channel-add URL= [COMMIT=]`. Its row's
  **Check for updates** (`channel-update`) pins its newest commit and says the commit it moved to; `make catalog-update
  CHANNEL=<name> [COMMIT=]` pins its newest, or the commit given.
  - **Said everywhere:** its entries are listed as "community: <name>", "not reviewed here".
  - **Nothing of it runs** until you **Allow code from <name>** (ADR 0021's consent), and then each entry still only
    after its own Install and proof. No longer allowing it takes its choices away, and a setting that chose one goes
    back to the default.
  - **Its entries' files** go to `models/<channel>/<id>/`, apart from the catalog's.
- **Portable Python and confined Fiji entries** (runners `python-env` and `fiji`) come through a channel. Any other needs the app's own
  code, so it is listed under "Made for another version of MicroscopeMind" ("comes with an app update").
- **Your channel** is a repository like this one: a `catalog/models/<id>/` folder per entry, each made and checked as
  in "Add a model with its own adapter" and "Publish it" (`make catalog-ci` checks them in any repository).

## Adding a model today

**A cloud model on a service the app already speaks to** (Gemini, OpenAI, Anthropic): its folder
(`kind = "cloud-model"`, `runs = "cloud"`, `runner = "cloud"`, `service`, `needs_to_run = ["key:..."]`), and an option
for each use, `Option("<id>", "<label>", "<what we measured>", spec="<provider>:<model>@<effort>", model="<id>")`. Its
key, licence and check come from the folder.

**A model on this Mac:**
1. **Its folder:** `catalog/models/<id>/model.toml`.
2. **Its load and run in the worker:** `gpu_worker/worker.py` (`load`, `unload`, and the op it serves) inside
   `Models.loading(<id>)`, so its load is said, measured and limited.
3. **Its implementation** in `orchestrator/operations.py` (`_model("<id>", accepts, worker, option)` and an adapter),
   and its option in `orchestrator/options.py` (`model="<id>"`) when one chooses it.
4. **Its licence component** in `orchestrator/licences.py` (then `make licences`), and its role's option in
   `contract/capabilities.json`.
5. **Its check** on the leaf frame in `orchestrator/model_checks.py`.

Then run `make check`. `test_catalog`, `test_capabilities`, `test_names` and `test_licences` each say what is missing.

**Steps 2 to 5 are already gone for a Cellpose model file** (above: the `cellpose-file` runner, and `make model-add`
writes its folder). Runners for more kinds of model (bioimage.io, Hugging Face pipelines) take them off for those.
