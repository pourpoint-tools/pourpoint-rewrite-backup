import pytest

from pourpoint.metadata import Manifest


def manifest_data() -> dict[str, object]:
    fingerprint = {"size_bytes": 100, "sha256": "a" * 64, "head_sha256": "b" * 64, "tail_sha256": "c" * 64}
    paths = {
        "graph": "graph.parquet",
        "catchments": "catchments.arrow",
        "lookup": "lookup.arrow",
        "directory": "lookup-directory.json",
        "source_manifest": "source-manifest.json",
    }
    return dict(
        format_version="0.4.0",
        crs="EPSG:4326",
        fabric_name="test",
        unit_count=3,
        topology="dag",
        has_up_area=True,
        bbox=[0, 0, 1, 1],
        created_at="2026-01-01T00:00:00Z",
        adapter_version="1",
        auxiliary=[],
        index_batch_rows=65536,
        catchment_batch_bytes=8388608,
        artifacts={p: fingerprint.copy() for p in paths.values()},
        caveat="source caveat",
        **paths,
    )


def test_manifest_artifact_identity_and_source_metadata() -> None:
    manifest = Manifest.parse(manifest_data())
    assert manifest.lookup == "lookup.arrow"
    assert manifest.artifacts[manifest.catchments].size_bytes == 100
    assert manifest.declarations["caveat"] == "source caveat"


@pytest.mark.parametrize(
    "key,value",
    [
        ("unit_count", 0),
        ("format_version", "0.3.0"),
        ("lookup", "../lookup.arrow"),
        ("index_batch_rows", 2),
        ("auxiliary", None),
    ],
)
def test_invalid_manifest(key: str, value: object) -> None:
    data = manifest_data()
    if value is None:
        data.pop(key)
    else:
        data[key] = value
    with pytest.raises(ValueError):
        Manifest.parse(data)


@pytest.mark.parametrize("path", [".", "a/../b", "%2e%2e/x", "a\\b", "a%5cb", "a//b", "a/./b", "a%00b"])
def test_artifact_paths_reject_ambiguous_or_escaping_names(path: str) -> None:
    from pourpoint.metadata import artifact_path

    with pytest.raises(ValueError):
        artifact_path(path)


def test_empty_provenance_artifact_fingerprint_is_valid() -> None:
    from pourpoint.metadata import Fingerprint

    digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert (
        Fingerprint.parse({"size_bytes": 0, "sha256": digest, "head_sha256": digest, "tail_sha256": digest}).size_bytes
        == 0
    )


def spatial_manifest_data() -> dict[str, object]:
    fingerprint = {"size_bytes": 100, "sha256": "a" * 64, "head_sha256": "b" * 64, "tail_sha256": "c" * 64}
    families = {
        name: {
            "data": f"{name}.parquet",
            "data_sha256": "a" * 64,
            "lookup": f"{name}.arrow",
            "directory": f"{name}.json",
            "count": 3,
        }
        for name in ("catchments", "rivers", "river_nodes")
    }
    return {
        "format_version": "0.4.0",
        "representation": "spatial-geoparquet-adjacency",
        "representation_version": 1,
        "crs": "EPSG:4326",
        "families": families,
        "relationships": {},
        "auxiliary": [],
        "artifacts": {
            path: fingerprint.copy()
            for family in families.values()
            for key, path in family.items()
            if key in ("data", "lookup", "directory")
        },
        "source_caveat": "unknown connectivity",
    }


def test_spatial_manifest_selects_representation_and_binds_addresses() -> None:
    from pourpoint.metadata import SpatialManifest

    data = spatial_manifest_data()
    manifest = SpatialManifest.parse(data)
    assert manifest.families["rivers"].data == "rivers.parquet"
    assert manifest.declarations["source_caveat"] == "unknown connectivity"
    data["representation"] = "ipc"
    with pytest.raises(ValueError, match="representation"):
        SpatialManifest.parse(data)
    data = spatial_manifest_data()
    data["families"]["rivers"]["data_sha256"] = "d" * 64
    with pytest.raises(ValueError, match="binding"):
        SpatialManifest.parse(data)


def test_spatial_relationship_binding_uses_target_domain_not_source_domain():
    from pourpoint.metadata import SpatialManifest

    data = spatial_manifest_data()
    fingerprints = data["artifacts"]
    fingerprints["relations.arrow"] = fingerprints["rivers.arrow"].copy()
    fingerprints["relations.json"] = fingerprints["rivers.json"].copy()
    data["relationships"] = {
        "catchments": {
            "data": "relations.arrow",
            "directory": "relations.json",
            "count": 3,
            "columns": {"local_river_ids": {"source_domain": "rivers", "target_domain": "rivers"}},
            "feature_artifacts": {
                name: {"data": data["families"][name]["data"], "data_sha256": "a" * 64}
                for name in ("rivers", "catchments")
            },
        }
    }
    assert SpatialManifest.parse(data).relationships["catchments"]["count"] == 3
    data["relationships"]["catchments"]["feature_artifacts"]["rivers"]["data_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="binding"):
        SpatialManifest.parse(data)
