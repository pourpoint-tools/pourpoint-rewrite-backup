# HydroBASINS measurements

[Isolated run](hydrobasins-isolated.json): code `5198b1690543e32feb0b2d4842f8fd016cbddad4`.
[Earlier concurrent run](hydrobasins-concurrent.json): code `73c66acdb10c916825bbb3b81c3c9ed860ac5449`.
Both used the complete original HydroBASINS bundle. Object identity, local SHA256,
queries, versions, and settings are in each record. The only intervening code change
handles absent snap bbox in query planning; this bundle has bbox.

## Isolated local run

Seconds below are observed read/filter timings rounded to four decimals, not speedup
claims. Decode timings are separate in JSON. First and repeated queries reuse the
same locator. Method order reverses for the repeat. No coordinated experiment ran
concurrently; unrelated laptop work and OS-cache state are uncontrolled.

| Query / geometry group size | Reader | Read/filter seconds first / repeat | Candidate groups | Selected rows | Selected WKB bytes |
|---|---|---:|---:|---:|---:|
| polygon / ordinary | filtered | 0.3459 / 0.2531 | 209 | 3 | 4253 |
| polygon / ordinary | explicit_groups | 0.0067 / 0.0065 | 1 | 3 | 4253 |
| polygon / largest | filtered | 0.1937 / 0.2085 | 94 | 3 | 2459871 |
| polygon / largest | explicit_groups | 0.1387 / 0.1339 | 1 | 3 | 2459871 |
| snap / ordinary | filtered | 0.1684 / 0.1679 | 3 | 9 | 1233 |
| snap / ordinary | explicit_groups | 0.1206 / 0.1157 | 3 | 9 | 1233 |
| snap / largest | filtered | 0.1955 / 0.1905 | 2 | 10 | 5082 |
| snap / largest | explicit_groups | 0.1328 / 0.1337 | 2 | 10 | 5082 |

Attribute-only: 3,786,228 rows, 0.0778 seconds read,
0.1480 seconds locator construction. Retained Arrow
parent/level/up-area buffers: 53,953,750 bytes;
catchment ID column: 30,289,824 bytes;
owned NumPy locator buffers (int64): 60,587,040 bytes.
Null parents: 10; null up-area values: 0.

Process lifetime peak RSS for the full sequence: 4,920,311,808 bytes.
Arrow pool lifetime peak: 2,114,586,112 bytes. These are different
measurements; neither is a per-method peak. Endpoint memory snapshots are in JSON.

The ordinary polygon filtered query has 209 metadata-selected candidate groups:
1,177,229,214 compressed column-chunk bytes. The explicit locator query has one:
2,949,496 compressed bytes and 5,227,386 measured prefilter WKB bytes. The largest
existing polygon geometry group has 169,966,246 compressed geometry bytes;
explicit access materializes 245,517,161 WKB bytes from that group.
These compressed sizes are **not bytes fetched**. Filtered scanner intermediate WKB,
physical I/O bytes, remote requests, and remote response bytes are **not measured**.

All polygon selected-ID and WKB checks passed. All snap ID/unit-ID/WKB and referenced
level checks passed. Snap decodes 9 ordinary or 10 large-group candidates; no null
bbox occurred in these selected real-data rows. Synthetic tests cover null/missing
bbox fallback and rejected fallback rows. Graph traversal, snap distance/ranking,
repair, union, and area computation are excluded.

## Reproduce

Use the commands in [the experiment README](../README.md), checking out the recorded
code commit first. For the isolated record set `--activity 'No concurrent coordinated
experiments; other laptop work unknown'`; for the earlier record set `--activity
'Arrow reader remote graph experiment concurrent; no other coordinated local
geometry/topology experiment; other laptop work unknown'`. Both runs use the same
receipt and complete immutable data directory. Initial acquisition is recorded in
the receipt, not charged to local read timings.
