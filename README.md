# pourpoint

Private workspace for a fresh Python rewrite of pourpoint, a watershed
delineation engine consuming [HFX](https://github.com/CooperBigFoot/hfx)
datasets. Functional upstream selection, raster refinement, river selection and
portable QGIS export use the final HFX 0.4.0 spatial reader and runtime.
See [hydrological operations](docs/operations.md) for usage and conventions.

## Reader and runtime

```python
from pourpoint import HydroFabric

with HydroFabric("https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0/", cache_dir="./hfx-cache") as fabric:
    ids = fabric.traverse("catchments", [1284709])
    subbasins = fabric.subbasins(ids)
    attributes = fabric.records("rivers", [1], columns=["id", "source_strahler_order"])
```

See [runtime access](docs/runtime.md) for costs, candidate APIs, integrity and lifetime.
[Native-grid raster access](docs/rasters.md) supplies prepared paired direction and
accumulation windows for whole-footprint refinement.
GRIT source data carry CC BY-NC 4.0 terms and attribution in their bundle; this
repository's code license does not replace source-data terms.

## Development

Use Python 3.13 and [uv](https://docs.astral.sh/uv/):

```bash
uv sync --group dev
uv run python -m pytest
uv run ruff format --check
uv run ruff check
uv run ty check src
uv build
```

See [AGENTS.md](AGENTS.md) for development conventions. The local package version
`0.0.0.dev0` is a setup placeholder, not a release plan. No package publishing or
CI is configured.

## Rust reference

The previous implementation remains in
[CooperBigFoot/pourpoint](https://github.com/CooperBigFoot/pourpoint).
Use commit [`60e3d6e383546ea6ed20e68fe4d77959c816a48c`](https://github.com/CooperBigFoot/pourpoint/tree/60e3d6e383546ea6ed20e68fe4d77959c816a48c)
as the stable comparison baseline. Its source, tests, and fixtures are evidence,
not a requirement to preserve every behavior or design choice.

This repository does not copy the Rust Git history. In a checkout of the old
repository, inspect a file with:

```bash
git show 60e3d6e383546ea6ed20e68fe4d77959c816a48c:crates/core/src/engine.rs
```

The canonical input contract is the
[HFX 0.4.0 vector contract](https://github.com/pourpoint-tools/hfx/blob/main/docs/v0.4.0/vector-data.md),
also available locally at `../hfx/docs/v0.4.0/vector-data.md` when using sibling checkouts.

## License

[MIT](LICENSE).
