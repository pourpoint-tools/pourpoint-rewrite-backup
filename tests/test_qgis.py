import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pyarrow as pa
import pytest
from shapely.geometry import box

from pourpoint.features import Subbasin
from pourpoint.qgis import QGISBundle


def test_portable_vectors_and_lossless_attributes(tmp_path: Path) -> None:
    table = pa.table(
        {
            "id": [7],
            "geometry": [box(0, 0, 1, 1).wkb],
            "optional": pa.array([None], type=pa.int64()),
            "nested": [[1, 2]],
        }
    ).replace_schema_metadata({b"source": b"fixture"})
    folder = QGISBundle().add_vector("basins", [Subbasin(table, 0)]).write(tmp_path / "bundle")
    with sqlite3.connect(folder / "vectors.gpkg") as db:
        row = db.execute("SELECT source_id, geometry FROM layer_0").fetchone()
        assert row[0] == 7
        assert db.execute("SELECT optional FROM layer_0").fetchone() == (None,)
        assert row[1][:2] == b"GP"
        ipc = db.execute("SELECT payload FROM source_arrow").fetchone()[0]
        with pa.ipc.open_stream(ipc) as reader:
            assert reader.read_all().equals(table, check_metadata=True)
    with zipfile.ZipFile(folder / "watershed.qgz") as archive:
        project = ET.fromstring(archive.read("watershed.qgs"))
    assert project.findtext("projectlayers/maplayer/datasource") == "./vectors.gpkg|layername=layer_0"
    assert project.findtext("projectlayers/maplayer/layername") == "basins"


def test_raw_geometry_requires_crs_and_existing_folder_is_not_overwritten(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="CRS"):
        QGISBundle().add_vector("cell", box(0, 0, 1, 1))
    bundle = QGISBundle().add_vector("cell", box(0, 0, 1, 1), crs="EPSG:3857")
    bundle.write(tmp_path / "bundle")
    with pytest.raises(FileExistsError):
        bundle.write(tmp_path / "bundle")


def test_batch_groups_keep_failed_targets(tmp_path: Path) -> None:
    from pourpoint.results import BatchIssue, BatchResult, DelineationResult, Target

    geometry = box(0, 0, 1, 1)
    result = DelineationResult([], geometry, None, None, geometry, geometry, {"license": "CC BY-NC 4.0"})
    batch = BatchResult(
        [result, None],
        [Target(0, "up/stream", geometry), Target(1, "missing", geometry)],
        [BatchIssue(1, "missing", "selection.no_coverage", "outside")],
    )
    folder = QGISBundle().add_result(batch).write(tmp_path / "batch")
    with sqlite3.connect(folder / "vectors.gpkg") as db:
        assert db.execute("SELECT * FROM target_issues").fetchall() == [
            (1, "missing", "selection.no_coverage", "outside")
        ]
        assert db.execute("SELECT target_name,target_index FROM layer_0").fetchall() == [("up/stream", 0)]
        import json

        metadata = json.loads(db.execute("SELECT metadata FROM result_metadata").fetchone()[0])
        assert metadata["license"] == "CC BY-NC 4.0"
        assert db.execute("SELECT original_target, adjusted_target FROM result_metadata").fetchone() == (
            geometry.wkb,
            geometry.wkb,
        )
    with zipfile.ZipFile(folder / "watershed.qgz") as archive:
        project = ET.fromstring(archive.read("watershed.qgs"))
    assert [g.attrib["name"] for g in project.findall("layer-tree-group/layer-tree-group")] == ["up/stream", "missing"]


def test_raster_export_preserves_native_values_and_whole_mask(tmp_path: Path) -> None:
    import numpy as np
    import rasterio
    from affine import Affine
    from rasterio.crs import CRS

    from pourpoint.rasters import RasterGrid, RasterRequest, RasterWindow
    from pourpoint.refinement import FootprintRefinement, OutletCell, RasterSubset, RefinementRasters

    crs = CRS.from_epsg(3857)
    affine = Affine(10, 0, 100, 0, -10, 200)
    grid = RasterGrid(crs, affine, 2, 2, "direction", "accumulation")
    request = RasterRequest(0, 0, 2, 2, grid, object(), 0)
    valid = np.ones((2, 2), dtype=bool)
    window = RasterWindow(
        np.ones((2, 2), dtype="int8"),
        np.array([[1, 2], [3, 4]], dtype="float32"),
        valid,
        valid,
        affine,
        crs,
        "cells",
        request,
        np.array([[128, 64], [32, 16]], dtype="uint8"),
        255,
        -9999,
    )
    mask = np.array([[True, True], [False, True]])
    rasters = RefinementRasters(RasterSubset(window, mask, "direction"), RasterSubset(window, mask, "accumulation"))
    result = FootprintRefinement(box(0, 0, 0.001, 0.001), OutletCell(grid, 0, 0), rasters)
    folder = QGISBundle().add_result(result).write(tmp_path / "rasters")
    with rasterio.open(folder / "raster_0.tif") as raster:
        assert raster.crs == crs
        assert raster.transform == affine
        assert raster.nodata == 255
        np.testing.assert_array_equal(raster.read(1), [[128, 64], [255, 16]])
    with rasterio.open(folder / "raster_1.tif") as raster:
        np.testing.assert_array_equal(raster.read(1), [[1, 2], [-9999, 4]])
    with zipfile.ZipFile(folder / "watershed.qgz") as archive:
        project = ET.fromstring(archive.read("watershed.qgs"))
    outlet = next(
        layer for layer in project.findall("projectlayers/maplayer") if layer.findtext("layername") == "outlet_cell"
    )
    assert outlet.findtext("srs/spatialrefsys/authid") == "EPSG:3857"
    assert outlet.find("renderer-v2/symbols/symbol/layer/Option/Option[@name='style']").attrib["value"] == "no"


def test_rivers_keep_clipped_geometry_and_directed_relationships(tmp_path: Path) -> None:
    from shapely.geometry import LineString

    from pourpoint.features import RiverNetwork
    from pourpoint.rivers import ClippedRiver

    table = pa.table({"id": [10], "geometry": [LineString([(0, 0), (2, 0)]).wkb], "order": [2]})
    relationships = pa.table({"id": [10], "downstream_ids": [[11, 12]]})
    network = RiverNetwork(
        [ClippedRiver(table, 0, LineString([(0, 0), (1, 0)]))],
        relationships,
        [],
        None,
        {"downstream_ids": {"target": "rivers"}},
    )
    folder = QGISBundle().add_vector("streams", network).write(tmp_path / "network")
    with sqlite3.connect(folder / "vectors.gpkg") as db:
        assert db.execute("SELECT source_id,relation,target_id FROM river_connections").fetchall() == [
            (10, "downstream_ids", 11),
            (10, "downstream_ids", 12),
        ]
        import shapely

        geometry = shapely.from_wkb(db.execute("SELECT geometry FROM layer_0").fetchone()[0][8:])
        assert geometry.equals(LineString([(0, 0), (1, 0)]))
        payload = db.execute("SELECT payload FROM source_arrow WHERE kind = 'relationships'").fetchone()[0]
        assert pa.ipc.open_stream(payload).read_all().equals(relationships)
