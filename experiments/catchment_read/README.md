# Catchment lookup and layout experiment

Standalone research for [#19](https://github.com/pourpoint-tools/pourpoint/issues/19).
No production reader or HFX release. #3 stays paused. The published
[vision](../../planning/visions/2026-10-07-hfx-selective-access-reader-benchmarks.md)
defines the comparison. [Results](results/README.md) compare both changes and
record the limits. [Machine records](results/runs.jsonl) retain each measured run.

## HTTP range follow-up

The HTTP experiment serves the unchanged bundles on `127.0.0.1` only.
It uses four pooled connections and explicit bounded range prefetch before Arrow
decoding. The same 64 KiB gap and 8 MiB coalescing target apply to all readers;
oversized source blocks remain whole. No lookup or data cache survives a call.
All HEAD, manifest, schema/footer, directory, fingerprint, lookup and data reads
are charged. Completed client/server request IDs and body-byte counts reconcile.

`run_http.py` runs sequential fresh workers with the original selections and
independent reference. It compares localhost with a clearly simulated 20 ms
per-request service delay. Neither run establishes cloud performance.
[HTTP results](http_results/README.md) are separate from the unchanged local results below.

## Real Hetzner follow-up

The same HTTP worker accepts `--receipt` for profile-authenticated Hetzner reads.
Botocore resolves `hetzner-pourpoint` and signs each HTTPX HEAD/GET. There is no SDK
object transport, upload, listing, redirect, retry, or presigned URL. Receipt
SHA-256 identities must match the original input provenance. Each consumed object
HEAD must match its verified receipt size and ETag before any bytes are trusted;
every GET uses If-Match and the existing strict 206/range checks. ETags are not
SHA-256 hashes. No full remote hashing occurs at startup.

From this directory, set `PREPARED`, `RECEIPT`, and `OUTPUT` to external artifact
paths, then run from a clean code commit:

```sh
uv run python run_http.py \
  --source s3://pourpoint-hfx/hfx/hydrobasins-pfaf1-12-nested-global-snap-2026-07-20/ \
  --candidates s3://pourpoint-hfx/experiments/hfx-selective-access/20261007T201153Z-74782fc0971b/ \
  --prepared "$PREPARED" --receipt "$RECEIPT" --output "$OUTPUT" --smoke
```

Smoke runs all three readers on the unchanged single selection. Inspect its exact
source witness verification and accounting before rerunning without `--smoke` at
a new output path. Full mode runs 42 sequential fresh workers: seven workloads,
three readers, two varied-order repetitions. No artificial latency is added.
Profile setup and all remote work are charged. Imports and local selection/receipt
parsing are excluded. Client overlap is measured; service overlap is unknown.
Failed workers preserve sanitized attempts and stop the run. Failed response body
bytes are unknown, so their timings are invalid and must not be mixed into results.

## Run locally

Use the complete immutable local HydroBASINS source and verified candidate bundles.
Do not upload them. Keep attribution beside the source. The candidates are private
experimental inputs, not portable dependencies or public fixtures.

```sh
cd experiments/catchment_read
uv sync --locked --python 3.14.6
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
