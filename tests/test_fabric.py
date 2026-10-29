import json
from pathlib import Path

import pytest
from test_metadata import spatial_manifest_data


def test_fabric_opens_only_manifest_and_closes_source(tmp_path: Path) -> None:
    from pourpoint import HydroFabric

    (tmp_path / "manifest.json").write_text(json.dumps(spatial_manifest_data()))
    with HydroFabric(tmp_path, cache=False) as fabric:
        assert set(fabric.manifest.families) == {"catchments", "rivers", "river_nodes"}
        assert fabric.buffer_bytes["vectors"] == 0
    with pytest.raises(RuntimeError, match="closed"):
        fabric.records("rivers", [1])


def test_materialized_rows_and_relationships_survive_close(tmp_path: Path) -> None:
    from spatial_fixture import make_spatial_bundle

    from pourpoint import HydroFabric

    source = make_spatial_bundle(tmp_path / "source")
    with HydroFabric(source, cache_dir=tmp_path / "cache") as fabric:
        basins = fabric.subbasins([20, 10, 20])
        network = fabric.river_network([20])
        assert fabric.traverse("rivers", [10], relationship="downstream_ids") == [10, 20, 30, 40]
        assert network.boundary_river_ids == frozenset({10, 40})
        assert [node.id for node in network.nodes] == [20, 30]
        assert network.node_relationships["outgoing_river_ids"].to_pylist() == [[20, 30], [40]]
        assert network.rivers[0].geometry.geom_type == "LineString"
        assert basins[0].geometry.geom_type == "Polygon"
    assert [basin.id for basin in basins] == [20, 10, 20]
    assert basins[1].attributes["source_value"] is None
    assert network.relationships["upstream_ids"].to_pylist() == [[10]]
    with HydroFabric(source, cache_dir=tmp_path / "cache", offline=True) as reopened:
        assert reopened.records("catchments", [20, 10, 20]).equals(basins[0].table, check_metadata=True)


def test_local_associations_preserve_plural_singleton_and_known_empty(tmp_path: Path) -> None:
    import pyarrow as pa
    from shapely.geometry import box
    from spatial_fixture import make_spatial_bundle

    from pourpoint import HydroFabric

    source = make_spatial_bundle(tmp_path / "source", local_associations=True)
    with HydroFabric(source) as fabric:
        records = fabric.records("rivers", [30, 10, 20])
        assert "local_catchment_id" not in records.column_names
        assert records["local_catchment_coverage"].to_pylist() == ["supplied"] * 3
        relations = fabric.relationships("rivers", [30, 10, 20], columns=["id", "local_catchment_ids"])
        assert relations.to_pylist() == [
            {"id": 30, "local_catchment_ids": []},
            {"id": 10, "local_catchment_ids": [10, 20]},
            {"id": 20, "local_catchment_ids": [30]},
        ]
        network = fabric.river_network([30, 10, 20])
        assert network.relationships["local_catchment_ids"].to_pylist() == [[], [10, 20], [30]]
        assert [river.id for river in network.rivers] == [30, 10, 20]
        assert [river.attributes["local_catchment_coverage"] for river in network.rivers] == ["supplied"] * 3
        assert [river.attributes["streamID"] for river in network.rivers] == [
            5_000_000_030,
            5_000_000_010,
            5_000_000_020,
        ]
        assert all(river.attributes.scalar("streamID").type == pa.int64() for river in network.rivers)
        assert fabric.subbasins(relations["local_catchment_ids"][0].as_py()) == []
        singleton = fabric.subbasins(relations["local_catchment_ids"][2].as_py())
        assert [basin.id for basin in singleton] == [30]
        assert singleton[0].attributes["streamID"] == 5_000_000_020
        basins = fabric.subbasins(relations["local_catchment_ids"][1].as_py())
        assert [basin.id for basin in basins] == [10, 20]
        assert [basin.attributes["streamID"] for basin in basins] == [5_000_000_010, 5_000_000_010]
        assert all(basin.attributes.scalar("streamID").type == pa.int64() for basin in basins)
        assert [basin.wkb for basin in basins] == [box(1, 1, 1.25, 1.25).wkb, box(2, 1, 2.25, 1.25).wkb]
        assert [basin.attributes["source_value"] for basin in basins] == [None, 2000]
        reverse = fabric.relationships("catchments", [20, 30, 10], columns=["id", "local_river_ids"])
        assert reverse.to_pylist() == [
            {"id": 20, "local_river_ids": [10]},
            {"id": 30, "local_river_ids": [20]},
            {"id": 10, "local_river_ids": [10]},
        ]
        linked = fabric.records("rivers", [ids[0] for ids in reverse["local_river_ids"].to_pylist()])
        assert linked["id"].to_pylist() == [10, 20, 10]
        assert linked["source_value"].to_pylist() == [None, 2000, None]
