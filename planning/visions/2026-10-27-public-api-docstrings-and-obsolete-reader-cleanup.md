# Public API docstrings and obsolete reader cleanup

## Outcome and authority

Make Pourpoint's supported Python API understandable through concise, LLM-friendly source docstrings suitable for generating an API reference. Remove the obsolete draft HFX reader rather than documenting or maintaining a second unsupported interface.

This is the implementation handoff for [discussion #54](https://github.com/pourpoint-tools/pourpoint/issues/54). Publishing this vision does not complete that issue. Completion requires the docstrings, repository guidance, cleanup and verification below. Resume [documentation discussion #46](https://github.com/pourpoint-tools/pourpoint/issues/46) afterward; documentation-site construction and deployment remain separate work.

The [Python API and QGIS workflows vision](2026-10-20-python-api-and-qgis-workflows.md) is the existing public-surface authority. Use it to select the reference surface, then inspect current implementations to explain concrete types and behavior. Do not turn the current export list into a competing API contract. The [reader/runtime vision](2026-10-20-hfx-reader-and-runtime-redesign.md) supplies current data-access and lifecycle context. Report material discrepancies rather than silently redesigning the API or documenting unintended behavior as a requirement.

## Docstring contract

Follow RivRetrieve's public-API guidance and source-driven reference approach, available in the sibling checkout `../RivRetrieve` and its [repository instructions](https://github.com/RivRetrieve/RivRetrieve/blob/main/AGENTS.md#public-api-docstrings). Add adapted guidance to Pourpoint's repository `AGENTS.md`; no global instruction changes are required.

Use NumPy-style docstrings for public operations and user-facing returned types, methods and attributes, regardless of their implementation location. Write explicit, readable semantics for humans and LLMs. Concision means removing repetition and filler, not dropping qualifications or compressing prose into ambiguous fragments.

- Let typed signatures carry types and defaults. Explain parameter meanings, constraints and interactions where needed.
- Explain units, CRS, output contents, absent values, source versus derived quantities, lifecycle, side effects, and single/batch failure behavior where applicable.
- Describe stable failure codes and their conditions when callers need them. Distinguish valid empty results, target-local failures, and fatal configuration or integrity failures.
- Include only useful sections. A short summary or no prose is sufficient when the name and signature fully explain an interface. Add examples only when they clarify use.
- Avoid blanket internal documentation, implementation narration, mandatory section/coverage quotas, and a parallel hand-written description of every API.

The approved level of detail is illustrated by this proposed `snap` docstring. Verify it against the implementation during delivery; the example is a writing model, not authorization to change behavior:

```python
def snap(*, target: BaseGeometry, onto: SnapGeometry, radius: float) -> Point:
    """Find the nearest point on supplied stream geometry within a search radius.

    Parameters
    ----------
    target
        Nonempty Point in EPSG:4326 (longitude, latitude).
    onto
        LineString, MultiLineString, River, RiverNetwork, or sequence of
        rivers. Coordinates must be finite and in EPSG:4326.
    radius
        Maximum WGS84 ground distance in metres. Must be finite and
        nonnegative.

    Returns
    -------
    Point
        Snapped location in EPSG:4326. Equal-distance matches retain
        input order.

    Raises
    ------
    OperationError
        ``snap.point_required`` if the target is not a valid WGS84 point.
        ``snap.no_match`` if no supplied stream is within the radius.
    ValueError
        If the radius or stream geometry is invalid. Split segments
        spanning 180 degrees or more of longitude before calling.

    Notes
    -----
    Intended for local stream segments. Segments are straight in
    longitude/latitude; distances are measured on the WGS84 ellipsoid.
    Snapping does not select a raster outlet cell.
    """
```

Cover core operations and their results: fabric lifecycle, upstream selection, delineation, refinement, merging, snapping, river selection, and portable QGIS delivery. Returned feature attributes, refinement variants, outlet cells, raster subsets, batch identity/results/issues and exceptions need enough explanation to interpret them without reading internal code. Separate these from advanced direct records, candidates, relationships, traversal, native raster access, metadata and diagnostics. This is a reference organization distinction, not a demand to reorganize classes or promote every helper to a public export.

Docstrings are the source for the later generated reference, following RivRetrieve's curated mkdocstrings approach. This effort does not build the documentation site or add speculative LLM-specific formats or infrastructure.

## Remove the obsolete reader

The earlier `Dataset` path reads a draft layout with catchments in Arrow IPC files and a separately prepared topology graph. Current hydrological operations use `HydroFabric` and the finalized spatial HFX publication. Keep the canonical HFX contract; do not retain a compatibility facade or copy old fixed-field restrictions into the current reader.

The read-only investigation identified these cleanup boundaries. Recheck references during implementation:

- Remove `dataset.py`, `catchments.py`, `topology.py`, `geometry.py`, and `_validation.py`, plus exports of `Dataset`, `GeometryBatch`, `DecodedGeometry`, `UnitSelection`, `Manifest` and `SnapSet`.
- In shared `metadata.py`, remove old `Manifest`, `SnapSet` and `Auxiliary` definitions while retaining current manifest parsing, fingerprints and path/bounds helpers.
- In `storage.py`, remove the old graph-file download/cache/identity path and its exclusive helpers. Preserve current range transport, integrity checks, cache and artifact access.
- In `spatial.py`, remove the old standalone candidate-group helper and graph-specific overlap branch. Retain `RowGroupBounds`, conservative bounds and current vector overlap behavior.
- Keep current raster access, vector readers, features, transport, cache and dictionary compaction. Arrow IPC itself is not obsolete: current HFX lookup and adjacency artifacts still use it.

Migrate useful coverage before deleting old tests and fixtures. Most raster tests and the remote dataset/raster transport tests currently enter through `Dataset`; exercise those shared guarantees through `HydroFabric` and the current spatial fixture instead. Preserve native-grid/CRS, nodata/direction/accumulation, lifecycle, identity, cache/offline and remote-transport coverage. Keep the current `RowGroupBounds` tests.

Audit legacy tests for distinct behavior not already covered by current tests, including float32 boundary handling, selected versus unselected geometry decoding, empty/invalid IDs, and preservation of source attributes/nulls/metadata/WKB. Transfer only missing meaningful coverage. Do not retain tests for obsolete wrapper shapes, dense graph internals, snap sets or fixed draft schemas. Remove old fixtures after their shared consumers have moved; keep shared fingerprint and raster-pair fixture utilities.

Retire `scripts/verify_dataset.py` and its legacy tests. Current fabric measurement tooling remains. Historical standalone experiments do not import the retired engine modules and need not be deleted. Keep evidence and planning history historical rather than rewriting it to imply it used the current reader. Remove obsolete-reader recommendations from current usage documentation; no migration narrative is needed.

## Verification and limits

Follow repository test-first guidance, use `uv`, and retain strong typing. Migrate shared-behavior tests first, remove the obsolete path and prune dead shared-module members, then run the affected tests and the full test suite, Ruff formatting/linting and `ty check src` before delivery. Prose-only docstring edits do not require artificial new tests. Check that the chosen NumPy sections and returned attributes can be consumed by the intended reference tooling without turning this into site deployment work.

Completion means the current API has concise, accurate source documentation; repository instructions preserve that standard; obsolete reader code, exports, fixtures and tools are removed; and meaningful current-behavior coverage remains passing. Inspect the remaining references to retired names, distinguishing historical evidence from active imports and usage instructions.

No algorithm redesign, scientific assumption changes, changed numerical defaults, source-normalization work, compatibility layer or documentation-site deployment is included. Exact implementation edits, test migration details and reversible reference-rendering checks remain engineering work. The investigation was source inspection only; it did not execute tests or establish runtime verification results.
