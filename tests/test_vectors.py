from pathlib import Path

import pyarrow as pa
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


def _rewrite_family(path: Path, domain: str, table: "pa.Table") -> None:
    """Rebind fixture lookup addresses after changing source rows."""
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    from conftest import refresh_fingerprint

    from tests.spatial_fixture import write_ipc

    artifact = f"{domain}.parquet"
    pq.write_table(table, path / artifact, row_group_size=2)
    refresh_fingerprint(path, artifact)
    manifest = json.loads((path / "manifest.json").read_text())
    family = manifest["families"][domain]
    digest = manifest["artifacts"][artifact]["sha256"]
    family["data_sha256"] = digest
    family["count"] = table.num_rows
    manifest["relationships"] = {}
    ids = table["id"].to_pylist()
    schema = pa.schema(
        [pa.field("id", pa.int64(), False), pa.field("block", pa.int32(), False), pa.field("row", pa.int32(), False)],
        metadata={b"hfx:data_sha256": digest.encode()},
    )
    write_ipc(
        path / family["lookup"],
        pa.Table.from_pylist(
            [{"id": value, "block": ids.index(value) // 2, "row": ids.index(value) % 2} for value in sorted(ids)],
            schema=schema,
        ),
    )
    ordered = sorted(ids)
    batches = [
        {
            "batch": offset // 2,
            "min_id": ordered[offset],
            "max_id": ordered[min(offset + 1, len(ids) - 1)],
            "rows": len(ordered[offset : offset + 2]),
        }
        for offset in range(0, len(ids), 2)
    ]
    (path / family["directory"]).write_text(json.dumps({"version": 1, "batches": batches}))
    (path / "manifest.json").write_text(json.dumps(manifest))
    for name in (family["lookup"], family["directory"]):
        refresh_fingerprint(path, name)


def test_float32_bounds_keep_full_precision_point(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from pourpoint import HydroFabric

    path = make_spatial_bundle(tmp_path)
    table = pq.read_table(path / "river_nodes.parquet").slice(0, 1)
    table = table.set_column(
        table.schema.get_field_index("bbox"),
        table.schema.field("bbox"),
        pa.array([dict.fromkeys(("xmin", "ymin", "xmax", "ymax"), 0.1)], type=table["bbox"].type),
    )
    table = table.set_column(
        table.schema.get_field_index("geometry"), table.schema.field("geometry"), pa.array([Point(0.1, 0.1).wkb])
    )
    _rewrite_family(path, "river_nodes", table)
    with HydroFabric(path, cache=False) as fabric:
        assert fabric.candidates("river_nodes", (0.1, 0.1, 0.1, 0.1))["id"].to_pylist() == [30]


@pytest.mark.parametrize("invalid", [b"invalid WKB", Point(4, 1).wkb])
def test_geometry_decoding_is_selected_and_explicit(tmp_path: Path, invalid: bytes) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from shapely.errors import GEOSException

    from pourpoint import HydroFabric

    path = make_spatial_bundle(tmp_path)
    table = pq.read_table(path / "catchments.parquet")
    values = table["geometry"].to_pylist()
    values[2] = invalid
    table = table.set_column(table.schema.get_field_index("geometry"), table.schema.field("geometry"), pa.array(values))
    _rewrite_family(path, "catchments", table)
    with HydroFabric(path, cache=False) as fabric:
        assert fabric.subbasins([10])[0].geometry.equals(box(1, 1, 1.25, 1.25))
        selected = fabric.subbasins([40])[0]
        assert selected.wkb == invalid
        with pytest.raises((ValueError, GEOSException)):
            _ = selected.geometry


@pytest.mark.parametrize("invalid", [True, 1.5, 0, -1, 2**63])
def test_invalid_record_ids(tmp_path: Path, invalid: object) -> None:
    from pourpoint import HydroFabric

    with (
        HydroFabric(make_spatial_bundle(tmp_path), cache=False) as fabric,
        pytest.raises(ValueError, match="positive int64"),
    ):
        fabric.records("catchments", [invalid])


def test_empty_record_ids_preserve_projected_schema(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from pourpoint import HydroFabric

    path = make_spatial_bundle(tmp_path)
    schema = pq.read_schema(path / "catchments.parquet")
    with HydroFabric(path, cache=False) as fabric:
        result = fabric.records("catchments", [], columns=["source_value", "id"])
        assert result.num_rows == 0
        assert result.schema.equals(
            pa.schema([schema.field("source_value"), schema.field("id")], metadata=schema.metadata), check_metadata=True
        )


def test_sparse_int64_ids_across_lookup_batches(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from pourpoint import HydroFabric

    path = make_spatial_bundle(tmp_path)
    table = pq.read_table(path / "catchments.parquet")
    ids = [2**62, 7, 2**62 + 3, 2**53 + 1]
    table = table.set_column(0, table.schema.field("id"), pa.array(ids, type=pa.int64()))
    _rewrite_family(path, "catchments", table)
    with HydroFabric(path, cache=False) as fabric:
        requested = [2**62 + 3, 7, 2**53 + 1, 2**62, 7]
        assert fabric.records("catchments", requested)["id"].to_pylist() == requested
        with pytest.raises(KeyError):
            fabric.records("catchments", [2**53 + 2])
