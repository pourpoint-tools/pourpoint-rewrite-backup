# Operation verification

Synthetic tests exercise independently worked small geometries and raster arrays:

- `test_operations.py`: requested levels, inclusive region boundaries, deterministic
  point boundary ownership, explicit empty and unavailable connectivity, stable batch
  identities/duplicates/failure slots, bounded simultaneous private contexts, persistent
  cache replay, composition and per-target raster pairs.
- `test_refinement.py`: whole-mask reverse D8 excludes cells joining below the outlet;
  granular/merged equivalence; independent band validity; signed terminals; mask gaps;
  row-major ties; global cell offsets; partial pixel/source attribution; no fallback.
- `test_rivers.py`: combined footprint clipping, directed downstream suffixes,
  splits/reconvergence, supplied endpoint orientation, ambiguity failures and metric
  snapping at high latitude.
- `test_qgis.py`: relative references, names/issues, explicit CRS, raw raster values,
  whole-vector masks and lossless Arrow values/metadata/relations.

Run with `uv sync --group dev`, then `uv run python -m pytest`,
`uv run ruff check src tests`, `uv run ruff format --check src tests`,
`uv run ty check`, and `uv build`. The implementation run used a fresh uv cache at
`~/.cache/uv-pourpoint-backend` because an inherited cache held an incomplete wheel;
no original cache or checkout was removed. Repository-wide Ruff format additionally
flags preexisting Python fence formatting in the published API vision; source/tests
are the validation scope, and published intent was not rewritten for formatting.

The installed QGIS 3.42.1 runtime independently loaded a relocated synthetic bundle:
watershed/outlet vector layers and both rasters were valid, with their expected CRS
and outlet renderer. Headless invocation used the QGIS application's bundled Python,
`QT_QPA_PLATFORM=offscreen`, and its `Contents/Resources/proj` data directory.

## Live GRIT receipts (2026-10-22)

[First/reuse/reopen measurements](receipts/backend-live.json) cover Netherlands
selection at level 1, eleven-unit level-2 refinement, granular/footprint agreement,
rivers, duplicate named targets, ocean failure, and per-target export.
[Amazon measurements](receipts/coarse-live.json) additionally check independently
witnessed closures, region selection and unavailable connectivity.
These initial runs overlapped implementation changes; their before/after source hashes
are retained, so they are diagnostics rather than frozen-revision certification.

| Integrated phase | Summed operation seconds | Requests | Body bytes |
| --- | ---: | ---: | ---: |
| First use, including open | 80.055 | 425 | 54,260,845 |
| Same context | 0.524 | 0 | 0 |
| Reopened persistent cache | 0.830 | 0 | 0 |
| Offline, transport disabled | 0.771 | 0 | 0 |

[Stable offline replay](receipts/backend-stable-live.json) passed against
`8373d852e462f31ea6c8b3933a3ed994a6b9bad5`, with unchanged source hashes, 0.746 summed
operation seconds, zero requests/bytes and 894,910,464 bytes process-lifetime peak RSS.
Its manifest SHA256 and exact targets/settings are in the receipt. This is reopened
persistent-cache execution, not a fresh download. The original diagnostic assertion
compared NaN nodata using equality; the replay uses NaN-aware comparison.

All four exported rasters exactly matched source values, native CRS/affine, validity
and nodata: 115,148 valid mask cells and 109,652 outside-mask cells each. The GeoPackage
retained the ocean issue and 83 river connection rows. [Actual QGIS loading](receipts/qgis-stable-live.json)
after relocation validated all 11 layers and three named target groups, including
EPSG:8857 outlet overlays. This checks provider loading, not visual styling approval.
Receipt paths use `$EVIDENCE_DIR` in place of the original local evidence directory.

The archived [offline recipe](receipts/replay_operations.py) reproduces this bounded
workload from the repository root with a previously populated measurement cache:

```bash
uv run python docs/receipts/replay_operations.py --cache /path/to/cache --output /path/to/new-output
```

Use a new output directory on each run; export refuses to overwrite a bundle. The
recipe disables transport and fails on cache misses. With a QGIS Python environment,
[inspect_qgis.py](receipts/inspect_qgis.py) accepts the relocated `.qgz` and an output
JSON path. On macOS the application runtime additionally needs `QT_QPA_PLATFORM=offscreen`
and `PROJ_LIB` pointing to its `Contents/Resources/proj` directory.

Times sum complete measured operations, including export, not whole-process wall time.
Requests include HEAD; bytes exclude headers/TLS and unobserved retries. Peak RSS is
process-lifetime (including native allocations), not incremental operation memory.
Worker transfers are included by the batch runtime. First use is not a cold-CDN
benchmark, and concurrent exploratory work limits timing comparisons.
The 1e-18 degree-squared overlay tolerance checks numerical agreement only. Interior
sample points are not independently verified stream stations. Synthetic correctness
and successful live execution do not prove vector/raster equivalence globally.

GRIT data and exports remain CC BY-NC 4.0: Michel Wortmann, Louise Slater,
Laurence Hawker, Yinxue Liu and Jeffrey Neal (2025),
https://doi.org/10.5281/zenodo.15715535; hosting sponsored by Upstream Tech.
