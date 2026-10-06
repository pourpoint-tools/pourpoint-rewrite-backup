# Selective vector access experiment

Standalone research for the [published vision](../../planning/visions/2026-10-06-vector-access-experiments.md).
No production engine behavior or format changes.

## Reproduce

Use the complete original HydroBASINS bundle and its download receipt. Keep both
outside Git. Preserve the bundle's attribution and provenance notices. The shared
acquisition is separate from these local measurements.

From this repository checkout:

```sh
uv sync --project experiments/selective_access
uv run --project experiments/selective_access pytest experiments/selective_access -q
uv run --project experiments/selective_access ruff check experiments/selective_access
uv run --project experiments/selective_access ty check experiments/selective_access/selective.py experiments/selective_access/snap_access.py experiments/selective_access/measure.py experiments/selective_access/test_selective.py experiments/selective_access/test_snap_access.py experiments/selective_access/test_measure.py
uv run --project experiments/selective_access python experiments/selective_access/measure.py --data /path/to/hydrobasins --receipt /path/to/download-receipt.json --output /path/to/result.json --activity 'other known activity'
```

Commit code before running. Each JSON records its code commit, receipt object keys,
ETags, local SHA256, package versions, projections, settings, inputs, and cache state.
The receipt's download duration is not part of warm local query timings.

## Measured work

- Attribute-only projection: `id`, `level`, `parent_id`, `up_area_km2`. Nulls remain
  Arrow nulls. No geometry is loaded. Attribute read and numeric catchment-ID locator
  construction have separate timers. The locator sorts original IDs, never graph rows.
  Locator sorted IDs, row positions, and cumulative group ends all use int64.
- Polygon selection: three declared IDs (first, middle, last) from each of two
  existing row groups: median and largest compressed geometry chunk. Ordinary
  Arrow filtered reads and explicit locator candidate-group reads use the same
  projection and exact ID check. Decode only selected WKB. Byte-for-byte selected
  WKB equality is checked across methods and repeats.
- Snap candidates: 0.1-degree boxes around the first stored bbox center in median
  and largest geometry groups. If bbox is absent or the first row is null, use the
  declared fallback box `[-0.1, -0.1, 0.1, 0.1]`. Preserve individual snap IDs and many-to-one unit
  references. Null/missing bbox rows remain candidates, then use decoded geometry
  bounds as fallback. Verify exact IDs/unit IDs/WKB across methods and repeats;
  verify referenced catchment levels against the manifest. Distance, radius,
  ranking, geometry repair, union, and area computation are excluded.

Each query is repeated once with method order reversed. Local OS cache state is
uncontrolled. The locator is reused, not polygon/geometry results. Equality checks
retain only selected WKB, not whole-dataset geometry. Times are laptop diagnostics. Startup covers entry into `run()` through manifest,
commit, and environment capture; interpreter startup and imports are excluded. Query
read timers include metadata candidate selection, I/O, and filtering; polygon timers
also include locator query lookup. Snap decode timing includes fallback filtering.

Arrow `nbytes` measures logical referenced bytes; `get_total_buffer_size()` measures
whole retained buffers. Locator NumPy arrays own their reported buffers. Logical
categories are not summed with process RSS. RSS is sampled only at stage endpoints;
`getrusage` supplies process-lifetime peak RSS (bytes on macOS), separately from
Arrow pool current and peak counters. Transient locator peak is not measured.

Candidate column-chunk compressed bytes are storage metadata, **not bytes read**.
Neither local physical I/O nor remote transport is instrumented. Wire requests and
response bytes are **not measured**. Explicit reads count all candidate WKB before
filtering. Arrow filtered scans expose selected WKB only; their intermediate WKB
materialization is **not measured**. Batch row settings are not byte-memory limits.

Runtime checks cover the operations exercised here, not complete HFX conformance.
Graph topology and traversal are excluded; companion experiments test DAG traversal.
Small independent tests cover non-row IDs, graph/catchment order independence, null
attributes, parent identity, missing IDs, geometry alignment, and snap fallback.

Initial development with scanner `batch_readahead=0, fragment_readahead=0` stalled
on the tiny test and was terminated. Published code uses 1 for both, without a tuning grid.

## Fresh-process polygon memory follow-up

`polygon_memory.py run` runs exactly one existing selection and one reader in a
fresh process. It reuses the ordinary (group 580) and largest (group 0) IDs from
`results/hydrobasins-isolated.json`; planning does not read geometry. Both readers
prepare the same locator and retain the same three decoded Shapely geometries,
selected Arrow table, and locator until the READY snapshot. The original
`polygons()` default still decodes and discards its results.

Startup excludes interpreter startup/imports. Attribute loading and locator build
are separate from query timing. Preparation and READY record endpoint RSS and
process-lifetime high-water RSS; these peaks are never subtracted or described as
query-only peaks. Arrow counters are separate. Explicit candidate buffers are
observed while the prefilter table is live, but that table is released before
READY. Filtered native intermediate WKB and decoded GEOS allocation sizes are
**not measured**. Selected Arrow logical/backing buffers and locator buffers are
reported separately. Three decoded geometries remain live; they are not a byte
allocation measurement. Read/filter and decode timers are inside total query time.

Witness serialization occurs only after READY. `compare` directly compares the
ID-sorted Arrow tables, including original WKB and WKB serialized from each retained
decoded output. It also checks source/decoded geometry equality with zero tolerance.
Witness SHA256 values summarize complete, framed IPC files; hashes do not replace
exact comparisons. Geometry witnesses stay outside Git.

```sh
uv run --project experiments/selective_access python experiments/selective_access/polygon_memory.py run --data /path/to/hydrobasins --receipt /path/to/download-receipt.json --selection ordinary --method filtered --witness /private/output/ordinary-filtered.arrow --output /private/output/ordinary-filtered.json --activity 'No concurrent coordinated experiments; other laptop work unknown'
uv run --project experiments/selective_access python experiments/selective_access/polygon_memory.py compare /private/output/ordinary-filtered.arrow /private/output/ordinary-explicit_groups.arrow --output /private/output/ordinary-equality.json
```

Repeat `run` for `ordinary`/`largest` and `filtered`/`explicit_groups`, each as a new
process. The results directory records the exact measured order and commands.
Use the same complete immutable local bundle; its acquisition remains excluded.
No cache flush is performed. Wire counts and bytes are not measured. Graph, snap,
repair, union, area computation, and complete conformance validation are excluded.

Check the added files explicitly (the repository's default type-check scope omits
experiments):

```sh
uv run --project experiments/selective_access ty check experiments/selective_access/polygon_memory.py experiments/selective_access/selective.py experiments/selective_access/test_polygon_memory.py experiments/selective_access/test_selective.py
```
