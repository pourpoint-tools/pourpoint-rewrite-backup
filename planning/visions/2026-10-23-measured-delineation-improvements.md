# Measured delineation improvements

Make delineation faster through measured, targeted improvements without changing
its results or redesigning the engine. The [GRIT benchmark](../../docs/grit-delineation-benchmark.md)
is an opportunity to review algorithms, array operations and use of native
libraries, not a requirement to match Rust or achieve a fixed speedup.
Small per-query costs matter when delineating thousands of basins.

This vision completes [#51](https://github.com/pourpoint-tools/pourpoint/issues/51)
when the investigation, justified improvements and their evidence are delivered.
Publishing this document does not complete the issue. The agreed scope extends
its original investigation to adopting improvements supported by the experiments.

## Outcome

Identify the dominant costs, run focused comparisons, and adopt improvements that
produce meaningful end-to-end gains while preserving correctness. Not every
experiment must become production code. Explain rejected approaches and any
remaining costs rather than adding complexity to meet a performance target.
Weigh gains against startup cost, retained memory, dependencies and portable QGIS
delivery. Keep the public API and canonical HFX input contract unchanged.

## Starting evidence and experiments

The existing benchmark measures candidate retrieval, snapping, upstream selection,
materialization and merge with refinement disabled. It compares different dataset
and engine stacks, not languages in isolation. Use its scripts, fixed targets and
receipts as the starting point rather than creating a benchmark framework.

An exploratory offline, warm-cache timing pass found spatial row-group metadata
filtering around 34–35 ms per query, snapping around 28–46 ms, and polygon union
ranging from negligible for Australia to about 49 ms for Spain. These are local
diagnostic observations, not acceptance thresholds or published comparison results.
Re-establish the baseline on the implementation revision.

Prioritize these experiments; mechanisms remain open to the implementing agent:

- **Numerical snapping.** `rivers.py:snap` creates a Shapely Point for each
  distance evaluation, then extracts coordinates for `Geod.inv`. Compare direct
  scalar coordinate arithmetic with the current implementation, then consider
  NumPy segment arrays and array-valued geodesic calls. An in-process prototype
  removing temporary points and caching target coordinates showed promising
  full-query reductions while matching snapped coordinates, ordered IDs and
  output geometry on the three targets. Its timing had possible competing work;
  treat it as a lead, not a verified gain. Preserve WGS84 ground-distance semantics,
  straight longitude/latitude segment interpolation, endpoints, tie ordering,
  radius behavior and input validation. Do not substitute degree-space nearest
  distance merely because it is faster.
- **Array-based spatial filtering.** `spatial.py:candidate_groups` repeatedly
  walks Parquet row-group metadata in Python. Compare extracting numeric bounds
  and level statistics once per reader and filtering arrays against the current
  path. Include preparation cost and memory, and require identical candidate
  groups, especially for null or unknown statistics and conservative bounds.
- **Polygon union.** `refinement.py:merge` already delegates union to native GEOS.
  Compare suitable union algorithms, including coverage union only where its
  validity and coverage preconditions are established. HFX's tolerance-based
  topology requirements do not guarantee GEOS-compatible coverage. Include
  validation cost; do not silently repair geometry or change union semantics to
  obtain speed. Retain general union where the specialized operation is unsuitable.

NumPy, Arrow, Shapely and pyproj already provide native operations. Start by using
those effectively; replacing an array library or adding a small compiled kernel
is an option only when evidence justifies the dependency and packaging cost, not
an objective in itself. Inspect other costs if the measurements point there,
without expanding into a general engine rewrite.

## Evidence and correctness

Separate profiling from uninstrumented before/after timings. Run comparison jobs
sequentially without competing benchmark work, using equivalent inputs and
settings. Record the code revision, environment, cache state, repetitions and
measurement limits. Distinguish time spent preparing data from steady-state gains;
check that improvements do not merely move work outside the timed query.

Use the three existing targets for continuity and focused additional cases where
an optimization's correctness or scaling requires them. Record phase and complete
query timings, relevant memory or startup trade-offs, and transport behavior when
reader changes affect it. These samples do not establish large-basin or batch
scaling; make no such claims without measuring them.

Preserve selected membership, outlet decisions, source attributes, error behavior
and geometry meaning. Use existing tests and independently established expectations
for focused cases, not only agreement with the old implementation. Exact comparisons
are appropriate for behavior-preserving numerical changes; a union algorithm may
change WKB ordering without changing geometry, so verify topology, validity and
geometric equivalence rather than requiring identical serialization alone.

Follow repository test-first guidance for production changes and use `uv`, Ruff,
`ty` and the relevant tests. Keep a concise repository report of experiments,
accepted changes, measured gains, rejected options and limitations, linked from
#51. A result that rejects an optimization is useful evidence, not a reason to
force its adoption.

## Boundaries

No Rust-parity threshold, broader benchmark framework, API redesign, HFX layout
change or batching architecture overhaul. Compiled raster tracing and other
refinement optimization are separate work: refinement was disabled in the
motivating benchmark. Do not add speculative caches, GPU infrastructure or broad
library migrations without a measured need within this scope.
