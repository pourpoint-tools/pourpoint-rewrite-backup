# HFX reader and runtime data foundation

Program: https://github.com/pourpoint-tools/pourpoint/issues/2
Effort: https://github.com/pourpoint-tools/pourpoint/issues/3

## Outcome

Deliver the **production HFX reader and the typed runtime representation it
populates**. Downstream algorithms must be able to select units, traverse upstream,
and access attributes, polygons and snap candidates without implementing file
access or ID lookup themselves. This is working engine foundation code, not another
benchmark or a collection of type declarations.

Support **HFX 0.4.0 only**. The maintainer selected the published-lookup and
byte-budgeted Arrow IPC design formerly called candidate B. Its finalized input
contract is [HFX 0.4.0 vector data](https://github.com/pourpoint-tools/hfx/blob/9000d57311382b1549eb5bc5eedb646d03a387aa/docs/v0.4.0/vector-data.md).
Do not retain a production 0.3.0 catchment reader, candidate A, or the experimental
B wrapper. Source-fabric normalization and conversion remain upstream in HFX.

The eventual public experience remains `Engine(dataset=url)` followed by
`engine.delineate(lat=..., lon=...)`. Users must not construct internal topology or
manually download hosted data. This Effort supplies the reader/runtime foundation;
complete delineation and the final public engine composition belong to later Efforts.

The previous implementation's 22.7-minute GRIT initialization was rejected.
Replace eager worldwide catchment preparation, not the useful compact runtime
work. Opening or the first useful request must not scan all catchment IDs,
attributes or geometries. Moving that work from opening into the first query would
not satisfy the outcome. Trust upstream dataset validity; do not make engine
opening a comprehensive dataset audit.

## Input and access

Use the finalized root manifest for artifact discovery, including graph and named
snap declarations. `source-manifest.json` is provenance, not the runtime discovery
manifest. Preserve additional fields and source caveats. Rasters are not delivered
by this vector contract; do not infer raster files from its source manifest or
settle raster APIs here.

Required real-scale input:

`https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0/`

This public GRIT bundle has 22,337,300 units and DAG topology. Its graph and two
snap artifacts retain their 0.3.0 bytes. Catchments are compressed Arrow IPC file
batches containing attributes and exact WKB. The uncompressed numeric lookup maps
positive int64 IDs to batch/row addresses. The small directory narrows lookup
batches; membership in a directory range is not proof that an ID exists.

Read directory and IPC footer/schema metadata, then only the numeric lookup and
catchment batches needed by a selection. Check exact IDs and addressed rows;
group shared reads for batched requests. Do not infer catchment positions from
graph order, ID values, Hilbert order, equal counts or matching batch boundaries.
The 8 MiB logical batch budget is not a hard allocation or response-size limit;
valid oversized singleton rows must work. IPC compression and WKB still require
decoding. Do not claim zero-copy geometry.

Support local paths, anonymous HTTP(S), AWS S3 configuration, and S3-compatible
storage such as Hetzner and R2. Use manifest-relative paths without directory
listing. Keep transport/authentication separate from hydrologic code; public HTTPS
must not require AWS credentials. Prevent credentials or cached objects from being
reused against the wrong source.

Build on **obstore, PyArrow and NumPy/Arrow**, with Shapely/GEOS for selected geometry
decoding. Prefer established transport and file adapters. Verify efficient range
access on the actual IPC and Parquet paths; adapter availability alone proves
neither request batching nor bounded reads. A measured adapter gap can justify
another established integration, not a custom storage engine or blindly copying
the benchmark's transport into production.

The unchanged graph may be acquired as its original local Parquet artifact and
prepared into compact resident topology. Existing evidence supports whole-graph
acquisition followed by local decoding, not remote serial projection. Local sources
need not be copied into another cache. Reuse verified original artifacts and
in-process prepared data; do not rebuild them for every query. Define what opening,
first topology use and reopening each cost. Preserve safe download completion and
source-specific identity. Do not invent a binary topology cache format. Elaborate
persistent-cache policy remains #7.

Honor the 0.4.0 integrity contract: sizes and bounded hashes for consumed lookup
and catchments, full directory digest, lookup-to-catchment digest binding, and a
consistent immutable artifact set. Pin strong transport ETags or object versions
across ranges; they are not SHA-256 digests. Verify range status, Content-Range and
body lengths over identity-encoded bytes. Bounded hashes are mismatch detection,
not whole-file integrity. Reject changed identities and incomplete cached artifacts.
Do not trust filename existence alone or require worldwide rehashing on every
ordinary remote opening. Consumers requiring independent full integrity have a
different cost from access to this trusted, producer-verified immutable publication.

## Runtime capabilities

Use a small coherent set of strongly typed interfaces backed by bulk arrays and
batches. Avoid one Python object or dictionary per dataset unit. Class names and
reversible internal widths remain implementation choices.

- **Identity and metadata:** preserve positive int64 dataset-local IDs, levels,
  artifact declarations and named snap sets. Checked narrower internal positions
  are acceptable. Selections retain source/table identity so they cannot silently
  address another dataset or ordering.
- **Topology:** compact same-level adjacency and numeric lookup support inclusive
  upstream selection. Include the terminal and each reachable unit once, including
  DAG reconvergence. Unknown requested IDs and unusable referenced IDs are errors.
  Do not restrict upstream membership to a spatial search window. Shared prepared
  topology is read-only; visited/frontier/output state belongs to each query.
  Account for graph-sized workspace even when the output is small. Avoid duplicate
  retained edge representations.
- **Attributes:** supply identity, level, nullable parent, local and nullable
  upstream area, outlet, bounds and optional source fields when needed. Preserve
  Arrow types and null semantics. Parent containment is separate from same-level
  connectivity. Do not rebuild the old worldwide nullable-attribute catalog.
  Attribute access may decode its containing IPC batch, including WKB buffers;
  it must not decode all those WKB values into geometry objects unnecessarily.
- **Selected polygons:** retrieve by unit IDs with explicit ordering and duplicate
  semantics. Preserve caller order and duplicates, and fail rather than silently
  dropping missing requested units. Carry IDs alongside WKB through gathering and
  decoding. Select compact results before geometry decoding; do not let small views
  pin discarded large batches or retain serialized and decoded copies indefinitely.
- **Spatial catchment candidates:** supply conservative bounds/level candidate
  access without scanning the entire IPC catchment file. The published lookup is
  an ID index, not a spatial index. Graph bbox columns and Parquet statistics are
  available evidence for candidate discovery; investigate and verify that path.
  Return selected catchment records through the published lookup. Bbox overlap is
  not exact containment. Preserve degenerate bounds and conservative handling of
  float32 bounds against full-precision coordinates.
- **Snap candidates:** support every declared snap set. Feature IDs differ from
  referenced unit IDs; many features may reference one unit. Respect declared and
  referenced levels, weights, nullable roles and correct absent/null-bounds fallback.
  Do not invent cross-set weight comparability. Candidate retrieval is not final
  radius testing, nearest-point calculation or snap ranking.

The dataset owns reusable readers, metadata and resident arrays. Queries own
workspace and selected results. Provide explicit resource release and clear
mutability/lifetime rules. Preserve useful tests and guarantees from the paused
runtime, not its eager preparation or duplicate decoding work.

## Validation boundary

Validate fields and representations actually used: supported version/schema,
safe relative paths, artifact identity, required types/nullability, capacities
before narrowing, bounds before indexing, exact lookup matches, and selected
references/levels where access depends on them. Fail clearly on malformed consumed
records rather than returning plausible incomplete results. Selected WKB decoding
must enforce the required geometry type without unnecessary repeated decoding.

Do not globally scan catchment attributes to certify parents, areas, outlets or
graph/catchment correspondence. Do not run comprehensive graph acyclicity/tree or
hydrologic geometry audits during opening. The producer owns dataset validity.
Numeric lookup and edge translation must still be unambiguous and safe. A visited
set prevents repeated traversal; it does not certify a valid DAG. Document these
limits and replace old tests that promised global auditing rather than silently
claiming the previous guarantees.

## Evidence and performance acceptance

The [producer verification record](https://github.com/pourpoint-tools/hfx/blob/9000d57311382b1549eb5bc5eedb646d03a387aa/docs/v0.4.0/grit-verification.md)
records independent equality for all 22,337,300 catchments and lookup addresses,
upload verification and public selective reads. It does not establish Pourpoint
startup, snapping or complete delineation performance. GRIT's partitioned DAG
upstream-area semantics and null values remain source facts, not errors to repair.
Preserve its attribution and CC BY-NC 4.0 terms; do not commit raw datasets.

Build on [#9](https://github.com/pourpoint-tools/pourpoint/issues/9),
[#19](https://github.com/pourpoint-tools/pourpoint/issues/19), the
[compact topology experiments](../../experiments/dense_topology/README.md), and
[local/HTTP/Hetzner catchment evidence](../../experiments/catchment_read/hetzner_results/README.md).
Published lookup removes startup scanning; byte-budgeted IPC reduces expensive
block overread. Small-request latency was mixed, not a universal B speed win.
Those candidate studies started after upstream IDs were known.

A focused 0.4.0 discovery run on 2026-10-08 used the same cached graph and terminal
`22333245` as the rejected initialization. It measured:

| Phase | Seconds |
| --- | ---: |
| Cached graph SHA-256, local decode, compact topology preparation | 1.283 |
| Remote metadata, selective lookup and catchment access | 2.322 |
| Inclusive traversal and terminal polygon decode | <0.001 |
| Total through first useful result | 3.612 |

The new run returned complete attributes and WKB for all 326 upstream units and
decoded the terminal polygon. Nine HTTP GETs returned 4,224,821 response-body bytes;
only lookup batch 340 and catchment batch 6655 were selected. Independent ID-domain
traversal matched membership, and independent source Parquet groups 5451/5452
matched all selected values, schema metadata and exact WKB after measurement.

The old run took 1,360.237 seconds to initialize plus 1.349 seconds for its first
attributes/terminal-polygon query. Its initialization used a cached graph but still
made 5,458 requests and received 1,246,090,633 response-body bytes. The new prototype
removed worldwide catalog/locator construction and the global Kahn audit. It also
used direct HTTP rather than the old proxied obstore path. This is evidence for the
redesign, **not an isolated format-only speedup or production implementation**.

Both exclude graph downloading. The new run excludes spatial/snap readiness and
queries, uses a selected-row Python dictionary rather than a production batch API,
and includes per-row witness serialization/hashing in timed access. It was one
fresh-process run after a one-ID smoke check; laptop, OS and CDN caches were
uncontrolled. Peak RSS through readiness was 2,855,813,120 bytes versus the old
2,984,624,128: this does not establish a meaningful peak-memory improvement.
Resident topology remained 670,353,612 bytes, with 111,686,500 bytes of query
workspace. Graph conversion temporaries still deserve attention.

The maintainer accepted this as sufficient design evidence to proceed with the
whole reader/runtime Effort. **3.612 seconds is not a fixed acceptance threshold.**
Verify the production integrated path rather than substituting this prototype result.
Measure fully cold access, cached-graph initialization through first useful access,
and repeated/batched reuse separately. Show preparation and deferred work so a cheap
`open()` cannot hide expensive first use. Record time, actual request/body-byte
counts, retained buffers and peak memory with dataset identity and settings.
Compressed object size is not network traffic. Explain any substantial regression
or unnecessary work before claiming delivery; do not claim an end-to-end delineation
latency or concurrent-service resource budget.

The discovery receipts and code are retained outside Git at
`~/.prime/agent/session-artifacts/01a11843-684e-72db-bb12-6a8534bf214a/grit04-comparison/`
(`README.md`, `measured.json`, `phase-summary.json`, `provenance.json`, fixture
red/green logs). The committed vision records the useful findings so implementation
does not depend on that local directory being available.

## Completion and boundaries

Deliver verified **open → topology selection → attributes → selected geometry**
through production interfaces, plus independent spatial catchment and both GRIT
snap-set candidate paths. Use independent small fixtures for sparse/shuffled IDs,
independent artifact orders, levels/parents/nulls, missing IDs, reconvergent DAGs,
lookup boundaries, oversized batches, changed objects, malformed consumed records,
multiple snap sets and absent/null/degenerate bounds. Test resource release,
selection identity, ordering/duplicates and selected-buffer lifetimes. Reuse existing
coverage where it proves the revised contract; do not duplicate test inventories.

Use public 0.4.0 GRIT for real-scale HTTP integration and controlled range tests to
expose accidental full catchment/snap downloads. Exercise local and S3-compatible
paths with valid 0.4.0 fixtures; existing HydroBASINS candidate studies are evidence,
not a requirement to support their obsolete wrapper or old 0.3.0 layout. Verify
AWS configuration without claiming unperformed live-provider testing. Keep raw
source data and credentials outside Git.

No complete outlet resolution/snapping, watershed dissolve, area calculation,
raster windows/refinement, CLI, release work or general cache framework belongs
here. #5 supplies whole-unit delineation, #4/#6 handle raster work, #7 extends
access/cache lifecycle, and #8 completes the public engine.

The root checkout currently contains research, not delivered production behavior.
The paused branch `feat/hfx-runtime-foundation` at
`7f9ce90fbba539ecc0062b6c3bb3b3b8ab76c29a` preserves reusable topology, selection,
spatial, storage and geometry work. Adapt useful code and tests deliberately; do not
merge that rejected implementation wholesale. Its existing worktree also holds
untracked verification records. Preserve unique files and history before any
separately authorized cleanup; a fresh implementing agent must not assume it is safe
to delete. Implement on a focused branch and deliver through a reviewed PR to `main`.

Use `uv`, strong typing, Ruff, ty and dependency-provided test utilities. The current
AGENTS.md reference to `../hfx/spec/HFX_SPEC.md` is stale: the finalized sibling
contract is `../hfx/docs/v0.4.0/vector-data.md`, pinned above. The
[Rust baseline](https://github.com/CooperBigFoot/pourpoint/tree/60e3d6e383546ea6ed20e68fe4d77959c816a48c)
is regression evidence, not authority to reproduce eager reads or suspected defects.
