# Native-grid raster reader and runtime

Program: https://github.com/pourpoint-tools/pourpoint/issues/2
Effort: https://github.com/pourpoint-tools/pourpoint/issues/4

Supply the paired direction and accumulation windows needed to refine a terminal
unit, with little overhead beyond selected reads. Build a reusable sequential
reader and typed runtime boundary that a future batch planner can control. This
work stops before masking the terminal polygon, seed selection, tracing or
polygonization. Upstream unit selection remains vector work.

## Authoritative input

Implement the delivered [HFX 0.4.0 raster contract](https://github.com/pourpoint-tools/hfx/blob/bb7ea1ddb0c83e2a38968450e7163a921d5e3a4f/docs/v0.4.0/raster-data.md)
together with its [vector contract](https://github.com/pourpoint-tools/hfx/blob/bb7ea1ddb0c83e2a38968450e7163a921d5e3a4f/docs/v0.4.0/vector-data.md).
Do not implement against an inferred 0.3.0 side channel or the old Effort prose.
The root manifest explicitly declares optional `hfx.aux.d8_raster.v2` pairs and
binds their relative artifact paths through its fingerprint map. The retained
source manifest is provenance only.

This was a small but real redesign, not just copying the old publication:
0.4.0 explicitly permits native-only internally tiled, cloud-friendly TIFFs
without overviews. Do not require strict overview-bearing COG conformance.
GRIT's native-only BigTIFF pair is 186,456,055,709 bytes, with 69,300,330,850 bytes
of overview storage removed. Native values, float32 bits, masks and grid were
preserved. D8 v2 direction, units, coverage and overlap meanings remain intact.
GRIT-specific compression and dimensions are evidence, not universal constraints.

The delivered root is `https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0/`.
Obtain a fresh manifest, then keep a consistent snapshot and artifact identities
through its reads. Old caches may still contain a valid vector-only manifest
under the former immutable policy. New manifest responses require revalidation.
Do not discover undeclared rasters to compensate for an old snapshot, and do not
refresh the manifest silently midway through a dataset lifetime.

## Reader and runtime boundary

Use Rasterio/GDAL for raster access and established CRS tooling where needed.
Integrate with the existing Dataset lifetime without opening raster files for
vector-only use. Reuse opened direction/accumulation handles for successive
requests; raster acquisition must not require loading topology or catchments.
Continue supporting local and remote dataset use; fit raster access to existing
source-specific configuration and integrity guarantees rather than assuming
obstore configuration or version pinning automatically applies to GDAL. Exercise
local, HTTP and S3-compatible access with controlled fixtures; public GRIT provides
real HTTP integration, not proof of a live credentialed S3 deployment.

Separate request preparation from execution:

- Select a declared pair covering the entire requested region. Respect each
  entry's raster CRS independently of the vector CRS. Transform the requested
  geometry/bounds conservatively; do not assume transforming two bbox corners
  encloses a projected terminal polygon. Headers can establish coverage lazily.
- Once grid metadata is known, resolve native bounds into enclosing whole-cell
  windows without pixel I/O. A prepared request carries integer column, row,
  width and height and immutable dataset/grid identity. Reject foreign requests.
- Execute the request against a reusable synchronous reader pair. A future
  planner must be able to inspect requests before deciding execution order.
  No batch, async, scheduling or coalescing API is required now.
- Return compact numeric direction and accumulation arrays, separate validity
  masks, window transform and CRS, and explicit direction/accumulation semantics.
  Normalize the declared D8 encoding on read so downstream algorithms do not
  reimplement storage decoding. Preserve terminal, exiting-flow and nodata
  distinctions; normalization must not discard signed GRASS meaning.
- Preserve accumulation precision and declared units. Do not cast int32 through
  float32 or turn width-partitioned GRIT km² into a different area definition.
- Results are caller-owned and remain valid after later reads, mutation of later
  results and reader closure. Do not return shared mutable scratch buffers.

Exact classes, method names and normalized direction representation are
implementation choices. Keep them strongly typed and small. No general raster
framework or duplicate raw/normalized representation is required by this vision.

One ordinary reader pair permits one active call. Future concurrent workers can
own private pairs with supported thread ownership; do not share ordinary GDAL
handles concurrently. GDAL owns its caches, including process-wide VSICURL state
and block-cache budgeting. Closing a dataset does not promise to flush every
process-wide cache. Document resource/lifetime rules without implementing worker
pools, custom caches or service-wide budgets.

## Trust and failure boundaries

Trust producer conformance. Check consumed declarations and supported semantics,
artifact bindings, opened pair CRS/grid/affine/dimensions, native dtype/nodata and
accessed values as needed to produce a usable result. Do not scan whole rasters,
compare all overlaps, certify coverage union or repeat producer layout/hydrology
validation at startup. Bounded identity checks are not full-file integrity proofs;
use the published fingerprints and stable object versions consistently. Never
hash a whole remote raster to open it or silently mix changed objects.

Support the contract's ESRI, TauDEM and unsigned/signed GRASS encodings and its
cells/km² accumulation variants. Header nodata takes precedence over guessed
codes. Keep direction and accumulation validity separate. Invalid consumed
values must fail explicitly rather than silently become absent flow.

Coherent overlapping entries permit selecting any fully covering pair. No
mosaicking, overlap arbitration or flow-direction warping. Missing declarations,
no fully covering pair and malformed/unreadable data must remain distinguishable;
this reader does not decide coarse-watershed fallback policy. Vector-only bundles
remain valid and usable.

## Evidence and success

The [merged baseline](https://github.com/pourpoint-tools/pourpoint/blob/8849ba565bd855375f460a0996c93b08e5608661/experiments/terminal_raster_access/README.md)
measured the original 0.3.0 raster pair: 1.43–1.63 s through first usable arrays,
sub-1.3 ms cached repeats with zero HTTP requests, and about 168 MB peak process
memory for five small terminal windows. Independent TIFF decoding checked all
233,085 cells per band and separate masks. These are diagnostics, not latency
targets or measurements of the newly published files.

A reviewed local follow-up used the same five windows: one sequential pair took
4.05–4.79 s / 25 HTTP requests / about 168 MB peak; two private pairs took
2.21–2.24 s / 27 requests / about 232 MB peak. Outputs matched exactly. It supports
leaving room for parallel scheduling, not implementing it now. At confirmation
this work remained unmerged on `experiment/terminal-raster-concurrency`; inspect
`experiments/terminal_raster_access/CONCURRENCY.md` in that branch if available.
Its measured code was `6eebc8c14809c5cad19fabee0a7e1b0890a00427`.
Neither experiment measured actual raster response bytes or optimized batching.

Use small independent fixtures for encodings, terminal/exiting cells, separate
nodata masks, precision, projected bounds, tile/window boundaries, pair mismatch,
missing coverage, foreign requests, ownership and closure. Reuse existing tests
where they prove the behavior. Ensure vector opening does not acquire raster
headers or payloads. Native-only layouts and large tile indexes must not trigger
whole-file or eager whole-index acquisition merely to satisfy a small window.

Verify selected results against an independent decoder on the delivered 0.4.0
publication, including boundary and nodata cases. Measure preparation through
first useful arrays, reuse, requests, response-body bytes where instrumented,
and retained/peak memory. Do not substitute requested ranges for received bytes;
report gaps explicitly. Keep correctness work outside timing and distinguish
array storage from process RSS. No numerical latency gate was agreed. Do not
expand this into another benchmark program or claim end-to-end delineation speed.

The [HFX delivery proof](https://github.com/pourpoint-tools/hfx/blob/bb7ea1ddb0c83e2a38968450e7163a921d5e3a4f/docs/v0.4.0/grit-raster-verification.md)
already establishes full native preservation and selected public access. Do not
repeat that global producer job. Preserve CC BY-NC 4.0 attribution and keep bulk
data and sensitive logs outside Git. Use uv, typed Python, Ruff, ty and the
repository's test-first conventions. Keep changes proportional to this reader.

No HFX publication changes, source conversion, raster generation, overview
creation, full raster download, custom TIFF decoder, batch optimizer, terminal
refinement algorithm or complete Engine composition belongs to this outcome.
