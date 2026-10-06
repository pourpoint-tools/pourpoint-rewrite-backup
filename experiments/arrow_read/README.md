# Compact Arrow graph reads

This isolated experiment reads the unchanged HFX 0.3.0 graph projection `id`,
`level`, `upstream_ids`. It compares serial reads (`pre_buffer=False`,
`use_threads=False`) with Arrow coalescing/prefetch and threaded decoding
(`pre_buffer=True`, `use_threads=True`). Both use `buffer_size=0`, no memory map,
the same combined Arrow buffers, and owned NumPy int64 sorted IDs and uint32 row positions.
No production engine code changes.

Run from this directory with `uv sync --locked`, then `uv run pytest -q`,
`uv run ruff check .`, `uv run ruff format --check .`, and
`uv run ty check read_graph.py test_read_graph.py`. Explicit filenames ensure ty
checks the experiment even when its worktree is below an ignored directory.

## Reproduce

Use the coordinator's complete immutable HydroBASINS copy, not a country extract.
Keep original attribution/notices with it. No bulk data is included here.
`results/identity.json` records the graph key, size, ETag, and local SHA256.
Set `DATA` to that dataset directory and `COMMIT` to the code commit in a result.
Run each command in a fresh process, replacing `MODE` with `serial` or `prefetch`:

```sh
uv run python read_graph.py "$DATA/graph.parquet" --mode MODE \
  --identity results/identity.json --commit "$COMMIT" \
  --queries 6010000010 2070018460 2070694550 \
  --concurrent-activity "describe observed concurrent work" --output /outside/git/local.json
uv run python read_graph.py \
  pourpoint-hfx/hfx/hydrobasins-pfaf1-12-nested-global-snap-2026-07-20/graph.parquet \
  --remote --mode MODE --identity results/identity.json --commit "$COMMIT" \
  --queries 6010000010 2070018460 2070694550 \
  --concurrent-activity "describe observed concurrent work" --output /outside/git/remote.json
```

Remote authentication uses local AWS profile `hetzner-pourpoint`, passed privately
to native Arrow S3. No request logs are enabled. Actual remote requests, retry
counts, and response bytes are **not measured**. Compressed column-chunk bytes
are storage counts, not traffic. Initial download is charged by the coordinator
separately. Local reads are warm/OS-cache-uncontrolled, not cold disk tests.

Queries are deterministic: first graph row and first two rows with nonempty edges
in row group 10. Exact sorted membership is checked against an independent Arrow
filter/flatten fixed-point traversal; saved hashes encode little-endian int64 IDs.
Synthetic tests cover reconvergence, identity, missing IDs, and cross-level edges.
Runtime checks cover types, nulls, positive unique IDs, and query-reached edge
references/levels. Full graph, parent, and geometry conformance is excluded.

## Measurement definitions

- Seconds use `perf_counter`. Imports exclude interpreter startup; filesystem
  setup, footer opening, projected read, and compact/index/runtime checks are
  separate. Query timings exclude independent reference checks and serialization.
- Graph referenced bytes use Arrow `Table.nbytes`; retained bytes walk buffers to
  their root parents and deduplicate addresses. NumPy graph arrays are zero-copy
  views. Sorted IDs are owned int64; positions are owned uint32 with an explicit
  capacity check. The temporary native-intp argsort output is discarded.
- Query workspace is a labeled calculation: `rows * (1-byte visited + 8-byte
  stack)`. Output bytes are separate; transient selection/sort copies are not
  included in that calculation. Process peak includes these allocations.
- RSS after load uses psutil. Peak is process-reported `ru_maxrss` (bytes on
  macOS, KiB converted to bytes elsewhere), including imports and correctness.
  Arrow live and peak pool allocations are not process RSS.
- Parents/attributes and catchment locator are excluded (zero allocated buffers),
  not a claim that a complete engine needs none. Snap, geometry decoding, repair,
  union, area, and rasters are excluded.

Pinned investigation evidence: HFX specification at
`CooperBigFoot/hfx@c18a8a6a115e2045080d8752279d39db2b9b29b6`, and Rust baseline
`CooperBigFoot/pourpoint@60e3d6e383546ea6ed20e68fe4d77959c816a48c`:
`reader/graph.rs`, `algo/upstream.rs`, `session.rs`. The old graph uses per-node
vectors and whole encoded remote artifacts. This experiment does neither.
