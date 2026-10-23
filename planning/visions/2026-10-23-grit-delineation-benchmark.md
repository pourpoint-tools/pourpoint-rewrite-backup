# GRIT delineation benchmark

Get a practical impression of performance and flag major regressions in the
Python rewrite, comparing unrefined GRIT watershed delineation with HFX 0.4.0
against the released Rust-backed implementation with HFX 0.3.0. This is a small
regression screen, not strict equivalence certification or a language benchmark: it
compares two complete engine/dataset stacks.

This vision defines the work to complete [#47](https://github.com/pourpoint-tools/pourpoint/issues/47).
Publishing the vision does not complete the issue. Return the benchmark findings
there; this work authorizes measurement, not production optimization.

## Compare the public APIs

Use separate environments because both packages import as `pourpoint`:

- Rust baseline: install the published `pourpoint==0.4.0` with `uv`. This is the
  **package version**, supporting **HFX 0.3.0**. Use `pourpoint.Engine(source,
  refine=False, snap_radius=1000)` followed by `engine.delineate(lon=lon, lat=lat, geometry=True)`.
  Standard delineation already merges the watershed geometry.
- Python baseline: use this repository through its `uv` environment. Open
  `HydroFabric(source, cache_dir=...)`, then call
  `delineate(fabric=fabric, target=Point(lon, lat), snap_onto=rivers,
  radius=1000, refine=False, merge=True)`. Supply local river candidates covering
  the full search radius. Select matching catchment and river tiers explicitly.

Use the hosted GRIT roots:

- Rust: `https://basin-delineations-public.upstream.tech/grit/hfx-v0.3.0/`
- Python: `https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0/`

The deprecated checkout at `../deprecated/pourpoint` is useful source evidence;
its pinned commit `60e3d6e383546ea6ed20e68fe4d77959c816a48c` is not a replacement
for the agreed released-package baseline. Verify behavior against that release.
The existing Rust diagnostic benchmark does not explicitly disable refinement
and uses an obsolete hosted alias; do not reuse its defaults blindly.

## Measure completed work

Time native snapping, upstream catchment selection, geometry retrieval, and the
completed merged watershed. Use an explicit **1,000 metre** snap radius in both
APIs and disable refinement. Include Python river-candidate acquisition and
materialization inside timed work, rather than preparing those inputs for free.
Exclude export and separate downstream river-selection operations. Use the same
input coordinates in both stacks; disclose API limitations rather than silently
changing the measured operation.

Measure two conditions:

1. **Cold start:** fresh application caches, timing engine/fabric opening through
   the first complete result. Report opening separately as well as total time to
   result; initialization deferred until delineation remains inside the total.
2. **Repeated use:** repeat the query in the same open engine/fabric, excluding
   opening. Describe observed cache reuse and available evidence; repeated calls
   do not by themselves prove every required read hits cache. Missing complete
   Rust cache telemetry must not block useful repeated-use timings: disclose the
   observability limit and do not label unverified runs as all-cache-hit.

Imports are outside timing. A fresh application cache does not imply a cold OS
or CDN cache. Keep result validation outside timed work. Do not compare traced
Rust calls with untraced Python calls or include dependency installation/builds.

Use a small fixed general-baseline set spanning small, medium, and large upstream
catchment counts across three regions. Run targets sequentially. Target
selection, repetition counts, and cache isolation mechanics are implementation
choices; five repeats in the discussion was illustrative, not a requirement.
Avoid competing benchmark jobs and record relevant machine/network limitations.
Parallel batch throughput is outside scope.

## Establish comparability before interpreting speed

The old GRIT publication has 22,337,300 units, with segment catchments at level 0
and reach catchments at level 1. The inspected Rust default selects its maximum
level, hence reach catchments. The current spatial HFX 0.4.0 publication has
22,419,857 catchments across component, segment, and reach tiers. Both derive
from GRIT v1.0, but normalization differs; identical numeric IDs or level labels
are not sufficient evidence of equivalent work.

Check source catchments, tiers, selected outlets, upstream source membership, and
merged footprints enough to detect materially different basins. Native snapping
is deliberately retained: Rust ranks by weight/mainstem first, while Python picks
the nearest candidate. Rust associates the selected river feature with a unit;
Python uses snapped-coordinate containment. Record resulting differences rather
than force either stack to imitate the other. Account for geometry cleaning and
explain any numerical tolerance, without requiring exact membership or geometry
equality to report a useful impression. Do not treat Rust as an unquestionable
oracle or sell a faster but materially different watershed as a speedup. Report
failures and unmatched targets separately, not hidden in aggregate timing.

Use the current [operation conventions](../../docs/operations.md) and HFX's
[final GRIT delivery evidence](https://github.com/pourpoint-tools/hfx/blob/main/docs/v0.4.0/grit-final-delivery.md).
Pin the actual manifest identities: the HFX 0.4.0 version string alone does not
identify its final spatial layout. GRIT data retain their CC BY-NC 4.0 terms and
attribution; do not redistribute bulk datasets as benchmark artifacts.

## Deliver evidence, not a framework

Deliver simple reproducible scripts and a concise results report. Record exact
commands, package/code and dataset identities, targets and tiers, cache settings,
machine details, and measurement limits. Preserve raw timings supporting per-target
cold totals, opening times, repeated-run variability, and Python/Rust ratios.
State clearly where Python is faster, comparable within observed variability, or
slower; do not hide differences behind one aggregate winner.

Keep the implementation proportionate and use project-native tools. No general
benchmark framework, dataset recompilation, exhaustive global audit, production
changes, optimization, or new public API is required. Success is a reproducible
major-regression screen with honest limits, regardless of which stack wins.
Distinguish timing observations from comparable-basin speed conclusions. Preserve
useful timings even where comparability or cache evidence is incomplete, and
state which conclusions those limits prevent.
