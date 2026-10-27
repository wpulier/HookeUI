# Scientific beta companion packages

These authored adapters and dependency sources include updates after the historical
beta companion archives listed below. A source change needs a newly prepared and
verified package before delivery. Install a matching `.observationui.zip` through
Models → Add your own → Add model/tool → Review setup → Prepare for review.
The distributed packages also contain the pinned napari wheel; do not substitute
an arbitrary napari version. Preparation downloads declared dependencies and, for
Cellpose, the declared model artifact. No API key is required for these local tools.

The full source is retained here; checksums identify the separate release assets.
These are candidate integrations, not independent qualification. Cellpose supports
the bounded 2D retained-image workflow described in its manifest. Animation recipe
capture preserves camera/dimension keyframes, not arbitrary layer appearance.
Recipe replay and complete final packaged workflow qualification remain open.

- `ome-zarr-reader.observationui.zip` — SHA256 `d254da03b656b77d770fe92dd482caa11b4c6ab342deb3b4d3768cb866c43bab`
  (historical format-2 reader; current exact-reader admission refuses that declaration).
  Current `ome-zarr-reader/` source is version 7 with a format-3 reader declaration,
  bounded uncompressed scalar numeric NGFF 0.4 preflight, exact dtype/byte-order checks and all-level
  validation before layer publication. It is not a newly built or published archive;
  full intake, complete tool Save/reopen and installed acceptance remain gates.
  Ticket 49 records the narrower actual selected-folder/native-output evidence.
  Compressed chunks are refused before upstream codec construction; use a separately
  exported uncompressed copy while keeping the original. The source adapter now reports this
  known refusal through `context.refuse_input`, the shared bounded startup-recovery
  contract. This requires a rebuilt and prepared companion; prior archives and
  prepared generations retain their original behavior. Bounded compressed NGFF
  support remains incomplete and part of the full intake requirements.
  Version 3 adds explicit standalone Labels only when the selected root declares
  [`image-label` version 0.4](https://ngff.openmicroscopy.org/0.4/#label-md).
  One uncompressed integer level with nonnegative object IDs, ordered spatial y/x
  or z/y/x axes, units and declared scale/translation becomes a native Labels layer.
  Integer images without that declaration stay Images. Linked roots, label hierarchies,
  multiscale Labels, custom colors/properties and time/channel Labels refuse before
  upstream reader construction; no label metadata is silently discarded. This bounded
  subset requires an explicit version even though NGFF permits it to be omitted,
  and retains the required dataset scale. The synthetic 40×60 rectangle test has 200 pixels at .5/.25 µm
  (25 µm²), entering through real folder intake/SDK grants with upstream/viewer stand-ins.
  It is not actual package, native Save/reopen, manual-edit or comparison acceptance.
  Version 4 introduced regional grayscale decoding through `Context.decode_image`,
  retaining bounded source reads and a 1 GiB aggregate decoded-publication ceiling.
  Its prepared generation `39dc5e99970e6a70` and existing receipts remain historical
  evidence, not a v5 preparation. Version 5 represents two to eight aligned image
  resolutions as **one multiscale Image** through that same shared native Save path.
  Levels must share dtype/byte order and origin, decrease in pixel count without
  any axis growing, and declare scales matching the finest shape ratios. Other
  physical grids refuse with recovery instead of dropping or inventing calibration.
  Single-level Images and bounded standalone Labels keep their existing semantics.
  Under 32 MiB total, values are decoded before publication; larger images/pyramids
  retain immutable regional decoders. Source chunks stay uncompressed and at most
  8 MiB, and the browser intake limit remains separate. Saving the native output
  retains every level's values and calibration; tool-workspace state Save has its
  separate bounds and is not claimed by this adapter change. Current focused
  evidence uses synthetic upstream/viewer seams plus real shared decoder and native
  output description. Version5 subsequently passed actual ordinary folder intake, native Save
  and restart/reopen on the bounded calibrated pyramid in ticket54; that does not qualify large data.
  Version6 negotiates `Context.streamed_image_publication` version1 before accepting decoded
  images above1GiB. It respects the host's declared maximum up to64GiB and still uses
  immutable regional decoding; source declaration is now64GiB, subject to ordinary intake's
  entry/storage limits. Older hosts refuse larger images before upstream construction and
  suggest updating or choosing smaller input. Labels keep their32MiB reader limit, and the
  uncompressed8MiB-chunk, eight aligned-level and exact calibration rules remain.
  The new host separately enforces disk, chunk count, rate, deadline and resident-memory
  limits; a64GiB declaration is not a measured working set or a qualification claim.
  Version6 focused contract/decoder checks pass32 tests in1.83s. Fresh package preparation
  and the actual large workflow remain required. Old archives/generations are untouched.
  Version7 adds two tiny `reader_checks` cases for the shared exact reader proof: a
  big-endian uint16 z/y/x grey pyramid and little-endian uint32 standalone object Labels.
  Their native bytes, ordered levels, dtype/axes/units, affine basis and label identity
  come from literal formulas/values in `ome-zarr-reader/generate_reference_cases.py`,
  using only Python struct/hashlib/JSON, independently of the adapter or upstream reader.
  The committed examples plus references total under8KiB; they contain no biological data.
  Version7 changes the declaration/fixtures only; v6 decoder, memory and size limits remain.
  Ordinary setup must run the real confined reader proof, then the user reviews and enables
  that exact generation. These source fixtures do not claim a proof receipt, native workflow,
  large-data qualification or public distribution permission. Historical v6 package and
  evidence remain unchanged; new packaging/preparation and actual acceptance are pending.
- `cellposesam-panel-beta4.observationui.zip` — SHA256 `36e6c2cf29b4e5d5335591f6dd1073d4d9e9c5a567e1078eabf2b2c3b8fec862`
  (released; its adapter saves every Cellpose run group empty)
- `cellposesam-panel-beta5.observationui.zip` — SHA256 `0822bc8ffc6bf523e5abd4b233827f7d9f74a768950d3641c126bfc6e687e06c`
  (built locally on 20 October 2026 for the L2 demonstration; never published; its reopen refused
  a saved output counter above 64)
- `cellposesam-panel-beta6.observationui.zip` — SHA256 `fc9de4a88e9728f435db9800a3d47bfadc1a4fdfffde8571c5bed6b5deb66aaf`
  (historical local build from 20 October 2026; not yet published).
  Current `cellposesam-panel/` source is beta-8 and preserves its device self-check,
  prepared model artifact and declared 4096 MiB runtime limit; beta-6 is not its source identity.
- `animation-camera-recipe.observationui.zip` — SHA256 `62a62b9caa75c97f46a756dfd259711653ee8a87f209523916a3093230f3767d`
- `bdv-hdf5-reader/` (ticket 59's MT5) — source only, **not packaged**: no `.observationui.zip` and no
  `env.lock` yet (they are resolved and pinned when the package is built). A format-3 folder reader for
  BigDataViewer HDF5 (the XML and the HDF5 file it names, as Huygens and Fiji's BigDataViewer export them):
  lazy pyramid levels each with its own scale, level 0 the measured one, a missing or misnamed companion said in
  words. Genuinely single-resolution inputs now decode all setup/timepoint images within a combined 64 MiB
  limit before publishing any layer, preserving native values, byte order and calibration for ordinary Save.
  Nonempty 3D fixed-width scalar pixels are required; the reader refuses unsupported or oversized inputs
  before pixel reads and closes sources on startup failures. Multilevel images stay lazy; pyramid persistence
  remains incomplete. A new package, preparation/proof and ordinary UI check are required for this change. Its evidence is synthetic folders written with h5py in the published layout
  (`services/tests/test_bdv_reader.py`); no real Huygens export has been read, and the napari host path
  is not run by those tests.

The source-only BDV reader accepts hard-linked objects within its selected HDF5 file. Soft/external links,
virtual datasets and external raw storage are refused before dereferencing or reading them; those dependencies
have not been brokered through the selected-folder grant. Resolution/chunk metadata is bounded to 256 numeric
x-y-z rows before allocation. XML is limited to 256 setups and 4,096 timepoints, with at most 4,096
setup/timepoint/level combinations, all counted before range allocation or pixel reads. This does not qualify all Huygens/BDV exports or create a distributable package.


The animation candidate's owner and member display names are `napari-animation`; its description separately
states the private-review and qualification limits. This is a metadata change, not a new scientific acceptance.
`envs.operational()` excludes names and member descriptions, so updating these words within the same catalog
namespace preserves its runtime generation. A newly shared archive has different manifest bytes and therefore
a new content-addressed channel/namespace: its normal import/preparation boundary still applies. Preserve old
archives, prepared snapshots and receipts; do not rewrite their historical names or infer new UI evidence from them.

The current animation adapter source corrects its movie-export guidance to name
Hooke's Export section. The archive above remains the original release artifact
and still has the earlier Save-section wording; this source change has not been
rebuilt or published as a new package.

Cellpose beta-8 source explicitly declares the actual bound input grid for the pinned
plugin's masks, outlines, cell probabilities and RGB flow diagnostics, after copying
full input geometry. Shared output binding version 2 validates the source reference,
unchanged data, axes/grid/affine/units; it keeps each output's own meaning and dtype.
Unknown outputs receive no coordinate relationship. The package refuses hosts without
`output_binding_version == 2` before importing the plugin. Reopened runs name the
actual rebound input object; prior output coordinates alone do not establish lineage.
This source has not been built/prepared or exercised with the actual async plugin;
quiescence and scientific/runtime verification remain required. Historical archives
and accepted outputs are unchanged, and their existing coordinate identities remain.

The animation source also scopes the pinned widget's slider signal block during a
frame-count change, preserving its previous state. The actual upstream callback
regression is recorded in ticket 58; the historical archive and prepared generation
remain unchanged. A rebuilt candidate and ordinary interaction/movie journey are
still required before this source repair can qualify the packaged tool.

The BDV source now preserves both little- and big-endian uint16 interpretation,
including BDV's signed int16 storage encoding and h5py scalar reads. Its format-3
`byte-order` declaration is supported by tiny real h5py 3.16.0 fixtures, not by a
real Huygens export or a complete native UI journey. The failed private candidate
`b6b909f9063e5fd1` retains its old code/declaration/proof; this source repair requires
a new package, genuine preparation and proof before the maintenance journey repeats.
