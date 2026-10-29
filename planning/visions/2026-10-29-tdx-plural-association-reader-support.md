# TDX plural association reader support

Make the small Pourpoint reader update needed for HFX rivers with multiple local
catchments, and verify it against the current full-coverage TDX bundle on Hetzner.
Implementation of this vision completes [issue #44](https://github.com/pourpoint-tools/pourpoint/issues/44);
publication of this document does not.

## Contract and scope

Follow the [HFX spatial contract](https://github.com/pourpoint-tools/hfx/blob/main/docs/site/spatial-layout.md).
River `local_catchment_ids` relationships and their reverse, catchment
`local_river_ids`, are authoritative. Preserve every association and each polygon's
identity. The scalar feature field `local_catchment_id` may be absent; do not
require it, choose one associated polygon, or merge source polygons sharing a
`streamID`. Existing GRIT singleton associations remain valid without rewriting
published bundles. Format 0.4.0 and representation version 1 are unchanged.

Use the existing reader API. Current source inspection found no dependency on
`local_catchment_id`: `HydroFabric.relationships` already returns generic list
columns, and `river_network` retains supplied relationship rows. Callers can fetch
associated polygons with `subbasins`. This suggests a small compatibility change,
or regression coverage alone if the existing reader already behaves correctly;
it is not yet proof of compatibility. Fix only demonstrated reader gaps.

The target is the full 62-basin delivery, not historical partial deliveries.
Preserve its supplied coverage attributes and native values, including int64
`streamID`; do not reinterpret a known-empty association list as missing data.
No new public API, watershed algorithm, raster work, source normalization,
publication changes, or historical partial-delivery support is requested.

## Target bundle

The [verified HFX delivery record](https://github.com/pourpoint-tools/hfx/blob/eb5386f4ad84252ed9b28b338d1e357356308a80/docs/v0.4.0/tdx-full-delivery.md)
and its linked receipt identify the private bundle:

- Manifest: `s3://pourpoint-hfx/tdx/hfx-0.4.0/NGA-TDX-Hydro-20230126-full62-20261027/manifest.json`
- Hetzner S3 endpoint: `https://fsn1.your-objectstorage.com`
- Manifest SHA-256: `af831fec8d1469e304d21ff9271faead0035bb6f0ce8bf1e007c03c022a13991`
- Coverage: 62 river and 62 polygon sources; 15,940,505 rivers and 16,864,998 catchments.
  Every basin has `local_catchment_coverage: supplied`; unmatched rivers remain represented.

The location and identity above come from the published receipt, not a new
Pourpoint access check. Use authorized credentials without committing secrets or
changing access permissions. Keep this bundle's IDs and artifact-bound lookups
together. HFX's verification does not establish Pourpoint compatibility.

## Evidence of completion

Add focused regression coverage showing that plural associations survive reader
and river-network materialization without a scalar field, and that their reverse
links resolve to the right records. Cover singleton and known-empty associations
without losing source identities or attributes. Use independent expected values,
not expectations copied from reader output.

Run a bounded authenticated check through Pourpoint against the pinned full bundle,
including a verified plural-association witness and both relationship directions.
Compare selected records and associations with the delivery evidence or an
independent source reference; report what was actually checked. Do not download
or reverify the entire global publication for this reader update. If credentials
are unavailable, report the access blocker rather than claim live compatibility.

Keep existing reader behavior passing and run the repository's relevant tests,
Ruff and ty checks. Leave implementation details to the implementing agent; no
production change is required merely to make the patch larger.
