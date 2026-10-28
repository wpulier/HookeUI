# Retained scientific matrix capabilities

The same catalog operations serve a person in Tools, a local researcher, and an authorized connected agent.
`WorkflowService` owns runs and attempts; `matrix_capability_runs` admits one exact operation and publishes its
immutable file result. This service does not schedule another job, replay work, or interpret a Study's claims.

## Interfaces and artifacts

Every input is a Project-owned document id, revision, digest and optional file component. The adapter verifies
ownership and content hashes before materializing bytes into a private worker directory. Arbitrary host paths,
foreign Projects, ambiguous components, changed generations and changed intent under the same request refuse.
Outputs use the existing chunked FileIntake and keep exact input references, capability generation, parameters,
runtime receipt identity and output meaning in protected `state.matrixOperation`. Matrix values never travel
through ordinary tables, chat context, or document JSON. Generic document saves cannot manufacture these receipts.

The capabilities are:

- `matrix-inspect`: read raw-count AnnData and report axes, count diagnostics and scientific meaning.
- `matrix-prepare`: aggregate counts by explicit biological context and target, relative to each context's controls.
- `aggregate-profile-prepare`: convert explicitly mapped, cell-weighted mean profiles using a retained scientific protocol.
- `response-effects-merge`: concatenate compatible prepared effects, refusing duplicate biological-context/target rows.
- `response-fit`: fit regularized target effects, optionally with a bounded low-rank approximation.
- `response-predict`: fit control-conditioned count moments and emit sparse whole-count predictions.
- `matrix-evaluate`: a deliberately limited mean-expression diagnostic, explicitly not a competition score.
- `matrix-evaluate-cell`: use pinned upstream cell-eval2 with a retained protocol and complete declared target panels.
- `response-combine`: combine fitted effects on identical ordered gene/target axes with explicit weights.
- `matrix-export`: validate and copy retained AnnData; packaging and upload remain the caller's separate operations.

The generic evaluator protocol is a retained JSON file with `preset`, `context_column`, `target_column`,
`control_label`, and `panels` (context label to complete unique target list). The preset belongs to the Project,
not this service. Missing/extra targets or contexts and reordered genes refuse. Each upstream call scores a whole
context; arbitrary target splitting would change discrimination. The CPU adapter supports raw-count presets using
real controls. It adds those controls only to a transient evaluation matrix because upstream group validation
requires them, without altering prediction artifacts. It records upstream raw metric rows and aggregates, resolved
configuration and CPU/scanpy backend. The upstream catalog identifies primary scored metrics separately from
diagnostic/dependency metrics. It does not invent reference anchors, an overall score, or organizer acceptance.
Nonfinite upstream measurements stay unavailable as JSON null rather than becoming zero.

The aggregate-profile protocol is a retained JSON file, not an executable transformation. Its version-1 fields are:

```json
{
  "format": "aggregate-profile-config-v1",
  "scientific_unit": "mean-raw-counts-per-filtered-cell",
  "context": "biological-context-one", "experiment": "screen-one",
  "row_id": "_index", "weight_column": "num_cells",
  "row_targets": {"construct-a": "target-a", "construct-b": "target-a"},
  "control": {"column": "core_control", "equals": true, "label": "non-targeting"},
  "gene_id": "_index", "gene_symbol": "gene_name",
  "selected_genes": ["gene-b", "gene-a"], "selected_targets": ["target-a"],
  "normalization_genes": ["gene-a", "gene-b", "gene-c"],
  "normalization_total": 10000, "pseudocount": 0.1,
  "duplicate_symbols": "refuse", "missing_policy": "refuse"
}
```

Column names refer to literal H5AD obs/var fields; a legacy named index is declared by that actual field name.
The Project supplies exact row-to-target mappings. No transcript-name parser, inferred cell identity or external
gene lookup is invoked. Each file declares one biological context and a separate experiment identity. The boolean
control selector cannot overlap mapped perturbed rows. Selected cell weights must be finite positive whole counts;
excluded source rows may lack weights and are retained as exclusions. Only selected rows and required gene blocks
are materialized. Legacy AnnData categorical symbol columns are supported.
Aggregate preparation supports dense and CSR sources with bounded explicit row reads;
CSC sources refuse until a separately qualified conversion is supplied. It never uses
backed sparse fancy indexing that can materialize the entire input.

Mean profiles are weighted by their cell counts to form aggregate counts, then normalized on the explicitly ordered
`normalization_genes` universe. Changing `selected_genes` only changes output columns, never that denominator.
Any duplicate symbol on either required axis refuses. The default missing policy refuses missing output genes or
targets; explicit `exclude` retains missingness flags and emits only measured responses. Missing normalization genes
always refuse. No missing observation is filled with a measured zero. The effect artifact keeps the frozen protocol,
its digest, source-row contributions/weights, exact gene identities, exclusions and normalization meaning. The enclosing
protected operation receipt retains the immutable matrix and protocol file references/content hashes.

Effects merge requires identical ordered output genes and normalization semantics, positive retained cell counts,
finite effects and explicit provenance. It preserves both source artifact hashes and metadata, and refuses duplicate
context/target pairs instead of pooling screens silently. Older `matrix-prepare` artifacts lack an explicit normalization
universe/provenance contract and therefore refuse this new merge; they still work through their existing fitting path.
Reprepare explicitly when combining sources. Model blending remains the separate `response-combine` operation.

## Statistical meaning

Preparation computes log2 ratios of normalized group sums with an explicit pseudocount. The target-only baseline
weights biological contexts equally, accounting for each context's panel size. Ridge penalty is relative to each
target's observed weight, so adding unrelated targets does not silently change regularization. This first response
model is context-independent; its separate count emission conditions on new controls. Unseen targets and unmeasured
genes use an explicit zero-effect prior with retained coverage flags. No anonymized context identity is guessed.

Emission fits control library-depth distributions, expression proportions and shrunk dispersion. Seeds are keyed by
seed/context/target/cell, independent of panel order and processing block boundaries. Each small generated block is
written as sparse float32 whole counts and combined with AnnData's `concat_on_disk`; there is no full dense prediction
allocation. Out-of-range counts or effects refuse instead of being clipped or pruned. This is a statistical baseline,
not a validated biological simulator. Generated-count moments and held-out scientific quality require independent tests.

## Runtime and resource ownership

`matrix-cpu-runtime` and optional `cell-evaluation-runtime` are ordinary catalog-owned environments with Python and
hashed dependency locks. The existing catalog preparation action installs and verifies each environment; scientific
operations use that prepared interpreter, not whatever happens to be installed in the app environment. Runtime setup
is readiness evidence, not model-quality evidence. Active execution leases its exact prepared generation. Missing or
changed runtime receipts refuse, and descriptor generations cover the adapter code, manifest and runtime identity.

Each execution acquires the shared machine heavy-work directory atomically and releases only its own unchanged
receipt. Occupied or uncertain ownership is never reclaimed automatically. The confined worker has authorized input
reads, its own output directory, no network/provider credentials and no child-process launch authority. The existing
process supervisor observes a 24 GiB RSS ceiling; the worker also has CPU-time/file limits. macOS does not offer a
usable address-space limit here, so RSS enforcement is observed and not a claim of a hard kernel allocation ceiling.
Stop terminates/reaps the worker before releasing its slot. Caller disconnect has no authority over an admitted run.

## Current qualification and limits

Tiny synthetic tests exercise real numerical libraries, sparse file assembly, Project retention, confined execution,
idempotency, cancellation and foreign/stale-input refusal. They also cover panel-size-independent regularization,
coverage priors, keyed seeds, approximate control moments and unsafe numerical-artifact headers. A separate prepared-
runtime canary exercised the actual upstream evaluator; this does not establish biological or full-size performance.

Dense intermediate effect preparation and fitting are bounded to 512 MiB; large training sources need a declared
compact preparation/fold recipe or further qualified streaming aggregation. Prediction preflights worst-case sparse
output plus temporary assembly space. Scoring and aggregate preparation project only X and requested metadata,
using HDF5/AnnData access; they do not load auxiliary layers, raw or embeddings. Sparse pointer/index dimensions and
integer dtypes, monotone pointer contents and in-range indices are checked in cancellable bounded chunks before
AnnData can cache pointers or invoke native sparse operations. Label arrays and categorical metadata are bounded before
decoding, with fixed-width HDF5 conversions for variable-length strings. Before materializing a whole
biological context it conservatively estimates eight copies of float64/64-bit-index payload from shape and sparse nnz,
refusing estimates over 8 GiB. The 24 GiB RSS supervisor remains an additional boundary. This conservative refusal
does not silently split the target panel. Large-context scoring, full-size packaging, sealed holdout isolation and sustained throughput are
still qualification work. The service cannot infer a sealed evaluator boundary from separate prompts or Projects.
No automatically generated labels or local proxy metric can establish the eventual research outcome.
