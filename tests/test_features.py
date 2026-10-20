import pyarrow as pa
from shapely import LineString, to_wkb


def test_materialized_features_retain_arrow_attributes_and_exact_wkb() -> None:
    from pourpoint.features import River

    wkb = to_wkb(LineString([(0, 0), (1, 1)]), byte_order=0)
    schema = pa.schema(
        [
            pa.field("id", pa.int64()),
            pa.field("geometry", pa.binary()),
            pa.field("source_order", pa.int64(), metadata={b"meaning": b"source only"}),
            pa.field("tags", pa.list_(pa.string())),
        ],
        metadata={b"source": b"fixture"},
    )
    table = pa.Table.from_pylist([{"id": 7, "geometry": wkb, "source_order": None, "tags": ["a", "b"]}], schema=schema)
    river = River(table, 0)
    assert river.id == 7
    assert river.wkb == wkb
    assert river.geometry.equals(LineString([(0, 0), (1, 1)]))
    assert river.attributes["source_order"] is None
    assert river.attributes["tags"] == ["a", "b"]
    assert river.attributes.scalar("tags").type == pa.list_(pa.string())
    assert river.attributes.schema.field("source_order").metadata == {b"meaning": b"source only"}
    assert river.attributes.schema.metadata == {b"source": b"fixture"}
    assert list(river.attributes) == ["id", "source_order", "tags"]


def test_network_retains_omitted_neighbors_without_inventing_outlets() -> None:
    from pourpoint.features import River, RiverNetwork

    table = pa.table({"id": [2], "geometry": [to_wkb(LineString([(0, 0), (1, 1)]))]})
    relationships = pa.table({"id": [2], "upstream_ids": [[1]], "downstream_ids": [[3, 4]], "parent_ids": [[90]]})
    network = RiverNetwork([River(table, 0)], relationships, [], None, {"topology": "dag"})
    assert network.boundary_river_ids == frozenset({1, 3, 4})
    assert network.relationships["parent_ids"].to_pylist() == [[90]]
    assert network.geometry.equals(LineString([(0, 0), (1, 1)]))
