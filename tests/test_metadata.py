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
    [("unit_count", 0), ("format_version", "0.3.0"), ("lookup", "../lookup.arrow"), ("index_batch_rows", 2)],
)
def test_invalid_manifest(key: str, value: object) -> None:
    data = manifest_data()
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
