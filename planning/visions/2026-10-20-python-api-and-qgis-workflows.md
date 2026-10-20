# Python API and QGIS workflows

## Purpose and authority

Document the agreed public experience so [reader/runtime design #36](https://github.com/pourpoint-tools/pourpoint/issues/36) has an API anchor. This is the design outcome of [#35](https://github.com/pourpoint-tools/pourpoint/issues/35), within [Program #2](https://github.com/pourpoint-tools/pourpoint/issues/2). It captures the requested API-design outcome, not an implemented engine; publication leaves the issue open for separate completion handling.

**This vision authorizes documentation only. It does not authorize implementing the functions, readers, runtime types, exporters, or a validation utility described below.** Implementation requires separately approved work. The signatures illustrate confirmed semantics; unresolved defaults, concrete storage types and execution mechanisms are not implementation requirements.

Keep the useful simplicity of deprecated Pourpoint, not its calling syntax or seven mandatory processing stages. Useful intermediate data should be available without requiring callers to assemble internal pipeline objects. Use functions with verbs, typed data, eager execution and explicit composition. A convenience wrapper serves users who do not need fine-grained control.

## Input boundary and terminology

`HydroFabric` represents opened HFX data: sub-basins, drainage connectivity and river information. It legitimately owns reusable state. HFX is the source-normalization boundary; Pourpoint exposes supplied identities, typed attributes, nulls, meanings and applicable metadata rather than inventing a parallel schema or missing values.

The delivered HFX 0.4.0 [spatial layout](https://github.com/pourpoint-tools/hfx/blob/main/docs/v0.4.0/spatial-layout.md) is authoritative for physical access; [vector-data.md](https://github.com/pourpoint-tools/hfx/blob/main/docs/v0.4.0/vector-data.md) supplies field meanings. The [final delivery record](https://github.com/pourpoint-tools/hfx/blob/main/docs/v0.4.0/grit-final-delivery.md) records publication. Existing IPC-reader/runtime code in this repository is not proof of compatibility with that layout. Readers and runtime representations for it remain separate design work.

`target` consistently identifies the spatial geometry or collection being analysed. Point and regional upstream selection are one operation. A multi-part geometry is one target; a sequence of geometries represents independent targets. Collections of sub-basins instead represent the parts of one footprint, with nested collections representing independent groups where batch operations accept them.

Types below are conceptual:

- `Subbasin`: a whole source drainage unit, with ID, Polygon or MultiPolygon geometry and available HFX attributes.
- `SubbasinContribution`: a non-empty contributing portion tied to its source sub-basin. It can equal the whole unit. Retain source identity and attributes without pretending source quantities were recomputed for a clipped piece.
- `RiverNetwork`: individual river features and their directed relationships, with source nodes where available. `.geometry` exposes their linework for geometric operations without replacing the feature/relationship representation.
- `Geometry`, `Point`, raster types and result fields describe roles, not finalized classes or dependencies.

No dedicated `SubbasinSelection` wrapper. Supplied nonstandard attributes on sub-basins and individual river features use `.attributes["field_name"]`, preserving typed values and nulls. Dataclasses versus other representations and per-feature objects versus columnar storage remain implementation choices. Do not allocate elaborate classes merely to match these sketches.

The subsequent [reader/runtime vision](2026-10-20-hfx-reader-and-runtime-redesign.md) settles one context-managed lifecycle: `with HydroFabric(source, cache_dir=...) as fabric:`. Perform source-dependent operations, including raster-backed export, inside the context. Materialized vector results and persistent cached data survive closure. It also supersedes the original explicit-raster-input requirement below with fabric-backed refinement.

## Explicit operations

### Whole upstream sub-basins

```python
@overload
def select_upstream_subbasins(
    fabric: HydroFabric,
    target: Geometry,
) -> list[Subbasin]: ...

@overload
def select_upstream_subbasins(
    fabric: HydroFabric,
    target: Sequence[Geometry],
    *,
    n_jobs: int = -1,
) -> list[list[Subbasin]]: ...
```

For a point, use its coordinates to determine the containing terminal unit, then include that whole unit and its upstream units. Do not substitute a snapped river's catchment association for point containment. Boundary resolution is engineering work. This is a whole-unit representation, not a claim that all selected land drains to the exact internal point.

For regions, use the agreed [upstream contributing area definition](../../docs/upstream-contributing-area.md): land draining to any part of the geometry, including its boundary, represented by whole sub-basins. Do not snap country polygons or silently raster-refine them.

Batch results preserve input order. `n_jobs=-1` requests available CPU capacity, `1` sequential execution, and positive values bound workers; the executor/backend is not chosen. Avoid deferred request objects, `compute()` and a special comprehension framework. Thread safety and actual speedups must be established during implementation, not assumed from this signature.

### Stream snapping

```python
def snap(
    target: Point,
    *,
    onto: Geometry,
    radius: float,
) -> Point: ...
```

Return the nearest point on the supplied stream geometry within the radius, using ground distance. Fail if no stream is in range, including empty input. The radius unit is metres; exact naming/default policy remains to be finalized. `snap` returns a Point, not `SnappedOutlet`. It does not read an accumulation raster, infer hydrological weights or silently switch methods.

```python
snapped = snap(target=station, onto=network.geometry, radius=500)
subbasins = select_upstream_subbasins(fabric, target=snapped)
```

The number is illustrative, not a default. `onto` need not come from HFX: the user supplies stream geometry. No historical named snap-set requirement. Snapping is optional when a caller already has the intended location; refinement does not require a preceding call to it.

### Refinement: whole-footprint raster delineation

Refinement is an explicit, computationally expensive alternative to whole-unit delineation. Pass `HydroFabric`; the backend resolves its declared direction/accumulation pair and reads needed native-grid windows. This reader/runtime decision supersedes the original requirement to pass both raster inputs separately. Missing rasters or inadequate coverage fail clearly; a custom raster override is not required.

```python
@overload
def refine(
    fabric: HydroFabric,
    subbasins: Sequence[Subbasin],
    *,
    target: Point,
    window_size: int,
) -> list[SubbasinContribution]: ...

@overload
def refine(
    fabric: HydroFabric,
    footprint: Polygon | MultiPolygon,
    *,
    target: Point,
    window_size: int,
) -> Polygon | MultiPolygon: ...
```

These names/types illustrate the accepted inputs and outputs; the window parameter spelling and defaults are not settled. Refinement:

1. Locates the cell containing the supplied point.
2. Chooses the highest valid accumulation cell in a local window restricted to the footprint.
3. Traces incoming D8 connections across the entire footprint.
4. Constructs either contributing pieces or their merged geometry.

Outlet-cell selection is private algorithm work, not another public primitive or a second snapping API. Do not branch on whether the input point came from vector snapping. Use local maximum accumulation initially, not a new weighted score or a basin-dependent accumulation threshold. Window size, ties, valid-cell conventions and chosen-cell provenance need concrete definitions later. The window adjusts local raster alignment; it does not guarantee identification of the intended river near a confluence. No silent fallback to coarse polygons if explicitly requested refinement fails.

With granular input, omit empty contributions. Do not independently trace within each sub-basin: flow can cross their boundaries. Preserve the equivalence:

```text
union(refined contributions) ≡ refine(union(sub-basins))
```

#### Scientific assumption

Let q be the terminal unit's declared outlet, p the requested point, M the union of the terminal unit and all upstream units, and W_R(x) the watershed under the supplied raster flow directions. Refinement assumes:

```math
M \equiv W_R(q)
```

For a point whose raster drainage reaches q:

```math
W_R(p) \subseteq W_R(q) \equiv M
```

**The upstream vector union represents the raster watershed at the terminal unit's outlet. Refinement delineates the requested point within that enclosing footprint.** Equivalence means drainage coverage at raster resolution, not necessarily identical simplified vector coordinates. Producer consistency, outlet-cell matching and boundary conventions matter; common packaging alone does not establish the assumption.

More explicitly, let G_M be cells admitted by the footprint mask, c_out the selected cell, and F the D8 downstream-neighbour function. Retain cells c for which some k >= 0 satisfies F^k(c) = c_out and every intermediate cell remains in G_M. Polygonize their footprints. Do not then add untouched upstream polygons. At q, under the consistency assumption, raster delineation recovers the whole mask. The mask is an enclosing computational domain, not just the terminal polygon.

This replaces historical terminal-only refinement, which could retain units entering below the requested point. Refinement may exclude whole selected units or return partial contributions; do not assume stronger source-construction properties without evidence. Raster/vector network consistency is an input assumption, not a guarantee that every vector-intersected pixel is a drainage cell.

The optional utility to check this assumption for user-selected cases is deferred to [#38](https://github.com/pourpoint-tools/pourpoint/issues/38). It is not a mandatory refinement gate, worldwide audit or validator framework.

[Lindsay, Rothwell & Davies (2008)](https://doi.org/10.1029/2007WR006507), sections 3–5, compares maximum accumulation, nearest threshold-qualified cells and AORA. Maximum accumulation is established practice but can move outlets across confluences; AORA still requires thresholds and station metadata. This vision adopts one known local rule, not a claim of universal scientific optimality. See [full-paper discussion](https://github.com/pourpoint-tools/pourpoint/issues/35#issuecomment-6277313122).

### Merge

```python
@overload
def merge(
    subbasins: Sequence[Subbasin],
) -> Polygon | MultiPolygon: ...

@overload
def merge(
    subbasins: Sequence[Sequence[Subbasin]],
    *,
    n_jobs: int = -1,
) -> list[Polygon | MultiPolygon]: ...
```

Also accept corresponding contribution collections. A public merge function is justified as a meaningful workflow step even if it wraps `union_all`. Each batch group becomes one geometry; independent groups are never merged together. Empty-input semantics and exact overload declarations remain to be designed.

### Rivers

```python
BasinPart = Subbasin | SubbasinContribution


def select_rivers(
    fabric: HydroFabric,
    target: BasinPart | Sequence[BasinPart],
    *,
    clip: bool = False,
    include_downstream: bool = False,
) -> RiverNetwork: ...
```

Treat the supplied parts as one footprint and select river features for it; an actual polygon union is not mandated internally. Default to whole source geometries. Clipping is explicit and must not invent flow connections or natural outlets. Detailed intersection/boundary rules and independent batch overloads remain engineering design work.

`include_downstream=True` returns the network within the basin AND every supplied downstream continuation from rivers leaving it, through bifurcations and reconvergence, as far as HFX provides. Deduplicate reconvergent features. Follow flow edges, not all incidence inferred from shared nodes. Dataset limits are not necessarily natural outlets; preserve source distinctions where available. A single selected mainstem is not the requested behavior.

The intended clipping interaction is to clip the basin portion while retaining an explicitly requested downstream extension outside it; precise feature identity and boundary handling remain to be specified. HFX supplies nullable single-outlet catchment coordinates and can represent multiple/no outlets through source data; handling those cases is not a requirement for a user-supplied arbitrary representative outlet.

River names, stream order and all other attributes come from HFX. Do not equate level/tier with stream order or infer missing order. Live public GRIT Parquet schema inspection confirmed nullable `source_strahler_order: int64`, plus source mainstem, width, drainage-area and name fields. This verified column availability, not global non-null completeness. Preserve other fields even if the current use case is only styling line thickness.

## Convenience workflow

`delineate` wraps the operations for users who want a simple call. It is not an alternative hydrological algorithm. Preserve simplicity, deliberately not legacy `Engine.delineate(lat=..., lon=...)` compatibility. Use explicit typed keyword arguments rather than an opaque kwargs bag.

```python
result = delineate(
    fabric,
    target=station,
    snap_onto=network.geometry,
    refine=True,
    merge=True,
    # Snap radius and raster-window options will be explicit;
    # their wrapper names/defaults remain to be specified.
)

rivers = select_rivers(
    fabric,
    target=result.parts,
    include_downstream=True,
)
```

`DelineationResult.parts` is a list of whole sub-basins without refinement, or non-empty contributions with refinement. A merged geometry is available when merge is requested; do not force it when merge is false. Retain useful source/provenance information without forcing callers to understand every field. Exact field layout, original-selection retention, raster descriptors and batch result signatures remain runtime/API elaboration work. Do not silently apply point snapping/refinement to regional targets.

## QGIS-ready delivery

The objective is a portable deliverable that opens in QGIS with its layers configured, not merely exported geometry. Use a folder containing a `.qgz` project, GeoPackage vector/relationship tables and GeoTIFF rasters, with relative references. A custom `.pourpoint_bundle` extension would not be natively understood by QGIS and is not required.

```python
(
    QGISBundle()
    .add_result(result)
    .add_vector("rivers", rivers)
    .write("watershed/")
)
```

`QGISBundle` is a proposed Python builder, not a new on-disk standard. Support both chaining and incremental construction. Explicit `add_vector(name, data)` and `add_raster(name, data)` let users choose custom content. Do not perform hidden delineation or river tracing during export.

`add_result` is the complete-result convenience: add available merged geometry, individual parts and applicable outlet/provenance layers; include direction and accumulation subsets used when refinement ran. No separate include-rasters switch is needed for this convenience path. Customization uses individual result fields and explicit additions. `refine=True` does not itself write files. Raster subset descriptors/ownership remain runtime work; callers should not need to reconstruct raster selections to export a completed result. Document export cost and preserve numerical values, grid/CRS and nodata, not rendered pictures. Exact footprint/window masking and storage/lifetime must be specified in reader/export design.

Example deliverable:

```text
watershed/
  watershed.qgz
  vectors.gpkg
  flow_direction.tif
  flow_accumulation.tif
```

River export preserves individual features, supplied typed attributes/nulls and relationships. A companion directed connection table preserves upstream/downstream IDs instead of flattening branching connectivity into text. Other available HFX information must not be discarded because plotting has no immediate need for it. Work out faithful encoding where GeoPackage cannot directly represent Arrow values; do not silently coerce away information. QGIS styling may consume supplied stream order for line widths, but styling does not define the retained schema. Exact mapping of metadata/relationships, layer names, styling and potential standalone GeoPackage-builder support are not finalized here.

## Handoff and evidence of success

For this documentation-only outcome, success is this coherent, published API anchor linked from #35 and available to #36. It describes the intended behavior without pretending the readers, types or functions exist. It does not revise the Program map or implement river workflow #34 independently.

Subsequent design should resolve concrete representation within the reader/runtime vision’s lifetime contract, signatures/defaults, error details, accumulation-cell ties/window conventions, batch corner cases, export encoding and project styling against this intent. These are not an invitation to reopen functional composition, public/private cell selection, whole-footprint refinement, HFX authority, or ordinary collection results. Material changes require discussion rather than silent compatibility machinery.

Implementation, tests of future engine behavior, source normalization, new ranking formulas, alternative refinement algorithms, routing/impact simulation, and automatic adoption of the possible equality checker are excluded from this publication.
