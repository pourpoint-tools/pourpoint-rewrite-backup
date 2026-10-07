# Real Hetzner range comparison

**Prefer B for geometry-heavy, scattered and combined-batch reads in this client/provider setting.**
Its smaller transfers outweighed extra requests. Do not claim a blanket winner:
A and B had similar fresh-reader times for small selections, and A's first calls
were faster in the four smaller cases. The published index is the main startup
improvement for both. This supports selective-access research, not a production
format change or resuming #3.

## What ran

The existing readers accessed the already uploaded, verified objects at Hetzner
`fsn1.your-objectstorage.com`. No objects were uploaded, modified or listed.
A three-reader smoke passed before **42 sequential fresh workers**: seven unchanged
workloads, two repetitions, rotated reader order and reversal in repetition two.
Each worker retrieved its workload twice; the existing combined batch was included.
All **192 full-run witnesses** (plus six smoke witnesses) matched the original
independent full-source-scan reference exactly: every column, null, WKB, schema
metadata, caller order and duplicate. Verification and witness serialization were
outside timing/memory boundaries; selections/reference were not regenerated.

## Fresh-reader time through first retrieval

Seconds, arithmetic mean of two repetitions (also their median). Each cell is
**0.3.0 / A / B**. Includes opening, preparation and first retrieval.

| Selection | Real Hetzner seconds |
| --- | --- |
| Single | 64.482 / 3.107 / 3.301 |
| Small upstream | 65.664 / 4.220 / 4.079 |
| Larger upstream | 61.479 / 3.553 / 3.645 |
| Overlapping | 62.111 / 3.426 / 3.587 |
| Scattered | 73.955 / 17.773 / 9.820 |
| Large geometry | 70.308 / 8.992 / 4.586 |
| Combined batch | 72.000 / 18.464 / 10.852 |

Baseline preparation made **924 GETs** per worker for 22,751,648 body bytes.
A/B preparation used **6/7 requests** and 202,900/268,436 bytes, without downloading
the full lookup. Opening added five requests for each reader, including all
HEAD, manifest and footer/schema reads. Across all workloads, average
open-plus-prepare was **62.730 / 2.638 / 2.213 seconds** (0.3.0/A/B). Candidate
bounded fingerprints and directory reads are charged, not hidden setup.

## Retrieval after preparation

Seconds and MiB (2^20 bytes), mean of two repetitions. Every cell is **A / B**.
Request and byte counts were identical between repetitions and between first and
repeat calls: no retained lookup/data windows concealed repeat traffic.

| Selection | First seconds | Repeat seconds | Requests/call | Response body MiB/call |
| --- | --- | --- | --- | --- |
| Single | 0.465 / 0.962 | 0.406 / 0.442 | 2 / 2 | 3.96 / 3.12 |
| Small upstream | 1.525 / 1.936 | 0.514 / 0.490 | 3 / 3 | 7.26 / 6.13 |
| Larger upstream | 0.842 / 1.382 | 0.977 / 0.712 | 2 / 2 | 12.71 / 10.24 |
| Overlapping | 0.765 / 1.372 | 0.668 / 0.778 | 2 / 2 | 9.02 / 7.09 |
| Scattered | 15.295 / 7.650 | 14.269 / 6.120 | 56 / 57 | 330.73 / 112.19 |
| Large geometry | 6.399 / 2.443 | 6.584 / 0.776 | 3 / 5 | 164.28 / 16.97 |
| Combined batch | 15.779 / 8.638 | 15.676 / 7.125 | 57 / 61 | 349.01 / 141.59 |

B reduced first-call body bytes substantially in the heavy cases despite more
requests. Tiny reads still pay the lookup-before-data dependency, so smaller bytes
do not ensure lower latency. Once baseline preparation is already paid, its
single-unit repeat averaged **0.256 seconds**, versus **0.406/0.442** for A/B.
Fresh-reader and already-prepared results answer different questions.

## Accounting, identities and limits

- Full study: **14,624 actual requests** (140 HEAD, 14,484 GET),
  **8,691,110,176 response body bytes**, zero failed attempts or failed workers.
  Smoke added 962 requests and 49,345,288 bytes. These are not header/TCP/TLS bytes.
- Recorded client in-flight overlap reached **four**, bounded by the same four-way
  pool and prefetch windows. No service logs were available; **server overlap is
  unknown**. Service request IDs are retained, but not authorization headers,
  signatures, tokens or credential values.
- The same 64 KiB coalescing gap and 8 MiB target apply to all readers. Oversized
  blocks remain whole; the largest response was 170,157,571 bytes. This is not a
  hard 8 MiB response or memory cap. All range plans and requests remain in raw logs.
- Original SHA-256/size provenance matches the completed upload-verification receipt.
  Every consumed HEAD matched receipt size and opaque ETag before bytes were trusted;
  every GET used If-Match and the existing strict 206/Content-Range checks.
  **ETags are not SHA-256 hashes.** Startup did not hash/download whole remote files.
- Botocore only resolved profile `hetzner-pourpoint` and signed requests; every
  worker recorded `shared-credentials-file`, which requires no SDK network access.
  HTTPX carried all data requests with retries disabled, no redirects, no artificial
  delay, four pooled connections and bounded 60-second network-operation timeouts.
- Base imports and local selection/receipt parsing were excluded. Lazy authentication
  imports, profile resolution, client setup/signing and **all remote work** were
  charged. Through-first sums open + prepare + first call, excluding snapshot work.
- Fresh-process peak client RSS reached **2.05/1.94/0.33 GB** (0.3.0/A/B).
  These process-lifetime peaks include imports/prior phases, both retained outputs
  and second-call decoded geometries; they are not phase-only allocations.
- Apple M4 Max, 68,719,476,736 RAM bytes, macOS 15.7.3. Client site, network route/
  bandwidth, provider cache/load and other laptop activity were uncontrolled and
  unrecorded. There was no fresh awake/plugged-in confirmation during the study.
  Only two repetitions ran. This is evidence for this client/provider setting,
  not a general cloud-performance proof.

[Compact records](runs.jsonl) and [provenance](provenance.json) pin the measured
code, receipt, input/contracts, settings, reference and raw-result hashes.
Detailed request logs and witnesses remain at the external artifact paths in
provenance; no source geometries or lookup arrays are published here.
The [local](../results/README.md) and [loopback HTTP](../http_results/README.md)
results are unchanged. [Run instructions](../README.md#real-hetzner-follow-up)
reuse the existing runner. Full measured code is `47f4a8f438e58e05e22baa93aa92f21a0fdf2aaf`;
later commits only publish this evidence.
