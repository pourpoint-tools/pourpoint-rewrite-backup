# Terminal raster access measurements

Small diagnostic measurements for the [standalone vision](../../planning/visions/2026-10-08-terminal-raster-access-measurements.md).
No production code, delineation, optimization comparison, or sufficiency judgment.
Issue #28 is background; its earlier strategy-comparison scope is superseded.

## Results

Measured 2026-10-08 on an Apple M4 Max, 64 GiB RAM, macOS 15.7.3.
Python 3.14.6, Rasterio 1.5.2, GDAL 3.12.2, NumPy 2.5.3; complete dependencies in `uv.lock`.
Direct public HTTPS, fresh sequential worker processes, normal GDAL caches.
Remote/CDN cache and laptop load were not controlled. These are not latency guarantees.

| Phase | Run 2: boundary first, seconds / HTTP requests | Run 3: compact first, seconds / HTTP requests |
|---|---:|---:|
| Manifest declaration | 0.263312 / 1 | 0.195293 / 1 |
| Open direction | 0.396224 / 2 | 0.388620 / 2 |
| Open accumulation | 0.153569 / 2 | 0.281485 / 2 |
| Grid metadata checks | 0.000140 / 0 | 0.000384 / 0 |
| First paired arrays, including deferred tile-index access | 0.802798 / 4 | 0.557016 / 4 |
| **First usable pair, total including preparation** | **1.627665 / 9** | **1.432025 / 9** |
| Same pair, repeat 1 | 0.001225 / 0 | 0.001164 / 0 |
| Same pair, repeat 2 | 0.000759 / 0 | 0.000757 / 0 |
| Fixed five-window sequential batch | 2.931605 / 16 | 2.106594 / 16 |

Each worker made 8 logical paired reads and 25 HTTP requests, with zero failed
HTTP responses. Each paired read calls two band reads and two mask reads; these
are **not** HTTP counts. The batch uses one already-open pair after its first
window and repeats, in frozen selection order. Different initially primed windows
mean these two workers are not identically conditioned statistical repetitions.

First-pair retained arrays: 741,566 bytes (boundary) and 7,616 bytes (compact).
The five retained batch pairs total **1,631,595 bytes**, including independent
boolean validity arrays. Process lifetime peak RSS: **168,853,504 bytes** and
**167,510,016 bytes**. This includes Python, libraries, caches, logging, and
retained outputs; it is not array storage or phase-local memory growth. Phase
records give the cumulative peak reached by that phase.

**Actual raster HTTP response bytes: not measured.** `CPL_CURL_VERBOSE` records
sent HTTP headers and statuses. `CPL_DEBUG` VSICURL reports requested ranges and
completion, not observed body byte counts. Header lengths and compressed tile
sizes are not substituted. TLS/header bytes are also not measured. The manifest
record's 1,426 bytes are the decoded JSON body, not wire traffic.

## Workload and input

The current explicit [GRIT HFX 0.3.0 manifest](https://basin-delineations-public.upstream.tech/grit/hfx-v0.3.0/manifest.json)
declares `aux/d8/flow_dir.tif`, `aux/d8/flow_acc.tif`, EPSG:8857, GRASS directions,
and km² accumulation. The pinned experimental contract is
[D8 raster v2](https://github.com/CooperBigFoot/hfx/blob/c18a8a6a115e2045080d8752279d39db2b9b29b6/spec/aux/d8_raster/v2.md).
Current headers: 1,070,000 columns × 500,000 rows, 512×512 tiles, native 30 m
cells, affine `(30, 0, -15000000, 0, -30, 8400000)`. Direction is uint8/nodata255;
accumulation is float32/nodataNaN. No resampling, warping, conversion to float64,
polygon masking, seed selection, tracing, or polygonization occurs.

The pinned Rust `refinement.rs` and `algo/refine.rs` at
`60e3d6e383546ea6ed20e68fe4d77959c816a48c` were inspected: localization uses the
terminal polygon's native bounds, not the upstream watershed. The historical
planetary-COG note provides context only; current requests are measured afresh.

`selections.json` freezes five real GRIT level-1 reach geometries, source IDs,
row-group/row indices, original WKB witnesses, native polygon bounds, CRS, exact
pixel windows, and selection rules. They are potential terminal units, not
assertions that these units are graph sinks or results of outlet delineation.
Bounds come from projecting every polygon vertex before taking extrema.
Floor/ceil preserves small projection roundoff; nearly aligned edges can add a
pixel. No place-name assumption determines a boundary crossing.

| Index / unit ID | Window `[column,row,width,height]` | Full-resolution tile indices |
|---|---|---|
| 0 / 22333205 | `[957346,466119,34,32]` | 1903769 |
| 1 / 22336871 | `[965628,462490,322,329]` | 1889155, 1889156 |
| 2 / 22333206 | `[957371,466098,37,37]` | 1903769 |
| 3 / 11167509 | `[666542,193275,30,73]` | 789231 |
| 4 / 22335498 | `[961902,463183,350,350]` | 1891238, 1891239, 1893328, 1893329 |

Indices are row-major with 2,090 tile columns. Windows 0 and 2 overlap by 9×16
cells; index 3 is scattered from the NZ selections. Index 1 crosses columns;
index 4 crosses both rows and columns. Selection used two bounded Parquet
row groups, outside timing: 2 HEADs and 6 range GETs, 26,666,233 response-body
bytes. No full vector or raster download. Raster header probes, vector selection,
and correctness requests are separate from measurement-worker totals.

## Correctness and evidence

`access.py` checks compatible CRS/shape/affine, allowed native dtypes, tiling,
and nodata without scanning the dataset. Per-band validity stays separate.
Focused fixtures check enclosing window placement, four-tile assembly, grid
mismatch rejection, NaN/sentinel validity, and int32 values above float32's exact
range. Remote selections happen to contain no nodata; the synthetic fixture
therefore supplies the missing-accumulation/usable-direction evidence.

`reference.py` independently decodes every selected cell with tifffile and
imagecodecs, not GDAL or `access.py`. It fetches a bounded BigTIFF header, the
selected original offset/count entries, and selected compressed tiles. Only an
in-memory header copy is patched to avoid loading the huge tile indexes.
Original payloads remain unchanged in external evidence. This deliberately
narrow correctness script is not a competing access implementation or timed
optimization. All 233,085 cells per band and both masks matched exactly, with
native dtype checks, for both accepted workers. Reference decoders share the
producer bytes, not GDAL's cell extraction. Producer-wide validity is trusted.

Small records: `results/measurements.json`, `results/reference.json`, and
`results/provenance.json`. Bulk raw diagnostics, NPZ arrays, TIFF ranges,
selection acquisition receipts, and test logs are outside Git:
`/Users/nicolaslazaro/Desktop/work/artifacts/terminal-raster-access/`.
Raw logs include network metadata and should not be published wholesale.
The reference summary records measured-array hashes and decoder versions.
Run 2's repeated independent check is `reference/run-2-comparison.json` externally.

One completed diagnostic worker (`run-1`) logged only `rasterio._env`, missing
read-phase messages on `rasterio._err`. It is retained externally but excluded
from the table. Root-logger capture fixed this; a test covers both logger
channels and a phase boundary. Each accepted raw log independently contains
24 request headers and 24 response status lines, plus the timed manifest GET.
No expensive cases were retried blindly. Two small header probes preceded the
three bounded workers. There were no failed or interrupted access workers.
JSON summaries encode NaN nodata as `"NaN"`; arrays keep native NaN.
Selection witness/attribution metadata was added after timing; the original
selection bytes and exact metadata-only revision proof are preserved.
`results/provenance.json` identifies the measured source hashes and external
snapshot. It was saved by reversing two post-run JSON-only edits (NaN string
encoding and strict serialization); access behavior is unchanged.

## Reproduce

From this directory, using a fresh external output path:

```sh
uv sync --frozen
uv run python select_windows.py
uv run python measure.py --selections selections.json --output /path/outside-git/run-a --first 1
uv run python measure.py --selections selections.json --output /path/outside-git/run-b --first 0
uv run python reference.py --selections selections.json --arrays /path/outside-git/run-b --output /path/outside-git/reference
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run ty check *.py
```

Run the two workers sequentially. Each has a 180-second alarm, GDAL connect
limit15s/read timeout30s and zero retries. `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`
avoids sibling-file discovery; normal GDAL block/VSICURL caching otherwise stays
at library defaults. Native reader diagnostics and their write overhead are
inside timing. Imports/process launch and selection-file loading are outside;
manifest fetch, environment setup, opening and metadata checks are charged.
The first usable total includes small gaps between named phases. Tile-index and
tile payload time are combined in reads because this API does not expose separate
operations. Evidence-array writes occur after access timing and memory capture.
The worker keeps output arrays to save evidence; batch retained-byte totals do
not include the separately retained first result. Exceptions are saved rather
than automatically retried.

Root validation also passed: 105 production tests, Ruff, ty on `src`, and build.
The experiment has 10 passing tests. No production source/dependency files changed.

## Data attribution

Embedded source geometry samples remain **CC BY-NC 4.0**, not the repository's
software license. See [DATA_NOTICE.txt](DATA_NOTICE.txt), copied from the current
public `NOTICE` and `CITATION.txt`. Credit Michel Wortmann, Louise Slater,
Laurence Hawker, Yinxue Liu, and Jeffrey Neal (2025): GRIT vector datasets,
[doi:10.5281/zenodo.17435232](https://doi.org/10.5281/zenodo.17435232), and raster
datasets, [doi:10.5281/zenodo.15715535](https://doi.org/10.5281/zenodo.15715535).
Original WKB samples are unchanged; native bounds are derived by projection.
Upstream Tech sponsors hosting, not publication or sale of these data.
