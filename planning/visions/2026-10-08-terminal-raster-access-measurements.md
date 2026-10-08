# Terminal raster access measurements

Run a small, reproducible Python experiment that measures the cost of supplying
paired flow-direction and accumulation windows for terminal-unit refinement.
Return the numbers to the maintainer. There is no performance target, sufficiency
judgment, optimization investigation, or design selection in this work. The
maintainer will interpret the results in a separate session.

This is standalone research associated with [issue #28](https://github.com/pourpoint-tools/pourpoint/issues/28).
The confirmed scope here supersedes that issue's earlier proposals to compare
batching strategies or recommend improvements. It does not implement Effort #4
or change its discovery status.

## What to measure

Use a sensible existing-library reader on the unchanged public GRIT HFX 0.3.0
rasters. Rasterio/GDAL is the starting point; exact reader settings and runtime
types are engineering choices, not a library-comparison experiment. Supply
compact numeric arrays with the information needed to interpret them: native
window transform and CRS, direction encoding, accumulation units, and separate
per-band validity. Preserve native cell values and meaning; do not warp,
resample, silently lose precision, or discard direction cells merely because
accumulation is missing.

Measure:

- First use: opening, metadata and tile-index access, and the first usable paired
  arrays, both together and by meaningful phase. Charge preparation rather than
  hiding it before timing or in an unreported first query.
- Repeated reads through an already-open reader, using its normal caching.
- A small fixed batch of terminal windows through one open reader, again using
  normal caching. Include nearby/overlapping and scattered selections so this
  provides a reference for possible future batching work. Sequential reads are
  acceptable for this baseline; they are not the proposed production design.
  Do not implement parallel delineation, smart batching, custom tile reuse,
  grouped-window alternatives, or byte-range coalescing experiments.

Choose a small useful workload from actual terminal-unit bounding boxes. Record
unit identities, source of bounds, CRS, and exact pixel windows so later work can
repeat the same requests. Include useful window-size and tile-boundary variation
without building a large benchmark matrix. Derive boundaries from actual raster
transforms and pixel offsets, not historical place-name labels. Selection and
correctness work stay outside access timing and are documented separately.

Report elapsed time, actual HTTP request counts and response bytes, retained
array bytes, and process peak memory. Distinguish logical read calls from HTTP
traffic and retained arrays from process memory. Include failed requests and
preparation traffic where applicable. Record dataset identity, library versions,
reader settings, first-use/cache state, measurement method and limitations.
Missing measurements must be marked “not measured,” never estimated from storage
API counters or compressed tile sizes and presented as observed traffic.

Use modest sequential repetitions on the shared laptop. These are diagnostic
raster-access measurements, not controlled latency guarantees or complete
watershed-delineation timings. Use bounded runs and report interrupted or failed
cases rather than repeatedly running expensive cases without new information.
Local or localhost observations do not establish remote behavior; measure the
public remote access path rather than downloading the full rasters.

## Inputs and correctness

Use the explicit HFX 0.3.0 declaration at
https://basin-delineations-public.upstream.tech/grit/hfx-v0.3.0/ . It declares
`aux/d8/flow_dir.tif` and `aux/d8/flow_acc.tif`, GRASS directions, km² accumulation,
and EPSG:8857. Inspect the current manifest and headers and record their identity;
historical probes are context, not current measurements.

The [D8 raster v2 contract](https://github.com/CooperBigFoot/hfx/blob/c18a8a6a115e2045080d8752279d39db2b9b29b6/spec/aux/d8_raster/v2.md)
is the experimental input contract. HFX 0.4.0 vector data do not define a new
raster contract or authorize implicit discovery through vector provenance.

Trust the producer's dataset validity. Keep inexpensive checks needed to
interpret selected data and reject unusable grid pairs; do not scan or certify
the entire dataset during opening. Check cell values against an independent
reference outside timed access. Use focused small fixtures for grid/window
placement, boundary assembly, and validity semantics as needed; plausible value
ranges or overlapping outputs alone are not independent correctness evidence.

The pinned [Rust reference](https://github.com/CooperBigFoot/pourpoint/tree/60e3d6e383546ea6ed20e68fe4d77959c816a48c),
also at `../deprecated/pourpoint`, explains the downstream need in
`crates/core/src/refinement.rs` and `crates/core/src/algo/refine.rs`: refinement
uses the terminal polygon's native-grid bounding window, not the whole upstream
watershed. Accumulation helps select a seed; missing accumulation must not erase
otherwise usable direction cells. This experiment stops before seed selection,
tracing, or polygonization.

Its `docs/releases/tile-count-independent-planetary-cog-reads.md` records why
small outputs can still have expensive tile-index access. Historical rasters
were approximately 50.7 GB and 205.1 GB, with about two million 512×512 tiles each.
Those observations do not prove that a modern existing-library reader needs
replacement. Historical internal storage-call counts were not HTTP measurements;
Zurich crossed a column boundary and Norway remained within one tile.

## Execution and delivery

A fresh implementation agent works in a dedicated worktree below this
repository's `.worktrees/`, implements only the experimental code needed, runs
the experiment, and returns the results. Keep it separate from production under
`experiments/`, following the repository's uv, strong typing, Ruff, ty, and
test-first conventions. Leave reversible harness and instrumentation choices to
the agent. Do not build a storage engine, production API, or general cache
framework.

Deliver a compact results table, small machine-readable measurements,
reproducible commands and settings, and independent correctness evidence. State
measurement gaps and limits plainly. The deliverable is the experiment and its
numbers, not recommendations about sufficiency or improvements.

No raster generation, reprojection, HFX redesign, production engine changes,
remote writes, or full raster downloads. Preserve original data and attribution;
keep bulk inputs and sensitive logs outside Git. Do not resume #4's vision or
implementation as part of this work.
