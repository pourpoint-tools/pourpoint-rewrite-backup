# Catchment lookup and layout experiment

Standalone research for [#19](https://github.com/pourpoint-tools/pourpoint/issues/19).
No production reader or HFX release. #3 stays paused. The published
[vision](../../planning/visions/2026-10-07-hfx-selective-access-reader-benchmarks.md)
defines the comparison. Results will accompany this harness.

## Run locally

Use the complete immutable local HydroBASINS source and verified candidate bundles.
Do not upload them. Keep attribution beside the source. The candidates are private
experimental inputs, not portable dependencies or public fixtures.

```sh
cd experiments/catchment_read
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run ty check access.py evidence.py measure.py run_local.py conftest.py test_access.py test_evidence.py test_measure.py
# Set SOURCE, CANDIDATES and OUTPUT to directories outside Git.
uv run python evidence.py prepare "$SOURCE" "$OUTPUT/prepared"
# Commit all measured code first. This command refuses a dirty checkout.
uv run python run_local.py --source "$SOURCE" --candidates "$CANDIDATES" \
  --prepared "$OUTPUT/prepared" --output "$OUTPUT/measurement"
```

Preparation generates selections once from unchanged graph IDs, then scans all
source catchments independently to create reference rows. None of this work runs
in a measured worker. Each worker opens only one reader, prepares it explicitly,
retrieves one workload twice, then decodes second-call WKB. Witness serialization
comes after all timings and memory snapshots. Verification runs in a separate
process and compares every output directly with the independent reference.

## Deliberately small access contract

`CatchmentReader.retrieve(list_of_requests)` returns all source fields, including
exact WKB, in caller order with duplicates. Any absent ID raises `KeyError` for
the whole call. Empty requests return empty typed tables. A batch deduplicates
IDs and reads each needed lookup/data block once. A new call rereads its lookup
and data blocks. Only metadata and the baseline locator remain cached.

The baseline reads **only IDs** to build its independent sorted locator. It never
uses graph row positions. Candidate preparation checks sizes and bounded first/
last 64 KiB fingerprints for the three consumed artifacts, plus lookup schema
binding. It reads the small directory, IPC schema and footer, not all lookup
numeric arrays. An explicit `--experimental` flag is required for wrapper manifests.
Trusted immutable manifests/bundles are assumed. Bounded checks do not detect
same-size interior corruption and are not comprehensive validation.

All paths share grouping, compact Arrow `take`, ordered output and instrumentation.
Parquet uses `pre_buffer=False`, IPC and Parquet use `use_threads=False`; no mmap,
transport adapter, cross-call block cache or general reader framework is added.
PyArrow owns footer/range access. FileIO counts cover local bytes returned,
including metadata and fingerprints. They are not physical disk or network I/O.

## Measurement boundaries

Forty-two sequential fresh workers: seven workloads, three readers, two repetitions.
Reader order rotates by workload and reverses in repetition two. Each worker makes
a first and repeated call. The batch combines all individual requests, including
overlap, so shared reads can be compared with separate calls. Filesystem-cache
state and other laptop activity are uncontrolled; these are not cold-disk tests.
No coordinated competing benchmark jobs are permitted.

Opening, preparation, first and repeated retrieval are timed separately. The
reported total-to-first is their phase sum, excluding snapshot bookkeeping.
Geometry decoding is a separate last phase. Import time and selection JSON parsing
are excluded. Both calls' selected tables remain live through READY, along with
second-call decoded geometries. This retention is intentional and recorded.

Memory includes endpoint RSS, **process-lifetime** peak RSS and Arrow allocator
current/peak counters. Peaks include imports and all prior measured phases, not
reference generation or witness serialization. They are not phase-only peaks.
Numeric locator ownership and selected backing buffers are counted. Python/reader
metadata and decoded native allocation sizes are not separately measured.
Materialized bytes sum logical Arrow blocks read, not simultaneous resident bytes;
largest block backing size is also reported. Requested logical bytes count the
returned fields including duplicates, not transport. Remote bytes are unmeasured.

These are post-traversal access tests, not delineation, cloud or GRIT-scale results.
