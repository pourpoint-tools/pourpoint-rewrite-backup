# Recorded results

Code: [`05b72f2fb30f41fc612605496367de26eb8d3559`](https://github.com/pourpoint-tools/pourpoint/commit/05b72f2fb30f41fc612605496367de26eb8d3559).
Input: complete HydroBASINS HFX 0.3.0, [graph identity](results/identity.json).
[Exact commands](results/commands.json); [measurement definitions](README.md).
Seconds below are rounded from the linked JSON observations; memory is integer bytes.
These are laptop diagnostics, not controlled benchmarks or a ranked recommendation.

| Run | Footer s | Projected read s | Compact/index/checks s | Total load s | RSS after load B | Process peak B |
|---|---:|---:|---:|---:|---:|---:|
| [local-serial-1](results/local-serial-1.json) | 0.003 | 0.291 | 0.145 | 0.439 | 267730944 | 311689216 |
| [local-prefetch-1](results/local-prefetch-1.json) | 0.003 | 0.064 | 0.148 | 0.215 | 335757312 | 371965952 |
| [local-prefetch-2](results/local-prefetch-2.json) | 0.003 | 0.070 | 0.136 | 0.209 | 337117184 | 373325824 |
| [local-serial-2](results/local-serial-2.json) | 0.003 | 0.072 | 0.137 | 0.211 | 269451264 | 305758208 |
| [remote-serial-1](results/remote-serial-1.json) | 1.247 | 383.140 | 0.140 | 384.528 | 280887296 | 317177856 |
| [remote-prefetch-1](results/remote-prefetch-1.json) | 1.419 | 128.989 | 0.139 | 130.546 | 348340224 | 388153344 |
| [remote-prefetch-2](results/remote-prefetch-2.json) | 1.121 | 111.924 | 0.143 | 113.188 | 346685440 | 382746624 |
| [remote-serial-2](results/remote-serial-2.json) | 1.220 | 366.577 | 0.147 | 367.945 | 279461888 | 319782912 |

All runs read 3,786,228 rows and 3,504,135 edges from 924 row groups.
Projected compressed column chunks total 45,485,602 bytes (storage, not wire).
Each retains 81,478,289 Arrow referenced bytes, 81,478,293 whole backing bytes,
and 45,434,736 owned lookup bytes (int64 IDs plus uint32 positions).
Original IDs remain at the query boundary. Parents and catchment locators are excluded.

All three query selections match the independent reference exactly: 1, 24, and
17 units for IDs 6010000010, 2070018460, and 2070694550. Per-query times, output
bytes, membership hashes, and calculated numeric workspace are in each record.
Process peaks include reference checks; Arrow pool counters are recorded separately.

Local reads used an uncontrolled warm OS cache. The first remote serial run
included selective local timings; the prefetch pair had no sibling experiment
runs, and the final serial repeat followed isolated dense/selective runs.
Other laptop work was uncontrolled. Initial activity strings are supplemented
by `observed_concurrent_activity` where the coordinator clarified actual overlap.
Actual remote requests/response bytes/retries are **not measured**.

Initial graph acquisition took 4.215565541759133 seconds according to the
shared download receipt, separate from these projected-reader runs. It fetched
the full 112,610,620-byte object. Its wire traffic was also not measured.

An earlier int64-position remote serial run was deliberately interrupted when
the coordinator selected compact uint32 positions. Its partial run and older
local measurements are excluded; all results here use the same compact code commit.
