# Q qualification procedures (H14)

**Frozen on 2026-10-20.** The SHA-256 of this file is recorded in `q-procedures.sha256` next to it and in
[ticket 60](../../../planning/open-source-successor/implementation/issues/60-hooke-h14.md). It is frozen separately
from the [study design](h14-study-design.md), so platform refinements after H11 never change the study's hash.
A change is allowed only as a dated amendment in §8, before the first qualification run of the procedure it
changes, and only one that adds or tightens a check.

These are the procedures for the Q groups of [acceptance.md](../acceptance.md) §2 and §6. The usability procedure
(Q.usable-workflow) is the study design itself, with its accessibility walkthrough in the study's §9. Nothing here
has been run, and nothing is qualified.

## 1. Common rules

Every run produces an **evidence record** (§6), and nothing is qualified by the implementation owner. Configured,
Operational and Usable are recorded separately (acceptance.md §2). Each per-OS child needs its own target. A record
for one target, mode, device or artifact never qualifies another.

## 2. Q.clean-preparation

Two independently reset instances of each advertised target (H11's machine list). Operator: the independent
qualification reviewer, or someone they supervise.

Checklist:
1. **Reset and inventory** (step 1). Restore the instance from its frozen image, or erase and install the OS from
   vendor media. Record:
   - macOS: `sw_vers`, `system_profiler SPHardwareDataType SPDisplaysDataType`, `spctl --status --verbose`,
     `xcode-select -p` (must fail), absence of `/opt/homebrew`, `/usr/local/bin/brew`, `~/.cache/uv`, the Hooke
     support folders, and Python other than the system stub; `pkgutil --pkgs` hashed.
   - Linux: `/etc/os-release`, `uname -a`, `lspci -k`, `glxinfo -B` and `vulkaninfo --summary` (from a vendor
     live medium if not installed), `nvidia-smi` or the vendor equivalent, the hashed `dpkg -l` or `rpm -qa` list,
     absence of compilers, Python dev headers, conda, uv and Hooke folders.
   - Windows: `winver`, `systeminfo`, `Get-ComputerInfo`, `dxdiag /t`, hashed `Get-AppxPackage` and installed
     programs, absence of Python, Visual Studio Build Tools, WSL distributions, conda, uv and Hooke folders;
     Defender and SmartScreen state.
   - All: the image ID or hash, the reset method and time, the GPU and driver, and the display scaling.
2. **Install** (step 2). Obtain the exact artifact the way a researcher would (browser download, so quarantine or
   Mark-of-the-Web applies) and check its hash. Install through the ordinary installer, without a terminal. Record
   every OS prompt.
3. **Prepare** (step 3). Through public controls only: discover the package, review its permissions, prepare it, and
   run its operation check. All traffic goes through a logging forward proxy, recording CONNECT host, port, bytes
   and time. Compare the hosts and artifact hashes against the package's declared downloads. Any undeclared host or
   artifact fails.
4. **Second instance** (step 4). Repeat steps 1–3 on the second reset instance, without copying anything from the
   first.
5. **Network denied** (step 5). Remove the network (disable the adapter, pull the cable, or detach the VM's NIC),
   restart the app, and run the target's local cases: open, view, edit, run the prepared operation, save, quit and
   reopen. Any attempted connection is logged and must not be needed.
6. **Failures.** Interrupt one preparation (network cut mid-download) and one install (low disk). Check the reason
   given, that nothing else broke, and that a corrected retry succeeds. Keep the first failure's evidence.

Zero terminal repairs, hidden caches, undeclared downloads or skipped interactions are allowed.

Evidence: the inventory JSON (hashed), the download receipt and hash, the installer prompts (screenshots), the proxy
log, the preparation and operation receipts, the network-denied run receipt, the failure and retry receipts, the screen
recording, and the evidence record.

## 3. Q.operation-proof

For each package mode, device and model that is advertised:
1. Run the actual pinned upstream implementation through Hooke's prepared environment, from the public UI, on the
   rights-cleared fixture and its synthetic supplement (step 1).
2. Run the same upstream code outside Hooke (its own pinned environment, its own documented API) on the same inputs
   and device, and compare outputs numerically and structurally within the case's frozen tolerance: exact for
   integer labels when the pipeline is deterministic, otherwise the H12 tolerance (step 2).
3. Exercise the selected backend's boundaries: each declared device (CPU, MPS, CUDA, ROCm, DirectML where declared),
   the declared dtypes and dimensionalities, and the declared minimum and maximum sizes. Out-of-range inputs are
   refused in words (step 3).
4. Try a mode, device or model that has no record, by asking for it through the UI and the API. The refusal must name
   the missing qualification, with no silent substitution and no inheritance from a sibling mode (step 4).

Evidence: the run record (operation ID, bundle, environment and model hashes, device and driver, parameters, input
hash, output hash), the upstream reference run and its environment lock, the comparison report, the boundary and
refusal receipts, and the evidence record. Biological suitability is a separate statement and is never implied.

## 4. Q.catalog-admission

1. An independent contributor submits each package record through the public contribution path. No maintainer edits
   it in the repository (step 1).
2. Verify the binding: the record's evidence names the exact package artifact hash, environment lock, model hashes,
   target, app version, and the Q records it rests on. Each signature verifies against the signer's key, and that
   key is in the signer registry, which only Will can add to (§10). The contributor's own self-test is recorded but
   never admits (step 2).
3. Replay: present the same evidence for a different artifact hash, a different target, a different model, a newer
   app version without an applicability analysis, and an expired or revoked signer. Each must be refused, with the
   entry kept as a private candidate listing its concrete missing requirements (step 3).
4. Public discovery shows admitted entries only. An unadmitted entry is visible to its owner as a candidate.

Evidence: the submission record, the signature verification log, the replay refusal receipts, discovery screenshots
for an admitted and an unadmitted entry, and the evidence record.

## 5. Q.failure-isolation

For each operation family (reader, segmentation, measurement, animation and export, Python kernel, plugin panel,
update), inject each applicable failure from acceptance.md §3: malformed declaration, message or output; dependency
conflict; unsupported input; denied file or network access; missing artifact; low disk or quota; interrupted write;
hang; crash; cancellation; reconnect; stale revision; failed upgrade or migration; revoked authorization; incomplete
cleanup.

Per injection checklist:
1. Before: snapshot the process tree, the store hashes, active jobs, the camera or acquisition state (the simulated
   source), and the active generations.
2. Inject through the public fault controls, or the environment's failure fixture. Never by editing app code.
3. Check that the failure is reported in words naming the cause, in the UI and the log.
4. After: unrelated jobs are still running and acquisition continues; saved work is byte-identical; generations are
   unchanged; owned workers, reservations, callbacks and temporary grants are gone (process tree, reservation
   ledger).
5. Correct the cause, retry through the UI, and check the result against the oracle.
6. Keep the first failure's evidence unchanged.

Evidence: the injection receipt, the before and after snapshots and their diff, the messages, the retry receipt, and
the evidence record, per injection.

## 6. Evidence record and authentication (the import the runner lacks)

`make scientific-acceptance` reports these children as `external` because no authenticated import exists. Each Q run
produces one record:

```json
{
  "format": "hooke-qualification-evidence/1",
  "case": "<atomic child id>",
  "group": "<group id>",
  "aspect": "configured | operational | usable | platform-macos | platform-windows | platform-linux",
  "artifact": {"app": "<sha256>", "sdk": "<version>", "bundle": "<sha256>", "environment_lock": "<sha256>",
               "model": "<sha256 or null>"},
  "target": {"os": "", "build": "", "arch": "", "hardware": "", "gpu": "", "driver": "", "image": "<sha256>",
             "reset": "<method and time>", "instance": "1 | 2"},
  "fixtures": {"<name>": "<sha256>"},
  "procedure": "<this file's sha256>#<section>",
  "started": "", "ended": "",
  "result": "pass | fail | inconclusive",
  "interventions": [], "retries": [], "limitations": "",
  "files": {"<relative path>": "<sha256>"},
  "operator": "<name>", "reviewer": "<name>",
  "signatures": [{"signer": "<registry id>", "sig": "<ssh-keygen -Y sign, namespace hooke-qualification>"}]
}
```

Signing uses `ssh-keygen -Y sign -n hooke-qualification` over the canonical JSON (sorted keys, no whitespace,
`signatures` removed). The signer registry is an `allowed_signers` file at `contract/qualification-signers`, changed
only by a PR Will approves. The runner's future import must check: the signature, that the signer is in the registry
and not revoked, that the procedure hash matches a frozen procedure, that the artifact hash matches the artifact under
qualification, and that every file hash is present. Implementing that import is H14 code work after this design; it
is not part of this freeze.

## 7. External gates (needs Will)

- **needs Will: provide reset targets for §2** per H11's machine list
  ([H11 feasibility §7](../../../research/2026-10-20-h11-linux-windows-feasibility.md#7-machines-and-identities-will-must-provide)).
- **needs Will: name the independent qualification reviewer** and add their SSH public key to
  `contract/qualification-signers` by PR (§6). The same person may serve the study.
- **needs Will: an independent contributor** for §4 step 1.

## 8. Amendments

Allowed, each dated, with its reason and the new SHA-256: a per-OS command or check added once H11 settles a
target's tools (for example the confinement self-test of each OS); a stricter check; a new failure injection.
Never allowed: removing or weakening a check, or any change to a procedure after its first qualification run
(a later change makes a new version, and earlier runs stay bound to the version they ran).

(No amendments yet.)
