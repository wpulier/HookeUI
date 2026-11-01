# Retained scientific matrix capabilities

The same catalog operations serve a person in Tools, a local researcher, and an authorized connected agent.
`WorkflowService` owns runs and attempts; `matrix_capability_runs` admits one exact operation and publishes its
immutable file result. This service does not schedule another job, replay work, or interpret a Study's claims.

## Interfaces and artifacts

Every input is a Project-owned document id, revision, digest and optional file component. The adapter verifies
ownership and content hashes before materializing bytes into a private worker directory. Arbitrary host paths,
foreign Projects, ambiguous components, changed generations and changed intent under the same request refuse.
Outputs use the existing chunked FileIntake and keep exact input references, capability generation, parameters,
runtime receipt identity and output meaning in protected `state.matrixOperation`. Full matrices never travel through ordinary tables or chat context. Inspection reports may retain the
strictly bounded preview described below; the original matrix remains an immutable file. Generic document saves cannot manufacture these receipts.

The capabilities are:

- `matrix-inspect`: read raw-count AnnData and report axes, count diagnostics and scientific meaning.
- `matrix-project-counts`: select explicitly mapped raw-cell panels and ordered genes, preserving source cell identities.
- `matrix-prepare`: aggregate counts by explicit biological context and target, relative to each context's controls.
- `aggregate-profile-prepare`: convert explicitly mapped, cell-weighted mean profiles using a retained scientific protocol.
- `response-effects-merge`: concatenate compatible prepared effects, refusing duplicate biological-context/target rows.
- `response-fit`: fit regularized target effects, optionally with a bounded low-rank approximation.
- `response-effect-evaluate`: compare fitted effects against measured effects from other biological contexts.
- `response-predict`: fit control-conditioned count moments and emit sparse whole-count predictions.
- `matrix-count-diagnostics`: compare population count moments, zero fractions and library depths.
- `matrix-evaluate`: a deliberately limited mean-expression diagnostic, explicitly not a competition score.
- `matrix-evaluate-cell`: use pinned upstream cell-eval2 with a retained protocol and complete declared target panels.
- `response-combine`: combine fitted effects on identical ordered gene/target axes with explicit weights.
- `matrix-concatenate`: join count rows with exact gene order and unique cell identities.
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

### Raw-cell reference projection

`matrix-project-counts` takes a retained matrix and `raw-count-projection-v1` JSON
protocol. Required fields are `format`, `context`, `experiment`, `observation_id`,
`target_column`, `target_mapping`, `control_label`, `selected_targets`, `gene_id`,
`gene_symbol`, `selected_genes`, `cells_per_group` and `seed`. Column names are literal
source fields. The mapping takes exact source annotation values to declared target
labels, including controls. Every mapping key must exist, and mapped values must
cover exactly the complete selected panel and control label. No inferred aliases,
missing genes filled with zeros, target intersection or scientific-context guessing.

Zero `cells_per_group` retains all mapped observations. A positive value requires
that many cells in every group, selecting the lowest SHA-256 keys over JSON tuples
of seed, context, target and source cell identity, then preserving source row order.
Selection is independent of expression and source row order, but does not establish
representative sampling. At most 4,096 perturbation groups are admitted. The exact
protocol, source hash, available/selected group counts and exclusions travel in
`uns.projection` as JSON as well as the protected operation receipt.

Dense/CSR rows are read in bounded blocks; sparse float32 output must preserve whole
count values exactly. CSC, ambiguous identities, missing groups, insufficient cells,
unsafe allocations and excessive stored entries refuse. Gene output order is explicit.
Target annotations use bounded compact string references rather than widening every
row to the longest label. The existing metadata allowance includes decoded text,
references and category codes; missing, oversized and invalid labels still refuse.
Source observation identities, mapped context/target, original target annotations
and source gene identities are retained and verified after disk assembly; a per-row
count witness verifies unchanged selected values. This is a declared projection:
other annotations, raw, layers and embeddings remain in the original retained input,
not the selected output. It is not a complete source export. No source is modified.

The same generic catalog/workflow operation prepares diagnostic or evaluation inputs
for any research Project. Fixture and confined-worker checks do not qualify full-size
public-source conversion, the chosen panel, biological accuracy or upstream scoring.

Normalized-effect transfer diagnostics require identical ordered gene axes and explicit
normalization semantics. The fit retains validated aggregate preparation meaning and its
source-file hash. Legacy fits remain usable in their prior paths but refuse this diagnostic
until explicitly refitted. Training/evaluation context overlap refuses; context labels are
the protocol's declarations, not an independently verified biological identity or isolation
boundary. Every reference context/target pair is scored, including unseen targets using the
declared zero-effect prior. No silent panel intersection occurs. MSE and MAE are averaged
equally by gene, target and then context; the zero-effect comparator follows the same weights.
MSE skill is unavailable when its zero-effect denominator is zero. These are development
diagnostics, not the official six-metric evaluator, generated-cell quality or sealed testing.
Model blends lacking preparation provenance refuse rather than inheriting an assumed scale.

Preparation computes log2 ratios of normalized group sums with an explicit pseudocount. The target-only baseline
weights biological contexts equally, accounting for each context's panel size. Ridge penalty is relative to each
target's observed weight, so adding unrelated targets does not silently change regularization. This first response
model is context-independent; its separate count emission conditions on new controls. Unseen targets and unmeasured
genes use an explicit zero-effect prior with retained coverage flags. No anonymized context identity is guessed.

Emission fits control library-depth distributions, expression proportions and shrunk dispersion. Seeds are keyed by
seed/context/target/cell, independent of panel order and processing block boundaries. Each small generated block is
written as sparse float32 whole counts and combined with AnnData's `concat_on_disk`; there is no full dense prediction
allocation. Out-of-range counts or effects refuse instead of being clipped or pruned. This is a statistical baseline,
not a validated biological simulator. The legacy `proportional-fold-v1` interpretation remains the default
and is explicitly an approximation to pseudocount-adjusted training effects. The optional
`pseudocount-inverse-v1` requires fitted aggregate normalization metadata and every normalization gene in controls.
It computes `(control_normalized + pseudocount) * 2**effect - pseudocount`, explicitly projects negative
expected expression to zero (reported as a count), then closes the whole output composition. The normalization
subset uses its own control-expression denominator. Zero effects preserve the original expectation before closure;
closure can change absolute proportions of genes carrying the zero-effect prior. Legacy/unknown scales refuse
inverse mode. Both methods retain the same keyed independent-gene sampler. Library depth is a sampled expectation,
not a promise that emitted totals reproduce the observed depth distribution. Analytical inversion tests establish
scale conversion, not a scientific improvement. Generated-count moments and held-out scientific quality require independent tests.

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

Retained matrix results use the ordinary exact-revision file download route, including fitted models,
effects and exported prediction files. Download verifies the stored file reference and bytes; it does
not execute a model or rewrite the result. Source files and computed results retain distinct kinds.

## Count assembly

`matrix-concatenate` takes two exact retained raw-count files. It preserves row order, cell identities,
context/target labels and ordered genes; overlapping identities, reordered genes and extra annotations refuse
rather than being renamed, intersected or discarded. This narrow counts-only operation refuses raw/layers,
embeddings, pairwise arrays and extra obs/var/uns annotations. It emits sparse float32 only when whole values
are exactly representable, with explicit cell-count and stored-entry limits. Zero entries are removed without
pruning nonzero values. Bounded chunks and upstream `concat_on_disk` avoid a full dense allocation. Post-assembly
reads verify labels, dimensions and canonical sparse content hashes before publication; source files are unchanged.
Imported storage and labels are preflighted through bounded HDF5 reads before AnnData decoding. Dense/CSR inputs are supported; CSC needs a separately qualified conversion. Sparse row-block allocation is bounded independently of logical shape. Ordinary workflow composition joins more than two files. The result is not an official packaged submission.

Scientific worker errors place their final exception first so bounded workflow notes preserve the reason.
The following diagnostic tail remains bounded; this is not a promise to retain a full worker log.

## Population-count diagnostics

`matrix-count-diagnostics` compares one explicitly labelled population per context in each
input. The population labels may differ (for example generated null response versus controls),
but context sets and ordered genes must match exactly. Mixed perturbations refuse instead of
being pooled. Each population needs two cells and positive library depths. Bounded dense/CSR
HDF5 reads project only X and the requested metadata; auxiliary arrays are not loaded.

It reports relative L1 differences in raw means, population variances and per-cell normalized
means, plus per-gene zero-fraction MAE and library-depth quantiles. A zero reference denominator
produces unavailable/null, never invented zero error. Full gene moments remain in the retained
JSON file; the operation receipt is a compact context summary. Population moments do not test
covariance, multimodality, biological fidelity or distribution equality. This is not an official
competition score, and inspecting conditioning controls is not independent predictive evaluation.

Count diagnostics v2 also summarize signed per-gene zero-fraction residuals (prediction minus
reference). They report sign counts, mean signed/absolute error, and positive-reference-mean
expression strata using nine linearly interpolated decile cuts. Ties at a cut stay in its lower
interval; empty intervals remain explicit and are not described as equal-sized groups. Genes
with zero reference mean have a separate group and remain in the overall diagnostics. The
average-rank correlation of positive reference means with signed residuals is descriptive,
with no p-value; fewer than two genes or a constant axis returns null. These summaries help
inspect distribution errors without transporting all gene arrays into chat. They neither
identify the cause nor change generation, scientific thresholds or previous reports. A new
comparison produces a new retained report through the same capability and workflow path.

Matrix-inspect v4 has one bounded document-preview exception: the positional first 10 observations × 10 variables and at most 10 annotation columns per axis. No full matrix is transported in document JSON. Unsafe integer values are decimal text with explicit encoding metadata; this preserves numeric identifiers beyond JavaScript’s safe integer range. Preview is never representative sampling.
