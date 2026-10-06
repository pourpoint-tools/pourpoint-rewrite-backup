# HFX reader and runtime data foundation

Program: https://github.com/pourpoint-tools/pourpoint/issues/2
Effort: https://github.com/pourpoint-tools/pourpoint/issues/3

## Outcome

Build the HFX reader and the typed runtime data model it populates. Together they
provide the data capabilities downstream engine operations need, without making
those operations understand JSON, Parquet layout, remote storage, or repeated ID
lookup machinery. This is working production foundation code, not another research
report or a collection of type declarations without usable access operations.

Design the representation around its consumers, then implement ingestion against
that representation. Keep **HFX 0.3.0 and snap v2 unchanged**. The research supports
compact resident topology and selective geometry access; it does not justify a new
HFX format or require dense on-disk IDs.

The eventual user experience remains simple:

```python
engine = pourpoint.Engine(dataset=url)
result = engine.delineate(lat=..., lon=...)
```

Users must not construct internal graph objects, manage Parquet readers, or manually
download the hosted dataset to use that interface. This Effort supplies its data
foundation; it does not implement the complete delineation operation or promise
compatibility with every historical option and result detail. Those belong to the
later engine composition work.

## Required access, including remote datasets

Remote access is essential, not a follow-up feature. Support local datasets, public
HTTP(S) dataset roots, AWS S3, and S3-compatible storage configured for services such
as Cloudflare R2 and Hetzner. Public GRIT is the normal hosted-user path and requires
no credentials:

`https://basin-delineations-public.upstream.tech/grit/hfx-v0.3.0/`

Use manifest-declared paths relative to the dataset root. Do not require bucket or
HTTP directory listing to discover known artifacts. Keep endpoint, authentication,
and transport configuration separate from hydrologic data and algorithms. Private
sources may need optional configuration; public HTTPS must not require AWS setup.
Do not hard-code a Cloudflare-specific storage engine or leak one source's
credentials into another source's requests.

Use **obstore** for object access, **PyArrow** for Parquet decoding and columnar
representations, and **NumPy/Arrow** for compact numeric operations. Prefer existing
library facilities over custom transport, storage, or binary cache formats. Obstore
provides streaming downloads, range reads, HTTP/S3 backends, and documented PyArrow
integration through its filesystem adapter. Verify that bridge on the actual access
paths: its existence does not prove efficient request batching or buffering. A
concrete adapter gap may justify another established integration, not an unmeasured
transport rewrite. Focused graph algorithms over these representations are welcome.

Download the original graph artifact to a local file and read its required columns
there. The measured whole-object path outperformed remote projected loading for
HydroBASINS. Reuse a verified local copy rather than reconstructing a new storage
format. This must not trigger downloads of complete polygon or snap artifacts.

Provide basic source-specific identity checks, safe completion of downloads, and
an explicit resource lifetime. Do not treat filename existence or the manifest
hash alone as proof of reusable content. Never expose an incomplete download as a
valid graph. Basic correctness cannot wait for #7; elaborate cache freshness
policies, eviction budgets, cross-process coordination, and concurrent-service
resource management remain primarily that Effort's responsibility.

## Runtime capabilities and guarantees

Choose a small coherent set of typed interfaces backed by bulk arrays and batches,
not one Python object, dictionary, or collection per unit. The names and exact class
boundaries are implementation choices. The following capabilities are required.

### Dataset metadata and unit identity

Represent the manifest, dataset-local levels, artifact references, and named snap
sets with small typed records. Preserve other auxiliary declarations, including
raster references, as metadata for their later consumers; do not read raster pixels
or settle raster processing APIs here.

HFX unit IDs are positive int64 identifiers scoped to one compiled dataset. They
need not survive recompilation and must not be narrowed or confused with row
positions. Internal positions may use a narrower checked integer width. A selection
or positional view must retain enough dataset/table identity to prevent accidental
use against another source or ordering.

### Resident topology and contributing selections

Keep compact same-level adjacency, IDs, levels, and numeric lookup resident. Dense
runtime positions are the supported traversal direction; they do not require
renumbering the dataset. Arrow list offsets/values and NumPy-compatible buffers are
suitable building blocks. Avoid permanently retaining duplicate edge representations
solely because the experiment kept both for comparison.

Supply a small working inclusive upstream-traversal path: terminal included once,
all reachable upstream units included once, including DAG reconvergence. Unknown
terminal or referenced IDs are errors, not empty adjacency. Do not constrain
upstream membership to the outlet's spatial search window. Non-terminal traversal
order need not reproduce incidental Rust ordering.

Shared topology is read-only after preparation. Frontier, visited state, and result
selection belong to the query. Account for graph-sized work arrays and their lifetime;
a small result does not necessarily imply small traversal workspace.

### Unit attributes and catchment location

Provide projected access to the full HFX unit information downstream operations
need: identity, level, nullable parent, local and nullable upstream area, outlet,
bounds, geometry, and optional audit attributes. Preserve types and null semantics;
null parent/upstream area is not zero, and `has_up_area=true` does not imply every
row is populated. Source labels must not control engine behavior.

Keep parent containment separate from same-level flow connectivity. Support
parent/level lookup without decoding polygons. Maintain an explicit catchment-ID
locator built from catchment data, independently of graph order. Equal row counts,
Hilbert sorting, or matching group boundaries do not prove graph/catchment alignment.
A general Parquet ID predicate is not a replacement for this locator.

Keep the scalar information and indexes needed for reliable access resident; permit
other attributes to be projected when requested. Avoid redundant indexes and
attribute copies where safe sharing is established explicitly, not assumed.

### Selected polygons and snap candidates

Support unit-ID polygon retrieval and spatial candidate access, including level
eligibility, without loading complete geometry tables. Select relevant row groups,
project columns, filter rows, and only then decode geometry when required. Carry
IDs alongside WKB or decoded geometries through filtering, gathering, and reordering.
Missing requested units must not silently disappear. Make ordering and duplicate
request semantics explicit rather than depending on scan order.

Use Arrow WKB batches for serialized geometry and mature Shapely/GEOS operations
for selected decoding. Do not retain both serialized and decoded forms indefinitely
by default. Small views must not accidentally pin large discarded candidate buffers;
a compact copy can be preferable to nominally zero-copy retention.

Handle every declared snap set, not a singleton root-level snap file. Snap-feature
ID and referenced unit ID are different domains; multiple features may reference
one unit. Snap level eligibility comes from the referenced unit and declaration.
Preserve weights and optional roles without inventing cross-set comparability.
Missing/null snap bounds must take a correct fallback path rather than excluding
possibly relevant features. Spatial bounds identify candidates, not exact radius,
containment, or final ranked results. Preserve degenerate bounds and conservative
boundary behavior when float32 bounds meet full-precision coordinates.

The reader supplies the necessary data and decoded forms; final outlet selection,
snap ranking, watershed union, geometry repair policy, and area calculation remain
in their downstream operations.

## Validation and ownership

Opening prepares the loaded structural information for safe use: validate relevant
schemas, nullability, counts, ID uniqueness, graph/catchment correspondence, levels,
references, and parent relationships. Check the graph invariants relied on by the
runtime, including acyclicity and declared tree/DAG constraints; a visited set merely
terminating on a cycle is not validation. Check capacity before narrowing positions
or offsets. Errors should identify the artifact and offending field/unit where
possible.

Validate selected spatial records when read, including expected geometry types and
snap references. Do not silently skip malformed selected data and return a plausible
but incomplete selection. Clearly document what opening and each access operation
establish. This runtime is not a replacement for the full HFX validator and must not
read every geometry on open to certify nesting, sibling disjointness, or outlet
placement. Checks deferred until access must not be advertised as globally complete.

The dataset owns reusable readers, metadata, and resident arrays. Operations own
query state and selected results. Define resource release and mutability clearly.
Count decoded geometry, Arrow buffers, NumPy arrays, temporary conversion copies,
reader prefetch, and retained views separately where practical. Row-count batch
limits do not guarantee byte limits, and reading selected groups does not eliminate
Parquet page/group overread.

## Evidence to build on

Research issue [#9](https://github.com/pourpoint-tools/pourpoint/issues/9) led to the
committed [experiments](../../experiments/README.md). Read their implementations,
settings, exclusions, and results before adapting them. They are evidence, not
production modules to copy unchanged.

- [Graph acquisition](../../experiments/arrow_read/FOLLOWUP.md): complete HydroBASINS
  graph download plus local preparation took 4.99/4.69 seconds versus 121.25/113.32
  seconds for remote Arrow projection, with exact graph equality. Native remote
  wire traffic was not measured. This supports the download approach, not a promised
  latency or a universal claim about all backends.
- [Topology](../../experiments/dense_topology/README.md): 3,786,228 units retained
  81.04 MB graph/levels and 45.43 MB numeric lookup; comparison-only dense edges added
  14.02 MB. Per-query workspace was 18.93 MB excluding output. Dense traversal was
  viable, but already-small traversal times were not the principal bottleneck.
- [Polygon access](../../experiments/selective_access/results/followup/README.md):
  explicit catchment lookup reduced candidate groups from 209 to 1 for one selection
  and from 94 to 1 for another. Fresh-process peaks were approximately 404 MB and
  1.50 GB for the locator paths, versus 3.3–3.6 GB for filtered scans. These local
  cases each retained three identical decoded geometries; they are not a workload
  distribution or remote-read benchmark.
- The largest selected group materialized 245.52 MB of WKB for about 2.46 MB of
  selected WKB. Selective decoding is necessary but does not erase overread.
- The prior TDX probe covered 15,936,428 units: 351.21 MB Arrow graph plus 191.24 MB
  lookup, with 975.32 MB peak process RSS. It excluded parents, catchment lookup,
  geometry, snapping, and traversal workspace. Its roughly 27-minute serial remote
  read was diagnostic, not evidence that the format needed changing.

All sizes above are decimal. Laptop timings were uncontrolled observations. Do not
sum separate experiment peaks into an integrated memory claim. The experiments did
not measure complete delineation or establish obstore–Arrow performance.

The live public GRIT manifest was separately inspected: HFX 0.3.0, **22,337,300
units**, DAG topology, and distinct level-0/level-1 snap declarations. Its graph was
699,720,490 bytes; graph, catchment, and both snap objects accepted anonymous HTTPS
byte ranges. Those metadata checks did not exercise a complete Python access path.
GRIT, not just tree-only HydroBASINS or TDX, is therefore required integration evidence.

## Evidence of completion

Deliver usable typed runtime structures and local/remote readers, with a verified
integrated **open → topology selection → attributes → selected geometry** path.
Also verify spatial catchment and snap candidate access independently of final
snapping. Downstream agents should be able to consume these capabilities without
implementing another HFX loader or index.

Use independent small valid/invalid cases for sparse/shuffled IDs, independent file
orders, multiple levels and parents, nulls, missing IDs, malformed records, DAG
reconvergence, cycles, multiple snap sets, many-to-one snap references, and absent/
null/degenerate bounds. Compare exact membership and geometry identity, not only
counts or final polygon area.

Use the complete, unchanged HydroBASINS dataset as the main reproducible large
fixture and public GRIT/R2 as a required remote integration path. Exercise the
S3-compatible path against the available Hetzner data, with provider/configuration
coverage for AWS S3 rather than provider-specific hydrologic code. Include controlled
range/read tests so a successful query cannot conceal a whole polygon or snap
download. Preserve raw datasets and credentials outside Git, with attribution.

Measure the integrated preparation and query peak, retained topology/catalog/
locator buffers, selected and intermediate geometry, and first/repeated access.
Instrument request counts/response bytes for the selected remote verification, or
identify an explicit verification blocker; stored compressed sizes are not wire
traffic. Record settings and dataset identity. Do not invent numerical latency
acceptance thresholds from a shared laptop. Verify buffer lifetimes and repeated
access without claiming production concurrent-query budgets, which remain later work.

## Boundaries and repository context

No HFX 0.4.0, fabric-specific normalization, new producer layout, or custom storage
engine is required. Raster pixels, raster windows, refinement, complete snapping,
dissolve/area algorithms, CLI recreation, releases, and unrelated tooling are out of
scope. Preserve declarations for later use without expanding this vector-focused
Effort into raster design. #5 supplies whole-unit delineation, #4/#6 handle raster
work, #7 extends dataset/cache lifecycle, and #8 completes the public engine.

The repository is a fresh Python package; research lives under `experiments/` and
production behavior is not yet implemented. Use `uv`, strong typing throughout,
Ruff, ty, and dependency-provided test utilities where suitable. Do not assume the
root type-check command covers experiment files. Preserve unrelated worktree edits.

The existing [HFX 0.3.0 contract](https://github.com/CooperBigFoot/hfx/blob/c18a8a6a115e2045080d8752279d39db2b9b29b6/spec/HFX_SPEC.md)
and [snap v2](https://github.com/CooperBigFoot/hfx/blob/c18a8a6a115e2045080d8752279d39db2b9b29b6/spec/aux/snap/v2.md)
are the input references. The clean-slate sibling HFX repository currently pins
these references rather than providing the older `../hfx/spec/HFX_SPEC.md` path.
The [Rust baseline](https://github.com/CooperBigFoot/pourpoint/tree/60e3d6e383546ea6ed20e68fe4d77959c816a48c),
available locally under `../deprecated/pourpoint`, is source/regression evidence,
not a requirement to preserve accidental copies, eager reads, or suspected defects.

For storage integration, consult [obstore's PyArrow example](https://developmentseed.org/obstore/latest/examples/pyarrow/),
[file-reader caveats](https://developmentseed.org/obstore/latest/api/file/), and
[filesystem integration](https://developmentseed.org/obstore/latest/integrations/fsspec/).
The inspected `reformatters` project uses obstore download helpers, but its
existence-only cache reuse, producer-oriented retry budgets, and Zarr/Icechunk/
Kubernetes infrastructure are not this engine's requirements. In particular,
concatenating requested byte ranges does not create a valid random-access Parquet
file. Leave reversible class names, buffer widths, and adapter wiring to the
implementing agent within these guarantees.
