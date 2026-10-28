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
production app. Its file paths resolve against the local asset store. The store's own private
`manifest.json` (`assets/README.md`) is read over either one, so recordings listed there resolve without
`SIM_MANIFEST`. `SIM_VIDEO`
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
provider credentials/spending. Neither `make check` nor the contributor check
turns that option on. Never treat timing measured under unrelated heavy work as
performance qualification. The full gate and affected tests are described in
[the testing contract](../services/tests/AGENTS.md).
