# Independent repository check repairs: 2026-10-13

Behavior fixes: `5358c502` and `58a4163c`. The affected regression run included
integration through `ccbbdc0f`; the final compatibility check includes catalog
adapter proof/publishing integration `0755eb41`, merged as `2e06dee2`.

Evidence class: generated inputs and scripted models through the production
App, Brain, tools, execution runner and filesystem persistence, with stand-in
workers and isolated temporary app/asset/model stores. The tests reused an
installed Python 3.13.5 dependency environment on macOS arm64. They did not use
owner recordings, real provider credentials, the account Keychain or model
weights; no model download or owner service was started. This is not a fresh
dependency installation, real model qualification or application performance
certification.

The failures identified four separate assumptions and one redundant computation:

- Checker wiring tests depended on the owner's named leaf frame. They now pass
  generated JPEGs through the existing explicit input parameter. A production
  check whose named input is missing or empty returns **not checked** with the
  input name; a model that actually fails still returns **broken**. The real
  benchmark still requires its named real frame and has no synthetic fallback.
- Tests with stand-in SAM workers did not declare their synthetic installation
  state. Those fixtures now declare it; production installation, download,
  permission and failure refusals still apply and are tested separately.
- A vector-save assertion depended on winning a race against the background
  writer. The test now holds the atomic manifest replacement, observes the
  reservation and unpublished manifest, releases the write, and checks released
  memory and restored vectors through the existing persistence interfaces.
- The three-request snapshot journey scored the words “snapshot of.” If the
  earlier zoom had not completed when the snapshot request arrived, the correct
  capture was the whole view at that request. The score now requires that named
  request's successful snapshot receipt, retained PNG, frame/session facts and
  matching displayed capture. An earlier capture, mismatched receipt or deleted
  PNG fails; changing just the caption does not. Other journey assertions,
  no-done-before-effect checks and all timing limits are unchanged.
- One capabilities response recomputed readiness facts for options and model
  cards. The existing readiness batch now spans the entire synchronous response;
  the scripted World follows the same boundary. Location evidence is shared
  within that response and discarded afterward. Regression checks verify that
  the next response observes changed files, keys, Fiji proof/generation and
  recorded model failures. No cache survives a response or authorizes execution.

| Verification | Result | Measured duration |
| --- | --- | --- |
| Original narrow Checker/execution reproduction | 9 failed, 5 passed | 3.43 s |
| Repaired Checker and execution checks | 16 passed | 3.25 s |
| Deterministic vector reservation/save check | 1 passed | 0.92 s |
| Affected checks after the correction integration | 115 passed | 38.45 s |
| Complete scripted acting set and snapshot evidence regression | 107 passed | 145.80 s |
| Capability matrix, including unchanged 90 s budget | 219 passed, **1 failed: time budget** | 114.82 s pytest; 115.217 s whole command |
| Compatibility after catalog proof/publishing integration: capability response, readiness, golden wires, snapshot evidence | 23 passed | 11.28 s |

The affected checks covered `test_capability_response`, `test_model_checks`,
`test_execution_paths`, `test_outline_seeded`, `test_enrichment`, `test_readiness`,
`test_fiji_generations`, `test_envs` and `test_particle_corrections`. Acting and
matrix runs were separate commands from `services/`:

```sh
python -m pytest -q tests/test_acting.py tests/test_acting_snapshot_evidence.py
python -m pytest -q tests/test_capability_matrix.py
python -m pytest -q tests/test_capability_response.py tests/test_readiness.py tests/test_golden.py tests/test_acting_snapshot_evidence.py
```

The matrix's measured row sum was **114.034 s against its 90 s limit**. Its slowest
groups were refusals by voice (7.6 s), refusals by chat (5.0 s), count by voice
(3.0 s), lesson by voice (2.8 s) and setting the default model by voice (2.5 s).
The 14-CPU host's load averages were 38.24/36.71/46.21 before the command and
29.79/36.64/45.15 afterward. These observations do not prove that external load
fully explains the budget failure. The time gate remains open; no threshold,
row, actor or assertion was removed.

Diagnostic runs are retained separately from acceptance results. Increasing only
scripted vision latency from two to six case seconds reproduced the old snapshot
caption failure; the structured score passed with the same overlapping requests
and zero premature done claims. Profiling the close-magnifier case identified
seven capability responses taking 0.377 s in the earlier run and 0.158 s after
the bounded batching change and integration. Both profiled runs missed the case's
2 s deadline (3.8 s and 2.5 s respectively); profiler overhead and different host
conditions make those diagnostics unsuitable as performance acceptance. The
unprofiled full acting run above passed its existing deadlines.

Private raw evidence retains the initial aggregate tracebacks, narrow red/green
checks, timing failures and development failures. These include an intermediate
Checker exception-compatibility error, corrected by preserving its `OSError`
contract, and snapshot negative-fixture assumptions about repeated requests and
content-addressed picture URLs. The final negative case changes the source frame
so a prior unrelated capture is distinguishable.

The complete repository aggregate must run on the final integrated candidate.
Studio/browser journeys, clean dependency setup, camera behavior, real model
quality and release readiness remain separate gates.
