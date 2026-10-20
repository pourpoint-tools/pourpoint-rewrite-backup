from pathlib import Path

import pytest
from shapely.geometry import LineString, Point, box

from pourpoint.metadata import SpatialManifest
from pourpoint.storage import Source
from tests.spatial_fixture import make_spatial_bundle


@pytest.mark.parametrize("domain", ["catchments", "rivers", "river_nodes"])
def test_records_preserve_order_types_nulls_metadata_and_wkb(tmp_path: Path, domain: str) -> None:
    from pourpoint.vectors import VectorReader

    with Source(make_spatial_bundle(tmp_path)) as source:
        reader = VectorReader(source, SpatialManifest.parse(source.read_json("manifest.json")), domain)
        result = reader.records([40, 10, 40])
        assert result["id"].to_pylist() == [40, 10, 40]
        assert result["source_value"].to_pylist() == [4000, None, 4000]
        expected_geometry = {
            "catchments": [box(4, 1, 4.25, 1.25).wkb, box(1, 1, 1.25, 1.25).wkb],
            "rivers": [LineString([(4, 1), (4.25, 1.25)]).wkb, LineString([(1, 1), (1.25, 1.25)]).wkb],
            "river_nodes": [Point(4, 1).wkb, Point(1, 1).wkb],
        }[domain]
        assert result["geometry"].to_pylist() == [*expected_geometry, expected_geometry[0]]
        assert result.schema.equals(reader.schema, check_metadata=True)
        assert result.schema.field("source_value").metadata == {b"unit": b"native"}
        assert reader.records([10], columns=["source_value"]).column_names == ["source_value"]
        with pytest.raises(KeyError):
            reader.records([11])


@pytest.mark.parametrize("domain", ["catchments", "rivers", "river_nodes"])
def test_candidates_and_split_reconvergent_traversal(tmp_path: Path, domain: str) -> None:
    from pourpoint.vectors import VectorReader

    with Source(make_spatial_bundle(tmp_path)) as source:
        reader = VectorReader(source, SpatialManifest.parse(source.read_json("manifest.json")), domain)
        assert reader.candidates((1, 1, 3, 1), columns=["id"])["id"].to_pylist() == [30, 10, 20]
        assert reader._lookup is None  # Spatial addresses must not be resolved again.
        assert reader.candidates((1, 1, 3, 1), levels=[2], columns=["id"])["id"].to_pylist() == [30]
        assert reader.relationships([10], columns=["downstream_ids"])["downstream_ids"].to_pylist() == [[20, 30]]
        assert reader.traverse([10], relationship="downstream_ids") == [10, 20, 30, 40]
        assert reader.traverse([40]) == [40, 20, 30, 10]
        assert reader.traverse([10], relationship="parent_ids") == [10, 40]
        with pytest.raises(KeyError):
            reader.relationships([11])


def _refresh(path: Path, artifact: str) -> None:
    import hashlib
    import json

    manifest = json.loads((path / "manifest.json").read_text())
    raw = (path / artifact).read_bytes()
    manifest["artifacts"][artifact] = {
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "head_sha256": hashlib.sha256(raw[:65536]).hexdigest(),
        "tail_sha256": hashlib.sha256(raw[-65536:]).hexdigest(),
    }
    (path / "manifest.json").write_text(json.dumps(manifest))


@pytest.mark.parametrize("damage", ["binding", "address", "id", "count"])
def test_rejects_invalid_published_lookup(tmp_path: Path, damage: str) -> None:
    import json

    import pyarrow as pa

    from pourpoint.vectors import VectorReader
    from tests.spatial_fixture import write_ipc

    path = make_spatial_bundle(tmp_path)
    artifact = "rivers-lookup.arrow"
    with pa.memory_map(str(path / artifact), "r") as stream:
        table = pa.ipc.open_file(stream).read_all()
    if damage == "binding":
        table = table.replace_schema_metadata({b"hfx:data_sha256": b"0" * 64})
    elif damage in {"address", "id"}:
        rows = table.to_pylist()
        rows[0]["row"] = 999 if damage == "address" else 0
        table = pa.Table.from_pylist(rows, schema=table.schema)
    else:
        artifact = "rivers-lookup.json"
        data = json.loads((path / artifact).read_text())
        data["batches"][0]["rows"] = 1
        (path / artifact).write_text(json.dumps(data))
    if damage != "count":
        write_ipc(path / artifact, table)
    _refresh(path, artifact)
    with Source(path) as source:
        reader = VectorReader(source, SpatialManifest.parse(source.read_json("manifest.json")), "rivers")
        with pytest.raises(ValueError, match="binding|bounds|mismatch"):
            reader.records([10])


def test_projected_relationship_reuse_and_lifetime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pourpoint.vectors import VectorReader

    with Source(make_spatial_bundle(tmp_path)) as source:
        reader = VectorReader(source, SpatialManifest.parse(source.read_json("manifest.json")), "rivers")
        materialized = reader.records([10])
        assert reader.traverse([10], "downstream_ids") == [10, 20, 30, 40]
        assert reader._relationships is not None
        assert reader._relationships._reader.schema.names == ["id", "downstream_ids"]

        def unexpected(*args: object, **kwargs: object) -> None:
            pytest.fail("retained traversal should not decode IPC batches again")

        monkeypatch.setattr(reader, "relationships", unexpected)
        assert reader.traverse([10], "downstream_ids") == [10, 20, 30, 40]
    assert materialized["id"].to_pylist() == [10]
    with pytest.raises(ValueError, match="closed"):
        reader.traverse([10], "downstream_ids")


def test_unknown_bounds_and_missing_statistics_are_conservative(tmp_path: Path) -> None:
    import json

    import pyarrow.parquet as pq

    from pourpoint.vectors import VectorReader

    path = make_spatial_bundle(tmp_path)
    artifact = "rivers.parquet"
    table = pq.read_table(path / artifact)
    rows = table.to_pylist()
    rows[0]["bbox"] = None
    table = type(table).from_pylist(rows, schema=table.schema)
    pq.write_table(table, path / artifact, row_group_size=2, write_statistics=False)
    _refresh(path, artifact)
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["families"]["rivers"]["data_sha256"] = manifest["artifacts"][artifact]["sha256"]
    manifest["relationships"] = {}
    with Source(path) as source:
        reader = VectorReader(source, SpatialManifest.parse(manifest), "rivers")
        assert reader.candidates((19, 19, 20, 20), columns=["id"])["id"].to_pylist() == [30]
        assert reader.candidates((1, 1, 1, 1), columns=["id"])["id"].to_pylist() == [30, 10]


def test_incidence_keeps_target_domain_distinct_from_flow(tmp_path: Path) -> None:
    from pourpoint.vectors import VectorReader

    with Source(make_spatial_bundle(tmp_path)) as source:
        manifest = SpatialManifest.parse(source.read_json("manifest.json"))
        rivers = VectorReader(source, manifest, "rivers")
        nodes = VectorReader(source, manifest, "river_nodes")
        assert rivers.relationships([10], ["downstream_node_ids"])["downstream_node_ids"].to_pylist() == [[20]]
        assert nodes.relationships([20], ["outgoing_river_ids"])["outgoing_river_ids"].to_pylist() == [[20, 30]]
        assert rivers.relationships([10], ["downstream_ids"])["downstream_ids"].to_pylist() == [[20, 30]]
        with pytest.raises(ValueError, match="same-domain"):
            rivers.traverse([10], "downstream_node_ids")


def test_id_metadata_and_nested_dictionary_selection(tmp_path: Path) -> None:
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq

    from pourpoint.vectors import VectorReader

    path = make_spatial_bundle(tmp_path)
    artifact = "rivers.parquet"
    table = pq.read_table(path / artifact)
    table = table.set_column(0, table.schema.field("id").with_metadata({b"meaning": b"source identity"}), table["id"])
    values = pa.DictionaryArray.from_arrays(
        pa.array([0, 1, 2, 3], type=pa.int8()), pa.array(["unused" * 10000, "selected", "other", "last"])
    )
    nested = pa.StructArray.from_arrays([values], names=["label"])
    table = table.append_column("source_nested", nested)
    pq.write_table(table, path / artifact, row_group_size=4)
    _refresh(path, artifact)
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["families"]["rivers"]["data_sha256"] = manifest["artifacts"][artifact]["sha256"]
    manifest["relationships"] = {}
    with Source(path) as source:
        reader = VectorReader(source, SpatialManifest.parse(manifest), "rivers")
        result = reader.candidates((1, 1, 1, 1), columns=["id", "source_nested"])
        assert result["id"].to_pylist() == [10]
        assert result.schema.field("id").metadata == {b"meaning": b"source identity"}
        assert result["source_nested"].to_pylist() == [{"label": "selected"}]
        assert result["source_nested"].chunk(0).field("label").dictionary.to_pylist() == ["selected"]
