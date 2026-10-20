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
