from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest


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
