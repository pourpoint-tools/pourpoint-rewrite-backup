# Graph download comparison

Complete HydroBASINS HFX 0.3.0, unchanged original graph.
Measured code: [`8a12603`](https://github.com/pourpoint-tools/pourpoint/commit/8a12603e6ab0e5fd0f8334d305a24378ea70cd4c).
[Exact commands](results/followup/commands.json), [identity](results/identity.json),
[settings and definitions](README.md#full-download-follow-up).

Every row ran in a fresh process. Order: A1, B1, B2, A2; then C separately.
B1 and B2 each downloaded the complete original object to a new destination.
C reused the shared local original; acquisition is excluded from C.

| Run | Setup s | Download s | Footer s | Projected read s | Compact/index/checks s | Total ready s | Peak at ready B |
|---|---:|---:|---:|---:|---:|---:|---:|
| [A1](results/followup/A1.json) native | 0.056 | 0.000 | 1.236 | 119.175 | 0.150 | 121.254 | 370900992 |
| [B1](results/followup/B1.json) download | 0.040 | 4.268 | 0.003 | 0.055 | 0.145 | 4.989 | 381550592 |
| [B2](results/followup/B2.json) download | 0.039 | 3.997 | 0.003 | 0.055 | 0.146 | 4.692 | 380387328 |
| [A2](results/followup/A2.json) native | 0.042 | 0.000 | 1.359 | 111.318 | 0.146 | 113.324 | 367640576 |
| [C](results/followup/C.json) reuse | 0.000 | 0.000 | 0.002 | 0.057 | 0.147 | 0.327 | 334839808 |

Total-ready includes imports, setup, and pre-read identity HEAD; those components
are also saved separately. Peak is process-reported `ru_maxrss` captured before
post-ready correctness verification. Zero download seconds means no download
stage, not a measured transfer. Retained graph buffers and RSS/Arrow counters
are saved separately in each record.

All five graphs exactly match the shared original, including ordered `id`,
`level`, adjacency offsets, and upstream values. Each contains 3,786,228 rows
and 3,504,135 edges. SHA256 verifies both fresh downloads against the original;
remote ETag and size match before and after each remote case.

All cases retain 81,478,289 referenced graph bytes, 81,478,293 backing graph
bytes, and 45,434,736 owned lookup bytes. The complete original object is
112,610,620 bytes; projected compressed column chunks total 45,485,602 bytes.
These are storage counts. Wire requests, response bytes, and actual retries
are **not measured**.

No polygon timings or coordinator scans ran concurrently. Other laptop activity
and OS/remote caches were uncontrolled. Post-ready equality/checksum verification,
traversal, parents, geometry, snap, and rasters are excluded. These are laptop
diagnostics; the earlier download and projected-read observations are not part
of this paired comparison.
