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

Live GRIT operation measurements and portable deliverable checks are recorded below
once complete. Synthetic correctness and successful live execution do not prove the
vector/raster equivalence assumption globally; scientific evaluation is separate.
