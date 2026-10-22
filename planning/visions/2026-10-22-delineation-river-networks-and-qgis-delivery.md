# Delineation, river networks and QGIS delivery

## Outcome and authority

Implement the hydrological operations and portable QGIS delivery on the merged HFX 0.4.0 reader/runtime. Users can select upstream sub-basins for a geometry, refine a point watershed on the raster grid, select rivers, and export named single or batch results without assembling an internal pipeline.

This implementation handoff records the [backend design discussion #42](https://github.com/pourpoint-tools/pourpoint/issues/42), building on the [API design discussion #35](https://github.com/pourpoint-tools/pourpoint/issues/35), its [API vision](2026-10-20-python-api-and-qgis-workflows.md), and the [implemented reader/runtime vision](2026-10-20-hfx-reader-and-runtime-redesign.md). Issue #35 already completed API documentation; this work does not reopen it. Publishing this document does not complete #42, implement the engine or complete other Program work.

The API vision has been updated in place with the agreed public surface. This backend handoff references it rather than defining competing signatures or result types. Publication is documentation only; implementation begins through a separate implementation invocation.

## Existing foundation and boundaries

Pourpoint merge #40 (`5c40e4a`) provides `HydroFabric`, spatial bbox candidates, source feature materialization, inclusive same-domain graph traversal, river relationships/nodes, persistent original-byte caching and paired native-grid raster windows. Exact spatial selection, refinement, public workflow composition and QGIS export remain engine work. Follow [runtime access](../../docs/runtime.md) and [raster access](../../docs/rasters.md). Bbox candidates are not exact intersections. Fabric instances and ordinary raster handles are single-thread-owned; batch scheduling must respect that ownership.

HFX is the source-normalization boundary. Use the sibling HFX [spatial layout](https://github.com/pourpoint-tools/hfx/blob/main/docs/v0.4.0/spatial-layout.md), vector field meanings and raster contract. Preserve supplied IDs, attributes, Arrow types/nulls, metadata and relationship meanings. Flow, hierarchy, incidence and catchment associations are different relationships. Missing connectivity and dataset cutoffs do not establish natural outlets. Do not invent stream order or equate it with level.

The deprecated checkout `../deprecated/pourpoint` and pinned Rust baseline `60e3d6e383546ea6ed20e68fe4d77959c816a48c` are evidence, not compatibility requirements. Reuse sound inclusive graph and reverse-D8 ideas. Do not port terminal-only refinement, untouched upstream polygon retention, weighted vector snapping, threshold-based raster snapping or silent refinement fallback. Source inspection found no existing regional selection or full RiverNetwork workflow to translate.

Use existing GIS types: Shapely geometries, the runtime's CRS/affine and native-grid descriptors. Do not build a replacement geometry system or add names to geometry objects. Small result and cell-reference types are appropriate where they carry real output information.

## Behavioral contract

Implement the [Python API and QGIS workflows vision](2026-10-20-python-api-and-qgis-workflows.md) as the single authority for signatures, result shapes, target identities, batch failure behavior, rivers and export. Do not preserve superseded sketches from issue comments or duplicate a competing API here.

Key implementation constraints are whole-unit selection at the requested level, whole-footprint raster tracing under the assumed equivalence, equivalent granular/merged refinement, source-faithful results, isolation of expected per-target failures, combined-footprint river clipping and branching downstream continuation, and per-target QGIS raster pairs. Keep source-dependent operations within the fabric lifecycle. Refinement assumes equivalence; it does not run a mandatory consistency check. Scientific evaluation of that assumption remains separate work.

## Implementation evidence and delegated experiments

The behavioral contract above anchors implementation. No separate upfront experiment project is required. Implementing agents may run focused experiments to choose algorithms, masking/boundary conventions, execution backends, concurrency and memory strategies. Bring back evidence requiring a changed public contract or exposing a failed scientific premise; do not silently change requirements. Ordinary reversible engineering choices need no new user decision gate.

Use project-native uv execution, strong typing, Ruff, ty and test-first development. Establish expected results independently of the implementation, in small examples covering:

- Point/region selection at an explicit level, boundary membership, splits/reconvergence and unavailable coverage/connectivity.
- Whole-footprint reverse-D8 contribution, including upstream units that join below the requested point and must be excluded.
- Granular/merged refinement equivalence, source attributes, outlet-cell identity, valid/nodata distinctions and no coarse fallback.
- Batch identity/order, duplicates, partial failure and propagation of fatal/unexpected errors.
- Rivers crossing constituent boundaries versus leaving the combined footprint, downstream branches/reconvergence and faithful clipping/connectivity.
- Portable QGIS output, target grouping, raster masks/grid/values, outlet-cell overlays, retained issues and relative paths.

Validate read-only against the live public GRIT HFX 0.4.0 publication:

```text
https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0/
```

Resolve `manifest.json` and its external direction/accumulation artifact declarations through `HydroFabric`; do not guess raster paths. Use selected representative real targets for vector selection, refinement, rivers, batch processing and export. Record publication identity, targets, settings, cache state, elapsed time, requests/bytes and memory with their measurement limits. Include first use, same-context reuse and reopened persistent-cache/offline replay where relevant. Measure complete operations, not only pre-known range downloads; do not require worldwide downloads or audits. Do not mutate the publication. GRIT attribution and CC BY-NC 4.0 terms remain applicable to data and exports.

Live execution complements synthetic correctness tests; it does not prove equivalence globally. The URL is established in repository delivery evidence, not newly availability-verified by this design discussion. If live access is unavailable, report the precise blocker and do not claim live validation passed.

Completion means implemented operations with the agreed behavior, focused correctness evidence, representative live validation and inspectable portable QGIS deliverables. No universal performance threshold is promised. Source normalization, new snapping scores, automatic consistency certification, routing/impact simulation and legacy calling-syntax compatibility are excluded.
