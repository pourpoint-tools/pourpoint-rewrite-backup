# Pourpoint and HFX documentation

## Outcome and scope

Prepare clear documentation for humans and LLMs, with **https://pourpoint-tools.github.io/** as the eventual public address. Keep all repositories and documentation private during development; public publication requires a later explicit maintainer decision. Explain Pourpoint's use and hydrological concepts, generate its API reference from source docstrings, and explain the current HFX 0.4.0 specification in its own section of the same site. Readers must not need development history to understand current behavior.

This is the implementation handoff for [Pourpoint #46](https://github.com/pourpoint-tools/pourpoint/issues/46), coordinated with [HFX #34](https://github.com/pourpoint-tools/hfx/issues/34). Delivering the outcome completes #46; publishing this vision does not. HFX #34 supplies related cleanup context, not a second specification or an automatic issue-closing instruction.

Use MkDocs Material and mkdocstrings, following RivRetrieve's source/site separation and release-only publication model. Do not copy unrelated application features or require PyPI publication as a dependency. Create `pourpoint-tools/pourpoint-tools.github.io` as a private generated-site repository. Keep public GitHub Pages deployment disabled during development. Private source repositories do not make a Pages site private. No custom domain or DNS setup.

## Reader-facing content

Rewrite Pourpoint's root README as a concise entry point: current purpose, basic setup, and links into the documentation, rather than a development chronology or duplicated API catalogue.

Pourpoint documentation covers getting started (installation and basic setup), concepts (including upstream contributing area, whole-subbasin selection versus raster refinement, assumptions and limitations), and API reference. Curate the reference into core operations and returned objects versus advanced source access. The [Python API vision](2026-10-20-python-api-and-qgis-workflows.md) selects the supported surface; source docstrings explain implemented behavior. Do not automatically expose all internal symbols or maintain parallel descriptions of each API.

[PR #56](https://github.com/pourpoint-tools/pourpoint/pull/56) delivered the user-facing NumPy-style docstrings and removed the obsolete reader. Reuse those docstrings and repository guidance. Signature repetition, filler sections and blanket internal documentation are not goals. Explain necessary units, CRS, result meanings, lifecycle and failure behavior without diluting qualifications.

**Examples are a separate effort.** Do not write worked examples, tutorials, or placeholder example pages here. Existing docstring examples can render as part of the reference; this exclusion does not require stripping them. Exact page organization is delegated.

## HFX: one self-contained current specification

Keep authoritative HFX documentation sources in the HFX repository. Assemble them into a dedicated HFX section of the shared website, without a second maintained copy in Pourpoint or a separate HFX website.

Present the reading order:

1. What HFX is and its boundary with a watershed engine.
2. Its data model: catchments, rivers, optional nodes, distinct relationships and optional rasters.
3. Bundle organization and manifest discovery.
4. The exact current physical specification: feature schemas, relationships, lookup/address binding, spatial access, identity requirements and native-grid rasters.

This is one coherent specification, not necessarily one long Markdown file. Make it approachable through concrete explanations and clear terminology, not merely a technical dump. Retain exact requirements where implementers need them. Do not turn this into producer tooling documentation or a new tutorial effort. Update HFX's stale README introduction and misleading canonical links as needed to route readers to the current specification.

Currently `docs/v0.4.0/spatial-layout.md` defines the spatial GeoParquet representation but inherits field meanings from `vector-data.md`, which also describes the superseded IPC feature layout. `raster-data.md` mixes generic requirements with GRIT production history. Consolidate current requirements so readers do not need to reconcile overrides or obsolete storage instructions. Preserve the existing contract; no schema redesign, new version, data conversion or dataset republishing is authorized.

The current representation is selected by all three manifest values: `format_version: "0.4.0"`, `representation: "spatial-geoparquet-adjacency"`, and `representation_version: 1`. The format string alone does not imply compatibility with earlier IPC feature publications. Current features are GeoParquet; lookup and immediate relationship artifacts still use Arrow IPC. Do not describe IPC itself as obsolete.

Preserve distinctions between independent ID domains, source and normalized identities, hierarchy and flow, node incidence and directed connectivity, local and component drainage associations, unknown/unavailable and empty values, source and derived quantities, and bounded fingerprints versus whole-file integrity. Include authoritative plural local drainage associations and optional/absent domains without reviving superseded scalar restrictions. Keep producer layout settings distinct from reader requirements. Explain native D8 grids, encoding, nodata, units and optionality without suggesting successful packaging certifies hydrological equivalence.

## Cleanup without breaking retained behavior

In Pourpoint, remove `docs/measured-delineation-improvements.md`, `docs/grit-delineation-benchmark.md`, and `docs/receipts/`. Keep reader, raster and operation verification reports out of the published reader-facing documentation. Carry useful current explanations into guides rather than carrying forward recorded-run narratives.

In HFX, remove obsolete `experiments/`, `docs/selective-access-candidates.md`, `docs/candidate-results.md`, and superseded build/delivery/handoff material from the current tree. Git history retains historical work. Inspect dependencies and preserve required source provenance, attribution and current contract information before deleting material. Resolve current links; do not leave the new specification dependent on deleted evidence pages. HFX #34 also records that handoffs should not remain tracked as repository documentation.

The investigation found four ordinary test imports from `experiments.data_interface.relationships`. Their test module also supplies fixtures used by production relationship and spatial-bundle tests. Preserve useful production coverage and move shared fixtures before removing experimental tests/code. Production code does not import experiments, but it does use helpers under `hfx.candidates` and `hfx.verify_candidates`. The `raster-experiment` dependency group is used for retained raster verification. Names alone do not justify deleting these modules or dependencies. Keep working producer tools; broad producer refactoring is outside scope.

## Cross-repository builds and publishing

Pourpoint owns site assembly and pins an **exact HFX commit** in its documentation build configuration. A normal Pourpoint build uses that revision; advancing the pin selects HFX content for the next release. Do not silently resolve HFX main during release builds or duplicate its source pages permanently.

- Pourpoint main updates check the combined documentation build without publishing.
- HFX documentation updates check the combined build using the proposed HFX revision, without publishing or automatically advancing Pourpoint's pin. The check must test the changed HFX content rather than accidentally testing only the old pin.
- A published Pourpoint release rebuilds the combined site from that release's Pourpoint revision and pinned HFX revision. During private development, generated output may be retained only in private artifacts or the private generated-site repository; it must not deploy to a public endpoint. After explicit approval to go public, this is the only event that publishes the site through Pages.
- HFX releases, main pushes, and ordinary build checks never publish the site. Do not introduce a separate HFX publication lifecycle.

Use a shared assembly path rather than independent copies of the site configuration. The implementation agent chooses workflow reuse, checkout layout, and credentials. Both source repositories were private during investigation; explicitly configure cross-repository read access and narrowly scoped generated-site write access. Do not make source repositories public to work around access. Surface any account permissions or Pages-plan limitation as a blocker rather than claiming deployment is complete. All generated documentation must remain private for now. Even after a future approval to go public, publish only intended reader-facing material, not private handoffs, credentials, or unpublished dataset access details.

Create/configure the private generated-site repository and required access as part of delivery. Prepare the eventual release-only Pages path, but keep public deployment disabled behind an explicit opt-in gate that is off by default. Do not enable public Pages or rely on source-repository visibility for site privacy. There is no requirement to introduce private-site hosting or an Enterprise plan during this effort. Validate the disabled publication gate without exposing content. An actual release or approval to go public remains a maintainer decision, not something this documentation effort should manufacture.

## Evidence and completion

The investigation found stale documentation routing, not accidental old-format selection in the current Pourpoint reader. It also found a separate local-raster preservation bug, now fixed in [HFX #68](https://github.com/pourpoint-tools/hfx/pull/68), with [#65](https://github.com/pourpoint-tools/hfx/issues/65) closed. Start HFX work from current main containing that fix; the sibling checkout used during discovery was older. Preserve local raster files/identities and independent verification; do not revive statements that the converter universally omits raster copying.

The reader audit passed 101 focused tests and opened the public GRIT manifest; the producer repair reports 246 tests and a bounded manifest check. These are not fresh worldwide vector/raster validation. No full production downloads, integrity audit, scientific recertification, or data regeneration are required for this documentation effort.

Success is observable when:

- A clean build produces the shared site with readable Pourpoint guides, a curated generated reference, and self-contained current HFX documentation, with working internal/cross-section links and no obsolete reports in navigation or generated output.
- The README and canonical links lead to current documentation; required meanings survive consolidation and no unintended API/format changes occur.
- Both repositories' CI can build the combined site; HFX checks use the revision being checked, while Pourpoint builds use the explicit pin. Strict documentation builds catch broken reference/rendering problems.
- All repositories and build outputs remain private, public Pages is disabled, and release builds cannot bypass the explicit public-publication gate. The eventual release-only publishing path is prepared without exposing the site. Record the exact source revisions used for release output so it is reproducible.
- Cleanup retains working production code and meaningful tests. Use repository-native uv commands and applicable pytest, Ruff and ty checks for changed source/test boundaries; validate documentation builds separately. Report unavailable checks or access as precise blockers.

Navigation, file layout, workflow mechanics and credential provisioning details remain implementation choices within these constraints. Do not start the deferred example effort, add a custom domain, change scientific defaults, or expand this into a format/API redesign.
