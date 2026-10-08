# Public raster verification

The reader returned exact independently checked results from the delivered HFX 0.4.0 rasters. This check covers selected access and reuse, not delineation speed or producer-wide validation.

Measured 2026-10-08 on macOS, Python 3.13.14, Rasterio 1.5.2 / GDAL 3.12.2, NumPy 2.5.3. Product commit: `db56dfc8d7a8ef9d4d37cd7b028232c11ca56c1a`. The worktree was clean. SHA256 hashes of every `src/pourpoint/*.py` file were unchanged before/after the run. One fresh process used `Dataset.open()` with no caller storage overrides, then `Dataset.rasters.prepare()` and `read()`.

## Input and correctness

Root: https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0/

The ordinary product manifest request returned SHA256 `3f8e6bb95e08063fff773096a6b949d3925527f15e748f66589b6d8ba73993fb`. Product requests use `Cache-Control: no-cache`; the same manifest snapshot remains in use throughout the Dataset lifetime.

| Artifact | Bytes | Manifest full SHA256 |
|---|---:|---|
| `aux/d8/flow_dir.tif` | 35,460,747,402 | `6d03ccdc092b914be0ae41975fbafebb2b02334ff481a1589897539228ab7c65` |
| `aux/d8/flow_acc.tif` | 150,995,308,307 | `01e8b76570a8d98026fdad77107ed5fe719490433d0351c43b62aca32ff0c15d` |

These full hashes identify the publication; this consumer run checked bounded fingerprints and pinned responses, not full-file hashes. Neither full raster nor full tile index was downloaded.

The first five windows are unchanged from `experiments/terminal_raster_access/selections.json`. Two small additional samples cross four tiles each and exercise nodata and valid cells. Window order is `[column,row,width,height]`:

```
[957346,466119,34,32]
[965628,462490,322,329]
[957371,466098,37,37]
[666542,193275,30,73]
[961902,463183,350,350]
[391664,49648,32,32]   # Greenland: all 1,024 cells nodata
[655344,346096,32,32]  # Madagascar: all 1,024 cells valid
```

All **235,133 cells per band** matched: normalized directions, accumulation float32 bit patterns (including nodata), and separate validity masks. Checks ran after Dataset closure. The independent decoder was the unchanged `experiments/terminal_raster_access/reference.py::decode_window`, SHA256 `276b4acf6b7f206a338c01791f6dac741772b7cab92c957966ff4bcca5fa2c09`, using tifffile 2026.9.20 and imagecodecs 2026.8.16, not GDAL. It read only selected ranges from the new 0.4.0 URLs, pinned to the delivered ETags. Reference traffic was separate from timing: 98 successful GETs and 3,855,523 received body bytes. No new decoder was written.

## Access diagnostics

First access used window 1 (the 322×329 boundary case). Preparation supplied exact native cell bounds in EPSG:8857. After the first read and a repeat, the fixed five-window sequence ran in order, then the two small extra cases. Correctness checks and evidence writes ran outside access timing.

| Phase | Seconds | Requests (HEAD+GET) | Received body bytes |
|---|---:|---:|---:|
| Dataset open, including existing vector metadata setup | 1.361643 | 14 | 473,902 |
| First raster preparation | 0.894930 | 8 | 395,264 |
| First paired arrays | 1.284592 | 7 | 1,038,950 |
| Same request repeated | 0.002318 | 0 | 0 |
| Five-window sequence after first/repeat | 2.977375 | 26 | 3,478,322 |

Through first usable arrays: **3.541293 s**, including Dataset opening and small phase gaps. No TIFF was accessed during Dataset opening. Later preparations made no requests. Complete run including the two extra checks: 82 requests and 8,141,227 received body bytes. These are one-run diagnostics, not latency gates or statistically controlled comparisons.

Counts instrumented completed Source range deliveries and HEAD calls. Body bytes are measured `len(data)` after the response has been received and checked, not inferred requested lengths. Counts exclude unobserved transport retries/failed-response bodies, HTTP headers and TLS. No assertion of total wire traffic is made.

## Memory and ownership

Array storage: first pair 741,566 bytes; retained five-window sequence 1,631,595 bytes; all seven windows 1,645,931 bytes. The run also retained separate first/repeat outputs.

Process RSS baseline: 91,881,472 bytes. After first arrays:185,663,488. After five-window sequence:200,163,328. After all seven:211,795,968. After close:211,877,888. These current-RSS snapshots use `psutil.Process().memory_info().rss` (psutil 7.2.2, macOS). The corresponding process-lifetime peaks from `resource.getrusage(...).ru_maxrss` were equal at these sample points; final peak 211,877,888 bytes. RSS includes Python, native libraries, GDAL/fsspec caches and retained outputs. It is not array storage or a cache budget. Closing handles does not promise process-wide cache release or immediate RSS reduction.

## Receipts and reproduction

Command used:
```
uv run --project .worktrees/visions/effort-4-native-grid-raster-reader --with psutil python <session-artifacts>/raster-access-evidence/measure_product.py
```

External receipt `product-final.json` contains phase records, exact successful ranges, source hashes, command and script hash. The measurement script SHA256 is `e11e677010415b57d0e4fef6cb92561c3374eaa76428384328511443e4da7498`. Bulk arrays, range bodies and diagnostic logs remain outside Git in the invocation's `raster-access-evidence` artifacts. The controlled HTTP/S3-compatible tests establish configured transport behavior; this public HTTP check does not demonstrate a live credentialed S3 deployment.

GRIT raster data remain CC BY-NC 4.0. Credit Michel Wortmann, Louise Slater, Laurence Hawker, Yinxue Liu and Jeffrey Neal (2025), https://doi.org/10.5281/zenodo.15715535. Upstream Tech sponsors hosting.
