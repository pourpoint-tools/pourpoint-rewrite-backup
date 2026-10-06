# Vector access experiments

Measure three ways to improve Python vector access on unchanged HFX 0.3.0.
Deliver reproducible numbers, not a production engine or an architecture decision.
The maintainer will bring the results back for discussion and choose what to do next.

Research context: https://github.com/pourpoint-tools/pourpoint/issues/9

This is standalone research. It does not complete issue #3, become a Program
member, or authorize changing either issue's scope or status.

## Parallel work

One coordinating agent owns this vision and consolidates the results. It delegates
three experiment owners in parallel, each with a separate branch and worktree
below the repository's `.worktrees/` directory. Each owner may use supporting
subagents for implementation and investigation; each PR needs independent review.
Branches start from the intended target branch. Keep experiment code separate
from production engine code. Do not build a general benchmarking framework.

1. **Reader and compact Arrow baseline.** Read projected `id`, `level`, and
   `upstream_ids` into compact columnar buffers with a numeric ID lookup. Compare
   explicit serial/no-prefetch settings with established Arrow coalescing and
   prefetch settings. Keep representation, projection, and correctness checks
   comparable. Measure startup, retained buffers, peak process memory, and remote
   read behavior. Use a small set of meaningful configurations, not a tuning grid.
2. **Dense runtime topology.** Compare ID-based traversal with converting edges
   once into dense runtime positions and traversing numeric arrays. Measure load,
   conversion/index construction, temporary and resident memory, and traversal
   separately. Include first-query and repeated-query measurements. Retain original
   dataset IDs at the boundary. Dense runtime positions do not require dense stored
   IDs or a format change.
3. **Selective vector access.** Measure attribute-only access, selected-unit polygon
   retrieval, and spatial snap candidates without loading all geometry. Account for
   the separate catchment-ID locator and parent/level data. Compare ordinary filtered
   reads with explicit candidate-row-group access where useful. Distinguish bytes
   fetched, WKB materialized, geometries decoded, and retained buffers. Include
   ordinary and large existing geometry groups. Preserve null bbox fallback behavior
   and snap's many-to-one unit references.

These experiments are complementary. The coordinator agrees shared inputs and
measurement definitions before dispatch. Owners can use a minimal common baseline
without waiting for another branch to land; do not turn three experiments into three
copies of an engine. Supporting tools must serve a concrete measurement need.

## Data and execution

Use the complete existing HydroBASINS dataset as the primary baseline, not a newly
extracted country dataset. Selective queries still select from that complete dataset.
Share one immutable local copy outside Git rather than download it independently
for each worktree. Preserve original artifacts and notices; write experimental
outputs elsewhere. Charge the initial download separately from warm local access.
TDX is a secondary scale check, not a requirement to download its entire vector bundle.

Issue #9 records dataset prefixes, manifests, sizes, and earlier probe evidence.
Read-only remote access uses bucket `pourpoint-hfx`, endpoint
`https://fsn1.your-objectstorage.com`, region `fsn1`, and local AWS profile
`hetzner-pourpoint`. Never print or commit credentials, signed requests, or sensitive
transport logs. HydroBASINS restricts standalone redistribution: retain attribution
and do not publish bulk data as fixtures. Do not modify remote data.

The available laptop has 64 GB RAM and runs other work. No arbitrary 2 GiB experiment
ceiling is required. Coordinate aggregate resource use and avoid unnecessary
whole-dataset geometry materialization. Parallel execution is allowed. Record
concurrent activity when known; elapsed times on this machine are diagnostics, not
controlled benchmarks. Repeat consequential timing comparisons without competing
experiments when needed. Do not invent a performance target.

## Measurement contract

Deliver a compact consolidated table, machine-readable measurements, and the commands
needed to reproduce each run. Link each result to its experiment commit, dataset/object
identity, query inputs, package versions, reader settings, and cold/local/reused state.
Record units explicitly. A reported value must come from a saved measurement or a
clearly labeled calculation with its inputs. Missing values say **not measured**;
failed runs say **failed** with the observed error. Never fill gaps with estimates
presented as observations.

Measure the following where the experiment exercises them:

- Row/edge counts and exact selected-ID correctness checks.
- Graph, lookup, parent/attribute, locator, and query buffer sizes separately.
- Live referenced buffers versus whole retained backing buffers; process RSS and
  process peak separately from library allocation counters.
- Startup, preprocessing, and query elapsed time separately; state sampling and
  measurement methods, including whether a peak is sampled or process-reported.
- Actual remote request counts and response bytes, including retries, **only when
  transport instrumentation verifies them**. Column-chunk compressed sizes and
  Python read calls are not wire measurements. If instrumentation cannot establish
  traffic, mark it not measured and report available storage counts separately.
- For geometry, candidate groups/rows, selected rows, materialized WKB bytes and
  actual decode count where measured. Batch row limits are not byte-memory limits.

Do not require recommendations, a ranked winner, a limitations essay, or speculative
explanations. Add only short factual notes needed to read the numbers: excluded work,
measurement coverage, observed failures, or concurrent execution. An unsuccessful
experiment is a valid result. Review checks traceability and correctness, not whether
an approach sounds promising. No claimed speedup without comparable measurements.

## Correctness and boundaries

Use HFX 0.3.0, including snap v2, without changing artifacts or inventing a replacement
contract. The sibling `hfx` rewrite lacks the former specification path; use the pinned
[HFX specification](https://github.com/CooperBigFoot/hfx/blob/c18a8a6a115e2045080d8752279d39db2b9b29b6/spec/HFX_SPEC.md)
and its linked snap v2 contract.

IDs are positive int64 identities within a compiled dataset, not row numbers. Graph
and catchment row alignment is not guaranteed. Preserve exact inclusive upstream
membership, same-level edges, parent semantics, missing-ID errors, null attributes,
and geometry/ID alignment. Use small independent correctness cases, including a
reconvergent DAG; both real baselines declare trees. Separate runtime checks from
expensive complete conformance validation. Do not change snapping, geometry repair,
union, or area semantics to make a comparison faster; report those stages as excluded
when they are not measured.

Prefer mature readers and numeric representations. Arrow is the starting point, not
an outcome to prove. Avoid Python objects per node, whole-graph `to_pylist()` expansion,
custom binary formats, general cache systems, and unrelated library comparisons.
Use `uv`, typed Python, Ruff, and ty under repository rules. Rasters are excluded.
No production implementation, HFX release, Program restructuring, or automatic
adoption of any experimental approach is authorized.

## Existing evidence to inspect

Read the pinned Rust baseline `60e3d6e383546ea6ed20e68fe4d77959c816a48c` in
`/Users/nicolaslazaro/Desktop/work/deprecated/pourpoint` with `git show`.
Relevant paths under `crates/core/src/` include `reader/graph.rs`,
`reader/catchment_store.rs`, `reader/snap_store.rs`, `algo/upstream.rs`, and `session.rs`.
The old graph uses per-node upstream vectors; remote loading fetches the whole encoded
artifact. The optimized polygon path already filters IDs before geometry decode;
the snap path decodes candidate geometries before row filtering. Treat these as
investigation evidence, not behavior to copy blindly.

Earlier local evidence is under
`/Users/nicolaslazaro/.prime/agent/session-artifacts/01a1121f-98dd-7427-b845-8012b351f5de/hetzner-hfx-inspection/`.
Issue #9 preserves essential results if that directory is unavailable. The prior TDX
probe measured 542.44 decimal MB of logical graph-plus-lookup buffers and about
975.32 MB peak process RSS. It excluded parents, catchment lookup, snap, geometry,
and traversal workspace. Its approximately 27-minute remote load explicitly used
`pre_buffer=False` and `use_threads=False`, with no traffic instrumentation. These
are historical observations, not targets or proof of a format bottleneck.

## Delivery

Land only reviewed experimental code and small measurement records; keep bulk data
and private raw logs outside Git. The coordinator verifies the combined target-branch
result and returns one lean comparison with links to reproducible evidence and PRs.
Stop there. The maintainer owns interpretation and the next decision.
