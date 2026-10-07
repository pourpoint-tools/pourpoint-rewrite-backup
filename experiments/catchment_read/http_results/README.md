# HTTP range comparison

The published index removes the global ID scan. B still helps when it avoids
large geometry blocks, but its small-read advantage largely disappears with
request latency. **Test a representative real object store next, using data that
may legally be hosted there.** These loopback results do not justify another
format change, uploading HydroBASINS, or resuming #3.

## What ran

84 sequential fresh processes: three readers, the same seven selections, two
repetitions, and two service-delay settings. All **384 output witnesses** matched
the existing independent source reference exactly, including attributes, nulls,
schema metadata, WKB, order and duplicates. Datasets and candidates were unchanged.

The second setting adds **20 ms of simulated service delay to every request**,
including HEAD. This is not WAN latency, bandwidth throttling, TLS or a cloud
object-store model. Filesystem caches and other laptop activity were uncontrolled.
Readers used four pooled connections and bounded explicit prefetch before decode,
not Arrow's serialized Python-file reads as a substitute for concurrency.

## Fresh-reader time through first retrieval

Seconds, median of two runs. Each cell is **0.3.0 / A / B**; includes opening,
preparation and the first retrieval. The machine records include all seven cases.

| Selection | Localhost | Localhost + simulated 20 ms/request |
| --- | --- | --- |
| Single | 0.606 / 0.061 / 0.050 | 7.678 / 0.430 / 0.435 |
| Small upstream | 0.618 / 0.067 / 0.060 | 7.476 / 0.434 / 0.447 |
| Larger upstream | 0.645 / 0.082 / 0.073 | 7.512 / 0.434 / 0.466 |
| Overlapping | 0.622 / 0.072 / 0.061 | 7.837 / 0.440 / 0.447 |
| Scattered | 1.146 / 0.621 / 0.303 | 8.502 / 1.361 / 1.073 |
| Large geometry | 0.899 / 0.356 / 0.088 | 8.036 / 0.700 / 0.485 |
| Combined batch | 1.165 / 0.639 / 0.361 | 8.302 / 1.415 / 1.214 |

Baseline preparation performs **924 ID-column GETs**, returning 22.75 MB, with
four requests actually overlapping. A/B preparation needs **6/7 requests** and
0.203/0.268 MB; neither scans the full 60.60 MB lookup. Their opening adds five
requests and 2.517/0.030 MB respectively. Baseline opening adds five requests and
2.515 MB. All metadata, manifests, directory and bounded fingerprints are charged.
Across workloads, median open-plus-prepare is 0.576/0.030/0.020 seconds locally,
and 7.689/0.304/0.325 seconds with simulated delay.

## Retrieval after preparation

B's smaller blocks still win the expensive cases despite extra requests:

| Selection | A / B requests per call | A / B response body MB | A / B repeated seconds, simulated delay |
| --- | --- | --- | --- |
| Scattered | 56 / 57 | 346.80 / 117.64 | 0.952 / 0.687 |
| Large geometry | 3 / 5 | 172.26 / 17.79 | 0.315 / 0.108 |
| Combined batch | 57 / 61 | 365.97 / 148.47 | 0.984 / 0.793 |

For a repeated single-unit call under simulated delay, 0.3.0/A/B take
0.038/0.069/0.068 seconds. Once baseline locator construction is already paid,
it needs one data request; A/B need lookup then data. Small-read B is not a clear
latency winner over A. Fresh-reader gains and steady-state gains are different.

Batching reduces retrieval requests from **35/68/71 to 32/57/61**, and body bytes
from **511.89/553.60/163.31 MB to 330.54/365.97/148.47 MB** (0.3.0/A/B). The
comparison sums the six non-batch workloads' retrievals, excluding their startup.
Every repeated call had the same request and body-byte counts as its first call:
no lookup/data cache hid later traffic.

## Accounting and limits

- Client and server logs reconcile every request ID, method, path, status, range
  and body-byte count after server shutdown joins the handlers. Observed maximum
  overlap was four; single-range phases naturally stayed serial. Logs include
  timestamps. Counts include HEAD; bytes are response bodies, **not TCP/header wire bytes**.
- All readers use a 64 KiB gap and 8 MiB coalescing target, fetching at most four
  merged spans per window. Selected adjacent blocks coalesced without gap-byte
  amplification in these inputs (1.000× planned block spans). This is not 1×
  requested-row bytes: whole blocks still overread heavily.
- Oversized source blocks remain whole and are recorded explicitly. The largest
  response was 170.16 MB. The 8 MiB target is not a hard memory or source-block cap.
- Fresh-process peak RSS reached 2.27/1.98/0.34 GB (0.3.0/A/B). These peaks include
  server threads/buffers, imports and earlier phases, not just the retrieval.
  Both selected outputs stay live; second-call geometry decoding is separate.
- Strong stat ETags pin these trusted immutable local files across requests;
  they are not content SHA-256 digests. Bounded fingerprints are not full integrity
  validation. Dictionary IPC is explicitly unsupported in this narrow experiment.
- Only two repetitions were taken. Hardware, filesystem cache, timer scheduling,
  socket behavior and uncontrolled laptop activity limit timing conclusions.
  The server enables TCP_NODELAY to avoid a localhost delayed-ACK artifact.

[Compact per-run records](runs.jsonl) and [provenance](provenance.json) pin code,
contracts, input identities, settings, versions and raw evidence hashes.
The earlier [local results](../results/README.md) remain unchanged. Detailed
request logs and all witnesses stay in the external artifact directory; no
HydroBASINS geometry or lookup arrays are published here.

<details>
<summary>Reproduce and inspect</summary>

From a clean checkout, use the same immutable input paths recorded in provenance:

```sh
cd experiments/catchment_read
uv sync --locked --python 3.14.6
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run ty check .
uv run python run_http.py --source "$SOURCE" --candidates "$CANDIDATES" \
  --prepared "$PREPARED" --output "$NEW_OUTPUT"
```

`PREPARED` is the existing selective-reader benchmark's `prepared` directory;
its selections and reference hashes are checked, not regenerated. The runner
refuses a dirty checkout or existing output directory. It rotates reader order,
reverses the second repetition, runs no concurrent benchmark workers, and invokes
the existing independent verifier after every timing/memory worker finishes.
Each worker serves only `127.0.0.1`, then closes its clients and server. The recorded
command uses the pinned measured code commit; later changes only add results or
rename a test. Source/candidate producer verification was not rerun.

</details>
