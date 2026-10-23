# GRIT delineation benchmark

Practical regression screen for [#47](https://github.com/pourpoint-tools/pourpoint/issues/47)
and the [published vision](../planning/visions/2026-10-23-grit-delineation-benchmark.md).
This compares complete engine/dataset stacks, not languages in isolation.

## Results

Python is **faster on cold start but slower on repeated use** for these matched
watersheds. The repeated-use slowdown is a clear regression flag: **2.68–4.46×**,
although absolute Python query times are only about 0.10–0.19 seconds here.
This does not identify a production optimization or generalize to large basins.
Cold totals are network-sensitive single observations, not stable speed guarantees.

| Target / units | Opening Python / Rust (s) | Cold Python / Rust (s) | Cold ratio | Python repeats (s) | Rust repeats (s) | Median repeat ratio |
|---|---|---|---:|---|---|---:|
| Australia / 2 | 0.991 / 0.383 | 78.760 / 289.793 | 0.272 | .1163, .1029, .1063 | .0380, .0238, .0223 | 4.46 |
| Amazon / 134 | 0.538 / 0.953 | 84.119 / 487.010 | 0.173 | .1576, .1551, .1527 | .0583, .0579, .0580 | 2.68 |
| Spain river / 503 | 0.602 / 0.453 | 99.366 / 628.630 | 0.158 | .1905, .1857, .1829 | .0636, .0607, .0614 | 3.03 |

Ratios are Python/Rust; below 1 means Python faster. Repeated ranges do not
overlap for any pair: none is comparable within the observed variability.
Python repeated queries issued zero additional transport requests/received bytes
and increased cache-hit bytes. Rust reuse remains uninstrumented. Every repeated
output preserved its stack's exact membership and WKB hash.

[Raw Australia Python](receipts/grit-delineation-python-australia.json) /
[Rust](receipts/grit-delineation-rust-australia.json);
[Amazon Python](receipts/grit-delineation-python-amazon.json) /
[Rust](receipts/grit-delineation-rust-amazon.json);
[Spain Python](receipts/grit-delineation-python-spain-river.json) /
[Rust](receipts/grit-delineation-rust-spain-river.json).

## Method and identities

Measured 2026-10-23 on Apple M4 Max, 16 CPUs, 64 GiB RAM, macOS 15.7.3,
Python 3.13.14. [Environment versions](receipts/grit-delineation-environment.txt).
Python code is `804a3f1` (production source unchanged from `994b7e0`);
Rust is the published `pourpoint==0.4.0` wheel, not a local Rust build.
One cold process and three same-engine repeats per target, run sequentially.
No competing benchmark jobs or tracing. Workstation load and public HTTPS latency
are uncontrolled; fresh application caches do not imply cold OS/CDN caches.
Three repeats show variability, not confidence intervals.

Imports, validation and WKB serialization are outside timing. Cold total includes
opening through completed merged result; Rust defers core initialization until
first delineation. Both disable refinement and snap within 1,000 metres. Python
acquires complete local reach-river candidates and materializes them **inside**
timed work, then calls `delineate(..., level=2, snap_onto=rivers, radius=1000,
refine=False, merge=True)`. Candidates use river tier 1 and a conservative 2 km
bbox at these nonpolar targets; the snap operation applies the actual 1 km limit.
Rust calls `Engine(source, refine=False, snap_radius=1000)` then
`delineate(lon=..., lat=..., geometry=True)`. No export or separate downstream
river operation is timed. Australia's earlier Rust run used the identical default
1,000 m radius; it is reused, not presented as an explicit-radius rerun.

Every cold process uses an empty cache: Python explicit `cache_dir`, Rust
`HFX_CACHE_DIR`. Rust's default HTTPS Parquet memory cache is 512 MiB and starts
fresh with the process. Python receipts contain cumulative transport counters.
Rust has no public counters; repeated timings are not certified all-cache hits.
Missing telemetry does not block this practical screen.

Used manifest snapshots, confirmed from application caches, have SHA-256:

- HFX 0.3.0: `02339ff92cbfd1d2ea57bb5332cb843b98115cd7a7395f64c14fac78d2ed643c`
- HFX 0.4.0: `04bbfb50907feaf339808730f85c39ee2cbeaed2c6217e9d546cd445b3bc897f`

The latter matches [final GRIT delivery](https://github.com/pourpoint-tools/hfx/blob/main/docs/v0.4.0/grit-final-delivery.md),
not earlier layouts with the same version string. Both derive from GRIT v1.0;
the old publication has 22,337,300 units, the new 22,419,857.
The [fixed target file](receipts/grit-delineation-targets.json) records interior
coordinates from reach polygons near Australia, Amazon and Spain windows. Their
unsnapped upstream counts were 2, 135 and 503. The original Spain representative
point failed in **both** stacks: no snap candidate within 1 km. Rust's failure
arrived after 714.096 seconds process wall time, not a completed-result timing.
The one replacement, `spain-river`, is the midpoint of source reach `160501332`,
prechecked to delineate 503 units. It stays alongside the failed target in the
configuration and receipts; no further targets were substituted. The largest is relative to this
small sample, not a continental-scale basin or a global throughput benchmark.

## Comparability checks

The untimed comparator maps old `reach:<global_id>` source IDs to current
`source_id` within `grit:1.0:<region>:reach_catchments:<table>`. Numeric HFX IDs
are not equated. It checks complete source membership and selected outlet source,
then compares valid merged footprints. It reads selected old Parquet row groups;
no bulk GRIT data or WKB geometry witnesses are committed.

Native behavior differs deliberately: Rust ranks by weight/mainstem, then distance;
Python chooses nearest WGS84 segment point. Rust chooses the snap feature's
associated catchment, while Python applies snapped-coordinate containment. Rust
cleans with outward/inward mitre buffers (`1e-5` degrees), then removes all holes;
Python unions without hole filling. `repair_geometry=False` does not disable that
Rust cleaning. No tuning forces either result to match the other.

Footprint diagnostics are angular square degrees and Hausdorff degrees, not
geodesic areas. Relative symmetric difference is measured against union area.
No fixed percentage tolerance certifies equality: outlet, membership and footprint
differences are reported together to distinguish materially different basins.
A hole-filled Python diagnostic only explains differences; it replaces neither output.

Release source evidence is the [0.4.0 sdist](https://files.pythonhosted.org/packages/a3/20/c0199785c580c6cef359d1ff60f7008c60ba98661174da856652c36a41cf/pourpoint-0.4.0.tar.gz),
SHA-256 `e7e98c6603d59c01cd41e5173b9fc8bb1a529eaf3b7cda833baa7a899d4f15c6`.
Relevant `crates/core/src/resolver.rs`, `assembly.rs`, `cache.rs` and Python binding
behavior match reference commit `60e3d6e383546ea6ed20e68fe4d77959c816a48c`.

All three final pairs have **identical upstream source membership and selected
outlet source identity**. Amazon snaps to source `407493` (134 units), not the
original interior source `407494` (135). Its original coordinate is outside the
selected outlet polygon, which is expected after snapping and not hidden as a
containment match. All six merged outputs are valid and have zero holes.

| Footprint diagnostic | Australia | Amazon | Spain river |
|---|---:|---:|---:|
| Symmetric difference (degrees²) | 2.789e-9 | 1.221e-8 | 1.317e-8 |
| Difference / union area | 6.089e-7 | 1.653e-7 | 3.534e-8 |
| Hausdorff distance (degrees) | 1.294e-8 | 9.992e-6 | 1.000e-5 |

These small boundary differences, together with exact source membership/outlet
matches, support treating these pairs as the same basins for this practical
screen; they are not exact geometric equivalence. Rust's hole-removal difference
has no effect on this sample because there are no holes.
Comparisons: [Australia](receipts/grit-delineation-compare-australia-snap.json),
[Amazon](receipts/grit-delineation-compare-amazon.json),
[Spain river](receipts/grit-delineation-compare-spain-river.json).
Failed original Spain: [Python](receipts/grit-delineation-python-spain-failed.json),
[Rust](receipts/grit-delineation-rust-spain-failed.json); neither supplies a
completed-result speed comparison.

## Reproduce

```bash
uv sync --group dev
uv venv --python 3.13 scratchpad/grit-benchmark/rust-env
uv pip install --python scratchpad/grit-benchmark/rust-env/bin/python pourpoint==0.4.0 shapely==2.2.0
uv run python scripts/measure_delineation.py --stack python --target docs/receipts/grit-delineation-targets.json --name australia --cache scratchpad/grit-benchmark/python-australia-snap --report scratchpad/grit-benchmark/python-australia-snap.json
scratchpad/grit-benchmark/rust-env/bin/python scripts/measure_delineation.py --stack rust --target docs/receipts/grit-delineation-targets.json --name australia --cache scratchpad/grit-benchmark/rust-australia --report scratchpad/grit-benchmark/rust-australia.json
UV_CACHE_DIR=scratchpad/grit-benchmark/uv-cache uv run --with aiohttp python scripts/compare_delineation.py --python scratchpad/grit-benchmark/python-australia-snap.json --rust scratchpad/grit-benchmark/rust-australia.json --report scratchpad/grit-benchmark/compare-australia-snap.json
```

Run the same commands sequentially for `amazon`, then `spain-river`, substituting all
names. Choose new cache/report paths for another attempt; measurements refuse
nonempty caches or existing reports. The isolated `UV_CACHE_DIR` avoids a broken
pre-existing shared uv cache encountered during comparator setup.
GRIT retains CC BY-NC 4.0 terms and source attribution; the code license does not
replace them.

Earlier unsnapped Python receipts are retained as `*-unsnapped.json`, not included
in this screen. A first unsnapped Spain attempt overlapped local tests; its
`*-excluded.json` receipt is retained but excluded. All final snapped timings run
without concurrent validation. The original no-snapping scope was revised in
[PR #49](https://github.com/pourpoint-tools/pourpoint/pull/49) after finding that
Rust always snaps when the selected level declares snap features and has no
public disable option. No production code or constants were changed.
