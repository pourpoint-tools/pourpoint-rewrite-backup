# Reader verification

Run the selected public GRIT workload from the repository:

```sh
uv run python -m scripts.measure_fabric \
  --expected docs/receipts/reader-independent-expectations.json \
  --cache /path/to/empty-cache \
  --report /path/to/report.json
```

Use a new empty cache for each run. Reports are checkpointed after each operation;
failed runs retain their cache and error. `--profile single --range-workers 1`
and `--profile single --range-workers 4` compare complete opening, projected
candidate selection in all three domains, catchment lookup, upstream traversal,
and full-record assembly. Worker selection is a measurement-only private hook,
not a public `HydroFabric` option.

The full profile adds overlapping bulk seeds, full catchment records, scattered
river/node records, all supplied immediate relationship columns, and paired
native Greenland/Madagascar raster windows. It repeats in the same context,
reopens the persistent cache, then replays with `offline=True` and obstore
transport entry points disabled. Flow, hierarchy, incidence, correspondence and
catchment associations remain separate columns. Scalar-backed relationships are
compared with feature values; independent upstream closure witnesses check flow.

The retained expectations predate this reader. They come from HFX source-only
experiment receipts for normalized manifest `b9a38083131dcf1a4c7c4e73ebe9a97dff2a4b7ef91a8df53f8bc38cadfa48db`,
whose preservation is certified by the [final HFX delivery](https://github.com/pourpoint-tools/hfx/blob/main/docs/v0.4.0/grit-final-delivery.md).
Spatial witnesses are conservative bbox candidates, not exact intersections.
River/node full rows have independent WKB hashes and tier expectations.
The supplemental catchment 11205505 has an independent WKB hash, source identity,
bounds, outlet and parent witness. Catchment 1284709 has only membership and
closure witnesses. River 1 additionally checks direct flow hashes and distinct
endpoint incidence, local catchment association and child membership.
Native raster hashes come from HFX's independently decoded delivery windows.
Direction hashes invert the documented lossless GRASS normalization and restore
nodata using the separate validity mask; no pointer grid is warped.

Reports separate transport body bytes, disk-cache size, logical Arrow/NumPy
buffers and process-lifetime peak RSS. RSS units are bytes on macOS and KiB on
Linux. Logical buffers do not include every native allocator or GDAL cache.
First use does not mean a cold CDN. Timings include acquisition, decoding and
assembly, but not excluded delineation algorithms. These selected witnesses do
not replace global producer verification or establish universal latency bounds.

## Recorded run (2026-10-20)

| Sequence | Seconds | Requests | Body bytes |
| --- | ---: | ---: | ---: |
| First single, four range workers | 41.501 | 266 | 34,940,755 |
| Incremental overlapping bulk | 52.581 | 344 | 33,334,332 |
| Same-context full replay | 0.211 | 0 | 0 |
| Reopened full replay | 0.352 | 0 | 0 |
| Network-disabled full replay | 0.288 | 0 | 0 |
| First single, one range worker | 97.621 | 266 | 34,940,755 |

The single profile includes opening, all-domain projected candidates, a three-unit
upstream closure and complete catchment record assembly. The four-worker run's
first-single prefix is the identical sequence. These are sequential single trials,
not a randomized benchmark; network/CDN conditions can differ. They do not establish
an optimal worker count. Cache grew from 35,385,344 bytes after the first single
to 69,197,824 bytes after bulk work, and did not grow during replays.
Process-lifetime peak RSS reached 1,215,168,512 bytes, including earlier contexts
and native allocator retention; small logical buffers are not a memory bound.

Retained reports: [full four-worker run](receipts/reader-final4.json),
[one-worker single run](receipts/reader-single1.json), and
[latest-code cached replay](receipts/reader-latest-replay.json).
Reports record source-file SHA-256 identities. The full four-worker run preceded
the final cached-cursor EOF and cross-origin credential-forwarding fixes. The
latest-code replay includes those fixes and the GDAL callback error bridge, and
verifies the exact acquired manifest
SHA-256 `04bbfb50907feaf339808730f85c39ee2cbeaed2c6217e9d546cd445b3bc897f`.
All domain, closure, relationship and raster checks passed again, including
network-disabled replay. The earlier failed diagnostic run used the wrong mask
hash convention (255 rather than boolean bytes); its logs/cache remain outside
the repository, not counted as successful evidence.
