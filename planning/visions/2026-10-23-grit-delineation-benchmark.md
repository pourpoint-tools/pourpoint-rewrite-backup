# GRIT delineation benchmark

Determine whether the current Python implementation with HFX 0.4.0 completes
unrefined GRIT watershed delineations as fast as the released Rust-backed
implementation with HFX 0.3.0. This compares two complete engine/dataset stacks,
not Python and Rust language performance in isolation.

This vision defines the work to complete [#47](https://github.com/pourpoint-tools/pourpoint/issues/47).
Publishing the vision does not complete the issue. Return the benchmark findings
there; this work authorizes measurement, not production optimization.

## Compare the public APIs

Use separate environments because both packages import as `pourpoint`:

- Rust baseline: install the published `pourpoint==0.4.0` with `uv`. This is the
  **package version**, supporting **HFX 0.3.0**. Use `pourpoint.Engine(source,
  refine=False)` followed by `engine.delineate(lon=lon, lat=lat, geometry=True)`.
  Standard delineation already merges the watershed geometry.
- Python baseline: use this repository through its `uv` environment. Open
  `HydroFabric(source, cache_dir=...)`, then call
  `delineate(fabric=fabric, target=Point(lon, lat), refine=False, merge=True)`.
  Select the matching catchment tier explicitly where needed.

Use the hosted GRIT roots:

- Rust: `https://basin-delineations-public.upstream.tech/grit/hfx-v0.3.0/`
- Python: `https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0/`

The deprecated checkout at `../deprecated/pourpoint` is useful source evidence;
its pinned commit `60e3d6e383546ea6ed20e68fe4d77959c816a48c` is not a replacement
for the agreed released-package baseline. Verify behavior against that release.
The existing Rust diagnostic benchmark does not explicitly disable refinement
and uses an obsolete hosted alias; do not reuse its defaults blindly.

## Measure completed work

Time upstream catchment selection, geometry retrieval, and the completed merged
watershed. Disable refinement. Exclude export, river selection, and snapping;
choose interior targets whose equivalent outlet selection can be verified in
both public APIs. If the released API cannot meet that comparison, report the
limitation rather than silently changing the measured operation.

Measure two conditions:

1. **Cold start:** fresh application caches, timing engine/fabric opening through
   the first complete result. Report opening separately as well as total time to
   result; initialization deferred until delineation remains inside the total.
2. **Repeated cache-hit use:** repeat the query in the same open engine/fabric,
   excluding opening. Verify and describe actual cache reuse; repeated calls do
   not by themselves prove every required read hits cache.

Imports are outside timing. A fresh application cache does not imply a cold OS
or CDN cache. Keep result validation outside timed work. Do not compare traced
Rust calls with untraced Python calls or include dependency installation/builds.

Use a small fixed general-baseline set spanning small, medium, and large upstream
catchment counts across several regions. Run targets sequentially. Target
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

Match source catchments, tiers, outlet selection, upstream membership, and merged
footprints. Account for geometry cleaning differences and explain any numerical
comparison tolerance. Do not treat Rust output as an unquestionable oracle or
report a faster but materially different watershed as a comparable success.
Report failures and unmatched targets rather than hiding them in aggregate timing.

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
optimization, or new public API is required. Success is a reproducible, validated
comparison with honest limits, regardless of which stack wins. If equivalence or
cache-hit conditions cannot be established, retain the evidence and identify the
blocked comparison rather than claim the speed question is answered.
