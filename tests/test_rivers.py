from pathlib import Path

import pytest
from shapely.geometry import LineString, Point, box
from spatial_fixture import make_spatial_bundle

from pourpoint.fabric import HydroFabric


def test_clipping_preserves_source_and_combines_parts(tmp_path: Path) -> None:
    from pourpoint.rivers import select_rivers

    with HydroFabric(make_spatial_bundle(tmp_path / "fabric")) as fabric:
        network = select_rivers(fabric=fabric, target=box(1.05, 1, 1.2, 2))
        assert [r.id for r in network.rivers] == [10]
        river = network.rivers[0]
        assert river.geometry.equals(LineString([(1.05, 1.05), (1.2, 1.2)]))
        assert river.attributes["source_value"] is None
        assert river.source.geometry.equals(LineString([(1, 1), (1.25, 1.25)]))
        assert network.relationships["downstream_ids"].to_pylist() == [[20, 30]]


def test_snap_metres_and_no_match() -> None:
    from pourpoint.results import OperationError
    from pourpoint.rivers import snap

    line = LineString([(0, -1), (0, 1)])
    result = snap(target=Point(0.001, 0), onto=line, radius=120)
    assert result.distance(Point(0, 0)) < 1e-8
    with pytest.raises(OperationError, match="range"):
        snap(target=Point(0.001, 0), onto=line, radius=100)


def river_publication(path: Path, *, orientation: int = 1, nodes: bool = True) -> Path:
    """Four directed features: trunk, two branches, reconvergence."""
    import hashlib
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    from spatial_fixture import write_ipc

    make_spatial_bundle(path)
    manifest = json.loads((path / "manifest.json").read_text())
    lines = {
        10: LineString([(0, 0), (2, 0)]),
        20: LineString([(2, 0), (3, 1), (4, 0)]),
        30: LineString([(2, 0), (3, -1), (4, 0)]),
        40: LineString([(4, 0), (5, 0)]),
    }
    points = {10: Point(0, 0), 20: Point(2, 0), 30: Point(4, 0), 40: Point(5, 0)}
    for domain, geometries in (("rivers", lines), ("river_nodes", points)):
        file = path / f"{domain}.parquet"
        table = pq.read_table(file)
        rows = table.to_pylist()
        for row in rows:
            geometry = geometries[row["id"]]
            if domain == "rivers" and orientation == 2:
                from shapely import reverse

                geometry = reverse(geometry)
            if domain == "river_nodes" and not nodes:
                geometry = Point(99, 99)
            row.update(
                level=1,
                geometry=geometry.wkb,
                bbox=dict(zip(("xmin", "ymin", "xmax", "ymax"), geometry.bounds, strict=True)),
            )
        table = pa.Table.from_pylist(rows, schema=table.schema)
        if domain == "rivers":
            table = table.append_column("geometry_orientation", pa.array([orientation] * 4, type=pa.int8()))
        pq.write_table(table, file, row_group_size=2)
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        manifest["families"][domain]["data_sha256"] = digest
        for declaration in manifest["relationships"].values():
            if domain in declaration["feature_artifacts"]:
                declaration["feature_artifacts"][domain]["data_sha256"] = digest
        lookup_path = path / f"{domain}-lookup.arrow"
        with pa.memory_map(str(lookup_path), "r") as stream:
            lookup = pa.ipc.open_file(stream).read_all()
        write_ipc(lookup_path, lookup.replace_schema_metadata({b"hfx:data_sha256": digest.encode()}))
    for name in manifest["artifacts"]:
        raw = (path / name).read_bytes()
        manifest["artifacts"][name] = {
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "head_sha256": hashlib.sha256(raw[:65536]).hexdigest(),
            "tail_sha256": hashlib.sha256(raw[-65536:]).hexdigest(),
        }
    (path / "manifest.json").write_text(json.dumps(manifest))
    return path


@pytest.mark.parametrize("orientation", [0, 1, 2])
def test_downstream_boundary_suffix_branches_and_reconvergence(tmp_path: Path, orientation: int) -> None:
    from pourpoint.rivers import select_rivers

    with HydroFabric(river_publication(tmp_path / "rivers", orientation=orientation)) as fabric:
        network = select_rivers(fabric=fabric, target=box(0.5, -0.5, 1.5, 0.5), include_downstream=True)
    assert [r.id for r in network.rivers] == [10, 20, 30, 40]
    assert network.rivers[0].geometry.equals(LineString([(0.5, 0), (2, 0)]))
    assert network.rivers[1].geometry.equals(LineString([(2, 0), (3, 1), (4, 0)]))
    assert network.rivers[2].geometry.equals(LineString([(2, 0), (3, -1), (4, 0)]))
    assert network.rivers[3].geometry.equals(LineString([(4, 0), (5, 0)]))
    assert len(network.nodes) == 4


def test_ambiguous_crossing_does_not_guess_geometry_order(tmp_path: Path) -> None:
    from pourpoint.results import OperationError
    from pourpoint.rivers import select_rivers

    with (
        HydroFabric(river_publication(tmp_path / "rivers", orientation=0, nodes=False)) as fabric,
        pytest.raises(OperationError) as exc,
    ):
        select_rivers(fabric=fabric, target=box(0.5, -0.5, 1.5, 0.5), include_downstream=True)
    assert exc.value.code == "rivers.ambiguous_direction"


def test_constituent_boundaries_do_not_extend_rivers(tmp_path: Path) -> None:
    import pyarrow as pa
    from shapely import union_all

    from pourpoint.features import Subbasin
    from pourpoint.rivers import select_rivers

    geometries = [box(0.5, -0.5, 1, 0.5), box(1, -0.5, 1.5, 0.5)]
    table = pa.table({"id": [100, 200], "geometry": [g.wkb for g in geometries]})
    with HydroFabric(river_publication(tmp_path / "rivers")) as fabric:
        granular = select_rivers(fabric=fabric, target=[Subbasin(table, 0), Subbasin(table, 1)])
        merged = select_rivers(fabric=fabric, target=union_all(geometries))
        assert granular.geometry.equals(merged.geometry)
        assert granular.geometry.equals(LineString([(0.5, 0), (1.5, 0)]))
        empty = select_rivers(fabric=fabric, target=box(50, 50, 51, 51))
        assert empty.rivers == []


def test_snap_zero_radius_retains_exact_on_stream_point() -> None:
    from pourpoint.rivers import snap

    target = Point(0, 0)
    assert snap(target=target, onto=LineString([(0, -1), (0, 1)]), radius=0).equals(target)


def test_contribution_footprint_uses_clipped_geometry(tmp_path: Path) -> None:
    import pyarrow as pa

    from pourpoint.features import Subbasin
    from pourpoint.refinement import SubbasinContribution
    from pourpoint.rivers import select_rivers

    table = pa.table({"id": [100], "geometry": [box(0, -1, 5, 1).wkb]})
    contribution = SubbasinContribution(Subbasin(table, 0), box(0.5, -0.5, 1.5, 0.5))
    with HydroFabric(river_publication(tmp_path / "rivers")) as fabric:
        selected = select_rivers(fabric=fabric, target=contribution)
    assert selected.geometry.equals(LineString([(0.5, 0), (1.5, 0)]))


def test_snap_uses_ground_distance_not_degree_distance_at_high_latitude() -> None:
    from shapely.geometry import MultiLineString

    from pourpoint.rivers import snap

    # 0.01 longitude degree at 80N is ~194 m, versus ~223 m for 0.002 latitude.
    # Degree-space nearest selection would incorrectly choose the northern line.
    streams = MultiLineString([[(0.01, 79.99), (0.01, 80.01)], [(-0.01, 80.002), (0.01, 80.002)]])
    selected = snap(target=Point(0, 80), onto=streams, radius=200)
    assert selected.x == pytest.approx(0.01)
    assert selected.y == pytest.approx(80, abs=1e-6)


def test_snap_rejects_unwrapped_antimeridian_segment() -> None:
    from pourpoint.rivers import snap

    with pytest.raises(ValueError, match="antimeridian"):
        snap(target=Point(180, 0), onto=LineString([(179, 0), (-179, 0)]), radius=200_000)


def test_snap_endpoint_ties_and_late_validation() -> None:
    from shapely.geometry import MultiLineString

    from pourpoint.rivers import snap

    target = Point(0, 0)
    for x in (1, -1):
        lines = MultiLineString([[(x, 0), (2 * x, 0)], [(-x, 0), (-2 * x, 0)]])
        assert snap(target=target, onto=lines, radius=120_000).equals(Point(x, 0))
    assert snap(target=target, onto=LineString([(1, 0), (1, 0)]), radius=120_000).equals(Point(1, 0))
    with pytest.raises(ValueError, match="antimeridian"):
        snap(target=target, onto=MultiLineString([[(-1, 0), (1, 0)], [(179, 0), (-179, 0)]]), radius=0)
