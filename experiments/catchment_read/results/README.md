# Local lookup/layout results

Publishing the lookup removed about **150 ms of ID-locator preparation** in this
local test. Arrow IPC did not consistently improve small repeated requests, but
substantially reduced large-block and scattered overread. This supports a bounded
remote follow-up, not a format decision or resuming #3 automatically.

Complete HydroBASINS: **3,786,228 units**. Measured code:
`6186c56f5fcbd4f240bdcfd2ce6f157a9e91df02`. All **192 output witnesses passed** independent
source equality for IDs, every attribute/null, schema metadata and exact WKB.
No bundles were changed, uploaded or redistributed.

## Opening + preparation + first retrieval

Milliseconds, median of two fresh workers per cell. This is the sum of the three
measured phases, not decode or full delineation. Small sample; no significance or
cloud-performance claim. Raw unrounded phases are in [runs.jsonl](runs.jsonl).

| Workload | Rows per call | 0.3.0 | A | B |
|---|---:|---:|---:|---:|
| single | 1 | 172.2 | 24.9 | 20.7 |
| small_upstream | 26 | 173.6 | 27.0 | 24.2 |
| larger_upstream | 2250 | 178.4 | 32.3 | 31.1 |
| overlap | 192 | 177.4 | 28.1 | 25.9 |
| scattered | 32 | 424.4 | 300.5 | 157.1 |
| large_geometry | 3 | 318.7 | 172.3 | 37.7 |
| batch | 2504 | 439.4 | 294.8 | 188.4 |

The small/larger watershed terminals are `6120125670`
and `6120215350`. Requests come from unchanged graph
connectivity, prepared once. Overlap contains three 64-ID requests, 96 unique IDs;
the third repeats the first. Scattered selects 32 evenly spaced sorted graph IDs.
Large geometry uses `6010000010, 6050010520, 2050067280`, the prior large-group case.
The batch contains all eight individual requests, including overlap duplicates.
[Selection counts](selections.json), generation code and the saved selection digest
make the exact input reproducible without publishing source geometry.

## Isolating the two changes

**0.3.0 → A:** baseline ID preparation read 924 ID column chunks, **22.75 MB**
at the local read boundary and retained **60.59 MB** of NumPy locator arrays.
It read no worldwide attributes. A preparation read 131,072 catchment bytes,
132,898 lookup bytes and 18,876 directory bytes, including bounded checks and IPC
metadata. It built no global numeric locator. A still pays the same Parquet data
block cost plus selective lookup I/O per call. That lookup overhead is visible:
A's first single request returned 4.15 MB at the boundary versus 3.10 MB for the
prepared baseline. Opening also counts manifest/footer reads: Parquet returned
2.51 MB of catchment metadata, versus 27,130 bytes for B's catchment IPC metadata.
These are local bytes returned, **not wire or physical disk bytes**.

**A → B:** the first large-geometry request materialized **245.77 MB** of complete
Arrow fields in A versus **23.73 MB** in B, for the same **2.460 MB** requested
logical output. A used one Parquet group; B used three IPC batches. Retrieval alone
was 167.5 versus 37.2 ms; repeated retrieval was 119.5 versus 19.5 ms. B's byte-budget
layout, compression and IPC decoding are compared together; this does not isolate
which individual mechanism caused the difference.

B was not uniformly faster. Repeated single/small/larger requests took
3.9/6.9/14.1 ms versus A's 2.9/5.2/9.6 ms. For scattered requests, repeated retrieval
was 130.1 ms versus A's 227.5 ms. B's first scattered call returned 117.64 MB at the
local boundary versus A's 346.80 MB. These few deterministic workloads are not a
representative workload distribution.

## Shared reads and memory

The batched call reads shared lookup/data blocks once. Compared with the sum of
the six standalone workload calls (retrieval only, no startup):

| Reader | Data blocks: separate → batch | Lookup batches: separate → batch | Local returned MB: separate → batch |
|---|---:|---:|---:|
| 0.3.0 | 40 → 35 | 0 → 0 | 511.89 → 330.54 |
| A | 40 → 35 | 40 → 34 | 553.60 → 365.97 |
| B | 43 → 39 | 40 → 34 | 163.31 → 148.47 |

Each repeated call rereads its selected lookup and data blocks. No cross-call
block cache is used. Numeric locators and file metadata are retained.

Maximum observed **process-lifetime** peak RSS across the two workers, decimal MB:

| Workload | 0.3.0 | A | B |
|---|---:|---:|---:|
| single | 209.2 | 114.0 | 82.8 |
| large_geometry | 1368.8 | 1308.1 | 121.1 |
| scattered | 1504.8 | 1435.5 | 129.5 |
| batch | 1505.6 | 1450.0 | 197.6 |

Peaks include imports and prior measured phases. The first result stays live during
the repeat; both Arrow outputs and second-call decoded geometries stay live at READY.
Reference generation and witness serialization are outside the measured process/
memory boundary. These are not per-phase peaks or concurrent-service budgets.
The large-geometry outputs retain 4.920 MB across both calls in every reader;
the batch retains 13.047 MB. Python/reader metadata and decoded native allocation
sizes are not separately measured. Raw records include endpoint RSS and Arrow
allocator counters, which do not cover all NumPy/Python/GEOS memory.

Decode is separate and runs only after both retrievals. Median large-geometry decode
was 1.78/1.68/1.70 ms for 0.3.0/A/B; batch decode was 6.37/6.45/6.54 ms. Logical
materialization sums blocks read, not simultaneous memory or transport.

## Correctness, provenance and limits

The independent reference scans the entire source Parquet using Arrow membership,
without any benchmark locator/address code. Every first/repeated/batched output is
compared directly, preserving caller order and duplicate IDs. Reference work ran
before workers; comparison ran afterward in another process. Raw references,
witnesses, full records, commands and red/green logs remain outside Git under the
artifact path in [provenance.json](provenance.json). That file also records source/
candidate identities, producer verification receipt, contract commits, versions,
hardware, measured code and raw-record digests. Producer verification is not used
as a substitute for reader correctness.

Thirteen focused tests cover shuffled non-row IDs, missing IDs inside/outside
lookup ranges, block boundaries, nulls/optional strings, caller order/duplicates,
explicit opt-in, bounded fingerprint mismatch, the ID-only projection and actual
I/O boundary reads. Deliberate mutations (reading an extra baseline attribute and
scanning a lookup batch at preparation) failed the boundary guards; restoring the
measured implementation passed. Candidate identities use bounded mismatch checks,
not full startup hashing or a guarantee against interior mutation.

42 fresh workers ran **sequentially**, with rotated/reversed reader order, on an
Apple M4 Max (64 GiB RAM). No coordinated competing benchmark jobs ran. Filesystem
cache, background laptop activity, power and thermals were uncontrolled. The source
reference scan preceded measurements. This is not cold disk. Two repetitions and
one machine cannot establish statistical superiority. Python FileIO instrumentation
and disabled reader threads/prefetch are explicit experimental settings, not a
claim to optimize every native access path.

A remote follow-up is justified: the lookup removes startup scanning but adds
per-request lookup ranges; latency/coalescing may change the result. B's reduction
in large-block overread is worth testing with immutable range-capable objects and
a more varied workload. Do not infer cloud latency, GRIT-scale behavior, an approved
HFX 0.4 release, or full delineation performance. Revisit #3's access assumptions
with these findings if the maintainer chooses; this PR does not resume or revise it.

See [reproduction and boundaries](../README.md). The implementation has one shared
reader, one measurement worker, an independent evidence script and a small serial
launcher. No production API, transport/cache framework or copied prior experiment
module was needed. The lockfile, small records and tests account for the remaining
scope; bulk data and witnesses are not committed.
