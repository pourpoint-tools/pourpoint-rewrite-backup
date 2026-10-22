from pathlib import Path

import pytest
from shapely.geometry import Point, box
from spatial_fixture import make_spatial_bundle

from pourpoint import HydroFabric
from pourpoint.operations import select_upstream_subbasins
from pourpoint.results import OperationError


def test_point_selection_is_inclusive_and_requested_level(tmp_path: Path) -> None:
    source = make_spatial_bundle(tmp_path / "source")
    with HydroFabric(source, cache=False) as fabric:
        selected = select_upstream_subbasins(fabric=fabric, target=Point(2.1, 1.1))
        assert [unit.id for unit in selected] == [20, 10]
        assert selected[1].attributes["source_value"] is None
        with pytest.raises(OperationError, match="coverage"):
            select_upstream_subbasins(fabric=fabric, target=Point(3.1, 1.1))
        # Polygon boundary membership is inclusive.
        selected = select_upstream_subbasins(fabric=fabric, target=box(1.25, 1, 1.5, 1.25))
        assert [unit.id for unit in selected] == [10]


def test_batch_identity_partial_failure_and_duplicate_targets(tmp_path: Path) -> None:
    from pourpoint.results import BatchResult

    source = make_spatial_bundle(tmp_path / "source")
    with HydroFabric(source, cache_dir=tmp_path / "cache") as fabric:
        result = select_upstream_subbasins(
            fabric=fabric, target={"a": Point(2.1, 1.1), "bad": Point(9, 9), "b": Point(2.1, 1.1)}, n_jobs=2
        )
        assert isinstance(result, BatchResult)
        assert [target.name for target in result.targets] == ["a", "bad", "b"]
        assert [[unit.id for unit in units] if units is not None else None for units in result.results] == [
            [20, 10],
            None,
            [20, 10],
        ]
        assert [(issue.index, issue.name, issue.code) for issue in result.issues] == [
            (1, "bad", "selection.no_coverage")
        ]
        first = select_upstream_subbasins(fabric=fabric, target=[Point(1.1, 1.1), Point(1.1, 1.1)], n_jobs=1)
        second = select_upstream_subbasins(fabric=fabric, target=[Point(1.1, 1.1)], n_jobs=1)
        assert len({t.name for t in first.targets + second.targets}) == 3
    with HydroFabric(source, cache_dir=tmp_path / "cache", offline=True) as fabric:
        replay = select_upstream_subbasins(fabric=fabric, target={"a": Point(2.1, 1.1)}, n_jobs=2)
        assert [u.id for u in replay.results[0]] == [20, 10]


def test_unavailable_connectivity_and_invalid_shared_configuration(tmp_path: Path) -> None:
    import json

    source = make_spatial_bundle(tmp_path / "source")
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["catchment_connectivity"] = [{"level": 1, "status": "not_supplied"}]
    manifest_path.write_text(json.dumps(manifest))
    with HydroFabric(source, cache=False) as fabric:
        with pytest.raises(OperationError) as caught:
            select_upstream_subbasins(fabric=fabric, target=Point(1.1, 1.1))
        assert caught.value.code == "selection.connectivity_unavailable"
        with pytest.raises(ValueError, match="n_jobs"):
            select_upstream_subbasins(fabric=fabric, target=[Point(1.1, 1.1)], n_jobs=0)


def test_batch_does_not_swallow_programming_or_integrity_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> object:
        raise RuntimeError("unexpected programming failure")

    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:
        monkeypatch.setattr(fabric, "candidates", broken)
        with pytest.raises(RuntimeError, match="unexpected programming"):
            select_upstream_subbasins(fabric=fabric, target=[Point(1.1, 1.1)])


def test_batch_worker_bound_context_reuse_and_concurrency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from pourpoint._batch import execute, identify

    barrier = threading.Barrier(2)
    lock = threading.Lock()
    seen: dict[int, list[int]] = {}
    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:

        def operation(reader: HydroFabric, point: Point) -> int:
            assert reader is not fabric
            thread = threading.get_ident()
            with lock:
                first = thread not in seen
                seen.setdefault(thread, []).append(id(reader))
            if first:
                barrier.wait(timeout=10)
            return int(point.geometry.x)

        result = execute(fabric, identify([Point(i, 1) for i in range(6)]), operation, 2)
    assert result.results == list(range(6))
    assert len(seen) == 2
    assert all(len(ids) == 3 and len(set(ids)) == 1 for ids in seen.values())


def test_delineate_composes_selection_and_requested_merge(tmp_path: Path) -> None:
    from pourpoint.operations import delineate

    source = make_spatial_bundle(tmp_path / "source")
    point = Point(2.1, 1.1)
    with HydroFabric(source, cache=False) as fabric:
        result = delineate(fabric=fabric, target=point, merge=False)
        assert [unit.id for unit in result.subbasins] == [20, 10]
        assert result.geometry is None
        assert result.outlet_cell is None and result.rasters is None
        assert result.original_target.equals(point) and result.target.equals(point)
        assert result.provenance["fabric_name"] == "example"


def test_empty_target_is_valid_empty_selection(tmp_path: Path) -> None:
    from shapely.geometry import Polygon

    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:
        assert select_upstream_subbasins(fabric=fabric, target=Polygon()) == []


def test_point_boundary_ownership_uses_smallest_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pyarrow as pa

    from pourpoint.features import Subbasin

    table = pa.table({"id": [20, 10], "level": [1, 1], "geometry": [box(0, 0, 1, 1).wkb, box(1, 0, 2, 1).wkb]})
    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:
        monkeypatch.setattr(fabric, "candidates", lambda *a, **kw: table)
        monkeypatch.setattr(fabric, "traverse", lambda domain, ids: ids)
        monkeypatch.setattr(fabric, "subbasins", lambda ids: [Subbasin(table, 1 if i == 10 else 0) for i in ids])
        assert [u.id for u in select_upstream_subbasins(fabric=fabric, target=Point(1, 0.5))] == [10]
        assert [u.id for u in select_upstream_subbasins(fabric=fabric, target=box(0.5, 0.25, 1.5, 0.75))] == [10, 20]


def test_explicit_batch_refinement_preserves_selection_identity(tmp_path: Path) -> None:
    from pourpoint import refine
    from pourpoint.results import BatchResult

    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:
        selection = select_upstream_subbasins(fabric=fabric, target={"ok": Point(1.1, 1.1), "bad": Point(9, 9)})
        refined = refine(fabric=fabric, subbasins=selection, n_jobs=2)
        assert isinstance(refined, BatchResult)
        assert refined.targets is selection.targets
        assert refined.results == [None, None]
        assert [(i.index, i.name, i.code) for i in refined.issues] == [
            (0, "ok", "refinement.missing_rasters"),
            (1, "bad", "selection.no_coverage"),
        ]


def test_delineate_refinement_failure_is_not_coarse_success(tmp_path: Path) -> None:
    from pourpoint import delineate

    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:
        with pytest.raises(OperationError) as caught:
            delineate(fabric=fabric, target=Point(1.1, 1.1), refine=True)
        assert caught.value.code == "refinement.missing_rasters"
        batch = delineate(fabric=fabric, target={"station": Point(1.1, 1.1)}, refine=True)
        assert batch.results == [None]
        assert batch.issues[0].code == "refinement.missing_rasters"


def test_refined_named_batch_exports_one_pair_per_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    import sqlite3

    import numpy as np
    import pyarrow as pa
    from conftest import add_pair
    from rasterio.transform import from_origin

    from pourpoint import QGISBundle, delineate
    from pourpoint.features import Subbasin

    source = make_spatial_bundle(tmp_path / "source")
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["auxiliary"] = []
    manifest_path.write_text(json.dumps(manifest))
    add_pair(
        source,
        direction=np.array([[8, 8, 8, 0]], dtype="uint8"),
        accumulation=np.array([[1, 2, 3, 4]], dtype="int32"),
        transform=from_origin(0, 1, 1, 1),
    )
    table = pa.table({"id": [1], "level": [1], "geometry": [box(0, 0, 4, 1).wkb]})
    with HydroFabric(source, cache=False) as fabric:
        monkeypatch.setattr(fabric, "candidates", lambda *a, **kw: table)
        monkeypatch.setattr(fabric, "traverse", lambda domain, ids: ids)
        monkeypatch.setattr(fabric, "subbasins", lambda ids: [Subbasin(table, 0)])
        result = delineate(
            fabric=fabric, target={"upper": Point(1.5, 0.5), "lower": Point(2.5, 0.5)}, refine=True, window_size=1
        )
        assert result.results[0].geometry.equals(box(0, 0, 2, 1))
        assert result.results[1].geometry.equals(box(0, 0, 3, 1))
        folder = QGISBundle().add_result(result).write(tmp_path / "export")
    assert len(list(folder.glob("*.tif"))) == 4
    with sqlite3.connect(folder / "vectors.gpkg") as db:
        names = db.execute("SELECT DISTINCT target_name FROM layer_0").fetchall()
        assert names == [("upper",)]


@pytest.mark.parametrize("n_jobs", [1, 2])
def test_refinement_batch_isolates_empty_point(tmp_path: Path, n_jobs: int) -> None:
    import json

    import numpy as np
    from conftest import add_pair
    from rasterio.transform import from_origin

    from pourpoint import refine

    source = make_spatial_bundle(tmp_path / "source")
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["auxiliary"] = []
    manifest_path.write_text(json.dumps(manifest))
    add_pair(
        source,
        direction=np.array([[0]], dtype="uint8"),
        accumulation=np.array([[1]], dtype="int32"),
        transform=from_origin(1, 1.25, 0.25, 0.25),
    )
    with HydroFabric(source, cache=False) as fabric:
        selection = select_upstream_subbasins(
            fabric=fabric, target={"empty": Point(), "station": Point(1.125, 1.125)}, n_jobs=n_jobs
        )
        result = refine(fabric=fabric, subbasins=selection, window_size=1, n_jobs=n_jobs)
        assert result.targets is selection.targets
        assert result.results[0] is None
        successful = result.results[1]
        assert successful is not None
        assert successful.subbasins[0].id == 10
        assert successful.subbasins[0].geometry.equals(box(1, 1, 1.25, 1.25))
        assert [(issue.index, issue.name, issue.code) for issue in result.issues] == [
            (0, "empty", "refinement.point_required")
        ]
        with pytest.raises(ValueError, match="window_size"):
            refine(fabric=fabric, subbasins=selection, window_size=2, n_jobs=n_jobs)


@pytest.mark.parametrize("n_jobs", [1, 2])
def test_snapping_delineation_batch_isolates_empty_point(tmp_path: Path, n_jobs: int) -> None:
    from shapely.geometry import LineString

    from pourpoint import delineate

    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:
        targets = {"empty": Point(), "station": Point(1.125, 1.125)}
        stream = LineString([(1, 1), (1.25, 1.25)])
        result = delineate(fabric=fabric, target=targets, snap_onto=stream, n_jobs=n_jobs)
        assert [target.name for target in result.targets] == ["empty", "station"]
        assert result.results[0] is None
        successful = result.results[1]
        assert successful is not None
        assert [unit.id for unit in successful.subbasins] == [10]
        assert successful.target.equals(targets["station"])
        assert [(issue.index, issue.name, issue.code) for issue in result.issues] == [
            (0, "empty", "snap.point_required")
        ]
        with pytest.raises(ValueError, match="radius"):
            delineate(fabric=fabric, target=targets, snap_onto=stream, radius=-1, n_jobs=n_jobs)
        with pytest.raises(ValueError, match="stream line geometry"):
            delineate(fabric=fabric, target=targets, snap_onto=box(1, 1, 1.25, 1.25), n_jobs=n_jobs)
