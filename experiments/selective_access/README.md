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
- Polygon selection: three declared IDs (first, middle, last) from each of two
  existing row groups: median and largest compressed geometry chunk. Ordinary
  Arrow filtered reads and explicit locator candidate-group reads use the same
  projection and exact ID check. Decode only selected WKB. Byte-for-byte selected
  WKB equality is checked across methods and repeats.
- Snap candidates: 0.1-degree boxes around the first stored bbox center in median
  and largest geometry groups. Preserve individual snap IDs and many-to-one unit
  references. Null/missing bbox rows remain candidates, then use decoded geometry
  bounds as fallback. Verify exact IDs/unit IDs/WKB across methods and repeats;
  verify referenced catchment levels against the manifest. Distance, radius,
  ranking, geometry repair, union, and area computation are excluded.

Each query is repeated once with method order reversed. Local OS cache state is
uncontrolled. The locator is reused, not polygon/geometry results. Equality checks
retain only selected WKB, not whole-dataset geometry. Times are laptop diagnostics.

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
