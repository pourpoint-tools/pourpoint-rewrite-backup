# Runtime access

`Dataset.open(root, cache_dir=None, storage_options=None)` supports HFX 0.4.0 only.
The manifest discovers every consumed artifact; no listing or source-manifest
inference occurs. Extra manifest fields and Arrow fields/metadata survive reading.
Source normalization, rasters and full delineation are outside this interface.

## Costs and ownership

- Opening reads the manifest, full small lookup directory, IPC schema/footer and
  bounded fingerprints. It does not read all lookup or catchment batches.
- First topology use downloads the original graph once (remote sources), verifies
  its SHA-256, reads local graph columns and builds compact resident numeric arrays.
  Local original graphs are not copied. A persistent caller-selected cache avoids
  downloading again; reopening hashes the cached original, then prepares a new
  topology. There is no binary topology cache. Without `cache_dir`, the temporary
  graph is removed on close.
- `upstream(id)` returns inclusive, terminal-first membership. DAG reconvergence
  appears once. Each call allocates `5 * unit_count` workspace bytes even for a
  small result. Resident arrays are read-only, with no duplicate ID edge layout.
- `records(ids)` returns all selected fields plus WKB in a `GeometryBatch`.
  `attributes(ids, columns=None)` excludes WKB; IPC still decodes its containing
  batch, but does not create geometry objects. `polygons(ids)` returns only ID/WKB.
  All preserve caller order and duplicates, and raise for any missing requested ID.
  Combine IDs in one call to share numeric lookup and catchment batch reads.
- `GeometryBatch.decode()` returns `DecodedGeometry(ids, geometries, attributes)`.
  It checks selected WKB type/coordinates and removes WKB from decoded attributes.
  Decoding is not zero-copy and is not cached. Retaining both returned objects is
  the caller's explicit choice. Selected buffers are compact copies, not slices
  that pin discarded batches.
- The dataset owns readers, metadata and prepared arrays. Queries own workspaces
  and selected results. `close()` is explicit and idempotent; a context manager is
  preferred. Selected results remain usable after close. Selections from one
  dataset instance cannot address another. Instances are not thread-safe or a
  concurrent-service API. Metadata declarations should be treated as read-only.

`buffer_bytes` reports resident numeric arrays and per-query workspace, not RSS,
reader metadata, temporary conversion buffers or native GEOS allocations.
`last_access` reports selected batches or row groups for the latest access.
It is a diagnostic, not network-traffic accounting.

## Spatial and snap candidates

`catchment_candidates((west, south, east, north), levels=None)` uses graph Parquet
bbox/level statistics and selected graph rows. It resolves resulting IDs through
the published lookup and returns complete `GeometryBatch` records in graph order.
Float32 envelopes expand conservatively, including degenerate bounds. Bbox overlap
is not containment, and upstream membership is never clipped to this window.

`snap_candidates(name, bounds, levels=None)` returns **DecodedGeometry**, consistently
for every set. Attributes include feature `id`, referenced `unit_id`, weight and
optional role/bbox. Many features may refer to one unit. Candidate row groups and
rows are pruned by bbox; absent/null bounds require decoding to test actual bounds.
Selected geometry is decoded once and serialized WKB is not retained in the result.
No radius test, nearest-point computation or ranking is performed. Weights are not
comparable across sets. A set without bbox statistics can require reading all of
its rows; no absent index is invented. Both declarations and selected references
constrain levels.

## Transport and integrity

Local paths, anonymous HTTP(S) and `s3://bucket/prefix` use obstore. For S3,
`storage_options` passes obstore constructor options such as `config` (region,
endpoint, credentials), `credential_provider`, `client_options` and `retry_config`.
Public HTTPS remains HTTP even when hosted by an S3 service; it needs no AWS
credentials. Source options and source-specific caches are not shared accidentally.
Endpoint settings are part of cache identity. Do not put credentials into URLs.

A thin fsspec buffered-file range hook uses obstore's conditional range operation.
The stock obstore file adapter does not expose conditional read options. The hook
pins strong ETag/object version, checks returned identity, range, encoding and body
length, and relies on obstore's HTTP status/Content-Range parsing. It adds no custom
transport, file-positioning or general cache engine. Controlled protocol tests
cover ignored conditions, malformed ranges and short/long responses.

Lookup/catchments check manifest sizes and first/last 64 KiB hashes. The directory
checks its full digest; lookup schema binds the full catchment digest. Bounded
hashes detect mismatches, not whole-file corruption. Trusted producer verification
and immutable publication remain required. Graph downloads complete atomically
only after full SHA-256 verification. Existing files are never trusted by name alone.
ETags identify transport versions, not SHA-256 content. Independent full catchment
integrity would have a different cost and is not done at ordinary opening.

## Validation limits

Used schemas, nulls, numeric capacity, paths, exact lookup matches, selected row
addresses and selected references are validated. Topology checks unique positive
IDs and usable same-level edges while preparing numeric adjacency. It does **not**
run a global acyclicity/tree audit; visited state prevents repeated traversal but
is not proof of a DAG. There is no worldwide nullable attribute catalog, parent
certification, graph/catchment correspondence scan, area recomputation or hydrologic
geometry audit. Source validity and GRIT's documented DAG upstream-area caveats
remain upstream responsibilities. Malformed consumed values fail rather than
silently producing incomplete selections.
