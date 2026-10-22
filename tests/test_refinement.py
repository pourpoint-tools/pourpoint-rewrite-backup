import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest
from conftest import add_pair
from rasterio.transform import from_origin
from shapely.geometry import Point, box
from spatial_fixture import make_spatial_bundle

from pourpoint.fabric import HydroFabric
from pourpoint.features import Subbasin
from pourpoint.refinement import merge, refine
from pourpoint.results import OperationError


def test_whole_mask_trace_excludes_downstream_branch_and_preserves_attributes(tmp_path: Path) -> None:
    hfx = make_spatial_bundle(tmp_path / "fabric")
    path = hfx / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["auxiliary"] = []
    path.write_text(json.dumps(manifest))
    # All cells flow east. The third unit joins below the requested outlet.
    add_pair(
        hfx,
        direction=np.array([[8, 8, 8, 0]], dtype="uint8"),
        accumulation=np.array([[-999, 2, 3, 4]], dtype="int32"),
        transform=from_origin(0, 1, 1, 1),
    )
    with HydroFabric(hfx) as fabric:
        table = pa.table(
            {
                "id": [10, 20, 30, 40],
                "geometry": [box(i, 0, i + 1, 1).wkb for i in range(4)],
                "source_id": ["ten", "twenty", "thirty", "forty"],
            }
        )
        units = [Subbasin(table, row) for row in range(4)]
        result = refine(fabric=fabric, subbasins=units, target=Point(1.5, 0.5), window_size=1)
        merged = refine(fabric=fabric, footprint=merge(subbasins=units), target=Point(1.5, 0.5), window_size=1)
    assert merge(subbasins=result.subbasins).equals(box(0, 0, 2, 1))
    assert merge(subbasins=result.subbasins).equals(merged.footprint)
    assert [p.id for p in result.subbasins] == [10, 20]
    assert result.subbasins[0].attributes["source_id"] == "ten"
    assert (result.outlet_cell.row, result.outlet_cell.col) == (0, 1)
    assert result.outlet_cell.center.equals(Point(1.5, 0.5))
    assert result.outlet_cell.geometry.equals(box(1, 0, 2, 1))
    assert result.rasters.direction.mask.tolist() == [[True] * 4]
    assert result.rasters.direction.window.raw_direction.tolist() == [[8, 8, 8, 0]]
    assert result.rasters.direction.window.direction_nodata == 255
    assert result.rasters.accumulation.window.accumulation_nodata == -999


@pytest.mark.parametrize("terminal", [-8, 0, -128])
def test_terminal_or_invalid_cell_does_not_flow_to_neighbor(tmp_path: Path, terminal: int) -> None:
    root = make_spatial_bundle(tmp_path / "fabric")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["auxiliary"] = []
    path.write_text(json.dumps(manifest))
    add_pair(
        root,
        direction=np.array([[8, terminal, 0]], dtype="int8"),
        accumulation=np.array([[1, 2, 3]], dtype="int32"),
        transform=from_origin(0, 1, 1, 1),
    )
    with HydroFabric(root) as fabric:
        result = refine(fabric=fabric, footprint=box(0, 0, 3, 1), target=Point(2.5, 0.5), window_size=1)
    assert result.footprint.equals(box(2, 0, 3, 1))


def test_mask_hole_breaks_flow_and_local_ties_are_row_major(tmp_path: Path) -> None:
    root = make_spatial_bundle(tmp_path / "fabric")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["auxiliary"] = []
    path.write_text(json.dumps(manifest))
    add_pair(
        root,
        direction=np.array([[8, 8, 8, 0]], dtype="uint8"),
        accumulation=np.array([[9, 9, 9, 9]], dtype="int32"),
        transform=from_origin(0, 1, 1, 1),
    )
    mask = box(0, 0, 1, 1).union(box(2, 0, 4, 1))
    with HydroFabric(root) as fabric:
        result = refine(fabric=fabric, footprint=mask, target=Point(2.5, 0.5))
    assert result.outlet_cell.col == 2
    assert result.footprint.equals(box(2, 0, 3, 1))
    assert result.rasters.direction.mask.tolist() == [[True, False, True, True]]


def test_missing_rasters_is_explicit_and_invalid_window_is_configuration_error(tmp_path: Path) -> None:
    with HydroFabric(make_spatial_bundle(tmp_path / "fabric")) as fabric:
        with pytest.raises(OperationError) as caught:
            refine(fabric=fabric, footprint=box(0, 0, 1, 1), target=Point(0.5, 0.5))
        assert caught.value.code == "refinement.missing_rasters"
        with pytest.raises(ValueError, match="positive odd"):
            refine(fabric=fabric, footprint=box(0, 0, 1, 1), target=Point(0.5, 0.5), window_size=2)


def test_merge_nested_groups_and_empty() -> None:
    table = pa.table({"id": [1, 2], "geometry": [box(0, 0, 1, 1).wkb, box(1, 0, 2, 1).wkb]})
    parts = [Subbasin(table, i) for i in range(2)]
    merged = merge(subbasins=[parts, [parts[0]], []])
    assert merged[0].equals(box(0, 0, 2, 1))
    assert merged[1].equals(parts[0].geometry)
    assert merged[2].is_empty


def test_nonzero_window_offset_and_non_aligned_unit_boundaries(tmp_path: Path) -> None:
    root = make_spatial_bundle(tmp_path / "fabric")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["auxiliary"] = []
    path.write_text(json.dumps(manifest))
    add_pair(
        root,
        direction=np.full((4, 5), 8, dtype="uint8"),
        accumulation=np.arange(20, dtype="int32").reshape(4, 5),
        transform=from_origin(0, 4, 1, 1),
    )
    table = pa.table({"id": [1, 2], "geometry": [box(1.2, 1.2, 2.3, 2.8).wkb, box(2.3, 1.2, 3.8, 2.8).wkb]})
    parts = [Subbasin(table, i) for i in range(2)]
    with HydroFabric(root) as fabric:
        granular = refine(fabric=fabric, subbasins=parts, target=Point(3.5, 1.5), window_size=1)
        merged = refine(fabric=fabric, footprint=merge(subbasins=parts), target=Point(3.5, 1.5), window_size=1)
    assert (granular.outlet_cell.row, granular.outlet_cell.col) == (2, 3)
    assert granular.outlet_cell.geometry.equals(box(3, 1, 4, 2))
    assert merge(subbasins=granular.subbasins).equals(merged.footprint)
    assert merged.footprint.equals(box(1.2, 1.2, 3.8, 2))


@pytest.mark.parametrize("case", ["coverage", "no_outlet", "invalid_direction"])
def test_refinement_does_not_fallback_on_unusable_rasters(tmp_path: Path, case: str) -> None:
    root = make_spatial_bundle(tmp_path / "fabric")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["auxiliary"] = []
    path.write_text(json.dumps(manifest))
    add_pair(
        root,
        direction=np.array([[9 if case == "invalid_direction" else 8]], dtype="uint8"),
        accumulation=np.array([[-999 if case == "no_outlet" else 1]], dtype="int32"),
        transform=from_origin(0, 1, 1, 1),
    )
    with HydroFabric(root) as fabric:
        if case == "invalid_direction":
            with pytest.raises(ValueError, match="invalid consumed direction"):
                refine(fabric=fabric, footprint=box(0, 0, 1, 1), target=Point(0.5, 0.5))
        else:
            footprint = box(0, 0, 2 if case == "coverage" else 1, 1)
            with pytest.raises(OperationError) as caught:
                refine(fabric=fabric, footprint=footprint, target=Point(0.5, 0.5))
            assert caught.value.code == ("refinement.no_coverage" if case == "coverage" else "refinement.no_outlet")


def test_three_by_three_ties_choose_first_source_row_then_column(tmp_path: Path) -> None:
    root = make_spatial_bundle(tmp_path / "fabric")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["auxiliary"] = []
    path.write_text(json.dumps(manifest))
    add_pair(
        root,
        direction=np.zeros((3, 3), dtype="uint8"),
        accumulation=np.array([[1, 9, 9], [9, 1, 1], [1, 1, 1]], dtype="int32"),
        transform=from_origin(0, 3, 1, 1),
    )
    with HydroFabric(root) as fabric:
        result = refine(fabric=fabric, footprint=box(0, 0, 3, 3), target=Point(1.5, 1.5))
    assert (result.outlet_cell.row, result.outlet_cell.col) == (0, 1)
    assert result.footprint.equals(box(1, 2, 2, 3))
