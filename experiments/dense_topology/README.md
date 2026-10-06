# Dense runtime topology experiment

Compare numeric ID lookup on each visited edge with one-time conversion to row
positions. Both paths use the same inclusive BFS, boolean visited array, uint32
queue, same-level checks and original-ID output. They share an Arrow graph and
sorted numeric ID lookup. Dense mode adds one uint32 array and **retains the ID
edge array** for correctness comparison; it does not replace the baseline graph.
No production modules change.

## Reproduce

Run from this directory with `uv sync --locked`. Use the complete unchanged
HydroBASINS HFX 0.3.0 local copy and its coordinator download receipt. Preserve
original artifacts and notices. Do not redistribute the dataset. `DATA` is its
directory; `RECEIPT` is the JSON download receipt; `OUT` is outside Git.

```sh
uv run python select_queries.py --data "$DATA" --receipt "$RECEIPT" \
  --commit "$COMMIT" --output "$OUT/queries.json"
uv run python measure.py --data "$DATA" --receipt "$RECEIPT" \
  --commit "$COMMIT" --mode id --ids $IDS --repeats 5 \
  --concurrent-activity "describe other work" --output "$OUT/id.json"
uv run python measure.py --data "$DATA" --receipt "$RECEIPT" \
  --commit "$COMMIT" --mode dense --ids $IDS --repeats 5 \
  --concurrent-activity "describe other work" --output "$OUT/dense.json"
```

`COMMIT` must be the clean experiment code commit. Set `IDS` to the selector's
printed IDs. Each layout runs in a fresh process. Repeat both commands, reversing
layout order, for a second comparison. Exact recorded commands and dependency
versions are in each result JSON. Query selection uses graph-only weak components
on the declared tree, at the finest level, with smallest-ID tie breaks. Queries
cover the largest component terminal, terminal nearest 1,000 component rows, and
a headwater. Selection is outside measured work.

## Read the measurements

- Seconds and bytes are explicit. Local state is warm/OS-cache-uncontrolled.
  Startup uses wall clock minus process creation time and includes Python imports,
  not the `uv` launcher. Load excludes index construction. Compact-and-index
  includes Arrow chunk consolidation, schema/identity checks and sorting.
- Conversion checks all references and levels in 65,536-row blocks. ID queries
  check edges as visited. Neither path performs complete HFX conformance checking.
- Every query allocates and resets its own visited array and queue. Timings include
  those allocations and original-ID output, not sorting/hashing/oracle checks.
  First-call and five repeated-call times are separate. No workspace is reused.
- NumPy graph views do not add storage to their retained Arrow buffers. The
  lookup arrays and dense edges own their bytes. Arrow logical bytes and retained
  backing bytes are separate. Query queue capacity is all rows, not selected rows.
- Each phase records endpoint RSS, sampled RSS peak (5 ms requested interval),
  process-lifetime `ru_maxrss`, and Arrow pool counters. Sampling can miss short
  peaks. Process peaks are not per-phase allocation totals. Temporary conversion
  memory is covered by sampled RSS; individual temporary arrays are not measured.
- A retained sorted first result per query supports exact repeat checks; later
  phases include these explicitly small correctness buffers. SciPy BFS runs only
  after all timed traversals, using a separate intersection-based ID mapping.
  Exact sorted original-ID arrays must match for every query. Checksums use sorted
  little-endian int64 IDs. Synthetic tests include a reconvergent DAG and sparse,
  shuffled IDs. This is not a tree-only traversal implementation.
- Parents, catchment alignment, null catchment attributes, geometry, snapping,
  union, area, raster work and complete conformance validation are excluded.
  No cross-level traversal or parent semantics are implemented. Remote traffic is
  **not measured**. Initial download is recorded separately by the coordinator.

## Saved HydroBASINS results

Code commit: `422babb3fb4c89dc623eaaa808f03653dd42bd43`.
Graph object SHA256:
`13a41abe69ff212343cf7aba8631a4bfd79a328d8be595990652e89321a05bd7`.
All four runs read 3,786,228 rows and 3,504,135 edges. Exact SciPy membership
checks passed for every query and exact repeat checks passed on every call.

The initial [ID](results/id-overlap.json) and
[dense](results/dense-overlap.json) pair overlapped Arrow remote reads.
The final pair below ran dense then ID, with no coordinated competing experiment.
Other laptop activity was uncontrolled. No cold-cache claim is made.

| Final local run | Load (s) | Compact/index (s) | Convert (s) | Ready RSS (bytes) |
|---|---:|---:|---:|---:|
| [id](results/id-isolated.json) | 0.056882 | 0.150839 | not applicable | 402,292,736 |
| [dense](results/dense-isolated.json) | 0.055837 | 0.151008 | 0.065389 | 420,462,592 |

| Query ID | Selected rows | ID first (s) | ID repeat median (s) | Dense first (s) | Dense repeat median (s) |
|---|---:|---:|---:|---:|---:|
| 6120007000 | 44,962 | 0.048507 | 0.047225 | 0.016374 | 0.015997 |
| 5120068450 | 1,004 | 0.001271 | 0.001198 | 0.000536 | 0.000527 |
| 1120000010 | 1 | 0.000155 | 0.000161 | 0.000150 | 0.000136 |

Repeat medians are calculations from the five saved repeated-call times; all
other table values are saved observations rounded to six decimal seconds.
[Query selection](results/queries.json) records the graph-only selection rule
and IDs. Each JSON contains exact command arguments, object ETags/sizes/SHA256,
package versions, reader settings and memory samples summarized by phase.

Both modes retain 81,040,276 bytes of Arrow graph/level backing buffers
(81,040,272 logical bytes) and 45,434,736 bytes of owned lookup arrays.
Dense mode additionally retains 14,016,540 bytes of converted edge positions.
Each query allocates 3,786,228 visited bytes and 15,144,912 queue bytes, plus its
selected-ID result. These are buffer sizes, not total process RSS. Individual
phases record sampled peaks and lifetime peaks separately from Arrow allocation.

## Validation

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run ty check --no-respect-ignore-files topology.py measure.py select_queries.py test_topology.py
```

The explicit type-check paths avoid parent `.worktrees/` ignore rules. Tests were
written first: the initial run failed to import the absent `topology` module;
implementation made those cases pass. Independent review must inspect numerical
correctness and traceability, not select a preferred architecture.

Published scope: [vector access experiments](../../planning/visions/2026-10-06-vector-access-experiments.md).
Pinned contract: [HFX 0.3.0](https://github.com/CooperBigFoot/hfx/blob/c18a8a6a115e2045080d8752279d39db2b9b29b6/spec/HFX_SPEC.md).
Historical upstream BFS investigation: Rust baseline
`60e3d6e383546ea6ed20e68fe4d77959c816a48c`, `crates/core/src/algo/upstream.rs`.
