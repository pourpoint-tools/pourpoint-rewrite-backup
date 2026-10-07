# Vector access measurements

These experiments measure Python vector access on unchanged HFX 0.3.0. They do
not implement the production engine or select its architecture.

Intent: [vector access experiments](../planning/visions/2026-10-06-vector-access-experiments.md).

## Shared dataset

All local runs use the complete HydroBASINS global dataset: 3,786,228 drainage
units and 8,477,883 snap features. Original files and notices stay outside Git.
The [acquisition receipt](results/hydrobasins-download.json) records object ETags,
SHA-256 hashes, sizes, and elapsed download times. The single initial download
saved 4,330,244,568 bytes of object payload (calculated sum of receipt object
sizes) in 139.588 seconds. This is not a wire-traffic measurement and is excluded
from local access timings.

To obtain the same input, use read-only S3 access with your configured profile:

```sh
aws --profile hetzner-pourpoint --region fsn1 \
  --endpoint-url https://fsn1.your-objectstorage.com s3 sync \
  s3://pourpoint-hfx/hfx/hydrobasins-pfaf1-12-nested-global-snap-2026-07-20/ \
  /path/outside-git/hydrobasins/
```

Check each file's SHA-256 against the receipt before comparing results. Keep
`ATTRIBUTION_AND_PROVENANCE.txt` with the dataset. Do not redistribute these
artifacts as standalone fixtures. Experiment outputs belong in a different
directory. Local access means OS-cache state is uncontrolled, not cold storage.

Native Arrow S3 request counts and response bytes are **not measured**. Stored
compressed column sizes are reported separately and are not network traffic.
All times are diagnostics from a shared laptop; no performance target or winner
is implied. Each experiment records its code commit, dependencies, reader
settings, query inputs, buffer accounting, and observed concurrent work.

## Comparison

The [machine-readable summary](results/comparison.json) links each value to its
saved run. Seconds are rounded here. Reader cells list run 1 / run 2; Baseline
is serial and Comparison is prefetch. Topology and selective rows use the isolated
local runs. “Ordinary” and “largest” refer to the selected existing geometry groups,
not newly extracted datasets.

| Measurement | Baseline | Comparison |
|---|---:|---:|
| Local graph read, seconds (serial / prefetch) | 0.291 / 0.072 | 0.064 / 0.070 |
| Remote graph read, seconds (serial / prefetch) | 383.140 / 366.577 | 128.989 / 111.924 |
| Topology load + index, seconds (ID / dense) | 0.0569 + 0.1508 | 0.0558 + 0.1510; conversion 0.0654 |
| 44,962-unit query, first / repeat median seconds (ID / dense) | 0.0485 / 0.0472 | 0.0164 / 0.0160 |
| Ready process RSS, decimal MB (ID / dense) | 402.29 | 420.46 |
| Ordinary polygon: candidate groups (filtered / locator) | 209 | 1 |
| Largest-group polygon: candidate groups (filtered / locator) | 94 | 1 |
| Ordinary polygon: read/filter seconds (filtered / locator) | 0.3459 | 0.0067 |
| Largest-group polygon: read/filter seconds (filtered / locator) | 0.1937 | 0.1387 |
| Ordinary snap: candidate groups / decoded features (filtered / explicit) | 3 / 9 | 3 / 9 |
| Largest-group snap: candidate groups / decoded features (filtered / explicit) | 2 / 10 | 2 / 10 |

Topology repeat medians use five queries with fresh query workspace. The terminal
ID is `6120007000`; exact selected-ID arrays agree with an independent SciPy
traversal. The graph plus levels retains 81.04 MB, the ID lookup 45.43 MB, and dense
edges add 14.02 MB while keeping the original ID edges for comparison. Query
workspace is 18.93 MB plus output. The reader’s graph representation is accounted for
separately in its records; these are not identical memory layouts.

Attribute-only access reads 3,786,228 rows in 0.0778 seconds, then builds the
catchment locator in 0.1480 seconds. Parent/level/up-area buffers retain 53.95 MB;
the ID column retains 30.29 MB and the int64 locator owns 60.59 MB. Graph row
alignment is not assumed. No geometry is decoded for this step.

Both polygon methods decode the same three selected geometries. Explicit access
materializes 5.23 MB of WKB for the ordinary group and 245.52 MB for the largest
group before filtering. Filtered-reader intermediate WKB is **not measured**.
The full selective sequence has a process lifetime peak RSS of 4,920.31 MB; this
is not a per-method peak. Snap candidates are not final snap selections. Distance,
ranking, geometry repair, union, and area computation are excluded.

Reader graph buffers retain 81.48 MB, plus 45.43 MB for the lookup. Remote serial
run 1 overlapped brief local experiments; run 2 and both prefetch runs had no
coordinated competing experiment runs. Reader RSS and process peaks are in
[the reader result table](arrow_read/RESULTS.md).

## Reproduce and inspect

- [Arrow reader commands and records](arrow_read/README.md) — [PR #13](https://github.com/pourpoint-tools/pourpoint/pull/13).
- [Topology commands and records](dense_topology/README.md) — [PR #11](https://github.com/pourpoint-tools/pourpoint/pull/11).
- [Selective access commands](selective_access/README.md) and
  [full query table](selective_access/results/README.md) — [PR #12](https://github.com/pourpoint-tools/pourpoint/pull/12).

Each experiment has an isolated `uv` project and lockfile. Correctness tests use
small independent cases, including a reconvergent DAG, non-row IDs, missing IDs,
null attributes, and missing/null snap bounds. Real-data records include exact
membership and geometry-identity checks. Expensive complete conformance validation
is excluded. No production code, HFX artifacts, or issue scopes were changed.

## Published-lookup and Arrow-layout follow-up

The [catchment reader comparison](catchment_read/results/README.md) measures an
ID-only HFX 0.3.0 baseline against the experimental published-lookup and Arrow IPC
bundles. It does not change the earlier measurements or production contract.
