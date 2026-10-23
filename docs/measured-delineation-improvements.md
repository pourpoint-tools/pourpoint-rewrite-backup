# Measured delineation improvements

Investigation and implementation for [#51](https://github.com/pourpoint-tools/pourpoint/issues/51).
The public API, HFX contract, geodesic semantics and general polygon union are unchanged.
No dependency, numerical tolerance, iteration count or transport constant changed.

## Accepted changes

- Snapping passes scalar coordinates directly to `Geod.inv`, avoiding temporary
  Shapely points and repeated target-coordinate extraction in every evaluation.
  The 64 iterations, expression order, endpoint ordering and strict tie updates remain.
- Each vector reader prepares numeric row-group bounds and level statistics lazily
  on its first spatial query. NumPy filters them thereafter. Null/unknown bounds
  remain conservative, levels remain independent, and physical group order survives.
  ID-only readers do not pay preparation cost. The draft `Dataset` path is unchanged.

## Sequential offline results

Same three targets and query settings as the [GRIT benchmark](grit-delineation-benchmark.md):
river candidates, 1,000 m WGS84 snapping, level 2 upstream selection, materialization
and merge, with refinement disabled. Times below are milliseconds.

| Target | Baseline median | Final median | Reduction | First query baseline / final |
|---|---:|---:|---:|---:|
| Australia | 91.88 | 25.48 | 72% | 250.15 / 154.14 |
| Amazon | 137.98 | 55.81 | 60% | 268.60 / 199.73 |
| Spain river | 171.79 | 91.41 | 47% | 343.99 / 250.90 |

Each process opens each target's existing disk cache offline, then runs one first
query and seven same-reader repeats. First queries include reader setup and array
preparation; they are **not network-cold timings**. Opening alone was 2.25–3.79 ms
baseline and 0.50–0.79 ms final, too small/noisy to attribute to these changes.
Final repeat ranges were 24.92–26.35, 55.31–57.60 and 89.07–94.53 ms.
No tests or other benchmark jobs ran concurrently with measurements. Workstation
load, OS cache and sequential drift are uncontrolled; seven repeats are not
confidence intervals. No Rust rerun, large-basin, batch or throughput claim is made.

Baseline is `296e46b`; subsequent receipts label the experimental source state and
record source SHA-256 hashes (final hashes identify the delivered production code).
Environment: macOS arm64, Python 3.13; exact package/platform versions are in receipts.
Imports, validation and output hashing are outside timings. Profiling is a separate
query after the uninstrumented repeats, not the source of the table above.

Separate cProfile observations (cumulative ms; phases overlap, do not sum):

| Phase | Australia before / after | Amazon before / after | Spain before / after |
|---|---:|---:|---:|
| Row-group filtering | 45.1 / 0.05 | 45.9 / 0.06 | 46.9 / 0.06 |
| Snap | 71.7 / 7.4 | 120.2 / 12.2 | 114.5 / 11.3 |
| Candidate retrieval (both domains) | 64.4 / 17.6 | 68.4 / 20.1 | 66.0 / 17.2 |
| Upstream selection | 46.0 / 14.1 | 57.6 / 24.0 | 62.3 / 26.7 |
| Materialization (`_records`) | 15.7 / 15.3 | 26.6 / 24.1 | 28.4 / 24.4 |
| Merge | 0.12 / 0.12 | 18.4 / 17.1 | 55.8 / 52.0 |

Preparation measured separately on already-open metadata took 12.7–13.2 ms for
3,206 river groups and 25.6–26.1 ms for 6,706 catchment groups. Arrays retain
50 bytes/group: **495,600 bytes** combined per reader pair (also per worker).
This is array payload, not RSS or total Parquet metadata. Actual first-query totals
above include preparation. Serialized source buffers and adjacency remain separately
accounted. All eight queries' cumulative transfer counters exactly match baseline:
zero requests/received bytes, identical disk cache-hit bytes. No new transport path.

## Rejected experiments

- Scalar snapping alone gave medians 63.5/92.9/128.1 ms. With spatial arrays,
  scalar snapping gave 28.3/61.2/97.4 ms in the earlier comparison; an array-valued
  `Geod.inv` prototype gave 29.8/58.7/94.4 ms with exact outputs. These small mixed
  gains do not justify segment arrays, extra temporary allocations and more complex
  tie handling. The final scalar rerun was faster still, illustrating timing drift.
  Retain the simple scalar change, not a compiled kernel or new dependency.
- All three polygon sets passed GEOS coverage validation. General union versus
  validation + coverage union was approximately 0.07 vs 0.37 ms, 17.9 vs 6.2 ms,
  and 54.7 vs 20.4 ms in the first diagnostic repetition. Five raw repeats are
  retained. All specialized outputs were valid and topologically equal to general
  union. However, coverage validation needs GEOS >=3.12, which the project's
  `shapely>=2.1` contract does not guarantee (even installed Shapely 2.2 supports
  GEOS >=3.10). Portable QGIS delivery and arbitrary overlapping merge inputs make
  a new capability branch/dependency floor disproportionate here. Keep general
  union; this measured opportunity can inform a separately agreed runtime policy.
  HFX tolerance-based topology is not certification of exact GEOS coverage.
- Disjoint-subset union was no faster on these connected basins. No geometry repair,
  hole filling or union semantic change was attempted. Merge remains the largest
  measured Spain cost; no broad rewrite is necessary for the adopted gains.

## Correctness and reproduction

Every measured final/prototype query matches baseline **ordered IDs, source-attribute
hash, exact snapped coordinates and exact merged WKB**. All merged outputs are valid.
The pre-existing benchmark WKB witnesses also matched the baseline hashes. Coverage
experiments use topology equality rather than serialization equality. Focused tests
independently cover geodesic high-latitude choice, endpoints, input-order ties,
degenerate segments, validation before early returns, null statistics, partial-null
leaves, inclusive bounds and level filtering. Existing river tests protect this
behavior-preserving numerical refactor; new spatial tests failed for the missing
prepared-bounds implementation before it was added.

Validation: `uv run python -m pytest -q` (231 passed), `uv run ty check`,
`uv run python -m ruff check .`, and Ruff format check on `src tests scripts` plus
the experimental Python receipt. Repository-wide formatting separately flags an
unchanged pre-existing vision Markdown code block; it was not rewritten. Bare
`uv run pytest` initially resolved a global Python missing obstore; using
`uv run python -m pytest` selected the project's interpreter correctly.

Run from the repository root with the original GRIT benchmark caches present:

```bash
uv run python -m scripts.measure_delineation_warm --revision "$(git rev-parse HEAD)" --diagnostics --compare docs/receipts/delineation-baseline.json --report scratchpad/delineation-final.json
```

Diagnostics require GEOS >=3.12; production does not. `--snap-prototype
 docs/receipts/delineation-array-snap.py` repeats the rejected numerical experiment.
The script reuses the original fixed targets and `search_bounds`; it is an offline
follow-up, not a new benchmark framework. It does not download or rebuild caches.
Raw receipts: [baseline](receipts/delineation-baseline.json),
[scalar](receipts/delineation-scalar.json), [spatial + scalar](receipts/delineation-optimized.json),
[array experiment](receipts/delineation-array.json), [final](receipts/delineation-final.json).
The earlier spatial comparison prepared on reader construction; final preparation
is lazy so nonspatial readers avoid it. Raw evidence is retained, not build artifacts.
