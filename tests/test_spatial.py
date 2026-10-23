from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import refresh_fingerprint
from shapely.geometry import Point

from pourpoint import Dataset


def test_spatial_catchment_levels_and_conservative_boundaries(hfx: Path) -> None:
    with Dataset.open(hfx, cache_dir=hfx / "cache") as data:
        batch = data.catchment_candidates((0.5, 0.5, 1.0, 0.5), levels=[1])
        assert batch.table["id"].to_pylist() == [20, 10]
        assert data.catchment_candidates((0.5, 0.5, 0.5, 0.5), levels=[0]).table["id"].to_pylist() == [9000000000]
        assert data.catchment_candidates((10, 10, 11, 11)).table.num_rows == 0
        with pytest.raises(ValueError, match="level"):
            data.catchment_candidates((0, 0, 1, 1), levels=[9])


def test_snap_sets_many_to_one_null_and_absent_bounds(hfx: Path) -> None:
    with Dataset.open(hfx, cache_dir=hfx / "cache") as data:
        result = data.snap_candidates("fine", (0.4, 0.4, 0.7, 0.6), levels=[1])
        assert result.attributes["id"].to_pylist() == [101, 102]
        assert result.attributes["unit_id"].to_pylist() == [10, 10]
        assert result.attributes["weight"].to_pylist() == [1, 2]
        assert result.attributes["stem_role"].to_pylist() == [None, "mainstem"]
        coarse = data.snap_candidates("coarse", (0.5, 0.5, 0.5, 0.5))
        assert coarse.attributes["id"].to_pylist() == [201]
        assert coarse.geometries[0].equals_exact(Point(0.5, 0.5), 0)
        assert data.snap_candidates("fine", (0, 0, 4, 1), levels=[0]).attributes.num_rows == 0


def test_unselected_malformed_geometry_is_not_decoded(hfx: Path) -> None:
    path = hfx / "fine.parquet"
    table = pq.read_table(path)
    values = table["geometry"].to_pylist()
    values[2] = b"invalid WKB"
    table = table.set_column(
        table.schema.get_field_index("geometry"), table.schema.field("geometry"), pa.array(values, type=pa.binary())
    )
    pq.write_table(table, path, row_group_size=2)
    refresh_fingerprint(hfx, "fine.parquet")
    with Dataset.open(hfx, cache_dir=hfx / "cache") as data:
        assert data.snap_candidates("fine", (0.4, 0.4, 0.7, 0.6)).attributes.num_rows == 2
        with pytest.raises(ValueError, match="geometry"):
            data.snap_candidates("fine", (3, 0, 4, 1))


def test_float32_bounds_do_not_exclude_full_precision_point(hfx: Path) -> None:
    # float32(0.1) rounds above 0.1. A direct xmin<=x test would discard the point.
    path = hfx / "fine.parquet"
    table = pq.read_table(path).slice(0, 1)
    table = table.set_column(
        table.schema.get_field_index("bbox"),
        table.schema.field("bbox"),
        pa.array([dict.fromkeys(("xmin", "ymin", "xmax", "ymax"), 0.1)], type=table["bbox"].type),
    )
    table = table.set_column(
        table.schema.get_field_index("geometry"),
        table.schema.field("geometry"),
        pa.array([Point(0.1, 0.1).wkb], type=pa.binary()),
    )
    pq.write_table(table, path)
    refresh_fingerprint(hfx, "fine.parquet")
    with Dataset.open(hfx, cache_dir=hfx / "cache") as data:
        assert data.snap_candidates("fine", (0.1, 0.1, 0.1, 0.1)).attributes["id"].to_pylist() == [101]


def test_graph_statistics_prune_before_catchment_lookup(hfx: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = hfx / "graph.parquet"
    table = pq.read_table(path)
    pq.write_table(table, path, row_group_size=1)
    refresh_fingerprint(hfx, "graph.parquet")
    reads: list[tuple[int, list[str] | None]] = []
    original = pq.ParquetFile.read_row_group

    def observed(reader: pq.ParquetFile, group: int, columns: list[str] | None = None, **kwargs: object) -> pa.Table:
        reads.append((group, columns))
        return original(reader, group, columns=columns, **kwargs)

    monkeypatch.setattr(pq.ParquetFile, "read_row_group", observed)
    with Dataset.open(hfx) as data:
        selected = data.catchment_candidates((0.5, 0.5, 0.5, 0.5), levels=[1])
        assert selected.table["id"].to_pylist() == [10]
        assert data.last_access["graph_row_groups"] == [3]
        assert reads == [(3, ["id", "level", "bbox_minx", "bbox_miny", "bbox_maxx", "bbox_maxy"])]
        assert data.last_access["catchment_batches"] == (1,)


def test_snap_extra_source_fields_do_not_change_spatial_schema(hfx: Path) -> None:
    path = hfx / "fine.parquet"
    table = pq.read_table(path).append_column("source_label", pa.array(["a", "b", "c", "d"]))
    # This is a valid extra source field, not a graph schema declaration.
    table = table.append_column("bbox_minx", pa.array([100.0] * 4))
    pq.write_table(table, path, row_group_size=2)
    refresh_fingerprint(hfx, "fine.parquet")
    with Dataset.open(hfx) as data:
        selected = data.snap_candidates("fine", (0.4, 0.4, 0.7, 0.6))
        assert selected.ids.tolist() == [101, 102]
        assert selected.attributes["source_label"].to_pylist() == ["a", "b"]
        assert selected.attributes["bbox_minx"].to_pylist() == [100.0, 100.0]


@pytest.mark.parametrize("statistics", [True, False])
def test_prepared_row_groups_keep_unknown_bounds_and_filter_known_levels(tmp_path: Path, statistics: bool) -> None:
    from pourpoint.spatial import RowGroupBounds

    bbox_type = pa.struct([pa.field(name, pa.float32()) for name in ("xmin", "ymin", "xmax", "ymax")])
    boxes = [
        dict(zip(("xmin", "ymin", "xmax", "ymax"), values, strict=True))
        for values in [(0, 0, 1, 1), (10, 10, 11, 11), (10, 10, 11, 11), (0, 0, 1, 1)]
    ]
    table = pa.table(
        {
            "bbox": pa.array([boxes[0], boxes[1], None, boxes[2], boxes[3], None], type=bbox_type),
            "level": pa.array([1, 1, 2, 2, None, None], type=pa.int16()),
        }
    )
    path = tmp_path / "bounds.parquet"
    pq.write_table(table, path, row_group_size=2, write_statistics=statistics)
    prepared = RowGroupBounds(pq.ParquetFile(path))
    # Group 0 touches the query, group 1 has a null bound, group 2 has unknown levels.
    assert prepared.candidate_groups((1, 1, 1, 1)) == [0, 1, 2]
    assert prepared.candidate_groups((1, 1, 1, 1), levels=(1,)) == ([0, 2] if statistics else [0, 1, 2])
    assert prepared.candidate_groups((20, 20, 21, 21)) == ([1, 2] if statistics else [0, 1, 2])
    assert prepared.candidate_groups((1, 1, 1, 1), levels=()) == ([2] if statistics else [0, 1, 2])
    assert prepared.buffer_bytes > 0


def test_prepared_row_groups_without_bounds_do_not_prune_levels(tmp_path: Path) -> None:
    from pourpoint.spatial import RowGroupBounds

    path = tmp_path / "unbounded.parquet"
    pq.write_table(pa.table({"level": pa.array([1, 2], type=pa.int16())}), path, row_group_size=1)
    prepared = RowGroupBounds(pq.ParquetFile(path))
    assert prepared.candidate_groups((10, 10, 11, 11), levels=()) == [0, 1]
    assert prepared.buffer_bytes == 0


def test_prepared_row_groups_keep_partial_null_leaves(tmp_path: Path) -> None:
    from pourpoint.spatial import RowGroupBounds

    bbox_type = pa.struct([pa.field(name, pa.float32()) for name in ("xmin", "ymin", "xmax", "ymax")])
    outside = {"xmin": 10.0, "ymin": 10.0, "xmax": 11.0, "ymax": 11.0}
    path = tmp_path / "partial.parquet"
    pq.write_table(pa.table({"bbox": pa.array([outside, {**outside, "xmin": None}], type=bbox_type)}), path)
    prepared = RowGroupBounds(pq.ParquetFile(path))
    assert prepared.candidate_groups((0, 0, 1, 1)) == [0]
