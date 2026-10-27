---
name: add-a-capability
description: Add a capability to ObservationUI (a Hugging Face or other model, a Python algorithm, a Fiji/ImageJ function), or make your own version of one, as one catalog folder that every actor (the user's Actions panel and Try, the agent, researchers) gets with no app code. Use when asked to add, wrap, port, contribute or adapt a model, algorithm, plugin or ImageJ command, or to make an existing capability do something different.
---

# Add a capability

A capability is **one folder** that declares what it takes and gives, the way a ComfyUI node does. The app builds
everything else from that declaration: the form in Actions, Try on its card, the agent's tool, a researcher's `run`, how
its result is shown and kept. You write the folder; you never edit the app. If you find yourself editing
`services/` or `apps/` to make a capability work, stop: that is a gap in the door, to report, not to patch around.

The contract is catalog format 2 (ADR 0024, `docs/adr/0024-one-capability-contract.md`; every field, traced to ComfyUI,
napari, bioimage.io, SciJava and Galaxy: `docs/design/2026-10-15-one-way-in.md`). Worked examples, each proven:
`catalog/models/spot-measure` (an algorithm), `catalog/models/swin2sr-enlarge` (a Hugging Face model),
`catalog/models/fiji-gaussian-blur` and `catalog/models/fiji-subtract-background` (Fiji functions; the second added by an
agent from this skill alone), `catalog/models/spot-shape` (a derived version).

## 1. Choose

- **Its job:** one thing it does to the view, in at most 12 words (`what`). Its `category` puts it in an Actions group,
  by the group's title: Outline, Find, Measure, Look closer, Clearer picture, Snapshot, Motion, Over time, The slide,
  Name, How it looks. Any other category (`Filters/Denoise`) is listed by what it gives. capability-test prints the
  group's id (`seeing` is Clearer picture, `measuring` is Measure).
- **A new job, or a new way to do one of the app's:** `operation` defaults to its own id (a new job). Set
  `operation = "cells"` to make it a way of finding every cell: it is then also a choice for the app's cell finding, and
  still gets its own row and form (`catalog/models/instanseg-brightfield`, `catalog/models/basic-bright-objects`).
- **What it takes:** the view, `image.2d.rgb8` (a uint8 RGB picture). `clip.2d.rgb8` (the last seconds) only if it gives
  `tracks.graph`. Axes beyond one plane, human steps and saved methods are reserved: the app refuses them for now.
- **Its parameters:** each a `[[inputs]]` entry of type `int`, `number`, `string`, `bool` or `choice`, with a `default`
  (or `optional = true`), its `min`/`max`/`step` or `options`, `unit`, `label` and a one-line `tooltip`. These become
  the form, the agent's arguments (`describe_capability` tells the agent each one's range and meaning) and a
  researcher's. Only what a person would actually change. `agent = false` makes one the user's alone: its form and Try
  set it, the agent and researchers can't (a cost, a privacy or a safety choice).
- **What it gives:** one `[[outputs]]` (or several, returned as a dict by output id). `image.2d.rgb8`, `table.objects`
  (columns with `unit` and `definition`), `text`, `points.2d`, `labels.instance.2d`, `tracks.graph` are shown and kept
  in their own places; **any other lowercase type id is fine**: it is kept and handed on as data.
- **How it runs (`runner`):**
  - `python-env`: your own Python, or a model's (Hugging Face, GitHub): an `adapter.py` in its own pinned environment.
  - `fiji`: an ImageJ command, run headless in the app's Fiji (`[fiji] command` and `options` as the macro recorder
    writes them, each `{param}` a parameter). Contributed Fiji folders run in fresh, confined JVMs on a pinned
    managed Fiji generation. They cannot install plugins, read unrelated files or use the network. A missing command
    needs the Toolbox's staged Fiji setup; a GUI-only plugin is unsupported.

## 2. Check, then scaffold

```bash
make capability-preflight FROM=<a link, or a folder>   # a model's link: which route it takes; a folder: what stands in the way
make capability-new ID=<id> RUNNER=python-env OUTPUT=table.objects INTO=catalog   # INTO=mine to try your own locally
make capability-new ID=<id> RUNNER=fiji OUTPUT=image.2d.rgb8 INTO=catalog          # an ImageJ command
```

**A GitHub link first:** paste it in the app (Add tools, Add a model or tool) or run the preflight on it. When the
repository's files or one of its releases' zips hold a bioimage.io model description (`rdf.yaml` or `bioimageio.yaml`)
the reviewed `bioimageio` template runs, it becomes an entry with no folder to write: every file pinned (the commit, the
zip's sha256, a Git LFS object by its hash). Otherwise the app says what the repository holds (a Python package, a
napari plugin, notebooks, weights with no description) and what it lacks; that is your brief for the folder below. A model
whose output names don't say what its values are needs a reviewed binding in
`catalog/templates/bioimageio/output-semantics.json` (its exact rdf.yaml and weights hashes and its makers' evidence),
never a guess from its channel count.

**Or have the app's AI build it** (no folder to write; the user's own route for a package no template runs): in Add a
model or tool, paste the link, then Have AI build the integration here; describe the job, choose a PNG or JPEG test
picture and press Build with AI (at most $2 and 15 minutes of the research model; each call is counted under Spending). A
researcher reads the source, pins what it needs from public https addresses (the makers' source at a commit included),
writes `adapter.py` with `segment(picture, params)` (the builder's name for `run`) and its requirements; the app locks them
as wheels and tries each draft inside macOS's sandbox on that picture, keeping every failed try and its reason. Then
Compare on my picture beside the current default, read its **review** (exactly the code, locked packages, pinned files
with their origins and hashes, what it imports, every try, and the static checks this preflight runs), and **Use it**
admits exactly what was read as one of yours: installed and proven inside the sandbox, shared with nobody. Nothing
half-made is kept when Use it fails; it says why.

Fill in every TODO (the preflight refuses a folder that still says TODO), then `make capability-preflight FROM=<id>`.

**A model or Python code (python-env):** for a model, `source = { hf, revision }` pinned to a full commit and
`needs_to_get = ["download"]`, then `make catalog-pin ID=<id> ONLY="config.json,model.safetensors,..."` writes its
`files` with their sizes and hashes. List its packages in `requirements.in`, then `make capability-lock ID=<id>` (wheels
only, every package by its hash, for this Mac). Then write the adapter (step 3).

**An ImageJ command (fiji):** no adapter, files or lock. Record the command once in Fiji's macro recorder (Plugins >
Macros > Record) and copy it: `run("Subtract Background...", "rolling=50 light")` is `command = "Subtract Background..."`
and `options = "rolling={radius} {light}"`. How each parameter is written:
- a number: its value (`sigma={sigma}` gives `sigma=2`);
- a choice: its value, bracketed when it has a space (`method={method}` gives `method=[Max Entropy]`);
- a yes/no: its **id** when yes and nothing when no, so the parameter's id must be the recorder's keyword (`light`,
  `dark`, `exclude`).
Its proof runs the folder's command beside the same command as an ImageJ macro, at its defaults, at each number's `min`
and `max`, each choice and each yes/no flipped (wide ranges are fine), and fails when any parameter changes nothing on
every picture: that is an option not reaching the command (a keyword the recorder doesn't use is silently ignored).

## 3. Write the adapter (python-env)

`run(picture, params)`: `picture` is the view (uint8 RGB `(height, width, 3)`; a clip is `(frames, h, w, 3)`); `params`
holds its parameters' values (checked, defaults filled) and any `hidden` values it asked for (`pixel_size_um`,
`objective`, `times`). Return its output; `{"unsupported": "why"}` to refuse a picture it can't take; an empty table,
no points or no labels when it finds nothing (that is a valid result, said as "found none"). Load a model once (a
module-level cache) from `os.environ["MICROMIND_ENTRY_FILES"]`, with `local_files_only=True` and safetensors.

<!-- rules -->
Rules every capability keeps (the in-app builder is held to the same):
- Pin everything: weights as `files` with their sha256, packages in a hashed `env.lock`. Nothing is fetched or installed at run time (no pip, no requests, no torch.hub, no `from_pretrained` without `local_files_only=True`).
- Never invent preprocessing: use the model the way its makers' own example does.
- Run the makers' own code: install their published wheel, or pin their source file at a commit (by its sha256) and call their function from it; never rewrite their algorithm from memory. A plugin's GUI decorators may be left out; its computation may not.
- Refuse what it doesn't take, in words (`{"unsupported": "why"}`); never guess on it.
- Return an empty result only for a valid input that holds nothing; a failure raises.
- No CUDA: run on the CPU, or Apple's mps with a CPU fallback.
- Credit its makers (`credit`) and declare its licence (`component`); a derived version keeps `derived_from`.
- Its outputs are its makers', never the camera's view: a picture it makes is labelled as made by it.
- Fiji contributions keep a declared Groovy script, if needed, and small single-plane 8-bit examples beside the manifest. Never include a JVM, JAR, native library or private data. Its result must match its declared ImageJ reference command.
- For a format-3 package bundle, one owner declares the Python environment and each `[[members]]` entry declares its own operation, adapter, ports and `reference_cases`. All members share the owner's immutable generation and switch together. Keep scientific projects outside the package. Bundle execution currently supports CPU-only single-plane RGB8 operations with one image, table, text or points output. Exact tested parameter configurations are required; no child processes, GPU, arbitrary Qt widgets or automatic package discovery is implied.
- `scientific-host-v1` declares an interactive integration with one required `project.scientific` input and output. Preflight and workspace packaging accept it as an unverified review candidate, not an enabled operation. It cannot inherit RGB operation proof and does not need fake RGB reference cases to travel. Import its package through Models, prepare the declared runtime, then review it on the intended input through the ordinary scientific workspace. Packaging, preparation and control-adapter tests do not qualify its interactions or results. Preserve the host's existing permission, proof, activation and qualification gates; report exactly which workflow has actually been verified.
- A private `scientific-host-v1` reader can declare `reader = { format = 1, input = "source", extensions = [".npy"], maximum_bytes = 67108864 }` on its member. The factory receives that exact accepted file as `context.files["source"]`, a read-only seekable handle with `name`, `size`, `sha256` and `reference`; no original host path or extra filesystem/network access is granted. Read in bounded ranges (at most 1 MiB each and 64 MiB total per private session), retain native values and create layers in `context.viewer`. Never execute pickle/object-array payloads from scientific data. Suffix matching only offers a private candidate; validate the actual file structure and refuse unsupported inputs before presenting a result. Source → Find readers → Review with… saves an exact reader generation and input revision; Start then uses the existing confined scientific host. This does not establish operation proof or clean-machine qualification and cannot make the reader green. Large files, remote sources and general automatic plugin adaptation remain separate unfinished interfaces. A directory reader instead declares `format = 2, kind = "directory"` with the same input/suffix/byte fields. Its `context.files["source"]` is an immutable named mapping: iterate relative entry names, `source[name]` reads at most 8 MiB, and `source.open(name)` returns an independent seekable stream with `name` and `size`. Streams read at most 1 MiB per request under the shared 64 MiB/4,096-request session allowance. Folder intake is atomic and limited to 64 MiB, 4,096 entries and 1 MiB metadata. No host path, extraction or write authority is granted. This interface has local component evidence only; it is not a frozen or independently qualified SDK.
- A private scientific-view factory may declare `context.bind_view(layer=layer, key="raw-image")` for a stable integration-owned layer identity. Keys are unique, begin with a letter and contain at most 64 letters, digits, underscores or hyphens. Bind only while creating the factory and only to layers in its viewer. Names and order are presentation, never identity. Removing a layer revokes its binding; re-adding an object cannot restore it. This declaration grants no edit permission and does not itself save a view. Temporary/unbound layers cannot be silently matched when restoring presentation. Remember stores applied camera, axes and supported appearance in the window’s arrangement, separately from scientific data. Its compatibility signature includes the complete data-to-world affine, axis labels and units. Changed geometry must be saved by an explicit scientific edit integration; presentation restoration refuses a mismatch rather than replaying transforms or guessing.
- A private scientific-view factory may declare `context.bind_editable(layer=layer, input="labels")` for one Image or Labels layer and one exact named native input. Bind only during factory creation. This declaration preserves source meaning; it cannot declare a conversion. Shape, dtype, axes and calibration must stay unchanged. The initial staging path accepts at most 64 MiB of worker-owned NumPy data and refuses background Python threads or external mutable buffers. Unknown semantics, new layers, lazy data and changed geometry need their own integration. Workers may export bounded chunks but cannot publish a Project revision. The workspace offers an explicit Save array changes action only for a bound layer. It creates a new accepted revision; the originating worker keeps its original input. Save-only retry publishes the same staged values without rerunning the tool. Open the accepted revision for further edits. Other layers, view settings and Python variables are not saved by this action. Bound Labels also expose single-plane 2D paint, erase, fill and pick with native undo/redo. Each complete gesture is admitted against its rendered frame; failures restore values and history. The worker shares a bounded history budget across layers, and uncertain replies are checked by their original edit identity rather than replayed. Python changes invalidate existing manual history until the user explicitly resets it. These controls do not supply polygon, 3D or lazy-data editing. This bounded path does not qualify a complete editor.
- A scientific RGB member may opt into `bridge = "regions-v1"`. Its adapter becomes `function(resources, params)` and returns the same `{value, scientific}` envelope. `resources[port]` exposes `shape`, `dtype`, detached `descriptor` and `read(((start, stop), ...))`. Read only explicit unit-stride regions, at most 1 MiB each, 1,024 requests and 64 MiB per invocation. The host supplies no source paths or network access. This bridge still takes one bounded RGB8 input; it does not establish volume, remote, native-label or large-data support. Algorithm-specific tiling, normalization or conversion must be declared and checked, never inferred from the read interface.
- Each bundle reference case names a public `example*.png` input, `reference*.png` or `reference*.json` output, its actual reference `source`, `device = "cpu"`, normalized `params` and explicit `tolerance`. Include defaults. Images/points need numeric `atol` and `rtol`; tables also name `identity_columns` and `numeric_columns`. Declaring a source is not rights clearance or proof of scientific accuracy. Copy the upstream reference faithfully and record its version.
- A bundle may opt into scientific metadata with `scientific = { inputs = { image = { exact = { semantic = "intensity" } } }, outputs = { result = { exact = { semantic = "intensity" } } } }`, using its actual port IDs. Contracts accept `exact`, `known` and `same_as = [{ peer = "image", fields = ["shape"] }]`. Unknown metadata never satisfies a requirement. Do not infer roles or calibration from RGB shape.
- Scientific adapters use `run(picture, params, resources)` with descriptors keyed by input port, and return exactly `{ "value": output, "scientific": descriptor }` or the existing unsupported reply. Describe the output actually produced. The host checks RGB8 shape/dtype and keeps producer claims separate from measured fields. No grayscale expansion, alpha removal or implicit crop is supported on this path. Reference cases provide `scientific = { input = { ... }, reference = { ... } }`; both undergo the same checks. Declare conversions and loss explicitly; never copy invalid calibration or use a declaration to fill missing runtime facts.
- New installs must pass the offline installed-requirement check before their receipt is published. Repair missing or incompatible requirements in the pinned lock; do not bypass the check or install during execution. This does not prove optional extras, model artifacts, devices or scientific behavior.
- Private scientific-host tools may declare `context.bind_document(schema=..., version=..., capture=..., restore=...)` alongside their controls. Capture returns bounded data-only JSON, never arrays, pickles or live Python objects. Bind every persisted layer with `context.bind_view`; Save tool captures all declared layers and state atomically. In a restoring factory, `context.restored_layers` maps those stable keys to accepted native layers: attach controls to them without rerunning the producing algorithm. Restore runs once in a fresh confined worker and must reproduce the exact declared state without changing scientific values. A failed restore preserves the saved original. The current path is bounded to the existing native snapshot limits, refuses undeclared file dependencies and does not preserve kernel variables or automatically serialize arbitrary Qt state. It is a private integration interface, not clean-machine or complete plugin qualification; see `docs/design/2026-10-16-private-scientific-view.md`.
- Explicit **Continue with another tool** records the exact accepted scene revision and prepared runtime before Start. On a retained scene, version-1 initialization passes `context.restored_layers` to the newly selected factory; attach controls without replacing or mutating the retained scientific layers. Earlier tool codecs stay in their original revisions; selecting the exact same runtime preserves its codec. Ordinary output reopen still skips its producing factory. This is bounded interactive continuation, not arbitrary graph execution or compatibility qualification.
- Scientific output view-binding interface v1 adds `context.bind_view_output(version=1, layer=layer, key="result-1")` for a newly created layer after factory completion. Feature-check the callable to refuse older hosts. Invoke only on the owning GUI thread. It preserves lifetime-unique keys, removal revocation and the64-identity bound; it grants no edit or publication authority. Keep factory-only `bind_view` for initial layers. Declare data-only tool settings separately and identify any transient upstream state that cannot reopen; never imply that a layer save preserves such state.
<!-- /rules -->

## 4. Prove it

Add a test: a small picture beside the manifest named `example-*.png` (it travels when the capability is shared; any
picture you may share, copied from another example is fine) and `tests = [{ input = "example-x.png", params = { ... },
outcome = "result" }]` (or `"empty"`, `"unsupported"`; parameters left out are their defaults; `count = 271` when you know
exactly how many it gives there, from an independent reference). Install's proof runs these tests too: a test that
doesn't give what it declares fails the install. Then:

```bash
make capability-test ID=<id>
```

It installs it in a catalog of its own (never the live app's), proves it as the app does (a Fiji function: identical to
Fiji's own macro at its defaults and each parameter's range), runs its tests through its real code, and runs it through
the agent, its Actions row, Try and a researcher. Fix until every line passes. Then `make check`.

## Package bundles (catalog format 3)

Use a bundle when several operations need the same pinned runtime. Keep `requires = { catalog = 3 }`, the owner's
`id`, `name`, `version`, `runner = "python-env"`, `env`, `component`, `licence`, and `needs_to_run = ["install:<owner-id>"]`
at the manifest root. Root-level `adapter`, `implements`, `inputs` and `outputs` belong to individual capabilities,
so omit them on a bundle. Each `[[members]]` needs a unique `id`, `name`, `adapter = "module:function"`, `inputs` and
`outputs`; `operation` defaults to its member id. Member keys are exactly `<owner>#<member>`.

Put reference cases under `[[members.reference_cases]]`. Sharing includes declared adapter modules, package
initializers, the hashed lock, declared examples/references and attribution. Undeclared utility modules do not travel;
keep helpers in a declared module or an installed pinned package. Do not place environments or private data in the folder.
Run the same preflight, lock, capability-test and package commands above, using the private workspace when provided.
For operation bundles, the test command proves the owner once and exercises each member through the actor doors.
Every reference must pass before an operation becomes available. Scientific-host members instead travel as
unverified review candidates, without invented RGB reference cases or a successful RGB proof. Their receiving
profile must prepare and review the declared scientific path; a bundle mixing member types retains each member's
own checks. Updating or deriving creates a candidate; proof alone does not switch an active version. Import the
resulting format-3 share package through the guided sheet; older clients must update to read it.


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

The existing workspace preflight/package and shared-file import paths preserve
these declarations. Older clients refuse format 4. Import grants no trust,
activation or qualification; installation, confined checks and explicit selection
remain separate. Private scientific-host candidates still need their workspace
and qualification. Pictures retain their smaller embedded-package limit; larger
recipes require their separate share file. This mechanism does not establish a
complete animation workflow or clean-machine qualification.

## 5. Your own version of one

```bash
make capability-derive FROM=<id> ID=<your-id> INTO=mine   # derived_from names the one it came from; the original is untouched
```

Change what it does (a measurement, a rule, a parameter), add a test for the change, then `make capability-test`.

## 6. Share it

- **With one person:** its card's Share it (a small file; weights never travel), or a channel (`make channel-add`). Python uses share format 1. Fiji uses format 2 after a confined functional proof; recipients need this format and a supported OS sandbox, and must allow and prove the contribution locally.
- **With everyone:** a pull request with your folder under `catalog/models/<id>` (`make catalog-pr ID=<id>`), signed off
  (DCO, `docs/contributing/DCO.txt`). CI runs `make catalog-ci`.

Say what you checked and what you didn't: `capability-test` runs the agent as a scripted call; whether a live model picks
it, and a person's run in the Studio, are the live test's.

## Private workspace handoff

When this skill is supplied inside a private capability workspace, use the commands in **Copy instructions**.
They pass `--workspace <folder>` to the same preflight, new, derive, lock, test and package tools.
The workspace records the requested job, supported input/output and fresh candidate ID; it is outside catalog discovery.
Keep all edits inside its candidate folder. Do not edit the host app or publish into its live catalog.

Use `new` for a new tool or `derive` for an existing capability. Derivation copies only capability-owned files,
preserves lineage and excludes installed revisions, activation state and caches. Lock Python dependencies as wheels
with hashes, then run the supplied `capability_test --workspace` command for operation capabilities. Its proof runs in a separate check catalog.
For Fiji, the check uses the confined JVM boundary and the recipient must support it too.

A scaffold is unfinished. Supply real behavior, a permitted example and checks for result, empty and unsupported
cases. For an operation, package only after those checks pass. A declared scientific-host integration can be
packaged for review after preflight passes, with its unverified status and remaining scientific checks explicit;
the RGB operation proof does not test interactive tools. The package includes neither runtime nor activation receipts. Return
the `.observationui.zip` through **Import completed capability** in the guided sheet. The app creates a new
candidate and requires permission and its bridge-specific checks; an active version stays intact. Scientific-host
candidates return through Models preparation and the ordinary scientific workspace review, with no automatic
activation or claim of qualified results. Operation capabilities require local proof and comparison before enabling.

The guided import currently supports 2D pictures with measurements tables, pictures, object outlines or written
results. Fiji supports tables and pictures. Point-only results cannot yet complete this route's comparison; explain
that limitation before creating one. The authoring service also accepts an explicit Python
`project.scientific` → `project.scientific` request when its source is an existing declared scientific-host
bundle. Derive that bundle with its fresh workspace identity and recorded lineage; retain its hashed lock and
declared wheels unless changing dependencies. This route packages a review candidate through the same command,
without an RGB scaffold or RGB operation proof. It does not add arbitrary scientific contracts to the guided
creation form or promise automatic adaptation of GUI plugins or volume settings.

Both `.agents/skills/add-a-capability/SKILL.md` and `.claude/skills/add-a-capability/SKILL.md` in the workspace must
match the supplied skill. Missing or changed instructions are an error: recreate the handoff rather than guessing.


## Private source preparation

If a pinned upstream revision is available only as source, do not relax the runtime installer's wheel-only rule.
The contributor preparation command is `make capability-build-source FROM=/absolute/source.zip SHA256=<digest>
SUBDIR=<source root> BUILDER=/absolute/pinned-builder-folder INTO=/absolute/new-private-attempt` (one shell line).
The builder is an existing manifest folder with its Python version, complete hashed wheel lock and imports.
Preparation uses a separate private catalog and confined offline build hooks; missing build dependencies must be
added to that lock. The current bridge refuses builds needing child processes, external compilers or network access.

Read `docs/design/2026-10-16-confined-source-preparation.md` for bounds and evidence. The result is a private wheel
and build receipt, not a runnable capability. Pin the source and wheel hashes in the eventual runtime recipe;
complete artifact delivery, permissions/rights, operation proof and workspace interaction checks are still required.
Do not publish a machine-local wheel path as a portable dependency, or label the source build as Ready here.

New managed installs check installed package requirements offline before publishing their installation receipt.
If a dependency is missing or incompatible, correct the complete pinned lock and prepare a new candidate.
The checker does not install repairs. It does not prove optional extras, devices, model artifacts or scientific
behavior; retain the corresponding operation and interaction checks. Existing active generations stay intact.

- A private saved tool may declare `context.bind_sequence(version=1, name="stable-name", count=..., select=...)` during its factory. Count returns 1–4096 frames; select receives each zero-based frame index exactly once, on the owning GUI thread. Count must remain unchanged during export. Integrations own interpolation and restore their tool state before selection. Hooke opens a separate exact-generation host from an accepted tool/layer revision, renders bounded RGBA frames and owns encoding, cancellation and atomic publication; callbacks receive no output path, executable or publication authority. Current `mov-qtrle-v1` supports bounded lossless rendered movies, not native array export or large-data qualification. Unsaved live state is never an implicit export input. Unsupported sequence versions refuse. See the animation and native-stack examples; these local authored consumers do not count as held-out integrations.

- Private scientific-host factories can read directly declared, prepared model files
  relative to `os.environ["MICROMIND_ENTRY_FILES"]`. Pin every file's path, byte size
  and SHA-256 (or Hugging Face git blob identity) in the owning manifest. The host
  verifies the exact prepared snapshot's published files with bounded reads and
  keeps their removal lease for the worker's lifetime. Only declared files are
  readable; directory enumeration, undeclared neighbours, developer caches and
  runtime downloads are not granted. `files_from` live aliases are unsupported
  here: declare immutable pins directly. Artifact availability does not raise
  the host's memory/process allowance or establish actual-model qualification.

- A scientific-host member may declare `resources = { format = 1, memory_mb = 4096 }`.
  Version1 admits integer1024–8192MiB, defaults to1024MiB when absent, and changes
  retained runtime identity when edited. Setup exposes the requested maximum;
  Start reserves available memory before launching and supervises that maximum.
  This does not grant additional storage, processes or network access. Old member
  readers reject the new field; unknown versions refuse. Choose and freeze the
  package/dataset budget before an experiment; a declaration is not footprint
  evidence, a large-data result, or independent qualification.

For explicit replayable settings, feature-check and declare
`context.bind_recipe(schema=..., version=..., capture=..., configure=...)` beside
`bind_document`. Its bounded JSON settings are saved with the tool; configure runs
once on a separately prepared retained-input scene and must round-trip exactly
without running analysis or changing scientific arrays. Use the ordinary operation
controls for execution. Recipe runs pause for explicit result review and never
replay manual corrections or execute on document reopen. See the private scientific
view contract for versioning and current beta limits; this is not independent SDK
qualification.
