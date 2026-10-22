# Python API and QGIS workflows

## Purpose and authority

This is the agreed public API for Pourpoint's hydrological operations and portable QGIS delivery. It originated in [API design discussion #35](https://github.com/pourpoint-tools/pourpoint/issues/35), completed as documentation in #37, and was revised through the subsequent backend-design discussion on 2026-10-22. Updating this document does not reopen #35 or implement the engine.

The [backend implementation vision](2026-10-22-delineation-river-networks-and-qgis-delivery.md) owns implementation constraints and evidence of completion. The [reader/runtime vision](2026-10-20-hfx-reader-and-runtime-redesign.md) and [runtime documentation](../../docs/runtime.md) govern the implemented HFX foundation and lifetime. This API document is the single public-surface authority; examples describe intended behavior, not existing engine functions or binding internal class layouts.

Use functional operations, eager execution, ordinary collections and typed results. `HydroFabric` owns reusable source state within `with HydroFabric(source, cache_dir=...) as fabric:`. Perform source-dependent operations, including raster-backed export, inside that context. Materialized vector outputs survive closure. Do not introduce deferred requests, `compute()` or mandatory preparation stages.

HFX is the source-normalization boundary. Preserve supplied IDs, typed attributes/nulls, metadata and relationship meanings. Sub-basins and individual river features expose nonstandard fields through `.attributes["field_name"]`; do not recompute source quantities for clipped pieces or invent missing stream order. Use Shapely geometries and the existing native-grid/CRS/affine types rather than a new geometry system. Targets carry names separately from their geometry.

This revision supersedes earlier sketches on keyword-only operation arguments, explicit level selection, named batches and partial failures, refinement result containers, default river clipping and per-target QGIS organization. Builder methods retain the concise positional layer-name/data syntax shown below.

## Whole upstream selection and snapping

The [mathematical definition](../../docs/upstream-contributing-area.md) is authoritative: U(R) is land draining to any part of target R, including its boundary. A point watershed is its singleton case; zero-length drainage paths include land within a region. Sharing a river system farther downstream is insufficient.

`select_upstream_subbasins(*, fabric, target, level=1, ...)` returns whole sub-basins approximating this area. For a point, identify its containing sub-basin by coordinates, then include its upstream closure. Never substitute a snapped river's catchment association. For a region, select intersecting units, including boundary intersections, and their upstream closure at the selected level. Whole-unit representation limits spatial precision; it is not exact-point raster delineation. Resolve point boundary ownership deterministically as engineering work.

Users choose HFX resolution via `level`; default to **1**, not automatically the finest available level. Do not mix nested levels or silently fall back to a different level. Missing coverage and explicitly unsupplied upstream connectivity must not masquerade as complete selection. Make failures clear and retain them per target in batches. Distinguish valid empty selections from inability to establish an answer. River levels are independent of catchment levels; expose independent river selection at `level=1` by default rather than infer numeric tier correspondence.

`snap(*, target, onto, radius)` remains an optional nearest-ground-distance operation on supplied stream geometry, with radius in metres. It returns a Point and fails when no stream is in range. No weighted ranking, raster overload, implicit network or fallback. Radius naming/default and detailed distance implementation are delegated. Do not apply snapping or point refinement implicitly to regional targets.

## Refinement under the equivalence assumption

When `refine` is called, **assume equivalence holds**. Let M be the upstream vector union and q the terminal unit's outlet. Assume M is equivalent to the raster watershed W_R(q) at raster resolution; for a requested point p draining to q, W_R(p) is contained in M. This is an input scientific assumption, not a property certified by HFX packaging or successful execution. Its further scientific investigation and the optional consistency utility in #38 are separate work; no mandatory preflight equality check or worldwide audit.

Refinement uses the whole vector delineation as a computational mask:

1. Locate the raster cell containing the supplied point.
2. Select the highest valid accumulation cell in a local window restricted to the mask.
3. Trace incoming D8 flow throughout the entire mask, retaining cells whose path reaches the outlet cell without leaving that mask.
4. Polygonize the contribution, preserving either granular or merged output shape.

Use an overridable **3 × 3 native-cell window** as the initial practical default (`window_size=3`), not a scientifically established universal optimum. Positive odd sizes express a centered window; size 1 permits no move beyond the containing cell. Resolve ties, valid-cell rules and mask admission explicitly during engineering. Accumulation validity for outlet selection must not unnecessarily discard direction-valid upstream cells. Respect native-grid directions, nodata and signed terminal semantics; do not warp direction pointers.

This is raster outlet adjustment even when preceded by vector snapping. Preserve the selected cell so users can see what was used. A larger window can switch branches near a confluence; do not claim the default universally identifies the intended river.

### Keyword-only calls and output shape

```python
subbasins = select_upstream_subbasins(
    fabric=fabric, target=station, level=1,
)
refined = refine(
    fabric=fabric, subbasins=subbasins, target=station,
)
refined.subbasins    # list[SubbasinContribution]
refined.outlet_cell
refined.rasters.direction
refined.rasters.accumulation
geometry = merge(subbasins=refined.subbasins)
```

```python
refined = refine(
    fabric=fabric,
    footprint=merge(subbasins=subbasins),
    target=station,
    window_size=5,
)
refined.footprint    # one Shapely Polygon or MultiPolygon
refined.outlet_cell
refined.rasters
```

`subbasins=` and `footprint=` are mutually exclusive. Sub-basins in means contributing sub-basins out; one footprint in means one refined footprint out. Use typed result variants as needed, not a proliferation of nullable unrelated fields. A merged contribution is geometry, not a fabricated source sub-basin. Retain source identity/attributes on granular contributions without pretending source quantities were recomputed for clipped pieces; omit empty pieces.

Both forms trace across the same complete combined mask. Preserve:

```text
merge(granular_result.subbasins) ≡ footprint_result.footprint
```

Do not independently trace within each source polygon, append untouched upstream polygons, or introduce geometry repair/hole filling that silently changes drainage coverage. Define pixel boundary and contribution attribution conventions that preserve this equivalence.

`outlet_cell` identifies a source-grid row and column, not indices relative to a read window. Reuse the grid descriptor, including CRS, affine and grid identity. Expose ordinary Shapely `.center` and `.geometry` (the cell polygon) in the native grid CRS. Derive them from the cell/grid rather than store inconsistent copies. The center is a display location, not a more accurate station coordinate.

Raster subset descriptors retain the direction/accumulation coverage used; users need not reconstruct windows for export. Source-dependent export occurs inside the fabric context. Materialized vector results survive closure. Missing rasters, inadequate coverage and failed explicit refinement never silently return coarse geometry.

`merge(*, subbasins=...)` accepts whole units or contributions and returns Polygon/MultiPolygon. Nested groups produce separate merged geometries, not one union across targets. Empty-input dispatch and concrete overloads are engineering work.

## Convenience result and batches

`delineate` composes the explicit operations; it is not a different algorithm. Use keyword-only typed arguments including `fabric`, `target`, `level=1`, optional `snap_onto`, `refine`, `merge`, and explicit snap/window controls. Share the refinement window default. Retain source/provenance information and original versus adjusted point locations without requiring users to reconstruct them.

A single `DelineationResult` exposes:

- `.subbasins`: whole units without refinement, contributing portions with refinement; use this name rather than `.parts` or `.contributions`.
- `.geometry`: merged geometry when requested, otherwise None.
- `.outlet_cell`: selected raster cell when refined, otherwise None.
- `.rasters`: applicable refinement subset information, absent when no refinement occurred; concrete absence representation is engineering work.

### Target identity

A single geometry represents one target, including a multipart geometry. For batch target inputs accept:

```python
target = {"upper_station": upper_point, "lower_station": lower_point}
# or
target = [upper_point, lower_point]
```

Dictionary keys supply names; list entries receive UUID strings generated once when accepting the batch. Preserve names, order and input indices through results, issues, refinement and export. Duplicate geometries remain independent targets. Repeated list-based calls get new UUIDs; dictionary names provide stable cross-run naming. Names are target identities within a request, not source feature IDs. Engineering must preserve identity through explicit composition without requiring convenience delineation or regenerating names between stages.

Batch upstream selection and delineation follow this convention. Maintain the same per-target isolation and correspondence wherever other operations expose batch forms; do not confuse a collection of sub-basins forming one footprint with independent targets. Batch execution uses `n_jobs=-1` for available CPU capacity, `1` for sequential work and positive bounds otherwise. Establish safe scheduling and actual performance; do not assume shared-reader thread safety.

### Failure behavior

Single-target calls return a value or raise a meaningful exception. Batch calls return a small typed container with `.results` (one ordered slot per input) and `.issues`. Successful selection slots remain ordinary lists of sub-basins; do not revive a dedicated selection wrapper. A failed target has None in its slot, not an empty geometry/list. Retain its input index, name, machine-readable failure code and human-readable reason. Preserve target identity on successful slots through the batch metadata/result representation; exact storage is delegated.

For example, one station outside level-1 coverage produces a `selection.no_coverage` issue while independent stations succeed. Codes in this document illustrate the classification, not a complete taxonomy. Single exceptions and batch issues should share failure meanings. Expected target-local failures are collected; invalid shared configuration, fatal integrity/internal contract failures and unexpected programming errors raise. Do not blanket-catch arbitrary exceptions. A failed refinement makes that target fail, not return a successful coarse delineation.

Do not initially add successful-result issue collections, severity hierarchies or an `on_issue` policy switch without a concrete need. Durable batch issues are required; notification mechanics can remain small. Partial success must not depend on users noticing log output.

## Rivers as one combined footprint

```python
rivers = select_rivers(
    fabric=fabric,
    target=refined.subbasins,
    level=1,
    include_downstream=True,
)
```

Accept a sub-basin/contribution or collection as the parts of one footprint. Internal sub-basin boundaries have no effect. An actual polygon union is not mandated internally.

By default return river portions inside that combined footprint; remove the previous `clip` option. `include_downstream=True` adds every supplied outgoing continuation beyond its combined boundary, through bifurcations and reconvergence, deduplicating source features. Do not extend separately at each constituent sub-basin boundary, select only a mainstem, or infer flow from shared-node incidence. Handle a boundary falling inside a river feature without creating gaps; direction/orientation and geometry ambiguities require engineering investigation, not invented flow.

`RiverNetwork` retains individual feature identities, typed attributes, supplied directed relationships and available nodes. `.geometry` exposes linework without replacing that representation. Clipped pieces retain source identity/provenance; their new endpoints are not natural outlets. Dataset limits remain distinct from source-described natural termination where supplied.

## Portable QGIS delivery

Keep a portable folder containing a `.qgz` project, GeoPackage vector/relationship tables and GeoTIFF rasters, using relative references. Do not introduce a custom bundle extension. `QGISBundle` supports chaining and incremental construction, explicit vector/raster additions and one consistent `add_result` method:

```python
QGISBundle().add_result(result).write("watershed/")
QGISBundle().add_result(refined).write("watershed/")
QGISBundle().add_result(batch).write("watersheds/")
```

Accept standalone refinement results as well as convenience delineation results and their batches. Do not require callers to pass `batch.results`, losing identity/issues. Add available outputs only; do not silently merge, delineate or trace rivers during export.

Explicit composition remains practical:

```python
cell = refined.outlet_cell
(
    QGISBundle()
    .add_vector("subbasins", refined.subbasins)
    .add_vector("watershed", geometry)
    .add_vector("outlet_cell", cell.geometry, crs=cell.grid.crs)
    .add_raster("flow_direction", refined.rasters.direction)
    .add_raster("flow_accumulation", refined.rasters.accumulation)
    .write("watershed/")
)
```

An outlet cell belongs to the raster; its polygon is an explicit vector representation for highlighting it. Give raw vector geometry unambiguous CRS treatment; never assume a native raster cell polygon is WGS84. Highlight outlet cells with an outline over the rasters. `add_result` performs that conversion using the retained CRS.

### Per-target organization

For batches use named QGIS groups, for example `upper_station` and `lower_station`, each containing its available watershed, sub-basins, outlet cell, direction and accumulation layers. Export **one direction/accumulation raster pair per refined target**, not per contributing sub-basin and not a shared cross-target mosaic. Overlapping targets may duplicate exported pixels; persistent source reads can still reuse cached bytes. Resolve safe filenames/layer names without losing original target names.

Export both rasters over the **whole vector delineation mask**, not only the final contributing watershed and not unrelated corners of the rectangular read window. Outside-mask cells become nodata. Preserve numerical values within the mask, native CRS/grid and source nodata semantics. Retain final watershed and outlet-cell overlays so users can inspect what refinement excluded. GeoTIFF arrays are still rectangular; nodata expresses the mask.

Preserve failed-target issues in an export table with target names/indices, codes and reasons. Do not fabricate empty watersheds or outlet cells for failures. Preserve target identity on exported features separately from source IDs.

River export keeps individual features and directed connections in companion tables, with supplied attributes/nulls and applicable metadata. Faithful encoding of rich Arrow values in GeoPackage is engineering work; do not silently stringify or discard information. Styling may use supplied stream order but must not invent it or constrain retained fields. Export cost and source-dependent lifetime must be clear.
