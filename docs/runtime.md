# Runtime access

`HydroFabric` reads the final HFX 0.4.0 `spatial-geoparquet-adjacency`
representation (version 1), including catchments, rivers, source nodes and native
rasters. Opening reads only the manifest, not worldwide indexes or feature tables.

```python
from pourpoint import HydroFabric

url = "https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0/"
with HydroFabric(url, cache_dir="./hfx-cache") as fabric:
    ids = fabric.traverse("catchments", [1284709])
    basins = fabric.subbasins(ids)
    print(basins[0].id, basins[0].attributes.keys())
    rivers = fabric.river_network([1])
    linework = rivers.geometry
```

These are source-reader operations. See [hydrological operations](operations.md)
for upstream selection, delineation, refinement, snapping and portable export.

## Selected records and relationships

- `records(domain, ids, columns=None)` returns a materialized Arrow table. Domains
  are `catchments`, `rivers`, and `river_nodes`. Input order and duplicates survive;
  any missing ID raises `KeyError`. Projection avoids acquiring/decompressing
  unwanted Parquet columns where the format permits.
- `candidates(domain, bounds, levels=None, columns=None)` selects conservative bbox
  overlaps in physical file order. Bounds are WGS84 west/south/east/north. Parquet
  statistics prune groups; missing statistics retain them. Record bounds and tiers
  refine candidates before full records are read using their discovered addresses.
  Bbox overlap is **not** exact geometry intersection or point containment.
- `relationships(domain, ids, columns=None)` returns all supplied immediate
  relationship columns unless projected. `manifest.relationships` retains source
  meanings, direction, target domain and artifact bindings. Flow, hierarchy,
  incidence, cross-tier node correspondence and drainage association remain distinct.
- `traverse(domain, ids, relationship="upstream_ids")` returns an inclusive,
  breadth-first same-domain closure, deduplicating splits/reconvergence and cycles.
  Missing/not-supplied connectivity and empty lists never classify natural outlets.
  Traversal keeps an estimated 8 MiB adjacency reuse cache; per-call visited state
  and returned IDs necessarily scale with the selected closure, not the world.

`subbasins(ids)` and `nodes(ids)` return ordinary lists of Arrow-backed features.
`river_network(ids)` retains selected river features, complete selected-row
relationships and supplied endpoint nodes with their relationships. Its `.geometry`
exposes linework; it does not replace individual features. `boundary_river_ids`
identifies supplied flow neighbors omitted from the selected rivers. They remain
references, not invented terminals or connections.

Each feature exposes `.id`, `.geometry`, `.wkb` and `.attributes`. WKB remains exact
source bytes; decoding geometry is explicit property access and does not rewrite
source values. `attributes["source_strahler_order"]` returns the supplied typed
Python value or `None`; no stream order is inferred. `attributes.scalar(name)` and
`attributes.schema` expose original Arrow types, field/schema metadata and rich
values without lossy coercion. Full `.table` retains all fields, including WKB.
Source provenance remains discoverable in `fabric.manifest.declarations`.

## Lifetime and persistent cache

Use one context manager per sequential workload. Calls are eager; combine IDs to
share lookup, relationship and feature reads. Instances are not a concurrent-query
service. Materialized tables and features survive closure. Source-dependent raster
reads must finish inside the context. Closing releases readers/connections and
leaves persistent cache content intact.

Caching defaults to `cache=True`. `cache_dir` selects the location; otherwise it is
`~/Library/Caches/pourpoint` on macOS, `%LOCALAPPDATA%/pourpoint` on Windows, and
`$XDG_CACHE_HOME/pourpoint` (or `~/.cache/pourpoint`) elsewhere. `cache=False`
disables disk caching, not bounded in-memory buffering.

One SQLite cache accumulates original byte ranges by source, artifact fingerprint
and address. Atomic writes retain only acquired coverage and deduplicate overlap.
No private feature format, index rebuild, per-watershed bundle or automatic disk
eviction is involved. Cached manifest bytes pin the first snapshot in that cache.
To deliberately adopt a newer publication, choose a new cache directory or remove
the old cache when no readers are using it.

```python
with HydroFabric(url, cache_dir="./hfx-cache", offline=True) as fabric:
    basins = fabric.subbasins(fabric.traverse("catchments", [1284709]))
```

`offline=True` prohibits network acquisition, including HEAD. Completed workloads
can replay with their cache intact. Missing metadata or payload raises a clear
error, never partial results or a different snapshot. A partial cache is not a
standalone complete HFX bundle. Users control cache retention. Cache metadata is
trusted local state, not a tamper-proof database; stored range checksums detect
accidental payload corruption.

## Rasters, transport and measurement

`fabric.rasters` resolves the manifest's local or external direction/accumulation
pair lazily. See [native-grid raster access](rasters.md). Requests carry native grid
and window descriptors for future export; no direction-pointer warping occurs.
Missing rasters or inadequate coverage are errors.

Local paths, anonymous HTTP(S) and S3 use the shared checked obstore transport;
`storage_options` passes its constructor options. URLs must not carry credentials.
External raster URLs on another origin use anonymous transport; credentials and
source-specific configuration are never forwarded across origins.
Remote range reads pin a strong ETag or immutable version and check range, encoding,
identity and length. Lookup metadata binds addresses to the exact feature digest;
relationship declarations bind every involved domain. Directories are fully hashed;
feature/IPC/TIFF bounded head/tail fingerprints are selective checks, **not** full
artifact verification. Immutable producer publication remains required.

Cached Parquet reads prefetch projected column chunks with four concurrent
checked transfers, split at 8 MiB each, without coalescing gaps. Scheduling is per
row group, not worldwide. These are initial engineering constants, not proven
optima or a 32 MiB RSS guarantee: SQLite copies, decoding and returned data have
separate memory costs. The existing obstore transport was retained after examining
fsspec async scheduling, avoiding a second authentication/integrity path.

`stats` reports source requests (including HEAD), response payload bytes and disk
cache-hit bytes, shared with external rasters. It is not wire traffic.
`buffer_bytes` reports retained serialized source buffers, spatial statistics arrays
and estimated adjacency storage, not process RSS, Arrow/GDAL metadata, query results
or geometry allocations. Spatial arrays are prepared on the first candidate query
per domain and retain 50 bytes per Parquet row group; ID-only reads do not prepare them.
Public end-to-end evidence documents measured scope and limitations separately.

## Draft-reader compatibility

Existing `Dataset.open` and its `GeometryBatch`/`UnitSelection` interfaces remain
available for the earlier IPC draft only. They do not read the finalized public
spatial publication. Migrate final-publication calls to `HydroFabric` and explicit
domain methods above; do not select a reader using `format_version` alone.
`Dataset`'s global graph preparation is not used by `HydroFabric`.
