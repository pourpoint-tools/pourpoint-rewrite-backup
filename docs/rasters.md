# Native-grid raster access

HFX 0.4.0 optionally declares `hfx.aux.d8_raster.v2` pairs in the root
manifest. The retained source manifest is provenance, not a discovery channel.
Vector-only bundles remain valid. Raster headers are not opened for vector use.

```python
from pourpoint import Dataset

with Dataset.open("/path/to/hfx") as dataset:
    request = dataset.rasters.prepare((west, south, east, north))
    # Inspect integer col, row, width, height and immutable grid before execution.
    window = dataset.rasters.read(request)
```

Bounds default to EPSG:4326. Pass `crs="EPSG:8857"` for bounds already on that
native CRS. Requests have positive area and must not wrap the antimeridian.
Preparation transforms all four envelope edges with 101 interior samples per
edge, not just two opposite corners. It uses the declared CRS of each pair,
then encloses the transformed envelope in whole native cells. This is conservative
edge sampling, not exact symbolic projection of arbitrary curved edges. Supplying
native bounds avoids that approximation. The direction raster is never warped.
A pair must cover the entire request. There is no mosaicking or overlap arbitration;
any fully covering coherent pair can be selected.

First preparation opens and validates pair headers. Once open, preparation uses
only grid metadata, with no pixel reads. Reader-bound frozen requests carry grid
CRS, affine, dimensions and both published SHA-256 identities. Requests from
another reader are rejected. The reader reuses ordinary synchronous GDAL handles.
There is no batch, scheduling or async API.

## Results and semantics

`RasterWindow` contains caller-owned arrays, `transform`, `crs` and
`accumulation_units`. Results survive later reads, mutations of later results and
reader closure.

- `direction` is int8: **1 E, 2 SE, 3 S, 4 SW, 5 W, 6 NW, 7 N, 8 NE**.
  These are native grid neighbors: E increases column and S increases row, even
  for a rotated affine.
- Zero is a terminal cell. Negative directions preserve signed GRASS exiting-flow
  meaning; they are terminal for upstream tracing, but retain the exit direction.
- `direction_valid` and `accumulation_valid` are separate boolean masks. A false
  direction mask has a zero payload, not a valid terminal cell. Header nodata
  takes precedence over codes. Invalid consumed data raises an error.
- `accumulation` retains native int32 or float32 values and bits. It is never
  converted through float32 from int32. Units are `cells` or `km2` exactly as
  declared. GRIT's width-partitioned drainage area remains width-partitioned km²;
  the reader does not convert it into another area definition.

The reader supports ESRI, TauDEM and unsigned/signed GRASS direction encodings.
It checks consumed declarations, fingerprints, pair CRS/grid, band count,
dtype/nodata and consumed values. It trusts producer conformance for internal
layout, overlap coherence, coverage union and hydrology. Native-only tiled TIFFs
without overviews are supported. Opening does not hash whole rasters or read
whole tile indexes.

Missing declarations raise `MissingRastersError`. No covering pair raises
`RasterCoverageError`. Invalid declarations, headers or consumed values raise
`ValueError`; unreadable raster data raises Rasterio/GDAL read errors. These
failures do not choose a coarse-watershed fallback.

## Lifetime and transport

For raster-only access without even catchment metadata acquisition:

```python
from pourpoint.metadata import Manifest
from pourpoint.rasters import RasterReader
from pourpoint.storage import Source

with Source("/path/to/hfx") as source:
    manifest = Manifest.parse(source.read_json("manifest.json"))
    with RasterReader(source, manifest) as reader:
        window = reader.read(reader.prepare((west, south, east, north)))
```

Close the reader before its Source. Create and use each reader on one thread.
One reader allows one active call. Future concurrent workers must create private
Sources and reader pairs in their own threads, not share ordinary GDAL handles.
Closing releases handles, not every process-wide GDAL cache. GDAL owns block-cache
budgeting and any process-wide VSICURL state; Pourpoint adds no worker pool,
custom raster cache or service-wide memory budget.

Rasterio uses a Python opener with independent fsspec cursors over the existing
Source's pinned obstore range access. It does **not** independently open the
remote URL with GDAL, copy credentials into GDAL settings, or pass a file object
to Rasterio's eager MemoryFile path. Local, HTTP and S3-compatible configuration
therefore use the same Source guarantees as vectors. Controlled S3 fixtures test
signed configured-endpoint access; public GRIT is HTTP evidence, not a live
credentialed S3 deployment test.

Source requests send `Cache-Control: no-cache` to revalidate publication responses.
Each Source retains its first manifest snapshot and never refreshes mid-lifetime.
An old vector-only snapshot is not supplemented by discovering undeclared files.
Raster opening checks size and bounded head/tail hashes. Every new remote range
uses the original strong ETag/version and validates the response. Cached bytes
remain part of that original snapshot; cached reads do not probe whether the
latest remote object changed. Local identities are checked before/after calls,
even on cached reads. Bounded checks are not whole-file integrity proofs.

`Dataset.buffer_bytes` includes fsspec cursor buffers, not GDAL native caches or
caller-owned result arrays. Array bytes and process RSS are different quantities.
Rasterio 1.5.2 can also emit an unraisable Cython diagnostic when a range callback
rejects changed data. The read still fails; Pourpoint does not suppress production
errors or accept data from that response.

GRIT data retain their bundle's CC BY-NC 4.0 license, attribution, provenance and
source caveats. Pourpoint's code license does not replace those terms.
