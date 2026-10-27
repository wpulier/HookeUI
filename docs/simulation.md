# Simulated-camera integration checks

The harness starts the production camera bridge, orchestrator and local model
workers on separate ports. Only the camera source is replaced by a recording.
Each run has isolated saved state. It does not certify physical camera behavior.

```sh
make check-public
# With locally available, qualified footage and model prerequisites:
make check SIM_MANIFEST=/absolute/private/test-manifest.json
# Affected scenarios only, during edits:
make sim-test SIM_MANIFEST=/absolute/private/test-manifest.json SIM_ONLY=freeze,crash
```

The full gate needs the original qualified leaf-epidermis input and actual local
models, including the models selected by the research edition. The distributed
asset manifest is intentionally empty: personal footage and internal backups are
private. Obtain appropriate authorized test material separately. `SIM_MANIFEST` reads an
external manifest only in the harness; it never copies it into Git or changes the
production app. Its file paths resolve against the local asset store. `SIM_VIDEO`
can select another video for scenarios that support it, but labelled scenarios
still require the matching qualified manifest. An arbitrary or
synthetic clip cannot qualify specimen-specific assertions. The dataset and
model-load scenarios check their known frame/annotation basis before acting.

The default manifest identifier is resolved only when the harness runs. An absent
manifest entry, video or model is a missing prerequisite, never a passing result.
Use the generated-input contributor check without these prerequisites. Keep test
outputs, recordings and credentials out of Git. Results live in the ignored
`services/sim/results` directory; retain failures with source revision, input hash,
model configuration and measured timing scope.

Cloud scenarios require the explicit harness `--cloud` option and authorized
provider credentials/spending, including when selected with `--only`. Owned non-cloud
stacks remove provider environment keys and disable file/Keychain lookup and research
extensions in both child processes. `--attach` uses an externally managed stack;
it cannot isolate that stack's credentials or saved data. Use an owned stack for
the full gate. Neither `make check` nor the contributor check
turns that option on. Never treat timing measured under unrelated heavy work as
performance qualification. The full gate and affected tests are described in
[the testing contract](../services/tests/AGENTS.md).

Steady-state follow-click, cells-click and model-load baselines wait at most 120 seconds for the default
models to be resident and no longer loading before seeding outlines. Following
measurements also require a completed follower step after the final seed reply. Preparation time and the last
observed state are retained separately; a timeout fails the scenario. Admission
does not inspect latency or retry slow samples. Cold-model loads, concurrent load
phases and their original latency thresholds remain part of the gate.

Owned simulations also use a fresh catalog containing independent copies of the
bundled models' available published weights and verification/device cache records.
On macOS these are APFS clones. Source symlinks are refused; personal entries and
channel catalogs are excluded. Missing weights remain missing and the production
pinned legacy-cache resolver remains available. Prepared Python environments and
tools are reused at their installed paths, including ordinary interpreter/dependency
cache writes; this isolates saved scientific state, not the whole filesystem.
No new model installation or provider request is made by this preparation.
`test_sim_catalog.py` uses synthetic files to check these boundaries; it does not
qualify model performance.

The cells-click scenario admits the same loaded cells RPC after setup, retains its
first measured click, and still requires ten complete successful click intervals
inside that exact RPC. Default-model residency and a fresh follower step replace
setup races; no cell-finding run is added as a warm-up.
