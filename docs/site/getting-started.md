# Getting started

## Install

Use Python 3.13 or newer and [uv](https://docs.astral.sh/uv/). From an authorized
checkout of the private Pourpoint repository, run:

```bash
uv sync
```

Run Python through `uv run python` in that checkout. No PyPI release is required.
For another Python project, install the checkout as a local dependency with
`uv add /path/to/pourpoint`.

## Choose an HFX bundle

Supply `HydroFabric` with a local bundle directory or an accessible HTTP(S) or S3
bundle location. Its root manifest must select all three current representation
values: `format_version: "0.4.0"`, `representation: "spatial-geoparquet-adjacency"`
and `representation_version: 1`. The format version alone is not sufficient.
See [bundle discovery](hfx/bundle.md). Source normalization belongs to HFX, not
Pourpoint.

Vector operations need catchment or river features and the applicable supplied
relationships. Point refinement also needs a declared, covering direction and
accumulation raster pair. Vector-only bundles remain valid; unavailable rasters
are not silently replaced with coarse results.

## Source lifetime and coordinates

Use `HydroFabric` as a context manager. Finish source-dependent operations and
exports inside the context. Calls are eager; materialized vector results survive
closure. A fabric instance is not a concurrent-query service.

Vector targets use WGS84 longitude/latitude, in that order. Native raster outputs
retain their own CRS and affine transform. Snapping radius is in metres; the
refinement window is measured in native cells.

The optional `cache_dir` selects a persistent byte-range cache. With the default
cache enabled, the first manifest snapshot is retained. Use a new cache directory
to adopt another publication deliberately. `offline=True` replays acquired ranges;
a partial cache cannot answer requests for data it never acquired.

See [runtime access](runtime.md) for cache, transport and identity guarantees and
[operations](operations.md) for selection and failure semantics. The generated
[core API](reference/operations.md) documents supported calls and returned values.

## Data terms

The MIT code license does not replace source-data licenses. Preserve each bundle's
attribution and provenance in derived outputs. GRIT data carry CC BY-NC 4.0 terms
and source caveats.
