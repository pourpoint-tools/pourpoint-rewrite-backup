# HFX selective-access reader benchmarks

Find out whether publishing a catchment lookup removes costly reader preparation,
and whether changing catchments from Parquet to Arrow IPC improves selected
retrieval further. Return evidence for the maintainer to decide what to do next,
not a predetermined format winner.

This vision serves [Pourpoint #19](https://github.com/pourpoint-tools/pourpoint/issues/19).
It is standalone research, not a Program Effort or production implementation.
[#3](https://github.com/pourpoint-tools/pourpoint/issues/3) remains paused: do not
revise its vision or resume its implementation as part of this work.

## The comparison

Build three experimental access paths behind one interface: given unit IDs,
return equivalent attributes and selected geometries.

- **HFX 0.3.0:** construct an independent catchment locator from catchment IDs
  only, then retrieve other fields on demand. Do not assume graph/catchment row
  alignment, load worldwide attributes, or perform comprehensive validation at
  startup.
- **Candidate A (`a-index`):** selectively read the published lookup and then
  the addressed Parquet row groups. The original catchment bytes are unchanged.
- **Candidate B (`b-arrow`):** selectively read the published lookup and then
  the addressed Arrow IPC catchment batches, with attributes and exact WKB together.

Compare **0.3.0 versus A** to isolate the published-lookup benefit and **A versus
B** to isolate the catchment-layout benefit. Share genuinely common behavior;
do not build three engines or a general reader/cache framework.

Follow the candidates' directory, exact-ID matching, physical-address and bounded
identity-check contracts. Read only needed lookup batches and group shared data
block reads. Do not construct a global candidate locator, scan all lookup batches,
or hash entire artifacts during measured startup. Full correctness verification
belongs outside timed access. Candidate wrapper manifests require explicit
experimental opt-in; `0.4.0` here is not an approved HFX release.

## Evidence to return

Use deterministic, shared selections covering one unit, small and larger upstream
watersheds, overlapping requests, scattered requests, and a selection touching a
large geometry block. Include repeated calls and a known batch of requests so
shared reads are visible. Generate upstream-ID selections once from unchanged
connectivity; keep that common preparation separate from reader measurements.

Measure opening, locator/index preparation and first retrieval both together and
by phase, followed by repeated retrieval. Do not hide baseline preparation before
timing or shift it into an unreported first query. Record:

- elapsed times and exact reader settings;
- instrumented read counts and bytes returned at the I/O boundary;
- retained buffers, process memory and peak memory, with clear measurement scope;
- geometry decoding separately from retrieval;
- logical requested bytes, materialized data, and transport bytes only where
  actually measured, without treating one as another.

Use sequential fresh processes, modest repetitions and varied reader order.
Filesystem-cache state is uncontrolled, not “cold disk.” Local laptop timings
are observations, not controlled cloud performance claims. Avoid coordinated
competing benchmark jobs and disclose relevant limitations.

Check every measured selection against an independent source reference for exact
IDs, attributes/nulls and source WKB. Keep reference work outside the timed path
and measured memory boundary. Define absent-ID, caller-order and duplicate-request
behavior consistently across the three paths. Use focused small fixtures for
shuffled/non-row IDs, missing IDs and lookup/data-block boundaries. Follow the
repository's uv, typing and test-first conventions.

Return a short comparison, reproducible commands, small machine-readable results,
correctness evidence and limitations. Record measured code and contract/source
commits, dataset identities, settings and dependency versions. Explain whether
results justify remote follow-up and revisiting #3; do not make those changes.
Negative or inconclusive findings are valid outcomes.

## Starting evidence and inputs

Read the [earlier research comparison](../../experiments/README.md) and
[polygon follow-up](../../experiments/selective_access/results/followup/README.md).
Explicit row-group lookup already reduced overread compared with filtering, but
one large group still materialized about 245.5 MB of WKB for about 2.46 MB selected.
Those experiments loaded worldwide attributes during preparation. Reuse useful
runtime and measurement findings, not that unnecessary baseline cost or whole
modules by default.

The HFX candidate producer has already verified both local bundles against the
source, including exact ordered B values/WKB and A's unchanged Parquet bytes.
That is producer correctness evidence, not reader correctness or speed evidence.
Read these contracts before implementing:

- [Candidate reader contract](https://github.com/pourpoint-tools/hfx/blob/main/docs/selective-access-candidates.md)
- [Candidate identities and verification](https://github.com/pourpoint-tools/hfx/blob/main/docs/candidate-results.md)
- [Pinned HFX 0.3.0 reference](https://github.com/CooperBigFoot/hfx/blob/c18a8a6a115e2045080d8752279d39db2b9b29b6/spec/HFX_SPEC.md)

In ordinary sibling checkouts, the first two documents are
`../hfx/docs/selective-access-candidates.md` and `../hfx/docs/candidate-results.md`.
The old `../hfx/spec/HFX_SPEC.md` path mentioned by Pourpoint scaffolding is absent
in the redesigned HFX repository; use the pinned 0.3.0 reference above instead.
Pin the candidate-document revision actually used in the experiment records.

Use the complete local HydroBASINS dataset (3,786,228 units), not a country subset:

- Source: `/Users/nicolaslazaro/.prime/agent/session-artifacts/01a1129b-ff00-7678-9ab2-c76e53fe810a/vector-access/data/hydrobasins/`
- A: `/Users/nicolaslazaro/.prime/agent/session-artifacts/hfx-candidates/hydrobasins/candidate-a-index/`
- B: `/Users/nicolaslazaro/.prime/agent/session-artifacts/hfx-candidates/hydrobasins/candidate-b-arrow/`

The candidates' parent directory contains `verification.json`. These paths were
available during discovery; treat them as local inputs, not portable dependencies.
Preserve all source/candidate artifacts and attribution. Write outputs separately,
keep bulk data outside Git, and **do not upload or redistribute these bundles**.

## Boundaries and implementation freedom

These are post-traversal catchment-access benchmarks, not complete delineation
timings. Graph acquisition, snapping, raster work, geometry repair, dissolve and
area calculation are outside scope. Do not change formats, source normalization,
production APIs or the existing engine contract. Preserve unrelated work.

Local results cannot establish cloud latency or GRIT-scale performance. A small
instrumented localhost range test is optional if needed to verify selective reads;
it is not a cloud benchmark and does not authorize uploading data.

Keep the harness proportional to these two comparisons. Reader structure, mature
library choices, concrete selection sizes, modest repetition counts and
instrumentation mechanisms remain implementation choices. Report any caching or
reuse policy explicitly rather than silently giving one reader extra preparation.
