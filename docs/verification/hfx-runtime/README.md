# Production HFX 0.4.0 verification

The production reader opened public GRIT, selected all 326 upstream units of
terminal `22333245`, returned complete attributes and exact WKB, and decoded the
terminal polygon. Independent spatial access returned five catchments. Both
named snap paths ran: one segment-stem and two reach-stem candidates.

These are reader/runtime measurements, **not complete delineation latency**.

## First useful result

Fresh worker processes ran sequentially on an Apple M4 Max, macOS 15.7.3.
The cold run started with an absent engine graph cache. The final cached run
reused only the verified original graph, not prepared topology or open readers.
OS, CDN, network and other laptop activity were uncontrolled. A bounded direct
HTTPS reader smoke preceded measurement; “cold” does not mean a cold publication.

| Phase | Cold seconds | Cached graph seconds |
| --- | ---: | ---: |
| Manifest, directory, IPC metadata and bounded hashes | 1.634 | 1.606 |
| First topology use, including graph acquisition/hash/local decode/preparation/traversal | 25.734 | 1.102 |
| Complete records for all 326 units | 0.435 | 0.506 |
| Selected terminal polygon decode, without rereading its batch | 0.0004 | 0.0004 |
| **Total through first useful result** | **27.803** | **3.214** |

Cold: **18 requests** (13 GET, 5 HEAD), **704,028,121 response-body bytes**.
This includes one complete graph GET of **699,720,490 actual bytes**.
Cached: **16 requests** (12 GET, 4 HEAD), **4,307,631 response-body bytes**.
Only lookup batch 340 and catchment batch 6655 were selected. No worldwide
catchment attributes, IDs or geometry were scanned during opening or first use.

The old rejected initialization cost 1,360.237 seconds with cached graph, plus
1.349 seconds for its first query. The discovery prototype cost 3.612 seconds.
The new production result is consistent with removing worldwide preparation;
it is not an isolated format speedup, a fixed threshold or a statistical claim.

## Reuse and independent candidate paths

Final cached-worker timings after first useful access:

| Operation | Seconds | Requests | Actual body bytes |
| --- | ---: | ---: | ---: |
| Repeated 326-unit records | 0.0080 | 0 | 0 |
| Batched records, three caller IDs including duplicate terminal | 0.0060 | 0 | 0 |
| Catchment candidates, first / repeat | 0.0776 / 0.0677 | 0 / 0 | 0 / 0 |
| Segment-stem candidates, first / repeat | 0.6576 / 0.0649 | 4 / 0 | 9,561,531 / 0 |
| Reach-stem candidates, first / repeat | 0.8840 / 0.4120 | 5 / 2 | 10,197,737 / 2,624,697 |

Existing fsspec read-ahead buffers explain zero-traffic repeated records and
nearby catchment reads; this is not a universal zero-network repeat guarantee.
The reach-stem repeat reread two ranges. Record batches were still decoded on
repeated access. No full catchment or snap artifact was downloaded.

## Memory categories

Final cached-run resident topology: **670,353,612 bytes**. Each upstream call uses
**111,686,500 bytes** of query-owned visited/frontier workspace, even for 326 rows.
The selection retains 3,912 bytes; complete selected Arrow records retain 530,716.
Serialized fsspec buffers retain 3,870,952 bytes after first records and
13,738,232 after both snap sets. Selected candidate buffers are reported per phase.

Process-lifetime peak RSS was **2,306,818,048 bytes through first useful access**
and **2,460,155,904 through measured candidate queries**. Cold first-use peak was
2,420,899,840 bytes. These are not per-phase allocations or a concurrent-service
budget. Source reader counters exclude Arrow Parquet internal prebuffering,
metadata, allocator overhead and native GEOS allocation. The later after-close
RSS peak includes the independent post-timing oracle and is not runtime readiness.
First records, the selection and small candidate witnesses remain live during
repeats; the machine records state this retention explicitly.

## Independent correctness

A post-timing Arrow ID-domain frontier matched upstream membership exactly,
without the production numeric lookup or dense edge representation.
The separate source oracle uses independent obstore/fsspec Parquet access:

- All 326 records matched original HFX 0.3.0 catchment groups 5451/5452 exactly:
  schema/field metadata, every value/null, and exact WKB.
- A complete **local cached graph** bbox/level scan established spatial candidate
  completeness, order and soundness. All five records matched original source rows.
- Independently enumerated snap statistics selected segment group 430 and reach
  groups 5020/5021. Full candidate attributes and decoded geometry matched, with
  declared levels and graph references checked. This proves both completeness and
  returned values for these real windows, not only repeated-call stability.
- All four Arrow witness tables were exactly equal across cold, initial cached
  and final repaired cached runs, including metadata. Raw witnesses remain outside
  Git. [Oracle](independent-source-oracle.json) and [equivalence](witness-equivalence.json)
  receipts give counts and source groups.

Independent fixture tests additionally cover sparse/shuffled IDs and artifact
orders, DAG reconvergence, lookup boundaries above 2^53, missing IDs, oversized
singleton batches, nullable/degenerate bounds, both snap sets, malformed consumed
records, changed objects, resource release and compact dictionary-backed results.
Controlled HTTP and AWS4-signed S3-compatible endpoints exercised the complete
valid 0.4.0 Dataset path. AWS configuration was tested; **no live AWS, Hetzner or R2
credentialed provider test is claimed**.

## Traffic, identities and reproducibility

The Dataset used the original public HTTPS URL through a **forward mitmproxy**
on `127.0.0.1:18080`, with an explicit local CA and `max_retries=0`. Source/cache
identity remained the original URL. The recorder streamed unchanged body chunks;
it did not buffer the complete graph, decompress responses or repair ranges.
Counts are actual upstream response-body bytes received by the proxy, including
unsuccessful flows, not compressed object sizes, TLS bytes or client-requested
lengths. A 32-byte native range smoke verified 206/range/identity encoding and
32 counted bytes. An earlier malformed smoke URL returned 404 and 27,150 bytes;
that failed setup is retained outside all measurement windows. Measured phases
had zero failed requests. Proxy overhead is included; **these are not direct-HTTPS
latency measurements**. No coordinated competing dataset benchmark ran, though
brief fixture tests overlapped some workers.

Production cold pin: `715aa11afafd8823685f95f52ab59ba03aee4970`.
Final cached pin: `2543ee5e305b61bf4f0264b4a70d8a2e666b4d23`.
Between pins, repairs changed malformed-object handling, source-field preservation,
local/cache graph identity guards, optional dictionary compaction and buffer
accounting. GRIT uses plain string source fields. Graph transfer code did not
change. Exact final witness equality plus the final cached rerun validates the
valid-data path; repeating the 700 MB cold graph download was unnecessary.
Later auxiliary-required validation only rejects malformed manifests.

[Cold record](cold.json), [final cached record](cached-final.json), and
[provenance](provenance.json) pin phases, dependencies, commands, source digests,
settings and external receipts. Fsspec is 2026.9.0, obstore 0.11.1, PyArrow 25.0.1,
NumPy 2.5.3, Shapely 2.2.0 and mitmproxy 12.2.3. Raw logs, scripts and source
witnesses are retained at the external paths in provenance; no raw dataset or
credentials are committed. The owned proxy was stopped after verification.

Reproduce the production driver from the repository with `uv run python
scripts/verify_dataset.py ROOT --cache CACHE --output-dir OUTPUT --unit-id 22333245`.
Use a fresh cache for cold acquisition, then a fresh process with the same cache.
`--storage-options-json` supplies optional transport settings without recording
credentials. Independent traffic receipts are required; driver counters alone are
not a network measurement. The committed driver reports timestamps for joining.

GRIT carries attribution and **CC BY-NC 4.0** terms in its source bundle. Its DAG
upstream-area semantics and nullable source values are preserved, not repaired.
