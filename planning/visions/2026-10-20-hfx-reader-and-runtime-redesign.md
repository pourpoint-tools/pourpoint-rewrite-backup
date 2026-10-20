# HFX reader and runtime redesign

## Outcome and scope

Redesign Pourpoint’s complete HFX reader and runtime representation for catchments/sub-basins, rivers, source nodes and rasters. Make the finalized HFX 0.4.0 publication useful for fast single-point and bulk watershed delineation, without consumer-side rebuilding of indexes that HFX already supplies.

This is the implementation handoff for [#36](https://github.com/pourpoint-tools/pourpoint/issues/36), within [Program #2](https://github.com/pourpoint-tools/pourpoint/issues/2). Publishing this vision does not complete #36. Completion requires the reader/runtime implementation and verification described here. Treat existing code as a draft to redesign, not an architecture to preserve through river-only additions. Reuse suitable pieces where justified.

The [Python API and QGIS workflow vision](2026-10-20-python-api-and-qgis-workflows.md) anchors user-facing semantics. This vision settles lifecycle, attribute access, caching intent and fabric-backed raster inputs. It does not authorize implementing the complete delineation/refinement algorithms, convenience workflow or QGIS exporter merely because their future use informs the runtime. The optional user-selected vector/raster consistency utility is deferred to [#38](https://github.com/pourpoint-tools/pourpoint/issues/38), not a startup requirement or worldwide audit.

## Authoritative input and existing evidence

Use HFX’s [spatial layout](https://github.com/pourpoint-tools/hfx/blob/main/docs/v0.4.0/spatial-layout.md) for physical access, [vector contract](https://github.com/pourpoint-tools/hfx/blob/main/docs/v0.4.0/vector-data.md) for field meanings, and [raster contract](https://github.com/pourpoint-tools/hfx/blob/main/docs/v0.4.0/raster-data.md). Source normalization remains HFX’s responsibility. Do not invent a parallel schema or reopen storage-format selection.

The final [public GRIT manifest](https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0/manifest.json) was fetched during this discussion and matched SHA-256 `04bbfb50907feaf339808730f85c39ee2cbeaed2c6217e9d546cd445b3bc897f`. It declares `representation: spatial-geoparquet-adjacency`, representation version 1, separate catchment/river/node GeoParquet families, artifact-bound ID lookups and directories, immediate relationship tables, and external direction/accumulation rasters. This live check verified the manifest, not every artifact. Full producer verification and measurement limits are recorded in the [delivery evidence](https://github.com/pourpoint-tools/hfx/blob/main/docs/v0.4.0/grit-final-delivery.md).

The shared `0.4.0` version does not imply compatibility with the draft IPC reader. Select the actual representation explicitly. HFX supplies spatial organization, ID addressing and immediate relationships; Pourpoint owns traversal and query workspace. Preserve the distinctions between flow, hierarchy, incidence, cross-tier node correspondence and catchment associations. Missing or not-supplied connectivity is not evidence of a natural outlet.

The existing native-grid raster, integrity, lookup and geometry code provides useful evidence, not a compatibility guarantee. Support the final manifest’s external raster identities. Keep direction data on its native grid; resolve geometry CRS and raster CRS correctly without warping direction pointers.

## Python experience and lifetime

Use one context-managed lifecycle:

```python
with HydroFabric(hfx_url, cache_dir="./hfx-cache") as fabric:
    subbasins = select_upstream_subbasins(fabric, target=station)

    contributions = refine(
        fabric,
        subbasins,
        target=station,
        window_size=window_size,
    )

    watershed = merge(contributions)
    rivers = select_rivers(fabric, target=contributions)
```

This illustrates the future workflow, not a claim that these engine functions are included in this effort. `HydroFabric` accepts a local path or supported remote source. Opening connects to a dataset; it must not require downloading the worldwide dataset or rebuilding its global indexes. Resource acquisition can happen as needed.

Operations remain eager. Keep ordinary collections and useful typed intermediate values as agreed in the API vision; no deferred objects, computational graph across script lines, mandatory preparation stage or separate single/bulk storage mode. Plan efficient access within calls and batches and reuse state across calls.

`refine` takes the fabric plus the sub-basins or merged footprint and resolves the supplied raster pair internally. This deliberately supersedes the earlier requirement to pass direction and accumulation separately, for both standalone refinement and `delineate`. Missing rasters, invalid data or inadequate coverage are errors, not permission to return coarse geometry silently. A custom raster-input override is not required by this vision.

Perform source-dependent operations within the context, including refinement and export that reads raster subsets. Closing releases readers and connections but preserves the disk cache. Materialized vector results remain usable after closure; they do not depend on live readers. Runtime objects/descriptors must allow the future exporter to access the raster subsets used without callers reconstructing their selections. Detailed raster descriptor storage remains implementation work.

## Faithful, inspectable results

Whole sub-basins, contributing pieces, river features and source nodes retain their distinct meanings and identities. A contribution retains source identity and attributes without pretending source quantities were recomputed for a clipped geometry. `merge` returns a Polygon or MultiPolygon, not a watershed wrapper; the concrete geometry dependency remains an implementation choice.

Core feature concepts such as `.id` and `.geometry` are directly accessible. Supplied nonstandard fields on sub-basins and individual river features use:

```python
subbasin.attributes["source_area"]
river.attributes["source_strahler_order"]  # When supplied by this dataset.
```

Attributes must be discoverable, and retain supplied types, nulls and meanings. This syntax does not mandate allocating a dictionary for every record or flattening rich Arrow types into lossy Python values. Preserve applicable field/schema metadata and provenance. Do not standardize source fields, invent missing stream order or equate order with tier.

`RiverNetwork` retains individual features and directed relationships, with source nodes where supplied; `.geometry` exposes linework without replacing that information. Runtime conversion must not lose HFX information because it is not needed for plotting. Preserve exact source WKB when reading unchanged features. Explicit clipping/refinement is distinct from reading and must retain the source distinction.

Selective reading is not permission to discard information: the reader must expose all supplied fields and relationships faithfully, while avoiding loading the whole world. Define selected-network boundary references without inventing connections or silently classifying omitted neighbors as natural terminals. The API vision’s faithful QGIS export requirements remain in force; implementation of that exporter is separate.

## One accumulating persistent cache

Caching is enabled by default (`cache=True`), with configurable `cache_dir` and a documented platform cache location when omitted. `cache=False` disables persistent caching; ordinary bounded in-memory reuse is separate.

Maintain one growing cache per dataset identity, not independent copies for each query. Retain original artifact identities and addresses, track acquired content, reuse overlap, and fill missing portions on later reads. Do not rewrite feature tables, rebuild published indexes or compile per-watershed HFX bundles. It is logically growing available coverage of the original publication, not necessarily a standalone subset bundle.

Persist the metadata, indexes/relationships, vector content and raster content needed to repeat completed workloads. Closing does not delete them. There is no automatic disk eviction; users control the retained cache directory. In-memory state can remain bounded. A completed workload must be repeatable offline against the same dataset snapshot when its required cache is intact, without re-downloading cached payloads. Missing content must produce a clear failure when no network is available, not partial results or substitution of another publication.

Bind cache content to immutable artifact identity and a consistent manifest snapshot. Growth must not mix versions. If all artifact bytes are eventually requested, the cache can contain the complete publication; delineating everywhere does not necessarily request unused columns or provenance. Do not claim every partial cache is a complete independently openable HFX bundle.

Range/block representation, overlap deduplication, atomic persistence and connection reuse are implementation design. An explicit offline switch is not a settled API requirement; choose and document a clear mechanism for using the cached snapshot without network access, proving the offline promise. Do not add a separate protected-workload/pinning product or dataset compiler.

## Efficient access and evidence of success

Speed and avoiding inefficient queries are primary requirements for both single-point and bulk workloads. Start from the [#36 comments](https://github.com/pourpoint-tools/pourpoint/issues/36) and linked HFX experiments, rather than repeating their format investigation:

- Project columns before acquisition/decompression where the format permits; avoid WKB when it is not needed.
- Prune spatial candidates through conservative Parquet statistics, then record bounds/tier; bbox overlap is not exact geometry intersection. Retain groups with missing statistics.
- Group shared lookup, relationship and feature reads. Reuse addresses found during spatial discovery rather than unnecessarily resolving the same IDs again.
- Retain useful reader metadata and reuse adjacency during traversal. Batch work should exploit shared access instead of blindly repeating independent reads.
- Investigate concurrent exact-range acquisition using fsspec 2026.9.0 and aiohttp 3.14.5 as the recorded starting point, not a mandated dependency combination. Concurrency 10 and no coalescing were experimental settings, not optimal constants. Preserve conditional identity, range, encoding and length checks with any transport.

The approximately 124-second concurrent replay knew its ranges in advance and excluded lookup, decoding and assembly. It is not an end-to-end reader benchmark. Decide selective versus larger preparation, transport libraries and memory policies from complete workload measurements. No mandatory global preparation or private duplicate feature format is justified by these experiments.

Verify small independently established cases and representative public GRIT access for all domains, spatial/ID selection, directed relationships and native raster windows. Establish source-value/type/null/metadata/WKB fidelity, missing-ID behavior, identity failures and conservative candidate membership. Test splits and reconvergence without confusing incidence or hierarchy with flow. Test persistent cache growth, overlapping reuse, closure lifetime and completed-workload replay with network access disabled.

Measure single-target and bulk access sequences needed by the API, including first use, same-context reuse and reopening the persistent cache. Include opening, lookup, traversal where supplied by this foundation, acquisition, decoding and assembly; do not require completion of the excluded engine algorithms to run these sequences. Report requests, transferred bytes, elapsed time and memory with their scope and limitations. Distinguish cache state and logical buffer sizes from process RSS; streaming output alone does not bound prefetch memory. Confirm useful end-to-end gains without claiming a universal latency threshold or globally optimal setting.

Follow repository test-first guidance, uv execution, strong typing, Ruff and ty. Internal classes, columnar storage, traversal structures and scheduling are delegated design, with focused experiments during implementation. Bring back material changes to the agreed user experience rather than silently altering it. No separate experimentation round is required before beginning this approved reader/runtime work.
